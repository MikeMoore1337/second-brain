"""Regression contracts for the React atmospheric layer in #116."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _styles() -> str:
    return (ROOT / "web/src/styles.css").read_text(encoding="utf-8")


def test_react_atmosphere_is_bounded_and_noninteractive() -> None:
    styles = _styles()

    assert ".hero::before," in styles
    assert ".hero::after" in styles
    assert "overflow: clip" in styles
    assert "pointer-events: none" in styles
    assert "var(--sb-gradient-aurora)" in styles
    assert "var(--sb-gradient-bloom)" in styles
    assert "var(--sb-gradient-streak)" in styles
    assert "filter:" not in styles
    assert "canvas" not in styles.lower()


def test_react_atmosphere_has_mobile_and_reduced_motion_fallbacks() -> None:
    styles = _styles()

    assert "@media (prefers-reduced-motion: reduce)" in styles
    reduced_motion = styles.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".hero::before" in reduced_motion
    assert ".hero::after" in reduced_motion
    assert "transform: none" in reduced_motion

    assert "@media (max-width: 760px)" in styles
    mobile = styles.split("@media (max-width: 760px)", 1)[1]
    assert ".hero::before" in mobile
    assert ".hero::after" in mobile
    assert "opacity: 0.2" in mobile
