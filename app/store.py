"""Projects, child variants, inputs, rounds, design versions, and the output selection.

A *node* is a project's base design or one of its child variants:
  "<project>"            design file  <root>/<project>/project.yaml
  "<project>/<variant>"  design file  <root>/<project>/variants/<variant>/variant.yaml
                         (an overlay on its parent — see landviz.project.variant_chain)

Each node keeps its app state in an `app/` folder next to its design file:
  node.json      title, parent, created
  inputs.json    input items + rounds (one per Send to AI). Items sent in a round are frozen.
  outputs.json   which output types are selected + custom output requests (never frozen)
  files/         uploaded input files
  design/        every version of the design file (v0.yaml, v1.yaml, …) + versions.json
  rounds/<n>/    prompt, agent log, and result of each AI round
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import config

LOCK = threading.RLock()

INPUT_TYPES = {
    "plan":      {"label": "Landscape plan", "accepts": ".pdf,.png,.jpg,.jpeg,.tif,.tiff",
                  "explain": "The landscaper's drawing (PDF or image). The AI reads every shape, label and "
                             "the plant key from it, and the app overlays it on the 3D model so you can check the result."},
    "spec":      {"label": "Specification / notes", "text": True,
                  "explain": "Anything in your own words: corrections, preferences, dimensions you measured, "
                             "what to emphasize. Treated exactly like the other inputs."},
    "photos":    {"label": "Site photos", "accepts": "image/*",
                  "explain": "Photos of the house and yard. They guide house colors, roof shape, windows and "
                             "what's already there."},
    "drone":     {"label": "Drone media", "accepts": "image/*,video/*", "folder": True,
                  "explain": "Overlapping drone photos or video of the property. Can become a textured 3D scan "
                             "of the real house and ground. Large sets: give a folder path instead of uploading."},
    "document":  {"label": "Document", "accepts": ".pdf,.txt,.md,.csv,.xlsx,.docx",
                  "explain": "Plant lists, quotes, HOA rules, survey reports — any supporting document."},
    "reference": {"label": "Inspiration image", "accepts": "image/*",
                  "explain": "Pictures of looks you like. Used as guidance for materials, plant character and mood."},
    "model":     {"label": "3D model / scan", "accepts": ".obj,.glb,.gltf,.ply,.fbx,.stl,.blend", "folder": True,
                  "explain": "An existing 3D model or photogrammetry scan to place in the scene (e.g. the house)."},
    "lidar":     {"label": "Public LiDAR data", "text": True, "placeholder":
                  "Address or coordinates (e.g. 40.5729, -105.1438) — or leave blank to use the location from your "
                  "other inputs (photo GPS, site location)",
                  "explain": "Allow the use of free public elevation data from the US Geological Survey: measured "
                             "ground slopes everywhere in the US, and — where a laser survey exists — measured roof and "
                             "tree heights and true-scale building outlines. The location (and an address, if you give "
                             "one, via the US Census geocoder) is sent to those public services. Surveys can be years "
                             "old, so trees may have grown since."},
    "aerial":    {"label": "Public aerial photo", "text": True, "placeholder":
                  "Address or coordinates (e.g. 40.5729, -105.1438) — or leave blank to use the location from your "
                  "other inputs (photo GPS, site location)",
                  "explain": "Allow the use of a free public aerial photo of the property (USGS NAIP, US only, about "
                             "0.6 m detail). It's taken straight down and lined up with the map, so building edges and "
                             "ground surfaces can be traced without the lean of a drone photo, and it's the backdrop of "
                             "the map editor. The location (and an address, if you give one, via the US Census "
                             "geocoder) is sent to those public services. Photos are a few years old."},
    "location":  {"label": "Site location", "text": True,
                  "explain": "Address or coordinates, and which way is north on the plan. Sets real sun angles "
                             "and shadows."},
}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(p: Path, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(p)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:48] or "untitled"


# ---------------------------------------------------------------- nodes


class Node:
    def __init__(self, node_id: str):
        self.id = node_id
        parts = node_id.split("/")
        if len(parts) not in (1, 2) or any(not p or p.startswith(".") for p in parts):
            raise ValueError(f"bad node id {node_id!r}")
        self.project_id = parts[0]
        self.variant = parts[1] if len(parts) == 2 else None
        self.project_dir = config.projects_root() / self.project_id
        self.dir = self.project_dir if not self.variant else self.project_dir / "variants" / self.variant
        if not (self.dir / self.design_name).exists() and not self.dir.exists():
            raise FileNotFoundError(node_id)

    @property
    def design_name(self) -> str:
        return "variant.yaml" if self.variant else "project.yaml"

    @property
    def design_path(self) -> Path:
        return self.dir / self.design_name

    @property
    def app_dir(self) -> Path:
        d = self.dir / "app"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def state(self) -> str:
        """Output state name used by landviz for this node's renders."""
        return self.variant or "base"

    def lv_args(self) -> list[str]:
        return [str(self.project_dir)] + (["--variant", self.variant] if self.variant else [])

    # ---- metadata
    def meta(self) -> dict:
        m = _read(self.app_dir / "node.json", {})
        if not m:
            m = {"title": self.variant or self.project_id.replace("-", " ").title(), "created": _now(),
                 "parent": None}
            if self.variant:
                ov = _yaml(self.design_path)
                parent = ov.get("parent") or "base"
                m["parent"] = self.project_id if parent == "base" else f"{self.project_id}/{parent}"
            _write(self.app_dir / "node.json", m)
            if self.design_path.exists() and not self.versions():
                self.snapshot_design("imported", "design file created outside the app")
        return m

    def set_meta(self, **kw):
        with LOCK:
            m = self.meta()
            m.update(kw)
            _write(self.app_dir / "node.json", m)

    # ---- inputs
    def inputs(self) -> dict:
        d = _read(self.app_dir / "inputs.json", None)
        if d is None:
            d = {"items": [], "rounds": [], "removed_inherited": []}
        return d

    def save_inputs(self, d: dict):
        _write(self.app_dir / "inputs.json", d)

    # ---- outputs selection
    def output_spec(self) -> dict:
        d = _read(self.app_dir / "outputs.json", None)
        if d is None:
            s = config.load_settings()["projects"]
            d = {"selected": list(s["default_outputs"]), "detail": s["default_detail"], "custom": []}
            _write(self.app_dir / "outputs.json", d)
        return d

    def save_output_spec(self, d: dict):
        _write(self.app_dir / "outputs.json", d)

    # ---- design versions
    def versions(self) -> list:
        return _read(self.app_dir / "design" / "versions.json", [])

    def snapshot_design(self, source: str, note: str = "", round_n: int | None = None) -> dict | None:
        """Save the current design file as a new version if it changed."""
        with LOCK:
            if not self.design_path.exists():
                return None
            text = self.design_path.read_text()
            sha = hashlib.sha256(text.encode()).hexdigest()[:12]
            vs = self.versions()
            if vs and vs[-1]["sha"] == sha:
                return vs[-1]
            k = (vs[-1]["k"] + 1) if vs else 0
            (self.app_dir / "design").mkdir(exist_ok=True)
            (self.app_dir / "design" / f"v{k}.yaml").write_text(text)
            v = {"k": k, "source": source, "note": note, "round": round_n, "time": _now(), "sha": sha}
            vs.append(v)
            _write(self.app_dir / "design" / "versions.json", vs)
            return v

    def version_text(self, k: int) -> str:
        return (self.app_dir / "design" / f"v{int(k)}.yaml").read_text()


def _yaml(p: Path) -> dict:
    import yaml
    try:
        return yaml.safe_load(p.read_text()) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------- tree / creation


def tree() -> list[dict]:
    root = config.projects_root()
    out = []
    for pdir in sorted(p for p in root.iterdir() if (p / "project.yaml").exists() and not p.name.startswith(("_", "."))):
        base = Node(pdir.name)
        children = []
        vroot = pdir / "variants"
        nodes = {}
        if vroot.exists():
            for vf in sorted(vroot.glob("*/variant.yaml")):
                n = Node(f"{pdir.name}/{vf.parent.name}")
                nodes[n.id] = {"id": n.id, "title": n.meta()["title"], "parent": n.meta()["parent"],
                               "children": [], "status": _status(n)}
        for nid, item in nodes.items():
            parent = item["parent"] or pdir.name
            (nodes[parent]["children"] if parent in nodes else children).append(item)
        m = base.meta()
        out.append({"id": base.id, "title": m["title"], "children": children, "status": _status(base),
                    "created": m["created"]})
    return out


def _status(n: Node) -> str:
    rounds = n.inputs()["rounds"]
    if rounds and rounds[-1]["status"] in ("queued", "running"):
        return "working"
    real = [v for v in n.versions() if v["source"] not in ("template", "branch")]
    if not real and not any(r["status"] == "done" for r in rounds):
        return "new"
    return "ready"


TEMPLATE = """# Design file — the deterministic source of truth for every output of this project.
# It is written by the AI from your inputs, can be edited here, and picks up hand edits
# pulled from Blender (edits.yaml). Reference: docs/SPEC.md
project:
  name: {title}
  units: {units}
site:
  location: {{lat: {lat}, lon: {lon}, utc_offset: {utc}}}
  boundary: [[0, 0], [100, 0], [100, 100], [0, 100]]   # placeholder until the AI reads your plan
  terrain: {{type: flat}}
elements: []
views:
  - {{name: aerial, preset: aerial}}
  - {{name: plan, preset: top}}
"""


def create_project(title: str) -> Node:
    with LOCK:
        root = config.projects_root()
        slug = slugify(title)
        pid, i = slug, 2
        while (root / pid).exists():
            pid, i = f"{slug}-{i}", i + 1
        pdir = root / pid
        for d in ("refs", "plan", "assets", "derived", "renders"):
            (pdir / d).mkdir(parents=True, exist_ok=True)
        s = config.load_settings()["projects"]
        loc = s["location"]
        (pdir / "project.yaml").write_text(TEMPLATE.format(
            title=title.replace("\n", " "), units=s["units"], lat=loc["lat"], lon=loc["lon"], utc=loc["utc_offset"]))
        n = Node(pid)
        _write(n.app_dir / "node.json", {"title": title, "created": _now(), "parent": None})
        n.save_inputs({"items": [], "rounds": [], "removed_inherited": []})
        n.output_spec()
        n.snapshot_design("template", "starting skeleton")
        return n


def create_variant(parent: Node, title: str) -> Node:
    """A child variant: overlay on its parent, with the parent's inputs copied in as editable."""
    with LOCK:
        name = slugify(title)
        import yaml
        taken = set(_yaml(parent.project_dir / "project.yaml").get("variants") or {})
        vroot = parent.project_dir / "variants"
        if vroot.exists():
            taken |= {p.name for p in vroot.iterdir()}
        base, i = name, 2
        while name in taken or name == "base":
            name, i = f"{base}-{i}", i + 1
        vdir = parent.project_dir / "variants" / name
        vdir.mkdir(parents=True)
        (vdir / "variant.yaml").write_text(
            f"# Child variant of {parent.meta()['title']!r}. Overlay on the parent design: only differences go here.\n"
            + yaml.safe_dump({"parent": parent.variant or "base", "title": title}, sort_keys=False))
        n = Node(f"{parent.project_id}/{name}")
        _write(n.app_dir / "node.json", {"title": title, "created": _now(), "parent": parent.id})
        # Copy the parent's inputs (all of them, history included) as editable, unfrozen items.
        items = []
        for it in parent.inputs()["items"]:
            c = json.loads(json.dumps(it))
            c["origin"] = {"node": parent.id, "id": it["id"], "round": it.get("round")}
            c["id"] = uuid.uuid4().hex[:10]
            c["round"] = None
            c["edited"] = False
            c["files"] = [{**f, "path": str(_abs_file(parent, f))} for f in it.get("files", [])]
            items.append(c)
        n.save_inputs({"items": items, "rounds": [], "removed_inherited": []})
        spec = parent.output_spec()
        n.save_output_spec({"selected": list(spec["selected"]), "detail": spec["detail"], "custom": []})
        n.snapshot_design("branch", f"branched from {parent.meta()['title']}")
        return n


def _abs_file(node: Node, f: dict) -> Path:
    p = Path(f["path"])
    return p if p.is_absolute() else node.app_dir / p


def file_path(node: Node, f: dict) -> Path:
    return _abs_file(node, f)


def archive(node: Node) -> Path:
    """Move a project or variant aside (never deleted) into <root>/.archive/."""
    with LOCK:
        dest = config.projects_root() / ".archive" / f"{node.id.replace('/', '__')}-{time.strftime('%Y%m%d-%H%M%S')}"
        dest.parent.mkdir(exist_ok=True)
        shutil.move(str(node.dir), str(dest))
        return dest


# ---------------------------------------------------------------- input items


def add_input(node: Node, itype: str, title: str = "", text: str = "", folder: str = "") -> dict:
    if itype not in INPUT_TYPES:
        raise ValueError(f"unknown input type {itype!r}")
    with LOCK:
        d = node.inputs()
        item = {"id": uuid.uuid4().hex[:10], "type": itype, "title": title or INPUT_TYPES[itype]["label"],
                "text": text, "folder": folder, "files": [], "created": _now(), "round": None}
        d["items"].append(item)
        node.save_inputs(d)
        return item


def _item(d, item_id):
    for it in d["items"]:
        if it["id"] == item_id:
            return it
    raise KeyError(item_id)


def update_input(node: Node, item_id: str, patch: dict) -> dict:
    with LOCK:
        d = node.inputs()
        it = _item(d, item_id)
        if it.get("round") is not None:
            raise PermissionError("this input was already sent and is frozen — start a variant to change it")
        for k in ("title", "text", "folder"):
            if k in patch:
                it[k] = patch[k]
        if it.get("origin"):
            it["edited"] = True
        node.save_inputs(d)
        return it


def delete_input(node: Node, item_id: str):
    with LOCK:
        d = node.inputs()
        it = _item(d, item_id)
        if it.get("round") is not None:
            raise PermissionError("this input was already sent and is frozen — start a variant to remove it")
        d["items"].remove(it)
        if it.get("origin"):
            d["removed_inherited"].append(it["origin"])
        own = node.app_dir / "files" / item_id
        if own.exists():
            shutil.rmtree(own)
        node.save_inputs(d)


def add_file(node: Node, item_id: str, filename: str, stream, length: int) -> dict:
    safe = re.sub(r"[^\w.\- ]+", "_", Path(filename).name).strip() or "file"
    with LOCK:
        d = node.inputs()
        it = _item(d, item_id)
        if it.get("round") is not None:
            raise PermissionError("frozen input")
    dest_dir = node.app_dir / "files" / item_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe
    remaining = length
    with open(dest, "wb") as f:
        while remaining > 0:
            chunk = stream.read(min(1 << 20, remaining))
            if not chunk:
                break
            f.write(chunk)
            remaining -= len(chunk)
    entry = {"name": safe, "path": str(dest.relative_to(node.app_dir)), "size": dest.stat().st_size}
    with LOCK:
        d = node.inputs()
        it = _item(d, item_id)
        it["files"] = [f for f in it["files"] if f["name"] != safe] + [entry]
        if it.get("origin"):
            it["edited"] = True
        node.save_inputs(d)
    return entry


def remove_file(node: Node, item_id: str, name: str):
    with LOCK:
        d = node.inputs()
        it = _item(d, item_id)
        if it.get("round") is not None:
            raise PermissionError("frozen input")
        it["files"] = [f for f in it["files"] if f["name"] != name]
        node.save_inputs(d)


# ---------------------------------------------------------------- output selection


def update_output_spec(node: Node, patch: dict) -> dict:
    with LOCK:
        spec = node.output_spec()
        if "selected" in patch:  # a set: each type at most once, order kept
            seen = []
            for t in patch["selected"]:
                if t not in seen:
                    seen.append(t)
            spec["selected"] = seen
        if "detail" in patch:
            spec["detail"] = patch["detail"]
        if patch.get("add_custom"):
            spec["custom"].append({"id": uuid.uuid4().hex[:10], "text": patch["add_custom"].strip(),
                                   "created": _now(), "status": "pending", "message": ""})
        if patch.get("remove_custom"):
            spec["custom"] = [c for c in spec["custom"]
                              if not (c["id"] == patch["remove_custom"] and c["status"] == "pending")]
        node.save_output_spec(spec)
        return spec
