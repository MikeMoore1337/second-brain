"""Проверки reviewed research draft -> Safe Write provenance mapping."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from second_brain.adapters.vault import FileSystemVaultReader, FileSystemVaultWriter
from second_brain.adapters.vault.frontmatter import parse_front_matter
from second_brain.application.llm import NoteDraft
from second_brain.application.research import SourceKind, SourceProvenance
from second_brain.application.research_draft import ReviewedResearchDraft
from second_brain.application.services import CreateManagedNoteFromReviewedResearchDraft
from second_brain.application.writes import (
    CreateManagedNoteFromReviewedResearchDraftRequest,
    CreateStatus,
)
from second_brain.domain.models import NoteType, parse_rfc3339, parse_uuid7
from tests.conftest import create_vault, snapshot_tree, write_note


def install_templates(vault: Path, content: str) -> None:
    """Добавить одинаковые templates для всех managed типов."""

    for name in ("Project.md", "Area.md", "Resource.md", "Zettel.md"):
        (vault / "_templates" / name).write_text(content, encoding="utf-8")


def make_source(*, optional: bool = True) -> SourceProvenance:
    """Собрать deterministic provenance для reviewed application boundary."""

    return SourceProvenance(
        uri="https://example.com/research/article",
        source_kind=SourceKind.WEB,
        retrieved_at=datetime(2026, 9, 4, 20, 0, tzinfo=UTC),
        published_at=datetime(2026, 9, 3, 10, 0, tzinfo=UTC) if optional else None,
        title="Источник — заголовок" if optional else None,
        author="Редактор" if optional else None,
        upstream_id="source-42" if optional else None,
    )


def make_reviewed(
    *, content: str = "## Exact reviewed body\n\nНе менять."
) -> ReviewedResearchDraft:
    """Собрать один reviewed NoteDraft с одним source."""

    return ReviewedResearchDraft(
        draft=NoteDraft(
            title="Research-derived note",
            note_type=NoteType.PROJECT,
            content=content,
            tags=("first", "второй"),
            links=("[[Existing candidate]]",),
        ),
        sources=(make_source(),),
    )


def create_service(vault: Path) -> CreateManagedNoteFromReviewedResearchDraft:
    """Подключить reviewed use case к существующим reader/writer adapters."""

    return CreateManagedNoteFromReviewedResearchDraft(
        FileSystemVaultReader(vault),
        FileSystemVaultWriter(vault),
    )


def test_research_dry_run_maps_exact_sources_and_writes_nothing(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(
        vault,
        '---\n# Keep template metadata\ncustom_field: "retained"\n---\n# Ignored template body\n',
    )
    reviewed = make_reviewed()
    before = snapshot_tree(vault)
    fixed_now = datetime(2026, 9, 4, 21, 30, 15, 123456, tzinfo=UTC)

    result = create_service(vault).execute(
        CreateManagedNoteFromReviewedResearchDraftRequest(
            reviewed_draft=reviewed,
            now=fixed_now,
        )
    )

    assert result.status is CreateStatus.DRY_RUN
    assert result.plan is not None
    parsed = parse_front_matter(result.plan.content)
    assert parsed.data["custom_field"] == "retained"
    assert parsed.data["tags"] == ["first", "второй"]
    assert parsed.data["links"] == ["[[Existing candidate]]"]
    assert list(parsed.data["sources"][0]) == [
        "uri",
        "kind",
        "retrieved_at",
        "title",
        "author",
        "published_at",
        "upstream_id",
    ]
    assert parsed.data["sources"] == [
        {
            "uri": "https://example.com/research/article",
            "kind": "web",
            "retrieved_at": "2026-09-04T20:00:00+00:00",
            "title": "Источник — заголовок",
            "author": "Редактор",
            "published_at": "2026-09-03T10:00:00+00:00",
            "upstream_id": "source-42",
        }
    ]
    assert parsed.body == reviewed.draft.content
    assert "https://example.com/research/article" not in parsed.body
    assert snapshot_tree(vault) == before


def test_research_dry_run_omits_none_source_fields_deterministically(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault, "# Template body\n")
    reviewed = ReviewedResearchDraft(
        draft=make_reviewed().draft,
        sources=(make_source(optional=False),),
    )

    result = create_service(vault).execute(
        CreateManagedNoteFromReviewedResearchDraftRequest(reviewed)
    )

    assert result.status is CreateStatus.DRY_RUN
    assert result.plan is not None
    parsed = parse_front_matter(result.plan.content)
    assert parsed.data["sources"] == [
        {
            "uri": "https://example.com/research/article",
            "kind": "web",
            "retrieved_at": "2026-09-04T20:00:00+00:00",
        }
    ]


def test_research_apply_uses_existing_safe_write_and_post_write_validation(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault, "# Ignored template body\n")
    reviewed = make_reviewed()
    fixed_now = datetime(2026, 9, 4, 21, 30, 15, tzinfo=UTC)
    before = snapshot_tree(vault)

    result = create_service(vault).execute(
        CreateManagedNoteFromReviewedResearchDraftRequest(
            reviewed_draft=reviewed,
            apply=True,
            now=fixed_now,
        )
    )

    assert result.status is CreateStatus.CREATED
    assert result.validation_report is not None
    assert result.validation_report.error_count == 0
    target = vault / "10 Projects" / "Research-derived note.md"
    assert target.exists()
    parsed = parse_front_matter(target.read_text(encoding="utf-8"))
    assert parse_uuid7(parsed.data["id"]).version == 7
    assert parsed.data["type"] == "project"
    assert parse_rfc3339(parsed.data["created"]) == fixed_now
    assert parsed.data["tags"] == ["first", "второй"]
    assert parsed.data["links"] == ["[[Existing candidate]]"]
    assert parsed.data["sources"][0]["uri"] == make_source().uri
    assert parsed.body == reviewed.draft.content
    assert make_source().uri not in parsed.body
    after = snapshot_tree(vault)
    assert set(after) - set(before) == {"10 Projects/Research-derived note.md"}
    assert not list((vault / "10 Projects").glob(".second-brain-*.tmp"))


def test_research_apply_rollback_is_unchanged_when_post_write_validation_fails(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault, "# Template body\n")
    reviewed = make_reviewed(content="# Body\n\n[[Missing research link]]\n")

    result = create_service(vault).execute(
        CreateManagedNoteFromReviewedResearchDraftRequest(reviewed, apply=True)
    )

    assert result.status is CreateStatus.ROLLED_BACK
    assert result.rollback_succeeded is True
    assert not (vault / "10 Projects" / "Research-derived note.md").exists()


def test_research_apply_never_overwrites_existing_target(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault, "# Template body\n")
    target = write_note(vault, "10 Projects/Research-derived note.md", "original\n")
    before = target.read_bytes()

    result = create_service(vault).execute(
        CreateManagedNoteFromReviewedResearchDraftRequest(make_reviewed(), apply=True)
    )

    assert result.status is CreateStatus.REJECTED
    assert result.diagnostics[0].code == "CREATE_TARGET_EXISTS"
    assert target.read_bytes() == before


def test_invalid_reviewed_boundary_is_rejected_before_vault_scan(tmp_path: Path) -> None:
    class FailReader:
        def scan(self) -> object:
            raise AssertionError("invalid reviewed draft must be rejected before vault scan")

    class FailWriter:
        def prepare_from_reviewed_research_draft(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("invalid reviewed draft must be rejected before planning")

    invalid = cast(ReviewedResearchDraft, object())
    result = CreateManagedNoteFromReviewedResearchDraft(
        FailReader(),  # type: ignore[arg-type]
        FailWriter(),  # type: ignore[arg-type]
    ).execute(CreateManagedNoteFromReviewedResearchDraftRequest(invalid, apply=True))

    assert result.status is CreateStatus.REJECTED
    assert result.diagnostics[0].code == "REVIEWED_RESEARCH_DRAFT_INVALID"
