"""Static contracts for the #113 capture and write surface redesign."""

from __future__ import annotations

from pathlib import Path

STATIC_DIR = Path(__file__).parents[1] / "src" / "second_brain" / "entrypoints" / "web" / "static"


def _read(name: str) -> str:
    """Read one packaged static asset as UTF-8."""

    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_capture_and_journal_surfaces_expose_one_review_first_route() -> None:
    """The visual route is explicit without changing the existing controls."""

    html = _read("index.html")

    for marker in (
        'class="capture-route"',
        "01</span> Capture",
        "02</span> Review",
        "03</span> Confirm",
        'data-capture-step="capture" aria-current="step"',
        'data-capture-step="review"',
        'data-capture-step="confirm"',
        'class="capture-panel-heading"',
        'class="journal-steps"',
        'data-journal-step="capture" aria-current="step"',
        "01</span> Capture",
        'data-journal-step="review"',
        'data-journal-step="confirm"',
        "02</span> Dry-run diff",
        'data-mode="url"',
        'data-mode="text"',
        'data-mode="voice"',
        "data-decision-prepare",
        "data-decision-confirm",
        "data-outcome-prepare",
        "data-outcome-confirm",
    ):
        assert marker in html

    assert 'aria-describedby="add-description"' in html
    assert 'aria-describedby="voice-description"' in html
    assert "LOCAL UI · NETWORKED DRAFT" in html
    assert "LOCAL · REVIEW FIRST" not in html
    assert 'class="review-button review-button-primary review-button-confirm"' in html


def test_capture_write_layout_has_bounded_desktop_and_mobile_composition() -> None:
    """Capture, voice, review and structured journal fields reflow as one flow."""

    css = _read("app.css")

    for marker in (
        ".capture-route",
        ".capture-panel-heading",
        ".voice-panel {",
        "grid-template-columns: minmax(0, 1fr) minmax(260px, 0.85fr);",
        ".review-field-wide",
        ".journal-steps",
        ".review-button-confirm",
        "@media (max-width: 900px)",
        "@media (max-width: 760px)",
        "grid-template-columns: 1fr;",
        "scrollbar-width: thin;",
    ):
        assert marker in css

    assert "min-height: var(--sb-touch-target);" in css
    assert "background: var(--sb-color-void);" in css
    assert "transition: all" not in css


def test_dynamic_review_surface_keeps_safe_write_and_personal_memory_hooks() -> None:
    """Only presentation hooks were added around the existing write contract."""

    script = _read("app.js")

    for marker in (
        'section.className = "review-editor"',
        'phase.textContent = "02 / review"',
        "const setCaptureStep = (currentStep) =>",
        'setCaptureStep("review")',
        'setCaptureStep("confirm")',
        'step.setAttribute("aria-current", "step")',
        'content.parentElement?.classList.add("review-field-wide")',
        'confirmButton.className = "review-button review-button-primary review-button-confirm"',
        'personalMemoryPanel.className = "personal-memory-panel"',
        'endpoint = "/api/drafts/personal-memory/save/prepare"',
        'endpoint = "/api/drafts/personal-memory/save/apply"',
        'preview.className = "markdown-preview"',
        'diff.className = "save-diff"',
    ):
        assert marker in script

    journal_script = _read("decision-journal.js")
    for marker in (
        "const setJournalStep = (currentStep) =>",
        "const journalStepForMode = (mode) =>",
        'setJournalStep("capture")',
        'setJournalStep("confirm")',
        'step.setAttribute("aria-current", "step")',
    ):
        assert marker in journal_script
