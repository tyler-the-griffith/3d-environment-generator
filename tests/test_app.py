"""App rules: projects, child variants, input freezing, rounds, the output selection. No Blender,
no AI, and no touching the real settings file (projects root + settings are redirected)."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ai, config, store  # noqa: E402
from landviz import blender_bridge  # noqa: E402
from landviz import project as project_mod  # noqa: E402
from landviz.resolve import resolve  # noqa: E402

blender_bridge._VERSION = "Blender test"

DESIGN = """
project: {name: T, units: ft}
site: {boundary: [[0,0],[100,0],[100,100],[0,100]]}
elements:
  - {id: patio, kind: paving, polygon: [[10,10],[30,10],[30,30],[10,30]]}
  - {id: tree, kind: plant, species: acer_rubrum, at: [60, 60]}
"""


class AppTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._orig = (config.projects_root, config.load_settings)
        config.projects_root = lambda: self.root
        config.load_settings = lambda: config._merge(config.DEFAULTS, {"projects": {"root": str(self.root)}})

    def tearDown(self):
        config.projects_root, config.load_settings = self._orig
        shutil.rmtree(self.root)

    def test_new_project(self):
        n = store.create_project("Elm Street")
        self.assertTrue(n.design_path.exists())
        self.assertEqual(n.versions()[0]["source"], "template")
        self.assertEqual(store.tree()[0]["status"], "new")
        self.assertEqual(n.output_spec()["selected"], config.DEFAULTS["projects"]["default_outputs"])

    def test_sending_freezes_inputs(self):
        n = store.create_project("P")
        it = store.add_input(n, "spec", text="hello")
        rnd = ai.start_round(n, "design")
        self.assertEqual(rnd["items"], [it["id"]])
        with self.assertRaises(PermissionError):
            store.update_input(n, it["id"], {"text": "changed"})
        with self.assertRaises(PermissionError):
            store.delete_input(n, it["id"])
        # Nothing new → nothing to send.
        with self.assertRaises(ValueError):
            ai.start_round(n, "design")
        # A new input is a new round, stacked on top of the frozen history.
        store.add_input(n, "spec", text="more")
        self.assertEqual(ai.start_round(n, "design")["n"], 2)

    def test_variant_copies_inputs_editable(self):
        n = store.create_project("P")
        n.design_path.write_text(DESIGN)
        it = store.add_input(n, "spec", text="original")
        ai.start_round(n, "design")
        v = store.create_variant(n, "Option B")
        items = v.inputs()["items"]
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["round"])  # editable again in the variant
        self.assertEqual(items[0]["origin"]["id"], it["id"])
        store.update_input(v, items[0]["id"], {"text": "edited"})
        self.assertTrue(v.inputs()["items"][0]["edited"])
        store.delete_input(v, items[0]["id"])
        self.assertEqual(v.inputs()["removed_inherited"][0]["id"], it["id"])
        # The parent's frozen input is untouched.
        self.assertEqual(n.inputs()["items"][0]["text"], "original")

    def test_variant_overlay_inherits_parent_fixes(self):
        n = store.create_project("P")
        n.design_path.write_text(DESIGN)
        v = store.create_variant(n, "Bigger patio")
        v.design_path.write_text(v.design_path.read_text() +
                                 "elements_override:\n  patio: {polygon: [[10,10],[40,10],[40,40],[10,40]]}\n")
        def patio(proj):
            return next(e for e in resolve(proj)["elements"] if e["id"] == "patio")["polygon"]
        base = project_mod.load(str(n.project_dir))
        var = project_mod.load(str(n.project_dir), v.variant)
        self.assertNotEqual(patio(base), patio(var))
        # Fix the parent's tree position: the variant picks it up.
        n.design_path.write_text(DESIGN.replace("at: [60, 60]", "at: [70, 70]"))
        tree = next(e for e in resolve(project_mod.load(str(n.project_dir), v.variant))["elements"] if e["id"] == "tree")
        self.assertAlmostEqual(tree["x"], 70 * 0.3048)
        # Grandchild variants chain through their parent.
        g = store.create_variant(v, "Even more")
        self.assertEqual(patio(project_mod.load(str(n.project_dir), g.variant)), patio(var))

    def test_output_selection(self):
        n = store.create_project("P")
        spec = store.update_output_spec(n, {"selected": ["stills", "stills", "report"]})
        self.assertEqual(spec["selected"], ["stills", "report"])  # each type at most once
        spec = store.update_output_spec(n, {"add_custom": "a view from the kitchen"})
        cid = spec["custom"][0]["id"]
        self.assertEqual(spec["custom"][0]["status"], "pending")
        # Requests go out with the next round and can't be removed once in progress.
        store.add_input(n, "spec", text="x")
        ai.start_round(n, "design")
        spec = store.update_output_spec(n, {"remove_custom": cid})
        self.assertEqual(spec["custom"][0]["status"], "in_progress")

    def test_restart_recovery(self):
        class FakeManager:
            def __init__(self):
                self.submitted = []
            def submit(self, kind, node, title, fn, lane="gpu"):
                self.submitted.append((node, title))
        n = store.create_project("P")
        store.add_input(n, "spec", text="x")
        r1 = ai.start_round(n, "design")
        ai._set_round(n, r1["n"], status="running", agent_pid=None)
        store.add_input(n, "spec", text="y")
        r2 = ai.start_round(n, "design")
        ai._set_round(n, r2["n"], status="running", agent_pid=None)
        (ai._round_dir(n, r2["n"]) / "result.json").write_text('{"summary": "done while closed"}')
        mgr = FakeManager()
        ai.recover_rounds(mgr)
        rounds = {r["n"]: r for r in n.inputs()["rounds"]}
        self.assertEqual(rounds[r1["n"]]["status"], "interrupted")       # nothing to recover → Retry
        self.assertEqual([t for _, t in mgr.submitted], [f"Finish round {r2['n']} — P"])  # result exists → finish

    def test_brief_mentions_inputs_and_rules(self):
        n = store.create_project("P")
        store.add_input(n, "spec", text="make the patio bigger")
        store.update_output_spec(n, {"add_custom": "a sunrise view"})
        rnd = ai.start_round(n, "design")
        brief = ai.build_brief(n, rnd)
        self.assertIn("make the patio bigger", brief)
        self.assertIn("a sunrise view", brief)
        self.assertIn(str(n.design_path), brief)
        self.assertIn("exactly as written", brief)


if __name__ == "__main__":
    unittest.main()
