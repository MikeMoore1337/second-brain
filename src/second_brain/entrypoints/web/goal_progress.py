"""Private owner-facing Web/API boundary for Stage 12 Goal Progress.

The Stage 12 application modules own Goal identity, definitions, comparison,
lineage and Safe Write semantics.  This module is intentionally a thin
transport/presentation adapter: it validates the browser envelope, resolves
server-owned Goal context, delegates to those application services and emits
bounded owner-safe projections.  Review plans live only in a short-lived
process-local store and are never persisted or logged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol, cast
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.adapters.vault import FileSystemVaultReader, FileSystemVaultWriter
from second_brain.application.goal_progress import (
    DefinitionRecordV1,
    GoalProgressValidationError,
    MilestoneObservationV1,
    MilestoneSetDefinitionV1,
    MilestoneStateV1,
    MilestoneV1,
    NumericDirectionV1,
    NumericObservationV1,
    NumericTargetDefinitionV1,
    ProgressModelV1,
    SupersessionChainStateV1,
    validate_definition_chain,
)
from second_brain.application.goal_progress_read import (
    BuildGoalProgress,
    GoalProgressError,
    GoalProgressErrorCode,
    GoalProgressRequestV1,
    GoalProgressResultV1,
    validate_goal_progress_request,
    validate_goal_progress_result,
)
from second_brain.application.goal_progress_safe_write import (
    GoalProgressDefinitionDraftV1,
    GoalProgressObservationDraftV1,
    GoalProgressSafeWrite,
    GoalProgressSafeWritePlanV1,
    GoalProgressSafeWriteResult,
)
from second_brain.application.growth import create_growth_engine
from second_brain.application.growth_goal_progress_composition import (
    BuildGrowthGoalProgressCompositionV1,
    GrowthGoalProgressCompositionError,
    GrowthGoalProgressCompositionErrorCodeV1,
    GrowthGoalProgressCompositionRequestV1,
    GrowthGoalProgressCompositionResultV1,
    validate_growth_goal_progress_composition_request,
    validate_growth_goal_progress_composition_result,
)
from second_brain.application.reports import ScanReport
from second_brain.application.self_model import DEFAULT_SELF_MODEL_POLICY, SelfModelError
from second_brain.application.validation import build_report
from second_brain.application.writes import CreateStatus
from second_brain.config import AppConfig, ConfigurationError, load_config
from second_brain.domain.models import parse_uuid7
from second_brain.entrypoints.web.auth import (
    AUTH_USER_ID_SCOPE_KEY,
    request_host_is_trusted,
    same_origin_is_trusted,
    trusted_authorities_from_scope,
)
from second_brain.entrypoints.web.growth import (
    GrowthGoalOwnerItemV1,
    GrowthGoalsProjectionV1,
    ProductionGrowthWebService,
)

GOAL_PROGRESS_PATH: Final[str] = "/api/goal-progress"
GROWTH_GOAL_PROGRESS_PATH: Final[str] = "/api/growth-goal-progress"
GOAL_PROGRESS_DEFINITION_PREPARE_PATH: Final[str] = "/api/goal-progress/definitions/prepare"
GOAL_PROGRESS_DEFINITION_APPLY_PATH: Final[str] = "/api/goal-progress/definitions/apply"
GOAL_PROGRESS_OBSERVATION_PREPARE_PATH: Final[str] = "/api/goal-progress/observations/prepare"
GOAL_PROGRESS_OBSERVATION_APPLY_PATH: Final[str] = "/api/goal-progress/observations/apply"

GOAL_PROGRESS_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
GOAL_PROGRESS_REQUEST_HEADER_VALUE: Final[str] = "goal-progress-v1"
GROWTH_GOAL_PROGRESS_REQUEST_HEADER_VALUE: Final[str] = "growth-goal-progress-composition-v1"

MAX_RAW_GOAL_PROGRESS_BODY_BYTES: Final[int] = 256 * 1024
MAX_GOAL_PROGRESS_READ_RESPONSE_BYTES: Final[int] = 96 * 1024
MAX_GROWTH_GOAL_PROGRESS_RESPONSE_BYTES: Final[int] = 256 * 1024
MAX_GOAL_PROGRESS_REVIEW_RESPONSE_BYTES: Final[int] = 256 * 1024
MAX_GOAL_PROGRESS_APPLY_RESPONSE_BYTES: Final[int] = 32 * 1024
MAX_GOAL_PROGRESS_REVIEW_PLAN_BYTES: Final[int] = 256 * 1024
MAX_GOAL_PROGRESS_REVIEW_ENTRIES: Final[int] = 64
GOAL_PROGRESS_REVIEW_TTL_SECONDS: Final[float] = 10 * 60

_GOAL_PROGRESS_PATHS: Final[frozenset[str]] = frozenset(
    {
        GOAL_PROGRESS_PATH,
        GROWTH_GOAL_PROGRESS_PATH,
        GOAL_PROGRESS_DEFINITION_PREPARE_PATH,
        GOAL_PROGRESS_DEFINITION_APPLY_PATH,
        GOAL_PROGRESS_OBSERVATION_PREPARE_PATH,
        GOAL_PROGRESS_OBSERVATION_APPLY_PATH,
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


class _MilestonePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: StrictStr
    label: StrictStr
    ordinal: StrictInt


class GoalProgressReadPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    goal_source_uuid: StrictStr
    as_of: StrictStr


class GrowthGoalProgressPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    goal_source_uuid: StrictStr
    progress_as_of: StrictStr


class GoalProgressDefinitionPreparePayload(BaseModel):
    """Owner-editable definition semantics; all identity metadata is server-owned."""

    model_config = ConfigDict(extra="forbid", strict=True)

    goal_source_uuid: StrictStr
    progress_model: StrictStr
    metric_id: StrictStr | None = None
    unit: StrictStr | None = None
    baseline: StrictStr | None = None
    target: StrictStr | None = None
    direction: StrictStr | None = None
    lower_bound: StrictStr | None = None
    upper_bound: StrictStr | None = None
    milestones: list[_MilestonePayload] | None = None
    supersedes_definition_id: StrictStr | None = None


class GoalProgressObservationPreparePayload(BaseModel):
    """Owner inputs for one observation; model and definition binding are derived."""

    model_config = ConfigDict(extra="forbid", strict=True)

    goal_source_uuid: StrictStr
    progress_definition_id: StrictStr
    value: StrictStr | None = None
    milestone_id: StrictStr | None = None
    state: StrictStr | None = None
    observed_at: StrictStr
    observed_at_precision: StrictStr
    supersedes_observation_id: StrictStr | None = None


class GoalProgressApplyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    review_token: StrictStr
    accepted_plan_sha256: StrictStr
    confirmed: StrictBool


class GoalProgressWebService(Protocol):
    """Small seam over existing Stage 12 read and Safe Write application services."""

    def read(self, request: GoalProgressRequestV1) -> GoalProgressResultV1:
        """Build one current Goal Progress result."""
        ...

    def compose(
        self,
        request: GrowthGoalProgressCompositionRequestV1,
    ) -> GrowthGoalProgressCompositionResultV1:
        """Build the additive Growth and Goal Progress composition."""
        ...

    def goals(self) -> GrowthGoalsProjectionV1:
        """Resolve current server-owned Goal projections."""
        ...

    def goal_projection(self, goal_source_uuid: UUID) -> GrowthGoalOwnerItemV1:
        """Resolve one exact current Goal and its owner-facing text."""
        ...

    def active_definition(
        self,
        goal_source_uuid: UUID,
        goal_identity_fingerprint: str,
        definition_id: UUID,
    ) -> DefinitionRecordV1:
        """Resolve and revalidate the one current active definition."""
        ...

    def definition_projection(self, result: GoalProgressResultV1) -> dict[str, object] | None:
        """Return a structured current definition without storage metadata."""
        ...

    def observation_projection(self, result: GoalProgressResultV1) -> list[dict[str, object]]:
        """Return structured current observations for correction review."""
        ...

    def prepare_definition(
        self,
        draft: GoalProgressDefinitionDraftV1,
    ) -> GoalProgressSafeWriteResult:
        """Prepare one exact definition plan without publishing it."""
        ...

    def prepare_observation(
        self,
        draft: GoalProgressObservationDraftV1,
    ) -> GoalProgressSafeWriteResult:
        """Prepare one exact observation plan without publishing it."""
        ...

    def apply(
        self,
        plan: GoalProgressSafeWritePlanV1,
        accepted_plan_sha256: str,
    ) -> GoalProgressSafeWriteResult:
        """Apply one exact reviewed plan through Stage 12B Safe Write."""
        ...

    def now(self) -> datetime:
        """Return the server-owned review timestamp."""
        ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class ProductionGoalProgressWebService:
    """Lazily construct the existing vault/application services."""

    env_file: Path | None = None
    vault_path_override: str | None = None
    clock: Callable[[], datetime] = _utc_now

    def _config(self) -> AppConfig:
        return load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )

    def _reader(self) -> FileSystemVaultReader:
        return FileSystemVaultReader(self._config().vault_path)

    def _writer(self, config: AppConfig) -> FileSystemVaultWriter:
        return FileSystemVaultWriter(
            config.vault_path,
            operation_lock_path=config.vault_operation_lock_path,
        )

    def _growth(self) -> ProductionGrowthWebService:
        return ProductionGrowthWebService(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
            clock=self.clock,
        )

    def _report(self) -> ScanReport:
        return build_report(self._reader().scan())

    def now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise GoalProgressError(GoalProgressErrorCode.REQUEST_INVALID)
        return value.astimezone(UTC)

    def goals(self) -> GrowthGoalsProjectionV1:
        return self._growth().goals()

    def goal_projection(self, goal_source_uuid: UUID) -> GrowthGoalOwnerItemV1:
        matches = tuple(
            item for item in self.goals().goals if item.goal.source_note_uuid == goal_source_uuid
        )
        if len(matches) != 1:
            raise GoalProgressError(GoalProgressErrorCode.GOAL_REQUIRED)
        return matches[0]

    def read(self, request: GoalProgressRequestV1) -> GoalProgressResultV1:
        result = BuildGoalProgress(
            self._reader(),
        ).execute(request)
        validate_goal_progress_result(result)
        return result

    def compose(
        self,
        request: GrowthGoalProgressCompositionRequestV1,
    ) -> GrowthGoalProgressCompositionResultV1:
        config = self._config()
        if config.env_file is None:
            raise ConfigurationError("composition requires the configured environment file")
        repository_root = Path(__file__).resolve().parents[4]
        env_file = config.env_file
        assert env_file is not None
        # Reuse the same Growth engine factory as the existing Growth Web
        # service; the composition builder remains the sole semantic owner.
        growth_engine = create_growth_engine(
            self._reader(),
            env_file,
            policy=DEFAULT_SELF_MODEL_POLICY,
            clock=self.clock,
            vault_root=config.vault_path,
            repository_root=repository_root,
        )
        result = BuildGrowthGoalProgressCompositionV1(
            reader=self._reader(),
            store=growth_engine.store,
        ).execute(request)
        validate_growth_goal_progress_composition_result(result)
        return result

    def active_definition(
        self,
        goal_source_uuid: UUID,
        goal_identity_fingerprint: str,
        definition_id: UUID,
    ) -> DefinitionRecordV1:
        report = self._report()
        if report.error_count:
            raise GoalProgressError(GoalProgressErrorCode.SOURCE_UNAVAILABLE)
        matches = tuple(
            item
            for item in report.goal_progress_definitions
            if item.goal_source_uuid == goal_source_uuid
            and item.goal_identity_fingerprint == goal_identity_fingerprint
        )
        chain = validate_definition_chain(matches) if matches else None
        if (
            chain is None
            or chain.issues
            or chain.state is not SupersessionChainStateV1.ONE_ACTIVE
            or len(chain.active_records) != 1
            or type(chain.active_records[0]) is not DefinitionRecordV1
        ):
            raise GoalProgressError(GoalProgressErrorCode.DEFINITION_CONFLICT)
        active = chain.active_records[0]
        if active.id != definition_id:
            raise GoalProgressError(GoalProgressErrorCode.DEFINITION_STALE)
        return active

    def definition_projection(self, result: GoalProgressResultV1) -> dict[str, object] | None:
        definition_id = result.active_definition_uuid
        if definition_id is None or result.current_goal_identity_fingerprint is None:
            return None
        report = self._report()
        matches = tuple(
            item
            for item in report.goal_progress_definitions
            if item.id == definition_id
            and item.goal_source_uuid == result.selected_goal_source_uuid
            and item.goal_identity_fingerprint == result.current_goal_identity_fingerprint
        )
        if len(matches) != 1:
            raise GoalProgressError(GoalProgressErrorCode.GOAL_SOURCE_CHANGED)
        return matches[0].as_dict()

    def observation_projection(self, result: GoalProgressResultV1) -> list[dict[str, object]]:
        if not result.current_observation_uuids:
            return []
        report = self._report()
        by_id = {
            item.id: item
            for item in report.goal_progress_observations
            if item.goal_source_uuid == result.selected_goal_source_uuid
            and item.goal_identity_fingerprint == result.current_goal_identity_fingerprint
            and item.definition_fingerprint == result.definition_fingerprint
        }
        if any(item_id not in by_id for item_id in result.current_observation_uuids):
            raise GoalProgressError(GoalProgressErrorCode.GOAL_SOURCE_CHANGED)
        return [by_id[item_id].as_dict() for item_id in result.current_observation_uuids]

    def prepare_definition(
        self,
        draft: GoalProgressDefinitionDraftV1,
    ) -> GoalProgressSafeWriteResult:
        config = self._config()
        return GoalProgressSafeWrite(
            self._reader(),
            self._writer(config),
            clock=self.clock,
        ).prepare_definition(draft)

    def prepare_observation(
        self,
        draft: GoalProgressObservationDraftV1,
    ) -> GoalProgressSafeWriteResult:
        config = self._config()
        return GoalProgressSafeWrite(
            self._reader(),
            self._writer(config),
            clock=self.clock,
        ).prepare_observation(draft)

    def apply(
        self,
        plan: GoalProgressSafeWritePlanV1,
        accepted_plan_sha256: str,
    ) -> GoalProgressSafeWriteResult:
        config = self._config()
        return GoalProgressSafeWrite(
            self._reader(),
            self._writer(config),
            clock=self.clock,
        ).apply(plan, accepted_plan_sha256)


def build_production_goal_progress_web_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> ProductionGoalProgressWebService:
    return ProductionGoalProgressWebService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


@dataclass(frozen=True, slots=True)
class _ReviewEntry:
    owner_key: str
    plan: GoalProgressSafeWritePlanV1
    expires_at: float


class _ReviewRequiredError(RuntimeError):
    """The one-time owner review is absent, expired or bound to another session."""


class GoalProgressReviewStore:
    """Bounded process-local review plans bound to one authenticated session."""

    def __init__(
        self,
        *,
        ttl_seconds: float = GOAL_PROGRESS_REVIEW_TTL_SECONDS,
        max_entries: int = MAX_GOAL_PROGRESS_REVIEW_ENTRIES,
        max_plan_bytes: int = MAX_GOAL_PROGRESS_REVIEW_PLAN_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not 1 <= ttl_seconds <= 3600
            or not 1 <= max_entries <= 1024
            or not 1 <= max_plan_bytes <= MAX_GOAL_PROGRESS_REVIEW_PLAN_BYTES
        ):
            raise ValueError("review store bounds are invalid")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._max_plan_bytes = max_plan_bytes
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, _ReviewEntry] = {}

    def _prune(self, now: float) -> None:
        expired = [token for token, entry in self._entries.items() if entry.expires_at <= now]
        for token in expired:
            self._entries.pop(token, None)

    def issue(self, owner_key: str, plan: GoalProgressSafeWritePlanV1) -> str:
        if type(owner_key) is not str or not owner_key:
            raise ValueError("review owner is invalid")
        if type(plan) is not GoalProgressSafeWritePlanV1:
            raise ValueError("review plan is invalid")
        try:
            plan_bytes = len(plan.content.encode("utf-8"))
        except UnicodeError:
            raise GoalProgressError(GoalProgressErrorCode.INTERNAL) from None
        if plan_bytes > self._max_plan_bytes:
            raise GoalProgressError(GoalProgressErrorCode.RESULT_TOO_LARGE)
        with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._entries) >= self._max_entries:
                raise GoalProgressError(GoalProgressErrorCode.INTERNAL)
            for _ in range(3):
                token = secrets.token_urlsafe(32)
                if token not in self._entries:
                    self._entries[token] = _ReviewEntry(
                        owner_key=owner_key,
                        plan=plan,
                        expires_at=now + self._ttl_seconds,
                    )
                    return token
        raise GoalProgressError(GoalProgressErrorCode.INTERNAL)

    def consume(
        self,
        owner_key: str,
        token: str,
        accepted_plan_sha256: str,
    ) -> GoalProgressSafeWritePlanV1:
        if type(token) is not str or type(accepted_plan_sha256) is not str:
            raise _ReviewRequiredError
        with self._lock:
            now = self._clock()
            self._prune(now)
            entry = self._entries.get(token)
            if entry is None or not hmac.compare_digest(entry.owner_key, owner_key):
                raise _ReviewRequiredError
            if not hmac.compare_digest(entry.plan.plan_sha256, accepted_plan_sha256):
                raise _ReviewRequiredError
            self._entries.pop(token, None)
            return entry.plan


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
    if not raw or len(raw) > MAX_RAW_GOAL_PROGRESS_BODY_BYTES:
        raise ValueError
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError:
        raise ValueError from None


def _uuid7_text(value: str) -> UUID:
    try:
        parsed = parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        raise ValueError from None
    if type(parsed) is not UUID or parsed.version != 7 or str(parsed) != value:
        raise ValueError
    return parsed


def _safe_error(code: str, message: str, status_code: int) -> Response:
    return _json_response(
        {"error": {"code": code, "message": message}},
        max_bytes=MAX_GOAL_PROGRESS_APPLY_RESPONSE_BYTES,
        status_code=status_code,
    )


_GOAL_PROGRESS_MESSAGES: Final[dict[str, tuple[int, str]]] = {
    GoalProgressErrorCode.REQUEST_INVALID.value: (
        400,
        "Запрос прогресса не прошёл проверку.",
    ),
    GoalProgressErrorCode.GOAL_REQUIRED.value: (404, "Выбранная цель больше недоступна."),
    GoalProgressErrorCode.GOAL_AMBIGUOUS.value: (409, "Текущая цель неоднозначна."),
    GoalProgressErrorCode.GOAL_SOURCE_CHANGED.value: (
        409,
        "Источник цели изменился; требуется новая проверка.",
    ),
    GoalProgressErrorCode.DEFINITION_MISSING.value: (
        200,
        "Для этой цели пока не определено, как измерять прогресс.",
    ),
    GoalProgressErrorCode.DEFINITION_INVALID.value: (
        400,
        "Правило измерения не прошло проверку.",
    ),
    GoalProgressErrorCode.DEFINITION_STALE.value: (
        409,
        "Правило измерения устарело; требуется новая проверка.",
    ),
    GoalProgressErrorCode.DEFINITION_CONFLICT.value: (
        409,
        "Текущие правила измерения конфликтуют.",
    ),
    GoalProgressErrorCode.OBSERVATION_INVALID.value: (
        400,
        "Запись наблюдения не соответствует правилу измерения.",
    ),
    GoalProgressErrorCode.OBSERVATION_MISSING.value: (
        409,
        "Исправляемая запись больше недоступна.",
    ),
    GoalProgressErrorCode.OBSERVATION_SOURCE_CHANGED.value: (
        409,
        "Источник наблюдения изменился; требуется новая проверка.",
    ),
    GoalProgressErrorCode.UNIT_INCOMPATIBLE.value: (
        400,
        "Единица измерения не соответствует правилу.",
    ),
    GoalProgressErrorCode.MODEL_UNSUPPORTED.value: (
        400,
        "Выбранная модель измерения не поддерживается.",
    ),
    GoalProgressErrorCode.POLICY_MISMATCH.value: (
        500,
        "Политика измерения сейчас недоступна.",
    ),
    GoalProgressErrorCode.NOT_COMPARABLE.value: (
        200,
        "Текущие записи пока нельзя сопоставить.",
    ),
    GoalProgressErrorCode.RESULT_TOO_LARGE.value: (
        413,
        "Результат прогресса слишком велик.",
    ),
    GoalProgressErrorCode.SOURCE_UNAVAILABLE.value: (
        503,
        "Текущий источник прогресса недоступен.",
    ),
    GoalProgressErrorCode.COMPARISON_UNSUPPORTED.value: (
        409,
        "Это сопоставление не поддерживается текущей политикой.",
    ),
    GoalProgressErrorCode.INTERNAL.value: (
        503,
        "Прогресс сейчас недоступен.",
    ),
}

_COMPOSITION_MESSAGES: Final[dict[str, tuple[int, str]]] = {
    GrowthGoalProgressCompositionErrorCodeV1.INVALID_REQUEST.value: (
        400,
        "Запрос связи и прогресса не прошёл проверку.",
    ),
    GrowthGoalProgressCompositionErrorCodeV1.GOAL_BINDING_MISMATCH.value: (
        409,
        "Связь с текущей целью не подтверждена.",
    ),
    GrowthGoalProgressCompositionErrorCodeV1.SOURCE_CHANGED.value: (
        409,
        "Источник цели изменился; требуется новая проверка.",
    ),
    GrowthGoalProgressCompositionErrorCodeV1.SOURCE_UNAVAILABLE.value: (
        503,
        "Источники связи и прогресса недоступны.",
    ),
    GrowthGoalProgressCompositionErrorCodeV1.POLICY_MISMATCH.value: (
        500,
        "Политика связи и прогресса сейчас недоступна.",
    ),
    GrowthGoalProgressCompositionErrorCodeV1.RESULT_TOO_LARGE.value: (
        413,
        "Результат связи и прогресса слишком велик.",
    ),
    GrowthGoalProgressCompositionErrorCodeV1.INTERNAL.value: (
        503,
        "Связь и измеряемый прогресс сейчас недоступны.",
    ),
}

_SAFE_WRITE_MESSAGES: Final[dict[str, tuple[int, str]]] = {
    "GOAL_PROGRESS_REQUEST_INVALID": (400, "Проверенные данные не соответствуют контракту."),
    "GOAL_PROGRESS_GOAL_REQUIRED": (404, "Текущая цель не найдена."),
    "GOAL_PROGRESS_GOAL_AMBIGUOUS": (409, "Текущая цель неоднозначна."),
    "GOAL_PROGRESS_GOAL_SOURCE_CHANGED": (
        409,
        "Источник цели изменился; запись остановлена.",
    ),
    "GOAL_PROGRESS_SOURCE_UNAVAILABLE": (503, "Текущий источник записи недоступен."),
    "GOAL_PROGRESS_POLICY_MISMATCH": (500, "Политика записи сейчас недоступна."),
    "GOAL_PROGRESS_DEFINITION_INVALID": (400, "Правило измерения не прошло проверку."),
    "GOAL_PROGRESS_DEFINITION_MISSING": (409, "Сначала настройте правило измерения."),
    "GOAL_PROGRESS_DEFINITION_STALE": (
        409,
        "Правило измерения устарело; требуется новая проверка.",
    ),
    "GOAL_PROGRESS_DEFINITION_CONFLICT": (409, "Правила измерения конфликтуют."),
    "GOAL_PROGRESS_MODEL_UNSUPPORTED": (400, "Модель измерения не поддерживается."),
    "GOAL_PROGRESS_OBSERVATION_INVALID": (
        400,
        "Запись наблюдения не соответствует правилу измерения.",
    ),
    "GOAL_PROGRESS_OBSERVATION_MISSING": (409, "Исправляемая запись больше недоступна."),
    "GOAL_PROGRESS_OBSERVATION_CONFLICT": (409, "История наблюдений конфликтует."),
    "GOAL_PROGRESS_SAFE_WRITE_REVIEW_REQUIRED": (
        409,
        "Требуется подтвердить точный просмотренный план.",
    ),
    "GOAL_PROGRESS_VAULT_CHANGED": (
        409,
        "Vault изменился после проверки; запись не выполнена.",
    ),
    "GOAL_PROGRESS_SAFE_WRITE_VALIDATION_FAILED": (
        503,
        "После записи проверка не пройдена; выполнен rollback.",
    ),
    "GOAL_PROGRESS_ROLLBACK_FAILED": (
        503,
        "Rollback записи не подтверждён; требуется ручная проверка.",
    ),
    "CREATE_TARGET_EXISTS": (409, "Целевой файл уже существует; данные не изменены."),
    "CREATE_LINKED_PATH": (409, "Целевой путь защищён; запись остановлена."),
    "CREATE_PATH_ESCAPE": (409, "Целевой путь не прошёл проверку."),
    "CREATE_ROOT_MISSING": (503, "Каталог записи недоступен."),
    "CREATE_TEMPLATE_MISSING": (503, "Шаблон записи недоступен."),
    "CREATE_TEMPLATE_INVALID": (503, "Шаблон записи не прошёл проверку."),
    "CREATE_VAULT_OPERATION_BUSY": (409, "Другая операция записи уже выполняется."),
    "CREATE_VAULT_LOCK_FAILED": (503, "Lock операции записи недоступен."),
    "CREATE_WRITE_FAILED": (503, "Файл не удалось безопасно опубликовать."),
    "CREATE_INVALID_PLAN": (400, "План записи некорректен."),
    "GOAL_PROGRESS_INTERNAL": (503, "Запись остановлена внутренней защитой."),
}


def _json_response(
    body: dict[str, object],
    *,
    max_bytes: int,
    status_code: int = 200,
) -> Response:
    try:
        encoded = json.dumps(
            body,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except TypeError, ValueError, OverflowError, UnicodeError:
        return _safe_error(
            GoalProgressErrorCode.INTERNAL.value,
            "Результат прогресса сейчас недоступен.",
            503,
        )
    if len(encoded) > max_bytes:
        return _safe_error(
            GoalProgressErrorCode.RESULT_TOO_LARGE.value,
            "Результат прогресса слишком велик.",
            413,
        )
    return Response(
        content=encoded,
        status_code=status_code,
        media_type="application/json",
        headers=_API_HEADERS,
    )


def _method_not_allowed() -> Response:
    response = _safe_error(
        GoalProgressErrorCode.REQUEST_INVALID.value,
        "Запрос прогресса не прошёл проверку.",
        405,
    )
    response.headers["Allow"] = "POST"
    return response


async def _send(response: Response, scope: Scope, receive: Receive, send: Send) -> None:
    await response(scope, receive, send)


class GoalProgressRequestBoundaryMiddleware:
    """Require trusted Host, optional same-origin Origin and exact JSON purpose."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _GOAL_PROGRESS_PATHS:
            await self.app(scope, receive, send)
            return
        if scope.get("method") != "POST":
            await _send(_method_not_allowed(), scope, receive, send)
            return
        header_names = [name.lower() for name, _ in scope.get("headers", [])]
        if any(header_names.count(name) > 1 for name in _SECURITY_HEADER_NAMES):
            await _send(
                _safe_error(
                    GoalProgressErrorCode.REQUEST_INVALID.value,
                    "Запрос прогресса не прошёл проверку.",
                    400,
                ),
                scope,
                receive,
                send,
            )
            return
        host_present, _host = _header(scope, b"host")
        origin_present, origin = _header(scope, b"origin")
        _, purpose = _header(scope, b"x-second-brain-request")
        _, content_type = _header(scope, b"content-type")
        authorities = trusted_authorities_from_scope(scope)
        expected_purpose = (
            GROWTH_GOAL_PROGRESS_REQUEST_HEADER_VALUE
            if scope.get("path") == GROWTH_GOAL_PROGRESS_PATH
            else GOAL_PROGRESS_REQUEST_HEADER_VALUE
        )
        if (
            not host_present
            or not request_host_is_trusted(scope, authorities)
            or (
                origin_present
                and (origin is None or not same_origin_is_trusted(scope, origin, authorities))
            )
            or purpose != expected_purpose
            or not _json_content_type(content_type)
        ):
            await _send(
                _safe_error(
                    GoalProgressErrorCode.REQUEST_INVALID.value,
                    "Запрос прогресса не прошёл проверку.",
                    400,
                ),
                scope,
                receive,
                send,
            )
            return
        _, content_length = _header(scope, b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length.strip())
            except ValueError:
                await _send(
                    _safe_error(
                        GoalProgressErrorCode.REQUEST_INVALID.value,
                        "Запрос прогресса не прошёл проверку.",
                        400,
                    ),
                    scope,
                    receive,
                    send,
                )
                return
            if declared < 0:
                await _send(
                    _safe_error(
                        GoalProgressErrorCode.REQUEST_INVALID.value,
                        "Запрос прогресса не прошёл проверку.",
                        400,
                    ),
                    scope,
                    receive,
                    send,
                )
                return
            if declared > MAX_RAW_GOAL_PROGRESS_BODY_BYTES:
                await _send(
                    _safe_error(
                        GoalProgressErrorCode.RESULT_TOO_LARGE.value,
                        "Запрос прогресса слишком велик.",
                        413,
                    ),
                    scope,
                    receive,
                    send,
                )
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                await _send(_method_not_allowed(), scope, receive, send)
                return
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                await _send(
                    _safe_error(
                        GoalProgressErrorCode.RESULT_TOO_LARGE.value,
                        "Запрос прогресса слишком велик.",
                        413,
                    ),
                    scope,
                    receive,
                    send,
                )
                return
            body.extend(chunk)
            if len(body) > MAX_RAW_GOAL_PROGRESS_BODY_BYTES:
                await _send(
                    _safe_error(
                        GoalProgressErrorCode.RESULT_TOO_LARGE.value,
                        "Запрос прогресса слишком велик.",
                        413,
                    ),
                    scope,
                    receive,
                    send,
                )
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


def _owner_binding(request: Request) -> str:
    """Bind a review to the authenticated owner and a digest of the session cookie."""

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


def _goal_body(item: GrowthGoalOwnerItemV1) -> dict[str, object]:
    goal = item.goal
    return {
        "source_note_uuid": str(goal.source_note_uuid),
        "text": item.goal_text,
        "domain": goal.domain,
        "identity_fingerprint": item.goal_identity_fingerprint,
    }


def _owner_result_body(
    service: GoalProgressWebService,
    result: GoalProgressResultV1,
) -> dict[str, object]:
    goal = service.goal_projection(cast(UUID, result.selected_goal_source_uuid))
    return {
        **result.as_dict(),
        "web_contract": "goal_progress_web_v1",
        "goal": _goal_body(goal),
        "definition": service.definition_projection(result),
        "observations": service.observation_projection(result),
    }


def _owner_composition_body(
    service: GoalProgressWebService,
    result: GrowthGoalProgressCompositionResultV1,
) -> dict[str, object]:
    goal = service.goal_projection(cast(UUID, result.selected_goal_source_uuid))
    progress = result.goal_progress_result
    return {
        **result.as_dict(),
        "web_contract": "growth_goal_progress_composition_web_v1",
        "goal": _goal_body(goal),
        "definition": service.definition_projection(progress),
        "observations": service.observation_projection(progress),
    }


def _review_projection(
    token: str,
    plan: GoalProgressSafeWritePlanV1,
    goal: GrowthGoalOwnerItemV1,
) -> dict[str, object]:
    record = plan.record
    payload = record.as_dict()
    return {
        "web_contract": "goal_progress_review_v1",
        "status": "dry-run",
        "review_token": token,
        "plan_sha256": plan.plan_sha256,
        "record_kind": plan.record_kind.value,
        "record_id": str(plan.note_id),
        "goal": _goal_body(goal),
        "record": payload,
        "write": {
            "operation": plan.operation,
            "created": plan.created.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "content_sha256": plan.content_sha256,
            "record_kind": ("definition" if type(record) is DefinitionRecordV1 else "observation"),
        },
    }


def _diagnostic_code(result: GoalProgressSafeWriteResult) -> str:
    if result.diagnostics:
        code = result.diagnostics[0].code
        if code in _SAFE_WRITE_MESSAGES:
            return code
    return "GOAL_PROGRESS_INTERNAL"


def _apply_body(result: GoalProgressSafeWriteResult) -> dict[str, object]:
    code = _diagnostic_code(result)
    if result.status is CreateStatus.CREATED and result.plan is not None:
        return {
            "web_contract": "goal_progress_apply_v1",
            "status": "saved",
            "write_status": "created",
            "record_kind": result.plan.record_kind.value,
            "record_id": str(result.plan.note_id),
            "plan_sha256": result.plan.plan_sha256,
        }
    if result.status is CreateStatus.ROLLED_BACK and result.plan is not None:
        return {
            "web_contract": "goal_progress_apply_v1",
            "status": "rolled_back",
            "write_status": "rolled-back",
            "record_kind": result.plan.record_kind.value,
            "record_id": str(result.plan.note_id),
            "plan_sha256": result.plan.plan_sha256,
            "rollback": "succeeded" if result.rollback_succeeded else "failed",
            "error": {"code": code, "message": _SAFE_WRITE_MESSAGES[code][1]},
        }
    return {
        "web_contract": "goal_progress_apply_v1",
        "status": "rejected",
        "write_status": "rejected",
        "error": {"code": code, "message": _SAFE_WRITE_MESSAGES[code][1]},
    }


def _application_error_response(error: GoalProgressError) -> Response:
    status, message = _GOAL_PROGRESS_MESSAGES.get(
        error.code,
        _GOAL_PROGRESS_MESSAGES[GoalProgressErrorCode.INTERNAL.value],
    )
    return _safe_error(error.code, message, status)


def _composition_error_response(error: GrowthGoalProgressCompositionError) -> Response:
    status, message = _COMPOSITION_MESSAGES.get(
        error.code,
        _COMPOSITION_MESSAGES[GrowthGoalProgressCompositionErrorCodeV1.INTERNAL.value],
    )
    return _safe_error(error.code, message, status)


def _write_error_response(result: GoalProgressSafeWriteResult) -> Response:
    code = _diagnostic_code(result)
    status, message = _SAFE_WRITE_MESSAGES[code]
    if code in {"GOAL_PROGRESS_VAULT_CHANGED", "GOAL_PROGRESS_SAFE_WRITE_REVIEW_REQUIRED"}:
        status = 409
    if result.status is CreateStatus.ROLLED_BACK and result.rollback_succeeded is False:
        status = 503
    return _safe_error(code, message, status)


def _definition_draft(
    payload: GoalProgressDefinitionPreparePayload,
    goal: GrowthGoalOwnerItemV1,
    reviewed_at: datetime,
) -> GoalProgressDefinitionDraftV1:
    try:
        model = ProgressModelV1(payload.progress_model)
    except TypeError, ValueError:
        raise GoalProgressError(GoalProgressErrorCode.MODEL_UNSUPPORTED) from None
    try:
        if model is ProgressModelV1.NUMERIC_TARGET:
            required = (
                payload.metric_id,
                payload.unit,
                payload.baseline,
                payload.target,
                payload.direction,
            )
            if any(value is None for value in required) or payload.milestones is not None:
                raise GoalProgressError(GoalProgressErrorCode.DEFINITION_INVALID)
            numeric = NumericTargetDefinitionV1(
                metric_id=cast(str, payload.metric_id),
                unit=cast(str, payload.unit),
                baseline=cast(str, payload.baseline),
                target=cast(str, payload.target),
                direction=NumericDirectionV1(cast(str, payload.direction)),
                lower_bound=payload.lower_bound,
                upper_bound=payload.upper_bound,
            )
            milestone_set = None
        else:
            if payload.milestones is None or any(
                value is not None
                for value in (
                    payload.metric_id,
                    payload.unit,
                    payload.baseline,
                    payload.target,
                    payload.direction,
                    payload.lower_bound,
                    payload.upper_bound,
                )
            ):
                raise GoalProgressError(GoalProgressErrorCode.DEFINITION_INVALID)
            milestones = tuple(
                MilestoneV1(id=item.id, label=item.label, ordinal=item.ordinal)
                for item in payload.milestones
            )
            numeric = None
            milestone_set = MilestoneSetDefinitionV1(
                ordering="display_only_v1",
                milestones=milestones,
            )
    except GoalProgressError:
        raise
    except GoalProgressValidationError, TypeError, ValueError, UnicodeError, OverflowError:
        raise GoalProgressError(GoalProgressErrorCode.DEFINITION_INVALID) from None
    return GoalProgressDefinitionDraftV1(
        goal_source_uuid=goal.goal.source_note_uuid,
        goal_identity_fingerprint=goal.goal_identity_fingerprint,
        definition_reviewed_at=reviewed_at,
        progress_model=model,
        numeric_target=numeric,
        milestone_set=milestone_set,
        supersedes_definition_id=(
            _uuid7_text(payload.supersedes_definition_id)
            if payload.supersedes_definition_id is not None
            else None
        ),
    )


def _observation_draft(
    payload: GoalProgressObservationPreparePayload,
    goal: GrowthGoalOwnerItemV1,
    definition: DefinitionRecordV1,
    reviewed_at: datetime,
) -> GoalProgressObservationDraftV1:
    try:
        if definition.progress_model is ProgressModelV1.NUMERIC_TARGET:
            if (
                payload.value is None
                or payload.milestone_id is not None
                or payload.state is not None
            ):
                raise GoalProgressError(GoalProgressErrorCode.OBSERVATION_INVALID)
            numeric = NumericObservationV1(
                metric_id=cast(str, definition.metric_id),
                unit=cast(str, definition.unit),
                value=payload.value,
            )
            milestone = None
        else:
            if payload.milestone_id is None or payload.state is None or payload.value is not None:
                raise GoalProgressError(GoalProgressErrorCode.OBSERVATION_INVALID)
            milestone = MilestoneObservationV1(
                milestone_id=payload.milestone_id,
                state=MilestoneStateV1(payload.state),
            )
            numeric = None
    except GoalProgressError:
        raise
    except GoalProgressValidationError, TypeError, ValueError, UnicodeError, OverflowError:
        raise GoalProgressError(GoalProgressErrorCode.OBSERVATION_INVALID) from None
    return GoalProgressObservationDraftV1(
        goal_source_uuid=goal.goal.source_note_uuid,
        goal_identity_fingerprint=goal.goal_identity_fingerprint,
        progress_definition_id=definition.id,
        progress_model=definition.progress_model,
        observed_at=payload.observed_at,
        observed_at_precision=payload.observed_at_precision,
        observation_reviewed_at=reviewed_at,
        numeric_observation=numeric,
        milestone_observation=milestone,
        supersedes_observation_id=(
            _uuid7_text(payload.supersedes_observation_id)
            if payload.supersedes_observation_id is not None
            else None
        ),
    )


def install_goal_progress_routes(
    app: FastAPI,
    *,
    service: GoalProgressWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install private Stage 12 read, composition, prepare and apply routes."""

    actual_service: GoalProgressWebService = service or build_production_goal_progress_web_service(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    review_store = GoalProgressReviewStore()
    app.add_middleware(GoalProgressRequestBoundaryMiddleware)
    app.state.goal_progress_review_store = review_store

    @app.post(GOAL_PROGRESS_PATH, include_in_schema=False)
    async def goal_progress_endpoint(request: Request) -> Response:
        try:
            payload = GoalProgressReadPayload.model_validate(await _read_json(request), strict=True)
            request_value = validate_goal_progress_request(
                GoalProgressRequestV1.from_dict(payload.model_dump(mode="python"))
            )
            result = await run_in_threadpool(actual_service.read, request_value)
            if type(result) is not GoalProgressResultV1:
                raise GoalProgressError(GoalProgressErrorCode.INTERNAL)
            body = await run_in_threadpool(_owner_result_body, actual_service, result)
            return _json_response(body, max_bytes=MAX_GOAL_PROGRESS_READ_RESPONSE_BYTES)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _safe_error(
                GoalProgressErrorCode.REQUEST_INVALID.value,
                _GOAL_PROGRESS_MESSAGES[GoalProgressErrorCode.REQUEST_INVALID.value][1],
                400,
            )
        except GoalProgressError as error:
            return _application_error_response(error)
        except ConfigurationError, SelfModelError:
            return _application_error_response(
                GoalProgressError(GoalProgressErrorCode.SOURCE_UNAVAILABLE)
            )
        except Exception:
            return _application_error_response(
                GoalProgressError(GoalProgressErrorCode.SOURCE_UNAVAILABLE)
            )

    @app.post(GROWTH_GOAL_PROGRESS_PATH, include_in_schema=False)
    async def growth_goal_progress_endpoint(request: Request) -> Response:
        try:
            payload = GrowthGoalProgressPayload.model_validate(
                await _read_json(request), strict=True
            )
            request_value = validate_growth_goal_progress_composition_request(
                GrowthGoalProgressCompositionRequestV1.from_dict(payload.model_dump(mode="python"))
            )
            result = await run_in_threadpool(actual_service.compose, request_value)
            if type(result) is not GrowthGoalProgressCompositionResultV1:
                raise GrowthGoalProgressCompositionError(
                    GrowthGoalProgressCompositionErrorCodeV1.INTERNAL
                )
            body = await run_in_threadpool(_owner_composition_body, actual_service, result)
            return _json_response(body, max_bytes=MAX_GROWTH_GOAL_PROGRESS_RESPONSE_BYTES)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _safe_error(
                GrowthGoalProgressCompositionErrorCodeV1.INVALID_REQUEST.value,
                _COMPOSITION_MESSAGES[
                    GrowthGoalProgressCompositionErrorCodeV1.INVALID_REQUEST.value
                ][1],
                400,
            )
        except GrowthGoalProgressCompositionError as error:
            return _composition_error_response(error)
        except ConfigurationError, SelfModelError:
            return _composition_error_response(
                GrowthGoalProgressCompositionError(
                    GrowthGoalProgressCompositionErrorCodeV1.SOURCE_UNAVAILABLE
                )
            )
        except Exception:
            return _composition_error_response(
                GrowthGoalProgressCompositionError(
                    GrowthGoalProgressCompositionErrorCodeV1.SOURCE_UNAVAILABLE
                )
            )

    @app.post(GOAL_PROGRESS_DEFINITION_PREPARE_PATH, include_in_schema=False)
    async def goal_progress_definition_prepare_endpoint(request: Request) -> Response:
        try:
            payload = GoalProgressDefinitionPreparePayload.model_validate(
                await _read_json(request), strict=True
            )
            goal_uuid = _uuid7_text(payload.goal_source_uuid)
            goal = await run_in_threadpool(actual_service.goal_projection, goal_uuid)
            reviewed_at = await run_in_threadpool(actual_service.now)
            draft = _definition_draft(payload, goal, reviewed_at)
            result = await run_in_threadpool(actual_service.prepare_definition, draft)
            if type(result) is not GoalProgressSafeWriteResult:
                raise GoalProgressError(GoalProgressErrorCode.INTERNAL)
            if result.status is not CreateStatus.DRY_RUN or result.plan is None:
                return _write_error_response(result)
            token = review_store.issue(_owner_binding(request), result.plan)
            return _json_response(
                _review_projection(token, result.plan, goal),
                max_bytes=MAX_GOAL_PROGRESS_REVIEW_RESPONSE_BYTES,
            )
        except ValidationError, json.JSONDecodeError, UnicodeError, TypeError:
            return _safe_error(
                GoalProgressErrorCode.REQUEST_INVALID.value,
                _GOAL_PROGRESS_MESSAGES[GoalProgressErrorCode.REQUEST_INVALID.value][1],
                400,
            )
        except ValueError:
            return _safe_error(
                GoalProgressErrorCode.REQUEST_INVALID.value,
                _GOAL_PROGRESS_MESSAGES[GoalProgressErrorCode.REQUEST_INVALID.value][1],
                400,
            )
        except GoalProgressError as error:
            if error.code in _SAFE_WRITE_MESSAGES:
                status, message = _SAFE_WRITE_MESSAGES[error.code]
                return _safe_error(error.code, message, status)
            return _application_error_response(error)
        except ConfigurationError, SelfModelError:
            return _safe_error(
                "GOAL_PROGRESS_SOURCE_UNAVAILABLE",
                _SAFE_WRITE_MESSAGES["GOAL_PROGRESS_SOURCE_UNAVAILABLE"][1],
                503,
            )
        except Exception:
            return _safe_error(
                "GOAL_PROGRESS_INTERNAL",
                _SAFE_WRITE_MESSAGES["GOAL_PROGRESS_INTERNAL"][1],
                503,
            )

    @app.post(GOAL_PROGRESS_OBSERVATION_PREPARE_PATH, include_in_schema=False)
    async def goal_progress_observation_prepare_endpoint(request: Request) -> Response:
        try:
            payload = GoalProgressObservationPreparePayload.model_validate(
                await _read_json(request), strict=True
            )
            goal_uuid = _uuid7_text(payload.goal_source_uuid)
            definition_id = _uuid7_text(payload.progress_definition_id)
            goal = await run_in_threadpool(actual_service.goal_projection, goal_uuid)
            definition = await run_in_threadpool(
                actual_service.active_definition,
                goal_uuid,
                goal.goal_identity_fingerprint,
                definition_id,
            )
            reviewed_at = await run_in_threadpool(actual_service.now)
            draft = _observation_draft(payload, goal, definition, reviewed_at)
            result = await run_in_threadpool(actual_service.prepare_observation, draft)
            if type(result) is not GoalProgressSafeWriteResult:
                raise GoalProgressError(GoalProgressErrorCode.INTERNAL)
            if result.status is not CreateStatus.DRY_RUN or result.plan is None:
                return _write_error_response(result)
            token = review_store.issue(_owner_binding(request), result.plan)
            return _json_response(
                _review_projection(token, result.plan, goal),
                max_bytes=MAX_GOAL_PROGRESS_REVIEW_RESPONSE_BYTES,
            )
        except ValidationError, json.JSONDecodeError, UnicodeError, TypeError, ValueError:
            return _safe_error(
                GoalProgressErrorCode.REQUEST_INVALID.value,
                _GOAL_PROGRESS_MESSAGES[GoalProgressErrorCode.REQUEST_INVALID.value][1],
                400,
            )
        except GoalProgressError as error:
            if error.code in _SAFE_WRITE_MESSAGES:
                status, message = _SAFE_WRITE_MESSAGES[error.code]
                return _safe_error(error.code, message, status)
            return _application_error_response(error)
        except ConfigurationError, SelfModelError:
            return _safe_error(
                "GOAL_PROGRESS_SOURCE_UNAVAILABLE",
                _SAFE_WRITE_MESSAGES["GOAL_PROGRESS_SOURCE_UNAVAILABLE"][1],
                503,
            )
        except Exception:
            return _safe_error(
                "GOAL_PROGRESS_INTERNAL",
                _SAFE_WRITE_MESSAGES["GOAL_PROGRESS_INTERNAL"][1],
                503,
            )

    async def _apply_endpoint(request: Request) -> Response:
        try:
            payload = GoalProgressApplyPayload.model_validate(
                await _read_json(request), strict=True
            )
            if not payload.confirmed:
                raise GoalProgressError(GoalProgressErrorCode.REQUEST_INVALID)
            plan = review_store.consume(
                _owner_binding(request),
                payload.review_token,
                payload.accepted_plan_sha256,
            )
            result = await run_in_threadpool(
                actual_service.apply,
                plan,
                payload.accepted_plan_sha256,
            )
            if type(result) is not GoalProgressSafeWriteResult:
                return _safe_error(
                    "GOAL_PROGRESS_INTERNAL",
                    _SAFE_WRITE_MESSAGES["GOAL_PROGRESS_INTERNAL"][1],
                    503,
                )
            if result.status is CreateStatus.CREATED:
                return _json_response(
                    _apply_body(result),
                    max_bytes=MAX_GOAL_PROGRESS_APPLY_RESPONSE_BYTES,
                )
            return _write_error_response(result)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _safe_error(
                GoalProgressErrorCode.REQUEST_INVALID.value,
                _GOAL_PROGRESS_MESSAGES[GoalProgressErrorCode.REQUEST_INVALID.value][1],
                400,
            )
        except _ReviewRequiredError:
            return _safe_error(
                "GOAL_PROGRESS_SAFE_WRITE_REVIEW_REQUIRED",
                _SAFE_WRITE_MESSAGES["GOAL_PROGRESS_SAFE_WRITE_REVIEW_REQUIRED"][1],
                409,
            )
        except GoalProgressError as error:
            if error.code in _SAFE_WRITE_MESSAGES:
                status, message = _SAFE_WRITE_MESSAGES[error.code]
                return _safe_error(error.code, message, status)
            return _application_error_response(error)
        except ConfigurationError, SelfModelError:
            return _safe_error(
                "GOAL_PROGRESS_SOURCE_UNAVAILABLE",
                _SAFE_WRITE_MESSAGES["GOAL_PROGRESS_SOURCE_UNAVAILABLE"][1],
                503,
            )
        except Exception:
            return _safe_error(
                "GOAL_PROGRESS_INTERNAL",
                _SAFE_WRITE_MESSAGES["GOAL_PROGRESS_INTERNAL"][1],
                503,
            )

    @app.post(GOAL_PROGRESS_DEFINITION_APPLY_PATH, include_in_schema=False)
    async def goal_progress_definition_apply_endpoint(request: Request) -> Response:
        return await _apply_endpoint(request)

    @app.post(GOAL_PROGRESS_OBSERVATION_APPLY_PATH, include_in_schema=False)
    async def goal_progress_observation_apply_endpoint(request: Request) -> Response:
        return await _apply_endpoint(request)


__all__ = [
    "GOAL_PROGRESS_DEFINITION_APPLY_PATH",
    "GOAL_PROGRESS_DEFINITION_PREPARE_PATH",
    "GOAL_PROGRESS_OBSERVATION_APPLY_PATH",
    "GOAL_PROGRESS_OBSERVATION_PREPARE_PATH",
    "GOAL_PROGRESS_PATH",
    "GOAL_PROGRESS_REQUEST_HEADER_NAME",
    "GOAL_PROGRESS_REQUEST_HEADER_VALUE",
    "GROWTH_GOAL_PROGRESS_PATH",
    "GROWTH_GOAL_PROGRESS_REQUEST_HEADER_VALUE",
    "MAX_GOAL_PROGRESS_APPLY_RESPONSE_BYTES",
    "MAX_GOAL_PROGRESS_READ_RESPONSE_BYTES",
    "MAX_GOAL_PROGRESS_REVIEW_PLAN_BYTES",
    "MAX_GOAL_PROGRESS_REVIEW_RESPONSE_BYTES",
    "MAX_GROWTH_GOAL_PROGRESS_RESPONSE_BYTES",
    "MAX_RAW_GOAL_PROGRESS_BODY_BYTES",
    "GoalProgressApplyPayload",
    "GoalProgressDefinitionPreparePayload",
    "GoalProgressObservationPreparePayload",
    "GoalProgressReadPayload",
    "GoalProgressRequestBoundaryMiddleware",
    "GoalProgressReviewStore",
    "GoalProgressWebService",
    "GrowthGoalProgressPayload",
    "ProductionGoalProgressWebService",
    "build_production_goal_progress_web_service",
    "install_goal_progress_routes",
]
