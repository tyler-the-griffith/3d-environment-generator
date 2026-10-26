"""Terrain, lawn, beds, paving."""
from __future__ import annotations

import bmesh

from .. import materials
from ..util import dist_to_loop, mesh_from_bmesh, new_object, polygon_mesh, set_smooth
from . import builder

LAWN_Z = 0.005  # lawn surface above finished grade


def build_terrain(scene, col, quality, heights=None):
    """Height-grid terrain (flat, graded, or derived from a scan) covering the site plus margin."""
    t = scene["site"]["terrain"]
    nx, ny, res, x0, y0 = t["nx"], t["ny"], t["res"], t["x0"], t["y0"]
    heights = heights if heights is not None else t.get("heights")
    if heights is None:  # flat: a coarse grid is enough
        step = max(1, int(round(4.0 / res)))
    else:
        step = 1
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    grid = {}
    for j in range(0, ny, step):
        for i in range(0, nx, step):
            z = heights[j * nx + i] if heights else 0.0
            grid[i, j] = bm.verts.new((x0 + i * res, y0 + j * res, z - 0.002))
    js, is_ = list(range(0, ny, step)), list(range(0, nx, step))
    for a, b in zip(js, js[1:]):
        for c, d in zip(is_, is_[1:]):
            f = bm.faces.new([grid[c, a], grid[d, a], grid[d, b], grid[c, b]])
            for loop in f.loops:
                loop[uv].uv = (loop.vert.co.x, loop.vert.co.y)
    me = mesh_from_bmesh("Terrain", bm)
    set_smooth(me)
    obj = new_object("Terrain", me, col)
    surround = scene["environment"].get("surroundings", "lawn")
    mat = {"lawn": "lawn_ground", "field": "lawn_ground", "none": "soil"}.get(surround, "lawn_ground")
    me.materials.append(materials.get(mat, color="#5a7a34", health=0.75))
    # Ground to the horizon: a large plane just under the terrain's lowest edge.
    zmin = min(heights) if heights else 0.0
    bm = bmesh.new()
    R = 3000.0
    cx, cy = x0 + nx * res / 2, y0 + ny * res / 2
    bm.faces.new([bm.verts.new((cx + dx * R, cy + dy * R, zmin - 0.15)) for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
    far = mesh_from_bmesh("Horizon", bm)
    far.materials.append(materials.get(mat, color="#5f7a3a", health=0.7))
    new_object("Horizon", far, col)
    return obj


def _cutters(ctx, kinds):
    out = []
    for r in ctx.scene["elements"]:
        if r["kind"] == "structure" and "structure" in kinds:
            out += [w["footprint"] for w in r.get("wings", []) if w.get("walls", True)] or [r.get("footprint")]
        elif r["kind"] in kinds:
            out.append(r.get("polygon") or r.get("footprint"))
        elif r["kind"] == "wall" and "wall" in kinds:
            out.append(ctx.wall_polygon(r))
    return [c for c in out if c]


@builder("lawn")
def lawn(rec, ctx):
    cut = _cutters(ctx, {"bed", "paving", "structure", "wall"})
    me = polygon_mesh(rec["id"], rec["polygon"], ctx.ground, lambda x, y, gz: gz + LAWN_Z,
                      step=0.5, cutters=cut, seed=1)
    set_smooth(me)
    g = rec["grass"]
    me.materials.append(materials.get("lawn_ground", color=g["color"], health=g["health"]))
    obj = new_object(rec["id"], me, ctx.col("Lawn"))
    mode = ctx.render["grass"]
    if mode != "texture":
        ctx.add_grass(obj, g, mode)
    return obj


@builder("bed")
def bed(rec, ctx):
    poly = rec["polygon"]
    mound = rec["mound"]
    inner = ctx.scene["elements"]
    cut = [r["polygon"] for r in inner if r["kind"] == "paving" and r is not rec]

    def z(x, y, gz):
        d = dist_to_loop(x, y, poly)
        s = min(1.0, d / 0.6)
        s = s * s * (3 - 2 * s)  # smoothstep: sunk at the spade edge, mounded inside
        return gz - 0.025 + (mound + 0.02) * s

    me = polygon_mesh(rec["id"], poly, ctx.ground, z, step=0.35, cutters=cut, seed=2)
    set_smooth(me)
    me.materials.append(materials.get(rec["surface"]))
    obj = new_object(rec["id"], me, ctx.col("Beds"))
    edge = rec["edging"]["type"]
    if edge in ("steel", "aluminum"):
        ctx.edge_strip(rec["id"] + "_edge", poly, height=0.025, width=0.005, color="#2a2522", depth=0.08)
    elif edge in ("stone", "brick", "paver", "block"):
        ctx.edge_units(rec["id"] + "_edge", poly, edge, rec["edging"].get("width"))
    return obj


@builder("paving")
def paving(rec, ctx):
    top = rec["raise"]
    me = polygon_mesh(rec["id"], rec["polygon"], ctx.ground, lambda x, y, gz: gz + top, step=0.75, seed=3)
    params = {"scale": rec.get("scale", 1.0)}
    if rec.get("joint_color"):
        params["joint_color"] = rec["joint_color"]
    if rec["material"] in ("decking", "composite_decking", "concrete", "stamped_concrete", "exposed_aggregate",
                           "pavers", "pavers_gray", "brick", "flagstone", "stepping_stone", "bluestone"):
        mat = materials.get(rec["material"], **params)
    else:
        mat = materials.get(rec["material"])
    me.materials.append(mat)
    obj = new_object(rec["id"], me, ctx.col("Hardscape"))
    sol = obj.modifiers.new("thickness", "SOLIDIFY")
    sol.thickness = rec["thickness"]
    sol.offset = -1
    sol.use_even_offset = True
    if rec["material"] in ("stepping_stone", "flagstone", "bluestone", "pavers", "brick"):
        bev = obj.modifiers.new("bevel", "BEVEL")
        bev.width = 0.01
        bev.segments = 2
        bev.limit_method = "ANGLE"
    return obj



def ground_from_scan(scene, scan_objs, window_m=None):
    """Bare-earth heights from a photogrammetry surface (which includes roofs and existing trees).

    1. Sample the scan top-down on the terrain grid (a DSM).
    2. Morphological opening (min filter then max filter) with a window wider than the
       largest building/tree removes everything that sticks up — a simple progressive-
       morphological ground filter, the same idea ODM/PDAL use for DTMs.
    3. Fill grid cells the scan doesn't cover from the nearest covered cells, then smooth.
    """
    import numpy as np
    from ..util import Ground
    t = scene["site"]["terrain"]
    nx, ny, res, x0, y0 = t["nx"], t["ny"], t["res"], t["x0"], t["y0"]
    window_m = window_m or t.get("filter_window", 14.0)
    g = Ground(scan_objs)
    dsm = np.full((ny, nx), np.nan)
    from mathutils import Vector
    for j in range(ny):
        for i in range(nx):
            hit = g.bvh.ray_cast(Vector((x0 + i * res, y0 + j * res, 10000.0)), Vector((0, 0, -1)))
            if hit[0] is not None:
                dsm[j, i] = hit[0].z
    covered = ~np.isnan(dsm)
    if not covered.any():
        raise RuntimeError("scan mesh doesn't cover the site — check its alignment")
    k = max(1, int(round(window_m / res / 2)))
    big = np.nanmax(dsm[covered]) + 100
    work = np.where(covered, dsm, big)

    def slide(a, fn):
        out = a.copy()
        for axis in (0, 1):
            pad = [(0, 0), (0, 0)]
            pad[axis] = (k, k)
            p = np.pad(out, pad, mode="edge")
            stack = [np.take(p, range(o, o + a.shape[axis]), axis=axis) for o in range(2 * k + 1)]
            out = fn(np.stack(stack), axis=0)
        return out

    opened = slide(slide(work, np.min), np.max)
    opened = np.where(covered, np.minimum(opened, dsm), np.nan)
    # Fill uncovered cells by repeated neighbour averaging (diffusion), then smooth lightly.
    filled = np.where(np.isnan(opened), np.nanmean(opened), opened)
    known = ~np.isnan(opened)
    for _ in range(200):
        p = np.pad(filled, 1, mode="edge")
        avg = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) / 4
        filled = np.where(known, filled, avg)
    for _ in range(3):
        p = np.pad(filled, 1, mode="edge")
        filled = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:] + 4 * filled) / 8
    return [float(v) for v in filled.ravel()]
