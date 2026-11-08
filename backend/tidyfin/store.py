"""
Persistent state in a single SQLite file inside the data directory.

The two deletion queues and the poster id cache are stored as plain
dictionaries (one JSON document per title). The ``library`` table is a snapshot of every movie and
series Radarr/Sonarr know about, refreshed each cycle, so the dashboard can
browse and search without hammering the upstream services.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import secrets
import sqlite3
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_STATE_TABLES = {"auto_queue", "manual_queue"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS auto_queue (key TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS manual_queue (key TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS id_cache (key TEXT PRIMARY KEY, jellyfin_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS library (
    key TEXT PRIMARY KEY, media_type TEXT NOT NULL, title TEXT NOT NULL, title_norm TEXT NOT NULL,
    year INTEGER, size_bytes INTEGER NOT NULL DEFAULT 0, added_at TEXT, arr_id INTEGER,
    tmdb_id INTEGER, tvdb_id INTEGER, jellyfin_id TEXT, tags TEXT NOT NULL DEFAULT '[]',
    protected INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_library_type ON library(media_type);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, event TEXT NOT NULL, key TEXT, title TEXT,
    media_type TEXT, source TEXT, details TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_ts ON history(ts DESC);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
    trigger TEXT, dry_run INTEGER NOT NULL DEFAULT 0, summary TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT, user_name TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
"""

_LIBRARY_SORTS = {"title": "title_norm", "size": "size_bytes", "added": "added_at"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# Letters that do not decompose into base + accent under NFKD.
_EXTRA_FOLD = str.maketrans({"ł": "l", "ø": "o", "đ": "d", "ħ": "h", "ı": "i"})


def normalize_title(text: str) -> str:
    """Case- and accent-insensitive form used for searching and sorting."""
    folded = unicodedata.normalize("NFKD", (text or "").casefold().translate(_EXTRA_FOLD))
    return "".join(c for c in folded if not unicodedata.combining(c))


class Store:
    def __init__(self, path):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(SCHEMA)
            # A cycle interrupted by a container restart must not stay "running".
            self._db.execute("UPDATE runs SET status='interrupted', finished_at=? WHERE status='running'",
                             (_iso(utcnow()),))

    @contextlib.contextmanager
    def _tx(self):
        with self._lock:
            self._db.execute("BEGIN")
            try:
                yield self._db
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    def close(self):
        with self._lock:
            self._db.close()

    # ---------- key/value ----------

    def get_json(self, key: str, default=None):
        with self._lock:
            row = self._db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    def set_json(self, key: str, value):
        payload = json.dumps(value, ensure_ascii=False)
        with self._tx() as db:
            db.execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, payload))

    # ---------- deletion queues (key -> dict) ----------

    def _load_state(self, table: str) -> dict:
        assert table in _STATE_TABLES
        with self._lock:
            rows = self._db.execute(f"SELECT key, data FROM {table}").fetchall()
        out = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["data"])
            except json.JSONDecodeError:
                continue
        return out

    def _save_state(self, table: str, state: dict):
        assert table in _STATE_TABLES
        with self._tx() as db:
            existing = {r[0] for r in db.execute(f"SELECT key FROM {table}")}
            for k in existing - set(state):
                db.execute(f"DELETE FROM {table} WHERE key=?", (k,))
            for k, v in state.items():
                db.execute(f"INSERT INTO {table}(key, data) VALUES(?, ?) "
                           f"ON CONFLICT(key) DO UPDATE SET data=excluded.data", (k, json.dumps(v, ensure_ascii=False)))

    def load_auto_state(self) -> dict:
        return self._load_state("auto_queue")

    def save_auto_state(self, state: dict):
        self._save_state("auto_queue", state)

    def load_manual_state(self) -> dict:
        return self._load_state("manual_queue")

    def save_manual_state(self, state: dict):
        self._save_state("manual_queue", state)

    # ---------- Jellyfin id cache ----------

    def load_library_id_cache(self) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT key, jellyfin_id FROM id_cache").fetchall()
        return {r["key"]: r["jellyfin_id"] for r in rows}

    def save_library_id_cache(self, cache: dict):
        with self._tx() as db:
            existing = {r[0] for r in db.execute("SELECT key FROM id_cache")}
            for k in existing - set(cache):
                db.execute("DELETE FROM id_cache WHERE key=?", (k,))
            for k, v in cache.items():
                db.execute("INSERT INTO id_cache(key, jellyfin_id) VALUES(?, ?) "
                           "ON CONFLICT(key) DO UPDATE SET jellyfin_id=excluded.jellyfin_id", (k, v))

    # ---------- library snapshot ----------

    def replace_library(self, rows: list, media_types: Optional[set] = None):
        """Replace the snapshot. ``media_types`` limits the replacement to the
        given types, so an unreachable Radarr never wipes the series rows."""
        now = _iso(utcnow())
        with self._tx() as db:
            if media_types is None:
                db.execute("DELETE FROM library")
            else:
                for t in media_types:
                    db.execute("DELETE FROM library WHERE media_type=?", (t,))
            for r in rows:
                db.execute(
                    "INSERT OR REPLACE INTO library(key, media_type, title, title_norm, year, size_bytes, added_at, arr_id, "
                    "tmdb_id, tvdb_id, jellyfin_id, tags, protected, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["key"], r["media_type"], r["title"], normalize_title(r["title"]), r.get("year"), r.get("size_bytes") or 0,
                     r.get("added_at"), r.get("arr_id"), r.get("tmdb_id"), r.get("tvdb_id"), r.get("jellyfin_id"),
                     json.dumps(r.get("tags") or [], ensure_ascii=False), 1 if r.get("protected") else 0, now))

    @staticmethod
    def _library_row(r) -> dict:
        d = dict(r)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["protected"] = bool(d["protected"])
        d.pop("title_norm", None)
        return d

    def library_keys(self, media_type: str) -> set:
        with self._lock:
            rows = self._db.execute("SELECT key FROM library WHERE media_type=?", (media_type,)).fetchall()
        return {r["key"] for r in rows}

    def get_library_item(self, key: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT * FROM library WHERE key=?", (key,)).fetchone()
        return self._library_row(row) if row else None

    def update_library_item(self, key: str, **fields):
        allowed = {"tags", "protected", "jellyfin_id", "size_bytes"}
        sets, params = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(k)
            sets.append(f"{k}=?")
            params.append(json.dumps(v, ensure_ascii=False) if k == "tags" else (1 if v is True else 0 if v is False else v))
        if sets:
            with self._tx() as db:
                db.execute(f"UPDATE library SET {', '.join(sets)} WHERE key=?", params + [key])

    def query_library(self, media_type: Optional[str] = None, q: str = "", status: str = "all",
                      sort: str = "title", descending: bool = False, limit: int = 60, offset: int = 0) -> dict:
        where, params = ["1=1"], []
        if media_type in ("movie", "series"):
            where.append("l.media_type=?")
            params.append(media_type)
        if q.strip():
            where.append("l.title_norm LIKE ?")
            params.append(f"%{normalize_title(q.strip())}%")
        if status == "protected":
            where.append("l.protected=1")
        elif status == "queued":
            where.append("(a.key IS NOT NULL OR m.key IS NOT NULL)")
        elif status == "free":
            where.append("a.key IS NULL AND m.key IS NULL AND l.protected=0")
        column = _LIBRARY_SORTS.get(sort, "title_norm")
        direction = "DESC" if descending else "ASC"
        base = ("FROM library l LEFT JOIN auto_queue a ON a.key=l.key LEFT JOIN manual_queue m ON m.key=l.key "
                f"WHERE {' AND '.join(where)}")
        with self._lock:
            total, size = self._db.execute(f"SELECT COUNT(*), COALESCE(SUM(l.size_bytes),0) {base}", params).fetchone()
            rows = self._db.execute(
                f"SELECT l.*, a.data AS auto_data, m.data AS manual_data {base} "
                f"ORDER BY l.{column} {direction}, l.title_norm ASC LIMIT ? OFFSET ?",
                params + [int(limit), int(offset)]).fetchall()
        items = []
        for r in rows:
            d = self._library_row(r)
            auto, manual = d.pop("auto_data"), d.pop("manual_data")
            entry = json.loads(manual) if manual else json.loads(auto) if auto else None
            d["queue"] = None if not entry else {
                "source": "manual" if manual else "auto", "delete_at": entry.get("delete_at"),
                "accelerated": bool(entry.get("accelerated"))}
            items.append(d)
        return {"total": total, "total_size_bytes": size, "items": items}

    def library_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM library").fetchone()[0]

    def list_protected(self) -> list:
        with self._lock:
            rows = self._db.execute("SELECT * FROM library WHERE protected=1 ORDER BY title_norm").fetchall()
        return [self._library_row(r) for r in rows]

    # ---------- history ----------

    def add_history(self, event: str, key: str = "", title: str = "", media_type: str = "",
                    source: str = "", details: Optional[dict] = None):
        with self._tx() as db:
            db.execute("INSERT INTO history(ts, event, key, title, media_type, source, details) VALUES(?,?,?,?,?,?,?)",
                       (_iso(utcnow()), event, key, title, media_type, source,
                        json.dumps(details, ensure_ascii=False) if details else None))

    def list_history(self, limit: int = 100, offset: int = 0, event: Optional[str] = None) -> dict:
        where, params = "", []
        if event:
            where, params = "WHERE event=?", [event]
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM history {where}", params).fetchone()[0]
            rows = self._db.execute(f"SELECT * FROM history {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                                    params + [int(limit), int(offset)]).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["details"] = json.loads(d["details"]) if d.get("details") else None
            items.append(d)
        return {"total": total, "items": items}

    def history_since(self, since_iso: str) -> list:
        """Every history row newer than ``since_iso`` (oldest first)."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM history WHERE ts >= ? ORDER BY id", [since_iso]).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["details"] = json.loads(d["details"]) if d.get("details") else None
            out.append(d)
        return out

    # ---------- cycle runs ----------

    def start_run(self, trigger: str, dry_run: bool) -> int:
        with self._tx() as db:
            cur = db.execute("INSERT INTO runs(started_at, status, trigger, dry_run) VALUES(?, 'running', ?, ?)",
                             (_iso(utcnow()), trigger, 1 if dry_run else 0))
            return cur.lastrowid

    def finish_run(self, run_id: int, status: str, summary: Optional[dict] = None, error: str = ""):
        with self._tx() as db:
            db.execute("UPDATE runs SET finished_at=?, status=?, summary=?, error=? WHERE id=?",
                       (_iso(utcnow()), status, json.dumps(summary, ensure_ascii=False) if summary else None,
                        error or None, run_id))

    @staticmethod
    def _run_row(r) -> dict:
        d = dict(r)
        d["dry_run"] = bool(d["dry_run"])
        d["summary"] = json.loads(d["summary"]) if d.get("summary") else None
        return d

    def list_runs(self, limit: int = 30) -> list:
        with self._lock:
            rows = self._db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [self._run_row(r) for r in rows]

    def last_run(self) -> Optional[dict]:
        runs = self.list_runs(1)
        return runs[0] if runs else None

    # ---------- dashboard sessions ----------

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_session(self, user_id: str, user_name: str, is_admin: bool, ttl_days: int = 14) -> str:
        token = secrets.token_urlsafe(32)
        now = utcnow()
        with self._tx() as db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(now),))
            db.execute("INSERT INTO sessions(token_hash, user_id, user_name, is_admin, created_at, expires_at) "
                       "VALUES(?,?,?,?,?,?)", (self._hash_token(token), user_id, user_name, 1 if is_admin else 0,
                                               _iso(now), _iso(now + timedelta(days=ttl_days))))
        return token

    def get_session(self, token: str) -> Optional[dict]:
        if not token:
            return None
        with self._lock:
            row = self._db.execute("SELECT * FROM sessions WHERE token_hash=?", (self._hash_token(token),)).fetchone()
        if not row:
            return None
        if row["expires_at"] < _iso(utcnow()):
            self.delete_session(token)
            return None
        return {"user_id": row["user_id"], "user_name": row["user_name"], "is_admin": bool(row["is_admin"])}

    def delete_session(self, token: str):
        with self._tx() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (self._hash_token(token),))
