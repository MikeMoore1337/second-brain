"""Bounded metadata-only Harness2 integration; no vault writes."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "harness2_import_note.py"
spec = importlib.util.spec_from_file_location("harness2_import_note", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(tmp_path: Path, **replacements: object) -> Path:
    target = tmp_path / "summary.json"
    obj = {
        "task_id": "case-1",
        "domain": "work",
        "k": 1,
        "seed": 3,
        "status": "ok",
        "base": [],
        "modes": {"agg": {"ok": True}},
        "selections": {"agg": {"why": "secret-like freeform model text"}},
        "errors": [],
    }
    obj.update(replacements)
    target.write_text(json.dumps(obj), encoding="utf-8")
    return target


def test_valid_note_is_read_only_and_sanitized(tmp_path: Path) -> None:
    path = fixture(tmp_path)
    previous = path.read_bytes()
    output = module.note_from_summary(path)
    assert path.read_bytes() == previous
    assert sorted(tmp_path.iterdir()) == [path]
    assert module.UPSTREAM_SHA in output
    assert "НЕ ПОДТВЕРЖДЕНА" in output
    assert "secret-like" not in output


@pytest.mark.parametrize(
    "update",
    [
        {"task_id": "../escape"},
        {"domain": "<script>"},
        {"k": True},
        {"k": -1},
        {"modes": {"evil": {}}},
        {"status": "completed_approved"},
        {"other": True},
    ],
)
def test_unsafe_metadata_fails_closed(tmp_path: Path, update: dict[str, object]) -> None:
    path = fixture(tmp_path, **update)
    with pytest.raises(module.UntrustedUpstreamError):
        module.note_from_summary(path)


@pytest.mark.parametrize("native_status", ["running", "ok", "partial", "skipped", "base-failed"])
def test_original_upstream_statuses_supported_without_credibility_upgrade(
    tmp_path: Path, native_status: str
) -> None:
    path = fixture(tmp_path, status=native_status)
    note = module.note_from_summary(path)
    assert native_status in note
    assert "НЕ ПОДТВЕРЖДЕНА" in note
    assert "secret-like" not in note


@pytest.mark.parametrize("invalid_status", ["failed", "success", "PASS", "approved"])
def test_non_upstream_status_rejected(tmp_path: Path, invalid_status: str) -> None:
    path = fixture(tmp_path, status=invalid_status)
    with pytest.raises(module.UntrustedUpstreamError):
        module.note_from_summary(path)


def test_bad_json_and_relative_paths_fail_closed(tmp_path: Path) -> None:
    path = fixture(tmp_path)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(module.UntrustedUpstreamError):
        module.note_from_summary(path)
    with pytest.raises(module.UntrustedUpstreamError):
        module.note_from_summary(Path("summary.json"))


def test_linked_file_is_rejected(tmp_path: Path) -> None:
    path = fixture(tmp_path)
    link = tmp_path / "linked"
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip("symlink unsupported")
    with pytest.raises(module.UntrustedUpstreamError):
        module.note_from_summary(link)
