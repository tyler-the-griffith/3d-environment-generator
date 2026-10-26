"""Sun position (NOAA approximation, ~0.1° — plenty for rendering)."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone


def sun_position(lat: float, lon: float, when: datetime) -> tuple[float, float]:
    """Return (azimuth_deg clockwise from true north, elevation_deg) for an aware datetime."""
    if when.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    t = when.astimezone(timezone.utc)
    jd = (t - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)) / timedelta(days=1) + 2451545.0
    jc = (jd - 2451545.0) / 36525.0

    geom_mean_long = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    geom_mean_anom = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    ecc = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    m = math.radians(geom_mean_anom)
    eq_ctr = (math.sin(m) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
              + math.sin(2 * m) * (0.019993 - 0.000101 * jc) + math.sin(3 * m) * 0.000289)
    true_long = geom_mean_long + eq_ctr
    omega = 125.04 - 1934.136 * jc
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    mean_obliq = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    obliq = mean_obliq + 0.00256 * math.cos(math.radians(omega))
    decl = math.degrees(math.asin(math.sin(math.radians(obliq)) * math.sin(math.radians(app_long))))

    y = math.tan(math.radians(obliq / 2)) ** 2
    l0 = math.radians(geom_mean_long)
    eq_time = 4 * math.degrees(
        y * math.sin(2 * l0) - 2 * ecc * math.sin(m) + 4 * ecc * y * math.sin(m) * math.cos(2 * l0)
        - 0.5 * y * y * math.sin(4 * l0) - 1.25 * ecc * ecc * math.sin(2 * m))

    minutes = t.hour * 60 + t.minute + t.second / 60
    true_solar = (minutes + eq_time + 4 * lon) % 1440
    hour_angle = true_solar / 4 - 180 if true_solar / 4 >= 0 else true_solar / 4 + 180

    lat_r, decl_r, ha_r = map(math.radians, (lat, decl, hour_angle))
    cos_zen = math.sin(lat_r) * math.sin(decl_r) + math.cos(lat_r) * math.cos(decl_r) * math.cos(ha_r)
    zen = math.degrees(math.acos(max(-1, min(1, cos_zen))))
    elevation = 90 - zen

    denom = math.cos(lat_r) * math.sin(math.radians(zen))
    if abs(denom) < 1e-9:
        az = 180.0
    else:
        c = (math.sin(lat_r) * math.cos(math.radians(zen)) - math.sin(decl_r)) / denom
        az = math.degrees(math.acos(max(-1, min(1, c))))
        az = (az + 180) % 360 if hour_angle > 0 else (540 - az) % 360
    return az, elevation
