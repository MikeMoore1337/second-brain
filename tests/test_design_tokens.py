"""Source-level checks for the active v7 token foundation.

These tests read the shared static stylesheet and packaged HTML. They document
the React import boundary but are not a browser rendering or accessibility run.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = PROJECT_ROOT / "src" / "second_brain" / "entrypoints" / "web" / "static" / "app.css"
INDEX_PATH = PROJECT_ROOT / "src" / "second_brain" / "entrypoints" / "web" / "static" / "index.html"


def _css() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


def test_black_violet_token_layer_is_complete() -> None:
    """The stylesheet exposes one namespaced foundation instead of legacy aliases."""

    css = _css()
    required_tokens = (
        "--sb-color-void",
        "--sb-color-ink",
        "--sb-color-basin",
        "--sb-color-focus",
        "--sb-color-accent",
        "--sb-color-violet-signal",
        "--sb-color-success",
        "--sb-color-warning",
        "--sb-color-danger",
        "--sb-container-max",
        "--sb-page-gutter",
        "--sb-layout-min-width",
        "--sb-touch-target",
        "--sb-font-sans",
        "--sb-font-mono",
        "--sb-measure-readable",
        "--sb-shadow-card",
        "--sb-z-overlay",
        "--sb-focus-width",
        "--sb-duration-short",
    )
    for token in required_tokens:
        assert f"{token}:" in css

    for legacy_alias in (
        "--bg:",
        "--bg-soft:",
        "--surface:",
        "--surface-raised:",
        "--line:",
        "--text:",
        "--muted:",
        "--lime:",
        "--mint:",
        "--coral:",
        "--radius-lg:",
        "--radius-md:",
        "--shadow:",
    ):
        assert legacy_alias not in css


def test_gradient_families_are_named_and_consumed() -> None:
    """Radial, conic and linear families have semantic consumers."""

    css = _css()
    gradient_tokens = (
        "--sb-gradient-aurora",
        "--sb-gradient-orbit",
        "--sb-gradient-trace",
        "--sb-gradient-bloom",
        "--sb-gradient-streak",
        "--sb-gradient-surface-accent",
        "--sb-gradient-surface-violet",
        "--sb-gradient-surface-success",
        "--sb-gradient-surface-danger",
    )
    for token in gradient_tokens:
        assert f"{token}:" in css
        assert f"var({token})" in css

    assert "background: radial-gradient" not in css
    assert "background: conic-gradient" not in css
    assert "background: linear-gradient" not in css


def test_legacy_theme_literals_and_framework_drift_are_absent() -> None:
    """The migration removes the old green palette and keeps the plain CSS stack."""

    css = _css()
    for old_literal in (
        "#0f1716",
        "#142018",
        "#15201e",
        "#1b2926",
        "#22332f",
        "#9aada3",
        "#9de4c5",
        "#c9f277",
        "#d9fa98",
        "#edf4ee",
        "#ff9f7a",
        "#ffb08f",
        "rgba(",
        "font-family: Inter",
    ):
        assert old_literal not in css


def test_accessibility_and_responsive_roles_are_wired() -> None:
    """Focus, touch targets and content breakpoint remain part of the CSS contract."""

    css = _css()
    assert "min-width: var(--sb-layout-min-width)" in css
    assert "min-height: var(--sb-touch-target)" in css
    assert "outline: var(--sb-focus-width) solid var(--sb-color-accent)" in css
    assert "--sb-breakpoint-stack: 760px" in css
    assert "@media (max-width: 760px)" in css
    assert "prefers-reduced-motion" in css


def test_mobile_browser_chrome_matches_the_page_ground() -> None:
    """The mobile theme color does not reintroduce the retired green palette."""

    index = INDEX_PATH.read_text(encoding="utf-8")
    assert '<meta name="theme-color" content="#07070b" />' in index
    assert "#0f1716" not in index
