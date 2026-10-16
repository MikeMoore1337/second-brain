"""Regression checks for SHA-pinned UI reference integrity on all platforms."""

import json

from scripts.check_external_ui_references import (
    git_blob_sha,
    main,
    matches_pinned_blob,
)
from scripts.search_ui_ux_catalog import format_results, search


def test_exact_git_blob_matches_every_platform() -> None:
    data = b"first\nsecond\n"
    expected = git_blob_sha(data)
    assert matches_pinned_blob(data, expected, windows_checkout=False)
    assert matches_pinned_blob(data, expected, windows_checkout=True)


def test_windows_crlf_checkout_is_normalized_only_for_verification() -> None:
    source = b"first\nsecond\n"
    checkout = b"first\r\nsecond\r\n"
    expected = git_blob_sha(source)
    assert matches_pinned_blob(checkout, expected, windows_checkout=True)
    assert not matches_pinned_blob(checkout, expected, windows_checkout=False)


def test_modified_content_is_rejected_even_on_windows() -> None:
    expected = git_blob_sha(b"first\nsecond\n")
    assert not matches_pinned_blob(b"first\r\nCHANGED\r\n", expected, windows_checkout=True)
    assert not matches_pinned_blob(b"first\nCHANGED\n", expected, windows_checkout=False)


def test_local_pinned_reference_manifest() -> None:
    assert main() == 0


def test_catalog_serialization_falls_back_for_cp1251() -> None:
    rows = [{"symbol": "×", "label": "прогресс"}]
    output = format_results(rows, encoding="cp1251")
    assert "\\u00d7" in output
    assert json.loads(output) == rows


def test_catalog_serialization_preserves_utf8() -> None:
    rows = [{"symbol": "×"}]
    output = format_results(rows, encoding="utf-8")
    assert "×" in output
    assert json.loads(output) == rows


def test_catalog_search_uses_real_pinned_data() -> None:
    assert search("charts", "progress", 1)
    assert search("app-interface", "input", 1)
    assert search("ux-guidelines", "focus", 1)
