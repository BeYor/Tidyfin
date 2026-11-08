// Floating action bar for a multi-selection of posters (queue and library views).
import { h, clear } from "../h.js";
import { api } from "../api.js";
import { icon } from "../icons.js";
import { t, tn } from "../i18n.js";
import { toast, toastError } from "../ui.js";
import { scheduleDialog } from "./scheduleDialog.js";

/**
 * selection: Set of keys. ctx: { app, onDone(), dryRun }.
 * Returns { el, refresh() } - call refresh() whenever the selection changes.
**/
export function bulkBar(selection, ctx) {
  const el = h("div", { class: "bulkbar", hidden: true });

  async function run(action, params, doneKey) {
    const keys = [...selection];
    try {
      const res = await api.post("/items/bulk", { action, keys, params });
      toast(res.failed ? t("bulk.partial", { ok: res.ok, failed: res.failed }) : t(doneKey, { n: res.ok }), res.failed ? "bad" : "ok");
      selection.clear();
      await ctx.onDone();
      ctx.app.refreshStatus();
    } catch (e) { toastError(e); throw e; }
  }

  function refresh() {
    clear(el);
    el.hidden = selection.size === 0;
    if (!selection.size) return;
    el.append(h("div", null,
      h("strong", null, tn("bulk.selected", selection.size)),
      h("button", { class: "btn small", type: "button", onclick: () => { selection.clear(); ctx.onDone(); } }, t("bulk.clear")),
      h("button", { class: "btn small primary", type: "button", onclick: () => run("protect", {}, "bulk.kept") }, icon("shield", 15), t("item.keep")),
      h("button", { class: "btn small", type: "button", onclick: () => scheduleDialog({
        mode: "postpone", title: t("sched.titlePostpone"), intro: t("sched.introBulkPostpone", { n: selection.size }), dryRun: ctx.dryRun,
        onSubmit: (v) => run("postpone", v, "bulk.postponed") }) }, icon("clockplus", 15), t("item.postpone")),
      h("button", { class: "btn small danger", type: "button", onclick: () => scheduleDialog({
        mode: "schedule", title: t("sched.titleSchedule"), intro: t("sched.introBulkSchedule", { n: selection.size }), dryRun: ctx.dryRun, count: selection.size,
        onSubmit: (v) => run("schedule", v, "bulk.scheduled") }) }, icon("trash", 15), t("item.deleteIn"))));
  }
  return { el, refresh };
}
