"""Разбор и проверка корневого manifest vault."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, cast

from ruamel.yaml import YAML

from second_brain.domain.models import (
    AttachmentPolicy,
    Diagnostic,
    DiagnosticSeverity,
    VaultManifest,
    VaultPaths,
    parse_uuid7,
)

SUPPORTED_SCHEMA_VERSION = 1
REQUIRED_PATH_KEYS = (
    "inbox",
    "projects",
    "areas",
    "resources",
    "zettelkasten",
    "archive",
    "templates",
    "attachments",
)
_KNOWN_ROOT_KEYS = {"schema_version", "vault_id", "default_language", "paths", "attachments"}


@dataclass(frozen=True, slots=True)
class ManifestLoadResult:
    """Значение manifest и diagnostics, полученные при его загрузке."""

    manifest: VaultManifest | None
    diagnostics: tuple[Diagnostic, ...]


def _error(code: str, message: str, path: str = "second-brain.yaml") -> Diagnostic:
    return Diagnostic(code, message, DiagnosticSeverity.ERROR, path)


def _warning(code: str, message: str, path: str = "second-brain.yaml") -> Diagnostic:
    return Diagnostic(code, message, DiagnosticSeverity.WARNING, path)


def _mapping(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    return None


def _relative_path(value: object, key: str, diagnostics: list[Diagnostic]) -> PurePosixPath | None:
    if not isinstance(value, str) or not value.strip():
        diagnostics.append(
            _error("MANIFEST_INVALID_PATH", f"paths.{key} must be a non-empty string")
        )
        return None
    normalized = value.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    windows_candidate = PureWindowsPath(normalized)
    if candidate.is_absolute() or windows_candidate.is_absolute() or windows_candidate.drive:
        diagnostics.append(_error("MANIFEST_ABSOLUTE_PATH", f"paths.{key} must be vault-relative"))
        return None
    if not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        diagnostics.append(
            _error(
                "MANIFEST_UNSAFE_PATH", f"paths.{key} must not contain empty, '.' or '..' segments"
            )
        )
        return None
    return candidate


def _positive_int(value: object, key: str, diagnostics: list[Diagnostic]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        diagnostics.append(
            _error("MANIFEST_INVALID_INTEGER", f"attachments.{key} must be a positive integer")
        )
        return None
    return value


def load_manifest(path: Path) -> ManifestLoadResult:
    """Загрузить и проверить один manifest, не обращаясь к другим файлам."""

    diagnostics: list[Diagnostic] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ManifestLoadResult(
            None,
            (_error("MANIFEST_READ_ERROR", f"cannot read manifest: {exc}"),),
        )
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        raw: object = yaml.load(text)
    except Exception as exc:  # ruamel exposes several parser/constructor exception types
        return ManifestLoadResult(
            None,
            (_error("MANIFEST_PARSE_ERROR", f"cannot parse YAML: {exc}"),),
        )
    data = _mapping(raw)
    if data is None:
        return ManifestLoadResult(
            None, (_error("MANIFEST_NOT_MAPPING", "manifest root must be a mapping"),)
        )

    for key in data:
        if key not in _KNOWN_ROOT_KEYS:
            diagnostics.append(_warning("MANIFEST_UNKNOWN_FIELD", f"unknown manifest field: {key}"))

    schema_version = data.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        diagnostics.append(_error("MANIFEST_SCHEMA_VERSION", "schema_version must be an integer"))
    elif schema_version != SUPPORTED_SCHEMA_VERSION:
        diagnostics.append(
            _error(
                "MANIFEST_UNSUPPORTED_SCHEMA",
                f"unsupported schema_version {schema_version}; supported version is "
                f"{SUPPORTED_SCHEMA_VERSION}",
            )
        )

    try:
        vault_id = parse_uuid7(data.get("vault_id"))
    except ValueError as exc:
        diagnostics.append(_error("MANIFEST_VAULT_ID", str(exc)))
        vault_id = None

    default_language = data.get("default_language", "ru")
    if not isinstance(default_language, str) or not default_language.strip():
        diagnostics.append(
            _error("MANIFEST_LANGUAGE", "default_language must be a non-empty string")
        )
        default_language = "ru"

    paths_data = _mapping(data.get("paths"))
    path_values: dict[str, PurePosixPath | None] = {}
    if paths_data is None:
        diagnostics.append(_error("MANIFEST_PATHS", "paths must be a mapping"))
    else:
        for key in REQUIRED_PATH_KEYS:
            if key not in paths_data:
                diagnostics.append(_error("MANIFEST_MISSING_PATH", f"paths.{key} is required"))
            path_values[key] = _relative_path(paths_data.get(key), key, diagnostics)
        for key in paths_data:
            if key not in REQUIRED_PATH_KEYS:
                diagnostics.append(_warning("MANIFEST_UNKNOWN_PATH", f"unknown paths field: {key}"))

    attachments_data = _mapping(data.get("attachments"))
    if attachments_data is None:
        diagnostics.append(_error("MANIFEST_ATTACHMENTS", "attachments must be a mapping"))
        warning_size = max_size = None
    else:
        warning_size = _positive_int(
            attachments_data.get("warning_size_bytes"), "warning_size_bytes", diagnostics
        )
        max_size = _positive_int(
            attachments_data.get("max_size_bytes"), "max_size_bytes", diagnostics
        )
        for key in attachments_data:
            if key not in {"warning_size_bytes", "max_size_bytes"}:
                diagnostics.append(
                    _warning(
                        "MANIFEST_UNKNOWN_ATTACHMENT_FIELD", f"unknown attachments field: {key}"
                    )
                )

    required_values = [path_values.get(key) for key in REQUIRED_PATH_KEYS]
    if len(set(value for value in required_values if value is not None)) != len(
        [value for value in required_values if value is not None]
    ):
        diagnostics.append(
            _error("MANIFEST_DUPLICATE_PATH", "paths must point to distinct vault directories")
        )

    policy: AttachmentPolicy | None = None
    if warning_size is not None and max_size is not None:
        try:
            policy = AttachmentPolicy(warning_size, max_size)
        except ValueError as exc:
            diagnostics.append(_error("MANIFEST_ATTACHMENT_POLICY", str(exc)))

    if (
        any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics)
        or vault_id is None
        or policy is None
        or any(value is None for value in required_values)
    ):
        return ManifestLoadResult(None, tuple(diagnostics))

    assert schema_version == SUPPORTED_SCHEMA_VERSION
    paths = VaultPaths(
        inbox=cast(PurePosixPath, path_values["inbox"]),
        projects=cast(PurePosixPath, path_values["projects"]),
        areas=cast(PurePosixPath, path_values["areas"]),
        resources=cast(PurePosixPath, path_values["resources"]),
        zettelkasten=cast(PurePosixPath, path_values["zettelkasten"]),
        archive=cast(PurePosixPath, path_values["archive"]),
        templates=cast(PurePosixPath, path_values["templates"]),
        attachments=cast(PurePosixPath, path_values["attachments"]),
    )
    return ManifestLoadResult(
        VaultManifest(schema_version, vault_id, default_language, paths, policy),
        tuple(diagnostics),
    )
