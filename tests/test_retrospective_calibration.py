"""Focused deterministic tests for Retrospective Calibration v1."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, cast
from uuid import UUID

import pytest

from second_brain.adapters.vault import FileSystemRetrospectiveCalibrationScanner
from second_brain.application.decision_journal import render_decision_journal_body
from second_brain.application.reports import (
    Diagnostic,
    DiagnosticSeverity,
    VaultRootRole,
    VaultSnapshot,
)
from second_brain.application.retrospective_calibration import (
    MAX_DECISION_CASES_V1,
    MAX_RESULT_BYTES_V1,
    BuildRetrospectiveCalibration,
    BuildRetrospectiveCalibrationContext,
    BuildRetrospectiveCalibrationReplay,
    RetrospectiveCalibrationCancelledError,
    RetrospectiveCalibrationContextInputV1,
    RetrospectiveCalibrationContextPort,
    RetrospectiveCalibrationCountV1,
    RetrospectiveCalibrationErrorCodeV1,
    RetrospectiveCalibrationExcludedCodeV1,
    RetrospectiveCalibrationInvalidRequestError,
    RetrospectiveCalibrationMetricsV1,
    RetrospectiveCalibrationRatioV1,
    RetrospectiveCalibrationReplayInvalidCodeV1,
    RetrospectiveCalibrationReplayPort,
    RetrospectiveCalibrationReplayUnavailableCodeV1,
    RetrospectiveCalibrationRequestV1,
    RetrospectiveCalibrationResultV1,
    RetrospectiveCalibrationScanLimitsV1,
    RetrospectiveCalibrationScanV1,
    RetrospectiveCalibrationSourceUnavailableError,
    RetrospectiveCalibrationTemporalCaveatCodeV1,
    RetrospectiveCalibrationTooLargeError,
    serialize_retrospective_calibration_result,
    validate_retrospective_calibration_policy,
)
from second_brain.application.self_model import (
    SelfModelRequest,
    SelfModelResult,
)
from second_brain.application.simulate_me import (
    SimulateMeAbstentionCode,
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResult,
    SimulateMeResultKind,
)
from second_brain.domain.models import (
    AttachmentPolicy,
    MarkdownDocument,
    VaultManifest,
    VaultPaths,
)
from tests.conftest import create_vault, write_note

DECISION_AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
CREATED_AT = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
GENERATED_AT = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)


def _uuid(index: int) -> UUID:
    return UUID(f"0198f4c5-6a00-7000-8000-{index:012d}")


def _manifest() -> VaultManifest:
    return VaultManifest(
        schema_version=1,
        vault_id=_uuid(900),
        default_language="ru",
        paths=VaultPaths(
            inbox=PurePosixPath("00 Inbox"),
            projects=PurePosixPath("10 Projects"),
            areas=PurePosixPath("20 Areas"),
            resources=PurePosixPath("30 Resources"),
            zettelkasten=PurePosixPath("40 Zettelkasten"),
            archive=PurePosixPath("90 Archive"),
            templates=PurePosixPath("Templates"),
            attachments=PurePosixPath("Attachments"),
        ),
        attachments=AttachmentPolicy(warning_size_bytes=1024, max_size_bytes=4096),
    )


def _journal_front_matter(
    note_id: UUID,
    *,
    evidence_at: str = "2026-09-05T12:00:00Z",
    precision: str = "exact",
    created: str = "2026-09-05T10:00:00Z",
    updated: str | None = None,
    note_type: str = "zettel",
) -> dict[str, object]:
    front_matter: dict[str, object] = {
        "id": str(note_id),
        "type": note_type,
        "created": created,
        "tags": [],
        "second_brain_personal_memory": 1,
        "evidence_kind": "observed_decision",
        "self_kind": "decision",
        "evidence_at": evidence_at,
        "evidence_at_precision": precision,
    }
    if updated is not None:
        front_matter["updated"] = updated
    return front_matter


def _journal_body(
    *,
    options: tuple[str, ...] = ("Первый вариант", "Второй вариант"),
    chosen: str = "Второй вариант",
    reasons: str = "Masked reasons.",
    confidence: str = "Masked confidence.",
    expected: str = "Masked expectation.",
    situation: str = "Выбрать направление",
    information: str = "Ограничение по сроку.",
    criteria: tuple[str, ...] = ("Скорость",),
) -> str:
    return render_decision_journal_body(
        situation=situation,
        available_options=options,
        information_known_at_decision_time=information,
        criteria=criteria,
        chosen_option=chosen,
        reasons=reasons,
        confidence=confidence,
        expected_result=expected,
    )


def _direct_front_matter(
    note_id: UUID,
    *,
    self_kind: str = "preference",
    evidence_at: str = "2026-09-05T11:00:00Z",
    precision: str = "exact",
    created: str = "2026-09-05T09:00:00Z",
    updated: str | None = None,
) -> dict[str, object]:
    front_matter: dict[str, object] = {
        "id": str(note_id),
        "type": "zettel",
        "created": created,
        "tags": [],
        "second_brain_personal_memory": 1,
        "evidence_kind": "user_statement",
        "self_kind": self_kind,
        "evidence_at": evidence_at,
        "evidence_at_precision": precision,
    }
    if updated is not None:
        front_matter["updated"] = updated
    return front_matter


def _snapshot(
    *documents: MarkdownDocument, diagnostics: tuple[Diagnostic, ...] = ()
) -> VaultSnapshot:
    return VaultSnapshot(
        vault_path="D:/synthetic-vault",
        manifest=_manifest(),
        documents=documents,
        diagnostics=diagnostics,
    )


@dataclass
class _Scanner:
    snapshot: VaultSnapshot
    limit_exceeded: bool = False
    calls: int = 0
    received_limits: RetrospectiveCalibrationScanLimitsV1 | None = None

    def scan(self, limits: RetrospectiveCalibrationScanLimitsV1) -> RetrospectiveCalibrationScanV1:
        self.calls += 1
        self.received_limits = limits
        return RetrospectiveCalibrationScanV1(
            snapshot=self.snapshot,
            entries_inspected=len(self.snapshot.documents),
            documents_materialized=len(self.snapshot.documents),
            raw_utf8_bytes=sum(
                len(document.body.encode("utf-8")) for document in self.snapshot.documents
            ),
            limit_exceeded=self.limit_exceeded,
        )


@dataclass
class _Context:
    result: SelfModelResult | Exception
    calls: int = 0
    received_requests: list[SelfModelRequest] | None = None

    def build(
        self,
        *,
        context: RetrospectiveCalibrationContextInputV1,
        decision_at: datetime,
        request: SelfModelRequest,
    ) -> SelfModelResult:
        del context, decision_at
        self.calls += 1
        if self.received_requests is None:
            self.received_requests = []
        self.received_requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@dataclass
class _Replay:
    result: SimulateMeResult | Exception
    calls: int = 0
    requests: list[object] | None = None
    contexts: list[object] | None = None

    def execute(
        self,
        request: SimulateMeRequest,
        *,
        context: SelfModelResult,
    ) -> SimulateMeResult:
        self.calls += 1
        if self.requests is None:
            self.requests = []
        if self.contexts is None:
            self.contexts = []
        self.requests.append(request)
        self.contexts.append(context)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _service(
    snapshot: VaultSnapshot,
    *,
    context: RetrospectiveCalibrationContextPort | None = None,
    replay: RetrospectiveCalibrationReplayPort | None = None,
    scanner: _Scanner | None = None,
) -> tuple[
    BuildRetrospectiveCalibration,
    _Scanner,
    RetrospectiveCalibrationContextPort,
    RetrospectiveCalibrationReplayPort,
]:
    actual_scanner = scanner or _Scanner(snapshot)
    actual_context = context or BuildRetrospectiveCalibrationContext(clock=lambda: GENERATED_AT)
    actual_replay = replay or BuildRetrospectiveCalibrationReplay()
    return (
        BuildRetrospectiveCalibration(actual_scanner, actual_context, actual_replay),
        actual_scanner,
        actual_context,
        actual_replay,
    )


def _journal_document(
    index: int,
    *,
    front_matter: dict[str, object] | None = None,
    body: str | None = None,
    **body_kwargs: object,
) -> MarkdownDocument:
    note_id = _uuid(index)
    return MarkdownDocument(
        relative_path=f"10 Projects/decision-{index}.md",
        front_matter=front_matter or _journal_front_matter(note_id),
        body=body if body is not None else _journal_body(**cast(Any, body_kwargs)),
        in_inbox=False,
    )


def _direct_document(index: int, body: str, **kwargs: object) -> MarkdownDocument:
    return MarkdownDocument(
        relative_path=f"10 Projects/direct-{index}.md",
        front_matter=_direct_front_matter(_uuid(index), **cast(Any, kwargs)),
        body=body,
        in_inbox=False,
    )


def _counts(result: RetrospectiveCalibrationResultV1, field: str) -> dict[str, int]:
    return {item.code: item.count for item in getattr(result, field)}


def test_policy_identity_and_canonical_result_shape_are_fixed() -> None:
    assert validate_retrospective_calibration_policy() == (
        "sha256:6218f228dfcf70c2bf410f5fd39323145681d5683275265ee6be7dd821a55f7f"
    )
    snapshot = _snapshot()
    service, _scanner, _context, _replay = _service(snapshot)
    result = service.execute(RetrospectiveCalibrationRequestV1())

    assert result.metrics.decision_notes_seen == 0
    assert result.metrics.coverage is None
    assert result.metrics.accuracy_non_abstained is None
    assert [item.code for item in result.excluded_decisions] == [
        item.value for item in RetrospectiveCalibrationExcludedCodeV1
    ]
    assert [item.code for item in result.replay_unavailable] == [
        item.value for item in RetrospectiveCalibrationReplayUnavailableCodeV1
    ]
    assert [item.code for item in result.replay_invalid] == [
        item.value for item in RetrospectiveCalibrationReplayInvalidCodeV1
    ]
    assert [item.code for item in result.temporal_caveats] == [
        item.value for item in RetrospectiveCalibrationTemporalCaveatCodeV1
    ]
    encoded = serialize_retrospective_calibration_result(result)
    assert encoded == serialize_retrospective_calibration_result(result)
    assert b"\\u" not in encoded
    assert len(encoded) <= MAX_RESULT_BYTES_V1
    assert not encoded.endswith(b"\n")


def test_temporal_caveat_counts_cannot_exceed_eligible_cases() -> None:
    service, _scanner, _context, _replay = _service(_snapshot())
    result = service.execute(RetrospectiveCalibrationRequestV1())
    invalid = replace(
        result,
        temporal_caveats=(
            RetrospectiveCalibrationCountV1("unknown_evidence_excluded", 1),
            *result.temporal_caveats[1:],
        ),
    )

    with pytest.raises(ValueError):
        serialize_retrospective_calibration_result(invalid)


def test_result_validator_rejects_decision_case_count_above_bound() -> None:
    service, _scanner, _context, _replay = _service(_snapshot())
    result = service.execute(RetrospectiveCalibrationRequestV1())
    case_count = MAX_DECISION_CASES_V1 + 1
    invalid_metrics = replace(
        result.metrics,
        decision_notes_seen=case_count,
        eligible_decisions=case_count,
        abstentions=case_count,
        coverage=RetrospectiveCalibrationRatioV1(0, case_count),
    )

    with pytest.raises(ValueError):
        serialize_retrospective_calibration_result(replace(result, metrics=invalid_metrics))


def test_bounded_scanner_enforces_entry_limit_before_materialization(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/one.md", "---\nid: x\n---\nbody")
    scanner = FileSystemRetrospectiveCalibrationScanner(vault)

    result = scanner.scan(
        RetrospectiveCalibrationScanLimitsV1(
            max_scan_entries=0,
            max_scan_documents=4096,
            max_scan_bytes=16_777_216,
        )
    )

    assert result.limit_exceeded is True
    assert result.documents_materialized == 0


def test_bounded_scanner_caps_a_file_that_grows_after_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = create_vault(tmp_path / "vault")
    note = write_note(vault, "10 Projects/growing.md", "small file")
    actual_bytes = note.read_bytes()
    target = note.resolve()
    read_sizes: list[int] = []

    class _GrowingStream(BytesIO):
        def __init__(self) -> None:
            super().__init__(actual_bytes + b"x" * 1024)

        def read(self, size: int | None = -1) -> bytes:
            read_sizes.append(-1 if size is None else size)
            return super().read(-1 if size is None else size)

    original_open = Path.open

    def growing_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.resolve(strict=False) == target:
            return _GrowingStream()
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", growing_open)
    result = FileSystemRetrospectiveCalibrationScanner(vault).scan(
        RetrospectiveCalibrationScanLimitsV1(
            max_scan_entries=16_384,
            max_scan_documents=4_096,
            max_scan_bytes=len(actual_bytes) + 2,
        )
    )

    assert result.limit_exceeded is True
    assert read_sizes == [len(actual_bytes) + 3]


def test_exact_prediction_uses_only_prechoice_fields_and_request_local_target() -> None:
    snapshot = _snapshot(
        _journal_document(1),
        _direct_document(2, "Второй вариант", self_kind="preference"),
    )
    service, scanner, _context, replay = _service(snapshot)

    result = service.execute(RetrospectiveCalibrationRequestV1())

    assert result.metrics.predicted_decisions == 1
    assert result.metrics.exact_option_match_count == 1
    assert result.metrics.mismatch_count == 0
    assert result.metrics.accuracy_non_abstained == RetrospectiveCalibrationRatioV1(1, 1)
    assert scanner.received_limits is not None
    assert replay is not None


def test_multiline_allowed_fields_are_reversibly_encoded_and_masked_fields_do_not_leak() -> None:
    first = _journal_document(
        1,
        situation="Строка 1\nСтрока 2 | сохраняется",
        information="Known\nvalue",
        criteria=("Критерий 1", "Критерий 2; %"),
        reasons="SECRET-REASON-A",
        confidence="SECRET-CONFIDENCE-A",
        expected="SECRET-EXPECTED-A",
    )
    second = _journal_document(
        1,
        situation="Строка 1\nСтрока 2 | сохраняется",
        information="Known\nvalue",
        criteria=("Критерий 1", "Критерий 2; %"),
        reasons="SECRET-REASON-B",
        confidence="SECRET-CONFIDENCE-B",
        expected="SECRET-EXPECTED-B",
    )

    # The fake returns a terminal result from its own request, so use a
    # recording replay that derives the valid result from the received request.
    class _RecordingReplay:
        def __init__(self) -> None:
            self.requests: list[SimulateMeRequest] = []

        def execute(
            self,
            request: SimulateMeRequest,
            *,
            context: SelfModelResult,
        ) -> SimulateMeResult:
            self.requests.append(request)
            return BuildRetrospectiveCalibrationReplay().execute(request, context=context)

    recording = _RecordingReplay()
    service_a, _scanner_a, _context_a, _replay_a = _service(
        _snapshot(first, _direct_document(2, "Второй вариант")), replay=recording
    )
    service_b, _scanner_b, _context_b, _replay_b = _service(
        _snapshot(second, _direct_document(2, "Второй вариант")), replay=recording
    )
    result_a = service_a.execute(RetrospectiveCalibrationRequestV1())
    result_b = service_b.execute(RetrospectiveCalibrationRequestV1())

    assert result_a == result_b
    assert len(recording.requests) == 2
    assert recording.requests[0] == recording.requests[1]
    request_text = recording.requests[0].query
    assert "SECRET-" not in request_text
    assert "Chosen" not in request_text
    assert "Expected" not in request_text
    assert "%D0%A1%D1%82%D1%80%D0%BE%D0%BA%D0%B0%201%0A" in request_text
    assert "%7C" in request_text


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    (
        ({"evidence_at": "unknown", "precision": "unknown"}, "decision_time_unknown"),
        ({"evidence_at": "not-a-time", "precision": "exact"}, "decision_time_not_exact_or_invalid"),
        ({"created": "2026-09-05T13:00:00Z"}, "decision_body_created_after_cutoff"),
        ({"updated": "2026-09-05T13:00:01Z"}, "decision_body_edited_after_cutoff"),
        ({"note_type": "not-a-note-type"}, "decision_note_metadata_invalid"),
    ),
)
def test_eligibility_first_match_maps_time_storage_and_note_metadata(
    kwargs: dict[str, str], expected: str
) -> None:
    document = _journal_document(1, front_matter=_journal_front_matter(_uuid(1), **kwargs))
    service, _scanner, context, replay = _service(_snapshot(document))

    result = service.execute(RetrospectiveCalibrationRequestV1())

    assert result.metrics.decision_notes_seen == 1
    assert _counts(result, "excluded_decisions")[expected] == 1
    assert result.metrics.eligible_decisions == 0
    assert getattr(context, "calls", 0) == 0
    assert getattr(replay, "calls", 0) == 0


def test_storage_timestamp_failures_follow_first_match_order() -> None:
    missing_created = _journal_front_matter(_uuid(1), updated="2026-09-05T13:00:01Z")
    del missing_created["created"]
    cases = (
        (missing_created, "decision_body_edited_after_cutoff"),
        (
            _journal_front_matter(_uuid(2), created="not-a-time", updated="2026-09-05T13:00:01Z"),
            "decision_body_edited_after_cutoff",
        ),
        (
            _journal_front_matter(_uuid(3), created="2026-09-05T13:00:01Z", updated="not-a-time"),
            "decision_body_created_after_cutoff",
        ),
        (
            _journal_front_matter(_uuid(4), created="not-a-time", updated="2026-09-05T11:00:00Z"),
            "decision_note_metadata_invalid",
        ),
        (
            _journal_front_matter(_uuid(5), created="not-a-time"),
            "decision_note_metadata_invalid",
        ),
    )

    for index, (front_matter, expected) in enumerate(cases, start=1):
        service, _scanner, context, replay = _service(
            _snapshot(_journal_document(index, front_matter=front_matter))
        )

        result = service.execute(RetrospectiveCalibrationRequestV1())

        assert result.metrics.decision_notes_seen == 1
        assert _counts(result, "excluded_decisions")[expected] == 1
        assert result.metrics.eligible_decisions == 0
        assert getattr(context, "calls", 0) == 0
        assert getattr(replay, "calls", 0) == 0


def test_unknown_evidence_time_has_exact_per_case_caveat_only_after_context_boundary() -> None:
    # An unknown-time decision is excluded before context inspection and gets
    # no temporal caveat.  A valid decision with unknown direct evidence does.
    unknown_decision = _journal_document(
        1,
        front_matter=_journal_front_matter(_uuid(1), evidence_at="unknown", precision="unknown"),
    )
    valid_decision = _journal_document(3)
    unknown_context = _direct_document(
        2, "Unknown source", evidence_at="unknown", precision="unknown"
    )
    snapshot = _snapshot(unknown_decision, valid_decision, unknown_context)
    service, _scanner, _context, _replay = _service(snapshot)

    result = service.execute(RetrospectiveCalibrationRequestV1())

    assert result.metrics.decision_notes_seen == 2
    assert result.metrics.eligible_decisions == 1
    assert _counts(result, "temporal_caveats")["unknown_evidence_excluded"] == 1
    assert _counts(result, "temporal_caveats")["historical_snapshot_unavailable"] == 1


def test_option_count_identity_and_body_failures_are_not_mismatches() -> None:
    too_many = _journal_document(
        1,
        body=_journal_body(
            options=tuple(f"Option {index}" for index in range(9)), chosen="Option 1"
        ),
    )
    duplicate = _journal_document(
        2,
        body=_journal_body(options=("A", " A "), chosen="A"),
    )
    chosen_missing = _journal_document(3, body=_journal_body(chosen="Absent"))
    malformed = _journal_document(4, body="## Situation\n\nbody")
    service, _scanner, context, replay = _service(
        _snapshot(too_many, duplicate, chosen_missing, malformed)
    )

    result = service.execute(RetrospectiveCalibrationRequestV1())
    excluded = _counts(result, "excluded_decisions")

    assert excluded["decision_option_count_unsupported"] == 1
    assert excluded["decision_option_identity_invalid"] == 2
    assert excluded["decision_body_invalid"] == 1
    assert result.metrics.mismatch_count == 0
    assert getattr(context, "calls", 0) == 0
    assert getattr(replay, "calls", 0) == 0


def test_non_journal_pair_with_malformed_personal_memory_is_not_a_candidate() -> None:
    front_matter = _direct_front_matter(_uuid(1))
    front_matter["domain"] = "INVALID DOMAIN"
    document = MarkdownDocument(
        relative_path="10 Projects/not-a-decision.md",
        front_matter=front_matter,
        body="A direct assertion.",
        in_inbox=False,
    )
    service, _scanner, context, replay = _service(_snapshot(document))

    result = service.execute(RetrospectiveCalibrationRequestV1())

    assert result.metrics.decision_notes_seen == 0
    assert getattr(context, "calls", 0) == 0
    assert getattr(replay, "calls", 0) == 0


def test_invalid_direct_context_metadata_reaches_self_model_boundary() -> None:
    invalid_direct = _direct_front_matter(_uuid(2))
    invalid_direct["domain"] = "INVALID DOMAIN"
    invalid_context = MarkdownDocument(
        relative_path="10 Projects/invalid-direct.md",
        front_matter=invalid_direct,
        body="A direct assertion.",
        in_inbox=False,
    )
    service, _scanner, _context, replay = _service(_snapshot(_journal_document(1), invalid_context))

    result = service.execute(RetrospectiveCalibrationRequestV1())

    assert _counts(result, "replay_unavailable")["prechoice_context_unavailable"] == 1
    assert getattr(replay, "calls", 0) == 0


def test_missing_personal_memory_kind_with_plausible_journal_shape_is_classified() -> None:
    front_matter = _journal_front_matter(_uuid(1))
    del front_matter["evidence_kind"]
    document = MarkdownDocument(
        relative_path="10 Projects/missing-kind.md",
        front_matter=front_matter,
        body=_journal_body(),
        in_inbox=False,
    )
    service, _scanner, context, replay = _service(_snapshot(document))

    result = service.execute(RetrospectiveCalibrationRequestV1())

    assert result.metrics.decision_notes_seen == 1
    assert _counts(result, "excluded_decisions")["decision_note_metadata_invalid"] == 1
    assert getattr(context, "calls", 0) == 0
    assert getattr(replay, "calls", 0) == 0


def test_content_completeness_failure_returns_top_level_source_error_without_partial_result() -> (
    None
):
    diagnostic = Diagnostic(
        code="VAULT_LINKED_ENTRY",
        message="linked entry",
        severity=DiagnosticSeverity.WARNING,
        path="10 Projects/linked.md",
        root_role=VaultRootRole.PROJECTS,
    )
    service, scanner, _context, _replay = _service(
        _snapshot(_journal_document(1), diagnostics=(diagnostic,))
    )

    with pytest.raises(RetrospectiveCalibrationSourceUnavailableError) as error:
        service.execute(RetrospectiveCalibrationRequestV1())

    assert error.value.code == RetrospectiveCalibrationErrorCodeV1.SOURCE_UNAVAILABLE.value
    assert scanner.calls == 1


def test_scan_limit_and_classified_case_limit_return_top_level_too_large() -> None:
    too_large_scanner = _Scanner(_snapshot(), limit_exceeded=True)
    service, scanner, _context, _replay = _service(_snapshot(), scanner=too_large_scanner)
    with pytest.raises(RetrospectiveCalibrationTooLargeError) as scan_error:
        service.execute(RetrospectiveCalibrationRequestV1())
    assert scan_error.value.code == RetrospectiveCalibrationErrorCodeV1.TOO_LARGE.value
    assert scanner.calls == 1

    documents = tuple(_journal_document(index) for index in range(1, MAX_DECISION_CASES_V1 + 2))
    case_service, _case_scanner, context, replay = _service(_snapshot(*documents))
    with pytest.raises(RetrospectiveCalibrationTooLargeError):
        case_service.execute(RetrospectiveCalibrationRequestV1())
    assert getattr(context, "calls", 0) == 0
    assert getattr(replay, "calls", 0) == 0


def test_invalid_request_is_rejected_before_bounded_scan() -> None:
    service, scanner, _context, _replay = _service(_snapshot())

    with pytest.raises(RetrospectiveCalibrationInvalidRequestError):
        service.execute(object())  # type: ignore[arg-type]

    assert scanner.calls == 0


def test_filtered_context_uses_exact_self_model_request_and_no_full_context_fallback() -> None:
    snapshot = _snapshot(_journal_document(1), _direct_document(2, "Other"))
    context = _Context(RetrospectiveCalibrationSourceUnavailableError())
    replay = _Replay(cast(SimulateMeResult, object()))
    service, _scanner, actual_context, actual_replay = _service(
        snapshot, context=context, replay=replay
    )

    result = service.execute(RetrospectiveCalibrationRequestV1())

    assert _counts(result, "replay_unavailable")["prechoice_context_unavailable"] == 1
    assert context.received_requests == [SelfModelRequest(200, 200)]
    assert actual_context is context
    assert replay.calls == 0
    assert actual_replay is replay


def test_wrong_policy_and_malformed_stage6_results_are_fixed_replay_invalid_codes() -> None:
    snapshot = _snapshot(_journal_document(1), _direct_document(2, "Второй вариант"))
    context_result = BuildRetrospectiveCalibrationContext(clock=lambda: GENERATED_AT)

    class _ContextFromReal:
        def __init__(self) -> None:
            self.inner = context_result

        def build(
            self,
            *,
            context: RetrospectiveCalibrationContextInputV1,
            decision_at: datetime,
            request: SelfModelRequest,
        ) -> SelfModelResult:
            return self.inner.build(context=context, decision_at=decision_at, request=request)

    class _WrongPolicy:
        def execute(
            self,
            request: SimulateMeRequest,
            *,
            context: SelfModelResult,
        ) -> SimulateMeResult:
            valid = BuildRetrospectiveCalibrationReplay().execute(request, context=context)
            return replace(valid, policy_id="wrong-policy")

    wrong_service, _scanner, _context, _replay = _service(
        snapshot, context=_ContextFromReal(), replay=_WrongPolicy()
    )
    wrong_result = wrong_service.execute(RetrospectiveCalibrationRequestV1())
    assert _counts(wrong_result, "replay_invalid")["simulate_me_policy_mismatch"] == 1

    class _Malformed:
        def execute(
            self,
            request: SimulateMeRequest,
            *,
            context: SelfModelResult,
        ) -> SimulateMeResult:
            valid = BuildRetrospectiveCalibrationReplay().execute(request, context=context)
            return replace(valid, selected_option=SimulateMeOption("bad", "bad"))

    malformed_service, _scanner, _context, _replay = _service(
        snapshot, context=_ContextFromReal(), replay=_Malformed()
    )
    malformed_result = malformed_service.execute(RetrospectiveCalibrationRequestV1())
    assert _counts(malformed_result, "replay_invalid")["simulate_me_result_invalid"] == 1

    class _CompositionMismatch:
        def execute(
            self,
            request: SimulateMeRequest,
            *,
            context: SelfModelResult,
        ) -> SimulateMeResult:
            valid = BuildRetrospectiveCalibrationReplay().execute(request, context=context)
            assert valid.evidence_refs
            return replace(
                valid,
                evidence_refs=(replace(valid.evidence_refs[0], evidence_at=DECISION_AT),),
            )

    composition_service, _scanner, _context, _replay = _service(
        snapshot, context=_ContextFromReal(), replay=_CompositionMismatch()
    )
    composition_result = composition_service.execute(RetrospectiveCalibrationRequestV1())
    assert _counts(composition_result, "replay_invalid")["calibration_composition_invalid"] == 1

    class _PredictionWithoutEvidence:
        def execute(
            self,
            request: SimulateMeRequest,
            *,
            context: SelfModelResult,
        ) -> SimulateMeResult:
            valid = BuildRetrospectiveCalibrationReplay().execute(request, context=context)
            return replace(valid, evidence_refs=())

    no_evidence_service, _scanner, _context, _replay = _service(
        snapshot, context=_ContextFromReal(), replay=_PredictionWithoutEvidence()
    )
    no_evidence_result = no_evidence_service.execute(RetrospectiveCalibrationRequestV1())
    assert _counts(no_evidence_result, "replay_invalid")["calibration_composition_invalid"] == 1


def test_valid_abstention_is_counted_without_materializing_target() -> None:
    snapshot = _snapshot(_journal_document(1), _direct_document(2, "Different"))
    service, _scanner, _context, replay = _service(snapshot)

    result = service.execute(RetrospectiveCalibrationRequestV1())

    assert result.metrics.abstentions == 1
    assert result.metrics.predicted_decisions == 0
    assert result.metrics.exact_option_match_count == 0
    assert result.metrics.mismatch_count == 0
    assert replay is not None


def test_unrelated_belief_is_not_required_for_valid_prediction() -> None:
    snapshot = _snapshot(
        _journal_document(1),
        _direct_document(2, "Второй вариант"),
        _direct_document(3, "Несвязанное убеждение", self_kind="belief"),
    )
    service, _scanner, _context, _replay = _service(snapshot)

    result = service.execute(RetrospectiveCalibrationRequestV1())

    assert result.metrics.predicted_decisions == 1
    assert result.metrics.exact_option_match_count == 1
    assert result.metrics.mismatch_count == 0
    assert _counts(result, "replay_invalid")["calibration_composition_invalid"] == 0


@pytest.mark.parametrize(
    ("context_notes", "returned_code", "expected_abstentions", "expected_invalid"),
    (
        ((), SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE, 1, 0),
        ((), SimulateMeAbstentionCode.MULTIPLE_OPTIONS_SUPPORTED, 0, 1),
        (
            (_direct_document(2, "Второй вариант"),),
            SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE,
            0,
            1,
        ),
        (
            (_direct_document(2, "Второй вариант"),),
            SimulateMeAbstentionCode.MULTIPLE_OPTIONS_SUPPORTED,
            0,
            1,
        ),
        (
            (
                _direct_document(2, "Первый вариант"),
                _direct_document(3, "Второй вариант"),
            ),
            SimulateMeAbstentionCode.MULTIPLE_OPTIONS_SUPPORTED,
            1,
            0,
        ),
        (
            (
                _direct_document(2, "Первый вариант"),
                _direct_document(3, "Второй вариант"),
            ),
            SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE,
            0,
            1,
        ),
    ),
)
def test_abstention_code_matches_supported_option_cardinality(
    context_notes: tuple[MarkdownDocument, ...],
    returned_code: SimulateMeAbstentionCode,
    expected_abstentions: int,
    expected_invalid: int,
) -> None:
    class _AbstentionWithCode:
        def execute(
            self,
            request: SimulateMeRequest,
            *,
            context: SelfModelResult,
        ) -> SimulateMeResult:
            valid = BuildRetrospectiveCalibrationReplay().execute(request, context=context)
            return replace(
                valid,
                kind=SimulateMeResultKind.ABSTENTION,
                selected_option=None,
                abstention_code=returned_code,
            )

    service, _scanner, _context, _replay = _service(
        _snapshot(_journal_document(1), *context_notes), replay=_AbstentionWithCode()
    )

    result = service.execute(RetrospectiveCalibrationRequestV1())

    assert result.metrics.abstentions == expected_abstentions
    assert _counts(result, "replay_invalid")["calibration_composition_invalid"] == expected_invalid


def test_mismatch_compares_only_request_local_ids() -> None:
    snapshot = _snapshot(_journal_document(1), _direct_document(2, "Первый вариант"))
    service, _scanner, _context, _replay = _service(snapshot)

    result = service.execute(RetrospectiveCalibrationRequestV1())

    assert result.metrics.predicted_decisions == 1
    assert result.metrics.exact_option_match_count == 0
    assert result.metrics.mismatch_count == 1
    assert result.metrics.accuracy_non_abstained == RetrospectiveCalibrationRatioV1(0, 1)


def test_global_cancellation_discards_partial_aggregate() -> None:
    class _CancelAfterFirst:
        def __init__(self) -> None:
            self.calls = 0

        def is_cancelled(self) -> bool:
            self.calls += 1
            return self.calls >= 6

    token = _CancelAfterFirst()
    service, _scanner, _context, _replay = _service(
        _snapshot(
            _journal_document(1),
            _direct_document(2, "Второй вариант"),
            _journal_document(3),
            _direct_document(4, "Второй вариант"),
        )
    )

    with pytest.raises(RetrospectiveCalibrationCancelledError):
        service.execute(RetrospectiveCalibrationRequestV1(), cancellation=token)


def test_canonical_result_stays_within_byte_limit_without_truncation() -> None:
    empty_metrics = RetrospectiveCalibrationMetricsV1(
        decision_notes_seen=0,
        eligible_decisions=0,
        predicted_decisions=0,
        abstentions=0,
        exact_option_match_count=0,
        mismatch_count=0,
        unavailable_count=0,
        invalid_count=0,
        coverage=None,
        accuracy_non_abstained=None,
    )
    zero_excluded = tuple(
        RetrospectiveCalibrationCountV1(code, 0)
        for code in (
            "decision_identity_invalid_or_duplicate",
            "decision_time_unknown",
            "decision_time_not_exact_or_invalid",
            "decision_body_created_after_cutoff",
            "decision_body_edited_after_cutoff",
            "decision_note_metadata_invalid",
            "decision_option_count_unsupported",
            "decision_option_identity_invalid",
            "decision_body_invalid",
        )
    )
    result = RetrospectiveCalibrationResultV1(
        "retrospective-calibration-v1",
        "retrospective-simulate-me-exact-cutoff-v1",
        "sha256:6218f228dfcf70c2bf410f5fd39323145681d5683275265ee6be7dd821a55f7f",
        "current-vault-temporal-projection-v1",
        empty_metrics,
        zero_excluded,
        tuple(
            RetrospectiveCalibrationCountV1(code, 0)
            for code in (
                "prechoice_context_unavailable",
                "historical_context_unreconstructable",
                "simulate_me_unavailable",
            )
        ),
        tuple(
            RetrospectiveCalibrationCountV1(code, 0)
            for code in (
                "prechoice_request_invalid",
                "simulate_me_result_invalid",
                "simulate_me_policy_mismatch",
                "calibration_composition_invalid",
            )
        ),
        tuple(
            RetrospectiveCalibrationCountV1(code, 0)
            for code in (
                "unknown_evidence_excluded",
                "later_evidence_excluded",
                "created_after_cutoff_excluded",
                "edited_after_cutoff_excluded",
                "historical_snapshot_unavailable",
            )
        ),
    )
    assert len(serialize_retrospective_calibration_result(result)) < MAX_RESULT_BYTES_V1
