"""
Application logic, independent of the HTTP layer (easy to test).
routes.py maps URLs to the methods of TidyfinApp.
"""
from __future__ import annotations

import io
import re
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

from . import __version__
from .badges import BadgeConfig, BadgeStyle, PRESETS as BADGE_PRESETS, RenderBadge, compose_badges, render_text, sample_poster
from .cleanup import CleanupEngine, build_queue_view, midnight_utc, _parse_dt, _utcnow
from .clients import ClientFactory
from .cycle import build_context, run_cycle
from .errors import (AuthError, BadRequest, Conflict, Forbidden, NotFound, RateLimited, SetupError, UpstreamError)
from .logbus import log
from .notify import Notifier
from .profiles import PRESETS as LANGUAGE_PRESETS, SUGGESTED_FOR_LOCALE, preset_profile, presets_public
from .scheduler import Scheduler
from .settings import (COLLECTION_NAMES, COUNTDOWN_TEXT, LOCALES, Settings, apply_patch, load_settings,
                       normalize_url, save_settings)
from .store import Store

_KEY_RE = re.compile(r"^(movie|series)_\d+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BULK_ACTIONS = ("protect", "unprotect", "schedule", "postpone", "unschedule")


class LoginLimiter:
    """Simple in-memory limit on failed logins."""

    def __init__(self, max_attempts: int = 8, window_seconds: int = 300):
        self.max_attempts, self.window = max_attempts, window_seconds
        self._fails: dict = {}
        self._lock = threading.Lock()

    def _prune(self, key, now):
        self._fails[key] = [t for t in self._fails.get(key, []) if now - t < self.window]

    def check(self, key: str):
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            if len(self._fails[key]) >= self.max_attempts:
                raise RateLimited("Too many failed login attempts. Try again in a few minutes.", code="rate_limited")

    def fail(self, key: str):
        with self._lock:
            self._fails.setdefault(key, []).append(time.monotonic())

    def success(self, key: str):
        with self._lock:
            self._fails.pop(key, None)


def _check_key(key: str) -> str:
    if not _KEY_RE.match(key or ""):
        raise BadRequest("Invalid item key.", code="invalid_key")
    return key


class TidyfinApp:
    def __init__(self, data_dir, factory: Optional[ClientFactory] = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.factory = factory or ClientFactory()
        self.store = Store(self.data_dir / "tidyfin.db")
        self.scheduler = Scheduler(self.store, self._run_cycle)
        self.limiter = LoginLimiter()

    # ---------- lifecycle ----------

    def _run_cycle(self, settings: Settings) -> dict:
        return run_cycle(build_context(settings, self.store, self.data_dir, self.factory))

    def start(self):
        self.scheduler.start()

    def stop(self):
        self.scheduler.stop()

    # ---------- settings ----------

    def settings(self) -> Settings:
        return load_settings(self.store)

    def public_settings(self) -> dict:
        return self.settings().to_public_dict()

    def update_settings(self, patch: dict) -> dict:
        new = apply_patch(self.settings(), patch)
        save_settings(self.store, new)
        log.info("Settings saved (dry_run=%s)", new.dry_run)
        return new.to_public_dict()

    def export_settings(self) -> dict:
        """Settings without any secret and without the connections - safe to
        attach to a bug report or move to another installation."""
        d = self.public_settings()
        for k in ("setup_complete", "jellyfin", "radarr", "sonarr"):
            d.pop(k, None)
        return {"tidyfin_version": __version__, "exported_at": _utcnow().isoformat(timespec="seconds"), "settings": d}

    def import_settings(self, payload: dict) -> dict:
        data = payload.get("settings") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise BadRequest("Not a Tidyfin settings export.", code="bad_import")
        data = {k: v for k, v in data.items() if k not in ("setup_complete", "jellyfin", "radarr", "sonarr")}
        return self.update_settings(data)

    def options(self) -> dict:
        """Static choices for the dashboard editors (presets, corners...)."""
        return {"badge_presets": BADGE_PRESETS, "language_presets": presets_public(),
                "suggested_for_locale": SUGGESTED_FOR_LOCALE, "locales": list(LOCALES),
                "defaults": {"countdown_text": COUNTDOWN_TEXT, "collection_names": COLLECTION_NAMES}}

    # ---------- first-run wizard ----------

    def setup_jellyfin(self, url: str, username: str, password: str, api_key: str = "",
                       locale: str = "en", languages: Optional[list] = None) -> dict:
        """Connect to Jellyfin with an admin login. Only possible until setup is complete."""
        current = self.settings()
        if current.setup_complete:
            raise SetupError("Setup is already complete.", code="setup_done")
        url = normalize_url(url)
        if not url:
            raise SetupError("Enter the Jellyfin address.", code="url_required")
        client = self.factory.jellyfin(url, "", current.request_timeout)
        try:
            client.public_info()
        except (UpstreamError, requests.RequestException) as e:
            raise SetupError(f"Cannot reach Jellyfin at {url}: {e}", code="jellyfin_unreachable") from e
        auth = client.authenticate_by_name(username, password)
        if not auth["is_admin"]:
            raise Forbidden("This account is not a Jellyfin administrator.", code="not_admin")
        key = (api_key or "").strip()
        if not key:
            try:
                key = client.ensure_api_key(auth["access_token"])
            except (UpstreamError, requests.RequestException) as e:
                raise SetupError("Could not create an API key automatically (%s). Create one in the Jellyfin "
                                 "dashboard under API Keys and enter it in the wizard." % e, code="api_key_failed") from e
        locale = locale if locale in LOCALES else "en"
        profiles = [preset_profile(c).to_dict() for c in (languages or []) if preset_profile(c)]
        new = apply_patch(current, {
            "jellyfin": {"url": url, "api_key": key}, "manual_user_id": current.manual_user_id or auth["user_id"],
            "locale": locale, "leaving_movies_collection_name": COLLECTION_NAMES[locale][0],
            "leaving_tv_collection_name": COLLECTION_NAMES[locale][1],
            "badges": {"countdown": {"text": COUNTDOWN_TEXT[locale]}}, "language": {"profiles": profiles}})
        new.setup_complete = True
        save_settings(self.store, new)
        log.info("Wizard: connected to Jellyfin (%s) as %s", url, auth["user_name"])
        token = self.store.create_session(auth["user_id"], auth["user_name"], True)
        return {"token": token, "user": {"id": auth["user_id"], "name": auth["user_name"], "is_admin": True}}

    # ---------- login ----------

    def login(self, username: str, password: str, client_id: str = "") -> dict:
        limiter_key = f"{client_id}|{(username or '').lower()}"
        self.limiter.check(limiter_key)
        settings = self.settings()
        if not settings.setup_complete or not settings.jellyfin.url:
            raise SetupError("Tidyfin is not set up yet.", code="not_set_up")
        client = self.factory.jellyfin(settings.jellyfin.url, "", settings.request_timeout)
        try:
            auth = client.authenticate_by_name(username, password)
        except AuthError:
            self.limiter.fail(limiter_key)
            raise
        if not auth["is_admin"]:
            self.limiter.fail(limiter_key)
            raise Forbidden("Only Jellyfin administrators can sign in.", code="not_admin")
        self.limiter.success(limiter_key)
        token = self.store.create_session(auth["user_id"], auth["user_name"], True)
        return {"token": token, "user": {"id": auth["user_id"], "name": auth["user_name"], "is_admin": True}}

    def session_user(self, token: Optional[str]) -> Optional[dict]:
        sess = self.store.get_session(token or "")
        if not sess or not sess["is_admin"]:
            return None
        return {"id": sess["user_id"], "name": sess["user_name"], "is_admin": True}

    def logout(self, token: Optional[str]):
        if token:
            self.store.delete_session(token)

    # ---------- connection tests ----------

    def test_connection(self, service: str, url: str = "", api_key: str = "") -> dict:
        cur = self.settings()
        conn = {"jellyfin": cur.jellyfin, "radarr": cur.radarr, "sonarr": cur.sonarr}.get(service)
        if conn is None:
            raise NotFound(f"Unknown service: {service}", code="unknown_service")
        url, api_key = normalize_url(url) or conn.url, (api_key or "").strip() or conn.api_key
        if not url or not api_key:
            return {"ok": False, "code": "missing_credentials", "error": "Address or API key missing."}
        try:
            info = (self.factory.jellyfin(url, api_key, cur.request_timeout).ping() if service == "jellyfin"
                    else self.factory.arr(service, url, api_key, cur.request_timeout).ping())
            return {"ok": True, **info}
        except UpstreamError as e:
            return {"ok": False, "code": e.code, "error": str(e)}
        except requests.RequestException as e:
            return {"ok": False, "code": "unreachable", "error": str(e)}

    def jellyfin_users(self) -> list:
        s = self.settings()
        users = self.factory.jellyfin(s.jellyfin.url, s.jellyfin.api_key, s.request_timeout).users()
        if users is None:
            raise UpstreamError("Could not list the Jellyfin users.", code="users_failed")
        return [{"id": u["Id"], "name": u["Name"], "is_admin": bool((u.get("Policy") or {}).get("IsAdministrator"))}
                for u in users]

    # ---------- status ----------

    @staticmethod
    def _disk(path_str: str) -> Optional[dict]:
        path = Path(path_str)
        if not path.exists():
            return None
        try:
            total, used, free = shutil.disk_usage(path)
        except OSError:
            return None
        return {"path": str(path), "total": total, "used": used, "free": free, "percent": round(used / total * 100, 1)}

    def cleanup_status(self, s: Settings) -> dict:
        """Is automatic cleanup running, or waiting for the disk to fill up?"""
        engine = CleanupEngine(s, self.store, None, None, None)
        types = {}
        for media_type in ("movie", "series"):
            why = engine.cleanup_gate(media_type)
            usage = engine.disk_usage_percent(media_type)
            types[media_type] = {"active": why is None, "usage": round(usage, 1) if usage is not None else None,
                                 "reason": why["reason"] if why else None}
        return {"start_percent": s.cleanup_start_percent, "types": types}

    def status(self) -> dict:
        s = self.settings()
        queue = build_queue_view(self.store)
        language = self.store.get_json("language_report") or {}
        disks = {}
        for label, path in (("main", s.disk_path), ("movies", s.disk_path_movies), ("series", s.disk_path_series)):
            if path and (label == "main" or path != s.disk_path):
                disks[label] = self._disk(path)
        return {
            "version": __version__, "setup_complete": s.setup_complete, "dry_run": s.dry_run, "locale": s.locale,
            "cleanup": self.cleanup_status(s),
            "connections": {"jellyfin": s.jellyfin.configured, "radarr": s.radarr.configured, "sonarr": s.sonarr.configured},
            "scheduler": self.scheduler.status(), "last_run": self.store.last_run(),
            "disk": disks.get("main"), "disks": disks,
            "disk_thresholds": [{"usage_percent": t.usage_percent, "movies_days": t.movies_days, "series_days": t.series_days}
                                for t in s.disk_thresholds],
            "jellyfin_web_url": s.jellyfin_public_url or s.jellyfin.url,
            "library": {"total": self.store.library_count(), "protected": len(self.store.list_protected())},
            "queue": {"total": len(queue), "due": sum(1 for q in queue if q["due"]),
                      "reclaimable_bytes": sum(q["size_bytes"] for q in queue)},
            "alerts": [{"service": k, "cycles": v["fails"]} for k, v in (self.store.get_json("conn_health", {}) or {}).items() if v.get("alerted")],
            "language": {"labeled": language.get("labeled", 0), "uncertain": len(language.get("uncertain", [])),
                         "updated_at": language.get("updated_at")}}

    # ---------- queue / library ----------

    def queue(self) -> list:
        protected = {p["key"] for p in self.store.list_protected()}
        items = build_queue_view(self.store)
        for i in items:
            i["protected"] = i["key"] in protected
        return items

    def library(self, media_type=None, q="", status="all", sort="title", descending=False, limit=60, offset=0) -> dict:
        return self.store.query_library(media_type, q, status if status in ("all", "protected", "queued", "free") else "all",
                                        sort, bool(descending), min(max(int(limit), 1), 200), max(int(offset), 0))

    def protected(self) -> list:
        return self.store.list_protected()

    def item_detail(self, key: str) -> dict:
        row = self.store.get_library_item(_check_key(key))
        if not row:
            raise NotFound("This item is not in the library snapshot yet.", code="not_in_library")
        queue = next((q for q in build_queue_view(self.store) if q["key"] == key), None)
        last_played, checked, language = None, False, None
        s = self.settings()
        if row.get("jellyfin_id") and s.jellyfin.configured:
            jf = self.factory.jellyfin(s.jellyfin.url, s.jellyfin.api_key, s.request_timeout)
            try:
                dt, checked = jf.last_played_across_users(row["jellyfin_id"], row["media_type"])
                last_played = dt.isoformat() if dt else None
            except requests.RequestException:
                checked = False
            if s.language.active_profiles:
                language = {"profiles": (self.store.get_json("language_detail", {}) or {}).get(key, [])}
        keep_until = (self.store.get_json("keep_until", {}) or {}).get(key) if row.get("protected") else None
        return {**row, "queue": queue, "last_played": last_played, "last_played_checked": checked, "language": language, "keep_until": keep_until,
                "jellyfin_web_url": s.jellyfin_public_url or s.jellyfin.url}

    def preview(self, patch=None) -> dict:
        """What would qualify. ``patch`` tries thresholds that are not saved yet."""
        s = apply_patch(self.settings(), patch) if patch else self.settings()
        if not s.jellyfin.configured:
            raise SetupError("No Jellyfin connection.", code="no_jellyfin")
        ctx = build_context(s, self.store, self.data_dir, self.factory)
        ctx.jf.reset_cycle_cache()
        return CleanupEngine(s, self.store, ctx.jf, ctx.radarr, ctx.sonarr).preview()

    # ---------- item actions ----------

    def _row(self, key: str) -> dict:
        row = self.store.get_library_item(_check_key(key))
        if not row:
            raise NotFound("This item is not in the library snapshot yet. Run a cycle first.", code="not_in_library")
        return row

    def _arr(self, row: dict, s: Settings):
        client = self.factory.arr("radarr" if row["media_type"] == "movie" else "sonarr",
                                  (s.radarr if row["media_type"] == "movie" else s.sonarr).url,
                                  (s.radarr if row["media_type"] == "movie" else s.sonarr).api_key, s.request_timeout)
        if not client.configured:
            raise Conflict(f"{client.kind.capitalize()} is not configured.", code="arr_not_configured")
        if not row.get("arr_id"):
            raise Conflict("This item has no Radarr/Sonarr id.", code="no_arr_id")
        return client

    def _drop_from_queues(self, key: str) -> list:
        removed = []
        for loader, saver, name in ((self.store.load_auto_state, self.store.save_auto_state, "auto"),
                                    (self.store.load_manual_state, self.store.save_manual_state, "manual")):
            state = loader()
            if key in state:
                del state[key]
                saver(state)
                removed.append(name)
        return removed

    def protect(self, key: str, days=None, date=None) -> dict:
        """Keep: add the protection tag in Radarr/Sonarr and drop the item from the queues.
        With ``days`` or ``date`` the protection ends by itself on that day."""
        row, s = self._row(key), self.settings()
        until = self._deadline(days, date) if (days is not None or date) else None
        client = self._arr(row, s)
        try:
            tag_id = client.ensure_tag(s.protect_tag)
            client.edit_tags(row["arr_id"], [tag_id], "add")
        except requests.RequestException as e:
            raise UpstreamError(str(e), code="arr_unreachable") from e
        tags = row["tags"] if s.protect_tag.lower() in {t.lower() for t in row["tags"]} else row["tags"] + [s.protect_tag.lower()]
        self.store.update_library_item(key, tags=tags, protected=True)
        self._drop_from_queues(key)
        keeps = self.store.get_json("keep_until", {}) or {}
        if until:
            keeps[key] = until.isoformat()
        else:
            keeps.pop(key, None)
        self.store.set_json("keep_until", keeps)
        self.store.add_history("protected", key, row["title"], row["media_type"], "dashboard", {"until": until.isoformat()} if until else None)
        return {"key": key, "title": row["title"], "until": until.isoformat() if until else None}

    def unprotect(self, key: str) -> dict:
        row, s = self._row(key), self.settings()
        keeps = self.store.get_json("keep_until", {}) or {}
        if keeps.pop(key, None) is not None:
            self.store.set_json("keep_until", keeps)
        client = self._arr(row, s)
        protected_labels = [t for t in row["tags"] if t.lower() in s.protected_tags]
        try:
            ids = [tid for tid, label in client.tags().items() if label in protected_labels]
            if ids:
                client.edit_tags(row["arr_id"], ids, "remove")
        except requests.RequestException as e:
            raise UpstreamError(str(e), code="arr_unreachable") from e
        remaining = [t for t in row["tags"] if t not in protected_labels]
        self.store.update_library_item(key, tags=remaining, protected=False)
        self.store.add_history("unprotected", key, row["title"], row["media_type"], "dashboard")
        return {"key": key, "title": row["title"]}

    @staticmethod
    def _deadline(days=None, date=None) -> datetime:
        """Resolve either a number of days from today or a calendar date to a UTC midnight."""
        now = _utcnow()
        if date:
            if not _DATE_RE.match(str(date)):
                raise BadRequest("Invalid date.", code="invalid_date")
            try:
                d = datetime.strptime(str(date), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError as e:
                raise BadRequest("Invalid date.", code="invalid_date") from e
            if d < midnight_utc(now):
                raise BadRequest("The date is in the past.", code="date_in_past")
            return d
        try:
            n = int(days)
        except (TypeError, ValueError) as e:
            raise BadRequest("Give a number of days or a date.", code="days_required") from e
        if not 0 <= n <= 3650:
            raise BadRequest("Days must be between 0 and 3650.", code="invalid_days")
        return midnight_utc(now + timedelta(days=n))

    def schedule(self, key: str, days=None, date=None) -> dict:
        """Delete from the dashboard: put the item into the manual queue with a chosen deadline."""
        row = self._row(key)
        if row["protected"]:
            raise Conflict("The item is protected. Remove the protection first.", code="item_protected")
        deadline = self._deadline(days, date)
        self._drop_from_queues(key)
        state = self.store.load_manual_state()
        state[key] = {"title": row["title"], "media_type": row["media_type"], "tmdb_id": row.get("tmdb_id"),
                      "tvdb_id": row.get("tvdb_id"), "arr_id": row.get("arr_id"), "jellyfin_id": row.get("jellyfin_id"),
                      "size_bytes": row.get("size_bytes", 0), "marked_at": _utcnow().isoformat(),
                      "delete_at": deadline.isoformat(), "origin": "dashboard"}
        self.store.save_manual_state(state)
        self.store.add_history("scheduled", key, row["title"], row["media_type"], "dashboard", {"delete_at": deadline.isoformat()})
        return {"key": key, "delete_at": deadline.isoformat()}

    def unschedule(self, key: str) -> dict:
        _check_key(key)
        manual = self.store.load_manual_state()
        if key not in manual:
            if key in self.store.load_auto_state():
                raise Conflict("Automatic queue items cannot be cancelled; keep or postpone them instead.", code="auto_item")
            raise NotFound("The item is not in the queue.", code="not_in_queue")
        info = manual.pop(key)
        self.store.save_manual_state(manual)
        self.store.add_history("dequeued", key, info.get("title", ""), info.get("media_type", ""), "dashboard", {"reason": "cancelled"})
        return {"key": key}

    def postpone(self, key: str, days=None, date=None) -> dict:
        """Move the deadline later: ``days`` extends from the current deadline, ``date`` sets it exactly."""
        _check_key(key)
        for loader, saver in ((self.store.load_auto_state, self.store.save_auto_state),
                              (self.store.load_manual_state, self.store.save_manual_state)):
            state = loader()
            if key not in state:
                continue
            info = state[key]
            if date:
                new = self._deadline(None, date)
            else:
                try:
                    n = int(days)
                except (TypeError, ValueError) as e:
                    raise BadRequest("Give a number of days or a date.", code="days_required") from e
                if not 1 <= n <= 3650:
                    raise BadRequest("Days must be between 1 and 3650.", code="invalid_days")
                new = midnight_utc(max(_parse_dt(info["delete_at"]), _utcnow()) + timedelta(days=n))
            info["delete_at"] = new.isoformat()
            info["postponed"] = True          # disk-usage shortening must not undo a manual decision
            info.pop("accelerated", None)
            saver(state)
            self.store.add_history("postponed", key, info.get("title", ""), info.get("media_type", ""), "dashboard",
                                   {"delete_at": info["delete_at"]})
            return {"key": key, "delete_at": info["delete_at"]}
        raise NotFound("The item is not in the queue.", code="not_in_queue")

    def bulk(self, action: str, keys: list, params: Optional[dict] = None) -> dict:
        if action not in BULK_ACTIONS:
            raise BadRequest("Unknown action.", code="unknown_action")
        if not isinstance(keys, list) or not keys or len(keys) > 500:
            raise BadRequest("Provide 1-500 item keys.", code="invalid_keys")
        params = params or {}
        results = {}
        for key in keys:
            try:
                if action == "protect":
                    self.protect(key)
                elif action == "unprotect":
                    self.unprotect(key)
                elif action == "schedule":
                    self.schedule(key, params.get("days"), params.get("date"))
                elif action == "postpone":
                    self.postpone(key, params.get("days"), params.get("date"))
                else:
                    self.unschedule(key)
                results[key] = {"ok": True}
            except (BadRequest, Conflict, NotFound, UpstreamError) as e:
                results[key] = {"ok": False, "code": e.code, "detail": str(e)}
        return {"results": results, "ok": sum(1 for r in results.values() if r["ok"]), "failed": sum(1 for r in results.values() if not r["ok"])}

    # ---------- posters ----------

    def poster(self, key: str, width: int = 300) -> bytes:
        """Clean poster of an item: the backup of the original (no badges) or,
        without a backup, the current poster from Jellyfin."""
        _check_key(key)
        width = max(80, min(int(width), 600))
        data = None
        backup = self.data_dir / "poster_backups" / f"{key}.jpg"
        if backup.exists():
            data = backup.read_bytes()
        if data is None:
            jid = None
            for state in (self.store.load_auto_state(), self.store.load_manual_state()):
                if state.get(key, {}).get("jellyfin_id"):
                    jid = state[key]["jellyfin_id"]
                    break
            row = self.store.get_library_item(key)
            jid = jid or (row or {}).get("jellyfin_id") or self.store.load_library_id_cache().get(key)
            s = self.settings()
            if not jid or not s.jellyfin.configured:
                raise NotFound("No poster.", code="no_poster")
            data = self.factory.jellyfin(s.jellyfin.url, s.jellyfin.api_key, s.request_timeout).get_primary_image(jid, max_width=width)
        if not data:
            raise NotFound("No poster.", code="no_poster")
        return _thumbnail(data, width)

    def badge_preview(self, badges: list, width: int = 360, key: str = "") -> bytes:
        """Render the given (unsaved) badges on a poster - the same code that
        draws the real posters. ``key`` picks a title from the library (its clean
        poster, without any badge); without it, or when it has no poster, a
        generated sample is used."""
        if not isinstance(badges, list) or len(badges) > 12:
            raise BadRequest("Provide up to 12 badges.", code="invalid_badges")
        width = max(160, min(int(width), 600))
        s = self.settings()
        sample = (_utcnow() + timedelta(days=6)).strftime(s.badges.date_format)
        render = []
        for b in badges:
            cfg = BadgeConfig.from_dict(b)
            if cfg.enabled and cfg.text:
                render.append(RenderBadge(render_text(cfg.text, sample, 7), cfg.corner, cfg.order, cfg.style))
        base = None
        if key:
            try:
                base = self.poster(key, width)
            except (NotFound, BadRequest, requests.RequestException):
                base = None
        return compose_badges(base or sample_poster(width), render)

    def restore_all_posters(self) -> dict:
        """Put every original poster back (e.g. before uninstalling)."""
        s = self.settings()
        if not s.jellyfin.configured:
            raise SetupError("No Jellyfin connection.", code="no_jellyfin")
        ctx = build_context(s, self.store, self.data_dir, self.factory)
        ctx.jf.reset_cycle_cache()
        cache, restored, failed = self.store.load_library_id_cache(), 0, 0
        for key in ctx.posters.backup_keys():
            jid = cache.get(key) or ctx.jf.lookup_by_key(key)
            if ctx.posters.restore_original(key, jid):
                restored += 1
            else:
                failed += 1
        log.info("Restored %d original posters (%d could not be restored)", restored, failed)
        return {"restored": restored, "failed": failed}


    # ---------- summary ----------

    SAVED_REASONS = ("watched", "favorited", "in_progress", "protected")

    def summary(self, days: int = 30) -> dict:
        """What the cleanup did over the last ``days`` days: space reclaimed and
        titles saved (watched in time, starred or in progress for someone else,
        kept with the protection tag)."""
        days = max(1, min(int(days), 3650))
        since = (_utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
        out = {"days": days, "deleted": {"count": 0, "bytes": 0, "movies": 0, "series": 0},
               "saved": {"count": 0, "bytes": 0, "by_reason": {}}, "queued": 0}
        for ev in self.store.history_since(since):
            kind, details = ev["event"], ev.get("details") or {}
            if kind == "deleted":
                out["deleted"]["count"] += 1
                out["deleted"]["bytes"] += int(details.get("size_bytes") or 0)
                out["deleted"]["movies" if ev["media_type"] == "movie" else "series"] += 1
            elif kind in ("queued", "scheduled", "marked"):
                out["queued"] += 1
            elif kind == "protected" or (kind == "dequeued" and details.get("reason") in self.SAVED_REASONS):
                reason = "protected" if kind == "protected" else details["reason"]
                out["saved"]["count"] += 1
                out["saved"]["bytes"] += int(details.get("size_bytes") or 0)
                out["saved"]["by_reason"][reason] = out["saved"]["by_reason"].get(reason, 0) + 1
        return out

    # ---------- language tags written to Jellyfin ----------

    def set_language_tags(self, key: str, profile_code: str, kinds: list, dismiss: bool = False) -> dict:
        """Write the manual language tags of one profile to the item in Jellyfin
        (replacing that profile's earlier tags). The tags win over every other
        detection layer on the next cycle."""
        row, s = self._row(key), self.settings()
        profile = next((p for p in s.language.profiles if p.code == profile_code), None)
        if profile is None:
            raise NotFound("Unknown language profile.", code="unknown_profile")
        if not row.get("jellyfin_id") or not s.jellyfin.configured:
            raise Conflict("The item is not in Jellyfin.", code="not_in_jellyfin")
        wanted = [k for k in ("voiceover", "dub", "audio", "subs") if k in (kinds or [])]
        tag_map = profile.tag_map()
        every = {t for names in tag_map.values() for t in names}
        add = [tag_map["dismiss"][0]] if dismiss and tag_map["dismiss"] else [tag_map[k][0] for k in wanted if tag_map[k]]
        jf = self.factory.jellyfin(s.jellyfin.url, s.jellyfin.api_key, s.request_timeout)
        try:
            ok = jf.set_item_tags(row["jellyfin_id"], add=add, remove=sorted(every))
        except requests.RequestException as e:
            raise UpstreamError(str(e), code="jellyfin_unreachable") from e
        if not ok:
            raise UpstreamError("Jellyfin did not accept the tag change.", code="tags_failed")
        self.store.add_history("language_tagged", key, row["title"], row["media_type"], "dashboard", {"tags": add})
        return {"key": key, "tags": add}

    # ---------- notifications ----------

    def notify_test(self) -> dict:
        s = self.settings()
        if not s.notifications.channels:
            raise BadRequest("No notification channel is configured.", code="no_channels")
        return {"results": Notifier(s.notifications).send_test(s.locale)}


def _thumbnail(data: bytes, width: int) -> bytes:
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except (UnidentifiedImageError, OSError) as e:
        raise NotFound("Invalid poster file.", code="no_poster") from e
    if img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=82)
    return out.getvalue()
