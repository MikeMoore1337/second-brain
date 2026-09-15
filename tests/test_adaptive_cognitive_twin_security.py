"""Stage 15.5 adversarial identity, store and privacy gates."""

from __future__ import annotations

import ast
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid7

import pytest

from second_brain.application.adaptive_cognitive_twin import (
    AdaptiveCognitiveTwinInputError,
    canonical_adaptive_json_bytes,
    derive_adaptive_candidate,
)
from second_brain.application.adaptive_cognitive_twin_store import (
    AdaptiveCognitiveTwinStore,
    AdaptiveCognitiveTwinStoreActiveProfileConflictError,
    AdaptiveCognitiveTwinStoreCorruptError,
    AdaptiveCognitiveTwinStoreProfileNotFoundError,
    AdaptiveCognitiveTwinStoreSourceChangedError,
    AdaptiveCognitiveTwinStoreStateConflictError,
    adaptive_store_hash_json,
)
from tests.test_adaptive_cognitive_twin import HASH_D, NOW, _goal, _source_snapshot


def _store(tmp_path: Path, name: str = "adaptive") -> tuple[AdaptiveCognitiveTwinStore, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    vault = tmp_path / "vault"
    vault.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    return AdaptiveCognitiveTwinStore(runtime / name, vault_root=vault), vault


def _activated_store(tmp_path: Path) -> tuple[AdaptiveCognitiveTwinStore, Any, Any, Any]:
    store, _ = _store(tmp_path)
    goal = _goal()
    source = _source_snapshot(goal)
    candidate = derive_adaptive_candidate(source, as_of=NOW)
    store.review_candidate(candidate, operation_id="review-activation")
    activation = store.activate_candidate(candidate, source, operation_id="activate-profile")
    assert activation.record.profile is not None
    return store, goal, source, activation.record.profile


@pytest.mark.parametrize(
    "family",
    ["stage9_calibration", "stage10_behavioral", "stage12_progress", "stage14_experiment"],
)
def test_activation_revalidates_every_exact_source_family(
    tmp_path: Path,
    family: str,
) -> None:
    """A candidate cannot be activated after any bound source reference drifts."""

    store, _ = _store(tmp_path)
    goal = _goal()
    source = _source_snapshot(goal)
    candidate = derive_adaptive_candidate(source, as_of=NOW)
    store.review_candidate(candidate, operation_id=f"review-{family}")

    if family == "stage9_calibration":
        changed_family = replace(
            source.stage9_calibration,
            result_fingerprint=HASH_D,
        )
    elif family == "stage10_behavioral":
        changed_family = replace(
            source.stage10_behavioral,
            cohort_fingerprint=HASH_D,
        )
    elif family == "stage12_progress":
        changed_family = replace(
            source.stage12_progress,
            progress_result_fingerprint=HASH_D,
        )
    else:
        changed_family = replace(
            source.stage14_experiment,
            terminal_result_fingerprint=HASH_D,
        )
    changed_source = replace(
        source,
        **{family: changed_family, "source_snapshot_fingerprint": ""},
    )

    with pytest.raises(AdaptiveCognitiveTwinStoreSourceChangedError):
        store.activate_candidate(candidate, changed_source, operation_id=f"activate-{family}")

    assert store.read_state().active_profiles == ()
    assert len(store.read_events()) == 1


def test_exact_goal_binding_rejects_same_shape_with_different_source_uuid(tmp_path: Path) -> None:
    """Matching labels or shape never retarget a candidate to another Goal UUID."""

    store, _ = _store(tmp_path)
    first_goal = _goal()
    second_goal = replace(first_goal, source_note_uuid=uuid7())
    first_source = _source_snapshot(first_goal)
    second_source = _source_snapshot(second_goal)
    candidate = derive_adaptive_candidate(first_source, as_of=NOW)
    store.review_candidate(candidate, operation_id="review-exact-goal")

    with pytest.raises(AdaptiveCognitiveTwinStoreSourceChangedError):
        store.activate_candidate(candidate, second_source, operation_id="activate-retargeted")

    assert store.read_state().active_profiles == ()


def test_source_pack_rejects_cross_goal_behavioral_reference() -> None:
    goal = _goal()
    source = _source_snapshot(goal)
    wrong_behavioral_goal = replace(source.stage10_behavioral, goal_source_uuid=uuid7())

    with pytest.raises(AdaptiveCognitiveTwinInputError):
        replace(
            source,
            stage10_behavioral=wrong_behavioral_goal,
            source_snapshot_fingerprint="",
        )


def test_cross_goal_revert_cannot_select_another_goals_profile(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    first_goal = _goal()
    second_goal = _goal()
    first_source = _source_snapshot(first_goal)
    second_source = _source_snapshot(second_goal)
    first_candidate = derive_adaptive_candidate(first_source, as_of=NOW)
    second_candidate = derive_adaptive_candidate(second_source, as_of=NOW)
    store.review_candidate(first_candidate, operation_id="review-first")
    store.review_candidate(second_candidate, operation_id="review-second")
    first_activation = store.activate_candidate(
        first_candidate, first_source, operation_id="activate-first"
    )
    second_activation = store.activate_candidate(
        second_candidate, second_source, operation_id="activate-second"
    )
    assert first_activation.record.profile is not None
    assert second_activation.record.profile is not None

    with pytest.raises(AdaptiveCognitiveTwinStoreProfileNotFoundError):
        store.revert_profile(
            goal_source_uuid=first_goal.source_note_uuid,
            goal_identity_fingerprint=first_candidate.goal_identity_fingerprint,
            target_profile_id=second_activation.record.profile.profile_id,
            target_profile_fingerprint=second_activation.record.profile.profile_fingerprint,
            operation_id="revert-cross-goal",
        )

    state = store.read_state()
    assert len(state.active_profiles) == 2
    assert any(
        profile.profile_id == first_activation.record.profile.profile_id
        for profile in state.active_profiles
    )
    assert any(
        profile.profile_id == second_activation.record.profile.profile_id
        for profile in state.active_profiles
    )


def test_concurrent_different_activations_leave_at_most_one_active_profile(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    goal = _goal()
    source = _source_snapshot(goal)
    candidate = derive_adaptive_candidate(source, as_of=NOW)
    store.review_candidate(candidate, operation_id="review-concurrent-different")

    def activate(operation_id: str) -> str:
        try:
            store.activate_candidate(candidate, source, operation_id=operation_id)
        except (
            AdaptiveCognitiveTwinStoreActiveProfileConflictError,
            AdaptiveCognitiveTwinStoreStateConflictError,
        ):
            return "active_conflict"
        return "activated"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(activate, ("activate-a", "activate-b")))

    assert sorted(results) == ["activated", "active_conflict"]
    assert len(store.read_state().active_profiles) == 1
    assert len(store.read_events()) == 2


def test_store_rejects_reordered_and_missing_sequence_history(tmp_path: Path) -> None:
    store, _, _, _ = _activated_store(tmp_path / "reordered")
    lines = store.records_path.read_bytes().splitlines(keepends=True)
    store.records_path.write_bytes(b"".join(reversed(lines)))
    with pytest.raises(AdaptiveCognitiveTwinStoreCorruptError):
        store.read_events()

    second_store, _, _, _ = _activated_store(tmp_path / "sequence")
    sequence_lines = second_store.records_path.read_bytes().splitlines()
    second_record = json.loads(sequence_lines[1].decode("utf-8"))
    second_record["sequence"] = 3
    second_store.records_path.write_bytes(
        sequence_lines[0] + canonical_adaptive_json_bytes(second_record) + b"\n"
    )
    with pytest.raises(AdaptiveCognitiveTwinStoreCorruptError):
        second_store.read_events()


def test_store_does_not_auto_repair_missing_manifest_or_leave_unsafe_recovery_path(
    tmp_path: Path,
) -> None:
    store, _, _, _ = _activated_store(tmp_path)
    vault = store.root.parent.parent / "vault"
    recovery_marker = store.root / ".manifest.recovery.tmp"
    recovery_marker.write_text("untrusted partial state", encoding="utf-8")
    store.manifest_path.unlink()

    with pytest.raises(AdaptiveCognitiveTwinStoreCorruptError):
        AdaptiveCognitiveTwinStore(store.root, vault_root=vault)

    assert recovery_marker.is_file()


def test_stage15_modules_have_no_provider_or_network_import_capability() -> None:
    root = Path(__file__).parents[1]
    paths = (
        root / "src" / "second_brain" / "application" / "adaptive_cognitive_twin.py",
        root / "src" / "second_brain" / "application" / "adaptive_cognitive_twin_projection.py",
        root / "src" / "second_brain" / "application" / "adaptive_cognitive_twin_store.py",
        root / "src" / "second_brain" / "entrypoints" / "web" / "adaptive_cognitive_twin.py",
    )
    forbidden_roots = {"aiohttp", "anthropic", "httpx", "openai", "requests", "socket", "urllib"}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
        assert imported_roots.isdisjoint(forbidden_roots), path


def test_stage15_browser_surface_is_memory_only_and_pwa_never_handles_its_api() -> None:
    root = Path(__file__).parents[1]
    source_paths = (
        root / "web" / "src" / "adaptive-cognitive-twin-api.ts",
        root / "web" / "src" / "adaptive-cognitive-twin-surface.tsx",
        root / "src" / "second_brain" / "entrypoints" / "web" / "adaptive_cognitive_twin.py",
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "Cache Storage",
        "serviceWorker",
    ):
        assert forbidden.casefold() not in source.casefold()
    assert re.search(r"\bconsole\s*\.", source) is None
    assert re.search(r"\b(?:logger|logging|print)\s*\(", source) is None

    worker = (root / "web" / "src" / "sw.ts").read_text(encoding="utf-8")
    assert "adaptive-cognitive-twin" not in worker
    assert "isApiOrAuthPath" in worker
    assert "caches.open(" not in worker
    assert "caches.put(" not in worker


def test_store_event_fingerprints_are_not_recomputed_from_untrusted_history(tmp_path: Path) -> None:
    store, _, _, _ = _activated_store(tmp_path)
    raw = store.records_path.read_bytes().splitlines()
    event = json.loads(raw[0].decode("utf-8"))
    event["record"]["candidate_fingerprint"] = "sha256:" + "f" * 64
    event["event_fingerprint"] = adaptive_store_hash_json(event["record"])
    store.records_path.write_bytes(canonical_adaptive_json_bytes(event) + b"\n" + raw[1] + b"\n")

    with pytest.raises(AdaptiveCognitiveTwinStoreCorruptError):
        store.read_events()
