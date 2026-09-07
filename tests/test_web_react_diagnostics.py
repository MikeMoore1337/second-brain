"""Static contracts for the production React Diagnostics surface."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    """Read one production React source file as UTF-8."""

    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_diagnostics_surface_is_russian_safe_and_explicit_refresh_only() -> None:
    source = _read("web/src/diagnostics-surface.tsx")
    app = _read("web/src/App.tsx")
    api = _read("web/src/api.ts")

    assert "Диагностика" in app
    assert "Обновить" in source
    assert "Нажми «Обновить»" in source
    assert '"/api/diagnostics"' in api
    assert '"diagnostics-v1"' in api
    assert "loadDiagnostics()" in source
    assert "setInterval" not in source
    assert "setTimeout" not in source
    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert "indexedDB" not in source
    assert "repair" not in source.casefold()
    assert "fix" not in source.casefold()


def test_diagnostics_surface_has_accessible_statuses_codes_and_local_icons() -> None:
    source = _read("web/src/diagnostics-surface.tsx")
    app = _read("web/src/App.tsx")

    assert "data-diagnostics-surface" in source
    assert 'aria-live="polite"' in source
    assert 'role="alert"' in source
    assert 'name="refresh"' in source
    assert "name={copy.icon}" in source
    assert "data-diagnostics-status={report.status}" in source
    assert 'className="diagnostics-code"' in source
    assert '["#diagnostics", "Диагностика", "diagnostics"]' in app


def test_diagnostics_surface_covers_mobile_overflow_and_reduced_motion_contract() -> None:
    styles = _read("web/src/styles.css")

    assert ".diagnostics-surface" in styles
    assert ".diagnostics-code" in styles
    assert "overflow-wrap: anywhere" in styles
    assert "@media (max-width: 760px)" in styles
    assert ".diagnostics-facts {\n    grid-template-columns: 1fr;" in styles
    assert "@media (max-width: 1024px)" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles
