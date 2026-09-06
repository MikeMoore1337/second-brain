"""Static contracts for the responsive workspace shell."""

from __future__ import annotations

from pathlib import Path

STATIC_DIR = Path(__file__).parents[1] / "src" / "second_brain" / "entrypoints" / "web" / "static"


def _read(name: str) -> str:
    """Read one packaged static asset as UTF-8."""

    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_workspace_shell_preserves_landmarks_and_surface_targets() -> None:
    """Keep the new frame navigable without removing existing product surfaces."""

    html = _read("index.html")

    assert '<header class="topbar">' in html
    assert '<aside class="workspace-rail"' in html
    assert '<nav class="topnav" aria-label="Основные разделы">' in html
    assert '<div class="workspace-content">' in html
    assert html.count("<main") == 1
    assert html.count("<nav") == 1

    for target in (
        "decision-journal",
        "timeline",
        "self-model",
        "simulate-me",
        "self-retrieval",
        "search",
        "memory",
        "growth",
    ):
        assert f'href="#{target}"' in html
        assert f'id="{target}"' in html


def test_workspace_shell_has_bounded_responsive_layout_contract() -> None:
    """Keep desktop, tablet and mobile shell behavior explicit and deterministic."""

    css = _read("app.css")

    assert "grid-template-columns: minmax(160px, 196px) minmax(0, 1fr);" in css
    assert "min-height: calc(100dvh - 124px);" in css
    assert "max-height: calc(100dvh - max(16px, env(safe-area-inset-top, 0px)) - 16px);" in css
    assert "overflow-y: auto;" in css
    assert "env(safe-area-inset-top, 0px)" in css
    assert "top: max(12px, env(safe-area-inset-top, 0px));" in css
    assert "left: max(12px, env(safe-area-inset-left, 0px));" in css
    assert "viewport-fit=cover" in _read("index.html")
    assert ".workspace-content,\n.workspace-content main {\n  min-width: 0;\n}" in css
    assert "@media (max-width: 760px)" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in css
    assert "@media (min-width: 761px) and (max-width: 980px)" in css
    assert "@media (min-width: 761px) and (max-width: 860px)" in css


def test_workspace_shell_keeps_touch_targets_and_motion_ownership_explicit() -> None:
    """Navigation remains touch-sized while motion ownership stays explicit."""

    css = _read("app.css")

    assert "min-height: var(--sb-touch-target);" in css
    assert ".topnav a::before" in css
    assert "padding: 8px;" in css
    assert "transition: transform var(--sb-duration-press) var(--sb-ease-out);" in css
    assert "--sb-duration-ambient: 24s;" in css
