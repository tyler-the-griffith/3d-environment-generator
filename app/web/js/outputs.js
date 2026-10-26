// Output page: every output of this node, grouped by output type, each opened from a cover image
// taken from the output itself. Images and PDFs display inline; tables are previewed.
import { api, enc } from "./api.js";
import { outSpecSection } from "./outspec.js";
import { publishSection } from "./publish.js";
import { explain, fmtTime, h, icon, lightbox, toast } from "./ui.js";

const csvCache = new Map();

export function renderOutput(el, S, A) {
  const N = S.node;
  const data = S.outputs || { groups: {}, types: S.app.output_types };
  const nodeUrl = `/api/nodes/${enc(S.nodeId)}`;
  const selected = N.output_spec.selected;
  const typeInfo = Object.fromEntries(S.app.output_types.map((t) => [t.key, t]));
  const genJob = N.busy.find((j) => j.kind === "generate");
  const hasErrors = N.issues.some((i) => i.level === "error");
  const neverDesigned = !N.versions.some((v) => !["template", "branch"].includes(v.source)) && !N.inputs.rounds.length;

  const generate = async (types, force = false) => {
    try {
      await api.post(`${nodeUrl}/generate`, { types, force });
      toast(types ? `Making ${types.map((t) => typeInfo[t]?.label || t).join(", ")}…` : "Making the selected outputs…");
    } catch (e) { toast(e.message, "error"); }
  };

  // Every still image on the page, in page order, so the viewer can arrow through all of them.
  const stillsInOrder = () => [...selected, "other"].flatMap((k) => (data.groups[k] || [])
    .filter((o) => o.kind === "still")
    .map((o) => ({ o, title: `${typeInfo[k]?.label || "Other"}: ${o.label}`, kind: "image", url: o.url, download: o.download })));

  const open = (o) => {
    if (o.kind === "still") {
      const items = stillsInOrder();
      lightbox({ items, index: Math.max(0, items.findIndex((x) => x.o === o)) });
    }
    else if (o.kind === "video") lightbox({ title: o.label, kind: "video", url: o.url, download: o.download });
    else if (o.kind === "web") lightbox({ title: `${o.label}: 3D walkthrough`, kind: "frame", url: o.url });
    else if (o.kind === "pdf") window.open(o.url, "_blank");
    else window.open(o.download, "_self");
  };

  const card = (o) => {
    const badges = [o.stale ? h("span", { class: "chip stale", title: "The design changed after this was made. Generate again to update it." }, "stale") : null,
      h("span", { class: "chip" }, o.detail)];
    const playable = { video: ["play", "Play video"], web: ["cube", "Open walkthrough"], file: ["download", "Download"],
      pdf: ["open", "Open PDF"] }[o.kind];
    const cover = o.cover_url
      ? h("img", { src: o.cover_url, alt: o.label, loading: "lazy" })
      : h("div", { style: { display: "grid", placeItems: "center", height: "100%", color: "var(--muted)" } }, icon("file"));
    const c = h("div", { class: `ocard ${o.stale ? "stale" : ""}`, "data-ctx": "output" },
      h("button", { class: "ocover", onclick: () => open(o), title: playable ? playable[1] : "View" },
        cover, playable ? h("div", { class: "play" }, h("span", {}, icon(playable[0]), playable[1])) : null,
        h("div", { class: "badges" }, badges)),
      h("footer", {},
        h("div", { style: { flex: 1, minWidth: 0 } }, h("div", { class: "name", title: o.label }, o.label),
          h("div", { class: "meta" }, `${fmtTime(o.rendered)} · ${o.fingerprint.slice(0, 8)}`)),
        h("a", { class: "btn ghost icon", href: o.download, title: "Download" }, icon("download"))));
    c.ctxItems = () => [
      { label: "Open", icon: "open", onClick: () => open(o) },
      { label: "Download", icon: "download", onClick: () => window.open(o.download, "_self") },
      { label: "Show in folder", icon: "folder", onClick: () => api.post("/api/reveal", { path: o.download.split("path=")[1] }) },
    ];
    return c;
  };

  const inlinePdf = (o) => h("div", { class: "card" },
    h("div", { class: "card-head" }, h("span", { class: "title" }, o.label),
      o.stale ? h("span", { class: "chip warn" }, "stale") : null,
      h("a", { class: "btn", href: o.download }, icon("download"), "Download"),
      h("button", { class: "btn", onclick: () => window.open(o.url, "_blank") }, icon("open"), "Own window")),
    h("iframe", { class: "pdfview", src: o.url, title: o.label }));

  const inlineCsv = (o) => {
    const wrap = h("div", { class: "table-wrap" }, h("div", { class: "muted small", style: { padding: "10px" } }, "Loading…"));
    const fill = (text) => {
      const rows = text.trim().split(/\r?\n/).map((r) => r.split(","));
      wrap.replaceChildren(h("table", { class: "data" },
        h("thead", {}, h("tr", {}, rows[0].map((c) => h("th", {}, c.replace(/_/g, " "))))),
        h("tbody", {}, rows.slice(1).map((r) => h("tr", {}, r.map((c) => h("td", {}, c)))))));
    };
    if (csvCache.has(o.url)) fill(csvCache.get(o.url));
    else fetch(o.url).then((r) => r.text()).then((t) => { csvCache.set(o.url, t); fill(t); });
    return h("div", { class: "card" }, h("div", { class: "card-head" }, h("span", { class: "title" }, o.label),
      o.stale ? h("span", { class: "chip warn" }, "stale") : null,
      h("a", { class: "btn", href: o.download }, icon("download"), "Download CSV")), wrap);
  };

  const group = (key) => {
    const t = typeInfo[key];
    const items = data.groups[key] || [];
    const running = genJob && genJob.step && genJob.step === t?.label;
    return h("div", { class: "ogroup", id: `og-${key}` },
      h("header", {}, h("h2", {}, t?.label || "Other"),
        h("span", { class: "muted small" }, items.length ? `${items.length} item${items.length === 1 ? "" : "s"}` : ""),
        h("span", { class: "spacer", style: { flex: 1 } }),
        running ? h("span", { class: "chip warn" }, "making now…") : null,
        t ? h("button", { class: "btn ghost", disabled: !!genJob || hasErrors, onclick: () => generate([key], true),
          title: "Make this output type again now" }, icon("refresh"), items.length ? "Remake" : "Make") : null),
      t ? explain(t.explain) : null,
      !items.length ? h("div", { class: "empty" }, `Not made yet. Use “Generate outputs”, or “Make” for just this type.`)
        : items.some((o) => o.kind === "pdf" || (o.kind === "file" && o.file.endsWith(".csv")))
          ? items.map((o) => o.kind === "pdf" ? inlinePdf(o) : o.file.endsWith(".csv") ? inlineCsv(o) : h("div", { class: "ocards" }, card(o)))
          : h("div", { class: "ocards" }, items.map(card)));
  };

  const hiddenCount = Object.keys(data.groups).filter((k) => k !== "other" && !selected.includes(k))
    .reduce((n, k) => n + data.groups[k].length, 0);
  const staleCount = selected.flatMap((k) => data.groups[k] || []).filter((o) => o.stale).length;

  // Visuals first: made types in importance order (the three essentials lead), then a compact
  // list of selected types that haven't been made yet. Controls live in the side column.
  const PRIMARY = ["stills", "plan_check", "web_walkthrough"];
  const ordered = [...PRIMARY.filter((k) => selected.includes(k)), ...selected.filter((k) => !PRIMARY.includes(k))];
  const made = ordered.filter((k) => (data.groups[k] || []).length);
  const notMade = ordered.filter((k) => !(data.groups[k] || []).length);
  const nothingYet = !made.length && !(data.groups.other || []).length;

  const controls = h("div", { class: "section" },
    h("h2", {}, icon("sparkle"), "Generate & download"),
    h("div", { class: "row" },
      h("button", { class: "btn primary big", disabled: !!genJob || hasErrors || neverDesigned, onclick: () => generate(null) },
        icon("sparkle"), genJob ? "Generating…" : "Generate outputs"),
      h("a", { class: "btn big", href: `${nodeUrl}/download-all`, title: "Every shown output as a .zip, in folders by type" },
        icon("download"), "Download all")),
    staleCount || hiddenCount ? h("div", { class: "row", style: { marginTop: "8px" } },
      staleCount ? h("span", { class: "chip warn" }, `${staleCount} stale`) : null,
      hiddenCount ? h("span", { class: "chip", title: "Outputs of types you've turned off; they reappear when you turn them back on" }, `${hiddenCount} hidden`) : null) : null,
    genJob ? h("div", { style: { marginTop: "10px" } },
      h("div", { class: "muted small" }, genJob.step || "Starting…"),
      h("div", { class: "job running" }, h("div", { class: "bar" }, h("i", { style: { width: `${Math.round((genJob.progress || 0) * 100)}%` } }))),
      h("button", { class: "btn ghost", onclick: () => api.post(`/api/jobs/${genJob.id}/cancel`) }, icon("stop"), "Cancel")) : null,
    neverDesigned ? h("p", { class: "muted" }, `No design yet: add inputs on the input page and send them to ${S.app.ai_name} first.`)
      : hasErrors ? h("p", { style: { color: "var(--danger)" } }, "The design file has errors. Fix them on the input page before generating.")
        : explain("Makes every selected type that's missing or stale and skips anything already up to date. " +
          "Videos and final-quality images take a while. Progress shows here and under Jobs."));

  const pending = notMade.length ? h("div", { class: "section pending" },
    h("h2", {}, nothingYet ? "Nothing made yet" : "Selected but not made yet"),
    h("div", { class: "pending-list" }, notMade.map((k) => h("div", { class: "pending-row" },
      h("div", { style: { flex: 1, minWidth: 0 } }, h("b", {}, typeInfo[k]?.label || k),
        h("div", { class: "muted small" }, typeInfo[k]?.explain || "")),
      h("button", { class: "btn", disabled: !!genJob || hasErrors || neverDesigned, onclick: () => generate([k], true) },
        icon("sparkle"), "Make"))))) : null;

  const side = h("aside", { class: "out-side" }, controls, publishSection(S, A), outSpecSection(S, A, "output"));
  const main = h("div", { class: "out-main" },
    made.map(group),
    (data.groups.other || []).length ? group("other") : null,
    pending);

  el.replaceChildren(h("div", { class: "pane-inner wide" },
    h("div", { class: "page-head" },
      h("div", { style: { flex: 1 } }, h("h1", {}, N.meta.title),
        h("div", { class: "sub" }, N.variant ? `Child variant of ${N.parent_title}` : "Base design", " · Output page")),
      h("button", { class: "btn", onclick: () => A.setTab("input") }, icon("chevron"), "Input page")),
    explain("Everything made from this design's 3D model. Click a cover to open it; images and PDFs show right here. " +
      "Every output is taken from the same model, and anything made from an older version of the design is marked stale."),
    h("div", { class: `out-layout ${nothingYet ? "side-first" : ""}` }, main, side),
  ));
}
