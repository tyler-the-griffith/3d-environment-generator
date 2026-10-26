"""Hand edits made in Blender → edits.yaml patches.

The model is never the source of truth; project.yaml (+ edits.yaml) is. But hand-tweaking
the model is the natural way to nudge things, so `lv pull` reads what moved/resized/vanished
in a saved model (via the as-built transforms stamped on every object) and records it here
as per-element patches. They're merged over project.yaml on every load, so they appear in
every view, every detail level, and every variant built on the base.

What can be pulled:
  - single elements (plant, boulder, light, bed, paving, lawn, wall, fence, structure):
    moves and rotations about Z; plants/boulders also resize; deletion → enabled: false
  - members of generated groups (plant_group, plant_row, boulder polygon): moves, resizes,
    deletion → adjust: {index: ...}
What isn't: vertex-level reshaping, vertical moves (everything sits on grade), parts moved
without their element (a roof without its house), stepping stones individually, scans.
Those are reported so nothing is silently lost.
"""
from __future__ import annotations

import math

import yaml

from . import units
from .project import EDITS_FILE, Project, load_edits

GEOM_KEY = {"lawn": "polygon", "bed": "polygon", "paving": "polygon", "plant_group": "polygon",
            "path": "line", "wall": "line", "fence": "line", "plant_row": "line",
            "structure": "footprint", "plant": "at", "boulder": "at", "light": "at"}


def _to_units(meters: float, project: Project) -> float:
    return round(meters / units.TO_METERS[project.units], 3)


def _xy(project, p):
    return [_to_units(p[0], project), _to_units(p[1], project)]


def _rigid(points_m, pivot0, pivot1, rot_deg):
    """Rotate about the as-built pivot by rot_deg, then translate pivot0 → pivot1."""
    a = math.radians(rot_deg)
    c, s = math.cos(a), math.sin(a)
    out = []
    for x, y in points_m:
        dx, dy = x - pivot0[0], y - pivot0[1]
        out.append((pivot1[0] + c * dx - s * dy, pivot1[1] + s * dx + c * dy))
    return out


def patches_from_report(project: Project, scene: dict, report: dict):
    """Return (patches {element id: patch}, notes [str])."""
    raw_by_id = {el["id"]: el for el in project.spec.get("elements", [])}
    rec_by_id = {r["id"]: r for r in scene["elements"]}
    patches: dict[str, dict] = {}
    notes: list[str] = []

    def patch(eid):
        return patches.setdefault(eid, {})

    for ch in report["changes"]:
        rid = ch["rec"]
        rec = rec_by_id.get(rid)
        if rec is None:
            notes.append(f"{rid}: not in the current design any more — skipped")
            continue
        src = rec["source"]
        raw = raw_by_id.get(src)
        if raw is None:
            notes.append(f"{rid}: element {src!r} not found in project.yaml — skipped")
            continue
        kind = raw["kind"]
        sub = rid.split("#", 1)[1] if "#" in rid else None
        if src in project.auto_ids:
            notes.append(f"{src}: has no `id` in project.yaml; its edit is keyed by position — "
                         f"give it an id so the edit survives reordering")

        if ch.get("deleted"):
            if sub is not None and kind in ("plant_group", "plant_row", "boulder"):
                patch(src).setdefault("adjust", {}).setdefault(int(sub), {})["remove"] = True
            elif sub is None:
                patch(src)["enabled"] = False
            else:
                notes.append(f"{rid}: deleting one piece of {src!r} can't be expressed — edit project.yaml")
            continue

        to, frm = ch["to"], ch["from"]
        moved = math.hypot(to[0] - frm[0], to[1] - frm[1]) > 0.005
        rot = ch["rot_z_deg"]
        sx, _sy, sz = ch["scale"]
        resized = abs(sx - 1) > 0.01 or abs(sz - 1) > 0.01
        if abs(to[2] - frm[2]) > 0.05:
            notes.append(f"{rid}: vertical move ignored (everything sits on grade)")

        if kind in ("plant_group", "plant_row") or (kind == "boulder" and sub is not None):
            adj = patch(src).setdefault("adjust", {}).setdefault(int(sub), {})
            if moved:
                adj["at"] = _xy(project, to)
            if resized:
                if kind == "boulder":
                    adj["height"] = _to_units(rec["size"] * sx, project)
                else:
                    adj["height"] = _to_units(rec["height"] * sz, project)
                    adj["spread"] = _to_units(rec["spread"] * sx, project)
            continue
        if kind in ("plant", "boulder", "light"):
            p = patch(src)
            if moved:
                p["at"], p["frame"] = _xy(project, to), "site"
            if resized and kind == "plant":
                p["height"] = _to_units(rec["height"] * sz, project)
                p["spread"] = _to_units(rec["spread"] * sx, project)
            if resized and kind == "boulder":
                p["size"] = _to_units(rec["size"] * sx, project)
            continue
        if sub is not None:
            notes.append(f"{rid}: individual pieces of {src!r} ({kind}) can't be moved on their own — "
                         f"move the whole element or edit project.yaml")
            continue
        key = GEOM_KEY.get(kind)
        if key is None:
            notes.append(f"{rid}: {kind} edits aren't pulled (e.g. scans) — edit project.yaml")
            continue
        if resized:
            notes.append(f"{rid}: scaling a {kind} isn't pulled — reshape it in project.yaml")
        if not (moved or abs(rot) > 0.05):
            continue
        pts_m = [p[:2] for p in project.pts(raw[key], raw.get("frame"))]
        new = _rigid(pts_m, frm, to, rot)
        p = patch(src)
        p[key] = [_xy(project, q) for q in new]
        p["frame"] = "site"

    for name in report.get("unpulled_parts", []):
        notes.append(f"{name}: moved on its own (not with its element) — not pulled; "
                     "select the element's main object to move it as a whole")
    return patches, notes


def write(project: Project, patches: dict, variant: str | None) -> dict:
    """Merge new patches into edits.yaml (base, or the variant's section)."""
    from .project import deep_merge
    edits = load_edits(project.dir)
    if variant:
        section = edits.setdefault("variants", {}).setdefault(variant, {}).setdefault("elements", {})
    else:
        section = edits.setdefault("elements", {})
    for eid, p in patches.items():
        section[eid] = deep_merge(section.get(eid, {}), p)
    header = ("# Hand edits pulled from the 3D model with `lv pull`. Merged over project.yaml on\n"
              "# every load (base edits apply to every variant). Safe to edit or delete by hand;\n"
              "# move entries into project.yaml when you want them to be part of the design proper.\n")
    (project.dir / EDITS_FILE).write_text(header + yaml.safe_dump(edits, sort_keys=False))
    return edits
