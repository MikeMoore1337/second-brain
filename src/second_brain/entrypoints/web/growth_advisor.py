"""Private owner-only Web transport for the Stage 11C Growth Advisor."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.adapters.advisor.cloudflare_workers_ai import CloudflareWorkersAiAdvisorPort
from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.growth_advisor import (
    BuildGrowthAdvisor,
    GrowthAdvisorBranchV1,
    GrowthAdvisorError,
    GrowthAdvisorErrorCode,
    GrowthAdvisorGoalPreviewV1,
    GrowthAdvisorInvalidRequestError,
    GrowthAdvisorRequestV1,
    validate_growth_advisor_branch,
    validate_growth_advisor_preview,
)
from second_brain.application.ports import AdvisorPort, CancellationTokenSource
from second_brain.config import ConfigurationError, load_config
from second_brain.entrypoints.web.auth import (
    request_host_is_trusted,
    same_origin_is_trusted,
    trusted_authorities_from_scope,
)

GROWTH_ADVISOR_PREVIEW_PATH: Final[str] = "/api/growth-advisor/preview"
GROWTH_ADVISOR_EXECUTE_PATH: Final[str] = "/api/growth-advisor/execute"
GROWTH_ADVISOR_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
GROWTH_ADVISOR_REQUEST_HEADER_VALUE: Final[str] = "growth-advisor-v1"
MAX_RAW_GROWTH_ADVISOR_BODY_BYTES: Final[int] = 64 * 1024
MAX_GROWTH_ADVISOR_RESPONSE_BYTES: Final[int] = 128 * 1024

_GROWTH_ADVISOR_PATHS: Final[frozenset[str]] = frozenset(
    {GROWTH_ADVISOR_PREVIEW_PATH, GROWTH_ADVISOR_EXECUTE_PATH}
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

_ERROR_STATUS: Final[dict[str, int]] = {
    GrowthAdvisorErrorCode.INVALID_REQUEST.value: 400,
    GrowthAdvisorErrorCode.GOAL_UNAVAILABLE.value: 503,
    GrowthAdvisorErrorCode.GOAL_MISSING.value: 404,
    GrowthAdvisorErrorCode.GOAL_CHANGED.value: 409,
    GrowthAdvisorErrorCode.GOAL_TEXT_UNSUPPORTED.value: 400,
    GrowthAdvisorErrorCode.GOAL_TEXT_TOO_LARGE.value: 413,
    GrowthAdvisorErrorCode.CONTEXT_TOO_LARGE.value: 413,
    GrowthAdvisorErrorCode.RESULT_TOO_LARGE.value: 502,
    GrowthAdvisorErrorCode.RECOMMENDATION_UNAVAILABLE.value: 503,
    GrowthAdvisorErrorCode.CANCELLED.value: 409,
    GrowthAdvisorErrorCode.TIMEOUT.value: 504,
    GrowthAdvisorErrorCode.FAILURE.value: 502,
    GrowthAdvisorErrorCode.INVALID_RESULT.value: 502,
    GrowthAdvisorErrorCode.POLICY_MISMATCH.value: 500,
}


class GrowthAdvisorWebService(Protocol):
    """Minimal injectable seam for the two explicit owner operations."""

    def preview(self, request: GrowthAdvisorRequestV1) -> GrowthAdvisorGoalPreviewV1:
        """Build one transient preview without a provider call."""

    def execute(
        self,
        request: GrowthAdvisorRequestV1,
        preview: GrowthAdvisorGoalPreviewV1,
        *,
        confirmed: bool,
    ) -> GrowthAdvisorBranchV1:
        """Execute one explicitly confirmed Advisor branch."""


@dataclass(slots=True)
class ProductionGrowthAdvisorWebService:
    """Resolve the current vault/provider only for an explicit route operation."""

    env_file: Path | None = None
    vault_path_override: str | None = None
    advisor: AdvisorPort | None = None

    def _runtime(self) -> BuildGrowthAdvisor:
        config = load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )
        return BuildGrowthAdvisor(
            FileSystemVaultReader(config.vault_path),
            self.advisor or CloudflareWorkersAiAdvisorPort(),
        )

    def preview(self, request: GrowthAdvisorRequestV1) -> GrowthAdvisorGoalPreviewV1:
        """Read and rebuild one current Goal; the provider is not invoked."""

        return self._runtime().preview(request)

    def execute(
        self,
        request: GrowthAdvisorRequestV1,
        preview: GrowthAdvisorGoalPreviewV1,
        *,
        confirmed: bool,
    ) -> GrowthAdvisorBranchV1:
        """Run the one-shot application coordinator after explicit confirmation."""

        cancellation = CancellationTokenSource()
        return self._runtime().execute(
            request,
            preview,
            confirmed=confirmed,
            cancellation=cancellation.token,
        )


def build_production_growth_advisor_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
    advisor: AdvisorPort | None = None,
) -> ProductionGrowthAdvisorWebService:
    """Build a lazy production service with the existing Advisor adapter."""

    return ProductionGrowthAdvisorWebService(
        env_file=env_file,
        vault_path_override=vault_path_override,
        advisor=advisor,
    )


@dataclass(frozen=True, slots=True)
class GrowthAdvisorExecutePayloadV1:
    """Strict transport envelope for one visible confirmation action."""

    request: GrowthAdvisorRequestV1
    preview: GrowthAdvisorGoalPreviewV1
    confirmed: bool

    @classmethod
    def from_dict(cls, value: object) -> GrowthAdvisorExecutePayloadV1:
        """Parse exactly ``request``, ``preview`` and ``confirmed`` fields."""

        if not isinstance(value, Mapping) or set(value) != {"request", "preview", "confirmed"}:
            raise GrowthAdvisorInvalidRequestError()
        if type(value["confirmed"]) is not bool:
            raise GrowthAdvisorInvalidRequestError()
        try:
            return cls(
                request=GrowthAdvisorRequestV1.from_dict(value["request"]),
                preview=GrowthAdvisorGoalPreviewV1.from_dict(value["preview"]),
                confirmed=value["confirmed"],
            )
        except GrowthAdvisorError:
            raise
        except KeyError, TypeError, ValueError, UnicodeError:
            raise GrowthAdvisorInvalidRequestError() from None

    def as_dict(self) -> dict[str, object]:
        """Return the exact confirmation envelope."""

        return {
            "request": self.request.as_dict(),
            "preview": self.preview.as_dict(),
            "confirmed": self.confirmed,
        }


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


def _error_response(error: GrowthAdvisorError, *, status_code: int | None = None) -> Response:
    body = json.dumps(
        {"error": error.as_dict()},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    headers = dict(_API_HEADERS)
    headers["Content-Type"] = "application/json; charset=utf-8"
    return Response(
        content=body,
        status_code=status_code if status_code is not None else _ERROR_STATUS[error.code],
        headers=headers,
    )


def _method_not_allowed() -> Response:
    response = _error_response(GrowthAdvisorInvalidRequestError(), status_code=405)
    response.headers["Allow"] = "POST"
    return response


def _too_large() -> Response:
    return _error_response(
        GrowthAdvisorError(GrowthAdvisorErrorCode.CONTEXT_TOO_LARGE),
        status_code=413,
    )


async def _send(response: Response, scope: Scope, receive: Receive, send: Send) -> None:
    await response(scope, receive, send)


class GrowthAdvisorRequestBoundaryMiddleware:
    """Fail-closed Host, Origin, purpose, JSON and bounded raw-body boundary."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _GROWTH_ADVISOR_PATHS:
            await self.app(scope, receive, send)
            return
        if scope.get("method") != "POST":
            await _send(_method_not_allowed(), scope, receive, send)
            return
        header_names = [name.lower() for name, _ in scope.get("headers", [])]
        if any(header_names.count(name) > 1 for name in _SECURITY_HEADER_NAMES):
            await _send(_error_response(GrowthAdvisorInvalidRequestError()), scope, receive, send)
            return
        host_present, _host = _header(scope, b"host")
        origin_present, origin = _header(scope, b"origin")
        _, purpose = _header(scope, b"x-second-brain-request")
        _, content_type = _header(scope, b"content-type")
        authorities = trusted_authorities_from_scope(scope)
        if (
            not host_present
            or not request_host_is_trusted(scope, authorities)
            or (
                origin_present
                and (origin is None or not same_origin_is_trusted(scope, origin, authorities))
            )
            or purpose != GROWTH_ADVISOR_REQUEST_HEADER_VALUE
            or not _json_content_type(content_type)
        ):
            await _send(_error_response(GrowthAdvisorInvalidRequestError()), scope, receive, send)
            return
        _, content_length = _header(scope, b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length.strip())
            except ValueError:
                await _send(
                    _error_response(GrowthAdvisorInvalidRequestError()), scope, receive, send
                )
                return
            if declared < 0:
                await _send(
                    _error_response(GrowthAdvisorInvalidRequestError()), scope, receive, send
                )
                return
            if declared > MAX_RAW_GROWTH_ADVISOR_BODY_BYTES:
                await _send(_too_large(), scope, receive, send)
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                await _send(
                    _error_response(GrowthAdvisorInvalidRequestError()), scope, receive, send
                )
                return
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                await _send(_too_large(), scope, receive, send)
                return
            body.extend(chunk)
            if len(body) > MAX_RAW_GROWTH_ADVISOR_BODY_BYTES:
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


async def _read_json(request: Request) -> object:
    raw = await request.body()
    if not raw or len(raw) > MAX_RAW_GROWTH_ADVISOR_BODY_BYTES:
        raise ValueError
    try:
        text = raw.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError:
        raise ValueError from None


def _response(content: dict[str, object], *, status_code: int = 200) -> Response:
    body = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_GROWTH_ADVISOR_RESPONSE_BYTES:
        return _error_response(
            GrowthAdvisorError(GrowthAdvisorErrorCode.RESULT_TOO_LARGE),
            status_code=502,
        )
    headers = dict(_API_HEADERS)
    headers["Content-Type"] = "application/json; charset=utf-8"
    return Response(content=body, status_code=status_code, headers=headers)


def install_growth_advisor_routes(
    app: FastAPI,
    *,
    service: GrowthAdvisorWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install separate preview and explicit-confirmation private routes."""

    actual_service = service or build_production_growth_advisor_service(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    app.add_middleware(GrowthAdvisorRequestBoundaryMiddleware)

    @app.post(GROWTH_ADVISOR_PREVIEW_PATH, include_in_schema=False)
    async def growth_advisor_preview_endpoint(request: Request) -> Response:
        try:
            payload = GrowthAdvisorRequestV1.from_dict(await _read_json(request))
            preview = await run_in_threadpool(actual_service.preview, payload)
            preview = validate_growth_advisor_preview(preview)
            return _response(preview.as_dict())
        except GrowthAdvisorError as error:
            return _error_response(error)
        except ConfigurationError:
            return _error_response(
                GrowthAdvisorError(GrowthAdvisorErrorCode.GOAL_UNAVAILABLE),
            )
        except ValueError, TypeError, UnicodeError, json.JSONDecodeError:
            return _error_response(GrowthAdvisorInvalidRequestError())
        except Exception:
            return _error_response(
                GrowthAdvisorError(GrowthAdvisorErrorCode.GOAL_UNAVAILABLE),
            )

    @app.post(GROWTH_ADVISOR_EXECUTE_PATH, include_in_schema=False)
    async def growth_advisor_execute_endpoint(request: Request) -> Response:
        try:
            payload = GrowthAdvisorExecutePayloadV1.from_dict(await _read_json(request))
            if not payload.confirmed:
                raise GrowthAdvisorInvalidRequestError()
            branch = await run_in_threadpool(
                actual_service.execute,
                payload.request,
                payload.preview,
                confirmed=payload.confirmed,
            )
            validated_branch = validate_growth_advisor_branch(branch)
            return _response(validated_branch.as_dict())
        except GrowthAdvisorError as error:
            return _error_response(error)
        except ConfigurationError:
            return _error_response(
                GrowthAdvisorError(GrowthAdvisorErrorCode.GOAL_UNAVAILABLE),
            )
        except ValueError, TypeError, UnicodeError, json.JSONDecodeError:
            return _error_response(GrowthAdvisorInvalidRequestError())
        except Exception:
            return _error_response(GrowthAdvisorError(GrowthAdvisorErrorCode.FAILURE))


__all__ = [
    "GROWTH_ADVISOR_EXECUTE_PATH",
    "GROWTH_ADVISOR_PREVIEW_PATH",
    "GROWTH_ADVISOR_REQUEST_HEADER_NAME",
    "GROWTH_ADVISOR_REQUEST_HEADER_VALUE",
    "MAX_GROWTH_ADVISOR_RESPONSE_BYTES",
    "MAX_RAW_GROWTH_ADVISOR_BODY_BYTES",
    "GrowthAdvisorExecutePayloadV1",
    "GrowthAdvisorRequestBoundaryMiddleware",
    "GrowthAdvisorWebService",
    "ProductionGrowthAdvisorWebService",
    "build_production_growth_advisor_service",
    "install_growth_advisor_routes",
]
