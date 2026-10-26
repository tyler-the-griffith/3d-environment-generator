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


@builder("structure")
def structure(rec, ctx):
    """Walls, gable ends and roofs from the resolved wing geometry (landviz/roofs.py)."""
    col = ctx.col("Structures")
    colors = rec["colors"]
    wings = rec.get("wings") or []
    all_pts = [Vector(p[:2]) for w in wings for p in w["footprint"]]
    if not all_pts:
        return None
    grade = max(ctx.ground.z(p.x, p.y) for p in all_pts)
    z0 = min(ctx.ground.z(p.x, p.y) for p in all_pts) - 0.3
    floor = grade + rec["floor_raise"]
    primary = None
    for wi, w in enumerate(wings):
        wc = w["colors"]
        name = f"{rec['id']}_w{wi}"
        if w["walls"]:
            walls = _wing_walls(name, w, floor, z0, col, wc)
            primary = primary or walls
            _foundation(name, w["footprint"], z0, floor + w["base"], col, wc)
        else:
            primary = primary or _posts(name, w, floor, z0, col, wc)
        roof = _wing_roof(name, w, floor, col, wc)
        primary = primary or roof
    _openings(rec, wings, floor, col)
    return primary


def _wing_walls(name, w, floor, z0, col, colors):
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    dist = 0.0
    for wall in w["wall_profiles"]:
        a, b = Vector(wall["a"]), Vector(wall["b"])
        L = (b - a).length
        if L < 1e-6:
            continue
        d = (b - a) / L
        prof = wall["profile"]
        verts = [(a, z0), (b, z0)] + [(a + d * t, floor + z) for t, z in reversed(prof)]
        bverts = [bm.verts.new((p.x, p.y, z)) for p, z in verts]
        try:
            f = bm.faces.new(bverts)
        except ValueError:
            continue
        for loop in f.loops:  # u = distance around the building, v = height (meters) → siding courses
            loop[uv].uv = (dist + (loop.vert.co.xy - a).dot(d), loop.vert.co.z)
        dist += L
    bm.normal_update()
    # Walls are single-sided quads; make normals face outward (footprints are CCW).
    for f in bm.faces:
        c = f.calc_center_median()
        centroid = sum((Vector(p) for p in w["footprint"]), Vector((0, 0))) / len(w["footprint"])
        if f.normal.xy.dot(c.xy - centroid) < 0:
            f.normal_flip()
    me = mesh_from_bmesh(name + "_walls", bm)
    me.materials.append(materials.get("siding", color=colors["walls"]))
    obj = new_object(name + "_walls", me, col)
    sol = obj.modifiers.new("thick", "SOLIDIFY")  # real wall thickness so the walls cast clean shadows
    sol.thickness = 0.2
    sol.offset = -1
    return obj


def _foundation(name, fp, z0, floor, col, colors):
    pts = [Vector(p[:2]) for p in fp]
    bm = bmesh.new()
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        d = (pts[j] - pts[i]).normalized()
        out = Vector((d.y, -d.x)) * 0.02
        a, b = pts[i] + out, pts[j] + out
        bm.faces.new([bm.verts.new((a.x, a.y, z0)), bm.verts.new((b.x, b.y, z0)),
                      bm.verts.new((b.x, b.y, floor)), bm.verts.new((a.x, a.y, floor))])
    me = mesh_from_bmesh(name + "_foundation", bm)
    me.materials.append(materials.get("concrete", color=colors["foundation"]))
    new_object(name + "_foundation", me, col)


def _posts(name, w, floor, z0, col, colors):
    """Open wing (porch, carport, pergola-roof): square posts at the corners instead of walls."""
    bm = bmesh.new()
    top = floor + w["base"] + w["wall_height"]
    for x, y in w["footprint"]:
        res = bmesh.ops.create_cube(bm, size=1.0)
        for v in res["verts"]:
            v.co = Vector((x + v.co.x * 0.15, y + v.co.y * 0.15, z0 + (v.co.z + 0.5) * (top - z0)))
    me = mesh_from_bmesh(name + "_posts", bm)
    me.materials.append(materials.get("flat", color=colors["trim"], roughness=0.5))
    return new_object(name + "_posts", me, col)


def _wing_roof(name, w, floor, col, colors):
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    for face in w["roof_faces"]:
        verts = [bm.verts.new((x, y, floor + z)) for x, y, z in face]
        try:
            f = bm.faces.new(verts)
        except ValueError:
            continue
        f.normal_update()
        if f.normal.z < 0:
            f.normal_flip()
        n = f.normal
        # Shingle courses run along the roof's contour lines.
        e = n.cross(Vector((0, 0, 1)))
        e = e.normalized() if e.length > 1e-6 else Vector((1, 0, 0))
        up = e.cross(n).normalized()
        for loop in f.loops:
            loop[uv].uv = (loop.vert.co.dot(e), loop.vert.co.dot(up))
    me = mesh_from_bmesh(name + "_roof", bm)
    flat = w["roof"]["type"] == "flat"
    me.materials.append(materials.get("flat", color=colors["roof"], roughness=0.8) if flat
                        else materials.get("shingles", color=colors["roof"]))
    roof = new_object(name + "_roof", me, col)
    sol = roof.modifiers.new("thick", "SOLIDIFY")
    sol.thickness = 0.15
    sol.offset = -1
    # Fascia band along the eaves: a thin vertical strip under each roof edge that hangs past the walls.
    bm = bmesh.new()
    for face in w["roof_faces"]:
        for (x1, y1, z1), (x2, y2, z2) in zip(face, face[1:] + face[:1]):
            if abs(z1 - z2) < 1e-4 and z1 <= w["base"] + w["wall_height"] + 1e-3 and not flat:
                a, b = (x1, y1, floor + z1), (x2, y2, floor + z2)
                bm.faces.new([bm.verts.new((a[0], a[1], a[2] - 0.2)), bm.verts.new((b[0], b[1], b[2] - 0.2)),
                              bm.verts.new(b), bm.verts.new(a)])
    if bm.faces:
        me = mesh_from_bmesh(name + "_fascia", bm)
        me.materials.append(materials.get("flat", color=colors["trim"], roughness=0.5))
        new_object(name + "_fascia", me, col)
    else:
        bm.free()
    return roof


def _openings(rec, wings, floor, col):
    glass = materials.get("glass")
    trim = materials.get("flat", color=rec["colors"]["trim"], roughness=0.5)
    door = materials.get("flat", color=rec["colors"]["door"], roughness=0.5)
    bm_glass, bm_trim, bm_door = bmesh.new(), bmesh.new(), bmesh.new()

    def panel(bm, a, d, out, w, h, base, depth):
        p0, p1 = a + d * (-w / 2), a + d * (w / 2)
        o = out * depth
        bm.faces.new([bm.verts.new((p0.x + o.x, p0.y + o.y, base)), bm.verts.new((p1.x + o.x, p1.y + o.y, base)),
                      bm.verts.new((p1.x + o.x, p1.y + o.y, base + h)), bm.verts.new((p0.x + o.x, p0.y + o.y, base + h))])

    for key, target in (("windows", "w"), ("doors", "d"), ("garage_doors", "g")):
        for o in rec.get(key, []):
            if o.get("wing", 0) >= len(wings):
                continue
            wing = wings[o.get("wing", 0)]
            pts = wing["footprint"]
            n = len(pts)
            i = o["edge"] % n
            a, b = Vector(pts[i]), Vector(pts[(i + 1) % n])
            d = (b - a).normalized()
            out = Vector((d.y, -d.x))  # CCW footprint → right-hand normal points outside
            L = (b - a).length
            total = (o["count"] - 1) * o["spacing"]
            base_z = floor + wing["base"]
            for k in range(o["count"]):
                center = a + d * (L * o["at"] - total / 2 + k * o["spacing"])
                w, h, sill = o["width"], o["height"], o["sill"]
                panel(bm_trim, center, d, out, w + 0.16, h + 0.16, base_z + sill - 0.08, 0.02)
                if target == "w":
                    panel(bm_glass, center, d, out, w, h, base_z + sill, 0.035)
                    panel(bm_trim, center, d, out, 0.04, h, base_z + sill, 0.04)
                    panel(bm_trim, center, d, out, w, 0.04, base_z + sill + h / 2 - 0.02, 0.04)
                elif target == "d":
                    panel(bm_door, center, d, out, w, h, base_z, 0.035)
                else:
                    panel(bm_trim, center, d, out, w, h, base_z, 0.035)
                    for row in range(1, 4):
                        panel(bm_door, center, d, out, w, 0.02, base_z + row * h / 4, 0.04)
    for bm, mat, nm in ((bm_glass, glass, "glass"), (bm_trim, trim, "trim"), (bm_door, door, "doors")):
        me = mesh_from_bmesh(f"{rec['id']}_{nm}", bm)
        me.materials.append(mat)
        new_object(f"{rec['id']}_{nm}", me, col)


@builder("deck")
def deck(rec, ctx):
    """Slab on posts at a height above grade, with railings (gaps where stairs land) and stairs."""
    col = ctx.col("Hardscape")
    poly = [Vector(p[:2]) for p in rec["polygon"]]
    n = len(poly)
    cx = sum(p.x for p in poly) / n
    cy = sum(p.y for p in poly) / n
    top = ctx.ground.z(cx, cy) + rec["height"]
    wood = materials.get(rec["material"], color=rec["color"]) if rec["material"] in ("decking", "composite_decking") \
        else materials.get(rec["material"])
    rail_mat = materials.get("flat", color=rec["railing"]["color"], roughness=0.6)
    # slab
    bm = bmesh.new()
    bm.faces.new([bm.verts.new((p.x, p.y, top)) for p in poly])
    me = mesh_from_bmesh(rec["id"], bm)
    me.materials.append(wood)
    slab = new_object(rec["id"], me, col)
    sol = slab.modifiers.new("thick", "SOLIDIFY")
    sol.thickness = rec["thickness"]
    sol.offset = -1

    parts = bmesh.new()

    def box(center, size, rot=0.0):
        res = bmesh.ops.create_cube(parts, size=1.0)
        R = Matrix.Rotation(rot, 3, "Z")
        for v in res["verts"]:
            v.co = R @ Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2])) + Vector(center)

    # stair openings per edge: (t0, t1) along the edge
    gaps = {}
    for st in rec["stairs"]:
        i = st["edge"] % n
        L = (poly[(i + 1) % n] - poly[i]).length
        c = st["at"] * L
        gaps.setdefault(i, []).append((c - st["width"] / 2, c + st["width"] / 2))
    rail = rec["railing"]
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        L = (b - a).length
        if L < 1e-3:
            continue
        d = (b - a) / L
        ang = math.atan2(d.y, d.x)
        inward = Vector((-d.y, d.x)) * 0.06
        if rec["posts"]:  # posts every ~2.4 m along each edge, down to grade
            k = max(1, int(math.ceil(L / 2.4)))
            for j in range(k):
                p = a + d * (L * j / k) + inward
                g = ctx.ground.z(p.x, p.y)
                box((p.x, p.y, (g - 0.2 + top - rec["thickness"]) / 2), (0.14, 0.14, top - rec["thickness"] - g + 0.2))
        if rail["style"] == "none" or i in rail["open_edges"]:
            continue
        spans, t = [], 0.0
        for g0, g1 in sorted(gaps.get(i, [])):
            spans.append((t, max(t, g0)))
            t = g1
        spans.append((t, L))
        for s0, s1 in spans:
            if s1 - s0 < 0.1:
                continue
            m = a + d * ((s0 + s1) / 2) + inward
            box((m.x, m.y, top + rail["height"]), (s1 - s0, 0.09, 0.06), ang)  # top rail
            if rail["style"] == "balusters":
                cnt = max(1, int((s1 - s0) / 0.13))
                for j in range(cnt):
                    p = a + d * (s0 + (j + 0.5) * (s1 - s0) / cnt) + inward
                    box((p.x, p.y, top + rail["height"] / 2), (0.035, 0.035, rail["height"]))
            elif rail["style"] == "horizontal":
                for r in (0.25, 0.5, 0.75):
                    box((m.x, m.y, top + rail["height"] * r), (s1 - s0, 0.03, 0.06), ang)
            elif rail["style"] == "glass":
                box((m.x, m.y, top + rail["height"] / 2), (s1 - s0, 0.012, rail["height"] * 0.85), ang)
            for p in (a + d * s0 + inward, a + d * s1 + inward):  # rail posts at span ends
                box((p.x, p.y, top + rail["height"] / 2), (0.09, 0.09, rail["height"] + 0.04))
    # stairs: steps run outward from the edge down to grade
    for st in rec["stairs"]:
        i = st["edge"] % n
        a, b = poly[i], poly[(i + 1) % n]
        L = (b - a).length
        d = (b - a) / L
        out = Vector((d.y, -d.x))
        c = a + d * (st["at"] * L)
        g = ctx.ground.z(c.x + out.x * 1.5, c.y + out.y * 1.5)
        steps = max(1, int(math.ceil((top - g) / st["rise"])))
        rise = (top - g) / steps
        for s in range(steps):
            h = top - rise * (s + 1)
            p = c + out * (st["run"] * (s + 0.5))
            box((p.x, p.y, (h + g) / 2 + rise * 0.0), (st["width"], st["run"], max(0.05, h - g + rise * 0.0 + 0.04)),
                math.atan2(d.y, d.x))
    me = mesh_from_bmesh(rec["id"] + "_parts", parts)
    me.materials.append(wood)
    new_object(rec["id"] + "_parts", me, col)
    return slab


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
