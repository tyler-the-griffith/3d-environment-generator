"""Launch the app:  ./launch  (launch.bat on Windows, or python -m app)

Starts the local server, then opens the UI in the best window available:
  1. a native window via pywebview, if installed (pip install pywebview)
  2. a Chromium-family browser in app mode (its own window, no tabs or address bar)
  3. the default web browser
The app quits when its last window closes (1 and 2), or on Ctrl+C.

Launching again while it runs just opens another window on the running app.

  ./launch --install     add it to the system app menu (Omarchy Super+Space, Start Menu, …)
  ./launch --uninstall   remove it from the menu
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser

from . import config

CHROMIUMS = ["google-chrome-stable", "google-chrome", "chromium", "chromium-browser", "brave-browser", "brave",
             "microsoft-edge", "microsoft-edge-stable", "vivaldi-stable"]
WIN_CHROMIUMS = [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
                 r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                 r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
                 r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"]
MAC_CHROMIUMS = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                 "/Applications/Chromium.app/Contents/MacOS/Chromium"]
INSTANCE = config.config_dir() / "instance.json"


def find_chromium() -> str | None:
    for name in CHROMIUMS:
        p = shutil.which(name)
        if p:
            return p
    for p in (WIN_CHROMIUMS if sys.platform.startswith("win") else MAC_CHROMIUMS if sys.platform == "darwin" else []):
        p = os.path.expandvars(p)
        if os.path.exists(p):
            return p
    return None


def running_url() -> str | None:
    """URL of an already-running instance, if any."""
    try:
        info = json.loads(INSTANCE.read_text())
        url = info["url"]
        with urllib.request.urlopen(url, timeout=1.5) as r:
            if r.status == 200:
                return url
    except Exception:  # noqa: BLE001 — no instance, stale file, or not reachable
        pass
    return None


def open_window(url: str, wait: bool) -> bool:
    """Open an app-mode window. Returns False if no Chromium-family browser is available.
    All windows share one browser profile, so `wait` returns when the last one closes."""
    exe = find_chromium()
    if not exe:
        return False
    profile = config.config_dir() / "window-profile"
    # The /envgen path gives the window a stable, unique Wayland app id
    # (chrome-127.0.0.1__envgen-Default) so menus and window rules can match it.
    proc = subprocess.Popen([exe, f"--app={url}envgen", f"--user-data-dir={profile}", "--no-first-run",
                             "--no-default-browser-check", f"--class={config.SLUG}", "--window-size=1480,940"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if wait:
        proc.wait()
    return True


def main():
    name = config.brand()["name"]
    ap = argparse.ArgumentParser(prog="launch", description=name)
    ap.add_argument("--port", type=int, default=0, help="fixed port (default: any free port)")
    ap.add_argument("--browser", action="store_true", help="open in the default browser instead of an app window")
    ap.add_argument("--no-window", action="store_true", help="just run the server and print its address")
    ap.add_argument("--install", action="store_true", help="add the app to the system app menu")
    ap.add_argument("--uninstall", action="store_true", help="remove the app from the system app menu")
    a = ap.parse_args()

    from . import install as inst
    if a.install:
        print(f"added {name} to the app menu: {inst.install()}")
        return
    if a.uninstall:
        print(f"removed: {inst.uninstall()}")
        return
    inst.refresh_icon()

    if not sys.stdout or not sys.stdout.isatty():  # started from a menu: keep a log instead
        log = open(config.config_dir() / "app.log", "a", buffering=1)
        sys.stdout = sys.stderr = log
        print(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} start")

    if not a.no_window and not a.port:
        url = running_url()
        if url:  # already running: just open another window on it
            if a.browser or not open_window(url, wait=False):
                webbrowser.open(url)
            return

    from .server import serve
    srv, url = serve(a.port)
    INSTANCE.write_text(json.dumps({"url": url, "pid": os.getpid()}))
    print(f"{name} running at {url}  (Ctrl+C to quit)", flush=True)
    try:
        if a.no_window:
            return _wait()
        if not a.browser:
            try:
                import webview  # type: ignore
                webview.create_window(name, url, width=1480, height=940, min_size=(960, 640))
                webview.start()
                return
            except ImportError:
                pass
            if open_window(url, wait=True):
                return
        webbrowser.open(url)
        _wait()
    finally:
        srv.shutdown()
        try:
            if json.loads(INSTANCE.read_text()).get("pid") == os.getpid():
                INSTANCE.unlink()
        except (OSError, ValueError):
            pass


def _wait():
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
