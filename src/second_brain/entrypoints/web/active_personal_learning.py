"""Bounded owner-only Web/API projection for Active Personal Learning v1."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.active_personal_learning import (
    ActiveLearningError,
    ActiveLearningErrorCodeV1,
    ActiveLearningResultV1,
    ActiveLearningSourceV1,
    BuildActiveLearningQuestion,
    serialize_active_learning_result,
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
from second_brain.entrypoints.web.legacy_app import SelfRetrievalRequestBoundaryMiddleware

ACTIVE_LEARNING_QUESTIONS_PATH: Final[str] = "/api/active-learning/questions"
ACTIVE_LEARNING_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
ACTIVE_LEARNING_REQUEST_HEADER_VALUE: Final[str] = "active-learning-v1"
MAX_RAW_ACTIVE_LEARNING_BODY_BYTES: Final[int] = 16 * 1024

_ACTIVE_LEARNING_PATHS: Final[frozenset[str]] = frozenset({ACTIVE_LEARNING_QUESTIONS_PATH})
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


class ActiveLearningWebService(Protocol):
    """Minimal injectable seam over the Stage 8 application core."""

    def execute(self, request: SimulateMeRequest) -> ActiveLearningResultV1:
        """Build one ephemeral candidate/no-candidate result."""


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

    def execute(self, request: SimulateMeRequest) -> ActiveLearningResultV1:
        """Compose one current Self Model/Simulate Me snapshot without writes or providers."""

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
        source = ActiveLearningSourceV1(
            self_model=self_model,
            simulate_me_request=validated_request,
            simulate_me_result=simulate_me_result,
        )
        return BuildActiveLearningQuestion(clock=self.clock).execute(
            source,
            questions_enabled=True,
        )


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


__all__ = [
    "ACTIVE_LEARNING_QUESTIONS_PATH",
    "ACTIVE_LEARNING_REQUEST_HEADER_NAME",
    "ACTIVE_LEARNING_REQUEST_HEADER_VALUE",
    "MAX_RAW_ACTIVE_LEARNING_BODY_BYTES",
    "ActiveLearningOptionPayload",
    "ActiveLearningQuestionsRequestPayload",
    "ActiveLearningRequestBoundaryMiddleware",
    "ActiveLearningWebService",
    "LazyVaultActiveLearningService",
    "active_learning_request",
    "build_production_active_learning_service",
    "install_active_learning_routes",
]
