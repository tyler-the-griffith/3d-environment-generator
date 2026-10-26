"""Blender-side entry point.

Two kinds of jobs, so that every output is taken from one saved model:

  build:  blender -b --factory-startup -P runner.py -- job.json
          Builds the model from the resolved scene and saves it (geometry only — no cameras,
          sky or render settings, which belong to views, not to the model).

  use:    blender -b <model.blend> -P runner.py -- job.json
          Opens a saved model and takes outputs from it: render (stills), animate (frames),
          export (glTF for the browser), pull (report hand edits for edits.yaml).

Job file (written by landviz/blender_bridge.py):
  {"actions": [...], "scene": "<resolved.json>", "blend": "<model path>", "device": "...",
   "views": [names] | null, "outputs": {view: path}, "frames": {view: dir}, "glb": "..."}
Writes <job>.result.json.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bpy  # noqa: E402

from landviz_blender import scene as scene_mod  # noqa: E402
from landviz_blender import stage  # noqa: E402
from landviz_blender.util import Ground, log  # noqa: E402


def build(job, data, result):
    t = time.time()
    scene_mod.build(data)
    bpy.context.scene["lv_fingerprint"] = job["fingerprint"]
    result["timings"]["build_s"] = round(time.time() - t, 1)
    bpy.ops.wm.save_as_mainfile(filepath=job["blend"], compress=True)
    result["files"].append(job["blend"])
    log(f"model saved ({result['timings']['build_s']}s build)")


def use(job, data, result):
    have = bpy.context.scene.get("lv_fingerprint")
    if have != job["fingerprint"]:
        raise RuntimeError(f"opened model {have} but job expects {job['fingerprint']}")
    terrain = bpy.data.objects.get("Terrain")
    ground = Ground([terrain] if terrain else [])
    actions = job["actions"]

    if "pull" in actions:
        result["pull"] = pull_report()
        return

    stage.setup_world(data["environment"], Path(data["project"]["dir"]))
    result["device"] = stage.configure_render(data["render"], job.get("device", "cpu"))
    log(f"render device: {result['device']}")
    cams = {v["name"]: (v, stage.make_camera(v, ground, data["render"]["resolution"])) for v in data["views"]}
    wanted = job.get("views")

    if "render" in actions:
        for name, (v, cam) in cams.items():
            if v.get("animation") or (wanted and name not in wanted):
                continue
            t = time.time()
            out = Path(job["outputs"][name])
            out.parent.mkdir(parents=True, exist_ok=True)
            res = v.get("resolution") or data["render"]["resolution"]  # photo matches use the photo's aspect
            bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y = res
            stage.render_still(cam, out)
            result["files"].append(str(out))
            result["timings"][f"render_{name}_s"] = round(time.time() - t, 1)
            log(f"rendered {name} in {time.time() - t:.0f}s")
            if v.get("ortho"):  # lets the core map plan pixels onto this image
                result.setdefault("ortho", {})[name] = {
                    "center": list(cam.location[:2]), "ortho_scale": cam.data.ortho_scale,
                    "resolution": [bpy.context.scene.render.resolution_x * bpy.context.scene.render.resolution_percentage // 100,
                                   bpy.context.scene.render.resolution_y * bpy.context.scene.render.resolution_percentage // 100]}
    if "animate" in actions:
        for name, (v, cam) in cams.items():
            if not v.get("animation") or (wanted and name not in wanted):
                continue
            t = time.time()
            frames = Path(job["frames"][name])
            stage.render_frames(cam, frames)
            result["files"].append(str(frames))
            log(f"rendered animation {name} in {time.time() - t:.0f}s")
    if "export" in actions:
        glb = Path(job["glb"])
        glb.parent.mkdir(parents=True, exist_ok=True)
        stage.export_gltf(glb)
        result["files"].append(str(glb))


def pull_report():
    """Compare every tagged object against its as-built transform; report what changed."""
    from mathutils import Matrix
    ids = json.loads(bpy.context.scene.get("lv_records", "[]"))
    primaries = {}
    loose = []
    for o in bpy.data.objects:
        rid = o.get("lv_rec")
        if rid is None:
            continue
        m0 = Matrix([list(o["lv_m0"][i * 4:i * 4 + 4]) for i in range(4)])
        moved = not _close(o.matrix_world, m0)
        if o.get("lv_primary"):
            primaries[rid] = (o, m0, moved)
        elif moved and o.parent is None:
            loose.append(o.name)
    changes = []
    for rid in ids:
        if rid not in primaries:
            changes.append({"rec": rid, "deleted": True})
            continue
        o, m0, moved = primaries[rid]
        if not moved:
            continue
        loc0, rot0, sc0 = m0.decompose()
        loc1, rot1, sc1 = o.matrix_world.decompose()
        changes.append({
            "rec": rid, "src": o["lv_src"], "kind": o["lv_kind"],
            "from": list(loc0), "to": list(loc1),
            "rot_z_deg": (rot0.rotation_difference(rot1).to_euler().z) * 57.29577951308232,
            "scale": [sc1[i] / sc0[i] if sc0[i] else 1.0 for i in range(3)],
        })
    return {"changes": changes, "unpulled_parts": loose}


def _close(a, b, eps=1e-4):
    return all(abs(a[i][j] - b[i][j]) < eps for i in range(4) for j in range(4))


def main():
    job_path = Path(sys.argv[sys.argv.index("--") + 1])
    job = json.loads(job_path.read_text())
    data = json.loads(Path(job["scene"]).read_text())
    result = {"files": [], "device": None, "timings": {}}
    if job["actions"] == ["build"]:
        build(job, data, result)
    else:
        use(job, data, result)
    job_path.with_suffix(".result.json").write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(3)
