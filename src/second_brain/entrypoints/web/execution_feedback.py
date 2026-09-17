"""Owner-only Web/API transport for Stage 18 execution feedback.

The module is deliberately provider-free.  It is a strict transport boundary
around the Stage 17 accepted-plan store, the Stage 18 operational event store,
and deterministic projections.  Browser input carries only exact immutable
identities; accepted item text and provenance are rebuilt from the server-side
snapshot before an event can be appended.
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
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.application.execution_feedback import (
    EXECUTION_FEEDBACK_CONTRACT_VERSION,
    ExecutionDeviationCodeV1,
    ExecutionEffortPrecisionV1,
    ExecutionEventTypeV1,
    ExecutionEventV1,
    ExecutionFeedbackCorrectionError,
    ExecutionFeedbackError,
    ExecutionFeedbackItemMismatchError,
    ExecutionFeedbackLifecycleError,
    ExecutionFeedbackNonExecutableError,
    ExecutionFeedbackSnapshotMismatchError,
    ExecutionFeedbackStaleError,
    ExecutionReasonCodeV1,
    ExecutionResultDispositionV1,
    ExecutionSourceStatusV1,
    accepted_item_fingerprint,
    create_execution_event,
    execution_event_fingerprint,
    replay_execution_events,
)
from second_brain.application.execution_feedback_projection import (
    ExecutionFeedbackProjectionError,
    ExecutionFeedbackReportV1,
    ExecutionItemStateV1,
    build_execution_calibration,
    build_execution_feedback_report,
)
from second_brain.application.execution_feedback_store import (
    ExecutionFeedbackOperationalStore,
    ExecutionFeedbackStoreCapacityError,
    ExecutionFeedbackStoreCorruptError,
    ExecutionFeedbackStoreError,
    ExecutionFeedbackStoreIdempotencyConflictError,
    ExecutionFeedbackStoreInvalidRequestError,
    ExecutionFeedbackStoreUnavailableError,
    derive_execution_feedback_store_root,
)
from second_brain.application.personal_planning import (
    PersonalPlanningError,
    PlanningItemKindV1,
)
from second_brain.application.personal_planning_store import (
    PersonalPlanningOperationalStore,
    PersonalPlanningStoreError,
    PlanningPlanV1,
    derive_personal_planning_store_root,
)
from second_brain.domain.models import parse_rfc3339, parse_uuid7
from second_brain.entrypoints.web.auth import (
    request_host_is_trusted,
    same_origin_is_trusted,
    trusted_authorities_from_scope,
)
from second_brain.entrypoints.web.personal_planning import (
    PersonalPlanningSourceUnavailableError,
    PersonalPlanningWebService,
    ProductionPersonalPlanningWebService,
    build_production_personal_planning_web_service,
)

EXECUTION_FEEDBACK_STATE_PATH: Final[str] = "/api/execution-feedback/state"
EXECUTION_FEEDBACK_EVENT_PATH: Final[str] = "/api/execution-feedback/event"
EXECUTION_FEEDBACK_FEEDBACK_PATH: Final[str] = "/api/execution-feedback/feedback"
EXECUTION_FEEDBACK_CALIBRATION_PATH: Final[str] = "/api/execution-feedback/calibration"
EXECUTION_FEEDBACK_CORRECTION_PATH: Final[str] = "/api/execution-feedback/correction"
EXECUTION_FEEDBACK_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
EXECUTION_FEEDBACK_REQUEST_HEADER_VALUE: Final[str] = EXECUTION_FEEDBACK_CONTRACT_VERSION

MAX_RAW_EXECUTION_FEEDBACK_BODY_BYTES: Final[int] = 256 * 1024
MAX_EXECUTION_FEEDBACK_STATE_RESPONSE_BYTES: Final[int] = 512 * 1024
MAX_EXECUTION_FEEDBACK_MUTATION_RESPONSE_BYTES: Final[int] = 512 * 1024
MAX_EXECUTION_FEEDBACK_FEEDBACK_RESPONSE_BYTES: Final[int] = 512 * 1024
MAX_EXECUTION_FEEDBACK_CALIBRATION_RESPONSE_BYTES: Final[int] = 1024 * 1024

_EXECUTION_FEEDBACK_PATHS: Final[frozenset[str]] = frozenset(
    {
        EXECUTION_FEEDBACK_STATE_PATH,
        EXECUTION_FEEDBACK_EVENT_PATH,
        EXECUTION_FEEDBACK_FEEDBACK_PATH,
        EXECUTION_FEEDBACK_CALIBRATION_PATH,
        EXECUTION_FEEDBACK_CORRECTION_PATH,
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
    "Cache-Control": "no-store, private",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; connect-src 'self'; font-src 'self'; "
        "form-action 'none'; frame-ancestors 'none'; img-src 'self'; object-src 'none'; "
        "script-src 'self'; style-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
_EXECUTABLE_KINDS: Final[frozenset[PlanningItemKindV1]] = frozenset(
    {PlanningItemKindV1.COMMITMENT, PlanningItemKindV1.NEXT_ACTION}
)


class ExecutionFeedbackWebError(RuntimeError):
    """Safe application-facing transport error."""


class ExecutionFeedbackSourceUnavailableError(ExecutionFeedbackWebError):
    """The accepted plan or exact current provenance cannot be read safely."""


class ExecutionFeedbackInvalidRequestError(ExecutionFeedbackWebError):
    """The owner request failed the typed application boundary."""


class ExecutionFeedbackStalePlanError(ExecutionFeedbackWebError):
    """A new event was based on stale or superseded source data."""


class ExecutionFeedbackStateConflictError(ExecutionFeedbackWebError):
    """The requested event cannot be applied to the current lifecycle."""


class ExecutionFeedbackEmptyPayload(BaseModel):
    """The state operation intentionally accepts no caller-selected state."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ExecutionFeedbackPlanSelectionPayload(BaseModel):
    """One exact plan identity used by feedback and calibration."""

    model_config = ConfigDict(extra="forbid", strict=True)

    planning_snapshot_id: StrictStr = Field(min_length=1, max_length=64)
    planning_snapshot_fingerprint: StrictStr = Field(min_length=64, max_length=64)


class ExecutionFeedbackEventPayload(BaseModel):
    """Strict event input; item content is never accepted from the browser."""

    model_config = ConfigDict(extra="forbid", strict=True)

    planning_snapshot_id: StrictStr = Field(min_length=1, max_length=64)
    planning_snapshot_fingerprint: StrictStr = Field(min_length=64, max_length=64)
    item_id: StrictStr = Field(min_length=1, max_length=64)
    accepted_item_fingerprint: StrictStr = Field(min_length=64, max_length=64)
    operation_id: StrictStr = Field(min_length=1, max_length=256)
    event_type: StrictStr = Field(min_length=1, max_length=16)
    occurred_at: StrictStr = Field(min_length=1, max_length=64)
    actual_effort_minutes: StrictInt | None = Field(default=None, ge=0, le=1440)
    effort_precision: StrictStr = Field(default="unknown", max_length=16)
    actual_result_note: StrictStr = Field(default="", max_length=2048)
    reason_codes: list[StrictStr] = Field(default_factory=list, max_length=3)
    deviation_codes: list[StrictStr] = Field(default_factory=list, max_length=3)
    result_disposition: StrictStr | None = Field(default=None, max_length=32)


class ExecutionFeedbackFeedbackPayload(BaseModel):
    """Exact plan selection for one read-only report."""

    model_config = ConfigDict(extra="forbid", strict=True)

    planning_snapshot_id: StrictStr = Field(min_length=1, max_length=64)
    planning_snapshot_fingerprint: StrictStr = Field(min_length=64, max_length=64)


class ExecutionFeedbackCalibrationPayload(BaseModel):
    """Explicit bounded historical selection; there is no implicit latest set."""

    model_config = ConfigDict(extra="forbid", strict=True)

    snapshots: list[ExecutionFeedbackPlanSelectionPayload] = Field(
        min_length=1,
        max_length=32,
    )


class ExecutionFeedbackCorrectionPayload(BaseModel):
    """Strict append-only void/correction request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    planning_snapshot_id: StrictStr = Field(min_length=1, max_length=64)
    planning_snapshot_fingerprint: StrictStr = Field(min_length=64, max_length=64)
    item_id: StrictStr = Field(min_length=1, max_length=64)
    accepted_item_fingerprint: StrictStr = Field(min_length=64, max_length=64)
    operation_id: StrictStr = Field(min_length=1, max_length=256)
    occurred_at: StrictStr = Field(min_length=1, max_length=64)
    void_target_event_id: StrictStr = Field(min_length=1, max_length=64)
    void_target_event_fingerprint: StrictStr = Field(min_length=64, max_length=64)
    correction_reason: StrictStr = Field(min_length=1, max_length=2048)


class ExecutionFeedbackWebService(Protocol):
    """Owner-only service seam used by the transport and focused web tests."""

    def state(self) -> dict[str, object]: ...

    def event(self, payload: ExecutionFeedbackEventPayload) -> dict[str, object]: ...

    def feedback(self, payload: ExecutionFeedbackFeedbackPayload) -> dict[str, object]: ...

    def calibration(self, payload: ExecutionFeedbackCalibrationPayload) -> dict[str, object]: ...

    def correction(self, payload: ExecutionFeedbackCorrectionPayload) -> dict[str, object]: ...


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


class ExecutionFeedbackRequestBoundaryMiddleware:
    """Fail closed on authority, origin, request purpose, content and body size."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _EXECUTION_FEEDBACK_PATHS:
            await self.app(scope, receive, send)
            return
        if scope.get("method") != "POST":
            await _send(
                _error_response(
                    "EXECUTION_FEEDBACK_METHOD_NOT_ALLOWED",
                    "Метод не поддерживается.",
                    405,
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
            or purpose != EXECUTION_FEEDBACK_REQUEST_HEADER_VALUE
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
            if declared > MAX_RAW_EXECUTION_FEEDBACK_BODY_BYTES:
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
            if len(body) > MAX_RAW_EXECUTION_FEEDBACK_BODY_BYTES:
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
    if not raw or len(raw) > MAX_RAW_EXECUTION_FEEDBACK_BODY_BYTES:
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
        raw = json.dumps(
            body,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except TypeError, ValueError, UnicodeError:
        return _error_response(
            "EXECUTION_FEEDBACK_UNAVAILABLE",
            "Выполнение и обратная связь сейчас недоступны.",
            503,
        )
    if len(raw) > max_bytes:
        return _error_response(
            "EXECUTION_FEEDBACK_RESULT_TOO_LARGE",
            "Результат слишком велик.",
            503,
        )
    return Response(raw, media_type="application/json", headers=_API_HEADERS)


def _error_response(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status_code,
        headers=_API_HEADERS,
    )


def _invalid_request() -> JSONResponse:
    return _error_response(
        "EXECUTION_FEEDBACK_INVALID_REQUEST",
        "Запрос выполнения и обратной связи некорректен.",
        400,
    )


def _too_large() -> JSONResponse:
    return _error_response(
        "EXECUTION_FEEDBACK_REQUEST_TOO_LARGE",
        "Запрос слишком велик.",
        413,
    )


def _exception_response(error: BaseException) -> JSONResponse:
    if isinstance(
        error, ExecutionFeedbackInvalidRequestError | ExecutionFeedbackStoreInvalidRequestError
    ):
        return _invalid_request()
    if isinstance(
        error,
        ExecutionFeedbackStalePlanError
        | ExecutionFeedbackStaleError
        | ExecutionFeedbackSnapshotMismatchError
        | ExecutionFeedbackItemMismatchError
        | ExecutionFeedbackNonExecutableError,
    ):
        return _error_response(
            "EXECUTION_FEEDBACK_STALE_PLAN",
            "План или выбранный элемент устарел; обновите поверхность выполнения.",
            409,
        )
    if isinstance(error, ExecutionFeedbackLifecycleError | ExecutionFeedbackStateConflictError):
        return _error_response(
            "EXECUTION_FEEDBACK_STATE_CONFLICT",
            "Это событие нельзя применить к текущему состоянию элемента.",
            409,
        )
    if isinstance(error, ExecutionFeedbackCorrectionError):
        return _error_response(
            "EXECUTION_FEEDBACK_CORRECTION_INVALID",
            "Коррекция не прошла проверку точной истории.",
            409,
        )
    if isinstance(error, ExecutionFeedbackStoreIdempotencyConflictError):
        return _error_response(
            "EXECUTION_FEEDBACK_IDEMPOTENCY_CONFLICT",
            "Операция уже использована с другим содержимым.",
            409,
        )
    if isinstance(error, ExecutionFeedbackStoreCapacityError):
        return _error_response(
            "EXECUTION_FEEDBACK_STORE_CAPACITY",
            "Операционное хранилище достигло установленного предела.",
            503,
        )
    if isinstance(
        error, ExecutionFeedbackSourceUnavailableError | PersonalPlanningSourceUnavailableError
    ):
        return _error_response(
            "EXECUTION_FEEDBACK_SOURCE_UNAVAILABLE",
            "Точный источник личного плана сейчас недоступен.",
            503,
        )
    if isinstance(
        error,
        ExecutionFeedbackStoreCorruptError
        | ExecutionFeedbackStoreUnavailableError
        | ExecutionFeedbackStoreError
        | PersonalPlanningStoreError
        | PersonalPlanningError
        | ExecutionFeedbackProjectionError,
    ):
        return _error_response(
            "EXECUTION_FEEDBACK_STORE_UNAVAILABLE",
            "Операционное хранилище выполнения сейчас недоступно.",
            503,
        )
    return _error_response(
        "EXECUTION_FEEDBACK_UNAVAILABLE",
        "Выполнение и обратная связь сейчас недоступны.",
        503,
    )


def _as_datetime(value: datetime | str) -> datetime:
    try:
        parsed = parse_rfc3339(value)
    except TypeError, ValueError, OverflowError:
        raise ExecutionFeedbackInvalidRequestError() from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExecutionFeedbackInvalidRequestError()
    return parsed.astimezone(UTC)


def _as_source_status(value: ExecutionSourceStatusV1 | str) -> ExecutionSourceStatusV1:
    if type(value) is ExecutionSourceStatusV1:
        return value
    if type(value) is str:
        try:
            return ExecutionSourceStatusV1(value)
        except ValueError:
            pass
    raise ExecutionFeedbackSourceUnavailableError()


def _plan_summary(
    plan: PlanningPlanV1, *, source_status: ExecutionSourceStatusV1
) -> dict[str, object]:
    executable_count = sum(
        item.kind in _EXECUTABLE_KINDS
        for item in plan.items
        if item.item_id in plan.selected_item_ids
    )
    return {
        "planning_snapshot_id": str(plan.plan_id),
        "planning_snapshot_fingerprint": plan.plan_fingerprint,
        "planning_plan_revision": plan.revision,
        "as_of": plan.as_of.isoformat().replace("+00:00", "Z"),
        "start_local": plan.start_local,
        "end_local": plan.end_local,
        "planning_timezone": plan.timezone,
        "selected_item_count": len(plan.selected_item_ids),
        "executable_item_count": executable_count,
        "source_status": source_status.value,
    }


def _event_view(event: ExecutionEventV1) -> dict[str, object]:
    if type(event) is not ExecutionEventV1:
        raise ExecutionFeedbackInvalidRequestError()
    # Keep the response useful for an owner while excluding store sequence,
    # chain digests, operation keys, and filesystem details.
    return {
        "event_id": str(event.event_id),
        "event_fingerprint": execution_event_fingerprint(event),
        "event_type": cast(ExecutionEventTypeV1, event.event_type).value,
        "occurred_at": cast(datetime, event.occurred_at).isoformat().replace("+00:00", "Z"),
        "planning_snapshot_id": str(event.planning_snapshot_id),
        "planning_snapshot_fingerprint": event.planning_snapshot_fingerprint,
        "item_id": event.item_id,
        "accepted_item_fingerprint": event.accepted_item_fingerprint,
        "actual_effort_minutes": event.actual_effort_minutes,
        "effort_precision": cast(ExecutionEffortPrecisionV1, event.effort_precision).value,
        "actual_result_note": event.actual_result_note,
        "reason_codes": [cast(ExecutionReasonCodeV1, code).value for code in event.reason_codes],
        "deviation_codes": [
            cast(ExecutionDeviationCodeV1, code).value for code in event.deviation_codes
        ],
        "result_disposition": (
            None
            if event.result_disposition is None
            else cast(ExecutionResultDispositionV1, event.result_disposition).value
        ),
        "void_target_event_id": (
            None if event.void_target_event_id is None else str(event.void_target_event_id)
        ),
        "void_target_event_fingerprint": event.void_target_event_fingerprint,
        "correction_reason": event.correction_reason,
    }


@dataclass(slots=True)
class ProductionExecutionFeedbackWebService:
    """Compose exact Stage 17 plans with the Stage 18 operational store."""

    planning_service: PersonalPlanningWebService
    execution_store: ExecutionFeedbackOperationalStore | None = None
    planning_store: PersonalPlanningOperationalStore | None = None
    env_file: Path | None = None
    vault_path_override: str | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def _now(self) -> datetime:
        return _as_datetime(self.clock())

    def _current_plan(self) -> PlanningPlanV1 | None:
        try:
            return self.planning_service.current_plan()
        except PersonalPlanningSourceUnavailableError:
            raise
        except Exception as exc:
            raise ExecutionFeedbackSourceUnavailableError() from exc

    def _planning_store(self) -> PersonalPlanningOperationalStore:
        if self.planning_store is not None:
            return self.planning_store
        if isinstance(self.planning_service, ProductionPersonalPlanningWebService):
            injected = self.planning_service.planning_store
            if injected is not None:
                return injected
        root = derive_personal_planning_store_root(self.env_file)
        if root is None:
            raise ExecutionFeedbackSourceUnavailableError()
        try:
            store = PersonalPlanningOperationalStore(root)
        except PersonalPlanningStoreError as exc:
            raise ExecutionFeedbackSourceUnavailableError() from exc
        return store

    def historical_plans(self) -> tuple[PlanningPlanV1, ...]:
        candidate = getattr(self.planning_service, "historical_plans", None)
        if callable(candidate):
            try:
                result = candidate()
            except Exception as exc:
                raise ExecutionFeedbackSourceUnavailableError() from exc
            if type(result) is not tuple or any(
                type(plan) is not PlanningPlanV1 for plan in result
            ):
                raise ExecutionFeedbackSourceUnavailableError()
            return result
        try:
            return self._planning_store().read_state().plan_history
        except PersonalPlanningStoreError as exc:
            raise ExecutionFeedbackSourceUnavailableError() from exc

    def _execution_store_root(self) -> Path:
        root = derive_execution_feedback_store_root(self.env_file)
        if root is None:
            raise ExecutionFeedbackSourceUnavailableError()
        return root

    def _store_for_mutation(self) -> ExecutionFeedbackOperationalStore:
        if self.execution_store is not None:
            return self.execution_store
        root = self._execution_store_root()
        try:
            self.execution_store = ExecutionFeedbackOperationalStore(root)
        except ExecutionFeedbackStoreError:
            raise
        except Exception as exc:
            raise ExecutionFeedbackSourceUnavailableError() from exc
        return self.execution_store

    def _read_events(self) -> tuple[ExecutionEventV1, ...]:
        if self.execution_store is not None:
            try:
                return self.execution_store.read_event_records()
            except ExecutionFeedbackStoreError:
                raise
            except Exception as exc:
                raise ExecutionFeedbackStoreUnavailableError() from exc
        root = derive_execution_feedback_store_root(self.env_file)
        # A read must not create an operational store merely because the owner
        # opened the surface.  Missing root means a valid empty history.
        if root is None:
            return ()
        if not root.exists():
            return ()
        if root.is_symlink() or not root.is_dir():
            raise ExecutionFeedbackStoreUnavailableError()
        records_path = root / "events.jsonl"
        manifest_path = root / "manifest.json"
        if not records_path.is_file() or not manifest_path.is_file():
            raise ExecutionFeedbackStoreCorruptError()
        try:
            self.execution_store = ExecutionFeedbackOperationalStore(root)
            return self.execution_store.read_event_records()
        except ExecutionFeedbackStoreError:
            raise
        except Exception as exc:
            raise ExecutionFeedbackStoreUnavailableError() from exc

    def _catalog(self, current: PlanningPlanV1 | None) -> tuple[PlanningPlanV1, ...]:
        history = self.historical_plans()
        plans: list[PlanningPlanV1] = []
        seen: set[tuple[UUID, str]] = set()
        for plan in (*history, *((current,) if current is not None else ())):
            key = (cast(UUID, plan.plan_id), plan.plan_fingerprint)
            if key in seen:
                continue
            seen.add(key)
            plans.append(plan)
        return tuple(plans)

    @staticmethod
    def _source_matches_current(plan: PlanningPlanV1, service: PersonalPlanningWebService) -> bool:
        hook = getattr(service, "execution_source_status", None)
        if callable(hook):
            result = hook(plan)
            if type(result) is bool:
                return result
            if type(result) is ExecutionSourceStatusV1:
                return result is ExecutionSourceStatusV1.CURRENT
            if type(result) is str:
                return result == ExecutionSourceStatusV1.CURRENT.value
            raise ExecutionFeedbackSourceUnavailableError()
        if not isinstance(service, ProductionPersonalPlanningWebService):
            # Test seams and alternate deployments may already guarantee the
            # exact Stage 17 provenance.  Their current_plan identity remains
            # the authoritative freshness boundary.
            return True
        try:
            goals = service._goals()
            goal_keys = {
                (str(item.goal.source_note_uuid), item.goal_identity_fingerprint) for item in goals
            }
            strategy_store = service._strategy_store()
            for item in plan.items:
                if item.item_id not in plan.selected_item_ids or item.kind not in _EXECUTABLE_KINDS:
                    continue
                for goal_ref in item.goal_refs:
                    if (
                        str(goal_ref.goal_source_uuid),
                        goal_ref.goal_identity_fingerprint,
                    ) not in goal_keys:
                        return False
                for binding in item.action_refs:
                    snapshot = strategy_store.current_snapshot(
                        binding.goal_source_uuid,
                        binding.goal_identity_fingerprint,
                    )
                    if (
                        snapshot is None
                        or str(snapshot.snapshot_id) != str(binding.strategy_snapshot_id)
                        or snapshot.snapshot_fingerprint != binding.strategy_snapshot_fingerprint
                        or not any(
                            action.action_id == binding.reviewed_action_id
                            and _reviewed_action_fingerprint(action.as_dict())
                            == binding.reviewed_action_fingerprint
                            for action in snapshot.selected_actions
                        )
                    ):
                        return False
            return True
        except (PersonalPlanningError, PersonalPlanningStoreError) as exc:
            raise ExecutionFeedbackSourceUnavailableError() from exc

    def _source_status(
        self,
        plan: PlanningPlanV1,
        current: PlanningPlanV1 | None,
    ) -> ExecutionSourceStatusV1:
        if current is None:
            return ExecutionSourceStatusV1.UNAVAILABLE
        if plan != current:
            return ExecutionSourceStatusV1.SUPERSEDED
        try:
            return (
                ExecutionSourceStatusV1.CURRENT
                if self._source_matches_current(plan, self.planning_service)
                else ExecutionSourceStatusV1.STALE
            )
        except ExecutionFeedbackSourceUnavailableError:
            return ExecutionSourceStatusV1.UNAVAILABLE

    def _select_plan(
        self,
        planning_snapshot_id: str,
        planning_snapshot_fingerprint: str,
        *,
        current: PlanningPlanV1 | None,
    ) -> tuple[PlanningPlanV1, ExecutionSourceStatusV1]:
        try:
            requested_id = parse_uuid7(planning_snapshot_id)
        except TypeError, ValueError, OverflowError:
            raise ExecutionFeedbackInvalidRequestError() from None
        if (
            type(planning_snapshot_fingerprint) is not str
            or len(planning_snapshot_fingerprint) != 64
        ):
            raise ExecutionFeedbackInvalidRequestError()
        for plan in self._catalog(current):
            if (
                cast(UUID, plan.plan_id) == requested_id
                and plan.plan_fingerprint == planning_snapshot_fingerprint
            ):
                return plan, self._source_status(plan, current)
        raise ExecutionFeedbackSnapshotMismatchError()

    @staticmethod
    def _event_records_for_plan(
        events: tuple[ExecutionEventV1, ...],
        plan: PlanningPlanV1,
    ) -> tuple[ExecutionEventV1, ...]:
        return tuple(
            event
            for event in events
            if getattr(event, "planning_snapshot_id", None) == plan.plan_id
            and getattr(event, "planning_snapshot_fingerprint", None) == plan.plan_fingerprint
        )

    @staticmethod
    def _event_records_for_item(
        events: tuple[ExecutionEventV1, ...],
        plan: PlanningPlanV1,
        item_id: str,
        item_fingerprint: str,
    ) -> tuple[ExecutionEventV1, ...]:
        return tuple(
            event
            for event in events
            if getattr(event, "planning_snapshot_id", None) == plan.plan_id
            and getattr(event, "planning_snapshot_fingerprint", None) == plan.plan_fingerprint
            and getattr(event, "item_id", None) == item_id
            and getattr(event, "accepted_item_fingerprint", None) == item_fingerprint
        )

    @staticmethod
    def _validate_item_identity(plan: PlanningPlanV1, item_id: str, item_fingerprint: str) -> None:
        item = next((item for item in plan.items if item.item_id == item_id), None)
        if item is None or item.item_id not in plan.selected_item_ids:
            raise ExecutionFeedbackSnapshotMismatchError()
        if item.kind not in _EXECUTABLE_KINDS:
            raise ExecutionFeedbackNonExecutableError()
        if accepted_item_fingerprint(item) != item_fingerprint:
            raise ExecutionFeedbackItemMismatchError()

    def _report(
        self,
        plan: PlanningPlanV1,
        status: ExecutionSourceStatusV1,
        events: tuple[ExecutionEventV1, ...],
    ) -> ExecutionFeedbackReportV1:
        return build_execution_feedback_report(
            plan,
            self._event_records_for_plan(events, plan),
            source_status=status,
        )

    def agent_item_states(
        self,
        plan: PlanningPlanV1,
    ) -> dict[str, ExecutionItemStateV1]:
        """Return exact Stage18 projections for one reviewed Stage17 plan.

        Stage20 consumes this typed projection instead of the Web JSON report so
        it cannot accidentally reparse history, notes, or other Stage18 detail.
        The read remains provider-free and does not create a missing store.
        """

        if type(plan) is not PlanningPlanV1:
            raise ExecutionFeedbackSourceUnavailableError()
        current = self._current_plan()
        status = self._source_status(plan, current)
        report = self._report(plan, status, self._read_events())
        return {item.item_id: item for item in report.item_states}

    def _mutation_body(
        self,
        *,
        status: str,
        plan: PlanningPlanV1,
        source_status: ExecutionSourceStatusV1,
        event: ExecutionEventV1,
        events: tuple[ExecutionEventV1, ...],
    ) -> dict[str, object]:
        report = self._report(plan, source_status, events)
        item = next(
            (item for item in report.item_states if item.item_id == event.item_id),
            None,
        )
        return {
            "status": status,
            "plan": _plan_summary(plan, source_status=source_status),
            "event": _event_view(event),
            "item": None if item is None else item.as_dict(),
            "report": report.as_dict(),
        }

    def state(self) -> dict[str, object]:
        current = self._current_plan()
        events = self._read_events()
        if current is None:
            return {
                "current_plan": None,
                "report": None,
                "available_snapshots": [
                    _plan_summary(plan, source_status=ExecutionSourceStatusV1.SUPERSEDED)
                    for plan in self._catalog(None)
                ],
                "caveats": ["current_accepted_plan_missing"],
            }
        source_status = self._source_status(current, current)
        report = self._report(current, source_status, events)
        historical = tuple(plan for plan in self._catalog(current) if plan != current)
        return {
            "current_plan": _plan_summary(current, source_status=source_status),
            "report": report.as_dict(),
            "available_snapshots": [
                _plan_summary(current, source_status=source_status),
                *(
                    _plan_summary(plan, source_status=ExecutionSourceStatusV1.SUPERSEDED)
                    for plan in historical
                ),
            ],
            "caveats": list(report.caveats),
        }

    def event(self, payload: ExecutionFeedbackEventPayload) -> dict[str, object]:
        if type(payload) is not ExecutionFeedbackEventPayload:
            raise ExecutionFeedbackInvalidRequestError()
        current = self._current_plan()
        plan, source_status = self._select_plan(
            payload.planning_snapshot_id,
            payload.planning_snapshot_fingerprint,
            current=current,
        )
        self._validate_item_identity(
            plan,
            payload.item_id,
            payload.accepted_item_fingerprint,
        )
        try:
            event_type = ExecutionEventTypeV1(payload.event_type)
        except ValueError:
            raise ExecutionFeedbackInvalidRequestError() from None
        if event_type is ExecutionEventTypeV1.VOID:
            raise ExecutionFeedbackInvalidRequestError()
        if (
            event_type is ExecutionEventTypeV1.START
            and source_status is ExecutionSourceStatusV1.UNAVAILABLE
        ):
            raise ExecutionFeedbackSourceUnavailableError()
        if (
            event_type is ExecutionEventTypeV1.START
            and source_status is not ExecutionSourceStatusV1.CURRENT
        ):
            raise ExecutionFeedbackStalePlanError()
        events = self._read_events()
        if source_status is ExecutionSourceStatusV1.SUPERSEDED:
            chain = self._event_records_for_item(
                events,
                plan,
                payload.item_id,
                payload.accepted_item_fingerprint,
            )
            replay = replay_execution_events(chain, item_id=payload.item_id)
            if not any(
                item.event_type is ExecutionEventTypeV1.START for item in replay.effective_events
            ):
                raise ExecutionFeedbackStalePlanError()
        event = create_execution_event(
            plan,
            item_id=payload.item_id,
            event_type=event_type,
            operation_id=payload.operation_id,
            occurred_at=payload.occurred_at,
            received_at=self._now(),
            actual_effort_minutes=payload.actual_effort_minutes,
            effort_precision=payload.effort_precision,
            actual_result_note=payload.actual_result_note,
            reason_codes=tuple(payload.reason_codes),
            deviation_codes=tuple(payload.deviation_codes),
            result_disposition=payload.result_disposition,
        )
        existing_operation = next(
            (
                existing
                for existing in events
                if existing.operation_id_fingerprint == event.operation_id_fingerprint
            ),
            None,
        )
        if existing_operation is None:
            try:
                replay_execution_events(
                    (
                        *self._event_records_for_item(
                            events,
                            plan,
                            payload.item_id,
                            payload.accepted_item_fingerprint,
                        ),
                        event,
                    ),
                    item_id=payload.item_id,
                )
            except ExecutionFeedbackError as exc:
                raise ExecutionFeedbackStateConflictError() from exc
        envelope = self._store_for_mutation().append_event(
            event,
            plan=plan,
            current_plan=current,
            source_status=source_status,
        )
        final_events = self._read_events()
        status = "recorded" if envelope.record.event_id == event.event_id else "replayed"
        return self._mutation_body(
            status=status,
            plan=plan,
            source_status=source_status,
            event=envelope.record,
            events=final_events,
        )

    def feedback(self, payload: ExecutionFeedbackFeedbackPayload) -> dict[str, object]:
        if type(payload) is not ExecutionFeedbackFeedbackPayload:
            raise ExecutionFeedbackInvalidRequestError()
        current = self._current_plan()
        plan, source_status = self._select_plan(
            payload.planning_snapshot_id,
            payload.planning_snapshot_fingerprint,
            current=current,
        )
        report = self._report(plan, source_status, self._read_events())
        return {
            "status": "ok",
            "plan": _plan_summary(plan, source_status=source_status),
            "report": report.as_dict(),
        }

    def calibration(self, payload: ExecutionFeedbackCalibrationPayload) -> dict[str, object]:
        if type(payload) is not ExecutionFeedbackCalibrationPayload:
            raise ExecutionFeedbackInvalidRequestError()
        current = self._current_plan()
        selected = tuple(
            self._select_plan(
                item.planning_snapshot_id,
                item.planning_snapshot_fingerprint,
                current=current,
            )[0]
            for item in payload.snapshots
        )
        calibration = build_execution_calibration(
            selected,
            self._read_events(),
        )
        return {"status": "ok", "calibration": calibration.as_dict()}

    def correction(self, payload: ExecutionFeedbackCorrectionPayload) -> dict[str, object]:
        if type(payload) is not ExecutionFeedbackCorrectionPayload:
            raise ExecutionFeedbackInvalidRequestError()
        current = self._current_plan()
        plan, source_status = self._select_plan(
            payload.planning_snapshot_id,
            payload.planning_snapshot_fingerprint,
            current=current,
        )
        self._validate_item_identity(
            plan,
            payload.item_id,
            payload.accepted_item_fingerprint,
        )
        try:
            target_id = parse_uuid7(payload.void_target_event_id)
        except TypeError, ValueError, OverflowError:
            raise ExecutionFeedbackInvalidRequestError() from None
        events = self._read_events()
        chain = self._event_records_for_item(
            events,
            plan,
            payload.item_id,
            payload.accepted_item_fingerprint,
        )
        target = next((event for event in chain if event.event_id == target_id), None)
        if target is None or target.event_type is ExecutionEventTypeV1.VOID:
            raise ExecutionFeedbackCorrectionError()
        if execution_event_fingerprint(target) != payload.void_target_event_fingerprint:
            raise ExecutionFeedbackCorrectionError()
        try:
            event = create_execution_event(
                plan,
                item_id=payload.item_id,
                event_type=ExecutionEventTypeV1.VOID,
                operation_id=payload.operation_id,
                occurred_at=payload.occurred_at,
                received_at=self._now(),
                void_target_event_id=target_id,
                void_target_event_fingerprint=payload.void_target_event_fingerprint,
                correction_reason=payload.correction_reason,
            )
        except ExecutionFeedbackCorrectionError:
            raise
        except ExecutionFeedbackError as exc:
            raise ExecutionFeedbackCorrectionError() from exc
        existing_operation = next(
            (
                existing
                for existing in events
                if existing.operation_id_fingerprint == event.operation_id_fingerprint
            ),
            None,
        )
        if existing_operation is None:
            try:
                corrected = replay_execution_events((*chain, event), item_id=payload.item_id)
            except ExecutionFeedbackCorrectionError:
                raise
            except ExecutionFeedbackError as exc:
                raise ExecutionFeedbackCorrectionError() from exc
            if source_status is ExecutionSourceStatusV1.SUPERSEDED and not any(
                item.event_type is ExecutionEventTypeV1.START for item in corrected.effective_events
            ):
                raise ExecutionFeedbackStalePlanError()
        envelope = self._store_for_mutation().append_event(
            event,
            plan=plan,
            current_plan=current,
            source_status=source_status,
        )
        final_events = self._read_events()
        status = "recorded" if envelope.record.event_id == event.event_id else "replayed"
        return self._mutation_body(
            status=status,
            plan=plan,
            source_status=source_status,
            event=envelope.record,
            events=final_events,
        )


def _reviewed_action_fingerprint(value: object) -> str:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except TypeError, ValueError, UnicodeError:
        raise ExecutionFeedbackSourceUnavailableError() from None
    return hashlib.sha256(raw).hexdigest()


def build_production_execution_feedback_web_service(
    *,
    planning_service: PersonalPlanningWebService | None = None,
    planning_store: PersonalPlanningOperationalStore | None = None,
    execution_store: ExecutionFeedbackOperationalStore | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ProductionExecutionFeedbackWebService:
    return ProductionExecutionFeedbackWebService(
        planning_service=planning_service
        or build_production_personal_planning_web_service(
            env_file=env_file,
            vault_path_override=vault_path_override,
        ),
        planning_store=planning_store,
        execution_store=execution_store,
        env_file=env_file,
        vault_path_override=vault_path_override,
        clock=clock or (lambda: datetime.now(UTC)),
    )


def install_execution_feedback_routes(
    app: FastAPI,
    *,
    service: ExecutionFeedbackWebService | None = None,
    planning_service: PersonalPlanningWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install the additive owner-only Stage 18 API routes."""

    actual_service: ExecutionFeedbackWebService = (
        service
        or build_production_execution_feedback_web_service(
            planning_service=planning_service,
            env_file=env_file,
            vault_path_override=vault_path_override,
        )
    )
    app.add_middleware(ExecutionFeedbackRequestBoundaryMiddleware)

    @app.post(EXECUTION_FEEDBACK_STATE_PATH, include_in_schema=False)
    async def state_endpoint(request: Request) -> Response:
        try:
            await _read_payload(request, ExecutionFeedbackEmptyPayload)
            body = await run_in_threadpool(actual_service.state)
            return _json_response(
                {"web_contract": "execution_feedback_state_web_v1", **body},
                max_bytes=MAX_EXECUTION_FEEDBACK_STATE_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(EXECUTION_FEEDBACK_EVENT_PATH, include_in_schema=False)
    async def event_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                ExecutionFeedbackEventPayload,
                await _read_payload(request, ExecutionFeedbackEventPayload),
            )
            body = await run_in_threadpool(actual_service.event, payload)
            return _json_response(
                {"web_contract": "execution_feedback_event_web_v1", **body},
                max_bytes=MAX_EXECUTION_FEEDBACK_MUTATION_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(EXECUTION_FEEDBACK_FEEDBACK_PATH, include_in_schema=False)
    async def feedback_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                ExecutionFeedbackFeedbackPayload,
                await _read_payload(request, ExecutionFeedbackFeedbackPayload),
            )
            body = await run_in_threadpool(actual_service.feedback, payload)
            return _json_response(
                {"web_contract": "execution_feedback_feedback_web_v1", **body},
                max_bytes=MAX_EXECUTION_FEEDBACK_FEEDBACK_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(EXECUTION_FEEDBACK_CALIBRATION_PATH, include_in_schema=False)
    async def calibration_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                ExecutionFeedbackCalibrationPayload,
                await _read_payload(request, ExecutionFeedbackCalibrationPayload),
            )
            body = await run_in_threadpool(actual_service.calibration, payload)
            return _json_response(
                {"web_contract": "execution_feedback_calibration_web_v1", **body},
                max_bytes=MAX_EXECUTION_FEEDBACK_CALIBRATION_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(EXECUTION_FEEDBACK_CORRECTION_PATH, include_in_schema=False)
    async def correction_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                ExecutionFeedbackCorrectionPayload,
                await _read_payload(request, ExecutionFeedbackCorrectionPayload),
            )
            body = await run_in_threadpool(actual_service.correction, payload)
            return _json_response(
                {"web_contract": "execution_feedback_correction_web_v1", **body},
                max_bytes=MAX_EXECUTION_FEEDBACK_MUTATION_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)


__all__ = [
    "EXECUTION_FEEDBACK_CALIBRATION_PATH",
    "EXECUTION_FEEDBACK_CORRECTION_PATH",
    "EXECUTION_FEEDBACK_EVENT_PATH",
    "EXECUTION_FEEDBACK_FEEDBACK_PATH",
    "EXECUTION_FEEDBACK_REQUEST_HEADER_NAME",
    "EXECUTION_FEEDBACK_REQUEST_HEADER_VALUE",
    "EXECUTION_FEEDBACK_STATE_PATH",
    "MAX_EXECUTION_FEEDBACK_CALIBRATION_RESPONSE_BYTES",
    "MAX_EXECUTION_FEEDBACK_FEEDBACK_RESPONSE_BYTES",
    "MAX_EXECUTION_FEEDBACK_MUTATION_RESPONSE_BYTES",
    "MAX_EXECUTION_FEEDBACK_STATE_RESPONSE_BYTES",
    "MAX_RAW_EXECUTION_FEEDBACK_BODY_BYTES",
    "ExecutionFeedbackCalibrationPayload",
    "ExecutionFeedbackCorrectionPayload",
    "ExecutionFeedbackEmptyPayload",
    "ExecutionFeedbackEventPayload",
    "ExecutionFeedbackFeedbackPayload",
    "ExecutionFeedbackInvalidRequestError",
    "ExecutionFeedbackPlanSelectionPayload",
    "ExecutionFeedbackRequestBoundaryMiddleware",
    "ExecutionFeedbackSourceUnavailableError",
    "ExecutionFeedbackStalePlanError",
    "ExecutionFeedbackStateConflictError",
    "ExecutionFeedbackWebError",
    "ExecutionFeedbackWebService",
    "ProductionExecutionFeedbackWebService",
    "build_production_execution_feedback_web_service",
    "install_execution_feedback_routes",
]
