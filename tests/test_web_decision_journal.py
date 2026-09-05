"""Focused deterministic tests for the structured Web Stage 2 surface."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from second_brain.adapters.vault.frontmatter import parse_front_matter
from second_brain.application.decision_journal import (
    DecisionJournalDraft,
    OutcomeObservationDraft,
    render_decision_journal_body,
    render_outcome_observation_body,
)
from second_brain.application.llm import NoteDraft
from second_brain.application.writes import CreateManagedNoteResult, CreateNotePlan, CreateStatus
from second_brain.domain.models import (
    EvidenceAtPrecision,
    EvidenceKind,
    NoteType,
    PersonalMemoryMetadata,
    SelfKind,
    parse_uuid7,
)
from second_brain.entrypoints.web.app import (
    DRAFT_REQUEST_HEADER_NAME,
    DRAFT_REQUEST_HEADER_VALUE,
    MAX_RAW_DRAFT_BODY_BYTES,
    DecisionJournalPayload,
    OutcomeObservationPayload,
    create_app,
)
from second_brain.entrypoints.web.review import (
    DECISION_JOURNAL_CONFIRMATION_PURPOSE,
    OUTCOME_OBSERVATION_CONFIRMATION_PURPOSE,
    ReviewTokenCodec,
    ReviewTokenError,
    ReviewTokenMode,
)
from second_brain.entrypoints.web.saves import DraftSaveService
from tests.conftest import create_vault, snapshot_tree
from tests.test_web_drafts import send_raw_asgi_request

LOOPBACK_BASE_URL = "http://127.0.0.1"
DRAFT_HEADERS = {DRAFT_REQUEST_HEADER_NAME: DRAFT_REQUEST_HEADER_VALUE}
DECISION_ID = parse_uuid7("0198f4c5-6a00-7000-8000-000000000002")
FIXED_TIME = "2026-09-05T15:30:00Z"


def decision_payload(**overrides: object) -> dict[str, object]:
    """Return one valid structured Decision payload."""

    payload: dict[str, object] = {
        "title": "Выбор проекта",
        "note_type": "project",
        "tags": ["decision"],
        "links": ["[[Context]]"],
        "evidence_at": FIXED_TIME,
        "evidence_at_precision": "exact",
        "domain": "work",
        "situation": "Нужно выбрать следующий проект.",
        "available_options": ["Сделать A", "Сделать B"],
        "information_known_at_decision_time": "Известны сроки и ограничения.",
        "criteria": ["Риск", "Ценность"],
        "chosen_option": "Сделать A",
        "reasons": "A лучше соответствует ограничениям.",
        "confidence": "Средняя.",
        "expected_result": "Получится проверяемый результат.",
    }
    payload.update(overrides)
    return payload


def outcome_payload(decision_id: str = str(DECISION_ID), **overrides: object) -> dict[str, object]:
    """Return one valid structured Outcome payload."""

    payload: dict[str, object] = {
        "title": "Результат выбора",
        "note_type": "project",
        "tags": ["outcome"],
        "links": [],
        "decision_id": decision_id,
        "evidence_at": FIXED_TIME,
        "evidence_at_precision": "exact",
        "domain": "work",
        "actual_result": "Результат измерен.",
        "reassessment": "",
        "notes": "Нужно повторить наблюдение позже.",
    }
    payload.update(overrides)
    return payload


def make_decision_draft() -> DecisionJournalDraft:
    """Build a typed valid Decision draft for codec tests."""

    return DecisionJournalDraft(
        draft=NoteDraft(
            title="Выбор проекта",
            note_type=NoteType.PROJECT,
            content=render_decision_journal_body(
                situation="Нужно выбрать следующий проект.",
                available_options=("Сделать A", "Сделать B"),
                information_known_at_decision_time="Известны сроки и ограничения.",
                criteria=("Риск", "Ценность"),
                chosen_option="Сделать A",
                reasons="A лучше соответствует ограничениям.",
                confidence="Средняя.",
                expected_result="Получится проверяемый результат.",
            ),
            tags=("decision",),
            links=("[[Context]]",),
        ),
        evidence_at=datetime(2026, 9, 5, 15, 30, tzinfo=UTC),
        evidence_at_precision="exact",
        domain="work",
    )


def make_outcome_draft(decision_id: Any = DECISION_ID) -> OutcomeObservationDraft:
    """Build a typed valid Outcome draft for codec tests."""

    return OutcomeObservationDraft(
        draft=NoteDraft(
            title="Результат выбора",
            note_type=NoteType.PROJECT,
            content=render_outcome_observation_body(
                actual_result="Результат измерен.",
                reassessment="",
                notes="Нужно повторить наблюдение позже.",
            ),
            tags=("outcome",),
            links=(),
        ),
        decision_id=decision_id,
        evidence_at=datetime(2026, 9, 5, 15, 30, tzinfo=UTC),
        evidence_at_precision="exact",
        domain="work",
    )


def dry_run_result() -> CreateManagedNoteResult:
    """Return a deterministic fake dry-run with a complete safe file."""

    return CreateManagedNoteResult(
        status=CreateStatus.DRY_RUN,
        plan=CreateNotePlan(
            note_type=NoteType.PROJECT,
            title="Stage 2",
            note_id=DECISION_ID,
            created=datetime(2026, 9, 5, 15, 30, tzinfo=UTC),
            relative_path="10 Projects/Stage 2.md",
            content="---\nid: test\n---\n## Stage 2\n",
        ),
    )


def created_result() -> CreateManagedNoteResult:
    """Return a deterministic fake apply result."""

    return replace(dry_run_result(), status=CreateStatus.CREATED, apply_requested=True)


class RecordingStage2SaveService:
    """Record only the new Stage 2 service seam."""

    def __init__(self) -> None:
        self.prepare_decisions: list[DecisionJournalDraft] = []
        self.apply_decisions: list[DecisionJournalDraft] = []
        self.prepare_outcomes: list[OutcomeObservationDraft] = []
        self.apply_outcomes: list[OutcomeObservationDraft] = []

    def prepare_decision_journal(self, draft: DecisionJournalDraft) -> CreateManagedNoteResult:
        self.prepare_decisions.append(draft)
        return dry_run_result()

    def apply_decision_journal(self, draft: DecisionJournalDraft) -> CreateManagedNoteResult:
        self.apply_decisions.append(draft)
        return created_result()

    def prepare_outcome_observation(
        self,
        draft: OutcomeObservationDraft,
    ) -> CreateManagedNoteResult:
        self.prepare_outcomes.append(draft)
        return dry_run_result()

    def apply_outcome_observation(
        self,
        draft: OutcomeObservationDraft,
    ) -> CreateManagedNoteResult:
        self.apply_outcomes.append(draft)
        return created_result()


def test_decision_http_dto_has_no_late_outcome_fields() -> None:
    """The anti-leakage boundary is structural, not a hidden-field convention."""

    assert set(DecisionJournalPayload.model_fields) == {
        "title",
        "note_type",
        "tags",
        "links",
        "evidence_at",
        "evidence_at_precision",
        "domain",
        "situation",
        "available_options",
        "information_known_at_decision_time",
        "criteria",
        "chosen_option",
        "reasons",
        "confidence",
        "expected_result",
    }
    assert "actual_result" not in DecisionJournalPayload.model_fields
    assert "reassessment" not in DecisionJournalPayload.model_fields


def test_outcome_http_dto_has_exact_relation_and_result_fields() -> None:
    """The Outcome DTO exposes relation/result input but no write authority."""

    assert set(OutcomeObservationPayload.model_fields) == {
        "title",
        "note_type",
        "tags",
        "links",
        "decision_id",
        "evidence_at",
        "evidence_at_precision",
        "domain",
        "actual_result",
        "reassessment",
        "notes",
    }
    assert not {
        "content",
        "front_matter",
        "marker",
        "evidence_kind",
        "self_kind",
        "path",
        "created",
    }.intersection(OutcomeObservationPayload.model_fields)


def test_renderers_are_server_owned_and_exact() -> None:
    """Renderers emit the only two accepted structured body shapes."""

    decision = render_decision_journal_body(
        situation="Situation",
        available_options=("A", "B"),
        information_known_at_decision_time="Known",
        criteria=("Criterion",),
        chosen_option="A",
        reasons="Reasons",
        confidence="Confidence",
        expected_result="Expected",
    )
    assert decision == (
        "## Situation\n\nSituation\n\n"
        "## Available options\n\n- A\n- B\n\n"
        "## Information known at decision time\n\nKnown\n\n"
        "## Criteria\n\n- Criterion\n\n"
        "## Chosen option\n\nA\n\n"
        "## Reasons\n\nReasons\n\n"
        "## Confidence\n\nConfidence\n\n"
        "## Expected result\n\nExpected\n\n"
        "## Actual result\n\n"
        "## Reassessment\n\n"
    )
    assert render_outcome_observation_body(
        actual_result="Actual",
        reassessment="Reassessment",
        notes="Notes",
    ) == ("## Actual result\n\nActual\n\n## Reassessment\n\nReassessment\n\n## Notes\n\nNotes\n\n")


def test_stage2_confirmation_tokens_are_purpose_separated_and_digest_only() -> None:
    """Each token binds exact NoteDraft plus Stage 2 metadata without plaintext."""

    codec = ReviewTokenCodec(b"stage2-review-secret-that-is-at-least-32-bytes")
    decision = make_decision_draft()
    outcome = make_outcome_draft()
    decision_token = codec.issue_decision_journal_confirmation(decision)
    outcome_token = codec.issue_outcome_observation_confirmation(outcome)

    decision_claims = codec.verify_decision_journal_confirmation(decision_token, decision)
    outcome_claims = codec.verify_outcome_observation_confirmation(outcome_token, outcome)
    assert decision_claims.purpose == DECISION_JOURNAL_CONFIRMATION_PURPOSE
    assert outcome_claims.purpose == OUTCOME_OBSERVATION_CONFIRMATION_PURPOSE
    assert len(decision_claims.draft_sha256) == 64
    assert len(decision_claims.metadata_sha256) == 64
    assert decision.draft.content not in decision_token
    assert outcome.draft.content not in outcome_token

    tampered_token = decision_token[:-1] + ("A" if decision_token[-1] != "A" else "B")
    with pytest.raises(ReviewTokenError):
        codec.verify_decision_journal_confirmation(tampered_token, decision)
    with pytest.raises(ReviewTokenError):
        codec.verify_outcome_observation_confirmation(decision_token, outcome)
    with pytest.raises(ReviewTokenError):
        codec.verify_decision_journal_confirmation(outcome_token, decision)
    with pytest.raises(ReviewTokenError):
        codec.verify_confirmation(
            decision_token,
            review_token=codec.issue_text(),
            draft=decision.draft,
            mode=ReviewTokenMode.TEXT,
        )


def test_stage2_confirmation_tokens_are_isolated_from_generic_and_personal_memory() -> None:
    """Every Stage 2 purpose stays outside the existing generic and PM contracts."""

    codec = ReviewTokenCodec(b"stage2-isolation-secret-that-is-at-least-32-bytes")
    decision = make_decision_draft()
    outcome = make_outcome_draft()
    review_token = codec.issue_text()
    personal_memory_metadata = PersonalMemoryMetadata(
        evidence_kind=EvidenceKind.EXPLICIT_USER_FACT,
        self_kind=SelfKind.MEMORY,
        evidence_at=datetime(2026, 9, 5, 15, 30, tzinfo=UTC),
        evidence_at_precision=EvidenceAtPrecision.EXACT,
        domain="work",
    )
    generic_token = codec.issue_confirmation(
        review_token=review_token,
        draft=decision.draft,
        mode=ReviewTokenMode.TEXT,
    )
    personal_memory_token = codec.issue_personal_memory_confirmation(
        review_token=review_token,
        draft=decision.draft,
        metadata=personal_memory_metadata,
    )
    decision_token = codec.issue_decision_journal_confirmation(decision)
    outcome_token = codec.issue_outcome_observation_confirmation(outcome)

    for token in (generic_token, personal_memory_token):
        with pytest.raises(ReviewTokenError):
            codec.verify_decision_journal_confirmation(token, decision)
        with pytest.raises(ReviewTokenError):
            codec.verify_outcome_observation_confirmation(token, outcome)
    for token in (decision_token, outcome_token):
        with pytest.raises(ReviewTokenError):
            codec.verify_confirmation(
                token,
                review_token=review_token,
                draft=decision.draft,
                mode=ReviewTokenMode.TEXT,
            )
        with pytest.raises(ReviewTokenError):
            codec.verify_personal_memory_confirmation(
                token,
                review_token=review_token,
                draft=decision.draft,
                metadata=personal_memory_metadata,
            )
    with pytest.raises(ReviewTokenError):
        codec.verify_decision_journal_confirmation(outcome_token, decision)
    with pytest.raises(ReviewTokenError):
        codec.verify_outcome_observation_confirmation(decision_token, outcome)


def test_decision_confirmation_binds_each_note_and_metadata_mutation() -> None:
    """Changing any valid NoteDraft or metadata projection invalidates the token."""

    codec = ReviewTokenCodec(b"decision-binding-secret-that-is-at-least-32-bytes")
    draft = make_decision_draft()
    token = codec.issue_decision_journal_confirmation(draft)
    changed_body = render_decision_journal_body(
        situation="Нужно выбрать следующий проект.",
        available_options=("Сделать A", "Сделать B"),
        information_known_at_decision_time="Известны сроки и ограничения.",
        criteria=("Риск", "Ценность"),
        chosen_option="Сделать A",
        reasons="Изменённые причины.",
        confidence="Средняя.",
        expected_result="Получится проверяемый результат.",
    )
    mutations = (
        replace(draft, draft=replace(draft.draft, title="Другое решение")),
        replace(draft, draft=replace(draft.draft, note_type=NoteType.AREA)),
        replace(draft, draft=replace(draft.draft, content=changed_body)),
        replace(draft, draft=replace(draft.draft, tags=("other",))),
        replace(draft, draft=replace(draft.draft, links=("[[Other]]",))),
        replace(draft, evidence_at=datetime(2026, 9, 5, 15, 31, tzinfo=UTC)),
        replace(draft, evidence_at_precision=EvidenceAtPrecision.UNKNOWN),
        replace(draft, evidence_at="unknown", evidence_at_precision=EvidenceAtPrecision.UNKNOWN),
        replace(draft, domain=None),
    )
    for mutated in mutations:
        with pytest.raises(ReviewTokenError):
            codec.verify_decision_journal_confirmation(token, mutated)


def test_outcome_confirmation_binds_each_note_relation_and_metadata_mutation() -> None:
    """Changing Outcome content, relation, or time/domain invalidates its token."""

    codec = ReviewTokenCodec(b"outcome-binding-secret-that-is-at-least-32-bytes")
    draft = make_outcome_draft()
    token = codec.issue_outcome_observation_confirmation(draft)
    changed_body = render_outcome_observation_body(
        actual_result="Изменённый результат.",
        reassessment="",
        notes="Нужно повторить наблюдение позже.",
    )
    other_decision_id = parse_uuid7("0198f4c5-6a00-7000-8000-000000000003")
    mutations = (
        replace(draft, draft=replace(draft.draft, title="Другой outcome")),
        replace(draft, draft=replace(draft.draft, note_type=NoteType.AREA)),
        replace(draft, draft=replace(draft.draft, content=changed_body)),
        replace(draft, draft=replace(draft.draft, tags=("other",))),
        replace(draft, draft=replace(draft.draft, links=("[[Other]]",))),
        replace(draft, decision_id=other_decision_id),
        replace(draft, evidence_at=datetime(2026, 9, 5, 15, 31, tzinfo=UTC)),
        replace(draft, evidence_at_precision=EvidenceAtPrecision.UNKNOWN),
        replace(draft, evidence_at="unknown", evidence_at_precision=EvidenceAtPrecision.UNKNOWN),
        replace(draft, domain=None),
    )
    for mutated in mutations:
        with pytest.raises(ReviewTokenError):
            codec.verify_outcome_observation_confirmation(token, mutated)


def test_structured_routes_prepare_and_apply_without_llm_review_token() -> None:
    """The new routes accept only structured data and use the dedicated service methods."""

    saver = RecordingStage2SaveService()
    with TestClient(
        create_app(save_service=cast(DraftSaveService, saver)),
        base_url=LOOPBACK_BASE_URL,
    ) as client:
        prepared = client.post(
            "/api/drafts/decision-journal/save/prepare",
            json={"decision": decision_payload()},
            headers=DRAFT_HEADERS,
        )
        assert prepared.status_code == 200, prepared.text
        token = prepared.json()["confirmation_token"]
        applied = client.post(
            "/api/drafts/decision-journal/save/apply",
            json={"confirmation_token": token, "decision": decision_payload()},
            headers=DRAFT_HEADERS,
        )

    assert applied.status_code == 200, applied.text
    assert len(saver.prepare_decisions) == 1
    assert len(saver.apply_decisions) == 1
    assert saver.prepare_decisions[0].draft.content.startswith("## Situation\n\n")


def test_structured_outcome_routes_prepare_and_apply_without_review_token() -> None:
    """Outcome uses the dedicated service seam and purpose-bound token only."""

    saver = RecordingStage2SaveService()
    with TestClient(
        create_app(save_service=cast(DraftSaveService, saver)),
        base_url=LOOPBACK_BASE_URL,
    ) as client:
        prepared = client.post(
            "/api/drafts/outcome-observation/save/prepare",
            json={"outcome": outcome_payload()},
            headers=DRAFT_HEADERS,
        )
        assert prepared.status_code == 200, prepared.text
        applied = client.post(
            "/api/drafts/outcome-observation/save/apply",
            json={
                "confirmation_token": prepared.json()["confirmation_token"],
                "outcome": outcome_payload(),
            },
            headers=DRAFT_HEADERS,
        )

    assert applied.status_code == 200, applied.text
    assert len(saver.prepare_outcomes) == 1
    assert len(saver.apply_outcomes) == 1
    assert "## Actual result\n\nРезультат измерен." in saver.prepare_outcomes[0].draft.content


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"available_options": ["Только один"]}, id="one-option"),
        pytest.param({"available_options": ["Один", "Один"]}, id="duplicate-options"),
        pytest.param(
            {"available_options": [f"Вариант {index}" for index in range(21)]},
            id="too-many-options",
        ),
        pytest.param({"criteria": []}, id="no-criteria"),
        pytest.param({"chosen_option": "Несуществующий вариант"}, id="chosen-mismatch"),
        pytest.param({"situation": ""}, id="empty-situation"),
        pytest.param({"information_known_at_decision_time": ""}, id="empty-information"),
        pytest.param({"reasons": ""}, id="empty-reasons"),
        pytest.param({"confidence": ""}, id="empty-confidence"),
        pytest.param({"expected_result": ""}, id="empty-expected-result"),
    ],
)
def test_decision_route_delegates_structural_validation_to_stage2_core(
    overrides: dict[str, object],
) -> None:
    """Options, chosen option, criteria, and narrative stay core-validated."""

    saver = RecordingStage2SaveService()
    with TestClient(
        create_app(save_service=cast(DraftSaveService, saver)),
        base_url=LOOPBACK_BASE_URL,
    ) as client:
        response = client.post(
            "/api/drafts/decision-journal/save/prepare",
            json={"decision": decision_payload(**overrides)},
            headers=DRAFT_HEADERS,
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DECISION_JOURNAL_INVALID_REQUEST"
    assert saver.prepare_decisions == []


@pytest.mark.parametrize(
    ("option_count", "evidence_at", "evidence_at_precision"),
    [
        pytest.param(2, FIXED_TIME, "exact", id="exact-minimum-options"),
        pytest.param(20, FIXED_TIME, "exact", id="exact-maximum-options"),
        pytest.param(2, "unknown", "unknown", id="unknown-time"),
    ],
)
def test_decision_route_accepts_bounded_options_and_exact_or_unknown_time(
    option_count: int,
    evidence_at: str,
    evidence_at_precision: str,
) -> None:
    """The Web projection accepts only the reviewed core's bounded choices."""

    options = [f"Вариант {index}" for index in range(option_count)]
    saver = RecordingStage2SaveService()
    with TestClient(
        create_app(save_service=cast(DraftSaveService, saver)),
        base_url=LOOPBACK_BASE_URL,
    ) as client:
        response = client.post(
            "/api/drafts/decision-journal/save/prepare",
            json={
                "decision": decision_payload(
                    available_options=options,
                    chosen_option=options[-1],
                    evidence_at=evidence_at,
                    evidence_at_precision=evidence_at_precision,
                )
            },
            headers=DRAFT_HEADERS,
        )

    assert response.status_code == 200, response.text
    assert len(saver.prepare_decisions) == 1
    prepared = saver.prepare_decisions[0]
    assert prepared.evidence_at == (
        "unknown" if evidence_at == "unknown" else datetime(2026, 9, 5, 15, 30, tzinfo=UTC)
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"actual_result": "late"},
        {"reassessment": "late"},
        {"content": "arbitrary body"},
        {"front_matter": {}},
    ],
)
def test_decision_route_rejects_late_or_application_owned_fields(
    extra: dict[str, object],
) -> None:
    """Extra fields cannot smuggle late data or canonical write authority."""

    payload = {"decision": decision_payload()}
    payload["decision"].update(extra)
    saver = RecordingStage2SaveService()
    with TestClient(
        create_app(save_service=cast(DraftSaveService, saver)),
        base_url=LOOPBACK_BASE_URL,
    ) as client:
        response = client.post(
            "/api/drafts/decision-journal/save/prepare",
            json=payload,
            headers=DRAFT_HEADERS,
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DECISION_JOURNAL_INVALID_REQUEST"
    assert saver.prepare_decisions == []


def test_stage2_routes_inherit_json_loopback_boundary() -> None:
    """Host, Origin, method, and request header remain mandatory on all routes."""

    with TestClient(create_app(), base_url=LOOPBACK_BASE_URL) as client:
        missing_header = client.post(
            "/api/drafts/decision-journal/save/prepare",
            json={"decision": decision_payload()},
        )
        bad_origin = client.post(
            "/api/drafts/outcome-observation/save/apply",
            json={"confirmation_token": "bad", "outcome": outcome_payload()},
            headers={**DRAFT_HEADERS, "origin": "https://evil.example"},
        )
        get_response = client.get("/api/drafts/decision-journal/save/prepare")

    assert missing_header.status_code == 400
    assert missing_header.json()["error"]["code"] == "DECISION_JOURNAL_INVALID_REQUEST"
    assert bad_origin.status_code == 400
    assert bad_origin.json()["error"]["code"] == "OUTCOME_OBSERVATION_INVALID_REQUEST"
    assert get_response.status_code == 405
    assert get_response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("path", "error_code"),
    [
        pytest.param(
            "/api/drafts/decision-journal/save/prepare",
            "DECISION_JOURNAL_INVALID_REQUEST",
            id="decision-prepare",
        ),
        pytest.param(
            "/api/drafts/decision-journal/save/apply",
            "DECISION_JOURNAL_INVALID_REQUEST",
            id="decision-apply",
        ),
        pytest.param(
            "/api/drafts/outcome-observation/save/prepare",
            "OUTCOME_OBSERVATION_INVALID_REQUEST",
            id="outcome-prepare",
        ),
        pytest.param(
            "/api/drafts/outcome-observation/save/apply",
            "OUTCOME_OBSERVATION_INVALID_REQUEST",
            id="outcome-apply",
        ),
    ],
)
@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"origin": "https://evil.example"}, id="bad-origin"),
        pytest.param({"host": "evil.example"}, id="bad-host"),
        pytest.param({"content-type": "text/plain"}, id="non-json"),
    ],
)
def test_all_stage2_routes_reject_untrusted_boundary_before_pydantic(
    path: str,
    error_code: str,
    headers: dict[str, str],
) -> None:
    """Every new write path inherits the existing no-CORS loopback JSON gate."""

    saver = RecordingStage2SaveService()
    request_headers = {**DRAFT_HEADERS, "content-type": "application/json", **headers}
    with TestClient(
        create_app(save_service=cast(DraftSaveService, saver)),
        base_url=LOOPBACK_BASE_URL,
    ) as client:
        response = client.post(path, content=b"{}", headers=request_headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == error_code
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
    assert saver.prepare_decisions == []
    assert saver.apply_decisions == []
    assert saver.prepare_outcomes == []
    assert saver.apply_outcomes == []


def test_all_stage2_routes_enforce_raw_body_cap_before_pydantic() -> None:
    """An oversized declared body is rejected without consuming request chunks."""

    status, headers, body, receive_calls = send_raw_asgi_request(
        create_app(),
        path="/api/drafts/outcome-observation/save/prepare",
        headers={
            **DRAFT_HEADERS,
            "content-type": "application/json",
            "content-length": str(MAX_RAW_DRAFT_BODY_BYTES + 1),
            "host": "127.0.0.1",
        },
        body_chunks=(b"{}",),
    )

    assert status == 413
    assert headers["cache-control"] == "no-store"
    assert cast(dict[str, Any], json.loads(body))["error"]["code"] == "DRAFT_CONTENT_TOO_LARGE"
    assert receive_calls == 0


@pytest.mark.parametrize(
    ("overrides", "expected_status"),
    [
        pytest.param(
            {"actual_result": "", "reassessment": "Переоценка."},
            200,
            id="reassessment-only",
        ),
        pytest.param({"actual_result": "", "reassessment": ""}, 400, id="both-empty"),
        pytest.param(
            {"decision_id": "00000000-0000-4000-8000-000000000001"},
            400,
            id="non-v7-uuid",
        ),
        pytest.param(
            {"decision_id": "0198f4c5-6a00-7000-8000-00000000000A"},
            400,
            id="uppercase-uuid",
        ),
        pytest.param(
            {"decision_id": "0198f4c5-6a00-7000-8000-000000000001"},
            200,
            id="different-valid-target",
        ),
    ],
)
def test_outcome_route_validates_result_and_strict_uuid_before_save(
    overrides: dict[str, object],
    expected_status: int,
) -> None:
    """Outcome keeps the core minimum and canonical lowercase UUIDv7 boundary."""

    saver = RecordingStage2SaveService()
    with TestClient(
        create_app(save_service=cast(DraftSaveService, saver)),
        base_url=LOOPBACK_BASE_URL,
    ) as client:
        response = client.post(
            "/api/drafts/outcome-observation/save/prepare",
            json={"outcome": {**outcome_payload(), **overrides}},
            headers=DRAFT_HEADERS,
        )

    # The recording seam does not perform the vault target check; target
    # existence belongs to the existing Stage 2 core used by production.
    if expected_status == 200:
        assert response.status_code == expected_status, response.text
        assert len(saver.prepare_outcomes) == 1
    else:
        assert response.status_code == expected_status
        assert response.json()["error"]["code"] == "OUTCOME_OBSERVATION_INVALID_REQUEST"
        assert saver.prepare_outcomes == []


def test_real_stage2_save_uses_core_and_rechecks_outcome_target(tmp_path: Path) -> None:
    """Dry-run is lossless; Outcome apply rejects a deleted target and writes nothing."""

    vault = create_vault(tmp_path / "vault")
    for filename in ("Project.md", "Area.md", "Resource.md", "Zettel.md"):
        (vault / "_templates" / filename).write_text("# template\n", encoding="utf-8")

    with TestClient(
        create_app(vault_path_override=str(vault)),
        base_url=LOOPBACK_BASE_URL,
    ) as client:
        decision_prepared = client.post(
            "/api/drafts/decision-journal/save/prepare",
            json={"decision": decision_payload()},
            headers=DRAFT_HEADERS,
        )
        assert decision_prepared.status_code == 200, decision_prepared.text
        before_apply = snapshot_tree(vault)
        decision_applied = client.post(
            "/api/drafts/decision-journal/save/apply",
            json={
                "confirmation_token": decision_prepared.json()["confirmation_token"],
                "decision": decision_payload(),
            },
            headers=DRAFT_HEADERS,
        )
        assert decision_applied.status_code == 200, decision_applied.text
        saved_decision = decision_applied.json()["note"]
        decision_path = vault / saved_decision["relative_path"]

        outcome_prepared = client.post(
            "/api/drafts/outcome-observation/save/prepare",
            json={"outcome": outcome_payload(saved_decision["id"])},
            headers=DRAFT_HEADERS,
        )
        assert outcome_prepared.status_code == 200, outcome_prepared.text
        outcome_before_apply = snapshot_tree(vault)
        decision_path.unlink()
        outcome_applied = client.post(
            "/api/drafts/outcome-observation/save/apply",
            json={
                "confirmation_token": outcome_prepared.json()["confirmation_token"],
                "outcome": outcome_payload(saved_decision["id"]),
            },
            headers=DRAFT_HEADERS,
        )

    assert set(snapshot_tree(vault)) == set(outcome_before_apply) - {
        saved_decision["relative_path"]
    }
    assert outcome_applied.status_code == 409
    assert outcome_applied.json()["error"]["code"] == "SAVE_PREFLIGHT_CONFLICT"
    assert "absolute" not in outcome_applied.text.lower()
    assert "front_matter" not in outcome_applied.text.lower()
    assert before_apply != outcome_before_apply
    assert snapshot_tree(vault) == {
        path: content
        for path, content in outcome_before_apply.items()
        if path != saved_decision["relative_path"]
    }
    assert not decision_path.exists()


def test_real_stage2_success_writes_separate_linked_outcome(tmp_path: Path) -> None:
    """A successful Outcome adds a linked note and leaves the Journal byte-stable."""

    vault = create_vault(tmp_path / "vault")
    for filename in ("Project.md", "Area.md", "Resource.md", "Zettel.md"):
        (vault / "_templates" / filename).write_text("# template\n", encoding="utf-8")

    with TestClient(
        create_app(vault_path_override=str(vault)),
        base_url=LOOPBACK_BASE_URL,
    ) as client:
        decision_prepared = client.post(
            "/api/drafts/decision-journal/save/prepare",
            json={"decision": decision_payload()},
            headers=DRAFT_HEADERS,
        )
        decision_applied = client.post(
            "/api/drafts/decision-journal/save/apply",
            json={
                "confirmation_token": decision_prepared.json()["confirmation_token"],
                "decision": decision_payload(),
            },
            headers=DRAFT_HEADERS,
        )
        assert decision_applied.status_code == 200, decision_applied.text
        saved_decision = decision_applied.json()["note"]
        decision_path = vault / saved_decision["relative_path"]
        journal_before_outcome = decision_path.read_bytes()

        outcome_prepared = client.post(
            "/api/drafts/outcome-observation/save/prepare",
            json={"outcome": outcome_payload(saved_decision["id"])},
            headers=DRAFT_HEADERS,
        )
        assert outcome_prepared.status_code == 200, outcome_prepared.text
        outcome_applied = client.post(
            "/api/drafts/outcome-observation/save/apply",
            json={
                "confirmation_token": outcome_prepared.json()["confirmation_token"],
                "outcome": outcome_payload(saved_decision["id"]),
            },
            headers=DRAFT_HEADERS,
        )

    assert outcome_applied.status_code == 200, outcome_applied.text
    saved_outcome = outcome_applied.json()["note"]
    outcome_path = vault / saved_outcome["relative_path"]
    parsed_outcome = parse_front_matter(outcome_path.read_text(encoding="utf-8"))
    assert saved_outcome["id"] != saved_decision["id"]
    assert saved_outcome["relative_path"] != saved_decision["relative_path"]
    assert parsed_outcome.data["evidence_kind"] == "outcome_later_observation"
    assert parsed_outcome.data["self_kind"] == "outcome"
    assert parsed_outcome.data["decision_id"] == saved_decision["id"]
    assert parsed_outcome.body == render_outcome_observation_body(
        actual_result="Результат измерен.",
        reassessment="",
        notes="Нужно повторить наблюдение позже.",
    )
    assert decision_path.read_bytes() == journal_before_outcome


def test_ui_journal_is_separate_storage_free_and_shows_search_ids() -> None:
    """Static UI keeps structured flows page-local and renders existing safe IDs."""

    journal_javascript = Path(
        "src/second_brain/entrypoints/web/static/decision-journal.js"
    ).read_text(encoding="utf-8")
    javascript = (
        Path("src/second_brain/entrypoints/web/static/app.js").read_text(encoding="utf-8")
        + journal_javascript
    )
    html = Path("src/second_brain/entrypoints/web/static/index.html").read_text(encoding="utf-8")
    assert "data-decision-journal-surface" in html
    assert 'data-journal-mode="decision"' in html
    assert 'data-journal-mode="outcome"' in html
    decision_form = html.split('<form class="journal-form" data-decision-form>', 1)[1].split(
        "</form>", 1
    )[0]
    assert "Actual result" not in decision_form
    assert "Reassessment" not in decision_form
    assert "/api/drafts/decision-journal/save/prepare" in javascript
    assert "/api/drafts/outcome-observation/save/apply" in javascript
    assert "decisionConfirmationToken = null" in javascript
    assert "outcomeDecisionId.value = savedDecisionId" in javascript
    assert 'addSearchField(fields, "ID", hit.id)' in javascript
    assert 'addSearchField(fields, "ID", note.id)' in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript
    assert "indexedDB" not in javascript
    assert javascript.count("innerHTML") == 1
