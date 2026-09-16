"""Owner-only Web/API projection for the Stage 16 Personal Strategy surface.

The transport keeps the Executive DTOs authoritative: the browser may carry a
pack and proposal between explicit review steps, but every consequential step
revalidates the exact current Goal and source projections before it can call an
Advisor or append an operational snapshot.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol, cast
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.adapters.advisor.cloudflare_workers_ai import CloudflareWorkersAiAdvisorPort
from second_brain.application.executive_strategy import (
    BuildExecutiveStrategy,
    ExecutiveContextPackV1,
    ExecutiveProviderResultInvalidError,
    ExecutiveSourceAliasV1,
    ExecutiveSourceItemV1,
    ExecutiveSourceReadinessV1,
    ReviewedActionV1,
    StrategyProposalV1,
    StrategyReasoningEnvelopeV1,
    build_executive_context_pack,
    build_strategy_reasoning_envelope,
    goal_identity_fingerprint,
    serialize_executive_context_pack,
    validate_executive_context_pack,
    validate_strategy_proposal,
)
from second_brain.application.executive_strategy_store import (
    ExecutiveStrategySnapshotStore,
    ExecutiveStrategyStoreError,
    ExecutiveStrategyStoreIdempotencyConflictError,
    ExecutiveStrategyStoreInvalidRequestError,
    ExecutiveStrategyStoreSourceChangedError,
    ExecutiveStrategyStoreStateConflictError,
    derive_executive_strategy_store_root,
)
from second_brain.application.goal_progress_read import GoalProgressRequestV1, GoalProgressResultV1
from second_brain.application.growth import (
    GROWTH_POLICY_FINGERPRINT,
    GrowthEngineRequestV1,
    GrowthGoalSelectionModeV1,
    GrowthGoalSelectionV1,
)
from second_brain.application.ports import AdvisorPort, CancellationTokenSource
from second_brain.config import AppConfig, load_config
from second_brain.entrypoints.web.auth import (
    request_host_is_trusted,
    same_origin_is_trusted,
    trusted_authorities_from_scope,
)
from second_brain.entrypoints.web.goal_progress import (
    GoalProgressWebService,
    ProductionGoalProgressWebService,
)
from second_brain.entrypoints.web.growth import (
    GrowthGoalOwnerItemV1,
    GrowthGoalsProjectionV1,
    GrowthWebService,
    ProductionGrowthWebService,
)

PERSONAL_STRATEGY_STATE_PATH: Final[str] = "/api/personal-strategy/state"
PERSONAL_STRATEGY_CONTEXT_PATH: Final[str] = "/api/personal-strategy/context"
PERSONAL_STRATEGY_GENERATE_PATH: Final[str] = "/api/personal-strategy/generate"
PERSONAL_STRATEGY_REJECT_PATH: Final[str] = "/api/personal-strategy/reject"
PERSONAL_STRATEGY_ACCEPT_PATH: Final[str] = "/api/personal-strategy/accept"
PERSONAL_STRATEGY_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
PERSONAL_STRATEGY_REQUEST_HEADER_VALUE: Final[str] = "executive-strategy-v1"

MAX_RAW_PERSONAL_STRATEGY_BODY_BYTES: Final[int] = 256 * 1024
MAX_PERSONAL_STRATEGY_STATE_RESPONSE_BYTES: Final[int] = 256 * 1024
MAX_PERSONAL_STRATEGY_CONTEXT_RESPONSE_BYTES: Final[int] = 256 * 1024
MAX_PERSONAL_STRATEGY_GENERATE_RESPONSE_BYTES: Final[int] = 256 * 1024
MAX_PERSONAL_STRATEGY_MUTATION_RESPONSE_BYTES: Final[int] = 64 * 1024
MAX_PERSONAL_STRATEGY_ACCEPT_RESPONSE_BYTES: Final[int] = (
    MAX_PERSONAL_STRATEGY_MUTATION_RESPONSE_BYTES
)

_PERSONAL_STRATEGY_PATHS: Final[frozenset[str]] = frozenset(
    {
        PERSONAL_STRATEGY_STATE_PATH,
        PERSONAL_STRATEGY_CONTEXT_PATH,
        PERSONAL_STRATEGY_GENERATE_PATH,
        PERSONAL_STRATEGY_REJECT_PATH,
        PERSONAL_STRATEGY_ACCEPT_PATH,
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

_INVALID_REQUEST_CODE: Final[str] = "PERSONAL_STRATEGY_INVALID_REQUEST"
_INVALID_REQUEST_MESSAGE: Final[str] = "Запрос личной стратегии не прошёл проверку."
_TOO_LARGE_CODE: Final[str] = "PERSONAL_STRATEGY_CONTENT_TOO_LARGE"
_TOO_LARGE_MESSAGE: Final[str] = "Запрос личной стратегии слишком велик."
_SOURCE_CHANGED_CODE: Final[str] = "PERSONAL_STRATEGY_SOURCE_CHANGED"
_SOURCE_CHANGED_MESSAGE: Final[str] = (
    "Текущая Goal или её контекст изменились; обнови контекст и повтори review."
)
_SOURCE_UNAVAILABLE_CODE: Final[str] = "PERSONAL_STRATEGY_SOURCE_UNAVAILABLE"
_SOURCE_UNAVAILABLE_MESSAGE: Final[str] = "Текущий источник личной стратегии недоступен."
_PREVIEW_MISMATCH_CODE: Final[str] = "PERSONAL_STRATEGY_PREVIEW_MISMATCH"
_PREVIEW_MISMATCH_MESSAGE: Final[str] = "Предпросмотр провайдера устарел; построй свежий контекст."
_GENERIC_MESSAGE: Final[str] = "Операция личной стратегии сейчас недоступна."


class PersonalStrategyError(RuntimeError):
    """Safe application-facing transport error."""


class PersonalStrategySourceChangedError(PersonalStrategyError):
    """The exact Goal or a source projection no longer matches the pack."""


class PersonalStrategySourceUnavailableError(PersonalStrategyError):
    """Current Goal/source/store configuration cannot be read safely."""


class PersonalStrategyPreviewMismatchError(PersonalStrategyError):
    """The submitted provider-visible preview is not the canonical preview."""


class PersonalStrategyInvalidRequestError(PersonalStrategyError):
    """The typed owner request cannot be accepted."""


class PersonalStrategyEmptyPayload(BaseModel):
    """Strict empty object used by the state read."""

    model_config = ConfigDict(extra="forbid", strict=True)


class PersonalStrategyContextPayload(BaseModel):
    """The only owner-entered fields allowed into a context pack."""

    model_config = ConfigDict(extra="forbid", strict=True)

    goal_source_uuid: StrictStr
    goal_identity_fingerprint: StrictStr
    task: StrictStr
    constraints: list[StrictStr] = Field(default_factory=list, max_length=8)
    current_context: StrictStr = ""


class PersonalStrategyGeneratePayload(BaseModel):
    """One explicit generation request carrying the reviewed canonical preview."""

    model_config = ConfigDict(extra="forbid", strict=True)

    context_pack: dict[str, object]
    provider_preview: StrictStr


class PersonalStrategyRejectPayload(BaseModel):
    """Reject one validated ephemeral proposal without retaining its payload."""

    model_config = ConfigDict(extra="forbid", strict=True)

    proposal: dict[str, object]
    operation_id: StrictStr
    reason: StrictStr = "owner_rejected"


class PersonalStrategyAcceptPayload(BaseModel):
    """Accept one exact owner-reviewed proposal and optional selected actions."""

    model_config = ConfigDict(extra="forbid", strict=True)

    context_pack: dict[str, object]
    proposal: dict[str, object]
    selected_actions: list[dict[str, object]] = Field(default_factory=list, max_length=8)
    operation_id: StrictStr
    expected_prior_snapshot_id: StrictStr | None = None
    expected_prior_snapshot_fingerprint: StrictStr | None = None


@dataclass(frozen=True, slots=True)
class PersonalStrategyContextRequest:
    """Normalized owner input for one provider-free context build."""

    goal_source_uuid: str
    goal_identity_fingerprint: str
    task: str
    constraints: tuple[str, ...]
    current_context: str


class PersonalStrategyWebService(Protocol):
    """Injectable seam for the private Personal Strategy transport."""

    def state(self) -> dict[str, object]:
        """Return bounded exact current Goals and accepted strategy projections."""
        ...

    def build_context(self, request: PersonalStrategyContextRequest) -> ExecutiveContextPackV1:
        """Build one fresh provider-free exact context pack."""
        ...

    def revalidate_context(self, pack: ExecutiveContextPackV1) -> ExecutiveContextPackV1:
        """Reread current sources and return the same pack or fail closed."""
        ...

    def current_snapshot(self, pack: ExecutiveContextPackV1) -> dict[str, object] | None:
        """Return the current snapshot for the pack's exact Goal, if one exists."""
        ...

    def generate(self, pack: ExecutiveContextPackV1) -> StrategyProposalV1:
        """Perform one explicit bounded Advisor operation."""
        ...

    def reject(
        self,
        proposal: StrategyProposalV1,
        *,
        operation_id: str,
        reason: str,
    ) -> None:
        """Append bounded rejection provenance only."""
        ...

    def accept(
        self,
        pack: ExecutiveContextPackV1,
        proposal: StrategyProposalV1,
        selected_actions: tuple[object, ...],
        *,
        operation_id: str,
        expected_prior_snapshot_id: str | None,
        expected_prior_snapshot_fingerprint: str | None,
    ) -> object:
        """Revalidate and append one accepted snapshot."""
        ...


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError, RecursionError) as exc:
        raise PersonalStrategyInvalidRequestError() from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _safe_json(value: object, *, max_bytes: int) -> bytes:
    encoded = _canonical_bytes(value)
    if len(encoded) > max_bytes:
        raise PersonalStrategySourceUnavailableError()
    return encoded


def _json_response(value: object, *, max_bytes: int) -> Response:
    try:
        return Response(
            content=_safe_json(value, max_bytes=max_bytes),
            media_type="application/json",
            headers=_API_HEADERS,
        )
    except PersonalStrategySourceUnavailableError:
        return _error_response(_SOURCE_UNAVAILABLE_CODE, _SOURCE_UNAVAILABLE_MESSAGE, 503)


def _error_response(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=_API_HEADERS,
    )


def _invalid_request() -> JSONResponse:
    return _error_response(_INVALID_REQUEST_CODE, _INVALID_REQUEST_MESSAGE, 400)


def _too_large() -> JSONResponse:
    return _error_response(_TOO_LARGE_CODE, _TOO_LARGE_MESSAGE, 413)


def _method_not_allowed() -> JSONResponse:
    response = _invalid_request()
    response.status_code = 405
    response.headers["Allow"] = "POST"
    return response


async def _send(response: Response, scope: Scope, receive: Receive, send: Send) -> None:
    await response(scope, receive, send)


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


class PersonalStrategyRequestBoundaryMiddleware:
    """Fail-closed Host, Origin, purpose, method, content and body boundary."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _PERSONAL_STRATEGY_PATHS:
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
            or not origin_present
            or origin is None
            or not same_origin_is_trusted(scope, origin, authorities)
            or purpose != PERSONAL_STRATEGY_REQUEST_HEADER_VALUE
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
            if declared > MAX_RAW_PERSONAL_STRATEGY_BODY_BYTES:
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
            if len(body) > MAX_RAW_PERSONAL_STRATEGY_BODY_BYTES:
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
    if not raw or len(raw) > MAX_RAW_PERSONAL_STRATEGY_BODY_BYTES:
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


def _context_request(payload: PersonalStrategyContextPayload) -> PersonalStrategyContextRequest:
    return PersonalStrategyContextRequest(
        goal_source_uuid=payload.goal_source_uuid,
        goal_identity_fingerprint=payload.goal_identity_fingerprint,
        task=payload.task,
        constraints=tuple(payload.constraints),
        current_context=payload.current_context,
    )


def _preview_body(pack: ExecutiveContextPackV1) -> dict[str, object]:
    envelope: StrategyReasoningEnvelopeV1 = build_strategy_reasoning_envelope(pack)
    canonical = envelope.canonical_bytes
    return {
        "source_pack_fingerprint": pack.source_pack_fingerprint,
        "canonical_json": canonical.decode("utf-8"),
        "canonical_bytes_sha256": hashlib.sha256(canonical).hexdigest(),
        "assistant_envelope": json.loads(canonical.decode("utf-8")),
    }


def _snapshot_status(
    snapshot: dict[str, object] | None,
    pack: ExecutiveContextPackV1,
) -> str:
    if snapshot is None:
        return "none"
    return (
        "current"
        if snapshot.get("source_pack_fingerprint") == pack.source_pack_fingerprint
        else "stale"
    )


def _context_body(
    service: PersonalStrategyWebService,
    pack: ExecutiveContextPackV1,
) -> dict[str, object]:
    snapshot = service.current_snapshot(pack)
    return {
        "web_contract": "personal_strategy_context_web_v1",
        "context_pack": pack.as_dict(),
        "provider_preview": _preview_body(pack),
        "accepted_snapshot": snapshot,
        "accepted_snapshot_status": _snapshot_status(snapshot, pack),
    }


def _proposal_body(
    service: PersonalStrategyWebService,
    pack: ExecutiveContextPackV1,
    proposal: StrategyProposalV1,
) -> dict[str, object]:
    snapshot = service.current_snapshot(pack)
    return {
        "web_contract": "personal_strategy_generate_web_v1",
        "context_pack": pack.as_dict(),
        "provider_preview": _preview_body(pack),
        "proposal": proposal.as_dict(),
        "accepted_snapshot": snapshot,
        "accepted_snapshot_status": _snapshot_status(snapshot, pack),
    }


def _proposal_from_payload(value: object) -> StrategyProposalV1:
    try:
        return StrategyProposalV1.from_dict(value)
    except TypeError, ValueError, RecursionError:
        raise PersonalStrategyInvalidRequestError() from None


def _pack_from_payload(value: object) -> ExecutiveContextPackV1:
    try:
        return ExecutiveContextPackV1.from_dict(value)
    except TypeError, ValueError, RecursionError:
        raise PersonalStrategyInvalidRequestError() from None


def _exception_response(error: BaseException) -> JSONResponse:
    if isinstance(error, PersonalStrategyPreviewMismatchError):
        return _error_response(_PREVIEW_MISMATCH_CODE, _PREVIEW_MISMATCH_MESSAGE, 409)
    if isinstance(error, PersonalStrategySourceChangedError):
        return _error_response(_SOURCE_CHANGED_CODE, _SOURCE_CHANGED_MESSAGE, 409)
    if isinstance(error, PersonalStrategyInvalidRequestError):
        return _invalid_request()
    if isinstance(
        error,
        (ExecutiveStrategyStoreStateConflictError, ExecutiveStrategyStoreSourceChangedError),
    ):
        return _error_response(_SOURCE_CHANGED_CODE, _SOURCE_CHANGED_MESSAGE, 409)
    if isinstance(error, ExecutiveStrategyStoreIdempotencyConflictError):
        return _error_response(
            "PERSONAL_STRATEGY_IDEMPOTENCY_CONFLICT",
            "Операция уже использована с другим содержимым.",
            409,
        )
    if isinstance(error, ExecutiveStrategyStoreInvalidRequestError):
        return _invalid_request()
    if isinstance(error, ExecutiveProviderResultInvalidError):
        return _error_response(
            "PERSONAL_STRATEGY_PROVIDER_RESULT_INVALID",
            "Ответ независимого совета не прошёл проверку.",
            502,
        )
    if isinstance(error, ExecutiveStrategyStoreError):
        return _error_response(
            "PERSONAL_STRATEGY_STORE_UNAVAILABLE",
            "Операционное хранилище личной стратегии недоступно.",
            503,
        )
    if isinstance(error, PersonalStrategySourceUnavailableError):
        return _error_response(_SOURCE_UNAVAILABLE_CODE, _SOURCE_UNAVAILABLE_MESSAGE, 503)
    return _error_response(_SOURCE_UNAVAILABLE_CODE, _SOURCE_UNAVAILABLE_MESSAGE, 503)


def _ensure_revalidated(
    service: PersonalStrategyWebService,
    pack: ExecutiveContextPackV1,
) -> ExecutiveContextPackV1:
    validated = validate_executive_context_pack(pack)
    fresh = service.revalidate_context(validated)
    validate_executive_context_pack(fresh)
    if serialize_executive_context_pack(fresh) != serialize_executive_context_pack(validated):
        raise PersonalStrategySourceChangedError()
    return fresh


def install_personal_strategy_routes(
    app: FastAPI,
    *,
    service: PersonalStrategyWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install only the additive Stage 16 owner-facing routes."""

    actual_service = service or build_production_personal_strategy_web_service(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    app.add_middleware(PersonalStrategyRequestBoundaryMiddleware)

    @app.post(PERSONAL_STRATEGY_STATE_PATH, include_in_schema=False)
    async def state_endpoint(request: Request) -> Response:
        try:
            await _read_payload(request, PersonalStrategyEmptyPayload)
            body = await run_in_threadpool(actual_service.state)
            return _json_response(
                {"web_contract": "personal_strategy_state_web_v1", **body},
                max_bytes=MAX_PERSONAL_STRATEGY_STATE_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_STRATEGY_CONTEXT_PATH, include_in_schema=False)
    async def context_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalStrategyContextPayload,
                await _read_payload(request, PersonalStrategyContextPayload),
            )
            pack = await run_in_threadpool(actual_service.build_context, _context_request(payload))
            validate_executive_context_pack(pack)
            return _json_response(
                _context_body(actual_service, pack),
                max_bytes=MAX_PERSONAL_STRATEGY_CONTEXT_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_STRATEGY_GENERATE_PATH, include_in_schema=False)
    async def generate_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalStrategyGeneratePayload,
                await _read_payload(request, PersonalStrategyGeneratePayload),
            )
            submitted_pack = _pack_from_payload(payload.context_pack)
            pack = await run_in_threadpool(_ensure_revalidated, actual_service, submitted_pack)
            preview = _preview_body(pack)
            if payload.provider_preview != preview["canonical_json"]:
                raise PersonalStrategyPreviewMismatchError()
            proposal = await run_in_threadpool(actual_service.generate, pack)
            validate_strategy_proposal(proposal)
            return _json_response(
                _proposal_body(actual_service, pack, proposal),
                max_bytes=MAX_PERSONAL_STRATEGY_GENERATE_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_STRATEGY_REJECT_PATH, include_in_schema=False)
    async def reject_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalStrategyRejectPayload,
                await _read_payload(request, PersonalStrategyRejectPayload),
            )
            proposal = _proposal_from_payload(payload.proposal)
            await run_in_threadpool(
                actual_service.reject,
                proposal,
                operation_id=payload.operation_id,
                reason=payload.reason,
            )
            return _json_response(
                {
                    "web_contract": "personal_strategy_reject_web_v1",
                    "status": "rejected",
                    "proposal_fingerprint": proposal.proposal_fingerprint,
                },
                max_bytes=MAX_PERSONAL_STRATEGY_MUTATION_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_STRATEGY_ACCEPT_PATH, include_in_schema=False)
    async def accept_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalStrategyAcceptPayload,
                await _read_payload(request, PersonalStrategyAcceptPayload),
            )
            pack = _pack_from_payload(payload.context_pack)
            fresh_pack = await run_in_threadpool(_ensure_revalidated, actual_service, pack)
            proposal = _proposal_from_payload(payload.proposal)
            actions = tuple(ReviewedActionV1.from_dict(item) for item in payload.selected_actions)
            snapshot = await run_in_threadpool(
                actual_service.accept,
                fresh_pack,
                proposal,
                actions,
                operation_id=payload.operation_id,
                expected_prior_snapshot_id=payload.expected_prior_snapshot_id,
                expected_prior_snapshot_fingerprint=payload.expected_prior_snapshot_fingerprint,
            )
            if not hasattr(snapshot, "as_dict"):
                raise PersonalStrategySourceUnavailableError()
            return _json_response(
                {
                    "web_contract": "personal_strategy_accept_web_v1",
                    "status": "accepted",
                    "snapshot": snapshot.as_dict(),
                },
                max_bytes=MAX_PERSONAL_STRATEGY_MUTATION_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)


@dataclass(slots=True)
class ProductionPersonalStrategyWebService:
    """Compose current Goal/source readers, the approved Advisor and store."""

    advisor: AdvisorPort
    growth_service: GrowthWebService
    progress_service: GoalProgressWebService | None = None
    env_file: Path | None = None
    vault_path_override: str | None = None
    snapshot_store: ExecutiveStrategySnapshotStore | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def _now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PersonalStrategySourceUnavailableError()
        return value.astimezone(UTC)

    def _config(self) -> AppConfig:
        try:
            return load_config(
                env_file=self.env_file,
                vault_path_override=self.vault_path_override,
            )
        except Exception as exc:
            raise PersonalStrategySourceUnavailableError() from exc

    def _store(self) -> ExecutiveStrategySnapshotStore:
        if self.snapshot_store is not None:
            return self.snapshot_store
        root = derive_executive_strategy_store_root(self.env_file)
        if root is None:
            raise PersonalStrategySourceUnavailableError()
        config = self._config()
        try:
            return ExecutiveStrategySnapshotStore(root, vault_root=config.vault_path)
        except ExecutiveStrategyStoreError as exc:
            raise PersonalStrategySourceUnavailableError() from exc

    def _goals(self) -> GrowthGoalsProjectionV1:
        try:
            result = self.growth_service.goals()
        except Exception as exc:
            raise PersonalStrategySourceUnavailableError() from exc
        if type(result) is not GrowthGoalsProjectionV1:
            raise PersonalStrategySourceUnavailableError()
        return result

    def _goal(self, source_uuid: str, fingerprint: str) -> GrowthGoalOwnerItemV1:
        try:
            matches = tuple(
                item
                for item in self._goals().goals
                if str(item.goal.source_note_uuid) == source_uuid
            )
            if len(matches) != 1:
                raise PersonalStrategySourceChangedError()
            item = matches[0]
            if goal_identity_fingerprint(item.goal) != fingerprint:
                raise PersonalStrategySourceChangedError()
            return item
        except PersonalStrategyError:
            raise
        except (TypeError, ValueError, UnicodeError) as exc:
            raise PersonalStrategySourceChangedError() from exc

    @staticmethod
    def _source(
        alias: ExecutiveSourceAliasV1,
        readiness: ExecutiveSourceReadinessV1,
        *,
        reference_id: str | None,
        reference_fingerprint: str | None,
        summary: str,
        policy_fingerprints: tuple[str, ...] = (),
        as_of: datetime,
    ) -> ExecutiveSourceItemV1:
        return ExecutiveSourceItemV1(
            alias=alias,
            readiness=readiness,
            reference_id=reference_id,
            reference_fingerprint=reference_fingerprint,
            summary=summary,
            policy_fingerprints=policy_fingerprints,
            as_of=as_of,
        )

    def _growth_source(
        self,
        goal: GrowthGoalOwnerItemV1,
        *,
        as_of: datetime,
    ) -> ExecutiveSourceItemV1:
        try:
            result = self.growth_service.execute(
                GrowthEngineRequestV1(
                    selection=GrowthGoalSelectionV1(
                        GrowthGoalSelectionModeV1.SELECTED_GOAL,
                        cast(UUID, goal.goal.source_note_uuid),
                    )
                )
            )
            if len(result.goal_results) != 1:
                raise ValueError
            relation = result.goal_results[0]
            if (
                relation.goal is None
                or relation.goal.source_note_uuid != goal.goal.source_note_uuid
            ):
                raise ValueError
            state = getattr(relation.state, "value", str(relation.state))
            projection = {
                "goal_source_uuid": str(goal.goal.source_note_uuid),
                "goal_identity_fingerprint": goal_identity_fingerprint(goal.goal),
                "state": state,
                "cohort_fingerprint": relation.cohort_fingerprint,
                "mapping": relation.mapping.as_dict() if relation.mapping is not None else None,
                "reason_codes": [
                    getattr(item, "value", str(item)) for item in relation.reason_codes
                ],
                "caveats": [getattr(item, "value", str(item)) for item in relation.caveats],
            }
            return self._source(
                ExecutiveSourceAliasV1.GROWTH_RELATION,
                ExecutiveSourceReadinessV1.EXACT_CURRENT,
                reference_id=f"growth:{goal.goal.source_note_uuid}",
                reference_fingerprint=_sha256(projection),
                summary=f"Связь Growth: {state}; результат построен по одной exact Goal.",
                policy_fingerprints=(GROWTH_POLICY_FINGERPRINT,),
                as_of=as_of,
            )
        except Exception:
            return self._source(
                ExecutiveSourceAliasV1.GROWTH_RELATION,
                ExecutiveSourceReadinessV1.MISSING,
                reference_id=None,
                reference_fingerprint=None,
                summary="Текущая связь Growth недоступна.",
                as_of=as_of,
            )

    def _progress_source(
        self,
        goal: GrowthGoalOwnerItemV1,
        *,
        as_of: datetime,
    ) -> ExecutiveSourceItemV1:
        try:
            progress = self.progress_service or ProductionGoalProgressWebService(
                env_file=self.env_file,
                vault_path_override=self.vault_path_override,
                clock=self.clock,
            )
            result = progress.read(GoalProgressRequestV1(goal.goal.source_note_uuid, as_of))
            if type(result) is not GoalProgressResultV1:
                raise ValueError
            if result.current_goal_identity_fingerprint != goal_identity_fingerprint(goal.goal):
                raise ValueError
            status = getattr(result.status, "value", str(result.status))
            projection = {
                "goal_source_uuid": str(goal.goal.source_note_uuid),
                "goal_identity_fingerprint": result.current_goal_identity_fingerprint,
                "policy_fingerprint": result.goal_progress_policy_fingerprint,
                "status": status,
                "definition_uuid": (
                    str(result.active_definition_uuid)
                    if result.active_definition_uuid is not None
                    else None
                ),
                "definition_fingerprint": result.definition_fingerprint,
                "observation_uuids": [str(item) for item in result.current_observation_uuids],
                "eligible_count": result.eligible_count,
            }
            return self._source(
                ExecutiveSourceAliasV1.PROGRESS_CURRENT,
                ExecutiveSourceReadinessV1.EXACT_CURRENT,
                reference_id=f"progress:{goal.goal.source_note_uuid}",
                reference_fingerprint=_sha256(projection),
                summary=(
                    f"Текущий прогресс: {status}; сопоставимых наблюдений: {result.eligible_count}."
                ),
                policy_fingerprints=(result.goal_progress_policy_fingerprint,),
                as_of=as_of,
            )
        except Exception:
            return self._source(
                ExecutiveSourceAliasV1.PROGRESS_CURRENT,
                ExecutiveSourceReadinessV1.MISSING,
                reference_id=None,
                reference_fingerprint=None,
                summary="Текущий прогресс недоступен.",
                as_of=as_of,
            )

    def _sources(
        self,
        goal: GrowthGoalOwnerItemV1,
        *,
        as_of: datetime,
    ) -> tuple[ExecutiveSourceItemV1, ...]:
        return (
            self._growth_source(goal, as_of=as_of),
            self._progress_source(goal, as_of=as_of),
        )

    def state(self) -> dict[str, object]:
        goals = self._goals()
        store = self._store()
        current_snapshots: list[dict[str, object]] = []
        for item in goals.goals:
            current = store.current_snapshot(
                item.goal.source_note_uuid,
                goal_identity_fingerprint(item.goal),
            )
            current_snapshots.append(
                {
                    "goal_source_uuid": str(item.goal.source_note_uuid),
                    "goal_identity_fingerprint": item.goal_identity_fingerprint,
                    "snapshot": current.as_dict() if current is not None else None,
                    "freshness": "requires_context_refresh" if current is not None else "none",
                }
            )
        return {
            "goals": [item.as_dict() for item in goals.goals],
            "eligible_goal_count": goals.eligible_goal_count,
            "generated_at": goals.generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "current_snapshots": current_snapshots,
            "caveats": [
                "Стратегия хранится отдельно от second-brain-vault.",
                "Актуальность принятого снимка подтверждается после свежего context review.",
            ],
        }

    def build_context(self, request: PersonalStrategyContextRequest) -> ExecutiveContextPackV1:
        as_of = self._now()
        goal = self._goal(request.goal_source_uuid, request.goal_identity_fingerprint)
        try:
            return build_executive_context_pack(
                goal.goal,
                goal_text=goal.goal_text,
                task=request.task,
                constraints=request.constraints,
                current_context=request.current_context,
                sources=self._sources(goal, as_of=as_of),
                pack_caveats=(
                    "Недоступные optional source families остаются missing; "
                    "безопасный результат может быть abstention.",
                ),
                as_of=as_of,
                expected_goal_identity_fingerprint=request.goal_identity_fingerprint,
            )
        except PersonalStrategyError:
            raise
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise PersonalStrategyInvalidRequestError() from exc

    def revalidate_context(self, pack: ExecutiveContextPackV1) -> ExecutiveContextPackV1:
        validated = validate_executive_context_pack(pack)
        goal = self._goal(str(validated.goal_source_uuid), validated.goal_identity_fingerprint)
        if goal.goal_text != validated.goal_text:
            raise PersonalStrategySourceChangedError()
        try:
            rebuilt = build_executive_context_pack(
                goal.goal,
                goal_text=goal.goal_text,
                task=validated.task,
                constraints=validated.constraints,
                current_context=validated.current_context,
                sources=self._sources(goal, as_of=validated.as_of),
                pack_caveats=validated.pack_caveats,
                as_of=validated.as_of,
                expected_goal_identity_fingerprint=validated.goal_identity_fingerprint,
            )
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise PersonalStrategySourceChangedError() from exc
        if serialize_executive_context_pack(rebuilt) != serialize_executive_context_pack(validated):
            raise PersonalStrategySourceChangedError()
        return rebuilt

    def current_snapshot(self, pack: ExecutiveContextPackV1) -> dict[str, object] | None:
        store = self._store()
        snapshot = store.current_snapshot(pack.goal_source_uuid, pack.goal_identity_fingerprint)
        return snapshot.as_dict() if snapshot is not None else None

    def generate(self, pack: ExecutiveContextPackV1) -> StrategyProposalV1:
        try:
            return BuildExecutiveStrategy(self.advisor).execute(
                pack,
                cancellation=CancellationTokenSource().token,
                as_of=self._now(),
            )
        except ExecutiveProviderResultInvalidError:
            raise
        except Exception as exc:
            raise PersonalStrategySourceUnavailableError() from exc

    def reject(
        self,
        proposal: StrategyProposalV1,
        *,
        operation_id: str,
        reason: str,
    ) -> None:
        try:
            self._store().reject(proposal, operation_id=operation_id, reason=reason)
        except ExecutiveStrategyStoreError:
            raise
        except Exception as exc:
            raise PersonalStrategySourceUnavailableError() from exc

    def accept(
        self,
        pack: ExecutiveContextPackV1,
        proposal: StrategyProposalV1,
        selected_actions: tuple[object, ...],
        *,
        operation_id: str,
        expected_prior_snapshot_id: str | None,
        expected_prior_snapshot_fingerprint: str | None,
    ) -> object:
        try:
            if any(type(item) is not ReviewedActionV1 for item in selected_actions):
                raise ExecutiveStrategyStoreInvalidRequestError()
            now = self._now()
            return self._store().accept(
                proposal,
                cast(tuple[ReviewedActionV1, ...], selected_actions),
                current_pack=pack,
                operation_id=operation_id,
                reviewed_at=now,
                accepted_at=now,
                expected_prior_snapshot_id=expected_prior_snapshot_id,
                expected_prior_snapshot_fingerprint=expected_prior_snapshot_fingerprint,
            )
        except ExecutiveStrategyStoreError:
            raise
        except Exception as exc:
            raise PersonalStrategySourceUnavailableError() from exc


def build_production_personal_strategy_web_service(
    *,
    advisor: AdvisorPort | None = None,
    growth_service: GrowthWebService | None = None,
    progress_service: GoalProgressWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
    snapshot_store: ExecutiveStrategySnapshotStore | None = None,
) -> ProductionPersonalStrategyWebService:
    """Build the bounded service without provider I/O until ``generate``."""

    return ProductionPersonalStrategyWebService(
        advisor=advisor or CloudflareWorkersAiAdvisorPort(),
        growth_service=growth_service
        or ProductionGrowthWebService(
            env_file=env_file,
            vault_path_override=vault_path_override,
        ),
        progress_service=progress_service,
        env_file=env_file,
        vault_path_override=vault_path_override,
        snapshot_store=snapshot_store,
    )


__all__ = [
    "MAX_PERSONAL_STRATEGY_ACCEPT_RESPONSE_BYTES",
    "MAX_PERSONAL_STRATEGY_CONTEXT_RESPONSE_BYTES",
    "MAX_PERSONAL_STRATEGY_GENERATE_RESPONSE_BYTES",
    "MAX_PERSONAL_STRATEGY_MUTATION_RESPONSE_BYTES",
    "MAX_PERSONAL_STRATEGY_STATE_RESPONSE_BYTES",
    "MAX_RAW_PERSONAL_STRATEGY_BODY_BYTES",
    "PERSONAL_STRATEGY_ACCEPT_PATH",
    "PERSONAL_STRATEGY_CONTEXT_PATH",
    "PERSONAL_STRATEGY_GENERATE_PATH",
    "PERSONAL_STRATEGY_REJECT_PATH",
    "PERSONAL_STRATEGY_REQUEST_HEADER_NAME",
    "PERSONAL_STRATEGY_REQUEST_HEADER_VALUE",
    "PERSONAL_STRATEGY_STATE_PATH",
    "PersonalStrategyAcceptPayload",
    "PersonalStrategyContextPayload",
    "PersonalStrategyContextRequest",
    "PersonalStrategyEmptyPayload",
    "PersonalStrategyGeneratePayload",
    "PersonalStrategyRejectPayload",
    "PersonalStrategyRequestBoundaryMiddleware",
    "PersonalStrategySourceChangedError",
    "PersonalStrategySourceUnavailableError",
    "PersonalStrategyWebService",
    "ProductionPersonalStrategyWebService",
    "build_production_personal_strategy_web_service",
    "install_personal_strategy_routes",
]
