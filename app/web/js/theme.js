// Theme: presets (light + dark palettes), per-token overrides, fonts, scale, density, radius.
// Everything lands as CSS variables on :root, so the whole UI follows.

import { GENERATED } from "./palettes.js";

export const DEFAULT_PRESET = "forest";
const RENAMED = { moss: "forest", slate: "deepsea", sand: "fields", plum: "lavender" };

export const TOKENS = [
  ["bg", "Window background"], ["surface", "Panels"], ["surface-2", "Raised / subtle"], ["surface-3", "Hover / wells"],
  ["ink", "Text"], ["muted", "Secondary text"], ["line", "Borders"], ["accent", "Accent"],
  ["accent-ink", "Text on accent"], ["accent-soft", "Accent tint"], ["ok", "Success"], ["warn", "Warning"],
  ["danger", "Danger"], ["focus", "Focus ring"],
];

const P = (bg, surface, s2, s3, ink, muted, line, accent, accentInk, soft, ok, warn, danger) =>
  ({ bg, surface, "surface-2": s2, "surface-3": s3, ink, muted, line, accent, "accent-ink": accentInk,
     "accent-soft": soft, ok, warn, danger, focus: accent });

// Palettes from Coolors' trending list (generated, contrast-checked: tools/make_palettes.py), plus a
// high-contrast accessibility preset. Old preset names from earlier versions map to their closest.
export const PRESETS = {
  ...GENERATED,
  contrast: { label: "High contrast",
    light: P("#ffffff", "#ffffff", "#f2f2f2", "#e0e0e0", "#000000", "#2b2b2b", "#000000", "#0047b3", "#ffffff", "#dbe8ff", "#006b2e", "#8a4b00", "#b00000"),
    dark: P("#000000", "#000000", "#111111", "#222222", "#ffffff", "#e0e0e0", "#ffffff", "#ffd400", "#000000", "#332b00", "#5dff9a", "#ffb84d", "#ff6b6b") },
};

export const FONTS = {
  system: 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  inter: 'Inter, "Inter Variable", system-ui, sans-serif',
  humanist: '"Source Sans 3", "Source Sans Pro", "Segoe UI", Ubuntu, Cantarell, sans-serif',
  rounded: '"Nunito", "Varela Round", ui-rounded, system-ui, sans-serif',
  serif: '"Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif',
  mono: 'ui-monospace, "JetBrains Mono", "Cascadia Code", Menlo, Consolas, monospace',
};
const DENSITY = { compact: 8, comfortable: 12, spacious: 16 };

let current = null;
const media = matchMedia("(prefers-color-scheme: dark)");
media.addEventListener("change", () => current && apply(current));

export function resolvedMode(a) {
  return a.mode === "system" ? (media.matches ? "dark" : "light") : a.mode;
}

export function presetKey(a) {
  return PRESETS[a.preset] ? a.preset : (RENAMED[a.preset] && PRESETS[RENAMED[a.preset]] ? RENAMED[a.preset] : DEFAULT_PRESET);
}

export function palette(a, mode) {
  const preset = PRESETS[a.preset] || PRESETS[RENAMED[a.preset]] || PRESETS[DEFAULT_PRESET];
  return { ...preset[mode], ...((a.overrides || {})[mode] || {}) };
}

export function apply(a) {
  current = a;
  const root = document.documentElement;
  const mode = resolvedMode(a);
  root.dataset.mode = mode;
  for (const [k, v] of Object.entries(palette(a, mode))) root.style.setProperty(`--${k}`, v);
  root.style.setProperty("--font-ui", FONTS[a.font_ui] || `"${a.font_ui}", ${FONTS.system}`);
  root.style.setProperty("--font-mono", a.font_mono === "system" ? FONTS.mono : `"${a.font_mono}", ${FONTS.mono}`);
  root.style.setProperty("--scale", a.font_scale || 1);
  root.style.setProperty("--pad", (DENSITY[a.density] || 12) + "px");
  root.style.setProperty("--radius", (a.radius ?? 10) + "px");
  root.style.setProperty("--nav-w", (a.nav_width || 272) + "px");
  document.body.classList.toggle("reduced-motion", !!a.reduced_motion);
  document.body.classList.toggle("hide-explain", a.show_explanations === false);
}
