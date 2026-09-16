"""Source checks for the React icon component and its local raster boundary.

This module does not load a browser or prove that every icon is visible at
runtime; ``web/qa/iconography.mjs`` covers that browser contract.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_react_production_icon_affordances_are_local_raster() -> None:
    app = (ROOT / "web/src/App.tsx").read_text(encoding="utf-8")
    parity = (ROOT / "web/src/parity.tsx").read_text(encoding="utf-8")
    parity_surfaces = (ROOT / "web/src/parity-surfaces.tsx").read_text(encoding="utf-8")
    production_surfaces = parity + parity_surfaces
    icons = (ROOT / "web/src/icons.tsx").read_text(encoding="utf-8")

    assert 'from "./icons"' in app
    assert 'from "./icons"' in production_surfaces
    assert "<img" in icons
    assert ".webp" in icons
    assert 'loading="lazy"' in icons
    assert "<svg" not in icons
    assert "fetch(" not in icons
    assert "@lucide" not in icons
    assert ">↗<" not in app
    assert not re.search(r'className="entry-icon"[^>]*>\s*\+\s*</div>', production_surfaces)


def test_different_sections_have_unique_icons() -> None:
    app = (ROOT / "web/src/App.tsx").read_text(encoding="utf-8")
    semantic = (ROOT / "web/src/semantic-navigation.tsx").read_text(encoding="utf-8")
    tool_pattern = (
        r'\{ id: "([^"]+)", label: "[^"]+", description: "[^"]+", '
        r'target: "#[^"]+", icon: "([^"]+)", availability: "available", '
        r'renderSurface: (?:true|false)(?:, foldId: "[^"]+")? \}'
    )
    sections = re.findall(
        tool_pattern,
        semantic,
    )
    assert len(sections) == 18
    names = [name for _, name in sections]
    assert len(names) == len(set(names)), sections
    assert ("decision-compass", "decision-compass") in sections
    assert ("personal-experiments", "personal-experiments") in sections
    assert 'id: "decision-compass", label: "Компас решения"' in semantic
    assert 'id="semantic-navigation"' in semantic
    assert "pillars" not in app
    assert "memoryBackdrop" not in app
    assert "growthBackdrop" not in app
