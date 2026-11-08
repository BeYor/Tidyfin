// "Leaving next": the titles closest to their deadline, each with a clear day count.
import { h } from "../h.js";
import { enc } from "../api.js";
import { t, tn } from "../i18n.js";
import { bytes, typeLabel } from "../fmt.js";

function when(item) {
  if (item.due) return { cls: "now", big: t("when.overdue"), small: null, label: t("when.overdue") };
  if (item.days_left <= 0) return { cls: "now", big: t("when.lastDay"), small: null, label: t("when.lastDay") };
  const n = Math.min(item.days_left, 99);
  return { cls: item.days_left <= 3 ? "soon" : "", big: String(n), small: tn("board.days", n).replace(/^\d+\s*/, ""), label: tn("when.days", item.days_left) };
}

/** queue: rows from /api/queue (any order). onOpen(item). **/
export function leavingNext(queue, onOpen, limit = 6) {
  const rows = [...queue].sort((a, b) => a.delete_at.localeCompare(b.delete_at)).slice(0, limit);
  if (!rows.length) return h("p", { class: "muted" }, t("ov.nextEmpty"));
  return h("div", { class: "next-list", role: "list" }, rows.map((item) => {
    const w = when(item);
    const meta = `${typeLabel(item.media_type)}${item.size_bytes ? `, ${bytes(item.size_bytes)}` : ""}`;
    return h("button", { class: "next-row", type: "button", role: "listitem", onclick: () => onOpen(item), "aria-label": `${item.title}: ${w.label}` },
      h("img", { class: "next-thumb", src: `/api/poster/${enc(item.key)}?w=120`, alt: "", loading: "lazy", onerror: (e) => e.target.remove() }),
      h("span", { class: "next-title" }, h("b", null, item.title), h("small", null, meta)),
      h("span", { class: `next-when ${w.cls}` }, h("b", null, w.big), w.small ? h("small", null, w.small) : null));
  }));
}
