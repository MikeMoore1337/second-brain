"""Bounded owner-only Web/API projection for Active Personal Learning v1."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Final, Protocol

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.active_personal_learning import (
    ActiveLearningCandidateStaleError,
    ActiveLearningError,
    ActiveLearningErrorCodeV1,
    ActiveLearningOperationStateV1,
    ActiveLearningResolutionResultV1,
    ActiveLearningResultV1,
    ActiveLearningSourceV1,
    AnswerCaptureV1,
    BuildActiveLearningQuestion,
    QuestionCandidateV1,
    QuestionDispositionV1,
    QuestionOptionV1,
    QuestionReasonCodeV1,
    QuestionResolutionV1,
    resolve_active_learning_question,
    serialize_active_learning_result,
    validate_question_candidate,
)
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    BuildSelfModel,
    SelfModelRequest,
    SelfModelResult,
)
from second_brain.application.simulate_me import (
    BuildSimulateMe,
    SimulateMeError,
    SimulateMeOption,
    SimulateMeRequest,
    validate_simulate_me_request,
)
from second_brain.config import load_config
from second_brain.domain.models import parse_uuid7
from second_brain.entrypoints.web.legacy_app import SelfRetrievalRequestBoundaryMiddleware

ACTIVE_LEARNING_QUESTIONS_PATH: Final[str] = "/api/active-learning/questions"
ACTIVE_LEARNING_RESOLVE_PATH: Final[str] = "/api/active-learning/questions/resolve"
ACTIVE_LEARNING_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
ACTIVE_LEARNING_REQUEST_HEADER_VALUE: Final[str] = "active-learning-v1"
MAX_RAW_ACTIVE_LEARNING_BODY_BYTES: Final[int] = 16 * 1024

_ACTIVE_LEARNING_PATHS: Final[frozenset[str]] = frozenset(
    {ACTIVE_LEARNING_QUESTIONS_PATH, ACTIVE_LEARNING_RESOLVE_PATH}
)
_API_HEADERS: Final[dict[str, str]] = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; connect-src 'self'; font-src 'self'; "
        "form-action 'none'; frame-ancestors 'none'; img-src 'self'; object-src 'none'; "
        "script-src 'self'; style-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
_ACTIVE_LEARNING_MESSAGES: Mapping[str, tuple[int, str]] = {
    ActiveLearningErrorCodeV1.INVALID_REQUEST.value: (
        400,
        "Запрос уточнения модели недопустим.",
    ),
    ActiveLearningErrorCodeV1.SOURCE_INVALID.value: (
        503,
        "Текущая модель для уточнения недоступна.",
    ),
    ActiveLearningErrorCodeV1.SOURCE_UNAVAILABLE.value: (
        503,
        "Текущая модель для уточнения недоступна.",
    ),
    ActiveLearningErrorCodeV1.CANDIDATE_STALE.value: (
        409,
        "Вопрос уточнения устарел.",
    ),
    ActiveLearningErrorCodeV1.CANDIDATE_EXPIRED.value: (
        409,
        "Срок действия вопроса уточнения истёк.",
    ),
    ActiveLearningErrorCodeV1.CANDIDATE_ALREADY_RESOLVED.value: (
        409,
        "Вопрос уточнения уже закрыт.",
    ),
    ActiveLearningErrorCodeV1.INVALID_ANSWER.value: (
        400,
        "Ответ на вопрос уточнения недопустим.",
    ),
    ActiveLearningErrorCodeV1.RESULT_TOO_LARGE.value: (
        500,
        "Результат уточнения модели слишком велик.",
    ),
}


class ActiveLearningOptionPayload(BaseModel):
    """Strict request-local option; no client evidence or source fields exist."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: StrictStr
    label: StrictStr


class ActiveLearningQuestionsRequestPayload(BaseModel):
    """Strict JSON input for one explicit question-surface operation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: StrictStr
    options: list[ActiveLearningOptionPayload]


class ActiveLearningCandidatePayload(BaseModel):
    """Strict client echo of the bounded candidate shown by the question UI."""

    model_config = ConfigDict(extra="forbid", strict=True)

    contract_version: StrictStr
    candidate_id: StrictStr
    kind: StrictStr
    reason_code: StrictStr
    task: StrictStr
    question: StrictStr
    options: list[ActiveLearningOptionPayload]
    source: StrictStr
    source_derivation_version: StrictStr
    source_policy_id: StrictStr
    source_policy_fingerprint: StrictStr
    evidence_note_ids: list[StrictStr]
    basis_fingerprint: StrictStr
    issued_at: StrictStr
    expires_at: StrictStr


class ActiveLearningResolutionPayload(BaseModel):
    """Strict terminal control DTO; it has no evidence or write fields."""

    model_config = ConfigDict(extra="forbid", strict=True)

    candidate_id: StrictStr
    disposition: StrictStr
    selected_option_id: StrictStr | None = None


class ActiveLearningResolveRequestPayload(BaseModel):
    """Strict request for server-side candidate revalidation and handoff."""

    model_config = ConfigDict(extra="forbid", strict=True)

    candidate: ActiveLearningCandidatePayload
    resolution: ActiveLearningResolutionPayload


class ActiveLearningAnswerCapturePayload(BaseModel):
    """Safe answer-only projection with no source, evidence, or vault fields."""

    model_config = ConfigDict(extra="forbid", strict=True)

    task: StrictStr
    option: ActiveLearningOptionPayload


class ActiveLearningResolutionResponsePayload(BaseModel):
    """Exact disposable resolution response for the editable answer handoff."""

    model_config = ConfigDict(extra="forbid", strict=True)

    candidate_id: StrictStr
    disposition: StrictStr
    answer_capture: ActiveLearningAnswerCapturePayload | None = None


class ActiveLearningWebService(Protocol):
    """Minimal injectable seam over the Stage 8 application core."""

    def execute(self, request: SimulateMeRequest) -> ActiveLearningResultV1:
        """Build one ephemeral candidate/no-candidate result."""


class ActiveLearningResolutionService(Protocol):
    """Minimal injectable seam for server-side candidate revalidation."""

    def resolve(
        self,
        request: SimulateMeRequest,
        candidate: QuestionCandidateV1,
        resolution: QuestionResolutionV1,
    ) -> ActiveLearningResolutionResultV1:
        """Revalidate the current candidate and return only an ephemeral handoff."""


@dataclass(frozen=True, slots=True)
class _SnapshotSelfModelBuilder:
    """Expose one already-built server snapshot to the Simulate Me core."""

    result: SelfModelResult

    def execute(self, request: SelfModelRequest) -> SelfModelResult:
        if type(request) is not SelfModelRequest:
            raise ValueError
        return self.result


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class LazyVaultActiveLearningService:
    """Read the current vault only after the owner explicitly requests a question."""

    env_file: Path | None = None
    vault_path_override: str | None = None
    clock: Callable[[], datetime] = _utc_now
    _last_candidate: QuestionCandidateV1 | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _state_lock: Lock = field(
        default_factory=Lock,
        init=False,
        repr=False,
        compare=False,
    )

    def _current_source(self, request: SimulateMeRequest) -> ActiveLearningSourceV1:
        """Compose one current server-owned snapshot without writes or providers."""
        validated_request = validate_simulate_me_request(request)
        config = load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )
        reader = FileSystemVaultReader(config.vault_path)
        self_model = BuildSelfModel(
            reader,
            policy=DEFAULT_SELF_MODEL_POLICY,
            clock=self.clock,
        ).execute(SelfModelRequest())
        simulate_me_result = BuildSimulateMe(
            self_model=_SnapshotSelfModelBuilder(self_model),
            clock=self.clock,
        ).execute(validated_request)
        return ActiveLearningSourceV1(
            self_model=self_model,
            simulate_me_request=validated_request,
            simulate_me_result=simulate_me_result,
        )

    def execute(self, request: SimulateMeRequest) -> ActiveLearningResultV1:
        """Build one current candidate/no-candidate result without a write seam."""

        with self._state_lock:
            source = self._current_source(request)
            result = BuildActiveLearningQuestion(clock=self.clock).execute(
                source,
                questions_enabled=True,
            )
            object.__setattr__(self, "_last_candidate", result.candidate)
            return result

    def resolve(
        self,
        request: SimulateMeRequest,
        candidate: QuestionCandidateV1,
        resolution: QuestionResolutionV1,
    ) -> ActiveLearningResolutionResultV1:
        """Revalidate against the current source before handing off an answer."""

        with self._state_lock:
            if self._last_candidate is None or self._last_candidate != candidate:
                raise ActiveLearningCandidateStaleError()
            source = self._current_source(request)
            result = resolve_active_learning_question(
                source,
                candidate,
                resolution,
                now=self.clock(),
                operation_state=ActiveLearningOperationStateV1(
                    candidate_id=candidate.candidate_id,
                    terminal=False,
                ),
            )
            object.__setattr__(self, "_last_candidate", None)
            return result


def build_production_active_learning_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> LazyVaultActiveLearningService:
    """Create a lazy service with no config, vault, persistence, or network side effects."""

    return LazyVaultActiveLearningService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


def active_learning_request(
    payload: ActiveLearningQuestionsRequestPayload,
) -> SimulateMeRequest:
    """Convert the strict Web DTO to the existing Simulate Me request contract."""

    return validate_simulate_me_request(
        SimulateMeRequest(
            query=payload.query,
            options=tuple(
                SimulateMeOption(id=option.id, label=option.label) for option in payload.options
            ),
        )
    )


def active_learning_candidate(
    payload: ActiveLearningCandidatePayload,
) -> QuestionCandidateV1:
    """Convert the strict candidate echo into the existing core candidate DTO."""

    try:
        candidate = QuestionCandidateV1(
            contract_version=payload.contract_version,
            candidate_id=payload.candidate_id,
            kind=payload.kind,
            reason_code=QuestionReasonCodeV1(payload.reason_code),
            task=payload.task,
            question=payload.question,
            options=tuple(
                QuestionOptionV1(id=option.id, label=option.label) for option in payload.options
            ),
            source=payload.source,
            source_derivation_version=payload.source_derivation_version,
            source_policy_id=payload.source_policy_id,
            source_policy_fingerprint=payload.source_policy_fingerprint,
            evidence_note_ids=tuple(parse_uuid7(note_id) for note_id in payload.evidence_note_ids),
            basis_fingerprint=payload.basis_fingerprint,
            issued_at=datetime.fromisoformat(payload.issued_at),
            expires_at=datetime.fromisoformat(payload.expires_at),
        )
        return validate_question_candidate(candidate)
    except ActiveLearningError:
        raise
    except TypeError, ValueError, UnicodeError:
        raise ValueError from None


def active_learning_resolution(
    payload: ActiveLearningResolutionPayload,
) -> QuestionResolutionV1:
    """Convert the strict terminal control into the existing core DTO."""

    try:
        return QuestionResolutionV1(
            candidate_id=payload.candidate_id,
            disposition=QuestionDispositionV1(payload.disposition),
            selected_option_id=payload.selected_option_id,
        )
    except TypeError, ValueError, UnicodeError:
        raise ValueError from None


def _resolution_response(result: object) -> JSONResponse:
    """Serialize only the bounded answer handoff and terminal disposition."""

    if type(result) is not ActiveLearningResolutionResultV1:
        raise ValueError
    assert isinstance(result, ActiveLearningResolutionResultV1)
    capture = result.answer_capture
    answer_capture = None
    if capture is not None:
        if type(capture) is not AnswerCaptureV1:
            raise ValueError
        answer_capture = ActiveLearningAnswerCapturePayload(
            task=capture.task,
            option=ActiveLearningOptionPayload(
                id=capture.option.id,
                label=capture.option.label,
            ),
        )
    response = ActiveLearningResolutionResponsePayload(
        candidate_id=result.candidate_id,
        disposition=result.disposition.value,
        answer_capture=answer_capture,
    )
    return JSONResponse(
        content=response.model_dump(mode="json", exclude_none=False),
        headers=_API_HEADERS,
    )


def _safe_error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=_API_HEADERS,
    )


def _active_learning_error(error: ActiveLearningError) -> JSONResponse:
    fallback = (
        503,
        "Текущая модель для уточнения недоступна.",
    )
    status, message = _ACTIVE_LEARNING_MESSAGES.get(error.code, fallback)
    code = (
        error.code
        if error.code in _ACTIVE_LEARNING_MESSAGES
        else ActiveLearningErrorCodeV1.SOURCE_UNAVAILABLE.value
    )
    return _safe_error(code, message, status)


def _invalid_request() -> JSONResponse:
    status, message = _ACTIVE_LEARNING_MESSAGES[ActiveLearningErrorCodeV1.INVALID_REQUEST.value]
    return _safe_error(ActiveLearningErrorCodeV1.INVALID_REQUEST.value, message, status)


def _too_large() -> JSONResponse:
    return _safe_error(
        "ACTIVE_LEARNING_CONTENT_TOO_LARGE",
        "Запрос уточнения модели слишком велик.",
        413,
    )


def _method_not_allowed() -> JSONResponse:
    response = _invalid_request()
    response.status_code = 405
    response.headers["Allow"] = "POST"
    return response


class ActiveLearningRequestBoundaryMiddleware(SelfRetrievalRequestBoundaryMiddleware):
    """Reuse the current fail-closed trusted same-origin JSON boundary."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        super().__init__(
            app,
            max_body_bytes=max_body_bytes,
            paths=_ACTIVE_LEARNING_PATHS,
            request_header_value=ACTIVE_LEARNING_REQUEST_HEADER_VALUE,
        )


def install_active_learning_routes(
    app: FastAPI,
    *,
    service: ActiveLearningWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install one explicit owner-only question endpoint over the existing core."""

    active_learning = service or build_production_active_learning_service(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    app.add_middleware(
        ActiveLearningRequestBoundaryMiddleware,
        max_body_bytes=MAX_RAW_ACTIVE_LEARNING_BODY_BYTES,
    )

    @app.post(ACTIVE_LEARNING_QUESTIONS_PATH, include_in_schema=False)
    async def active_learning_questions_endpoint(request: Request) -> Response:
        try:
            raw = await request.json()
            payload = ActiveLearningQuestionsRequestPayload.model_validate(raw, strict=True)
            typed_request = active_learning_request(payload)
            result = await run_in_threadpool(active_learning.execute, typed_request)
            body = serialize_active_learning_result(result)
        except (
            ValidationError,
            json.JSONDecodeError,
            SimulateMeError,
            UnicodeError,
            ValueError,
            TypeError,
        ):
            return _invalid_request()
        except ActiveLearningError as error:
            return _active_learning_error(error)
        except Exception:
            return _safe_error(
                ActiveLearningErrorCodeV1.SOURCE_UNAVAILABLE.value,
                "Текущая модель для уточнения недоступна.",
                503,
            )
        return Response(
            content=body,
            media_type="application/json",
            headers=_API_HEADERS,
        )

    @app.post(ACTIVE_LEARNING_RESOLVE_PATH, include_in_schema=False)
    async def active_learning_resolve_endpoint(
        payload: ActiveLearningResolveRequestPayload,
    ) -> Response:
        """Revalidate a candidate before exposing its editable answer handoff."""

        resolver = getattr(active_learning, "resolve", None)
        if not callable(resolver):
            return _invalid_request()
        try:
            candidate = active_learning_candidate(payload.candidate)
            resolution = active_learning_resolution(payload.resolution)
            typed_request = active_learning_request(
                ActiveLearningQuestionsRequestPayload(
                    query=candidate.task,
                    options=[
                        ActiveLearningOptionPayload(id=option.id, label=option.label)
                        for option in candidate.options
                    ],
                )
            )
            result = await run_in_threadpool(
                resolver,
                typed_request,
                candidate,
                resolution,
            )
            return _resolution_response(result)
        except (
            ValidationError,
            SimulateMeError,
            UnicodeError,
            ValueError,
            TypeError,
        ):
            return _invalid_request()
        except ActiveLearningError as error:
            return _active_learning_error(error)
        except Exception:
            return _safe_error(
                ActiveLearningErrorCodeV1.SOURCE_UNAVAILABLE.value,
                "Текущая модель для уточнения недоступна.",
                503,
            )


__all__ = [
    "ACTIVE_LEARNING_QUESTIONS_PATH",
    "ACTIVE_LEARNING_REQUEST_HEADER_NAME",
    "ACTIVE_LEARNING_REQUEST_HEADER_VALUE",
    "ACTIVE_LEARNING_RESOLVE_PATH",
    "MAX_RAW_ACTIVE_LEARNING_BODY_BYTES",
    "ActiveLearningAnswerCapturePayload",
    "ActiveLearningCandidatePayload",
    "ActiveLearningOptionPayload",
    "ActiveLearningQuestionsRequestPayload",
    "ActiveLearningRequestBoundaryMiddleware",
    "ActiveLearningResolutionPayload",
    "ActiveLearningResolutionResponsePayload",
    "ActiveLearningResolutionService",
    "ActiveLearningResolveRequestPayload",
    "ActiveLearningWebService",
    "LazyVaultActiveLearningService",
    "active_learning_candidate",
    "active_learning_request",
    "active_learning_resolution",
    "build_production_active_learning_service",
    "install_active_learning_routes",
]
