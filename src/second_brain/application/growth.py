"""Provider-free Stage 11A current Goal identity/context core.

This module intentionally composes the existing Stage 4 ``BuildSelfModel``
operation.  It does not scan the vault itself, read a derived cache, call a
provider, persist a result, or expose a transport boundary.  Every Goal
identity is rebuilt from the current validated Self Model result.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Literal
from uuid import UUID

from second_brain.application.ports import VaultReader
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    MAX_SELF_MODEL_LIMIT,
    BuildSelfModel,
    SelfModelClaim,
    SelfModelError,
    SelfModelEvidenceInvalidError,
    SelfModelInvalidClockError,
    SelfModelInvalidRequestError,
    SelfModelPolicy,
    SelfModelPolicyUnavailableError,
    SelfModelRequest,
    SelfModelResult,
    SelfModelResultInvalidError,
    SelfModelResultTooLargeError,
    SelfModelVaultUnavailableError,
    validate_self_model_claim,
    validate_self_model_policy,
    validate_self_model_result,
)
from second_brain.domain.models import (
    EvidenceAtPrecision,
    EvidenceKind,
    SelfKind,
    parse_rfc3339,
    parse_uuid7,
)

type GrowthHashV1 = str
type GrowthEvidenceAt = datetime | Literal["unknown"]
type GrowthClock = Callable[[], datetime]

GROWTH_CONTRACT_VERSION: Final[str] = "growth-engine-v1"
GROWTH_DERIVATION_VERSION: Final[str] = "growth-engine-derivation-v1"
GROWTH_POLICY_ID: Final[str] = "growth-engine-explicit-relation-v1"
GROWTH_POLICY_CANONICAL_JSON: Final[str] = (
    '{"advisor":"none-v1","behavior":"stage10-current-exact-subject-v1",'
    '"comparison":"none-v1","contract":"growth-engine-v1",'
    '"goal":"stage4-direct-goal-v1","mapping":"owner-explicit-goal-choice-v1",'
    '"persistence":"dedicated-operational-growth-mapping-v1",'
    '"relation":"supports-conflicts-neutral-v1",'
    '"selection":"owner-selected-or-per-goal-v1",'
    '"temporal":"separate-times-no-backfill-v1","version":"1"}'
)
GROWTH_POLICY_FINGERPRINT: Final[GrowthHashV1] = (
    "sha256:3fefc6d638b8bb0de3143cbef4ce51bebbae007ee5e95c5fe2aa81f02b4db182"
)

GROWTH_MIN_RESULTS: Final[int] = 1
GROWTH_MAX_RESULTS: Final[int] = 200
GROWTH_MIN_RESULT_BYTES: Final[int] = 1
GROWTH_MAX_RESULT_BYTES: Final[int] = 131_072

# Descriptive aliases follow the naming used by the other application cores.
CONTRACT_VERSION: Final[str] = GROWTH_CONTRACT_VERSION
DERIVATION_VERSION: Final[str] = GROWTH_DERIVATION_VERSION
POLICY_ID: Final[str] = GROWTH_POLICY_ID
POLICY_FINGERPRINT: Final[GrowthHashV1] = GROWTH_POLICY_FINGERPRINT

_GROWTH_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_RAW_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_DOMAIN_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z", re.ASCII)
_GROWTH_POLICY_PAYLOAD: Final[dict[str, str]] = {
    "advisor": "none-v1",
    "behavior": "stage10-current-exact-subject-v1",
    "comparison": "none-v1",
    "contract": GROWTH_CONTRACT_VERSION,
    "goal": "stage4-direct-goal-v1",
    "mapping": "owner-explicit-goal-choice-v1",
    "persistence": "dedicated-operational-growth-mapping-v1",
    "relation": "supports-conflicts-neutral-v1",
    "selection": "owner-selected-or-per-goal-v1",
    "temporal": "separate-times-no-backfill-v1",
    "version": "1",
}


class GrowthGoalSelectionModeV1(StrEnum):
    """The only explicit Goal selection modes in Stage 11A."""

    SELECTED_GOAL = "selected_goal"
    EACH_CURRENT_GOAL = "each_current_goal"


GrowthGoalSelectionMode = GrowthGoalSelectionModeV1


class GrowthCaveatCodeV1(StrEnum):
    """Closed caveats that are safe to expose with a Goal context."""

    CURRENT_GOAL_REVALIDATED = "current_goal_revalidated"
    GOAL_EVIDENCE_TIME_UNKNOWN = "goal_evidence_time_unknown"
    BEHAVIORAL_RELATION_EXACT_ONLY = "behavioral_relation_exact_only"
    CURRENT_BEHAVIOR_REVALIDATED = "current_behavior_revalidated"
    MIXED_NO_WINNER = "mixed_no_winner"
    CHANGED_NOT_A_TRAIT = "changed_not_a_trait"
    INSUFFICIENT_NOT_CONFLICT = "insufficient_not_conflict"
    GOAL_MAPPING_IS_EXPLICIT = "goal_mapping_is_explicit"
    GOAL_MAPPING_MISSING_FOR_OBSERVED_OPTION = "goal_mapping_missing_for_observed_option"
    MAPPING_REVIEW_TIME_IS_NOT_EVIDENCE_TIME = "mapping_review_time_is_not_evidence_time"
    TEMPORAL_ALIGNMENT_NOT_PROVEN = "temporal_alignment_not_proven"
    OUTCOME_PRESENCE_ONLY = "outcome_presence_only"
    PREFERENCE_BRANCH_INDEPENDENT = "preference_branch_independent"
    NO_GROWTH_OPTIMAL_CLAIM = "no_growth_optimal_claim"
    ADVISOR_NOT_USED_V1 = "advisor_not_used_v1"


GrowthCaveatCode = GrowthCaveatCodeV1


class GrowthErrorCode(StrEnum):
    """Fixed safe Growth error vocabulary."""

    INVALID_REQUEST = "GROWTH_INVALID_REQUEST"
    GOAL_SOURCE_UNAVAILABLE = "GROWTH_GOAL_SOURCE_UNAVAILABLE"
    GOAL_MISSING = "GROWTH_GOAL_MISSING"
    GOAL_SOURCE_CHANGED = "GROWTH_GOAL_SOURCE_CHANGED"
    MULTIPLE_GOALS_AMBIGUOUS = "GROWTH_MULTIPLE_GOALS_AMBIGUOUS"
    GOAL_SELECTION_REQUIRED = "GROWTH_GOAL_SELECTION_REQUIRED"
    GOAL_MAPPING_MISSING = "GROWTH_GOAL_MAPPING_MISSING"
    GOAL_MAPPING_INVALID = "GROWTH_GOAL_MAPPING_INVALID"
    GOAL_MAPPING_STALE = "GROWTH_GOAL_MAPPING_STALE"
    BEHAVIORAL_SOURCE_UNAVAILABLE = "GROWTH_BEHAVIORAL_SOURCE_UNAVAILABLE"
    BEHAVIORAL_EVIDENCE_INSUFFICIENT = "GROWTH_BEHAVIORAL_EVIDENCE_INSUFFICIENT"
    BEHAVIORAL_STATE_NOT_COMPARABLE = "GROWTH_BEHAVIORAL_STATE_NOT_COMPARABLE"
    RECOMMENDATION_UNAVAILABLE = "GROWTH_RECOMMENDATION_UNAVAILABLE"
    UNSUPPORTED_SEMANTIC_COMPARISON = "GROWTH_UNSUPPORTED_SEMANTIC_COMPARISON"
    POLICY_MISMATCH = "GROWTH_POLICY_MISMATCH"
    RESULT_TOO_LARGE = "GROWTH_RESULT_TOO_LARGE"
    MAPPING_STORE_UNAVAILABLE = "GROWTH_MAPPING_STORE_UNAVAILABLE"
    MAPPING_STORE_CORRUPT = "GROWTH_MAPPING_STORE_CORRUPT"
    MAPPING_CONFLICT = "GROWTH_MAPPING_CONFLICT"
    IDEMPOTENCY_CONFLICT = "GROWTH_IDEMPOTENCY_CONFLICT"
    CONCURRENCY_CONFLICT = "GROWTH_CONCURRENCY_CONFLICT"


_ERROR_MESSAGES: Final[dict[GrowthErrorCode, str]] = {
    GrowthErrorCode.INVALID_REQUEST: "growth request failed validation",
    GrowthErrorCode.GOAL_SOURCE_UNAVAILABLE: "the current goal source is unavailable",
    GrowthErrorCode.GOAL_MISSING: "the requested goal is missing",
    GrowthErrorCode.GOAL_SOURCE_CHANGED: "the current goal source changed",
    GrowthErrorCode.MULTIPLE_GOALS_AMBIGUOUS: "multiple current goals require explicit selection",
    GrowthErrorCode.GOAL_SELECTION_REQUIRED: "an explicit goal selection is required",
    GrowthErrorCode.GOAL_MAPPING_MISSING: "the current goal mapping is missing",
    GrowthErrorCode.GOAL_MAPPING_INVALID: "the current goal mapping is invalid",
    GrowthErrorCode.GOAL_MAPPING_STALE: "the current goal mapping is stale",
    GrowthErrorCode.BEHAVIORAL_SOURCE_UNAVAILABLE: "the current behavioral source is unavailable",
    GrowthErrorCode.BEHAVIORAL_EVIDENCE_INSUFFICIENT: "behavioral evidence is insufficient",
    GrowthErrorCode.BEHAVIORAL_STATE_NOT_COMPARABLE: "the behavioral state is not comparable",
    GrowthErrorCode.RECOMMENDATION_UNAVAILABLE: "the independent recommendation is unavailable",
    GrowthErrorCode.UNSUPPORTED_SEMANTIC_COMPARISON: "the semantic comparison is unsupported",
    GrowthErrorCode.POLICY_MISMATCH: "the Growth policy binding is invalid",
    GrowthErrorCode.RESULT_TOO_LARGE: "the Growth result exceeds its bounded limit",
    GrowthErrorCode.MAPPING_STORE_UNAVAILABLE: "the Growth mapping store is unavailable",
    GrowthErrorCode.MAPPING_STORE_CORRUPT: "the Growth mapping store is corrupt",
    GrowthErrorCode.MAPPING_CONFLICT: "the Growth mapping is conflicting",
    GrowthErrorCode.IDEMPOTENCY_CONFLICT: (
        "the Growth operation conflicts with an earlier operation"
    ),
    GrowthErrorCode.CONCURRENCY_CONFLICT: "the Growth operation conflicts with current state",
}


class GrowthError(RuntimeError):
    """Safe application error containing only a fixed code and message."""

    def __init__(self, code: GrowthErrorCode | str) -> None:
        normalized = _normalize_error_code(code)
        self.code = normalized.value
        self.message = _ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Return a bounded JSON-compatible error projection."""

        return {"code": self.code, "message": self.message}


class GrowthInvalidRequestError(GrowthError):
    """The request type or bounds are invalid."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.INVALID_REQUEST)


class GrowthGoalSourceUnavailableError(GrowthError):
    """The complete current canonical source cannot be proven."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.GOAL_SOURCE_UNAVAILABLE)


class GrowthGoalMissingError(GrowthError):
    """The explicitly requested UUID is not an eligible current Goal."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.GOAL_MISSING)


class GrowthGoalSourceChangedError(GrowthError):
    """The current Goal identity no longer matches the exact source contract."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.GOAL_SOURCE_CHANGED)


class GrowthMultipleGoalsAmbiguousError(GrowthError):
    """A consumer tried to use an ambiguous multi-goal scope."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.MULTIPLE_GOALS_AMBIGUOUS)


class GrowthGoalSelectionRequiredError(GrowthError):
    """An explicit Goal focus is required by the requested operation."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.GOAL_SELECTION_REQUIRED)


class GrowthPolicyMismatchError(GrowthError):
    """The approved Growth or Stage 4 policy binding cannot be proven."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.POLICY_MISMATCH)


class GrowthResultTooLargeError(GrowthError):
    """The complete result exceeds a declared bound and is never truncated."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.RESULT_TOO_LARGE)


@dataclass(frozen=True, slots=True)
class GrowthGoalSelectionV1:
    """Explicit Goal selector revalidated against the current Self Model."""

    mode: GrowthGoalSelectionModeV1
    source_note_uuid: UUID | None

    def __post_init__(self) -> None:
        mode = _normalize_selection_mode(self.mode)
        source_note_uuid = _normalize_optional_uuid7(self.source_note_uuid)
        if mode is GrowthGoalSelectionModeV1.SELECTED_GOAL and source_note_uuid is None:
            raise ValueError("selected Goal requires a source UUID")
        if mode is GrowthGoalSelectionModeV1.EACH_CURRENT_GOAL and source_note_uuid is not None:
            raise ValueError("each-current-goal selection cannot carry a source UUID")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "source_note_uuid", source_note_uuid)

    @classmethod
    def from_dict(cls, value: object) -> GrowthGoalSelectionV1:
        """Parse an exact mapping without accepting unknown selector fields."""

        if not isinstance(value, Mapping) or set(value) != {"mode", "source_note_uuid"}:
            raise GrowthInvalidRequestError()
        try:
            return cls(value["mode"], value["source_note_uuid"])
        except TypeError, ValueError:
            raise GrowthInvalidRequestError() from None

    def as_dict(self) -> dict[str, str | None]:
        """Return the exact selector projection."""

        return {
            "mode": self.mode.value,
            "source_note_uuid": (
                str(self.source_note_uuid) if self.source_note_uuid is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class GrowthEngineRequestV1:
    """Strict bounded request shared by the future Growth Engine contract."""

    contract_version: str = GROWTH_CONTRACT_VERSION
    selection: GrowthGoalSelectionV1 = field(
        default_factory=lambda: GrowthGoalSelectionV1(
            GrowthGoalSelectionModeV1.EACH_CURRENT_GOAL,
            None,
        )
    )
    max_results: int = GROWTH_MAX_RESULTS
    max_result_bytes: int = GROWTH_MAX_RESULT_BYTES

    def __post_init__(self) -> None:
        if (
            type(self.contract_version) is not str
            or self.contract_version != GROWTH_CONTRACT_VERSION
        ):
            raise ValueError("Growth contract version is invalid")
        if type(self.selection) is not GrowthGoalSelectionV1:
            raise ValueError("Growth selection is invalid")
        if not _valid_bound(self.max_results, GROWTH_MIN_RESULTS, GROWTH_MAX_RESULTS):
            raise ValueError("Growth result bound is invalid")
        if not _valid_bound(
            self.max_result_bytes,
            GROWTH_MIN_RESULT_BYTES,
            GROWTH_MAX_RESULT_BYTES,
        ):
            raise ValueError("Growth byte bound is invalid")

    @classmethod
    def from_dict(cls, value: object) -> GrowthEngineRequestV1:
        """Parse the exact request shape and reject unknown fields."""

        if not isinstance(value, Mapping) or set(value) != {
            "contract_version",
            "selection",
            "max_results",
            "max_result_bytes",
        }:
            raise GrowthInvalidRequestError()
        try:
            return cls(
                contract_version=value["contract_version"],
                selection=GrowthGoalSelectionV1.from_dict(value["selection"]),
                max_results=value["max_results"],
                max_result_bytes=value["max_result_bytes"],
            )
        except TypeError, ValueError, GrowthError:
            raise GrowthInvalidRequestError() from None

    def as_dict(self) -> dict[str, object]:
        """Return the exact request projection without raw private context."""

        return {
            "contract_version": self.contract_version,
            "selection": self.selection.as_dict(),
            "max_results": self.max_results,
            "max_result_bytes": self.max_result_bytes,
        }


class _DefaultGrowthRequest:
    __slots__ = ()


_DEFAULT_GROWTH_REQUEST: Final[_DefaultGrowthRequest] = _DefaultGrowthRequest()


@dataclass(frozen=True, slots=True)
class GrowthGoalIdentityV1:
    """Exact raw-body-free identity for one current Stage 4 Goal claim."""

    source_note_uuid: UUID | str
    dimension: str
    source_evidence_kind: str
    source_self_kind: str
    domain: str | None
    evidence_at: GrowthEvidenceAt
    evidence_at_precision: str
    source_contract_version: str
    source_derivation_version: str
    self_model_policy_fingerprint: str
    source_fingerprint: GrowthHashV1
    claim_fingerprint: GrowthHashV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_note_uuid", _normalize_uuid7(self.source_note_uuid))
        if type(self.dimension) is not str or self.dimension != SelfKind.GOAL.value:
            raise ValueError("Goal dimension is invalid")
        object.__setattr__(
            self,
            "source_evidence_kind",
            _normalize_evidence_kind(self.source_evidence_kind),
        )
        object.__setattr__(self, "source_self_kind", _normalize_self_kind(self.source_self_kind))
        if self.source_self_kind != SelfKind.GOAL.value:
            raise ValueError("Goal self kind is invalid")
        object.__setattr__(self, "domain", _normalize_domain(self.domain))
        evidence_at, precision = _normalize_evidence_time(
            self.evidence_at,
            self.evidence_at_precision,
        )
        object.__setattr__(self, "evidence_at", evidence_at)
        object.__setattr__(self, "evidence_at_precision", precision)
        if (
            type(self.source_contract_version) is not str
            or self.source_contract_version != "self-model-v1"
        ):
            raise ValueError("Goal source contract is invalid")
        if (
            type(self.source_derivation_version) is not str
            or self.source_derivation_version != "self-model-derivation-v1"
        ):
            raise ValueError("Goal source derivation is invalid")
        _validate_raw_hash(self.self_model_policy_fingerprint)
        validate_growth_hash(self.source_fingerprint)
        validate_growth_hash(self.claim_fingerprint)

    def as_dict(self) -> dict[str, object]:
        """Return the exact identity fields; the raw Goal body is absent."""

        return {
            "source_note_uuid": str(self.source_note_uuid),
            "dimension": self.dimension,
            "source_evidence_kind": self.source_evidence_kind,
            "source_self_kind": self.source_self_kind,
            "domain": self.domain,
            "evidence_at": _format_evidence_at(self.evidence_at),
            "evidence_at_precision": self.evidence_at_precision,
            "source_contract_version": self.source_contract_version,
            "source_derivation_version": self.source_derivation_version,
            "self_model_policy_fingerprint": self.self_model_policy_fingerprint,
            "source_fingerprint": self.source_fingerprint,
            "claim_fingerprint": self.claim_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class GrowthGoalContextV1:
    """Bounded in-memory current Goal context, not the full Growth result."""

    contract_version: str
    derivation_version: str
    policy_id: str
    policy_fingerprint: GrowthHashV1
    selection_mode: GrowthGoalSelectionModeV1
    selected_goal_source_uuid: UUID | None
    eligible_goal_count: int
    goals: tuple[GrowthGoalIdentityV1, ...]
    reason_codes: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.contract_version) is not str
            or self.contract_version != GROWTH_CONTRACT_VERSION
        ):
            raise ValueError("Growth context contract is invalid")
        if (
            type(self.derivation_version) is not str
            or self.derivation_version != GROWTH_DERIVATION_VERSION
        ):
            raise ValueError("Growth context derivation is invalid")
        if type(self.policy_id) is not str or self.policy_id != GROWTH_POLICY_ID:
            raise ValueError("Growth context policy is invalid")
        validate_growth_hash(self.policy_fingerprint)
        if self.policy_fingerprint != GROWTH_POLICY_FINGERPRINT:
            raise ValueError("Growth context policy fingerprint is invalid")
        mode = _normalize_selection_mode(self.selection_mode)
        selected_uuid = _normalize_optional_uuid7(self.selected_goal_source_uuid)
        if not _valid_non_negative_int(self.eligible_goal_count):
            raise ValueError("Growth eligible Goal count is invalid")
        if type(self.goals) is not tuple or len(self.goals) > GROWTH_MAX_RESULTS:
            raise ValueError("Growth Goal identities are invalid")
        if any(type(goal) is not GrowthGoalIdentityV1 for goal in self.goals):
            raise ValueError("Growth Goal identities are invalid")
        goal_ids = tuple(str(goal.source_note_uuid) for goal in self.goals)
        if goal_ids != tuple(sorted(goal_ids)) or len(goal_ids) != len(set(goal_ids)):
            raise ValueError("Growth Goal identities are not deterministic")
        if self.eligible_goal_count < len(self.goals):
            raise ValueError("Growth Goal count is inconsistent")
        if mode is GrowthGoalSelectionModeV1.SELECTED_GOAL:
            if selected_uuid is None or len(self.goals) != 1:
                raise ValueError("selected Goal context is invalid")
            if self.goals[0].source_note_uuid != selected_uuid:
                raise ValueError("selected Goal context is invalid")
        elif selected_uuid is not None or self.eligible_goal_count != len(self.goals):
            raise ValueError("each-current-goal context is invalid")
        reason_codes = _normalize_reason_codes(self.reason_codes)
        caveats = _normalize_caveats(self.caveats)
        object.__setattr__(self, "selection_mode", mode)
        object.__setattr__(self, "selected_goal_source_uuid", selected_uuid)
        object.__setattr__(self, "reason_codes", reason_codes)
        object.__setattr__(self, "caveats", caveats)

    @property
    def goal_identities(self) -> tuple[GrowthGoalIdentityV1, ...]:
        """Descriptive alias for consumers that avoid the short ``goals`` name."""

        return self.goals

    def as_dict(self) -> dict[str, object]:
        """Return only the bounded identity/context projection."""

        return {
            "contract_version": self.contract_version,
            "derivation_version": self.derivation_version,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
            "selection_mode": self.selection_mode.value,
            "selected_goal_source_uuid": (
                str(self.selected_goal_source_uuid)
                if self.selected_goal_source_uuid is not None
                else None
            ),
            "eligible_goal_count": self.eligible_goal_count,
            "goals": [goal.as_dict() for goal in self.goals],
            "reason_codes": list(self.reason_codes),
            "caveats": list(self.caveats),
        }

    def to_json(self) -> str:
        """Serialize the complete context with the normative JSON profile."""

        validate_growth_goal_context(self)
        return canonical_growth_json(self.as_dict())


# Names with an explicit result suffix are convenient for callers while
# remaining the same narrow DTO, not a GrowthEngineResult implementation.
GrowthGoalContextResultV1 = GrowthGoalContextV1
GrowthGoalContext = GrowthGoalContextV1


@dataclass(frozen=True, slots=True)
class BuildGrowthGoalContext:
    """Rebuild the bounded current Goal context from the current vault."""

    reader: VaultReader
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY
    clock: GrowthClock = lambda: datetime.now(UTC)

    def execute(
        self,
        request: GrowthEngineRequestV1 | _DefaultGrowthRequest = _DEFAULT_GROWTH_REQUEST,
    ) -> GrowthGoalContextV1:
        """Return a complete current Goal context or one fixed safe error."""

        if isinstance(request, _DefaultGrowthRequest):
            request = GrowthEngineRequestV1()
        request = validate_growth_engine_request(request)
        try:
            validate_growth_policy()
            self_model_policy_fingerprint = validate_self_model_policy(self.policy)
        except TypeError, ValueError, GrowthError, SelfModelError:
            raise GrowthPolicyMismatchError() from None

        try:
            self_model = BuildSelfModel(
                self.reader,
                policy=self.policy,
                clock=self.clock,
            ).execute(
                SelfModelRequest(
                    max_claims=MAX_SELF_MODEL_LIMIT,
                    max_evidence_refs_per_claim=MAX_SELF_MODEL_LIMIT,
                )
            )
        except SelfModelInvalidRequestError, SelfModelInvalidClockError:
            raise GrowthInvalidRequestError() from None
        except SelfModelPolicyUnavailableError:
            raise GrowthPolicyMismatchError() from None
        except SelfModelResultTooLargeError:
            raise GrowthResultTooLargeError() from None
        except (
            SelfModelVaultUnavailableError,
            SelfModelEvidenceInvalidError,
            SelfModelResultInvalidError,
            SelfModelError,
        ):
            raise GrowthGoalSourceUnavailableError() from None
        except Exception:
            raise GrowthGoalSourceUnavailableError() from None

        try:
            all_goals = tuple(
                sorted(
                    (
                        build_growth_goal_identity(
                            claim,
                            self_model,
                            policy=self.policy,
                            expected_self_model_policy_fingerprint=self_model_policy_fingerprint,
                        )
                        for claim in self_model.claims
                        if claim.dimension.value == SelfKind.GOAL.value
                    ),
                    key=lambda goal: str(goal.source_note_uuid),
                )
            )
        except GrowthError:
            raise
        except Exception:
            raise GrowthGoalSourceChangedError() from None

        selection = request.selection
        if selection.mode is GrowthGoalSelectionModeV1.SELECTED_GOAL:
            assert selection.source_note_uuid is not None
            selected = next(
                (goal for goal in all_goals if goal.source_note_uuid == selection.source_note_uuid),
                None,
            )
            if selected is None:
                raise GrowthGoalMissingError()
            selected_goals: tuple[GrowthGoalIdentityV1, ...] = (selected,)
        else:
            if len(all_goals) > request.max_results:
                raise GrowthResultTooLargeError()
            selected_goals = all_goals

        context = GrowthGoalContextV1(
            contract_version=GROWTH_CONTRACT_VERSION,
            derivation_version=GROWTH_DERIVATION_VERSION,
            policy_id=GROWTH_POLICY_ID,
            policy_fingerprint=GROWTH_POLICY_FINGERPRINT,
            selection_mode=selection.mode,
            selected_goal_source_uuid=(
                selection.source_note_uuid
                if selection.mode is GrowthGoalSelectionModeV1.SELECTED_GOAL
                else None
            ),
            eligible_goal_count=len(all_goals),
            goals=selected_goals,
            caveats=_context_caveats(selected_goals),
        )
        validate_growth_goal_context(context)
        if len(context.to_json().encode("utf-8")) > request.max_result_bytes:
            raise GrowthResultTooLargeError()
        return context


BuildGrowthGoalContextV1 = BuildGrowthGoalContext
BuildGrowthGoals = BuildGrowthGoalContext


def validate_growth_policy() -> GrowthHashV1:
    """Validate the compiled Growth policy against its normative payload."""

    if canonical_growth_json(_GROWTH_POLICY_PAYLOAD) != GROWTH_POLICY_CANONICAL_JSON:
        raise GrowthPolicyMismatchError()
    fingerprint = growth_hash_json(_GROWTH_POLICY_PAYLOAD)
    if fingerprint != GROWTH_POLICY_FINGERPRINT:
        raise GrowthPolicyMismatchError()
    return fingerprint


def validate_growth_engine_request(value: object) -> GrowthEngineRequestV1:
    """Validate request type, selector and bounds before any vault read."""

    if type(value) is not GrowthEngineRequestV1:
        raise GrowthInvalidRequestError()
    request = value
    try:
        if (
            type(request.contract_version) is not str
            or request.contract_version != GROWTH_CONTRACT_VERSION
            or type(request.selection) is not GrowthGoalSelectionV1
            or not _valid_bound(request.max_results, GROWTH_MIN_RESULTS, GROWTH_MAX_RESULTS)
            or not _valid_bound(
                request.max_result_bytes,
                GROWTH_MIN_RESULT_BYTES,
                GROWTH_MAX_RESULT_BYTES,
            )
        ):
            raise ValueError
        validate_growth_goal_selection(request.selection)
    except TypeError, ValueError:
        raise GrowthInvalidRequestError() from None
    return request


def validate_growth_goal_selection(value: object) -> GrowthGoalSelectionV1:
    """Validate the exact selected/every-current selector."""

    if type(value) is not GrowthGoalSelectionV1:
        raise GrowthInvalidRequestError()
    selection = value
    try:
        mode = _normalize_selection_mode(selection.mode)
        source_uuid = _normalize_optional_uuid7(selection.source_note_uuid)
        if mode is GrowthGoalSelectionModeV1.SELECTED_GOAL and source_uuid is None:
            raise ValueError
        if mode is GrowthGoalSelectionModeV1.EACH_CURRENT_GOAL and source_uuid is not None:
            raise ValueError
    except TypeError, ValueError:
        raise GrowthInvalidRequestError() from None
    return selection


def validate_growth_goal_identity(value: object) -> GrowthGoalIdentityV1:
    """Validate one strict raw-body-free Goal identity DTO."""

    if type(value) is not GrowthGoalIdentityV1:
        raise ValueError("Goal identity is invalid")
    identity = value
    try:
        GrowthGoalIdentityV1(
            source_note_uuid=identity.source_note_uuid,
            dimension=identity.dimension,
            source_evidence_kind=identity.source_evidence_kind,
            source_self_kind=identity.source_self_kind,
            domain=identity.domain,
            evidence_at=identity.evidence_at,
            evidence_at_precision=identity.evidence_at_precision,
            source_contract_version=identity.source_contract_version,
            source_derivation_version=identity.source_derivation_version,
            self_model_policy_fingerprint=identity.self_model_policy_fingerprint,
            source_fingerprint=identity.source_fingerprint,
            claim_fingerprint=identity.claim_fingerprint,
        )
    except TypeError, ValueError:
        raise ValueError("Goal identity is invalid") from None
    return identity


def validate_growth_goal_context(value: object) -> GrowthGoalContextV1:
    """Validate the complete bounded Goal context and deterministic order."""

    if type(value) is not GrowthGoalContextV1:
        raise GrowthGoalSourceChangedError()
    context = value
    try:
        if validate_growth_policy() != context.policy_fingerprint:
            raise ValueError
        if type(context.goals) is not tuple:
            raise ValueError
        for goal in context.goals:
            validate_growth_goal_identity(goal)
        GrowthGoalContextV1(
            contract_version=context.contract_version,
            derivation_version=context.derivation_version,
            policy_id=context.policy_id,
            policy_fingerprint=context.policy_fingerprint,
            selection_mode=context.selection_mode,
            selected_goal_source_uuid=context.selected_goal_source_uuid,
            eligible_goal_count=context.eligible_goal_count,
            goals=context.goals,
            reason_codes=context.reason_codes,
            caveats=context.caveats,
        )
    except GrowthError:
        raise
    except TypeError, ValueError:
        raise GrowthError(GrowthErrorCode.GOAL_SOURCE_CHANGED) from None
    return context


def build_growth_goal_identity(
    claim: SelfModelClaim,
    result: SelfModelResult,
    *,
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY,
    expected_self_model_policy_fingerprint: str | None = None,
) -> GrowthGoalIdentityV1:
    """Build one exact Goal identity from one current Stage 4 claim."""

    if type(claim) is not SelfModelClaim or type(result) is not SelfModelResult:
        raise GrowthGoalSourceChangedError()
    try:
        policy_fingerprint = validate_self_model_policy(policy)
    except TypeError, ValueError, SelfModelError:
        raise GrowthPolicyMismatchError() from None
    if expected_self_model_policy_fingerprint is not None:
        policy_fingerprint = expected_self_model_policy_fingerprint
    try:
        validate_self_model_result(
            result,
            request=SelfModelRequest(
                max_claims=MAX_SELF_MODEL_LIMIT,
                max_evidence_refs_per_claim=MAX_SELF_MODEL_LIMIT,
            ),
            policy=policy,
            expected_policy_fingerprint=policy_fingerprint,
        )
        validate_self_model_claim(
            claim,
            max_evidence_refs_per_claim=MAX_SELF_MODEL_LIMIT,
        )
    except SelfModelPolicyUnavailableError:
        raise GrowthPolicyMismatchError() from None
    except SelfModelError:
        raise GrowthGoalSourceChangedError() from None
    if claim not in result.claims:
        raise GrowthGoalSourceChangedError()
    if (
        type(result.policy_fingerprint) is not str
        or result.policy_fingerprint != policy_fingerprint
        or result.derivation_version != "self-model-derivation-v1"
        or claim.derivation_version != "self-model-derivation-v1"
        or claim.generated_at != result.generated_at
    ):
        raise GrowthPolicyMismatchError()
    if (
        claim.dimension.value != SelfKind.GOAL.value
        or len(claim.supporting_evidence) != 1
        or claim.contradicting_evidence != ()
        or claim.contextual_evidence != ()
        or claim.status is not None
    ):
        raise GrowthGoalSourceChangedError()
    evidence = claim.supporting_evidence[0]
    evidence_kind = _enum_value(evidence.evidence_kind)
    self_kind = _enum_value(evidence.self_kind)
    if (
        evidence_kind
        not in {
            EvidenceKind.EXPLICIT_USER_FACT.value,
            EvidenceKind.USER_STATEMENT.value,
        }
        or self_kind != SelfKind.GOAL.value
    ):
        raise GrowthGoalSourceChangedError()
    if claim.domain != evidence.domain:
        raise GrowthGoalSourceChangedError()
    try:
        source_uuid = _normalize_uuid7(evidence.note_id)
        domain = _normalize_domain(evidence.domain)
        evidence_at, precision = _normalize_evidence_time(
            evidence.evidence_at,
            evidence.evidence_at_precision,
        )
        claim_text_fingerprint = growth_hash_text(claim.claim)
        claim_fingerprint = growth_hash_json(
            {
                "claim_text_fingerprint": claim_text_fingerprint,
                "dimension": SelfKind.GOAL.value,
                "domain": domain,
                "source_contract_version": "self-model-v1",
                "source_derivation_version": "self-model-derivation-v1",
                "source_note_uuid": str(source_uuid),
            }
        )
        source_fingerprint = growth_hash_json(
            {
                "claim_fingerprint": claim_fingerprint,
                "domain": domain,
                "evidence_at": _format_evidence_at(evidence_at),
                "evidence_at_precision": precision,
                "evidence_kind": evidence_kind,
                "self_kind": SelfKind.GOAL.value,
                "source_contract_version": "self-model-v1",
                "source_note_uuid": str(source_uuid),
            }
        )
        return GrowthGoalIdentityV1(
            source_note_uuid=source_uuid,
            dimension=SelfKind.GOAL.value,
            source_evidence_kind=evidence_kind,
            source_self_kind=SelfKind.GOAL.value,
            domain=domain,
            evidence_at=evidence_at,
            evidence_at_precision=precision,
            source_contract_version="self-model-v1",
            source_derivation_version="self-model-derivation-v1",
            self_model_policy_fingerprint=policy_fingerprint,
            source_fingerprint=source_fingerprint,
            claim_fingerprint=claim_fingerprint,
        )
    except GrowthError:
        raise
    except TypeError, ValueError, UnicodeError:
        raise GrowthGoalSourceChangedError() from None


def canonical_growth_json(value: object) -> str:
    """Serialize the exact Growth canonical JSON profile."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except TypeError, ValueError, UnicodeError:
        raise ValueError("value is not canonical Growth JSON") from None


def growth_hash_json(value: object) -> GrowthHashV1:
    """Hash canonical UTF-8 JSON using the normative ``sha256:`` form."""

    return "sha256:" + hashlib.sha256(canonical_growth_json(value).encode("utf-8")).hexdigest()


def growth_hash_text(value: str) -> GrowthHashV1:
    """Hash exact UTF-8 claim bytes without text normalization."""

    if type(value) is not str:
        raise ValueError("Growth text is invalid")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Growth text is invalid") from None
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_growth_hash(value: object) -> GrowthHashV1:
    """Validate a complete ``GrowthHashV1`` value."""

    if type(value) is not str or _GROWTH_HASH_PATTERN.fullmatch(value) is None:
        raise ValueError("Growth hash is invalid")
    return value


def serialize_growth_goal_context(context: GrowthGoalContextV1) -> str:
    """Return canonical context JSON for the result byte bound."""

    validate_growth_goal_context(context)
    return context.to_json()


def _normalize_error_code(code: GrowthErrorCode | str) -> GrowthErrorCode:
    if isinstance(code, GrowthErrorCode):
        return code
    try:
        return GrowthErrorCode(code)
    except TypeError, ValueError:
        return GrowthErrorCode.INVALID_REQUEST


def _normalize_selection_mode(value: object) -> GrowthGoalSelectionModeV1:
    if type(value) is GrowthGoalSelectionModeV1:
        return value
    if type(value) is str:
        try:
            return GrowthGoalSelectionModeV1(value)
        except ValueError:
            pass
    raise ValueError("Growth selection mode is invalid")


def _normalize_uuid7(value: object) -> UUID:
    try:
        parsed = parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        raise ValueError("UUIDv7 is invalid") from None
    if type(parsed) is not UUID or parsed.version != 7:
        raise ValueError("UUIDv7 is invalid")
    return parsed


def _normalize_optional_uuid7(value: object) -> UUID | None:
    if value is None:
        return None
    return _normalize_uuid7(value)


def _normalize_evidence_kind(value: object) -> str:
    if type(value) is EvidenceKind:
        normalized = value.value
    elif type(value) is str:
        normalized = value
    else:
        raise ValueError("Goal evidence kind is invalid")
    if normalized not in {
        EvidenceKind.EXPLICIT_USER_FACT.value,
        EvidenceKind.USER_STATEMENT.value,
    }:
        raise ValueError("Goal evidence kind is invalid")
    return normalized


def _normalize_self_kind(value: object) -> str:
    if type(value) is SelfKind:
        normalized = value.value
    elif type(value) is str:
        normalized = value
    else:
        raise ValueError("Goal self kind is invalid")
    if normalized != SelfKind.GOAL.value:
        raise ValueError("Goal self kind is invalid")
    return normalized


def _enum_value(value: object) -> str:
    if isinstance(value, StrEnum):
        return value.value
    if type(value) is str:
        return value
    raise ValueError("enum value is invalid")


def _normalize_domain(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value:
        raise ValueError("Goal domain is invalid")
    try:
        if len(value.encode("utf-8")) > 64:
            raise ValueError("Goal domain is invalid")
    except UnicodeEncodeError:
        raise ValueError("Goal domain is invalid") from None
    if _DOMAIN_PATTERN.fullmatch(value) is None:
        raise ValueError("Goal domain is invalid")
    return value


def _normalize_evidence_time(
    value: object,
    precision: object,
) -> tuple[GrowthEvidenceAt, str]:
    if type(precision) is EvidenceAtPrecision:
        normalized_precision = precision.value
    elif type(precision) is str:
        normalized_precision = precision
    else:
        raise ValueError("Goal evidence precision is invalid")
    if normalized_precision not in {
        EvidenceAtPrecision.EXACT.value,
        EvidenceAtPrecision.UNKNOWN.value,
    }:
        raise ValueError("Goal evidence precision is invalid")
    if type(value) is str and value == "unknown":
        if normalized_precision != EvidenceAtPrecision.UNKNOWN.value:
            raise ValueError("Goal evidence precision is invalid")
        return "unknown", EvidenceAtPrecision.UNKNOWN.value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Goal evidence time is invalid")
        if normalized_precision != EvidenceAtPrecision.EXACT.value:
            raise ValueError("Goal evidence precision is invalid")
        return value.astimezone(UTC), EvidenceAtPrecision.EXACT.value
    if type(value) is str:
        try:
            parsed = parse_rfc3339(value)
        except TypeError, ValueError, OverflowError:
            raise ValueError("Goal evidence time is invalid") from None
        if normalized_precision != EvidenceAtPrecision.EXACT.value:
            raise ValueError("Goal evidence precision is invalid")
        return parsed.astimezone(UTC), EvidenceAtPrecision.EXACT.value
    raise ValueError("Goal evidence time is invalid")


def _format_evidence_at(value: GrowthEvidenceAt) -> str:
    if value == "unknown":
        return "unknown"
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Goal evidence time is invalid")
    normalized = value.astimezone(UTC)
    rendered = normalized.isoformat(
        timespec="microseconds" if normalized.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def _validate_raw_hash(value: object) -> str:
    if type(value) is not str or _RAW_HASH_PATTERN.fullmatch(value) is None:
        raise ValueError("raw policy hash is invalid")
    return value


def _valid_bound(value: object, lower: int, upper: int) -> bool:
    return type(value) is int and lower <= value <= upper


def _valid_non_negative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _normalize_reason_codes(
    values: tuple[GrowthErrorCode | str, ...],
) -> tuple[str, ...]:
    if type(values) is not tuple:
        raise ValueError("Growth reason codes are invalid")
    normalized: list[str] = []
    for value in values:
        code = _normalize_error_code(value)
        if isinstance(value, str) and value not in {item.value for item in GrowthErrorCode}:
            raise ValueError("Growth reason code is invalid")
        normalized.append(code.value)
    if len(normalized) != len(set(normalized)):
        raise ValueError("Growth reason codes are invalid")
    return tuple(normalized)


def _normalize_caveats(
    values: tuple[GrowthCaveatCodeV1 | str, ...],
) -> tuple[str, ...]:
    if type(values) is not tuple:
        raise ValueError("Growth caveats are invalid")
    normalized: list[str] = []
    for value in values:
        if isinstance(value, GrowthCaveatCodeV1):
            normalized.append(value.value)
        elif type(value) is str and value in {item.value for item in GrowthCaveatCodeV1}:
            normalized.append(value)
        else:
            raise ValueError("Growth caveat is invalid")
    if len(normalized) != len(set(normalized)):
        raise ValueError("Growth caveats are invalid")
    order = {item.value: index for index, item in enumerate(GrowthCaveatCodeV1)}
    if normalized != sorted(normalized, key=order.__getitem__):
        raise ValueError("Growth caveats are not deterministic")
    return tuple(normalized)


def _context_caveats(
    goals: tuple[GrowthGoalIdentityV1, ...],
) -> tuple[GrowthCaveatCodeV1, ...]:
    caveats: list[GrowthCaveatCodeV1] = [GrowthCaveatCodeV1.CURRENT_GOAL_REVALIDATED]
    if any(goal.evidence_at == "unknown" for goal in goals):
        caveats.append(GrowthCaveatCodeV1.GOAL_EVIDENCE_TIME_UNKNOWN)
    return tuple(caveats)


__all__ = [
    "CONTRACT_VERSION",
    "DERIVATION_VERSION",
    "GROWTH_CONTRACT_VERSION",
    "GROWTH_DERIVATION_VERSION",
    "GROWTH_MAX_RESULTS",
    "GROWTH_MAX_RESULT_BYTES",
    "GROWTH_MIN_RESULTS",
    "GROWTH_MIN_RESULT_BYTES",
    "GROWTH_POLICY_CANONICAL_JSON",
    "GROWTH_POLICY_FINGERPRINT",
    "GROWTH_POLICY_ID",
    "POLICY_FINGERPRINT",
    "POLICY_ID",
    "BuildGrowthGoalContext",
    "BuildGrowthGoalContextV1",
    "BuildGrowthGoals",
    "GrowthCaveatCode",
    "GrowthCaveatCodeV1",
    "GrowthEngineRequestV1",
    "GrowthError",
    "GrowthErrorCode",
    "GrowthGoalContext",
    "GrowthGoalContextResultV1",
    "GrowthGoalContextV1",
    "GrowthGoalIdentityV1",
    "GrowthGoalMissingError",
    "GrowthGoalSelectionMode",
    "GrowthGoalSelectionModeV1",
    "GrowthGoalSelectionRequiredError",
    "GrowthGoalSelectionV1",
    "GrowthGoalSourceChangedError",
    "GrowthGoalSourceUnavailableError",
    "GrowthInvalidRequestError",
    "GrowthMultipleGoalsAmbiguousError",
    "GrowthPolicyMismatchError",
    "GrowthResultTooLargeError",
    "build_growth_goal_identity",
    "canonical_growth_json",
    "growth_hash_json",
    "growth_hash_text",
    "serialize_growth_goal_context",
    "validate_growth_engine_request",
    "validate_growth_goal_context",
    "validate_growth_goal_identity",
    "validate_growth_goal_selection",
    "validate_growth_hash",
    "validate_growth_policy",
]
