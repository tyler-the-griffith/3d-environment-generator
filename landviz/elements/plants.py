"""Plants: single specimens, massed beds, and rows/hedges. All emit `plant` records."""
from __future__ import annotations

import math
import random

from .. import geometry
from . import ElementType, register


def _species(raw, ctx):
    key = raw["species"]
    sp = ctx.project.catalog.get(key)
    if sp is None:
        near = [s.id for s in ctx.project.catalog.search(key.split()[0])][:5]
        ctx.error(f"unknown species {key!r}" + (f" — did you mean {', '.join(near)}?" if near else
                                                 " — add it to the project's species.yaml"))
    return sp


def plant_record(sp, x, y, raw, ctx, idx=0):
    """One resolved plant: concrete size for the render year, appearance for the render month."""
    rng = random.Random(f"{ctx.seed}:{ctx.element_id}:{idx}")
    planted_h = ctx.project.length(raw.get("planted_height"))
    planted_s = ctx.project.length(raw.get("planted_spread"))
    h, s = sp.size_at(ctx.year, planted_h, planted_s)
    # Natural plant-to-plant variation, unless a size is given explicitly (a sheared hedge
    # kept at 4 ft, or a size pulled from a hand edit) — explicit sizes are exact.
    var = float(raw.get("variation", 0.12))
    fh, fs = 1 + rng.uniform(-var, var), 1 + rng.uniform(-var, var)
    h = ctx.project.length(raw["height"]) if "height" in raw else h * fh
    s = ctx.project.length(raw["spread"]) if "spread" in raw else s * fs
    ctx.species_used.add(sp.id)
    state = sp.state(ctx.month, ctx.southern)
    return {
        "kind": "plant", "species": sp.id, "x": x, "y": y,
        "height": h * state["height_scale"], "spread": s,
        "rotation": rng.uniform(0, 2 * math.pi) if "rotation" not in raw else math.radians(float(raw["rotation"])),
        "seed": rng.randrange(1 << 30),
        "state": state,
        "sheared": bool(raw.get("sheared", sp.generator.get("sheared", False))),
    }


@register
class Plant(ElementType):
    kind = "plant"
    layer = 70
    keys = {"species", "at", "height", "spread", "planted_height", "planted_spread", "rotation",
            "variation", "sheared", "existing"}
    required = {"species", "at"}

    def resolve(self, raw, ctx):
        if not self.check_keys(raw, ctx):
            return []
        sp = _species(raw, ctx)
        if not sp:
            return []
        x, y = ctx.project.pt(raw["at"], raw.get("frame"))[:2]
        rec = plant_record(sp, x, y, raw, ctx)
        rec["existing"] = bool(raw.get("existing", False))
        return [rec]

    def preview(self, rec, svg):
        svg.plant(rec)


def adjusted(points, raw, ctx):
    """Apply per-member hand edits (`adjust: {index: {at, height, spread, remove}}`, written by
    `lv pull`) to a generated layout. Yields (index, x, y, overrides) — indices stay original."""
    adjust = raw.get("adjust") or {}
    for k in adjust:
        if not isinstance(k, int) or k >= len(points) or k < 0:
            ctx.warn(f"adjust index {k!r} is outside the generated layout (0..{len(points) - 1}) — "
                     "the layout changed since the edit was pulled")
    for i, (x, y) in enumerate(points):
        a = adjust.get(i) or {}
        if a.get("remove"):
            continue
        if "at" in a:
            x, y = ctx.project.pt(a["at"], "site")[:2]
        yield i, x, y, {k: a[k] for k in ("height", "spread") if k in a}


@register
class PlantGroup(ElementType):
    """A massed planting: fill a polygon on a triangular grid at `spacing` on-center, or
    scatter exactly `count` plants (what plan keys usually say: "(7) Catmint @ 24\" o.c.")."""
    kind = "plant_group"
    layer = 70
    keys = Plant.keys - {"at"} | {"polygon", "spacing", "count", "jitter", "smooth", "adjust"}
    required = {"species", "polygon"}

    def resolve(self, raw, ctx):
        if not self.check_keys(raw, ctx):
            return []
        sp = _species(raw, ctx)
        if not sp:
            return []
        poly = geometry.ensure_ccw([p[:2] for p in ctx.project.pts(raw["polygon"], raw.get("frame"))])
        if raw.get("count") and not raw.get("spacing"):
            n = int(raw["count"])
            min_d = math.sqrt(geometry.area(poly) / max(n, 1)) * 0.7
            pts = geometry.scatter_polygon(poly, n, seed=ctx.stable_seed(), min_dist=min_d)
            if len(pts) < n:
                ctx.warn(f"only fit {len(pts)} of {n} plants in the polygon")
        else:
            spacing = ctx.project.length(raw.get("spacing"), sp.spread * 0.9)
            pts = geometry.fill_polygon(poly, spacing, seed=ctx.stable_seed(),
                                        jitter=float(raw.get("jitter", 0.15)))
            if raw.get("count") and len(pts) != int(raw["count"]):
                ctx.info(f"spacing gives {len(pts)} plants; plan count says {raw['count']}")
        return [{**plant_record(sp, x, y, {**raw, **ov}, ctx, i), "sub": i}
                for i, x, y, ov in adjusted(pts, raw, ctx)]


@register
class PlantRow(ElementType):
    """Plants along a line — hedges, screens, borders."""
    kind = "plant_row"
    layer = 70
    keys = Plant.keys - {"at"} | {"line", "spacing", "count", "smooth", "adjust"}
    required = {"species", "line"}

    def resolve(self, raw, ctx):
        if not self.check_keys(raw, ctx):
            return []
        sp = _species(raw, ctx)
        if not sp:
            return []
        line = [p[:2] for p in ctx.project.pts(raw["line"], raw.get("frame"))]
        if raw.get("smooth"):
            line = geometry.smooth_polyline(line, int(raw["smooth"]))
        if raw.get("count"):
            n = int(raw["count"])
            L = geometry.polyline_length(line)
            spacing = L / max(n - 1, 1)
        else:
            spacing = ctx.project.length(raw.get("spacing"), sp.spread * 0.8)
        pts = [(x, y) for x, y, _h in geometry.resample_polyline(line, spacing)]
        return [{**plant_record(sp, x, y, {**raw, **ov}, ctx, i), "sub": i}
                for i, x, y, ov in adjusted(pts, raw, ctx)]
