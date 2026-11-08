"""One full Tidyfin cycle."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .badges import RenderBadge, render_text
from .cleanup import CleanupEngine, _parse_dt, _utcnow, countdown_date
from .clients import ClientFactory
from .language import LanguageDetector
from .leaving import ensure_collection_id, sync_collection
from .library import refresh_library
from .logbus import log
from .notify import notify_connection, notify_cycle
from .posters import PosterService
from .settings import Settings


@dataclass
class Context:
    settings: Settings
    store: object
    jf: object
    radarr: object
    sonarr: object
    posters: PosterService


def build_context(settings: Settings, store, data_dir, factory: Optional[ClientFactory] = None) -> Context:
    factory = factory or ClientFactory()
    t = settings.request_timeout
    jf = factory.jellyfin(settings.jellyfin.url, settings.jellyfin.api_key, t)
    return Context(settings=settings, store=store, jf=jf,
                   radarr=factory.arr("radarr", settings.radarr.url, settings.radarr.api_key, t),
                   sonarr=factory.arr("sonarr", settings.sonarr.url, settings.sonarr.api_key, t),
                   posters=PosterService(jf, Path(data_dir) / "poster_backups"))


def _in_window(settings: Settings, delete_at_iso: str) -> bool:
    """
    Determines when to show a countdown on the poster and add an item to the "Leaving soon" collection.

    Both use the same rule: only announce departures when the deadline is close enough. Set to 0 to always show.
    """
    window = settings.badges.countdown_show_within_days
    days_left = max((_parse_dt(delete_at_iso) - _utcnow()).days, 0)
    return not window or days_left <= window


def _countdown_badge(settings: Settings, delete_at_iso: str):
    cfg = settings.badges.countdown
    if not cfg.enabled:
        return None
    delete_at = _parse_dt(delete_at_iso)
    days_left = max((delete_at - _utcnow()).days, 0)
    if not _in_window(settings, delete_at_iso):
        return None                      # too far away: no badge yet
    text = render_text(cfg.text, countdown_date(delete_at).strftime(settings.badges.date_format), days_left)
    return RenderBadge(text, cfg.corner, cfg.order, cfg.style)


def _language_badges(settings: Settings, labels: list) -> list:
    by_code = {p.code: p for p in settings.language.profiles}
    out = []
    for lab in labels:
        profile = by_code.get(lab["profile"])
        cfg = profile.badges.get(lab["kind"]) if profile else None
        if cfg and cfg.enabled:
            out.append(RenderBadge(cfg.text, cfg.corner, cfg.order, cfg.style))
    return out


_REPORTED_MISSING: set = set()      # titles already reported as missing in Jellyfin (once, not every cycle)


def _verified_queue(ctx: Context, auto_map: dict, manual_state: dict) -> dict:
    """
    Stores all queued titles with a Jellyfin ID, including their media type and deletion date. Manual deadlines take priority over automatic ones.

    Before updating collections, Tidyfin checks every ID:
    - Updated if the title has moved.
    - Skipped if the title no longer exists.
    - Kept if Jellyfin is unavailable, so nothing is lost.
    """
    queued = {k: dict(v) for k, v in auto_map.items() if v.get("jellyfin_id")}
    for key, info in manual_state.items():
        if info.get("jellyfin_id"):
            if key in queued:
                log.warning("'%s' is in both the auto and the manual queue - showing the manual deadline", info["title"])
            queued[key] = {"jellyfin_id": info["jellyfin_id"], "media_type": info["media_type"], "delete_at": info["delete_at"]}
    alive = ctx.jf.existing_item_ids({v["jellyfin_id"] for v in queued.values()})
    if alive is None:
        return queued
    manual_changed = False
    for key, v in list(queued.items()):
        if v["jellyfin_id"] in alive:
            _REPORTED_MISSING.discard(key)
            continue
        fresh = ctx.jf.lookup_by_key(key)
        if fresh:
            v["jellyfin_id"] = fresh
            if key in manual_state:
                manual_state[key]["jellyfin_id"] = fresh
                manual_changed = True
        else:
            if key not in _REPORTED_MISSING:
                _REPORTED_MISSING.add(key)
                log.info("[Jellyfin] %s is queued but not in Jellyfin (yet) - no badge or collection for it until it is", key)
            del queued[key]
    if manual_changed:
        ctx.store.save_manual_state(manual_state)
    return queued


CONNECTION_ALERT_AFTER = 3      # cycles in a row without an answer before the dashboard and notifications speak up


def _check_connections(ctx: Context, lib_counts: dict):
    """
    Tracks consecutive cycles where Radarr, Sonarr, or Jellyfin are unavailable.

    Tidyfin skips checks for unavailable services and notifies the admin after several failed cycles.
    """
    s = ctx.settings
    bad = {}
    if s.radarr.configured:
        bad["radarr"] = lib_counts.get("movie") is None
    if s.sonarr.configured:
        bad["sonarr"] = lib_counts.get("series") is None
    if s.jellyfin.configured:
        bad["jellyfin"] = ctx.jf.users() is None
    old = ctx.store.get_json("conn_health", {}) or {}
    new = {}
    for service, failed in bad.items():
        prev = old.get(service) or {"fails": 0, "alerted": False}
        if failed:
            fails, alerted = prev["fails"] + 1, prev["alerted"]
            if fails >= CONNECTION_ALERT_AFTER and not alerted:
                alerted = True
                log.warning("[Connection] %s has not answered for %d cycles in a row", service.capitalize(), fails)
                notify_connection(s, service, fails, lost=True)
            new[service] = {"fails": fails, "alerted": alerted}
        else:
            if prev["alerted"]:
                log.info("[Connection] %s answers again", service.capitalize())
                notify_connection(s, service, 0, lost=False)
            new[service] = {"fails": 0, "alerted": False}
    ctx.store.set_json("conn_health", new)


def run_cycle(ctx: Context) -> dict:
    """ auto cleanup -> manual stars -> language detection -> poster badges -> collections -> restore unused posters -> notifications. """
    s, store, jf = ctx.settings, ctx.store, ctx.jf
    jf.reset_cycle_cache()
    engine = CleanupEngine(s, store, jf, ctx.radarr, ctx.sonarr)
    manual_state = store.load_manual_state()

    # 0) library snapshot for the dashboard
    lib_counts = refresh_library(s, store, jf, ctx.radarr, ctx.sonarr)

    _check_connections(ctx, lib_counts)
    engine.expire_keeps()
    engine.drop_vanished_auto(lib_counts)

    # 1) automatic cleanup
    auto_map = engine.process_automatic_cleanup()

    # 2) manual deletion: stars in Jellyfin plus items scheduled from the dashboard
    engine.drop_vanished_manual(manual_state)
    engine.process_manual_new_favorites(manual_state)
    engine.process_manual_due_items(manual_state)
    store.save_manual_state(manual_state)
    if manual_state:
        log.info("[Manual] Waiting in the queue: %d", len(manual_state))

    # 3) language labels for the whole library
    known_ids = {k: v["jellyfin_id"] for k, v in auto_map.items() if v.get("jellyfin_id")}
    known_ids.update({k: v["jellyfin_id"] for k, v in manual_state.items() if v.get("jellyfin_id")})
    language_map, uncertain = LanguageDetector(jf, s.language, store).compute(known_ids)
    by_profile: dict = {}
    for v in language_map.values():
        for lab in v["labels"]:
            by_profile.setdefault(lab["profile"], {}).setdefault(lab["kind"], 0)
            by_profile[lab["profile"]][lab["kind"]] += 1
    store.set_json("language_report", {"updated_at": _utcnow().isoformat(timespec="seconds"),
                                       "labeled": len(language_map), "by_profile": by_profile, "uncertain": uncertain})

    # 4) queue with verified Jellyfin ids, then poster badges: countdown + language labels
    queued = _verified_queue(ctx, auto_map, manual_state)
    combined: dict = {}
    if s.badges.enabled:
        for key, v in queued.items():
            badge = _countdown_badge(s, v["delete_at"])
            if badge:
                combined.setdefault(key, {"jellyfin_id": v["jellyfin_id"], "badges": []})["badges"].append(badge)
        for key, v in language_map.items():
            badges = _language_badges(s, v["labels"])
            if badges:
                combined.setdefault(key, {"jellyfin_id": v["jellyfin_id"], "badges": []})["badges"].extend(badges)
        for key, data in combined.items():
            ctx.posters.render_item_badges(key, data["jellyfin_id"], data["badges"])

    # 5) "Leaving soon" collections: only titles whose deadline is close (the same window as the countdown)
    movie_ids, series_ids = set(), set()
    for v in queued.values():
        if _in_window(s, v["delete_at"]):
            (movie_ids if v["media_type"] == "movie" else series_ids).add(v["jellyfin_id"])
    movies_col = ensure_collection_id(jf, store, s.leaving_movies_collection_id, s.leaving_movies_collection_name, "movies")
    tv_col = ensure_collection_id(jf, store, s.leaving_tv_collection_id, s.leaving_tv_collection_name, "tv")
    if movies_col:
        sync_collection(jf, movies_col, movie_ids)
    if tv_col:
        sync_collection(jf, tv_col, series_ids)
    log.info("[Collections] Synced: %d movies, %d series", len(movie_ids), len(series_ids))

    # 6) restore originals for backups no longer used by any badge
    id_cache = store.load_library_id_cache()
    for key in ctx.posters.backup_keys():
        if key in combined:
            continue
        jid = ((auto_map.get(key) or {}).get("jellyfin_id") or (manual_state.get(key) or {}).get("jellyfin_id")
               or (language_map.get(key) or {}).get("jellyfin_id") or id_cache.get(key) or jf.lookup_by_key(key))
        log.info("  -> %s is no longer covered by any badge, restoring the original", key)
        ctx.posters.restore_original(key, jid)

    usage = engine.disk_usage_percent()
    summary = {
        "dry_run": s.dry_run, "auto_queue": len(auto_map), "manual_queue": len(manual_state),
        "language_labeled": len(language_map), "language_uncertain": len(uncertain),
        "library": lib_counts, "disk_percent": round(usage, 1) if usage is not None else None,
        "cleanup_paused": engine.paused, **engine.stats}
    try:
        notify_cycle(s, engine.events, summary)
    except Exception as e:      # a notification problem must never fail the cycle
        log.warning("[Notify] %s", e)
    return summary
