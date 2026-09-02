"""Проверки минимального Obsidian wikilink scanner."""

from second_brain.adapters.vault.markdown_links import extract_links


def test_basic_wikilink_forms_and_fragments_are_extracted() -> None:
    links = extract_links(
        """[[Note]] [[Note|Текст]] [[Note#Heading]] [[Note^block-id]] """
        """![[image.png]] [[#Local]] [[^block]]""",
        "10 Projects/Source.md",
    )

    assert [item.target for item in links] == ["Note", "Note", "Note", "Note", "image.png", "", ""]
    assert [item.fragment_kind for item in links] == [None, None, "#", "^", None, "#", "^"]
    assert links[4].is_embed is True


def test_code_and_front_matter_are_not_scanned_by_markdown_parser() -> None:
    links = extract_links(
        """---
link: [[FrontMatter]]
---

`[[InlineCode]]`

```text
[[FencedCode]]
```

[[Real]]
""",
        "Note.md",
    )

    assert [item.target for item in links] == ["Real"]
