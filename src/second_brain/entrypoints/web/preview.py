"""Fail-closed Markdown rendering for untrusted Web draft content."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from markdown_it import MarkdownIt
from markdown_it.token import Token

from second_brain.application.llm import MAX_CONTENT_BYTES

_ALLOWED_TEXT_CONTROLS = frozenset({"\t", "\n", "\r"})
_MARKDOWN = MarkdownIt(
    "commonmark",
    {
        "html": False,
        "linkify": False,
        "typographer": False,
    },
)


class PreviewInputError(ValueError):
    """Safe validation failure for an oversized or malformed preview body."""


class PreviewContentTooLargeError(PreviewInputError):
    """The Markdown body exceeds the existing application content limit."""


class PreviewRenderError(ValueError):
    """Safe fail-closed renderer failure without parser details."""


def render_safe_markdown(content: str) -> str:
    """Render bounded Markdown with no active links, HTML, images, or code attrs."""

    _validate_content(content)
    try:
        rendered = _MARKDOWN.render(content)
    except RecursionError, TypeError, ValueError:
        raise PreviewRenderError() from None
    if type(rendered) is not str:
        raise PreviewRenderError()
    if _contains_active_markup(rendered):
        raise PreviewRenderError()
    return rendered


def _validate_content(content: object) -> None:
    """Apply the existing content byte policy before invoking Markdown parsing."""

    if type(content) is not str:
        raise PreviewInputError()
    if len(content) > MAX_CONTENT_BYTES:
        raise PreviewContentTooLargeError()
    if _contains_forbidden_control(content):
        raise PreviewInputError()
    try:
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise PreviewContentTooLargeError()
    except UnicodeEncodeError:
        raise PreviewInputError() from None


def _contains_forbidden_control(value: str) -> bool:
    """Reject control characters that can confuse logs, DOM, or parser boundaries."""

    for char in value:
        codepoint = ord(char)
        if codepoint < 32 and char not in _ALLOWED_TEXT_CONTROLS:
            return True
        if 0x7F <= codepoint <= 0x9F or char in {"\u2028", "\u2029"}:
            return True
    return False


def _contains_active_markup(rendered: str) -> bool:
    """Keep a final invariant against accidental renderer rule expansion."""

    lowered = rendered.casefold()
    return any(
        marker in lowered
        for marker in (
            "<a",
            "<img",
            "<iframe",
            "<form",
            "<style",
            "<script",
            "<svg",
            "<object",
            "<embed",
        )
    )


def _render_link_open(
    renderer: object,
    tokens: Sequence[Token],
    index: int,
    options: object,
    env: object,
) -> str:
    """Render a Markdown link label as inert fixed markup."""

    del renderer, tokens, index, options, env
    return '<span class="preview-link">'


def _render_link_close(
    renderer: object,
    tokens: Sequence[Token],
    index: int,
    options: object,
    env: object,
) -> str:
    """Close the inert link-label span."""

    del renderer, tokens, index, options, env
    return "</span>"


def _render_image(
    renderer: object,
    tokens: Sequence[Token],
    index: int,
    options: object,
    env: object,
) -> str:
    """Replace images with a text placeholder and discard every URL."""

    del renderer, options, env
    alt = "".join(child.content for child in (tokens[index].children or ()))
    label = f"Изображение: {alt}" if alt else "Изображение"
    return f'<span class="preview-image">[{escape(label)}]</span>'


def _render_html(
    renderer: object,
    tokens: Sequence[Token],
    index: int,
    options: object,
    env: object,
) -> str:
    """Escape raw HTML even if a future parser preset emits an HTML token."""

    del renderer, options, env
    return escape(tokens[index].content, quote=False)


def _render_code(
    renderer: object,
    tokens: Sequence[Token],
    index: int,
    options: object,
    env: object,
) -> str:
    """Render code without trusting the fence info string as an HTML attribute."""

    del renderer, options, env
    return f"<pre><code>{escape(tokens[index].content, quote=False)}</code></pre>\n"


_MARKDOWN.add_render_rule("link_open", _render_link_open)
_MARKDOWN.add_render_rule("link_close", _render_link_close)
_MARKDOWN.add_render_rule("image", _render_image)
_MARKDOWN.add_render_rule("html_inline", _render_html)
_MARKDOWN.add_render_rule("html_block", _render_html)
_MARKDOWN.add_render_rule("code_block", _render_code)
_MARKDOWN.add_render_rule("fence", _render_code)


__all__ = [
    "PreviewContentTooLargeError",
    "PreviewInputError",
    "PreviewRenderError",
    "render_safe_markdown",
]
