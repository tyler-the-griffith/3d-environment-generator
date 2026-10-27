"""The top-down site map: backdrops, editable geometry, step maps for the AI, and writing edits back.

Everything here is 2D and Blender-free:

- `backdrops(project)`: the images that can sit under the map, each with the matrix that places
  its pixels in site meters: the public aerial photo, the plan image (the landscaper's drawing or
  a calibrated overhead photo), a LiDAR height map, and straight-down drone photos.
- `editable(project, scene)`: every element's authored geometry converted to site meters, with the
  path of the key it came from, so an editor can change it.
- `render_map(...)`: a labeled PNG of chosen elements over a backdrop with a coordinate grid in
  project units, so the AI can check one step in isolation (`lv map --focus`).
- `apply_edits(...)`: write geometry changes (in site meters) back into the design file in each
  element's own frame and units, touching nothing else (landviz.yamledit).
"""
from __future__ import annotations

import base64
import math
import struct
import zlib
from pathlib import Path

import numpy as np

from . import geometry, units
from .project import Project, SpecError

# Keys of each kind that hold points (authored in the element's frame).
POINT_KEYS = {"lawn": ["polygon"], "bed": ["polygon"], "paving": ["polygon"], "plant_group": ["polygon"],
              "deck": ["polygon"], "path": ["line"], "wall": ["line"], "fence": ["line"], "plant_row": ["line"],
              "plant": ["at"], "boulder": ["at", "polygon"], "light": ["at"], "structure": ["footprint"]}
SIZE_KEYS = {"plant": "spread", "boulder": "size"}


# ---------------------------------------------------------------- backdrops


def _matrix_from_frame(pf) -> list[float]:
    return [float(v) for v in pf.svg_matrix()]


def _frame_from_pairs(px, site):
    return geometry.PlanFrame(px, site)


def backdrops(project: Project, scene: dict | None = None) -> list[dict]:
    from . import aerial, preview
    out = []
    if project.aerial_frame is not None:
        meta = aerial.load(project.dir)
        out.append({"key": "aerial", "label": f"Aerial photo ({meta.get('source', 'NAIP')}, {meta.get('fetched', '')})",
                    "path": str(aerial.image_path(project.dir)), "size": meta["size"],
                    "matrix": _matrix_from_frame(project.aerial_frame)})
    plan = project.spec.get("plan") or {}
    if project.plan_frame is not None and plan.get("image") and project.path(plan["image"]).exists():
        img = project.path(plan["image"])
        out.append({"key": "plan", "label": "Plan image", "path": str(img), "size": list(preview._image_size(img)),
                    "matrix": _matrix_from_frame(project.plan_frame)})
    lid = lidar_backdrop(project)
    if lid:
        out.append(lid)
    for v in (scene or {}).get("photo_views") or []:
        if v.get("pitch_deg", 0) > -80 or v["position"][2] <= 1:
            continue
        m = _drone_matrix(v)
        if m:
            out.append({"key": f"photo:{v['name']}", "label": f"Drone photo {v['name']} (leans: tall things shift outward)",
                        "path": v["photo"], "size": m[1], "matrix": m[0]})
    return out


def _drone_matrix(v):
    """Approximate placement of a straight-down drone photo on the ground plane."""
    from . import photos
    info = photos.read(v["photo"])
    W, H = info.get("width"), info.get("height")
    if not W or not H:
        return None
    x, y, z = v["position"]
    m = 2 * z * math.tan(math.radians(v["hfov_deg"]) / 2) / W
    yaw = math.radians(v.get("yaw_deg", 0.0))
    up = (math.sin(yaw), math.cos(yaw))
    right = (math.cos(yaw), -math.sin(yaw))

    def site(px, py):
        a, b = (px - W / 2) * m, (H / 2 - py) * m
        return (x + a * right[0] + b * up[0], y + a * right[1] + b * up[1])
    corners = [(0, 0), (W, 0), (W, H), (0, H)]
    pf = _frame_from_pairs(corners, [site(*c) for c in corners])
    return _matrix_from_frame(pf), [W, H]


def lidar_backdrop(project: Project) -> dict | None:
    """A shaded height map from the LiDAR data, saved as a PNG beside it."""
    from . import lidar
    from .photos import M_PER_DEG_LAT, M_PER_DEG_LON_EQ, latlon_to_site
    from .resolve import site_origin
    ldir = project.dir / "derived" / "lidar"
    if not (ldir / "lidar.npz").exists():
        return None
    origin, north = site_origin(project)
    if origin is None:
        return None
    grids, meta = lidar.load(ldir)
    png = ldir / "heightmap.png"
    if not png.exists() or png.stat().st_mtime < (ldir / "lidar.npz").stat().st_mtime:
        _write_png(png, _shade(grids, meta))
    n, r = meta["size"], meta["radius"]
    lat0, lon0 = meta["center"]

    def to_site(E, N):
        lat = lat0 + N / M_PER_DEG_LAT
        lon = lon0 + E / (M_PER_DEG_LON_EQ * math.cos(math.radians(lat0)))
        return latlon_to_site(lat, lon, origin, north)
    # pixel (j, i) with i = 0 at the north edge
    px = [(0, 0), (n, 0), (n, n), (0, n)]
    site = [to_site(-r, r), to_site(r, r), to_site(r, -r), to_site(-r, -r)]
    pf = _frame_from_pairs(px, site)
    label = "LiDAR height map" + (" (roofs and trees measured)" if "dsm" in grids else " (ground only)")
    return {"key": "lidar", "label": label, "path": str(png), "size": [n, n], "matrix": _matrix_from_frame(pf)}


def _shade(grids, meta) -> np.ndarray:
    surf = grids.get("dsm", grids["dem"])
    surf = np.where(np.isfinite(surf), surf, np.nanmedian(surf))
    dem = np.where(np.isfinite(grids["dem"]), grids["dem"], np.nanmedian(grids["dem"]))
    res = meta["res"]
    gy, gx = np.gradient(surf, res)
    # light from the north-west, 45° up
    lx, ly, lz = -0.5, 0.5, 0.707
    nrm = np.sqrt(gx ** 2 + gy ** 2 + 1)
    shade = np.clip((-gx * lx - gy * ly + lz) / nrm, 0, 1)
    hag = np.clip(surf - dem, 0, 12) / 12  # height above ground → color
    base = np.stack([0.55 + 0.4 * hag, 0.62 + 0.1 * hag - 0.2 * hag ** 2, 0.5 - 0.3 * hag], -1)
    rel = (dem - dem.min()) / max(1e-6, dem.max() - dem.min())
    base = base * (0.85 + 0.15 * rel[..., None])
    img = np.clip(base * (0.35 + 0.75 * shade[..., None]), 0, 1)
    return (img[::-1] * 255).astype(np.uint8)  # row 0 = north


def _write_png(path: Path, rgb: np.ndarray):
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[i].tobytes() for i in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------- editable geometry


def _frame_of(project: Project, el: dict) -> str:
    return el.get("frame") or project.spec.get("defaults", {}).get("frame", "site")


def editable(project: Project, scene: dict) -> dict:
    """Site boundary and every element's geometry in site meters, keyed for editing."""
    site = project.spec.get("site") or {}
    out = {"units": project.units, "m_per_unit": units.TO_METERS[project.units],
           "boundary": [list(p[:2]) for p in project.pts(site["boundary"], site.get("frame"))] if site.get("boundary") else [],
           "elements": [], "inventory": []}
    recs: dict[str, list] = {}
    for r in scene["elements"]:
        recs.setdefault(r["source"], []).append(r)
    for el in project.spec.get("elements") or []:
        if not isinstance(el, dict) or "kind" not in el:
            continue
        kind, eid = el["kind"], el.get("id")
        frame = _frame_of(project, el)
        geoms = []
        try:
            for key in POINT_KEYS.get(kind, []):
                if key in el:
                    pts = project.pts([el[key]] if key == "at" else el[key], frame)
                    gtype = "point" if key == "at" else ("line" if key == "line" else "polygon")
                    geoms.append({"path": [key], "type": gtype, "pts": [list(p[:2]) for p in pts]})
            for i, w in enumerate(el.get("wings") or []):
                if "footprint" in w:
                    pts = project.pts(w["footprint"], frame)
                    geoms.append({"path": ["wings", i, "footprint"], "type": "polygon",
                                  "pts": [list(p[:2]) for p in pts], "label": w.get("name") or f"wing {i + 1}"})
        except (SpecError, TypeError, ValueError, IndexError):
            geoms = []
        rs = recs.get(eid, [])
        item = {"id": eid, "kind": kind, "frame": frame, "geoms": geoms, "enabled": el.get("enabled", True) is not False,
                "species": el.get("species"), "note": el.get("note"), "raw": _jsonable(el),
                "auto_id": eid in project.auto_ids}
        if kind in SIZE_KEYS and rs and "at" in el:
            r0 = rs[0]
            item["size_m"] = r0.get("spread") or r0.get("size")
        if kind == "structure" or kind == "deck":
            item["height_m"] = rs[0].get("height") if rs else None
        # Generated members (plant groups/rows, boulder fields) are drawn but edited through the parent.
        if kind in ("plant_group", "plant_row") or (kind == "boulder" and "polygon" in el):
            item["members"] = [{"x": r["x"], "y": r["y"], "r": (r.get("spread") or r.get("size") or 0.5) / 2}
                               for r in rs if "x" in r]
        if kind == "path":
            item["width_m"] = rs[0].get("width") if rs else None
        out["elements"].append(item)
    for i, it in enumerate(project.spec.get("inventory") or []):
        if not isinstance(it, dict):
            continue
        try:
            frame = it.get("frame") or project.spec.get("defaults", {}).get("frame", "site")
            g = {}
            if "polygon" in it:
                g = {"type": "polygon", "pts": [list(p[:2]) for p in project.pts(it["polygon"], frame)], "path": ["polygon"]}
            elif "at" in it:
                g = {"type": "point", "pts": [list(project.pt(it["at"], frame)[:2])], "path": ["at"]}
        except (SpecError, TypeError, ValueError):
            g = {}
        out["inventory"].append({"index": i, "id": it.get("id"), "category": it.get("category"), "label": it.get("label"),
                                 "element": it.get("element"), "note": it.get("note"), "frame": frame, "geom": g,
                                 "raw": _jsonable(it)})
    out["location"] = _jsonable(site.get("location") or {})
    out["terrain"] = _jsonable(site.get("terrain") or {"type": "flat"})
    out["photos"] = [{"index": v.get("index"), "name": v["name"], "at": list(v["position"][:2]), "z": v["position"][2],
                      "yaw": v.get("yaw_deg"), "pitch": v.get("pitch_deg"), "hfov": v.get("hfov_deg"),
                      "raw": _jsonable(((project.spec.get("photo_matches") or {}).get("photos") or [{}] * 999)[v.get("index") or 0])}
                     for v in scene.get("photo_views") or []]
    out["schema"] = schema(project)
    return out


def _jsonable(v):
    import json
    return json.loads(json.dumps(v, default=str))


def schema(project: Project) -> dict:
    """Choices the editor offers, straight from the element definitions (one source of truth)."""
    from .elements import linear, structures, surfaces
    sp = sorted(project.catalog.species.values(), key=lambda s: s.common.lower())
    return {
        "paving_materials": sorted(surfaces.PAVING_MATERIALS), "bed_surfaces": sorted(surfaces.BED_SURFACES),
        "edging": ["spade", "steel", "stone", "brick", "paver", "block", "none"],
        "wall_materials": sorted(linear.WALL_MATERIALS), "fence_styles": sorted(linear.FENCE_STYLES),
        "roof_types": sorted(structures.ROOF_TYPES), "light_types": ["path", "uplight"],
        "path_styles": ["", "stepping_stones"], "terrain_types": ["flat", "graded", "lidar", "scan"],
        "inventory_categories": ["house", "garage", "shed", "porch", "addition", "deck", "patio", "driveway", "walkway",
                                 "court", "pool", "play", "fence", "wall", "retaining_wall", "lawn", "bed", "gravel",
                                 "tree", "shrub", "hedge", "boulder", "utility", "other"],
        "species": [{"id": s.id, "common": s.common, "category": s.category} for s in sp],
    }


# ---------------------------------------------------------------- writing edits back


def _clean(v):
    return int(v) if abs(v - round(v)) < 1e-9 else v


def _keep_unmoved(project: Project, raw_pts, new_m, frame, tol=0.002):
    """Authored values for new points, reusing the original text for points that didn't move
    (so a round trip through meters never turns 84 into 83.999)."""
    out = project.to_authored(new_m, frame)
    try:
        old_m = project.pts(raw_pts, frame) if raw_pts else []
    except (SpecError, TypeError, ValueError):
        old_m = []
    for i, p in enumerate(new_m):
        for j, q in enumerate(old_m):
            if abs(p[0] - q[0]) < tol and abs(p[1] - q[1]) < tol:
                out[i] = raw_pts[j]
                break
    return [[_clean(v) if isinstance(v, float) else v for v in p] for p in out]


def _get(d, path):
    for k in path:
        d = d[k]
    return d


def apply_edits(project: Project, changes: list[dict]) -> list[str]:
    """Apply editor changes (site meters) to project.yaml, or to the variant's overlay file.

    change forms:
      {"id": eid, "path": [...], "pts": [[x, y], ...]}        geometry in site meters
      {"id": eid, "path": ["spread"], "length": m}              a length in meters
      {"id": eid, "delete": True}
      {"add": {...element..., <point key>: pts in site meters}}  added with frame: site
      {"site": "boundary", "pts": [...]}
      {"inventory": i, "path": [...], "pts": [...]}            an inventory item's rough outline
    Returns notes.
    """
    from . import yamledit as Y
    from .project import list_child_variants
    notes = []
    if project.variant and project.variant in list_child_variants(project.dir):
        return _apply_variant(project, changes)
    if project.variant:
        raise SpecError("quick variants defined inside project.yaml can't be edited on the map; edit the base")
    path = project.dir / "project.yaml"
    text = path.read_text()
    raw = {el.get("id"): (i, el) for i, el in enumerate(project.spec.get("elements") or []) if isinstance(el, dict)}
    k = units.TO_METERS[project.units]

    def el_path(eid):
        if eid in project.auto_ids:
            return ["elements", raw[eid][0]]
        return Y.element_path(text, eid)

    for ch in changes:
        if "rename" in ch:
            text = _rename(project, text, raw, ch["rename"]["from"], ch["rename"]["to"], el_path)
            notes.append(f"renamed {ch['rename']['from']} → {ch['rename']['to']}")
            raw[ch["rename"]["to"]] = raw.pop(ch["rename"]["from"])
            continue
        if "spec" in ch:  # a design-level setting, e.g. ["site", "location", "north_offset_deg"]
            text = Y.set_deep(text, list(ch["spec"]), ch["value"])
            continue
        if "replace" in ch:  # the whole element, edited as text
            el = dict(ch["replace"])
            if not el.get("kind"):
                raise SpecError("an element needs a kind")
            text = Y.set_value(text, el_path(ch["id"]), el)
            continue
        if "site" in ch:
            frame = (project.spec.get("site") or {}).get("frame")
            text = Y.set_value(text, ["site", "boundary"],
                               _keep_unmoved(project, (project.spec.get("site") or {}).get("boundary"), ch["pts"], frame))
        elif "inventory" in ch:
            it = (project.spec.get("inventory") or [])[ch["inventory"]]
            frame = it.get("frame") or project.spec.get("defaults", {}).get("frame", "site")
            if "value" in ch:
                text = Y.set_value(text, ["inventory", ch["inventory"], *ch["path"]], ch["value"])
                continue
            old = it.get(ch["path"][0])
            val = _keep_unmoved(project, [old] if ch["path"] == ["at"] else old, ch["pts"], frame)
            text = Y.set_value(text, ["inventory", ch["inventory"], *ch["path"]], val[0] if ch["path"] == ["at"] else val)
        elif "add" in ch:
            el = dict(ch["add"])
            for key in ("polygon", "line", "footprint"):
                if key in el:
                    el[key] = [[round(x / k, 3), round(y / k, 3)] for x, y in el[key]]
            if "at" in el:
                el["at"] = [round(el["at"][0] / k, 3), round(el["at"][1] / k, 3)]
            for w in el.get("wings") or []:  # duplicated buildings
                if "footprint" in w:
                    w["footprint"] = [[round(x / k, 3), round(y / k, 3)] for x, y in w["footprint"]]
            # (sizes and other fields arrive in project units already)
            if project.spec.get("defaults", {}).get("frame", "site") != "site":
                el["frame"] = "site"
            text = Y.append_item(text, ["elements"], el)
            notes.append(f"added {el.get('id')}")
        elif ch.get("delete"):
            text = Y.delete_item(text, el_path(ch["id"]))
            notes.append(f"removed {ch['id']}")
        else:
            eid = ch["id"]
            if eid not in raw:
                raise SpecError(f"no element {eid!r}")
            el = raw[eid][1]
            p = el_path(eid) + list(ch["path"])
            if "value" in ch:  # a field in project units / as authored (material, species, height…)
                text = Y.set_value(text, p, ch["value"])
                continue
            if "pts" in ch:
                try:
                    old = _get(el, ch["path"])
                except (KeyError, IndexError, TypeError):
                    old = None
                val = _keep_unmoved(project, [old] if ch["path"] == ["at"] and old else old, ch["pts"], _frame_of(project, el))
                text = Y.set_value(text, p, val[0] if ch["path"] == ["at"] else val)
            elif "length" in ch:
                text = Y.set_value(text, p, round(ch["length"] / k, 2))
    path.write_text(text)
    return notes


def _rename(project: Project, text: str, raw: dict, old: str, new: str, el_path) -> str:
    """Rename an element and every reference to it: inventory links, hand edits, child variants."""
    import re

    import yaml

    from . import yamledit as Y
    from .project import EDITS_FILE, list_child_variants, variant_file
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", new or ""):
        raise SpecError("names may use letters, digits, '-', '_' and '.', starting with a letter or digit")
    if new in raw:
        raise SpecError(f"there's already an element called {new!r}")
    if old not in raw:
        raise SpecError(f"no element {old!r}")
    text = Y.set_value(text, el_path(old) + ["id"], new)
    for i, it in enumerate(project.spec.get("inventory") or []):
        if isinstance(it, dict) and it.get("element") == old:
            text = Y.set_value(text, ["inventory", i, "element"], new)
    ef = project.dir / EDITS_FILE
    if ef.exists():
        et = ef.read_text()
        et = Y.rename_key(et, ["elements"], old, new) if Y.has_path(et, ["elements"]) else et
        for v in ((yaml.safe_load(et) or {}).get("variants") or {}):
            if Y.has_path(et, ["variants", v, "elements"]):
                et = Y.rename_key(et, ["variants", v, "elements"], old, new)
        ef.write_text(et)
    for name in list_child_variants(project.dir):
        vf = variant_file(project.dir, name)
        vt = vf.read_text()
        ov = yaml.safe_load(vt) or {}
        if Y.has_path(vt, ["elements_override"]):
            vt = Y.rename_key(vt, ["elements_override"], old, new)
        for j, rid in enumerate(ov.get("elements_remove") or []):
            if rid == old:
                vt = Y.set_value(vt, ["elements_remove", j], new)
        vf.write_text(vt)
    return text


def _apply_variant(project: Project, changes: list[dict]) -> list[str]:
    """Child variants: changes go into the overlay (elements_add patched in place, everything else as
    elements_override / elements_remove in site frame)."""
    import yaml

    from . import yamledit as Y
    from .project import variant_file
    vf = variant_file(project.dir, project.variant)
    text = vf.read_text()
    overlay = yaml.safe_load(text) or {}
    added = {el.get("id"): i for i, el in enumerate(overlay.get("elements_add") or []) if isinstance(el, dict)}
    merged = {el.get("id"): el for el in project.spec.get("elements") or [] if isinstance(el, dict)}
    k = units.TO_METERS[project.units]
    notes = []

    def site_units(pts):
        return [[round(x / k, 3), round(y / k, 3)] for x, y in pts]

    for ch in changes:
        if "site" in ch:
            text = Y.set_value(text, ["site"], {**(overlay.get("site") or {}), "boundary": site_units(ch["pts"]), "frame": "site"})
        elif "add" in ch:
            el = dict(ch["add"])
            for key in ("polygon", "line", "footprint"):
                if key in el:
                    el[key] = site_units(el[key])
            if "at" in el:
                el["at"] = site_units([el["at"]])[0]
            el["frame"] = "site"
            text = Y.append_item(text, ["elements_add"], el)
            notes.append(f"added {el.get('id')}")
        elif ch.get("delete"):
            if ch["id"] in added:
                text = Y.delete_item(text, ["elements_add", added[ch["id"]]])
            else:
                text = Y.append_item(text, ["elements_remove"], ch["id"])
            notes.append(f"removed {ch['id']}")
        elif "inventory" in ch or "spec" in ch or "rename" in ch:
            notes.append("that kind of change is made on the base design")
        elif "replace" in ch:
            eid = ch["id"]
            if eid in added:
                text = Y.set_value(text, ["elements_add", added[eid]], ch["replace"])
            else:
                overlay.setdefault("elements_override", {})[eid] = {k: v for k, v in ch["replace"].items() if k != "id"}
                text = Y.set_deep(text, ["elements_override", eid], overlay["elements_override"][eid])
        elif "value" in ch:
            eid = ch["id"]
            if eid in added:
                text = Y.set_value(text, ["elements_add", added[eid], *ch["path"]], ch["value"])
            else:
                cur = (overlay.get("elements_override") or {}).get(eid) or {}
                tgt = cur
                for key in ch["path"][:-1]:
                    tgt = tgt.setdefault(key, {}) if isinstance(key, str) else tgt
                tgt[ch["path"][-1]] = ch["value"]
                overlay.setdefault("elements_override", {})[eid] = cur
                text = Y.set_deep(text, ["elements_override", eid], cur)
        else:
            eid = ch["id"]
            el = merged[eid]
            if eid in added:
                p = ["elements_add", added[eid], *ch["path"]]
                if "pts" in ch:
                    val = project.to_authored(ch["pts"], _frame_of(project, el))
                    text = Y.set_value(text, p, val[0] if ch["path"] == ["at"] else val)
                else:
                    text = Y.set_value(text, p, round(ch["length"] / k, 2))
                continue
            # An inherited element: override every point key in site frame so frames never mix.
            ov = {"frame": "site"}
            frame = _frame_of(project, el)
            for key in POINT_KEYS.get(el["kind"], []):
                if key in el:
                    pts = [p[:2] for p in project.pts([el[key]] if key == "at" else el[key], frame)]
                    ov[key] = site_units(pts)[0] if key == "at" else site_units(pts)
            if el.get("wings"):
                ov["wings"] = [{**w, "footprint": site_units([p[:2] for p in project.pts(w["footprint"], frame)])}
                               if "footprint" in w else w for w in el["wings"]]
            if "pts" in ch:
                val = site_units(ch["pts"])
                tgt = ov
                for key in ch["path"][:-1]:
                    tgt = tgt[key]
                tgt[ch["path"][-1]] = val[0] if ch["path"] == ["at"] else val
            else:
                ov[ch["path"][-1]] = round(ch["length"] / k, 2)
            current = (overlay.get("elements_override") or {}).get(eid) or {}
            overlay.setdefault("elements_override", {})[eid] = {**current, **ov}
            if _has(text, ["elements_override"]):
                text = Y.set_value(text, ["elements_override", eid], overlay["elements_override"][eid])
            else:
                text = Y.set_value(text, ["elements_override"], overlay["elements_override"])
    vf.write_text(text)
    return notes


def _has(text, path):
    from . import yamledit as Y
    try:
        node, _, _ = Y._node_at(Y._compose(text), path)
        return node is not None
    except Y.YamlEditError:
        return False


# ---------------------------------------------------------------- step maps (for the AI)


def render_map(project: Project, scene: dict, out_png: Path, focus_ids: set | None = None,
               backdrop: str | None = None, title: str = "", show_inventory: bool = False,
               width: int = 1800) -> Path:
    """Labeled top-down map: focused elements bold over a backdrop, others faint, grid in project units."""
    from . import preview
    data = editable(project, scene)
    bds = {b["key"]: b for b in backdrops(project, scene)}
    bd = bds.get(backdrop) if backdrop else next((bds[k] for k in ("aerial", "plan", "lidar") if k in bds), None)
    bd = bd or next(iter(bds.values()), None)
    pts = list(data["boundary"])
    for e in data["elements"]:
        if focus_ids is None or e["id"] in focus_ids:
            for g in e["geoms"]:
                pts.extend(g["pts"])
    if not pts:
        pts = [(0, 0), (30, 30)]
    x0, y0, x1, y1 = geometry.bbox(pts)
    pad = max(5.0, 0.12 * max(x1 - x0, y1 - y0))
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    kpx = max(10.0, min(60.0, width / max(x1 - x0, y1 - y0)))
    svg = preview.Svg((x0, y0, x1, y1), px_per_m=kpx)
    if bd:
        img = Path(bd["path"])
        mime = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
        data_uri = f"data:{mime};base64,{base64.b64encode(img.read_bytes()).decode()}"
        a, b, c, d, e, f = bd["matrix"]
        svg.parts.append(f'<image href="{data_uri}" width="{bd["size"][0]}" height="{bd["size"][1]}" '
                         f'transform="matrix({a} {b} {c} {d} {e} {f})" preserveAspectRatio="none"/>')
    # grid in project units, labeled, so positions can be read off the image
    k = units.TO_METERS[project.units]
    span_u = max(x1 - x0, y1 - y0) / k
    step_u = next(s for s in (1, 2, 5, 10, 20, 25, 50, 100, 200) if span_u / s <= 16)
    gx = math.floor(x0 / k / step_u) * step_u
    while gx * k <= x1:
        svg.polyline([(gx * k, y0), (gx * k, y1)], stroke="#ffffff", width=0.04)
        svg.text((gx * k, y0 + 18 / kpx), f"{gx:g}", size=22 / kpx, color="#ffef7a")
        gx += step_u
    gy = math.floor(y0 / k / step_u) * step_u
    while gy * k <= y1:
        svg.polyline([(x0, gy * k), (x1, gy * k)], stroke="#ffffff", width=0.04)
        svg.text((x0 + 26 / kpx, gy * k), f"{gy:g}", size=22 / kpx, color="#ffef7a")
        gy += step_u
    if data["boundary"]:
        svg.polygon(data["boundary"], stroke="#ff3b3b", width=0.18, dash="0.8,0.4")
    colors = {"structure": "#ff9f1c", "deck": "#ff9f1c", "paving": "#4cc9f0", "path": "#4cc9f0", "lawn": "#80ed99",
              "bed": "#c77dff", "wall": "#f15bb5", "fence": "#f15bb5", "plant": "#38b000", "plant_group": "#38b000",
              "plant_row": "#38b000", "boulder": "#bdbdbd", "light": "#ffd60a"}
    for e in data["elements"]:
        focused = focus_ids is None or e["id"] in focus_ids
        col = colors.get(e["kind"], "#ffffff") if focused else "#ffffff"
        wdt = 0.16 if focused else 0.05
        for g in e["geoms"]:
            if g["type"] == "polygon":
                svg.polygon(g["pts"], fill=col, opacity=0.18 if focused else 0.0, stroke=col, width=wdt)
            elif g["type"] == "line":
                svg.polyline(g["pts"], stroke=col, width=wdt * 1.5)
            elif g["type"] == "point":
                r = (e.get("size_m") or 0.6) / 2
                svg.circle(g["pts"][0], r, fill=col, opacity=0.25 if focused else 0.0, stroke=col, width=wdt)
        for m in e.get("members") or []:
            svg.circle((m["x"], m["y"]), m["r"], fill=col, opacity=0.2 if focused else 0, stroke=col, width=wdt * 0.6)
        if focused and e["geoms"]:
            g = e["geoms"][0]
            at = geometry.centroid(g["pts"]) if g["type"] == "polygon" and len(g["pts"]) > 2 else g["pts"][0]
            svg.text(at, e["id"] or e["kind"], size=22 / kpx, color="#ffffff")
    if show_inventory:
        for it in data["inventory"]:
            g = it["geom"]
            if not g:
                continue
            if g["type"] == "polygon":
                svg.polygon(g["pts"], stroke="#00f5d4", width=0.12, dash="0.5,0.3")
                at = geometry.centroid(g["pts"])
            else:
                at = g["pts"][0]
                svg.circle(at, 0.5, stroke="#00f5d4", width=0.12)
            svg.text(at, f"{it.get('category')}: {it.get('label') or it.get('id')}", size=20 / kpx, color="#00f5d4")
    # dark canvas so a missing backdrop still reads
    body = svg.render(title or project.name, [])
    body = body.replace('<rect width="100%" height="100%" fill="#fbfaf6"/>', '<rect width="100%" height="100%" fill="#2b2f2c"/>')
    out_svg = Path(out_png).with_suffix(".svg")
    out_svg.write_text(body)
    preview.svg_to_png(out_svg, Path(out_png), width)
    return Path(out_png)

