"""Public aerial photos (USGS NAIP, public domain) as a geo-referenced, straight-down backdrop.

A drone photo from above leans (tall things shift outward); an orthophoto doesn't, and it is
aligned to the map. That makes it the best image to trace ground surfaces and building
outlines from, and to check the model against.

The image is saved in the project's `aerial/` folder with its lat/lon bounds. Once the site is
geo-referenced (`site.location.origin`), points can be authored in aerial pixels with
`frame: aerial`, exactly like `frame: plan` for a landscaper's drawing.

NAIP is flown every 2–3 years per state at 0.3–1 m per pixel; trees and anything built since
can differ.
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .photos import M_PER_DEG_LAT, M_PER_DEG_LON_EQ, latlon_to_site

SERVICE = "https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer/exportImage"
FOLDER = "aerial"
MAX_PX = 4000


def fetch(project_dir: Path, lat: float, lon: float, radius: float = 80.0, m_per_px: float = 0.3,
          place: str = "", log=print) -> dict:
    """Download a square orthophoto `radius` meters around lat/lon. Pixels are square in meters."""
    out = Path(project_dir) / FOLDER
    out.mkdir(parents=True, exist_ok=True)
    dlat = radius / M_PER_DEG_LAT
    dlon = radius / (M_PER_DEG_LON_EQ * math.cos(math.radians(lat)))
    bbox = [lon - dlon, lat - dlat, lon + dlon, lat + dlat]  # w, s, e, n
    size = min(MAX_PX, int(round(2 * radius / m_per_px)))
    q = urllib.parse.urlencode({"bbox": ",".join(f"{v:.7f}" for v in bbox), "bboxSR": 4326, "imageSR": 4326,
                                "size": f"{size},{size}", "format": "jpg", "f": "image"})
    log(f"downloading aerial photo ({2 * radius:.0f} m square, {2 * radius / size:.2f} m/px) from USGS NAIP")
    req = urllib.request.Request(f"{SERVICE}?{q}", headers={"User-Agent": "3d-environment-generator"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
        if not data[:3] == b"\xff\xd8\xff":
            raise RuntimeError(f"the imagery service didn't return an image: {data[:200]!r}")
    (out / "aerial.jpg").write_bytes(data)
    meta = {"source": "USGS NAIP (public domain)", "bbox": bbox, "size": [size, size], "center": [lat, lon],
            "radius": radius, "m_per_px": 2 * radius / size, "place": place, "fetched": time.strftime("%Y-%m-%d")}
    (out / "aerial.json").write_text(json.dumps(meta, indent=1))
    return meta


def load(project_dir: Path) -> dict | None:
    p = Path(project_dir) / FOLDER / "aerial.json"
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def image_path(project_dir: Path) -> Path:
    return Path(project_dir) / FOLDER / "aerial.jpg"


def px_to_latlon(meta: dict, px: float, py: float) -> tuple[float, float]:
    w, s, e, n = meta["bbox"]
    W, H = meta["size"]
    return n - (py / H) * (n - s), w + (px / W) * (e - w)


def control_points(meta: dict, origin, north_offset_deg=0.0):
    """Pixel ↔ site-meter pairs (the four corners and the center) for a PlanFrame."""
    W, H = meta["size"]
    px = [(0, 0), (W, 0), (W, H), (0, H), (W / 2, H / 2)]
    site = [latlon_to_site(*px_to_latlon(meta, x, y), origin, north_offset_deg) for x, y in px]
    return px, site


def frame(project_dir: Path, origin, north_offset_deg=0.0):
    """PlanFrame for the aerial photo, or None if there's no photo or the site isn't geo-referenced."""
    meta = load(project_dir)
    if meta is None or origin is None:
        return None
    from .geometry import PlanFrame
    px, site = control_points(meta, origin, north_offset_deg)
    return PlanFrame(px, site)
