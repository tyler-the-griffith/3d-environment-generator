"""Walls and fences — extruded along polylines, following or stepping with grade."""
from __future__ import annotations

import math

import bmesh
from mathutils import Vector

from .. import materials
from ..util import mesh_from_bmesh, new_object
from . import builder


def _densify(line, step=0.5):
    out = []
    for a, b in zip(line, line[1:]):
        a, b = Vector(a[:2]), Vector(b[:2])
        n = max(1, int(math.ceil((b - a).length / step)))
        out += [a.lerp(b, i / n) for i in range(n)]
    out.append(Vector(line[-1][:2]))
    return out


def _offsets(pts, half):
    """Left/right offset points with mitered joints."""
    left, right = [], []
    n = len(pts)
    for i in range(n):
        if i == 0:
            d = (pts[1] - pts[0]).normalized()
        elif i == n - 1:
            d = (pts[-1] - pts[-2]).normalized()
        else:
            d = ((pts[i] - pts[i - 1]).normalized() + (pts[i + 1] - pts[i]).normalized()).normalized()
        nrm = Vector((-d.y, d.x))
        k = 1.0
        if 0 < i < n - 1:
            d1 = (pts[i] - pts[i - 1]).normalized()
            k = min(3.0, 1 / max(0.2, nrm.dot(Vector((-d1.y, d1.x)))))
        left.append(pts[i] + nrm * half * k)
        right.append(pts[i] - nrm * half * k)
    return left, right


def extrude_band(name, line, thickness, bottom_fn, top_fn, col, material):
    """Solid band along a polyline. bottom_fn/top_fn(i, point2d) → z. UVs: u = distance, v = z (meters)."""
    pts = _densify(line)
    left, right = _offsets(pts, thickness / 2)
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    dist = [0.0]
    for a, b in zip(pts, pts[1:]):
        dist.append(dist[-1] + (b - a).length)
    rows = []
    for i, p in enumerate(pts):
        zb, zt = bottom_fn(i, p), top_fn(i, p)
        rows.append([bm.verts.new((left[i].x, left[i].y, zb)), bm.verts.new((left[i].x, left[i].y, zt)),
                     bm.verts.new((right[i].x, right[i].y, zt)), bm.verts.new((right[i].x, right[i].y, zb))])
    for i in range(len(rows) - 1):
        a, b = rows[i], rows[i + 1]
        for k in range(4):
            f = bm.faces.new([a[k], b[k], b[(k + 1) % 4], a[(k + 1) % 4]])
            for loop in f.loops:
                vi = 0 if loop.vert in a else 1
                loop[uv].uv = (dist[i + vi], loop.vert.co.z)
    bm.faces.new(rows[0][::-1])
    bm.faces.new(rows[-1])
    bm.normal_update()
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = mesh_from_bmesh(name, bm)
    me.materials.append(material)
    return new_object(name, me, col)


@builder("wall")
def wall(rec, ctx):
    line = rec["line"]
    pts = _densify(line)
    grades = [ctx.ground.z(p.x, p.y) for p in pts]
    H = rec["height"]
    level_top = max(grades) + H
    col = ctx.col("Walls")
    mat = materials.get(rec["material"], uv=True)
    top_fn = (lambda i, p: level_top) if rec["top"] == "level" else (lambda i, p: grades[i] + H)
    cap = 0.08 if rec["cap"] else 0.0
    obj = extrude_band(rec["id"], line, rec["thickness"], lambda i, p: grades[i] - 0.25,
                       lambda i, p: top_fn(i, p) - cap, col, mat)
    if cap:
        extrude_band(rec["id"] + "_cap", line, rec["thickness"] + 0.05, lambda i, p: top_fn(i, p) - cap,
                     top_fn, col, materials.get("concrete", color="#c8c0b0"))
    return obj


@builder("fence")
def fence(rec, ctx):
    col = ctx.col("Fences")
    line = rec["line"]
    H = rec["height"]
    color = rec["color"]
    style = rec["style"]
    wood = materials.get("decking", color=color, scale=1.0) if style not in ("metal",) else \
        materials.get("flat", color="#1e1e1e", roughness=0.4, metallic=0.8)
    pts = _densify(line, rec["post_spacing"])
    bm = bmesh.new()

    def box(center, size, rot=0.0):
        res = bmesh.ops.create_cube(bm, size=1.0)
        for v in res["verts"]:
            v.co = Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2]))
            v.co.rotate(_rotz(rot))
            v.co += Vector(center)

    for p in pts:  # posts
        z = ctx.ground.z(p.x, p.y)
        ph = H + (0.05 if style != "metal" else 0)
        box((p.x, p.y, z + ph / 2 - 0.1), (0.09, 0.09, ph + 0.2) if style != "metal" else (0.05, 0.05, ph + 0.2))
    for a, b in zip(pts, pts[1:]):
        d = b - a
        L = d.length
        ang = math.atan2(d.y, d.x)
        mid = (a + b) / 2
        z = (ctx.ground.z(a.x, a.y) + ctx.ground.z(b.x, b.y)) / 2
        if style in ("privacy",):
            n = max(1, int(L / 0.14))
            for k in range(n):
                q = a + d * ((k + 0.5) / n)
                box((q.x, q.y, z + H / 2 + 0.03), (L / n * 0.97, 0.02, H - 0.05), ang)
        elif style in ("picket", "metal"):
            spacing = 0.15 if style == "picket" else 0.12
            n = max(1, int(L / spacing))
            for k in range(n):
                q = a + d * ((k + 0.5) / n)
                w = 0.08 if style == "picket" else 0.02
                box((q.x, q.y, z + H / 2 + 0.05), (w, 0.02, H - 0.05), ang)
            for rh in (0.25, 0.8):
                box((mid.x, mid.y, z + H * rh), (L, 0.04, 0.06), ang)
        elif style == "horizontal":
            n = max(2, int(H / 0.16))
            for k in range(n):
                box((mid.x, mid.y, z + 0.08 + k * (H - 0.1) / n + 0.06), (L, 0.025, 0.13), ang)
        else:  # rail / split_rail
            for rh in ((0.35, 0.75) if style == "rail" else (0.3, 0.6, 0.9)):
                box((mid.x, mid.y, z + H * rh), (L, 0.07, 0.1), ang)
    me = mesh_from_bmesh(rec["id"], bm)
    me.materials.append(wood)
    return new_object(rec["id"], me, col)


def _rotz(a):
    from mathutils import Matrix
    return Matrix.Rotation(a, 3, "Z")
