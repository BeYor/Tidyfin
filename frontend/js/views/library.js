import { h, clear } from "../h.js";
import { api } from "../api.js";
import { icon } from "../icons.js";
import { t, tn } from "../i18n.js";
import { bytes } from "../fmt.js";
import { emptyState, loading, segmented } from "../ui.js";
import { pageHead } from "./common.js";
import { posterCard } from "../components/posterCard.js";
import { openItemDialog } from "../components/itemDialog.js";
import { bulkBar } from "../components/bulkBar.js";

const PAGE = 48;

export function mount(root, app) {
  let alive = true, total = 0, totalSize = 0, items = [], busy = false, pending = false, timer = null;
  const f = { type: "all", status: "all", q: "", sort: "title", desc: false };
  const selection = new Set();
  const wall = h("div", { class: "wall" });
  const info = h("p", { class: "muted count-line" });
  const more = h("button", { class: "btn", type: "button", hidden: true, onclick: () => load(false) }, t("library.more"));
  const holder = h("div");
  const toolbar = h("div", { class: "toolbar" });
  const bar = bulkBar(selection, { app, dryRun: app.status && app.status.dry_run, onDone: () => load(true) });
  root.append(pageHead(t("nav.library")), toolbar, holder);
  document.body.append(bar.el);

  function buildToolbar() {
    const search = h("div", { class: "search" }, icon("search", 16),
      h("input", { class: "input", type: "search", placeholder: t("library.search"), "aria-label": t("library.search"), value: f.q,
        oninput: (e) => { f.q = e.target.value; clearTimeout(timer); timer = setTimeout(() => load(true), 250); } }));
    const sortSel = h("select", { class: "input", "aria-label": t("common.sort"), onchange: (e) => { f.sort = e.target.value; load(true); } },
      [["title", t("queue.sortTitle")], ["size", t("queue.sortSize")], ["added", t("library.sortAdded")]].map(([v, l]) => h("option", { value: v, selected: f.sort === v }, l)));
    const dir = h("button", { class: "btn icon", type: "button", title: t("library.reverse"), "aria-label": t("library.reverse"), "aria-pressed": String(f.desc),
      onclick: (e) => { f.desc = !f.desc; e.currentTarget.setAttribute("aria-pressed", String(f.desc)); load(true); } }, icon("sort", 16));
    toolbar.append(
      segmented([["all", t("filter.all")], ["movie", t("type.movies")], ["series", t("type.seriesPlural")]], f.type, (v) => { f.type = v; load(true); }, t("col.type")),
      segmented([["all", t("filter.all")], ["free", t("library.free")], ["queued", t("library.queued")], ["protected", t("library.kept")]], f.status, (v) => { f.status = v; load(true); }, t("item.status")),
      search, sortSel, dir);
  }

  async function load(reset) {
    if (busy) { if (reset) pending = true; return; } // re-run once the request in flight finishes
    busy = true;
    try {
      const offset = reset ? 0 : items.length;
      const qs = new URLSearchParams({ limit: PAGE, offset, sort: f.sort, status: f.status, q: f.q, order: f.desc ? "desc" : "asc" });
      if (f.type !== "all") qs.set("type", f.type);
      const res = await api.get(`/library?${qs}`);
      if (!alive) return;
      if (reset) { items = []; clear(wall); }
      items = items.concat(res.items);
      total = res.total; totalSize = res.total_size_bytes;
      for (const it of res.items) {
        wall.append(posterCard(it, { selected: selection.has(it.key),
          onOpen: (c) => openItemDialog(c.key, app, () => load(true)),
          onToggle: (c, on) => { on ? selection.add(c.key) : selection.delete(c.key); bar.refresh(); } }));
      }
      clear(holder);
      if (!total) {
        const empty = (await app.refreshStatus())?.library?.total === 0;
        holder.append(emptyState(empty ? "poster" : "search", empty ? t("library.emptyTitle") : t("common.noResults"), empty ? t("library.emptyText") : t("common.noResultsText")));
      } else {
        info.textContent = `${tn("count.items", total)}, ${bytes(totalSize)}`;
        more.hidden = items.length >= total;
        holder.append(info, wall, h("div", { class: "more" }, more));
      }
      bar.refresh();
    } catch (e) { clear(holder); holder.append(h("div", { class: "err" }, e.message)); }
    finally { busy = false; if (pending) { pending = false; load(true); } }
  }
  buildToolbar();
  holder.append(loading());
  load(true);
  return () => { alive = false; clearTimeout(timer); bar.el.remove(); };
}
