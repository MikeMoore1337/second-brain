"""Private owner-only Web/API boundary for Adaptive Cognitive Twin v1.

The application modules remain the only owners of Stage 15 source, candidate,
projection and store semantics.  This module accepts bounded selectors,
rebuilds the current typed source pack, and exposes a transient owner
projection.  It never accepts a raw source/candidate/profile as authority and
never calls a provider or network service.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Protocol, cast
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.adaptive_cognitive_twin import (
    ADAPTIVE_EVALUATION_POLICY_ID,
    ADAPTIVE_POLICY_FINGERPRINT,
    AdaptiveCognitiveTwinInputError,
    AdaptiveSourceSnapshotV1,
    Stage14ExperimentSelectorV1,
    Stage15CandidateV1,
    Stage15EvaluationPlanV1,
    Stage15MeasureV1,
    load_adaptive_source_snapshot,
)
from second_brain.application.adaptive_cognitive_twin_projection import (
    ADAPTIVE_NON_CAUSAL_PHRASE,
    MAX_ADAPTIVE_PROJECTION_BYTES,
    Stage15AdaptiveProjectionV1,
    evaluate_adaptive_profile,
    project_adaptive_cognitive_twin,
)
from second_brain.application.adaptive_cognitive_twin_store import (
    AdaptiveCognitiveTwinStore,
    AdaptiveCognitiveTwinStoreError,
    AdaptiveStoreErrorCodeV1,
    derive_adaptive_cognitive_twin_store_root,
)
from second_brain.application.goal_progress_read import GoalProgressRequestV1
from second_brain.application.growth import (
    GrowthEngineRequestV1,
    GrowthGoalIdentityV1,
    GrowthGoalSelectionModeV1,
    GrowthGoalSelectionV1,
    growth_hash_json,
)
from second_brain.application.personal_experiments import (
    PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
    PersonalExperimentChainStateV1,
    PersonalExperimentReassessmentRecordV1,
    validate_personal_experiment_reassessment_chain,
)
from second_brain.application.personal_experiments_evaluator import (
    PersonalExperimentEvaluationRequestV1,
    PersonalExperimentEvaluationResultV1,
    evaluate_personal_experiment,
)
from second_brain.application.prospective_audit import (
    ProspectiveCalibrationRequestV1,
    ProspectiveCalibrationResultV1,
)
from second_brain.application.reports import ScanReport
from second_brain.application.validation import build_report
from second_brain.config import AppConfig, ConfigurationError, load_config
from second_brain.domain.models import parse_rfc3339, parse_uuid7
from second_brain.entrypoints.web.auth import (
    request_host_is_trusted,
    same_origin_is_trusted,
    trusted_authorities_from_scope,
)
from second_brain.entrypoints.web.goal_progress import ProductionGoalProgressWebService
from second_brain.entrypoints.web.growth import ProductionGrowthWebService
from second_brain.entrypoints.web.prospective_audit import (
    PRODUCTION_PROSPECTIVE_AUDIT_OWNER_GROUP,
    PRODUCTION_PROSPECTIVE_AUDIT_STORE_ROOT,
    build_production_prospective_audit_service,
)

ADAPTIVE_COGNITIVE_TWIN_STATE_PATH: Final[str] = "/api/adaptive-cognitive-twin/state"
ADAPTIVE_COGNITIVE_TWIN_CANDIDATE_PATH: Final[str] = "/api/adaptive-cognitive-twin/candidate"
ADAPTIVE_COGNITIVE_TWIN_REVIEW_PATH: Final[str] = "/api/adaptive-cognitive-twin/review"
ADAPTIVE_COGNITIVE_TWIN_ACTIVATE_PATH: Final[str] = "/api/adaptive-cognitive-twin/activate"
ADAPTIVE_COGNITIVE_TWIN_REJECT_PATH: Final[str] = "/api/adaptive-cognitive-twin/reject"
ADAPTIVE_COGNITIVE_TWIN_EVALUATE_PATH: Final[str] = "/api/adaptive-cognitive-twin/evaluate"
ADAPTIVE_COGNITIVE_TWIN_SUPERSEDE_PATH: Final[str] = "/api/adaptive-cognitive-twin/supersede"
ADAPTIVE_COGNITIVE_TWIN_REVERT_PATH: Final[str] = "/api/adaptive-cognitive-twin/revert"

ADAPTIVE_COGNITIVE_TWIN_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
ADAPTIVE_COGNITIVE_TWIN_REQUEST_HEADER_VALUE: Final[str] = "adaptive-cognitive-twin-v1"
MAX_RAW_ADAPTIVE_COGNITIVE_TWIN_BODY_BYTES: Final[int] = 64 * 1024
MAX_ADAPTIVE_COGNITIVE_TWIN_STATE_RESPONSE_BYTES: Final[int] = 256 * 1024
MAX_ADAPTIVE_COGNITIVE_TWIN_MUTATION_RESPONSE_BYTES: Final[int] = 192 * 1024

_ADAPTIVE_COGNITIVE_TWIN_PATHS: Final[frozenset[str]] = frozenset(
    {
        ADAPTIVE_COGNITIVE_TWIN_STATE_PATH,
        ADAPTIVE_COGNITIVE_TWIN_CANDIDATE_PATH,
        ADAPTIVE_COGNITIVE_TWIN_REVIEW_PATH,
        ADAPTIVE_COGNITIVE_TWIN_ACTIVATE_PATH,
        ADAPTIVE_COGNITIVE_TWIN_REJECT_PATH,
        ADAPTIVE_COGNITIVE_TWIN_EVALUATE_PATH,
        ADAPTIVE_COGNITIVE_TWIN_SUPERSEDE_PATH,
        ADAPTIVE_COGNITIVE_TWIN_REVERT_PATH,
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

ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST: Final[str] = "ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST"
ADAPTIVE_COGNITIVE_TWIN_AUTH_REQUIRED: Final[str] = "ADAPTIVE_COGNITIVE_TWIN_AUTH_REQUIRED"
ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE: Final[str] = (
    "ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE"
)
ADAPTIVE_COGNITIVE_TWIN_INSUFFICIENT_EVIDENCE: Final[str] = (
    "ADAPTIVE_COGNITIVE_TWIN_INSUFFICIENT_EVIDENCE"
)
ADAPTIVE_COGNITIVE_TWIN_NOT_COMPARABLE: Final[str] = "ADAPTIVE_COGNITIVE_TWIN_NOT_COMPARABLE"
ADAPTIVE_COGNITIVE_TWIN_SOURCE_CHANGED: Final[str] = "ADAPTIVE_COGNITIVE_TWIN_SOURCE_CHANGED"
ADAPTIVE_COGNITIVE_TWIN_POLICY_MISMATCH: Final[str] = "ADAPTIVE_COGNITIVE_TWIN_POLICY_MISMATCH"
ADAPTIVE_COGNITIVE_TWIN_STALE_CANDIDATE: Final[str] = "ADAPTIVE_COGNITIVE_TWIN_STALE_CANDIDATE"
ADAPTIVE_COGNITIVE_TWIN_IDEMPOTENCY_CONFLICT: Final[str] = (
    "ADAPTIVE_COGNITIVE_TWIN_IDEMPOTENCY_CONFLICT"
)
ADAPTIVE_COGNITIVE_TWIN_CONCURRENCY_CONFLICT: Final[str] = (
    "ADAPTIVE_COGNITIVE_TWIN_CONCURRENCY_CONFLICT"
)
ADAPTIVE_COGNITIVE_TWIN_STORE_UNAVAILABLE: Final[str] = "ADAPTIVE_COGNITIVE_TWIN_STORE_UNAVAILABLE"
ADAPTIVE_COGNITIVE_TWIN_STORE_CORRUPT: Final[str] = "ADAPTIVE_COGNITIVE_TWIN_STORE_CORRUPT"
ADAPTIVE_COGNITIVE_TWIN_RESULT_TOO_LARGE: Final[str] = "ADAPTIVE_COGNITIVE_TWIN_RESULT_TOO_LARGE"

_ERROR_MESSAGES: Final[dict[str, str]] = {
    ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST: "Запрос адаптивного слоя недействителен.",
    ADAPTIVE_COGNITIVE_TWIN_AUTH_REQUIRED: "Требуется вход владельца.",
    ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE: (
        "Точный источник адаптивного слоя сейчас недоступен."
    ),
    ADAPTIVE_COGNITIVE_TWIN_INSUFFICIENT_EVIDENCE: (
        "Недостаточно точных проверенных данных для предложения изменения."
    ),
    ADAPTIVE_COGNITIVE_TWIN_NOT_COMPARABLE: "Выбранные источники нельзя безопасно сопоставить.",
    ADAPTIVE_COGNITIVE_TWIN_SOURCE_CHANGED: "Источник изменился; требуется новая проверка.",
    ADAPTIVE_COGNITIVE_TWIN_POLICY_MISMATCH: "Версия политики адаптивного слоя недействительна.",
    ADAPTIVE_COGNITIVE_TWIN_STALE_CANDIDATE: "Предложение устарело; сначала пересоберите его.",
    ADAPTIVE_COGNITIVE_TWIN_IDEMPOTENCY_CONFLICT: (
        "Операция конфликтует с уже обработанным запросом."
    ),
    ADAPTIVE_COGNITIVE_TWIN_CONCURRENCY_CONFLICT: (
        "Состояние изменилось; требуется повторная проверка."
    ),
    ADAPTIVE_COGNITIVE_TWIN_STORE_UNAVAILABLE: (
        "Операционное состояние адаптивного слоя недоступно."
    ),
    ADAPTIVE_COGNITIVE_TWIN_STORE_CORRUPT: (
        "Операционное состояние адаптивного слоя не прошло проверку целостности."
    ),
    ADAPTIVE_COGNITIVE_TWIN_RESULT_TOO_LARGE: (
        "Результат адаптивного слоя превышает допустимый размер."
    ),
}


class AdaptiveCognitiveTwinWebError(RuntimeError):
    """Fixed-code error that never carries source or filesystem details."""

    def __init__(self, code: str) -> None:
        if code not in _ERROR_MESSAGES:
            code = ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE
        self.code = code
        super().__init__(code)


class AdaptiveCognitiveTwinStatePayload(BaseModel):
    """Optional exact Goal selector used to load discovery or selected state."""

    model_config = ConfigDict(extra="forbid", strict=True)

    goal_source_uuid: StrictStr | None = None
    goal_identity_fingerprint: StrictStr | None = None
    as_of: StrictStr | None = None
    stage14_experiment_definition_id: StrictStr | None = None
    stage14_experiment_definition_fingerprint: StrictStr | None = None


class AdaptiveCognitiveTwinCandidatePayload(BaseModel):
    """Exact source selectors; the candidate is always rebuilt server-side."""

    model_config = ConfigDict(extra="forbid", strict=True)

    goal_source_uuid: StrictStr
    goal_identity_fingerprint: StrictStr
    as_of: StrictStr
    stage14_experiment_definition_id: StrictStr | None = None
    stage14_experiment_definition_fingerprint: StrictStr | None = None


class AdaptiveCognitiveTwinCandidateOperationPayload(AdaptiveCognitiveTwinCandidatePayload):
    """Candidate fingerprint and operation identity for one lifecycle action."""

    candidate_fingerprint: StrictStr
    source_snapshot_fingerprint: StrictStr
    operation_id: StrictStr
    confirmed: StrictBool


class AdaptiveCognitiveTwinReviewPayload(AdaptiveCognitiveTwinCandidateOperationPayload):
    """Explicit owner review event."""


class AdaptiveCognitiveTwinActivatePayload(AdaptiveCognitiveTwinCandidateOperationPayload):
    """Explicit owner activation event."""


class AdaptiveCognitiveTwinRejectPayload(AdaptiveCognitiveTwinCandidateOperationPayload):
    """Explicit owner rejection event."""


class AdaptiveCognitiveTwinSupersedePayload(AdaptiveCognitiveTwinCandidateOperationPayload):
    """Explicit replacement of the exact current active profile."""

    prior_profile_id: StrictStr
    prior_profile_fingerprint: StrictStr


class AdaptiveCognitiveTwinEvaluatePayload(AdaptiveCognitiveTwinCandidatePayload):
    """Explicit later comparison of one exact active profile."""

    active_profile_id: StrictStr
    active_profile_fingerprint: StrictStr
    activation_source_snapshot_fingerprint: StrictStr
    operation_id: StrictStr
    confirmed: StrictBool


class AdaptiveCognitiveTwinRevertPayload(AdaptiveCognitiveTwinCandidatePayload):
    """Explicit revert to one exact previous profile version."""

    target_profile_id: StrictStr
    target_profile_fingerprint: StrictStr
    operation_id: StrictStr
    confirmed: StrictBool


class AdaptiveCognitiveTwinWebService(Protocol):
    """Injectable seam for the private owner-facing transport."""

    def state(self, payload: AdaptiveCognitiveTwinStatePayload) -> Mapping[str, object]: ...

    def candidate(self, payload: AdaptiveCognitiveTwinCandidatePayload) -> Mapping[str, object]: ...

    def review(self, payload: AdaptiveCognitiveTwinReviewPayload) -> Mapping[str, object]: ...

    def activate(self, payload: AdaptiveCognitiveTwinActivatePayload) -> Mapping[str, object]: ...

    def reject(self, payload: AdaptiveCognitiveTwinRejectPayload) -> Mapping[str, object]: ...

    def evaluate(self, payload: AdaptiveCognitiveTwinEvaluatePayload) -> Mapping[str, object]: ...

    def supersede(self, payload: AdaptiveCognitiveTwinSupersedePayload) -> Mapping[str, object]: ...

    def revert(self, payload: AdaptiveCognitiveTwinRevertPayload) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class _Selection:
    goal_source_uuid: UUID
    goal_identity_fingerprint: str
    as_of: datetime
    stage14_selector: Stage14ExperimentSelectorV1 | None


@dataclass(frozen=True, slots=True)
class _Context:
    selection: _Selection
    snapshot: AdaptiveSourceSnapshotV1
    projection: Stage15AdaptiveProjectionV1
    store: AdaptiveCognitiveTwinStore | None


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


def _json_response(
    body: Mapping[str, object], *, max_bytes: int, status_code: int = 200
) -> Response:
    try:
        encoded = json.dumps(
            dict(body),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
        raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE) from exc
    if len(encoded) > max_bytes:
        return _error_response(
            ADAPTIVE_COGNITIVE_TWIN_RESULT_TOO_LARGE,
            status_code=413,
        )
    return Response(
        content=encoded,
        status_code=status_code,
        media_type="application/json",
        headers=_API_HEADERS,
    )


def _error_response(code: str, *, status_code: int) -> JSONResponse:
    safe_code = code if code in _ERROR_MESSAGES else ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": safe_code, "message": _ERROR_MESSAGES[safe_code]}},
        headers=_API_HEADERS,
    )


def _invalid_request() -> JSONResponse:
    return _error_response(ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST, status_code=400)


def _too_large() -> JSONResponse:
    return _error_response(ADAPTIVE_COGNITIVE_TWIN_RESULT_TOO_LARGE, status_code=413)


def _method_not_allowed() -> JSONResponse:
    return _error_response(ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST, status_code=405)


async def _send(response: Response, scope: Scope, receive: Receive, send: Send) -> None:
    await response(scope, receive, send)


class AdaptiveCognitiveTwinRequestBoundaryMiddleware:
    """Fail-closed same-origin, purpose and bounded raw-body boundary."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _ADAPTIVE_COGNITIVE_TWIN_PATHS:
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
            or purpose != ADAPTIVE_COGNITIVE_TWIN_REQUEST_HEADER_VALUE
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
            if declared > MAX_RAW_ADAPTIVE_COGNITIVE_TWIN_BODY_BYTES:
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
            if len(body) > MAX_RAW_ADAPTIVE_COGNITIVE_TWIN_BODY_BYTES:
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
    if not raw or len(raw) > MAX_RAW_ADAPTIVE_COGNITIVE_TWIN_BODY_BYTES:
        raise ValueError
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError:
        raise ValueError from None
    return payload_type.model_validate(value, strict=True)


def _uuid7_text(value: str) -> UUID:
    try:
        parsed = parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        raise ValueError from None
    if type(parsed) is not UUID or parsed.version != 7 or str(parsed) != value:
        raise ValueError
    return parsed


def _timestamp_text(value: str) -> datetime:
    try:
        parsed = parse_rfc3339(value)
    except TypeError, ValueError, OverflowError:
        raise ValueError from None
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset() != timedelta(0):
        raise ValueError
    return parsed.astimezone(UTC)


def _selection_from_payload(
    payload: AdaptiveCognitiveTwinCandidatePayload,
) -> _Selection:
    try:
        goal_id = _uuid7_text(payload.goal_source_uuid)
        goal_fingerprint = payload.goal_identity_fingerprint
        from second_brain.application.adaptive_cognitive_twin import validate_adaptive_hash

        validate_adaptive_hash(goal_fingerprint)
        as_of = _timestamp_text(payload.as_of)
        selector_id = payload.stage14_experiment_definition_id
        selector_fingerprint = payload.stage14_experiment_definition_fingerprint
        if (selector_id is None) != (selector_fingerprint is None):
            raise ValueError
        selector = (
            Stage14ExperimentSelectorV1(
                _uuid7_text(selector_id),
                selector_fingerprint,
            )
            if selector_id is not None and selector_fingerprint is not None
            else None
        )
    except AdaptiveCognitiveTwinInputError, TypeError, ValueError, OverflowError:
        raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST) from None
    return _Selection(goal_id, goal_fingerprint, as_of, selector)


def _selection_from_state_payload(payload: AdaptiveCognitiveTwinStatePayload) -> _Selection | None:
    values = (
        payload.goal_source_uuid,
        payload.goal_identity_fingerprint,
        payload.as_of,
        payload.stage14_experiment_definition_id,
        payload.stage14_experiment_definition_fingerprint,
    )
    if all(value is None for value in values):
        return None
    if payload.goal_source_uuid is None or payload.goal_identity_fingerprint is None:
        raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST)
    as_of = payload.as_of or _utc_now().isoformat().replace("+00:00", "Z")
    candidate_payload = AdaptiveCognitiveTwinCandidatePayload(
        goal_source_uuid=payload.goal_source_uuid,
        goal_identity_fingerprint=payload.goal_identity_fingerprint,
        as_of=as_of,
        stage14_experiment_definition_id=payload.stage14_experiment_definition_id,
        stage14_experiment_definition_fingerprint=payload.stage14_experiment_definition_fingerprint,
    )
    return _selection_from_payload(candidate_payload)


def _operation_id(value: str) -> UUID:
    try:
        return _uuid7_text(value)
    except ValueError:
        raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST) from None


def _utc_now(clock: Callable[[], datetime] | None = None) -> datetime:
    value = clock() if clock is not None else datetime.now(UTC)
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE)
    return value.astimezone(UTC)


def _store_error(error: AdaptiveCognitiveTwinStoreError) -> AdaptiveCognitiveTwinWebError:
    mapping = {
        AdaptiveStoreErrorCodeV1.INVALID_REQUEST.value: ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST,
        AdaptiveStoreErrorCodeV1.STORE_UNAVAILABLE.value: ADAPTIVE_COGNITIVE_TWIN_STORE_UNAVAILABLE,
        AdaptiveStoreErrorCodeV1.STORE_CORRUPT.value: ADAPTIVE_COGNITIVE_TWIN_STORE_CORRUPT,
        AdaptiveStoreErrorCodeV1.IDEMPOTENCY_CONFLICT.value: (
            ADAPTIVE_COGNITIVE_TWIN_IDEMPOTENCY_CONFLICT
        ),
        AdaptiveStoreErrorCodeV1.SOURCE_CHANGED.value: ADAPTIVE_COGNITIVE_TWIN_SOURCE_CHANGED,
        AdaptiveStoreErrorCodeV1.CANDIDATE_NOT_REVIEWED.value: (
            ADAPTIVE_COGNITIVE_TWIN_CONCURRENCY_CONFLICT
        ),
        AdaptiveStoreErrorCodeV1.ACTIVE_PROFILE_CONFLICT.value: (
            ADAPTIVE_COGNITIVE_TWIN_CONCURRENCY_CONFLICT
        ),
        AdaptiveStoreErrorCodeV1.PROFILE_NOT_FOUND.value: (
            ADAPTIVE_COGNITIVE_TWIN_CONCURRENCY_CONFLICT
        ),
        AdaptiveStoreErrorCodeV1.STATE_CONFLICT.value: (
            ADAPTIVE_COGNITIVE_TWIN_CONCURRENCY_CONFLICT
        ),
    }
    return AdaptiveCognitiveTwinWebError(
        mapping.get(error.code, ADAPTIVE_COGNITIVE_TWIN_STORE_UNAVAILABLE)
    )


@dataclass(slots=True)
class ProductionAdaptiveCognitiveTwinWebService:
    """Rebuild exact current sources and delegate lifecycle to the Stage15 store."""

    env_file: Path | None = None
    vault_path_override: str | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def _config(self) -> AppConfig:
        try:
            return load_config(
                env_file=self.env_file,
                vault_path_override=self.vault_path_override,
            )
        except (ConfigurationError, OSError, RuntimeError, ValueError) as exc:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE) from exc

    def _reader(self, config: AppConfig) -> FileSystemVaultReader:
        return FileSystemVaultReader(config.vault_path)

    def _verified_report(self, reader: FileSystemVaultReader) -> ScanReport:
        try:
            report = build_report(reader.scan())
        except Exception as exc:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE) from exc
        if (
            type(report) is not ScanReport
            or report.manifest is None
            or report.error_count != 0
            or not report.content_scan_complete
        ):
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE)
        return report

    def _current_goal(self, selection: _Selection) -> object:
        try:
            goals = ProductionGrowthWebService(
                env_file=self.env_file,
                vault_path_override=self.vault_path_override,
                clock=self.clock,
            ).goals()
            matches = tuple(
                item
                for item in goals.goals
                if item.goal.source_note_uuid == selection.goal_source_uuid
            )
        except Exception as exc:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE) from exc
        if len(matches) == 0:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_INSUFFICIENT_EVIDENCE)
        if len(matches) != 1:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_NOT_COMPARABLE)
        match = matches[0]
        if (
            match.goal_identity_fingerprint != selection.goal_identity_fingerprint
            or growth_hash_json(match.goal.as_dict()) != selection.goal_identity_fingerprint
        ):
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_CHANGED)
        return match.goal

    def _stage9(self, selection: _Selection) -> tuple[ProspectiveCalibrationResultV1, str]:
        try:
            service = build_production_prospective_audit_service(
                env_file=self.env_file,
                vault_path_override=self.vault_path_override,
            )
            components = service._get_components()
            result = components.calibration.execute(
                ProspectiveCalibrationRequestV1(),
                now=selection.as_of,
            )
            generation = components.store.read_verified_snapshot().manifest.generation_id
            if type(generation) is not str:
                raise ValueError
            _uuid7_text(generation)
            if type(result) is not ProspectiveCalibrationResultV1:
                raise TypeError
            return result, generation
        except AdaptiveCognitiveTwinWebError:
            raise
        except Exception as exc:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE) from exc

    def _stage14_reassessment(
        self,
        report: ScanReport,
        reader: FileSystemVaultReader,
        selection: _Selection,
    ) -> tuple[
        PersonalExperimentEvaluationResultV1 | None, PersonalExperimentReassessmentRecordV1 | None
    ]:
        selector = selection.stage14_selector
        if selector is None:
            return None, None
        try:
            result = evaluate_personal_experiment(
                reader,
                PersonalExperimentEvaluationRequestV1(
                    selector.experiment_definition_id,
                    selector.experiment_definition_fingerprint,
                    selection.as_of,
                ),
            )
            matching = tuple(
                item
                for item in report.personal_experiment_reassessments
                if item.experiment_definition_id == selector.experiment_definition_id
                and item.experiment_definition_fingerprint
                == selector.experiment_definition_fingerprint
                and item.goal_source_uuid == selection.goal_source_uuid
                and item.goal_identity_fingerprint == selection.goal_identity_fingerprint
                and item.result_fingerprint == result.result_fingerprint
                and item.evaluation_as_of == result.as_of
                and item.evaluation_policy_fingerprint == PERSONAL_EXPERIMENT_POLICY_FINGERPRINT
            )
            chain = validate_personal_experiment_reassessment_chain(matching)
            if (
                chain.state is not PersonalExperimentChainStateV1.ONE_ACTIVE
                or len(chain.active_records) != 1
                or type(chain.active_records[0]) is not PersonalExperimentReassessmentRecordV1
            ):
                return result, None
            return result, chain.active_records[0]
        except (TypeError, ValueError, OverflowError) as exc:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE) from exc

    def _source(self, selection: _Selection) -> AdaptiveSourceSnapshotV1:
        config = self._config()
        reader = self._reader(config)
        report = self._verified_report(reader)
        goal = self._current_goal(selection)
        try:
            if type(goal) is not GrowthGoalIdentityV1:
                raise TypeError
            stage9_result, stage9_generation = self._stage9(selection)
            growth_result = ProductionGrowthWebService(
                env_file=self.env_file,
                vault_path_override=self.vault_path_override,
                clock=self.clock,
            ).execute(
                GrowthEngineRequestV1(
                    selection=GrowthGoalSelectionV1(
                        GrowthGoalSelectionModeV1.SELECTED_GOAL,
                        selection.goal_source_uuid,
                    )
                )
            )
            progress_result = ProductionGoalProgressWebService(
                env_file=self.env_file,
                vault_path_override=self.vault_path_override,
                clock=self.clock,
            ).read(GoalProgressRequestV1(selection.goal_source_uuid, selection.as_of))
            experiment_result, reassessment = self._stage14_reassessment(
                report,
                reader,
                selection,
            )
            return load_adaptive_source_snapshot(
                goal=goal,
                stage9_result=stage9_result,
                stage9_generation_id=stage9_generation,
                stage9_as_of=selection.as_of,
                stage10_source=growth_result,
                stage12_result=progress_result,
                stage14_selector=selection.stage14_selector,
                stage14_result=experiment_result,
                stage14_reassessment=reassessment,
                as_of=selection.as_of,
            )
        except AdaptiveCognitiveTwinInputError as exc:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_POLICY_MISMATCH) from exc
        except AdaptiveCognitiveTwinWebError:
            raise
        except Exception as exc:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE) from exc

    def _store(self, config: AppConfig, *, create: bool) -> AdaptiveCognitiveTwinStore | None:
        if config.env_file is None:
            if create:
                raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_STORE_UNAVAILABLE)
            return None
        root = derive_adaptive_cognitive_twin_store_root(config.env_file)
        if root is None:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_STORE_UNAVAILABLE)
        if not create and not root.exists():
            return None
        expected_owner_group = (
            PRODUCTION_PROSPECTIVE_AUDIT_OWNER_GROUP
            if root.parent == PRODUCTION_PROSPECTIVE_AUDIT_STORE_ROOT
            else None
        )
        try:
            return AdaptiveCognitiveTwinStore(
                root,
                vault_root=config.vault_path,
                expected_owner_group=expected_owner_group,
            )
        except AdaptiveCognitiveTwinStoreError as exc:
            raise _store_error(exc) from exc
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_STORE_UNAVAILABLE) from exc

    def _context(self, selection: _Selection, *, create_store: bool = False) -> _Context:
        config = self._config()
        store = self._store(config, create=create_store)
        snapshot = self._source(selection)
        try:
            active = (
                store.active_profile(
                    selection.goal_source_uuid,
                    selection.goal_identity_fingerprint,
                )
                if store is not None
                else None
            )
            projection = project_adaptive_cognitive_twin(
                snapshot,
                as_of=selection.as_of,
                active_profile=active,
            )
        except AdaptiveCognitiveTwinInputError as exc:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_POLICY_MISMATCH) from exc
        except AdaptiveCognitiveTwinWebError:
            raise
        except Exception as exc:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE) from exc
        return _Context(selection, snapshot, projection, store)

    @staticmethod
    def _ensure_candidate(
        payload: AdaptiveCognitiveTwinCandidateOperationPayload,
        projection: Stage15AdaptiveProjectionV1,
    ) -> Stage15CandidateV1:
        from second_brain.application.adaptive_cognitive_twin import validate_adaptive_hash

        try:
            validate_adaptive_hash(payload.candidate_fingerprint)
            validate_adaptive_hash(payload.source_snapshot_fingerprint)
        except AdaptiveCognitiveTwinInputError as exc:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST) from exc
        if payload.source_snapshot_fingerprint != projection.source_snapshot_fingerprint:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_STALE_CANDIDATE)
        if payload.candidate_fingerprint != projection.candidate.candidate_fingerprint:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_STALE_CANDIDATE)
        candidate = projection.candidate
        if candidate.candidate_status.value == "insufficient":
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_INSUFFICIENT_EVIDENCE)
        if candidate.candidate_status.value == "not_comparable":
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_NOT_COMPARABLE)
        if candidate.candidate_status.value == "source_changed":
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_CHANGED)
        if candidate.candidate_status.value == "policy_mismatch":
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_POLICY_MISMATCH)
        if candidate.candidate_status.value != "candidate" or candidate.proposed_profile is None:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_INSUFFICIENT_EVIDENCE)
        return candidate

    @staticmethod
    def _mutation_body(
        status: str,
        event: object,
        projection: Stage15AdaptiveProjectionV1,
    ) -> dict[str, object]:
        event_dict = getattr(event, "record", None)
        as_dict = getattr(event_dict, "as_dict", None)
        return {
            "web_contract": "adaptive_cognitive_twin_web_v1",
            "status": status,
            "event": as_dict() if callable(as_dict) else None,
            "projection": projection.as_dict(),
        }

    def _after_mutation(
        self,
        selection: _Selection,
        status: str,
        event: object,
    ) -> dict[str, object]:
        context = self._context(selection)
        return self._mutation_body(status, event, context.projection)

    def state(self, payload: AdaptiveCognitiveTwinStatePayload) -> Mapping[str, object]:
        selection = _selection_from_state_payload(payload)
        config = self._config()
        reader = self._reader(config)
        goals = ProductionGrowthWebService(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
            clock=self.clock,
        ).goals()
        options: list[dict[str, object]] = []
        projection: dict[str, object] | None = None
        selected_goal: dict[str, object] | None = None
        profile_history: list[dict[str, object]] = []
        reviewed_candidate_fingerprints: list[str] = []
        rejected_candidate_fingerprints: list[str] = []
        as_of = selection.as_of if selection is not None else _utc_now(self.clock)
        if selection is not None:
            context = self._context(selection)
            selected_goal_item = next(
                (
                    item
                    for item in goals.goals
                    if item.goal.source_note_uuid == selection.goal_source_uuid
                ),
                None,
            )
            if selected_goal_item is None:
                raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_CHANGED)
            selected_goal = {
                "source_note_uuid": str(selected_goal_item.goal.source_note_uuid),
                "goal_identity_fingerprint": selected_goal_item.goal_identity_fingerprint,
                "goal_text": selected_goal_item.goal_text,
                "domain": selected_goal_item.goal.domain,
            }
            if context.store is not None:
                try:
                    store_state = context.store.read_state()
                    profile_history = [
                        profile.as_dict()
                        for profile in store_state.profile_history
                        if profile.goal_source_uuid == selection.goal_source_uuid
                        and profile.goal_identity_fingerprint == selection.goal_identity_fingerprint
                    ]
                    reviewed_candidate_fingerprints = list(
                        store_state.reviewed_candidate_fingerprints
                    )
                    rejected_candidate_fingerprints = list(
                        store_state.rejected_candidate_fingerprints
                    )
                except AdaptiveCognitiveTwinStoreError as exc:
                    raise _store_error(exc) from exc
            report = self._verified_report(reader)
            options = [
                {
                    "experiment_definition_id": str(item.id),
                    "experiment_definition_fingerprint": item.experiment_definition_fingerprint,
                }
                for item in report.personal_experiment_definitions
                if item.goal_source_uuid == selection.goal_source_uuid
                and item.goal_identity_fingerprint == selection.goal_identity_fingerprint
            ]
            projection = context.projection.as_dict()
        return {
            "web_contract": "adaptive_cognitive_twin_web_v1",
            "as_of": as_of.isoformat().replace("+00:00", "Z"),
            "goals": [
                {
                    "source_note_uuid": str(item.goal.source_note_uuid),
                    "goal_identity_fingerprint": item.goal_identity_fingerprint,
                    "goal_text": item.goal_text,
                    "domain": item.goal.domain,
                }
                for item in goals.goals
            ],
            "selected_goal": selected_goal,
            "stage14_experiment_selectors": options,
            "projection": projection,
            "profile_history": profile_history,
            "reviewed_candidate_fingerprints": reviewed_candidate_fingerprints,
            "rejected_candidate_fingerprints": rejected_candidate_fingerprints,
            "non_causal_phrase": ADAPTIVE_NON_CAUSAL_PHRASE,
        }

    def candidate(self, payload: AdaptiveCognitiveTwinCandidatePayload) -> Mapping[str, object]:
        selection = _selection_from_payload(payload)
        context = self._context(selection)
        return {
            "web_contract": "adaptive_cognitive_twin_web_v1",
            "projection": context.projection.as_dict(),
        }

    def review(self, payload: AdaptiveCognitiveTwinReviewPayload) -> Mapping[str, object]:
        if not payload.confirmed:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST)
        selection = _selection_from_payload(payload)
        context = self._context(selection, create_store=True)
        candidate = self._ensure_candidate(payload, context.projection)
        assert context.store is not None
        try:
            event = context.store.review_candidate(
                candidate, operation_id=_operation_id(payload.operation_id)
            )
        except AdaptiveCognitiveTwinStoreError as exc:
            raise _store_error(exc) from exc
        return self._after_mutation(selection, "reviewed", event)

    def activate(self, payload: AdaptiveCognitiveTwinActivatePayload) -> Mapping[str, object]:
        if not payload.confirmed:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST)
        selection = _selection_from_payload(payload)
        context = self._context(selection, create_store=True)
        candidate = self._ensure_candidate(payload, context.projection)
        assert context.store is not None
        try:
            event = context.store.activate_candidate(
                candidate,
                context.snapshot,
                operation_id=_operation_id(payload.operation_id),
            )
        except AdaptiveCognitiveTwinStoreError as exc:
            raise _store_error(exc) from exc
        return self._after_mutation(selection, "activated", event)

    def reject(self, payload: AdaptiveCognitiveTwinRejectPayload) -> Mapping[str, object]:
        if not payload.confirmed:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST)
        selection = _selection_from_payload(payload)
        context = self._context(selection, create_store=True)
        candidate = self._ensure_candidate(payload, context.projection)
        assert context.store is not None
        try:
            event = context.store.reject_candidate(
                candidate, operation_id=_operation_id(payload.operation_id)
            )
        except AdaptiveCognitiveTwinStoreError as exc:
            raise _store_error(exc) from exc
        return self._after_mutation(selection, "rejected", event)

    def supersede(self, payload: AdaptiveCognitiveTwinSupersedePayload) -> Mapping[str, object]:
        if not payload.confirmed:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST)
        selection = _selection_from_payload(payload)
        context = self._context(selection, create_store=True)
        candidate = self._ensure_candidate(payload, context.projection)
        try:
            if (
                candidate.prior_profile_id != _uuid7_text(payload.prior_profile_id)
                or candidate.prior_profile_fingerprint != payload.prior_profile_fingerprint
            ):
                raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_CONCURRENCY_CONFLICT)
        except TypeError, ValueError, OverflowError:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST) from None
        assert context.store is not None
        try:
            event = context.store.supersede_candidate(
                candidate,
                context.snapshot,
                operation_id=_operation_id(payload.operation_id),
            )
        except AdaptiveCognitiveTwinStoreError as exc:
            raise _store_error(exc) from exc
        return self._after_mutation(selection, "superseded", event)

    def revert(self, payload: AdaptiveCognitiveTwinRevertPayload) -> Mapping[str, object]:
        if not payload.confirmed:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST)
        selection = _selection_from_payload(payload)
        context = self._context(selection, create_store=True)
        assert context.store is not None
        try:
            from second_brain.application.adaptive_cognitive_twin import validate_adaptive_hash

            validate_adaptive_hash(payload.target_profile_fingerprint)
            event = context.store.revert_profile(
                goal_source_uuid=selection.goal_source_uuid,
                goal_identity_fingerprint=selection.goal_identity_fingerprint,
                target_profile_id=_uuid7_text(payload.target_profile_id),
                target_profile_fingerprint=payload.target_profile_fingerprint,
                operation_id=_operation_id(payload.operation_id),
            )
        except AdaptiveCognitiveTwinWebError:
            raise
        except AdaptiveCognitiveTwinStoreError as exc:
            raise _store_error(exc) from exc
        except AdaptiveCognitiveTwinInputError, TypeError, ValueError, OverflowError:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST) from None
        return self._after_mutation(selection, "reverted", event)

    def evaluate(self, payload: AdaptiveCognitiveTwinEvaluatePayload) -> Mapping[str, object]:
        if not payload.confirmed:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST)
        selection = _selection_from_payload(payload)
        context = self._context(selection)
        if context.store is None:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_CONCURRENCY_CONFLICT)
        try:
            active = context.store.active_profile(
                selection.goal_source_uuid,
                selection.goal_identity_fingerprint,
            )
            if active is None:
                raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_CONCURRENCY_CONFLICT)
            if (
                active.profile_id != _uuid7_text(payload.active_profile_id)
                or active.profile_fingerprint != payload.active_profile_fingerprint
                or active.source_snapshot_fingerprint
                != payload.activation_source_snapshot_fingerprint
            ):
                raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_CHANGED)
            from second_brain.application.adaptive_cognitive_twin import validate_adaptive_hash

            validate_adaptive_hash(payload.active_profile_fingerprint)
            validate_adaptive_hash(payload.activation_source_snapshot_fingerprint)
            plan = Stage15EvaluationPlanV1(
                later_source_required=True,
                explicit_as_of_required=True,
                baseline_source_snapshot_fingerprint=active.source_snapshot_fingerprint,
                measures=(cast(Stage15MeasureV1, active.evaluation_measure),),
                evaluation_policy_id=ADAPTIVE_EVALUATION_POLICY_ID,
                evaluation_policy_fingerprint=ADAPTIVE_POLICY_FINGERPRINT,
                non_causal_wording_id="observed-change-not-causation-v1",
            )
            result = evaluate_adaptive_profile(
                active,
                later_snapshot=context.snapshot,
                as_of=selection.as_of,
                activation_source_snapshot_fingerprint=active.source_snapshot_fingerprint,
                evaluation_plan=plan,
            )
            event = context.store.record_evaluation(
                active,
                result.evaluation_fingerprint,
                operation_id=_operation_id(payload.operation_id),
            )
        except AdaptiveCognitiveTwinWebError:
            raise
        except AdaptiveCognitiveTwinStoreError as exc:
            raise _store_error(exc) from exc
        except (AdaptiveCognitiveTwinInputError, TypeError, ValueError, OverflowError) as exc:
            raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_CHANGED) from exc
        body = self._after_mutation(selection, "evaluated", event)
        body["evaluation"] = result.as_dict()
        return body


def build_production_adaptive_cognitive_twin_web_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> ProductionAdaptiveCognitiveTwinWebService:
    return ProductionAdaptiveCognitiveTwinWebService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


def _route_error(error: AdaptiveCognitiveTwinWebError) -> JSONResponse:
    status_by_code = {
        ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST: 400,
        ADAPTIVE_COGNITIVE_TWIN_INSUFFICIENT_EVIDENCE: 409,
        ADAPTIVE_COGNITIVE_TWIN_NOT_COMPARABLE: 409,
        ADAPTIVE_COGNITIVE_TWIN_SOURCE_CHANGED: 409,
        ADAPTIVE_COGNITIVE_TWIN_POLICY_MISMATCH: 409,
        ADAPTIVE_COGNITIVE_TWIN_STALE_CANDIDATE: 409,
        ADAPTIVE_COGNITIVE_TWIN_IDEMPOTENCY_CONFLICT: 409,
        ADAPTIVE_COGNITIVE_TWIN_CONCURRENCY_CONFLICT: 409,
        ADAPTIVE_COGNITIVE_TWIN_STORE_UNAVAILABLE: 503,
        ADAPTIVE_COGNITIVE_TWIN_STORE_CORRUPT: 503,
        ADAPTIVE_COGNITIVE_TWIN_RESULT_TOO_LARGE: 413,
        ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE: 503,
    }
    return _error_response(error.code, status_code=status_by_code.get(error.code, 503))


def _service_body(result: object) -> Mapping[str, object]:
    if not isinstance(result, Mapping):
        raise AdaptiveCognitiveTwinWebError(ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE)
    return result


def install_adaptive_cognitive_twin_routes(
    app: FastAPI,
    *,
    service: AdaptiveCognitiveTwinWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install all eight private additive Stage15.4 route families."""

    actual_service = service or build_production_adaptive_cognitive_twin_web_service(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    app.add_middleware(AdaptiveCognitiveTwinRequestBoundaryMiddleware)

    @app.post(ADAPTIVE_COGNITIVE_TWIN_STATE_PATH, include_in_schema=False)
    async def adaptive_cognitive_twin_state_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                AdaptiveCognitiveTwinStatePayload,
                await _read_payload(request, AdaptiveCognitiveTwinStatePayload),
            )
            body = _service_body(await run_in_threadpool(actual_service.state, payload))
            return _json_response(body, max_bytes=MAX_ADAPTIVE_COGNITIVE_TWIN_STATE_RESPONSE_BYTES)
        except AdaptiveCognitiveTwinWebError as error:
            return _route_error(error)
        except (
            ValidationError,
            json.JSONDecodeError,
            UnicodeError,
            TypeError,
            ValueError,
            RecursionError,
        ):
            return _invalid_request()
        except Exception:
            return _error_response(ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE, status_code=503)

    @app.post(ADAPTIVE_COGNITIVE_TWIN_CANDIDATE_PATH, include_in_schema=False)
    async def adaptive_cognitive_twin_candidate_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                AdaptiveCognitiveTwinCandidatePayload,
                await _read_payload(request, AdaptiveCognitiveTwinCandidatePayload),
            )
            body = _service_body(await run_in_threadpool(actual_service.candidate, payload))
            return _json_response(body, max_bytes=MAX_ADAPTIVE_PROJECTION_BYTES)
        except AdaptiveCognitiveTwinWebError as error:
            return _route_error(error)
        except (
            ValidationError,
            json.JSONDecodeError,
            UnicodeError,
            TypeError,
            ValueError,
            RecursionError,
        ):
            return _invalid_request()
        except Exception:
            return _error_response(ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE, status_code=503)

    route_payloads: tuple[tuple[str, type[BaseModel], str], ...] = (
        (ADAPTIVE_COGNITIVE_TWIN_REVIEW_PATH, AdaptiveCognitiveTwinReviewPayload, "review"),
        (ADAPTIVE_COGNITIVE_TWIN_ACTIVATE_PATH, AdaptiveCognitiveTwinActivatePayload, "activate"),
        (ADAPTIVE_COGNITIVE_TWIN_REJECT_PATH, AdaptiveCognitiveTwinRejectPayload, "reject"),
        (ADAPTIVE_COGNITIVE_TWIN_EVALUATE_PATH, AdaptiveCognitiveTwinEvaluatePayload, "evaluate"),
        (
            ADAPTIVE_COGNITIVE_TWIN_SUPERSEDE_PATH,
            AdaptiveCognitiveTwinSupersedePayload,
            "supersede",
        ),
        (ADAPTIVE_COGNITIVE_TWIN_REVERT_PATH, AdaptiveCognitiveTwinRevertPayload, "revert"),
    )
    for path, payload_type, method_name in route_payloads:

        async def endpoint(
            request: Request,
            *,
            _payload_type: type[BaseModel] = payload_type,
            _method_name: str = method_name,
        ) -> Response:
            try:
                payload = await _read_payload(request, _payload_type)
                method = getattr(actual_service, _method_name)
                body = _service_body(await run_in_threadpool(method, payload))
                return _json_response(
                    body,
                    max_bytes=MAX_ADAPTIVE_COGNITIVE_TWIN_MUTATION_RESPONSE_BYTES,
                )
            except AdaptiveCognitiveTwinWebError as error:
                return _route_error(error)
            except (
                ValidationError,
                json.JSONDecodeError,
                UnicodeError,
                TypeError,
                ValueError,
                RecursionError,
            ):
                return _invalid_request()
            except Exception:
                return _error_response(ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE, status_code=503)

        app.post(path, include_in_schema=False)(endpoint)


__all__ = [
    "ADAPTIVE_COGNITIVE_TWIN_ACTIVATE_PATH",
    "ADAPTIVE_COGNITIVE_TWIN_CANDIDATE_PATH",
    "ADAPTIVE_COGNITIVE_TWIN_EVALUATE_PATH",
    "ADAPTIVE_COGNITIVE_TWIN_REJECT_PATH",
    "ADAPTIVE_COGNITIVE_TWIN_REQUEST_HEADER_NAME",
    "ADAPTIVE_COGNITIVE_TWIN_REQUEST_HEADER_VALUE",
    "ADAPTIVE_COGNITIVE_TWIN_RESULT_TOO_LARGE",
    "ADAPTIVE_COGNITIVE_TWIN_REVERT_PATH",
    "ADAPTIVE_COGNITIVE_TWIN_REVIEW_PATH",
    "ADAPTIVE_COGNITIVE_TWIN_STATE_PATH",
    "ADAPTIVE_COGNITIVE_TWIN_SUPERSEDE_PATH",
    "MAX_ADAPTIVE_COGNITIVE_TWIN_MUTATION_RESPONSE_BYTES",
    "MAX_ADAPTIVE_COGNITIVE_TWIN_STATE_RESPONSE_BYTES",
    "MAX_RAW_ADAPTIVE_COGNITIVE_TWIN_BODY_BYTES",
    "AdaptiveCognitiveTwinActivatePayload",
    "AdaptiveCognitiveTwinCandidateOperationPayload",
    "AdaptiveCognitiveTwinCandidatePayload",
    "AdaptiveCognitiveTwinEvaluatePayload",
    "AdaptiveCognitiveTwinRejectPayload",
    "AdaptiveCognitiveTwinRequestBoundaryMiddleware",
    "AdaptiveCognitiveTwinRevertPayload",
    "AdaptiveCognitiveTwinReviewPayload",
    "AdaptiveCognitiveTwinStatePayload",
    "AdaptiveCognitiveTwinSupersedePayload",
    "AdaptiveCognitiveTwinWebError",
    "AdaptiveCognitiveTwinWebService",
    "ProductionAdaptiveCognitiveTwinWebService",
    "build_production_adaptive_cognitive_twin_web_service",
    "install_adaptive_cognitive_twin_routes",
]
