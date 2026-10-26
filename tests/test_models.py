"""Model store, edits and pull tests (no Blender needed)."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from landviz import blender_bridge, edits, models  # noqa: E402
from landviz import project as project_mod  # noqa: E402
from landviz.resolve import resolve  # noqa: E402

blender_bridge._VERSION = "Blender test"  # fingerprints without launching Blender

SPEC = """
project: {name: T, units: ft}
site: {boundary: [[0,0],[100,0],[100,100],[0,100]]}
elements:
  - {id: tree, kind: plant, species: acer_rubrum, at: [20, 20]}
  - {id: mass, kind: plant_group, species: nepeta, polygon: [[50,50],[60,50],[60,60],[50,60]], spacing: 3}
  - {id: patio, kind: paving, polygon: [[10,60],[30,60],[30,80],[10,80]]}
  - {kind: plant, species: buxus, at: [80, 80]}
render: {quality: preview}
variants:
  dusk: {render: {time: "21:00"}}
  fall: {render: {season: fall}}
"""


def fp(proj):
    return models.fingerprint(resolve(proj))


class Fingerprints(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "project.yaml").write_text(SPEC)

    def tearDown(self):
        shutil.rmtree(self.d)

    def load(self, variant=None, sets=None):
        return project_mod.load(str(self.d), variant, sets)

    def test_lighting_shares_the_model(self):
        base = fp(self.load())
        self.assertEqual(base, fp(self.load("dusk")))
        self.assertEqual(base, fp(self.load(sets=["render.time=08:00", "render.samples=999",
                                                  "render.resolution=[100,100]"])))

    def test_geometry_changes_the_model(self):
        base = fp(self.load())
        self.assertNotEqual(base, fp(self.load("fall")))  # foliage
        self.assertNotEqual(base, fp(self.load(sets=["render.year=1"])))  # sizes
        self.assertNotEqual(base, fp(self.load(sets=["render.quality=draft"])))  # detail tier

    def test_state_names(self):
        self.assertEqual(models.state_name(None, []), "base")
        self.assertEqual(models.state_name("fall", ["render.quality=final"]), "fall")
        self.assertEqual(models.state_name(None, ["render.time=09:30"]), "base~time=0930")


class Edits(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "project.yaml").write_text(SPEC)

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_auto_ids_are_positional(self):
        proj = project_mod.load(str(self.d))
        self.assertIn("plant-3", proj.auto_ids)

    def test_edits_merge_and_adjust(self):
        before = resolve(project_mod.load(str(self.d)))
        ids = [e["id"] for e in before["elements"] if e["source"] == "mass"]
        self.assertGreater(len(ids), 3)
        (self.d / "edits.yaml").write_text(
            "elements:\n"
            "  tree: {at: [25, 22], frame: site}\n"
            "  mass: {adjust: {1: {remove: true}, 2: {at: [55, 55]}}}\n")
        after = resolve(project_mod.load(str(self.d)))
        E = {e["id"]: e for e in after["elements"]}
        self.assertAlmostEqual(E["tree"]["x"], 25 * 0.3048)
        self.assertNotIn("mass#1", E)  # removed, and siblings keep their indices
        self.assertIn("mass#2", E)
        self.assertAlmostEqual(E["mass#2"]["x"], 55 * 0.3048)
        self.assertEqual(len([i for i in E if i.startswith("mass#")]), len(ids) - 1)

    def test_variant_inherits_base_edits(self):
        (self.d / "edits.yaml").write_text("elements:\n  tree: {at: [30, 30], frame: site}\n")
        fall = resolve(project_mod.load(str(self.d), "fall"))
        tree = next(e for e in fall["elements"] if e["id"] == "tree")
        self.assertAlmostEqual(tree["x"], 30 * 0.3048)

    def test_report_to_patches(self):
        proj = project_mod.load(str(self.d))
        scene = resolve(proj)
        ft = 0.3048
        report = {"changes": [
            {"rec": "tree", "src": "tree", "kind": "plant", "from": [20 * ft, 20 * ft, 0],
             "to": [22 * ft, 20 * ft, 0], "rot_z_deg": 0, "scale": [1, 1, 1]},
            {"rec": "mass#0", "deleted": True},
            # patio pivot at its center (20, 70); turned 90° in place
            {"rec": "patio", "src": "patio", "kind": "paving", "from": [20 * ft, 70 * ft, 0],
             "to": [20 * ft, 70 * ft, 0], "rot_z_deg": 90, "scale": [1, 1, 1]},
        ], "unpulled_parts": []}
        patches, notes = edits.patches_from_report(proj, scene, report)
        self.assertEqual(patches["tree"]["at"], [22.0, 20.0])
        self.assertEqual(patches["mass"]["adjust"][0], {"remove": True})
        poly = patches["patio"]["polygon"]
        self.assertEqual([round(v, 2) for v in poly[0]], [30.0, 60.0])  # (10,60) → (30,60)
        edits.write(proj, patches, None)
        again = project_mod.load(str(self.d))
        tree = next(e for e in resolve(again)["elements"] if e["id"] == "tree")
        self.assertAlmostEqual(tree["x"], 22 * ft, places=4)


if __name__ == "__main__":
    unittest.main()
