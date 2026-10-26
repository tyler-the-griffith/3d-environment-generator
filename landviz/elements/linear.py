"""Linear built elements: walls, fences, standalone edging."""
from __future__ import annotations

from .. import geometry
from . import ElementType, register

WALL_MATERIALS = {
    "block": "#a49a8a", "stone": "#8f8577", "fieldstone": "#8a7f70", "brick": "#9c4f3a",
    "concrete": "#bdb8ae", "timber": "#6a4f38", "gabion": "#8a8478",
}
FENCE_STYLES = {"privacy", "picket", "rail", "horizontal", "metal", "split_rail"}


def _line(raw, ctx):
    line = [p[:2] for p in ctx.project.pts(raw["line"], raw.get("frame"))]
    if len(line) < 2:
        ctx.error("line needs at least 2 points")
        return None
    if raw.get("closed"):
        line = line + [line[0]]
    return line


@register
class Wall(ElementType):
    kind = "wall"
    layer = 40
    keys = {"line", "height", "thickness", "material", "top", "cap", "closed"}
    required = {"line", "height"}

    def resolve(self, raw, ctx):
        if not self.check_keys(raw, ctx):
            return []
        line = _line(raw, ctx)
        if not line:
            return []
        mat = raw.get("material", "block")
        if mat not in WALL_MATERIALS:
            ctx.warn(f"unknown wall material {mat!r}; known: {', '.join(WALL_MATERIALS)}")
        return [{
            "kind": "wall", "line": line, "material": mat,
            "height": ctx.project.length(raw["height"]),
            "thickness": ctx.project.length(raw.get("thickness"), 0.30),
            "top": raw.get("top", "level"),  # level: flat top at the highest grade; follow: parallels grade
            "cap": raw.get("cap", True),
        }]

    def preview(self, rec, svg):
        poly = geometry.offset_polyline_to_polygon(rec["line"], rec["thickness"])
        svg.polygon(poly, fill=WALL_MATERIALS.get(rec["material"], "#999"), stroke="#333", width=0.04)


@register
class Fence(ElementType):
    kind = "fence"
    layer = 45
    keys = {"line", "height", "style", "color", "post_spacing", "closed"}
    required = {"line", "height"}

    def resolve(self, raw, ctx):
        if not self.check_keys(raw, ctx):
            return []
        line = _line(raw, ctx)
        if not line:
            return []
        style = raw.get("style", "privacy")
        if style not in FENCE_STYLES:
            ctx.warn(f"unknown fence style {style!r}; known: {', '.join(sorted(FENCE_STYLES))}")
        return [{
            "kind": "fence", "line": line, "style": style,
            "height": ctx.project.length(raw["height"]),
            "color": raw.get("color", "#8a6a4a"),
            "post_spacing": ctx.project.length(raw.get("post_spacing"), 2.4),
        }]

    def preview(self, rec, svg):
        svg.polyline(rec["line"], stroke="#5a3a1a", width=0.12, dash="0.3,0.15")
