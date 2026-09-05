"""Local-only FastAPI application for the read-only Web GUI."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Final

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StrictStr
from starlette.middleware.trustedhost import TrustedHostMiddleware

from second_brain.application.llm import MAX_CONTEXT_BYTES, NoteDraft
from second_brain.application.ports import LlmError, ResearchError
from second_brain.application.research import SourceProvenance

from .drafts import DraftService, build_production_draft_service

STATIC_DIR: Final[Path] = Path(__file__).resolve().parent / "static"
INDEX_FILE: Final[Path] = STATIC_DIR / "index.html"
CONTENT_SECURITY_POLICY: Final[str] = (
    "default-src 'self'; "
    "base-uri 'none'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "form-action 'none'; "
    "frame-ancestors 'none'; "
    "img-src 'self'; "
    "object-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'"
)
_DRAFT_ERROR_HEADERS: Final[dict[str, str]] = {"Cache-Control": "no-store"}


class TextDraftRequest(BaseModel):
    """Strict JSON request for the text Add mode."""

    model_config = ConfigDict(extra="forbid", strict=True)

    text: StrictStr


class UrlDraftRequest(BaseModel):
    """Strict JSON request for the public WEB Add mode."""

    model_config = ConfigDict(extra="forbid", strict=True)

    url: StrictStr


class DraftPayload(BaseModel):
    """HTTP projection of the unchanged five-field ``NoteDraft``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: StrictStr
    note_type: StrictStr
    content: StrictStr
    tags: list[StrictStr]
    links: list[StrictStr]


class SourceProvenancePayload(BaseModel):
    """Bounded public projection without raw research or provider fields."""

    model_config = ConfigDict(extra="forbid", strict=True)

    uri: StrictStr
    kind: StrictStr
    retrieved_at: datetime
    published_at: datetime | None = None
    title: StrictStr | None = None
    author: StrictStr | None = None
    upstream_id: StrictStr | None = None


class DraftResponse(BaseModel):
    """Common read-only API response for both Add modes."""

    model_config = ConfigDict(extra="forbid", strict=True)

    draft: DraftPayload
    sources: list[SourceProvenancePayload]


class ErrorPayload(BaseModel):
    """Safe error object with no exception or upstream details."""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: StrictStr
    message: StrictStr


class ErrorResponse(BaseModel):
    """Stable error envelope for draft API failures."""

    model_config = ConfigDict(extra="forbid", strict=True)

    error: ErrorPayload


_ERRORS: Final[dict[str, tuple[int, str]]] = {
    "LLM_INVALID_REQUEST": (400, "draft request failed validation"),
    "LLM_CANCELLED": (504, "draft operation was cancelled"),
    "LLM_TIMEOUT": (504, "LLM draft operation timed out"),
    "LLM_BACKEND_UNAVAILABLE": (503, "LLM backend is unavailable"),
    "LLM_UPSTREAM_FAILURE": (502, "LLM backend failed"),
    "LLM_MALFORMED_RESULT": (502, "LLM backend returned an invalid draft"),
    "LLM_CONTENT_TOO_LARGE": (413, "draft content is too large"),
    "RESEARCH_INVALID_REQUEST": (400, "research request failed validation"),
    "RESEARCH_CANCELLED": (504, "draft operation was cancelled"),
    "RESEARCH_TIMEOUT": (504, "research backend timed out"),
    "RESEARCH_BACKEND_UNAVAILABLE": (503, "research backend is unavailable"),
    "RESEARCH_UPSTREAM_FAILURE": (502, "research backend failed"),
    "RESEARCH_MALFORMED_RESULT": (502, "research backend returned an invalid result"),
    "RESEARCH_CONTENT_TOO_LARGE": (413, "research content is too large"),
}
_GENERIC_ERROR: Final[tuple[int, str, str]] = (
    500,
    "DRAFT_GENERATION_FAILED",
    "draft generation failed",
)


def create_app(*, draft_service: DraftService | None = None) -> FastAPI:
    """Создать Web GUI без config/network side effects до draft POST."""

    service = draft_service if draft_service is not None else build_production_draft_service()
    app = FastAPI(
        title="Second Brain",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost"],
    )

    @app.middleware("http")
    async def add_security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Добавить security headers к shell, API и static responses."""

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/drafts/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        return response

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """Скрыть Pydantic details и использовать API error contract."""

        del exc
        code = (
            "RESEARCH_INVALID_REQUEST"
            if request.url.path == "/api/drafts/url"
            else "LLM_INVALID_REQUEST"
        )
        return _error_response(code)

    @app.post("/api/drafts/text", include_in_schema=False)
    def text_draft(payload: TextDraftRequest) -> Response:
        """Создать один text draft без research или write side effects."""

        if not payload.text.strip():
            return _error_response("LLM_INVALID_REQUEST")
        if _text_is_too_large(payload.text):
            return _error_response("LLM_CONTENT_TOO_LARGE")
        try:
            draft = service.draft_text(payload.text)
            return _draft_response(draft=draft, source=None)
        except Exception as error:
            return _error_response_for_exception(error)

    @app.post("/api/drafts/url", include_in_schema=False)
    def url_draft(payload: UrlDraftRequest) -> Response:
        """Создать один public WEB research draft с bounded provenance."""

        if not payload.url.strip():
            return _error_response("RESEARCH_INVALID_REQUEST")
        try:
            result = service.draft_url(payload.url)
            return _draft_response(draft=result.draft, source=result.source)
        except Exception as error:
            return _error_response_for_exception(error)

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


def _text_is_too_large(value: str) -> bool:
    """Отдельно классифицировать тот же bounded context limit как HTTP 413."""

    try:
        return len(value.encode("utf-8")) > MAX_CONTEXT_BYTES
    except UnicodeEncodeError:
        return False


def _draft_response(
    *,
    draft: NoteDraft,
    source: SourceProvenance | None,
) -> JSONResponse:
    """Сериализовать только validated NoteDraft и optional public provenance."""

    sources = [] if source is None else [_source_payload(source)]
    response = DraftResponse(draft=_draft_payload(draft), sources=sources)
    return JSONResponse(
        content=response.model_dump(mode="json", exclude_none=True),
        headers=_DRAFT_ERROR_HEADERS,
    )


def _draft_payload(draft: NoteDraft) -> DraftPayload:
    """Сохранить ровно пять semantic fields и не добавлять metadata."""

    return DraftPayload(
        title=draft.title,
        note_type=draft.note_type.value,
        content=draft.content,
        tags=list(draft.tags),
        links=list(draft.links),
    )


def _source_payload(source: SourceProvenance) -> SourceProvenancePayload:
    """Вывести только разрешённые SourceProvenance fields."""

    return SourceProvenancePayload(
        uri=source.uri,
        kind=source.source_kind.value,
        retrieved_at=source.retrieved_at,
        published_at=source.published_at,
        title=source.title,
        author=source.author,
        upstream_id=source.upstream_id,
    )


def _error_response(code: str) -> JSONResponse:
    """Собрать deterministic safe error без переданного exception message."""

    error_definition = _ERRORS.get(code)
    if error_definition is None:
        status_code, safe_code, message = _GENERIC_ERROR
    else:
        status_code, message = error_definition
        safe_code = code
    payload = ErrorResponse(error=ErrorPayload(code=safe_code, message=message))
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=_DRAFT_ERROR_HEADERS,
    )


def _error_response_for_exception(error: Exception) -> JSONResponse:
    """Сопоставить application taxonomy с HTTP без provider/upstream details."""

    if isinstance(error, (LlmError, ResearchError)):
        return _error_response(error.code)
    return _error_response(_GENERIC_ERROR[1])


__all__ = [
    "CONTENT_SECURITY_POLICY",
    "DraftPayload",
    "DraftResponse",
    "ErrorResponse",
    "SourceProvenancePayload",
    "TextDraftRequest",
    "UrlDraftRequest",
    "create_app",
]
