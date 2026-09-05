"""Reviewed Decision Journal и Outcome Observation boundaries Stage 2."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from second_brain.application.llm import (
    MAX_CONTENT_BYTES,
    MAX_MAX_OUTPUT_BYTES,
    NoteDraft,
    validate_note_draft,
)
from second_brain.application.personal_memory import (
    PersonalMemoryDraftError,
    validate_stage2_metadata,
)
from second_brain.application.ports import LlmError
from second_brain.domain.models import (
    DecisionJournalRecord,
    EvidenceAt,
    EvidenceAtPrecision,
    EvidenceKind,
    OutcomeObservationRecord,
    PersonalMemoryMetadata,
    SelfKind,
)

JOURNAL_REQUIRED_HEADINGS = (
    "Situation",
    "Available options",
    "Information known at decision time",
    "Criteria",
    "Chosen option",
    "Reasons",
    "Confidence",
    "Expected result",
    "Actual result",
    "Reassessment",
)
OUTCOME_REQUIRED_HEADINGS = ("Actual result", "Reassessment", "Notes")
MIN_JOURNAL_OPTIONS = 2
MAX_JOURNAL_OPTIONS = 20
MIN_JOURNAL_CRITERIA = 1
MAX_JOURNAL_CRITERIA = 20
MAX_JOURNAL_BODY_BYTES = MAX_CONTENT_BYTES
MAX_JOURNAL_SECTION_BYTES = 64 * 1024
MAX_JOURNAL_ITEM_BYTES = 16 * 1024

_BULLET_PATTERN = re.compile(r"^[ \t]{0,3}[-*+][ \t]+(?P<item>.*?)\s*$")
_BODY_ERROR_MESSAGES = {
    "body_type": "structured body must be text",
    "body_too_large": "structured body exceeds its byte limit",
    "heading_missing": "structured body is missing a required heading",
    "heading_duplicate": "structured body contains a duplicate heading",
    "heading_order": "structured body headings are not in the required order",
    "heading_unknown": "structured body contains an unsupported heading",
    "body_preamble": "structured body cannot contain content before its headings",
    "section_too_large": "structured body section exceeds its byte limit",
    "section_empty": "required structured body section is empty",
    "list_invalid": "structured body list must contain only non-empty Markdown bullets",
    "list_too_few": "structured body list has too few items",
    "list_too_many": "structured body list has too many items",
    "list_duplicate": "structured body list contains duplicate items",
    "chosen_invalid": "chosen option must be one plain-text value",
    "chosen_missing": "chosen option is not one of the available options",
    "initial_actual": "initial Decision Journal Actual result must be empty",
    "initial_reassessment": "initial Decision Journal Reassessment must be empty",
    "outcome_missing": "Outcome Observation requires Actual result or Reassessment",
    "decision_id_invalid": "Outcome Observation decision_id must be UUIDv7",
}


class StructuredBodyError(ValueError):
    """Безопасная ошибка deterministic structured Markdown parser."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        self.message = _BODY_ERROR_MESSAGES[reason]
        super().__init__(self.message)


class DecisionJournalBodyError(StructuredBodyError):
    """Decision Journal body нарушает exact Stage 2 structure."""

    def __init__(self, reason: str) -> None:
        super().__init__("DECISION_JOURNAL_INVALID_BODY", reason)


class OutcomeObservationBodyError(StructuredBodyError):
    """Outcome Observation body нарушает exact Stage 2 structure."""

    def __init__(self, reason: str) -> None:
        super().__init__("OUTCOME_OBSERVATION_INVALID_BODY", reason)


class DecisionJournalDraftError(ValueError):
    """Reviewed Decision Journal input не прошёл application boundary."""

    def __init__(
        self, code: str = "DECISION_JOURNAL_DRAFT_INVALID", message: str | None = None
    ) -> None:
        self.code = code
        self.message = message or "Decision Journal draft does not satisfy its reviewed contract"
        super().__init__(self.message)


class OutcomeObservationDraftError(ValueError):
    """Reviewed Outcome Observation input не прошёл application boundary."""

    def __init__(
        self, code: str = "OUTCOME_OBSERVATION_DRAFT_INVALID", message: str | None = None
    ) -> None:
        self.code = code
        self.message = message or (
            "Outcome Observation draft does not satisfy its reviewed contract"
        )
        super().__init__(self.message)


@dataclass(frozen=True, slots=True)
class DecisionJournalDraft:
    """Reviewed semantic NoteDraft с application-owned Journal metadata."""

    draft: NoteDraft
    evidence_at: EvidenceAt | str
    evidence_at_precision: EvidenceAtPrecision | str
    domain: str | None = None

    def __post_init__(self) -> None:
        if type(self.draft) is not NoteDraft:
            raise ValueError("draft must be a NoteDraft")

    @property
    def metadata(self) -> PersonalMemoryMetadata:
        """Вернуть fixed ``observed_decision + decision`` metadata."""

        return validate_stage2_metadata(
            evidence_kind=EvidenceKind.OBSERVED_DECISION,
            self_kind=SelfKind.DECISION,
            evidence_at=self.evidence_at,
            evidence_at_precision=self.evidence_at_precision,
            domain=self.domain,
        )


@dataclass(frozen=True, slots=True)
class OutcomeObservationDraft:
    """Reviewed semantic NoteDraft с UUIDv7 relation к Decision Journal."""

    draft: NoteDraft
    decision_id: UUID
    evidence_at: EvidenceAt | str
    evidence_at_precision: EvidenceAtPrecision | str
    domain: str | None = None

    def __post_init__(self) -> None:
        if type(self.draft) is not NoteDraft:
            raise ValueError("draft must be a NoteDraft")

    @property
    def metadata(self) -> PersonalMemoryMetadata:
        """Вернуть fixed ``outcome_later_observation + outcome`` metadata."""

        if type(self.decision_id) is not UUID:
            raise PersonalMemoryDraftError("OUTCOME_DECISION_ID_INVALID")
        return validate_stage2_metadata(
            evidence_kind=EvidenceKind.OUTCOME_LATER_OBSERVATION,
            self_kind=SelfKind.OUTCOME,
            evidence_at=self.evidence_at,
            evidence_at_precision=self.evidence_at_precision,
            domain=self.domain,
            decision_id=self.decision_id,
        )


def parse_decision_journal_body(body: str) -> DecisionJournalRecord:
    """Проверить initial Journal body и вернуть typed pre-choice projection."""

    sections = _parse_sections(
        body,
        JOURNAL_REQUIRED_HEADINGS,
        DecisionJournalBodyError,
    )
    required = (
        "Situation",
        "Information known at decision time",
        "Reasons",
        "Confidence",
        "Expected result",
    )
    for heading in required:
        if not sections[heading].strip():
            raise DecisionJournalBodyError("section_empty")

    available_options = _parse_bullet_list(
        sections["Available options"],
        minimum=MIN_JOURNAL_OPTIONS,
        maximum=MAX_JOURNAL_OPTIONS,
        error_type=DecisionJournalBodyError,
    )
    criteria = _parse_bullet_list(
        sections["Criteria"],
        minimum=MIN_JOURNAL_CRITERIA,
        maximum=MAX_JOURNAL_CRITERIA,
        error_type=DecisionJournalBodyError,
    )
    chosen_option = sections["Chosen option"].strip()
    if not chosen_option or len(chosen_option.splitlines()) != 1:
        raise DecisionJournalBodyError("chosen_invalid")
    if _BULLET_PATTERN.fullmatch(chosen_option) is not None:
        raise DecisionJournalBodyError("chosen_invalid")
    normalized_chosen = _normalize_whitespace(chosen_option)
    if normalized_chosen not in {_normalize_whitespace(item) for item in available_options}:
        raise DecisionJournalBodyError("chosen_missing")
    if sections["Actual result"].strip():
        raise DecisionJournalBodyError("initial_actual")
    if sections["Reassessment"].strip():
        raise DecisionJournalBodyError("initial_reassessment")

    return DecisionJournalRecord(
        situation=sections["Situation"].strip(),
        available_options=available_options,
        information_known_at_decision_time=sections["Information known at decision time"].strip(),
        criteria=criteria,
        chosen_option=chosen_option,
        reasons=sections["Reasons"].strip(),
        confidence=sections["Confidence"].strip(),
        expected_result=sections["Expected result"].strip(),
    )


def parse_outcome_observation_body(
    body: str,
    decision_id: UUID,
) -> OutcomeObservationRecord:
    """Проверить Outcome body и вернуть typed projection с current UUID relation."""

    if type(decision_id) is not UUID or decision_id.version != 7:
        raise OutcomeObservationBodyError("decision_id_invalid")
    sections = _parse_sections(
        body,
        OUTCOME_REQUIRED_HEADINGS,
        OutcomeObservationBodyError,
    )
    actual_result = sections["Actual result"].strip()
    reassessment = sections["Reassessment"].strip()
    if not actual_result and not reassessment:
        raise OutcomeObservationBodyError("outcome_missing")
    return OutcomeObservationRecord(
        decision_id=decision_id,
        actual_result=actual_result,
        reassessment=reassessment,
        notes=sections["Notes"].strip(),
    )


def validate_decision_journal_draft(draft: object) -> DecisionJournalDraft:
    """Проверить NoteDraft, fixed metadata и initial Journal body."""

    if type(draft) is not DecisionJournalDraft:
        raise DecisionJournalDraftError()
    try:
        semantic_draft = validate_note_draft(draft.draft, max_output_bytes=MAX_MAX_OUTPUT_BYTES)
    except LlmError:
        raise DecisionJournalDraftError(
            "DRAFT_SCHEMA_INVALID", "draft does not satisfy the NoteDraft semantic contract"
        ) from None
    try:
        metadata = draft.metadata
    except PersonalMemoryDraftError as exc:
        raise DecisionJournalDraftError(exc.code, exc.message) from None
    try:
        parse_decision_journal_body(semantic_draft.content)
    except DecisionJournalBodyError as exc:
        raise DecisionJournalDraftError(exc.code, exc.message) from None
    return DecisionJournalDraft(
        draft=semantic_draft,
        evidence_at=metadata.evidence_at,
        evidence_at_precision=metadata.evidence_at_precision,
        domain=metadata.domain,
    )


def validate_outcome_observation_draft(draft: object) -> OutcomeObservationDraft:
    """Проверить NoteDraft, fixed metadata, UUIDv7 и Outcome body."""

    if type(draft) is not OutcomeObservationDraft:
        raise OutcomeObservationDraftError()
    try:
        semantic_draft = validate_note_draft(draft.draft, max_output_bytes=MAX_MAX_OUTPUT_BYTES)
    except LlmError:
        raise OutcomeObservationDraftError(
            "DRAFT_SCHEMA_INVALID",
            "draft does not satisfy the NoteDraft semantic contract",
        ) from None
    try:
        metadata = draft.metadata
    except PersonalMemoryDraftError as exc:
        raise OutcomeObservationDraftError(exc.code, exc.message) from None
    assert metadata.decision_id is not None
    try:
        parse_outcome_observation_body(semantic_draft.content, metadata.decision_id)
    except OutcomeObservationBodyError as exc:
        raise OutcomeObservationDraftError(exc.code, exc.message) from None
    return OutcomeObservationDraft(
        draft=semantic_draft,
        decision_id=metadata.decision_id,
        evidence_at=metadata.evidence_at,
        evidence_at_precision=metadata.evidence_at_precision,
        domain=metadata.domain,
    )


def _parse_sections(
    body: str,
    expected: tuple[str, ...],
    error_type: Callable[[str], StructuredBodyError],
) -> dict[str, str]:
    if type(body) is not str:
        raise error_type("body_type")
    try:
        body_bytes = len(body.encode("utf-8"))
    except UnicodeEncodeError:
        raise error_type("body_type") from None
    if body_bytes > MAX_JOURNAL_BODY_BYTES:
        raise error_type("body_too_large")

    lines = body.splitlines(keepends=True)
    expected_headings = {f"## {heading}" for heading in expected}
    indexes: list[int] = []
    found: list[str] = []
    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")
        if not line.startswith("##"):
            continue
        if line not in expected_headings:
            raise error_type("heading_unknown")
        indexes.append(index)
        found.append(line.removeprefix("## "))
    if not indexes:
        raise error_type("heading_missing")
    if any(line.strip() for line in lines[: indexes[0]]):
        raise error_type("body_preamble")
    if len(found) != len(set(found)):
        raise error_type("heading_duplicate")
    if len(found) != len(expected):
        raise error_type("heading_missing")
    if tuple(found) != expected:
        raise error_type("heading_order")

    sections: dict[str, str] = {}
    for position, heading in enumerate(found):
        start = indexes[position] + 1
        end = indexes[position + 1] if position + 1 < len(indexes) else len(lines)
        value = "".join(lines[start:end])
        try:
            value_bytes = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            raise error_type("body_type") from None
        if value_bytes > MAX_JOURNAL_SECTION_BYTES:
            raise error_type("section_too_large")
        sections[heading] = value
    return sections


def _parse_bullet_list(
    value: str,
    *,
    minimum: int,
    maximum: int,
    error_type: Callable[[str], StructuredBodyError],
) -> tuple[str, ...]:
    items: list[str] = []
    for line in value.splitlines():
        if not line.strip():
            continue
        match = _BULLET_PATTERN.fullmatch(line)
        if match is None:
            raise error_type("list_invalid")
        item = match.group("item").strip()
        if not item:
            raise error_type("list_invalid")
        try:
            item_bytes = len(item.encode("utf-8"))
        except UnicodeEncodeError:
            raise error_type("list_invalid") from None
        if item_bytes > MAX_JOURNAL_ITEM_BYTES:
            raise error_type("section_too_large")
        items.append(item)
    if len(items) < minimum:
        raise error_type("list_too_few")
    if len(items) > maximum:
        raise error_type("list_too_many")
    normalized = [_normalize_whitespace(item) for item in items]
    if len(normalized) != len(set(normalized)):
        raise error_type("list_duplicate")
    return tuple(items)


def _normalize_whitespace(value: str) -> str:
    """Сделать минимальную whitespace-нормализацию только для сравнения."""

    return " ".join(value.split())


# Descriptive aliases keep the parser boundary discoverable for callers/tests.
validate_decision_journal_body = parse_decision_journal_body
validate_outcome_observation_body = parse_outcome_observation_body


__all__ = [
    "JOURNAL_REQUIRED_HEADINGS",
    "MAX_JOURNAL_BODY_BYTES",
    "MAX_JOURNAL_CRITERIA",
    "MAX_JOURNAL_ITEM_BYTES",
    "MAX_JOURNAL_OPTIONS",
    "MAX_JOURNAL_SECTION_BYTES",
    "MIN_JOURNAL_CRITERIA",
    "MIN_JOURNAL_OPTIONS",
    "OUTCOME_REQUIRED_HEADINGS",
    "DecisionJournalBodyError",
    "DecisionJournalDraft",
    "DecisionJournalDraftError",
    "OutcomeObservationBodyError",
    "OutcomeObservationDraft",
    "OutcomeObservationDraftError",
    "parse_decision_journal_body",
    "parse_outcome_observation_body",
    "validate_decision_journal_body",
    "validate_decision_journal_draft",
    "validate_outcome_observation_body",
    "validate_outcome_observation_draft",
]
