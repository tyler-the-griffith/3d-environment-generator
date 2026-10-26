"""Procedural plant generator.

One *prototype* collection is built per (species, variant) at a nominal size; every plant in
the scene is a collection instance of a prototype scaled to its own height/spread. That keeps
memory flat no matter how many plants the plan calls for (hundreds of perennials cost about
the same as one).

Forms are registered in FORMS: a form maps to a builder function that receives a PlantSpec
and returns nothing (it populates the prototype collection). Woody plants share one pipeline:
  envelope (crown shape profile)  →  lobes (cloud-like clumps filling the envelope)
  →  skeleton (trunk + branches feeding each lobe)  →  foliage particles on lobe surfaces
  →  flower particles.
Grasses and spiky perennials build blade/stem geometry directly.

If a species has an `asset`, that model is imported instead (see assets.py).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

import bmesh
import bpy
from mathutils import Vector

from .. import materials
from ..scatter import Layer, scatter
from ..util import collection, mesh_from_bmesh, new_object

FORMS = {}

LEAF_SIZE_M = {"tiny": 0.022, "small": 0.04, "medium": 0.07, "large": 0.12, "needle": 0.05, "blade": 0.06}


def form(*names):
    def deco(fn):
        for n in names:
            FORMS[n] = fn
        return fn
    return deco


@dataclass
class PlantSpec:
    species_id: str
    form: str
    height: float
    spread: float
    leaf_size: str
    foliage: str
    bark: str
    leaf_density: float  # 0 (bare) .. 1
    flower_color: str | None
    flower_density: float
    sheared: bool
    generator: dict
    seed: int
    quality_mult: float  # overall foliage particle multiplier from the render quality preset
    category: str
    impostor: bool = False  # web/real-time: solid canopy shells instead of scattered leaves

    def g(self, key, default):
        return self.generator.get(key, default)


# ---------------------------------------------------------------- envelopes (normalized: height 1, spread 1)


def envelope(form_name: str):
    """(z_min, z_max, radius(t)) for t∈[0,1] along the crown. Radius is a fraction of spread."""
    def ell(t):
        return 0.5 * math.sqrt(max(0.0, 1 - (2 * t - 1) ** 2))
    table = {
        "tree_round":       (0.35, 1.0, ell),
        "tree_oval":        (0.28, 1.0, lambda t: 0.5 * math.sqrt(max(0, 1 - (2 * t - 1) ** 2)) * (0.85 + 0.15 * t)),
        "tree_multistem":   (0.30, 1.0, ell),
        "tree_vase":        (0.35, 1.0, lambda t: 0.5 * min(1, 0.45 + t) * math.sqrt(max(0, 1 - max(0, 2 * t - 1) ** 2))),
        "tree_spreading":   (0.28, 0.95, lambda t: 0.5 * math.sqrt(max(0, 1 - (1.6 * t - 0.75) ** 2)) if t > 0.0 else 0.2),
        "tree_weeping":     (0.15, 1.0, lambda t: 0.5 * (0.55 + 0.45 * math.sin(min(1, t * 1.2) * math.pi / 2)) * math.sqrt(max(0, 1 - max(0, 3 * t - 2) ** 2))),
        "tree_columnar":    (0.15, 1.0, lambda t: 0.5 * math.sqrt(max(0, 1 - (2 * t - 1) ** 4))),
        "conifer_conical":  (0.04, 1.0, lambda t: 0.5 * (1 - t) ** 0.95),
        "conifer_columnar": (0.03, 1.0, lambda t: 0.5 * min(1, 1.3 * (1 - t) ** 0.6) * min(1, 0.7 + t * 3)),
        "shrub_round":      (0.02, 1.0, lambda t: 0.5 * math.sqrt(max(0, 1 - (2 * t - 1) ** 2)) * 0.9 + 0.05),
        "shrub_upright":    (0.05, 1.0, lambda t: 0.5 * (0.45 + 0.55 * math.sin(min(1, t * 1.4) * math.pi / 2)) * math.sqrt(max(0, 1 - max(0, 2.4 * t - 1.4) ** 2))),
        "shrub_spreading":  (0.02, 1.0, lambda t: 0.5 * math.sqrt(max(0, 1 - t ** 2))),
        "shrub_mound":      (0.0, 1.0, lambda t: 0.5 * math.sqrt(max(0, 1 - t ** 2))),
        "hedge":            (0.0, 1.0, lambda t: 0.5 * (1 - 0.15 * t)),
        "perennial_mound":  (0.0, 1.0, lambda t: 0.5 * math.sqrt(max(0, 1 - t ** 2))),
        "groundcover":      (0.0, 1.0, lambda t: 0.5 * math.sqrt(max(0, 1 - t ** 1.5))),
    }
    return table.get(form_name, table["shrub_round"])


def sample_lobes(spec: PlantSpec, rng: random.Random):
    """Fill the crown envelope with overlapping spherical lobes (meters, plant at origin)."""
    z0, z1, rad = envelope(spec.form)
    H, S = spec.height, spec.spread
    crown_h = (z1 - z0) * H
    woody_tree = spec.form.startswith(("tree", "conifer"))
    # Lobe size: trees get ~1/4-spread clumps; small plants a few clumps; sheared = many tiny for a smooth skin.
    conifer = spec.form.startswith("conifer")
    lobe_r = max(0.08, min(S, crown_h) * (0.13 if spec.sheared else (0.2 if conifer else (0.24 if woody_tree else 0.3))))
    lobe_r *= spec.g("lobe_scale", 1.0)
    vol = sum(math.pi * (rad((i + 0.5) / 20) * S) ** 2 * crown_h / 20 for i in range(20))
    # Heavy overlap (≈4x volume) so the crown reads as one mass, not pom-poms; at least
    # enough lobes to stack the full crown height twice over.
    n = int(min(160, max(4, 4.0 * vol / (4 / 3 * math.pi * lobe_r ** 3), 2 * crown_h / lobe_r)))
    lobes, tries = [], 0
    while len(lobes) < n and tries < n * 60:
        tries += 1
        t = rng.random()
        r_max = rad(t) * S
        # Bias toward the outer shell — that's where foliage reads.
        rr = r_max * (rng.random() ** 0.35)
        a = rng.uniform(0, 2 * math.pi)
        x, y, z = rr * math.cos(a), rr * math.sin(a), (z0 + t * (z1 - z0)) * H
        r = lobe_r * rng.uniform(0.75, 1.15)
        # Keep lobes inside the silhouette.
        shrink = max(0.25, min(1.0, (r_max - rr) / r + 0.6)) if r_max > 0 else 0.3
        r *= shrink
        z = min(max(z, z0 * H + r * 0.6), H - r * 0.8)
        lobes.append((Vector((x, y, z)), r))
    return lobes


# ---------------------------------------------------------------- shared library objects


def _library():
    return collection("landviz_library", hidden=True)


def leaf_cluster(leaf: float, spray: float, kind: str, rng: random.Random, name: str):
    """A twig spray: many leaf cards (or needle tufts) of botanical size `leaf` filling a
    `spray`-sized dome. Modeled with +Z as "outward" — the scatter aligns +Z to the canopy
    surface normal, so leaves face out toward the light like real foliage.
    Built once per species and instanced thousands of times."""
    bm = bmesh.new()
    n = int(max(6, min(40, (spray / leaf) ** 2 * 1.4)))
    for _ in range(n):
        # position in a flattened dome (local Y up/out)
        a = rng.uniform(0, 2 * math.pi)
        r = spray * 0.5 * math.sqrt(rng.random())
        center = Vector((r * math.cos(a), r * math.sin(a), rng.uniform(-0.15, 0.25) * spray))
        if kind == "needle":
            _needle_tuft(bm, center, leaf, rng)
            continue
        out = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(0.2, 1.0))).normalized()
        normal = Vector((rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5), 1.0)).normalized()
        axis = (out - normal * out.dot(normal))
        if axis.length < 1e-3:
            axis = Vector((1, 0, 0))
        axis.normalize()
        side = axis.cross(normal).normalized()
        L = leaf * rng.uniform(0.8, 1.15)
        W = L * rng.uniform(0.45, 0.6)
        _leaf_card(bm, center, axis, side, normal, L, W)
    me = mesh_from_bmesh(name, bm)
    return new_object(name, me, _library())


def _leaf_card(bm, center, axis, side, normal, L, W):
    """Pointed-ellipse leaf with a slight fold along the midrib (5 segments)."""
    segs = 5
    mid, left, right = [], [], []
    for k in range(segs + 1):
        u = k / segs
        hw = W / 2 * math.sin(math.pi * u) ** 0.8
        p = center + axis * (u - 0.5) * L
        mid.append(bm.verts.new(p))
        if 0 < k < segs:
            left.append(bm.verts.new(p + side * hw + normal * hw * 0.3))
            right.append(bm.verts.new(p - side * hw + normal * hw * 0.3))
    bm.faces.new([mid[0], left[0], mid[1]])
    bm.faces.new([mid[0], mid[1], right[0]])
    for j in range(len(left) - 1):
        bm.faces.new([mid[j + 1], left[j], left[j + 1], mid[j + 2]])
        bm.faces.new([mid[j + 1], mid[j + 2], right[j + 1], right[j]])
    bm.faces.new([mid[-2], left[-1], mid[-1]])
    bm.faces.new([mid[-2], mid[-1], right[-1]])


def _needle_tuft(bm, center, size, rng):
    for _ in range(10):
        d = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(0.1, 1))).normalized()
        side = d.cross(Vector((0, 0, 1))).normalized() if abs(d.z) < 0.99 else Vector((1, 0, 0))
        w = size * 0.025
        tip = center + d * size * rng.uniform(0.7, 1.0)
        bm.faces.new([bm.verts.new(center - side * w), bm.verts.new(center + side * w),
                      bm.verts.new(tip + side * w * 0.3), bm.verts.new(tip - side * w * 0.3)])


def spray_size(spec) -> float:
    """Twig-spray size: reads at landscape viewing distance, scales with the plant."""
    base = max(spec.spread, spec.height * 0.6)
    return spec.g("spray", max(0.05, min(0.5, base * 0.055)))


def flower_object(shape: str, size: float, color: str, rng: random.Random, name: str):
    bm = bmesh.new()
    if shape == "ball":  # mophead / panicle hydrangea, lilac: a lumpy sphere of florets
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=size / 2)
        for v in bm.verts:
            v.co *= rng.uniform(0.85, 1.1)
            v.co.z *= 1.15 if rng.random() < 0.5 else 1.0
    elif shape == "spike":  # salvia, lavender, astilbe plume
        bmesh.ops.create_cone(bm, cap_ends=True, segments=6, radius1=size * 0.12, radius2=0.0, depth=size)
        bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, size / 2))
    elif shape == "plume":  # grass seed heads
        bmesh.ops.create_cone(bm, cap_ends=True, segments=5, radius1=size * 0.06, radius2=size * 0.02, depth=size)
        bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, size / 2))
    else:  # "disc" daisy-like / "blossom": center + petals
        petals = 5 if shape == "blossom" else 12
        c = bm.verts.new((0, 0, 0))
        ring = []
        for i in range(petals * 2):
            a = 2 * math.pi * i / (petals * 2)
            r = size / 2 if i % 2 == 0 else size / 5
            ring.append(bm.verts.new((r * math.cos(a), r * math.sin(a), size * 0.05 * (i % 2))))
        for i in range(len(ring)):
            bm.faces.new([c, ring[i], ring[(i + 1) % len(ring)]])
    me = mesh_from_bmesh(name, bm)
    me.materials.append(materials.get("petal", color=color))
    if shape == "ball":
        me.shade_smooth()
    obj = new_object(name, me, _library())
    obj["size"], obj["shape"] = size, shape
    return obj


def default_flower_shape(spec: PlantSpec) -> str:
    if spec.g("flower_shape", None):
        return spec.generator["flower_shape"]
    if spec.form.startswith(("grass",)):
        return "plume"
    if spec.form == "perennial_spike":
        return "spike"
    if spec.category == "tree":
        return "blossom"
    if spec.category == "shrub":
        return "ball" if spec.leaf_size == "large" else "blossom"
    return "disc"


# ---------------------------------------------------------------- woody pipeline


def _curve_skeleton(name, branches, bark_color, col):
    """branches: list of (points[list[Vector]], radii[list[float]]) → one bark mesh object."""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = 1.0
    cu.bevel_resolution = 2
    cu.use_fill_caps = True
    cu.resolution_u = 3
    for pts, radii in branches:
        sp = cu.splines.new("POLY")
        sp.points.add(len(pts) - 1)
        for p, co, r in zip(sp.points, pts, radii):
            p.co = (co.x, co.y, co.z, 1)
            p.radius = r
    obj = bpy.data.objects.new(name, cu)
    col.objects.link(obj)
    obj.data.materials.append(materials.get("bark", color=bark_color))
    return obj


def _branch_path(a: Vector, b: Vector, rng, segs=5, wiggle=0.08):
    pts = []
    d = b - a
    side = d.orthogonal().normalized()
    for i in range(segs + 1):
        t = i / segs
        p = a.lerp(b, t)
        if 0 < i < segs:
            p += side * math.sin(t * math.pi) * d.length * rng.uniform(-wiggle, wiggle)
            p += Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-0.3, 0.6))) * d.length * wiggle * 0.5
        pts.append(p)
    return pts


def build_skeleton(spec: PlantSpec, lobes, rng, col):
    H = spec.height
    base_r = max(0.01, spec.g("trunk_radius", 0.014 * H if spec.category == "tree" else 0.01 * H))
    branches = []
    is_tree = spec.form.startswith(("tree", "conifer"))
    trunks = int(spec.g("trunk_count", 3 if spec.form == "tree_multistem" else 1))
    z0, z1, _ = envelope(spec.form)
    if spec.form.startswith("conifer"):
        top = Vector((0, 0, H * 0.97))
        branches.append((_branch_path(Vector((0, 0, -0.1)), top, rng, 8, 0.01),
                         [base_r * (1 - i / 8) + 0.004 for i in range(9)]))
        for c, r in lobes:  # short horizontal-ish limbs to each lobe
            start = Vector((0, 0, c.z + r * 0.3))
            branches.append((_branch_path(start, c, rng, 3, 0.05), [base_r * 0.25, base_r * 0.15, base_r * 0.1, 0.004]))
        return _curve_skeleton(f"{col.name}_bark", branches, spec.bark, col)

    fork_z = z0 * H * (0.55 if is_tree else 0.1)
    trunk_tops = []
    for k in range(trunks):
        if trunks == 1:
            top = Vector((rng.uniform(-0.03, 0.03) * H, rng.uniform(-0.03, 0.03) * H, max(fork_z, 0.05)))
            base = Vector((0, 0, -0.1))
        else:
            a = 2 * math.pi * k / trunks + rng.uniform(-0.4, 0.4)
            lean = spec.spread * 0.18
            top = Vector((lean * math.cos(a), lean * math.sin(a), max(fork_z, z0 * H * 0.9)))
            base = Vector((math.cos(a), math.sin(a), 0)) * base_r * 1.5 + Vector((0, 0, -0.1))
        r0 = base_r / math.sqrt(trunks)
        branches.append((_branch_path(base, top, rng, 4, 0.04), [r0 * 1.15, r0, r0 * 0.95, r0 * 0.9, r0 * 0.85]))
        trunk_tops.append((top, r0 * 0.85))
    # Each lobe gets a limb from the nearest trunk top, plus a couple of twigs inside the lobe.
    for c, r in lobes:
        top, tr = min(trunk_tops, key=lambda t: (t[0] - c).length)
        L = (c - top).length
        rr = max(0.004, tr * min(1.0, 0.25 + L / max(H, 0.1)) * 0.38)
        branches.append((_branch_path(top, c, rng, 5, 0.12), [rr, rr * 0.85, rr * 0.7, rr * 0.55, rr * 0.4, rr * 0.25]))
        if is_tree:
            for _ in range(2):
                tip = c + Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-0.3, 1))).normalized() * r * 0.9
                branches.append((_branch_path(c, tip, rng, 3, 0.15), [rr * 0.3, rr * 0.2, rr * 0.12, 0.003]))
    return _curve_skeleton(f"{col.name}_bark", branches, spec.bark, col)


def build_canopy(spec: PlantSpec, lobes, rng, col, leaf_objs, flower_obj):
    """Lobe emitter mesh + foliage and flower particle systems."""
    bm = bmesh.new()
    for c, r in lobes:
        res = bmesh.ops.create_icosphere(bm, subdivisions=2 if r > 0.4 else 1, radius=r)
        for v in res["verts"]:
            v.co = v.co * rng.uniform(0.92, 1.08) + c
    me = mesh_from_bmesh(f"{col.name}_canopy", bm)
    emitter = new_object(f"{col.name}_canopy", me, col)
    # Break up the spheres a little so silhouettes aren't bubbly.
    tex = bpy.data.textures.new(f"{col.name}_clouds", "CLOUDS")
    tex.noise_scale = max(0.15, spec.spread * 0.15)
    disp = emitter.modifiers.new("lumps", "DISPLACE")
    disp.texture = tex
    disp.strength = min(spec.spread, spec.height) * (0.04 if spec.sheared else 0.12)
    disp.mid_level = 0.5

    cluster = spray_size(spec)
    area = sum(4 * math.pi * r * r for _c, r in lobes)
    density = spec.g("leaf_density", 1.0) * spec.leaf_density * spec.quality_mult
    # ~2.5x coverage of the lobe skins (lobes overlap, so the visible shell is denser still).
    coverage = 4.0 if (spec.form.startswith("conifer") or spec.sheared or spec.form == "hedge") else 2.5
    count = int(min(spec.g("max_sprays", 80000), area / (cluster * cluster) * coverage * density))
    if spec.impostor:
        if spec.leaf_density > 0:
            emitter.data.materials.append(materials.get("flat", color=spec.foliage, roughness=0.8))
            emitter.data.shade_smooth()
        else:
            emitter.modifiers.clear()
            emitter.data = bpy.data.meshes.new(f"{col.name}_empty")
        return emitter
    layers = []
    if count > 0 and leaf_objs:
        leaf_col = collection(f"{col.name}_leaves", parent=_library())
        for o in leaf_objs:
            if o.name not in leaf_col.objects:
                leaf_col.objects.link(o)
        layers.append(Layer(leaf_col, density=count / area, align="normal", tilt=0.45,
                            scale=(0.7, 1.3), seed=rng.randrange(1 << 16)))
    if flower_obj is not None and spec.flower_density > 0:
        fsize = flower_obj["size"]
        # Enough flowers to cover ~this fraction of the canopy skin at full bloom.
        cover = {"ball": 0.25, "blossom": 0.5, "disc": 0.15}.get(flower_obj["shape"], 0.1)
        fcount = min(20000, area / (fsize * fsize) * cover * spec.flower_density * max(0.5, spec.quality_mult))
        fcol = collection(f"{col.name}_flowers", parent=_library())
        if flower_obj.name not in fcol.objects:
            fcol.objects.link(flower_obj)
        layers.append(Layer(fcol, density=fcount / area, align="normal", tilt=0.3, scale=(0.7, 1.2),
                            seed=rng.randrange(1 << 16)))
    if layers:
        scatter(emitter, layers, keep_mesh=False, name="foliage")
    else:  # bare: keep the emitter out of the render entirely
        emitter.modifiers.clear()
        emitter.data = bpy.data.meshes.new(f"{col.name}_empty")
    return emitter


# ---------------------------------------------------------------- form builders


def _leaf_set(spec: PlantSpec, rng, col):
    if spec.leaf_density <= 0 or spec.impostor:
        return []
    kind = spec.leaf_size
    size = LEAF_SIZE_M.get(kind, 0.07)
    spray = spray_size(spec)
    # Visual floor: tiny botanical leaves (honeylocust leaflets, needles) would make airy,
    # see-through sprays at landscape distance; real twigs read as dense fronds.
    size = min(max(size, spray / (4.0 if kind == "needle" else 5.5)), spray * 0.6)
    mat = materials.get("leaf", color=spec.foliage, translucency=0.35 if kind != "needle" else 0.15)
    objs = []
    for i in range(3):
        o = leaf_cluster(size, spray, kind, random.Random(rng.random()), f"{col.name}_leafset{i}")
        o.data.materials.append(mat)
        objs.append(o)
    return objs


@form("tree_round", "tree_oval", "tree_vase", "tree_spreading", "tree_weeping", "tree_columnar",
      "tree_multistem", "conifer_conical", "conifer_columnar", "shrub_round", "shrub_upright",
      "shrub_spreading", "shrub_mound", "hedge", "perennial_mound", "groundcover")
def woody(spec: PlantSpec, col, rng):
    lobes = sample_lobes(spec, rng)
    if spec.category in ("tree", "shrub") and not spec.form == "groundcover":
        build_skeleton(spec, lobes, rng, col)
    leaves = _leaf_set(spec, rng, col)
    flower = None
    if spec.flower_color and spec.flower_density > 0:
        shape = default_flower_shape(spec)
        fsize = {"ball": max(0.12, min(0.25, spec.spread * 0.12)), "spike": spec.height * 0.25,
                 "plume": spec.height * 0.2, "disc": 0.07, "blossom": 0.035}[shape]
        flower = flower_object(shape, fsize, spec.flower_color, rng, f"{col.name}_flower")
    build_canopy(spec, lobes, rng, col, leaves, flower)


@form("grass_clump", "grass_fountain")
def grass(spec: PlantSpec, col, rng):
    """Blade-by-blade clump. Clump = upright vase of blades; fountain = arching outward."""
    H, S = spec.height, spec.spread
    n = int(spec.g("blades", 170 if spec.form == "grass_clump" else 220) * max(0.4, spec.quality_mult))
    bm = bmesh.new()
    tint = bm.loops.layers.color.new("tint")
    width = spec.g("blade_width", max(0.006, min(0.02, H * 0.012)))
    for _ in range(n):
        a = rng.uniform(0, 2 * math.pi)
        r0 = rng.uniform(0, S * 0.12)
        base = Vector((r0 * math.cos(a), r0 * math.sin(a), 0))
        out = Vector((math.cos(a), math.sin(a), 0))
        L = H * rng.uniform(0.6, 1.0)
        if spec.form == "grass_clump":
            lean = rng.uniform(0.05, 0.35) * (S / max(H, 0.1))
            arch = 0.15
        else:
            lean = rng.uniform(0.6, 1.4)
            arch = rng.uniform(0.8, 1.4)
        segs = 5
        side = Vector((-math.sin(a), math.cos(a), 0))
        prev = None
        shade = rng.uniform(0.75, 1.15)
        col_rgba = (shade, shade * rng.uniform(0.95, 1.05), shade * 0.9, 1.0)
        for i in range(segs + 1):
            t = i / segs
            pos = base + out * (lean * L * t * 0.6 + arch * L * t * t * 0.3 * (1 if spec.form != "grass_clump" else 0.2)) \
                + Vector((0, 0, L * (t - arch * 0.35 * t ** 3 if spec.form != "grass_clump" else t)))
            w = width * (1 - t * 0.85)
            v1, v2 = bm.verts.new(pos - side * w), bm.verts.new(pos + side * w)
            if prev:
                f = bm.faces.new([prev[0], prev[1], v2, v1])
                for loop in f.loops:
                    loop[tint] = col_rgba
            prev = (v1, v2)
    me = mesh_from_bmesh(f"{col.name}_blades", bm)
    me.materials.append(materials.get("blade_vcol", color=spec.foliage))
    new_object(f"{col.name}_blades", me, col)
    if spec.flower_color and spec.flower_density > 0:
        # Seed heads on stalks above the foliage.
        bm = bmesh.new()
        stalks = int(n * 0.25 * spec.flower_density)
        for _ in range(stalks):
            a = rng.uniform(0, 2 * math.pi)
            r = rng.uniform(0, S * 0.18)
            top = Vector((r * math.cos(a) * 1.6, r * math.sin(a) * 1.6, H * rng.uniform(1.0, 1.2)))
            bmesh.ops.create_cone(bm, cap_ends=True, segments=4, radius1=0.004, radius2=0.002,
                                  depth=top.length, matrix=_seg_matrix(Vector((r * math.cos(a), r * math.sin(a), 0)), top))
        if stalks:
            me = mesh_from_bmesh(f"{col.name}_stalks", bm)
            me.materials.append(materials.get("leaf", color=spec.foliage))
            new_object(f"{col.name}_stalks", me, col)
            fl = flower_object("plume", H * 0.22, spec.flower_color, rng, f"{col.name}_plume")
            pts = []
            for _ in range(stalks):
                a = rng.uniform(0, 2 * math.pi)
                r = rng.uniform(0, S * 0.28)
                pts.append((r * math.cos(a), r * math.sin(a), H * rng.uniform(0.95, 1.15)))
            _point_instances(col, f"{col.name}_plumes", pts, fl, rng)
        else:
            bm.free()


def _seg_matrix(a: Vector, b: Vector):
    from mathutils import Matrix
    d = b - a
    rot = d.to_track_quat("Z", "Y").to_matrix().to_4x4()
    return Matrix.Translation((a + b) / 2) @ rot


@form("perennial_spike")
def spike(spec: PlantSpec, col, rng):
    """Basal foliage mound + upright flower spikes (salvia, coneflower, lavender, astilbe)."""
    base = PlantSpec(**{**spec.__dict__, "form": "perennial_mound", "height": spec.height * 0.45,
                        "flower_color": None, "flower_density": 0})
    woody(base, col, rng)
    n = int(spec.g("stems", 18 if spec.spread < 0.8 else 30))
    bm = bmesh.new()
    tips = []
    for _ in range(n):
        a = rng.uniform(0, 2 * math.pi)
        r = rng.uniform(0, spec.spread * 0.3)
        foot = Vector((r * 0.4 * math.cos(a), r * 0.4 * math.sin(a), 0))
        top = Vector((r * math.cos(a), r * math.sin(a), spec.height * rng.uniform(0.75, 1.0)))
        bmesh.ops.create_cone(bm, cap_ends=False, segments=4, radius1=0.004, radius2=0.002,
                              depth=(top - foot).length, matrix=_seg_matrix(foot, top))
        tips.append(top)
    me = mesh_from_bmesh(f"{col.name}_stems", bm)
    me.materials.append(materials.get("leaf", color=spec.foliage))
    new_object(f"{col.name}_stems", me, col)
    if spec.flower_color and spec.flower_density > 0:
        shape = default_flower_shape(spec)
        size = spec.height * (0.3 if shape == "spike" else 0.0) or 0.07
        fl = flower_object(shape, size, spec.flower_color, rng, f"{col.name}_flower")
        n_fl = max(1, int(len(tips) * spec.flower_density))
        pts = [tuple(t - Vector((0, 0, size * 0.5 if shape == "spike" else 0))) for t in tips[:n_fl]]
        _point_instances(col, f"{col.name}_flower_pts", pts, fl, rng)


def _point_instances(col, name, pts, obj, rng):
    """Instance `obj` upright at each point."""
    bm = bmesh.new()
    for p in pts:
        bm.verts.new(p)
    em = new_object(name, mesh_from_bmesh(name, bm), col)
    icol = collection(f"{name}_set", parent=_library())
    if obj.name not in icol.objects:
        icol.objects.link(obj)
    scatter(em, [Layer(icol, mode="points", align="up", scale=(0.75, 1.15), seed=rng.randrange(1 << 16))],
            name="points")


# ---------------------------------------------------------------- prototype + instancing API


_PROTOS: dict[tuple, bpy.types.Collection] = {}


def prototype(spec: PlantSpec, variant: int):
    key = (spec.species_id, variant)
    if key in _PROTOS:
        return _PROTOS[key]
    col = collection(f"plant_{spec.species_id}_v{variant}", parent=_library())
    rng = random.Random(f"{spec.species_id}:{variant}:{spec.seed}")
    builder = FORMS.get(spec.form) or FORMS["shrub_round"]
    builder(spec, col, rng)
    _PROTOS[key] = col
    return col


def place(proto_col, name, x, y, z, height_scale, spread_scale, rotation, parent_col):
    inst = bpy.data.objects.new(name, None)
    inst.instance_type = "COLLECTION"
    inst.instance_collection = proto_col
    inst.location = (x, y, z)
    inst.rotation_euler = (0, 0, rotation)
    inst.scale = (spread_scale, spread_scale, height_scale)
    inst.empty_display_size = 0.3
    parent_col.objects.link(inst)
    return inst



def asset_prototype(species_id, path, height, spread):
    """Use a downloaded/hand-made model instead of the generator; normalized to height × spread."""
    key = (species_id, "asset")
    if key in _PROTOS:
        return _PROTOS[key]
    from ..builders.structures import import_model
    col = collection(f"plant_{species_id}_asset", parent=_library())
    objs = import_model(path, col)
    bpy.context.view_layer.update()
    pts = [o.matrix_world @ Vector(c) for o in objs if o.type == "MESH" for c in o.bound_box]
    if pts:
        h = max(p.z for p in pts) - min(p.z for p in pts)
        w = max(max(p.x for p in pts) - min(p.x for p in pts), max(p.y for p in pts) - min(p.y for p in pts))
        root = bpy.data.objects.new(f"{species_id}_asset_root", None)
        col.objects.link(root)
        for o in objs:
            if o.parent is None:
                o.parent = root
        root.scale = (spread / max(w, 1e-3), spread / max(w, 1e-3), height / max(h, 1e-3))
        root.location.z = -min(p.z for p in pts) * root.scale.z
    _PROTOS[key] = col
    return col
