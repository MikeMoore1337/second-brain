"""Достаточно сохраняющий read-only парсер front matter Markdown."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ruamel.yaml import YAML


@dataclass(frozen=True, slots=True)
class FrontMatterResult:
    """Разобранные front matter и body либо ошибка парсера."""

    data: Mapping[str, Any]
    body: str
    has_front_matter: bool
    error: str | None = None
    error_line: int | None = None
    header: str | None = None


def parse_front_matter(text: str) -> FrontMatterResult:
    """Разобрать начальный YAML front matter из Markdown-текста."""

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return FrontMatterResult({}, text, False)

    closing_index: int | None = None
    for index in range(1, len(lines)):
        line = lines[index].rstrip("\r\n")
        if line.rstrip(" \t") in {"---", "..."}:
            closing_index = index
            break
    if closing_index is None:
        return FrontMatterResult(
            {},
            "",
            True,
            "front matter opening delimiter has no closing delimiter",
            1,
        )

    header = "".join(lines[1:closing_index])
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        raw: object = yaml.load(header)
    except Exception as exc:  # ruamel exposes several parser/constructor exception types
        return FrontMatterResult({}, "".join(lines[closing_index + 1 :]), True, str(exc), 2)
    if raw is None:
        data: Mapping[str, Any] = {}
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        return FrontMatterResult(
            {},
            "".join(lines[closing_index + 1 :]),
            True,
            "front matter must be a mapping",
            2,
        )
    return FrontMatterResult(
        data,
        "".join(lines[closing_index + 1 :]),
        True,
        header=header,
    )
