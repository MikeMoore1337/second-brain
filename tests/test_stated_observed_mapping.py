"""Focused Stage 10C runtime, store-integrity and privacy tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid7

import pytest

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.behavioral_self_model import BuildBehavioralSelfModel
from second_brain.application.decision_journal import render_decision_journal_body
from second_brain.application.personal_memory import PERSONAL_MEMORY_MARKER
from second_brain.application.stated_observed_mapping import (
    CONTRACT_VERSION,
    MAPPING_BASIS,
    MAPPING_POLICY_FINGERPRINT,
    MAPPING_POLICY_ID,
    MappingLifecycleStateV1,
    StatedObservedCompositionStateV1,
    StatedObservedMappingAcceptanceRequest,
    StatedObservedMappingError,
    StatedObservedMappingErrorCode,
    StatedObservedMappingSelectorV1,
    StatedObservedMappingService,
    StatedObservedMappingStore,
    StatedObservedMappingStoreCorruptError,
    compute_mapping_fingerprint,
    derive_stated_observed_mapping_store_root,
    validate_mapping_policy,
)
from tests.conftest import create_vault, write_note

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
PREFERENCE_ID = UUID("0198f4c5-6a00-7000-8000-000000000013")
DECISION_IDS = tuple(UUID(f"0198f4c5-6a00-7000-8000-00000000000{index}") for index in range(1, 4))


def _preference_note(*, body: str = "Люблю короткие циклы.") -> str:
    return (
        "---\n"
        f"id: {PREFERENCE_ID}\n"
        "type: zettel\n"
        "created: 2026-09-13T11:30:00+00:00\n"
        f"{PERSONAL_MEMORY_MARKER}: 1\n"
        "evidence_kind: user_statement\n"
        "self_kind: preference\n"
        "evidence_at: 2026-09-12T10:00:00+00:00\n"
        "evidence_at_precision: exact\n"
        "domain: work\n"
        "tags: [preference]\n"
        "links: []\n"
        "---\n"
        f"{body}\n"
    )


def _decision_note(identifier: UUID, *, chosen: str = "Alpha", offset: int = 1) -> str:
    body = render_decision_journal_body(
        situation="Choose a direction",
        available_options=("Alpha", "Beta"),
        information_known_at_decision_time="Known constraints",
        criteria=("Speed",),
        chosen_option=chosen,
        reasons="The reviewed context supports the selected option.",
        confidence="Medium.",
        expected_result="A bounded result.",
    )
    evidence_at = (NOW - timedelta(days=offset)).isoformat()
    return (
        "---\n"
        f"id: {identifier}\n"
        "type: zettel\n"
        "created: 2026-09-13T11:30:00+00:00\n"
        f"{PERSONAL_MEMORY_MARKER}: 1\n"
        "evidence_kind: observed_decision\n"
        "self_kind: decision\n"
        f"evidence_at: {evidence_at}\n"
        "evidence_at_precision: exact\n"
        "domain: work\n"
        "tags: [behavioral]\n"
        "links: []\n"
        "---\n"
        f"{body}\n"
    )


def _seed_vault(root: Path) -> Path:
    vault = create_vault(root)
    write_note(vault, "10 Projects/Preference.md", _preference_note())
    for index, identifier in enumerate(DECISION_IDS, start=1):
        write_note(
            vault,
            f"10 Projects/Decision-{index}.md",
            _decision_note(identifier, offset=index),
        )
    return vault


def _selector(vault: Path) -> StatedObservedMappingSelectorV1:
    reader = FileSystemVaultReader(vault)
    behavioral = BuildBehavioralSelfModel(reader, clock=lambda: NOW).execute()
    assert len(behavioral.patterns) == 1
    pattern = behavioral.patterns[0]
    assert pattern.cohort is not None
    assert pattern.selected_option is not None
    return StatedObservedMappingSelectorV1(
        source_note_uuid=PREFERENCE_ID,
        behavioral_cohort_fingerprint=pattern.cohort.cohort_fingerprint,
        behavioral_option_index=pattern.selected_option.option_index,
        behavioral_option_fingerprint=pattern.selected_option.option_fingerprint,
    )


def _service(vault: Path, store_root: Path) -> StatedObservedMappingService:
    return StatedObservedMappingService(
        FileSystemVaultReader(vault),
        StatedObservedMappingStore(store_root, clock=lambda: NOW),
        clock=lambda: NOW,
    )


def test_policy_binding_is_exact_and_empty_store_read_is_lazy(tmp_path: Path) -> None:
    assert validate_mapping_policy() == MAPPING_POLICY_FINGERPRINT
    store_root = tmp_path / "mapping-store"
    store = StatedObservedMappingStore(store_root, clock=lambda: NOW)

    assert not store_root.exists()
    assert store.read_active() == ()
    assert not store_root.exists()


def test_store_root_is_derived_only_from_explicit_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "runtime" / "web.env"
    env_file.parent.mkdir()
    env_file.write_text("SECOND_BRAIN_VAULT=/srv/second-brain/vault\n", encoding="utf-8")

    assert (
        derive_stated_observed_mapping_store_root(env_file)
        == env_file.parent / "stated-observed-mapping"
    )
    assert not (env_file.parent / "stated-observed-mapping").exists()


def test_review_accepts_only_fresh_server_identities_and_persists_no_raw_text(
    tmp_path: Path,
) -> None:
    vault = _seed_vault(tmp_path / "vault")
    store_root = tmp_path / "mapping-store"
    service = _service(vault, store_root)
    selector = _selector(vault)

    projection = service.review(selector)
    assert projection.claim_text == "Люблю короткие циклы.\n"
    assert projection.ordered_options[0].label == "Alpha"
    accepted = service.accept(
        StatedObservedMappingAcceptanceRequest(
            selector=selector,
            operation_id=uuid7(),
            confirmed=True,
            review_projection=projection,
        )
    )

    serialized = store_root.joinpath("mappings.jsonl").read_text(encoding="utf-8")
    assert "Люблю короткие циклы" not in serialized
    assert "Alpha" not in serialized
    assert "Choose a direction" not in serialized
    assert accepted.contract_version == CONTRACT_VERSION
    assert accepted.mapping_policy_id == MAPPING_POLICY_ID
    assert accepted.mapping_basis == MAPPING_BASIS
    assert accepted.mapping_fingerprint == compute_mapping_fingerprint(
        accepted.stated, accepted.behavioral
    )

    result = service.compose(PREFERENCE_ID)
    assert result.state is StatedObservedCompositionStateV1.ALIGNED
    assert result.mapping_id == accepted.mapping_id
    assert result.reason_code is None


def test_accept_requires_explicit_confirmation_and_retry_is_idempotent(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    service = _service(vault, tmp_path / "mapping-store")
    selector = _selector(vault)
    projection = service.review(selector)
    operation_id = uuid7()

    with pytest.raises(Exception) as error:
        service.accept(
            StatedObservedMappingAcceptanceRequest(
                selector=selector,
                operation_id=operation_id,
                confirmed=False,
                review_projection=projection,
            )
        )
    assert (
        getattr(error.value, "code", None) == StatedObservedMappingErrorCode.INVALID_REQUEST.value
    )

    request = StatedObservedMappingAcceptanceRequest(
        selector=selector,
        operation_id=operation_id,
        confirmed=True,
        review_projection=projection,
    )
    first = service.accept(request)
    second = service.accept(request)
    assert second.mapping_id == first.mapping_id
    assert len(service.store.read_active()) == 1

    with pytest.raises(StatedObservedMappingError) as conflict:
        service.accept(
            StatedObservedMappingAcceptanceRequest(
                selector=selector,
                operation_id=uuid7(),
                confirmed=True,
                review_projection=projection,
            )
        )
    assert conflict.value.code == StatedObservedMappingErrorCode.CONCURRENCY_CONFLICT.value


def test_lifecycle_is_append_only_and_delete_can_follow_invalidation(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    service = _service(vault, tmp_path / "mapping-store")
    selector = _selector(vault)
    projection = service.review(selector)
    accepted = service.accept(
        StatedObservedMappingAcceptanceRequest(
            selector=selector,
            operation_id=uuid7(),
            confirmed=True,
            review_projection=projection,
        )
    )

    invalidated = service.store.invalidate_mapping(accepted.mapping_id, operation_id=uuid7())
    assert invalidated.lifecycle_state is MappingLifecycleStateV1.INVALIDATED
    assert invalidated.record == accepted
    assert service.store.read_active() == ()
    deleted = service.store.delete_mapping(accepted.mapping_id, operation_id=uuid7())
    assert deleted.lifecycle_state is MappingLifecycleStateV1.DELETED
    assert deleted.record == accepted
    assert service.store.get(accepted.mapping_id).record == accepted  # type: ignore[union-attr]


def test_source_edit_is_stale_not_divergent_and_missing_source_is_distinct(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    service = _service(vault, tmp_path / "mapping-store")
    selector = _selector(vault)
    projection = service.review(selector)
    service.accept(
        StatedObservedMappingAcceptanceRequest(
            selector=selector,
            operation_id=uuid7(),
            confirmed=True,
            review_projection=projection,
        )
    )

    write_note(vault, "10 Projects/Preference.md", _preference_note(body="Теперь другой текст."))
    stale = service.compose(PREFERENCE_ID)
    assert stale.state is StatedObservedCompositionStateV1.NOT_COMPARABLE
    assert stale.reason_code == StatedObservedMappingErrorCode.STATED_SOURCE_CHANGED.value

    (vault / "10 Projects/Preference.md").unlink()
    missing = service.compose(PREFERENCE_ID)
    assert missing.state is StatedObservedCompositionStateV1.STATED_EVIDENCE_MISSING
    assert missing.reason_code == StatedObservedMappingErrorCode.STATED_EVIDENCE_MISSING.value


def test_partial_append_fails_closed_without_salvage(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    service = _service(vault, tmp_path / "mapping-store")
    selector = _selector(vault)
    projection = service.review(selector)
    service.accept(
        StatedObservedMappingAcceptanceRequest(
            selector=selector,
            operation_id=uuid7(),
            confirmed=True,
            review_projection=projection,
        )
    )
    with service.store.records_path.open("ab") as stream:
        stream.write(b'{"partial":')

    with pytest.raises(StatedObservedMappingStoreCorruptError):
        service.store.read_active()
