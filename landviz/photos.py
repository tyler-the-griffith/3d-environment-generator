"""Photo metadata (EXIF + DJI XMP) and geo-referencing — pure Python.

Used by the photo-match check: a photo taken at a known GPS position, altitude, heading,
tilt and lens can be re-created as a camera in the 3D model, and the render compared with
the photo. Positions come from GPS; heading/tilt come from DJI's XMP metadata when the
photo carries it (original DJI files do; "beautify"/edited exports usually don't), or from
the design file.
"""
from __future__ import annotations

import math
import re
import struct
from pathlib import Path

M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LON_EQ = 111_320.0

_TAGS = {0x010F: "make", 0x0110: "model", 0x0112: "orientation", 0x9003: "datetime", 0x920A: "focal_mm",
         0xA405: "f35", 0xA002: "exif_width", 0xA003: "exif_height", 0x8825: "_gps", 0x8769: "_exif"}
_GPS = {1: "lat_ref", 2: "lat", 3: "lon_ref", 4: "lon", 5: "alt_ref", 6: "alt"}


def _read_ifd(buf, off, endian, tagmap):
    out = {}
    try:
        (n,) = struct.unpack_from(endian + "H", buf, off)
    except struct.error:
        return out
    for i in range(n):
        tag, typ, cnt, val = struct.unpack_from(endian + "HHI4s", buf, off + 2 + i * 12)
        if tag not in tagmap:
            continue
        size = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}.get(typ, 1) * cnt
        data = val if size <= 4 else buf[struct.unpack(endian + "I", val)[0]:][:size]
        name = tagmap[tag]
        if typ == 2:
            v = data[:cnt].rstrip(b"\0").decode("latin-1", "replace")
        elif typ == 3:
            v = struct.unpack_from(endian + "H" * cnt, data)
        elif typ == 4:
            v = struct.unpack_from(endian + "I" * cnt, data)
        elif typ in (5, 10):
            raw = struct.unpack_from(endian + ("I" if typ == 5 else "i") * (2 * cnt), data)
            v = tuple(raw[k] / raw[k + 1] if raw[k + 1] else 0.0 for k in range(0, len(raw), 2))
        elif typ in (1, 7):
            v = tuple(data[:cnt])
        else:
            continue
        out[name] = v[0] if isinstance(v, tuple) and len(v) == 1 else v
    return out


def _jpeg_size(data):
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None, None


def read(path) -> dict:
    """Metadata of one JPEG: lat/lon (signed degrees), alt (m), datetime, f35, image size, and
    DJI gimbal yaw/pitch/roll + relative altitude when present."""
    data = Path(path).read_bytes()
    info: dict = {"file": str(path)}
    w, h = _jpeg_size(data)
    info["width"], info["height"] = w, h
    m = data.find(b"Exif\0\0")
    if m >= 0:
        tiff = data[m + 6:]
        endian = "<" if tiff[:2] == b"II" else ">"
        (ifd0,) = struct.unpack_from(endian + "I", tiff, 4)
        tags = _read_ifd(tiff, ifd0, endian, _TAGS)
        if "_exif" in tags:
            tags.update(_read_ifd(tiff, tags["_exif"], endian, _TAGS))
        gps = _read_ifd(tiff, tags["_gps"], endian, _GPS) if "_gps" in tags else {}
        for k in ("make", "model", "datetime", "focal_mm", "f35", "orientation"):
            if k in tags:
                info[k] = tags[k]

        def dms(v):
            return v[0] + v[1] / 60 + v[2] / 3600 if isinstance(v, tuple) and len(v) == 3 else None
        lat, lon = dms(gps.get("lat")), dms(gps.get("lon"))
        if lat is not None and lon is not None:
            # Some exports drop the N/S, E/W refs; assume N/W only if the refs are missing AND say so.
            lat_ref, lon_ref = gps.get("lat_ref"), gps.get("lon_ref")
            info["lat"] = -lat if lat_ref == "S" else lat
            info["lon"] = -lon if lon_ref == "W" else lon
            if not lon_ref:
                info["lon_ref_missing"] = True
        if "alt" in gps:
            info["alt"] = gps["alt"] * (-1 if gps.get("alt_ref") in (1, (1,)) else 1)
    xmp = re.search(rb"<x:xmpmeta.*?</x:xmpmeta>", data, re.S)
    if xmp:
        x = xmp.group(0).decode("utf-8", "replace")
        for key, name in (("GimbalYawDegree", "gimbal_yaw"), ("GimbalPitchDegree", "gimbal_pitch"),
                          ("GimbalRollDegree", "gimbal_roll"), ("FlightYawDegree", "flight_yaw"),
                          ("RelativeAltitude", "rel_alt"), ("AbsoluteAltitude", "abs_alt")):
            mm = re.search(rf'drone-dji:{key}="([^"]*)"', x) or re.search(rf"<drone-dji:{key}>([^<]*)<", x)
            if mm:
                try:
                    info[name] = float(mm.group(1))
                except ValueError:
                    pass
    return info


def fov_deg(info: dict, override: float | None = None) -> tuple[float, float]:
    """(horizontal, vertical) field of view. EXIF's 35mm-equivalent focal length is defined on the
    43.27 mm diagonal of a 36×24 frame."""
    w, h = info.get("width") or 4000, info.get("height") or 3000
    if override:
        hf = math.radians(override)
        return override, math.degrees(2 * math.atan(math.tan(hf / 2) * h / w))
    f35 = info.get("f35") or 24
    diag_fov = 2 * math.atan(43.27 / (2 * f35))
    diag_px = math.hypot(w, h)
    t = math.tan(diag_fov / 2)
    return (math.degrees(2 * math.atan(t * w / diag_px)), math.degrees(2 * math.atan(t * h / diag_px)))


# ---------------------------------------------------------------- geo-referencing


def latlon_to_site(lat, lon, origin, north_offset_deg=0.0):
    """Local tangent-plane meters relative to the site origin (lat, lon), rotated into the site
    frame (site +Y is `north_offset_deg` clockwise from true north)."""
    lat0, lon0 = origin
    e = (lon - lon0) * M_PER_DEG_LON_EQ * math.cos(math.radians(lat0))
    n = (lat - lat0) * M_PER_DEG_LAT
    t = math.radians(north_offset_deg)
    return e * math.cos(t) - n * math.sin(t), e * math.sin(t) + n * math.cos(t)


def site_to_latlon(x, y, origin, north_offset_deg=0.0):
    lat0, lon0 = origin
    t = math.radians(north_offset_deg)
    e = x * math.cos(t) + y * math.sin(t)
    n = -x * math.sin(t) + y * math.cos(t)
    return lat0 + n / M_PER_DEG_LAT, lon0 + e / (M_PER_DEG_LON_EQ * math.cos(math.radians(lat0)))


def parse_latlon(text: str) -> tuple[float, float] | None:
    """'40.5729, -105.1437' / '40.5729 N 105.1437 W' / '40°34'22.3"N 105°8'37.5"W' → (lat, lon)."""
    if not text:
        return None
    s = text.strip()
    dms = re.findall(r"(\d+(?:\.\d+)?)\s*°\s*(\d+(?:\.\d+)?)\s*['′]\s*(\d+(?:\.\d+)?)\s*[\"″]?\s*([NSEW])", s, re.I)
    if len(dms) >= 2:
        vals = []
        for d, m_, sec, hemi in dms[:2]:
            v = float(d) + float(m_) / 60 + float(sec) / 3600
            vals.append((-v if hemi.upper() in "SW" else v, hemi.upper()))
        lat = next(v for v, hh in vals if hh in "NS")
        lon = next(v for v, hh in vals if hh in "EW")
        return lat, lon
    nums = re.findall(r"(-?\d+(?:\.\d+)?)\s*°?\s*([NSEW])?", s, re.I)
    nums = [(float(v), (hh or "").upper()) for v, hh in nums]
    if len(nums) >= 2:
        (a, ha), (b, hb) = nums[0], nums[1]
        if ha in "SW" and ha:
            a = -abs(a)
        if hb in "SW" and hb:
            b = -abs(b)
        if abs(a) <= 90 and abs(b) <= 180 and (abs(a) > 0.0001 or abs(b) > 0.0001):
            return a, b
    return None
