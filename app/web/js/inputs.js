// Input page: sent (frozen) rounds, new inputs, outputs to make, the design file, and Send to AI.
import { api, enc } from "./api.js";
import { CONTINUE_TITLE, continueRound as continueIn } from "./jobs.js";
import { outSpecSection } from "./outspec.js";
import { stepsSection } from "./steps.js";
import { bytes, confirmBox, explain, fmtTime, h, icon, lightbox, modal, promptText, toast } from "./ui.js";

const IMG = /\.(png|jpe?g|webp|gif|bmp|tiff?)$/i;

export function renderInput(el, S, A) {
  const N = S.node;
  const types = S.app.input_types;
  const d = N.inputs;
  const drafts = d.items.filter((i) => i.round == null);
  const frozen = d.items.filter((i) => i.round != null);
  const rounds = d.rounds;
  const lastRound = rounds[rounds.length - 1];
  const working = lastRound && ["queued", "running"].includes(lastRound.status);
  const aiJob = N.busy.find((j) => j.kind === "ai_round");
  const isVariant = !!N.variant;
  const nodeUrl = `/api/nodes/${enc(S.nodeId)}`;

  // ---------------------------------------------------------- input cards
  const fileChip = (it, f, editable) => h("span", { class: "file" },
    f.url && IMG.test(f.name) ? h("img", { src: f.url, alt: "", onclick: () => {
      // Browse all images in this input (← / →), starting at the one clicked.
      const imgs = it.files.filter((x) => x.url && IMG.test(x.name));
      lightbox({ items: imgs.map((x) => ({ title: `${it.title}: ${x.name}`, kind: "image", url: x.url, download: x.url })),
        index: imgs.indexOf(f) });
    } })
      : icon("file"),
    f.url ? h("a", { href: f.url, target: "_blank" }, f.name) : f.name,
    h("span", { class: "muted" }, f.size ? bytes(f.size) : ""),
    editable ? h("span", { class: "x", title: "Remove file", onclick: async () => {
      await api.del(`${nodeUrl}/inputs/${it.id}/files?name=${encodeURIComponent(f.name)}`);
      await A.refreshNode(); A.render();
    } }, icon("x")) : null);

  const upload = async (it, files) => {
    for (const file of files) {
      toast(`Uploading ${file.name}…`);
      try { await api.upload(`${nodeUrl}/inputs/${it.id}/files?name=${encodeURIComponent(file.name)}`, file); }
      catch (e) { toast(`${file.name}: ${e.message}`, "error"); }
    }
    await A.refreshNode(); A.render();
  };

  const draftCard = (it) => {
    const t = types[it.type] || { label: it.type };
    const patch = debounce((p) => api.patch(`${nodeUrl}/inputs/${it.id}`, p).catch((e) => toast(e.message, "error")), 500);
    const fileInput = h("input", { type: "file", multiple: true, accept: t.accepts || "", hidden: true,
      onchange: (e) => upload(it, e.target.files) });
    const drop = h("div", { class: "drop", tabindex: 0, onclick: () => fileInput.click(),
      ondragover: (e) => { e.preventDefault(); drop.classList.add("over"); },
      ondragleave: () => drop.classList.remove("over"),
      ondrop: (e) => { e.preventDefault(); drop.classList.remove("over"); upload(it, e.dataTransfer.files); } },
      icon("upload"), " Drop files here or click to choose", fileInput);
    const card = h("div", { class: "card", "data-ctx": "input" },
      h("div", { class: "card-head" },
        h("span", { class: "chip accent" }, t.label),
        it.origin ? h("span", { class: "chip", title: "Copied from the parent; editable in this variant" }, "from parent") : null,
        it.edited ? h("span", { class: "chip warn" }, "edited") : null,
        h("input", { type: "text", value: it.title, "aria-label": "Title", style: { flex: 1 },
          oninput: (e) => patch({ title: e.target.value }) }),
        h("button", { class: "btn ghost icon", title: "Remove this input", onclick: () => removeItem(it) }, icon("trash"))),
      explain(t.explain || ""),
      t.text ? h("textarea", { rows: ["lidar", "aerial"].includes(it.type) ? 2 : 4, placeholder: t.placeholder || (it.type === "location" ? "e.g. 123 Elm St, Columbus OH — north is up on the plan" : "Write it in your own words…"),
        oninput: (e) => patch({ text: e.target.value }) }, it.text || "") : null,
      t.accepts ? drop : null,
      t.folder ? h("label", { class: "field", style: { marginTop: "8px" } },
        h("span", {}, "…or a folder on this computer (for large photo sets)"),
        h("input", { type: "text", value: it.folder || "", placeholder: "/path/to/drone/photos",
          oninput: (e) => patch({ folder: e.target.value }) })) : null,
      it.files.length ? h("div", { class: "files" }, it.files.map((f) => fileChip(it, f, true))) : null);
    card.ctxItems = () => [{ label: "Remove this input", icon: "trash", onClick: () => removeItem(it) }];
    return card;
  };

  const frozenCard = (it) => {
    const t = types[it.type] || { label: it.type };
    return h("div", { class: "card frozen" },
      h("div", { class: "card-head" }, icon("lock"), h("span", { class: "chip" }, t.label),
        h("span", { class: "title" }, it.title), it.origin ? h("span", { class: "chip" }, "from parent") : null),
      it.text ? h("div", { style: { whiteSpace: "pre-wrap" } }, it.text) : null,
      it.folder ? h("div", { class: "mono" }, it.folder) : null,
      it.files.length ? h("div", { class: "files" }, it.files.map((f) => fileChip(it, f, false))) : null);
  };

  const removeItem = async (it) => {
    if ((it.text || it.files.length) && !(await confirmBox("Remove input?", `“${it.title}” and its files will be removed.`, "Remove", true))) return;
    try { await api.del(`${nodeUrl}/inputs/${it.id}`); await A.refreshNode(); A.render(); }
    catch (e) { toast(e.message, "error"); }
  };

  const addInput = async (type) => {
    const r = await api.post(`${nodeUrl}/inputs`, { type });
    await A.refreshNode(); A.render();
    setTimeout(() => document.querySelector(`#pane-input .card:last-of-type textarea, #pane-input .card:last-of-type .drop`)?.focus(), 50);
    return r;
  };

  // ---------------------------------------------------------- rounds (history)
  const statusChip = (r) => ({ queued: h("span", { class: "chip warn" }, "queued"),
    running: h("span", { class: "chip warn" }, "working…"), done: h("span", { class: "chip ok" }, "done"),
    failed: h("span", { class: "chip danger" }, "failed"), cancelled: h("span", { class: "chip" }, "cancelled"),
    interrupted: h("span", { class: "chip warn", title: "The app was closed before this round finished" }, "interrupted"),
    awaiting_answers: h("span", { class: "chip warn" }, "needs your answers") }[r.status]);

  const continueRound = (r) => continueIn(S.nodeId, r.n);

  const showRoundFile = async (r, what) => {
    const t = (await api.get(`${nodeUrl}/rounds/${r.n}/${what}`)).text;
    lightboxText(what === "prompt" ? `Round ${r.n}: what was sent` : `Round ${r.n}: agent log`, t);
  };

  const roundBlock = (r, open) => {
    const its = frozen.filter((i) => i.round === r.n);
    return h("details", { class: "card fold", open },
      h("summary", {}, h("span", { class: "round-head" },
        `Round ${r.n}`, statusChip(r), h("span", { class: "muted small" }, fmtTime(r.created)),
        r.kind === "requests" ? h("span", { class: "chip" }, "output requests") : null,
        h("span", { class: "muted small" }, `${its.length} input${its.length === 1 ? "" : "s"}`))),
      r.summary ? h("p", {}, r.summary) : null,
      r.error ? h("p", { style: { color: "var(--danger)" } }, r.error) : null,
      its.map(frozenCard),
      h("div", { class: "row", style: { marginTop: "8px" } },
        h("button", { class: "btn ghost", onclick: () => showRoundFile(r, "prompt") }, icon("file"), "What was sent"),
        h("button", { class: "btn ghost", onclick: () => showRoundFile(r, "log") }, icon("text"), "Agent log"),
        r.session_id ? h("button", { class: "btn", onclick: () => continueRound(r), title: CONTINUE_TITLE },
          icon("open"), "Continue in Claude Code") : null,
        ["failed", "cancelled", "interrupted"].includes(r.status) ? h("button", { class: "btn", onclick: async () => {
          try { await api.post(`${nodeUrl}/rounds/${r.n}/retry`); toast("Retrying…"); } catch (e) { toast(e.message, "error"); }
        } }, icon("refresh"), "Retry round") : null));
  };

  // ---------------------------------------------------------- questions from the AI
  const normQ = (qs) => (qs || []).map((q, i) => typeof q === "string" ? { id: `q${i + 1}`, text: q, why: "", default: "" } : q);
  const awaiting = rounds.find((r) => r.status === "awaiting_answers");
  const lastDone = [...rounds].reverse().find((r) => r.status === "done");
  const followUp = !awaiting && lastDone && lastDone === lastRound && (lastDone.questions || []).length
    && !lastDone.questions_answered ? lastDone : null;

  const questionForm = (r, before) => {
    const qs = normQ(r.questions);
    const key = `${S.nodeId}:${r.n}`;
    S.qa = S.qa || {};
    const st = S.qa[key] = S.qa[key] || Object.fromEntries(qs.map((q) => [q.id, { text: "", judge: false }]));
    const ready = () => qs.every((q) => st[q.id].judge || st[q.id].text.trim());
    const sendBtn = h("button", { class: "btn primary big", disabled: !ready(), onclick: async () => {
      const answers = Object.fromEntries(qs.map((q) => [q.id, st[q.id].judge ? null : st[q.id].text.trim()]));
      try {
        await api.post(`${nodeUrl}/rounds/${r.n}/answers`, { answers });
        delete S.qa[key];
        toast(before ? `Answers sent — ${S.app.ai_name} is building round ${r.n}` : `Answers sent as round ${r.n + 1}`);
        await A.refreshNode(); A.render();
      } catch (e) { toast(e.message, "error"); }
    } }, icon("send"), before ? "Send answers and build" : `Send answers (starts round ${r.n + 1})`);
    const refresh = () => { sendBtn.disabled = !ready(); };
    const allJudge = () => { qs.forEach((q) => { st[q.id].judge = true; }); A.render(); };
    return h("div", { class: "section", style: { borderColor: "var(--accent)" } },
      h("h2", {}, icon("info"), before ? `${S.app.ai_name} has questions before building` : `${S.app.ai_name} asks`,
        h("span", { class: "count" }, `round ${r.n}`)),
      explain(before
        ? `${S.app.ai_name} stopped before building the model because these answers would change it. Answer what you know; for the rest, choose “Exercise your best judgement” and it uses the assumption shown. Your answers are kept with this round's inputs.`
        : `Questions that came up while building round ${r.n}. Answering sends them as a new input and starts the next round.`),
      qs.map((q, i) => {
        const ta = h("textarea", { rows: 2, placeholder: "Your answer…", disabled: st[q.id].judge,
          oninput: (e) => { st[q.id].text = e.target.value; refresh(); } }, st[q.id].text);
        return h("div", { class: "card", style: { marginTop: "8px" } },
          h("div", { style: { fontWeight: 600 } }, `${i + 1}. ${q.text}`),
          q.why ? h("div", { class: "muted small", style: { margin: "2px 0 6px" } }, q.why) : null,
          ta,
          h("label", { class: "row small", style: { marginTop: "6px", gap: "6px", cursor: "pointer" } },
            h("input", { type: "checkbox", checked: st[q.id].judge, onchange: (e) => {
              st[q.id].judge = e.target.checked; ta.disabled = e.target.checked; refresh(); } }),
            h("span", {}, h("b", {}, "Exercise your best judgement"),
              q.default ? h("span", { class: "muted" }, ` — ${S.app.ai_name} will assume: ${q.default}`) : null)));
      }),
      h("div", { class: "row", style: { marginTop: "12px" } },
        sendBtn, h("button", { class: "btn", onclick: allJudge }, icon("sparkle"), "Exercise your best judgement on all")));
  };

  // ---------------------------------------------------------- review of the latest round
  const review = () => {
    const r = [...rounds].reverse().find((x) => x.status === "done");
    const errors = N.issues.filter((i) => i.level === "error");
    const warns = N.issues.filter((i) => i.level === "warning");
    if (!r && !N.check_image) return null;
    return h("div", { class: "section" },
      h("h2", {}, icon("check"), "Review", r ? h("span", { class: "count" }, `after round ${r.n}`) : null),
      explain(`Check what ${S.app.ai_name} built before making outputs. The plan check is the 3D model seen from directly ` +
        "above with your plan drawing laid over it at half opacity: shapes and plants that don't line up are mistakes " +
        "to correct with a new input."),
      r?.summary ? h("p", {}, r.summary) : null,
      r?.notes?.length ? h("div", {}, h("b", {}, "Notes"), h("ul", {}, r.notes.map((n) => h("li", {}, n)))) : null,
      r?.asked_questions?.length ? h("div", {}, h("b", {}, "Asked before building"),
        h("ul", {}, normQ(r.asked_questions).map((q) => h("li", {}, q.text,
          h("div", { class: "muted small" }, r.answers && r.answers[q.id] ? `Answer: ${r.answers[q.id]}` : "Answer: use your best judgement"))))) : null,
      r?.questions?.length && r.questions_answered ? h("div", {}, h("b", {}, `${S.app.ai_name} asked`),
        h("ul", {}, normQ(r.questions).map((q) => h("li", {}, q.text))),
        h("p", { class: "muted small" }, "Answered in the next round.")) : null,
      N.issues.length ? h("ul", { class: "issues" }, [...errors, ...warns].slice(0, 12).map((i) =>
        h("li", {}, h("span", { class: `chip ${i.level === "error" ? "danger" : "warn"}` }, i.level), h("span", {}, `${i.where}: ${i.message}`))))
        : h("p", { class: "muted small" }, "The design file validates cleanly."),
      N.check_image ? h("img", { class: "check-img", src: N.check_image, alt: "Plan check",
        onclick: () => lightbox({ items: reviewImages(), index: 0 }) }) : null,
      (N.photo_matches || []).length ? h("div", {},
        h("h3", { style: { margin: "14px 0 4px", fontSize: ".95rem" } }, "Photo-match check"),
        explain("The model rendered from each drone photo's exact camera, next to and over the photo. Red lines are the " +
          "model's edges: where they miss the real roofs, walls and trees, the model is off."),
        h("div", { class: "ocards" }, N.photo_matches.map((u, i) => h("button", { class: "ocover",
          style: { borderRadius: "var(--radius)", overflow: "hidden" }, title: "Open (← / → to browse)",
          onclick: () => lightbox({ items: reviewImages(), index: (N.check_image ? 1 : 0) + i }) },
          h("img", { src: u, alt: "Photo match", loading: "lazy" }))))) : null,
      h("div", { class: "row", style: { marginTop: "12px" } },
        h("button", { class: "btn primary", disabled: errors.length > 0 || N.busy.some((j) => j.kind === "generate"),
          onclick: generate }, icon("sparkle"), "Generate outputs"),
        h("button", { class: "btn", onclick: openBlender, title: "Open the 3D model in Blender to nudge things by hand" },
          icon("cube"), "Open in Blender"),
        h("button", { class: `btn ${N.unpulled_edits ? "primary" : ""}`, onclick: pull,
          title: "Carry hand edits made in Blender into the design" }, icon("pull"),
          N.unpulled_edits ? "Pull Blender edits (unsaved changes found)" : "Pull Blender edits"),
        r?.session_id ? h("button", { class: "btn", onclick: () => continueRound(r), title: CONTINUE_TITLE },
          icon("open"), "Continue in Claude Code") : null));
  };

  const reviewImages = () => [
    ...(N.check_image ? [{ title: "Plan check: model with plan overlay", kind: "image", url: N.check_image, download: N.check_image }] : []),
    ...(N.photo_matches || []).map((u) => ({ title: `Photo match: ${decodeURIComponent(u.split("/").pop().split(".png")[0])}`,
      kind: "image", url: u, download: u })),
  ];

  const generate = async () => {
    try { await api.post(`${nodeUrl}/generate`, {}); toast("Generating outputs…"); A.setTab("output"); }
    catch (e) { toast(e.message, "error"); }
  };
  const openBlender = async () => {
    try { await api.post(`${nodeUrl}/blender/open`); toast("Opening the model in Blender (building it first if needed)…"); }
    catch (e) { toast(e.message, "error"); }
  };
  const pull = async () => {
    try { await api.post(`${nodeUrl}/blender/pull`, {}); toast("Pulling hand edits…"); }
    catch (e) { toast(e.message, "error"); }
  };

  // ---------------------------------------------------------- design file
  const designSection = () => {
    const ta = h("textarea", { class: "code", spellcheck: "false" }, N.design);
    const vsel = h("select", { onchange: async (e) => {
      if (e.target.value === "") { ta.value = N.design; return; }
      ta.value = (await api.get(`${nodeUrl}/design/versions/${e.target.value}`)).text;
      toast("Showing an older version. Save to restore it.");
    } }, h("option", { value: "" }, "Current"),
      N.versions.slice().reverse().map((v) => h("option", { value: v.k }, `v${v.k} · ${v.source}${v.note ? " · " + v.note : ""} · ${fmtTime(v.time)}`)));
    const save = async () => {
      try {
        const r = await api.put(`${nodeUrl}/design`, { text: ta.value, note: "edited in the app" });
        const errs = r.issues.filter((i) => i.level === "error").length;
        toast(errs ? `Saved v${r.version.k} — ${errs} error(s) to fix` : `Saved v${r.version.k}`, errs ? "error" : "");
        await A.refreshNode(); A.render();
      } catch (e) { toast(e.message, "error"); }
    };
    return h("details", { class: "section fold" },
      h("summary", {}, icon("text"), `Design file (${N.design_name})`,
        h("span", { class: "count" }, N.versions.length ? `v${N.versions[N.versions.length - 1].k}` : "")),
      explain(isVariant
        ? "This variant's design file is an overlay: it lists only what differs from its parent, so fixes to the parent keep flowing in. It's written by the AI, editable here, and every output is made from it."
        : "The deterministic source of truth: exact positions and sizes of every element. It's written by the AI from your inputs, editable here, and updated by hand edits pulled from Blender. Every output is made from the model built from this file. Every saved version is kept."),
      h("div", { class: "row", style: { margin: "6px 0 8px" } },
        h("label", { class: "field", style: { flex: 1, minWidth: "260px" } }, h("span", {}, "Version"), vsel),
        h("button", { class: "btn", onclick: () => { ta.value = N.design; vsel.value = ""; } }, icon("refresh"), "Revert"),
        h("button", { class: "btn primary", onclick: save }, icon("check"), "Save")),
      ta);
  };

  // ---------------------------------------------------------- send bar
  const pendingReq = N.output_spec.custom.filter((c) => c.status === "pending").length;
  const hasContent = drafts.some((i) => (i.text || "").trim() || i.files.length || (i.folder || "").trim() || ["lidar", "aerial"].includes(i.type));
  const provider = S.app.providers[S.settings.ai.provider];
  let why = "";
  if (working) why = `${S.app.ai_name} is working on round ${lastRound.n}.`;
  else if (awaiting) why = `Answer ${S.app.ai_name}'s questions above (or let it use its best judgement) to continue round ${awaiting.n}.`;
  else if (!provider?.available) why = `${provider?.label || "The AI provider"} isn't ready: ${provider?.status}. See Settings → AI.`;
  else if (!drafts.length && !pendingReq) why = rounds.length ? "Add a new input or an output request to send another round." : "Add at least one input first.";
  else if (drafts.length && !hasContent) why = "Your new inputs are empty: add text or files.";
  const useSteps = !isVariant && N.steps?.enabled;
  const sendSteps = () => {
    const P = N.steps;
    const firstOpen = P.steps.find((s) => !["approved", "provisional"].includes(s.status));
    let from = (firstOpen || P.steps[0]).key;
    let auto = P.auto;
    const sel = h("select", { onchange: (e) => { from = e.target.value; } },
      P.steps.map((s, i) => h("option", { value: s.key, selected: s.key === from }, `${i + 1}. ${s.label}`)));
    modal({ title: `Send to ${S.app.ai_name}`, body: h("div", {},
      h("p", {}, `${drafts.length} new input${drafts.length === 1 ? "" : "s"}${pendingReq ? ` and ${pendingReq} output request(s)` : ""} will be sent and then frozen. ` +
        "To change them later you'll start a variant."),
      h("label", { class: "field" }, h("span", {}, "Work through the steps starting at"), sel,
        h("span", { class: "muted small" }, "Steps already settled stay as they are unless they come after this one; then they're flagged for a recheck if something they depend on changes.")),
      h("label", { style: { display: "flex", alignItems: "flex-start", marginTop: "12px", gap: "8px", cursor: "pointer" } },
        h("input", { type: "checkbox", checked: auto, style: { marginTop: "4px" }, onchange: (e) => { auto = e.target.checked; } }),
        h("span", {}, h("b", {}, "Use best judgement for all steps"), h("div", { class: "muted small" },
          "Don't pause for reviews: every step is accepted as “best judgement for now” and can be reviewed later.")))),
      actions: [{ label: "Cancel" }, { label: `Send to ${S.app.ai_name}`, primary: true, onClick: async () => {
        try { await api.post(`${nodeUrl}/steps/start`, { from, auto }); toast(`Sent to ${S.app.ai_name}`); await A.refreshNode(); A.render(); }
        catch (e) { toast(e.message, "error"); }
      } }] });
  };
  const send = async () => {
    if (useSteps && drafts.length) { sendSteps(); return; }
    if (drafts.length && !(await confirmBox(`Send to ${S.app.ai_name}?`,
      `${drafts.length} new input${drafts.length === 1 ? "" : "s"}${pendingReq ? ` and ${pendingReq} output request(s)` : ""} will be sent and then frozen. ` +
      (drafts.length === 1 ? "To change it" : "To change them") + " later you'll start a variant.", `Send to ${S.app.ai_name}`))) return;
    try { await api.post(`${nodeUrl}/send`, { kind: "design" }); toast(`Sent to ${S.app.ai_name}`); await A.refreshNode(); A.render(); }
    catch (e) { toast(e.message, "error"); }
  };

  // ---------------------------------------------------------- assemble
  el.replaceChildren(h("div", { class: "pane-inner" },
    h("div", { class: "page-head" },
      h("div", { style: { flex: 1 } },
        h("h1", { title: "Click to rename", style: { cursor: "text" }, onclick: async () => {
          const t = await promptText("Rename", "Name", N.meta.title); if (t) { await api.patch(nodeUrl, { title: t }); await A.refreshTree(); await A.refreshNode(); A.render(); }
        } }, N.meta.title),
        h("div", { class: "sub" }, isVariant ? `Child variant of ${N.parent_title}` : "Base design", " · Input page")),
      h("button", { class: "btn", onclick: () => A.setTab("output") }, "Output page", icon("chevron"))),
    explain("Everything the design is made from. Inputs you've sent are frozen and kept as history; " +
      "new inputs are added on top. To change or remove a sent input, start a child variant: its copies are editable."),
    working ? h("div", { class: "section", style: { borderColor: "var(--warn)" } },
      h("h2", {}, icon("jobs"), `${S.app.ai_name} is working on round ${lastRound.n}`),
      h("p", { class: "muted" }, aiJob?.step || "Starting…"),
      h("pre", { class: "mono", style: { whiteSpace: "pre-wrap", maxHeight: "160px", overflow: "auto" } }, (aiJob?.log_tail || []).join("\n")),
      aiJob ? h("button", { class: "btn", onclick: () => api.post(`/api/jobs/${aiJob.id}/cancel`) }, icon("stop"), "Cancel") : null) : null,
    awaiting ? questionForm(awaiting, true) : null,
    useSteps ? stepsSection(S, A) : null,
    review(),
    followUp ? questionForm(followUp, false) : null,
    rounds.length ? h("div", { class: "section" },
      h("h2", {}, icon("lock"), "Sent inputs", h("span", { class: "count" }, `${rounds.length} round${rounds.length === 1 ? "" : "s"}`)),
      explain("The history of what was sent, round by round. Frozen: it's the record of how the design came to be."),
      rounds.slice().reverse().map((r, i) => roundBlock(r, i === 0))) : null,
    h("div", { class: "section" },
      h("h2", {}, icon("plus"), rounds.length ? "New inputs" : "Inputs", h("span", { class: "count" }, drafts.length ? `${drafts.length}` : "")),
      explain(isVariant && !rounds.length
        ? "This variant starts with copies of its parent's inputs. Edit or delete any of them, or add new ones, then send to regenerate the variant's design."
        : "Add anything that describes the design: the landscaper's plan, photos, notes. Each one becomes part of the next round."),
      drafts.map(draftCard),
      h("div", { class: "type-grid", style: { marginTop: drafts.length ? "12px" : "0" } },
        Object.entries(types).map(([k, t]) => h("button", { class: "type-btn", onclick: () => addInput(k), title: t.explain },
          h("b", {}, "+ ", t.label), h("span", {}, t.explain.split(/(?<=\.)\s/)[0]))))),
    outSpecSection(S, A, "input"),
    designSection(),
    h("div", { class: "sendbar" }, h("div", { class: "sendbox" },
      h("div", { class: "why" },
        h("div", { class: "explain", style: { margin: 0, background: "transparent", padding: 0 } }, icon("info"),
          h("div", {}, `Uses AI (${provider?.label || "your provider"}) to write the design file: the deterministic source of truth `,
            "that every output is made from. It checks its work against your plan and reports back here. ",
            "What you send becomes frozen history.")),
        why ? h("div", { style: { color: "var(--warn)", marginTop: "4px" } }, why) : null),
      h("button", { class: "btn primary big", disabled: !!why, onclick: send }, icon("send"), `Send to ${S.app.ai_name}`))),
  ));
}

function lightboxText(title, text) {
  import("./ui.js").then(({ modal }) => modal({ title, wide: true,
    body: h("pre", { class: "mono", style: { whiteSpace: "pre-wrap", margin: 0 } }, text || "(empty)") }));
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
