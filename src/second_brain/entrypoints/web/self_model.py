"""Lazy local Web projection of the application-owned Self Model result."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_MAX_CLAIMS,
    DEFAULT_SELF_MODEL_POLICY,
    BuildSelfModel,
    SelfModelClaim,
    SelfModelConfidence,
    SelfModelError,
    SelfModelEvidenceRef,
    SelfModelRequest,
    SelfModelResult,
    SelfModelStatus,
    SelfModelTemporalContext,
    validate_self_model_policy,
    validate_self_model_request,
    validate_self_model_result,
)
from second_brain.config import load_config


class SelfModelService(Protocol):
    """Minimal injectable seam for one current Self Model rebuild."""

    def build(self, request: SelfModelRequest) -> SelfModelResult:
        """Build the current Self Model through the application core."""


@dataclass(frozen=True, slots=True)
class LazyVaultSelfModelService:
    """Resolve configuration and scan the vault only for an explicit request."""

    env_file: Path | None = None
    vault_path_override: str | None = None

    def build(self, request: SelfModelRequest) -> SelfModelResult:
        """Rebuild the current result without persistence, cache, Search, or LLM."""

        config = load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )
        reader = FileSystemVaultReader(config.vault_path)
        return BuildSelfModel(
            reader,
            policy=DEFAULT_SELF_MODEL_POLICY,
            clock=lambda: datetime.now(UTC),
        ).execute(request)


def build_production_self_model_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> LazyVaultSelfModelService:
    """Create a lazy Self Model service without config or vault side effects."""

    return LazyVaultSelfModelService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


class SelfModelRequestPayload(BaseModel):
    """Strict JSON projection of the existing application Self Model request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    max_claims: StrictInt = DEFAULT_SELF_MODEL_MAX_CLAIMS
    max_evidence_refs_per_claim: StrictInt = DEFAULT_SELF_MODEL_MAX_CLAIMS


class SelfModelEvidenceRefPayload(BaseModel):
    """Safe UUID/evidence-time projection without canonical body or path."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: StrictStr
    evidence_kind: StrictStr
    self_kind: StrictStr
    domain: StrictStr | None
    evidence_at: datetime | Literal["unknown"]
    evidence_at_precision: Literal["exact", "unknown"]
    related_note_ids: list[StrictStr]


class SelfModelTemporalContextPayload(BaseModel):
    """Descriptive evidence-time aggregate preserving unknown time explicitly."""

    model_config = ConfigDict(extra="forbid", strict=True)

    earliest_known_evidence_at: datetime | None
    latest_known_evidence_at: datetime | None
    known_evidence_count: StrictInt
    unknown_evidence_count: StrictInt


class SelfModelConfidencePayload(BaseModel):
    """Unassessed v1 confidence envelope; no model score is exposed."""

    model_config = ConfigDict(extra="forbid", strict=True)

    state: Literal["not_assessed"]
    score: None = None
    policy_version: StrictStr
    supporting_evidence_count: StrictInt
    contradicting_evidence_count: StrictInt
    unknown_time_count: StrictInt


class SelfModelStatusPayload(BaseModel):
    """Future status seam retained only for the application DTO shape."""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: StrictStr
    policy_version: StrictStr


class SelfModelClaimPayload(BaseModel):
    """Safe projection of one derived claim and its explainability envelope."""

    model_config = ConfigDict(extra="forbid", strict=True)

    dimension: Literal["preference", "belief", "goal"]
    claim: StrictStr
    domain: StrictStr | None
    supporting_evidence: list[SelfModelEvidenceRefPayload]
    contradicting_evidence: list[SelfModelEvidenceRefPayload]
    contextual_evidence: list[SelfModelEvidenceRefPayload]
    confidence: SelfModelConfidencePayload
    temporal_context: SelfModelTemporalContextPayload
    generated_at: datetime
    derivation_version: StrictStr
    status: SelfModelStatusPayload | None = None


class SelfModelResponse(BaseModel):
    """Bounded read-only response containing only existing core DTO fields."""

    model_config = ConfigDict(extra="forbid", strict=True)

    claims: list[SelfModelClaimPayload]
    eligible_evidence_count: StrictInt
    represented_evidence_count: StrictInt
    generated_at: datetime
    derivation_version: StrictStr
    policy_fingerprint: StrictStr


def self_model_response(
    result: SelfModelResult,
    request: SelfModelRequest,
) -> SelfModelResponse:
    """Validate and serialize one core result without adding Web semantics."""

    validate_self_model_request(request)
    policy_fingerprint = validate_self_model_policy(DEFAULT_SELF_MODEL_POLICY)
    validated = validate_self_model_result(
        result,
        request=request,
        policy=DEFAULT_SELF_MODEL_POLICY,
        expected_policy_fingerprint=policy_fingerprint,
    )
    return SelfModelResponse(
        claims=[_claim_payload(claim) for claim in validated.claims],
        eligible_evidence_count=validated.eligible_evidence_count,
        represented_evidence_count=validated.represented_evidence_count,
        generated_at=validated.generated_at,
        derivation_version=validated.derivation_version,
        policy_fingerprint=validated.policy_fingerprint,
    )


def _claim_payload(claim: SelfModelClaim) -> SelfModelClaimPayload:
    """Project a validated claim without body duplication or storage identity."""

    dimension = _enum_value(claim.dimension)
    if dimension not in {"preference", "belief", "goal"}:
        raise SelfModelError("SELF_MODEL_RESULT_INVALID")
    dimension_value = cast(Literal["preference", "belief", "goal"], dimension)
    return SelfModelClaimPayload(
        dimension=dimension_value,
        claim=claim.claim,
        domain=claim.domain,
        supporting_evidence=[_evidence_payload(ref) for ref in claim.supporting_evidence],
        contradicting_evidence=[_evidence_payload(ref) for ref in claim.contradicting_evidence],
        contextual_evidence=[_evidence_payload(ref) for ref in claim.contextual_evidence],
        confidence=_confidence_payload(claim.confidence),
        temporal_context=_temporal_payload(claim.temporal_context),
        generated_at=claim.generated_at,
        derivation_version=claim.derivation_version,
        status=_status_payload(claim.status),
    )


def _evidence_payload(ref: SelfModelEvidenceRef) -> SelfModelEvidenceRefPayload:
    """Project only canonical UUID evidence and approved metadata."""

    evidence_at: datetime | Literal["unknown"]
    if ref.evidence_at == "unknown":
        evidence_at = "unknown"
    elif isinstance(ref.evidence_at, datetime):
        evidence_at = ref.evidence_at
    else:
        raise SelfModelError("SELF_MODEL_RESULT_INVALID")

    precision = _enum_value(ref.evidence_at_precision)
    if precision not in {"exact", "unknown"}:
        raise SelfModelError("SELF_MODEL_RESULT_INVALID")
    precision_value = cast(Literal["exact", "unknown"], precision)
    return SelfModelEvidenceRefPayload(
        id=str(ref.note_id),
        evidence_kind=_enum_value(ref.evidence_kind),
        self_kind=_enum_value(ref.self_kind),
        domain=ref.domain,
        evidence_at=evidence_at,
        evidence_at_precision=precision_value,
        related_note_ids=[str(note_id) for note_id in ref.related_note_ids],
    )


def _confidence_payload(confidence: SelfModelConfidence) -> SelfModelConfidencePayload:
    """Project the core unassessed confidence envelope exactly."""

    state = _enum_value(confidence.state)
    if state != "not_assessed" or confidence.score is not None:
        raise SelfModelError("SELF_MODEL_RESULT_INVALID")
    return SelfModelConfidencePayload(
        state="not_assessed",
        score=None,
        policy_version=confidence.policy_version,
        supporting_evidence_count=confidence.supporting_evidence_count,
        contradicting_evidence_count=confidence.contradicting_evidence_count,
        unknown_time_count=confidence.unknown_time_count,
    )


def _status_payload(status: SelfModelStatus | None) -> SelfModelStatusPayload | None:
    """Keep the future DTO seam safe; v1 core always supplies ``None``."""

    if status is None:
        return None
    return SelfModelStatusPayload(
        code=status.code,
        policy_version=status.policy_version,
    )


def _temporal_payload(context: SelfModelTemporalContext) -> SelfModelTemporalContextPayload:
    """Project only evidence-time aggregate fields from the core."""

    return SelfModelTemporalContextPayload(
        earliest_known_evidence_at=context.earliest_known_evidence_at,
        latest_known_evidence_at=context.latest_known_evidence_at,
        known_evidence_count=context.known_evidence_count,
        unknown_evidence_count=context.unknown_evidence_count,
    )


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", None)
    if type(raw) is not str:
        raise SelfModelError("SELF_MODEL_RESULT_INVALID")
    return raw


__all__ = [
    "LazyVaultSelfModelService",
    "SelfModelClaimPayload",
    "SelfModelConfidencePayload",
    "SelfModelEvidenceRefPayload",
    "SelfModelRequestPayload",
    "SelfModelResponse",
    "SelfModelService",
    "SelfModelStatusPayload",
    "SelfModelTemporalContextPayload",
    "build_production_self_model_service",
    "self_model_response",
]
