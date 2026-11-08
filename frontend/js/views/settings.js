import { h, clear } from "../h.js";
import { api } from "../api.js";
import { icon } from "../icons.js";
import { t, tn, errText, getLocale } from "../i18n.js";
import { bytes } from "../fmt.js";
import { actionButton, confirmDialog, dialog, loading, toast, toastError } from "../ui.js";
import { pageHead } from "./common.js";
import { makeKit } from "../components/formKit.js";
import { badgeEditor } from "../components/badgeEditor.js";
import { previewDock } from "../components/previewDock.js";
import { profileEditor } from "../components/profileEditor.js";
import { languageSwitch } from "../components/languageSwitch.js";
import { SUPPORT, REPO_URL } from "../support.js";

const INTERVALS = [900, 1800, 3600, 7200, 21600, 43200, 86400];
const clone = (o) => JSON.parse(JSON.stringify(o));

function customProfile(name, native, short, presets) {
  const s = short.toLowerCase().slice(0, 3), up = s.toUpperCase();
  const mk = (text) => ({ enabled: true, text, corner: "bottom-left", order: 0, style: { ...presets.glass, preset: "glass", size: 5.5, radius: 20, weight: "bold" } });
  return { code: s, name, native: native || name, short: s, enabled: true, codes: [s], hints: [], voiceover_words: [], dub_words: [], subs_words: [], tags: {},
    badges: { audio: mk((native || name).toUpperCase()), dub: mk(`${up} DUB`), subs: mk(`${up} SUBS`), voiceover: mk(`${up} VO`) } };
}

export function mount(root, app) {
  let alive = true, draft = null, orig = "", options = null, users = null, focused = null, dock = null, observer = null;
  const visible = new Set();
  const body = h("div", null, loading());
  const bar = h("div", { class: "savebar", hidden: true });
  document.body.append(bar);
  dock = previewDock(() => {
    const list = [{ ...draft.badges.countdown }];
    if (focused && focused !== draft.badges.countdown) list.push({ ...focused });
    else {
      const first = draft.language.profiles.find((p) => p.enabled);
      if (first) list.push({ ...first.badges.dub });
    }
    return list;
  });
  root.classList.add("has-dock");
  document.body.append(dock.el);
  root.append(pageHead(t("nav.settings")), body);

  const dirty = () => JSON.stringify(draft) !== orig;
  const touch = () => { bar.hidden = !dirty(); if (dock) dock.refresh(); };
  const kit = makeKit(() => draft, touch);
  const ctxFor = () => ({ kit, presets: options.badge_presets, touch, focus: (cfg) => { focused = cfg; if (dock) { dock.show(true); dock.refresh(); } }, remove: (p) => { draft.language.profiles = draft.language.profiles.filter((x) => x !== p); renderAll(); touch(); } });

  const sec = (id, title, lede, ...kids) => h("section", { class: "section", id: `sec-${id}` }, h("h2", null, title), lede ? h("p", { class: "lede" }, lede) : null, ...kids);

  // ---------- sections ----------
  function modeSection() {
    return sec("mode", t("set.mode"), t("set.modeLede"),
      h("div", { class: "stack", style: { gap: "20px" } },
        kit.toggle(t("set.dryRun"), t("set.dryRunDesc"), "dry_run", { confirmOff: { title: t("set.dryOffTitle"), text: t("set.dryOffText"), confirmLabel: t("set.dryOffConfirm"), cancelLabel: t("set.dryOffCancel"), danger: true } }),
        h("div", { class: "grid-2" },
          kit.select(t("set.interval"), "run_interval_seconds", INTERVALS.map((s) => [s, t(`interval.${s}`)]), { numeric: true }),
          kit.select(t("set.serverLang"), "locale", [["en", "English"], ["pl", "Polski"]], { hint: t("set.serverLangHint") }))));
  }

  function connSection() {
    const conn = (svc, label, extra) => {
      const res = h("div", { class: "test-res", "aria-live": "polite" });
      const c = draft[svc], keyId = kit.nextId(), urlId = kit.nextId();
      const keyInput = h("input", { class: "input", id: keyId, type: "password", autocomplete: "off", placeholder: c.api_key_set ? t("set.keySaved") : t("set.keyPaste"), value: c.api_key || "", oninput: (e) => { c.api_key = e.target.value; touch(); } });
      const urlInput = h("input", { class: "input", id: urlId, type: "url", placeholder: "http://host:port", value: c.url || "", oninput: (e) => { c.url = e.target.value; touch(); } });
      const test = actionButton({ label: t("common.test"), small: true, onClick: async () => {
        res.className = "test-res"; res.textContent = t("common.checking");
        const r = await api.post(`/test/${svc}`, { url: c.url, api_key: c.api_key }).catch((e) => ({ ok: false, error: errText(e) }));
        res.className = "test-res " + (r.ok ? "ok" : "bad");
        res.textContent = r.ok ? t("common.connected", { name: r.name || label, version: r.version || "" }) : (r.code && errText({ code: r.code }) !== t("err.unknown") ? errText({ code: r.code }) : r.error);
      } });
      return h("div", { class: "conn" }, h("div", { class: "name" }, label), kit.field(t("set.address"), urlInput, null, urlId), kit.field(t("set.apiKey"), keyInput, null, keyId), test,
        h("div", { class: "full" }, res), extra ? h("div", { class: "full" }, extra) : null);
    };
    return sec("conn", t("set.connections"), t("set.connectionsLede"),
      conn("jellyfin", "Jellyfin", kit.text(t("set.publicUrl"), "jellyfin_public_url", { hint: t("set.publicUrlHint") })), conn("radarr", "Radarr"), conn("sonarr", "Sonarr"));
  }

  function rulesSection() {
    const user = h("select", { class: "input", id: kit.nextId(), onchange: (e) => { draft.manual_user_id = e.target.value; touch(); } }, h("option", { value: "" }, t("common.off")));
    const fill = (list) => { for (const u of list) user.append(h("option", { value: u.id, selected: u.id === draft.manual_user_id }, u.name + (u.is_admin ? ` (${t("nav.admin").toLowerCase()})` : ""))); };
    if (users) fill(users);
    else api.get("/jellyfin/users").then((l) => { users = l; if (alive) fill(l); }).catch(() => { if (draft.manual_user_id) user.append(h("option", { value: draft.manual_user_id, selected: true }, draft.manual_user_id)); });
    const col = (title, path) => h("div", { class: "stack", style: { gap: "16px" } }, h("h3", { class: "h3" }, title),
      kit.num(t("set.fileAge"), `${path}.content_age_threshold_days`, { suffix: t("unit.days"), hint: path === "movies" ? t("set.fileAgeHint") : t("set.fileAgeHintSeries") }),
      kit.num(t("set.notWatched"), `${path}.last_stream_threshold_days`, { suffix: t("unit.days") }),
      kit.num(t("set.grace"), `${path}.cleanup_delay_days`, { suffix: t("unit.days") }));
    return sec("rules", t("set.rules"), t("set.rulesLede"),
      h("div", { class: "grid-2" }, col(t("type.movies"), "movies"), col(t("type.seriesPlural"), "series")),
      h("div", { class: "stack", style: { marginTop: "26px", gap: "20px" } },
        h("div", { class: "grid-2" }, kit.text(t("set.protectTag"), "protect_tag", { hint: t("set.protectTagHint") }), kit.tags(t("set.excludeTags"), "exclude_tags", t("set.excludeTagsHint"))),
        h("div", { class: "grid-2" }, kit.field(t("set.starUser"), user, t("set.starUserHint"), user.id), kit.num(t("set.starGrace"), "grace_days", { suffix: t("unit.days") })),
        h("h3", { class: "h3" }, t("set.activity")), h("p", { class: "lede small" }, t("set.activityLede")),
        h("div", { class: "grid-2" },
          kit.num(t("set.favProtect"), "favorites_protect_days", { min: 0, suffix: t("unit.days"), hint: t("set.favProtectHint") }),
          kit.num(t("set.resumeProtect"), "resume_protect_days", { min: 0, suffix: t("unit.days"), hint: t("set.resumeProtectHint") })),
        whatIf()));
  }

  // Try the numbers on screen against the library without saving them.
  function whatIf() {
    const out = h("div", { class: "whatif-cols" });
    const names = (list, n = 6) => h("ul", null, list.slice(0, n).map((i) => h("li", null, i.title)), list.length > n ? h("li", { class: "muted" }, tn("set.whatIfMore", list.length - n)) : null);
    const column = (label, items) => h("div", null, h("span", { class: "muted small" }, label), h("div", { class: "whatif-num" }, tn("count.items", items.length), items.length ? ` · ${bytes(items.reduce((a, i) => a + i.size_bytes, 0))}` : ""));
    const run = async () => {
      const patch = { movies: draft.movies, series: draft.series, favorites_protect_days: draft.favorites_protect_days, resume_protect_days: draft.resume_protect_days };
      const [now, tryIt] = await Promise.all([api.post("/preview"), api.post("/preview", patch)]);
      const before = new Set(now.items.map((i) => i.key)), after = new Set(tryIt.items.map((i) => i.key));
      const added = tryIt.items.filter((i) => !before.has(i.key)), dropped = now.items.filter((i) => !after.has(i.key));
      clear(out);
      out.append(column(t("set.whatIfSaved"), now.items), column(t("set.whatIfTry"), tryIt.items));
      if (added.length) out.append(h("div", null, h("b", null, t("set.whatIfAdded")), names(added)));
      if (dropped.length) out.append(h("div", null, h("b", null, t("set.whatIfDropped")), names(dropped)));
      if (!added.length && !dropped.length) out.append(h("p", { class: "muted" }, t("set.whatIfSame")));
    };
    return h("div", { class: "whatif" }, h("h3", { class: "h3" }, t("set.whatIf")), h("p", { class: "muted small" }, t("set.whatIfHint")),
      actionButton({ label: t("set.whatIfRun"), iconName: "search", onClick: async () => { try { await run(); } catch (e) { toastError(e); } } }), out);
  }

  function thresholds() {
    const wrap = h("div");
    const paint = () => {
      clear(wrap);
      draft.disk_thresholds.forEach((th, i) => wrap.append(h("div", { class: "thr" },
        kit.num(t("set.thrFrom"), "usage_percent", { target: th, min: 1, max: 100, suffix: "%" }),
        kit.num(t("set.thrMovies"), "movies_days", { target: th, suffix: t("unit.days") }),
        kit.num(t("set.thrSeries"), "series_days", { target: th, suffix: t("unit.days") }),
        h("button", { class: "btn icon", type: "button", "aria-label": t("set.thrRemove"), onclick: () => { draft.disk_thresholds.splice(i, 1); paint(); touch(); } }, icon("trash", 16)))));
      wrap.append(h("button", { class: "btn small", type: "button", onclick: () => { draft.disk_thresholds.push({ usage_percent: 90, movies_days: 3, series_days: 3 }); paint(); touch(); } }, icon("plus", 14), t("set.thrAdd")));
    };
    paint();
    return wrap;
  }

  function diskSection() {
    return sec("disk", t("set.disk"), t("set.diskLede"),
      h("div", { class: "stack", style: { gap: "20px" } },
        h("div", { class: "grid-2" }, kit.num(t("set.startPercent"), "cleanup_start_percent", { min: 0, max: 100, suffix: t("unit.percentFull"), hint: t("set.startPercentHint") })),
        kit.text(t("set.diskPath"), "disk_path", { hint: t("set.diskPathHint") }),
        h("details", { class: "adv" }, h("summary", null, t("set.diskPerType")), h("div", { class: "grid-2" },
          kit.text(t("set.diskMovies"), "disk_path_movies", { hint: t("set.diskPerTypeHint") }), kit.text(t("set.diskSeries"), "disk_path_series"))),
        h("p", { class: "lede small" }, t("set.thrLede")), thresholds(),
        kit.toggle(t("set.shorten"), t("set.shortenDesc"), "shorten_existing_queue")));
  }

  function badgesSection() {
    const countdown = draft.badges.countdown;
    const ctx = ctxFor();
    return sec("badges", t("set.badges"), t("set.badgesLede"),
      h("div", { class: "stack", style: { gap: "20px" } },
        kit.toggle(t("set.badgesOn"), t("set.badgesOnDesc"), "badges.enabled"),
        h("div", { class: "grid-2" }, kit.num(t("set.countdownWithin"), "countdown_show_within_days", { target: draft.badges, min: 0, suffix: t("unit.days"), hint: t("set.countdownWithinHint") }),
          kit.text(t("set.dateFormat"), "date_format", { target: draft.badges, hint: t("set.dateFormatHint") }),
          kit.text(t("set.collMovies"), "leaving_movies_collection_name"), kit.text(t("set.collSeries"), "leaving_tv_collection_name")),
        h("div", { class: "badge-list" }, badgeEditor(t("badge.countdown"), countdown, ctx, { hint: t("badge.countdownHint") })),
        h("p", { class: "muted small" }, t("badge.applyNote"))));
  }

  function languagesSection() {
    const list = h("div", { class: "profiles" });
    const ctx = ctxFor();
    for (const p of draft.language.profiles) list.append(profileEditor(p, ctx));
    if (!draft.language.profiles.length) list.append(h("p", { class: "muted" }, t("set.noProfiles")));
    const have = new Set(draft.language.profiles.map((p) => p.code));
    const sel = h("select", { class: "input", "aria-label": t("set.addLanguage") },
      h("option", { value: "" }, t("set.addLanguage")), options.language_presets.filter((p) => !have.has(p.code)).map((p) => h("option", { value: p.code }, `${p.name} (${p.native})`)),
      h("option", { value: "__custom" }, t("set.customLanguage")));
    const add = h("button", { class: "btn", type: "button", onclick: () => {
      if (!sel.value) return;
      if (sel.value === "__custom") return openCustom();
      draft.language.profiles.push(clone(options.language_presets.find((p) => p.code === sel.value)));
      renderAll(); touch();
    } }, icon("plus", 16), t("common.add"));
    function openCustom() {
      const name = h("input", { class: "input", placeholder: t("set.customName") }), native = h("input", { class: "input", placeholder: t("set.customNative") }), short = h("input", { class: "input", placeholder: "sv", maxlength: 3 });
      dialog({ title: t("set.customLanguage"), body: h("div", { class: "stack", style: { gap: "14px" } }, kit.field(t("profile.name"), name), kit.field(t("profile.native"), native), kit.field(t("profile.short"), short, t("set.customShortHint"))),
        actions: [{ label: t("common.cancel") }, { label: t("common.add"), kind: "primary", onClick: () => {
          if (!name.value.trim() || short.value.trim().length < 2) { toast(t("set.customInvalid"), "bad"); return false; }
          let p = customProfile(name.value.trim(), native.value.trim(), short.value.trim(), options.badge_presets);
          if (have.has(p.code)) p.code += "2";
          draft.language.profiles.push(p); renderAll(); touch();
        } }] });
    }
    return sec("languages", t("set.languages"), t("set.languagesLede"), list,
      h("div", { class: "add-row" }, sel, add),
      h("details", { class: "adv" }, h("summary", null, t("set.advanced")), h("div", { class: "stack", style: { gap: "18px" } },
        kit.toggle(t("set.deepScan"), t("set.deepScanDesc"), "language.deep_scan"),
        h("div", { class: "grid-2" },
          kit.text(t("set.pathJf"), "language.jellyfin_path_prefix", { hint: t("set.pathHint") }), kit.text(t("set.pathLocal"), "language.local_path_prefix"),
          kit.text(t("set.ffprobe"), "language.ffprobe_bin"), kit.num(t("set.maxEpisodes"), "language.max_episodes", { min: 1 }),
          kit.text(t("set.collIdMovies"), "leaving_movies_collection_id", { hint: t("set.collIdHint") }), kit.text(t("set.collIdSeries"), "leaving_tv_collection_id")))));
  }

  function secretField(label, path, placeholder) {
    const [section, name] = path.split(".");
    const isSet = draft[section][`${name}_set`];
    const id = kit.nextId();
    const input = h("input", { class: "input", id, type: "password", autocomplete: "off", placeholder: isSet ? t("set.keySaved") : placeholder || "", value: draft[section][name] || "",
      oninput: (e) => { draft[section][name] = e.target.value; touch(); } });
    const remove = isSet ? h("button", { class: "btn small", type: "button", onclick: (e) => { draft._clear_secrets = [...(draft._clear_secrets || []), path]; draft[section][`${name}_set`] = false; e.currentTarget.remove(); input.placeholder = placeholder || ""; touch(); } }, t("common.remove")) : null;
    return kit.field(label, h("div", { class: "input-row" }, input, remove), null, id);
  }

  function notifySection() {
    const results = h("div", { class: "test-res" });
    const test = actionButton({ label: t("set.notifyTest"), iconName: "bell", onClick: async () => {
      if (dirty()) { toast(t("set.saveFirst"), "bad"); return; }
      try {
        const r = await api.post("/notify/test");
        results.className = "test-res";
        results.textContent = Object.entries(r.results).map(([ch, v]) => `${ch}: ${v === true ? t("common.ok") : v}`).join("; ");
      } catch (e) { results.className = "test-res bad"; results.textContent = errText(e); }
    } });
    return sec("notify", t("set.notify"), t("set.notifyLede"), h("div", { class: "stack", style: { gap: "18px" } },
      kit.toggle(t("set.notifyOn"), null, "notifications.enabled"),
      h("div", { class: "checks" }, kit.toggle(t("set.nQueued"), null, "notifications.on_queued"), kit.toggle(t("set.nDeleted"), null, "notifications.on_deleted"), kit.toggle(t("set.nErrors"), null, "notifications.on_errors")),
      h("div", { class: "grid-2" },
        secretField("Discord webhook", "notifications.discord_url", "https://discord.com/api/webhooks/..."),
        h("div", null, kit.text("ntfy URL", "notifications.ntfy_url", { placeholder: "https://ntfy.sh/my-topic" })), secretField(t("set.ntfyToken"), "notifications.ntfy_token"),
        secretField("Telegram bot token", "notifications.telegram_token"), kit.text("Telegram chat id", "notifications.telegram_chat_id"),
        secretField(t("set.webhook"), "notifications.webhook_url", "https://example.com/hook")),
      h("div", { class: "input-row" }, test, results)));
  }

  function backupSection() {
    const fileInput = h("input", { type: "file", accept: "application/json,.json", hidden: true, onchange: async (e) => {
      const file = e.target.files[0]; if (!file) return;
      try { await api.post("/settings/import", JSON.parse(await file.text())); toast(t("set.imported")); await load(); }
      catch (ex) { toast(ex instanceof SyntaxError ? t("err.bad_import") : errText(ex), "bad"); }
      e.target.value = "";
    } });
    return sec("backup", t("set.backup"), t("set.backupLede"), h("div", { class: "btn-row" },
      actionButton({ label: t("set.export"), iconName: "link", onClick: async () => {
        const data = await api.get("/settings/export");
        const a = h("a", { href: URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })), download: "tidyfin-settings.json" });
        document.body.append(a); a.click(); a.remove();
      } }),
      h("button", { class: "btn", type: "button", onclick: () => fileInput.click() }, icon("plus", 16), t("set.import")), fileInput,
      actionButton({ label: t("set.restorePosters"), kind: "danger", iconName: "refresh", onClick: async () => {
        if (!(await confirmDialog({ title: t("set.restoreTitle"), text: t("set.restoreText"), confirmLabel: t("set.restoreConfirm"), danger: true }))) return;
        try { const r = await api.post("/posters/restore-all"); toast(t("set.restored", { n: r.restored })); } catch (e) { toastError(e); }
      } })));
  }

  function aboutSection() {
    const links = [];
    if (SUPPORT.link && SUPPORT.link.url) links.push(h("a", { class: "btn", href: SUPPORT.link.url, target: "_blank", rel: "noopener" }, icon("heart", 16), SUPPORT.link.label || SUPPORT.link.url));
    return sec("about", t("set.about"), null,
      h("p", null, t("set.aboutText", { version: app.status?.version || "" })),
      h("div", { class: "btn-row", style: { marginTop: "14px" } }, h("a", { class: "btn", href: REPO_URL, target: "_blank", rel: "noopener" }, icon("link", 16), "GitHub"), ...links, languageSwitch()),
      links.length ? h("p", { class: "muted small", style: { marginTop: "12px" } }, t("set.supportNote")) : null);
  }

  const SECTIONS = [["mode", "set.mode", modeSection], ["conn", "set.connections", connSection], ["rules", "set.rules", rulesSection], ["disk", "set.disk", diskSection],
    ["badges", "set.badges", badgesSection], ["languages", "set.languages", languagesSection], ["notify", "set.notify", notifySection], ["backup", "set.backup", backupSection], ["about", "set.about", aboutSection]];

  function watchDock() {
    if (observer) observer.disconnect();
    visible.clear();
    if (!dock) return;
    observer = new IntersectionObserver((entries) => {
      for (const e of entries) (e.isIntersecting ? visible.add(e.target.id) : visible.delete(e.target.id));
      dock.show(visible.size > 0);
    }, { threshold: [0, 0.05] });
    for (const id of ["sec-badges", "sec-languages"]) { const el = document.getElementById(id); if (el) observer.observe(el); }
  }

  function renderAll() {
    focused = null;
    clear(body);
    body.append(h("nav", { class: "subnav", "aria-label": t("nav.settings") }, SECTIONS.map(([id, key]) =>
      h("button", { type: "button", onclick: () => document.getElementById(`sec-${id}`).scrollIntoView({ behavior: "smooth", block: "start" }) }, t(key)))));
    for (const [, , build] of SECTIONS) body.append(build());
    touch();
    watchDock();
  }

  function paintBar() {
    clear(bar);
    bar.append(h("div", null, h("span", { class: "muted" }, t("set.unsaved")),
      h("button", { class: "btn", type: "button", onclick: () => { draft = JSON.parse(orig); renderAll(); } }, t("set.discard")),
      actionButton({ label: t("set.save"), kind: "primary", onClick: save })));
  }

  async function save() {
    try {
      const patch = clone(draft);
      const saved = await api.put("/settings", patch);
      adopt(saved);
      toast(t("set.saved"));
      renderAll();
      app.refreshStatus();
    } catch (e) { toastError(e); }
  }

  function adopt(settings) {
    draft = clone(settings);
    for (const k of ["jellyfin", "radarr", "sonarr"]) draft[k].api_key = "";
    for (const k of ["discord_url", "ntfy_token", "telegram_token", "webhook_url"]) draft.notifications[k] = "";
    draft._clear_secrets = [];
    orig = JSON.stringify(draft);
  }

  async function load() {
    const [s, o] = await Promise.all([api.get("/settings"), options ? Promise.resolve(options) : api.get("/options")]);
    if (!alive) return;
    options = o;
    adopt(s);
    paintBar();
    renderAll();
  }
  load().catch((e) => { clear(body); body.append(h("div", { class: "err" }, errText(e))); });
  return () => { alive = false; root.classList.remove("has-dock"); if (observer) observer.disconnect(); if (dock) dock.destroy(); bar.remove(); };
}
