"""Presentation report (PDF) and plant schedule (CSV) for a design state.

Both are built from the same resolved design the model is built from, plus the images already
taken from that model (renders whose fingerprint matches today's). Nothing here draws its own
picture of the design.
"""
from __future__ import annotations

import csv
import shutil
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import geometry
from .outputs import load_manifest
from .pdf import Document, text_width

FT = 0.3048


def plant_schedule(scene: dict) -> list[dict]:
    counts = Counter(e["species"] for e in scene["elements"] if e["kind"] == "plant")
    heights = defaultdict(list)
    for e in scene["elements"]:
        if e["kind"] == "plant":
            heights[e["species"]].append((e["height"], e["spread"]))
    rows = []
    for sid, n in sorted(counts.items(), key=lambda kv: (scene["species"][kv[0]]["category"], kv[0])):
        sp = scene["species"][sid]
        hs = heights[sid]
        rows.append({
            "species_id": sid, "common_name": sp["common"], "botanical_name": sp["botanical"],
            "category": sp["category"], "quantity": n,
            "avg_height_ft": round(sum(h for h, _ in hs) / len(hs) / FT, 1),
            "avg_spread_ft": round(sum(s for _, s in hs) / len(hs) / FT, 1),
            "evergreen": "yes" if sp["evergreen"] else "no",
        })
    return rows


def area_summary(scene: dict) -> list[tuple[str, float]]:
    totals = Counter()
    for e in scene["elements"]:
        if e["kind"] in ("lawn", "bed", "paving") and "polygon" in e:
            label = {"lawn": "Lawn", "bed": "Planting beds", "paving": "Paving & paths"}[e["kind"]]
            totals[label] += geometry.area(e["polygon"])
    return [(k, v / FT / FT) for k, v in totals.items()]


def write_csv(scene: dict, out: Path) -> Path:
    rows = plant_schedule(scene)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["species_id"])
        w.writeheader()
        w.writerows(rows)
    return out


def _jpeg(src: Path, tmp: Path) -> Path:
    out = tmp / (src.stem + ".jpg")
    subprocess.run(["magick", str(src), "-resize", "2000x2000>", "-quality", "88", str(out)], check=True)
    return out


def write_pdf(project, state: str, scene: dict, fp: str, current_fps: set, out: Path) -> Path:
    """Report from today's renders of this state (stale images are left out)."""
    W, H = 792.0, 612.0
    doc = Document(title=f"{project.name} - {state}")
    m = load_manifest(project)
    entries = [o for o in m["outputs"].values()
               if o["state"].split("~")[0] == state.split("~")[0] and o["kind"] == "still"
               and o["fingerprint"] in current_fps]
    order = {"plan-check": 0}
    entries.sort(key=lambda o: (order.get(o["view"], 1), o["state"] != state, o["state"], o["view"]))

    tmp = Path(tempfile.mkdtemp())
    try:
        # Title page
        p = doc.page()
        p.rect(0, 0, W, H, fill=(0.96, 0.96, 0.94))
        p.text(54, H - 120, project.name, size=30, bold=True)
        p.text(54, H - 150, "Landscape design presentation" + (f" — {state}" if state != "base" else ""),
               size=14, color=(0.35, 0.38, 0.35))
        y = H - 200
        g = scene["growth"]
        for k, v in (("Prepared", time.strftime("%B %d, %Y")),
                     ("Growth shown", f"{g['year']:g} years after planting" if g["year"] != "mature" else "mature"),
                     ("Model", fp)):
            p.text(54, y, k, size=10, bold=True, color=(0.35, 0.38, 0.35))
            p.text(170, y, str(v), size=10)
            y -= 18
        y -= 14
        p.text(54, y, "Areas", size=12, bold=True)
        y -= 18
        for label, sqft in area_summary(scene):
            p.text(54, y, label, size=10)
            p.text(220, y, f"{sqft:,.0f} sq ft", size=10)
            y -= 15
        if entries:
            hero = next((o for o in entries if o["view"] != "plan-check"), entries[0])
            p.image(_jpeg(project.renders / hero["file"], tmp), 330, 110, 410, 380)

        # Image pages
        for o in entries:
            p = doc.page()
            caption = o["view"].replace("-", " ").title()
            sub = o["state"].replace("~", " · ").replace("=", " ")
            p.text(40, H - 40, caption, size=16, bold=True)
            p.text(40 + text_width(caption, 16, True) + 12, H - 40, sub, size=10, color=(0.4, 0.43, 0.4))
            p.image(_jpeg(project.renders / o["file"], tmp), 40, 40, W - 80, H - 100)

        # Plant schedule
        rows = plant_schedule(scene)
        cols = [("Common name", 40), ("Botanical name", 220), ("Type", 420), ("Qty", 500),
                ("Height", 545), ("Spread", 610), ("Evergreen", 675)]
        p, y = None, 0
        for i, r in enumerate(rows):
            if p is None or y < 50:
                p = doc.page()
                p.text(40, H - 40, "Plant schedule", size=16, bold=True)
                y = H - 75
                for label, x in cols:
                    p.text(x, y, label, size=9, bold=True, color=(0.35, 0.38, 0.35))
                y -= 8
                p.line(40, y, W - 40, y)
                y -= 16
            vals = [r["common_name"], r["botanical_name"], r["category"], str(r["quantity"]),
                    f"{r['avg_height_ft']} ft", f"{r['avg_spread_ft']} ft", r["evergreen"]]
            if i % 2:
                p.rect(36, y - 4, W - 72, 16, fill=(0.95, 0.95, 0.93))
            for (label, x), v in zip(cols, vals):
                p.text(x, y, v[:34], size=9)
            y -= 16
        doc.save(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out
