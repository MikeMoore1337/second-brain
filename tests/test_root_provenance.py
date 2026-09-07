"""Deterministic root-provenance and nested-root isolation regressions."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.reports import (
    VaultRootRole,
    diagnostic_affects_content,
)
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    BuildSelfModel,
    SelfModelRequest,
)
from second_brain.application.timeline import BuildPersonalTimeline, PersonalTimelineRequest
from second_brain.application.validation import build_report
from tests.conftest import create_vault, write_note

GENERATED_AT = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
MEMORY_ID = "0198f4c5-6a00-7000-8000-000000000050"


def _set_manifest_path(vault: Path, key: str, value: str) -> None:
    path = vault / "second-brain.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    prefix = f"  {key}: "
    matches = [index for index, line in enumerate(lines) if line.startswith(prefix)]
    assert len(matches) == 1
    lines[matches[0]] = prefix + value
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _memory_note(note_id: str = MEMORY_ID) -> str:
    return f"""---
id: {note_id}
type: zettel
created: 2026-09-07T10:00:00+00:00
tags: []
second_brain_personal_memory: 1
evidence_kind: user_statement
self_kind: memory
evidence_at: "2026-09-07T09:00:00+00:00"
evidence_at_precision: exact
---
Каноническая память.
"""


def test_root_failures_carry_exact_role_and_safe_metadata(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    shutil.rmtree(vault / "20 Areas")

    snapshot = FileSystemVaultReader(vault).scan()
    diagnostic = next(item for item in snapshot.diagnostics if item.code == "VAULT_ROOT_MISSING")

    assert diagnostic.root_role is VaultRootRole.AREAS
    assert diagnostic.originating_root_role is VaultRootRole.AREAS
    assert diagnostic.involved_root_roles == ()
    assert diagnostic.path == "20 Areas"
    assert str(vault) not in str(diagnostic.as_dict())
    assert diagnostic.as_dict()["root_role"] == "areas"


def test_support_only_overlap_is_bounded_and_does_not_block_content(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    support_root = vault / "support"
    (support_root / "_templates").mkdir(parents=True)
    (support_root / "asset.bin").write_bytes(b"asset")
    (support_root / "_templates" / "template.md").write_text("# template\n", encoding="utf-8")
    _set_manifest_path(vault, "templates", "support/_templates")
    _set_manifest_path(vault, "attachments", "support")

    snapshot = FileSystemVaultReader(vault).scan()
    overlap = next(item for item in snapshot.diagnostics if item.code == "VAULT_OVERLAPPING_ROOTS")
    report = build_report(snapshot)

    assert overlap.root_role is VaultRootRole.GLOBAL
    assert overlap.involved_root_roles == (
        VaultRootRole.TEMPLATES,
        VaultRootRole.ATTACHMENTS,
    )
    assert diagnostic_affects_content(overlap) is False
    assert report.content_scan_complete is True
    assert report.attachments_scan_complete is True
    assert [item.relative_path for item in report.attachments] == ["support/asset.bin"]
    assert not any(note.relative_path.startswith("support/") for note in report.notes)
    assert str(vault) not in str(overlap.as_dict())


def test_nested_support_roots_are_isolated_from_content_scan(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    content_root = vault / "10 Projects"
    templates_root = content_root / "_templates"
    attachments_root = content_root / "_attachments"
    templates_root.mkdir()
    attachments_root.mkdir()
    write_note(vault, "10 Projects/Memory.md", _memory_note())
    write_note(vault, "10 Projects/_templates/Template.md", _memory_note(MEMORY_ID[:-2] + "51"))
    write_note(
        vault,
        "10 Projects/_attachments/Attachment.md",
        _memory_note(MEMORY_ID[:-2] + "52"),
    )
    _set_manifest_path(vault, "templates", "10 Projects/_templates")
    _set_manifest_path(vault, "attachments", "10 Projects/_attachments")

    report = build_report(FileSystemVaultReader(vault).scan())

    assert [note.relative_path for note in report.notes] == ["10 Projects/Memory.md"]
    assert [item.relative_path for item in report.attachments] == [
        "10 Projects/_attachments/Attachment.md"
    ]
    overlaps = [item for item in report.diagnostics if item.code == "VAULT_OVERLAPPING_ROOTS"]
    assert {item.involved_root_roles for item in overlaps} == {
        (VaultRootRole.PROJECTS, VaultRootRole.TEMPLATES),
        (VaultRootRole.PROJECTS, VaultRootRole.ATTACHMENTS),
    }


@pytest.mark.parametrize(
    ("support_key", "support_role"),
    (
        ("templates", VaultRootRole.TEMPLATES),
        ("attachments", VaultRootRole.ATTACHMENTS),
    ),
)
def test_nested_support_failure_does_not_block_timeline_or_self_model(
    tmp_path: Path,
    support_key: str,
    support_role: VaultRootRole,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", _memory_note())
    _set_manifest_path(vault, support_key, f"10 Projects/missing-{support_key}")

    snapshot = FileSystemVaultReader(vault).scan()
    report = build_report(snapshot)
    diagnostic = next(
        item
        for item in report.diagnostics
        if item.code == "VAULT_ROOT_MISSING" and item.root_role is support_role
    )

    assert report.content_scan_complete is True
    assert report.attachments_scan_complete is (support_role is not VaultRootRole.ATTACHMENTS)
    timeline = BuildPersonalTimeline(
        FileSystemVaultReader(vault), clock=lambda: GENERATED_AT
    ).execute(PersonalTimelineRequest())
    self_model = BuildSelfModel(
        FileSystemVaultReader(vault), DEFAULT_SELF_MODEL_POLICY, lambda: GENERATED_AT
    ).execute(SelfModelRequest())

    assert diagnostic.involved_root_roles == ()
    assert timeline.unknown_total + timeline.known_total == 1
    assert self_model.eligible_evidence_count == 1


@pytest.mark.skipif(
    os.name != "nt", reason="case-insensitive resolved identity is Windows-specific"
)
def test_windows_case_alias_and_same_identity_overlap_keep_roles_exact(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", _memory_note())
    _set_manifest_path(vault, "templates", "10 PROJECTS")

    snapshot = FileSystemVaultReader(vault).scan()
    project_root = next(
        (
            item
            for item in snapshot.diagnostics
            if item.code == "VAULT_OVERLAPPING_ROOTS"
            and item.involved_root_roles == (VaultRootRole.PROJECTS, VaultRootRole.TEMPLATES)
        ),
        None,
    )
    project_missing = [
        item
        for item in snapshot.diagnostics
        if item.root_role is VaultRootRole.PROJECTS and item.code == "VAULT_ROOT_MISSING"
    ]

    assert project_root is not None, snapshot.diagnostics
    assert project_root.root_role is VaultRootRole.GLOBAL
    assert project_missing == []
    assert snapshot.documents == ()
    assert str(vault).casefold() not in str(project_root.as_dict()).casefold()
