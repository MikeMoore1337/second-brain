"""Owner-only Web/API transport for Personal Planning v1.

The transport is intentionally a thin boundary around the Stage 17 DTOs and
the append-only operational store.  The browser may carry an immutable
context pack or an ephemeral proposal between explicit review steps, but the
server rebuilds the pack from the current Goal and accepted Stage 16 strategy
before any provider call or store mutation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol, cast

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.adapters.advisor.cloudflare_workers_ai import CloudflareWorkersAiAdvisorPort
from second_brain.application.executive_strategy import (
    StrategySnapshotV1,
    goal_identity_fingerprint,
)
from second_brain.application.executive_strategy_store import (
    ExecutiveStrategySnapshotStore,
    ExecutiveStrategyStoreError,
    derive_executive_strategy_store_root,
)
from second_brain.application.personal_planning import (
    BuildPersonalPlanner,
    PlanningCapacityEntryV1,
    PlanningContextPackV1,
    PlanningGoalSelectionV1,
    PlanningItemV1,
    PlanningPlannerCancelledError,
    PlanningPlannerError,
    PlanningProposalV1,
    PlanningProviderEnvelopeV1,
    PlanningProviderResultInvalidError,
    PlanningWindowV1,
    build_planning_context_pack,
    build_planning_provider_envelope,
    serialize_planning_context_pack,
    validate_planning_context_pack,
    validate_planning_proposal,
)
from second_brain.application.personal_planning_store import (
    PersonalPlanningOperationalStore,
    PersonalPlanningStoreCapacityConflictError,
    PersonalPlanningStoreError,
    PersonalPlanningStoreIdempotencyConflictError,
    PersonalPlanningStoreInvalidRequestError,
    PersonalPlanningStoreSourceChangedError,
    PersonalPlanningStoreStateConflictError,
    PlanningPlanV1,
    derive_personal_planning_store_root,
)
from second_brain.application.ports import AdvisorPort, CancellationTokenSource
from second_brain.config import AppConfig, load_config
from second_brain.domain.models import parse_rfc3339
from second_brain.entrypoints.web.auth import (
    request_host_is_trusted,
    same_origin_is_trusted,
    trusted_authorities_from_scope,
)
from second_brain.entrypoints.web.growth import (
    GrowthGoalOwnerItemV1,
    GrowthWebService,
    ProductionGrowthWebService,
)

PERSONAL_PLANNING_STATE_PATH: Final[str] = "/api/personal-planning/state"
PERSONAL_PLANNING_CONTEXT_PATH: Final[str] = "/api/personal-planning/context"
PERSONAL_PLANNING_GENERATE_PATH: Final[str] = "/api/personal-planning/generate"
PERSONAL_PLANNING_ACCEPT_PATH: Final[str] = "/api/personal-planning/accept"
PERSONAL_PLANNING_EDIT_PATH: Final[str] = "/api/personal-planning/edit"
PERSONAL_PLANNING_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
PERSONAL_PLANNING_REQUEST_HEADER_VALUE: Final[str] = "personal-planning-v1"

MAX_RAW_PERSONAL_PLANNING_BODY_BYTES: Final[int] = 256 * 1024
MAX_PERSONAL_PLANNING_STATE_RESPONSE_BYTES: Final[int] = 256 * 1024
MAX_PERSONAL_PLANNING_CONTEXT_RESPONSE_BYTES: Final[int] = 256 * 1024
MAX_PERSONAL_PLANNING_GENERATE_RESPONSE_BYTES: Final[int] = 256 * 1024
MAX_PERSONAL_PLANNING_MUTATION_RESPONSE_BYTES: Final[int] = 192 * 1024

_PERSONAL_PLANNING_PATHS: Final[frozenset[str]] = frozenset(
    {
        PERSONAL_PLANNING_STATE_PATH,
        PERSONAL_PLANNING_CONTEXT_PATH,
        PERSONAL_PLANNING_GENERATE_PATH,
        PERSONAL_PLANNING_ACCEPT_PATH,
        PERSONAL_PLANNING_EDIT_PATH,
    }
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

_SOURCE_CHANGED_CODE: Final[str] = "PERSONAL_PLANNING_SOURCE_CHANGED"
_SOURCE_CHANGED_MESSAGE: Final[str] = (
    "Текущая цель или принятая стратегия изменились; собери свежий контекст."
)
_STORE_UNAVAILABLE_MESSAGE: Final[str] = "Операционное хранилище личного плана недоступно."
_GENERIC_MESSAGE: Final[str] = "Личное планирование сейчас недоступно."


class PersonalPlanningError(RuntimeError):
    """Safe application-facing transport error."""


class PersonalPlanningSourceChangedError(PersonalPlanningError):
    """The exact current Goal or accepted Stage 16 strategy changed."""


class PersonalPlanningSourceUnavailableError(PersonalPlanningError):
    """The owner-only planning sources cannot be read safely."""


class PersonalPlanningInvalidRequestError(PersonalPlanningError):
    """The typed owner request cannot be accepted."""


class PersonalPlanningEmptyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PersonalPlanningContextPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    goal_source_uuids: list[StrictStr] = Field(min_length=1, max_length=8)
    start_local: StrictStr
    end_local: StrictStr
    timezone: StrictStr
    capacity: list[dict[str, object]] = Field(min_length=1, max_length=31)
    fixed_windows: list[dict[str, object]] = Field(default_factory=list, max_length=16)
    planning_constraints: list[StrictStr] = Field(default_factory=list, max_length=8)
    planning_context: StrictStr = ""


class PersonalPlanningGeneratePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    context_pack: dict[str, object]
    provider_preview: StrictStr


class PersonalPlanningAcceptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    context_pack: dict[str, object]
    proposal: dict[str, object]
    selected_item_ids: list[StrictStr] = Field(min_length=1, max_length=32)
    item_order: list[StrictStr] = Field(min_length=1, max_length=32)
    operation_id: StrictStr
    accepted_at: StrictStr | None = None
    expected_current_plan_fingerprint: StrictStr | None = None


class PersonalPlanningEditPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[dict[str, object]] = Field(min_length=1, max_length=32)
    selected_item_ids: list[StrictStr] = Field(min_length=1, max_length=32)
    item_order: list[StrictStr] = Field(min_length=1, max_length=32)
    operation_id: StrictStr
    edited_at: StrictStr | None = None
    expected_current_plan_fingerprint: StrictStr


@dataclass(frozen=True, slots=True)
class PersonalPlanningContextRequest:
    """Normalized owner input for a provider-free deterministic pack build."""

    goal_source_uuids: tuple[str, ...]
    start_local: str
    end_local: str
    timezone: str
    capacity: tuple[PlanningCapacityEntryV1, ...]
    fixed_windows: tuple[PlanningWindowV1, ...]
    planning_constraints: tuple[str, ...]
    planning_context: str
    as_of: datetime | None = None


class PersonalPlanningWebService(Protocol):
    def state(self) -> dict[str, object]: ...

    def build_context(self, request: PersonalPlanningContextRequest) -> PlanningContextPackV1: ...

    def revalidate_context(self, pack: PlanningContextPackV1) -> PlanningContextPackV1: ...

    def current_plan(self) -> PlanningPlanV1 | None: ...

    def generate(self, pack: PlanningContextPackV1) -> PlanningProposalV1: ...

    def accept(
        self,
        pack: PlanningContextPackV1,
        proposal: PlanningProposalV1,
        *,
        selected_item_ids: tuple[str, ...],
        item_order: tuple[str, ...],
        operation_id: str,
        expected_current_plan_fingerprint: str | None,
        accepted_at: datetime | None,
    ) -> PlanningPlanV1: ...

    def edit(
        self,
        *,
        items: tuple[PlanningItemV1, ...],
        selected_item_ids: tuple[str, ...],
        item_order: tuple[str, ...],
        operation_id: str,
        expected_current_plan_fingerprint: str,
        edited_at: datetime | None,
    ) -> PlanningPlanV1: ...


def _header(scope: Scope, name: bytes) -> tuple[bool, str | None]:
    values = [value for key, value in scope.get("headers", []) if key.lower() == name]
    return bool(values), values[0].decode("latin-1") if values else None


async def _send(response: Response, scope: Scope, receive: Receive, send: Send) -> None:
    await response(scope, receive, send)


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


class PersonalPlanningRequestBoundaryMiddleware:
    """Fail-closed host, origin, purpose, method, content and body boundary."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _PERSONAL_PLANNING_PATHS:
            await self.app(scope, receive, send)
            return
        if scope.get("method") != "POST":
            await _send(
                JSONResponse(
                    {
                        "error": {
                            "code": "METHOD_NOT_ALLOWED",
                            "message": "Метод не поддерживается.",
                        }
                    },
                    status_code=405,
                    headers=_API_HEADERS,
                ),
                scope,
                receive,
                send,
            )
            return
        header_names = [name.lower() for name, _ in scope.get("headers", [])]
        if any(header_names.count(name) > 1 for name in _SECURITY_HEADER_NAMES):
            await _send(_invalid_request(), scope, receive, send)
            return
        host_present, _ = _header(scope, b"host")
        origin_present, origin = _header(scope, b"origin")
        _, purpose = _header(scope, b"x-second-brain-request")
        _, content_type = _header(scope, b"content-type")
        authorities = trusted_authorities_from_scope(scope)
        if (
            not host_present
            or not request_host_is_trusted(scope, authorities)
            or not origin_present
            or origin is None
            or not same_origin_is_trusted(scope, origin, authorities)
            or purpose != PERSONAL_PLANNING_REQUEST_HEADER_VALUE
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
            if declared > MAX_RAW_PERSONAL_PLANNING_BODY_BYTES:
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
                await _send(_invalid_request(), scope, receive, send)
                return
            body.extend(chunk)
            if len(body) > MAX_RAW_PERSONAL_PLANNING_BODY_BYTES:
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


async def _read_payload(request: Request, payload_type: type[BaseModel]) -> BaseModel:
    raw = await request.body()
    if not raw or len(raw) > MAX_RAW_PERSONAL_PLANNING_BODY_BYTES:
        raise ValueError
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
        return payload_type.model_validate(decoded, strict=True)
    except UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError:
        raise ValueError from None


def _json_response(body: dict[str, object], *, max_bytes: int) -> Response:
    try:
        raw = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
            "utf-8"
        )
    except TypeError, ValueError, UnicodeError:
        return _error_response("PERSONAL_PLANNING_UNAVAILABLE", _GENERIC_MESSAGE, 503)
    if len(raw) > max_bytes:
        return _error_response("PERSONAL_PLANNING_RESULT_TOO_LARGE", _GENERIC_MESSAGE, 503)
    return Response(
        raw,
        media_type="application/json",
        headers=_API_HEADERS,
    )


def _error_response(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status_code,
        headers=_API_HEADERS,
    )


def _invalid_request() -> JSONResponse:
    return _error_response(
        "PERSONAL_PLANNING_INVALID_REQUEST",
        "Запрос личного планирования некорректен.",
        400,
    )


def _too_large() -> JSONResponse:
    return _error_response("PERSONAL_PLANNING_REQUEST_TOO_LARGE", "Запрос слишком велик.", 413)


def _timestamp(value: str | None, *, clock: Callable[[], datetime]) -> datetime:
    if value is None:
        current = clock()
    else:
        try:
            current = parse_rfc3339(value)
        except TypeError, ValueError, OverflowError:
            raise PersonalPlanningInvalidRequestError() from None
    if current.tzinfo is None or current.utcoffset() is None:
        raise PersonalPlanningInvalidRequestError()
    return current.astimezone(UTC)


def _context_request(payload: PersonalPlanningContextPayload) -> PersonalPlanningContextRequest:
    try:
        capacity = tuple(PlanningCapacityEntryV1.from_dict(item) for item in payload.capacity)
        windows = tuple(PlanningWindowV1.from_dict(item) for item in payload.fixed_windows)
    except TypeError, ValueError, RecursionError:
        raise PersonalPlanningInvalidRequestError() from None
    return PersonalPlanningContextRequest(
        goal_source_uuids=tuple(payload.goal_source_uuids),
        start_local=payload.start_local,
        end_local=payload.end_local,
        timezone=payload.timezone,
        capacity=capacity,
        fixed_windows=windows,
        planning_constraints=tuple(payload.planning_constraints),
        planning_context=payload.planning_context,
    )


def _pack_from_payload(value: object) -> PlanningContextPackV1:
    try:
        return PlanningContextPackV1.from_dict(value)
    except TypeError, ValueError, RecursionError:
        raise PersonalPlanningInvalidRequestError() from None


def _proposal_from_payload(value: object) -> PlanningProposalV1:
    try:
        return PlanningProposalV1.from_dict(value)
    except TypeError, ValueError, RecursionError:
        raise PersonalPlanningInvalidRequestError() from None


def _preview_body(envelope: PlanningProviderEnvelopeV1) -> dict[str, object]:
    canonical = envelope.canonical_bytes
    return {
        "source_pack_fingerprint": envelope.source_pack_fingerprint,
        "canonical_json": envelope.canonical_json,
        "canonical_bytes_sha256": hashlib.sha256(canonical).hexdigest(),
        "assistant_envelope": json.loads(envelope.canonical_json),
    }


def _current_plan_body(service: PersonalPlanningWebService) -> dict[str, object] | None:
    plan = service.current_plan()
    return None if plan is None else plan.as_dict()


def _context_body(
    service: PersonalPlanningWebService,
    pack: PlanningContextPackV1,
    envelope: PlanningProviderEnvelopeV1,
) -> dict[str, object]:
    return {
        "web_contract": "personal_planning_context_web_v1",
        "context_pack": pack.as_dict(),
        "provider_preview": _preview_body(envelope),
        "current_plan": _current_plan_body(service),
    }


def _exception_response(error: BaseException) -> JSONResponse:
    if isinstance(
        error,
        PersonalPlanningSourceChangedError | PersonalPlanningStoreSourceChangedError,
    ):
        return _error_response(_SOURCE_CHANGED_CODE, _SOURCE_CHANGED_MESSAGE, 409)
    if isinstance(
        error,
        PersonalPlanningInvalidRequestError | PersonalPlanningStoreInvalidRequestError,
    ):
        return _invalid_request()
    if isinstance(
        error,
        PersonalPlanningStoreStateConflictError | PersonalPlanningStoreCapacityConflictError,
    ):
        return _error_response(
            "PERSONAL_PLANNING_STATE_CONFLICT",
            "План уже изменился или не помещается в доступное время.",
            409,
        )
    if isinstance(error, PersonalPlanningStoreIdempotencyConflictError):
        return _error_response(
            "PERSONAL_PLANNING_IDEMPOTENCY_CONFLICT",
            "Операция уже использована с другим содержимым.",
            409,
        )
    if isinstance(error, PlanningProviderResultInvalidError):
        return _error_response(
            "PERSONAL_PLANNING_PROVIDER_RESULT_INVALID",
            "Ответ независимого совета не прошёл проверку.",
            502,
        )
    if isinstance(error, PlanningPlannerCancelledError):
        return _error_response(
            "PERSONAL_PLANNING_CANCELLED",
            "Операция планирования отменена.",
            409,
        )
    if isinstance(error, PlanningPlannerError):
        return _error_response(
            "PERSONAL_PLANNING_PROVIDER_UNAVAILABLE",
            "Независимый совет сейчас недоступен.",
            503,
        )
    if isinstance(error, PersonalPlanningStoreError | ExecutiveStrategyStoreError):
        return _error_response(
            "PERSONAL_PLANNING_STORE_UNAVAILABLE", _STORE_UNAVAILABLE_MESSAGE, 503
        )
    if isinstance(error, PersonalPlanningSourceUnavailableError):
        return _error_response(
            "PERSONAL_PLANNING_SOURCE_UNAVAILABLE",
            "Текущий источник личного планирования недоступен.",
            503,
        )
    return _error_response("PERSONAL_PLANNING_SOURCE_UNAVAILABLE", _GENERIC_MESSAGE, 503)


def _ensure_revalidated(
    service: PersonalPlanningWebService,
    pack: PlanningContextPackV1,
) -> PlanningContextPackV1:
    validated = validate_planning_context_pack(pack)
    fresh = service.revalidate_context(validated)
    validate_planning_context_pack(fresh)
    if serialize_planning_context_pack(fresh) != serialize_planning_context_pack(validated):
        raise PersonalPlanningSourceChangedError()
    return fresh


def install_personal_planning_routes(
    app: FastAPI,
    *,
    service: PersonalPlanningWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install the additive owner-only Stage 17 Web/API routes."""

    actual_service: PersonalPlanningWebService = (
        service
        or build_production_personal_planning_web_service(
            env_file=env_file,
            vault_path_override=vault_path_override,
        )
    )
    app.add_middleware(PersonalPlanningRequestBoundaryMiddleware)

    @app.post(PERSONAL_PLANNING_STATE_PATH, include_in_schema=False)
    async def state_endpoint(request: Request) -> Response:
        try:
            await _read_payload(request, PersonalPlanningEmptyPayload)
            body = await run_in_threadpool(actual_service.state)
            return _json_response(
                {"web_contract": "personal_planning_state_web_v1", **body},
                max_bytes=MAX_PERSONAL_PLANNING_STATE_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_PLANNING_CONTEXT_PATH, include_in_schema=False)
    async def context_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalPlanningContextPayload,
                await _read_payload(request, PersonalPlanningContextPayload),
            )
            pack = await run_in_threadpool(actual_service.build_context, _context_request(payload))
            validate_planning_context_pack(pack)
            envelope = build_planning_provider_envelope(pack)
            return _json_response(
                _context_body(actual_service, pack, envelope),
                max_bytes=MAX_PERSONAL_PLANNING_CONTEXT_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_PLANNING_GENERATE_PATH, include_in_schema=False)
    async def generate_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalPlanningGeneratePayload,
                await _read_payload(request, PersonalPlanningGeneratePayload),
            )
            submitted = _pack_from_payload(payload.context_pack)
            pack = await run_in_threadpool(_ensure_revalidated, actual_service, submitted)
            envelope = build_planning_provider_envelope(pack)
            if payload.provider_preview != envelope.canonical_json:
                return _error_response(
                    "PERSONAL_PLANNING_PREVIEW_MISMATCH",
                    "Предпросмотр устарел; собери свежий контекст.",
                    409,
                )
            proposal = await run_in_threadpool(actual_service.generate, pack)
            validate_planning_proposal(proposal, pack=pack)
            return _json_response(
                {
                    "web_contract": "personal_planning_generate_web_v1",
                    "context_pack": pack.as_dict(),
                    "provider_preview": _preview_body(envelope),
                    "proposal": proposal.as_dict(),
                    "current_plan": _current_plan_body(actual_service),
                },
                max_bytes=MAX_PERSONAL_PLANNING_GENERATE_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_PLANNING_ACCEPT_PATH, include_in_schema=False)
    async def accept_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalPlanningAcceptPayload,
                await _read_payload(request, PersonalPlanningAcceptPayload),
            )
            pack = _pack_from_payload(payload.context_pack)
            fresh_pack = await run_in_threadpool(_ensure_revalidated, actual_service, pack)
            proposal = _proposal_from_payload(payload.proposal)
            validate_planning_proposal(proposal, pack=fresh_pack)
            plan = await run_in_threadpool(
                actual_service.accept,
                fresh_pack,
                proposal,
                selected_item_ids=tuple(payload.selected_item_ids),
                item_order=tuple(payload.item_order),
                operation_id=payload.operation_id,
                expected_current_plan_fingerprint=payload.expected_current_plan_fingerprint,
                accepted_at=_timestamp(payload.accepted_at, clock=lambda: datetime.now(UTC)),
            )
            return _json_response(
                {
                    "web_contract": "personal_planning_accept_web_v1",
                    "status": "accepted",
                    "plan": plan.as_dict(),
                },
                max_bytes=MAX_PERSONAL_PLANNING_MUTATION_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_PLANNING_EDIT_PATH, include_in_schema=False)
    async def edit_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalPlanningEditPayload,
                await _read_payload(request, PersonalPlanningEditPayload),
            )
            try:
                items = tuple(PlanningItemV1.from_dict(item) for item in payload.items)
            except TypeError, ValueError, RecursionError:
                raise PersonalPlanningInvalidRequestError() from None
            plan = await run_in_threadpool(
                actual_service.edit,
                items=items,
                selected_item_ids=tuple(payload.selected_item_ids),
                item_order=tuple(payload.item_order),
                operation_id=payload.operation_id,
                expected_current_plan_fingerprint=payload.expected_current_plan_fingerprint,
                edited_at=_timestamp(payload.edited_at, clock=lambda: datetime.now(UTC)),
            )
            return _json_response(
                {
                    "web_contract": "personal_planning_edit_web_v1",
                    "status": "edited",
                    "plan": plan.as_dict(),
                },
                max_bytes=MAX_PERSONAL_PLANNING_MUTATION_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)


@dataclass(slots=True)
class ProductionPersonalPlanningWebService:
    """Compose current Goal/strategy readers, the approved Advisor and store."""

    advisor: AdvisorPort
    growth_service: GrowthWebService
    strategy_store: ExecutiveStrategySnapshotStore | None = None
    planning_store: PersonalPlanningOperationalStore | None = None
    env_file: Path | None = None
    vault_path_override: str | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise PersonalPlanningSourceUnavailableError()
        return value.astimezone(UTC)

    def _config(self) -> AppConfig:
        try:
            return load_config(env_file=self.env_file, vault_path_override=self.vault_path_override)
        except Exception as exc:
            raise PersonalPlanningSourceUnavailableError() from exc

    def _strategy_store(self) -> ExecutiveStrategySnapshotStore:
        if self.strategy_store is not None:
            return self.strategy_store
        root = derive_executive_strategy_store_root(self.env_file)
        if root is None:
            raise PersonalPlanningSourceUnavailableError()
        try:
            return ExecutiveStrategySnapshotStore(root, vault_root=self._config().vault_path)
        except ExecutiveStrategyStoreError as exc:
            raise PersonalPlanningSourceUnavailableError() from exc

    def _planning_store(self) -> PersonalPlanningOperationalStore:
        if self.planning_store is not None:
            return self.planning_store
        root = derive_personal_planning_store_root(self.env_file)
        if root is None:
            raise PersonalPlanningSourceUnavailableError()
        try:
            return PersonalPlanningOperationalStore(root, vault_root=self._config().vault_path)
        except PersonalPlanningStoreError as exc:
            raise PersonalPlanningSourceUnavailableError() from exc

    def _goals(self) -> tuple[GrowthGoalOwnerItemV1, ...]:
        try:
            projection = self.growth_service.goals()
        except Exception as exc:
            raise PersonalPlanningSourceUnavailableError() from exc
        return projection.goals

    def _goal(self, source_uuid: str, fingerprint: str) -> GrowthGoalOwnerItemV1:
        matches = tuple(
            item for item in self._goals() if str(item.goal.source_note_uuid) == source_uuid
        )
        if len(matches) != 1:
            raise PersonalPlanningSourceChangedError()
        item = matches[0]
        if goal_identity_fingerprint(item.goal) != fingerprint:
            raise PersonalPlanningSourceChangedError()
        return item

    def _selection(self, item: GrowthGoalOwnerItemV1) -> PlanningGoalSelectionV1:
        snapshot = self._strategy_store().current_snapshot(
            item.goal.source_note_uuid,
            goal_identity_fingerprint(item.goal),
        )
        if (
            snapshot is None
            or type(snapshot) is not StrategySnapshotV1
            or not snapshot.selected_actions
        ):
            raise PersonalPlanningSourceChangedError()
        return PlanningGoalSelectionV1(
            goal=item.goal,
            goal_text=item.goal_text,
            strategy_snapshot=snapshot,
            selected_action_ids=tuple(action.action_id for action in snapshot.selected_actions),
        )

    def state(self) -> dict[str, object]:
        goals: list[dict[str, object]] = []
        for item in self._goals():
            snapshot = self._strategy_store().current_snapshot(
                item.goal.source_note_uuid,
                goal_identity_fingerprint(item.goal),
            )
            goals.append(
                {
                    "goal": item.as_dict(),
                    "goal_source_uuid": str(item.goal.source_note_uuid),
                    "goal_identity_fingerprint": goal_identity_fingerprint(item.goal),
                    "goal_text": item.goal_text,
                    "strategy_snapshot": None
                    if snapshot is None
                    else {
                        "snapshot_id": str(snapshot.snapshot_id),
                        "snapshot_fingerprint": snapshot.snapshot_fingerprint,
                        "sequence": snapshot.sequence,
                        "selected_actions": [
                            action.as_dict() for action in snapshot.selected_actions
                        ],
                    },
                }
            )
        eligible = sum(1 for item in goals if item["strategy_snapshot"] is not None)
        return {
            "goals": goals,
            "eligible_goal_count": eligible,
            "generated_at": self._now().isoformat().replace("+00:00", "Z"),
            "current_plan": _current_plan_body(self),
            "caveats": (
                "План хранится отдельно от second-brain-vault.",
                "В план попадают только цели с принятой стратегией Stage 16.",
                "План не запускает задачи, календарь или внешние действия.",
            ),
        }

    def build_context(self, request: PersonalPlanningContextRequest) -> PlanningContextPackV1:
        try:
            if len(set(request.goal_source_uuids)) != len(request.goal_source_uuids):
                raise PersonalPlanningInvalidRequestError()
            selections = tuple(
                self._selection(
                    next(
                        item
                        for item in self._goals()
                        if str(item.goal.source_note_uuid) == source_uuid
                    )
                )
                for source_uuid in request.goal_source_uuids
            )
            return build_planning_context_pack(
                selections,
                start_local=request.start_local,
                end_local=request.end_local,
                timezone=request.timezone,
                available_minutes_by_date=request.capacity,
                fixed_windows=request.fixed_windows,
                planning_constraints=request.planning_constraints,
                planning_context=request.planning_context,
                as_of=self._now() if request.as_of is None else request.as_of,
            )
        except PersonalPlanningError:
            raise
        except (StopIteration, TypeError, ValueError, RecursionError) as exc:
            raise PersonalPlanningInvalidRequestError() from exc

    def revalidate_context(self, pack: PlanningContextPackV1) -> PlanningContextPackV1:
        validated = validate_planning_context_pack(pack)
        request = PersonalPlanningContextRequest(
            goal_source_uuids=tuple(validated.portfolio_order),
            start_local=validated.start_local,
            end_local=validated.end_local,
            timezone=validated.timezone,
            capacity=validated.capacity,
            fixed_windows=validated.fixed_windows,
            planning_constraints=validated.planning_constraints,
            planning_context=validated.planning_context,
            as_of=validated.as_of,
        )
        try:
            rebuilt = self.build_context(request)
        except PersonalPlanningInvalidRequestError:
            raise PersonalPlanningSourceChangedError() from None
        if serialize_planning_context_pack(rebuilt) != serialize_planning_context_pack(validated):
            raise PersonalPlanningSourceChangedError()
        return rebuilt

    def current_plan(self) -> PlanningPlanV1 | None:
        try:
            return self._planning_store().current_plan()
        except PersonalPlanningStoreError as exc:
            raise PersonalPlanningSourceUnavailableError() from exc

    def generate(self, pack: PlanningContextPackV1) -> PlanningProposalV1:
        try:
            return BuildPersonalPlanner(self.advisor).execute(
                pack,
                cancellation=CancellationTokenSource().token,
                as_of=self._now(),
            )
        except PlanningPlannerError:
            raise
        except Exception as exc:
            raise PersonalPlanningSourceUnavailableError() from exc

    def accept(
        self,
        pack: PlanningContextPackV1,
        proposal: PlanningProposalV1,
        *,
        selected_item_ids: tuple[str, ...],
        item_order: tuple[str, ...],
        operation_id: str,
        expected_current_plan_fingerprint: str | None,
        accepted_at: datetime | None,
    ) -> PlanningPlanV1:
        try:
            return self._planning_store().accept(
                proposal,
                context_pack=pack,
                operation_id=operation_id,
                selected_item_ids=selected_item_ids,
                item_order=item_order,
                accepted_at=accepted_at,
                expected_current_plan_fingerprint=expected_current_plan_fingerprint,
            )
        except PersonalPlanningStoreError:
            raise
        except Exception as exc:
            raise PersonalPlanningSourceUnavailableError() from exc

    def edit(
        self,
        *,
        items: tuple[PlanningItemV1, ...],
        selected_item_ids: tuple[str, ...],
        item_order: tuple[str, ...],
        operation_id: str,
        expected_current_plan_fingerprint: str,
        edited_at: datetime | None,
    ) -> PlanningPlanV1:
        try:
            return self._planning_store().edit(
                items=items,
                selected_item_ids=selected_item_ids,
                item_order=item_order,
                operation_id=operation_id,
                expected_current_plan_fingerprint=expected_current_plan_fingerprint,
                edited_at=edited_at,
            )
        except PersonalPlanningStoreError:
            raise
        except Exception as exc:
            raise PersonalPlanningSourceUnavailableError() from exc


def build_production_personal_planning_web_service(
    *,
    advisor: AdvisorPort | None = None,
    growth_service: GrowthWebService | None = None,
    strategy_store: ExecutiveStrategySnapshotStore | None = None,
    planning_store: PersonalPlanningOperationalStore | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> ProductionPersonalPlanningWebService:
    return ProductionPersonalPlanningWebService(
        advisor=advisor or CloudflareWorkersAiAdvisorPort(),
        growth_service=growth_service
        or ProductionGrowthWebService(env_file=env_file, vault_path_override=vault_path_override),
        strategy_store=strategy_store,
        planning_store=planning_store,
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


__all__ = [
    "MAX_PERSONAL_PLANNING_CONTEXT_RESPONSE_BYTES",
    "MAX_PERSONAL_PLANNING_GENERATE_RESPONSE_BYTES",
    "MAX_PERSONAL_PLANNING_MUTATION_RESPONSE_BYTES",
    "MAX_PERSONAL_PLANNING_STATE_RESPONSE_BYTES",
    "MAX_RAW_PERSONAL_PLANNING_BODY_BYTES",
    "PERSONAL_PLANNING_ACCEPT_PATH",
    "PERSONAL_PLANNING_CONTEXT_PATH",
    "PERSONAL_PLANNING_EDIT_PATH",
    "PERSONAL_PLANNING_GENERATE_PATH",
    "PERSONAL_PLANNING_REQUEST_HEADER_NAME",
    "PERSONAL_PLANNING_REQUEST_HEADER_VALUE",
    "PERSONAL_PLANNING_STATE_PATH",
    "PersonalPlanningAcceptPayload",
    "PersonalPlanningContextPayload",
    "PersonalPlanningContextRequest",
    "PersonalPlanningEditPayload",
    "PersonalPlanningEmptyPayload",
    "PersonalPlanningGeneratePayload",
    "PersonalPlanningRequestBoundaryMiddleware",
    "PersonalPlanningSourceChangedError",
    "PersonalPlanningSourceUnavailableError",
    "PersonalPlanningWebService",
    "ProductionPersonalPlanningWebService",
    "build_production_personal_planning_web_service",
    "install_personal_planning_routes",
]
