// Dialog that writes the manual language tags of one title to Jellyfin.
import { h } from "../h.js";
import { api, enc } from "../api.js";
import { t, errText } from "../i18n.js";
import { dialog, toast } from "../ui.js";

const KINDS = ["voiceover", "dub", "subs", "audio"];

/** One line per language Tidyfin has an opinion about: what was found, the badge it gives, and which layers found it. */
export function languageSummary(language, { onlyCode } = {}) {
  const rows = (language?.profiles || []).filter((p) => (!onlyCode || p.code === onlyCode) && (p.badge || p.dismissed || p.uncertain.length || p.audio.length || p.subs));
  if (!rows.length) return h("span", { class: "muted" }, t("ltag.detectedNone"));
  return h("div", { class: "lang-found" }, rows.map((p) => {
    const found = [...p.audio.map((k) => t(`kind.${k}`)), ...(p.subs ? [t("kind.subs")] : [])];
    let text;
    if (p.dismissed) text = t("ltag.foundDismissed");
    else if (found.length) text = `${found.join(" + ")}${p.text ? `, ${t("ltag.badgeIs", { text: p.text })}` : ""}`;
    else text = t("ltag.foundHint", { hints: p.uncertain.join(", ") });
    const src = p.sources.length ? ` (${p.sources.map((x) => t(`ltag.src.${x}`)).join(", ")})` : "";
    return h("div", null, h("b", null, `${p.name}: `), text, h("span", { class: "muted small" }, src));
  }));
}

/**
 * key: item key; title: shown in the intro; profiles: enabled language profiles;
 * preset: { profile, kinds, dismiss } to start from; onDone(): after a successful save.
**/
export function openLanguageTags({ key, title, profiles, preset = {}, onDone, onClose }) {
  if (!profiles.length) return null;
  let profile = profiles.find((p) => p.code === preset.profile) || profiles[0];
  const picked = new Set(preset.kinds || []);
  let dismiss = !!preset.dismiss;
  const list = h("div", { class: "stack", style: { gap: "10px" } });
  const hint = h("p", { class: "muted small" });
  const found = h("div", { class: "found-box" }, h("span", { class: "muted" }, t("ltag.detectedLoading")));
  let language = null;
  const paintFound = () => { found.replaceChildren(h("b", { class: "found-title" }, t("ltag.detected")), languageSummary(language, { onlyCode: profile.code })); };
  // What Tidyfin sees right now becomes the starting point of the checkboxes (unless the caller preset them).
  const prefill = () => {
    const row = (language?.profiles || []).find((p) => p.code === profile.code);
    picked.clear();
    dismiss = false;
    if (row && !preset.kinds) { dismiss = row.dismissed; (row.audio || []).forEach((k) => picked.add(k)); if (row.subs) picked.add("subs"); }
    else (preset.kinds || []).forEach((k) => picked.add(k));
  };

  const paint = () => {
    const tags = (k) => profile.tags?.[k]?.[0] || `${k === "subs" ? "subs" : k}-${profile.short}`;
    const dis = profile.tags?.dismiss?.[0] || `no-${profile.short}`;
    list.replaceChildren(...KINDS.map((k) => h("label", { class: "check" },
      h("input", { type: "checkbox", checked: picked.has(k) && !dismiss, disabled: dismiss, onchange: (e) => { e.target.checked ? picked.add(k) : picked.delete(k); } }),
      h("span", null, t(`kind.${k}`), " ", h("span", { class: "muted small" }, `(${tags(k)})`)))),
      h("label", { class: "check" },
        h("input", { type: "checkbox", checked: dismiss, onchange: (e) => { dismiss = e.target.checked; paint(); } }),
        h("span", null, t("ltag.dismiss", { name: profile.name }), " ", h("span", { class: "muted small" }, `(${dis})`))));
    hint.textContent = t("ltag.hint");
  };
  const sel = profiles.length > 1 ? h("select", { class: "input", "aria-label": t("ltag.language"), onchange: (e) => { profile = profiles.find((p) => p.code === e.target.value); prefill(); paintFound(); paint(); } },
    profiles.map((p) => h("option", { value: p.code, selected: p === profile }, `${p.name} (${p.native})`))) : null;
  paint();
  api.get(`/items/${enc(key)}`).then((d) => { language = d.language; prefill(); paintFound(); paint(); }).catch(() => { found.replaceChildren(); });

  return dialog({
    onClose,
    title: t("ltag.title"),
    body: h("div", { class: "stack", style: { gap: "14px" } }, h("p", null, t("ltag.intro", { title })), found, sel, list, hint),
    actions: [{ label: t("common.cancel") }, { label: t("ltag.write"), kind: "primary", onClick: async () => {
      try {
        await api.post(`/items/${enc(key)}/language-tags`, { profile: profile.code, kinds: [...picked], dismiss });
        toast(t("ltag.saved"));
        if (onDone) onDone();
      } catch (e) { toast(errText(e), "bad"); return false; }
    } }],
  });
}
