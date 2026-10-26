// Small DOM toolkit: element builder, icons, dialogs, context menu, toasts, lightbox.

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "html") el.innerHTML = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const PATHS = {
  plus: "M12 5v14M5 12h14",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  branch: "M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9",
  chevron: "M9 6l6 6-6 6",
  down: "M6 9l6 6 6-6",
  split: "M4 4h16v16H4zM12 4v16",
  info: "M12 16v-4M12 8h.01M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  eyeoff: "M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6 0 10 7 10 7a17 17 0 0 1-3.2 3.9M6.6 6.6C3.8 8.4 2 12 2 12s4 7 10 7a9.7 9.7 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  send: "M22 2L11 13M22 2l-7 20-4-9-9-4z",
  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3",
  play: "M8 5v14l11-7z",
  cube: "M21 16V8l-9-5-9 5v8l9 5 9-5zM3.3 7L12 12l8.7-5M12 22V12",
  open: "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3",
  trash: "M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6",
  x: "M18 6L6 18M6 6l12 12",
  check: "M20 6L9 17l-5-5",
  lock: "M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4",
  edit: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z",
  refresh: "M23 4v6h-6M1 20v-6h6M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15",
  jobs: "M12 8v4l3 3M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z",
  globe: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z",
  upload: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12",
  file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6",
  image: "M3 3h18v18H3zM8.5 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM21 15l-5-5L5 21",
  text: "M4 7V4h16v3M9 20h6M12 4v16",
  pull: "M12 3v12M7 10l5 5 5-5M5 21h14",
  stop: "M6 6h12v12H6z",
  sparkle: "M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z",
};

export function icon(name, cls = "") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  if (cls) svg.setAttribute("class", cls);
  const p = document.createElementNS(ns, "path");
  p.setAttribute("d", PATHS[name] || PATHS.info);
  svg.append(p);
  return svg;
}

export function explain(text) {
  return h("div", { class: "explain" }, icon("info"), h("div", {}, text));
}

export function toast(msg, kind = "") {
  const t = h("div", { class: `toast ${kind}` }, msg);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), kind === "error" ? 7000 : 3500);
}

const root = () => document.getElementById("overlay-root");

export function modal({ title, body, actions = [], wide = false, full = false, onClose }) {
  const close = () => { back.remove(); document.removeEventListener("keydown", esc); onClose && onClose(); };
  const esc = (e) => { if (e.key === "Escape") close(); };
  const footer = actions.length ? h("footer", {}, actions.map((a) =>
    h("button", { class: `btn ${a.primary ? "primary" : ""} ${a.danger ? "danger" : ""}`,
      onclick: async () => { const r = await a.onClick?.(); if (r !== false) close(); } }, a.label))) : null;
  const m = h("div", { class: `modal ${wide ? "wide" : ""} ${full ? "full" : ""}`, role: "dialog", "aria-label": title },
    h("header", {}, title, h("span", { class: "spacer" }),
      h("button", { class: "btn ghost icon", onclick: close, "aria-label": "Close" }, icon("x"))),
    h("div", { class: "body" }, body), footer);
  const back = h("div", { class: "modal-back", onclick: (e) => { if (e.target === back) close(); } }, m);
  root().append(back);
  document.addEventListener("keydown", esc);
  return { close, el: m };
}

export function promptText(title, label, value = "", { multiline = false, okLabel = "OK" } = {}) {
  return new Promise((resolve) => {
    const input = multiline ? h("textarea", {}, value) : h("input", { type: "text", value });
    let done = false;
    const m = modal({
      title, body: h("label", { class: "field" }, h("span", {}, label), input),
      actions: [{ label: "Cancel" }, { label: okLabel, primary: true, onClick: () => { done = true; resolve(input.value.trim()); } }],
      onClose: () => { if (!done) resolve(null); },
    });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !multiline) { done = true; resolve(input.value.trim()); m.close(); } });
    setTimeout(() => input.focus(), 30);
  });
}

export function confirmBox(title, text, okLabel = "OK", danger = false) {
  return new Promise((resolve) => {
    let done = false;
    modal({ title, body: h("p", {}, text),
      actions: [{ label: "Cancel" }, { label: okLabel, primary: !danger, danger, onClick: () => { done = true; resolve(true); } }],
      onClose: () => { if (!done) resolve(false); } });
  });
}

// ---- context menu: right-click anywhere; regions can add their own items via data-ctx
let menuEl = null;
export function closeMenu() { menuEl?.remove(); menuEl = null; }
export function contextMenu(x, y, items) {
  closeMenu();
  menuEl = h("div", { class: "ctxmenu", role: "menu" }, items.map((it) => it === "-" ? h("hr") :
    h("button", { role: "menuitem", onclick: () => { closeMenu(); it.onClick(); } }, icon(it.icon || "chevron"), it.label)));
  document.body.append(menuEl);
  const r = menuEl.getBoundingClientRect();
  menuEl.style.left = Math.min(x, innerWidth - r.width - 8) + "px";
  menuEl.style.top = Math.min(y, innerHeight - r.height - 8) + "px";
}
addEventListener("click", closeMenu);
addEventListener("blur", closeMenu);
addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });

// ---- lightbox for images, video, PDFs and the 3D walkthrough.
// Pass `items` (+ `index`) to browse a set: ← / → keys, on-screen arrows, or click the edges.
export function lightbox({ title, kind, url, download, items, index = 0 }) {
  const list = items && items.length ? items : [{ title, kind, url, download }];
  let i = Math.max(0, Math.min(index, list.length - 1));
  const titleEl = h("b", {});
  const countEl = h("span", { class: "muted small" });
  const actions = h("span", { class: "row" });
  const stage = h("div", { class: "stage" });
  const many = list.length > 1;
  const prevBtn = h("button", { class: "lb-nav prev", "aria-label": "Previous", title: "Previous (←)",
    onclick: (e) => { e.stopPropagation(); go(-1); } }, icon("chevron"));
  const nextBtn = h("button", { class: "lb-nav next", "aria-label": "Next", title: "Next (→)",
    onclick: (e) => { e.stopPropagation(); go(1); } }, icon("chevron"));

  function show() {
    const it = list[i];
    let media;
    if (it.kind === "video") media = h("video", { src: it.url, controls: true, autoplay: true });
    else if (it.kind === "frame") media = h("iframe", { src: it.url, allow: "fullscreen" });
    else media = h("img", { src: it.url, alt: it.title });
    stage.replaceChildren(media);
    titleEl.textContent = it.title;
    countEl.textContent = many ? `${i + 1} / ${list.length}` : "";
    actions.replaceChildren(...[
      it.kind === "frame" ? h("button", { class: "btn", onclick: () => window.open(it.url, "_blank") }, icon("open"), "Own window") : null,
      it.download ? h("a", { class: "btn", href: it.download }, icon("download"), "Download") : null,
    ].filter(Boolean));
    for (const j of [i - 1, i + 1]) {  // preload neighbours so arrowing feels instant
      const n = list[(j + list.length) % list.length];
      if (n && n.kind !== "video" && n.kind !== "frame") new Image().src = n.url;
    }
  }
  function go(step) {
    if (!many) return;
    i = (i + step + list.length) % list.length;
    show();
  }
  const close = () => { lb.remove(); document.removeEventListener("keydown", keys); };
  const keys = (e) => {
    if (e.key === "Escape") close();
    else if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
    else if (e.key === "Home" && many) { i = 0; show(); }
    else if (e.key === "End" && many) { i = list.length - 1; show(); }
  };
  const lb = h("div", { class: "lightbox", onclick: (e) => { if (e.target === stage) close(); } },
    h("header", {}, titleEl, countEl, h("span", { class: "spacer", style: { flex: 1 } }), actions,
      h("button", { class: "btn", onclick: close }, icon("x"), "Close")),
    h("div", { class: "lb-body" }, stage, many ? prevBtn : null, many ? nextBtn : null));
  show();
  root().append(lb);
  document.addEventListener("keydown", keys);
}

export function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(typeof iso === "number" ? iso * 1000 : iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function bytes(n) {
  if (n > 1e9) return (n / 1e9).toFixed(1) + " GB";
  if (n > 1e6) return (n / 1e6).toFixed(1) + " MB";
  if (n > 1e3) return Math.round(n / 1e3) + " KB";
  return n + " B";
}
