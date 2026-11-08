// Editor for one language profile: matching rules and the four badges.
import { h } from "../h.js";
import { icon } from "../icons.js";
import { t } from "../i18n.js";
import { confirmDialog } from "../ui.js";
import { badgeEditor } from "./badgeEditor.js";

const KINDS = ["voiceover", "dub", "subs", "audio"];

/** ctx: { kit, presets, touch(), focus(cfg), remove(profile) } */
export function profileEditor(profile, ctx) {
  const { kit } = ctx;
  const tagNames = KINDS.map((k) => (profile.tags?.[k]?.[0] || `${k}-${profile.short}`));
  const dismiss = profile.tags?.dismiss?.[0] || `no-${profile.short}`;

  const head = h("summary", { class: "profile-head" },
    h("span", { class: "ph-name" }, h("b", null, profile.name), h("span", { class: "muted" }, ` ${profile.native}`)),
    h("label", { class: "switch compact", onclick: (e) => e.stopPropagation(), title: t("profile.enabled") },
      h("input", { type: "checkbox", checked: profile.enabled, "aria-label": `${profile.name}: ${t("profile.enabled")}`, onchange: (e) => { profile.enabled = e.target.checked; ctx.touch(); } }), h("span", { class: "track" })),
    h("button", { class: "btn small danger", type: "button", onclick: async (e) => {
      e.preventDefault(); e.stopPropagation();
      if (await confirmDialog({ title: t("profile.removeTitle", { name: profile.name }), text: t("profile.removeText"), confirmLabel: t("profile.remove"), danger: true })) ctx.remove(profile);
    } }, icon("trash", 14), t("profile.remove")));

  const rules = h("div", { class: "grid-2" },
    kit.wordList(t("profile.codes"), profile, "codes", t("profile.codesHint")),
    kit.wordList(t("profile.hints"), profile, "hints", t("profile.hintsHint")),
    kit.wordList(t("profile.voiceover"), profile, "voiceover_words", t("profile.voiceoverHint")),
    kit.wordList(t("profile.dub"), profile, "dub_words"),
    kit.wordList(t("profile.subs"), profile, "subs_words"),
    h("div", { class: "field" }, h("label", null, t("profile.tags")),
      h("div", { class: "tag-list" }, [...tagNames, dismiss].map((x, i) => h("span", { class: "tag" + (i === tagNames.length ? " hot" : "") }, x))),
      h("div", { class: "hint" }, t("profile.tagsHint"))));
  const names = h("div", { class: "grid-3" }, kit.text(t("profile.name"), "name", { target: profile }), kit.text(t("profile.native"), "native", { target: profile }), kit.text(t("profile.short"), "short", { target: profile }));

  const rows = KINDS.map((k) => badgeEditor(t(`kind.${k}`), profile.badges[k], ctx, { hint: t(`kind.${k}.hint`) }));
  return h("details", { class: "profile" }, head, h("div", { class: "profile-body" }, names, rules, h("h4", null, t("profile.badges")), h("div", { class: "badge-list" }, rows)));
}
