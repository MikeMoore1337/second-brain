"""Bounded private Web/API composition for Cognitive Twin Stage 10D.

The module deliberately keeps the Web boundary thin.  Stage 10B and Stage
10C own all source authority, identity construction, drift checks and durable
store guarantees; this layer only validates transport metadata and projects
their already-validated DTOs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, cast
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.behavioral_self_model import (
    BehavioralPatternStateV1,
    BehavioralPatternTypeV1,
    BehavioralSelfModelError,
    BehavioralSelfModelErrorCode,
    BehavioralSelfModelRequest,
    BehavioralSelfModelResultV1,
    BuildBehavioralSelfModel,
    validate_behavioral_self_model_result,
)
from second_brain.application.self_model import SelfModelError
from second_brain.application.stated_observed_mapping import (
    MAX_REVIEW_PROJECTION_BYTES,
    MappingLifecycleStateV1,
    StatedObservedCompositionResultV1,
    StatedObservedMappingAcceptanceRequest,
    StatedObservedMappingError,
    StatedObservedMappingErrorCode,
    StatedObservedMappingReviewProjectionV1,
    StatedObservedMappingReviewRequest,
    StatedObservedMappingSelectorV1,
    StatedObservedMappingService,
    StatedObservedMappingV1,
    build_stated_observed_mapping_service,
)
from second_brain.config import ConfigurationError, load_config
from second_brain.entrypoints.web.auth import (
    request_host_is_trusted,
    same_origin_is_trusted,
    trusted_authorities_from_scope,
)

BEHAVIORAL_SELF_MODEL_PATH: Final[str] = "/api/behavioral-self-model"
STATED_OBSERVED_MAPPING_STATUS_PATH: Final[str] = "/api/stated-observed-mapping/status"
STATED_OBSERVED_MAPPING_REVIEW_PATH: Final[str] = "/api/stated-observed-mapping/review"
STATED_OBSERVED_MAPPING_CONFIRM_PATH: Final[str] = "/api/stated-observed-mapping/confirm"
STATED_OBSERVED_COMPOSITION_PATH: Final[str] = "/api/stated-observed-composition"
COGNITIVE_TWIN_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
COGNITIVE_TWIN_REQUEST_HEADER_VALUE: Final[str] = "cognitive-twin-v1"
MAX_RAW_COGNITIVE_TWIN_BODY_BYTES: Final[int] = MAX_REVIEW_PROJECTION_BYTES
MAX_COGNITIVE_TWIN_STATUS_BYTES: Final[int] = 128 * 1024
MAX_COGNITIVE_TWIN_ACCEPTED_BYTES: Final[int] = 24 * 1024

_COGNITIVE_TWIN_PATHS: Final[frozenset[str]] = frozenset(
    {
        BEHAVIORAL_SELF_MODEL_PATH,
        STATED_OBSERVED_MAPPING_STATUS_PATH,
        STATED_OBSERVED_MAPPING_REVIEW_PATH,
        STATED_OBSERVED_MAPPING_CONFIRM_PATH,
        STATED_OBSERVED_COMPOSITION_PATH,
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

_BEHAVIORAL_MESSAGES: Final[dict[str, tuple[int, str]]] = {
    BehavioralSelfModelErrorCode.INVALID_REQUEST.value: (
        400,
        "Запрос наблюдаемого поведения не прошёл проверку.",
    ),
    BehavioralSelfModelErrorCode.SOURCE_UNAVAILABLE.value: (
        503,
        "Текущие наблюдаемые решения недоступны.",
    ),
    BehavioralSelfModelErrorCode.INVALID_JOURNAL.value: (
        409,
        "Текущие решения не прошли проверку.",
    ),
    BehavioralSelfModelErrorCode.INSUFFICIENT_COMPARABLE_DECISIONS.value: (
        409,
        "Текущие наблюдаемые решения нельзя сопоставить.",
    ),
    BehavioralSelfModelErrorCode.AMBIGUOUS_GROUPING.value: (
        409,
        "Текущая exact-группа наблюдений неоднозначна.",
    ),
    BehavioralSelfModelErrorCode.UNSUPPORTED_SEMANTIC_COMPARISON.value: (
        400,
        "Запрошено сопоставление вне exact-политики.",
    ),
    BehavioralSelfModelErrorCode.RESULT_TOO_LARGE.value: (
        413,
        "Результат наблюдаемого поведения слишком велик.",
    ),
    BehavioralSelfModelErrorCode.POLICY_MISMATCH.value: (
        500,
        "Политика наблюдаемого поведения недоступна.",
    ),
}

_MAPPING_MESSAGES: Final[dict[str, tuple[int, str]]] = {
    StatedObservedMappingErrorCode.INVALID_REQUEST.value: (
        400,
        "Запрос сопоставления не прошёл проверку.",
    ),
    StatedObservedMappingErrorCode.STATED_SOURCE_UNAVAILABLE.value: (
        503,
        "Текущий stated-источник недоступен.",
    ),
    StatedObservedMappingErrorCode.STATED_SOURCE_CHANGED.value: (
        409,
        "Stated-источник изменился; требуется новый review.",
    ),
    StatedObservedMappingErrorCode.UNSUPPORTED_STATED_DIMENSION.value: (
        409,
        "Этот stated-слой нельзя сопоставить по текущей политике.",
    ),
    StatedObservedMappingErrorCode.BEHAVIORAL_SOURCE_UNAVAILABLE.value: (
        503,
        "Текущий behavioral-источник недоступен.",
    ),
    StatedObservedMappingErrorCode.BEHAVIORAL_COHORT_CHANGED.value: (
        409,
        "Behavioral cohort изменилась; требуется новый review.",
    ),
    StatedObservedMappingErrorCode.BEHAVIORAL_OPTION_CHANGED.value: (
        409,
        "Behavioral option изменилась; требуется новый review.",
    ),
    StatedObservedMappingErrorCode.BEHAVIORAL_EVIDENCE_INSUFFICIENT.value: (
        409,
        "Сопоставимых behavioral-свидетельств недостаточно.",
    ),
    StatedObservedMappingErrorCode.MISSING.value: (
        200,
        "Активного сопоставления нет.",
    ),
    StatedObservedMappingErrorCode.INVALID.value: (
        503,
        "Сохранённое сопоставление не прошло проверку.",
    ),
    StatedObservedMappingErrorCode.STALE.value: (
        409,
        "Review устарел; требуется новая проверка.",
    ),
    StatedObservedMappingErrorCode.AMBIGUOUS.value: (
        409,
        "Текущее сопоставление неоднозначно.",
    ),
    StatedObservedMappingErrorCode.POLICY_MISMATCH.value: (
        500,
        "Политика сопоставления недоступна.",
    ),
    StatedObservedMappingErrorCode.NOT_COMPARABLE.value: (
        409,
        "Текущие источники нельзя сопоставить по exact-политике.",
    ),
    StatedObservedMappingErrorCode.RESULT_TOO_LARGE.value: (
        413,
        "Результат сопоставления слишком велик.",
    ),
    StatedObservedMappingErrorCode.STORE_UNAVAILABLE.value: (
        503,
        "Операционное хранилище сопоставлений недоступно.",
    ),
    StatedObservedMappingErrorCode.STORE_CORRUPT.value: (
        503,
        "Операционное хранилище сопоставлений не прошло проверку целостности.",
    ),
    StatedObservedMappingErrorCode.IDEMPOTENCY_CONFLICT.value: (
        409,
        "Повтор операции сопоставления конфликтует с уже принятой операцией.",
    ),
    StatedObservedMappingErrorCode.CONCURRENCY_CONFLICT.value: (
        409,
        "Операция сопоставления конфликтует с текущим состоянием.",
    ),
    StatedObservedMappingErrorCode.STATED_EVIDENCE_MISSING.value: (
        409,
        "Stated-свидетельство для сопоставления отсутствует.",
    ),
}


class BehavioralSelfModelService(Protocol):
    """Minimal seam for one current Stage 10B rebuild."""

    def build(self, request: BehavioralSelfModelRequest) -> BehavioralSelfModelResultV1:
        """Build the current raw-label-free Stage 10B result."""


class StatedObservedMappingWebService(Protocol):
    """Minimal seam over the already-validated Stage 10C runtime."""

    def review(
        self,
        request: StatedObservedMappingReviewRequest | StatedObservedMappingSelectorV1,
    ) -> StatedObservedMappingReviewProjectionV1:
        """Build one transient review projection."""

    def accept(self, request: StatedObservedMappingAcceptanceRequest) -> StatedObservedMappingV1:
        """Freshly validate and durably accept one explicit mapping."""

    def compose(self, source_note_uuid: UUID | None = None) -> StatedObservedCompositionResultV1:
        """Build one current read-only composition result."""

    def snapshot(self) -> tuple[object, ...]:
        """Return bounded lifecycle metadata without raw source content."""


@dataclass(frozen=True, slots=True)
class LazyVaultBehavioralSelfModelService:
    """Resolve config and scan only for an explicit authenticated refresh."""

    env_file: Path | None = None
    vault_path_override: str | None = None

    def build(self, request: BehavioralSelfModelRequest) -> BehavioralSelfModelResultV1:
        config = load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )
        return BuildBehavioralSelfModel(FileSystemVaultReader(config.vault_path)).execute(request)


@dataclass(frozen=True, slots=True)
class LazyVaultStatedObservedMappingService:
    """Resolve the dedicated Stage 10C store only for a real mapping operation."""

    env_file: Path | None = None
    vault_path_override: str | None = None

    def _service(self) -> StatedObservedMappingService:
        config = load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )
        if config.env_file is None:
            raise StatedObservedMappingError(StatedObservedMappingErrorCode.STORE_UNAVAILABLE)
        repository_root = Path(__file__).resolve().parents[4]
        return build_stated_observed_mapping_service(
            FileSystemVaultReader(config.vault_path),
            env_file=config.env_file,
            vault_root=config.vault_path,
            repository_root=repository_root,
        )

    def review(
        self,
        request: StatedObservedMappingReviewRequest | StatedObservedMappingSelectorV1,
    ) -> StatedObservedMappingReviewProjectionV1:
        return self._service().review(request)

    def accept(self, request: StatedObservedMappingAcceptanceRequest) -> StatedObservedMappingV1:
        return self._service().accept(request)

    def compose(self, source_note_uuid: UUID | None = None) -> StatedObservedCompositionResultV1:
        return self._service().compose(source_note_uuid)

    def snapshot(self) -> tuple[object, ...]:
        return self._service().snapshot()


def build_production_behavioral_self_model_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> LazyVaultBehavioralSelfModelService:
    """Build a lazy production Stage 10B Web service."""

    return LazyVaultBehavioralSelfModelService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


def build_production_stated_observed_mapping_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> LazyVaultStatedObservedMappingService:
    """Build a lazy production Stage 10C Web service."""

    return LazyVaultStatedObservedMappingService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


class BehavioralSelfModelPayload(BaseModel):
    """Strict empty request; Stage 10 policy is server-owned."""

    model_config = ConfigDict(extra="forbid", strict=True)


class StatedObservedMappingStatusPayload(BaseModel):
    """Strict empty lifecycle read; no generic JSONL explorer is exposed."""

    model_config = ConfigDict(extra="forbid", strict=True)


class StatedObservedMappingSelectorPayload(BaseModel):
    """Transport-only intent selector; the backend rebuilds every identity."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_note_uuid: StrictStr
    behavioral_cohort_fingerprint: StrictStr
    behavioral_option_index: StrictInt
    behavioral_option_fingerprint: StrictStr


class StatedObservedMappingReviewPayload(StatedObservedMappingSelectorPayload):
    """Explicit review intent with no client-supplied source authority."""


class StatedObservedMappingConfirmPayload(StatedObservedMappingSelectorPayload):
    """Final owner confirmation; the server performs the mandatory fresh rebuild."""

    operation_id: StrictStr
    confirmed: StrictBool
    supersedes_mapping_id: StrictStr | None = None


class StatedObservedCompositionPayload(BaseModel):
    """Optional exact source selector for one composition read."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_note_uuid: StrictStr | None = None


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


def _error_response(code: str, *, status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=_API_HEADERS,
    )


def _invalid_request() -> JSONResponse:
    return _error_response(
        "COGNITIVE_TWIN_INVALID_REQUEST",
        status_code=400,
        message="Запрос Cognitive Twin не прошёл проверку.",
    )


def _too_large() -> JSONResponse:
    return _error_response(
        "COGNITIVE_TWIN_CONTENT_TOO_LARGE",
        status_code=413,
        message="Тело запроса Cognitive Twin слишком велико.",
    )


def _method_not_allowed() -> JSONResponse:
    return _error_response(
        "COGNITIVE_TWIN_METHOD_NOT_ALLOWED",
        status_code=405,
        message="Для этого закрытого API разрешён только метод POST.",
    )


async def _send(response: Response, scope: Scope, receive: Receive, send: Send) -> None:
    await response(scope, receive, send)


class CognitiveTwinRequestBoundaryMiddleware:
    """Fail-closed same-origin, purpose and bounded raw-body boundary."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _COGNITIVE_TWIN_PATHS:
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
            or (
                origin_present
                and (origin is None or not same_origin_is_trusted(scope, origin, authorities))
            )
            or purpose != COGNITIVE_TWIN_REQUEST_HEADER_VALUE
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
            if declared > MAX_RAW_COGNITIVE_TWIN_BODY_BYTES:
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
            if len(body) > MAX_RAW_COGNITIVE_TWIN_BODY_BYTES:
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


async def _read_payload(request: Request, payload_type: type[BaseModel]) -> BaseModel:
    raw = await request.json()
    return payload_type.model_validate(raw, strict=True)


def _uuid7_text(value: str, *, required: bool = True) -> UUID | None:
    if not required and value == "":
        return None
    try:
        parsed = UUID(value)
    except AttributeError, TypeError, ValueError:
        raise ValueError from None
    if str(parsed) != value or parsed.version != 7:
        raise ValueError
    return parsed


def _selector(payload: StatedObservedMappingSelectorPayload) -> StatedObservedMappingSelectorV1:
    source_uuid = _uuid7_text(payload.source_note_uuid)
    if source_uuid is None:
        raise ValueError
    try:
        return StatedObservedMappingSelectorV1(
            source_note_uuid=source_uuid,
            behavioral_cohort_fingerprint=payload.behavioral_cohort_fingerprint,
            behavioral_option_index=payload.behavioral_option_index,
            behavioral_option_fingerprint=payload.behavioral_option_fingerprint,
        )
    except TypeError, ValueError:
        raise ValueError from None


def _operation_id(value: str) -> UUID:
    parsed = _uuid7_text(value)
    if parsed is None:
        raise ValueError
    return parsed


def _mapping_error(error: StatedObservedMappingError) -> JSONResponse:
    status, message = _MAPPING_MESSAGES.get(
        error.code,
        (503, "Операция сопоставления сейчас недоступна."),
    )
    return _error_response(error.code, status_code=status, message=message)


def _behavioral_error(error: BehavioralSelfModelError) -> JSONResponse:
    status, message = _BEHAVIORAL_MESSAGES.get(
        error.code,
        (503, "Наблюдаемый слой сейчас недоступен."),
    )
    return _error_response(error.code, status_code=status, message=message)


def _lifecycle_projection(view: object) -> dict[str, object]:
    """Project lifecycle metadata without raw claim, Journal or option labels."""

    lifecycle_state = getattr(view, "lifecycle_state", None)
    record = getattr(view, "record", None)
    if not isinstance(record, StatedObservedMappingV1):
        raise ValueError
    if not isinstance(lifecycle_state, MappingLifecycleStateV1):
        raise ValueError
    pattern_type = record.behavioral.pattern_type
    pattern_state = record.behavioral.pattern_state
    if not isinstance(pattern_type, BehavioralPatternTypeV1) or not isinstance(
        pattern_state, BehavioralPatternStateV1
    ):
        raise ValueError
    return {
        "mapping_id": str(record.mapping_id),
        "lifecycle_state": lifecycle_state.value,
        "source_note_uuid": str(record.stated.source_note_uuid),
        "domain": record.stated.domain,
        "behavioral_cohort_fingerprint": record.behavioral.cohort.cohort_fingerprint,
        "behavioral_option_index": record.behavioral.option.option_index,
        "behavioral_option_fingerprint": record.behavioral.option.option_fingerprint,
        "pattern_type": pattern_type.value,
        "pattern_state": pattern_state.value,
        "mapping_fingerprint": record.mapping_fingerprint,
        "mapping_policy_fingerprint": record.mapping_policy_fingerprint,
        "created_at": record.created_at.isoformat().replace("+00:00", "Z"),
        "reviewed_at": record.reviewed_at.isoformat().replace("+00:00", "Z"),
        "supersedes_mapping_id": (
            str(record.supersedes_mapping_id) if record.supersedes_mapping_id is not None else None
        ),
    }


def _status_payload(service: StatedObservedMappingWebService) -> dict[str, object]:
    views = service.snapshot()
    if type(views) is not tuple:
        raise ValueError
    mappings = [_lifecycle_projection(view) for view in views]
    body = {
        "mapping_policy_id": "stated-observed-explicit-mapping-v1",
        "mappings": mappings,
        "active_mapping_count": sum(item["lifecycle_state"] == "active" for item in mappings),
    }
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    if len(encoded) > MAX_COGNITIVE_TWIN_STATUS_BYTES:
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.RESULT_TOO_LARGE)
    return body


def _projection_response(projection: StatedObservedMappingReviewProjectionV1) -> Response:
    raw = projection.to_json().encode("utf-8")
    if len(raw) > MAX_REVIEW_PROJECTION_BYTES:
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.RESULT_TOO_LARGE)
    return Response(content=raw, media_type="application/json", headers=_API_HEADERS)


def install_cognitive_twin_routes(
    app: FastAPI,
    *,
    behavioral_self_model_service: BehavioralSelfModelService | None = None,
    stated_observed_mapping_service: StatedObservedMappingWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install hidden, owner-authenticated Stage 10B/10C POST routes."""

    behavioral = behavioral_self_model_service or build_production_behavioral_self_model_service(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    mapping = stated_observed_mapping_service or build_production_stated_observed_mapping_service(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    app.add_middleware(CognitiveTwinRequestBoundaryMiddleware)

    @app.post(BEHAVIORAL_SELF_MODEL_PATH, include_in_schema=False)
    async def behavioral_self_model_endpoint(request: Request) -> Response:
        try:
            await _read_payload(request, BehavioralSelfModelPayload)
            result = await run_in_threadpool(
                behavioral.build,
                BehavioralSelfModelRequest(),
            )
            validate_behavioral_self_model_result(result)
            return Response(
                content=result.to_json().encode("utf-8"),
                media_type="application/json",
                headers=_API_HEADERS,
            )
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except BehavioralSelfModelError as error:
            return _behavioral_error(error)
        except ConfigurationError:
            return _error_response(
                BehavioralSelfModelErrorCode.SOURCE_UNAVAILABLE.value,
                status_code=503,
                message=_BEHAVIORAL_MESSAGES[BehavioralSelfModelErrorCode.SOURCE_UNAVAILABLE.value][
                    1
                ],
            )
        except SelfModelError:
            return _error_response(
                BehavioralSelfModelErrorCode.SOURCE_UNAVAILABLE.value,
                status_code=503,
                message=_BEHAVIORAL_MESSAGES[BehavioralSelfModelErrorCode.SOURCE_UNAVAILABLE.value][
                    1
                ],
            )
        except Exception:
            return _error_response(
                BehavioralSelfModelErrorCode.SOURCE_UNAVAILABLE.value,
                status_code=503,
                message=_BEHAVIORAL_MESSAGES[BehavioralSelfModelErrorCode.SOURCE_UNAVAILABLE.value][
                    1
                ],
            )

    @app.post(STATED_OBSERVED_MAPPING_STATUS_PATH, include_in_schema=False)
    async def stated_observed_mapping_status_endpoint(request: Request) -> Response:
        try:
            await _read_payload(request, StatedObservedMappingStatusPayload)
            body = await run_in_threadpool(_status_payload, mapping)
            return JSONResponse(content=body, headers=_API_HEADERS)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except StatedObservedMappingError as error:
            return _mapping_error(error)
        except ConfigurationError:
            return _mapping_error(
                StatedObservedMappingError(StatedObservedMappingErrorCode.STORE_UNAVAILABLE)
            )
        except Exception:
            return _mapping_error(
                StatedObservedMappingError(StatedObservedMappingErrorCode.STORE_UNAVAILABLE)
            )

    @app.post(STATED_OBSERVED_MAPPING_REVIEW_PATH, include_in_schema=False)
    async def stated_observed_mapping_review_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                StatedObservedMappingReviewPayload,
                await _read_payload(request, StatedObservedMappingReviewPayload),
            )
            selector = _selector(payload)
            projection = await run_in_threadpool(mapping.review, selector)
            return _projection_response(projection)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except StatedObservedMappingError as error:
            return _mapping_error(error)
        except ConfigurationError:
            return _mapping_error(
                StatedObservedMappingError(StatedObservedMappingErrorCode.STORE_UNAVAILABLE)
            )
        except Exception:
            return _mapping_error(
                StatedObservedMappingError(StatedObservedMappingErrorCode.STATED_SOURCE_UNAVAILABLE)
            )

    @app.post(STATED_OBSERVED_MAPPING_CONFIRM_PATH, include_in_schema=False)
    async def stated_observed_mapping_confirm_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                StatedObservedMappingConfirmPayload,
                await _read_payload(request, StatedObservedMappingConfirmPayload),
            )
            selector = _selector(payload)
            request_value = StatedObservedMappingAcceptanceRequest(
                selector=selector,
                operation_id=_operation_id(payload.operation_id),
                confirmed=payload.confirmed,
                # The review projection is intentionally not echoed by the
                # browser.  Stage 10C still performs its mandatory fresh
                # current rebuild; any supplied projection remains optional
                # for direct application callers and is never authority.
                review_projection=None,
                supersedes_mapping_id=(
                    _uuid7_text(payload.supersedes_mapping_id)
                    if payload.supersedes_mapping_id is not None
                    else None
                ),
            )
            accepted = await run_in_threadpool(mapping.accept, request_value)
            body = {"status": "accepted", "mapping": accepted.as_dict()}
            encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_COGNITIVE_TWIN_ACCEPTED_BYTES:
                raise StatedObservedMappingError(StatedObservedMappingErrorCode.RESULT_TOO_LARGE)
            return JSONResponse(content=body, headers=_API_HEADERS)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except StatedObservedMappingError as error:
            return _mapping_error(error)
        except ConfigurationError:
            return _mapping_error(
                StatedObservedMappingError(StatedObservedMappingErrorCode.STORE_UNAVAILABLE)
            )
        except Exception:
            return _mapping_error(
                StatedObservedMappingError(StatedObservedMappingErrorCode.STORE_UNAVAILABLE)
            )

    @app.post(STATED_OBSERVED_COMPOSITION_PATH, include_in_schema=False)
    async def stated_observed_composition_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                StatedObservedCompositionPayload,
                await _read_payload(request, StatedObservedCompositionPayload),
            )
            source_uuid = (
                _uuid7_text(payload.source_note_uuid)
                if payload.source_note_uuid is not None
                else None
            )
            result = await run_in_threadpool(mapping.compose, source_uuid)
            if type(result) is not StatedObservedCompositionResultV1:
                raise ValueError
            return JSONResponse(content=result.as_dict(), headers=_API_HEADERS)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except StatedObservedMappingError as error:
            return _mapping_error(error)
        except ConfigurationError:
            return _mapping_error(
                StatedObservedMappingError(StatedObservedMappingErrorCode.STORE_UNAVAILABLE)
            )
        except Exception:
            return _mapping_error(
                StatedObservedMappingError(StatedObservedMappingErrorCode.STORE_UNAVAILABLE)
            )


__all__ = [
    "BEHAVIORAL_SELF_MODEL_PATH",
    "COGNITIVE_TWIN_REQUEST_HEADER_NAME",
    "COGNITIVE_TWIN_REQUEST_HEADER_VALUE",
    "MAX_RAW_COGNITIVE_TWIN_BODY_BYTES",
    "STATED_OBSERVED_COMPOSITION_PATH",
    "STATED_OBSERVED_MAPPING_CONFIRM_PATH",
    "STATED_OBSERVED_MAPPING_REVIEW_PATH",
    "STATED_OBSERVED_MAPPING_STATUS_PATH",
    "BehavioralSelfModelPayload",
    "BehavioralSelfModelService",
    "CognitiveTwinRequestBoundaryMiddleware",
    "LazyVaultBehavioralSelfModelService",
    "LazyVaultStatedObservedMappingService",
    "StatedObservedCompositionPayload",
    "StatedObservedMappingConfirmPayload",
    "StatedObservedMappingReviewPayload",
    "StatedObservedMappingSelectorPayload",
    "StatedObservedMappingStatusPayload",
    "StatedObservedMappingWebService",
    "build_production_behavioral_self_model_service",
    "build_production_stated_observed_mapping_service",
    "install_cognitive_twin_routes",
]
