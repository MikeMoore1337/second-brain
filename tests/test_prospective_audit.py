"""Deterministic Stage 9A prospective audit core tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from second_brain.application.prospective_audit import (
    CAPTURE_MODE,
    CONTRACT_VERSION,
    EVENT_TYPE,
    POLICY_FINGERPRINT,
    POLICY_ID,
    RETENTION,
    BuildProspectiveAudit,
    ProspectiveAuditAbstentionCode,
    ProspectiveAuditError,
    ProspectiveAuditErrorCode,
    ProspectiveAuditEventV1,
    ProspectiveAuditIdempotencyConflictError,
    ProspectiveAuditInvalidRequestError,
    ProspectiveAuditResultKind,
    ProspectiveAuditResultV1,
    ProspectiveAuditStore,
    ProspectiveAuditStoreCorruptError,
    ProspectiveAuditStoreUnavailableError,
    build_prospective_audit_event,
    canonical_json_bytes,
    fingerprint_operation_id,
)
from second_brain.application.simulate_me import (
    DERIVATION_VERSION,
    SimulateMeAbstentionCode,
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResult,
    SimulateMeResultKind,
)
from second_brain.application.simulate_me import (
    POLICY_FINGERPRINT as STAGE6_POLICY_FINGERPRINT,
)
from second_brain.application.simulate_me import (
    POLICY_ID as STAGE6_POLICY_ID,
)

BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _request() -> SimulateMeRequest:
    return SimulateMeRequest("private query must not be stored", (SimulateMeOption("a", "A"),))


def _result(
    *,
    kind: SimulateMeResultKind = SimulateMeResultKind.PREDICTION,
    selected: SimulateMeOption | None = None,
    abstention: SimulateMeAbstentionCode | None = None,
) -> SimulateMeResult:
    if selected is None and kind is SimulateMeResultKind.PREDICTION:
        selected = SimulateMeOption("a", "A")
    if kind is SimulateMeResultKind.ABSTENTION and abstention is None:
        abstention = SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE
    return SimulateMeResult(
        kind,
        selected,
        (),
        (),
        (),
        abstention,
        DERIVATION_VERSION,
        STAGE6_POLICY_ID,
        STAGE6_POLICY_FINGERPRINT,
    )


def _event(
    operation_id: str = "operation-1",
    *,
    created_at: datetime = BASE_TIME,
    event_id: str = "0198f4c5-6a00-7000-8000-000000000101",
    result: SimulateMeResult | None = None,
) -> ProspectiveAuditEventV1:
    return build_prospective_audit_event(
        _request(),
        result or _result(),
        operation_id,
        created_at=created_at,
        event_id=event_id,
    )


def test_event_has_exact_identity_and_privacy_safe_serialization() -> None:
    event = _event()
    payload = canonical_json_bytes(event.as_dict())

    assert event.contract_version == CONTRACT_VERSION
    assert event.event_type == EVENT_TYPE
    assert event.capture_mode == CAPTURE_MODE
    assert event.policy_id == POLICY_ID
    assert event.policy_fingerprint == POLICY_FINGERPRINT
    assert b"private query must not be stored" not in payload
    assert b"evidence_refs" not in payload
    assert b"note_ids" not in payload
    assert b"\xef\xbb\xbf" not in payload
    assert canonical_json_bytes(event.as_dict()) == payload


@pytest.mark.parametrize(
    "code",
    (
        SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE,
        SimulateMeAbstentionCode.MULTIPLE_OPTIONS_SUPPORTED,
        SimulateMeAbstentionCode.INSUFFICIENT_OR_INVALID_CURRENT_CONTEXT,
    ),
)
def test_all_valid_abstentions_are_auditable(code: SimulateMeAbstentionCode) -> None:
    event = _event(result=_result(kind=SimulateMeResultKind.ABSTENTION, abstention=code))

    assert event.result.kind is ProspectiveAuditResultKind.ABSTENTION
    assert event.result.predicted_option_id is None
    assert event.result.abstention_code is ProspectiveAuditAbstentionCode(code.value)


def test_invalid_result_combination_is_rejected() -> None:
    with pytest.raises(ValueError):
        ProspectiveAuditResultV1(
            ProspectiveAuditResultKind.PREDICTION,
            None,
            ProspectiveAuditAbstentionCode.NO_MATCHING_EVIDENCE,
        )


def test_store_initial_generation_append_chain_and_idempotency(tmp_path: Path) -> None:
    store = ProspectiveAuditStore(tmp_path / "audit")
    first = _event()
    second = _event(
        operation_id="operation-2",
        event_id="0198f4c5-6a00-7000-8000-000000000102",
    )

    assert store.read_manifest().last_sequence == 0
    assert store.append(first) == first
    assert store.append(second) == second
    records = store.read_events()
    assert [record.sequence for record in records] == [1, 2]
    assert records[0].previous_record_digest is None
    assert records[1].previous_record_digest == records[0].record_digest
    assert store.append(first) == first
    assert len(store.read_events()) == 2


def test_incompatible_operation_retry_fails_closed(tmp_path: Path) -> None:
    store = ProspectiveAuditStore(tmp_path / "audit")
    store.append(_event())

    with pytest.raises(ProspectiveAuditIdempotencyConflictError) as raised:
        store.append(
            _event(
                result=_result(kind=SimulateMeResultKind.ABSTENTION),
                event_id="0198f4c5-6a00-7000-8000-000000000102",
            )
        )
    assert raised.value.code == ProspectiveAuditErrorCode.IDEMPOTENCY_CONFLICT.value
    assert len(store.read_events()) == 1


def test_modified_record_blocks_read_and_append(tmp_path: Path) -> None:
    store = ProspectiveAuditStore(tmp_path / "audit")
    store.append(_event())
    raw = store.events_path.read_bytes()
    store.events_path.write_bytes(raw.replace(b'"sequence":1', b'"sequence":2', 1))

    with pytest.raises(ProspectiveAuditStoreCorruptError):
        store.read_events()
    with pytest.raises(ProspectiveAuditStoreCorruptError):
        store.append(
            _event(
                operation_id="operation-2",
                event_id="0198f4c5-6a00-7000-8000-000000000102",
            )
        )


def test_truncated_last_record_and_manifest_corruption_fail_closed(tmp_path: Path) -> None:
    store = ProspectiveAuditStore(tmp_path / "audit")
    store.append(_event())
    store.events_path.write_bytes(store.events_path.read_bytes()[:-1])

    with pytest.raises(ProspectiveAuditStoreCorruptError):
        store.read_events()

    store.reset()
    store.manifest_path.write_bytes(b'{"format_version":"prospective-audit-store-v1"}')
    with pytest.raises(ProspectiveAuditStoreCorruptError):
        store.read_events()


def test_reset_recovers_explicitly_from_corruption(tmp_path: Path) -> None:
    store = ProspectiveAuditStore(tmp_path / "audit")
    store.append(_event())
    store.manifest_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ProspectiveAuditStoreCorruptError):
        store.read_manifest()
    manifest = store.reset()
    assert manifest.last_sequence == 0
    assert store.read_events() == ()


def test_retention_uses_exact_180_day_cutoff(tmp_path: Path) -> None:
    store = ProspectiveAuditStore(tmp_path / "audit")
    store.append(_event(created_at=BASE_TIME))

    assert len(store.active_events(now=BASE_TIME + RETENTION - timedelta(microseconds=1))) == 1
    assert store.active_events(now=BASE_TIME + RETENTION) == ()
    assert store.purge_expired(now=BASE_TIME + RETENTION) == 1
    assert store.read_events() == ()


@dataclass
class _FakeStage6:
    result: SimulateMeResult
    calls: int = 0

    def execute(self, request: SimulateMeRequest) -> SimulateMeResult:
        del request
        self.calls += 1
        return self.result


def test_foreground_wrapper_calls_stage6_once_and_commits(tmp_path: Path) -> None:
    fake = _FakeStage6(_result())
    store = ProspectiveAuditStore(tmp_path / "audit")
    result = BuildProspectiveAudit(fake, store, clock=lambda: BASE_TIME).execute(
        _request(), "operation-1"
    )

    assert fake.calls == 1
    assert result == fake.result
    assert len(store.read_events()) == 1


def test_concurrent_duplicate_and_unique_operations_are_serialized(tmp_path: Path) -> None:
    store = ProspectiveAuditStore(tmp_path / "audit")
    events = [
        _event(
            operation_id=f"operation-{index}",
            event_id=f"0198f4c5-6a00-7000-8000-{index + 200:012d}",
        )
        for index in range(4)
    ]
    duplicate = [
        _event(event_id=f"0198f4c5-6a00-7000-8000-{index + 300:012d}") for index in range(4)
    ]

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(store.append, [*events, *duplicate]))

    records = store.read_events()
    assert [record.sequence for record in records] == [1, 2, 3, 4]
    assert len({record.event.operation_id_fingerprint for record in records}) == 4


@dataclass
class _Cancelled:
    value: bool = True

    def is_cancelled(self) -> bool:
        return self.value


def test_cancellation_before_stage6_creates_no_event(tmp_path: Path) -> None:
    fake = _FakeStage6(_result())
    store = ProspectiveAuditStore(tmp_path / "audit")

    with pytest.raises(ProspectiveAuditError):
        BuildProspectiveAudit(fake, store).execute(
            _request(), "operation-1", cancellation=_Cancelled()
        )
    assert fake.calls == 0
    assert store.read_events() == ()


def test_wrapper_invalid_request_does_not_call_stage6(tmp_path: Path) -> None:
    fake = _FakeStage6(_result())
    store = ProspectiveAuditStore(tmp_path / "audit")
    invalid = SimulateMeRequest("query", ())

    with pytest.raises(ProspectiveAuditInvalidRequestError):
        BuildProspectiveAudit(fake, store).execute(invalid, "operation-1")
    assert fake.calls == 0
    assert store.read_events() == ()


def test_wrapper_commit_failure_has_no_success_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeStage6(_result())
    store = ProspectiveAuditStore(tmp_path / "audit")

    def fail_manifest(_manifest: object) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(store, "_write_manifest_atomic", fail_manifest)
    with pytest.raises(ProspectiveAuditStoreUnavailableError):
        BuildProspectiveAudit(fake, store).execute(_request(), "operation-1")
    assert fake.calls == 1


def test_operation_fingerprint_is_deterministic_and_raw_token_absent() -> None:
    assert fingerprint_operation_id("  operation-1  ") == fingerprint_operation_id("operation-1")
    assert b"operation-1" not in canonical_json_bytes(
        {"fingerprint": fingerprint_operation_id("operation-1")}
    )
