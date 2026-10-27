// "Build the environment": the base design is worked out step by step (location → ground → boundary →
// features → surfaces → building outlines → building details → fences & walls → plants), each step
// reviewed on the map before the next. A step can be approved, adjusted on the map, redone with notes
// and pinned comments, or accepted as "best judgement for now" and revisited any time.
import { api, enc } from "./api.js";
import { CONTINUE_TITLE, continueRound } from "./jobs.js";
import { siteMap } from "./mapview.js";
import { explain, h, icon, lightbox, toast } from "./ui.js";

const STATUS = {
  pending: ["Not started", ""], working: ["Working…", "warn"], review: ["Waiting for your review", "accent"],
  approved: ["Approved", "ok"], provisional: ["Best judgement for now", "provisional"],
};
const notes = new Map(); // node:step → what the user typed (kept across re-renders)

export function stepsSection(S, A) {
  const N = S.node;
  const P = N.steps;
  if (!P || !P.enabled) return null;
  const url = `/api/nodes/${enc(S.nodeId)}`;
  const ai = S.app.ai_name;
  const rounds = N.inputs.rounds;
  const started = P.steps.some((s) => s.status !== "pending") || rounds.some((r) => r.kind === "step");
  const legacy = !started && rounds.some((r) => r.status === "done");
  const settled = P.steps.filter((s) => ["approved", "provisional"].includes(s.status)).length;
  S.stepSel = S.stepSel || {};
  const pick = () => {
    const want = S.stepSel[S.nodeId];
    if (want && P.steps.some((s) => s.key === want)) return want;
    return P.working?.step || P.steps.find((s) => s.status === "review")?.key || P.steps.find((s) => s.recheck)?.key
      || P.current || P.steps[P.steps.length - 1].key;
  };
  const selKey = pick();
  const sel = P.steps.find((s) => s.key === selKey);
  const idx = P.steps.indexOf(sel);
  const aiBusy = N.busy.some((j) => j.kind === "ai_round");
  const working = P.working;

  const post = async (path, body = {}, msg) => {
    try { await api.post(`${url}/steps${path}`, body); if (msg) toast(msg); await A.refreshNode(); A.render(); }
    catch (e) { toast(e.message, "error"); }
  };

  // ---------------------------------------------------------------- the stepper
  const stepper = h("ol", { class: "stepper" }, P.steps.map((s, i) => {
    const [label, cls] = STATUS[s.status] || ["", ""];
    const isWorking = working?.step === s.key;
    return h("li", { class: `stp ${s.status} ${s.key === selKey ? "sel" : ""} ${s.recheck ? "recheck" : ""}`,
      title: `${s.label}: ${isWorking ? "working" : label}${s.recheck ? " — needs a recheck" : ""}`,
      onclick: () => { S.stepSel[S.nodeId] = s.key; A.render(); } },
      h("span", { class: `stp-dot ${cls}` }, s.status === "approved" ? icon("check") : isWorking ? icon("refresh") : String(i + 1)),
      h("span", { class: "stp-label" }, s.short),
      s.recheck ? h("span", { class: "stp-flag", title: "Something earlier changed" }, "!") : null);
  }));

  // ---------------------------------------------------------------- the selected step
  const [stLabel, stCls] = STATUS[sel.status] || ["", ""];
  const isWorking = working?.step === sel.key;
  const key = `${S.nodeId}:${sel.key}`;
  const noteBox = h("textarea", { rows: 2, placeholder: `What should change in “${sel.label}”? (optional — pinned map comments are sent too)`,
    oninput: (e) => notes.set(key, e.target.value) }, notes.get(key) || "");
  const questions = sel.questions || [];
  const answers = {};

  const qForm = questions.length ? h("div", { class: "card", style: { marginTop: "10px" } },
    h("b", {}, `${ai} asks about this step`),
    questions.map((q, i) => {
      const ta = h("textarea", { rows: 2, placeholder: "Your answer… (leave empty to let it use its best judgement)",
        oninput: (e) => { answers[q.id] = e.target.value.trim() || null; } });
      return h("div", { style: { marginTop: "8px" } }, h("div", { style: { fontWeight: 600 } }, `${i + 1}. ${q.text}`),
        q.why ? h("div", { class: "muted small" }, q.why) : null,
        q.default ? h("div", { class: "muted small" }, `If you leave it: ${q.default}`) : null, ta);
    }),
    h("div", { class: "row", style: { marginTop: "8px" } },
      h("button", { class: "btn", disabled: aiBusy, onclick: () => post(`/${sel.key}/redo`, { note: noteBox.value, answers },
        `Redoing ${sel.label} with your answers…`) }, icon("send"), "Send answers and redo this step"),
      h("button", { class: "btn ghost", onclick: () => post(`/${sel.key}/judgement`, { answers: {} }) }, "Keep it as is"))) : null;

  const actions = [];
  if (sel.status === "pending") {
    actions.push(h("button", { class: "btn primary", disabled: aiBusy || !rounds.length, onclick: () => post(`/${sel.key}/redo`, { note: noteBox.value },
      `${ai} is working on ${sel.label}…`) }, icon("sparkle"), "Run this step"));
  } else if (!isWorking) {
    if (sel.status !== "approved" || sel.recheck)
      actions.push(h("button", { class: "btn primary", disabled: aiBusy, onclick: () => post(`/${sel.key}/approve`, {},
        "Approved") }, icon("check"), sel.recheck ? "Still right — approve" : "Approve"));
    if (sel.status === "review")
      actions.push(h("button", { class: "btn", disabled: aiBusy, onclick: () => post(`/${sel.key}/judgement`, {}, "Accepted for now; you can come back to it") },
        icon("sparkle"), "Use best judgement for now"));
    actions.push(h("button", { class: "btn", disabled: aiBusy, title: "The AI redoes just this step, with your note and the comments pinned on the map",
      onclick: () => post(`/${sel.key}/redo`, { note: noteBox.value }, `Redoing ${sel.label}…`) }, icon("refresh"), "Redo this step"));
  }
  const round = sel.round && rounds.find((r) => r.n === sel.round);

  const panel = h("div", { class: "step-panel" },
    h("div", { class: "step-head" },
      h("div", { style: { flex: 1, minWidth: 0 } },
        h("div", { class: "muted small" }, `Step ${idx + 1} of ${P.steps.length}`),
        h("h3", {}, sel.label)),
      isWorking ? h("span", { class: "chip warn" }, working.status === "awaiting_answers" ? "has questions for you" : "working…")
        : h("span", { class: `chip ${stCls}` }, stLabel + (sel.status === "provisional" && sel.by === "imported" ? " (from the earlier design)" : ""))),
    explain(sel.explain),
    sel.recheck ? h("div", { class: "callout warn" }, icon("info"),
      h("div", {}, `Changed since this was settled: ${sel.recheck_because.join(", ")}. Check that this step still fits, then approve it again or redo it.`)) : null,
    sel.revisit?.length ? h("div", { class: "callout" }, icon("info"),
      h("div", {}, `${ai} thinks these earlier steps need another look: ${sel.revisit.map((k) => P.steps.find((s) => s.key === k)?.label || k).join(", ")}.`)) : null,
    sel.error ? h("div", { class: "callout danger" }, icon("info"), h("div", {}, sel.error)) : null,
    isWorking ? h("div", { class: "muted" }, N.busy.find((j) => j.kind === "ai_round")?.step || "Starting…") : null,
    sel.summary ? h("p", {}, sel.summary) : null,
    sel.notes?.length ? h("details", { class: "fold" }, h("summary", {}, `Notes (${sel.notes.length})`),
      h("ul", {}, sel.notes.map((n) => h("li", {}, n)))) : null,
    siteMap(S, A, { step: sel.key, height: 600 }),
    h("div", { class: "row small muted", style: { marginTop: "4px" } },
      sel.map_url ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); lightbox({ title: `${ai}'s check image: ${sel.label}`, kind: "image", url: sel.map_url, download: sel.map_url }); } },
        `${ai}'s check image`) : null,
      round?.session_id ? h("a", { href: "#", title: CONTINUE_TITLE, onclick: (e) => { e.preventDefault(); continueRound(S.nodeId, round.n); } }, "Continue in Claude Code") : null),
    qForm,
    !isWorking && sel.status !== "pending" ? noteBox : null,
    sel.status === "pending" && !isWorking ? noteBox : null,
    h("div", { class: "row", style: { marginTop: "10px" } }, actions,
      h("span", { style: { flex: 1 } }),
      idx > 0 ? h("button", { class: "btn ghost", onclick: () => { S.stepSel[S.nodeId] = P.steps[idx - 1].key; A.render(); } }, "‹ Previous") : null,
      idx < P.steps.length - 1 ? h("button", { class: "btn ghost", onclick: () => { S.stepSel[S.nodeId] = P.steps[idx + 1].key; A.render(); } }, "Next ›") : null));

  return h("div", { class: "section steps" },
    h("h2", {}, icon("map"), "Build the environment",
      h("span", { class: "count" }, `${settled} of ${P.steps.length} steps settled`),
      h("span", { style: { flex: 1 } }),
      h("label", { class: "auto-toggle", title: `${ai} accepts each step itself (marked “best judgement for now”) and goes straight on; questions get its default answers. You can review and change any step later.` },
        h("input", { type: "checkbox", checked: P.auto, onchange: (e) => post("/auto", { on: e.target.checked },
          e.target.checked ? `${ai} will use its best judgement for the remaining steps` : "Steps will pause for your review again") }),
        h("span", {}, "Best judgement for all remaining steps"))),
    explain(`The existing property is worked out one kind of thing at a time, in this order. After each step ${ai} pauses so you can check it on the map: ` +
      "approve it, fix it by dragging outlines, pin comments where something's off and have it redone, or accept it as “best judgement for now” and come back later. " +
      "Changing an earlier step flags the later ones for a recheck."),
    legacy ? h("div", { class: "callout" }, icon("info"), h("div", {},
      h("div", {}, `This design was made before the steps existed, all at once. Load it into the steps: each step that has content starts as “best judgement for now”, so you can review and fix them one at a time. Open questions from the last round move to the step they're about.`),
      h("button", { class: "btn primary", style: { marginTop: "8px" }, onclick: () => post("/import", {}, "Loaded into the steps") }, "Load the current design into the steps"))) : null,
    stepper, panel);
}
