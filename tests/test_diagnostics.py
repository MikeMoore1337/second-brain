"""Focused bounded doctor diagnostics regressions."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from second_brain.adapters.search import SqliteFts5SearchIndex
from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.diagnostics import BuildDoctorReport, DoctorReport, DoctorStatus
from second_brain.application.reports import VaultRootRole, VaultSnapshot
from second_brain.application.self_retrieval import SelfRetrievalSearchUnavailableError
from second_brain.application.validation import build_report
from second_brain.entrypoints.cli.app import app
from tests.conftest import create_vault, managed_note, snapshot_tree, write_note

GENERATED_AT = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
MEMORY_ID = "0198f4c5-6a00-7000-8000-000000000050"
DECISION_ID = "0198f4c5-6a00-7000-8000-000000000051"
OUTCOME_ID = "0198f4c5-6a00-7000-8000-000000000052"
runner = CliRunner()


def _set_manifest_path(vault: Path, key: str, value: str) -> None:
    manifest = vault / "second-brain.yaml"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    prefix = f"  {key}: "
    matches = [index for index, line in enumerate(lines) if line.startswith(prefix)]
    assert len(matches) == 1
    lines[matches[0]] = prefix + value
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _enrolled_note(
    note_id: str,
    *,
    evidence_kind: str = "user_statement",
    self_kind: str = "memory",
    body: str = "Каноническая память.",
    decision_id: str | None = None,
) -> str:
    lines = [
        "---",
        f"id: {note_id}",
        "type: zettel",
        "created: 2026-09-07T10:00:00+00:00",
        "tags: []",
        "second_brain_personal_memory: 1",
        f"evidence_kind: {evidence_kind}",
        f"self_kind: {self_kind}",
        "evidence_at: 2026-09-07T09:00:00+00:00",
        "evidence_at_precision: exact",
    ]
    if decision_id is not None:
        lines.append(f"decision_id: {decision_id}")
    return "\n".join([*lines, "---", body, ""])


def _decision_body() -> str:
    return (
        "## Situation\nСитуация.\n\n"
        "## Available options\n- Первый\n- Второй\n\n"
        "## Information known at decision time\nОграничения известны.\n\n"
        "## Criteria\n- Скорость\n\n"
        "## Chosen option\nПервый\n\n"
        "## Reasons\nСоответствует цели.\n\n"
        "## Confidence\nСредняя\n\n"
        "## Expected result\nОжидаемый результат.\n\n"
        "## Actual result\n\n"
        "## Reassessment\n"
    )


def _outcome_body() -> str:
    return (
        "## Actual result\nПолучено.\n\n## Reassessment\nБез изменений.\n\n## Notes\nПроверено.\n"
    )


def _doctor(vault: Path) -> DoctorReport:
    return BuildDoctorReport(
        FileSystemVaultReader(vault),
        config_resolvable=True,
        clock=lambda: GENERATED_AT,
        search_index_factory=SqliteFts5SearchIndex,
    ).execute()


def test_doctor_reports_counts_and_statuses_from_one_safe_projection(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", _enrolled_note(MEMORY_ID))
    write_note(
        vault,
        "10 Projects/Decision.md",
        _enrolled_note(
            DECISION_ID,
            evidence_kind="observed_decision",
            self_kind="decision",
            body=_decision_body(),
        ),
    )
    write_note(
        vault,
        "10 Projects/Outcome.md",
        _enrolled_note(
            OUTCOME_ID,
            evidence_kind="outcome_later_observation",
            self_kind="outcome",
            body=_outcome_body(),
            decision_id=DECISION_ID,
        ),
    )
    (vault / "_attachments" / "small.bin").write_bytes(b"1234")
    before = snapshot_tree(vault)

    report = _doctor(vault)

    assert report.status is DoctorStatus.HEALTHY
    assert report.generated_at == GENERATED_AT
    assert report.config_resolvable is True
    assert report.manifest_available is True
    assert report.content_roots_available is True
    assert report.managed_note_count == 3
    assert report.enrolled_personal_memory_count == 3
    assert report.valid_decision_count == 1
    assert report.valid_outcome_count == 1
    assert report.attachments_scan_complete is True
    assert report.attachment_count == 1
    assert report.attachment_total == 4
    assert report.timeline.status is DoctorStatus.HEALTHY
    assert report.self_model.status is DoctorStatus.HEALTHY
    assert report.self_retrieval.status is DoctorStatus.HEALTHY
    assert snapshot_tree(vault) == before

    payload = report.as_dict()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["config"] == {"resolvable": True}
    assert payload["vault"]["manifest_available"] is True
    assert payload["vault"]["content_roots_available"] is True
    assert payload["attachments"] == {
        "scan_complete": True,
        "count": 1,
        "total_bytes": 4,
    }
    assert "vault_path" not in payload
    assert "paths" not in payload["manifest"]
    assert "second-brain.yaml" not in serialized


def test_warning_only_diagnostics_make_doctor_degraded(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "00 Inbox/Unmanaged.md", "# Черновик без managed-полей.\n")

    report = _doctor(vault)

    assert report.warning_count == 1
    assert report.error_count == 0
    assert report.status is DoctorStatus.DEGRADED
    assert report.exit_code == 1
    assert report.managed_note_count == 0


def test_missing_content_root_makes_content_counts_and_required_layers_unavailable(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    (vault / "20 Areas").rmdir()

    report = _doctor(vault)

    assert report.status is DoctorStatus.UNAVAILABLE
    assert report.manifest_available is True
    assert report.content_roots_available is False
    assert report.managed_note_count is None
    assert report.enrolled_personal_memory_count is None
    assert report.valid_decision_count is None
    assert report.valid_outcome_count is None
    assert report.timeline.status is DoctorStatus.UNAVAILABLE
    assert report.self_model.status is DoctorStatus.UNAVAILABLE
    assert any(item.code == "VAULT_ROOT_MISSING" for item in report.diagnostics)


@pytest.mark.parametrize("support_key", ["templates", "attachments"])
def test_nested_support_root_failure_does_not_hide_content_or_required_models(
    tmp_path: Path,
    support_key: str,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", managed_note())
    _set_manifest_path(vault, support_key, f"10 Projects/missing-{support_key}")

    report = _doctor(vault)

    assert report.content_roots_available is True
    assert report.managed_note_count == 1
    assert report.enrolled_personal_memory_count == 0
    assert report.valid_decision_count == 0
    assert report.valid_outcome_count == 0
    assert report.timeline.status is DoctorStatus.HEALTHY
    assert report.self_model.status is DoctorStatus.HEALTHY
    if support_key == "attachments":
        assert report.attachments_scan_complete is False
        assert report.attachment_total is None
    else:
        assert report.attachments_scan_complete is True


def test_support_only_overlap_does_not_block_content_or_derived_layers(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", managed_note())
    support = vault / "support"
    (support / "_templates").mkdir(parents=True)
    (support / "asset.bin").write_bytes(b"asset")
    _set_manifest_path(vault, "templates", "support/_templates")
    _set_manifest_path(vault, "attachments", "support")

    snapshot = FileSystemVaultReader(vault).scan()
    report = _doctor(vault)
    overlap = next(item for item in snapshot.diagnostics if item.code == "VAULT_OVERLAPPING_ROOTS")

    assert overlap.involved_root_roles == (
        VaultRootRole.TEMPLATES,
        VaultRootRole.ATTACHMENTS,
    )
    assert report.content_roots_available is True
    assert report.managed_note_count == 1
    assert report.timeline.status is DoctorStatus.HEALTHY
    assert report.self_model.status is DoctorStatus.HEALTHY
    assert report.attachments_scan_complete is False
    assert report.attachment_total is None


def test_content_support_overlap_blocks_only_content_derived_scope(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", managed_note())
    (vault / "10 Projects" / "_templates").mkdir()
    _set_manifest_path(vault, "templates", "10 Projects/_templates")

    snapshot = FileSystemVaultReader(vault).scan()
    report = _doctor(vault)
    overlap = next(
        item
        for item in snapshot.diagnostics
        if item.code == "VAULT_OVERLAPPING_ROOTS"
        and item.involved_root_roles == (VaultRootRole.PROJECTS, VaultRootRole.TEMPLATES)
    )

    assert overlap.root_role is VaultRootRole.GLOBAL
    assert report.content_roots_available is False
    assert report.managed_note_count is None
    assert report.timeline.status is DoctorStatus.UNAVAILABLE
    assert report.self_model.status is DoctorStatus.UNAVAILABLE
    assert report.attachments_scan_complete is True


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive identity is Windows-specific")
def test_windows_case_insensitive_support_spelling_preserves_typed_roles(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    support = vault / "support"
    support.mkdir()
    (support / "asset.bin").write_bytes(b"asset")
    write_note(vault, "10 Projects/Memory.md", managed_note())
    _set_manifest_path(vault, "templates", "SUPPORT")
    _set_manifest_path(vault, "attachments", "support")

    snapshot = FileSystemVaultReader(vault).scan()
    report = _doctor(vault)
    overlap = next(item for item in snapshot.diagnostics if item.code == "VAULT_OVERLAPPING_ROOTS")

    assert overlap.involved_root_roles == (
        VaultRootRole.TEMPLATES,
        VaultRootRole.ATTACHMENTS,
    )
    assert report.content_roots_available is True
    assert report.timeline.status is DoctorStatus.HEALTHY
    assert report.self_model.status is DoctorStatus.HEALTHY
    assert report.attachment_total is None


def test_content_note_read_error_nulls_content_counts_and_derived_layers(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Good.md", managed_note())
    (vault / "10 Projects" / "Unreadable.md").write_bytes(b"\xff\xfe\xfd")

    report = _doctor(vault)

    assert report.content_roots_available is True
    assert report.managed_note_count is None
    assert report.enrolled_personal_memory_count is None
    assert report.timeline.status is DoctorStatus.UNAVAILABLE
    assert report.self_model.status is DoctorStatus.UNAVAILABLE
    assert report.self_retrieval.code == "SELF_RETRIEVAL_SELF_MODEL_UNAVAILABLE"
    assert any(item.code == "NOTE_READ_ERROR" for item in report.diagnostics)


@pytest.mark.parametrize("failure", ["traversal", "stat"])
def test_attachment_failures_only_null_attachment_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", managed_note())
    attachment_root = vault / "_attachments"
    attachment = attachment_root / "asset.bin"
    attachment.write_bytes(b"asset")

    if failure == "traversal":
        original_iterdir = Path.iterdir

        def failing_iterdir(path: Path) -> Iterator[Path]:
            if path == attachment_root:
                raise OSError("private traversal detail")
            return original_iterdir(path)

        monkeypatch.setattr(Path, "iterdir", failing_iterdir)
    else:
        original_stat: Any = Path.stat

        def failing_stat(path: Path, *args: Any, **kwargs: Any) -> Any:
            if path == attachment:
                raise OSError("private stat detail")
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", failing_stat)

    report = _doctor(vault)

    assert report.content_roots_available is True
    assert report.managed_note_count == 1
    assert report.timeline.status is DoctorStatus.HEALTHY
    assert report.self_model.status is DoctorStatus.HEALTHY
    assert report.attachments_scan_complete is False
    assert report.attachment_total is None
    assert any(
        item.code
        in {
            "ATTACHMENT_DIRECTORY_READ_ERROR",
            "ATTACHMENT_STAT_ERROR",
            "VAULT_ENTRY_RESOLVE_ERROR",
        }
        for item in report.diagnostics
    )


def test_doctor_uses_one_underlying_scan_snapshot(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", managed_note())
    snapshot = FileSystemVaultReader(vault).scan()

    class CountingReader:
        def __init__(self) -> None:
            self.calls = 0

        def scan(self) -> VaultSnapshot:
            self.calls += 1
            return snapshot

    reader = CountingReader()
    report = BuildDoctorReport(
        reader,
        config_resolvable=True,
        clock=lambda: GENERATED_AT,
        search_index_factory=SqliteFts5SearchIndex,
    ).execute()

    assert report.status is DoctorStatus.HEALTHY
    assert reader.calls == 1


def test_doctor_reports_actual_stage5_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Memory.md", managed_note())

    class FailingSelfRetrieval:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def execute(self, _request: object) -> None:
            raise SelfRetrievalSearchUnavailableError()

    monkeypatch.setattr(
        "second_brain.application.diagnostics.BuildSelfContext",
        FailingSelfRetrieval,
    )

    report = _doctor(vault)

    assert report.status is DoctorStatus.DEGRADED
    assert report.self_retrieval.status is DoctorStatus.UNAVAILABLE
    assert report.self_retrieval.code == "SELF_RETRIEVAL_SEARCH_UNAVAILABLE"


def test_doctor_never_exports_private_details_or_raw_diagnostics(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "private-vault")
    secret = "private body sentinel"
    source_url = "https://private.example/source"
    write_note(
        vault,
        "10 Projects/Private.md",
        _enrolled_note(
            MEMORY_ID,
            evidence_kind="not-a-kind",
            body=f"{secret}\n{source_url}",
        ),
    )
    before = snapshot_tree(vault)

    report = _doctor(vault)
    serialized = json.dumps(report.as_dict(), ensure_ascii=False)

    assert secret not in serialized
    assert source_url not in serialized
    assert str(vault) not in serialized
    assert "Traceback" not in serialized
    assert "raw" not in serialized.casefold()
    assert snapshot_tree(vault) == before


def test_unavailable_config_is_reported_without_private_path(tmp_path: Path) -> None:
    missing_vault = tmp_path / "private-missing-vault"

    result = runner.invoke(
        app,
        ["--vault-path", str(missing_vault), "doctor", "--format", "json"],
    )

    assert result.exit_code == 2
    assert str(missing_vault) not in result.stdout
    assert str(missing_vault) not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "unavailable"
    assert payload["config"] == {"resolvable": False}
    assert payload["diagnostics"] == [
        {"code": "CONFIG_UNAVAILABLE", "severity": "error", "count": 1}
    ]


def test_doctor_snapshot_validation_failure_is_safe(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    snapshot = FileSystemVaultReader(vault).scan()
    assert build_report(snapshot).manifest is not None

    class BrokenReader:
        def scan(self) -> VaultSnapshot:
            raise RuntimeError("private scanner exception")

    report = BuildDoctorReport(
        BrokenReader(),
        config_resolvable=True,
        clock=lambda: GENERATED_AT,
        search_index_factory=SqliteFts5SearchIndex,
    ).execute()

    assert report.status is DoctorStatus.UNAVAILABLE
    assert report.diagnostics[0].code == "DOCTOR_SCAN_UNAVAILABLE"
