"""Boulders and landscape lights."""
from __future__ import annotations

import math
import random

import bmesh
import bpy

from .. import materials
from ..util import hex_rgba, mesh_from_bmesh, new_object
from . import builder


@builder("boulder")
def boulder(rec, ctx):
    rng = random.Random(rec["seed"])
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=4, radius=0.5)
    sx, sy, sz = rng.uniform(0.9, 1.2), rng.uniform(0.7, 1.0), rng.uniform(0.5, 0.75)
    # Flatten a few facets (fractured rock look) then scale.
    planes = [(rng.uniform(0, 2 * math.pi), rng.uniform(-0.3, 0.9), rng.uniform(0.3, 0.42)) for _ in range(7)]
    for v in bm.verts:
        for az, el, d in planes:
            n = (math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el))
            dot = v.co.x * n[0] + v.co.y * n[1] + v.co.z * n[2]
            if dot > d:
                v.co.x -= n[0] * (dot - d)
                v.co.y -= n[1] * (dot - d)
                v.co.z -= n[2] * (dot - d)
        v.co.x *= sx * rec["size"]
        v.co.y *= sy * rec["size"]
        v.co.z *= sz * rec["size"]
    me = mesh_from_bmesh(rec["id"], bm)
    me.shade_smooth()
    me.materials.append(materials.get("rock", color=rec["color"]))
    obj = new_object(rec["id"], me, ctx.col("Hardscape"))
    tex = bpy.data.textures.new(rec["id"] + "_n", "VORONOI")
    tex.noise_scale = 0.35 * rec["size"]
    d = obj.modifiers.new("rough", "DISPLACE")
    d.texture = tex
    d.strength = 0.06 * rec["size"]
    z = ctx.ground.z(rec["x"], rec["y"])
    obj.location = (rec["x"], rec["y"], z + sz * rec["size"] * 0.5 * (1 - 2 * rec["bury"]))
    obj.rotation_euler = (0, 0, rng.uniform(0, 2 * math.pi))
    return obj


@builder("light")
def light(rec, ctx):
    col = ctx.col("Lighting")
    z = ctx.ground.z(rec["x"], rec["y"])
    data = bpy.data.lights.new(rec["id"], "SPOT" if rec["type"] == "uplight" else "POINT")
    data.energy = rec["power"] * 25
    data.color = hex_rgba(rec["color"])[:3]
    data.shadow_soft_size = 0.05
    obj = bpy.data.objects.new(rec["id"], data)
    col.objects.link(obj)
    if rec["type"] == "uplight":
        obj.location = (rec["x"], rec["y"], z + 0.1)
        data.spot_size = math.radians(50)
        obj.rotation_euler = (0, 0, 0)  # spot lights point down -Z; flip to point up
        obj.rotation_euler.x = math.pi
    else:
        obj.location = (rec["x"], rec["y"], z + 0.45)
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=0.08, radius2=0.03, depth=0.06)
        me = mesh_from_bmesh(rec["id"] + "_fixture", bm)
        me.materials.append(materials.get("flat", color="#222222", roughness=0.4, metallic=0.7))
        fx = new_object(rec["id"] + "_fixture", me, col)
        fx.location = (rec["x"], rec["y"], z + 0.5)
    return obj
