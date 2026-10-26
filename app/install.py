"""Add the app to the system's app menu (or remove it).   ./launch --install | --uninstall

  Linux    ~/.local/share/applications/3d-environment-generator.desktop
           (Omarchy's Super+Space launcher, GNOME, KDE… all read this)
  macOS    ~/Applications/<App Name>.app   (Launchpad / Spotlight)
  Windows  Start Menu shortcut  (%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs)

The menu icon is app/branding/app-icon.png: your logo.png if present, otherwise the default
icon. It's refreshed on every launch, so dropping in a logo later updates the menu icon too.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from . import config

BRANDING = config.APP_DIR / "branding"
ICON = BRANDING / "app-icon.png"
WINDOW_CLASS = "chrome-127.0.0.1__envgen-Default"  # Chrome app-mode window on Wayland (see __main__.open_window)


def refresh_icon() -> Path:
    """app-icon.png ← logo.png (if the user added one) else default-icon.png."""
    logo = config.APP_DIR / config.brand().get("logo", "branding/logo.png")
    src = logo if logo.exists() else BRANDING / "default-icon.png"
    try:
        if not ICON.exists() or src.stat().st_mtime > ICON.stat().st_mtime or ICON.read_bytes() != src.read_bytes():
            shutil.copy2(src, ICON)
    except OSError:
        pass
    return ICON


def _png_to_ico(png: Path, ico: Path):
    """An .ico holding one PNG image (valid since Windows Vista); no imaging library needed."""
    data = png.read_bytes()
    w = int.from_bytes(data[16:20], "big")
    h = int.from_bytes(data[20:24], "big")
    entry = struct.pack("<BBBBHHII", w if w < 256 else 0, h if h < 256 else 0, 0, 0, 1, 32, len(data), 6 + 16)
    ico.write_bytes(struct.pack("<HHH", 0, 1, 1) + entry + data)


def install() -> str:
    b = config.brand()
    name = b["name"]
    refresh_icon()
    repo = config.REPO_ROOT
    if sys.platform.startswith("linux"):
        apps = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        f = apps / f"{config.SLUG}.desktop"
        f.write_text("\n".join([
            "[Desktop Entry]", "Version=1.0", "Type=Application",
            f"Name={name}", "GenericName=Landscape 3D Generator",
            f"Comment={b.get('tagline', '')}",
            f'Exec="{repo / "launch"}"', f"Path={repo}", f"Icon={ICON}",
            "Terminal=false", "StartupNotify=true", f"StartupWMClass={WINDOW_CLASS}",
            "Categories=Graphics;3DGraphics;", "Keywords=landscape;3D;render;garden;yard;plan;", ""]))
        f.chmod(0o755)
        if shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database", str(apps)], capture_output=True)
        return str(f)
    if sys.platform == "darwin":
        app = Path.home() / "Applications" / f"{name}.app"
        macos = app / "Contents" / "MacOS"
        res = app / "Contents" / "Resources"
        macos.mkdir(parents=True, exist_ok=True)
        res.mkdir(parents=True, exist_ok=True)
        exe = macos / "launch"
        exe.write_text(f'#!/bin/bash\nexec "{repo / "launch"}" "$@"\n')
        exe.chmod(0o755)
        plist = {"CFBundleName": name, "CFBundleDisplayName": name, "CFBundleExecutable": "launch",
                 "CFBundleIdentifier": f"org.local.{config.SLUG}", "CFBundlePackageType": "APPL",
                 "CFBundleShortVersionString": b.get("version", "0.1.0")}
        if shutil.which("sips") and shutil.which("iconutil"):  # build an .icns from the PNG
            iconset = res / "icon.iconset"
            iconset.mkdir(exist_ok=True)
            for size in (16, 32, 128, 256, 512):
                subprocess.run(["sips", "-z", str(size), str(size), str(ICON), "--out",
                                str(iconset / f"icon_{size}x{size}.png")], capture_output=True)
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(res / "icon.icns")], capture_output=True)
            shutil.rmtree(iconset, ignore_errors=True)
            plist["CFBundleIconFile"] = "icon.icns"
        with open(app / "Contents" / "Info.plist", "wb") as fh:
            plistlib.dump(plist, fh)
        return str(app)
    if sys.platform.startswith("win"):
        ico = BRANDING / "app-icon.ico"
        _png_to_ico(ICON, ico)
        programs = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        lnk = programs / f"{name}.lnk"
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        target = pythonw if pythonw.exists() else Path(sys.executable)
        ps = (f'$s=(New-Object -ComObject WScript.Shell).CreateShortcut("{lnk}");'
              f'$s.TargetPath="{target}";$s.Arguments="-m app";$s.WorkingDirectory="{repo}";'
              f'$s.IconLocation="{ico}";$s.Description="{b.get("tagline", "")}";$s.Save()')
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
        return str(lnk)
    raise RuntimeError(f"don't know how to add menu entries on {sys.platform}")


def uninstall() -> str:
    name = config.brand()["name"]
    if sys.platform.startswith("linux"):
        f = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "applications" / f"{config.SLUG}.desktop"
    elif sys.platform == "darwin":
        f = Path.home() / "Applications" / f"{name}.app"
    else:
        f = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{name}.lnk"
    if f.is_dir():
        shutil.rmtree(f)
    elif f.exists():
        f.unlink()
    return str(f)
