"""Интеграционные проверки read-only vault scanner."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.reports import ScanReport
from second_brain.application.services import ValidateVault
from tests.conftest import (
    SECOND_NOTE_ID,
    VALID_NOTE_ID,
    create_vault,
    managed_note,
    snapshot_tree,
    write_note,
)


def validate_vault(vault: Path) -> ScanReport:
    return ValidateVault(FileSystemVaultReader(vault)).execute()


def persisted_source_yaml(
    *,
    omit: str | None = None,
    uri: str = "https://example.com/article",
    kind: str = "web",
    retrieved_at: str = "2026-09-04T20:00:00+03:00",
    title: str = "Заголовок источника",
    author: str = "Автор на русском",
    published_at: str = "2026-09-03T10:00:00+00:00",
    upstream_id: str = "upstream-123",
) -> str:
    """Собрать deterministic persisted sources mapping для scanner tests."""

    values = {
        "uri": uri,
        "kind": kind,
        "retrieved_at": retrieved_at,
        "title": title,
        "author": author,
        "published_at": published_at,
        "upstream_id": upstream_id,
    }
    lines = ["sources:"]
    item_started = False
    for field, value in values.items():
        if field == omit:
            continue
        indent = "    " if item_started else "  - "
        if field in {"title", "author", "upstream_id"}:
            lines.append(f'{indent}{field}: "{value}"')
        else:
            lines.append(f"{indent}{field}: {value}")
        item_started = True
    return "\n".join(lines)


def report_with_front_matter(tmp_path: Path, extra: str) -> ScanReport:
    """Проверить одну managed note с дополнительным YAML front matter."""

    vault = create_vault(tmp_path / "vault")
    note = managed_note().replace("tags: []", f"tags: []\n{extra}", 1)
    write_note(vault, "10 Projects/Note.md", note)
    return validate_vault(vault)


def diagnostic_codes(report: ScanReport) -> list[str]:
    """Вернуть codes для assertions focused validation tests."""

    return [item.code for item in report.diagnostics]


def test_valid_vault_links_and_embeds_scan_without_errors(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Project.md",
        managed_note(VALID_NOTE_ID, "project")
        + "\n[[Zettel]] [[40 Zettelkasten/Zettel#Heading]] ![[image.png]] [[#Local]]\n",
    )
    write_note(vault, "40 Zettelkasten/Zettel.md", managed_note(SECOND_NOTE_ID))
    (vault / "_attachments" / "image.png").write_bytes(b"image")

    report = validate_vault(vault)

    assert report.error_count == 0
    assert len(report.notes) == 2
    assert len(report.links) == 4
    assert report.attachments[0].relative_path == "_attachments/image.png"


def test_managed_note_without_sources_remains_valid(tmp_path: Path) -> None:
    report = report_with_front_matter(tmp_path, "")

    assert report.error_count == 0


def test_valid_persisted_sources_scan_without_errors(tmp_path: Path) -> None:
    report = report_with_front_matter(tmp_path, persisted_source_yaml())

    assert report.error_count == 0


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ('sources: {uri: "https://example.com/article"}', "NOTE_INVALID_SOURCES"),
        ("sources: []", "NOTE_INVALID_SOURCE_COUNT"),
        (
            "sources:\n  - https://example.com/article\n",
            "NOTE_INVALID_SOURCE_RECORD",
        ),
        (
            persisted_source_yaml() + "\n  - uri: https://example.com/second\n    kind: web\n"
            "    retrieved_at: 2026-09-04T20:00:00+03:00",
            "NOTE_INVALID_SOURCE_COUNT",
        ),
    ],
)
def test_invalid_persisted_sources_shape_is_reported_as_error(
    tmp_path: Path,
    extra: str,
    code: str,
) -> None:
    report = report_with_front_matter(tmp_path, extra)

    assert report.error_count >= 1
    assert code in diagnostic_codes(report)


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("uri", "NOTE_SOURCE_MISSING_URI"),
        ("kind", "NOTE_SOURCE_MISSING_KIND"),
        ("retrieved_at", "NOTE_SOURCE_MISSING_RETRIEVED_AT"),
    ],
)
def test_persisted_sources_require_v1_fields(
    tmp_path: Path,
    field: str,
    code: str,
) -> None:
    report = report_with_front_matter(tmp_path, persisted_source_yaml(omit=field))

    assert report.error_count >= 1
    assert code in diagnostic_codes(report)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("kind", "not-a-kind", "NOTE_SOURCE_INVALID_KIND"),
        ("uri", "relative/path", "NOTE_SOURCE_INVALID_URI"),
        ("retrieved_at", "2026-09-04T20:00:00", "NOTE_SOURCE_INVALID_RETRIEVED_AT"),
        ("published_at", "2026-09-03T10:00:00", "NOTE_SOURCE_INVALID_PUBLISHED_AT"),
    ],
)
def test_persisted_sources_reuse_source_value_validation(
    tmp_path: Path,
    field: str,
    value: str,
    code: str,
) -> None:
    values = {
        "uri": "https://example.com/article",
        "kind": "web",
        "retrieved_at": "2026-09-04T20:00:00+03:00",
        "title": "Заголовок источника",
        "author": "Автор на русском",
        "published_at": "2026-09-03T10:00:00+00:00",
        "upstream_id": "upstream-123",
    }
    values[field] = value
    report = report_with_front_matter(tmp_path, persisted_source_yaml(**values))

    assert report.error_count >= 1
    assert code in diagnostic_codes(report)


@pytest.mark.parametrize("field", ["title", "author", "upstream_id"])
def test_persisted_sources_reject_control_or_newline_metadata(
    tmp_path: Path,
    field: str,
) -> None:
    values = {
        "uri": "https://example.com/article",
        "kind": "web",
        "retrieved_at": "2026-09-04T20:00:00+03:00",
        "title": "Заголовок источника",
        "author": "Автор на русском",
        "published_at": "2026-09-03T10:00:00+00:00",
        "upstream_id": "upstream-123",
    }
    values[field] = r"bad\nheader"
    report = report_with_front_matter(tmp_path, persisted_source_yaml(**values))

    assert report.error_count >= 1
    assert "NOTE_SOURCE_INVALID_METADATA" in diagnostic_codes(report)


def test_persisted_sources_reject_oversized_metadata(tmp_path: Path) -> None:
    values = {
        "uri": "https://example.com/article",
        "kind": "web",
        "retrieved_at": "2026-09-04T20:00:00+03:00",
        "title": "x" * 16_385,
        "author": "Автор на русском",
        "published_at": "2026-09-03T10:00:00+00:00",
        "upstream_id": "upstream-123",
    }
    report = report_with_front_matter(tmp_path, persisted_source_yaml(**values))

    assert report.error_count >= 1
    assert "NOTE_SOURCE_INVALID_METADATA" in diagnostic_codes(report)


def test_persisted_sources_keep_unicode_metadata_valid(tmp_path: Path) -> None:
    values = {
        "uri": "https://example.com/article",
        "kind": "web",
        "retrieved_at": "2026-09-04T20:00:00+03:00",
        "title": "Заголовок — русский текст",
        "author": "Автор / редактор",
        "published_at": "2026-09-03T10:00:00+00:00",
        "upstream_id": "идентификатор-42",
    }
    report = report_with_front_matter(tmp_path, persisted_source_yaml(**values))

    assert report.error_count == 0


def test_managed_and_unmanaged_inbox_rules_are_explicit(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "00 Inbox/raw.md", "# Raw\n\nБез metadata.\n")
    write_note(vault, "00 Inbox/with-tags.md", "---\ntags: [idea]\n---\n# Draft\n")
    write_note(vault, "00 Inbox/partial.md", "---\nid: null\n---\n# Partial\n")
    write_note(vault, "10 Projects/missing.md", "# Missing\n")

    snapshot = FileSystemVaultReader(vault).scan()
    assert not any(item.code == "NOTE_INVALID_ID" for item in snapshot.diagnostics)

    report = validate_vault(vault)
    codes = [item.code for item in report.diagnostics]

    assert codes.count("UNMANAGED_INBOX_NOTE") == 2
    assert "NOTE_INVALID_ID" in codes
    assert codes.count("NOTE_MISSING_TYPE") >= 1
    assert codes.count("NOTE_MISSING_TIMESTAMP") >= 1


def test_duplicate_ids_and_broken_ambiguous_links_are_reported(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/One.md",
        managed_note(VALID_NOTE_ID, "project") + "\n[[Missing]] [[Shared]]\n",
    )
    write_note(vault, "10 Projects/Two.md", managed_note(VALID_NOTE_ID, "project"))
    write_note(vault, "10 Projects/A/Shared.md", managed_note(SECOND_NOTE_ID))
    write_note(
        vault, "10 Projects/B/Shared.md", managed_note("0198f4c5-6a00-7000-8000-000000000004")
    )

    report = validate_vault(vault)
    codes = [item.code for item in report.diagnostics]

    assert "DUPLICATE_NOTE_ID" in codes
    assert "BROKEN_WIKILINK" in codes
    assert "AMBIGUOUS_WIKILINK" in codes


def test_scan_does_not_modify_vault(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Note.md", managed_note())
    before = snapshot_tree(vault)

    validate_vault(vault)

    assert snapshot_tree(vault) == before


def test_body_yaml_like_delimiters_do_not_drop_wikilinks(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Source.md",
        managed_note() + "\n---\n[[Hidden]]\n---\n[[Visible]]\n",
    )

    snapshot = FileSystemVaultReader(vault).scan()

    assert [link.target for link in snapshot.links] == ["Hidden", "Visible"]


def test_obsidian_service_directories_are_not_notes(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, ".obsidian/metadata.md", managed_note())
    write_note(vault, ".trash/deleted.md", managed_note())

    report = validate_vault(vault)

    assert report.notes == ()


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX symlink behavior is tested separately on POSIX CI"
)
def test_posix_symlink_escape_is_not_followed(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    outside = tmp_path / "outside"
    outside.mkdir()
    write_note(outside, "Secret.md", managed_note())
    link = vault / "10 Projects" / "escape"
    link.symlink_to(outside, target_is_directory=True)

    report = validate_vault(vault)

    assert not any(note.relative_path.endswith("Secret.md") for note in report.notes)
    assert any(item.code == "VAULT_LINKED_ENTRY" for item in report.diagnostics)


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX symlink behavior is tested separately on POSIX CI"
)
def test_posix_file_symlink_escape_is_not_read(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    outside = tmp_path / "outside.md"
    outside.write_text(managed_note(), encoding="utf-8")
    link = vault / "10 Projects" / "escape.md"
    link.symlink_to(outside)

    report = validate_vault(vault)

    assert not any(note.relative_path.endswith("escape.md") for note in report.notes)
    assert any(item.code == "VAULT_LINKED_ENTRY" for item in report.diagnostics)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction behavior is conditional")
def test_windows_junction_escape_is_not_followed(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    outside = tmp_path / "outside"
    outside.mkdir()
    write_note(outside, "Secret.md", managed_note())
    junction = vault / "10 Projects" / "escape"
    try:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"junction creation unavailable: {exc}")

    report = validate_vault(vault)

    assert not any(note.relative_path.endswith("Secret.md") for note in report.notes)
    assert any(item.code == "VAULT_LINKED_ENTRY" for item in report.diagnostics)


def test_attachment_threshold_diagnostics_are_configurable(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    manifest = vault / "second-brain.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "warning_size_bytes: 10485760\n  max_size_bytes: 52428800",
            "warning_size_bytes: 10\n  max_size_bytes: 50",
        ),
        encoding="utf-8",
    )
    (vault / "_attachments" / "warning.bin").write_bytes(b"0123456789")
    (vault / "_attachments" / "large.bin").write_bytes(b"0" * 51)

    report = validate_vault(vault)
    codes = [item.code for item in report.diagnostics]

    assert "ATTACHMENT_LARGE" in codes
    assert "ATTACHMENT_TOO_LARGE" in codes
    assert report.attachment_bytes == 61
