"""Local-only FastAPI application for Web draft review and explicit Save."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from difflib import unified_diff
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StrictStr
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.application.llm import MAX_CONTEXT_BYTES, NoteDraft, validate_note_draft
from second_brain.application.ports import LlmError, ResearchError
from second_brain.application.research import SourceProvenance
from second_brain.application.writes import (
    CreateManagedNoteResult,
    CreateNotePlan,
    CreateStatus,
    WriteSafetyError,
)
from second_brain.config import ConfigurationError
from second_brain.domain.models import NoteType

from .drafts import DraftService, build_production_draft_service
from .preview import (
    PreviewContentTooLargeError,
    PreviewInputError,
    PreviewRenderError,
    render_safe_markdown,
)
from .review import ReviewTokenClaims, ReviewTokenCodec, ReviewTokenError, ReviewTokenMode
from .saves import DraftSaveService, build_production_save_service

STATIC_DIR: Final[Path] = Path(__file__).resolve().parent / "static"
INDEX_FILE: Final[Path] = STATIC_DIR / "index.html"
DRAFT_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
DRAFT_REQUEST_HEADER_VALUE: Final[str] = "draft-v1"
MAX_RAW_DRAFT_BODY_BYTES: Final[int] = 512 * 1024
_DRAFT_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/api/drafts/text",
        "/api/drafts/url",
        "/api/drafts/preview",
        "/api/drafts/save/prepare",
        "/api/drafts/save/apply",
    }
)
_REVIEW_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/api/drafts/preview",
        "/api/drafts/save/prepare",
        "/api/drafts/save/apply",
    }
)
_SAVE_PREFLIGHT_CODES: Final[frozenset[str]] = frozenset(
    {
        "CREATE_INVALID_PLAN",
        "CREATE_INVALID_TIMESTAMP",
        "CREATE_INVALID_TITLE",
        "CREATE_LINKED_PATH",
        "CREATE_PATH_ESCAPE",
        "CREATE_PLAN_FAILED",
        "CREATE_PREFLIGHT_FAILED",
        "CREATE_ROOT_MISSING",
        "CREATE_ROOT_NOT_DIRECTORY",
        "CREATE_TEMPLATE_INVALID",
        "CREATE_TEMPLATE_MISSING",
        "CREATE_TEMPLATE_READ_FAILED",
        "CREATE_UNSUPPORTED_TYPE",
        "CREATE_TARGET_CHECK_FAILED",
    }
)
_TRUSTED_LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "localhost"})
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
    """API response carrying a draft and a server-issued review token."""

    model_config = ConfigDict(extra="forbid", strict=True)

    draft: DraftPayload
    sources: list[SourceProvenancePayload]
    review_token: StrictStr


class PreviewDraftRequest(BaseModel):
    """Strict JSON request for safe Markdown preview."""

    model_config = ConfigDict(extra="forbid", strict=True)

    content: StrictStr


class PrepareDraftRequest(BaseModel):
    """Strict JSON request for the first, dry-run-only Save phase."""

    model_config = ConfigDict(extra="forbid", strict=True)

    review_token: StrictStr
    draft: DraftPayload


class ApplyDraftRequest(BaseModel):
    """Strict JSON request for the confirmed Safe Write apply phase."""

    model_config = ConfigDict(extra="forbid", strict=True)

    review_token: StrictStr
    confirmation_token: StrictStr
    draft: DraftPayload


class PreviewResponse(BaseModel):
    """Safe server-rendered HTML for the dedicated preview container."""

    model_config = ConfigDict(extra="forbid", strict=True)

    html: StrictStr


class DryRunNotePayload(BaseModel):
    """Safe application-owned note fields exposed by the dry-run plan."""

    model_config = ConfigDict(extra="forbid", strict=True)

    type: StrictStr
    relative_path: StrictStr


class PrepareResponse(BaseModel):
    """Safe full-file dry-run diff and stateless apply confirmation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: StrictStr
    note: DryRunNotePayload
    diff: StrictStr
    confirmation_token: StrictStr


class SavedNotePayload(BaseModel):
    """Safe relative result of one successful managed note creation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: StrictStr
    type: StrictStr
    created: datetime
    relative_path: StrictStr


class SaveResponse(BaseModel):
    """Minimal success envelope with no receipt, absolute path, or content."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: StrictStr
    note: SavedNotePayload


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
    "DRAFT_INVALID_REQUEST": (400, "draft request failed validation"),
    "DRAFT_CONTENT_TOO_LARGE": (413, "draft content is too large"),
    "REVIEW_TOKEN_INVALID": (400, "review token is invalid or expired"),
    "SAVE_CONFIRMATION_INVALID": (400, "save confirmation is invalid or expired"),
    "DRAFT_SCHEMA_INVALID": (400, "edited draft failed validation"),
    "PREVIEW_FAILED": (500, "safe Markdown preview failed"),
    "VAULT_UNAVAILABLE": (503, "vault is unavailable for saving"),
    "CREATE_TARGET_EXISTS": (409, "note target already exists"),
    "SAVE_PREFLIGHT_CONFLICT": (409, "vault preflight rejected the save"),
    "SAVE_ROLLED_BACK": (500, "save was rolled back; no note was published"),
    "SAVE_RECOVERY_REQUIRED": (500, "save recovery requires manual vault verification"),
    "SAVE_FAILED": (500, "save failed"),
}
_GENERIC_ERROR: Final[tuple[int, str, str]] = (
    500,
    "DRAFT_GENERATION_FAILED",
    "draft generation failed",
)


class DraftRequestBoundaryMiddleware:
    """Scoped ASGI boundary для draft headers, Origin и raw request body."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        """Сохранить единственный bounded cap без generic HTTP framework."""

        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Проверить draft request до FastAPI JSON/Pydantic parsing."""

        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in _DRAFT_PATHS
        ):
            await self.app(scope, receive, send)
            return

        path = str(scope["path"])
        invalid_code = _invalid_request_code(path)
        trusted_host = _trusted_request_host(scope)
        content_type_present, content_type = _single_header(scope, "content-type")
        request_header_present, request_header = _single_header(scope, DRAFT_REQUEST_HEADER_NAME)
        origin_present, origin = _single_header(scope, "origin")
        content_length_present, content_length = _single_header(scope, "content-length")

        if (
            not content_type_present
            or content_type is None
            or not _is_json_content_type(content_type)
            or not request_header_present
            or request_header != DRAFT_REQUEST_HEADER_VALUE
            or not trusted_host
            or (origin_present and (origin is None or not _origin_matches(scope, origin)))
        ):
            await _send_boundary_error(scope, receive, send, invalid_code)
            return

        if content_length_present:
            if content_length is None:
                await _send_boundary_error(scope, receive, send, invalid_code)
                return
            try:
                declared_length = int(content_length.strip())
            except ValueError:
                await _send_boundary_error(scope, receive, send, invalid_code)
                return
            if declared_length < 0:
                await _send_boundary_error(scope, receive, send, invalid_code)
                return
            if declared_length > self.max_body_bytes:
                await _send_boundary_error(
                    scope,
                    receive,
                    send,
                    _content_too_large_code(path),
                )
                return

        received_bytes = 0
        body_messages: list[Message] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                body_messages.append(message)
                break
            body = message.get("body", b"")
            if not isinstance(body, bytes):
                await _send_boundary_error(
                    scope,
                    receive,
                    send,
                    _content_too_large_code(path),
                )
                return
            received_bytes += len(body)
            if received_bytes > self.max_body_bytes:
                await _send_boundary_error(
                    scope,
                    receive,
                    send,
                    _content_too_large_code(path),
                )
                return
            body_messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay_receive() -> Message:
            if body_messages:
                return body_messages.pop(0)
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)


async def _send_boundary_error(
    scope: Scope,
    receive: Receive,
    send: Send,
    code: str,
) -> None:
    """Отправить safe JSON boundary error без чтения request body."""

    await _error_response(code)(scope, receive, send)


def _single_header(scope: Scope, name: str) -> tuple[bool, str | None]:
    """Получить только один header и отклонить duplicate ambiguity."""

    normalized_name = name.casefold().encode("ascii")
    values = [
        value.decode("latin-1")
        for header_name, value in scope.get("headers", [])
        if header_name.lower() == normalized_name
    ]
    if not values:
        return False, None
    if len(values) != 1:
        return True, None
    return True, values[0]


def _is_json_content_type(value: str) -> bool:
    """Разрешить только application/json и optional UTF-8 charset parameter."""

    parts = value.split(";")
    if parts[0].strip().casefold() != "application/json":
        return False
    if len(parts) == 1:
        return True
    if len(parts) != 2:
        return False
    name, separator, parameter_value = parts[1].partition("=")
    if separator == "" or name.strip().casefold() != "charset":
        return False
    normalized_value = parameter_value.strip()
    if len(normalized_value) >= 2 and normalized_value[0] == normalized_value[-1] == '"':
        normalized_value = normalized_value[1:-1]
    return normalized_value.casefold() == "utf-8"


def _origin_matches(scope: Scope, origin: str) -> bool:
    """Разрешить только same-origin loopback Origin с тем же Host/port."""

    scheme = str(scope.get("scheme", "")).casefold()
    if scheme not in {"http", "https"}:
        return False
    try:
        parsed_origin = urlsplit(origin)
    except ValueError:
        return False
    if (
        parsed_origin.scheme.casefold() != scheme
        or not parsed_origin.netloc
        or parsed_origin.path
        or parsed_origin.query
        or parsed_origin.fragment
        or parsed_origin.username is not None
        or parsed_origin.password is not None
    ):
        return False
    host_header_present, host_header = _single_header(scope, "host")
    if not host_header_present or host_header is None:
        return False
    origin_host = _loopback_host_port(parsed_origin.netloc, scheme)
    request_host = _loopback_host_port(host_header, scheme)
    return origin_host is not None and origin_host == request_host


def _trusted_request_host(scope: Scope) -> bool:
    """Разрешить только trusted loopback Host для draft boundary."""

    scheme = str(scope.get("scheme", "")).casefold()
    if scheme not in {"http", "https"}:
        return False
    host_present, host = _single_header(scope, "host")
    return host_present and host is not None and _loopback_host_port(host, scheme) is not None


def _loopback_host_port(value: str, scheme: str) -> tuple[str, int] | None:
    """Распознать только localhost/127.0.0.1 и нормализовать default port."""

    if not value or any(character.isspace() for character in value):
        return None
    try:
        parsed = urlsplit(f"//{value}")
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        hostname is None
        or hostname.casefold() not in _TRUSTED_LOOPBACK_HOSTS
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return hostname.casefold(), port


def _invalid_request_code(path: str) -> str:
    """Выбрать safe application code по draft route."""

    if path.endswith("/url"):
        return "RESEARCH_INVALID_REQUEST"
    if path in _REVIEW_PATHS:
        return "DRAFT_INVALID_REQUEST"
    return "LLM_INVALID_REQUEST"


def _content_too_large_code(path: str) -> str:
    """Выбрать bounded content-too-large code по draft route."""

    if path.endswith("/url"):
        return "RESEARCH_CONTENT_TOO_LARGE"
    if path in _REVIEW_PATHS:
        return "DRAFT_CONTENT_TOO_LARGE"
    return "LLM_CONTENT_TOO_LARGE"


def create_app(
    *,
    draft_service: DraftService | None = None,
    save_service: DraftSaveService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> FastAPI:
    """Создать Web GUI без config/vault side effects до explicit Save."""

    service = draft_service if draft_service is not None else build_production_draft_service()
    saver = (
        save_service
        if save_service is not None
        else build_production_save_service(
            env_file=env_file,
            vault_path_override=vault_path_override,
        )
    )
    review_tokens = ReviewTokenCodec()
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
    app.add_middleware(
        DraftRequestBoundaryMiddleware,
        max_body_bytes=MAX_RAW_DRAFT_BODY_BYTES,
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
        return _error_response(_invalid_request_code(request.url.path))

    @app.post("/api/drafts/text", include_in_schema=False)
    def text_draft(payload: TextDraftRequest) -> Response:
        """Создать один text draft без research или write side effects."""

        if not payload.text.strip():
            return _error_response("LLM_INVALID_REQUEST")
        if _text_is_too_large(payload.text):
            return _error_response("LLM_CONTENT_TOO_LARGE")
        try:
            draft = service.draft_text(payload.text)
            return _draft_response(draft=draft, source=None, review_tokens=review_tokens)
        except Exception as error:
            return _error_response_for_exception(error)

    @app.post("/api/drafts/url", include_in_schema=False)
    def url_draft(payload: UrlDraftRequest) -> Response:
        """Создать один public WEB research draft с bounded provenance."""

        if not payload.url.strip():
            return _error_response("RESEARCH_INVALID_REQUEST")
        try:
            result = service.draft_url(payload.url)
            return _draft_response(
                draft=result.draft,
                source=result.source,
                review_tokens=review_tokens,
            )
        except Exception as error:
            return _error_response_for_exception(error)

    @app.post("/api/drafts/preview", include_in_schema=False)
    def preview_draft(payload: PreviewDraftRequest) -> Response:
        """Render only the edited Markdown body without network, vault, or Git."""

        try:
            html = render_safe_markdown(payload.content)
        except PreviewContentTooLargeError:
            return _error_response("DRAFT_CONTENT_TOO_LARGE")
        except PreviewInputError:
            return _error_response("DRAFT_INVALID_REQUEST")
        except PreviewRenderError:
            return _error_response("PREVIEW_FAILED")
        except Exception:
            return _error_response("PREVIEW_FAILED")
        response = PreviewResponse(html=html)
        return JSONResponse(
            content=response.model_dump(mode="json"),
            headers=_DRAFT_ERROR_HEADERS,
        )

    @app.post("/api/drafts/save/prepare", include_in_schema=False)
    def prepare_save(payload: PrepareDraftRequest) -> Response:
        """Run Safe Write dry-run and return a reviewable full-file plan."""

        try:
            claims = review_tokens.verify(payload.review_token)
        except ReviewTokenError:
            return _error_response("REVIEW_TOKEN_INVALID")

        try:
            draft = _note_draft_from_payload(payload.draft)
        except LlmError, ValueError:
            return _error_response("DRAFT_SCHEMA_INVALID")

        try:
            result = _run_save_phase(saver, claims, draft, apply=False)
        except ConfigurationError, WriteSafetyError, OSError:
            return _error_response("VAULT_UNAVAILABLE")
        except Exception:
            return _error_response("SAVE_FAILED")
        return _prepare_response(
            result,
            review_token=payload.review_token,
            draft=draft,
            review_tokens=review_tokens,
            mode=claims.mode,
        )

    @app.post("/api/drafts/save/apply", include_in_schema=False)
    def apply_save(payload: ApplyDraftRequest) -> Response:
        """Require a matching dry-run confirmation before Safe Write apply."""

        try:
            claims = review_tokens.verify(payload.review_token)
        except ReviewTokenError:
            return _error_response("REVIEW_TOKEN_INVALID")

        try:
            draft = _note_draft_from_payload(payload.draft)
        except LlmError, ValueError:
            return _error_response("DRAFT_SCHEMA_INVALID")

        try:
            review_tokens.verify_confirmation(
                payload.confirmation_token,
                review_token=payload.review_token,
                draft=draft,
                mode=claims.mode,
            )
        except ReviewTokenError:
            return _error_response("SAVE_CONFIRMATION_INVALID")

        try:
            result = _run_save_phase(saver, claims, draft, apply=True)
        except ConfigurationError, WriteSafetyError, OSError:
            return _error_response("VAULT_UNAVAILABLE")
        except Exception:
            return _error_response("SAVE_FAILED")
        return _save_response(result)

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
    review_tokens: ReviewTokenCodec,
) -> JSONResponse:
    """Serialize only semantic draft/provenance and issue a review token."""

    sources = [] if source is None else [_source_payload(source)]
    review_token = (
        review_tokens.issue_text() if source is None else review_tokens.issue_research(source)
    )
    response = DraftResponse(
        draft=_draft_payload(draft),
        sources=sources,
        review_token=review_token,
    )
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


def _note_draft_from_payload(payload: DraftPayload) -> NoteDraft:
    """Convert exactly five editable fields into the existing semantic NoteDraft."""

    note_type = NoteType(payload.note_type)
    draft = NoteDraft(
        title=payload.title,
        note_type=note_type,
        content=payload.content,
        tags=tuple(payload.tags),
        links=tuple(payload.links),
    )
    return validate_note_draft(draft)


def _run_save_phase(
    saver: DraftSaveService,
    claims: ReviewTokenClaims,
    draft: NoteDraft,
    *,
    apply: bool,
) -> CreateManagedNoteResult:
    """Dispatch one server-selected phase without exposing an apply flag to HTTP."""

    mode = claims.mode
    if mode is ReviewTokenMode.TEXT:
        return saver.apply_text(draft) if apply else saver.prepare_text(draft)
    source = claims.source
    if source is None:
        raise ReviewTokenError()
    return saver.apply_research(draft, source) if apply else saver.prepare_research(draft, source)


def _prepare_response(
    result: CreateManagedNoteResult,
    *,
    review_token: str,
    draft: NoteDraft,
    review_tokens: ReviewTokenCodec,
    mode: ReviewTokenMode,
) -> JSONResponse:
    """Serialize only a successful dry-run plan and its stateless confirmation."""

    if result.status is CreateStatus.CREATED:
        return _error_response("SAVE_FAILED")
    if result.status is not CreateStatus.DRY_RUN or result.plan is None:
        return _save_response(result)
    relative_path = _safe_relative_path(result.plan.relative_path)
    if relative_path is None:
        return _error_response("SAVE_FAILED")
    try:
        diff = _plan_diff(result.plan, relative_path)
        confirmation_token = review_tokens.issue_confirmation(
            review_token=review_token,
            draft=draft,
            mode=mode,
        )
    except ReviewTokenError, TypeError, UnicodeError, ValueError:
        return _error_response("SAVE_FAILED")
    response = PrepareResponse(
        status=CreateStatus.DRY_RUN.value,
        note=DryRunNotePayload(
            type=result.plan.note_type.value,
            relative_path=relative_path,
        ),
        diff=diff,
        confirmation_token=confirmation_token,
    )
    return JSONResponse(
        content=response.model_dump(mode="json"),
        headers=_DRAFT_ERROR_HEADERS,
    )


def _plan_diff(plan: CreateNotePlan, relative_path: str) -> str:
    """Build a full proposed-file diff with no absolute filesystem identity."""

    if type(plan.content) is not str:
        raise TypeError("plan content must be text")
    return "".join(
        unified_diff(
            (),
            plan.content.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile=relative_path,
            lineterm="\n",
        )
    )


def _safe_relative_path(value: str) -> str | None:
    """Allow only the application-owned normalized relative POSIX path projection."""

    if type(value) is not str or not value or "\\" in value:
        return None
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or posix_path.as_posix() != value
        or any(part in {"", ".", ".."} for part in posix_path.parts)
    ):
        return None
    return value


def _save_response(result: CreateManagedNoteResult) -> JSONResponse:
    """Expose only safe success fields or a deterministic non-diagnostic error."""

    if result.status is CreateStatus.CREATED and result.plan is not None:
        relative_path = _safe_relative_path(result.plan.relative_path)
        if relative_path is None:
            return _error_response("SAVE_FAILED")
        response = SaveResponse(
            status=CreateStatus.CREATED.value,
            note=SavedNotePayload(
                id=str(result.plan.note_id),
                type=result.plan.note_type.value,
                created=result.plan.created,
                relative_path=relative_path,
            ),
        )
        return JSONResponse(
            content=response.model_dump(mode="json"),
            headers=_DRAFT_ERROR_HEADERS,
        )
    if result.status is CreateStatus.ROLLED_BACK:
        code = "SAVE_ROLLED_BACK" if result.rollback_succeeded is True else "SAVE_RECOVERY_REQUIRED"
        return _error_response(code)
    diagnostic_codes = {diagnostic.code for diagnostic in result.diagnostics}
    if "CREATE_TARGET_EXISTS" in diagnostic_codes:
        return _error_response("CREATE_TARGET_EXISTS")
    if "CREATE_VAULT_ROOT_INVALID" in diagnostic_codes:
        return _error_response("VAULT_UNAVAILABLE")
    if diagnostic_codes.intersection(_SAVE_PREFLIGHT_CODES):
        return _error_response("SAVE_PREFLIGHT_CONFLICT")
    return _error_response("SAVE_FAILED")


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
    "DRAFT_REQUEST_HEADER_NAME",
    "DRAFT_REQUEST_HEADER_VALUE",
    "MAX_RAW_DRAFT_BODY_BYTES",
    "ApplyDraftRequest",
    "DraftPayload",
    "DraftRequestBoundaryMiddleware",
    "DraftResponse",
    "DryRunNotePayload",
    "ErrorResponse",
    "PrepareDraftRequest",
    "PrepareResponse",
    "PreviewDraftRequest",
    "PreviewResponse",
    "SaveResponse",
    "SavedNotePayload",
    "SourceProvenancePayload",
    "TextDraftRequest",
    "UrlDraftRequest",
    "create_app",
]
