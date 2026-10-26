// App shell: state, routing (#/n/<node>/<input|output>), top bar, explanations, context menu, live events.
import { api, enc, events } from "./api.js";
import { renderInput } from "./inputs.js";
import { renderJobs, jobsBadge } from "./jobs.js";
import { renderNav } from "./nav.js";
import { renderOutput } from "./outputs.js";
import { openSettings } from "./settings.js";
import { apply as applyTheme } from "./theme.js";
import { contextMenu, h, icon, toast } from "./ui.js";

export const S = {
  app: null, settings: null, tree: [], nodeId: null, node: null, outputs: null,
  tab: "input", split: false, jobs: new Map(), expanded: new Set(), drawerOpen: false,
};

export const actions = {
  async openNode(id, tab) {
    S.nodeId = id;
    if (tab) S.tab = tab;
    location.hash = id ? `#/n/${enc(id)}/${S.tab}` : "";
    if (id) {
      const [proj] = id.split("/");
      S.expanded.add(proj);
      await Promise.all([actions.refreshNode(), actions.refreshOutputs()]);
    } else { S.node = null; S.outputs = null; }
    remember();
    render();
  },
  setTab(tab) {
    S.tab = tab;
    if (S.nodeId) history.replaceState(null, "", `#/n/${enc(S.nodeId)}/${tab}`);
    remember();
    render();
  },
  toggleSplit() { S.split = !S.split; remember(); render(); },
  async refreshNode() {
    if (!S.nodeId) return;
    try { S.node = await api.get(`/api/nodes/${enc(S.nodeId)}`); }
    catch (e) { toast(e.message, "error"); S.node = null; S.nodeId = null; }
  },
  async refreshOutputs() {
    if (!S.nodeId) return;
    try { S.outputs = await api.get(`/api/nodes/${enc(S.nodeId)}/outputs`); } catch { S.outputs = null; }
  },
  async refreshTree() { S.tree = (await api.get("/api/tree")).tree; },
  async saveSettings(patch) {
    const r = await api.put("/api/settings", patch);
    S.settings = r.settings;
    S.app.providers = r.providers;
    S.app.ai_name = r.ai_name;
    applyTheme(S.settings.appearance);
    render();
    return r;
  },
  toggleExplain() {
    const now = S.settings.appearance.show_explanations !== false;
    actions.saveSettings({ appearance: { show_explanations: !now } });
  },
  render: () => render(),
  renderTopbar: () => renderTopbar(),
};

let rememberTimer;
function remember() {
  clearTimeout(rememberTimer);
  rememberTimer = setTimeout(() => api.put("/api/settings",
    { window: { last_node: S.nodeId, last_tab: S.tab, split: S.split } }).catch(() => {}), 600);
}

// ---------------------------------------------------------------- rendering

export function render() {
  renderNav(document.getElementById("nav"), S, actions);
  renderTopbar();
  const panes = document.getElementById("panes");
  const pin = document.getElementById("pane-input");
  const pout = document.getElementById("pane-output");
  panes.classList.toggle("split", S.split && !!S.node);
  if (!S.node) {
    pout.hidden = true;
    pin.hidden = false;
    pin.replaceChildren(welcome());
    return;
  }
  pin.hidden = !(S.split || S.tab === "input");
  pout.hidden = !(S.split || S.tab === "output");
  if (!pin.hidden) renderInput(pin, S, actions);
  if (!pout.hidden) renderOutput(pout, S, actions);
}

function renderTopbar() {
  const bar = document.getElementById("topbar");
  const explainOn = S.settings.appearance.show_explanations !== false;
  const running = [...S.jobs.values()].filter((j) => j.status === "running" || j.status === "queued").length;
  bar.replaceChildren(...[
    S.node ? h("div", { class: "tabs", role: "tablist" },
      ["input", "output"].map((t) => h("button", {
        class: `tab ${S.tab === t ? "active" : ""}`, role: "tab",
        "aria-selected": S.tab === t, title: t === "input" ? "What the design is made from" : "Everything made from the model",
        onclick: () => actions.setTab(t) }, t === "input" ? "Input" : "Output"))) : null,
    S.node ? h("div", { class: "crumb" }, S.node.parent_title ? `${S.node.parent_title} › ` : "",
      h("b", {}, S.node.meta.title)) : h("div", { class: "crumb" }, h("b", {}, S.app.brand.name)),
    h("span", { class: "spacer" }),
    S.node ? h("button", { class: `btn ${S.split ? "on" : ""}`, onclick: actions.toggleSplit,
      title: "Show the input and output pages side by side" }, icon("split"), S.split ? "Unsplit" : "Split view") : null,
    h("button", { class: `btn ${explainOn ? "" : "on"}`, onclick: actions.toggleExplain,
      title: "Hide or show the explanation text (also on right-click)" },
      icon(explainOn ? "eyeoff" : "eye"), explainOn ? "Hide explanations" : "Show explanations"),
    h("button", { class: "btn", onclick: () => { S.drawerOpen = !S.drawerOpen; renderJobs(S, actions); },
      title: "Background jobs: AI rounds, renders, videos" }, icon("jobs"), "Jobs", running ? jobsBadge(running) : null),
    h("button", { class: "btn icon", onclick: () => openSettings(S, actions), title: "Settings", "aria-label": "Settings" }, icon("gear")),
  ].filter(Boolean));
}

function welcome() {
  const b = S.app.brand;
  return h("div", { class: "welcome" },
    b.has_logo ? h("img", { src: "/branding/logo.png", alt: "" }) : null,
    h("h1", {}, b.name),
    h("p", { class: "muted" }, b.tagline),
    h("div", { class: "explain", style: { textAlign: "left" } }, icon("info"),
      h("div", {}, "Start a project for a property, add what you have (the landscaper's plan, photos, notes), and send it to ",
        S.app.ai_name, ". It writes the design file, which deterministic code turns into one 3D model. Every image, video ",
        "and walkthrough is then taken from that model.")),
    h("button", { class: "btn primary big", onclick: () => document.getElementById("new-project")?.click() },
      icon("plus"), "New project"));
}

// ---------------------------------------------------------------- global right-click menu

addEventListener("contextmenu", (e) => {
  if (e.target.closest("input, textarea, [contenteditable]")) return; // keep native menus for text
  e.preventDefault();
  const on = S.settings.appearance.show_explanations !== false;
  const items = [];
  const nodeEl = e.target.closest("[data-node]");
  if (nodeEl && nodeEl.ctxItems) items.push(...nodeEl.ctxItems(), "-");
  const card = e.target.closest("[data-ctx]");
  if (card && card.ctxItems) items.push(...card.ctxItems(), "-");
  items.push({ label: on ? "Hide explanations" : "Show explanations", icon: on ? "eyeoff" : "eye", onClick: actions.toggleExplain });
  if (S.node) {
    items.push({ label: S.split ? "Unsplit view" : "Split input / output", icon: "split", onClick: actions.toggleSplit });
    items.push({ label: S.tab === "input" ? "Go to output page" : "Go to input page", icon: "chevron",
      onClick: () => actions.setTab(S.tab === "input" ? "output" : "input") });
  }
  items.push({ label: "Settings", icon: "gear", onClick: () => openSettings(S, actions) });
  contextMenu(e.clientX, e.clientY, items);
});

// ---------------------------------------------------------------- nav resizing

function navResizer() {
  const bar = document.getElementById("nav-resizer");
  bar.addEventListener("pointerdown", (e) => {
    bar.setPointerCapture(e.pointerId);
    const move = (ev) => document.documentElement.style.setProperty("--nav-w", Math.max(200, Math.min(520, ev.clientX)) + "px");
    const up = (ev) => {
      bar.removeEventListener("pointermove", move);
      bar.removeEventListener("pointerup", up);
      actions.saveSettings({ appearance: { nav_width: Math.max(200, Math.min(520, ev.clientX)) } });
    };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", up);
  });
}

// ---------------------------------------------------------------- boot

let refreshTimer = null;
function scheduleRefresh(nodeId) {
  if (nodeId && nodeId !== S.nodeId) { actions.refreshTree().then(render); return; }
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => {
    const active = document.activeElement;
    const typing = active && active.matches("textarea, input[type=text]") && active.closest(".pane");
    await Promise.all([actions.refreshTree(), actions.refreshNode(), actions.refreshOutputs()]);
    if (typing) { renderNav(document.getElementById("nav"), S, actions); renderTopbar(); }  // don't clobber typing
    else render();
  }, 250);
}

async function boot() {
  S.app = await api.get("/api/app");
  S.settings = S.app.settings;
  applyTheme(S.settings.appearance);
  document.title = S.app.brand.name;
  await actions.refreshTree();
  for (const j of (await api.get("/api/jobs")).jobs) S.jobs.set(j.id, j);
  S.split = !!S.settings.window.split;
  const m = location.hash.match(/^#\/n\/([^/]+)\/(input|output)/);
  const target = m ? decodeURIComponent(m[1]) : S.settings.window.last_node;
  S.tab = m ? m[2] : (S.settings.window.last_tab || "input");
  const exists = (id) => JSON.stringify(S.tree).includes(`"id":${JSON.stringify(id)}`);
  navResizer();
  if (target && exists(target)) await actions.openNode(target);
  else render();
  events({
    job: (j) => { S.jobs.set(j.id, j); renderTopbar(); if (S.drawerOpen) renderJobs(S, actions);
      if (j.node && j.node === S.nodeId) scheduleRefresh(j.node);
      if (j.status === "failed") toast(`${j.title} failed: ${j.error}`, "error");
      if (j.status === "done") toast(`${j.title} — done`); },
    job_log: (l) => { const j = S.jobs.get(l.id); if (j) { j.log_tail = [...(j.log_tail || []), l.line].slice(-6); if (S.drawerOpen) renderJobs(S, actions); } },
    node_changed: (d) => scheduleRefresh(d.node),
    tree_changed: () => scheduleRefresh(null),
    settings: (s) => { S.settings = s; applyTheme(s.appearance); },
  });
}

addEventListener("hashchange", () => {
  const m = location.hash.match(/^#\/n\/([^/]+)\/(input|output)/);
  if (m && decodeURIComponent(m[1]) !== S.nodeId) actions.openNode(decodeURIComponent(m[1]), m[2]);
});

boot().catch((e) => {
  document.body.replaceChildren(h("div", { class: "welcome" }, h("h1", {}, "Couldn't start"), h("p", {}, e.message)));
});
