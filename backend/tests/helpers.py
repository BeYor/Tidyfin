"""Shared test helpers: in-memory fakes plus tiny fake HTTP servers."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from tidyfin.devtools.fakes import FakeArr, FakeFactory, FakeJellyfin, Resp, iso, make_jpeg  # noqa: F401

VALID_KEY = "serverkey"


class _Server:
    """Context manager that serves ``handler_cls`` on a random local port."""

    def __init__(self, handler_cls):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def _reply(h, code=200, body=None):
    payload = json.dumps(body).encode() if body is not None else b""
    h.send_response(code)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(payload)))
    h.end_headers()
    h.wfile.write(payload)


class JellyfinState:
    def __init__(self):
        self.keys, self.movies, self.collection_calls, self.last_played = [], [], [], {}
        self.items_by_id, self.resume, self.item_updates = {}, {}, []
        self.episode_plays, self.episodes_fail = {}, False      # (series, user, filter) -> date


def jellyfin_server(state: JellyfinState):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _token(self):
            auth = self.headers.get("Authorization", "")
            if 'Token="' in auth:
                return auth.split('Token="', 1)[1].split('"', 1)[0]
            return self.headers.get("X-Emby-Token") or ""

        def _ok(self):
            tok = self._token()
            return tok in (VALID_KEY, "admin-token") or any(k["AccessToken"] == tok for k in state.keys)

        def do_GET(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path == "/System/Info/Public":
                return _reply(self, 200, {"ServerName": "Test", "Version": "10.9.0"})
            if u.path == "/System/Info":
                return _reply(self, 200, {"ServerName": "Test", "Version": "10.9.0"}) if self._ok() else _reply(self, 401)
            if not self._ok():
                return _reply(self, 401)
            if u.path == "/Auth/Keys":
                return _reply(self, 200, {"Items": state.keys})
            if u.path == "/Users":
                return _reply(self, 200, [{"Id": "u1", "Name": "alice", "Policy": {"IsAdministrator": True}},
                                          {"Id": "u2", "Name": "bob", "Policy": {"IsAdministrator": False}}])
            if u.path.startswith("/UserItems/") and u.path.endswith("/UserData"):
                lp = state.last_played.get((u.path.split("/")[2], q.get("userId", [""])[0]))
                return _reply(self, 200, {"LastPlayedDate": lp} if lp else {})
            if u.path == "/UserItems/Resume":
                return _reply(self, 200, {"Items": state.resume.get(q.get("userId", [""])[0], [])})
            if u.path.startswith("/Items/") and u.path.count("/") == 2:
                item = state.items_by_id.get(u.path.split("/")[2])
                return _reply(self, 200, item) if item else _reply(self, 404)
            if u.path == "/Items":
                if "Ids" in q:
                    return _reply(self, 200, {"Items": [state.items_by_id[i] for i in q["Ids"][0].split(",") if i in state.items_by_id]})
                if "ParentId" in q and q.get("IncludeItemTypes") == ["Episode"]:
                    if state.episodes_fail:
                        return _reply(self, 500)
                    hit = state.episode_plays.get((q["ParentId"][0], q.get("userId", [""])[0], q.get("Filters", [""])[0]))
                    return _reply(self, 200, {"Items": [{"UserData": {"LastPlayedDate": hit}}] if hit else []})
                if q.get("IncludeItemTypes") == ["Movie"]:
                    return _reply(self, 200, {"Items": state.movies})
                if q.get("IncludeItemTypes") == ["BoxSet"]:
                    return _reply(self, 200, {"Items": [{"Id": "box1", "Name": "Leaving soon - movies"}]})
                return _reply(self, 200, {"Items": []})
            _reply(self, 404)

        def do_POST(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if u.path == "/Users/AuthenticateByName":
                body = json.loads(raw or b"{}")
                hit = {("admin", "pass"): ("u1", True), ("bob", "pass"): ("u2", False)}.get((body.get("Username"), body.get("Pw")))
                if not hit:
                    return _reply(self, 401)
                return _reply(self, 200, {"AccessToken": "admin-token" if hit[1] else "bob-token",
                                          "User": {"Id": hit[0], "Name": body["Username"], "Policy": {"IsAdministrator": hit[1]}}})
            if not self._ok():
                return _reply(self, 401)
            if u.path == "/Auth/Keys":
                state.keys.append({"AppName": q["app"][0], "AccessToken": "generated-key", "DateCreated": "2026-10-26T00:00:00"})
                return _reply(self, 204)
            if u.path.startswith("/Collections/"):
                state.collection_calls.append(("add", u.path, q.get("ids", [""])[0]))
                return _reply(self, 204)
            if u.path == "/Collections":
                return _reply(self, 200, {"Id": "new-collection"})
            if u.path.startswith("/Items/") and u.path.count("/") == 2:
                state.item_updates.append((u.path.split("/")[2], json.loads(raw or b"{}")))
                return _reply(self, 204)
            _reply(self, 404)

        def do_DELETE(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path.startswith("/Collections/") and self._ok():
                state.collection_calls.append(("remove", u.path, q.get("ids", [""])[0]))
                return _reply(self, 204)
            _reply(self, 401 if not self._ok() else 404)

    return H


class ArrState:
    def __init__(self, editor=True):
        self.tags = [{"id": 1, "label": "keep"}]
        self.items = {7: {"id": 7, "title": "Film", "tags": []}}
        self.calls, self.editor = [], editor


def arr_server(state: ArrState, kind="radarr"):
    resource, ids_key = ("movie", "movieIds") if kind == "radarr" else ("series", "seriesIds")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _auth(self):
            return self.headers.get("X-Api-Key") == "k"

        def do_GET(self):
            if not self._auth():
                return _reply(self, 401)
            p = urlparse(self.path).path
            if p == "/api/v3/system/status":
                return _reply(self, 200, {"appName": kind.capitalize(), "version": "5.2.0"})
            if p == "/api/v3/tag":
                return _reply(self, 200, state.tags)
            if p.startswith(f"/api/v3/{resource}/"):
                return _reply(self, 200, state.items[int(p.rsplit("/", 1)[1])])
            _reply(self, 404)

        def _body(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")

        def do_POST(self):
            if not self._auth():
                return _reply(self, 401)
            if urlparse(self.path).path == "/api/v3/tag":
                body = self._body()
                tag = {"id": max(t["id"] for t in state.tags) + 1, "label": body["label"]}
                state.tags.append(tag)
                state.calls.append(("create_tag", body))
                return _reply(self, 201, tag)
            _reply(self, 404)

        def do_PUT(self):
            if not self._auth():
                return _reply(self, 401)
            p, body = urlparse(self.path).path, self._body()
            if p == f"/api/v3/{resource}/editor":
                if not state.editor:
                    return _reply(self, 404)
                state.calls.append(("editor", body))
                for i in body[ids_key]:
                    cur = set(state.items[i]["tags"])
                    state.items[i]["tags"] = sorted(cur | set(body["tags"]) if body["applyTags"] == "add" else cur - set(body["tags"]))
                return _reply(self, 202, [])
            if p.startswith(f"/api/v3/{resource}/"):
                state.calls.append(("put_item", body))
                state.items[int(p.rsplit("/", 1)[1])] = body
                return _reply(self, 202, body)
            _reply(self, 404)

    return H


def webhook_server(received: list, status=200):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            received.append({"path": self.path, "headers": dict(self.headers), "body": raw})
            _reply(self, status, {})

    return H


def http_server(handler_cls):
    return _Server(handler_cls)
