"""Проверки чистых domain-инвариантов."""

from datetime import datetime

import pytest

from second_brain.domain.models import NoteType, parse_rfc3339, parse_uuid7


def test_uuid7_is_accepted_and_uuid4_is_rejected() -> None:
    assert parse_uuid7("0198f4c5-6a00-7000-8000-000000000001").version == 7
    with pytest.raises(ValueError, match="version must be 7"):
        parse_uuid7("550e8400-e29b-41d4-a716-446655440000")
    with pytest.raises(ValueError, match="lowercase canonical"):
        parse_uuid7("0198F4C5-6A00-7000-8000-000000000001")


def test_rfc3339_requires_explicit_offset() -> None:
    parsed = parse_rfc3339("2026-09-02T12:00:00+03:00")
    assert parsed == datetime(2026, 9, 2, 12, 0, tzinfo=parsed.tzinfo)
    offset = parse_rfc3339("2026-09-02T09:00:00Z").utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 0
    with pytest.raises(ValueError, match="explicit UTC offset"):
        parse_rfc3339("2026-09-02T12:00:00")
    with pytest.raises(ValueError, match="valid RFC 3339"):
        parse_rfc3339("2026-09-02")


def test_note_type_does_not_include_location_lifecycle() -> None:
    assert NoteType.PROJECT.value == "project"
    assert all(item.value not in {"inbox", "archive"} for item in NoteType)
