"""Local-only FastAPI application for Web draft review and explicit Save."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from difflib import unified_diff
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final, cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.application.decision_journal import (
    DecisionJournalDraft,
    DecisionJournalDraftError,
    OutcomeObservationDraft,
    OutcomeObservationDraftError,
    render_decision_journal_body,
    render_outcome_observation_body,
    validate_decision_journal_draft,
    validate_outcome_observation_draft,
)
from second_brain.application.llm import MAX_CONTEXT_BYTES, NoteDraft, validate_note_draft
from second_brain.application.personal_memory import (
    PersonalMemoryDraft,
    PersonalMemoryDraftError,
    validate_personal_memory_draft,
)
from second_brain.application.ports import (
    LlmError,
    ResearchError,
    RetrievedNote,
    SearchError,
    SearchHit,
    SearchRequest,
    TranscriptionError,
)
from second_brain.application.research import SourceProvenance
from second_brain.application.self_model import (
    SelfModelError,
    SelfModelRequest,
    validate_self_model_request,
)
from second_brain.application.self_retrieval import (
    SelfContextRequest,
    SelfRetrievalError,
    validate_self_context_request,
)
from second_brain.application.simulate_me import SimulateMeError
from second_brain.application.timeline import (
    PersonalTimelineRequest,
    TimelineError,
    TimelineOrder,
    validate_personal_timeline_request,
)
from second_brain.application.transcription import (
    MAX_TRANSCRIPTION_AUDIO_BYTES,
    TranscriptionInvalidRequestError,
    normalize_audio_media_type,
    validate_transcript,
)
from second_brain.application.writes import (
    CreateManagedNoteResult,
    CreateNotePlan,
    CreateStatus,
    WriteSafetyError,
)
from second_brain.config import ConfigurationError
from second_brain.domain.models import NoteType, parse_uuid7

from .auth import (
    AUTH_MODE_APP_STATE_KEY,
    WEB_AUTH_DISABLED,
    WEB_AUTH_GITHUB,
    WebAuthMode,
    configured_authority_port,
    trusted_authorities_from_scope,
    web_index_response,
)
from .diagnostics import (
    DiagnosticsRequestPayload,
    DiagnosticsService,
    build_production_diagnostics_service,
)
from .drafts import DraftService, build_production_draft_service
from .preview import (
    PreviewContentTooLargeError,
    PreviewInputError,
    PreviewRenderError,
    render_safe_markdown,
)
from .review import ReviewTokenClaims, ReviewTokenCodec, ReviewTokenError, ReviewTokenMode
from .saves import DraftSaveService, build_production_save_service
from .search import SearchService, build_production_search_service
from .self_model import (
    SelfModelRequestPayload,
    SelfModelResponse,
    SelfModelService,
    build_production_self_model_service,
    self_model_response,
)
from .self_retrieval import (
    SelfRetrievalRequestPayload,
    SelfRetrievalResponse,
    SelfRetrievalService,
    build_production_self_retrieval_service,
    self_retrieval_response,
)
from .simulate_me import (
    SimulateMeContextualEvidenceRefPayload,
    SimulateMeEvidenceRefPayload,
    SimulateMeOptionPayload,
    SimulateMeRequestPayload,
    SimulateMeResponse,
    SimulateMeService,
    SimulateMeTemporalCaveatPayload,
    build_production_simulate_me_service,
    simulate_me_request,
    simulate_me_response,
)
from .timeline import (
    TimelineRequestPayload,
    TimelineResponse,
    TimelineService,
    build_production_timeline_service,
    timeline_response,
)
from .transcriptions import TranscriptionService, build_production_transcription_service

REACT_DIST_DIR: Final[Path] = Path(__file__).resolve().parents[4] / "web" / "dist"
REACT_INDEX_FILE: Final[Path] = REACT_DIST_DIR / "index.html"
REACT_ASSETS_DIR: Final[Path] = REACT_DIST_DIR / "assets"
REACT_PWA_MANIFEST_FILE: Final[Path] = REACT_DIST_DIR / "manifest.webmanifest"
REACT_PWA_SERVICE_WORKER_FILE: Final[Path] = REACT_DIST_DIR / "sw.js"
REACT_PWA_OFFLINE_FILE: Final[Path] = REACT_DIST_DIR / "offline.html"
REACT_PWA_OFFLINE_STYLES_FILE: Final[Path] = REACT_DIST_DIR / "offline.css"
REACT_PWA_ICONS_DIR: Final[Path] = REACT_DIST_DIR / "icons"
DRAFT_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
DRAFT_REQUEST_HEADER_VALUE: Final[str] = "draft-v1"
ACTIVE_LEARNING_ANSWER_REVIEW_PATH: Final[str] = "/api/drafts/active-learning/answer/review"
TRANSCRIPTION_REQUEST_HEADER_NAME: Final[str] = DRAFT_REQUEST_HEADER_NAME
TRANSCRIPTION_REQUEST_HEADER_VALUE: Final[str] = "voice-v1"
SEARCH_REQUEST_HEADER_NAME: Final[str] = DRAFT_REQUEST_HEADER_NAME
SEARCH_REQUEST_HEADER_VALUE: Final[str] = "search-v1"
TIMELINE_REQUEST_HEADER_NAME: Final[str] = DRAFT_REQUEST_HEADER_NAME
TIMELINE_REQUEST_HEADER_VALUE: Final[str] = "timeline-v1"
SELF_MODEL_REQUEST_HEADER_NAME: Final[str] = DRAFT_REQUEST_HEADER_NAME
SELF_MODEL_REQUEST_HEADER_VALUE: Final[str] = "self-model-v1"
SELF_RETRIEVAL_REQUEST_HEADER_NAME: Final[str] = DRAFT_REQUEST_HEADER_NAME
SELF_RETRIEVAL_REQUEST_HEADER_VALUE: Final[str] = "self-retrieval-v1"
SIMULATE_ME_REQUEST_HEADER_NAME: Final[str] = DRAFT_REQUEST_HEADER_NAME
SIMULATE_ME_REQUEST_HEADER_VALUE: Final[str] = "simulate-me-v1"
DIAGNOSTICS_REQUEST_HEADER_NAME: Final[str] = DRAFT_REQUEST_HEADER_NAME
DIAGNOSTICS_REQUEST_HEADER_VALUE: Final[str] = "diagnostics-v1"
MAX_RAW_DRAFT_BODY_BYTES: Final[int] = 512 * 1024
MAX_RAW_TRANSCRIPTION_BODY_BYTES: Final[int] = MAX_TRANSCRIPTION_AUDIO_BYTES
MAX_RAW_AUDIO_BODY_BYTES: Final[int] = MAX_RAW_TRANSCRIPTION_BODY_BYTES
MAX_RAW_SEARCH_BODY_BYTES: Final[int] = 64 * 1024
MAX_RAW_TIMELINE_BODY_BYTES: Final[int] = 16 * 1024
MAX_RAW_SELF_MODEL_BODY_BYTES: Final[int] = 16 * 1024
MAX_RAW_SELF_RETRIEVAL_BODY_BYTES: Final[int] = 16 * 1024
MAX_RAW_SIMULATE_ME_BODY_BYTES: Final[int] = 16 * 1024
MAX_RAW_DIAGNOSTICS_BODY_BYTES: Final[int] = 4 * 1024
_TRANSCRIPTION_PATH: Final[str] = "/api/transcriptions/audio"
_SEARCH_PATHS: Final[frozenset[str]] = frozenset({"/api/search", "/api/retrieval/note"})
_TIMELINE_PATHS: Final[frozenset[str]] = frozenset({"/api/timeline"})
_SELF_MODEL_PATHS: Final[frozenset[str]] = frozenset({"/api/self-model"})
_SELF_RETRIEVAL_PATHS: Final[frozenset[str]] = frozenset({"/api/self-retrieval"})
_SIMULATE_ME_PATHS: Final[frozenset[str]] = frozenset({"/api/simulate-me"})
_ACTIVE_LEARNING_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/api/active-learning/questions",
        "/api/active-learning/questions/resolve",
    }
)
_DIAGNOSTICS_PATHS: Final[frozenset[str]] = frozenset({"/api/diagnostics"})
_DECISION_JOURNAL_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/api/drafts/decision-journal/save/prepare",
        "/api/drafts/decision-journal/save/apply",
    }
)
_OUTCOME_OBSERVATION_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/api/drafts/outcome-observation/save/prepare",
        "/api/drafts/outcome-observation/save/apply",
    }
)
_STAGE2_PATHS: Final[frozenset[str]] = _DECISION_JOURNAL_PATHS | _OUTCOME_OBSERVATION_PATHS
_DRAFT_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/api/drafts/text",
        "/api/drafts/url",
        "/api/drafts/preview",
        ACTIVE_LEARNING_ANSWER_REVIEW_PATH,
        "/api/drafts/save/prepare",
        "/api/drafts/save/apply",
        "/api/drafts/personal-memory/save/prepare",
        "/api/drafts/personal-memory/save/apply",
        *_STAGE2_PATHS,
    }
)
_REVIEW_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/api/drafts/preview",
        ACTIVE_LEARNING_ANSWER_REVIEW_PATH,
        "/api/drafts/save/prepare",
        "/api/drafts/save/apply",
        "/api/drafts/personal-memory/save/prepare",
        "/api/drafts/personal-memory/save/apply",
        *_STAGE2_PATHS,
    }
)
_PERSONAL_MEMORY_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/api/drafts/personal-memory/save/prepare",
        "/api/drafts/personal-memory/save/apply",
    }
)
_TRANSCRIPTION_PATHS: Final[frozenset[str]] = frozenset({_TRANSCRIPTION_PATH})
_SAVE_PREFLIGHT_CODES: Final[frozenset[str]] = frozenset(
    {
        "CREATE_INVALID_PLAN",
        "CREATE_INVALID_TIMESTAMP",
        "CREATE_INVALID_TITLE",
        "CREATE_LINKED_PATH",
        "CREATE_PATH_ESCAPE",
        "CREATE_PLAN_FAILED",
        "CREATE_PLAN_STALE",
        "CREATE_PREFLIGHT_FAILED",
        "CREATE_ROOT_MISSING",
        "CREATE_ROOT_NOT_DIRECTORY",
        "CREATE_TEMPLATE_INVALID",
        "CREATE_TEMPLATE_MISSING",
        "CREATE_TEMPLATE_READ_FAILED",
        "CREATE_UNSUPPORTED_TYPE",
        "CREATE_TARGET_CHECK_FAILED",
        "OUTCOME_DECISION_NOT_FOUND",
        "OUTCOME_DECISION_IDENTITY_CONFLICT",
        "OUTCOME_DECISION_TARGET_INVALID",
    }
)
_TRUSTED_LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "localhost", "::1"})
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


class SearchRequestPayload(BaseModel):
    """Strict JSON request for one local Search operation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: StrictStr
    limit: StrictInt = 20


class RetrievalRequestPayload(BaseModel):
    """Strict JSON request for canonical UUID retrieval."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: StrictStr


class TranscriptPayload(BaseModel):
    """Минимальная HTTP projection of provider-neutral ``Transcript``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    text: StrictStr


class TranscriptionResponse(BaseModel):
    """Safe response containing only the reviewed-flow transcript text."""

    model_config = ConfigDict(extra="forbid", strict=True)

    transcript: TranscriptPayload


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


class ActiveLearningAnswerReviewRequest(BaseModel):
    """Strict source-free review request for a user-authored answer draft."""

    model_config = ConfigDict(extra="forbid", strict=True)

    draft: DraftPayload


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


class PersonalMemoryPayload(BaseModel):
    """Strict HTTP projection of the controlled Personal Memory Stage 1 fields."""

    model_config = ConfigDict(extra="forbid", strict=True)

    evidence_kind: StrictStr
    self_kind: StrictStr
    evidence_at: StrictStr
    evidence_at_precision: StrictStr
    domain: StrictStr | None = None


class PreparePersonalMemoryRequest(BaseModel):
    """Strict request for a source-free Text Personal Memory dry-run."""

    model_config = ConfigDict(extra="forbid", strict=True)

    review_token: StrictStr
    draft: DraftPayload
    personal_memory: PersonalMemoryPayload


class ApplyPersonalMemoryRequest(BaseModel):
    """Strict request for a confirmed Personal Memory Safe Write apply."""

    model_config = ConfigDict(extra="forbid", strict=True)

    review_token: StrictStr
    confirmation_token: StrictStr
    draft: DraftPayload
    personal_memory: PersonalMemoryPayload


class DecisionJournalPayload(BaseModel):
    """Strict structured input for an initial Decision Journal only.

    Late outcome fields are deliberately absent from this HTTP DTO. The
    server owns the application marker/kinds and renders the canonical body.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    title: StrictStr
    note_type: StrictStr
    tags: list[StrictStr]
    links: list[StrictStr]
    evidence_at: StrictStr
    evidence_at_precision: StrictStr
    domain: StrictStr | None = None
    situation: StrictStr
    available_options: list[StrictStr]
    information_known_at_decision_time: StrictStr
    criteria: list[StrictStr]
    chosen_option: StrictStr
    reasons: StrictStr
    confidence: StrictStr
    expected_result: StrictStr


class PrepareDecisionJournalRequest(BaseModel):
    """Strict request for a structured Decision Journal dry-run."""

    model_config = ConfigDict(extra="forbid", strict=True)

    decision: DecisionJournalPayload


class ApplyDecisionJournalRequest(BaseModel):
    """Strict request for a confirmed structured Decision Journal apply."""

    model_config = ConfigDict(extra="forbid", strict=True)

    confirmation_token: StrictStr
    decision: DecisionJournalPayload


class OutcomeObservationPayload(BaseModel):
    """Strict structured input for a separate linked Outcome Observation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: StrictStr
    note_type: StrictStr
    tags: list[StrictStr]
    links: list[StrictStr]
    decision_id: StrictStr
    evidence_at: StrictStr
    evidence_at_precision: StrictStr
    domain: StrictStr | None = None
    actual_result: StrictStr
    reassessment: StrictStr
    notes: StrictStr


class PrepareOutcomeObservationRequest(BaseModel):
    """Strict request for a structured Outcome Observation dry-run."""

    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: OutcomeObservationPayload


class ApplyOutcomeObservationRequest(BaseModel):
    """Strict request for a confirmed structured Outcome Observation apply."""

    model_config = ConfigDict(extra="forbid", strict=True)

    confirmation_token: StrictStr
    outcome: OutcomeObservationPayload


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


class SearchHitPayload(BaseModel):
    """Safe public SearchHit projection without indexed body content."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: StrictStr
    type: StrictStr
    title: StrictStr
    relative_path: StrictStr
    tags: list[StrictStr]
    created: datetime
    updated: datetime | None
    snippet: StrictStr


class SearchResponse(BaseModel):
    """Ranked Search response containing bounded snippets only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    hits: list[SearchHitPayload]


class RetrievedNotePayload(BaseModel):
    """Safe current-note projection with exact body under ``content``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: StrictStr
    type: StrictStr
    title: StrictStr
    relative_path: StrictStr
    tags: list[StrictStr]
    created: datetime
    updated: datetime | None
    content: StrictStr


class RetrievalResponse(BaseModel):
    """Canonical read-only retrieval response."""

    model_config = ConfigDict(extra="forbid", strict=True)

    note: RetrievedNotePayload


_ERRORS: Final[dict[str, tuple[int, str]]] = {
    "LLM_INVALID_REQUEST": (400, "Запрос черновика не прошёл проверку"),
    "LLM_CANCELLED": (504, "Операция построения черновика отменена"),
    "LLM_TIMEOUT": (504, "Операция построения черновика превысила лимит времени"),
    "LLM_BACKEND_UNAVAILABLE": (503, "Сервис построения черновика недоступен"),
    "LLM_UPSTREAM_FAILURE": (502, "Сервис построения черновика вернул ошибку"),
    "LLM_MALFORMED_RESULT": (502, "Сервис построения черновика вернул некорректный результат"),
    "LLM_CONTENT_TOO_LARGE": (413, "Содержимое черновика слишком велико"),
    "RESEARCH_INVALID_REQUEST": (400, "Запрос исследования не прошёл проверку"),
    "RESEARCH_CANCELLED": (504, "Операция исследования отменена"),
    "RESEARCH_TIMEOUT": (504, "Операция исследования превысила лимит времени"),
    "RESEARCH_BACKEND_UNAVAILABLE": (503, "Сервис исследования недоступен"),
    "RESEARCH_UPSTREAM_FAILURE": (502, "Сервис исследования вернул ошибку"),
    "RESEARCH_MALFORMED_RESULT": (502, "Сервис исследования вернул некорректный результат"),
    "RESEARCH_CONTENT_TOO_LARGE": (413, "Содержимое исследования слишком велико"),
    "TRANSCRIPTION_INVALID_REQUEST": (400, "Запрос распознавания не прошёл проверку"),
    "TRANSCRIPTION_CANCELLED": (504, "Операция распознавания отменена"),
    "TRANSCRIPTION_TIMEOUT": (504, "Операция распознавания превысила лимит времени"),
    "TRANSCRIPTION_BACKEND_UNAVAILABLE": (503, "Сервис распознавания недоступен"),
    "TRANSCRIPTION_UPSTREAM_FAILURE": (502, "Сервис распознавания вернул ошибку"),
    "TRANSCRIPTION_MALFORMED_RESULT": (502, "Сервис распознавания вернул некорректный результат"),
    "TRANSCRIPTION_CONTENT_TOO_LARGE": (413, "Аудиосодержимое слишком велико"),
    "DRAFT_INVALID_REQUEST": (400, "Запрос черновика не прошёл проверку"),
    "DRAFT_CONTENT_TOO_LARGE": (413, "Содержимое черновика слишком велико"),
    "REVIEW_TOKEN_INVALID": (400, "Токен проверки недействителен или истёк"),
    "SAVE_CONFIRMATION_INVALID": (400, "Подтверждение сохранения недействительно или истекло"),
    "PERSONAL_MEMORY_INVALID_REQUEST": (400, "Запрос личной памяти не прошёл проверку"),
    "PERSONAL_MEMORY_CONTEXT_INVALID": (400, "Для личной памяти нужен текстовый черновик"),
    "DECISION_JOURNAL_INVALID_REQUEST": (400, "Запрос журнала решений не прошёл проверку"),
    "OUTCOME_OBSERVATION_INVALID_REQUEST": (
        400,
        "Запрос наблюдения результата не прошёл проверку",
    ),
    "STAGE2_SAVE_CONFIRMATION_INVALID": (
        400,
        "Подтверждение сохранения шага 2 недействительно или истекло",
    ),
    "DRAFT_SCHEMA_INVALID": (400, "Изменённый черновик не прошёл проверку"),
    "PREVIEW_FAILED": (500, "Не удалось построить безопасный предпросмотр Markdown"),
    "VAULT_UNAVAILABLE": (503, "Хранилище недоступно для сохранения"),
    "CREATE_TARGET_EXISTS": (409, "Файл заметки уже существует"),
    "SAVE_PREFLIGHT_CONFLICT": (409, "Предварительная проверка хранилища отклонила сохранение"),
    "SAVE_ROLLED_BACK": (500, "Сохранение отменено откатом; заметка не опубликована"),
    "SAVE_RECOVERY_REQUIRED": (500, "После сбоя сохранения требуется ручная проверка хранилища"),
    "SAVE_FAILED": (500, "Не удалось сохранить заметку"),
    "SEARCH_INVALID_REQUEST": (400, "Запрос поиска не прошёл проверку"),
    "SEARCH_CONTENT_TOO_LARGE": (413, "Запрос поиска слишком велик"),
    "SEARCH_BACKEND_UNAVAILABLE": (503, "Сервис поиска недоступен"),
    "SEARCH_INDEX_FAILED": (500, "Не удалось пересобрать индекс поиска"),
    "SEARCH_QUERY_FAILED": (500, "Не удалось выполнить поисковый запрос"),
    "SEARCH_NOT_FOUND": (404, "Запрошенная заметка не найдена"),
    "SEARCH_IDENTITY_CONFLICT": (409, "Идентификатор запрошенной заметки конфликтует"),
    "TIMELINE_INVALID_REQUEST": (400, "Запрос хронологии не прошёл проверку"),
    "TIMELINE_CONTENT_TOO_LARGE": (413, "Запрос хронологии слишком велик"),
    "TIMELINE_EVIDENCE_INVALID": (409, "Свидетельство хронологии недействительно"),
    "TIMELINE_VAULT_UNAVAILABLE": (503, "Хранилище хронологии недоступно"),
    "TIMELINE_INVALID_CLOCK": (500, "Часы сервера хронологии недействительны"),
    "TIMELINE_INTERNAL_ERROR": (500, "Не удалось построить хронологию"),
    "SELF_MODEL_INVALID_REQUEST": (400, "Запрос модели себя не прошёл проверку"),
    "SELF_MODEL_CONTENT_TOO_LARGE": (413, "Запрос модели себя слишком велик"),
    "SELF_MODEL_POLICY_UNAVAILABLE": (500, "Политика модели себя недоступна"),
    "SELF_MODEL_VAULT_UNAVAILABLE": (503, "Хранилище модели себя недоступно"),
    "SELF_MODEL_EVIDENCE_INVALID": (409, "Свидетельство модели себя недействительно"),
    "SELF_MODEL_RESULT_INVALID": (500, "Результат модели себя не прошёл проверку"),
    "SELF_MODEL_RESULT_TOO_LARGE": (413, "Результат модели себя слишком велик"),
    "SELF_MODEL_INVALID_CLOCK": (500, "Часы сервера модели себя недействительны"),
    "SELF_RETRIEVAL_INVALID_REQUEST": (400, "Запрос сбора контекста не прошёл проверку"),
    "SELF_RETRIEVAL_SEARCH_UNAVAILABLE": (503, "Поиск для сбора контекста недоступен"),
    "SELF_RETRIEVAL_CURRENT_READ_UNAVAILABLE": (
        503,
        "Текущее чтение для сбора контекста недоступно",
    ),
    "SELF_RETRIEVAL_SELF_MODEL_UNAVAILABLE": (503, "Модель себя для сбора контекста недоступна"),
    "SELF_RETRIEVAL_RESULT_INVALID": (500, "Результат сбора контекста не прошёл проверку"),
    "SELF_RETRIEVAL_RESULT_TOO_LARGE": (413, "Результат сбора контекста слишком велик"),
    "SIMULATE_ME_INVALID_REQUEST": (400, "Запрос прогноза не прошёл проверку"),
    "SIMULATE_ME_CONTENT_TOO_LARGE": (413, "Запрос прогноза слишком велик"),
    "SIMULATE_ME_VAULT_UNAVAILABLE": (503, "Хранилище прогноза недоступно"),
    "SIMULATE_ME_RESULT_INVALID": (500, "Результат прогноза не прошёл проверку"),
    "ACTIVE_LEARNING_INVALID_REQUEST": (400, "Запрос уточнения модели не прошёл проверку"),
    "ACTIVE_LEARNING_CONTENT_TOO_LARGE": (413, "Запрос уточнения модели слишком велик"),
    "DIAGNOSTICS_INVALID_REQUEST": (400, "Запрос диагностики не прошёл проверку"),
    "DIAGNOSTICS_CONTENT_TOO_LARGE": (413, "Запрос диагностики слишком велик"),
    "DIAGNOSTICS_UNAVAILABLE": (503, "Диагностика рабочего пространства недоступна"),
    "DIAGNOSTICS_INTERNAL_ERROR": (500, "Не удалось получить диагностику рабочего пространства"),
}
_GENERIC_ERROR: Final[tuple[int, str, str]] = (
    500,
    "DRAFT_GENERATION_FAILED",
    "Не удалось построить черновик",
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


class SearchRequestBoundaryMiddleware:
    """Scoped ASGI boundary for private Search/Retrieval JSON requests."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        """Store the raw-body cap before FastAPI JSON parsing."""

        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Require search-v1, trusted same-origin metadata and bounded JSON."""

        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in _SEARCH_PATHS
        ):
            await self.app(scope, receive, send)
            return

        path = str(scope["path"])
        content_type_present, content_type = _single_header(scope, "content-type")
        request_header_present, request_header = _single_header(scope, SEARCH_REQUEST_HEADER_NAME)
        origin_present, origin = _single_header(scope, "origin")
        content_length_present, content_length = _single_header(scope, "content-length")
        if (
            not content_type_present
            or content_type is None
            or not _is_json_content_type(content_type)
            or not request_header_present
            or request_header != SEARCH_REQUEST_HEADER_VALUE
            or not _trusted_request_host(scope)
            or (origin_present and (origin is None or not _origin_matches(scope, origin)))
        ):
            await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
            return

        if content_length_present:
            if content_length is None:
                await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
                return
            try:
                declared_length = int(content_length.strip())
            except ValueError:
                await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
                return
            if declared_length < 0:
                await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
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


class TimelineRequestBoundaryMiddleware:
    """Scoped ASGI boundary for private Timeline JSON requests."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        """Store the small raw-body cap before FastAPI JSON parsing."""

        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Require timeline-v1, trusted same-origin metadata and bounded JSON."""

        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in _TIMELINE_PATHS
        ):
            await self.app(scope, receive, send)
            return

        path = str(scope["path"])
        content_type_present, content_type = _single_header(scope, "content-type")
        request_header_present, request_header = _single_header(scope, TIMELINE_REQUEST_HEADER_NAME)
        origin_present, origin = _single_header(scope, "origin")
        content_length_present, content_length = _single_header(scope, "content-length")
        if (
            not content_type_present
            or content_type is None
            or not _is_json_content_type(content_type)
            or not request_header_present
            or request_header != TIMELINE_REQUEST_HEADER_VALUE
            or not _trusted_request_host(scope)
            or (origin_present and (origin is None or not _origin_matches(scope, origin)))
        ):
            await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
            return

        if content_length_present:
            if content_length is None:
                await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
                return
            try:
                declared_length = int(content_length.strip())
            except ValueError:
                await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
                return
            if declared_length < 0:
                await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
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


class SelfModelRequestBoundaryMiddleware:
    """Scoped ASGI boundary for private Self Model JSON requests."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        """Store the small raw-body cap before FastAPI JSON parsing."""

        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Require self-model-v1, trusted same-origin metadata and bounded JSON."""

        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in _SELF_MODEL_PATHS
        ):
            await self.app(scope, receive, send)
            return

        path = str(scope["path"])
        content_type_present, content_type = _single_header(scope, "content-type")
        request_header_present, request_header = _single_header(
            scope, SELF_MODEL_REQUEST_HEADER_NAME
        )
        origin_present, origin = _single_header(scope, "origin")
        content_length_present, content_length = _single_header(scope, "content-length")
        if (
            not content_type_present
            or content_type is None
            or not _is_json_content_type(content_type)
            or not request_header_present
            or request_header != SELF_MODEL_REQUEST_HEADER_VALUE
            or not _trusted_request_host(scope)
            or (origin_present and (origin is None or not _origin_matches(scope, origin)))
        ):
            await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
            return

        if content_length_present:
            if content_length is None:
                await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
                return
            try:
                declared_length = int(content_length.strip())
            except ValueError:
                await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
                return
            if declared_length < 0:
                await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
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


class SelfRetrievalRequestBoundaryMiddleware:
    """Scoped ASGI boundary for private Self Retrieval JSON requests."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        paths: frozenset[str] = _SELF_RETRIEVAL_PATHS,
        request_header_value: str = SELF_RETRIEVAL_REQUEST_HEADER_VALUE,
    ) -> None:
        """Store the small raw-body cap before FastAPI JSON parsing."""

        self.app = app
        self.max_body_bytes = max_body_bytes
        self.paths = paths
        self.request_header_value = request_header_value

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Require self-retrieval-v1, trusted same-origin metadata and bounded JSON."""

        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in self.paths
        ):
            await self.app(scope, receive, send)
            return

        path = str(scope["path"])
        content_type_present, content_type = _single_header(scope, "content-type")
        request_header_present, request_header = _single_header(
            scope, SELF_RETRIEVAL_REQUEST_HEADER_NAME
        )
        origin_present, origin = _single_header(scope, "origin")
        content_length_present, content_length = _single_header(scope, "content-length")
        if (
            not content_type_present
            or content_type is None
            or not _is_json_content_type(content_type)
            or not request_header_present
            or request_header != self.request_header_value
            or not _trusted_request_host(scope)
            or (origin_present and (origin is None or not _origin_matches(scope, origin)))
        ):
            await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
            return

        if content_length_present:
            if content_length is None:
                await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
                return
            try:
                declared_length = int(content_length.strip())
            except ValueError:
                await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
                return
            if declared_length < 0:
                await _send_boundary_error(scope, receive, send, _invalid_request_code(path))
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


class SimulateMeRequestBoundaryMiddleware(SelfRetrievalRequestBoundaryMiddleware):
    """Scoped ASGI boundary for the private Simulate Me JSON request."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        """Require the Simulate Me purpose and its bounded raw JSON body."""

        super().__init__(
            app,
            max_body_bytes=max_body_bytes,
            paths=_SIMULATE_ME_PATHS,
            request_header_value=SIMULATE_ME_REQUEST_HEADER_VALUE,
        )


class DiagnosticsRequestBoundaryMiddleware(SelfRetrievalRequestBoundaryMiddleware):
    """Scoped ASGI boundary for the private Diagnostics JSON request."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        """Require the diagnostics purpose and its bounded raw JSON body."""

        super().__init__(
            app,
            max_body_bytes=max_body_bytes,
            paths=_DIAGNOSTICS_PATHS,
            request_header_value=DIAGNOSTICS_REQUEST_HEADER_VALUE,
        )


class TranscriptionRequestBoundaryMiddleware:
    """Scoped ASGI boundary для raw audio, voice headers, Origin и byte cap."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        """Сохранить единственный bounded raw-audio cap."""

        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Проверить voice request до FastAPI endpoint body handling."""

        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in _TRANSCRIPTION_PATHS
        ):
            await self.app(scope, receive, send)
            return

        content_type_present, content_type = _single_header(scope, "content-type")
        request_header_present, request_header = _single_header(
            scope, TRANSCRIPTION_REQUEST_HEADER_NAME
        )
        origin_present, origin = _single_header(scope, "origin")
        content_length_present, content_length = _single_header(scope, "content-length")
        valid_content_type = False
        if content_type_present and content_type is not None:
            try:
                normalize_audio_media_type(content_type)
                valid_content_type = True
            except TranscriptionInvalidRequestError:
                pass

        if (
            not valid_content_type
            or not request_header_present
            or request_header != TRANSCRIPTION_REQUEST_HEADER_VALUE
            or not _trusted_request_host(scope)
            or (origin_present and (origin is None or not _origin_matches(scope, origin)))
        ):
            await _send_boundary_error(scope, receive, send, "TRANSCRIPTION_INVALID_REQUEST")
            return

        if content_length_present:
            if content_length is None:
                await _send_boundary_error(scope, receive, send, "TRANSCRIPTION_INVALID_REQUEST")
                return
            try:
                declared_length = int(content_length.strip())
            except ValueError:
                await _send_boundary_error(scope, receive, send, "TRANSCRIPTION_INVALID_REQUEST")
                return
            if declared_length < 0:
                await _send_boundary_error(scope, receive, send, "TRANSCRIPTION_INVALID_REQUEST")
                return
            if declared_length > self.max_body_bytes:
                await _send_boundary_error(
                    scope,
                    receive,
                    send,
                    "TRANSCRIPTION_CONTENT_TOO_LARGE",
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
                    "TRANSCRIPTION_CONTENT_TOO_LARGE",
                )
                return
            received_bytes += len(body)
            if received_bytes > self.max_body_bytes:
                await _send_boundary_error(
                    scope,
                    receive,
                    send,
                    "TRANSCRIPTION_CONTENT_TOO_LARGE",
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

    response = _error_response(code)
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    await response(scope, receive, send)


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
    """Разрешить same-origin loopback или exact configured Origin с тем же Host/port."""

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
    if origin_host is not None or request_host is not None:
        return origin_host is not None and origin_host == request_host
    authorities = trusted_authorities_from_scope(scope)
    configured_origin = configured_authority_port(parsed_origin.netloc, scheme, authorities)
    configured_request = configured_authority_port(host_header, scheme, authorities)
    return configured_origin is not None and configured_origin == configured_request


def _trusted_request_host(scope: Scope) -> bool:
    """Разрешить trusted loopback или exact configured Host для draft boundary."""

    scheme = str(scope.get("scheme", "")).casefold()
    if scheme not in {"http", "https"}:
        return False
    host_present, host = _single_header(scope, "host")
    if not host_present or host is None:
        return False
    if _loopback_host_port(host, scheme) is not None:
        return True
    return (
        configured_authority_port(
            host,
            scheme,
            trusted_authorities_from_scope(scope),
        )
        is not None
    )


def _loopback_host_port(value: str, scheme: str) -> tuple[str, int] | None:
    """Распознать только loopback authorities и нормализовать default port."""

    if not value or any(character.isspace() for character in value):
        return None
    try:
        parsed = urlsplit(f"//{value}")
        hostname = parsed.hostname
        port = parsed.port
    except UnicodeError, ValueError:
        return None
    if (
        hostname is None
        or hostname.casefold() not in _TRUSTED_LOOPBACK_HOSTS
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
    ):
        return None
    normalized_scheme = scheme.casefold()
    if port is None:
        port = 443 if normalized_scheme == "https" else 80
    if not 1 <= port <= 65535:
        return None
    return hostname.casefold(), port


class _LoopbackTrustedHostMiddleware(TrustedHostMiddleware):
    """Проверить Host strict parser для loopback и configured public authority."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(
            app,
            allowed_hosts=["127.0.0.1", "localhost", "::1"],
            www_redirect=False,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        host_present, host = _single_header(scope, "host")
        scheme = str(scope.get("scheme", "")).casefold()
        if scheme == "ws":
            scheme = "http"
        elif scheme == "wss":
            scheme = "https"
        if (
            scheme in {"http", "https"}
            and host_present
            and host is not None
            and (
                _loopback_host_port(host, scheme) is not None
                or configured_authority_port(
                    host,
                    scheme,
                    trusted_authorities_from_scope(scope),
                )
                is not None
            )
        ):
            await self.app(scope, receive, send)
            return

        response = PlainTextResponse("Некорректный заголовок запроса Host.", status_code=400)
        await response(scope, receive, send)


def _invalid_request_code(path: str) -> str:
    """Выбрать safe application code по private API route."""

    if path in _SIMULATE_ME_PATHS:
        return "SIMULATE_ME_INVALID_REQUEST"
    if path in _ACTIVE_LEARNING_PATHS:
        return "ACTIVE_LEARNING_INVALID_REQUEST"
    if path in _DIAGNOSTICS_PATHS:
        return "DIAGNOSTICS_INVALID_REQUEST"
    if path in _SELF_RETRIEVAL_PATHS:
        return "SELF_RETRIEVAL_INVALID_REQUEST"
    if path in _SELF_MODEL_PATHS:
        return "SELF_MODEL_INVALID_REQUEST"
    if path in _TIMELINE_PATHS:
        return "TIMELINE_INVALID_REQUEST"
    if path in _DECISION_JOURNAL_PATHS:
        return "DECISION_JOURNAL_INVALID_REQUEST"
    if path in _OUTCOME_OBSERVATION_PATHS:
        return "OUTCOME_OBSERVATION_INVALID_REQUEST"
    if path in _PERSONAL_MEMORY_PATHS:
        return "PERSONAL_MEMORY_INVALID_REQUEST"
    if path in _SEARCH_PATHS:
        return "SEARCH_INVALID_REQUEST"
    if path.endswith("/url"):
        return "RESEARCH_INVALID_REQUEST"
    if path == _TRANSCRIPTION_PATH:
        return "TRANSCRIPTION_INVALID_REQUEST"
    if path in _REVIEW_PATHS:
        return "DRAFT_INVALID_REQUEST"
    return "LLM_INVALID_REQUEST"


def _content_too_large_code(path: str) -> str:
    """Выбрать bounded content-too-large code по private API route."""

    if path in _SIMULATE_ME_PATHS:
        return "SIMULATE_ME_CONTENT_TOO_LARGE"
    if path in _ACTIVE_LEARNING_PATHS:
        return "ACTIVE_LEARNING_CONTENT_TOO_LARGE"
    if path in _DIAGNOSTICS_PATHS:
        return "DIAGNOSTICS_CONTENT_TOO_LARGE"
    if path in _SELF_RETRIEVAL_PATHS:
        return "SELF_RETRIEVAL_RESULT_TOO_LARGE"
    if path in _SELF_MODEL_PATHS:
        return "SELF_MODEL_CONTENT_TOO_LARGE"
    if path in _TIMELINE_PATHS:
        return "TIMELINE_CONTENT_TOO_LARGE"
    if path in _SEARCH_PATHS:
        return "SEARCH_CONTENT_TOO_LARGE"
    if path.endswith("/url"):
        return "RESEARCH_CONTENT_TOO_LARGE"
    if path == _TRANSCRIPTION_PATH:
        return "TRANSCRIPTION_CONTENT_TOO_LARGE"
    if path in _REVIEW_PATHS:
        return "DRAFT_CONTENT_TOO_LARGE"
    return "LLM_CONTENT_TOO_LARGE"


def _pwa_file_response(file_path: Path, *, media_type: str) -> Response:
    """Отдать только заранее собранный публичный PWA-файл без private fallback."""

    if not file_path.is_file():
        return PlainTextResponse(
            "PWA-ресурс доступен после сборки React-интерфейса.",
            status_code=404,
            headers={"Cache-Control": "no-store"},
        )
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={"Cache-Control": "no-cache"},
    )


def create_app(
    *,
    draft_service: DraftService | None = None,
    transcription_service: TranscriptionService | None = None,
    save_service: DraftSaveService | None = None,
    search_service: SearchService | None = None,
    timeline_service: TimelineService | None = None,
    self_model_service: SelfModelService | None = None,
    self_retrieval_service: SelfRetrievalService | None = None,
    simulate_me_service: SimulateMeService | None = None,
    diagnostics_service: DiagnosticsService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> FastAPI:
    """Создать Web GUI без config/vault side effects до явного read/write запроса."""

    service = draft_service if draft_service is not None else build_production_draft_service()
    transcriber = (
        transcription_service
        if transcription_service is not None
        else build_production_transcription_service()
    )
    saver = (
        save_service
        if save_service is not None
        else build_production_save_service(
            env_file=env_file,
            vault_path_override=vault_path_override,
        )
    )
    searcher = (
        search_service
        if search_service is not None
        else build_production_search_service(
            env_file=env_file,
            vault_path_override=vault_path_override,
        )
    )
    timeliner = (
        timeline_service
        if timeline_service is not None
        else build_production_timeline_service(
            env_file=env_file,
            vault_path_override=vault_path_override,
        )
    )
    self_modeler = (
        self_model_service
        if self_model_service is not None
        else build_production_self_model_service(
            env_file=env_file,
            vault_path_override=vault_path_override,
        )
    )
    self_retriever = (
        self_retrieval_service
        if self_retrieval_service is not None
        else build_production_self_retrieval_service(
            env_file=env_file,
            vault_path_override=vault_path_override,
        )
    )
    simulate_me = (
        simulate_me_service
        if simulate_me_service is not None
        else build_production_simulate_me_service(
            env_file=env_file,
            vault_path_override=vault_path_override,
        )
    )
    doctor = (
        diagnostics_service
        if diagnostics_service is not None
        else build_production_diagnostics_service(
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
        _LoopbackTrustedHostMiddleware,
    )
    app.add_middleware(
        DraftRequestBoundaryMiddleware,
        max_body_bytes=MAX_RAW_DRAFT_BODY_BYTES,
    )
    app.add_middleware(
        SearchRequestBoundaryMiddleware,
        max_body_bytes=MAX_RAW_SEARCH_BODY_BYTES,
    )
    app.add_middleware(
        TranscriptionRequestBoundaryMiddleware,
        max_body_bytes=MAX_RAW_TRANSCRIPTION_BODY_BYTES,
    )
    app.add_middleware(
        TimelineRequestBoundaryMiddleware,
        max_body_bytes=MAX_RAW_TIMELINE_BODY_BYTES,
    )
    app.add_middleware(
        SelfModelRequestBoundaryMiddleware,
        max_body_bytes=MAX_RAW_SELF_MODEL_BODY_BYTES,
    )
    app.add_middleware(
        SelfRetrievalRequestBoundaryMiddleware,
        max_body_bytes=MAX_RAW_SELF_RETRIEVAL_BODY_BYTES,
    )
    app.add_middleware(
        SimulateMeRequestBoundaryMiddleware,
        max_body_bytes=MAX_RAW_SIMULATE_ME_BODY_BYTES,
    )
    app.add_middleware(
        DiagnosticsRequestBoundaryMiddleware,
        max_body_bytes=MAX_RAW_DIAGNOSTICS_BODY_BYTES,
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
        if request.url.path.startswith(
            (
                "/api/drafts/",
                "/api/transcriptions/",
                "/api/search",
                "/api/retrieval/",
                "/api/timeline",
                "/api/self-model",
                "/api/self-retrieval",
                "/api/simulate-me",
                "/api/active-learning/",
                "/api/diagnostics",
                "/api/behavioral-self-model",
                "/api/stated-observed-mapping/",
                "/api/stated-observed-composition",
                "/api/growth-advisor/",
                "/api/growth",
                "/api/growth-learning/",
                "/api/goal-progress",
                "/api/growth-goal-progress",
            )
        ):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/icons/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
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

    @app.post(ACTIVE_LEARNING_ANSWER_REVIEW_PATH, include_in_schema=False)
    def review_active_learning_answer(payload: ActiveLearningAnswerReviewRequest) -> Response:
        """Issue the existing source-free text review token for an edited answer."""

        try:
            draft = _note_draft_from_payload(payload.draft)
        except LlmError, ValueError:
            return _error_response("DRAFT_SCHEMA_INVALID")
        if not draft.content.strip():
            return _error_response("DRAFT_SCHEMA_INVALID")
        try:
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

    @app.post(_TRANSCRIPTION_PATH, include_in_schema=False)
    async def transcribe_audio(request: Request) -> Response:
        """Распознать raw audio и вернуть только editable-flow transcript."""

        content_type = request.headers.get("content-type")
        if content_type is None:
            return _error_response("TRANSCRIPTION_INVALID_REQUEST")
        try:
            normalize_audio_media_type(content_type)
            body = await request.body()
        except TranscriptionInvalidRequestError:
            return _error_response("TRANSCRIPTION_INVALID_REQUEST")
        except Exception:
            return _error_response("TRANSCRIPTION_INVALID_REQUEST")
        if not body:
            return _error_response("TRANSCRIPTION_INVALID_REQUEST")
        if len(body) > MAX_RAW_TRANSCRIPTION_BODY_BYTES:
            return _error_response("TRANSCRIPTION_CONTENT_TOO_LARGE")
        try:
            transcript = validate_transcript(
                await run_in_threadpool(transcriber.transcribe, body, content_type)
            )
        except TranscriptionError as error:
            return _error_response(error.code)
        except Exception:
            return _error_response("TRANSCRIPTION_UPSTREAM_FAILURE")
        response = TranscriptionResponse(
            transcript=TranscriptPayload(text=transcript.text),
        )
        return JSONResponse(
            content=response.model_dump(mode="json"),
            headers=_DRAFT_ERROR_HEADERS,
        )

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

    @app.post("/api/drafts/personal-memory/save/prepare", include_in_schema=False)
    def prepare_personal_memory_save(payload: PreparePersonalMemoryRequest) -> Response:
        """Prepare an explicit source-free Text Personal Memory dry-run."""

        try:
            claims = review_tokens.verify(payload.review_token)
        except ReviewTokenError:
            return _error_response("REVIEW_TOKEN_INVALID")
        if claims.mode is not ReviewTokenMode.TEXT:
            return _error_response("PERSONAL_MEMORY_CONTEXT_INVALID")

        try:
            personal_memory = _personal_memory_from_payload(payload.draft, payload.personal_memory)
        except LlmError, PersonalMemoryDraftError, ValueError:
            return _error_response("PERSONAL_MEMORY_INVALID_REQUEST")

        try:
            result = saver.prepare_personal_memory(personal_memory)
        except ConfigurationError, WriteSafetyError, OSError:
            return _error_response("VAULT_UNAVAILABLE")
        except Exception:
            return _error_response("SAVE_FAILED")
        return _prepare_personal_memory_response(
            result,
            review_token=payload.review_token,
            personal_memory=personal_memory,
            review_tokens=review_tokens,
        )

    @app.post("/api/drafts/personal-memory/save/apply", include_in_schema=False)
    def apply_personal_memory_save(payload: ApplyPersonalMemoryRequest) -> Response:
        """Apply only an exact Personal Memory confirmation-bound Safe Write plan."""

        try:
            claims = review_tokens.verify(payload.review_token)
        except ReviewTokenError:
            return _error_response("REVIEW_TOKEN_INVALID")
        if claims.mode is not ReviewTokenMode.TEXT:
            return _error_response("PERSONAL_MEMORY_CONTEXT_INVALID")

        try:
            personal_memory = _personal_memory_from_payload(payload.draft, payload.personal_memory)
        except LlmError, PersonalMemoryDraftError, ValueError:
            return _error_response("PERSONAL_MEMORY_INVALID_REQUEST")

        try:
            review_tokens.verify_personal_memory_confirmation(
                payload.confirmation_token,
                review_token=payload.review_token,
                draft=personal_memory.draft,
                metadata=personal_memory.metadata,
            )
        except ReviewTokenError:
            return _error_response("SAVE_CONFIRMATION_INVALID")

        try:
            result = saver.apply_personal_memory(personal_memory)
        except ConfigurationError, WriteSafetyError, OSError:
            return _error_response("VAULT_UNAVAILABLE")
        except Exception:
            return _error_response("SAVE_FAILED")
        return _save_response(result)

    @app.post("/api/drafts/decision-journal/save/prepare", include_in_schema=False)
    def prepare_decision_journal_save(payload: PrepareDecisionJournalRequest) -> Response:
        """Render, validate, and dry-run one explicit Decision Journal."""

        try:
            draft = _decision_journal_from_payload(payload.decision)
        except DecisionJournalDraftError, LlmError, ValueError, TypeError, UnicodeError:
            return _error_response("DECISION_JOURNAL_INVALID_REQUEST")

        try:
            result = saver.prepare_decision_journal(draft)
        except ConfigurationError, WriteSafetyError, OSError:
            return _error_response("VAULT_UNAVAILABLE")
        except Exception:
            return _error_response("SAVE_FAILED")
        return _prepare_stage2_response(
            result,
            draft=draft,
            review_tokens=review_tokens,
            decision=True,
        )

    @app.post("/api/drafts/decision-journal/save/apply", include_in_schema=False)
    def apply_decision_journal_save(payload: ApplyDecisionJournalRequest) -> Response:
        """Verify a purpose-bound Journal token before the existing Safe Write apply."""

        try:
            draft = _decision_journal_from_payload(payload.decision)
        except DecisionJournalDraftError, LlmError, ValueError, TypeError, UnicodeError:
            return _error_response("DECISION_JOURNAL_INVALID_REQUEST")

        try:
            claims = review_tokens.verify_decision_journal_confirmation(
                payload.confirmation_token,
                draft,
            )
        except ReviewTokenError:
            return _error_response("STAGE2_SAVE_CONFIRMATION_INVALID")

        try:
            result = saver.apply_decision_journal(draft, plan_sha256=claims.plan_sha256)
        except ConfigurationError, WriteSafetyError, OSError:
            return _error_response("VAULT_UNAVAILABLE")
        except Exception:
            return _error_response("SAVE_FAILED")
        return _save_response(result)

    @app.post("/api/drafts/outcome-observation/save/prepare", include_in_schema=False)
    def prepare_outcome_observation_save(payload: PrepareOutcomeObservationRequest) -> Response:
        """Render, validate, current-scan, and dry-run one Outcome Observation."""

        try:
            draft = _outcome_observation_from_payload(payload.outcome)
        except OutcomeObservationDraftError, LlmError, ValueError, TypeError, UnicodeError:
            return _error_response("OUTCOME_OBSERVATION_INVALID_REQUEST")

        try:
            result = saver.prepare_outcome_observation(draft)
        except ConfigurationError, WriteSafetyError, OSError:
            return _error_response("VAULT_UNAVAILABLE")
        except Exception:
            return _error_response("SAVE_FAILED")
        return _prepare_stage2_response(
            result,
            draft=draft,
            review_tokens=review_tokens,
            decision=False,
        )

    @app.post("/api/drafts/outcome-observation/save/apply", include_in_schema=False)
    def apply_outcome_observation_save(payload: ApplyOutcomeObservationRequest) -> Response:
        """Revalidate current Journal target before applying a linked Outcome."""

        try:
            draft = _outcome_observation_from_payload(payload.outcome)
        except OutcomeObservationDraftError, LlmError, ValueError, TypeError, UnicodeError:
            return _error_response("OUTCOME_OBSERVATION_INVALID_REQUEST")

        try:
            claims = review_tokens.verify_outcome_observation_confirmation(
                payload.confirmation_token,
                draft,
            )
        except ReviewTokenError:
            return _error_response("STAGE2_SAVE_CONFIRMATION_INVALID")

        try:
            result = saver.apply_outcome_observation(draft, plan_sha256=claims.plan_sha256)
        except ConfigurationError, WriteSafetyError, OSError:
            return _error_response("VAULT_UNAVAILABLE")
        except Exception:
            return _error_response("SAVE_FAILED")
        return _save_response(result)

    @app.post("/api/timeline", include_in_schema=False)
    def timeline(payload: TimelineRequestPayload) -> Response:
        """Return one fresh bounded projection of the canonical Timeline result."""

        request = PersonalTimelineRequest(
            order=cast(TimelineOrder, payload.order),
            known_limit=payload.known_limit,
            unknown_limit=payload.unknown_limit,
        )
        try:
            validate_personal_timeline_request(request)
            response = timeline_response(timeliner.build(request))
        except TimelineError as error:
            return _error_response(error.code)
        except ConfigurationError, OSError:
            return _error_response("TIMELINE_VAULT_UNAVAILABLE")
        except Exception:
            return _error_response("TIMELINE_INTERNAL_ERROR")
        return JSONResponse(
            content=response.model_dump(mode="json"),
            headers=_DRAFT_ERROR_HEADERS,
        )

    @app.post("/api/self-model", include_in_schema=False)
    def self_model(payload: SelfModelRequestPayload) -> Response:
        """Return one fresh bounded projection of the canonical Self Model result."""

        request = SelfModelRequest(
            max_claims=payload.max_claims,
            max_evidence_refs_per_claim=payload.max_evidence_refs_per_claim,
        )
        try:
            validate_self_model_request(request)
            response = self_model_response(self_modeler.build(request), request)
        except SelfModelError as error:
            return _error_response(error.code)
        except ConfigurationError, OSError:
            return _error_response("SELF_MODEL_VAULT_UNAVAILABLE")
        except Exception:
            return _error_response("SELF_MODEL_RESULT_INVALID")
        return JSONResponse(
            content=response.model_dump(mode="json"),
            headers=_DRAFT_ERROR_HEADERS,
        )

    @app.post("/api/self-retrieval", include_in_schema=False)
    def self_retrieval(payload: SelfRetrievalRequestPayload) -> Response:
        """Return one fresh bounded projection of the core Self Retrieval result."""

        request = SelfContextRequest(
            query=payload.query,
            limit=payload.limit,
            max_content_bytes=payload.max_content_bytes,
        )
        try:
            validate_self_context_request(request)
            response = self_retrieval_response(self_retriever.build(request), request)
        except SelfRetrievalError as error:
            return _error_response(error.code)
        except ConfigurationError, OSError:
            return _error_response("SELF_RETRIEVAL_SEARCH_UNAVAILABLE")
        except Exception:
            return _error_response("SELF_RETRIEVAL_RESULT_INVALID")
        return JSONResponse(
            content=response.model_dump(mode="json"),
            headers=_DRAFT_ERROR_HEADERS,
        )

    @app.post("/api/simulate-me", include_in_schema=False)
    def simulate_me_route(payload: SimulateMeRequestPayload) -> Response:
        """Return one thin read-only projection of the Simulate Me core result."""

        try:
            request = simulate_me_request(payload)
            result = simulate_me.build(request)
            response = simulate_me_response(result, request)
        except SimulateMeError as error:
            return _error_response(error.code)
        except ConfigurationError, OSError:
            return _error_response("SIMULATE_ME_VAULT_UNAVAILABLE")
        except Exception:
            return _error_response("SIMULATE_ME_RESULT_INVALID")
        return JSONResponse(
            content=response.model_dump(mode="json"),
            headers=_DRAFT_ERROR_HEADERS,
        )

    @app.post("/api/diagnostics", include_in_schema=False)
    def diagnostics(payload: DiagnosticsRequestPayload) -> Response:
        """Return the exact safe DoctorReport projection on explicit refresh."""

        del payload
        try:
            report = doctor.build()
            response = report.as_dict()
        except Exception:
            return _error_response("DIAGNOSTICS_INTERNAL_ERROR")
        return JSONResponse(content=response, headers=_DRAFT_ERROR_HEADERS)

    @app.post("/api/search", include_in_schema=False)
    def search(payload: SearchRequestPayload) -> Response:
        """Search current private vault through a fresh derived FTS5 index."""

        try:
            hits = searcher.search(SearchRequest(query=payload.query, limit=payload.limit))
            response = SearchResponse(hits=[_search_hit_payload(hit) for hit in hits])
        except SearchError as error:
            return _error_response(error.code)
        except ConfigurationError, OSError:
            return _error_response("SEARCH_BACKEND_UNAVAILABLE")
        except Exception:
            return _error_response("SEARCH_QUERY_FAILED")
        return JSONResponse(
            content=response.model_dump(mode="json"),
            headers=_DRAFT_ERROR_HEADERS,
        )

    @app.post("/api/retrieval/note", include_in_schema=False)
    def retrieve_note(payload: RetrievalRequestPayload) -> Response:
        """Return one read-only current note after canonical vault re-scan."""

        try:
            note_id = parse_uuid7(payload.id)
        except ValueError:
            return _error_response("SEARCH_INVALID_REQUEST")
        try:
            note = searcher.retrieve(note_id)
            response = RetrievalResponse(note=_retrieved_note_payload(note))
        except SearchError as error:
            return _error_response(error.code)
        except ConfigurationError, OSError:
            return _error_response("SEARCH_BACKEND_UNAVAILABLE")
        except Exception:
            return _error_response("SEARCH_QUERY_FAILED")
        return JSONResponse(
            content=response.model_dump(mode="json"),
            headers=_DRAFT_ERROR_HEADERS,
        )

    @app.get("/", include_in_schema=False)
    def index(request: Request) -> Response:
        return react_index(request)

    @app.get("/react", include_in_schema=False)
    @app.get("/react/", include_in_schema=False)
    def react_index(request: Request) -> Response:
        """Serve the single production React GUI after the parity build."""

        auth_mode = cast(
            WebAuthMode,
            getattr(request.app.state, AUTH_MODE_APP_STATE_KEY, WEB_AUTH_DISABLED),
        )
        if auth_mode not in {WEB_AUTH_DISABLED, WEB_AUTH_GITHUB}:
            auth_mode = WEB_AUTH_DISABLED
        return web_index_response(REACT_INDEX_FILE, auth_mode=auth_mode)

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def pwa_manifest() -> Response:
        """Serve the public install manifest without exposing application data."""

        return _pwa_file_response(
            REACT_PWA_MANIFEST_FILE,
            media_type="application/manifest+json",
        )

    @app.get("/sw.js", include_in_schema=False)
    def pwa_service_worker() -> Response:
        """Serve the static service worker entrypoint with revalidation."""

        return _pwa_file_response(
            REACT_PWA_SERVICE_WORKER_FILE,
            media_type="application/javascript",
        )

    @app.get("/offline.html", include_in_schema=False)
    def pwa_offline_page() -> Response:
        """Serve the non-private offline fallback page."""

        return _pwa_file_response(REACT_PWA_OFFLINE_FILE, media_type="text/html")

    @app.get("/offline.css", include_in_schema=False)
    def pwa_offline_styles() -> Response:
        """Serve the static styles for the offline fallback page."""

        return _pwa_file_response(REACT_PWA_OFFLINE_STYLES_FILE, media_type="text/css")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> JSONResponse:
        return JSONResponse(content={"status": "ok"})

    if REACT_ASSETS_DIR.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=REACT_ASSETS_DIR, html=False, check_dir=True),
            name="react-assets",
        )
        app.mount(
            "/react/assets",
            StaticFiles(directory=REACT_ASSETS_DIR, html=False, check_dir=True),
            name="react-assets-compat",
        )
    if REACT_PWA_ICONS_DIR.is_dir():
        app.mount(
            "/icons",
            StaticFiles(directory=REACT_PWA_ICONS_DIR, html=False, check_dir=True),
            name="pwa-icons",
        )
    return app


def _search_hit_payload(hit: SearchHit) -> SearchHitPayload:
    """Сериализовать SearchHit без absolute path, score или full body."""

    relative_path = _safe_relative_path(hit.relative_path)
    if relative_path is None:
        raise ValueError("search hit path is not a safe relative path")
    return SearchHitPayload(
        id=str(hit.note_id),
        type=hit.note_type.value,
        title=hit.title,
        relative_path=relative_path,
        tags=list(hit.tags),
        created=hit.created,
        updated=hit.updated,
        snippet=hit.snippet,
    )


def _retrieved_note_payload(note: RetrievedNote) -> RetrievedNotePayload:
    """Сериализовать current canonical note без arbitrary front matter."""

    relative_path = _safe_relative_path(note.relative_path)
    if relative_path is None:
        raise ValueError("retrieved note path is not a safe relative path")
    return RetrievedNotePayload(
        id=str(note.note_id),
        type=note.note_type.value,
        title=note.title,
        relative_path=relative_path,
        tags=list(note.tags),
        created=note.created,
        updated=note.updated,
        content=note.body,
    )


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


def _personal_memory_from_payload(
    draft_payload: DraftPayload,
    personal_memory_payload: PersonalMemoryPayload,
) -> PersonalMemoryDraft:
    """Reconstruct and validate the existing Stage 1 wrapper from controlled HTTP fields."""

    draft = _note_draft_from_payload(draft_payload)
    return validate_personal_memory_draft(
        PersonalMemoryDraft(
            draft=draft,
            evidence_kind=personal_memory_payload.evidence_kind,
            self_kind=personal_memory_payload.self_kind,
            evidence_at=personal_memory_payload.evidence_at,
            evidence_at_precision=personal_memory_payload.evidence_at_precision,
            domain=personal_memory_payload.domain,
        )
    )


def _decision_journal_from_payload(
    payload: DecisionJournalPayload,
) -> DecisionJournalDraft:
    """Build and normalize the server-owned Journal draft from structured fields."""

    draft = DecisionJournalDraft(
        draft=NoteDraft(
            title=payload.title,
            note_type=NoteType(payload.note_type),
            content=render_decision_journal_body(
                situation=payload.situation,
                available_options=payload.available_options,
                information_known_at_decision_time=payload.information_known_at_decision_time,
                criteria=payload.criteria,
                chosen_option=payload.chosen_option,
                reasons=payload.reasons,
                confidence=payload.confidence,
                expected_result=payload.expected_result,
            ),
            tags=tuple(payload.tags),
            links=tuple(payload.links),
        ),
        evidence_at=payload.evidence_at,
        evidence_at_precision=payload.evidence_at_precision,
        domain=payload.domain,
    )
    return validate_decision_journal_draft(draft)


def _outcome_observation_from_payload(
    payload: OutcomeObservationPayload,
) -> OutcomeObservationDraft:
    """Build and normalize the server-owned Outcome draft from structured fields."""

    draft = OutcomeObservationDraft(
        draft=NoteDraft(
            title=payload.title,
            note_type=NoteType(payload.note_type),
            content=render_outcome_observation_body(
                actual_result=payload.actual_result,
                reassessment=payload.reassessment,
                notes=payload.notes,
            ),
            tags=tuple(payload.tags),
            links=tuple(payload.links),
        ),
        decision_id=parse_uuid7(payload.decision_id),
        evidence_at=payload.evidence_at,
        evidence_at_precision=payload.evidence_at_precision,
        domain=payload.domain,
    )
    return validate_outcome_observation_draft(draft)


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


def _prepare_personal_memory_response(
    result: CreateManagedNoteResult,
    *,
    review_token: str,
    personal_memory: PersonalMemoryDraft,
    review_tokens: ReviewTokenCodec,
) -> JSONResponse:
    """Serialize a PM dry-run with its purpose-separated confirmation token."""

    if result.status is CreateStatus.CREATED:
        return _error_response("SAVE_FAILED")
    if result.status is not CreateStatus.DRY_RUN or result.plan is None:
        return _save_response(result)
    relative_path = _safe_relative_path(result.plan.relative_path)
    if relative_path is None:
        return _error_response("SAVE_FAILED")
    try:
        diff = _plan_diff(result.plan, relative_path)
        confirmation_token = review_tokens.issue_personal_memory_confirmation(
            review_token=review_token,
            draft=personal_memory.draft,
            metadata=personal_memory.metadata,
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


def _prepare_stage2_response(
    result: CreateManagedNoteResult,
    *,
    draft: DecisionJournalDraft | OutcomeObservationDraft,
    review_tokens: ReviewTokenCodec,
    decision: bool,
) -> JSONResponse:
    """Serialize a Stage 2 dry-run and issue only its matching confirmation."""

    if result.status is CreateStatus.CREATED:
        return _error_response("SAVE_FAILED")
    if result.status is not CreateStatus.DRY_RUN or result.plan is None:
        return _save_response(result)
    relative_path = _safe_relative_path(result.plan.relative_path)
    if relative_path is None:
        return _error_response("SAVE_FAILED")
    plan_sha256 = result.plan.plan_sha256
    if type(plan_sha256) is not str:
        return _error_response("SAVE_FAILED")
    try:
        diff = _plan_diff(result.plan, relative_path)
        if decision:
            if not isinstance(draft, DecisionJournalDraft):
                raise ReviewTokenError()
            confirmation_token = review_tokens.issue_decision_journal_confirmation(
                draft,
                plan_sha256=plan_sha256,
            )
        else:
            if not isinstance(draft, OutcomeObservationDraft):
                raise ReviewTokenError()
            confirmation_token = review_tokens.issue_outcome_observation_confirmation(
                draft,
                plan_sha256=plan_sha256,
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

    if isinstance(
        error,
        (
            LlmError,
            ResearchError,
            TranscriptionError,
            SearchError,
            SelfRetrievalError,
            SimulateMeError,
        ),
    ):
        return _error_response(error.code)
    return _error_response(_GENERIC_ERROR[1])


__all__ = [
    "ACTIVE_LEARNING_ANSWER_REVIEW_PATH",
    "CONTENT_SECURITY_POLICY",
    "DRAFT_REQUEST_HEADER_NAME",
    "DRAFT_REQUEST_HEADER_VALUE",
    "MAX_RAW_AUDIO_BODY_BYTES",
    "MAX_RAW_DRAFT_BODY_BYTES",
    "MAX_RAW_SEARCH_BODY_BYTES",
    "MAX_RAW_SELF_MODEL_BODY_BYTES",
    "MAX_RAW_SELF_RETRIEVAL_BODY_BYTES",
    "MAX_RAW_SIMULATE_ME_BODY_BYTES",
    "MAX_RAW_TIMELINE_BODY_BYTES",
    "MAX_RAW_TRANSCRIPTION_BODY_BYTES",
    "REACT_PWA_ICONS_DIR",
    "REACT_PWA_MANIFEST_FILE",
    "REACT_PWA_OFFLINE_FILE",
    "REACT_PWA_OFFLINE_STYLES_FILE",
    "REACT_PWA_SERVICE_WORKER_FILE",
    "SEARCH_REQUEST_HEADER_NAME",
    "SEARCH_REQUEST_HEADER_VALUE",
    "SELF_MODEL_REQUEST_HEADER_NAME",
    "SELF_MODEL_REQUEST_HEADER_VALUE",
    "SELF_RETRIEVAL_REQUEST_HEADER_NAME",
    "SELF_RETRIEVAL_REQUEST_HEADER_VALUE",
    "SIMULATE_ME_REQUEST_HEADER_NAME",
    "SIMULATE_ME_REQUEST_HEADER_VALUE",
    "TIMELINE_REQUEST_HEADER_NAME",
    "TIMELINE_REQUEST_HEADER_VALUE",
    "TRANSCRIPTION_REQUEST_HEADER_NAME",
    "TRANSCRIPTION_REQUEST_HEADER_VALUE",
    "ActiveLearningAnswerReviewRequest",
    "ApplyDecisionJournalRequest",
    "ApplyDraftRequest",
    "ApplyOutcomeObservationRequest",
    "ApplyPersonalMemoryRequest",
    "DecisionJournalPayload",
    "DraftPayload",
    "DraftRequestBoundaryMiddleware",
    "DraftResponse",
    "DryRunNotePayload",
    "ErrorResponse",
    "OutcomeObservationPayload",
    "PersonalMemoryPayload",
    "PrepareDecisionJournalRequest",
    "PrepareDraftRequest",
    "PrepareOutcomeObservationRequest",
    "PreparePersonalMemoryRequest",
    "PrepareResponse",
    "PreviewDraftRequest",
    "PreviewResponse",
    "RetrievalRequestPayload",
    "RetrievalResponse",
    "RetrievedNotePayload",
    "SaveResponse",
    "SavedNotePayload",
    "SearchHitPayload",
    "SearchRequestBoundaryMiddleware",
    "SearchRequestPayload",
    "SearchResponse",
    "SelfModelRequestBoundaryMiddleware",
    "SelfModelRequestPayload",
    "SelfModelResponse",
    "SelfRetrievalRequestBoundaryMiddleware",
    "SelfRetrievalRequestPayload",
    "SelfRetrievalResponse",
    "SimulateMeContextualEvidenceRefPayload",
    "SimulateMeEvidenceRefPayload",
    "SimulateMeOptionPayload",
    "SimulateMeRequestBoundaryMiddleware",
    "SimulateMeRequestPayload",
    "SimulateMeResponse",
    "SimulateMeTemporalCaveatPayload",
    "SourceProvenancePayload",
    "TextDraftRequest",
    "TimelineRequestBoundaryMiddleware",
    "TimelineRequestPayload",
    "TimelineResponse",
    "TranscriptPayload",
    "TranscriptionRequestBoundaryMiddleware",
    "TranscriptionResponse",
    "UrlDraftRequest",
    "create_app",
]
