from __future__ import annotations

from pathlib import Path

STATIC_DIR = Path(__file__).parents[1] / "src" / "second_brain" / "entrypoints" / "web" / "static"


def _read(name: str) -> str:
    """Read one packaged static asset as UTF-8."""

    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_cognitive_surfaces_keep_the_current_read_only_dom_contract() -> None:
    """The visual redesign must preserve every existing surface and data hook."""

    html = _read("index.html")
    for surface, hook in (
        ("timeline", "data-timeline-surface"),
        ("self-model", "data-self-model-surface"),
        ("simulate-me", "data-simulate-me-surface"),
        ("self-retrieval", "data-self-retrieval-surface"),
        ("search", "data-search-surface"),
    ):
        assert f'id="{surface}"' in html
        assert hook in html

    for hook in (
        "data-timeline-known-list",
        "data-timeline-unknown-list",
        "data-self-model-list",
        "data-simulate-me-result-content",
        "data-self-retrieval-results",
        "data-search-results",
        "data-retrieved-note",
    ):
        assert hook in html

    assert 'href="#timeline"' in html
    assert 'href="#self-model"' in html
    assert 'href="#simulate-me"' in html
    assert 'href="#self-retrieval"' in html
    assert 'href="#search"' in html


def test_cognitive_surfaces_use_signal_routes_without_nested_card_defaults() -> None:
    """Timeline and retrieval hierarchy are expressed by routes and dividers."""

    css = _read("app.css")
    for selector in (
        ".timeline-surface::before",
        ".timeline-group:not(.timeline-group-unknown) .timeline-list::before",
        ".timeline-group:not(.timeline-group-unknown) .timeline-item::before",
        ".self-model-evidence",
        ".simulate-me-result",
        ".retrieved-note",
        ".self-retrieval-exclusions",
    ):
        assert selector in css

    assert "border-radius: 0;" in css
    assert "background: transparent;" in css
    assert "border-top: 1px dashed var(--sb-color-line);" in css
    assert ".simulate-me-result[hidden]" in css
    assert ".retrieved-note[hidden]" in css


def test_cognitive_surfaces_bound_mobile_controls_and_long_evidence() -> None:
    """The current evidence stays reachable at phone width and wraps safely."""

    css = _read("app.css")
    assert "--sb-touch-target" in css
    assert "min-height: var(--sb-touch-target);" in css
    assert "overflow-wrap: anywhere;" in css
    assert "overscroll-behavior: contain;" in css
    assert "scrollbar-gutter: stable;" in css
    assert "@media (max-width: 760px)" in css
    assert "grid-template-columns: 1fr;" in css
    assert ".search-form-row .capture-submit" in css
    assert ".self-retrieval-form-row .capture-submit" in css
    assert "width: 100%;" in css


def test_cognitive_surfaces_keep_safe_rendering_and_shared_motion_contract() -> None:
    """Visual polish does not add persistence, unsafe HTML, or new animation paths."""

    scripts = "\n".join(path.read_text(encoding="utf-8") for path in STATIC_DIR.glob("*.js"))
    for forbidden in ("localStorage", "sessionStorage", "indexedDB", "setInterval"):
        assert forbidden not in scripts

    assert "innerHTML" not in _read("timeline.js")
    assert "innerHTML" not in _read("self-model.js")
    assert "innerHTML" not in _read("simulate-me.js")
    assert "innerHTML" not in _read("self-retrieval.js")
    assert _read("app.js").count("innerHTML") == 1

    css = _read("app.css")
    assert '[aria-busy="true"]' in css
    assert "@media (hover: hover) and (pointer: fine)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "transition: all" not in css
