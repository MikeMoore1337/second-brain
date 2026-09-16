"""Private owner-only Web/API boundary for Stage 19 actions.

The transport layer accepts only the typed action gateway envelopes.  It keeps
prepared actions and confirmation tokens in the caller's page memory, never
logs request bodies, and projects only bounded safe receipts and readiness.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol, cast
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.application.action_gateway import (
    ActionExecutionResultV1,
    ActionGatewayConfirmationError,
    ActionGatewayConflictError,
    ActionGatewayConnectorError,
    ActionGatewayError,
    ActionGatewayInvalidRequestError,
    ActionGatewayTargetChangedError,
    ActionIntentV1,
    ActionReceiptV1,
    PreparedExternalActionV1,
)
from second_brain.application.action_gateway_orchestration import (
    ActionGatewayCompensationError,
    ActionGatewayOperationNotFoundError,
    ActionGatewayReconciliationUnavailableError,
    ActionGatewayRuntimeUnavailableError,
    ActionGatewayStatusV1,
    ProductionActionGatewayServiceV1,
)
from second_brain.entrypoints.web.auth import (
    AUTH_USER_ID_SCOPE_KEY,
    request_host_is_trusted,
    same_origin_is_trusted,
    trusted_authorities_from_scope,
)

ACTION_GATEWAY_STATUS_PATH: Final[str] = "/api/action-gateway/status"
ACTION_GATEWAY_PREPARE_PATH: Final[str] = "/api/action-gateway/prepare"
ACTION_GATEWAY_EXECUTE_PATH: Final[str] = "/api/action-gateway/execute"
ACTION_GATEWAY_RECONCILE_PATH: Final[str] = "/api/action-gateway/reconcile"
ACTION_GATEWAY_COMPENSATION_PREPARE_PATH: Final[str] = "/api/action-gateway/compensation/prepare"
ACTION_GATEWAY_HISTORY_PATH: Final[str] = "/api/action-gateway/history"
ACTION_GATEWAY_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
ACTION_GATEWAY_REQUEST_HEADER_VALUE: Final[str] = "action-gateway-v1"
MAX_RAW_ACTION_GATEWAY_BODY_BYTES: Final[int] = 128 * 1024
MAX_ACTION_GATEWAY_RESPONSE_BYTES: Final[int] = 256 * 1024
MAX_ACTION_GATEWAY_HISTORY_ITEMS: Final[int] = 100

_ACTION_GATEWAY_PATHS: Final[frozenset[str]] = frozenset(
    {
        ACTION_GATEWAY_STATUS_PATH,
        ACTION_GATEWAY_PREPARE_PATH,
        ACTION_GATEWAY_EXECUTE_PATH,
        ACTION_GATEWAY_RECONCILE_PATH,
        ACTION_GATEWAY_COMPENSATION_PREPARE_PATH,
        ACTION_GATEWAY_HISTORY_PATH,
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


class ActionGatewayEmptyPayload(BaseModel):
    """Strict empty envelope for status and history requests."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ActionGatewayIntentPayload(BaseModel):
    """Strict envelope around one provider-neutral intent."""

    model_config = ConfigDict(extra="forbid", strict=True)

    intent: dict[str, object]


class ActionGatewayPreparedPayload(BaseModel):
    """Strict envelope around one immutable prepared action."""

    model_config = ConfigDict(extra="forbid", strict=True)

    prepared: dict[str, object]
    confirmation_token: StrictStr
    parent_receipt_id: StrictStr | None = None


class ActionGatewayReconcilePayload(BaseModel):
    """Strict read-only reconciliation envelope."""

    model_config = ConfigDict(extra="forbid", strict=True)

    prepared: dict[str, object]


class ActionGatewayCompensationPreparePayload(BaseModel):
    """Strict envelope for a new owner-requested compensation prepare."""

    model_config = ConfigDict(extra="forbid", strict=True)

    parent_receipt_id: StrictStr
    operation_id: StrictStr


class ActionGatewayWebService(Protocol):
    """Application seam shared by production composition and test doubles."""

    def status(self) -> ActionGatewayStatusV1:
        """Return safe readiness and catalog metadata."""

    def prepare(
        self, intent: ActionIntentV1, *, now: datetime | None = None
    ) -> PreparedExternalActionV1:
        """Prepare one exact action."""

    def issue_confirmation(
        self, prepared: PreparedExternalActionV1, *, now: datetime | None = None
    ) -> str:
        """Issue a one-use page-memory confirmation token."""

    def execute(
        self,
        prepared: PreparedExternalActionV1,
        confirmation: object,
        *,
        now: datetime | None = None,
    ) -> ActionExecutionResultV1:
        """Execute one explicitly confirmed action."""

    def reconcile(
        self, prepared: PreparedExternalActionV1, *, now: datetime | None = None
    ) -> ActionExecutionResultV1:
        """Read-only reconcile one uncertain action."""

    def execute_compensation(
        self,
        prepared: PreparedExternalActionV1,
        confirmation: object,
        parent_receipt_id: UUID,
        *,
        now: datetime | None = None,
    ) -> ActionExecutionResultV1:
        """Execute one separately confirmed compensation."""

    def prepare_compensation(
        self,
        parent_receipt_id: UUID,
        operation_id: str,
        *,
        now: datetime | None = None,
    ) -> PreparedExternalActionV1:
        """Prepare a new explicit compensation action."""

    def history(self) -> tuple[ActionReceiptV1, ...]:
        """Return safe operational receipts."""


def _header(scope: Scope, name: bytes) -> tuple[bool, str | None]:
    values = [
        value.decode("latin-1")
        for header_name, value in scope.get("headers", [])
        if header_name.lower() == name
    ]
    if not values:
        return False, None
    if len(values) != 1:
        return True, None
    return True, values[0]


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
    return _error_response("INVALID_ACTION", "Запрос действия не прошёл проверку.", 400)


def _too_large() -> JSONResponse:
    return _error_response("INVALID_ACTION", "Запрос действия слишком велик.", 413)


def _method_not_allowed() -> JSONResponse:
    response = _invalid_request()
    response.status_code = 405
    response.headers["Allow"] = "POST"
    return response


def _auth_required() -> JSONResponse:
    return _error_response("AUTH_REQUIRED", "Требуется вход.", 401)


async def _send(response: Response, scope: Scope, receive: Receive, send: Send) -> None:
    await response(scope, receive, send)


class ActionGatewayRequestBoundaryMiddleware:
    """Fail-closed owner, Host, Origin, purpose and raw JSON boundary."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _ACTION_GATEWAY_PATHS:
            await self.app(scope, receive, send)
            return
        if scope.get("method") != "POST":
            await _send(_method_not_allowed(), scope, receive, send)
            return
        header_names = [name.lower() for name, _ in scope.get("headers", [])]
        if any(header_names.count(name) > 1 for name in _SECURITY_HEADER_NAMES):
            await _send(_invalid_request(), scope, receive, send)
            return
        host_present, _host = _header(scope, b"host")
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
            or purpose != ACTION_GATEWAY_REQUEST_HEADER_VALUE
            or not _json_content_type(content_type)
        ):
            await _send(_invalid_request(), scope, receive, send)
            return
        if not isinstance(scope.get(AUTH_USER_ID_SCOPE_KEY), str) or not scope.get(
            AUTH_USER_ID_SCOPE_KEY
        ):
            await _send(_auth_required(), scope, receive, send)
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
            if declared > MAX_RAW_ACTION_GATEWAY_BODY_BYTES:
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
            if len(body) > MAX_RAW_ACTION_GATEWAY_BODY_BYTES:
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
    if not raw or len(raw) > MAX_RAW_ACTION_GATEWAY_BODY_BYTES:
        raise ValueError
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        RecursionError,
    ):
        raise ValueError from None
    return payload_type.model_validate(decoded, strict=True)


def _uuid7_text(value: str) -> UUID:
    try:
        parsed = UUID(value)
    except AttributeError, TypeError, ValueError:
        raise ValueError from None
    if parsed.version != 7 or str(parsed) != value:
        raise ValueError
    return parsed


def _prepared(payload: dict[str, object]) -> PreparedExternalActionV1:
    return PreparedExternalActionV1.from_dict(payload)


def _prepared_response(
    prepared: PreparedExternalActionV1,
    confirmation_token: str,
    parent_receipt_id: UUID | None = None,
) -> Response:
    body: dict[str, object] = {
        "prepared": prepared.as_dict(),
        "confirmation_token": confirmation_token,
    }
    if parent_receipt_id is not None:
        body["parent_receipt_id"] = str(parent_receipt_id)
    return _json_response(
        body,
        max_bytes=MAX_ACTION_GATEWAY_RESPONSE_BYTES,
    )


def _execution_response(result: ActionExecutionResultV1) -> Response:
    if type(result) is not ActionExecutionResultV1 or type(result.receipt) is not ActionReceiptV1:
        raise ActionGatewayInvalidRequestError()
    return _json_response(
        {"receipt": result.receipt.as_dict(), "replayed": result.replayed},
        max_bytes=MAX_ACTION_GATEWAY_RESPONSE_BYTES,
    )


def _json_response(body: dict[str, object], *, max_bytes: int) -> Response:
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    if len(encoded) > max_bytes:
        raise ActionGatewayInvalidRequestError()
    return Response(content=encoded, media_type="application/json", headers=_API_HEADERS)


_CONNECTOR_ERROR_MESSAGES: Final[dict[str, tuple[str, str, int]]] = {
    "connector_disabled": ("CONNECTOR_DISABLED", "Интеграция GitHub не включена.", 503),
    "credential_unavailable": (
        "CREDENTIAL_UNAVAILABLE",
        "Учетные данные интеграции GitHub недоступны.",
        503,
    ),
    "target_not_allowed": ("TARGET_NOT_ALLOWED", "Эта цель не входит в разрешённый список.", 403),
    "target_not_found": ("TARGET_NOT_FOUND", "Точная цель GitHub не найдена.", 404),
    "issue_locked": ("TARGET_CHANGED", "Целевая задача заблокирована или изменилась.", 409),
    "pull_request_forbidden": (
        "TARGET_NOT_ALLOWED",
        "Действия над запросами на слияние запрещены.",
        403,
    ),
    "provider_rejected": ("PROVIDER_REJECTED", "GitHub отклонил действие без изменения.", 502),
    "provider_outcome_uncertain": (
        "OUTCOME_UNCERTAIN",
        "Результат действия не подтверждён. Проверь результат отдельно.",
        502,
    ),
    "reconciliation_unavailable": (
        "RECONCILIATION_AMBIGUOUS",
        "Результат нельзя безопасно определить.",
        409,
    ),
    "provider_preflight_failed": (
        "PROVIDER_UNAVAILABLE",
        "GitHub сейчас недоступен для проверки цели.",
        503,
    ),
    "provider_invalid_response": (
        "PROVIDER_UNAVAILABLE",
        "GitHub вернул неподтверждённый ответ.",
        503,
    ),
}


def _web_error(error: BaseException) -> JSONResponse:
    if isinstance(error, ActionGatewayConnectorError):
        return _error_response(
            *_CONNECTOR_ERROR_MESSAGES.get(
                error.safe_error_code,
                ("PROVIDER_UNAVAILABLE", "Интеграция GitHub сейчас недоступна.", 503),
            )
        )
    if isinstance(error, ActionGatewayConfirmationError):
        return _error_response(
            "CONFIRMATION_INVALID",
            "Подтверждение недействительно или истекло. Подготовь действие заново.",
            409,
        )
    if isinstance(error, ActionGatewayConflictError):
        return _error_response(
            "ACTION_CONFLICT",
            "Эта операция уже связана с другим содержимым.",
            409,
        )
    if isinstance(error, ActionGatewayTargetChangedError):
        return _error_response(
            "TARGET_CHANGED",
            "Точная цель изменилась. Подготовь действие заново.",
            409,
        )
    if isinstance(error, ActionGatewayOperationNotFoundError):
        return _error_response("RECONCILIATION_NOT_FOUND", "Операция не найдена.", 404)
    if isinstance(error, ActionGatewayReconciliationUnavailableError):
        return _error_response(
            "RECONCILIATION_AMBIGUOUS",
            "Результат нельзя безопасно определить.",
            409,
        )
    if isinstance(error, ActionGatewayCompensationError):
        return _error_response(
            "COMPENSATION_NOT_SUPPORTED",
            "Для этой операции компенсация не поддерживается.",
            409,
        )
    if isinstance(error, ActionGatewayRuntimeUnavailableError):
        return _error_response("STORE_UNAVAILABLE", "Операционное хранилище недоступно.", 503)
    if isinstance(error, ActionGatewayInvalidRequestError):
        return _invalid_request()
    if isinstance(error, ActionGatewayError):
        return _error_response("ACTION_UNAVAILABLE", "Действие сейчас недоступно.", 503)
    return _error_response("ACTION_UNAVAILABLE", "Действие сейчас недоступно.", 503)


def _safe_status(service: ActionGatewayWebService) -> Response:
    status = service.status()
    if type(status) is not ActionGatewayStatusV1:
        raise ActionGatewayInvalidRequestError()
    return _json_response(status.as_dict(), max_bytes=MAX_ACTION_GATEWAY_RESPONSE_BYTES)


def _safe_history(service: ActionGatewayWebService) -> Response:
    receipts = service.history()
    if type(receipts) is not tuple or any(type(item) is not ActionReceiptV1 for item in receipts):
        raise ActionGatewayInvalidRequestError()
    bounded = receipts[-MAX_ACTION_GATEWAY_HISTORY_ITEMS:]
    return _json_response(
        {
            "receipts": [item.as_dict() for item in bounded],
            "count": len(receipts),
            "truncated": len(bounded) != len(receipts),
        },
        max_bytes=MAX_ACTION_GATEWAY_RESPONSE_BYTES,
    )


def install_action_gateway_routes(
    app: FastAPI,
    *,
    service: ActionGatewayWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install the closed, private Stage 19 action gateway routes."""

    actual_service: ActionGatewayWebService = service or ProductionActionGatewayServiceV1(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    app.add_middleware(ActionGatewayRequestBoundaryMiddleware)

    @app.post(ACTION_GATEWAY_STATUS_PATH, include_in_schema=False)
    async def action_gateway_status_endpoint(request: Request) -> Response:
        try:
            payload = await _read_payload(request, ActionGatewayEmptyPayload)
            del payload
            return await run_in_threadpool(_safe_status, actual_service)
        except ActionGatewayError as error:
            return _web_error(error)
        except ValidationError, TypeError, ValueError, UnicodeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _web_error(error)

    @app.post(ACTION_GATEWAY_PREPARE_PATH, include_in_schema=False)
    async def action_gateway_prepare_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                ActionGatewayIntentPayload,
                await _read_payload(request, ActionGatewayIntentPayload),
            )
            intent = ActionIntentV1.from_dict(payload.intent)
            prepared = await run_in_threadpool(actual_service.prepare, intent)
            token = await run_in_threadpool(actual_service.issue_confirmation, prepared)
            return _prepared_response(prepared, token)
        except ActionGatewayError as error:
            return _web_error(error)
        except ValidationError, TypeError, ValueError, UnicodeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _web_error(error)

    @app.post(ACTION_GATEWAY_EXECUTE_PATH, include_in_schema=False)
    async def action_gateway_execute_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                ActionGatewayPreparedPayload,
                await _read_payload(request, ActionGatewayPreparedPayload),
            )
            prepared = _prepared(payload.prepared)
            if payload.parent_receipt_id is None:
                result = await run_in_threadpool(
                    actual_service.execute,
                    prepared,
                    payload.confirmation_token,
                )
            else:
                result = await run_in_threadpool(
                    actual_service.execute_compensation,
                    prepared,
                    payload.confirmation_token,
                    _uuid7_text(payload.parent_receipt_id),
                )
            return _execution_response(result)
        except ActionGatewayError as error:
            return _web_error(error)
        except ValidationError, TypeError, ValueError, UnicodeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _web_error(error)

    @app.post(ACTION_GATEWAY_RECONCILE_PATH, include_in_schema=False)
    async def action_gateway_reconcile_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                ActionGatewayReconcilePayload,
                await _read_payload(request, ActionGatewayReconcilePayload),
            )
            prepared = _prepared(payload.prepared)
            result = await run_in_threadpool(actual_service.reconcile, prepared)
            return _execution_response(result)
        except ActionGatewayError as error:
            return _web_error(error)
        except ValidationError, TypeError, ValueError, UnicodeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _web_error(error)

    @app.post(ACTION_GATEWAY_COMPENSATION_PREPARE_PATH, include_in_schema=False)
    async def action_gateway_compensation_prepare_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                ActionGatewayCompensationPreparePayload,
                await _read_payload(request, ActionGatewayCompensationPreparePayload),
            )
            parent_receipt_id = _uuid7_text(payload.parent_receipt_id)
            prepared = await run_in_threadpool(
                actual_service.prepare_compensation,
                parent_receipt_id,
                payload.operation_id,
            )
            token = await run_in_threadpool(actual_service.issue_confirmation, prepared)
            return _prepared_response(prepared, token, parent_receipt_id)
        except ActionGatewayError as error:
            return _web_error(error)
        except ValidationError, TypeError, ValueError, UnicodeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _web_error(error)

    @app.post(ACTION_GATEWAY_HISTORY_PATH, include_in_schema=False)
    async def action_gateway_history_endpoint(request: Request) -> Response:
        try:
            payload = await _read_payload(request, ActionGatewayEmptyPayload)
            del payload
            return await run_in_threadpool(_safe_history, actual_service)
        except ActionGatewayError as error:
            return _web_error(error)
        except ValidationError, TypeError, ValueError, UnicodeError, json.JSONDecodeError:
            return _invalid_request()
        except Exception as error:
            return _web_error(error)


__all__ = [
    "ACTION_GATEWAY_COMPENSATION_PREPARE_PATH",
    "ACTION_GATEWAY_EXECUTE_PATH",
    "ACTION_GATEWAY_HISTORY_PATH",
    "ACTION_GATEWAY_PREPARE_PATH",
    "ACTION_GATEWAY_RECONCILE_PATH",
    "ACTION_GATEWAY_REQUEST_HEADER_NAME",
    "ACTION_GATEWAY_REQUEST_HEADER_VALUE",
    "ACTION_GATEWAY_STATUS_PATH",
    "MAX_RAW_ACTION_GATEWAY_BODY_BYTES",
    "ActionGatewayCompensationPreparePayload",
    "ActionGatewayEmptyPayload",
    "ActionGatewayIntentPayload",
    "ActionGatewayPreparedPayload",
    "ActionGatewayReconcilePayload",
    "ActionGatewayRequestBoundaryMiddleware",
    "ActionGatewayWebService",
    "install_action_gateway_routes",
]
