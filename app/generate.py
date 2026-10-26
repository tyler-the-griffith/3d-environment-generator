"""Output types: the tried-and-true outputs a homeowner can select, and how each is produced.

Every output is produced by the landviz CLI from the node's saved model(s) and recorded in the
project's renders/manifest.json with its type, so the output page can group and hide by type.
Turning a type off only hides its outputs; nothing is deleted.
"""
from __future__ import annotations

import json

from . import config, store
from .jobs import BUS, Job

VIDEO_ARGS = ["--seconds", "12", "--fps", "24", "--resolution", "1280x720"]

OUTPUT_TYPES = [
    {"key": "stills", "label": "Still images",
     "explain": "Photo-style images from every camera view in the design: street, front walk, patio, aerial, top-down.",
     "needs": "eye-level views from where people stand, plus aerial and top views"},
    {"key": "plan_check", "label": "Plan check overlay",
     "explain": "The model seen from directly above with the landscaper's drawing laid over it. Anything that "
                "doesn't line up is a transcription mistake to fix.",
     "needs": "a calibrated plan image"},
    {"key": "photo_match", "label": "Photo-match check",
     "explain": "The model rendered from the exact position, heading and lens of your drone photos, shown next to "
                "and over each photo. Wrong heights, roof shapes and tree positions stand out.",
     "needs": "`photo_matches` in the design (GPS + heading/tilt per photo)"},
    {"key": "orbit_video", "label": "Fly-around video",
     "explain": "A smooth 12-second circle around the property from above.",
     "needs": "an `orbit` view (added automatically if missing)"},
    {"key": "walk_video", "label": "Walk-through video",
     "explain": "A walk at eye height along a path through the yard.",
     "needs": "a `flythrough` view with a path through the main spaces"},
    {"key": "web_walkthrough", "label": "3D walkthrough",
     "explain": "Walk or orbit around the model yourself in the browser (WASD keys). Plants are simplified "
                "so it runs smoothly.",
     "needs": "nothing extra"},
    {"key": "panorama", "label": "360° panoramas",
     "explain": "Look-all-around images from standing spots, viewable in any 360° photo viewer.",
     "needs": "`pano` views at standing spots (one at the center of the yard is added if none)"},
    {"key": "seasons", "label": "Four seasons",
     "explain": "The main views in spring, summer, fall and winter: foliage color, bloom and bare branches.",
     "needs": "good main eye-level views"},
    {"key": "growth", "label": "Growth over time",
     "explain": "The main views at planting, 3, 5 and 10 years, and maturity.",
     "needs": "good main eye-level views"},
    {"key": "dusk", "label": "Dusk & lighting",
     "explain": "The main views at golden hour and dusk, with any landscape lighting turned on.",
     "needs": "`light` elements if the plan has landscape lighting"},
    {"key": "report", "label": "PDF presentation",
     "explain": "A printable presentation: cover, every current image, area summary and plant schedule.",
     "needs": "nothing extra"},
    {"key": "plant_list", "label": "Plant schedule (CSV)",
     "explain": "Spreadsheet of every species, quantity and size for shopping or quotes.",
     "needs": "nothing extra"},
    {"key": "model_file", "label": "Blender model file",
     "explain": "The 3D model itself (.blend), for opening in Blender or handing to a designer.",
     "needs": "nothing extra"},
]
# The essentials, shown up front; everything else sits under "Advanced outputs" in the UI.
PRIMARY = ("stills", "plan_check", "web_walkthrough")
for _t in OUTPUT_TYPES:
    _t["primary"] = _t["key"] in PRIMARY
TYPE_KEYS = [t["key"] for t in OUTPUT_TYPES]
ORDER = ["stills", "plan_check", "photo_match", "panorama", "seasons", "growth", "dusk", "model_file", "web_walkthrough",
         "orbit_video", "walk_video", "plant_list", "report"]


def _scene(node: store.Node, sets=None, extra_views=None):
    from landviz import project as project_mod
    from landviz.resolve import resolve
    proj = project_mod.load(str(node.project_dir), node.variant, sets, extra_views)
    return proj, resolve(proj)


def _hero_views(scene) -> list[str]:
    eyes = [v["name"] for v in scene["views"] if v["preset"] == "eye"]
    aerial = [v["name"] for v in scene["views"] if v["preset"] == "aerial"]
    return (eyes[:2] + aerial[:1]) or [v["name"] for v in scene["views"] if not v.get("animation")][:2]


def _units_point(proj, xy):
    from landviz import units
    k = units.TO_METERS[proj.units]
    return [round(xy[0] / k, 2), round(xy[1] / k, 2)]


def default_views(node: store.Node) -> dict:
    """Views an output type needs when the design doesn't define one."""
    from landviz import geometry
    proj, scene = _scene(node)
    cx, cy = geometry.centroid(scene["site"]["boundary"])
    return {
        "orbit": {"name": "orbit", "preset": "orbit", "seconds": 12},
        "pano": {"name": "pano-center", "preset": "pano", "position": _units_point(proj, (cx, cy))},
    }


def up_to_date(node: store.Node, key: str, detail: str) -> bool:
    from landviz import outputs
    from landviz import project as project_mod
    from landviz.cli import current_fingerprints
    proj = project_mod.load(str(node.project_dir), node.variant)
    entries = [o for o in outputs.load_manifest(proj)["outputs"].values()
               if o.get("type") == key and o["state"].split("~")[0] == node.state]
    if not entries:
        return False
    current = current_fingerprints(proj)
    detail_free = key in ("report", "plant_list")
    return all((detail_free or o["detail"] == detail) and current.get((o["state"], o["detail"])) == o["fingerprint"]
               for o in entries)


def run(job: Job, node: store.Node, keys: list[str] | None, force: bool = False):
    spec = node.output_spec()
    keys = [k for k in ORDER if k in (keys or spec["selected"])]
    detail = spec["detail"]
    q = ["--quality", detail]
    base = node.lv_args()
    skipped, done, problems = [], [], []
    for i, key in enumerate(keys):
        label = next(t["label"] for t in OUTPUT_TYPES if t["key"] == key)
        job.set_step(label, i / max(1, len(keys)))
        if not force and up_to_date(node, key, detail):
            job.say(f"{label}: already up to date")
            skipped.append(key)
            continue
        t = ["--output-type", key]
        try:
            _, scene = _scene(node)
            hero = _hero_views(scene)
            if key == "stills":
                job.lv("render", *base, *q, *t)
            elif key == "plan_check":
                job.lv("check", *base, *t)
            elif key == "photo_match":
                if not (scene.get("photo_views") or []):
                    problems.append(f"{label}: the design has no usable photo matches yet. Send a round to "
                                    f"{_ai_name()} with drone photos (it sets them up when this output is selected).")
                    continue
                job.lv("photomatch", *base, *t)
            elif key == "panorama":
                panos = [v["name"] for v in scene["views"] if v["preset"] == "pano"]
                extra = [] if panos else ["--extra-view", json.dumps(default_views(node)["pano"])]
                names = panos or ["pano-center"]
                job.lv("render", *base, *q, *t, *extra, "--resolution", "4096x2048",
                       *sum((["--view", n] for n in names), []))
            elif key == "seasons":
                for season in ("spring", "summer", "fall", "winter"):
                    job.lv("render", *base, *q, *t, "--season", season, *sum((["--view", n] for n in hero), []))
            elif key == "growth":
                for year in ("0", "3", "5", "10", "mature"):
                    job.lv("render", *base, *q, *t, "--year", year, *sum((["--view", n] for n in hero), []))
            elif key == "dusk":
                for when in ("golden", "dusk"):
                    job.lv("render", *base, *q, *t, "--time", when, *sum((["--view", n] for n in hero), []))
            elif key == "orbit_video":
                orbit = [v["name"] for v in scene["views"] if (v.get("animation") or {}).get("type") == "orbit"]
                extra = [] if orbit else ["--extra-view", json.dumps(default_views(node)["orbit"])]
                job.lv("animate", *base, *q, *t, *extra, *VIDEO_ARGS, "--view", (orbit or ["orbit"])[0])
            elif key == "walk_video":
                walks = [v["name"] for v in scene["views"] if (v.get("animation") or {}).get("type") == "flythrough"]
                if not walks:
                    problems.append(f"{label}: the design has no walk-through path yet. Send a round to "
                                    f"{_ai_name()} (it's asked to add one when this output is selected).")
                    continue
                job.lv("animate", *base, *q, *t, *VIDEO_ARGS, *sum((["--view", n] for n in walks), []))
            elif key == "web_walkthrough":
                job.lv("export", *base, *t)
            elif key == "model_file":
                job.lv("model", *base, *q, *t)
            elif key == "plant_list":
                job.lv("report", *base, *t, "--what", "csv")
            elif key == "report":
                job.lv("report", *base, *t, "--what", "pdf")
            done.append(key)
        except Exception as e:  # noqa: BLE001 — keep going with the other types
            if job.cancel_requested:
                raise
            problems.append(f"{label}: {e}")
            job.say(f"{label} failed: {e}")
        BUS.publish("node_changed", {"node": node.id})
    if problems:
        job.say("Problems:\n" + "\n".join(problems))
    job.result = {"done": done, "skipped": skipped, "problems": problems}
    if problems and not done:
        raise RuntimeError("; ".join(problems))
    return job.result


def _ai_name():
    from .ai import display_name
    return display_name()


def list_outputs(node: store.Node) -> dict:
    """Everything the output page shows for a node, grouped by output type."""
    from landviz import outputs
    from landviz import project as project_mod
    from landviz.cli import current_fingerprints
    proj = project_mod.load(str(node.project_dir), node.variant)
    m = outputs.load_manifest(proj)
    try:
        current = current_fingerprints(proj)
    except Exception:  # noqa: BLE001
        current = {}
    groups: dict[str, list] = {}
    root = config.projects_root()
    rel_renders = proj.renders.relative_to(root)
    for o in m["outputs"].values():
        if o["state"].split("~")[0] != node.state:
            continue
        key = o.get("type") or "other"
        fresh = current.get((o["state"], o["detail"]))
        url = f"/files/{rel_renders.as_posix()}/{o['file']}"
        cover = f"/files/{rel_renders.as_posix()}/{o['cover']}" if o.get("cover") else (url if o["kind"] == "still" else None)
        groups.setdefault(key, []).append({
            **o, "url": url, "cover_url": cover, "stale": fresh is not None and fresh != o["fingerprint"],
            "label": _label(o), "download": f"/api/download?path={rel_renders.as_posix()}/{o['file']}",
        })
    for items in groups.values():
        items.sort(key=lambda o: (o["state"], o["view"]))
    return {"groups": groups, "types": OUTPUT_TYPES}


def _label(o) -> str:
    view = o["view"].replace("-", " ").replace("_", " ").title()
    extra = o["state"].split("~")[1:]
    nice = []
    for part in extra:
        k, _, v = part.partition("=")
        nice.append({"season": v.title(), "year": ("At planting" if v == "0" else
                                                    "Mature" if v == "mature" else f"Year {v}"),
                     "time": v.title()}.get(k, part))
    return " · ".join([view] + nice)


def standalone_viewer(web_dir) -> str:
    """The walkthrough viewer with the model and sun embedded, so it opens from a downloaded folder
    with a double-click (browsers block fetching local files from file:// pages)."""
    import base64
    from pathlib import Path
    web_dir = Path(web_dir)
    html = (web_dir / "viewer.html").read_text()
    glb = base64.b64encode((web_dir / "model.glb").read_bytes()).decode()
    info = (web_dir / "model.json").read_text() if (web_dir / "model.json").exists() else "null"
    html = html.replace('const modelUrl = params.get("model") || "model.glb";',
                        f'const modelUrl = "data:model/gltf-binary;base64,{glb}";')
    html = html.replace('const info = await fetch(modelUrl.replace(/\\.glb$/, ".json")).then(r => r.ok ? r.json() : null).catch(() => null);',
                        f"const info = {info};")
    return html
