import { h } from "../h.js";
import { LOCALES, getLocale, setLocale } from "../i18n.js";

/** EN | PL switch. The choice is stored in the browser. */
export function languageSwitch() {
  const el = h("div", { class: "seg lang-switch", role: "group", "aria-label": "Language" });
  for (const code of LOCALES) {
    el.append(h("button", { type: "button", lang: code, "aria-pressed": String(code === getLocale()), onclick: () => setLocale(code) }, code.toUpperCase()));
  }
  return el;
}
