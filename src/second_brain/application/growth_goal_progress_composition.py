"""Provider-free Stage 12D Growth and Goal Progress composition.

This module is an additive private read-model boundary.  It invokes the
existing Stage 11 Growth Engine and Stage 12C Goal Progress builders, keeps
their outputs in separate namespaces, and only composes them after proving
the exact current Goal identity.  It does not call Advisor, Learning, a
provider or a writer, and it has no persistence capability.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, cast
from uuid import UUID

from second_brain.application.goal_progress import (
    DEFAULT_GOAL_PROGRESS_POLICY,
    GOAL_PROGRESS_POLICY_FINGERPRINT,
    validate_goal_progress_policy,
)
from second_brain.application.goal_progress_read import (
    MAX_GOAL_PROGRESS_RESULT_BYTES,
    BuildGoalProgress,
    GoalProgressError,
    GoalProgressGoalAmbiguousError,
    GoalProgressGoalSourceChangedError,
    GoalProgressInvalidRequestError,
    GoalProgressPolicyMismatchError,
    GoalProgressRequestV1,
    GoalProgressResultTooLargeError,
    GoalProgressResultV1,
    GoalProgressSourceUnavailableError,
    GoalProgressStatusV1,
    validate_goal_progress_result,
)
from second_brain.application.growth import (
    GROWTH_MAX_RESULT_BYTES,
    GROWTH_POLICY_FINGERPRINT,
    BuildGrowthEngine,
    BuildGrowthGoalContext,
    GrowthEngineRequestV1,
    GrowthEngineResultV1,
    GrowthError,
    GrowthGoalIdentityV1,
    GrowthGoalMissingError,
    GrowthGoalSelectionModeV1,
    GrowthGoalSelectionV1,
    GrowthGoalSourceChangedError,
    GrowthInvalidRequestError,
    GrowthMappingStore,
    GrowthPolicyMismatchError,
    GrowthResultTooLargeError,
    growth_hash_json,
    validate_growth_engine_result,
    validate_growth_policy,
)
from second_brain.application.ports import VaultReader
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    SelfModelPolicy,
)
from second_brain.domain.models import parse_rfc3339, parse_uuid7

type CompositionClock = Callable[[], datetime]
type CompositionHashV1 = str

COMPOSITION_CONTRACT_VERSION: Final[str] = "growth_goal_progress_composition_v1"
COMPOSITION_DERIVATION_VERSION: Final[str] = "growth-goal-progress-composition-derivation-v1"
COMPOSITION_POLICY_ID: Final[str] = "growth-goal-progress-composition-v1"
COMPOSITION_POLICY_PAYLOAD: Final[dict[str, object]] = {
    "advisor": "forbidden",
    "binding": "exact-current-goal-identity-v1",
    "bounds": "growth-max-plus-progress-max-plus-overhead-v1",
    "causal_inference": "forbidden",
    "contract": COMPOSITION_CONTRACT_VERSION,
    "cross_branch_inference": "forbidden",
    "growth_branch": "growth_engine_v1",
    "learning": "forbidden",
    "persistence": "none-v1",
    "progress_branch": "goal_progress_v1",
    "provider": "forbidden",
    "selection": "explicit-selected-goal-only-v1",
    "temporal": "separate-times-no-alignment-v1",
    "version": 1,
    "write": "forbidden",
}
COMPOSITION_POLICY_CANONICAL_JSON: Final[str] = (
    '{"advisor":"forbidden","binding":"exact-current-goal-identity-v1",'
    '"bounds":"growth-max-plus-progress-max-plus-overhead-v1",'
    '"causal_inference":"forbidden","contract":"growth_goal_progress_composition_v1",'
    '"cross_branch_inference":"forbidden","growth_branch":"growth_engine_v1",'
    '"learning":"forbidden","persistence":"none-v1",'
    '"progress_branch":"goal_progress_v1","provider":"forbidden",'
    '"selection":"explicit-selected-goal-only-v1",'
    '"temporal":"separate-times-no-alignment-v1","version":1,"write":"forbidden"}'
)
COMPOSITION_POLICY_FINGERPRINT: Final[CompositionHashV1] = (
    "sha256:30b7c0b7aea4e90c857402ee9890c20eeaf7ec6222dad3ebab07a01deca01e5d"
)

# The complete result is bounded by the already approved branch limits plus
# a fixed allowance for this DTO's keys, caveats and provenance.  Branch
# results are never truncated to fit the outer limit.
COMPOSITION_STRUCTURAL_OVERHEAD_BYTES: Final[int] = 8 * 1024
MAX_COMPOSITION_RESULT_BYTES: Final[int] = (
    GROWTH_MAX_RESULT_BYTES + MAX_GOAL_PROGRESS_RESULT_BYTES + COMPOSITION_STRUCTURAL_OVERHEAD_BYTES
)
MAX_GROWTH_GOAL_PROGRESS_COMPOSITION_RESULT_BYTES: Final[int] = MAX_COMPOSITION_RESULT_BYTES

_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)


class GrowthGoalProgressCompositionErrorCodeV1(StrEnum):
    """Closed errors for the Stage 12D composition boundary."""

    INVALID_REQUEST = "GROWTH_PROGRESS_COMPOSITION_INVALID_REQUEST"
    GOAL_BINDING_MISMATCH = "GROWTH_PROGRESS_COMPOSITION_GOAL_BINDING_MISMATCH"
    SOURCE_CHANGED = "GROWTH_PROGRESS_COMPOSITION_SOURCE_CHANGED"
    SOURCE_UNAVAILABLE = "GROWTH_PROGRESS_COMPOSITION_SOURCE_UNAVAILABLE"
    POLICY_MISMATCH = "GROWTH_PROGRESS_COMPOSITION_POLICY_MISMATCH"
    RESULT_TOO_LARGE = "GROWTH_PROGRESS_COMPOSITION_RESULT_TOO_LARGE"
    INTERNAL = "GROWTH_PROGRESS_COMPOSITION_INTERNAL"


GrowthGoalProgressCompositionErrorCode = GrowthGoalProgressCompositionErrorCodeV1
CompositionErrorCodeV1 = GrowthGoalProgressCompositionErrorCodeV1

_ERROR_MESSAGES: Final[dict[GrowthGoalProgressCompositionErrorCodeV1, str]] = {
    GrowthGoalProgressCompositionErrorCodeV1.INVALID_REQUEST: (
        "the Growth and Goal Progress composition request is invalid"
    ),
    GrowthGoalProgressCompositionErrorCodeV1.GOAL_BINDING_MISMATCH: (
        "the Growth and Goal Progress Goal binding does not match"
    ),
    GrowthGoalProgressCompositionErrorCodeV1.SOURCE_CHANGED: (
        "the current Goal source changed during composition"
    ),
    GrowthGoalProgressCompositionErrorCodeV1.SOURCE_UNAVAILABLE: (
        "the Growth or Goal Progress source is unavailable"
    ),
    GrowthGoalProgressCompositionErrorCodeV1.POLICY_MISMATCH: (
        "the Growth and Goal Progress policy binding is invalid"
    ),
    GrowthGoalProgressCompositionErrorCodeV1.RESULT_TOO_LARGE: (
        "the Growth and Goal Progress composition exceeds its bounded limit"
    ),
    GrowthGoalProgressCompositionErrorCodeV1.INTERNAL: (
        "the Growth and Goal Progress composition is unavailable"
    ),
}


class GrowthGoalProgressCompositionError(RuntimeError):
    """Safe composition error containing only a fixed code and message."""

    def __init__(self, code: GrowthGoalProgressCompositionErrorCodeV1 | str) -> None:
        normalized = _normalize_error_code(code)
        self.code = normalized.value
        self.message = _ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Return the bounded public error projection."""

        return {"code": self.code, "message": self.message}


class GrowthGoalProgressCompositionCaveatV1(StrEnum):
    """Fixed caveats that make the non-inferential boundary explicit."""

    GROWTH_AND_PROGRESS_ARE_INDEPENDENT_LAYERS = "growth_and_progress_are_independent_layers"
    NO_CAUSAL_CLAIM = "no_causal_claim"
    NO_PROGRESS_INFERRED_FROM_GROWTH = "no_progress_inferred_from_growth"
    NO_GROWTH_RELATION_INFERRED_FROM_PROGRESS = "no_growth_relation_inferred_from_progress"
    TEMPORAL_ALIGNMENT_NOT_PROVEN = "temporal_alignment_not_proven"
    ADVISOR_NOT_USED = "advisor_not_used"
    LEARNING_NOT_USED = "learning_not_used"


CompositionCaveatV1 = GrowthGoalProgressCompositionCaveatV1
COMPOSITION_CAVEATS: Final[tuple[GrowthGoalProgressCompositionCaveatV1, ...]] = tuple(
    GrowthGoalProgressCompositionCaveatV1
)


@dataclass(frozen=True, slots=True)
class GrowthGoalProgressCompositionRequestV1:
    """Explicit request for one exact Goal and one UTC Progress cutoff."""

    goal_source_uuid: UUID | str | None = None
    progress_as_of: datetime | str | None = None

    @classmethod
    def from_dict(cls, value: object) -> GrowthGoalProgressCompositionRequestV1:
        """Parse exactly the two caller-owned request fields."""

        if not isinstance(value, Mapping) or set(value) != {
            "goal_source_uuid",
            "progress_as_of",
        }:
            raise GrowthGoalProgressCompositionError(
                GrowthGoalProgressCompositionErrorCodeV1.INVALID_REQUEST
            )
        return cls(value["goal_source_uuid"], value["progress_as_of"])

    def as_dict(self) -> dict[str, object]:
        """Return a bounded request projection without generated values."""

        return {
            "goal_source_uuid": (
                str(self.goal_source_uuid) if self.goal_source_uuid is not None else None
            ),
            "progress_as_of": _timestamp_projection(self.progress_as_of),
        }


CompositionRequestV1 = GrowthGoalProgressCompositionRequestV1


@dataclass(frozen=True, slots=True)
class GrowthGoalProgressCompositionProvenanceV1:
    """Bounded proof of the read-only source and binding boundary."""

    source: str
    provider: str
    network: str
    write: str
    goal_binding: str
    temporal_alignment: str
    selected_goal_source_uuid: UUID | str
    current_goal_identity_fingerprint: str
    progress_as_of: datetime | str
    growth_policy_fingerprint: str
    goal_progress_policy_fingerprint: str

    def __post_init__(self) -> None:
        if self.source != "current_vault_and_growth_mapping_store":
            raise ValueError("composition provenance source is invalid")
        if self.provider != "none" or self.network != "none" or self.write != "none":
            raise ValueError("composition provenance side effects are invalid")
        if self.goal_binding != "exact_current_identity":
            raise ValueError("composition provenance binding is invalid")
        if self.temporal_alignment != "not_proven":
            raise ValueError("composition provenance temporal value is invalid")
        object.__setattr__(
            self, "selected_goal_source_uuid", _parse_uuid(self.selected_goal_source_uuid)
        )
        object.__setattr__(
            self,
            "progress_as_of",
            _parse_exact_utc(self.progress_as_of),
        )
        _parse_hash(self.current_goal_identity_fingerprint)
        if self.growth_policy_fingerprint != GROWTH_POLICY_FINGERPRINT:
            raise ValueError("composition provenance Growth policy is invalid")
        if self.goal_progress_policy_fingerprint != GOAL_PROGRESS_POLICY_FINGERPRINT:
            raise ValueError("composition provenance Goal Progress policy is invalid")

    def as_dict(self) -> dict[str, object]:
        """Return provenance without paths, bodies or transient identifiers."""

        return {
            "source": self.source,
            "provider": self.provider,
            "network": self.network,
            "write": self.write,
            "goal_binding": self.goal_binding,
            "temporal_alignment": self.temporal_alignment,
            "selected_goal_source_uuid": str(self.selected_goal_source_uuid),
            "current_goal_identity_fingerprint": self.current_goal_identity_fingerprint,
            "progress_as_of": _format_timestamp(cast(datetime, self.progress_as_of)),
            "growth_policy_fingerprint": self.growth_policy_fingerprint,
            "goal_progress_policy_fingerprint": self.goal_progress_policy_fingerprint,
        }


CompositionProvenanceV1 = GrowthGoalProgressCompositionProvenanceV1


@dataclass(frozen=True, slots=True)
class GrowthGoalProgressCompositionResultV1:
    """Immutable side-by-side result with no cross-branch verdict."""

    contract_version: str
    derivation_version: str
    policy_id: str
    policy_fingerprint: CompositionHashV1
    selected_goal_source_uuid: UUID | str
    current_goal_identity_fingerprint: CompositionHashV1
    progress_as_of: datetime | str
    growth_policy_fingerprint: str
    goal_progress_policy_fingerprint: str
    growth_result: GrowthEngineResultV1
    goal_progress_result: GoalProgressResultV1
    caveats: tuple[GrowthGoalProgressCompositionCaveatV1 | str, ...]
    provenance: GrowthGoalProgressCompositionProvenanceV1

    def __post_init__(self) -> None:
        if (
            self.contract_version != COMPOSITION_CONTRACT_VERSION
            or self.derivation_version != COMPOSITION_DERIVATION_VERSION
            or self.policy_id != COMPOSITION_POLICY_ID
            or self.policy_fingerprint != COMPOSITION_POLICY_FINGERPRINT
        ):
            raise ValueError("composition policy is invalid")
        object.__setattr__(
            self, "selected_goal_source_uuid", _parse_uuid(self.selected_goal_source_uuid)
        )
        object.__setattr__(
            self,
            "current_goal_identity_fingerprint",
            _parse_hash(self.current_goal_identity_fingerprint),
        )
        object.__setattr__(self, "progress_as_of", _parse_exact_utc(self.progress_as_of))
        if self.growth_policy_fingerprint != GROWTH_POLICY_FINGERPRINT:
            raise ValueError("composition Growth policy is invalid")
        if self.goal_progress_policy_fingerprint != GOAL_PROGRESS_POLICY_FINGERPRINT:
            raise ValueError("composition Goal Progress policy is invalid")
        if type(self.growth_result) is not GrowthEngineResultV1:
            raise ValueError("composition Growth result is invalid")
        if type(self.goal_progress_result) is not GoalProgressResultV1:
            raise ValueError("composition Goal Progress result is invalid")
        if type(self.caveats) is not tuple:
            raise ValueError("composition caveats are invalid")
        normalized_caveats = tuple(_parse_caveat(item) for item in self.caveats)
        if normalized_caveats != COMPOSITION_CAVEATS:
            raise ValueError("composition caveats are invalid")
        object.__setattr__(self, "caveats", normalized_caveats)
        if type(self.provenance) is not GrowthGoalProgressCompositionProvenanceV1:
            raise ValueError("composition provenance is invalid")
        _validate_branch_binding(
            self.growth_result,
            self.goal_progress_result,
            cast(UUID, self.selected_goal_source_uuid),
            self.current_goal_identity_fingerprint,
            cast(datetime, self.progress_as_of),
        )
        if (
            self.provenance.selected_goal_source_uuid != self.selected_goal_source_uuid
            or self.provenance.current_goal_identity_fingerprint
            != self.current_goal_identity_fingerprint
            or self.provenance.progress_as_of != self.progress_as_of
            or self.provenance.growth_policy_fingerprint != self.growth_policy_fingerprint
            or self.provenance.goal_progress_policy_fingerprint
            != self.goal_progress_policy_fingerprint
        ):
            raise ValueError("composition provenance binding is invalid")

    def as_dict(self) -> dict[str, object]:
        """Return deterministic side-by-side branch namespaces."""

        return {
            "contract_version": self.contract_version,
            "derivation_version": self.derivation_version,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
            "selected_goal_source_uuid": str(self.selected_goal_source_uuid),
            "current_goal_identity_fingerprint": self.current_goal_identity_fingerprint,
            "progress_as_of": _format_timestamp(cast(datetime, self.progress_as_of)),
            "growth_policy_fingerprint": self.growth_policy_fingerprint,
            "goal_progress_policy_fingerprint": self.goal_progress_policy_fingerprint,
            "growth_result": self.growth_result.as_dict(),
            "goal_progress_result": self.goal_progress_result.as_dict(),
            "caveats": [
                cast(GrowthGoalProgressCompositionCaveatV1, item).value for item in self.caveats
            ],
            "provenance": self.provenance.as_dict(),
        }

    def fingerprint_payload(self) -> dict[str, object]:
        """Return the semantic identity input without storage metadata."""

        return {
            "contract_version": self.contract_version,
            "derivation_version": self.derivation_version,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
            "selected_goal_source_uuid": str(self.selected_goal_source_uuid),
            "current_goal_identity_fingerprint": self.current_goal_identity_fingerprint,
            "progress_as_of": _format_timestamp(cast(datetime, self.progress_as_of)),
            "growth_policy_fingerprint": self.growth_policy_fingerprint,
            "goal_progress_policy_fingerprint": self.goal_progress_policy_fingerprint,
            "growth_result": self.growth_result.as_dict(),
            "goal_progress_result": self.goal_progress_result.as_dict(),
        }

    @property
    def semantic_fingerprint(self) -> CompositionHashV1:
        """Return the deterministic composition identity fingerprint."""

        return growth_goal_progress_composition_hash_json(self.fingerprint_payload())

    def to_json(self) -> str:
        """Serialize the complete bounded composition result."""

        return canonical_growth_goal_progress_composition_json(self.as_dict())


CompositionResultV1 = GrowthGoalProgressCompositionResultV1


@dataclass(frozen=True, slots=True)
class BuildGrowthGoalProgressCompositionV1:
    """Build one exact Goal composition without side effects."""

    reader: VaultReader
    store: GrowthMappingStore
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY
    growth_clock: CompositionClock = lambda: datetime.now(UTC)

    def execute(
        self,
        request: GrowthGoalProgressCompositionRequestV1,
    ) -> GrowthGoalProgressCompositionResultV1:
        """Validate, build, revalidate and return the complete composition."""

        validated_request = validate_growth_goal_progress_composition_request(request)
        try:
            validate_growth_goal_progress_composition_policy()
            validate_growth_policy()
            validate_goal_progress_policy(DEFAULT_GOAL_PROGRESS_POLICY)
            if self.growth_clock is None or not callable(self.growth_clock):
                raise ValueError
        except TypeError, ValueError, GrowthError, GoalProgressError:
            raise GrowthGoalProgressCompositionError(
                GrowthGoalProgressCompositionErrorCodeV1.POLICY_MISMATCH
            ) from None

        goal_uuid = cast(UUID, validated_request.goal_source_uuid)
        progress_as_of = cast(datetime, validated_request.progress_as_of)
        growth_request = GrowthEngineRequestV1(
            selection=GrowthGoalSelectionV1(
                GrowthGoalSelectionModeV1.SELECTED_GOAL,
                goal_uuid,
            )
        )
        growth_result = _build_growth_branch(
            self.reader,
            self.store,
            growth_request,
            policy=self.policy,
            clock=self.growth_clock,
        )
        growth_goal, growth_identity_fingerprint = _selected_growth_binding(
            growth_result,
            goal_uuid,
        )

        progress_result = _build_progress_branch(
            self.reader,
            GoalProgressRequestV1(goal_uuid, progress_as_of),
            policy=self.policy,
        )
        if progress_result.status is GoalProgressStatusV1.GOAL_SOURCE_CHANGED:
            raise GrowthGoalProgressCompositionError(
                GrowthGoalProgressCompositionErrorCodeV1.SOURCE_CHANGED
            )
        _validate_progress_binding(
            progress_result,
            goal_uuid,
            growth_identity_fingerprint,
            progress_as_of,
        )

        revalidated_goal = _revalidate_current_goal(
            self.reader,
            goal_uuid,
            policy=self.policy,
            clock=self.growth_clock,
        )
        revalidated_fingerprint = _goal_identity_fingerprint(revalidated_goal)
        if revalidated_fingerprint != growth_identity_fingerprint:
            raise GrowthGoalProgressCompositionError(
                GrowthGoalProgressCompositionErrorCodeV1.SOURCE_CHANGED
            )
        if progress_result.current_goal_identity_fingerprint != revalidated_fingerprint:
            raise GrowthGoalProgressCompositionError(
                GrowthGoalProgressCompositionErrorCodeV1.SOURCE_CHANGED
            )
        if growth_goal != revalidated_goal:
            raise GrowthGoalProgressCompositionError(
                GrowthGoalProgressCompositionErrorCodeV1.SOURCE_CHANGED
            )

        try:
            result = GrowthGoalProgressCompositionResultV1(
                contract_version=COMPOSITION_CONTRACT_VERSION,
                derivation_version=COMPOSITION_DERIVATION_VERSION,
                policy_id=COMPOSITION_POLICY_ID,
                policy_fingerprint=COMPOSITION_POLICY_FINGERPRINT,
                selected_goal_source_uuid=goal_uuid,
                current_goal_identity_fingerprint=revalidated_fingerprint,
                progress_as_of=progress_as_of,
                growth_policy_fingerprint=growth_result.policy_fingerprint,
                goal_progress_policy_fingerprint=(progress_result.goal_progress_policy_fingerprint),
                growth_result=growth_result,
                goal_progress_result=progress_result,
                caveats=COMPOSITION_CAVEATS,
                provenance=GrowthGoalProgressCompositionProvenanceV1(
                    source="current_vault_and_growth_mapping_store",
                    provider="none",
                    network="none",
                    write="none",
                    goal_binding="exact_current_identity",
                    temporal_alignment="not_proven",
                    selected_goal_source_uuid=goal_uuid,
                    current_goal_identity_fingerprint=revalidated_fingerprint,
                    progress_as_of=progress_as_of,
                    growth_policy_fingerprint=growth_result.policy_fingerprint,
                    goal_progress_policy_fingerprint=(
                        progress_result.goal_progress_policy_fingerprint
                    ),
                ),
            )
            validate_growth_goal_progress_composition_result(result)
        except GrowthGoalProgressCompositionError:
            raise
        except GrowthError, GoalProgressError, TypeError, ValueError, UnicodeError, OverflowError:
            raise GrowthGoalProgressCompositionError(
                GrowthGoalProgressCompositionErrorCodeV1.INTERNAL
            ) from None
        return result

    build = execute
    compose = execute


BuildGrowthGoalProgressComposition = BuildGrowthGoalProgressCompositionV1
GrowthGoalProgressCompositionBuilder = BuildGrowthGoalProgressCompositionV1


def build_growth_goal_progress_composition(
    reader: VaultReader,
    store: GrowthMappingStore,
    request: GrowthGoalProgressCompositionRequestV1,
    *,
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY,
    growth_clock: CompositionClock = lambda: datetime.now(UTC),
) -> GrowthGoalProgressCompositionResultV1:
    """Functional entry point for the private Stage 12D read model."""

    return BuildGrowthGoalProgressCompositionV1(
        reader=reader,
        store=store,
        policy=policy,
        growth_clock=growth_clock,
    ).execute(request)


build_growth_progress_composition = build_growth_goal_progress_composition


def validate_growth_goal_progress_composition_request(
    value: object,
) -> GrowthGoalProgressCompositionRequestV1:
    """Validate exact Goal UUID and exact UTC Progress cutoff before any scan."""

    if type(value) is not GrowthGoalProgressCompositionRequestV1:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.INVALID_REQUEST
        )
    request = value
    if request.goal_source_uuid is None or request.progress_as_of is None:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.INVALID_REQUEST
        )
    try:
        goal_uuid = _parse_uuid(request.goal_source_uuid)
        progress_as_of = _parse_exact_utc(request.progress_as_of)
    except TypeError, ValueError, OverflowError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.INVALID_REQUEST
        ) from None
    return GrowthGoalProgressCompositionRequestV1(goal_uuid, progress_as_of)


validate_composition_request = validate_growth_goal_progress_composition_request


def validate_growth_goal_progress_composition_result(
    value: object,
) -> GrowthGoalProgressCompositionResultV1:
    """Validate policy/binding and the complete fixed-size result."""

    if type(value) is not GrowthGoalProgressCompositionResultV1:
        raise GrowthGoalProgressCompositionError(GrowthGoalProgressCompositionErrorCodeV1.INTERNAL)
    result = value
    try:
        validate_growth_goal_progress_composition_policy()
        validate_growth_policy()
        validate_goal_progress_policy(DEFAULT_GOAL_PROGRESS_POLICY)
        validate_growth_engine_result(result.growth_result)
        validate_goal_progress_result(result.goal_progress_result)
        _validate_branch_binding(
            result.growth_result,
            result.goal_progress_result,
            cast(UUID, result.selected_goal_source_uuid),
            result.current_goal_identity_fingerprint,
            cast(datetime, result.progress_as_of),
        )
        if result.goal_progress_result.status is GoalProgressStatusV1.GOAL_SOURCE_CHANGED:
            raise GrowthGoalProgressCompositionError(
                GrowthGoalProgressCompositionErrorCodeV1.SOURCE_CHANGED
            )
        if len(result.to_json().encode("utf-8")) > MAX_COMPOSITION_RESULT_BYTES:
            raise GrowthGoalProgressCompositionError(
                GrowthGoalProgressCompositionErrorCodeV1.RESULT_TOO_LARGE
            )
    except GrowthGoalProgressCompositionError:
        raise
    except GrowthResultTooLargeError, GoalProgressResultTooLargeError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.RESULT_TOO_LARGE
        ) from None
    except GrowthPolicyMismatchError, GoalProgressPolicyMismatchError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.POLICY_MISMATCH
        ) from None
    except GrowthError, GoalProgressError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.INTERNAL
        ) from None
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.INTERNAL
        ) from None
    return result


validate_composition_result = validate_growth_goal_progress_composition_result


def canonical_growth_goal_progress_composition_json(value: object) -> str:
    """Encode one composition payload with the repository JSON profile."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except TypeError, ValueError, OverflowError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.INTERNAL
        ) from None


def growth_goal_progress_composition_hash_json(value: object) -> CompositionHashV1:
    """Hash a canonical composition payload without private storage fields."""

    encoded = canonical_growth_goal_progress_composition_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


composition_hash_json = growth_goal_progress_composition_hash_json


def validate_growth_goal_progress_composition_policy() -> CompositionHashV1:
    """Validate the fixed additive Stage 12D policy identity."""

    canonical = canonical_growth_goal_progress_composition_json(COMPOSITION_POLICY_PAYLOAD)
    if canonical != COMPOSITION_POLICY_CANONICAL_JSON:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.POLICY_MISMATCH
        )
    fingerprint = growth_goal_progress_composition_hash_json(COMPOSITION_POLICY_PAYLOAD)
    if fingerprint != COMPOSITION_POLICY_FINGERPRINT:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.POLICY_MISMATCH
        )
    return fingerprint


validate_composition_policy = validate_growth_goal_progress_composition_policy


def _build_growth_branch(
    reader: VaultReader,
    store: GrowthMappingStore,
    request: GrowthEngineRequestV1,
    *,
    policy: SelfModelPolicy,
    clock: CompositionClock,
) -> GrowthEngineResultV1:
    try:
        result = BuildGrowthEngine(
            reader=reader,
            store=store,
            policy=policy,
            clock=clock,
        ).execute(request)
        validate_growth_engine_result(result)
        return result
    except GrowthGoalProgressCompositionError:
        raise
    except GrowthInvalidRequestError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.INVALID_REQUEST
        ) from None
    except GrowthPolicyMismatchError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.POLICY_MISMATCH
        ) from None
    except GrowthResultTooLargeError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.RESULT_TOO_LARGE
        ) from None
    except GrowthGoalMissingError, GrowthGoalSourceChangedError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.SOURCE_CHANGED
        ) from None
    except GrowthError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.SOURCE_UNAVAILABLE
        ) from None
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.INTERNAL
        ) from None
    except Exception:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.INTERNAL
        ) from None


def _build_progress_branch(
    reader: VaultReader,
    request: GoalProgressRequestV1,
    *,
    policy: SelfModelPolicy,
) -> GoalProgressResultV1:
    try:
        result = BuildGoalProgress(reader, policy=policy).execute(request)
        validate_goal_progress_result(result)
        return result
    except GrowthGoalProgressCompositionError:
        raise
    except GoalProgressInvalidRequestError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.INVALID_REQUEST
        ) from None
    except GoalProgressPolicyMismatchError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.POLICY_MISMATCH
        ) from None
    except GoalProgressResultTooLargeError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.RESULT_TOO_LARGE
        ) from None
    except GoalProgressGoalSourceChangedError, GoalProgressGoalAmbiguousError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.SOURCE_CHANGED
        ) from None
    except GoalProgressSourceUnavailableError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.SOURCE_UNAVAILABLE
        ) from None
    except GoalProgressError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.SOURCE_UNAVAILABLE
        ) from None
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.INTERNAL
        ) from None
    except Exception:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.INTERNAL
        ) from None


def _selected_growth_binding(
    result: GrowthEngineResultV1,
    goal_uuid: UUID,
) -> tuple[GrowthGoalIdentityV1, CompositionHashV1]:
    try:
        if (
            result.selection_mode is not GrowthGoalSelectionModeV1.SELECTED_GOAL
            or result.selected_goal_source_uuid != goal_uuid
            or not result.goal_results
        ):
            raise ValueError
        goals = tuple(item.goal for item in result.goal_results)
        if any(goal is None for goal in goals):
            raise ValueError
        typed_goals = cast(tuple[GrowthGoalIdentityV1, ...], goals)
        if any(goal.source_note_uuid != goal_uuid for goal in typed_goals):
            raise ValueError
        fingerprints = tuple(_goal_identity_fingerprint(goal) for goal in typed_goals)
        if len(set(fingerprints)) != 1:
            raise ValueError
        return typed_goals[0], fingerprints[0]
    except GrowthGoalProgressCompositionError:
        raise
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.GOAL_BINDING_MISMATCH
        ) from None


def _validate_progress_binding(
    result: GoalProgressResultV1,
    goal_uuid: UUID,
    growth_identity_fingerprint: str,
    progress_as_of: datetime,
) -> None:
    if (
        result.selected_goal_source_uuid != goal_uuid
        or result.current_goal_identity_fingerprint != growth_identity_fingerprint
        or result.as_of != progress_as_of
    ):
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.GOAL_BINDING_MISMATCH
        )


def _revalidate_current_goal(
    reader: VaultReader,
    goal_uuid: UUID,
    *,
    policy: SelfModelPolicy,
    clock: CompositionClock,
) -> GrowthGoalIdentityV1:
    try:
        context = BuildGrowthGoalContext(
            reader=reader,
            policy=policy,
            clock=clock,
        ).execute(
            GrowthEngineRequestV1(
                selection=GrowthGoalSelectionV1(
                    GrowthGoalSelectionModeV1.SELECTED_GOAL,
                    goal_uuid,
                )
            )
        )
        if len(context.goals) != 1 or context.goals[0].source_note_uuid != goal_uuid:
            raise ValueError
        return context.goals[0]
    except GrowthGoalProgressCompositionError:
        raise
    except GrowthGoalMissingError, GrowthGoalSourceChangedError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.SOURCE_CHANGED
        ) from None
    except GrowthPolicyMismatchError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.POLICY_MISMATCH
        ) from None
    except GrowthError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.SOURCE_UNAVAILABLE
        ) from None
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.SOURCE_CHANGED
        ) from None
    except Exception:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.SOURCE_UNAVAILABLE
        ) from None


def _validate_branch_binding(
    growth_result: GrowthEngineResultV1,
    progress_result: GoalProgressResultV1,
    goal_uuid: UUID,
    identity_fingerprint: str,
    progress_as_of: datetime,
) -> None:
    if (
        growth_result.policy_fingerprint != GROWTH_POLICY_FINGERPRINT
        or progress_result.goal_progress_policy_fingerprint != GOAL_PROGRESS_POLICY_FINGERPRINT
        or progress_result.status is GoalProgressStatusV1.GOAL_SOURCE_CHANGED
    ):
        raise ValueError("composition branch policy/binding is invalid")
    _growth_goal, growth_fingerprint = _selected_growth_binding(growth_result, goal_uuid)
    _validate_progress_binding(progress_result, goal_uuid, identity_fingerprint, progress_as_of)
    if growth_fingerprint != identity_fingerprint:
        raise ValueError("composition Goal identity is invalid")


def _goal_identity_fingerprint(goal: GrowthGoalIdentityV1) -> CompositionHashV1:
    try:
        return growth_hash_json(goal.as_dict())
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise GrowthGoalProgressCompositionError(
            GrowthGoalProgressCompositionErrorCodeV1.GOAL_BINDING_MISMATCH
        ) from None


def _normalize_error_code(
    code: GrowthGoalProgressCompositionErrorCodeV1 | str,
) -> GrowthGoalProgressCompositionErrorCodeV1:
    if isinstance(code, GrowthGoalProgressCompositionErrorCodeV1):
        return code
    try:
        return GrowthGoalProgressCompositionErrorCodeV1(code)
    except TypeError, ValueError:
        return GrowthGoalProgressCompositionErrorCodeV1.INTERNAL


def _parse_uuid(value: object) -> UUID:
    try:
        return parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        raise ValueError("composition UUID is invalid") from None


def _parse_hash(value: object) -> str:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise ValueError("composition hash is invalid")
    return value


def _parse_exact_utc(value: object) -> datetime:
    if type(value) is str:
        try:
            parsed = parse_rfc3339(value)
        except TypeError, ValueError, OverflowError:
            raise ValueError("composition timestamp is invalid") from None
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise ValueError("composition timestamp is invalid")
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset() != UTC.utcoffset(parsed)
    ):
        raise ValueError("composition timestamp is invalid")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    normalized = _parse_exact_utc(value)
    rendered = normalized.isoformat(
        timespec="microseconds" if normalized.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def _timestamp_projection(value: object) -> str | None:
    if value is None:
        return None
    try:
        return _format_timestamp(_parse_exact_utc(value))
    except TypeError, ValueError, OverflowError:
        return None


def _parse_caveat(value: object) -> GrowthGoalProgressCompositionCaveatV1:
    if isinstance(value, GrowthGoalProgressCompositionCaveatV1):
        return value
    if type(value) is not str:
        raise ValueError("composition caveat is invalid")
    try:
        return GrowthGoalProgressCompositionCaveatV1(value)
    except ValueError:
        raise ValueError("composition caveat is invalid") from None


__all__ = [
    "COMPOSITION_CAVEATS",
    "COMPOSITION_CONTRACT_VERSION",
    "COMPOSITION_DERIVATION_VERSION",
    "COMPOSITION_POLICY_CANONICAL_JSON",
    "COMPOSITION_POLICY_FINGERPRINT",
    "COMPOSITION_POLICY_ID",
    "COMPOSITION_POLICY_PAYLOAD",
    "COMPOSITION_STRUCTURAL_OVERHEAD_BYTES",
    "MAX_COMPOSITION_RESULT_BYTES",
    "MAX_GROWTH_GOAL_PROGRESS_COMPOSITION_RESULT_BYTES",
    "BuildGrowthGoalProgressComposition",
    "BuildGrowthGoalProgressCompositionV1",
    "CompositionCaveatV1",
    "CompositionErrorCodeV1",
    "CompositionProvenanceV1",
    "CompositionRequestV1",
    "CompositionResultV1",
    "GrowthGoalProgressCompositionBuilder",
    "GrowthGoalProgressCompositionCaveatV1",
    "GrowthGoalProgressCompositionError",
    "GrowthGoalProgressCompositionErrorCode",
    "GrowthGoalProgressCompositionErrorCodeV1",
    "GrowthGoalProgressCompositionProvenanceV1",
    "GrowthGoalProgressCompositionRequestV1",
    "GrowthGoalProgressCompositionResultV1",
    "build_growth_goal_progress_composition",
    "build_growth_progress_composition",
    "canonical_growth_goal_progress_composition_json",
    "composition_hash_json",
    "growth_goal_progress_composition_hash_json",
    "validate_composition_policy",
    "validate_composition_request",
    "validate_composition_result",
    "validate_growth_goal_progress_composition_policy",
    "validate_growth_goal_progress_composition_request",
    "validate_growth_goal_progress_composition_result",
]
