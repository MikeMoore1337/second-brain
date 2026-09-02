"""Регрессионные проверки YAML front matter."""

from second_brain.adapters.vault.frontmatter import parse_front_matter


def test_indented_block_scalar_delimiter_is_not_a_closing_marker() -> None:
    text = (
        "---\r\n"
        "id: 0198f4c5-6a00-7000-8000-000000000002\r\n"
        "type: note\r\n"
        "created: 2026-09-02T12:00:00+03:00\r\n"
        "description: |\r\n"
        "  line 1\r\n"
        "  ---\r\n"
        "  line 2\r\n"
        "---\r\n"
        "[[Visible]]\r\n"
    )

    result = parse_front_matter(text)

    assert result.error is None
    assert result.data["description"] == "line 1\n---\nline 2\n"
    assert result.body == "[[Visible]]\r\n"
