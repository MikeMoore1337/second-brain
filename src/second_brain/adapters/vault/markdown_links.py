"""Небольшой явный scanner Obsidian wikilinks для read-only MVP."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt

from second_brain.adapters.vault.frontmatter import parse_front_matter
from second_brain.domain.models import LinkReference

_WIKILINK = re.compile(r"(?P<embed>!)?\[\[(?P<inner>[^\]\r\n]+)\]\]")
_MARKDOWN = MarkdownIt("commonmark")


def extract_links(markdown: str, source_path: str) -> tuple[LinkReference, ...]:
    """Извлечь базовые wikilinks из Markdown, пропуская code spans/blocks."""

    links: list[LinkReference] = []
    body = parse_front_matter(markdown).body
    for token in _MARKDOWN.parse(body):
        if token.type != "inline" or token.children is None:
            continue
        for child in token.children:
            if child.type != "text":
                continue
            for match in _WIKILINK.finditer(child.content):
                is_embed = bool(match.group("embed"))
                raw = match.group(0)
                inner = match.group("inner").strip()
                target_with_fragment = inner.split("|", 1)[0].strip()
                target, fragment, fragment_kind = _split_fragment(target_with_fragment)
                links.append(
                    LinkReference(
                        source_path=source_path,
                        raw=raw,
                        target=target,
                        fragment=fragment,
                        fragment_kind=fragment_kind,
                        is_embed=is_embed,
                    )
                )
    return tuple(links)


def _split_fragment(value: str) -> tuple[str, str | None, str | None]:
    hash_index = value.find("#")
    caret_index = value.find("^")
    indexes = [index for index in (hash_index, caret_index) if index >= 0]
    if not indexes:
        return value, None, None
    index = min(indexes)
    marker = value[index]
    return value[:index].strip(), value[index + 1 :].strip() or None, marker
