"""Private owner-only Web/API transport for Growth Learning v1.

The route accepts only a selected Goal UUID and an echo of the candidate that
was shown in the current page.  The application core rebuilds the current
Growth result before every resolution; this transport never writes a vault,
mapping store or question history.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Final, Protocol
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.growth import (
    BuildGrowthEngine,
    GrowthError,
    GrowthErrorCode,
    GrowthRelationStateV1,
    create_growth_engine,
)
from second_brain.application.growth_learning import (
    GROWTH_LEARNING_CONTRACT_VERSION,
    GROWTH_LEARNING_MAX_RESULT_BYTES,
    BuildGrowthLearningQuestion,
    GrowthLearningCandidateV1,
    GrowthLearningError,
    GrowthLearningErrorCodeV1,
    GrowthLearningKindV1,
    GrowthLearningOperationStateV1,
    GrowthLearningReasonCodeV1,
    GrowthLearningRequestV1,
    GrowthLearningResolutionResultV1,
    GrowthLearningResolutionV1,
    GrowthLearningResultV1,
    serialize_growth_learning_result,
    validate_growth_learning_candidate,
)
from second_brain.application.self_model import DEFAULT_SELF_MODEL_POLICY
from second_brain.config import ConfigurationError, load_config
from second_brain.domain.models import parse_rfc3339, parse_uuid7
from second_brain.entrypoints.web.auth import (
    request_host_is_trusted,
    same_origin_is_trusted,
    trusted_authorities_from_scope,
)

GROWTH_LEARNING_QUESTIONS_PATH: Final[str] = "/api/growth-learning/questions"
GROWTH_LEARNING_RESOLVE_PATH: Final[str] = "/api/growth-learning/questions/resolve"
GROWTH_LEARNING_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
GROWTH_LEARNING_REQUEST_HEADER_VALUE: Final[str] = GROWTH_LEARNING_CONTRACT_VERSION
MAX_RAW_GROWTH_LEARNING_BODY_BYTES: Final[int] = 64 * 1024
MAX_GROWTH_LEARNING_RESPONSE_BYTES: Final[int] = GROWTH_LEARNING_MAX_RESULT_BYTES

_GROWTH_LEARNING_PATHS: Final[frozenset[str]] = frozenset(
    {GROWTH_LEARNING_QUESTIONS_PATH, GROWTH_LEARNING_RESOLVE_PATH}
)
_SECURITY_HEADER_NAMES: Final[frozenset[bytes]] = frozenset(
    {
        b"host",
        b"origin",
        b"content-type",
        b"content-length",
        b"x-second-brain-request",
    }
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

_LEARNING_MESSAGES: Final[dict[str, tuple[int, str]]] = {
    GrowthLearningErrorCodeV1.INVALID_REQUEST.value: (400, "Запрос уточнения Growth недопустим."),
    GrowthLearningErrorCodeV1.GROWTH_SOURCE_UNAVAILABLE.value: (
        503,
        "Текущий Growth-источник недоступен.",
    ),
    GrowthLearningErrorCodeV1.GOAL_SOURCE_CHANGED.value: (
        409,
        "Источник выбранной цели изменился.",
    ),
    GrowthLearningErrorCodeV1.BEHAVIORAL_SOURCE_CHANGED.value: (
        409,
        "Наблюдаемый источник изменился.",
    ),
    GrowthLearningErrorCodeV1.MAPPING_SOURCE_UNAVAILABLE.value: (
        503,
        "Источник сопоставлений Growth недоступен.",
    ),
    GrowthLearningErrorCodeV1.MAPPING_STORE_CORRUPT.value: (
        503,
        "Хранилище сопоставлений Growth не прошло проверку целостности.",
    ),
    GrowthLearningErrorCodeV1.POLICY_MISMATCH.value: (500, "Политика Growth Learning недоступна."),
    GrowthLearningErrorCodeV1.STALE_CANDIDATE.value: (
        409,
        "Вопрос Growth устарел; запроси новый.",
    ),
    GrowthLearningErrorCodeV1.CANDIDATE_EXPIRED.value: (
        409,
        "Срок действия вопроса Growth истёк.",
    ),
    GrowthLearningErrorCodeV1.CANDIDATE_ALREADY_RESOLVED.value: (
        409,
        "Вопрос Growth уже закрыт.",
    ),
    GrowthLearningErrorCodeV1.INVALID_RESOLUTION.value: (
        400,
        "Действие с вопросом Growth недопустимо.",
    ),
    GrowthLearningErrorCodeV1.ANSWER_TOO_LARGE.value: (
        413,
        "Ответ на вопрос Growth слишком велик.",
    ),
    GrowthLearningErrorCodeV1.RESULT_TOO_LARGE.value: (
        413,
        "Результат вопроса Growth слишком велик.",
    ),
    GrowthLearningErrorCodeV1.INTERNAL_CONTRACT_VIOLATION.value: (
        500,
        "Источник Growth Learning нарушил контракт.",
    ),
}


class GrowthLearningQuestionsPayload(BaseModel):
    """Strict request with no client-supplied Growth result or Goal text."""

    model_config = ConfigDict(extra="forbid", strict=True)

    contract_version: StrictStr
    goal_source_uuid: StrictStr | None


class GrowthLearningCandidatePayload(BaseModel):
    """Strict echo of the bounded candidate shown to the owner."""

    model_config = ConfigDict(extra="forbid", strict=True)

    contract_version: StrictStr
    derivation_version: StrictStr
    candidate_id: StrictStr
    kind: StrictStr
    reason_code: StrictStr
    growth_contract_version: StrictStr
    growth_derivation_version: StrictStr
    growth_policy_id: StrictStr
    growth_policy_fingerprint: StrictStr
    goal_source_uuid: StrictStr
    goal_identity_fingerprint: StrictStr
    growth_state: StrictStr
    cohort_fingerprint: StrictStr | None
    behavioral_option_fingerprint: StrictStr | None
    behavioral_reference_fingerprint: StrictStr | None
    mapping_id: StrictStr | None
    mapping_fingerprint: StrictStr | None
    question: StrictStr
    basis_fingerprint: StrictStr
    issued_at: StrictStr
    expires_at: StrictStr


class GrowthLearningResolutionPayload(BaseModel):
    """One explicit terminal owner control."""

    model_config = ConfigDict(extra="forbid", strict=True)

    candidate_id: StrictStr
    disposition: StrictStr
    answer: StrictStr | None


class GrowthLearningResolveRequestPayload(BaseModel):
    """Candidate echo plus a bounded explicit resolution."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request: GrowthLearningQuestionsPayload
    candidate: GrowthLearningCandidatePayload
    resolution: GrowthLearningResolutionPayload


class GrowthLearningWebService(Protocol):
    """Minimal injectable seam over the provider-free Learning runtime."""

    def questions(self, request: GrowthLearningRequestV1) -> GrowthLearningResultV1:
        """Build zero or one current candidate."""

    def resolve(
        self,
        request: GrowthLearningRequestV1,
        candidate: GrowthLearningCandidateV1,
        resolution: GrowthLearningResolutionV1,
    ) -> GrowthLearningResolutionResultV1:
        """Freshly validate one explicit resolution."""


@dataclass(slots=True)
class ProductionGrowthLearningWebService:
    """Lazy current-vault Learning runtime with request-local page memory."""

    env_file: Path | None = None
    vault_path_override: str | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    _state: GrowthLearningOperationStateV1 = field(
        default_factory=GrowthLearningOperationStateV1,
        init=False,
        repr=False,
    )
    _goal_source_uuid: UUID | str | None = field(default=None, init=False, repr=False)
    _state_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def _engine(self) -> BuildGrowthEngine:
        config = load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )
        if config.env_file is None:
            raise GrowthError(GrowthErrorCode.MAPPING_STORE_UNAVAILABLE)
        repository_root = Path(__file__).resolve().parents[4]
        return create_growth_engine(
            FileSystemVaultReader(config.vault_path),
            config.env_file,
            policy=DEFAULT_SELF_MODEL_POLICY,
            clock=self.clock,
            vault_root=config.vault_path,
            repository_root=repository_root,
        )

    def _builder(self) -> BuildGrowthLearningQuestion:
        return BuildGrowthLearningQuestion(
            growth_engine=self._engine(),
            clock=self.clock,
        )

    def questions(self, request: GrowthLearningRequestV1) -> GrowthLearningResultV1:
        with self._state_lock:
            if request.goal_source_uuid != self._goal_source_uuid:
                self._state = GrowthLearningOperationStateV1()
                self._goal_source_uuid = request.goal_source_uuid
            builder = self._builder()
            result = builder.execute(
                request,
                operation_state=self._state,
                now=self.clock(),
            )
            if result.candidate is not None:
                self._state = self._state.with_candidate(result.candidate)
            return result

    def resolve(
        self,
        request: GrowthLearningRequestV1,
        candidate: GrowthLearningCandidateV1,
        resolution: GrowthLearningResolutionV1,
    ) -> GrowthLearningResolutionResultV1:
        with self._state_lock:
            builder = self._builder()
            result = builder.resolve(
                request,
                candidate,
                resolution,
                operation_state=self._state,
                now=self.clock(),
            )
            self._state = result.next_state
            return result


def build_production_growth_learning_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> ProductionGrowthLearningWebService:
    return ProductionGrowthLearningWebService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


def _header(scope: Scope, name: bytes) -> tuple[bool, str | None]:
    values = [
        value.decode("latin-1")
        for header_name, value in scope.get("headers", [])
        if header_name.lower() == name
    ]
    if not values:
        return False, None
    if len(values) != 1:
        return True, None
    return True, values[0]


def _json_content_type(value: str | None) -> bool:
    if value is None:
        return False
    parts = value.split(";")
    if parts[0].strip().casefold() != "application/json":
        return False
    if len(parts) == 1:
        return True
    if len(parts) != 2:
        return False
    name, separator, parameter = parts[1].partition("=")
    if separator == "" or name.strip().casefold() != "charset":
        return False
    normalized = parameter.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] == '"':
        normalized = normalized[1:-1]
    return normalized.casefold() == "utf-8"


def _error_response(error: GrowthLearningError, *, status_code: int | None = None) -> JSONResponse:
    status, message = _LEARNING_MESSAGES.get(
        error.code,
        (503, "Операция Growth Learning сейчас недоступна."),
    )
    return JSONResponse(
        status_code=status if status_code is None else status_code,
        content={"error": {"code": error.code, "message": message}},
        headers=_API_HEADERS,
    )


def _invalid_request() -> JSONResponse:
    return _error_response(GrowthLearningError(GrowthLearningErrorCodeV1.INVALID_REQUEST))


def _too_large() -> JSONResponse:
    return _error_response(
        GrowthLearningError(GrowthLearningErrorCodeV1.RESULT_TOO_LARGE),
        status_code=413,
    )


def _method_not_allowed() -> JSONResponse:
    response = _invalid_request()
    response.status_code = 405
    response.headers["Allow"] = "POST"
    return response


async def _send(response: Response, scope: Scope, receive: Receive, send: Send) -> None:
    await response(scope, receive, send)


class GrowthLearningRequestBoundaryMiddleware:
    """Fail-closed trusted same-origin JSON boundary for both Learning routes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _GROWTH_LEARNING_PATHS:
            await self.app(scope, receive, send)
            return
        if scope.get("method") != "POST":
            await _send(_method_not_allowed(), scope, receive, send)
            return
        header_names = [name.lower() for name, _ in scope.get("headers", [])]
        if any(header_names.count(name) > 1 for name in _SECURITY_HEADER_NAMES):
            await _send(_invalid_request(), scope, receive, send)
            return
        host_present, _host = _header(scope, b"host")
        origin_present, origin = _header(scope, b"origin")
        _, purpose = _header(scope, b"x-second-brain-request")
        _, content_type = _header(scope, b"content-type")
        authorities = trusted_authorities_from_scope(scope)
        if (
            not host_present
            or not request_host_is_trusted(scope, authorities)
            or (
                origin_present
                and (origin is None or not same_origin_is_trusted(scope, origin, authorities))
            )
            or purpose != GROWTH_LEARNING_REQUEST_HEADER_VALUE
            or not _json_content_type(content_type)
        ):
            await _send(_invalid_request(), scope, receive, send)
            return
        _, content_length = _header(scope, b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length.strip())
            except ValueError:
                await _send(_invalid_request(), scope, receive, send)
                return
            if declared < 0:
                await _send(_invalid_request(), scope, receive, send)
                return
            if declared > MAX_RAW_GROWTH_LEARNING_BODY_BYTES:
                await _send(_too_large(), scope, receive, send)
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                await _send(_invalid_request(), scope, receive, send)
                return
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                await _send(_too_large(), scope, receive, send)
                return
            body.extend(chunk)
            if len(body) > MAX_RAW_GROWTH_LEARNING_BODY_BYTES:
                await _send(_too_large(), scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(value)


async def _read_json(request: Request) -> object:
    raw = await request.body()
    if not raw or len(raw) > MAX_RAW_GROWTH_LEARNING_BODY_BYTES:
        raise ValueError
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError:
        raise ValueError from None


def _uuid7(value: str) -> UUID:
    try:
        parsed = parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        raise ValueError from None
    if type(parsed) is not UUID or parsed.version != 7 or str(parsed) != value:
        raise ValueError
    return parsed


def _request(payload: GrowthLearningQuestionsPayload) -> GrowthLearningRequestV1:
    try:
        return GrowthLearningRequestV1.from_dict(payload.model_dump(mode="json"))
    except GrowthLearningError:
        raise
    except TypeError, ValueError, UnicodeError:
        raise GrowthLearningError(GrowthLearningErrorCodeV1.INVALID_REQUEST) from None


def _candidate(payload: GrowthLearningCandidatePayload) -> GrowthLearningCandidateV1:
    try:
        candidate = GrowthLearningCandidateV1(
            contract_version=payload.contract_version,
            derivation_version=payload.derivation_version,
            candidate_id=payload.candidate_id,
            kind=GrowthLearningKindV1(payload.kind),
            reason_code=GrowthLearningReasonCodeV1(payload.reason_code),
            growth_contract_version=payload.growth_contract_version,
            growth_derivation_version=payload.growth_derivation_version,
            growth_policy_id=payload.growth_policy_id,
            growth_policy_fingerprint=payload.growth_policy_fingerprint,
            goal_source_uuid=_uuid7(payload.goal_source_uuid),
            goal_identity_fingerprint=payload.goal_identity_fingerprint,
            growth_state=GrowthRelationStateV1(payload.growth_state),
            cohort_fingerprint=payload.cohort_fingerprint,
            behavioral_option_fingerprint=payload.behavioral_option_fingerprint,
            behavioral_reference_fingerprint=payload.behavioral_reference_fingerprint,
            mapping_id=_uuid7(payload.mapping_id) if payload.mapping_id is not None else None,
            mapping_fingerprint=payload.mapping_fingerprint,
            question=payload.question,
            basis_fingerprint=payload.basis_fingerprint,
            issued_at=parse_rfc3339(payload.issued_at),
            expires_at=parse_rfc3339(payload.expires_at),
        )
        return validate_growth_learning_candidate(candidate)
    except GrowthLearningError:
        raise GrowthLearningError(GrowthLearningErrorCodeV1.STALE_CANDIDATE) from None
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise GrowthLearningError(GrowthLearningErrorCodeV1.STALE_CANDIDATE) from None


def _resolution(payload: GrowthLearningResolutionPayload) -> GrowthLearningResolutionV1:
    try:
        return GrowthLearningResolutionV1.from_dict(payload.model_dump(mode="json"))
    except GrowthLearningError:
        raise
    except TypeError, ValueError, UnicodeError:
        raise GrowthLearningError(GrowthLearningErrorCodeV1.INVALID_RESOLUTION) from None


def _resolution_body(result: GrowthLearningResolutionResultV1) -> dict[str, object]:
    if type(result) is not GrowthLearningResolutionResultV1:
        raise GrowthLearningError(GrowthLearningErrorCodeV1.INTERNAL_CONTRACT_VIOLATION)
    handoff = result.handoff
    return {
        "candidate_id": result.candidate_id,
        "disposition": result.disposition.value,
        "answer_draft": (
            {"candidate_id": result.answer_draft.candidate_id, "text": result.answer_draft.text}
            if result.answer_draft is not None
            else None
        ),
        "handoff": handoff.as_dict() if handoff is not None else None,
    }


def _resolution_response(result: GrowthLearningResolutionResultV1) -> Response:
    body = _resolution_body(result)
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    if len(encoded) > MAX_GROWTH_LEARNING_RESPONSE_BYTES:
        raise GrowthLearningError(GrowthLearningErrorCodeV1.RESULT_TOO_LARGE)
    return Response(content=encoded, media_type="application/json", headers=_API_HEADERS)


def install_growth_learning_routes(
    app: FastAPI,
    *,
    service: GrowthLearningWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install only the two explicit Stage 11D Learning routes."""

    actual_service = service or build_production_growth_learning_service(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    app.add_middleware(GrowthLearningRequestBoundaryMiddleware)

    @app.post(GROWTH_LEARNING_QUESTIONS_PATH, include_in_schema=False)
    async def growth_learning_questions_endpoint(request: Request) -> Response:
        try:
            raw = await _read_json(request)
            payload = GrowthLearningQuestionsPayload.model_validate(raw, strict=True)
            result = await run_in_threadpool(actual_service.questions, _request(payload))
            body = serialize_growth_learning_result(result)
            if len(body) > MAX_GROWTH_LEARNING_RESPONSE_BYTES:
                raise GrowthLearningError(GrowthLearningErrorCodeV1.RESULT_TOO_LARGE)
            return Response(content=body, media_type="application/json", headers=_API_HEADERS)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except GrowthLearningError as error:
            return _error_response(error)
        except ConfigurationError, GrowthError:
            return _error_response(
                GrowthLearningError(GrowthLearningErrorCodeV1.GROWTH_SOURCE_UNAVAILABLE)
            )
        except Exception:
            return _error_response(
                GrowthLearningError(GrowthLearningErrorCodeV1.GROWTH_SOURCE_UNAVAILABLE)
            )

    @app.post(GROWTH_LEARNING_RESOLVE_PATH, include_in_schema=False)
    async def growth_learning_resolve_endpoint(request: Request) -> Response:
        try:
            raw = await _read_json(request)
            payload = GrowthLearningResolveRequestPayload.model_validate(raw, strict=True)
            typed_request = _request(payload.request)
            candidate = _candidate(payload.candidate)
            resolution = _resolution(payload.resolution)
            result = await run_in_threadpool(
                actual_service.resolve,
                typed_request,
                candidate,
                resolution,
            )
            return _resolution_response(result)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except GrowthLearningError as error:
            return _error_response(error)
        except ConfigurationError, GrowthError:
            return _error_response(
                GrowthLearningError(GrowthLearningErrorCodeV1.GROWTH_SOURCE_UNAVAILABLE)
            )
        except Exception:
            return _error_response(
                GrowthLearningError(GrowthLearningErrorCodeV1.GROWTH_SOURCE_UNAVAILABLE)
            )


__all__ = [
    "GROWTH_LEARNING_QUESTIONS_PATH",
    "GROWTH_LEARNING_REQUEST_HEADER_NAME",
    "GROWTH_LEARNING_REQUEST_HEADER_VALUE",
    "GROWTH_LEARNING_RESOLVE_PATH",
    "MAX_GROWTH_LEARNING_RESPONSE_BYTES",
    "MAX_RAW_GROWTH_LEARNING_BODY_BYTES",
    "GrowthLearningCandidatePayload",
    "GrowthLearningQuestionsPayload",
    "GrowthLearningRequestBoundaryMiddleware",
    "GrowthLearningResolutionPayload",
    "GrowthLearningResolveRequestPayload",
    "GrowthLearningWebService",
    "ProductionGrowthLearningWebService",
    "build_production_growth_learning_service",
    "install_growth_learning_routes",
]
