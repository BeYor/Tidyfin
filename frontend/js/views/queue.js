import { h, clear, append } from "../h.js";
import { api } from "../api.js";
import { icon } from "../icons.js";
import { t, tn } from "../i18n.js";
import { bytes, typeLabel, dateTime } from "../fmt.js";
import { actionButton, dialog, emptyState, loading, segmented, toast, toastError } from "../ui.js";
import { pageHead } from "./common.js";
import { posterCard } from "../components/posterCard.js";
import { openItemDialog } from "../components/itemDialog.js";
import { bulkBar } from "../components/bulkBar.js";
import { scheduleDialog } from "../components/scheduleDialog.js";

function showProtected(app, onChange) {
  const list = h("div", null, loading());
  const dlg = dialog({ title: t("queue.protectedTitle"), wide: true, body: list, actions: [{ label: t("common.close"), kind: "primary" }] });
  async function fill() {
    const items = await api.get("/protected");
    clear(list);
    if (!items.length) { list.append(h("p", { class: "muted" }, t("queue.protectedEmpty"))); return; }
    list.append(h("div", { class: "rows" }, items.map((p) => h("div", { class: "row" },
      h("div", { class: "grow" }, h("div", { class: "t" }, p.title), h("div", { class: "s" }, `${typeLabel(p.media_type)}${p.tags.length ? `, ${p.tags.join(", ")}` : ""}`)),
      actionButton({ label: t("item.unprotect"), small: true, onClick: async () => {
        try { await api.del(`/items/${p.key}/protect`); toast(t("toast.unprotected")); await fill(); onChange(); } catch (e) { toastError(e); } } })))));
  }
  fill().catch((e) => { clear(list); list.append(h("div", { class: "err" }, e.message)); });
  return dlg;
}

function showPreview() {
  const body = h("div", null, h("div", { style: { display: "flex", gap: "12px", alignItems: "center" } }, loading()), h("p", { class: "muted" }, t("queue.previewWait")));
  dialog({ title: t("queue.previewTitle"), wide: true, body, actions: [{ label: t("common.close"), kind: "primary" }] });
  api.post("/preview").then((pv) => {
    clear(body);
    if (pv.unavailable?.length) body.append(h("div", { class: "err", style: { marginBottom: "14px" } }, t("queue.previewSkipped", { what: pv.unavailable.map((x) => (x === "movie" ? t("type.movies") : t("type.seriesPlural"))).join(", ") })));
    if (!pv.items.length) { body.append(h("p", { class: "muted" }, t("queue.previewNone"))); return; }
    body.append(h("p", { style: { marginBottom: "14px" } }, t("queue.previewSummary", { items: tn("count.items", pv.items.length), size: bytes(pv.total_size_bytes) })),
      h("div", { style: { overflowX: "auto" } }, h("table", { class: "table" },
        h("thead", null, h("tr", null, h("th", null, t("col.title")), h("th", null, t("col.type")), h("th", null, t("col.deadline")), h("th", { class: "num" }, t("col.size")))),
        h("tbody", null, pv.items.sort((a, b) => b.size_bytes - a.size_bytes).map((i) => h("tr", null,
          h("td", null, i.title, i.in_queue ? h("span", { class: "tag", style: { marginLeft: "8px" } }, t("queue.alreadyQueued")) : null),
          h("td", { class: "muted" }, typeLabel(i.media_type)), h("td", { class: "muted" }, new Date(i.delete_at).toLocaleDateString()), h("td", { class: "num" }, bytes(i.size_bytes))))))));
  }).catch((e) => { clear(body); body.append(h("div", { class: "err" }, e.message)); });
}

export function mount(root, app) {
  let alive = true, items = [], protectedCount = 0;
  const f = { type: "all", source: "all", q: "", sort: "soon", group: true };
  const selection = new Set();
  const wall = h("div");
  const toolbar = h("div", { class: "toolbar" });
  const dryRun = () => app.status && app.status.dry_run;
  const bar = bulkBar(selection, { app, dryRun: dryRun(), onDone: async () => { await load(); } });
  const protBtn = h("button", { class: "btn", type: "button", onclick: () => showProtected(app, load) });
  root.append(pageHead(t("nav.queue"), h("button", { class: "btn", type: "button", onclick: showPreview }, icon("search", 16), t("queue.preview")), protBtn), toolbar, wall);
  document.body.append(bar.el);
  wall.append(loading());

  const quick = [
    { label: t("item.keep"), icon: "shield", title: t("item.keepHint"), onClick: async (it) => {
      try { await api.post(`/items/${it.key}/protect`); toast(t("toast.kept", { title: it.title })); await load(); app.refreshStatus(); } catch (e) { toastError(e); } } },
    { label: t("queue.plus7"), icon: "clockplus", title: t("queue.plus7Hint"), onClick: async (it) => {
      try { await api.post(`/items/${it.key}/postpone`, { days: 7 }); toast(t("toast.postponed")); await load(); } catch (e) { toastError(e); } } },
  ];

  function paint() {
    clear(toolbar);
    const sortSel = h("select", { class: "input", "aria-label": t("common.sort"), onchange: (e) => { f.sort = e.target.value; paintWall(); } },
      [["soon", t("queue.sortSoon")], ["size", t("queue.sortSize")], ["title", t("queue.sortTitle")]].map(([v, l]) => h("option", { value: v, selected: f.sort === v }, l)));
    const search = h("div", { class: "search" }, icon("search", 16), h("input", { class: "input", type: "search", placeholder: t("queue.search"), "aria-label": t("queue.search"), value: f.q, oninput: (e) => { f.q = e.target.value; paintWall(); } }));
    toolbar.append(
      segmented([["all", t("filter.all")], ["movie", t("type.movies")], ["series", t("type.seriesPlural")]], f.type, (v) => { f.type = v; paintWall(); }, t("col.type")),
      segmented([["all", t("filter.anySource")], ["auto", t("filter.auto")], ["manual", t("filter.manual")]], f.source, (v) => { f.source = v; paintWall(); }, t("item.source")),
      search, sortSel,
      h("button", { class: "btn", type: "button", "aria-pressed": String(f.group), title: t("queue.group"), onclick: (e) => { f.group = !f.group; e.currentTarget.setAttribute("aria-pressed", String(f.group)); paintWall(); } }, icon("queue", 16), t("queue.group")));
    paintWall();
  }

  const BUCKETS = [["now", "queue.groupNow", (i) => i.due], ["last", "queue.groupLast", (i) => i.days_left <= 0],
    ["week", "queue.groupWeek", (i) => i.days_left <= 7], ["later", "queue.groupLater", () => true]];

  function emptyQueue() {
    const types = Object.values((app.status && app.status.cleanup && app.status.cleanup.types) || {});
    const waiting = types.find((x) => !x.active && x.reason === "below_threshold");
    return emptyState("check", t("queue.emptyTitle"),
      waiting ? t("queue.emptyPaused", { usage: waiting.usage, start: app.status.cleanup.start_percent }) : t("queue.emptyText"),
      h("button", { class: "btn", type: "button", onclick: showPreview }, icon("search", 16), t("queue.preview")));
  }

  function paintWall() {
    clear(wall);
    let list = items.filter((i) => (f.type === "all" || i.media_type === f.type) && (f.source === "all" || i.source === f.source)
      && (!f.q || (i.title || "").toLowerCase().includes(f.q.toLowerCase())));
    const cmp = { soon: (a, b) => a.delete_at.localeCompare(b.delete_at), size: (a, b) => b.size_bytes - a.size_bytes, title: (a, b) => (a.title || "").localeCompare(b.title || "", undefined, { sensitivity: "base" }) }[f.sort];
    list = list.sort(cmp);
    if (!items.length) { wall.append(emptyQueue()); return; }
    if (!list.length) { wall.append(emptyState("search", t("common.noResults"), t("common.noResultsText"))); return; }
    const card = (i) => posterCard(i, {
      selected: selection.has(i.key), actions: quick,
      onOpen: (it) => openItemDialog(it.key, app, load),
      onToggle: (it, on) => { on ? selection.add(it.key) : selection.delete(it.key); bar.refresh(); } });
    const total = list.reduce((a, i) => a + (i.size_bytes || 0), 0);
    wall.append(h("p", { class: "muted count-line" }, `${tn("count.items", list.length)}, ${bytes(total)}`));
    if (f.group && f.sort === "soon") {
      const taken = new Set();
      for (const [id, key, test] of BUCKETS) {
        const group = list.filter((i) => !taken.has(i.key) && test(i));
        group.forEach((i) => taken.add(i.key));
        if (!group.length) continue;
        wall.append(h("section", { class: `group g-${id}` },
          h("h2", { class: "group-head" }, t(key), h("span", null, `${tn("count.items", group.length)}, ${bytes(group.reduce((a, i) => a + (i.size_bytes || 0), 0))}`)),
          h("div", { class: "wall" }, group.map(card))));
      }
    } else {
      wall.append(h("div", { class: "wall" }, list.map(card)));
    }
  }

  async function load() {
    try {
      const [q, prot] = await Promise.all([api.get("/queue"), api.get("/protected")]);
      if (!alive) return;
      items = q; protectedCount = prot.length;
      for (const k of [...selection]) if (!items.some((i) => i.key === k)) selection.delete(k);
      clear(protBtn);
      append(protBtn, [icon("shield", 16), t("queue.protectedList"), protectedCount ? h("span", { class: "tag", style: { marginLeft: "2px" } }, String(protectedCount)) : null]);
      bar.refresh();
      paint();
    } catch (e) { clear(wall); wall.append(h("div", { class: "err" }, e.message)); }
  }
  load();
  return () => { alive = false; bar.el.remove(); };
}
