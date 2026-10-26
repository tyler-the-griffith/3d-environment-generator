"""Output bookkeeping: where outputs go, which model each came from, and the gallery page.

Layout:  renders/<state>/<view>.png | <view>.mp4 | web/   (latest of each)
         renders/manifest.json   one entry per output: model fingerprint, detail, settings, time
         renders/index.html      the gallery (every state × view, stale outputs flagged)

An output is *stale* when the model its state would build today has a different
fingerprint than the one it was taken from — i.e. the design (or the framework) changed
since it was rendered.
"""
from __future__ import annotations

import html
import json
import time
from pathlib import Path

from .project import Project


def state_dir(project: Project, state: str) -> Path:
    d = project.renders / state
    d.mkdir(parents=True, exist_ok=True)
    return d


def manifest_path(project: Project) -> Path:
    return project.renders / "manifest.json"


def load_manifest(project: Project) -> dict:
    p = manifest_path(project)
    return json.loads(p.read_text()) if p.exists() else {"outputs": {}}


def record(project: Project, state: str, view: str, kind: str, file: Path, fp: str, scene: dict,
           variant: str | None, sets: list[str], output_type: str | None = None, cover: Path | None = None):
    m = load_manifest(project)
    r = scene["render"]
    m["outputs"][f"{state}/{view}"] = {
        "state": state, "view": view, "kind": kind, "file": str(file.relative_to(project.renders)),
        "type": output_type, "cover": str(cover.relative_to(project.renders)) if cover else None,
        "fingerprint": fp, "detail": r["quality"], "variant": variant, "sets": sets,
        "resolution": r["resolution"], "samples": r["samples"],
        "season_month": scene["growth"]["month"], "year": scene["growth"]["year"],
        "time": scene["environment"]["datetime"], "rendered": time.strftime("%Y-%m-%d %H:%M"),
    }
    manifest_path(project).write_text(json.dumps(m, indent=1))


def build_gallery(project: Project, current: dict[tuple[str, str], str]) -> Path:
    """current: {(state, detail): fingerprint today} — used to flag stale outputs."""
    m = load_manifest(project)
    outs = sorted(m["outputs"].values(), key=lambda o: (o["state"] != "base", o["state"], o["view"]))
    states = []
    for o in outs:
        if o["state"] not in states:
            states.append(o["state"])
    cards = []
    for st in states:
        items = []
        for o in (o for o in outs if o["state"] == st):
            fresh = current.get((o["state"], o["detail"]))
            stale = fresh is not None and fresh != o["fingerprint"]
            unknown = fresh is None
            badge = ("<span class=stale>stale — design changed since this was made</span>" if stale else
                     "<span class=unk>state no longer defined</span>" if unknown else "")
            src = html.escape(o["file"])
            if o["kind"] == "still":
                media = f'<a href="{src}"><img loading=lazy src="{src}"></a>'
            elif o["kind"] == "video":
                media = f'<video controls preload=metadata src="{src}"></video>'
            else:
                media = (f'<a class=web href="{src}"><span>Open 3D walkthrough ↗</span>'
                         f'<small>orbit or walk with WASD in the browser</small></a>')
            when = o["time"][:16].replace("T", " ")
            items.append(
                f'<figure class="{"is-stale" if stale else ""}">{media}<figcaption><b>{html.escape(o["view"])}</b>'
                f' · {o["detail"]} · year {o["year"]} · {html.escape(when)}<br>'
                f'<code title="model fingerprint">{o["fingerprint"]}</code> · {o["rendered"]} {badge}'
                f'</figcaption></figure>')
        cards.append(f'<section><h2>{html.escape(st)}</h2><div class=grid>{"".join(items)}</div></section>')
    page = f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>{html.escape(project.name)} renders</title>
<style>
:root {{ --bg:#f6f5f1; --card:#fff; --ink:#1f2a22; --muted:#5d665f; --warn:#a4400c; --line:#e3e1da; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16191b; --card:#1f2427; --ink:#e7ebe8; --muted:#9aa39d; --warn:#f0a070; --line:#2c3236; }} }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.45 system-ui, sans-serif; }}
header {{ padding:24px 16px 8px; max-width:1400px; margin:auto; }}
header p {{ color:var(--muted); margin:4px 0 0; }}
section {{ max-width:1400px; margin:auto; padding:8px 16px 24px; }}
h2 {{ font-size:18px; margin:16px 0 10px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(min(100%, 380px), 1fr)); gap:14px; }}
figure {{ margin:0; background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
figure img, figure video {{ display:block; width:100%; aspect-ratio:16/9; object-fit:cover; background:#0002; }}
figure.is-stale img, figure.is-stale video {{ opacity:.55; }}
figcaption {{ padding:8px 10px; font-size:13px; color:var(--muted); }}
figcaption b {{ color:var(--ink); }}
code {{ font-size:12px; }}
.stale {{ color:var(--warn); font-weight:600; }} .unk {{ color:var(--muted); font-style:italic; }}
a.web {{ display:grid; place-content:center; text-align:center; aspect-ratio:16/9; color:var(--ink);
         text-decoration:none; background:linear-gradient(135deg,#6f9a50,#2f5a3a); }}
a.web span {{ font-size:18px; font-weight:600; color:#fff; }} a.web small {{ color:#e3efe0; }}
</style></head><body>
<header><h1>{html.escape(project.name)}</h1>
<p>Every image, video and walkthrough on this page was taken from the saved 3D model named by its
fingerprint. Outputs from an older version of the design are marked stale.</p></header>
{"".join(cards) or "<section><p>No outputs yet — run <code>lv render</code>.</p></section>"}
</body></html>"""
    out = project.renders / "index.html"
    out.write_text(page)
    return out
