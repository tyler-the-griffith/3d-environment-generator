"""2D plan preview: the resolved scene drawn as an SVG over the landscaper's plan image.

This is the fast feedback loop (no Blender): transcribe the plan into project.yaml, render the
preview, and check every bed edge and plant circle lands on the drawing before spending GPU
time. Also renders to PNG (ImageMagick) so it can be eyeballed or sent around.
"""
from __future__ import annotations

import base64
import html
import shutil
import subprocess
from pathlib import Path

from . import elements, geometry
from .project import Project

# Plant fill colors by category so the preview reads like a landscape plan.
CATEGORY_FILL = {"tree": "#2f6b2f", "shrub": "#4f8a3a", "grass": "#a8a04a", "perennial": "#b0609a",
                 "groundcover": "#6a9a5a", "vine": "#5a8a5a", "annual": "#d07a3a"}


class Svg:
    def __init__(self, bounds, px_per_m=20.0):
        self.x0, self.y0, self.x1, self.y1 = bounds
        self.k = px_per_m
        self.parts: list[str] = []
        self.labels: list[str] = []

    # site (y-up meters) → svg (y-down) is done with one group transform, so drawing code uses site coords.
    def _pts(self, pts):
        return " ".join(f"{p[0]:.3f},{p[1]:.3f}" for p in pts)

    def polygon(self, pts, fill="none", stroke="#000", width=0.05, opacity=1.0, dash=None, hatch=False):
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(f'<polygon points="{self._pts(pts)}" fill="{fill}" fill-opacity="{opacity}" '
                          f'stroke="{stroke}" stroke-width="{width}"{extra}/>')
        if hatch:
            self.parts.append(f'<polygon points="{self._pts(pts)}" fill="url(#hatch)" stroke="none"/>')

    def polyline(self, pts, stroke="#000", width=0.05, dash=None):
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(f'<polyline points="{self._pts(pts)}" fill="none" stroke="{stroke}" '
                          f'stroke-width="{width}"{extra}/>')

    def circle(self, c, r, fill="none", stroke="#000", width=0.03, opacity=1.0):
        self.parts.append(f'<circle cx="{c[0]:.3f}" cy="{c[1]:.3f}" r="{r:.3f}" fill="{fill}" '
                          f'fill-opacity="{opacity}" stroke="{stroke}" stroke-width="{width}"/>')

    def text(self, at, s, size=0.5, color="#000"):
        # Text is drawn in an un-flipped layer so it isn't mirrored.
        self.labels.append(f'<text x="{at[0]:.3f}" y="{-at[1]:.3f}" font-size="{size}" fill="{color}" '
                           f'text-anchor="middle" dominant-baseline="middle" '
                           f'font-family="sans-serif">{html.escape(s)}</text>')

    def plant(self, rec, catalog=None):
        cat = rec.get("_category", "shrub")
        fill = CATEGORY_FILL.get(cat, "#4f8a3a")
        r = rec["spread"] / 2
        self.circle((rec["x"], rec["y"]), r, fill=fill, stroke="#1a3a1a", width=0.03, opacity=0.45)
        self.circle((rec["x"], rec["y"]), min(0.06, r * 0.3), fill="#1a3a1a", stroke="none")

    def image(self, href: str, matrix, w, h):
        a, b, c, d, e, f = matrix
        self.parts.append(f'<image href="{href}" width="{w}" height="{h}" opacity="0.55" '
                          f'transform="matrix({a} {b} {c} {d} {e} {f})" preserveAspectRatio="none"/>')

    def render(self, title: str, legend: list[tuple[str, str, int]]) -> str:
        w_m, h_m = self.x1 - self.x0, self.y1 - self.y0
        W, H = w_m * self.k, h_m * self.k
        legend_h = 22 + 16 * len(legend)
        out = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
               f'width="{W:.0f}" height="{H + legend_h:.0f}" viewBox="0 0 {W:.1f} {H + legend_h:.1f}">',
               '<defs><pattern id="hatch" width="0.6" height="0.6" patternUnits="userSpaceOnUse" '
               'patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="0.6" stroke="#888" '
               'stroke-width="0.06"/></pattern></defs>',
               f'<rect width="100%" height="100%" fill="#fbfaf6"/>',
               f'<g transform="scale({self.k}) translate({-self.x0:.3f} {self.y1:.3f})">',
               '<g transform="scale(1 -1)">', *self.parts, "</g>", *self.labels, "</g>"]
        # North arrow + scale bar (pixel space)
        bar_m = 5 if w_m < 40 else 10
        out.append(f'<g font-family="sans-serif" font-size="12"><text x="{W - 30:.0f}" y="22" '
                   f'text-anchor="middle" font-weight="bold">N ↑</text>'
                   f'<rect x="12" y="{H - 22:.0f}" width="{bar_m * self.k:.0f}" height="6" fill="#222"/>'
                   f'<text x="12" y="{H - 28:.0f}">{bar_m} m ({bar_m / 0.3048:.0f} ft)</text></g>')
        y = H + 16
        out.append(f'<text x="12" y="{y:.0f}" font-family="sans-serif" font-size="13" '
                   f'font-weight="bold">{html.escape(title)}</text>')
        for label, color, count in legend:
            y += 16
            out.append(f'<rect x="12" y="{y - 10:.0f}" width="12" height="12" fill="{color}"/>'
                       f'<text x="30" y="{y:.0f}" font-family="sans-serif" font-size="12">'
                       f'{html.escape(label)} ×{count}</text>')
        out.append("</svg>")
        return "\n".join(out)


def render_preview(project: Project, scene: dict, out_svg: Path, plan_underlay: bool = True,
                   labels: bool = True) -> Path:
    boundary = scene["site"]["boundary"]
    # Frame the property (plus a margin for street trees, walks, etc.); far-reaching elements
    # like a modeled street just get clipped by the SVG viewport.
    x0, y0, x1, y1 = geometry.bbox(boundary)
    pad = max(4.0, 0.12 * max(x1 - x0, y1 - y0))
    svg = Svg((x0 - pad, y0 - pad, x1 + pad, y1 + pad), px_per_m=max(8.0, min(40.0, 1600 / max(x1 - x0, y1 - y0))))

    plan = project.spec.get("plan") or {}
    if plan_underlay and project.plan_frame and plan.get("image"):
        img = project.path(plan["image"])
        if img.exists():
            w, h = _image_size(img)
            data = base64.b64encode(img.read_bytes()).decode()
            mime = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
            svg.image(f"data:{mime};base64,{data}", project.plan_frame.svg_matrix(), w, h)

    svg.polygon(boundary, fill="none", stroke="#c33", width=0.12, dash="0.8,0.3")

    legend_counts: dict[str, list] = {}
    for rec in scene["elements"]:
        if rec["kind"] == "plant":
            sp = scene["species"][rec["species"]]
            rec = {**rec, "_category": sp["category"]}
            key = f"{sp['common']} ({rec['species']})"
            legend_counts.setdefault(key, [CATEGORY_FILL.get(sp["category"], "#4f8a3a"), 0])[1] += 1
        et = elements.get(rec["kind"])
        if et:
            et.preview(rec, svg)

    if labels:  # one label per source element, at its first record
        done = set()
        for rec in scene["elements"]:
            src = rec["source"]
            if src in done or rec["kind"] in ("lawn",):
                continue
            done.add(src)
            if rec["kind"] == "plant":
                svg.text((rec["x"], rec["y"] + rec["spread"] / 2 + 0.3), src, size=0.35, color="#234")
            elif "polygon" in rec:
                svg.text(geometry.centroid(rec["polygon"]), src, size=0.4, color="#222")

    legend = sorted(((k, c, n) for k, (c, n) in legend_counts.items()), key=lambda t: -t[2])
    title = f"{scene['project']['name']} — year {scene['growth']['year']:g}, month {scene['growth']['month']}"
    out_svg.write_text(svg.render(title, legend))
    return out_svg


def svg_to_png(svg: Path, png: Path, width: int = 1800) -> Path | None:
    magick = shutil.which("magick")
    if not magick:
        return None
    subprocess.run([magick, "-density", "96", str(svg), "-resize", f"{width}x", str(png)],
                   check=True, capture_output=True)
    return png


def _image_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:64 * 1024]
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    # JPEG: walk segments to SOF
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    out = subprocess.run(["magick", "identify", "-format", "%w %h", str(path)], capture_output=True, text=True)
    w, h = out.stdout.split()
    return int(w), int(h)
