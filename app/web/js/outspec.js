// "Outputs to make": the one section shared by the input and output pages. It edits a single
// record on the server (outputs.json), so both instances always show the same thing.
// It's the only part of the input page that is never frozen.
import { api, enc } from "./api.js";
import { explain, h, icon, toast } from "./ui.js";

export function outSpecSection(S, A, where) {
  const spec = S.node.output_spec;
  const types = S.app.output_types;
  const selected = new Set(spec.selected);
  const PRIMARY = ["stills", "plan_check", "web_walkthrough"];
  const isPrimary = (t) => (t.primary ?? PRIMARY.includes(t.key));
  const primary = types.filter(isPrimary);
  const advanced = types.filter((t) => !isPrimary(t));
  const advSelected = advanced.filter((t) => selected.has(t.key)).length;
  const advOpen = !!S.settings.window.advanced_outputs;
  const toggleAdvanced = () => {
    S.settings.window.advanced_outputs = !advOpen;  // remembered per user, shared by both pages
    A.render();
    A.saveSettings({ window: { advanced_outputs: !advOpen } }).catch(() => {});
  };
  const chip = (t) => h("button", { class: `otype ${selected.has(t.key) ? "on" : ""}`, "aria-pressed": selected.has(t.key),
    title: t.explain, onclick: () => toggle(t.key) },
    h("span", { class: "box" }, selected.has(t.key) ? icon("check") : null), t.label);
  const save = async (patch) => {
    try {
      const r = await api.put(`/api/nodes/${enc(S.nodeId)}/output-spec`, patch);
      S.node.output_spec = r.output_spec;
      A.render();
    } catch (e) { toast(e.message, "error"); }
  };
  const toggle = (key) => {
    const next = selected.has(key) ? spec.selected.filter((k) => k !== key) : [...spec.selected, key];
    save({ selected: next });
  };
  const input = h("textarea", { rows: 2, placeholder: "e.g. “a view from the kitchen window looking at the patio”, or “a printable poster of the backyard”" });
  const addCustom = async () => {
    const text = input.value.trim();
    if (!text) return;
    await save({ add_custom: text });
    toast(where === "output" ? "Request added. Send it to " + S.app.ai_name + " below." : "Request added. It goes with your next Send.");
  };
  const pending = spec.custom.filter((c) => c.status === "pending");
  const sendRequests = async () => {
    try {
      await api.post(`/api/nodes/${enc(S.nodeId)}/send`, { kind: "requests" });
      toast(`Sent ${pending.length} request(s) to ${S.app.ai_name}`);
      A.refreshNode().then(A.render);
    } catch (e) { toast(e.message, "error"); }
  };
  const statusChip = (c) => ({
    pending: h("span", { class: "chip" }, "waiting to send"),
    in_progress: h("span", { class: "chip warn" }, `${S.app.ai_name} is working on it`),
    accepted: h("span", { class: "chip ok" }, "accepted"),
    declined: h("span", { class: "chip danger" }, "not possible"),
  }[c.status] || h("span", { class: "chip" }, c.status));

  return h("div", { class: "section", id: `outspec-${where}` },
    h("h2", {}, icon("sparkle"), "Outputs to make", h("span", { class: "count" }, `${selected.size} selected`)),
    explain([
      "Choose what gets made from the 3D model. This section is the same on the input and output pages, and it's ",
      "never frozen: change it any time. Turning a type off only hides its outputs; nothing is deleted, and they come ",
      "back when you turn it on again.",
    ].join("")),
    h("div", { class: "otypes", role: "group", "aria-label": "Main outputs" }, primary.map(chip)),
    h("div", { class: `advanced ${advOpen ? "open" : ""}` },
      h("button", { class: "advanced-head", "aria-expanded": advOpen, onclick: toggleAdvanced,
        title: advOpen ? "Hide advanced outputs" : "Show advanced outputs" },
        icon(advOpen ? "down" : "chevron"), h("b", {}, "Advanced outputs"),
        h("span", { class: "muted small" }, advSelected ? `${advSelected} selected` : "videos, seasons, growth, panoramas, report…")),
      advOpen ? h("div", { class: "advanced-body" },
        explain("Extra outputs made from the same model. Videos and multi-season or multi-year sets take longer to make."),
        h("div", { class: "otypes", role: "group", "aria-label": "Advanced outputs" }, advanced.map(chip))) : null),
    h("div", { class: "row", style: { marginTop: "12px" } },
      h("label", { class: "field", style: { maxWidth: "260px" } }, h("span", {}, "Detail level"),
        h("select", { onchange: (e) => save({ detail: e.target.value }) },
          [["draft", "Draft: fastest, for checking layout"], ["preview", "Preview: good everyday quality"],
           ["final", "Final: presentation quality, slowest"]].map(([v, l]) =>
            h("option", { value: v, selected: spec.detail === v }, l))))),
    h("h3", { style: { margin: "16px 0 4px", fontSize: ".95rem" } }, "Request something else"),
    explain(`Describe any other output you'd like. ${S.app.ai_name} adds what the design needs to make it ` +
      "(usually a new camera view), or tells you why it isn't possible."),
    h("div", { class: "row" }, input,
      h("button", { class: "btn", onclick: addCustom }, icon("plus"), "Add request"),
      where === "output" && pending.length ? h("button", { class: "btn primary", onclick: sendRequests },
        icon("send"), `Send ${pending.length} request${pending.length > 1 ? "s" : ""} to ${S.app.ai_name}`) : null),
    spec.custom.length ? h("div", { class: "custom-list" }, spec.custom.slice().reverse().map((c) =>
      h("div", { class: "custom" },
        h("div", { style: { flex: 1 } }, h("div", {}, c.text), c.message ? h("div", { class: "msg" }, c.message) : null),
        statusChip(c),
        c.status === "pending" ? h("button", { class: "btn ghost icon", title: "Remove request",
          onclick: () => save({ remove_custom: c.id }) }, icon("x")) : null))) : null,
  );
}
