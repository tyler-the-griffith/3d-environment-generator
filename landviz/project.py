"""Project loading: project.yaml + variants + CLI overrides → a validated in-memory Project.

Project folder layout (created by `landviz new`):

    projects/<name>/
      project.yaml      the scene spec — the single source of truth
      species.yaml      optional project-local catalog (overrides/extends builtin)
      refs/             raw inputs exactly as received: landscaper plan, photos, drone media
      plan/             rasterized plan pages (landviz plan import)
      assets/           project-specific models / textures
      derived/          regenerable outputs: resolved.json, preview.svg/png, scan/, scene.blend
      renders/          final images and videos
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import geometry, units
from .catalog import Catalog

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = REPO_ROOT / "projects"


class SpecError(ValueError):
    pass


@dataclass
class Issue:
    level: str  # error | warning | info
    where: str
    message: str

    def __str__(self):
        return f"[{self.level.upper():7}] {self.where}: {self.message}"


@dataclass
class Project:
    dir: Path
    spec: dict
    variant: str | None
    units: str
    catalog: Catalog
    plan_frame: geometry.PlanFrame | None
    aerial_frame: geometry.PlanFrame | None = None
    issues: list[Issue] = field(default_factory=list)
    sets: list[str] = field(default_factory=list)
    auto_ids: set[str] = field(default_factory=set)
    load_issues: list[Issue] = field(default_factory=list)

    # ---- paths
    @property
    def derived(self) -> Path:
        d = self.dir / "derived"
        d.mkdir(exist_ok=True)
        return d

    @property
    def renders(self) -> Path:
        d = self.dir / "renders"
        d.mkdir(exist_ok=True)
        return d

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (self.dir / p)

    @property
    def name(self) -> str:
        return self.spec.get("project", {}).get("name", self.dir.name)

    # ---- unit/frame helpers used by element resolvers
    def length(self, v, default=None):
        if v is None:
            return default
        return units.length(v, self.units)

    def pts(self, raw, frame: str | None = None):
        """Convert authored points to site meters (2D or 3D). frame: 'site' (default), 'plan' (landscaper's
        drawing pixels) or 'aerial' (public aerial photo pixels)."""
        frame = frame or self.spec.get("defaults", {}).get("frame", "site")
        if frame in ("plan", "aerial"):
            pf = self.plan_frame if frame == "plan" else self.aerial_frame
            if pf is None:
                raise SpecError("points use frame: plan but plan.calibration is missing" if frame == "plan" else
                                "points use frame: aerial but there's no aerial photo (lv aerial fetch) or "
                                "site.location.origin isn't set")
            xy = pf.to_site([(float(p[0]), float(p[1])) for p in raw])
            return [(x, y, units.length(p[2], self.units)) if len(p) > 2 else (x, y)
                    for (x, y), p in zip(xy, raw)]
        if frame != "site":
            raise SpecError(f"unknown frame {frame!r}")
        return units.points(raw, self.units)

    def pt(self, raw, frame=None):
        return self.pts([raw], frame)[0]

    def to_authored(self, pts_m, frame: str | None = None):
        """Inverse of pts: site meters → points in the given authoring frame and project units."""
        frame = frame or self.spec.get("defaults", {}).get("frame", "site")
        if frame in ("plan", "aerial"):
            pf = self.plan_frame if frame == "plan" else self.aerial_frame
            if pf is None:
                raise SpecError(f"no {frame} frame to convert into")
            return [[round(x, 1), round(y, 1)] for x, y in pf.to_pixels(pts_m)]
        k = units.TO_METERS[self.units]
        return [[round(p[0] / k, 3), round(p[1] / k, 3)] for p in pts_m]


def resolve_project_dir(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if (p / "project.yaml").exists():
        return p.resolve()
    if p.name == "project.yaml" and p.exists():
        return p.parent.resolve()
    q = PROJECTS_DIR / name_or_path
    if (q / "project.yaml").exists():
        return q
    raise SpecError(f"no project.yaml found for {name_or_path!r} (looked in {p} and {q})")


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def apply_set(spec: dict, assignment: str) -> None:
    """CLI override: 'render.year=5' or 'render.season=fall'. Values parsed as YAML scalars."""
    key, _, val = assignment.partition("=")
    if not _:
        raise SpecError(f"--set expects key=value, got {assignment!r}")
    node = spec
    parts = key.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = yaml.safe_load(val)


OVERLAY_KEYS = ("include_tags", "exclude_tags", "elements_override", "elements_add", "elements_remove",
                "parent", "title", "note")


def apply_overlay(spec: dict, v: dict) -> dict:
    """Apply a variant overlay: deep-merge design keys, then add/remove/override elements by id."""
    v = v or {}
    out = deep_merge(spec, {k: val for k, val in v.items() if k not in OVERLAY_KEYS})
    removed = set(v.get("elements_remove") or [])
    out["elements"] = [el for el in out.get("elements", []) if el.get("id") not in removed]
    for el in v.get("elements_add") or []:
        out["elements"].append(copy.deepcopy(el))
    overrides = v.get("elements_override", {})
    include, exclude = set(v.get("include_tags", [])), set(v.get("exclude_tags", []))
    els = []
    for el in out.get("elements", []):
        tags = set(el.get("tags", []))
        if exclude & tags:
            continue
        if include and tags and not (include & tags) and any(t.startswith("option") for t in tags):
            continue
        if el.get("id") in overrides:
            el = deep_merge(el, overrides[el["id"]])
        els.append(el)
    out["elements"] = els
    return out


VARIANTS_DIR = "variants"


def variant_file(pdir: Path, name: str) -> Path:
    return pdir / VARIANTS_DIR / name / "variant.yaml"


def variant_chain(pdir: Path, spec: dict, variant: str) -> list[tuple[str, dict]]:
    """[(name, overlay), ...] from the base's first child down to `variant`.

    Variants come from project.yaml `variants:` (quick options like seasons) or from
    variants/<name>/variant.yaml (child variants made in the app; `parent:` names the
    variant they branch from, or `base`)."""
    chain, seen, name = [], set(), variant
    while name and name != "base":
        if name in seen:
            raise SpecError(f"variant parent cycle at {name!r}")
        seen.add(name)
        if name in (spec.get("variants") or {}):
            overlay = (spec["variants"] or {})[name] or {}
        elif variant_file(pdir, name).exists():
            try:
                overlay = yaml.safe_load(variant_file(pdir, name).read_text()) or {}
            except yaml.YAMLError as e:
                raise SpecError(f"{variant_file(pdir, name)} is not valid YAML: {e}") from None
        else:
            known = list(spec.get("variants") or {}) + list_child_variants(pdir)
            raise SpecError(f"unknown variant {name!r}; defined: {', '.join(known) or 'none'}")
        chain.append((name, overlay))
        name = overlay.get("parent")
    return chain[::-1]


def list_child_variants(pdir: Path) -> list[str]:
    d = pdir / VARIANTS_DIR
    return sorted(p.parent.name for p in d.glob("*/variant.yaml")) if d.exists() else []


def apply_variant(spec: dict, variant: str | None, pdir: Path | None = None) -> dict:
    """Back-compat single-step form (project.yaml variants only)."""
    if not variant:
        return spec
    for _name, overlay in variant_chain(pdir or Path("."), spec, variant):
        spec = apply_overlay(spec, overlay)
    return spec


def _build_plan_frame(spec: dict, units_name: str) -> geometry.PlanFrame | None:
    plan = spec.get("plan") or {}
    cal = plan.get("calibration")
    if not cal:
        return None
    if "points" in cal:
        pts = cal["points"]
        if len(pts) < 2:
            raise SpecError("plan.calibration.points needs at least 2 points")
        return geometry.PlanFrame([p["px"] for p in pts],
                                  [units.point(p["at"], units_name)[:2] for p in pts])
    if "drawing_scale" in cal:
        # e.g. drawing_scale: "1in = 10ft", dpi: 150 (the dpi the page was rasterized at)
        left, right = [s.strip() for s in str(cal["drawing_scale"]).split("=")]
        paper_m, ground_m = units.length(left, "in"), units.length(right, units_name)
        dpi = float(cal.get("dpi", plan.get("dpi", 150)))
        m_per_px = ground_m / paper_m * 0.0254 / dpi
        ox, oy = cal.get("origin_px", [0, 0])
        at = units.point(cal.get("origin_at", [0, 0]), units_name)
        import math
        rot = math.radians(float(cal.get("rotation_deg", 0)))  # plan "up" relative to site +Y
        d = 1000.0
        p2 = (ox + d, oy)
        q2 = (at[0] + d * m_per_px * math.cos(rot), at[1] - d * m_per_px * math.sin(rot))
        return geometry.PlanFrame([(ox, oy), p2], [at[:2], q2])
    raise SpecError("plan.calibration needs 'points' or 'drawing_scale'")


EDITS_FILE = "edits.yaml"


def load_edits(pdir: Path) -> dict:
    p = pdir / EDITS_FILE
    if not p.exists():
        return {}
    try:
        return yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise SpecError(f"{EDITS_FILE} is not valid YAML: {e}") from None


def apply_element_edits(spec: dict, patches: dict, issues: list, where: str) -> None:
    """Merge per-element patches (from `lv pull`) into the spec's elements, by id."""
    by_id = {el.get("id"): el for el in spec.get("elements", [])}
    for eid, patch in (patches or {}).items():
        el = by_id.get(eid)
        if el is None:
            issues.append(Issue("warning", where, f"edit for {eid!r} matches no element (renamed or removed?)"))
            continue
        el.update(deep_merge(el, patch))


def load(name_or_path: str, variant: str | None = None, sets: list[str] | None = None,
         extra_views: list[dict] | None = None) -> Project:
    pdir = resolve_project_dir(name_or_path)
    try:
        spec = yaml.safe_load((pdir / "project.yaml").read_text()) or {}
    except yaml.YAMLError as e:
        raise SpecError(f"project.yaml is not valid YAML: {e}") from None
    # Stable ids for elements that don't have one: position in project.yaml (before variants
    # filter anything), so edits and output names stay attached to the same element.
    auto_ids = set()
    for i, el in enumerate(spec.get("elements", []) or []):
        if isinstance(el, dict) and "id" not in el and "kind" in el:
            el["id"] = f"{el['kind']}-{i}"
            auto_ids.add(el["id"])
    # Hand edits pulled from the model: base edits apply before the variant (so every
    # variant inherits them), variant edits after.
    edits = load_edits(pdir)
    load_issues: list[Issue] = []
    apply_element_edits(spec, edits.get("elements"), load_issues, EDITS_FILE)
    if variant:
        for name, overlay in variant_chain(pdir, spec, variant):
            spec = apply_overlay(spec, overlay)
            apply_element_edits(spec, ((edits.get("variants") or {}).get(name) or {}).get("elements"),
                                load_issues, f"{EDITS_FILE} ({name})")
    # Views an output type needs (e.g. an orbit for "orbit video") when the design has none.
    have = {v.get("name") for v in spec.get("views") or []}
    for v in extra_views or []:
        if v["name"] not in have:
            spec.setdefault("views", []).append(v)
    for s in sets or []:
        apply_set(spec, s)

    units_name = spec.get("project", {}).get("units", "ft")
    if units_name not in units.TO_METERS:
        raise SpecError(f"project.units {units_name!r} not one of m, ft, in, cm, mm, yd")

    catalog = Catalog.builtin()
    for c in spec.get("catalogs", []):
        catalog.load(pdir / c)
    if (pdir / "species.yaml").exists() and "species.yaml" not in spec.get("catalogs", []):
        catalog.load(pdir / "species.yaml")

    proj = Project(dir=pdir, spec=spec, variant=variant, units=units_name, catalog=catalog,
                   plan_frame=_build_plan_frame(spec, units_name), sets=list(sets or []),
                   auto_ids=auto_ids, load_issues=load_issues)
    loc = (spec.get("site") or {}).get("location") or {}
    if loc.get("origin"):
        from . import aerial
        try:
            proj.aerial_frame = aerial.frame(pdir, [float(v) for v in loc["origin"]],
                                             float(loc.get("north_offset_deg", 0.0)))
        except (TypeError, ValueError):
            pass
    return proj
