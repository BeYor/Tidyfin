import { h } from "../h.js";
import { t } from "../i18n.js";

export function pageHead(title, ...actions) {
  return h("header", { class: "page-head" }, h("h1", null, title), h("div", { class: "actions" }, ...actions));
}

export function modeChip(dryRun) {
  return dryRun
    ? h("span", { class: "chip safe", title: t("mode.dryHint") }, h("span", { class: "dot ok" }), t("mode.dry"))
    : h("span", { class: "chip live", title: t("mode.liveHint") }, h("span", { class: "dot bad" }), t("mode.live"));
}

export const EVENT_CLASS = {
  queued: "queued", dequeued: "ok", deleted: "deleted", delete_failed: "failed", marked: "queued", scheduled: "queued",
  protected: "ok", unprotected: "", postponed: "", accelerated: "hot", language_tagged: "",
};
export const EVENTS = Object.keys(EVENT_CLASS);
export const eventTag = (ev) => h("span", { class: `tag ${EVENT_CLASS[ev] || ""}` }, t(`event.${ev}`));
