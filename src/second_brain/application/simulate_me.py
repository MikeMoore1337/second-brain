"""Provider-free, exact-label Simulate Me v1 application use case.

The use case consumes only a server-owned, freshly validated Self Model
result.  Caller options stay request-local and the result is an ephemeral
prediction or abstention; no persistence, ranking, network, or write path is
available from this module.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Literal, Protocol
from uuid import UUID

from second_brain.application.ports import VaultReader
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    BuildSelfModel,
    SelfModelClaim,
    SelfModelDimension,
    SelfModelRequest,
    SelfModelResult,
    validate_self_model_policy,
    validate_self_model_result,
)
from second_brain.domain.models import EvidenceAt

DERIVATION_VERSION: Final[str] = "simulate-me-v1"
POLICY_ID: Final[str] = "simulate-me-direct-exact-v1"
MAX_OPTIONS: Final[int] = 8
MIN_OPTIONS: Final[int] = 1
MAX_QUERY_BYTES: Final[int] = 4096
MIN_TEXT_BYTES: Final[int] = 1
MAX_LABEL_BYTES: Final[int] = 256
MAX_RESULT_REFS: Final[int] = 20
MAX_NOTE_IDS_PER_REF: Final[int] = 20

POLICY_CANONICAL_JSON: Final[str] = (
    '{"abstention_codes":["no_matching_evidence","multiple_options_supported",'
    '"insufficient_or_invalid_current_context"],"eligible_dimensions":["preference",'
    '"goal"],"evidence_at_unknown":"temporal_caveat","matching":"exact-whole-label-v1",'
    '"non_eligible_dimensions":["belief","decision_rule","behavioral_pattern"],'
    '"policy_id":"simulate-me-direct-exact-v1","recency":"disabled",'
    '"selection":"one-distinct-option-or-abstain-v1","version":"1"}'
)
POLICY_FINGERPRINT: Final[str] = (
    "sha256:07aa1d0d57fdd2d009087c05423fc4eb9304da70e87f32b1790fbd4753f21c3a"
)
_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z", re.ASCII)
_FINGERPRINT_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z")


class SimulateMeResultKind(StrEnum):
    """The only two result shapes in the approved contract."""

    PREDICTION = "prediction"
    ABSTENTION = "abstention"


class SimulateMeAbstentionCode(StrEnum):
    """Closed abstention vocabulary; no confidence or recommendation state."""

    NO_MATCHING_EVIDENCE = "no_matching_evidence"
    MULTIPLE_OPTIONS_SUPPORTED = "multiple_options_supported"
    INSUFFICIENT_OR_INVALID_CURRENT_CONTEXT = "insufficient_or_invalid_current_context"


class SimulateMeDimension(StrEnum):
    """Dimensions exposed by the result DTO."""

    PREFERENCE = "preference"
    GOAL = "goal"
    BELIEF = "belief"


class SimulateMeTemporalCaveatCode(StrEnum):
    """The only temporal caveat emitted by v1."""

    EVIDENCE_AT_UNKNOWN = "evidence_at_unknown"


class SimulateMeErrorCode(StrEnum):
    """Safe request/result error taxonomy for the application boundary."""

    INVALID_REQUEST = "SIMULATE_ME_INVALID_REQUEST"
    RESULT_INVALID = "SIMULATE_ME_RESULT_INVALID"


@dataclass(frozen=True, slots=True)
class SimulateMeOption:
    """Caller-owned request-local option identity and display label."""

    id: str
    label: str


@dataclass(frozen=True, slots=True)
class SimulateMeRequest:
    """Bounded literal task and caller-owned options."""

    query: str
    options: tuple[SimulateMeOption, ...]


@dataclass(frozen=True, slots=True)
class SimulateMeEvidenceRef:
    """Current canonical evidence supporting one or more matched options."""

    claim_id: UUID
    dimension: SimulateMeDimension
    note_ids: tuple[UUID, ...]
    evidence_at: EvidenceAt


@dataclass(frozen=True, slots=True)
class SimulateMeContextualEvidenceRef:
    """Belief evidence shown for context but never used for selection."""

    claim_id: UUID
    dimension: Literal[SimulateMeDimension.BELIEF]
    note_ids: tuple[UUID, ...]
    evidence_at: EvidenceAt


@dataclass(frozen=True, slots=True)
class SimulateMeTemporalCaveat:
    """Explicit caveat for a current evidence ref whose time is unknown."""

    code: SimulateMeTemporalCaveatCode
    claim_id: UUID


@dataclass(frozen=True, slots=True)
class SimulateMeResult:
    """Exact prediction/abstention DTO with no score or recommendation fields."""

    kind: SimulateMeResultKind
    selected_option: SimulateMeOption | None
    evidence_refs: tuple[SimulateMeEvidenceRef, ...]
    contextual_evidence_refs: tuple[SimulateMeContextualEvidenceRef, ...]
    temporal_caveats: tuple[SimulateMeTemporalCaveat, ...]
    abstention_code: SimulateMeAbstentionCode | None
    derivation_version: str
    policy_id: str
    policy_fingerprint: str


class SimulateMeError(RuntimeError):
    """Safe application error without query, note text, or backend details."""

    def __init__(self, code: SimulateMeErrorCode | str) -> None:
        normalized = _normalize_error_code(code)
        self.code = normalized.value
        self.message = {
            SimulateMeErrorCode.INVALID_REQUEST: "simulate me request failed validation",
            SimulateMeErrorCode.RESULT_INVALID: "simulate me result failed validation",
        }[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Return the bounded public error projection."""

        return {"code": self.code, "message": self.message}


class SimulateMeInvalidRequestError(SimulateMeError):
    """The caller request is outside the exact v1 bounds."""

    def __init__(self) -> None:
        super().__init__(SimulateMeErrorCode.INVALID_REQUEST)


class SimulateMeResultInvalidError(SimulateMeError):
    """A result DTO does not satisfy the exact v1 invariants."""

    def __init__(self) -> None:
        super().__init__(SimulateMeErrorCode.RESULT_INVALID)


class CurrentSelfModelBuilder(Protocol):
    """Server-owned current Self Model boundary used by the use case."""

    def execute(self, request: SelfModelRequest) -> SelfModelResult:
        """Build a fresh current result without accepting caller evidence."""


class _ContextProjectionError(ValueError):
    """A valid Self Model cannot be represented in the bounded Stage 6 DTO."""


class BuildSimulateMe:
    """Build one deterministic prediction or abstention from current evidence."""

    def __init__(
        self,
        reader: VaultReader | None = None,
        *,
        self_model: CurrentSelfModelBuilder | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create the default current-vault composition or an injected seam."""

        builder: CurrentSelfModelBuilder
        if self_model is None:
            if reader is None:
                raise ValueError("reader is required for the default current context")
            builder = BuildSelfModel(
                reader,
                policy=DEFAULT_SELF_MODEL_POLICY,
                clock=clock or _utc_now,
            )
        else:
            builder = self_model
        self._self_model = builder

    def execute(self, request: SimulateMeRequest) -> SimulateMeResult:
        """Validate the request, reread current context, and apply exact matching."""

        validated_request = validate_simulate_me_request(request)
        context = self._read_current_context()
        if context is None:
            return _validated_abstention(
                validated_request,
                SimulateMeAbstentionCode.INSUFFICIENT_OR_INVALID_CURRENT_CONTEXT,
            )

        try:
            normalized_options = tuple(
                (option, normalize_simulate_me_text(option.label, MAX_LABEL_BYTES))
                for option in validated_request.options
            )
            support_by_option: dict[str, list[SimulateMeEvidenceRef]] = {
                option.id: [] for option in validated_request.options
            }
            contextual_refs: list[SimulateMeContextualEvidenceRef] = []
            for claim in context.claims:
                matched_options = tuple(
                    option
                    for option, normalized_label in normalized_options
                    if normalize_simulate_me_text(claim.claim, MAX_LABEL_BYTES) == normalized_label
                )
                if not matched_options:
                    continue
                if claim.dimension is SelfModelDimension.BELIEF:
                    contextual_refs.append(_contextual_ref(claim))
                    continue
                if claim.dimension not in {
                    SelfModelDimension.PREFERENCE,
                    SelfModelDimension.GOAL,
                }:
                    continue
                evidence_ref = _evidence_ref(claim)
                for option in matched_options:
                    support_by_option[option.id].append(evidence_ref)

            contextual = _unique_contextual_refs(contextual_refs)
            supported_ids = tuple(
                option.id for option in validated_request.options if support_by_option[option.id]
            )
            if len(supported_ids) == 1:
                selected_id = supported_ids[0]
                evidence = _unique_evidence_refs(support_by_option[selected_id])
                return _build_result(
                    request=validated_request,
                    kind=SimulateMeResultKind.PREDICTION,
                    selected_option=_option_by_id(validated_request, selected_id),
                    evidence_refs=evidence,
                    contextual_evidence_refs=contextual,
                    abstention_code=None,
                )
            evidence = _unique_evidence_refs(
                ref for option_id in supported_ids for ref in support_by_option[option_id]
            )
            abstention = (
                SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE
                if not supported_ids
                else SimulateMeAbstentionCode.MULTIPLE_OPTIONS_SUPPORTED
            )
            return _build_result(
                request=validated_request,
                kind=SimulateMeResultKind.ABSTENTION,
                selected_option=None,
                evidence_refs=evidence,
                contextual_evidence_refs=contextual,
                abstention_code=abstention,
            )
        except SimulateMeError, UnicodeError, _ContextProjectionError, ValueError:
            return _validated_abstention(
                validated_request,
                SimulateMeAbstentionCode.INSUFFICIENT_OR_INVALID_CURRENT_CONTEXT,
            )

    def _read_current_context(self) -> SelfModelResult | None:
        """Validate every server-owned result before using any claim text."""

        try:
            request = SelfModelRequest()
            fingerprint = validate_self_model_policy(DEFAULT_SELF_MODEL_POLICY)
            result = self._self_model.execute(request)
            return validate_self_model_result(
                result,
                request=request,
                policy=DEFAULT_SELF_MODEL_POLICY,
                expected_policy_fingerprint=fingerprint,
            )
        except Exception:
            return None


def normalize_simulate_me_text(value: str, max_bytes: int) -> str:
    """Apply only strict UTF-8, NFC, edge-strip, control and byte-bound rules."""

    if type(value) is not str:
        raise SimulateMeInvalidRequestError()
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise SimulateMeInvalidRequestError() from None
    if _contains_forbidden_text_codepoint(value):
        raise SimulateMeInvalidRequestError()
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized or _contains_forbidden_text_codepoint(normalized):
        raise SimulateMeInvalidRequestError()
    try:
        size = len(normalized.encode("utf-8"))
    except UnicodeEncodeError:
        raise SimulateMeInvalidRequestError() from None
    if not MIN_TEXT_BYTES <= size <= max_bytes:
        raise SimulateMeInvalidRequestError()
    return normalized


def validate_simulate_me_request(request: object) -> SimulateMeRequest:
    """Validate exact request type, options, ids and normalized text bounds."""

    if type(request) is not SimulateMeRequest:
        raise SimulateMeInvalidRequestError()
    normalize_simulate_me_text(request.query, MAX_QUERY_BYTES)
    if type(request.options) is not tuple or not MIN_OPTIONS <= len(request.options) <= MAX_OPTIONS:
        raise SimulateMeInvalidRequestError()
    ids: set[str] = set()
    for option in request.options:
        if type(option) is not SimulateMeOption:
            raise SimulateMeInvalidRequestError()
        if type(option.id) is not str or _ID_PATTERN.fullmatch(option.id) is None:
            raise SimulateMeInvalidRequestError()
        if option.id in ids:
            raise SimulateMeInvalidRequestError()
        ids.add(option.id)
        normalize_simulate_me_text(option.label, MAX_LABEL_BYTES)
    return request


def validate_simulate_me_policy() -> str:
    """Return the fixed policy fingerprint after checking its canonical bytes."""

    digest = hashlib.sha256(POLICY_CANONICAL_JSON.encode("utf-8")).hexdigest()
    if f"sha256:{digest}" != POLICY_FINGERPRINT:
        raise SimulateMeResultInvalidError()
    return POLICY_FINGERPRINT


def validate_simulate_me_result(
    result: object,
    *,
    request: SimulateMeRequest,
) -> SimulateMeResult:
    """Validate the closed DTO shape and exact prediction/abstention invariants."""

    validate_simulate_me_request(request)
    if type(result) is not SimulateMeResult:
        raise SimulateMeResultInvalidError()
    if (
        type(result.kind) is not SimulateMeResultKind
        or result.derivation_version != DERIVATION_VERSION
        or result.policy_id != POLICY_ID
        or result.policy_fingerprint != POLICY_FINGERPRINT
        or not _FINGERPRINT_PATTERN.fullmatch(result.policy_fingerprint)
        or type(result.evidence_refs) is not tuple
        or type(result.contextual_evidence_refs) is not tuple
        or type(result.temporal_caveats) is not tuple
        or len(result.evidence_refs) > MAX_RESULT_REFS
        or len(result.contextual_evidence_refs) > MAX_RESULT_REFS
        or len(result.temporal_caveats) > MAX_RESULT_REFS
    ):
        raise SimulateMeResultInvalidError()
    if result.kind is SimulateMeResultKind.PREDICTION:
        if (
            type(result.selected_option) is not SimulateMeOption
            or result.selected_option not in request.options
            or result.abstention_code is not None
        ):
            raise SimulateMeResultInvalidError()
    elif (
        result.selected_option is not None
        or type(result.abstention_code) is not SimulateMeAbstentionCode
    ):
        raise SimulateMeResultInvalidError()

    evidence = tuple(_validate_evidence_ref(ref) for ref in result.evidence_refs)
    contextual = tuple(_validate_contextual_ref(ref) for ref in result.contextual_evidence_refs)
    if len({ref.claim_id for ref in evidence}) != len(evidence):
        raise SimulateMeResultInvalidError()
    if len({ref.claim_id for ref in contextual}) != len(contextual):
        raise SimulateMeResultInvalidError()
    if {ref.claim_id for ref in evidence} & {ref.claim_id for ref in contextual}:
        raise SimulateMeResultInvalidError()
    caveats = tuple(_validate_caveat(caveat) for caveat in result.temporal_caveats)
    if len({caveat.claim_id for caveat in caveats}) != len(caveats):
        raise SimulateMeResultInvalidError()
    all_claim_ids = {ref.claim_id for ref in evidence} | {ref.claim_id for ref in contextual}
    if not {caveat.claim_id for caveat in caveats} <= all_claim_ids:
        raise SimulateMeResultInvalidError()
    return result


def _build_result(
    *,
    request: SimulateMeRequest,
    kind: SimulateMeResultKind,
    selected_option: SimulateMeOption | None,
    evidence_refs: tuple[SimulateMeEvidenceRef, ...],
    contextual_evidence_refs: tuple[SimulateMeContextualEvidenceRef, ...],
    abstention_code: SimulateMeAbstentionCode | None,
) -> SimulateMeResult:
    all_refs: tuple[
        SimulateMeEvidenceRef | SimulateMeContextualEvidenceRef,
        ...,
    ] = (*evidence_refs, *contextual_evidence_refs)
    temporal_caveats = tuple(
        SimulateMeTemporalCaveat(
            code=SimulateMeTemporalCaveatCode.EVIDENCE_AT_UNKNOWN,
            claim_id=ref.claim_id,
        )
        for ref in all_refs
        if ref.evidence_at == "unknown"
    )
    if (
        len(evidence_refs) > MAX_RESULT_REFS
        or len(contextual_evidence_refs) > MAX_RESULT_REFS
        or len(temporal_caveats) > MAX_RESULT_REFS
    ):
        raise _ContextProjectionError()
    result = SimulateMeResult(
        kind=kind,
        selected_option=selected_option,
        evidence_refs=evidence_refs,
        contextual_evidence_refs=contextual_evidence_refs,
        temporal_caveats=temporal_caveats,
        abstention_code=abstention_code,
        derivation_version=DERIVATION_VERSION,
        policy_id=POLICY_ID,
        policy_fingerprint=validate_simulate_me_policy(),
    )
    return validate_simulate_me_result(result, request=request)


def _validated_abstention(
    request: SimulateMeRequest,
    code: SimulateMeAbstentionCode,
) -> SimulateMeResult:
    del request
    result = SimulateMeResult(
        kind=SimulateMeResultKind.ABSTENTION,
        selected_option=None,
        evidence_refs=(),
        contextual_evidence_refs=(),
        temporal_caveats=(),
        abstention_code=code,
        derivation_version=DERIVATION_VERSION,
        policy_id=POLICY_ID,
        policy_fingerprint=validate_simulate_me_policy(),
    )
    return result


def _evidence_ref(claim: SelfModelClaim) -> SimulateMeEvidenceRef:
    support = claim.supporting_evidence
    if len(support) != 1:
        raise _ContextProjectionError()
    source = support[0]
    note_ids = _bounded_note_ids((source.note_id, *source.related_note_ids))
    return SimulateMeEvidenceRef(
        claim_id=source.note_id,
        dimension=SimulateMeDimension(claim.dimension.value),
        note_ids=note_ids,
        evidence_at=source.evidence_at,
    )


def _contextual_ref(claim: SelfModelClaim) -> SimulateMeContextualEvidenceRef:
    source = _evidence_ref(claim)
    return SimulateMeContextualEvidenceRef(
        claim_id=source.claim_id,
        dimension=SimulateMeDimension.BELIEF,
        note_ids=source.note_ids,
        evidence_at=source.evidence_at,
    )


def _bounded_note_ids(note_ids: tuple[UUID, ...]) -> tuple[UUID, ...]:
    if any(type(note_id) is not UUID or note_id.version != 7 for note_id in note_ids):
        raise _ContextProjectionError()
    ordered = tuple(sorted(set(note_ids), key=str))
    if not ordered or len(ordered) > MAX_NOTE_IDS_PER_REF:
        raise _ContextProjectionError()
    return ordered


def _unique_evidence_refs(
    refs: Iterable[SimulateMeEvidenceRef],
) -> tuple[SimulateMeEvidenceRef, ...]:
    by_id: dict[UUID, SimulateMeEvidenceRef] = {}
    for ref in refs:
        previous = by_id.get(ref.claim_id)
        if previous is not None and previous != ref:
            raise _ContextProjectionError()
        by_id[ref.claim_id] = ref
    ordered = tuple(sorted(by_id.values(), key=lambda ref: str(ref.claim_id)))
    if len(ordered) > MAX_RESULT_REFS:
        raise _ContextProjectionError()
    return ordered


def _unique_contextual_refs(
    refs: Iterable[SimulateMeContextualEvidenceRef],
) -> tuple[SimulateMeContextualEvidenceRef, ...]:
    by_id: dict[UUID, SimulateMeContextualEvidenceRef] = {}
    for ref in refs:
        previous = by_id.get(ref.claim_id)
        if previous is not None and previous != ref:
            raise _ContextProjectionError()
        by_id[ref.claim_id] = ref
    ordered = tuple(sorted(by_id.values(), key=lambda ref: str(ref.claim_id)))
    if len(ordered) > MAX_RESULT_REFS:
        raise _ContextProjectionError()
    return ordered


def _option_by_id(request: SimulateMeRequest, option_id: str) -> SimulateMeOption:
    for option in request.options:
        if option.id == option_id:
            return option
    raise _ContextProjectionError()


def _validate_evidence_ref(ref: object) -> SimulateMeEvidenceRef:
    if type(ref) is not SimulateMeEvidenceRef:
        raise SimulateMeResultInvalidError()
    if (
        type(ref.claim_id) is not UUID
        or ref.claim_id.version != 7
        or type(ref.dimension) is not SimulateMeDimension
        or ref.dimension not in {SimulateMeDimension.PREFERENCE, SimulateMeDimension.GOAL}
        or type(ref.note_ids) is not tuple
        or not 1 <= len(ref.note_ids) <= MAX_NOTE_IDS_PER_REF
        or len(set(ref.note_ids)) != len(ref.note_ids)
        or ref.claim_id not in ref.note_ids
        or any(type(note_id) is not UUID or note_id.version != 7 for note_id in ref.note_ids)
        or not _valid_evidence_at(ref.evidence_at)
    ):
        raise SimulateMeResultInvalidError()
    return ref


def _validate_contextual_ref(ref: object) -> SimulateMeContextualEvidenceRef:
    if type(ref) is not SimulateMeContextualEvidenceRef:
        raise SimulateMeResultInvalidError()
    if (
        type(ref.claim_id) is not UUID
        or ref.claim_id.version != 7
        or ref.dimension is not SimulateMeDimension.BELIEF
        or type(ref.note_ids) is not tuple
        or not 1 <= len(ref.note_ids) <= MAX_NOTE_IDS_PER_REF
        or len(set(ref.note_ids)) != len(ref.note_ids)
        or ref.claim_id not in ref.note_ids
        or any(type(note_id) is not UUID or note_id.version != 7 for note_id in ref.note_ids)
        or not _valid_evidence_at(ref.evidence_at)
    ):
        raise SimulateMeResultInvalidError()
    return ref


def _validate_caveat(caveat: object) -> SimulateMeTemporalCaveat:
    if (
        type(caveat) is not SimulateMeTemporalCaveat
        or caveat.code is not SimulateMeTemporalCaveatCode.EVIDENCE_AT_UNKNOWN
        or type(caveat.claim_id) is not UUID
        or caveat.claim_id.version != 7
    ):
        raise SimulateMeResultInvalidError()
    return caveat


def _valid_evidence_at(value: EvidenceAt) -> bool:
    return value == "unknown" or (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _contains_forbidden_text_codepoint(value: str) -> bool:
    return any(
        (ord(char) <= 0x1F) or 0x7F <= ord(char) <= 0x9F or unicodedata.category(char) == "Cf"
        for char in value
    )


def _normalize_error_code(code: SimulateMeErrorCode | str) -> SimulateMeErrorCode:
    if isinstance(code, SimulateMeErrorCode):
        return code
    try:
        return SimulateMeErrorCode(code)
    except TypeError, ValueError:
        return SimulateMeErrorCode.RESULT_INVALID


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "DERIVATION_VERSION",
    "MAX_LABEL_BYTES",
    "MAX_NOTE_IDS_PER_REF",
    "MAX_OPTIONS",
    "MAX_QUERY_BYTES",
    "MAX_RESULT_REFS",
    "MIN_OPTIONS",
    "POLICY_CANONICAL_JSON",
    "POLICY_FINGERPRINT",
    "POLICY_ID",
    "BuildSimulateMe",
    "CurrentSelfModelBuilder",
    "SimulateMeAbstentionCode",
    "SimulateMeContextualEvidenceRef",
    "SimulateMeDimension",
    "SimulateMeError",
    "SimulateMeErrorCode",
    "SimulateMeEvidenceRef",
    "SimulateMeInvalidRequestError",
    "SimulateMeOption",
    "SimulateMeRequest",
    "SimulateMeResult",
    "SimulateMeResultInvalidError",
    "SimulateMeResultKind",
    "SimulateMeTemporalCaveat",
    "SimulateMeTemporalCaveatCode",
    "normalize_simulate_me_text",
    "validate_simulate_me_policy",
    "validate_simulate_me_request",
    "validate_simulate_me_result",
]
