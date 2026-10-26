"""Ground surfaces: lawn, planting beds, paving, paths."""
from __future__ import annotations

from .. import geometry
from . import ElementType, register

PAVING_MATERIALS = {
    "concrete": "#cfcac0", "stamped_concrete": "#bba98f", "exposed_aggregate": "#b8ad9c",
    "pavers": "#a88a72", "pavers_gray": "#9a9690", "brick": "#9c4f3a", "flagstone": "#a69a84",
    "bluestone": "#7f8a90", "gravel": "#c4b9a5", "pea_gravel": "#c9b89a", "asphalt": "#4a4a4a",
    "decking": "#8a6446", "composite_decking": "#6f6258", "stepping_stone": "#9c9488",
}
BED_SURFACES = {
    "mulch_dark": "#3f2c20", "mulch": "#6a4a32", "mulch_red": "#7a3a28", "pine_straw": "#9a6a3a",
    "river_rock": "#9a948a", "gravel": "#c4b9a5", "lava_rock": "#6a3a2a", "soil": "#4a3828",
}


LAWN_SEASON = {"spring": ("#5a8a30", 0.95), "summer": ("#4f7a2c", 0.9),
               "fall": ("#5a7a34", 0.8), "winter": ("#7d7f4e", 0.35)}


def _season(ctx):
    from ..catalog import _SEASON_OF_MONTH
    m = ctx.month if not ctx.southern else (ctx.month + 5) % 12 + 1
    return _SEASON_OF_MONTH[m]


def _polygon(raw, ctx, key="polygon"):
    pts = ctx.project.pts(raw[key], raw.get("frame"))
    poly = geometry.ensure_ccw([p[:2] for p in pts])
    if len(poly) < 3:
        ctx.error(f"{key} needs at least 3 points")
        return None
    if geometry.self_intersections(poly):
        ctx.warn(f"{key} crosses itself — check point order")
    smooth = int(raw.get("smooth", 0))
    if smooth:
        poly = geometry.smooth_polygon(poly, smooth)
    return poly


@register
class Lawn(ElementType):
    kind = "lawn"
    layer = 10
    keys = {"polygon", "smooth", "grass"}
    required = {"polygon"}

    def resolve(self, raw, ctx):
        if not self.check_keys(raw, ctx):
            return []
        poly = _polygon(raw, ctx)
        if not poly:
            return []
        g = raw.get("grass", {}) or {}
        # Cool-season turf through the year (override with grass.color / grass.health).
        season_color, season_health = LAWN_SEASON[_season(ctx)]
        return [{
            "kind": "lawn", "polygon": poly,
            "grass": {
                "height": ctx.project.length(g.get("height"), 0.07),
                "color": g.get("color", season_color),
                "health": float(g.get("health", season_health)),  # 0 = patchy/brown, 1 = golf course
            },
        }]

    def preview(self, rec, svg):
        svg.polygon(rec["polygon"], fill="#9cc77a", stroke="#6f9a50", width=0.04, opacity=0.9)


@register
class Bed(ElementType):
    kind = "bed"
    layer = 20
    keys = {"polygon", "smooth", "surface", "edging", "mound"}
    required = {"polygon"}

    def resolve(self, raw, ctx):
        if not self.check_keys(raw, ctx):
            return []
        poly = _polygon(raw, ctx)
        if not poly:
            return []
        surface = raw.get("surface", "mulch_dark")
        if surface not in BED_SURFACES:
            ctx.warn(f"unknown bed surface {surface!r}; known: {', '.join(BED_SURFACES)}")
        edging = raw.get("edging", "spade")
        if isinstance(edging, str):
            edging = {"type": edging}
        return [{
            "kind": "bed", "polygon": poly, "surface": surface,
            "edging": {"type": edging.get("type", "spade"),  # spade | steel | stone | brick | paver | none
                       "height": ctx.project.length(edging.get("height"), 0.04),
                       "width": ctx.project.length(edging.get("width"), None)},
            "mound": ctx.project.length(raw.get("mound"), 0.05),
        }]

    def preview(self, rec, svg):
        svg.polygon(rec["polygon"], fill=BED_SURFACES.get(rec["surface"], "#6a4a32"),
                    stroke="#2a1d14", width=0.05, opacity=0.75)


@register
class Paving(ElementType):
    kind = "paving"
    layer = 30
    keys = {"polygon", "smooth", "material", "thickness", "raise", "pattern", "joint_color", "scale"}
    required = {"polygon"}

    def resolve(self, raw, ctx):
        if not self.check_keys(raw, ctx):
            return []
        poly = _polygon(raw, ctx)
        if not poly:
            return []
        return [self.record(poly, raw, ctx)]

    @staticmethod
    def record(poly, raw, ctx, **extra):
        mat = raw.get("material", "concrete")
        if mat not in PAVING_MATERIALS:
            ctx.warn(f"unknown paving material {mat!r}; known: {', '.join(PAVING_MATERIALS)}")
        return {
            "kind": "paving", "polygon": poly, "material": mat,
            "thickness": ctx.project.length(raw.get("thickness"), 0.10),
            "raise": ctx.project.length(raw.get("raise"), 0.03),  # top above finished grade
            "pattern": raw.get("pattern"), "joint_color": raw.get("joint_color"),
            "scale": float(raw.get("scale", 1.0)), **extra,
        }

    def preview(self, rec, svg):
        svg.polygon(rec["polygon"], fill=PAVING_MATERIALS.get(rec["material"], "#ccc"),
                    stroke="#555", width=0.05, opacity=0.95)


@register
class Path(ElementType):
    """A walkway authored as a centerline + width. Emits paving (or stepping-stone pads)."""
    kind = "path"
    layer = 30
    keys = {"line", "width", "material", "smooth", "thickness", "raise", "style", "stone_size",
            "gap", "pattern", "scale", "joint_color"}
    required = {"line", "width"}

    def resolve(self, raw, ctx):
        if not self.check_keys(raw, ctx):
            return []
        line = [p[:2] for p in ctx.project.pts(raw["line"], raw.get("frame"))]
        if len(line) < 2:
            ctx.error("line needs at least 2 points")
            return []
        if raw.get("smooth"):
            line = geometry.smooth_polyline(line, int(raw["smooth"]))
        width = ctx.project.length(raw["width"])
        if raw.get("style") == "stepping_stones":
            size = ctx.project.length(raw.get("stone_size"), width)
            gap = ctx.project.length(raw.get("gap"), 0.15)
            recs = []
            for i, (x, y, _h) in enumerate(geometry.resample_polyline(line, size + gap)):
                pad = _irregular_pad(x, y, size / 2, seed=ctx.seed + i)
                recs.append(Paving.record(pad, {**raw, "material": raw.get("material", "stepping_stone")},
                                          ctx, sub=i))
            return recs
        poly = geometry.offset_polyline_to_polygon(line, width)
        return [Paving.record(poly, raw, ctx, centerline=line)]


def _irregular_pad(x, y, r, seed):
    import math
    import random
    rng = random.Random(seed)
    n = 9
    return [(x + r * rng.uniform(0.85, 1.05) * math.cos(2 * math.pi * k / n),
             y + r * rng.uniform(0.85, 1.05) * math.sin(2 * math.pi * k / n)) for k in range(n)]
