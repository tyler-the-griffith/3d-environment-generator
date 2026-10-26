"""Procedural material library (Cycles). Materials are registered by name and built on demand.

Ground-type materials map by world position in meters (consistent scale everywhere, no UVs
needed). Vertical materials (walls, siding) use the UV map, which builders lay out in meters.

To use a downloaded PBR texture set instead (e.g. from Poly Haven, CC0), put the maps in
<project>/assets/textures/<name>/ (files containing diff/color, rough, nor/normal, disp) and
reference the material as "tex:<name>" — see image_pbr().
"""
from __future__ import annotations

from pathlib import Path

import bpy

from .util import hex_rgba

REGISTRY = {}
ASSET_DIRS: list[Path] = []


def register(*names):
    def deco(fn):
        for n in names:
            REGISTRY[n] = fn
        return fn
    return deco


def get(name: str, **params):
    """Get or build a material. params become part of the cache key (e.g. color, scale)."""
    key = name + ("|" + "|".join(f"{k}={v}" for k, v in sorted(params.items())) if params else "")
    mat = bpy.data.materials.get(key)
    if mat:
        return mat
    mat = bpy.data.materials.new(key)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    if name.startswith("tex:"):
        image_pbr(nt, name[4:], **params)
    else:
        builder = REGISTRY.get(name)
        if builder is None:
            print(f"landviz: unknown material {name!r}, using neutral gray")
            builder = REGISTRY["neutral"]
        builder(nt, **params)
    return mat


# ---------------------------------------------------------------- node helpers


class B:
    """Tiny node-graph builder: b.node('ShaderNodeTexNoise', Scale=4) and b.link(a, 'Fac', c, 0)."""

    def __init__(self, nt):
        self.nt = nt
        self.x = 0

    def node(self, kind, **inputs):
        n = self.nt.nodes.new(kind)
        n.location = (self.x, 0)
        self.x += 200
        for k, v in inputs.items():
            if k.startswith("_"):  # node properties, e.g. _blend_type='MULTIPLY'
                setattr(n, k[1:], v)
            else:
                n.inputs[k].default_value = v
        return n

    def link(self, a, out, b, inp):
        if out == 0 and a.bl_idname == "ShaderNodeMix":  # RGBA mix: the color result is output 2
            out = 2
        self.nt.links.new(a.outputs[out], b.inputs[inp])

    def output(self, shader, displacement=None):
        o = self.node("ShaderNodeOutputMaterial")
        self.link(shader, 0, o, "Surface")
        if displacement is not None:
            self.link(displacement, 0, o, "Displacement")
        return o

    def principled(self, color=None, roughness=0.6, **kw):
        p = self.node("ShaderNodeBsdfPrincipled", Roughness=roughness, **kw)
        if color is not None:
            if isinstance(color, tuple):
                p.inputs["Base Color"].default_value = color
            else:
                self.link(color, 0, p, "Base Color")
        return p

    def world_coords(self, scale=1.0):
        g = self.node("ShaderNodeNewGeometry")
        m = self.node("ShaderNodeMapping")
        m.inputs["Scale"].default_value = (scale, scale, scale)
        self.link(g, "Position", m, "Vector")
        return m

    def uv_coords(self, scale=1.0):
        t = self.node("ShaderNodeTexCoord")
        m = self.node("ShaderNodeMapping")
        m.inputs["Scale"].default_value = (scale, scale, scale)
        self.link(t, "UV", m, "Vector")
        return m

    def ramp(self, fac_node, fac_out, stops):
        r = self.node("ShaderNodeValToRGB")
        el = r.color_ramp.elements
        while len(el) > len(stops):
            el.remove(el[-1])
        while len(el) < len(stops):
            el.new(0.5)
        for e, (pos, col) in zip(el, stops):
            e.position = pos
            e.color = hex_rgba(col) if isinstance(col, str) else col
        self.link(fac_node, fac_out, r, "Fac")
        return r

    def bump(self, height_node, out=0, strength=0.3, distance=0.02):
        b = self.node("ShaderNodeBump", Strength=strength, Distance=distance)
        self.link(height_node, out, b, "Height")
        return b

    def noise(self, vec, scale=5.0, detail=6.0, roughness=0.55):
        n = self.node("ShaderNodeTexNoise", Scale=scale, Detail=detail, Roughness=roughness)
        self.link(vec, 0, n, "Vector")
        return n

    def voronoi(self, vec, scale=5.0, feature="F1", randomness=1.0):
        v = self.node("ShaderNodeTexVoronoi", Scale=scale, Randomness=randomness, _feature=feature)
        self.link(vec, 0, v, "Vector")
        return v

    def mix(self, a, b, fac, blend="MIX"):
        m = self.node("ShaderNodeMix", _data_type="RGBA", _blend_type=blend)
        for src, idx in ((fac, 0), (a, 6), (b, 7)):
            if isinstance(src, tuple) and not hasattr(src[0], "outputs"):
                m.inputs[idx].default_value = src
            elif isinstance(src, (int, float)):
                m.inputs[idx].default_value = src
            else:
                node, out = src
                self.link(node, out, m, idx)
        return m


# ---------------------------------------------------------------- generic


@register("neutral")
def neutral(nt, color="#9a9a9a"):
    b = B(nt)
    b.output(b.principled(hex_rgba(color), 0.7))


@register("flat")
def flat(nt, color="#9a9a9a", roughness=0.6, metallic=0.0):
    b = B(nt)
    b.output(b.principled(hex_rgba(color), roughness, Metallic=metallic))


# ---------------------------------------------------------------- ground


@register("lawn_ground", "lawn")
def lawn_ground(nt, color="#4f7a2c", health=0.9):
    """Lawn seen from afar / under the grass particles: mottled greens, a little thatch."""
    b = B(nt)
    vec = b.world_coords()
    big = b.noise(vec, scale=0.08, detail=4)
    small = b.noise(vec, scale=3.0, detail=8)
    base = hex_rgba(color)
    dark = tuple(c * 0.6 for c in base[:3]) + (1,)
    light = tuple(min(1, c * 1.25) for c in base[:3]) + (1,)
    dry = hex_rgba("#8a8a4a")
    r1 = b.ramp(big, "Fac", [(0.35, dark), (0.65, light)])
    patch = b.ramp(small, "Fac", [(0.4, (0, 0, 0, 1)), (1.0, (1, 1, 1, 1))])
    m = b.mix((r1, 0), dry, (patch, 0)) if health < 0.999 else r1
    if health < 0.999:
        m.inputs[0].default_value = 0
        mul = b.node("ShaderNodeMath", _operation="MULTIPLY", Value=0.0)
        mul.inputs[1].default_value = (1 - health) * 1.5
        b.link(patch, 0, mul, 0)
        b.link(mul, 0, m, 0)
    bump = b.bump(small, "Fac", 0.25, 0.01)
    p = b.principled(m, 0.85)
    b.link(bump, 0, p, "Normal")
    b.output(p)


@register("grass_blade")
def grass_blade(nt, color="#4f7a2c", health=0.9):
    """Translucent blades with per-instance color variation (Object Info Random)."""
    b = B(nt)
    info = b.node("ShaderNodeObjectInfo")
    base = hex_rgba(color)
    tips = hex_rgba("#a8a860") if health < 0.95 else tuple(min(1, c * 1.35) for c in base[:3]) + (1,)
    dark = tuple(c * 0.7 for c in base[:3]) + (1,)
    col = b.ramp(info, "Random", [(0.0, dark), (0.75, base), (1.0, tips)])
    p = b.principled(col, 0.55, **{"Specular IOR Level": 0.35})
    tr = b.node("ShaderNodeBsdfTranslucent")
    b.link(col, 0, tr, "Color")
    mixs = b.node("ShaderNodeMixShader", Fac=0.3)
    b.link(p, 0, mixs, 1)
    b.link(tr, 0, mixs, 2)
    b.output(mixs)


def _granular(nt, colors, scale, roughness=0.9, bump=0.6, cell_scale=None):
    """Mulch / gravel / rock beds: voronoi cells with per-cell color + noise."""
    b = B(nt)
    vec = b.world_coords()
    vor = b.voronoi(vec, scale=cell_scale or scale)
    n = b.noise(vec, scale=scale * 0.7, detail=6)
    col_ramp = b.ramp(vor, "Color", [(i / max(1, len(colors) - 1), c) for i, c in enumerate(colors)])
    # Voronoi "Color" is RGB; take its value via ramp on Fac-like input by separating.
    sep = b.node("ShaderNodeSeparateColor")
    b.link(vor, "Color", sep, "Color")
    b.link(sep, "Red", col_ramp, "Fac")
    m = b.mix((col_ramp, 0), (0.0, 0.0, 0.0, 1.0), 0.0)
    m.inputs[0].default_value = 0.15
    b.link(n, "Fac", m, 0)
    m.blend_type = "MULTIPLY"
    m.inputs[7].default_value = (0.6, 0.6, 0.6, 1)
    bp = b.bump(vor, "Distance", bump, 0.02)
    p = b.principled(m, roughness)
    b.link(bp, 0, p, "Normal")
    b.output(p)


@register("mulch_dark")
def mulch_dark(nt):
    _granular(nt, ["#21160f", "#3a271a", "#4a3222", "#2a1b12"], 18, bump=0.8)


@register("mulch")
def mulch(nt):
    _granular(nt, ["#4a3020", "#6b4a32", "#7a5a3c", "#5a3a26"], 18, bump=0.8)


@register("mulch_red")
def mulch_red(nt):
    _granular(nt, ["#5a2418", "#7a3424", "#6a2c1e", "#8a4430"], 18, bump=0.8)


@register("pine_straw")
def pine_straw(nt):
    _granular(nt, ["#6a4220", "#8a5a2a", "#a0703a", "#7a4a24"], 40, bump=0.4)


@register("soil")
def soil(nt):
    _granular(nt, ["#2e2218", "#3e2e20", "#4a3828"], 30, bump=0.3)


@register("river_rock")
def river_rock(nt):
    _granular(nt, ["#6a645c", "#9a948a", "#b8b0a2", "#7d7468", "#a89a88"], 9, roughness=0.5, bump=1.0)


@register("gravel", "pea_gravel")
def gravel(nt):
    _granular(nt, ["#a89c88", "#c4b9a5", "#8a8070", "#d0c6b2", "#b0a48e"], 40, roughness=0.8, bump=0.7)


@register("lava_rock")
def lava_rock(nt):
    _granular(nt, ["#4a2418", "#6a3424", "#3a1c14"], 14, roughness=0.95, bump=1.0)


# ---------------------------------------------------------------- paving


@register("concrete")
def concrete(nt, color="#cfcac0", scale=1.0, joint=1.5, **_):
    b = B(nt)
    vec = b.world_coords(scale)
    n = b.noise(vec, scale=1.5, detail=8)
    fine = b.noise(vec, scale=60, detail=4)
    c = hex_rgba(color)
    col = b.ramp(n, "Fac", [(0.3, tuple(x * 0.85 for x in c[:3]) + (1,)), (0.7, c)])
    # Control joints every `joint` meters.
    brick = b.node("ShaderNodeTexBrick", Scale=1.0 / joint, **{"Mortar Size": 0.004, "Brick Width": 1.0,
                                                                "Row Height": 1.0})
    brick.offset = 0.0
    b.link(vec, 0, brick, "Vector")
    brick.inputs["Color1"].default_value = (1, 1, 1, 1)
    brick.inputs["Color2"].default_value = (1, 1, 1, 1)
    brick.inputs["Mortar"].default_value = (0.4, 0.4, 0.4, 1)
    m = b.mix((col, 0), (brick, "Color"), 1.0, blend="MULTIPLY")
    bp = b.bump(fine, "Fac", 0.15, 0.005)
    p = b.principled(m, 0.8)
    b.link(bp, 0, p, "Normal")
    b.output(p)


@register("stamped_concrete")
def stamped(nt, scale=1.0, **_):
    flagstone(nt, colors=["#a8957a", "#bba98f", "#9a8670"], joint_color="#6a5a4a", scale=scale * 1.2)


@register("exposed_aggregate")
def exposed_aggregate(nt, scale=1.0, **_):
    _granular(nt, ["#a89a86", "#c0b4a0", "#8a7e70", "#d6ccb8"], 90 * scale, roughness=0.7, bump=0.4)


def _bricks(nt, c1, c2, mortar, w, h, scale=1.0, uv=False, offset=0.5, bump=0.4, rough=0.75):
    b = B(nt)
    vec = b.uv_coords(scale) if uv else b.world_coords(scale)
    n = b.noise(vec, scale=8, detail=6)
    brick = b.node("ShaderNodeTexBrick", Scale=1.0, **{"Mortar Size": 0.008, "Brick Width": w, "Row Height": h,
                                                        "Mortar Smooth": 0.2, "Bias": 0.0})
    brick.offset = offset
    b.link(vec, 0, brick, "Vector")
    brick.inputs["Color1"].default_value = hex_rgba(c1)
    brick.inputs["Color2"].default_value = hex_rgba(c2)
    brick.inputs["Mortar"].default_value = hex_rgba(mortar)
    m = b.mix((brick, "Color"), (n, "Color"), 0.12, blend="OVERLAY")
    bp = b.bump(brick, "Fac", bump, 0.01)
    bp.invert = True
    p = b.principled(m, rough)
    b.link(bp, 0, p, "Normal")
    b.output(p)


@register("pavers")
def pavers(nt, scale=1.0, joint_color=None, **_):
    _bricks(nt, "#a88a72", "#8e7260", joint_color or "#6e6458", 0.30, 0.15, scale)


@register("pavers_gray")
def pavers_gray(nt, scale=1.0, joint_color=None, **_):
    _bricks(nt, "#9a9690", "#7e7a74", joint_color or "#5a5650", 0.40, 0.20, scale)


@register("brick")
def brick(nt, scale=1.0, joint_color=None, uv=False, **_):
    _bricks(nt, "#9c4f3a", "#7c3a2a", joint_color or "#b0a898", 0.20, 0.10, scale, uv=uv)


@register("flagstone", "stepping_stone")
def flagstone(nt, colors=None, joint_color=None, scale=1.0, **_):
    b = B(nt)
    vec = b.world_coords(scale)
    vor = b.voronoi(vec, scale=1.4, feature="DISTANCE_TO_EDGE")
    vcol = b.voronoi(vec, scale=1.4)
    n = b.noise(vec, scale=6, detail=8)
    colors = colors or ["#8f8574", "#a69a84", "#7f7668", "#b2a68e", "#968a7a"]
    sep = b.node("ShaderNodeSeparateColor")
    b.link(vcol, "Color", sep, "Color")
    stone = b.ramp(sep, "Red", [(i / (len(colors) - 1), c) for i, c in enumerate(colors)])
    joint = b.ramp(vor, "Distance", [(0.0, (0, 0, 0, 1)), (0.04, (1, 1, 1, 1))])
    m = b.mix(joint_color and hex_rgba(joint_color) or hex_rgba("#5a5248"), (stone, 0), (joint, 0))
    m2 = b.mix((m, 2), (n, "Color"), 0.1, blend="OVERLAY")
    bp = b.bump(joint, 0, 0.6, 0.02)
    p = b.principled(m2, 0.8)
    b.link(bp, 0, p, "Normal")
    b.output(p)


@register("bluestone")
def bluestone(nt, scale=1.0, **_):
    _bricks(nt, "#7f8a90", "#6c767c", "#55595c", 0.6, 0.45, scale, offset=0.33, bump=0.2)


@register("asphalt")
def asphalt(nt, **_):
    _granular(nt, ["#3a3a3a", "#4a4a4a", "#2e2e2e", "#565656"], 120, roughness=0.9, bump=0.3)


@register("decking")
def decking(nt, color="#8a6446", scale=1.0, **_):
    b = B(nt)
    vec = b.world_coords(scale)
    brick = b.node("ShaderNodeTexBrick", Scale=1.0, **{"Mortar Size": 0.006, "Brick Width": 2.4,
                                                        "Row Height": 0.14})
    brick.offset = 0.37
    b.link(vec, 0, brick, "Vector")
    c = hex_rgba(color)
    brick.inputs["Color1"].default_value = c
    brick.inputs["Color2"].default_value = tuple(x * 0.8 for x in c[:3]) + (1,)
    brick.inputs["Mortar"].default_value = (0.02, 0.015, 0.01, 1)
    wave = b.node("ShaderNodeTexWave", Scale=3, Distortion=6, Detail=4, _wave_type="BANDS")
    b.link(vec, 0, wave, "Vector")
    m = b.mix((brick, "Color"), (wave, "Color"), 0.15, blend="OVERLAY")
    bp = b.bump(brick, "Fac", 0.5, 0.01)
    bp.invert = True
    p = b.principled(m, 0.6)
    b.link(bp, 0, p, "Normal")
    b.output(p)


@register("composite_decking")
def composite_decking(nt, scale=1.0, **_):
    decking(nt, color="#6f6258", scale=scale)


# ---------------------------------------------------------------- walls / buildings (UV in meters)


@register("block")
def block(nt, uv=True, **_):
    _bricks(nt, "#a49a8a", "#958b7b", "#7a7266", 0.45, 0.20, uv=uv, bump=0.5, rough=0.85)


@register("stone", "fieldstone")
def stone_wall(nt, uv=True, **_):
    b = B(nt)
    vec = b.uv_coords() if uv else b.world_coords()
    sq = b.node("ShaderNodeMapping")
    sq.inputs["Scale"].default_value = (1.0, 1.8, 1.0)
    b.link(vec, 0, sq, "Vector")
    vor = b.voronoi(sq, scale=3.2, feature="DISTANCE_TO_EDGE")
    vcol = b.voronoi(sq, scale=3.2)
    sep = b.node("ShaderNodeSeparateColor")
    b.link(vcol, "Color", sep, "Color")
    stone = b.ramp(sep, "Red", [(0, "#7a7062"), (0.33, "#9a8f7e"), (0.66, "#8a7f70"), (1, "#a89c88")])
    joint = b.ramp(vor, "Distance", [(0.0, (0, 0, 0, 1)), (0.06, (1, 1, 1, 1))])
    m = b.mix(hex_rgba("#4a443c"), (stone, 0), (joint, 0))
    bp = b.bump(joint, 0, 0.9, 0.04)
    p = b.principled(m, 0.9)
    b.link(bp, 0, p, "Normal")
    b.output(p)


@register("timber")
def timber(nt, uv=True, **_):
    _bricks(nt, "#6a4f38", "#5a4230", "#2a2018", 3.0, 0.15, uv=uv, offset=0.0, bump=0.4)


@register("gabion")
def gabion(nt, uv=True, **_):
    river_rock(nt)


@register("siding")
def siding(nt, color="#d9d4c7", **_):
    """Lap siding: ~7in exposure courses (UV meters, v = height)."""
    c = color.lstrip("#")
    dark = "#%02x%02x%02x" % tuple(int(int(c[i:i + 2], 16) * 0.55) for i in (0, 2, 4))
    alt = "#%02x%02x%02x" % tuple(int(int(c[i:i + 2], 16) * 0.96) for i in (0, 2, 4))
    _bricks(nt, color, alt, dark, 12.0, 0.178, uv=True, offset=0.0, bump=0.5, rough=0.55)


@register("shingles")
def shingles(nt, color="#3d3b3a", **_):
    b = B(nt)
    vec = b.uv_coords()
    brick = b.node("ShaderNodeTexBrick", Scale=1.0, **{"Mortar Size": 0.004, "Brick Width": 0.33,
                                                        "Row Height": 0.14})
    brick.offset = 0.5
    b.link(vec, 0, brick, "Vector")
    c = hex_rgba(color)
    brick.inputs["Color1"].default_value = c
    brick.inputs["Color2"].default_value = tuple(x * 0.75 for x in c[:3]) + (1,)
    brick.inputs["Mortar"].default_value = tuple(x * 0.4 for x in c[:3]) + (1,)
    n = b.noise(vec, scale=40, detail=6)
    m = b.mix((brick, "Color"), (n, "Color"), 0.15, blend="OVERLAY")
    bp = b.bump(brick, "Fac", 0.5, 0.01)
    bp.invert = True
    p = b.principled(m, 0.85)
    b.link(bp, 0, p, "Normal")
    b.output(p)


@register("glass")
def glass(nt, **_):
    b = B(nt)
    b.output(b.principled(hex_rgba("#1c2228"), 0.05, **{"Specular IOR Level": 0.8}))


# ---------------------------------------------------------------- vegetation


@register("leaf")
def leaf(nt, color="#4a6b2f", translucency=0.35, variation=0.25):
    """Foliage: per-instance hue/value variation, translucent backlighting, slight sheen."""
    b = B(nt)
    info = b.node("ShaderNodeObjectInfo")
    base = hex_rgba(color)
    lo = tuple(c * (1 - variation) for c in base[:3]) + (1,)
    hi = tuple(min(1, c * (1 + variation * 1.2)) for c in base[:3]) + (1,)
    yel = tuple(min(1, c * f) for c, f in zip(base[:3], (1.25, 1.2, 0.8))) + (1,)
    col = b.ramp(info, "Random", [(0, lo), (0.5, base), (0.85, hi), (1, yel)])
    p = b.principled(col, 0.5, **{"Specular IOR Level": 0.4})
    tr = b.node("ShaderNodeBsdfTranslucent")
    b.link(col, 0, tr, "Color")
    ms = b.node("ShaderNodeMixShader", Fac=translucency)
    b.link(p, 0, ms, 1)
    b.link(tr, 0, ms, 2)
    b.output(ms)


@register("blade_vcol")
def blade_vcol(nt, color="#4a6b2f", **_):
    """Grass-clump blades: color from the per-blade 'tint' color attribute × species color."""
    b = B(nt)
    attr = b.node("ShaderNodeVertexColor", _layer_name="tint")
    m = b.mix(hex_rgba(color), (attr, "Color"), 1.0, blend="MULTIPLY")
    p = b.principled(m, 0.55)
    tr = b.node("ShaderNodeBsdfTranslucent")
    b.link(m, 2, tr, "Color")
    ms = b.node("ShaderNodeMixShader", Fac=0.3)
    b.link(p, 0, ms, 1)
    b.link(tr, 0, ms, 2)
    b.output(ms)


@register("bark")
def bark(nt, color="#5a4a3c", **_):
    b = B(nt)
    vec = b.world_coords()
    sq = b.node("ShaderNodeMapping")
    sq.inputs["Scale"].default_value = (6, 6, 1.2)
    b.link(vec, 0, sq, "Vector")
    n = b.noise(sq, scale=4, detail=10, roughness=0.7)
    c = hex_rgba(color)
    col = b.ramp(n, "Fac", [(0.35, tuple(x * 0.6 for x in c[:3]) + (1,)), (0.65, c)])
    bp = b.bump(n, "Fac", 0.8, 0.02)
    p = b.principled(col, 0.9)
    b.link(bp, 0, p, "Normal")
    b.output(p)


@register("petal")
def petal(nt, color="#ffffff", **_):
    b = B(nt)
    info = b.node("ShaderNodeObjectInfo")
    base = hex_rgba(color)
    col = b.ramp(info, "Random", [(0, tuple(c * 0.85 for c in base[:3]) + (1,)), (1, base)])
    p = b.principled(col, 0.45, **{"Subsurface Weight": 0.15})
    tr = b.node("ShaderNodeBsdfTranslucent")
    b.link(col, 0, tr, "Color")
    ms = b.node("ShaderNodeMixShader", Fac=0.25)
    b.link(p, 0, ms, 1)
    b.link(tr, 0, ms, 2)
    b.output(ms)


@register("rock")
def rock(nt, color="#8a857c", **_):
    b = B(nt)
    vec = b.node("ShaderNodeTexCoord")
    n = b.noise(vec, scale=3, detail=10, roughness=0.65)
    b.link(vec, "Object", n, "Vector")
    v = b.voronoi(vec, scale=8)
    b.link(vec, "Object", v, "Vector")
    c = hex_rgba(color)
    col = b.ramp(n, "Fac", [(0.3, tuple(x * 0.65 for x in c[:3]) + (1,)), (0.55, c),
                            (0.8, tuple(min(1, x * 1.2) for x in c[:3]) + (1,))])
    bp = b.bump(n, "Fac", 0.7, 0.05)
    p = b.principled(col, 0.85)
    b.link(bp, 0, p, "Normal")
    b.output(p)


@register("emission")
def emission(nt, color="#ffd9a0", strength=10.0, **_):
    b = B(nt)
    e = b.node("ShaderNodeEmission", Strength=strength)
    e.inputs["Color"].default_value = hex_rgba(color)
    b.output(e)


# ---------------------------------------------------------------- image-based PBR


def image_pbr(nt, name, scale=1.0, **_):
    """Image texture set from <assets>/textures/<name>/, mapped in world meters (1 tile = 1 m × scale)."""
    folder = None
    for root in ASSET_DIRS:
        cand = root / "textures" / name
        if cand.is_dir():
            folder = cand
            break
    b = B(nt)
    if folder is None:
        print(f"landviz: texture set {name!r} not found in {[str(a) for a in ASSET_DIRS]}")
        b.output(b.principled(hex_rgba("#ff00ff"), 0.5))
        return
    files = {f.name.lower(): f for f in folder.iterdir() if f.is_file()}

    def find(*keys):
        for k in keys:
            for fname, f in files.items():
                if k in fname:
                    return f
        return None

    vec = b.world_coords(1.0 / scale)
    p = b.principled(None, 0.6)
    for keys, inp, noncolor in ((("diff", "color", "albedo", "basecolor"), "Base Color", False),
                                (("rough",), "Roughness", True)):
        f = find(*keys)
        if f:
            tex = b.node("ShaderNodeTexImage")
            tex.image = bpy.data.images.load(str(f), check_existing=True)
            if noncolor:
                tex.image.colorspace_settings.name = "Non-Color"
            b.link(vec, 0, tex, "Vector")
            b.link(tex, "Color", p, inp)
    f = find("nor_gl", "normal", "nor")
    if f:
        tex = b.node("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(str(f), check_existing=True)
        tex.image.colorspace_settings.name = "Non-Color"
        b.link(vec, 0, tex, "Vector")
        nm = b.node("ShaderNodeNormalMap")
        b.link(tex, "Color", nm, "Color")
        b.link(nm, 0, p, "Normal")
    b.output(p)

