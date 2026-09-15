"""Adversarial Stage 13D regression coverage for the Decision Compass boundary."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.compare import CompareExecutionContextV1
from second_brain.application.decision_compass import (
    BuildDecisionCompass,
    DecisionCompassErrorCodeV1,
    DecisionCompassResultV1,
)
from second_brain.application.goal_progress import parse_definition_record
from second_brain.application.growth import GrowthMappingStore
from second_brain.application.growth_advisor import GrowthAdvisorGoalPreviewV1
from second_brain.application.ports import CancellationTokenSource
from second_brain.entrypoints.web.app import (
    DECISION_COMPASS_ADVISOR_EXECUTE_PATH,
    DECISION_COMPASS_ADVISOR_PREVIEW_PATH,
    DECISION_COMPASS_PATH,
    DECISION_COMPASS_REQUEST_HEADER_VALUE,
    MAX_RAW_DECISION_COMPASS_BODY_BYTES,
    create_app,
)
from tests.conftest import snapshot_tree, write_note
from tests.test_decision_compass import _request as _compass_request
from tests.test_growth_goal_progress_composition import (
    GOAL_B_ID,
    GOAL_ID,
    GROWTH_AT,
    _goal_hash,
    _goal_hash_for,
    _numeric_definition_fields,
    _observation_fields,
    _render_note,
    _seed,
    _write_definition_and_observation,
)
from tests.test_web_decision_compass import (
    BASE_URL,
    _disabled_auth_config,
    _fixture_app,
    _github_auth_config,
    _headers,
    _request_payload,
)
from tests.test_web_security import _raw_request

_B_DEFINITION_ID = UUID("0198f4c5-6a00-7000-8000-000000001205")
_B_OBSERVATION_ID = UUID("0198f4c5-6a00-7000-8000-000000001206")


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _execute_payload(request: dict[str, object]) -> dict[str, object]:
    selected_goal = request["selected_goal"]
    assert isinstance(selected_goal, dict)
    return {
        "request": request,
        "preview": {
            "contract_version": "growth-advisor-v1",
            "goal_source_uuid": selected_goal["source_uuid"],
            "goal_identity_fingerprint": selected_goal["identity_fingerprint"],
            "assistant_contract_version": "assistant-v1",
            "advisor_policy_id": "growth-advisor-owner-explicit-goal-v1",
            "goal_text": "Выбранная текущая цель",
            "goal_text_utf8_bytes": len("Выбранная текущая цель".encode()),
        },
        "confirmed": True,
    }


def _raw_headers(*, content_length: str | None = None) -> list[tuple[str, str]]:
    headers = [
        ("host", "127.0.0.1"),
        ("content-type", "application/json"),
        ("x-second-brain-request", DECISION_COMPASS_REQUEST_HEADER_VALUE),
    ]
    if content_length is not None:
        headers.append(("content-length", content_length))
    return headers


def _execution() -> CompareExecutionContextV1:
    return CompareExecutionContextV1(
        cancellation=CancellationTokenSource().token,
        deadline=10_000_000_000.0,
    )


class _TamperingPreviewService:
    """Return an invalid typed preview to exercise the transport revalidation seam."""

    def __init__(self, inner: object) -> None:
        self.inner = inner

    def build(self, request: object) -> object:
        return self.inner.build(request)  # type: ignore[attr-defined]

    def preview_advisor(self, request: object) -> object:
        preview = self.inner.preview_advisor(request)  # type: ignore[attr-defined]
        assert type(preview) is GrowthAdvisorGoalPreviewV1
        object.__setattr__(preview, "goal_text", "Секретный текст не должен выйти")
        return preview

    def execute_advisor(
        self,
        request: object,
        preview: object,
        *,
        confirmed: bool,
    ) -> object:
        return self.inner.execute_advisor(  # type: ignore[attr-defined]
            request,
            preview,
            confirmed=confirmed,
        )


def test_all_compass_actions_require_owner_auth_before_source_or_provider(
    tmp_path: Path,
) -> None:
    """Anonymous users get one generic response on every Compass action."""

    _disabled_application, service = _fixture_app(tmp_path)
    application = create_app(
        decision_compass_service=service,
        web_auth_config=_github_auth_config(),
    )
    request = _request_payload(_goal_hash(service.reader))
    routes = (
        (DECISION_COMPASS_PATH, request),
        (DECISION_COMPASS_ADVISOR_PREVIEW_PATH, request),
        (DECISION_COMPASS_ADVISOR_EXECUTE_PATH, _execute_payload(request)),
    )

    with TestClient(application, base_url=BASE_URL) as client:
        responses = [
            client.post(path, json=payload, headers=_headers()) for path, payload in routes
        ]

    assert [response.status_code for response in responses] == [401, 401, 401]
    assert [response.json() for response in responses] == [
        {"error": {"code": "AUTH_REQUIRED", "message": "Требуется вход"}}
    ] * 3
    assert all(str(GOAL_ID) not in response.text for response in responses)
    assert service.build_calls == []
    assert service.growth.preview_calls == 0
    assert service.growth.execute_calls == 0


def test_compass_json_boundary_rejects_parser_and_strict_schema_attacks(
    tmp_path: Path,
) -> None:
    """Malformed, duplicate, non-finite and non-strict values never reach services."""

    application, service = _fixture_app(tmp_path)
    request = _request_payload(_goal_hash(service.reader))
    unknown_nested = {
        **request,
        "options": [{**request["options"][0], "unexpected": True}, request["options"][1]],  # type: ignore[index]
    }
    execute_with_non_strict_confirmation = _execute_payload(request)
    execute_with_non_strict_confirmation["confirmed"] = 1

    cases = (
        (DECISION_COMPASS_PATH, b'{"task":"one","task":"two"}'),
        (DECISION_COMPASS_PATH, b'{"progress_as_of":NaN}'),
        (DECISION_COMPASS_PATH, _json_bytes(unknown_nested)),
        (DECISION_COMPASS_ADVISOR_PREVIEW_PATH, b'{"task":'),
        (
            DECISION_COMPASS_ADVISOR_EXECUTE_PATH,
            _json_bytes(execute_with_non_strict_confirmation),
        ),
    )

    with TestClient(application, base_url=BASE_URL) as client:
        responses = [client.post(path, content=body, headers=_headers()) for path, body in cases]

    assert all(response.status_code == 400 for response in responses)
    assert all("Секретный" not in response.text for response in responses)
    assert all("traceback" not in response.text.lower() for response in responses)
    assert service.build_calls == []
    assert service.growth.preview_calls == 0
    assert service.growth.execute_calls == 0


def test_compass_raw_body_cap_rejects_declared_and_streamed_overflow(
    tmp_path: Path,
) -> None:
    """Both HTTP framing and streamed bytes are bounded before parsing or service calls."""

    application, service = _fixture_app(tmp_path)
    declared = _raw_request(
        application,
        path=DECISION_COMPASS_PATH,
        headers=_raw_headers(content_length=str(MAX_RAW_DECISION_COMPASS_BODY_BYTES + 1)),
        body=b"",
    )
    streamed = _raw_request(
        application,
        path=DECISION_COMPASS_PATH,
        headers=_raw_headers(),
        body=b"",
        body_chunks=(b"x" * MAX_RAW_DECISION_COMPASS_BODY_BYTES, b"x"),
    )

    assert declared[0] == 413
    assert declared[3] == 0
    assert streamed[0] == 413
    assert streamed[3] >= 2
    assert b"RESULT_TOO_LARGE" in declared[2]
    assert b"RESULT_TOO_LARGE" in streamed[2]
    assert service.build_calls == []
    assert service.growth.preview_calls == 0
    assert service.growth.execute_calls == 0


def test_preview_route_revalidates_typed_preview_before_serialization(tmp_path: Path) -> None:
    """A compromised in-memory builder cannot project an invalid Goal preview."""

    base_application, service = _fixture_app(tmp_path)
    del base_application
    application = create_app(
        decision_compass_service=_TamperingPreviewService(service),
        web_auth_config=_disabled_auth_config(),
    )

    with TestClient(application, base_url=BASE_URL) as client:
        response = client.post(
            DECISION_COMPASS_ADVISOR_PREVIEW_PATH,
            json=_request_payload(_goal_hash(service.reader)),
            headers=_headers(),
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": DecisionCompassErrorCodeV1.INTERNAL.value,
            "message": "Decision Compass временно недоступен.",
        }
    }
    assert "Секретный текст не должен выйти" not in response.text


def _seed_two_goal_progress(
    tmp_path: Path,
) -> tuple[Path, GrowthMappingStore, FileSystemVaultReader, str]:
    """Create same-text Goals with independently fingerprinted progress records."""

    vault, store, reader = _seed(tmp_path, include_goal_b=True)
    _write_definition_and_observation(vault, reader)
    goal_b_hash = _goal_hash_for(reader, GOAL_B_ID)
    definition_b = _numeric_definition_fields(goal_b_hash)
    definition_b.update(
        {
            "id": str(_B_DEFINITION_ID),
            "goal_source_uuid": str(GOAL_B_ID),
        }
    )
    parsed_b = parse_definition_record(definition_b)
    assert parsed_b is not None
    observation_b = _observation_fields(parsed_b, value="84")
    observation_b.update(
        {
            "id": str(_B_OBSERVATION_ID),
            "goal_source_uuid": str(GOAL_B_ID),
        }
    )
    write_note(vault, "40 Zettelkasten/Definition-B.md", _render_note(definition_b))
    write_note(vault, "40 Zettelkasten/Observation-B.md", _render_note(observation_b))
    return vault, store, reader, goal_b_hash


def test_real_compass_result_isolated_by_selected_goal_uuid_and_fingerprint(
    tmp_path: Path,
) -> None:
    """Same-text foreign Goal data cannot enter a selected Goal's Compass result."""

    vault, store, reader, goal_b_hash = _seed_two_goal_progress(tmp_path)
    before = snapshot_tree(vault)
    compass = BuildDecisionCompass(
        reader=reader,
        store=store,
        growth_clock=lambda: GROWTH_AT,
        behavioral_clock=lambda: GROWTH_AT,
        goal_clock=lambda: GROWTH_AT,
    )
    request = _compass_request(_goal_hash(reader))
    result = compass.execute(request, execution=_execution())

    assert isinstance(result, DecisionCompassResultV1)
    encoded = result.to_json()
    assert str(GOAL_ID) in encoded
    assert str(GOAL_B_ID) not in encoded
    assert goal_b_hash not in encoded
    assert str(_B_DEFINITION_ID) not in encoded
    assert str(_B_OBSERVATION_ID) not in encoded
    assert snapshot_tree(vault) == before


def test_real_compass_result_keeps_branch_statuses_without_oracle_fields(
    tmp_path: Path,
) -> None:
    """Independent branch outcomes remain visible without ranking or causal verdicts."""

    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader, value="84")
    compass = BuildDecisionCompass(
        reader=reader,
        store=store,
        growth_clock=lambda: GROWTH_AT,
        behavioral_clock=lambda: GROWTH_AT,
        goal_clock=lambda: GROWTH_AT,
    )
    result = compass.execute(_compass_request(_goal_hash(reader)), execution=_execution())

    assert isinstance(result, DecisionCompassResultV1)
    payload = result.to_json()
    assert '"simulate_me"' in payload
    assert '"growth_progress"' in payload
    assert '"status":"away_from_target"' in payload
    assert '"overall_score"' not in payload
    assert '"best_option"' not in payload
    assert '"winner"' not in payload
    assert '"alignment_score"' not in payload
    assert '"success_probability"' not in payload
