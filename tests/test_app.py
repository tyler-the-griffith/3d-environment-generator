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
            def submit(self, kind, node, title, fn, lane="gpu", meta=None):
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

    def test_questions_before_building(self):
        """The agent can stop to ask; answers (or 'use your best judgement') continue the same round."""
        from app.jobs import Job
        fake = self.root / "fake_agent.py"
        fake.write_text(
            "import json, sys, pathlib\n"
            "prompt, result = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])\n"
            "if 'answers to your questions' in prompt.read_text():\n"
            "    result.write_text(json.dumps({'summary': 'built with answers'}))\n"
            "else:\n"
            "    (result.parent / 'questions.json').write_text(json.dumps({'questions': [\n"
            "        {'id': 'q1', 'text': 'Lot width?', 'why': 'scale', 'default': '80 ft'},\n"
            "        'Keep the shed?']}))\n")
        base = config.load_settings
        config.load_settings = lambda: config._merge(base(), {"ai": {
            "provider": "custom", "custom_command": f"{sys.executable} {fake} {{prompt_file}} {{result_file}}"}})
        n = store.create_project("P")
        store.add_input(n, "spec", text="build it")
        rnd = ai.start_round(n, "design")
        job = Job("ai_round", n.id, "t", None, "ai")
        job.lv = lambda *a, **k: None
        ai.run_round(job, n, rnd)
        r = n.inputs()["rounds"][-1]
        self.assertEqual(r["status"], "awaiting_answers")
        self.assertEqual([q["id"] for q in r["questions"]], ["q1", "q2"])
        self.assertEqual(r["questions"][0]["default"], "80 ft")
        with self.assertRaises(ValueError):  # no new round while questions are open
            store.add_input(n, "spec", text="more")
            ai.start_round(n, "design")

        ai.answer_round(job, n, r, {"q1": "72 ft", "q2": None})
        r = n.inputs()["rounds"][-1]
        self.assertEqual(r["status"], "done")
        self.assertEqual(r["summary"], "built with answers")
        self.assertEqual(r["questions"], [])
        self.assertEqual(len(r["asked_questions"]), 2)
        answers = next(i for i in n.inputs()["items"] if i.get("answers_to") == r["n"])
        self.assertEqual(answers["round"], r["n"])  # frozen with this round
        self.assertIn("72 ft", answers["text"])
        self.assertIn("best judgement", answers["text"])

    def test_publish_theme_and_empty_site(self):
        from app import publish
        css = publish._theme_css({"light": {"bg": "#ffffff", "ink": "#000"}, "dark": {"bg": "#000000", "x;}": "#fff",
                                  "ink": "red;}body{display:none"}, "mode": "system", "radius": 8})
        self.assertIn("--bg:#ffffff", css)
        self.assertIn("prefers-color-scheme: dark", css)
        self.assertNotIn("display:none", css)   # values are validated, not pasted into the page
        self.assertNotIn("x;}", css)
        self.assertNotIn("prefers-color-scheme", publish._theme_css({"light": {"bg": "#fff"}, "mode": "light"}))
        n = store.create_project("P")
        self.assertEqual(publish.status(n)["available"], [])
        with self.assertRaises(RuntimeError):  # nothing made yet → nothing to publish
            publish.build_site(None, n, {"types": ["stills"]})

    def _design(self, n, extra=""):
        n.design_path.write_text(DESIGN + extra)

    def test_steps_recheck_when_an_earlier_step_changes(self):
        from app import steps
        n = store.create_project("P")
        self._design(n, "  - {id: shed, kind: structure, footprint: [[70,10],[80,10],[80,18],[70,18]], wall_height: 8}\n")
        d = steps.load(n)
        for k in steps.KEYS:
            d["steps"][k] = {"status": "approved", "hash": None}
        steps.save(n, d)
        steps.refresh(n)  # records hashes
        # Move the patio (a ground surface): building outlines and later steps need a recheck, earlier ones don't.
        n.design_path.write_text(n.design_path.read_text().replace("[[10,10],[30,10]", "[[12,10],[30,10]"))
        v = steps.view(n)
        flags = {s["key"]: bool(s.get("recheck")) for s in v["steps"]}
        self.assertFalse(flags["boundary"])
        self.assertFalse(flags["surfaces"])          # the changed step itself stays approved
        self.assertTrue(flags["buildings"])
        self.assertTrue(flags["plants"])
        self.assertEqual(next(s for s in v["steps"] if s["key"] == "plants")["recheck_because"], ["Surfaces"])
        # Changing only a roof (details) leaves the outline step alone.
        h0 = steps._hash(steps.content(steps.merged_spec(n), "buildings"))
        n.design_path.write_text(n.design_path.read_text().replace("wall_height: 8", "wall_height: 9"))
        self.assertEqual(steps._hash(steps.content(steps.merged_spec(n), "buildings")), h0)

    def test_step_rounds_chain_and_import(self):
        from app import steps
        n = store.create_project("P")
        self._design(n)
        store.add_input(n, "spec", text="x")
        queued = []
        steps.SUBMIT = lambda node, rnd: queued.append(rnd)
        rnd = steps.start(n, "location", new_inputs=True)
        self.assertEqual((rnd["kind"], rnd["step"], len(rnd["items"])), ("step", "location", 1))
        self.assertEqual(steps.load(n)["steps"]["location"]["status"], "working")
        ai._set_round(n, rnd["n"], status="done")
        steps.after_round(n, rnd, {"summary": "geo-referenced", "revisit": ["nonsense", "ground"]})
        st = steps.load(n)["steps"]["location"]
        self.assertEqual((st["status"], st["summary"], st["revisit"]), ("review", "geo-referenced", ["ground"]))
        nxt = steps.settle(n, "location", "approved")
        self.assertEqual(nxt, "ground")                  # approving starts the next step
        self.assertEqual(queued[-1]["step"], "ground")
        self.assertEqual(queued[-1]["items"], [])        # inputs were frozen with the first step
        # auto mode: a finished step is accepted provisionally and the chain goes on
        ai._set_round(n, queued[-1]["n"], status="done")
        steps.set_auto(n, True)
        steps.after_round(n, queued[-1], {"summary": "flat"})
        self.assertEqual(steps.load(n)["steps"]["ground"]["status"], "provisional")
        self.assertEqual(queued[-1]["step"], "boundary")
        # A design made before the steps: everything with content becomes provisional; questions move to steps.
        m = store.create_project("Q")
        self._design(m)
        store.add_input(m, "spec", text="y")
        r = ai.start_round(m, "design")
        ai._set_round(m, r["n"], status="done", questions=ai.normalize_questions(
            ["Where exactly is the west property line?", "How tall is the deck above the patio?"]))
        out = steps.import_existing(m)
        self.assertEqual(out["questions_moved"], 2)
        d = steps.load(m)["steps"]
        self.assertEqual(d["surfaces"]["status"], "provisional")
        self.assertEqual(d["inventory"]["status"], "pending")      # nothing there yet
        self.assertEqual(len(d["boundary"]["questions"]), 1)
        self.assertEqual(len(d["details"]["questions"]), 1)
        self.assertTrue(m.inputs()["rounds"][-1]["questions_answered"])

    def test_map_edits_on_a_child_variant_go_into_its_overlay(self):
        import yaml
        from landviz import sitemap
        n = store.create_project("P")
        self._design(n)
        v = store.create_variant(n, "Bigger patio")
        proj = project_mod.load(str(v.project_dir), v.variant)
        data = sitemap.editable(proj, resolve(proj))
        patio = next(e for e in data["elements"] if e["id"] == "patio")["geoms"][0]["pts"]
        patio[2] = [patio[2][0] + 3, patio[2][1] + 3]
        sitemap.apply_edits(proj, [{"id": "patio", "path": ["polygon"], "pts": patio}])
        overlay = yaml.safe_load(v.design_path.read_text())
        self.assertIn("patio", overlay["elements_override"])
        self.assertNotIn("[[10,10],[30,10],[30,30]", v.design_path.read_text())
        base = yaml.safe_load(n.design_path.read_text())
        self.assertEqual(base["elements"][0]["polygon"][2], [30, 30])   # the parent is untouched
        merged = project_mod.load(str(v.project_dir), v.variant)
        self.assertAlmostEqual(merged.pts(next(e for e in merged.spec["elements"] if e["id"] == "patio")["polygon"],
                                          "site")[2][0], 30 * 0.3048 + 3, places=2)


if __name__ == "__main__":
    unittest.main()
