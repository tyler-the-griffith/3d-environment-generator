// Settings: per-user preferences shared by every project (Appearance, AI, Projects, Rendering, About).
import { FONTS, PRESETS, TOKENS, apply as applyTheme, palette, resolvedMode } from "./theme.js";
import { explain, h, icon, modal, toast } from "./ui.js";

export function openSettings(S, A) {
  let tab = "appearance";
  const body = h("div", { class: "settings" });
  // Working copy of appearance for live preview; saved on every change (debounced).
  const ap = JSON.parse(JSON.stringify(S.settings.appearance));
  let saveTimer;
  const saveAppearance = () => {
    applyTheme(ap);
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => A.saveSettings({ appearance: ap }), 300);
  };

  const navBtn = (k, label) => h("button", { class: tab === k ? "active" : "", onclick: () => { tab = k; draw(); } }, label);

  const panels = {
    appearance() {
      const mode = resolvedMode(ap);
      const pal = palette(ap, mode);
      const editMode = mode;  // edit the palette for the mode currently shown
      ap.overrides = ap.overrides || { light: {}, dark: {} };
      const ov = ap.overrides[editMode] = ap.overrides[editMode] || {};
      return [
        h("h3", {}, "Mode"),
        h("div", { class: "row" }, ["system", "light", "dark"].map((m) =>
          h("button", { class: `btn ${ap.mode === m ? "on" : ""}`, onclick: () => { ap.mode = m; saveAppearance(); draw(); } },
            { system: "Match system", light: "Light", dark: "Dark" }[m]))),
        h("h3", {}, "Palette"),
        h("div", { class: "swatches" }, Object.entries(PRESETS).map(([k, p]) =>
          h("button", { class: `swatch ${ap.preset === k ? "on" : ""}`, onclick: () => { ap.preset = k; saveAppearance(); draw(); } },
            h("div", { class: "bars" }, ["bg", "surface-2", "accent", "ink"].map((t) => h("i", { style: { background: p[mode][t] } }))),
            p.label))),
        h("h3", {}, `Colors (${editMode} mode)`),
        explain("Fine-tune any color. Changes apply on top of the palette above, separately for light and dark mode (switch mode above to edit the other). ↺ resets one color."),
        h("div", { class: "token-grid" }, TOKENS.map(([k, label]) => h("label", { class: "token" },
          h("input", { type: "color", value: toHex(pal[k]), oninput: (e) => { ov[k] = e.target.value; saveAppearance(); } }),
          h("span", {}, label),
          ov[k] ? h("span", { class: "reset", title: "Reset to palette", onclick: (e) => { e.preventDefault(); delete ov[k]; saveAppearance(); draw(); } }, "↺") : null))),
        h("h3", {}, "Text"),
        h("div", { class: "grid2" },
          h("label", { class: "field" }, h("span", {}, "Interface font"),
            fontSelect(ap.font_ui, Object.keys(FONTS).filter((f) => f !== "mono"), (v) => { ap.font_ui = v; saveAppearance(); })),
          h("label", { class: "field" }, h("span", {}, "Code font (design file, logs)"),
            fontSelect(ap.font_mono, ["system"], (v) => { ap.font_mono = v; saveAppearance(); })),
          h("label", { class: "field" }, h("span", {}, `Text size: ${Math.round(ap.font_scale * 100)}%`),
            h("input", { type: "range", min: 0.85, max: 1.3, step: 0.05, value: ap.font_scale,
              oninput: (e) => { ap.font_scale = +e.target.value; e.target.previousSibling.textContent = `Text size: ${Math.round(ap.font_scale * 100)}%`; saveAppearance(); } }))),
        h("h3", {}, "Layout"),
        h("div", { class: "grid2" },
          h("label", { class: "field" }, h("span", {}, "Density"),
            h("select", { onchange: (e) => { ap.density = e.target.value; saveAppearance(); } },
              ["compact", "comfortable", "spacious"].map((d) => h("option", { value: d, selected: ap.density === d }, d[0].toUpperCase() + d.slice(1))))),
          h("label", { class: "field" }, h("span", {}, `Corner radius: ${ap.radius}px`),
            h("input", { type: "range", min: 0, max: 18, step: 1, value: ap.radius,
              oninput: (e) => { ap.radius = +e.target.value; e.target.previousSibling.textContent = `Corner radius: ${ap.radius}px`; saveAppearance(); } })),
          h("label", { class: "field" }, h("span", {}, `Navigation width: ${ap.nav_width}px`),
            h("input", { type: "range", min: 200, max: 520, step: 4, value: ap.nav_width,
              oninput: (e) => { ap.nav_width = +e.target.value; e.target.previousSibling.textContent = `Navigation width: ${ap.nav_width}px`; saveAppearance(); } }))),
        h("div", { class: "row", style: { marginTop: "12px" } },
          check("Show explanations", ap.show_explanations !== false, (v) => { ap.show_explanations = v; saveAppearance(); }),
          check("Reduce motion", !!ap.reduced_motion, (v) => { ap.reduced_motion = v; saveAppearance(); })),
        h("h3", {}, "Share or back up your theme"),
        h("div", { class: "row" },
          h("button", { class: "btn", onclick: () => {
            const blob = new Blob([JSON.stringify(ap, null, 2)], { type: "application/json" });
            const a = h("a", { href: URL.createObjectURL(blob), download: "theme.json" }); a.click();
          } }, icon("download"), "Export theme"),
          h("label", { class: "btn" }, icon("upload"), "Import theme",
            h("input", { type: "file", accept: ".json", hidden: true, onchange: async (e) => {
              try { Object.assign(ap, JSON.parse(await e.target.files[0].text())); saveAppearance(); draw(); toast("Theme imported"); }
              catch { toast("That file isn't a theme", "error"); }
            } })),
          h("button", { class: "btn danger", onclick: () => {
            Object.assign(ap, { mode: "system", preset: "moss", overrides: { light: {}, dark: {} }, font_ui: "system", font_mono: "system",
              font_scale: 1, density: "comfortable", radius: 10, nav_width: 272, reduced_motion: false });
            saveAppearance(); draw();
          } }, icon("refresh"), "Reset appearance")),
      ];
    },

    ai() {
      const ai = { ...S.settings.ai };
      const save = (patch) => A.saveSettings({ ai: patch }).then(() => draw());
      const key = h("input", { type: "password", placeholder: S.settings.ai.api_key_set ? "A key is set; type to replace it" : "sk-ant-…", autocomplete: "off" });
      return [
        explain("Which AI writes the design file when you press Send. The CLI options use the subscription you're already signed in with. The API option bills your Anthropic account per use."),
        Object.entries(S.app.providers).map(([k, p]) => h("div", { class: `provider ${ai.provider === k ? "on" : ""}`, onclick: () => save({ provider: k }) },
          h("input", { type: "radio", name: "prov", checked: ai.provider === k }),
          h("b", {}, p.label),
          h("span", { class: `chip ${p.available ? "ok" : "warn"}` }, p.status),
          h("small", {}, p.explain))),
        h("h3", {}, "Model"),
        h("label", { class: "field" }, h("span", {}, "Model name (blank = the provider's default)"),
          h("input", { type: "text", value: ai.model || "", placeholder: ai.provider === "anthropic_api" ? "claude-opus-5-5" : "default",
            onchange: (e) => save({ model: e.target.value.trim() }) })),
        ai.provider === "anthropic_api" ? [
          h("label", { class: "field", style: { marginTop: "10px" } }, h("span", {}, "Effort"),
            h("select", { onchange: (e) => save({ effort: e.target.value }) },
              ["low", "medium", "high", "xhigh", "max"].map((v) => h("option", { value: v, selected: ai.effort === v }, v)))),
          h("label", { class: "field", style: { marginTop: "10px" } }, h("span", {}, "API key (stored only in your settings file on this computer; the ANTHROPIC_API_KEY environment variable takes precedence)"),
            h("div", { class: "row" }, key, h("button", { class: "btn", onclick: () => { if (key.value.trim()) save({ api_key: key.value.trim() }); } }, "Save key"),
              S.settings.ai.api_key_set ? h("button", { class: "btn danger", onclick: () => save({ api_key: "" }) }, "Remove") : null)),
        ] : null,
        ai.provider === "custom" ? h("label", { class: "field", style: { marginTop: "10px" } },
          h("span", {}, "Command"),
          h("input", { type: "text", class: "mono", value: ai.custom_command, placeholder: "my-agent --task {prompt_file}",
            onchange: (e) => save({ custom_command: e.target.value }) })) : null,
      ];
    },

    projects() {
      const p = S.settings.projects;
      const save = (patch) => A.saveSettings({ projects: patch }).then(() => draw());
      return [
        explain("Defaults for new projects. Each project can change its own outputs and detail level afterwards."),
        h("label", { class: "field" }, h("span", {}, "Projects folder"),
          h("input", { type: "text", class: "mono", value: p.root, onchange: (e) => save({ root: e.target.value }).then(() => A.refreshTree()).then(A.render) })),
        h("div", { class: "grid2", style: { marginTop: "10px" } },
          h("label", { class: "field" }, h("span", {}, "Units"),
            h("select", { onchange: (e) => save({ units: e.target.value }) },
              [["ft", "Feet"], ["m", "Meters"]].map(([v, l]) => h("option", { value: v, selected: p.units === v }, l)))),
          h("label", { class: "field" }, h("span", {}, "Default detail level"),
            h("select", { onchange: (e) => save({ default_detail: e.target.value }) },
              ["draft", "preview", "final"].map((v) => h("option", { value: v, selected: p.default_detail === v }, v))))),
        h("h3", {}, "Default location (sun angles until a project sets its own)"),
        h("div", { class: "grid2" },
          ["lat", "lon", "utc_offset"].map((k) => h("label", { class: "field" }, h("span", {}, { lat: "Latitude", lon: "Longitude", utc_offset: "UTC offset (hours)" }[k]),
            h("input", { type: "number", step: "any", value: p.location[k], onchange: (e) => save({ location: { ...p.location, [k]: +e.target.value } }) })))),
        h("h3", {}, "Outputs selected for new projects"),
        h("div", { class: "otypes" }, S.app.output_types.map((t) => {
          const on = p.default_outputs.includes(t.key);
          return h("button", { class: `otype ${on ? "on" : ""}`, onclick: () =>
            save({ default_outputs: on ? p.default_outputs.filter((k) => k !== t.key) : [...p.default_outputs, t.key] }) },
            h("span", { class: "box" }, on ? icon("check") : null), t.label);
        })),
      ];
    },

    rendering() {
      const r = S.settings.rendering;
      const save = (patch) => A.saveSettings({ rendering: patch }).then(() => draw());
      const gpu = S.app.gpu || {};
      return [
        explain("Rendering runs Blender in the background. On a machine whose GPU is shared with a virtual machine, renders automatically use the CPU while the VM is running."),
        h("label", { class: "field" }, h("span", {}, "Render device"),
          h("select", { onchange: (e) => save({ device: e.target.value }) },
            [["auto", "Automatic (GPU when free)"], ["gpu", "Always GPU"], ["cpu", "Always CPU"]].map(([v, l]) => h("option", { value: v, selected: r.device === v }, l)))),
        h("label", { class: "field", style: { marginTop: "10px" } }, h("span", {}, "Blender executable (blank = find it on PATH)"),
          h("input", { type: "text", class: "mono", value: r.blender_path || "", onchange: (e) => save({ blender_path: e.target.value.trim() }) })),
        h("p", {}, h("span", { class: `chip ${gpu.vm_running ? "warn" : "ok"}` }, gpu.vm_running ? `GPU in use by ${gpu.vms.join(", ")}` : "GPU available")),
      ];
    },

    about() {
      const b = S.app.brand;
      return [
        h("div", { class: "row" }, b.has_logo ? h("img", { src: "/branding/logo.png", style: { width: "64px", height: "64px", objectFit: "contain" } })
          : h("div", { class: "logo-ph", style: { width: "64px", height: "64px" } }, "LOGO"),
          h("div", {}, h("h2", { style: { margin: 0 } }, b.name), h("div", { class: "muted" }, `Version ${b.version}: ${b.tagline}`))),
        h("h3", {}, "Your logo"),
        h("p", {}, "Put a transparent-background PNG at ", h("code", {}, "app/branding/logo.png"),
          ". It appears at the top of the navigation bar and as the window icon. The app's name is set in ", h("code", {}, "app/brand.json"), "."),
        h("h3", {}, "Files"),
        h("p", { class: "small" }, "Settings: ", h("code", {}, S.app.settings_file), h("br"), "Repository: ", h("code", {}, S.app.repo)),
      ];
    },
  };

  function draw() {
    body.replaceChildren(
      h("nav", {}, navBtn("appearance", "Appearance"), navBtn("ai", "AI"), navBtn("projects", "Projects"),
        navBtn("rendering", "Rendering"), navBtn("about", "About")),
      h("div", { class: "panel" }, panels[tab]()));
  }
  draw();
  modal({ title: "Settings", body, wide: true });
}

function check(label, value, onChange) {
  return h("label", { class: "row", style: { gap: "6px", cursor: "pointer" } },
    h("input", { type: "checkbox", checked: value, onchange: (e) => onChange(e.target.checked) }), label);
}

function fontSelect(value, presets, onChange) {
  const known = presets.includes(value);
  const custom = h("input", { type: "text", placeholder: "Any installed font family", value: known ? "" : value,
    onchange: (e) => e.target.value.trim() && onChange(e.target.value.trim()) });
  const sel = h("select", { onchange: (e) => { if (e.target.value !== "__custom") onChange(e.target.value); custom.hidden = e.target.value !== "__custom"; } },
    presets.map((p) => h("option", { value: p, selected: value === p }, p[0].toUpperCase() + p.slice(1))),
    h("option", { value: "__custom", selected: !known }, "Other…"));
  custom.hidden = known;
  return h("div", { style: { display: "grid", gap: "6px" } }, sel, custom);
}

function toHex(c) {
  if (/^#[0-9a-f]{6}$/i.test(c)) return c;
  const ctx = document.createElement("canvas").getContext("2d");
  ctx.fillStyle = c;
  return ctx.fillStyle;
}
