// "Publish as a web page": turns this design's outputs into one web page in the app's own theme,
// to preview, download as a folder, or put online with GitHub Pages (through the gh CLI).
import { api, enc } from "./api.js";
import { FONTS, palette } from "./theme.js";
import { confirmBox, explain, h, icon, toast } from "./ui.js";

const cache = new Map();   // node id -> publish status from the server
const forms = new Map();   // node id -> what the user typed (kept across re-renders)

export async function refreshPublish(S) {
  if (!S.nodeId) return;
  try { cache.set(S.nodeId, await api.get(`/api/nodes/${enc(S.nodeId)}/publish`)); } catch { /* shown as loading */ }
}

const slug = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "landscape";
// A title that starts with a house number is probably the street address.
const looksLikeAddress = (t) => /^\s*\d{2,6}\s+\S+/.test(t);

function theme(S) {
  const a = S.settings.appearance;
  return { light: palette(a, "light"), dark: palette(a, "dark"), mode: a.mode,
    font: FONTS[a.font_ui] || `"${a.font_ui}", ${FONTS.system}`, radius: a.radius ?? 10 };
}

export function publishSection(S, A) {
  const N = S.node;
  const st = cache.get(S.nodeId);
  const url = `/api/nodes/${enc(S.nodeId)}/publish`;
  const job = N.busy.find((j) => j.kind === "publish");
  const sec = h("div", { class: "section publish" }, h("h2", {}, icon("globe"), "Publish as a web page"));
  if (!st) {
    refreshPublish(S).then(() => A.render());
    sec.append(h("div", { class: "muted small" }, "Loading…"));
    return sec;
  }
  if (!st.available.length) {
    sec.append(explain("Turns your images, walkthrough and videos into one web page that looks like this app, to share with a link or as a folder."),
      h("div", { class: "muted small" }, "Make some outputs first; then they can be published here."));
    return sec;
  }
  const f = forms.get(S.nodeId) || {
    title: st.options?.title || N.meta.title, subtitle: st.options?.subtitle || "",
    types: new Set(st.options?.types?.length ? st.options.types.filter((k) => st.available.some((a) => a.key === k)) : st.defaults),
    repo: (st.github.repo || "").split("/")[1] || `${slug(N.meta.title)}-landscape`,
  };
  forms.set(S.nodeId, f);
  const body = () => ({ title: f.title, subtitle: f.subtitle, types: [...f.types], theme: theme(S) });
  const run = async (path, extra = {}, msg) => {
    try { await api.post(`${url}/${path}`, { ...body(), ...extra }); toast(msg); A.render(); }
    catch (e) { toast(e.message, "error"); }
  };
  const gh = st.github;
  const live = gh.url;
  const staleN = st.available.filter((a) => f.types.has(a.key)).reduce((n, a) => n + a.stale, 0);

  const titleIn = h("input", { type: "text", value: f.title, oninput: (e) => { f.title = e.target.value; warn.hidden = !looksLikeAddress(f.title); } });
  const warn = h("div", { class: "small", style: { color: "var(--warn)" }, hidden: !looksLikeAddress(f.title) },
    "This looks like a street address. Anyone with the link will see the title; consider a name instead.");

  sec.append(...[
    explain("Turns the outputs you pick into one web page in this app's colors: an image gallery, the 3D walkthrough and videos. " +
      "Preview it here, download it as a folder for any web host, or put it online with GitHub Pages."),
    h("div", { class: "pub-fields" },
      h("label", { class: "field" }, h("span", {}, "Page title"), titleIn, warn),
      h("label", { class: "field" }, h("span", {}, "Subtitle (optional)"),
        h("input", { type: "text", value: f.subtitle, placeholder: "e.g. Backyard plan, spring 2027", oninput: (e) => { f.subtitle = e.target.value; } }))),
    h("div", { class: "field-label" }, "Include"),
    h("div", { class: "pub-types" }, st.available.map((a) => h("label", { class: "pub-type" },
      h("input", { type: "checkbox", checked: f.types.has(a.key), onchange: (e) => { e.target.checked ? f.types.add(a.key) : f.types.delete(a.key); A.render(); } }),
      h("span", {}, a.label), h("span", { class: "muted small" }, String(a.count)),
      a.stale ? h("span", { class: "chip warn", title: "Made from an older version of the design" }, "stale") : null))),
    staleN ? h("div", { class: "small", style: { color: "var(--warn)", marginTop: "6px" } },
      `${staleN} of the included outputs are stale. Generate outputs first if you want the page to show the current design.`) : null,
    h("div", { class: "row", style: { marginTop: "12px" } },
      h("button", { class: "btn primary", disabled: !!job || !f.types.size, onclick: () => run("build", {}, "Building the web page…") },
        icon("refresh"), st.built ? "Rebuild page" : "Build page"),
      st.preview_url ? h("button", { class: "btn", onclick: () => window.open(st.preview_url, "_blank") }, icon("open"), "Preview") : null,
      st.built ? h("a", { class: "btn", href: `${url}/download`, title: "The page as a folder (.zip) for any web host" }, icon("download"), ".zip") : null),
    st.built ? h("div", { class: "muted small", style: { marginTop: "4px" } },
      `Built ${new Date(st.built_at * 1000).toLocaleString()} · ${st.size_mb} MB`) : null,
    job ? h("div", { style: { marginTop: "10px" } }, h("div", { class: "muted small" }, job.step || "Starting…"),
      h("div", { class: "job running" }, h("div", { class: "bar" }, h("i", { style: { width: `${Math.round((job.progress || 0) * 100)}%` } })))) : null,

    h("div", { class: "pub-online" },
      h("div", { class: "field-label" }, "Online"),
      !gh.gh ? h("div", { class: "muted small" }, "To put the page online from here, install the GitHub CLI (cli.github.com) and run “gh auth login”. " +
          "Or upload the .zip folder to any static host (Netlify, Cloudflare Pages…).")
      : !gh.user ? h("div", { class: "muted small" }, "The GitHub CLI isn't signed in. Run “gh auth login” in a terminal, then reopen this page.")
      : [
        live ? h("div", { class: "pub-live" }, h("span", { class: "chip ok" }, "online"),
          h("a", { href: live, target: "_blank", rel: "noopener" }, live)) : null,
        gh.taken_down_at && !live ? h("div", { class: "muted small" }, `Taken offline. The repository github.com/${gh.repo} still exists; delete it on GitHub to remove the files.`) : null,
        h("label", { class: "field" }, h("span", {}, `Repository (github.com/${gh.user}/…)`),
          h("input", { type: "text", value: f.repo, disabled: !!live, oninput: (e) => { f.repo = e.target.value; } })),
        h("div", { class: "row", style: { marginTop: "8px" } },
          h("button", { class: "btn", disabled: !!job || !f.types.size || !f.repo.trim(), onclick: async () => {
            const ok = await confirmBox(live ? "Update the online page?" : "Publish to GitHub Pages?",
              `The page and every image, video and 3D model on it will be public at github.com/${gh.user}/${f.repo.trim()} ` +
              "and its github.io address. Anyone with the link can see it, and copies may be kept even after you take it down.",
              live ? "Update" : "Publish publicly");
            if (ok) run("github", { repo: f.repo.trim(), confirm_public: true }, "Publishing to GitHub Pages…");
          } }, icon("globe"), live ? "Update online page" : "Publish to GitHub Pages"),
          live ? h("button", { class: "btn ghost danger", disabled: !!job, onclick: async () => {
            if (await confirmBox("Take the page offline?", "Turns GitHub Pages off so the link stops working. The repository itself stays on GitHub until you delete it there.", "Take offline", true))
              run("takedown", {}, "Taking the page offline…");
          } }, "Take offline") : null),
      ]),
  ].filter(Boolean));  // append() would print null as text
  return sec;
}
