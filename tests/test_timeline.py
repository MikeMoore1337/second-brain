"""Deterministic tests for the Stage 3 Personal Timeline core."""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.decision_journal import (
    render_decision_journal_body,
    render_outcome_observation_body,
)
from second_brain.application.reports import Diagnostic, DiagnosticSeverity, VaultSnapshot
from second_brain.application.timeline import (
    MAX_TIMELINE_LIMIT,
    MAX_TIMELINE_SUMMARY_BYTES,
    BuildPersonalTimeline,
    PersonalTimelineRequest,
    PersonalTimelineResult,
    TimelineErrorCode,
    TimelineEvidenceInvalidError,
    TimelineInvalidClockError,
    TimelineInvalidRequestError,
    TimelineVaultUnavailableError,
)
from second_brain.application.validation import build_report
from tests.conftest import create_vault, managed_note, snapshot_tree, write_note

DECISION_ID = "0198f4c5-6a00-7000-8000-000000000010"
OUTCOME_ID = "0198f4c5-6a00-7000-8000-000000000011"
MEMORY_ID = "0198f4c5-6a00-7000-8000-000000000012"
PREFERENCE_ID = "0198f4c5-6a00-7000-8000-000000000013"
UNKNOWN_EARLY_UUID = "0198f4c5-6a00-7000-8000-000000000001"
UNKNOWN_LATE_UUID = "0198f4c5-6a00-7000-8000-000000000099"
GENERATED_AT = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)


class _SnapshotReader:
    """Deterministic reader seam for scanner diagnostics that are hard to induce on Windows."""

    def __init__(self, snapshot: VaultSnapshot) -> None:
        self.snapshot = snapshot

    def scan(self) -> VaultSnapshot:
        return self.snapshot


def _memory_note(
    note_id: str,
    *,
    evidence_kind: str = "user_statement",
    self_kind: str = "memory",
    evidence_at: str = "2026-09-05T12:00:00Z",
    precision: str = "exact",
    created: str = "2026-09-05T18:00:00+03:00",
    updated: str | None = None,
    domain: str | None = None,
    body: str = "# Canonical memory\n\nПользовательская запись.",
    decision_id: str | None = None,
) -> str:
    fields = [
        f"id: {note_id}",
        "type: zettel",
        f"created: {created}",
        "tags: []",
        "second_brain_personal_memory: 1",
        f"evidence_kind: {evidence_kind}",
        f"self_kind: {self_kind}",
        f'evidence_at: "{evidence_at}"' if evidence_at != "unknown" else "evidence_at: unknown",
        f"evidence_at_precision: {precision}",
    ]
    if updated is not None:
        fields.insert(3, f"updated: {updated}")
    if domain is not None:
        fields.append(f"domain: {domain}")
    if decision_id is not None:
        fields.append(f"decision_id: {decision_id}")
    return "---\n" + "\n".join(fields) + f"\n---\n{body}\n"


def _decision_body() -> str:
    return render_decision_journal_body(
        situation="Выбрать направление работы",
        available_options=("Первый вариант", "Второй вариант"),
        information_known_at_decision_time="Известны ограничения и срок.",
        criteria=("Скорость",),
        chosen_option="Второй вариант",
        reasons="Он лучше соответствует ограничению по времени.",
        confidence="Средняя",
        expected_result="Получится закончить быстрее.",
    )


def _outcome_body() -> str:
    return render_outcome_observation_body(
        actual_result="Результат оказался положительным.",
        reassessment="",
        notes="Проверено пользователем.",
    )


def _build(
    vault: Path,
    request: PersonalTimelineRequest | None = None,
) -> PersonalTimelineResult:
    return BuildPersonalTimeline(
        FileSystemVaultReader(vault),
        clock=lambda: GENERATED_AT,
    ).execute(request or PersonalTimelineRequest())


def _build_from_snapshot(
    snapshot: VaultSnapshot,
    request: PersonalTimelineRequest | None = None,
) -> PersonalTimelineResult:
    return BuildPersonalTimeline(
        _SnapshotReader(snapshot),
        clock=lambda: GENERATED_AT,
    ).execute(request or PersonalTimelineRequest())


def _snapshot_with_diagnostic(vault: Path, diagnostic: Diagnostic) -> VaultSnapshot:
    snapshot = FileSystemVaultReader(vault).scan()
    return replace(snapshot, diagnostics=(*snapshot.diagnostics, diagnostic))


def test_stage1_stage2_eligibility_and_projection(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Memory.md",
        _memory_note(
            MEMORY_ID,
            self_kind="memory",
            evidence_at="unknown",
            precision="unknown",
            body="  Строка   памяти\n\nс Unicode: ё и 🧠.  ",
        ),
    )
    write_note(
        vault,
        "10 Projects/Decision.md",
        _memory_note(
            DECISION_ID,
            evidence_kind="observed_decision",
            self_kind="decision",
            evidence_at="2026-09-05T12:00:00Z",
            created="2026-09-05T21:00:00+03:00",
            domain="career",
            body=_decision_body(),
        ),
    )
    write_note(
        vault,
        "10 Projects/Outcome.md",
        _memory_note(
            OUTCOME_ID,
            evidence_kind="outcome_later_observation",
            self_kind="outcome",
            evidence_at="2026-09-06T15:00:00+03:00",
            created="2026-09-06T16:00:00+03:00",
            body=_outcome_body(),
            decision_id=DECISION_ID,
        ),
    )
    write_note(
        vault,
        "30 Resources/Ordinary.md",
        "---\nid: 0198f4c5-6a00-7000-8000-000000000014\ntype: resource\n"
        "created: 2026-09-05T12:00:00+03:00\nself_kind: preference\n---\nОбычная заметка.\n",
    )
    write_note(vault, "30 Resources/Inbox.md", "ordinary managed note without evidence marker")

    result = _build(vault)

    assert result.known_total == 2
    assert result.unknown_total == 1
    assert [item.note_id for item in result.known_items] == [UUID(OUTCOME_ID), UUID(DECISION_ID)]
    assert [item.note_id for item in result.unknown_items] == [UUID(MEMORY_ID)]
    decision = result.known_items[1]
    outcome = result.known_items[0]
    assert decision.event_kind.value == "decision"
    assert decision.evidence_kind.value == "observed_decision"
    assert decision.summary == "Второй вариант"
    assert decision.related_note_ids == ()
    assert outcome.summary == "Результат оказался положительным."
    assert outcome.related_note_ids == (UUID(DECISION_ID),)
    assert result.unknown_items[0].event_at == "unknown"


def test_wrong_marker_and_coincidental_stage2_metadata_are_excluded(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    body = _decision_body()
    for path, marker, note_id in (
        ("30 Resources/Bool.md", "true", "0198f4c5-6a00-7000-8000-000000000020"),
        ("30 Resources/String.md", '"1"', "0198f4c5-6a00-7000-8000-000000000021"),
        ("30 Resources/Float.md", "1.0", "0198f4c5-6a00-7000-8000-000000000022"),
    ):
        write_note(
            vault,
            path,
            _memory_note(
                note_id,
                evidence_kind="observed_decision",
                self_kind="decision",
                body=body,
            ).replace("second_brain_personal_memory: 1", f"second_brain_personal_memory: {marker}"),
        )

    result = _build(vault)
    assert result.known_items == ()
    assert result.unknown_items == ()


def test_missing_content_root_with_non_null_manifest_fails_closed(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", _memory_note(MEMORY_ID))
    shutil.rmtree(vault / "20 Areas")

    snapshot = FileSystemVaultReader(vault).scan()
    assert snapshot.manifest is not None
    assert any(
        item.code == "VAULT_ROOT_MISSING" and item.path == "20 Areas"
        for item in snapshot.diagnostics
    )

    with pytest.raises(TimelineVaultUnavailableError) as error:
        _build(vault)

    assert error.value.code == TimelineErrorCode.VAULT_UNAVAILABLE.value


def test_note_read_error_fails_closed_without_partial_timeline(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", _memory_note(MEMORY_ID))
    (vault / "10 Projects/Unreadable.md").write_bytes(b"\xff\xfe invalid utf-8")

    snapshot = FileSystemVaultReader(vault).scan()
    assert any(item.code == "NOTE_READ_ERROR" for item in snapshot.diagnostics)
    with pytest.raises(TimelineVaultUnavailableError):
        _build_from_snapshot(snapshot)


def test_note_front_matter_error_fails_closed_without_partial_timeline(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", _memory_note(MEMORY_ID))
    write_note(vault, "10 Projects/Malformed.md", "---\nid: missing closing delimiter\n")

    snapshot = FileSystemVaultReader(vault).scan()
    assert any(item.code == "NOTE_FRONT_MATTER_ERROR" for item in snapshot.diagnostics)
    with pytest.raises(TimelineEvidenceInvalidError):
        _build_from_snapshot(snapshot)


def test_content_directory_read_error_fails_closed_with_bounded_vault_error(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", _memory_note(MEMORY_ID))
    snapshot = _snapshot_with_diagnostic(
        vault,
        Diagnostic(
            "VAULT_DIRECTORY_READ_ERROR",
            "synthetic read failure",
            DiagnosticSeverity.ERROR,
            "10 Projects",
        ),
    )

    with pytest.raises(TimelineVaultUnavailableError):
        _build_from_snapshot(snapshot)


@pytest.mark.parametrize("missing_root", ["_templates", "_attachments"])
def test_missing_non_content_root_does_not_block_timeline(
    tmp_path: Path,
    missing_root: str,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", _memory_note(MEMORY_ID))
    shutil.rmtree(vault / missing_root)

    result = _build(vault)
    assert result.known_total == 1
    assert result.known_items[0].note_id == UUID(MEMORY_ID)


def test_broken_wikilink_diagnostic_does_not_block_timeline(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Memory.md",
        _memory_note(MEMORY_ID, body="Каноническая запись со ссылкой [[Missing]]."),
    )

    snapshot = FileSystemVaultReader(vault).scan()
    report = build_report(snapshot)
    assert any(item.code == "BROKEN_WIKILINK" for item in report.diagnostics)
    result = _build_from_snapshot(snapshot)
    assert result.known_total == 1


def test_evidence_integrity_fails_closed_without_partial_items(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Valid.md", _memory_note(MEMORY_ID))
    write_note(
        vault,
        "10 Projects/Malformed.md",
        _memory_note(MEMORY_ID.replace("12", "15"), body="missing canonical metadata").replace(
            "self_kind: memory\n", ""
        ),
    )

    with pytest.raises(TimelineEvidenceInvalidError) as error:
        _build(vault)

    assert error.value.code == TimelineErrorCode.EVIDENCE_INVALID.value


@pytest.mark.parametrize("type_line", ["", "type: invalid"])
def test_enrolled_note_requires_valid_managed_note_type(
    tmp_path: Path,
    type_line: str,
) -> None:
    vault = create_vault(tmp_path / "vault")
    note = _memory_note(MEMORY_ID).replace("type: zettel\n", f"{type_line}\n")
    write_note(vault, "10 Projects/InvalidType.md", note)

    with pytest.raises(TimelineEvidenceInvalidError) as error:
        _build(vault)

    assert error.value.code == TimelineErrorCode.EVIDENCE_INVALID.value


def test_ordinary_invalid_note_type_does_not_block_valid_enrolled_timeline(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", _memory_note(MEMORY_ID))
    write_note(
        vault,
        "30 Resources/OrdinaryInvalidType.md",
        managed_note().replace("type: zettel", "type: invalid"),
    )

    result = _build(vault)
    assert result.known_total == 1
    assert result.known_items[0].note_id == UUID(MEMORY_ID)


def test_duplicate_note_id_remains_globally_blocking_for_timeline(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", _memory_note(MEMORY_ID))
    duplicate = managed_note("0198f4c5-6a00-7000-8000-000000000020")
    write_note(vault, "30 Resources/OrdinaryA.md", duplicate)
    write_note(vault, "30 Resources/OrdinaryB.md", duplicate)

    with pytest.raises(TimelineEvidenceInvalidError) as error:
        _build(vault)

    assert error.value.code == TimelineErrorCode.EVIDENCE_INVALID.value


@pytest.mark.parametrize(
    ("kind", "body", "extra"),
    [
        (
            "observed_decision",
            "## Situation\n\nНедостаточно\n",
            {"self_kind": "decision"},
        ),
        (
            "outcome_later_observation",
            "## Actual result\n\n\n## Reassessment\n\n\n## Notes\n\n\n",
            {"self_kind": "outcome", "decision_id": DECISION_ID},
        ),
    ],
)
def test_malformed_stage2_body_fails_closed(
    tmp_path: Path,
    kind: str,
    body: str,
    extra: dict[str, str],
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Bad.md",
        _memory_note(
            MEMORY_ID,
            evidence_kind=kind,
            evidence_at="2026-09-05T12:00:00Z",
            body=body,
            **extra,
        ),
    )

    with pytest.raises(TimelineEvidenceInvalidError):
        _build(vault)


def test_invalid_outcome_relation_fails_closed(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Outcome.md",
        _memory_note(
            OUTCOME_ID,
            evidence_kind="outcome_later_observation",
            self_kind="outcome",
            body=_outcome_body(),
            decision_id=DECISION_ID,
        ),
    )

    with pytest.raises(TimelineEvidenceInvalidError):
        _build(vault)


def test_exact_event_time_is_not_storage_time_and_offsets_compare_as_instants(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Zulu.md",
        _memory_note(
            MEMORY_ID,
            evidence_at="2026-09-05T12:00:00Z",
            created="2027-01-01T00:00:00+03:00",
        ),
    )
    write_note(
        vault,
        "10 Projects/Offset.md",
        _memory_note(
            PREFERENCE_ID,
            self_kind="preference",
            evidence_at="2026-09-05T15:00:00+03:00",
            created="2025-01-01T00:00:00+03:00",
        ),
    )

    result = _build(vault)
    first, second = result.known_items
    assert first.event_at == second.event_at
    assert first.event_at == datetime(2026, 9, 5, 12, tzinfo=UTC)
    assert {item.storage_created_at.year for item in result.known_items} == {2025, 2027}


def test_known_asc_desc_and_non_temporal_tie_break_are_deterministic(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    for path, note_id in (
        ("10 Projects/Z.md", MEMORY_ID),
        ("10 Projects/A.md", PREFERENCE_ID),
    ):
        write_note(
            vault,
            path,
            _memory_note(
                note_id,
                self_kind="memory" if note_id == MEMORY_ID else "preference",
                evidence_at="2026-09-05T12:00:00Z",
            ),
        )
    write_note(
        vault,
        "10 Projects/Older.md",
        _memory_note(
            UNKNOWN_LATE_UUID,
            evidence_at="2026-09-04T12:00:00Z",
            created="2026-09-05T23:00:00+03:00",
        ),
    )

    desc = _build(vault, PersonalTimelineRequest(order="desc"))
    asc = _build(vault, PersonalTimelineRequest(order="asc"))
    assert [item.relative_path for item in desc.known_items] == [
        "10 Projects/A.md",
        "10 Projects/Z.md",
        "10 Projects/Older.md",
    ]
    assert [item.relative_path for item in asc.known_items] == [
        "10 Projects/Older.md",
        "10 Projects/A.md",
        "10 Projects/Z.md",
    ]


def test_unknown_order_ignores_storage_time_and_uuidv7_time(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/B.md",
        _memory_note(
            UNKNOWN_EARLY_UUID,
            evidence_at="unknown",
            precision="unknown",
            created="2099-01-01T00:00:00+00:00",
        ),
    )
    write_note(
        vault,
        "10 Projects/A.md",
        _memory_note(
            UNKNOWN_LATE_UUID,
            evidence_at="unknown",
            precision="unknown",
            created="2000-01-01T00:00:00+00:00",
        ),
    )

    result = _build(vault, PersonalTimelineRequest(order="asc"))
    assert [item.relative_path for item in result.unknown_items] == [
        "10 Projects/A.md",
        "10 Projects/B.md",
    ]


def test_summary_is_plain_bounded_utf8_and_sanitizes_controls(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    unicode_body = "Начало\u0000\u200b " + ("Привет 🧠 " * 200)
    write_note(vault, "10 Projects/Long.md", _memory_note(MEMORY_ID, body=unicode_body))

    item = _build(vault).known_items[0]
    assert len(item.summary.encode("utf-8")) <= MAX_TIMELINE_SUMMARY_BYTES
    assert "\x00" not in item.summary
    assert "\u200b" not in item.summary
    assert "🧠" in item.summary


def test_request_bounds_bool_rejection_totals_and_generated_at(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    for index, note_id in enumerate((MEMORY_ID, PREFERENCE_ID), start=1):
        write_note(
            vault,
            f"10 Projects/{index}.md",
            _memory_note(
                note_id,
                self_kind="memory" if index == 1 else "preference",
                evidence_at=f"2026-09-0{index}T12:00:00Z",
            ),
        )

    result = _build(vault, PersonalTimelineRequest(known_limit=1, unknown_limit=0))
    assert len(result.known_items) == 1
    assert result.known_total == 2
    assert result.unknown_items == ()
    assert result.generated_at == GENERATED_AT

    for request in (
        PersonalTimelineRequest(known_limit=-1),
        PersonalTimelineRequest(known_limit=MAX_TIMELINE_LIMIT + 1),
        PersonalTimelineRequest(known_limit=True),
        PersonalTimelineRequest(order="middle"),  # type: ignore[arg-type]
    ):
        with pytest.raises(TimelineInvalidRequestError):
            _build(vault, request)


def test_naive_clock_is_bounded_failure(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", _memory_note(MEMORY_ID))

    with pytest.raises(TimelineInvalidClockError) as error:
        BuildPersonalTimeline(
            FileSystemVaultReader(vault),
            clock=lambda: datetime(2026, 9, 5, 20),
        ).execute(PersonalTimelineRequest())

    assert error.value.code == TimelineErrorCode.INVALID_CLOCK.value


def test_read_only_rebuild_reflects_current_vault_and_creates_no_state(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    note_path = write_note(vault, "10 Projects/Memory.md", _memory_note(MEMORY_ID))
    before = snapshot_tree(vault)
    builder = BuildPersonalTimeline(FileSystemVaultReader(vault), clock=lambda: GENERATED_AT)

    first = builder.execute(PersonalTimelineRequest())
    assert snapshot_tree(vault) == before
    assert set(snapshot_tree(vault)) == set(before)
    assert first.known_total == 1

    note_path.write_text(
        _memory_note(MEMORY_ID, body="Изменённая текущая запись."), encoding="utf-8"
    )
    second = builder.execute(PersonalTimelineRequest())
    assert second.known_items[0].summary == "Изменённая текущая запись."
    assert not list(vault.glob("**/*timeline*"))
