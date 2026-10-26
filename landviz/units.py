"""Length / angle parsing. Everything downstream of the spec loader is meters.

Accepted length forms: 12, 12.5, "12 ft", "12ft", "12'", "6' 4\"", "18 in", "18\"",
"3 m", "45 cm", "300 mm", "4 yd". Bare numbers use the project's default unit.
"""
from __future__ import annotations

import re

TO_METERS = {
    "m": 1.0, "meter": 1.0, "meters": 1.0,
    "cm": 0.01, "mm": 0.001,
    "ft": 0.3048, "feet": 0.3048, "foot": 0.3048, "'": 0.3048,
    "in": 0.0254, "inch": 0.0254, "inches": 0.0254, '"': 0.0254,
    "yd": 0.9144, "yard": 0.9144, "yards": 0.9144,
}

_FEET_INCHES = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*'\s*(\d+(?:\.\d+)?)\s*\"?\s*$")
_NUM_UNIT = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*([a-zA-Z'\"]*)\s*$")


class UnitError(ValueError):
    pass


def length(value, default_unit: str = "m") -> float:
    """Parse a length into meters."""
    if isinstance(value, bool):
        raise UnitError(f"not a length: {value!r}")
    if isinstance(value, (int, float)):
        return float(value) * TO_METERS[default_unit]
    if not isinstance(value, str):
        raise UnitError(f"not a length: {value!r}")
    m = _FEET_INCHES.match(value)
    if m:
        ft, inch = float(m.group(1)), float(m.group(2))
        sign = -1 if ft < 0 else 1
        return sign * (abs(ft) * 0.3048 + inch * 0.0254)
    m = _NUM_UNIT.match(value)
    if not m:
        raise UnitError(f"cannot parse length {value!r}")
    num, unit = float(m.group(1)), (m.group(2) or default_unit).lower()
    if unit not in TO_METERS:
        raise UnitError(f"unknown unit {unit!r} in {value!r}")
    return num * TO_METERS[unit]


def pitch(value) -> float:
    """Roof pitch → degrees. Accepts "6/12" (rise/run), "30deg", or a bare number of degrees."""
    import math
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower()
    if "/" in s:
        rise, run = s.split("/", 1)
        return math.degrees(math.atan2(float(rise), float(run)))
    return float(s.replace("deg", "").replace("°", ""))


def point(value, default_unit: str = "m") -> tuple[float, ...]:
    return tuple(length(v, default_unit) for v in value)


def points(values, default_unit: str = "m") -> list[tuple[float, ...]]:
    return [point(v, default_unit) for v in values]
