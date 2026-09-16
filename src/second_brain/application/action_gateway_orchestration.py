"""Owner-controlled orchestration for the Stage 19 action gateway.

The module is the application seam between private Web/API handlers, the
provider-neutral gateway core, the fixed GitHub Issues connector, and the
append-only operational receipt store.  It deliberately keeps prepared
actions in caller memory, reloads the provider configuration for every
provider-facing request, and never turns reconciliation into a retry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Final, cast
from uuid import UUID, uuid7

from second_brain.adapters.actions.github_issues import (
    GitHubActionConfigStatusV1,
    GitHubIssuesActionConnectorV1,
    load_github_action_config,
)
from second_brain.application.action_gateway import (
    ACTION_GATEWAY_CONNECTOR,
    ACTION_GATEWAY_CREDENTIAL_PROFILE_ID,
    ACTION_GATEWAY_POLICY_ID,
    ActionConnectorV1,
    ActionExecutionOutcomeV1,
    ActionExecutionResultV1,
    ActionGatewayConflictError,
    ActionGatewayConnectorError,
    ActionGatewayCoreV1,
    ActionGatewayError,
    ActionGatewayInvalidRequestError,
    ActionGatewayTargetChangedError,
    ActionIntentV1,
    ActionKindV1,
    ActionReceiptKindV1,
    ActionReceiptStateV1,
    ActionReceiptStoreV1,
    ActionReceiptV1,
    ConfirmationCodecV1,
    ConnectorReconciliationResultV1,
    ExactTargetIdentityV1,
    IssueStateV1,
    PreparedExternalActionV1,
    ReversibilityV1,
    RiskClassV1,
)
from second_brain.application.action_gateway_store import (
    ActionGatewayOperationalStore,
    ActionGatewayStoreError,
    derive_action_gateway_store_root,
)
from second_brain.config import ConfigurationError, load_config

ACTION_GATEWAY_CATALOG_V1: Final[tuple[dict[str, str], ...]] = (
    {
        "action_kind": ActionKindV1.GITHUB_ISSUE_CREATE.value,
        "risk": RiskClassV1.CONTROLLED_WRITE.value,
        "reversibility": ReversibilityV1.COMPENSATION_ONLY.value,
    },
    {
        "action_kind": ActionKindV1.GITHUB_ISSUE_COMMENT.value,
        "risk": RiskClassV1.CONTROLLED_WRITE.value,
        "reversibility": ReversibilityV1.NOT_SUPPORTED.value,
    },
    {
        "action_kind": ActionKindV1.GITHUB_ISSUE_SET_STATE.value,
        "risk": RiskClassV1.CONTROLLED_WRITE.value,
        "reversibility": ReversibilityV1.SUPPORTED.value,
    },
)


class ActionGatewayOrchestrationError(ActionGatewayError):
    """Safe application-level failure at the Stage 19 orchestration seam."""

    code = "action_gateway_orchestration_error"


class ActionGatewayOperationNotFoundError(ActionGatewayOrchestrationError):
    """The requested receipt identity is not in the verified operational store."""

    code = "operation_not_found"


class ActionGatewayReconciliationUnavailableError(ActionGatewayOrchestrationError):
    """The requested uncertain action cannot be reconciled safely."""

    code = "reconciliation_unavailable"


class ActionGatewayCompensationError(ActionGatewayOrchestrationError):
    """Compensation is not supported or its exact parent relation is invalid."""

    code = "compensation_not_supported"


class ActionGatewayRuntimeUnavailableError(ActionGatewayOrchestrationError):
    """The production connector/store runtime is unavailable."""

    code = "runtime_unavailable"


def _now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    return _utc_time(value)


def _utc_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ActionGatewayInvalidRequestError()
    return value.astimezone(UTC)


def _receipt_kind(value: object) -> ActionReceiptKindV1:
    if isinstance(value, ActionReceiptKindV1):
        return value
    try:
        return ActionReceiptKindV1(cast(str, value))
    except (TypeError, ValueError) as exc:
        raise ActionGatewayInvalidRequestError() from exc


def _receipt_state(value: object) -> ActionReceiptStateV1:
    if isinstance(value, ActionReceiptStateV1):
        return value
    try:
        return ActionReceiptStateV1(cast(str, value))
    except (TypeError, ValueError) as exc:
        raise ActionGatewayInvalidRequestError() from exc


def _action_kind(value: object) -> ActionKindV1:
    if isinstance(value, ActionKindV1):
        return value
    try:
        return ActionKindV1(cast(str, value))
    except (TypeError, ValueError) as exc:
        raise ActionGatewayInvalidRequestError() from exc


def _positive_int(value: object) -> int:
    if type(value) is not int or isinstance(value, bool) or value <= 0:
        raise ActionGatewayCompensationError()
    return value


def _repository(value: object) -> str:
    if type(value) is not str or value.count("/") != 1 or value != value.strip():
        raise ActionGatewayCompensationError()
    owner, name = value.split("/")
    if not owner or not name:
        raise ActionGatewayCompensationError()
    return value


@dataclass(frozen=True, slots=True)
class ActionGatewayStatusV1:
    """Safe readiness and closed catalog projection for the owner UI."""

    config_status: GitHubActionConfigStatusV1 | str
    configured: bool
    repositories: tuple[str, ...]
    store_status: str = "not_checked"

    def __post_init__(self) -> None:
        try:
            status = (
                self.config_status
                if isinstance(self.config_status, GitHubActionConfigStatusV1)
                else GitHubActionConfigStatusV1(self.config_status)
            )
        except (TypeError, ValueError) as exc:
            raise ActionGatewayInvalidRequestError() from exc
        if type(self.configured) is not bool or type(self.repositories) is not tuple:
            raise ActionGatewayInvalidRequestError()
        repositories = tuple(item for item in self.repositories if type(item) is str)
        if len(repositories) != len(self.repositories):
            raise ActionGatewayInvalidRequestError()
        if type(self.store_status) is not str or not self.store_status:
            raise ActionGatewayInvalidRequestError()
        object.__setattr__(self, "config_status", status)
        object.__setattr__(self, "repositories", repositories)

    @property
    def ready(self) -> bool:
        return self.config_status is GitHubActionConfigStatusV1.READY

    def as_dict(self) -> dict[str, object]:
        return {
            "contract": "action-gateway-v1",
            "connector": ACTION_GATEWAY_CONNECTOR,
            "policy_id": ACTION_GATEWAY_POLICY_ID,
            "credential_profile_id": ACTION_GATEWAY_CREDENTIAL_PROFILE_ID,
            "status": cast(GitHubActionConfigStatusV1, self.config_status).value,
            "configured": self.configured,
            "ready": self.ready,
            "repositories": list(self.repositories),
            "store_status": self.store_status,
            "action_catalog": [dict(item) for item in ACTION_GATEWAY_CATALOG_V1],
            "owner_confirmation_required": True,
            "background_execution": False,
        }


class ActionGatewayOrchestratorV1:
    """Compose prepare, confirmation, execution, reconciliation and compensation."""

    def __init__(
        self,
        connector: ActionConnectorV1,
        store: ActionReceiptStoreV1,
        *,
        confirmation: ConfirmationCodecV1 | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._clock = clock
        self._connector = connector
        self._core = ActionGatewayCoreV1(
            connector,
            store,
            confirmation=confirmation,
        )

    def prepare(
        self, intent: ActionIntentV1, *, now: datetime | None = None
    ) -> PreparedExternalActionV1:
        return self._core.prepare(intent, now=_now(self._clock) if now is None else _utc_time(now))

    def issue_confirmation(
        self, prepared: PreparedExternalActionV1, *, now: datetime | None = None
    ) -> str:
        return self._core.issue_confirmation(
            prepared,
            now=_now(self._clock) if now is None else _utc_time(now),
        )

    def execute(
        self,
        prepared: PreparedExternalActionV1,
        confirmation: object,
        *,
        now: datetime | None = None,
    ) -> ActionExecutionResultV1:
        return self._core.execute(
            prepared,
            confirmation,
            now=_now(self._clock) if now is None else _utc_time(now),
        )

    def history(self) -> tuple[ActionReceiptV1, ...]:
        return self._store.read_receipts()

    def reconcile(
        self,
        prepared: PreparedExternalActionV1,
        *,
        now: datetime | None = None,
    ) -> ActionExecutionResultV1:
        """Reconcile one uncertain action with read-only provider evidence."""

        if type(prepared) is not PreparedExternalActionV1:
            raise ActionGatewayInvalidRequestError()
        current = _now(self._clock) if now is None else _utc_time(now)
        receipts = self._store.read_receipts()
        action = self._find_action_receipt(receipts, prepared)
        if action is None:
            raise ActionGatewayOperationNotFoundError()
        self._assert_prepared_matches_receipt(prepared, action)
        state = _receipt_state(action.state)
        if state is ActionReceiptStateV1.EXECUTION_STARTED:
            action = self._started_to_uncertain(action, finished_at=current)
            receipts = (*receipts, action)
            state = ActionReceiptStateV1.OUTCOME_UNCERTAIN
        if state in {
            ActionReceiptStateV1.EXECUTED,
            ActionReceiptStateV1.ALREADY_SATISFIED,
        }:
            return ActionExecutionResultV1(action, True)
        if state is not ActionReceiptStateV1.OUTCOME_UNCERTAIN:
            raise ActionGatewayReconciliationUnavailableError()

        prior = self._latest_reconciliation(receipts, action.receipt_id)
        if prior is not None and _receipt_state(prior.state) in {
            ActionReceiptStateV1.RECONCILED_EXECUTED,
            ActionReceiptStateV1.RECONCILED_NOT_EXECUTED,
        }:
            return ActionExecutionResultV1(prior, True)

        reconcile_method = getattr(self._connector, "reconcile", None)
        if not callable(reconcile_method):
            raise ActionGatewayReconciliationUnavailableError()
        try:
            outcome = reconcile_method(prepared, now=current)
        except ActionGatewayConnectorError:
            outcome = ConnectorReconciliationResultV1(
                ActionReceiptStateV1.RECONCILIATION_AMBIGUOUS,
                current,
                safe_error_code="reconciliation_unavailable",
            )
        except ActionGatewayError:
            raise
        except Exception:
            outcome = ConnectorReconciliationResultV1(
                ActionReceiptStateV1.RECONCILIATION_AMBIGUOUS,
                current,
                safe_error_code="reconciliation_unavailable",
            )
        if type(outcome) is not ConnectorReconciliationResultV1:
            raise ActionGatewayReconciliationUnavailableError()
        receipt = ActionReceiptV1(
            receipt_id=uuid7(),
            receipt_kind=ActionReceiptKindV1.RECONCILIATION,
            operation_id_fingerprint=prepared.operation_id_fingerprint,
            prepared_action_id=prepared.prepared_action_id,
            intent_fingerprint=prepared.intent_fingerprint,
            action_kind=prepared.action_kind,
            risk=prepared.risk,
            connector_policy_id=prepared.connector_policy_id,
            credential_profile_id=prepared.credential_profile_id,
            target_safe_identity=prepared.exact_target_identity.safe_identity(),
            payload_fingerprint=prepared.payload_fingerprint,
            state=outcome.state,
            finished_at=outcome.finished_at,
            remote_safe_identity=outcome.remote_safe_identity,
            remote_url=outcome.remote_url,
            safe_error_code=outcome.safe_error_code,
            parent_receipt_id=action.receipt_id,
        )
        return ActionExecutionResultV1(self._store.append(receipt), False)

    def prepare_compensation(
        self,
        parent_receipt_id: UUID,
        operation_id: str,
        *,
        now: datetime | None = None,
    ) -> PreparedExternalActionV1:
        """Build a new exact intent for an owner-requested compensation."""

        parent = self._receipt_by_id(parent_receipt_id)
        self._assert_compensable_parent(parent)
        action_kind = _action_kind(parent.action_kind)
        remote = parent.remote_safe_identity or {}
        if action_kind is ActionKindV1.GITHUB_ISSUE_CREATE:
            repository = _repository(remote.get("repository"))
            issue_number = _positive_int(remote.get("issue_number"))
            intent = ActionIntentV1(
                "action-intent-v1",
                operation_id,
                ActionKindV1.GITHUB_ISSUE_SET_STATE,
                ACTION_GATEWAY_CONNECTOR,
                repository,
                issue_number=issue_number,
                desired_state=IssueStateV1.CLOSED,
            )
        elif action_kind is ActionKindV1.GITHUB_ISSUE_SET_STATE:
            target = self._parent_target(parent)
            intent = ActionIntentV1(
                "action-intent-v1",
                operation_id,
                ActionKindV1.GITHUB_ISSUE_SET_STATE,
                ACTION_GATEWAY_CONNECTOR,
                target.repository,
                issue_number=cast(int, target.issue_number),
                desired_state=cast(IssueStateV1, target.current_state),
            )
        else:
            raise ActionGatewayCompensationError()
        if intent.operation_id_fingerprint == parent.operation_id_fingerprint:
            raise ActionGatewayCompensationError()
        prepared = self.prepare(intent, now=now)
        self._assert_compensation_target(parent, prepared)
        return prepared

    def execute_compensation(
        self,
        prepared: PreparedExternalActionV1,
        confirmation: object,
        parent_receipt_id: UUID,
        *,
        now: datetime | None = None,
    ) -> ActionExecutionResultV1:
        """Execute a previously prepared compensation as a distinct receipt."""

        parent = self._receipt_by_id(parent_receipt_id)
        self._assert_compensable_parent(parent)
        self._assert_compensation_target(parent, prepared)
        if prepared.operation_id_fingerprint == parent.operation_id_fingerprint:
            raise ActionGatewayCompensationError()
        return self._core.execute(
            prepared,
            confirmation,
            now=_now(self._clock) if now is None else _utc_time(now),
            receipt_kind=ActionReceiptKindV1.COMPENSATION,
            parent_receipt_id=parent.receipt_id,
        )

    @staticmethod
    def _find_action_receipt(
        receipts: tuple[ActionReceiptV1, ...], prepared: PreparedExternalActionV1
    ) -> ActionReceiptV1 | None:
        matches = tuple(
            receipt
            for receipt in receipts
            if receipt.operation_id_fingerprint == prepared.operation_id_fingerprint
            and _receipt_kind(receipt.receipt_kind) is ActionReceiptKindV1.ACTION
        )
        return matches[-1] if matches else None

    @staticmethod
    def _latest_reconciliation(
        receipts: tuple[ActionReceiptV1, ...], parent_receipt_id: UUID
    ) -> ActionReceiptV1 | None:
        matches = tuple(
            receipt
            for receipt in receipts
            if _receipt_kind(receipt.receipt_kind) is ActionReceiptKindV1.RECONCILIATION
            and receipt.parent_receipt_id == parent_receipt_id
        )
        return matches[-1] if matches else None

    @staticmethod
    def _assert_prepared_matches_receipt(
        prepared: PreparedExternalActionV1, receipt: ActionReceiptV1
    ) -> None:
        if (
            receipt.intent_fingerprint != prepared.intent_fingerprint
            or _action_kind(receipt.action_kind) is not _action_kind(prepared.action_kind)
            or receipt.payload_fingerprint != prepared.payload_fingerprint
            or receipt.target_safe_identity != prepared.exact_target_identity.safe_identity()
        ):
            raise ActionGatewayConflictError()

    def _receipt_by_id(self, receipt_id: UUID) -> ActionReceiptV1:
        if type(receipt_id) is not UUID:
            raise ActionGatewayInvalidRequestError()
        match = next(
            (item for item in self._store.read_receipts() if item.receipt_id == receipt_id),
            None,
        )
        if match is None:
            raise ActionGatewayOperationNotFoundError()
        return match

    def _started_to_uncertain(
        self,
        receipt: ActionReceiptV1,
        *,
        finished_at: datetime,
    ) -> ActionReceiptV1:
        return self._store.append(
            ActionReceiptV1(
                receipt_id=uuid7(),
                receipt_kind=ActionReceiptKindV1.ACTION,
                operation_id_fingerprint=receipt.operation_id_fingerprint,
                prepared_action_id=receipt.prepared_action_id,
                intent_fingerprint=receipt.intent_fingerprint,
                action_kind=receipt.action_kind,
                risk=receipt.risk,
                connector_policy_id=receipt.connector_policy_id,
                credential_profile_id=receipt.credential_profile_id,
                target_safe_identity=receipt.target_safe_identity,
                payload_fingerprint=receipt.payload_fingerprint,
                state=ActionReceiptStateV1.OUTCOME_UNCERTAIN,
                attempt_started_at=receipt.attempt_started_at,
                sent_at=receipt.sent_at,
                finished_at=finished_at,
                safe_error_code="execution_started_without_terminal_receipt",
                parent_receipt_id=receipt.receipt_id,
            )
        )

    @staticmethod
    def _assert_compensable_parent(parent: ActionReceiptV1) -> None:
        if _receipt_state(parent.state) not in {
            ActionReceiptStateV1.EXECUTED,
            ActionReceiptStateV1.RECONCILED_EXECUTED,
        }:
            raise ActionGatewayCompensationError()
        if _action_kind(parent.action_kind) is ActionKindV1.GITHUB_ISSUE_COMMENT:
            raise ActionGatewayCompensationError()

    @staticmethod
    def _parent_target(parent: ActionReceiptV1) -> ExactTargetIdentityV1:
        try:
            target = ExactTargetIdentityV1.from_dict(parent.target_safe_identity)
        except ActionGatewayError as exc:
            raise ActionGatewayCompensationError() from exc
        if target.issue_number is None or target.current_state is None:
            raise ActionGatewayCompensationError()
        return target

    @staticmethod
    def _assert_compensation_target(
        parent: ActionReceiptV1, prepared: PreparedExternalActionV1
    ) -> None:
        if _action_kind(prepared.action_kind) is not ActionKindV1.GITHUB_ISSUE_SET_STATE:
            raise ActionGatewayCompensationError()
        target = prepared.exact_target_identity
        desired_raw = prepared.semantic_payload.get("desired_state")
        try:
            desired = (
                desired_raw
                if isinstance(desired_raw, IssueStateV1)
                else IssueStateV1(cast(str, desired_raw))
            )
        except (TypeError, ValueError) as exc:
            raise ActionGatewayCompensationError() from exc
        parent_kind = _action_kind(parent.action_kind)
        if parent_kind is ActionKindV1.GITHUB_ISSUE_CREATE:
            remote = parent.remote_safe_identity or {}
            remote_state = remote.get("state")
            if (
                _repository(remote.get("repository")) != target.repository
                or _positive_int(remote.get("issue_number")) != target.issue_number
                or target.issue_id != remote.get("issue_id")
                or target.issue_node_id != remote.get("issue_node_id")
                or remote_state not in {IssueStateV1.OPEN.value, IssueStateV1.CLOSED.value}
                or target.current_state != remote_state
                or desired != IssueStateV1.CLOSED
            ):
                raise ActionGatewayTargetChangedError()
            return
        parent_target = ActionGatewayOrchestratorV1._parent_target(parent)
        inverse = cast(IssueStateV1, parent_target.current_state)
        remote_state = (parent.remote_safe_identity or {}).get("state")
        expected_state = (
            remote_state
            if remote_state in {IssueStateV1.OPEN.value, IssueStateV1.CLOSED.value}
            else inverse.value
        )
        same_static_target = (
            target.repository == parent_target.repository
            and target.repository_id == parent_target.repository_id
            and target.repository_node_id == parent_target.repository_node_id
            and target.issue_number == parent_target.issue_number
            and target.issue_id == parent_target.issue_id
            and target.issue_node_id == parent_target.issue_node_id
            and target.locked == parent_target.locked
        )
        if target.current_state != expected_state or not same_static_target or desired != inverse:
            raise ActionGatewayTargetChangedError()


class ProductionActionGatewayServiceV1:
    """Lazy production composition with per-request credential/config reload."""

    def __init__(
        self,
        *,
        env_file: Path | None = None,
        vault_path_override: str | None = None,
        connector: ActionConnectorV1 | None = None,
        store: ActionReceiptStoreV1 | None = None,
        confirmation: ConfirmationCodecV1 | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.env_file = env_file
        self.vault_path_override = vault_path_override
        self._injected_connector = connector
        self._store: ActionReceiptStoreV1 | None = store
        self._confirmation = confirmation or ConfirmationCodecV1()
        self._clock = clock
        self._store_lock = Lock()

    def status(self) -> ActionGatewayStatusV1:
        if self._injected_connector is not None:
            store_status = "ready" if self._store is not None else "unavailable"
            return ActionGatewayStatusV1(
                GitHubActionConfigStatusV1.READY,
                True,
                (),
                store_status,
            )
        config = load_github_action_config(env_file=self.env_file)
        return ActionGatewayStatusV1(
            config.status,
            config.enabled,
            config.repositories,
            "not_checked",
        )

    def prepare(
        self, intent: ActionIntentV1, *, now: datetime | None = None
    ) -> PreparedExternalActionV1:
        return self._orchestrator().prepare(intent, now=now)

    def issue_confirmation(
        self, prepared: PreparedExternalActionV1, *, now: datetime | None = None
    ) -> str:
        return self._orchestrator().issue_confirmation(prepared, now=now)

    def execute(
        self,
        prepared: PreparedExternalActionV1,
        confirmation: object,
        *,
        now: datetime | None = None,
    ) -> ActionExecutionResultV1:
        return self._orchestrator().execute(prepared, confirmation, now=now)

    def reconcile(
        self,
        prepared: PreparedExternalActionV1,
        *,
        now: datetime | None = None,
    ) -> ActionExecutionResultV1:
        return self._orchestrator().reconcile(prepared, now=now)

    def prepare_compensation(
        self,
        parent_receipt_id: UUID,
        operation_id: str,
        *,
        now: datetime | None = None,
    ) -> PreparedExternalActionV1:
        return self._orchestrator().prepare_compensation(
            parent_receipt_id,
            operation_id,
            now=now,
        )

    def execute_compensation(
        self,
        prepared: PreparedExternalActionV1,
        confirmation: object,
        parent_receipt_id: UUID,
        *,
        now: datetime | None = None,
    ) -> ActionExecutionResultV1:
        return self._orchestrator().execute_compensation(
            prepared,
            confirmation,
            parent_receipt_id,
            now=now,
        )

    def history(self) -> tuple[ActionReceiptV1, ...]:
        return self._get_store().read_receipts()

    def _orchestrator(self) -> ActionGatewayOrchestratorV1:
        if self._injected_connector is not None:
            store = self._store
            if store is None:
                raise ActionGatewayRuntimeUnavailableError()
            return ActionGatewayOrchestratorV1(
                self._injected_connector,
                store,
                confirmation=self._confirmation,
                clock=self._clock,
            )
        config = load_github_action_config(env_file=self.env_file)
        if not config.ready:
            code = (
                "connector_disabled"
                if config.status is GitHubActionConfigStatusV1.DISABLED
                else "credential_unavailable"
            )
            raise ActionGatewayConnectorError(ActionExecutionOutcomeV1.FAILED_BEFORE_SEND, code)
        return ActionGatewayOrchestratorV1(
            GitHubIssuesActionConnectorV1(config),
            self._get_store(),
            confirmation=self._confirmation,
            clock=self._clock,
        )

    def _get_store(self) -> ActionReceiptStoreV1:
        store = self._store
        if store is not None:
            return store
        with self._store_lock:
            store = self._store
            if store is not None:
                return store
            root = derive_action_gateway_store_root(self.env_file)
            if root is None:
                raise ActionGatewayRuntimeUnavailableError()
            try:
                config = load_config(
                    env_file=self.env_file,
                    vault_path_override=self.vault_path_override,
                )
                store = ActionGatewayOperationalStore(root, vault_root=config.vault_path)
            except (
                ActionGatewayStoreError,
                ConfigurationError,
                OSError,
                ValueError,
                TypeError,
            ) as exc:
                raise ActionGatewayRuntimeUnavailableError() from exc
            self._store = store
            return store


__all__ = [
    "ACTION_GATEWAY_CATALOG_V1",
    "ActionGatewayCompensationError",
    "ActionGatewayOperationNotFoundError",
    "ActionGatewayOrchestrationError",
    "ActionGatewayOrchestratorV1",
    "ActionGatewayReconciliationUnavailableError",
    "ActionGatewayRuntimeUnavailableError",
    "ActionGatewayStatusV1",
    "ProductionActionGatewayServiceV1",
]
