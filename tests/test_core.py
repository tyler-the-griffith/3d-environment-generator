"""Core tests (no Blender). Run:  python3 -m unittest discover -s tests"""
import math
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from landviz import geometry, solar, units  # noqa: E402
from landviz import project as project_mod  # noqa: E402
from landviz.catalog import Catalog  # noqa: E402
from landviz.resolve import resolve  # noqa: E402


class Units(unittest.TestCase):
    def test_lengths(self):
        self.assertAlmostEqual(units.length("10 ft"), 3.048)
        self.assertAlmostEqual(units.length("6' 6\""), 1.9812)
        self.assertAlmostEqual(units.length("18in"), 0.4572)
        self.assertAlmostEqual(units.length(5, "ft"), 1.524)
        self.assertAlmostEqual(units.length("2 m", "ft"), 2.0)
        with self.assertRaises(units.UnitError):
            units.length("3 furlongs")

    def test_pitch(self):
        self.assertAlmostEqual(units.pitch("12/12"), 45.0)
        self.assertAlmostEqual(units.pitch(30), 30.0)


class Geometry(unittest.TestCase):
    def test_area_ccw(self):
        sq = [(0, 0), (0, 2), (2, 2), (2, 0)]  # clockwise
        self.assertAlmostEqual(geometry.area(sq), 4)
        self.assertGreater(geometry.signed_area(geometry.ensure_ccw(sq)), 0)

    def test_plan_frame_roundtrip(self):
        # 100 px per 10 m, image y down, origin pixel (50, 950) = site (0, 0)
        f = geometry.PlanFrame([(50, 950), (1050, 950)], [(0, 0), (100, 0)])
        x, y = f.to_site([(50, 850)])[0]
        self.assertAlmostEqual(x, 0, places=6)
        self.assertAlmostEqual(y, 10, places=6)  # up in the image = north
        self.assertAlmostEqual(f.meters_per_pixel, 0.1)

    def test_similarity_3d(self):
        import numpy as np
        rng = np.random.default_rng(0)
        src = rng.random((6, 3))
        a = math.radians(30)
        R = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
        dst = (2.5 * (R @ src.T)).T + [3, -1, 0.5]
        s, R2, t = geometry.similarity_fit(src, dst)
        self.assertAlmostEqual(s, 2.5)
        self.assertTrue(np.allclose(geometry.apply_similarity(s, R2, t, src), dst))

    def test_fill_and_resample(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        pts = geometry.fill_polygon(sq, 1.0, seed=1, jitter=0)
        self.assertTrue(80 <= len(pts) <= 120)
        self.assertTrue(all(geometry.point_in_polygon(x, y, sq) for x, y in pts))
        line = geometry.resample_polyline([(0, 0), (10, 0)], 2.5)
        self.assertEqual(len(line), 5)

    def test_offset_polyline(self):
        poly = geometry.offset_polyline_to_polygon([(0, 0), (10, 0)], 2)
        self.assertAlmostEqual(geometry.area(poly), 20)

    def test_tps_exact_at_points(self):
        pts = [(0, 0, 0), (10, 0, 1), (0, 10, 2), (10, 10, 4), (5, 5, 1.5)]
        tps = geometry.ThinPlateSpline(pts)
        for x, y, z in pts:
            self.assertAlmostEqual(float(tps([(x, y)])[0]), z, places=6)


class Solar(unittest.TestCase):
    def test_noon_columbus_june(self):
        # Solar noon near the June solstice at 40°N: sun due south, elevation ≈ 73.5°.
        tz = timezone(timedelta(hours=-4))
        best = max((solar.sun_position(40.0, -83.0, datetime(2026, 6, 21, h, m, tzinfo=tz))
                    for h in (13, 14) for m in range(0, 60, 2)), key=lambda p: p[1])
        self.assertAlmostEqual(best[1], 73.5, delta=0.6)
        self.assertAlmostEqual(best[0], 180, delta=3)

    def test_afternoon_is_west(self):
        tz = timezone(timedelta(hours=-4))
        az, el = solar.sun_position(40.0, -83.0, datetime(2026, 7, 15, 18, 0, tzinfo=tz))
        self.assertTrue(240 < az < 300 and 10 < el < 45)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.cat = Catalog.builtin()

    def test_lookup_by_alias(self):
        self.assertEqual(self.cat.get("Karl Foerster Grass").id, "calamagrostis")
        self.assertEqual(self.cat.get("Limelight Hydrangea").id, "hydrangea_paniculata")
        self.assertIsNone(self.cat.get("Triffid"))

    def test_growth_monotonic(self):
        sp = self.cat.get("acer_rubrum")
        sizes = [sp.size_at(y)[0] for y in (0, 1, 5, 10, 30)]
        self.assertEqual(sizes, sorted(sizes))
        self.assertLess(sizes[-1], sp.height + 1e-6)
        self.assertEqual(sp.size_at("mature"), (sp.height, sp.spread))

    def test_seasons(self):
        maple = self.cat.get("acer_rubrum")
        self.assertEqual(maple.state(1)["leaf_density"], 0)
        self.assertEqual(maple.state(10)["foliage_color"], "#c1301f")
        self.assertEqual(self.cat.get("buxus").state(1)["leaf_density"], 1.0)
        self.assertIsNotNone(self.cat.get("nepeta").state(6)["flower_color"])

    def test_all_forms_known(self):
        from landviz.catalog import KNOWN_FORMS
        for sp in self.cat.species.values():
            self.assertIn(sp.form, KNOWN_FORMS, sp.id)


class Resolve(unittest.TestCase):
    def test_demo_resolves_clean(self):
        proj = project_mod.load("demo")
        scene = resolve(proj)
        self.assertFalse([i for i in proj.issues if i.level == "error"])
        kinds = {e["kind"] for e in scene["elements"]}
        self.assertTrue({"lawn", "bed", "paving", "plant", "structure", "wall", "fence"} <= kinds)
        self.assertEqual(scene["site"]["terrain"]["type"], "graded")
        layers = [e["layer"] for e in scene["elements"]]
        self.assertEqual(layers, sorted(layers))

    def test_deterministic(self):
        a = resolve(project_mod.load("demo"))
        b = resolve(project_mod.load("demo"))
        self.assertEqual([(p.get("x"), p.get("y")) for p in a["elements"]],
                         [(p.get("x"), p.get("y")) for p in b["elements"]])

    def test_variant_and_overrides(self):
        winter = resolve(project_mod.load("demo", variant="winter"))
        self.assertEqual(winter["growth"]["month"], 1)
        y10 = resolve(project_mod.load("demo", sets=["render.year=10"]))
        y1 = resolve(project_mod.load("demo", sets=["render.year=1"]))
        h = lambda s: max(p["height"] for p in s["elements"] if p.get("species") == "cercis_canadensis")  # noqa: E731
        self.assertGreater(h(y10), h(y1))

    def test_errors_reported(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "project.yaml").write_text(
                "project: {units: ft}\nsite: {boundary: [[0,0],[10,0],[10,10],[0,10]]}\n"
                "elements:\n  - {kind: plant, species: triffid, at: [1, 1]}\n  - {kind: gazebo}\n")
            proj = project_mod.load(d)
            resolve(proj)
            msgs = " ".join(i.message for i in proj.issues if i.level == "error")
            self.assertIn("unknown species", msgs)
            self.assertIn("unknown kind", msgs)

    def test_plan_frame_points(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "project.yaml").write_text(
                "project: {units: ft}\n"
                "site: {boundary: [[0,0],[100,0],[100,100],[0,100]]}\n"
                "plan: {calibration: {points: [{px: [0, 1000], at: [0, 0]}, {px: [1000, 1000], at: [100, 0]}]}}\n"
                "elements:\n  - {kind: plant, species: buxus, at: [500, 500], frame: plan}\n")
            scene = resolve(project_mod.load(d))
            p = next(e for e in scene["elements"] if e["kind"] == "plant")
            self.assertAlmostEqual(p["x"], units.length("50 ft"), places=4)
            self.assertAlmostEqual(p["y"], units.length("50 ft"), places=4)


if __name__ == "__main__":
    unittest.main()


class RoofsAndGeo(unittest.TestCase):
    def test_roof_types(self):
        from landviz import roofs
        rect = [(0, 0), (10, 0), (10, 6), (0, 6)]
        pitch = math.degrees(math.atan(8 / 12))
        expect = {"hip": 4, "gable": 2, "half_hip": 4, "shed": 1, "flat": 1, "gambrel": 4, "mansard": 8}
        for kind, nfaces in expect.items():
            faces = roofs.roof_faces(rect, 3.0, {"type": kind, "pitch_deg": pitch}, 0.5)
            self.assertEqual(len(faces), nfaces, kind)
        ridge = max(z for f in roofs.roof_faces(rect, 3.0, {"type": "gable", "pitch_deg": pitch}) for *_, z in f)
        self.assertAlmostEqual(ridge, 3.0 + 3 * 8 / 12)  # half-depth 3 m × pitch
        prof = roofs.wall_profiles(rect, 3.0, {"type": "gable", "pitch_deg": pitch})[1]["profile"]
        self.assertAlmostEqual(max(z for _, z in prof), 5.0, places=4)  # gable end rises to the ridge
        self.assertFalse(roofs.is_convex([(0, 0), (4, 0), (4, 4), (2, 2), (0, 4)]))

    def test_wings_resolve(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "project.yaml").write_text(
                "project: {units: ft}\nsite: {boundary: [[0,0],[100,0],[100,100],[0,100]]}\n"
                "elements:\n  - id: house\n    kind: structure\n    wings:\n"
                "      - {footprint: [[0,0],[40,0],[40,28],[0,28]], wall_height: 18, roof: {type: gable, ridge: x}}\n"
                "      - {footprint: [[24,-14],[40,-14],[40,28],[24,28]], wall_height: 18, roof: {type: hip}}\n"
                "  - {id: deck, kind: deck, polygon: [[0,28],[20,28],[20,40],[0,40]], height: 6, stairs: [{edge: 1}]}\n")
            proj = project_mod.load(d)
            scene = resolve(proj)
            self.assertFalse([i for i in proj.issues if i.level == "error"])
            house = next(e for e in scene["elements"] if e["kind"] == "structure")
            self.assertEqual(len(house["wings"]), 2)
            self.assertEqual(len(house["wings"][1]["roof_faces"]), 4)

    def test_geo_roundtrip_and_latlon_parsing(self):
        from landviz import photos
        origin = (40.5727, -105.1440)
        for north in (0.0, 17.0):
            x, y = photos.latlon_to_site(40.5730, -105.1436, origin, north)
            lat, lon = photos.site_to_latlon(x, y, origin, north)
            self.assertAlmostEqual(lat, 40.5730, places=7)
            self.assertAlmostEqual(lon, -105.1436, places=7)
        self.assertEqual(photos.parse_latlon("40.5729, -105.1437"), (40.5729, -105.1437))
        lat, lon = photos.parse_latlon("40°34'22.3\"N 105°8'37.5\"W")
        self.assertAlmostEqual(lon, -105.14375, places=4)
        self.assertIsNone(photos.parse_latlon("3614 Main Street"))
        hfov, vfov = photos.fov_deg({"width": 4000, "height": 3000, "f35": 24})
        self.assertAlmostEqual(hfov, 71.6, delta=0.2)  # 24 mm-equiv on 4:3 = 84° diagonal

    def test_lidar_sampling(self):
        from landviz import lidar
        meta = {"radius": 10.0, "res": 1.0, "size": 21, "center": [40.0, -105.0]}
        E, N = np.meshgrid(np.arange(-10, 11.0), np.arange(-10, 11.0))
        grid = 100 + 0.5 * E + 0.25 * N  # a tilted plane: bilinear sampling must be exact
        self.assertAlmostEqual(float(lidar.sample(grid, meta, [3.3], [-2.7])[0]), 100 + 1.65 - 0.675, places=6)
        e, n = lidar.site_to_local(5.0, 0.0, meta, (40.0, -105.0), 0.0)
        self.assertAlmostEqual(float(e), 5.0, places=3)


class YamlEditAndMap(unittest.TestCase):
    DESIGN = """# keep me
project: {name: T, units: ft}
site:
  boundary: [[0,0],[100,0],[100,100],[0,100]]   # lot
elements:
  - {id: patio, kind: paving, polygon: [[10,10],[30,10],[30,30],[10,30]]}
  # a tree
  - id: tree
    kind: plant
    species: acer_rubrum
    at: [60, 60]
  - id: house
    kind: structure
    wings:
      - footprint:
          - [40, 40]
          - [60, 40]
          - [60, 55]
        wall_height: 9
"""

    def test_targeted_edits_keep_the_rest(self):
        import yaml
        from landviz import yamledit as Y
        t = Y.set_value(self.DESIGN, Y.element_path(self.DESIGN, "patio") + ["polygon"], [[11, 10], [30, 10], [30, 30]])
        self.assertIn("# keep me", t)
        self.assertIn("# lot", t)
        self.assertIn("# a tree", t)
        self.assertEqual(yaml.safe_load(t)["elements"][0]["polygon"], [[11, 10], [30, 10], [30, 30]])
        t = Y.set_value(t, Y.element_path(t, "tree") + ["spread"], 20)       # new key in a block mapping
        t = Y.set_value(t, Y.element_path(t, "patio") + ["frame"], "site")   # new key in a flow mapping
        t = Y.set_value(t, Y.element_path(t, "house") + ["wings", 0, "footprint"], [[40, 40], [62, 40], [62, 55]])
        d = yaml.safe_load(t)
        self.assertEqual(d["elements"][1]["spread"], 20)
        self.assertEqual(d["elements"][0]["frame"], "site")
        self.assertEqual(d["elements"][2]["wings"][0]["footprint"][1], [62, 40])
        self.assertEqual(d["elements"][2]["wings"][0]["wall_height"], 9)
        t = Y.delete_item(t, Y.element_path(t, "tree"))
        t = Y.append_item(t, ["elements"], {"id": "rock", "kind": "boulder", "at": [5, 5]})
        self.assertEqual([e["id"] for e in yaml.safe_load(t)["elements"]], ["patio", "house", "rock"])

    def test_map_edits_round_trip_in_each_frame(self):
        import shutil
        import yaml
        from landviz import sitemap
        d = Path(tempfile.mkdtemp())
        try:
            # elements authored in plan pixels (calibrated 10 px per foot): edits come back in pixels
            design = self.DESIGN.replace("project: {name: T, units: ft}", "project: {name: T, units: ft}\nplan:\n  calibration:\n"
                                         "    points: [{px: [0, 1000], at: [0, 0]}, {px: [1000, 1000], at: [100, 0]}]")
            design = design.replace("{id: patio, kind: paving, polygon: [[10,10],[30,10],[30,30],[10,30]]}",
                                    "{id: patio, kind: paving, frame: plan, polygon: [[100,900],[300,900],[300,700],[100,700]]}")
            (d / "project.yaml").write_text(design)
            proj = project_mod.load(str(d))
            scene = resolve(proj)
            data = sitemap.editable(proj, scene)
            patio = next(e for e in data["elements"] if e["id"] == "patio")["geoms"][0]["pts"]
            self.assertAlmostEqual(patio[1][0], 30 * 0.3048, places=4)          # 300 px = 30 ft
            patio[1] = [patio[1][0] + 0.3048, patio[1][1]]                         # move one corner 1 ft east
            sitemap.apply_edits(proj, [{"id": "patio", "path": ["polygon"], "pts": patio},
                                       {"id": "tree", "path": ["spread"], "length": 3.048}])
            out = yaml.safe_load((d / "project.yaml").read_text())
            self.assertEqual(out["elements"][0]["polygon"], [[100, 900], [310.0, 900.0], [300, 700], [100, 700]])
            self.assertEqual(out["elements"][1]["spread"], 10.0)
            self.assertIn("# a tree", (d / "project.yaml").read_text())
        finally:
            shutil.rmtree(d)

    def test_editor_fields_rename_and_settings(self):
        import shutil
        import yaml
        from landviz import sitemap
        d = Path(tempfile.mkdtemp())
        try:
            (d / "project.yaml").write_text(self.DESIGN + "inventory:\n  - {id: f1, category: patio, element: patio, at: [20, 20]}\n")
            (d / "edits.yaml").write_text("elements:\n  patio: {material: pavers}\n")
            (d / "variants" / "opt").mkdir(parents=True)
            (d / "variants" / "opt" / "variant.yaml").write_text("parent: base\nelements_remove: [patio]\n")
            proj = project_mod.load(str(d))
            sitemap.apply_edits(proj, [
                {"id": "patio", "path": ["material"], "value": "flagstone"},
                {"rename": {"from": "patio", "to": "back-patio"}},
                {"id": "back-patio", "path": ["note"], "value": "renamed on the map"},
                {"spec": ["site", "location", "north_offset_deg"], "value": 12},
                {"id": "tree", "replace": {"id": "tree", "kind": "plant", "species": "betula_nigra", "at": [61, 60]}},
            ])
            out = yaml.safe_load((d / "project.yaml").read_text())
            el = out["elements"][0]
            self.assertEqual((el["id"], el["material"], el["note"]), ("back-patio", "flagstone", "renamed on the map"))
            self.assertEqual(out["elements"][1], {"id": "tree", "kind": "plant", "species": "betula_nigra", "at": [61, 60]})
            self.assertEqual(out["site"]["location"]["north_offset_deg"], 12)
            self.assertEqual(out["inventory"][0]["element"], "back-patio")       # references follow the rename
            self.assertIn("back-patio", yaml.safe_load((d / "edits.yaml").read_text())["elements"])
            self.assertEqual(yaml.safe_load((d / "variants" / "opt" / "variant.yaml").read_text())["elements_remove"], ["back-patio"])
            self.assertIn("# a tree", (d / "project.yaml").read_text())
            with self.assertRaises(project_mod.SpecError):  # names must be unique
                sitemap.apply_edits(project_mod.load(str(d)), [{"rename": {"from": "back-patio", "to": "house"}}])
        finally:
            shutil.rmtree(d)

    def test_aerial_frame(self):
        from landviz import aerial, photos
        # fetch() always asks for a square in meters (the frame is a similarity: one scale)
        dlon = 0.001 * photos.M_PER_DEG_LAT / (photos.M_PER_DEG_LON_EQ * math.cos(math.radians(40.585)))
        meta = {"bbox": [-105.0850 - dlon, 40.5840, -105.0850 + dlon, 40.5860], "size": [600, 600]}
        origin = (40.5850, -105.0850)
        pf = geometry.PlanFrame(*aerial.control_points(meta, origin))
        x, y = pf.to_site([(300, 300)])[0]
        self.assertAlmostEqual(x, 0, places=1)    # the photo's center is the origin
        self.assertAlmostEqual(y, 0, places=1)
        x, y = pf.to_site([(600, 0)])[0]          # north-east corner
        self.assertGreater(x, 0)
        self.assertGreater(y, 0)
        self.assertAlmostEqual(y, 0.001 * photos.M_PER_DEG_LAT, delta=1.0)
        back = pf.to_pixels([(x, y)])[0]
        self.assertAlmostEqual(back[0], 600, places=3)
        self.assertAlmostEqual(back[1], 0, places=3)


if __name__ == "__main__":
    unittest.main()
