"""Local-only FastAPI application for the Web GUI foundation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR: Final[Path] = Path(__file__).resolve().parent / "static"
INDEX_FILE: Final[Path] = STATIC_DIR / "index.html"
CONTENT_SECURITY_POLICY: Final[str] = (
    "default-src 'self'; "
    "base-uri 'none'; "
    "connect-src 'none'; "
    "font-src 'self'; "
    "form-action 'none'; "
    "frame-ancestors 'none'; "
    "img-src 'self'; "
    "object-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'"
)


def create_app() -> FastAPI:
    """Создать Web GUI shell без конфигурации, сети или application side effects."""

    app = FastAPI(
        title="Second Brain",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def add_security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        return response

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(INDEX_FILE, media_type="text/html")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> JSONResponse:
        return JSONResponse(content={"status": "ok"})

    app.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR, html=False, check_dir=True),
        name="static",
    )
    return app
