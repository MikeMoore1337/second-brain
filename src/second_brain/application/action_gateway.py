"""Provider-neutral Stage 19 action gateway core.

The module owns the closed action vocabulary, strict wire DTOs, canonical
fingerprints, confirmation tokens, and the at-most-once execution seam.  It
does not import Web, GitHub, vault, Git, an LLM, or a database.  Provider
connectors implement the small protocol at the bottom of the module.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final, Protocol, cast
from uuid import UUID, uuid7

from second_brain.domain.models import parse_rfc3339, parse_uuid7

ACTION_INTENT_CONTRACT_VERSION: Final[str] = "action-intent-v1"
PREPARED_ACTION_CONTRACT_VERSION: Final[str] = "prepared-external-action-v1"
RECEIPT_CONTRACT_VERSION: Final[str] = "external-action-receipt-v1"
ACTION_GATEWAY_CONNECTOR: Final[str] = "github_issues"
ACTION_GATEWAY_POLICY_ID: Final[str] = "github-issues-v1"
ACTION_GATEWAY_CREDENTIAL_PROFILE_ID: Final[str] = "github-actions-primary"
ACTION_GATEWAY_CONFIRM_PURPOSE: Final[str] = "action-gateway-confirm-v1"
ACTION_GATEWAY_CONFIRM_VERSION: Final[int] = 1
ACTION_GATEWAY_MAX_CONFIRM_TTL_SECONDS: Final[int] = 300
ACTION_GATEWAY_DEFAULT_CONFIRM_TTL_SECONDS: Final[int] = 300

MAX_OPERATION_ID_BYTES: Final[int] = 256
MAX_REPOSITORY_BYTES: Final[int] = 200
MAX_TITLE_BYTES: Final[int] = 256
MAX_BODY_BYTES: Final[int] = 64 * 1024
MAX_COMMENT_BYTES: Final[int] = 64 * 1024
MAX_PREVIEW_BYTES: Final[int] = 16 * 1024
MAX_NODE_ID_BYTES: Final[int] = 256
MAX_SAFE_IDENTITY_KEYS: Final[int] = 12
MAX_SAFE_IDENTITY_VALUE_BYTES: Final[int] = 512
MAX_ERROR_CODE_BYTES: Final[int] = 80

_ASCII_OPERATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z", re.ASCII
)
_REPOSITORY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z",
    re.ASCII,
)
_NODE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9_-][A-Za-z0-9._:-]{0,255}\Z", re.ASCII
)
_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SAFE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z", re.ASCII
)
_ERROR_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9_]{1,80}\Z", re.ASCII)
_MARKER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"<!-- second-brain-action:[0-9a-f-]{36} -->\Z", re.ASCII
)

ACTION_GATEWAY_POLICY_DOCUMENT: Final[dict[str, object]] = {
    "contract": "action-gateway-v1",
    "connector": ACTION_GATEWAY_CONNECTOR,
    "policy_id": ACTION_GATEWAY_POLICY_ID,
    "risk": "controlled_write",
    "action_kinds": [
        "github.issue.comment",
        "github.issue.create",
        "github.issue.set_state",
    ],
    "confirmation_purpose": ACTION_GATEWAY_CONFIRM_PURPOSE,
    "confirmation_ttl_seconds": ACTION_GATEWAY_MAX_CONFIRM_TTL_SECONDS,
    "max_body_bytes": MAX_BODY_BYTES,
    "max_records": 32768,
}


class ActionGatewayError(ValueError):
    """Base safe error for the Stage 19 core boundary."""

    code: str = "action_gateway_invalid"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class ActionGatewayInvalidRequestError(ActionGatewayError):
    """The strict action DTO or a bounded value is invalid."""

    code = "invalid_action"


class ActionGatewayConfirmationError(ActionGatewayError):
    """The confirmation token is malformed, expired, mismatched, or replayed."""

    code = "confirmation_invalid"


class ActionGatewayConflictError(ActionGatewayError):
    """One operation identity was reused with a different canonical intent."""

    code = "action_conflict"


class ActionGatewayTargetChangedError(ActionGatewayError):
    """The exact provider target no longer matches the prepared target."""

    code = "target_changed"


class ActionGatewayConnectorError(ActionGatewayError):
    """A connector reports a bounded external outcome."""

    def __init__(self, outcome: ActionExecutionOutcomeV1, safe_error_code: str) -> None:
        self.outcome = _as_execution_outcome(outcome)
        self.safe_error_code = _safe_error_code(safe_error_code)
        super().__init__(self.safe_error_code)


class ActionKindV1(StrEnum):
    """The complete Stage 19 v1 mutation catalog."""

    GITHUB_ISSUE_CREATE = "github.issue.create"
    GITHUB_ISSUE_COMMENT = "github.issue.comment"
    GITHUB_ISSUE_SET_STATE = "github.issue.set_state"


class RiskClassV1(StrEnum):
    """Closed risk vocabulary; only controlled writes are in this catalog."""

    READ_ONLY = "read_only"
    CONTROLLED_WRITE = "controlled_write"
    HIGH_IMPACT = "high_impact"
    PROHIBITED = "prohibited"


class IssueStateV1(StrEnum):
    """GitHub issue state accepted by the set-state action."""

    OPEN = "open"
    CLOSED = "closed"


class ReversibilityV1(StrEnum):
    """Bounded language for compensation availability."""

    SUPPORTED = "supported"
    COMPENSATION_ONLY = "compensation_only"
    NOT_SUPPORTED = "not_supported"


class ActionExecutionOutcomeV1(StrEnum):
    """Provider outcome classes used to decide whether another mutation is safe."""

    EXECUTED = "executed"
    ALREADY_SATISFIED = "already_satisfied"
    FAILED_BEFORE_SEND = "failed_before_send"
    FAILED_CONFIRMED_NO_MUTATION = "failed_confirmed_no_mutation"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


class ActionReceiptKindV1(StrEnum):
    """Durable receipt purpose."""

    ACTION = "action"
    RECONCILIATION = "reconciliation"
    COMPENSATION = "compensation"


class ActionReceiptStateV1(StrEnum):
    """Closed durable lifecycle vocabulary."""

    ALREADY_SATISFIED = "already_satisfied"
    EXECUTION_STARTED = "execution_started"
    EXECUTED = "executed"
    FAILED_BEFORE_SEND = "failed_before_send"
    FAILED_CONFIRMED_NO_MUTATION = "failed_confirmed_no_mutation"
    OUTCOME_UNCERTAIN = "outcome_uncertain"
    RECONCILED_EXECUTED = "reconciled_executed"
    RECONCILED_NOT_EXECUTED = "reconciled_not_executed"
    RECONCILIATION_AMBIGUOUS = "reconciliation_ambiguous"
    COMPENSATION_PREPARED = "compensation_prepared"


ActionKind = ActionKindV1
RiskClass = RiskClassV1
ActionReceiptState = ActionReceiptStateV1


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError, OverflowError) as exc:
        raise ActionGatewayInvalidRequestError() from exc


def action_gateway_hash(value: object) -> str:
    """Return the canonical lowercase SHA-256 for Stage 19 identities."""

    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


ACTION_GATEWAY_POLICY_FINGERPRINT: Final[str] = action_gateway_hash(ACTION_GATEWAY_POLICY_DOCUMENT)


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ActionGatewayInvalidRequestError()
        result[key] = value
    return result


def _strict_dict(value: object, expected: set[str]) -> dict[str, object]:
    if type(value) is not dict or any(
        type(key) is not str for key in cast(dict[object, object], value)
    ):
        raise ActionGatewayInvalidRequestError()
    data = cast(dict[str, object], value)
    if not set(data).issubset(expected):
        raise ActionGatewayInvalidRequestError()
    return data


def _json_object(value: object) -> dict[str, object]:
    if type(value) is not str or len(value.encode("utf-8")) > 64 * 1024:
        raise ActionGatewayInvalidRequestError()
    try:
        decoded = json.loads(
            value,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ActionGatewayInvalidRequestError() from exc
    return _strict_dict(decoded, set(decoded) if type(decoded) is dict else set())


def _text(value: object, *, max_bytes: int, pattern: re.Pattern[str] | None = None) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > max_bytes:
        raise ActionGatewayInvalidRequestError()
    normalized = unicodedata.normalize("NFC", value)
    if pattern is not None and pattern.fullmatch(normalized) is None:
        raise ActionGatewayInvalidRequestError()
    if any(ord(character) < 0x20 and character not in "\n\r\t" for character in normalized):
        raise ActionGatewayInvalidRequestError()
    return normalized


def _bounded_text(value: object, *, max_bytes: int, allow_empty: bool = True) -> str:
    if type(value) is not str or (not allow_empty and not value):
        raise ActionGatewayInvalidRequestError()
    if len(value.encode("utf-8")) > max_bytes:
        raise ActionGatewayInvalidRequestError()
    normalized = unicodedata.normalize("NFC", value)
    if any(ord(character) < 0x20 and character not in "\n\r\t" for character in normalized):
        raise ActionGatewayInvalidRequestError()
    return normalized


def _hash(value: object) -> str:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise ActionGatewayInvalidRequestError()
    return value


def _as_action_kind(value: ActionKindV1 | str) -> ActionKindV1:
    try:
        return value if isinstance(value, ActionKindV1) else ActionKindV1(value)
    except (TypeError, ValueError) as exc:
        raise ActionGatewayInvalidRequestError() from exc


def _as_issue_state(value: IssueStateV1 | str) -> IssueStateV1:
    try:
        return value if isinstance(value, IssueStateV1) else IssueStateV1(value)
    except (TypeError, ValueError) as exc:
        raise ActionGatewayInvalidRequestError() from exc


def _as_risk(value: RiskClassV1 | str) -> RiskClassV1:
    try:
        return value if isinstance(value, RiskClassV1) else RiskClassV1(value)
    except (TypeError, ValueError) as exc:
        raise ActionGatewayInvalidRequestError() from exc


def _as_execution_outcome(value: ActionExecutionOutcomeV1 | str) -> ActionExecutionOutcomeV1:
    try:
        return (
            value
            if isinstance(value, ActionExecutionOutcomeV1)
            else ActionExecutionOutcomeV1(value)
        )
    except (TypeError, ValueError) as exc:
        raise ActionGatewayInvalidRequestError() from exc


def _as_receipt_state(value: ActionReceiptStateV1 | str) -> ActionReceiptStateV1:
    try:
        return value if isinstance(value, ActionReceiptStateV1) else ActionReceiptStateV1(value)
    except (TypeError, ValueError) as exc:
        raise ActionGatewayInvalidRequestError() from exc


def _as_receipt_kind(value: ActionReceiptKindV1 | str) -> ActionReceiptKindV1:
    try:
        return value if isinstance(value, ActionReceiptKindV1) else ActionReceiptKindV1(value)
    except (TypeError, ValueError) as exc:
        raise ActionGatewayInvalidRequestError() from exc


def _safe_error_code(value: object) -> str:
    return _text(value, max_bytes=MAX_ERROR_CODE_BYTES, pattern=_ERROR_CODE_PATTERN)


def _uuid7(value: object) -> UUID:
    try:
        return parse_uuid7(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ActionGatewayInvalidRequestError() from exc


def _utc(value: object) -> datetime:
    try:
        parsed = parse_rfc3339(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ActionGatewayInvalidRequestError() from exc
    if parsed.utcoffset() != timedelta(0):
        raise ActionGatewayInvalidRequestError()
    return parsed.astimezone(UTC)


def _wire_time(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _optional_uuid(value: object) -> UUID | None:
    return None if value is None else _uuid7(value)


def _repository(value: object) -> str:
    return _text(value, max_bytes=MAX_REPOSITORY_BYTES, pattern=_REPOSITORY_PATTERN)


def _optional_safe_text(value: object, *, max_bytes: int) -> str | None:
    return None if value is None else _text(value, max_bytes=max_bytes, pattern=_SAFE_ID_PATTERN)


def _marker_for(prepared_action_id: UUID) -> str:
    return f"<!-- second-brain-action:{prepared_action_id} -->"


@dataclass(frozen=True, slots=True)
class ActionProvenanceV1:
    """Optional exact Stage 17/18 references; never an authority grant."""

    planning_snapshot_id: str | None = None
    planning_snapshot_fingerprint: str | None = None
    item_id: str | None = None
    accepted_item_fingerprint: str | None = None
    execution_context_id: str | None = None
    execution_context_fingerprint: str | None = None

    def __post_init__(self) -> None:
        snapshot = (
            None if self.planning_snapshot_id is None else str(_uuid7(self.planning_snapshot_id))
        )
        snapshot_fp = (
            None
            if self.planning_snapshot_fingerprint is None
            else _hash(self.planning_snapshot_fingerprint)
        )
        item = _optional_safe_text(self.item_id, max_bytes=128)
        item_fp = (
            None
            if self.accepted_item_fingerprint is None
            else _hash(self.accepted_item_fingerprint)
        )
        context = _optional_safe_text(self.execution_context_id, max_bytes=128)
        context_fp = (
            None
            if self.execution_context_fingerprint is None
            else _hash(self.execution_context_fingerprint)
        )
        if (
            (snapshot is None) != (snapshot_fp is None)
            or (item is None) != (item_fp is None)
            or (context is None) != (context_fp is None)
        ):
            raise ActionGatewayInvalidRequestError()
        object.__setattr__(self, "planning_snapshot_id", snapshot)
        object.__setattr__(self, "planning_snapshot_fingerprint", snapshot_fp)
        object.__setattr__(self, "item_id", item)
        object.__setattr__(self, "accepted_item_fingerprint", item_fp)
        object.__setattr__(self, "execution_context_id", context)
        object.__setattr__(self, "execution_context_fingerprint", context_fp)

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = {}
        if self.planning_snapshot_id is not None:
            data["planning_snapshot_id"] = self.planning_snapshot_id
            data["planning_snapshot_fingerprint"] = self.planning_snapshot_fingerprint
        if self.item_id is not None:
            data["item_id"] = self.item_id
            data["accepted_item_fingerprint"] = self.accepted_item_fingerprint
        if self.execution_context_id is not None:
            data["execution_context_id"] = self.execution_context_id
            data["execution_context_fingerprint"] = self.execution_context_fingerprint
        return data

    @classmethod
    def from_dict(cls, value: object) -> ActionProvenanceV1:
        allowed = {
            "planning_snapshot_id",
            "planning_snapshot_fingerprint",
            "item_id",
            "accepted_item_fingerprint",
            "execution_context_id",
            "execution_context_fingerprint",
        }
        data = _strict_dict(value, allowed)
        return cls(
            planning_snapshot_id=cast(str | None, data.get("planning_snapshot_id")),
            planning_snapshot_fingerprint=cast(
                str | None, data.get("planning_snapshot_fingerprint")
            ),
            item_id=cast(str | None, data.get("item_id")),
            accepted_item_fingerprint=cast(str | None, data.get("accepted_item_fingerprint")),
            execution_context_id=cast(str | None, data.get("execution_context_id")),
            execution_context_fingerprint=cast(
                str | None, data.get("execution_context_fingerprint")
            ),
        )


@dataclass(frozen=True, slots=True)
class ActionIntentV1:
    """Strict owner intent for exactly one catalog action."""

    contract_version: str
    operation_id: str
    action_kind: ActionKindV1 | str
    connector: str
    repository: str
    issue_number: int | None = None
    title: str | None = None
    body: str | None = None
    comment: str | None = None
    desired_state: IssueStateV1 | str | None = None
    provenance: ActionProvenanceV1 | None = None

    def __post_init__(self) -> None:
        if self.contract_version != ACTION_INTENT_CONTRACT_VERSION:
            raise ActionGatewayInvalidRequestError()
        operation_id = _text(
            self.operation_id, max_bytes=MAX_OPERATION_ID_BYTES, pattern=_ASCII_OPERATION_PATTERN
        )
        action_kind = _as_action_kind(self.action_kind)
        if self.connector != ACTION_GATEWAY_CONNECTOR:
            raise ActionGatewayInvalidRequestError()
        repository = _repository(self.repository)
        issue_number = self.issue_number
        if issue_number is not None and (
            type(issue_number) is not int
            or isinstance(issue_number, bool)
            or not 1 <= issue_number <= 2**31 - 1
        ):
            raise ActionGatewayInvalidRequestError()
        title = self.title
        body = self.body
        comment = self.comment
        desired_state = self.desired_state
        if action_kind is ActionKindV1.GITHUB_ISSUE_CREATE:
            if (
                issue_number is not None
                or title is None
                or body is None
                or comment is not None
                or desired_state is not None
            ):
                raise ActionGatewayInvalidRequestError()
            title = _bounded_text(title, max_bytes=MAX_TITLE_BYTES, allow_empty=False)
            body = _bounded_text(body, max_bytes=MAX_BODY_BYTES)
        elif action_kind is ActionKindV1.GITHUB_ISSUE_COMMENT:
            if (
                issue_number is None
                or title is not None
                or body is not None
                or comment is None
                or desired_state is not None
            ):
                raise ActionGatewayInvalidRequestError()
            comment = _bounded_text(comment, max_bytes=MAX_COMMENT_BYTES, allow_empty=False)
        else:
            if (
                issue_number is None
                or title is not None
                or body is not None
                or comment is not None
                or desired_state is None
            ):
                raise ActionGatewayInvalidRequestError()
            desired_state = _as_issue_state(desired_state)
        if self.provenance is not None and type(self.provenance) is not ActionProvenanceV1:
            raise ActionGatewayInvalidRequestError()
        object.__setattr__(self, "operation_id", operation_id)
        object.__setattr__(self, "action_kind", action_kind)
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "comment", comment)
        object.__setattr__(self, "desired_state", desired_state)

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "contract_version": self.contract_version,
            "operation_id": self.operation_id,
            "action_kind": cast(ActionKindV1, self.action_kind).value,
            "connector": self.connector,
            "repository": self.repository,
        }
        if self.issue_number is not None:
            data["issue_number"] = self.issue_number
        if self.title is not None:
            data["title"] = self.title
        if self.body is not None:
            data["body"] = self.body
        if self.comment is not None:
            data["comment"] = self.comment
        if self.desired_state is not None:
            data["desired_state"] = cast(IssueStateV1, self.desired_state).value
        if self.provenance is not None:
            data["provenance"] = self.provenance.as_dict()
        return data

    @property
    def operation_id_fingerprint(self) -> str:
        return hashlib.sha256(self.operation_id.encode("utf-8")).hexdigest()

    @property
    def intent_fingerprint(self) -> str:
        data = self.as_dict()
        data.pop("operation_id", None)
        return action_gateway_hash(data)

    @classmethod
    def from_dict(cls, value: object) -> ActionIntentV1:
        allowed = {
            "contract_version",
            "operation_id",
            "action_kind",
            "connector",
            "repository",
            "issue_number",
            "title",
            "body",
            "comment",
            "desired_state",
            "provenance",
        }
        data = _strict_dict(value, allowed)
        provenance_value = data.get("provenance")
        return cls(
            contract_version=cast(str, data.get("contract_version")),
            operation_id=cast(str, data.get("operation_id")),
            action_kind=cast(str, data.get("action_kind")),
            connector=cast(str, data.get("connector")),
            repository=cast(str, data.get("repository")),
            issue_number=cast(int | None, data.get("issue_number")),
            title=cast(str | None, data.get("title")),
            body=cast(str | None, data.get("body")),
            comment=cast(str | None, data.get("comment")),
            desired_state=cast(str | None, data.get("desired_state")),
            provenance=None
            if provenance_value is None
            else ActionProvenanceV1.from_dict(provenance_value),
        )

    @classmethod
    def from_json(cls, value: object) -> ActionIntentV1:
        return cls.from_dict(_json_object(value))


@dataclass(frozen=True, slots=True)
class ExactTargetIdentityV1:
    """Remote numeric/node identity and safety-relevant issue state."""

    repository: str
    repository_id: int
    repository_node_id: str
    issue_number: int | None = None
    issue_id: int | None = None
    issue_node_id: str | None = None
    current_state: IssueStateV1 | str | None = None
    locked: bool | None = None

    def __post_init__(self) -> None:
        repository = _repository(self.repository)
        if (
            type(self.repository_id) is not int
            or isinstance(self.repository_id, bool)
            or self.repository_id <= 0
        ):
            raise ActionGatewayInvalidRequestError()
        node_id = _text(
            self.repository_node_id, max_bytes=MAX_NODE_ID_BYTES, pattern=_NODE_ID_PATTERN
        )
        values = (
            self.issue_number,
            self.issue_id,
            self.issue_node_id,
            self.current_state,
            self.locked,
        )
        if all(value is None for value in values):
            pass
        elif any(value is None for value in values):
            raise ActionGatewayInvalidRequestError()
        else:
            issue_number = self.issue_number
            issue_id = self.issue_id
            if (
                type(issue_number) is not int
                or isinstance(issue_number, bool)
                or not 1 <= issue_number <= 2**31 - 1
            ):
                raise ActionGatewayInvalidRequestError()
            if type(issue_id) is not int or isinstance(issue_id, bool) or issue_id <= 0:
                raise ActionGatewayInvalidRequestError()
            if type(self.locked) is not bool:
                raise ActionGatewayInvalidRequestError()
            object.__setattr__(
                self,
                "issue_node_id",
                _text(self.issue_node_id, max_bytes=MAX_NODE_ID_BYTES, pattern=_NODE_ID_PATTERN),
            )
            object.__setattr__(
                self, "current_state", _as_issue_state(cast(IssueStateV1 | str, self.current_state))
            )
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "repository_node_id", node_id)

    def as_dict(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "repository_id": self.repository_id,
            "repository_node_id": self.repository_node_id,
            "issue_number": self.issue_number,
            "issue_id": self.issue_id,
            "issue_node_id": self.issue_node_id,
            "current_state": None
            if self.current_state is None
            else cast(IssueStateV1, self.current_state).value,
            "locked": self.locked,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExactTargetIdentityV1:
        data = _strict_dict(
            value,
            {
                "repository",
                "repository_id",
                "repository_node_id",
                "issue_number",
                "issue_id",
                "issue_node_id",
                "current_state",
                "locked",
            },
        )
        return cls(
            repository=cast(str, data.get("repository")),
            repository_id=cast(int, data.get("repository_id")),
            repository_node_id=cast(str, data.get("repository_node_id")),
            issue_number=cast(int | None, data.get("issue_number")),
            issue_id=cast(int | None, data.get("issue_id")),
            issue_node_id=cast(str | None, data.get("issue_node_id")),
            current_state=cast(str | None, data.get("current_state")),
            locked=cast(bool | None, data.get("locked")),
        )

    @property
    def fingerprint(self) -> str:
        return action_gateway_hash(self.as_dict())

    def safe_identity(self) -> dict[str, object]:
        return {key: value for key, value in self.as_dict().items() if value is not None}


def _validate_semantic_payload(
    action_kind: ActionKindV1,
    payload: Mapping[str, object],
    target: ExactTargetIdentityV1,
) -> dict[str, object]:
    if type(payload) is not dict:
        raise ActionGatewayInvalidRequestError()
    data = dict(payload)
    if action_kind is ActionKindV1.GITHUB_ISSUE_CREATE:
        if (
            set(data) != {"repository", "title", "body", "marker"}
            or target.issue_number is not None
        ):
            raise ActionGatewayInvalidRequestError()
        if data["repository"] != target.repository:
            raise ActionGatewayInvalidRequestError()
        _repository(data["repository"])
        _text(data["title"], max_bytes=MAX_TITLE_BYTES)
        _bounded_text(data["body"], max_bytes=MAX_BODY_BYTES)
    elif action_kind is ActionKindV1.GITHUB_ISSUE_COMMENT:
        if (
            set(data) != {"repository", "issue_number", "comment", "marker"}
            or target.issue_number is None
        ):
            raise ActionGatewayInvalidRequestError()
        if data["repository"] != target.repository or data["issue_number"] != target.issue_number:
            raise ActionGatewayInvalidRequestError()
        _repository(data["repository"])
        _bounded_text(data["comment"], max_bytes=MAX_COMMENT_BYTES)
    else:
        if (
            set(data) != {"repository", "issue_number", "desired_state"}
            or target.issue_number is None
        ):
            raise ActionGatewayInvalidRequestError()
        if data["repository"] != target.repository or data["issue_number"] != target.issue_number:
            raise ActionGatewayInvalidRequestError()
        _repository(data["repository"])
        _as_issue_state(cast(str, data["desired_state"]))
    marker = data.get("marker")
    if action_kind is not ActionKindV1.GITHUB_ISSUE_SET_STATE and (
        type(marker) is not str or _MARKER_PATTERN.fullmatch(marker) is None
    ):
        raise ActionGatewayInvalidRequestError()
    return data


@dataclass(frozen=True, slots=True)
class ConnectorPreparedActionV1:
    """Provider-owned prepare result before the core adds policy metadata."""

    exact_target_identity: ExactTargetIdentityV1
    preflight_fingerprint: str
    semantic_payload: dict[str, object]
    preview: str
    reversibility: ReversibilityV1 | str

    def __post_init__(self) -> None:
        if type(self.exact_target_identity) is not ExactTargetIdentityV1:
            raise ActionGatewayInvalidRequestError()
        _hash(self.preflight_fingerprint)
        _bounded_text(self.preview, max_bytes=MAX_PREVIEW_BYTES, allow_empty=False)
        try:
            reversibility = (
                self.reversibility
                if isinstance(self.reversibility, ReversibilityV1)
                else ReversibilityV1(self.reversibility)
            )
        except (TypeError, ValueError) as exc:
            raise ActionGatewayInvalidRequestError() from exc
        object.__setattr__(self, "reversibility", reversibility)
        object.__setattr__(self, "semantic_payload", dict(self.semantic_payload))


@dataclass(frozen=True, slots=True)
class PreparedExternalActionV1:
    """Immutable page-memory prepare result."""

    prepared_action_id: UUID
    contract_version: str
    operation_id_fingerprint: str
    action_kind: ActionKindV1 | str
    risk: RiskClassV1 | str
    connector: str
    connector_policy_id: str
    credential_profile_id: str
    exact_target_identity: ExactTargetIdentityV1
    preflight_fingerprint: str
    semantic_payload: dict[str, object]
    payload_fingerprint: str
    preview: str
    preview_fingerprint: str
    prepared_at: datetime
    expires_at: datetime
    reversibility: ReversibilityV1 | str
    provenance: ActionProvenanceV1 | None = None

    def __post_init__(self) -> None:
        prepared_id = _uuid7(self.prepared_action_id)
        if self.contract_version != PREPARED_ACTION_CONTRACT_VERSION:
            raise ActionGatewayInvalidRequestError()
        operation = _hash(self.operation_id_fingerprint)
        action_kind = _as_action_kind(self.action_kind)
        risk = _as_risk(self.risk)
        if (
            risk is not RiskClassV1.CONTROLLED_WRITE
            or self.connector != ACTION_GATEWAY_CONNECTOR
            or self.connector_policy_id != ACTION_GATEWAY_POLICY_ID
            or self.credential_profile_id != ACTION_GATEWAY_CREDENTIAL_PROFILE_ID
        ):
            raise ActionGatewayInvalidRequestError()
        if type(self.exact_target_identity) is not ExactTargetIdentityV1:
            raise ActionGatewayInvalidRequestError()
        preflight = _hash(self.preflight_fingerprint)
        payload = _validate_semantic_payload(
            action_kind, self.semantic_payload, self.exact_target_identity
        )
        payload_fp = _hash(self.payload_fingerprint)
        if payload_fp != action_gateway_hash(payload):
            raise ActionGatewayInvalidRequestError()
        preview = _bounded_text(self.preview, max_bytes=MAX_PREVIEW_BYTES, allow_empty=False)
        preview_fp = _hash(self.preview_fingerprint)
        if preview_fp != action_gateway_hash(preview):
            raise ActionGatewayInvalidRequestError()
        prepared_at = _utc(self.prepared_at)
        expires_at = _utc(self.expires_at)
        if expires_at <= prepared_at or expires_at - prepared_at > timedelta(
            seconds=ACTION_GATEWAY_MAX_CONFIRM_TTL_SECONDS
        ):
            raise ActionGatewayInvalidRequestError()
        try:
            reversibility = (
                self.reversibility
                if isinstance(self.reversibility, ReversibilityV1)
                else ReversibilityV1(self.reversibility)
            )
        except (TypeError, ValueError) as exc:
            raise ActionGatewayInvalidRequestError() from exc
        if self.provenance is not None and type(self.provenance) is not ActionProvenanceV1:
            raise ActionGatewayInvalidRequestError()
        object.__setattr__(self, "prepared_action_id", prepared_id)
        object.__setattr__(self, "operation_id_fingerprint", operation)
        object.__setattr__(self, "action_kind", action_kind)
        object.__setattr__(self, "risk", risk)
        object.__setattr__(self, "preflight_fingerprint", preflight)
        object.__setattr__(self, "semantic_payload", payload)
        object.__setattr__(self, "prepared_at", prepared_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "preview", preview)
        object.__setattr__(self, "reversibility", reversibility)

    def as_dict(self) -> dict[str, object]:
        return {
            "prepared_action_id": str(self.prepared_action_id),
            "contract_version": self.contract_version,
            "operation_id_fingerprint": self.operation_id_fingerprint,
            "action_kind": cast(ActionKindV1, self.action_kind).value,
            "risk": cast(RiskClassV1, self.risk).value,
            "connector": self.connector,
            "connector_policy_id": self.connector_policy_id,
            "credential_profile_id": self.credential_profile_id,
            "exact_target_identity": self.exact_target_identity.as_dict(),
            "preflight_fingerprint": self.preflight_fingerprint,
            "semantic_payload": dict(self.semantic_payload),
            "payload_fingerprint": self.payload_fingerprint,
            "preview": self.preview,
            "preview_fingerprint": self.preview_fingerprint,
            "prepared_at": _wire_time(self.prepared_at),
            "expires_at": _wire_time(self.expires_at),
            "reversibility": cast(ReversibilityV1, self.reversibility).value,
            "provenance": None if self.provenance is None else self.provenance.as_dict(),
        }

    @property
    def fingerprint(self) -> str:
        return action_gateway_hash(self.as_dict())

    @classmethod
    def from_dict(cls, value: object) -> PreparedExternalActionV1:
        allowed = {
            "prepared_action_id",
            "contract_version",
            "operation_id_fingerprint",
            "action_kind",
            "risk",
            "connector",
            "connector_policy_id",
            "credential_profile_id",
            "exact_target_identity",
            "preflight_fingerprint",
            "semantic_payload",
            "payload_fingerprint",
            "preview",
            "preview_fingerprint",
            "prepared_at",
            "expires_at",
            "reversibility",
            "provenance",
        }
        data = _strict_dict(value, allowed)
        provenance_value = data.get("provenance")
        semantic = data.get("semantic_payload")
        if type(semantic) is not dict:
            raise ActionGatewayInvalidRequestError()
        return cls(
            prepared_action_id=_uuid7(data.get("prepared_action_id")),
            contract_version=cast(str, data.get("contract_version")),
            operation_id_fingerprint=cast(str, data.get("operation_id_fingerprint")),
            action_kind=cast(str, data.get("action_kind")),
            risk=cast(str, data.get("risk")),
            connector=cast(str, data.get("connector")),
            connector_policy_id=cast(str, data.get("connector_policy_id")),
            credential_profile_id=cast(str, data.get("credential_profile_id")),
            exact_target_identity=ExactTargetIdentityV1.from_dict(
                data.get("exact_target_identity")
            ),
            preflight_fingerprint=cast(str, data.get("preflight_fingerprint")),
            semantic_payload=cast(dict[str, object], semantic),
            payload_fingerprint=cast(str, data.get("payload_fingerprint")),
            preview=cast(str, data.get("preview")),
            preview_fingerprint=cast(str, data.get("preview_fingerprint")),
            prepared_at=_utc(data.get("prepared_at")),
            expires_at=_utc(data.get("expires_at")),
            reversibility=cast(str, data.get("reversibility")),
            provenance=None
            if provenance_value is None
            else ActionProvenanceV1.from_dict(provenance_value),
        )


_SAFE_IDENTITY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "repository",
        "repository_id",
        "repository_node_id",
        "issue_number",
        "issue_id",
        "issue_node_id",
        "current_state",
        "state",
        "locked",
        "url",
    }
)


def _safe_identity(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise ActionGatewayInvalidRequestError()
    data = cast(dict[str, object], value)
    if not set(data).issubset(_SAFE_IDENTITY_KEYS) or len(data) > MAX_SAFE_IDENTITY_KEYS:
        raise ActionGatewayInvalidRequestError()
    result: dict[str, object] = {}
    for key, item in data.items():
        if key in {
            "repository",
            "repository_node_id",
            "issue_node_id",
            "current_state",
            "state",
            "url",
        }:
            if type(item) is not str or len(item.encode("utf-8")) > MAX_SAFE_IDENTITY_VALUE_BYTES:
                raise ActionGatewayInvalidRequestError()
        elif key in {"repository_id", "issue_number", "issue_id"}:
            if type(item) is not int or isinstance(item, bool) or item <= 0:
                raise ActionGatewayInvalidRequestError()
        elif key == "locked" and type(item) is not bool:
            raise ActionGatewayInvalidRequestError()
        result[key] = item
    return result


@dataclass(frozen=True, slots=True)
class ActionReceiptV1:
    """Safe durable evidence; it intentionally excludes outgoing content."""

    receipt_id: UUID
    receipt_kind: ActionReceiptKindV1 | str
    operation_id_fingerprint: str
    prepared_action_id: UUID
    intent_fingerprint: str
    action_kind: ActionKindV1 | str
    risk: RiskClassV1 | str
    connector_policy_id: str
    credential_profile_id: str
    target_safe_identity: dict[str, object]
    payload_fingerprint: str
    state: ActionReceiptStateV1 | str
    attempt_started_at: datetime | None = None
    sent_at: datetime | None = None
    finished_at: datetime | None = None
    remote_safe_identity: dict[str, object] | None = None
    remote_url: str | None = None
    safe_error_code: str | None = None
    parent_receipt_id: UUID | None = None
    reconciliation_receipt_id: UUID | None = None
    compensation_receipt_id: UUID | None = None

    def __post_init__(self) -> None:
        receipt_id = _uuid7(self.receipt_id)
        prepared_id = _uuid7(self.prepared_action_id)
        receipt_kind = _as_receipt_kind(self.receipt_kind)
        operation = _hash(self.operation_id_fingerprint)
        intent = _hash(self.intent_fingerprint)
        action_kind = _as_action_kind(self.action_kind)
        risk = _as_risk(self.risk)
        if (
            risk is not RiskClassV1.CONTROLLED_WRITE
            or self.connector_policy_id != ACTION_GATEWAY_POLICY_ID
            or self.credential_profile_id != ACTION_GATEWAY_CREDENTIAL_PROFILE_ID
        ):
            raise ActionGatewayInvalidRequestError()
        payload = _hash(self.payload_fingerprint)
        state = _as_receipt_state(self.state)
        target = _safe_identity(self.target_safe_identity)
        remote = (
            None if self.remote_safe_identity is None else _safe_identity(self.remote_safe_identity)
        )
        times = tuple(
            None if value is None else _utc(value)
            for value in (self.attempt_started_at, self.sent_at, self.finished_at)
        )
        if self.remote_url is not None:
            remote_url = _text(self.remote_url, max_bytes=2048)
            if (
                not remote_url.startswith("https://github.com/")
                or "?" in remote_url
                or "#" in remote_url
            ):
                raise ActionGatewayInvalidRequestError()
        else:
            remote_url = None
        error = None if self.safe_error_code is None else _safe_error_code(self.safe_error_code)
        parents = tuple(
            None if value is None else _uuid7(value)
            for value in (
                self.parent_receipt_id,
                self.reconciliation_receipt_id,
                self.compensation_receipt_id,
            )
        )
        object.__setattr__(self, "receipt_id", receipt_id)
        object.__setattr__(self, "receipt_kind", receipt_kind)
        object.__setattr__(self, "operation_id_fingerprint", operation)
        object.__setattr__(self, "prepared_action_id", prepared_id)
        object.__setattr__(self, "intent_fingerprint", intent)
        object.__setattr__(self, "action_kind", action_kind)
        object.__setattr__(self, "risk", risk)
        object.__setattr__(self, "target_safe_identity", target)
        object.__setattr__(self, "payload_fingerprint", payload)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "attempt_started_at", times[0])
        object.__setattr__(self, "sent_at", times[1])
        object.__setattr__(self, "finished_at", times[2])
        object.__setattr__(self, "remote_safe_identity", remote)
        object.__setattr__(self, "remote_url", remote_url)
        object.__setattr__(self, "safe_error_code", error)
        object.__setattr__(self, "parent_receipt_id", parents[0])
        object.__setattr__(self, "reconciliation_receipt_id", parents[1])
        object.__setattr__(self, "compensation_receipt_id", parents[2])

    def as_dict(self) -> dict[str, object]:
        return {
            "receipt_id": str(self.receipt_id),
            "receipt_kind": cast(ActionReceiptKindV1, self.receipt_kind).value,
            "operation_id_fingerprint": self.operation_id_fingerprint,
            "prepared_action_id": str(self.prepared_action_id),
            "intent_fingerprint": self.intent_fingerprint,
            "action_kind": cast(ActionKindV1, self.action_kind).value,
            "risk": cast(RiskClassV1, self.risk).value,
            "connector_policy_id": self.connector_policy_id,
            "credential_profile_id": self.credential_profile_id,
            "target_safe_identity": dict(self.target_safe_identity),
            "payload_fingerprint": self.payload_fingerprint,
            "state": cast(ActionReceiptStateV1, self.state).value,
            "attempt_started_at": None
            if self.attempt_started_at is None
            else _wire_time(self.attempt_started_at),
            "sent_at": None if self.sent_at is None else _wire_time(self.sent_at),
            "finished_at": None if self.finished_at is None else _wire_time(self.finished_at),
            "remote_safe_identity": None
            if self.remote_safe_identity is None
            else dict(self.remote_safe_identity),
            "remote_url": self.remote_url,
            "safe_error_code": self.safe_error_code,
            "parent_receipt_id": None
            if self.parent_receipt_id is None
            else str(self.parent_receipt_id),
            "reconciliation_receipt_id": None
            if self.reconciliation_receipt_id is None
            else str(self.reconciliation_receipt_id),
            "compensation_receipt_id": None
            if self.compensation_receipt_id is None
            else str(self.compensation_receipt_id),
        }

    @property
    def fingerprint(self) -> str:
        return action_gateway_hash(self.as_dict())

    @classmethod
    def from_dict(cls, value: object) -> ActionReceiptV1:
        allowed = {
            "receipt_id",
            "receipt_kind",
            "operation_id_fingerprint",
            "prepared_action_id",
            "intent_fingerprint",
            "action_kind",
            "risk",
            "connector_policy_id",
            "credential_profile_id",
            "target_safe_identity",
            "payload_fingerprint",
            "state",
            "attempt_started_at",
            "sent_at",
            "finished_at",
            "remote_safe_identity",
            "remote_url",
            "safe_error_code",
            "parent_receipt_id",
            "reconciliation_receipt_id",
            "compensation_receipt_id",
        }
        data = _strict_dict(value, allowed)
        return cls(
            receipt_id=_uuid7(data.get("receipt_id")),
            receipt_kind=cast(str, data.get("receipt_kind")),
            operation_id_fingerprint=cast(str, data.get("operation_id_fingerprint")),
            prepared_action_id=_uuid7(data.get("prepared_action_id")),
            intent_fingerprint=cast(str, data.get("intent_fingerprint")),
            action_kind=cast(str, data.get("action_kind")),
            risk=cast(str, data.get("risk")),
            connector_policy_id=cast(str, data.get("connector_policy_id")),
            credential_profile_id=cast(str, data.get("credential_profile_id")),
            target_safe_identity=cast(dict[str, object], data.get("target_safe_identity")),
            payload_fingerprint=cast(str, data.get("payload_fingerprint")),
            state=cast(str, data.get("state")),
            attempt_started_at=None
            if data.get("attempt_started_at") is None
            else _utc(data.get("attempt_started_at")),
            sent_at=None if data.get("sent_at") is None else _utc(data.get("sent_at")),
            finished_at=None if data.get("finished_at") is None else _utc(data.get("finished_at")),
            remote_safe_identity=None
            if data.get("remote_safe_identity") is None
            else cast(dict[str, object], data.get("remote_safe_identity")),
            remote_url=cast(str | None, data.get("remote_url")),
            safe_error_code=cast(str | None, data.get("safe_error_code")),
            parent_receipt_id=_optional_uuid(data.get("parent_receipt_id")),
            reconciliation_receipt_id=_optional_uuid(data.get("reconciliation_receipt_id")),
            compensation_receipt_id=_optional_uuid(data.get("compensation_receipt_id")),
        )


@dataclass(frozen=True, slots=True)
class ConnectorRevalidationV1:
    """Fresh exact identity read performed immediately before a mutation."""

    exact_target_identity: ExactTargetIdentityV1

    def __post_init__(self) -> None:
        if type(self.exact_target_identity) is not ExactTargetIdentityV1:
            raise ActionGatewayInvalidRequestError()


@dataclass(frozen=True, slots=True)
class ConnectorExecutionResultV1:
    """Safe connector result with no raw response or exception text."""

    outcome: ActionExecutionOutcomeV1 | str
    attempt_started_at: datetime
    sent_at: datetime | None = None
    finished_at: datetime | None = None
    remote_safe_identity: dict[str, object] | None = None
    remote_url: str | None = None
    safe_error_code: str | None = None

    def __post_init__(self) -> None:
        outcome = _as_execution_outcome(self.outcome)
        started = _utc(self.attempt_started_at)
        sent = None if self.sent_at is None else _utc(self.sent_at)
        finished = None if self.finished_at is None else _utc(self.finished_at)
        remote = (
            None if self.remote_safe_identity is None else _safe_identity(self.remote_safe_identity)
        )
        if outcome is ActionExecutionOutcomeV1.EXECUTED and finished is None:
            raise ActionGatewayInvalidRequestError()
        if (
            outcome
            in {
                ActionExecutionOutcomeV1.FAILED_BEFORE_SEND,
                ActionExecutionOutcomeV1.FAILED_CONFIRMED_NO_MUTATION,
                ActionExecutionOutcomeV1.OUTCOME_UNCERTAIN,
            }
            and self.safe_error_code is None
        ):
            raise ActionGatewayInvalidRequestError()
        error = None if self.safe_error_code is None else _safe_error_code(self.safe_error_code)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "attempt_started_at", started)
        object.__setattr__(self, "sent_at", sent)
        object.__setattr__(self, "finished_at", finished)
        object.__setattr__(self, "remote_safe_identity", remote)
        object.__setattr__(self, "safe_error_code", error)


class ActionConnectorV1(Protocol):
    """Small provider boundary used by the provider-free core."""

    connector_id: str
    policy_id: str
    credential_profile_id: str

    def prepare(
        self,
        intent: ActionIntentV1,
        *,
        prepared_action_id: UUID,
        now: datetime,
    ) -> ConnectorPreparedActionV1:
        """Perform bounded read-only preflight and build outgoing semantics."""

    def revalidate(
        self,
        prepared: PreparedExternalActionV1,
        *,
        now: datetime,
    ) -> ConnectorRevalidationV1:
        """Read safety-relevant remote identity immediately before execution."""

    def execute(
        self,
        prepared: PreparedExternalActionV1,
        *,
        now: datetime,
    ) -> ConnectorExecutionResultV1:
        """Attempt exactly one provider mutation."""


class ActionReceiptStoreV1(Protocol):
    """Minimal receipt-store seam required by the core."""

    def find_operation(self, operation_id_fingerprint: str) -> ActionReceiptV1 | None:
        """Return the effective last receipt for an operation."""

    def begin_execution(
        self,
        prepared: PreparedExternalActionV1,
        *,
        now: datetime,
    ) -> tuple[ActionReceiptV1, bool]:
        """Atomically record execution_started or return an existing lifecycle."""

    def append(self, receipt: ActionReceiptV1) -> ActionReceiptV1:
        """Append one receipt lifecycle record."""


@dataclass(frozen=True, slots=True)
class ActionExecutionResultV1:
    """Result returned to the application boundary."""

    receipt: ActionReceiptV1
    replayed: bool


@dataclass(frozen=True, slots=True)
class _ConfirmationClaimsV1:
    nonce: str
    prepared_action_id: UUID
    prepared_fingerprint: str
    operation_id_fingerprint: str
    intent_fingerprint: str
    action_kind: ActionKindV1
    target_fingerprint: str
    payload_fingerprint: str
    policy_id: str
    policy_fingerprint: str
    risk: RiskClassV1
    expires_at_epoch: int


class ConfirmationCodecV1:
    """Process-local HMAC confirmation codec with one-use replay tracking."""

    def __init__(
        self,
        secret: bytes | None = None,
        *,
        ttl_seconds: int = ACTION_GATEWAY_DEFAULT_CONFIRM_TTL_SECONDS,
    ) -> None:
        if secret is None:
            secret = secrets.token_bytes(32)
        if type(secret) is not bytes or len(secret) < 32:
            raise ActionGatewayInvalidRequestError()
        if (
            type(ttl_seconds) is not int
            or isinstance(ttl_seconds, bool)
            or not 1 <= ttl_seconds <= ACTION_GATEWAY_MAX_CONFIRM_TTL_SECONDS
        ):
            raise ActionGatewayInvalidRequestError()
        self._secret = bytes(secret)
        self._ttl_seconds = ttl_seconds
        self._used_tokens: set[str] = set()

    def issue(self, prepared: PreparedExternalActionV1, *, now: datetime | None = None) -> str:
        if type(prepared) is not PreparedExternalActionV1:
            raise ActionGatewayInvalidRequestError()
        current = _utc(now or datetime.now(UTC))
        expires = min(prepared.expires_at, current + timedelta(seconds=self._ttl_seconds))
        if expires <= current:
            raise ActionGatewayConfirmationError()
        claims = {
            "purpose": ACTION_GATEWAY_CONFIRM_PURPOSE,
            "version": ACTION_GATEWAY_CONFIRM_VERSION,
            "nonce": _b64(secrets.token_bytes(16)),
            "prepared_action_id": str(prepared.prepared_action_id),
            "prepared_fingerprint": prepared.fingerprint,
            "operation_id_fingerprint": prepared.operation_id_fingerprint,
            "intent_fingerprint": _intent_fingerprint_from_prepared(prepared),
            "action_kind": cast(ActionKindV1, prepared.action_kind).value,
            "target_fingerprint": prepared.exact_target_identity.fingerprint,
            "payload_fingerprint": prepared.payload_fingerprint,
            "policy_id": prepared.connector_policy_id,
            "policy_fingerprint": ACTION_GATEWAY_POLICY_FINGERPRINT,
            "risk": cast(RiskClassV1, prepared.risk).value,
            "expires_at_epoch": int(expires.timestamp()),
        }
        payload = _canonical_bytes(claims)
        signature = hmac.new(
            self._secret,
            ACTION_GATEWAY_CONFIRM_PURPOSE.encode("ascii") + b"\0" + payload,
            hashlib.sha256,
        ).digest()
        return f"{_b64(payload)}.{_b64(signature)}"

    def verify(
        self, token: object, prepared: PreparedExternalActionV1, *, now: datetime | None = None
    ) -> None:
        self._claims(token, prepared, now=now)

    def consume(
        self, token: object, prepared: PreparedExternalActionV1, *, now: datetime | None = None
    ) -> None:
        claims = self._claims(token, prepared, now=now)
        token_digest = hashlib.sha256(_token_bytes(token)).hexdigest()
        if token_digest in self._used_tokens:
            raise ActionGatewayConfirmationError("confirmation_replayed")
        self._used_tokens.add(token_digest)
        del claims

    def _claims(
        self, token: object, prepared: PreparedExternalActionV1, *, now: datetime | None
    ) -> _ConfirmationClaimsV1:
        if (
            type(prepared) is not PreparedExternalActionV1
            or type(token) is not str
            or len(token.encode("utf-8")) > 8192
        ):
            raise ActionGatewayConfirmationError()
        parts = token.split(".")
        if len(parts) != 2:
            raise ActionGatewayConfirmationError()
        try:
            payload = _b64decode(parts[0])
            signature = _b64decode(parts[1])
            expected = hmac.new(
                self._secret,
                ACTION_GATEWAY_CONFIRM_PURPOSE.encode("ascii") + b"\0" + payload,
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(signature, expected):
                raise ActionGatewayConfirmationError()
            decoded = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
        except (
            ActionGatewayError,
            TypeError,
            ValueError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ActionGatewayConfirmationError() from exc
        if type(decoded) is not dict:
            raise ActionGatewayConfirmationError()
        expected_keys = {
            "purpose",
            "version",
            "nonce",
            "prepared_action_id",
            "prepared_fingerprint",
            "operation_id_fingerprint",
            "intent_fingerprint",
            "action_kind",
            "target_fingerprint",
            "payload_fingerprint",
            "policy_id",
            "policy_fingerprint",
            "risk",
            "expires_at_epoch",
        }
        if set(decoded) != expected_keys or _canonical_bytes(decoded) != payload:
            raise ActionGatewayConfirmationError()
        try:
            claims = _ConfirmationClaimsV1(
                nonce=cast(str, decoded["nonce"]),
                prepared_action_id=_uuid7(decoded["prepared_action_id"]),
                prepared_fingerprint=_hash(decoded["prepared_fingerprint"]),
                operation_id_fingerprint=_hash(decoded["operation_id_fingerprint"]),
                intent_fingerprint=_hash(decoded["intent_fingerprint"]),
                action_kind=_as_action_kind(cast(str, decoded["action_kind"])),
                target_fingerprint=_hash(decoded["target_fingerprint"]),
                payload_fingerprint=_hash(decoded["payload_fingerprint"]),
                policy_id=_text(decoded["policy_id"], max_bytes=128, pattern=_SAFE_ID_PATTERN),
                policy_fingerprint=_hash(decoded["policy_fingerprint"]),
                risk=_as_risk(cast(str, decoded["risk"])),
                expires_at_epoch=decoded["expires_at_epoch"],
            )
        except (ActionGatewayError, TypeError, ValueError, OverflowError) as exc:
            raise ActionGatewayConfirmationError() from exc
        try:
            if len(_b64decode(claims.nonce)) != 16:
                raise ActionGatewayConfirmationError()
        except ActionGatewayError as exc:
            raise ActionGatewayConfirmationError() from exc
        current = _utc(now or datetime.now(UTC))
        if (
            type(claims.expires_at_epoch) is not int
            or isinstance(claims.expires_at_epoch, bool)
            or int(current.timestamp()) >= claims.expires_at_epoch
        ):
            raise ActionGatewayConfirmationError()
        expected_intent = _intent_fingerprint_from_prepared(prepared)
        if (
            claims.prepared_action_id != prepared.prepared_action_id
            or claims.prepared_fingerprint != prepared.fingerprint
            or claims.operation_id_fingerprint != prepared.operation_id_fingerprint
            or claims.intent_fingerprint != expected_intent
            or claims.action_kind is not prepared.action_kind
            or claims.target_fingerprint != prepared.exact_target_identity.fingerprint
            or claims.payload_fingerprint != prepared.payload_fingerprint
            or claims.policy_id != prepared.connector_policy_id
            or claims.policy_fingerprint != ACTION_GATEWAY_POLICY_FINGERPRINT
            or claims.risk is not prepared.risk
            or claims.expires_at_epoch > int(prepared.expires_at.timestamp())
        ):
            raise ActionGatewayConfirmationError()
        return claims


def _token_bytes(token: object) -> bytes:
    if type(token) is not str:
        raise ActionGatewayConfirmationError()
    return token.encode("ascii")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    if not value or any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for character in value
    ):
        raise ActionGatewayConfirmationError()
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ActionGatewayConfirmationError() from exc


class ActionGatewayCoreV1:
    """Prepare and execute one action through a provider connector."""

    def __init__(
        self,
        connector: ActionConnectorV1,
        store: ActionReceiptStoreV1,
        *,
        confirmation: ConfirmationCodecV1 | None = None,
    ) -> None:
        if (
            connector.connector_id != ACTION_GATEWAY_CONNECTOR
            or connector.policy_id != ACTION_GATEWAY_POLICY_ID
            or connector.credential_profile_id != ACTION_GATEWAY_CREDENTIAL_PROFILE_ID
        ):
            raise ActionGatewayInvalidRequestError()
        self._connector = connector
        self._store = store
        self._confirmation = confirmation or ConfirmationCodecV1()

    def prepare(
        self, intent: ActionIntentV1, *, now: datetime | None = None
    ) -> PreparedExternalActionV1:
        if type(intent) is not ActionIntentV1:
            raise ActionGatewayInvalidRequestError()
        existing = self._store.find_operation(intent.operation_id_fingerprint)
        if existing is not None and existing.intent_fingerprint != intent.intent_fingerprint:
            raise ActionGatewayConflictError()
        current = _utc(now or datetime.now(UTC))
        prepared_id = uuid7()
        try:
            connector_result = self._connector.prepare(
                intent, prepared_action_id=prepared_id, now=current
            )
        except ActionGatewayError:
            raise
        except Exception as exc:
            raise ActionGatewayConnectorError(
                ActionExecutionOutcomeV1.FAILED_BEFORE_SEND, "provider_preflight_failed"
            ) from exc
        if type(connector_result) is not ConnectorPreparedActionV1:
            raise ActionGatewayInvalidRequestError()
        target = connector_result.exact_target_identity
        if target.repository.casefold() != intent.repository.casefold():
            raise ActionGatewayTargetChangedError()
        if (intent.issue_number is None and target.issue_number is not None) or (
            intent.issue_number is not None and target.issue_number != intent.issue_number
        ):
            raise ActionGatewayTargetChangedError()
        semantic = _validate_semantic_payload(
            cast(ActionKindV1, intent.action_kind), connector_result.semantic_payload, target
        )
        if cast(ActionKindV1, intent.action_kind) is not ActionKindV1.GITHUB_ISSUE_SET_STATE:
            marker = semantic.get("marker")
            if marker != _marker_for(prepared_id):
                raise ActionGatewayInvalidRequestError()
            body_key = "body" if "body" in semantic else "comment"
            body_value = cast(str, semantic[body_key])
            if not body_value.endswith(f"\n{marker}") and body_value != marker:
                raise ActionGatewayInvalidRequestError()
        expires = min(
            current + timedelta(seconds=ACTION_GATEWAY_MAX_CONFIRM_TTL_SECONDS),
            current + timedelta(seconds=ACTION_GATEWAY_MAX_CONFIRM_TTL_SECONDS),
        )
        return PreparedExternalActionV1(
            prepared_action_id=prepared_id,
            contract_version=PREPARED_ACTION_CONTRACT_VERSION,
            operation_id_fingerprint=intent.operation_id_fingerprint,
            action_kind=intent.action_kind,
            risk=RiskClassV1.CONTROLLED_WRITE,
            connector=ACTION_GATEWAY_CONNECTOR,
            connector_policy_id=ACTION_GATEWAY_POLICY_ID,
            credential_profile_id=ACTION_GATEWAY_CREDENTIAL_PROFILE_ID,
            exact_target_identity=target,
            preflight_fingerprint=connector_result.preflight_fingerprint,
            semantic_payload=semantic,
            payload_fingerprint=action_gateway_hash(semantic),
            preview=connector_result.preview,
            preview_fingerprint=action_gateway_hash(connector_result.preview),
            prepared_at=current,
            expires_at=expires,
            reversibility=connector_result.reversibility,
            provenance=intent.provenance,
        )

    def issue_confirmation(
        self, prepared: PreparedExternalActionV1, *, now: datetime | None = None
    ) -> str:
        return self._confirmation.issue(prepared, now=now)

    def execute(
        self,
        prepared: PreparedExternalActionV1,
        confirmation: object,
        *,
        now: datetime | None = None,
    ) -> ActionExecutionResultV1:
        if type(prepared) is not PreparedExternalActionV1:
            raise ActionGatewayInvalidRequestError()
        current = _utc(now or datetime.now(UTC))
        existing = self._store.find_operation(prepared.operation_id_fingerprint)
        if existing is not None:
            if existing.intent_fingerprint != _intent_fingerprint_from_prepared(prepared):
                raise ActionGatewayConflictError()
            self._confirmation.consume(confirmation, prepared, now=current)
            if existing.state is ActionReceiptStateV1.EXECUTION_STARTED:
                uncertain = self._uncertain_receipt(prepared, existing, now=current)
                return ActionExecutionResultV1(self._store.append(uncertain), True)
            return ActionExecutionResultV1(existing, True)
        self._confirmation.consume(confirmation, prepared, now=current)
        try:
            revalidated = self._connector.revalidate(prepared, now=current)
        except ActionGatewayError:
            raise
        except Exception:
            failed = self._failure_receipt(
                prepared,
                ActionReceiptStateV1.FAILED_BEFORE_SEND,
                "provider_revalidation_failed",
                now=current,
            )
            return ActionExecutionResultV1(self._store.append(failed), False)
        if (
            type(revalidated) is not ConnectorRevalidationV1
            or revalidated.exact_target_identity.as_dict()
            != prepared.exact_target_identity.as_dict()
        ):
            failed = self._failure_receipt(
                prepared,
                ActionReceiptStateV1.FAILED_BEFORE_SEND,
                ActionGatewayTargetChangedError.code,
                now=current,
            )
            return ActionExecutionResultV1(self._store.append(failed), False)
        if (
            cast(ActionKindV1, prepared.action_kind) is ActionKindV1.GITHUB_ISSUE_SET_STATE
            and prepared.exact_target_identity.current_state
            == prepared.semantic_payload.get("desired_state")
        ):
            receipt = self._simple_receipt(
                prepared, ActionReceiptStateV1.ALREADY_SATISFIED, now=current
            )
            return ActionExecutionResultV1(self._store.append(receipt), False)
        started, is_new = self._store.begin_execution(prepared, now=current)
        if not is_new:
            if started.intent_fingerprint != _intent_fingerprint_from_prepared(prepared):
                raise ActionGatewayConflictError()
            if started.state is ActionReceiptStateV1.EXECUTION_STARTED:
                uncertain = self._uncertain_receipt(prepared, started, now=current)
                return ActionExecutionResultV1(self._store.append(uncertain), True)
            return ActionExecutionResultV1(started, True)
        try:
            outcome = self._connector.execute(prepared, now=current)
        except ActionGatewayConnectorError as exc:
            outcome = ConnectorExecutionResultV1(
                outcome=exc.outcome,
                attempt_started_at=started.attempt_started_at or current,
                sent_at=current
                if exc.outcome is not ActionExecutionOutcomeV1.FAILED_BEFORE_SEND
                else None,
                finished_at=current,
                safe_error_code=exc.safe_error_code,
            )
        except Exception:
            outcome = ConnectorExecutionResultV1(
                outcome=ActionExecutionOutcomeV1.OUTCOME_UNCERTAIN,
                attempt_started_at=started.attempt_started_at or current,
                sent_at=current,
                finished_at=current,
                safe_error_code="provider_outcome_uncertain",
            )
        result = self._receipt_from_outcome(prepared, started, outcome, now=current)
        return ActionExecutionResultV1(self._store.append(result), False)

    def _receipt_from_outcome(
        self,
        prepared: PreparedExternalActionV1,
        started: ActionReceiptV1,
        outcome: ConnectorExecutionResultV1,
        *,
        now: datetime,
    ) -> ActionReceiptV1:
        state_map: dict[ActionExecutionOutcomeV1, ActionReceiptStateV1] = {
            ActionExecutionOutcomeV1.EXECUTED: ActionReceiptStateV1.EXECUTED,
            ActionExecutionOutcomeV1.ALREADY_SATISFIED: ActionReceiptStateV1.ALREADY_SATISFIED,
            ActionExecutionOutcomeV1.FAILED_BEFORE_SEND: ActionReceiptStateV1.FAILED_BEFORE_SEND,
            ActionExecutionOutcomeV1.FAILED_CONFIRMED_NO_MUTATION: (
                ActionReceiptStateV1.FAILED_CONFIRMED_NO_MUTATION
            ),
            ActionExecutionOutcomeV1.OUTCOME_UNCERTAIN: ActionReceiptStateV1.OUTCOME_UNCERTAIN,
        }
        return ActionReceiptV1(
            receipt_id=uuid7(),
            receipt_kind=ActionReceiptKindV1.ACTION,
            operation_id_fingerprint=prepared.operation_id_fingerprint,
            prepared_action_id=prepared.prepared_action_id,
            intent_fingerprint=_intent_fingerprint_from_prepared(prepared),
            action_kind=prepared.action_kind,
            risk=prepared.risk,
            connector_policy_id=prepared.connector_policy_id,
            credential_profile_id=prepared.credential_profile_id,
            target_safe_identity=prepared.exact_target_identity.safe_identity(),
            payload_fingerprint=prepared.payload_fingerprint,
            state=state_map[_as_execution_outcome(outcome.outcome)],
            attempt_started_at=outcome.attempt_started_at,
            sent_at=outcome.sent_at,
            finished_at=outcome.finished_at or now,
            remote_safe_identity=outcome.remote_safe_identity,
            remote_url=outcome.remote_url,
            safe_error_code=outcome.safe_error_code,
            parent_receipt_id=started.receipt_id,
        )

    def _simple_receipt(
        self, prepared: PreparedExternalActionV1, state: ActionReceiptStateV1, *, now: datetime
    ) -> ActionReceiptV1:
        return ActionReceiptV1(
            receipt_id=uuid7(),
            receipt_kind=ActionReceiptKindV1.ACTION,
            operation_id_fingerprint=prepared.operation_id_fingerprint,
            prepared_action_id=prepared.prepared_action_id,
            intent_fingerprint=_intent_fingerprint_from_prepared(prepared),
            action_kind=prepared.action_kind,
            risk=prepared.risk,
            connector_policy_id=prepared.connector_policy_id,
            credential_profile_id=prepared.credential_profile_id,
            target_safe_identity=prepared.exact_target_identity.safe_identity(),
            payload_fingerprint=prepared.payload_fingerprint,
            state=state,
            finished_at=now,
        )

    def _failure_receipt(
        self,
        prepared: PreparedExternalActionV1,
        state: ActionReceiptStateV1,
        error: str,
        *,
        now: datetime,
    ) -> ActionReceiptV1:
        return ActionReceiptV1(
            receipt_id=uuid7(),
            receipt_kind=ActionReceiptKindV1.ACTION,
            operation_id_fingerprint=prepared.operation_id_fingerprint,
            prepared_action_id=prepared.prepared_action_id,
            intent_fingerprint=_intent_fingerprint_from_prepared(prepared),
            action_kind=prepared.action_kind,
            risk=prepared.risk,
            connector_policy_id=prepared.connector_policy_id,
            credential_profile_id=prepared.credential_profile_id,
            target_safe_identity=prepared.exact_target_identity.safe_identity(),
            payload_fingerprint=prepared.payload_fingerprint,
            state=state,
            finished_at=now,
            safe_error_code=error,
        )

    def _uncertain_receipt(
        self, prepared: PreparedExternalActionV1, started: ActionReceiptV1, *, now: datetime
    ) -> ActionReceiptV1:
        return ActionReceiptV1(
            receipt_id=uuid7(),
            receipt_kind=ActionReceiptKindV1.ACTION,
            operation_id_fingerprint=prepared.operation_id_fingerprint,
            prepared_action_id=prepared.prepared_action_id,
            intent_fingerprint=_intent_fingerprint_from_prepared(prepared),
            action_kind=prepared.action_kind,
            risk=prepared.risk,
            connector_policy_id=prepared.connector_policy_id,
            credential_profile_id=prepared.credential_profile_id,
            target_safe_identity=prepared.exact_target_identity.safe_identity(),
            payload_fingerprint=prepared.payload_fingerprint,
            state=ActionReceiptStateV1.OUTCOME_UNCERTAIN,
            attempt_started_at=started.attempt_started_at,
            sent_at=started.sent_at,
            finished_at=now,
            safe_error_code="execution_started_without_terminal_receipt",
            parent_receipt_id=started.receipt_id,
        )


def _intent_fingerprint_from_prepared(prepared: PreparedExternalActionV1) -> str:
    payload = {
        "contract_version": ACTION_INTENT_CONTRACT_VERSION,
        "action_kind": cast(ActionKindV1, prepared.action_kind).value,
        "connector": prepared.connector,
        "repository": prepared.semantic_payload["repository"],
    }
    if prepared.provenance is not None:
        payload["provenance"] = prepared.provenance.as_dict()
    if cast(ActionKindV1, prepared.action_kind) is ActionKindV1.GITHUB_ISSUE_CREATE:
        body = cast(str, prepared.semantic_payload["body"])
        marker = cast(str, prepared.semantic_payload["marker"])
        payload.update(
            {
                "title": prepared.semantic_payload["title"],
                "body": body.removesuffix("\n" + marker).removesuffix(marker),
            }
        )
    elif cast(ActionKindV1, prepared.action_kind) is ActionKindV1.GITHUB_ISSUE_COMMENT:
        comment = cast(str, prepared.semantic_payload["comment"])
        marker = cast(str, prepared.semantic_payload["marker"])
        payload.update(
            {
                "issue_number": prepared.semantic_payload["issue_number"],
                "comment": comment.removesuffix("\n" + marker).removesuffix(marker),
            }
        )
    else:
        payload.update(
            {
                "issue_number": prepared.semantic_payload["issue_number"],
                "desired_state": prepared.semantic_payload["desired_state"],
            }
        )
    return action_gateway_hash(payload)


__all__ = [
    "ACTION_GATEWAY_CONFIRM_PURPOSE",
    "ACTION_GATEWAY_CONNECTOR",
    "ACTION_GATEWAY_CREDENTIAL_PROFILE_ID",
    "ACTION_GATEWAY_POLICY_FINGERPRINT",
    "ACTION_GATEWAY_POLICY_ID",
    "ACTION_INTENT_CONTRACT_VERSION",
    "PREPARED_ACTION_CONTRACT_VERSION",
    "ActionConnectorV1",
    "ActionExecutionOutcomeV1",
    "ActionExecutionResultV1",
    "ActionGatewayConfirmationError",
    "ActionGatewayConflictError",
    "ActionGatewayConnectorError",
    "ActionGatewayCoreV1",
    "ActionGatewayError",
    "ActionGatewayInvalidRequestError",
    "ActionGatewayTargetChangedError",
    "ActionIntentV1",
    "ActionKind",
    "ActionKindV1",
    "ActionProvenanceV1",
    "ActionReceiptKindV1",
    "ActionReceiptState",
    "ActionReceiptStateV1",
    "ActionReceiptStoreV1",
    "ActionReceiptV1",
    "ConfirmationCodecV1",
    "ConnectorExecutionResultV1",
    "ConnectorPreparedActionV1",
    "ConnectorRevalidationV1",
    "ExactTargetIdentityV1",
    "IssueStateV1",
    "PreparedExternalActionV1",
    "ReversibilityV1",
    "RiskClass",
    "RiskClassV1",
    "action_gateway_hash",
]
