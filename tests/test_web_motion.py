"""Static contracts for the bounded Web motion system."""

from __future__ import annotations

from pathlib import Path

STATIC_DIR = Path(__file__).parents[1] / "src" / "second_brain" / "entrypoints" / "web" / "static"


def _read(name: str) -> str:
    """Read one packaged static asset as UTF-8."""

    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_motion_tokens_and_ambient_layer_are_named_and_low_frequency() -> None:
    """Keep ambient motion independent, decorative, and intentionally slow."""

    css = _read("app.css")

    assert "--sb-duration-press: 140ms;" in css
    assert "--sb-duration-short: 180ms;" in css
    assert "--sb-duration-panel: 280ms;" in css
    assert "--sb-duration-busy: 1s;" in css
    assert "--sb-duration-ambient: 24s;" in css
    assert "body::before" in css
    assert "pointer-events: none;" in css
    assert "animation: sb-ambient-drift var(--sb-duration-ambient)" in css
    assert "1 alternate both;" in css
    assert "infinite alternate" not in css
    assert "@keyframes sb-ambient-drift" in css
    assert "transform: translate3d" in css
    assert "overflow-x: clip;" in css
    assert "transition: all" not in css
    assert "background-color var(--sb-duration-short)" not in css
    assert "border-color var(--sb-duration-short)" not in css
    assert "color var(--sb-duration-short)" not in css
    assert "box-shadow var(--sb-duration-short)" not in css
    assert "scale(0)" not in css


def test_motion_primitives_cover_state_arrival_feedback_and_details() -> None:
    """Keep all requested arrival and interaction surfaces on shared primitives."""

    css = _read("app.css")
    for selector in (
        ".draft-result",
        ".save-plan",
        ".saved-note",
        ".simulate-me-result",
        ".search-hit",
        ".retrieved-note",
        ".self-retrieval-item",
        ".self-retrieval-exclusions",
    ):
        assert selector in css

    assert "@starting-style" in css
    assert ".motion-details > summary" in css
    assert ".motion-details[open] > :not(summary)" in css
    assert ":nth-child(-n + 6)" in css
    assert '[aria-busy="true"]' in css
    assert "@keyframes sb-busy-pulse" in css
    assert ".topnav a:active:not(:focus-visible)" in css
    assert ".review-button:active:not(:disabled)" in css
    assert ".review-button:active:not(:disabled):not(:focus-visible)" in css

    motion_section = css.split("/* Motion system v1:", 1)[1].split("@media (max-width: 760px)", 1)[
        0
    ]
    assert ".timeline-item:nth-child(-n + 6)" in motion_section
    assert ".self-model-claim:nth-child(-n + 6)" in motion_section

    reduced_motion_section = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".timeline-item:nth-child(-n + 6)" in reduced_motion_section
    assert ".self-model-claim:nth-child(-n + 6)" in reduced_motion_section


def test_motion_respects_touch_hover_and_reduced_motion_contracts() -> None:
    """Keep feedback bounded and preserve a first-class reduced-motion path."""

    css = _read("app.css")

    assert ":active" in css
    assert "@media (hover: hover) and (pointer: fine)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "body::before {\n    animation: none;" in css
    assert "transition-duration: 0.01ms !important;" in css
    assert "animation-iteration-count: 1 !important;" in css
    assert "transform: none;" in css
    reduced_motion_section = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".topnav a:active," in reduced_motion_section
    assert "transform: none;" in reduced_motion_section
    reduced_motion_section = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".topnav a:active" in reduced_motion_section
    assert ".review-button:active:not(:disabled)" in reduced_motion_section


def test_motion_consumers_match_existing_dynamic_dom_contracts() -> None:
    """Ensure the CSS primitives cover the current JS-created result classes."""

    scripts = "\n".join(path.read_text(encoding="utf-8") for path in STATIC_DIR.glob("*.js"))
    html = _read("index.html")

    for selector in (
        'article.className = known ? "timeline-item"',
        'article.className = "self-model-claim"',
        'article.className = "search-hit"',
        'article.className = "self-retrieval-item"',
        'section.className = "self-retrieval-exclusions"',
        'plan.className = "save-plan"',
        'saved.className = "saved-note"',
    ):
        assert selector in scripts

    for selector in ('class="draft-result"', 'class="simulate-me-result"'):
        assert selector in html

    assert "retrievedNote.hidden = false" in _read("app.js")
