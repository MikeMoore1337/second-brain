"""On-demand, derived Self Model v1 read model over the canonical vault scan.

This module deliberately contains no persistence, network, Search, LLM, or
write capability.  Every emitted claim is rebuilt from the current validated
``VaultReader`` report.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Final
from uuid import UUID

from second_brain.application.personal_memory import is_personal_memory_enrolled
from second_brain.application.ports import VaultReader
from second_brain.application.reports import (
    DiagnosticSeverity,
    ScanReport,
)
from second_brain.application.validation import build_report
from second_brain.domain.models import (
    EvidenceAt,
    EvidenceAtPrecision,
    EvidenceKind,
    NoteRecord,
    NoteType,
    PersonalMemoryMetadata,
    SelfKind,
    VaultManifest,
)

type SelfModelClock = Callable[[], datetime]

DERIVATION_VERSION: Final[str] = "self-model-derivation-v1"
CLAIM_GENERATION_POLICY: Final[str] = "direct-assertion-v1"
CONFIDENCE_POLICY: Final[str] = "unassessed-v1"
EVIDENCE_WEIGHT_POLICY: Final[str] = "none-v1"
RECENCY_POLICY: Final[str] = "explanation-only-v1"
CONTRADICTION_POLICY: Final[str] = "explicit-only-v1"
STALE_POLICY: Final[str] = "disabled-v1"
SUPERSEDE_POLICY: Final[str] = "disabled-v1"
PRECEDENCE_POLICY: Final[str] = "none-v1"
STATUS_POLICY: Final[str] = "disabled-v1"

DEFAULT_SELF_MODEL_MAX_CLAIMS: Final[int] = 200
MIN_SELF_MODEL_LIMIT: Final[int] = 1
MAX_SELF_MODEL_LIMIT: Final[int] = 200
MAX_SELF_MODEL_CLAIM_BYTES: Final[int] = 4096
MAX_SELF_MODEL_DOMAIN_BYTES: Final[int] = 64

_FINGERPRINT_POLICY_FIELDS: Final[tuple[str, ...]] = (
    "claim_generation_policy",
    "confidence_policy",
    "contradiction_policy",
    "evidence_weight_policy",
    "precedence_policy",
    "recency_policy",
    "stale_policy",
    "status_policy",
    "supersede_policy",
)
_CONTENT_ROOT_FIELDS: Final[tuple[str, ...]] = (
    "inbox",
    "projects",
    "areas",
    "resources",
    "zettelkasten",
    "archive",
)
_SAFE_RELATIVE_PATH_PARTS: Final[frozenset[str]] = frozenset({"", ".", ".."})
_SCOPED_SCAN_DIAGNOSTIC_CODES: Final[frozenset[str]] = frozenset(
    {
        "VAULT_ROOT_MISSING",
        "VAULT_ROOT_NOT_DIRECTORY",
        "VAULT_LINKED_DIRECTORY",
        "VAULT_ENTRY_RESOLVE_ERROR",
        "VAULT_PATH_ESCAPE",
    }
)
_BLOCKING_DIAGNOSTIC_CODES: Final[frozenset[str]] = frozenset(
    {
        "DUPLICATE_NOTE_ID",
        "DECISION_JOURNAL_INVALID_BODY",
        "OUTCOME_OBSERVATION_INVALID_BODY",
    }
)
_DOMAIN_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z", re.ASCII)


class SelfModelDimension(StrEnum):
    """Bounded derived dimensions; the last two are future-only seams."""

    PREFERENCE = "preference"
    BELIEF = "belief"
    GOAL = "goal"
    DECISION_RULE = "decision_rule"
    BEHAVIORAL_PATTERN = "behavioral_pattern"


class SelfModelConfidenceState(StrEnum):
    """Only the explicitly unassessed v1 state is emitted."""

    NOT_ASSESSED = "not_assessed"


class SelfModelErrorCode(StrEnum):
    """Stable, bounded error codes for the Self Model application boundary."""

    INVALID_REQUEST = "SELF_MODEL_INVALID_REQUEST"
    INVALID_CLOCK = "SELF_MODEL_INVALID_CLOCK"
    POLICY_UNAVAILABLE = "SELF_MODEL_POLICY_UNAVAILABLE"
    VAULT_UNAVAILABLE = "SELF_MODEL_VAULT_UNAVAILABLE"
    EVIDENCE_INVALID = "SELF_MODEL_EVIDENCE_INVALID"
    RESULT_INVALID = "SELF_MODEL_RESULT_INVALID"
    RESULT_TOO_LARGE = "SELF_MODEL_RESULT_TOO_LARGE"


@dataclass(frozen=True, slots=True)
class SelfModelEvidenceRef:
    """A current canonical evidence reference without body or path duplication."""

    note_id: UUID
    evidence_kind: EvidenceKind
    self_kind: SelfKind
    domain: str | None
    evidence_at: EvidenceAt
    evidence_at_precision: EvidenceAtPrecision
    related_note_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class SelfModelTemporalContext:
    """Descriptive evidence-time aggregate; unknown time remains unknown."""

    earliest_known_evidence_at: datetime | None
    latest_known_evidence_at: datetime | None
    known_evidence_count: int
    unknown_evidence_count: int


@dataclass(frozen=True, slots=True)
class SelfModelConfidence:
    """Unassessed envelope with descriptive counts, never a model score."""

    state: SelfModelConfidenceState
    score: None
    policy_version: str
    supporting_evidence_count: int
    contradicting_evidence_count: int
    unknown_time_count: int


@dataclass(frozen=True, slots=True)
class SelfModelStatus:
    """Future status seam; #82 does not emit this object."""

    code: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class SelfModelClaim:
    """One immutable derived direct assertion and its explainability envelope."""

    dimension: SelfModelDimension
    claim: str
    domain: str | None
    supporting_evidence: tuple[SelfModelEvidenceRef, ...]
    contradicting_evidence: tuple[SelfModelEvidenceRef, ...]
    contextual_evidence: tuple[SelfModelEvidenceRef, ...]
    confidence: SelfModelConfidence
    temporal_context: SelfModelTemporalContext
    generated_at: datetime
    derivation_version: str
    status: SelfModelStatus | None = None


@dataclass(frozen=True, slots=True)
class SelfModelRequest:
    """Bounded in-memory request; policy is intentionally not client input."""

    max_claims: int = DEFAULT_SELF_MODEL_MAX_CLAIMS
    max_evidence_refs_per_claim: int = DEFAULT_SELF_MODEL_MAX_CLAIMS


@dataclass(frozen=True, slots=True)
class SelfModelResult:
    """Complete disposable result of one current-vault build."""

    claims: tuple[SelfModelClaim, ...]
    eligible_evidence_count: int
    represented_evidence_count: int
    generated_at: datetime
    derivation_version: str
    policy_fingerprint: str


@dataclass(frozen=True, slots=True)
class SelfModelPolicy:
    """Composition-owned exact v1 policy binding."""

    derivation_version: str
    claim_generation_policy: str
    confidence_policy: str
    evidence_weight_policy: str
    recency_policy: str
    contradiction_policy: str
    stale_policy: str
    supersede_policy: str
    precedence_policy: str
    status_policy: str


DEFAULT_SELF_MODEL_POLICY: Final[SelfModelPolicy] = SelfModelPolicy(
    derivation_version=DERIVATION_VERSION,
    claim_generation_policy=CLAIM_GENERATION_POLICY,
    confidence_policy=CONFIDENCE_POLICY,
    evidence_weight_policy=EVIDENCE_WEIGHT_POLICY,
    recency_policy=RECENCY_POLICY,
    contradiction_policy=CONTRADICTION_POLICY,
    stale_policy=STALE_POLICY,
    supersede_policy=SUPERSEDE_POLICY,
    precedence_policy=PRECEDENCE_POLICY,
    status_policy=STATUS_POLICY,
)


_ERROR_MESSAGES: Final[dict[SelfModelErrorCode, str]] = {
    SelfModelErrorCode.INVALID_REQUEST: "self model request failed validation",
    SelfModelErrorCode.INVALID_CLOCK: "self model clock must return an aware datetime",
    SelfModelErrorCode.POLICY_UNAVAILABLE: "self model policy binding is unavailable",
    SelfModelErrorCode.VAULT_UNAVAILABLE: "self model vault read is unavailable",
    SelfModelErrorCode.EVIDENCE_INVALID: "self model evidence is invalid",
    SelfModelErrorCode.RESULT_INVALID: "self model result failed validation",
    SelfModelErrorCode.RESULT_TOO_LARGE: "self model result exceeds its bounded limit",
}


class SelfModelError(RuntimeError):
    """Safe application error without paths, bodies, or exception details."""

    def __init__(self, code: SelfModelErrorCode | str) -> None:
        normalized = _normalize_error_code(code)
        self.code = normalized.value
        self.message = _ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Return a stable JSON-compatible error projection."""

        return {"code": self.code, "message": self.message}


class SelfModelInvalidRequestError(SelfModelError):
    """The request type or bounds are invalid."""

    def __init__(self) -> None:
        super().__init__(SelfModelErrorCode.INVALID_REQUEST)


class SelfModelInvalidClockError(SelfModelError):
    """The injected application clock is unavailable or naive."""

    def __init__(self) -> None:
        super().__init__(SelfModelErrorCode.INVALID_CLOCK)


class SelfModelPolicyUnavailableError(SelfModelError):
    """The exact approved policy binding cannot be proven."""

    def __init__(self) -> None:
        super().__init__(SelfModelErrorCode.POLICY_UNAVAILABLE)


class SelfModelVaultUnavailableError(SelfModelError):
    """The current vault scan cannot be safely read."""

    def __init__(self) -> None:
        super().__init__(SelfModelErrorCode.VAULT_UNAVAILABLE)


class SelfModelEvidenceInvalidError(SelfModelError):
    """Canonical evidence integrity is insufficient for a complete build."""

    def __init__(self) -> None:
        super().__init__(SelfModelErrorCode.EVIDENCE_INVALID)


class SelfModelResultInvalidError(SelfModelError):
    """A derived DTO violates the bounded application contract."""

    def __init__(self) -> None:
        super().__init__(SelfModelErrorCode.RESULT_INVALID)


class SelfModelResultTooLargeError(SelfModelError):
    """The complete result or direct claim exceeds a declared bound."""

    def __init__(self) -> None:
        super().__init__(SelfModelErrorCode.RESULT_TOO_LARGE)


@dataclass(frozen=True, slots=True)
class BuildSelfModel:
    """Rebuild the Self Model from the current canonical vault on every call."""

    reader: VaultReader
    policy: SelfModelPolicy
    clock: SelfModelClock

    def execute(self, request: SelfModelRequest) -> SelfModelResult:
        """Return a complete in-memory result or a bounded safe error."""

        validate_self_model_request(request)
        policy_fingerprint = validate_self_model_policy(self.policy)
        generated_at = _read_clock(self.clock)

        try:
            report = _read_report(self.reader)
            _validate_scan_completeness(report)
            eligible = _collect_eligible_evidence(report)
            claims = _build_claims(
                eligible,
                generated_at=generated_at,
                policy=self.policy,
                max_claims=request.max_claims,
            )
            result = SelfModelResult(
                claims=claims,
                eligible_evidence_count=len(eligible),
                represented_evidence_count=_represented_evidence_count(claims),
                generated_at=generated_at,
                derivation_version=self.policy.derivation_version,
                policy_fingerprint=policy_fingerprint,
            )
            validate_self_model_result(
                result,
                request=request,
                policy=self.policy,
                expected_policy_fingerprint=policy_fingerprint,
            )
            return result
        except SelfModelError:
            raise
        except Exception:
            raise SelfModelResultInvalidError() from None


def validate_self_model_request(request: object) -> SelfModelRequest:
    """Validate the exact request type and strict integer bounds before scan."""

    if type(request) is not SelfModelRequest:
        raise SelfModelInvalidRequestError()
    if not _valid_limit(request.max_claims) or not _valid_limit(
        request.max_evidence_refs_per_claim
    ):
        raise SelfModelInvalidRequestError()
    return request


def validate_self_model_policy(policy: object) -> str:
    """Validate the complete approved binding and return its fingerprint."""

    if type(policy) is not SelfModelPolicy:
        raise SelfModelPolicyUnavailableError()
    expected = DEFAULT_SELF_MODEL_POLICY
    for field in (
        "derivation_version",
        *_FINGERPRINT_POLICY_FIELDS,
    ):
        value = getattr(policy, field, None)
        if type(value) is not str or value != getattr(expected, field):
            raise SelfModelPolicyUnavailableError()
    return _compute_policy_fingerprint(policy)


def validate_self_model_claim(
    claim: object,
    *,
    max_evidence_refs_per_claim: int = MAX_SELF_MODEL_LIMIT,
) -> SelfModelClaim:
    """Validate one exact v1 emitted claim and return it unchanged."""

    if type(claim) is not SelfModelClaim:
        raise SelfModelResultInvalidError()
    if not _valid_limit(max_evidence_refs_per_claim):
        raise SelfModelResultInvalidError()
    if type(claim.dimension) is not SelfModelDimension or claim.dimension not in {
        SelfModelDimension.PREFERENCE,
        SelfModelDimension.BELIEF,
        SelfModelDimension.GOAL,
    }:
        raise SelfModelResultInvalidError()
    if not _valid_claim_text(claim.claim):
        raise SelfModelResultInvalidError()
    if not _valid_domain(claim.domain):
        raise SelfModelResultInvalidError()
    if (
        type(claim.supporting_evidence) is not tuple
        or type(claim.contradicting_evidence) is not tuple
        or type(claim.contextual_evidence) is not tuple
        or not claim.supporting_evidence
    ):
        raise SelfModelResultInvalidError()
    all_roles = (
        claim.supporting_evidence,
        claim.contradicting_evidence,
        claim.contextual_evidence,
    )
    if any(len(role) > max_evidence_refs_per_claim for role in all_roles):
        raise SelfModelResultTooLargeError()
    if any(not _valid_evidence_ref(ref) for role in all_roles for ref in role):
        raise SelfModelResultInvalidError()
    role_ids = [tuple(ref.note_id for ref in role) for role in all_roles]
    if any(len(ids) != len(set(ids)) for ids in role_ids):
        raise SelfModelResultInvalidError()
    if set(role_ids[0]) & set(role_ids[1]):
        raise SelfModelResultInvalidError()
    if set(role_ids[0]) & set(role_ids[2]):
        raise SelfModelResultInvalidError()
    if set(role_ids[1]) & set(role_ids[2]):
        raise SelfModelResultInvalidError()

    # #82 is intentionally narrower than the future DTO seam: one direct
    # assertion, one support ref, and no inferred relation role.
    if (
        len(claim.supporting_evidence) != 1
        or claim.contradicting_evidence != ()
        or claim.contextual_evidence != ()
        or claim.status is not None
    ):
        raise SelfModelResultInvalidError()
    support = claim.supporting_evidence[0]
    if support.related_note_ids != () or not _is_direct_assertion_pair(
        support.evidence_kind, support.self_kind
    ):
        raise SelfModelResultInvalidError()
    if _dimension_for_self_kind(support.self_kind) is not claim.dimension:
        raise SelfModelResultInvalidError()
    if support.domain != claim.domain:
        raise SelfModelResultInvalidError()
    if not _is_aware(claim.generated_at) or claim.derivation_version != DERIVATION_VERSION:
        raise SelfModelResultInvalidError()
    if type(claim.confidence) is not SelfModelConfidence:
        raise SelfModelResultInvalidError()
    confidence = claim.confidence
    if (
        confidence.state is not SelfModelConfidenceState.NOT_ASSESSED
        or confidence.score is not None
        or confidence.policy_version != CONFIDENCE_POLICY
        or confidence.supporting_evidence_count != len(claim.supporting_evidence)
        or confidence.contradicting_evidence_count != len(claim.contradicting_evidence)
        or confidence.unknown_time_count
        != sum(ref.evidence_at == "unknown" for role in all_roles for ref in role)
    ):
        raise SelfModelResultInvalidError()
    if type(claim.temporal_context) is not SelfModelTemporalContext:
        raise SelfModelResultInvalidError()
    if claim.temporal_context != _temporal_context(
        tuple(ref for role in all_roles for ref in role)
    ):
        raise SelfModelResultInvalidError()
    return claim


def validate_self_model_result(
    result: object,
    *,
    request: SelfModelRequest,
    policy: SelfModelPolicy,
    expected_policy_fingerprint: str,
) -> SelfModelResult:
    """Validate a complete result, including fingerprint and deterministic order."""

    if type(result) is not SelfModelResult:
        raise SelfModelResultInvalidError()
    if type(request) is not SelfModelRequest or type(policy) is not SelfModelPolicy:
        raise SelfModelResultInvalidError()
    if type(result.claims) is not tuple:
        raise SelfModelResultInvalidError()
    if len(result.claims) > request.max_claims:
        raise SelfModelResultTooLargeError()
    if (
        not _valid_non_negative_int(result.eligible_evidence_count)
        or not _valid_non_negative_int(result.represented_evidence_count)
        or not _is_aware(result.generated_at)
        or result.derivation_version != DERIVATION_VERSION
        or result.derivation_version != policy.derivation_version
        or type(result.policy_fingerprint) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", result.policy_fingerprint)
        or result.policy_fingerprint != expected_policy_fingerprint
    ):
        raise SelfModelResultInvalidError()
    claims = tuple(
        validate_self_model_claim(
            claim,
            max_evidence_refs_per_claim=request.max_evidence_refs_per_claim,
        )
        for claim in result.claims
    )
    if claims != tuple(sorted(claims, key=_claim_sort_key)):
        raise SelfModelResultInvalidError()
    represented_ids = {
        ref.note_id
        for claim in claims
        for role in (
            claim.supporting_evidence,
            claim.contradicting_evidence,
            claim.contextual_evidence,
        )
        for ref in role
    }
    if result.represented_evidence_count != len(represented_ids):
        raise SelfModelResultInvalidError()
    if result.represented_evidence_count > result.eligible_evidence_count:
        raise SelfModelResultInvalidError()
    return result


def _normalize_error_code(code: SelfModelErrorCode | str) -> SelfModelErrorCode:
    if isinstance(code, SelfModelErrorCode):
        return code
    try:
        return SelfModelErrorCode(code)
    except TypeError, ValueError:
        return SelfModelErrorCode.RESULT_INVALID


def _valid_limit(value: object) -> bool:
    return type(value) is int and MIN_SELF_MODEL_LIMIT <= value <= MAX_SELF_MODEL_LIMIT


def _valid_non_negative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _read_clock(clock: object) -> datetime:
    if not callable(clock):
        raise SelfModelInvalidClockError()
    try:
        value = clock()
    except Exception:
        raise SelfModelInvalidClockError() from None
    if not isinstance(value, datetime) or not _is_aware(value):
        raise SelfModelInvalidClockError()
    return value


def _read_report(reader: VaultReader) -> ScanReport:
    """Use only ``scan() -> build_report()`` and reject an invalid manifest."""

    try:
        report = build_report(reader.scan())
        manifest = report.manifest
        if type(report) is not ScanReport or type(manifest) is not VaultManifest:
            raise SelfModelVaultUnavailableError()
        if any(
            item.severity is DiagnosticSeverity.ERROR and item.code.startswith("MANIFEST_")
            for item in report.diagnostics
        ):
            raise SelfModelVaultUnavailableError()
    except SelfModelError:
        raise
    except Exception:
        raise SelfModelVaultUnavailableError() from None
    return report


def _validate_scan_completeness(report: ScanReport) -> None:
    """Reject diagnostics that make current canonical content incomplete."""

    for diagnostic in report.diagnostics:
        if diagnostic.code == "NOTE_FRONT_MATTER_ERROR":
            raise SelfModelEvidenceInvalidError()
        if diagnostic.code in {
            "NOTE_READ_ERROR",
            "VAULT_DIRECTORY_READ_ERROR",
            "VAULT_OVERLAPPING_ROOTS",
        }:
            raise SelfModelVaultUnavailableError()
        if diagnostic.code in _SCOPED_SCAN_DIAGNOSTIC_CODES and _diagnostic_affects_content_scope(
            diagnostic.path, report
        ):
            raise SelfModelVaultUnavailableError()
        if (
            diagnostic.code.startswith("MANIFEST_")
            and diagnostic.severity is DiagnosticSeverity.ERROR
        ):
            raise SelfModelVaultUnavailableError()

    _validate_evidence_integrity(report)


def _diagnostic_affects_content_scope(path: str | None, report: ScanReport) -> bool:
    if path is None or report.manifest is None:
        return True
    candidate = PurePosixPath(path)
    return any(
        _is_path_under(candidate, getattr(report.manifest.paths, field))
        for field in _CONTENT_ROOT_FIELDS
    )


def _is_path_under(path: PurePosixPath, root: PurePosixPath) -> bool:
    return path == root or root in path.parents


def _validate_evidence_integrity(report: ScanReport) -> None:
    if any(_is_blocking_diagnostic(item.code) for item in report.diagnostics):
        raise SelfModelEvidenceInvalidError()
    for note in report.notes:
        if not is_personal_memory_enrolled(note.front_matter):
            continue
        if (
            note.personal_memory is None
            or not _has_valid_storage_identity(note)
            or not _is_safe_relative_path(note.relative_path)
            or not _valid_metadata(note.personal_memory, note)
        ):
            raise SelfModelEvidenceInvalidError()


def _is_blocking_diagnostic(code: str) -> bool:
    return code in _BLOCKING_DIAGNOSTIC_CODES or code.startswith(
        ("PERSONAL_MEMORY_", "OUTCOME_DECISION_")
    )


def _collect_eligible_evidence(
    report: ScanReport,
) -> tuple[tuple[NoteRecord, PersonalMemoryMetadata], ...]:
    eligible: list[tuple[NoteRecord, PersonalMemoryMetadata]] = []
    seen_ids: set[UUID] = set()
    for note in report.notes:
        metadata = note.personal_memory
        if metadata is None:
            continue
        if (
            not _has_valid_storage_identity(note)
            or not _is_safe_relative_path(note.relative_path)
            or not _valid_metadata(metadata, note)
            or note.note_id is None
        ):
            raise SelfModelEvidenceInvalidError()
        if note.note_id in seen_ids:
            raise SelfModelEvidenceInvalidError()
        seen_ids.add(note.note_id)
        eligible.append((note, metadata))
    return tuple(eligible)


def _build_claims(
    eligible: tuple[tuple[NoteRecord, PersonalMemoryMetadata], ...],
    *,
    generated_at: datetime,
    policy: SelfModelPolicy,
    max_claims: int,
) -> tuple[SelfModelClaim, ...]:
    direct = tuple(
        item
        for item in eligible
        if _is_direct_assertion(item[1]) and _dimension_for_self_kind(item[1].self_kind) is not None
    )
    if len(direct) > max_claims:
        raise SelfModelResultTooLargeError()

    claims: list[SelfModelClaim] = []
    for note, metadata in direct:
        claim_text = _project_direct_body(note.body)
        assert note.note_id is not None
        ref = SelfModelEvidenceRef(
            note_id=note.note_id,
            evidence_kind=metadata.evidence_kind,
            self_kind=metadata.self_kind,
            domain=metadata.domain,
            evidence_at=metadata.evidence_at,
            evidence_at_precision=metadata.evidence_at_precision,
            related_note_ids=(),
        )
        dimension = _dimension_for_self_kind(metadata.self_kind)
        assert dimension is not None
        claims.append(
            SelfModelClaim(
                dimension=dimension,
                claim=claim_text,
                domain=metadata.domain,
                supporting_evidence=(ref,),
                contradicting_evidence=(),
                contextual_evidence=(),
                confidence=SelfModelConfidence(
                    state=SelfModelConfidenceState.NOT_ASSESSED,
                    score=None,
                    policy_version=policy.confidence_policy,
                    supporting_evidence_count=1,
                    contradicting_evidence_count=0,
                    unknown_time_count=int(metadata.evidence_at == "unknown"),
                ),
                temporal_context=_temporal_context((ref,)),
                generated_at=generated_at,
                derivation_version=policy.derivation_version,
                status=None,
            )
        )
    return tuple(sorted(claims, key=_claim_sort_key))


def _project_direct_body(value: object) -> str:
    if type(value) is not str:
        raise SelfModelEvidenceInvalidError()
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        raise SelfModelEvidenceInvalidError()
    if any(
        (ord(char) < 0x20 and char not in {"\t", "\n"}) or ord(char) == 0x7F for char in normalized
    ):
        raise SelfModelEvidenceInvalidError()
    try:
        encoded = normalized.encode("utf-8")
    except UnicodeEncodeError:
        raise SelfModelEvidenceInvalidError() from None
    if len(encoded) > MAX_SELF_MODEL_CLAIM_BYTES:
        raise SelfModelResultTooLargeError()
    return normalized


def _represented_evidence_count(claims: tuple[SelfModelClaim, ...]) -> int:
    return len(
        {
            ref.note_id
            for claim in claims
            for role in (
                claim.supporting_evidence,
                claim.contradicting_evidence,
                claim.contextual_evidence,
            )
            for ref in role
        }
    )


def _temporal_context(refs: tuple[SelfModelEvidenceRef, ...]) -> SelfModelTemporalContext:
    known = tuple(ref.evidence_at for ref in refs if isinstance(ref.evidence_at, datetime))
    unknown_count = sum(ref.evidence_at == "unknown" for ref in refs)
    if not known:
        return SelfModelTemporalContext(None, None, 0, unknown_count)
    ordered = sorted(known, key=lambda value: (value.astimezone(UTC), value.isoformat()))
    return SelfModelTemporalContext(
        earliest_known_evidence_at=ordered[0],
        latest_known_evidence_at=ordered[-1],
        known_evidence_count=len(known),
        unknown_evidence_count=unknown_count,
    )


def _claim_sort_key(claim: SelfModelClaim) -> tuple[str, str, str, str]:
    return (
        claim.dimension.value,
        claim.domain or "",
        str(claim.supporting_evidence[0].note_id),
        claim.claim,
    )


def _valid_evidence_ref(ref: object) -> bool:
    if type(ref) is not SelfModelEvidenceRef:
        return False
    if (
        type(ref.note_id) is not UUID
        or ref.note_id.version != 7
        or type(ref.evidence_kind) is not EvidenceKind
        or type(ref.self_kind) is not SelfKind
        or not _valid_domain(ref.domain)
        or not _valid_event_time(ref.evidence_at, ref.evidence_at_precision)
        or type(ref.related_note_ids) is not tuple
    ):
        return False
    if any(type(item) is not UUID or item.version != 7 for item in ref.related_note_ids):
        return False
    return len(set(ref.related_note_ids)) == len(ref.related_note_ids)


def _valid_metadata(metadata: PersonalMemoryMetadata, note: NoteRecord) -> bool:
    if type(metadata) is not PersonalMemoryMetadata:
        return False
    if (
        type(metadata.evidence_kind) is not EvidenceKind
        or type(metadata.self_kind) is not SelfKind
        or not _valid_domain(metadata.domain)
        or not _valid_event_time(metadata.evidence_at, metadata.evidence_at_precision)
    ):
        return False
    if not _valid_kind_pair(metadata.evidence_kind, metadata.self_kind):
        return False
    if metadata.evidence_kind is EvidenceKind.OBSERVED_DECISION:
        return note.decision_journal is not None and metadata.self_kind is SelfKind.DECISION
    if metadata.evidence_kind is EvidenceKind.OUTCOME_LATER_OBSERVATION:
        return (
            metadata.decision_id is not None
            and type(metadata.decision_id) is UUID
            and metadata.decision_id.version == 7
            and note.outcome_observation is not None
            and note.outcome_observation.decision_id == metadata.decision_id
            and metadata.self_kind is SelfKind.OUTCOME
        )
    return metadata.decision_id is None


def _valid_kind_pair(evidence_kind: EvidenceKind, self_kind: SelfKind) -> bool:
    if evidence_kind in {EvidenceKind.EXPLICIT_USER_FACT, EvidenceKind.USER_STATEMENT}:
        return self_kind in {
            SelfKind.MEMORY,
            SelfKind.PREFERENCE,
            SelfKind.BELIEF,
            SelfKind.GOAL,
        }
    if evidence_kind is EvidenceKind.OBSERVED_DECISION:
        return self_kind is SelfKind.DECISION
    if evidence_kind is EvidenceKind.OUTCOME_LATER_OBSERVATION:
        return self_kind is SelfKind.OUTCOME
    return False


def _is_direct_assertion(metadata: PersonalMemoryMetadata) -> bool:
    return _is_direct_assertion_pair(metadata.evidence_kind, metadata.self_kind)


def _is_direct_assertion_pair(evidence_kind: EvidenceKind, self_kind: SelfKind) -> bool:
    return evidence_kind in {
        EvidenceKind.EXPLICIT_USER_FACT,
        EvidenceKind.USER_STATEMENT,
    } and self_kind in {
        SelfKind.PREFERENCE,
        SelfKind.BELIEF,
        SelfKind.GOAL,
    }


def _dimension_for_self_kind(self_kind: SelfKind) -> SelfModelDimension | None:
    return {
        SelfKind.PREFERENCE: SelfModelDimension.PREFERENCE,
        SelfKind.BELIEF: SelfModelDimension.BELIEF,
        SelfKind.GOAL: SelfModelDimension.GOAL,
    }.get(self_kind)


def _valid_claim_text(value: object) -> bool:
    if type(value) is not str or not value:
        return False
    if any((ord(char) < 0x20 and char not in {"\t", "\n"}) or ord(char) == 0x7F for char in value):
        return False
    try:
        return len(value.encode("utf-8")) <= MAX_SELF_MODEL_CLAIM_BYTES
    except UnicodeEncodeError:
        return False


def _valid_domain(value: object) -> bool:
    if value is None:
        return True
    if type(value) is not str:
        return False
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return len(encoded) <= MAX_SELF_MODEL_DOMAIN_BYTES and bool(_DOMAIN_PATTERN.fullmatch(value))


def _valid_event_time(value: object, precision: object) -> bool:
    if type(precision) is not EvidenceAtPrecision:
        return False
    if value == "unknown":
        return precision is EvidenceAtPrecision.UNKNOWN
    return (
        isinstance(value, datetime) and _is_aware(value) and precision is EvidenceAtPrecision.EXACT
    )


def _has_valid_storage_identity(note: NoteRecord) -> bool:
    return (
        note.managed is True
        and type(note.note_id) is UUID
        and note.note_id.version == 7
        and type(note.note_type) is NoteType
        and _is_aware(note.created)
        and (note.updated is None or _is_aware(note.updated))
    )


def _is_aware(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _is_safe_relative_path(value: object) -> bool:
    if type(value) is not str or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in _SAFE_RELATIVE_PATH_PARTS for part in path.parts)
    )


def _compute_policy_fingerprint(policy: SelfModelPolicy) -> str:
    payload = {field: getattr(policy, field) for field in _FINGERPRINT_POLICY_FIELDS}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CLAIM_GENERATION_POLICY",
    "CONFIDENCE_POLICY",
    "CONTRADICTION_POLICY",
    "DEFAULT_SELF_MODEL_MAX_CLAIMS",
    "DEFAULT_SELF_MODEL_POLICY",
    "DERIVATION_VERSION",
    "EVIDENCE_WEIGHT_POLICY",
    "MAX_SELF_MODEL_CLAIM_BYTES",
    "MAX_SELF_MODEL_DOMAIN_BYTES",
    "MAX_SELF_MODEL_LIMIT",
    "MIN_SELF_MODEL_LIMIT",
    "PRECEDENCE_POLICY",
    "RECENCY_POLICY",
    "STALE_POLICY",
    "STATUS_POLICY",
    "SUPERSEDE_POLICY",
    "BuildSelfModel",
    "SelfModelClaim",
    "SelfModelConfidence",
    "SelfModelConfidenceState",
    "SelfModelDimension",
    "SelfModelError",
    "SelfModelErrorCode",
    "SelfModelEvidenceInvalidError",
    "SelfModelEvidenceRef",
    "SelfModelInvalidClockError",
    "SelfModelInvalidRequestError",
    "SelfModelPolicy",
    "SelfModelPolicyUnavailableError",
    "SelfModelRequest",
    "SelfModelResult",
    "SelfModelResultInvalidError",
    "SelfModelResultTooLargeError",
    "SelfModelStatus",
    "SelfModelTemporalContext",
    "SelfModelVaultUnavailableError",
    "validate_self_model_claim",
    "validate_self_model_policy",
    "validate_self_model_request",
    "validate_self_model_result",
]
