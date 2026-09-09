"""Bounded Web/API projection for Stage 7 Assistant + Compare v1."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.adapters.advisor.cloudflare_workers_ai import (
    ADVISOR_TOTAL_DEADLINE_SECONDS,
    CloudflareWorkersAiAdvisorPort,
)
from second_brain.application.assistant import (
    AssistantError,
    AssistantExplicitContext,
    AssistantOption,
    AssistantReasoningEnvelopeV1,
    AssistantRequest,
    AssistantResultEnvelopeV1,
    BuildAssistant,
    serialize_assistant_result_envelope,
)
from second_brain.application.compare import (
    BuildCompare,
    CompareAssistantInputsV1,
    CompareAssistantPort,
    CompareErrorV1,
    CompareExecutionContextV1,
    CompareOptionV1,
    CompareRequestV1,
    CompareResultV1,
    CompareSimulateMePort,
    serialize_compare_result,
)
from second_brain.application.ports import AdvisorPort, CancellationTokenSource
from second_brain.application.simulate_me import SimulateMeRequest, SimulateMeResult
from second_brain.entrypoints.web.simulate_me import (
    SimulateMeService,
    build_production_simulate_me_service,
)

ASSISTANT_REQUEST_HEADER_NAME = "X-Second-Brain-Request"
ASSISTANT_REQUEST_HEADER_VALUE = "assistant-v1"
COMPARE_REQUEST_HEADER_NAME = "X-Second-Brain-Request"
COMPARE_REQUEST_HEADER_VALUE = "compare-v1"
MAX_RAW_ASSISTANT_BODY_BYTES = 64 * 1024
MAX_RAW_COMPARE_BODY_BYTES = 128 * 1024

_API_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; connect-src 'self'; font-src 'self'; "
        "form-action 'none'; frame-ancestors 'none'; img-src 'self'; object-src 'none'; "
        "script-src 'self'; style-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}

_ASSISTANT_MESSAGES: Mapping[str, tuple[int, str]] = {
    "ASSISTANT_INVALID_REQUEST": (400, "Запрос независимого совета недопустим."),
    "ASSISTANT_CANCELLED": (409, "Запрос независимого совета отменён."),
    "ASSISTANT_TIMEOUT": (504, "Сервис независимого совета не ответил вовремя."),
    "ASSISTANT_PROVIDER_UNAVAILABLE": (503, "Сервис независимого совета сейчас недоступен."),
    "ASSISTANT_PROVIDER_FAILURE": (502, "Не удалось получить независимый совет."),
    "ASSISTANT_MALFORMED_RESULT": (502, "Сервис независимого совета вернул некорректный ответ."),
    "ASSISTANT_RESULT_TOO_LARGE": (502, "Ответ независимого совета слишком велик."),
    "ASSISTANT_RESULT_INVALID": (502, "Ответ независимого совета не прошёл проверку."),
}

_COMPARE_MESSAGES: Mapping[str, tuple[int, str]] = {
    "COMPARE_INVALID_REQUEST": (400, "Запрос сравнения недопустим."),
    "COMPARE_CANCELLED": (409, "Запрос сравнения отменён."),
    "COMPARE_COMPOSITION_INVALID": (500, "Не удалось собрать структурное сравнение."),
    "COMPARE_RESULT_TOO_LARGE": (500, "Результат сравнения слишком велик."),
}


class Stage7OptionPayload(BaseModel):
    """Caller-owned request-local option."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: StrictStr
    label: StrictStr


class Stage7ExplicitContextPayload(BaseModel):
    """Context entered explicitly by the current caller."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["fact", "background"]
    text: StrictStr


class Stage7RequestPayload(BaseModel):
    """Exact browser-owned Stage 7 input; no automatic private context fields exist."""

    model_config = ConfigDict(extra="forbid", strict=True)

    task: StrictStr
    options: list[Stage7OptionPayload]
    explicit_constraints: list[StrictStr] = []
    explicit_goals: list[StrictStr] = []
    explicit_context: list[Stage7ExplicitContextPayload] = []


class AssistantWebService(Protocol):
    """Minimal injectable Web seam returning the canonical Assistant projection."""

    def execute(self, payload: Stage7RequestPayload) -> dict[str, object]:
        """Execute one caller-explicit Assistant request."""


class CompareWebService(Protocol):
    """Minimal injectable Web seam returning the canonical Compare projection."""

    def execute(self, payload: Stage7RequestPayload) -> dict[str, object] | CompareErrorV1:
        """Execute one independent two-branch Compare request."""


def _assistant_request(payload: Stage7RequestPayload) -> AssistantRequest:
    return AssistantRequest(
        task=payload.task,
        options=tuple(
            AssistantOption(id=option.id, label=option.label) for option in payload.options
        ),
        explicit_constraints=tuple(payload.explicit_constraints),
        explicit_goals=tuple(payload.explicit_goals),
        explicit_context=tuple(
            AssistantExplicitContext(kind=context.kind, text=context.text)
            for context in payload.explicit_context
        ),
    )


def _compare_request(payload: Stage7RequestPayload) -> CompareRequestV1:
    return CompareRequestV1(
        task=payload.task,
        options=tuple(
            CompareOptionV1(id=option.id, label=option.label) for option in payload.options
        ),
        assistant=CompareAssistantInputsV1(
            explicit_constraints=tuple(payload.explicit_constraints),
            explicit_goals=tuple(payload.explicit_goals),
            explicit_context=tuple(
                AssistantExplicitContext(kind=context.kind, text=context.text)
                for context in payload.explicit_context
            ),
        ),
    )


@dataclass(slots=True)
class ProductionAssistantWebService:
    """Ephemeral Assistant service over the already-approved AdvisorPort."""

    advisor: AdvisorPort

    def execute(self, payload: Stage7RequestPayload) -> dict[str, object]:
        request = _assistant_request(payload)
        cancellation = CancellationTokenSource()
        result = BuildAssistant(self.advisor).execute(request, cancellation=cancellation.token)
        decoded = json.loads(serialize_assistant_result_envelope(result).decode("utf-8"))
        if type(decoded) is not dict:
            raise RuntimeError("assistant serializer returned a non-object")
        return cast(dict[str, object], decoded)


@dataclass(slots=True)
class _CompareAssistantAdapter:
    advisor: AdvisorPort

    def advise(
        self,
        request: AssistantReasoningEnvelopeV1,
        *,
        execution: CompareExecutionContextV1,
    ) -> AssistantResultEnvelopeV1:
        return self.advisor.advise(request, cancellation=execution.cancellation)


@dataclass(slots=True)
class _CompareSimulateMeAdapter:
    service: SimulateMeService

    def execute(
        self,
        request: SimulateMeRequest,
        *,
        execution: CompareExecutionContextV1,
    ) -> SimulateMeResult:
        if execution.cancellation.is_cancelled():
            raise TimeoutError
        return self.service.build(request)


@dataclass(slots=True)
class ProductionCompareWebService:
    """One ephemeral Compare execution over independent Assistant and Simulate Me seams."""

    assistant: CompareAssistantPort
    simulate_me: CompareSimulateMePort
    clock: Callable[[], float] = time.monotonic

    def execute(self, payload: Stage7RequestPayload) -> dict[str, object] | CompareErrorV1:
        request = _compare_request(payload)
        cancellation = CancellationTokenSource()
        execution = CompareExecutionContextV1(
            cancellation=cancellation.token,
            deadline=float(self.clock() + ADVISOR_TOTAL_DEADLINE_SECONDS),
        )
        result = BuildCompare(
            self.assistant,
            self.simulate_me,
            clock=self.clock,
        ).execute(request, execution=execution)
        if isinstance(result, CompareErrorV1):
            return result
        if not isinstance(result, CompareResultV1):
            raise RuntimeError("compare core returned an unexpected result")
        decoded = json.loads(serialize_compare_result(result, request=request).decode("utf-8"))
        if type(decoded) is not dict:
            raise RuntimeError("compare serializer returned a non-object")
        return cast(dict[str, object], decoded)


def build_production_assistant_web_service(
    *,
    advisor: AdvisorPort | None = None,
) -> ProductionAssistantWebService:
    """Build without reading config or contacting the provider until an explicit request."""

    return ProductionAssistantWebService(advisor=advisor or CloudflareWorkersAiAdvisorPort())


def build_production_compare_web_service(
    *,
    advisor: AdvisorPort | None = None,
    simulate_me_service: SimulateMeService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> ProductionCompareWebService:
    """Compose the existing provider boundary with the existing current-context Simulate Me seam."""

    actual_advisor = advisor or CloudflareWorkersAiAdvisorPort()
    actual_simulate = simulate_me_service or build_production_simulate_me_service(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    return ProductionCompareWebService(
        assistant=_CompareAssistantAdapter(actual_advisor),
        simulate_me=_CompareSimulateMeAdapter(actual_simulate),
    )


def _safe_error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=_API_HEADERS,
    )


def _assistant_error(error: AssistantError) -> JSONResponse:
    status, message = _ASSISTANT_MESSAGES.get(
        error.code,
        (500, "Не удалось выполнить независимый совет."),
    )
    code = error.code if error.code in _ASSISTANT_MESSAGES else "ASSISTANT_PROVIDER_FAILURE"
    return _safe_error(code, message, status)


def _compare_error(error: CompareErrorV1) -> JSONResponse:
    code = error.code.value
    status, message = _COMPARE_MESSAGES.get(
        code,
        (500, "Не удалось выполнить структурное сравнение."),
    )
    safe_code = code if code in _COMPARE_MESSAGES else "COMPARE_COMPOSITION_INVALID"
    return _safe_error(safe_code, message, status)


def _invalid_request(path: str) -> JSONResponse:
    if path == "/api/compare":
        return _safe_error("COMPARE_INVALID_REQUEST", "Запрос сравнения недопустим.", 400)
    return _safe_error("ASSISTANT_INVALID_REQUEST", "Запрос независимого совета недопустим.", 400)


def _too_large(path: str) -> JSONResponse:
    if path == "/api/compare":
        return _safe_error("COMPARE_INVALID_REQUEST", "Запрос сравнения слишком велик.", 413)
    return _safe_error(
        "ASSISTANT_INVALID_REQUEST", "Запрос независимого совета слишком велик.", 413
    )


def _method_not_allowed(path: str) -> JSONResponse:
    response = _invalid_request(path)
    response.status_code = 405
    response.headers["Allow"] = "POST"
    return response


def _header_map(scope: Scope) -> dict[bytes, bytes]:
    return {name.lower(): value for name, value in scope.get("headers", [])}


def _decode_header(headers: Mapping[bytes, bytes], name: bytes) -> str | None:
    value = headers.get(name)
    if value is None:
        return None
    return value.decode("latin-1")


def _loopback_host(host_header: str | None) -> bool:
    if not host_header:
        return False
    candidate = host_header.strip().lower()
    if candidate.startswith("["):
        hostname = candidate[1:].split("]", 1)[0]
    else:
        hostname = candidate.split(":", 1)[0]
    return hostname in {"127.0.0.1", "localhost", "::1"}


def _same_origin(origin: str | None, host_header: str | None) -> bool:
    if origin is None:
        return True
    if host_header is None:
        return False
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return False
    return bool(parsed.netloc) and parsed.netloc.lower() == host_header.strip().lower()


async def _send_json_response(
    response: JSONResponse, scope: Scope, receive: Receive, send: Send
) -> None:
    await response(scope, receive, send)


class AssistantCompareRequestBoundaryMiddleware:
    """Fail-closed request boundary for the two explicit Stage 7 POST endpoints."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = cast(str, scope.get("path", ""))
        if path not in {"/api/assistant", "/api/compare"}:
            await self.app(scope, receive, send)
            return
        if scope.get("method") != "POST":
            await _send_json_response(_method_not_allowed(path), scope, receive, send)
            return
        security_headers = {
            b"host",
            b"origin",
            b"content-type",
            b"content-length",
            b"x-second-brain-request",
        }
        header_names = [name.lower() for name, _ in scope.get("headers", [])]
        if any(header_names.count(name) > 1 for name in security_headers):
            await _send_json_response(_invalid_request(path), scope, receive, send)
            return
        headers = _header_map(scope)
        host = _decode_header(headers, b"host")
        origin = _decode_header(headers, b"origin")
        if not _loopback_host(host) or not _same_origin(origin, host):
            await _send_json_response(_invalid_request(path), scope, receive, send)
            return
        purpose = _decode_header(headers, b"x-second-brain-request")
        expected = (
            ASSISTANT_REQUEST_HEADER_VALUE
            if path == "/api/assistant"
            else COMPARE_REQUEST_HEADER_VALUE
        )
        if purpose != expected:
            await _send_json_response(_invalid_request(path), scope, receive, send)
            return
        content_type = _decode_header(headers, b"content-type")
        if (
            content_type is None
            or content_type.split(";", 1)[0].strip().lower() != "application/json"
        ):
            await _send_json_response(_invalid_request(path), scope, receive, send)
            return
        cap = (
            MAX_RAW_ASSISTANT_BODY_BYTES if path == "/api/assistant" else MAX_RAW_COMPARE_BODY_BYTES
        )
        content_length = _decode_header(headers, b"content-length")
        if content_length is not None:
            try:
                parsed_length = int(content_length)
                if parsed_length < 0:
                    raise ValueError
                if parsed_length > cap:
                    await _send_json_response(_too_large(path), scope, receive, send)
                    return
            except ValueError:
                await _send_json_response(_invalid_request(path), scope, receive, send)
                return
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                await _send_json_response(_invalid_request(path), scope, receive, send)
                return
            body.extend(message.get("body", b""))
            if len(body) > cap:
                await _send_json_response(_too_large(path), scope, receive, send)
                return
            more_body = bool(message.get("more_body", False))
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


def install_assistant_compare_routes(
    app: FastAPI,
    *,
    assistant_service: AssistantWebService | None = None,
    compare_service: CompareWebService | None = None,
    simulate_me_service: SimulateMeService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install exactly two bounded read-only Stage 7 endpoints into the current app."""

    assistant = assistant_service or build_production_assistant_web_service()
    compare = compare_service or build_production_compare_web_service(
        simulate_me_service=simulate_me_service,
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    app.add_middleware(AssistantCompareRequestBoundaryMiddleware)

    @app.post("/api/assistant", include_in_schema=False)
    async def assistant_endpoint(request: Request) -> JSONResponse:
        try:
            raw = await request.json()
            payload = Stage7RequestPayload.model_validate(raw, strict=True)
            result = await run_in_threadpool(assistant.execute, payload)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request("/api/assistant")
        except AssistantError as error:
            return _assistant_error(error)
        except Exception:
            return _safe_error(
                "ASSISTANT_PROVIDER_FAILURE",
                "Не удалось получить независимый совет.",
                500,
            )
        return JSONResponse(content=result, headers=_API_HEADERS)

    @app.post("/api/compare", include_in_schema=False)
    async def compare_endpoint(request: Request) -> JSONResponse:
        try:
            raw = await request.json()
            payload = Stage7RequestPayload.model_validate(raw, strict=True)
            result = await run_in_threadpool(compare.execute, payload)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request("/api/compare")
        except Exception:
            return _safe_error(
                "COMPARE_COMPOSITION_INVALID",
                "Не удалось выполнить структурное сравнение.",
                500,
            )
        if isinstance(result, CompareErrorV1):
            return _compare_error(result)
        return JSONResponse(content=result, headers=_API_HEADERS)


__all__ = [
    "ASSISTANT_REQUEST_HEADER_NAME",
    "ASSISTANT_REQUEST_HEADER_VALUE",
    "COMPARE_REQUEST_HEADER_NAME",
    "COMPARE_REQUEST_HEADER_VALUE",
    "MAX_RAW_ASSISTANT_BODY_BYTES",
    "MAX_RAW_COMPARE_BODY_BYTES",
    "AssistantCompareRequestBoundaryMiddleware",
    "AssistantWebService",
    "CompareWebService",
    "ProductionAssistantWebService",
    "ProductionCompareWebService",
    "Stage7ExplicitContextPayload",
    "Stage7OptionPayload",
    "Stage7RequestPayload",
    "build_production_assistant_web_service",
    "build_production_compare_web_service",
    "install_assistant_compare_routes",
]
