// "Delete in how many days?" and "Postpone by..." dialogs: preset chips, a custom
// number of days, or an exact date from a calendar - with a live preview of the result.
import { h } from "../h.js";
import { t, tn } from "../i18n.js";
import { dateOnly, isoDay } from "../fmt.js";
import { dialog } from "../ui.js";

const DAY = 864e5;

/**
 * mode: "schedule" (days from today, 0 = next cycle) | "postpone" (days added to the current deadline) | "keep" (protected until that day)
 * currentDeadline: ISO string, used for the postpone preview
 * onSubmit({days} | {date}) -> Promise
**/
export function scheduleDialog({ mode, title, intro, currentDeadline, dryRun, onSubmit, count = 1, onClose }) {
  const presets = mode === "schedule" ? [0, 1, 3, 7, 14, 30] : mode === "keep" ? [14, 30, 60, 90, 180, 365] : [3, 7, 14, 30, 60];
  let days = presets[mode === "schedule" ? 3 : mode === "keep" ? 1 : 1];
  let date = "";
  let source = "days";  // which input is authoritative: "days" or "date"

  const preview = h("p", { class: "sched-preview", "aria-live": "polite" });
  const chips = h("div", { class: "chips", role: "group" });
  const custom = h("input", { class: "input", type: "number", min: mode === "schedule" ? 0 : 1, max: 3650, value: days, "aria-label": t("sched.customDays"), style: { maxWidth: "110px" } });
  const dateInput = h("input", { class: "input", type: "date", min: isoDay(new Date()), "aria-label": t("sched.pickDate") });

  function base() {
    return mode === "postpone" && currentDeadline ? Math.max(new Date(currentDeadline).getTime(), Date.now()) : Date.now();
  }
  function target() {
    if (source === "date" && date) return new Date(date + "T00:00:00");
    const d = new Date(base() + days * DAY);
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }
  function paint() {
    preview.textContent = "";
    const deadlineText = source === "date" && !date ? "" : dateOnly(target());
    if (source === "days" && mode === "schedule" && days === 0) preview.append(t("sched.nextCycle"));
    else if (deadlineText) preview.append(mode === "schedule" ? t("sched.deletionOn", { date: deadlineText }) : mode === "keep" ? t("sched.keepUntil", { date: deadlineText }) : t("sched.newDeadline", { date: deadlineText }));
    if (dryRun && mode !== "keep") preview.append(h("span", { class: "muted" }, " " + t("sched.dryRunNote")));
    chips.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(source === "days" && Number(b.dataset.days) === days)));
  }
  for (const n of presets) {
    chips.append(h("button", { type: "button", class: "chip-btn", dataset: { days: String(n) }, onclick: () => {
      source = "days"; days = n; custom.value = n; dateInput.value = ""; date = ""; paint();
    } }, n === 0 && mode === "schedule" ? t("sched.next") : tn("sched.chipDays", n)));
  }
  custom.addEventListener("input", () => { source = "days"; days = Math.max(mode === "schedule" ? 0 : 1, parseInt(custom.value, 10) || 0); dateInput.value = ""; date = ""; paint(); });
  dateInput.addEventListener("input", () => { date = dateInput.value; source = date ? "date" : "days"; paint(); });

  const body = h("div", null,
    intro ? h("p", { style: { marginBottom: "16px" } }, intro) : null,
    chips,
    h("div", { class: "sched-row" },
      h("label", null, t("sched.orDays")), custom,
      h("label", null, t("sched.orDate")), dateInput),
    preview);
  paint();

  return dialog({ title, body, onClose, actions: [
    { label: t("common.cancel") },
    { label: mode === "schedule" ? (count > 1 ? t("sched.confirmSchedulePlural", { n: count }) : t("sched.confirmSchedule")) : mode === "keep" ? t("sched.confirmKeep") : t("sched.confirmPostpone"),
      kind: mode === "schedule" ? "danger solid" : "primary", keepOpen: true,
      onClick: async (e, close) => {
        e.currentTarget.disabled = true;
        try { await onSubmit(source === "date" && date ? { date } : { days }); close(); }
        catch { e.currentTarget.disabled = false; }
        return false;
      } }] });
}
