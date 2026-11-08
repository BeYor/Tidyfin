import { h, clear } from "../h.js";
import { api } from "../api.js";
import { t, tn, hasKey } from "../i18n.js";
import { summaryPanel } from "../components/summaryPanel.js";
import { relTime, dateTime, typeLabel } from "../fmt.js";
import { emptyState, loading } from "../ui.js";
import { pageHead, eventTag, EVENTS } from "./common.js";

const reasonNote = (e) => {
  const r = e.details && e.details.reason;
  return r && hasKey(`reason.${r}`) ? h("span", { class: "muted small reason" }, t(`reason.${r}`)) : null;
};

export function mount(root) {
  let alive = true, offset = 0, event = "";
  const PAGE = 100;
  const tbody = h("tbody");
  const more = h("button", { class: "btn", type: "button", hidden: true, onclick: () => load(false) }, t("history.older"));
  const info = h("p", { class: "muted small", style: { marginTop: "14px" } });
  const sel = h("select", { class: "input", "aria-label": t("history.filter"), onchange: (e) => { event = e.target.value; load(true); } },
    h("option", { value: "" }, t("history.all")), EVENTS.map((k) => h("option", { value: k }, t(`event.${k}`))));
  const table = h("table", { class: "table" }, h("thead", null, h("tr", null, h("th", null, t("col.when")), h("th", null, t("col.event")), h("th", null, t("col.title")), h("th", null, t("col.type")), h("th", null, t("col.source")))), tbody);
  const holder = h("div", null, loading());
  const summary = summaryPanel({ days: 30 });
  root.append(pageHead(t("nav.history"), sel), summary, h("div", { style: { height: "22px" } }), holder);

  async function load(reset) {
    if (reset) offset = 0;
    try {
      const res = await api.get(`/history?limit=${PAGE}&offset=${offset}` + (event ? `&event=${event}` : ""));
      if (!alive) return;
      if (reset) clear(tbody);
      clear(holder);
      if (!res.total) { holder.append(emptyState("history", t("history.emptyTitle"), event ? t("history.emptyFiltered") : t("history.emptyText"))); return; }
      for (const e of res.items) {
        tbody.append(h("tr", null, h("td", { class: "muted nowrap", title: dateTime(e.ts) }, relTime(e.ts)), h("td", null, eventTag(e.event), reasonNote(e)),
          h("td", null, e.title || e.key), h("td", { class: "muted" }, typeLabel(e.media_type)), h("td", { class: "muted" }, e.source ? t(`source.${e.source}`) : "")));
      }
      offset += res.items.length;
      info.textContent = t("history.shown", { n: offset, total: res.total });
      more.hidden = offset >= res.total;
      holder.append(h("div", { class: "scroll-x" }, table), info, h("div", { style: { marginTop: "12px" } }, more));
    } catch (e) { clear(holder); holder.append(h("div", { class: "err" }, e.message)); }
  }
  load(true);
  return () => { alive = false; if (summary.destroy) summary.destroy(); };
}
