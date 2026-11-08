import { h } from "../h.js";
import { enc } from "../api.js";
import { icon } from "../icons.js";
import { t } from "../i18n.js";
import { bytes, typeLabel, whenLabel, urgency } from "../fmt.js";

const DAY = 864e5;

/** Normalise a queue row or a library row into one shape for the card. */
export function cardItem(row) {
  const q = row.queue !== undefined ? row.queue : (row.delete_at ? row : null);   // library rows nest it under .queue
  const base = { key: row.key, title: row.title, media_type: row.media_type, size_bytes: row.size_bytes || 0, protected: !!row.protected };
  if (!q || !q.delete_at) return { ...base, inQueue: false };
  const end = new Date(q.delete_at).getTime();
  const due = Date.now() >= end;
  return { ...base, inQueue: true, delete_at: q.delete_at, queued_at: row.queued_at || null, due,
           days_left: Math.max(Math.floor((end - Date.now()) / DAY), 0), source: q.source || row.source,
           origin: row.origin, accelerated: !!(q.accelerated || row.accelerated) };
}

export function posterCard(row, opts = {}) {
  const item = cardItem(row);
  const img = h("img", { src: `/api/poster/${enc(item.key)}?w=300`, alt: "", loading: "lazy", decoding: "async" });
  img.addEventListener("error", () => img.remove());
  const fade = item.inQueue ? urgency(item) : 0;

  const art = h("div", { class: "art", style: { "--u": fade.toFixed(3) } },
    h("div", { class: "none" }, icon(item.media_type === "series" ? "film" : "poster", 30)), img);
  if (item.inQueue) {
    art.append(h("span", { class: "when" + (item.due ? " due" : "") }, whenLabel(item)),
      h("div", { class: "fuse", "aria-hidden": "true" }, h("i")));
  } else if (item.protected) {
    art.append(h("span", { class: "when kept" }, icon("shield", 12), t("card.kept")));
  }
  if (opts.onToggle) {
    art.append(h("label", { class: "pick", title: t("card.select") },
      h("input", { type: "checkbox", checked: !!opts.selected, "aria-label": t("card.selectItem", { title: item.title }),
        onchange: (e) => opts.onToggle(item, e.target.checked) }), h("span", { class: "box" }, icon("check", 14))));
  }
  const open = h("button", { class: "open", type: "button", "aria-label": t("card.open", { title: item.title }), onclick: () => opts.onOpen && opts.onOpen(item) });
  art.append(open);
  if (opts.actions && opts.actions.length) {
    art.append(h("div", { class: "poster-acts" }, opts.actions.map((a) =>
      h("button", { class: "btn small", type: "button", title: a.title || a.label, "aria-label": `${a.label}: ${item.title}`, onclick: () => a.onClick(item) }, icon(a.icon, 15), a.label))));
  }

  const origin = item.inQueue
    ? (item.source === "manual"
        ? h("span", { class: "src-manual", title: item.origin === "dashboard" ? t("card.srcDashboard") : t("card.srcStar") }, icon(item.origin === "dashboard" ? "trash" : "star", 13))
        : h("span", { class: "src-auto", title: t("card.srcAuto") }, icon("bolt", 13)))
    : null;
  const meta = h("div", { class: "p-meta" }, origin,
    h("span", null, `${typeLabel(item.media_type)}${item.size_bytes ? `, ${bytes(item.size_bytes)}` : ""}`),
    item.accelerated ? h("span", { class: "tag hot", title: t("card.acceleratedHint") }, t("card.accelerated")) : null);
  return h("article", { class: "poster" + (opts.selected ? " selected" : "") }, art, h("div", { class: "p-title" }, item.title || item.key), meta);
}
