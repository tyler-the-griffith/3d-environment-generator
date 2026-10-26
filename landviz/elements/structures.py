"""Buildings and imported models."""
from __future__ import annotations

from .. import geometry, units
from . import ElementType, register


ROOF_TYPES = {"gable", "hip", "half_hip", "shed", "flat", "gambrel", "mansard", "custom"}


def _obb_rect(poly):
    """Minimum-area bounding rectangle of a polygon (CCW corners)."""
    import math
    best = None
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        L = math.hypot(bx - ax, by - ay)
        if L < 1e-9:
            continue
        ux, uy = (bx - ax) / L, (by - ay) / L
        us = [x * ux + y * uy for x, y in poly]
        vs = [-x * uy + y * ux for x, y in poly]
        area = (max(us) - min(us)) * (max(vs) - min(vs))
        if best is None or area < best[0]:
            best = (area, ux, uy, min(us), max(us), min(vs), max(vs))
    _, ux, uy, u0, u1, v0, v1 = best
    pt = lambda u, v: (u * ux - v * uy, u * uy + v * ux)  # noqa: E731
    return [pt(u0, v0), pt(u1, v0), pt(u1, v1), pt(u0, v1)]


@register
class Structure(ElementType):
    """A building as one or more *wings*: each a convex footprint with walls up to an eave and
    its own roof. Overlapping wings make L/T shapes and cross-gables (their roofs intersect
    into valleys). A single footprint (no `wings`) is one wing. See landviz/roofs.py."""
    kind = "structure"
    layer = 60
    keys = {"footprint", "wall_height", "roof", "colors", "floor_raise", "windows", "doors", "garage_doors",
            "wings"}
    required = set()

    def resolve(self, raw, ctx):
        from .. import roofs
        if not self.check_keys(raw, ctx):
            return []
        P = ctx.project
        colors = {"walls": "#d9d4c7", "roof": "#3d3b3a", "trim": "#f2f0ea", "door": "#3a2a22",
                  "foundation": "#9a958c", **(raw.get("colors") or {})}
        wings_raw = raw.get("wings") or ([{"footprint": raw["footprint"], "wall_height": raw.get("wall_height"),
                                           "roof": raw.get("roof", {})}] if "footprint" in raw else [])
        if not wings_raw:
            ctx.error("a structure needs `footprint` (+ `wall_height`) or `wings`")
            return []
        default_roof = raw.get("roof") or {}
        wings = []
        for i, w in enumerate(wings_raw):
            if "footprint" not in w:
                ctx.error(f"wing {i}: missing footprint")
                continue
            fp = geometry.ensure_ccw([p[:2] for p in P.pts(w["footprint"], w.get("frame", raw.get("frame")))])
            wall_h = P.length(w.get("wall_height", raw.get("wall_height")))
            if wall_h is None:
                ctx.error(f"wing {i}: missing wall_height")
                continue
            r = {**default_roof, **(w.get("roof") or {})} if "roof" in w or default_roof else {}
            rtype = r.get("type", "hip")
            if rtype not in ROOF_TYPES:
                ctx.warn(f"wing {i}: unknown roof type {rtype!r}; known: {', '.join(sorted(ROOF_TYPES))}")
                rtype = "hip"
            ridge = r.get("ridge", "auto")
            roof = {"type": rtype, "pitch_deg": units.pitch(r.get("pitch", "6/12")), "ridge": ridge,
                    "half_hip": float(r.get("half_hip", 0.6)), "break": float(r.get("break", 0.45)),
                    "lower_pitch_deg": units.pitch(r.get("lower_pitch", "24/12")),
                    "end_pitch_factor": float(r.get("end_pitch_factor", 1.4)),
                    "low_edge": r.get("low_edge"),
                    "edges": {int(k): v for k, v in (r.get("edges") or {}).items()}}
            overhang = P.length(r.get("overhang"), 0.0 if rtype == "flat" else 0.45)
            parapet = P.length(r.get("parapet"), 0.0)
            base = P.length(w.get("base"), 0.0)  # floor of this wing relative to the structure floor
            roof_fp = fp
            if not roofs.is_convex(fp):
                ctx.warn(f"wing {i}: footprint isn't convex — split it into convex wings (rectangles) for an exact "
                         "roof; roofed over its bounding rectangle for now")
                roof_fp = _obb_rect(fp)
            eave = base + wall_h
            wings.append({
                "footprint": fp, "wall_height": wall_h, "base": base, "walls": bool(w.get("walls", True)),
                "roof": roof, "overhang": overhang, "parapet": parapet,
                "roof_faces": roofs.roof_faces(roof_fp, eave, roof, overhang, parapet),
                "wall_profiles": roofs.wall_profiles(fp if roof_fp is fp else roof_fp, eave, roof, parapet)
                if roof_fp is fp else roofs.wall_profiles(fp, eave, {"type": "flat"}, 0.0),
                "colors": {**colors, **(w.get("colors") or {})},
            })

        def openings(key):
            out = []
            for o in raw.get(key, []) or []:
                out.append({"wing": int(o.get("wing", 0)), "edge": int(o["edge"]), "at": float(o.get("at", 0.5)),
                            "width": P.length(o.get("width"), 0.9),
                            "height": P.length(o.get("height"), 1.4),
                            "sill": P.length(o.get("sill"), 0.9 if key == "windows" else 0.0),
                            "count": int(o.get("count", 1)),
                            "spacing": P.length(o.get("spacing"), 1.5)})
            return out

        return [{
            "kind": "structure", "wings": wings,
            "footprint": wings[0]["footprint"] if wings else [],
            "footprints": [w["footprint"] for w in wings],
            "floor_raise": P.length(raw.get("floor_raise"), 0.3),
            "colors": colors,
            # Openings sit on a wing's footprint edge (edge i = footprint[i]→footprint[i+1]).
            "windows": openings("windows"),
            "doors": openings("doors"),
            "garage_doors": openings("garage_doors"),
        }]

    def preview(self, rec, svg):
        for wi, fp in enumerate(rec.get("footprints") or [rec["footprint"]]):
            svg.polygon(fp, fill="#d8d2c6", stroke="#222", width=0.08, hatch=True)
            for i, (a, b) in enumerate(zip(fp, fp[1:] + fp[:1])):
                svg.text(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), f"w{wi}e{i}" if wi else f"e{i}", size=0.5, color="#a33")


@register
class Deck(ElementType):
    """A raised deck or porch floor: slab on posts, optional railing and stairs down to grade."""
    kind = "deck"
    layer = 58
    keys = {"polygon", "height", "thickness", "material", "color", "railing", "stairs", "posts"}
    required = {"polygon", "height"}

    def resolve(self, raw, ctx):
        if not self.check_keys(raw, ctx):
            return []
        P = ctx.project
        poly = geometry.ensure_ccw([p[:2] for p in P.pts(raw["polygon"], raw.get("frame"))])
        rail = raw.get("railing", {})
        if rail is True or rail is None:
            rail = {}
        if rail is False:
            rail = {"style": "none"}
        stairs = []
        for st in raw.get("stairs", []) or []:
            stairs.append({"edge": int(st["edge"]), "at": float(st.get("at", 0.5)),
                           "width": P.length(st.get("width"), 1.1),
                           "rise": P.length(st.get("rise"), 0.18), "run": P.length(st.get("run"), 0.28)})
        return [{"kind": "deck", "polygon": poly, "height": P.length(raw["height"]),
                 "thickness": P.length(raw.get("thickness"), 0.2),
                 "material": raw.get("material", "decking"), "color": raw.get("color", "#8a6446"),
                 "railing": {"style": rail.get("style", "balusters"),  # balusters | horizontal | glass | none
                             "height": P.length(rail.get("height"), 0.95),
                             "color": rail.get("color", raw.get("color", "#8a6446")),
                             "open_edges": [int(e) for e in rail.get("open_edges", [])]},
                 "stairs": stairs, "posts": raw.get("posts", True)}]

    def preview(self, rec, svg):
        svg.polygon(rec["polygon"], fill="#8a6446", stroke="#3a2a1a", width=0.06, opacity=0.9)


@register
class Mesh(ElementType):
    """Import an external model (photogrammetry scan, downloaded asset, CAD export)."""
    kind = "mesh"
    layer = 50
    keys = {"file", "at", "rotation", "scale", "matrix", "drop_to_ground", "keep", "clip_out",
            "alignment", "role"}
    required = {"file"}

    def resolve(self, raw, ctx):
        if not self.check_keys(raw, ctx):
            return []
        path = ctx.project.path(raw["file"])
        if not path.exists():
            ctx.warn(f"mesh file not found: {path} (it will be skipped at build time)")
        rec = {"kind": "mesh", "file": str(path), "role": raw.get("role", "object")}
        if "alignment" in raw:  # control point pairs: model coords → site coords
            import numpy as np
            pairs = raw["alignment"]["points"]
            src = [p["model"] for p in pairs]
            dst = ctx.project.pts([p["site"] for p in pairs])
            if len(pairs) < 3:
                ctx.error("mesh alignment needs ≥3 point pairs")
                return []
            s, R, t = geometry.similarity_fit(src, dst)
            M = np.eye(4)
            M[:3, :3], M[:3, 3] = s * R, t
            rec["matrix"] = M.tolist()
            err = geometry.apply_similarity(s, R, t, src) - np.asarray(dst)
            rec["alignment_rms"] = float(np.sqrt((err ** 2).sum(1).mean()))
            if rec["alignment_rms"] > 0.25:
                ctx.warn(f"alignment RMS error {rec['alignment_rms']:.2f} m — check control points")
        elif "matrix" in raw:
            rec["matrix"] = raw["matrix"]
        else:
            at = ctx.project.pt(raw.get("at", [0, 0, 0]), raw.get("frame"))
            rec["at"] = list(at) + [0.0] * (3 - len(at))
            rec["rotation_deg"] = float(raw.get("rotation", 0))
            rec["scale"] = float(raw.get("scale", 1.0))
        rec["drop_to_ground"] = bool(raw.get("drop_to_ground", "matrix" not in rec))
        # For scans: polygons to keep (e.g. the house) and to cut away (yard to be relandscaped).
        rec["keep"] = [[p[:2] for p in ctx.project.pts(poly, raw.get("frame"))] for poly in raw.get("keep", [])]
        rec["clip_out"] = [[p[:2] for p in ctx.project.pts(poly, raw.get("frame"))] for poly in raw.get("clip_out", [])]
        return [rec]

    def preview(self, rec, svg):
        for poly in rec.get("keep", []):
            svg.polygon(poly, fill="none", stroke="#06c", width=0.08, dash="0.4,0.2")
        if "at" in rec:
            svg.text(rec["at"][:2], "mesh", size=0.6, color="#06c")
