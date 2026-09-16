"""Private Web/API boundary tests for the Stage 19 action gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid7

from fastapi import FastAPI
from fastapi.testclient import TestClient

from second_brain.adapters.actions.github_issues import GitHubActionConfigStatusV1
from second_brain.application.action_gateway import (
    ACTION_GATEWAY_CONNECTOR,
    ACTION_GATEWAY_CREDENTIAL_PROFILE_ID,
    ACTION_GATEWAY_POLICY_ID,
    ActionExecutionResultV1,
    ActionIntentV1,
    ActionKindV1,
    ActionReceiptKindV1,
    ActionReceiptStateV1,
    ActionReceiptV1,
    ExactTargetIdentityV1,
    PreparedExternalActionV1,
    ReversibilityV1,
    RiskClassV1,
    action_gateway_hash,
)
from second_brain.application.action_gateway_orchestration import ActionGatewayStatusV1
from second_brain.entrypoints.web.action_gateway import (
    ACTION_GATEWAY_COMPENSATION_PREPARE_PATH,
    ACTION_GATEWAY_EXECUTE_PATH,
    ACTION_GATEWAY_HISTORY_PATH,
    ACTION_GATEWAY_PREPARE_PATH,
    ACTION_GATEWAY_REQUEST_HEADER_VALUE,
    ACTION_GATEWAY_STATUS_PATH,
    MAX_RAW_ACTION_GATEWAY_BODY_BYTES,
)
from second_brain.entrypoints.web.app import create_app
from second_brain.entrypoints.web.auth import (
    DEFAULT_OAUTH_STATE_TTL_SECONDS,
    DEFAULT_SESSION_TTL_SECONDS,
    SignedSessionCodec,
    WebAuthConfig,
)

BASE_URL = "https://brain.example.test"
OWNER_ID = "42142321"
SESSION_SECRET = b"session-secret-that-is-long-enough-for-tests-123456"
REPOSITORY = "MikeMoore1337/second-brain"


def _auth_config() -> WebAuthConfig:
    return WebAuthConfig(
        mode="github",
        public_base_url=BASE_URL,
        github_client_id="client-id",
        github_client_secret="client-secret",
        allowed_user_id=OWNER_ID,
        session_secret=SESSION_SECRET,
        session_ttl_seconds=DEFAULT_SESSION_TTL_SECONDS,
        oauth_state_ttl_seconds=DEFAULT_OAUTH_STATE_TTL_SECONDS,
        trusted_authorities=frozenset({("brain.example.test", 443)}),
    )


def _session_cookie() -> str:
    codec = SignedSessionCodec(
        secret=SESSION_SECRET,
        allowed_user_id=OWNER_ID,
        ttl_seconds=DEFAULT_SESSION_TTL_SECONDS,
    )
    return codec.issue(OWNER_ID)


def _headers(*, origin: str = BASE_URL) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Origin": origin,
        "X-Second-Brain-Request": ACTION_GATEWAY_REQUEST_HEADER_VALUE,
        "Cookie": f"second_brain_session={_session_cookie()}",
    }


def _intent_payload() -> dict[str, object]:
    return {
        "intent": {
            "contract_version": "action-intent-v1",
            "operation_id": "web-action-1",
            "action_kind": ActionKindV1.GITHUB_ISSUE_CREATE.value,
            "connector": ACTION_GATEWAY_CONNECTOR,
            "repository": REPOSITORY,
            "title": "Внешняя задача",
            "body": "Проверить действие",
        }
    }


def _prepared() -> PreparedExternalActionV1:
    prepared_id = uuid7()
    marker = f"<!-- second-brain-action:{prepared_id} -->"
    semantic: dict[str, object] = {
        "repository": REPOSITORY,
        "title": "Внешняя задача",
        "body": f"Проверить действие\n{marker}",
        "marker": marker,
    }
    target = ExactTargetIdentityV1(REPOSITORY, 1, "R_repo")
    preview = (
        "Предпросмотр действия GitHub\nИзменение возможно только после подтверждения владельца."
    )
    return PreparedExternalActionV1(
        prepared_id,
        "prepared-external-action-v1",
        action_gateway_hash("web-action-1"),
        ActionKindV1.GITHUB_ISSUE_CREATE,
        RiskClassV1.CONTROLLED_WRITE,
        ACTION_GATEWAY_CONNECTOR,
        ACTION_GATEWAY_POLICY_ID,
        ACTION_GATEWAY_CREDENTIAL_PROFILE_ID,
        target,
        "a" * 64,
        semantic,
        action_gateway_hash(semantic),
        preview,
        action_gateway_hash(preview),
        datetime(2026, 9, 16, 20, 0, tzinfo=UTC),
        datetime(2026, 9, 16, 20, 5, tzinfo=UTC),
        ReversibilityV1.COMPENSATION_ONLY,
    )


def _receipt(prepared: PreparedExternalActionV1) -> ActionReceiptV1:
    return ActionReceiptV1(
        receipt_id=uuid7(),
        receipt_kind=ActionReceiptKindV1.ACTION,
        operation_id_fingerprint=prepared.operation_id_fingerprint,
        prepared_action_id=prepared.prepared_action_id,
        intent_fingerprint=prepared.intent_fingerprint,
        action_kind=prepared.action_kind,
        risk=prepared.risk,
        connector_policy_id=prepared.connector_policy_id,
        credential_profile_id=prepared.credential_profile_id,
        target_safe_identity=prepared.exact_target_identity.safe_identity(),
        payload_fingerprint=prepared.payload_fingerprint,
        state=ActionReceiptStateV1.EXECUTED,
        finished_at=datetime(2026, 9, 16, 20, 1, tzinfo=UTC),
        remote_safe_identity={
            "repository": REPOSITORY,
            "issue_number": 1,
            "issue_id": 2,
            "state": "open",
        },
        remote_url=f"https://github.com/{REPOSITORY}/issues/1",
    )


@dataclass
class RecordingActionService:
    prepared: PreparedExternalActionV1 = field(default_factory=_prepared)
    calls: list[str] = field(default_factory=list)
    receipts: tuple[ActionReceiptV1, ...] = ()

    def status(self) -> ActionGatewayStatusV1:
        self.calls.append("status")
        return ActionGatewayStatusV1(
            GitHubActionConfigStatusV1.DISABLED,
            False,
            (),
        )

    def prepare(
        self, intent: ActionIntentV1, *, now: datetime | None = None
    ) -> PreparedExternalActionV1:
        del now
        self.calls.append(f"prepare:{intent.operation_id}")
        return self.prepared

    def issue_confirmation(
        self, prepared: PreparedExternalActionV1, *, now: datetime | None = None
    ) -> str:
        del prepared, now
        self.calls.append("confirm")
        return "confirmation-token-for-page-memory"

    def execute(
        self,
        prepared: PreparedExternalActionV1,
        confirmation: object,
        *,
        now: datetime | None = None,
    ) -> ActionExecutionResultV1:
        del confirmation, now
        self.calls.append("execute")
        receipt = _receipt(prepared)
        return ActionExecutionResultV1(receipt, False)

    def reconcile(
        self, prepared: PreparedExternalActionV1, *, now: datetime | None = None
    ) -> ActionExecutionResultV1:
        del now
        self.calls.append("reconcile")
        return ActionExecutionResultV1(_receipt(prepared), False)

    def execute_compensation(
        self,
        prepared: PreparedExternalActionV1,
        confirmation: object,
        parent_receipt_id: UUID,
        *,
        now: datetime | None = None,
    ) -> ActionExecutionResultV1:
        del confirmation, now
        self.calls.append(f"execute_compensation:{parent_receipt_id}")
        return ActionExecutionResultV1(_receipt(prepared), False)

    def prepare_compensation(
        self,
        parent_receipt_id: UUID,
        operation_id: str,
        *,
        now: datetime | None = None,
    ) -> PreparedExternalActionV1:
        del parent_receipt_id, operation_id, now
        self.calls.append("compensation")
        return self.prepared

    def history(self) -> tuple[ActionReceiptV1, ...]:
        self.calls.append("history")
        return self.receipts


def _app(service: RecordingActionService) -> FastAPI:
    return create_app(action_gateway_service=service, web_auth_config=_auth_config())


def test_anonymous_request_is_rejected_before_action_service() -> None:
    service = RecordingActionService()
    with TestClient(_app(service), base_url=BASE_URL) as client:
        response = client.post(
            ACTION_GATEWAY_STATUS_PATH,
            json={},
            headers={
                "Content-Type": "application/json",
                "Origin": BASE_URL,
                "X-Second-Brain-Request": ACTION_GATEWAY_REQUEST_HEADER_VALUE,
            },
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    assert response.headers["cache-control"] == "no-store"
    assert service.calls == []


def test_action_boundary_requires_same_origin_and_bounded_json() -> None:
    service = RecordingActionService()
    with TestClient(_app(service), base_url=BASE_URL) as client:
        extra = client.post(
            ACTION_GATEWAY_STATUS_PATH,
            json={"unexpected": True},
            headers=_headers(),
        )
        foreign_origin = client.post(
            ACTION_GATEWAY_STATUS_PATH,
            json={},
            headers=_headers(origin="https://evil.example"),
        )
        missing_origin_headers = _headers()
        missing_origin_headers.pop("Origin")
        missing_origin = client.post(
            ACTION_GATEWAY_STATUS_PATH,
            json={},
            headers=missing_origin_headers,
        )
        oversized = client.post(
            ACTION_GATEWAY_STATUS_PATH,
            content=b"x" * (MAX_RAW_ACTION_GATEWAY_BODY_BYTES + 1),
            headers=_headers(),
        )
        get_response = client.get(ACTION_GATEWAY_STATUS_PATH, headers=_headers())

    assert extra.status_code == 400
    assert foreign_origin.status_code == 400
    assert missing_origin.status_code == 400
    assert oversized.status_code == 413
    assert get_response.status_code == 405
    assert service.calls == []


def test_owner_can_prepare_and_read_bounded_history_without_exposing_provider_secret() -> None:
    service = RecordingActionService()
    service.receipts = (_receipt(service.prepared),)
    with TestClient(_app(service), base_url=BASE_URL) as client:
        prepared = client.post(
            ACTION_GATEWAY_PREPARE_PATH,
            json=_intent_payload(),
            headers=_headers(),
        )
        history = client.post(
            ACTION_GATEWAY_HISTORY_PATH,
            json={},
            headers=_headers(),
        )

    assert prepared.status_code == 200
    payload = prepared.json()
    assert payload["prepared"]["contract_version"] == "prepared-external-action-v1"
    assert payload["confirmation_token"] == "confirmation-token-for-page-memory"
    assert history.status_code == 200
    assert history.json()["count"] == 1
    assert "Authorization" not in history.text
    assert "session-secret" not in history.text
    assert prepared.headers["cache-control"] == "no-store, private"
    assert history.headers["cache-control"] == "no-store, private"
    assert service.calls == ["prepare:web-action-1", "confirm", "history"]


def test_owner_can_prepare_and_execute_compensation_with_parent_binding() -> None:
    service = RecordingActionService()
    parent_receipt_id = _receipt(service.prepared).receipt_id
    with TestClient(_app(service), base_url=BASE_URL) as client:
        prepared = client.post(
            ACTION_GATEWAY_COMPENSATION_PREPARE_PATH,
            json={
                "parent_receipt_id": str(parent_receipt_id),
                "operation_id": str(uuid7()),
            },
            headers=_headers(),
        )
        assert prepared.status_code == 200
        prepared_payload = prepared.json()
        assert prepared_payload["parent_receipt_id"] == str(parent_receipt_id)

        executed = client.post(
            ACTION_GATEWAY_EXECUTE_PATH,
            json={
                "prepared": prepared_payload["prepared"],
                "confirmation_token": prepared_payload["confirmation_token"],
                "parent_receipt_id": str(parent_receipt_id),
            },
            headers=_headers(),
        )

    assert executed.status_code == 200
    assert executed.json()["receipt"]["state"] == "executed"
    assert service.calls == [
        "compensation",
        "confirm",
        f"execute_compensation:{parent_receipt_id}",
    ]
