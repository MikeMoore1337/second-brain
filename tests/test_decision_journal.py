"""Focused deterministic tests for the Stage 2 Decision Journal core."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from second_brain.adapters.vault import FileSystemVaultReader, FileSystemVaultWriter
from second_brain.adapters.vault.frontmatter import parse_front_matter
from second_brain.application.decision_journal import (
    DecisionJournalBodyError,
    DecisionJournalDraft,
    OutcomeObservationBodyError,
    OutcomeObservationDraft,
    parse_decision_journal_body,
    parse_outcome_observation_body,
)
from second_brain.application.llm import NoteDraft
from second_brain.application.personal_memory import (
    PERSONAL_MEMORY_MARKER,
    PersonalMemoryDraft,
    PersonalMemoryDraftError,
    validate_personal_memory_draft,
)
from second_brain.application.reports import ScanReport
from second_brain.application.search import SearchRequest, SearchVault
from second_brain.application.services import (
    CreateManagedNoteFromDecisionJournalDraft,
    CreateManagedNoteFromOutcomeObservationDraft,
    ValidateVault,
)
from second_brain.application.writes import (
    CreateManagedNoteFromDecisionJournalDraftRequest,
    CreateManagedNoteFromOutcomeObservationDraftRequest,
    CreateNotePlan,
    CreateStatus,
)
from second_brain.domain.models import EvidenceAtPrecision, EvidenceKind, NoteType, SelfKind
from tests.conftest import create_vault, managed_note, snapshot_tree, write_note

FIXED_NOW = datetime(2026, 9, 5, 17, 0, 0, tzinfo=UTC)
DECISION_ID = UUID("0198f4c5-6a00-7000-8000-000000000002")
OUTCOME_ID = UUID("0198f4c5-6a00-7000-8000-000000000003")
OTHER_ID = UUID("0198f4c5-6a00-7000-8000-000000000004")


def journal_body(
    *,
    options: tuple[str, ...] = ("Остаться", "Уйти"),
    chosen: str = "Остаться",
    actual: str = "",
    reassessment: str = "",
    criteria: tuple[str, ...] = ("Риск",),
) -> str:
    """Собрать exact initial Journal body."""

    options_text = "\n".join(f"- {item}" for item in options)
    criteria_text = "\n".join(f"- {item}" for item in criteria)
    return (
        "## Situation\n"
        "Нужно принять решение в русском контексте.\n\n"
        "## Available options\n"
        f"{options_text}\n\n"
        "## Information known at decision time\n"
        "Известна только информация на момент выбора.\n\n"
        "## Criteria\n"
        f"{criteria_text}\n\n"
        "## Chosen option\n"
        f"{chosen}\n\n"
        "## Reasons\n"
        "Выбор лучше соответствует ограничениям.\n\n"
        "## Confidence\n"
        "Уверенность средняя.\n\n"
        "## Expected result\n"
        "Ожидаю измеримый результат.\n\n"
        "## Actual result\n"
        f"{actual}\n\n"
        "## Reassessment\n"
        f"{reassessment}\n"
    )


def outcome_body(
    *, actual: str = "Результат получен.", reassessment: str = "", notes: str = ""
) -> str:
    """Собрать exact Outcome Observation body."""

    return f"## Actual result\n{actual}\n\n## Reassessment\n{reassessment}\n\n## Notes\n{notes}\n"


def install_templates(vault: Path, template: str = "# Template body\n") -> None:
    """Установить все templates без изменения production vault."""

    for name in ("Project.md", "Area.md", "Resource.md", "Zettel.md"):
        (vault / "_templates" / name).write_text(template, encoding="utf-8")


def enrolled_stage2_note(
    *,
    evidence_kind: str,
    self_kind: str,
    note_id: UUID = DECISION_ID,
    body: str,
    decision_id: UUID | None = None,
    marker: str = "1",
    evidence_at: str = "2026-09-05T18:00:00+00:00",
    evidence_at_precision: str = "exact",
) -> str:
    """Собрать managed enrolled record для read-side tests."""

    lines = [
        "---",
        f"id: {note_id}",
        "type: project",
        "created: 2026-09-05T19:00:00+00:00",
        f"{PERSONAL_MEMORY_MARKER}: {marker}",
        f"evidence_kind: {evidence_kind}",
        f"self_kind: {self_kind}",
        f"evidence_at: {evidence_at}",
        f"evidence_at_precision: {evidence_at_precision}",
        "domain: decisions",
        "tags: [stage2]",
        "links: []",
    ]
    if decision_id is not None:
        lines.append(f"decision_id: {decision_id}")
    return "\n".join([*lines, "---", body])


def make_journal_draft(
    *,
    content: str | None = None,
    evidence_at: datetime | str = FIXED_NOW,
    evidence_at_precision: EvidenceAtPrecision | str = EvidenceAtPrecision.EXACT,
    domain: str | None = "decisions",
) -> DecisionJournalDraft:
    """Собрать reviewed Journal DTO без front matter authority."""

    return DecisionJournalDraft(
        draft=NoteDraft(
            title="Decision Journal",
            note_type=NoteType.PROJECT,
            content=content or journal_body(),
            tags=("decision",),
            links=("[[Context]]",),
        ),
        evidence_at=evidence_at,
        evidence_at_precision=evidence_at_precision,
        domain=domain,
    )


def make_outcome_draft(
    *,
    decision_id: UUID | str = DECISION_ID,
    content: str | None = None,
    evidence_at: datetime | str = FIXED_NOW,
    evidence_at_precision: EvidenceAtPrecision | str = EvidenceAtPrecision.EXACT,
    domain: str | None = "decisions",
) -> OutcomeObservationDraft:
    """Собрать reviewed Outcome DTO с explicit UUID relation."""

    return OutcomeObservationDraft(
        draft=NoteDraft(
            title="Outcome Observation",
            note_type=NoteType.PROJECT,
            content=content or outcome_body(),
            tags=("outcome",),
            links=(),
        ),
        decision_id=decision_id,  # type: ignore[arg-type]
        evidence_at=evidence_at,
        evidence_at_precision=evidence_at_precision,
        domain=domain,
    )


def report_for(vault: Path) -> ScanReport:
    """Вернуть canonical report текущего temporary vault."""

    return ValidateVault(FileSystemVaultReader(vault)).execute()


def codes(report: ScanReport) -> set[str]:
    """Извлечь diagnostic codes без привязки к output formatting."""

    return {item.code for item in report.diagnostics}


def journal_service(vault: Path) -> CreateManagedNoteFromDecisionJournalDraft:
    return CreateManagedNoteFromDecisionJournalDraft(
        FileSystemVaultReader(vault), FileSystemVaultWriter(vault)
    )


def outcome_service(vault: Path) -> CreateManagedNoteFromOutcomeObservationDraft:
    return CreateManagedNoteFromOutcomeObservationDraft(
        FileSystemVaultReader(vault), FileSystemVaultWriter(vault)
    )


def test_stage2_enums_and_valid_read_projections(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Decision.md",
        enrolled_stage2_note(
            evidence_kind="observed_decision",
            self_kind="decision",
            body=journal_body(),
        ),
    )
    write_note(
        vault,
        "10 Projects/Outcome.md",
        enrolled_stage2_note(
            evidence_kind="outcome_later_observation",
            self_kind="outcome",
            note_id=OUTCOME_ID,
            decision_id=DECISION_ID,
            evidence_at="2026-09-20T12:00:00+00:00",
            body=outcome_body(notes="Наблюдение сохранено."),
        ),
    )

    report = report_for(vault)

    assert report.error_count == 0
    assert EvidenceKind.OBSERVED_DECISION.value == "observed_decision"
    assert SelfKind.OUTCOME.value == "outcome"
    decision = next(note for note in report.notes if note.note_id == DECISION_ID)
    outcome = next(note for note in report.notes if note.note_id == OUTCOME_ID)
    assert decision.decision_journal is not None
    assert decision.decision_journal.available_options == ("Остаться", "Уйти")
    assert decision.decision_journal.chosen_option == "Остаться"
    assert outcome.outcome_observation is not None
    assert outcome.outcome_observation.decision_id == DECISION_ID
    assert report.decision_journals == (decision.decision_journal,)
    assert report.outcome_observations == (outcome.outcome_observation,)


@pytest.mark.parametrize(
    ("evidence_kind", "self_kind"),
    [
        ("observed_decision", "preference"),
        ("user_statement", "decision"),
        ("outcome_later_observation", "goal"),
        ("explicit_user_fact", "outcome"),
        ("observed_decision", "outcome"),
    ],
)
def test_stage2_cross_pairs_are_rejected(
    tmp_path: Path, evidence_kind: str, self_kind: str
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Cross-pair.md",
        enrolled_stage2_note(
            evidence_kind=evidence_kind,
            self_kind=self_kind,
            body=journal_body(),
        ),
    )

    report = report_for(vault)

    assert report.error_count >= 1
    assert any(code.startswith("PERSONAL_MEMORY_INVALID") for code in codes(report))
    assert not report.notes[0].decision_journal


def test_stage1_personal_memory_writer_rejects_all_stage2_values() -> None:
    draft = PersonalMemoryDraft(
        draft=NoteDraft("Stage 2", NoteType.PROJECT, "body"),
        evidence_kind=EvidenceKind.OBSERVED_DECISION,
        self_kind=SelfKind.DECISION,
        evidence_at=FIXED_NOW,
        evidence_at_precision=EvidenceAtPrecision.EXACT,
    )

    with pytest.raises(PersonalMemoryDraftError):
        validate_personal_memory_draft(draft)


def test_stage2_like_legacy_metadata_without_exact_marker_remains_ordinary(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    legacy = managed_note().replace(
        "tags: []",
        "tags: []\n"
        "evidence_kind: observed_decision\n"
        "self_kind: decision\n"
        "evidence_at: yesterday\n"
        "evidence_at_precision: custom\n"
        f"decision_id: {DECISION_ID}",
        1,
    )
    write_note(vault, "10 Projects/Legacy.md", legacy)

    report = report_for(vault)

    assert report.error_count == 0
    assert report.notes[0].personal_memory is None
    assert report.notes[0].decision_journal is None
    assert report.notes[0].outcome_observation is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.replace("## Reasons\n", "", 1),
        lambda body: body.replace("## Reasons", "## Situation", 1),
        lambda body: body.replace("## Available options", "## Criteria", 1),
        lambda body: body.replace("## Criteria\n- Риск", "## Criteria\n\n", 1),
    ],
)
def test_journal_body_rejects_missing_duplicate_order_or_empty_shape(
    mutate: Callable[[str], str],
) -> None:
    with pytest.raises(DecisionJournalBodyError):
        parse_decision_journal_body(mutate(journal_body()))


@pytest.mark.parametrize(
    "body",
    [
        journal_body(options=("Один",)),
        journal_body(options=tuple(f"Option {index}" for index in range(21))),
        journal_body(options=("A", " A ")),
        journal_body(chosen="Не выбран"),
        journal_body(criteria=()),
        journal_body(actual="Утечка поздних данных"),
        journal_body(reassessment="Утечка переоценки"),
    ],
)
def test_journal_body_rejects_deterministic_content_violations(body: str) -> None:
    with pytest.raises(DecisionJournalBodyError):
        parse_decision_journal_body(body)


def test_journal_body_projection_preserves_unicode_and_exact_option_match() -> None:
    body = journal_body(
        options=("Сохранить ёлку", "Переехать в Москву"), chosen="  Сохранить ёлку  "
    )

    record = parse_decision_journal_body(body)

    assert record.situation == "Нужно принять решение в русском контексте."
    assert record.available_options == ("Сохранить ёлку", "Переехать в Москву")
    assert record.chosen_option == "Сохранить ёлку"
    assert "ё" in record.available_options[0]


def test_journal_and_outcome_body_bounds_and_outcome_requirement() -> None:
    with pytest.raises(DecisionJournalBodyError):
        parse_decision_journal_body(journal_body().replace("Нужно принять", "x" * 300_000))
    with pytest.raises(OutcomeObservationBodyError):
        parse_outcome_observation_body(outcome_body(actual="", reassessment=""), DECISION_ID)


def test_journal_dry_run_is_lossless_and_application_owns_metadata(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(
        vault,
        "---\n"
        "defaults: &defaults\n"
        "  second_brain_personal_memory: 1\n"
        "  evidence_kind: user_statement\n"
        "  self_kind: preference\n"
        "  evidence_at: 2020-01-01T00:00:00+00:00\n"
        "  evidence_at_precision: exact\n"
        "  domain: template\n"
        f"  decision_id: {OTHER_ID}\n"
        "<<: *defaults\n"
        "custom_field: retained\n"
        "---\n# Ignored template body\n",
    )
    draft = make_journal_draft()
    before = snapshot_tree(vault)

    result = journal_service(vault).execute(
        CreateManagedNoteFromDecisionJournalDraftRequest(draft=draft, now=FIXED_NOW)
    )

    assert result.status is CreateStatus.DRY_RUN
    assert result.plan is not None
    parsed = parse_front_matter(result.plan.content)
    assert parsed.data[PERSONAL_MEMORY_MARKER] == 1
    assert parsed.data["evidence_kind"] == "observed_decision"
    assert parsed.data["self_kind"] == "decision"
    assert parsed.data["evidence_at"] == FIXED_NOW.isoformat()
    assert parsed.data["evidence_at_precision"] == "exact"
    assert parsed.data["domain"] == "decisions"
    assert "decision_id" not in parsed.data
    assert parsed.data["custom_field"] == "retained"
    assert parsed.body == draft.draft.content
    assert snapshot_tree(vault) == before


def test_journal_apply_creates_one_typed_note_and_uses_own_time(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)

    result = journal_service(vault).execute(
        CreateManagedNoteFromDecisionJournalDraftRequest(
            draft=make_journal_draft(
                evidence_at="2026-09-05T18:00:00+03:00",
                evidence_at_precision="exact",
            ),
            apply=True,
            now=FIXED_NOW,
        )
    )

    assert result.status is CreateStatus.CREATED
    assert result.validation_report is not None
    assert result.validation_report.error_count == 0
    target = vault / "10 Projects/Decision Journal.md"
    parsed = parse_front_matter(target.read_text(encoding="utf-8"))
    assert parsed.data["evidence_at"] == "2026-09-05T18:00:00+03:00"
    assert parsed.data["created"] == FIXED_NOW.isoformat()
    assert parsed.body == journal_body()


def test_journal_invalid_draft_is_rejected_before_vault_scan() -> None:
    class FailReader:
        def scan(self) -> object:
            pytest.fail("invalid Journal draft must be rejected before scan")

    invalid = make_journal_draft(content=journal_body(actual="not empty"))
    result = CreateManagedNoteFromDecisionJournalDraft(
        FailReader(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    ).execute(CreateManagedNoteFromDecisionJournalDraftRequest(invalid, apply=True))

    assert result.status is CreateStatus.REJECTED
    assert result.diagnostics[0].code == "DECISION_JOURNAL_INVALID_BODY"


def test_journal_post_write_failure_rolls_back_created_note(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    draft = make_journal_draft(content=journal_body().replace("Нужно принять", "[[Missing]]", 1))

    result = journal_service(vault).execute(
        CreateManagedNoteFromDecisionJournalDraftRequest(draft=draft, apply=True, now=FIXED_NOW)
    )

    assert result.status is CreateStatus.ROLLED_BACK
    assert result.rollback_succeeded is True
    assert result.plan is not None
    assert not (vault / result.plan.relative_path).exists()


def test_outcome_requires_current_valid_journal_and_writes_separate_note(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    write_note(
        vault,
        "10 Projects/Decision.md",
        enrolled_stage2_note(
            evidence_kind="observed_decision",
            self_kind="decision",
            body=journal_body(),
        ),
    )

    outcome_result = outcome_service(vault).execute(
        CreateManagedNoteFromOutcomeObservationDraftRequest(
            make_outcome_draft(
                evidence_at="2026-09-20T12:00:00+03:00",
                evidence_at_precision="exact",
                content=outcome_body(notes="Проверка outcome."),
            ),
            apply=True,
            now=FIXED_NOW,
        )
    )

    assert outcome_result.status is CreateStatus.CREATED
    target = vault / "10 Projects/Outcome Observation.md"
    parsed = parse_front_matter(target.read_text(encoding="utf-8"))
    assert parsed.data["evidence_kind"] == "outcome_later_observation"
    assert parsed.data["self_kind"] == "outcome"
    assert parsed.data["decision_id"] == str(DECISION_ID)
    assert parsed.data["evidence_at"] == "2026-09-20T12:00:00+03:00"
    assert parsed.data["created"] == FIXED_NOW.isoformat()
    assert parsed.body == outcome_body(notes="Проверка outcome.")
    report = report_for(vault)
    assert report.error_count == 0
    assert len(report.decision_journals) == 1
    assert len(report.outcome_observations) == 1


def test_outcome_dry_run_does_not_change_vault(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    write_note(
        vault,
        "10 Projects/Decision.md",
        enrolled_stage2_note(
            evidence_kind="observed_decision",
            self_kind="decision",
            body=journal_body(),
        ),
    )
    before = snapshot_tree(vault)

    result = outcome_service(vault).execute(
        CreateManagedNoteFromOutcomeObservationDraftRequest(make_outcome_draft())
    )

    assert result.status is CreateStatus.DRY_RUN
    assert result.plan is not None
    assert snapshot_tree(vault) == before


def test_outcome_target_is_checked_before_prepare_and_write(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)

    class FailWriter(FileSystemVaultWriter):
        def prepare_from_outcome_observation_draft(
            self, *args: object, **kwargs: object
        ) -> CreateNotePlan:
            pytest.fail("missing target must be rejected before prepare")

    result = CreateManagedNoteFromOutcomeObservationDraft(
        FileSystemVaultReader(vault), FailWriter(vault)
    ).execute(
        CreateManagedNoteFromOutcomeObservationDraftRequest(
            make_outcome_draft(decision_id=OTHER_ID), apply=True, now=FIXED_NOW
        )
    )

    assert result.status is CreateStatus.REJECTED
    assert result.plan is None
    assert "OUTCOME_DECISION_NOT_FOUND" in {item.code for item in result.diagnostics}


@pytest.mark.parametrize("target_kind", ["ordinary", "stage1", "outcome", "malformed"])
def test_outcome_rejects_non_journal_targets(tmp_path: Path, target_kind: str) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    if target_kind == "ordinary":
        write_note(vault, "10 Projects/Ordinary.md", managed_note())
    elif target_kind == "stage1":
        write_note(
            vault,
            "10 Projects/Stage1.md",
            enrolled_stage2_note(
                evidence_kind="user_statement",
                self_kind="preference",
                body="# Stage 1\n",
            ),
        )
    elif target_kind == "outcome":
        write_note(
            vault,
            "10 Projects/Outcome.md",
            enrolled_stage2_note(
                evidence_kind="outcome_later_observation",
                self_kind="outcome",
                note_id=DECISION_ID,
                decision_id=OTHER_ID,
                body=outcome_body(),
            ),
        )
    else:
        write_note(
            vault,
            "10 Projects/Malformed.md",
            enrolled_stage2_note(
                evidence_kind="observed_decision",
                self_kind="decision",
                body=journal_body(actual="forbidden"),
            ),
        )

    result = outcome_service(vault).execute(
        CreateManagedNoteFromOutcomeObservationDraftRequest(
            make_outcome_draft(), apply=True, now=FIXED_NOW
        )
    )

    assert result.status is CreateStatus.REJECTED
    assert "OUTCOME_DECISION_TARGET_INVALID" in {item.code for item in result.diagnostics}


def test_outcome_rejects_duplicate_target_identity(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    for name in ("First.md", "Second.md"):
        write_note(
            vault,
            f"10 Projects/{name}",
            enrolled_stage2_note(
                evidence_kind="observed_decision",
                self_kind="decision",
                body=journal_body(),
            ),
        )

    result = outcome_service(vault).execute(
        CreateManagedNoteFromOutcomeObservationDraftRequest(
            make_outcome_draft(), apply=True, now=FIXED_NOW
        )
    )

    assert result.status is CreateStatus.REJECTED
    assert "OUTCOME_DECISION_IDENTITY_CONFLICT" in {item.code for item in result.diagnostics}


@pytest.mark.parametrize(
    ("decision_id", "code"),
    [
        ("0198f4c5-6a00-7000-8000-000000000002", "OUTCOME_DECISION_ID_INVALID"),
        (UUID("550e8400-e29b-41d4-a716-446655440000"), "OUTCOME_DECISION_ID_INVALID"),
    ],
)
def test_outcome_uuidv7_is_type_strict_and_rejected_before_scan(
    decision_id: UUID | str, code: str
) -> None:
    class FailReader:
        def scan(self) -> object:
            pytest.fail("invalid outcome relation must be rejected before scan")

    result = CreateManagedNoteFromOutcomeObservationDraft(
        FailReader(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    ).execute(
        CreateManagedNoteFromOutcomeObservationDraftRequest(
            make_outcome_draft(decision_id=decision_id),
            apply=True,
        )
    )

    assert result.status is CreateStatus.REJECTED
    assert result.diagnostics[0].code == code


def test_outcome_unknown_time_has_no_created_fallback_and_merge_cannot_override_relation(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(
        vault,
        "---\n"
        "defaults: &defaults\n"
        "  second_brain_personal_memory: 1\n"
        "  evidence_kind: user_statement\n"
        "  self_kind: preference\n"
        "  evidence_at: 2020-01-01T00:00:00+00:00\n"
        "  evidence_at_precision: exact\n"
        f"  decision_id: {OTHER_ID}\n"
        "<<: *defaults\n"
        "custom_field: retained\n"
        "---\n# ignored\n",
    )
    write_note(
        vault,
        "10 Projects/Decision.md",
        enrolled_stage2_note(
            evidence_kind="observed_decision",
            self_kind="decision",
            body=journal_body(),
        ),
    )

    result = outcome_service(vault).execute(
        CreateManagedNoteFromOutcomeObservationDraftRequest(
            make_outcome_draft(
                evidence_at="unknown",
                evidence_at_precision=EvidenceAtPrecision.UNKNOWN,
                domain=None,
            ),
            apply=True,
            now=FIXED_NOW,
        )
    )

    assert result.status is CreateStatus.CREATED
    parsed = parse_front_matter(
        (vault / "10 Projects/Outcome Observation.md").read_text(encoding="utf-8")
    )
    assert parsed.data["evidence_at"] == "unknown"
    assert parsed.data["evidence_at_precision"] == "unknown"
    assert parsed.data["decision_id"] == str(DECISION_ID)
    assert parsed.data["created"] == FIXED_NOW.isoformat()
    assert "domain" not in parsed.data
    assert parsed.data["custom_field"] == "retained"


def test_stage2_notes_use_existing_search_projection_only(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Decision.md",
        enrolled_stage2_note(
            evidence_kind="observed_decision",
            self_kind="decision",
            body=journal_body().replace("Нужно принять", "Searchable decision", 1),
        ),
    )
    from second_brain.adapters.search import SqliteFts5SearchIndex

    index = SqliteFts5SearchIndex()
    try:
        hits = SearchVault(FileSystemVaultReader(vault), index).execute(SearchRequest("Searchable"))
    finally:
        index.close()

    assert len(hits) == 1
    assert not hasattr(hits[0], "evidence_kind")
    assert not hasattr(hits[0], "decision_id")
