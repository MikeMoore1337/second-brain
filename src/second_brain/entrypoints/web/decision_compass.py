"""Private owner-only Web transport for Cognitive Twin v3 Decision Compass.

The application layer owns the Stage 13A/B semantics.  This module only
validates the wire envelope, installs the existing private boundary and
serializes the bounded transient result.  The base route is provider-free;
Advisor is available only through the separate explicit preview/execute pair.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.adapters.advisor.cloudflare_workers_ai import CloudflareWorkersAiAdvisorPort
from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.compare import CompareExecutionContextV1
from second_brain.application.decision_compass import (
    CONTRACT_VERSION,
    MAX_RESULT_BYTES,
    BuildDecisionCompass,
    DecisionCompassErrorCodeV1,
    DecisionCompassErrorV1,
    DecisionCompassRequestV1,
    DecisionCompassResultV1,
    validate_decision_compass_result,
)
from second_brain.application.growth import create_growth_mapping_store
from second_brain.application.growth_advisor import (
    BuildGrowthAdvisor,
    GrowthAdvisorError,
    GrowthAdvisorErrorCode,
    GrowthAdvisorGoalPreviewV1,
)
from second_brain.application.ports import AdvisorPort, CancellationTokenSource
from second_brain.config import ConfigurationError, load_config
from second_brain.entrypoints.web.auth import (
    request_host_is_trusted,
    same_origin_is_trusted,
    trusted_authorities_from_scope,
)

DECISION_COMPASS_PATH: Final[str] = "/api/decision-compass"
DECISION_COMPASS_ADVISOR_PREVIEW_PATH: Final[str] = "/api/decision-compass/advisor/preview"
DECISION_COMPASS_ADVISOR_EXECUTE_PATH: Final[str] = "/api/decision-compass/advisor/execute"
DECISION_COMPASS_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
DECISION_COMPASS_REQUEST_HEADER_VALUE: Final[str] = "decision-compass-v1"
MAX_RAW_DECISION_COMPASS_BODY_BYTES: Final[int] = 64 * 1024
MAX_DECISION_COMPASS_RESPONSE_BYTES: Final[int] = 128 * 1024
DECISION_COMPASS_TOTAL_DEADLINE_SECONDS: Final[float] = 30.0

_DECISION_COMPASS_PATHS: Final[frozenset[str]] = frozenset(
    {
        DECISION_COMPASS_PATH,
        DECISION_COMPASS_ADVISOR_PREVIEW_PATH,
        DECISION_COMPASS_ADVISOR_EXECUTE_PATH,
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
_ERROR_STATUS: Final[dict[DecisionCompassErrorCodeV1, int]] = {
    DecisionCompassErrorCodeV1.INVALID_REQUEST: 400,
    DecisionCompassErrorCodeV1.GOAL_REQUIRED: 400,
    DecisionCompassErrorCodeV1.GOAL_UNAVAILABLE: 503,
    DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED: 409,
    DecisionCompassErrorCodeV1.BEHAVIORAL_SCOPE_INVALID: 400,
    DecisionCompassErrorCodeV1.OPTION_BINDING_INVALID: 400,
    DecisionCompassErrorCodeV1.SOURCE_UNAVAILABLE: 503,
    DecisionCompassErrorCodeV1.SOURCE_CHANGED: 409,
    DecisionCompassErrorCodeV1.POLICY_MISMATCH: 500,
    DecisionCompassErrorCodeV1.RESULT_TOO_LARGE: 413,
    DecisionCompassErrorCodeV1.CANCELLED: 409,
    DecisionCompassErrorCodeV1.TIMEOUT: 504,
    DecisionCompassErrorCodeV1.INTERNAL: 503,
}
_ERROR_MESSAGES: Final[dict[DecisionCompassErrorCodeV1, str]] = {
    DecisionCompassErrorCodeV1.INVALID_REQUEST: "Запрос Decision Compass некорректен.",
    DecisionCompassErrorCodeV1.GOAL_REQUIRED: "Требуется явная текущая Цель.",
    DecisionCompassErrorCodeV1.GOAL_UNAVAILABLE: "Выбранная Цель недоступна.",
    DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED: "Источник выбранной Цели изменился.",
    DecisionCompassErrorCodeV1.BEHAVIORAL_SCOPE_INVALID: (
        "Выбранный поведенческий контекст некорректен."
    ),
    DecisionCompassErrorCodeV1.OPTION_BINDING_INVALID: (
        "Связка варианта и поведенческого контекста некорректна."
    ),
    DecisionCompassErrorCodeV1.SOURCE_UNAVAILABLE: "Источник Decision Compass недоступен.",
    DecisionCompassErrorCodeV1.SOURCE_CHANGED: "Источник изменился во время сборки.",
    DecisionCompassErrorCodeV1.POLICY_MISMATCH: "Политика Decision Compass не подтверждена.",
    DecisionCompassErrorCodeV1.RESULT_TOO_LARGE: (
        "Результат Decision Compass превышает допустимый размер."
    ),
    DecisionCompassErrorCodeV1.CANCELLED: "Сборка Decision Compass отменена.",
    DecisionCompassErrorCodeV1.TIMEOUT: "Сборка Decision Compass превысила срок.",
    DecisionCompassErrorCodeV1.INTERNAL: "Decision Compass временно недоступен.",
}


class DecisionCompassWebService(Protocol):
    """Minimal injectable seam for the read and explicit Advisor actions."""

    def build(self, request: DecisionCompassRequestV1) -> object:
        """Build one provider-free transient Compass result."""

    def preview_advisor(self, request: DecisionCompassRequestV1) -> object:
        """Build the existing Growth Advisor preview without a provider call."""

    def execute_advisor(
        self,
        request: DecisionCompassRequestV1,
        preview: GrowthAdvisorGoalPreviewV1,
        *,
        confirmed: bool,
    ) -> object:
        """Run the explicitly confirmed Advisor branch."""


@dataclass(slots=True)
class ProductionDecisionCompassWebService:
    """Resolve vault, operational mapping store and Advisor lazily per action."""

    env_file: Path | None = None
    vault_path_override: str | None = None
    advisor: AdvisorPort | None = None

    def _runtime(self) -> BuildDecisionCompass:
        config = load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )
        if config.env_file is None:
            raise ConfigurationError("Decision Compass requires the configured environment file")
        reader = FileSystemVaultReader(config.vault_path)
        repository_root = Path(__file__).resolve().parents[4]
        store = create_growth_mapping_store(
            config.env_file,
            vault_root=config.vault_path,
            repository_root=repository_root,
        )
        growth_advisor = BuildGrowthAdvisor(
            reader,
            self.advisor or CloudflareWorkersAiAdvisorPort(),
        )
        return BuildDecisionCompass(
            reader=reader,
            store=store,
            growth_advisor=growth_advisor,
        )

    @staticmethod
    def _execution() -> CompareExecutionContextV1:
        cancellation = CancellationTokenSource()
        return CompareExecutionContextV1(
            cancellation=cancellation.token,
            deadline=float(time.monotonic() + DECISION_COMPASS_TOTAL_DEADLINE_SECONDS),
        )

    def build(self, request: DecisionCompassRequestV1) -> object:
        """Build only the provider-free Stage 13A projection."""

        return self._runtime().execute(request, execution=self._execution())

    def preview_advisor(self, request: DecisionCompassRequestV1) -> object:
        """Use the existing Growth Advisor preview boundary without the provider."""

        return self._runtime().preview_advisor(request)

    def execute_advisor(
        self,
        request: DecisionCompassRequestV1,
        preview: GrowthAdvisorGoalPreviewV1,
        *,
        confirmed: bool,
    ) -> object:
        """Rebuild a fresh base only for this explicit action, then hand off once."""

        runtime = self._runtime()
        execution = self._execution()
        base = runtime.execute(request, execution=execution)
        if not isinstance(base, DecisionCompassResultV1):
            return base
        return runtime.execute_advisor(
            request,
            base,
            preview,
            execution=execution,
            confirmed=confirmed,
        )


def build_production_decision_compass_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
    advisor: AdvisorPort | None = None,
) -> ProductionDecisionCompassWebService:
    """Build the lazy production service without reading the vault/provider."""

    return ProductionDecisionCompassWebService(
        env_file=env_file,
        vault_path_override=vault_path_override,
        advisor=advisor,
    )


class DecisionCompassOptionPayload(BaseModel):
    """Strict request-local option; labels never become identity."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: StrictStr
    label: StrictStr


class DecisionCompassCriterionPayload(BaseModel):
    """Strict inert owner criterion with no score or weight field."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: StrictStr
    label: StrictStr
    description: StrictStr | None = None


class DecisionCompassGoalPayload(BaseModel):
    """Exact Goal UUID/fingerprint selector."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_uuid: StrictStr
    identity_fingerprint: StrictStr


class DecisionCompassBehaviorScopePayload(BaseModel):
    """Optional exact current Behavioral cohort selector."""

    model_config = ConfigDict(extra="forbid", strict=True)

    behavioral_cohort_fingerprint: StrictStr


class DecisionCompassBehaviorBindingPayload(BaseModel):
    """Ephemeral exact request-option to Behavioral option bridge."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request_option_id: StrictStr
    behavioral_cohort_fingerprint: StrictStr
    behavioral_option_index: StrictInt
    behavioral_option_fingerprint: StrictStr


class DecisionCompassContextPayload(BaseModel):
    """Strict explicit context item; nested unknown fields are forbidden."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: StrictStr
    text: StrictStr


class DecisionCompassRequestPayload(BaseModel):
    """Strict transport projection of the already-approved core request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    contract_version: StrictStr = CONTRACT_VERSION
    task: StrictStr
    options: list[DecisionCompassOptionPayload]
    selected_goal: DecisionCompassGoalPayload | None
    criteria: list[DecisionCompassCriterionPayload] = Field(default_factory=list)
    explicit_constraints: list[StrictStr] = Field(default_factory=list)
    explicit_context: list[DecisionCompassContextPayload] = Field(default_factory=list)
    progress_as_of: StrictStr
    behavioral_scope: DecisionCompassBehaviorScopePayload | None = None
    behavioral_option_binding: DecisionCompassBehaviorBindingPayload | None = None
    max_result_bytes: StrictInt = MAX_RESULT_BYTES

    def to_domain(self) -> DecisionCompassRequestV1:
        """Convert only after Pydantic has rejected unknown/non-strict fields."""

        return DecisionCompassRequestV1.from_dict(self.model_dump(mode="python"))


class DecisionCompassPreviewPayload(BaseModel):
    """Strict wire form of the existing Growth Advisor preview."""

    model_config = ConfigDict(extra="forbid", strict=True)

    contract_version: StrictStr
    goal_source_uuid: StrictStr
    goal_identity_fingerprint: StrictStr
    assistant_contract_version: StrictStr
    advisor_policy_id: StrictStr
    goal_text: StrictStr
    goal_text_utf8_bytes: StrictInt

    def to_domain(self) -> GrowthAdvisorGoalPreviewV1:
        return GrowthAdvisorGoalPreviewV1.from_dict(self.model_dump(mode="python"))


class DecisionCompassAdvisorExecutePayload(BaseModel):
    """Strict explicit-confirmation envelope; no base result is accepted from the client."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request: DecisionCompassRequestPayload
    preview: DecisionCompassPreviewPayload
    confirmed: StrictBool


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


def _error_payload(code: DecisionCompassErrorCodeV1) -> dict[str, object]:
    return {"error": {"code": code.value, "message": _ERROR_MESSAGES[code]}}


def _error_response(
    error: DecisionCompassErrorV1 | DecisionCompassErrorCodeV1,
    *,
    status_code: int | None = None,
) -> Response:
    if isinstance(error, DecisionCompassErrorV1):
        try:
            code = DecisionCompassErrorCodeV1(error.code)
        except TypeError, ValueError:
            code = DecisionCompassErrorCodeV1.INTERNAL
    else:
        code = error
    body = json.dumps(_error_payload(code), ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    headers = dict(_API_HEADERS)
    headers["Content-Type"] = "application/json; charset=utf-8"
    return Response(
        content=body,
        status_code=status_code if status_code is not None else _ERROR_STATUS[code],
        headers=headers,
    )


def _method_not_allowed() -> Response:
    response = _error_response(DecisionCompassErrorCodeV1.INVALID_REQUEST, status_code=405)
    response.headers["Allow"] = "POST"
    return response


def _too_large() -> Response:
    return _error_response(DecisionCompassErrorCodeV1.RESULT_TOO_LARGE, status_code=413)


async def _send(response: Response, scope: Scope, receive: Receive, send: Send) -> None:
    await response(scope, receive, send)


class DecisionCompassRequestBoundaryMiddleware:
    """Fail-closed Host, Origin, purpose, JSON and bounded raw-body boundary."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _DECISION_COMPASS_PATHS:
            await self.app(scope, receive, send)
            return
        if scope.get("method") != "POST":
            await _send(_method_not_allowed(), scope, receive, send)
            return
        header_names = [name.lower() for name, _ in scope.get("headers", [])]
        if any(header_names.count(name) > 1 for name in _SECURITY_HEADER_NAMES):
            await _send(
                _error_response(DecisionCompassErrorCodeV1.INVALID_REQUEST), scope, receive, send
            )
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
            or purpose != DECISION_COMPASS_REQUEST_HEADER_VALUE
            or not _json_content_type(content_type)
        ):
            await _send(
                _error_response(DecisionCompassErrorCodeV1.INVALID_REQUEST), scope, receive, send
            )
            return
        _, content_length = _header(scope, b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length.strip())
            except ValueError:
                await _send(
                    _error_response(DecisionCompassErrorCodeV1.INVALID_REQUEST),
                    scope,
                    receive,
                    send,
                )
                return
            if declared < 0:
                await _send(
                    _error_response(DecisionCompassErrorCodeV1.INVALID_REQUEST),
                    scope,
                    receive,
                    send,
                )
                return
            if declared > MAX_RAW_DECISION_COMPASS_BODY_BYTES:
                await _send(_too_large(), scope, receive, send)
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                await _send(
                    _error_response(DecisionCompassErrorCodeV1.INVALID_REQUEST),
                    scope,
                    receive,
                    send,
                )
                return
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                await _send(_too_large(), scope, receive, send)
                return
            body.extend(chunk)
            if len(body) > MAX_RAW_DECISION_COMPASS_BODY_BYTES:
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
    if not raw or len(raw) > MAX_RAW_DECISION_COMPASS_BODY_BYTES:
        raise ValueError
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError:
        raise ValueError from None


def _response(content: Mapping[str, object]) -> Response:
    body = json.dumps(dict(content), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_DECISION_COMPASS_RESPONSE_BYTES:
        return _error_response(DecisionCompassErrorCodeV1.RESULT_TOO_LARGE, status_code=413)
    headers = dict(_API_HEADERS)
    headers["Content-Type"] = "application/json; charset=utf-8"
    return Response(content=body, status_code=200, headers=headers)


def _result_response(value: object) -> Response:
    if isinstance(value, DecisionCompassErrorV1):
        return _error_response(value)
    if type(value) is not DecisionCompassResultV1:
        return _error_response(DecisionCompassErrorCodeV1.INTERNAL)
    try:
        result = validate_decision_compass_result(value)
        payload = result.as_dict()
        if not isinstance(payload, dict):
            return _error_response(DecisionCompassErrorCodeV1.INTERNAL)
        return _response(payload)
    except Exception:
        return _error_response(DecisionCompassErrorCodeV1.INTERNAL)


def _preview_error(error: GrowthAdvisorError) -> Response:
    code = str(error.code)
    if code == GrowthAdvisorErrorCode.GOAL_CHANGED.value:
        return _error_response(DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED)
    if code in {
        GrowthAdvisorErrorCode.GOAL_MISSING.value,
        GrowthAdvisorErrorCode.GOAL_UNAVAILABLE.value,
    }:
        return _error_response(DecisionCompassErrorCodeV1.GOAL_UNAVAILABLE)
    if code == GrowthAdvisorErrorCode.INVALID_REQUEST.value:
        return _error_response(DecisionCompassErrorCodeV1.INVALID_REQUEST)
    return _error_response(DecisionCompassErrorCodeV1.INTERNAL)


def install_decision_compass_routes(
    app: FastAPI,
    *,
    service: DecisionCompassWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install the base provider-free route and separate Advisor actions."""

    actual_service = service or build_production_decision_compass_service(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    app.add_middleware(DecisionCompassRequestBoundaryMiddleware)

    @app.post(DECISION_COMPASS_PATH, include_in_schema=False)
    async def decision_compass_endpoint(request: Request) -> Response:
        try:
            raw = await _read_json(request)
            payload = DecisionCompassRequestPayload.model_validate(raw, strict=True)
            domain_request = payload.to_domain()
            return _result_response(await run_in_threadpool(actual_service.build, domain_request))
        except ConfigurationError:
            return _error_response(DecisionCompassErrorCodeV1.SOURCE_UNAVAILABLE)
        except ValueError, TypeError, UnicodeError:
            return _error_response(DecisionCompassErrorCodeV1.INVALID_REQUEST)
        except Exception:
            return _error_response(DecisionCompassErrorCodeV1.INTERNAL)

    @app.post(DECISION_COMPASS_ADVISOR_PREVIEW_PATH, include_in_schema=False)
    async def decision_compass_advisor_preview_endpoint(request: Request) -> Response:
        try:
            raw = await _read_json(request)
            payload = DecisionCompassRequestPayload.model_validate(raw, strict=True)
            preview = await run_in_threadpool(
                actual_service.preview_advisor,
                payload.to_domain(),
            )
            if type(preview) is not GrowthAdvisorGoalPreviewV1:
                return _error_response(DecisionCompassErrorCodeV1.INTERNAL)
            return _response(preview.as_dict())
        except GrowthAdvisorError as error:
            return _preview_error(error)
        except ValueError, TypeError, UnicodeError:
            return _error_response(DecisionCompassErrorCodeV1.INVALID_REQUEST)
        except ConfigurationError:
            return _error_response(DecisionCompassErrorCodeV1.SOURCE_UNAVAILABLE)
        except Exception:
            return _error_response(DecisionCompassErrorCodeV1.INTERNAL)

    @app.post(DECISION_COMPASS_ADVISOR_EXECUTE_PATH, include_in_schema=False)
    async def decision_compass_advisor_execute_endpoint(request: Request) -> Response:
        try:
            raw = await _read_json(request)
            payload = DecisionCompassAdvisorExecutePayload.model_validate(raw, strict=True)
            if not payload.confirmed:
                return _error_response(DecisionCompassErrorCodeV1.INVALID_REQUEST)
            result = await run_in_threadpool(
                actual_service.execute_advisor,
                payload.request.to_domain(),
                payload.preview.to_domain(),
                confirmed=payload.confirmed,
            )
            return _result_response(result)
        except ValueError, TypeError, UnicodeError:
            return _error_response(DecisionCompassErrorCodeV1.INVALID_REQUEST)
        except ConfigurationError:
            return _error_response(DecisionCompassErrorCodeV1.SOURCE_UNAVAILABLE)
        except Exception:
            return _error_response(DecisionCompassErrorCodeV1.INTERNAL)


__all__ = [
    "DECISION_COMPASS_ADVISOR_EXECUTE_PATH",
    "DECISION_COMPASS_ADVISOR_PREVIEW_PATH",
    "DECISION_COMPASS_PATH",
    "DECISION_COMPASS_REQUEST_HEADER_NAME",
    "DECISION_COMPASS_REQUEST_HEADER_VALUE",
    "DECISION_COMPASS_TOTAL_DEADLINE_SECONDS",
    "MAX_DECISION_COMPASS_RESPONSE_BYTES",
    "MAX_RAW_DECISION_COMPASS_BODY_BYTES",
    "DecisionCompassAdvisorExecutePayload",
    "DecisionCompassBehaviorBindingPayload",
    "DecisionCompassBehaviorScopePayload",
    "DecisionCompassContextPayload",
    "DecisionCompassCriterionPayload",
    "DecisionCompassGoalPayload",
    "DecisionCompassOptionPayload",
    "DecisionCompassPreviewPayload",
    "DecisionCompassRequestBoundaryMiddleware",
    "DecisionCompassRequestPayload",
    "DecisionCompassWebService",
    "ProductionDecisionCompassWebService",
    "build_production_decision_compass_service",
    "install_decision_compass_routes",
]
