"""Local HTTP server: the app window's backend (JSON API + static UI + project files).

Binds to 127.0.0.1 only. Every request except the page itself must carry the per-launch
session cookie (set when the page loads, SameSite=Strict), so other websites open in the same
browser can't drive the API.
"""
from __future__ import annotations

import json
import mimetypes
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import urllib.parse
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import ai, config, generate, store
from .jobs import BUS, MANAGER

TOKEN = secrets.token_urlsafe(24)
ROUTES = []


def route(method, pattern):
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def node_or_404(nid: str) -> store.Node:
    try:
        return store.Node(urllib.parse.unquote(nid))
    except (FileNotFoundError, ValueError):
        raise ApiError(404, f"no project or variant {nid!r}") from None


# ---------------------------------------------------------------- app / settings


@route("GET", "/api/app")
def api_app(h, m):
    s = config.load_settings()
    return {"brand": config.brand(), "settings": config.public_settings(s),
            "input_types": store.INPUT_TYPES, "output_types": generate.OUTPUT_TYPES,
            "providers": ai.provider_status(), "ai_name": ai.display_name(),
            "settings_file": str(config.settings_path()), "repo": str(config.REPO_ROOT),
            "gpu": _gpu_status()}


def _gpu_status():
    from landviz import blender_bridge
    try:
        vms = blender_bridge.running_vms()
    except Exception:  # noqa: BLE001
        vms = []
    return {"vm_running": bool(vms), "vms": vms}


@route("PUT", "/api/settings")
def api_settings(h, m):
    patch = h.json()
    ai_patch = patch.get("ai", {})
    if "api_key" in ai_patch and ai_patch["api_key"].startswith("••••"):
        ai_patch.pop("api_key")  # masked value echoed back — keep the stored key
    s = config.save_settings(patch)
    BUS.publish("settings", config.public_settings(s))
    return {"settings": config.public_settings(s), "providers": ai.provider_status(), "ai_name": ai.display_name()}


# ---------------------------------------------------------------- tree / nodes


@route("GET", "/api/tree")
def api_tree(h, m):
    return {"tree": store.tree()}


@route("POST", "/api/projects")
def api_new_project(h, m):
    title = (h.json().get("title") or "").strip() or "Untitled property"
    n = store.create_project(title)
    BUS.publish("tree_changed", {})
    return {"node": n.id}


@route("POST", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/variants")
def api_new_variant(h, m):
    parent = node_or_404(m["nid"])
    title = (h.json().get("title") or "").strip() or "New variant"
    n = store.create_variant(parent, title)
    BUS.publish("tree_changed", {})
    return {"node": n.id}


@route("GET", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)")
def api_node(h, m):
    n = node_or_404(m["nid"])
    meta = n.meta()
    # A design edited outside the app (a text editor, or a resumed Claude Code session) is
    # versioned the next time it's looked at, so the history never misses a change.
    if n.design_path.exists() and n.versions() and not MANAGER.busy_for(n.id):
        n.snapshot_design("external", "changed outside the app")
    d = n.inputs()
    for r in d["rounds"]:  # which rounds can be continued in Claude Code
        if r.get("provider") == "claude_code" and r["status"] not in ("queued", "running"):
            r["session_id"] = ai.session_id_for(n, r)
    root = config.projects_root().resolve()
    for it in d["items"]:  # browser URLs for thumbnails / opening input files
        for f in it.get("files", []):
            p = store.file_path(n, f).resolve()
            f["url"] = f"/files/{p.relative_to(root).as_posix()}" if str(p).startswith(str(root)) else None
    design = n.design_path.read_text() if n.design_path.exists() else ""
    renders_rel = (n.project_dir / "renders").relative_to(config.projects_root()).as_posix()
    check = n.project_dir / "renders" / n.state / "plan-check.png"
    return {
        "id": n.id, "meta": meta, "variant": n.variant, "project": n.project_id,
        "parent_title": store.Node(meta["parent"]).meta()["title"] if meta.get("parent") else None,
        "inputs": d, "output_spec": n.output_spec(), "design": design, "design_name": n.design_name,
        "versions": n.versions(), "issues": ai.validate(n),
        "check_image": f"/files/{renders_rel}/{n.state}/plan-check.png?v={int(check.stat().st_mtime)}"
        if check.exists() else None,
        "busy": [j.public() for j in MANAGER.busy_for(n.id)],
        "unpulled_edits": _unpulled(n),
        "files_root": f"/files/{n.dir.relative_to(config.projects_root()).as_posix()}/app",
    }


def _has_children(tree_node, nid) -> bool:
    for c in tree_node.get("children", []):
        if c["id"] == nid:
            return bool(c["children"])
        if _has_children(c, nid):
            return True
    return False


def _unpulled(n: store.Node) -> bool:
    from landviz import project as project_mod
    from landviz.models import ModelStore
    try:
        st = ModelStore(project_mod.load(str(n.project_dir), n.variant))
        return any(st.is_edited(p.stem) for p in st.dir.glob("*.blend"))
    except Exception:  # noqa: BLE001
        return False


@route("PATCH", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)")
def api_patch_node(h, m):
    n = node_or_404(m["nid"])
    body = h.json()
    if "title" in body:
        n.set_meta(title=body["title"].strip() or n.meta()["title"])
    BUS.publish("tree_changed", {})
    return {"ok": True}


@route("DELETE", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)")
def api_archive_node(h, m):
    n = node_or_404(m["nid"])
    if MANAGER.busy_for(n.id):
        raise ApiError(409, "wait for this project's running jobs to finish")
    if n.variant:  # a variant with children of its own keeps them only if archived together
        kids = [c for c in store.tree() if c["id"] == n.project_id]
        if kids and _has_children(kids[0], n.id):
            raise ApiError(409, "archive this variant's child variants first")
    dest = store.archive(n)  # a project is archived together with all of its variants
    BUS.publish("tree_changed", {})
    return {"archived_to": str(dest)}


# ---------------------------------------------------------------- inputs


@route("POST", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/inputs")
def api_add_input(h, m):
    n = node_or_404(m["nid"])
    b = h.json()
    item = store.add_input(n, b.get("type", "spec"), b.get("title", ""), b.get("text", ""), b.get("folder", ""))
    BUS.publish("node_changed", {"node": n.id})
    return {"item": item}


@route("PATCH", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/inputs/(?P<iid>\w+)")
def api_patch_input(h, m):
    n = node_or_404(m["nid"])
    try:
        item = store.update_input(n, m["iid"], h.json())
    except PermissionError as e:
        raise ApiError(409, str(e)) from None
    BUS.publish("node_changed", {"node": n.id})
    return {"item": item}


@route("DELETE", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/inputs/(?P<iid>\w+)")
def api_delete_input(h, m):
    n = node_or_404(m["nid"])
    try:
        store.delete_input(n, m["iid"])
    except PermissionError as e:
        raise ApiError(409, str(e)) from None
    BUS.publish("node_changed", {"node": n.id})
    return {"ok": True}


@route("PUT", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/inputs/(?P<iid>\w+)/files")
def api_upload(h, m):
    n = node_or_404(m["nid"])
    name = urllib.parse.unquote(h.query.get("name", ["file"])[0])
    length = int(h.headers.get("Content-Length", "0"))
    try:
        entry = store.add_file(n, m["iid"], name, h.rfile, length)
    except PermissionError as e:
        raise ApiError(409, str(e)) from None
    BUS.publish("node_changed", {"node": n.id})
    return {"file": entry}


@route("DELETE", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/inputs/(?P<iid>\w+)/files")
def api_remove_file(h, m):
    n = node_or_404(m["nid"])
    store.remove_file(n, m["iid"], h.query.get("name", [""])[0])
    BUS.publish("node_changed", {"node": n.id})
    return {"ok": True}


# ---------------------------------------------------------------- AI rounds


@route("POST", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/send")
def api_send(h, m):
    n = node_or_404(m["nid"])
    kind = h.json().get("kind", "design")
    prov = ai.provider_status()[config.load_settings()["ai"]["provider"]]
    if not prov["available"]:
        raise ApiError(409, f"{prov['label']} isn't ready: {prov['status']} (Settings → AI)")
    try:
        rnd = ai.start_round(n, kind)
    except ValueError as e:
        raise ApiError(409, str(e)) from None
    job = MANAGER.submit("ai_round", n.id, f"{ai.display_name()}: round {rnd['n']} — {n.meta()['title']}",
                         lambda job: ai.run_round(job, n, rnd), lane="ai")
    BUS.publish("node_changed", {"node": n.id})
    return {"job": job.public(), "round": rnd}


@route("POST", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/rounds/(?P<rn>\d+)/retry")
def api_retry(h, m):
    n = node_or_404(m["nid"])
    d = n.inputs()
    rnd = next((r for r in d["rounds"] if r["n"] == int(m["rn"])), None)
    if not rnd or rnd["status"] not in ("failed", "cancelled", "interrupted"):
        raise ApiError(409, "only a failed or interrupted round can be retried")
    with store.LOCK:
        spec = n.output_spec()
        for c in spec["custom"]:
            if c["id"] in rnd.get("requests", []):
                c["status"], c["round"] = "in_progress", rnd["n"]
        n.save_output_spec(spec)
    job = MANAGER.submit("ai_round", n.id, f"{ai.display_name()}: retry round {rnd['n']}",
                         lambda job: ai.run_round(job, n, rnd), lane="ai")
    return {"job": job.public()}


@route("POST", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/rounds/(?P<rn>\d+)/continue")
def api_continue(h, m):
    n = node_or_404(m["nid"])
    rnd = next((r for r in n.inputs()["rounds"] if r["n"] == int(m["rn"])), None)
    if not rnd:
        raise ApiError(404, "no such round")
    if rnd["status"] in ("queued", "running"):
        raise ApiError(409, "this round is still running — wait for it to finish")
    try:
        sid = ai.continue_in_terminal(n, rnd)
    except RuntimeError as e:
        raise ApiError(409, str(e)) from None
    return {"session_id": sid}


@route("GET", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/rounds/(?P<rn>\d+)/(?P<what>prompt|log)")
def api_round_file(h, m):
    n = node_or_404(m["nid"])
    f = n.app_dir / "rounds" / m["rn"] / ("prompt.md" if m["what"] == "prompt" else "agent.log")
    return {"text": f.read_text() if f.exists() else ""}


# ---------------------------------------------------------------- design file


@route("PUT", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/design")
def api_save_design(h, m):
    import yaml
    n = node_or_404(m["nid"])
    text = h.json().get("text", "")
    try:
        yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ApiError(400, f"not valid YAML: {e}") from None
    n.design_path.write_text(text)
    v = n.snapshot_design("manual", h.json().get("note", "edited in the app"))
    BUS.publish("node_changed", {"node": n.id})
    return {"version": v, "issues": ai.validate(n)}


@route("GET", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/design/versions/(?P<k>\d+)")
def api_version(h, m):
    return {"text": node_or_404(m["nid"]).version_text(int(m["k"]))}


# ---------------------------------------------------------------- outputs


@route("PUT", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/output-spec")
def api_output_spec(h, m):
    n = node_or_404(m["nid"])
    b = h.json()
    if "selected" in b:
        bad = set(b["selected"]) - set(generate.TYPE_KEYS)
        if bad:
            raise ApiError(400, f"unknown output types {sorted(bad)}")
    spec = store.update_output_spec(n, b)
    BUS.publish("node_changed", {"node": n.id})
    return {"output_spec": spec}


@route("GET", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/outputs")
def api_outputs(h, m):
    return generate.list_outputs(node_or_404(m["nid"]))


@route("POST", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/generate")
def api_generate(h, m):
    n = node_or_404(m["nid"])
    b = h.json()
    keys = b.get("types")
    if any(i["level"] == "error" for i in ai.validate(n)):
        raise ApiError(409, "the design file has errors — fix them (or send a round) before generating outputs")
    title = f"Outputs — {n.meta()['title']}" + (f" ({', '.join(keys)})" if keys else "")
    job = MANAGER.submit("generate", n.id, title, lambda job: generate.run(job, n, keys, b.get("force", False)))
    return {"job": job.public()}


@route("GET", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/download-all")
def api_download_all(h, m):
    n = node_or_404(m["nid"])
    data = generate.list_outputs(n)
    selected = set(n.output_spec()["selected"])
    labels = {t["key"]: t["label"] for t in generate.OUTPUT_TYPES}
    proj_title = re.sub(r'[\\/:*?"<>|]+', "-", store.Node(n.project_id).meta()["title"])
    node_title = re.sub(r'[\\/:*?"<>|]+', "-", n.meta()["title"]) if n.variant else "Base design"
    tmp = Path(tempfile.mkstemp(suffix=".zip")[1])
    root = config.projects_root()
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for key, items in data["groups"].items():
            if key not in selected and key != "other":
                continue  # hidden types stay out of "download all"
            folder = labels.get(key, "Other")
            for o in items:
                src = root / urllib.parse.unquote(o["url"][len("/files/"):])
                if o["kind"] == "web":
                    base = f"{proj_title}/{node_title}/{folder}/{o['state']}"
                    for f in src.parent.iterdir():
                        z.write(f, f"{base}/{f.name}")
                    z.writestr(f"{base}/Open walkthrough.html", generate.standalone_viewer(src.parent))
                elif src.exists():
                    name = f"{o['label']}{src.suffix}".replace("·", "-").replace("/", "-")
                    z.write(src, f"{proj_title}/{node_title}/{folder}/{name}")
    h.send_file(tmp, f"{proj_title} - {node_title}.zip", "application/zip", delete=True)
    return None


@route("GET", "/api/download")
def api_download(h, m):
    rel = urllib.parse.unquote(h.query.get("path", [""])[0])
    p = _safe_path(rel)
    if p.is_dir():
        raise ApiError(400, "not a file")
    h.send_file(p, p.name, mimetypes.guess_type(p.name)[0] or "application/octet-stream", attachment=True)
    return None


# ---------------------------------------------------------------- Blender hand edits


@route("POST", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/blender/open")
def api_open_blender(h, m):
    n = node_or_404(m["nid"])
    job = MANAGER.submit("open", n.id, f"Open in Blender — {n.meta()['title']}",
                         lambda job: job.lv("open", *n.lv_args()))
    return {"job": job.public()}


@route("POST", r"/api/nodes/(?P<nid>[^/]+(?:%2F[^/]+)?)/blender/pull")
def api_pull(h, m):
    n = node_or_404(m["nid"])
    discard = h.json().get("discard", False)

    def work(job):
        job.lv("pull", str(n.project_dir), *(["--variant", n.variant] if n.variant else []),
               *(["--discard"] if discard else []))
        n.snapshot_design("pull", "hand edits pulled from Blender")
    job = MANAGER.submit("pull", n.id, f"Pull Blender edits — {n.meta()['title']}", work)
    return {"job": job.public()}


# ---------------------------------------------------------------- jobs / events


@route("GET", "/api/jobs")
def api_jobs(h, m):
    return {"jobs": MANAGER.list()}


@route("GET", r"/api/jobs/(?P<jid>\w+)")
def api_job(h, m):
    j = MANAGER.jobs.get(m["jid"])
    if not j:
        raise ApiError(404, "no such job")
    return {"job": j.public(with_log=True)}


@route("POST", r"/api/jobs/(?P<jid>\w+)/cancel")
def api_cancel(h, m):
    return {"ok": MANAGER.cancel(m["jid"])}


@route("POST", "/api/reveal")
def api_reveal(h, m):
    """Open a project folder in the system file manager."""
    p = _safe_path(h.json().get("path", ""))
    opener = {"darwin": "open", "win32": "explorer"}.get(sys.platform, "xdg-open")
    subprocess.Popen([opener, str(p if p.is_dir() else p.parent)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True}


# ---------------------------------------------------------------- plumbing


def _safe_path(rel: str) -> Path:
    root = config.projects_root().resolve()
    p = (root / rel.split("?")[0]).resolve()
    if not str(p).startswith(str(root)) or not p.exists():
        raise ApiError(404, "not found")
    return p


class Handler(BaseHTTPRequestHandler):
    server_version = "EnvGen/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        pass

    # ---- helpers
    def json(self) -> dict:
        n = int(self.headers.get("Content-Length", "0") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            raise ApiError(400, "bad JSON") from None

    def reply(self, status: int, body: dict | None):
        data = json.dumps(body if body is not None else {}, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path: Path, name: str, ctype: str, attachment=True, delete=False):
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        disp = "attachment" if attachment else "inline"
        self.send_header("Content-Disposition", f"{disp}; filename*=UTF-8''{urllib.parse.quote(name)}")
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 1 << 20)
        if delete:
            path.unlink(missing_ok=True)

    def static(self, path: Path, ctype=None, cache=True):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        ctype = ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        size = path.stat().st_size
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng and rng.startswith("bytes="):  # video seeking
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else max(0, size - int(b))
            end = int(b) if (a and b) else size - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-cache" if not cache else "max-age=60")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    def authed(self) -> bool:
        cookie = self.headers.get("Cookie", "")
        return f"envgen={TOKEN}" in cookie

    # ---- dispatch
    def handle_any(self, method: str):
        parsed = urllib.parse.urlsplit(self.path)
        self.query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path
        try:
            if method == "GET" and path in ("/", "/index.html", "/envgen"):
                html = (config.WEB_DIR / "index.html").read_text().replace("{{APP_NAME}}", config.brand()["name"])
                data = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Set-Cookie", f"envgen={TOKEN}; Path=/; HttpOnly; SameSite=Strict")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if method == "GET" and path.startswith("/web/"):
                return self.static((config.WEB_DIR / path[5:]).resolve(), cache=False)
            if method == "GET" and path.startswith("/branding/"):
                return self.static((config.APP_DIR / "branding" / Path(path).name).resolve())
            if not self.authed():
                return self.reply(403, {"error": "missing session — reload the app window"})
            if method == "GET" and path.startswith("/files/"):
                return self.static(_safe_path(urllib.parse.unquote(path[len("/files/"):])))
            if method == "GET" and path == "/api/events":
                return self.events()
            for m_, rx, fn in ROUTES:
                if m_ != method:
                    continue
                mt = rx.match(path)
                if mt:
                    body = fn(self, mt.groupdict())
                    if body is not None:
                        self.reply(200, body)
                    return
            self.reply(404, {"error": f"no route {method} {path}"})
        except ApiError as e:
            self.reply(e.status, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            try:
                self.reply(500, {"error": f"{type(e).__name__}: {e}"})
            except Exception:  # noqa: BLE001
                pass

    def events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = BUS.subscribe()
        try:
            self.wfile.write(b": hello\n\n")
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    payload = f"event: {msg['event']}\ndata: {json.dumps(msg['data'], default=str)}\n\n"
                except Exception:  # noqa: BLE001 — queue.Empty → keepalive
                    payload = ": ping\n\n"
                self.wfile.write(payload.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            BUS.unsubscribe(q)

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.handle_any("PUT")

    def do_PATCH(self):
        self.handle_any("PATCH")

    def do_DELETE(self):
        self.handle_any("DELETE")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(port: int = 0) -> tuple[Server, str]:
    srv = Server(("127.0.0.1", port), Handler)
    try:
        for note in ai.recover_rounds(MANAGER):
            print("recovered:", note, flush=True)
    except Exception as e:  # noqa: BLE001 — never block startup on recovery
        print("round recovery failed:", e, flush=True)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    threading.Thread(target=srv.serve_forever, daemon=True, name="http").start()
    return srv, url

