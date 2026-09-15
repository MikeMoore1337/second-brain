"""Application validation и cross-record diagnostics для read-only vault scan."""

from __future__ import annotations

import posixpath
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any, cast
from uuid import UUID

from second_brain.application.decision_journal import (
    DecisionJournalBodyError,
    OutcomeObservationBodyError,
    parse_decision_journal_body,
    parse_outcome_observation_body,
)
from second_brain.application.goal_progress import (
    GOAL_PROGRESS_DIAGNOSTIC_MESSAGES,
    DefinitionRecordV1,
    GoalProgressRecordError,
    ObservationRecordV1,
    parse_goal_progress_record,
    validate_definition_chain,
    validate_observation_against_definition,
    validate_observation_chain,
)
from second_brain.application.personal_experiments import (
    MAX_PERSONAL_EXPERIMENT_RECORDS,
    PERSONAL_EXPERIMENT_DIAGNOSTIC_MESSAGES,
    PersonalExperimentDefinitionRecordV1,
    PersonalExperimentLifecycleRecordV1,
    PersonalExperimentObservationRecordV1,
    PersonalExperimentReassessmentRecordV1,
    PersonalExperimentRecordError,
    parse_personal_experiment_record,
    validate_personal_experiment_definition_chain,
    validate_personal_experiment_lifecycle_chain,
    validate_personal_experiment_observation_binding,
    validate_personal_experiment_observation_chain,
    validate_personal_experiment_reassessment_chain,
)
from second_brain.application.personal_memory import (
    personal_memory_diagnostic_message,
    validate_canonical_personal_memory_fields,
)
from second_brain.application.reports import (
    Diagnostic,
    DiagnosticSeverity,
    ScanReport,
    VaultSnapshot,
)
from second_brain.application.research import SourceKind, SourceProvenance
from second_brain.domain.models import (
    AttachmentRecord,
    DecisionJournalRecord,
    LinkReference,
    MarkdownDocument,
    NoteRecord,
    NoteType,
    OutcomeObservationRecord,
    PersonalMemoryMetadata,
    VaultManifest,
    parse_rfc3339,
    parse_uuid7,
)

_MANAGED_FIELDS = frozenset(("id", "type", "created"))


def build_report(snapshot: VaultSnapshot) -> ScanReport:
    """Собрать итоговый report из raw DTO и выполнить application validation."""

    diagnostics = list(snapshot.diagnostics)
    notes = [_validate_document(document, diagnostics) for document in snapshot.documents]
    _report_attachment_diagnostics(snapshot.attachments, snapshot.manifest, diagnostics)
    _report_duplicate_ids(notes, diagnostics)
    _report_stage2_relations(notes, diagnostics)
    _report_goal_progress_relations(notes, diagnostics)
    _report_personal_experiment_relations(notes, diagnostics)
    _report_link_diagnostics(notes, snapshot.attachments, snapshot.links, diagnostics)
    return ScanReport(
        vault_path=snapshot.vault_path,
        manifest=snapshot.manifest,
        notes=tuple(notes),
        links=snapshot.links,
        attachments=snapshot.attachments,
        diagnostics=tuple(diagnostics),
    )


def _validate_document(
    document: MarkdownDocument,
    diagnostics: list[Diagnostic],
) -> NoteRecord:
    data = dict(document.front_matter)
    _validate_sources(data, document.relative_path, diagnostics)
    marker_present = bool(_MANAGED_FIELDS.intersection(data))
    managed = not document.in_inbox or marker_present
    if not managed:
        diagnostics.append(
            Diagnostic(
                "UNMANAGED_INBOX_NOTE",
                "Inbox Markdown has no id, type, or created field and is temporarily unmanaged",
                DiagnosticSeverity.WARNING,
                document.relative_path,
            )
        )
        return NoteRecord(document.relative_path, data, document.body, False)

    note_id = _parse_note_id(data, document.relative_path, diagnostics)
    note_type = _parse_note_type(data, document.relative_path, diagnostics)
    created = _parse_note_timestamp(
        data, "created", document.relative_path, diagnostics, required=True
    )
    updated = _parse_note_timestamp(
        data, "updated", document.relative_path, diagnostics, required=False
    )
    tags = _parse_tags(data, document.relative_path, diagnostics)
    personal_memory, decision_journal, outcome_observation = _parse_personal_memory(
        data,
        document.body,
        document.relative_path,
        diagnostics,
    )
    goal_progress_definition, goal_progress_observation = _parse_goal_progress(
        data,
        note_id,
        document.relative_path,
        diagnostics,
    )
    (
        personal_experiment_definition,
        personal_experiment_lifecycle,
        personal_experiment_observation,
        personal_experiment_reassessment,
    ) = _parse_personal_experiment(data, note_id, document.relative_path, diagnostics)
    return NoteRecord(
        relative_path=document.relative_path,
        front_matter=data,
        body=document.body,
        managed=True,
        note_id=note_id,
        note_type=note_type,
        created=created,
        updated=updated,
        tags=tags,
        personal_memory=personal_memory,
        decision_journal=decision_journal,
        outcome_observation=outcome_observation,
        goal_progress_definition=goal_progress_definition,
        goal_progress_observation=goal_progress_observation,
        personal_experiment_definition=personal_experiment_definition,
        personal_experiment_lifecycle=personal_experiment_lifecycle,
        personal_experiment_observation=personal_experiment_observation,
        personal_experiment_reassessment=personal_experiment_reassessment,
    )


def _parse_personal_memory(
    data: Mapping[str, Any],
    body: str,
    path: str,
    diagnostics: list[Diagnostic],
) -> tuple[
    PersonalMemoryMetadata | None,
    DecisionJournalRecord | None,
    OutcomeObservationRecord | None,
]:
    """Запустить marker-gated canonical Stage 1/Stage 2 validator."""

    metadata, issues = validate_canonical_personal_memory_fields(data)
    for issue in issues:
        diagnostics.append(
            Diagnostic(
                issue.code,
                personal_memory_diagnostic_message(issue.code),
                DiagnosticSeverity.ERROR,
                path,
            )
        )
    if metadata is None or issues:
        return metadata, None, None
    decision_journal: DecisionJournalRecord | None = None
    outcome_observation: OutcomeObservationRecord | None = None
    if metadata.evidence_kind.value == "observed_decision":
        try:
            decision_journal = parse_decision_journal_body(body)
        except DecisionJournalBodyError:
            diagnostics.append(
                Diagnostic(
                    "DECISION_JOURNAL_INVALID_BODY",
                    "Decision Journal body does not satisfy the deterministic Stage 2 contract",
                    DiagnosticSeverity.ERROR,
                    path,
                )
            )
    elif (
        metadata.evidence_kind.value == "outcome_later_observation"
        and metadata.decision_id is not None
    ):
        try:
            outcome_observation = parse_outcome_observation_body(body, metadata.decision_id)
        except OutcomeObservationBodyError:
            diagnostics.append(
                Diagnostic(
                    "OUTCOME_OBSERVATION_INVALID_BODY",
                    "Outcome Observation body does not satisfy the deterministic Stage 2 contract",
                    DiagnosticSeverity.ERROR,
                    path,
                )
            )
    return metadata, decision_journal, outcome_observation


def _parse_goal_progress(
    data: Mapping[str, Any],
    note_id: UUID | None,
    path: str,
    diagnostics: list[Diagnostic],
) -> tuple[DefinitionRecordV1 | None, ObservationRecordV1 | None]:
    """Parse only exact marker-enrolled Stage 12A companion records."""

    try:
        record = parse_goal_progress_record(data, note_id=note_id)
    except GoalProgressRecordError as exc:
        diagnostics.append(
            Diagnostic(
                exc.code,
                GOAL_PROGRESS_DIAGNOSTIC_MESSAGES.get(
                    exc.code,
                    GOAL_PROGRESS_DIAGNOSTIC_MESSAGES["GOAL_PROGRESS_INVALID_RECORD"],
                ),
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        return None, None
    except TypeError, ValueError, UnicodeError:
        diagnostics.append(
            Diagnostic(
                "GOAL_PROGRESS_INVALID_RECORD",
                GOAL_PROGRESS_DIAGNOSTIC_MESSAGES["GOAL_PROGRESS_INVALID_RECORD"],
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        return None, None
    if type(record) is DefinitionRecordV1:
        return record, None
    if type(record) is ObservationRecordV1:
        return None, record
    return None, None


def _parse_personal_experiment(
    data: Mapping[str, Any],
    note_id: UUID | None,
    path: str,
    diagnostics: list[Diagnostic],
) -> tuple[
    PersonalExperimentDefinitionRecordV1 | None,
    PersonalExperimentLifecycleRecordV1 | None,
    PersonalExperimentObservationRecordV1 | None,
    PersonalExperimentReassessmentRecordV1 | None,
]:
    """Parse only exact marker-enrolled Stage 14 companion records."""

    try:
        record = parse_personal_experiment_record(data, note_id=note_id)
    except PersonalExperimentRecordError as exc:
        diagnostics.append(
            Diagnostic(
                exc.code,
                PERSONAL_EXPERIMENT_DIAGNOSTIC_MESSAGES.get(
                    exc.code,
                    PERSONAL_EXPERIMENT_DIAGNOSTIC_MESSAGES["PERSONAL_EXPERIMENT_INVALID_RECORD"],
                ),
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        return None, None, None, None
    except TypeError, ValueError, UnicodeError, OverflowError:
        diagnostics.append(
            Diagnostic(
                "PERSONAL_EXPERIMENT_INVALID_RECORD",
                PERSONAL_EXPERIMENT_DIAGNOSTIC_MESSAGES["PERSONAL_EXPERIMENT_INVALID_RECORD"],
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        return None, None, None, None
    if type(record) is PersonalExperimentDefinitionRecordV1:
        return record, None, None, None
    if type(record) is PersonalExperimentLifecycleRecordV1:
        return None, record, None, None
    if type(record) is PersonalExperimentObservationRecordV1:
        return None, None, record, None
    if type(record) is PersonalExperimentReassessmentRecordV1:
        return None, None, None, record
    return None, None, None, None


def _report_personal_experiment_relations(
    notes: Iterable[NoteRecord],
    diagnostics: list[Diagnostic],
) -> None:
    """Validate Stage 14 companion chains and exact Stage 12 source links."""

    current_notes = tuple(notes)
    paths_by_id: dict[UUID, str] = {}
    for note in sorted(current_notes, key=lambda item: item.relative_path):
        if note.note_id is not None:
            paths_by_id.setdefault(note.note_id, note.relative_path)

    definitions = tuple(
        sorted(
            (
                note.personal_experiment_definition
                for note in current_notes
                if note.personal_experiment_definition is not None
            ),
            key=lambda record: str(record.id),
        )
    )
    lifecycles = tuple(
        sorted(
            (
                note.personal_experiment_lifecycle
                for note in current_notes
                if note.personal_experiment_lifecycle is not None
            ),
            key=lambda record: str(record.id),
        )
    )
    observations = tuple(
        sorted(
            (
                note.personal_experiment_observation
                for note in current_notes
                if note.personal_experiment_observation is not None
            ),
            key=lambda record: str(record.id),
        )
    )
    reassessments = tuple(
        sorted(
            (
                note.personal_experiment_reassessment
                for note in current_notes
                if note.personal_experiment_reassessment is not None
            ),
            key=lambda record: str(record.id),
        )
    )
    all_records = (*definitions, *lifecycles, *observations, *reassessments)
    if len(all_records) > MAX_PERSONAL_EXPERIMENT_RECORDS:
        _append_personal_experiment_diagnostic(
            diagnostics,
            "PERSONAL_EXPERIMENT_RECORD_LIMIT_EXCEEDED",
            None,
        )

    stage12_definitions_by_id: defaultdict[UUID, list[DefinitionRecordV1]] = defaultdict(list)
    stage12_observations: list[ObservationRecordV1] = []
    for note in current_notes:
        if note.goal_progress_definition is not None:
            stage12_definitions_by_id[cast(UUID, note.goal_progress_definition.id)].append(
                note.goal_progress_definition
            )
        if note.goal_progress_observation is not None:
            stage12_observations.append(note.goal_progress_observation)

    for definition in definitions:
        stage12_matches = stage12_definitions_by_id.get(
            cast(UUID, definition.goal_progress_definition_id), []
        )
        path = paths_by_id.get(cast(UUID, definition.id))
        if not stage12_matches:
            _append_personal_experiment_diagnostic(
                diagnostics,
                "PERSONAL_EXPERIMENT_SOURCE_MISSING",
                path,
            )
        elif len(stage12_matches) > 1:
            _append_personal_experiment_diagnostic(
                diagnostics,
                "PERSONAL_EXPERIMENT_BINDING_MISMATCH",
                path,
            )
        else:
            stage12_definition = stage12_matches[0]
            if (
                stage12_definition.definition_fingerprint
                != definition.goal_progress_definition_fingerprint
                or stage12_definition.goal_source_uuid != definition.goal_source_uuid
                or stage12_definition.goal_identity_fingerprint
                != definition.goal_identity_fingerprint
                or stage12_definition.goal_progress_policy_fingerprint
                != definition.goal_progress_policy_fingerprint
            ):
                _append_personal_experiment_diagnostic(
                    diagnostics,
                    "PERSONAL_EXPERIMENT_SOURCE_CHANGED",
                    path,
                )

    definitions_by_id: defaultdict[UUID, list[PersonalExperimentDefinitionRecordV1]] = defaultdict(
        list
    )
    for definition in definitions:
        definitions_by_id[cast(UUID, definition.id)].append(definition)

    def check_definition_target(
        record_id: UUID | str,
        definition_id: UUID | str,
        definition_fingerprint: str,
    ) -> None:
        definition_matches = definitions_by_id.get(cast(UUID, definition_id), [])
        path = paths_by_id.get(cast(UUID, record_id))
        if not definition_matches:
            _append_personal_experiment_diagnostic(
                diagnostics,
                "PERSONAL_EXPERIMENT_DEFINITION_MISSING",
                path,
            )
        elif len(definition_matches) > 1:
            _append_personal_experiment_diagnostic(
                diagnostics,
                "PERSONAL_EXPERIMENT_DEFINITION_AMBIGUOUS",
                path,
            )
        elif definition_matches[0].experiment_definition_fingerprint != definition_fingerprint:
            _append_personal_experiment_diagnostic(
                diagnostics,
                "PERSONAL_EXPERIMENT_DEFINITION_CHANGED",
                path,
            )

    for lifecycle in lifecycles:
        check_definition_target(
            lifecycle.id,
            lifecycle.experiment_definition_id,
            lifecycle.experiment_definition_fingerprint,
        )
    for observation in observations:
        check_definition_target(
            observation.id,
            observation.experiment_definition_id,
            observation.experiment_definition_fingerprint,
        )
    for reassessment in reassessments:
        check_definition_target(
            reassessment.id,
            reassessment.experiment_definition_id,
            reassessment.experiment_definition_fingerprint,
        )

    for observation in observations:
        source = validate_personal_experiment_observation_binding(
            observation,
            stage12_observations=stage12_observations,
        )
        for issue in source.issues:
            _append_personal_experiment_diagnostic(
                diagnostics,
                issue,
                paths_by_id.get(cast(UUID, observation.id)),
            )

    definition_groups: defaultdict[
        tuple[UUID, str, UUID, str], list[PersonalExperimentDefinitionRecordV1]
    ] = defaultdict(list)
    for definition in definitions:
        definition_groups[
            (
                cast(UUID, definition.goal_source_uuid),
                definition.goal_identity_fingerprint,
                cast(UUID, definition.goal_progress_definition_id),
                definition.goal_progress_definition_fingerprint,
            )
        ].append(definition)
    for key in sorted(definition_groups, key=lambda item: tuple(map(str, item))):
        result = validate_personal_experiment_definition_chain(definition_groups[key])
        for issue in result.issues:
            _append_personal_experiment_diagnostic(
                diagnostics,
                issue,
                paths_by_id.get(cast(UUID, definition_groups[key][0].id)),
            )

    lifecycle_groups: defaultdict[tuple[UUID, str], list[PersonalExperimentLifecycleRecordV1]] = (
        defaultdict(list)
    )
    for lifecycle in lifecycles:
        lifecycle_groups[
            (
                cast(UUID, lifecycle.experiment_definition_id),
                lifecycle.experiment_definition_fingerprint,
            )
        ].append(lifecycle)
    for lifecycle_key in sorted(lifecycle_groups, key=lambda item: tuple(map(str, item))):
        lifecycle_result = validate_personal_experiment_lifecycle_chain(
            lifecycle_groups[lifecycle_key]
        )
        for issue in lifecycle_result.issues:
            _append_personal_experiment_diagnostic(
                diagnostics,
                issue,
                paths_by_id.get(cast(UUID, lifecycle_groups[lifecycle_key][0].id)),
            )

    observation_groups: defaultdict[
        tuple[UUID, str], list[PersonalExperimentObservationRecordV1]
    ] = defaultdict(list)
    for observation in observations:
        observation_groups[
            (
                cast(UUID, observation.experiment_definition_id),
                observation.experiment_definition_fingerprint,
            )
        ].append(observation)
    for observation_key in sorted(observation_groups, key=lambda item: tuple(map(str, item))):
        observation_result = validate_personal_experiment_observation_chain(
            observation_groups[observation_key]
        )
        for issue in observation_result.issues:
            _append_personal_experiment_diagnostic(
                diagnostics,
                issue,
                paths_by_id.get(cast(UUID, observation_groups[observation_key][0].id)),
            )

    reassessment_groups: defaultdict[
        tuple[UUID, str], list[PersonalExperimentReassessmentRecordV1]
    ] = defaultdict(list)
    for reassessment in reassessments:
        reassessment_groups[
            (
                cast(UUID, reassessment.experiment_definition_id),
                reassessment.experiment_definition_fingerprint,
            )
        ].append(reassessment)
    for reassessment_key in sorted(reassessment_groups, key=lambda item: tuple(map(str, item))):
        reassessment_result = validate_personal_experiment_reassessment_chain(
            reassessment_groups[reassessment_key]
        )
        for issue in reassessment_result.issues:
            _append_personal_experiment_diagnostic(
                diagnostics,
                issue,
                paths_by_id.get(cast(UUID, reassessment_groups[reassessment_key][0].id)),
            )


def _parse_note_id(data: dict[str, Any], path: str, diagnostics: list[Diagnostic]) -> UUID | None:
    if "id" not in data:
        diagnostics.append(
            Diagnostic(
                "NOTE_MISSING_ID", "managed note requires id", DiagnosticSeverity.ERROR, path
            )
        )
        return None
    try:
        return parse_uuid7(data["id"])
    except ValueError as exc:
        diagnostics.append(Diagnostic("NOTE_INVALID_ID", str(exc), DiagnosticSeverity.ERROR, path))
        return None


def _parse_note_type(
    data: dict[str, Any], path: str, diagnostics: list[Diagnostic]
) -> NoteType | None:
    if "type" not in data:
        diagnostics.append(
            Diagnostic(
                "NOTE_MISSING_TYPE", "managed note requires type", DiagnosticSeverity.ERROR, path
            )
        )
        return None
    value = data["type"]
    try:
        return NoteType(value)
    except ValueError:
        allowed = ", ".join(item.value for item in NoteType)
        diagnostics.append(
            Diagnostic(
                "NOTE_INVALID_TYPE",
                f"type must be one of: {allowed}",
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        return None


def _parse_note_timestamp(
    data: dict[str, Any],
    field: str,
    path: str,
    diagnostics: list[Diagnostic],
    required: bool,
) -> Any:
    if field not in data:
        if required:
            diagnostics.append(
                Diagnostic(
                    "NOTE_MISSING_TIMESTAMP",
                    f"managed note requires {field}",
                    DiagnosticSeverity.ERROR,
                    path,
                )
            )
        return None
    try:
        return parse_rfc3339(data[field])
    except ValueError as exc:
        diagnostics.append(
            Diagnostic("NOTE_INVALID_TIMESTAMP", f"{field}: {exc}", DiagnosticSeverity.ERROR, path)
        )
        return None


def _parse_tags(data: dict[str, Any], path: str, diagnostics: list[Diagnostic]) -> tuple[str, ...]:
    if "tags" not in data:
        return ()
    value = data["tags"]
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        diagnostics.append(
            Diagnostic(
                "NOTE_INVALID_TAGS",
                "tags must be a list of non-empty strings",
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        return ()
    return tuple(value)


def _validate_sources(
    data: Mapping[str, Any],
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    """Проверить optional persisted v1 provenance без network или write capability."""

    if "sources" not in data:
        return
    raw_sources = data["sources"]
    if not isinstance(raw_sources, list):
        diagnostics.append(
            Diagnostic(
                "NOTE_INVALID_SOURCES",
                "sources must be a list containing exactly one source record",
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        return
    if len(raw_sources) != 1:
        diagnostics.append(
            Diagnostic(
                "NOTE_INVALID_SOURCE_COUNT",
                "sources must contain exactly one source record in v1",
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        return
    raw_source = raw_sources[0]
    if not isinstance(raw_source, Mapping):
        diagnostics.append(
            Diagnostic(
                "NOTE_INVALID_SOURCE_RECORD",
                "sources[0] must be a mapping",
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        return
    source = cast(Mapping[object, object], raw_source)
    for field, code in (
        ("uri", "NOTE_SOURCE_MISSING_URI"),
        ("kind", "NOTE_SOURCE_MISSING_KIND"),
        ("retrieved_at", "NOTE_SOURCE_MISSING_RETRIEVED_AT"),
    ):
        if field not in source:
            diagnostics.append(
                Diagnostic(
                    code,
                    f"sources[0] requires {field}",
                    DiagnosticSeverity.ERROR,
                    path,
                )
            )
    if any(field not in source for field in ("uri", "kind", "retrieved_at")):
        return
    _validate_source_record(source, path, diagnostics)


def _validate_source_record(
    source: Mapping[object, object],
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    """Разобрать persisted mapping через общую SourceProvenance policy."""

    try:
        source_kind = SourceKind(cast(str, source["kind"]))
    except TypeError, ValueError:
        diagnostics.append(
            Diagnostic(
                "NOTE_SOURCE_INVALID_KIND",
                "sources[0].kind must be one of: web, rss, youtube, github",
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        source_kind = None

    retrieved_at = None
    try:
        retrieved_at = parse_rfc3339(source["retrieved_at"])
    except (TypeError, ValueError, OverflowError) as exc:
        diagnostics.append(
            Diagnostic(
                "NOTE_SOURCE_INVALID_RETRIEVED_AT",
                f"sources[0].retrieved_at: {exc}",
                DiagnosticSeverity.ERROR,
                path,
            )
        )

    published_at = None
    published_at_valid = True
    if "published_at" in source and source["published_at"] is not None:
        try:
            published_at = parse_rfc3339(source["published_at"])
        except (TypeError, ValueError, OverflowError) as exc:
            diagnostics.append(
                Diagnostic(
                    "NOTE_SOURCE_INVALID_PUBLISHED_AT",
                    f"sources[0].published_at: {exc}",
                    DiagnosticSeverity.ERROR,
                    path,
                )
            )
            published_at_valid = False

    if source_kind is None or retrieved_at is None or not published_at_valid:
        return

    try:
        SourceProvenance(
            uri=cast(str, source["uri"]),
            source_kind=source_kind,
            retrieved_at=retrieved_at,
            published_at=published_at,
            title=cast(str | None, source.get("title")),
            author=cast(str | None, source.get("author")),
            upstream_id=cast(str | None, source.get("upstream_id")),
        )
    except ValueError as exc:
        message = str(exc)
        if message.startswith("uri "):
            code = "NOTE_SOURCE_INVALID_URI"
        elif any(message.startswith(f"{field} ") for field in ("title", "author", "upstream_id")):
            code = "NOTE_SOURCE_INVALID_METADATA"
        else:
            code = "NOTE_INVALID_SOURCE"
        diagnostics.append(Diagnostic(code, message, DiagnosticSeverity.ERROR, path))


def _report_attachment_diagnostics(
    attachments: Iterable[AttachmentRecord],
    manifest: VaultManifest | None,
    diagnostics: list[Diagnostic],
) -> None:
    if manifest is None:
        return
    warning_size = manifest.attachments.warning_size_bytes
    max_size = manifest.attachments.max_size_bytes
    for attachment in attachments:
        if attachment.size_bytes > max_size:
            diagnostics.append(
                Diagnostic(
                    "ATTACHMENT_TOO_LARGE",
                    f"attachment is larger than the {max_size} byte limit",
                    DiagnosticSeverity.ERROR,
                    attachment.relative_path,
                )
            )
        elif attachment.size_bytes >= warning_size:
            diagnostics.append(
                Diagnostic(
                    "ATTACHMENT_LARGE",
                    f"attachment is at or above the {warning_size} byte warning threshold",
                    DiagnosticSeverity.WARNING,
                    attachment.relative_path,
                )
            )


def _report_duplicate_ids(notes: Iterable[NoteRecord], diagnostics: list[Diagnostic]) -> None:
    paths_by_id: defaultdict[UUID, list[str]] = defaultdict(list)
    for note in notes:
        if note.note_id is not None:
            paths_by_id[note.note_id].append(note.relative_path)
    for note_id, paths in sorted(paths_by_id.items(), key=lambda item: str(item[0])):
        if len(paths) > 1:
            diagnostics.append(
                Diagnostic(
                    "DUPLICATE_NOTE_ID",
                    f"note id {note_id} is used by: {', '.join(sorted(paths))}",
                    DiagnosticSeverity.ERROR,
                )
            )


def _report_stage2_relations(
    notes: Iterable[NoteRecord],
    diagnostics: list[Diagnostic],
) -> None:
    """Проверить Outcome relation по current canonical scan, не по Search index."""

    current_notes = tuple(notes)
    for outcome_note in current_notes:
        outcome = outcome_note.outcome_observation
        if outcome is None:
            continue
        matches = [note for note in current_notes if note.note_id == outcome.decision_id]
        if not matches:
            diagnostics.append(
                Diagnostic(
                    "OUTCOME_DECISION_NOT_FOUND",
                    "Outcome Observation decision target was not found in the current vault",
                    DiagnosticSeverity.ERROR,
                    outcome_note.relative_path,
                )
            )
            continue
        if len(matches) > 1:
            diagnostics.append(
                Diagnostic(
                    "OUTCOME_DECISION_IDENTITY_CONFLICT",
                    "Outcome Observation decision target has conflicting canonical identities",
                    DiagnosticSeverity.ERROR,
                    outcome_note.relative_path,
                )
            )
            continue
        target = matches[0]
        metadata = target.personal_memory
        if (
            not target.managed
            or metadata is None
            or metadata.evidence_kind.value != "observed_decision"
            or metadata.self_kind.value != "decision"
            or target.decision_journal is None
        ):
            diagnostics.append(
                Diagnostic(
                    "OUTCOME_DECISION_TARGET_INVALID",
                    "Outcome Observation decision target is not a valid Decision Journal",
                    DiagnosticSeverity.ERROR,
                    outcome_note.relative_path,
                )
            )


def _report_goal_progress_relations(
    notes: Iterable[NoteRecord],
    diagnostics: list[Diagnostic],
) -> None:
    """Validate exact Stage 12A cross-record bindings and replacement chains."""

    current_notes = tuple(notes)
    definitions = tuple(
        record
        for _, record in sorted(
            (
                (note.relative_path, note.goal_progress_definition)
                for note in current_notes
                if note.goal_progress_definition is not None
            ),
            key=lambda item: (str(item[1].id), item[0]),
        )
    )
    observations = tuple(
        record
        for _, record in sorted(
            (
                (note.relative_path, note.goal_progress_observation)
                for note in current_notes
                if note.goal_progress_observation is not None
            ),
            key=lambda item: (str(item[1].id), item[0]),
        )
    )
    paths_by_id: dict[UUID, str] = {}
    for note in sorted(current_notes, key=lambda item: item.relative_path):
        if note.note_id is not None:
            paths_by_id.setdefault(note.note_id, note.relative_path)

    definitions_by_id: dict[UUID, list[DefinitionRecordV1]] = defaultdict(list)
    for definition in definitions:
        definitions_by_id[cast(UUID, definition.id)].append(definition)

    for observation in observations:
        observation_definition_id = cast(UUID, observation.progress_definition_id)
        matches = definitions_by_id.get(observation_definition_id, [])
        if len(matches) != 1:
            _append_goal_progress_diagnostic(
                diagnostics,
                "GOAL_PROGRESS_DEFINITION_NOT_FOUND",
                paths_by_id.get(cast(UUID, observation.id)),
            )
            continue
        for issue in validate_observation_against_definition(observation, matches[0]):
            _append_goal_progress_diagnostic(
                diagnostics,
                issue,
                paths_by_id.get(cast(UUID, observation.id)),
            )

    definition_groups: defaultdict[tuple[UUID, str], list[DefinitionRecordV1]] = defaultdict(list)
    for definition in definitions:
        definition_groups[
            (
                cast(UUID, definition.goal_source_uuid),
                definition.goal_identity_fingerprint,
            )
        ].append(definition)
    for definition_group_key in sorted(
        definition_groups,
        key=lambda item: (str(item[0]), item[1]),
    ):
        definition_group = definition_groups[definition_group_key]
        result = validate_definition_chain(definition_group)
        if result.issues:
            for issue in result.issues:
                _append_goal_progress_diagnostic(
                    diagnostics,
                    issue,
                    paths_by_id.get(cast(UUID, definition_group[0].id)),
                )
        elif result.state.value == "multiple_active":
            _append_goal_progress_diagnostic(
                diagnostics,
                "GOAL_PROGRESS_DEFINITION_CONFLICT",
                paths_by_id.get(cast(UUID, definition_group[0].id)),
            )

    observation_groups: defaultdict[tuple[UUID, str, UUID], list[ObservationRecordV1]] = (
        defaultdict(list)
    )
    for observation in observations:
        observation_groups[
            (
                cast(UUID, observation.goal_source_uuid),
                observation.goal_identity_fingerprint,
                cast(UUID, observation.progress_definition_id),
            )
        ].append(observation)
    for observation_group_key in sorted(
        observation_groups,
        key=lambda item: (str(item[0]), item[1], str(item[2])),
    ):
        observation_group = observation_groups[observation_group_key]
        result = validate_observation_chain(observation_group)
        for issue in result.issues:
            _append_goal_progress_diagnostic(
                diagnostics,
                issue,
                paths_by_id.get(cast(UUID, observation_group[0].id)),
            )


def _append_personal_experiment_diagnostic(
    diagnostics: list[Diagnostic],
    code: str,
    path: str | None,
) -> None:
    """Append one fixed Stage 14 diagnostic without exposing record payloads."""

    diagnostics.append(
        Diagnostic(
            code,
            PERSONAL_EXPERIMENT_DIAGNOSTIC_MESSAGES.get(
                code,
                PERSONAL_EXPERIMENT_DIAGNOSTIC_MESSAGES["PERSONAL_EXPERIMENT_INVALID_RECORD"],
            ),
            DiagnosticSeverity.ERROR,
            path,
        )
    )


def _append_goal_progress_diagnostic(
    diagnostics: list[Diagnostic],
    code: str,
    path: str | None,
) -> None:
    """Append one fixed Stage 12 diagnostic without exposing record payloads."""

    if code.startswith("GOAL_PROGRESS_CHAIN_"):
        message = GOAL_PROGRESS_DIAGNOSTIC_MESSAGES.get(
            code,
            GOAL_PROGRESS_DIAGNOSTIC_MESSAGES["GOAL_PROGRESS_OBSERVATION_INVALID"],
        )
    else:
        message = GOAL_PROGRESS_DIAGNOSTIC_MESSAGES.get(
            code,
            GOAL_PROGRESS_DIAGNOSTIC_MESSAGES["GOAL_PROGRESS_INVALID_RECORD"],
        )
    severity = DiagnosticSeverity.ERROR
    diagnostics.append(Diagnostic(code, message, severity, path))


def _report_link_diagnostics(
    notes: Iterable[NoteRecord],
    attachments: Iterable[AttachmentRecord],
    links: Iterable[LinkReference],
    diagnostics: list[Diagnostic],
) -> None:
    note_paths: defaultdict[str, list[str]] = defaultdict(list)
    attachment_paths: defaultdict[str, list[str]] = defaultdict(list)
    for note in notes:
        _add_note_keys(note.relative_path, note_paths)
    for attachment in attachments:
        _add_attachment_keys(attachment.relative_path, attachment_paths)
    for link in links:
        if not link.target and link.fragment is not None:
            continue
        if not link.target:
            diagnostics.append(
                Diagnostic(
                    "EMPTY_WIKILINK_TARGET",
                    f"wikilink has no note target: {link.raw}",
                    DiagnosticSeverity.ERROR,
                    link.source_path,
                )
            )
            continue
        if "://" in link.target:
            continue
        matches = _resolve_link(link, note_paths, attachment_paths)
        if len(matches) == 0:
            diagnostics.append(
                Diagnostic(
                    "BROKEN_WIKILINK",
                    f"wikilink target does not resolve: {link.raw}",
                    DiagnosticSeverity.ERROR,
                    link.source_path,
                )
            )
        elif len(matches) > 1:
            diagnostics.append(
                Diagnostic(
                    "AMBIGUOUS_WIKILINK",
                    f"wikilink target resolves to multiple files: {link.raw}",
                    DiagnosticSeverity.ERROR,
                    link.source_path,
                )
            )


def _add_note_keys(path: str, index: defaultdict[str, list[str]]) -> None:
    normalized = _key(path)
    index[normalized].append(path)
    if normalized.endswith(".md"):
        index[normalized[:-3]].append(path)
    basename = PurePosixPath(normalized).name
    index[basename].append(path)
    if basename.endswith(".md"):
        index[basename[:-3]].append(path)


def _add_attachment_keys(path: str, index: defaultdict[str, list[str]]) -> None:
    normalized = _key(path)
    index[normalized].append(path)
    index[PurePosixPath(normalized).name].append(path)


def _resolve_link(
    link: LinkReference,
    note_paths: defaultdict[str, list[str]],
    attachment_paths: defaultdict[str, list[str]],
) -> list[str]:
    target = link.target.replace("\\", "/").strip().lstrip("/")
    if not target:
        return []
    note_candidates = _link_candidates(target, link.source_path)
    for candidate in note_candidates:
        matches = _unique(note_paths.get(_key(candidate), []))
        if matches:
            return matches
    if link.is_embed or PurePosixPath(target).suffix:
        for candidate in note_candidates:
            matches = _unique(attachment_paths.get(_key(candidate), []))
            if matches:
                return matches
    fallback = _unique(note_paths.get(_key(target), []))
    if fallback:
        return fallback
    if link.is_embed:
        return _unique(attachment_paths.get(_key(target), []))
    return []


def _link_candidates(target: str, source_path: str) -> tuple[str, ...]:
    variants = (target,) if target.casefold().endswith(".md") else (target, f"{target}.md")
    values: list[str] = []
    source_parent = PurePosixPath(source_path).parent
    for variant in variants:
        values.append(variant)
        relative = posixpath.normpath(str(source_parent / variant))
        if relative != ".." and not relative.startswith("../"):
            values.append(relative)
    return tuple(dict.fromkeys(values))


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _key(value: str) -> str:
    normalized = posixpath.normpath(value.replace("\\", "/"))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    return normalized.casefold()
