"""Generate the app's theme presets (app/web/js/palettes.js) from Coolors trending palettes.

Each preset names, per mode, which palette colors play the anchor roles (background tint, text,
accent); every other token is derived from those anchors. Then every pairing the UI actually uses
is checked against WCAG 2.x contrast, and the accent/status colors are nudged in lightness (hue and
chroma kept) only as far as needed to pass:

  text, muted text           on bg / surface / surface-2   >= 4.5
  accent (links, icons)       on surface                    >= 4.5 (light) / 4.5 (dark)
  accent-ink                  on accent (primary buttons)   >= 4.5
  ok / warn / danger (chips)  on surface                    >= 4.5
  line                        on surface                    >= 1.3 (visible, not loud)

Run:  python3 tools/make_palettes.py   (prints a report; writes the JS file)
"""
from __future__ import annotations

import colorsys
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "web" / "js" / "palettes.js"


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def hexs(c):
    return "#" + "".join(f"{round(max(0, min(1, v)) * 255):02x}" for v in c)


def _lin(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def lum(c):
    r, g, b = (_lin(v) for v in rgb(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _oklab(c):
    r, g, b = (_lin(v) for v in rgb(c))
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _from_oklab(L, A, B):
    l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3
    m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3
    s = (L - 0.0894841775 * A - 1.2914855480 * B) ** 3
    lin = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
           -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
    return hexs([12.92 * v if v <= 0.0031308 else 1.055 * max(v, 0) ** (1 / 2.4) - 0.055 for v in lin])


def mix(a, b, t):
    """t of b into a, in OKLab (perceptually even steps for both light and dark tints)."""
    pa, pb = _oklab(a), _oklab(b)
    return _from_oklab(*(x + (y - x) * t for x, y in zip(pa, pb)))


def shift(c, dl):
    """Change HLS lightness by dl, keeping hue and saturation."""
    h, l, s = colorsys.rgb_to_hls(*rgb(c))
    return hexs(colorsys.hls_to_rgb(h, max(0, min(1, l + dl)), s))


def fit(c, against, target, direction):
    """Move c darker (-1) or lighter (+1) until contrast with every `against` is >= target."""
    against = against if isinstance(against, (list, tuple)) else [against]
    for i in range(200):  # always from the original color, so hue doesn't drift with rounding
        x = shift(c, 0.004 * i * direction)
        if min(contrast(x, a) for a in against) >= target:
            return x
    return x


def build(mode, bg, ink, accent, accent_ink=None, ok=None, warn=None, danger=None, surface=None):
    dark = mode == "dark"
    if dark:
        surface = surface or mix(bg, ink, 0.055)
        s2 = mix(surface, ink, 0.045)
        s3 = mix(surface, ink, 0.09)
        line = mix(surface, ink, 0.16)
        muted = mix(ink, bg, 0.36)
    else:
        surface = surface or mix(bg, "#ffffff", 0.75)
        s2 = mix(bg, ink, 0.035)
        s3 = mix(bg, ink, 0.075)
        line = mix(bg, ink, 0.17)
        muted = mix(ink, bg, 0.40)
    direction = 1 if dark else -1
    texts = [bg, surface, s2]
    ink = fit(ink, texts + [s3], 7.0, direction)
    line = fit(line, surface, 1.3, direction)
    muted = fit(muted, texts, 4.6, direction)
    accent = fit(accent, [surface, bg], 4.5, direction)
    if accent_ink is None:
        # Prefer the palette's own tints over pure white/black when they're readable.
        accent_ink = next((c for c in (bg, ink) if contrast(c, accent) >= 4.5),
                          max(["#ffffff", "#000000"], key=lambda c: contrast(c, accent)))
    accent_ink = fit(accent_ink, accent, 4.5, 1 if lum(accent_ink) > lum(accent) else -1)
    soft = mix(surface, accent, 0.20 if dark else 0.13)
    defaults = ({"ok": "#7cc48f", "warn": "#e8a94f", "danger": "#f08378"} if dark else
                {"ok": "#2c7a4b", "warn": "#9a5b07", "danger": "#b0352e"})
    st = {k: fit(v or defaults[k], [surface, s2], 4.5, direction) for k, v in
          (("ok", ok), ("warn", warn), ("danger", danger))}
    return {"bg": bg, "surface": surface, "surface-2": s2, "surface-3": s3, "ink": ink, "muted": muted,
            "line": line, "accent": accent, "accent-ink": accent_ink, "accent-soft": soft, **st, "focus": accent}


# name, Coolors title, source colors, light anchors, dark anchors
PRESETS = [
    ("forest", "Earthy Forest Hues", ["DAD7CD", "A3B18A", "588157", "3A5A40", "344E41"],
     dict(bg=None, ink="#344e41", accent="#588157"),
     dict(bg=None, ink="#dad7cd", accent="#a3b18a")),
    ("olive", "Olive Garden Feast", ["606C38", "283618", "FEFAE0", "DDA15E", "BC6C25"],
     dict(bg="#fefae0", ink="#283618", accent="#606c38", warn="#bc6c25"),
     dict(bg=None, ink="#fefae0", accent="#dda15e", warn="#dda15e")),
    ("meadow", "Leafy Green Garden", ["132A13", "31572C", "4F772D", "90A955", "ECF39E"],
     dict(bg=None, ink="#132a13", accent="#4f772d", ok="#31572c"),
     dict(bg=None, ink="#ecf39e", accent="#90a955", ok="#90a955")),
    ("fields", "Golden Summer Fields", ["CCD5AE", "E9EDC9", "FEFAE0", "FAEDCD", "D4A373"],
     dict(bg="#fefae0", ink="#3b2f1e", accent="#d4a373"),
     dict(bg="#1b1712", ink="#faedcd", accent="#d4a373")),
    ("beach", "Sunny Beach Day", ["264653", "2A9D8F", "E9C46A", "F4A261", "E76F51"],
     dict(bg="#f5f7f6", ink="#264653", accent="#2a9d8f", warn="#f4a261", danger="#e76f51"),
     dict(bg=None, ink="#eef3f1", accent="#e9c46a", ok="#2a9d8f", warn="#f4a261", danger="#e76f51")),
    ("breeze", "Summer Ocean Breeze", ["E63946", "F1FAEE", "A8DADC", "457B9D", "1D3557"],
     dict(bg="#f1faee", ink="#1d3557", accent="#457b9d", danger="#e63946"),
     dict(bg=None, ink="#f1faee", accent="#a8dadc", danger="#e63946")),
    ("deepsea", "Deep Sea", ["0D1321", "1D2D44", "3E5C76", "748CAB", "F0EBD8"],
     dict(bg="#f0ebd8", ink="#0d1321", accent="#3e5c76"),
     dict(bg="#0d1321", ink="#f0ebd8", accent="#748cab", surface="#16213a")),
    ("twilight", "Golden Twilight", ["000814", "001D3D", "003566", "FFC300", "FFD60A"],
     dict(bg="#f4f6f9", ink="#000814", accent="#003566", warn="#ffc300"),
     dict(bg="#000814", ink="#eef2f7", accent="#ffc300", accent_ink="#000814", surface="#001b38")),
    ("lavender", "Soft Lavender", ["22223B", "4A4E69", "9A8C98", "C9ADA7", "F2E9E4"],
     dict(bg="#f2e9e4", ink="#22223b", accent="#4a4e69"),
     dict(bg=None, ink="#f2e9e4", accent="#c9ada7")),
    ("fiery", "Fiery Ocean", ["780000", "C1121F", "FDF0D5", "003049", "669BBC"],
     dict(bg="#fdf0d5", ink="#003049", accent="#780000", danger="#c1121f"),
     dict(bg=None, ink="#fdf0d5", accent="#669bbc", danger="#c1121f")),
]


def default_bg(mode, colors):
    """Light: the palette's lightest color pushed toward white; dark: its darkest pushed toward black."""
    cs = ["#" + c.lower() for c in colors]
    if mode == "light":
        c = max(cs, key=lum)
        return mix(c, "#ffffff", 0.6)
    c = min(cs, key=lum)
    return mix(c, "#000000", 0.55) if lum(c) > 0.012 else c


def main():
    presets = {}
    report = []
    for key, title, colors, light, dark in PRESETS:
        entry = {"label": title, "source": ["#" + c.lower() for c in colors]}
        for mode, a in (("light", light), ("dark", dark)):
            a = dict(a)
            a["bg"] = a.get("bg") or default_bg(mode, colors)
            p = build(mode, **a)
            entry[mode] = p
            checks = {
                "ink/bg": contrast(p["ink"], p["bg"]), "muted/s2": contrast(p["muted"], p["surface-2"]),
                "accent/surface": contrast(p["accent"], p["surface"]),
                "accent-ink/accent": contrast(p["accent-ink"], p["accent"]),
                "ok": contrast(p["ok"], p["surface"]), "warn": contrast(p["warn"], p["surface"]),
                "danger": contrast(p["danger"], p["surface"]), "line": contrast(p["line"], p["surface"]),
            }
            bad = [k for k, v in checks.items() if v < (1.3 if k == "line" else 4.5)]
            report.append(f"{key:9} {mode:5} " + " ".join(f"{k}={v:.1f}" for k, v in checks.items())
                          + ("   FAIL " + ",".join(bad) if bad else ""))
            if a.get("accent") and p["accent"] != a["accent"]:
                report.append(f"{'':16}accent {a['accent']} -> {p['accent']} (lightness adjusted for contrast)")
        presets[key] = entry
    js = ("// Generated by tools/make_palettes.py from Coolors trending palettes (coolors.co/palettes/trending).\n"
          "// Edit the anchors there and re-run it; every token pair is checked for WCAG contrast.\n"
          f"export const GENERATED = {json.dumps(presets, indent=1)};\n")
    OUT.write_text(js)
    print("\n".join(report))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
