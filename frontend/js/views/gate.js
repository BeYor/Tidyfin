// Sign-in screen and the first-run wizard.
import { h, clear } from "../h.js";
import { api } from "../api.js";
import { icon } from "../icons.js";
import { t, getLocale, errText } from "../i18n.js";
import { actionButton, toastError } from "../ui.js";
import { languageSwitch } from "../components/languageSwitch.js";

const brand = (tagline) => h("div", { class: "hero" },
  h("img", { src: "assets/mark.png", alt: "" }),
  h("div", { class: "wordmark" }, "Tidy", h("b", null, "fin")),
  tagline ? h("p", null, tagline) : null);

function labeled(id, label, props, hint) {
  const input = h("input", { class: "input", id, ...props });
  return h("div", { class: "field" }, h("label", { for: id }, label), input, hint ? h("div", { class: "hint" }, hint) : null);
}
const topRight = () => h("div", { class: "gate-lang" }, languageSwitch());

export function showLogin(host, onDone, notice) {
  clear(host);
  const err = h("div", { class: "err", hidden: true, role: "alert" });
  const user = labeled("lg-user", t("login.user"), { type: "text", autocomplete: "username", required: true, autofocus: true });
  const pass = labeled("lg-pass", t("login.password"), { type: "password", autocomplete: "current-password", required: true });
  const btn = h("button", { class: "btn primary", type: "submit" }, t("login.submit"));
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault(); btn.disabled = true; err.hidden = true;
    try { await api.post("/auth/login", { username: user.querySelector("input").value, password: pass.querySelector("input").value }); onDone(); }
    catch (ex) { err.textContent = errText(ex); err.hidden = false; btn.disabled = false; }
  } }, err, user, pass, btn);
  host.append(h("div", { class: "gate" }, topRight(), h("div", { class: "gate-card" },
    brand(t("login.tagline")), notice ? h("div", { class: "err", style: { marginBottom: "16px" } }, notice) : null, form,
    h("p", { class: "muted small center", style: { marginTop: "18px" } }, t("login.adminsOnly")))));
}

export function showSetup(host, onDone) {
  let step = 0;
  const STEPS = [t("wiz.stepJellyfin"), t("wiz.stepArr"), t("wiz.stepLanguages"), t("wiz.stepMode")];

  function frame(title, lede, content, nav) {
    clear(host);
    host.append(h("div", { class: "gate" }, topRight(), h("div", { class: "wizard" },
      h("div", null, h("div", { class: "hero left" }, h("img", { src: "assets/mark.png", alt: "", style: { width: "64px" } }), h("div", { class: "wordmark" }, "Tidy", h("b", null, "fin"))),
        h("ol", { class: "steps" }, STEPS.map((s, i) => h("li", { class: i === step ? "cur" : i < step ? "done" : "" },
          h("span", { class: "n" }, i < step ? icon("check", 14) : String(i + 1)), h("span", { class: "tx" }, s))))),
      h("div", { class: "panel" }, h("h2", null, title), h("p", { class: "lede" }, lede), content, nav))));
  }

  function stepJellyfin() {
    const err = h("div", { class: "err", hidden: true, role: "alert" });
    const url = labeled("s-url", t("wiz.jfUrl"), { type: "url", placeholder: "http://192.168.1.10:8096", required: true, autofocus: true }, t("wiz.jfUrlHint"));
    const user = labeled("s-user", t("wiz.adminUser"), { type: "text", autocomplete: "username", required: true });
    const pass = labeled("s-pass", t("login.password"), { type: "password", autocomplete: "current-password", required: true });
    const key = labeled("s-key", t("wiz.apiKeyOptional"), { type: "text", placeholder: t("wiz.apiKeyHint") });
    const btn = h("button", { class: "btn primary", type: "submit" }, t("wiz.connect"));
    const form = h("form", { class: "stack", style: { gap: "16px" }, onsubmit: async (e) => {
      e.preventDefault(); btn.disabled = true; err.hidden = true;
      try {
        await api.post("/setup/jellyfin", { url: url.querySelector("input").value, username: user.querySelector("input").value,
          password: pass.querySelector("input").value, api_key: key.querySelector("input").value, locale: getLocale() });
        step = 1; stepArr();
      } catch (ex) { err.textContent = errText(ex); err.hidden = false; btn.disabled = false; }
    } }, err, url, user, pass, h("details", { class: "adv" }, h("summary", null, t("wiz.haveKey")), key), h("div", { class: "nav-row end" }, btn));
    frame(t("wiz.jfTitle"), t("wiz.jfLede"), form, null);
  }

  function arrBlock(svc, label, port) {
    const url = labeled(`a-${svc}-url`, t("wiz.addressOf", { name: label }), { type: "url", placeholder: `http://192.168.1.10:${port}` });
    const key = labeled(`a-${svc}-key`, t("wiz.keyOf", { name: label }), { type: "text", placeholder: t("wiz.pasteKey") });
    const res = h("div", { class: "test-res", "aria-live": "polite" });
    const test = actionButton({ label: t("common.test"), small: true, onClick: async () => {
      res.className = "test-res"; res.textContent = t("common.checking");
      const r = await api.post(`/test/${svc}`, { url: url.querySelector("input").value, api_key: key.querySelector("input").value }).catch((e) => ({ ok: false, error: errText(e) }));
      res.className = "test-res " + (r.ok ? "ok" : "bad");
      res.textContent = r.ok ? t("common.connected", { name: r.name || label, version: r.version || "" }) : (r.code && errText({ code: r.code }) !== t("err.unknown") ? errText({ code: r.code }) : r.error);
    } });
    return { svc, url, key, el: h("div", { class: "conn" }, h("div", { class: "name" }, label), url, key, test, h("div", { class: "full" }, res)) };
  }
  function stepArr() {
    const radarr = arrBlock("radarr", "Radarr", 7878), sonarr = arrBlock("sonarr", "Sonarr", 8989);
    const next = () => { step = 2; stepLanguages(); };
    const save = actionButton({ label: t("wiz.saveNext"), kind: "primary", onClick: async () => {
      const patch = {};
      for (const b of [radarr, sonarr]) {
        const u = b.url.querySelector("input").value.trim(), k = b.key.querySelector("input").value.trim();
        if (u && k) patch[b.svc] = { url: u, api_key: k };
      }
      try { if (Object.keys(patch).length) await api.put("/settings", patch); next(); } catch (ex) { toastError(ex); }
    } });
    frame(t("wiz.arrTitle"), t("wiz.arrLede"), h("div", null, radarr.el, sonarr.el),
      h("div", { class: "nav-row" }, h("button", { class: "btn", type: "button", onclick: next }, t("common.skip")), save));
  }

  async function stepLanguages() {
    let options;
    try { options = await api.get("/options"); } catch (ex) { toastError(ex); options = { language_presets: [], suggested_for_locale: {} }; }
    const suggested = getLocale() === "en" ? null : options.suggested_for_locale[getLocale()];
    const chosen = new Set(suggested ? [suggested] : []);
    const grid = h("div", { class: "pick-grid" }, options.language_presets.map((p) => h("label", { class: "pick-card" },
      h("input", { type: "checkbox", checked: chosen.has(p.code), onchange: (e) => { e.target.checked ? chosen.add(p.code) : chosen.delete(p.code); } }),
      h("span", { class: "pc-body" }, h("b", null, p.name), h("span", null, p.native)))));
    const save = actionButton({ label: t("wiz.saveNext"), kind: "primary", onClick: async () => {
      try {
        const profiles = options.language_presets.filter((p) => chosen.has(p.code));
        if (profiles.length) await api.put("/settings", { language: { profiles } });
        step = 3; stepMode();
      } catch (ex) { toastError(ex); }
    } });
    frame(t("wiz.langTitle"), t("wiz.langLede"), grid, h("div", { class: "nav-row" }, h("button", { class: "btn", type: "button", onclick: () => { step = 3; stepMode(); } }, t("common.skip")), save));
  }

  function stepMode() {
    const row = (ic, title, text) => h("div", { class: "row" }, icon(ic, 20), h("div", { class: "grow" }, h("div", { class: "t" }, title), h("div", { class: "s" }, text)));
    frame(t("wiz.modeTitle"), t("wiz.modeLede"),
      h("div", { class: "rows" }, row("shield", t("wiz.mode1"), t("wiz.mode1t")), row("queue", t("wiz.mode2"), t("wiz.mode2t")), row("poster", t("wiz.mode3"), t("wiz.mode3t")), row("overview", t("wiz.mode4"), t("wiz.mode4t"))),
      h("div", { class: "nav-row end" }, h("button", { class: "btn primary", type: "button", onclick: onDone }, t("wiz.finish"))));
  }
  stepJellyfin();
}
