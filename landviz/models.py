"""The model store: one saved 3D model per design state, reused by every output.

A *design state* is the base design or a variant, plus any --set overrides. Its *model* is
the Blender file built from it. Every image, video, web export and plan check for that
state is taken from that one file.

Models are keyed by a fingerprint of everything that determines geometry:
  - the resolved site, elements, species and growth state (positions, sizes, seasons)
  - the detail tier (draft / preview / final / web: leaf density, grass)
  - the framework's Blender-side code, the Blender version, and the stamps of any
    imported files (scans, plant models)
Lighting, cameras, time of day, samples and resolution are *not* in it: they're how you
look at the model, not what the model is. So a dusk render and a noon render of the
same design come from the same file.

If a saved model was changed after it was built (hand-edited in Blender) it is never
rendered from; `lv pull` carries the edits into edits.yaml and the next build includes them.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import blender_bridge
from .project import REPO_ROOT, Project

TIERS = ("draft", "preview", "final", "web")
_GEOMETRY_RENDER_KEYS = ("quality", "grass", "leaf_mult", "seed")
_CODE_HASH = None


class ModelEdited(RuntimeError):
    pass


def code_hash() -> str:
    global _CODE_HASH
    if _CODE_HASH is None:
        h = hashlib.sha256()
        # Model-building code only. stage.py/runner.py take outputs *from* a model (cameras,
        # lighting, export) and don't change it, so editing them doesn't invalidate models.
        for p in sorted((REPO_ROOT / "landviz_blender").rglob("*.py")):
            if p.name in ("stage.py", "runner.py"):
                continue
            h.update(p.relative_to(REPO_ROOT).as_posix().encode())
            h.update(p.read_bytes())
        _CODE_HASH = h.hexdigest()[:16]
    return _CODE_HASH


def _file_stamps(scene: dict) -> dict:
    files = [e["file"] for e in scene["elements"] if e["kind"] == "mesh"]
    files += [s["asset"] for s in scene["species"].values() if s.get("asset")]
    out = {}
    for f in files:
        p = Path(f)
        out[f] = [p.stat().st_size, p.stat().st_mtime_ns] if p.exists() else None
    return out


def fingerprint(scene: dict) -> str:
    payload = {
        "site": scene["site"], "elements": scene["elements"], "species": scene["species"],
        "growth": scene["growth"],
        "detail": {k: scene["render"][k] for k in _GEOMETRY_RENDER_KEYS},
        "files": _file_stamps(scene), "code": code_hash(), "blender": blender_bridge.blender_version(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def state_name(variant: str | None, sets: list[str] | None) -> str:
    """'base', 'fall', or 'base~render.year=1' — names the design state for outputs."""
    ignore = ("render.quality", "render.samples", "render.resolution", "render.device", "render.seed")
    parts = [s for s in (sets or []) if not s.startswith(ignore)]
    name = variant or "base"
    for p in parts:  # filesystem-safe on Linux and Windows (this folder may be shared with a VM)
        p = p.removeprefix("render.")
        name += "~" + "".join(c for c in p.replace("/", "-") if c not in ' :<>"\\|?*')
    return name


class ModelStore:
    def __init__(self, project: Project):
        self.project = project
        self.dir = project.derived / "models"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.logs = project.derived / "logs"
        self.logs.mkdir(exist_ok=True)

    def path(self, fp: str) -> Path:
        return self.dir / f"{fp}.blend"

    def meta_path(self, fp: str) -> Path:
        return self.dir / f"{fp}.json"

    def meta(self, fp: str) -> dict | None:
        p = self.meta_path(fp)
        return json.loads(p.read_text()) if p.exists() else None

    def is_edited(self, fp: str) -> bool:
        meta, p = self.meta(fp), self.path(fp)
        if not meta or not p.exists():
            return False
        st = p.stat()
        return [st.st_size, st.st_mtime_ns] != meta["stamp"]

    def ensure(self, scene: dict, scene_path: Path, state: str, sets: list[str] | None = None) -> tuple[str, Path, bool]:
        """Return (fingerprint, model path, built_now). Builds only if no matching model exists."""
        fp = fingerprint(scene)
        p = self.path(fp)
        if p.exists() and self.meta(fp):
            if self.is_edited(fp):
                raise ModelEdited(
                    f"the {state} model ({p.name}) was edited by hand after it was built.\n"
                    f"  Run `lv pull {self.project.dir.name}"
                    + (f" --variant {self.project.variant}" if self.project.variant else "")
                    + "` to carry the edits into edits.yaml (they then appear in every view),\n"
                    f"  or `lv pull {self.project.dir.name} --discard` to throw them away.")
            meta = self.meta(fp)
            if state not in meta["states"]:
                meta["states"].append(state)
                self.meta_path(fp).write_text(json.dumps(meta, indent=1))
            print(f"model {fp} ({scene['render']['quality']} detail) — reusing saved model")
            return fp, p, False
        t = time.time()
        job = {"actions": ["build"], "scene": str(scene_path), "blend": str(p), "fingerprint": fp}
        blender_bridge.run(job, self.dir / f"{fp}.job.json", log_path=self.logs / f"build_{fp}.log")
        st = p.stat()
        # Keep the exact scene the model was built from (lv pull needs as-built sizes).
        (self.dir / f"{fp}.scene.json").write_text(scene_path.read_text())
        self.meta_path(fp).write_text(json.dumps({
            "fingerprint": fp, "states": [state], "tier": scene["render"]["quality"],
            "variant": self.project.variant, "sets": list(sets or []),
            "built": time.strftime("%Y-%m-%d %H:%M:%S"), "build_s": round(time.time() - t, 1),
            "stamp": [st.st_size, st.st_mtime_ns], "project": self.project.name,
            "scene": str(scene_path)}, indent=1))
        for leftover in self.dir.glob(f"{fp}.job*.json"):
            leftover.unlink()
        print(f"model {fp} ({scene['render']['quality']} detail) — built in {time.time() - t:.0f}s")
        return fp, p, True

    def use(self, fp: str, scene_path: Path, actions: list[str], device: str, name: str, **extra) -> dict:
        """Run an output job on a saved model."""
        job = {"actions": actions, "scene": str(scene_path), "blend": str(self.path(fp)),
               "fingerprint": fp, "device": device, **extra}
        jp = self.dir / f"{fp}.{name}.job.json"
        try:
            return blender_bridge.run(job, jp, log_path=self.logs / f"{name}_{fp}.log", open_blend=self.path(fp))
        finally:
            for leftover in self.dir.glob(f"{fp}.{name}.job*.json"):
                leftover.unlink()

    def prune(self, keep: set[str]) -> list[Path]:
        removed = []
        for p in self.dir.glob("*.blend"):
            fp = p.stem
            if fp not in keep:
                for q in (p, self.meta_path(fp), p.with_suffix(".blend1"), self.dir / f"{fp}.scene.json"):
                    if q.exists():
                        q.unlink()
                        removed.append(q)
        return removed
