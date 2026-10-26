// Theme: presets (light + dark palettes), per-token overrides, fonts, scale, density, radius.
// Everything lands as CSS variables on :root, so the whole UI follows.

export const TOKENS = [
  ["bg", "Window background"], ["surface", "Panels"], ["surface-2", "Raised / subtle"], ["surface-3", "Hover / wells"],
  ["ink", "Text"], ["muted", "Secondary text"], ["line", "Borders"], ["accent", "Accent"],
  ["accent-ink", "Text on accent"], ["accent-soft", "Accent tint"], ["ok", "Success"], ["warn", "Warning"],
  ["danger", "Danger"], ["focus", "Focus ring"],
];

const P = (bg, surface, s2, s3, ink, muted, line, accent, accentInk, soft, ok, warn, danger) =>
  ({ bg, surface, "surface-2": s2, "surface-3": s3, ink, muted, line, accent, "accent-ink": accentInk,
     "accent-soft": soft, ok, warn, danger, focus: accent });

export const PRESETS = {
  moss: { label: "Moss",
    light: P("#f5f4ef", "#ffffff", "#f0efe8", "#e7e5dc", "#1d2420", "#5e6862", "#dcd9cf", "#3f7a4a", "#ffffff", "#e2eee2", "#2f7d4c", "#a8650a", "#b3362f"),
    dark: P("#131715", "#1a1f1c", "#212723", "#2a312c", "#e6ebe7", "#9aa59e", "#2e3631", "#7cc08a", "#0f1a12", "#213326", "#73c08b", "#e0a34a", "#ef7a70") },
  slate: { label: "Slate",
    light: P("#f3f5f8", "#ffffff", "#edf0f4", "#e2e7ee", "#1b2230", "#5a6474", "#d8dee7", "#3b6ea8", "#ffffff", "#e1ebf7", "#2f7d5a", "#a8650a", "#b3362f"),
    dark: P("#11151b", "#181d25", "#1f2530", "#28303c", "#e4e9f1", "#97a2b3", "#2b3340", "#7fb0ea", "#0d1622", "#1d2b3d", "#6fc29a", "#e0a34a", "#ef7a70") },
  sand: { label: "Sand",
    light: P("#f7f3ec", "#fffdf9", "#f2ece2", "#e9e1d4", "#2a2119", "#6d6152", "#e2d8c8", "#a8642c", "#ffffff", "#f5e6d6", "#4f7d3a", "#a8650a", "#b3362f"),
    dark: P("#17130f", "#1f1a15", "#27211b", "#312a22", "#efe7dc", "#ab9e8d", "#3a3128", "#e6a066", "#1f140a", "#3a2a1c", "#93c27a", "#e0a34a", "#ef7a70") },
  plum: { label: "Plum",
    light: P("#f6f3f7", "#ffffff", "#f0ebf2", "#e6dfea", "#251d29", "#665a6c", "#ddd3e2", "#7a4a8a", "#ffffff", "#eee2f3", "#3f7d55", "#a8650a", "#b3362f"),
    dark: P("#151118", "#1c1720", "#241e29", "#2e2634", "#ece4f0", "#a598ad", "#352c3b", "#c49ad6", "#1a1020", "#33243b", "#7cc29a", "#e0a34a", "#ef7a70") },
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

export function palette(a, mode) {
  const preset = PRESETS[a.preset] || PRESETS.moss;
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
