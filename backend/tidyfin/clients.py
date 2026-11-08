"""
HTTP clients for Jellyfin, Radarr, and Sonarr.

Always distinguish between confirmed missing data and failed requests.
Errors are reported as UpstreamError (or ok=False), ensuring network issues are never mistaken for empty libraries or missing activity.
"""
from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import __version__
from .errors import AuthError, UpstreamError
from .logbus import log

__all__ = ["JellyfinClient", "ArrClient", "make_session", "UpstreamError", "AuthError"]


def make_session(timeout: int = 15) -> requests.Session:
    """Session with a shared timeout and a light retry on 502/503/504."""
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=1.0, status_forcelist=(502, 503, 504))
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    orig_request = session.request

    def request_with_timeout(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return orig_request(method, url, **kwargs)

    session.request = request_with_timeout  # type: ignore[assignment]
    return session


def _auth_header(token: Optional[str] = None) -> str:
    value = f'MediaBrowser Client="Tidyfin", Device="Tidyfin", DeviceId="tidyfin-server", Version="{__version__}"'
    if token:
        value += f', Token="{token}"'
    return value


_TMDBID_IN_PATH_RE = re.compile(r"\[tmdbid[-=](\d+)\]", re.IGNORECASE)
_TVDBID_IN_PATH_RE = re.compile(r"\[tvdbid[-=](\d+)\]", re.IGNORECASE)


def parse_jf_date(raw) -> Optional[datetime]:
    """Jellyfin dates look like 2026-10-30T20:15:00.0000000Z (7 fractional digits)."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip().replace("Z", "+00:00")
    m = re.match(r"^(.*?\.\d{1,6})\d*(.*)$", text)
    if m:
        text = m.group(1) + m.group(2)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class JellyfinClient:
    def __init__(self, url: str, api_key: str = "", session: Optional[requests.Session] = None, timeout: int = 15):
        self.base = url.rstrip("/")
        self.api_key = api_key
        self.http = session or make_session(timeout)
        self.headers = {"Authorization": _auth_header(api_key or None), "X-Emby-Token": api_key}
        self._index_cache: dict = {}
        self._users_cache: Optional[list] = None

    # ---------- per-cycle caches ----------

    def reset_cycle_cache(self):
        """The library index is only valid for a single cycle."""
        self._index_cache = {}
        self._users_cache = None

    # ---------- connection / login ----------

    def public_info(self) -> dict:
        resp = self.http.get(f"{self.base}/System/Info/Public")
        if resp.status_code != 200:
            raise UpstreamError(f"Jellyfin answered HTTP {resp.status_code}", code="jellyfin_http")
        return resp.json()

    def ping(self) -> dict:
        """API key check: the full /System/Info requires authorization."""
        resp = self.http.get(f"{self.base}/System/Info", headers=self.headers)
        if resp.status_code in (401, 403):
            raise UpstreamError(f"Jellyfin rejected the API key (HTTP {resp.status_code})", code="bad_api_key")
        if resp.status_code != 200:
            raise UpstreamError(f"Jellyfin answered HTTP {resp.status_code}", code="jellyfin_http")
        info = resp.json()
        return {"name": info.get("ServerName"), "version": info.get("Version")}

    def authenticate_by_name(self, username: str, password: str) -> dict:
        resp = self.http.post(
            f"{self.base}/Users/AuthenticateByName",
            headers={"Authorization": _auth_header(), "Content-Type": "application/json"},
            json={"Username": username, "Pw": password})
        if resp.status_code in (400, 401, 403):
            raise AuthError("Invalid username or password.", code="invalid_credentials")
        if resp.status_code != 200:
            raise UpstreamError(f"Jellyfin answered HTTP {resp.status_code}", code="jellyfin_http")
        data = resp.json()
        user = data.get("User") or {}
        return {"access_token": data.get("AccessToken", ""), "user_id": user.get("Id", ""),
                "user_name": user.get("Name", username),
                "is_admin": bool((user.get("Policy") or {}).get("IsAdministrator"))}

    def ensure_api_key(self, admin_token: str, app_name: str = "Tidyfin") -> str:
        """Create (or reuse) a Jellyfin API key for Tidyfin using the token of the logged-in admin, so nobody has to copy it from the dashboard."""
        headers = {"Authorization": _auth_header(admin_token), "X-Emby-Token": admin_token}

        def _find():
            r = self.http.get(f"{self.base}/Auth/Keys", headers=headers)
            if r.status_code != 200:
                raise UpstreamError(f"Could not list API keys: HTTP {r.status_code}", code="api_key_failed")
            items = [k for k in r.json().get("Items", []) if k.get("AppName") == app_name]
            items.sort(key=lambda k: k.get("DateCreated") or "")
            return items[-1]["AccessToken"] if items else None

        key = _find()
        if key:
            return key
        r = self.http.post(f"{self.base}/Auth/Keys", headers=headers, params={"app": app_name})
        if r.status_code not in (200, 204):
            raise UpstreamError(f"Could not create an API key: HTTP {r.status_code}", code="api_key_failed")
        key = _find()
        if not key:
            raise UpstreamError("API key created but could not be read back.", code="api_key_failed")
        return key

    # ---------- users / playback ----------

    def users(self) -> Optional[list]:
        if self._users_cache is None:
            resp = self.http.get(f"{self.base}/Users", headers=self.headers)
            if resp.status_code != 200:
                return None
            self._users_cache = resp.json()
        return self._users_cache

    def last_played_across_users(self, item_id: str, media_type: str = ""):
        """
        Returns the latest play date and whether the check succeeded.

        If the check fails, the item must not be treated as unwatched.

        Uses Jellyfin's dedicated UserData endpoint for reliable results.

        For series, checks episodes instead, using the latest played or resumable episode date.
        """
        users = self.users()
        if users is None:
            return None, False
        latest = None
        for user in users:
            found = []
            ud = self.http.get(f"{self.base}/UserItems/{item_id}/UserData", headers=self.headers,
                               params={"userId": user["Id"]})
            if ud.status_code == 200:
                found.append(ud.json().get("LastPlayedDate"))
            elif ud.status_code not in (403, 404):      # no access / no data for this user is fine, anything else is "unknown"
                return None, False
            if media_type == "series":
                ok, episode_date = self._last_episode_play(user["Id"], item_id)
                if not ok:
                    return None, False
                found.append(episode_date)
            for raw in found:
                dt = parse_jf_date(raw)
                if dt and (latest is None or dt > latest):
                    latest = dt
        return latest, True

    def _last_episode_play(self, user_id: str, series_id: str):
        """(ok, newest LastPlayedDate string | None) over played and half-watched episodes."""
        newest = None
        for flt in ("IsPlayed", "IsResumable"):
            resp = self.http.get(f"{self.base}/Items", headers=self.headers, params={
                "userId": user_id, "ParentId": series_id, "Recursive": "true", "IncludeItemTypes": "Episode",
                "Filters": flt, "SortBy": "DatePlayed", "SortOrder": "Descending", "Limit": "1", "EnableUserData": "true"})
            if resp.status_code in (403, 404):
                continue
            if resp.status_code != 200:
                return False, None
            for it in resp.json().get("Items", []):
                dt = parse_jf_date((it.get("UserData") or {}).get("LastPlayedDate"))
                if dt and (newest is None or dt > newest):
                    newest = dt
        return True, (newest.isoformat() if newest else None)

    def existing_item_ids(self, ids) -> Optional[set]:
        """Which of the given Jellyfin ids still exist. None = could not check(callers must then not drop anything)."""
        wanted = sorted({i for i in ids if i})
        found: set = set()
        for n in range(0, len(wanted), 50):
            resp = self.http.get(f"{self.base}/Items", headers=self.headers,
                                 params={"Ids": ",".join(wanted[n:n + 50]), "Recursive": "true"})
            if resp.status_code != 200:
                return None
            found |= {item["Id"] for item in resp.json().get("Items", [])}
        return found

    def favorites_of(self, user_id: str) -> Optional[list]:
        """Like ``favorites`` but None when the request failed (an empty list then would look like "the user has no favorites")."""
        resp = self.http.get(f"{self.base}/Users/{user_id}/Items", headers=self.headers, params={
            "Filters": "IsFavorite", "Recursive": "true", "IncludeItemTypes": "Movie,Series",
            "CollapseBoxSetItems": "false", "Fields": "ProviderIds"})
        return resp.json().get("Items", []) if resp.status_code == 200 else None

    def resume_items(self, user_id: str) -> Optional[list]:
        """Continue watching" of one user: [{item_id, last_played | None}] where item_id is the movie, or the SERIES for an episode. None = could not check."""
        resp = self.http.get(f"{self.base}/UserItems/Resume", headers=self.headers, params={
            "userId": user_id, "MediaTypes": "Video", "Limit": "300", "Recursive": "true", "EnableUserData": "true"})
        if resp.status_code != 200:
            return None
        out = []
        for it in resp.json().get("Items", []):
            item_id = it.get("SeriesId") if it.get("Type") == "Episode" else it.get("Id")
            if item_id:
                out.append({"item_id": item_id, "last_played": parse_jf_date((it.get("UserData") or {}).get("LastPlayedDate"))})
        return out

    def set_item_tags(self, item_id: str, add=(), remove=()) -> bool:
        """Add / remove tags on an item in Jellyfin (read the item, change its Tags,write it back - the way the Jellyfin web client edits metadata)."""
        resp = self.http.get(f"{self.base}/Items/{item_id}", headers=self.headers)
        if resp.status_code != 200:
            return False
        item = resp.json()
        drop = {t.lower() for t in remove}
        tags = [t for t in (item.get("Tags") or []) if t.lower() not in drop]
        have = {t.lower() for t in tags}
        tags += [t for t in add if t.lower() not in have]
        item["Tags"] = tags
        item["TagItems"] = [{"Name": t} for t in tags]
        upd = self.http.post(f"{self.base}/Items/{item_id}", headers=self.headers, json=item)
        if upd.status_code not in (200, 204):
            log.warning("Could not update the tags of %s: HTTP %s - %s", item_id, upd.status_code, upd.text[:200])
            return False
        return True

    # ---------- images ----------

    def item_exists(self, item_id: str) -> bool:
        return self.http.get(f"{self.base}/Items/{item_id}/Images/Primary", headers=self.headers).status_code == 200

    def get_primary_image(self, item_id: str, max_width: Optional[int] = None) -> Optional[bytes]:
        params = {"maxWidth": max_width} if max_width else None
        resp = self.http.get(f"{self.base}/Items/{item_id}/Images/Primary", headers=self.headers, params=params)
        if resp.status_code == 404:
            return None                  # the item has no poster; callers say so once
        if resp.status_code != 200:
            log.warning("Could not download the poster of %s (HTTP %s)", item_id, resp.status_code)
            return None
        return resp.content

    def upload_primary_image(self, item_id: str, image_bytes: bytes) -> bool:
        # Jellyfin expects the image base64-encoded in the body even though
        # the content type says image/jpeg.
        resp = self.http.post(f"{self.base}/Items/{item_id}/Images/Primary",
                              headers={**self.headers, "Content-Type": "image/jpeg"},
                              data=base64.b64encode(image_bytes))
        if resp.status_code not in (200, 204):
            log.warning("Poster upload failed for %s: HTTP %s - %s", item_id, resp.status_code, resp.text[:200])
            return False
        return True

    # ---------- library index ----------

    def _build_provider_index(self, item_type: str):
        """
        Jellyfin's AnyProviderIdEquals filter is unreliable, so Tidyfin fetches all items of the requested type and builds its own TMDB/TVDB index.

        If an item has no provider ID, IDs found in its path ([tmdbid-X] / [tvdbid-X]) are used instead.

        Returns None if the request fails.
        """
        resp = self.http.get(f"{self.base}/Items", headers=self.headers, params={
            "IncludeItemTypes": item_type, "Recursive": "true", "CollapseBoxSetItems": "false",
            "Fields": "ProviderIds,MediaStreams,Path,Tags", "Limit": "-1"})
        if resp.status_code != 200:
            log.warning("Could not list %s items from Jellyfin: HTTP %s", item_type, resp.status_code)
            return None
        raw_items = resp.json().get("Items", [])
        index, fallback_count = {}, 0
        id_pattern = _TMDBID_IN_PATH_RE if item_type == "Movie" else _TVDBID_IN_PATH_RE
        for item in raw_items:
            pids = item.get("ProviderIds", {})
            key = pids.get("Tmdb") if item_type == "Movie" else pids.get("Tvdb")
            if not key:
                match = id_pattern.search(item.get("Path") or "")
                if match:
                    key = match.group(1)
                    fallback_count += 1
            if key:
                index[str(key)] = item
        msg = f"[Diagnostics] {item_type}: fetched {len(raw_items)} items, indexed {len(index)}"
        if fallback_count:
            msg += f" ({fallback_count} resolved from the path annotation)"
        log.info(msg)
        return index

    def provider_index(self, item_type: str):
        entry = self._index_cache.get(item_type)
        if entry is None:
            entry = {"data": self._build_provider_index(item_type)}
            self._index_cache[item_type] = entry
        return entry["data"]

    def resolve_item(self, tmdb_id, tvdb_id, media_type: str):
        """(item | None, lookup_ok). lookup_ok=False means we could not check."""
        data = self.provider_index("Movie" if media_type == "movie" else "Series")
        if data is None:
            return None, False
        return data.get(str(tmdb_id) if media_type == "movie" else str(tvdb_id)), True

    def lookup_by_key(self, key: str) -> Optional[str]:
        """'movie_123' / 'series_456' -> current Jellyfin id (or None)."""
        media_type, _, ext = key.partition("_")
        if media_type not in ("movie", "series") or not ext:
            return None
        item, _ok = self.resolve_item(ext if media_type == "movie" else None,
                                      ext if media_type == "series" else None, media_type)
        return item["Id"] if item else None

    def full_library_index(self):
        return self.provider_index("Movie") or {}, self.provider_index("Series") or {}

    def fetch_item(self, item_id: str) -> Optional[dict]:
        resp = self.http.get(f"{self.base}/Items/{item_id}", headers=self.headers,
                             params={"Fields": "Tags,MediaStreams,Path,ProviderIds"})
        return resp.json() if resp.status_code == 200 else None

    def series_episodes(self, series_id: str) -> list:
        resp = self.http.get(f"{self.base}/Shows/{series_id}/Episodes", headers=self.headers,
                             params={"Fields": "MediaStreams,Path"})
        if resp.status_code != 200:
            log.warning("Could not list the episodes of series %s: HTTP %s", series_id, resp.status_code)
            return []
        return resp.json().get("Items", [])

    # ---------- favorites (stars) ----------

    def favorites(self, user_id: str) -> list:
        resp = self.http.get(f"{self.base}/Users/{user_id}/Items", headers=self.headers, params={
            "Filters": "IsFavorite", "Recursive": "true", "IncludeItemTypes": "Movie,Series",
            "CollapseBoxSetItems": "false", "Fields": "ProviderIds"})
        return resp.json().get("Items", []) if resp.status_code == 200 else []

    def unfavorite(self, user_id: str, item_id: str) -> bool:
        resp = self.http.delete(f"{self.base}/Users/{user_id}/FavoriteItems/{item_id}", headers=self.headers)
        return resp.status_code in (200, 204)

    # ---------- collections ----------

    def collection_member_ids(self, collection_id: str) -> set:
        resp = self.http.get(f"{self.base}/Items", headers=self.headers, params={
            "ParentId": collection_id, "Recursive": "true", "IncludeItemTypes": "Movie,Series"})
        if resp.status_code != 200:
            return set()
        return {item["Id"] for item in resp.json().get("Items", [])}

    def add_to_collection(self, collection_id: str, ids) -> requests.Response:
        return self.http.post(f"{self.base}/Collections/{collection_id}/Items", headers=self.headers,
                              params={"ids": ",".join(ids)})

    def remove_from_collection(self, collection_id: str, ids) -> requests.Response:
        return self.http.delete(f"{self.base}/Collections/{collection_id}/Items", headers=self.headers,
                                params={"ids": ",".join(ids)})

    def find_collection_by_name(self, name: str) -> Optional[str]:
        resp = self.http.get(f"{self.base}/Items", headers=self.headers,
                             params={"IncludeItemTypes": "BoxSet", "Recursive": "true"})
        if resp.status_code != 200:
            return None
        for item in resp.json().get("Items", []):
            if (item.get("Name") or "").strip().lower() == name.strip().lower():
                return item["Id"]
        return None

    def create_collection(self, name: str) -> Optional[str]:
        resp = self.http.post(f"{self.base}/Collections", headers=self.headers, params={"name": name})
        if resp.status_code != 200:
            log.warning("Could not create the collection '%s': HTTP %s - %s", name, resp.status_code, resp.text[:200])
            return None
        return (resp.json() or {}).get("Id")


class ArrClient:
    """Radarr (kind='radarr') or Sonarr (kind='sonarr'), API v3."""

    def __init__(self, kind: str, url: str, api_key: str = "", session: Optional[requests.Session] = None,
                 timeout: int = 15):
        assert kind in ("radarr", "sonarr")
        self.kind = kind
        self.base = url.rstrip("/")
        self.headers = {"X-Api-Key": api_key}
        self.http = session or make_session(timeout)

    @property
    def configured(self) -> bool:
        return bool(self.base and self.headers.get("X-Api-Key"))

    @property
    def _resource(self) -> str:
        return "movie" if self.kind == "radarr" else "series"

    def _request(self, method: str, path: str, **kw):
        try:
            return self.http.request(method, f"{self.base}/api/v3/{path}", headers=self.headers, **kw)
        except requests.RequestException as e:
            raise UpstreamError(f"{self.kind}: {e}", code="arr_unreachable") from e

    def _get_json(self, path: str, **kw):
        resp = self._request("GET", path, **kw)
        if resp.status_code != 200:
            raise UpstreamError(f"{self.kind}: HTTP {resp.status_code} for /{path}", code="arr_http")
        return resp.json()

    def ping(self) -> dict:
        data = self._get_json("system/status")
        return {"name": data.get("appName"), "version": data.get("version")}

    def tags(self) -> dict:
        return {t["id"]: t["label"] for t in self._get_json("tag")}

    def items(self) -> list:
        return self._get_json(self._resource)

    def episode_files(self, series_id) -> list:
        return self._get_json("episodefile", params={"seriesId": series_id})

    def find_by_external_id(self, ext_id) -> Optional[dict]:
        param = {"tmdbId": ext_id} if self.kind == "radarr" else {"tvdbId": ext_id}
        data = self._get_json(self._resource, params=param)
        return data[0] if data else None

    def delete(self, item_id) -> bool:
        params = {"deleteFiles": "true"}
        params["addImportExclusion" if self.kind == "radarr" else "addImportListExclusion"] = "false"
        resp = self._request("DELETE", f"{self._resource}/{item_id}", params=params)
        return resp.status_code in (200, 204)

    # ---------- tags ----------

    def ensure_tag(self, label: str) -> int:
        """Id of the tag with this label (case-insensitive); created if missing."""
        for tag_id, existing in self.tags().items():
            if existing.lower() == label.lower():
                return tag_id
        resp = self._request("POST", "tag", json={"label": label.lower()})
        if resp.status_code not in (200, 201):
            raise UpstreamError(f"{self.kind}: could not create the tag '{label}' (HTTP {resp.status_code})",
                                code="arr_tag_failed")
        return resp.json()["id"]

    def edit_tags(self, item_id: int, tag_ids: list, mode: str):
        """
        Adds or removes tags from an item.

        Uses the bulk editor to avoid overwriting other fields. If unavailable, updates the full item instead.
        """
        assert mode in ("add", "remove")
        ids_key = "movieIds" if self.kind == "radarr" else "seriesIds"
        resp = self._request("PUT", f"{self._resource}/editor",
                             json={ids_key: [item_id], "tags": list(tag_ids), "applyTags": mode})
        if resp.status_code in (200, 202):
            return
        if resp.status_code not in (404, 405):
            raise UpstreamError(f"{self.kind}: tag update failed (HTTP {resp.status_code})", code="arr_tag_failed")
        item = self._get_json(f"{self._resource}/{item_id}")
        current = set(item.get("tags", []))
        item["tags"] = sorted(current | set(tag_ids) if mode == "add" else current - set(tag_ids))
        put = self._request("PUT", f"{self._resource}/{item_id}", json=item)
        if put.status_code not in (200, 202):
            raise UpstreamError(f"{self.kind}: tag update failed (HTTP {put.status_code})", code="arr_tag_failed")


class ClientFactory:
    """Creates upstream clients. The dev server and the tests swap this for in-memory fakes, so the whole application can run without Jellyfin."""

    def jellyfin(self, url: str, api_key: str = "", timeout: int = 15) -> JellyfinClient:
        return JellyfinClient(url, api_key, timeout=timeout)

    def arr(self, kind: str, url: str, api_key: str = "", timeout: int = 15) -> ArrClient:
        return ArrClient(kind, url, api_key, timeout=timeout)
