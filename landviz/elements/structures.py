"""Buildings and imported models."""
from __future__ import annotations

from .. import geometry, units
from . import ElementType, register


@register
class Structure(ElementType):
    """Procedural building massing: footprint + wall height + roof. Good enough for a house
    that's context, not subject. Replace with a `mesh` (photogrammetry scan) when available."""
    kind = "structure"
    layer = 60
    keys = {"footprint", "wall_height", "roof", "colors", "floor_raise", "windows", "doors", "garage_doors"}
    required = {"footprint", "wall_height"}

    def resolve(self, raw, ctx):
        if not self.check_keys(raw, ctx):
            return []
        fp = geometry.ensure_ccw([p[:2] for p in ctx.project.pts(raw["footprint"], raw.get("frame"))])
        roof = raw.get("roof", {}) or {}
        colors = {"walls": "#d9d4c7", "roof": "#3d3b3a", "trim": "#f2f0ea", "door": "#3a2a22",
                  "foundation": "#9a958c", **(raw.get("colors") or {})}

        def openings(key):
            out = []
            for o in raw.get(key, []) or []:
                out.append({"edge": int(o["edge"]), "at": float(o.get("at", 0.5)),
                            "width": ctx.project.length(o.get("width"), 0.9),
                            "height": ctx.project.length(o.get("height"), 1.4),
                            "sill": ctx.project.length(o.get("sill"), 0.9 if key == "windows" else 0.0),
                            "count": int(o.get("count", 1)),
                            "spacing": ctx.project.length(o.get("spacing"), 1.5)})
            return out

        return [{
            "kind": "structure", "footprint": fp,
            "wall_height": ctx.project.length(raw["wall_height"]),
            "floor_raise": ctx.project.length(raw.get("floor_raise"), 0.3),
            "roof": {"type": roof.get("type", "hip"),  # gable | hip | flat | shed
                     "pitch_deg": units.pitch(roof.get("pitch", "6/12")),
                     "overhang": ctx.project.length(roof.get("overhang"), 0.4),
                     "ridge": roof.get("ridge", "auto")},  # auto | x | y (gable/shed ridge direction)
            "colors": colors,
            # Openings are placed per footprint edge (edge i = footprint[i]→footprint[i+1]).
            "windows": openings("windows"),
            "doors": openings("doors"),
            "garage_doors": openings("garage_doors"),
        }]

    def preview(self, rec, svg):
        svg.polygon(rec["footprint"], fill="#d8d2c6", stroke="#222", width=0.08, hatch=True)
        for i, (a, b) in enumerate(zip(rec["footprint"], rec["footprint"][1:] + rec["footprint"][:1])):
            svg.text(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), f"e{i}", size=0.5, color="#a33")


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
