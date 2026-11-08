// Tiny i18n layer: dictionaries are plain objects, plural forms use Intl.PluralRules.
import en from "./locales/en.js";
import pl from "./locales/pl.js";

const DICTS = { en, pl };
export const LOCALES = Object.keys(DICTS);
const STORAGE_KEY = "tidyfin.lang";
const listeners = new Set();

function detect() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && DICTS[saved]) return saved;
  } catch { /* storage may be blocked */ }
  const nav = (navigator.language || "en").slice(0, 2).toLowerCase();
  return DICTS[nav] ? nav : "en";
}

let locale = detect();
document.documentElement.lang = locale;

export const getLocale = () => locale;

export function setLocale(next) {
  if (!DICTS[next] || next === locale) return;
  locale = next;
  document.documentElement.lang = next;
  try { localStorage.setItem(STORAGE_KEY, next); } catch { /* ignore */ }
  listeners.forEach((fn) => fn(next));
}

export const onLocaleChange = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };

const fill = (s, params) => (params ? s.replace(/\{(\w+)\}/g, (_, k) => (params[k] ?? "")) : s);

/** Translate a key; falls back to English, then to the key itself. */
export function t(key, params) {
  return fill(DICTS[locale][key] ?? DICTS.en[key] ?? key, params);
}

/** Does the current dictionary (or English) define this key? */
export const hasKey = (key) => key in DICTS[locale] || key in DICTS.en;

/** Plural-aware translation: looks up `key.one|few|many|other` for the number n. */
export function tn(key, n, params) {
  const rule = new Intl.PluralRules(locale).select(n);
  const s = DICTS[locale][`${key}.${rule}`] ?? DICTS[locale][`${key}.other`] ?? DICTS.en[`${key}.${rule}`] ?? DICTS.en[`${key}.other`] ?? key;
  return fill(s, { n, ...params });
}

/** Message for an API error: translated by its code when we know it. */
export function errText(e) {
  if (e && e.code && hasKey(`err.${e.code}`)) return t(`err.${e.code}`);
  return (e && e.message) || t("err.unknown");
}
