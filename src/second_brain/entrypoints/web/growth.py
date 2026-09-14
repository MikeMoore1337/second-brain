"""Private owner-facing Web/API composition for Growth Engine v1.

The application module owns all Growth identities, exact comparisons, mapping
integrity and stale-source checks.  This module only provides a bounded
owner-facing projection and a fail-closed transport boundary.  No raw JSONL
rows, paths or provider payloads cross this boundary.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol, cast
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.growth import (
    GROWTH_CONTRACT_VERSION,
    GROWTH_MAPPING_POLICY_FINGERPRINT,
    GROWTH_MAPPING_POLICY_ID,
    GROWTH_MAX_RESULT_BYTES,
    GROWTH_POLICY_FINGERPRINT,
    GROWTH_POLICY_ID,
    BuildGrowthEngine,
    BuildGrowthGoalContext,
    GrowthEngineRequestV1,
    GrowthEngineResultV1,
    GrowthError,
    GrowthErrorCode,
    GrowthGoalChoiceMappingReviewProjectionV1,
    GrowthGoalChoiceMappingSelectorV1,
    GrowthGoalIdentityV1,
    GrowthGoalRelationV1,
    GrowthGoalSelectionModeV1,
    GrowthGoalSelectionV1,
    GrowthGoalSourceChangedError,
    GrowthMappingAcceptanceRequestV1,
    GrowthMappingLifecycleEventV1,
    GrowthMappingLifecycleStateV1,
    GrowthMappingLifecycleViewV1,
    GrowthMappingReviewRequestV1,
    GrowthMappingStoreUnavailableError,
    build_growth_goal_identity,
    create_growth_engine,
    growth_hash_json,
    validate_growth_engine_result,
)
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    MAX_SELF_MODEL_LIMIT,
    BuildSelfModel,
    SelfModelError,
    SelfModelRequest,
)
from second_brain.config import AppConfig, ConfigurationError, load_config
from second_brain.domain.models import SelfKind, parse_uuid7
from second_brain.entrypoints.web.auth import (
    request_host_is_trusted,
    same_origin_is_trusted,
    trusted_authorities_from_scope,
)

GROWTH_PATH: Final[str] = "/api/growth"
GROWTH_ENGINE_PATH: Final[str] = GROWTH_PATH
GROWTH_GOALS_PATH: Final[str] = "/api/growth/goals"
GROWTH_MAPPING_STATUS_PATH: Final[str] = "/api/growth/mappings/status"
GROWTH_MAPPING_REVIEW_PATH: Final[str] = "/api/growth/mappings/review"
GROWTH_MAPPING_CONFIRM_PATH: Final[str] = "/api/growth/mappings/confirm"
GROWTH_MAPPING_INVALIDATE_PATH: Final[str] = "/api/growth/mappings/invalidate"
GROWTH_MAPPING_DELETE_PATH: Final[str] = "/api/growth/mappings/delete"
GROWTH_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
GROWTH_REQUEST_HEADER_VALUE: Final[str] = "growth-engine-v1"

MAX_RAW_GROWTH_BODY_BYTES: Final[int] = 64 * 1024
MAX_GROWTH_GOALS_RESPONSE_BYTES: Final[int] = 64 * 1024
MAX_GROWTH_STATUS_RESPONSE_BYTES: Final[int] = 64 * 1024
MAX_GROWTH_REVIEW_RESPONSE_BYTES: Final[int] = 32 * 1024
MAX_GROWTH_ACCEPTED_RESPONSE_BYTES: Final[int] = 24 * 1024

_GROWTH_PATHS: Final[frozenset[str]] = frozenset(
    {
        GROWTH_PATH,
        GROWTH_GOALS_PATH,
        GROWTH_MAPPING_STATUS_PATH,
        GROWTH_MAPPING_REVIEW_PATH,
        GROWTH_MAPPING_CONFIRM_PATH,
        GROWTH_MAPPING_INVALIDATE_PATH,
        GROWTH_MAPPING_DELETE_PATH,
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

_GROWTH_MESSAGES: Final[dict[str, tuple[int, str]]] = {
    GrowthErrorCode.INVALID_REQUEST.value: (400, "Запрос Growth не прошёл проверку."),
    GrowthErrorCode.GOAL_SOURCE_UNAVAILABLE.value: (503, "Текущие цели недоступны."),
    GrowthErrorCode.GOAL_MISSING.value: (404, "Выбранная цель больше недоступна."),
    GrowthErrorCode.GOAL_SOURCE_CHANGED.value: (409, "Источник цели изменился; обнови Growth."),
    GrowthErrorCode.MULTIPLE_GOALS_AMBIGUOUS.value: (409, "Нужно явно выбрать одну цель."),
    GrowthErrorCode.GOAL_SELECTION_REQUIRED.value: (409, "Нужно явно выбрать цель."),
    GrowthErrorCode.GOAL_MAPPING_MISSING.value: (409, "Для этого варианта ещё нет связи."),
    GrowthErrorCode.GOAL_MAPPING_INVALID.value: (409, "Связь цели и варианта недопустима."),
    GrowthErrorCode.GOAL_MAPPING_STALE.value: (409, "Review устарел; требуется новая проверка."),
    GrowthErrorCode.BEHAVIORAL_SOURCE_UNAVAILABLE.value: (503, "Наблюдаемый слой недоступен."),
    GrowthErrorCode.BEHAVIORAL_EVIDENCE_INSUFFICIENT.value: (
        409,
        "Сопоставимых поведенческих данных пока недостаточно.",
    ),
    GrowthErrorCode.BEHAVIORAL_STATE_NOT_COMPARABLE.value: (
        409,
        "Текущее состояние нельзя сопоставить по exact-политике.",
    ),
    GrowthErrorCode.RECOMMENDATION_UNAVAILABLE.value: (503, "Независимая рекомендация недоступна."),
    GrowthErrorCode.UNSUPPORTED_SEMANTIC_COMPARISON.value: (
        409,
        "Семантическое сопоставление вне exact-политики запрещено.",
    ),
    GrowthErrorCode.POLICY_MISMATCH.value: (500, "Политика Growth недоступна."),
    GrowthErrorCode.RESULT_TOO_LARGE.value: (413, "Результат Growth слишком велик."),
    GrowthErrorCode.MAPPING_STORE_UNAVAILABLE.value: (
        503,
        "Операционное хранилище Growth недоступно.",
    ),
    GrowthErrorCode.MAPPING_STORE_CORRUPT.value: (
        503,
        "Операционное хранилище Growth не прошло проверку целостности.",
    ),
    GrowthErrorCode.MAPPING_CONFLICT.value: (409, "Связь конфликтует с текущим состоянием."),
    GrowthErrorCode.IDEMPOTENCY_CONFLICT.value: (
        409,
        "Операция уже использована с другим содержимым.",
    ),
    GrowthErrorCode.CONCURRENCY_CONFLICT.value: (409, "Состояние изменилось; повтори операцию."),
}


@dataclass(frozen=True, slots=True)
class GrowthGoalOwnerItemV1:
    """Transient bounded human-readable projection of one current Goal."""

    goal: GrowthGoalIdentityV1
    goal_text: str
    goal_identity_fingerprint: str

    def as_dict(self) -> dict[str, object]:
        return {
            "goal": self.goal.as_dict(),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "goal_text": self.goal_text,
        }


@dataclass(frozen=True, slots=True)
class GrowthGoalsProjectionV1:
    """Transient current Goal list; it carries no raw note body or path."""

    generated_at: datetime
    eligible_goal_count: int
    goals: tuple[GrowthGoalOwnerItemV1, ...]
    reason_codes: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": GROWTH_CONTRACT_VERSION,
            "derivation_version": "growth-engine-derivation-v1",
            "policy_id": GROWTH_POLICY_ID,
            "policy_fingerprint": GROWTH_POLICY_FINGERPRINT,
            "generated_at": self.generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "selection_mode": GrowthGoalSelectionModeV1.EACH_CURRENT_GOAL.value,
            "selected_goal_source_uuid": None,
            "eligible_goal_count": self.eligible_goal_count,
            "goals": [item.as_dict() for item in self.goals],
            "reason_codes": list(self.reason_codes),
            "caveats": list(self.caveats),
        }


class GrowthWebService(Protocol):
    """Minimal injectable seam for Growth read and explicit mapping operations."""

    def goals(self) -> GrowthGoalsProjectionV1:
        """Build current server-owned human-readable Goal projections."""

    def execute(self, request: GrowthEngineRequestV1) -> GrowthEngineResultV1:
        """Build the current deterministic Stage 11B result."""

    def review(
        self,
        request: GrowthMappingReviewRequestV1 | GrowthGoalChoiceMappingSelectorV1,
    ) -> GrowthGoalChoiceMappingReviewProjectionV1:
        """Build one transient exact mapping review."""

    def accept(self, request: GrowthMappingAcceptanceRequestV1) -> object:
        """Append one explicitly confirmed mapping after fresh validation."""

    def snapshot(self) -> tuple[GrowthMappingLifecycleViewV1, ...]:
        """Return bounded lifecycle metadata only."""

    def invalidate(self, mapping_id: UUID, *, operation_id: UUID) -> GrowthMappingLifecycleEventV1:
        """Append an explicit invalidation lifecycle event."""

    def delete(self, mapping_id: UUID, *, operation_id: UUID) -> GrowthMappingLifecycleEventV1:
        """Append an explicit deletion lifecycle event."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class ProductionGrowthWebService:
    """Resolve the existing vault and dedicated mapping store lazily."""

    env_file: Path | None = None
    vault_path_override: str | None = None
    clock: Callable[[], datetime] = _utc_now

    def _config(self) -> AppConfig:
        config = load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )
        if config.env_file is None:
            raise GrowthMappingStoreUnavailableError()
        return config

    def _engine(self) -> BuildGrowthEngine:
        config = self._config()
        env_file = config.env_file
        if env_file is None:
            raise GrowthMappingStoreUnavailableError()
        repository_root = Path(__file__).resolve().parents[4]
        return create_growth_engine(
            FileSystemVaultReader(config.vault_path),
            env_file,
            policy=DEFAULT_SELF_MODEL_POLICY,
            clock=self.clock,
            vault_root=config.vault_path,
            repository_root=repository_root,
        )

    def goals(self) -> GrowthGoalsProjectionV1:
        """Build direct Stage 4 Goals and add only their bounded exact text."""

        config = self._config()
        reader = FileSystemVaultReader(config.vault_path)
        request = GrowthEngineRequestV1(
            selection=GrowthGoalSelectionV1(GrowthGoalSelectionModeV1.EACH_CURRENT_GOAL, None),
        )
        context = BuildGrowthGoalContext(
            reader,
            policy=DEFAULT_SELF_MODEL_POLICY,
            clock=self.clock,
        ).execute(request)
        model = BuildSelfModel(
            reader,
            policy=DEFAULT_SELF_MODEL_POLICY,
            clock=self.clock,
        ).execute(
            SelfModelRequest(
                max_claims=MAX_SELF_MODEL_LIMIT,
                max_evidence_refs_per_claim=MAX_SELF_MODEL_LIMIT,
            )
        )
        items: list[GrowthGoalOwnerItemV1] = []
        for goal in context.goals:
            matches = tuple(
                claim
                for claim in model.claims
                if claim.dimension.value == SelfKind.GOAL.value
                and len(claim.supporting_evidence) == 1
                and claim.supporting_evidence[0].note_id == goal.source_note_uuid
            )
            if len(matches) != 1:
                raise GrowthGoalSourceChangedError()
            claim = matches[0]
            rebuilt = build_growth_goal_identity(
                claim,
                model,
                policy=DEFAULT_SELF_MODEL_POLICY,
                expected_self_model_policy_fingerprint=goal.self_model_policy_fingerprint,
            )
            if rebuilt != goal:
                raise GrowthGoalSourceChangedError()
            if type(claim.claim) is not str or not claim.claim:
                raise GrowthGoalSourceChangedError()
            items.append(
                GrowthGoalOwnerItemV1(
                    goal=goal,
                    goal_text=claim.claim,
                    goal_identity_fingerprint=growth_hash_json(goal.as_dict()),
                )
            )
        return GrowthGoalsProjectionV1(
            generated_at=model.generated_at,
            eligible_goal_count=context.eligible_goal_count,
            goals=tuple(items),
            reason_codes=context.reason_codes,
            caveats=context.caveats,
        )

    def execute(self, request: GrowthEngineRequestV1) -> GrowthEngineResultV1:
        return self._engine().execute(request)

    def review(
        self,
        request: GrowthMappingReviewRequestV1 | GrowthGoalChoiceMappingSelectorV1,
    ) -> GrowthGoalChoiceMappingReviewProjectionV1:
        return self._engine().review(request)

    def accept(self, request: GrowthMappingAcceptanceRequestV1) -> object:
        return self._engine().accept(request)

    def snapshot(self) -> tuple[GrowthMappingLifecycleViewV1, ...]:
        return self._engine().store.read_verified_snapshot()

    def invalidate(self, mapping_id: UUID, *, operation_id: UUID) -> GrowthMappingLifecycleEventV1:
        return self._engine().store.invalidate_mapping(mapping_id, operation_id=operation_id)

    def delete(self, mapping_id: UUID, *, operation_id: UUID) -> GrowthMappingLifecycleEventV1:
        return self._engine().store.delete_mapping(mapping_id, operation_id=operation_id)


def build_production_growth_web_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> ProductionGrowthWebService:
    return ProductionGrowthWebService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


class GrowthEmptyPayload(BaseModel):
    """Strict empty body for current Goal and lifecycle reads."""

    model_config = ConfigDict(extra="forbid", strict=True)


class GrowthSelectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: StrictStr
    source_note_uuid: StrictStr | None


class GrowthEnginePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    contract_version: StrictStr
    selection: GrowthSelectionPayload
    max_results: StrictInt
    max_result_bytes: StrictInt


class GrowthMappingSelectorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_note_uuid: StrictStr
    behavioral_cohort_fingerprint: StrictStr
    behavioral_option_index: StrictInt
    behavioral_option_fingerprint: StrictStr


class GrowthMappingReviewPayload(GrowthMappingSelectorPayload):
    relation: StrictStr


class GrowthMappingConfirmPayload(GrowthMappingSelectorPayload):
    operation_id: StrictStr
    relation: StrictStr
    confirmed: StrictBool
    review_fingerprint: StrictStr
    supersedes_mapping_id: StrictStr | None = None


class GrowthMappingLifecyclePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mapping_id: StrictStr
    operation_id: StrictStr
    confirmed: StrictBool


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


def _error_response(error: GrowthError, *, status_code: int | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code if status_code is not None else _GROWTH_MESSAGES[error.code][0],
        content={
            "error": {
                "code": error.code,
                "message": _GROWTH_MESSAGES.get(
                    error.code, (503, "Операция Growth сейчас недоступна.")
                )[1],
            }
        },
        headers=_API_HEADERS,
    )


def _invalid_request() -> JSONResponse:
    return _error_response(GrowthError(GrowthErrorCode.INVALID_REQUEST))


def _too_large() -> JSONResponse:
    return _error_response(
        GrowthError(GrowthErrorCode.RESULT_TOO_LARGE),
        status_code=413,
    )


def _method_not_allowed() -> JSONResponse:
    response = _invalid_request()
    response.status_code = 405
    response.headers["Allow"] = "POST"
    return response


async def _send(response: Response, scope: Scope, receive: Receive, send: Send) -> None:
    await response(scope, receive, send)


class GrowthRequestBoundaryMiddleware:
    """Fail-closed trusted Host, same-origin, purpose and raw JSON cap."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _GROWTH_PATHS:
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
            or purpose != GROWTH_REQUEST_HEADER_VALUE
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
            if declared > MAX_RAW_GROWTH_BODY_BYTES:
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
            if len(body) > MAX_RAW_GROWTH_BODY_BYTES:
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
    if not raw or len(raw) > MAX_RAW_GROWTH_BODY_BYTES:
        raise ValueError
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError:
        raise ValueError from None


def _uuid7_text(value: str) -> UUID:
    try:
        parsed = parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        raise ValueError from None
    if type(parsed) is not UUID or parsed.version != 7 or str(parsed) != value:
        raise ValueError
    return parsed


def _engine_request(payload: GrowthEnginePayload) -> GrowthEngineRequestV1:
    try:
        return GrowthEngineRequestV1.from_dict(payload.model_dump(mode="json"))
    except GrowthError:
        raise
    except TypeError, ValueError, UnicodeError:
        raise GrowthError(GrowthErrorCode.INVALID_REQUEST) from None


def _selector(payload: GrowthMappingSelectorPayload) -> GrowthGoalChoiceMappingSelectorV1:
    try:
        return GrowthGoalChoiceMappingSelectorV1(
            source_note_uuid=_uuid7_text(payload.source_note_uuid),
            behavioral_cohort_fingerprint=payload.behavioral_cohort_fingerprint,
            behavioral_option_index=payload.behavioral_option_index,
            behavioral_option_fingerprint=payload.behavioral_option_fingerprint,
        )
    except TypeError, ValueError, UnicodeError:
        raise GrowthError(GrowthErrorCode.INVALID_REQUEST) from None


def _relation(value: str) -> GrowthGoalRelationV1:
    try:
        return GrowthGoalRelationV1(value)
    except TypeError, ValueError:
        raise GrowthError(GrowthErrorCode.INVALID_REQUEST) from None


def _operation_id(value: str) -> UUID:
    try:
        return _uuid7_text(value)
    except ValueError:
        raise GrowthError(GrowthErrorCode.INVALID_REQUEST) from None


def _mapping_id(value: str) -> UUID:
    return _operation_id(value)


def _json_response(body: dict[str, object], *, max_bytes: int) -> Response:
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    if len(encoded) > max_bytes:
        raise GrowthError(GrowthErrorCode.RESULT_TOO_LARGE)
    return Response(content=encoded, media_type="application/json", headers=_API_HEADERS)


def _growth_result_response(result: GrowthEngineResultV1) -> Response:
    validate_growth_engine_result(result)
    encoded = result.to_json().encode("utf-8")
    if len(encoded) > GROWTH_MAX_RESULT_BYTES:
        raise GrowthError(GrowthErrorCode.RESULT_TOO_LARGE)
    return Response(content=encoded, media_type="application/json", headers=_API_HEADERS)


def _lifecycle_projection(view: GrowthMappingLifecycleViewV1) -> dict[str, object]:
    mapping = view.mapping
    target = mapping.behavioral_target
    lifecycle = cast(GrowthMappingLifecycleStateV1, view.lifecycle_state)
    relation = cast(GrowthGoalRelationV1, mapping.relation)
    return {
        "mapping_id": str(mapping.mapping_id),
        "lifecycle_state": lifecycle.value,
        "goal": {
            "source_note_uuid": str(mapping.goal.source_note_uuid),
            "domain": mapping.goal.domain,
            "source_fingerprint": mapping.goal.source_fingerprint,
            "claim_fingerprint": mapping.goal.claim_fingerprint,
        },
        "behavioral_target": {
            "cohort_fingerprint": target.cohort.cohort_fingerprint,
            "option_index": target.option.option_index,
            "option_fingerprint": target.option.option_fingerprint,
        },
        "relation": relation.value,
        "mapping_fingerprint": mapping.mapping_fingerprint,
        "mapping_policy_id": mapping.mapping_policy_id,
        "mapping_policy_fingerprint": mapping.mapping_policy_fingerprint,
        "created_at": mapping.created_at.isoformat().replace("+00:00", "Z"),
        "reviewed_at": mapping.reviewed_at.isoformat().replace("+00:00", "Z"),
        "supersedes_mapping_id": (
            str(mapping.supersedes_mapping_id)
            if mapping.supersedes_mapping_id is not None
            else None
        ),
    }


def _status_body(service: GrowthWebService) -> dict[str, object]:
    views = service.snapshot()
    if type(views) is not tuple or any(
        type(view) is not GrowthMappingLifecycleViewV1 for view in views
    ):
        raise GrowthError(GrowthErrorCode.MAPPING_STORE_CORRUPT)
    mappings = [_lifecycle_projection(view) for view in views]
    return {
        "mapping_policy_id": GROWTH_MAPPING_POLICY_ID,
        "mapping_policy_fingerprint": GROWTH_MAPPING_POLICY_FINGERPRINT,
        "mappings": mappings,
        "active_mapping_count": sum(item["lifecycle_state"] == "active" for item in mappings),
    }


def _projection_response(projection: GrowthGoalChoiceMappingReviewProjectionV1) -> Response:
    encoded = projection.to_json().encode("utf-8")
    if len(encoded) > MAX_GROWTH_REVIEW_RESPONSE_BYTES:
        raise GrowthError(GrowthErrorCode.RESULT_TOO_LARGE)
    return Response(content=encoded, media_type="application/json", headers=_API_HEADERS)


def _accepted_response(accepted: object) -> Response:
    as_dict = getattr(accepted, "as_dict", None)
    if not callable(as_dict):
        raise GrowthError(GrowthErrorCode.GOAL_MAPPING_INVALID)
    return _json_response(
        {"status": "accepted", "mapping": as_dict()},
        max_bytes=MAX_GROWTH_ACCEPTED_RESPONSE_BYTES,
    )


def _event_response(event: GrowthMappingLifecycleEventV1) -> Response:
    return _json_response(
        {"status": "updated", "event": event.as_dict()},
        max_bytes=MAX_GROWTH_ACCEPTED_RESPONSE_BYTES,
    )


def install_growth_routes(
    app: FastAPI,
    *,
    service: GrowthWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install hidden private Growth read, review and lifecycle routes."""

    actual_service = service or build_production_growth_web_service(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    app.add_middleware(GrowthRequestBoundaryMiddleware)

    @app.post(GROWTH_GOALS_PATH, include_in_schema=False)
    async def growth_goals_endpoint(request: Request) -> Response:
        try:
            payload = GrowthEmptyPayload.model_validate(await _read_json(request), strict=True)
            del payload
            projection = await run_in_threadpool(actual_service.goals)
            if type(projection) is not GrowthGoalsProjectionV1:
                raise GrowthError(GrowthErrorCode.GOAL_SOURCE_CHANGED)
            return _json_response(projection.as_dict(), max_bytes=MAX_GROWTH_GOALS_RESPONSE_BYTES)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except GrowthError as error:
            return _error_response(error)
        except ConfigurationError, SelfModelError:
            return _error_response(GrowthError(GrowthErrorCode.GOAL_SOURCE_UNAVAILABLE))
        except Exception:
            return _error_response(GrowthError(GrowthErrorCode.GOAL_SOURCE_UNAVAILABLE))

    @app.post(GROWTH_PATH, include_in_schema=False)
    async def growth_engine_endpoint(request: Request) -> Response:
        try:
            payload = GrowthEnginePayload.model_validate(await _read_json(request), strict=True)
            result = await run_in_threadpool(actual_service.execute, _engine_request(payload))
            return _growth_result_response(result)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except GrowthError as error:
            return _error_response(error)
        except ConfigurationError, SelfModelError:
            return _error_response(GrowthError(GrowthErrorCode.GOAL_SOURCE_UNAVAILABLE))
        except Exception:
            return _error_response(GrowthError(GrowthErrorCode.GOAL_SOURCE_UNAVAILABLE))

    @app.post(GROWTH_MAPPING_STATUS_PATH, include_in_schema=False)
    async def growth_mapping_status_endpoint(request: Request) -> Response:
        try:
            payload = GrowthEmptyPayload.model_validate(await _read_json(request), strict=True)
            del payload
            body = await run_in_threadpool(_status_body, actual_service)
            return _json_response(body, max_bytes=MAX_GROWTH_STATUS_RESPONSE_BYTES)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except GrowthError as error:
            return _error_response(error)
        except ConfigurationError, SelfModelError:
            return _error_response(GrowthError(GrowthErrorCode.MAPPING_STORE_UNAVAILABLE))
        except Exception:
            return _error_response(GrowthError(GrowthErrorCode.MAPPING_STORE_UNAVAILABLE))

    @app.post(GROWTH_MAPPING_REVIEW_PATH, include_in_schema=False)
    async def growth_mapping_review_endpoint(request: Request) -> Response:
        try:
            payload = GrowthMappingReviewPayload.model_validate(
                await _read_json(request), strict=True
            )
            selector = _selector(payload)
            relation = _relation(payload.relation)
            projection = await run_in_threadpool(
                actual_service.review,
                GrowthMappingReviewRequestV1(selector=selector, relation=relation),
            )
            if projection.proposed_relation is not relation:
                raise GrowthError(GrowthErrorCode.GOAL_MAPPING_STALE)
            return _projection_response(projection)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except GrowthError as error:
            return _error_response(error)
        except ConfigurationError, SelfModelError:
            return _error_response(GrowthError(GrowthErrorCode.GOAL_SOURCE_UNAVAILABLE))
        except Exception:
            return _error_response(GrowthError(GrowthErrorCode.GOAL_SOURCE_UNAVAILABLE))

    @app.post(GROWTH_MAPPING_CONFIRM_PATH, include_in_schema=False)
    async def growth_mapping_confirm_endpoint(request: Request) -> Response:
        try:
            payload = GrowthMappingConfirmPayload.model_validate(
                await _read_json(request), strict=True
            )
            if not payload.confirmed:
                raise GrowthError(GrowthErrorCode.INVALID_REQUEST)
            selector = _selector(payload)
            relation = _relation(payload.relation)
            fresh_review = await run_in_threadpool(
                actual_service.review,
                GrowthMappingReviewRequestV1(selector=selector, relation=relation),
            )
            if fresh_review.candidate_mapping_fingerprint != payload.review_fingerprint:
                raise GrowthError(GrowthErrorCode.GOAL_MAPPING_STALE)
            accepted = await run_in_threadpool(
                actual_service.accept,
                GrowthMappingAcceptanceRequestV1(
                    selector=selector,
                    operation_id=_operation_id(payload.operation_id),
                    relation=relation,
                    confirmed=True,
                    review_projection=fresh_review,
                    supersedes_mapping_id=(
                        _mapping_id(payload.supersedes_mapping_id)
                        if payload.supersedes_mapping_id is not None
                        else None
                    ),
                ),
            )
            return _accepted_response(accepted)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except GrowthError as error:
            return _error_response(error)
        except ConfigurationError, SelfModelError:
            return _error_response(GrowthError(GrowthErrorCode.MAPPING_STORE_UNAVAILABLE))
        except Exception:
            return _error_response(GrowthError(GrowthErrorCode.MAPPING_STORE_UNAVAILABLE))

    async def _lifecycle_endpoint(request: Request, *, action: str) -> Response:
        try:
            payload = GrowthMappingLifecyclePayload.model_validate(
                await _read_json(request), strict=True
            )
            if not payload.confirmed:
                raise GrowthError(GrowthErrorCode.INVALID_REQUEST)
            mapping_id = _mapping_id(payload.mapping_id)
            operation_id = _operation_id(payload.operation_id)
            method = getattr(actual_service, action, None)
            if not callable(method):
                raise GrowthError(GrowthErrorCode.MAPPING_STORE_UNAVAILABLE)
            event = await run_in_threadpool(method, mapping_id, operation_id=operation_id)
            if type(event) is not GrowthMappingLifecycleEventV1:
                raise GrowthError(GrowthErrorCode.MAPPING_STORE_UNAVAILABLE)
            return _event_response(event)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except GrowthError as error:
            return _error_response(error)
        except ConfigurationError, SelfModelError:
            return _error_response(GrowthError(GrowthErrorCode.MAPPING_STORE_UNAVAILABLE))
        except Exception:
            return _error_response(GrowthError(GrowthErrorCode.MAPPING_STORE_UNAVAILABLE))

    @app.post(GROWTH_MAPPING_INVALIDATE_PATH, include_in_schema=False)
    async def growth_mapping_invalidate_endpoint(request: Request) -> Response:
        return await _lifecycle_endpoint(request, action="invalidate")

    @app.post(GROWTH_MAPPING_DELETE_PATH, include_in_schema=False)
    async def growth_mapping_delete_endpoint(request: Request) -> Response:
        return await _lifecycle_endpoint(request, action="delete")


__all__ = [
    "GROWTH_ENGINE_PATH",
    "GROWTH_GOALS_PATH",
    "GROWTH_MAPPING_CONFIRM_PATH",
    "GROWTH_MAPPING_DELETE_PATH",
    "GROWTH_MAPPING_INVALIDATE_PATH",
    "GROWTH_MAPPING_REVIEW_PATH",
    "GROWTH_MAPPING_STATUS_PATH",
    "GROWTH_PATH",
    "GROWTH_REQUEST_HEADER_NAME",
    "GROWTH_REQUEST_HEADER_VALUE",
    "MAX_GROWTH_ACCEPTED_RESPONSE_BYTES",
    "MAX_GROWTH_GOALS_RESPONSE_BYTES",
    "MAX_GROWTH_REVIEW_RESPONSE_BYTES",
    "MAX_GROWTH_STATUS_RESPONSE_BYTES",
    "MAX_RAW_GROWTH_BODY_BYTES",
    "GrowthEmptyPayload",
    "GrowthEnginePayload",
    "GrowthGoalOwnerItemV1",
    "GrowthGoalsProjectionV1",
    "GrowthMappingConfirmPayload",
    "GrowthMappingLifecyclePayload",
    "GrowthMappingReviewPayload",
    "GrowthMappingSelectorPayload",
    "GrowthRequestBoundaryMiddleware",
    "GrowthWebService",
    "ProductionGrowthWebService",
    "build_production_growth_web_service",
    "install_growth_routes",
]
