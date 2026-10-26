"""AI rounds: turn a node's inputs into its design file.

A round = (freeze the new inputs) → write a task brief → run the configured AI agent in the
repo → read its result → validate → snapshot the design version → render the plan check.

Providers are interchangeable command-line agents (or the Anthropic API via a small tool loop
in api_agent.py). They all receive the same brief and must write the same two files: the
design file and the result JSON.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .. import config, store
from ..jobs import BUS, Job

HERE = Path(__file__).resolve().parent

PROVIDERS = {
    "claude_code": {"label": "Claude Code", "kind": "cli", "exe": "claude",
                    "explain": "Uses your Claude subscription through the Claude Code CLI. It can run the "
                               "framework's checks and look at the plan-check image to fix its own mistakes."},
    "codex": {"label": "OpenAI Codex CLI", "kind": "cli", "exe": "codex",
              "explain": "Uses your ChatGPT/OpenAI subscription through the Codex CLI (sandboxed workspace-write)."},
    "gemini": {"label": "Google Gemini CLI", "kind": "cli", "exe": "gemini",
               "explain": "Uses your Google account through the Gemini CLI. In this mode it can edit files but "
                          "may not be allowed to run the checks."},
    "anthropic_api": {"label": "Anthropic API (API key)", "kind": "api",
                      "explain": "Calls Claude directly with an API key (billed per use). Needs the `anthropic` "
                                 "Python package. Can validate the design, but can't look at the rendered check image."},
    "custom": {"label": "Custom command", "kind": "custom",
               "explain": "Any agent you can run from a terminal. Placeholders: {prompt_file} {prompt} {repo} "
                          "{project_dir} {design_file} {result_file}."},
}


def provider_status() -> dict:
    s = config.load_settings()["ai"]
    out = {}
    for key, p in PROVIDERS.items():
        if p["kind"] == "cli":
            ok = shutil.which(p["exe"]) is not None
            why = "installed" if ok else f"`{p['exe']}` not found on PATH"
        elif p["kind"] == "api":
            has_pkg = importlib.util.find_spec("anthropic") is not None
            has_key = bool(s.get("api_key") or os.environ.get("ANTHROPIC_API_KEY"))
            ok = has_pkg and has_key
            why = "ready" if ok else ("install with: pip install anthropic" if not has_pkg else "no API key set")
        else:
            ok = bool(s.get("custom_command"))
            why = "configured" if ok else "no command set"
        out[key] = {**p, "available": ok, "status": why}
    return out


def display_name() -> str:
    """'Claude' for Claude providers, otherwise the provider's label — used on the Send button."""
    p = config.load_settings()["ai"]["provider"]
    return "Claude" if p in ("claude_code", "anthropic_api") else PROVIDERS.get(p, {}).get("label", "AI")


# ---------------------------------------------------------------- rounds


def start_round(node: store.Node, kind: str = "design") -> dict:
    """Freeze new inputs (design rounds) and record a new round. Returns the round record."""
    with store.LOCK:
        d = node.inputs()
        spec = node.output_spec()
        if any(r["status"] == "awaiting_answers" for r in d["rounds"]):
            raise ValueError("answer the open questions first (or let the AI use its best judgement)")
        n = (d["rounds"][-1]["n"] + 1) if d["rounds"] else 1
        new_items = [it for it in d["items"] if it.get("round") is None] if kind == "design" else []
        pending = [c for c in spec["custom"] if c["status"] == "pending"]
        if kind == "design" and not new_items and not pending and d["rounds"]:
            raise ValueError("nothing new to send — add an input or an output request first")
        if kind == "design" and not d["items"]:
            raise ValueError("add at least one input before sending")
        if kind == "requests" and not pending:
            raise ValueError("no pending output requests")
        for it in new_items:
            it["round"] = n
        for c in pending:
            c["status"], c["round"] = "in_progress", n
        rnd = {"n": n, "kind": kind, "phase": "ask", "status": "queued", "provider": config.load_settings()["ai"]["provider"],
               "created": store._now(), "items": [it["id"] for it in new_items],
               "requests": [c["id"] for c in pending], "summary": "", "notes": [], "questions": []}
        d["rounds"].append(rnd)
        node.save_inputs(d)
        node.save_output_spec(spec)
        return rnd


def _set_round(node: store.Node, n: int, **kw):
    with store.LOCK:
        d = node.inputs()
        for r in d["rounds"]:
            if r["n"] == n:
                r.update(kw)
        node.save_inputs(d)
    BUS.publish("node_changed", {"node": node.id})


def _round_dir(node: store.Node, n: int) -> Path:
    d = node.app_dir / "rounds" / str(n)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _describe_item(node: store.Node, it: dict) -> str:
    t = store.INPUT_TYPES.get(it["type"], {}).get("label", it["type"])
    lines = [f"- **{t}** — {it.get('title') or ''}".rstrip(" —")]
    if it.get("origin"):
        lines[0] += "  _(inherited from the parent" + (", edited in this variant)" if it.get("edited") else ")") + "_"
    if it.get("text"):
        lines.append("  > " + it["text"].strip().replace("\n", "\n  > "))
    for f in it.get("files", []):
        lines.append(f"  - file: `{store.file_path(node, f)}`")
    if it.get("folder"):
        lines.append(f"  - folder: `{it['folder']}`")
    return "\n".join(lines)


def _lidar_items(node):
    return [it for it in node.inputs()["items"] if it["type"] == "lidar"]


def _prefetch_lidar(job: Job, node: store.Node) -> str:
    """If the homeowner allowed public LiDAR, fetch it before the AI starts (deterministic, cached
    per requested location). Returns a note for the brief."""
    items = _lidar_items(node)
    if not items:
        return ""
    from landviz import photos as ph
    text = (items[-1].get("text") or "").strip()
    args = []
    if text:
        ll = ph.parse_latlon(text)
        args = ["--latlon", f"{ll[0]}, {ll[1]}"] if ll else ["--address", text]
    meta_file = node.project_dir / "derived" / "lidar" / "lidar.json"
    want = json.dumps(args)
    if meta_file.exists():
        try:
            if json.loads(meta_file.read_text()).get("request") == want:
                return "LiDAR data for this site is already in the project (derived/lidar/)."
        except json.JSONDecodeError:
            pass
    job.set_step("Fetching public LiDAR / elevation data (USGS)", 0.03)
    try:
        job.lv("lidar", "fetch", str(node.project_dir), *args)
        meta = json.loads(meta_file.read_text())
        meta["request"] = want
        meta_file.write_text(json.dumps(meta, indent=1))
        pc = meta.get("point_cloud")
        return ("LiDAR data was fetched for this site (derived/lidar/): measured bare-earth terrain"
                + (f" and a point cloud ({pc['dataset']}) with roof and tree heights" if pc else
                   f"; no point cloud ({meta.get('point_cloud_error', 'unavailable')})") + ".")
    except Exception as e:  # noqa: BLE001 — the round still runs without it
        return (f"Fetching LiDAR data failed ({e}). If you can determine the location from the inputs, run "
                f"`./lv lidar fetch {shlex.quote(str(node.project_dir))} --latlon \"LAT, LON\"` yourself.")


def build_brief(node: store.Node, rnd: dict, lidar_note: str = "") -> str:
    d = node.inputs()
    spec = node.output_spec()
    meta = node.meta()
    rdir = _round_dir(node, rnd["n"])
    result_file = rdir / "result.json"
    lv_args = " ".join(shlex.quote(a) for a in node.lv_args())
    from ..generate import OUTPUT_TYPES

    parts = [(HERE / "instructions.md").read_text(), "\n---\n# Task\n"]
    if node.variant:
        parent = store.Node(meta["parent"]) if meta.get("parent") else None
        parts.append(
            f"This is the child variant **{meta['title']}** (`{node.variant}`) of "
            f"**{parent.meta()['title'] if parent else 'the base design'}**.\n\n"
            f"The variant's design file `{node.design_path}` is an **overlay** on its parent: keep "
            f"`parent: {store._yaml(node.design_path).get('parent', 'base')}` "
            "and express only the differences, using `elements_add`, `elements_remove` (ids), "
            "`elements_override` ({id: partial element}), and any top-level keys to change (deep-merged). "
            "That way fixes to the parent keep flowing into this variant.\n\n"
            f"The parent's full design: `{parent.design_path if parent else node.project_dir / 'project.yaml'}`"
            f"{' (itself an overlay; run ./lv validate on it to see the result)' if parent and parent.variant else ''}.\n")
    else:
        parts.append(f"Project **{meta['title']}**. Design file: `{node.design_path}`.\n")
    vs = node.versions()
    if vs and vs[-1]["source"] not in ("template", "branch"):
        parts.append(f"\nThe design file already exists (version {vs[-1]['k']}, {vs[-1]['source']}). "
                     "**Revise it** for the new inputs; don't start over.\n")
    elif not node.variant:
        parts.append("\nThe design file is still the starting skeleton: write it from the inputs.\n")

    if rnd["kind"] == "design":
        sent = [it for it in d["items"] if it.get("round") not in (None, rnd["n"])]
        new = [it for it in d["items"] if it.get("round") == rnd["n"]]
        if sent:
            parts.append("\n## Inputs from earlier rounds (already reflected in the design)\n")
            for r in d["rounds"]:
                its = [it for it in sent if it.get("round") == r["n"]]
                if its:
                    parts.append(f"\n### Round {r['n']} ({r['created'][:10]})\n" + "\n".join(_describe_item(node, i) for i in its))
        parts.append("\n## New inputs in this round\n" + ("\n".join(_describe_item(node, i) for i in new) or "_none_"))
        if d.get("removed_inherited"):
            parts.append("\n\nInputs the homeowner **removed** from this variant (they no longer apply): "
                         + ", ".join(f"{o['node']}#{o['id']}" for o in d["removed_inherited"]))
    else:
        parts.append("\nThis round has no new inputs: it's about the output requests below.\n")

    sel = [t for t in OUTPUT_TYPES if t["key"] in spec["selected"]]
    parts.append("\n## Outputs the homeowner wants\n" + "\n".join(f"- {t['label']}: {t['needs']}" for t in sel))
    pending = [c for c in spec["custom"] if c.get("round") == rnd["n"]]
    if pending:
        parts.append("\n\n## Custom output requests: decide each one\n"
                     "Make each request possible by adding what the design needs (usually a camera view: "
                     "presets eye, aerial, top, pano, orbit, flythrough) and mark it `accepted`, explaining what "
                     "you added and which output type will produce it. If it can't be done with these "
                     "capabilities, or isn't sensible, mark it `declined` with a short, friendly reason.\n"
                     + "\n".join(f"- `{c['id']}`: {c['text']}" for c in pending))
    if lidar_note or _lidar_items(node):
        pq = shlex.quote(str(node.project_dir))
        parts.append("\n\n## Public LiDAR data (the homeowner allowed it)\n" + (lidar_note or "") +
                     "\nUse it, it's measured rather than estimated:\n"
                     "- Geo-reference the site first: `site.location.origin` = lat/lon of site (0, 0). With a "
                     f"straight-down plan photo, `./lv photos <photo> --project {pq} --anchor <photo> --ground <ground photo>` "
                     "prints it, plus a scale check of your plan calibration.\n"
                     f"- `./lv lidar info {pq}`; `./lv lidar image {pq}` writes derived/lidar/height-map.png: a true-scale "
                     "top-down height map in site coordinates (no photo lean). Read building outlines, ridge lines and "
                     "tree crowns from it.\n"
                     f"- `./lv lidar heights {pq} --at X,Y --at X,Y …` gives ground elevation and height above ground "
                     "(eaves, ridges, treetops) at site points.\n"
                     "- Use `site.terrain: {type: lidar}` for the ground shape.\n")
    parts.append(f"\n\n## Files you may write\n- `{node.design_path}`\n- `{node.project_dir / 'species.yaml'}`"
                 f" (only to add missing plants)\n- `{result_file}`\n")
    has_photos = any(it["type"] in ("drone", "photos") for it in d["items"])
    if has_photos:
        parts.append("\n## Photo-match check\nThere are photos with GPS among the inputs. Set up `photo_matches` in "
                     "the design (see docs/SPEC.md): the model is then rendered from each photo's exact camera and "
                     "compared with it. Use the straight-down photo and two or three angled ones. If the files have "
                     "no gimbal data, estimate each photo's heading (yaw, compass degrees) and tilt (pitch) and refine "
                     "them until the street and property lines line up, then fix the *model* until the rest does. "
                     f"Run `./lv photomatch {lv_args}` and look at each sheet in renders/<state>/photo-match/.\n")
    parts.append(f"\n## Commands\nRun these **exactly as written**, from the repository root (the working directory). "
                 "Don't prefix them with `cd` or chain them with `;`/`&&`: other command shapes are blocked.\n"
                 f"- `./lv validate {lv_args}`\n- `./lv check {lv_args}`  (then open the image it prints with your Read tool)\n"
                 f"- `./lv species <text>`\n- `./lv photos <files or folder> --project {shlex.quote(str(node.project_dir))}`"
                 f"\n- `./lv photomatch {lv_args}`\n- `./lv plan import {shlex.quote(str(node.project_dir))} <pdf>`  "
                 "(rasterizes a PDF plan into the project's plan/ folder)\n"
                 "\nView images and PDFs with your Read tool, not with scripts. Input files live under the project's "
                 "`app/files/` folder; in the design file, reference them by path relative to the project folder "
                 "(e.g. `plan: {image: app/files/<id>/plan.png}`).\n")
    answered = any(it.get("answers_to") == rnd["n"] for it in node.inputs()["items"])
    if rnd.get("phase", "ask") == "ask" and not answered:
        parts.append(f"\nIf you need answers before building, write `{rdir / 'questions.json'}` and stop "
                     "(see \"Ask before you build\").")
    parts.append(f"\nWhen done, write `{result_file}` as described above.\n")
    return "".join(parts)


def run_round(job: Job, node: store.Node, rnd: dict):
    n = rnd["n"]
    rdir = _round_dir(node, n)
    brief = build_brief(node, rnd)
    prompt_file = rdir / "prompt.md"
    prompt_file.write_text(brief)
    result_file = rdir / "result.json"
    result_file.unlink(missing_ok=True)
    (rdir / "questions.json").unlink(missing_ok=True)
    settings = config.load_settings()["ai"]
    provider = settings["provider"]
    _set_round(node, n, status="running", started=store._now(), provider=provider)
    lidar_note = _prefetch_lidar(job, node)
    if lidar_note:
        brief = build_brief(node, rnd, lidar_note)
        prompt_file.write_text(brief)
    # Kept so a round can still be finished after an app restart (see recover_rounds).
    (rdir / "design-before.yaml").write_text(node.design_path.read_text() if node.design_path.exists() else "")
    log_path = rdir / "agent.log"
    short = (f"Read the task file {prompt_file} and do exactly what it says. "
             f"Work in this repository. Finish by writing {result_file}.")
    try:
        job.set_step(f"{PROVIDERS[provider]['label']} is working on round {n}", 0.05)
        with open(log_path, "w") as log:
            def on_line(line, _log=log):
                _log.write(line)
                _log.flush()
                if provider == "claude_code" and '"subtype":"init"' in line.replace(" ", ""):
                    try:
                        sid = json.loads(line).get("session_id")
                        if sid:
                            _set_round(node, n, session_id=sid)
                    except json.JSONDecodeError:
                        pass
                return _pretty(provider, line)
            _run_provider(job, provider, settings, node, prompt_file, result_file, short, on_line,
                          on_start=lambda pid: _set_round(node, n, agent_pid=pid))
    except Exception as e:
        _set_round(node, n, status="failed", finished=store._now(), error=str(e))
        _finish_requests(node, n, {}, failed=True)
        raise
    questions = _read_questions(rdir / "questions.json")
    if questions and not result_file.exists():
        # The agent wants answers before it builds: park the round until the homeowner replies.
        _set_round(node, n, status="awaiting_answers", questions=questions, agent_pid=None,
                   asked=store._now())
        job.set_step(f"{display_name()} has {len(questions)} question(s) before building", 1.0)
        return {"round": n, "questions": len(questions)}
    return finish_round(job, node, rnd)


def _read_questions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    qs = data.get("questions", data) if isinstance(data, dict) else data
    return normalize_questions(qs)


def normalize_questions(qs) -> list[dict]:
    out = []
    for i, q in enumerate(qs or []):
        if isinstance(q, str):
            q = {"text": q}
        if not isinstance(q, dict) or not str(q.get("text", "")).strip():
            continue
        out.append({"id": str(q.get("id") or f"q{i + 1}"), "text": str(q["text"]).strip(),
                    "why": str(q.get("why") or "").strip(), "default": str(q.get("default") or "").strip()})
    return out


def format_answers(questions: list[dict], answers: dict) -> str:
    """Q&A as plain text — stored as a frozen input and sent to the agent."""
    lines = []
    for q in questions:
        a = answers.get(q["id"])
        if a is None or not str(a).strip():
            a = "Use your best judgement" + (f" (your stated default: {q['default']})" if q.get("default") else "") + "."
        lines.append(f"Q: {q['text']}\nA: {str(a).strip()}")
    return "\n\n".join(lines)


def answer_round(job: Job, node: store.Node, rnd: dict, answers: dict):
    """Continue a round that stopped to ask questions: record the answers as a frozen input of that
    round, then let the agent build (Claude Code resumes the same session)."""
    n = rnd["n"]
    rdir = _round_dir(node, n)
    text = format_answers(rnd.get("questions", []), answers)
    with store.LOCK:
        d = node.inputs()
        import uuid as _uuid
        d["items"].append({"id": _uuid.uuid4().hex[:10], "type": "spec", "title": f"Answers to {display_name()}'s questions",
                           "text": text, "folder": "", "files": [], "created": store._now(), "round": n,
                           "answers_to": n})
        node.save_inputs(d)
    (rdir / "answers.txt").write_text(text)
    settings = config.load_settings()["ai"]
    provider = rnd.get("provider") or settings["provider"]
    result_file = rdir / "result.json"
    message = ("Here are the homeowner's answers to your questions. Now build the design as described in the "
               f"task file, verify it, and write {result_file}. Don't ask further questions this round.\n\n" + text)
    sid = session_id_for(node, rnd) if provider == "claude_code" else None
    if sid:
        prompt_file, short = None, message
    else:  # providers without session resume get the whole brief again, plus the answers
        prompt_file = rdir / "prompt-answers.md"
        prompt_file.write_text((rdir / "prompt.md").read_text() + "\n\n## The homeowner's answers to your questions\n\n"
                               + text + "\n\nBuild now; don't ask further questions this round.\n")
        short = (f"Read the task file {prompt_file} and do exactly what it says. Work in this repository. "
                 f"Finish by writing {result_file}.")
    # Keep what was asked separately: `questions` is reused for anything that comes up while building.
    _set_round(node, n, status="running", answered=store._now(), asked_questions=rnd.get("questions", []),
               questions=[], answers=answers)
    try:
        job.set_step(f"{PROVIDERS[provider]['label']} is building round {n} with your answers", 0.1)
        with open(rdir / "agent.log", "a") as log:
            def on_line(line, _log=log):
                _log.write(line)
                _log.flush()
                return _pretty(provider, line)
            _run_provider(job, provider, settings, node, prompt_file or (rdir / "prompt.md"), result_file, short,
                          on_line, on_start=lambda pid: _set_round(node, n, agent_pid=pid), resume=sid)
    except Exception as e:
        _set_round(node, n, status="failed", finished=store._now(), error=str(e))
        _finish_requests(node, n, {}, failed=True)
        raise
    return finish_round(job, node, rnd)


def finish_round(job: Job, node: store.Node, rnd: dict):
    """Everything after the agent exits: read its result, version the design, validate, plan check."""
    n = rnd["n"]
    rdir = _round_dir(node, n)
    result_file = rdir / "result.json"
    before_file = rdir / "design-before.yaml"
    before = before_file.read_text() if before_file.exists() else None
    result = {}
    if result_file.exists():
        try:
            result = json.loads(result_file.read_text())
        except json.JSONDecodeError:
            result = {"notes": ["The AI wrote a result file that isn't valid JSON."]}
    else:
        result = {"notes": ["The AI finished without writing its summary file."]}
    current = node.design_path.read_text() if node.design_path.exists() else ""
    changed = before is None or current != before
    v = node.snapshot_design("ai", f"round {n}", n) if changed else None
    job.set_step("Validating the design", 0.8)
    issues = validate(node)
    check = None
    if not any(i["level"] == "error" for i in issues):
        job.set_step("Rendering the plan check from the model", 0.85)
        try:
            job.lv("check", *node.lv_args(), "--output-type", "plan_check")
            check = f"{node.state}/plan-check.png"
        except Exception as e:  # noqa: BLE001
            job.say(f"plan check failed: {e}")
        if "photo_matches" in (node.design_path.read_text() if node.design_path.exists() else ""):
            job.set_step("Rendering the photo-match check from the model", 0.92)
            try:
                job.lv("photomatch", *node.lv_args(), "--output-type", "photo_match")
            except Exception as e:  # noqa: BLE001
                job.say(f"photo match failed: {e}")
    _finish_requests(node, n, result)
    _set_round(node, n, status="done", finished=store._now(), summary=result.get("summary", ""),
               notes=result.get("notes", []), questions=normalize_questions(result.get("questions", [])),
               design_version=v["k"] if v else None, design_changed=changed, issues=issues, check_image=check,
               agent_pid=None)
    return {"round": n, "changed": changed}


def _pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def recover_rounds(manager) -> list[str]:
    """At app start: rounds left 'running' by a previous app session (window closed mid-round).

    - the AI is still running (it outlives the app) → wait for it, then finish the round
    - the AI finished and wrote its result        → finish the round now
    - neither                                      → mark it interrupted (Retry is offered)
    """
    import time as _time
    notes = []

    def visit(items):
        for item in items:
            yield item["id"]
            yield from visit(item.get("children", []))

    for tree_node in store.tree():
        for nid in visit([tree_node]):
            node = store.Node(nid)
            for rnd in node.inputs()["rounds"]:
                if rnd["status"] not in ("queued", "running"):
                    continue
                result_exists = (_round_dir(node, rnd["n"]) / "result.json").exists()
                if _pid_alive(rnd.get("agent_pid")):
                    def wait_then_finish(job, node=node, rnd=rnd):
                        job.set_step(f"Waiting for the AI to finish round {rnd['n']} (started before a restart)")
                        while _pid_alive(rnd.get("agent_pid")):
                            if job.cancel_requested:
                                return None
                            _time.sleep(3)
                        return finish_round(job, node, rnd)
                    manager.submit("ai_round", node.id, f"Resume round {rnd['n']} — {node.meta()['title']}",
                                   wait_then_finish, lane="ai")
                    notes.append(f"{nid} round {rnd['n']}: waiting for the running AI")
                elif result_exists and rnd["status"] == "running":
                    manager.submit("ai_round", node.id, f"Finish round {rnd['n']} — {node.meta()['title']}",
                                   lambda job, node=node, rnd=rnd: finish_round(job, node, rnd), lane="ai")
                    notes.append(f"{nid} round {rnd['n']}: finishing")
                else:
                    _set_round(node, rnd["n"], status="interrupted", finished=store._now(),
                               error="The app was closed before this round finished. Retry to run it again.")
                    _finish_requests(node, rnd["n"], {}, failed=True)
                    notes.append(f"{nid} round {rnd['n']}: interrupted")
    return notes


def session_id_for(node: store.Node, rnd: dict) -> str | None:
    """The Claude Code session a round ran in (recorded live, or found in its saved transcripts)."""
    if rnd.get("session_id"):
        return rnd["session_id"]
    if rnd.get("provider") != "claude_code":
        return None
    import re as _re
    tdir = Path.home() / ".claude" / "projects" / _re.sub(r"[^A-Za-z0-9]", "-", str(config.REPO_ROOT))
    marker = str(_round_dir(node, rnd["n"]) / "prompt.md")
    for f in sorted(tdir.glob("*.jsonl"), key=lambda p: -p.stat().st_mtime) if tdir.exists() else []:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if marker in line:
                        _set_round(node, rnd["n"], session_id=f.stem)
                        return f.stem
                    if i > 30:  # the brief is in the first few lines of a round's transcript
                        break
        except OSError:
            continue
    return None


def continue_in_terminal(node: store.Node, rnd: dict) -> str:
    """Open a terminal resumed into the round's Claude Code session."""
    sid = session_id_for(node, rnd)
    if not sid:
        raise RuntimeError("couldn't find this round's Claude Code session")
    claude = shutil.which("claude") or "claude"
    cmd = [claude, "--resume", sid]
    repo = str(config.REPO_ROOT)
    if sys.platform == "darwin":
        script = f'tell application "Terminal" to do script "cd {shlex.quote(repo)} && {shlex.join(cmd)}"'
        subprocess.Popen(["osascript", "-e", script, "-e", 'tell application "Terminal" to activate'])
    elif sys.platform.startswith("win"):
        subprocess.Popen(["cmd", "/c", "start", "Claude Code", "cmd", "/k", f'cd /d "{repo}" && {subprocess.list2cmdline(cmd)}'])
    else:
        term = None
        for cand in (os.environ.get("TERMINAL"), "xdg-terminal-exec", "x-terminal-emulator", "alacritty", "kitty",
                     "foot", "ghostty", "gnome-terminal", "konsole", "xterm"):
            if cand and shutil.which(cand.split()[0]):
                term = cand
                break
        if not term:
            raise RuntimeError("no terminal emulator found")
        args = {"gnome-terminal": ["--"], "konsole": ["-e"], "xterm": ["-e"], "alacritty": ["-e"], "kitty": [],
                "foot": [], "ghostty": ["-e"]}.get(Path(term.split()[0]).name, [])
        subprocess.Popen([*term.split(), *args, *cmd], cwd=repo, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sid


def _finish_requests(node, n, result, failed=False):
    with store.LOCK:
        spec = node.output_spec()
        verdicts = {r.get("id"): r for r in result.get("requests", []) if isinstance(r, dict)}
        for c in spec["custom"]:
            if c.get("round") != n:
                continue
            if failed:
                c["status"], c["message"] = "pending", "The AI round failed; this request will be sent again."
            elif c["id"] in verdicts:
                c["status"] = "accepted" if verdicts[c["id"]].get("status") == "accepted" else "declined"
                c["message"] = verdicts[c["id"]].get("message", "")
            else:
                c["status"], c["message"] = "declined", "The AI didn't respond to this request."
        node.save_output_spec(spec)


def validate(node: store.Node) -> list[dict]:
    from landviz import project as project_mod
    from landviz.resolve import resolve
    try:
        proj = project_mod.load(str(node.project_dir), node.variant)
        resolve(proj)
        return [vars(i) for i in proj.issues]
    except Exception as e:  # noqa: BLE001
        return [{"level": "error", "where": node.design_name, "message": str(e)}]


# ---------------------------------------------------------------- providers


def _run_provider(job, provider, s, node, prompt_file, result_file, short, on_line, on_start=None, resume=None):
    repo = config.REPO_ROOT
    model = (s.get("model") or "").strip()
    if provider == "claude_code":
        cmd = [shutil.which("claude") or "claude", "-p", short, *(["--resume", resume] if resume else []),
               "--output-format", "stream-json", "--verbose",
               "--permission-mode", "acceptEdits",
               "--allowedTools", "Read", "Write", "Edit", "Glob", "Grep",
               "Bash(./lv validate:*)", "Bash(./lv check:*)", "Bash(./lv species:*)", "Bash(./lv preview:*)",
               "Bash(./lv plan import:*)", "Bash(./lv --help:*)", "Bash(./lv plan --help:*)",
               "Bash(./lv photos:*)", "Bash(./lv photomatch:*)", "Bash(./lv lidar fetch:*)", "Bash(./lv lidar info:*)",
               "Bash(./lv lidar heights:*)", "Bash(./lv lidar image:*)",
               "Bash(ls:*)", "Bash(file:*)", "Bash(pdfinfo:*)", "Bash(magick identify:*)",
               "--add-dir", str(node.project_dir)]
        if model:
            cmd += ["--model", model]
        job.run(cmd, cwd=repo, on_line=on_line, on_start=on_start)
    elif provider == "codex":
        cmd = [shutil.which("codex") or "codex", "exec", "-s", "workspace-write", "--skip-git-repo-check",
               "-C", str(repo), short]
        if model:
            cmd += ["-m", model]
        job.run(cmd, cwd=repo, on_line=on_line, on_start=on_start)
    elif provider == "gemini":
        cmd = [shutil.which("gemini") or "gemini", "-p", short, "--approval-mode", "auto_edit",
               "--include-directories", str(node.project_dir)]
        if model:
            cmd += ["-m", model]
        job.run(cmd, cwd=repo, on_line=on_line, on_start=on_start)
    elif provider == "anthropic_api":
        env = {}
        if s.get("api_key") and not os.environ.get("ANTHROPIC_API_KEY"):
            env["ANTHROPIC_API_KEY"] = s["api_key"]
        cmd = [sys.executable, "-m", "app.ai.api_agent", "--prompt-file", str(prompt_file),
               "--result-file", str(result_file), "--design-file", str(node.design_path),
               "--project-dir", str(node.project_dir), "--effort", s.get("effort", "high")]
        if model:
            cmd += ["--model", model]
        job.run(cmd, cwd=repo, env=env, on_line=on_line, on_start=on_start)
    elif provider == "custom":
        tmpl = s.get("custom_command") or ""
        if not tmpl.strip():
            raise RuntimeError("no custom AI command configured (Settings → AI)")
        fields = {"prompt_file": str(prompt_file), "prompt": short, "repo": str(repo),
                  "project_dir": str(node.project_dir), "design_file": str(node.design_path),
                  "result_file": str(result_file)}
        cmd = [part.format(**fields) for part in shlex.split(tmpl)]
        job.run(cmd, cwd=repo, on_line=on_line, on_start=on_start)
    else:
        raise RuntimeError(f"unknown AI provider {provider!r}")


def _pretty(provider: str, line: str) -> str | None:
    """Turn an agent's raw output line into something readable for the progress log."""
    if provider != "claude_code":
        return line.rstrip()[:400] or None
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return line.rstrip()[:400] or None
    t = ev.get("type")
    if t == "assistant":
        out = []
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                out.append(block["text"].strip())
            elif block.get("type") == "tool_use":
                inp = block.get("input", {})
                what = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                out.append(f"→ {block.get('name')} {str(what)[:160]}")
        return "\n".join(out)[:1200] or None
    if t == "result":
        cost = ev.get("total_cost_usd")
        return f"finished in {ev.get('duration_ms', 0) / 1000:.0f}s" + (f" (${cost:.2f})" if cost else "")
    return None

