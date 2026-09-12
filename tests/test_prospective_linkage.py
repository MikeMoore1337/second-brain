"""Focused deterministic Stage 9B explicit Decision Journal linkage tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.prospective_audit import (
    AuditLogEnvelopeV1,
    BuildProspectiveAudit,
    BuildProspectiveDecisionLink,
    ProspectiveAuditLinkInvalidError,
    ProspectiveAuditLinkReasonCode,
    ProspectiveAuditLinkUnavailableError,
    ProspectiveAuditResultKind,
    ProspectiveAuditStore,
    ProspectiveAuditStoreCorruptError,
    ProspectiveDecisionLinkStateV1,
    ProspectiveOptionMappingV1,
    fingerprint_decision_option,
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
DECISION_ID = "0198f4c5-6a00-7000-8000-000000000201"
EVENT_ID = "0198f4c5-6a00-7000-8000-000000000202"


def _request() -> SimulateMeRequest:
    return SimulateMeRequest(
        "private query must not be stored",
        (SimulateMeOption("audit-a", "Audit A"), SimulateMeOption("audit-b", "Audit B")),
    )


def _result(
    *,
    kind: SimulateMeResultKind = SimulateMeResultKind.PREDICTION,
    selected: SimulateMeOption | None = None,
    abstention: SimulateMeAbstentionCode | None = None,
) -> SimulateMeResult:
    if kind is SimulateMeResultKind.PREDICTION and selected is None:
        selected = _request().options[0]
    if kind is SimulateMeResultKind.ABSTENTION and abstention is None:
        abstention = SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE
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
    path = vault / "10 Projects" / "Decision.md"
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
        "tags: [stage9b]\n"
        "links: []\n"
        "---\n"
    )
    write_note(
        vault,
        "10 Projects/Decision.md",
        front_matter + _journal_body(options=options, chosen=chosen),
    )
    return path


def _event(
    store: ProspectiveAuditStore, *, result: SimulateMeResult | None = None
) -> AuditLogEnvelopeV1:
    event = BuildProspectiveAudit(
        type("Stage6", (), {"execute": lambda _self, _request: result or _result()})(),
        store,
        clock=lambda: AUDIT_CREATED,
    ).execute(
        _request(),
        "operation-240",
    )
    del event
    return store.read_events()[0]


def _build_store_and_event(
    tmp_path: Path, *, result: SimulateMeResult | None = None
) -> tuple[ProspectiveAuditStore, AuditLogEnvelopeV1]:
    store = ProspectiveAuditStore(tmp_path / "operational-audit", clock=lambda: LINKED_AT)
    envelope = _event(store, result=result)
    return store, envelope


def _mapping(index: int = 0, *, audit_option_id: str = "audit-a") -> ProspectiveOptionMappingV1:
    option = ("Остаться", "Уйти")[index]
    return ProspectiveOptionMappingV1(
        audit_option_id=audit_option_id,
        decision_option_index=index,
        decision_option_fingerprint=fingerprint_decision_option(option),
    )


def _linker(
    tmp_path: Path, vault: Path, store: ProspectiveAuditStore
) -> BuildProspectiveDecisionLink:
    return BuildProspectiveDecisionLink(
        store,
        FileSystemVaultReader(vault),
        clock=lambda: LINKED_AT,
    )


def test_valid_prediction_link_rereads_canonical_target_and_is_durable(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault)
    store, event = _build_store_and_event(tmp_path)
    before = snapshot_tree(vault)

    link = _linker(tmp_path, vault, store).execute(
        event.event.event_id,
        DECISION_ID,
        (_mapping(),),
    )

    assert link.audit_event_id == event.event.event_id
    assert link.decision_id == DECISION_ID
    assert link.actual_chosen_option_index == 0
    assert link.mapping_basis == "owner-explicit-v1"
    assert link.mapping == (_mapping(),)
    records = store.read_link_envelopes()
    assert len(records) == 1
    assert records[0].sequence > event.sequence
    assert records[0].link == link
    assert store.links_path.read_bytes().find("Остаться".encode()) == -1
    assert store.links_path.read_bytes().find(b"Decision.md") == -1
    assert snapshot_tree(vault) == before
    assert _linker(tmp_path, vault, store).verify(event.event.event_id).state is (
        ProspectiveDecisionLinkStateV1.LINKED_VALID
    )


def test_valid_abstention_link_has_no_prediction_mapping_but_snapshots_actual(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault)
    store, event = _build_store_and_event(
        tmp_path,
        result=_result(
            kind=SimulateMeResultKind.ABSTENTION,
            abstention=SimulateMeAbstentionCode.MULTIPLE_OPTIONS_SUPPORTED,
        ),
    )

    link = _linker(tmp_path, vault, store).execute(event.event.event_id, DECISION_ID, ())

    assert event.event.result.kind is ProspectiveAuditResultKind.ABSTENTION
    assert link.mapping == ()
    assert link.actual_chosen_option_index == 0


@pytest.mark.parametrize(
    ("mapping", "reason"),
    [
        ((_mapping(), _mapping()), ProspectiveAuditLinkReasonCode.OPTION_MAPPING_INVALID),
        (
            (
                ProspectiveOptionMappingV1(
                    "unknown",
                    0,
                    fingerprint_decision_option("Остаться"),
                ),
            ),
            ProspectiveAuditLinkReasonCode.OPTION_MAPPING_INVALID,
        ),
        (
            (_mapping(1), _mapping(1, audit_option_id="audit-b")),
            ProspectiveAuditLinkReasonCode.OPTION_MAPPING_INVALID,
        ),
        (
            (
                _mapping(),
                ProspectiveOptionMappingV1(
                    "not-an-event-option",
                    1,
                    fingerprint_decision_option("Уйти"),
                ),
            ),
            ProspectiveAuditLinkReasonCode.OPTION_MAPPING_INVALID,
        ),
        (
            (
                ProspectiveOptionMappingV1(
                    "audit-a",
                    0,
                    fingerprint_decision_option("Уйти"),
                ),
            ),
            ProspectiveAuditLinkReasonCode.LINK_FINGERPRINT_MISMATCH,
        ),
    ],
)
def test_invalid_or_non_injective_mapping_fails_closed(
    tmp_path: Path,
    mapping: tuple[ProspectiveOptionMappingV1, ...],
    reason: ProspectiveAuditLinkReasonCode,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault)
    store, event = _build_store_and_event(tmp_path)

    with pytest.raises(ProspectiveAuditLinkInvalidError) as raised:
        _linker(tmp_path, vault, store).execute(event.event.event_id, DECISION_ID, mapping)

    assert raised.value.reason_code == reason.value
    assert store.read_link_envelopes() == ()


def test_prediction_requires_mapping_for_actual_chosen_option(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault)
    store, event = _build_store_and_event(tmp_path)
    mapping = (
        ProspectiveOptionMappingV1(
            "audit-a",
            1,
            fingerprint_decision_option("Уйти"),
        ),
    )

    with pytest.raises(ProspectiveAuditLinkInvalidError) as raised:
        _linker(tmp_path, vault, store).execute(event.event.event_id, DECISION_ID, mapping)

    assert raised.value.reason_code == ProspectiveAuditLinkReasonCode.CHOSEN_OPTION_UNMAPPED.value


@pytest.mark.parametrize(
    ("created", "evidence_at", "link_clock", "reason"),
    [
        (
            "2026-01-01T12:30:00+00:00",
            "2026-01-01T12:00:00+00:00",
            LINKED_AT,
            ProspectiveAuditLinkReasonCode.DECISION_PRECEDES_PREDICTION,
        ),
        (
            "2026-01-01T12:00:00+00:00",
            "2026-01-01T13:00:00+00:00",
            LINKED_AT,
            ProspectiveAuditLinkReasonCode.DECISION_NOTE_CREATED_BEFORE_PREDICTION,
        ),
        (
            "2026-01-01T12:30:00+00:00",
            "2026-01-01T13:00:00+00:00",
            datetime(2026, 1, 1, 12, 45, tzinfo=UTC),
            ProspectiveAuditLinkReasonCode.DECISION_TIME_INVALID,
        ),
    ],
)
def test_temporal_gate_is_strict_and_never_backdates(
    tmp_path: Path,
    created: str,
    evidence_at: str,
    link_clock: datetime,
    reason: ProspectiveAuditLinkReasonCode,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault, created=created, evidence_at=evidence_at)
    store, event = _build_store_and_event(tmp_path)

    with pytest.raises(ProspectiveAuditLinkInvalidError) as raised:
        BuildProspectiveDecisionLink(
            store,
            FileSystemVaultReader(vault),
            clock=lambda: link_clock,
        ).execute(event.event.event_id, DECISION_ID, (_mapping(),))

    assert raised.value.reason_code == reason.value
    assert store.read_link_envelopes() == ()


def test_non_exact_evidence_time_is_rejected(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault, evidence_at="unknown")
    store, event = _build_store_and_event(tmp_path)
    with pytest.raises(ProspectiveAuditLinkInvalidError) as raised:
        _linker(tmp_path, vault, store).execute(event.event.event_id, DECISION_ID, (_mapping(),))
    assert raised.value.reason_code == ProspectiveAuditLinkReasonCode.DECISION_TIME_INVALID.value


def test_duplicate_decision_identity_fails_closed(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    note = _write_journal(vault)
    write_note(vault, "10 Projects/Decision-copy.md", note.read_text(encoding="utf-8"))
    store, event = _build_store_and_event(tmp_path)

    with pytest.raises(ProspectiveAuditLinkInvalidError) as raised:
        _linker(tmp_path, vault, store).execute(event.event.event_id, DECISION_ID, (_mapping(),))

    assert raised.value.reason_code == ProspectiveAuditLinkReasonCode.DECISION_IDENTITY_CONFLICT
    assert store.read_link_envelopes() == ()


def test_idempotent_retry_and_concurrent_duplicate_are_one_link(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault)
    store, event = _build_store_and_event(tmp_path)

    builder = _linker(tmp_path, vault, store)
    first = builder.execute(event.event.event_id, DECISION_ID, (_mapping(),))
    second = builder.execute(event.event.event_id, DECISION_ID, (_mapping(),))
    assert second == first

    with ThreadPoolExecutor(max_workers=6) as executor:
        concurrent = list(
            executor.map(
                lambda _item: builder.execute(event.event.event_id, DECISION_ID, (_mapping(),)),
                range(6),
            )
        )
    assert all(item == first for item in concurrent)
    assert len(store.read_link_envelopes()) == 1


def test_concurrent_conflicting_link_fails_closed(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault)
    store, event = _build_store_and_event(tmp_path)
    builder = _linker(tmp_path, vault, store)

    first = builder.execute(event.event.event_id, DECISION_ID, (_mapping(),))
    conflicting = (
        ProspectiveOptionMappingV1(
            "audit-a",
            1,
            fingerprint_decision_option("Уйти"),
        ),
        ProspectiveOptionMappingV1(
            "audit-b",
            0,
            fingerprint_decision_option("Остаться"),
        ),
    )
    with pytest.raises(ProspectiveAuditLinkInvalidError) as raised:
        builder.execute(event.event.event_id, DECISION_ID, conflicting)
    assert raised.value.reason_code == ProspectiveAuditLinkReasonCode.LINK_OPERATION_CONFLICT.value
    assert store.read_link_envelopes()[0].link == first


def test_target_drift_is_invalid_and_delete_is_unavailable(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    note = _write_journal(vault)
    store, event = _build_store_and_event(tmp_path)
    builder = _linker(tmp_path, vault, store)
    builder.execute(event.event.event_id, DECISION_ID, (_mapping(),))

    changed = note.read_text(encoding="utf-8").replace("Средняя.", "Высокая.")
    note.write_text(changed, encoding="utf-8")
    drift = builder.verify(event.event.event_id)
    assert drift.state is ProspectiveDecisionLinkStateV1.LINKED_VALID

    changed = note.read_text(encoding="utf-8").replace(
        "## Chosen option\n\nОстаться", "## Chosen option\n\nУйти"
    )
    note.write_text(changed, encoding="utf-8")
    drift = builder.verify(event.event.event_id)
    assert drift.state is ProspectiveDecisionLinkStateV1.LINK_INVALID
    assert drift.reason_code == ProspectiveAuditLinkReasonCode.DECISION_RECORD_CHANGED.value

    note.unlink()
    unavailable = builder.verify(event.event.event_id)
    assert unavailable.state is ProspectiveDecisionLinkStateV1.LINK_UNAVAILABLE


def test_explicit_correction_appends_tombstone_and_new_link_id(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    note = _write_journal(vault)
    store, event = _build_store_and_event(tmp_path)
    builder = _linker(tmp_path, vault, store)
    old = builder.execute(event.event.event_id, DECISION_ID, (_mapping(),))

    note.write_text(
        note.read_text(encoding="utf-8").replace(
            "## Chosen option\n\nОстаться", "## Chosen option\n\nУйти"
        ),
        encoding="utf-8",
    )
    new = builder.correct(event.event.event_id, DECISION_ID, (_mapping(1),))

    assert new.link_id != old.link_id
    records = store.read_link_envelopes()
    assert len(records) == 3
    assert records[0].link == old
    assert records[1].tombstone is not None
    assert records[1].tombstone.supersedes_link_id == old.link_id
    assert records[2].link == new
    assert builder.verify(event.event.event_id).state is ProspectiveDecisionLinkStateV1.LINKED_VALID


def test_store_corruption_blocks_link_reads_and_writes(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault)
    store, event = _build_store_and_event(tmp_path)
    _linker(tmp_path, vault, store).execute(event.event.event_id, DECISION_ID, (_mapping(),))
    store.links_path.write_bytes(
        store.links_path.read_bytes().replace(b'"sequence":2', b'"sequence":1', 1)
    )

    with pytest.raises(ProspectiveAuditStoreCorruptError) as raised:
        store.read_link_envelopes()
    assert "private" not in str(raised.value).casefold()


def test_unknown_event_and_conflicting_target_are_typed_unavailable_or_invalid(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_journal(vault)
    store = ProspectiveAuditStore(tmp_path / "operational-audit", clock=lambda: LINKED_AT)
    builder = _linker(tmp_path, vault, store)
    with pytest.raises(ProspectiveAuditLinkUnavailableError) as raised:
        builder.execute(EVENT_ID, DECISION_ID, (_mapping(),))
    assert (
        raised.value.reason_code
        == ProspectiveAuditLinkReasonCode.AUDIT_EVENT_MISSING_OR_DELETED.value
    )
