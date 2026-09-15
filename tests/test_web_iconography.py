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
    sections = re.findall(r'<FoldSection\s+id="([^"]+)"[^>]*icon="([^"]+)"', app)
    assert len(sections) > 5
    names = [name for _, name in sections]
    assert len(names) == len(set(names)), sections
    assert ("decision-compass", "decision-compass") in sections
    assert '["#decision-compass", "Компас решения", "decision-compass"]' in app
