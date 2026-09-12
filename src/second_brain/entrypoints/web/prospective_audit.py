"""Bounded owner-only Web/API projection for Prospective Audit v1.

The Web layer composes the already-validated Stage 6/9 application cores.  It
keeps the operational store outside the vault and repository, derives its path
only from the explicitly supplied environment file, and never exposes query,
evidence, body, path, or raw exception details.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Final, Protocol, cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.prospective_audit import (
    AuditLogEnvelopeV1,
    BuildProspectiveAudit,
    BuildProspectiveCalibration,
    BuildProspectiveDecisionLink,
    DecisionJournalTargetV1,
    ProspectiveAuditError,
    ProspectiveAuditEventV1,
    ProspectiveAuditLinkError,
    ProspectiveAuditLinkInvalidError,
    ProspectiveAuditLinkReasonCode,
    ProspectiveAuditLinkUnavailableError,
    ProspectiveAuditStore,
    ProspectiveAuditStoreUnavailableError,
    ProspectiveCalibrationError,
    ProspectiveCalibrationErrorCodeV1,
    ProspectiveCalibrationRequestV1,
    ProspectiveOptionMappingV1,
    fingerprint_operation_id,
    normalize_operation_id,
    resolve_current_decision_journal,
    serialize_prospective_calibration_result,
)
from second_brain.application.reports import ScanReport
from second_brain.application.simulate_me import (
    SimulateMeError,
    SimulateMeRequest,
    SimulateMeResult,
)
from second_brain.application.validation import build_report
from second_brain.config import ConfigurationError, load_config
from second_brain.entrypoints.web.auth import (
    configured_authority_port,
    trusted_authorities_from_scope,
)
from second_brain.entrypoints.web.simulate_me import (
    SimulateMeOptionPayload,
    SimulateMeRequestPayload,
    SimulateMeService,
    build_production_simulate_me_service,
    simulate_me_request,
)

PROSPECTIVE_AUDIT_EXECUTE_PATH: Final[str] = "/api/prospective-audit/execute"
PROSPECTIVE_AUDIT_PENDING_PATH: Final[str] = "/api/prospective-audit/pending"
PROSPECTIVE_AUDIT_REVIEW_PATH: Final[str] = "/api/prospective-audit/link/review"
PROSPECTIVE_AUDIT_CONFIRM_PATH: Final[str] = "/api/prospective-audit/link/confirm"
PROSPECTIVE_AUDIT_CALIBRATION_PATH: Final[str] = "/api/prospective-audit/calibration"
PROSPECTIVE_AUDIT_REQUEST_HEADER_NAME: Final[str] = "X-Second-Brain-Request"
PROSPECTIVE_AUDIT_REQUEST_HEADER_VALUE: Final[str] = "prospective-audit-v1"
MAX_RAW_PROSPECTIVE_AUDIT_BODY_BYTES: Final[int] = 32 * 1024
MAX_PENDING_EVENTS: Final[int] = 32
MAX_DECISION_TARGETS: Final[int] = 32
PRODUCTION_PROSPECTIVE_AUDIT_STORE_ROOT: Final[Path] = Path(
    "/srv/second-brain/runtime/prospective-audit"
)
PRODUCTION_PROSPECTIVE_AUDIT_OWNER_GROUP: Final[tuple[str, str]] = (
    "second-brain",
    "second-brain",
)

_PROSPECTIVE_AUDIT_PATHS: Final[frozenset[str]] = frozenset(
    {
        PROSPECTIVE_AUDIT_EXECUTE_PATH,
        PROSPECTIVE_AUDIT_PENDING_PATH,
        PROSPECTIVE_AUDIT_REVIEW_PATH,
        PROSPECTIVE_AUDIT_CONFIRM_PATH,
        PROSPECTIVE_AUDIT_CALIBRATION_PATH,
    }
)
_STAGE9_LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "localhost", "::1"})
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

_AUDIT_MESSAGES: Mapping[str, tuple[int, str]] = {
    "PROSPECTIVE_AUDIT_INVALID_REQUEST": (
        400,
        "Запрос аудита прогноза не прошёл проверку.",
    ),
    "PROSPECTIVE_AUDIT_RESULT_INVALID": (
        500,
        "Результат прогноза не удалось безопасно записать.",
    ),
    "PROSPECTIVE_AUDIT_STALE_OR_REPLAYED": (
        409,
        "Результат прогноза устарел или уже использован.",
    ),
    "PROSPECTIVE_AUDIT_IDEMPOTENCY_CONFLICT": (
        409,
        "Повтор операции аудита конфликтует с уже записанным событием.",
    ),
    "PROSPECTIVE_AUDIT_STORE_UNAVAILABLE": (
        503,
        "Операционный журнал аудита сейчас недоступен.",
    ),
    "PROSPECTIVE_AUDIT_STORE_CORRUPT": (
        503,
        "Операционный журнал аудита не прошёл проверку целостности.",
    ),
    "PROSPECTIVE_AUDIT_LINK_INVALID": (
        409,
        "Явная связь с журналом решений не прошла проверку.",
    ),
    "PROSPECTIVE_AUDIT_LINK_UNAVAILABLE": (
        503,
        "Источник для связи с журналом решений сейчас недоступен.",
    ),
    "PROSPECTIVE_AUDIT_CANCELLED": (
        409,
        "Операция аудита прогноза отменена.",
    ),
}
_CALIBRATION_MESSAGES: Mapping[str, tuple[int, str]] = {
    ProspectiveCalibrationErrorCodeV1.UNAVAILABLE.value: (
        503,
        "Источники prospective aggregate сейчас недоступны.",
    ),
    ProspectiveCalibrationErrorCodeV1.RESULT_TOO_LARGE.value: (
        500,
        "Результат prospective aggregate слишком велик.",
    ),
}


class ProspectiveAuditExecutePayload(BaseModel):
    """Strict caller-owned Stage 6 input plus a one-operation control token."""

    model_config = ConfigDict(extra="forbid", strict=True)

    operation_id: StrictStr
    query: StrictStr
    options: list[SimulateMeOptionPayload]


class ProspectiveAuditPendingPayload(BaseModel):
    """Strict empty request for the explicit pending-review read."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ProspectiveAuditLinkReviewPayload(BaseModel):
    """Exact immutable IDs used to reread one event and one current Journal."""

    model_config = ConfigDict(extra="forbid", strict=True)

    audit_event_id: StrictStr
    decision_id: StrictStr


class ProspectiveAuditOptionMappingPayload(BaseModel):
    """Transport-only mapping with the current Journal option fingerprint."""

    model_config = ConfigDict(extra="forbid", strict=True)

    audit_option_id: StrictStr
    decision_option_index: StrictInt
    decision_option_fingerprint: StrictStr


class ProspectiveAuditLinkConfirmPayload(BaseModel):
    """Final owner confirmation; the server rereads all canonical sources."""

    model_config = ConfigDict(extra="forbid", strict=True)

    audit_event_id: StrictStr
    decision_id: StrictStr
    operation_id: StrictStr
    mapping: list[ProspectiveAuditOptionMappingPayload] = Field(max_length=20)
    confirmed: StrictBool


class ProspectiveAuditCalibrationPayload(BaseModel):
    """Strict empty request; Stage 9C has no caller-controlled policy knobs."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ProspectiveAuditWebService(Protocol):
    """Injectable seam over the provider-free Stage 9 application cores."""

    def execute(self, request: SimulateMeRequest, operation_id: str) -> Mapping[str, object]:
        """Execute Stage 6 once and durably record its terminal result."""

    def pending(self) -> Mapping[str, object]:
        """Return bounded unlinked events and current Journal candidates."""

    def review(self, audit_event_id: str, decision_id: str) -> Mapping[str, object]:
        """Reread one event and one current Journal before mapping."""

    def confirm(
        self,
        audit_event_id: str,
        decision_id: str,
        operation_id: str,
        mapping: tuple[ProspectiveOptionMappingV1, ...],
        confirmed: bool,
    ) -> Mapping[str, object]:
        """Revalidate and durably append one explicit link."""

    def calibration(self) -> bytes:
        """Build the exact bounded Stage 9C aggregate."""


@dataclass(frozen=True, slots=True)
class _Stage9Components:
    store: ProspectiveAuditStore
    reader: FileSystemVaultReader
    audit: BuildProspectiveAudit
    linker: BuildProspectiveDecisionLink
    calibration: BuildProspectiveCalibration


@dataclass(frozen=True, slots=True)
class _SimulateMeExecutor:
    service: SimulateMeService

    def execute(self, request: SimulateMeRequest) -> SimulateMeResult:
        return self.service.build(request)


def _canonical_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _event_projection(envelope: AuditLogEnvelopeV1) -> dict[str, object]:
    if type(envelope.event) is not ProspectiveAuditEventV1:
        raise ProspectiveAuditStoreUnavailableError()
    event = envelope.event
    options = tuple(
        {
            "id": option.id,
            "ordinal": option.ordinal,
            "label": option.label,
        }
        for option in event.request.options
    )
    predicted_option_id = event.result.predicted_option_id
    predicted_option_label = next(
        (option.label for option in event.request.options if option.id == predicted_option_id),
        None,
    )
    return {
        "event_id": event.event_id,
        "created_at": _canonical_time(event.created_at),
        "kind": event.result.kind.value,
        "options": list(options),
        "predicted_option_id": predicted_option_id,
        "predicted_option_label": predicted_option_label,
        "abstention_code": (
            None if event.result.abstention_code is None else event.result.abstention_code.value
        ),
        "derivation_version": event.derivation_version,
        "policy_id": event.policy_id,
    }


def _decision_projection(
    target: DecisionJournalTargetV1,
    *,
    include_fingerprints: bool,
) -> dict[str, object]:
    options: list[dict[str, object]] = []
    for index, label in enumerate(target.available_options):
        option: dict[str, object] = {"index": index, "label": label}
        if include_fingerprints:
            option["fingerprint"] = target.option_fingerprints[index]
        options.append(option)
    return {
        "decision_id": target.decision_id,
        "evidence_at": _canonical_time(target.evidence_at),
        "evidence_at_precision": target.evidence_at_precision.value,
        "created": _canonical_time(target.created),
        "options": options,
        "chosen_option_index": target.chosen_option_index,
        "chosen_option": target.chosen_option,
    }


def _event_for_operation(
    store: ProspectiveAuditStore,
    operation_id: str,
) -> AuditLogEnvelopeV1:
    operation_fingerprint = fingerprint_operation_id(operation_id)
    matches = tuple(
        envelope
        for envelope in store.read_events()
        if envelope.event.operation_id_fingerprint == operation_fingerprint
    )
    if len(matches) != 1:
        raise ProspectiveAuditStoreUnavailableError()
    return matches[0]


def _validate_production_runtime_parent(root: Path) -> None:
    """Refuse to create the approved child when its existing parent is unsafe."""

    if os.name == "nt" or root != PRODUCTION_PROSPECTIVE_AUDIT_STORE_ROOT:
        return
    parent = root.parent
    try:
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError("runtime parent is unavailable")
        item_stat = parent.stat()
        if item_stat.st_mode & 0o077:
            raise ValueError("runtime parent permissions are unsafe")
        import grp
        import pwd

        owner_name, group_name = PRODUCTION_PROSPECTIVE_AUDIT_OWNER_GROUP
        pwd_api = cast(Any, pwd)
        grp_api = cast(Any, grp)
        if (
            item_stat.st_uid != pwd_api.getpwnam(owner_name).pw_uid
            or item_stat.st_gid != grp_api.getgrnam(group_name).gr_gid
        ):
            raise ValueError("runtime parent owner is unsafe")
    except (KeyError, OSError, ValueError) as exc:
        raise ProspectiveAuditStoreUnavailableError() from exc


@dataclass(slots=True)
class LazyProductionProspectiveAuditService:
    """Resolve config/store/vault only after an explicit Stage 9 request."""

    env_file: Path | None = None
    vault_path_override: str | None = None
    store_root: Path | None = None
    expected_owner_group: tuple[str, str] | None = None
    _components: _Stage9Components | None = field(default=None, init=False, repr=False)
    _components_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def _get_components(self) -> _Stage9Components:
        components = self._components
        if components is not None:
            return components
        with self._components_lock:
            components = self._components
            if components is not None:
                return components
            if self.store_root is None:
                raise ProspectiveAuditStoreUnavailableError()
            _validate_production_runtime_parent(self.store_root)
            try:
                config = load_config(
                    env_file=self.env_file,
                    vault_path_override=self.vault_path_override,
                )
                reader = FileSystemVaultReader(config.vault_path)
                store = ProspectiveAuditStore(
                    root=self.store_root,
                    vault_root=config.vault_path,
                    expected_owner_group=self.expected_owner_group,
                )
            except ProspectiveAuditError:
                raise
            except (ConfigurationError, OSError, RuntimeError, ValueError, TypeError) as exc:
                raise ProspectiveAuditStoreUnavailableError() from exc
            simulate = build_production_simulate_me_service(
                env_file=self.env_file,
                vault_path_override=self.vault_path_override,
            )
            components = _Stage9Components(
                store=store,
                reader=reader,
                audit=BuildProspectiveAudit(_SimulateMeExecutor(simulate), store),
                linker=BuildProspectiveDecisionLink(store, reader),
                calibration=BuildProspectiveCalibration(store, reader),
            )
            self._components = components
            return components

    def execute(self, request: SimulateMeRequest, operation_id: str) -> Mapping[str, object]:
        components = self._get_components()
        components.audit.execute(request, operation_id)
        return {"event": _event_projection(_event_for_operation(components.store, operation_id))}

    def pending(self) -> Mapping[str, object]:
        components = self._get_components()
        events = tuple(
            envelope
            for envelope in components.store.active_events()
            if components.store.current_link_state(envelope.event.event_id).state.value
            == "ACTIVE_UNLINKED"
        )
        if len(events) > MAX_PENDING_EVENTS:
            raise ProspectiveAuditStoreUnavailableError()
        ordered_events = tuple(
            sorted(
                events,
                key=lambda item: (_canonical_time(item.event.created_at), item.event.event_id),
            )
        )
        if not ordered_events:
            return {"events": [], "decision_journals": [], "limits": self._limits()}
        try:
            report = build_report(components.reader.scan())
        except Exception as exc:
            raise ProspectiveAuditLinkUnavailableError(
                ProspectiveAuditLinkReasonCode.CANONICAL_SCAN_UNAVAILABLE
            ) from exc
        if report.manifest is None or not report.content_scan_complete:
            raise ProspectiveAuditLinkUnavailableError(
                ProspectiveAuditLinkReasonCode.CANONICAL_SCAN_UNAVAILABLE
            )

        def read_report() -> ScanReport:
            return report

        targets: dict[str, DecisionJournalTargetV1] = {}
        for note in report.notes:
            if note.note_id is None:
                continue
            try:
                target = resolve_current_decision_journal(
                    read_report,
                    str(note.note_id),
                )
            except ProspectiveAuditLinkError:
                continue
            targets[target.decision_id] = target
            if len(targets) > MAX_DECISION_TARGETS:
                raise ProspectiveAuditStoreUnavailableError()
        ordered_targets = tuple(targets[key] for key in sorted(targets))
        return {
            "events": [_event_projection(item) for item in ordered_events],
            "decision_journals": [
                _decision_projection(item, include_fingerprints=False) for item in ordered_targets
            ],
            "limits": self._limits(),
        }

    def review(self, audit_event_id: str, decision_id: str) -> Mapping[str, object]:
        components = self._get_components()
        envelope = components.store.read_event(audit_event_id)
        if envelope is None:
            raise ProspectiveAuditLinkUnavailableError(
                ProspectiveAuditLinkReasonCode.AUDIT_EVENT_MISSING_OR_DELETED
            )
        state = components.store.current_link_state(audit_event_id)
        if state.state.value != "ACTIVE_UNLINKED":
            if state.state.value == "EXPIRED":
                raise ProspectiveAuditLinkUnavailableError(
                    ProspectiveAuditLinkReasonCode.DECISION_TARGET_EXPIRED
                )
            raise ProspectiveAuditLinkError(ProspectiveAuditLinkReasonCode.LINK_OPERATION_CONFLICT)
        target = resolve_current_decision_journal(components.reader, decision_id)
        return {
            "event": _event_projection(envelope),
            "decision": _decision_projection(target, include_fingerprints=True),
            "mapping_basis": "owner-explicit-v1",
            "requires_confirmation": True,
        }

    def confirm(
        self,
        audit_event_id: str,
        decision_id: str,
        operation_id: str,
        mapping: tuple[ProspectiveOptionMappingV1, ...],
        confirmed: bool,
    ) -> Mapping[str, object]:
        if not confirmed:
            raise ProspectiveAuditLinkInvalidError(
                ProspectiveAuditLinkReasonCode.OPTION_MAPPING_INVALID
            )
        try:
            normalize_operation_id(operation_id)
        except Exception as exc:
            raise ProspectiveAuditError("PROSPECTIVE_AUDIT_INVALID_REQUEST") from exc
        components = self._get_components()
        link = components.linker.execute(audit_event_id, decision_id, mapping)
        return {
            "status": "linked",
            "audit_event_id": link.audit_event_id,
            "decision_id": link.decision_id,
            "link_state": "LINKED_VALID",
            "linked_at": _canonical_time(link.linked_at),
        }

    def calibration(self) -> bytes:
        components = self._get_components()
        result = components.calibration.execute(ProspectiveCalibrationRequestV1())
        return serialize_prospective_calibration_result(result)

    @staticmethod
    def _limits() -> dict[str, int]:
        return {
            "max_events": MAX_PENDING_EVENTS,
            "max_decision_journals": MAX_DECISION_TARGETS,
        }


def derive_prospective_audit_store_root(env_file: Path | os.PathLike[str] | None) -> Path | None:
    """Derive the store only from an explicit existing environment file."""

    if env_file is None:
        return None
    candidate = Path(env_file).expanduser()
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        selected = candidate.resolve(strict=True)
        root = selected.parent / "prospective-audit"
        if root.is_symlink() or root.parent.is_symlink() or not root.parent.is_dir():
            return None
        return root
    except OSError, RuntimeError, ValueError:
        return None


def build_production_prospective_audit_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> LazyProductionProspectiveAuditService:
    """Build a lazy service without config, store, vault, or network side effects."""

    root = derive_prospective_audit_store_root(env_file)
    expected_owner_group = (
        PRODUCTION_PROSPECTIVE_AUDIT_OWNER_GROUP
        if root is not None
        and root.resolve(strict=False) == PRODUCTION_PROSPECTIVE_AUDIT_STORE_ROOT
        else None
    )
    return LazyProductionProspectiveAuditService(
        env_file=env_file,
        vault_path_override=vault_path_override,
        store_root=root,
        expected_owner_group=expected_owner_group,
    )


def _safe_error(
    code: str, message: str, status_code: int, *, reason: str | None = None
) -> JSONResponse:
    error: dict[str, str] = {"code": code, "message": message}
    if reason is not None:
        error["reason"] = reason
    return JSONResponse(status_code=status_code, content={"error": error}, headers=_API_HEADERS)


def _invalid_request() -> JSONResponse:
    status, message = _AUDIT_MESSAGES["PROSPECTIVE_AUDIT_INVALID_REQUEST"]
    return _safe_error("PROSPECTIVE_AUDIT_INVALID_REQUEST", message, status)


def _too_large() -> JSONResponse:
    return _safe_error(
        "PROSPECTIVE_AUDIT_CONTENT_TOO_LARGE",
        "Объём запроса аудита прогноза превышает допустимый предел.",
        413,
    )


def _method_not_allowed() -> JSONResponse:
    response = _invalid_request()
    response.status_code = 405
    response.headers["Allow"] = "POST"
    return response


def _audit_error(error: ProspectiveAuditError) -> JSONResponse:
    fallback = _AUDIT_MESSAGES["PROSPECTIVE_AUDIT_STORE_UNAVAILABLE"]
    status, message = _AUDIT_MESSAGES.get(error.code, fallback)
    reason = error.reason_code if isinstance(error, ProspectiveAuditLinkError) else None
    code = error.code if error.code in _AUDIT_MESSAGES else "PROSPECTIVE_AUDIT_STORE_UNAVAILABLE"
    return _safe_error(code, message, status, reason=reason)


def _calibration_error(error: ProspectiveCalibrationError) -> JSONResponse:
    fallback = _CALIBRATION_MESSAGES[ProspectiveCalibrationErrorCodeV1.UNAVAILABLE.value]
    status, message = _CALIBRATION_MESSAGES.get(error.code, fallback)
    code = (
        error.code
        if error.code in _CALIBRATION_MESSAGES
        else ProspectiveCalibrationErrorCodeV1.UNAVAILABLE.value
    )
    return _safe_error(code, message, status)


def _header_map(scope: Scope) -> dict[bytes, bytes]:
    return {name.lower(): value for name, value in scope.get("headers", [])}


def _decode_header(headers: Mapping[bytes, bytes], name: bytes) -> str | None:
    value = headers.get(name)
    return None if value is None else value.decode("latin-1")


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
        or hostname.casefold() not in _STAGE9_LOOPBACK_HOSTS
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
    ):
        return None
    return (
        hostname.casefold(),
        443 if port is None and normalized_scheme == "https" else 80 if port is None else port,
    )


def _trusted_host(
    host_header: str | None,
    scheme: str,
    authorities: frozenset[tuple[str, int]],
) -> bool:
    loopback = _loopback_host_port(host_header, scheme)
    configured = configured_authority_port(host_header or "", scheme, authorities)
    return loopback is not None or configured is not None


def _same_origin(
    origin: str | None,
    host_header: str | None,
    scheme: str,
    authorities: frozenset[tuple[str, int]],
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
    return configured_authority_port(
        parsed.netloc, normalized_scheme, authorities
    ) is not None and configured_authority_port(
        host_header or "", normalized_scheme, authorities
    ) == configured_authority_port(parsed.netloc, normalized_scheme, authorities)


async def _send_json_response(
    response: JSONResponse,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    await response(scope, receive, send)


class ProspectiveAuditRequestBoundaryMiddleware:
    """Fail-closed trusted same-origin boundary for all Stage 9 POST routes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = cast(str, scope.get("path", ""))
        if path not in _PROSPECTIVE_AUDIT_PATHS:
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
        if not _trusted_host(host, scheme, authorities):
            await _send_json_response(_invalid_request(), scope, receive, send)
            return
        if not _same_origin(origin, host, scheme, authorities):
            await _send_json_response(_invalid_request(), scope, receive, send)
            return
        if (
            _decode_header(headers, b"x-second-brain-request")
            != PROSPECTIVE_AUDIT_REQUEST_HEADER_VALUE
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
        cap = MAX_RAW_PROSPECTIVE_AUDIT_BODY_BYTES
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


async def _read_payload(request: Request, payload_type: type[BaseModel]) -> BaseModel:
    raw = await request.json()
    return payload_type.model_validate(raw, strict=True)


def install_prospective_audit_routes(
    app: FastAPI,
    *,
    service: ProspectiveAuditWebService | None = None,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> None:
    """Install the explicit Stage 9D routes without changing legacy routes."""

    audit = service or build_production_prospective_audit_service(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    app.add_middleware(ProspectiveAuditRequestBoundaryMiddleware)

    @app.post(PROSPECTIVE_AUDIT_EXECUTE_PATH, include_in_schema=False)
    async def prospective_audit_execute_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                ProspectiveAuditExecutePayload,
                await _read_payload(request, ProspectiveAuditExecutePayload),
            )
            typed_request = simulate_me_request(
                SimulateMeRequestPayload(
                    query=payload.query,
                    options=payload.options,
                )
            )
            result = await run_in_threadpool(audit.execute, typed_request, payload.operation_id)
            return JSONResponse(content=result, headers=_API_HEADERS)
        except (
            ValidationError,
            SimulateMeError,
            json.JSONDecodeError,
            UnicodeError,
            ValueError,
            TypeError,
        ):
            return _invalid_request()
        except ProspectiveAuditError as error:
            return _audit_error(error)
        except Exception:
            status, message = _AUDIT_MESSAGES["PROSPECTIVE_AUDIT_STORE_UNAVAILABLE"]
            return _safe_error("PROSPECTIVE_AUDIT_STORE_UNAVAILABLE", message, status)

    @app.post(PROSPECTIVE_AUDIT_PENDING_PATH, include_in_schema=False)
    async def prospective_audit_pending_endpoint(request: Request) -> Response:
        try:
            await _read_payload(request, ProspectiveAuditPendingPayload)
            result = await run_in_threadpool(audit.pending)
            return JSONResponse(content=result, headers=_API_HEADERS)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except ProspectiveAuditError as error:
            return _audit_error(error)
        except Exception:
            status, message = _AUDIT_MESSAGES["PROSPECTIVE_AUDIT_STORE_UNAVAILABLE"]
            return _safe_error("PROSPECTIVE_AUDIT_STORE_UNAVAILABLE", message, status)

    @app.post(PROSPECTIVE_AUDIT_REVIEW_PATH, include_in_schema=False)
    async def prospective_audit_review_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                ProspectiveAuditLinkReviewPayload,
                await _read_payload(request, ProspectiveAuditLinkReviewPayload),
            )
            result = await run_in_threadpool(
                audit.review,
                payload.audit_event_id,
                payload.decision_id,
            )
            return JSONResponse(content=result, headers=_API_HEADERS)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except ProspectiveAuditError as error:
            return _audit_error(error)
        except Exception:
            status, message = _AUDIT_MESSAGES["PROSPECTIVE_AUDIT_LINK_UNAVAILABLE"]
            return _safe_error("PROSPECTIVE_AUDIT_LINK_UNAVAILABLE", message, status)

    @app.post(PROSPECTIVE_AUDIT_CONFIRM_PATH, include_in_schema=False)
    async def prospective_audit_confirm_endpoint(request: Request) -> Response:
        try:
            payload = cast(
                ProspectiveAuditLinkConfirmPayload,
                await _read_payload(request, ProspectiveAuditLinkConfirmPayload),
            )
            mapping = tuple(
                ProspectiveOptionMappingV1(
                    audit_option_id=item.audit_option_id,
                    decision_option_index=item.decision_option_index,
                    decision_option_fingerprint=item.decision_option_fingerprint,
                )
                for item in payload.mapping
            )
            result = await run_in_threadpool(
                audit.confirm,
                payload.audit_event_id,
                payload.decision_id,
                payload.operation_id,
                mapping,
                payload.confirmed,
            )
            return JSONResponse(content=result, headers=_API_HEADERS)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except ProspectiveAuditError as error:
            return _audit_error(error)
        except Exception:
            status, message = _AUDIT_MESSAGES["PROSPECTIVE_AUDIT_STORE_UNAVAILABLE"]
            return _safe_error("PROSPECTIVE_AUDIT_STORE_UNAVAILABLE", message, status)

    @app.post(PROSPECTIVE_AUDIT_CALIBRATION_PATH, include_in_schema=False)
    async def prospective_audit_calibration_endpoint(request: Request) -> Response:
        try:
            await _read_payload(request, ProspectiveAuditCalibrationPayload)
            body = await run_in_threadpool(audit.calibration)
        except ValidationError, json.JSONDecodeError, UnicodeError, ValueError, TypeError:
            return _invalid_request()
        except ProspectiveCalibrationError as error:
            return _calibration_error(error)
        except ProspectiveAuditError as error:
            return _audit_error(error)
        except Exception:
            status, message = _CALIBRATION_MESSAGES[
                ProspectiveCalibrationErrorCodeV1.UNAVAILABLE.value
            ]
            return _safe_error(ProspectiveCalibrationErrorCodeV1.UNAVAILABLE.value, message, status)
        return Response(content=body, media_type="application/json", headers=_API_HEADERS)


__all__ = [
    "MAX_DECISION_TARGETS",
    "MAX_PENDING_EVENTS",
    "MAX_RAW_PROSPECTIVE_AUDIT_BODY_BYTES",
    "PRODUCTION_PROSPECTIVE_AUDIT_OWNER_GROUP",
    "PRODUCTION_PROSPECTIVE_AUDIT_STORE_ROOT",
    "PROSPECTIVE_AUDIT_CALIBRATION_PATH",
    "PROSPECTIVE_AUDIT_CONFIRM_PATH",
    "PROSPECTIVE_AUDIT_EXECUTE_PATH",
    "PROSPECTIVE_AUDIT_PENDING_PATH",
    "PROSPECTIVE_AUDIT_REQUEST_HEADER_NAME",
    "PROSPECTIVE_AUDIT_REQUEST_HEADER_VALUE",
    "PROSPECTIVE_AUDIT_REVIEW_PATH",
    "LazyProductionProspectiveAuditService",
    "ProspectiveAuditCalibrationPayload",
    "ProspectiveAuditExecutePayload",
    "ProspectiveAuditLinkConfirmPayload",
    "ProspectiveAuditLinkReviewPayload",
    "ProspectiveAuditOptionMappingPayload",
    "ProspectiveAuditPendingPayload",
    "ProspectiveAuditRequestBoundaryMiddleware",
    "ProspectiveAuditWebService",
    "build_production_prospective_audit_service",
    "derive_prospective_audit_store_root",
    "install_prospective_audit_routes",
]
