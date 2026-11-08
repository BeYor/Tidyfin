"""
Language detection for badges (audio, dubbing, voice-over, and subtitles).

Detection uses editable LanguageProfile settings, allowing any language to be recognised. Rules are checked from strongest to weakest:

0. Manual Jellyfin tags (highest priority; dismiss tags disable a profile).
1. Jellyfin stream metadata.
2. Video file names.
3. Subtitle files and ffprobe data.

Lower-priority rules run only if audio or subtitle information is still missing.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Optional

from .logbus import log
from .profiles import LanguageProfile, word_regex
from .settings import LanguageSettings

_SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".sub", ".vtt", ".pgs", ".sup"}
_SUBTITLE_SUBDIRS = {"subs", "subtitles", "napisy", "napisy pl", "napisy_pl", "untertitel", "sous-titres", "subtitulos"}

_SHORT_WORD = 4   # words shorter than this must match as whole words in stream titles
NO_KINDS = frozenset()
_SPECIFIC = ("voiceover", "dub")


def _kinds(value) -> frozenset:
    """Normalise a finding to a set of audio kinds ({"voiceover", "dub", "audio"})."""
    if not value:
        return NO_KINDS
    return frozenset([value]) if isinstance(value, str) else frozenset(value)


def upgrade_kind(current, new) -> frozenset:
    """
    Merge two independent findings about the audio kinds into one set. A
    voice-over and a dub found in the same release are both kept; plain
    "audio" is absorbed by a specific kind (it is most likely that very track).
    """
    merged = _kinds(current) | _kinds(new)
    if merged & set(_SPECIFIC):
        merged = merged - {"audio"}
    return merged


def badge_kind(kinds, has_subs) -> Optional[str]:
    """
    Determines which badge to show based on detected audio and subtitles.

    A single match shows its specific badge (voice-over, dub, subtitles, or audio).
    Multiple matches show the language's general "audio" badge.
    """
    kinds = _kinds(kinds)
    specific = [k for k in _SPECIFIC if k in kinds]
    parts = len(specific) or (1 if "audio" in kinds else 0)
    parts += 1 if has_subs else 0
    if parts == 0:
        return None
    if parts >= 2:
        return "audio"
    if specific:
        return specific[0]
    return "audio" if "audio" in kinds else "subs"


class Matcher:
    """Pre-compiled matching rules of one profile."""

    def __init__(self, profile: LanguageProfile):
        self.profile = profile
        self.codes = set(profile.codes)
        self.hint_re = word_regex(profile.hint_words())
        self.subfile_re = word_regex(profile.subtitle_file_words())
        self.vo_re = word_regex(profile.voiceover_words)
        self.dub_re = word_regex(profile.dub_words)
        self.subs_re = word_regex(profile.subs_words)
        self.tags = {k: set(v) for k, v in profile.tag_map().items()}

    def _title_has(self, title_lower: str, words: list, regex) -> bool:
        for w in words:
            if len(w) >= _SHORT_WORD and w in title_lower:
                return True
        return bool(regex and regex.search(title_lower))

    def classify_title(self, title: str) -> str:
        t = (title or "").lower()
        if self._title_has(t, self.profile.voiceover_words, self.vo_re):
            return "voiceover"
        if self._title_has(t, self.profile.dub_words, self.dub_re):
            return "dub"
        return "audio"

    # ----- layer 0: manual tags -----

    def from_tags(self, tags):
        """(kind, has_subs, dismissed). The dismiss tag beats everything."""
        lower = {(t or "").strip().lower() for t in (tags or [])}
        if lower & self.tags["dismiss"]:
            return NO_KINDS, False, True
        kind, subs = NO_KINDS, False
        for kind_name in ("voiceover", "dub", "audio"):
            if lower & self.tags[kind_name]:
                kind = upgrade_kind(kind, kind_name)
        if lower & self.tags["subs"]:
            subs = True
        return kind, subs, False

    # ----- layer 1 (Jellyfin) and ffprobe: stream lists -----

    def from_streams(self, streams):
        """
        Checks Jellyfin MediaStreams or ffprobe streams and returns the detected type, subtitle status, and uncertain hints.

        Confirmed: the stream has a matching language tag or identifies a specific type (voice-over or dub).

        Presumed: ambiguous hints without a language tag are flagged for manual review.
        """
        kind, has_subs, uncertain = NO_KINDS, False, []
        for s in streams:
            tags = s.get("tags", {})
            stype = s.get("Type") or {"audio": "Audio", "subtitle": "Subtitle"}.get(s.get("codec_type"), "")
            lang = (s.get("Language") or tags.get("language") or "").lower()
            title = s.get("Title") or s.get("DisplayTitle") or tags.get("title") or ""
            path = s.get("Path") or ""
            stem = Path(path).stem if path else ""
            confirmed = lang in self.codes
            hint = bool(self.hint_re and (self.hint_re.search(title) or self.hint_re.search(stem)))
            if not (confirmed or hint):
                continue
            if stype == "Audio":
                s_kind = self.classify_title(title)
                if s_kind in ("voiceover", "dub"):
                    kind = upgrade_kind(kind, s_kind)
                elif confirmed:
                    kind = upgrade_kind(kind, "audio")
                else:
                    uncertain.append(title or stem or "(untitled)")
            elif stype == "Subtitle":
                has_subs = True
        return kind, has_subs, uncertain

    # ----- layer 2: file name -----

    def from_filename(self, stem: str):
        """Signals from the video file name. Returns (kind, has_subs, uncertain)."""
        if not stem or not self.hint_re or not self.hint_re.search(stem):
            return NO_KINDS, False, []
        kind, subs = NO_KINDS, False
        if self.subs_re and self.subs_re.search(stem):
            subs = True
        if self.vo_re and self.vo_re.search(stem):
            kind = upgrade_kind(kind, "voiceover")
        if self.dub_re and self.dub_re.search(stem):
            kind = upgrade_kind(kind, "dub")
        if kind or subs:
            return kind, subs, []
        return NO_KINDS, False, [stem]      # only an ambiguous hint -> manual review


class LanguageDetector:
    def __init__(self, jf, cfg: LanguageSettings, store=None):
        self.jf = jf
        self.cfg = cfg
        self.store = store
        self.matchers = [Matcher(p) for p in cfg.active_profiles]
        self._probe_cache: dict = {}
        self._folder_cache: dict = {}

    # ---------- disk / ffprobe layer ----------

    def map_jellyfin_path_to_local(self, jf_path):
        if not jf_path:
            return None
        prefix = self.cfg.jellyfin_path_prefix
        if prefix and jf_path.startswith(prefix):
            return str(Path(self.cfg.local_path_prefix) / jf_path[len(prefix):].lstrip("/\\"))
        return jf_path

    def _subtitle_stems(self, video_local_path) -> list:
        """
        File-name stems of subtitle files next to the video (and in the usual
        subtitle sub-folders); cached per folder.
        """
        folder = str(Path(video_local_path).parent)
        if folder in self._folder_cache:
            return self._folder_cache[folder]
        stems = []
        try:
            root = Path(folder)
            if root.is_dir():
                dirs = [root] + [d for d in root.iterdir() if d.is_dir() and d.name.lower() in _SUBTITLE_SUBDIRS]
                for d in dirs:
                    stems += [f.stem for f in d.iterdir() if f.is_file() and f.suffix.lower() in _SUBTITLE_EXTENSIONS]
        except OSError:
            pass
        self._folder_cache[folder] = stems
        return stems

    def ffprobe_streams(self, video_local_path) -> list:
        """ffprobe once per file, shared by every profile."""
        key = str(video_local_path)
        if key in self._probe_cache:
            return self._probe_cache[key]
        streams = []
        try:
            result = subprocess.run(
                [self.cfg.ffprobe_bin, "-v", "error", "-show_entries", "stream=index,codec_type:stream_tags=language,title",
                 "-of", "json", key], capture_output=True, text=True, timeout=self.cfg.ffprobe_timeout)
            if result.returncode == 0:
                streams = json.loads(result.stdout).get("streams", [])
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            streams = []
        self._probe_cache[key] = streams
        return streams

    def _deep_scan(self, m: Matcher, jf_path):
        if not self.cfg.deep_scan:
            return NO_KINDS, False, []
        local = self.map_jellyfin_path_to_local(jf_path)
        if not local:
            return NO_KINDS, False, []
        has_subs = bool(m.subfile_re) and any(m.subfile_re.search(s) for s in self._subtitle_stems(local))
        kind, subs, unc = m.from_streams(self.ffprobe_streams(local))
        return kind, has_subs or subs, unc

    @staticmethod
    def _complete(kind, has_subs) -> bool:
        """
        Nothing more to learn: subtitles, a voice-over and a dub are all
        found, so the weaker layers are skipped.
        """
        return bool(has_subs and set(_SPECIFIC) <= set(kind))

    def _file_layers(self, m: Matcher, kind, has_subs, uncertain, jf_path):
        """Layers 2 and 3, shared by movies and single episodes."""
        if jf_path and not (self._complete(kind, has_subs)):
            f_kind, f_subs, f_unc = m.from_filename(Path(jf_path).stem)
            kind, has_subs, uncertain = upgrade_kind(kind, f_kind), has_subs or f_subs, uncertain + f_unc
        if self.cfg.deep_scan and jf_path and not self._complete(kind, has_subs):
            d_kind, d_subs, d_unc = self._deep_scan(m, jf_path)
            kind, has_subs, uncertain = upgrade_kind(kind, d_kind), has_subs or d_subs, uncertain + d_unc
        return kind, has_subs, uncertain

    # ---------- per item ----------

    def _movie(self, m: Matcher, item):
        kind, has_subs, dismissed = m.from_tags(item.get("Tags"))
        if dismissed:
            return NO_KINDS, False, []
        if kind or has_subs:                 # manual tags settle it (the same as for a series)
            return kind, has_subs, []
        s_kind, s_subs, uncertain = m.from_streams(item.get("MediaStreams", []))
        return self._file_layers(m, upgrade_kind(kind, s_kind), has_subs or s_subs, list(uncertain), item.get("Path"))

    def _series(self, m: Matcher, series_item, episodes_loader):
        """
        Tags on the series settle it at once; otherwise up to ``max_episodes``
        episodes are inspected, stopping early once audio and subtitles are found.
        """
        kind, has_subs, dismissed = m.from_tags(series_item.get("Tags"))
        if dismissed:
            return NO_KINDS, False, []
        if kind or has_subs:
            return kind, has_subs, []
        uncertain = []
        for ep in episodes_loader()[: self.cfg.max_episodes]:
            e_kind, e_subs, e_unc = m.from_streams(ep.get("MediaStreams", []))
            e_kind, e_subs, e_unc = self._file_layers(m, e_kind, e_subs, list(e_unc), ep.get("Path"))
            kind, has_subs, uncertain = upgrade_kind(kind, e_kind), has_subs or e_subs, uncertain + e_unc
            if self._complete(kind, has_subs):
                break
        return kind, has_subs, uncertain

    def analyze_item(self, item, media_type):
        """Per-profile findings for one item: list of (matcher, kind, subs, uncertain)."""
        out = []
        cache = {}

        def episodes():
            if "eps" not in cache:
                cache["eps"] = self.jf.series_episodes(item["Id"])
            return cache["eps"]

        for m in self.matchers:
            if media_type == "series":
                kind, subs, unc = self._series(m, item, episodes)
            else:
                kind, subs, unc = self._movie(m, item)
            out.append((m, kind, subs, unc))
        return out

    def explain_item(self, item, media_type) -> list:
        """
        Shows the admin how Tidyfin detects languages for an item.

        For each enabled profile, displays the language code, name, audio, subtitles, badge, text, dismissed status, uncertain hints, and detection sources.

        Sources indicate where matches were found: tags, streams, filename, files (subtitles / ffprobe), or episodes.
        """
        return [self._explain_row(m, kind, subs, unc, item, media_type) for m, kind, subs, unc in self.analyze_item(item, media_type)]

    @staticmethod
    def _explain_row(m, kind, subs, unc, item, media_type) -> dict:
        tag_kind, tag_subs, dismissed = m.from_tags(item.get("Tags"))
        sources = []
        if not dismissed:
            if tag_kind or tag_subs:
                sources = ["tags"]
            elif media_type == "series":
                sources = ["episodes"] if (kind or subs) else []
            else:
                s_kind, s_subs, _ = m.from_streams(item.get("MediaStreams", []))
                f_kind, f_subs, _ = m.from_filename(Path(item["Path"]).stem) if item.get("Path") else (NO_KINDS, False, [])
                if s_kind or s_subs:
                    sources.append("streams")
                if f_kind or f_subs:
                    sources.append("filename")
                if (kind or subs) and (set(kind) - set(s_kind) - set(f_kind) or (subs and not (s_subs or f_subs))):
                    sources.append("files")
        badge = None if dismissed else badge_kind(kind, subs)
        return {"code": m.profile.code, "name": m.profile.name, "audio": [k for k in ("voiceover", "dub", "audio") if k in kind],
                "subs": bool(subs and not dismissed), "badge": badge, "text": m.profile.badges[badge].text if badge else "",
                "dismissed": dismissed, "uncertain": sorted(set(unc)) if not (kind or subs) else [], "sources": sources}

    # ---------- whole library ----------

    def compute(self, extra_known_ids=None):
        """
        Returns (result, uncertain).

        result    = {key: {jellyfin_id, media_type, labels: [{profile, kind}]}} for
                    items with at least one language badge,
        uncertain = [{title, hints, profile, jellyfin_id, media_type}] items with
                    only ambiguous signals (they need a manual Jellyfin tag).
        """
        if not self.matchers:
            return {}, []

        movie_index, series_index = self.jf.full_library_index()
        id_cache = self.store.load_library_id_cache() if self.store else {}
        before = json.dumps(id_cache, sort_keys=True)
        result, uncertain_log, seen, detail = {}, [], set(), {}

        def record(key, item, media_type):
            labels, rows = [], []
            for m, kind, subs, unc in self.analyze_item(item, media_type):
                b = badge_kind(kind, subs)
                row = self._explain_row(m, kind, subs, unc, item, media_type)
                if row["badge"] or row["dismissed"] or row["uncertain"]:
                    rows.append(row)
                if unc and not kind and not subs:
                    uncertain_log.append({"key": key, "title": item.get("Name", "?"), "hints": sorted(set(unc)),
                                          "profile": m.profile.code, "jellyfin_id": item.get("Id"), "media_type": media_type})
                if b:
                    labels.append({"profile": m.profile.code, "kind": b})
            if labels:
                result[key] = {"jellyfin_id": item["Id"], "media_type": media_type, "labels": labels}
            if rows:
                detail[key] = rows

        for tmdb_id, item in movie_index.items():
            key = f"movie_{tmdb_id}"
            seen.add(key)
            id_cache[key] = item["Id"]
            record(key, item, "movie")
        for tvdb_id, item in series_index.items():
            key = f"series_{tvdb_id}"
            seen.add(key)
            id_cache[key] = item["Id"]
            record(key, item, "series")

        # Fallback ids: queued items plus the persistent cache from earlier
        # cycles. The bulk list from Jellyfin can be incomplete (e.g. mid-scan).
        fallback = dict(id_cache)
        fallback.update(extra_known_ids or {})
        pulled = 0
        for key, jellyfin_id in fallback.items():
            if key in seen or not jellyfin_id:
                continue
            item = self.jf.fetch_item(jellyfin_id)
            if not item:
                id_cache.pop(key, None)      # really gone
                continue
            pulled += 1
            media_type = "movie" if key.startswith("movie_") else "series"
            record(key, item, media_type)
            if key in result:
                result[key]["jellyfin_id"] = jellyfin_id
        if pulled:
            log.info("[Language] %d items fetched directly (missing from the bulk listing)", pulled)

        if self.store and json.dumps(id_cache, sort_keys=True) != before:
            self.store.save_library_id_cache(id_cache)
        if self.store:
            # What was found per title, for the dashboard: the title dialog shows exactly this,
            # the same result the badges were drawn from (not a second, possibly different, detection).
            self.store.set_json("language_detail", detail)

        if uncertain_log:
            log.info("[Language] %d items have only ambiguous signals - add a manual tag in Jellyfin", len(uncertain_log))
        log.info("[Language] Items with a language badge: %d", len(result))
        return result, uncertain_log
