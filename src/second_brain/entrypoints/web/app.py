"""Current Web application plus the bounded Stage 7 Assistant/Compare projection."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

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
from second_brain.entrypoints.web.legacy_app import *  # noqa: F403
from second_brain.entrypoints.web.legacy_app import (
    DIAGNOSTICS_REQUEST_HEADER_NAME,
    DIAGNOSTICS_REQUEST_HEADER_VALUE,
    MAX_RAW_DIAGNOSTICS_BODY_BYTES,
    __all__ as _legacy_all,
    create_app as _legacy_create_app,
)


def create_app(**kwargs: Any) -> FastAPI:
    """Build the current app and add the explicit-input Stage 7 routes."""

    assistant_service = kwargs.pop("assistant_web_service", None)
    compare_service = kwargs.pop("compare_web_service", None)
    simulate_me_service = kwargs.get("simulate_me_service")
    env_file = kwargs.get("env_file")
    vault_path_override = kwargs.get("vault_path_override")
    app = _legacy_create_app(**kwargs)
    install_assistant_compare_routes(
        app,
        assistant_service=assistant_service,
        compare_service=compare_service,
        simulate_me_service=simulate_me_service,
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    return app


__all__ = [
    *_legacy_all,
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
    "create_app",
]
