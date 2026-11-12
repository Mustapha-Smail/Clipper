"""The HTTP API. Versioned under /api/v1; interactive docs at /api/docs."""

import logging
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text

from .. import config
from ..db import session_scope
from ..ffutil import ToolError
from ..log import configure
from . import campaigns, ideas, routes, scripts, storyboards, videos, voice

access = logging.getLogger("clipper.api.access")
# Polled every second or so by the UI; logging each one would drown everything else.
QUIET_PATHS = ("/api/v1/jobs", "/api/v1/system/status", "/healthz")


def create_app() -> FastAPI:
    configure("api")
    app = FastAPI(title="Clipper API", version=config.VERSION,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            access.exception("unhandled error", extra={
                "request_id": request_id, "method": request.method, "path": request.url.path})
            return JSONResponse({"detail": "Internal error. See the server log.", "request_id": request_id},
                                status_code=500, headers={"X-Request-ID": request_id})
        path = request.url.path
        quiet = request.method == "GET" and path.startswith(QUIET_PATHS) and response.status_code < 400
        if path.startswith("/api") or response.status_code >= 400:
            access.log(logging.DEBUG if quiet else logging.INFO, f"{request.method} {path}", extra={
                "request_id": request_id, "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(ToolError)
    async def tool_error(_: Request, err: ToolError):
        return JSONResponse({"detail": str(err)}, status_code=400)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        """Liveness and database reachability, for container health checks."""
        with session_scope() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok"}

    for router in (videos.router, campaigns.router, ideas.router, scripts.router, voice.router, storyboards.router,
                   routes.clips,
                   routes.jobs_router, routes.activity, routes.settings_router, routes.system):
        app.include_router(router, prefix="/api/v1")

    _serve_frontend(app)
    return app


def _serve_frontend(app: FastAPI) -> None:
    """Serve the built Angular app when it is on disk (native runs).

    In Docker a separate nginx container serves it and proxies /api here.
    """
    dist = config.frontend_dir()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Not found")
        index = dist / "index.html"
        if not index.is_file():
            return JSONResponse({
                "detail": "The API is running, but the web UI has not been built. "
                          "Run `npm run build` in frontend/, or use `npm start` for development.",
                "docs": "/api/docs"}, status_code=404)
        file = (dist / path).resolve()
        if path and file.is_file() and dist.resolve() in file.parents:
            return FileResponse(file)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
