"""Security and privacy regression tests for the Stage 7 Assistant/Compare Web slice."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from fastapi.testclient import TestClient

from second_brain.application.assistant import (
    ASSISTANT_CONTRACT_VERSION,
    ASSISTANT_OUTPUT_LABEL,
    AssistantReasoningEnvelopeV1,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
)
from second_brain.application.ports import CancellationToken
from second_brain.entrypoints.web.app import (
    ASSISTANT_REQUEST_HEADER_VALUE,
    COMPARE_REQUEST_HEADER_VALUE,
    MAX_RAW_ASSISTANT_BODY_BYTES,
    create_app,
)
from second_brain.entrypoints.web.assistant_compare import (
    ProductionAssistantWebService,
    Stage7RequestPayload,
)

BASE_URL = "http://127.0.0.1"


@dataclass(slots=True)
class RecordingAssistantWebService:
    calls: list[Stage7RequestPayload] = field(default_factory=list)

    def execute(self, payload: Stage7RequestPayload) -> dict[str, object]:
        self.calls.append(payload)
        return {
            "output_label": ASSISTANT_OUTPUT_LABEL,
            "kind": "analysis",
            "recommendation": None,
            "selected_option": None,
            "rationale": ["Проверен только явный ввод."],
            "evidence_refs": [],
            "constraints_used": [],
            "objectives_used": [],
            "uncertainty": [],
            "abstention_code": None,
            "contract_version": ASSISTANT_CONTRACT_VERSION,
        }


@dataclass(slots=True)
class RecordingCompareWebService:
    calls: list[Stage7RequestPayload] = field(default_factory=list)

    def execute(self, payload: Stage7RequestPayload) -> dict[str, object]:
        self.calls.append(payload)
        return {
            "option_ids": [option.id for option in payload.options],
            "assistant": {"state": "error", "result": None, "error": {"code": "COMPARE_BRANCH_UNAVAILABLE", "message": "safe"}},
            "simulate_me": {"state": "abstention", "result": {"kind": "abstention", "selected_option": None}, "error": None},
            "delta": {
                "relation": "assistant_error",
                "assistant_state": "error",
                "simulate_me_state": "abstention",
                "assistant_selected_option_id": None,
                "simulate_me_selected_option_id": None,
                "explanation": "Ветки сохранены отдельно.",
            },
            "derivation_version": "compare-v1",
            "policy_id": "compare-structural-delta-v1",
            "policy_fingerprint": "fixture",
        }


@dataclass(slots=True)
class RecordingAdvisor:
    calls: list[AssistantReasoningEnvelopeV1] = field(default_factory=list)

    def advise(
        self,
        request: AssistantReasoningEnvelopeV1,
        *,
        cancellation: CancellationToken,
    ) -> AssistantResultEnvelopeV1:
        assert not cancellation.is_cancelled()
        self.calls.append(request)
        return AssistantResultEnvelopeV1(
            output_label=ASSISTANT_OUTPUT_LABEL,
            kind=AssistantResultKind.ANALYSIS,
            recommendation=None,
            selected_option=None,
            rationale=("Проверен явный ввод.",),
            evidence_refs=(),
            constraints_used=(),
            objectives_used=(),
            uncertainty=(),
            abstention_code=None,
            contract_version=ASSISTANT_CONTRACT_VERSION,
        )


def _payload() -> dict[str, object]:
    return {
        "task": "Выбрать подход",
        "options": [
            {"id": "option-1", "label": "Первый"},
            {"id": "option-2", "label": "Второй"},
        ],
        "explicit_constraints": ["Без новых секретов"],
        "explicit_goals": ["Сохранить границу приватности"],
        "explicit_context": [
            {"kind": "fact", "text": "Контекст введён вручную"},
            {"kind": "background", "text": "Это фон, а не проверенный факт"},
        ],
    }


def _headers(purpose: str) -> dict[str, str]:
    return {
        "X-Second-Brain-Request": purpose,
        "Origin": BASE_URL,
    }


def test_assistant_api_projects_only_caller_explicit_body() -> None:
    assistant = RecordingAssistantWebService()
    compare = RecordingCompareWebService()
    app = create_app(assistant_web_service=assistant, compare_web_service=compare)

    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(
            "/api/assistant",
            json=_payload(),
            headers=_headers(ASSISTANT_REQUEST_HEADER_VALUE),
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
    assert len(assistant.calls) == 1
    call = assistant.calls[0]
    assert call.task == "Выбрать подход"
    assert [item.label for item in call.options] == ["Первый", "Второй"]
    assert call.explicit_constraints == ["Без новых секретов"]
    assert call.explicit_goals == ["Сохранить границу приватности"]
    assert [item.text for item in call.explicit_context] == [
        "Контекст введён вручную",
        "Это фон, а не проверенный факт",
    ]


def test_assistant_production_service_sends_exact_explicit_envelope_to_advisor() -> None:
    advisor = RecordingAdvisor()
    service = ProductionAssistantWebService(advisor=advisor)
    payload = Stage7RequestPayload.model_validate(_payload(), strict=True)

    result = service.execute(payload)

    assert result["kind"] == "analysis"
    assert len(advisor.calls) == 1
    envelope = advisor.calls[0]
    assert envelope.task == payload.task
    assert tuple(option.id for option in envelope.options) == ("option-1", "option-2")
    assert envelope.explicit_constraints == ("Без новых секретов",)
    assert envelope.explicit_goals == ("Сохранить границу приватности",)
    assert tuple(item.text for item in envelope.explicit_context) == (
        "Контекст введён вручную",
        "Это фон, а не проверенный факт",
    )
    assert not hasattr(envelope, "personal_memory")
    assert not hasattr(envelope, "self_model")
    assert not hasattr(envelope, "search")


def test_private_context_fields_are_rejected_before_service_call() -> None:
    assistant = RecordingAssistantWebService()
    compare = RecordingCompareWebService()
    app = create_app(assistant_web_service=assistant, compare_web_service=compare)
    payload = _payload()
    payload["personal_memory"] = ["forbidden"]

    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(
            "/api/assistant",
            json=payload,
            headers=_headers(ASSISTANT_REQUEST_HEADER_VALUE),
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ASSISTANT_INVALID_REQUEST"
    assert assistant.calls == []


def test_stage7_boundary_rejects_missing_purpose_cross_origin_and_oversize() -> None:
    assistant = RecordingAssistantWebService()
    compare = RecordingCompareWebService()
    app = create_app(assistant_web_service=assistant, compare_web_service=compare)

    with TestClient(app, base_url=BASE_URL) as client:
        missing = client.post("/api/assistant", json=_payload(), headers={"Origin": BASE_URL})
        cross_origin = client.post(
            "/api/assistant",
            json=_payload(),
            headers={
                "X-Second-Brain-Request": ASSISTANT_REQUEST_HEADER_VALUE,
                "Origin": "https://example.invalid",
            },
        )
        too_large = client.post(
            "/api/assistant",
            content=b"x" * (MAX_RAW_ASSISTANT_BODY_BYTES + 1),
            headers={
                **_headers(ASSISTANT_REQUEST_HEADER_VALUE),
                "Content-Type": "application/json",
            },
        )

    assert missing.status_code == 400
    assert cross_origin.status_code == 400
    assert too_large.status_code == 413
    assert assistant.calls == []


def test_compare_api_uses_separate_purpose_and_preserves_branch_separation() -> None:
    assistant = RecordingAssistantWebService()
    compare = RecordingCompareWebService()
    app = create_app(assistant_web_service=assistant, compare_web_service=compare)

    with TestClient(app, base_url=BASE_URL) as client:
        wrong = client.post(
            "/api/compare",
            json=_payload(),
            headers=_headers(ASSISTANT_REQUEST_HEADER_VALUE),
        )
        response = client.post(
            "/api/compare",
            json=_payload(),
            headers=_headers(COMPARE_REQUEST_HEADER_VALUE),
        )

    assert wrong.status_code == 400
    assert response.status_code == 200
    body = cast(dict[str, object], response.json())
    assert set(body) >= {"assistant", "simulate_me", "delta"}
    assert len(compare.calls) == 1
    assert assistant.calls == []
