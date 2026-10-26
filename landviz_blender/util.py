"""Shared Blender helpers: collections, colors, polygon meshing, ground sampling."""
from __future__ import annotations

import math
import random

import bmesh
import bpy
from mathutils import Vector
from mathutils import geometry as mg
from mathutils.bvhtree import BVHTree


def log(*a):
    print("landviz:", *a, flush=True)


# ---------------------------------------------------------------- colors


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_rgba(h: str, alpha: float = 1.0):
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), alpha)


def jitter_rgba(rgba, rng: random.Random, amount: float = 0.08):
    f = 1 + rng.uniform(-amount, amount)
    return tuple(min(1.0, c * f * (1 + rng.uniform(-amount, amount) * 0.5)) for c in rgba[:3]) + (rgba[3],)


# ---------------------------------------------------------------- collections / objects


def collection(name: str, parent=None, hidden: bool = False):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(col)
    if hidden:  # library/prototype collections: excluded from the view layer, used via instancing
        lc = _find_layer_collection(bpy.context.view_layer.layer_collection, name)
        if lc:
            lc.exclude = True
    return col


def _find_layer_collection(lc, name):
    if lc.collection.name == name:
        return lc
    for child in lc.children:
        found = _find_layer_collection(child, name)
        if found:
            return found
    return None


def new_object(name, mesh, col):
    obj = bpy.data.objects.new(name, mesh)
    col.objects.link(obj)
    return obj


def mesh_from_bmesh(name, bm):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def set_smooth(me, smooth=True):
    me.shade_smooth() if smooth else me.shade_flat()


# ---------------------------------------------------------------- polygon meshing


def densify_loop(poly, step):
    out = []
    n = len(poly)
    for i in range(n):
        a, b = Vector(poly[i][:2]), Vector(poly[(i + 1) % n][:2])
        L = (b - a).length
        k = max(1, int(math.ceil(L / step)))
        for j in range(k):
            out.append(a.lerp(b, j / k))
    return out


def point_in_poly(x, y, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def dist_to_loop(x, y, poly):
    best = 1e9
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i][0], poly[i][1]
        bx, by = poly[(i + 1) % n][0], poly[(i + 1) % n][1]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0 if L2 == 0 else max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / L2))
        best = min(best, math.hypot(x - ax - t * dx, y - ay - t * dy))
    return best


def triangulate_polygon(poly, step=0.6, cutters=(), seed=0):
    """Constrained Delaunay triangulation of a polygon with interior points every `step` meters
    (so it can follow terrain), minus any `cutters` polygons (exact edges — the cutter outlines
    are constraint edges, so removed regions line up with neighbouring elements precisely).
    Returns (verts2d list[Vector], faces list[tuple])."""
    rng = random.Random(seed)
    outer = densify_loop(poly, step)
    verts = list(outer)
    edges = [(i, (i + 1) % len(outer)) for i in range(len(outer))]
    face = list(range(len(outer)))
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    cut_loops = []
    for c in cutters:
        cx, cy = [p[0] for p in c], [p[1] for p in c]
        if max(cx) < x0 or min(cx) > x1 or max(cy) < y0 or min(cy) > y1:
            continue
        loop = densify_loop(c, step)
        base = len(verts)
        verts += loop
        edges += [(base + i, base + (i + 1) % len(loop)) for i in range(len(loop))]
        cut_loops.append(c)
    # Interior points on a jittered grid, kept away from edges to avoid slivers.
    y = y0 + step / 2
    while y < y1:
        x = x0 + step / 2
        while x < x1:
            px, py = x + rng.uniform(-0.2, 0.2) * step, y + rng.uniform(-0.2, 0.2) * step
            if point_in_poly(px, py, poly) and dist_to_loop(px, py, poly) > step * 0.35 and \
                    all(dist_to_loop(px, py, c) > step * 0.35 for c in cut_loops):
                verts.append(Vector((px, py)))
            x += step
        y += step
    out_verts, _e, out_faces, *_ = mg.delaunay_2d_cdt(verts, edges, [face], 1, 1e-5)
    keep = []
    for f in out_faces:
        cxm = sum(out_verts[i][0] for i in f) / len(f)
        cym = sum(out_verts[i][1] for i in f) / len(f)
        if any(point_in_poly(cxm, cym, c) for c in cut_loops):
            continue
        keep.append(tuple(f))
    return out_verts, keep


def polygon_mesh(name, poly, ground, z_fn=None, step=0.6, cutters=(), seed=0, uv_scale=1.0):
    """Ground-following surface mesh for a polygon. z_fn(x, y, ground_z) → z (default: on grade)."""
    verts2d, faces = triangulate_polygon(poly, step, cutters, seed)
    bm = bmesh.new()
    bverts = []
    for v in verts2d:
        gz = ground.z(v[0], v[1])
        bverts.append(bm.verts.new((v[0], v[1], z_fn(v[0], v[1], gz) if z_fn else gz)))
    uv = bm.loops.layers.uv.new("UVMap")
    for f in faces:
        try:
            face = bm.faces.new([bverts[i] for i in f])
        except ValueError:
            continue
        for loop in face.loops:  # planar world-space UVs in meters
            loop[uv].uv = (loop.vert.co.x * uv_scale, loop.vert.co.y * uv_scale)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bm.normal_update()
    for f in bm.faces:
        if f.normal.z < 0:
            f.normal_flip()
    return mesh_from_bmesh(name, bm)


# ---------------------------------------------------------------- ground


class Ground:
    """Height queries against whatever object(s) represent finished grade."""

    def __init__(self, objects=(), flat_z: float = 0.0):
        self.flat_z = flat_z
        self.bvh = None
        if objects:
            verts, polys = [], []
            dg = bpy.context.evaluated_depsgraph_get()
            for obj in objects:
                ev = obj.evaluated_get(dg)
                me = ev.to_mesh()
                base = len(verts)
                mw = obj.matrix_world
                verts += [mw @ v.co for v in me.vertices]
                polys += [[base + i for i in p.vertices] for p in me.polygons]
                ev.to_mesh_clear()
            if polys:
                self.bvh = BVHTree.FromPolygons(verts, polys)

    def z(self, x, y) -> float:
        if self.bvh is None:
            return self.flat_z
        hit = self.bvh.ray_cast(Vector((x, y, 10000.0)), Vector((0, 0, -1)))
        if hit[0] is None:
            hit = self.bvh.find_nearest(Vector((x, y, 0)))
            return hit[0].z if hit[0] is not None else self.flat_z
        return hit[0].z


def look_at(obj, target):
    d = Vector(target) - obj.location
    obj.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
