// Jobs drawer: AI rounds, builds, renders, videos — with progress, log tails and cancel.
import { api } from "./api.js";
import { fmtTime, h, icon } from "./ui.js";

export function jobsBadge(n) {
  return h("span", { class: "jobs-badge" }, n);
}

export function renderJobs(S, A) {
  const el = document.getElementById("drawer");
  el.hidden = !S.drawerOpen;
  if (!S.drawerOpen) return;
  const jobs = [...S.jobs.values()].sort((a, b) => b.created - a.created).slice(0, 40);
  const statusChip = (j) => h("span", { class: `chip ${{ running: "warn", queued: "", done: "ok", failed: "danger" }[j.status] || ""}` }, j.status);
  el.replaceChildren(
    h("header", {}, icon("jobs"), "Jobs", h("span", { style: { flex: 1 } }),
      h("span", { class: "muted small" }, "Renders run one at a time; AI rounds run alongside"),
      h("button", { class: "btn ghost icon", onclick: () => { S.drawerOpen = false; renderJobs(S, A); } }, icon("x"))),
    h("div", { class: "list" }, jobs.length ? jobs.map((j) => h("div", { class: `job ${j.status}` },
      h("div", { class: "row" }, h("b", { style: { flex: 1 } }, j.title), statusChip(j)),
      h("div", { class: "muted small" }, [j.step, fmtTime(j.started || j.created)].filter(Boolean).join(" · ")),
      ["running", "queued"].includes(j.status) ? h("div", { class: "bar" },
        h("i", { class: j.progress == null ? "indet" : "", style: j.progress != null ? { width: `${Math.round(j.progress * 100)}%` } : {} })) : null,
      j.error ? h("div", { style: { color: "var(--danger)" }, class: "small" }, j.error) : null,
      (j.log_tail || []).length ? h("pre", {}, j.log_tail.join("\n")) : null,
      h("div", { class: "row" },
        ["running", "queued"].includes(j.status) ? h("button", { class: "btn ghost", onclick: () => api.post(`/api/jobs/${j.id}/cancel`) }, icon("stop"), "Cancel") : null,
        h("button", { class: "btn ghost", onclick: async () => {
          const full = (await api.get(`/api/jobs/${j.id}`)).job;
          const { modal } = await import("./ui.js");
          modal({ title: j.title, wide: true, body: h("pre", { class: "mono", style: { whiteSpace: "pre-wrap", margin: 0 } }, (full.log || []).join("\n")) });
        } }, icon("text"), "Full log"),
        j.node ? h("button", { class: "btn ghost", onclick: () => A.openNode(j.node) }, icon("chevron"), "Go to project") : null),
    )) : h("div", { class: "muted", style: { padding: "12px" } }, "No jobs yet.")));
}
