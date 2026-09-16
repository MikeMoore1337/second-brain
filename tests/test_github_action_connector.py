"""Детерминированные проверки закрытого GitHub action connector без live network."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from email.message import Message
from typing import cast
from uuid import uuid7

import pytest

from second_brain.adapters.actions.github_issues import (
    GITHUB_ACTION_ACCEPT,
    GITHUB_ACTION_API_BASE_URL,
    GITHUB_ACTION_API_VERSION,
    GITHUB_ACTION_MAX_RECONCILIATION_ITEMS,
    GITHUB_ACTION_USER_AGENT,
    GitHubActionConfigStatusV1,
    GitHubActionConfigurationError,
    GitHubActionConfigV1,
    GitHubIssuesActionConnectorV1,
    load_github_action_config,
)
from second_brain.application.action_gateway import (
    ACTION_GATEWAY_CONNECTOR,
    ACTION_GATEWAY_CREDENTIAL_PROFILE_ID,
    ACTION_GATEWAY_POLICY_ID,
    PREPARED_ACTION_CONTRACT_VERSION,
    ActionExecutionOutcomeV1,
    ActionGatewayConnectorError,
    ActionIntentV1,
    ActionKindV1,
    ActionReceiptStateV1,
    IssueStateV1,
    PreparedExternalActionV1,
    ReversibilityV1,
    RiskClassV1,
    action_gateway_hash,
)

NOW = datetime(2026, 9, 16, 21, 0, tzinfo=UTC)
REPOSITORY = "MikeMoore1337/second-brain"
TOKEN = "ghs-stage19-test-token"


class FakeResponse:
    """Минимальный bounded response для внутренней transport seam."""

    def __init__(self, status: int, payload: bytes) -> None:
        self.status = status
        self.payload = payload

    def read(self, amount: int = -1) -> bytes:
        return self.payload if amount < 0 else self.payload[:amount]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc_value, traceback


class RecordingOpener:
    """Запоминает только запросы, которые тест сам разрешил отправить."""

    def __init__(self, responses: list[FakeResponse | BaseException]) -> None:
        self.responses = responses
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float] = []

    def __call__(self, request: urllib.request.Request, *, timeout: float) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if not self.responses:
            raise AssertionError("unexpected additional provider request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _response(payload: dict[str, object], status: int = 200) -> FakeResponse:
    return FakeResponse(
        status,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )


def _list_response(payload: list[object], status: int = 200) -> FakeResponse:
    return FakeResponse(
        status,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )


def _repository_payload(*, repository_id: int = 101, node_id: str = "R_repo") -> dict[str, object]:
    return {"id": repository_id, "node_id": node_id, "full_name": REPOSITORY}


def _issue_payload(
    *,
    issue_id: int = 202,
    node_id: str = "I_issue",
    state: str = "open",
    locked: bool = False,
    number: int = 123,
) -> dict[str, object]:
    return {
        "id": issue_id,
        "node_id": node_id,
        "number": number,
        "state": state,
        "locked": locked,
    }


def _mutation_payload(*, number: int = 301, state: str = "open") -> dict[str, object]:
    return {
        "id": 303,
        "node_id": "I_created",
        "number": number,
        "state": state,
        "html_url": f"https://github.com/{REPOSITORY}/issues/{number}",
    }


def _config(*repositories: str) -> GitHubActionConfigV1:
    return GitHubActionConfigV1(
        enabled=True,
        repositories=tuple(repositories or (REPOSITORY,)),
        token=TOKEN,
        status=GitHubActionConfigStatusV1.READY,
    )


def _intent(
    action_kind: ActionKindV1 = ActionKindV1.GITHUB_ISSUE_CREATE,
    *,
    operation_id: str = "connector-operation-1",
    desired_state: IssueStateV1 = IssueStateV1.CLOSED,
) -> ActionIntentV1:
    if action_kind is ActionKindV1.GITHUB_ISSUE_CREATE:
        return ActionIntentV1(
            "action-intent-v1",
            operation_id,
            action_kind,
            ACTION_GATEWAY_CONNECTOR,
            REPOSITORY,
            title="Точная задача",
            body="Содержимое задачи",
        )
    if action_kind is ActionKindV1.GITHUB_ISSUE_COMMENT:
        return ActionIntentV1(
            "action-intent-v1",
            operation_id,
            action_kind,
            ACTION_GATEWAY_CONNECTOR,
            REPOSITORY,
            issue_number=123,
            comment="Точный комментарий",
        )
    return ActionIntentV1(
        "action-intent-v1",
        operation_id,
        action_kind,
        ACTION_GATEWAY_CONNECTOR,
        REPOSITORY,
        issue_number=123,
        desired_state=desired_state,
    )


def _headers(request: urllib.request.Request) -> dict[str, str]:
    return {key.casefold(): value for key, value in request.header_items()}


def _prepared(
    connector: GitHubIssuesActionConnectorV1,
    intent: ActionIntentV1,
) -> PreparedExternalActionV1:
    prepared_id = uuid7()
    result = connector.prepare(intent, prepared_action_id=prepared_id, now=NOW)
    return PreparedExternalActionV1(
        prepared_action_id=prepared_id,
        contract_version=PREPARED_ACTION_CONTRACT_VERSION,
        operation_id_fingerprint=intent.operation_id_fingerprint,
        action_kind=intent.action_kind,
        risk=RiskClassV1.CONTROLLED_WRITE,
        connector=ACTION_GATEWAY_CONNECTOR,
        connector_policy_id=ACTION_GATEWAY_POLICY_ID,
        credential_profile_id=ACTION_GATEWAY_CREDENTIAL_PROFILE_ID,
        exact_target_identity=result.exact_target_identity,
        preflight_fingerprint=result.preflight_fingerprint,
        semantic_payload=result.semantic_payload,
        payload_fingerprint=action_gateway_hash(result.semantic_payload),
        preview=result.preview,
        preview_fingerprint=action_gateway_hash(result.preview),
        prepared_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        reversibility=cast(ReversibilityV1, result.reversibility),
        provenance=intent.provenance,
    )


def test_action_config_is_disabled_by_default_and_never_repr_exposes_token() -> None:
    disabled = load_github_action_config(environ={})
    assert disabled.status is GitHubActionConfigStatusV1.DISABLED
    assert disabled.ready is False
    assert disabled.token is None

    unavailable = load_github_action_config(
        environ={
            "SECOND_BRAIN_ACTION_GITHUB_ENABLED": "true",
            "SECOND_BRAIN_ACTION_GITHUB_TOKEN": "",
            "SECOND_BRAIN_ACTION_GITHUB_REPOSITORIES": REPOSITORY,
        }
    )
    assert unavailable.status is GitHubActionConfigStatusV1.CREDENTIAL_UNAVAILABLE
    assert unavailable.token is None
    assert TOKEN not in repr(unavailable)

    ready = load_github_action_config(
        environ={
            "SECOND_BRAIN_ACTION_GITHUB_ENABLED": "on",
            "SECOND_BRAIN_ACTION_GITHUB_TOKEN": TOKEN,
            "SECOND_BRAIN_ACTION_GITHUB_REPOSITORIES": REPOSITORY,
        }
    )
    assert ready.status is GitHubActionConfigStatusV1.READY
    assert ready.repositories == (REPOSITORY,)
    assert TOKEN not in repr(ready)


def test_action_config_rejects_duplicate_or_malformed_allowlist() -> None:
    with pytest.raises(GitHubActionConfigurationError):
        GitHubActionConfigV1(
            enabled=True,
            repositories=(REPOSITORY, REPOSITORY.lower()),
            token=TOKEN,
            status=GitHubActionConfigStatusV1.READY,
        )
    malformed = load_github_action_config(
        environ={
            "SECOND_BRAIN_ACTION_GITHUB_ENABLED": "true",
            "SECOND_BRAIN_ACTION_GITHUB_TOKEN": TOKEN,
            "SECOND_BRAIN_ACTION_GITHUB_REPOSITORIES": "owner/repo.git",
        }
    )
    assert malformed.status is GitHubActionConfigStatusV1.CREDENTIAL_UNAVAILABLE


def test_prepare_reads_exact_repository_without_mutating_or_exposing_credential() -> None:
    opener = RecordingOpener([_response(_repository_payload())])
    connector = GitHubIssuesActionConnectorV1(_config(), opener=opener)

    prepared = connector.prepare(_intent(), prepared_action_id=uuid7(), now=NOW)

    assert len(opener.requests) == 1
    request = opener.requests[0]
    assert request.full_url == f"{GITHUB_ACTION_API_BASE_URL}/repos/MikeMoore1337/second-brain"
    assert request.get_method() == "GET"
    assert request.data is None
    request_headers = _headers(request)
    assert request_headers["accept"] == GITHUB_ACTION_ACCEPT
    assert request_headers["user-agent"] == GITHUB_ACTION_USER_AGENT
    assert request_headers["x-github-api-version"] == GITHUB_ACTION_API_VERSION
    assert "content-type" not in request_headers
    assert request_headers["authorization"] == f"Bearer {TOKEN}"
    assert prepared.exact_target_identity.repository_id == 101
    assert prepared.exact_target_identity.repository_node_id == "R_repo"
    assert "изменение в GitHub ещё не выполнялось" in prepared.preview
    assert TOKEN not in prepared.preview
    assert TOKEN not in repr(prepared)


@pytest.mark.parametrize(
    ("payload", "error_code"),
    [
        (
            {**_issue_payload(), "pull_request": {"url": "https://api.github.com/pulls/123"}},
            "pull_request_forbidden",
        ),
        (_issue_payload(locked=True), "issue_locked"),
    ],
)
def test_prepare_rejects_pull_request_and_locked_issue_before_mutation(
    payload: dict[str, object], error_code: str
) -> None:
    opener = RecordingOpener([_response(_repository_payload()), _response(payload)])
    connector = GitHubIssuesActionConnectorV1(_config(), opener=opener)

    with pytest.raises(ActionGatewayConnectorError) as raised:
        connector.prepare(
            _intent(ActionKindV1.GITHUB_ISSUE_COMMENT),
            prepared_action_id=uuid7(),
            now=NOW,
        )

    assert raised.value.safe_error_code == error_code
    assert raised.value.outcome is ActionExecutionOutcomeV1.FAILED_BEFORE_SEND
    assert len(opener.requests) == 2
    assert all(request.get_method() == "GET" for request in opener.requests)


def test_create_execute_sends_one_exact_post_with_bound_marker() -> None:
    opener = RecordingOpener(
        [_response(_repository_payload()), _response(_mutation_payload(), status=201)]
    )
    connector = GitHubIssuesActionConnectorV1(_config(), opener=opener)
    prepared = _prepared(connector, _intent())

    result = connector.execute(prepared, now=NOW)

    assert result.outcome is ActionExecutionOutcomeV1.EXECUTED
    assert result.remote_safe_identity == {
        "repository": REPOSITORY,
        "issue_id": 303,
        "issue_node_id": "I_created",
        "issue_number": 301,
        "state": "open",
        "url": f"https://github.com/{REPOSITORY}/issues/301",
    }
    assert len(opener.requests) == 2
    request = opener.requests[1]
    assert request.get_method() == "POST"
    assert request.full_url == f"{GITHUB_ACTION_API_BASE_URL}/repos/{REPOSITORY}/issues"
    assert _headers(request)["content-type"] == "application/json"
    assert json.loads(cast(bytes, request.data).decode("utf-8")) == {
        "body": prepared.semantic_payload["body"],
        "title": prepared.semantic_payload["title"],
    }


def test_comment_execute_records_comment_identity_not_issue_identity() -> None:
    opener = RecordingOpener(
        [
            _response(_repository_payload()),
            _response(_issue_payload()),
        ]
    )
    connector = GitHubIssuesActionConnectorV1(_config(), opener=opener)
    prepared = _prepared(connector, _intent(ActionKindV1.GITHUB_ISSUE_COMMENT))
    opener.responses.append(
        _response(
            {
                "id": 909,
                "node_id": "IC_comment",
                "body": cast(str, prepared.semantic_payload["comment"]),
                "html_url": f"https://github.com/{REPOSITORY}/issues/123#issuecomment-909",
            },
            status=201,
        )
    )

    result = connector.execute(prepared, now=NOW)

    assert result.remote_safe_identity == {
        "repository": REPOSITORY,
        "issue_number": 123,
        "issue_id": 202,
        "issue_node_id": "I_issue",
        "comment_id": 909,
        "comment_node_id": "IC_comment",
        "url": f"https://github.com/{REPOSITORY}/issues/123",
    }
    assert opener.requests[2].get_method() == "POST"


def test_set_state_sends_only_state_patch_and_revalidation_is_exact() -> None:
    opener = RecordingOpener(
        [
            _response(_repository_payload()),
            _response(_issue_payload()),
            _response(_repository_payload()),
            _response(_issue_payload()),
            _response(_mutation_payload(number=123, state="closed")),
        ]
    )
    connector = GitHubIssuesActionConnectorV1(_config(), opener=opener)
    prepared = _prepared(connector, _intent(ActionKindV1.GITHUB_ISSUE_SET_STATE))
    revalidated = connector.revalidate(prepared, now=NOW)
    assert revalidated.exact_target_identity == prepared.exact_target_identity

    result = connector.execute(prepared, now=NOW)

    assert result.outcome is ActionExecutionOutcomeV1.EXECUTED
    request = opener.requests[4]
    assert request.get_method() == "PATCH"
    assert request.full_url == f"{GITHUB_ACTION_API_BASE_URL}/repos/{REPOSITORY}/issues/123"
    assert json.loads(cast(bytes, request.data).decode("utf-8")) == {"state": "closed"}
    assert len(opener.requests) == 5


def test_create_reconciliation_uses_one_bounded_marker_list_without_mutation() -> None:
    opener = RecordingOpener([_response(_repository_payload())])
    connector = GitHubIssuesActionConnectorV1(_config(), opener=opener)
    prepared = _prepared(connector, _intent())
    marker = cast(str, prepared.semantic_payload["marker"])
    opener.responses.append(
        _response(_repository_payload()),
    )
    opener.responses.append(
        _list_response(
            [
                {
                    **_mutation_payload(number=321),
                    "body": f"created {marker}",
                }
            ]
        )
    )

    result = connector.reconcile(prepared, now=NOW)

    assert result.state is ActionReceiptStateV1.RECONCILED_EXECUTED
    assert result.remote_safe_identity is not None
    assert result.remote_safe_identity["issue_number"] == 321
    assert len(opener.requests) == 3
    request = opener.requests[2]
    assert request.get_method() == "GET"
    assert request.full_url == (
        f"{GITHUB_ACTION_API_BASE_URL}/repos/{REPOSITORY}/issues"
        f"?state=all&per_page={GITHUB_ACTION_MAX_RECONCILIATION_ITEMS}&page=1"
    )
    assert all(item.get_method() == "GET" for item in opener.requests)


def test_marker_reconciliation_is_not_executed_or_ambiguous_by_cardinality() -> None:
    opener = RecordingOpener([_response(_repository_payload())])
    connector = GitHubIssuesActionConnectorV1(_config(), opener=opener)
    prepared = _prepared(connector, _intent())
    marker = cast(str, prepared.semantic_payload["marker"])
    opener.responses.extend([_response(_repository_payload()), _list_response([])])
    not_executed = connector.reconcile(prepared, now=NOW)
    assert not_executed.state is ActionReceiptStateV1.RECONCILED_NOT_EXECUTED

    opener.responses.extend(
        [
            _response(_repository_payload()),
            _list_response(
                [
                    {**_mutation_payload(number=321), "body": marker},
                    {**_mutation_payload(number=322), "body": marker},
                ]
            ),
        ]
    )
    ambiguous = connector.reconcile(prepared, now=NOW)
    assert ambiguous.state is ActionReceiptStateV1.RECONCILIATION_AMBIGUOUS
    assert ambiguous.safe_error_code == "reconciliation_ambiguous"
    assert len(opener.requests) == 5


def test_comment_reconciliation_requires_exact_issue_and_returns_comment_identity() -> None:
    opener = RecordingOpener([_response(_repository_payload()), _response(_issue_payload())])
    connector = GitHubIssuesActionConnectorV1(_config(), opener=opener)
    prepared = _prepared(
        connector,
        _intent(ActionKindV1.GITHUB_ISSUE_COMMENT),
    )
    marker = cast(str, prepared.semantic_payload["marker"])
    opener.responses.extend(
        [
            _response(_repository_payload()),
            _response(_issue_payload()),
            _list_response(
                [
                    {
                        "id": 909,
                        "node_id": "IC_comment",
                        "body": f"Комментарий\n{marker}",
                        "html_url": f"https://github.com/{REPOSITORY}/issues/123#issuecomment-909",
                    }
                ]
            ),
        ]
    )

    result = connector.reconcile(prepared, now=NOW)

    assert result.state is ActionReceiptStateV1.RECONCILED_EXECUTED
    assert result.remote_safe_identity == {
        "repository": REPOSITORY,
        "issue_number": 123,
        "issue_id": 202,
        "issue_node_id": "I_issue",
        "comment_id": 909,
        "comment_node_id": "IC_comment",
        "url": f"https://github.com/{REPOSITORY}/issues/123",
    }
    assert len(opener.requests) == 5
    assert opener.requests[4].get_method() == "GET"
    assert "comments?per_page=100&page=1" in opener.requests[4].full_url


@pytest.mark.parametrize(
    ("current_state", "expected"),
    [
        ("closed", ActionReceiptStateV1.RECONCILED_EXECUTED),
        ("open", ActionReceiptStateV1.RECONCILED_NOT_EXECUTED),
    ],
)
def test_set_state_reconciliation_compares_exact_state_without_patch(
    current_state: str, expected: ActionReceiptStateV1
) -> None:
    opener = RecordingOpener(
        [
            _response(_repository_payload()),
            _response(_issue_payload(state="open")),
            _response(_repository_payload()),
            _response(_issue_payload(state=current_state)),
        ]
    )
    connector = GitHubIssuesActionConnectorV1(_config(), opener=opener)
    prepared = _prepared(
        connector,
        _intent(ActionKindV1.GITHUB_ISSUE_SET_STATE, desired_state=IssueStateV1.CLOSED),
    )

    result = connector.reconcile(prepared, now=NOW)

    assert result.state is expected
    assert len(opener.requests) == 4
    assert all(request.get_method() == "GET" for request in opener.requests)


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (422, ActionExecutionOutcomeV1.FAILED_CONFIRMED_NO_MUTATION),
        (500, ActionExecutionOutcomeV1.OUTCOME_UNCERTAIN),
    ],
)
def test_mutation_http_failures_have_bounded_outcome_and_no_retry(
    status: int, outcome: ActionExecutionOutcomeV1
) -> None:
    error = urllib.error.HTTPError(
        f"{GITHUB_ACTION_API_BASE_URL}/repos/{REPOSITORY}/issues",
        status,
        "provider detail must not escape",
        hdrs=Message(),
        fp=None,
    )
    opener = RecordingOpener([_response(_repository_payload()), error])
    connector = GitHubIssuesActionConnectorV1(_config(), opener=opener)
    prepared = _prepared(connector, _intent())

    with pytest.raises(ActionGatewayConnectorError) as raised:
        connector.execute(prepared, now=NOW)

    assert raised.value.outcome is outcome
    assert raised.value.safe_error_code in {
        "provider_rejected",
        "provider_outcome_uncertain",
    }
    assert len(opener.requests) == 2
    assert "provider detail" not in str(raised.value)


def test_transport_failure_is_uncertain_only_for_mutation() -> None:
    opener = RecordingOpener([_response(_repository_payload()), TimeoutError()])
    connector = GitHubIssuesActionConnectorV1(_config(), opener=opener)
    prepared = _prepared(connector, _intent())

    with pytest.raises(ActionGatewayConnectorError) as raised:
        connector.execute(prepared, now=NOW)

    assert raised.value.outcome is ActionExecutionOutcomeV1.OUTCOME_UNCERTAIN
    assert raised.value.safe_error_code == "provider_outcome_uncertain"
    assert len(opener.requests) == 2


def test_disabled_connector_does_not_open_network() -> None:
    opener = RecordingOpener([])
    connector = GitHubIssuesActionConnectorV1(load_github_action_config(environ={}), opener=opener)

    with pytest.raises(ActionGatewayConnectorError) as raised:
        connector.prepare(_intent(), prepared_action_id=uuid7(), now=NOW)

    assert raised.value.outcome is ActionExecutionOutcomeV1.FAILED_BEFORE_SEND
    assert raised.value.safe_error_code == "connector_disabled"
    assert opener.requests == []
