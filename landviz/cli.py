"""landviz command line.  Run `./lv --help` from the repo root.

Every output command follows the same path:
    project.yaml (+ edits.yaml, variant, --set)  →  resolved scene  →  saved model (built once,
    keyed by fingerprint)  →  output taken from that model  →  manifest + gallery
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import blender_bridge, edits, ingest, outputs, reconstruct, solar
from . import project as project_mod
from .catalog import Catalog
from .models import ModelEdited, ModelStore, fingerprint, state_name
from .preview import render_preview, svg_to_png
from .resolve import QUALITY_PRESETS, _views, resolve

TEMPLATE = project_mod.REPO_ROOT / "projects" / "_template"
VIEWER = project_mod.REPO_ROOT / "viewer" / "index.html"
PORT = 8790


@dataclass
class Prepared:
    project: project_mod.Project
    scene: dict
    scene_path: Path
    state: str
    sets: list
    store: ModelStore


def _sets(args) -> list[str]:
    sets = list(getattr(args, "set", None) or [])
    for k in ("year", "season", "time", "date"):
        if getattr(args, k, None):
            sets.append(f"render.{k}={getattr(args, k)}")
    if getattr(args, "samples", None):
        sets.append(f"render.samples={args.samples}")
    if getattr(args, "resolution", None):
        w, h = args.resolution.lower().split("x")
        sets.append(f"render.resolution=[{w},{h}]")
    return sets


def _prepare(args, detail: str | None = None, quiet=True) -> Prepared:
    variant = getattr(args, "variant", None)
    sets = _sets(args)
    detail = detail or getattr(args, "quality", None)
    all_sets = sets + ([f"render.quality={detail}"] if detail else [])
    proj = project_mod.load(args.project, variant, all_sets, getattr(args, "extra_views", None))
    scene = resolve(proj)
    state = state_name(variant, sets)
    rdir = proj.derived / "resolved"
    rdir.mkdir(exist_ok=True)
    path = rdir / f"{state}__{scene['render']['quality']}.json"
    path.write_text(json.dumps(scene, indent=1))
    errors = [i for i in proj.issues if i.level == "error"]
    for i in proj.issues:
        if (not quiet and i.level != "info") or i.level == "error" or getattr(args, "verbose", False):
            print(i)
    if errors:
        sys.exit(f"{len(errors)} error(s) in project — fix them first")
    return Prepared(proj, scene, path, state, sets, ModelStore(proj))


def _model(prep: Prepared):
    try:
        return prep.store.ensure(prep.scene, prep.scene_path, prep.state, prep.sets)
    except ModelEdited as e:
        sys.exit(f"error: {e}")


def current_fingerprints(project: project_mod.Project) -> dict:
    """{(state, detail): today's fingerprint} for every state that has outputs."""
    m = outputs.load_manifest(project)
    current = {}
    seen = set()
    for o in m["outputs"].values():
        key = (o["variant"], tuple(o["sets"]), o["detail"])
        if key in seen:
            continue
        seen.add(key)
        try:
            p = project_mod.load(str(project.dir), o["variant"], list(o["sets"]) + [f"render.quality={o['detail']}"])
            current[(o["state"], o["detail"])] = fingerprint(resolve(p))
        except project_mod.SpecError:
            pass
    return current


def _refresh_gallery(project: project_mod.Project) -> Path:
    """Recompute today's fingerprint for every state in the manifest to flag stale outputs."""
    return outputs.build_gallery(project, current_fingerprints(project))


def _finish(prep: Prepared, produced: list[tuple], fp: str, args=None):
    """produced: (view, kind, file[, cover]). Records each output against the model it came from."""
    otype = getattr(args, "output_type", None) if args is not None else None
    for item in produced:
        view, kind, f = item[:3]
        cover = item[3] if len(item) > 3 else None
        outputs.record(prep.project, prep.state, view, kind, f, fp, prep.scene, prep.project.variant, prep.sets,
                       output_type=otype, cover=cover)
    g = _refresh_gallery(prep.project)
    print(f"gallery → {g}   (lv serve {prep.project.dir.name} to browse)")


# ---------------------------------------------------------------- project setup


def cmd_new(args):
    dest = project_mod.PROJECTS_DIR / args.name
    if dest.exists():
        sys.exit(f"{dest} already exists")
    shutil.copytree(TEMPLATE, dest)
    for d in ("refs", "plan", "assets", "derived", "renders"):
        (dest / d).mkdir(exist_ok=True)
    y = dest / "project.yaml"
    y.write_text(y.read_text().replace("TEMPLATE_NAME", args.title or args.name))
    print(f"created {dest}\n  put the landscaper's plan and photos in {dest / 'refs'}/")


def cmd_validate(args):
    prep = _prepare(args, quiet=False)
    scene, proj = prep.scene, prep.project
    kinds = {}
    for e in scene["elements"]:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    n_plants = kinds.get("plant", 0)
    print(f"{proj.name} [{prep.state}]: {len(scene['elements'])} records "
          f"({', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))})")
    print(f"  {n_plants} plants of {len(scene['species'])} species; year {scene['growth']['year']}, "
          f"month {scene['growth']['month']}; sun az {scene['environment']['sun_azimuth_deg']:.0f}° "
          f"el {scene['environment']['sun_elevation_deg']:.0f}°")
    if proj.plan_frame:
        print(f"  plan scale {proj.plan_frame.meters_per_pixel * 1000:.1f} mm/px"
              + (f", calibration RMS {proj.plan_frame.rms_error:.2f} m" if proj.plan_frame.rms_error else ""))
    if (proj.dir / project_mod.EDITS_FILE).exists():
        print(f"  includes hand edits from {project_mod.EDITS_FILE}")
    print(f"  model fingerprint ({scene['render']['quality']} detail): {fingerprint(scene)}")


def cmd_preview(args):
    """Diagnostic 2D drawing straight from the resolved layout (labels, legend) — no Blender."""
    prep = _prepare(args)
    svg = prep.project.derived / f"preview_{prep.state}.svg"
    render_preview(prep.project, prep.scene, svg, plan_underlay=not args.no_plan)
    png = svg_to_png(svg, svg.with_suffix(".png"), args.width)
    print(f"preview → {png or svg}")


# ---------------------------------------------------------------- model + outputs


def cmd_build(args):
    prep = _prepare(args)
    fp, path, _ = _model(prep)
    print(f"model → {path}")


def cmd_render(args):
    prep = _prepare(args)
    fp, _path, _ = _model(prep)
    device = blender_bridge.pick_device(args.device)
    sdir = outputs.state_dir(prep.project, prep.state)
    stills = [v["name"] for v in prep.scene["views"] if not v.get("animation")]
    wanted = args.view or stills
    unknown = set(wanted) - set(stills)
    if unknown:
        sys.exit(f"no still view named {', '.join(sorted(unknown))}; views: {', '.join(stills)}")
    outs = {v: str(sdir / f"{v}.png") for v in wanted}
    t = time.time()
    res = prep.store.use(fp, prep.scene_path, ["render"], device, "render", views=wanted, outputs=outs)
    print(f"rendered {len(wanted)} view(s) from model {fp} in {time.time() - t:.0f}s on {res['device']}:")
    for v in wanted:
        print("  ", outs[v])
    _finish(prep, [(v, "still", Path(outs[v])) for v in wanted], fp, args)


def cmd_animate(args):
    prep = _prepare(args)
    anims = [v for v in prep.scene["views"] if v.get("animation") and (not args.view or v["name"] in args.view)]
    if not anims:
        sys.exit("no orbit/flythrough views to animate")
    for v in anims:
        if args.seconds:
            v["animation"]["seconds"] = args.seconds
        if args.fps:
            v["animation"]["fps"] = args.fps
    prep.scene_path.write_text(json.dumps(prep.scene, indent=1))  # views aren't in the fingerprint
    fp, _path, _ = _model(prep)
    device = blender_bridge.pick_device(args.device)
    frames = {}
    for v in anims:
        # Frame folders are keyed by model + camera/render settings, so a resumed render can
        # never splice frames from two different models into one video.
        key = hashlib.sha256(json.dumps([fp, v, prep.scene["render"], prep.scene["environment"]],
                                        sort_keys=True).encode()).hexdigest()[:12]
        frames[v["name"]] = str(prep.project.derived / "frames" / f"{v['name']}_{key}")
    prep.store.use(fp, prep.scene_path, ["animate"], device, "animate", views=list(frames), frames=frames)
    sdir = outputs.state_dir(prep.project, prep.state)
    produced = []
    for v in anims:
        mp4 = sdir / f"{v['name']}.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(v["animation"]["fps"]),
                        "-pattern_type", "glob", "-i", str(Path(frames[v["name"]]) / "f_*.png"),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(mp4)], check=True)
        cover = mp4.with_suffix(".cover.jpg")  # a frame from the video itself
        mid = len(list(Path(frames[v["name"]]).glob("f_*.png"))) // 3 + 1
        subprocess.run(["magick", str(Path(frames[v["name"]]) / f"f_{mid:04d}.png"), "-resize", "960x>",
                        "-quality", "85", str(cover)], check=False)
        print("video →", mp4)
        produced.append((v["name"], "video", mp4, cover if cover.exists() else None))
    _finish(prep, produced, fp, args)


def cmd_export(args):
    prep = _prepare(args, detail="web")
    fp, _path, _ = _model(prep)
    wdir = outputs.state_dir(prep.project, prep.state) / "web"
    glb = wdir / "model.glb"
    prep.store.use(fp, prep.scene_path, ["export"], "cpu", "export", glb=str(glb))
    env = prep.scene["environment"]
    (wdir / "model.json").write_text(json.dumps({
        "name": prep.project.name, "state": prep.state, "fingerprint": fp,
        "sun": {"azimuth_site_deg": env["sun_azimuth_site_deg"], "elevation_deg": env["sun_elevation_deg"]},
        "datetime": env["datetime"]}, indent=1))
    shutil.copy2(VIEWER, wdir / "viewer.html")
    print(f"web model → {glb} ({glb.stat().st_size / 1e6:.1f} MB)")
    cover = _model_cover(prep, fp, wdir / "cover.png")  # a picture of exactly what the viewer loads
    rel = wdir / "viewer.html"
    _finish(prep, [("walkthrough", "web", rel, cover)], fp, args)
    print(f"open: lv serve {prep.project.dir.name}  →  http://localhost:{PORT}/{prep.state}/web/viewer.html")


def _model_cover(prep: Prepared, fp: str, out: Path) -> Path | None:
    """Quick aerial still of a saved model — the cover image for outputs that aren't images."""
    saved = prep.project.spec.get("views")
    prep.project.spec["views"] = [{"name": "_cover", "preset": "aerial", "azimuth": 205, "elevation": 35}]
    view = _views(prep.project, prep.scene["site"]["boundary"])[0]
    prep.project.spec["views"] = saved
    scene = json.loads(json.dumps(prep.scene))
    scene["views"] = [view]
    scene["render"].update(resolution=[960, 540], resolution_pct=100, samples=24, denoise=True)
    sp = prep.scene_path.with_name(prep.scene_path.stem + "__cover.json")
    sp.write_text(json.dumps(scene))
    try:
        prep.store.use(fp, sp, ["render"], blender_bridge.pick_device("auto"), "cover",
                       views=["_cover"], outputs={"_cover": str(out)})
    except blender_bridge.BlenderError as e:
        print("cover render failed:", e)
        return None
    return out if out.exists() else None


def cmd_model(args):
    """Offer the saved model itself (.blend) as an output."""
    prep = _prepare(args)
    fp, path, _ = _model(prep)
    sdir = outputs.state_dir(prep.project, prep.state)
    dest = sdir / "model.blend"
    shutil.copy2(path, dest)
    cover = _model_cover(prep, fp, sdir / "model.cover.png")
    print(f"model file → {dest}")
    _finish(prep, [("blender-model", "file", dest, cover)], fp, args)


def cmd_report(args):
    """Presentation PDF + plant schedule CSV, from the design and today's images of this state."""
    from . import report
    prep = _prepare(args)
    fp = fingerprint(prep.scene)
    sdir = outputs.state_dir(prep.project, prep.state)
    current = set(current_fingerprints(prep.project).values()) | {fp}
    produced = []
    kinds = args.what or ["pdf", "csv"]
    if "csv" in kinds:
        csv_path = report.write_csv(prep.scene, sdir / "plant-schedule.csv")
        print("plant schedule →", csv_path)
        produced.append(("plant-schedule", "file", csv_path))
    if "pdf" in kinds:
        pdf_path = report.write_pdf(prep.project, prep.state, prep.scene, fp, current, sdir / "report.pdf")
        cover = sdir / "report.cover.png"
        subprocess.run(["pdftoppm", "-png", "-r", "60", "-f", "1", "-l", "1", "-singlefile", str(pdf_path),
                        str(cover.with_suffix(""))], check=False)
        print("report →", pdf_path)
        produced.append(("report", "pdf", pdf_path, cover if cover.exists() else None))
    _finish(prep, produced, fp, args)


def cmd_check(args):
    """Top-down render of the model with the landscaper's plan laid over it."""
    prep = _prepare(args, detail="draft")
    proj, scene = prep.project, prep.scene
    saved = proj.spec.get("views")
    proj.spec["views"] = [{"name": "plan-check", "preset": "top", "padding": 1.12}]
    view = _views(proj, scene["site"]["boundary"])[0]
    proj.spec["views"] = saved
    w, h = view["ortho_extent"]
    width = args.width
    scene["views"] = [view]
    scene["render"]["resolution"] = [width, max(64, int(width * h / w))]
    scene["render"]["resolution_pct"] = 100
    # Plan checks read best under a high sun (short shadows); lighting isn't part of the model.
    env = scene["environment"]
    noon = env["datetime"][:11] + "13:00" + env["datetime"][16:]
    from datetime import datetime
    az, el = solar.sun_position(env["lat"], env["lon"], datetime.fromisoformat(noon))
    north = env["sun_azimuth_deg"] - env["sun_azimuth_site_deg"]
    env.update(sun_azimuth_deg=az, sun_elevation_deg=el, sun_azimuth_site_deg=(az - north) % 360)
    scene["render"]["exposure"] = -3.5
    prep.scene_path.write_text(json.dumps(scene, indent=1))
    fp, _path, _ = _model(prep)
    sdir = outputs.state_dir(proj, prep.state)
    out = sdir / "plan-check.png"
    res = prep.store.use(fp, prep.scene_path, ["render"], blender_bridge.pick_device(args.device), "check",
                         views=["plan-check"], outputs={"plan-check": str(out)})
    plan = proj.spec.get("plan") or {}
    if proj.plan_frame and plan.get("image") and proj.path(plan["image"]).exists():
        _overlay_plan(proj, proj.path(plan["image"]), out, res["ortho"]["plan-check"], args.opacity)
        print(f"plan check → {out}  (plan drawing at {int(args.opacity * 100)}% over the model)")
    else:
        print(f"plan check → {out}  (no calibrated plan image to overlay)")
    _finish(prep, [("plan-check", "still", out)], fp, args)


def _overlay_plan(proj, plan_img: Path, render: Path, ortho: dict, opacity: float):
    W, H = ortho["resolution"]
    cx, cy = ortho["center"]
    k = max(W, H) / ortho["ortho_scale"]

    def to_px(x, y):
        return (W / 2 + (x - cx) * k, H / 2 - (y - cy) * k)

    from .preview import _image_size
    pw, ph = _image_size(plan_img)
    src = [(0, 0), (pw, 0), (0, ph)]
    dst = [to_px(*p) for p in proj.plan_frame.to_site(src)]
    pairs = " ".join(f"{s[0]},{s[1]} {d[0]:.2f},{d[1]:.2f}" for s, d in zip(src, dst))
    subprocess.run(["magick", str(render), "(", str(plan_img), "-alpha", "set", "-channel", "A", "-evaluate",
                    "multiply", str(opacity), "+channel", "-virtual-pixel", "transparent",
                    "-define", f"distort:viewport={W}x{H}+0+0", "-distort", "Affine", pairs, ")",
                    "-compose", "over", "-composite", str(render)], check=True)


# ---------------------------------------------------------------- hand edits


def cmd_open(args):
    """Open the model in Blender to look around or nudge things by hand."""
    prep = _prepare(args, detail="draft")
    fp, path, _ = _model(prep)
    exe = blender_bridge.blender_exe()
    subprocess.Popen([exe, str(path)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"opening {path} in Blender.\n"
          "  Move, rotate, resize or delete plants and other elements (select an element's main\n"
          "  object — its parts follow), then Ctrl+S and run:\n"
          f"    lv pull {prep.project.dir.name}" + (f" --variant {args.variant}" if args.variant else "")
          + "\n  Until you pull, renders refuse to use the edited model.")


def cmd_pull(args):
    proj = project_mod.load(args.project)
    store = ModelStore(proj)
    edited = [p.stem for p in store.dir.glob("*.blend") if store.is_edited(p.stem)]
    if not edited:
        print("no hand-edited models found — nothing to pull")
        return
    aside = store.dir / "edited"
    aside.mkdir(exist_ok=True)
    for fp in edited:
        meta = store.meta(fp)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if args.discard:
            shutil.move(store.path(fp), aside / f"{fp}-{stamp}-discarded.blend")
            store.meta_path(fp).unlink()
            print(f"discarded hand edits in model {fp}")
            continue
        scene_copy = store.dir / f"{fp}.scene.json"
        scene = json.loads(scene_copy.read_text())
        # The edits are relative to the design the model was built from; refuse if it changed.
        target = project_mod.load(args.project, scene["project"]["variant"],
                                  meta.get("sets", []) + [f"render.quality={meta['tier']}"])
        if fingerprint(resolve(target)) != fp and not args.force:
            sys.exit(f"error: the design changed since model {fp} was built, so its hand edits may not line up.\n"
                     "  Re-open with `lv open`, redo the edits, or pass --force to pull anyway.")
        res = store.use(fp, scene_copy, ["pull"], "cpu", "pull")
        patches, notes = edits.patches_from_report(target, scene, res["pull"])
        variant = args.variant
        if patches:
            edits.write(proj, patches, variant)
        where = f"variant {variant!r}" if variant else "the base design (inherited by every variant)"
        print(f"model {fp}: {len(patches)} element(s) changed → {project_mod.EDITS_FILE} for {where}")
        for eid, p in patches.items():
            print(f"   {eid}: {json.dumps(p)}")
        for n in notes:
            print("   note:", n)
        # The edited file is no longer reproducible from the design files — move it aside.
        shutil.move(store.path(fp), aside / f"{fp}-{stamp}.blend")
        store.meta_path(fp).unlink()
    print("next render rebuilds the model with these edits, and every view picks them up.")


# ---------------------------------------------------------------- browsing / housekeeping


def cmd_gallery(args):
    proj = project_mod.load(args.project)
    print("gallery →", _refresh_gallery(proj))


def cmd_serve(args):
    proj = project_mod.load(args.project)
    _refresh_gallery(proj)
    print(f"serving {proj.renders} → http://localhost:{args.port}/   (Ctrl+C to stop)")
    subprocess.run([sys.executable, "-m", "http.server", str(args.port), "--directory", str(proj.renders)])


def cmd_clean(args):
    proj = project_mod.load(args.project)
    store = ModelStore(proj)
    keep = {o["fingerprint"] for o in outputs.load_manifest(proj)["outputs"].values()}
    keep |= {p.stem for p in store.dir.glob("*.blend") if store.is_edited(p.stem)}  # never drop unpulled edits
    removed = store.prune(keep)
    print(f"removed {len(removed)} file(s) for models no output refers to")


def cmd_plan_import(args):
    proj = project_mod.load(args.project)
    out = ingest.import_plan(Path(args.file), proj.dir / "plan", args.page, args.dpi)
    for p in out:
        print("plan page →", p)
    print("next: set plan.image and plan.calibration in project.yaml "
          "(two pixel points with known site coordinates, or drawing_scale + dpi)")


def cmd_media_ingest(args):
    proj = project_mod.load(args.project)
    summary = ingest.ingest_media(Path(args.source), proj.derived / "scan" / "images", args.fps)
    print(json.dumps(summary, indent=2))


def cmd_reconstruct(args):
    if args.status:
        for k, v in reconstruct.status().items():
            print(f"{k:8} {v}")
        return
    if not args.project:
        sys.exit("error: project required")
    proj = project_mod.load(args.project)
    res = reconstruct.run(args.engine, proj.derived / "scan" / "images", proj.derived / "scan", args.quality)
    print(json.dumps(res, indent=2))
    print("next: add a `mesh` element with role: terrain pointing at the mesh, with alignment control points")


def cmd_species(args):
    cat = project_mod.load(args.project).catalog if args.project else Catalog.builtin()
    items = cat.search(args.query) if args.query else sorted(cat.species.values(), key=lambda s: (s.category, s.id))
    for s in items:
        print(f"{s.id:24} {s.category:11} {s.form:17} {s.height / 0.3048:5.0f}ft × {s.spread / 0.3048:3.0f}ft  "
              f"{s.common} ({s.botanical})")


def cmd_doctor(args):
    print("python      ", sys.version.split()[0])
    try:
        exe = blender_bridge.blender_exe()
        print("blender     ", blender_bridge.blender_version(), f"({exe})")
    except blender_bridge.BlenderError as e:
        print("blender      MISSING:", e)
    for tool in ("pdftoppm", "magick", "ffmpeg", "virsh"):
        print(f"{tool:12}", shutil.which(tool) or "missing")
    vms = blender_bridge.running_vms()
    print("gpu         ", "in use by VM: " + ", ".join(vms) if vms else "available to host")
    for k, v in reconstruct.status().items():
        print(f"recon/{k:6}", v)


# ---------------------------------------------------------------- parser


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lv", description="Landscape plan → one 3D model → every view")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def with_project(p, variant=True):
        p.add_argument("project", help="project name (in projects/) or path")
        if variant:
            p.add_argument("--variant", help="named variant (design option, season, …) from project.yaml")
            p.add_argument("--set", action="append", metavar="KEY=VALUE",
                           help="override any spec value, e.g. --set render.year=10")
        p.add_argument("-v", "--verbose", action="store_true")
        return p

    def state_flags(p):
        p.add_argument("--output-type", help=argparse.SUPPRESS)  # set by the app: groups outputs by type
        p.add_argument("--extra-view", dest="extra_views", action="append", type=json.loads,
                       help="JSON view to add if the design lacks one with that name")
        p.add_argument("--year", help="growth year after planting, or 'mature'")
        p.add_argument("--season", choices=["spring", "summer", "fall", "winter"])
        p.add_argument("--time", help="HH:MM, or sunrise/morning/noon/afternoon/golden/sunset/dusk/night "
                                      "(lighting only — same model)")
        p.add_argument("--date", help="YYYY-MM-DD (overrides season)")

    devices = ["auto", "gpu", "optix", "cuda", "cpu"]

    p = sub.add_parser("new", help="create a project from the template")
    p.add_argument("name")
    p.add_argument("--title")
    p.set_defaults(fn=cmd_new)

    p = with_project(sub.add_parser("validate", help="check the design files and print a summary"))
    state_flags(p)
    p.set_defaults(fn=cmd_validate)

    p = with_project(sub.add_parser("preview", help="diagnostic 2D drawing with labels/legend (no Blender)"))
    p.add_argument("--no-plan", action="store_true", help="omit the plan image underlay")
    p.add_argument("--width", type=int, default=1800)
    state_flags(p)
    p.set_defaults(fn=cmd_preview)

    p = with_project(sub.add_parser("build", help="build (or reuse) the saved model for a design state"))
    p.add_argument("--quality", choices=list(QUALITY_PRESETS), help="detail level")
    state_flags(p)
    p.set_defaults(fn=cmd_build)

    p = with_project(sub.add_parser("render", help="still images from the model"))
    p.add_argument("--view", action="append", help="view name(s); default all stills")
    p.add_argument("--quality", choices=list(QUALITY_PRESETS))
    p.add_argument("--samples", type=int)
    p.add_argument("--resolution", help="WxH, e.g. 2560x1440")
    p.add_argument("--device", default="auto", choices=devices)
    state_flags(p)
    p.set_defaults(fn=cmd_render)

    p = with_project(sub.add_parser("animate", help="orbit/flythrough videos from the model"))
    p.add_argument("--view", action="append")
    p.add_argument("--seconds", type=float, help="override clip length")
    p.add_argument("--fps", type=int, help="override frame rate")
    p.add_argument("--quality", choices=list(QUALITY_PRESETS))
    p.add_argument("--resolution", help="WxH")
    p.add_argument("--device", default="auto", choices=devices)
    state_flags(p)
    p.set_defaults(fn=cmd_animate)

    p = with_project(sub.add_parser("export", help="browser walkthrough (glTF + viewer) from the model"))
    state_flags(p)
    p.set_defaults(fn=cmd_export)

    p = with_project(sub.add_parser("report", help="presentation PDF + plant schedule CSV"))
    p.add_argument("--what", action="append", choices=["pdf", "csv"])
    state_flags(p)
    p.set_defaults(fn=cmd_report)

    p = with_project(sub.add_parser("model", help="save the 3D model file (.blend) as an output"))
    p.add_argument("--quality", choices=list(QUALITY_PRESETS))
    state_flags(p)
    p.set_defaults(fn=cmd_model)

    p = with_project(sub.add_parser("check", help="top-down render of the model with the plan drawing overlaid"))
    p.add_argument("--opacity", type=float, default=0.5)
    p.add_argument("--width", type=int, default=1800)
    p.add_argument("--device", default="auto", choices=devices)
    state_flags(p)
    p.set_defaults(fn=cmd_check)

    p = with_project(sub.add_parser("open", help="open the model in Blender for hand edits"))
    state_flags(p)
    p.set_defaults(fn=cmd_open)

    p = sub.add_parser("pull", help="carry hand edits made in Blender into edits.yaml")
    p.add_argument("project")
    p.add_argument("--variant", help="record the edits for this variant only (default: base design)")
    p.add_argument("--discard", action="store_true", help="throw hand edits away instead")
    p.add_argument("--force", action="store_true", help="pull even if the design changed since the model was built")
    p.set_defaults(fn=cmd_pull)

    p = sub.add_parser("gallery", help="rebuild the gallery page")
    p.add_argument("project")
    p.set_defaults(fn=cmd_gallery)

    p = sub.add_parser("serve", help="browse the gallery and walkthroughs in a web browser")
    p.add_argument("project")
    p.add_argument("--port", type=int, default=PORT)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("clean", help="delete saved models no output refers to")
    p.add_argument("project")
    p.set_defaults(fn=cmd_clean)

    plan = sub.add_parser("plan", help="landscaper plan tools").add_subparsers(dest="plan_cmd", required=True)
    p = plan.add_parser("import", help="rasterize a plan PDF/image into plan/")
    p.add_argument("project")
    p.add_argument("file")
    p.add_argument("--page", type=int)
    p.add_argument("--dpi", type=int, default=150)
    p.set_defaults(fn=cmd_plan_import)

    media = sub.add_parser("media", help="drone/site photo tools").add_subparsers(dest="media_cmd", required=True)
    p = media.add_parser("ingest", help="collect photos / extract video frames")
    p.add_argument("project")
    p.add_argument("source", help="folder or video file")
    p.add_argument("--fps", type=float, default=1.0, help="frames per second to sample from video")
    p.set_defaults(fn=cmd_media_ingest)

    p = sub.add_parser("reconstruct", help="photogrammetry: photos → textured mesh")
    p.add_argument("project", nargs="?")
    p.add_argument("--engine", default="odm", choices=list(reconstruct.ENGINES))
    p.add_argument("--quality", default="high", choices=["fast", "high", "ultra"])
    p.add_argument("--status", action="store_true", help="just report which engines are usable")
    p.set_defaults(fn=cmd_reconstruct)

    p = sub.add_parser("species", help="list/search the plant catalog")
    p.add_argument("query", nargs="?")
    p.add_argument("--project", help="include a project's local species.yaml")
    p.set_defaults(fn=cmd_species)

    sub.add_parser("doctor", help="check tools and GPU availability").set_defaults(fn=cmd_doctor)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except (project_mod.SpecError, blender_bridge.BlenderError, ingest.IngestError,
            reconstruct.ReconstructError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
