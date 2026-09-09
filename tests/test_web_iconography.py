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
