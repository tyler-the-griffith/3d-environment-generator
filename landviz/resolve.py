"""Project → resolved scene (plain JSON). The contract between the core and Blender.

Everything in the resolved scene is in site meters with all defaults filled, all plants
expanded to individual instances with concrete sizes/colors for the chosen year and month,
the sun position computed, and the terrain sampled to a height grid. The Blender side is
deliberately dumb: it only turns these records into geometry.
"""
from __future__ import annotations

import math
import zlib
from datetime import datetime, timedelta, timezone

import numpy as np

from . import elements, geometry, solar
from .catalog import SEASON_MONTH
from .project import Issue, Project, SpecError

SCHEMA_VERSION = 1

QUALITY_PRESETS = {
    # samples, resolution %, grass mode, leaf density multiplier
    "draft":   {"samples": 24,  "resolution_pct": 50,  "grass": "texture", "leaf_mult": 0.35, "denoise": True},
    "preview": {"samples": 96,  "resolution_pct": 100, "grass": "light",   "leaf_mult": 0.7,  "denoise": True},
    "final":   {"samples": 384, "resolution_pct": 100, "grass": "full",    "leaf_mult": 1.0,  "denoise": True},
    "web":     {"samples": 0,   "resolution_pct": 100, "grass": "texture", "leaf_mult": 0.25, "denoise": False},
}


class Context:
    def __init__(self, project: Project, year, month: int, southern: bool, seed: int):
        self.project = project
        self.year = year
        self.month = month
        self.southern = southern
        self.seed = seed
        self.element_id = "?"
        self.species_used: set[str] = set()

    def stable_seed(self, *parts) -> int:
        return zlib.crc32(":".join(map(str, (self.seed, self.element_id) + parts)).encode())

    def _issue(self, level, msg):
        self.project.issues.append(Issue(level, self.element_id, msg))

    def error(self, msg):
        self._issue("error", msg)

    def warn(self, msg):
        self._issue("warning", msg)

    def info(self, msg):
        self._issue("info", msg)


def _render_settings(spec: dict) -> dict:
    r = spec.get("render", {}) or {}
    quality = r.get("quality", "preview")
    if quality not in QUALITY_PRESETS:
        raise SpecError(f"render.quality must be one of {', '.join(QUALITY_PRESETS)}")
    q = dict(QUALITY_PRESETS[quality])
    for k in ("samples", "resolution_pct", "grass", "leaf_mult", "denoise"):
        if k in r:
            q[k] = r[k]
    res = r.get("resolution", [1920, 1080])
    return {"quality": quality, **q, "resolution": [int(res[0]), int(res[1])],
            "device": r.get("device", "auto"), "exposure": float(r.get("exposure", -3.5)),
            "look": r.get("look", "AgX - Medium High Contrast"), "seed": int(r.get("seed", 1)),
            "film_transparent": bool(r.get("film_transparent", False))}


NAMED_TIMES = {"sunrise": ("rise", 0.0), "morning": ("rise", 15.0), "noon": None, "afternoon": ("set", 35.0),
               "golden": ("set", 8.0), "sunset": ("set", 0.0), "dusk": ("set", -3.0), "night": ("set", -12.0)}


def _parse_time(value) -> str:
    """'17:30', or a YAML-1.1 sexagesimal int (unquoted 17:30 loads as 1050), or a named time."""
    if isinstance(value, int):
        return f"{value // 60:02d}:{value % 60:02d}"
    return str(value).strip().lower()


def _named_time(name: str, day: datetime, lat: float, lon: float) -> datetime:
    """Time of day when the sun reaches a named elevation (searched minute by minute)."""
    if name == "noon":
        return max((day.replace(hour=h, minute=m) for h in range(10, 15) for m in range(0, 60, 2)),
                   key=lambda t: solar.sun_position(lat, lon, t)[1])
    side, target = NAMED_TIMES[name]
    hours = range(3, 13) if side == "rise" else range(12, 24)
    times = [day.replace(hour=h, minute=m) for h in hours for m in range(0, 60, 2)]
    if side == "set":
        times = times[::-1]  # search backwards from midnight
    for t in times:
        if solar.sun_position(lat, lon, t)[1] >= target:
            return t
    return day.replace(hour=12)


def _when(spec: dict) -> tuple[datetime, int]:
    r = spec.get("render", {}) or {}
    loc = (spec.get("site", {}) or {}).get("location", {}) or {}
    tz = timezone(timedelta(hours=float(loc.get("utc_offset", -5))))
    season = r.get("season")
    date = r.get("date")
    time = _parse_time(r.get("time", "16:30"))
    if date:
        d = datetime.fromisoformat(str(date))
    else:
        month = SEASON_MONTH.get(season or "summer", 7)
        d = datetime(2026, month, 15)
    day = d.replace(hour=0, minute=0, tzinfo=tz)
    if time in NAMED_TIMES:
        when = _named_time(time, day, float(loc.get("lat", 40.0)), float(loc.get("lon", -83.0)))
    else:
        try:
            hh, mm = (int(x) for x in time.split(":")[:2])
        except ValueError:
            raise SpecError(f"render.time {time!r}: use HH:MM or one of {', '.join(NAMED_TIMES)}") from None
        when = day.replace(hour=hh, minute=mm)
    return when, when.month


def _environment(spec: dict, when: datetime) -> dict:
    site = spec.get("site", {}) or {}
    loc = site.get("location", {}) or {}
    env = spec.get("environment", {}) or {}
    lat, lon = float(loc.get("lat", 40.0)), float(loc.get("lon", -83.0))
    az, el = solar.sun_position(lat, lon, when)
    north = float(loc.get("north_offset_deg", 0.0))  # site +Y is this many degrees clockwise of true north
    return {
        "lat": lat, "lon": lon, "datetime": when.isoformat(),
        "sun_azimuth_deg": az, "sun_elevation_deg": el,
        "sun_azimuth_site_deg": (az - north) % 360,  # clockwise from site +Y
        "sky": env.get("sky", "physical"),  # physical | hdri
        "hdri": env.get("hdri"),
        "hdri_strength": float(env.get("hdri_strength", 1.0)),
        "sun_strength": float(env.get("sun_strength", 1.0)),
        "haze": float(env.get("haze", 1.0)),
        "cloudiness": float(env.get("cloudiness", 0.0)),  # 0 clear .. 1 overcast (softens sun)
        "surroundings": env.get("surroundings", "lawn"),  # ground beyond the site: lawn | field | none
    }


def _terrain(project: Project) -> dict:
    site = project.spec.get("site", {}) or {}
    t = site.get("terrain", {"type": "flat"}) or {"type": "flat"}
    boundary = _boundary(project)
    x0, y0, x1, y1 = geometry.bbox(boundary)
    margin = project.length(t.get("margin"), 25.0)
    res = project.length(t.get("resolution"), 0.5)
    x0, y0, x1, y1 = x0 - margin, y0 - margin, x1 + margin, y1 + margin
    nx, ny = int(math.ceil((x1 - x0) / res)) + 1, int(math.ceil((y1 - y0) / res)) + 1
    kind = t.get("type", "flat")
    if kind == "flat":
        heights = None
    elif kind == "graded":
        pts = project.pts(t["points"], t.get("frame"))
        if any(len(p) < 3 for p in pts):
            raise SpecError("site.terrain.points need [x, y, elevation]")
        tps = geometry.ThinPlateSpline(pts, smoothing=float(t.get("smoothing", 0.0)))
        gx, gy = np.meshgrid(np.linspace(x0, x0 + (nx - 1) * res, nx), np.linspace(y0, y0 + (ny - 1) * res, ny))
        z = tps(np.column_stack([gx.ravel(), gy.ravel()]))
        # Flatten far outside the site toward the edge mean so the horizon isn't a cliff.
        heights = [round(float(v), 4) for v in z]
    elif kind == "scan":
        heights = None  # Blender derives ground from the scan mesh (see builders/terrain.py)
    else:
        raise SpecError(f"site.terrain.type must be flat | graded | scan, not {kind!r}")
    return {"type": kind, "x0": x0, "y0": y0, "res": res, "nx": nx, "ny": ny, "heights": heights,
            "base_material": t.get("material", "lawn"), "scan_element": t.get("scan_element")}


def _boundary(project: Project):
    site = project.spec.get("site", {}) or {}
    if "boundary" not in site:
        raise SpecError("site.boundary (property line polygon) is required")
    return geometry.ensure_ccw([p[:2] for p in project.pts(site["boundary"], site.get("frame"))])


def _views(project: Project, boundary) -> list[dict]:
    x0, y0, x1, y1 = geometry.bbox(boundary)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    diag = math.hypot(x1 - x0, y1 - y0)
    out = []
    raw_views = project.spec.get("views") or [{"name": "aerial", "preset": "aerial"}, {"name": "plan", "preset": "top"}]
    for v in raw_views:
        name = v.get("name") or f"view{len(out)}"
        preset = v.get("preset", "eye")
        lens = float(v.get("lens", 24 if preset == "eye" else 35))
        rec = {"name": name, "preset": preset, "lens": lens, "ortho": False,
               "animation": None, "dof": v.get("dof")}
        if preset == "top":
            pad = float(v.get("padding", 1.08))
            rec.update(ortho=True, ortho_extent=[(x1 - x0) * pad, (y1 - y0) * pad],
                       position=[cx, cy, 200.0], target=[cx, cy, 0.0],
                       position_above_ground=False, target_above_ground=False)
        elif preset == "aerial":
            az = math.radians(float(v.get("azimuth", 200)))  # direction the camera sits, from site +Y clockwise
            el = math.radians(float(v.get("elevation", 35)))
            dist = project.length(v.get("distance"), diag * float(v.get("zoom", 1.15)))
            tx, ty = project.pt(v["target"])[:2] if "target" in v else (cx, cy)
            rec.update(position=[tx + dist * math.cos(el) * math.sin(az), ty + dist * math.cos(el) * math.cos(az),
                                 dist * math.sin(el)],
                       target=[tx, ty, 0.0], position_above_ground=True, target_above_ground=True)
        elif preset == "pano":  # 360° equirectangular from a standing (or given) height
            pos = project.pt(v["position"], v.get("frame"))
            h = project.length(v.get("eye_height"), 1.65)
            rec.update(pano=True, position=[pos[0], pos[1], pos[2] if len(pos) > 2 else h],
                       target=[pos[0], pos[1] + 1, pos[2] if len(pos) > 2 else h],
                       position_above_ground=len(pos) < 3, target_above_ground=len(pos) < 3)
        elif preset == "eye":
            pos = project.pt(v["position"], v.get("frame"))
            tgt = project.pt(v["target"], v.get("frame"))
            eye = project.length(v.get("eye_height"), 1.65)
            rec.update(position=[pos[0], pos[1], pos[2] if len(pos) > 2 else eye],
                       target=[tgt[0], tgt[1], tgt[2] if len(tgt) > 2 else 1.0],
                       position_above_ground=len(pos) < 3, target_above_ground=len(tgt) < 3)
        elif preset in ("orbit", "flythrough"):
            rec["animation"] = {
                "type": preset, "seconds": float(v.get("seconds", 20)), "fps": int(v.get("fps", 30)),
            }
            if preset == "orbit":
                tx, ty = project.pt(v["target"])[:2] if "target" in v else (cx, cy)
                rec["animation"].update(
                    center=[tx, ty], radius=project.length(v.get("radius"), diag * 0.75),
                    height=project.length(v.get("height"), diag * 0.35),
                    start_azimuth=float(v.get("start_azimuth", 180)), degrees=float(v.get("degrees", 360)))
                rec.update(position=[tx, ty - 1, 1], target=[tx, ty, 1.0],
                           position_above_ground=True, target_above_ground=True)
            else:
                pts = project.pts(v["path"], v.get("frame"))
                h = project.length(v.get("height"), 1.65)
                rec["animation"]["path"] = [[p[0], p[1], p[2] if len(p) > 2 else h] for p in pts]
                rec["animation"]["look_ahead"] = project.length(v.get("look_ahead"), 4.0)
                rec["animation"]["smooth"] = int(v.get("smooth", 3))
                rec.update(position=rec["animation"]["path"][0], target=rec["animation"]["path"][-1],
                           position_above_ground=True, target_above_ground=True)
        else:
            raise SpecError(f"view {name}: unknown preset {preset!r} (eye | aerial | top | pano | orbit | flythrough)")
        out.append(rec)
    return out


def resolve(project: Project) -> dict:
    spec = project.spec
    project.issues.clear()
    project.issues.extend(project.load_issues)
    r = spec.get("render", {}) or {}
    when, month = _when(spec)
    year = r.get("year", 5)
    if year != "mature":
        year = float(year)
    southern = float((spec.get("site", {}).get("location", {}) or {}).get("lat", 40)) < 0
    ctx = Context(project, year, month, southern, seed=int(r.get("seed", 1)))

    boundary = _boundary(project)
    records = []
    seen_ids = set()
    for i, raw in enumerate(spec.get("elements", []) or []):
        if not isinstance(raw, dict) or "kind" not in raw:
            project.issues.append(Issue("error", f"elements[{i}]", "each element needs a `kind`"))
            continue
        if raw.get("enabled", True) is False:
            continue
        eid = raw.get("id") or f"{raw['kind']}-{i}"
        ctx.element_id = eid
        if eid in seen_ids:
            ctx.error("duplicate id")
        seen_ids.add(eid)
        et = elements.get(raw["kind"])
        if et is None:
            ctx.error(f"unknown kind {raw['kind']!r}; known: {', '.join(sorted(elements.REGISTRY))}")
            continue
        try:
            recs = et.resolve(raw, ctx)
        except (KeyError, ValueError, TypeError) as e:
            ctx.error(f"{type(e).__name__}: {e}")
            continue
        multi = len(recs) > 1 or any("sub" in r for r in recs)
        for j, rec in enumerate(recs):
            # Sub-records keep their original index even when siblings are removed, so
            # hand edits (adjust: {index: ...}) stay attached to the same plant.
            rec["id"] = f"{eid}#{rec.get('sub', j)}" if multi else eid
            rec["source"] = eid
            rec["layer"] = elements.get(rec["kind"]).layer
            rec["tags"] = raw.get("tags", [])
            records.append(rec)

    views = _views(project, boundary)
    _sanity_checks(project, boundary, records, views)

    species = {}
    for sid in sorted(ctx.species_used):
        sp = project.catalog.get(sid)
        species[sid] = {"common": sp.common, "botanical": sp.botanical, "category": sp.category,
                        "form": sp.form, "leaf_size": sp.leaf_size, "bark": sp.bark,
                        "evergreen": sp.evergreen, "generator": sp.generator,
                        "asset": str(project.path(sp.asset)) if sp.asset else None}

    env = _environment(spec, when)
    render = _render_settings(spec)
    if "exposure" not in (r or {}):  # twilight: open up so the sky and landscape lighting read
        el = env["sun_elevation_deg"]
        if el < 6:
            render["exposure"] += (6 - max(el, -8)) * 0.4
    return {
        "schema": SCHEMA_VERSION,
        "project": {"name": project.name, "dir": str(project.dir), "variant": project.variant},
        "site": {"boundary": boundary, "terrain": _terrain(project)},
        "environment": env,
        "growth": {"year": year, "month": month},
        "render": render,
        "species": species,
        "elements": sorted(records, key=lambda e: e["layer"]),
        "views": views,
        "issues": [vars(i) for i in project.issues],
    }


def _sanity_checks(project, boundary, records, views=()):
    plants = [r for r in records if r["kind"] == "plant"]
    outside = [p for p in plants if not geometry.point_in_polygon(p["x"], p["y"], boundary)]
    if outside:
        project.issues.append(Issue("warning", "site", f"{len(outside)} plant(s) outside the property boundary: "
                                    + ", ".join(sorted({p['source'] for p in outside})[:6])))
    # Trunks closer than 15% of their combined spread usually means a typo in coordinates.
    if len(plants) < 4000:
        xy = np.array([[p["x"], p["y"]] for p in plants]) if plants else np.zeros((0, 2))
        sp = np.array([p["spread"] for p in plants])
        crowded = set()
        for i in range(len(plants)):
            d = np.hypot(*(xy[i + 1:] - xy[i]).T)
            close = np.nonzero(d < 0.15 * (sp[i + 1:] + sp[i]))[0]
            for j in close:
                crowded.add((plants[i]["source"], plants[i + 1 + j]["source"]))
        for a, b in sorted(crowded)[:10]:
            project.issues.append(Issue("warning", a, f"plants nearly on top of each other (with {b})"))

    # Eye-level cameras standing inside a canopy see nothing but leaves.
    for v in views:
        if v["preset"] != "eye":
            continue
        cx, cy, cz = v["position"]
        for p in plants:
            if math.hypot(p["x"] - cx, p["y"] - cy) < p["spread"] * 0.45 and cz < p["height"]:
                project.issues.append(Issue("warning", f"view {v['name']}",
                                            f"camera is inside {p['source']} ({p['species']}) — move it"))
                break
