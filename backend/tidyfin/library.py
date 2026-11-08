"""
Library snapshot: stores all movies and series with files, including their tags and Jellyfin IDs.

Updated each cycle to keep the dashboard fast without querying Radarr, Sonarr, and Jellyfin on every page load.
"""
from __future__ import annotations

from .errors import UpstreamError
from .logbus import log


def _movie_rows(radarr, jf, settings, id_cache):
    tag_labels = radarr.tags()
    rows = []
    for m in radarr.items():
        tmdb_id = m.get("tmdbId")
        if not tmdb_id or not m.get("hasFile"):
            continue
        key = f"movie_{tmdb_id}"
        labels = [tag_labels[t] for t in m.get("tags", []) if t in tag_labels]
        item, ok = jf.resolve_item(tmdb_id, None, "movie")
        movie_file = m.get("movieFile") or {}
        rows.append({
            "key": key, "media_type": "movie", "title": m["title"], "year": m.get("year"),
            "size_bytes": m.get("sizeOnDisk") or movie_file.get("size") or 0,
            "added_at": movie_file.get("dateAdded") or m.get("added"), "arr_id": m["id"],
            "tmdb_id": tmdb_id, "tvdb_id": None,
            "jellyfin_id": item["Id"] if item else (id_cache.get(key) if not ok else None),
            "tags": labels, "protected": bool({l.lower() for l in labels} & settings.protected_tags)})
    return rows


def _series_rows(sonarr, jf, settings, id_cache):
    tag_labels = sonarr.tags()
    rows = []
    for s in sonarr.items():
        tvdb_id = s.get("tvdbId")
        stats = s.get("statistics", {})
        if not tvdb_id or not stats.get("episodeFileCount"):
            continue
        key = f"series_{tvdb_id}"
        labels = [tag_labels[t] for t in s.get("tags", []) if t in tag_labels]
        item, ok = jf.resolve_item(None, tvdb_id, "series")
        rows.append({
            "key": key, "media_type": "series", "title": s["title"], "year": s.get("year"),
            "size_bytes": stats.get("sizeOnDisk") or 0, "added_at": s.get("added"), "arr_id": s["id"],
            "tmdb_id": None, "tvdb_id": tvdb_id,
            "jellyfin_id": item["Id"] if item else (id_cache.get(key) if not ok else None),
            "tags": labels, "protected": bool({l.lower() for l in labels} & settings.protected_tags)})
    return rows


def refresh_library(settings, store, jf, radarr, sonarr) -> dict:
    """
    Rebuild the snapshot. A type whose upstream is unreachable keeps its old
    rows instead of being wiped. Returns {"movie": n | None, "series": n | None}.
    """
    id_cache = store.load_library_id_cache()
    counts = {"movie": None, "series": None}
    for media_type, client, builder in (("movie", radarr, _movie_rows), ("series", sonarr, _series_rows)):
        if not client.configured:
            continue
        try:
            rows = builder(client, jf, settings, id_cache)
        except UpstreamError as e:
            log.warning("[Library] %s unavailable (%s) - keeping the previous snapshot", client.kind, e)
            continue
        store.replace_library(rows, media_types={media_type})
        counts[media_type] = len(rows)
    return counts
