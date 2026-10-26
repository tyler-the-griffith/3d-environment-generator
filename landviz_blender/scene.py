"""Scene assembly: resolved scene JSON → a complete Blender scene."""
from __future__ import annotations

import math
import random
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

from . import materials
from .scatter import Layer, scatter
from .builders import BUILDERS
from .builders.linear import _offsets, extrude_band
from .builders.structures import clip_scan
from .builders.surfaces import build_terrain, ground_from_scan
from .builders.vegetation import build_all_plants
from .util import Ground, collection, log, mesh_from_bmesh, new_object

GRASS_DENSITY = {"light": 500, "full": 1400}  # tufts per m² (each tuft ≈ 9 blades)
GRASS_CAP = 2_500_000


class BuildContext:
    def __init__(self, scene: dict):
        self.scene = scene
        self.render = scene["render"]
        self.root = collection("Landscape")
        self.ground = Ground()
        self._cols = {}
        self.rec_objects = {}  # record id → (record, primary object, all created objects)

    def col(self, name):
        if name not in self._cols:
            self._cols[name] = collection(name, parent=self.root)
        return self._cols[name]

    def wall_polygon(self, rec):
        pts = [Vector(p[:2]) for p in rec["line"]]
        left, right = _offsets(pts, rec["thickness"] / 2 + 0.02)
        return [tuple(p) for p in left + right[::-1]]

    # ---- bed edging
    def edge_strip(self, name, poly, height, width, color, depth):
        loop = list(poly) + [poly[0]]
        g = self.ground
        extrude_band(name, loop, width, lambda i, p: g.z(p.x, p.y) - depth,
                     lambda i, p: g.z(p.x, p.y) + height, self.col("Beds"),
                     materials.get("flat", color=color, roughness=0.5, metallic=0.6))

    def edge_units(self, name, poly, kind, width=None):
        w = width or {"stone": 0.3, "brick": 0.1, "paver": 0.15, "block": 0.3}[kind]
        length = {"stone": 0.45, "brick": 0.2, "paver": 0.3, "block": 0.45}[kind]
        h = 0.08 if kind != "stone" else 0.15
        mat = materials.get({"stone": "stone", "brick": "brick", "paver": "pavers_gray", "block": "block"}[kind],
                            uv=False)
        bm = bmesh.new()
        rng = random.Random(name)
        n = len(poly)
        for i in range(n):
            a, b = Vector(poly[i][:2]), Vector(poly[(i + 1) % n][:2])
            L = (b - a).length
            if L < 1e-3:
                continue
            d = (b - a) / L
            ang = math.atan2(d.y, d.x)
            k = max(1, int(L / (length + 0.01)))
            for j in range(k):
                c = a + d * (L * (j + 0.5) / k)
                z = self.ground.z(c.x, c.y)
                res = bmesh.ops.create_cube(bm, size=1.0)
                s = (L / k - 0.01, w, h * rng.uniform(0.85, 1.1))
                for v in res["verts"]:
                    v.co = Vector((v.co.x * s[0], v.co.y * s[1], v.co.z * s[2]))
                    v.co.rotate(_rot(ang))
                    v.co += Vector((c.x, c.y, z + s[2] / 2 - 0.03))
        me = mesh_from_bmesh(name, bm)
        me.materials.append(mat)
        new_object(name, me, self.col("Beds"))

    # ---- grass
    def add_grass(self, lawn_obj, g, mode):
        lib = collection("landviz_library", hidden=True)
        tufts = collection(f"grass_tufts_{g['color']}_{g['height']:.3f}", parent=lib)
        if not tufts.objects:
            mat = materials.get("grass_blade", color=g["color"], health=g["health"])
            for v in range(4):
                tuft = _grass_tuft(f"tuft_{v}", g["height"], random.Random(v))
                tuft.data.materials.append(mat)
                tufts.objects.link(tuft)
        area = sum(p.area for p in lawn_obj.data.polygons)
        density = GRASS_DENSITY[mode]
        count = int(min(GRASS_CAP, area * density))
        scatter(lawn_obj, [Layer(tufts, density=count / max(area, 1e-6), align="up", scale=(0.6, 1.3), seed=3)],
                keep_mesh=True, name="grass")
        log(f"grass: {lawn_obj.name} {area:.0f} m² → {count:,} tufts ({mode})")


def _rot(a):
    from mathutils import Matrix
    return Matrix.Rotation(a, 3, "Z")


def _grass_tuft(name, height, rng):
    """~9 curved blades, modeled +Z up."""
    bm = bmesh.new()
    for _ in range(9):
        a = rng.uniform(0, 2 * math.pi)
        r = rng.uniform(0, 0.02)
        base = Vector((r * math.cos(a), r * math.sin(a), 0))
        out = Vector((math.cos(a), math.sin(a), 0))
        side = Vector((-math.sin(a), math.cos(a), 0))
        L = height * rng.uniform(0.6, 1.25)
        lean = rng.uniform(0.1, 0.45)
        prev = None
        for i in range(4):
            t = i / 3
            p = base + out * (lean * L * t * t) + Vector((0, 0, L * t * (1 - 0.25 * lean * t)))
            w = 0.0025 * (1 - t * 0.9)
            v1, v2 = bm.verts.new(p - side * w), bm.verts.new(p + side * w)
            if prev:
                bm.faces.new([prev[0], prev[1], v2, v1])
            prev = (v1, v2)
    return bpy.data.objects.new(name, mesh_from_bmesh(name, bm))


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    s = bpy.context.scene
    s.unit_settings.system = "METRIC"
    s.render.engine = "CYCLES"


def build(scene: dict):
    reset()
    materials.ASSET_DIRS[:] = [Path(scene["project"]["dir"]) / "assets",
                               Path(__file__).resolve().parent.parent / "assets"]
    ctx = BuildContext(scene)
    site = ctx.col("Site")
    t = scene["site"]["terrain"]
    if t["type"] == "scan":
        # The drone scan is the context (house, neighbours) *and* the source of bare-earth grade:
        # import it unclipped, filter a ground surface out of it, then cut the yard away so the
        # new landscape is built on clean terrain while the scanned house stays.
        scans = [r for r in scene["elements"] if r["kind"] == "mesh" and r.get("role") == "terrain"]
        if not scans:
            raise RuntimeError("terrain type 'scan' needs a mesh element with role: terrain")
        for rec in scans:
            BUILDERS["mesh"](rec, ctx, clip=False)
        bpy.context.view_layer.update()
        scan_objs = [o for o in ctx.col("Imported").all_objects if o.type == "MESH"]
        heights = ground_from_scan(scene, scan_objs)
        terrain = build_terrain(scene, site, ctx.render["quality"], heights=heights)
        ctx.ground = Ground([terrain])
        for rec in scans:
            root = bpy.data.objects[rec["id"]]
            clip_scan([o for o in root.children_recursive if o.type == "MESH"], rec.get("keep", []),
                      rec.get("clip_out", []) or [scene["site"]["boundary"]])
        log(f"scan: ground filtered from {len(scan_objs)} mesh(es)")
    else:
        terrain = build_terrain(scene, site, ctx.render["quality"])
        ctx.ground = Ground([terrain])

    counts = {}
    for rec in scene["elements"]:
        if rec["kind"] == "mesh" and rec.get("role") == "terrain":
            continue
        fn = BUILDERS.get(rec["kind"])
        if fn is None:
            log(f"no builder for kind {rec['kind']!r} — skipped {rec['id']}")
            continue
        before = set(bpy.data.objects)
        primary = fn(rec, ctx)
        created = [o for o in bpy.data.objects if o not in before]
        if created:
            if not isinstance(primary, bpy.types.Object):
                primary = created[0]
            ctx.rec_objects[rec["id"]] = (rec, primary, created)
        counts[rec["kind"]] = counts.get(rec["kind"], 0) + 1
    build_all_plants(ctx)
    tag_records(ctx)
    log("built: " + ", ".join(f"{k}×{v}" for k, v in sorted(counts.items())))
    return ctx


def tag_records(ctx):
    """Stamp every built object with the record it came from and its as-built transform.

    `lv pull` compares these against the (possibly hand-edited) model to recover moves,
    resizes and deletions and write them back to edits.yaml — the model is never the
    source of truth, but hand edits made in it can be carried back to the one that is.
    """
    import json
    bpy.context.view_layer.update()
    from mathutils import Matrix
    ids = []
    for rid, (rec, primary, created) in ctx.rec_objects.items():
        ids.append(rid)
        # Pivot at the element's own center (geometry is built in world coordinates, so the
        # origin would otherwise be the property corner) — rotating a patio by hand then
        # turns it in place.
        if primary.type == "MESH" and primary.parent is None and primary.location.length < 1e-6 \
                and len(primary.data.vertices):
            vs = [v.co for v in primary.data.vertices]
            c = Vector(((min(v.x for v in vs) + max(v.x for v in vs)) / 2,
                        (min(v.y for v in vs) + max(v.y for v in vs)) / 2, min(v.z for v in vs)))
            primary.data.transform(Matrix.Translation(-c))
            primary.location = c
            bpy.context.view_layer.update()
        # Parent an element's parts (roof, trim, edging…) to its primary object so moving
        # it by hand in Blender moves the whole thing.
        for o in created:
            if o is not primary and o.parent is None:
                o.parent = primary
                o.matrix_parent_inverse = primary.matrix_world.inverted()
        for o in created:
            o["lv_rec"] = rid
            o["lv_src"] = rec.get("source", rid)
            o["lv_kind"] = rec["kind"]
            o["lv_primary"] = o is primary
            o["lv_m0"] = [v for row in o.matrix_world for v in row]
    bpy.context.scene["lv_records"] = json.dumps(ids)
