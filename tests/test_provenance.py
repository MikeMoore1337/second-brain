"""Детерминированные проверки provider-neutral source provenance."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest

from second_brain.application.research import (
    MAX_PROVENANCE_FIELD_BYTES,
    MAX_SOURCE_URI_BYTES,
    ResearchSource,
    SourceKind,
    SourceProvenance,
)


def source_uri(source_kind: SourceKind) -> str:
    """Вернуть public URI, соответствующий каждому v1 source kind."""

    return {
        SourceKind.WEB: "https://example.com/article",
        SourceKind.RSS: "https://feeds.example.org/feed.xml",
        SourceKind.YOUTUBE: "https://www.youtube.com/watch?v=public-video",
        SourceKind.GITHUB: "https://github.com/MikeMoore1337/second-brain",
    }[source_kind]


def make_provenance(source_kind: SourceKind = SourceKind.WEB) -> SourceProvenance:
    """Собрать валидный provenance с Unicode metadata."""

    return SourceProvenance(
        uri=source_uri(source_kind),
        source_kind=source_kind,
        retrieved_at=datetime(2026, 9, 4, 20, 0, tzinfo=timezone(timedelta(hours=3))),
        published_at=datetime(2026, 9, 3, 10, 0, tzinfo=UTC),
        title="Заголовок источника",
        author="Автор на русском",
        upstream_id="upstream-123",
    )


@pytest.mark.parametrize("source_kind", list(SourceKind))
def test_all_v1_source_kinds_have_the_same_provider_neutral_shape(
    source_kind: SourceKind,
) -> None:
    provenance = make_provenance(source_kind)

    assert provenance.source_kind is source_kind
    assert provenance.uri == source_uri(source_kind)
    assert provenance.retrieved_at.utcoffset() is not None
    assert provenance.published_at is not None
    assert provenance.title == "Заголовок источника"
    assert provenance.author == "Автор на русском"
    assert provenance.upstream_id == "upstream-123"
    assert tuple(field.name for field in fields(SourceProvenance)) == (
        "uri",
        "source_kind",
        "retrieved_at",
        "published_at",
        "title",
        "author",
        "upstream_id",
    )
    assert not hasattr(provenance, "content")
    assert not hasattr(provenance, "backend")
    assert not hasattr(provenance, "media_type")


def test_provenance_is_extracted_only_from_research_source_metadata() -> None:
    source = ResearchSource(
        uri=source_uri(SourceKind.WEB),
        source_kind=SourceKind.WEB,
        retrieved_at=datetime(2026, 9, 4, 20, 0, tzinfo=UTC),
        backend="provider-detail-must-not-persist",
        content="raw source content must not persist",
        title="title",
        author="author",
        media_type="text/markdown",
        upstream_id="id",
        published_at=datetime(2026, 9, 3, 10, 0, tzinfo=UTC),
    )

    provenance = SourceProvenance.from_research_source(source)

    assert provenance == SourceProvenance(
        uri=source.uri,
        source_kind=source.source_kind,
        retrieved_at=source.retrieved_at,
        published_at=source.published_at,
        title=source.title,
        author=source.author,
        upstream_id=source.upstream_id,
    )
    assert not hasattr(provenance, "content")
    assert not hasattr(provenance, "backend")
    assert not hasattr(provenance, "media_type")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"uri": ""},
        {"uri": "relative/path"},
        {"uri": "http://localhost/article"},
        {"source_kind": cast(SourceKind, "web")},
        {"retrieved_at": datetime(2026, 9, 4, 20, 0)},
        {"published_at": datetime(2026, 9, 3, 10, 0)},
        {"title": "bad\nheader"},
        {"author": "bad\x00author"},
        {"upstream_id": "bad\ridentifier"},
        {"title": ["not", "a", "scalar"]},
    ],
)
def test_invalid_required_or_scalar_metadata_is_rejected(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "uri": source_uri(SourceKind.WEB),
        "source_kind": SourceKind.WEB,
        "retrieved_at": datetime(2026, 9, 4, 20, 0, tzinfo=UTC),
        "published_at": None,
        "title": None,
        "author": None,
        "upstream_id": None,
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        SourceProvenance(**values)  # type: ignore[arg-type]


def test_oversized_uri_and_metadata_are_rejected_by_utf8_byte_bounds() -> None:
    with pytest.raises(ValueError):
        SourceProvenance(
            uri="https://example.com/" + "x" * MAX_SOURCE_URI_BYTES,
            source_kind=SourceKind.WEB,
            retrieved_at=datetime(2026, 9, 4, 20, 0, tzinfo=UTC),
        )

    with pytest.raises(ValueError):
        SourceProvenance(
            uri=source_uri(SourceKind.WEB),
            source_kind=SourceKind.WEB,
            retrieved_at=datetime(2026, 9, 4, 20, 0, tzinfo=UTC),
            title="я" * MAX_PROVENANCE_FIELD_BYTES,
        )


def test_optional_provenance_fields_preserve_unicode_without_normalization() -> None:
    provenance = SourceProvenance(
        uri=source_uri(SourceKind.WEB),
        source_kind=SourceKind.WEB,
        retrieved_at=datetime(2026, 9, 4, 20, 0, tzinfo=UTC),
        title="  Заголовок — без потери Unicode  ",
        author="Автор / редактор",
        upstream_id="идентификатор-42",
    )

    assert provenance.title == "  Заголовок — без потери Unicode  "
    assert provenance.author == "Автор / редактор"
    assert provenance.upstream_id == "идентификатор-42"
