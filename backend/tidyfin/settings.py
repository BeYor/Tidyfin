"""
Tidyfin settings.

Everything is one ``Settings`` object persisted in SQLite and editable from
the dashboard. Plain dataclasses (no pydantic) keep the engine testable with
nothing but the standard library, requests and Pillow.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Optional

from .badges import BadgeConfig, BadgeStyle
from .profiles import LanguageProfile

SETTINGS_KV_KEY = "settings"
LOCALES = ("en", "pl")

COUNTDOWN_TEXT = {"en": "AVAILABLE UNTIL {date}", "pl": "DOSTĘPNE DO: {date}"}
COLLECTION_NAMES = {
    "en": ("Leaving soon - movies", "Leaving soon - series"),
    "pl": ("Wkrótce znikają – filmy", "Wkrótce znikają – seriale"),
}

# Setting keys that hold secrets: never sent to the browser, and an empty
# value in a patch means "keep the stored one".
SECRET_PATHS = (
    "jellyfin.api_key", "radarr.api_key", "sonarr.api_key",
    "notifications.discord_url", "notifications.telegram_token",
    "notifications.webhook_url", "notifications.ntfy_token",
)
_SECRET_NAMES = {p.split(".")[-1] for p in SECRET_PATHS}


# ---------- coercion helpers ----------

def _to_int(value, default, minimum=None, maximum=None):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        n = max(minimum, n)
    if maximum is not None:
        n = min(maximum, n)
    return n


def _to_float(value, default, minimum=None, maximum=None):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        n = max(minimum, n)
    if maximum is not None:
        n = min(maximum, n)
    return n


def _to_bool(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _to_str(value, default=""):
    return default if value is None else str(value).strip()


def normalize_url(url: str) -> str:
    url = _to_str(url)
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    return url.rstrip("/")


# ---------- structures ----------

@dataclass
class Connection:
    url: str = ""
    api_key: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Connection":
        d = d or {}
        return cls(url=normalize_url(d.get("url", "")), api_key=_to_str(d.get("api_key", "")))


@dataclass
class MediaRules:
    content_age_threshold_days: int = 45
    last_stream_threshold_days: int = 45
    cleanup_delay_days: int = 14

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "MediaRules":
        d = d or {}
        base = cls()
        return cls(
            content_age_threshold_days=_to_int(d.get("content_age_threshold_days"), base.content_age_threshold_days, 0, 36500),
            last_stream_threshold_days=_to_int(d.get("last_stream_threshold_days"), base.last_stream_threshold_days, 0, 36500),
            cleanup_delay_days=_to_int(d.get("cleanup_delay_days"), base.cleanup_delay_days, 0, 3650),
        )


@dataclass
class DiskThreshold:
    """
        When the disk is at least ``usage_percent`` full, the grace period is
        capped at ``movies_days`` / ``series_days`` (never lengthened).
    """
    usage_percent: float = 85.0
    movies_days: int = 3
    series_days: int = 3

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "DiskThreshold":
        d = d or {}
        return cls(
            usage_percent=_to_float(d.get("usage_percent"), 85.0, 1, 100),
            movies_days=_to_int(d.get("movies_days"), 3, 0, 3650),
            series_days=_to_int(d.get("series_days"), 3, 0, 3650),
        )


@dataclass
class LanguageSettings:
    deep_scan: bool = True
    ffprobe_bin: str = "ffprobe"
    ffprobe_timeout: int = 20
    # When Tidyfin sees the library under a different path than Jellyfin
    # (another mount of the same volume) set both prefixes.
    jellyfin_path_prefix: str = ""
    local_path_prefix: str = ""
    max_episodes: int = 5
    profiles: list = field(default_factory=list)   # list[LanguageProfile]

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "LanguageSettings":
        d = d or {}
        base = cls()
        profiles, seen = [], set()
        for raw in d.get("profiles") or []:
            if not isinstance(raw, dict):
                continue
            p = LanguageProfile.from_dict(raw)
            if p.code in seen:
                continue
            seen.add(p.code)
            profiles.append(p)
        return cls(
            deep_scan=_to_bool(d.get("deep_scan"), base.deep_scan),
            ffprobe_bin=_to_str(d.get("ffprobe_bin"), base.ffprobe_bin) or base.ffprobe_bin,
            ffprobe_timeout=_to_int(d.get("ffprobe_timeout"), base.ffprobe_timeout, 1, 600),
            jellyfin_path_prefix=_to_str(d.get("jellyfin_path_prefix")),
            local_path_prefix=_to_str(d.get("local_path_prefix")),
            max_episodes=_to_int(d.get("max_episodes"), base.max_episodes, 1, 500),
            profiles=profiles[:20],
        )

    def to_dict(self) -> dict:
        return {
            "deep_scan": self.deep_scan, "ffprobe_bin": self.ffprobe_bin, "ffprobe_timeout": self.ffprobe_timeout,
            "jellyfin_path_prefix": self.jellyfin_path_prefix, "local_path_prefix": self.local_path_prefix,
            "max_episodes": self.max_episodes, "profiles": [p.to_dict() for p in self.profiles],
        }

    @property
    def active_profiles(self) -> list:
        return [p for p in self.profiles if p.enabled]


@dataclass
class BadgeSettings:
    enabled: bool = True  # master switch for every poster modification
    date_format: str = "%d.%m.%Y"
    # The countdown badge appears only when at most this many days remain
    # (0 = always show it).
    countdown_show_within_days: int = 14
    countdown: BadgeConfig = field(default_factory=lambda: BadgeConfig(
        text=COUNTDOWN_TEXT["en"], corner="top-left", style=BadgeStyle()))

    @classmethod
    def from_dict(cls, d: Optional[dict], locale: str = "en") -> "BadgeSettings":
        d = d or {}
        base = cls()
        base.countdown.text = COUNTDOWN_TEXT.get(locale, COUNTDOWN_TEXT["en"])
        fmt = _to_str(d.get("date_format"), base.date_format)
        if not fmt or len(fmt) > 20 or "%" not in fmt:
            fmt = base.date_format
        return cls(enabled=_to_bool(d.get("enabled"), True), date_format=fmt,
                   countdown_show_within_days=_to_int(d.get("countdown_show_within_days"), base.countdown_show_within_days, 0, 3650),
                   countdown=BadgeConfig.from_dict(d.get("countdown"), base.countdown))

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "date_format": self.date_format,
                "countdown_show_within_days": self.countdown_show_within_days, "countdown": self.countdown.to_dict()}


@dataclass
class NotifySettings:
    enabled: bool = False
    on_queued: bool = True
    on_deleted: bool = True
    on_errors: bool = True
    discord_url: str = ""
    ntfy_url: str = ""            # full topic URL, e.g. https://ntfy.sh/my-topic
    ntfy_token: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    webhook_url: str = ""         # generic JSON webhook

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "NotifySettings":
        d = d or {}
        base = cls()
        return cls(
            enabled=_to_bool(d.get("enabled"), base.enabled), on_queued=_to_bool(d.get("on_queued"), True),
            on_deleted=_to_bool(d.get("on_deleted"), True), on_errors=_to_bool(d.get("on_errors"), True),
            discord_url=_to_str(d.get("discord_url")), ntfy_url=normalize_url(d.get("ntfy_url", "")),
            ntfy_token=_to_str(d.get("ntfy_token")), telegram_token=_to_str(d.get("telegram_token")),
            telegram_chat_id=_to_str(d.get("telegram_chat_id")), webhook_url=_to_str(d.get("webhook_url")),
        )

    @property
    def channels(self) -> list:
        out = []
        if self.discord_url:
            out.append("discord")
        if self.ntfy_url:
            out.append("ntfy")
        if self.telegram_token and self.telegram_chat_id:
            out.append("telegram")
        if self.webhook_url:
            out.append("webhook")
        return out


@dataclass
class Settings:
    setup_complete: bool = False
    locale: str = "en"                          # language of notifications and default badge texts
    jellyfin: Connection = field(default_factory=Connection)
    manual_user_id: str = ""                    # whose Jellyfin favorites (stars) mark items for deletion
    jellyfin_public_url: str = ""               # Jellyfin address as seen from the browser (for links)
    radarr: Connection = field(default_factory=Connection)
    sonarr: Connection = field(default_factory=Connection)

    grace_days: int = 14                        # grace period for the star flow and dashboard scheduling default
    protect_tag: str = "do-not-delete"          # tag added by the "Keep" button
    exclude_tags: list = field(default_factory=lambda: ["do-not-delete"])
    run_interval_seconds: int = 3600
    # Safe start for a fresh install: nothing is deleted until the admin
    # switches dry run off.
    dry_run: bool = True
    disk_path: str = "/media"
    # Automatic cleanup waits until the disk is at least this full (0 = always
    # active). Small libraries never need to delete anything; manual deletions
    # are not affected.
    cleanup_start_percent: float = 40.0
    disk_path_movies: str = ""                  # optional per-type overrides
    disk_path_series: str = ""
    disk_thresholds: list = field(default_factory=list)
    shorten_existing_queue: bool = False

    # Time-limited protection from other people's activity (0 = off). A star
    # of any user except the one whose stars mark deletions, or a title sitting
    # in someone's "Continue watching", keeps it out of the automatic queue for
    # this many days - not forever, so a forgotten star cannot pin a title.
    favorites_protect_days: int = 60
    resume_protect_days: int = 30

    movies: MediaRules = field(default_factory=MediaRules)
    series: MediaRules = field(default_factory=MediaRules)

    leaving_movies_collection_id: str = ""
    leaving_tv_collection_id: str = ""
    leaving_movies_collection_name: str = COLLECTION_NAMES["en"][0]
    leaving_tv_collection_name: str = COLLECTION_NAMES["en"][1]

    language: LanguageSettings = field(default_factory=LanguageSettings)
    badges: BadgeSettings = field(default_factory=BadgeSettings)
    notifications: NotifySettings = field(default_factory=NotifySettings)
    request_timeout: int = 15

    @property
    def protected_tags(self) -> set:
        """Lower-cased tags that exclude an item from any deletion."""
        return {t.lower() for t in self.exclude_tags} | ({self.protect_tag.lower()} if self.protect_tag else set())

    # ---------- (de)serialization ----------

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Settings":
        d = d or {}
        base = cls()
        locale = d.get("locale") if d.get("locale") in LOCALES else "en"

        tags = d.get("exclude_tags", base.exclude_tags)
        if isinstance(tags, str):
            tags = tags.split(",")
        tags = [str(t).strip() for t in (tags or []) if str(t).strip()]
        protect_tag = _to_str(d.get("protect_tag"), base.protect_tag) or base.protect_tag
        if protect_tag.lower() not in {t.lower() for t in tags}:
            tags.append(protect_tag)

        thresholds = [DiskThreshold.from_dict(t) for t in (d.get("disk_thresholds") or []) if isinstance(t, dict)]
        thresholds.sort(key=lambda t: t.usage_percent)

        names = COLLECTION_NAMES[locale]
        return cls(
            setup_complete=_to_bool(d.get("setup_complete"), False),
            locale=locale,
            jellyfin=Connection.from_dict(d.get("jellyfin")),
            manual_user_id=_to_str(d.get("manual_user_id")),
            jellyfin_public_url=normalize_url(d.get("jellyfin_public_url", "")),
            radarr=Connection.from_dict(d.get("radarr")),
            sonarr=Connection.from_dict(d.get("sonarr")),
            grace_days=_to_int(d.get("grace_days"), base.grace_days, 0, 3650),
            protect_tag=protect_tag,
            exclude_tags=tags,
            run_interval_seconds=_to_int(d.get("run_interval_seconds"), base.run_interval_seconds, 60, 7 * 86400),
            dry_run=_to_bool(d.get("dry_run"), base.dry_run),
            disk_path=_to_str(d.get("disk_path"), base.disk_path) or base.disk_path,
            cleanup_start_percent=_to_float(d.get("cleanup_start_percent"), base.cleanup_start_percent, 0, 100),
            disk_path_movies=_to_str(d.get("disk_path_movies")),
            disk_path_series=_to_str(d.get("disk_path_series")),
            disk_thresholds=thresholds,
            shorten_existing_queue=_to_bool(d.get("shorten_existing_queue"), False),
            favorites_protect_days=_to_int(d.get("favorites_protect_days"), base.favorites_protect_days, 0, 3650),
            resume_protect_days=_to_int(d.get("resume_protect_days"), base.resume_protect_days, 0, 3650),
            movies=MediaRules.from_dict(d.get("movies")),
            series=MediaRules.from_dict(d.get("series")),
            leaving_movies_collection_id=_to_str(d.get("leaving_movies_collection_id")),
            leaving_tv_collection_id=_to_str(d.get("leaving_tv_collection_id")),
            leaving_movies_collection_name=_to_str(d.get("leaving_movies_collection_name"), names[0]) or names[0],
            leaving_tv_collection_name=_to_str(d.get("leaving_tv_collection_name"), names[1]) or names[1],
            language=LanguageSettings.from_dict(d.get("language")),
            badges=BadgeSettings.from_dict(d.get("badges"), locale),
            notifications=NotifySettings.from_dict(d.get("notifications")),
            request_timeout=_to_int(d.get("request_timeout"), base.request_timeout, 1, 300),
        )

    def to_dict(self) -> dict:
        n = self.notifications
        return {
            "setup_complete": self.setup_complete, "locale": self.locale,
            "jellyfin": {"url": self.jellyfin.url, "api_key": self.jellyfin.api_key},
            "manual_user_id": self.manual_user_id, "jellyfin_public_url": self.jellyfin_public_url,
            "radarr": {"url": self.radarr.url, "api_key": self.radarr.api_key},
            "sonarr": {"url": self.sonarr.url, "api_key": self.sonarr.api_key},
            "grace_days": self.grace_days, "protect_tag": self.protect_tag, "exclude_tags": list(self.exclude_tags),
            "run_interval_seconds": self.run_interval_seconds, "dry_run": self.dry_run,
            "disk_path": self.disk_path, "cleanup_start_percent": self.cleanup_start_percent, "disk_path_movies": self.disk_path_movies, "disk_path_series": self.disk_path_series,
            "disk_thresholds": [{"usage_percent": t.usage_percent, "movies_days": t.movies_days, "series_days": t.series_days}
                                for t in self.disk_thresholds],
            "shorten_existing_queue": self.shorten_existing_queue,
            "favorites_protect_days": self.favorites_protect_days, "resume_protect_days": self.resume_protect_days,
            "movies": vars(self.movies).copy(), "series": vars(self.series).copy(),
            "leaving_movies_collection_id": self.leaving_movies_collection_id,
            "leaving_tv_collection_id": self.leaving_tv_collection_id,
            "leaving_movies_collection_name": self.leaving_movies_collection_name,
            "leaving_tv_collection_name": self.leaving_tv_collection_name,
            "language": self.language.to_dict(), "badges": self.badges.to_dict(),
            "notifications": {
                "enabled": n.enabled, "on_queued": n.on_queued, "on_deleted": n.on_deleted, "on_errors": n.on_errors,
                "discord_url": n.discord_url, "ntfy_url": n.ntfy_url, "ntfy_token": n.ntfy_token,
                "telegram_token": n.telegram_token, "telegram_chat_id": n.telegram_chat_id, "webhook_url": n.webhook_url},
            "request_timeout": self.request_timeout,
        }

    def to_public_dict(self) -> dict:
        """Browser-safe copy: secrets replaced by ``<name>_set`` booleans."""
        d = self.to_dict()
        for path in SECRET_PATHS:
            section, name = path.split(".")
            d[section][f"{name}_set"] = bool(d[section].pop(name))
        return d

    def with_overrides(self, **overrides) -> "Settings":
        """Copy with some top-level fields replaced (e.g. ``dry_run=True`` for
        a one-off test run) without touching the stored settings."""
        clone = copy.deepcopy(self)
        for k, v in overrides.items():
            setattr(clone, k, v)
        return clone


# ---------- partial updates ----------

def _deep_update(target: dict, patch: dict):
    for k, v in patch.items():
        if k in _SECRET_NAMES and v in (None, ""):
            continue                      # empty secret = keep the stored one
        if k.endswith("_set"):
            continue                      # read-only helper flags
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _deep_update(target[k], v)
        else:
            target[k] = v


def _apply_patch(current: Settings, patch: dict) -> Settings:
    """Merge a partial change into the current settings and validate the
    result. ``setup_complete`` can only be changed by the setup wizard."""
    merged = current.to_dict()
    patch = {k: v for k, v in (patch or {}).items() if k not in ("setup_complete", "_clear_secrets")}
    _deep_update(merged, patch)
    merged["setup_complete"] = current.setup_complete
    return Settings.from_dict(merged)


def apply_patch(current: Settings, patch: dict) -> Settings:
    """Public entry point. ``_clear_secrets`` lists notification secrets to wipe
    (e.g. a webhook URL); connection API keys can only be replaced."""
    patch = dict(patch or {})
    clear = patch.pop("_clear_secrets", None) or []
    merged = _apply_patch(current, patch).to_dict()
    for path in clear:
        if path in SECRET_PATHS and path.startswith("notifications."):   # connection keys are never cleared
            section, name = path.split(".")
            merged[section][name] = ""
    merged["setup_complete"] = current.setup_complete
    return Settings.from_dict(merged)


# ---------- persistence ----------

def load_settings(store) -> Settings:
    return Settings.from_dict(store.get_json(SETTINGS_KV_KEY, {}))


def save_settings(store, settings: Settings):
    store.set_json(SETTINGS_KV_KEY, settings.to_dict())
