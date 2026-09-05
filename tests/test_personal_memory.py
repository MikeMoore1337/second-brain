"""Focused contract tests for reviewed Personal Memory v1."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from second_brain.adapters.vault import FileSystemVaultReader, FileSystemVaultWriter
from second_brain.adapters.vault.frontmatter import parse_front_matter
from second_brain.application.llm import NoteDraft
from second_brain.application.personal_memory import (
    MAX_PERSONAL_MEMORY_DOMAIN_BYTES,
    PERSONAL_MEMORY_MARKER,
    PersonalMemoryDraft,
    is_personal_memory_enrolled,
    validate_personal_memory_fields,
)
from second_brain.application.reports import ScanReport
from second_brain.application.research import SourceKind, SourceProvenance
from second_brain.application.research_draft import ReviewedResearchDraft
from second_brain.application.search import SearchRequest, SearchVault
from second_brain.application.services import (
    CreateManagedNote,
    CreateManagedNoteFromDraft,
    CreateManagedNoteFromPersonalMemoryDraft,
    CreateManagedNoteFromReviewedResearchDraft,
    ValidateVault,
)
from second_brain.application.writes import (
    CreateManagedNoteFromDraftRequest,
    CreateManagedNoteFromPersonalMemoryDraftRequest,
    CreateManagedNoteFromReviewedResearchDraftRequest,
    CreateManagedNoteRequest,
    CreateStatus,
)
from second_brain.domain.models import (
    EvidenceAtPrecision,
    EvidenceKind,
    NoteType,
    SelfKind,
)
from tests.conftest import create_vault, managed_note, snapshot_tree, write_note

FIXED_NOW = datetime(2026, 9, 5, 17, 0, 0, tzinfo=timezone(timedelta(hours=3)))
FIXED_EVIDENCE_AT = datetime(2026, 9, 5, 16, 55, 0, tzinfo=timezone(timedelta(hours=3)))


def validate_vault(vault: Path) -> ScanReport:
    """Проверить fixture vault через canonical reader -> build_report boundary."""

    return ValidateVault(FileSystemVaultReader(vault)).execute()


def diagnostic_codes(report: ScanReport) -> list[str]:
    """Вернуть deterministic diagnostic codes для focused assertions."""

    return [item.code for item in report.diagnostics]


def enrolled_note(
    *,
    evidence_kind: str = "user_statement",
    self_kind: str = "preference",
    evidence_at: str = '"2026-09-05T16:55:00+03:00"',
    evidence_at_precision: str = "exact",
    domain: str | None = "career",
    marker: str = "1",
    body: str = "# Personal assertion\n\nРусское тело сохраняется буквально.\n",
) -> str:
    """Собрать одну managed note с контролируемыми Personal Memory values."""

    lines = [
        "---",
        "id: 0198f4c5-6a00-7000-8000-000000000002",
        "type: zettel",
        "created: 2026-09-05T17:00:00+03:00",
        f"{PERSONAL_MEMORY_MARKER}: {marker}",
        f"evidence_kind: {evidence_kind}",
        f"self_kind: {self_kind}",
        f"evidence_at: {evidence_at}",
        f"evidence_at_precision: {evidence_at_precision}",
    ]
    if domain is not None:
        lines.append(f"domain: {domain}")
    lines.extend(["tags: [memory]", "links: []", "---", body])
    return "\n".join(lines)


def install_templates(vault: Path, project_template: str = "# Template body\n") -> None:
    """Добавить templates, используемые dedicated Personal Memory writer path."""

    for filename, content in {
        "Project.md": project_template,
        "Area.md": "# Area template\n",
        "Resource.md": "# Resource template\n",
        "Zettel.md": "# Zettel template\n",
    }.items():
        (vault / "_templates" / filename).write_text(content, encoding="utf-8")


def merged_marker_template() -> str:
    """Шаблон с valid marker/companion defaults, унаследованными через merge."""

    return (
        "---\n"
        "defaults: &defaults\n"
        "  second_brain_personal_memory: 1\n"
        "  evidence_kind: user_statement\n"
        "  self_kind: preference\n"
        '  evidence_at: "2026-09-05T16:55:00+03:00"\n'
        "  evidence_at_precision: exact\n"
        "  domain: career\n"
        "<<: *defaults\n"
        "custom_field: retained\n"
        "---\n"
        "# Template body is ignored\n"
    )


def nested_multiple_merge_marker_template() -> str:
    """Шаблон с nested и multiple YAML merge sources для sanitizer regression."""

    return (
        "---\n"
        "base: &base\n"
        "  second_brain_personal_memory: 1\n"
        "  evidence_kind: user_statement\n"
        "  self_kind: preference\n"
        '  evidence_at: "2026-09-05T16:55:00+03:00"\n'
        "  evidence_at_precision: exact\n"
        "domain_defaults: &domain_defaults\n"
        "  domain: career\n"
        "nested: &nested\n"
        "  <<: [*base, *domain_defaults]\n"
        "<<: [*nested, *domain_defaults]\n"
        "custom_field: retained\n"
        "---\n"
        "# Template body is ignored\n"
    )


def make_reviewed_research_draft() -> ReviewedResearchDraft:
    """Собрать минимальный reviewed research record без network."""

    return ReviewedResearchDraft(
        draft=NoteDraft(
            title="Research-derived note",
            note_type=NoteType.PROJECT,
            content="# Reviewed research body\n",
            tags=("research",),
            links=(),
        ),
        sources=(
            SourceProvenance(
                uri="https://example.com/research",
                source_kind=SourceKind.WEB,
                retrieved_at=FIXED_NOW,
            ),
        ),
    )


def make_personal_memory_draft(
    *,
    content: str = "## Assertion\n\nЯ предпочитаю ясные границы.\n",
    evidence_kind: EvidenceKind | str = EvidenceKind.USER_STATEMENT,
    self_kind: SelfKind | str = SelfKind.PREFERENCE,
    evidence_at: datetime | str = FIXED_EVIDENCE_AT,
    evidence_at_precision: EvidenceAtPrecision | str = EvidenceAtPrecision.EXACT,
    domain: str | None = "career",
) -> PersonalMemoryDraft:
    """Собрать reviewed wrapper только из semantic NoteDraft и controlled fields."""

    return PersonalMemoryDraft(
        draft=NoteDraft(
            title="Reviewed memory",
            note_type=NoteType.PROJECT,
            content=content,
            tags=("reviewed",),
            links=("[[Context]]",),
        ),
        evidence_kind=evidence_kind,
        self_kind=self_kind,
        evidence_at=evidence_at,
        evidence_at_precision=evidence_at_precision,
        domain=domain,
    )


def create_service(vault: Path) -> CreateManagedNoteFromPersonalMemoryDraft:
    """Создать use case над теми же reader/writer boundaries, что и обычная note."""

    return CreateManagedNoteFromPersonalMemoryDraft(
        FileSystemVaultReader(vault),
        FileSystemVaultWriter(vault),
    )


def test_marker_gate_is_type_strict_and_bool_cannot_pass_as_integer() -> None:
    assert True == 1
    assert is_personal_memory_enrolled({PERSONAL_MEMORY_MARKER: 1}) is True
    assert is_personal_memory_enrolled({PERSONAL_MEMORY_MARKER: True}) is False
    assert is_personal_memory_enrolled({PERSONAL_MEMORY_MARKER: "1"}) is False
    assert is_personal_memory_enrolled({PERSONAL_MEMORY_MARKER: 1.0}) is False
    assert is_personal_memory_enrolled({PERSONAL_MEMORY_MARKER: 2}) is False
    assert is_personal_memory_enrolled({}) is False


def test_public_read_validator_has_only_marker_gated_semantics() -> None:
    fields = {
        "evidence_kind": "user_statement",
        "self_kind": "preference",
        "evidence_at": FIXED_EVIDENCE_AT,
        "evidence_at_precision": "exact",
        "domain": "career",
    }

    for marker in (None, True, "1", 1.0):
        front_matter = dict(fields)
        if marker is not None:
            front_matter[PERSONAL_MEMORY_MARKER] = marker
        metadata, issues = validate_personal_memory_fields(front_matter)
        assert metadata is None
        assert issues == ()

    front_matter = dict(fields)
    front_matter[PERSONAL_MEMORY_MARKER] = 1
    metadata, issues = validate_personal_memory_fields(front_matter)
    assert metadata is not None
    assert metadata.evidence_kind is EvidenceKind.USER_STATEMENT
    assert issues == ()


@pytest.mark.parametrize("marker", ["true", '"1"', "1.0", "2"])
def test_wrong_marker_values_keep_coincident_fields_legacy_unknown(
    tmp_path: Path,
    marker: str,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Legacy.md",
        enrolled_note(
            marker=marker,
            evidence_at="yesterday",
            evidence_at_precision="custom",
            domain="[work, home]",
        ),
    )

    report = validate_vault(vault)

    assert report.error_count == 0
    note = report.notes[0]
    assert note.personal_memory is None
    assert note.personal_memory_metadata is None
    assert note.front_matter["evidence_at"] == "yesterday"
    assert note.front_matter["domain"] == ["work", "home"]
    assert not any(code.startswith("PERSONAL_MEMORY_") for code in diagnostic_codes(report))


def test_absent_marker_with_all_coincident_fields_is_valid_ordinary_note(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    legacy = managed_note().replace(
        "tags: []",
        "tags: []\n"
        "domain: [work, home]\n"
        "self_kind: memory\n"
        "evidence_kind: user_statement\n"
        "evidence_at: yesterday\n"
        "evidence_at_precision: custom",
        1,
    )
    write_note(vault, "10 Projects/Legacy.md", legacy)

    report = validate_vault(vault)

    assert report.error_count == 0
    assert report.notes[0].personal_memory is None


def test_valid_exact_personal_memory_projection_and_unicode_body(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    body = "# Память\n\nТочный русский текст: ёлка, Москва, предпочтение.\n"
    write_note(vault, "40 Zettelkasten/Memory.md", enrolled_note(body=body))

    report = validate_vault(vault)

    assert report.error_count == 0
    note = report.notes[0]
    assert note.personal_memory is not None
    assert note.personal_memory.evidence_kind is EvidenceKind.USER_STATEMENT
    assert note.personal_memory.self_kind is SelfKind.PREFERENCE
    assert note.personal_memory.evidence_at == FIXED_EVIDENCE_AT
    assert note.personal_memory.evidence_at_precision is EvidenceAtPrecision.EXACT
    assert note.personal_memory.domain == "career"
    assert note.body == body


@pytest.mark.parametrize(
    "evidence_at",
    [
        datetime(2026, 9, 5, 13, 55, tzinfo=UTC),
        datetime(
            2026,
            9,
            5,
            12,
            55,
            0,
            123456,
            tzinfo=timezone(timedelta(hours=-4)),
        ),
    ],
)
def test_accepted_datetime_evidence_round_trips_through_write_and_scan(
    tmp_path: Path,
    evidence_at: datetime,
) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    draft = make_personal_memory_draft(evidence_at=evidence_at)

    result = create_service(vault).execute(
        CreateManagedNoteFromPersonalMemoryDraftRequest(
            draft=draft,
            apply=True,
            now=FIXED_NOW,
        )
    )

    assert result.status is CreateStatus.CREATED
    report = validate_vault(vault)
    assert report.error_count == 0
    metadata = report.notes[0].personal_memory
    assert metadata is not None
    assert metadata.evidence_at == evidence_at


@pytest.mark.parametrize(
    "evidence_at",
    [
        datetime(2026, 9, 5, 16, 55),
        datetime(
            2026,
            9,
            5,
            16,
            55,
            tzinfo=timezone(timedelta(hours=1, seconds=30)),
        ),
    ],
)
def test_invalid_datetime_evidence_is_rejected_before_plan(
    tmp_path: Path,
    evidence_at: datetime,
) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    before = snapshot_tree(vault)

    result = create_service(vault).execute(
        CreateManagedNoteFromPersonalMemoryDraftRequest(
            draft=make_personal_memory_draft(evidence_at=evidence_at),
            now=FIXED_NOW,
        )
    )

    assert result.status is CreateStatus.REJECTED
    assert result.plan is None
    assert result.diagnostics[0].code == "PERSONAL_MEMORY_INVALID_EVIDENCE_AT"
    assert snapshot_tree(vault) == before


def test_valid_unknown_evidence_time_has_no_created_fallback(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "40 Zettelkasten/Unknown-time.md",
        enrolled_note(
            evidence_kind="explicit_user_fact",
            self_kind="memory",
            evidence_at='"unknown"',
            evidence_at_precision="unknown",
            domain=None,
        ),
    )

    report = validate_vault(vault)

    assert report.error_count == 0
    metadata = report.notes[0].personal_memory
    assert metadata is not None
    assert metadata.evidence_at == "unknown"
    assert metadata.evidence_at_precision is EvidenceAtPrecision.UNKNOWN
    assert metadata.domain is None


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("evidence_kind", "PERSONAL_MEMORY_MISSING_EVIDENCE_KIND"),
        ("self_kind", "PERSONAL_MEMORY_MISSING_SELF_KIND"),
        ("evidence_at", "PERSONAL_MEMORY_MISSING_EVIDENCE_AT"),
        ("evidence_at_precision", "PERSONAL_MEMORY_MISSING_EVIDENCE_AT_PRECISION"),
    ],
)
def test_enrolled_record_requires_all_stage_one_fields(
    tmp_path: Path,
    field: str,
    code: str,
) -> None:
    vault = create_vault(tmp_path / "vault")
    text = enrolled_note()
    text = "\n".join(line for line in text.splitlines() if not line.startswith(f"{field}:"))
    write_note(vault, "10 Projects/Missing.md", text)

    report = validate_vault(vault)

    assert code in diagnostic_codes(report)
    assert report.notes[0].personal_memory is None


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("evidence_kind", "observed_decision", "PERSONAL_MEMORY_INVALID_EVIDENCE_KIND"),
        (
            "evidence_kind",
            "model_inference",
            "PERSONAL_MEMORY_INVALID_EVIDENCE_KIND",
        ),
        ("self_kind", "decision", "PERSONAL_MEMORY_INVALID_SELF_KIND"),
        ("self_kind", "outcome", "PERSONAL_MEMORY_INVALID_SELF_KIND"),
        ("evidence_at", "2026-09-05T16:55:00", "PERSONAL_MEMORY_INVALID_EVIDENCE_AT"),
        (
            "evidence_at_precision",
            "unknown",
            "PERSONAL_MEMORY_INVALID_EVIDENCE_AT_PAIR",
        ),
        ("evidence_at", '"unknown"', "PERSONAL_MEMORY_INVALID_EVIDENCE_AT_PAIR"),
        ("domain", "Career", "PERSONAL_MEMORY_INVALID_DOMAIN"),
        ("domain", "work/health", "PERSONAL_MEMORY_INVALID_DOMAIN"),
        ("domain", "[work, home]", "PERSONAL_MEMORY_INVALID_DOMAIN"),
        ("domain", "true", "PERSONAL_MEMORY_INVALID_DOMAIN"),
        ("domain", "{name: work}", "PERSONAL_MEMORY_INVALID_DOMAIN"),
        ("domain", "''", "PERSONAL_MEMORY_INVALID_DOMAIN"),
        (
            "domain",
            '"' + ("x" * (MAX_PERSONAL_MEMORY_DOMAIN_BYTES + 1)) + '"',
            "PERSONAL_MEMORY_INVALID_DOMAIN",
        ),
    ],
)
def test_enrolled_record_rejects_closed_contract_violations(
    tmp_path: Path,
    field: str,
    value: str,
    code: str,
) -> None:
    vault = create_vault(tmp_path / "vault")
    text = enrolled_note()
    text = text.replace(
        f"{field}: {text.split(f'{field}: ', 1)[1].splitlines()[0]}", f"{field}: {value}"
    )
    write_note(vault, "10 Projects/Invalid.md", text)

    report = validate_vault(vault)

    assert code in diagnostic_codes(report)
    assert report.notes[0].personal_memory is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidence_kind", "true"),
        ("self_kind", "[preference]"),
        ("evidence_at", "123"),
        ("evidence_at_precision", "1"),
        ("domain", "42"),
    ],
)
def test_enrolled_metadata_does_not_coerce_yaml_types(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    vault = create_vault(tmp_path / "vault")
    text = enrolled_note()
    old = text.split(f"{field}: ", 1)[1].splitlines()[0]
    write_note(
        vault, "10 Projects/No-coercion.md", text.replace(f"{field}: {old}", f"{field}: {value}")
    )

    report = validate_vault(vault)

    assert report.error_count >= 1
    assert report.notes[0].personal_memory is None


def test_personal_memory_dry_run_is_lossless_and_marker_is_not_client_input(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(
        vault,
        "---\n"
        "# Preserve template metadata\n"
        'custom_field: "retained"\n'
        "domain: template-default\n"
        "tags: [template-default]\n"
        "links: [template-default-link]\n"
        "---\n"
        "# Template body is ignored\n",
    )
    draft = make_personal_memory_draft()
    before = snapshot_tree(vault)

    result = create_service(vault).execute(
        CreateManagedNoteFromPersonalMemoryDraftRequest(draft=draft, now=FIXED_NOW)
    )

    assert result.status is CreateStatus.DRY_RUN
    assert result.plan is not None
    parsed = parse_front_matter(result.plan.content)
    assert type(parsed.data[PERSONAL_MEMORY_MARKER]) is int
    assert parsed.data[PERSONAL_MEMORY_MARKER] == 1
    assert parsed.data["evidence_kind"] == "user_statement"
    assert parsed.data["self_kind"] == "preference"
    assert parsed.data["evidence_at"] == "2026-09-05T16:55:00+03:00"
    assert parsed.data["evidence_at_precision"] == "exact"
    assert parsed.data["domain"] == "career"
    assert parsed.data["custom_field"] == "retained"
    assert parsed.data["tags"] == ["reviewed"]
    assert parsed.data["links"] == ["[[Context]]"]
    assert parsed.body == draft.draft.content
    assert "Template body is ignored" not in result.plan.content
    assert not hasattr(draft, "front_matter")
    assert snapshot_tree(vault) == before


def test_personal_memory_controlled_fields_cannot_be_resurrected_by_merge(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(
        vault,
        "---\n"
        "defaults: &defaults\n"
        "  second_brain_personal_memory: 1\n"
        "  evidence_kind: explicit_user_fact\n"
        "  self_kind: memory\n"
        '  evidence_at: "2020-01-01T00:00:00+00:00"\n'
        "  evidence_at_precision: exact\n"
        "  domain: career\n"
        "<<: *defaults\n"
        "custom_field: retained\n"
        "---\n"
        "# Template body is ignored\n",
    )
    draft = make_personal_memory_draft(
        evidence_at="unknown",
        evidence_at_precision=EvidenceAtPrecision.UNKNOWN,
        domain=None,
    )

    dry_run = create_service(vault).execute(
        CreateManagedNoteFromPersonalMemoryDraftRequest(draft=draft, now=FIXED_NOW)
    )

    assert dry_run.status is CreateStatus.DRY_RUN
    assert dry_run.plan is not None
    parsed = parse_front_matter(dry_run.plan.content)
    assert parsed.data[PERSONAL_MEMORY_MARKER] == 1
    assert parsed.data["evidence_kind"] == "user_statement"
    assert parsed.data["self_kind"] == "preference"
    assert parsed.data["evidence_at"] == "unknown"
    assert parsed.data["evidence_at_precision"] == "unknown"
    assert "domain" not in parsed.data
    assert parsed.data["custom_field"] == "retained"

    applied = create_service(vault).execute(
        CreateManagedNoteFromPersonalMemoryDraftRequest(
            draft=draft,
            apply=True,
            now=FIXED_NOW,
        )
    )

    assert applied.status is CreateStatus.CREATED
    report = validate_vault(vault)
    assert report.error_count == 0
    metadata = report.notes[0].personal_memory
    assert metadata is not None
    assert metadata.evidence_at == "unknown"
    assert metadata.evidence_at_precision is EvidenceAtPrecision.UNKNOWN
    assert metadata.domain is None


def test_generic_draft_writer_cannot_emit_reserved_personal_memory_marker(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(
        vault,
        "---\nsecond_brain_personal_memory: 1\ncustom_field: retained\n---\n# Template body\n",
    )
    note_draft = make_personal_memory_draft().draft

    result = CreateManagedNoteFromDraft(
        FileSystemVaultReader(vault),
        FileSystemVaultWriter(vault),
    ).execute(CreateManagedNoteFromDraftRequest(draft=note_draft))

    assert result.status is CreateStatus.DRY_RUN
    assert result.plan is not None
    parsed = parse_front_matter(result.plan.content)
    assert PERSONAL_MEMORY_MARKER not in parsed.data
    assert parsed.data["custom_field"] == "retained"


def test_generic_create_writer_cannot_emit_merge_inherited_marker(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault, merged_marker_template())

    result = CreateManagedNote(
        FileSystemVaultReader(vault),
        FileSystemVaultWriter(vault),
    ).execute(
        CreateManagedNoteRequest(
            note_type=NoteType.PROJECT,
            title="Generic marker merge",
            apply=True,
            now=FIXED_NOW,
        )
    )

    assert result.status is CreateStatus.CREATED
    report = validate_vault(vault)
    assert report.error_count == 0
    assert report.notes[0].personal_memory is None
    assert PERSONAL_MEMORY_MARKER not in report.notes[0].front_matter


def test_draft_writer_cannot_emit_nested_multiple_merge_marker(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault, nested_multiple_merge_marker_template())

    result = CreateManagedNoteFromDraft(
        FileSystemVaultReader(vault),
        FileSystemVaultWriter(vault),
    ).execute(
        CreateManagedNoteFromDraftRequest(
            draft=make_personal_memory_draft().draft,
            apply=True,
            now=FIXED_NOW,
        )
    )

    assert result.status is CreateStatus.CREATED
    report = validate_vault(vault)
    assert report.error_count == 0
    assert report.notes[0].personal_memory is None
    assert PERSONAL_MEMORY_MARKER not in report.notes[0].front_matter
    assert report.notes[0].front_matter["custom_field"] == "retained"


def test_reviewed_research_writer_cannot_emit_merge_inherited_marker(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault, merged_marker_template())

    result = CreateManagedNoteFromReviewedResearchDraft(
        FileSystemVaultReader(vault),
        FileSystemVaultWriter(vault),
    ).execute(
        CreateManagedNoteFromReviewedResearchDraftRequest(
            reviewed_draft=make_reviewed_research_draft(),
            apply=True,
            now=FIXED_NOW,
        )
    )

    assert result.status is CreateStatus.CREATED
    report = validate_vault(vault)
    assert report.error_count == 0
    assert report.notes[0].personal_memory is None
    assert PERSONAL_MEMORY_MARKER not in report.notes[0].front_matter


def test_personal_memory_apply_creates_one_typed_note_and_post_validates(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    before = snapshot_tree(vault)

    result = create_service(vault).execute(
        CreateManagedNoteFromPersonalMemoryDraftRequest(
            draft=make_personal_memory_draft(),
            apply=True,
            now=FIXED_NOW,
        )
    )

    assert result.status is CreateStatus.CREATED
    assert result.validation_report is not None
    assert result.validation_report.error_count == 0
    assert result.plan is not None
    target = vault / "10 Projects" / "Reviewed memory.md"
    parsed = parse_front_matter(target.read_text(encoding="utf-8"))
    assert type(parsed.data[PERSONAL_MEMORY_MARKER]) is int
    assert parsed.data[PERSONAL_MEMORY_MARKER] == 1
    assert parsed.data["evidence_kind"] == "user_statement"
    assert parsed.data["self_kind"] == "preference"
    assert parsed.data["evidence_at"] == "2026-09-05T16:55:00+03:00"
    assert parsed.data["evidence_at_precision"] == "exact"
    assert parsed.data["domain"] == "career"
    assert parsed.body == make_personal_memory_draft().draft.content
    after = snapshot_tree(vault)
    assert set(after) - set(before) == {"10 Projects/Reviewed memory.md"}
    assert {path: after[path] for path in before} == before


def test_personal_memory_unknown_time_is_written_without_created_fallback(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    draft = make_personal_memory_draft(
        evidence_kind=EvidenceKind.EXPLICIT_USER_FACT,
        self_kind=SelfKind.MEMORY,
        evidence_at="unknown",
        evidence_at_precision=EvidenceAtPrecision.UNKNOWN,
        domain=None,
    )

    result = create_service(vault).execute(
        CreateManagedNoteFromPersonalMemoryDraftRequest(draft=draft, apply=True, now=FIXED_NOW)
    )

    assert result.status is CreateStatus.CREATED
    target = vault / "10 Projects" / "Reviewed memory.md"
    parsed = parse_front_matter(target.read_text(encoding="utf-8"))
    assert parsed.data["evidence_at"] == "unknown"
    assert parsed.data["evidence_at_precision"] == "unknown"
    assert "domain" not in parsed.data
    assert parsed.data["created"] == FIXED_NOW.replace(microsecond=0).isoformat()


def test_personal_memory_rejects_invalid_draft_before_vault_scan() -> None:
    class FailReader:
        def scan(self) -> object:
            pytest.fail("invalid Personal Memory draft must be rejected before scan")

    class FailWriter:
        def prepare_from_personal_memory_draft(self, *args: object, **kwargs: object) -> object:
            pytest.fail("invalid Personal Memory draft must be rejected before plan")

    invalid = make_personal_memory_draft(evidence_kind="observed_decision")
    result = CreateManagedNoteFromPersonalMemoryDraft(
        FailReader(),  # type: ignore[arg-type]
        FailWriter(),  # type: ignore[arg-type]
    ).execute(CreateManagedNoteFromPersonalMemoryDraftRequest(invalid, apply=True))

    assert result.status is CreateStatus.REJECTED
    assert result.diagnostics[0].code == "PERSONAL_MEMORY_INVALID_EVIDENCE_KIND"


def test_personal_memory_does_not_overwrite_existing_target(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    target = write_note(vault, "10 Projects/Reviewed memory.md", "original\n")

    result = create_service(vault).execute(
        CreateManagedNoteFromPersonalMemoryDraftRequest(
            make_personal_memory_draft(),
            apply=True,
            now=FIXED_NOW,
        )
    )

    assert result.status is CreateStatus.REJECTED
    assert result.diagnostics[0].code == "CREATE_TARGET_EXISTS"
    assert target.read_text(encoding="utf-8") == "original\n"


def test_personal_memory_post_write_failure_rolls_back_exact_created_file(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    draft = make_personal_memory_draft(content="# Body\n\n[[Missing note]]\n")

    result = create_service(vault).execute(
        CreateManagedNoteFromPersonalMemoryDraftRequest(draft, apply=True, now=FIXED_NOW)
    )

    assert result.status is CreateStatus.ROLLED_BACK
    assert result.rollback_succeeded is True
    assert result.plan is not None
    assert not (vault / result.plan.relative_path).exists()


def test_personal_memory_search_uses_existing_title_body_tags_projection_only(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", enrolled_note(body="# Searchable assertion\n"))
    reader = FileSystemVaultReader(vault)
    from second_brain.adapters.search import SqliteFts5SearchIndex

    index = SqliteFts5SearchIndex()
    try:
        hits = SearchVault(reader, index).execute(SearchRequest("Searchable"))
    finally:
        index.close()

    assert len(hits) == 1
    assert not hasattr(hits[0], "evidence_kind")
    assert not hasattr(hits[0], "self_kind")
    assert not hasattr(hits[0], "domain")


def test_personal_memory_draft_has_no_arbitrary_metadata_mapping() -> None:
    draft = make_personal_memory_draft()

    assert not hasattr(draft, "metadata_map")
    with pytest.raises(TypeError):
        PersonalMemoryDraft(  # type: ignore[call-arg]
            draft=draft.draft,
            evidence_kind=draft.evidence_kind,
            self_kind=draft.self_kind,
            evidence_at=draft.evidence_at,
            evidence_at_precision=draft.evidence_at_precision,
            domain=draft.domain,
            front_matter={"unsafe": True},
        )


def test_personal_memory_draft_json_like_values_cannot_be_written_as_marker() -> None:
    draft = make_personal_memory_draft()
    payload = json.dumps({"marker": True, "draft": repr(draft)}, ensure_ascii=False)

    assert PERSONAL_MEMORY_MARKER not in payload
