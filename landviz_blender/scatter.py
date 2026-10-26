"""Geometry Nodes scattering — leaves, flowers, grass.

Why not particle systems: a hidden particle emitter inside a collection instance also hides
its particles in Cycles, and every plant here is a collection instance. A GN modifier outputs
only the instances (the emitter mesh simply isn't in the output), so the shell is invisible
by construction while the leaves render. Bonus: proper rotation control.

Instance objects are modeled with local +Z as "outward" (leaf sprays) or "up" (grass, flowers).
"""
from __future__ import annotations

import math

import bpy


def _sock(node, name, kind, inputs=True):
    for s in (node.inputs if inputs else node.outputs):
        if s.name == name and s.type == kind and s.enabled:
            return s
    for s in (node.inputs if inputs else node.outputs):
        if s.name == name and s.type == kind:
            return s
    raise KeyError(f"{node.bl_idname}: no {kind} socket {name!r}")


class Layer:
    """One scatter layer: instances of `collection` on the source geometry.

    mode:  'surface' — random points on faces at `density` per m²
           'points'  — one instance per vertex
    align: 'normal'  — +Z along the surface normal, `tilt` radians of random wobble, random spin
           'up'      — +Z stays world-up, random spin only
    """

    def __init__(self, collection, density=10.0, mode="surface", align="normal", tilt=0.35,
                 scale=(0.75, 1.25), seed=0, selection_attr=None):
        self.collection = collection
        self.density = density
        self.mode = mode
        self.align = align
        self.tilt = tilt
        self.scale = scale
        self.seed = seed
        self.selection_attr = selection_attr


def scatter(obj, layers, keep_mesh=False, name="scatter"):
    ng = bpy.data.node_groups.new(f"{obj.name}_{name}", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    N = ng.nodes
    L = ng.links
    gin = N.new("NodeGroupInput")
    gout = N.new("NodeGroupOutput")
    join = N.new("GeometryNodeJoinGeometry")
    if keep_mesh:
        L.new(gin.outputs[0], join.inputs[0])
    for layer in layers:
        coll = N.new("GeometryNodeCollectionInfo")
        coll.inputs["Collection"].default_value = layer.collection
        coll.inputs["Separate Children"].default_value = True
        coll.inputs["Reset Children"].default_value = True
        coll.transform_space = "ORIGINAL"
        inst = N.new("GeometryNodeInstanceOnPoints")
        inst.inputs["Pick Instance"].default_value = True
        L.new(coll.outputs[0], inst.inputs["Instance"])

        if layer.mode == "surface":
            dist = N.new("GeometryNodeDistributePointsOnFaces")
            dist.distribute_method = "RANDOM"
            dist.inputs["Density"].default_value = layer.density
            dist.inputs["Seed"].default_value = layer.seed
            L.new(gin.outputs[0], dist.inputs["Mesh"])
            if layer.selection_attr:
                attr = N.new("GeometryNodeInputNamedAttribute")
                attr.data_type = "FLOAT"
                attr.inputs["Name"].default_value = layer.selection_attr
                cmp = N.new("FunctionNodeCompare")
                cmp.data_type = "FLOAT"
                cmp.operation = "GREATER_THAN"
                L.new(_sock(attr, "Attribute", "VALUE", inputs=False), cmp.inputs[0])
                cmp.inputs[1].default_value = 0.5
                L.new(cmp.outputs[0], dist.inputs["Selection"])
            L.new(dist.outputs["Points"], inst.inputs["Points"])
            base_rot = dist.outputs["Rotation"] if layer.align == "normal" else None
        else:
            L.new(gin.outputs[0], inst.inputs["Points"])
            base_rot = None

        # Random wobble/spin: Euler(±tilt, ±tilt, 0..2π)
        rnd = N.new("FunctionNodeRandomValue")
        rnd.data_type = "FLOAT_VECTOR"
        t = layer.tilt if layer.align == "normal" else 0.0
        _sock(rnd, "Min", "VECTOR").default_value = (-t, -t, 0.0)
        _sock(rnd, "Max", "VECTOR").default_value = (t, t, 2 * math.pi)
        _sock(rnd, "Seed", "INT").default_value = layer.seed + 7
        e2r = N.new("FunctionNodeEulerToRotation")
        L.new(_sock(rnd, "Value", "VECTOR", inputs=False), e2r.inputs[0])
        if base_rot is not None:
            rot = N.new("FunctionNodeRotateRotation")
            rot.rotation_space = "LOCAL"
            L.new(base_rot, rot.inputs["Rotation"])
            L.new(e2r.outputs[0], rot.inputs["Rotate By"])
            L.new(rot.outputs[0], inst.inputs["Rotation"])
        else:
            L.new(e2r.outputs[0], inst.inputs["Rotation"])

        sc = N.new("FunctionNodeRandomValue")
        sc.data_type = "FLOAT"
        _sock(sc, "Min", "VALUE").default_value = layer.scale[0]
        _sock(sc, "Max", "VALUE").default_value = layer.scale[1]
        _sock(sc, "Seed", "INT").default_value = layer.seed + 13
        L.new(_sock(sc, "Value", "VALUE", inputs=False), inst.inputs["Scale"])
        L.new(inst.outputs[0], join.inputs[0])
    L.new(join.outputs[0], gout.inputs[0])
    mod = obj.modifiers.new(name, "NODES")
    mod.node_group = ng
    return mod
