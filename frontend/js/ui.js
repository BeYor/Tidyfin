import { h } from "./h.js";
import { icon } from "./icons.js";
import { t, errText } from "./i18n.js";

let toastHost;
export function toast(message, kind = "ok") {
  if (!toastHost) { toastHost = h("div", { class: "toasts", role: "status", "aria-live": "polite" }); document.body.append(toastHost); }
  const el = h("div", { class: `toast ${kind}` }, icon(kind === "bad" ? "alert" : "check", 18), h("div", null, message));
  toastHost.append(el);
  setTimeout(() => el.remove(), kind === "bad" ? 7000 : 3800);
}
/** Show an API error as a toast, translated by its code. */
export const toastError = (e) => toast(errText(e), "bad");

export function spinner() { return h("span", { class: "spinner", role: "progressbar", "aria-label": t("common.loading") }); }
export const loading = () => h("div", { class: "loading", role: "status", "aria-label": t("common.loadingBrand") },
  h("div", { class: "loader-cards", "aria-hidden": "true" }, h("i"), h("i"), h("i")));

export function emptyState(iconName, title, text, action) {
  return h("div", { class: "empty" }, icon(iconName), h("h2", null, title), text ? h("p", null, text) : null, action);
}

/** Modal dialog. Esc and a click on the backdrop close it. Returns { close, el }. */
export function dialog({ title, body, actions = [], wide = false, onClose }) {
  const prevFocus = document.activeElement;
  const foot = h("div", { class: "foot" });
  const box = h("div", { class: "dialog" + (wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-label": title },
    h("h2", null, title), h("div", { class: "body" }, body), foot);
  const scrim = h("div", { class: "scrim" }, box);
  const close = () => {
    document.removeEventListener("keydown", onKey);
    scrim.remove();
    if (prevFocus && prevFocus.focus) prevFocus.focus();
    if (onClose) onClose();
  };
  // With stacked dialogs, Escape closes only the topmost visible one.
  const onKey = (e) => {
    if (e.key !== "Escape") return;
    const open = [...document.querySelectorAll(".scrim")].filter((x) => !x.hidden);
    if (open[open.length - 1] === scrim) close();
  };
  scrim.addEventListener("mousedown", (e) => { if (e.target === scrim) close(); });
  document.addEventListener("keydown", onKey);
  for (const a of actions) {
    const btn = h("button", { class: `btn ${a.kind || ""}`, type: "button", disabled: a.disabled,
      onclick: async (e) => { const r = a.onClick ? await a.onClick(e, close) : undefined; if (r !== false && !a.keepOpen) close(); } }, a.label);
    foot.append(btn);
    a.el = btn;
  }
  document.body.append(scrim);
  (foot.querySelector("button:last-child") || box).focus();
  return { close, el: box };
}

export function confirmDialog({ title, text, confirmLabel, cancelLabel = t("common.cancel"), danger = false }) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (!settled) { settled = true; resolve(v); } };
    dialog({ title, body: h("p", null, text), onClose: () => done(false),
      actions: [{ label: cancelLabel, onClick: () => done(false) },
                { label: confirmLabel, kind: danger ? "danger solid" : "primary", onClick: () => done(true) }] });
  });
}

/** Button that is disabled while an async action runs. */
export function actionButton({ label, kind = "", iconName, onClick, small = false, title }) {
  const btn = h("button", { class: `btn ${kind} ${small ? "small" : ""}`, type: "button", title }, iconName ? icon(iconName) : null, label);
  btn.addEventListener("click", async () => {
    if (btn.disabled) return;
    btn.disabled = true;
    try { await onClick(btn); } finally { btn.disabled = false; }
  });
  return btn;
}

/** Segmented control. options: [[value, label], ...]; onChange(value). */
export function segmented(options, value, onChange, ariaLabel) {
  const el = h("div", { class: "seg", role: "group", "aria-label": ariaLabel });
  const paint = (v) => el.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === String(v))));
  for (const [v, label] of options) {
    el.append(h("button", { type: "button", dataset: { v: String(v) }, "aria-pressed": String(v === value),
      onclick: () => { paint(v); onChange(v); } }, label));
  }
  return el;
}
