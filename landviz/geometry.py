"""Pure-numpy 2D/3D geometry used by the core (no shapely/scipy dependency).

Site frame convention everywhere: meters, X = east, Y = north, Z = up.
"""
from __future__ import annotations

import math
import random

import numpy as np

# ---------------------------------------------------------------- polygons


def signed_area(poly) -> float:
    p = np.asarray(poly, dtype=float)
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def area(poly) -> float:
    return abs(signed_area(poly))


def ensure_ccw(poly):
    poly = [tuple(p[:2]) for p in poly]
    if len(poly) > 1 and poly[0] == poly[-1]:
        poly = poly[:-1]
    return poly if signed_area(poly) >= 0 else poly[::-1]


def centroid(poly) -> tuple[float, float]:
    p = np.asarray(poly, dtype=float)[:, :2]
    a = signed_area(p)
    if abs(a) < 1e-12:
        return tuple(p.mean(axis=0))
    x, y = p[:, 0], p[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    cross = x * yn - xn * y
    return (float(((x + xn) * cross).sum() / (6 * a)), float(((y + yn) * cross).sum() / (6 * a)))


def bbox(pts) -> tuple[float, float, float, float]:
    p = np.asarray(pts, dtype=float)[:, :2]
    return float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max())


def point_in_polygon(x: float, y: float, poly) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _segments_cross(a, b, c, d) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return o1 * o2 < 0 and o3 * o4 < 0


def self_intersections(poly) -> list[tuple[int, int]]:
    """Pairs of non-adjacent crossing edges (O(n^2); fine for hand-authored shapes)."""
    n = len(poly)
    hits = []
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _segments_cross(a, b, poly[j], poly[(j + 1) % n]):
                hits.append((i, j))
    return hits


# ---------------------------------------------------------------- polylines


def polyline_length(line) -> float:
    p = np.asarray(line, dtype=float)[:, :2]
    return float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum())


def resample_polyline(line, spacing: float, include_end: bool = True):
    """Points every `spacing` meters along a polyline, with headings (radians)."""
    p = np.asarray(line, dtype=float)[:, :2]
    seg = np.diff(p, axis=0)
    seg_len = np.linalg.norm(seg, axis=1)
    total = seg_len.sum()
    if total == 0:
        return [(float(p[0, 0]), float(p[0, 1]), 0.0)]
    count = max(1, int(math.floor(total / spacing + 1e-9)))
    # Center the run so the leftover is split between both ends.
    lead = (total - count * spacing) / 2 if not include_end else 0.0
    stations = [lead + i * spacing for i in range(count + (1 if include_end else 0))]
    stations = [s for s in stations if s <= total + 1e-9]
    out = []
    cum = np.concatenate([[0], np.cumsum(seg_len)])
    for s in stations:
        k = min(int(np.searchsorted(cum, s, side="right")) - 1, len(seg) - 1)
        t = (s - cum[k]) / seg_len[k] if seg_len[k] else 0
        pt = p[k] + seg[k] * t
        out.append((float(pt[0]), float(pt[1]), float(math.atan2(seg[k, 1], seg[k, 0]))))
    return out


def offset_polyline_to_polygon(line, width: float, miter_limit: float = 3.0):
    """Buffer an open polyline into a closed polygon of the given total width (mitered joints)."""
    p = np.asarray(line, dtype=float)[:, :2]
    half = width / 2
    n = len(p)
    left, right = [], []
    for i in range(n):
        if i == 0:
            d = p[1] - p[0]
        elif i == n - 1:
            d = p[-1] - p[-2]
        else:
            d1 = p[i] - p[i - 1]
            d2 = p[i + 1] - p[i]
            d1 /= np.linalg.norm(d1) or 1
            d2 /= np.linalg.norm(d2) or 1
            d = d1 + d2
        d = d / (np.linalg.norm(d) or 1)
        nrm = np.array([-d[1], d[0]])
        scale = 1.0
        if 0 < i < n - 1:
            d1 = p[i] - p[i - 1]
            d1 /= np.linalg.norm(d1) or 1
            cos_half = float(np.dot(nrm, np.array([-d1[1], d1[0]])))
            scale = min(1.0 / max(cos_half, 1e-6), miter_limit)
        left.append(tuple(p[i] + nrm * half * scale))
        right.append(tuple(p[i] - nrm * half * scale))
    return ensure_ccw(left + right[::-1])


def smooth_polyline(line, iterations: int = 2):
    """Chaikin corner cutting, endpoints preserved — turns a few clicks into a natural curve."""
    pts = [tuple(p[:2]) for p in line]
    for _ in range(iterations):
        if len(pts) < 3:
            break
        new = [pts[0]]
        for a, b in zip(pts[:-1], pts[1:]):
            new.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            new.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        new.append(pts[-1])
        pts = new
    return pts


def smooth_polygon(poly, iterations: int = 2):
    pts = [tuple(p[:2]) for p in poly]
    for _ in range(iterations):
        new = []
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            new.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            new.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        pts = new
    return pts


# ---------------------------------------------------------------- fills


def fill_polygon(poly, spacing: float, seed: int = 0, jitter: float = 0.15, inset: float | None = None):
    """Triangular-grid (on-center) planting layout inside a polygon, the way beds are specced."""
    rng = random.Random(seed)
    inset = spacing * 0.4 if inset is None else inset
    x0, y0, x1, y1 = bbox(poly)
    row_h = spacing * math.sqrt(3) / 2
    pts = []
    row = 0
    y = y0 + row_h / 2
    while y < y1:
        x = x0 + (spacing / 2 if row % 2 else 0) + spacing / 2
        while x < x1:
            jx = x + rng.uniform(-jitter, jitter) * spacing
            jy = y + rng.uniform(-jitter, jitter) * spacing
            if point_in_polygon(jx, jy, poly) and _dist_to_boundary(jx, jy, poly) >= inset:
                pts.append((jx, jy))
            x += spacing
        y += row_h
        row += 1
    if not pts:  # tiny bed: at least one plant at the centroid
        pts.append(centroid(poly))
    return pts


def scatter_polygon(poly, count: int, seed: int = 0, min_dist: float = 0.0):
    """`count` random points inside a polygon (dart throwing with optional min spacing)."""
    rng = random.Random(seed)
    x0, y0, x1, y1 = bbox(poly)
    pts = []
    tries = 0
    while len(pts) < count and tries < count * 200:
        tries += 1
        x, y = rng.uniform(x0, x1), rng.uniform(y0, y1)
        if not point_in_polygon(x, y, poly):
            continue
        if min_dist and any((x - a) ** 2 + (y - b) ** 2 < min_dist ** 2 for a, b in pts):
            continue
        pts.append((x, y))
    return pts


def _dist_to_boundary(x, y, poly) -> float:
    best = float("inf")
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i][0], poly[i][1]
        bx, by = poly[(i + 1) % n][0], poly[(i + 1) % n][1]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0 if L2 == 0 else max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / L2))
        px, py = ax + t * dx, ay + t * dy
        best = min(best, math.hypot(x - px, y - py))
    return best


# ---------------------------------------------------------------- transforms


def similarity_fit(src, dst, allow_scale: bool = True):
    """Least-squares similarity (Umeyama) mapping src→dst, any dimension. Returns (s, R, t).

    dst ≈ s * R @ src + t. Used for plan-pixel calibration (2D) and scan alignment (3D).
    """
    A = np.asarray(src, dtype=float)
    B = np.asarray(dst, dtype=float)
    if A.shape != B.shape or len(A) < 2:
        raise ValueError("similarity_fit needs ≥2 matching point pairs")
    dim = A.shape[1]
    ma, mb = A.mean(axis=0), B.mean(axis=0)
    a, b = A - ma, B - mb
    cov = b.T @ a / len(A)
    U, S, Vt = np.linalg.svd(cov)
    D = np.eye(dim)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        D[-1, -1] = -1
    R = U @ D @ Vt
    var_a = (a ** 2).sum() / len(A)
    s = float(np.trace(np.diag(S) @ D) / var_a) if allow_scale and var_a > 0 else 1.0
    t = mb - s * R @ ma
    return s, R, t


def apply_similarity(s, R, t, pts):
    P = np.asarray(pts, dtype=float)
    return (s * (R @ P.T)).T + t


class PlanFrame:
    """Maps landscaper-plan image pixels → site meters.

    Image y grows downward, site y grows north, so pixels are y-flipped before the
    (rotation + uniform scale + translation) fit. Two or more control points required.
    """

    def __init__(self, pixel_pts, site_pts):
        src = np.array([[p[0], -p[1]] for p in pixel_pts], dtype=float)
        self.s, self.R, self.t = similarity_fit(src, np.asarray(site_pts, dtype=float)[:, :2])
        resid = apply_similarity(self.s, self.R, self.t, src) - np.asarray(site_pts, dtype=float)[:, :2]
        self.rms_error = float(np.sqrt((resid ** 2).sum(axis=1).mean())) if len(src) > 2 else 0.0

    @property
    def meters_per_pixel(self) -> float:
        return self.s

    def to_site(self, pts):
        src = np.array([[p[0], -p[1]] for p in pts], dtype=float)
        return [tuple(map(float, q)) for q in apply_similarity(self.s, self.R, self.t, src)]

    def to_pixels(self, pts):
        """Inverse of to_site: site meters → image pixels."""
        q = (np.asarray(pts, dtype=float)[:, :2] - self.t) @ self.R / self.s  # R is orthonormal
        return [(float(x), float(-y)) for x, y in q]

    def svg_matrix(self):
        """SVG matrix(a b c d e f) placing the raw image (pixel space) into site space (y-up)."""
        M = self.s * self.R @ np.diag([1, -1])
        return (M[0, 0], M[1, 0], M[0, 1], M[1, 1], self.t[0], self.t[1])


# ---------------------------------------------------------------- terrain


class ThinPlateSpline:
    """Smooth surface through scattered (x, y, z) grade points. Exact at the points."""

    def __init__(self, pts, smoothing: float = 0.0):
        P = np.asarray(pts, dtype=float)
        self.xy = P[:, :2]
        z = P[:, 2]
        n = len(P)
        if n < 3:  # not enough for a plane fit — degrade gracefully
            self.const = float(z.mean()) if n else 0.0
            self.w = None
            return
        K = self._kernel(self.xy, self.xy) + smoothing * np.eye(n)
        Pm = np.hstack([np.ones((n, 1)), self.xy])
        A = np.zeros((n + 3, n + 3))
        A[:n, :n], A[:n, n:], A[n:, :n] = K, Pm, Pm.T
        sol = np.linalg.lstsq(A, np.concatenate([z, np.zeros(3)]), rcond=None)[0]
        self.w, self.a = sol[:n], sol[n:]

    @staticmethod
    def _kernel(a, b):
        r2 = ((a[:, None, :] - b[None, :, :]) ** 2).sum(-1)
        with np.errstate(divide="ignore", invalid="ignore"):
            k = 0.5 * r2 * np.log(r2)
        return np.nan_to_num(k)

    def __call__(self, xy):
        Q = np.atleast_2d(np.asarray(xy, dtype=float))
        if self.w is None:
            return np.full(len(Q), self.const)
        return self._kernel(Q, self.xy) @ self.w + self.a[0] + Q @ self.a[1:]
