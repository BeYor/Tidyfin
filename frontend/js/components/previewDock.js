// Floating live preview for badge editing. It stays on screen while the badge settings are being edited
// (wherever they are on the page), shows a real poster from the library, and is drawn by the same server code as the real posters.
import { h } from "../h.js";
import { api } from "../api.js";
import { icon } from "../icons.js";
import { t } from "../i18n.js";

const KEY = "tidyfin.previewKey";
const read = () => { try { return localStorage.getItem(KEY) || ""; } catch { return ""; } };
const write = (v) => { try { localStorage.setItem(KEY, v); } catch { /* ignore */ } };

/** getBadges(): the draft badges to draw. Returns { el, refresh, show(bool), destroy }. */
export function previewDock(getBadges) {
  const img = h("img", { class: "dock-img", alt: t("badge.previewAlt"), width: 270, height: 405 });
  const select = h("select", { class: "input dock-select", "aria-label": t("dock.poster"), onchange: (e) => { key = e.target.value; write(key); refresh(); } },
    h("option", { value: "" }, t("dock.sample")));
  const note = h("p", { class: "dock-note" }, t("badge.previewNote"));
  const toggle = h("button", { class: "btn icon small dock-toggle", type: "button", "aria-label": t("dock.collapse"), "aria-expanded": "true", onclick: () => {
    const open = el.classList.toggle("collapsed") === false;
    toggle.setAttribute("aria-expanded", String(open));
  } }, icon("x", 14));
  const el = h("aside", { class: "dock", hidden: true, "aria-label": t("dock.title") },
    h("div", { class: "dock-head" }, h("b", null, t("dock.title")), toggle),
    h("div", { class: "dock-body" }, img, select, note));

  let hideTimer = null, key = read(), timer = null, url = null, seq = 0, alive = true, titles = [];

  api.get("/library?limit=200&sort=title").then((res) => {
    if (!alive) return;
    titles = res.items || [];
    for (const it of titles) select.append(h("option", { value: it.key }, it.title));
    if (!key && titles.length) key = titles[0].key;  // default: a real poster from the library
    if (key && !titles.some((x) => x.key === key)) key = "";
    select.value = key;
    refresh();
  }).catch(() => refresh());

  function refresh() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const mine = ++seq;
      try {
        const blob = await api.blob(`/badges/preview?w=360${key ? `&key=${encodeURIComponent(key)}` : ""}`, { badges: getBadges() });
        if (mine !== seq || !alive) return;
        const next = URL.createObjectURL(blob);
        img.src = next;
        if (url) URL.revokeObjectURL(url);
        url = next;
      } catch { /* the preview is best-effort */ }
    }, 220);
  }

  return {
    el, refresh,
    show(on) {
      clearTimeout(hideTimer);
      if (on) el.hidden = false;
      else hideTimer = setTimeout(() => { el.hidden = true; }, 220);
    },
    destroy() { alive = false; clearTimeout(timer); clearTimeout(hideTimer); if (url) URL.revokeObjectURL(url); el.remove(); },
  };
}
