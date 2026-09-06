"""Regression coverage for the production React Russian-language contract."""

from __future__ import annotations

import re
from pathlib import Path

from second_brain.entrypoints.web.app import _ERRORS, _GENERIC_ERROR

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_production_react_surfaces_use_russian_user_copy() -> None:
    sources = "\n".join(
        _read(path)
        for path in (
            "web/index.html",
            "web/src/App.tsx",
            "web/src/parity.tsx",
            "web/src/api.ts",
        )
    )
    assert '<html lang="ru">' in _read("web/index.html")
    forbidden_user_copy = (
        "Personal knowledge system",
        "Workspace map",
        "Signal field",
        "Local foundation",
        "Private knowledge, deliberate growth",
        "Decision Journal.",
        "Personal Timeline.",
        "Self Model.",
        "Simulate Me.",
        "Private retrieval",
        "Stage 2 · deliberate capture",
        "Stage 3 · current evidence",
        "Stage 4 · derived, current, explainable",
        "Stage 5 · current context",
        "Stage 6 · mechanical, current, bounded",
        "Safe Write dry-run",
        "Добавить outcome",
        "example.com/article",
        "Не удалось распознать audio.",
    )
    assert not [copy for copy in forbidden_user_copy if copy in sources]


def test_web_error_messages_are_safe_russian_human_messages() -> None:
    messages = [message for _, message in _ERRORS.values()]
    messages.append(_GENERIC_ERROR[2])
    assert messages
    assert all(re.search(r"[А-Яа-яЁё]", message) for message in messages)
    assert all("traceback" not in message.lower() for message in messages)
    assert all("vault" not in message.lower() for message in messages)


def test_localized_presentation_keeps_machine_values_out_of_visible_labels() -> None:
    presentation = _read("web/src/presentation.ts")
    assert 'project: "Проект"' in presentation
    assert 'note: "Заметка"' in presentation
    assert 'not_assessed: "Не оценивалось"' in presentation
    assert 'no_matching_evidence: "Подходящих свидетельств не найдено"' in presentation
    assert "insufficient_or_invalid_current_context:" in presentation
    assert "Недостаточно или некорректен актуальный контекст" in presentation


def test_russian_ui_mobile_contract_retains_existing_touch_and_width_floor() -> None:
    styles = _read("web/src/styles.css")
    production_styles = _read("src/second_brain/entrypoints/web/static/app.css")
    assert "min-width: 320px" in styles
    assert "min-width: 44px" in styles
    assert "overflow-x: clip" in production_styles
    assert "@media (max-width: 760px)" in production_styles
    assert "width: min(100%, calc(100vw - 32px))" in production_styles
    assert "@media (max-width: 1024px)" in styles
    assert "font-size: 16px" in styles
    assert ".brand" in styles
    assert ".skip-link" in styles
    assert "body::before" in styles
