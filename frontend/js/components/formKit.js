// Small form-control factory bound to a mutable "draft" object, used by the settings page.
import { h, clear } from "../h.js";
import { icon } from "../icons.js";
import { t } from "../i18n.js";
import { confirmDialog } from "../ui.js";

const getp = (o, p) => p.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
function setp(o, p, v) { const ks = p.split("."); const last = ks.pop(); ks.reduce((a, k) => a[k], o)[last] = v; }

export function makeKit(getDraft, touch) {
  let uid = 0;
  const nextId = () => `f-${++uid}`;

  function field(label, control, hint, labelFor) {
    return h("div", { class: "field" }, label ? h("label", { for: labelFor }, label) : null, control, hint ? h("div", { class: "hint" }, hint) : null);
  }
  function text(label, path, { hint, type = "text", placeholder = "", target } = {}) {
    const id = nextId();
    const obj = () => target || getDraft();
    return field(label, h("input", { class: "input", id, type, placeholder, value: getp(obj(), path) ?? "", oninput: (e) => { setp(obj(), path, e.target.value); touch(); } }), hint, id);
  }
  function num(label, path, { hint, min = 0, max, suffix, target } = {}) {
    const id = nextId();
    const obj = () => target || getDraft();
    const input = h("input", { class: "input short", id, type: "number", min, max, value: getp(obj(), path) ?? 0,
      oninput: (e) => { const n = parseInt(e.target.value, 10); setp(obj(), path, Number.isNaN(n) ? 0 : n); touch(); } });
    return field(label, h("div", { class: "input-row" }, input, suffix ? h("span", { class: "suffix" }, suffix) : null), hint, id);
  }
  function select(label, path, options, { hint, numeric = false, target } = {}) {
    const id = nextId();
    const obj = () => target || getDraft();
    const cur = getp(obj(), path);
    return field(label, h("select", { class: "input", id, onchange: (e) => { setp(obj(), path, numeric ? Number(e.target.value) : e.target.value); touch(); } },
      options.map(([v, l]) => h("option", { value: v, selected: String(v) === String(cur) }, l))), hint, id);
  }
  function toggle(label, desc, path, { confirmOff, target, onChange } = {}) {
    const obj = () => target || getDraft();
    const input = h("input", { type: "checkbox", checked: !!getp(obj(), path), onchange: async (e) => {
      if (confirmOff && !e.target.checked && !(await confirmDialog(confirmOff))) { e.target.checked = true; return; }
      setp(obj(), path, e.target.checked); touch(); if (onChange) onChange(e.target.checked);
    } });
    return h("label", { class: "switch" }, input, h("span", { class: "track" }), h("span", { class: "sw-text" }, h("b", null, label), desc ? h("span", null, desc) : null));
  }
  /** Chip input for a list of strings stored at `path`. **/
  function tags(label, path, hint) {
    const id = nextId();
    const wrap = h("div", { class: "tags" });
    const input = h("input", { id, placeholder: t("kit.addTag"), "aria-label": label });
    const paint = () => {
      clear(wrap);
      for (const tag of getp(getDraft(), path)) {
        wrap.append(h("span", { class: "tg" }, tag, h("button", { type: "button", "aria-label": t("kit.removeTag", { tag }), onclick: () => {
          setp(getDraft(), path, getp(getDraft(), path).filter((x) => x !== tag)); paint(); touch(); } }, icon("x", 13))));
      }
      wrap.append(input);
    };
    const add = () => {
      const v = input.value.trim().replace(/,$/, "");
      if (v && !getp(getDraft(), path).includes(v)) { getp(getDraft(), path).push(v); touch(); }
      input.value = ""; paint(); input.focus();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); }
      else if (e.key === "Backspace" && !input.value) { getp(getDraft(), path).pop(); paint(); touch(); }
    });
    input.addEventListener("blur", () => { if (input.value.trim()) add(); });
    paint();
    return field(label, wrap, hint, id);
  }
  /** Comma separated list stored as an array of strings at obj[key]. **/
  function wordList(label, obj, key, hint) {
    const id = nextId();
    return field(label, h("input", { class: "input", id, value: (obj[key] || []).join(", "),
      oninput: (e) => { obj[key] = e.target.value.split(",").map((s) => s.trim()).filter(Boolean); touch(); } }), hint, id);
  }
  return { field, text, num, select, toggle, tags, wordList, nextId, getp, setp };
}
