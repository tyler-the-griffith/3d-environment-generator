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
