import { h } from "../h.js";
import { api } from "../api.js";
import { t } from "../i18n.js";
import { timeOnly } from "../fmt.js";
import { pageHead } from "./common.js";

export function mount(root) {
  let es = null, paused = false, onlyWarn = false, follow = true;
  const term = h("div", { class: "terminal", role: "log", "aria-live": "off", tabindex: "0", "aria-label": t("nav.logs") });
  const pauseBtn = h("button", { class: "btn", type: "button", onclick: () => { paused = !paused; pauseBtn.textContent = paused ? t("logs.resume") : t("logs.pause"); } }, t("logs.pause"));
  const warnBtn = h("button", { class: "btn", type: "button", "aria-pressed": "false", onclick: () => { onlyWarn = !onlyWarn; warnBtn.setAttribute("aria-pressed", String(onlyWarn)); warnBtn.textContent = onlyWarn ? t("logs.warnOnly") : t("logs.all"); apply(); } }, t("logs.all"));
  const clearBtn = h("button", { class: "btn", type: "button", onclick: () => term.replaceChildren() }, t("logs.clear"));
  root.append(pageHead(t("nav.logs"), warnBtn, pauseBtn, clearBtn), term);

  const isWarn = (lvl) => ["WARNING", "ERROR", "CRITICAL"].includes(lvl);
  const line = (e) => h("div", { class: `log ${e.level}`, hidden: onlyWarn && !isWarn(e.level) }, h("time", null, timeOnly(e.ts)), h("span", null, e.msg));
  function apply() { term.querySelectorAll(".log").forEach((el) => { el.hidden = onlyWarn && !/WARNING|ERROR|CRITICAL/.test(el.className); }); }
  term.addEventListener("scroll", () => { follow = term.scrollTop + term.clientHeight >= term.scrollHeight - 30; });
  function push(e) {
    if (paused) return;
    term.append(line(e));
    while (term.childElementCount > 1500) term.firstChild.remove();
    if (follow) term.scrollTop = term.scrollHeight;
  }
  api.get("/logs?limit=300").then((rows) => {
    rows.forEach((e) => term.append(line(e)));
    term.scrollTop = term.scrollHeight;
    es = new EventSource(`/api/logs/stream?after=${rows.length ? rows[rows.length - 1].id : 0}`);
    es.onmessage = (m) => { try { push(JSON.parse(m.data)); } catch { /* ignore */ } };
  }).catch((e) => term.append(h("div", { class: "log ERROR" }, h("time", null, ""), h("span", null, e.message))));
  return () => { if (es) es.close(); };
}
