// The site map editor: a feature list + properties panel beside a top-down map of the design over a
// backdrop (aerial photo, plan, LiDAR height map, straight-down drone photo), focused on one build step.
//
// Interaction principles (they apply to every step):
// - The list is the reliable way in: every feature of the step is a row (name, type); selecting a row
//   selects it on the map and zooms to it. Other steps' features are one click away in "Other steps".
// - The map is forgiving: one hit-test decides what a click means, with generous screen-space
//   tolerances. Small things win over big things they sit on; clicking the same spot again cycles to
//   the next thing underneath. Hovering highlights what a click would select and names it.
// - Nothing changes by accident: a shape must be selected before dragging moves or reshapes it
//   (dragging anything else pans). Drags only start after a few pixels.
// - Reshaping: big corner handles; drag anywhere on an edge to add a corner there; Delete (or
//   double-click / right-click) removes a corner; snapping to nearby corners (Alt places freely);
//   edge lengths shown; square corners, rotate, exact coordinates in the panel.
// - Adding: pick a type, then drag a rectangle or click corners (Enter or double-click finishes,
//   Backspace removes the last corner, Esc cancels); the new feature is selected, name ready to type.
// - Properties: rename (references follow), material/species/heights, notes, duplicate, delete, or
//   edit the element as text.
// Edits stay here until Save; the server writes them into the design file in each element's own
// frame and units without touching anything else. One editor per node survives page re-renders.
import { api, enc } from "./api.js";
import { confirmBox, h, icon, modal, toast } from "./ui.js";

const NS = "http://www.w3.org/2000/svg";
const COLORS = { structure: "#ff9f1c", deck: "#ffbf69", paving: "#4cc9f0", path: "#48bfe3", lawn: "#80ed99",
  bed: "#c77dff", wall: "#f15bb5", fence: "#f72585", light: "#ffd60a", plant: "#38b000", plant_group: "#70e000",
  plant_row: "#9ef01a", boulder: "#d9d9d9", mesh: "#aaaaaa", inventory: "#00f5d4", boundary: "#ff3b3b",
  pin: "#ff4d6d", photo: "#ffd166", origin: "#ffffff" };
const KIND_LABEL = { structure: "Building", deck: "Deck", paving: "Paving", path: "Walkway", lawn: "Lawn", bed: "Bed",
  wall: "Wall", fence: "Fence", light: "Light", plant: "Tree / shrub", plant_group: "Planting area", plant_row: "Hedge / row",
  boulder: "Boulder", mesh: "3D model" };
const STEP_OF = { structure: "buildings", deck: "buildings", mesh: "details", paving: "surfaces", path: "surfaces", lawn: "surfaces",
  bed: "surfaces", wall: "fixtures", fence: "fixtures", light: "fixtures", plant: "plants", plant_group: "plants",
  plant_row: "plants", boulder: "plants" };
const SIZE_KEY = { plant: "spread", boulder: "size" };
// What can be added in each step: [kind, label, how it's drawn]
const ADDABLE = {
  surfaces: [["paving", "Paving / driveway", "rect"], ["bed", "Bed / gravel", "poly"], ["lawn", "Lawn", "poly"], ["path", "Walkway", "line"]],
  buildings: [["structure", "Building", "rect"], ["deck", "Deck", "rect"]],
  details: [["structure", "Building", "rect"], ["deck", "Deck", "rect"]],
  fixtures: [["fence", "Fence", "line"], ["wall", "Wall", "line"], ["light", "Light", "point"]],
  plants: [["plant", "Tree / shrub", "point"], ["boulder", "Boulder", "point"], ["plant_group", "Planting area", "poly"], ["plant_row", "Hedge / row", "line"]],
};
const HIT = { vertex: 13, edge: 10, point: 16, line: 11 }; // screen pixels

const instances = new Map();

export function siteMap(S, A, { step, height = 560 }) {
  let m = instances.get(S.nodeId);
  if (!m) { m = new MapEditor(S, A, S.nodeId); instances.set(S.nodeId, m); }
  m.S = S; m.A = A;
  m.setStep(step);
  m.el.style.setProperty("--smap-h", `${height}px`);
  m.syncVersion(versionKey(S.node));
  return m.el;
}
export function mapIsDirty(nodeId) { return !!instances.get(nodeId)?.changes.size; }
function versionKey(N) { const v = N.versions[N.versions.length - 1]; return v ? `${v.k}:${v.sha}` : "none"; }

class MapEditor {
  constructor(S, A, nodeId) {
    this.S = S; this.A = A; this.nodeId = nodeId;
    this.url = `/api/nodes/${enc(nodeId)}`;
    this.data = null; this.work = null; this.changes = new Map(); this.undoStack = []; this.redoStack = [];
    this.step = null; this.mode = "select"; this.sel = null; this.selVertex = null; this.hover = null;
    this.draft = null; this.version = null; this.backdrop = undefined; this.opacity = 1; this.vb = null;
    this.full = false; this.labels = "smart"; this.showOthers = false; this.snap = true; this.filter = "";
    this.lastClick = null; this.snapMark = null; this.expanded = new Set();
    this.el = h("div", { class: "smap", tabIndex: 0 });
    this.bar = h("div", { class: "smap-bar" });
    this.side = h("div", { class: "smap-side" });
    this.svg = document.createElementNS(NS, "svg");
    this.svg.setAttribute("class", "smap-svg");
    this.banner = h("div", { class: "smap-banner", hidden: true });
    this.tip = h("div", { class: "smap-tip", hidden: true });
    this.pop = h("div", { class: "smap-pop", hidden: true });
    this.stage = h("div", { class: "smap-stage" }, this.svg, this.banner, this.tip, this.pop);
    this.status = h("div", { class: "smap-status" });
    this.el.append(this.bar, h("div", { class: "smap-body" }, this.side, h("div", { class: "smap-main" }, this.stage, this.status)));
    this.bindEvents();
  }

  // ---------------------------------------------------------------- data
  setStep(step) {
    if (step === this.step) return;
    this.step = step; this.sel = null; this.selVertex = null; this.draft = null; this.mode = "select"; this.filter = "";
    if (this.work) { this.fit(); this.redraw(); }
  }
  async syncVersion(v) {
    if (v === this.version && this.data) return;
    if (this.changes.size) { this.version = v; this.notice = "The design changed elsewhere. Save or discard your edits to see it."; this.drawStatus(); return; }
    this.version = v;
    await this.load();
  }
  async load() {
    try { this.data = await api.get(`${this.url}/map`); this.error = null; }
    catch (e) { this.error = e.message; this.data = null; }
    this.work = this.data && structuredClone(this.data);
    this.notice = null;
    if (this.data && this.backdrop === undefined) this.backdrop = this.data.backdrops[0]?.key || null;
    if (!this.vb) this.fit();
    if (this.sel && !this.item(this.sel)) this.sel = null;
    this.redraw();
  }
  u() { return this.work?.m_per_unit || 0.3048; }
  units() { return this.work?.units || "ft"; }

  // ---------------------------------------------------------------- the model as "items"
  // Everything the editor can select, in one shape: {key, type, label, kind, step, color, geoms, ...}.
  items() {
    const w = this.work;
    if (!w) return [];
    const out = [];
    for (const e of w.elements) {
      if (e.enabled === false && !e._new) continue;
      out.push({ key: `el:${e.id}`, type: "el", el: e, label: e.id, kind: e.kind, kindLabel: KIND_LABEL[e.kind] || e.kind,
        step: STEP_OF[e.kind] || "surfaces", color: COLORS[e.kind] || "#fff", geoms: e.geoms, size: e.size_m, members: e.members });
    }
    w.inventory.forEach((it) => {
      if (!it.geom?.pts) return;
      out.push({ key: `inv:${it.index}`, type: "inv", inv: it, label: it.label || it.id || `feature ${it.index + 1}`,
        kind: "inventory", kindLabel: (it.category || "feature").replace(/_/g, " "), step: "inventory", color: COLORS.inventory, geoms: [it.geom] });
    });
    if (w.boundary.length) out.push({ key: "boundary", type: "boundary", label: "Property boundary", kind: "boundary",
      kindLabel: "Boundary", step: "boundary", color: COLORS.boundary, geoms: [{ type: "polygon", pts: w.boundary, path: ["boundary"] }] });
    out.push({ key: "origin", type: "origin", label: "Site origin (0, 0)", kind: "origin", kindLabel: "Geo-reference",
      step: "location", color: COLORS.origin, geoms: [{ type: "point", pts: [[0, 0]] }], fixed: true });
    (w.photos || []).forEach((p) => out.push({ key: `photo:${p.index}`, type: "photo", photo: p, label: p.name,
      kind: "photo", kindLabel: `Photo camera${p.pitch <= -80 ? " (straight down)" : ""}`, step: "location",
      color: COLORS.photo, geoms: [{ type: "point", pts: [p.at] }], fixed: true }));
    (w.comments || []).forEach((c, i) => out.push({ key: `pin:${c.id}`, type: "pin", pin: c, label: `Comment ${i + 1}: ${c.text.slice(0, 40)}`,
      kind: "pin", kindLabel: c.resolved ? "Comment (done)" : "Comment", step: c.step, color: c.resolved ? "#999" : COLORS.pin,
      geoms: [{ type: "point", pts: [c.at] }] }));
    return out;
  }
  item(key) { return this.items().find((i) => i.key === key); }
  inStep(it) {
    if (it.type === "pin") return it.step === this.step;
    return it.step === this.step || (this.step === "details" && it.step === "buildings");
  }
  visible(it) {
    if (this.inStep(it) || it.key === this.sel) return true;
    if (it.type === "pin") return this.showOthers;
    if (it.type === "boundary") return true;
    if (it.type === "origin" || it.type === "photo") return false;
    if (it.type === "inv") return this.showOthers;
    return this.showOthers || ["surfaces", "buildings", "details", "fixtures", "plants", "boundary", "inventory"].includes(this.step);
  }

  // ---------------------------------------------------------------- view
  scale() { const r = this.svg.getBoundingClientRect(); return r.width && this.vb ? this.vb[2] / r.width : 0.05; }
  fit(pts) {
    if (!this.work) return;
    if (!pts) {
      pts = [...this.work.boundary];
      for (const it of this.items()) if (this.inStep(it) && it.type !== "pin") it.geoms.forEach((g) => pts.push(...g.pts));
    }
    if (!pts.length) pts = [[0, 0], [30, 30]];
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    let [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const pad = Math.max(3, 0.12 * Math.max(x1 - x0, y1 - y0));
    x0 -= pad; x1 += pad; y0 -= pad; y1 += pad;
    const box = this.svg.getBoundingClientRect();
    const aspect = box.width > 0 && box.height > 0 ? box.height / box.width : 0.6;
    let w = x1 - x0;
    const hh = y1 - y0;
    if (hh / w > aspect) { const nw = hh / aspect; x0 -= (nw - w) / 2; w = nw; }
    this.vb = [x0, -(y0 + hh / 2) - (w * aspect) / 2, w, w * aspect];
  }
  zoomTo(it) {
    const pts = it.geoms.flatMap((g) => g.pts);
    if (pts.length === 1) { const [x, y] = pts[0], r = it.fixed ? 25 : Math.max(8, (it.size || 0) * 1.5); this.fit([[x - r, y - r], [x + r, y + r]]); }
    else this.fit(pts);
  }
  toSite(ev) {
    const pt = this.svg.createSVGPoint();
    pt.x = ev.clientX; pt.y = ev.clientY;
    const p = pt.matrixTransform(this.root.getScreenCTM().inverse());
    return [p.x, p.y];
  }

  // ---------------------------------------------------------------- edits, undo
  record(change) {
    // Edits to an element added in this session fold into its "add".
    const add = change.id && this.changes.get(`add:${change.id}`);
    if (add && (change.pts || "value" in change || change.length != null)) {
      if (change.pts) setPath(add.add, change.path, change.path[0] === "at" ? change.pts[0] : change.pts);
      else if ("value" in change) setPath(add.add, change.path, change.value);
      else add.add[change.path[0]] = round2(change.length / this.u());
      return;
    }
    const k = change.rename ? `rename:${change.rename.from}` : change.spec ? `spec:${change.spec.join(".")}`
      : change.site ? "site" : change.inventory != null ? `inv:${change.inventory}:${change.path.join(".")}`
        : change.add ? `add:${change.add.id}` : change.delete ? `del:${change.id}` : change.replace ? `rep:${change.id}`
          : `${change.id}:${change.path.join(".")}`;
    this.changes.delete(k);  // re-insert so the order of edits is kept
    this.changes.set(k, change);
  }
  checkpoint() {
    this.undoStack.push([structuredClone(this.work), new Map(this.changes), this.sel]);
    if (this.undoStack.length > 80) this.undoStack.shift();
    this.redoStack = [];
  }
  undo() {
    const u = this.undoStack.pop();
    if (!u) return;
    this.redoStack.push([structuredClone(this.work), new Map(this.changes), this.sel]);
    [this.work, this.changes, this.sel] = u; this.selVertex = null; this.redraw();
  }
  redo() {
    const r = this.redoStack.pop();
    if (!r) return;
    this.undoStack.push([structuredClone(this.work), new Map(this.changes), this.sel]);
    [this.work, this.changes, this.sel] = r; this.selVertex = null; this.redraw();
  }
  discard() { this.changes.clear(); this.undoStack = []; this.redoStack = []; this.sel = null; this.notice = null; this.load(); }
  async save() {
    if (!this.changes.size || this.saving) return;
    this.saving = true; this.drawBar();
    try {
      const r = await api.post(`${this.url}/map/edits`, { changes: [...this.changes.values()] });
      this.changes.clear(); this.undoStack = []; this.redoStack = [];
      toast(`Saved to the design${r.notes?.length ? `: ${r.notes.join(", ")}` : ""}`);
      this.version = null;
      await this.A.refreshNode(); this.A.render();
      await this.load();
    } catch (e) { toast(e.message, "error"); }
    finally { this.saving = false; this.drawBar(); }
  }
  geomChanged(it, gi) {
    const g = it.geoms[gi];
    const pts = g.pts.map((p) => [round3(p[0]), round3(p[1])]);
    if (it.type === "el") this.record({ id: it.el.id, path: g.path, pts });
    else if (it.type === "inv") this.record({ inventory: it.inv.index, path: g.path, pts });
    else if (it.type === "boundary") this.record({ site: "boundary", pts });
  }
  setField(it, path, value) {
    this.checkpoint();
    if (it.type === "el") { setPath(it.el.raw, path, value); if (path[0] === "species") it.el.species = value; this.record({ id: it.el.id, path, value }); }
    else if (it.type === "inv") { setPath(it.inv.raw, path, value); it.inv[path[0]] = value; this.record({ inventory: it.inv.index, path, value }); }
    this.redraw();
  }
  setSpec(path, value, apply) { this.checkpoint(); apply?.(); this.record({ spec: path, value }); this.redraw(); }

  // ---------------------------------------------------------------- hit testing
  candidates(p) {
    const k = this.scale();
    const hits = [];
    for (const it of this.items()) {
      if (!this.visible(it)) continue;
      let best = null;
      for (const g of it.geoms) {
        let d, area;
        if (g.type === "point") {
          const r = it.type === "el" && it.size ? it.size / 2 : 0;
          d = Math.max(0, dist(p, g.pts[0]) - r) / k;
          if (d > HIT.point) continue;
          area = Math.max(Math.PI * r * r, 0.01);
        } else if (g.type === "line") {
          d = distToPolyline(p, g.pts, false) / k;
          if (d > HIT.line) continue;
          area = 0.05;
        } else {
          const inside = pointInPoly(p, g.pts);
          d = inside ? 0 : distToPolyline(p, g.pts, true) / k;
          if (!inside && d > HIT.edge) continue;
          area = Math.abs(polyArea(g.pts));
        }
        if (!best || area < best.area) best = { area, d };
      }
      if (!best) continue;
      // This step before other steps; pins first; then small before big (and closer before farther).
      const rank = (this.inStep(it) ? 0 : 1e12) + (it.type === "pin" ? 0 : 1e6) + best.area + best.d * 0.5;
      hits.push({ it, rank });
    }
    hits.sort((a, b) => a.rank - b.rank);
    return hits.map((x) => x.it);
  }
  pickHandle(p) { // a corner, an edge or the size handle of the selected item
    const it = this.sel && this.item(this.sel);
    if (!it || it.fixed || it.type === "pin") return null;
    const k = this.scale();
    for (let gi = 0; gi < it.geoms.length; gi++) {
      const g = it.geoms[gi];
      if (g.type === "point") {
        if (SIZE_KEY[it.kind] && it.size) {
          const c = g.pts[0];
          if (dist(p, [c[0] + it.size / 2, c[1]]) / k < HIT.vertex) return { kind: "size", it };
        }
        continue;
      }
      let bi = -1, bd = Infinity;
      g.pts.forEach((q, i) => { const d = dist(p, q) / k; if (d < HIT.vertex && d < bd) { bd = d; bi = i; } });
      if (bi >= 0) return { kind: "vertex", it, gi, i: bi };
    }
    for (let gi = 0; gi < it.geoms.length; gi++) {
      const g = it.geoms[gi];
      if (g.type === "point") continue;
      const n = g.pts.length, closed = g.type === "polygon";
      for (let i = 0; i < (closed ? n : n - 1); i++) {
        const a = g.pts[i], b = g.pts[(i + 1) % n];
        if (distToSeg(p, a, b) / k < HIT.edge) return { kind: "edge", it, gi, i, at: projectOnSeg(p, a, b) };
      }
    }
    return null;
  }

  // ---------------------------------------------------------------- pointer handling
  bindEvents() {
    const svg = this.svg;
    svg.addEventListener("wheel", (ev) => {
      if (!this.vb) return;
      ev.preventDefault();
      const p = this.toSite(ev), f = Math.exp(ev.deltaY * 0.0015), [x, y, w, hh] = this.vb, px = p[0], py = -p[1];
      this.vb = [px - (px - x) * f, py - (py - y) * f, w * f, hh * f];
      this.drawMap();
    }, { passive: false });
    svg.addEventListener("pointermove", (ev) => {
      if (this.dragging || !this.work) return;
      const p = this.toSite(ev);
      if (this.mode === "draw") { this.draftCursor = this.snapPoint(p, ev); this.drawMap(); return; }
      if (this.mode === "comment") { svg.style.cursor = "crosshair"; return; }
      const hd = this.pickHandle(p);
      const top = hd ? null : this.candidates(p)[0];
      svg.style.cursor = hd ? (hd.kind === "edge" ? "copy" : "grab") : top ? (top.key === this.sel ? "move" : "pointer") : "grab";
      const hk = top && top.key !== this.sel ? top.key : null;
      if (hk !== this.hover) { this.hover = hk; this.drawMap(); this.markRows(); }
      this.showTip(ev, hk ? top : null);
    });
    svg.addEventListener("pointerleave", () => { this.tip.hidden = true; if (this.hover) { this.hover = null; this.drawMap(); this.markRows(); } });
    svg.addEventListener("pointerdown", (ev) => this.onDown(ev));
    svg.addEventListener("dblclick", (ev) => {
      if (this.mode === "draw") { ev.preventDefault(); this.finishDraft(); return; }
      const hd = this.pickHandle(this.toSite(ev));
      if (hd?.kind === "vertex") this.removeVertex(hd.it, hd.gi, hd.i);
    });
    svg.addEventListener("contextmenu", (ev) => {
      const hd = this.pickHandle(this.toSite(ev));
      if (hd?.kind === "vertex") { ev.preventDefault(); ev.stopPropagation(); this.removeVertex(hd.it, hd.gi, hd.i); }
    });
    this.el.addEventListener("keydown", (ev) => this.onKey(ev));
    new ResizeObserver(() => {
      this.el.classList.toggle("narrow", this.el.getBoundingClientRect().width < 860);
      if (this.work) this.drawMap();
    }).observe(this.el);
  }

  onDown(ev) {
    if (!this.work || (ev.button !== 0 && ev.button !== 1)) return;
    this.el.focus({ preventScroll: true });
    this.tip.hidden = true;
    const p = this.toSite(ev);
    if (ev.button === 1) return this.pan(ev);
    if (this.mode === "comment") return this.addPin(ev, p);
    if (this.mode === "draw") return this.draftDown(ev, p);
    const hd = this.pickHandle(p);
    const cands = this.candidates(p);
    if (hd) return this.dragHandle(ev, hd, () => this.clickSelect(p, cands, ev));
    const sel = this.sel && this.item(this.sel);
    // Dragging the selected shape moves it; anything else must be clicked (selected) first.
    if (sel && cands.some((c) => c.key === sel.key) && !sel.fixed && sel.type !== "boundary" && sel.type !== "pin") {
      return this.dragMove(ev, sel, p, cands);
    }
    this.pan(ev, () => this.clickSelect(p, cands, ev));
  }

  clickSelect(p, cands, ev) {
    let pick = cands[0] || null;
    const lc = this.lastClick;
    if (lc && dist(lc, p) / this.scale() < 8 && cands.length > 1 && cands.some((c) => c.key === this.sel)) {
      const i = cands.findIndex((c) => c.key === this.sel);  // same spot again → the next one underneath
      pick = cands[(i + 1) % cands.length];
    }
    this.lastClick = p;
    this.select(pick ? pick.key : null);
    if (pick?.type === "pin") this.openPin(pick.pin, ev);
    if (cands.length > 1 && pick) this.flash(`${cands.length} features here — click again for the next one`);
  }

  select(key, zoom = false) {
    this.sel = key; this.selVertex = null; this.pop.hidden = true; this.hover = null;
    const it = key && this.item(key);
    if (it && it.type === "el" && it.el.geoms.length > 1) this.expanded.add(it.key);
    if (it && !this.inStep(it)) this.showOthers = true;
    if (zoom && it) this.zoomTo(it);
    this.redraw();
    if (it) requestAnimationFrame(() => this.side.querySelector(".smap-row.sel")?.scrollIntoView({ block: "nearest" }));
  }
  flash(msg) {
    this.flashMsg = msg; this.drawStatus();
    clearTimeout(this.flashT); this.flashT = setTimeout(() => { this.flashMsg = null; this.drawStatus(); }, 2500);
  }

  pan(ev, onClick) {
    const start = [ev.clientX, ev.clientY], vb0 = this.vb.slice(), k = this.scale();
    let moved = false;
    const move = (e2) => {
      const dx = e2.clientX - start[0], dy = e2.clientY - start[1];
      if (!moved && Math.hypot(dx, dy) < 4) return;
      moved = true; this.dragging = true;
      this.vb = [vb0[0] - dx * k, vb0[1] - dy * k, vb0[2], vb0[3]];
      this.drawMap();
    };
    const up = () => {
      removeEventListener("pointermove", move); removeEventListener("pointerup", up);
      this.dragging = false;
      if (!moved && onClick) onClick();
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  }

  dragHandle(ev, hd, onClick) {
    ev.preventDefault();
    const it = hd.it;
    let started = false;
    const start = [ev.clientX, ev.clientY];
    const begin = () => {
      this.checkpoint(); started = true; this.dragging = true;
      if (hd.kind === "edge") { it.geoms[hd.gi].pts.splice(hd.i + 1, 0, hd.at); hd.kind = "vertex"; hd.i += 1; }
    };
    const move = (e2) => {
      if (!started) { if (Math.hypot(e2.clientX - start[0], e2.clientY - start[1]) < 3) return; begin(); }
      const q = this.snapPoint(this.toSite(e2), e2, hd.kind === "vertex" ? { it, gi: hd.gi, i: hd.i } : null);
      if (hd.kind === "size") { const c = it.geoms[0].pts[0]; it.el.size_m = Math.max(0.2, 2 * dist(q, c)); it.size = it.el.size_m; }
      else it.geoms[hd.gi].pts[hd.i] = q;
      this.drawMap();
    };
    const up = () => {
      removeEventListener("pointermove", move); removeEventListener("pointerup", up);
      this.dragging = false; this.snapMark = null;
      if (!started) { // a click on a corner selects it (Delete removes it, arrows nudge it); on an edge, it's a normal click
        if (hd.kind === "vertex") { this.selVertex = { gi: hd.gi, i: hd.i }; this.redraw(); }
        else onClick?.();
        return;
      }
      if (hd.kind === "size") {
        it.el.raw[SIZE_KEY[it.kind]] = round2(it.el.size_m / this.u());
        this.record({ id: it.el.id, path: [SIZE_KEY[it.kind]], length: round3(it.el.size_m) });
      }
      else { this.geomChanged(it, hd.gi); this.selVertex = { gi: hd.gi, i: hd.i }; }
      this.redraw();
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  }

  dragMove(ev, it, p0, cands) {
    ev.preventDefault();
    const orig = it.geoms.map((g) => g.pts.map((q) => q.slice()));
    const mem = (it.members || []).map((m) => [m.x, m.y]);
    let started = false;
    const start = [ev.clientX, ev.clientY];
    const move = (e2) => {
      if (!started) { if (Math.hypot(e2.clientX - start[0], e2.clientY - start[1]) < 5) return; this.checkpoint(); started = true; this.dragging = true; }
      const p = this.toSite(e2);
      let dx = p[0] - p0[0], dy = p[1] - p0[1];
      if (e2.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0; }
      it.geoms.forEach((g, gi) => { g.pts.splice(0, g.pts.length, ...orig[gi].map((q) => [q[0] + dx, q[1] + dy])); });
      (it.members || []).forEach((m, i) => { m.x = mem[i][0] + dx; m.y = mem[i][1] + dy; });
      this.drawMap();
    };
    const up = () => {
      removeEventListener("pointermove", move); removeEventListener("pointerup", up);
      this.dragging = false;
      if (started) { it.geoms.forEach((_, gi) => this.geomChanged(it, gi)); this.redraw(); }
      else this.clickSelect(p0, cands, ev);  // a click on the selected shape: maybe the one underneath
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  }

  snapPoint(p, ev, self) {
    this.snapMark = null;
    if (!this.snap || ev?.altKey) return [round3(p[0]), round3(p[1])];
    const tol = 10 * this.scale();
    let best = null;
    for (const it of this.items()) {
      if (!this.visible(it) || ["pin", "photo", "origin"].includes(it.type)) continue;
      it.geoms.forEach((g, gi) => g.pts.forEach((q, i) => {
        if (self && it.key === self.it.key && gi === self.gi && i === self.i) return;
        const d = dist(p, q);
        if (d < tol && (!best || d < best.d)) best = { d, q };
      }));
    }
    if (best) { this.snapMark = best.q; return best.q.slice(); }
    return [round3(p[0]), round3(p[1])];
  }

  removeVertex(it, gi, i) {
    const g = it.geoms[gi];
    const min = g.type === "polygon" ? 3 : 2;
    if (g.pts.length <= min) { toast(g.type === "polygon" ? "A shape needs at least 3 corners" : "A line needs at least 2 points"); return; }
    this.checkpoint();
    g.pts.splice(i, 1);
    this.geomChanged(it, gi);
    this.selVertex = null;
    this.redraw();
  }

  onKey(ev) {
    if (ev.target.closest("input, textarea, select")) return;
    const mod = ev.ctrlKey || ev.metaKey, key = ev.key.toLowerCase();
    const it = this.sel && this.item(this.sel);
    if (mod && key === "z") { ev.preventDefault(); ev.shiftKey ? this.redo() : this.undo(); return; }
    if (mod && key === "y") { ev.preventDefault(); this.redo(); return; }
    if (mod && key === "s") { ev.preventDefault(); this.save(); return; }
    if (mod && key === "d" && it) { ev.preventDefault(); this.duplicate(it); return; }
    if (this.mode === "draw") {
      if (ev.key === "Enter") { ev.preventDefault(); this.finishDraft(); }
      else if (ev.key === "Backspace") { ev.preventDefault(); this.draft.pts.pop(); this.drawMap(); this.drawBanner(); }
      else if (ev.key === "Escape") this.cancelDraft();
      return;
    }
    if (ev.key === "Escape") {
      if (!this.pop.hidden) { this.pop.hidden = true; return; }
      if (this.mode !== "select") { this.mode = "select"; this.redraw(); return; }
      if (this.selVertex) { this.selVertex = null; this.drawMap(); return; }
      if (this.sel) { this.select(null); return; }
      if (this.full) this.toggleFull();
      return;
    }
    if ((ev.key === "Delete" || ev.key === "Backspace") && it) {
      ev.preventDefault();
      if (this.selVertex) this.removeVertex(it, this.selVertex.gi, this.selVertex.i);
      else this.remove(it);
      return;
    }
    if (ev.key.startsWith("Arrow") && it && !it.fixed && it.type !== "pin") {
      ev.preventDefault();
      const s = (ev.shiftKey ? 5 : 0.5) * this.u();
      const d = { ArrowLeft: [-s, 0], ArrowRight: [s, 0], ArrowUp: [0, s], ArrowDown: [0, -s] }[ev.key];
      this.checkpoint();
      it.geoms.forEach((g, gi) => {
        if (this.selVertex) { if (this.selVertex.gi !== gi) return; const q = g.pts[this.selVertex.i]; g.pts[this.selVertex.i] = [q[0] + d[0], q[1] + d[1]]; }
        else g.pts.splice(0, g.pts.length, ...g.pts.map((q) => [q[0] + d[0], q[1] + d[1]]));
        this.geomChanged(it, gi);
      });
      this.redraw();
      return;
    }
    if (key === "f" && it) { this.zoomTo(it); this.drawMap(); return; }
    if (key === "c" && !mod) { this.mode = "comment"; this.redraw(); return; }
    if (ev.key === "?") this.shortcuts();
  }
  shortcuts() {
    modal({ title: "Map editor shortcuts", body: h("table", { class: "data" }, h("tbody", {}, [
      ["Click", "Select (click the same spot again for the feature underneath)"], ["Drag a selected shape", "Move it (hold Shift: straight)"],
      ["Drag a corner", "Reshape (snaps to nearby corners; hold Alt to place freely)"], ["Drag an edge", "Add a corner there"],
      ["Double-click / right-click a corner", "Remove the corner"], ["Click a corner, then Delete", "Remove the corner"],
      ["Delete", "Remove the selected feature"], ["Arrow keys", "Nudge 0.5 (Shift: 5) — the selected corner, or the whole feature"],
      ["Ctrl+D", "Duplicate"], ["F", "Zoom to the selection"], ["C", "Pin a comment"], ["Ctrl+Z / Ctrl+Shift+Z", "Undo / redo"],
      ["Ctrl+S", "Save"], ["Esc", "Cancel / deselect / leave full screen"], ["Drawing: Enter or double-click", "Finish (Backspace removes the last point)"],
      ["Scroll / drag the background", "Zoom / pan"],
    ].map(([a, b]) => h("tr", {}, h("td", {}, h("b", {}, a)), h("td", {}, b))))) });
  }

  // ---------------------------------------------------------------- adding
  startDraw(kind, shape) {
    this.mode = "draw"; this.draft = { kind, shape, pts: [] };
    this.sel = null; this.selVertex = null;
    this.redraw();
    this.el.focus({ preventScroll: true });
  }
  cancelDraft() { this.draft = null; this.draftCursor = null; this.mode = "select"; this.redraw(); }
  draftDown(ev, p) {
    const d = this.draft;
    const q = this.snapPoint(p, ev);
    if (d.shape === "point") { d.pts = [q]; this.finishDraft(); return; }
    if (d.shape === "rect") {
      const start = q;
      let moved = false;
      this.dragging = true;
      const move = (e2) => { const c = this.snapPoint(this.toSite(e2), e2); moved = true; d.pts = rectPts(start, c); this.drawMap(); };
      const up = () => {
        removeEventListener("pointermove", move); removeEventListener("pointerup", up);
        this.dragging = false;
        if (!moved || Math.abs(polyArea(d.pts)) < 0.2) { d.shape = "poly"; d.pts = [start]; this.drawMap(); this.drawBanner(); return; }
        this.finishDraft();
      };
      addEventListener("pointermove", move); addEventListener("pointerup", up);
      return;
    }
    if (d.shape === "poly" && d.pts.length >= 3 && dist(q, d.pts[0]) / this.scale() < HIT.vertex) { this.finishDraft(); return; }
    d.pts.push(q);
    this.drawMap(); this.drawBanner();
  }
  finishDraft() {
    const d = this.draft;
    if (!d) return;
    const geomType = d.shape === "point" ? "point" : d.shape === "line" ? "line" : "polygon";
    const need = { point: 1, line: 2, polygon: 3 }[geomType];
    if (d.pts.length < need) { toast(`Add at least ${need} point${need > 1 ? "s" : ""} first`); return; }
    this.checkpoint();
    const base = d.kind.replace(/_/g, "-");
    let n = 1;
    const ids = new Set(this.work.elements.map((x) => x.id));
    while (ids.has(`${base}-${n}`)) n++;
    const id = `${base}-${n}`;
    const geoKey = d.kind === "structure" ? "footprint" : geomType === "line" ? "line" : geomType === "point" ? "at" : "polygon";
    const add = { id, kind: d.kind, ...defaultsFor(d.kind, this.u()) };
    add[geoKey] = geomType === "point" ? d.pts[0] : d.pts;
    add.note = "added on the map";
    this.record({ add });
    const sizeKey = SIZE_KEY[d.kind];
    this.work.elements.push({ id, kind: d.kind, _new: true, enabled: true, raw: structuredClone(add),
      size_m: sizeKey ? add[sizeKey] * this.u() : undefined,
      geoms: [{ path: [geoKey], type: geomType, pts: geomType === "point" ? [d.pts[0]] : d.pts }] });
    this.draft = null; this.draftCursor = null; this.mode = "select";
    this.select(`el:${id}`);
    requestAnimationFrame(() => { const inp = this.side.querySelector(".smap-name"); inp?.focus(); inp?.select(); });
  }
  duplicate(it) {
    if (it.type !== "el") return;
    this.checkpoint();
    const off = 1.5;
    const raw = structuredClone(it.el.raw);
    const ids = new Set(this.work.elements.map((x) => x.id));
    let id = `${it.el.id}-copy`, n = 2;
    while (ids.has(id)) id = `${it.el.id}-copy-${n++}`;
    raw.id = id;
    delete raw.frame;
    const geoms = it.geoms.map((g) => ({ ...g, pts: g.pts.map((q) => [round3(q[0] + off), round3(q[1] - off)]) }));
    for (const g of geoms) setPath(raw, g.path, g.path[0] === "at" ? g.pts[0] : g.pts);
    this.record({ add: raw });
    this.work.elements.push({ ...structuredClone(it.el), id, _new: true, raw, geoms, members: [] });
    this.select(`el:${id}`);
  }
  async remove(it) {
    if (it.type === "pin") return this.deletePin(it.pin);
    if (it.type !== "el") { toast(it.type === "inv" ? "Inventory entries are managed by the AI: pin a comment to change the list." : "This can only be reshaped, not removed."); return; }
    if (!(await confirmBox(`Remove ${it.el.id}?`, "It's removed from the design when you save (Ctrl+Z brings it back before then).", "Remove", true))) return;
    this.checkpoint();
    this.work.elements = this.work.elements.filter((x) => x !== it.el);
    if (it.el._new) this.changes.delete(`add:${it.el.id}`); else this.record({ id: it.el.id, delete: true });
    this.select(null);
  }
  rename(it, to) {
    to = (to || "").trim().replace(/\s+/g, "-");
    const from = it.el.id;
    if (!to || to === from) return;
    if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/.test(to)) { toast("Names may use letters, digits, - _ and ."); this.drawSide(); return; }
    if (this.work.elements.some((x) => x.id === to)) { toast(`There's already a ${to}`); this.drawSide(); return; }
    this.checkpoint();
    if (it.el._new) {
      const add = this.changes.get(`add:${from}`);
      this.changes.delete(`add:${from}`); add.add.id = to; this.changes.set(`add:${to}`, add);
    } else {
      // Later edits address the new name, so the rename is applied right where it happens in the list.
      this.changes.set(`rename:${from}`, { rename: { from, to } });
    }
    it.el.id = to; it.el.raw.id = to;
    this.sel = `el:${to}`;
    this.redraw();
  }

  // ---------------------------------------------------------------- comments
  addPin(ev, at) {
    this.mode = "select";
    const ta = h("textarea", { rows: 3, placeholder: "e.g. The garage ends at this fence post" });
    this.showPop(ev, h("div", {},
      h("b", {}, "Pin a comment"),
      h("div", { class: "muted small" }, `${this.S.app.ai_name} gets it, with this exact spot, when it redoes this step.`),
      ta, h("div", { class: "row end" },
        h("button", { class: "btn", onclick: () => { this.pop.hidden = true; this.redraw(); } }, "Cancel"),
        h("button", { class: "btn primary", onclick: async () => {
          if (!ta.value.trim()) return;
          try {
            const c = await api.post(`${this.url}/map/comments`, { step: this.step, at: at.map(round3), text: ta.value.trim() });
            this.work.comments.push(c); this.data.comments.push(c);
            this.pop.hidden = true; this.select(`pin:${c.id}`);
          } catch (e) { toast(e.message, "error"); }
        } }, "Pin it"))));
    setTimeout(() => ta.focus(), 30);
    this.redraw();
  }
  openPin(c, ev) {
    const ta = h("textarea", { rows: 3 }, c.text);
    this.showPop(ev, h("div", {},
      h("div", { class: "muted small" }, c.resolved ? `Handled${typeof c.resolved === "number" ? ` in round ${c.resolved}` : ""}` : c.sent_round ? `Sent in round ${c.sent_round}` : "Not sent yet"),
      ta, h("div", { class: "row end" },
        h("button", { class: "btn ghost danger", onclick: () => this.deletePin(c) }, "Delete"),
        h("button", { class: "btn", onclick: () => this.updatePin(c, { resolved: c.resolved ? null : "you" }) }, c.resolved ? "Reopen" : "Mark done"),
        h("button", { class: "btn primary", onclick: () => this.updatePin(c, { text: ta.value }) }, "Save"))));
  }
  async updatePin(c, patch) {
    try { const r = await api.put(`${this.url}/map/comments/${c.id}`, patch); Object.assign(c, r); this.pop.hidden = true; this.redraw(); }
    catch (e) { toast(e.message, "error"); }
  }
  async deletePin(c) {
    await api.del(`${this.url}/map/comments/${c.id}`);
    this.work.comments = this.work.comments.filter((x) => x.id !== c.id);
    this.data.comments = this.data.comments.filter((x) => x.id !== c.id);
    this.pop.hidden = true; this.select(null);
  }
  showPop(ev, content) {
    const r = this.stage.getBoundingClientRect();
    this.pop.replaceChildren(content);
    this.pop.hidden = false;
    this.pop.style.left = `${Math.min(r.width - 310, Math.max(8, ev.clientX - r.left + 12))}px`;
    this.pop.style.top = `${Math.min(r.height - 190, Math.max(8, ev.clientY - r.top + 12))}px`;
  }
  showTip(ev, it) {
    if (!it || this.dragging) { this.tip.hidden = true; return; }
    const r = this.stage.getBoundingClientRect();
    this.tip.textContent = `${it.label} · ${it.kindLabel}${this.inStep(it) ? "" : ` · ${this.stepLabel(it.step)}`}`;
    this.tip.hidden = false;
    this.tip.style.left = `${Math.min(r.width - 260, ev.clientX - r.left + 14)}px`;
    this.tip.style.top = `${ev.clientY - r.top + 18}px`;
  }
  toggleFull() { this.full = !this.full; this.el.classList.toggle("full", this.full); setTimeout(() => this.drawMap(), 0); }

  // ---------------------------------------------------------------- drawing
  redraw() { this.drawBar(); this.drawSide(); this.drawMap(); this.drawBanner(); this.drawStatus(); }
  markRows() { this.side.querySelectorAll(".smap-row").forEach((r) => r.classList.toggle("hover", r.dataset.key === this.hover)); }

  drawStatus() {
    const n = this.changes.size;
    this.status.replaceChildren(...[
      h("span", { class: "small", style: { flex: 1 } }, this.flashMsg || this.notice || this.hint()),
      n ? h("span", { class: "small smap-unsaved" }, `${n} unsaved`) : null,
      h("a", { href: "#", class: "small", onclick: (e) => { e.preventDefault(); this.shortcuts(); } }, "Shortcuts (?)")].filter(Boolean));
  }
  hint() {
    if (this.mode === "comment") return "Click the map where the comment applies (Esc cancels).";
    const it = this.sel && this.item(this.sel);
    if (!it) return "Click a feature on the map or in the list. Drag the background to pan, scroll to zoom.";
    if (it.fixed) return "Shown for reference; change its numbers in the panel.";
    if (it.type === "pin") return "Edit the comment in the panel.";
    if (it.type === "boundary") return "Drag a corner to move it · drag an edge to add a corner · Delete removes the selected corner · exact positions in the panel.";
    return "Drag to move · drag a corner to reshape · drag an edge to add a corner · Delete removes the selected corner or feature.";
  }
  drawBanner() {
    const d = this.draft;
    if (this.mode !== "draw" || !d) { this.banner.hidden = true; return; }
    const what = (KIND_LABEL[d.kind] || d.kind).toLowerCase();
    const how = d.shape === "point" ? "Click where it goes." : d.shape === "rect"
      ? "Drag a rectangle (or click to draw corner by corner)." : d.shape === "line"
        ? `Click points along it (${d.pts.length} so far). Enter or double-click finishes; Backspace removes the last.`
        : `Click the corners (${d.pts.length} so far). Click the first corner, press Enter or double-click to finish; Backspace removes the last.`;
    this.banner.hidden = false;
    this.banner.replaceChildren(h("span", {}, h("b", {}, `New ${what}: `), how),
      d.shape === "rect" ? h("button", { class: "btn ghost", onclick: () => { d.shape = "poly"; this.drawBanner(); } }, "Corner by corner") : null,
      h("button", { class: "btn ghost", onclick: () => this.cancelDraft() }, "Cancel"));
  }

  drawBar() {
    const d = this.work;
    const n = this.changes.size;
    this.bar.replaceChildren(...[
      h("button", { class: `btn ${this.mode === "comment" ? "on" : ""}`, title: "Pin a comment to a spot (C)",
        onclick: () => { this.mode = this.mode === "comment" ? "select" : "comment"; this.draft = null; this.redraw(); } }, icon("pin"), "Comment"),
      h("span", { class: "smap-sep" }),
      d?.backdrops?.length ? h("select", { title: "Backdrop image", onchange: (e) => { this.backdrop = e.target.value || null; this.drawMap(); } },
        h("option", { value: "" }, "No backdrop"), d.backdrops.map((b) => h("option", { value: b.key, selected: b.key === this.backdrop }, b.label))) : null,
      d?.backdrops?.length ? h("input", { type: "range", min: 0.15, max: 1, step: 0.05, value: this.opacity, title: "Backdrop opacity",
        class: "smap-op", oninput: (e) => { this.opacity = +e.target.value; this.drawMap(); } }) : null,
      h("select", { title: "Which names to show on the map", onchange: (e) => { this.labels = e.target.value; this.drawMap(); } },
        [["smart", "Names where they fit"], ["all", "All names"], ["none", "Names on hover"]].map(([v, l]) => h("option", { value: v, selected: this.labels === v }, l))),
      h("label", { class: "smap-check", title: "Show other steps' features (selectable)" },
        h("input", { type: "checkbox", checked: this.showOthers, onchange: (e) => { this.showOthers = e.target.checked; this.redraw(); } }), "Other steps"),
      h("label", { class: "smap-check", title: "Snap corners to nearby corners (hold Alt to place freely)" },
        h("input", { type: "checkbox", checked: this.snap, onchange: (e) => { this.snap = e.target.checked; } }), "Snap"),
      h("button", { class: "btn ghost icon", title: "Fit the step in view", onclick: () => { this.fit(); this.drawMap(); } }, icon("target")),
      h("span", { style: { flex: 1 } }),
      h("button", { class: "btn ghost icon", title: "Undo (Ctrl+Z)", disabled: !this.undoStack.length, onclick: () => this.undo() }, icon("undo")),
      h("button", { class: "btn ghost icon", title: "Redo (Ctrl+Shift+Z)", disabled: !this.redoStack.length, onclick: () => this.redo() }, icon("redo")),
      h("button", { class: "btn ghost", disabled: !n, onclick: () => this.discard() }, "Discard"),
      h("button", { class: `btn ${n ? "primary" : ""}`, disabled: !n || this.saving, title: "Save (Ctrl+S)", onclick: () => this.save() }, icon("check"),
        this.saving ? "Saving…" : n ? `Save ${n} change${n === 1 ? "" : "s"}` : "Saved"),
      h("button", { class: "btn ghost icon", title: this.full ? "Exit full screen (Esc)" : "Full screen", onclick: () => this.toggleFull() }, icon(this.full ? "x" : "expand")),
    ].filter(Boolean));
  }

  // ---------------------------------------------------------------- side panel: add, list, properties
  drawSide() {
    if (!this.work) { this.side.replaceChildren(h("div", { class: "muted small smap-empty" }, this.error || "Loading…")); return; }
    const all = this.items();
    const f = this.filter.toLowerCase();
    const match = (it) => !f || `${it.label} ${it.kindLabel}`.toLowerCase().includes(f);
    const mine = all.filter((it) => this.inStep(it) && match(it) && !(it.type === "boundary" && this.step !== "boundary"));
    const others = all.filter((it) => !this.inStep(it) && !it.fixed && it.type !== "pin" && match(it));
    const sel = this.sel && this.item(this.sel);
    const addable = ADDABLE[this.step] || [];

    const row = (it) => {
      const multi = it.type === "el" && it.geoms.length > 1;
      const open = this.expanded.has(it.key);
      const r = h("div", { class: `smap-row ${it.key === this.sel ? "sel" : ""} ${it.key === this.hover ? "hover" : ""}`, "data-key": it.key,
        title: `${it.label} · ${it.kindLabel}`, onclick: () => this.select(it.key, true),
        onmouseenter: () => { this.hover = it.key; this.drawMap(); }, onmouseleave: () => { this.hover = null; this.drawMap(); } },
        h("span", { class: "smap-tw", onclick: multi ? (e) => { e.stopPropagation(); open ? this.expanded.delete(it.key) : this.expanded.add(it.key); this.drawSide(); } : null }, multi ? (open ? "▾" : "▸") : ""),
        h("i", { class: "smap-sw", style: { background: it.color } }),
        h("span", { class: "smap-lbl" }, it.label),
        h("span", { class: "smap-kind" }, it.kindLabel));
      const kids = multi && open ? it.geoms.map((g, gi) => h("div", { class: "smap-row sub", onclick: () => { this.select(it.key); this.fit(g.pts); this.drawMap(); } },
        h("span", { class: "smap-tw" }), h("span", { class: "smap-lbl" }, g.label || `part ${gi + 1}`),
        h("span", { class: "smap-kind" }, `${g.pts.length} corners`))) : [];
      return [r, ...kids];
    };
    const groups = (list) => {
      const by = new Map();
      for (const it of list) {
        const k = it.type === "pin" ? "Comments" : it.type === "photo" ? "Photo cameras" : it.type === "inv" ? "Features" : it.kindLabel;
        (by.get(k) || by.set(k, []).get(k)).push(it);
      }
      if (by.size <= 1) return list.flatMap(row);
      return [...by].flatMap(([k, items]) => [h("div", { class: "smap-group" }, `${k} (${items.length})`), ...items.flatMap(row)]);
    };

    const addBox = addable.length ? h("div", { class: "smap-add" },
      h("div", { class: "smap-h" }, "Add"),
      h("div", { class: "smap-addgrid" }, addable.map(([kind, label, shape]) => h("button", {
        class: `btn ${this.mode === "draw" && this.draft?.kind === kind ? "on" : ""}`,
        title: shape === "rect" ? "Drag a rectangle on the map (or click corners)" : shape === "poly" ? "Click the corners on the map" : shape === "line" ? "Click points along it" : "Click where it goes",
        onclick: () => (this.mode === "draw" && this.draft?.kind === kind ? this.cancelDraft() : this.startDraw(kind, shape)) },
        h("i", { class: "smap-sw", style: { background: COLORS[kind] } }), label)))) : null;

    const list = h("div", { class: "smap-list" },
      h("input", { type: "search", class: "smap-filter", placeholder: `Find among ${mine.length + others.length} features…`, value: this.filter,
        oninput: (e) => { this.filter = e.target.value; const pos = e.target.selectionStart; this.drawSide(); const i = this.side.querySelector(".smap-filter"); i.focus(); i.setSelectionRange(pos, pos); } }),
      mine.length ? groups(mine) : h("div", { class: "muted small smap-empty" }, this.emptyText()),
      others.length ? h("details", { class: "smap-others", open: this.showOthers || !!f, ontoggle: (e) => { if (e.target.open !== this.showOthers) { this.showOthers = e.target.open; this.drawMap(); this.drawBar(); } } },
        h("summary", {}, `Other steps (${others.length})`), groups(others)) : null);

    this.side.replaceChildren(...[addBox, list, sel ? this.props(sel) : null].filter(Boolean));
  }
  emptyText() {
    return { location: "No geo-reference yet: run this step.", ground: "Pin comments where the ground looks wrong.",
      inventory: "No inventory yet: run this step.", boundary: "No boundary yet." }[this.step] || "Nothing here yet. Add one above, or run the step.";
  }

  props(it) {
    const u = this.units();
    const box = h("div", { class: "smap-props" });
    const head = h("div", { class: "smap-props-head" }, h("i", { class: "smap-sw", style: { background: it.color } }));
    if (it.type === "el") {
      head.append(h("input", { type: "text", class: "smap-name", value: it.el.id, title: "Name (Enter to rename; links in the design follow)",
        onkeydown: (e) => { if (e.key === "Enter") e.target.blur(); if (e.key === "Escape") { e.target.value = it.el.id; e.target.blur(); } },
        onchange: (e) => this.rename(it, e.target.value) }));
    } else head.append(h("b", { class: "smap-title" }, it.label));
    head.append(h("button", { class: "btn ghost icon", title: "Deselect (Esc)", onclick: () => this.select(null) }, icon("x")));
    box.append(head, h("div", { class: "muted small" }, it.kindLabel + (this.inStep(it) ? "" : ` · part of ${this.stepLabel(it.step)}`)));

    const fields = h("div", { class: "smap-fields" });
    const field = (label, control, hint) => fields.append(h("label", { class: "smap-field" }, h("span", {}, label), control, hint ? h("small", { class: "muted" }, hint) : null));
    const raw = it.type === "el" ? it.el.raw : it.type === "inv" ? it.inv.raw : null;
    const val = (path) => getPath(raw, path);
    const text = (path, label, ph) => field(label, h("input", { type: "text", value: val(path) ?? "", placeholder: ph || "",
      onchange: (e) => this.setField(it, path, e.target.value.trim() === "" ? null : coerce(e.target.value)) }));
    const num = (path, label) => field(label, h("input", { type: "text", inputmode: "decimal", value: val(path) ?? "", placeholder: `in ${u}`,
      onchange: (e) => this.setField(it, path, e.target.value.trim() === "" ? null : coerce(e.target.value)) }));
    const choice = (path, label, options) => field(label, h("select", { onchange: (e) => this.setField(it, path, e.target.value || null) },
      h("option", { value: "" }, "—"), [...new Set([...(options || []), val(path)].filter((x) => x != null && x !== ""))].map((o) => h("option", { value: o, selected: o === val(path) }, String(o).replace(/_/g, " ")))));
    const check = (path, label) => fields.append(h("label", { class: "smap-checkrow" }, h("input", { type: "checkbox", checked: !!val(path), onchange: (e) => this.setField(it, path, e.target.checked) }), label));
    const note = () => field("Note", h("textarea", { rows: 2, onchange: (e) => this.setField(it, ["note"], e.target.value.trim() || null) }, val(["note"]) || ""));
    const sc = this.work.schema || {};

    if (it.type === "el") {
      const k = it.kind;
      if (k === "paving" || k === "path") choice(["material"], "Material", sc.paving_materials);
      if (k === "path") { num(["width"], `Width (${u})`); choice(["style"], "Style", sc.path_styles); }
      if (k === "bed") { choice(["surface"], "Surface", sc.bed_surfaces); choice(["edging"], "Edging", sc.edging); }
      if (k === "wall") { num(["height"], `Height (${u})`); choice(["material"], "Material", sc.wall_materials); }
      if (k === "fence") { num(["height"], `Height (${u})`); choice(["style"], "Style", sc.fence_styles); text(["color"], "Color", "#6b5a4a"); }
      if (k === "light") choice(["type"], "Type", sc.light_types);
      if (["plant", "plant_group", "plant_row"].includes(k)) {
        const dl = `smap-species`;
        if (!document.getElementById(dl)) document.body.append(h("datalist", { id: dl }, (sc.species || []).map((s) => h("option", { value: s.id }, `${s.common} (${s.category})`))));
        field("Species", h("input", { type: "text", value: val(["species"]) ?? "", list: dl, placeholder: "type to search the catalog",
          onchange: (e) => this.setField(it, ["species"], speciesId(sc.species, e.target.value)) }),
        (sc.species || []).find((s) => s.id === val(["species"]))?.common || (val(["species"]) ? "not in the catalog (the AI can add it)" : ""));
      }
      if (k === "plant") { num(["height"], `Height (${u})`); num(["spread"], `Spread (${u})`); check(["existing"], "Already there today"); }
      if (k === "plant_group" || k === "plant_row") num(["spacing"], `Spacing (${u})`);
      if (k === "boulder") num(["size"], `Size (${u})`);
      if (k === "deck") { num(["height"], `Height above ground (${u})`); text(["material"], "Material", "composite_decking"); }
      if (k === "structure") {
        const parts = raw.wings ? raw.wings.map((w, i) => [["wings", i], w.name || `Part ${i + 1}`]) : [[[], null]];
        for (const [pre, label] of parts) {
          if (label) fields.append(h("div", { class: "smap-sub" }, label));
          num([...pre, "wall_height"], `Wall height (${u})`);
          choice([...pre, "roof", "type"], "Roof", sc.roof_types);
          text([...pre, "roof", "pitch"], "Roof pitch", "6/12");
        }
      }
      note();
    } else if (it.type === "inv") {
      text(["label"], "Label");
      choice(["category"], "Category", sc.inventory_categories);
      choice(["element"], "Modeled as", this.work.elements.map((e) => e.id));
      note();
    } else if (it.type === "origin") {
      const loc = this.work.location || {};
      const o = loc.origin || [];
      field("Latitude, longitude of (0, 0)", h("input", { type: "text", value: o.length ? `${o[0]}, ${o[1]}` : "", placeholder: "40.5727, -105.1440",
        onchange: (e) => { const m = e.target.value.split(/[,\s]+/).filter(Boolean).map(Number); if (m.length === 2 && m.every((x) => !isNaN(x))) this.setSpec(["site", "location", "origin"], m, () => { loc.origin = m; }); } }),
      "Where the design's (0, 0) is on Earth: places the aerial photo, LiDAR and photo cameras.");
      field("North offset (degrees)", h("input", { type: "text", inputmode: "decimal", value: loc.north_offset_deg ?? 0,
        onchange: (e) => { const v = Number(e.target.value); if (!isNaN(v)) this.setSpec(["site", "location", "north_offset_deg"], v, () => { loc.north_offset_deg = v; }); } }),
      "How far the design's up (+Y) is turned clockwise from true north.");
    } else if (it.type === "photo") {
      const p = it.photo, i = p.index;
      fields.append(h("div", { class: "muted small" }, `${(p.z / this.u()).toFixed(0)} ${u} up · heading ${Math.round(p.yaw)}° · tilt ${Math.round(p.pitch)}° · ${Math.round(p.hfov)}° wide`));
      for (const [key, label] of [["yaw", "Heading (° from north)"], ["pitch", "Tilt (°, −90 = straight down)"], ["fov", "Field of view (°)"]])
        field(label, h("input", { type: "text", inputmode: "decimal", value: p.raw?.[key] ?? "", placeholder: "from the photo",
          onchange: (e) => { const v = e.target.value.trim() === "" ? null : Number(e.target.value); if (v === null || !isNaN(v)) this.setSpec(["photo_matches", "photos", i, key], v, () => { p.raw = { ...(p.raw || {}), [key]: v }; }); } }));
    } else if (it.type === "pin") {
      const ta = h("textarea", { rows: 3 }, it.pin.text);
      fields.append(ta, h("div", { class: "row" },
        h("button", { class: "btn", onclick: () => this.updatePin(it.pin, { text: ta.value }) }, "Save text"),
        h("button", { class: "btn", onclick: () => this.updatePin(it.pin, { resolved: it.pin.resolved ? null : "you" }) }, it.pin.resolved ? "Reopen" : "Mark done")));
    } else if (it.type === "boundary") {
      fields.append(h("div", { class: "muted small" }, "Drag corners on the map, or type exact positions below."));
    }
    box.append(fields);

    // geometry: size facts, square corners, rotate, exact coordinates
    if (!it.fixed && it.type !== "pin") {
      const geo = h("div", { class: "smap-geo" });
      const facts = [];
      for (const g of it.geoms) {
        if (g.type === "polygon") facts.push(`${fmtArea(Math.abs(polyArea(g.pts)), this.u(), u)}, ${g.pts.length} corners`);
        if (g.type === "line") facts.push(`${fmtLen(pathLen(g.pts), this.u(), u)} long`);
        if (g.type === "point" && it.size) facts.push(`${fmtLen(it.size, this.u(), u)} across`);
      }
      if (facts.length) geo.append(h("div", { class: "muted small" }, facts.join(" · ")));
      const tools = h("div", { class: "row smap-tools" });
      if (it.geoms.some((g) => g.type === "polygon")) tools.append(h("button", { class: "btn", title: "Make every corner a right angle (buildings, patios, square beds)",
        onclick: () => { this.checkpoint(); it.geoms.forEach((g, gi) => { if (g.type === "polygon") { g.pts.splice(0, g.pts.length, ...squareUp(g.pts)); this.geomChanged(it, gi); } }); this.redraw(); } }, "Square corners"));
      if (it.geoms[0]?.type !== "point") for (const a of [-90, -5, 5, 90]) tools.append(h("button", { class: "btn ghost", title: `Rotate ${a > 0 ? "clockwise" : "counter-clockwise"} ${Math.abs(a)}°`,
        onclick: () => { this.checkpoint(); const c = centroidAll(it.geoms); it.geoms.forEach((g, gi) => { g.pts.splice(0, g.pts.length, ...g.pts.map((q) => rot(q, c, -a))); this.geomChanged(it, gi); }); this.redraw(); } }, `${a > 0 ? "↻" : "↺"}${Math.abs(a)}°`));
      if (tools.childNodes.length) geo.append(tools);
      geo.append(h("details", { class: "smap-coords" }, h("summary", {}, `Exact positions (${u})`),
        it.geoms.map((g, gi) => h("div", {}, it.geoms.length > 1 ? h("div", { class: "smap-sub" }, g.label || `part ${gi + 1}`) : null,
          g.pts.map((q, i) => h("div", { class: "smap-xy" }, h("span", { class: "muted small" }, `${i + 1}`),
            ...[0, 1].map((ax) => h("input", { type: "text", inputmode: "decimal", title: ax ? "y (north)" : "x (east)", value: (q[ax] / this.u()).toFixed(2), onchange: (e) => {
              const v = Number(e.target.value); if (isNaN(v)) return;
              this.checkpoint(); g.pts[i][ax] = v * this.u(); this.geomChanged(it, gi); this.redraw();
            } }))))))));
      box.append(geo);
    }
    const acts = h("div", { class: "row smap-acts" },
      h("button", { class: "btn ghost", title: "F", onclick: () => { this.zoomTo(it); this.drawMap(); } }, icon("target"), "Zoom to"));
    if (it.type === "el") {
      acts.append(h("button", { class: "btn ghost", title: "Ctrl+D", onclick: () => this.duplicate(it) }, "Duplicate"),
        h("button", { class: "btn ghost", onclick: () => this.editAsText(it) }, "Edit as text"),
        h("button", { class: "btn ghost danger", title: "Delete", onclick: () => this.remove(it) }, icon("trash"), "Remove"));
      if (!this.inStep(it)) acts.append(h("button", { class: "btn ghost", onclick: () => { this.S.stepSel = this.S.stepSel || {}; this.S.stepSel[this.nodeId] = it.step; this.A.render(); } }, `Go to ${this.stepLabel(it.step)}`));
    }
    if (it.type === "pin") acts.append(h("button", { class: "btn ghost danger", onclick: () => this.deletePin(it.pin) }, icon("trash"), "Delete"));
    box.append(acts);
    return box;
  }
  stepLabel(k) { return this.work?.steps?.find((s) => s.key === k)?.label || k; }

  editAsText(it) {
    if (it.el._new) { toast("Save the new feature first, then edit it as text"); return; }
    const ta = h("textarea", { class: "code", style: { minHeight: "300px" } }, JSON.stringify(it.el.raw, null, 1));
    modal({ title: `Edit ${it.el.id} as text`, wide: true, body: h("div", {},
      h("p", { class: "muted small" }, `Every field of this feature as written in the design file (positions in its own frame: ${it.el.frame}). docs/SPEC.md describes each field.`), ta),
    actions: [{ label: "Cancel" }, { label: "Apply", primary: true, onClick: () => {
      let v;
      try { v = JSON.parse(ta.value); } catch (e) { toast(`Not valid: ${e.message}`, "error"); return false; }
      if (!v.kind) { toast("It needs a kind", "error"); return false; }
      if (v.id && v.id !== it.el.id) { toast("Rename it with the name field instead"); return false; }
      this.checkpoint();
      it.el.raw = v;
      this.record({ id: it.el.id, replace: v });
      this.redraw();
      toast("Applied. Save to write it; the map redraws its shape after saving.");
      return true;
    } }] });
  }

  // ---------------------------------------------------------------- the map itself
  drawMap() {
    const svg = this.svg;
    if (!this.work || !this.vb) { svg.replaceChildren(); return; }
    const box = svg.getBoundingClientRect();
    if (box.width > 0 && box.height > 0) {
      const want = this.vb[2] * box.height / box.width;
      this.vb[1] -= (want - this.vb[3]) / 2; this.vb[3] = want;
    }
    svg.setAttribute("viewBox", this.vb.join(" "));
    const k = this.scale() || 0.05;
    svg.replaceChildren();
    const root = this.root = el("g", { transform: "scale(1 -1)" });
    svg.append(root);
    const bd = this.work.backdrops.find((b) => b.key === this.backdrop);
    if (bd) {
      // One persistent <image> per backdrop: re-creating it on every redraw re-decodes a large photo (flicker).
      this.bgCache = this.bgCache || new Map();
      let img = this.bgCache.get(bd.url);
      if (!img) {
        const [a, b, c, d, e, f] = bd.matrix;
        img = el("image", { href: bd.url, width: bd.size[0], height: bd.size[1], preserveAspectRatio: "none",
          transform: `matrix(${a} ${b} ${c} ${d} ${e} ${f})` });
        this.bgCache.set(bd.url, img);
      }
      img.setAttribute("opacity", this.opacity);
      root.append(img);
    }
    this.drawGrid(root, k);
    const items = this.items().filter((it) => this.visible(it));
    // big things first so small ones sit on top; this step above other steps; hover and selection last
    const order = (it) => (it.key === this.sel ? 3e12 : it.key === this.hover ? 2.5e12 : 0) + (this.inStep(it) ? 1e12 : 0) - geomSize(it);
    items.sort((a, b) => order(a) - order(b));
    const labels = el("g", {});
    for (const it of items) this.drawItem(root, labels, it, k);
    root.append(labels);
    if (this.mode === "draw") this.drawDraft(root, k);
    const sel = this.sel && this.item(this.sel);
    if (sel && !sel.fixed && sel.type !== "pin") this.drawHandles(root, sel, k);
    if (this.snapMark) root.append(el("circle", { cx: this.snapMark[0], cy: this.snapMark[1], r: 10 * k, fill: "none", stroke: "#ffd60a", "stroke-width": 2.5, "vector-effect": "non-scaling-stroke" }));
  }

  drawGrid(root, k) {
    const u = this.u(), [x0, ny1, w, hh] = this.vb, y1 = -ny1, y0 = y1 - hh;
    const spanU = Math.max(w, hh) / u;
    const step = [1, 2, 5, 10, 20, 25, 50, 100, 200, 500].find((s) => spanU / s <= 12) || 1000;
    const g = el("g", { opacity: 0.25 });
    for (let x = Math.ceil(x0 / u / step) * step; x * u <= x0 + w; x += step)
      g.append(el("line", { x1: x * u, y1: y0, x2: x * u, y2: y1, stroke: "#fff", "stroke-width": 1, "vector-effect": "non-scaling-stroke" }));
    for (let y = Math.ceil(y0 / u / step) * step; y * u <= y1; y += step)
      g.append(el("line", { x1: x0, y1: y * u, x2: x0 + w, y2: y * u, stroke: "#fff", "stroke-width": 1, "vector-effect": "non-scaling-stroke" }));
    root.append(g);
    const lab = el("g", { opacity: 0.8 });
    for (let x = Math.ceil(x0 / u / step) * step; x * u <= x0 + w; x += step) lab.append(text([x * u + 3 * k, y0 + 9 * k], `${x}`, 11 * k, "#fff", "start"));
    for (let y = Math.ceil(y0 / u / step) * step; y * u <= y1; y += step) lab.append(text([x0 + 4 * k, y * u + 7 * k], `${y}`, 11 * k, "#fff", "start"));
    lab.append(text([x0 + w - 6 * k, y1 - 12 * k], `grid ${step} ${this.units()}`, 11 * k, "#fff", "end"));
    root.append(lab);
  }

  drawItem(root, labels, it, k) {
    if (!it) return;
    const isSel = it.key === this.sel, isHover = it.key === this.hover, mine = this.inStep(it);
    const col = it.color;
    const sw = isSel ? 3 : isHover ? 3 : mine ? 1.8 : 1;
    let fillOp = isSel ? 0.28 : isHover ? 0.24 : mine ? 0.1 : 0.03;
    if (it.type === "boundary") fillOp = isSel ? 0.06 : 0;  // the whole lot: tinting it hides the photo
    const g = el("g", { opacity: mine || isSel || isHover ? 1 : 0.5 });
    if (isSel || isHover) g.setAttribute("filter", "drop-shadow(0 0 2px rgba(0,0,0,.9))");
    for (const geo of it.geoms) {
      if (it.type === "pin") { g.append(pinShape(geo.pts[0], k, col, isSel || isHover)); continue; }
      if (it.type === "photo") { g.append(cameraShape(it.photo, k, col, isSel || isHover)); continue; }
      if (it.type === "origin") {
        g.append(el("circle", { cx: 0, cy: 0, r: 7 * k, fill: "none", stroke: col, "stroke-width": 2, "vector-effect": "non-scaling-stroke" }),
          el("path", { d: `M${-12 * k} 0H${12 * k}M0 ${-12 * k}V${12 * k}`, stroke: col, "stroke-width": 1.5, "vector-effect": "non-scaling-stroke" }),
          northArrow(this.work.location?.north_offset_deg || 0, k));
        continue;
      }
      let shape;
      if (geo.type === "polygon") shape = el("polygon", { points: ptsAttr(geo.pts), fill: col, "fill-opacity": fillOp });
      else if (geo.type === "line") shape = el("polyline", { points: ptsAttr(geo.pts), fill: "none", "stroke-linecap": "round", "stroke-linejoin": "round" });
      else shape = el("circle", { cx: geo.pts[0][0], cy: geo.pts[0][1], r: Math.max((it.size || 0.8) / 2, 4 * k), fill: col, "fill-opacity": fillOp + 0.1 });
      shape.setAttribute("stroke", col);
      shape.setAttribute("stroke-width", geo.type === "line" ? sw + 1.5 : sw);
      if (it.type === "boundary" || it.type === "inv") shape.setAttribute("stroke-dasharray", it.type === "boundary" ? "9 5" : "6 4");
      shape.setAttribute("vector-effect", "non-scaling-stroke");
      g.append(shape);
    }
    for (const m of it.members || []) g.append(el("circle", { cx: m.x, cy: m.y, r: m.r, fill: col, "fill-opacity": fillOp,
      stroke: col, "stroke-width": 0.8, "vector-effect": "non-scaling-stroke" }));
    root.append(g);
    if (it.type === "pin" || it.type === "origin") return;
    const g0 = it.geoms[0];
    const at = g0.type === "polygon" ? centroid(g0.pts) : g0.type === "line" ? g0.pts[Math.floor(g0.pts.length / 2)] : g0.pts[0];
    const sizePx = Math.sqrt(geomSize(it)) / k;
    const show = isSel || isHover || (mine && (this.labels === "all" || (this.labels === "smart" && sizePx > it.label.length * 7.5)));
    if (show) labels.append(pillText([at[0], at[1] + (g0.type === "point" ? (it.size || 1) / 2 + 14 * k : 0)], it.label, 12 * k, k, isSel));
  }

  drawHandles(root, it, k) {
    const g = el("g", {});
    it.geoms.forEach((geo, gi) => {
      if (geo.type === "point") {
        if (SIZE_KEY[it.kind] && it.size) {
          const c = geo.pts[0];
          g.append(el("circle", { cx: c[0], cy: c[1], r: it.size / 2, fill: "none", stroke: "#ffd60a", "stroke-width": 1.5, "stroke-dasharray": "4 3", "vector-effect": "non-scaling-stroke" }),
            el("circle", { cx: c[0] + it.size / 2, cy: c[1], r: 7.5 * k, fill: "#ffd60a", stroke: "#111", "stroke-width": 1.5, "vector-effect": "non-scaling-stroke" }));
        }
        return;
      }
      const n = geo.pts.length, closed = geo.type === "polygon";
      for (let i = 0; i < (closed ? n : n - 1); i++) {
        const a = geo.pts[i], b = geo.pts[(i + 1) % n], L = dist(a, b);
        if (L / k > 70) g.append(pillText([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], fmtLen(L, this.u(), this.units()), 10 * k, k, false, 0.7));
      }
      geo.pts.forEach((p, i) => {
        const selV = this.selVertex && this.selVertex.gi === gi && this.selVertex.i === i;
        g.append(el("circle", { cx: p[0], cy: p[1], r: (selV ? 8.5 : 7) * k, fill: selV ? "#ffd60a" : "#fff", stroke: "#111", "stroke-width": 1.8, "vector-effect": "non-scaling-stroke" }));
      });
    });
    root.append(g);
  }

  drawDraft(root, k) {
    const d = this.draft;
    if (!d) return;
    const col = COLORS[d.kind] || "#fff";
    const pts = [...d.pts];
    if (this.draftCursor && (d.shape === "poly" || d.shape === "line") && pts.length) pts.push(this.draftCursor);
    if (pts.length > 1) {
      const shape = d.shape === "line" ? el("polyline", { points: ptsAttr(pts), fill: "none" }) : el("polygon", { points: ptsAttr(pts), fill: col, "fill-opacity": 0.2 });
      shape.setAttribute("stroke", col); shape.setAttribute("stroke-width", 2.5); shape.setAttribute("vector-effect", "non-scaling-stroke"); shape.setAttribute("stroke-dasharray", "7 4");
      root.append(shape);
      if (pts.length >= 2) {
        const a = pts[pts.length - 2], b = pts[pts.length - 1];
        if (d.shape !== "rect") root.append(pillText([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], fmtLen(dist(a, b), this.u(), this.units()), 11 * k, k, true));
      }
      if (d.shape === "rect" && pts.length === 4)
        root.append(pillText(centroid(pts), `${fmtLen(dist(pts[0], pts[1]), this.u(), this.units())} × ${fmtLen(dist(pts[1], pts[2]), this.u(), this.units())}`, 12 * k, k, true));
    }
    d.pts.forEach((p, i) => root.append(el("circle", { cx: p[0], cy: p[1], r: (i === 0 && d.shape === "poly" ? 10 : 5.5) * k,
      fill: i === 0 ? "#fff" : col, stroke: "#000", "stroke-width": 1.2, "vector-effect": "non-scaling-stroke" })));
    if (this.draftCursor) root.append(el("circle", { cx: this.draftCursor[0], cy: this.draftCursor[1], r: 4 * k, fill: col }));
  }
}

// ---------------------------------------------------------------- shapes, text
function el(tag, attrs) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null) e.setAttribute(k, v);
  return e;
}
function ptsAttr(list) { return list.map((p) => `${p[0]},${p[1]}`).join(" "); }
// Text lives inside the y-flipped group, so flip it back locally.
function text(at, s, size, color, anchor = "middle") {
  const t = el("text", { x: at[0], y: -at[1], "font-size": size, fill: color, "text-anchor": anchor, "dominant-baseline": "middle",
    transform: "scale(1 -1)", class: "smap-label", "stroke-width": size * 0.25 });
  t.textContent = s;
  return t;
}
function pillText(at, s, size, k, strong, opacity = 0.82) {
  const g = el("g", { transform: `translate(${at[0]} ${at[1]}) scale(1 -1)`, class: "smap-pill" });
  const w = s.length * size * 0.58 + 12 * k, hgt = size + 8 * k;
  g.append(el("rect", { x: -w / 2, y: -hgt / 2, width: w, height: hgt, rx: hgt / 2, fill: "#000", "fill-opacity": strong ? 0.92 : opacity }));
  const t = el("text", { x: 0, y: 0, "font-size": size, fill: "#fff", "text-anchor": "middle", "dominant-baseline": "central", "font-weight": strong ? 700 : 550 });
  t.textContent = s;
  g.append(t);
  return g;
}
function pinShape(p, k, col, hi) {
  const r = (hi ? 12 : 10) * k;
  const g = el("g", { transform: `translate(${p[0]} ${p[1]})` });
  g.append(el("path", { d: `M0 0 L${-r * 0.7} ${r * 1.4} A${r} ${r} 0 1 1 ${r * 0.7} ${r * 1.4} Z`, fill: col, stroke: "#fff", "stroke-width": 1.5, "vector-effect": "non-scaling-stroke" }));
  return g;
}
function cameraShape(p, k, col, hi) {
  const [x, y] = p.at, yaw = (p.yaw || 0) * Math.PI / 180, half = ((p.hfov || 60) / 2) * Math.PI / 180, L = 44 * k;
  const g = el("g", {});
  if (p.pitch > -80) {
    const a = [x + Math.sin(yaw - half) * L, y + Math.cos(yaw - half) * L], b = [x + Math.sin(yaw + half) * L, y + Math.cos(yaw + half) * L];
    g.append(el("path", { d: `M${x} ${y}L${a[0]} ${a[1]}L${b[0]} ${b[1]}Z`, fill: col, "fill-opacity": hi ? 0.45 : 0.25, stroke: col, "stroke-width": 1, "vector-effect": "non-scaling-stroke" }));
  }
  g.append(el("circle", { cx: x, cy: y, r: (hi ? 8 : 6) * k, fill: col, stroke: "#111", "stroke-width": 1.2, "vector-effect": "non-scaling-stroke" }));
  return g;
}
function northArrow(offsetDeg, k) {
  const a = -offsetDeg * Math.PI / 180, L = 32 * k, tip = [Math.sin(a) * L, Math.cos(a) * L];
  const g = el("g", {});
  g.append(el("line", { x1: 0, y1: 0, x2: tip[0], y2: tip[1], stroke: "#fff", "stroke-width": 2, "vector-effect": "non-scaling-stroke" }));
  g.append(text([tip[0] * 1.3, tip[1] * 1.3], "N", 12 * k, "#fff"));
  return g;
}

// ---------------------------------------------------------------- geometry helpers
function dist(a, b) { return Math.hypot(a[0] - b[0], a[1] - b[1]); }
function round3(v) { return Math.round(v * 1000) / 1000; }
function round2(v) { return Math.round(v * 100) / 100; }
function projectOnSeg(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy;
  const t = L2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) : 0;
  return [a[0] + t * dx, a[1] + t * dy];
}
function distToSeg(p, a, b) { return dist(p, projectOnSeg(p, a, b)); }
function distToPolyline(p, pts, closed) {
  let d = Infinity;
  for (let i = 0; i < (closed ? pts.length : pts.length - 1); i++) d = Math.min(d, distToSeg(p, pts[i], pts[(i + 1) % pts.length]));
  return d;
}
function pointInPoly(p, pts) {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [xi, yi] = pts[i], [xj, yj] = pts[j];
    if ((yi > p[1]) !== (yj > p[1]) && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function polyArea(pts) { let a = 0; for (let i = 0; i < pts.length; i++) { const [x0, y0] = pts[i], [x1, y1] = pts[(i + 1) % pts.length]; a += x0 * y1 - x1 * y0; } return a / 2; }
function pathLen(pts) { let L = 0; for (let i = 1; i < pts.length; i++) L += dist(pts[i - 1], pts[i]); return L; }
function centroid(list) {
  let a = 0, cx = 0, cy = 0;
  for (let i = 0; i < list.length; i++) { const [x0, y0] = list[i], [x1, y1] = list[(i + 1) % list.length]; const f = x0 * y1 - x1 * y0; a += f; cx += (x0 + x1) * f; cy += (y0 + y1) * f; }
  return Math.abs(a) < 1e-9 ? list[0] : [cx / (3 * a), cy / (3 * a)];
}
function centroidAll(geoms) { const p = geoms.flatMap((g) => g.pts); return [p.reduce((s, q) => s + q[0], 0) / p.length, p.reduce((s, q) => s + q[1], 0) / p.length]; }
function rot(q, c, deg) { const a = deg * Math.PI / 180, dx = q[0] - c[0], dy = q[1] - c[1]; return [round3(c[0] + dx * Math.cos(a) - dy * Math.sin(a)), round3(c[1] + dx * Math.sin(a) + dy * Math.cos(a))]; }
function geomSize(it) {
  let s = 0;
  for (const g of it.geoms) s = Math.max(s, g.type === "polygon" ? Math.abs(polyArea(g.pts)) : g.type === "line" ? 1 : Math.PI * ((it.size || 1) / 2) ** 2);
  return s;
}
function rectPts(a, b) { return [[a[0], a[1]], [b[0], a[1]], [b[0], b[1]], [a[0], b[1]]]; }
// Right angles everywhere: align to the shape's main direction, make each edge exactly horizontal or
// vertical in that frame (corners that become straight disappear), rotate back.
export function squareUp(pts) {
  let sx = 0, sy = 0;
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length], L = dist(a, b), ang = Math.atan2(b[1] - a[1], b[0] - a[0]) * 4;
    sx += Math.cos(ang) * L; sy += Math.sin(ang) * L;
  }
  const th = Math.atan2(sy, sx) / 4 * 180 / Math.PI, c = centroid(pts);
  const P = pts.map((q) => rot(q, c, -th)), n = P.length;
  const horiz = P.map((a, i) => { const b = P[(i + 1) % n]; return Math.abs(b[0] - a[0]) >= Math.abs(b[1] - a[1]); });
  const line = P.map((a, i) => { const b = P[(i + 1) % n]; return horiz[i] ? (a[1] + b[1]) / 2 : (a[0] + b[0]) / 2; });
  const out = [];
  for (let i = 0; i < n; i++) {
    const prev = (i - 1 + n) % n;
    if (horiz[prev] === horiz[i]) continue;
    out.push(horiz[i] ? [line[prev], line[i]] : [line[i], line[prev]]);
  }
  return out.length >= 3 ? out.map((q) => rot(q, c, th)) : pts;
}

// ---------------------------------------------------------------- formatting, data helpers
function fmtLen(m, u, units) { const v = m / u; return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units}`; }
function fmtArea(m2, u, units) { const v = m2 / (u * u); return `${v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(1)} sq ${units}`; }
function getPath(o, path) { for (const k of path) { if (o == null) return undefined; o = o[k]; } return o; }
function setPath(o, path, v) { for (const k of path.slice(0, -1)) { if (o[k] == null) o[k] = {}; o = o[k]; } o[path[path.length - 1]] = v; }
function coerce(s) { const t = String(s).trim(); return /^-?\d+(\.\d+)?$/.test(t) ? Number(t) : t; }
function speciesId(list, v) {
  const t = (v || "").trim().toLowerCase();
  const hit = (list || []).find((s) => s.id === t || s.common.toLowerCase() === t);
  return hit ? hit.id : (v || "").trim() || null;
}
// New elements' starting properties, in the project's units.
function defaultsFor(kind, mPerUnit) {
  const u = (m) => Math.round((m / mPerUnit) * 10) / 10;
  return { paving: { material: "concrete" }, bed: { surface: "mulch" }, lawn: {}, path: { width: u(1.2), material: "concrete" },
    structure: { wall_height: u(2.7), roof: { type: "gable", pitch: "6/12" } }, deck: { height: u(0.6) },
    fence: { height: u(1.8), style: "privacy" }, wall: { height: u(0.6), material: "block" }, light: { type: "path" },
    plant: { species: "acer_rubrum", spread: u(4), height: u(6), existing: true }, boulder: { size: u(0.8) },
    plant_group: { species: "buxus", spacing: u(0.9) }, plant_row: { species: "buxus", spacing: u(0.6) } }[kind] || {};
}
