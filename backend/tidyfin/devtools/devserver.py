"""
Development server: the real Tidyfin application against fake upstreams.

    python -m tidyfin.devtools.devserver                # demo library, http://127.0.0.1:8585
    python -m tidyfin.devtools.devserver --empty        # fresh install (setup wizard)

Sign in with ``admin`` / ``pass``. The whole stack is real (routes, services,
cycle, SQLite, poster rendering); only Jellyfin, Radarr and Sonarr are
in-memory fakes seeded with fictional titles. Needs nothing but Pillow,
requests and PyYAML
"""
from __future__ import annotations

import argparse
import mimetypes
import random
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..logbus import setup_logging
from ..profiles import preset_profile
from ..routes import Request, Router
from ..services import TidyfinApp
from ..settings import apply_patch, save_settings
from .artwork import poster as art_poster
from .fakes import FakeFactory, iso

MOVIES = ["The Last Lighthouse", "Midnight Orchard", "Paper Moons", "Iron Harbor", "Summer of Static", "Glass Meridian",
          "Northern Lanterns", "The Quiet Engine", "Saffron Skies", "Clockwork Tides", "Velvet Horizon", "Ash and Ember",
          "Echoes of Marrow", "The Copper Sea", "Lunar Parade", "Wolfpine", "Tin Kingdom", "A Map of Salt", "Moth Season",
          "The Orange Hour"]
SERIES = ["Harbor Lights", "Signal Fires", "The Long Winter", "Kitchen Table", "Parallel Hours", "Stone and Sparrow",
          "Deep Frequency", "Neon Orchard"]

# Release-name / stream variety so the language detection has something to chew on.
_STREAMS = {
    0: dict(streams=[{"Type": "Audio", "Language": "pol", "Title": "Lektor PL"}, {"Type": "Subtitle", "Language": "pol"}]),
    1: dict(streams=[{"Type": "Audio", "Language": "pol", "Title": "Dubbing PL"}]),
    2: dict(streams=[{"Type": "Audio", "Language": "ger", "Title": "German"}, {"Type": "Subtitle", "Language": "eng"}]),
    3: dict(path="/media/movies/Iron Harbor [VF].mkv"),
    4: dict(path="/media/movies/Summer of Static PL 1080p.mkv"),
    5: dict(streams=[{"Type": "Subtitle", "Language": "pol"}]),
    6: dict(tags=["Lektor-PL"]),
    7: dict(streams=[{"Type": "Audio", "Language": "fre", "Title": "Français"}]),
}


def seed_demo(factory: FakeFactory):
    rnd = random.Random(11)
    jf, radarr, sonarr = factory.jf, factory.radarr, factory.sonarr
    for i, title in enumerate(MOVIES):
        jid, tmdb = f"jm{i}", 1000 + i
        extra = _STREAMS.get(i, {})
        item = jf.add_movie(tmdb, jid, title, streams=extra.get("streams"), path=extra.get("path") or f"/media/movies/{title}.mkv",
                            tags=extra.get("tags"))
        jf.posters[jid] = art_poster(title)
        age = [210, 190, 150, 130, 120, 95, 80, 70, 64, 58, 52, 49, 47, 30, 25, 12, 8, 5, 300, 400][i]
        radarr.add_movie(i + 1, tmdb, title, file_days_ago=age, size=rnd.randint(2, 38) * 10**9, year=2008 + i % 16,
                         tags=[1] if i in (9, 13) else [])
        if i in (4, 11, 16):
            jf.last_played[jid] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc) - __import__("datetime").timedelta(days=9)
    radarr._tags = {1: "do-not-delete"}
    for i, title in enumerate(SERIES):
        jid, tvdb = f"js{i}", 5000 + i
        jf.add_series(tvdb, jid, title)
        jf.posters[jid] = art_poster(title)
        sonarr.add_series(i + 1, tvdb, title, file_days_ago=[240, 160, 110, 90, 75, 40, 20, 300][i], size=rnd.randint(8, 90) * 10**9)
        jf.episodes[jid] = [{"MediaStreams": [{"Type": "Audio", "Language": "pol", "Title": "Lektor PL"}] if i == 1 else [],
                             "Path": f"/media/tv/{title}/S01E01.mkv"}]
    sonarr._tags = {1: "do-not-delete"}


def build(data_dir: str, empty: bool):
    factory = FakeFactory()
    seed_demo(factory)
    app = TidyfinApp(data_dir, factory)
    base = {"disk_path": "/", "cleanup_start_percent": 0, "disk_thresholds": [{"usage_percent": 90, "movies_days": 3, "series_days": 5}]}
    if empty:
        save_settings(app.store, apply_patch(app.settings(), base))
        return app
    patch = {**base, "jellyfin": {"url": "http://demo-jellyfin:8096", "api_key": "demo"},
             "radarr": {"url": "http://demo-radarr:7878", "api_key": "demo"},
             "sonarr": {"url": "http://demo-sonarr:8989", "api_key": "demo"}, "manual_user_id": "u-admin",
             "language": {"profiles": [preset_profile(c).to_dict() for c in ("pol", "ger", "fre")]},
             "movies": {"content_age_threshold_days": 45}, "series": {"content_age_threshold_days": 60}}
    settings = apply_patch(app.settings(), patch)
    settings.setup_complete = True
    save_settings(app.store, settings)
    return app


def stagger_demo_queue(app: TidyfinApp):
    """Spread the queue over time so the demo shows every stage (the first cycle
    gives every title the same deadline). Demo-only: rewrites queue dates."""
    from datetime import datetime, timedelta, timezone
    state = app.store.load_auto_state()
    now = datetime.now(timezone.utc)
    for i, key in enumerate(sorted(state)):
        left = [-0.2, 0.4, 1, 1, 2, 3, 4, 5, 6, 8, 9, 11, 12, 13][i % 14]
        end = (now + timedelta(days=left)).replace(hour=0, minute=0, second=0, microsecond=0) if left > 0 else now - timedelta(hours=5)
        state[key]["delete_at"] = end.isoformat()
        state[key]["first_detected"] = (end - timedelta(days=14)).isoformat()
    app.store.save_auto_state(state)


def make_handler(router: Router, frontend: Path):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _serve(self):
            u = urlparse(self.path)
            if u.path.startswith("/api/"):
                length = int(self.headers.get("Content-Length") or 0)
                cookies = {}
                for part in (self.headers.get("Cookie") or "").split(";"):
                    if "=" in part:
                        k, v = part.strip().split("=", 1)
                        cookies[k] = v
                req = Request(self.command, u.path, {k: v[0] for k, v in parse_qs(u.query).items()},
                              self.rfile.read(length) if length else b"", cookies, self.client_address[0])
                return self._write(router.dispatch(req))
            target = (frontend / u.path.lstrip("/")).resolve()
            if not str(target).startswith(str(frontend.resolve())) or not target.is_file():
                target = frontend / "index.html"
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _write(self, resp):
            import json
            if resp.stream is not None:
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.content_type)
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    for chunk in resp.stream:
                        self.wfile.write(chunk.encode("utf-8"))
                        self.wfile.flush()
                except OSError:
                    pass
                return
            payload = resp.raw if resp.raw is not None else json.dumps(resp.body, ensure_ascii=False).encode("utf-8")
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type if resp.raw is not None else "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            for k, v in resp.headers.items():
                self.send_header(k, v)
            if resp.set_cookie:
                self.send_header("Set-Cookie", f"{resp.set_cookie[0]}={resp.set_cookie[1]}; Path=/; HttpOnly; SameSite=Lax")
            if resp.delete_cookie:
                self.send_header("Set-Cookie", f"{resp.delete_cookie}=; Path=/; Max-Age=0")
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_DELETE = _serve

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8585)
    ap.add_argument("--empty", action="store_true", help="fresh install: starts with the setup wizard")
    ap.add_argument("--data", default="", help="data directory (default: a temp dir)")
    ap.add_argument("--frontend", default=str(Path(__file__).resolve().parents[3] / "frontend"))
    args = ap.parse_args()
    setup_logging()
    data = args.data or tempfile.mkdtemp(prefix="tidyfin-dev-")
    app = build(data, args.empty)
    if not args.empty:
        app.scheduler.run_once("startup")
        stagger_demo_queue(app)
    app.start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(Router(app), Path(args.frontend)))
    server.daemon_threads = True
    print(f"Tidyfin dev server: http://127.0.0.1:{args.port}  (login: admin / pass, data: {data})")
    server.serve_forever()


if __name__ == "__main__":
    main()
