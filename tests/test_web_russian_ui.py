"""Regression coverage for the production React Russian-language contract."""

from __future__ import annotations

import re
from pathlib import Path

from second_brain.entrypoints.web.app import _ERRORS, _GENERIC_ERROR

ROOT = Path(__file__).resolve().parents[1]
WEB_SOURCE_ROOT = ROOT / "web" / "src"

_QUOTED_STRING = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
_JSX_TEXT = re.compile(r"<[A-Za-z][^<>]*>([^<>{}\r\n]+)</[A-Za-z][^<>]*>")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_LATIN_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?![A-Za-z0-9])")
_ALLOWED_UI_LATIN_TOKENS = frozenset(
    {
        "API",
        "Brain",
        "FastAPI",
        "GitHub",
        "HTTP",
        "ID",
        "JSON",
        "Markdown",
        "MiB",
        "MikeMoore1337",
        "PWA",
        "React",
        "RFC3339",
        "Second",
        "URL",
        "UUID",
        "UUIDv7",
        "YAML",
    }
)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _production_web_sources() -> dict[str, str]:
    paths = sorted(WEB_SOURCE_ROOT.glob("*.tsx")) + sorted(WEB_SOURCE_ROOT.glob("*.ts"))
    return {str(path.relative_to(ROOT)): path.read_text(encoding="utf-8") for path in paths}


def _ui_copy_candidates(path: str, source: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for line_number, line in enumerate(source.splitlines(), 1):
        for match in _QUOTED_STRING.finditer(line):
            value = match.group()[1:-1]
            if (
                "<" not in value
                and ">" not in value
                and _CYRILLIC.search(value)
                and _LATIN_TOKEN.search(value)
            ):
                candidates.append((f"{path}:{line_number}", value))
        if path.endswith(".tsx"):
            for match in _JSX_TEXT.finditer(line):
                value = match.group(1).strip()
                if _LATIN_TOKEN.search(value):
                    candidates.append((f"{path}:{line_number}", value))
    return candidates


def test_production_react_surfaces_use_russian_user_copy() -> None:
    sources = "\n".join([_read("web/index.html"), *_production_web_sources().values()])
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
        "идентификатор GitHub",
        "Cognitive Twin",
        "Growth Learning / Question",
        "server-owned",
        "no-store",
        "foreground-only",
        "Personal Memory",
        "Safe Write",
        "Goal preview",
        "Growth result",
        "exact-срез",
        "exact-вариант",
        "Review текущего",
        "Lifecycle mapping store",
        "Diff до записи",
    )
    assert not [copy for copy in forbidden_user_copy if copy in sources]

    unexpected = [
        f"{location}: {value}"
        for path, source in _production_web_sources().items()
        for location, value in _ui_copy_candidates(path, source)
        for token in _LATIN_TOKEN.findall(value)
        if token not in _ALLOWED_UI_LATIN_TOKENS
    ]
    assert not unexpected


def test_github_login_uses_the_canonical_technical_label() -> None:
    login = _read("web/src/login-screen.tsx")
    assert "по вашему GitHub ID" in login
    assert "идентификатор GitHub" not in login


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
    assert "body::before" in styles
    assert ".brand" in styles
    assert ".skip-link" in styles


def test_production_ui_does_not_show_development_stage_labels() -> None:
    for path in WEB_SOURCE_ROOT.rglob("*.tsx"):
        if "test" in path.relative_to(WEB_SOURCE_ROOT).parts:
            continue
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"(?:Этап|этап|Stage|stage)\s+\d+", source), path
