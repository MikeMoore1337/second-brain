"""Lazy local Web projection of the provider-free Simulate Me v1 core."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, StrictStr

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.simulate_me import (
    BuildSimulateMe,
    SimulateMeContextualEvidenceRef,
    SimulateMeDimension,
    SimulateMeError,
    SimulateMeEvidenceRef,
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResult,
    SimulateMeTemporalCaveat,
    validate_simulate_me_request,
    validate_simulate_me_result,
)
from second_brain.config import load_config


class SimulateMeService(Protocol):
    """Minimal injectable seam for one current Simulate Me build."""

    def build(self, request: SimulateMeRequest) -> SimulateMeResult:
        """Build one result through the provider-free application core."""


@dataclass(frozen=True, slots=True)
class LazyVaultSimulateMeService:
    """Resolve configuration and read the vault only for an explicit request."""

    env_file: Path | None = None
    vault_path_override: str | None = None

    def build(self, request: SimulateMeRequest) -> SimulateMeResult:
        """Build one disposable result without persistence, cache, or providers."""

        validate_simulate_me_request(request)
        config = load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )
        reader = FileSystemVaultReader(config.vault_path)
        return BuildSimulateMe(reader).execute(request)


def build_production_simulate_me_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> LazyVaultSimulateMeService:
    """Create a lazy service without config or vault side effects."""

    return LazyVaultSimulateMeService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


class SimulateMeOptionPayload(BaseModel):
    """Exact caller-owned option projection with no server-created identity."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: StrictStr
    label: StrictStr


class SimulateMeRequestPayload(BaseModel):
    """Strict HTTP request containing only the approved core fields."""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: StrictStr
    options: list[SimulateMeOptionPayload]


class SimulateMeEvidenceRefPayload(BaseModel):
    """Safe evidence projection without claim text, path, or note body."""

    model_config = ConfigDict(extra="forbid", strict=True)

    claim_id: StrictStr
    dimension: Literal["preference", "goal"]
    note_ids: list[StrictStr]
    evidence_at: datetime | Literal["unknown"]


class SimulateMeContextualEvidenceRefPayload(BaseModel):
    """Belief context projection emitted only when the core supplies it."""

    model_config = ConfigDict(extra="forbid", strict=True)

    claim_id: StrictStr
    dimension: Literal["belief"]
    note_ids: list[StrictStr]
    evidence_at: datetime | Literal["unknown"]


class SimulateMeTemporalCaveatPayload(BaseModel):
    """Explicit unknown-time caveat from the core result."""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: Literal["evidence_at_unknown"]
    claim_id: StrictStr


class SimulateMeResponse(BaseModel):
    """Exact read-only projection with no score or recommendation fields."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["prediction", "abstention"]
    selected_option: SimulateMeOptionPayload | None
    evidence_refs: list[SimulateMeEvidenceRefPayload]
    contextual_evidence_refs: list[SimulateMeContextualEvidenceRefPayload]
    temporal_caveats: list[SimulateMeTemporalCaveatPayload]
    abstention_code: (
        Literal[
            "no_matching_evidence",
            "multiple_options_supported",
            "insufficient_or_invalid_current_context",
        ]
        | None
    )
    derivation_version: StrictStr
    policy_id: StrictStr
    policy_fingerprint: StrictStr


def simulate_me_request(payload: SimulateMeRequestPayload) -> SimulateMeRequest:
    """Convert the strict Web DTO to the immutable application request."""

    request = SimulateMeRequest(
        query=payload.query,
        options=tuple(
            SimulateMeOption(id=option.id, label=option.label) for option in payload.options
        ),
    )
    return validate_simulate_me_request(request)


def simulate_me_response(
    result: SimulateMeResult,
    request: SimulateMeRequest,
) -> SimulateMeResponse:
    """Validate and serialize one exact core result without adding Web semantics."""

    validated = validate_simulate_me_result(result, request=request)
    kind = _enum_value(validated.kind)
    if kind not in {"prediction", "abstention"}:
        raise SimulateMeError("SIMULATE_ME_RESULT_INVALID")
    abstention_code = (
        None if validated.abstention_code is None else _enum_value(validated.abstention_code)
    )
    if abstention_code not in {
        None,
        "no_matching_evidence",
        "multiple_options_supported",
        "insufficient_or_invalid_current_context",
    }:
        raise SimulateMeError("SIMULATE_ME_RESULT_INVALID")
    return SimulateMeResponse(
        kind=cast(Literal["prediction", "abstention"], kind),
        selected_option=(
            None
            if validated.selected_option is None
            else _option_payload(validated.selected_option)
        ),
        evidence_refs=[_evidence_payload(ref) for ref in validated.evidence_refs],
        contextual_evidence_refs=[
            _contextual_evidence_payload(ref) for ref in validated.contextual_evidence_refs
        ],
        temporal_caveats=[_caveat_payload(caveat) for caveat in validated.temporal_caveats],
        abstention_code=cast(
            Literal[
                "no_matching_evidence",
                "multiple_options_supported",
                "insufficient_or_invalid_current_context",
            ]
            | None,
            abstention_code,
        ),
        derivation_version=validated.derivation_version,
        policy_id=validated.policy_id,
        policy_fingerprint=validated.policy_fingerprint,
    )


def _option_payload(option: SimulateMeOption) -> SimulateMeOptionPayload:
    """Preserve the exact caller-owned pair from the core result."""

    return SimulateMeOptionPayload(id=option.id, label=option.label)


def _evidence_payload(ref: SimulateMeEvidenceRef) -> SimulateMeEvidenceRefPayload:
    """Project only canonical UUID references and evidence time."""

    dimension = _enum_value(ref.dimension)
    if dimension not in {"preference", "goal"}:
        raise SimulateMeError("SIMULATE_ME_RESULT_INVALID")
    evidence_at: datetime | Literal["unknown"]
    if ref.evidence_at == "unknown":
        evidence_at = "unknown"
    elif isinstance(ref.evidence_at, datetime):
        evidence_at = ref.evidence_at
    else:
        raise SimulateMeError("SIMULATE_ME_RESULT_INVALID")
    return SimulateMeEvidenceRefPayload(
        claim_id=str(ref.claim_id),
        dimension=cast(Literal["preference", "goal"], dimension),
        note_ids=[str(note_id) for note_id in ref.note_ids],
        evidence_at=evidence_at,
    )


def _contextual_evidence_payload(
    ref: SimulateMeContextualEvidenceRef,
) -> SimulateMeContextualEvidenceRefPayload:
    """Project belief context without turning it into selection evidence."""

    if ref.dimension is not SimulateMeDimension.BELIEF:
        raise SimulateMeError("SIMULATE_ME_RESULT_INVALID")
    evidence_at: datetime | Literal["unknown"]
    if ref.evidence_at == "unknown":
        evidence_at = "unknown"
    elif isinstance(ref.evidence_at, datetime):
        evidence_at = ref.evidence_at
    else:
        raise SimulateMeError("SIMULATE_ME_RESULT_INVALID")
    return SimulateMeContextualEvidenceRefPayload(
        claim_id=str(ref.claim_id),
        dimension="belief",
        note_ids=[str(note_id) for note_id in ref.note_ids],
        evidence_at=evidence_at,
    )


def _caveat_payload(caveat: SimulateMeTemporalCaveat) -> SimulateMeTemporalCaveatPayload:
    """Project only the approved unknown-time caveat."""

    if _enum_value(caveat.code) != "evidence_at_unknown":
        raise SimulateMeError("SIMULATE_ME_RESULT_INVALID")
    return SimulateMeTemporalCaveatPayload(
        code="evidence_at_unknown",
        claim_id=str(caveat.claim_id),
    )


def _enum_value(value: object) -> str:
    """Read an application enum without leaking arbitrary object data."""

    raw = getattr(value, "value", None)
    if type(raw) is not str:
        raise SimulateMeError("SIMULATE_ME_RESULT_INVALID")
    return raw


__all__ = [
    "LazyVaultSimulateMeService",
    "SimulateMeContextualEvidenceRefPayload",
    "SimulateMeEvidenceRefPayload",
    "SimulateMeOptionPayload",
    "SimulateMeRequestPayload",
    "SimulateMeResponse",
    "SimulateMeService",
    "SimulateMeTemporalCaveatPayload",
    "build_production_simulate_me_service",
    "simulate_me_request",
    "simulate_me_response",
]
