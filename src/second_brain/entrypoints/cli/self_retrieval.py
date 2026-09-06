"""Bounded read-only CLI projection of the Stage 5 Self Retrieval core."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from second_brain.adapters.search import SqliteFts5SearchIndex
from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.self_retrieval import (
    BuildSelfContext,
    SelfContextClaim,
    SelfContextExclusion,
    SelfContextItem,
    SelfContextRequest,
    SelfContextResult,
    SelfRetrievalError,
    SelfRetrievalErrorCode,
    validate_self_context_request,
    validate_self_context_result,
)
from second_brain.config import load_config


@dataclass(frozen=True, slots=True)
class LazyVaultSelfRetrievalService:
    """Resolve local configuration only for an explicit retrieval request."""

    env_file: Path | None = None
    vault_path_override: str | None = None

    def build(self, request: SelfContextRequest) -> SelfContextResult:
        """Build one disposable current context without persistence or network."""

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


def self_retrieval_as_dict(
    result: SelfContextResult,
    request: SelfContextRequest,
) -> dict[str, object]:
    """Project the exact core DTO without paths, snippets, or private fields."""

    validated = validate_self_context_result(result, request=request)
    return {
        "items": [_item_as_dict(item) for item in validated.items],
        "candidate_count": validated.candidate_count,
        "included_count": validated.included_count,
        "excluded_count": validated.excluded_count,
        "exclusions": [_exclusion_as_dict(exclusion) for exclusion in validated.exclusions],
        "truncated": validated.truncated,
        "content_bytes": validated.content_bytes,
        "self_model_derivation_version": validated.self_model_derivation_version,
        "self_model_policy_fingerprint": validated.self_model_policy_fingerprint,
    }


def render_self_retrieval_text(
    result: SelfContextResult,
    request: SelfContextRequest,
) -> str:
    """Render the validated result in stable server order."""

    validated = validate_self_context_result(result, request=request)
    lines = [
        f"Candidates: {validated.candidate_count}",
        f"Included: {validated.included_count}",
        f"Excluded: {validated.excluded_count}",
        f"Truncated: {'yes' if validated.truncated else 'no'}",
        f"Content bytes: {validated.content_bytes}",
        f"Self Model derivation: {validated.self_model_derivation_version}",
        f"Self Model policy fingerprint: {validated.self_model_policy_fingerprint}",
    ]
    for item in validated.items:
        lines.extend(
            [
                "",
                f"[{item.search_rank}] {item.title}",
                f"ID: {item.note_id}",
                f"Type: {item.note_type.value}",
                f"Created: {item.created.isoformat()}",
                f"Updated: {item.updated.isoformat() if item.updated is not None else '-'}",
                f"Tags: {', '.join(item.tags) or '-'}",
                "Body:",
                item.body or "-",
                "Self Model claims:",
            ]
        )
        if item.self_model_claims:
            for claim in item.self_model_claims:
                lines.extend(
                    [
                        f"- {claim.dimension.value}: {claim.claim}",
                        "  Supporting note IDs: "
                        + ", ".join(str(note_id) for note_id in claim.supporting_note_ids),
                        f"  Derivation version: {claim.derivation_version}",
                        f"  Policy fingerprint: {claim.policy_fingerprint}",
                    ]
                )
        else:
            lines.append("- none")

    if validated.exclusions:
        lines.append("")
        lines.append("Exclusions:")
        for exclusion in validated.exclusions:
            lines.append(f"- rank {exclusion.search_rank}: {exclusion.reason.value}")
    return "\n".join(lines)


_ERROR_MESSAGES: dict[str, str] = {
    SelfRetrievalErrorCode.INVALID_REQUEST.value: "запрос не прошёл проверку",
    SelfRetrievalErrorCode.SEARCH_UNAVAILABLE.value: "локальный search backend недоступен",
    SelfRetrievalErrorCode.CURRENT_READ_UNAVAILABLE.value: "current UUID reread недоступен",
    SelfRetrievalErrorCode.SELF_MODEL_UNAVAILABLE.value: "текущий Self Model недоступен",
    SelfRetrievalErrorCode.RESULT_INVALID.value: "результат self retrieval не прошёл проверку",
    SelfRetrievalErrorCode.RESULT_TOO_LARGE.value: (
        "результат self retrieval превысил bounded limit"
    ),
}


def self_retrieval_error_message(error: SelfRetrievalError) -> tuple[str, str]:
    """Return a closed safe code/message pair without exception details."""

    code = (
        error.code if error.code in _ERROR_MESSAGES else SelfRetrievalErrorCode.RESULT_INVALID.value
    )
    return code, _ERROR_MESSAGES[code]


def _item_as_dict(item: SelfContextItem) -> dict[str, object]:
    return {
        "note_id": str(item.note_id),
        "note_type": item.note_type.value,
        "title": item.title,
        "body": item.body,
        "tags": list(item.tags),
        "created": item.created.isoformat(),
        "updated": item.updated.isoformat() if item.updated is not None else None,
        "search_rank": item.search_rank,
        "self_model_claims": [_claim_as_dict(claim) for claim in item.self_model_claims],
    }


def _claim_as_dict(claim: SelfContextClaim) -> dict[str, object]:
    return {
        "dimension": claim.dimension.value,
        "claim": claim.claim,
        "supporting_note_ids": [str(note_id) for note_id in claim.supporting_note_ids],
        "derivation_version": claim.derivation_version,
        "policy_fingerprint": claim.policy_fingerprint,
    }


def _exclusion_as_dict(exclusion: SelfContextExclusion) -> dict[str, object]:
    return {
        "search_rank": exclusion.search_rank,
        "reason": exclusion.reason.value,
    }


__all__ = [
    "LazyVaultSelfRetrievalService",
    "build_production_self_retrieval_service",
    "render_self_retrieval_text",
    "self_retrieval_as_dict",
    "self_retrieval_error_message",
]
