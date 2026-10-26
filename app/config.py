"""Branding, per-user settings, and paths.

Settings are per user, shared by every project made in the app, and stored outside the repo
so a `git pull` never touches them:
  Linux   $XDG_CONFIG_HOME/3d-environment-generator/settings.json  (~/.config/…)
  macOS   ~/Library/Application Support/3d-environment-generator/settings.json
  Windows %APPDATA%\\3d-environment-generator\\settings.json
"""
from __future__ import annotations

import copy
import json
import os
import sys
import threading
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent
WEB_DIR = APP_DIR / "web"
BRAND_FILE = APP_DIR / "brand.json"
SLUG = "3d-environment-generator"


def brand() -> dict:
    b = json.loads(BRAND_FILE.read_text())
    logo = APP_DIR / b.get("logo", "branding/logo.png")
    b["has_logo"] = logo.exists()
    return b


def config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / SLUG
    d.mkdir(parents=True, exist_ok=True)
    return d


DEFAULTS = {
    "appearance": {
        "mode": "system",            # system | light | dark
        "preset": "forest",          # starting palette; per-token overrides below win
        "overrides": {"light": {}, "dark": {}},
        "font_ui": "system",         # system | inter | serif | rounded | mono | any installed family name
        "font_mono": "system",
        "font_scale": 1.0,           # 0.85 – 1.3
        "density": "comfortable",    # compact | comfortable | spacious
        "radius": 10,                # px corner radius
        "nav_width": 272,
        "reduced_motion": False,
        "show_explanations": True,
    },
    "ai": {
        "provider": "claude_code",   # claude_code | codex | gemini | anthropic_api | custom
        "model": "",                 # blank = the provider's default
        "effort": "high",            # anthropic_api only: low | medium | high | xhigh | max
        "api_key": "",               # anthropic_api only; stored in this file (0600). Env var wins if set.
        "custom_command": "",        # custom provider: e.g. my-agent --prompt-file {prompt_file}
    },
    "projects": {
        "root": str(REPO_ROOT / "projects"),
        "units": "ft",
        "location": {"lat": 40.0, "lon": -83.0, "utc_offset": -5},
        "default_outputs": ["stills", "plan_check", "web_walkthrough"],
        "default_detail": "preview",
    },
    "rendering": {
        "device": "auto",            # auto | gpu | cpu
        "blender_path": "",
    },
    "window": {"last_node": None, "last_tab": "input", "split": False, "advanced_outputs": False},
}

_lock = threading.Lock()


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def settings_path() -> Path:
    return config_dir() / "settings.json"


def load_settings() -> dict:
    p = settings_path()
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError:
            p.rename(p.with_suffix(".corrupt.json"))
    return _merge(DEFAULTS, data)


def save_settings(patch: dict) -> dict:
    with _lock:
        s = _merge(load_settings(), patch)
        ov = (patch.get("appearance") or {}).get("overrides")
        if ov is not None:  # replace, so a color reset (key removed) sticks
            s["appearance"]["overrides"] = ov
        p = settings_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=1))
        try:
            os.chmod(tmp, 0o600)  # may hold an API key
        except OSError:
            pass
        tmp.replace(p)
        return s


def public_settings(s: dict) -> dict:
    """Settings as sent to the UI: secrets masked."""
    out = copy.deepcopy(s)
    key = out["ai"].get("api_key") or ""
    out["ai"]["api_key"] = ("•••• " + key[-4:]) if key else ""
    out["ai"]["api_key_set"] = bool(key) or bool(os.environ.get("ANTHROPIC_API_KEY"))
    return out


def projects_root() -> Path:
    root = Path(load_settings()["projects"]["root"]).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root
