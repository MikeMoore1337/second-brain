"""Integrity and recovery tests for the Stage 19 operational receipt store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid7

import pytest

from second_brain.application.action_gateway import (
    ACTION_GATEWAY_CONNECTOR,
    ACTION_GATEWAY_CREDENTIAL_PROFILE_ID,
    ACTION_GATEWAY_POLICY_ID,
    ActionIntentV1,
    ActionKindV1,
    ActionReceiptKindV1,
    ActionReceiptStateV1,
    ActionReceiptV1,
    ExactTargetIdentityV1,
    PreparedExternalActionV1,
    ReversibilityV1,
    RiskClassV1,
    action_gateway_hash,
)
from second_brain.application.action_gateway_store import (
    ACTION_GATEWAY_STORE_RECORD_FILE_NAME,
    ActionGatewayOperationalStore,
    ActionGatewayStoreCorruptError,
    derive_action_gateway_store_root,
)

NOW = datetime(2026, 9, 16, 21, 0, tzinfo=UTC)


def _prepared(operation_id: str = "store-operation") -> PreparedExternalActionV1:
    intent = ActionIntentV1(
        "action-intent-v1",
        operation_id,
        ActionKindV1.GITHUB_ISSUE_CREATE,
        ACTION_GATEWAY_CONNECTOR,
        "MikeMoore1337/second-brain",
        title="Задача",
        body="Текст",
    )
    target = ExactTargetIdentityV1("MikeMoore1337/second-brain", 1, "R_repo")
    marker = "<!-- second-brain-action:0199b5f4-8b9d-7f24-b3b0-123456789abc -->"
    payload: dict[str, object] = {
        "repository": intent.repository,
        "title": intent.title,
        "body": f"{intent.body}\n{marker}",
        "marker": marker,
    }
    preview = "Предпросмотр действия GitHub"
    return PreparedExternalActionV1(
        uuid7(),
        "prepared-external-action-v1",
        intent.operation_id_fingerprint,
        intent.action_kind,
        RiskClassV1.CONTROLLED_WRITE,
        ACTION_GATEWAY_CONNECTOR,
        ACTION_GATEWAY_POLICY_ID,
        ACTION_GATEWAY_CREDENTIAL_PROFILE_ID,
        target,
        "a" * 64,
        payload,
        action_gateway_hash(payload),
        preview,
        action_gateway_hash(preview),
        NOW,
        datetime(2026, 9, 16, 21, 5, tzinfo=UTC),
        ReversibilityV1.COMPENSATION_ONLY,
    )


def test_store_is_outside_git_and_derivation_requires_explicit_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "runtime" / "web.env"
    env_file.parent.mkdir()
    env_file.write_text("SAFE=1\n", encoding="utf-8")
    root = derive_action_gateway_store_root(env_file)
    assert root == env_file.parent / "prospective-audit" / "action-gateway"
    store = ActionGatewayOperationalStore(root)
    assert store.root == root
    assert store.read_receipts() == ()
    assert derive_action_gateway_store_root(tmp_path / "missing.env") is None


def test_hash_chain_readback_and_restart_persistence(tmp_path: Path) -> None:
    store = ActionGatewayOperationalStore(tmp_path / "action-gateway")
    prepared = _prepared()
    started, created = store.begin_execution(prepared, now=NOW)
    assert created is True
    assert started.state is ActionReceiptStateV1.EXECUTION_STARTED
    final = ActionReceiptV1(
        receipt_id=uuid7(),
        receipt_kind=ActionReceiptKindV1.ACTION,
        operation_id_fingerprint=started.operation_id_fingerprint,
        prepared_action_id=started.prepared_action_id,
        intent_fingerprint=started.intent_fingerprint,
        action_kind=started.action_kind,
        risk=started.risk,
        connector_policy_id=started.connector_policy_id,
        credential_profile_id=started.credential_profile_id,
        target_safe_identity=started.target_safe_identity,
        payload_fingerprint=started.payload_fingerprint,
        state=ActionReceiptStateV1.EXECUTED,
        attempt_started_at=started.attempt_started_at,
        sent_at=NOW,
        finished_at=NOW,
        remote_safe_identity={"issue_id": 2, "issue_number": 123, "state": "open"},
        remote_url="https://github.com/MikeMoore1337/second-brain/issues/123",
        parent_receipt_id=started.receipt_id,
    )
    store.append(final)
    reopened = ActionGatewayOperationalStore(tmp_path / "action-gateway")
    assert reopened.find_operation(prepared.operation_id_fingerprint) == final
    assert reopened.manifest.record_count == 2
    assert len(reopened.records_path.read_bytes().splitlines()) == 2


def test_torn_or_tampered_jsonl_fails_closed(tmp_path: Path) -> None:
    store = ActionGatewayOperationalStore(tmp_path / "action-gateway")
    prepared = _prepared()
    store.begin_execution(prepared, now=NOW)
    records = store.records_path
    original = records.read_text(encoding="utf-8")
    records.write_text(original[:-1], encoding="utf-8")
    with pytest.raises(ActionGatewayStoreCorruptError):
        store.read_verified_snapshot()


def test_duplicate_receipt_identity_and_noncanonical_manifest_are_rejected(tmp_path: Path) -> None:
    store = ActionGatewayOperationalStore(tmp_path / "action-gateway")
    prepared = _prepared()
    store.begin_execution(prepared, now=NOW)
    raw = store.records_path.read_text(encoding="utf-8")
    store.records_path.write_text(raw + raw, encoding="utf-8")
    with pytest.raises(ActionGatewayStoreCorruptError):
        store.read_verified_snapshot()

    fresh = ActionGatewayOperationalStore(tmp_path / "fresh-action-gateway")
    manifest = json.loads(fresh.manifest_path.read_text(encoding="utf-8"))
    fresh.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with pytest.raises(ActionGatewayStoreCorruptError):
        fresh.read_verified_snapshot()


def test_store_uses_expected_operational_filenames(tmp_path: Path) -> None:
    store = ActionGatewayOperationalStore(tmp_path / "action-gateway")
    assert store.records_path.name == ACTION_GATEWAY_STORE_RECORD_FILE_NAME
    assert store.manifest_path.name == "manifest.json"
    assert store.lock_path.name == ".store.lock"
