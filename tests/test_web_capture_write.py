"""Static contracts for the capture and Safe Write surface redesign."""

from __future__ import annotations

from pathlib import Path

STATIC_DIR = Path(__file__).parents[1] / "src" / "second_brain" / "entrypoints" / "web" / "static"


def _read(name: str) -> str:
    """Read one packaged static asset as UTF-8."""

    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_capture_write_keeps_source_and_save_dom_contracts() -> None:
    """The visual pass must not rename the existing API and accessibility hooks."""

    html = _read("index.html")
    for hook in (
        'class="capture-panel" data-capture-panel',
        'class="capture-form" data-draft-form',
        "data-url-input",
        "data-text-input",
        "data-voice-panel",
        "data-audio-file",
        "data-result",
        "data-decision-journal-surface",
        "data-decision-form",
        "data-outcome-form",
        "data-decision-plan",
        "data-outcome-plan",
        "data-decision-confirm",
        "data-outcome-confirm",
    ):
        assert hook in html

    assert html.count('data-mode="') == 3
    assert html.count('data-journal-mode="') == 2
    assert 'role="alert"' in html
    assert 'aria-live="polite"' in html


def test_capture_write_styles_make_the_safety_sequence_visually_distinct() -> None:
    """Capture, review, dry-run and confirmed states need separate visual owners."""

    css = _read("app.css")
    for selector in (
        ".capture-form",
        ".voice-panel",
        ".draft-result",
        ".review-editor",
        ".personal-memory-panel",
        ".save-plan",
        ".save-diff",
        ".saved-note",
        ".decision-journal-surface",
        ".journal-form",
    ):
        assert selector in css

    assert 'content: "ПРОВЕРКА";' in css
    assert 'content: "БЕЗОПАСНОЕ СОХРАНЕНИЕ";' in css
    assert ".capture-form {" in css
    assert "border-left: 2px solid rgb(var(--sb-rgb-violet-signal) / 0.62);" in css
    assert "border-left: 3px solid var(--sb-color-success);" in css
    assert "border-left: 3px solid var(--sb-color-accent);" in css


def test_capture_write_is_mobile_first_and_bounds_long_review_content() -> None:
    """Narrow screens keep controls reachable and long diffs inside their owner."""

    css = _read("app.css")
    assert "grid-template-columns: 1fr;" in css
    assert "min-height: var(--sb-touch-target);" in css
    assert "max-height: min(54vh, 480px);" in css
    assert "max-height: min(52vh, 360px);" in css
    assert "overflow-wrap: anywhere;" in css
    assert ".review-actions > .review-button" in css
    assert ".journal-actions > .review-button" in css
    assert "width: 100%;" in css
    assert "@media (max-width: 760px)" in css


def test_capture_write_preserves_safe_text_rendering_and_state_feedback() -> None:
    """The redesign remains text-only and exposes busy/error/reduced-motion paths."""

    app_js = _read("app.js")
    journal_js = _read("decision-journal.js")
    css = _read("app.css")

    assert "diff.textContent = payload.diff" in app_js
    assert "name.textContent = label" in journal_js
    assert app_js.count("innerHTML") == 1
    assert "single dedicated container allowed to receive server-safe preview HTML" in app_js
    assert "innerHTML" not in journal_js
    assert '[data-capture-panel][aria-busy="true"]' in css
    assert '[data-decision-journal-surface][aria-busy="true"]' in css
    assert "@media (prefers-reduced-motion: reduce)" in css
