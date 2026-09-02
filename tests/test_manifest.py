"""Проверки root manifest."""

from pathlib import Path

from second_brain.adapters.vault.manifest import load_manifest
from second_brain.domain.models import DiagnosticSeverity
from tests.conftest import VALID_VAULT_ID, create_vault


def test_valid_manifest_is_loaded(tmp_path: Path) -> None:
    result = load_manifest(create_vault(tmp_path / "vault") / "second-brain.yaml")

    assert result.manifest is not None
    assert str(result.manifest.vault_id) == VALID_VAULT_ID
    assert result.manifest.paths.inbox.as_posix() == "00 Inbox"
    assert result.manifest.attachments.warning_size_bytes == 10 * 1024 * 1024
    assert result.manifest.attachments.max_size_bytes == 50 * 1024 * 1024
    assert not any(item.severity is DiagnosticSeverity.ERROR for item in result.diagnostics)


def test_unknown_manifest_field_is_a_warning(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    manifest = vault / "second-brain.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "future_field: true\n", encoding="utf-8"
    )

    result = load_manifest(manifest)

    assert result.manifest is not None
    assert any(item.code == "MANIFEST_UNKNOWN_FIELD" for item in result.diagnostics)


def test_unsafe_manifest_path_is_rejected(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    manifest = vault / "second-brain.yaml"
    content = manifest.read_text(encoding="utf-8").replace(
        "projects: 10 Projects", "projects: ../outside"
    )
    manifest.write_text(content, encoding="utf-8")

    result = load_manifest(manifest)

    assert result.manifest is None
    assert any(item.code == "MANIFEST_UNSAFE_PATH" for item in result.diagnostics)


def test_root_manifest_path_is_rejected(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    manifest = vault / "second-brain.yaml"
    content = manifest.read_text(encoding="utf-8").replace("projects: 10 Projects", "projects: .")
    manifest.write_text(content, encoding="utf-8")

    result = load_manifest(manifest)

    assert result.manifest is None
    assert any(item.code == "MANIFEST_UNSAFE_PATH" for item in result.diagnostics)


def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    manifest = vault / "second-brain.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("schema_version: 1", "schema_version: 2"),
        encoding="utf-8",
    )

    result = load_manifest(manifest)

    assert result.manifest is None
    assert any(item.code == "MANIFEST_UNSUPPORTED_SCHEMA" for item in result.diagnostics)
