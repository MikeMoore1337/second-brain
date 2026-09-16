"""Application orchestration tests for Stage 19."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from second_brain.application.action_gateway import (
    ACTION_GATEWAY_CONNECTOR,
    ACTION_GATEWAY_CREDENTIAL_PROFILE_ID,
    ACTION_GATEWAY_POLICY_ID,
    ActionExecutionOutcomeV1,
    ActionGatewayConnectorError,
    ActionGatewayTargetChangedError,
    ActionIntentV1,
    ActionKindV1,
    ActionReceiptKindV1,
    ActionReceiptStateV1,
    ConfirmationCodecV1,
    ConnectorExecutionResultV1,
    ConnectorPreparedActionV1,
    ConnectorReconciliationResultV1,
    ConnectorRevalidationV1,
    ExactTargetIdentityV1,
    IssueStateV1,
    PreparedExternalActionV1,
    ReversibilityV1,
)
from second_brain.application.action_gateway_orchestration import (
    ActionGatewayCompensationError,
    ActionGatewayOrchestratorV1,
    ProductionActionGatewayServiceV1,
)
from second_brain.application.action_gateway_store import ActionGatewayOperationalStore

NOW = datetime(2026, 9, 16, 22, 0, tzinfo=UTC)


def _intent(
    operation_id: str,
    action_kind: ActionKindV1 = ActionKindV1.GITHUB_ISSUE_CREATE,
    *,
    desired_state: IssueStateV1 = IssueStateV1.OPEN,
) -> ActionIntentV1:
    if action_kind is ActionKindV1.GITHUB_ISSUE_CREATE:
        return ActionIntentV1(
            "action-intent-v1",
            operation_id,
            action_kind,
            ACTION_GATEWAY_CONNECTOR,
            "MikeMoore1337/second-brain",
            title="Внешняя задача",
            body="Текст задачи",
        )
    if action_kind is ActionKindV1.GITHUB_ISSUE_COMMENT:
        return ActionIntentV1(
            "action-intent-v1",
            operation_id,
            action_kind,
            ACTION_GATEWAY_CONNECTOR,
            "MikeMoore1337/second-brain",
            issue_number=123,
            comment="Комментарий",
        )
    return ActionIntentV1(
        "action-intent-v1",
        operation_id,
        action_kind,
        ACTION_GATEWAY_CONNECTOR,
        "MikeMoore1337/second-brain",
        issue_number=123,
        desired_state=desired_state,
    )


class OrchestrationConnector:
    connector_id = ACTION_GATEWAY_CONNECTOR
    policy_id = ACTION_GATEWAY_POLICY_ID
    credential_profile_id = ACTION_GATEWAY_CREDENTIAL_PROFILE_ID

    def __init__(self, *, current_state: IssueStateV1 = IssueStateV1.OPEN) -> None:
        self.current_state = current_state
        self.execute_calls = 0
        self.reconcile_calls = 0
        self.uncertain = False
        self.reconciliation = ConnectorReconciliationResultV1(
            ActionReceiptStateV1.RECONCILED_NOT_EXECUTED,
            NOW,
        )

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
            reversibility = ReversibilityV1.COMPENSATION_ONLY
        elif intent.action_kind is ActionKindV1.GITHUB_ISSUE_COMMENT:
            payload = {
                "repository": intent.repository,
                "issue_number": intent.issue_number,
                "comment": f"{intent.comment}\n{marker}",
                "marker": marker,
            }
            reversibility = ReversibilityV1.NOT_SUPPORTED
        else:
            payload = {
                "repository": intent.repository,
                "issue_number": intent.issue_number,
                "desired_state": cast(IssueStateV1, intent.desired_state).value,
            }
            reversibility = ReversibilityV1.SUPPORTED
        return ConnectorPreparedActionV1(
            target,
            "a" * 64,
            payload,
            "Предпросмотр действия; изменение возможно только после подтверждения владельца.",
            reversibility,
        )

    def revalidate(
        self,
        prepared: PreparedExternalActionV1,
        *,
        now: datetime,
    ) -> ConnectorRevalidationV1:
        del now
        return ConnectorRevalidationV1(prepared.exact_target_identity)

    def execute(
        self,
        prepared: PreparedExternalActionV1,
        *,
        now: datetime,
    ) -> ConnectorExecutionResultV1:
        self.execute_calls += 1
        if self.uncertain:
            raise ActionGatewayConnectorError(
                ActionExecutionOutcomeV1.OUTCOME_UNCERTAIN,
                "provider_outcome_uncertain",
            )
        if prepared.action_kind is ActionKindV1.GITHUB_ISSUE_SET_STATE:
            desired = cast(str, prepared.semantic_payload["desired_state"])
            self.current_state = IssueStateV1(desired)
            state = desired
        else:
            state = IssueStateV1.OPEN.value
        return ConnectorExecutionResultV1(
            ActionExecutionOutcomeV1.EXECUTED,
            attempt_started_at=now,
            sent_at=now,
            finished_at=now,
            remote_safe_identity={
                "repository": "MikeMoore1337/second-brain",
                "issue_id": 2,
                "issue_node_id": "I_issue",
                "issue_number": 123,
                "state": state,
                "url": "https://github.com/MikeMoore1337/second-brain/issues/123",
            },
            remote_url="https://github.com/MikeMoore1337/second-brain/issues/123",
        )

    def reconcile(
        self,
        prepared: PreparedExternalActionV1,
        *,
        now: datetime,
    ) -> ConnectorReconciliationResultV1:
        del prepared, now
        self.reconcile_calls += 1
        return self.reconciliation


def _orchestrator(
    tmp_path: Path,
    connector: OrchestrationConnector | None = None,
) -> tuple[ActionGatewayOrchestratorV1, OrchestrationConnector]:
    selected = connector or OrchestrationConnector()
    store = ActionGatewayOperationalStore(tmp_path / "action-gateway")
    return (
        ActionGatewayOrchestratorV1(
            selected,
            store,
            confirmation=ConfirmationCodecV1(b"o" * 32),
            clock=lambda: NOW,
        ),
        selected,
    )


def test_reconcile_is_explicit_read_only_and_replays_safe_result(tmp_path: Path) -> None:
    gateway, connector = _orchestrator(tmp_path)
    connector.uncertain = True
    prepared = gateway.prepare(_intent("uncertain-create"), now=NOW)
    executed = gateway.execute(
        prepared,
        gateway.issue_confirmation(prepared, now=NOW),
        now=NOW,
    )
    assert executed.receipt.state is ActionReceiptStateV1.OUTCOME_UNCERTAIN
    connector.reconciliation = ConnectorReconciliationResultV1(
        ActionReceiptStateV1.RECONCILED_EXECUTED,
        NOW,
        remote_safe_identity={
            "repository": "MikeMoore1337/second-brain",
            "issue_id": 2,
            "issue_node_id": "I_issue",
            "issue_number": 123,
            "state": "open",
            "url": "https://github.com/MikeMoore1337/second-brain/issues/123",
        },
        remote_url="https://github.com/MikeMoore1337/second-brain/issues/123",
    )
    reconciled = gateway.reconcile(prepared, now=NOW)
    replayed = gateway.reconcile(prepared, now=NOW)
    assert reconciled.receipt.state is ActionReceiptStateV1.RECONCILED_EXECUTED
    assert reconciled.replayed is False
    assert replayed.replayed is True
    assert connector.execute_calls == 1
    assert connector.reconcile_calls == 1


def test_reconcile_promotes_durable_started_record_without_confirmation_or_mutation(
    tmp_path: Path,
) -> None:
    gateway, connector = _orchestrator(tmp_path)
    prepared = gateway.prepare(_intent("crashed-create"), now=NOW)
    started, created = gateway._store.begin_execution(prepared, now=NOW)
    assert created is True
    connector.reconciliation = ConnectorReconciliationResultV1(
        ActionReceiptStateV1.RECONCILED_NOT_EXECUTED,
        NOW,
    )

    result = gateway.reconcile(prepared, now=NOW)

    assert started.state is ActionReceiptStateV1.EXECUTION_STARTED
    assert result.receipt.state is ActionReceiptStateV1.RECONCILED_NOT_EXECUTED
    assert connector.execute_calls == 0
    assert connector.reconcile_calls == 1


def test_ambiguous_reconciliation_can_be_rechecked_but_never_mutates(tmp_path: Path) -> None:
    gateway, connector = _orchestrator(tmp_path)
    connector.uncertain = True
    connector.reconciliation = ConnectorReconciliationResultV1(
        ActionReceiptStateV1.RECONCILIATION_AMBIGUOUS,
        NOW,
        safe_error_code="reconciliation_ambiguous",
    )
    prepared = gateway.prepare(_intent("ambiguous-create"), now=NOW)
    gateway.execute(
        prepared,
        gateway.issue_confirmation(prepared, now=NOW),
        now=NOW,
    )
    ambiguous = gateway.reconcile(prepared, now=NOW)
    assert ambiguous.receipt.state is ActionReceiptStateV1.RECONCILIATION_AMBIGUOUS
    connector.reconciliation = ConnectorReconciliationResultV1(
        ActionReceiptStateV1.RECONCILED_EXECUTED,
        NOW,
        remote_safe_identity={
            "repository": "MikeMoore1337/second-brain",
            "issue_id": 2,
            "issue_node_id": "I_issue",
            "issue_number": 123,
            "state": "open",
            "url": "https://github.com/MikeMoore1337/second-brain/issues/123",
        },
        remote_url="https://github.com/MikeMoore1337/second-brain/issues/123",
    )
    second = gateway.reconcile(prepared, now=NOW)
    assert second.receipt.state is ActionReceiptStateV1.RECONCILED_EXECUTED
    assert connector.execute_calls == 1
    assert connector.reconcile_calls == 2


def test_compensation_is_new_prepared_confirmed_operation(tmp_path: Path) -> None:
    connector = OrchestrationConnector(current_state=IssueStateV1.CLOSED)
    gateway, connector = _orchestrator(tmp_path, connector)
    prepared = gateway.prepare(
        _intent(
            "state-operation",
            ActionKindV1.GITHUB_ISSUE_SET_STATE,
            desired_state=IssueStateV1.OPEN,
        ),
        now=NOW,
    )
    original = gateway.execute(
        prepared,
        gateway.issue_confirmation(prepared, now=NOW),
        now=NOW,
    )
    compensation = gateway.prepare_compensation(
        original.receipt.receipt_id,
        "state-compensation",
        now=NOW,
    )
    assert compensation.semantic_payload["desired_state"] == IssueStateV1.CLOSED.value
    result = gateway.execute_compensation(
        compensation,
        gateway.issue_confirmation(compensation, now=NOW),
        original.receipt.receipt_id,
        now=NOW,
    )
    assert result.receipt.receipt_kind is ActionReceiptKindV1.COMPENSATION
    assert result.receipt.parent_receipt_id == original.receipt.receipt_id
    assert connector.execute_calls == 2
    compensation_started = gateway.history()[-2]
    assert compensation_started.receipt_kind is ActionReceiptKindV1.COMPENSATION
    assert compensation_started.parent_receipt_id == original.receipt.receipt_id


def test_comment_compensation_and_changed_compensation_target_fail_closed(
    tmp_path: Path,
) -> None:
    gateway, connector = _orchestrator(tmp_path)
    comment = gateway.prepare(
        _intent("comment-operation", ActionKindV1.GITHUB_ISSUE_COMMENT),
        now=NOW,
    )
    connector.uncertain = False
    original = gateway.execute(
        comment,
        gateway.issue_confirmation(comment, now=NOW),
        now=NOW,
    )
    with pytest.raises(ActionGatewayCompensationError):
        gateway.prepare_compensation(original.receipt.receipt_id, "comment-compensation", now=NOW)

    connector = OrchestrationConnector(current_state=IssueStateV1.CLOSED)
    gateway, connector = _orchestrator(tmp_path / "changed", connector)
    state = gateway.prepare(
        _intent(
            "changed-parent",
            ActionKindV1.GITHUB_ISSUE_SET_STATE,
            desired_state=IssueStateV1.OPEN,
        ),
        now=NOW,
    )
    original = gateway.execute(
        state,
        gateway.issue_confirmation(state, now=NOW),
        now=NOW,
    )
    connector.current_state = IssueStateV1.CLOSED
    with pytest.raises(ActionGatewayTargetChangedError):
        gateway.prepare_compensation(original.receipt.receipt_id, "changed-compensation", now=NOW)


def test_disabled_production_status_is_safe_without_store_or_token(tmp_path: Path) -> None:
    env_file = tmp_path / "web.env"
    env_file.write_text("SECOND_BRAIN_ACTION_GITHUB_ENABLED=false\n", encoding="utf-8")
    service = ProductionActionGatewayServiceV1(env_file=env_file)
    status = service.status().as_dict()
    assert status["status"] == "disabled"
    assert status["ready"] is False
    assert status["repositories"] == []
    assert "token" not in status
