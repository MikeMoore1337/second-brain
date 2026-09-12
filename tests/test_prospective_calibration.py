"""Focused deterministic Stage 9C prospective calibration tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import second_brain.application.prospective_audit as prospective_audit
from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.prospective_audit import (
    RETENTION,
    AuditLogEnvelopeV1,
    BuildProspectiveAudit,
    BuildProspectiveCalibration,
    BuildProspectiveDecisionLink,
    ProspectiveAuditStore,
    ProspectiveCalibrationCountV1,
    ProspectiveCalibrationErrorCodeV1,
    ProspectiveCalibrationMetricsV1,
    ProspectiveCalibrationRatioV1,
    ProspectiveCalibrationRequestV1,
    ProspectiveCalibrationResultTooLargeError,
    ProspectiveCalibrationResultV1,
    ProspectiveCalibrationUnavailableError,
    ProspectiveDecisionLinkStateV1,
    ProspectiveDecisionLinkVerificationV1,
    ProspectiveOptionMappingV1,
    fingerprint_decision_option,
    serialize_prospective_calibration_result,
    validate_prospective_calibration_result,
)
from second_brain.application.prospective_audit import (
    ProspectiveCalibrationInvalidLinkageCodeV1 as InvalidCode,
)
from second_brain.application.prospective_audit import (
    ProspectiveCalibrationUnavailableLinkageCodeV1 as UnavailableCode,
)
from second_brain.application.simulate_me import (
    DERIVATION_VERSION,
    POLICY_FINGERPRINT,
    POLICY_ID,
    SimulateMeAbstentionCode,
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResult,
    SimulateMeResultKind,
)
from tests.conftest import create_vault, snapshot_tree, write_note

AUDIT_CREATED = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
DECISION_CREATED = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)
DECISION_EVIDENCE = datetime(2026, 1, 1, 13, 0, tzinfo=UTC)
LINKED_AT = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
DECISION_ID = "0198f4c5-6a00-7000-8000-000000000301"


def _request() -> SimulateMeRequest:
    return SimulateMeRequest(
        "private query must not be stored",
        (
            SimulateMeOption("audit-a", "Audit A"),
            SimulateMeOption("audit-b", "Audit B"),
        ),
    )


def _result(
    *,
    kind: SimulateMeResultKind = SimulateMeResultKind.PREDICTION,
    selected_id: str = "audit-a",
    abstention: SimulateMeAbstentionCode | None = None,
) -> SimulateMeResult:
    selected = next(
        (option for option in _request().options if option.id == selected_id),
        None,
    )
    if kind is SimulateMeResultKind.ABSTENTION:
        selected = None
        abstention = abstention or SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE
    return SimulateMeResult(
        kind=kind,
        selected_option=selected,
        evidence_refs=(),
        contextual_evidence_refs=(),
        temporal_caveats=(),
        abstention_code=abstention,
        derivation_version=DERIVATION_VERSION,
        policy_id=POLICY_ID,
        policy_fingerprint=POLICY_FINGERPRINT,
    )


def _journal_body(
    *,
    options: tuple[str, ...] = ("Остаться", "Уйти"),
    chosen: str = "Остаться",
) -> str:
    return (
        "## Situation\n\nНужно принять reviewed decision.\n\n"
        "## Available options\n\n"
        + "\n".join(f"- {item}" for item in options)
        + "\n\n## Information known at decision time\n\nТолько текущие данные.\n\n"
        "## Criteria\n\n- Риск\n\n"
        f"## Chosen option\n\n{chosen}\n\n"
        "## Reasons\n\nПричина выбора.\n\n"
        "## Confidence\n\nСредняя.\n\n"
        "## Expected result\n\nОжидаемый результат.\n\n"
        "## Actual result\n\n"
        "## Reassessment\n\n"
    )


def _write_journal(
    vault: Path,
    *,
    options: tuple[str, ...] = ("Остаться", "Уйти"),
    chosen: str = "Остаться",
    evidence_at: str = "2026-01-01T13:00:00+00:00",
    created: str = "2026-01-01T12:30:00+00:00",
    note_id: str = DECISION_ID,
) -> Path:
    front_matter = (
        "---\n"
        f"id: {note_id}\n"
        "type: project\n"
        f"created: {created}\n"
        "second_brain_personal_memory: 1\n"
        "evidence_kind: observed_decision\n"
        "self_kind: decision\n"
        f"evidence_at: {evidence_at}\n"
        "evidence_at_precision: exact\n"
        "domain: decisions\n"
        "tags: [stage9c]\n"
        "links: []\n"
        "---\n"
    )
    return write_note(
        vault,
        "10 Projects/Decision.md",
        front_matter + _journal_body(options=options, chosen=chosen),
    )


@dataclass
class _Stage6:
    result: SimulateMeResult

    def execute(self, request: SimulateMeRequest) -> SimulateMeResult:
        del request
        return self.result


def _capture(
    store: ProspectiveAuditStore,
    operation_id: str,
    *,
    result: SimulateMeResult | None = None,
    created_at: datetime = AUDIT_CREATED,
) -> AuditLogEnvelopeV1:
    BuildProspectiveAudit(
        _Stage6(result or _result()),
        store,
        clock=lambda: created_at,
    ).execute(_request(), operation_id)
    return store.read_events()[-1]


def _mapping(index: int = 0, *, audit_option_id: str = "audit-a") -> ProspectiveOptionMappingV1:
    option = ("Остаться", "Уйти")[index]
    return ProspectiveOptionMappingV1(
        audit_option_id=audit_option_id,
        decision_option_index=index,
        decision_option_fingerprint=fingerprint_decision_option(option),
    )


def _linker(vault: Path, store: ProspectiveAuditStore) -> BuildProspectiveDecisionLink:
    return BuildProspectiveDecisionLink(
        store,
        FileSystemVaultReader(vault),
        clock=lambda: LINKED_AT,
    )


def _counts(result: ProspectiveCalibrationResultV1, field: str) -> dict[str, int]:
    return {item.code: item.count for item in getattr(result, field)}


def test_empty_generation_has_exact_zero_dto_and_fixed_code_arrays(tmp_path: Path) -> None:
    store = ProspectiveAuditStore(tmp_path / "operational-audit", clock=lambda: LINKED_AT)

    result = BuildProspectiveCalibration(store).execute(ProspectiveCalibrationRequestV1())

    assert result.metrics == ProspectiveCalibrationMetricsV1(
        audited_operations=0,
        predictions=0,
        abstentions=0,
        linked_actual_decisions=0,
        pending_unlinked_events=0,
        invalid_linkage_events=0,
        unavailable_linkage_events=0,
        exact_option_matches=0,
        mismatches=0,
        coverage=None,
        actual_linkage_coverage=None,
        evaluated_prediction_coverage=None,
        accuracy_non_abstained=None,
    )
    assert [item.code for item in result.invalid_linkage_by_code] == [
        code.value for code in InvalidCode
    ]
    assert [item.code for item in result.unavailable_linkage_by_code] == [
        code.value for code in UnavailableCode
    ]
    assert all(item.count == 0 for item in result.invalid_linkage_by_code)
    assert all(item.count == 0 for item in result.unavailable_linkage_by_code)
    assert len(serialize_prospective_calibration_result(result)) < 65_536


def test_mixed_population_counts_exact_ratios_and_abstention_semantics(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault)
    store = ProspectiveAuditStore(tmp_path / "operational-audit", clock=lambda: LINKED_AT)
    exact = _capture(store, "operation-exact")
    mismatch = _capture(store, "operation-mismatch")
    abstention = _capture(
        store,
        "operation-abstention",
        result=_result(
            kind=SimulateMeResultKind.ABSTENTION,
            abstention=SimulateMeAbstentionCode.MULTIPLE_OPTIONS_SUPPORTED,
        ),
    )
    _capture(store, "operation-pending")
    linker = _linker(vault, store)

    linker.execute(exact.event.event_id, DECISION_ID, (_mapping(),))
    linker.execute(
        mismatch.event.event_id,
        DECISION_ID,
        (_mapping(1), _mapping(0, audit_option_id="audit-b")),
    )
    linker.execute(abstention.event.event_id, DECISION_ID, ())

    result = BuildProspectiveCalibration(store, FileSystemVaultReader(vault)).execute()

    assert result.metrics.audited_operations == 4
    assert result.metrics.predictions == 3
    assert result.metrics.abstentions == 1
    assert result.metrics.linked_actual_decisions == 3
    assert result.metrics.pending_unlinked_events == 1
    assert result.metrics.invalid_linkage_events == 0
    assert result.metrics.unavailable_linkage_events == 0
    assert result.metrics.exact_option_matches == 1
    assert result.metrics.mismatches == 1
    assert result.metrics.coverage == ProspectiveCalibrationRatioV1(3, 4)
    assert result.metrics.actual_linkage_coverage == ProspectiveCalibrationRatioV1(3, 4)
    assert result.metrics.evaluated_prediction_coverage == ProspectiveCalibrationRatioV1(2, 3)
    assert result.metrics.accuracy_non_abstained == ProspectiveCalibrationRatioV1(1, 2)


def test_link_revalidation_drift_is_invalid_and_delete_is_unavailable(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    note = _write_journal(vault)
    store = ProspectiveAuditStore(tmp_path / "operational-audit", clock=lambda: LINKED_AT)
    event = _capture(store, "operation-drift")
    linker = _linker(vault, store)
    linker.execute(event.event.event_id, DECISION_ID, (_mapping(),))

    note.write_text(
        note.read_text(encoding="utf-8").replace(
            "## Chosen option\n\nОстаться", "## Chosen option\n\nУйти"
        ),
        encoding="utf-8",
    )
    invalid = BuildProspectiveCalibration(store, FileSystemVaultReader(vault)).execute()
    assert invalid.metrics.invalid_linkage_events == 1
    assert invalid.metrics.mismatches == 0
    assert _counts(invalid, "invalid_linkage_by_code")["decision_record_changed"] == 1

    note.unlink()
    unavailable = BuildProspectiveCalibration(store, FileSystemVaultReader(vault)).execute()
    assert unavailable.metrics.invalid_linkage_events == 0
    assert unavailable.metrics.unavailable_linkage_events == 1
    assert _counts(unavailable, "unavailable_linkage_by_code")["decision_target_unavailable"] == 1


def test_canonical_scan_failure_is_an_unavailable_linkage_not_a_mismatch(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault)
    store = ProspectiveAuditStore(tmp_path / "operational-audit", clock=lambda: LINKED_AT)
    event = _capture(store, "operation-scan-failure")
    _linker(vault, store).execute(event.event.event_id, DECISION_ID, (_mapping(),))

    class _BrokenReader:
        def scan(self) -> object:
            raise RuntimeError("private scan details must not escape")

    result = BuildProspectiveCalibration(store, _BrokenReader()).execute()  # type: ignore[arg-type]

    assert result.metrics.unavailable_linkage_events == 1
    assert result.metrics.mismatches == 0
    assert _counts(result, "unavailable_linkage_by_code")["canonical_scan_unavailable"] == 1


@pytest.mark.parametrize(
    ("state", "code"),
    [(ProspectiveDecisionLinkStateV1.LINK_INVALID, code.value) for code in InvalidCode]
    + [(ProspectiveDecisionLinkStateV1.LINK_UNAVAILABLE, code.value) for code in UnavailableCode],
)
def test_all_fixed_linkage_codes_are_counted_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: ProspectiveDecisionLinkStateV1,
    code: str,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault)
    store = ProspectiveAuditStore(tmp_path / "operational-audit", clock=lambda: LINKED_AT)
    event = _capture(store, "operation-code")
    _linker(vault, store).execute(event.event.event_id, DECISION_ID, (_mapping(),))

    def fake_revalidate(
        event_envelope: object,
        link: object,
        reader: object,
    ) -> ProspectiveDecisionLinkVerificationV1:
        del event_envelope, link, reader
        return ProspectiveDecisionLinkVerificationV1(state, reason_code=code)

    monkeypatch.setattr(prospective_audit, "_revalidate_calibration_link", fake_revalidate)
    result = BuildProspectiveCalibration(store).execute()

    if state is ProspectiveDecisionLinkStateV1.LINK_INVALID:
        assert result.metrics.invalid_linkage_events == 1
        assert result.metrics.unavailable_linkage_events == 0
        assert _counts(result, "invalid_linkage_by_code")[code] == 1
    else:
        assert result.metrics.invalid_linkage_events == 0
        assert result.metrics.unavailable_linkage_events == 1
        assert _counts(result, "unavailable_linkage_by_code")[code] == 1


def test_retention_boundary_tombstone_and_reset_are_read_only_calibration_inputs(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault)
    store = ProspectiveAuditStore(tmp_path / "operational-audit", clock=lambda: LINKED_AT)
    event = _capture(store, "operation-retention")
    linker = _linker(vault, store)
    linker.execute(event.event.event_id, DECISION_ID, (_mapping(),))
    before_reset = snapshot_tree(vault)

    active = BuildProspectiveCalibration(store, FileSystemVaultReader(vault)).execute(
        now=AUDIT_CREATED + RETENTION - timedelta(microseconds=1)
    )
    assert active.metrics.audited_operations == 1
    assert active.metrics.linked_actual_decisions == 1

    expired = BuildProspectiveCalibration(store, FileSystemVaultReader(vault)).execute(
        now=AUDIT_CREATED + RETENTION
    )
    assert expired.metrics.audited_operations == 0
    assert expired.metrics.coverage is None
    assert expired.metrics.accuracy_non_abstained is None

    linker.invalidate(event.event.event_id)
    tombstoned = BuildProspectiveCalibration(store, FileSystemVaultReader(vault)).execute(
        now=AUDIT_CREATED + RETENTION - timedelta(microseconds=1)
    )
    assert tombstoned.metrics.pending_unlinked_events == 1
    assert tombstoned.metrics.linked_actual_decisions == 0

    store.reset()
    reset = BuildProspectiveCalibration(store).execute()
    assert reset.metrics.audited_operations == 0
    assert snapshot_tree(vault) == before_reset


def test_corrupt_store_returns_no_partial_result(tmp_path: Path) -> None:
    store = ProspectiveAuditStore(tmp_path / "operational-audit", clock=lambda: LINKED_AT)
    _capture(store, "operation-corrupt")
    store.events_path.write_bytes(
        store.events_path.read_bytes().replace(b'"sequence":1', b'"sequence":2', 1)
    )

    with pytest.raises(ProspectiveCalibrationUnavailableError) as raised:
        BuildProspectiveCalibration(store).execute()
    assert raised.value.code == ProspectiveCalibrationErrorCodeV1.UNAVAILABLE.value
    assert "sequence" not in str(raised.value)
    assert "operation-corrupt" not in str(raised.value)


def test_corrupt_link_store_returns_no_partial_result(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault)
    store = ProspectiveAuditStore(tmp_path / "operational-audit", clock=lambda: LINKED_AT)
    event = _capture(store, "operation-corrupt-link")
    _linker(vault, store).execute(event.event.event_id, DECISION_ID, (_mapping(),))
    store.links_path.write_bytes(
        store.links_path.read_bytes().replace(b'"sequence":2', b'"sequence":1', 1)
    )

    with pytest.raises(ProspectiveCalibrationUnavailableError) as raised:
        BuildProspectiveCalibration(store, FileSystemVaultReader(vault)).execute()
    assert raised.value.code == ProspectiveCalibrationErrorCodeV1.UNAVAILABLE.value
    assert "sequence" not in str(raised.value)
    assert "operation-corrupt-link" not in str(raised.value)


def test_serialization_has_only_exact_private_safe_fields(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault)
    store = ProspectiveAuditStore(tmp_path / "operational-audit", clock=lambda: LINKED_AT)
    event = _capture(store, "operation-privacy")
    _linker(vault, store).execute(event.event.event_id, DECISION_ID, (_mapping(),))
    payload = serialize_prospective_calibration_result(
        BuildProspectiveCalibration(store, FileSystemVaultReader(vault)).execute()
    )

    assert b"operation-privacy" not in payload
    assert event.event.event_id.encode() not in payload
    assert DECISION_ID.encode() not in payload
    assert b"private query must not be stored" not in payload
    assert "Остаться".encode() not in payload
    assert b"Decision.md" not in payload
    assert b"audit-a" not in payload
    assert b"decision_option_index" not in payload
    assert b"provider" not in payload


def test_impossible_result_identity_and_result_size_fail_closed() -> None:
    zero_counts = tuple(ProspectiveCalibrationCountV1(code.value, 0) for code in InvalidCode)
    zero_unavailable = tuple(
        ProspectiveCalibrationCountV1(code.value, 0) for code in UnavailableCode
    )
    zero_metrics = ProspectiveCalibrationMetricsV1(
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        None,
        None,
        None,
        None,
    )
    result = ProspectiveCalibrationResultV1(
        "prospective-audit-calibration-v1",
        "prospective-calibration-v1",
        "prospective-simulate-me-explicit-link-v1",
        "sha256:f6a3229ecdc547bb16f9426d1b78d6e5d06e2355954ca30a37be90bdaec1bbc1",
        "prospective-audit-retention-180d-v1",
        zero_metrics,
        zero_counts,
        zero_unavailable,
    )
    assert validate_prospective_calibration_result(result) == result

    huge = 10**100000
    too_large = ProspectiveCalibrationResultV1(
        result.contract_version,
        result.derivation_version,
        result.policy_id,
        result.policy_fingerprint,
        result.retention_policy,
        ProspectiveCalibrationMetricsV1(
            huge,
            0,
            0,
            0,
            huge,
            0,
            0,
            0,
            0,
            ProspectiveCalibrationRatioV1(0, huge),
            ProspectiveCalibrationRatioV1(0, huge),
            None,
            None,
        ),
        zero_counts,
        zero_unavailable,
    )
    with pytest.raises(ValueError):
        validate_prospective_calibration_result(too_large)

    valid_huge = ProspectiveCalibrationResultV1(
        result.contract_version,
        result.derivation_version,
        result.policy_id,
        result.policy_fingerprint,
        result.retention_policy,
        ProspectiveCalibrationMetricsV1(
            huge,
            huge,
            0,
            huge,
            0,
            0,
            0,
            huge,
            0,
            ProspectiveCalibrationRatioV1(huge, huge),
            ProspectiveCalibrationRatioV1(huge, huge),
            ProspectiveCalibrationRatioV1(huge, huge),
            ProspectiveCalibrationRatioV1(huge, huge),
        ),
        zero_counts,
        zero_unavailable,
    )
    with pytest.raises(ProspectiveCalibrationResultTooLargeError):
        serialize_prospective_calibration_result(valid_huge)
