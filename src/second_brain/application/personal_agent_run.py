"""Reviewed foreground Run lifecycle for Stage 20.

The module owns only typed operational coordination.  It never imports a
provider adapter, a vault writer, a web entrypoint, or a scheduler.  External
mutation is available only through the narrow Stage 19 application protocol
defined below; prepared actions and confirmation values are deliberately
transient and are never part of an operational snapshot.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol, cast
from uuid import UUID, uuid7

from second_brain.application.action_gateway import (
    ACTION_GATEWAY_CONNECTOR,
    ACTION_GATEWAY_POLICY_ID,
    ACTION_INTENT_CONTRACT_VERSION,
    ActionExecutionResultV1,
    ActionIntentV1,
    ActionKindV1,
    ActionProvenanceV1,
    ActionReceiptKindV1,
    ActionReceiptStateV1,
    IssueStateV1,
    PreparedExternalActionV1,
    ReversibilityV1,
)
from second_brain.application.personal_agent import (
    AgentContextPackV1,
    AgentMissionV1,
    AgentStage19ActionCapabilityV1,
    PersonalAgentError,
    personal_agent_hash,
    validate_agent_context_pack,
)
from second_brain.application.personal_agent_planner import (
    AgentCheckpointStepV1,
    AgentClarifyStepV1,
    AgentHoldStepV1,
    AgentRunProposalInvalidError,
    AgentRunProposalV1,
    AgentRunStepKindV1,
    AgentRunStepV1,
    AgentStage19ActionStepV1,
    _step_from_dict,
    validate_agent_run_proposal,
)
from second_brain.domain.models import parse_rfc3339, parse_uuid7

AGENT_RUN_CONTRACT_VERSION: Final[str] = "personal-agent-run-v1"
AGENT_RUN_SNAPSHOT_CONTRACT_VERSION: Final[str] = "personal-agent-run-snapshot-v1"
AGENT_RUN_RECEIPT_REF_CONTRACT_VERSION: Final[str] = "personal-agent-receipt-ref-v1"
MAX_AGENT_RUN_STEPS: Final[int] = 12
MAX_AGENT_RUN_RECEIPT_REFS: Final[int] = 32
MAX_AGENT_RUN_ANSWER_BYTES: Final[int] = 4096
MAX_AGENT_RUN_NOTE_BYTES: Final[int] = 1024
MAX_AGENT_RUN_OPERATION_BYTES: Final[int] = 256
MAX_AGENT_RUN_HISTORY_BYTES: Final[int] = 256 * 1024

_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_OPERATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z", re.ASCII
)
_SAFE_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9_]{1,80}\Z", re.ASCII)


class AgentRunError(PersonalAgentError):
    """Base safe error for reviewed Run progression."""

    code = "AGENT_RUN_INVALID_REQUEST"


class AgentRunSnapshotInvalidError(AgentRunError):
    """A persisted or supplied Run snapshot is not structurally valid."""

    code = "AGENT_RUN_SNAPSHOT_INVALID"


class AgentRunConflictError(AgentRunError):
    """Another current Run or idempotency intent prevents the operation."""

    code = "RUN_CONFLICT"


class AgentRunNotFoundError(AgentRunError):
    """The requested Run is not present in the operational store."""

    code = "RUN_NOT_FOUND"


class AgentRunNotCurrentError(AgentRunError):
    """The requested Run is not the one current operational Run."""

    code = "RUN_NOT_CURRENT"


class AgentRunStepNotCurrentError(AgentRunError):
    """The requested operation does not address the one current step."""

    code = "STEP_NOT_CURRENT"


class AgentRunInvalidTransitionError(AgentRunError):
    """The explicit owner action is not legal from the current state."""

    code = "INVALID_TRANSITION"


class AgentRunActionNotAllowedError(AgentRunError):
    """The closed Stage 19 action candidate is not allowed by the Run."""

    code = "ACTION_NOT_ALLOWED"


class AgentRunPrepareRequiredError(AgentRunError):
    """Execution requires an exact transient Stage 19 prepared action."""

    code = "ACTION_PREPARE_REQUIRED"


class AgentRunStage19UnavailableError(AgentRunError):
    """The Stage 19 application capability is currently unavailable."""

    code = "STAGE19_UNAVAILABLE"


class AgentRunReceiptUncertainError(AgentRunError):
    """An uncertain external outcome cannot be advanced or retried blindly."""

    code = "RECEIPT_UNCERTAIN"


class AgentRunReconciliationRequiredError(AgentRunError):
    """Read-only Stage 19 reconciliation is required before progression."""

    code = "RECONCILIATION_REQUIRED"


class AgentRunSourceDriftError(AgentRunError):
    """The exact Stage 17/18/19 source binding changed."""

    code = "SOURCE_STALE"


class AgentRunStoreError(AgentRunError):
    """Application-facing store failure without filesystem detail."""

    code = "STORE_UNAVAILABLE"


class AgentRunStateV1(StrEnum):
    """Closed Run lifecycle states from the normative Stage 20 contract."""

    PROPOSAL = "proposal"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    ACTIVE = "active"
    WAITING_OWNER = "waiting_owner"
    WAITING_STAGE19 = "waiting_stage19"
    PAUSED = "paused"
    READY_TO_COMPLETE = "ready_to_complete"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    SUPERSEDED = "superseded"


class AgentRunStepStateV1(StrEnum):
    """Closed per-step progression states."""

    PENDING = "pending"
    CURRENT = "current"
    WAITING_OWNER = "waiting_owner"
    WAITING_STAGE19 = "waiting_stage19"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class AgentRunEventKindV1(StrEnum):
    """Only safe lifecycle event labels may enter the Stage 20 ledger."""

    ACCEPT = "accept"
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    ANSWER = "answer"
    CONTINUE = "continue"
    SKIP = "skip"
    PREPARE_RESULT = "prepare_result"
    STAGE19_RESULT = "stage19_result"
    RECONCILE_RESULT = "reconcile_result"
    COMPENSATION_RESULT = "compensation_result"
    SUPERSEDE = "supersede"
    ABANDON = "abandon"
    COMPLETE = "complete"


def _safe_text(value: object, *, maximum: int, error: type[PersonalAgentError]) -> str:
    if type(value) is not str:
        raise error()
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized or len(normalized.encode("utf-8")) > maximum:
        raise error()
    if any(
        ord(character) < 0x20
        or 0x7F <= ord(character) <= 0x9F
        or unicodedata.category(character) == "Cf"
        for character in normalized
    ):
        raise error()
    return normalized


def _optional_text(value: object, *, maximum: int, error: type[PersonalAgentError]) -> str | None:
    return None if value is None else _safe_text(value, maximum=maximum, error=error)


def _hash(value: object, *, error: type[PersonalAgentError]) -> str:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise error()
    return value


def _uuid7(value: object, *, error: type[PersonalAgentError]) -> UUID:
    try:
        return parse_uuid7(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise error() from exc


def _timestamp(value: object, *, error: type[PersonalAgentError]) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else parse_rfc3339(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise error() from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise error()
    normalized = parsed.astimezone(UTC)
    return normalized


def _wire_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _operation(value: object) -> str:
    normalized = _safe_text(value, maximum=MAX_AGENT_RUN_OPERATION_BYTES, error=AgentRunError)
    if _OPERATION_PATTERN.fullmatch(normalized) is None:
        raise AgentRunError()
    return normalized


def _enum_value[EnumT: StrEnum](
    value: object,
    enum_type: type[EnumT],
    *,
    error: type[PersonalAgentError],
) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if type(value) is str:
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise error()


def _step_state(value: object, *, error: type[PersonalAgentError]) -> AgentRunStepStateV1:
    return _enum_value(value, AgentRunStepStateV1, error=error)


def _run_state(value: object, *, error: type[PersonalAgentError]) -> AgentRunStateV1:
    return _enum_value(value, AgentRunStateV1, error=error)


def _event_kind(value: object) -> AgentRunEventKindV1:
    return _enum_value(value, AgentRunEventKindV1, error=AgentRunSnapshotInvalidError)


@dataclass(frozen=True, slots=True)
class AgentStage18BindingV1:
    """Exact Stage 18 identity retained by an accepted Run."""

    item_id: str
    accepted_item_fingerprint: str
    execution_context_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "item_id",
            _safe_text(self.item_id, maximum=128, error=AgentRunSnapshotInvalidError),
        )
        object.__setattr__(
            self,
            "accepted_item_fingerprint",
            _hash(self.accepted_item_fingerprint, error=AgentRunSnapshotInvalidError),
        )
        object.__setattr__(
            self,
            "execution_context_fingerprint",
            _hash(self.execution_context_fingerprint, error=AgentRunSnapshotInvalidError),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "item_id": self.item_id,
            "accepted_item_fingerprint": self.accepted_item_fingerprint,
            "execution_context_fingerprint": self.execution_context_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: object) -> AgentStage18BindingV1:
        if type(value) is not dict or set(value) != {
            "item_id",
            "accepted_item_fingerprint",
            "execution_context_fingerprint",
        }:
            raise AgentRunSnapshotInvalidError()
        data = cast(dict[str, object], value)
        return cls(
            item_id=cast(str, data["item_id"]),
            accepted_item_fingerprint=cast(str, data["accepted_item_fingerprint"]),
            execution_context_fingerprint=cast(str, data["execution_context_fingerprint"]),
        )


@dataclass(frozen=True, slots=True)
class AgentStage19ReceiptRefV1:
    """Safe final Stage 19 receipt reference; never a raw provider response."""

    contract_version: str
    receipt_id: UUID | str
    receipt_kind: ActionReceiptKindV1 | str
    operation_id_fingerprint: str
    intent_fingerprint: str
    action_kind: ActionKindV1 | str
    payload_fingerprint: str
    state: ActionReceiptStateV1 | str
    safe_error_code: str | None = None
    parent_receipt_id: UUID | str | None = None

    def __post_init__(self) -> None:
        if self.contract_version != AGENT_RUN_RECEIPT_REF_CONTRACT_VERSION:
            raise AgentRunSnapshotInvalidError()
        receipt_id = _uuid7(self.receipt_id, error=AgentRunSnapshotInvalidError)
        parent = (
            None
            if self.parent_receipt_id is None
            else _uuid7(self.parent_receipt_id, error=AgentRunSnapshotInvalidError)
        )
        try:
            receipt_kind = ActionReceiptKindV1(self.receipt_kind)
            action_kind = ActionKindV1(self.action_kind)
            state = ActionReceiptStateV1(self.state)
        except (TypeError, ValueError) as exc:
            raise AgentRunSnapshotInvalidError() from exc
        object.__setattr__(
            self,
            "operation_id_fingerprint",
            _hash(self.operation_id_fingerprint, error=AgentRunSnapshotInvalidError),
        )
        object.__setattr__(
            self,
            "intent_fingerprint",
            _hash(self.intent_fingerprint, error=AgentRunSnapshotInvalidError),
        )
        object.__setattr__(
            self,
            "payload_fingerprint",
            _hash(self.payload_fingerprint, error=AgentRunSnapshotInvalidError),
        )
        safe_error_code = _optional_text(
            self.safe_error_code,
            maximum=80,
            error=AgentRunSnapshotInvalidError,
        )
        if safe_error_code is not None and _SAFE_CODE_PATTERN.fullmatch(safe_error_code) is None:
            raise AgentRunSnapshotInvalidError()
        object.__setattr__(self, "receipt_id", receipt_id)
        object.__setattr__(self, "parent_receipt_id", parent)
        object.__setattr__(self, "receipt_kind", receipt_kind)
        object.__setattr__(self, "action_kind", action_kind)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "safe_error_code", safe_error_code)

    @classmethod
    def from_result(cls, result: ActionExecutionResultV1) -> AgentStage19ReceiptRefV1:
        receipt = result.receipt
        return cls(
            contract_version=AGENT_RUN_RECEIPT_REF_CONTRACT_VERSION,
            receipt_id=receipt.receipt_id,
            receipt_kind=receipt.receipt_kind,
            operation_id_fingerprint=receipt.operation_id_fingerprint,
            intent_fingerprint=receipt.intent_fingerprint,
            action_kind=receipt.action_kind,
            payload_fingerprint=receipt.payload_fingerprint,
            state=receipt.state,
            safe_error_code=receipt.safe_error_code,
            parent_receipt_id=receipt.parent_receipt_id,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "receipt_id": str(self.receipt_id),
            "receipt_kind": cast(ActionReceiptKindV1, self.receipt_kind).value,
            "operation_id_fingerprint": self.operation_id_fingerprint,
            "intent_fingerprint": self.intent_fingerprint,
            "action_kind": cast(ActionKindV1, self.action_kind).value,
            "payload_fingerprint": self.payload_fingerprint,
            "state": cast(ActionReceiptStateV1, self.state).value,
            "safe_error_code": self.safe_error_code,
            "parent_receipt_id": (
                None if self.parent_receipt_id is None else str(self.parent_receipt_id)
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> AgentStage19ReceiptRefV1:
        if type(value) is not dict or set(value) != {
            "contract_version",
            "receipt_id",
            "receipt_kind",
            "operation_id_fingerprint",
            "intent_fingerprint",
            "action_kind",
            "payload_fingerprint",
            "state",
            "safe_error_code",
            "parent_receipt_id",
        }:
            raise AgentRunSnapshotInvalidError()
        data = cast(dict[str, object], value)
        return cls(
            contract_version=cast(str, data["contract_version"]),
            receipt_id=cast(UUID | str, data["receipt_id"]),
            receipt_kind=cast(ActionReceiptKindV1 | str, data["receipt_kind"]),
            operation_id_fingerprint=cast(str, data["operation_id_fingerprint"]),
            intent_fingerprint=cast(str, data["intent_fingerprint"]),
            action_kind=cast(ActionKindV1 | str, data["action_kind"]),
            payload_fingerprint=cast(str, data["payload_fingerprint"]),
            state=cast(ActionReceiptStateV1 | str, data["state"]),
            safe_error_code=cast(str | None, data["safe_error_code"]),
            parent_receipt_id=cast(UUID | str | None, data["parent_receipt_id"]),
        )


@dataclass(frozen=True, slots=True)
class AgentRunStepSnapshotV1:
    """One reviewed proposal step plus operational progression state."""

    step: AgentRunStepV1
    state: AgentRunStepStateV1 | str
    owner_answer: str | None = None
    resolution_note: str | None = None
    receipt_ref: AgentStage19ReceiptRefV1 | None = None

    def __post_init__(self) -> None:
        if type(self.step) not in {
            AgentClarifyStepV1,
            AgentCheckpointStepV1,
            AgentHoldStepV1,
            AgentStage19ActionStepV1,
        }:
            raise AgentRunSnapshotInvalidError()
        state = _step_state(self.state, error=AgentRunSnapshotInvalidError)
        answer = _optional_text(
            self.owner_answer,
            maximum=MAX_AGENT_RUN_ANSWER_BYTES,
            error=AgentRunSnapshotInvalidError,
        )
        note = _optional_text(
            self.resolution_note,
            maximum=MAX_AGENT_RUN_NOTE_BYTES,
            error=AgentRunSnapshotInvalidError,
        )
        if answer is not None and type(self.step) is not AgentClarifyStepV1:
            raise AgentRunSnapshotInvalidError()
        if self.receipt_ref is not None:
            if type(self.receipt_ref) is not AgentStage19ReceiptRefV1:
                raise AgentRunSnapshotInvalidError()
            if type(self.step) is not AgentStage19ActionStepV1:
                raise AgentRunSnapshotInvalidError()
        if state is AgentRunStepStateV1.PENDING and any(
            value is not None for value in (answer, note, self.receipt_ref)
        ):
            raise AgentRunSnapshotInvalidError()
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "owner_answer", answer)
        object.__setattr__(self, "resolution_note", note)

    def as_dict(self) -> dict[str, object]:
        return {
            "step": self.step.as_dict(),
            "state": cast(AgentRunStepStateV1, self.state).value,
            "owner_answer": self.owner_answer,
            "resolution_note": self.resolution_note,
            "receipt_ref": None if self.receipt_ref is None else self.receipt_ref.as_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> AgentRunStepSnapshotV1:
        if type(value) is not dict or set(value) != {
            "step",
            "state",
            "owner_answer",
            "resolution_note",
            "receipt_ref",
        }:
            raise AgentRunSnapshotInvalidError()
        data = cast(dict[str, object], value)
        try:
            step = _step_from_dict(data["step"])
        except (AgentRunProposalInvalidError, TypeError, ValueError) as exc:
            raise AgentRunSnapshotInvalidError() from exc
        receipt = (
            None
            if data["receipt_ref"] is None
            else AgentStage19ReceiptRefV1.from_dict(data["receipt_ref"])
        )
        return cls(
            step=step,
            state=cast(AgentRunStepStateV1 | str, data["state"]),
            owner_answer=cast(str | None, data["owner_answer"]),
            resolution_note=cast(str | None, data["resolution_note"]),
            receipt_ref=receipt,
        )


def _snapshot_core(snapshot: AgentRunSnapshotV1) -> dict[str, object]:
    return {
        "contract_version": snapshot.contract_version,
        "run_id": str(snapshot.run_id),
        "revision": snapshot.revision,
        "mission": snapshot.mission.as_dict(),
        "context_pack_fingerprint": snapshot.context_pack_fingerprint,
        "planning_snapshot_id": str(snapshot.planning_snapshot_id),
        "planning_snapshot_fingerprint": snapshot.planning_snapshot_fingerprint,
        "planning_policy_id": snapshot.planning_policy_id,
        "planning_policy_fingerprint": snapshot.planning_policy_fingerprint,
        "stage18_bindings": [item.as_dict() for item in snapshot.stage18_bindings],
        "stage19_policy_id": snapshot.stage19_policy_id,
        "stage19_policy_fingerprint": snapshot.stage19_policy_fingerprint,
        "stage19_catalog_fingerprint": snapshot.stage19_catalog_fingerprint,
        "proposal_id": str(snapshot.proposal_id),
        "proposal_fingerprint": snapshot.proposal_fingerprint,
        "provider_fingerprint": snapshot.provider_fingerprint,
        "steps": [item.as_dict() for item in snapshot.steps],
        "receipt_refs": [item.as_dict() for item in snapshot.receipt_refs],
        "state": cast(AgentRunStateV1, snapshot.state).value,
        "accepted_at": _wire_timestamp(cast(datetime, snapshot.accepted_at)),
        "updated_at": _wire_timestamp(cast(datetime, snapshot.updated_at)),
        "supersedes_run_id": (
            None if snapshot.supersedes_run_id is None else str(snapshot.supersedes_run_id)
        ),
    }


def _snapshot_fingerprint_for(values: Mapping[str, object]) -> str:
    return personal_agent_hash(dict(values))


@dataclass(frozen=True, slots=True)
class AgentRunSnapshotV1:
    """Immutable accepted Run snapshot replayed by the append-only store."""

    contract_version: str
    run_id: UUID | str
    revision: int
    mission: AgentMissionV1
    context_pack_fingerprint: str
    planning_snapshot_id: UUID | str
    planning_snapshot_fingerprint: str
    planning_policy_id: str
    planning_policy_fingerprint: str
    stage18_bindings: tuple[AgentStage18BindingV1, ...]
    stage19_policy_id: str
    stage19_policy_fingerprint: str
    stage19_catalog_fingerprint: str
    proposal_id: UUID | str
    proposal_fingerprint: str
    provider_fingerprint: str
    steps: tuple[AgentRunStepSnapshotV1, ...]
    receipt_refs: tuple[AgentStage19ReceiptRefV1, ...]
    state: AgentRunStateV1 | str
    accepted_at: datetime | str
    updated_at: datetime | str
    supersedes_run_id: UUID | str | None
    snapshot_fingerprint: str

    def __post_init__(self) -> None:
        if self.contract_version != AGENT_RUN_SNAPSHOT_CONTRACT_VERSION:
            raise AgentRunSnapshotInvalidError()
        run_id = _uuid7(self.run_id, error=AgentRunSnapshotInvalidError)
        proposal_id = _uuid7(self.proposal_id, error=AgentRunSnapshotInvalidError)
        supersedes = (
            None
            if self.supersedes_run_id is None
            else _uuid7(self.supersedes_run_id, error=AgentRunSnapshotInvalidError)
        )
        if type(self.revision) is not int or isinstance(self.revision, bool) or self.revision < 1:
            raise AgentRunSnapshotInvalidError()
        if type(self.mission) is not AgentMissionV1:
            raise AgentRunSnapshotInvalidError()
        context_fp = _hash(self.context_pack_fingerprint, error=AgentRunSnapshotInvalidError)
        planning_id = _uuid7(self.planning_snapshot_id, error=AgentRunSnapshotInvalidError)
        planning_fp = _hash(
            self.planning_snapshot_fingerprint,
            error=AgentRunSnapshotInvalidError,
        )
        if (
            planning_id != self.mission.planning_snapshot_id
            or planning_fp != self.mission.planning_snapshot_fingerprint
            or self.planning_policy_id != self.mission.planning_policy_id
            or self.planning_policy_fingerprint != self.mission.planning_policy_fingerprint
        ):
            raise AgentRunSnapshotInvalidError()
        if self.stage19_policy_id != ACTION_GATEWAY_POLICY_ID:
            raise AgentRunSnapshotInvalidError()
        stage19_fp = _hash(
            self.stage19_policy_fingerprint,
            error=AgentRunSnapshotInvalidError,
        )
        catalog_fp = _hash(
            self.stage19_catalog_fingerprint,
            error=AgentRunSnapshotInvalidError,
        )
        proposal_fp = _hash(self.proposal_fingerprint, error=AgentRunSnapshotInvalidError)
        provider_fp = _hash(self.provider_fingerprint, error=AgentRunSnapshotInvalidError)
        state = _run_state(self.state, error=AgentRunSnapshotInvalidError)
        accepted_at = _timestamp(self.accepted_at, error=AgentRunSnapshotInvalidError)
        updated_at = _timestamp(self.updated_at, error=AgentRunSnapshotInvalidError)
        if updated_at < accepted_at:
            raise AgentRunSnapshotInvalidError()
        if type(self.stage18_bindings) is not tuple or not self.stage18_bindings:
            raise AgentRunSnapshotInvalidError()
        bindings = tuple(self.stage18_bindings)
        if any(type(item) is not AgentStage18BindingV1 for item in bindings):
            raise AgentRunSnapshotInvalidError()
        if tuple(item.item_id for item in bindings) != tuple(
            item.item_id for item in self.mission.selected_items
        ):
            raise AgentRunSnapshotInvalidError()
        if type(self.steps) is not tuple or not 1 <= len(self.steps) <= MAX_AGENT_RUN_STEPS:
            raise AgentRunSnapshotInvalidError()
        steps = tuple(self.steps)
        if any(type(item) is not AgentRunStepSnapshotV1 for item in steps):
            raise AgentRunSnapshotInvalidError()
        if tuple(item.step.position for item in steps) != tuple(range(1, len(steps) + 1)):
            raise AgentRunSnapshotInvalidError()
        if len({item.step.step_id for item in steps}) != len(steps):
            raise AgentRunSnapshotInvalidError()
        if (
            type(self.receipt_refs) is not tuple
            or len(self.receipt_refs) > MAX_AGENT_RUN_RECEIPT_REFS
        ):
            raise AgentRunSnapshotInvalidError()
        receipts = tuple(self.receipt_refs)
        if any(type(item) is not AgentStage19ReceiptRefV1 for item in receipts):
            raise AgentRunSnapshotInvalidError()
        active_states = {
            AgentRunStepStateV1.CURRENT,
            AgentRunStepStateV1.WAITING_OWNER,
            AgentRunStepStateV1.WAITING_STAGE19,
        }
        current_count = sum(item.state in active_states for item in steps)
        if current_count > 1:
            raise AgentRunSnapshotInvalidError()
        if state is AgentRunStateV1.ACCEPTED and any(
            item.state is not AgentRunStepStateV1.PENDING for item in steps
        ):
            raise AgentRunSnapshotInvalidError()
        if state is AgentRunStateV1.WAITING_OWNER and not any(
            item.state is AgentRunStepStateV1.WAITING_OWNER for item in steps
        ):
            raise AgentRunSnapshotInvalidError()
        if state is AgentRunStateV1.WAITING_STAGE19 and not any(
            item.state is AgentRunStepStateV1.WAITING_STAGE19 for item in steps
        ):
            raise AgentRunSnapshotInvalidError()
        if state is AgentRunStateV1.READY_TO_COMPLETE and (
            current_count
            or any(
                item.state not in {AgentRunStepStateV1.COMPLETED, AgentRunStepStateV1.SKIPPED}
                for item in steps
            )
        ):
            raise AgentRunSnapshotInvalidError()
        if (
            state
            in {
                AgentRunStateV1.COMPLETED,
                AgentRunStateV1.ABANDONED,
                AgentRunStateV1.SUPERSEDED,
            }
            and current_count
        ):
            raise AgentRunSnapshotInvalidError()
        supplied = _hash(self.snapshot_fingerprint, error=AgentRunSnapshotInvalidError)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "proposal_id", proposal_id)
        object.__setattr__(self, "supersedes_run_id", supersedes)
        object.__setattr__(self, "context_pack_fingerprint", context_fp)
        object.__setattr__(self, "planning_snapshot_id", planning_id)
        object.__setattr__(self, "planning_snapshot_fingerprint", planning_fp)
        object.__setattr__(self, "stage19_policy_fingerprint", stage19_fp)
        object.__setattr__(self, "stage19_catalog_fingerprint", catalog_fp)
        object.__setattr__(self, "proposal_fingerprint", proposal_fp)
        object.__setattr__(self, "provider_fingerprint", provider_fp)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "accepted_at", accepted_at)
        object.__setattr__(self, "updated_at", updated_at)
        object.__setattr__(self, "stage18_bindings", bindings)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "receipt_refs", receipts)
        if supplied != personal_agent_hash(_snapshot_core(self)):
            raise AgentRunSnapshotInvalidError()

    @classmethod
    def create(
        cls,
        *,
        run_id: UUID | str,
        revision: int,
        mission: AgentMissionV1,
        context_pack_fingerprint: str,
        planning_snapshot_id: UUID | str,
        planning_snapshot_fingerprint: str,
        planning_policy_id: str,
        planning_policy_fingerprint: str,
        stage18_bindings: tuple[AgentStage18BindingV1, ...],
        stage19_policy_id: str,
        stage19_policy_fingerprint: str,
        stage19_catalog_fingerprint: str,
        proposal_id: UUID | str,
        proposal_fingerprint: str,
        provider_fingerprint: str,
        steps: tuple[AgentRunStepSnapshotV1, ...],
        receipt_refs: tuple[AgentStage19ReceiptRefV1, ...],
        state: AgentRunStateV1 | str,
        accepted_at: datetime | str,
        updated_at: datetime | str,
        supersedes_run_id: UUID | str | None = None,
    ) -> AgentRunSnapshotV1:
        values: dict[str, object] = {
            "contract_version": AGENT_RUN_SNAPSHOT_CONTRACT_VERSION,
            "run_id": str(run_id),
            "revision": revision,
            "mission": mission.as_dict(),
            "context_pack_fingerprint": context_pack_fingerprint,
            "planning_snapshot_id": str(planning_snapshot_id),
            "planning_snapshot_fingerprint": planning_snapshot_fingerprint,
            "planning_policy_id": planning_policy_id,
            "planning_policy_fingerprint": planning_policy_fingerprint,
            "stage18_bindings": [item.as_dict() for item in stage18_bindings],
            "stage19_policy_id": stage19_policy_id,
            "stage19_policy_fingerprint": stage19_policy_fingerprint,
            "stage19_catalog_fingerprint": stage19_catalog_fingerprint,
            "proposal_id": str(proposal_id),
            "proposal_fingerprint": proposal_fingerprint,
            "provider_fingerprint": provider_fingerprint,
            "steps": [item.as_dict() for item in steps],
            "receipt_refs": [item.as_dict() for item in receipt_refs],
            "state": state.value if isinstance(state, AgentRunStateV1) else state,
            "accepted_at": _wire_timestamp(_timestamp(accepted_at, error=AgentRunError)),
            "updated_at": _wire_timestamp(_timestamp(updated_at, error=AgentRunError)),
            "supersedes_run_id": None if supersedes_run_id is None else str(supersedes_run_id),
        }
        fingerprint = _snapshot_fingerprint_for(values)
        return cls(
            contract_version=AGENT_RUN_SNAPSHOT_CONTRACT_VERSION,
            run_id=run_id,
            revision=revision,
            mission=mission,
            context_pack_fingerprint=context_pack_fingerprint,
            planning_snapshot_id=planning_snapshot_id,
            planning_snapshot_fingerprint=planning_snapshot_fingerprint,
            planning_policy_id=planning_policy_id,
            planning_policy_fingerprint=planning_policy_fingerprint,
            stage18_bindings=stage18_bindings,
            stage19_policy_id=stage19_policy_id,
            stage19_policy_fingerprint=stage19_policy_fingerprint,
            stage19_catalog_fingerprint=stage19_catalog_fingerprint,
            proposal_id=proposal_id,
            proposal_fingerprint=proposal_fingerprint,
            provider_fingerprint=provider_fingerprint,
            steps=steps,
            receipt_refs=receipt_refs,
            state=state,
            accepted_at=accepted_at,
            updated_at=updated_at,
            supersedes_run_id=supersedes_run_id,
            snapshot_fingerprint=fingerprint,
        )

    @classmethod
    def accepted_from(
        cls,
        pack: AgentContextPackV1,
        proposal: AgentRunProposalV1,
        *,
        run_id: UUID | str,
        now: datetime,
        supersedes_run_id: UUID | str | None = None,
    ) -> AgentRunSnapshotV1:
        validated_pack = validate_agent_context_pack(pack)
        validated_proposal = validate_agent_run_proposal(proposal)
        if (
            validated_proposal.mission_fingerprint != validated_pack.mission.fingerprint
            or validated_proposal.context_pack_fingerprint != validated_pack.fingerprint
        ):
            raise AgentRunSourceDriftError()
        stage18 = tuple(
            AgentStage18BindingV1(
                item_id=item.item_id,
                accepted_item_fingerprint=item.accepted_item_fingerprint,
                execution_context_fingerprint=personal_agent_hash(item.execution.as_dict()),
            )
            for item in validated_pack.selected_items
        )
        steps = tuple(
            AgentRunStepSnapshotV1(step=step, state=AgentRunStepStateV1.PENDING)
            for step in validated_proposal.steps
        )
        return cls.create(
            run_id=run_id,
            revision=1,
            mission=validated_pack.mission,
            context_pack_fingerprint=validated_pack.fingerprint,
            planning_snapshot_id=validated_pack.mission.planning_snapshot_id,
            planning_snapshot_fingerprint=validated_pack.mission.planning_snapshot_fingerprint,
            planning_policy_id=validated_pack.mission.planning_policy_id,
            planning_policy_fingerprint=validated_pack.mission.planning_policy_fingerprint,
            stage18_bindings=stage18,
            stage19_policy_id=validated_pack.stage19.policy_id,
            stage19_policy_fingerprint=validated_pack.stage19.policy_fingerprint,
            stage19_catalog_fingerprint=personal_agent_hash(validated_pack.stage19.as_dict()),
            proposal_id=validated_proposal.proposal_id,
            proposal_fingerprint=validated_proposal.proposal_fingerprint,
            provider_fingerprint=validated_proposal.provider_fingerprint,
            steps=steps,
            receipt_refs=(),
            state=AgentRunStateV1.ACCEPTED,
            accepted_at=now,
            updated_at=now,
            supersedes_run_id=supersedes_run_id,
        )

    @property
    def fingerprint(self) -> str:
        return self.snapshot_fingerprint

    @property
    def current_step(self) -> AgentRunStepSnapshotV1 | None:
        current_states = {
            AgentRunStepStateV1.CURRENT,
            AgentRunStepStateV1.WAITING_OWNER,
            AgentRunStepStateV1.WAITING_STAGE19,
        }
        matches = tuple(item for item in self.steps if item.state in current_states)
        return matches[0] if matches else None

    @property
    def current_step_position(self) -> int | None:
        step = self.current_step
        return None if step is None else step.step.position

    def as_dict(self) -> dict[str, object]:
        return {**_snapshot_core(self), "snapshot_fingerprint": self.snapshot_fingerprint}

    @classmethod
    def from_dict(cls, value: object) -> AgentRunSnapshotV1:
        if type(value) is not dict or set(value) != {
            "contract_version",
            "run_id",
            "revision",
            "mission",
            "context_pack_fingerprint",
            "planning_snapshot_id",
            "planning_snapshot_fingerprint",
            "planning_policy_id",
            "planning_policy_fingerprint",
            "stage18_bindings",
            "stage19_policy_id",
            "stage19_policy_fingerprint",
            "stage19_catalog_fingerprint",
            "proposal_id",
            "proposal_fingerprint",
            "provider_fingerprint",
            "steps",
            "receipt_refs",
            "state",
            "accepted_at",
            "updated_at",
            "supersedes_run_id",
            "snapshot_fingerprint",
        }:
            raise AgentRunSnapshotInvalidError()
        data = cast(dict[str, object], value)
        bindings = data["stage18_bindings"]
        steps = data["steps"]
        receipts = data["receipt_refs"]
        if type(bindings) is not list or type(steps) is not list or type(receipts) is not list:
            raise AgentRunSnapshotInvalidError()
        try:
            mission = AgentMissionV1.from_dict(data["mission"])
            return cls(
                contract_version=cast(str, data["contract_version"]),
                run_id=cast(UUID | str, data["run_id"]),
                revision=cast(int, data["revision"]),
                mission=mission,
                context_pack_fingerprint=cast(str, data["context_pack_fingerprint"]),
                planning_snapshot_id=cast(UUID | str, data["planning_snapshot_id"]),
                planning_snapshot_fingerprint=cast(str, data["planning_snapshot_fingerprint"]),
                planning_policy_id=cast(str, data["planning_policy_id"]),
                planning_policy_fingerprint=cast(str, data["planning_policy_fingerprint"]),
                stage18_bindings=tuple(AgentStage18BindingV1.from_dict(item) for item in bindings),
                stage19_policy_id=cast(str, data["stage19_policy_id"]),
                stage19_policy_fingerprint=cast(str, data["stage19_policy_fingerprint"]),
                stage19_catalog_fingerprint=cast(str, data["stage19_catalog_fingerprint"]),
                proposal_id=cast(UUID | str, data["proposal_id"]),
                proposal_fingerprint=cast(str, data["proposal_fingerprint"]),
                provider_fingerprint=cast(str, data["provider_fingerprint"]),
                steps=tuple(AgentRunStepSnapshotV1.from_dict(item) for item in steps),
                receipt_refs=tuple(AgentStage19ReceiptRefV1.from_dict(item) for item in receipts),
                state=cast(AgentRunStateV1 | str, data["state"]),
                accepted_at=cast(datetime | str, data["accepted_at"]),
                updated_at=cast(datetime | str, data["updated_at"]),
                supersedes_run_id=cast(UUID | str | None, data["supersedes_run_id"]),
                snapshot_fingerprint=cast(str, data["snapshot_fingerprint"]),
            )
        except (PersonalAgentError, TypeError, ValueError, KeyError) as exc:
            if isinstance(exc, AgentRunSnapshotInvalidError):
                raise
            raise AgentRunSnapshotInvalidError() from exc


class AgentRunStorePort(Protocol):
    """Narrow append-only Stage 20 operational store boundary."""

    def append(
        self,
        snapshot: AgentRunSnapshotV1,
        *,
        operation_id: str,
        event_kind: AgentRunEventKindV1,
    ) -> AgentRunSnapshotV1:
        """Append one immutable snapshot or return the exact idempotent result."""

    def latest(self, run_id: UUID | str) -> AgentRunSnapshotV1 | None:
        """Return one Run's latest snapshot without creating state."""

    def current(self) -> AgentRunSnapshotV1 | None:
        """Return the sole non-terminal current Run, if any."""

    def history(self, run_id: UUID | str) -> tuple[AgentRunSnapshotV1, ...]:
        """Return one immutable Run history in append order."""


class AgentStage19ActionBridgePort(Protocol):
    """Typed Stage 19 application seam; no connector or credential methods."""

    def prepare(self, intent: ActionIntentV1) -> PreparedExternalActionV1:
        """Run exactly one Stage 19 prepare/preflight operation."""

    def issue_confirmation(self, prepared: PreparedExternalActionV1) -> str:
        """Issue one transient Stage 19 confirmation value."""

    def execute(
        self, prepared: PreparedExternalActionV1, confirmation: object
    ) -> ActionExecutionResultV1:
        """Execute one explicitly confirmed Stage 19 action."""

    def reconcile(self, prepared: PreparedExternalActionV1) -> ActionExecutionResultV1:
        """Request one read-only Stage 19 reconciliation."""

    def prepare_compensation(
        self, parent_receipt_id: UUID, operation_id: str
    ) -> PreparedExternalActionV1:
        """Prepare a separately confirmed Stage 19 compensation."""

    def execute_compensation(
        self,
        prepared: PreparedExternalActionV1,
        confirmation: object,
        parent_receipt_id: UUID,
    ) -> ActionExecutionResultV1:
        """Execute one separately confirmed Stage 19 compensation."""


@dataclass(frozen=True, slots=True)
class AgentStage19PreparedActionV1:
    """Transient safe projection around a Stage 19 prepared action."""

    prepared: PreparedExternalActionV1

    def __post_init__(self) -> None:
        if type(self.prepared) is not PreparedExternalActionV1:
            raise AgentRunPrepareRequiredError()

    def as_dict(self) -> dict[str, object]:
        prepared = self.prepared
        return {
            "prepared_action_id": str(prepared.prepared_action_id),
            "action_kind": cast(ActionKindV1, prepared.action_kind).value,
            "operation_id_fingerprint": prepared.operation_id_fingerprint,
            "intent_fingerprint": prepared.intent_fingerprint,
            "payload_fingerprint": prepared.payload_fingerprint,
            "target_safe_identity": prepared.exact_target_identity.safe_identity(),
            "preview": prepared.preview,
            "preview_fingerprint": prepared.preview_fingerprint,
            "expires_at": _wire_timestamp(prepared.expires_at),
            "reversibility": cast(ReversibilityV1, prepared.reversibility).value,
        }


def _proposal_from_steps(
    proposal: AgentRunProposalV1,
    steps: tuple[AgentRunStepV1, ...],
) -> AgentRunProposalV1:
    core = {
        "contract_version": proposal.contract_version,
        "proposal_id": str(proposal.proposal_id),
        "mission_fingerprint": proposal.mission_fingerprint,
        "context_pack_fingerprint": proposal.context_pack_fingerprint,
        "steps": [step.as_dict() for step in steps],
        "caveats": list(proposal.caveats),
        "provider_policy_id": proposal.provider_policy_id,
        "provider_policy_fingerprint": proposal.provider_policy_fingerprint,
        "provider_fingerprint": proposal.provider_fingerprint,
    }
    return AgentRunProposalV1(
        contract_version=proposal.contract_version,
        proposal_id=proposal.proposal_id,
        mission_fingerprint=proposal.mission_fingerprint,
        context_pack_fingerprint=proposal.context_pack_fingerprint,
        steps=steps,
        caveats=proposal.caveats,
        provider_policy_id=proposal.provider_policy_id,
        provider_policy_fingerprint=proposal.provider_policy_fingerprint,
        provider_fingerprint=proposal.provider_fingerprint,
        proposal_fingerprint=personal_agent_hash(core),
    )


def edit_agent_run_proposal(
    pack: AgentContextPackV1,
    proposal: AgentRunProposalV1,
    steps: tuple[AgentRunStepV1, ...],
) -> AgentRunProposalV1:
    """Apply bounded owner edits without changing mission or target authority."""

    validated_pack = validate_agent_context_pack(pack)
    validated = validate_agent_run_proposal(proposal)
    if (
        validated.mission_fingerprint != validated_pack.mission.fingerprint
        or validated.context_pack_fingerprint != validated_pack.fingerprint
    ):
        raise AgentRunSourceDriftError()
    if type(steps) is not tuple or not 1 <= len(steps) <= MAX_AGENT_RUN_STEPS:
        raise AgentRunProposalInvalidError()
    original = {
        item.step.step_id: item.step
        for item in (
            AgentRunStepSnapshotV1(step=item, state=AgentRunStepStateV1.PENDING)
            for item in validated.steps
        )
    }
    if any(
        type(step)
        not in {
            AgentClarifyStepV1,
            AgentCheckpointStepV1,
            AgentHoldStepV1,
            AgentStage19ActionStepV1,
        }
        for step in steps
    ):
        raise AgentRunProposalInvalidError()
    if not {step.step_id for step in steps} <= set(original):
        raise AgentRunProposalInvalidError()
    for step in steps:
        previous = original[step.step_id]
        if type(previous) is AgentStage19ActionStepV1:
            if type(step) is AgentStage19ActionStepV1:
                previous_action = previous.action
                current_action = step.action
                if (
                    previous_action.action_kind != current_action.action_kind
                    or previous_action.repository != current_action.repository
                    or previous_action.issue_number != current_action.issue_number
                ):
                    raise AgentRunActionNotAllowedError()
            elif type(step) is not AgentHoldStepV1:
                raise AgentRunActionNotAllowedError()
        elif type(step) is AgentStage19ActionStepV1:
            raise AgentRunActionNotAllowedError()
    edited = _proposal_from_steps(validated, steps)
    # Re-run the planner's exact target/capability checks through the same
    # immutable Context Pack boundary without importing any Stage 19 adapter.
    for step in edited.steps:
        if type(step) is not AgentStage19ActionStepV1:
            continue
        action = step.action
        target = next(
            (
                target
                for target in validated_pack.mission.external_targets
                if cast(AgentStage19ActionCapabilityV1, target.action_kind).value
                == cast(AgentStage19ActionCapabilityV1, action.action_kind).value
                and target.repository.casefold() == action.repository.casefold()
                and target.issue_number == action.issue_number
            ),
            None,
        )
        if target is None:
            raise AgentRunActionNotAllowedError()
    return edited


def _stage19_bindings(pack: AgentContextPackV1) -> tuple[AgentStage18BindingV1, ...]:
    return tuple(
        AgentStage18BindingV1(
            item_id=item.item_id,
            accepted_item_fingerprint=item.accepted_item_fingerprint,
            execution_context_fingerprint=personal_agent_hash(item.execution.as_dict()),
        )
        for item in pack.selected_items
    )


def _source_matches(snapshot: AgentRunSnapshotV1, pack: AgentContextPackV1) -> bool:
    validated = validate_agent_context_pack(pack)
    return (
        validated.mission.fingerprint == snapshot.mission.fingerprint
        and validated.fingerprint == snapshot.context_pack_fingerprint
        and validated.mission.planning_snapshot_id == snapshot.planning_snapshot_id
        and validated.mission.planning_snapshot_fingerprint
        == snapshot.planning_snapshot_fingerprint
        and validated.stage19.policy_id == snapshot.stage19_policy_id
        and validated.stage19.policy_fingerprint == snapshot.stage19_policy_fingerprint
        and personal_agent_hash(validated.stage19.as_dict()) == snapshot.stage19_catalog_fingerprint
        and _stage19_bindings(validated) == snapshot.stage18_bindings
    )


def _current_step_index(snapshot: AgentRunSnapshotV1) -> int:
    matches = [
        index
        for index, item in enumerate(snapshot.steps)
        if item.state
        in {
            AgentRunStepStateV1.CURRENT,
            AgentRunStepStateV1.WAITING_OWNER,
            AgentRunStepStateV1.WAITING_STAGE19,
        }
    ]
    if len(matches) != 1:
        raise AgentRunStepNotCurrentError()
    return matches[0]


def _next_pending_index(steps: tuple[AgentRunStepSnapshotV1, ...]) -> int | None:
    for index, item in enumerate(steps):
        if item.state is AgentRunStepStateV1.PENDING:
            return index
    return None


def _state_for_step(step: AgentRunStepSnapshotV1) -> tuple[AgentRunStateV1, AgentRunStepStateV1]:
    kind = step.step.kind
    if kind is AgentRunStepKindV1.CLARIFY or kind is AgentRunStepKindV1.CHECKPOINT:
        return AgentRunStateV1.WAITING_OWNER, AgentRunStepStateV1.WAITING_OWNER
    if kind is AgentRunStepKindV1.HOLD:
        return AgentRunStateV1.WAITING_OWNER, AgentRunStepStateV1.WAITING_OWNER
    return AgentRunStateV1.ACTIVE, AgentRunStepStateV1.CURRENT


def _receipt_can_continue(receipt: AgentStage19ReceiptRefV1) -> bool:
    state = cast(ActionReceiptStateV1, receipt.state)
    return state in {
        ActionReceiptStateV1.EXECUTED,
        ActionReceiptStateV1.ALREADY_SATISFIED,
        ActionReceiptStateV1.FAILED_BEFORE_SEND,
        ActionReceiptStateV1.FAILED_CONFIRMED_NO_MUTATION,
        ActionReceiptStateV1.RECONCILED_EXECUTED,
        ActionReceiptStateV1.RECONCILED_NOT_EXECUTED,
    }


def _step_with(
    value: AgentRunStepSnapshotV1,
    *,
    state: AgentRunStepStateV1,
    owner_answer: str | None = None,
    resolution_note: str | None = None,
    receipt_ref: AgentStage19ReceiptRefV1 | None = None,
) -> AgentRunStepSnapshotV1:
    return AgentRunStepSnapshotV1(
        step=value.step,
        state=state,
        owner_answer=owner_answer,
        resolution_note=resolution_note,
        receipt_ref=receipt_ref,
    )


def _block_active_step(
    value: AgentRunStepSnapshotV1,
    *,
    resolution_note: str,
) -> AgentRunStepSnapshotV1:
    current_state = cast(AgentRunStepStateV1, value.state)
    is_active = current_state in {
        AgentRunStepStateV1.CURRENT,
        AgentRunStepStateV1.WAITING_OWNER,
        AgentRunStepStateV1.WAITING_STAGE19,
    }
    return _step_with(
        value,
        state=AgentRunStepStateV1.BLOCKED if is_active else current_state,
        owner_answer=value.owner_answer,
        resolution_note=resolution_note if is_active else value.resolution_note,
        receipt_ref=value.receipt_ref,
    )


def _evolve(
    snapshot: AgentRunSnapshotV1,
    *,
    now: datetime,
    state: AgentRunStateV1,
    steps: tuple[AgentRunStepSnapshotV1, ...] | None = None,
    receipt_refs: tuple[AgentStage19ReceiptRefV1, ...] | None = None,
    supersedes_run_id: UUID | str | None = None,
) -> AgentRunSnapshotV1:
    return AgentRunSnapshotV1.create(
        run_id=snapshot.run_id,
        revision=snapshot.revision + 1,
        mission=snapshot.mission,
        context_pack_fingerprint=snapshot.context_pack_fingerprint,
        planning_snapshot_id=snapshot.planning_snapshot_id,
        planning_snapshot_fingerprint=snapshot.planning_snapshot_fingerprint,
        planning_policy_id=snapshot.planning_policy_id,
        planning_policy_fingerprint=snapshot.planning_policy_fingerprint,
        stage18_bindings=snapshot.stage18_bindings,
        stage19_policy_id=snapshot.stage19_policy_id,
        stage19_policy_fingerprint=snapshot.stage19_policy_fingerprint,
        stage19_catalog_fingerprint=snapshot.stage19_catalog_fingerprint,
        proposal_id=snapshot.proposal_id,
        proposal_fingerprint=snapshot.proposal_fingerprint,
        provider_fingerprint=snapshot.provider_fingerprint,
        steps=snapshot.steps if steps is None else steps,
        receipt_refs=snapshot.receipt_refs if receipt_refs is None else receipt_refs,
        state=state,
        accepted_at=snapshot.accepted_at,
        updated_at=now,
        supersedes_run_id=snapshot.supersedes_run_id
        if supersedes_run_id is None
        else supersedes_run_id,
    )


class AgentRunService:
    """Explicit foreground lifecycle service with no provider progression."""

    def __init__(
        self,
        store: AgentRunStorePort,
        *,
        stage19: AgentStage19ActionBridgePort | None = None,
        clock: object | None = None,
    ) -> None:
        self._store = store
        self._stage19 = stage19
        self._clock = clock

    def _now(self) -> datetime:
        if callable(self._clock):
            value = self._clock()
            if isinstance(value, datetime):
                return _timestamp(value, error=AgentRunError)
        return datetime.now(UTC)

    def latest(self, run_id: UUID | str) -> AgentRunSnapshotV1:
        snapshot = self._store.latest(run_id)
        if snapshot is None:
            raise AgentRunNotFoundError()
        return snapshot

    def current(self) -> AgentRunSnapshotV1 | None:
        return self._store.current()

    def review(
        self,
        pack: AgentContextPackV1,
        proposal: AgentRunProposalV1,
        *,
        steps: tuple[AgentRunStepV1, ...] | None = None,
    ) -> AgentRunProposalV1:
        """Revalidate a proposal and optionally apply bounded owner edits."""

        if steps is None:
            validated = validate_agent_run_proposal(proposal)
            validated_pack = validate_agent_context_pack(pack)
            if (
                validated.mission_fingerprint != validated_pack.mission.fingerprint
                or validated.context_pack_fingerprint != validated_pack.fingerprint
            ):
                raise AgentRunSourceDriftError()
            return validated
        return edit_agent_run_proposal(pack, proposal, steps)

    def accept(
        self,
        pack: AgentContextPackV1,
        proposal: AgentRunProposalV1,
        *,
        operation_id: str,
        run_id: UUID | str | None = None,
        supersedes_run_id: UUID | str | None = None,
    ) -> AgentRunSnapshotV1:
        """Create one immutable accepted Run; acceptance never starts it."""

        validated_pack = validate_agent_context_pack(pack)
        reviewed = self.review(validated_pack, proposal)
        resolved_run_id = uuid7() if run_id is None else _uuid7(run_id, error=AgentRunError)
        snapshot = AgentRunSnapshotV1.accepted_from(
            validated_pack,
            reviewed,
            run_id=resolved_run_id,
            now=self._now(),
            supersedes_run_id=supersedes_run_id,
        )
        return self._store.append(
            snapshot,
            operation_id=_operation(operation_id),
            event_kind=AgentRunEventKindV1.ACCEPT,
        )

    def _load_current_for_action(
        self,
        pack: AgentContextPackV1,
        run_id: UUID | str,
        *,
        allow_paused: bool = False,
    ) -> AgentRunSnapshotV1:
        snapshot = self.latest(run_id)
        current = self._store.current()
        if current is None or current.run_id != snapshot.run_id:
            raise AgentRunNotCurrentError()
        if snapshot.state is AgentRunStateV1.PAUSED and allow_paused:
            return snapshot
        if snapshot.state not in {
            AgentRunStateV1.ACTIVE,
            AgentRunStateV1.WAITING_OWNER,
            AgentRunStateV1.WAITING_STAGE19,
        }:
            raise AgentRunInvalidTransitionError()
        if not _source_matches(snapshot, pack):
            raise AgentRunSourceDriftError()
        return snapshot

    def start(
        self,
        pack: AgentContextPackV1,
        run_id: UUID | str,
        *,
        operation_id: str,
    ) -> AgentRunSnapshotV1:
        snapshot = self.latest(run_id)
        if snapshot.state is not AgentRunStateV1.ACCEPTED:
            raise AgentRunInvalidTransitionError()
        current = self._store.current()
        if current is None or current.run_id != snapshot.run_id:
            raise AgentRunNotCurrentError()
        if not _source_matches(snapshot, pack):
            raise AgentRunSourceDriftError()
        index = _next_pending_index(snapshot.steps)
        if index is None:
            raise AgentRunInvalidTransitionError()
        pending = snapshot.steps[index]
        state, step_state = _state_for_step(pending)
        steps = list(snapshot.steps)
        steps[index] = _step_with(pending, state=step_state)
        updated = _evolve(snapshot, now=self._now(), state=state, steps=tuple(steps))
        return self._store.append(
            updated,
            operation_id=_operation(operation_id),
            event_kind=AgentRunEventKindV1.START,
        )

    def pause(
        self,
        run_id: UUID | str,
        *,
        operation_id: str,
    ) -> AgentRunSnapshotV1:
        snapshot = self.latest(run_id)
        if snapshot.state not in {
            AgentRunStateV1.ACTIVE,
            AgentRunStateV1.WAITING_OWNER,
            AgentRunStateV1.WAITING_STAGE19,
        }:
            raise AgentRunInvalidTransitionError()
        updated = _evolve(snapshot, now=self._now(), state=AgentRunStateV1.PAUSED)
        return self._store.append(
            updated,
            operation_id=_operation(operation_id),
            event_kind=AgentRunEventKindV1.PAUSE,
        )

    def resume(
        self,
        pack: AgentContextPackV1,
        run_id: UUID | str,
        *,
        operation_id: str,
    ) -> AgentRunSnapshotV1:
        snapshot = self.latest(run_id)
        if snapshot.state is not AgentRunStateV1.PAUSED:
            raise AgentRunInvalidTransitionError()
        if not _source_matches(snapshot, pack):
            raise AgentRunSourceDriftError()
        step = snapshot.current_step
        if step is None:
            raise AgentRunInvalidTransitionError()
        step_state = cast(AgentRunStepStateV1, step.state)
        if step_state is AgentRunStepStateV1.WAITING_STAGE19:
            state = AgentRunStateV1.WAITING_STAGE19
        elif step_state is AgentRunStepStateV1.WAITING_OWNER:
            state = AgentRunStateV1.WAITING_OWNER
        else:
            state, _ = _state_for_step(step)
        updated = _evolve(snapshot, now=self._now(), state=state)
        return self._store.append(
            updated,
            operation_id=_operation(operation_id),
            event_kind=AgentRunEventKindV1.RESUME,
        )

    def answer(
        self,
        pack: AgentContextPackV1,
        run_id: UUID | str,
        answer: str,
        *,
        operation_id: str,
    ) -> AgentRunSnapshotV1:
        snapshot = self._load_current_for_action(pack, run_id)
        index = _current_step_index(snapshot)
        current = snapshot.steps[index]
        if (
            current.state is not AgentRunStepStateV1.WAITING_OWNER
            or type(current.step) is not AgentClarifyStepV1
        ):
            raise AgentRunStepNotCurrentError()
        normalized = _safe_text(answer, maximum=MAX_AGENT_RUN_ANSWER_BYTES, error=AgentRunError)
        steps = list(snapshot.steps)
        steps[index] = _step_with(
            current,
            state=AgentRunStepStateV1.COMPLETED,
            owner_answer=normalized,
            resolution_note="owner_answered",
        )
        next_index = _next_pending_index(tuple(steps))
        next_state = AgentRunStateV1.READY_TO_COMPLETE
        if next_index is not None:
            next_step = steps[next_index]
            next_state, next_step_state = _state_for_step(next_step)
            steps[next_index] = _step_with(next_step, state=next_step_state)
        updated = _evolve(snapshot, now=self._now(), state=next_state, steps=tuple(steps))
        return self._store.append(
            updated,
            operation_id=_operation(operation_id),
            event_kind=AgentRunEventKindV1.ANSWER,
        )

    def continue_run(
        self,
        pack: AgentContextPackV1,
        run_id: UUID | str,
        *,
        operation_id: str,
    ) -> AgentRunSnapshotV1:
        snapshot = self._load_current_for_action(pack, run_id)
        if snapshot.current_step is None:
            if snapshot.state is not AgentRunStateV1.ACTIVE:
                raise AgentRunInvalidTransitionError()
            next_index = _next_pending_index(snapshot.steps)
            if next_index is None:
                raise AgentRunInvalidTransitionError()
            steps = list(snapshot.steps)
            next_step = steps[next_index]
            next_state, next_step_state = _state_for_step(next_step)
            steps[next_index] = _step_with(next_step, state=next_step_state)
            updated = _evolve(
                snapshot,
                now=self._now(),
                state=next_state,
                steps=tuple(steps),
            )
            return self._store.append(
                updated,
                operation_id=_operation(operation_id),
                event_kind=AgentRunEventKindV1.CONTINUE,
            )
        index = _current_step_index(snapshot)
        current = snapshot.steps[index]
        if current.state is AgentRunStepStateV1.WAITING_STAGE19:
            receipt = current.receipt_ref
            if receipt is None:
                raise AgentRunReconciliationRequiredError()
            if not _receipt_can_continue(receipt):
                if cast(ActionReceiptStateV1, receipt.state) in {
                    ActionReceiptStateV1.OUTCOME_UNCERTAIN,
                    ActionReceiptStateV1.RECONCILIATION_AMBIGUOUS,
                }:
                    raise AgentRunReceiptUncertainError()
                raise AgentRunReconciliationRequiredError()
        elif current.state is not AgentRunStepStateV1.WAITING_OWNER:
            raise AgentRunStepNotCurrentError()
        if current.state is AgentRunStepStateV1.WAITING_OWNER and current.step.kind in {
            AgentRunStepKindV1.CLARIFY,
            AgentRunStepKindV1.HOLD,
        }:
            raise AgentRunStepNotCurrentError()
        steps = list(snapshot.steps)
        steps[index] = _step_with(
            current,
            state=AgentRunStepStateV1.COMPLETED,
            resolution_note="owner_continued",
            owner_answer=current.owner_answer,
            receipt_ref=current.receipt_ref,
        )
        next_index = _next_pending_index(tuple(steps))
        next_state = AgentRunStateV1.READY_TO_COMPLETE
        if next_index is not None:
            next_step = steps[next_index]
            next_state, next_step_state = _state_for_step(next_step)
            steps[next_index] = _step_with(next_step, state=next_step_state)
        updated = _evolve(snapshot, now=self._now(), state=next_state, steps=tuple(steps))
        return self._store.append(
            updated,
            operation_id=_operation(operation_id),
            event_kind=AgentRunEventKindV1.CONTINUE,
        )

    def skip(
        self,
        pack: AgentContextPackV1,
        run_id: UUID | str,
        reason: str,
        *,
        operation_id: str,
    ) -> AgentRunSnapshotV1:
        snapshot = self._load_current_for_action(pack, run_id)
        index = _current_step_index(snapshot)
        current = snapshot.steps[index]
        if current.state is AgentRunStepStateV1.WAITING_STAGE19:
            receipt = current.receipt_ref
            if receipt is None:
                raise AgentRunReconciliationRequiredError()
            if not _receipt_can_continue(receipt):
                if cast(ActionReceiptStateV1, receipt.state) in {
                    ActionReceiptStateV1.OUTCOME_UNCERTAIN,
                    ActionReceiptStateV1.RECONCILIATION_AMBIGUOUS,
                }:
                    raise AgentRunReceiptUncertainError()
                raise AgentRunReconciliationRequiredError()
        note = _safe_text(reason, maximum=MAX_AGENT_RUN_NOTE_BYTES, error=AgentRunError)
        steps = list(snapshot.steps)
        steps[index] = _step_with(
            current,
            state=AgentRunStepStateV1.SKIPPED,
            resolution_note=note,
            owner_answer=current.owner_answer,
            receipt_ref=current.receipt_ref,
        )
        next_state = (
            AgentRunStateV1.READY_TO_COMPLETE
            if _next_pending_index(tuple(steps)) is None
            else AgentRunStateV1.ACTIVE
        )
        updated = _evolve(snapshot, now=self._now(), state=next_state, steps=tuple(steps))
        return self._store.append(
            updated,
            operation_id=_operation(operation_id),
            event_kind=AgentRunEventKindV1.SKIP,
        )

    def complete(
        self,
        run_id: UUID | str,
        *,
        operation_id: str,
    ) -> AgentRunSnapshotV1:
        snapshot = self.latest(run_id)
        if snapshot.state is not AgentRunStateV1.READY_TO_COMPLETE:
            raise AgentRunInvalidTransitionError()
        updated = _evolve(snapshot, now=self._now(), state=AgentRunStateV1.COMPLETED)
        return self._store.append(
            updated,
            operation_id=_operation(operation_id),
            event_kind=AgentRunEventKindV1.COMPLETE,
        )

    def abandon(
        self,
        run_id: UUID | str,
        *,
        operation_id: str,
    ) -> AgentRunSnapshotV1:
        snapshot = self.latest(run_id)
        if snapshot.state not in {
            AgentRunStateV1.ACTIVE,
            AgentRunStateV1.WAITING_OWNER,
            AgentRunStateV1.WAITING_STAGE19,
            AgentRunStateV1.PAUSED,
        }:
            raise AgentRunInvalidTransitionError()
        steps = tuple(
            _block_active_step(item, resolution_note="run_abandoned") for item in snapshot.steps
        )
        updated = _evolve(snapshot, now=self._now(), state=AgentRunStateV1.ABANDONED, steps=steps)
        return self._store.append(
            updated,
            operation_id=_operation(operation_id),
            event_kind=AgentRunEventKindV1.ABANDON,
        )

    def supersede(
        self,
        run_id: UUID | str,
        *,
        operation_id: str,
    ) -> AgentRunSnapshotV1:
        snapshot = self.latest(run_id)
        if snapshot.state in {
            AgentRunStateV1.COMPLETED,
            AgentRunStateV1.ABANDONED,
            AgentRunStateV1.SUPERSEDED,
        }:
            raise AgentRunInvalidTransitionError()
        steps = tuple(
            _block_active_step(item, resolution_note="run_superseded") for item in snapshot.steps
        )
        updated = _evolve(snapshot, now=self._now(), state=AgentRunStateV1.SUPERSEDED, steps=steps)
        return self._store.append(
            updated,
            operation_id=_operation(operation_id),
            event_kind=AgentRunEventKindV1.SUPERSEDE,
        )

    def _action_intent(
        self,
        snapshot: AgentRunSnapshotV1,
        pack: AgentContextPackV1,
        step: AgentRunStepSnapshotV1,
    ) -> ActionIntentV1:
        if type(step.step) is not AgentStage19ActionStepV1:
            raise AgentRunActionNotAllowedError()
        action = step.step.action
        try:
            action_kind = ActionKindV1(
                cast(AgentStage19ActionCapabilityV1, action.action_kind).value
            )
        except (TypeError, ValueError) as exc:
            raise AgentRunActionNotAllowedError() from exc
        allowed = {
            cast(AgentStage19ActionCapabilityV1, item.action_kind).value
            for item in pack.stage19.action_catalog
        }
        target = next(
            (
                item
                for item in pack.mission.external_targets
                if cast(AgentStage19ActionCapabilityV1, item.action_kind).value == action_kind.value
                and item.repository.casefold() == action.repository.casefold()
                and item.issue_number == action.issue_number
            ),
            None,
        )
        if action_kind.value not in allowed or target is None or not pack.stage19.ready:
            raise AgentRunStage19UnavailableError()
        selected = pack.mission.selected_items[0]
        stage18 = next(
            item for item in snapshot.stage18_bindings if item.item_id == selected.item_id
        )
        provenance = ActionProvenanceV1(
            planning_snapshot_id=str(snapshot.planning_snapshot_id),
            planning_snapshot_fingerprint=snapshot.planning_snapshot_fingerprint,
            item_id=selected.item_id,
            accepted_item_fingerprint=selected.accepted_item_fingerprint,
            execution_context_id=f"stage18:{selected.item_id}",
            execution_context_fingerprint=stage18.execution_context_fingerprint,
        )
        operation_id = f"stage20/{snapshot.run_id}/step/{step.step.step_id}"
        if action_kind is ActionKindV1.GITHUB_ISSUE_CREATE:
            return ActionIntentV1(
                ACTION_INTENT_CONTRACT_VERSION,
                operation_id,
                action_kind,
                ACTION_GATEWAY_CONNECTOR,
                action.repository,
                title=cast(str, action.title),
                body=cast(str, action.body),
                provenance=provenance,
            )
        if action_kind is ActionKindV1.GITHUB_ISSUE_COMMENT:
            return ActionIntentV1(
                ACTION_INTENT_CONTRACT_VERSION,
                operation_id,
                action_kind,
                ACTION_GATEWAY_CONNECTOR,
                action.repository,
                issue_number=cast(int, action.issue_number),
                comment=cast(str, action.comment),
                provenance=provenance,
            )
        return ActionIntentV1(
            ACTION_INTENT_CONTRACT_VERSION,
            operation_id,
            action_kind,
            ACTION_GATEWAY_CONNECTOR,
            action.repository,
            issue_number=cast(int, action.issue_number),
            desired_state=cast(IssueStateV1, action.desired_state),
            provenance=provenance,
        )

    def prepare_stage19_action(
        self,
        pack: AgentContextPackV1,
        run_id: UUID | str,
        *,
        operation_id: str,
    ) -> AgentStage19PreparedActionV1:
        if self._stage19 is None:
            raise AgentRunStage19UnavailableError()
        snapshot = self._load_current_for_action(pack, run_id)
        index = _current_step_index(snapshot)
        current = snapshot.steps[index]
        if (
            snapshot.state is not AgentRunStateV1.ACTIVE
            or current.state is not AgentRunStepStateV1.CURRENT
        ):
            raise AgentRunStepNotCurrentError()
        intent = self._action_intent(snapshot, pack, current)
        prepared = self._stage19.prepare(intent)
        # Prepare is transient Stage 19 page state.  Only a safe event marker
        # is persisted; no prepared DTO, credential profile, or token crosses
        # the store boundary.
        updated = _evolve(snapshot, now=self._now(), state=AgentRunStateV1.ACTIVE)
        self._store.append(
            updated,
            operation_id=_operation(operation_id),
            event_kind=AgentRunEventKindV1.PREPARE_RESULT,
        )
        return AgentStage19PreparedActionV1(prepared)

    def _assert_prepared(
        self,
        snapshot: AgentRunSnapshotV1,
        pack: AgentContextPackV1,
        prepared: AgentStage19PreparedActionV1,
    ) -> ActionIntentV1:
        index = _current_step_index(snapshot)
        current = snapshot.steps[index]
        if current.state not in {
            AgentRunStepStateV1.CURRENT,
            AgentRunStepStateV1.WAITING_STAGE19,
        }:
            raise AgentRunPrepareRequiredError()
        expected = self._action_intent(snapshot, pack, current)
        if prepared.prepared.intent_fingerprint != expected.intent_fingerprint:
            raise AgentRunPrepareRequiredError()
        return expected

    def issue_stage19_confirmation(
        self,
        prepared: AgentStage19PreparedActionV1,
    ) -> str:
        if self._stage19 is None:
            raise AgentRunStage19UnavailableError()
        try:
            return self._stage19.issue_confirmation(prepared.prepared)
        except PersonalAgentError:
            raise
        except Exception as exc:
            raise AgentRunStage19UnavailableError() from exc

    def execute_stage19_action(
        self,
        pack: AgentContextPackV1,
        run_id: UUID | str,
        prepared: AgentStage19PreparedActionV1,
        confirmation: object,
        *,
        operation_id: str,
    ) -> AgentRunSnapshotV1:
        if self._stage19 is None:
            raise AgentRunStage19UnavailableError()
        snapshot = self._load_current_for_action(pack, run_id)
        self._assert_prepared(snapshot, pack, prepared)
        result = self._stage19.execute(prepared.prepared, confirmation)
        ref = AgentStage19ReceiptRefV1.from_result(result)
        index = _current_step_index(snapshot)
        current = snapshot.steps[index]
        steps = list(snapshot.steps)
        steps[index] = _step_with(
            current,
            state=AgentRunStepStateV1.WAITING_STAGE19,
            owner_answer=current.owner_answer,
            resolution_note="stage19_result_observed",
            receipt_ref=ref,
        )
        refs = (*snapshot.receipt_refs, ref)
        if len(refs) > MAX_AGENT_RUN_RECEIPT_REFS:
            raise AgentRunStoreError()
        updated = _evolve(
            snapshot,
            now=self._now(),
            state=AgentRunStateV1.WAITING_STAGE19,
            steps=tuple(steps),
            receipt_refs=refs,
        )
        return self._store.append(
            updated,
            operation_id=_operation(operation_id),
            event_kind=AgentRunEventKindV1.STAGE19_RESULT,
        )

    def reconcile_stage19_action(
        self,
        pack: AgentContextPackV1,
        run_id: UUID | str,
        prepared: AgentStage19PreparedActionV1,
        *,
        operation_id: str,
    ) -> AgentRunSnapshotV1:
        if self._stage19 is None:
            raise AgentRunStage19UnavailableError()
        snapshot = self._load_current_for_action(pack, run_id)
        self._assert_prepared(snapshot, pack, prepared)
        current = snapshot.current_step
        if current is None or current.state is not AgentRunStepStateV1.WAITING_STAGE19:
            raise AgentRunReconciliationRequiredError()
        result = self._stage19.reconcile(prepared.prepared)
        ref = AgentStage19ReceiptRefV1.from_result(result)
        index = _current_step_index(snapshot)
        steps = list(snapshot.steps)
        steps[index] = _step_with(
            steps[index],
            state=AgentRunStepStateV1.WAITING_STAGE19,
            owner_answer=steps[index].owner_answer,
            resolution_note="stage19_reconciliation_observed",
            receipt_ref=ref,
        )
        refs = (*snapshot.receipt_refs, ref)
        updated = _evolve(
            snapshot,
            now=self._now(),
            state=AgentRunStateV1.WAITING_STAGE19,
            steps=tuple(steps),
            receipt_refs=refs,
        )
        return self._store.append(
            updated,
            operation_id=_operation(operation_id),
            event_kind=AgentRunEventKindV1.RECONCILE_RESULT,
        )

    def prepare_compensation(
        self,
        run_id: UUID | str,
        parent_receipt_id: UUID,
        *,
        operation_id: str,
    ) -> AgentStage19PreparedActionV1:
        if self._stage19 is None:
            raise AgentRunStage19UnavailableError()
        snapshot = self.latest(run_id)
        if snapshot.state not in {
            AgentRunStateV1.ACTIVE,
            AgentRunStateV1.WAITING_STAGE19,
            AgentRunStateV1.PAUSED,
        }:
            raise AgentRunInvalidTransitionError()
        prepared = self._stage19.prepare_compensation(parent_receipt_id, _operation(operation_id))
        return AgentStage19PreparedActionV1(prepared)

    def execute_compensation(
        self,
        run_id: UUID | str,
        prepared: AgentStage19PreparedActionV1,
        confirmation: object,
        parent_receipt_id: UUID,
        *,
        operation_id: str,
    ) -> AgentRunSnapshotV1:
        if self._stage19 is None:
            raise AgentRunStage19UnavailableError()
        snapshot = self.latest(run_id)
        if snapshot.state not in {
            AgentRunStateV1.ACTIVE,
            AgentRunStateV1.WAITING_STAGE19,
            AgentRunStateV1.PAUSED,
        }:
            raise AgentRunInvalidTransitionError()
        result = self._stage19.execute_compensation(
            prepared.prepared,
            confirmation,
            parent_receipt_id,
        )
        ref = AgentStage19ReceiptRefV1.from_result(result)
        refs = (*snapshot.receipt_refs, ref)
        updated = _evolve(
            snapshot,
            now=self._now(),
            state=cast(AgentRunStateV1, snapshot.state),
            receipt_refs=refs,
        )
        return self._store.append(
            updated,
            operation_id=_operation(operation_id),
            event_kind=AgentRunEventKindV1.COMPENSATION_RESULT,
        )


def serialize_agent_run_snapshot(value: object) -> bytes:
    """Serialize a validated safe snapshot without transient Stage 19 state."""

    if type(value) is not AgentRunSnapshotV1:
        raise AgentRunSnapshotInvalidError()
    import json

    try:
        return json.dumps(
            value.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError, OverflowError) as exc:
        raise AgentRunSnapshotInvalidError() from exc


__all__ = [
    "AGENT_RUN_CONTRACT_VERSION",
    "AGENT_RUN_RECEIPT_REF_CONTRACT_VERSION",
    "AGENT_RUN_SNAPSHOT_CONTRACT_VERSION",
    "AgentRunActionNotAllowedError",
    "AgentRunConflictError",
    "AgentRunError",
    "AgentRunEventKindV1",
    "AgentRunInvalidTransitionError",
    "AgentRunNotCurrentError",
    "AgentRunNotFoundError",
    "AgentRunPrepareRequiredError",
    "AgentRunProposalInvalidError",
    "AgentRunReceiptUncertainError",
    "AgentRunReconciliationRequiredError",
    "AgentRunService",
    "AgentRunSnapshotInvalidError",
    "AgentRunSnapshotV1",
    "AgentRunSourceDriftError",
    "AgentRunStage19UnavailableError",
    "AgentRunStateV1",
    "AgentRunStepNotCurrentError",
    "AgentRunStepSnapshotV1",
    "AgentRunStepStateV1",
    "AgentRunStoreError",
    "AgentRunStorePort",
    "AgentStage18BindingV1",
    "AgentStage19ActionBridgePort",
    "AgentStage19PreparedActionV1",
    "AgentStage19ReceiptRefV1",
    "edit_agent_run_proposal",
    "serialize_agent_run_snapshot",
]
