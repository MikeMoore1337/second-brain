"""Lazy local Web projection of the application-owned Self Retrieval result."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

from second_brain.adapters.search import SqliteFts5SearchIndex
from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.self_retrieval import (
    DEFAULT_MAX_CONTENT_BYTES,
    DEFAULT_SELF_CONTEXT_LIMIT,
    BuildSelfContext,
    SelfContextClaim,
    SelfContextExclusion,
    SelfContextItem,
    SelfContextRequest,
    SelfContextResult,
    SelfRetrievalError,
    SelfRetrievalResultInvalidError,
    validate_self_context_request,
    validate_self_context_result,
)
from second_brain.config import load_config


class SelfRetrievalService(Protocol):
    """Minimal injectable Web seam for one current Self Retrieval build."""

    def build(self, request: SelfContextRequest) -> SelfContextResult:
        """Build one bounded result through the application core."""


@dataclass(frozen=True, slots=True)
class LazyVaultSelfRetrievalService:
    """Resolve configuration and read the vault only for an explicit request."""

    env_file: Path | None = None
    vault_path_override: str | None = None

    def build(self, request: SelfContextRequest) -> SelfContextResult:
        """Build a disposable current context without persistence or cache."""

        validate_self_context_request(request)
        config = load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )
        reader = FileSystemVaultReader(config.vault_path)
        index = SqliteFts5SearchIndex()
        try:
            return BuildSelfContext(reader, index).execute(request)
        finally:
            index.close()


def build_production_self_retrieval_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> LazyVaultSelfRetrievalService:
    """Create a lazy service without config, vault, or index side effects."""

    return LazyVaultSelfRetrievalService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


class SelfRetrievalRequestPayload(BaseModel):
    """Strict HTTP projection of the existing application request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: StrictStr
    limit: StrictInt = DEFAULT_SELF_CONTEXT_LIMIT
    max_content_bytes: StrictInt = DEFAULT_MAX_CONTENT_BYTES


class SelfContextClaimPayload(BaseModel):
    """Exact claim-link projection without confidence or inferred fields."""

    model_config = ConfigDict(extra="forbid", strict=True)

    dimension: Literal["preference", "belief", "goal"]
    claim: StrictStr
    supporting_note_ids: list[StrictStr]
    derivation_version: StrictStr
    policy_fingerprint: StrictStr


class SelfContextItemPayload(BaseModel):
    """Current note content plus ordinal and exact claim links."""

    model_config = ConfigDict(extra="forbid", strict=True)

    note_id: StrictStr
    note_type: StrictStr
    title: StrictStr
    body: StrictStr
    tags: list[StrictStr]
    created: datetime
    updated: datetime | None
    search_rank: StrictInt
    self_model_claims: list[SelfContextClaimPayload]


class SelfContextExclusionPayload(BaseModel):
    """Bounded exclusion audit metadata without candidate identity leakage."""

    model_config = ConfigDict(extra="forbid", strict=True)

    search_rank: StrictInt
    reason: Literal["context_budget_exceeded", "candidate_not_found"]


class SelfRetrievalResponse(BaseModel):
    """Exact bounded Web projection of the application result."""

    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[SelfContextItemPayload]
    candidate_count: StrictInt
    included_count: StrictInt
    excluded_count: StrictInt
    exclusions: list[SelfContextExclusionPayload]
    truncated: bool
    content_bytes: StrictInt
    self_model_derivation_version: StrictStr
    self_model_policy_fingerprint: StrictStr


def self_retrieval_response(
    result: SelfContextResult,
    request: SelfContextRequest,
) -> SelfRetrievalResponse:
    """Validate and serialize one core result without adding Web semantics."""

    try:
        validate_self_context_request(request)
        validated = validate_self_context_result(result, request=request)
        return SelfRetrievalResponse(
            items=[_item_payload(item) for item in validated.items],
            candidate_count=validated.candidate_count,
            included_count=validated.included_count,
            excluded_count=validated.excluded_count,
            exclusions=[_exclusion_payload(exclusion) for exclusion in validated.exclusions],
            truncated=validated.truncated,
            content_bytes=validated.content_bytes,
            self_model_derivation_version=validated.self_model_derivation_version,
            self_model_policy_fingerprint=validated.self_model_policy_fingerprint,
        )
    except SelfRetrievalError:
        raise
    except Exception:
        raise SelfRetrievalResultInvalidError() from None


def _item_payload(item: SelfContextItem) -> SelfContextItemPayload:
    """Project only fields already approved by the core DTO."""

    return SelfContextItemPayload(
        note_id=str(item.note_id),
        note_type=item.note_type.value,
        title=item.title,
        body=item.body,
        tags=list(item.tags),
        created=item.created,
        updated=item.updated,
        search_rank=item.search_rank,
        self_model_claims=[_claim_payload(claim) for claim in item.self_model_claims],
    )


def _claim_payload(claim: SelfContextClaim) -> SelfContextClaimPayload:
    """Project exact supporting UUID links without deriving new associations."""

    dimension = getattr(claim.dimension, "value", None)
    if dimension not in {"preference", "belief", "goal"}:
        raise SelfRetrievalResultInvalidError()
    return SelfContextClaimPayload(
        dimension=cast(Literal["preference", "belief", "goal"], dimension),
        claim=claim.claim,
        supporting_note_ids=[str(note_id) for note_id in claim.supporting_note_ids],
        derivation_version=claim.derivation_version,
        policy_fingerprint=claim.policy_fingerprint,
    )


def _exclusion_payload(exclusion: SelfContextExclusion) -> SelfContextExclusionPayload:
    """Project only the core's bounded reason and server order."""

    reason = getattr(exclusion.reason, "value", None)
    if reason not in {"context_budget_exceeded", "candidate_not_found"}:
        raise SelfRetrievalResultInvalidError()
    return SelfContextExclusionPayload(
        search_rank=exclusion.search_rank,
        reason=cast(Literal["context_budget_exceeded", "candidate_not_found"], reason),
    )


__all__ = [
    "LazyVaultSelfRetrievalService",
    "SelfContextClaimPayload",
    "SelfContextExclusionPayload",
    "SelfContextItemPayload",
    "SelfRetrievalRequestPayload",
    "SelfRetrievalResponse",
    "SelfRetrievalService",
    "build_production_self_retrieval_service",
    "self_retrieval_response",
]
