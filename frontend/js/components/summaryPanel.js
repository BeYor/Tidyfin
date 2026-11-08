// Summary of what cleanup did over a period: space reclaimed and titles saved.
import { h, clear, append } from "../h.js";
import { api } from "../api.js";
import { t, tn } from "../i18n.js";
import { bytes } from "../fmt.js";

const PERIODS = [7, 30, 90];

/** options.compact: no period switch (overview card). **/
export function summaryPanel({ days = 30, compact = false, title } = {}) {
  let period = days, alive = true;
  const body = h("div", { class: "sum-body" });
  const root = h("section", { class: "panel summary" });
  const seg = h("div", { class: "seg small", role: "group", "aria-label": t("sum.period") });

  function paintSeg() {
    clear(seg);
    for (const d of PERIODS) seg.append(h("button", { type: "button", "aria-pressed": String(d === period), onclick: () => { period = d; paintSeg(); load(); } }, t("sum.days", { n: d })));
  }

  async function load() {
    try {
      const s = await api.get(`/summary?days=${period}`);
      if (!alive) return;
      clear(body);
      const reasons = Object.entries(s.saved.by_reason).sort((a, b) => b[1] - a[1]);
      append(body, [
        h("div", { class: "sum-grid" },
          h("div", null, h("b", { class: "num" }, bytes(s.deleted.bytes)), h("span", null, t("sum.reclaimed"))),
          h("div", null, h("b", { class: "num" }, String(s.deleted.count)), h("span", null, t("sum.deleted"))),
          h("div", { class: "good" }, h("b", { class: "num" }, String(s.saved.count)), h("span", null, t("sum.saved")))),
        reasons.length ? h("p", { class: "muted small sum-why" }, reasons.map(([r, n]) => `${t(`reason.${r}`)}: ${n}`).join(" · ")) : null,
        s.saved.bytes ? h("p", { class: "muted small" }, t("sum.savedBytes", { size: bytes(s.saved.bytes) })) : null]);
    } catch { clear(body); }
  }

  root.append(h("div", { class: "sum-head" }, h("h2", null, title || t("sum.title")), compact ? h("span", { class: "muted small" }, t("sum.days", { n: period })) : seg), body);
  if (!compact) paintSeg();
  load();
  root.destroy = () => { alive = false; };
  return root;
}
