"""Reviewed Personal Memory Contract v1 и его bounded validation boundary."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from second_brain.application.llm import NoteDraft, validate_note_draft
from second_brain.application.ports import LlmError
from second_brain.domain.models import (
    EvidenceAt,
    EvidenceAtPrecision,
    EvidenceKind,
    PersonalMemoryMetadata,
    SelfKind,
    parse_rfc3339,
)

PERSONAL_MEMORY_MARKER = "second_brain_personal_memory"
PERSONAL_MEMORY_ENROLLMENT_FIELD = PERSONAL_MEMORY_MARKER
PERSONAL_MEMORY_MARKER_VALUE = 1
PERSONAL_MEMORY_UNKNOWN_TIME: Literal["unknown"] = "unknown"
MAX_PERSONAL_MEMORY_DOMAIN_BYTES = 64

_DOMAIN_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z", re.ASCII)
_REQUIRED_FIELDS = (
    "evidence_kind",
    "self_kind",
    "evidence_at",
    "evidence_at_precision",
)

_PERSONAL_MEMORY_MESSAGES = {
    "PERSONAL_MEMORY_INVALID_RECORD": "Personal Memory metadata must be a mapping",
    "PERSONAL_MEMORY_MISSING_EVIDENCE_KIND": "Personal Memory requires evidence_kind",
    "PERSONAL_MEMORY_INVALID_EVIDENCE_KIND": "Personal Memory evidence_kind is not supported",
    "PERSONAL_MEMORY_MISSING_SELF_KIND": "Personal Memory requires self_kind",
    "PERSONAL_MEMORY_INVALID_SELF_KIND": "Personal Memory self_kind is not supported",
    "PERSONAL_MEMORY_MISSING_EVIDENCE_AT": "Personal Memory requires evidence_at",
    "PERSONAL_MEMORY_INVALID_EVIDENCE_AT": "Personal Memory evidence_at is invalid",
    "PERSONAL_MEMORY_MISSING_EVIDENCE_AT_PRECISION": (
        "Personal Memory requires evidence_at_precision"
    ),
    "PERSONAL_MEMORY_INVALID_EVIDENCE_AT_PRECISION": (
        "Personal Memory evidence_at_precision is not supported"
    ),
    "PERSONAL_MEMORY_INVALID_EVIDENCE_AT_PAIR": (
        "Personal Memory evidence_at and evidence_at_precision must agree"
    ),
    "PERSONAL_MEMORY_INVALID_DOMAIN": "Personal Memory domain must be one lowercase ASCII slug",
    "PERSONAL_MEMORY_DRAFT_INVALID": "Personal Memory draft does not satisfy its reviewed contract",
}


@dataclass(frozen=True, slots=True)
class PersonalMemoryValidationIssue:
    """Безопасный код одной ошибки закрытой Personal Memory metadata policy."""

    code: str


class PersonalMemoryDraftError(ValueError):
    """Bounded ошибка reviewed Personal Memory input без raw metadata details."""

    def __init__(self, code: str = "PERSONAL_MEMORY_DRAFT_INVALID") -> None:
        normalized = code if code in _PERSONAL_MEMORY_MESSAGES else "PERSONAL_MEMORY_DRAFT_INVALID"
        self.code = normalized
        self.message = _PERSONAL_MEMORY_MESSAGES[normalized]
        super().__init__(self.message)


@dataclass(frozen=True, slots=True)
class PersonalMemoryDraft:
    """Reviewed wrapper над semantic ``NoteDraft`` с controlled Stage 1 metadata."""

    draft: NoteDraft
    evidence_kind: EvidenceKind | str
    self_kind: SelfKind | str
    evidence_at: EvidenceAt | str
    evidence_at_precision: EvidenceAtPrecision | str
    domain: str | None = None

    def __post_init__(self) -> None:
        """Сразу ограничить semantic payload существующим exact ``NoteDraft`` type."""

        if type(self.draft) is not NoteDraft:
            raise ValueError("draft must be a NoteDraft")

    @property
    def note_draft(self) -> NoteDraft:
        """Вернуть semantic NoteDraft под описательным alias-именем."""

        return self.draft

    @property
    def metadata(self) -> PersonalMemoryMetadata:
        """Проверить и вернуть normalized typed metadata этой reviewed boundary."""

        metadata, issues = _validate_values(
            evidence_kind=self.evidence_kind,
            self_kind=self.self_kind,
            evidence_at=self.evidence_at,
            evidence_at_precision=self.evidence_at_precision,
            domain=self.domain,
            missing=frozenset(),
        )
        if issues or metadata is None:
            raise PersonalMemoryDraftError(
                issues[0].code if issues else "PERSONAL_MEMORY_DRAFT_INVALID"
            )
        return metadata


def is_personal_memory_enrolled(front_matter: Mapping[str, object] | object) -> bool:
    """Проверить единственный enrollment marker с type-strict Python semantics."""

    if not isinstance(front_matter, Mapping):
        return False
    value = front_matter.get(PERSONAL_MEMORY_MARKER)
    return type(value) is int and value == PERSONAL_MEMORY_MARKER_VALUE


def validate_personal_memory_fields(
    front_matter: Mapping[str, object] | object,
) -> tuple[PersonalMemoryMetadata | None, tuple[PersonalMemoryValidationIssue, ...]]:
    """Проверить только enrolled metadata, не меняя переданный unknown front matter."""

    if not isinstance(front_matter, Mapping):
        return None, (PersonalMemoryValidationIssue("PERSONAL_MEMORY_INVALID_RECORD"),)
    data = front_matter
    missing = frozenset(field for field in _REQUIRED_FIELDS if field not in data)
    return _validate_values(
        evidence_kind=data.get("evidence_kind"),
        self_kind=data.get("self_kind"),
        evidence_at=data.get("evidence_at"),
        evidence_at_precision=data.get("evidence_at_precision"),
        domain=data.get("domain"),
        missing=missing,
    )


def validate_personal_memory_draft(draft: object) -> PersonalMemoryDraft:
    """Проверить reviewed wrapper и вернуть normalized controlled values."""

    if type(draft) is not PersonalMemoryDraft:
        raise PersonalMemoryDraftError()
    try:
        semantic_draft = validate_note_draft(draft.draft)
    except LlmError:
        raise PersonalMemoryDraftError() from None

    metadata, issues = _validate_values(
        evidence_kind=draft.evidence_kind,
        self_kind=draft.self_kind,
        evidence_at=draft.evidence_at,
        evidence_at_precision=draft.evidence_at_precision,
        domain=draft.domain,
        missing=frozenset(),
    )
    if issues or metadata is None:
        raise PersonalMemoryDraftError(
            issues[0].code if issues else "PERSONAL_MEMORY_DRAFT_INVALID"
        )
    return PersonalMemoryDraft(
        draft=semantic_draft,
        evidence_kind=metadata.evidence_kind,
        self_kind=metadata.self_kind,
        evidence_at=metadata.evidence_at,
        evidence_at_precision=metadata.evidence_at_precision,
        domain=metadata.domain,
    )


def personal_memory_diagnostic_message(code: str) -> str:
    """Вернуть фиксированный public-facing diagnostic без raw field values."""

    return _PERSONAL_MEMORY_MESSAGES.get(code, "Personal Memory metadata is invalid")


def _validate_values(
    *,
    evidence_kind: object,
    self_kind: object,
    evidence_at: object,
    evidence_at_precision: object,
    domain: object,
    missing: frozenset[str],
) -> tuple[PersonalMemoryMetadata | None, tuple[PersonalMemoryValidationIssue, ...]]:
    issues: list[PersonalMemoryValidationIssue] = []

    parsed_evidence_kind: EvidenceKind | None = None
    if "evidence_kind" in missing:
        issues.append(PersonalMemoryValidationIssue("PERSONAL_MEMORY_MISSING_EVIDENCE_KIND"))
    else:
        parsed_evidence_kind = _parse_enum(evidence_kind, EvidenceKind)
        if parsed_evidence_kind is None:
            issues.append(PersonalMemoryValidationIssue("PERSONAL_MEMORY_INVALID_EVIDENCE_KIND"))

    parsed_self_kind: SelfKind | None = None
    if "self_kind" in missing:
        issues.append(PersonalMemoryValidationIssue("PERSONAL_MEMORY_MISSING_SELF_KIND"))
    else:
        parsed_self_kind = _parse_enum(self_kind, SelfKind)
        if parsed_self_kind is None:
            issues.append(PersonalMemoryValidationIssue("PERSONAL_MEMORY_INVALID_SELF_KIND"))

    parsed_evidence_at: EvidenceAt | None = None
    if "evidence_at" in missing:
        issues.append(PersonalMemoryValidationIssue("PERSONAL_MEMORY_MISSING_EVIDENCE_AT"))
    else:
        parsed_evidence_at = _parse_evidence_at(evidence_at)
        if parsed_evidence_at is None:
            issues.append(PersonalMemoryValidationIssue("PERSONAL_MEMORY_INVALID_EVIDENCE_AT"))

    parsed_precision: EvidenceAtPrecision | None = None
    if "evidence_at_precision" in missing:
        issues.append(
            PersonalMemoryValidationIssue("PERSONAL_MEMORY_MISSING_EVIDENCE_AT_PRECISION")
        )
    else:
        parsed_precision = _parse_enum(evidence_at_precision, EvidenceAtPrecision)
        if parsed_precision is None:
            issues.append(
                PersonalMemoryValidationIssue("PERSONAL_MEMORY_INVALID_EVIDENCE_AT_PRECISION")
            )

    if (
        parsed_evidence_at is not None
        and parsed_precision is not None
        and not _valid_time_pair(parsed_evidence_at, parsed_precision)
    ):
        issues.append(PersonalMemoryValidationIssue("PERSONAL_MEMORY_INVALID_EVIDENCE_AT_PAIR"))

    parsed_domain = _parse_domain(domain)
    if isinstance(parsed_domain, _InvalidDomain):
        issues.append(PersonalMemoryValidationIssue("PERSONAL_MEMORY_INVALID_DOMAIN"))
        parsed_domain = None

    if issues:
        return None, tuple(issues)
    assert parsed_evidence_kind is not None
    assert parsed_self_kind is not None
    assert parsed_evidence_at is not None
    assert parsed_precision is not None
    return (
        PersonalMemoryMetadata(
            evidence_kind=parsed_evidence_kind,
            self_kind=parsed_self_kind,
            evidence_at=parsed_evidence_at,
            evidence_at_precision=parsed_precision,
            domain=parsed_domain,
        ),
        (),
    )


class _InvalidDomain:
    """Внутренний sentinel для различения invalid domain и optional None."""


_INVALID_DOMAIN = _InvalidDomain()


def _parse_enum[EnumValue: StrEnum](value: object, enum_type: type[EnumValue]) -> EnumValue | None:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        return None
    try:
        return enum_type(value)
    except ValueError:
        return None


def _parse_evidence_at(value: object) -> EvidenceAt | None:
    if type(value) is str:
        if value == PERSONAL_MEMORY_UNKNOWN_TIME:
            return PERSONAL_MEMORY_UNKNOWN_TIME
        try:
            return parse_rfc3339(value)
        except TypeError, ValueError, OverflowError:
            return None
    if isinstance(value, datetime):
        try:
            return parse_rfc3339(value)
        except TypeError, ValueError, OverflowError:
            return None
    return None


def _parse_domain(value: object) -> str | _InvalidDomain | None:
    if value is None:
        return None
    if type(value) is not str or not value:
        return _INVALID_DOMAIN
    try:
        if len(value.encode("utf-8")) > MAX_PERSONAL_MEMORY_DOMAIN_BYTES:
            return _INVALID_DOMAIN
    except UnicodeEncodeError:
        return _INVALID_DOMAIN
    if not value.isascii() or _DOMAIN_PATTERN.fullmatch(value) is None:
        return _INVALID_DOMAIN
    return value


def _valid_time_pair(evidence_at: EvidenceAt, precision: EvidenceAtPrecision) -> bool:
    if evidence_at == PERSONAL_MEMORY_UNKNOWN_TIME:
        return precision is EvidenceAtPrecision.UNKNOWN
    return precision is EvidenceAtPrecision.EXACT


__all__ = [
    "MAX_PERSONAL_MEMORY_DOMAIN_BYTES",
    "PERSONAL_MEMORY_ENROLLMENT_FIELD",
    "PERSONAL_MEMORY_MARKER",
    "PERSONAL_MEMORY_MARKER_VALUE",
    "PERSONAL_MEMORY_UNKNOWN_TIME",
    "EvidenceAt",
    "EvidenceAtPrecision",
    "EvidenceKind",
    "PersonalMemoryDraft",
    "PersonalMemoryDraftError",
    "PersonalMemoryMetadata",
    "PersonalMemoryValidationIssue",
    "SelfKind",
    "is_personal_memory_enrolled",
    "personal_memory_diagnostic_message",
    "validate_personal_memory_draft",
    "validate_personal_memory_fields",
]
