"""Bounded read-only Web/API projection for Retrospective Calibration v1."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.adapters.vault import FileSystemRetrospectiveCalibrationScanner
from second_brain.application.retrospective_calibration import (
    BuildRetrospectiveCalibration,
    BuildRetrospectiveCalibrationContext,
    BuildRetrospectiveCalibrationReplay,
    RetrospectiveCalibrationError,
    RetrospectiveCalibrationRequestV1,
    RetrospectiveCalibrationResultV1,
    serialize_retrospective_calibration_result,
)
from second_brain.config import load_config
from second_brain.entrypoints.web.auth import (
    configured_authority_port,
    trusted_authorities_from_scope,
)

RETROSPECTIVE_CALIBRATION_REQUEST_HEADER_NAME = "X-Second-Brain-Request"
RETROSPECTIVE_CALIBRATION_REQUEST_HEADER_VALUE = "retrospective-calibration-v1"
MAX_RAW_RETROSPECTIVE_CALIBRATION_BODY_BYTES = 8 * 1024

_STAGE7_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

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

_CALIBRATION_MESSAGES: Mapping[str, tuple[int, str]] = {
    "RETROSPECTIVE_CALIBRATION_INVALID_REQUEST": (
        400,
        "Не удалось проверить запрос ретроспективной проверки.",
    ),
    "RETROSPECTIVE_CALIBRATION_CANCELLED": (
        409,
        "Ретроспективная проверка отменена.",
    ),
    "RETROSPECTIVE_CALIBRATION_SOURCE_UNAVAILABLE": (
        503,
        "Источники ретроспективной проверки сейчас недоступны.",
    ),
    "RETROSPECTIVE_CALIBRATION_TOO_LARGE": (
        413,
        "Объём данных для ретроспективной проверки превышает допустимый предел.",
    ),
    "RETROSPECTIVE_CALIBRATION_RESULT_TOO_LARGE": (
        500,
        "Результат ретроспективной проверки слишком велик.",
    ),
}


class RetrospectiveCalibrationRequestPayload(BaseModel):
    """Strict empty HTTP request; v1 policy has no caller-controlled options."""

    model_config = ConfigDict(extra="forbid", strict=True)


class RetrospectiveCalibrationWebService(Protocol):
    """Minimal injectable seam over the existing calibration application core."""

    def execute(
        self,
        request: RetrospectiveCalibrationRequestV1,
    ) -> RetrospectiveCalibrationResultV1:
        """Build one ephemeral aggregate through the application core."""


@dataclass(frozen=True, slots=True)
class LazyVaultRetrospectiveCalibrationService:
    """Resolve configuration and read the vault only for an explicit request."""

    env_file: Path | None = None
    vault_path_override: str | None = None

    def execute(
        self,
        request: RetrospectiveCalibrationRequestV1,
    ) -> RetrospectiveCalibrationResultV1:
        """Run the bounded provider-free core without persistence or caching."""

        config = load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )
        scanner = FileSystemRetrospectiveCalibrationScanner(config.vault_path)
        return BuildRetrospectiveCalibration(
            scanner,
            BuildRetrospectiveCalibrationContext(),
            BuildRetrospectiveCalibrationReplay(),
        ).execute(request)


def build_production_retrospective_calibration_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> LazyVaultRetrospectiveCalibrationService:
    """Create a lazy service without config or vault side effects."""

    return LazyVaultRetrospectiveCalibrationService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


def _safe_error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=_API_HEADERS,
    )


def _calibration_error(error: RetrospectiveCalibrationError) -> JSONResponse:
    fallback = (
        503,
        "Источники ретроспективной проверки сейчас недоступны.",
    )
    status, message = _CALIBRATION_MESSAGES.get(error.code, fallback)
    code = (
        error.code
        if error.code in _CALIBRATION_MESSAGES
        else "RETROSPECTIVE_CALIBRATION_SOURCE_UNAVAILABLE"
    )
    return _safe_error(code, message, status)


def _invalid_request() -> JSONResponse:
    return _safe_error(
        "RETROSPECTIVE_CALIBRATION_INVALID_REQUEST",
        _CALIBRATION_MESSAGES["RETROSPECTIVE_CALIBRATION_INVALID_REQUEST"][1],
        400,
    )


def _too_large() -> JSONResponse:
    return _safe_error(
        "RETROSPECTIVE_CALIBRATION_TOO_LARGE",
        _CALIBRATION_MESSAGES["RETROSPECTIVE_CALIBRATION_TOO_LARGE"][1],
        413,
    )


def _method_not_allowed() -> JSONResponse:
    response = _invalid_request()
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


def _loopback_host_port(host_header: str | None, scheme: str) -> tuple[str, int] | None:
    if not host_header or any(character.isspace() for character in host_header):
        return None
    normalized_scheme = scheme.casefold()
    if normalized_scheme not in {"http", "https"}:
        return None
    try:
        parsed = urlsplit(f"//{host_header}")
        hostname = parsed.hostname
        port = parsed.port
    except UnicodeError, ValueError:
        return None
    if (
        hostname is None
        or hostname.casefold() not in _STAGE7_LOOPBACK_HOSTS
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
    ):
        return None
    if port is None:
        port = 443 if normalized_scheme == "https" else 80
    if not 1 <= port <= 65535:
        return None
    return hostname.casefold(), port


def _loopback_host(
    host_header: str | None,
    scheme: str,
    authorities: frozenset[tuple[str, int]] = frozenset(),
) -> bool:
    return (
        _loopback_host_port(host_header, scheme) is not None
        or configured_authority_port(host_header or "", scheme, authorities) is not None
    )


def _same_origin(
    origin: str | None,
    host_header: str | None,
    scheme: str,
    authorities: frozenset[tuple[str, int]] = frozenset(),
) -> bool:
    if origin is None:
        return True
    if not origin or any(character.isspace() for character in origin):
        return False
    try:
        parsed = urlsplit(origin)
    except UnicodeError, ValueError:
        return False
    normalized_scheme = scheme.casefold()
    if (
        normalized_scheme not in {"http", "https"}
        or parsed.scheme.casefold() != normalized_scheme
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    origin_authority = _loopback_host_port(parsed.netloc, normalized_scheme)
    request_authority = _loopback_host_port(host_header, normalized_scheme)
    if origin_authority is not None or request_authority is not None:
        return origin_authority is not None and origin_authority == request_authority
    configured_origin = configured_authority_port(parsed.netloc, normalized_scheme, authorities)
    configured_request = configured_authority_port(
        host_header or "",
        normalized_scheme,
        authorities,
    )
    return configured_origin is not None and configured_origin == configured_request


async def _send_json_response(
    response: JSONResponse, scope: Scope, receive: Receive, send: Send
) -> None:
    await response(scope, receive, send)


class RetrospectiveCalibrationRequestBoundaryMiddleware:
    """Fail-closed trusted same-origin boundary for the calibration POST."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = cast(str, scope.get("path", ""))
        if path != "/api/retrospective-calibration":
            await self.app(scope, receive, send)
            return
        if scope.get("method") != "POST":
            await _send_json_response(_method_not_allowed(), scope, receive, send)
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
            await _send_json_response(_invalid_request(), scope, receive, send)
            return
        headers = _header_map(scope)
        host = _decode_header(headers, b"host")
        origin = _decode_header(headers, b"origin")
        scheme = str(scope.get("scheme", "")).casefold()
        authorities = trusted_authorities_from_scope(scope)
        if not _loopback_host(host, scheme, authorities) or not _same_origin(
            origin,
            host,
            scheme,
            authorities,
        ):
            await _send_json_response(_invalid_request(), scope, receive, send)
            return
        if (
            _decode_header(headers, b"x-second-brain-request")
            != RETROSPECTIVE_CALIBRATION_REQUEST_HEADER_VALUE
        ):
            await _send_json_response(_invalid_request(), scope, receive, send)
            return
        content_type = _decode_header(headers, b"content-type")
        if (
            content_type is None
            or content_type.split(";", 1)[0].strip().lower() != "application/json"
        ):
            await _send_json_response(_invalid_request(), scope, receive, send)
            return
        cap = MAX_RAW_RETROSPECTIVE_CALIBRATION_BODY_BYTES
        content_length = _decode_header(headers, b"content-length")
        if content_length is not None:
            try:
                parsed_length = int(content_length)
                if parsed_length < 0:
                    raise ValueError
                if parsed_length > cap:
                    await _send_json_response(_too_large(), scope, receive, send)
                    return
            except ValueError:
                await _send_json_response(_invalid_request(), scope, receive, send)
                return
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                await _send_json_response(_invalid_request(), scope, receive, send)
                return
            body.extend(message.get("body", b""))
            if len(body) > cap:
                await _send_json_response(_too_large(), scope, receive, send)
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


def install_retrospective_calibration_routes(
    app: FastAPI,
    *,
    service: RetrospectiveCalibrationWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install one bounded read-only endpoint over the existing core."""

    calibration = service or build_production_retrospective_calibration_service(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    app.add_middleware(RetrospectiveCalibrationRequestBoundaryMiddleware)

    @app.post("/api/retrospective-calibration", include_in_schema=False)
    async def retrospective_calibration_endpoint(request: Request) -> Response:
        try:
            raw = await request.json()
            RetrospectiveCalibrationRequestPayload.model_validate(raw, strict=True)
            result = await run_in_threadpool(
                calibration.execute,
                RetrospectiveCalibrationRequestV1(),
            )
            body = serialize_retrospective_calibration_result(result)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except RetrospectiveCalibrationError as error:
            return _calibration_error(error)
        except Exception:
            return _safe_error(
                "RETROSPECTIVE_CALIBRATION_SOURCE_UNAVAILABLE",
                _CALIBRATION_MESSAGES["RETROSPECTIVE_CALIBRATION_SOURCE_UNAVAILABLE"][1],
                503,
            )
        return Response(
            content=body,
            media_type="application/json",
            headers=_API_HEADERS,
        )


__all__ = [
    "MAX_RAW_RETROSPECTIVE_CALIBRATION_BODY_BYTES",
    "RETROSPECTIVE_CALIBRATION_REQUEST_HEADER_NAME",
    "RETROSPECTIVE_CALIBRATION_REQUEST_HEADER_VALUE",
    "LazyVaultRetrospectiveCalibrationService",
    "RetrospectiveCalibrationRequestBoundaryMiddleware",
    "RetrospectiveCalibrationRequestPayload",
    "RetrospectiveCalibrationWebService",
    "build_production_retrospective_calibration_service",
    "install_retrospective_calibration_routes",
]
