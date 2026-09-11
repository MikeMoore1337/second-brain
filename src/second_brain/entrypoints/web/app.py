"""Current Web application plus the bounded Stage 7 Assistant/Compare projection."""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI

from second_brain.entrypoints.web import legacy_app as _legacy_app
from second_brain.entrypoints.web.assistant_compare import (
    ASSISTANT_REQUEST_HEADER_NAME,
    ASSISTANT_REQUEST_HEADER_VALUE,
    COMPARE_REQUEST_HEADER_NAME,
    COMPARE_REQUEST_HEADER_VALUE,
    MAX_RAW_ASSISTANT_BODY_BYTES,
    MAX_RAW_COMPARE_BODY_BYTES,
    AssistantWebService,
    CompareWebService,
    install_assistant_compare_routes,
)
from second_brain.entrypoints.web.auth import (
    WebAuthConfig,
    install_web_auth,
    load_web_auth_config,
)
from second_brain.entrypoints.web.legacy_app import (
    DIAGNOSTICS_REQUEST_HEADER_NAME,
    DIAGNOSTICS_REQUEST_HEADER_VALUE,
    MAX_RAW_DIAGNOSTICS_BODY_BYTES,
)
from second_brain.entrypoints.web.legacy_app import (
    __all__ as _legacy_all,
)
from second_brain.entrypoints.web.legacy_app import (
    create_app as _legacy_create_app,
)
from second_brain.entrypoints.web.retrospective_calibration import (
    MAX_RAW_RETROSPECTIVE_CALIBRATION_BODY_BYTES,
    RETROSPECTIVE_CALIBRATION_REQUEST_HEADER_NAME,
    RETROSPECTIVE_CALIBRATION_REQUEST_HEADER_VALUE,
    RetrospectiveCalibrationWebService,
    install_retrospective_calibration_routes,
)

# Preserve the historical module surface used by direct imports and monkeypatch-based
# tests. Star imports intentionally omit private names, so expose the known seams
# explicitly and delegate any other legacy attributes through __getattr__.
_ERRORS = _legacy_app._ERRORS
_GENERIC_ERROR = _legacy_app._GENERIC_ERROR
REACT_INDEX_FILE = _legacy_app.REACT_INDEX_FILE
REACT_ASSETS_DIR = _legacy_app.REACT_ASSETS_DIR
REACT_PWA_MANIFEST_FILE = _legacy_app.REACT_PWA_MANIFEST_FILE
REACT_PWA_SERVICE_WORKER_FILE = _legacy_app.REACT_PWA_SERVICE_WORKER_FILE
REACT_PWA_OFFLINE_FILE = _legacy_app.REACT_PWA_OFFLINE_FILE
REACT_PWA_OFFLINE_STYLES_FILE = _legacy_app.REACT_PWA_OFFLINE_STYLES_FILE
REACT_PWA_ICONS_DIR = _legacy_app.REACT_PWA_ICONS_DIR
run_in_threadpool = _legacy_app.run_in_threadpool  # type: ignore[attr-defined]

for _legacy_name in _legacy_all:
    if _legacy_name != "create_app":
        globals()[_legacy_name] = getattr(_legacy_app, _legacy_name)

_LEGACY_PATCHABLE_NAMES = frozenset(
    {
        *(_legacy_all),
        "_ERRORS",
        "_GENERIC_ERROR",
        "REACT_INDEX_FILE",
        "REACT_ASSETS_DIR",
        "REACT_PWA_MANIFEST_FILE",
        "REACT_PWA_SERVICE_WORKER_FILE",
        "REACT_PWA_OFFLINE_FILE",
        "REACT_PWA_OFFLINE_STYLES_FILE",
        "REACT_PWA_ICONS_DIR",
        "run_in_threadpool",
    }
) - {"create_app"}


def __getattr__(name: str) -> Any:
    """Delegate historical module attributes to the original Web module."""

    return getattr(_legacy_app, name)


def __dir__() -> list[str]:
    """Expose both wrapper and historical module names to introspection."""

    return sorted(set(globals()) | set(dir(_legacy_app)))


def _sync_legacy_patchable_seams() -> None:
    """Propagate monkeypatched wrapper seams before constructing the legacy app."""

    namespace = globals()
    for name in _LEGACY_PATCHABLE_NAMES:
        if name in namespace and hasattr(_legacy_app, name):
            setattr(_legacy_app, name, namespace[name])


def create_app(**kwargs: Any) -> FastAPI:
    """Build the current app and add the explicit-input Stage 7 routes."""

    assistant_service = kwargs.pop("assistant_web_service", None)
    compare_service = kwargs.pop("compare_web_service", None)
    retrospective_calibration_service = kwargs.pop("retrospective_calibration_service", None)
    auth_config = kwargs.pop("web_auth_config", None)
    auth_gateway = kwargs.pop("web_auth_gateway", None)
    auth_clock = kwargs.pop("web_auth_clock", None)
    simulate_me_service = kwargs.get("simulate_me_service")
    env_file = kwargs.get("env_file")
    vault_path_override = kwargs.get("vault_path_override")
    if auth_config is None:
        auth_config = load_web_auth_config(env_file=env_file)
    _sync_legacy_patchable_seams()
    app = _legacy_create_app(**kwargs)
    install_assistant_compare_routes(
        app,
        assistant_service=assistant_service,
        compare_service=compare_service,
        simulate_me_service=simulate_me_service,
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    install_retrospective_calibration_routes(
        app,
        service=retrospective_calibration_service,
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    install_web_auth(
        app,
        config=auth_config,
        index_file=REACT_INDEX_FILE,
        gateway=auth_gateway,
        clock=auth_clock or time.time,
    )
    return app


__all__ = [
    *_legacy_all,
    "_ERRORS",
    "_GENERIC_ERROR",
    "REACT_INDEX_FILE",
    "REACT_ASSETS_DIR",
    "REACT_PWA_MANIFEST_FILE",
    "REACT_PWA_SERVICE_WORKER_FILE",
    "REACT_PWA_OFFLINE_FILE",
    "REACT_PWA_OFFLINE_STYLES_FILE",
    "REACT_PWA_ICONS_DIR",
    "run_in_threadpool",
    "DIAGNOSTICS_REQUEST_HEADER_NAME",
    "DIAGNOSTICS_REQUEST_HEADER_VALUE",
    "MAX_RAW_DIAGNOSTICS_BODY_BYTES",
    "ASSISTANT_REQUEST_HEADER_NAME",
    "ASSISTANT_REQUEST_HEADER_VALUE",
    "COMPARE_REQUEST_HEADER_NAME",
    "COMPARE_REQUEST_HEADER_VALUE",
    "MAX_RAW_ASSISTANT_BODY_BYTES",
    "MAX_RAW_COMPARE_BODY_BYTES",
    "AssistantWebService",
    "CompareWebService",
    "MAX_RAW_RETROSPECTIVE_CALIBRATION_BODY_BYTES",
    "RETROSPECTIVE_CALIBRATION_REQUEST_HEADER_NAME",
    "RETROSPECTIVE_CALIBRATION_REQUEST_HEADER_VALUE",
    "RetrospectiveCalibrationWebService",
    "WebAuthConfig",
    "create_app",
]
