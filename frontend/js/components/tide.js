// "Tide gauge": disk usage as water in a vial. The marks show where cleanup starts and
// where deadlines get shorter, so the effect of the settings is visible at a glance.
import { h } from "../h.js";
import { t } from "../i18n.js";

const NS = "http://www.w3.org/2000/svg";
const svg = (tag, attrs = {}, ...kids) => {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  kids.forEach((k) => el.append(k));
  return el;
};

let seq = 0;

/**
 * percent: current usage; start: cleanup start percent (0 = none);
 * faster: usage percents where deadlines shrink; active: is cleanup running.
**/
export function tideGauge({ percent, start = 0, faster = [], active = true }) {
  const id = `tide${++seq}`;
  const W = 132, H = 236, X = 14, Y = 8, VW = W - 2 * X, VH = H - 2 * Y, R = 24;
  const top = Y + 6, bottom = Y + VH - 6, inner = bottom - top;
  const yOf = (pct) => bottom - (inner * Math.min(Math.max(pct, 0), 100)) / 100;
  const level = yOf(percent);
  const hot = faster.some((p) => percent >= p);
  const tone = hot ? "hot" : !active ? "idle" : "ok";

  // One period of the wave is exactly `len` wide, and the CSS animation moves it by exactly
  // one period (--len), so the loop is seamless instead of jumping.
  const wave = (amp, len) => {
    let d = `M ${-len * 2} 0`;
    for (let x = -len * 2; x < W + len * 2; x += len) d += ` q ${len / 4} ${-amp} ${len / 2} 0 t ${len / 2} 0`;
    return d + ` V ${H} H ${-len * 2} Z`;
  };
  const gradient = svg("linearGradient", { id: `${id}g`, x1: "0", y1: "0", x2: "0", y2: "1" },
    svg("stop", { offset: "0", "stop-color": "var(--tide-top)" }), svg("stop", { offset: "1", "stop-color": "var(--tide-bottom)" }));
  const clip = svg("clipPath", { id: `${id}c` }, svg("rect", { x: X, y: Y, width: VW, height: VH, rx: R }));

  const marks = [];
  if (start > 0) marks.push({ pct: start, cls: "start" });
  for (const p of faster) marks.push({ pct: p, cls: "faster" });

  const root = svg("svg", { class: `tide ${tone}`, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": t("tide.level", { pct: percent }) },
    svg("defs", {}, gradient, clip),
    svg("rect", { x: X, y: Y, width: VW, height: VH, rx: R, class: "tide-glass" }),
    svg("g", { "clip-path": `url(#${id}c)` },
      svg("g", { class: "water", style: `--level: ${level}px; --from: ${bottom + 14}px` },
        svg("path", { d: wave(5, 60), class: "wave w2", style: "--len: 60px", fill: `url(#${id}g)`, opacity: ".45" }),
        svg("path", { d: wave(4, 44), class: "wave w1", style: "--len: 44px", fill: `url(#${id}g)` }))),
    svg("rect", { x: X, y: Y, width: VW, height: VH, rx: R, class: "tide-rim" }),
    ...marks.map((m) => svg("line", { x1: X - 8, x2: X + VW + 8, y1: yOf(m.pct), y2: yOf(m.pct), class: `mark ${m.cls}` })));
  return h("div", { class: "tide-wrap" }, root, h("div", { class: "tide-pct" }, h("b", { class: "num" }, `${Math.round(percent)}`), h("span", null, "%")));
}

/** Text legend that goes next to the gauge. **/
export function tideLegend({ start = 0, faster = [] }) {
  const rows = [];
  if (start > 0) rows.push(h("li", null, h("span", { class: "key start" }), t("tide.startMark", { pct: start })));
  for (const p of faster) rows.push(h("li", null, h("span", { class: "key faster" }), t("tide.fasterMark", { pct: p })));
  return rows.length ? h("ul", { class: "tide-legend" }, rows) : null;
}
