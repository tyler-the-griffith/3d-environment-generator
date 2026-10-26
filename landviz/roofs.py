"""Roof geometry for building wings (pure Python — tested without Blender).

A *wing* is a convex footprint polygon with walls up to an eave height and a roof on top.
Real houses with complex roofs are modeled as several overlapping wings: intersecting roof
prisms produce the valleys of cross-gables, L- and T-shapes on their own.

Every roof here is a *lower envelope of planes*: each footprint edge contributes zero or more
planes rising inward from that edge (pitch = rise/run, optional start offset), and the roof
surface is the minimum of all of them. One mechanism gives every common roof form:

  hip         every edge pitched
  gable       edges along the ridge pitched; end edges have no plane (vertical gable walls)
  half_hip    gable whose ends are hipped above a fraction of the rise (jerkinhead)
  shed        one low edge pitched; the other walls rise to meet it
  flat        horizontal at the eave (optional parapet)
  gambrel     gable with two pitches per side (steep lower, shallow upper)
  mansard     hip with two pitches per side
  custom      per-edge planes given explicitly

Heights are relative to the wing's floor; Blender adds the ground elevation.
"""
from __future__ import annotations

import math

EPS = 1e-9


# ---------------------------------------------------------------- 2D helpers


def _ccw(poly):
    a = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
            for i in range(len(poly)))
    return poly if a > 0 else poly[::-1]


def is_convex(poly, tol=1e-6) -> bool:
    n = len(poly)
    sign = 0
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        cx, cy = poly[(i + 2) % n]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) < tol:
            continue
        s = 1 if cross > 0 else -1
        if sign and s != sign:
            return False
        sign = s
    return True


def _edges(poly):
    n = len(poly)
    out = []
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1.0
        ux, uy = dx / L, dy / L
        out.append({"a": (ax, ay), "b": (bx, by), "u": (ux, uy), "n": (-uy, ux), "len": L})  # n points inward (CCW)
    return out


def offset_convex(poly, d):
    """Grow (d>0) a convex CCW polygon by moving every edge outward by d."""
    es = _edges(poly)
    lines = [((e["a"][0] - e["n"][0] * d, e["a"][1] - e["n"][1] * d), e["u"]) for e in es]
    out = []
    n = len(lines)
    for i in range(n):
        (p, u), (q, v) = lines[i - 1], lines[i]
        den = u[0] * v[1] - u[1] * v[0]
        if abs(den) < EPS:
            out.append((q[0], q[1]))
            continue
        t = ((q[0] - p[0]) * v[1] - (q[1] - p[1]) * v[0]) / den
        out.append((p[0] + u[0] * t, p[1] + u[1] * t))
    return out


def clip_halfplane(poly, a, b, c):
    """Keep the part of a convex polygon where a*x + b*y + c <= 0 (Sutherland–Hodgman)."""
    out = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        fp, fq = a * p[0] + b * p[1] + c, a * q[0] + b * q[1] + c
        if fp <= EPS:
            out.append(p)
        if (fp < -EPS and fq > EPS) or (fp > EPS and fq < -EPS):
            t = fp / (fp - fq)
            out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
    # drop duplicates
    clean = []
    for p in out:
        if not clean or math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) > 1e-7:
            clean.append(p)
    if len(clean) > 1 and math.hypot(clean[0][0] - clean[-1][0], clean[0][1] - clean[-1][1]) < 1e-7:
        clean.pop()
    return clean


# ---------------------------------------------------------------- planes


class Plane:
    """z = base + pitch * (inward distance from the edge line) + offset."""

    def __init__(self, edge, base, pitch, offset=0.0, d0=0.0):
        self.ax, self.ay = edge["a"]
        self.nx, self.ny = edge["n"]
        self.base, self.pitch, self.offset, self.d0 = base, pitch, offset, d0

    def dist(self, x, y):
        return (x - self.ax) * self.nx + (y - self.ay) * self.ny

    def z(self, x, y):
        return self.base + self.offset + self.pitch * (self.dist(x, y) - self.d0)

    def coeffs(self):
        """z = A x + B y + C"""
        A, B = self.pitch * self.nx, self.pitch * self.ny
        C = self.base + self.offset - self.pitch * (self.ax * self.nx + self.ay * self.ny + self.d0)
        return A, B, C


def _depth_across(poly, direction):
    """Half the polygon's extent measured perpendicular to `direction` (unit)."""
    px, py = -direction[1], direction[0]
    proj = [x * px + y * py for x, y in poly]
    return (max(proj) - min(proj)) / 2


def ridge_direction(poly, ridge="auto"):
    """Unit vector of the ridge. auto = along the longest side; x/y = site axes; int = along that edge."""
    es = _edges(poly)
    if ridge == "x":
        return (1.0, 0.0)
    if ridge == "y":
        return (0.0, 1.0)
    if isinstance(ridge, int):
        return es[ridge % len(es)]["u"]
    e = max(es, key=lambda e: e["len"])
    return e["u"]


def wing_planes(poly, eave, roof: dict) -> tuple[list, list]:
    """(planes, wall_kind per edge). wall_kind: 'eave' (wall stops at the eave) or 'gable'
    (wall rises to meet the roof). Pitches are tangents (rise/run)."""
    poly = _ccw(poly)
    es = _edges(poly)
    kind = roof.get("type", "gable")
    pitch = math.tan(math.radians(roof.get("pitch_deg", 26.57)))
    rdir = ridge_direction(poly, roof.get("ridge", "auto"))
    along = [abs(e["u"][0] * rdir[0] + e["u"][1] * rdir[1]) > 0.7 for e in es]  # edge runs along the ridge
    half = _depth_across(poly, rdir)
    planes, walls = [], []
    if kind == "flat":
        return [Plane(es[0], eave, 0.0)], ["eave"] * len(es)
    if kind == "shed":
        low = roof.get("low_edge")
        if low is None:  # the eave edge farthest "downhill": lowest y edge parallel to the ridge
            cands = [i for i, a in enumerate(along) if a] or list(range(len(es)))
            low = min(cands, key=lambda i: (es[i]["a"][1] + es[i]["b"][1]))
        planes = [Plane(es[low % len(es)], eave, pitch)]
        walls = ["eave" if i == low % len(es) else "gable" for i in range(len(es))]
        return planes, walls
    for i, e in enumerate(es):
        custom = (roof.get("edges") or {}).get(i) if kind == "custom" else None
        if kind == "custom":
            if custom in (None, "wall", "gable"):
                walls.append("gable")
                continue
            for p in custom:  # [[pitch_deg, offset_m, start_dist_m], ...]
                planes.append(Plane(e, eave, math.tan(math.radians(p[0])), p[1] if len(p) > 1 else 0.0,
                                    p[2] if len(p) > 2 else 0.0))
            walls.append("eave")
            continue
        sloped_side = along[i] or kind in ("hip", "mansard")
        if kind in ("gable", "gambrel") and not along[i]:
            walls.append("gable")
            continue
        if kind == "half_hip" and not along[i]:
            # Gable wall up to a fraction of the rise, hipped above that.
            frac = float(roof.get("half_hip", 0.6))
            planes.append(Plane(e, eave, pitch * float(roof.get("end_pitch_factor", 1.4)), offset=frac * pitch * half))
            walls.append("gable")
            continue
        if kind in ("gambrel", "mansard") and sloped_side:
            lower = math.tan(math.radians(roof.get("lower_pitch_deg", 60)))
            brk = float(roof.get("break", 0.45)) * half  # horizontal distance of the break line
            planes.append(Plane(e, eave, lower))
            planes.append(Plane(e, eave, pitch, offset=lower * brk, d0=brk))
            walls.append("eave")
            continue
        planes.append(Plane(e, eave, pitch))
        walls.append("eave")
    return planes, walls


# ---------------------------------------------------------------- the envelope


def roof_faces(poly, eave, roof: dict, overhang: float = 0.0, parapet: float = 0.0):
    """Roof surface as planar faces [[(x, y, z), ...], ...] over the footprint grown by `overhang`."""
    poly = _ccw(poly)
    planes, _ = wing_planes(poly, eave, roof)
    region = offset_convex(poly, overhang) if overhang > 0 else list(poly)
    faces = []
    coeffs = [p.coeffs() for p in planes]
    for j, (Aj, Bj, Cj) in enumerate(coeffs):
        reg = region
        for k, (Ak, Bk, Ck) in enumerate(coeffs):
            if k == j or not reg:
                continue
            # keep where plane j <= plane k:  (Aj-Ak) x + (Bj-Bk) y + (Cj-Ck) <= 0 ; ties go to the lower index
            a, b, c = Aj - Ak, Bj - Bk, Cj - Ck
            if abs(a) < EPS and abs(b) < EPS:
                if c > EPS or (abs(c) <= EPS and k < j):
                    reg = []
                continue
            reg = clip_halfplane(reg, a, b, c + (1e-9 if k < j else 0.0))
        if len(reg) >= 3:
            faces.append([(x, y, Aj * x + Bj * y + Cj) for x, y in reg])
    return faces


def roof_height(planes, x, y):
    return min(p.z(x, y) for p in planes) if planes else None


def wall_profiles(poly, eave, roof: dict, parapet: float = 0.0, samples: int = 48):
    """Each wall's top line as [(t, z), ...] along the edge (t in meters from edge start)."""
    poly = _ccw(poly)
    planes, kinds = wing_planes(poly, eave, roof)
    es = _edges(poly)
    out = []
    for e, kind in zip(es, kinds):
        if roof.get("type") == "flat" or kind == "eave":
            top = eave + (parapet if roof.get("type") == "flat" else 0.0)
            out.append({"a": e["a"], "b": e["b"], "profile": [(0.0, top), (e["len"], top)]})
            continue
        prof = []
        for i in range(samples + 1):
            t = e["len"] * i / samples
            x, y = e["a"][0] + e["u"][0] * t, e["a"][1] + e["u"][1] * t
            prof.append((t, max(eave, roof_height(planes, x, y))))
        # keep only the bends of the (piecewise-linear) profile
        keep = [prof[0]]
        for k in range(1, len(prof) - 1):
            (t0, z0), (t1, z1), (t2, z2) = keep[-1], prof[k], prof[k + 1]
            if abs((z1 - z0) * (t2 - t0) - (z2 - z0) * (t1 - t0)) > 1e-6:
                keep.append(prof[k])
        keep.append(prof[-1])
        out.append({"a": e["a"], "b": e["b"], "profile": keep})
    return out
