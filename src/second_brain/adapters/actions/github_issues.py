"""Closed, fixed-host GitHub Issues connector for Stage 19.

This adapter is deliberately separate from the public GitHub research adapter
and from the login-only OAuth client.  It accepts only the action gateway
protocol, reads a server-side credential, and constructs every request from a
closed endpoint map.  No caller URL, headers, proxy, redirect, generic HTTP
method, or provider response is exposed.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol, cast
from urllib.parse import quote
from uuid import UUID

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from second_brain.application.action_gateway import (
    ACTION_GATEWAY_CONNECTOR,
    ACTION_GATEWAY_CREDENTIAL_PROFILE_ID,
    ACTION_GATEWAY_POLICY_ID,
    ActionExecutionOutcomeV1,
    ActionGatewayConnectorError,
    ActionGatewayInvalidRequestError,
    ActionIntentV1,
    ActionKindV1,
    ActionReceiptStateV1,
    ConnectorExecutionResultV1,
    ConnectorPreparedActionV1,
    ConnectorReconciliationResultV1,
    ConnectorRevalidationV1,
    ExactTargetIdentityV1,
    IssueStateV1,
    PreparedExternalActionV1,
    ReversibilityV1,
    action_gateway_hash,
)

GITHUB_ACTION_API_BASE_URL: Final[str] = "https://api.github.com"
GITHUB_ACTION_API_VERSION: Final[str] = "2026-03-10"
GITHUB_ACTION_USER_AGENT: Final[str] = "Second-Brain-Action-Gateway-v1"
GITHUB_ACTION_ACCEPT: Final[str] = "application/vnd.github+json"
GITHUB_ACTION_TIMEOUT_SECONDS: Final[float] = 10.0
GITHUB_ACTION_MAX_RESPONSE_BYTES: Final[int] = 256 * 1024
GITHUB_ACTION_MAX_REQUEST_BYTES: Final[int] = 128 * 1024
GITHUB_ACTION_MAX_REPOSITORIES: Final[int] = 32
GITHUB_ACTION_MAX_TOKEN_BYTES: Final[int] = 4096
GITHUB_ACTION_MAX_RECONCILIATION_ITEMS: Final[int] = 100

_REPOSITORY_OWNER_MAX_BYTES: Final[int] = 39
_REPOSITORY_NAME_MAX_BYTES: Final[int] = 100


class GitHubActionConfigStatusV1(StrEnum):
    """String status kept intentionally tiny for safe API/UI projection."""

    DISABLED = "disabled"
    READY = "ready"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"


class GitHubActionConfigurationError(ValueError):
    """Invalid server-side action configuration, without secret details."""


class GitHubActionTransportError(RuntimeError):
    """Internal fixed-host transport failure with no upstream detail."""


class GitHubActionResponseError(RuntimeError):
    """Upstream response was bounded but not a valid expected JSON document."""


class _EnvironmentSettings(BaseSettings):
    enabled: bool = False
    token: str | None = None
    repositories: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="SECOND_BRAIN_ACTION_GITHUB_",
        env_file=None,
        extra="ignore",
    )


@dataclass(frozen=True, slots=True)
class GitHubActionConfigV1:
    """Validated server-only configuration; token is excluded from repr."""

    enabled: bool
    repositories: tuple[str, ...]
    token: str | None = field(default=None, repr=False)
    status: GitHubActionConfigStatusV1 = GitHubActionConfigStatusV1.DISABLED

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or type(self.repositories) is not tuple:
            raise GitHubActionConfigurationError()
        try:
            status = (
                self.status
                if isinstance(self.status, GitHubActionConfigStatusV1)
                else GitHubActionConfigStatusV1(self.status)
            )
        except (TypeError, ValueError) as exc:
            raise GitHubActionConfigurationError() from exc
        repositories = (
            ()
            if not self.repositories and status != GitHubActionConfigStatusV1.READY
            else _parse_repository_allowlist(self.repositories)
        )
        if status == GitHubActionConfigStatusV1.READY:
            if not self.enabled or not _valid_token(self.token) or not repositories:
                raise GitHubActionConfigurationError()
        elif status == GitHubActionConfigStatusV1.DISABLED:
            if self.enabled or self.token is not None or repositories:
                raise GitHubActionConfigurationError()
        elif not self.enabled or self.token is not None:
            raise GitHubActionConfigurationError()
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "repositories", repositories)

    @property
    def ready(self) -> bool:
        return self.status == GitHubActionConfigStatusV1.READY


def load_github_action_config(
    *,
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> GitHubActionConfigV1:
    """Load the dedicated action env boundary without failing app startup."""

    try:
        if environ is None:
            settings = _EnvironmentSettings(_env_file=env_file)  # type: ignore[call-arg]
        else:
            raw_enabled = environ.get("SECOND_BRAIN_ACTION_GITHUB_ENABLED", "false").casefold()
            if raw_enabled in {"1", "true", "yes", "on"}:
                enabled = True
            elif raw_enabled in {"0", "false", "no", "off"}:
                enabled = False
            else:
                raise ValueError()
            settings = _EnvironmentSettings(
                enabled=enabled,
                token=environ.get("SECOND_BRAIN_ACTION_GITHUB_TOKEN"),
                repositories=environ.get("SECOND_BRAIN_ACTION_GITHUB_REPOSITORIES"),
            )
    except ValidationError, OSError, ValueError:
        return GitHubActionConfigV1(
            enabled=True,
            repositories=(),
            token=None,
            status=GitHubActionConfigStatusV1.CREDENTIAL_UNAVAILABLE,
        )
    if not settings.enabled:
        return GitHubActionConfigV1(
            enabled=False,
            repositories=(),
            token=None,
            status=GitHubActionConfigStatusV1.DISABLED,
        )
    try:
        token = _validate_token(settings.token)
        repositories = _parse_repository_allowlist(settings.repositories or "")
    except GitHubActionConfigurationError:
        return GitHubActionConfigV1(
            enabled=True,
            repositories=(),
            token=None,
            status=GitHubActionConfigStatusV1.CREDENTIAL_UNAVAILABLE,
        )
    return GitHubActionConfigV1(
        enabled=True,
        repositories=repositories,
        token=token,
        status=GitHubActionConfigStatusV1.READY,
    )


def _valid_token(value: object) -> bool:
    if (
        type(value) is not str
        or not 1 <= len(value.encode("utf-8")) <= GITHUB_ACTION_MAX_TOKEN_BYTES
    ):
        return False
    return not any(ord(character) < 0x21 or ord(character) == 0x7F for character in value)


def _validate_token(value: object) -> str:
    if not _valid_token(value):
        raise GitHubActionConfigurationError()
    return cast(str, value)


def _repository_parts(value: object) -> tuple[str, str]:
    if type(value) is not str or value.count("/") != 1 or value != value.strip():
        raise GitHubActionConfigurationError()
    owner, repository = value.split("/")
    try:
        owner_bytes = len(owner.encode("ascii"))
        repository_bytes = len(repository.encode("ascii"))
    except UnicodeEncodeError:
        raise GitHubActionConfigurationError() from None
    if not 1 <= owner_bytes <= _REPOSITORY_OWNER_MAX_BYTES:
        raise GitHubActionConfigurationError()
    if not 1 <= repository_bytes <= _REPOSITORY_NAME_MAX_BYTES:
        raise GitHubActionConfigurationError()
    if (
        owner.endswith(("-",))
        or repository in {".", ".."}
        or repository.casefold().endswith(".git")
    ):
        raise GitHubActionConfigurationError()
    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
    if any(character not in allowed for character in owner + repository):
        raise GitHubActionConfigurationError()
    return owner, repository


def _parse_repository_allowlist(value: object) -> tuple[str, ...]:
    if type(value) is tuple:
        entries = list(cast(tuple[object, ...], value))
    elif type(value) is str:
        entries = [
            part.strip() for part in value.replace("\r", "\n").replace(",", "\n").split("\n")
        ]
    else:
        raise GitHubActionConfigurationError()
    if not entries or len(entries) > GITHUB_ACTION_MAX_REPOSITORIES:
        raise GitHubActionConfigurationError()
    result: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        owner, repository = _repository_parts(entry)
        canonical = f"{owner}/{repository}"
        folded = canonical.casefold()
        if folded in seen:
            raise GitHubActionConfigurationError()
        seen.add(folded)
        result.append(canonical)
    return tuple(result)


class _HttpResponse(Protocol):
    status: int

    def read(self, amount: int = -1) -> bytes:
        """Read bounded bytes."""

    def __enter__(self) -> _HttpResponse:
        """Enter response context."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        """Close response."""


class _UrlOpen(Protocol):
    def __call__(self, request: urllib.request.Request, *, timeout: float) -> _HttpResponse:
        """Open one internally constructed request."""


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject all redirects to preserve the fixed origin and path."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request:
        del req, fp, code, msg, headers, newurl
        raise GitHubActionTransportError()


def _urlopen_without_redirects(request: urllib.request.Request, *, timeout: float) -> _HttpResponse:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirectHandler,
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    return cast(_HttpResponse, opener.open(request, timeout=timeout))


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise GitHubActionResponseError()
        result[key] = value
    return result


def _json_response_document(response: _HttpResponse, *, expected_status: set[int]) -> object:
    try:
        status = response.status
        body = response.read(GITHUB_ACTION_MAX_RESPONSE_BYTES + 1)
    except Exception as exc:
        raise GitHubActionTransportError() from exc
    if type(status) is not int or type(body) is not bytes:
        raise GitHubActionResponseError()
    if status not in expected_status:
        raise GitHubActionResponseError()
    if len(body) > GITHUB_ACTION_MAX_RESPONSE_BYTES:
        raise GitHubActionResponseError()
    try:
        decoded = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise GitHubActionResponseError() from exc
    if type(decoded) not in {dict, list}:
        raise GitHubActionResponseError()
    return decoded


def _json_response(response: _HttpResponse, *, expected_status: set[int]) -> dict[str, object]:
    decoded = _json_response_document(response, expected_status=expected_status)
    if type(decoded) is not dict:
        raise GitHubActionResponseError()
    return cast(dict[str, object], decoded)


def _safe_int(value: object, *, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or isinstance(value, bool) or not 1 <= value <= maximum:
        raise GitHubActionResponseError()
    return value


def _safe_text(value: object, *, max_bytes: int = 512) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > max_bytes:
        raise GitHubActionResponseError()
    if any(ord(character) < 0x20 and character not in "\n\r\t" for character in value):
        raise GitHubActionResponseError()
    return value


def _repo_target(payload: Mapping[str, object], repository: str) -> ExactTargetIdentityV1:
    if type(payload) is not dict:
        raise GitHubActionResponseError()
    full_name = payload.get("full_name")
    if type(full_name) is not str or full_name.casefold() != repository.casefold():
        raise GitHubActionResponseError()
    return ExactTargetIdentityV1(
        repository,
        _safe_int(payload.get("id")),
        _safe_text(payload.get("node_id"), max_bytes=256),
    )


def _issue_target(
    payload: Mapping[str, object],
    repository: str,
    issue_number: int,
    repository_target: ExactTargetIdentityV1,
) -> ExactTargetIdentityV1:
    if "pull_request" in payload:
        raise ActionGatewayConnectorError(
            ActionExecutionOutcomeV1.FAILED_BEFORE_SEND,
            "pull_request_forbidden",
        )
    if payload.get("number") != issue_number:
        raise GitHubActionResponseError()
    state = payload.get("state")
    if state not in {IssueStateV1.OPEN.value, IssueStateV1.CLOSED.value}:
        raise GitHubActionResponseError()
    locked = payload.get("locked")
    if type(locked) is not bool:
        raise GitHubActionResponseError()
    return ExactTargetIdentityV1(
        repository,
        repository_target.repository_id,
        repository_target.repository_node_id,
        issue_number=issue_number,
        issue_id=_safe_int(payload.get("id")),
        issue_node_id=_safe_text(payload.get("node_id"), max_bytes=256),
        current_state=state,
        locked=locked,
    )


@dataclass(slots=True)
class GitHubIssuesActionConnectorV1:
    """Fixed GitHub Issues action connector with injectable transport."""

    config: GitHubActionConfigV1
    opener: _UrlOpen = field(default_factory=lambda: _urlopen_without_redirects)
    timeout_seconds: float = GITHUB_ACTION_TIMEOUT_SECONDS
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    connector_id: str = field(default=ACTION_GATEWAY_CONNECTOR, init=False)
    policy_id: str = field(default=ACTION_GATEWAY_POLICY_ID, init=False)
    credential_profile_id: str = field(default=ACTION_GATEWAY_CREDENTIAL_PROFILE_ID, init=False)

    def __post_init__(self) -> None:
        if (
            type(self.config) is not GitHubActionConfigV1
            or type(self.timeout_seconds) not in {int, float}
            or not 0 < self.timeout_seconds <= 60
        ):
            raise GitHubActionConfigurationError()

    def _require_ready(self) -> str:
        if self.config.status == GitHubActionConfigStatusV1.DISABLED:
            raise ActionGatewayConnectorError(
                ActionExecutionOutcomeV1.FAILED_BEFORE_SEND,
                "connector_disabled",
            )
        if not self.config.ready or self.config.token is None:
            raise ActionGatewayConnectorError(
                ActionExecutionOutcomeV1.FAILED_BEFORE_SEND,
                "credential_unavailable",
            )
        return self.config.token

    def _canonical_repository(self, repository: str) -> str:
        for configured in self.config.repositories:
            if configured.casefold() == repository.casefold():
                return configured
        raise ActionGatewayConnectorError(
            ActionExecutionOutcomeV1.FAILED_BEFORE_SEND,
            "target_not_allowed",
        )

    def prepare(
        self,
        intent: ActionIntentV1,
        *,
        prepared_action_id: UUID,
        now: datetime,
    ) -> ConnectorPreparedActionV1:
        del now
        token = self._require_ready()
        repository = self._canonical_repository(intent.repository)
        owner, repo = _repository_parts(repository)
        repository_payload = self._request_json(
            "GET", f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}", token=token
        )
        target = _repo_target(repository_payload, repository)
        if intent.issue_number is not None:
            issue_payload = self._request_json(
                "GET",
                f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
                f"/issues/{intent.issue_number}",
                token=token,
            )
            issue = _issue_target(issue_payload, repository, intent.issue_number, target)
            target = ExactTargetIdentityV1(
                repository,
                target.repository_id,
                target.repository_node_id,
                issue_number=issue.issue_number,
                issue_id=issue.issue_id,
                issue_node_id=issue.issue_node_id,
                current_state=issue.current_state,
                locked=issue.locked,
            )
            if issue.locked:
                raise ActionGatewayConnectorError(
                    ActionExecutionOutcomeV1.FAILED_BEFORE_SEND,
                    "issue_locked",
                )
        marker = f"<!-- second-brain-action:{prepared_action_id} -->"
        payload: dict[str, object]
        if intent.action_kind is ActionKindV1.GITHUB_ISSUE_CREATE:
            payload = {
                "repository": repository,
                "title": cast(str, intent.title),
                "body": f"{intent.body}\n{marker}",
                "marker": marker,
            }
            reversibility = ReversibilityV1.COMPENSATION_ONLY
            title = "создание задачи GitHub"
            semantic = f"Заголовок: {intent.title}\nТело: {intent.body}\nСлужебный маркер: {marker}"
        elif intent.action_kind is ActionKindV1.GITHUB_ISSUE_COMMENT:
            payload = {
                "repository": repository,
                "issue_number": intent.issue_number,
                "comment": f"{intent.comment}\n{marker}",
                "marker": marker,
            }
            reversibility = ReversibilityV1.NOT_SUPPORTED
            title = "комментарий к задаче GitHub"
            semantic = f"Комментарий: {intent.comment}\nСлужебный маркер: {marker}"
        else:
            payload = {
                "repository": repository,
                "issue_number": intent.issue_number,
                "desired_state": cast(IssueStateV1, intent.desired_state).value,
            }
            reversibility = ReversibilityV1.SUPPORTED
            title = "изменение состояния задачи GitHub"
            semantic = f"Состояние: {target.current_state} -> {intent.desired_state}"
        preview = (
            f"GitHub: {title}\n"
            f"Точная цель: {repository}"
            + (f" / задача #{intent.issue_number}\n" if intent.issue_number is not None else "\n")
            + f"{semantic}\n"
            "Риск: controlled_write\n"
            "Внешний побочный эффект: только после отдельного подтверждения владельца.\n"
            f"Профиль доступа: {ACTION_GATEWAY_CREDENTIAL_PROFILE_ID}\n"
            "Предпросмотр действия; изменение в GitHub ещё не выполнялось."
        )
        preflight = action_gateway_hash(
            {
                "repository": target.repository,
                "repository_id": target.repository_id,
                "repository_node_id": target.repository_node_id,
                "issue_number": target.issue_number,
                "issue_id": target.issue_id,
                "issue_node_id": target.issue_node_id,
                "current_state": target.current_state,
                "locked": target.locked,
                "action_kind": intent.action_kind,
            }
        )
        return ConnectorPreparedActionV1(target, preflight, payload, preview, reversibility)

    def revalidate(
        self,
        prepared: PreparedExternalActionV1,
        *,
        now: datetime,
    ) -> ConnectorRevalidationV1:
        del now
        token = self._require_ready()
        repository = self._canonical_repository(cast(str, prepared.semantic_payload["repository"]))
        owner, repo = _repository_parts(repository)
        repository_payload = self._request_json(
            "GET", f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}", token=token
        )
        repository_target = _repo_target(repository_payload, repository)
        if prepared.exact_target_identity.issue_number is None:
            return ConnectorRevalidationV1(repository_target)
        issue_number = prepared.exact_target_identity.issue_number
        issue_payload = self._request_json(
            "GET",
            f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/issues/{issue_number}",
            token=token,
        )
        issue = _issue_target(issue_payload, repository, issue_number, repository_target)
        if issue.locked:
            raise ActionGatewayConnectorError(
                ActionExecutionOutcomeV1.FAILED_BEFORE_SEND,
                "issue_locked",
            )
        return ConnectorRevalidationV1(
            ExactTargetIdentityV1(
                repository,
                repository_target.repository_id,
                repository_target.repository_node_id,
                issue_number=issue.issue_number,
                issue_id=issue.issue_id,
                issue_node_id=issue.issue_node_id,
                current_state=issue.current_state,
                locked=issue.locked,
            )
        )

    def reconcile(
        self,
        prepared: PreparedExternalActionV1,
        *,
        now: datetime,
    ) -> ConnectorReconciliationResultV1:
        """Check one exact marker or state without issuing a mutation."""

        token = self._require_ready()
        finished = now
        action_kind = cast(ActionKindV1, prepared.action_kind)
        repository = self._canonical_repository(cast(str, prepared.semantic_payload["repository"]))
        owner, repo = _repository_parts(repository)
        base_path = f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
        if action_kind is ActionKindV1.GITHUB_ISSUE_CREATE:
            marker = cast(str, prepared.semantic_payload["marker"])
            repository_payload = self._request_json("GET", base_path, token=token)
            repository_target = _repo_target(repository_payload, repository)
            if not _same_repository_identity(repository_target, prepared.exact_target_identity):
                return ConnectorReconciliationResultV1(
                    ActionReceiptStateV1.RECONCILIATION_AMBIGUOUS,
                    finished,
                    safe_error_code="reconciliation_ambiguous",
                )
            items = self._request_list(
                f"{base_path}/issues?state=all&per_page={GITHUB_ACTION_MAX_RECONCILIATION_ITEMS}&page=1",
                token=token,
            )
            matches, marker_ambiguous = _marker_matches(items, marker)
            if marker_ambiguous or len(matches) > 1:
                return ConnectorReconciliationResultV1(
                    ActionReceiptStateV1.RECONCILIATION_AMBIGUOUS,
                    finished,
                    safe_error_code="reconciliation_ambiguous",
                )
            if not matches:
                return ConnectorReconciliationResultV1(
                    ActionReceiptStateV1.RECONCILED_NOT_EXECUTED,
                    finished,
                )
            remote = _remote_identity(matches[0], repository, None)
            return ConnectorReconciliationResultV1(
                ActionReceiptStateV1.RECONCILED_EXECUTED,
                finished,
                remote_safe_identity=remote,
                remote_url=cast(str, remote["url"]),
            )

        issue_number = prepared.exact_target_identity.issue_number
        if issue_number is None:
            raise ActionGatewayInvalidRequestError()
        if action_kind is ActionKindV1.GITHUB_ISSUE_COMMENT:
            marker = cast(str, prepared.semantic_payload["marker"])
            repository_payload = self._request_json("GET", base_path, token=token)
            repository_target = _repo_target(repository_payload, repository)
            issue_payload = self._request_json(
                "GET", f"{base_path}/issues/{issue_number}", token=token
            )
            issue = _issue_target(issue_payload, repository, issue_number, repository_target)
            if issue.as_dict() != prepared.exact_target_identity.as_dict():
                return ConnectorReconciliationResultV1(
                    ActionReceiptStateV1.RECONCILIATION_AMBIGUOUS,
                    finished,
                    safe_error_code="reconciliation_ambiguous",
                )
            items = self._request_list(
                f"{base_path}/issues/{issue_number}/comments?per_page="
                f"{GITHUB_ACTION_MAX_RECONCILIATION_ITEMS}&page=1",
                token=token,
            )
            matches, marker_ambiguous = _marker_matches(items, marker)
            if marker_ambiguous or len(matches) > 1:
                return ConnectorReconciliationResultV1(
                    ActionReceiptStateV1.RECONCILIATION_AMBIGUOUS,
                    finished,
                    safe_error_code="reconciliation_ambiguous",
                )
            if not matches:
                return ConnectorReconciliationResultV1(
                    ActionReceiptStateV1.RECONCILED_NOT_EXECUTED,
                    finished,
                )
            remote = _comment_identity(
                matches[0],
                repository,
                prepared.exact_target_identity,
            )
            return ConnectorReconciliationResultV1(
                ActionReceiptStateV1.RECONCILED_EXECUTED,
                finished,
                remote_safe_identity=remote,
                remote_url=cast(str, remote["url"]),
            )

        repository_payload = self._request_json("GET", base_path, token=token)
        repository_target = _repo_target(repository_payload, repository)
        issue_payload = self._request_json("GET", f"{base_path}/issues/{issue_number}", token=token)
        issue = _issue_target(issue_payload, repository, issue_number, repository_target)
        bound = prepared.exact_target_identity
        if not _same_issue_identity(issue, bound):
            return ConnectorReconciliationResultV1(
                ActionReceiptStateV1.RECONCILIATION_AMBIGUOUS,
                finished,
                safe_error_code="reconciliation_ambiguous",
            )
        desired = cast(str, prepared.semantic_payload["desired_state"])
        if cast(IssueStateV1, issue.current_state).value == desired:
            state = ActionReceiptStateV1.RECONCILED_EXECUTED
        elif (
            cast(IssueStateV1, issue.current_state).value
            == cast(IssueStateV1, bound.current_state).value
        ):
            state = ActionReceiptStateV1.RECONCILED_NOT_EXECUTED
        else:
            state = ActionReceiptStateV1.RECONCILIATION_AMBIGUOUS
        return ConnectorReconciliationResultV1(
            state,
            finished,
            remote_safe_identity={
                **issue.safe_identity(),
                "state": cast(IssueStateV1, issue.current_state).value,
                "url": f"https://github.com/{repository}/issues/{issue_number}",
            },
            remote_url=f"https://github.com/{repository}/issues/{issue_number}",
            safe_error_code=(
                "reconciliation_ambiguous"
                if state is ActionReceiptStateV1.RECONCILIATION_AMBIGUOUS
                else None
            ),
        )

    def execute(
        self,
        prepared: PreparedExternalActionV1,
        *,
        now: datetime,
    ) -> ConnectorExecutionResultV1:
        token = self._require_ready()
        started = now
        action_kind = cast(ActionKindV1, prepared.action_kind)
        repository = cast(str, prepared.semantic_payload["repository"])
        owner, repo = _repository_parts(repository)
        issue_number = prepared.exact_target_identity.issue_number
        if action_kind is ActionKindV1.GITHUB_ISSUE_CREATE:
            method = "POST"
            path = f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/issues"
            outgoing = {
                "title": prepared.semantic_payload["title"],
                "body": prepared.semantic_payload["body"],
            }
            expected_status = {201}
        elif action_kind is ActionKindV1.GITHUB_ISSUE_COMMENT:
            if issue_number is None:
                raise ActionGatewayInvalidRequestError()
            method = "POST"
            path = (
                f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
                f"/issues/{issue_number}/comments"
            )
            outgoing = {"body": prepared.semantic_payload["comment"]}
            expected_status = {201}
        else:
            if issue_number is None:
                raise ActionGatewayInvalidRequestError()
            method = "PATCH"
            path = f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/issues/{issue_number}"
            outgoing = {"state": prepared.semantic_payload["desired_state"]}
            expected_status = {200}
        try:
            payload = self._request_json(
                method,
                path,
                token=token,
                body=outgoing,
                expected_status=expected_status,
                mutation=True,
            )
        except ActionGatewayConnectorError:
            raise
        finished = self.clock()
        remote = (
            _comment_identity(payload, repository, prepared.exact_target_identity)
            if action_kind is ActionKindV1.GITHUB_ISSUE_COMMENT
            else _remote_identity(payload, repository, issue_number)
        )
        remote_url = cast(str, remote["url"])
        return ConnectorExecutionResultV1(
            ActionExecutionOutcomeV1.EXECUTED,
            attempt_started_at=started,
            sent_at=started,
            finished_at=finished,
            remote_safe_identity=remote,
            remote_url=remote_url,
        )

    def _request_document(
        self,
        method: str,
        path: str,
        *,
        token: str,
        body: Mapping[str, object] | None = None,
        expected_status: set[int] | None = None,
        mutation: bool = False,
        expect_list: bool = False,
    ) -> object:
        if method not in {"GET", "POST", "PATCH"} or not path.startswith("/repos/"):
            raise ActionGatewayInvalidRequestError()
        encoded_body: bytes | None = None
        if body is not None:
            try:
                encoded_body = json.dumps(
                    body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
            except (TypeError, UnicodeError, ValueError, OverflowError) as exc:
                raise ActionGatewayInvalidRequestError() from exc
            if len(encoded_body) > GITHUB_ACTION_MAX_REQUEST_BYTES:
                raise ActionGatewayInvalidRequestError()
        headers = {
            "Accept": GITHUB_ACTION_ACCEPT,
            "Authorization": f"Bearer {token}",
            "User-Agent": GITHUB_ACTION_USER_AGENT,
            "X-GitHub-Api-Version": GITHUB_ACTION_API_VERSION,
        }
        if encoded_body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            GITHUB_ACTION_API_BASE_URL + path,
            data=encoded_body,
            headers=headers,
            method=method,
        )
        expected = expected_status or {200}
        try:
            response = self.opener(request, timeout=self.timeout_seconds)
            with response:
                decoded = _json_response_document(response, expected_status=expected)
                if (expect_list and type(decoded) is not list) or (
                    not expect_list and type(decoded) is not dict
                ):
                    raise GitHubActionResponseError()
                return decoded
        except urllib.error.HTTPError as exc:
            status = exc.code
            if mutation:
                if 400 <= status < 500:
                    raise ActionGatewayConnectorError(
                        ActionExecutionOutcomeV1.FAILED_CONFIRMED_NO_MUTATION,
                        "provider_rejected",
                    ) from None
                raise ActionGatewayConnectorError(
                    ActionExecutionOutcomeV1.OUTCOME_UNCERTAIN,
                    "provider_outcome_uncertain",
                ) from None
            raise ActionGatewayConnectorError(
                ActionExecutionOutcomeV1.FAILED_BEFORE_SEND,
                "provider_preflight_failed",
            ) from None
        except ActionGatewayConnectorError:
            raise
        except GitHubActionTransportError, OSError, TimeoutError, urllib.error.URLError:
            code = "provider_outcome_uncertain" if mutation else "provider_unavailable"
            outcome = (
                ActionExecutionOutcomeV1.OUTCOME_UNCERTAIN
                if mutation
                else ActionExecutionOutcomeV1.FAILED_BEFORE_SEND
            )
            raise ActionGatewayConnectorError(outcome, code) from None
        except GitHubActionResponseError:
            code = "provider_outcome_uncertain" if mutation else "provider_invalid_response"
            outcome = (
                ActionExecutionOutcomeV1.OUTCOME_UNCERTAIN
                if mutation
                else ActionExecutionOutcomeV1.FAILED_BEFORE_SEND
            )
            raise ActionGatewayConnectorError(outcome, code) from None

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        token: str,
        body: Mapping[str, object] | None = None,
        expected_status: set[int] | None = None,
        mutation: bool = False,
    ) -> dict[str, object]:
        decoded = self._request_document(
            method,
            path,
            token=token,
            body=body,
            expected_status=expected_status,
            mutation=mutation,
        )
        if type(decoded) is not dict:
            raise ActionGatewayInvalidRequestError()
        return cast(dict[str, object], decoded)

    def _request_list(
        self,
        path: str,
        *,
        token: str,
    ) -> list[object]:
        decoded = self._request_document(
            "GET",
            path,
            token=token,
            expected_status={200},
            expect_list=True,
        )
        if type(decoded) is not list:
            raise ActionGatewayInvalidRequestError()
        return cast(list[object], decoded)


def _remote_identity(
    payload: Mapping[str, object], repository: str, issue_number: int | None
) -> dict[str, object]:
    if "pull_request" in payload:
        raise ActionGatewayConnectorError(
            ActionExecutionOutcomeV1.FAILED_CONFIRMED_NO_MUTATION,
            "pull_request_forbidden",
        )
    result: dict[str, object] = {
        "repository": repository,
        "issue_id": _safe_int(payload.get("id")),
        "issue_node_id": _safe_text(payload.get("node_id"), max_bytes=256),
    }
    number = payload.get("number", issue_number)
    if number is not None:
        result["issue_number"] = _safe_int(number, maximum=2**31 - 1)
    state = payload.get("state")
    if state is not None:
        if state not in {IssueStateV1.OPEN.value, IssueStateV1.CLOSED.value}:
            raise GitHubActionResponseError()
        result["state"] = state
    if issue_number is not None and number != issue_number:
        raise GitHubActionResponseError()
    url = _canonical_issue_url(payload.get("html_url"), repository)
    result["url"] = url
    return result


def _canonical_issue_url(value: object, repository: str) -> str:
    raw_url = _safe_text(value, max_bytes=512)
    if "?" in raw_url:
        raise GitHubActionResponseError()
    url = raw_url.split("#", maxsplit=1)[0]
    expected_prefix = f"https://github.com/{repository}/issues/"
    if not url.casefold().startswith(expected_prefix.casefold()):
        raise GitHubActionResponseError()
    issue_suffix = url[len(expected_prefix) :]
    if not issue_suffix.isdecimal() or not 1 <= int(issue_suffix) <= 2**31 - 1:
        raise GitHubActionResponseError()
    return url


def _marker_matches(items: list[object], marker: str) -> tuple[list[dict[str, object]], bool]:
    if len(items) > GITHUB_ACTION_MAX_RECONCILIATION_ITEMS:
        raise ActionGatewayConnectorError(
            ActionExecutionOutcomeV1.FAILED_BEFORE_SEND,
            "reconciliation_response_invalid",
        )
    matches: list[dict[str, object]] = []
    ambiguous = False
    for item in items:
        if type(item) is not dict:
            raise ActionGatewayConnectorError(
                ActionExecutionOutcomeV1.FAILED_BEFORE_SEND,
                "reconciliation_response_invalid",
            )
        body = item.get("body")
        if type(body) is not str:
            continue
        occurrences = body.count(marker)
        if occurrences == 1:
            matches.append(cast(dict[str, object], item))
        elif occurrences > 1:
            ambiguous = True
    return matches, ambiguous


def _same_repository_identity(
    current: ExactTargetIdentityV1,
    bound: ExactTargetIdentityV1,
) -> bool:
    return (
        current.repository.casefold() == bound.repository.casefold()
        and current.repository_id == bound.repository_id
        and current.repository_node_id == bound.repository_node_id
    )


def _same_issue_identity(
    current: ExactTargetIdentityV1,
    bound: ExactTargetIdentityV1,
) -> bool:
    return (
        current.repository.casefold() == bound.repository.casefold()
        and current.repository_id == bound.repository_id
        and current.repository_node_id == bound.repository_node_id
        and current.issue_number == bound.issue_number
        and current.issue_id == bound.issue_id
        and current.issue_node_id == bound.issue_node_id
        and current.locked == bound.locked
    )


def _comment_identity(
    payload: Mapping[str, object],
    repository: str,
    issue_target: ExactTargetIdentityV1,
) -> dict[str, object]:
    issue_number = issue_target.issue_number
    issue_id = issue_target.issue_id
    issue_node_id = issue_target.issue_node_id
    if issue_number is None or issue_id is None or issue_node_id is None:
        raise GitHubActionResponseError()
    return {
        "repository": repository,
        "issue_number": issue_number,
        "issue_id": issue_id,
        "issue_node_id": issue_node_id,
        "comment_id": _safe_int(payload.get("id")),
        "comment_node_id": _safe_text(payload.get("node_id"), max_bytes=256),
        "url": _canonical_issue_url(payload.get("html_url"), repository),
    }


__all__ = [
    "GITHUB_ACTION_ACCEPT",
    "GITHUB_ACTION_API_BASE_URL",
    "GITHUB_ACTION_API_VERSION",
    "GITHUB_ACTION_MAX_REQUEST_BYTES",
    "GITHUB_ACTION_MAX_RESPONSE_BYTES",
    "GITHUB_ACTION_TIMEOUT_SECONDS",
    "GITHUB_ACTION_USER_AGENT",
    "GitHubActionConfigStatusV1",
    "GitHubActionConfigV1",
    "GitHubActionConfigurationError",
    "GitHubActionResponseError",
    "GitHubActionTransportError",
    "GitHubIssuesActionConnectorV1",
    "load_github_action_config",
]
