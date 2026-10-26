"""Publish a node's outputs as a web page.

`build_site` turns the outputs of chosen types into a static site (index.html + assets) styled
with the user's app theme: same color tokens, fonts and corner radius, so it looks like the app.
The site works from any static host and from a downloaded folder (the walkthrough needs to be
served over http, so the page links a standalone copy for file:// use).

`publish_github` pushes that site to a GitHub repository and turns on GitHub Pages, through the
`gh` CLI the user is already signed in to. Pages sites from free accounts are public, so the UI
asks for an explicit confirmation first. Everything published comes from one rebuilt site folder.

Site:    <project>/renders/_site/<state>/       (regenerable, inside the ignored renders/)
Deploy:  <project>/renders/_site/.deploy-<state>/  (a git checkout of the published repo)
Record:  <node app dir>/publish.json
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config, generate, store
from .jobs import Job

# What a page can show, in page order. The primary visuals lead.
PAGE_ORDER = ["stills", "plan_check", "web_walkthrough", "orbit_video", "walk_video", "panorama", "seasons", "growth",
              "dusk", "photo_match", "report", "plant_list", "model_file"]
DEFAULT_TYPES = ["stills", "plan_check", "web_walkthrough", "orbit_video", "walk_video"]
# Files GitHub refuses (100 MB hard limit); stay safely under it.
MAX_FILE = 95 * 1024 * 1024


def site_dir(node: store.Node) -> Path:
    return node.project_dir / "renders" / "_site" / node.state


def _deploy_dir(node: store.Node) -> Path:
    return node.project_dir / "renders" / "_site" / f".deploy-{node.state}"


def record(node: store.Node) -> dict:
    p = node.app_dir / "publish.json"
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_record(node: store.Node, **kw):
    r = record(node)
    r.update(kw)
    (node.app_dir / "publish.json").write_text(json.dumps(r, indent=1))
    return r


def status(node: store.Node) -> dict:
    data = generate.list_outputs(node)
    labels = {t["key"]: t["label"] for t in generate.OUTPUT_TYPES}
    available = [{"key": k, "label": labels[k], "count": len(data["groups"][k]),
                  "stale": sum(1 for o in data["groups"][k] if o["stale"])}
                 for k in PAGE_ORDER if data["groups"].get(k)]
    r = record(node)
    sd = site_dir(node)
    built = (sd / "index.html").exists()
    rel = sd.relative_to(config.projects_root()).as_posix()
    return {
        "available": available,
        "defaults": [a["key"] for a in available if a["key"] in DEFAULT_TYPES],
        "built": built, "built_at": r.get("built_at"), "preview_url": f"/files/{rel}/index.html" if built else None,
        "options": r.get("options"), "github": {**gh_status(), **(r.get("github") or {})},
        "size_mb": round(sum(f.stat().st_size for f in sd.rglob("*") if f.is_file()) / 1e6, 1) if built else 0,
    }


# ---------------------------------------------------------------- the site


def _img(src: Path, dst: Path, width: int, quality: int = 84):
    """A web-sized JPEG (renders are large PNGs). Falls back to a plain copy without ImageMagick."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tool = shutil.which("magick") or shutil.which("convert")
    if tool:
        r = subprocess.run([tool, str(src), "-resize", f"{width}x{width}>", "-strip", "-quality", str(quality),
                            str(dst)], capture_output=True)
        if r.returncode == 0:
            return dst
    out = dst.with_suffix(src.suffix)
    shutil.copy2(src, out)
    return out


def _src(o) -> Path:
    import urllib.parse
    return config.projects_root() / urllib.parse.unquote(o["url"][len("/files/"):])


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "item"


def _theme_css(theme: dict) -> str:
    light, dark = theme.get("light") or {}, theme.get("dark") or {}
    mode = theme.get("mode", "system")

    def block(p):
        return "".join(f"--{k}:{v};" for k, v in p.items() if re.fullmatch(r"[a-z0-9-]+", k)
                       and re.fullmatch(r"#[0-9a-fA-F]{3,8}|rgba?\([0-9., /]+\)", str(v)))
    font = theme.get("font") or 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
    radius = int(theme.get("radius", 10))
    base = f":root{{{block(light)}--font-ui:{font};--radius:{radius}px;color-scheme:light}}"
    darkcss = f"{block(dark)}color-scheme:dark;--shadow:0 1px 2px rgb(0 0 0/.4),0 6px 20px rgb(0 0 0/.35)"
    if mode == "dark":
        return f":root{{{block(dark)}--font-ui:{font};--radius:{radius}px;{darkcss}}}"
    if mode == "light":
        return base
    return base + f"@media (prefers-color-scheme: dark){{:root{{{darkcss}}}}}"


def build_site(job: Job | None, node: store.Node, options: dict) -> Path:
    """Rebuild the site folder from the current outputs. options: title, subtitle, types, theme."""
    say = (job.say if job else print)
    step = (job.set_step if job else (lambda s, p=None: print(s)))
    title = (options.get("title") or node.meta()["title"]).strip()
    subtitle = (options.get("subtitle") or "").strip()
    types = [k for k in PAGE_ORDER if k in (options.get("types") or DEFAULT_TYPES)]
    data = generate.list_outputs(node)
    labels = {t["key"]: t["label"] for t in generate.OUTPUT_TYPES}
    explains = {t["key"]: t["explain"] for t in generate.OUTPUT_TYPES}
    out = site_dir(node)
    tmp = out.with_name(out.name + ".building")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    sections, skipped, gallery = [], [], []
    total = sum(len(data["groups"].get(k, [])) for k in types) or 1
    done = 0
    for key in types:
        items = data["groups"].get(key) or []
        if not items:
            continue
        cards, extra = [], []
        for o in items:
            done += 1
            step(f"Adding {labels[key].lower()}: {o['label']}", done / total * 0.9)
            src = _src(o)
            if not src.exists():
                continue
            name = f"{key}/{_slug(o['label'])}"
            label = html.escape(o["label"])
            if o["kind"] == "still":
                full = _img(src, tmp / f"media/{name}.jpg", 2560)
                thumb = _img(src, tmp / f"media/{name}-thumb.jpg", 900, 78)
                gallery.append({"src": full.relative_to(tmp).as_posix(), "title": f"{labels[key]}: {o['label']}"})
                cards.append(f'<button class="ocard" data-i="{len(gallery) - 1}"><span class="ocover">'
                             f'<img src="{thumb.relative_to(tmp).as_posix()}" alt="{label}" loading="lazy"></span>'
                             f'<span class="name">{label}</span></button>')
            elif o["kind"] == "video":
                if src.stat().st_size > MAX_FILE:
                    skipped.append(f"{o['label']} (video over 95 MB)")
                    continue
                dst = tmp / f"media/{name}{src.suffix}"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                poster = ""
                if o.get("cover_url"):
                    poster = _img(_src({"url": o["cover_url"]}), tmp / f"media/{name}-poster.jpg", 1600)
                    poster = f' poster="{poster.relative_to(tmp).as_posix()}"'
                cards.append(f'<figure class="vcard"><video controls preload="none" playsinline{poster} '
                             f'src="{dst.relative_to(tmp).as_posix()}"></video><figcaption>{label}</figcaption></figure>')
            elif o["kind"] == "web":
                wdir = tmp / f"walkthrough/{_slug(o['state'])}"
                wdir.mkdir(parents=True, exist_ok=True)
                for f in ("viewer.html", "model.glb", "model.json"):
                    if (src.parent / f).exists():
                        shutil.copy2(src.parent / f, wdir / f)
                if (wdir / "model.glb").stat().st_size > MAX_FILE:
                    skipped.append(f"{o['label']} walkthrough (model over 95 MB)")
                    shutil.rmtree(wdir)
                    continue
                _theme_viewer(wdir, title, options.get("theme") or {})
                rel = wdir.relative_to(tmp).as_posix()
                cards.append(f'<div class="walk"><iframe src="{rel}/viewer.html" title="3D walkthrough: {label}" '
                             f'loading="lazy" allow="fullscreen"></iframe><div class="walk-bar"><span>{label}</span>'
                             f'<a class="btn" href="{rel}/viewer.html" target="_blank" rel="noopener">Open full screen</a>'
                             f'</div></div>')
            elif o["kind"] == "pdf":
                dst = tmp / f"media/{name}.pdf"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                extra.append(f'<a class="btn" href="{dst.relative_to(tmp).as_posix()}" target="_blank" rel="noopener">'
                             f'Open {label} (PDF)</a>')
            elif src.suffix == ".csv":
                dst = tmp / f"media/{name}.csv"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                rows = [r.split(",") for r in src.read_text().strip().splitlines()]
                head = "".join(f"<th>{html.escape(c.replace('_', ' '))}</th>" for c in rows[0])
                body = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
                extra.append(f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
                             f'</div><a class="btn" href="{dst.relative_to(tmp).as_posix()}" download>Download CSV</a>')
            else:
                if src.stat().st_size > MAX_FILE:
                    skipped.append(f"{o['label']} (file over 95 MB)")
                    continue
                dst = tmp / f"media/{name}{src.suffix}"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                extra.append(f'<a class="btn" href="{dst.relative_to(tmp).as_posix()}" download>Download {label}</a>')
        grid = {"stills": "ocards", "web_walkthrough": "walks"}.get(key, "ocards")
        if any("vcard" in c for c in cards):
            grid = "vcards"
        sections.append(f'<section class="section" id="{key}"><h2>{html.escape(labels[key])}</h2>'
                        f'<p class="explain">{html.escape(explains[key])}</p>'
                        + (f'<div class="{grid}">{"".join(cards)}</div>' if cards else "")
                        + (f'<div class="extra">{"".join(extra)}</div>' if extra else "") + "</section>")
    if not sections:
        shutil.rmtree(tmp)
        raise RuntimeError("none of the chosen output types has anything made yet")
    step("Writing the page", 0.95)
    b = config.brand()
    logo = ""
    if b.get("has_logo"):
        shutil.copy2(config.APP_DIR / b.get("logo", "branding/logo.png"), tmp / "logo.png")
        logo = '<img class="logo" src="logo.png" alt="">'
    nav = "".join(f'<a href="#{k}">{html.escape(labels[k])}</a>' for k in types if f'id="{k}"' in "".join(sections))
    page = (PAGE.replace("{{THEME}}", _theme_css(options.get("theme") or {}))
            .replace("{{TITLE}}", html.escape(title))
            .replace("{{SUBTITLE}}", f'<p class="sub">{html.escape(subtitle)}</p>' if subtitle else "")
            .replace("{{LOGO}}", logo).replace("{{NAV}}", nav)
            .replace("{{SECTIONS}}", "".join(sections))
            .replace("{{GALLERY}}", json.dumps(gallery).replace("</", "<\\/"))
            .replace("{{FOOTER}}", f"Made with {html.escape(b['name'])} · {time.strftime('%B %-d, %Y')}"))
    (tmp / "index.html").write_text(page)
    (tmp / ".nojekyll").write_text("")
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    if skipped:
        say("Left out: " + "; ".join(skipped))
    _save_record(node, built_at=time.time(), options={k: options.get(k) for k in ("title", "subtitle", "types")},
                 skipped=skipped)
    return out


def _theme_viewer(wdir: Path, title: str, theme: dict):
    """Give the walkthrough's overlay the site's colors and title (and drop the project name)."""
    v = wdir / "viewer.html"
    text = v.read_text()
    light, dark = theme.get("light") or {}, theme.get("dark") or {}
    if light.get("bg") and dark.get("bg"):
        text = re.sub(r":root \{ --bg: [^}]*\}\n\s*@media \(prefers-color-scheme: dark\) \{[^\n]*\n",
                      f":root {{ --bg: {light['surface-2']}; --panel: {light['surface']}e6; --ink: {light['ink']}; }}\n"
                      f"  @media (prefers-color-scheme: dark) {{ :root {{ --bg: {dark['bg']}; --panel: {dark['surface']}e6; "
                      f"--ink: {dark['ink']}; }} }}\n", text, count=1)
    v.write_text(text.replace("<title>Landscape Walkthrough</title>", f"<title>{html.escape(title)}: 3D walkthrough</title>"))
    info = wdir / "model.json"
    if info.exists():
        d = json.loads(info.read_text())
        d["name"] = title
        info.write_text(json.dumps(d, indent=1))


# ---------------------------------------------------------------- GitHub Pages


def _gh(*args, check=True, cwd=None) -> subprocess.CompletedProcess:
    r = subprocess.run([shutil.which("gh") or "gh", *args], capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"gh {args[0]} failed")
    return r


_GH_CACHE: dict = {}


def gh_status() -> dict:
    """Whether the GitHub CLI is installed and signed in (cached for a minute)."""
    if _GH_CACHE.get("t", 0) > time.time() - 60:
        return _GH_CACHE["v"]
    if not shutil.which("gh"):
        v = {"gh": False, "user": None}
    else:
        r = subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True)
        v = {"gh": True, "user": r.stdout.strip() if r.returncode == 0 else None}
    _GH_CACHE.update(t=time.time(), v=v)
    return v


def publish_github(job: Job, node: store.Node, repo: str, options: dict):
    gh = gh_status()
    if not gh["gh"]:
        raise RuntimeError("the GitHub CLI (gh) isn't installed: https://cli.github.com")
    if not gh["user"]:
        raise RuntimeError("the GitHub CLI isn't signed in: run `gh auth login` in a terminal")
    repo = repo.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", repo):
        raise RuntimeError("repository names may only use letters, digits, '.', '-' and '_'")
    full = f"{gh['user']}/{repo}"
    build_site(job, node, options)
    site = site_dir(node)
    job.set_step(f"Preparing {full}", 0.92)
    exists = _gh("repo", "view", full, "--json", "name", check=False).returncode == 0
    if not exists:
        job.say(f"Creating the public repository {full}")
        _gh("repo", "create", full, "--public", "--description", f"{options.get('title') or node.meta()['title']}"
            " (published from 3D Environment Generator)")
    dep = _deploy_dir(node)
    git = shutil.which("git") or "git"
    if not (dep / ".git").exists():
        shutil.rmtree(dep, ignore_errors=True)
        dep.mkdir(parents=True)
        subprocess.run([git, "init", "-q", "-b", "main"], cwd=dep, check=True)
    subprocess.run([git, "remote", "remove", "origin"], cwd=dep, capture_output=True)
    subprocess.run([git, "remote", "add", "origin", f"https://github.com/{full}.git"], cwd=dep, check=True)
    for f in dep.iterdir():  # replace the checkout's contents with the fresh site
        if f.name != ".git":
            shutil.rmtree(f) if f.is_dir() else f.unlink()
    shutil.copytree(site, dep, dirs_exist_ok=True)
    who = ["-c", f"user.name={gh['user']}", "-c", f"user.email={gh['user']}@users.noreply.github.com"]
    subprocess.run([git, "add", "-A"], cwd=dep, check=True)
    subprocess.run([git, *who, "commit", "-q", "--allow-empty", "-m", f"Publish {time.strftime('%Y-%m-%d %H:%M')}"],
                   cwd=dep, check=True)
    job.set_step(f"Uploading to {full}", 0.95)
    # One-shot credential helper: gh's token, no change to the user's git config.
    job.run([git, "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
             "push", "-q", "-f", "origin", "main"], cwd=dep)
    r = _gh("api", f"repos/{full}/pages", check=False)
    if r.returncode != 0:
        job.say("Turning on GitHub Pages")
        _gh("api", "-X", "POST", f"repos/{full}/pages", "-f", "source[branch]=main", "-f", "source[path]=/")
        r = _gh("api", f"repos/{full}/pages", check=False)
    url = json.loads(r.stdout).get("html_url") if r.returncode == 0 else f"https://{gh['user']}.github.io/{repo}/"
    _save_record(node, github={"repo": full, "url": url, "published_at": time.time()})
    job.say(f"Published: {url} (GitHub takes a minute or two to put a new version online)")
    job.result = {"url": url}
    return job.result


def take_down(job: Job, node: store.Node):
    """Turn GitHub Pages off for the published repository (the repository itself stays)."""
    full = (record(node).get("github") or {}).get("repo")
    if not full:
        raise RuntimeError("this design hasn't been published to GitHub")
    job.set_step(f"Turning off GitHub Pages for {full}")
    r = _gh("api", "-X", "DELETE", f"repos/{full}/pages", check=False)
    if r.returncode != 0 and "404" not in (r.stderr + r.stdout):
        raise RuntimeError(r.stderr.strip() or "couldn't turn off GitHub Pages")
    _save_record(node, github={"repo": full, "url": None, "taken_down_at": time.time()})
    job.say(f"The page is offline. The repository https://github.com/{full} still exists and is public: "
            "delete it on GitHub (Settings → Delete this repository) to remove the files too.")


# ---------------------------------------------------------------- page template (mirrors app.css)

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
{{THEME}}
:root{--shadow:0 1px 2px rgb(0 0 0/.06),0 4px 16px rgb(0 0 0/.06);--pad:12px}
*{box-sizing:border-box}
html{font-size:15px;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:1rem/1.5 var(--font-ui)}
a{color:var(--accent)}
.top{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:12px;padding:10px 20px;background:var(--surface);border-bottom:1px solid var(--line)}
.top .logo{width:30px;height:30px;object-fit:contain}
.top b{font-size:1.02rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.top nav{display:flex;gap:2px;background:var(--surface-2);padding:3px;border-radius:calc(var(--radius)*.9);margin-left:auto;overflow-x:auto;max-width:100%}
.top nav a{color:var(--muted);text-decoration:none;font-weight:550;padding:5px 12px;border-radius:calc(var(--radius)*.7);white-space:nowrap}
.top nav a:hover{background:var(--surface);color:var(--ink)}
main{max-width:1200px;margin:0 auto;padding:24px 20px 60px}
.hero h1{margin:6px 0 4px;font-size:1.9rem;line-height:1.2}
.hero .sub{color:var(--muted);margin:0 0 6px;font-size:1.05rem}
.section{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:calc(var(--pad)*1.5);margin:calc(var(--pad)*1.4) 0;box-shadow:var(--shadow);scroll-margin-top:70px}
.section h2{margin:0 0 4px;font-size:1.15rem}
.explain{color:var(--muted);font-size:.9rem;margin:0 0 12px}
.ocards{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,300px),1fr));gap:12px}
.ocard{display:flex;flex-direction:column;text-align:left;padding:0;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;background:var(--surface);cursor:zoom-in;color:inherit;font:inherit;box-shadow:var(--shadow)}
.ocard:hover{border-color:var(--accent)}
.ocover{aspect-ratio:16/9;background:var(--surface-3);display:block;overflow:hidden}
.ocover img{width:100%;height:100%;object-fit:cover;display:block}
.ocard .name{padding:8px 10px;font-weight:600;font-size:.9rem}
.vcards{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,420px),1fr));gap:12px}
.vcard{margin:0;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;background:var(--surface-3)}
.vcard video{width:100%;display:block;aspect-ratio:16/9;background:#000}
.vcard figcaption{padding:8px 10px;font-weight:600;font-size:.9rem;background:var(--surface)}
.walks{display:grid;gap:12px}
.walk{border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
.walk iframe{width:100%;height:min(70vh,640px);border:0;display:block;background:var(--surface-3)}
.walk-bar{display:flex;align-items:center;gap:8px;padding:8px 10px;font-weight:600;font-size:.9rem}
.walk-bar span{flex:1}
.extra{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;align-items:flex-start}
.btn{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--surface);color:var(--ink);padding:6px 12px;border-radius:calc(var(--radius)*.75);font-weight:550;text-decoration:none;cursor:pointer;font:inherit}
.btn:hover{background:var(--surface-2)}
.table-wrap{overflow:auto;max-height:420px;border:1px solid var(--line);border-radius:var(--radius);width:100%}
table{border-collapse:collapse;width:100%;font-size:.85rem}
th,td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{position:sticky;top:0;background:var(--surface-2)}
footer{color:var(--muted);font-size:.82rem;text-align:center;padding:10px 0 30px}
.lb{position:fixed;inset:0;background:rgb(0 0 0/.88);z-index:20;display:none;grid-template-rows:auto minmax(0,1fr)}
.lb.open{display:grid}
.lb header{display:flex;gap:10px;align-items:center;padding:10px 14px;color:#eee}
.lb header span{flex:1}
.lb header .btn{background:rgb(255 255 255/.1);color:#fff;border-color:rgb(255 255 255/.2)}
.lb .stage{position:relative;display:flex;align-items:center;justify-content:center;min-height:0;padding:0 84px 20px}
.lb img{max-width:100%;max-height:100%;object-fit:contain;border-radius:6px}
.lb .nav{position:absolute;top:50%;transform:translateY(-50%);width:52px;height:52px;border-radius:50%;border:1px solid rgb(255 255 255/.25);background:rgb(0 0 0/.45);color:#fff;font-size:26px;cursor:pointer}
.lb .prev{left:16px}.lb .next{right:16px}
@media (max-width:640px){.top{flex-wrap:wrap}.top nav{margin-left:0;width:100%}.lb .stage{padding:0 8px 12px}.lb .nav{display:none}.hero h1{font-size:1.5rem}}
</style>
</head>
<body>
<header class="top">{{LOGO}}<b>{{TITLE}}</b><nav>{{NAV}}</nav></header>
<main>
<div class="hero"><h1>{{TITLE}}</h1>{{SUBTITLE}}</div>
{{SECTIONS}}
<footer>{{FOOTER}}</footer>
</main>
<div class="lb" id="lb" role="dialog" aria-modal="true">
<header><span id="lb-title"></span><span id="lb-count" style="flex:0;color:#bbb"></span><a class="btn" id="lb-dl" download>Download</a><button class="btn" id="lb-close">Close</button></header>
<div class="stage"><button class="nav prev" id="lb-prev" aria-label="Previous">&#8249;</button><img id="lb-img" alt=""><button class="nav next" id="lb-next" aria-label="Next">&#8250;</button></div>
</div>
<script>
const G = {{GALLERY}};
const lb = document.getElementById("lb"), img = document.getElementById("lb-img");
let i = 0;
function show(n) { i = (n + G.length) % G.length; img.src = G[i].src; document.getElementById("lb-title").textContent = G[i].title;
  document.getElementById("lb-count").textContent = G.length > 1 ? (i + 1) + " / " + G.length : "";
  document.getElementById("lb-dl").href = G[i].src; lb.classList.add("open"); }
document.querySelectorAll(".ocard[data-i]").forEach(b => b.onclick = () => show(+b.dataset.i));
document.getElementById("lb-close").onclick = () => lb.classList.remove("open");
document.getElementById("lb-prev").onclick = () => show(i - 1);
document.getElementById("lb-next").onclick = () => show(i + 1);
lb.onclick = e => { if (e.target === lb || e.target.classList.contains("stage")) lb.classList.remove("open"); };
addEventListener("keydown", e => { if (!lb.classList.contains("open")) return;
  if (e.key === "Escape") lb.classList.remove("open"); if (e.key === "ArrowLeft") show(i - 1); if (e.key === "ArrowRight") show(i + 1); });
</script>
</body>
</html>
"""
