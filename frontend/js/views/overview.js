import { h, clear, append } from "../h.js";
import { api } from "../api.js";
import { t, tn } from "../i18n.js";
import { bytes, relTime, duration } from "../fmt.js";
import { actionButton, toast, toastError } from "../ui.js";
import { pageHead, modeChip, eventTag } from "./common.js";
import { tideGauge, tideLegend } from "../components/tide.js";
import { leavingNext } from "../components/leavingNext.js";
import { summaryPanel } from "../components/summaryPanel.js";
import { openItemDialog } from "../components/itemDialog.js";
import { getLocale } from "../i18n.js";

const DAY = 864e5;
const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

function horizon(queue) {
  const today = startOfDay(new Date());
  const counts = new Array(14).fill(0);
  let later = 0;
  for (const q of queue) {
    const idx = Math.max(0, Math.round((startOfDay(new Date(q.delete_at)) - today) / DAY));
    if (idx > 13) later++; else counts[idx]++;
  }
  const max = Math.max(1, ...counts);
  const wd = new Intl.DateTimeFormat(getLocale(), { weekday: "short" });
  const cols = counts.map((n, i) => {
    const d = new Date(today + i * DAY);
    return h("div", { class: "hz-col" + (i === 0 ? " today" : ""), title: `${d.toLocaleDateString(getLocale(), { day: "numeric", month: "long" })}: ${n}` },
      h("div", { class: "hz-track" }, h("div", { class: "hz-bar" + (n ? "" : " zero"), style: { height: n ? `${Math.max(8, (n / max) * 100)}%` : "4px" } }, n ? h("span", null, String(n)) : null)),
      h("div", { class: "hz-day" }, h("b", null, String(d.getDate())), h("span", { class: "wd" }, wd.format(d))));
  });
  const total = counts.reduce((a, b) => a + b, 0);
  return h("section", { class: "panel deadlines" },
    h("div", { class: "dl-head" }, h("h2", null, t("ov.deadlines")), h("span", { class: "dl-total" }, total ? tn("ov.deadlinesTotal", total) : "")),
    h("div", { class: "horizon", role: "img", "aria-label": t("ov.horizonLabel", { n: total }) }, cols),
    h("p", { class: "hz-note" }, total === 0 && later === 0 ? t("ov.horizonNone") : later ? t("ov.horizonLater", { items: tn("count.items", later) }) : t("ov.horizonAll")));
}

function statStrip(status) {
  const q = status.queue;
  const tile = (value, label, cls = "") => h("div", { class: `stat ${cls}` }, h("b", { class: "num" }, value), h("span", null, label));
  return h("div", { class: "statbar", role: "group", "aria-label": t("ov.statsLabel") },
    tile(String(q.total), t("ov.statWaiting")), tile(bytes(q.reclaimable_bytes), t("ov.statReclaim")),
    tile(String(q.due), t("ov.statDue"), q.due ? "hot" : "calm"));
}

function sparkline(runs) {
  const pts = runs.filter((r) => r.summary && typeof r.summary.disk_percent === "number").reverse().map((r) => r.summary.disk_percent);
  if (pts.length < 2) return null;
  const lo = Math.min(...pts), hi = Math.max(...pts), span = Math.max(hi - lo, 1);
  const W = 300, H = 44;
  const xy = pts.map((v, i) => `${(i / (pts.length - 1)) * W},${H - 4 - ((v - lo) / span) * (H - 10)}`).join(" ");
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("preserveAspectRatio", "none"); svg.setAttribute("class", "spark");
  svg.setAttribute("role", "img"); svg.setAttribute("aria-label", t("ov.sparkLabel"));
  const line = document.createElementNS(NS, "polyline");
  for (const [k, v] of Object.entries({ points: xy, fill: "none", stroke: "#12D9B0", "stroke-width": "2", "stroke-linejoin": "round", "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" })) line.setAttribute(k, v);
  svg.append(line);
  return svg;
}

function oneDisk(label, d, status, key) {
  if (!d) return h("p", { class: "muted" }, t("ov.diskMissing"));
  const cleanup = status.cleanup || { start_percent: 0, types: {} };
  const typeKeys = key === "movies" ? ["movie"] : key === "series" ? ["series"] : ["movie", "series"];
  const active = typeKeys.every((k) => cleanup.types[k]?.active !== false);
  const faster = [...new Set((status.disk_thresholds || []).map((th) => th.usage_percent))];
  const start = cleanup.start_percent || 0;
  return h("div", { class: "disk-one" },
    tideGauge({ percent: d.percent, start, faster, active }),
    h("div", { class: "disk-side" },
      label ? h("div", { class: "disk-label" }, label) : null,
      h("div", { class: "disk-state " + (active ? "on" : "off") }, h("span", { class: "dot " + (active ? "ok" : "") }), active ? t("tide.stateActive") : t("tide.stateWaiting")),
      h("div", { class: "disk-figs" }, h("span", { class: "muted" }, t("ov.diskFree", { free: bytes(d.free), total: bytes(d.total) }))),
      tideLegend({ start, faster })));
}

function diskPanel(status, runs) {
  const disks = status.disks || {};
  const many = Object.keys(disks).length > 1;
  const body = !status.disk && !many
    ? h("p", { class: "muted" }, t("ov.diskMissing"))
    : many
      ? h("div", { class: "stack", style: { gap: "22px" } }, Object.entries(disks).map(([k, d]) => oneDisk(t(`ov.disk.${k}`), d, status, k)))
      : oneDisk("", status.disk, status, "main");
  return h("section", { class: "panel" }, h("h2", null, t("ov.disk")), body, sparkline(runs));
}

function alertsBanner(status) {
  const alerts = status.alerts || [];
  if (!alerts.length) return null;
  return h("div", { class: "notice bad", role: "alert" }, h("span", { class: "dot" }),
    h("span", null, alerts.map((a) => t("ov.connLost", { service: a.service === "jellyfin" ? "Jellyfin" : a.service === "radarr" ? "Radarr" : "Sonarr", n: a.cycles })).join(" ")));
}

function cleanupNotice(status) {
  const types = Object.values(status.cleanup?.types || {});
  const waiting = types.find((x) => !x.active);
  if (!waiting || !status.connections.radarr && !status.connections.sonarr) return null;
  return h("div", { class: "notice", role: "status" }, h("span", { class: "dot" }),
    waiting.reason === "unknown_usage" ? t("ov.cleanupUnknown")
      : t("ov.cleanupWaiting", { usage: waiting.usage, start: status.cleanup.start_percent }));
}

function runPanel(status) {
  const r = status.last_run, sch = status.scheduler || {};
  let head;
  if (sch.running) head = h("div", { class: "row first" }, h("span", { class: "dot busy" }), h("div", { class: "grow" }, h("div", { class: "t" }, t("ov.running")), h("div", { class: "s", id: "live-line" }, t("ov.waitingLogs"))));
  else if (!r) head = h("p", { class: "muted" }, t("ov.noRuns"));
  else {
    const cls = r.status === "ok" ? "ok" : r.status === "error" ? "bad" : "";
    const label = r.status === "ok" ? t("ov.lastOk") : r.status === "error" ? t("ov.lastError") : t("ov.lastInterrupted");
    const when = [relTime(r.finished_at || r.started_at)];
    if (r.summary?.duration_seconds != null) when.push(t("ov.tookFor", { d: duration(r.summary.duration_seconds) }));
    if (r.dry_run) when.push(t("ov.testRun"));
    head = h("div", { class: "row first" }, h("span", { class: `dot ${cls}` }), h("div", { class: "grow" }, h("div", { class: "t" }, label), h("div", { class: "s" }, when.join(", "))));
  }
  const sm = r?.summary;
  const facts = sm ? h("div", { class: "facts" },
    h("div", null, h("b", { class: "num" }, String(sm.queued ?? 0)), h("span", null, t("ov.fQueued"))),
    h("div", null, h("b", { class: "num" }, String(sm.dry_run ? (sm.would_delete ?? 0) : (sm.deleted ?? 0))), h("span", null, sm.dry_run ? t("ov.fWouldDelete") : t("ov.fDeleted"))),
    h("div", null, h("b", { class: "num" }, String(sm.dequeued ?? 0)), h("span", null, t("ov.fDequeued"))),
    h("div", null, h("b", { class: "num" }, String(sm.language_labeled ?? 0)), h("span", null, t("ov.fLanguage")))) : null;
  const next = !sch.running && sch.next_run_at ? h("p", { class: "muted small", style: { marginTop: "16px" } }, t("ov.nextRun", { when: relTime(sch.next_run_at) })) : null;
  return h("section", { class: "panel" }, h("h2", null, t("ov.cycle")), head, facts, next);
}

export function mount(root, app) {
  let alive = true, timer = null, connAt = 0, settingsPub = null, lastStatus = null, lastSig = "", summary = null;
  const conn = {};
  const body = h("div");
  const runBtn = actionButton({ label: t("ov.runNow"), kind: "primary", iconName: "play", onClick: () => start(null) });
  const testBtn = actionButton({ label: t("ov.runTest"), iconName: "shield", title: t("ov.runTestHint"), onClick: () => start(true) });
  const actions = h("div", { class: "actions" });
  root.append(pageHead(t("nav.overview"), actions), body);

  async function start(dry) {
    try { await api.post("/run", { dry_run: dry }); toast(dry ? t("toast.testCycleStarted") : t("toast.cycleStarted")); await load(); }
    catch (e) { toastError(e); }
  }

  function testConnections(status) {
    if (Date.now() - connAt < 60000) return;
    connAt = Date.now();
    for (const svc of ["jellyfin", "radarr", "sonarr"]) {
      if (!status.connections[svc]) { conn[svc] = { none: true }; continue; }
      api.post(`/test/${svc}`, {}).then((r) => { conn[svc] = r; if (alive) paintConn(); }).catch((e) => { conn[svc] = { ok: false, error: e.message }; if (alive) paintConn(); });
    }
  }
  function connPanel(status) {
    const rows = ["jellyfin", "radarr", "sonarr"].map((svc) => {
      const name = { jellyfin: "Jellyfin", radarr: "Radarr", sonarr: "Sonarr" }[svc];
      const r = conn[svc], url = settingsPub?.[svc]?.url || "";
      let state;
      if (!status.connections[svc]) state = h("a", { href: "#/settings" }, t("ov.configure"));
      else if (!r) state = h("span", { class: "muted" }, t("ov.checking"));
      else state = r.ok ? h("span", { class: "muted" }, t("ov.version", { v: r.version || "?" })) : h("span", { style: { color: "#FF8DA0" } }, r.error || t("ov.noConnection"));
      return h("div", { class: "row" }, h("span", { class: "dot " + (!status.connections[svc] ? "" : !r ? "busy" : r.ok ? "ok" : "bad") }),
        h("div", { class: "grow" }, h("div", { class: "t" }, name), url ? h("div", { class: "s t" }, url) : null), state);
    });
    return h("section", { class: "panel", id: "conn-panel" }, h("h2", null, t("ov.connections")), h("div", { class: "rows" }, rows));
  }
  function paintConn() { const old = document.getElementById("conn-panel"); if (old && lastStatus) old.replaceWith(connPanel(lastStatus)); }

  function render(status, queue, hist, runs) {
    lastStatus = status;
    clear(actions);
    actions.append(modeChip(status.dry_run), runBtn);
    if (!status.dry_run) actions.append(testBtn);
    runBtn.disabled = status.scheduler.running; testBtn.disabled = status.scheduler.running;

    const q = status.queue;
    const noArr = !status.connections.radarr && !status.connections.sonarr;
    let lead = null, sub = null;
    if (noArr) { lead = t("ov.leadConnect"); sub = h("span", null, t("ov.subConnect"), " ", h("a", { href: "#/settings" }, t("ov.openSettings")), "."); }
    else if (q.total === 0) { lead = t("ov.leadEmpty"); sub = t("ov.subEmpty"); }
    else sub = (q.due ? t("ov.subDue", { due: tn("ov.dueCount", q.due) }) + " " : "") + (status.dry_run ? t("ov.subDry") : t("ov.subTime"));
    const act = h("section", { class: "panel" }, h("h2", null, t("ov.activity")),
      hist.items.length ? h("div", { class: "rows" }, hist.items.map((e) => h("div", { class: "row" }, eventTag(e.event), h("div", { class: "grow" }, h("div", { class: "t" }, e.title || e.key)), h("span", { class: "s" }, relTime(e.ts)))))
        : h("p", { class: "muted" }, t("ov.activityEmpty")));
    const lib = status.library || {};
    clear(body);
    if (summary && summary.destroy) summary.destroy();
    summary = summaryPanel({ days: 30, compact: true });
    append(body, [
      lead ? h("p", { class: "lead" }, lead) : statStrip(status),
      sub ? h("p", { class: "lead-sub" }, sub) : null,
      alertsBanner(status),
      cleanupNotice(status),
      h("div", { class: "cols" },
        h("div", { class: "stack" },
          queue.length ? horizon(queue) : null,
          h("section", { class: "panel" }, h("h2", null, t("ov.next")),
            leavingNext(queue, (it) => openItemDialog(it.key, app, load)), queue.length ? h("a", { class: "board-all", href: "#/queue" }, t("ov.nextMore")) : null), act),
        h("div", { class: "stack" }, diskPanel(status, runs), runPanel(status), summary, connPanel(status),
          h("section", { class: "panel" }, h("h2", null, t("ov.library")), h("div", { class: "facts" },
            h("div", null, h("b", { class: "num" }, String(lib.total ?? 0)), h("span", null, t("ov.libTotal"))),
            h("div", null, h("b", { class: "num" }, String(lib.protected ?? 0)), h("span", null, t("ov.libKept")))))))]);
    testConnections(status);
  }

  async function liveLine() {
    try { const logs = await api.get("/logs?limit=1"); const el = document.getElementById("live-line"); if (el && logs[0]) el.textContent = logs[0].msg.slice(0, 140); } catch { /* ignore */ }
  }
  async function load() {
    try {
      const [status, queue, hist, runs, st] = await Promise.all([api.get("/status"), api.get("/queue"), api.get("/history?limit=7"), api.get("/runs?limit=30"), settingsPub ? Promise.resolve(settingsPub) : api.get("/settings")]);
      settingsPub = st;
      if (!alive) return;
      app.status = status;
      // Rebuilding the screen restarts every animation (the tide), so only do it when something changed.
      const sig = JSON.stringify([status, queue, hist.items, runs.map((r) => [r.id, r.status, r.finished_at])]);
      if (sig !== lastSig) { lastSig = sig; render(status, queue, hist, runs); }
      if (status.scheduler.running) liveLine();
      clearTimeout(timer);
      timer = setTimeout(load, status.scheduler.running ? 2000 : 15000);
    } catch (e) {
      if (!alive) return;
      clear(body); body.append(h("div", { class: "err" }, e.message));
      timer = setTimeout(load, 10000);
    }
  }
  load();
  return () => { alive = false; clearTimeout(timer); if (summary && summary.destroy) summary.destroy(); };
}
