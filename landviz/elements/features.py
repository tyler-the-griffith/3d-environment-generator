"""Small hardscape features: boulders, landscape lights."""
from __future__ import annotations

import random

from .. import geometry
from . import ElementType, register


@register
class Boulder(ElementType):
    kind = "boulder"
    layer = 55
    keys = {"at", "size", "polygon", "count", "color", "bury", "adjust"}

    def resolve(self, raw, ctx):
        if not self.check_keys(raw, ctx):
            return []
        size = ctx.project.length(raw.get("size"), 0.8)
        rng = random.Random(f"{ctx.seed}:{ctx.element_id}")
        if "polygon" in raw:
            poly = [p[:2] for p in ctx.project.pts(raw["polygon"], raw.get("frame"))]
            pts = geometry.scatter_polygon(poly, int(raw.get("count", 3)), seed=rng.randrange(1 << 16),
                                           min_dist=size)
        elif "at" in raw:
            pts = [ctx.project.pt(raw["at"], raw.get("frame"))[:2]]
        else:
            ctx.error("boulder needs `at` or `polygon`")
            return []
        from .plants import adjusted
        sizes = [size * rng.uniform(0.7, 1.2) for _ in pts]
        seeds = [rng.randrange(1 << 30) for _ in pts]
        out = []
        for i, x, y, ov in adjusted(pts, raw, ctx):
            s = ctx.project.length(ov["height"]) if "height" in ov else sizes[i]
            out.append({"kind": "boulder", "x": x, "y": y, "size": s, "seed": seeds[i],
                        "color": raw.get("color", "#8a857c"), "bury": float(raw.get("bury", 0.3)),
                        **({"sub": i} if "polygon" in raw else {})})
        return out

    def preview(self, rec, svg):
        svg.circle((rec["x"], rec["y"]), rec["size"] / 2, fill=rec["color"], stroke="#333", width=0.03)


@register
class Light(ElementType):
    """Landscape lighting fixture — only visible in dusk/night renders."""
    kind = "light"
    layer = 80
    keys = {"at", "type", "color", "power", "aim"}
    required = {"at"}

    def resolve(self, raw, ctx):
        if not self.check_keys(raw, ctx):
            return []
        x, y = ctx.project.pt(raw["at"], raw.get("frame"))[:2]
        return [{"kind": "light", "x": x, "y": y, "type": raw.get("type", "path"),  # path | uplight | wall
                 "color": raw.get("color", "#ffd9a0"), "power": float(raw.get("power", 5.0)),
                 "aim": ctx.project.pt(raw["aim"])[:2] if "aim" in raw else None}]

    def preview(self, rec, svg):
        svg.circle((rec["x"], rec["y"]), 0.15, fill="#fc0", stroke="#a80", width=0.03)
