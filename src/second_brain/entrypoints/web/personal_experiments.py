"""Private owner-only Web/API projection for Personal Experiments v1.

The application modules own the Stage 14 record, Safe Write and provider-free
evaluation semantics.  This module only validates the browser envelope,
projects bounded owner-facing state and keeps reviewed plans in process memory
until one explicit apply request consumes them.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol, cast
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.adapters.vault import FileSystemVaultReader, FileSystemVaultWriter
from second_brain.application.goal_progress import (
    DefinitionRecordV1,
    ObservationEligibilityReasonV1,
    ObservationRecordV1,
    SupersessionChainStateV1,
    evaluate_observation_eligibility,
    validate_definition_chain,
    validate_observation_chain,
)
from second_brain.application.personal_experiments import (
    PERSONAL_EXPERIMENT_DERIVATION_ID,
    PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
    PERSONAL_EXPERIMENT_POLICY_ID,
    PersonalExperimentChainResultV1,
    PersonalExperimentDefinitionRecordV1,
    PersonalExperimentLifecycleRecordV1,
    PersonalExperimentLifecycleValidationV1,
    PersonalExperimentObservationRecordV1,
    PersonalExperimentReadProjectionV1,
    PersonalExperimentReassessmentRecordV1,
    validate_personal_experiment_definition_chain,
    validate_personal_experiment_observation_chain,
    validate_personal_experiment_reassessment_chain,
)
from second_brain.application.personal_experiments_evaluator import (
    MAX_PERSONAL_EXPERIMENT_RESULT_BYTES,
    PersonalExperimentEvaluationError,
    PersonalExperimentEvaluationRequestV1,
    PersonalExperimentEvaluationResultV1,
    evaluate_personal_experiment,
)
from second_brain.application.personal_experiments_safe_write import (
    PersonalExperimentDefinitionDraftV1,
    PersonalExperimentLifecycleDraftV1,
    PersonalExperimentNoteWriter,
    PersonalExperimentObservationDraftV1,
    PersonalExperimentReassessmentDraftV1,
    PersonalExperimentReviewError,
    PersonalExperimentReviewStore,
    PersonalExperimentSafeWrite,
    PersonalExperimentSafeWritePlanV1,
    PersonalExperimentSafeWriteResult,
)
from second_brain.application.reports import ScanReport
from second_brain.application.validation import build_report
from second_brain.application.writes import CreateStatus
from second_brain.config import AppConfig, load_config
from second_brain.entrypoints.web.auth import (
    AUTH_USER_ID_SCOPE_KEY,
    request_host_is_trusted,
    same_origin_is_trusted,
    trusted_authorities_from_scope,
)
from second_brain.entrypoints.web.growth import (
    GrowthGoalOwnerItemV1,
    ProductionGrowthWebService,
)

PERSONAL_EXPERIMENTS_PATH: Final[str] = "/api/personal-experiments"
PERSONAL_EXPERIMENT_DEFINITION_PREPARE_PATH: Final[str] = (
    "/api/personal-experiments/definitions/prepare"
)
PERSONAL_EXPERIMENT_DEFINITION_APPLY_PATH: Final[str] = (
    "/api/personal-experiments/definitions/apply"
)
PERSONAL_EXPERIMENT_LIFECYCLE_PREPARE_PATH: Final[str] = (
    "/api/personal-experiments/lifecycle/prepare"
)
PERSONAL_EXPERIMENT_LIFECYCLE_APPLY_PATH: Final[str] = "/api/personal-experiments/lifecycle/apply"
PERSONAL_EXPERIMENT_OBSERVATION_PREPARE_PATH: Final[str] = (
    "/api/personal-experiments/observations/prepare"
)
PERSONAL_EXPERIMENT_OBSERVATION_APPLY_PATH: Final[str] = (
    "/api/personal-experiments/observations/apply"
)
PERSONAL_EXPERIMENT_EVALUATE_PATH: Final[str] = "/api/personal-experiments/evaluate"
PERSONAL_EXPERIMENT_REASSESSMENT_PREPARE_PATH: Final[str] = (
    "/api/personal-experiments/reassessments/prepare"
)
PERSONAL_EXPERIMENT_REASSESSMENT_APPLY_PATH: Final[str] = (
    "/api/personal-experiments/reassessments/apply"
)

PERSONAL_EXPERIMENT_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
PERSONAL_EXPERIMENT_REQUEST_HEADER_VALUE: Final[str] = "personal-experiment-v1"
MAX_RAW_PERSONAL_EXPERIMENT_BODY_BYTES: Final[int] = 256 * 1024
MAX_PERSONAL_EXPERIMENT_STATE_RESPONSE_BYTES: Final[int] = 256 * 1024
MAX_PERSONAL_EXPERIMENT_REVIEW_RESPONSE_BYTES: Final[int] = 256 * 1024
MAX_PERSONAL_EXPERIMENT_APPLY_RESPONSE_BYTES: Final[int] = 32 * 1024

_PERSONAL_EXPERIMENT_PATHS: Final[frozenset[str]] = frozenset(
    {
        PERSONAL_EXPERIMENTS_PATH,
        PERSONAL_EXPERIMENT_DEFINITION_PREPARE_PATH,
        PERSONAL_EXPERIMENT_DEFINITION_APPLY_PATH,
        PERSONAL_EXPERIMENT_LIFECYCLE_PREPARE_PATH,
        PERSONAL_EXPERIMENT_LIFECYCLE_APPLY_PATH,
        PERSONAL_EXPERIMENT_OBSERVATION_PREPARE_PATH,
        PERSONAL_EXPERIMENT_OBSERVATION_APPLY_PATH,
        PERSONAL_EXPERIMENT_EVALUATE_PATH,
        PERSONAL_EXPERIMENT_REASSESSMENT_PREPARE_PATH,
        PERSONAL_EXPERIMENT_REASSESSMENT_APPLY_PATH,
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

_INVALID_REQUEST_CODE: Final[str] = "PERSONAL_EXPERIMENT_WEB_INVALID_REQUEST"
_CONTENT_TOO_LARGE_CODE: Final[str] = "PERSONAL_EXPERIMENT_WEB_CONTENT_TOO_LARGE"
_INVALID_REQUEST_MESSAGE: Final[str] = "Запрос личного эксперимента не прошёл проверку."
_CONTENT_TOO_LARGE_MESSAGE: Final[str] = "Запрос личного эксперимента слишком велик."
_SOURCE_UNAVAILABLE_MESSAGE: Final[str] = "Текущий источник личных экспериментов недоступен."
_GENERIC_ERROR_MESSAGE: Final[str] = "Операция личного эксперимента сейчас недоступна."

_SAFE_WRITE_MESSAGES: Final[dict[str, str]] = {
    "PERSONAL_EXPERIMENT_REQUEST_INVALID": "Данные личного эксперимента не прошли проверку.",
    "PERSONAL_EXPERIMENT_SOURCE_UNAVAILABLE": _SOURCE_UNAVAILABLE_MESSAGE,
    "PERSONAL_EXPERIMENT_VAULT_CHANGED": (
        "Состояние хранилища изменилось после review. Обнови поверхность и повтори."
    ),
    "PERSONAL_EXPERIMENT_SAFE_WRITE_REVIEW_REQUIRED": (
        "Нужно открыть свежий review и явно подтвердить запись."
    ),
    "PERSONAL_EXPERIMENT_SAFE_WRITE_VALIDATION_FAILED": (
        "Проверка после записи не пройдена; новая запись отклонена."
    ),
    "PERSONAL_EXPERIMENT_ROLLBACK_FAILED": "Не удалось безопасно откатить новую запись.",
    "PERSONAL_EXPERIMENT_INTERNAL": _GENERIC_ERROR_MESSAGE,
    "CREATE_TARGET_EXISTS": "Целевой идентификатор уже занят; существующие данные не изменены.",
    "PERSONAL_EXPERIMENT_BASELINE_REQUIRED": "Выбери явный baseline перед review.",
    "PERSONAL_EXPERIMENT_BASELINE_INVALID": "Выбранный baseline не соответствует эксперименту.",
    "PERSONAL_EXPERIMENT_BASELINE_TIME_REQUIRED": "Для baseline нужно точное время наблюдения.",
    "PERSONAL_EXPERIMENT_GOAL_REQUIRED": "Выбери одну точную текущую Goal.",
    "PERSONAL_EXPERIMENT_GOAL_SOURCE_CHANGED": "Точная Goal изменилась; обнови список.",
    "PERSONAL_EXPERIMENT_GOAL_AMBIGUOUS": "Goal неоднозначна; запись остановлена.",
    "PERSONAL_EXPERIMENT_STAGE12_DEFINITION_REQUIRED": (
        "Выбери одну активную Stage 12 progress definition."
    ),
    "PERSONAL_EXPERIMENT_STAGE12_DEFINITION_CHANGED": (
        "Точная Stage 12 progress definition изменилась; обнови список."
    ),
    "PERSONAL_EXPERIMENT_STAGE12_DEFINITION_CONFLICT": (
        "Цепочка Stage 12 progress definition содержит конфликт."
    ),
    "PERSONAL_EXPERIMENT_DEFINITION_MISSING": "Определение эксперимента не найдено.",
    "PERSONAL_EXPERIMENT_DEFINITION_CHANGED": "Определение эксперимента изменилось.",
    "PERSONAL_EXPERIMENT_DEFINITION_STALE": "Определение эксперимента уже заменено.",
    "PERSONAL_EXPERIMENT_DEFINITION_CONFLICT": (
        "Цепочка определений эксперимента содержит конфликт."
    ),
    "PERSONAL_EXPERIMENT_LIFECYCLE_REQUIRED": "Сначала создай definition эксперимента.",
    "PERSONAL_EXPERIMENT_LIFECYCLE_INVALID": "Это событие жизненного цикла недопустимо сейчас.",
    "PERSONAL_EXPERIMENT_LIFECYCLE_STALE": "Событие жизненного цикла уже заменено.",
    "PERSONAL_EXPERIMENT_LIFECYCLE_CONFLICT": "Цепочка жизненного цикла содержит конфликт.",
    "PERSONAL_EXPERIMENT_ONE_ACTIVE_PER_GOAL": (
        "Для одной точной Goal уже есть активный эксперимент."
    ),
    "PERSONAL_EXPERIMENT_SOURCE_MISSING": "Исходная запись Stage 12 не найдена.",
    "PERSONAL_EXPERIMENT_SOURCE_CHANGED": "Исходная запись Stage 12 изменилась.",
    "PERSONAL_EXPERIMENT_OBSERVATION_TIME_REQUIRED": (
        "Для enrollment нужно точное время наблюдения."
    ),
    "PERSONAL_EXPERIMENT_OBSERVATION_OUTSIDE_WINDOW": (
        "Наблюдение находится вне окна эксперимента."
    ),
    "PERSONAL_EXPERIMENT_OBSERVATION_LIMIT_EXCEEDED": "Достигнут предел enrolled observations.",
    "PERSONAL_EXPERIMENT_OBSERVATION_STALE": "Enrollment observation уже заменено.",
    "PERSONAL_EXPERIMENT_OBSERVATION_INVALID": "Enrollment observation недействительно.",
    "PERSONAL_EXPERIMENT_OBSERVATION_CONFLICT": (
        "Цепочка enrollment observations содержит конфликт."
    ),
    "PERSONAL_EXPERIMENT_TERMINAL_REQUIRED": "Сначала явно заверши или отмени эксперимент.",
    "PERSONAL_EXPERIMENT_EVALUATION_WINDOW_INVALID": "Cutoff результата вне окна эксперимента.",
    "PERSONAL_EXPERIMENT_REASSESSMENT_STALE": "Reassessment уже заменён.",
    "PERSONAL_EXPERIMENT_REASSESSMENT_CONFLICT": "Цепочка reassessment содержит конфликт.",
    "PERSONAL_EXPERIMENT_POLICY_MISMATCH": "Политика личного эксперимента недействительна.",
    "PERSONAL_EXPERIMENT_INVALID_FIELD": "Одно из полей личного эксперимента недействительно.",
    "PERSONAL_EXPERIMENT_RESULT_TOO_LARGE": "Review-план личного эксперимента слишком велик.",
    "PERSONAL_EXPERIMENT_REVIEW_STORE_FULL": "Временное review-хранилище переполнено.",
}

_CONFLICT_CODES: Final[frozenset[str]] = frozenset(
    {
        "CREATE_TARGET_EXISTS",
        "PERSONAL_EXPERIMENT_VAULT_CHANGED",
        "PERSONAL_EXPERIMENT_SAFE_WRITE_REVIEW_REQUIRED",
        "PERSONAL_EXPERIMENT_BASELINE_INVALID",
        "PERSONAL_EXPERIMENT_GOAL_SOURCE_CHANGED",
        "PERSONAL_EXPERIMENT_GOAL_AMBIGUOUS",
        "PERSONAL_EXPERIMENT_STAGE12_DEFINITION_CHANGED",
        "PERSONAL_EXPERIMENT_STAGE12_DEFINITION_CONFLICT",
        "PERSONAL_EXPERIMENT_DEFINITION_CHANGED",
        "PERSONAL_EXPERIMENT_DEFINITION_STALE",
        "PERSONAL_EXPERIMENT_DEFINITION_CONFLICT",
        "PERSONAL_EXPERIMENT_LIFECYCLE_STALE",
        "PERSONAL_EXPERIMENT_LIFECYCLE_CONFLICT",
        "PERSONAL_EXPERIMENT_ONE_ACTIVE_PER_GOAL",
        "PERSONAL_EXPERIMENT_SOURCE_CHANGED",
        "PERSONAL_EXPERIMENT_OBSERVATION_OUTSIDE_WINDOW",
        "PERSONAL_EXPERIMENT_OBSERVATION_STALE",
        "PERSONAL_EXPERIMENT_OBSERVATION_CONFLICT",
        "PERSONAL_EXPERIMENT_TERMINAL_REQUIRED",
        "PERSONAL_EXPERIMENT_EVALUATION_WINDOW_INVALID",
        "PERSONAL_EXPERIMENT_REASSESSMENT_STALE",
        "PERSONAL_EXPERIMENT_REASSESSMENT_CONFLICT",
    }
)


class PersonalExperimentEmptyPayload(BaseModel):
    """Strict empty body for the current experiment list."""

    model_config = ConfigDict(extra="forbid", strict=True)


class PersonalExperimentDefinitionPreparePayload(BaseModel):
    """Owner-authored definition semantics with exact source identities."""

    model_config = ConfigDict(extra="forbid", strict=True)

    goal_source_uuid: StrictStr
    goal_identity_fingerprint: StrictStr
    goal_progress_definition_id: StrictStr
    goal_progress_definition_fingerprint: StrictStr
    hypothesis: StrictStr
    intervention: StrictStr
    baseline_strategy: StrictStr
    baseline_observation_uuid: StrictStr | None = None
    baseline_observation_fingerprint: StrictStr | None = None
    supersedes_definition_id: StrictStr | None = None
    supersedes_definition_fingerprint: StrictStr | None = None


class PersonalExperimentLifecyclePreparePayload(BaseModel):
    """One explicit activation, completion or cancellation intent."""

    model_config = ConfigDict(extra="forbid", strict=True)

    experiment_definition_id: StrictStr
    experiment_definition_fingerprint: StrictStr
    lifecycle_event: StrictStr
    event_at: StrictStr
    supersedes_lifecycle_id: StrictStr | None = None
    supersedes_lifecycle_fingerprint: StrictStr | None = None


class PersonalExperimentObservationPreparePayload(BaseModel):
    """Enrollment of one exact Stage 12 observation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    experiment_definition_id: StrictStr
    experiment_definition_fingerprint: StrictStr
    stage12_observation_id: StrictStr
    stage12_observation_fingerprint: StrictStr
    supersedes_observation_id: StrictStr | None = None
    supersedes_observation_fingerprint: StrictStr | None = None


class PersonalExperimentReassessmentPreparePayload(BaseModel):
    """Owner-reviewed reassessment tied to one derived result fingerprint."""

    model_config = ConfigDict(extra="forbid", strict=True)

    experiment_definition_id: StrictStr
    experiment_definition_fingerprint: StrictStr
    result_fingerprint: StrictStr
    evaluation_as_of: StrictStr
    evaluation_policy_fingerprint: StrictStr
    disposition: StrictStr
    rationale: StrictStr
    supersedes_reassessment_id: StrictStr | None = None
    supersedes_reassessment_fingerprint: StrictStr | None = None


class PersonalExperimentEvaluationPayload(BaseModel):
    """Exact identity and explicit UTC cutoff for one provider-free result."""

    model_config = ConfigDict(extra="forbid", strict=True)

    experiment_definition_id: StrictStr
    experiment_definition_fingerprint: StrictStr
    as_of: StrictStr


class PersonalExperimentApplyPayload(BaseModel):
    """Apply only the owner-bound one-time review token and exact plan hash."""

    model_config = ConfigDict(extra="forbid", strict=True)

    review_token: StrictStr
    accepted_plan_sha256: StrictStr
    confirmed: StrictBool


type PersonalExperimentWebDraft = (
    PersonalExperimentDefinitionDraftV1
    | PersonalExperimentLifecycleDraftV1
    | PersonalExperimentObservationDraftV1
    | PersonalExperimentReassessmentDraftV1
)


class PersonalExperimentWebService(Protocol):
    """Injectable seam for the private owner-facing transport."""

    def state(self) -> dict[str, object]:
        """Return the bounded current list and exact selectable sources."""
        ...

    def prepare(self, draft: PersonalExperimentWebDraft) -> PersonalExperimentSafeWriteResult:
        """Create one dry-run plan without a write."""
        ...

    def apply(
        self,
        plan: PersonalExperimentSafeWritePlanV1,
        accepted_plan_sha256: str,
    ) -> PersonalExperimentSafeWriteResult:
        """Apply one exact reviewed plan."""
        ...

    def evaluate(
        self,
        request: PersonalExperimentEvaluationRequestV1,
    ) -> PersonalExperimentEvaluationResultV1:
        """Build one provider-free result."""
        ...


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


def _error_response(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=_API_HEADERS,
    )


def _invalid_request() -> JSONResponse:
    return _error_response(_INVALID_REQUEST_CODE, _INVALID_REQUEST_MESSAGE, 400)


def _too_large() -> JSONResponse:
    return _error_response(_CONTENT_TOO_LARGE_CODE, _CONTENT_TOO_LARGE_MESSAGE, 413)


def _method_not_allowed() -> JSONResponse:
    response = _invalid_request()
    response.status_code = 405
    response.headers["Allow"] = "POST"
    return response


async def _send(response: Response, scope: Scope, receive: Receive, send: Send) -> None:
    await response(scope, receive, send)


class PersonalExperimentRequestBoundaryMiddleware:
    """Fail-closed trusted Host, same-origin, purpose and body boundary."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _PERSONAL_EXPERIMENT_PATHS:
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
            or purpose != PERSONAL_EXPERIMENT_REQUEST_HEADER_VALUE
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
            if declared > MAX_RAW_PERSONAL_EXPERIMENT_BODY_BYTES:
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
            if len(body) > MAX_RAW_PERSONAL_EXPERIMENT_BODY_BYTES:
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


def _safe_json(value: object, *, max_bytes: int) -> bytes:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError("response exceeds the transport bound")
    return encoded


def _json_response(value: object, *, max_bytes: int) -> Response:
    return Response(
        content=_safe_json(value, max_bytes=max_bytes),
        media_type="application/json",
        headers=_API_HEADERS,
    )


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(value)


def _clock_utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock is invalid")
    return value.astimezone(UTC)


def _record_fingerprint(record: object) -> str:
    if isinstance(record, PersonalExperimentDefinitionRecordV1):
        return record.experiment_definition_fingerprint
    if isinstance(record, PersonalExperimentLifecycleRecordV1):
        return record.lifecycle_fingerprint
    if isinstance(record, PersonalExperimentObservationRecordV1):
        return record.observation_fingerprint
    if isinstance(record, PersonalExperimentReassessmentRecordV1):
        return record.reassessment_fingerprint
    raise ValueError("record kind is invalid")


def _stage12_definition_group(
    report: ScanReport,
    definition: DefinitionRecordV1,
) -> tuple[DefinitionRecordV1, ...]:
    return tuple(
        item
        for item in report.goal_progress_definitions
        if item.goal_source_uuid == definition.goal_source_uuid
        and item.goal_identity_fingerprint == definition.goal_identity_fingerprint
    )


def _stage12_definition_is_active(report: ScanReport, definition: DefinitionRecordV1) -> bool:
    chain = validate_definition_chain(_stage12_definition_group(report, definition))
    return (
        not chain.issues
        and chain.state is SupersessionChainStateV1.ONE_ACTIVE
        and len(chain.active_records) == 1
        and chain.active_records[0].id == definition.id
    )


def _stage12_observation_group(
    report: ScanReport,
    definition: DefinitionRecordV1,
) -> tuple[ObservationRecordV1, ...]:
    return tuple(
        item
        for item in report.goal_progress_observations
        if item.goal_source_uuid == definition.goal_source_uuid
        and item.goal_identity_fingerprint == definition.goal_identity_fingerprint
        and item.progress_definition_id == definition.id
        and item.definition_fingerprint == definition.definition_fingerprint
    )


def _stage12_active_observation_ids(
    report: ScanReport,
    definition: DefinitionRecordV1,
) -> set[UUID]:
    chain = validate_observation_chain(_stage12_observation_group(report, definition))
    if chain.issues:
        return set()
    return {cast(UUID, item.id) for item in chain.active_records}


def _goal_projection(item: GrowthGoalOwnerItemV1) -> dict[str, object]:
    return {
        "source_note_uuid": str(item.goal.source_note_uuid),
        "goal_text": item.goal_text,
        "domain": item.goal.domain,
        "goal_identity_fingerprint": item.goal_identity_fingerprint,
    }


def _stage12_definition_projection(
    report: ScanReport,
    definition: DefinitionRecordV1,
) -> dict[str, object]:
    result = definition.as_dict()
    result["definition_fingerprint"] = definition.definition_fingerprint
    result["active"] = _stage12_definition_is_active(report, definition)
    return result


def _stage12_observation_projection(
    report: ScanReport,
    definition: DefinitionRecordV1,
    observation: ObservationRecordV1,
) -> dict[str, object]:
    result = observation.as_dict()
    result["observation_fingerprint"] = observation.observation_fingerprint
    result["active"] = cast(UUID, observation.id) in _stage12_active_observation_ids(
        report,
        definition,
    )
    return result


def _active_enrollment_records(
    records: Iterable[PersonalExperimentObservationRecordV1],
) -> tuple[PersonalExperimentObservationRecordV1, ...]:
    chain = _validate_personal_observation_chain(records)
    if chain.issues:
        return ()
    return tuple(cast(PersonalExperimentObservationRecordV1, item) for item in chain.active_records)


def _validate_personal_observation_chain(
    records: Iterable[PersonalExperimentObservationRecordV1],
) -> PersonalExperimentChainResultV1:
    return validate_personal_experiment_observation_chain(records)


def _lifecycle_projection(
    chain: PersonalExperimentLifecycleValidationV1 | None,
) -> dict[str, object]:
    if chain is None:
        return {"state": "planned", "activation": None, "terminal": None, "issues": []}

    def record_projection(
        record: PersonalExperimentLifecycleRecordV1 | None,
    ) -> dict[str, object] | None:
        if record is None:
            return None
        result = record.as_dict()
        result["lifecycle_fingerprint"] = record.lifecycle_fingerprint
        return result

    return {
        "state": chain.state,
        "activation": record_projection(chain.activation),
        "terminal": record_projection(chain.terminal),
        "issues": list(chain.issues),
    }


def _eligible_stage12_observations(
    report: ScanReport,
    definition: PersonalExperimentDefinitionRecordV1,
    lifecycle: PersonalExperimentLifecycleValidationV1 | None,
    stage12_definition: DefinitionRecordV1 | None,
    enrolled: tuple[PersonalExperimentObservationRecordV1, ...],
    as_of: datetime,
) -> list[dict[str, object]]:
    if (
        lifecycle is None
        or lifecycle.state != "active"
        or lifecycle.activation is None
        or stage12_definition is None
        or not _stage12_definition_is_active(report, stage12_definition)
    ):
        return []
    enrolled_ids = {item.stage12_observation_id for item in enrolled}
    active_ids = _stage12_active_observation_ids(report, stage12_definition)
    activation_at = cast(datetime, lifecycle.activation.event_at)
    result: list[dict[str, object]] = []
    for observation in _stage12_observation_group(report, stage12_definition):
        observation_id = cast(UUID, observation.id)
        if observation_id in enrolled_ids or observation_id not in active_ids:
            continue
        try:
            eligibility = evaluate_observation_eligibility(
                observation,
                stage12_definition,
                as_of=as_of,
            )
        except Exception:
            continue
        if eligibility.reason is not ObservationEligibilityReasonV1.ELIGIBLE:
            continue
        if not isinstance(observation.observed_at, datetime):
            continue
        if observation.observed_at < activation_at:
            continue
        if cast(datetime, observation.observation_reviewed_at) > as_of:
            continue
        projected = observation.as_dict()
        projected["observation_fingerprint"] = observation.observation_fingerprint
        projected["eligible"] = True
        result.append(projected)
    result.sort(key=lambda item: (str(item["observed_at"]), str(item["id"])))
    return result


def _experiment_projection(
    report: ScanReport,
    definition: PersonalExperimentDefinitionRecordV1,
    goals_by_id: Mapping[UUID, GrowthGoalOwnerItemV1],
    as_of: datetime,
) -> dict[str, object]:
    projection: PersonalExperimentReadProjectionV1 = report.personal_experiment_read_projection
    definition_records = tuple(
        item
        for item in projection.definitions
        if item.goal_source_uuid == definition.goal_source_uuid
        and item.goal_identity_fingerprint == definition.goal_identity_fingerprint
        and item.goal_progress_definition_id == definition.goal_progress_definition_id
        and item.goal_progress_definition_fingerprint
        == definition.goal_progress_definition_fingerprint
    )
    definition_chain = validate_personal_experiment_definition_chain(definition_records)
    if definition_chain.issues:
        definition_state = "invalid"
    else:
        active_ids = {cast(UUID, item.id) for item in definition_chain.active_records}
        definition_state = "active" if cast(UUID, definition.id) in active_ids else "superseded"

    lifecycle_chain = next(
        (
            chain
            for chain in projection.lifecycle_chains
            if chain.experiment_definition_id == definition.id
            and chain.experiment_definition_fingerprint
            == definition.experiment_definition_fingerprint
        ),
        None,
    )
    enrollment_records = tuple(
        item
        for item in projection.observations
        if item.experiment_definition_id == definition.id
        and item.experiment_definition_fingerprint == definition.experiment_definition_fingerprint
    )
    active_enrollments = _active_enrollment_records(enrollment_records)
    reassessments = tuple(
        item
        for item in projection.reassessments
        if item.experiment_definition_id == definition.id
        and item.experiment_definition_fingerprint == definition.experiment_definition_fingerprint
    )
    reassessment_chain = (
        validate_personal_experiment_reassessment_chain(reassessments) if reassessments else None
    )
    stage12_definition = next(
        (
            item
            for item in report.goal_progress_definitions
            if item.id == definition.goal_progress_definition_id
            and item.definition_fingerprint == definition.goal_progress_definition_fingerprint
            and item.goal_source_uuid == definition.goal_source_uuid
            and item.goal_identity_fingerprint == definition.goal_identity_fingerprint
        ),
        None,
    )
    result: dict[str, object] = {
        "definition": {
            **definition.as_dict(),
            "experiment_definition_fingerprint": definition.experiment_definition_fingerprint,
            "definition_fingerprint": definition.experiment_definition_fingerprint,
            "state": definition_state,
        },
        "goal": _goal_projection(goals_by_id[cast(UUID, definition.goal_source_uuid)])
        if cast(UUID, definition.goal_source_uuid) in goals_by_id
        else None,
        "lifecycle": _lifecycle_projection(lifecycle_chain),
        "enrollments": [
            {
                **item.as_dict(),
                "observation_fingerprint": item.observation_fingerprint,
            }
            for item in active_enrollments
        ],
        "reassessments": [
            {
                **item.as_dict(),
                "reassessment_fingerprint": item.reassessment_fingerprint,
            }
            for item in reassessments
            if reassessment_chain is None
            or cast(UUID, item.id)
            in {cast(UUID, active.id) for active in reassessment_chain.active_records}
        ],
        "eligible_stage12_observations": _eligible_stage12_observations(
            report,
            definition,
            lifecycle_chain,
            stage12_definition,
            active_enrollments,
            as_of,
        ),
    }
    return result


def build_personal_experiments_state(
    report: ScanReport,
    goals: Iterable[GrowthGoalOwnerItemV1],
    *,
    as_of: datetime,
) -> dict[str, object]:
    """Build one bounded owner projection without raw notes or storage paths."""

    goal_items = tuple(sorted(goals, key=lambda item: str(item.goal.source_note_uuid)))
    goals_by_id = {cast(UUID, item.goal.source_note_uuid): item for item in goal_items}
    exact_goal_pairs = {
        (cast(UUID, item.goal.source_note_uuid), item.goal_identity_fingerprint)
        for item in goal_items
    }
    stage12_definitions = tuple(
        item
        for item in report.goal_progress_definitions
        if (cast(UUID, item.goal_source_uuid), item.goal_identity_fingerprint) in exact_goal_pairs
    )
    stage12_observations = tuple(
        item
        for item in report.goal_progress_observations
        if (cast(UUID, item.goal_source_uuid), item.goal_identity_fingerprint) in exact_goal_pairs
    )
    projection = report.personal_experiment_read_projection
    definitions = projection.definitions
    stage12_observation_projections: list[dict[str, object]] = []
    for item in sorted(stage12_observations, key=lambda value: str(value.id)):
        definition = next(
            (
                candidate
                for candidate in stage12_definitions
                if candidate.goal_source_uuid == item.goal_source_uuid
                and candidate.goal_identity_fingerprint == item.goal_identity_fingerprint
                and candidate.id == item.progress_definition_id
                and candidate.definition_fingerprint == item.definition_fingerprint
            ),
            None,
        )
        if definition is not None:
            stage12_observation_projections.append(
                _stage12_observation_projection(report, definition, item)
            )
    body: dict[str, object] = {
        "web_contract": "personal_experiments_web_v1",
        "contract_id": "personal-experiments-v1",
        "derivation_id": PERSONAL_EXPERIMENT_DERIVATION_ID,
        "policy_id": PERSONAL_EXPERIMENT_POLICY_ID,
        "policy_fingerprint": PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
        "generated_at": as_of.isoformat().replace("+00:00", "Z"),
        "goals": [_goal_projection(item) for item in goal_items],
        "stage12_definitions": [
            _stage12_definition_projection(report, item)
            for item in sorted(stage12_definitions, key=lambda value: str(value.id))
        ],
        "stage12_observations": stage12_observation_projections,
        "experiments": [
            _experiment_projection(report, item, goals_by_id, as_of)
            for item in sorted(definitions, key=lambda value: str(value.id))
        ],
        "caveats": [
            "observed_change_is_not_proof_of_causation",
            "no_automatic_adaptation",
            "no_provider_or_growth_learning",
        ],
    }
    return body


@dataclass(slots=True)
class ProductionPersonalExperimentWebService:
    """Lazily construct current-vault services for one owner request."""

    env_file: Path | None = None
    vault_path_override: str | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def _config(self) -> AppConfig:
        return load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )

    def _reader(self) -> FileSystemVaultReader:
        return FileSystemVaultReader(self._config().vault_path)

    def _writer(self, config: AppConfig) -> PersonalExperimentNoteWriter:
        return FileSystemVaultWriter(
            config.vault_path,
            operation_lock_path=config.vault_operation_lock_path,
        )

    def _report(self) -> ScanReport:
        report = build_report(self._reader().scan())
        if (
            type(report) is not ScanReport
            or report.manifest is None
            or report.error_count != 0
            or not report.content_scan_complete
        ):
            raise PersonalExperimentEvaluationError(
                "PERSONAL_EXPERIMENT_EVALUATION_SOURCE_UNAVAILABLE"
            )
        return report

    def state(self) -> dict[str, object]:
        report = self._report()
        goals = ProductionGrowthWebService(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
            clock=self.clock,
        ).goals()
        return build_personal_experiments_state(
            report,
            goals.goals,
            as_of=_clock_utc(self.clock),
        )

    def prepare(self, draft: PersonalExperimentWebDraft) -> PersonalExperimentSafeWriteResult:
        config = self._config()
        return PersonalExperimentSafeWrite(
            self._reader(),
            self._writer(config),
            clock=self.clock,
        ).prepare(draft)

    def apply(
        self,
        plan: PersonalExperimentSafeWritePlanV1,
        accepted_plan_sha256: str,
    ) -> PersonalExperimentSafeWriteResult:
        config = self._config()
        return PersonalExperimentSafeWrite(
            self._reader(),
            self._writer(config),
            clock=self.clock,
        ).apply(plan, accepted_plan_sha256)

    def evaluate(
        self,
        request: PersonalExperimentEvaluationRequestV1,
    ) -> PersonalExperimentEvaluationResultV1:
        return evaluate_personal_experiment(self._reader(), request)


def build_production_personal_experiment_web_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> ProductionPersonalExperimentWebService:
    return ProductionPersonalExperimentWebService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


def _owner_binding(request: Request) -> str:
    """Bind an in-memory plan to the authenticated owner and cookie digest."""

    import hashlib

    user_id = request.scope.get(AUTH_USER_ID_SCOPE_KEY)
    owner = user_id if isinstance(user_id, str) and user_id else "auth-disabled"
    cookie_values = [
        value.decode("latin-1")
        for name, value in request.scope.get("headers", [])
        if name.lower() == b"cookie"
    ]
    if len(cookie_values) == 1 and cookie_values[0]:
        raw = cookie_values[0].encode("latin-1")
        if len(raw) <= 8192:
            return f"{owner}:session:{hashlib.sha256(raw).hexdigest()}"
    return f"{owner}:no-session"


def _review_projection(token: str, plan: PersonalExperimentSafeWritePlanV1) -> dict[str, object]:
    """Expose exact semantic review data without raw filesystem topology."""

    return {
        "web_contract": "personal_experiment_review_v1",
        "status": "dry-run",
        "review_token": token,
        "plan_sha256": plan.plan_sha256,
        "record_kind": plan.record_kind.value,
        "record_id": str(plan.note_id),
        "created": plan.created.isoformat().replace("+00:00", "Z"),
        "title": plan.title,
        "payload": plan.record.as_dict(),
        "record_fingerprint": _record_fingerprint(plan.record),
        "content_sha256": plan.content_sha256,
        "bindings": {
            "goal_source_uuid": str(plan.goal_source_uuid),
            "goal_identity_fingerprint": plan.goal_identity_fingerprint,
            "experiment_definition_id": str(plan.experiment_definition_id),
            "experiment_definition_fingerprint": plan.experiment_definition_fingerprint,
            "supersedes_record_id": (
                str(plan.supersedes_record_id) if plan.supersedes_record_id is not None else None
            ),
        },
    }


def _write_error_code(result: PersonalExperimentSafeWriteResult) -> str:
    if result.diagnostics:
        code = result.diagnostics[0].code
        if code in _SAFE_WRITE_MESSAGES:
            return code
    return "PERSONAL_EXPERIMENT_INTERNAL"


def _write_error_response(result: PersonalExperimentSafeWriteResult) -> JSONResponse:
    code = _write_error_code(result)
    if code in {"PERSONAL_EXPERIMENT_RESULT_TOO_LARGE"}:
        status = 413
    elif code in {"PERSONAL_EXPERIMENT_SOURCE_UNAVAILABLE", "PERSONAL_EXPERIMENT_INTERNAL"}:
        status = 503
    elif code in _CONFLICT_CODES:
        status = 409
    else:
        status = 400
    return _error_response(code, _SAFE_WRITE_MESSAGES.get(code, _GENERIC_ERROR_MESSAGE), status)


def _apply_body(result: PersonalExperimentSafeWriteResult) -> dict[str, object]:
    if result.plan is None:
        raise ValueError("apply result has no plan")
    return {
        "web_contract": "personal_experiment_apply_v1",
        "status": "saved" if result.status is CreateStatus.CREATED else "rolled-back",
        "write_status": "created" if result.status is CreateStatus.CREATED else "rolled-back",
        "record_kind": result.plan.record_kind.value,
        "record_id": str(result.plan.note_id),
        "plan_sha256": result.plan.plan_sha256,
        "rollback": (
            "succeeded"
            if result.rollback_succeeded is True
            else "failed"
            if result.rollback_succeeded is False
            else "not-needed"
        ),
    }


def _evaluation_error_response(error: PersonalExperimentEvaluationError) -> JSONResponse:
    status = (
        400
        if error.code.endswith("REQUEST_INVALID")
        else 413
        if error.code.endswith("TOO_LARGE")
        else 503
    )
    return _error_response(error.code, error.message, status)


async def _read_payload(request: Request, payload_type: type[BaseModel]) -> BaseModel:
    raw = await request.body()
    if not raw or len(raw) > MAX_RAW_PERSONAL_EXPERIMENT_BODY_BYTES:
        raise ValueError
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError:
        raise ValueError from None
    return payload_type.model_validate(decoded, strict=True)


def _draft_from_payload(
    payload: BaseModel,
) -> PersonalExperimentWebDraft:
    try:
        if isinstance(payload, PersonalExperimentDefinitionPreparePayload):
            return PersonalExperimentDefinitionDraftV1.from_dict(payload.model_dump(mode="python"))
        if isinstance(payload, PersonalExperimentLifecyclePreparePayload):
            return PersonalExperimentLifecycleDraftV1.from_dict(payload.model_dump(mode="python"))
        if isinstance(payload, PersonalExperimentObservationPreparePayload):
            return PersonalExperimentObservationDraftV1.from_dict(payload.model_dump(mode="python"))
        if isinstance(payload, PersonalExperimentReassessmentPreparePayload):
            return PersonalExperimentReassessmentDraftV1.from_dict(
                payload.model_dump(mode="python")
            )
    except Exception:
        raise ValueError from None
    raise ValueError


def _install_prepare_route(
    app: FastAPI,
    *,
    path: str,
    payload_type: type[BaseModel],
    service: PersonalExperimentWebService,
    review_store: PersonalExperimentReviewStore,
) -> None:
    @app.post(path, include_in_schema=False)
    async def prepare_endpoint(request: Request) -> Response:
        try:
            payload = await _read_payload(request, payload_type)
            draft = _draft_from_payload(payload)
            result = await run_in_threadpool(service.prepare, draft)
            if type(result) is not PersonalExperimentSafeWriteResult:
                return _error_response(
                    "PERSONAL_EXPERIMENT_INTERNAL",
                    _GENERIC_ERROR_MESSAGE,
                    503,
                )
            if result.status is not CreateStatus.DRY_RUN or result.plan is None:
                return _write_error_response(result)
            try:
                token = review_store.issue(_owner_binding(request), result.plan)
            except PersonalExperimentReviewError as error:
                return _error_response(
                    error.code,
                    _SAFE_WRITE_MESSAGES.get(error.code, _GENERIC_ERROR_MESSAGE),
                    503,
                )
            return _json_response(
                _review_projection(token, result.plan),
                max_bytes=MAX_PERSONAL_EXPERIMENT_REVIEW_RESPONSE_BYTES,
            )
        except ValidationError, json.JSONDecodeError, UnicodeError, TypeError, ValueError:
            return _invalid_request()
        except Exception:
            return _error_response(
                "PERSONAL_EXPERIMENT_SOURCE_UNAVAILABLE",
                _SOURCE_UNAVAILABLE_MESSAGE,
                503,
            )


def _install_apply_route(
    app: FastAPI,
    *,
    path: str,
    service: PersonalExperimentWebService,
    review_store: PersonalExperimentReviewStore,
) -> None:
    @app.post(path, include_in_schema=False)
    async def apply_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalExperimentApplyPayload,
                await _read_payload(request, PersonalExperimentApplyPayload),
            )
            if not payload.confirmed:
                return _error_response(
                    "PERSONAL_EXPERIMENT_SAFE_WRITE_REVIEW_REQUIRED",
                    _SAFE_WRITE_MESSAGES["PERSONAL_EXPERIMENT_SAFE_WRITE_REVIEW_REQUIRED"],
                    409,
                )
            plan = review_store.consume(
                _owner_binding(request),
                payload.review_token,
                payload.accepted_plan_sha256,
            )
            result = await run_in_threadpool(
                service.apply,
                plan,
                payload.accepted_plan_sha256,
            )
            if type(result) is not PersonalExperimentSafeWriteResult:
                return _error_response(
                    "PERSONAL_EXPERIMENT_INTERNAL",
                    _GENERIC_ERROR_MESSAGE,
                    503,
                )
            if result.status in {CreateStatus.CREATED, CreateStatus.ROLLED_BACK}:
                return _json_response(
                    _apply_body(result),
                    max_bytes=MAX_PERSONAL_EXPERIMENT_APPLY_RESPONSE_BYTES,
                )
            return _write_error_response(result)
        except PersonalExperimentReviewError as error:
            return _error_response(
                error.code,
                _SAFE_WRITE_MESSAGES.get(error.code, "Review-план недействителен или устарел."),
                409,
            )
        except ValidationError, json.JSONDecodeError, UnicodeError, TypeError, ValueError:
            return _invalid_request()
        except Exception:
            return _error_response(
                "PERSONAL_EXPERIMENT_SOURCE_UNAVAILABLE",
                _SOURCE_UNAVAILABLE_MESSAGE,
                503,
            )


def install_personal_experiment_routes(
    app: FastAPI,
    *,
    service: PersonalExperimentWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install Stage 14 read, review/apply, lifecycle and evaluation routes."""

    actual_service = service or build_production_personal_experiment_web_service(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    review_store = PersonalExperimentReviewStore()
    app.add_middleware(PersonalExperimentRequestBoundaryMiddleware)
    app.state.personal_experiment_review_store = review_store

    @app.post(PERSONAL_EXPERIMENTS_PATH, include_in_schema=False)
    async def personal_experiments_endpoint(request: Request) -> Response:
        try:
            await _read_payload(request, PersonalExperimentEmptyPayload)
            body = await run_in_threadpool(actual_service.state)
            return _json_response(body, max_bytes=MAX_PERSONAL_EXPERIMENT_STATE_RESPONSE_BYTES)
        except ValidationError, json.JSONDecodeError, UnicodeError, TypeError, ValueError:
            return _invalid_request()
        except Exception:
            return _error_response(
                "PERSONAL_EXPERIMENT_SOURCE_UNAVAILABLE",
                _SOURCE_UNAVAILABLE_MESSAGE,
                503,
            )

    @app.post(PERSONAL_EXPERIMENT_EVALUATE_PATH, include_in_schema=False)
    async def personal_experiment_evaluate_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalExperimentEvaluationPayload,
                await _read_payload(request, PersonalExperimentEvaluationPayload),
            )
            typed_request = PersonalExperimentEvaluationRequestV1.from_dict(
                payload.model_dump(mode="python")
            )
            result = await run_in_threadpool(actual_service.evaluate, typed_request)
            if type(result) is not PersonalExperimentEvaluationResultV1:
                return _error_response(
                    "PERSONAL_EXPERIMENT_EVALUATION_INTERNAL",
                    _GENERIC_ERROR_MESSAGE,
                    503,
                )
            return _json_response(
                {"web_contract": "personal_experiment_evaluation_web_v1", **result.as_dict()},
                max_bytes=MAX_PERSONAL_EXPERIMENT_RESULT_BYTES,
            )
        except PersonalExperimentEvaluationError as error:
            return _evaluation_error_response(error)
        except ValidationError, json.JSONDecodeError, UnicodeError, TypeError, ValueError:
            return _invalid_request()
        except Exception:
            return _error_response(
                "PERSONAL_EXPERIMENT_EVALUATION_SOURCE_UNAVAILABLE",
                _SOURCE_UNAVAILABLE_MESSAGE,
                503,
            )

    _install_prepare_route(
        app,
        path=PERSONAL_EXPERIMENT_DEFINITION_PREPARE_PATH,
        payload_type=PersonalExperimentDefinitionPreparePayload,
        service=actual_service,
        review_store=review_store,
    )
    _install_prepare_route(
        app,
        path=PERSONAL_EXPERIMENT_LIFECYCLE_PREPARE_PATH,
        payload_type=PersonalExperimentLifecyclePreparePayload,
        service=actual_service,
        review_store=review_store,
    )
    _install_prepare_route(
        app,
        path=PERSONAL_EXPERIMENT_OBSERVATION_PREPARE_PATH,
        payload_type=PersonalExperimentObservationPreparePayload,
        service=actual_service,
        review_store=review_store,
    )
    _install_prepare_route(
        app,
        path=PERSONAL_EXPERIMENT_REASSESSMENT_PREPARE_PATH,
        payload_type=PersonalExperimentReassessmentPreparePayload,
        service=actual_service,
        review_store=review_store,
    )
    for path in (
        PERSONAL_EXPERIMENT_DEFINITION_APPLY_PATH,
        PERSONAL_EXPERIMENT_LIFECYCLE_APPLY_PATH,
        PERSONAL_EXPERIMENT_OBSERVATION_APPLY_PATH,
        PERSONAL_EXPERIMENT_REASSESSMENT_APPLY_PATH,
    ):
        _install_apply_route(app, path=path, service=actual_service, review_store=review_store)


__all__ = [
    "MAX_PERSONAL_EXPERIMENT_APPLY_RESPONSE_BYTES",
    "MAX_PERSONAL_EXPERIMENT_REVIEW_RESPONSE_BYTES",
    "MAX_PERSONAL_EXPERIMENT_STATE_RESPONSE_BYTES",
    "MAX_RAW_PERSONAL_EXPERIMENT_BODY_BYTES",
    "PERSONAL_EXPERIMENTS_PATH",
    "PERSONAL_EXPERIMENT_DEFINITION_APPLY_PATH",
    "PERSONAL_EXPERIMENT_DEFINITION_PREPARE_PATH",
    "PERSONAL_EXPERIMENT_EVALUATE_PATH",
    "PERSONAL_EXPERIMENT_LIFECYCLE_APPLY_PATH",
    "PERSONAL_EXPERIMENT_LIFECYCLE_PREPARE_PATH",
    "PERSONAL_EXPERIMENT_OBSERVATION_APPLY_PATH",
    "PERSONAL_EXPERIMENT_OBSERVATION_PREPARE_PATH",
    "PERSONAL_EXPERIMENT_REASSESSMENT_APPLY_PATH",
    "PERSONAL_EXPERIMENT_REASSESSMENT_PREPARE_PATH",
    "PERSONAL_EXPERIMENT_REQUEST_HEADER_NAME",
    "PERSONAL_EXPERIMENT_REQUEST_HEADER_VALUE",
    "PersonalExperimentApplyPayload",
    "PersonalExperimentDefinitionPreparePayload",
    "PersonalExperimentEmptyPayload",
    "PersonalExperimentEvaluationPayload",
    "PersonalExperimentLifecyclePreparePayload",
    "PersonalExperimentObservationPreparePayload",
    "PersonalExperimentReassessmentPreparePayload",
    "PersonalExperimentRequestBoundaryMiddleware",
    "PersonalExperimentWebService",
    "ProductionPersonalExperimentWebService",
    "build_personal_experiments_state",
    "build_production_personal_experiment_web_service",
    "install_personal_experiment_routes",
]
