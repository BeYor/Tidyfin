"""
Library cleanup: automatic (based on age and watch status) or manual (using stars and dashboard scheduling).

Rules:
- Service outages never empty queues or trigger deletions. Items that cannot be checked are left untouched.
- Automatic cleanup only runs when disk usage reaches cleanup_start_percent. Until then, queues remain unchanged.
- Protection is managed through Radarr/Sonarr tags, the single source of truth.
- All cleanup events are recorded in history and engine.events for notifications.
- Disk-space reduction settings are configured separately for each media type.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import requests

from .errors import UpstreamError
from .logbus import log
from .settings import Settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def midnight_utc(dt: datetime) -> datetime:
    """Round DOWN to midnight of that day (rounding up would add a day to the configured grace period)."""
    return datetime.combine(dt.date(), datetime.min.time(), tzinfo=timezone.utc)


def countdown_date(delete_at: datetime) -> datetime:
    """The last day the item is still available: the day before deletion."""
    return delete_at - timedelta(days=1)


@dataclass
class EvalResult:
    candidates: list = field(default_factory=list)
    failed_types: set = field(default_factory=set)   # "movie"/"series": not checked (outage or paused)
    unknown_keys: set = field(default_factory=set)   # single items that could not be evaluated now
    paused: dict = field(default_factory=dict)       # "movie"/"series" -> why cleanup is waiting
    saved: dict = field(default_factory=dict)        # key -> why a title is NOT a candidate (watched, favorited, in_progress, protected)


class CleanupEngine:
    def __init__(self, settings: Settings, store, jf, radarr, sonarr, now: Callable[[], datetime] = _utcnow):
        self.s = settings
        self.store = store
        self.jf = jf
        self.radarr = radarr
        self.sonarr = sonarr
        self.now = now
        self.stats = {"queued": 0, "dequeued": 0, "deleted": 0, "would_delete": 0, "delete_failed": 0, "accelerated": 0}
        self.events: list = []          # [{type, title, media_type, size_bytes, source}] for notifications
        self.paused: dict = {}          # media types whose cleanup is waiting for the disk to fill up
        self._disk_cache: dict = {}
        self._ignore_gate = False
        self._activity: Optional[dict] = None     # {jellyfin_id: reason}, loaded once per cycle
        self._activity_loaded = False
        self._activity: Optional[dict] = None     # lazily loaded per cycle, see _activity_protection

    # ---------- disk ----------

    def _disk_path(self, media_type: str) -> str:
        specific = self.s.disk_path_movies if media_type == "movie" else self.s.disk_path_series
        return specific or self.s.disk_path

    def disk_usage_percent(self, media_type: str = "") -> Optional[float]:
        path_str = self._disk_path(media_type) if media_type else self.s.disk_path
        if path_str in self._disk_cache:
            return self._disk_cache[path_str]
        usage = None
        path = Path(path_str)
        if path.exists():
            try:
                total, used, _free = shutil.disk_usage(path)
                usage = (used / total) * 100
            except OSError:
                usage = None
        self._disk_cache[path_str] = usage
        return usage

    def effective_cleanup_delay(self, base_days: int, media_type: str) -> int:
        """Shorten the grace period when the disk holding this media type is full. The most restrictive matching threshold wins; never lengthens."""
        if not self.s.disk_thresholds:
            return base_days
        usage = self.disk_usage_percent(media_type)
        if usage is None:
            return base_days
        best = base_days
        for t in self.s.disk_thresholds:
            if usage >= t.usage_percent:
                best = min(best, t.movies_days if media_type == "movie" else t.series_days)
        return best

    def cleanup_gate(self, media_type: str) -> Optional[dict]:
        """
        Returns None when automatic cleanup is allowed. Otherwise, explains why it is waiting:

        - below_threshold: disk usage is below the required level.
        - unknown_usage: disk usage cannot be determined.
        """
        start = self.s.cleanup_start_percent
        if start <= 0:
            return None
        usage = self.disk_usage_percent(media_type)
        if usage is None:
            return {"reason": "unknown_usage", "usage": None, "start": start}
        if usage < start:
            return {"reason": "below_threshold", "usage": round(usage, 1), "start": start}
        return None

    def _gated(self, media_type: str, res: "EvalResult") -> bool:
        """True (and the type is marked as skipped) while cleanup must wait."""
        if self._ignore_gate:
            return False
        why = self.cleanup_gate(media_type)
        if not why:
            return False
        res.paused[media_type] = why
        res.failed_types.add(media_type)      # an untouched queue: nothing queued, dequeued or deleted
        log.info("[Auto] Cleanup of %s is waiting: %s", "movies" if media_type == "movie" else "series",
                 "the disk usage cannot be read" if why["reason"] == "unknown_usage"
                 else f"disk usage {why['usage']}% is below the {why['start']:g}% start threshold")
        return True

    # ---------- candidate evaluation ----------

    def evaluate_automatic_candidates(self, ignore_gate: bool = False) -> EvalResult:
        self._ignore_gate = ignore_gate
        res = EvalResult()
        now = self.now()
        self._evaluate_movies(res, now)
        self._evaluate_series(res, now)
        return res

    # ---------- time-limited protection from other people's activity ----------

    def _load_activity(self) -> Optional[dict]:
        """
        Returns protected titles as {jellyfin_id: reason}, based on other users' stars or "Continue watching".

        Returns None if the data cannot be read. In that case, nothing is marked as protected.

        Star dates are tracked separately because Jellyfin does not store them.
        """
        s = self.s
        favs_on, resume_on = s.favorites_protect_days > 0, s.resume_protect_days > 0
        if not (favs_on or resume_on):
            return {}
        users = self.jf.users()
        if users is None:
            return None
        now, protected = self.now(), {}
        if favs_on:
            seen = self.store.get_json("favorite_first_seen", {}) or {}
            fresh, window = {}, timedelta(days=s.favorites_protect_days)
            for user in users:
                if user["Id"] == s.manual_user_id:
                    continue                     # those stars are deletion marks, not protection
                items = self.jf.favorites_of(user["Id"])
                if items is None:
                    return None
                for item in items:
                    tag = f"{user['Id']}:{item['Id']}"
                    first = _parse_dt(seen[tag]) if tag in seen else now
                    fresh[tag] = first.isoformat()
                    if now - first < window:
                        protected.setdefault(item["Id"], "favorited")
            self.store.set_json("favorite_first_seen", fresh)
        if resume_on:
            window = timedelta(days=s.resume_protect_days)
            for user in users:
                rows = self.jf.resume_items(user["Id"])
                if rows is None:
                    return None
                for row in rows:
                    played = row["last_played"]
                    if played is None or now - played < window:
                        protected.setdefault(row["item_id"], "in_progress")
        return protected

    def _activity_protection(self, jellyfin_id) -> tuple:
        """(reason | None, readable). readable=False: the activity could not be read, so the title must not be judged in this cycle."""
        if not jellyfin_id:
            return None, True
        if not self._activity_loaded:
            self._activity, self._activity_loaded = self._load_activity(), True
        if self._activity is None:
            return None, False
        return self._activity.get(jellyfin_id), True

    def _jellyfin_state(self, key, tmdb_id, tvdb_id, media_type, res: EvalResult):
        """(jellyfin_id | None, lookup_ok, last_played, usable). usable=False means we cannot tell whether anyone watched it, so the item is not judged."""
        jf_item, lookup_ok = self.jf.resolve_item(tmdb_id, tvdb_id, media_type)
        if not lookup_ok:
            res.unknown_keys.add(key)
            return None, False, None, False
        jellyfin_id = jf_item["Id"] if jf_item else None
        last_played = None
        if jellyfin_id:
            last_played, ok = self.jf.last_played_across_users(jellyfin_id, media_type)
            if not ok:
                res.unknown_keys.add(key)
                return jellyfin_id, True, None, False
        return jellyfin_id, True, last_played, True

    def _spare_by_activity(self, key, jellyfin_id, res: EvalResult) -> bool:
        """ Returns False if a title cannot be considered for cleanup because of other users' stars, "Continue watching", or unavailable data. """
        reason, readable = self._activity_protection(jellyfin_id)
        if not readable:
            res.unknown_keys.add(key)
            return False
        if reason:
            res.saved[key] = reason
            return False
        return True

    def _evaluate_movies(self, res, now):
        if not self.radarr.configured:
            res.failed_types.add("movie")
            return
        if self._gated("movie", res):
            return
        try:
            tag_labels, movies = self.radarr.tags(), self.radarr.items()
        except UpstreamError as e:
            log.warning("[Auto] Radarr unavailable (%s) - skipping movies this cycle", e)
            res.failed_types.add("movie")
            return
        rules, protected_tags = self.s.movies, self.s.protected_tags
        for movie in movies:
            tmdb_id = movie.get("tmdbId")
            if not tmdb_id:
                continue
            key = f"movie_{tmdb_id}"
            if {tag_labels.get(t, "").lower() for t in movie.get("tags", [])} & protected_tags:
                res.saved[key] = "protected"
                continue
            if not movie.get("hasFile"):
                continue
            # Age counts from when the FILE appeared, not from when the movie
            # was added to monitoring; fall back to "added" if there is no file date.
            file_date = (movie.get("movieFile") or {}).get("dateAdded") or movie["added"]
            if (now - _parse_dt(file_date)).days < rules.content_age_threshold_days:
                continue
            jellyfin_id, lookup_ok, last_played, usable = self._jellyfin_state(key, tmdb_id, None, "movie", res)
            if not usable:
                continue
            if last_played and (now - last_played).days < rules.last_stream_threshold_days:
                res.saved[key] = "watched"
                continue
            if not self._spare_by_activity(key, jellyfin_id, res):
                continue
            res.candidates.append({
                "key": key, "title": movie["title"], "media_type": "movie", "tmdb_id": tmdb_id, "tvdb_id": None,
                "arr_id": movie["id"], "jellyfin_id": jellyfin_id, "jellyfin_lookup_ok": lookup_ok,
                "cleanup_delay_days": rules.cleanup_delay_days,
                "size_bytes": movie.get("sizeOnDisk") or (movie.get("movieFile") or {}).get("size") or 0})

    def _evaluate_series(self, res, now):
        if not self.sonarr.configured:
            res.failed_types.add("series")
            return
        if self._gated("series", res):
            return
        try:
            tag_labels, all_series = self.sonarr.tags(), self.sonarr.items()
        except UpstreamError as e:
            log.warning("[Auto] Sonarr unavailable (%s) - skipping series this cycle", e)
            res.failed_types.add("series")
            return
        rules, protected_tags = self.s.series, self.s.protected_tags
        for series in all_series:
            tvdb_id = series.get("tvdbId")
            if not tvdb_id:
                continue
            key = f"series_{tvdb_id}"
            if {tag_labels.get(t, "").lower() for t in series.get("tags", [])} & protected_tags:
                res.saved[key] = "protected"
                continue
            stats = series.get("statistics", {})
            if not stats.get("episodeFileCount"):
                continue
            # Age of the OLDEST episode file on disk (same idea as for movies).
            try:
                episode_files = self.sonarr.episode_files(series["id"])
            except UpstreamError as e:
                log.warning("[Auto] Could not fetch episode files of '%s' (%s) - skipping", series.get("title"), e)
                res.unknown_keys.add(key)
                continue
            dates = [ef["dateAdded"] for ef in episode_files if ef.get("dateAdded")]
            oldest = min(dates) if dates else series["added"]
            if (now - _parse_dt(oldest)).days < rules.content_age_threshold_days:
                continue
            jellyfin_id, lookup_ok, last_played, usable = self._jellyfin_state(key, None, tvdb_id, "series", res)
            if not usable:
                continue
            if last_played and (now - last_played).days < rules.last_stream_threshold_days:
                res.saved[key] = "watched"
                continue
            if not self._spare_by_activity(key, jellyfin_id, res):
                continue
            res.candidates.append({
                "key": key, "title": series["title"], "media_type": "series", "tmdb_id": None, "tvdb_id": tvdb_id,
                "arr_id": series["id"], "jellyfin_id": jellyfin_id, "jellyfin_lookup_ok": lookup_ok,
                "cleanup_delay_days": rules.cleanup_delay_days, "size_bytes": stats.get("sizeOnDisk") or 0})

    # ---------- automatic cleanup ----------

    def _delete_item(self, media_type: str, arr_id) -> bool:
        return (self.radarr if media_type == "movie" else self.sonarr).delete(arr_id)

    def _record(self, kind, title, media_type, source, size=0):
        self.events.append({"type": kind, "title": title, "media_type": media_type, "source": source, "size_bytes": size})

    def process_automatic_cleanup(self) -> dict:
        """Returns {key: {jellyfin_id, media_type, delete_at}} for every item under cleanup (used for badges, collections and poster backups)."""
        state = self.store.load_auto_state()
        res = self.evaluate_automatic_candidates()
        self.paused = res.paused
        candidate_keys = {c["key"] for c in res.candidates}
        now = self.now()
        dry_run = self.s.dry_run
        log.info("[Auto] Items meeting the thresholds: %d", len(res.candidates))

        result_map = {}
        for c in res.candidates:
            key = c["key"]
            if key not in state:
                delay = self.effective_cleanup_delay(c["cleanup_delay_days"], c["media_type"])
                state[key] = {
                    "title": c["title"], "media_type": c["media_type"], "tmdb_id": c["tmdb_id"], "tvdb_id": c["tvdb_id"],
                    "arr_id": c["arr_id"], "jellyfin_id": None, "first_detected": now.isoformat(),
                    "delete_at": midnight_utc(now + timedelta(days=delay)).isoformat(), "size_bytes": c.get("size_bytes", 0)}
                self.stats["queued"] += 1
                self._record("queued", c["title"], c["media_type"], "auto", c.get("size_bytes", 0))
                self.store.add_history("queued", key, c["title"], c["media_type"], "auto",
                                       {"delete_at": state[key]["delete_at"], "size_bytes": c.get("size_bytes", 0)})
            info = state[key]
            info["size_bytes"] = c.get("size_bytes", info.get("size_bytes", 0))
            info["arr_id"] = c["arr_id"]
            self._maybe_shorten(key, info, c)

            if c.get("jellyfin_lookup_ok", True):
                jellyfin_id = c["jellyfin_id"]
                if jellyfin_id:
                    info["jellyfin_id"] = jellyfin_id
                else:
                    # The Jellyfin list can be briefly incomplete - use the remembered id.
                    stored = info.get("jellyfin_id")
                    if stored and self.jf.item_exists(stored):
                        jellyfin_id = stored
                        log.warning("'%s' is missing from the Jellyfin list this cycle, but the stored id still exists - using it", c["title"])
                    else:
                        log.warning("'%s' not found in Jellyfin (tmdb=%s, tvdb=%s) - no badge or collection this cycle",
                                    c["title"], c["tmdb_id"], c["tvdb_id"])
            else:
                jellyfin_id = info.get("jellyfin_id")

            delete_at = _parse_dt(info["delete_at"])
            log.info("  -> %s: deletion on %s", c["title"], delete_at.strftime("%Y-%m-%d"))
            if jellyfin_id:
                result_map[key] = {"jellyfin_id": jellyfin_id, "media_type": c["media_type"], "delete_at": info["delete_at"]}

            if now >= delete_at:
                if dry_run:
                    self.stats["would_delete"] += 1
                    self._record("would_delete", c["title"], c["media_type"], "auto", info.get("size_bytes", 0))
                    log.info("  -> [DRY RUN] '%s' is due, would delete the files (dry run is on)", c["title"])
                    continue
                log.info("  -> '%s' is due, deleting the files", c["title"])
                if self._delete_item(c["media_type"], c["arr_id"]):
                    log.info("     [OK] deleted")
                    self.stats["deleted"] += 1
                    self._record("deleted", c["title"], c["media_type"], "auto", info.get("size_bytes", 0))
                    self.store.add_history("deleted", key, c["title"], c["media_type"], "auto", {"size_bytes": info.get("size_bytes", 0)})
                    del state[key]
                    result_map.pop(key, None)
                else:
                    self.stats["delete_failed"] += 1
                    self._record("delete_failed", c["title"], c["media_type"], "auto")
                    self.store.add_history("delete_failed", key, c["title"], c["media_type"], "auto")
                    log.warning("     deletion failed, will retry next cycle")

        # Entries we could NOT evaluate stay queued; keep their badge and collection stable.
        for key, info in state.items():
            if key in candidate_keys or key in result_map:
                continue
            media_type = info.get("media_type", "")
            if (key in res.unknown_keys or media_type in res.failed_types) and info.get("jellyfin_id"):
                result_map[key] = {"jellyfin_id": info["jellyfin_id"], "media_type": media_type, "delete_at": info["delete_at"]}

        # Dequeue entries that were evaluated and no longer meet the thresholds.
        for key in list(state.keys()):
            media_type = state[key].get("media_type", "")
            if key in candidate_keys or key in res.unknown_keys or media_type in res.failed_types:
                continue
            log.info("  -> '%s' no longer meets the thresholds, removing from the queue", state[key]["title"])
            self.stats["dequeued"] += 1
            why = res.saved.get(key, "no_longer_qualifies")
            self.store.add_history("dequeued", key, state[key]["title"], media_type, "auto",
                                   {"reason": why, "size_bytes": state[key].get("size_bytes", 0)})
            del state[key]

        self.store.save_auto_state(state)
        return result_map

    def _maybe_shorten(self, key, info, candidate):
        """Optionally pull an existing deadline closer when the disk got full
        after the item was queued. Never moves a deadline later."""
        if not self.s.shorten_existing_queue or not info.get("first_detected") or info.get("postponed"):
            return
        delay = self.effective_cleanup_delay(candidate["cleanup_delay_days"], candidate["media_type"])
        new_end = midnight_utc(_parse_dt(info["first_detected"]) + timedelta(days=delay))
        if new_end < _parse_dt(info["delete_at"]):
            info["delete_at"] = new_end.isoformat()
            if not info.get("accelerated"):
                info["accelerated"] = True
                self.stats["accelerated"] += 1
                self.store.add_history("accelerated", key, info["title"], info["media_type"], "auto",
                                       {"delete_at": info["delete_at"], "reason": "disk_usage"})

    # ---------- manual deletion (stars and dashboard) ----------

    def expire_keeps(self):
        """
        Removes protection tags when their scheduled end date is reached.

        The tag remains the single source of truth; this only tracks when to remove it.
        Entries are cleared when the title is deleted or protection is removed manually.
        """
        keeps = self.store.get_json("keep_until", {}) or {}
        if not keeps:
            return
        now, changed = self.now(), False
        tag = self.s.protect_tag.lower()
        for key, iso in list(keeps.items()):
            row = self.store.get_library_item(key)
            if row is None or not row.get("protected"):
                del keeps[key]
                changed = True
                continue
            if now < _parse_dt(iso):
                continue
            client = self.radarr if row["media_type"] == "movie" else self.sonarr
            if not client.configured or not row.get("arr_id"):
                continue
            try:
                ids = [tid for tid, label in client.tags().items() if label.lower() == tag]
                if ids:
                    client.edit_tags(row["arr_id"], ids, "remove")
            except (UpstreamError, requests.RequestException) as e:
                log.warning("[Keep] could not end the protection of '%s': %s", row["title"], e)
                continue
            left = [t for t in row["tags"] if t.lower() != tag]
            self.store.update_library_item(key, tags=left, protected=bool({t.lower() for t in left} & set(self.s.protected_tags)))
            self.store.add_history("unprotected", key, row["title"], row["media_type"], "expired", {"until": iso})
            log.info("[Keep] the protection of '%s' ended (kept until %s)", row["title"], iso[:10])
            del keeps[key]
            changed = True
        if changed:
            self.store.set_json("keep_until", keeps)

    def drop_vanished_auto(self, refreshed: dict) -> int:
        """
        Removes automatic queue entries for titles that no longer exist in Radarr/Sonarr or have no files left.

        This runs regardless of cleanup thresholds or the start gate.
        Successfully refreshed library snapshots determine which entries can be removed.
        If a media type could not be checked, its entries are kept.
        """
        state = self.store.load_auto_state()
        gone = []
        for media_type in ("movie", "series"):
            if refreshed.get(media_type) is None:
                continue
            present = self.store.library_keys(media_type)
            gone += [k for k in state if k.startswith(f"{media_type}_") and k not in present]
        for key in gone:
            info = state.pop(key)
            media_type = "movie" if key.startswith("movie_") else "series"
            log.info("[Auto] '%s' is no longer in %s - removed from the queue", info.get("title", key), "Radarr" if media_type == "movie" else "Sonarr")
            self.store.add_history("dequeued", key, info.get("title", ""), media_type, "auto",
                                   {"reason": "removed_elsewhere", "size_bytes": info.get("size_bytes", 0)})
        if gone:
            self.store.save_auto_state(state)
        return len(gone)

    def drop_vanished_manual(self, state: dict):
        """
        Removes manual queue entries for titles deleted from Radarr/Sonarr by someone else.

        Only runs when Radarr/Sonarr are available. Service outages never clear the queue.
        """
        for key, info in list(state.items()):
            client = self.radarr if info.get("media_type") == "movie" else self.sonarr
            ext = info.get("tmdb_id") if info.get("media_type") == "movie" else info.get("tvdb_id")
            if not client.configured or not ext:
                continue
            try:
                found = client.find_by_external_id(ext)
            except UpstreamError:
                continue
            if found is not None:
                continue
            log.info("[Manual] '%s' is no longer in %s - removed from the queue", info.get("title", key), client.kind.capitalize())
            self.store.add_history("dequeued", key, info.get("title", ""), info.get("media_type", ""), "manual",
                                   {"reason": "removed_elsewhere", "size_bytes": info.get("size_bytes", 0)})
            del state[key]

    def process_manual_new_favorites(self, state: dict):
        user_id = self.s.manual_user_id
        if not user_id:
            return
        for item in self.jf.favorites(user_id):
            ids = item.get("ProviderIds", {})
            tmdb_id, tvdb_id = ids.get("Tmdb"), ids.get("Tvdb")
            media_type = "movie" if item.get("Type") == "Movie" else "series"
            key = f"movie_{tmdb_id}" if media_type == "movie" else f"series_{tvdb_id}"
            if key in state:
                continue
            if (media_type == "movie" and not tmdb_id) or (media_type == "series" and not tvdb_id):
                continue
            marked_at = self.now()
            deadline = midnight_utc(marked_at + timedelta(days=self.s.grace_days))
            state[key] = {"title": item.get("Name", "Unknown"), "media_type": media_type, "tmdb_id": tmdb_id,
                          "tvdb_id": tvdb_id, "jellyfin_id": item["Id"], "marked_at": marked_at.isoformat(),
                          "delete_at": deadline.isoformat(), "origin": "star"}
            log.info("[Manual] New star: %s -> deletion on %s", item.get("Name"), deadline.strftime("%Y-%m-%d"))
            self.store.add_history("marked", key, item.get("Name", ""), media_type, "star", {"delete_at": deadline.isoformat()})
            self._record("queued", item.get("Name", ""), media_type, "star")
            if self.jf.unfavorite(user_id, item["Id"]):
                log.info("     [OK] star removed")

    def _protected_now(self, client, found, tag_cache: dict) -> bool:
        if client.kind not in tag_cache:
            tag_cache[client.kind] = client.tags()
        labels = {tag_cache[client.kind].get(t, "").lower() for t in found.get("tags", [])}
        return bool(labels & self.s.protected_tags)

    def process_manual_due_items(self, state: dict):
        now = self.now()
        to_remove, tag_cache = [], {}
        for key, info in state.items():
            if now < _parse_dt(info["delete_at"]):
                continue
            if self.s.dry_run:
                self.stats["would_delete"] += 1
                self._record("would_delete", info["title"], info["media_type"], "manual")
                log.info("[Manual] [DRY RUN] '%s' is due, would delete the files (dry run is on)", info["title"])
                continue
            success = False
            try:
                client = self.radarr if info["media_type"] == "movie" else self.sonarr
                found = client.find_by_external_id(info["tmdb_id"] if info["media_type"] == "movie" else info["tvdb_id"])
                if found and self._protected_now(client, found, tag_cache):
                    # Someone protected it after it was scheduled: honor the tag.
                    log.info("[Manual] '%s' carries a protection tag - dropped from the queue", info["title"])
                    self.store.add_history("dequeued", key, info["title"], info["media_type"], "manual", {"reason": "protected"})
                    to_remove.append(key)
                    continue
                log.info("[Manual] '%s' is due, deleting the files", info["title"])
                success = bool(found) and client.delete(found["id"])
            except UpstreamError as e:
                log.warning("     Radarr/Sonarr unavailable (%s)", e)
            if success:
                log.info("     [OK] deleted")
                self.stats["deleted"] += 1
                self._record("deleted", info["title"], info["media_type"], "manual", info.get("size_bytes", 0))
                self.store.add_history("deleted", key, info["title"], info["media_type"], "manual", {"size_bytes": info.get("size_bytes", 0)})
                to_remove.append(key)
            else:
                self.stats["delete_failed"] += 1
                self._record("delete_failed", info["title"], info["media_type"], "manual")
                self.store.add_history("delete_failed", key, info["title"], info["media_type"], "manual")
                log.warning("     deletion failed, will retry next cycle")
        for key in to_remove:
            del state[key]

    # ---------- preview (changes nothing) ----------

    def preview(self) -> dict:
        """
        Shows which titles would enter the queue based on current thresholds.

        The disk-usage start gate is ignored when evaluating candidates but reported separately as "paused" so the dashboard can explain why cleanup is not running.
        """
        res = self.evaluate_automatic_candidates(ignore_gate=True)
        waiting = {t: why for t in ("movie", "series") if (why := self.cleanup_gate(t))}
        state = self.store.load_auto_state()
        now = self.now()
        items = []
        for c in res.candidates:
            existing = state.get(c["key"])
            if existing:
                delete_at = _parse_dt(existing["delete_at"])
            else:
                delete_at = midnight_utc(now + timedelta(days=self.effective_cleanup_delay(c["cleanup_delay_days"], c["media_type"])))
            items.append({"key": c["key"], "title": c["title"], "media_type": c["media_type"],
                          "size_bytes": c.get("size_bytes", 0), "in_queue": bool(existing), "delete_at": delete_at.isoformat()})
        return {"items": items, "total_size_bytes": sum(i["size_bytes"] for i in items),
                "unavailable": sorted(res.failed_types), "paused": waiting}


def build_queue_view(store, now: Optional[datetime] = None) -> list:
    """Both queues in one list for the dashboard."""
    now = now or _utcnow()
    out = []
    for source, state in (("auto", store.load_auto_state()), ("manual", store.load_manual_state())):
        for key, info in state.items():
            delete_at = _parse_dt(info["delete_at"])
            out.append({
                "key": key, "title": info.get("title", ""), "media_type": info.get("media_type", ""),
                "source": source, "origin": info.get("origin", "auto" if source == "auto" else "star"),
                "jellyfin_id": info.get("jellyfin_id"), "size_bytes": info.get("size_bytes", 0),
                "queued_at": info.get("first_detected") or info.get("marked_at"),
                "delete_at": info["delete_at"], "days_left": max((delete_at - now).days, 0),
                "due": now >= delete_at, "accelerated": bool(info.get("accelerated"))})
    out.sort(key=lambda i: i["delete_at"])
    return out
