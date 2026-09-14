"""Current Web application plus the bounded Stage 7 Assistant/Compare projection."""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI

from second_brain.entrypoints.web import legacy_app as _legacy_app
from second_brain.entrypoints.web.active_personal_learning import (
    ACTIVE_LEARNING_QUESTIONS_PATH,
    ACTIVE_LEARNING_REQUEST_HEADER_NAME,
    ACTIVE_LEARNING_REQUEST_HEADER_VALUE,
    ACTIVE_LEARNING_RESOLVE_PATH,
    MAX_RAW_ACTIVE_LEARNING_BODY_BYTES,
    ActiveLearningAnswerCapturePayload,
    ActiveLearningCandidatePayload,
    ActiveLearningOptionPayload,
    ActiveLearningQuestionsRequestPayload,
    ActiveLearningRequestBoundaryMiddleware,
    ActiveLearningResolutionPayload,
    ActiveLearningResolutionResponsePayload,
    ActiveLearningResolutionService,
    ActiveLearningResolveRequestPayload,
    ActiveLearningWebService,
    LazyVaultActiveLearningService,
    install_active_learning_routes,
)
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
from second_brain.entrypoints.web.cognitive_twin import (
    BEHAVIORAL_SELF_MODEL_PATH,
    COGNITIVE_TWIN_REQUEST_HEADER_NAME,
    COGNITIVE_TWIN_REQUEST_HEADER_VALUE,
    MAX_RAW_COGNITIVE_TWIN_BODY_BYTES,
    STATED_OBSERVED_COMPOSITION_PATH,
    STATED_OBSERVED_MAPPING_CONFIRM_PATH,
    STATED_OBSERVED_MAPPING_REVIEW_PATH,
    STATED_OBSERVED_MAPPING_STATUS_PATH,
    BehavioralSelfModelPayload,
    BehavioralSelfModelService,
    CognitiveTwinRequestBoundaryMiddleware,
    LazyVaultBehavioralSelfModelService,
    LazyVaultStatedObservedMappingService,
    StatedObservedCompositionPayload,
    StatedObservedMappingConfirmPayload,
    StatedObservedMappingReviewPayload,
    StatedObservedMappingSelectorPayload,
    StatedObservedMappingStatusPayload,
    StatedObservedMappingWebService,
    build_production_behavioral_self_model_service,
    build_production_stated_observed_mapping_service,
    install_cognitive_twin_routes,
)
from second_brain.entrypoints.web.growth import (
    GROWTH_ENGINE_PATH,
    GROWTH_GOALS_PATH,
    GROWTH_MAPPING_CONFIRM_PATH,
    GROWTH_MAPPING_DELETE_PATH,
    GROWTH_MAPPING_INVALIDATE_PATH,
    GROWTH_MAPPING_REVIEW_PATH,
    GROWTH_MAPPING_STATUS_PATH,
    GROWTH_PATH,
    GROWTH_REQUEST_HEADER_NAME,
    GROWTH_REQUEST_HEADER_VALUE,
    MAX_RAW_GROWTH_BODY_BYTES,
    GrowthEmptyPayload,
    GrowthEnginePayload,
    GrowthGoalOwnerItemV1,
    GrowthGoalsProjectionV1,
    GrowthMappingConfirmPayload,
    GrowthMappingLifecyclePayload,
    GrowthMappingReviewPayload,
    GrowthMappingSelectorPayload,
    GrowthRequestBoundaryMiddleware,
    GrowthWebService,
    ProductionGrowthWebService,
    build_production_growth_web_service,
    install_growth_routes,
)
from second_brain.entrypoints.web.growth_advisor import (
    GROWTH_ADVISOR_EXECUTE_PATH,
    GROWTH_ADVISOR_PREVIEW_PATH,
    GROWTH_ADVISOR_REQUEST_HEADER_NAME,
    GROWTH_ADVISOR_REQUEST_HEADER_VALUE,
    MAX_GROWTH_ADVISOR_RESPONSE_BYTES,
    MAX_RAW_GROWTH_ADVISOR_BODY_BYTES,
    GrowthAdvisorExecutePayloadV1,
    GrowthAdvisorRequestBoundaryMiddleware,
    GrowthAdvisorWebService,
    ProductionGrowthAdvisorWebService,
    build_production_growth_advisor_service,
    install_growth_advisor_routes,
)
from second_brain.entrypoints.web.growth_learning import (
    GROWTH_LEARNING_QUESTIONS_PATH,
    GROWTH_LEARNING_REQUEST_HEADER_NAME,
    GROWTH_LEARNING_REQUEST_HEADER_VALUE,
    GROWTH_LEARNING_RESOLVE_PATH,
    MAX_GROWTH_LEARNING_RESPONSE_BYTES,
    MAX_RAW_GROWTH_LEARNING_BODY_BYTES,
    GrowthLearningCandidatePayload,
    GrowthLearningQuestionsPayload,
    GrowthLearningRequestBoundaryMiddleware,
    GrowthLearningResolutionPayload,
    GrowthLearningResolveRequestPayload,
    GrowthLearningWebService,
    ProductionGrowthLearningWebService,
    build_production_growth_learning_service,
    install_growth_learning_routes,
)
from second_brain.entrypoints.web.legacy_app import (
    ACTIVE_LEARNING_ANSWER_REVIEW_PATH,
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
from second_brain.entrypoints.web.prospective_audit import (
    MAX_RAW_PROSPECTIVE_AUDIT_BODY_BYTES,
    PROSPECTIVE_AUDIT_REQUEST_HEADER_NAME,
    PROSPECTIVE_AUDIT_REQUEST_HEADER_VALUE,
    ProspectiveAuditWebService,
    install_prospective_audit_routes,
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
    """Build the current app and add the explicit-input Stage 7/8/9/10 routes."""

    active_learning_service = kwargs.pop("active_learning_service", None)
    assistant_service = kwargs.pop("assistant_web_service", None)
    compare_service = kwargs.pop("compare_web_service", None)
    retrospective_calibration_service = kwargs.pop("retrospective_calibration_service", None)
    prospective_audit_service = kwargs.pop("prospective_audit_service", None)
    behavioral_self_model_service = kwargs.pop("behavioral_self_model_service", None)
    stated_observed_mapping_service = kwargs.pop("stated_observed_mapping_service", None)
    growth_advisor_service = kwargs.pop("growth_advisor_service", None)
    growth_service = kwargs.pop("growth_web_service", None)
    growth_learning_service = kwargs.pop("growth_learning_web_service", None)
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
    install_prospective_audit_routes(
        app,
        service=prospective_audit_service,
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    install_active_learning_routes(
        app,
        service=active_learning_service,
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    install_cognitive_twin_routes(
        app,
        behavioral_self_model_service=behavioral_self_model_service,
        stated_observed_mapping_service=stated_observed_mapping_service,
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    install_growth_advisor_routes(
        app,
        service=growth_advisor_service,
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    install_growth_routes(
        app,
        service=growth_service,
        env_file=env_file,
        vault_path_override=vault_path_override,
    )
    install_growth_learning_routes(
        app,
        service=growth_learning_service,
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
    "ACTIVE_LEARNING_QUESTIONS_PATH",
    "ACTIVE_LEARNING_RESOLVE_PATH",
    "ACTIVE_LEARNING_ANSWER_REVIEW_PATH",
    "ACTIVE_LEARNING_REQUEST_HEADER_NAME",
    "ACTIVE_LEARNING_REQUEST_HEADER_VALUE",
    "MAX_RAW_ACTIVE_LEARNING_BODY_BYTES",
    "ActiveLearningOptionPayload",
    "ActiveLearningQuestionsRequestPayload",
    "ActiveLearningCandidatePayload",
    "ActiveLearningResolutionPayload",
    "ActiveLearningResolveRequestPayload",
    "ActiveLearningAnswerCapturePayload",
    "ActiveLearningResolutionResponsePayload",
    "ActiveLearningRequestBoundaryMiddleware",
    "ActiveLearningWebService",
    "ActiveLearningResolutionService",
    "LazyVaultActiveLearningService",
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
    "MAX_RAW_PROSPECTIVE_AUDIT_BODY_BYTES",
    "PROSPECTIVE_AUDIT_REQUEST_HEADER_NAME",
    "PROSPECTIVE_AUDIT_REQUEST_HEADER_VALUE",
    "ProspectiveAuditWebService",
    "BEHAVIORAL_SELF_MODEL_PATH",
    "COGNITIVE_TWIN_REQUEST_HEADER_NAME",
    "COGNITIVE_TWIN_REQUEST_HEADER_VALUE",
    "CognitiveTwinRequestBoundaryMiddleware",
    "MAX_RAW_COGNITIVE_TWIN_BODY_BYTES",
    "STATED_OBSERVED_COMPOSITION_PATH",
    "STATED_OBSERVED_MAPPING_CONFIRM_PATH",
    "STATED_OBSERVED_MAPPING_REVIEW_PATH",
    "STATED_OBSERVED_MAPPING_STATUS_PATH",
    "BehavioralSelfModelPayload",
    "BehavioralSelfModelService",
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
    "GROWTH_ENGINE_PATH",
    "GROWTH_GOALS_PATH",
    "GROWTH_MAPPING_CONFIRM_PATH",
    "GROWTH_MAPPING_DELETE_PATH",
    "GROWTH_MAPPING_INVALIDATE_PATH",
    "GROWTH_MAPPING_REVIEW_PATH",
    "GROWTH_MAPPING_STATUS_PATH",
    "GROWTH_PATH",
    "GROWTH_REQUEST_HEADER_NAME",
    "GROWTH_REQUEST_HEADER_VALUE",
    "MAX_RAW_GROWTH_BODY_BYTES",
    "GrowthEnginePayload",
    "GrowthEmptyPayload",
    "GrowthGoalOwnerItemV1",
    "GrowthGoalsProjectionV1",
    "GrowthMappingConfirmPayload",
    "GrowthMappingLifecyclePayload",
    "GrowthMappingReviewPayload",
    "GrowthMappingSelectorPayload",
    "GrowthRequestBoundaryMiddleware",
    "GrowthWebService",
    "ProductionGrowthWebService",
    "build_production_growth_web_service",
    "install_growth_routes",
    "GROWTH_LEARNING_QUESTIONS_PATH",
    "GROWTH_LEARNING_REQUEST_HEADER_NAME",
    "GROWTH_LEARNING_REQUEST_HEADER_VALUE",
    "GROWTH_LEARNING_RESOLVE_PATH",
    "MAX_GROWTH_LEARNING_RESPONSE_BYTES",
    "MAX_RAW_GROWTH_LEARNING_BODY_BYTES",
    "GrowthLearningCandidatePayload",
    "GrowthLearningQuestionsPayload",
    "GrowthLearningRequestBoundaryMiddleware",
    "GrowthLearningResolutionPayload",
    "GrowthLearningResolveRequestPayload",
    "GrowthLearningWebService",
    "ProductionGrowthLearningWebService",
    "build_production_growth_learning_service",
    "install_growth_learning_routes",
    "WebAuthConfig",
    "create_app",
]
