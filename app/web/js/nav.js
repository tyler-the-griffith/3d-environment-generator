// Left navigation: brand/logo slot, new project, project tree with nested child variants.
import { api, enc } from "./api.js";
import { confirmBox, h, icon, promptText, toast } from "./ui.js";

export function renderNav(el, S, A) {
  const b = S.app.brand;
  const logo = b.has_logo
    ? h("img", { src: "/branding/logo.png", alt: "" })
    : h("div", { class: "logo-ph", title: "Put your logo at app/branding/logo.png" }, "LOGO");

  const newProject = async () => {
    const title = await promptText("New project", "Property or project name", "", { okLabel: "Create" });
    if (!title) return;
    const r = await api.post("/api/projects", { title });
    await A.refreshTree();
    A.openNode(r.node, "input");
  };

  const newVariant = async (parentId, parentTitle) => {
    const title = await promptText("New child variant",
      `A variant of “${parentTitle}”. It starts as an exact copy; its inputs become editable, and it keeps inheriting fixes from its parent.`,
      "", { okLabel: "Create variant" });
    if (!title) return;
    try {
      const r = await api.post(`/api/nodes/${enc(parentId)}/variants`, { title });
      S.expanded.add(parentId);
      await A.refreshTree();
      A.openNode(r.node);
    } catch (e) { toast(e.message, "error"); }
  };

  const rename = async (n) => {
    const title = await promptText("Rename", "Name", n.title, { okLabel: "Rename" });
    if (!title) return;
    await api.patch(`/api/nodes/${enc(n.id)}`, { title });
    await A.refreshTree();
    if (S.nodeId === n.id) await A.refreshNode();
    A.render();
  };

  const archive = async (n) => {
    const isProject = !n.id.includes("/");
    const ok = await confirmBox(`Archive “${n.title}”?`,
      (isProject ? "The project and all its variants" : "This variant") +
      " will be moved to the projects/.archive folder. Nothing is deleted.", "Archive", true);
    if (!ok) return;
    try {
      await api.del(`/api/nodes/${enc(n.id)}`);
      if (S.nodeId === n.id || (isProject && S.nodeId?.startsWith(n.id + "/"))) await A.openNode(null);
      await A.refreshTree();
      A.render();
    } catch (e) { toast(e.message, "error"); }
  };

  const nodeRow = (n, depth) => {
    const kids = n.children || [];
    const open = S.expanded.has(n.id);
    const row = h("div", {
      class: `tnode ${S.nodeId === n.id ? "active" : ""}`, "data-node": n.id, tabindex: 0, role: "treeitem",
      title: n.id.includes("/") ? "Child variant" : "Project",
      onclick: () => A.openNode(n.id),
      onkeydown: (e) => { if (e.key === "Enter") A.openNode(n.id); },
    },
      h("span", { class: "twisty", onclick: (e) => {
        e.stopPropagation();
        open ? S.expanded.delete(n.id) : S.expanded.add(n.id);
        A.render();
      } }, kids.length ? icon(open ? "down" : "chevron") : null),
      h("span", { class: "tlabel" }, n.title),
      h("span", { class: `dot ${n.status}`, title: { ready: "Has a design", working: "AI working",
        new: "No design yet", imported: "Created outside the app" }[n.status] || n.status }));
    row.ctxItems = () => [
      { label: "New child variant", icon: "branch", onClick: () => newVariant(n.id, n.title) },
      { label: "Rename", icon: "edit", onClick: () => rename(n) },
      { label: "Open input page", icon: "chevron", onClick: () => A.openNode(n.id, "input") },
      { label: "Open output page", icon: "chevron", onClick: () => A.openNode(n.id, "output") },
      { label: "Archive", icon: "trash", onClick: () => archive(n) },
    ];
    return h("div", {}, row,
      kids.length && open ? h("div", { class: "tchildren", role: "group" }, kids.map((k) => nodeRow(k, depth + 1))) : null);
  };

  const gpu = S.app.gpu || {};
  el.replaceChildren(
    h("div", { class: "brand" }, logo, h("div", {}, h("div", { class: "brand-name" }, b.name),
      h("div", { class: "brand-sub" }, `v${b.version}`))),
    h("div", { class: "nav-actions" },
      h("button", { class: "btn primary", id: "new-project", onclick: newProject }, icon("plus"), "New project"),
      S.node ? h("button", { class: "btn", title: "Create a child variant of the open project or variant",
        onclick: () => newVariant(S.nodeId, S.node.meta.title) }, icon("branch"), "Variant") : null),
    h("div", { class: "tree", role: "tree" },
      h("div", { class: "tree-section" }, "Projects"),
      S.tree.length ? S.tree.map((n) => nodeRow(n, 0)) : h("div", { class: "muted small", style: { padding: "8px" } },
        "No projects yet.")),
    h("div", { class: "nav-foot" },
      h("span", { class: `chip ${gpu.vm_running ? "warn" : "ok"}`,
        title: gpu.vm_running ? `GPU in use by ${gpu.vms.join(", ")}; renders use the CPU` : "GPU available for rendering" },
        gpu.vm_running ? "GPU: VM" : "GPU ready"),
      h("span", { class: "spacer", style: { flex: 1 } }),
      h("span", { class: "chip", title: "AI provider (Settings → AI)" }, S.app.providers[S.settings.ai.provider]?.label || "AI")),
  );
}
