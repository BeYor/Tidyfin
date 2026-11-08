// Editor for one badge (text, corner, order, style). The live preview is the floating
// dock (previewDock.js), so it stays visible wherever on the page a badge is edited.
import { h } from "../h.js";
import { t } from "../i18n.js";

const CORNERS = ["top-left", "top-right", "bottom-left", "bottom-right", "top-center", "bottom-center"];
const PRESET_NAMES = ["glass", "tint", "frost", "solid", "gradient", "neon", "outline"];
const SWATCHES = ["#FF4967", "#6048F5", "#12D9B0", "#269FE8", "#F5B83D", "#7892B5", "#F3F7FB", "#070D16"];
const BORDER_ONLY = new Set(["neon", "outline"]);        // presets whose accent is the border, not the fill

function contrastFg(hex) {
  const n = parseInt(hex.slice(1), 16);
  const lum = (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
  return lum > 0.6 ? "#0A1522" : "#FFFFFF";
}

function mix(hex, other, amount) {
  const a = parseInt(hex.slice(1), 16), b = parseInt(other.slice(1), 16);
  const ch = (sh) => Math.round(((a >> sh) & 255) * (1 - amount) + ((b >> sh) & 255) * amount);
  return "#" + [16, 8, 0].map((sh) => ch(sh).toString(16).padStart(2, "0")).join("").toUpperCase();
}

/**
 * cfg: the badge config object from the draft (mutated in place).
 * ctx: { presets, touch(), focus(cfg) }
**/
export function badgeEditor(label, cfg, ctx, { hint } = {}) {
  const s = cfg.style;
  const changed = () => { ctx.touch(); ctx.focus(cfg); };
  const custom = () => { s.preset = "custom"; presetSel.value = "custom"; };

  const colorInput = (key, lab) => h("label", { class: "color" }, h("span", null, lab),
    h("input", { type: "color", value: s[key] || "#000000", oninput: (e) => { s[key] = e.target.value.toUpperCase(); custom(); changed(); } }));
  const range = (key, lab, min, max, step, { styleKey = true } = {}) => h("label", { class: "range" }, h("span", null, lab),
    h("input", { type: "range", min, max, step, value: s[key], oninput: (e) => { s[key] = parseFloat(e.target.value); if (styleKey) custom(); changed(); } }));

  const gradientToggle = () => h("div", { class: "color" }, h("span", null, t("badge.gradient")),
    h("div", { class: "grad-row" },
      h("input", { type: "checkbox", checked: !!s.gradient, "aria-label": t("badge.gradient"), onchange: (e) => {
        s.gradient = e.target.checked ? (s.gradient || mix(s.bg, "#FFFFFF", 0.35)) : ""; custom(); buildAdvanced(); changed(); } }),
      s.gradient ? h("input", { type: "color", value: s.gradient, oninput: (e) => { s.gradient = e.target.value.toUpperCase(); custom(); changed(); } }) : null));

  const presetSel = h("select", { class: "input", "aria-label": t("badge.preset"), onchange: (e) => {
    const name = e.target.value;
    if (name !== "custom") Object.assign(s, ctx.presets[name], { preset: name });
    else s.preset = "custom";
    buildAdvanced(); changed();
  } }, [...PRESET_NAMES, "custom"].map((n) => h("option", { value: n, selected: s.preset === n }, t(`badge.preset.${n}`))));

  const swatchRow = h("div", { class: "swatches", role: "group", "aria-label": t("badge.accent") },
    SWATCHES.map((c) => h("button", { type: "button", class: "swatch", style: { background: c }, title: c, "aria-label": c, onclick: () => {
      if (BORDER_ONLY.has(s.preset)) { s.border = c; s.border_opacity = 1; s.border_width = Math.max(s.border_width, 2); }
      else {
        s.bg = c;
        if (s.preset === "solid" || s.preset === "gradient" || s.preset === "custom") { s.bg_opacity = Math.max(s.bg_opacity, 0.9); s.fg = contrastFg(c); }
        else { s.border = mix(c, "#FFFFFF", 0.55); s.fg = s.preset === "frost" ? contrastFg(mix(c, "#FFFFFF", 0.5)) : "#FFFFFF"; }   // tinted glass keeps its glassy look
        if (s.gradient) s.gradient = mix(c, "#FFFFFF", 0.35);
      }
      buildAdvanced(); changed();
    } })));

  const advanced = h("div", { class: "badge-adv" });
  function buildAdvanced() {
    advanced.replaceChildren(
      colorInput("bg", t("badge.bg")), colorInput("fg", t("badge.fg")), colorInput("border", t("badge.border")), gradientToggle(),
      range("bg_opacity", t("badge.bgOpacity"), 0, 1, 0.05), range("border_opacity", t("badge.borderOpacity"), 0, 1, 0.05),
      range("border_width", t("badge.borderWidth"), 0, 6, 1), range("blur", t("badge.blur"), 0, 100, 5),
      range("shine", t("badge.shine"), 0, 1, 0.05), range("glow", t("badge.glow"), 0, 1, 0.05), range("shadow", t("badge.shadow"), 0, 1, 0.05),
      range("size", t("badge.size"), 3, 12, 0.5, { styleKey: false }), range("radius", t("badge.radius"), 0, 50, 5, { styleKey: false }),
      h("label", { class: "range" }, h("span", null, t("badge.weight")), h("select", { class: "input", onchange: (e) => { s.weight = e.target.value; changed(); } },
        ["bold", "regular"].map((w) => h("option", { value: w, selected: s.weight === w }, t(`badge.weight.${w}`))))));
  }
  buildAdvanced();

  return h("div", { class: "badge-row", onfocusin: () => ctx.focus(cfg) },
    h("div", { class: "badge-head" },
      h("label", { class: "switch compact", title: t("badge.enabled") }, h("input", { type: "checkbox", checked: cfg.enabled, "aria-label": `${label}: ${t("badge.enabled")}`, onchange: (e) => { cfg.enabled = e.target.checked; changed(); } }), h("span", { class: "track" })),
      h("div", { class: "badge-name" }, h("b", null, label), hint ? h("span", null, hint) : null),
      h("input", { class: "input badge-text", value: cfg.text, "aria-label": `${label}: ${t("badge.text")}`, maxlength: 60, oninput: (e) => { cfg.text = e.target.value; changed(); } }),
      h("select", { class: "input badge-corner", "aria-label": `${label}: ${t("badge.corner")}`, onchange: (e) => { cfg.corner = e.target.value; changed(); } },
        CORNERS.map((c) => h("option", { value: c, selected: cfg.corner === c }, t(`corner.${c}`))))),
    h("details", { class: "adv", ontoggle: (e) => { if (e.target.open) ctx.focus(cfg); } }, h("summary", null, t("badge.style")),
      h("div", { class: "badge-style" }, presetSel, swatchRow,
        h("label", { class: "range" }, h("span", null, t("badge.order")), h("input", { class: "input short", type: "number", min: -9, max: 9, value: cfg.order, oninput: (e) => { cfg.order = parseInt(e.target.value, 10) || 0; changed(); } })),
        advanced)));
}
