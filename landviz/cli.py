"""landviz command line.  Run `./lv --help` from the repo root.

Every output command follows the same path:
    project.yaml (+ edits.yaml, variant, --set)  →  resolved scene  →  saved model (built once,
    keyed by fingerprint)  →  output taken from that model  →  manifest + gallery
"""
from __future__ import annotations

import argparse
import hashlib
import math
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

import numpy as np
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


def cmd_photos(args):
    """Photo metadata table; with --project, site positions and (with --anchor) geo-anchoring + scale check."""
    from . import photos
    files = []
    for f in args.files:
        p = Path(f)
        files += sorted(x for x in p.iterdir() if x.suffix.lower() in (".jpg", ".jpeg")) if p.is_dir() else [p]
    proj = project_mod.load(args.project) if args.project else None
    origin, north = (None, 0.0)
    if proj:
        from .resolve import site_origin
        origin, north = site_origin(proj)
    rows = [photos.read(f) for f in files]
    lon_sign = -1 if (origin and origin[1] < 0) or args.west else 1
    for r in rows:
        if r.get("lon_ref_missing") and "lon" in r:
            r["lon"] = abs(r["lon"]) * lon_sign
    ref_alt = None
    if args.ground:
        ref_alt = photos.read(args.ground).get("alt")
    print(f"{'photo':42} {'time':8} {'lat':>10} {'lon':>11} {'alt m':>8} {'H m':>6} {'f35':>4} {'hFOV':>5} "
          f"{'yaw':>6} {'pitch':>6}" + ("   site x,y (m)" if origin else ""))
    for r in rows:
        H = (r["alt"] - ref_alt) if ref_alt is not None and r.get("alt") is not None else r.get("rel_alt")
        hf, _ = photos.fov_deg(r)
        line = (f"{Path(r['file']).name[:42]:42} {str(r.get('datetime', ''))[11:19]:8} {r.get('lat', 0):10.6f} "
                f"{r.get('lon', 0):11.6f} {r.get('alt', 0):8.1f} {H if H is not None else float('nan'):6.1f} "
                f"{r.get('f35') or 0:4} {hf:5.1f} {r.get('gimbal_yaw', float('nan')):6.1f} "
                f"{r.get('gimbal_pitch', float('nan')):6.1f}")
        if origin and "lat" in r:
            x, y = photos.latlon_to_site(r["lat"], r["lon"], origin, north)
            line += f"   {x:7.1f},{y:7.1f}"
        print(line)
    if any(r.get("lon_ref_missing") for r in rows):
        print("note: these files have no E/W, N/S markers; longitude sign taken from the site origin"
              + (" (or --west)" if not origin else ""))
    if not any("gimbal_yaw" in r for r in rows):
        print("note: no DJI gimbal angles (edited/exported files drop them) — photo matches need yaw/pitch in the design")
    if args.anchor:
        if not proj or not proj.plan_frame:
            sys.exit("--anchor needs --project with a calibrated plan image (the straight-down photo)")
        a = photos.read(args.anchor)
        if a.get("lon_ref_missing"):
            a["lon"] = abs(a["lon"]) * lon_sign
        w, h = a["width"], a["height"]
        sx, sy = proj.plan_frame.to_site([(w / 2, h / 2)])[0]
        o = photos.site_to_latlon(-sx, -sy, (a["lat"], a["lon"]), north)
        print(f"\nanchor: the camera of {Path(args.anchor).name} is above plan pixel ({w / 2:.0f}, {h / 2:.0f}) = site "
              f"({sx:.2f}, {sy:.2f}) m\n  → site.location.origin: [{o[0]:.7f}, {o[1]:.7f}]   (lat/lon of site 0,0)")
        Hn = (a["alt"] - ref_alt) if ref_alt is not None else a.get("rel_alt")
        if Hn:
            hf, _ = photos.fov_deg(a)
            gsd = 2 * Hn * math.tan(math.radians(hf) / 2) / w
            plan = proj.plan_frame.meters_per_pixel
            print(f"  scale check: plan calibration {plan * 100:.2f} cm/px vs camera geometry {gsd * 100:.2f} cm/px "
                  f"(height {Hn:.1f} m, hFOV {hf:.1f}°) → ratio {plan / gsd:.2f}")
            if abs(plan / gsd - 1) > 0.15:
                print("  ⚠ these disagree by more than 15%: either the plan calibration or the photo's lens/height "
                      "data is off (edited exports may be cropped). Check against a measured distance or LiDAR.")


def cmd_photomatch(args):
    """Render the model from each photo's camera and compare (photo | model | overlay | edges)."""
    prep = _prepare(args, detail=getattr(args, "quality", None) or "draft")
    pv = prep.scene.get("photo_views") or []
    for i in prep.project.issues:
        if i.where.startswith("photo_matches"):
            print(i)
    if not pv:
        sys.exit("no usable photo matches — add `photo_matches` to the design (see docs/SPEC.md)")
    scene = json.loads(json.dumps(prep.scene))
    scene["views"] = pv
    sp = prep.scene_path.with_name(prep.scene_path.stem + "__photomatch.json")
    sp.write_text(json.dumps(scene))
    prep.scene_path.write_text(json.dumps(prep.scene, indent=1))
    fp, _path, _ = _model(prep)
    out_dir = outputs.state_dir(prep.project, prep.state) / "photo-match"
    out_dir.mkdir(exist_ok=True)
    renders = {v["name"]: str(out_dir / f"{v['name']}.model.png") for v in pv}
    prep.store.use(fp, sp, ["render"], blender_bridge.pick_device(getattr(args, "device", "auto")), "photomatch",
                   views=list(renders), outputs=renders)
    produced = []
    for v in pv:
        model = Path(renders[v["name"]])
        if not model.exists():
            continue
        out = out_dir / f"{v['name']}.png"
        _photo_compare(Path(v["photo"]), model, out, v["resolution"])
        produced.append((f"match-{v['name']}", "still", out))
        print("photo match →", out)
    _finish(prep, produced, fp, args)


def _photo_compare(photo: Path, model: Path, out: Path, res):
    """2×2 sheet: photo | model / 50% overlay | model edges (red) on the photo. All at the photo's size."""
    W, H = res
    tmp = out.with_suffix(".tmp")
    tmp.mkdir(exist_ok=True)
    a, m, e, c, d = (tmp / f"{k}.png" for k in "amecd")
    fit = ["-resize", f"{W}x{H}!"]
    subprocess.run(["magick", str(photo), "-auto-orient", *fit, str(a)], check=True)
    subprocess.run(["magick", str(model), *fit, str(m)], check=True)
    subprocess.run(["magick", str(m), "-colorspace", "Gray", "-canny", "0x1+8%+25%", "-morphology", "Dilate",
                    "Disk:1", str(e)], check=True)
    subprocess.run(["magick", str(a), str(m), "-compose", "blend", "-define", "compose:args=50", "-composite",
                    str(c)], check=True)
    subprocess.run(["magick", str(a), "(", "-size", f"{W}x{H}", "xc:#ff2a2a", ")", str(e), "-composite", str(d)],
                   check=True)
    size = str(max(18, W // 40))

    def label(path, text):
        subprocess.run(["magick", str(path), "-gravity", "NorthWest", "-fill", "white", "-undercolor", "#000a",
                        "-pointsize", size, "-annotate", "+10+8", f" {text} ", str(path)], check=True)
    for path, text in ((a, "Photo"), (m, "Model"), (c, "50% overlay"), (d, "Model edges on photo")):
        label(path, text)
    subprocess.run(["magick", "(", str(a), str(m), "+append", ")", "(", str(c), str(d), "+append", ")",
                    "-append", "-resize", "2400x>", "-quality", "88", str(out)], check=True)
    shutil.rmtree(tmp, ignore_errors=True)


def _lidar_location(args, proj):
    """Where to fetch: --latlon, --address, the design's origin/location, or the photos' GPS."""
    from . import lidar, photos
    from .resolve import site_origin
    if args.latlon:
        ll = photos.parse_latlon(args.latlon)
        if not ll:
            sys.exit(f"couldn't read coordinates from {args.latlon!r}")
        return ll[0], ll[1], f"coordinates {ll[0]:.6f}, {ll[1]:.6f}"
    if args.address:
        lat, lon, matched = lidar.geocode(args.address)
        return lat, lon, matched
    origin, _ = site_origin(proj)
    if origin:
        return origin[0], origin[1], "site.location.origin"
    gps = []
    for f in list(proj.dir.rglob("*.jpg")) + list(proj.dir.rglob("*.JPG")):
        if "renders" in f.parts or "derived" in f.parts:
            continue
        info = photos.read(f)
        if "lat" in info:
            gps.append((info["lat"], info["lon"], info.get("lon_ref_missing")))
    loc = (proj.spec.get("site", {}) or {}).get("location", {}) or {}
    if gps:
        lat = sum(g[0] for g in gps) / len(gps)
        lon = sum(g[1] for g in gps) / len(gps)
        if any(g[2] for g in gps) and float(loc.get("lon", -1)) < 0:
            lon = -abs(lon)
        return lat, lon, f"GPS of {len(gps)} project photos"
    if "lat" in loc and "lon" in loc:
        return float(loc["lat"]), float(loc["lon"]), "site.location (may be approximate)"
    sys.exit("no location: pass --address or --latlon, or add photos with GPS / site.location.origin")


def cmd_aerial(args):
    from . import aerial, units
    proj = project_mod.load(args.project)
    if args.aerial_cmd == "fetch":
        lat, lon, how = _lidar_location(args, proj)
        print(f"location: {lat:.6f}, {lon:.6f}  ({how})")
        meta = aerial.fetch(proj.dir, lat, lon, args.radius, args.res, place=how)
        print(f"aerial photo → {aerial.image_path(proj.dir)} ({meta['size'][0]} px, {meta['m_per_px']:.2f} m/px)")
        proj = project_mod.load(args.project)
    meta = aerial.load(proj.dir)
    if meta is None:
        sys.exit("no aerial photo yet — run `lv aerial fetch` first")
    if args.aerial_cmd == "fetch" or args.aerial_cmd == "info":
        w, s_, e, n = meta["bbox"]
        print(f"aerial photo: {meta['source']}, fetched {meta['fetched']}, {meta['size'][0]}×{meta['size'][1]} px "
              f"at {meta['m_per_px']:.2f} m/px, bounds lat {s_:.6f}–{n:.6f}, lon {w:.6f}–{e:.6f}")
        if proj.aerial_frame is None:
            c = meta["center"]
            print("  The site isn't geo-referenced yet. Set site.location.origin to the lat/lon of site (0, 0), e.g.\n"
                  f"    origin: [{c[0]:.6f}, {c[1]:.6f}]   # the photo's center\n"
                  "  then author points in aerial pixels with `frame: aerial` (or convert with `lv aerial locate`).")
        else:
            print("  frame: aerial is available (points in aerial-photo pixels, x right, y down).")
    elif args.aerial_cmd == "locate":
        k = units.TO_METERS[proj.units]
        for spec in args.px:
            px, py = (float(v) for v in spec.split(","))
            lat, lon = aerial.px_to_latlon(meta, px, py)
            line = f"px {px:.0f},{py:.0f} → lat/lon {lat:.6f}, {lon:.6f}"
            if proj.aerial_frame is not None:
                x, y = proj.aerial_frame.to_site([(px, py)])[0]
                line += f" → site {x / k:.2f}, {y / k:.2f} {proj.units}"
            print(line)


def cmd_map(args):
    """Top-down 2D map of chosen elements over a backdrop, with a grid in project units."""
    from . import sitemap
    prep = _prepare(args)
    focus = None
    if args.focus:
        focus = {f.strip() for f in args.focus.split(",") if f.strip()}
    if args.kinds:
        kinds = {k.strip() for k in args.kinds.split(",")}
        focus = (focus or set()) | {el.get("id") for el in prep.project.spec.get("elements") or []
                                    if isinstance(el, dict) and el.get("kind") in kinds}
    out = prep.project.derived / f"map_{prep.state}{'_' + args.name if args.name else ''}.png"
    sitemap.render_map(prep.project, prep.scene, out, focus, args.backdrop, args.title or "",
                       show_inventory=args.inventory, width=args.width)
    bds = [b["key"] for b in sitemap.backdrops(prep.project, prep.scene)]
    print(f"map → {out}")
    print(f"  backdrops available: {', '.join(bds) or 'none'} (choose with --backdrop)")


def cmd_lidar(args):
    from . import lidar
    proj = project_mod.load(args.project)
    ldir = proj.derived / "lidar"
    if args.lidar_cmd == "fetch":
        lat, lon, how = _lidar_location(args, proj)
        print(f"location: {lat:.6f}, {lon:.6f}  ({how})")
        meta = lidar.fetch(ldir, lat, lon, args.radius, args.res, points=not args.no_points, place=how)
        _lidar_summary(proj, ldir)
        return
    if not (ldir / "lidar.npz").exists():
        sys.exit("no LiDAR data for this project yet — run `lv lidar fetch` first")
    if args.lidar_cmd == "info":
        _lidar_summary(proj, ldir)
    elif args.lidar_cmd == "heights":
        _lidar_heights(proj, ldir, args.at)
    elif args.lidar_cmd == "image":
        print("height map →", _lidar_image(proj, ldir, args.extent))


def _lidar_summary(proj, ldir):
    from . import lidar
    grids, meta = lidar.load(ldir)
    dem = grids["dem"]
    span = 2 * meta["radius"]
    print(f"LiDAR data: {span:.0f} m × {span:.0f} m around {meta['center'][0]:.6f}, {meta['center'][1]:.6f} "
          f"at {meta['res']} m")
    print(f"  ground: {np.nanmin(dem):.1f}–{np.nanmax(dem):.1f} m elevation (relief {np.nanmax(dem) - np.nanmin(dem):.1f} m)")
    pc = meta.get("point_cloud")
    if pc:
        nd = grids["dsm"] - dem
        print(f"  point cloud: {pc['dataset']} ({pc['returns']:,} returns) — buildings and trees measured")
        print(f"  tallest feature {np.nanmax(nd):.1f} m above ground; building cells {int(grids['building'].sum())}, "
              f"vegetation cells {int(grids['vegetation'].sum())}")
    else:
        print(f"  point cloud: none ({meta.get('point_cloud_error', 'not requested')}) — terrain only")


def _lidar_heights(proj, ldir, points):
    from . import lidar, units
    from .resolve import site_origin
    grids, meta = lidar.load(ldir)
    origin, north = site_origin(proj)
    if origin is None:
        sys.exit("set site.location.origin first (lat/lon of site 0,0) — e.g. from `lv photos --anchor`")
    k = units.TO_METERS[proj.units]
    e0, n0 = lidar.site_to_local(0.0, 0.0, meta, origin, north)
    z0 = float(lidar.sample(grids["dem"], meta, [e0], [n0])[0])
    print(f"{'x':>8} {'y':>8}   ground z ({proj.units})   height above ground ({proj.units})")
    for spec in points:
        x, y = (float(v) for v in spec.split(","))
        E, N = lidar.site_to_local(x * k, y * k, meta, origin, north)
        g = float(lidar.sample(grids["dem"], meta, [E], [N])[0])
        line = f"{x:8.1f} {y:8.1f}   {(g - z0) / k:10.2f}"
        if "dsm" in grids:
            # highest return within ~1 m (roof peaks and treetops are small targets)
            r, res, n = meta["radius"], meta["res"], meta["size"]
            i, j = int(round((float(N) + r) / res)), int(round((float(E) + r) / res))
            win = grids["dsm"][max(0, i - 1):i + 2, max(0, j - 1):j + 2]
            line += f"   {(np.nanmax(win) - g) / k:10.2f}"
        print(line)


def _lidar_image(proj, ldir, extent):
    """Top-down height map in the site frame with a labeled grid (project units), for reading
    building outlines, roof ridges and tree crowns at true scale — no photo lean."""
    from . import lidar, units
    from .resolve import site_origin
    grids, meta = lidar.load(ldir)
    origin, north = site_origin(proj)
    if origin is None:
        sys.exit("set site.location.origin first (lat/lon of site 0,0)")
    k = units.TO_METERS[proj.units]
    if extent:
        x0, y0, x1, y1 = (float(v) * k for v in extent.split(","))
    else:
        from . import geometry
        from .resolve import _boundary
        bx0, by0, bx1, by1 = geometry.bbox(_boundary(proj))
        pad = 0.25 * max(bx1 - bx0, by1 - by0)
        x0, y0, x1, y1 = bx0 - pad, by0 - pad, bx1 + pad, by1 + pad
    px = 0.1  # 10 cm per pixel
    W, H = int((x1 - x0) / px), int((y1 - y0) / px)
    xs = x0 + (np.arange(W) + 0.5) * px
    ys = y1 - (np.arange(H) + 0.5) * px
    X, Y = np.meshgrid(xs, ys)
    E, N = lidar.site_to_local(X.ravel(), Y.ravel(), meta, origin, north)
    dem = lidar.sample(grids["dem"], meta, E, N).reshape(H, W)
    if "dsm" in grids:
        hag = np.clip(lidar.sample(grids["dsm"], meta, E, N).reshape(H, W) - dem, 0, None)
    else:
        hag = dem - np.nanmin(dem)
    # colour by height (0 → dark, 12 m+ → bright), shaded by the surface slope
    top = max(3.0, float(np.nanpercentile(hag, 99.5)))
    t = np.clip(hag / top, 0, 1)
    gy, gx = np.gradient(dem + (hag if "dsm" in grids else 0), px)
    shade = np.clip(0.65 + 0.35 * (-gx * 0.7 + gy * 0.7) / np.sqrt(1 + gx ** 2 + gy ** 2), 0.3, 1)
    ramp = np.array([[20, 30, 45], [40, 110, 70], [190, 190, 60], [230, 120, 40], [250, 240, 230]], float)
    pos = t * (len(ramp) - 1)
    lo = np.floor(pos).astype(int).clip(0, len(ramp) - 2)
    f = (pos - lo)[..., None]
    rgb = (ramp[lo] * (1 - f) + ramp[lo + 1] * f) * shade[..., None]
    img = rgb.clip(0, 255).astype(np.uint8)
    out = ldir / "height-map.png"
    raw = ldir / "height-map.rgb"
    raw.write_bytes(img.tobytes())
    step = 10 if proj.units == "ft" else 5  # grid every 10 ft / 5 m
    draw = []
    for gxv in np.arange(math.ceil(x0 / k / step) * step, x1 / k, step):
        c = (gxv * k - x0) / px
        draw += ["-draw", f"line {c:.0f},0 {c:.0f},{H}"]
    for gyv in np.arange(math.ceil(y0 / k / step) * step, y1 / k, step):
        r = (y1 - gyv * k) / px
        draw += ["-draw", f"line 0,{r:.0f} {W},{r:.0f}"]
    labels = []
    for gxv in np.arange(math.ceil(x0 / k / step) * step, x1 / k, step * 2):
        labels += ["-annotate", f"+{(gxv * k - x0) / px + 4:.0f}+{H - 8}", f"x{gxv:g}"]
    for gyv in np.arange(math.ceil(y0 / k / step) * step, y1 / k, step * 2):
        labels += ["-annotate", f"+4+{(y1 - gyv * k) / px - 4:.0f}", f"y{gyv:g}"]
    subprocess.run(["magick", "-size", f"{W}x{H}", "-depth", "8", f"rgb:{raw}", "-stroke", "#ffffff55",
                    "-strokewidth", "1", *draw, "-stroke", "none", "-fill", "white", "-pointsize", "22", *labels,
                    "-gravity", "NorthWest", "-fill", "white", "-undercolor", "#000a", "-annotate", "+8+8",
                    f" height above ground: dark 0 → white {top:.0f} m · grid {step} {proj.units} (site coords) ",
                    str(out)], check=True)
    raw.unlink()
    return out


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

    p = sub.add_parser("photos", help="photo metadata (GPS, altitude, lens, gimbal) and geo-anchoring")
    p.add_argument("files", nargs="+", help="photos or folders")
    p.add_argument("--project", help="show site positions using the project's site.location.origin")
    p.add_argument("--ground", help="a photo taken on the ground: heights are reported relative to it")
    p.add_argument("--anchor", help="the straight-down photo used as the plan image: prints site.location.origin + a scale check")
    p.add_argument("--west", action="store_true", help="treat longitudes as west when the files lack E/W markers")
    p.set_defaults(fn=cmd_photos)

    p = with_project(sub.add_parser("photomatch", help="render the model from each photo's camera and compare"))
    p.add_argument("--quality", choices=list(QUALITY_PRESETS))
    p.add_argument("--device", default="auto", choices=devices)
    state_flags(p)
    p.set_defaults(fn=cmd_photomatch)

    lid = sub.add_parser("lidar", help="public USGS elevation / LiDAR data for the site").add_subparsers(
        dest="lidar_cmd", required=True)
    p = lid.add_parser("fetch", help="download measured terrain (+ roofs/trees if available)")
    p.add_argument("project")
    p.add_argument("--address", help="US street address (geocoded by the US Census)")
    p.add_argument("--latlon", help="coordinates, e.g. '40.5729, -105.1438'")
    p.add_argument("--radius", type=float, default=70.0, help="meters around the location (default 70)")
    p.add_argument("--res", type=float, default=1.0, help="grid spacing in meters (default 1)")
    p.add_argument("--no-points", action="store_true", help="terrain only (skip the point cloud)")
    p.set_defaults(fn=cmd_lidar)
    p = lid.add_parser("info", help="summary of the fetched data")
    p.add_argument("project")
    p.set_defaults(fn=cmd_lidar)
    p = lid.add_parser("heights", help="ground elevation and height above ground at site points")
    p.add_argument("project")
    p.add_argument("--at", action="append", required=True, metavar="X,Y", help="site coordinates (project units)")
    p.set_defaults(fn=cmd_lidar)
    p = lid.add_parser("image", help="top-down height map with a site grid (true scale)")
    p.add_argument("project")
    p.add_argument("--extent", help="x0,y0,x1,y1 in project units (default: boundary + margin)")
    p.set_defaults(fn=cmd_lidar)

    aer = sub.add_parser("aerial", help="public straight-down aerial photo (USGS NAIP) of the site").add_subparsers(
        dest="aerial_cmd", required=True)
    p = aer.add_parser("fetch", help="download the aerial photo around the site")
    p.add_argument("project")
    p.add_argument("--address", help="US street address (geocoded by the US Census)")
    p.add_argument("--latlon", help="coordinates, e.g. '40.5729, -105.1438'")
    p.add_argument("--radius", type=float, default=80.0, help="meters around the location (default 80)")
    p.add_argument("--res", type=float, default=0.3, help="meters per pixel requested (default 0.3)")
    p.set_defaults(fn=cmd_aerial)
    p = aer.add_parser("info", help="what was fetched and whether frame: aerial is usable")
    p.add_argument("project")
    p.set_defaults(fn=cmd_aerial)
    p = aer.add_parser("locate", help="aerial pixel → lat/lon and site coordinates")
    p.add_argument("project")
    p.add_argument("--px", action="append", required=True, metavar="X,Y", help="pixel position in aerial/aerial.jpg")
    p.set_defaults(fn=cmd_aerial)

    p = with_project(sub.add_parser("map", help="top-down 2D map of chosen elements over a backdrop (no Blender)"))
    p.add_argument("--focus", help="comma-separated element ids to highlight (default: all)")
    p.add_argument("--kinds", help="comma-separated element kinds to highlight, e.g. structure,deck")
    p.add_argument("--backdrop", help="aerial | plan | lidar | photo:<name> (default: the best available)")
    p.add_argument("--inventory", action="store_true", help="also draw the feature inventory")
    p.add_argument("--name", help="suffix for the output file")
    p.add_argument("--title")
    p.add_argument("--width", type=int, default=1800)
    state_flags(p)
    p.set_defaults(fn=cmd_map)

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
