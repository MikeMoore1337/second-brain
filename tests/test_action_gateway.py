"""Adversarial coverage for the provider-neutral Stage 19 gateway core."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid7

import pytest

from second_brain.application.action_gateway import (
    ACTION_GATEWAY_CONNECTOR,
    ACTION_GATEWAY_CREDENTIAL_PROFILE_ID,
    ACTION_GATEWAY_POLICY_ID,
    ActionExecutionOutcomeV1,
    ActionGatewayConfirmationError,
    ActionGatewayConflictError,
    ActionGatewayConnectorError,
    ActionGatewayCoreV1,
    ActionGatewayInvalidRequestError,
    ActionGatewayTargetChangedError,
    ActionIntentV1,
    ActionKindV1,
    ActionProvenanceV1,
    ActionReceiptStateV1,
    ConfirmationCodecV1,
    ConnectorExecutionResultV1,
    ConnectorPreparedActionV1,
    ConnectorRevalidationV1,
    ExactTargetIdentityV1,
    IssueStateV1,
    PreparedExternalActionV1,
    ReversibilityV1,
    RiskClassV1,
)
from second_brain.application.action_gateway_store import ActionGatewayOperationalStore

NOW = datetime(2026, 9, 16, 21, 0, tzinfo=UTC)


def _intent(
    operation_id: str = "stage19-operation-1",
    *,
    action_kind: ActionKindV1 = ActionKindV1.GITHUB_ISSUE_CREATE,
    body: str = "Текст внешней задачи",
    desired_state: IssueStateV1 = IssueStateV1.OPEN,
    provenance: ActionProvenanceV1 | None = None,
) -> ActionIntentV1:
    if action_kind is ActionKindV1.GITHUB_ISSUE_CREATE:
        return ActionIntentV1(
            "action-intent-v1",
            operation_id,
            action_kind,
            ACTION_GATEWAY_CONNECTOR,
            "MikeMoore1337/second-brain",
            title="Новая задача",
            body=body,
            provenance=provenance,
        )
    if action_kind is ActionKindV1.GITHUB_ISSUE_COMMENT:
        return ActionIntentV1(
            "action-intent-v1",
            operation_id,
            action_kind,
            ACTION_GATEWAY_CONNECTOR,
            "MikeMoore1337/second-brain",
            issue_number=123,
            comment=body,
            provenance=provenance,
        )
    return ActionIntentV1(
        "action-intent-v1",
        operation_id,
        action_kind,
        ACTION_GATEWAY_CONNECTOR,
        "MikeMoore1337/second-brain",
        issue_number=123,
        desired_state=desired_state,
        provenance=provenance,
    )


class FakeConnector:
    connector_id = ACTION_GATEWAY_CONNECTOR
    policy_id = ACTION_GATEWAY_POLICY_ID
    credential_profile_id = ACTION_GATEWAY_CREDENTIAL_PROFILE_ID

    def __init__(self, *, current_state: IssueStateV1 = IssueStateV1.OPEN) -> None:
        self.current_state = current_state
        self.target_changed = False
        self.execute_calls = 0
        self.revalidate_calls = 0
        self.next_outcome: ActionExecutionOutcomeV1 | None = None

    def _target(self, intent: ActionIntentV1) -> ExactTargetIdentityV1:
        if intent.issue_number is None:
            return ExactTargetIdentityV1("MikeMoore1337/second-brain", 1, "R_repo")
        return ExactTargetIdentityV1(
            "MikeMoore1337/second-brain",
            1,
            "R_repo",
            issue_number=intent.issue_number,
            issue_id=2,
            issue_node_id="I_issue",
            current_state=self.current_state,
            locked=False,
        )

    def prepare(
        self,
        intent: ActionIntentV1,
        *,
        prepared_action_id: UUID,
        now: datetime,
    ) -> ConnectorPreparedActionV1:
        del now
        target = self._target(intent)
        marker = f"<!-- second-brain-action:{prepared_action_id} -->"
        if intent.action_kind is ActionKindV1.GITHUB_ISSUE_CREATE:
            payload: dict[str, object] = {
                "repository": intent.repository,
                "title": cast(str, intent.title),
                "body": f"{intent.body}\n{marker}",
                "marker": marker,
            }
        elif intent.action_kind is ActionKindV1.GITHUB_ISSUE_COMMENT:
            payload = {
                "repository": intent.repository,
                "issue_number": intent.issue_number,
                "comment": f"{intent.comment}\n{marker}",
                "marker": marker,
            }
        else:
            payload = {
                "repository": intent.repository,
                "issue_number": intent.issue_number,
                "desired_state": cast(IssueStateV1, intent.desired_state).value,
            }
        return ConnectorPreparedActionV1(
            target,
            "a" * 64,
            payload,
            "Предпросмотр действия GitHub\nВнешний побочный эффект: после подтверждения владельца.",
            ReversibilityV1.COMPENSATION_ONLY,
        )

    def revalidate(
        self,
        prepared: PreparedExternalActionV1,
        *,
        now: datetime,
    ) -> ConnectorRevalidationV1:
        del now
        self.revalidate_calls += 1
        if self.target_changed:
            if prepared.exact_target_identity.issue_number is None:
                target = ExactTargetIdentityV1("MikeMoore1337/second-brain", 2, "R_changed")
            else:
                target = ExactTargetIdentityV1(
                    "MikeMoore1337/second-brain",
                    1,
                    "R_repo",
                    issue_number=prepared.exact_target_identity.issue_number,
                    issue_id=2,
                    issue_node_id="I_changed",
                    current_state=prepared.exact_target_identity.current_state,
                    locked=False,
                )
        else:
            target = prepared.exact_target_identity
        return ConnectorRevalidationV1(target)

    def execute(
        self, prepared: PreparedExternalActionV1, *, now: datetime
    ) -> ConnectorExecutionResultV1:
        self.execute_calls += 1
        if self.next_outcome is not None:
            outcome = self.next_outcome
            self.next_outcome = None
            if outcome is ActionExecutionOutcomeV1.OUTCOME_UNCERTAIN:
                raise ActionGatewayConnectorError(outcome, "provider_outcome_uncertain")
        else:
            outcome = ActionExecutionOutcomeV1.EXECUTED
        return ConnectorExecutionResultV1(
            outcome,
            attempt_started_at=now,
            sent_at=now,
            finished_at=now,
            remote_safe_identity={"issue_id": 2, "issue_number": 123, "state": "open"},
            remote_url="https://github.com/MikeMoore1337/second-brain/issues/123",
        )


def _gateway(
    tmp_path: Path, connector: FakeConnector | None = None
) -> tuple[ActionGatewayCoreV1, FakeConnector, ActionGatewayOperationalStore]:
    selected = connector or FakeConnector()
    store = ActionGatewayOperationalStore(tmp_path / "action-gateway")
    return (
        ActionGatewayCoreV1(selected, store, confirmation=ConfirmationCodecV1(b"x" * 32)),
        selected,
        store,
    )


def test_intent_is_strict_and_provenance_is_pairwise() -> None:
    with pytest.raises(ActionGatewayInvalidRequestError):
        ActionIntentV1.from_dict(
            {
                "contract_version": "action-intent-v1",
                "operation_id": "op",
                "action_kind": "github.issue.create",
                "connector": "github_issues",
                "repository": "MikeMoore1337/second-brain",
                "title": "title",
                "body": "body",
                "unknown": "reject",
            }
        )
    with pytest.raises(ActionGatewayInvalidRequestError):
        ActionProvenanceV1(planning_snapshot_id=str(uuid7()))
    with pytest.raises(ActionGatewayInvalidRequestError):
        ActionIntentV1.from_json(
            '{"contract_version":"action-intent-v1","operation_id":"op",'
            '"action_kind":"github.issue.create","action_kind":"github.issue.comment",'
            '"connector":"github_issues","repository":"a/b","title":"t","body":"b"}'
        )


def test_prepare_is_non_mutating_and_marker_is_bound_to_immutable_action(tmp_path: Path) -> None:
    gateway, connector, store = _gateway(tmp_path)
    prepared = gateway.prepare(_intent(), now=NOW)
    assert connector.revalidate_calls == 0
    assert store.read_receipts() == ()
    marker = cast(str, prepared.semantic_payload["marker"])
    assert marker == f"<!-- second-brain-action:{prepared.prepared_action_id} -->"
    assert cast(str, prepared.semantic_payload["body"]).endswith(f"\n{marker}")
    assert prepared.risk is RiskClassV1.CONTROLLED_WRITE
    assert "Текст внешней задачи" not in prepared.preview


def test_confirmation_binds_action_and_rejects_tamper_expiry_and_replay(tmp_path: Path) -> None:
    gateway, _, _ = _gateway(tmp_path)
    prepared = gateway.prepare(_intent(), now=NOW)
    token = gateway.issue_confirmation(prepared, now=NOW)
    assert "Текст внешней задачи" not in token
    gateway._confirmation.verify(token, prepared, now=NOW + timedelta(seconds=299))
    with pytest.raises(ActionGatewayConfirmationError):
        gateway._confirmation.verify(token + "x", prepared, now=NOW)
    with pytest.raises(ActionGatewayConfirmationError):
        gateway._confirmation.verify(token, prepared, now=NOW + timedelta(seconds=300))
    gateway._confirmation.consume(token, prepared, now=NOW)
    with pytest.raises(ActionGatewayConfirmationError):
        gateway._confirmation.consume(token, prepared, now=NOW)


def test_execute_is_at_most_once_and_new_confirmation_replays_receipt(tmp_path: Path) -> None:
    gateway, connector, store = _gateway(tmp_path)
    prepared = gateway.prepare(_intent(), now=NOW)
    first_token = gateway.issue_confirmation(prepared, now=NOW)
    first = gateway.execute(prepared, first_token, now=NOW)
    second = gateway.execute(
        prepared,
        gateway.issue_confirmation(prepared, now=NOW + timedelta(seconds=1)),
        now=NOW + timedelta(seconds=1),
    )
    assert first.receipt.state is ActionReceiptStateV1.EXECUTED
    assert second.replayed is True
    assert second.receipt.receipt_id == first.receipt.receipt_id
    assert connector.execute_calls == 1
    assert len(store.read_receipts()) == 2
    with pytest.raises(ActionGatewayConfirmationError):
        gateway.execute(prepared, first_token, now=NOW)


def test_same_operation_different_intent_is_conflict_before_provider(tmp_path: Path) -> None:
    gateway, connector, _ = _gateway(tmp_path)
    first = gateway.prepare(_intent(), now=NOW)
    gateway.execute(first, gateway.issue_confirmation(first, now=NOW), now=NOW)
    changed = _intent(body="Другая задача")
    with pytest.raises(ActionGatewayConflictError):
        gateway.prepare(changed, now=NOW + timedelta(seconds=1))
    assert connector.execute_calls == 1


def test_target_drift_is_failed_before_send(tmp_path: Path) -> None:
    connector = FakeConnector()
    gateway, connector, store = _gateway(tmp_path, connector)
    prepared = gateway.prepare(_intent(), now=NOW)
    connector.target_changed = True
    result = gateway.execute(prepared, gateway.issue_confirmation(prepared, now=NOW), now=NOW)
    assert result.receipt.state is ActionReceiptStateV1.FAILED_BEFORE_SEND
    assert result.receipt.safe_error_code == ActionGatewayTargetChangedError.code
    assert connector.execute_calls == 0
    assert len(store.read_receipts()) == 1


def test_uncertain_outcome_is_terminal_and_never_retried(tmp_path: Path) -> None:
    gateway, connector, _ = _gateway(tmp_path)
    prepared = gateway.prepare(_intent(), now=NOW)
    connector.next_outcome = ActionExecutionOutcomeV1.OUTCOME_UNCERTAIN
    first = gateway.execute(prepared, gateway.issue_confirmation(prepared, now=NOW), now=NOW)
    second = gateway.execute(
        prepared,
        gateway.issue_confirmation(prepared, now=NOW + timedelta(seconds=1)),
        now=NOW + timedelta(seconds=1),
    )
    assert first.receipt.state is ActionReceiptStateV1.OUTCOME_UNCERTAIN
    assert second.receipt.state is ActionReceiptStateV1.OUTCOME_UNCERTAIN
    assert connector.execute_calls == 1


def test_set_state_already_satisfied_does_not_call_mutation(tmp_path: Path) -> None:
    connector = FakeConnector(current_state=IssueStateV1.CLOSED)
    gateway, connector, store = _gateway(tmp_path, connector)
    prepared = gateway.prepare(
        _intent(action_kind=ActionKindV1.GITHUB_ISSUE_SET_STATE, desired_state=IssueStateV1.CLOSED),
        now=NOW,
    )
    result = gateway.execute(prepared, gateway.issue_confirmation(prepared, now=NOW), now=NOW)
    assert result.receipt.state is ActionReceiptStateV1.ALREADY_SATISFIED
    assert connector.execute_calls == 0
    assert len(store.read_receipts()) == 1
