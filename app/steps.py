"""The build steps: how a project's existing environment is worked out, one kind of thing at a time.

A project's base design is the property as it exists today. Instead of one AI round that writes
everything at once, it's built in a fixed order. Each step works out one kind of thing in isolation
and pauses for the homeowner to approve it, adjust it on the map, or let the AI use its best
judgement for now:

  location → ground → boundary → inventory → surfaces → buildings → details → fixtures → plants

Each step owns a part of the design file (its "content"). A hash of that content is kept per step,
so changes are noticed however they happen (map editor, AI, hand edits). When an approved step's
content changes, every later step that was already approved is flagged for a recheck (it may have
been built on the old version). Nothing is wiped.

Child variants (renovation ideas, the landscaper's proposal) don't use the steps: they describe
changes to their parent in one round.

State: <node app dir>/pipeline.json
"""
from __future__ import annotations

import hashlib
import json
import re

from . import store

STEPS = [
    {"key": "location", "label": "Location & orientation", "short": "Location",
     "explain": "Where the property is and which way is north, so measured data (aerial photo, LiDAR, photo GPS) "
                "lines up with the model.",
     "task": "Geo-reference the site. Set `site.location` (lat, lon, utc_offset) and `site.location.origin` (the "
             "lat/lon of site (0, 0)) and `north_offset_deg`. If the aerial photo exists, choose the origin so the "
             "property sits near (0, 0) and keep north_offset_deg 0 unless there's a reason to rotate; then points "
             "can be authored with `frame: aerial` (aerial-photo pixels). If the main tracing image is a straight-down "
             "drone photo, calibrate it as `plan` (`lv photos --anchor`). Set up `photo_matches` for photos with GPS. "
             "Don't add elements yet.",
     "check": "map"},
    {"key": "ground", "label": "Ground elevation", "short": "Ground",
     "explain": "The shape of the ground: slopes, berms, the height of the yard relative to the street.",
     "task": "Set `site.terrain`. Prefer `type: lidar` when LiDAR data exists (measured). Otherwise `type: graded` "
             "with elevation points read from measurements and photos (steps, retaining walls, curb heights), or "
             "`type: flat` if the lot really is flat. Report the overall relief in your summary.",
     "check": "map:lidar"},
    {"key": "boundary", "label": "Property boundary", "short": "Boundary",
     "explain": "The property lines, or the area of interest to model. Everything else is placed inside it.",
     "task": "Set `site.boundary`: the property line polygon (or the area of interest, if the homeowner only wants "
             "part of the lot). Use surveys or plats if given; otherwise sidewalk edges, fences, hedges, driveways "
             "and neighbors' structures on the aerial photo. Say how sure you are about each side.",
     "check": "map"},
    {"key": "inventory", "label": "Feature inventory", "short": "Features",
     "explain": "A labeled list of everything on the property, roughly outlined: house, garage, sheds, deck, patio, "
                "driveway, walkways, courts, fences, walls, trees, beds, boulders. Later steps model each one.",
     "task": "Write a top-level `inventory:` list (no elements in this step). One entry per feature: "
             "`{id, category, label, polygon: [...]}` (or `at:` for small things) with a rough outline in any frame "
             "(add `frame:`), plus `note:` when something is uncertain. Categories: house, garage, shed, porch, "
             "addition, deck, patio, driveway, walkway, court, pool, play, fence, wall, retaining_wall, lawn, bed, "
             "gravel, tree, shrub, hedge, boulder, utility, other. Decide what is part of the house (an attached "
             "garage, a bump-out, a covered porch) versus a separate structure, and ask when you can't tell.",
     "check": "map:inventory"},
    {"key": "surfaces", "label": "Ground surfaces", "short": "Surfaces",
     "explain": "What covers the ground and where: lawn, beds, gravel, patios, driveways, walkways, courts — "
                "with their real outlines and dimensions.",
     "task": "Add or correct `lawn`, `bed`, `paving` and `path` elements for every surface in the inventory, with "
             "outlines traced on the straightest-down image available and materials from the photos. Give dimensions "
             "in your notes for the main surfaces (driveway width, patio size).",
     "kinds": ["lawn", "bed", "paving", "path"], "check": "map"},
    {"key": "buildings", "label": "Building outlines", "short": "Buildings",
     "explain": "Where the walls of each building meet the ground, and how the house breaks into wings. "
                "Roof shapes come next.",
     "task": "Add or correct `structure` footprints and `wings` (where walls meet the ground: correct for roof "
             "overhang and for photo lean; a LiDAR height map shows roofs without lean) and `deck` outlines. Decide "
             "the wing breakdown (main block, garage, bump-outs). Only set heights/roofs if the element needs them to "
             "be valid; details are the next step.",
     "kinds": ["structure", "deck"], "fields": "geometry", "check": "map"},
    {"key": "details", "label": "Building details", "short": "Details",
     "explain": "Heights, roof shapes and pitches, garage doors, front doors, windows, exterior stairs, deck "
                "heights and railings, colors.",
     "task": "Fill in each structure's wall heights, roofs (type, pitch, ridge, overhang), floor raise, colors, "
             "doors, garage doors and windows, and each deck's height, railings and stairs. Use LiDAR heights when "
             "available and the photos for everything else. Check with the photo match.",
     "kinds": ["structure", "deck", "mesh"], "fields": "details", "check": "photomatch"},
    {"key": "fixtures", "label": "Fences, walls & fixtures", "short": "Fences & walls",
     "explain": "Fences, garden and retaining walls, posts, and outdoor lights.",
     "task": "Add or correct `fence`, `wall` and `light` elements: lines, heights, styles, materials.",
     "kinds": ["fence", "wall", "light"], "check": "map"},
    {"key": "plants", "label": "Plants, trees & boulders", "short": "Plants",
     "explain": "Every tree, shrub, hedge, planting and boulder: position, size and species.",
     "task": "Add or correct `plant`, `plant_group`, `plant_row` and `boulder` elements. Existing trees get "
             "`existing: true` and measured heights/spreads (LiDAR heights where available, crowns from the aerial "
             "photo). Identify species from the photos; use a sensible look-alike from the catalog when unsure and "
             "say so. This is the last step: also set up the camera `views` the homeowner's outputs need (listed "
             "below) and finish with `./lv check`.",
     "kinds": ["plant", "plant_group", "plant_row", "boulder"], "check": "map"},
]
KEYS = [s["key"] for s in STEPS]
BY_KEY = {s["key"]: s for s in STEPS}
DONE = ("approved", "provisional")
GEOMETRY_FIELDS = {"footprint", "wings", "polygon", "id", "kind"}


# ---------------------------------------------------------------- content per step


def _strip_geometry(el: dict) -> dict:
    out = {k: v for k, v in el.items() if k not in ("footprint", "polygon")}
    if "wings" in out:
        out["wings"] = [{k: v for k, v in w.items() if k != "footprint"} for w in out["wings"]]
    return out


def _geometry_only(el: dict) -> dict:
    out = {"id": el.get("id"), "kind": el["kind"]}
    for k in ("footprint", "polygon", "frame"):
        if k in el:
            out[k] = el[k]
    if el.get("wings"):
        out["wings"] = [w.get("footprint") for w in el["wings"]]
    return out


def content(spec: dict, key: str):
    """The part of the (merged) design a step owns."""
    site = spec.get("site") or {}
    els = [el for el in spec.get("elements") or [] if isinstance(el, dict)]
    if key == "location":
        return {"location": site.get("location"), "plan": spec.get("plan"), "photo_matches": spec.get("photo_matches"),
                "defaults": spec.get("defaults")}
    if key == "ground":
        return site.get("terrain")
    if key == "boundary":
        return {"boundary": site.get("boundary"), "frame": site.get("frame")}
    if key == "inventory":
        return spec.get("inventory")
    step = BY_KEY[key]
    kinds = set(step.get("kinds", []))
    picked = [el for el in els if el.get("kind") in kinds]
    if step.get("fields") == "geometry":
        return [_geometry_only(el) for el in picked]
    if step.get("fields") == "details":
        return [_strip_geometry(el) if el["kind"] in ("structure", "deck") else el for el in picked]
    return picked


def _empty(c) -> bool:
    if c in (None, [], {}):
        return True
    if isinstance(c, dict):
        return all(_empty(v) for v in c.values())
    return False


def step_of_element(el: dict) -> str:
    for s in STEPS:
        if el.get("kind") in s.get("kinds", []):
            return s["key"]  # structures/decks: their outline step (details share the element)
    return "surfaces"


def element_ids(spec: dict, key: str) -> list[str]:
    kinds = set(BY_KEY[key].get("kinds", []))
    return [el.get("id") for el in spec.get("elements") or [] if isinstance(el, dict) and el.get("kind") in kinds]


def _hash(c) -> str:
    return hashlib.sha1(json.dumps(c, sort_keys=True, default=str).encode()).hexdigest()[:12]


def merged_spec(node: store.Node) -> dict:
    from landviz import project as project_mod
    return project_mod.load(str(node.project_dir), node.variant).spec


# ---------------------------------------------------------------- state


def _path(node):
    return node.app_dir / "pipeline.json"


def load(node: store.Node) -> dict:
    try:
        d = json.loads(_path(node).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    d.setdefault("auto", False)
    d.setdefault("steps", {})
    for k in KEYS:
        d["steps"].setdefault(k, {"status": "pending"})
    return d


def save(node: store.Node, d: dict):
    _path(node).write_text(json.dumps(d, indent=1))


def refresh(node: store.Node, d: dict | None = None, spec: dict | None = None) -> dict:
    """Notice content changes since each step was settled; flag later settled steps for a recheck."""
    with store.LOCK:
        d = d or load(node)
        try:
            spec = spec or merged_spec(node)
        except Exception:  # noqa: BLE001 — an invalid design file: leave the state as it is
            return d
        changed_any = False
        for i, k in enumerate(KEYS):
            st = d["steps"][k]
            h = _hash(content(spec, k))
            if st.get("hash") == h:
                continue
            if st["status"] in DONE and st.get("hash") is not None:
                st["changed_after"] = store._now()
                for later in KEYS[i + 1:]:
                    lst = d["steps"][later]
                    if lst["status"] in DONE:
                        lst["recheck"] = True
                        lst.setdefault("recheck_because", []).append(k)
            st["hash"] = h
            changed_any = True
        if changed_any:
            save(node, d)
        return d


def view(node: store.Node) -> dict:
    """What the UI shows: the steps with their state, the current one, and whether it's running."""
    d = refresh(node)
    rounds = node.inputs()["rounds"]
    working = next((r for r in rounds if r.get("kind") == "step" and r["status"] in ("queued", "running", "awaiting_answers")), None)
    steps = []
    for s in STEPS:
        st = d["steps"][s["key"]]
        steps.append({**{k: s[k] for k in ("key", "label", "short", "explain")}, **st,
                      "recheck_because": [BY_KEY[k]["short"] for k in st.get("recheck_because", [])]})
    current = next((s["key"] for s in steps if s["status"] not in DONE), None)
    return {"steps": steps, "auto": d["auto"], "current": current, "working": working and {
        "step": working.get("step"), "round": working["n"], "status": working["status"]},
            "enabled": not node.variant}


# ---------------------------------------------------------------- transitions

SUBMIT = None  # set by the server: SUBMIT(node, rnd) queues ai.run_round for a step round


def start(node: store.Node, key: str, note: str = "", auto: bool | None = None, new_inputs: bool = False) -> dict:
    """Run one step (an AI round scoped to it)."""
    from . import ai
    if node.variant:
        raise ValueError("child variants describe changes in one round; the steps build the base environment")
    if key not in BY_KEY:
        raise ValueError(f"unknown step {key!r}")
    with store.LOCK:
        d = load(node)
        if auto is not None:
            d["auto"] = bool(auto)
        st = d["steps"][key]
        st.update(status="working", started=store._now(), recheck=False, recheck_because=[])
        save(node, d)
    pins = open_comments(node, key)
    try:
        rnd = ai.start_round(node, "step", step=key, note=note, comments=pins, include_new=new_inputs)
    except ValueError:
        with store.LOCK:
            d = load(node)
            d["steps"][key]["status"] = "pending" if not d["steps"][key].get("summary") else "review"
            save(node, d)
        raise
    with store.LOCK:
        d = load(node)
        d["steps"][key]["round"] = rnd["n"]
        save(node, d)
        items = comments(node)
        for c in items:
            if c.get("step") == key and not c.get("resolved"):
                c["sent_round"] = rnd["n"]
        save_comments(node, items)
    SUBMIT(node, rnd)
    return rnd


def after_round(node: store.Node, rnd: dict, result: dict, failed: bool = False):
    """A step round finished: put the step up for review (or accept it and go on, in auto mode)."""
    key = rnd.get("step")
    if key not in BY_KEY:
        return
    spec = None
    try:
        spec = merged_spec(node)
    except Exception:  # noqa: BLE001
        pass
    with store.LOCK:
        d = load(node)
        st = d["steps"][key]
        if failed:
            st.update(status="pending", error=result.get("error") or "the round failed")
            save(node, d)
            return
        st.update(status="review", round=rnd["n"], summary=result.get("summary", ""),
                  notes=result.get("notes", []), revisit=[k for k in result.get("revisit", []) if k in BY_KEY],
                  error=None, finished=store._now())
        if spec is not None:
            st["hash"] = _hash(content(spec, key))
            # Content of other steps the AI changed anyway: their hashes move, and refresh() flags rechecks.
        resolve_comments(node, key, rnd["n"])
        auto = d["auto"]
        save(node, d)
    refresh(node)
    if auto and not rnd.get("questions_pending"):
        settle(node, key, "provisional", by="ai")


def settle(node: store.Node, key: str, status: str, by: str = "you"):
    """Approve a step (or accept it provisionally) and move on to the next step that isn't settled."""
    with store.LOCK:
        d = load(node)
        st = d["steps"][key]
        st.update(status=status, by=by, settled=store._now(), recheck=False, recheck_because=[])
        try:
            st["hash"] = _hash(content(merged_spec(node), key))
        except Exception:  # noqa: BLE001
            pass
        save(node, d)
        nxt = next((k for k in KEYS[KEYS.index(key) + 1:] if d["steps"][k]["status"] in ("pending",)), None)
        busy = any(r.get("kind") == "step" and r["status"] in ("queued", "running", "awaiting_answers")
                   for r in node.inputs()["rounds"])
    if nxt and not busy:
        start(node, nxt)
    return nxt


def set_auto(node: store.Node, on: bool):
    with store.LOCK:
        d = load(node)
        d["auto"] = bool(on)
        save(node, d)
        waiting = [k for k in KEYS if d["steps"][k]["status"] == "review"]
    if on:
        for k in waiting:
            settle(node, k, "provisional", by="you")


def import_existing(node: store.Node) -> dict:
    """Load a design made before the steps existed: every step with content becomes 'best judgement for now'.
    Questions still open from the last round are attached to the step they're about."""
    spec = merged_spec(node)
    with store.LOCK:
        d = load(node)
        for k in KEYS:
            c = content(spec, k)
            d["steps"][k] = ({"status": "provisional", "by": "imported", "hash": _hash(c), "settled": store._now()}
                             if not _empty(c) else {"status": "pending", "hash": _hash(c)})
        d["imported"] = store._now()
        inp = node.inputs()
        last = next((r for r in reversed(inp["rounds"]) if r["status"] == "done"), None)
        moved = 0
        if last and last.get("questions") and not last.get("questions_answered"):
            from .ai import normalize_questions
            for q in normalize_questions(last["questions"]):
                k = guess_step(q["text"] + " " + (q.get("why") or ""))
                d["steps"][k].setdefault("questions", []).append({**q, "from_round": last["n"]})
                moved += 1
            for r in inp["rounds"]:
                if r["n"] == last["n"]:
                    r["questions_answered"] = True
                    r["questions_moved_to_steps"] = True
            node.save_inputs(inp)
        save(node, d)
    return {"questions_moved": moved}


_KEYWORDS = [
    ("boundary", r"property line|lot line|boundary|setback|survey|neighbou?r"),
    ("ground", r"slope|grade|elevation|terrain|berm|level of the yard|drainage"),
    ("location", r"north|orientation|latitude|longitude|coordinates|address"),
    ("details", r"roof|pitch|ridge|eave|height|stair|step|deck|garage door|window|door|siding|color|colour|story|storey"),
    ("buildings", r"footprint|wall line|outline of the house|wing|addition|bump-?out"),
    ("fixtures", r"fence|wall|light|post|gate"),
    ("plants", r"tree|shrub|plant|hedge|bush|species|boulder|rock"),
    ("surfaces", r"patio|driveway|walkway|path|gravel|paver|concrete|lawn|grass|bed|mulch|court"),
    ("inventory", r"shed|structure|what is|is that|separate"),
]


def guess_step(text: str) -> str:
    t = text.lower()
    for key, pat in _KEYWORDS:
        if re.search(pat, t):
            return key
    return "inventory"


def answer_questions(node: store.Node, key: str, answers: dict) -> str:
    """Answers to a step's open questions; returns them as text for the redo note."""
    from .ai import format_answers
    with store.LOCK:
        d = load(node)
        st = d["steps"][key]
        qs = st.get("questions") or []
        text = format_answers(qs, answers)
        st.setdefault("answered", []).append({"questions": qs, "answers": answers, "at": store._now()})
        st["questions"] = []
        save(node, d)
    return text


# ---------------------------------------------------------------- map comments (pinned notes)


def _comments_path(node):
    return node.app_dir / "comments.json"


def comments(node: store.Node) -> list[dict]:
    try:
        return json.loads(_comments_path(node).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_comments(node: store.Node, items: list[dict]):
    _comments_path(node).write_text(json.dumps(items, indent=1))


def open_comments(node: store.Node, key: str) -> list[dict]:
    return [c for c in comments(node) if c.get("step") == key and not c.get("resolved")]


def resolve_comments(node: store.Node, key: str, n: int):
    items = comments(node)
    for c in items:
        if c.get("step") == key and not c.get("resolved") and c.get("sent_round") == n:
            c["resolved"] = n
    save_comments(node, items)
