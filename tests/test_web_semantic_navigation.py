"""Source contracts for the four-direction React information architecture."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_main_page_uses_exactly_the_four_semantic_directions() -> None:
    source = _read("web/src/semantic-navigation.tsx")
    app = _read("web/src/App.tsx")

    for direction in ("memory", "self-understanding", "decisions", "growth"):
        assert f'id: "{direction}"' in source
    assert source.count('id: "memory"') == 1
    assert source.count('id: "self-understanding"') == 1
    assert source.count('id: "decisions"') == 1
    assert source.count('id: "growth"') == 1
    assert "Personal Experiments" not in source
    assert 'id: "personal-experiments"' in source
    assert 'target: "#personal-experiments"' in source
    assert "FoldSection" not in source
    assert "data-semantic-tool-link={tool.id}" in source
    assert "data-semantic-functional-surface={tool.id}" in source
    assert (
        '"active-learning": { '
        'groupId: "decisions", '
        'toolId: "simulate-me", '
        'surfaceId: "simulate-me" }' in source
    )
    assert 'tool.id === "active-learning"' not in source
    assert "pillars" not in app
    assert "memoryBackdrop" not in app
    assert "growthBackdrop" not in app


def test_semantic_navigation_has_mobile_and_reduced_motion_paths() -> None:
    css = _read("web/src/semantic-navigation.css")

    assert "@media(max-width:600px)" in css
    assert ".semantic-tool-links { grid-template-columns:1fr;" in css
    assert (
        ".semantic-navigation,.semantic-group,.semantic-group-panel-inner,.semantic-tool-copy { "
        "min-width:0; }"
    ) in css
    assert "overflow-wrap:anywhere" in css
    assert "@media(prefers-reduced-motion:reduce)" in css
    assert (
        ".semantic-group-panel,.semantic-group-chevron,.semantic-tool-link { transition:none; }"
    ) in css
    assert ".semantic-group-trigger:focus-visible" in css


def test_semantic_navigation_keeps_existing_surface_anchors_and_no_future_stage_link() -> None:
    source = _read("web/src/semantic-navigation.tsx")

    for anchor in (
        "#capture",
        "#search",
        "#decision-journal",
        "#timeline",
        "#self-retrieval",
        "#self-model",
        "#cognitive-twin",
        "#retrospective-calibration",
        "#diagnostics",
        "#simulate-me",
        "#prospective-audit",
        "#assistant-compare",
        "#decision-compass",
        "#growth-engine",
    ):
        assert f'target: "{anchor}"' in source
    assert "Personal Experiments" not in source
    assert 'target: "#personal-experiments"' in source
