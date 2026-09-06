import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_react_production_icon_affordances_are_local_svg() -> None:
    app = (ROOT / "web/src/App.tsx").read_text(encoding="utf-8")
    parity = (ROOT / "web/src/parity.tsx").read_text(encoding="utf-8")
    icons = (ROOT / "web/src/icons.tsx").read_text(encoding="utf-8")

    assert 'from "./icons"' in app
    assert 'from "./icons"' in parity
    assert 'stroke="currentColor"' in icons
    assert "fetch(" not in icons
    assert "@lucide" not in icons
    assert ">↗<" not in app
    assert not re.search(r'className="entry-icon"[^>]*>\s*\+\s*</div>', parity)
