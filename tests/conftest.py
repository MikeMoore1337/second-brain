"""Общие builders для изолированных fixture vault."""

from __future__ import annotations

from pathlib import Path

VALID_VAULT_ID = "0198f4c5-6a00-7000-8000-000000000001"
VALID_NOTE_ID = "0198f4c5-6a00-7000-8000-000000000002"
SECOND_NOTE_ID = "0198f4c5-6a00-7000-8000-000000000003"

VAULT_DIRECTORIES = (
    "00 Inbox",
    "10 Projects",
    "20 Areas",
    "30 Resources",
    "40 Zettelkasten",
    "90 Archive",
    "_templates",
    "_attachments",
)


def create_vault(root: Path) -> Path:
    """Создать минимальный vault contract v1 в каталоге теста."""

    root.mkdir(parents=True, exist_ok=True)
    for directory in VAULT_DIRECTORIES:
        (root / directory).mkdir()
    (root / "second-brain.yaml").write_text(
        f"""schema_version: 1
vault_id: {VALID_VAULT_ID}
default_language: ru
paths:
  inbox: 00 Inbox
  projects: 10 Projects
  areas: 20 Areas
  resources: 30 Resources
  zettelkasten: 40 Zettelkasten
  archive: 90 Archive
  templates: _templates
  attachments: _attachments
attachments:
  warning_size_bytes: 10485760
  max_size_bytes: 52428800
""",
        encoding="utf-8",
    )
    return root


def write_note(root: Path, relative_path: str, text: str) -> Path:
    """Записать одну тестовую Markdown-запись."""

    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def managed_note(note_id: str = VALID_NOTE_ID, note_type: str = "zettel") -> str:
    """Вернуть минимальную валидную managed note."""

    return f"""---
id: {note_id}
type: {note_type}
created: 2026-09-02T12:00:00+03:00
tags: []
---
# Тестовая заметка

Текст заметки.
"""


def snapshot_tree(root: Path) -> dict[str, bytes]:
    """Снять байтовый snapshot всех файлов fixture vault."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
