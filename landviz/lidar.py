"""Public elevation data (USGS 3DEP) for a site: measured terrain, roof and tree heights.

Two sources, both free and public:

  bare-earth DEM   USGS 3DEP elevation service (1 m where available, nationwide). Needs nothing
                   extra — sampled point by point over HTTPS. Gives the ground's real shape.
  point cloud      USGS lidar on AWS (Entwine/EPT, streamed by area). Gives roofs, trees and
                   building outlines at true scale (no photo lean). Needs the optional `laspy`
                   + `lazrs` packages (./launch --setup installs them). Surveys can be years old:
                   buildings are usually still right, trees will have grown.

Everything is stored in <project>/derived/lidar/ as a grid in a local frame centered on the
fetch location (meters east/north). The design file uses it through `site.terrain.type: lidar`
and `site.location.origin` (the lat/lon of site 0,0). Nothing about the location is sent
anywhere except these two USGS services — and, if you give a street address, the US Census
geocoder to turn it into coordinates.
"""
from __future__ import annotations

import io
import json
import math
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .photos import M_PER_DEG_LAT, M_PER_DEG_LON_EQ

DEM_URL = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples"
EPT_INDEX = "https://raw.githubusercontent.com/hobuinc/usgs-lidar/master/boundaries/resources.geojson"
EPT_ROOT = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public"
GEOCODER = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
UA = {"User-Agent": "3D-Environment-Generator/0.1 (landscape visualization)"}
R_MERC = 6378137.0


class LidarError(RuntimeError):
    pass


def _get(url, data=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def geocode(address: str) -> tuple[float, float, str]:
    """US street address → (lat, lon, matched address) via the US Census geocoder."""
    q = urllib.parse.urlencode({"address": address, "benchmark": "Public_AR_Current", "format": "json"})
    d = json.loads(_get(f"{GEOCODER}?{q}"))
    matches = d.get("result", {}).get("addressMatches", [])
    if not matches:
        raise LidarError(f"the US Census geocoder found no match for {address!r} — try coordinates instead")
    m = matches[0]
    return m["coordinates"]["y"], m["coordinates"]["x"], m.get("matchedAddress", address)


# ---------------------------------------------------------------- local frame


def _local(lat, lon, lat0, lon0):
    return ((np.asarray(lon) - lon0) * M_PER_DEG_LON_EQ * math.cos(math.radians(lat0)),
            (np.asarray(lat) - lat0) * M_PER_DEG_LAT)


def _latlon(e, n, lat0, lon0):
    return lat0 + np.asarray(n) / M_PER_DEG_LAT, lon0 + np.asarray(e) / (M_PER_DEG_LON_EQ * math.cos(math.radians(lat0)))


# ---------------------------------------------------------------- bare-earth DEM


def fetch_dem(lat0, lon0, radius=70.0, res=1.0, log=print) -> np.ndarray:
    n = int(round(2 * radius / res)) + 1
    es = -radius + np.arange(n) * res
    E, N = np.meshgrid(es, es)  # row = north index (south→north), col = east index
    LAT, LON = _latlon(E.ravel(), N.ravel(), lat0, lon0)
    pts = list(zip(LON.tolist(), LAT.tolist()))
    batches = [pts[i:i + 1000] for i in range(0, len(pts), 1000)]
    out = np.full(len(pts), np.nan)

    def one(bi):
        body = urllib.parse.urlencode({
            "geometry": json.dumps({"points": batches[bi], "spatialReference": {"wkid": 4326}}),
            "geometryType": "esriGeometryMultipoint", "returnFirstValueOnly": "true", "f": "json"}).encode()
        for attempt in range(4):
            try:
                d = json.loads(_get(DEM_URL, body, timeout=90))
                if "error" in d:
                    raise LidarError(d["error"].get("message", "elevation service error"))
                return bi, d["samples"]
            except Exception:  # noqa: BLE001 — retry transient failures
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
    log(f"elevation: sampling {len(pts):,} points ({n}×{n} at {res} m) from USGS 3DEP…")
    with ThreadPoolExecutor(6) as ex:
        for bi, samples in ex.map(one, range(len(batches))):
            for s in samples:
                try:
                    out[bi * 1000 + s["locationId"]] = float(s["value"])
                except (ValueError, TypeError):
                    pass
    grid = out.reshape(n, n)
    if np.isnan(grid).all():
        raise LidarError("the elevation service returned no data here (outside USGS 3DEP coverage?)")
    if np.isnan(grid).any():  # fill small gaps from neighbours
        idx = np.where(np.isnan(grid))
        grid[idx] = np.nanmean(grid)
    return grid


# ---------------------------------------------------------------- point cloud (optional)


def points_available() -> tuple[bool, str]:
    try:
        import laspy  # noqa: F401
        import lazrs  # noqa: F401
        return True, "ok"
    except ImportError:
        return False, "optional packages laspy + lazrs not installed (./launch --setup)"


def _merc(lat, lon):
    return (np.radians(lon) * R_MERC, np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) * R_MERC)


def find_dataset(lat, lon, cache: Path | None = None) -> str | None:
    """Name of the newest USGS EPT point-cloud dataset covering a point."""
    raw = None
    if cache and cache.exists() and time.time() - cache.stat().st_mtime < 30 * 86400:
        raw = cache.read_bytes()
    if raw is None:
        raw = _get(EPT_INDEX, timeout=120)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(raw)
    d = json.loads(raw)

    def inside(ring):
        c, j = False, len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i][:2]
            xj, yj = ring[j][:2]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                c = not c
            j = i
        return c
    hits = []
    for f in d["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        if any(inside(p[0]) for p in polys):
            name = f["properties"]["name"]
            year = max([int(t) for t in __import__("re").findall(r"(?<!\d)(20\d\d|19\d\d)(?!\d)", name)] or [0])
            hits.append((year, name))
    return max(hits)[1] if hits else None


def fetch_points(lat0, lon0, radius=70.0, dataset=None, cache=None, log=print):
    """(E, N, Z, classification, dataset name) for every lidar return in the square around the site."""
    import laspy
    name = dataset or find_dataset(lat0, lon0, cache)
    if not name:
        raise LidarError("no USGS point-cloud survey covers this location")
    meta = json.loads(_get(f"{EPT_ROOT}/{name}/ept.json"))
    if meta.get("srs", {}).get("horizontal") not in ("3857", 3857):
        raise LidarError(f"{name}: unexpected coordinate system {meta.get('srs')}")
    # query box in web-mercator (scale ~1/cos(lat) vs meters on the ground)
    k = 1 / math.cos(math.radians(lat0))
    cx, cy = _merc(lat0, lon0)
    q = (cx - radius * k, cy - radius * k, cx + radius * k, cy + radius * k)
    root = meta["bounds"]  # [xmin, ymin, zmin, xmax, ymax, zmax] cube

    def node_bounds(key):
        d, x, y, z = key
        size = (root[3] - root[0]) / 2 ** d
        return root[0] + x * size, root[1] + y * size, root[0] + (x + 1) * size, root[1] + (y + 1) * size

    def intersects(key):
        x0, y0, x1, y1 = node_bounds(key)
        return not (x1 < q[0] or x0 > q[2] or y1 < q[1] or y0 > q[3])

    keys, todo, seen_h = [], ["0-0-0-0"], set()
    while todo:
        hk = todo.pop()
        if hk in seen_h:
            continue
        seen_h.add(hk)
        hier = json.loads(_get(f"{EPT_ROOT}/{name}/ept-hierarchy/{hk}.json"))
        for kstr, count in hier.items():
            key = tuple(int(v) for v in kstr.split("-"))
            if not intersects(key):
                continue
            if count == -1:
                todo.append(kstr)
            elif count > 0:
                keys.append(kstr)
    log(f"point cloud: {name}, {len(keys)} tiles to stream…")

    def one(kstr):
        for attempt in range(4):
            try:
                return laspy.read(io.BytesIO(_get(f"{EPT_ROOT}/{name}/ept-data/{kstr}.laz", timeout=120)))
            except Exception:  # noqa: BLE001
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
    xs, ys, zs, cs = [], [], [], []
    with ThreadPoolExecutor(6) as ex:
        for las in ex.map(one, keys):
            x, y = np.asarray(las.x), np.asarray(las.y)
            m = (x >= q[0]) & (x <= q[2]) & (y >= q[1]) & (y <= q[3])
            if m.any():
                xs.append(x[m])
                ys.append(y[m])
                zs.append(np.asarray(las.z)[m])
                cs.append(np.asarray(las.classification)[m])
    if not xs:
        raise LidarError(f"{name} has no points here")
    X, Y, Z, C = (np.concatenate(a) for a in (xs, ys, zs, cs))
    lon = np.degrees(X / R_MERC)
    lat = np.degrees(2 * np.arctan(np.exp(Y / R_MERC)) - np.pi / 2)
    E, N = _local(lat, lon, lat0, lon0)
    keep = (C != 7) & (C != 18)  # drop noise
    log(f"point cloud: {keep.sum():,} returns")
    return E[keep], N[keep], Z[keep], C[keep], name


def surface_grids(E, N, Z, C, radius, res):
    """DSM (highest return per cell), building and vegetation masks."""
    n = int(round(2 * radius / res)) + 1
    i = np.clip(np.round((N + radius) / res).astype(int), 0, n - 1)
    j = np.clip(np.round((E + radius) / res).astype(int), 0, n - 1)
    flat = i * n + j
    dsm = np.full(n * n, -np.inf)
    np.maximum.at(dsm, flat, Z)
    building = np.zeros(n * n, bool)
    building[flat[C == 6]] = True
    veg = np.zeros(n * n, bool)
    veg[flat[(C >= 3) & (C <= 5)]] = True
    dsm = dsm.reshape(n, n)
    empty = ~np.isfinite(dsm)
    if empty.any():  # fill sparse cells with the neighbourhood max
        from numpy.lib.stride_tricks import sliding_window_view
        pad = np.pad(np.where(empty, -np.inf, dsm), 1, mode="edge")
        nb = sliding_window_view(pad, (3, 3)).max(axis=(-1, -2))
        dsm = np.where(empty, nb, dsm)
        dsm[~np.isfinite(dsm)] = np.nan
    return dsm, building.reshape(n, n), veg.reshape(n, n)


# ---------------------------------------------------------------- store / load / query


def fetch(out_dir: Path, lat0, lon0, radius=70.0, res=1.0, points=True, log=print, place="") -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    dem = fetch_dem(lat0, lon0, radius, res, log)
    data = {"dem": dem}
    meta = {"center": [lat0, lon0], "radius": radius, "res": res, "size": dem.shape[0], "place": place,
            "dem_source": "USGS 3DEP elevation service (bare earth)", "fetched": time.strftime("%Y-%m-%d"),
            "point_cloud": None}
    ok, why = points_available()
    if points and ok:
        try:
            E, N, Z, C, name = fetch_points(lat0, lon0, radius, cache=out_dir.parent / "ept-index.geojson", log=log)
            dsm, bld, veg = surface_grids(E, N, Z, C, radius, res)
            # point-cloud heights are on the same vertical datum family as the DEM; align the
            # two by the median offset on ground returns so heights above ground are clean
            gi = C == 2
            if gi.any():
                g_dem = sample(dem, meta, E[gi], N[gi])
                off = float(np.nanmedian(Z[gi] - g_dem))
                dsm = dsm - off
                meta["vertical_offset_applied"] = off
            data.update(dsm=dsm, building=bld, vegetation=veg)
            meta["point_cloud"] = {"dataset": name, "returns": int(len(Z))}
        except Exception as e:  # noqa: BLE001 — the DEM alone is still useful
            log(f"point cloud unavailable: {e}")
            meta["point_cloud_error"] = str(e)
    elif points:
        meta["point_cloud_error"] = why
        log(f"point cloud skipped: {why}")
    np.savez_compressed(out_dir / "lidar.npz", **data)
    (out_dir / "lidar.json").write_text(json.dumps(meta, indent=1))
    return meta


def load(lidar_dir: Path):
    meta = json.loads((lidar_dir / "lidar.json").read_text())
    with np.load(lidar_dir / "lidar.npz") as z:
        grids = {k: z[k] for k in z.files}
    return grids, meta


def sample(grid, meta, E, N):
    """Bilinear sample of a grid at local (east, north) meters."""
    r, res, n = meta["radius"], meta["res"], meta["size"]
    fi = np.clip((np.asarray(N, float) + r) / res, 0, n - 1.000001)
    fj = np.clip((np.asarray(E, float) + r) / res, 0, n - 1.000001)
    i0, j0 = np.floor(fi).astype(int), np.floor(fj).astype(int)
    di, dj = fi - i0, fj - j0
    g = grid
    return ((g[i0, j0] * (1 - dj) + g[i0, j0 + 1] * dj) * (1 - di) +
            (g[i0 + 1, j0] * (1 - dj) + g[i0 + 1, j0 + 1] * dj) * di)


def site_to_local(x, y, meta, origin, north_offset_deg=0.0):
    """Site meters → the lidar grid's local east/north meters."""
    from .photos import site_to_latlon
    lat, lon = site_to_latlon(np.asarray(x, float), np.asarray(y, float), origin, north_offset_deg)
    return _local(lat, lon, meta["center"][0], meta["center"][1])
