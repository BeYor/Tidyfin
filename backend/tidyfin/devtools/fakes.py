"""In-memory stand-ins for Jellyfin, Radarr and Sonarr.

They implement the same public methods as ``tidyfin.clients`` so the real
cycle, services and routes can run against them."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from ..clients import ClientFactory
from ..errors import AuthError, UpstreamError


class Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code, self.text = status_code, text


def make_jpeg(w=200, h=300, color=(30, 60, 120), title: str = "") -> bytes:
    img = Image.new("RGB", (w, h), color)
    d = ImageDraw.Draw(img)
    for y in range(h):                       # vertical gradient towards the bottom
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(c * (1 - 0.65 * t)) for c in color))
    if title:
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", max(w // 12, 10))
        except OSError:
            font = ImageFont.load_default()
        words, lines, cur = title.split(), [], ""
        for word in words:
            trial = (cur + " " + word).strip()
            if d.textlength(trial, font=font) > w * 0.84 and cur:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
        y = int(h * 0.62)
        for line in lines[:3]:
            d.text((w * 0.08, y), line, fill=(243, 247, 251), font=font)
            y += int(w / 10)
    out = BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


def iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class FakeJellyfin:
    def __init__(self):
        self.movies, self.series = {}, {}          # external id -> Jellyfin item
        self.last_played = {}                      # item id -> datetime
        self.posters, self.uploads = {}, []
        self.collections, self.favorites_by_user, self.unfavorited = {}, {}, []
        self.index_ok = self.users_ok = True
        self.exists_ok = self.activity_ok = True      # set to False to simulate "could not check"
        self.resume_by_user, self.tag_updates = {}, []
        self.episodes, self.items_by_id = {}, {}
        self.accounts = {"admin": ("pass", "u-admin", True), "ola": ("pass", "u-ola", False)}
        self.user_list = [{"Id": "u-admin", "Name": "admin", "Policy": {"IsAdministrator": True}},
                          {"Id": "u-ola", "Name": "ola", "Policy": {"IsAdministrator": False}}]

    # ----- connection / login -----
    def reset_cycle_cache(self): pass
    def public_info(self): return {"ServerName": "Demo Jellyfin", "Version": "10.10.0"}
    def ping(self): return {"name": "Demo Jellyfin", "version": "10.10.0"}

    def authenticate_by_name(self, username, password):
        acc = self.accounts.get(username)
        if not acc or acc[0] != password:
            raise AuthError("Invalid username or password.")
        return {"access_token": f"token-{username}", "user_id": acc[1], "user_name": username, "is_admin": acc[2]}

    def ensure_api_key(self, admin_token, app_name="Tidyfin"): return "generated-api-key"
    def users(self): return self.user_list if self.users_ok else None

    # ----- items -----
    def add_movie(self, tmdb, jid, name="Movie", streams=None, path=None, tags=None):
        item = {"Id": jid, "Name": name, "ProviderIds": {"Tmdb": str(tmdb)}, "Type": "Movie",
                "MediaStreams": streams or [], "Path": path, "Tags": tags or []}
        self.movies[str(tmdb)] = self.items_by_id[jid] = item
        self.posters[jid] = make_jpeg(title=name)
        return item

    def add_series(self, tvdb, jid, name="Series", tags=None):
        item = {"Id": jid, "Name": name, "ProviderIds": {"Tvdb": str(tvdb)}, "Type": "Series", "Tags": tags or []}
        self.series[str(tvdb)] = self.items_by_id[jid] = item
        self.posters[jid] = make_jpeg(title=name)
        return item

    def resolve_item(self, tmdb_id, tvdb_id, media_type):
        if not self.index_ok:
            return None, False
        data = self.movies if media_type == "movie" else self.series
        return data.get(str(tmdb_id) if media_type == "movie" else str(tvdb_id)), True

    def full_library_index(self):
        return ({}, {}) if not self.index_ok else (dict(self.movies), dict(self.series))

    def lookup_by_key(self, key):
        mt, _, ext = key.partition("_")
        item = (self.movies if mt == "movie" else self.series).get(ext)
        return item["Id"] if item else None

    def fetch_item(self, jid): return self.items_by_id.get(jid)
    def series_episodes(self, sid): return self.episodes.get(sid, [])

    def last_played_across_users(self, jid, media_type=""):
        return (None, False) if not self.users_ok else (self.last_played.get(jid), True)

    def existing_item_ids(self, ids):
        return None if self.exists_ok is False else {i for i in ids if i in self.items_by_id}

    def favorites_of(self, user_id):
        return None if self.activity_ok is False else list(self.favorites_by_user.get(user_id, []))

    def resume_items(self, user_id):
        return None if self.activity_ok is False else list(self.resume_by_user.get(user_id, []))

    def set_item_tags(self, jid, add=(), remove=()):
        item = self.items_by_id.get(jid)
        if not item:
            return False
        drop = {t.lower() for t in remove}
        tags = [t for t in item.get("Tags", []) if t.lower() not in drop]
        tags += [t for t in add if t.lower() not in {x.lower() for x in tags}]
        item["Tags"] = tags
        self.tag_updates.append((jid, list(add), list(remove)))
        return True

    # ----- images -----
    def item_exists(self, jid): return jid in self.posters
    def get_primary_image(self, jid, max_width=None): return self.posters.get(jid)

    def upload_primary_image(self, jid, data):
        self.uploads.append((jid, data))
        self.posters[jid] = data
        return True

    # ----- stars -----
    def favorites(self, user_id): return list(self.favorites_by_user.get(user_id, []))

    def unfavorite(self, user_id, jid):
        self.unfavorited.append(jid)
        self.favorites_by_user[user_id] = [i for i in self.favorites_by_user.get(user_id, []) if i["Id"] != jid]
        return True

    # ----- collections -----
    def collection_member_ids(self, cid): return set(self.collections.get(cid, set()))

    def add_to_collection(self, cid, ids):
        self.collections.setdefault(cid, set()).update(ids)
        return Resp(204)

    def remove_from_collection(self, cid, ids):
        self.collections.setdefault(cid, set()).difference_update(ids)
        return Resp(204)

    def find_collection_by_name(self, name): return None

    def create_collection(self, name):
        cid = f"col-{name}"
        self.collections[cid] = set()
        return cid


class FakeArr:
    def __init__(self, kind):
        self.kind, self.configured = kind, True
        self._items, self._tags, self._episode_files = [], {}, {}
        self.deleted, self.fail, self.delete_ok = [], False, True
        self.tag_calls = []

    def _check(self):
        if self.fail:
            raise UpstreamError(f"{self.kind} unavailable", code="arr_unreachable")

    def ping(self): self._check(); return {"name": self.kind.capitalize(), "version": "5.2.0"}
    def tags(self): self._check(); return dict(self._tags)
    def items(self): self._check(); return list(self._items)
    def episode_files(self, sid): self._check(); return self._episode_files.get(sid, [])

    def find_by_external_id(self, ext):
        self._check()
        key = "tmdbId" if self.kind == "radarr" else "tvdbId"
        return next((i for i in self._items if str(i.get(key)) == str(ext)), None)

    def delete(self, item_id):
        if not self.delete_ok:
            return False
        self.deleted.append(item_id)
        self._items = [i for i in self._items if i["id"] != item_id]
        return True

    def ensure_tag(self, label):
        self._check()
        for tid, existing in self._tags.items():
            if existing.lower() == label.lower():
                return tid
        tid = max(self._tags, default=0) + 1
        self._tags[tid] = label.lower()
        return tid

    def edit_tags(self, item_id, tag_ids, mode):
        self._check()
        self.tag_calls.append((item_id, list(tag_ids), mode))
        for i in self._items:
            if i["id"] == item_id:
                cur = set(i.get("tags", []))
                i["tags"] = sorted(cur | set(tag_ids) if mode == "add" else cur - set(tag_ids))

    def add_movie(self, rid, tmdb, title, file_days_ago=100, tags=(), size=5_000_000_000, year=2020):
        self._items.append({"id": rid, "tmdbId": tmdb, "title": title, "year": year, "hasFile": True, "added": iso(file_days_ago),
                            "movieFile": {"dateAdded": iso(file_days_ago), "size": size}, "tags": list(tags), "sizeOnDisk": size})

    def add_series(self, sid, tvdb, title, file_days_ago=100, tags=(), size=20_000_000_000, year=2019):
        self._items.append({"id": sid, "tvdbId": tvdb, "title": title, "year": year, "added": iso(file_days_ago),
                            "tags": list(tags), "statistics": {"episodeFileCount": 10, "sizeOnDisk": size}})
        self._episode_files[sid] = [{"dateAdded": iso(file_days_ago)}]


class FakeFactory(ClientFactory):
    """Hands out the same in-memory fakes for every request, so state is shared."""

    def __init__(self, jf=None, radarr=None, sonarr=None):
        self.jf, self.radarr, self.sonarr = jf or FakeJellyfin(), radarr or FakeArr("radarr"), sonarr or FakeArr("sonarr")

    def jellyfin(self, url, api_key="", timeout=15): return self.jf

    def arr(self, kind, url, api_key="", timeout=15):
        client = self.radarr if kind == "radarr" else self.sonarr
        client.configured = bool(url and api_key)
        return client
