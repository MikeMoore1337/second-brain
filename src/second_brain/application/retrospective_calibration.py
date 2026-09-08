"""Provider-free Retrospective Calibration v1 application core.

The use case is deliberately composed from three explicit seams: a
pre-materialization bounded scanner, a filtered current Self Model builder,
and one provider-free Simulate Me replay.  It does not accept a normal
``VaultReader`` and has no persistence, network, Search, or write capability.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Final, Protocol, cast
from uuid import UUID

from second_brain.application.decision_journal import (
    JOURNAL_REQUIRED_HEADINGS,
    MAX_JOURNAL_OPTIONS,
    MIN_JOURNAL_OPTIONS,
    DecisionJournalBodyError,
    _parse_bullet_list,
    _parse_sections,
    parse_decision_journal_body,
)
from second_brain.application.personal_memory import (
    PERSONAL_MEMORY_UNKNOWN_TIME,
    is_personal_memory_enrolled,
    validate_canonical_personal_memory_fields,
)
from second_brain.application.reports import (
    DiagnosticSeverity,
    ScanReport,
    VaultSnapshot,
    diagnostic_affects_content,
)
from second_brain.application.retrospective_calibration_scan import (
    RetrospectiveCalibrationScanLimitsV1,
    RetrospectiveCalibrationScanV1,
)
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    BuildSelfModel,
    SelfModelClaim,
    SelfModelEvidenceRef,
    SelfModelRequest,
    SelfModelResult,
    validate_self_model_policy,
    validate_self_model_request,
    validate_self_model_result,
)
from second_brain.application.simulate_me import (
    DERIVATION_VERSION as SIMULATE_ME_DERIVATION_VERSION,
)
from second_brain.application.simulate_me import (
    POLICY_FINGERPRINT as SIMULATE_ME_POLICY_FINGERPRINT,
)
from second_brain.application.simulate_me import (
    POLICY_ID as SIMULATE_ME_POLICY_ID,
)
from second_brain.application.simulate_me import (
    BuildSimulateMe,
    SimulateMeAbstentionCode,
    SimulateMeError,
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResult,
    SimulateMeResultKind,
    validate_simulate_me_policy,
    validate_simulate_me_request,
    validate_simulate_me_result,
)
from second_brain.application.validation import build_report
from second_brain.domain.models import (
    DecisionJournalRecord,
    EvidenceAtPrecision,
    EvidenceKind,
    MarkdownDocument,
    NoteRecord,
    NoteType,
    PersonalMemoryMetadata,
    SelfKind,
    VaultManifest,
    parse_rfc3339,
)

DERIVATION_VERSION: Final[str] = "retrospective-calibration-v1"
POLICY_ID: Final[str] = "retrospective-simulate-me-exact-cutoff-v1"
RECONSTRUCTION_MODE: Final[str] = "current-vault-temporal-projection-v1"

MAX_SCAN_ENTRIES_V1: Final[int] = 16_384
MAX_SCAN_DOCUMENTS_V1: Final[int] = 4_096
MAX_SCAN_BYTES_V1: Final[int] = 16_777_216
MAX_DECISION_CASES_V1: Final[int] = 512
MAX_RESULT_BYTES_V1: Final[int] = 65_536

SELF_MODEL_DERIVATION_VERSION: Final[str] = "self-model-derivation-v1"
SELF_MODEL_POLICY_FINGERPRINT: Final[str] = (
    "d7969ba732665c0406736b0e669a9123ccd0ee7b12de36f57899f59f94282cd3"
)
SELF_MODEL_REQUEST_V1: Final[SelfModelRequest] = SelfModelRequest(
    max_claims=200,
    max_evidence_refs_per_claim=200,
)

SIMULATE_ME_DERIVATION_VERSION_V1: Final[str] = SIMULATE_ME_DERIVATION_VERSION
SIMULATE_ME_POLICY_ID_V1: Final[str] = SIMULATE_ME_POLICY_ID
SIMULATE_ME_POLICY_FINGERPRINT_V1: Final[str] = SIMULATE_ME_POLICY_FINGERPRINT

POLICY_CANONICAL_JSON: Final[str] = (
    '{"decision_eligibility":"current-valid-stage2-journal-exact-time-v1",'
    '"decision_note_metadata":"all-canonical-note-and-plausible-journal-errors-excluded-v5",'
    '"diagnostics":"exclusive-phase-mapped-code-sums-v2",'
    '"evidence_cutoff":"exact-aware-inclusive-utc;unknown-excluded-v1",'
    '"execution":"one-provider-free-simulate-me-replay-per-eligible-decision-v1",'
    '"journal_body_cutoff":"updated-after-decision-excluded-v1",'
    '"journal_creation_cutoff":"journal-and-context-created-after-decision-excluded-v2",'
    '"leakage":"mask-choice-reasons-confidence-expectation-outcome-later-context-eligible-only-v2",'
    '"max_decision_cases":"512",'
    '"max_result_bytes":"65536",'
    '"metrics":"bounded-counts-and-exact-ratios-no-confidence-v1",'
    '"option_failure_mapping":"available-options-before-generic-body-v1",'
    '"option_identity":"journal-order-exact-label-request-local-id-v1",'
    '"query_serialization":"utf8-byte-percent-encode-unreserved-v1",'
    '"result_size_guard":"internal-canonical-utf8-byte-length-v1",'
    '"scan_completeness":"content-affecting-diagnostics-abort-before-classification-v3",'
    '"scan_limits":"entries-16384;documents-4096;bytes-16777216-v1",'
    '"self_model_derivation_version":"self-model-derivation-v1",'
    '"self_model_policy_fingerprint":"d7969ba732665c0406736b0e669a9123ccd0ee7b12de36f57899f59f94282cd3",'
    '"self_model_request_limits":"max-claims-200;max-evidence-refs-per-claim-200-v1",'
    '"simulate_me_derivation_version":"simulate-me-v1",'
    '"simulate_me_policy_fingerprint":"sha256:07aa1d0d57fdd2d009087c05423fc4eb9304da70e87f32b1790fbd4753f21c3a",'
    '"simulate_me_policy_id":"simulate-me-direct-exact-v1",'
    '"simulate_me_request_limits":"min-text-bytes-1;query-bytes-4096;label-bytes-256;options-1..8-v1",'
    '"simulate_me_result_limits":"max-result-refs-20;max-note-ids-per-ref-20-v1",'
    '"source_authority":"current-vault-only-no-historical-snapshot-v1",'
    '"storage_metadata":"created-required-updated-optional-never-evidence-time-v2",'
    '"target_extraction":"post-validated-terminal-result-isolated-v1",'
    '"temporal_caveat_counting":"per-eligible-case-independent-codes-v2",'
    '"temporal_caveat_scope":"after-request-validation-context-source-inspection-v1",'
    '"unknown_time":"exclude-and-report-caveat-v1",'
    '"version":"1"}'
)
POLICY_FINGERPRINT: Final[str] = (
    "sha256:6218f228dfcf70c2bf410f5fd39323145681d5683275265ee6be7dd821a55f7f"
)

SCAN_LIMITS_V1: Final[RetrospectiveCalibrationScanLimitsV1] = RetrospectiveCalibrationScanLimitsV1(
    max_scan_entries=MAX_SCAN_ENTRIES_V1,
    max_scan_documents=MAX_SCAN_DOCUMENTS_V1,
    max_scan_bytes=MAX_SCAN_BYTES_V1,
)


class RetrospectiveCalibrationExcludedCodeV1(StrEnum):
    """Fixed eligibility exclusion vocabulary."""

    IDENTITY = "decision_identity_invalid_or_duplicate"
    TIME_UNKNOWN = "decision_time_unknown"
    TIME_INVALID = "decision_time_not_exact_or_invalid"
    CREATED_AFTER_CUTOFF = "decision_body_created_after_cutoff"
    EDITED_AFTER_CUTOFF = "decision_body_edited_after_cutoff"
    METADATA_INVALID = "decision_note_metadata_invalid"
    OPTION_COUNT = "decision_option_count_unsupported"
    OPTION_IDENTITY = "decision_option_identity_invalid"
    BODY_INVALID = "decision_body_invalid"


class RetrospectiveCalibrationReplayUnavailableCodeV1(StrEnum):
    """Fixed unavailable replay vocabulary."""

    CONTEXT_UNAVAILABLE = "prechoice_context_unavailable"
    HISTORICAL_UNRECONSTRUCTABLE = "historical_context_unreconstructable"
    SIMULATE_ME_UNAVAILABLE = "simulate_me_unavailable"


class RetrospectiveCalibrationReplayInvalidCodeV1(StrEnum):
    """Fixed invalid replay vocabulary."""

    REQUEST_INVALID = "prechoice_request_invalid"
    RESULT_INVALID = "simulate_me_result_invalid"
    POLICY_MISMATCH = "simulate_me_policy_mismatch"
    COMPOSITION_INVALID = "calibration_composition_invalid"


class RetrospectiveCalibrationTemporalCaveatCodeV1(StrEnum):
    """Fixed current-vault temporal limitation vocabulary."""

    UNKNOWN_EVIDENCE = "unknown_evidence_excluded"
    LATER_EVIDENCE = "later_evidence_excluded"
    CREATED_AFTER_CUTOFF = "created_after_cutoff_excluded"
    EDITED_AFTER_CUTOFF = "edited_after_cutoff_excluded"
    HISTORICAL_SNAPSHOT_UNAVAILABLE = "historical_snapshot_unavailable"


class RetrospectiveCalibrationErrorCodeV1(StrEnum):
    """Closed top-level error taxonomy."""

    INVALID_REQUEST = "RETROSPECTIVE_CALIBRATION_INVALID_REQUEST"
    CANCELLED = "RETROSPECTIVE_CALIBRATION_CANCELLED"
    SOURCE_UNAVAILABLE = "RETROSPECTIVE_CALIBRATION_SOURCE_UNAVAILABLE"
    TOO_LARGE = "RETROSPECTIVE_CALIBRATION_TOO_LARGE"
    RESULT_TOO_LARGE = "RETROSPECTIVE_CALIBRATION_RESULT_TOO_LARGE"


_ERROR_MESSAGES: Final[dict[RetrospectiveCalibrationErrorCodeV1, str]] = {
    RetrospectiveCalibrationErrorCodeV1.INVALID_REQUEST: (
        "retrospective calibration request failed validation"
    ),
    RetrospectiveCalibrationErrorCodeV1.CANCELLED: "retrospective calibration operation cancelled",
    RetrospectiveCalibrationErrorCodeV1.SOURCE_UNAVAILABLE: (
        "retrospective calibration source unavailable"
    ),
    RetrospectiveCalibrationErrorCodeV1.TOO_LARGE: (
        "retrospective calibration input exceeds its bounded limit"
    ),
    RetrospectiveCalibrationErrorCodeV1.RESULT_TOO_LARGE: (
        "retrospective calibration result exceeds its byte limit"
    ),
}

_EXCLUDED_CODES: Final[tuple[str, ...]] = tuple(
    item.value for item in RetrospectiveCalibrationExcludedCodeV1
)
_REPLAY_UNAVAILABLE_CODES: Final[tuple[str, ...]] = tuple(
    item.value for item in RetrospectiveCalibrationReplayUnavailableCodeV1
)
_REPLAY_INVALID_CODES: Final[tuple[str, ...]] = tuple(
    item.value for item in RetrospectiveCalibrationReplayInvalidCodeV1
)
_TEMPORAL_CAVEAT_CODES: Final[tuple[str, ...]] = tuple(
    item.value for item in RetrospectiveCalibrationTemporalCaveatCodeV1
)

_TIME_UNKNOWN_DIAGNOSTICS: Final[frozenset[str]] = frozenset(
    {
        "PERSONAL_MEMORY_MISSING_EVIDENCE_AT",
    }
)
_TIME_INVALID_DIAGNOSTICS: Final[frozenset[str]] = frozenset(
    {
        "PERSONAL_MEMORY_INVALID_EVIDENCE_AT",
    }
)
_TIME_PRECISION_DIAGNOSTICS: Final[frozenset[str]] = frozenset(
    {
        "PERSONAL_MEMORY_MISSING_EVIDENCE_AT_PRECISION",
        "PERSONAL_MEMORY_INVALID_EVIDENCE_AT_PRECISION",
    }
)
_TIME_PAIR_DIAGNOSTICS: Final[frozenset[str]] = frozenset(
    {
        "PERSONAL_MEMORY_INVALID_EVIDENCE_AT_PAIR",
    }
)
_PERSONAL_MEMORY_DIAGNOSTICS: Final[frozenset[str]] = frozenset(
    {
        "PERSONAL_MEMORY_INVALID_RECORD",
        "PERSONAL_MEMORY_MISSING_EVIDENCE_KIND",
        "PERSONAL_MEMORY_INVALID_EVIDENCE_KIND",
        "PERSONAL_MEMORY_MISSING_SELF_KIND",
        "PERSONAL_MEMORY_INVALID_SELF_KIND",
        "PERSONAL_MEMORY_INVALID_KIND_PAIR",
        "PERSONAL_MEMORY_MISSING_EVIDENCE_AT",
        "PERSONAL_MEMORY_INVALID_EVIDENCE_AT",
        "PERSONAL_MEMORY_MISSING_EVIDENCE_AT_PRECISION",
        "PERSONAL_MEMORY_INVALID_EVIDENCE_AT_PRECISION",
        "PERSONAL_MEMORY_INVALID_EVIDENCE_AT_PAIR",
        "PERSONAL_MEMORY_INVALID_DOMAIN",
    }
)
_NOTE_METADATA_DIAGNOSTICS: Final[frozenset[str]] = frozenset(
    {
        "NOTE_MISSING_TYPE",
        "NOTE_INVALID_TYPE",
        "NOTE_MISSING_TIMESTAMP",
        "NOTE_INVALID_TIMESTAMP",
        "NOTE_INVALID_TAGS",
        "NOTE_INVALID_SOURCES",
        "NOTE_INVALID_SOURCE_COUNT",
        "NOTE_INVALID_SOURCE_RECORD",
        "NOTE_SOURCE_MISSING_URI",
        "NOTE_SOURCE_MISSING_KIND",
        "NOTE_SOURCE_MISSING_RETRIEVED_AT",
        "NOTE_SOURCE_INVALID_KIND",
        "NOTE_SOURCE_INVALID_RETRIEVED_AT",
        "NOTE_SOURCE_INVALID_PUBLISHED_AT",
        "NOTE_SOURCE_INVALID_URI",
        "NOTE_SOURCE_INVALID_METADATA",
        "NOTE_INVALID_SOURCE",
    }
)
_IDENTITY_DIAGNOSTICS: Final[frozenset[str]] = frozenset(
    {
        "NOTE_MISSING_ID",
        "NOTE_INVALID_ID",
        "DUPLICATE_NOTE_ID",
    }
)
_SCAN_COMPLETENESS_CODES: Final[frozenset[str]] = frozenset(
    {
        "NOTE_READ_ERROR",
        "NOTE_FRONT_MATTER_ERROR",
        "VAULT_DIRECTORY_READ_ERROR",
        "VAULT_ENTRY_RESOLVE_ERROR",
        "VAULT_LINKED_DIRECTORY",
        "VAULT_LINKED_ENTRY",
        "VAULT_OVERLAPPING_ROOTS",
        "VAULT_PATH_ESCAPE",
        "VAULT_ROOT_MISSING",
        "VAULT_ROOT_NOT_DIRECTORY",
    }
)
_VALID_NON_JOURNAL_PAIRS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        (evidence_kind, self_kind)
        for evidence_kind in ("explicit_user_fact", "user_statement")
        for self_kind in ("memory", "preference", "belief", "goal")
    }
    | {("outcome_later_observation", "outcome")}
)
_UNRESERVED = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


@dataclass(frozen=True, slots=True)
class RetrospectiveCalibrationRequestV1:
    """Empty configuration object: v1 policy is never caller-configurable."""


@dataclass(frozen=True, slots=True)
class RetrospectiveCalibrationContextInputV1:
    """Current direct-assertion source pack with Journal data excluded."""

    manifest: VaultManifest
    notes: tuple[NoteRecord, ...]


@dataclass(frozen=True, slots=True)
class RetrospectiveCalibrationRatioV1:
    """Exact integer ratio; no floating-point representation is exposed."""

    numerator: int
    denominator: int


@dataclass(frozen=True, slots=True)
class RetrospectiveCalibrationCountV1:
    """One fixed-code counter in the canonical aggregate."""

    code: str
    count: int


@dataclass(frozen=True, slots=True)
class RetrospectiveCalibrationMetricsV1:
    """Bounded aggregate metrics without confidence or probability fields."""

    decision_notes_seen: int
    eligible_decisions: int
    predicted_decisions: int
    abstentions: int
    exact_option_match_count: int
    mismatch_count: int
    unavailable_count: int
    invalid_count: int
    coverage: RetrospectiveCalibrationRatioV1 | None
    accuracy_non_abstained: RetrospectiveCalibrationRatioV1 | None


@dataclass(frozen=True, slots=True)
class RetrospectiveCalibrationResultV1:
    """Fixed-shape, privacy-safe, rebuildable calibration aggregate."""

    derivation_version: str
    policy_id: str
    policy_fingerprint: str
    reconstruction_mode: str
    metrics: RetrospectiveCalibrationMetricsV1
    excluded_decisions: tuple[RetrospectiveCalibrationCountV1, ...]
    replay_unavailable: tuple[RetrospectiveCalibrationCountV1, ...]
    replay_invalid: tuple[RetrospectiveCalibrationCountV1, ...]
    temporal_caveats: tuple[RetrospectiveCalibrationCountV1, ...]


class RetrospectiveCalibrationError(RuntimeError):
    """Safe top-level error containing only a fixed code and message."""

    def __init__(self, code: RetrospectiveCalibrationErrorCodeV1 | str) -> None:
        normalized = _normalize_error_code(code)
        self.code = normalized.value
        self.message = _ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Return the only public projection of a failed operation."""

        return {"code": self.code, "message": self.message}


class RetrospectiveCalibrationInvalidRequestError(RetrospectiveCalibrationError):
    """The immutable operation request is not the exact v1 DTO."""

    def __init__(self) -> None:
        super().__init__(RetrospectiveCalibrationErrorCodeV1.INVALID_REQUEST)


class RetrospectiveCalibrationCancelledError(RetrospectiveCalibrationError):
    """Global cancellation discards all partial counters."""

    def __init__(self) -> None:
        super().__init__(RetrospectiveCalibrationErrorCodeV1.CANCELLED)


class RetrospectiveCalibrationSourceUnavailableError(RetrospectiveCalibrationError):
    """The bounded source or completeness gate is not trustworthy."""

    def __init__(self) -> None:
        super().__init__(RetrospectiveCalibrationErrorCodeV1.SOURCE_UNAVAILABLE)


class RetrospectiveCalibrationTooLargeError(RetrospectiveCalibrationError):
    """Input crossed a scan or classified-case bound."""

    def __init__(self) -> None:
        super().__init__(RetrospectiveCalibrationErrorCodeV1.TOO_LARGE)


class RetrospectiveCalibrationResultTooLargeError(RetrospectiveCalibrationError):
    """The complete canonical aggregate crossed the byte bound."""

    def __init__(self) -> None:
        super().__init__(RetrospectiveCalibrationErrorCodeV1.RESULT_TOO_LARGE)


class RetrospectiveCalibrationContextUnavailableError(RuntimeError):
    """Internal safe signal for a failed filtered current context boundary."""


class RetrospectiveCalibrationHistoricalContextError(RuntimeError):
    """Internal signal that true historical bytes/edit history are required."""


class RetrospectiveCalibrationReplayUnavailableError(RuntimeError):
    """Internal safe signal for an unavailable single replay attempt."""


class RetrospectiveCalibrationReplayCancelledError(RuntimeError):
    """Internal safe signal for a case-local replay cancellation."""


class RetrospectiveCalibrationScanPort(Protocol):
    """Only approved bounded scanner boundary for the use case."""

    def scan(self, limits: RetrospectiveCalibrationScanLimitsV1) -> RetrospectiveCalibrationScanV1:
        """Inspect current roots without materializing beyond the limits."""


class RetrospectiveCalibrationContextPort(Protocol):
    """Build current typed Self Model from Journal-free source input."""

    def build(
        self,
        *,
        context: RetrospectiveCalibrationContextInputV1,
        decision_at: datetime,
        request: SelfModelRequest,
    ) -> SelfModelResult:
        """Return one current result for later per-case filtering."""


class RetrospectiveCalibrationReplayPort(Protocol):
    """One approved provider-free Stage 6 attempt over filtered context."""

    def execute(
        self,
        request: SimulateMeRequest,
        *,
        context: SelfModelResult,
    ) -> SimulateMeResult:
        """Return one terminal Stage 6 DTO without retry or fallback."""


@dataclass(frozen=True, slots=True)
class _Candidate:
    note: NoteRecord
    diagnostic_codes: frozenset[str]


@dataclass(frozen=True, slots=True)
class _EligibleCase:
    note: NoteRecord
    journal: DecisionJournalRecord
    decision_at: datetime


@dataclass(slots=True)
class _MutableMetrics:
    """Private fixed terminal tallies assembled before DTO construction."""

    predicted_decisions: int = 0
    abstentions: int = 0
    exact_option_match_count: int = 0
    mismatch_count: int = 0


@dataclass(slots=True)
class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


class _CancellationLike(Protocol):
    def is_cancelled(self) -> bool:
        """Return the current cancellation state."""


class _ReportSnapshotReader:
    """Read-only in-memory adapter used by the concrete context seam."""

    def __init__(self, snapshot: VaultSnapshot) -> None:
        self._snapshot = snapshot

    def scan(self) -> VaultSnapshot:
        return self._snapshot


class BuildRetrospectiveCalibrationContext:
    """Reuse Stage 4 validation over a Journal-free current source pack."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or _utc_now

    def build(
        self,
        *,
        context: RetrospectiveCalibrationContextInputV1,
        decision_at: datetime,
        request: SelfModelRequest,
    ) -> SelfModelResult:
        del decision_at
        try:
            validate_self_model_request(request)
            if (
                request != SELF_MODEL_REQUEST_V1
                or type(context) is not RetrospectiveCalibrationContextInputV1
            ):
                raise RetrospectiveCalibrationContextUnavailableError()
            if type(context.manifest) is not VaultManifest:
                raise RetrospectiveCalibrationContextUnavailableError()
            documents = tuple(
                MarkdownDocument(
                    relative_path=note.relative_path,
                    front_matter=dict(note.front_matter),
                    body=note.body,
                    in_inbox=_is_inbox_note(note.relative_path, context.manifest),
                )
                for note in context.notes
            )
            snapshot = VaultSnapshot(
                vault_path="<retrospective-calibration-context>",
                manifest=context.manifest,
                documents=documents,
            )
            return BuildSelfModel(
                _ReportSnapshotReader(snapshot),
                policy=DEFAULT_SELF_MODEL_POLICY,
                clock=self._clock,
            ).execute(request)
        except RetrospectiveCalibrationContextUnavailableError:
            raise
        except Exception:
            raise RetrospectiveCalibrationContextUnavailableError() from None


@dataclass(frozen=True, slots=True)
class _StaticSelfModel:
    result: SelfModelResult

    def execute(self, request: SelfModelRequest) -> SelfModelResult:
        if request != SELF_MODEL_REQUEST_V1:
            raise ValueError("unexpected self model request")
        return self.result


class BuildRetrospectiveCalibrationReplay:
    """Run existing exact Stage 6 mechanics against one filtered result."""

    def execute(
        self,
        request: SimulateMeRequest,
        *,
        context: SelfModelResult,
    ) -> SimulateMeResult:
        return BuildSimulateMe(self_model=_StaticSelfModel(context)).execute(request)


class BuildRetrospectiveCalibration:
    """Build one complete deterministic calibration aggregate in memory."""

    def __init__(
        self,
        scanner: RetrospectiveCalibrationScanPort,
        context: RetrospectiveCalibrationContextPort,
        replay: RetrospectiveCalibrationReplayPort,
    ) -> None:
        self._scanner = scanner
        self._context = context
        self._replay = replay

    def execute(
        self,
        request: RetrospectiveCalibrationRequestV1,
        *,
        cancellation: object | None = None,
    ) -> RetrospectiveCalibrationResultV1:
        try:
            _validate_request(request)
        except Exception:
            raise RetrospectiveCalibrationInvalidRequestError() from None

        token = cancellation if cancellation is not None else _NeverCancelled()
        if _is_cancelled(token):
            raise RetrospectiveCalibrationCancelledError()

        scan = self._read_bounded_scan()
        if _is_cancelled(token):
            raise RetrospectiveCalibrationCancelledError()
        report = self._build_complete_report(scan)
        if _is_cancelled(token):
            raise RetrospectiveCalibrationCancelledError()

        candidates = _candidate_notes(report)
        if len(candidates) > MAX_DECISION_CASES_V1:
            raise RetrospectiveCalibrationTooLargeError()
        duplicate_ids = _duplicate_note_ids(report)
        context_input = _context_input(report)

        excluded = _zero_counts(_EXCLUDED_CODES)
        unavailable = _zero_counts(_REPLAY_UNAVAILABLE_CODES)
        invalid = _zero_counts(_REPLAY_INVALID_CODES)
        temporal = _zero_counts(_TEMPORAL_CAVEAT_CODES)
        metrics = _MutableMetrics()
        eligible_cases: list[_EligibleCase] = []

        for candidate in candidates:
            if _is_cancelled(token):
                raise RetrospectiveCalibrationCancelledError()
            classification = _classify_candidate(candidate, duplicate_ids)
            if isinstance(classification, str):
                excluded[classification] += 1
            else:
                eligible_cases.append(classification)

        eligible_cases.sort(key=_eligible_sort_key)
        for case in eligible_cases:
            if _is_cancelled(token):
                raise RetrospectiveCalibrationCancelledError()
            self._process_case(
                case,
                context_input,
                excluded=excluded,
                unavailable=unavailable,
                invalid=invalid,
                temporal=temporal,
                metrics=metrics,
                cancellation=token,
            )

        result = _build_result(
            decision_notes_seen=len(candidates),
            eligible_decisions=len(eligible_cases),
            excluded=excluded,
            unavailable=unavailable,
            invalid=invalid,
            temporal=temporal,
            metrics=metrics,
        )
        if _is_cancelled(token):
            raise RetrospectiveCalibrationCancelledError()
        try:
            serialize_retrospective_calibration_result(result)
        except RetrospectiveCalibrationResultTooLargeError:
            raise
        except Exception:
            raise RetrospectiveCalibrationSourceUnavailableError() from None
        return result

    def _read_bounded_scan(self) -> RetrospectiveCalibrationScanV1:
        try:
            scan = self._scanner.scan(SCAN_LIMITS_V1)
        except RetrospectiveCalibrationTooLargeError:
            raise
        except Exception:
            raise RetrospectiveCalibrationSourceUnavailableError() from None
        if type(scan) is not RetrospectiveCalibrationScanV1:
            raise RetrospectiveCalibrationSourceUnavailableError()
        if type(scan.limit_exceeded) is not bool:
            raise RetrospectiveCalibrationSourceUnavailableError()
        if scan.limit_exceeded:
            raise RetrospectiveCalibrationTooLargeError()
        if (
            not _valid_scan_counter(scan.entries_inspected)
            or not _valid_scan_counter(scan.documents_materialized)
            or not _valid_scan_counter(scan.raw_utf8_bytes)
        ):
            raise RetrospectiveCalibrationSourceUnavailableError()
        if (
            scan.entries_inspected > MAX_SCAN_ENTRIES_V1
            or scan.documents_materialized > MAX_SCAN_DOCUMENTS_V1
            or scan.raw_utf8_bytes > MAX_SCAN_BYTES_V1
        ):
            raise RetrospectiveCalibrationTooLargeError()
        return scan

    @staticmethod
    def _build_complete_report(scan: RetrospectiveCalibrationScanV1) -> ScanReport:
        if type(scan.snapshot) is not VaultSnapshot:
            raise RetrospectiveCalibrationSourceUnavailableError()
        try:
            report = build_report(scan.snapshot)
        except Exception:
            raise RetrospectiveCalibrationSourceUnavailableError() from None
        if type(report) is not ScanReport or not _scan_is_complete(report):
            raise RetrospectiveCalibrationSourceUnavailableError()
        return report

    def _process_case(
        self,
        case: _EligibleCase,
        context_input: RetrospectiveCalibrationContextInputV1,
        *,
        excluded: dict[str, int],
        unavailable: dict[str, int],
        invalid: dict[str, int],
        temporal: dict[str, int],
        metrics: _MutableMetrics,
        cancellation: object,
    ) -> None:
        del excluded
        request = _build_simulate_me_request(case.journal)
        if request is None:
            invalid[RetrospectiveCalibrationReplayInvalidCodeV1.REQUEST_INVALID.value] += 1
            return

        temporal[
            RetrospectiveCalibrationTemporalCaveatCodeV1.HISTORICAL_SNAPSHOT_UNAVAILABLE.value
        ] += 1
        if _is_cancelled(cancellation):
            raise RetrospectiveCalibrationCancelledError()
        try:
            context_result = self._context.build(
                context=context_input,
                decision_at=case.decision_at,
                request=SELF_MODEL_REQUEST_V1,
            )
            filtered_context, flags = _filter_context(
                context_result,
                context_input,
                case.decision_at,
            )
            for code in flags:
                temporal[code] += 1
        except RetrospectiveCalibrationHistoricalContextError:
            unavailable[
                RetrospectiveCalibrationReplayUnavailableCodeV1.HISTORICAL_UNRECONSTRUCTABLE.value
            ] += 1
            return
        except Exception:
            unavailable[
                RetrospectiveCalibrationReplayUnavailableCodeV1.CONTEXT_UNAVAILABLE.value
            ] += 1
            return

        if _is_cancelled(cancellation):
            raise RetrospectiveCalibrationCancelledError()
        try:
            replay_result = self._replay.execute(request, context=filtered_context)
        except RetrospectiveCalibrationReplayCancelledError:
            if _is_cancelled(cancellation):
                raise RetrospectiveCalibrationCancelledError() from None
            unavailable[
                RetrospectiveCalibrationReplayUnavailableCodeV1.SIMULATE_ME_UNAVAILABLE.value
            ] += 1
            return
        except (
            RetrospectiveCalibrationReplayUnavailableError,
            TimeoutError,
            SimulateMeError,
        ):
            unavailable[
                RetrospectiveCalibrationReplayUnavailableCodeV1.SIMULATE_ME_UNAVAILABLE.value
            ] += 1
            return
        except Exception:
            unavailable[
                RetrospectiveCalibrationReplayUnavailableCodeV1.SIMULATE_ME_UNAVAILABLE.value
            ] += 1
            return

        if _is_cancelled(cancellation):
            raise RetrospectiveCalibrationCancelledError()
        if _stage6_policy_mismatch(replay_result):
            invalid[RetrospectiveCalibrationReplayInvalidCodeV1.POLICY_MISMATCH.value] += 1
            return
        try:
            validated = validate_simulate_me_result(replay_result, request=request)
        except Exception:
            invalid[RetrospectiveCalibrationReplayInvalidCodeV1.RESULT_INVALID.value] += 1
            return
        if not _stage6_composition_is_safe(validated, filtered_context):
            invalid[RetrospectiveCalibrationReplayInvalidCodeV1.COMPOSITION_INVALID.value] += 1
            return

        if validated.kind is SimulateMeResultKind.ABSTENTION:
            assert validated.abstention_code is not None
            if (
                validated.abstention_code
                is SimulateMeAbstentionCode.INSUFFICIENT_OR_INVALID_CURRENT_CONTEXT
            ):
                unavailable[
                    RetrospectiveCalibrationReplayUnavailableCodeV1.CONTEXT_UNAVAILABLE.value
                ] += 1
            else:
                # Target extraction is intentionally absent on this branch.
                metrics.abstentions += 1
            return

        # The target request-local ID is materialized only after this validated
        # terminal prediction has arrived.
        target_id = _target_option_id(request, case.journal.chosen_option)
        if target_id is None:
            invalid[RetrospectiveCalibrationReplayInvalidCodeV1.COMPOSITION_INVALID.value] += 1
            return
        if validated.selected_option is None:
            invalid[RetrospectiveCalibrationReplayInvalidCodeV1.COMPOSITION_INVALID.value] += 1
            return
        metrics.predicted_decisions += 1
        if validated.selected_option.id == target_id:
            metrics.exact_option_match_count += 1
        else:
            metrics.mismatch_count += 1


def _validate_request(request: object) -> RetrospectiveCalibrationRequestV1:
    if type(request) is not RetrospectiveCalibrationRequestV1:
        raise RetrospectiveCalibrationInvalidRequestError()
    return request


def _normalize_error_code(
    code: RetrospectiveCalibrationErrorCodeV1 | str,
) -> RetrospectiveCalibrationErrorCodeV1:
    if isinstance(code, RetrospectiveCalibrationErrorCodeV1):
        return code
    try:
        return RetrospectiveCalibrationErrorCodeV1(code)
    except TypeError, ValueError:
        return RetrospectiveCalibrationErrorCodeV1.SOURCE_UNAVAILABLE


def validate_retrospective_calibration_policy() -> str:
    """Recompute the exact approved policy fingerprint."""

    try:
        digest = hashlib.sha256(POLICY_CANONICAL_JSON.encode("utf-8")).hexdigest()
        if f"sha256:{digest}" != POLICY_FINGERPRINT:
            raise ValueError("calibration policy fingerprint mismatch")
        if (
            DEFAULT_SELF_MODEL_POLICY.derivation_version != SELF_MODEL_DERIVATION_VERSION
            or validate_self_model_policy(DEFAULT_SELF_MODEL_POLICY)
            != SELF_MODEL_POLICY_FINGERPRINT
            or SIMULATE_ME_DERIVATION_VERSION_V1 != "simulate-me-v1"
            or SIMULATE_ME_POLICY_ID_V1 != "simulate-me-direct-exact-v1"
            or validate_simulate_me_policy() != SIMULATE_ME_POLICY_FINGERPRINT_V1
            or SIMULATE_ME_POLICY_FINGERPRINT_V1
            != "sha256:07aa1d0d57fdd2d009087c05423fc4eb9304da70e87f32b1790fbd4753f21c3a"
        ):
            raise ValueError("stage policy identity mismatch")
    except Exception:
        raise RetrospectiveCalibrationSourceUnavailableError() from None
    return POLICY_FINGERPRINT


def validate_retrospective_calibration_result(
    result: object,
) -> RetrospectiveCalibrationResultV1:
    """Validate exact DTO shape, fixed arrays, ratios, and all count equations."""

    if type(result) is not RetrospectiveCalibrationResultV1:
        raise ValueError("result is not the exact calibration DTO")
    if (
        result.derivation_version != DERIVATION_VERSION
        or result.policy_id != POLICY_ID
        or result.policy_fingerprint != POLICY_FINGERPRINT
        or result.reconstruction_mode != RECONSTRUCTION_MODE
        or type(result.metrics) is not RetrospectiveCalibrationMetricsV1
    ):
        raise ValueError("result identity is invalid")
    validate_retrospective_calibration_policy()
    metrics = result.metrics
    metric_values = (
        metrics.decision_notes_seen,
        metrics.eligible_decisions,
        metrics.predicted_decisions,
        metrics.abstentions,
        metrics.exact_option_match_count,
        metrics.mismatch_count,
        metrics.unavailable_count,
        metrics.invalid_count,
    )
    if any(not _valid_non_negative_int(value) for value in metric_values):
        raise ValueError("metric count is invalid")
    _validate_count_array(result.excluded_decisions, _EXCLUDED_CODES)
    _validate_count_array(result.replay_unavailable, _REPLAY_UNAVAILABLE_CODES)
    _validate_count_array(result.replay_invalid, _REPLAY_INVALID_CODES)
    _validate_count_array(result.temporal_caveats, _TEMPORAL_CAVEAT_CODES)
    excluded_total = sum(item.count for item in result.excluded_decisions)
    unavailable_total = sum(item.count for item in result.replay_unavailable)
    invalid_total = sum(item.count for item in result.replay_invalid)
    if (
        metrics.decision_notes_seen != excluded_total + metrics.eligible_decisions
        or metrics.eligible_decisions
        != metrics.predicted_decisions
        + metrics.abstentions
        + metrics.unavailable_count
        + metrics.invalid_count
        or metrics.predicted_decisions != metrics.exact_option_match_count + metrics.mismatch_count
        or metrics.unavailable_count != unavailable_total
        or metrics.invalid_count != invalid_total
    ):
        raise ValueError("metric count invariant is invalid")
    _validate_ratio(
        metrics.coverage,
        numerator=metrics.predicted_decisions,
        denominator=metrics.eligible_decisions,
        nullable=metrics.eligible_decisions == 0,
    )
    _validate_ratio(
        metrics.accuracy_non_abstained,
        numerator=metrics.exact_option_match_count,
        denominator=metrics.predicted_decisions,
        nullable=metrics.predicted_decisions == 0,
    )
    return result


def serialize_retrospective_calibration_result(
    result: RetrospectiveCalibrationResultV1,
) -> bytes:
    """Serialize the result as canonical UTF-8 JSON with fixed key order."""

    validated = validate_retrospective_calibration_result(result)
    payload = {
        "derivation_version": validated.derivation_version,
        "policy_id": validated.policy_id,
        "policy_fingerprint": validated.policy_fingerprint,
        "reconstruction_mode": validated.reconstruction_mode,
        "metrics": _metrics_dict(validated.metrics),
        "excluded_decisions": _counts_dict(validated.excluded_decisions),
        "replay_unavailable": _counts_dict(validated.replay_unavailable),
        "replay_invalid": _counts_dict(validated.replay_invalid),
        "temporal_caveats": _counts_dict(validated.temporal_caveats),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES_V1:
        raise RetrospectiveCalibrationResultTooLargeError()
    return encoded


def _metrics_dict(metrics: RetrospectiveCalibrationMetricsV1) -> dict[str, object]:
    return {
        "decision_notes_seen": metrics.decision_notes_seen,
        "eligible_decisions": metrics.eligible_decisions,
        "predicted_decisions": metrics.predicted_decisions,
        "abstentions": metrics.abstentions,
        "exact_option_match_count": metrics.exact_option_match_count,
        "mismatch_count": metrics.mismatch_count,
        "unavailable_count": metrics.unavailable_count,
        "invalid_count": metrics.invalid_count,
        "coverage": _ratio_dict(metrics.coverage),
        "accuracy_non_abstained": _ratio_dict(metrics.accuracy_non_abstained),
    }


def _ratio_dict(
    ratio: RetrospectiveCalibrationRatioV1 | None,
) -> dict[str, int] | None:
    if ratio is None:
        return None
    return {"numerator": ratio.numerator, "denominator": ratio.denominator}


def _counts_dict(
    counts: tuple[RetrospectiveCalibrationCountV1, ...],
) -> list[dict[str, int | str]]:
    return [{"code": item.code, "count": item.count} for item in counts]


def _validate_count_array(
    counts: object,
    expected_codes: tuple[str, ...],
) -> None:
    if type(counts) is not tuple or len(counts) != len(expected_codes):
        raise ValueError("fixed count array is invalid")
    for item, expected in zip(counts, expected_codes, strict=True):
        if (
            type(item) is not RetrospectiveCalibrationCountV1
            or item.code != expected
            or not _valid_non_negative_int(item.count)
        ):
            raise ValueError("fixed count item is invalid")


def _validate_ratio(
    ratio: RetrospectiveCalibrationRatioV1 | None,
    *,
    numerator: int,
    denominator: int,
    nullable: bool,
) -> None:
    if nullable:
        if ratio is not None:
            raise ValueError("nullable ratio must be null")
        return
    if (
        type(ratio) is not RetrospectiveCalibrationRatioV1
        or ratio.numerator != numerator
        or ratio.denominator != denominator
        or not _valid_non_negative_int(ratio.numerator)
        or type(ratio.denominator) is not int
        or ratio.denominator <= 0
    ):
        raise ValueError("ratio is invalid")


def _build_result(
    *,
    decision_notes_seen: int,
    eligible_decisions: int,
    excluded: dict[str, int],
    unavailable: dict[str, int],
    invalid: dict[str, int],
    temporal: dict[str, int],
    metrics: _MutableMetrics,
) -> RetrospectiveCalibrationResultV1:
    unavailable_count = sum(unavailable.values())
    invalid_count = sum(invalid.values())
    tallies = metrics
    metric_dto = RetrospectiveCalibrationMetricsV1(
        decision_notes_seen=decision_notes_seen,
        eligible_decisions=eligible_decisions,
        predicted_decisions=tallies.predicted_decisions,
        abstentions=tallies.abstentions,
        exact_option_match_count=tallies.exact_option_match_count,
        mismatch_count=tallies.mismatch_count,
        unavailable_count=unavailable_count,
        invalid_count=invalid_count,
        coverage=(
            RetrospectiveCalibrationRatioV1(
                tallies.predicted_decisions,
                eligible_decisions,
            )
            if eligible_decisions
            else None
        ),
        accuracy_non_abstained=(
            RetrospectiveCalibrationRatioV1(
                tallies.exact_option_match_count,
                tallies.predicted_decisions,
            )
            if tallies.predicted_decisions
            else None
        ),
    )
    result = RetrospectiveCalibrationResultV1(
        derivation_version=DERIVATION_VERSION,
        policy_id=POLICY_ID,
        policy_fingerprint=POLICY_FINGERPRINT,
        reconstruction_mode=RECONSTRUCTION_MODE,
        metrics=metric_dto,
        excluded_decisions=_counts_from(excluded, _EXCLUDED_CODES),
        replay_unavailable=_counts_from(unavailable, _REPLAY_UNAVAILABLE_CODES),
        replay_invalid=_counts_from(invalid, _REPLAY_INVALID_CODES),
        temporal_caveats=_counts_from(temporal, _TEMPORAL_CAVEAT_CODES),
    )
    try:
        validate_retrospective_calibration_result(result)
    except Exception:
        raise RetrospectiveCalibrationSourceUnavailableError() from None
    return result


def _counts_from(
    counters: dict[str, int],
    codes: tuple[str, ...],
) -> tuple[RetrospectiveCalibrationCountV1, ...]:
    return tuple(RetrospectiveCalibrationCountV1(code, counters.get(code, 0)) for code in codes)


def _zero_counts(codes: tuple[str, ...]) -> dict[str, int]:
    return {code: 0 for code in codes}


def _scan_is_complete(report: ScanReport) -> bool:
    if report.manifest is None:
        return False
    if any(
        diagnostic.severity is DiagnosticSeverity.ERROR and diagnostic.code.startswith("MANIFEST_")
        for diagnostic in report.diagnostics
    ):
        return False
    if not report.content_scan_complete:
        return False
    return not any(
        diagnostic.code in _SCAN_COMPLETENESS_CODES and diagnostic_affects_content(diagnostic)
        for diagnostic in report.diagnostics
    )


def _candidate_notes(report: ScanReport) -> tuple[_Candidate, ...]:
    diagnostics_by_path: dict[str, set[str]] = {}
    for diagnostic in report.diagnostics:
        if diagnostic.path is not None:
            diagnostics_by_path.setdefault(diagnostic.path, set()).add(diagnostic.code)
    candidates: list[_Candidate] = []
    for note in report.notes:
        if note.managed is not True or not is_personal_memory_enrolled(note.front_matter):
            continue
        codes = frozenset(diagnostics_by_path.get(note.relative_path, set()))
        raw_evidence_kind = note.front_matter.get("evidence_kind")
        raw_self_kind = note.front_matter.get("self_kind")
        journal_pair = (
            type(raw_evidence_kind) is str
            and type(raw_self_kind) is str
            and raw_evidence_kind == EvidenceKind.OBSERVED_DECISION.value
            and raw_self_kind == SelfKind.DECISION.value
        )
        valid_non_journal = (
            type(raw_evidence_kind) is str
            and type(raw_self_kind) is str
            and (raw_evidence_kind, raw_self_kind) in _VALID_NON_JOURNAL_PAIRS
        )
        plausible = journal_pair or not valid_non_journal
        has_pm_diagnostic = bool(_PERSONAL_MEMORY_DIAGNOSTICS.intersection(codes))
        if plausible and (journal_pair or has_pm_diagnostic):
            candidates.append(_Candidate(note, codes))
    return tuple(sorted(candidates, key=_candidate_sort_key))


def _candidate_sort_key(candidate: _Candidate) -> tuple[str, str]:
    note = candidate.note
    note_id = str(note.note_id).lower() if isinstance(note.note_id, UUID) else ""
    return note_id, note.relative_path


def _duplicate_note_ids(report: ScanReport) -> frozenset[UUID]:
    counts: dict[UUID, int] = {}
    for note in report.notes:
        if type(note.note_id) is UUID and note.note_id.version == 7:
            counts[note.note_id] = counts.get(note.note_id, 0) + 1
    return frozenset(note_id for note_id, count in counts.items() if count > 1)


def _classify_candidate(
    candidate: _Candidate,
    duplicate_ids: frozenset[UUID],
) -> _EligibleCase | str:
    note = candidate.note
    codes = candidate.diagnostic_codes
    if (
        type(note.note_id) is not UUID
        or note.note_id.version != 7
        or not _safe_relative_path(note.relative_path)
        or note.note_id in duplicate_ids
        or bool(_IDENTITY_DIAGNOSTICS.intersection(codes))
    ):
        return RetrospectiveCalibrationExcludedCodeV1.IDENTITY.value

    decision_at, time_code = _decision_time(note, codes)
    if time_code == RetrospectiveCalibrationExcludedCodeV1.TIME_UNKNOWN.value:
        return time_code
    if (
        time_code == RetrospectiveCalibrationExcludedCodeV1.TIME_INVALID.value
        or decision_at is None
    ):
        return RetrospectiveCalibrationExcludedCodeV1.TIME_INVALID.value
    decision_utc = decision_at.astimezone(UTC)

    storage_code = _storage_cutoff_code(note, decision_utc)
    if storage_code is not None:
        return storage_code
    if (
        bool(_NOTE_METADATA_DIAGNOSTICS.intersection(codes))
        or _has_non_time_personal_memory_diagnostic(codes)
        or not _valid_journal_metadata(note)
    ):
        return RetrospectiveCalibrationExcludedCodeV1.METADATA_INVALID.value

    try:
        journal = parse_decision_journal_body(note.body)
    except DecisionJournalBodyError as error:
        if error.reason in {"list_too_few", "list_too_many"}:
            options_reason = _available_options_reason(note.body)
            if options_reason == error.reason:
                return RetrospectiveCalibrationExcludedCodeV1.OPTION_COUNT.value
        if error.reason == "chosen_missing" or (
            error.reason == "list_duplicate"
            and _available_options_reason(note.body) == "list_duplicate"
        ):
            return RetrospectiveCalibrationExcludedCodeV1.OPTION_IDENTITY.value
        return RetrospectiveCalibrationExcludedCodeV1.BODY_INVALID.value
    if not MIN_JOURNAL_OPTIONS <= len(journal.available_options) <= 8:
        return RetrospectiveCalibrationExcludedCodeV1.OPTION_COUNT.value
    if len(_matching_option_indexes(journal)) != 1:
        return RetrospectiveCalibrationExcludedCodeV1.OPTION_IDENTITY.value
    return _EligibleCase(note, journal, decision_utc)


def _decision_time(
    note: NoteRecord,
    codes: frozenset[str],
) -> tuple[datetime | None, str | None]:
    raw = note.front_matter
    raw_evidence_at = raw.get("evidence_at")
    if "evidence_at" not in raw or _TIME_UNKNOWN_DIAGNOSTICS.intersection(codes):
        return None, RetrospectiveCalibrationExcludedCodeV1.TIME_UNKNOWN.value
    if _TIME_INVALID_DIAGNOSTICS.intersection(codes):
        return None, RetrospectiveCalibrationExcludedCodeV1.TIME_INVALID.value
    if _TIME_PRECISION_DIAGNOSTICS.intersection(codes):
        if raw_evidence_at == PERSONAL_MEMORY_UNKNOWN_TIME:
            return None, RetrospectiveCalibrationExcludedCodeV1.TIME_UNKNOWN.value
        return None, RetrospectiveCalibrationExcludedCodeV1.TIME_INVALID.value
    if _TIME_PAIR_DIAGNOSTICS.intersection(codes):
        if raw_evidence_at == PERSONAL_MEMORY_UNKNOWN_TIME:
            return None, RetrospectiveCalibrationExcludedCodeV1.TIME_UNKNOWN.value
        return None, RetrospectiveCalibrationExcludedCodeV1.TIME_INVALID.value
    if raw_evidence_at == PERSONAL_MEMORY_UNKNOWN_TIME:
        return None, RetrospectiveCalibrationExcludedCodeV1.TIME_UNKNOWN.value
    metadata = note.personal_memory
    if (
        type(metadata) is PersonalMemoryMetadata
        and isinstance(metadata.evidence_at, datetime)
        and metadata.evidence_at_precision is EvidenceAtPrecision.EXACT
        and _aware(metadata.evidence_at)
    ):
        return metadata.evidence_at, None
    if raw.get("evidence_at_precision") != EvidenceAtPrecision.EXACT.value:
        return None, RetrospectiveCalibrationExcludedCodeV1.TIME_INVALID.value
    try:
        parsed = parse_rfc3339(raw_evidence_at)
    except TypeError, ValueError, OverflowError:
        return None, RetrospectiveCalibrationExcludedCodeV1.TIME_INVALID.value
    return parsed, None


def _has_non_time_personal_memory_diagnostic(codes: frozenset[str]) -> bool:
    time_codes = (
        _TIME_UNKNOWN_DIAGNOSTICS
        | _TIME_INVALID_DIAGNOSTICS
        | _TIME_PRECISION_DIAGNOSTICS
        | _TIME_PAIR_DIAGNOSTICS
    )
    return bool((_PERSONAL_MEMORY_DIAGNOSTICS - time_codes).intersection(codes))


def _storage_cutoff_code(note: NoteRecord, decision_at: datetime) -> str | None:
    raw = note.front_matter
    if "created" not in raw:
        return RetrospectiveCalibrationExcludedCodeV1.METADATA_INVALID.value
    try:
        created = parse_rfc3339(raw["created"])
    except TypeError, ValueError, OverflowError:
        return RetrospectiveCalibrationExcludedCodeV1.METADATA_INVALID.value
    if not _same_instant(note.created, created):
        return RetrospectiveCalibrationExcludedCodeV1.METADATA_INVALID.value
    if created.astimezone(UTC) > decision_at:
        return RetrospectiveCalibrationExcludedCodeV1.CREATED_AFTER_CUTOFF.value
    if "updated" not in raw:
        if note.updated is not None:
            return RetrospectiveCalibrationExcludedCodeV1.METADATA_INVALID.value
        return None
    try:
        updated = parse_rfc3339(raw["updated"])
    except TypeError, ValueError, OverflowError:
        return RetrospectiveCalibrationExcludedCodeV1.METADATA_INVALID.value
    if not _same_instant(note.updated, updated):
        return RetrospectiveCalibrationExcludedCodeV1.METADATA_INVALID.value
    if updated.astimezone(UTC) > decision_at:
        return RetrospectiveCalibrationExcludedCodeV1.EDITED_AFTER_CUTOFF.value
    return None


def _valid_journal_metadata(note: NoteRecord) -> bool:
    metadata = note.personal_memory
    if not (
        type(note.note_type) is NoteType
        and type(metadata) is PersonalMemoryMetadata
        and metadata.evidence_kind is EvidenceKind.OBSERVED_DECISION
        and metadata.self_kind is SelfKind.DECISION
        and metadata.decision_id is None
        and note.front_matter.get("id") == str(note.note_id)
        and note.front_matter.get("type") == note.note_type.value
    ):
        return False
    raw_metadata, issues = validate_canonical_personal_memory_fields(note.front_matter)
    return not issues and raw_metadata == metadata


def _available_options_reason(body: str) -> str | None:
    try:
        sections = _parse_sections(body, JOURNAL_REQUIRED_HEADINGS, DecisionJournalBodyError)
    except DecisionJournalBodyError:
        return None
    try:
        _parse_bullet_list(
            sections["Available options"],
            minimum=MIN_JOURNAL_OPTIONS,
            maximum=MAX_JOURNAL_OPTIONS,
            error_type=DecisionJournalBodyError,
        )
    except DecisionJournalBodyError as error:
        return error.reason
    return None


def _matching_option_indexes(journal: DecisionJournalRecord) -> tuple[int, ...]:
    chosen = _normalize_whitespace(journal.chosen_option)
    return tuple(
        index
        for index, option in enumerate(journal.available_options)
        if _normalize_whitespace(option) == chosen
    )


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def _build_simulate_me_request(journal: DecisionJournalRecord) -> SimulateMeRequest | None:
    try:
        query = "Situation=" + _encode_field(journal.situation)
        query += "|InformationKnownAtDecisionTime=" + _encode_field(
            journal.information_known_at_decision_time
        )
        query += "|Criteria=" + ";".join(_encode_field(item) for item in journal.criteria)
        request = SimulateMeRequest(
            query=query,
            options=tuple(
                SimulateMeOption(id=f"o{index}", label=label)
                for index, label in enumerate(journal.available_options, start=1)
            ),
        )
        return validate_simulate_me_request(request)
    except Exception:
        return None


def _encode_field(value: str) -> str:
    encoded = value.encode("utf-8")
    return "".join(chr(byte) if byte in _UNRESERVED else f"%{byte:02X}" for byte in encoded)


def _target_option_id(request: SimulateMeRequest, chosen_option: str) -> str | None:
    matches = tuple(
        option.id
        for option in request.options
        if _normalize_whitespace(option.label) == _normalize_whitespace(chosen_option)
    )
    return matches[0] if len(matches) == 1 else None


def _context_input(report: ScanReport) -> RetrospectiveCalibrationContextInputV1:
    if type(report.manifest) is not VaultManifest:
        raise RetrospectiveCalibrationSourceUnavailableError()
    notes = tuple(
        note
        for note in report.notes
        if note.managed is True
        and is_personal_memory_enrolled(note.front_matter)
        and _is_direct_metadata(note.personal_memory)
    )
    return RetrospectiveCalibrationContextInputV1(report.manifest, notes)


def _is_direct_metadata(metadata: PersonalMemoryMetadata | None) -> bool:
    return bool(
        type(metadata) is PersonalMemoryMetadata
        and metadata.evidence_kind in {EvidenceKind.EXPLICIT_USER_FACT, EvidenceKind.USER_STATEMENT}
        and metadata.self_kind in {SelfKind.PREFERENCE, SelfKind.BELIEF, SelfKind.GOAL}
    )


def _filter_context(
    result: SelfModelResult,
    context: RetrospectiveCalibrationContextInputV1,
    decision_at: datetime,
) -> tuple[SelfModelResult, frozenset[str]]:
    validate_self_model_result(
        result,
        request=SELF_MODEL_REQUEST_V1,
        policy=DEFAULT_SELF_MODEL_POLICY,
        expected_policy_fingerprint=SELF_MODEL_POLICY_FINGERPRINT,
    )
    notes_by_id: dict[UUID, list[NoteRecord]] = {}
    for note in context.notes:
        if type(note.note_id) is UUID and note.note_id.version == 7:
            notes_by_id.setdefault(note.note_id, []).append(note)
    flags: set[str] = set()
    safe_claims: list[SelfModelClaim] = []
    for claim in result.claims:
        refs = tuple(
            ref
            for role in (
                claim.supporting_evidence,
                claim.contradicting_evidence,
                claim.contextual_evidence,
            )
            for ref in role
        )
        claim_safe = True
        for ref in refs:
            source_ids = (ref.note_id, *ref.related_note_ids)
            for source_id in source_ids:
                matches = notes_by_id.get(source_id, [])
                if len(matches) != 1:
                    claim_safe = False
                    continue
                note = matches[0]
                source_flags = _source_temporal_flags(note, decision_at)
                flags.update(source_flags)
                if source_flags or not _source_matches_ref(note, ref, claim):
                    claim_safe = False
        if claim_safe:
            safe_claims.append(claim)
    safe_claim_tuple = tuple(safe_claims)
    represented_ids = {
        ref.note_id
        for claim in safe_claim_tuple
        for role in (
            claim.supporting_evidence,
            claim.contradicting_evidence,
            claim.contextual_evidence,
        )
        for ref in role
    }
    filtered = replace(
        result,
        claims=safe_claim_tuple,
        eligible_evidence_count=len(represented_ids),
        represented_evidence_count=len(represented_ids),
    )
    validate_self_model_result(
        filtered,
        request=SELF_MODEL_REQUEST_V1,
        policy=DEFAULT_SELF_MODEL_POLICY,
        expected_policy_fingerprint=SELF_MODEL_POLICY_FINGERPRINT,
    )
    return filtered, frozenset(flags)


def _source_temporal_flags(note: NoteRecord, decision_at: datetime) -> frozenset[str]:
    metadata = note.personal_memory
    flags: set[str] = set()
    if type(metadata) is not PersonalMemoryMetadata:
        return frozenset()
    if metadata.evidence_at == PERSONAL_MEMORY_UNKNOWN_TIME:
        flags.add(RetrospectiveCalibrationTemporalCaveatCodeV1.UNKNOWN_EVIDENCE.value)
    elif (
        isinstance(metadata.evidence_at, datetime)
        and metadata.evidence_at.astimezone(UTC) > decision_at
    ):
        flags.add(RetrospectiveCalibrationTemporalCaveatCodeV1.LATER_EVIDENCE.value)
    if (
        isinstance(note.created, datetime)
        and _aware(note.created)
        and note.created.astimezone(UTC) > decision_at
    ):
        flags.add(RetrospectiveCalibrationTemporalCaveatCodeV1.CREATED_AFTER_CUTOFF.value)
    if (
        isinstance(note.updated, datetime)
        and _aware(note.updated)
        and note.updated.astimezone(UTC) > decision_at
    ):
        flags.add(RetrospectiveCalibrationTemporalCaveatCodeV1.EDITED_AFTER_CUTOFF.value)
    return frozenset(flags)


def _source_matches_ref(
    note: NoteRecord,
    ref: SelfModelEvidenceRef,
    claim: SelfModelClaim,
) -> bool:
    metadata = note.personal_memory
    if (
        note.managed is not True
        or not is_personal_memory_enrolled(note.front_matter)
        or not _safe_relative_path(note.relative_path)
        or type(note.note_id) is not UUID
        or note.note_id.version != 7
        or type(note.note_type) is not NoteType
        or not _aware(note.created)
        or (note.updated is not None and not _aware(note.updated))
        or not _is_direct_metadata(metadata)
        or metadata is None
        or not _raw_metadata_matches_note(note, metadata)
        or metadata.evidence_kind is not ref.evidence_kind
        or metadata.self_kind is not ref.self_kind
        or metadata.domain != ref.domain
        or metadata.evidence_at != ref.evidence_at
        or metadata.evidence_at_precision is not ref.evidence_at_precision
    ):
        return False
    if claim.supporting_evidence == (ref,):
        return _normalize_body(note.body) == claim.claim
    return True


def _raw_metadata_matches_note(
    note: NoteRecord,
    metadata: PersonalMemoryMetadata,
) -> bool:
    raw_metadata, issues = validate_canonical_personal_memory_fields(note.front_matter)
    return not issues and raw_metadata == metadata


def _stage6_policy_mismatch(result: object) -> bool:
    return type(result) is SimulateMeResult and (
        result.derivation_version != SIMULATE_ME_DERIVATION_VERSION_V1
        or result.policy_id != SIMULATE_ME_POLICY_ID_V1
        or result.policy_fingerprint != SIMULATE_ME_POLICY_FINGERPRINT_V1
    )


def _stage6_composition_is_safe(
    result: SimulateMeResult,
    context: SelfModelResult,
) -> bool:
    if result.temporal_caveats:
        return False
    context_ids = {
        ref.note_id
        for claim in context.claims
        for role in (
            claim.supporting_evidence,
            claim.contradicting_evidence,
            claim.contextual_evidence,
        )
        for ref in role
    }
    return all(ref.claim_id in context_ids for ref in result.evidence_refs) and all(
        ref.claim_id in context_ids for ref in result.contextual_evidence_refs
    )


def _eligible_sort_key(case: _EligibleCase) -> tuple[datetime, str, str]:
    assert case.note.note_id is not None
    return case.decision_at.astimezone(UTC), str(case.note.note_id).lower(), case.note.relative_path


def _valid_scan_counter(value: object) -> bool:
    return type(value) is int and value >= 0


def _is_cancelled(token: object) -> bool:
    try:
        checker = cast(_CancellationLike, token).is_cancelled
        if not callable(checker):
            return True
        return bool(checker())
    except Exception:
        return True


def _valid_non_negative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _aware(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _same_instant(first: object, second: datetime) -> bool:
    return (
        isinstance(first, datetime)
        and _aware(first)
        and first.astimezone(UTC) == second.astimezone(UTC)
    )


def _safe_relative_path(value: object) -> bool:
    if type(value) is not str or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    )


def _normalize_body(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _is_inbox_note(relative_path: str, manifest: VaultManifest) -> bool:
    path = PurePosixPath(relative_path)
    inbox = manifest.paths.inbox
    return path == inbox or inbox in path.parents


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "DERIVATION_VERSION",
    "MAX_DECISION_CASES_V1",
    "MAX_RESULT_BYTES_V1",
    "MAX_SCAN_BYTES_V1",
    "MAX_SCAN_DOCUMENTS_V1",
    "MAX_SCAN_ENTRIES_V1",
    "POLICY_CANONICAL_JSON",
    "POLICY_FINGERPRINT",
    "POLICY_ID",
    "RECONSTRUCTION_MODE",
    "SCAN_LIMITS_V1",
    "SELF_MODEL_DERIVATION_VERSION",
    "SELF_MODEL_POLICY_FINGERPRINT",
    "SELF_MODEL_REQUEST_V1",
    "SIMULATE_ME_DERIVATION_VERSION_V1",
    "SIMULATE_ME_POLICY_FINGERPRINT_V1",
    "SIMULATE_ME_POLICY_ID_V1",
    "BuildRetrospectiveCalibration",
    "BuildRetrospectiveCalibrationContext",
    "BuildRetrospectiveCalibrationReplay",
    "RetrospectiveCalibrationCancelledError",
    "RetrospectiveCalibrationContextInputV1",
    "RetrospectiveCalibrationContextPort",
    "RetrospectiveCalibrationContextUnavailableError",
    "RetrospectiveCalibrationCountV1",
    "RetrospectiveCalibrationError",
    "RetrospectiveCalibrationErrorCodeV1",
    "RetrospectiveCalibrationExcludedCodeV1",
    "RetrospectiveCalibrationHistoricalContextError",
    "RetrospectiveCalibrationInvalidRequestError",
    "RetrospectiveCalibrationMetricsV1",
    "RetrospectiveCalibrationRatioV1",
    "RetrospectiveCalibrationReplayCancelledError",
    "RetrospectiveCalibrationReplayInvalidCodeV1",
    "RetrospectiveCalibrationReplayPort",
    "RetrospectiveCalibrationReplayUnavailableCodeV1",
    "RetrospectiveCalibrationReplayUnavailableError",
    "RetrospectiveCalibrationRequestV1",
    "RetrospectiveCalibrationResultTooLargeError",
    "RetrospectiveCalibrationResultV1",
    "RetrospectiveCalibrationScanLimitsV1",
    "RetrospectiveCalibrationScanPort",
    "RetrospectiveCalibrationScanV1",
    "RetrospectiveCalibrationSourceUnavailableError",
    "RetrospectiveCalibrationTemporalCaveatCodeV1",
    "RetrospectiveCalibrationTooLargeError",
    "serialize_retrospective_calibration_result",
    "validate_retrospective_calibration_policy",
    "validate_retrospective_calibration_result",
]
