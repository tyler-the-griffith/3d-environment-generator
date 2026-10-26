"""Procedural buildings and imported meshes (incl. photogrammetry scans)."""
from __future__ import annotations

import math
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

from .. import materials
from ..util import log, mesh_from_bmesh, new_object, point_in_poly
from . import builder


def _obb(poly):
    """Minimum-area oriented rectangle: (center, axis_u, axis_v, half_u, half_v) with half_u ≥ half_v."""
    best = None
    pts = [Vector(p[:2]) for p in poly]
    for i in range(len(pts)):
        e = pts[(i + 1) % len(pts)] - pts[i]
        if e.length < 1e-6:
            continue
        u = e.normalized()
        v = Vector((-u.y, u.x))
        us = [p.dot(u) for p in pts]
        vs = [p.dot(v) for p in pts]
        area = (max(us) - min(us)) * (max(vs) - min(vs))
        if best is None or area < best[0]:
            c = u * (max(us) + min(us)) / 2 + v * (max(vs) + min(vs)) / 2
            best = (area, c, u, v, (max(us) - min(us)) / 2, (max(vs) - min(vs)) / 2)
    _, c, u, v, hu, hv = best
    if hv > hu:
        u, v, hu, hv = v, -u, hv, hu
    return c, u, v, hu, hv


@builder("structure")
def structure(rec, ctx):
    col = ctx.col("Structures")
    fp = rec["footprint"]
    colors = rec["colors"]
    pts = [Vector(p[:2]) for p in fp]
    grade = max(ctx.ground.z(p.x, p.y) for p in pts)
    z0 = min(ctx.ground.z(p.x, p.y) for p in pts) - 0.3
    floor = grade + rec["floor_raise"]
    eave = floor + rec["wall_height"]

    # Walls: footprint extruded, UV u = perimeter distance, v = z (meters) for siding courses.
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    base_v = [bm.verts.new((p.x, p.y, z0)) for p in pts]
    top_v = [bm.verts.new((p.x, p.y, eave)) for p in pts]
    dist = 0.0
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        f = bm.faces.new([base_v[i], base_v[j], top_v[j], top_v[i]])
        L = (pts[j] - pts[i]).length
        for loop, (u, v) in zip(f.loops, ((dist, z0), (dist + L, z0), (dist + L, eave), (dist, eave))):
            loop[uv].uv = (u, v)
        dist += L
    bm.faces.new(top_v)
    bm.normal_update()
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = mesh_from_bmesh(rec["id"] + "_walls", bm)
    me.materials.append(materials.get("siding", color=colors["walls"]))
    walls = new_object(rec["id"] + "_walls", me, col)

    # Foundation band.
    bm = bmesh.new()
    for i in range(n):
        j = (i + 1) % n
        d = (pts[j] - pts[i]).normalized()
        out = Vector((d.y, -d.x)) * 0.02
        a, b = pts[i] + out, pts[j] + out
        bm.faces.new([bm.verts.new((a.x, a.y, z0)), bm.verts.new((b.x, b.y, z0)),
                      bm.verts.new((b.x, b.y, floor)), bm.verts.new((a.x, a.y, floor))])
    me = mesh_from_bmesh(rec["id"] + "_foundation", bm)
    me.materials.append(materials.get("concrete", color=colors["foundation"]))
    new_object(rec["id"] + "_foundation", me, col)

    _roof(rec, ctx, pts, eave, col)
    _openings(rec, pts, floor, col)
    return walls


def _roof(rec, ctx, pts, eave, col):
    r = rec["roof"]
    c, u, v, hu, hv = _obb([tuple(p) for p in pts])
    if r["ridge"] == "x":
        u, v, hu, hv = (Vector((1, 0)), Vector((0, 1)), hu, hv) if abs(u.x) > abs(u.y) else (v, -u, hv, hu)
    elif r["ridge"] == "y":
        u, v, hu, hv = (u, v, hu, hv) if abs(u.y) > abs(u.x) else (v, -u, hv, hu)
    o = r["overhang"]
    slope = math.tan(math.radians(r["pitch_deg"]))
    hu_o, hv_o = hu + o, hv + o
    z_e = eave - o * slope
    rise = hv * slope
    z_r = eave + rise
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")

    def P(su, sv, z):
        p = c + u * su + v * sv
        return bm.verts.new((p.x, p.y, z))

    faces = []
    kind = r["type"]
    if kind == "flat":
        a, b, cc, d = P(-hu_o, -hv_o, eave + 0.3), P(hu_o, -hv_o, eave + 0.3), P(hu_o, hv_o, eave + 0.3), P(-hu_o, hv_o, eave + 0.3)
        faces.append([a, b, cc, d])
    elif kind == "shed":
        hi = eave + 2 * hv * slope
        a, b = P(-hu_o, -hv_o, z_e), P(hu_o, -hv_o, z_e)
        cc, d = P(hu_o, hv_o, hi + o * slope), P(-hu_o, hv_o, hi + o * slope)
        faces.append([a, b, cc, d])
    else:
        ridge_half = (hu - hv) if kind == "hip" else hu_o
        ridge_half = max(0.0, ridge_half)
        r1, r2 = P(-ridge_half, 0, z_r), P(ridge_half, 0, z_r)
        e1, e2, e3, e4 = P(-hu_o, -hv_o, z_e), P(hu_o, -hv_o, z_e), P(hu_o, hv_o, z_e), P(-hu_o, hv_o, z_e)
        if kind == "hip":
            faces += [[e1, e2, r2, r1], [e3, e4, r1, r2], [e2, e3, r2], [e4, e1, r1]]
        else:  # gable
            faces += [[e1, e2, r2, r1], [e3, e4, r1, r2]]
    for vs in faces:
        f = bm.faces.new(vs)
        # UVs along the slope in meters (shingle courses run along the eave).
        e = (vs[1].co - vs[0].co).normalized()
        up = Vector((0, 0, 1)).cross(e).cross(e).normalized() * -1
        for loop in f.loops:
            loop[uv].uv = (loop.vert.co.dot(e), (loop.vert.co - vs[0].co).dot(up) if up.length else loop.vert.co.z)
    bm.normal_update()
    for f in bm.faces:
        if f.normal.z < 0:
            f.normal_flip()
    me = mesh_from_bmesh(rec["id"] + "_roof", bm)
    me.materials.append(materials.get("shingles", color=rec["colors"]["roof"]))
    roof = new_object(rec["id"] + "_roof", me, col)
    sol = roof.modifiers.new("thick", "SOLIDIFY")
    sol.thickness = 0.12
    sol.offset = -1
    if kind == "gable":  # gable-end wall triangles
        bm = bmesh.new()
        uv = bm.loops.layers.uv.new("UVMap")
        for s in (-1, 1):
            a, b, t = P(s * hu, -hv, eave), P(s * hu, hv, eave), P(s * hu, 0, z_r)
            f = bm.faces.new([a, b, t])
            for loop in f.loops:
                loop[uv].uv = (loop.vert.co.xy.dot(v), loop.vert.co.z)
        me = mesh_from_bmesh(rec["id"] + "_gables", bm)
        me.materials.append(materials.get("siding", color=rec["colors"]["walls"]))
        new_object(rec["id"] + "_gables", me, col)
    # Fascia/trim band under the eaves.
    bm = bmesh.new()
    corners = [c + u * su + v * sv for su, sv in ((-hu_o, -hv_o), (hu_o, -hv_o), (hu_o, hv_o), (-hu_o, hv_o))]
    if kind in ("hip", "flat", "gable"):
        for i in range(4):
            if kind == "gable" and i in (1, 3):
                continue
            a, b = corners[i], corners[(i + 1) % 4]
            zz = z_e if kind != "flat" else eave + 0.3
            bm.faces.new([bm.verts.new((a.x, a.y, zz - 0.2)), bm.verts.new((b.x, b.y, zz - 0.2)),
                          bm.verts.new((b.x, b.y, zz)), bm.verts.new((a.x, a.y, zz))])
    me = mesh_from_bmesh(rec["id"] + "_fascia", bm)
    me.materials.append(materials.get("flat", color=rec["colors"]["trim"], roughness=0.5))
    new_object(rec["id"] + "_fascia", me, col)


def _openings(rec, pts, floor, col):
    n = len(pts)
    glass = materials.get("glass")
    trim = materials.get("flat", color=rec["colors"]["trim"], roughness=0.5)
    door = materials.get("flat", color=rec["colors"]["door"], roughness=0.5)
    bm_glass, bm_trim, bm_door = bmesh.new(), bmesh.new(), bmesh.new()

    def panel(bm, a, d, out, w, h, sill, depth, inset=0.0):
        p0 = a + d * (-w / 2)
        p1 = a + d * (w / 2)
        o = out * depth
        vs = [bm.verts.new((p0.x + o.x, p0.y + o.y, floor + sill + inset)),
              bm.verts.new((p1.x + o.x, p1.y + o.y, floor + sill + inset)),
              bm.verts.new((p1.x + o.x, p1.y + o.y, floor + sill + h - inset)),
              bm.verts.new((p0.x + o.x, p0.y + o.y, floor + sill + h - inset))]
        bm.faces.new(vs)

    for key, target in (("windows", "w"), ("doors", "d"), ("garage_doors", "g")):
        for o in rec.get(key, []):
            i = o["edge"] % n
            a, b = Vector(pts[i]), Vector(pts[(i + 1) % n])
            d = (b - a).normalized()
            out = Vector((d.y, -d.x))  # footprint is CCW → right-hand normal points outside
            L = (b - a).length
            cnt = o["count"]
            total = (cnt - 1) * o["spacing"]
            for k in range(cnt):
                center = a + d * (L * o["at"] - total / 2 + k * o["spacing"])
                w, h, sill = o["width"], o["height"], o["sill"]
                panel(bm_trim, center, d, out, w + 0.16, h + 0.16, sill - 0.08, 0.02)
                if target == "w":
                    panel(bm_glass, center, d, out, w, h, sill, 0.035)
                    # muntin cross
                    panel(bm_trim, center, d, out, 0.04, h, sill, 0.04)
                    panel(bm_trim, center, d, out, w, 0.04, sill + h / 2 - 0.02, 0.04)
                elif target == "d":
                    panel(bm_door, center, d, out, w, h, 0.0, 0.035)
                else:
                    panel(bm_trim, center, d, out, w, h, 0.0, 0.035)
                    for row in range(1, 4):  # garage door panel grooves
                        panel(bm_door, center, d, out, w, 0.02, row * h / 4, 0.04)
    for bm, mat, nm in ((bm_glass, glass, "glass"), (bm_trim, trim, "trim"), (bm_door, door, "doors")):
        me = mesh_from_bmesh(f"{rec['id']}_{nm}", bm)
        me.materials.append(mat)
        new_object(f"{rec['id']}_{nm}", me, col)


# ---------------------------------------------------------------- imported meshes


IMPORTERS = {
    ".obj": lambda p: bpy.ops.wm.obj_import(filepath=p, forward_axis="Y", up_axis="Z"),
    ".ply": lambda p: bpy.ops.wm.ply_import(filepath=p),
    ".glb": lambda p: bpy.ops.import_scene.gltf(filepath=p),
    ".gltf": lambda p: bpy.ops.import_scene.gltf(filepath=p),
    ".fbx": lambda p: bpy.ops.import_scene.fbx(filepath=p),
    ".stl": lambda p: bpy.ops.wm.stl_import(filepath=p),
}


def import_model(path: str, col):
    p = Path(path)
    if p.suffix.lower() == ".blend":
        with bpy.data.libraries.load(str(p), link=False) as (src, dst):
            dst.objects = list(src.objects)
        objs = [o for o in dst.objects if o is not None]
        for o in objs:
            col.objects.link(o)
        return objs
    imp = IMPORTERS.get(p.suffix.lower())
    if imp is None:
        raise ValueError(f"can't import {p.suffix}")
    before = set(bpy.data.objects)
    imp(str(p))
    objs = [o for o in bpy.data.objects if o not in before]
    for o in objs:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        col.objects.link(o)
    return objs


@builder("mesh")
def mesh(rec, ctx, clip=True):
    if not Path(rec["file"]).exists():
        log(f"skipping {rec['id']}: {rec['file']} not found")
        return None
    col = ctx.col("Imported")
    objs = import_model(rec["file"], col)
    root = bpy.data.objects.new(rec["id"], None)
    col.objects.link(root)
    for o in objs:
        if o.parent is None:
            o.parent = root
    if "matrix" in rec:
        root.matrix_world = Matrix(rec["matrix"])
    else:
        root.location = rec["at"]
        root.rotation_euler = (0, 0, math.radians(rec["rotation_deg"]))
        root.scale = (rec["scale"],) * 3
    bpy.context.view_layer.update()
    meshes = [o for o in objs if o.type == "MESH"]
    if clip and (rec.get("keep") or rec.get("clip_out")):
        clip_scan(meshes, rec.get("keep", []), rec.get("clip_out", []))
    if rec.get("drop_to_ground") and meshes:
        minz = min((o.matrix_world @ Vector(c)).z for o in meshes for c in o.bound_box)
        x, y = root.matrix_world.translation.xy
        root.location.z += ctx.ground.z(x, y) - minz
    return root


def clip_scan(meshes, keep, clip_out):
    """Delete scan faces inside clip_out polygons unless they're inside a keep polygon.
    Typical use: clip_out = the yard being relandscaped, keep = the house footprint (+ margin)."""
    for o in meshes:
        mw = o.matrix_world
        bm = bmesh.new()
        bm.from_mesh(o.data)
        doomed = []
        for f in bm.faces:
            c = mw @ f.calc_center_median()
            if keep and any(point_in_poly(c.x, c.y, k) for k in keep):
                continue
            if any(point_in_poly(c.x, c.y, k) for k in clip_out):
                doomed.append(f)
        bmesh.ops.delete(bm, geom=doomed, context="FACES")
        bm.to_mesh(o.data)
        bm.free()
