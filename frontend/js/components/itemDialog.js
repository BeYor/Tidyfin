// Detail dialog for one title: status, last play, and every action (keep, delete in..., postpone, cancel).
import { h, clear } from "../h.js";
import { api, enc } from "../api.js";
import { icon } from "../icons.js";
import { t, tn } from "../i18n.js";
import { bytes, dateOnly, relTime, typeLabel } from "../fmt.js";
import { actionButton, dialog, loading, toast, toastError } from "../ui.js";
import { scheduleDialog } from "./scheduleDialog.js";
import { openLanguageTags, languageSummary } from "./languageTags.js";

/** Open the dialog for `key`. `onChange()` runs after any successful action. **/
export function openItemDialog(key, app, onChange) {
  const body = h("div", null, loading());
  const dlg = dialog({ title: t("item.loadingTitle"), body, wide: true, actions: [{ label: t("common.close"), kind: "primary" }] });
  const titleEl = dlg.el.querySelector("h2");

  // The second dialog replaces the first visually: hide this one while it is open.
  function openChild(opts) {
    const scrim = dlg.el.parentElement;
    scrim.hidden = true;
    return scheduleDialog({ ...opts, onClose: () => { scrim.hidden = false; } });
  }

  function openTags(opts) {
    const scrim = dlg.el.parentElement;
    scrim.hidden = true;
    const child = openLanguageTags({ ...opts, onClose: () => { scrim.hidden = false; } });
    if (!child) scrim.hidden = false;
  }

  async function act(promise, okMsg, close = true) {
    try { await promise; toast(okMsg); if (close) dlg.close(); await onChange(); app.refreshStatus(); }
    catch (e) { toastError(e); throw e; }
  }

  Promise.all([api.get(`/items/${enc(key)}`), api.get("/settings").catch(() => null)]).then(([d, cfg]) => {
    const dryRun = app.status && app.status.dry_run;
    const q = d.queue;
    titleEl.textContent = d.year ? `${d.title} (${d.year})` : d.title;
    clear(body);

    const lastPlayed = !d.last_played_checked ? t("item.lastUnknown") : d.last_played ? relTime(d.last_played) : t("item.lastNever");
    const state = d.protected
      ? h("span", { class: "tag ok" }, icon("shield", 12), t("item.stateKept"))
      : q ? h("span", { class: "tag hot" }, t("item.stateQueued", { date: dateOnly(q.delete_at) }))
          : h("span", { class: "tag" }, t("item.stateFree"));
    const rows = [
      [t("item.status"), state],
      [t("item.type"), `${typeLabel(d.media_type)}${d.size_bytes ? `, ${bytes(d.size_bytes)}` : ""}`],
      [t("item.lastPlayed"), lastPlayed],
      [t("item.tags"), d.tags.length ? h("span", { class: "tag-list" }, d.tags.map((x) => h("span", { class: "tag" }, x))) : t("common.none")],
    ];
    if (d.protected && d.keep_until) rows.push([t("item.keptUntil"), dateOnly(d.keep_until)]);
    if (d.language && d.language.profiles.length) rows.push([t("item.language"), languageSummary(d.language)]);
    if (q) rows.splice(1, 0, [t("item.source"), q.source === "auto" ? t("item.srcAuto") : q.origin === "dashboard" ? t("card.srcDashboard") : t("card.srcStar")]);

    const web = (d.jellyfin_web_url || "").replace(/\/$/, "");
    const actions = h("div", { class: "item-actions" });
    if (d.protected) {
      actions.append(actionButton({ label: t("item.unprotect"), iconName: "shield", onClick: () => act(api.del(`/items/${enc(key)}/protect`), t("toast.unprotected")) }));
    } else {
      actions.append(
        actionButton({ label: t("item.keep"), kind: "primary", iconName: "shield", title: t("item.keepHint"), onClick: () => act(api.post(`/items/${enc(key)}/protect`), t("toast.kept", { title: d.title })) }),
        h("button", { class: "btn", type: "button", title: t("item.keepUntilHint"), onclick: () => openChild({
          mode: "keep", title: t("sched.titleKeep"), intro: t("sched.introKeep", { title: d.title }), dryRun,
          onSubmit: (v) => act(api.post(`/items/${enc(key)}/protect`, v), t("toast.keptUntil", { title: d.title })) }) }, icon("clockplus", 16), t("item.keepUntil")),
        h("button", { class: "btn danger", type: "button", onclick: () => openChild({
          mode: "schedule", title: t("sched.titleSchedule"), intro: t("sched.introSchedule", { title: d.title }), dryRun,
          onSubmit: (v) => act(api.post(`/items/${enc(key)}/schedule`, v), t("toast.scheduled", { title: d.title })) }) }, icon("trash", 16), t("item.deleteIn")));
    }
    if (q) {
      actions.append(h("button", { class: "btn", type: "button", onclick: () => openChild({
        mode: "postpone", title: t("sched.titlePostpone"), intro: t("sched.introPostpone", { title: d.title, date: dateOnly(q.delete_at) }), currentDeadline: q.delete_at, dryRun,
        onSubmit: (v) => act(api.post(`/items/${enc(key)}/postpone`, v), t("toast.postponed")) }) }, icon("clockplus", 16), t("item.postpone")));
      if (q.source === "manual") {
        actions.append(actionButton({ label: t("item.cancelSchedule"), iconName: "x", onClick: () => act(api.del(`/items/${enc(key)}/schedule`), t("toast.cancelled")) }));
      }
    }
    const langProfiles = (cfg?.language?.profiles || []).filter((p) => p.enabled);
    if (d.jellyfin_id && langProfiles.length) {
      actions.append(h("button", { class: "btn", type: "button", onclick: () => openTags({ key, title: d.title, profiles: langProfiles, onDone: () => {} }) }, icon("language", 16), t("item.languageTags")));
    }
    if (web && d.jellyfin_id) {
      actions.append(h("a", { class: "btn", href: `${web}/web/#/details?id=${d.jellyfin_id}`, target: "_blank", rel: "noopener" }, icon("link", 16), t("item.openJellyfin")));
    }

    body.append(h("div", { class: "item-grid" },
      h("img", { class: "item-poster", src: `/api/poster/${enc(key)}?w=300`, alt: "", onerror: (e) => e.target.remove() }),
      h("div", null,
        h("dl", { class: "kv" }, rows.map(([k, v]) => [h("dt", null, k), h("dd", null, v)])),
        d.protected ? null : h("p", { class: "muted small" }, t("item.keepHint")),
        actions)));
  }).catch((e) => { clear(body); body.append(h("div", { class: "err" }, e.message)); toastError(e); });
  return dlg;
}
