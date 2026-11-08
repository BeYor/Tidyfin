"""
Framework-independent HTTP routing.

The Router handles requests and returns responses.
Both production and development servers use it, ensuring consistent routing, authentication, and error handling.
"""
from __future__ import annotations

import json
import queue
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from . import __version__
from .errors import AppError, BadRequest
from .logbus import bus, log
from .services import TidyfinApp

COOKIE = "tidyfin_session"
COOKIE_MAX_AGE = 14 * 86400


@dataclass
class Request:
    method: str
    path: str
    query: dict = field(default_factory=dict)
    raw_body: bytes = b""
    cookies: dict = field(default_factory=dict)
    client_ip: str = ""

    def json(self) -> dict:
        if not self.raw_body:
            return {}
        try:
            data = json.loads(self.raw_body)
        except (ValueError, UnicodeDecodeError) as e:
            raise BadRequest("Request body is not valid JSON.", code="bad_json") from e
        if not isinstance(data, dict):
            raise BadRequest("Request body must be a JSON object.", code="bad_json")
        return data

    def int_arg(self, name: str, default: int) -> int:
        try:
            return int(self.query.get(name, default))
        except (TypeError, ValueError):
            return default


@dataclass
class Response:
    status: int = 200
    body: Any = None                      # JSON-serializable value
    raw: Optional[bytes] = None           # binary payload (images); wins over ``body``
    content_type: str = "application/json"
    headers: dict = field(default_factory=dict)
    set_cookie: Optional[tuple] = None    # (name, value, max_age)
    delete_cookie: Optional[str] = None
    stream: Optional[Iterable] = None     # server-sent events


def _pattern(path: str) -> "re.Pattern":
    return re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "$")


class Router:
    def __init__(self, tidy: TidyfinApp):
        self.tidy = tidy
        self._routes: list = []
        self._register()

    def add(self, method: str, path: str, handler: Callable, public: bool = False):
        self._routes.append((method, _pattern(path), handler, public))

    # ---------- dispatch ----------

    def dispatch(self, req: Request) -> Response:
        try:
            path_matched = False
            for method, pattern, handler, public in self._routes:
                m = pattern.match(req.path)
                if not m:
                    continue
                path_matched = True
                if method != req.method:
                    continue
                if not public:
                    user = self.tidy.session_user(req.cookies.get(COOKIE))
                    if not user:
                        return Response(401, {"detail": "Sign in required.", "code": "unauthorized"})
                result = handler(self.tidy, req, **m.groupdict())
                return result if isinstance(result, Response) else Response(200, result)
            if path_matched:
                return Response(405, {"detail": "Method not allowed.", "code": "method_not_allowed"})
            return Response(404, {"detail": "Not found.", "code": "not_found"})
        except AppError as e:
            return Response(e.status, {"detail": str(e), "code": e.code})
        except Exception as e:      # never leak internals to the client
            log.exception("Unhandled error in %s %s: %s", req.method, req.path, e)
            return Response(500, {"detail": "Internal server error.", "code": "internal_error"})

    # ---------- route table ----------

    def _register(self):
        a = self.add

        # public
        a("GET", "/api/health", lambda t, r: {"ok": True, "version": __version__, "setup_complete": t.settings().setup_complete}, public=True)
        a("GET", "/api/options", lambda t, r: t.options(), public=True)
        a("POST", "/api/setup/jellyfin", _setup, public=True)
        a("POST", "/api/auth/login", _login, public=True)
        a("POST", "/api/auth/logout", _logout, public=True)

        # session / status
        a("GET", "/api/auth/me", lambda t, r: {"user": t.session_user(r.cookies.get(COOKIE))})
        a("GET", "/api/status", lambda t, r: t.status())

        # settings
        a("GET", "/api/settings", lambda t, r: t.public_settings())
        a("PUT", "/api/settings", lambda t, r: t.update_settings(r.json()))
        a("GET", "/api/settings/export", lambda t, r: t.export_settings())
        a("POST", "/api/settings/import", lambda t, r: t.import_settings(r.json()))
        a("POST", "/api/test/{service}", lambda t, r, service: t.test_connection(service, r.json().get("url", ""), r.json().get("api_key", "")))
        a("GET", "/api/jellyfin/users", lambda t, r: t.jellyfin_users())
        a("POST", "/api/notify/test", lambda t, r: t.notify_test())

        # cycle control
        a("POST", "/api/run", _run)
        a("POST", "/api/preview", lambda t, r: t.preview(r.json() or None))

        # queue / library / item actions
        a("GET", "/api/queue", lambda t, r: t.queue())
        a("GET", "/api/library", _library)
        a("GET", "/api/protected", lambda t, r: t.protected())
        a("POST", "/api/items/bulk", lambda t, r: t.bulk(r.json().get("action", ""), r.json().get("keys"), r.json().get("params")))
        a("GET", "/api/items/{key}", lambda t, r, key: t.item_detail(key))
        a("POST", "/api/items/{key}/protect", lambda t, r, key: t.protect(key, (r.json() or {}).get("days"), (r.json() or {}).get("date")))
        a("DELETE", "/api/items/{key}/protect", lambda t, r, key: t.unprotect(key))
        a("POST", "/api/items/{key}/schedule", lambda t, r, key: t.schedule(key, r.json().get("days"), r.json().get("date")))
        a("DELETE", "/api/items/{key}/schedule", lambda t, r, key: t.unschedule(key))
        a("POST", "/api/items/{key}/postpone", lambda t, r, key: t.postpone(key, r.json().get("days"), r.json().get("date")))

        a("POST", "/api/items/{key}/language-tags", lambda t, r, key: t.set_language_tags(key, r.json().get("profile", ""), r.json().get("kinds") or [], bool(r.json().get("dismiss"))))

        # summary
        a("GET", "/api/summary", lambda t, r: t.summary(r.int_arg("days", 30)))

        # history, logs, language report
        a("GET", "/api/history", lambda t, r: t.store.list_history(min(max(r.int_arg("limit", 100), 1), 500), max(r.int_arg("offset", 0), 0), r.query.get("event") or None))
        a("GET", "/api/runs", lambda t, r: t.store.list_runs(min(max(r.int_arg("limit", 30), 1), 200)))
        a("GET", "/api/language/report", lambda t, r: t.store.get_json("language_report") or {"labeled": 0, "by_profile": {}, "uncertain": []})
        a("GET", "/api/logs", lambda t, r: bus.recent(min(max(r.int_arg("limit", 200), 1), 2000), r.int_arg("after", 0)))
        a("GET", "/api/logs/stream", _logs_stream)

        # images
        a("GET", "/api/poster/{key}", lambda t, r, key: Response(200, raw=t.poster(key, r.int_arg("w", 300)), content_type="image/jpeg",
                                                                   headers={"Cache-Control": "private, max-age=600"}))
        a("POST", "/api/badges/preview", lambda t, r: Response(200, raw=t.badge_preview(r.json().get("badges"), r.int_arg("w", 360), r.query.get("key", "")),
                                                                content_type="image/jpeg", headers={"Cache-Control": "no-store"}))
        a("POST", "/api/posters/restore-all", lambda t, r: t.restore_all_posters())


def _setup(t: TidyfinApp, r: Request):
    b = r.json()
    result = t.setup_jellyfin(b.get("url", ""), b.get("username", ""), b.get("password", ""), b.get("api_key", ""),
                              b.get("locale", "en"), b.get("languages"))
    return Response(200, {"user": result["user"]}, set_cookie=(COOKIE, result["token"], COOKIE_MAX_AGE))


def _login(t: TidyfinApp, r: Request):
    b = r.json()
    result = t.login(b.get("username", ""), b.get("password", ""), r.client_ip)
    return Response(200, {"user": result["user"]}, set_cookie=(COOKIE, result["token"], COOKIE_MAX_AGE))


def _logout(t: TidyfinApp, r: Request):
    t.logout(r.cookies.get(COOKIE))
    return Response(200, {"ok": True}, delete_cookie=COOKIE)


def _run(t: TidyfinApp, r: Request):
    if not t.scheduler.trigger(r.json().get("dry_run")):
        return Response(409, {"detail": "A cycle is already running.", "code": "already_running"})
    return {"started": True}


def _library(t: TidyfinApp, r: Request):
    q = r.query
    return t.library(q.get("type") or None, q.get("q", ""), q.get("status", "all"), q.get("sort", "title"),
                     q.get("order") == "desc", r.int_arg("limit", 60), r.int_arg("offset", 0))


def _logs_stream(t: TidyfinApp, r: Request):
    q = bus.subscribe()
    after = r.int_arg("after", 0)

    def gen():
        last_id = after
        try:
            for e in bus.recent(200, after):
                last_id = max(last_id, e["id"])
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
            while True:
                try:
                    e = q.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                if e["id"] <= last_id:
                    continue
                last_id = e["id"]
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
        finally:
            bus.unsubscribe(q)

    return Response(200, content_type="text/event-stream", stream=gen(),
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
