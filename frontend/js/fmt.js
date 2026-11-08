// Locale-aware formatting helpers.
import { getLocale, t, tn } from "./i18n.js";

const nf = () => new Intl.NumberFormat(getLocale(), { maximumFractionDigits: 1 });

export function bytes(n) {
  n = Number(n) || 0;
  if (n < 1000) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let i = -1;
  do { n /= 1000; i++; } while (n >= 1000 && i < units.length - 1);
  return `${nf().format(n)} ${units[i]}`;
}

export function relTime(iso) {
  if (!iso) return t("time.never");
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return t("time.justNow");
  const rtf = new Intl.RelativeTimeFormat(getLocale(), { numeric: "auto" });
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}

export function duration(sec) {
  sec = Math.round(Number(sec) || 0);
  if (sec < 60) return t("time.seconds", { n: sec });
  const m = Math.floor(sec / 60), s = sec % 60;
  return s ? t("time.minSec", { m, s }) : t("time.minutes", { m });
}

export const dateTime = (iso) => new Date(iso).toLocaleString(getLocale(), { dateStyle: "medium", timeStyle: "short" });
export const dateOnly = (iso) => new Date(iso).toLocaleDateString(getLocale(), { dateStyle: "medium" });
export const timeOnly = (iso) => new Date(iso).toLocaleTimeString(getLocale(), { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** Short label for the deadline pill on a poster. */
export function whenLabel(item) {
  if (item.due) return t("when.overdue");
  if (item.days_left <= 0) return t("when.lastDay");
  return tn("when.days", item.days_left);
}

/** 0..1: how much of the grace period has passed (drives the poster fade). */
export function urgency(item) {
  const end = new Date(item.delete_at).getTime();
  const start = item.queued_at ? new Date(item.queued_at).getTime() : end - 14 * 864e5;
  if (!(end > start)) return 1;
  return Math.min(1, Math.max(0, (Date.now() - start) / (end - start)));
}

export const typeLabel = (type) => (type === "movie" ? t("type.movie") : type === "series" ? t("type.series") : "");

/** yyyy-mm-dd for <input type="date"> from a Date. */
export const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
