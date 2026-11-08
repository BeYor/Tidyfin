import { h, clear } from "../h.js";
import { api } from "../api.js";
import { icon } from "../icons.js";
import { t, tn } from "../i18n.js";
import { relTime } from "../fmt.js";
import { emptyState, loading } from "../ui.js";
import { pageHead } from "./common.js";
import { openLanguageTags } from "../components/languageTags.js";

export function mount(root, app) {
  let alive = true;
  const body = h("div", null, loading());
  root.append(pageHead(t("nav.language")), body);

  const load = () => Promise.all([api.get("/language/report"), api.get("/status"), api.get("/settings")]).then(([rep, status, settings]) => {
    if (!alive) return;
    clear(body);
    const profiles = (settings.language.profiles || []).filter((p) => p.enabled);
    if (!profiles.length) { body.append(emptyState("language", t("lang.noneTitle"), t("lang.noneText"), h("a", { class: "btn", href: "#/settings" }, icon("settings", 16), t("ov.openSettings")))); return; }
    if (!rep.updated_at) { body.append(emptyState("language", t("lang.notYetTitle"), t("lang.notYetText"))); return; }

    const web = (status.jellyfin_web_url || "").replace(/\/$/, "");
    body.append(h("p", { class: "lead-sub", style: { marginBottom: "22px" } }, t("lang.summary", { items: tn("count.items", rep.labeled), when: relTime(rep.updated_at) })));

    const byProfile = rep.by_profile || {};
    const bars = profiles.map((p) => {
      const counts = byProfile[p.code] || {};
      const total = Object.values(counts).reduce((a, b) => a + b, 0);
      const max = Math.max(1, ...Object.values(counts));
      return h("div", { class: "lang-profile" },
        h("h3", null, `${p.name} `, h("span", { class: "muted" }, `(${total})`)),
        total ? h("div", { class: "lang-bars" }, Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([kind, n]) =>
          h("div", { class: "lang-bar" }, h("span", null, p.badges[kind]?.text || kind), h("div", { class: "bar" }, h("i", { style: { width: `${(n / max) * 100}%` } })), h("b", null, String(n)))))
          : h("p", { class: "muted small" }, t("lang.noneFound")));
    });
    body.append(h("section", { class: "section first" }, h("h2", null, t("lang.badges")), h("div", { class: "lang-grid" }, bars)));

    const unc = rep.uncertain || [];
    const tagHelp = profiles.map((p) => `${p.name}: ${["voiceover", "dub", "subs", "audio"].map((k) => (p.tags?.[k]?.[0] || `${k}-${p.short}`)).join(", ")}, ${p.tags?.dismiss?.[0] || `no-${p.short}`}`);
    
    body.append(h("section", { class: "section" }, h("h2", null, t("lang.review")),
      h("p", { class: "lede" }, t("lang.reviewText")), h("p", { class: "lede small" }, t("lang.tagHelp"), " ", tagHelp.join(" | ")),
      unc.length ? h("div", { class: "rows" }, unc.map((u) => h("div", { class: "row" },
        h("div", { class: "grow" }, h("div", { class: "t" }, u.title), h("div", { class: "s t" }, (u.hints || []).join("; "))),
        h("span", { class: "tag lang" }, profiles.find((p) => p.code === u.profile)?.name || u.profile),
        u.key ? h("button", { class: "btn small", type: "button", onclick: () => openLanguageTags({ key: u.key, title: u.title, profiles, preset: { profile: u.profile }, onDone: load }) }, icon("language", 14), t("ltag.set")) : null,
        web && u.jellyfin_id ? h("a", { class: "btn small", href: `${web}/web/#/details?id=${u.jellyfin_id}`, target: "_blank", rel: "noopener" }, icon("link", 14), t("item.openJellyfin")) : null)))
        : h("div", { class: "empty left" }, icon("check", 28), h("p", null, t("lang.allClear")))));
  }).catch((e) => { clear(body); body.append(h("div", { class: "err" }, e.message)); });
  load();
  return () => { alive = false; };
}
