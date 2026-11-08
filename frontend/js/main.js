import { h, clear } from "./h.js";
import { api, setUnauthorizedHandler } from "./api.js";
import { icon } from "./icons.js";
import { t, onLocaleChange } from "./i18n.js";
import { showLogin, showSetup } from "./views/gate.js";
import { loading } from "./ui.js";
import { languageSwitch } from "./components/languageSwitch.js";

const ROUTES = [
  { path: "", key: "nav.overview", icon: "overview", load: () => import("./views/overview.js") },
  { path: "queue", key: "nav.queue", icon: "queue", load: () => import("./views/queue.js"), badge: true },
  { path: "library", key: "nav.library", icon: "library", load: () => import("./views/library.js") },
  { path: "history", key: "nav.history", icon: "history", load: () => import("./views/history.js") },
  { path: "language", key: "nav.language", icon: "language", load: () => import("./views/language.js") },
  { path: "logs", key: "nav.logs", icon: "logs", load: () => import("./views/logs.js") },
  { path: "settings", key: "nav.settings", icon: "settings", load: () => import("./views/settings.js") },
];

const host = document.getElementById("app");
let dispose = null, statusTimer = null, shuttingDown = false, renderToken = 0;

const app = {
  user: null,
  status: null,
  async refreshStatus() {
    try { this.status = await api.get("/status"); updateBadge(); } catch { /* a 401 is handled by the global handler */ }
    return this.status;
  },
  go(path) { location.hash = "#/" + path; },
  async logout() { try { await api.post("/auth/logout"); } catch { /* ignore */ } boot(); },
};

const currentPath = () => (location.hash.replace(/^#\/?/, "") || "").split("?")[0];

function updateBadge() {
  const el = document.querySelector('[data-badge="queue"]');
  if (!el) return;
  const n = app.status?.queue?.total || 0;
  el.textContent = String(n);
  el.hidden = n === 0;
}

function buildShell() {
  clear(host);
  const nav = h("nav", { class: "nav", "aria-label": t("nav.aria") }, ROUTES.map((r) =>
    h("a", { href: "#/" + r.path, "data-path": r.path }, icon(r.icon, 20), h("span", null, t(r.key)),
      r.badge ? h("span", { class: "count", "data-badge": "queue", hidden: true }, "0") : null)));
  const rail = h("aside", { class: "rail" },
    h("div", { class: "brand" }, h("img", { src: "assets/mark-256.png", alt: "" }), h("div", { class: "wordmark" }, "Tidy", h("b", null, "fin"))),
    nav,
    h("div", { class: "rail-foot" },
      languageSwitch(),
      h("div", { class: "who-row" },
        h("div", { class: "who" }, h("b", null, app.user?.name || ""), h("span", null, t("nav.admin"))),
        h("button", { class: "btn icon small", type: "button", title: t("nav.logout"), "aria-label": t("nav.logout"), onclick: () => app.logout() }, icon("logout", 18)))));
  host.append(h("div", { class: "shell" }, rail, h("main", { class: "main", id: "view" })));
  updateBadge();
}

async function renderRoute() {
  const view = document.getElementById("view");
  if (!view) return;
  const token = ++renderToken;
  if (dispose) { try { dispose(); } catch { /* ignore */ } dispose = null; }
  const route = ROUTES.find((r) => r.path === currentPath()) || ROUTES[0];
  document.title = `${t(route.key)} - Tidyfin`;
  document.querySelectorAll(".nav a").forEach((a) => (a.dataset.path === route.path ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  clear(view);
  view.append(loading());
  try {
    const mod = await route.load();
    if (token !== renderToken) return;       // the user already navigated elsewhere
    const root = h("div", { class: "page" });
    clear(view); view.append(root);
    dispose = mod.mount(root, app) || null;
    window.scrollTo(0, 0);
  } catch (ex) {
    if (token !== renderToken) return;
    clear(view);
    view.append(h("div", { class: "page" }, h("div", { class: "err" }, t("err.viewFailed", { msg: ex.message }))));
  }
}

async function boot() {
  if (dispose) { try { dispose(); } catch { /* ignore */ } dispose = null; }
  clearInterval(statusTimer);
  clear(host); host.append(loading());
  let health;
  try { health = await api.get("/health"); }
  catch (ex) {
    clear(host);
    host.append(h("div", { class: "gate" }, h("div", { class: "gate-card" },
      h("div", { class: "err" }, t("err.network")), h("button", { class: "btn", style: { marginTop: "14px" }, onclick: boot }, t("common.retry")))));
    return;
  }
  if (!health.setup_complete) { showSetup(host, boot); return; }
  try { app.user = (await api.get("/auth/me")).user; }
  catch (ex) { if (ex.status === 401) { showLogin(host, boot); return; } throw ex; }
  buildShell();
  await app.refreshStatus();
  statusTimer = setInterval(() => app.refreshStatus(), 30000);
  renderRoute();
}

window.addEventListener("hashchange", renderRoute);
// Switching language rebuilds the current screen in place.
onLocaleChange(() => { if (document.getElementById("view")) { buildShell(); renderRoute(); } else boot(); });
setUnauthorizedHandler(() => {
  if (shuttingDown) return;
  shuttingDown = true;
  clearInterval(statusTimer);
  if (dispose) { try { dispose(); } catch { /* ignore */ } dispose = null; }
  showLogin(host, () => { shuttingDown = false; boot(); }, t("login.expired"));
});
boot();
