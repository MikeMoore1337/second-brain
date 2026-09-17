"""Owner-only Web/API boundary for Stage 20 Personal Agent v1.

The browser carries Mission, Context Pack, Proposal and prepared Stage 19
values only for the current page lifetime.  This module rebuilds the exact
provider-free context from the current Stage 17/18 sources before every
meaningful operation and persists only the reviewed Run projection through
the Stage 20 operational store.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Final, Protocol, cast
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.adapters.advisor.cloudflare_workers_ai import CloudflareWorkersAiAdvisorPort
from second_brain.application.action_gateway import (
    ACTION_GATEWAY_POLICY_FINGERPRINT,
    PreparedExternalActionV1,
)
from second_brain.application.action_gateway_orchestration import (
    ActionGatewayStatusV1,
    ProductionActionGatewayServiceV1,
)
from second_brain.application.execution_feedback_projection import ExecutionItemStateV1
from second_brain.application.personal_agent import (
    AgentContextPackV1,
    AgentMissionV1,
    AgentStage18ItemProjectionV1,
    AgentStage19ActionCapabilityProjectionV1,
    AgentStage19CapabilityProjectionV1,
    PersonalAgentError,
    build_agent_context_pack,
)
from second_brain.application.personal_agent_planner import (
    AgentPlannerError,
    AgentPlannerProviderResultInvalidError,
    AgentPlannerProviderUnavailableError,
    AgentReasoningEnvelopeV1,
    AgentRunProposalV1,
    BuildPersonalAgentRun,
    build_agent_reasoning_envelope,
    parse_agent_run_step,
)
from second_brain.application.personal_agent_run import (
    AgentRunActionNotAllowedError,
    AgentRunError,
    AgentRunInvalidTransitionError,
    AgentRunNotCurrentError,
    AgentRunNotFoundError,
    AgentRunPrepareRequiredError,
    AgentRunReceiptUncertainError,
    AgentRunReconciliationRequiredError,
    AgentRunService,
    AgentRunSnapshotV1,
    AgentRunSourceDriftError,
    AgentRunStage19UnavailableError,
    AgentStage19PreparedActionV1,
)
from second_brain.application.personal_agent_run_store import (
    PersonalAgentRunOperationalStore,
    PersonalAgentRunStoreCorruptError,
    PersonalAgentRunStoreError,
    PersonalAgentRunStoreIdempotencyConflictError,
    PersonalAgentRunStoreStateConflictError,
    PersonalAgentRunStoreUnavailableError,
    derive_personal_agent_run_store_root,
)
from second_brain.application.personal_planning_store import PlanningPlanV1
from second_brain.application.ports import AdvisorPort, CancellationTokenSource
from second_brain.domain.models import parse_uuid7
from second_brain.entrypoints.web.action_gateway import ActionGatewayWebService
from second_brain.entrypoints.web.auth import (
    request_host_is_trusted,
    same_origin_is_trusted,
    trusted_authorities_from_scope,
)
from second_brain.entrypoints.web.execution_feedback import (
    ExecutionFeedbackSourceUnavailableError,
    build_production_execution_feedback_web_service,
)
from second_brain.entrypoints.web.personal_planning import (
    PersonalPlanningWebService,
    build_production_personal_planning_web_service,
)

PERSONAL_AGENT_STATE_PATH: Final[str] = "/api/personal-agent/state"
PERSONAL_AGENT_CONTEXT_PATH: Final[str] = "/api/personal-agent/context"
PERSONAL_AGENT_BUILD_PATH: Final[str] = "/api/personal-agent/build"
PERSONAL_AGENT_REVIEW_PATH: Final[str] = "/api/personal-agent/review"
PERSONAL_AGENT_ACCEPT_PATH: Final[str] = "/api/personal-agent/accept"
PERSONAL_AGENT_START_PATH: Final[str] = "/api/personal-agent/start"
PERSONAL_AGENT_PAUSE_PATH: Final[str] = "/api/personal-agent/pause"
PERSONAL_AGENT_RESUME_PATH: Final[str] = "/api/personal-agent/resume"
PERSONAL_AGENT_ANSWER_PATH: Final[str] = "/api/personal-agent/answer"
PERSONAL_AGENT_CONTINUE_PATH: Final[str] = "/api/personal-agent/continue"
PERSONAL_AGENT_SKIP_PATH: Final[str] = "/api/personal-agent/skip"
PERSONAL_AGENT_ABANDON_PATH: Final[str] = "/api/personal-agent/abandon"
PERSONAL_AGENT_COMPLETE_PATH: Final[str] = "/api/personal-agent/complete"
PERSONAL_AGENT_SUPERSEDE_PATH: Final[str] = "/api/personal-agent/supersede"
PERSONAL_AGENT_PREPARE_PATH: Final[str] = "/api/personal-agent/prepare"
PERSONAL_AGENT_CONFIRM_PATH: Final[str] = "/api/personal-agent/confirm"
PERSONAL_AGENT_RECONCILE_PATH: Final[str] = "/api/personal-agent/reconcile"
PERSONAL_AGENT_COMPENSATION_PREPARE_PATH: Final[str] = "/api/personal-agent/compensation/prepare"
PERSONAL_AGENT_COMPENSATION_CONFIRM_PATH: Final[str] = "/api/personal-agent/compensation/confirm"
PERSONAL_AGENT_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
PERSONAL_AGENT_REQUEST_HEADER_VALUE: Final[str] = "personal-agent-v1"

MAX_RAW_PERSONAL_AGENT_BODY_BYTES: Final[int] = 512 * 1024
MAX_PERSONAL_AGENT_STATE_RESPONSE_BYTES: Final[int] = 512 * 1024
MAX_PERSONAL_AGENT_CONTEXT_RESPONSE_BYTES: Final[int] = 512 * 1024
MAX_PERSONAL_AGENT_BUILD_RESPONSE_BYTES: Final[int] = 256 * 1024
MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES: Final[int] = 256 * 1024

_PERSONAL_AGENT_PATHS: Final[frozenset[str]] = frozenset(
    {
        PERSONAL_AGENT_STATE_PATH,
        PERSONAL_AGENT_CONTEXT_PATH,
        PERSONAL_AGENT_BUILD_PATH,
        PERSONAL_AGENT_REVIEW_PATH,
        PERSONAL_AGENT_ACCEPT_PATH,
        PERSONAL_AGENT_START_PATH,
        PERSONAL_AGENT_PAUSE_PATH,
        PERSONAL_AGENT_RESUME_PATH,
        PERSONAL_AGENT_ANSWER_PATH,
        PERSONAL_AGENT_CONTINUE_PATH,
        PERSONAL_AGENT_SKIP_PATH,
        PERSONAL_AGENT_ABANDON_PATH,
        PERSONAL_AGENT_COMPLETE_PATH,
        PERSONAL_AGENT_SUPERSEDE_PATH,
        PERSONAL_AGENT_PREPARE_PATH,
        PERSONAL_AGENT_CONFIRM_PATH,
        PERSONAL_AGENT_RECONCILE_PATH,
        PERSONAL_AGENT_COMPENSATION_PREPARE_PATH,
        PERSONAL_AGENT_COMPENSATION_CONFIRM_PATH,
    }
)
_SECURITY_HEADER_NAMES: Final[frozenset[bytes]] = frozenset(
    {
        b"host",
        b"origin",
        b"content-type",
        b"content-length",
        b"x-second-brain-request",
    }
)
_API_HEADERS: Final[dict[str, str]] = {
    "Cache-Control": "no-store, private",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; connect-src 'self'; font-src 'self'; "
        "form-action 'none'; frame-ancestors 'none'; img-src 'self'; object-src 'none'; "
        "script-src 'self'; style-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class PersonalAgentWebError(RuntimeError):
    """Base safe transport error for the Stage 20 boundary."""


class PersonalAgentSourceUnavailableError(PersonalAgentWebError):
    """Exact Stage 17/18 source data cannot be read safely."""


class PersonalAgentInvalidRequestError(PersonalAgentWebError):
    """The bounded owner request is not valid."""


class PersonalAgentPreviewMismatchError(PersonalAgentWebError):
    """The owner-visible preview no longer matches the exact request."""


class PersonalAgentStoreUnavailableError(PersonalAgentWebError):
    """The operational Run store is unavailable or corrupt."""


class PersonalAgentEmptyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PersonalAgentMissionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mission: dict[str, object]


class PersonalAgentBuildPayload(PersonalAgentMissionPayload):
    context_pack: dict[str, object]
    provider_preview: dict[str, object]


class PersonalAgentReviewPayload(PersonalAgentMissionPayload):
    proposal: dict[str, object]
    steps: list[dict[str, object]] = Field(min_length=1, max_length=12)


class PersonalAgentAcceptPayload(PersonalAgentMissionPayload):
    proposal: dict[str, object]
    operation_id: StrictStr
    run_id: StrictStr | None = None
    supersedes_run_id: StrictStr | None = None


class PersonalAgentRunPayload(PersonalAgentMissionPayload):
    run_id: StrictStr
    operation_id: StrictStr


class PersonalAgentControlPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: StrictStr
    operation_id: StrictStr


class PersonalAgentAnswerPayload(PersonalAgentRunPayload):
    answer: StrictStr


class PersonalAgentSkipPayload(PersonalAgentRunPayload):
    reason: StrictStr


class PersonalAgentPreparedPayload(PersonalAgentRunPayload):
    prepared: dict[str, object]
    confirmation_token: StrictStr


class PersonalAgentReconcilePayload(PersonalAgentRunPayload):
    prepared: dict[str, object]


class PersonalAgentCompensationPreparePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: StrictStr
    parent_receipt_id: StrictStr
    operation_id: StrictStr


class PersonalAgentCompensationConfirmPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: StrictStr
    parent_receipt_id: StrictStr
    operation_id: StrictStr
    prepared: dict[str, object]
    confirmation_token: StrictStr


class PersonalAgentExecutionProjectionService(Protocol):
    def agent_item_states(self, plan: PlanningPlanV1) -> Mapping[str, ExecutionItemStateV1]: ...


class PersonalAgentWebService(Protocol):
    def state(self) -> dict[str, object]: ...

    def context(self, mission: AgentMissionV1) -> dict[str, object]: ...

    def build(
        self,
        mission: AgentMissionV1,
        context_pack: Mapping[str, object],
        provider_preview: Mapping[str, object],
    ) -> dict[str, object]: ...

    def review(
        self,
        mission: AgentMissionV1,
        proposal: Mapping[str, object],
        steps: tuple[dict[str, object], ...],
    ) -> dict[str, object]: ...

    def accept(
        self,
        mission: AgentMissionV1,
        proposal: Mapping[str, object],
        *,
        operation_id: str,
        run_id: str | None,
        supersedes_run_id: str | None,
    ) -> dict[str, object]: ...

    def start(
        self, mission: AgentMissionV1, run_id: str, *, operation_id: str
    ) -> dict[str, object]: ...

    def pause(self, run_id: str, *, operation_id: str) -> dict[str, object]: ...

    def resume(
        self, mission: AgentMissionV1, run_id: str, *, operation_id: str
    ) -> dict[str, object]: ...

    def answer(
        self, mission: AgentMissionV1, run_id: str, answer: str, *, operation_id: str
    ) -> dict[str, object]: ...

    def continue_run(
        self, mission: AgentMissionV1, run_id: str, *, operation_id: str
    ) -> dict[str, object]: ...

    def skip(
        self, mission: AgentMissionV1, run_id: str, reason: str, *, operation_id: str
    ) -> dict[str, object]: ...

    def abandon(self, run_id: str, *, operation_id: str) -> dict[str, object]: ...

    def complete(self, run_id: str, *, operation_id: str) -> dict[str, object]: ...

    def supersede(self, run_id: str, *, operation_id: str) -> dict[str, object]: ...

    def prepare(
        self, mission: AgentMissionV1, run_id: str, *, operation_id: str
    ) -> dict[str, object]: ...

    def confirm(
        self,
        mission: AgentMissionV1,
        run_id: str,
        prepared: Mapping[str, object],
        confirmation_token: str,
        *,
        operation_id: str,
    ) -> dict[str, object]: ...

    def reconcile(
        self,
        mission: AgentMissionV1,
        run_id: str,
        prepared: Mapping[str, object],
        *,
        operation_id: str,
    ) -> dict[str, object]: ...

    def compensation_prepare(
        self, run_id: str, parent_receipt_id: str, *, operation_id: str
    ) -> dict[str, object]: ...

    def compensation_confirm(
        self,
        run_id: str,
        parent_receipt_id: str,
        prepared: Mapping[str, object],
        confirmation_token: str,
        *,
        operation_id: str,
    ) -> dict[str, object]: ...


def _header(scope: Scope, name: bytes) -> tuple[bool, str | None]:
    values = [
        value.decode("latin-1")
        for header_name, value in scope.get("headers", [])
        if header_name.lower() == name
    ]
    return bool(values), values[0] if values else None


def _json_content_type(value: str | None) -> bool:
    if value is None:
        return False
    parts = value.split(";")
    if parts[0].strip().casefold() != "application/json":
        return False
    if len(parts) == 1:
        return True
    if len(parts) != 2:
        return False
    name, separator, parameter = parts[1].partition("=")
    if separator == "" or name.strip().casefold() != "charset":
        return False
    normalized = parameter.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] == '"':
        normalized = normalized[1:-1]
    return normalized.casefold() == "utf-8"


def _error_response(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=_API_HEADERS,
    )


def _invalid_request() -> JSONResponse:
    return _error_response("INVALID_REQUEST", "Запрос агента некорректен.", 400)


def _too_large() -> JSONResponse:
    return _error_response("INVALID_REQUEST", "Запрос агента слишком велик.", 413)


def _method_not_allowed() -> JSONResponse:
    response = _invalid_request()
    response.status_code = 405
    response.headers["Allow"] = "POST"
    return response


async def _send(response: Response, scope: Scope, receive: Receive, send: Send) -> None:
    await response(scope, receive, send)


class PersonalAgentRequestBoundaryMiddleware:
    """Fail-closed owner, Host, Origin, purpose and raw JSON boundary."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _PERSONAL_AGENT_PATHS:
            await self.app(scope, receive, send)
            return
        if scope.get("method") != "POST":
            await _send(_method_not_allowed(), scope, receive, send)
            return
        header_names = [name.lower() for name, _ in scope.get("headers", [])]
        if any(header_names.count(name) > 1 for name in _SECURITY_HEADER_NAMES):
            await _send(_invalid_request(), scope, receive, send)
            return
        host_present, _ = _header(scope, b"host")
        origin_present, origin = _header(scope, b"origin")
        _, purpose = _header(scope, b"x-second-brain-request")
        _, content_type = _header(scope, b"content-type")
        authorities = trusted_authorities_from_scope(scope)
        if (
            not host_present
            or not request_host_is_trusted(scope, authorities)
            or not origin_present
            or origin is None
            or not same_origin_is_trusted(scope, origin, authorities)
            or purpose != PERSONAL_AGENT_REQUEST_HEADER_VALUE
            or not _json_content_type(content_type)
        ):
            await _send(_invalid_request(), scope, receive, send)
            return
        _, content_length = _header(scope, b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length.strip())
            except ValueError:
                await _send(_invalid_request(), scope, receive, send)
                return
            if declared < 0:
                await _send(_invalid_request(), scope, receive, send)
                return
            if declared > MAX_RAW_PERSONAL_AGENT_BODY_BYTES:
                await _send(_too_large(), scope, receive, send)
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                await _send(_invalid_request(), scope, receive, send)
                return
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                await _send(_too_large(), scope, receive, send)
                return
            body.extend(chunk)
            if len(body) > MAX_RAW_PERSONAL_AGENT_BODY_BYTES:
                await _send(_too_large(), scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(value)


async def _read_payload(request: Request, payload_type: type[BaseModel]) -> BaseModel:
    raw = await request.body()
    if not raw or len(raw) > MAX_RAW_PERSONAL_AGENT_BODY_BYTES:
        raise ValueError
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError:
        raise ValueError from None
    return payload_type.model_validate(decoded, strict=True)


def _json_response(body: dict[str, object], *, max_bytes: int) -> Response:
    try:
        raw = json.dumps(
            body,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except TypeError, ValueError, UnicodeError:
        return _error_response("SOURCE_UNAVAILABLE", "Состояние агента недоступно.", 503)
    if len(raw) > max_bytes:
        return _error_response("INVALID_REQUEST", "Ответ агента слишком велик.", 503)
    return Response(raw, media_type="application/json", headers=_API_HEADERS)


def _strict_mapping(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise PersonalAgentInvalidRequestError()
    result = dict(value)
    if set(result) != keys:
        raise PersonalAgentInvalidRequestError()
    return result


def _mission(value: object) -> AgentMissionV1:
    try:
        return AgentMissionV1.from_dict(value)
    except (PersonalAgentError, TypeError, ValueError, RecursionError) as exc:
        raise PersonalAgentInvalidRequestError() from exc


def _proposal(value: object) -> AgentRunProposalV1:
    try:
        return AgentRunProposalV1.from_dict(value)
    except (PersonalAgentError, TypeError, ValueError, RecursionError) as exc:
        raise PersonalAgentInvalidRequestError() from exc


def _prepared(value: object) -> AgentStage19PreparedActionV1:
    try:
        return AgentStage19PreparedActionV1(PreparedExternalActionV1.from_dict(value))
    except (PersonalAgentError, TypeError, ValueError, RecursionError) as exc:
        raise PersonalAgentInvalidRequestError() from exc


def _uuid(value: str) -> UUID:
    try:
        return parse_uuid7(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PersonalAgentInvalidRequestError() from exc


def _preview_body(envelope: AgentReasoningEnvelopeV1) -> dict[str, object]:
    canonical = envelope.canonical_bytes
    try:
        canonical_json = canonical.decode("utf-8")
        assistant_envelope = json.loads(canonical_json)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise PersonalAgentInvalidRequestError() from exc
    if type(assistant_envelope) is not dict:
        raise PersonalAgentInvalidRequestError()
    return {
        "mission_fingerprint": envelope.mission_fingerprint,
        "context_pack_fingerprint": envelope.context_pack_fingerprint,
        "canonical_json": canonical_json,
        "canonical_bytes_sha256": hashlib.sha256(canonical).hexdigest(),
        "assistant_envelope": assistant_envelope,
    }


def _assert_preview(expected: Mapping[str, object], supplied: Mapping[str, object]) -> None:
    expected_data = dict(expected)
    supplied_data = _strict_mapping(
        supplied,
        {
            "mission_fingerprint",
            "context_pack_fingerprint",
            "canonical_json",
            "canonical_bytes_sha256",
            "assistant_envelope",
        },
    )
    if supplied_data != expected_data:
        raise PersonalAgentPreviewMismatchError()


def _safe_stage19(status: ActionGatewayStatusV1) -> AgentStage19CapabilityProjectionV1:
    if type(status) is not ActionGatewayStatusV1:
        raise PersonalAgentSourceUnavailableError()
    raw = status.as_dict()
    raw_catalog = raw.get("action_catalog")
    if type(raw_catalog) is not list:
        raise PersonalAgentSourceUnavailableError()
    try:
        catalog = tuple(
            AgentStage19ActionCapabilityProjectionV1.from_dict(item) for item in raw_catalog
        )
        return AgentStage19CapabilityProjectionV1(
            contract=cast(str, raw.get("contract")),
            connector=cast(str, raw.get("connector")),
            policy_id=cast(str, raw.get("policy_id")),
            policy_fingerprint=ACTION_GATEWAY_POLICY_FINGERPRINT,
            status=cast(str, raw.get("status")),
            configured=cast(bool, raw.get("configured")),
            repositories=tuple(cast(str, item) for item in cast(list[object], raw["repositories"])),
            action_catalog=catalog,
            owner_confirmation_required=cast(bool, raw.get("owner_confirmation_required")),
            background_execution=cast(bool, raw.get("background_execution")),
        )
    except (PersonalAgentError, TypeError, ValueError, KeyError, RecursionError) as exc:
        raise PersonalAgentSourceUnavailableError() from exc


def _stage18_body(value: object) -> dict[str, object]:
    if type(value) is not AgentStage18ItemProjectionV1:
        raise PersonalAgentSourceUnavailableError()
    return value.as_dict()


def _snapshot_body(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if type(value) is not AgentRunSnapshotV1:
        raise PersonalAgentSourceUnavailableError()
    return value.as_dict()


@dataclass(slots=True)
class ProductionPersonalAgentWebService:
    """Compose Stage 17/18/19 projections without granting Stage20 authority."""

    planning_service: PersonalPlanningWebService
    execution_service: PersonalAgentExecutionProjectionService
    action_gateway_service: ActionGatewayWebService
    advisor: AdvisorPort
    run_store: PersonalAgentRunOperationalStore | None = None
    run_service: AgentRunService | None = None
    env_file: Path | None = None
    vault_path_override: str | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def _stage19(self) -> AgentStage19CapabilityProjectionV1:
        try:
            return _safe_stage19(self.action_gateway_service.status())
        except PersonalAgentWebError:
            raise
        except Exception as exc:
            raise PersonalAgentSourceUnavailableError() from exc

    def _run(self) -> AgentRunService:
        if self.run_service is not None:
            return self.run_service
        store = self.run_store
        if store is None:
            root = derive_personal_agent_run_store_root(self.env_file)
            if root is None:
                raise PersonalAgentStoreUnavailableError()
            try:
                store = PersonalAgentRunOperationalStore(
                    root,
                    vault_root=(
                        None
                        if self.vault_path_override is None
                        else Path(self.vault_path_override).expanduser()
                    ),
                    clock=self.clock,
                )
            except PersonalAgentRunStoreError as exc:
                raise PersonalAgentStoreUnavailableError() from exc
            self.run_store = store
        self.run_service = AgentRunService(
            store,
            stage19=self.action_gateway_service,
            clock=self.clock,
        )
        return self.run_service

    def _current_plan(self) -> PlanningPlanV1:
        try:
            plan = self.planning_service.current_plan()
        except Exception as exc:
            raise PersonalAgentSourceUnavailableError() from exc
        if type(plan) is not PlanningPlanV1:
            raise PersonalAgentSourceUnavailableError()
        return plan

    def _pack(self, mission: AgentMissionV1) -> AgentContextPackV1:
        plan = self._current_plan()
        try:
            raw_states = self.execution_service.agent_item_states(plan)
        except ExecutionFeedbackSourceUnavailableError, PersonalAgentWebError:
            raise PersonalAgentSourceUnavailableError() from None
        if not isinstance(raw_states, Mapping):
            raise PersonalAgentSourceUnavailableError()
        try:
            states = dict(raw_states)
            return build_agent_context_pack(
                mission,
                plan,
                states,
                self._stage19(),
                current_planning_plan=plan,
            )
        except PersonalAgentError:
            raise
        except (TypeError, ValueError, KeyError, RecursionError) as exc:
            raise PersonalAgentSourceUnavailableError() from exc

    def state(self) -> dict[str, object]:
        plan: PlanningPlanV1 | None
        try:
            plan = self.planning_service.current_plan()
        except Exception as exc:
            raise PersonalAgentSourceUnavailableError() from exc
        stage19 = self._stage19()
        execution: list[dict[str, object]] = []
        if type(plan) is PlanningPlanV1:
            try:
                states = self.execution_service.agent_item_states(plan)
                for item_id in plan.selected_item_ids:
                    state = states.get(item_id)
                    if state is None:
                        raise PersonalAgentSourceUnavailableError()
                    execution.append(
                        {
                            "item_id": item_id,
                            "projection": _stage18_body(
                                AgentStage18ItemProjectionV1.from_execution_state(state)
                            ),
                        }
                    )
            except PersonalAgentWebError:
                raise
            except (PersonalAgentError, ExecutionFeedbackSourceUnavailableError) as exc:
                raise PersonalAgentSourceUnavailableError() from exc
            except (TypeError, ValueError, KeyError, RecursionError) as exc:
                raise PersonalAgentSourceUnavailableError() from exc
        run_service = self._run()
        try:
            current_run = run_service.current()
            history = (
                ()
                if current_run is None or self.run_store is None
                else self.run_store.history(current_run.run_id)
            )
        except PersonalAgentRunStoreError as exc:
            raise PersonalAgentStoreUnavailableError() from exc
        return {
            "web_contract": "personal_agent_state_web_v1",
            "current_plan": None if plan is None else plan.as_dict(),
            "execution_items": execution,
            "stage19": stage19.as_dict(),
            "current_run": _snapshot_body(current_run),
            "run_history": [item.as_dict() for item in history],
            "caveats": ([] if plan is not None else ["current_accepted_plan_missing"]),
        }

    def context(self, mission: AgentMissionV1) -> dict[str, object]:
        pack = self._pack(mission)
        envelope = build_agent_reasoning_envelope(pack)
        return {
            "web_contract": "personal_agent_context_web_v1",
            "context_pack": pack.as_dict(),
            "provider_preview": _preview_body(envelope),
            "current_plan": self._current_plan().as_dict(),
        }

    def build(
        self,
        mission: AgentMissionV1,
        context_pack: Mapping[str, object],
        provider_preview: Mapping[str, object],
    ) -> dict[str, object]:
        pack = self._pack(mission)
        if (
            not isinstance(context_pack, Mapping)
            or context_pack.get("pack_fingerprint") != pack.fingerprint
        ):
            raise PersonalAgentPreviewMismatchError()
        envelope = build_agent_reasoning_envelope(pack)
        expected_preview = _preview_body(envelope)
        _assert_preview(expected_preview, provider_preview)
        cancellation = CancellationTokenSource()
        try:
            proposal = BuildPersonalAgentRun(self.advisor).build(
                pack,
                cancellation=cancellation.token,
            )
        except AgentPlannerError:
            raise
        except Exception as exc:
            raise PersonalAgentSourceUnavailableError() from exc
        return {
            "web_contract": "personal_agent_build_web_v1",
            "context_pack": pack.as_dict(),
            "provider_preview": expected_preview,
            "proposal": proposal.as_dict(),
        }

    def review(
        self,
        mission: AgentMissionV1,
        proposal: Mapping[str, object],
        steps: tuple[dict[str, object], ...],
    ) -> dict[str, object]:
        pack = self._pack(mission)
        parsed_proposal = _proposal(proposal)
        try:
            parsed_steps = tuple(parse_agent_run_step(item) for item in steps)
            reviewed = self._run().review(pack, parsed_proposal, steps=parsed_steps)
        except PersonalAgentError, AgentRunError:
            raise
        except (TypeError, ValueError, RecursionError) as exc:
            raise PersonalAgentInvalidRequestError() from exc
        return {"web_contract": "personal_agent_review_web_v1", "proposal": reviewed.as_dict()}

    def accept(
        self,
        mission: AgentMissionV1,
        proposal: Mapping[str, object],
        *,
        operation_id: str,
        run_id: str | None,
        supersedes_run_id: str | None,
    ) -> dict[str, object]:
        pack = self._pack(mission)
        try:
            snapshot = self._run().accept(
                pack,
                _proposal(proposal),
                operation_id=operation_id,
                run_id=run_id,
                supersedes_run_id=supersedes_run_id,
            )
        except PersonalAgentError, AgentRunError:
            raise
        return {"web_contract": "personal_agent_accept_web_v1", "run": snapshot.as_dict()}

    def start(
        self, mission: AgentMissionV1, run_id: str, *, operation_id: str
    ) -> dict[str, object]:
        snapshot = self._run().start(self._pack(mission), run_id, operation_id=operation_id)
        return {"web_contract": "personal_agent_start_web_v1", "run": snapshot.as_dict()}

    def pause(self, run_id: str, *, operation_id: str) -> dict[str, object]:
        snapshot = self._run().pause(run_id, operation_id=operation_id)
        return {"web_contract": "personal_agent_pause_web_v1", "run": snapshot.as_dict()}

    def resume(
        self, mission: AgentMissionV1, run_id: str, *, operation_id: str
    ) -> dict[str, object]:
        snapshot = self._run().resume(self._pack(mission), run_id, operation_id=operation_id)
        return {"web_contract": "personal_agent_resume_web_v1", "run": snapshot.as_dict()}

    def answer(
        self, mission: AgentMissionV1, run_id: str, answer: str, *, operation_id: str
    ) -> dict[str, object]:
        snapshot = self._run().answer(
            self._pack(mission), run_id, answer, operation_id=operation_id
        )
        return {"web_contract": "personal_agent_answer_web_v1", "run": snapshot.as_dict()}

    def continue_run(
        self, mission: AgentMissionV1, run_id: str, *, operation_id: str
    ) -> dict[str, object]:
        snapshot = self._run().continue_run(self._pack(mission), run_id, operation_id=operation_id)
        return {"web_contract": "personal_agent_continue_web_v1", "run": snapshot.as_dict()}

    def skip(
        self, mission: AgentMissionV1, run_id: str, reason: str, *, operation_id: str
    ) -> dict[str, object]:
        snapshot = self._run().skip(self._pack(mission), run_id, reason, operation_id=operation_id)
        return {"web_contract": "personal_agent_skip_web_v1", "run": snapshot.as_dict()}

    def abandon(self, run_id: str, *, operation_id: str) -> dict[str, object]:
        snapshot = self._run().abandon(run_id, operation_id=operation_id)
        return {"web_contract": "personal_agent_abandon_web_v1", "run": snapshot.as_dict()}

    def complete(self, run_id: str, *, operation_id: str) -> dict[str, object]:
        snapshot = self._run().complete(run_id, operation_id=operation_id)
        return {"web_contract": "personal_agent_complete_web_v1", "run": snapshot.as_dict()}

    def supersede(self, run_id: str, *, operation_id: str) -> dict[str, object]:
        snapshot = self._run().supersede(run_id, operation_id=operation_id)
        return {"web_contract": "personal_agent_supersede_web_v1", "run": snapshot.as_dict()}

    def prepare(
        self, mission: AgentMissionV1, run_id: str, *, operation_id: str
    ) -> dict[str, object]:
        prepared = self._run().prepare_stage19_action(
            self._pack(mission), run_id, operation_id=operation_id
        )
        token = self._run().issue_stage19_confirmation(prepared)
        snapshot = self._run().latest(run_id)
        return {
            "web_contract": "personal_agent_prepare_web_v1",
            "run": snapshot.as_dict(),
            "prepared": prepared.as_dict(),
            "confirmation_token": token,
        }

    def confirm(
        self,
        mission: AgentMissionV1,
        run_id: str,
        prepared: Mapping[str, object],
        confirmation_token: str,
        *,
        operation_id: str,
    ) -> dict[str, object]:
        snapshot = self._run().execute_stage19_action(
            self._pack(mission),
            run_id,
            _prepared(prepared),
            confirmation_token,
            operation_id=operation_id,
        )
        return {"web_contract": "personal_agent_confirm_web_v1", "run": snapshot.as_dict()}

    def reconcile(
        self,
        mission: AgentMissionV1,
        run_id: str,
        prepared: Mapping[str, object],
        *,
        operation_id: str,
    ) -> dict[str, object]:
        snapshot = self._run().reconcile_stage19_action(
            self._pack(mission),
            run_id,
            _prepared(prepared),
            operation_id=operation_id,
        )
        return {"web_contract": "personal_agent_reconcile_web_v1", "run": snapshot.as_dict()}

    def compensation_prepare(
        self, run_id: str, parent_receipt_id: str, *, operation_id: str
    ) -> dict[str, object]:
        prepared = self._run().prepare_compensation(
            run_id,
            _uuid(parent_receipt_id),
            operation_id=operation_id,
        )
        token = self._run().issue_stage19_confirmation(prepared)
        return {
            "web_contract": "personal_agent_compensation_prepare_web_v1",
            "prepared": prepared.as_dict(),
            "confirmation_token": token,
        }

    def compensation_confirm(
        self,
        run_id: str,
        parent_receipt_id: str,
        prepared: Mapping[str, object],
        confirmation_token: str,
        *,
        operation_id: str,
    ) -> dict[str, object]:
        snapshot = self._run().execute_compensation(
            run_id,
            _prepared(prepared),
            confirmation_token,
            _uuid(parent_receipt_id),
            operation_id=operation_id,
        )
        return {
            "web_contract": "personal_agent_compensation_confirm_web_v1",
            "run": snapshot.as_dict(),
        }


def build_production_personal_agent_web_service(
    *,
    planning_service: PersonalPlanningWebService | None = None,
    execution_service: PersonalAgentExecutionProjectionService | None = None,
    action_gateway_service: ActionGatewayWebService | None = None,
    advisor: AdvisorPort | None = None,
    run_store: PersonalAgentRunOperationalStore | None = None,
    run_service: AgentRunService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ProductionPersonalAgentWebService:
    actual_planning = planning_service or build_production_personal_planning_web_service(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    actual_execution = execution_service or build_production_execution_feedback_web_service(
        planning_service=actual_planning,
        env_file=env_file,
        vault_path_override=vault_path_override,
        clock=clock,
    )
    return ProductionPersonalAgentWebService(
        planning_service=actual_planning,
        execution_service=actual_execution,
        action_gateway_service=action_gateway_service
        or ProductionActionGatewayServiceV1(
            env_file=env_file,
            vault_path_override=vault_path_override,
            clock=clock or (lambda: datetime.now(UTC)),
        ),
        advisor=advisor or CloudflareWorkersAiAdvisorPort(),
        run_store=run_store,
        run_service=run_service,
        env_file=env_file,
        vault_path_override=vault_path_override,
        clock=clock or (lambda: datetime.now(UTC)),
    )


_ERRORS: Final[dict[type[BaseException], tuple[str, str, int]]] = {
    PersonalAgentPreviewMismatchError: (
        "SOURCE_MISMATCH",
        "Предпросмотр устарел. Собери точный предпросмотр заново.",
        409,
    ),
    PersonalAgentSourceUnavailableError: (
        "SOURCE_UNAVAILABLE",
        "Точные источники агента сейчас недоступны.",
        503,
    ),
    PersonalAgentInvalidRequestError: (
        "INVALID_REQUEST",
        "Запрос агента некорректен.",
        400,
    ),
    PersonalAgentStoreUnavailableError: (
        "STORE_UNAVAILABLE",
        "Операционное состояние агента недоступно.",
        503,
    ),
    PersonalAgentRunStoreCorruptError: (
        "STORE_CORRUPT",
        "Операционное состояние агента повреждено и требует проверки.",
        503,
    ),
    PersonalAgentRunStoreUnavailableError: (
        "STORE_UNAVAILABLE",
        "Операционное состояние агента недоступно.",
        503,
    ),
    PersonalAgentRunStoreStateConflictError: (
        "RUN_CONFLICT",
        "Другой Run уже занимает текущее состояние.",
        409,
    ),
    PersonalAgentRunStoreIdempotencyConflictError: (
        "RUN_CONFLICT",
        "Эта операция уже использована с другим содержимым.",
        409,
    ),
    AgentRunNotFoundError: ("RUN_NOT_CURRENT", "Run не найден.", 404),
    AgentRunNotCurrentError: ("RUN_NOT_CURRENT", "Этот Run больше не текущий.", 409),
    AgentRunInvalidTransitionError: (
        "INVALID_TRANSITION",
        "Это действие сейчас недоступно для Run.",
        409,
    ),
    AgentRunActionNotAllowedError: (
        "ACTION_NOT_ALLOWED",
        "Действие не входит в разрешённый набор Run.",
        403,
    ),
    AgentRunPrepareRequiredError: (
        "ACTION_PREPARE_REQUIRED",
        "Сначала подготовь точный предпросмотр действия.",
        409,
    ),
    AgentRunStage19UnavailableError: (
        "STAGE19_UNAVAILABLE",
        "Контролируемое внешнее действие сейчас недоступно.",
        503,
    ),
    AgentRunReceiptUncertainError: (
        "RECEIPT_UNCERTAIN",
        "Результат действия не подтверждён. Повторная запись не запускается.",
        409,
    ),
    AgentRunReconciliationRequiredError: (
        "RECONCILIATION_REQUIRED",
        "Сначала отдельно проверь результат действия.",
        409,
    ),
    AgentRunSourceDriftError: (
        "SOURCE_STALE",
        "Источник изменился. Требуется явное обновление Run.",
        409,
    ),
    AgentPlannerProviderUnavailableError: (
        "PROVIDER_UNAVAILABLE",
        "Независимый совет сейчас недоступен.",
        503,
    ),
    AgentPlannerProviderResultInvalidError: (
        "PROPOSAL_INVALID",
        "Предложение агента не прошло строгую проверку.",
        502,
    ),
}


def _exception_response(error: BaseException) -> JSONResponse:
    for error_type, result in _ERRORS.items():
        if isinstance(error, error_type):
            return _error_response(*result)
    if isinstance(error, AgentPlannerError):
        return _error_response(
            "PROPOSAL_INVALID",
            "Предложение агента не прошло строгую проверку.",
            502,
        )
    if isinstance(error, PersonalAgentError | AgentRunError):
        return _error_response("INVALID_REQUEST", "Запрос агента некорректен.", 400)
    return _error_response("SOURCE_UNAVAILABLE", "Состояние агента недоступно.", 503)


def _mission_from_payload(payload: PersonalAgentMissionPayload) -> AgentMissionV1:
    return _mission(payload.mission)


def _run_result(
    service: PersonalAgentWebService,
    method: Callable[[], dict[str, object]],
    *,
    max_bytes: int = MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES,
) -> Response:
    del service
    return _json_response(method(), max_bytes=max_bytes)


def install_personal_agent_routes(
    app: FastAPI,
    *,
    service: PersonalAgentWebService | None = None,
    planning_service: PersonalPlanningWebService | None = None,
    execution_service: PersonalAgentExecutionProjectionService | None = None,
    action_gateway_service: ActionGatewayWebService | None = None,
    advisor: AdvisorPort | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install additive owner-only Stage 20 routes."""

    actual_service = service or build_production_personal_agent_web_service(
        planning_service=planning_service,
        execution_service=execution_service,
        action_gateway_service=action_gateway_service,
        advisor=advisor,
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    app.add_middleware(PersonalAgentRequestBoundaryMiddleware)

    @app.post(PERSONAL_AGENT_STATE_PATH, include_in_schema=False)
    async def state_endpoint(request: Request) -> Response:
        try:
            await _read_payload(request, PersonalAgentEmptyPayload)
            body = await run_in_threadpool(actual_service.state)
            return _json_response(body, max_bytes=MAX_PERSONAL_AGENT_STATE_RESPONSE_BYTES)
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_AGENT_CONTEXT_PATH, include_in_schema=False)
    async def context_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalAgentMissionPayload,
                await _read_payload(request, PersonalAgentMissionPayload),
            )
            body = await run_in_threadpool(actual_service.context, _mission_from_payload(payload))
            return _json_response(body, max_bytes=MAX_PERSONAL_AGENT_CONTEXT_RESPONSE_BYTES)
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_AGENT_BUILD_PATH, include_in_schema=False)
    async def build_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalAgentBuildPayload,
                await _read_payload(request, PersonalAgentBuildPayload),
            )
            body = await run_in_threadpool(
                actual_service.build,
                _mission(payload.mission),
                payload.context_pack,
                payload.provider_preview,
            )
            return _json_response(body, max_bytes=MAX_PERSONAL_AGENT_BUILD_RESPONSE_BYTES)
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_AGENT_REVIEW_PATH, include_in_schema=False)
    async def review_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalAgentReviewPayload,
                await _read_payload(request, PersonalAgentReviewPayload),
            )
            body = await run_in_threadpool(
                actual_service.review,
                _mission(payload.mission),
                payload.proposal,
                tuple(payload.steps),
            )
            return _json_response(body, max_bytes=MAX_PERSONAL_AGENT_BUILD_RESPONSE_BYTES)
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_AGENT_ACCEPT_PATH, include_in_schema=False)
    async def accept_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalAgentAcceptPayload,
                await _read_payload(request, PersonalAgentAcceptPayload),
            )
            body = await run_in_threadpool(
                actual_service.accept,
                _mission(payload.mission),
                payload.proposal,
                operation_id=payload.operation_id,
                run_id=payload.run_id,
                supersedes_run_id=payload.supersedes_run_id,
            )
            return _json_response(body, max_bytes=MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES)
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_AGENT_START_PATH, include_in_schema=False)
    async def start_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalAgentRunPayload, await _read_payload(request, PersonalAgentRunPayload)
            )
            body = await run_in_threadpool(
                actual_service.start,
                _mission_from_payload(payload),
                payload.run_id,
                operation_id=payload.operation_id,
            )
            return _json_response(body, max_bytes=MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES)
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_AGENT_PAUSE_PATH, include_in_schema=False)
    async def pause_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalAgentRunPayload, await _read_payload(request, PersonalAgentRunPayload)
            )
            body = await run_in_threadpool(
                actual_service.pause, payload.run_id, operation_id=payload.operation_id
            )
            return _json_response(body, max_bytes=MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES)
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_AGENT_RESUME_PATH, include_in_schema=False)
    async def resume_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalAgentRunPayload, await _read_payload(request, PersonalAgentRunPayload)
            )
            body = await run_in_threadpool(
                actual_service.resume,
                _mission_from_payload(payload),
                payload.run_id,
                operation_id=payload.operation_id,
            )
            return _json_response(body, max_bytes=MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES)
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_AGENT_ANSWER_PATH, include_in_schema=False)
    async def answer_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalAgentAnswerPayload, await _read_payload(request, PersonalAgentAnswerPayload)
            )
            body = await run_in_threadpool(
                actual_service.answer,
                _mission_from_payload(payload),
                payload.run_id,
                payload.answer,
                operation_id=payload.operation_id,
            )
            return _json_response(body, max_bytes=MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES)
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_AGENT_CONTINUE_PATH, include_in_schema=False)
    async def continue_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalAgentRunPayload, await _read_payload(request, PersonalAgentRunPayload)
            )
            body = await run_in_threadpool(
                actual_service.continue_run,
                _mission_from_payload(payload),
                payload.run_id,
                operation_id=payload.operation_id,
            )
            return _json_response(body, max_bytes=MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES)
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_AGENT_SKIP_PATH, include_in_schema=False)
    async def skip_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalAgentSkipPayload, await _read_payload(request, PersonalAgentSkipPayload)
            )
            body = await run_in_threadpool(
                actual_service.skip,
                _mission_from_payload(payload),
                payload.run_id,
                payload.reason,
                operation_id=payload.operation_id,
            )
            return _json_response(body, max_bytes=MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES)
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    def install_simple(
        path: str,
        method: Callable[..., dict[str, object]],
        payload_type: type[BaseModel],
    ) -> None:
        @app.post(path, include_in_schema=False)
        async def endpoint(request: Request) -> Response:
            try:
                payload = cast(
                    PersonalAgentControlPayload, await _read_payload(request, payload_type)
                )
                body = await run_in_threadpool(
                    partial(method, payload.run_id, operation_id=payload.operation_id),
                )
                return _json_response(body, max_bytes=MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES)
            except ValidationError, ValueError, TypeError, json.JSONDecodeError:
                return _invalid_request()
            except Exception as error:
                return _exception_response(error)

    install_simple(PERSONAL_AGENT_ABANDON_PATH, actual_service.abandon, PersonalAgentControlPayload)
    install_simple(
        PERSONAL_AGENT_COMPLETE_PATH, actual_service.complete, PersonalAgentControlPayload
    )
    install_simple(
        PERSONAL_AGENT_SUPERSEDE_PATH, actual_service.supersede, PersonalAgentControlPayload
    )

    @app.post(PERSONAL_AGENT_PREPARE_PATH, include_in_schema=False)
    async def prepare_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalAgentRunPayload, await _read_payload(request, PersonalAgentRunPayload)
            )
            body = await run_in_threadpool(
                actual_service.prepare,
                _mission_from_payload(payload),
                payload.run_id,
                operation_id=payload.operation_id,
            )
            return _json_response(body, max_bytes=MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES)
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_AGENT_CONFIRM_PATH, include_in_schema=False)
    async def confirm_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalAgentPreparedPayload,
                await _read_payload(request, PersonalAgentPreparedPayload),
            )
            body = await run_in_threadpool(
                actual_service.confirm,
                _mission_from_payload(payload),
                payload.run_id,
                payload.prepared,
                payload.confirmation_token,
                operation_id=payload.operation_id,
            )
            return _json_response(body, max_bytes=MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES)
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_AGENT_RECONCILE_PATH, include_in_schema=False)
    async def reconcile_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalAgentReconcilePayload,
                await _read_payload(request, PersonalAgentReconcilePayload),
            )
            body = await run_in_threadpool(
                actual_service.reconcile,
                _mission_from_payload(payload),
                payload.run_id,
                payload.prepared,
                operation_id=payload.operation_id,
            )
            return _json_response(body, max_bytes=MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES)
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_AGENT_COMPENSATION_PREPARE_PATH, include_in_schema=False)
    async def compensation_prepare_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalAgentCompensationPreparePayload,
                await _read_payload(request, PersonalAgentCompensationPreparePayload),
            )
            body = await run_in_threadpool(
                actual_service.compensation_prepare,
                payload.run_id,
                payload.parent_receipt_id,
                operation_id=payload.operation_id,
            )
            return _json_response(
                body,
                max_bytes=MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)

    @app.post(PERSONAL_AGENT_COMPENSATION_CONFIRM_PATH, include_in_schema=False)
    async def compensation_confirm_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                PersonalAgentCompensationConfirmPayload,
                await _read_payload(request, PersonalAgentCompensationConfirmPayload),
            )
            body = await run_in_threadpool(
                actual_service.compensation_confirm,
                payload.run_id,
                payload.parent_receipt_id,
                payload.prepared,
                payload.confirmation_token,
                operation_id=payload.operation_id,
            )
            return _json_response(
                body,
                max_bytes=MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES,
            )
        except ValidationError, ValueError, TypeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _exception_response(error)


__all__ = [
    "MAX_PERSONAL_AGENT_BUILD_RESPONSE_BYTES",
    "MAX_PERSONAL_AGENT_CONTEXT_RESPONSE_BYTES",
    "MAX_PERSONAL_AGENT_MUTATION_RESPONSE_BYTES",
    "MAX_PERSONAL_AGENT_STATE_RESPONSE_BYTES",
    "MAX_RAW_PERSONAL_AGENT_BODY_BYTES",
    "PERSONAL_AGENT_ABANDON_PATH",
    "PERSONAL_AGENT_ACCEPT_PATH",
    "PERSONAL_AGENT_ANSWER_PATH",
    "PERSONAL_AGENT_BUILD_PATH",
    "PERSONAL_AGENT_COMPENSATION_CONFIRM_PATH",
    "PERSONAL_AGENT_COMPENSATION_PREPARE_PATH",
    "PERSONAL_AGENT_COMPLETE_PATH",
    "PERSONAL_AGENT_CONFIRM_PATH",
    "PERSONAL_AGENT_CONTEXT_PATH",
    "PERSONAL_AGENT_CONTINUE_PATH",
    "PERSONAL_AGENT_PAUSE_PATH",
    "PERSONAL_AGENT_PREPARE_PATH",
    "PERSONAL_AGENT_RECONCILE_PATH",
    "PERSONAL_AGENT_REQUEST_HEADER_NAME",
    "PERSONAL_AGENT_REQUEST_HEADER_VALUE",
    "PERSONAL_AGENT_RESUME_PATH",
    "PERSONAL_AGENT_REVIEW_PATH",
    "PERSONAL_AGENT_SKIP_PATH",
    "PERSONAL_AGENT_START_PATH",
    "PERSONAL_AGENT_STATE_PATH",
    "PERSONAL_AGENT_SUPERSEDE_PATH",
    "PersonalAgentAcceptPayload",
    "PersonalAgentAnswerPayload",
    "PersonalAgentBuildPayload",
    "PersonalAgentCompensationConfirmPayload",
    "PersonalAgentCompensationPreparePayload",
    "PersonalAgentControlPayload",
    "PersonalAgentEmptyPayload",
    "PersonalAgentExecutionProjectionService",
    "PersonalAgentInvalidRequestError",
    "PersonalAgentMissionPayload",
    "PersonalAgentPreparedPayload",
    "PersonalAgentPreviewMismatchError",
    "PersonalAgentReconcilePayload",
    "PersonalAgentRequestBoundaryMiddleware",
    "PersonalAgentReviewPayload",
    "PersonalAgentRunPayload",
    "PersonalAgentSkipPayload",
    "PersonalAgentSourceUnavailableError",
    "PersonalAgentStoreUnavailableError",
    "PersonalAgentWebError",
    "PersonalAgentWebService",
    "ProductionPersonalAgentWebService",
    "build_production_personal_agent_web_service",
    "install_personal_agent_routes",
]
