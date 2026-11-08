"""Production web server: a thin Starlette adapter around routes.Router."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response as StarletteResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .logbus import setup_logging
from .routes import COOKIE_MAX_AGE, Request, Response, Router
from .services import TidyfinApp

_SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin"}


def _to_starlette(resp: Response) -> StarletteResponse:
    headers = {**_SECURITY_HEADERS, **resp.headers}
    if resp.stream is not None:
        out = StreamingResponse(resp.stream, status_code=resp.status, media_type=resp.content_type, headers=headers)
    elif resp.raw is not None:
        out = StarletteResponse(resp.raw, status_code=resp.status, media_type=resp.content_type, headers=headers)
    else:
        out = JSONResponse(resp.body, status_code=resp.status, headers=headers)
    if resp.set_cookie:
        name, value, max_age = resp.set_cookie
        out.set_cookie(name, value, max_age=max_age or COOKIE_MAX_AGE, httponly=True, samesite="lax", path="/")
    if resp.delete_cookie:
        out.delete_cookie(resp.delete_cookie, path="/")
    return out


def create_app(data_dir=None, tidy: Optional[TidyfinApp] = None) -> Starlette:
    setup_logging()
    tidy = tidy or TidyfinApp(data_dir or os.getenv("TIDYFIN_DATA_DIR", "/data"))
    router = Router(tidy)

    async def endpoint(request):
        req = Request(method="GET" if request.method == "HEAD" else request.method, path=request.url.path, query=dict(request.query_params),
                      raw_body=await request.body(), cookies=dict(request.cookies),
                      client_ip=request.client.host if request.client else "")
        # Handlers do blocking I/O (requests, SQLite, Pillow), so keep them off the event loop.
        return _to_starlette(await run_in_threadpool(router.dispatch, req))

    @asynccontextmanager
    async def lifespan(app):
        tidy.start()
        yield
        tidy.stop()

    routes = [Route("/api/{rest:path}", endpoint, methods=["GET", "POST", "PUT", "DELETE"])]
    static_dir = Path(os.getenv("TIDYFIN_STATIC_DIR", "/app/static"))
    if static_dir.is_dir():
        routes.append(Mount("/", app=StaticFiles(directory=str(static_dir), html=True), name="static"))
    return Starlette(routes=routes, lifespan=lifespan)
