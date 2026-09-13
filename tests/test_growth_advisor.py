"""Deterministic Stage 11C application tests with provider doubles only."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.assistant import (
    ASSISTANT_CONTRACT_VERSION,
    ASSISTANT_OUTPUT_LABEL,
    AssistantAbstentionCode,
    AssistantCancelledError,
    AssistantExplicitContext,
    AssistantOption,
    AssistantProviderFailureError,
    AssistantProviderUnavailableError,
    AssistantReasoningEnvelopeV1,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
)
from second_brain.application.growth import (
    BuildGrowthGoalContext,
    GrowthEngineRequestV1,
    GrowthGoalSelectionModeV1,
    GrowthGoalSelectionV1,
)
from second_brain.application.growth_advisor import (
    GROWTH_ADVISOR_POLICY_CANONICAL_JSON,
    GROWTH_ADVISOR_POLICY_FINGERPRINT,
    GROWTH_ADVISOR_POLICY_ID,
    BuildGrowthAdvisor,
    GrowthAdvisorBranchStateV1,
    GrowthAdvisorErrorCode,
    GrowthAdvisorGoalTextTooLargeError,
    GrowthAdvisorGoalTextUnsupportedError,
    GrowthAdvisorInvalidRequestError,
    GrowthAdvisorPolicyMismatchError,
    GrowthAdvisorRequestV1,
    growth_goal_identity_fingerprint,
    validate_growth_advisor_policy,
)
from second_brain.application.ports import CancellationToken, CancellationTokenSource
from second_brain.application.self_model import DEFAULT_SELF_MODEL_POLICY
from second_brain.domain.models import parse_uuid7
from tests.conftest import create_vault, write_note

GENERATED_AT = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
GOAL_ID = "0198f4c5-6a00-7000-8000-000000000201"


def _goal_note(
    body: str,
    *,
    domain: str | None = "work",
    evidence_at: str = "2026-09-05T12:00:00Z",
) -> str:
    fields = [
        f"id: {GOAL_ID}",
        "type: zettel",
        "created: 2026-09-05T18:00:00+03:00",
        "tags: []",
        "second_brain_personal_memory: 1",
        "evidence_kind: user_statement",
        "self_kind: goal",
        f'evidence_at: "{evidence_at}"',
        "evidence_at_precision: exact",
    ]
    if domain is not None:
        fields.append(f"domain: {domain}")
    return "---\n" + "\n".join(fields) + f"\n---\n{body}"


class _RecordingAdvisor:
    def __init__(self, outcome: AssistantResultEnvelopeV1 | Exception | object) -> None:
        self.outcome = outcome
        self.calls = 0
        self.requests: list[AssistantReasoningEnvelopeV1] = []

    def advise(
        self,
        request: AssistantReasoningEnvelopeV1,
        *,
        cancellation: CancellationToken,
    ) -> AssistantResultEnvelopeV1:
        self.calls += 1
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return cast(AssistantResultEnvelopeV1, self.outcome)


class _CountingReader:
    def __init__(self, reader: FileSystemVaultReader) -> None:
        self.reader = reader
        self.calls = 0

    def scan(self) -> Any:
        self.calls += 1
        return self.reader.scan()


def _analysis() -> AssistantResultEnvelopeV1:
    return AssistantResultEnvelopeV1(
        output_label=ASSISTANT_OUTPUT_LABEL,
        kind=AssistantResultKind.ANALYSIS,
        recommendation=None,
        selected_option=None,
        rationale=("Рассмотрен только явный запрос и выбранная цель.",),
        evidence_refs=(),
        constraints_used=(),
        objectives_used=(),
        uncertainty=(),
        abstention_code=None,
        contract_version=ASSISTANT_CONTRACT_VERSION,
    )


def _abstention() -> AssistantResultEnvelopeV1:
    return AssistantResultEnvelopeV1(
        output_label=ASSISTANT_OUTPUT_LABEL,
        kind=AssistantResultKind.ABSTENTION,
        recommendation=None,
        selected_option=None,
        rationale=("Явных оснований недостаточно.",),
        evidence_refs=(),
        constraints_used=(),
        objectives_used=(),
        uncertainty=(),
        abstention_code=AssistantAbstentionCode.INSUFFICIENT_BASIS,
        contract_version=ASSISTANT_CONTRACT_VERSION,
    )


def _request_for(vault: Path) -> GrowthAdvisorRequestV1:
    context = BuildGrowthGoalContext(
        FileSystemVaultReader(vault),
        clock=lambda: GENERATED_AT,
    ).execute(
        GrowthEngineRequestV1(
            selection=GrowthGoalSelectionV1(
                GrowthGoalSelectionModeV1.SELECTED_GOAL,
                parse_uuid7(GOAL_ID),
            )
        )
    )
    identity_fingerprint = growth_goal_identity_fingerprint(context.goals[0])
    return GrowthAdvisorRequestV1(
        contract_version="growth-advisor-v1",
        goal_source_uuid=GOAL_ID,
        goal_identity_fingerprint=identity_fingerprint,
        task="Разобрать следующий шаг",
        options=(AssistantOption("a", "Первый вариант"),),
        explicit_constraints=("Не менять цель автоматически",),
        explicit_context=(AssistantExplicitContext("fact", "У меня есть один час"),),
    )


def _runtime(
    tmp_path: Path,
    *,
    body: str = "  Цель e\u0301  ",
    outcome: AssistantResultEnvelopeV1 | Exception | object | None = None,
) -> tuple[Path, GrowthAdvisorRequestV1, BuildGrowthAdvisor, _RecordingAdvisor]:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Goal.md", _goal_note(body))
    request = _request_for(vault)
    advisor = _RecordingAdvisor(_analysis() if outcome is None else outcome)
    runtime = BuildGrowthAdvisor(
        _CountingReader(FileSystemVaultReader(vault)),
        advisor,
        clock=lambda: GENERATED_AT,
    )
    return vault, request, runtime, advisor


def test_policy_is_computed_from_the_normative_canonical_json() -> None:
    assert validate_growth_advisor_policy() == GROWTH_ADVISOR_POLICY_FINGERPRINT
    assert GROWTH_ADVISOR_POLICY_CANONICAL_JSON.startswith('{"assistant_contract":"assistant-v1"')
    assert "\n" not in GROWTH_ADVISOR_POLICY_CANONICAL_JSON


def test_request_parser_is_exact_and_has_no_client_goal_authority() -> None:
    raw = {
        "contract_version": "growth-advisor-v1",
        "goal_source_uuid": GOAL_ID,
        "goal_identity_fingerprint": "sha256:" + "a" * 64,
        "task": "Задача",
        "options": [],
        "explicit_constraints": [],
        "explicit_context": [],
        "max_context_bytes": 65536,
        "max_result_bytes": 65536,
    }
    request = GrowthAdvisorRequestV1.from_dict(raw)
    projection = request.as_dict()
    assert "goal_text" not in projection
    assert "explicit_goals" not in projection
    assert "provider" not in projection

    with pytest.raises(GrowthAdvisorInvalidRequestError):
        GrowthAdvisorRequestV1.from_dict({**raw, "goal_text": "forbidden"})
    with pytest.raises(GrowthAdvisorInvalidRequestError):
        GrowthAdvisorRequestV1.from_dict({**raw, "max_context_bytes": True})
    with pytest.raises(GrowthAdvisorInvalidRequestError):
        GrowthAdvisorRequestV1.from_dict({**raw, "options": [{"id": "a"}]})


def test_preview_rebuilds_exact_goal_and_never_invokes_advisor(tmp_path: Path) -> None:
    _vault, request, runtime, advisor = _runtime(tmp_path)

    preview = runtime.preview(request)

    assert preview.goal_source_uuid == request.goal_source_uuid
    assert preview.goal_identity_fingerprint == request.goal_identity_fingerprint
    assert preview.goal_text == "Цель é"
    assert preview.goal_text_utf8_bytes == len(preview.goal_text.encode("utf-8"))
    assert advisor.calls == 0
    assert "Цель" not in request.to_json()


def test_execute_passes_only_caller_fields_and_one_exact_goal(tmp_path: Path) -> None:
    _vault, request, runtime, advisor = _runtime(tmp_path)
    preview = runtime.preview(request)

    branch = runtime.execute(request, preview, confirmed=True)

    assert branch.state is GrowthAdvisorBranchStateV1.RESULT
    assert branch.error is None
    assert branch.assistant_result is not None
    assert branch.provenance is not None
    envelope = advisor.requests[0]
    assert advisor.calls == 1
    assert envelope.task == request.task
    assert envelope.options == request.options
    assert envelope.explicit_constraints == request.explicit_constraints
    assert envelope.explicit_context == request.explicit_context
    assert envelope.explicit_goals == (preview.goal_text,)
    assert not hasattr(envelope, "goal_source_uuid")
    assert not hasattr(envelope, "policy_fingerprint")
    assert branch.provenance.advisor_policy_id == GROWTH_ADVISOR_POLICY_ID
    assert branch.provenance.goal_identity_fingerprint == request.goal_identity_fingerprint


def test_valid_assistant_abstention_is_a_separate_transient_state(tmp_path: Path) -> None:
    _vault, request, runtime, advisor = _runtime(tmp_path, outcome=_abstention())
    preview = runtime.preview(request)

    branch = runtime.execute(request, preview)

    assert branch.state is GrowthAdvisorBranchStateV1.ABSTENTION
    assert branch.assistant_result is not None
    assert branch.assistant_result.kind is AssistantResultKind.ABSTENTION
    assert branch.error is None
    assert advisor.calls == 1


def test_goal_edit_fails_closed_before_provider_call(tmp_path: Path) -> None:
    vault, request, runtime, advisor = _runtime(tmp_path)
    preview = runtime.preview(request)
    write_note(vault, "10 Projects/Goal.md", _goal_note("Изменённая цель"))

    branch = runtime.execute(request, preview)

    assert branch.error is not None
    assert branch.error.code is GrowthAdvisorErrorCode.GOAL_CHANGED
    assert advisor.calls == 0


def test_goal_delete_fails_closed_before_provider_call(tmp_path: Path) -> None:
    vault, request, runtime, advisor = _runtime(tmp_path)
    preview = runtime.preview(request)
    (vault / "10 Projects/Goal.md").unlink()

    branch = runtime.execute(request, preview)

    assert branch.error is not None
    assert branch.error.code is GrowthAdvisorErrorCode.GOAL_MISSING
    assert advisor.calls == 0


@pytest.mark.parametrize(
    ("body", "error_type"),
    [
        ("x" * 513, GrowthAdvisorGoalTextTooLargeError),
        ("Цель\nс control", GrowthAdvisorGoalTextUnsupportedError),
    ],
)
def test_goal_projection_uses_assistant_bounds_without_truncation(
    tmp_path: Path,
    body: str,
    error_type: type[Exception],
) -> None:
    _vault, request, runtime, advisor = _runtime(tmp_path, body=body)

    with pytest.raises(error_type):
        runtime.preview(request)

    assert advisor.calls == 0


def test_context_overflow_is_rejected_before_provider_call(tmp_path: Path) -> None:
    _vault, request, runtime, advisor = _runtime(tmp_path)
    request = replace(request, max_context_bytes=1)
    preview = runtime.preview(request)

    branch = runtime.execute(request, preview)

    assert branch.error is not None
    assert branch.error.code is GrowthAdvisorErrorCode.CONTEXT_TOO_LARGE
    assert advisor.calls == 0


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (
            AssistantProviderUnavailableError(),
            GrowthAdvisorErrorCode.RECOMMENDATION_UNAVAILABLE,
        ),
        (AssistantProviderFailureError(), GrowthAdvisorErrorCode.FAILURE),
        (AssistantCancelledError(), GrowthAdvisorErrorCode.CANCELLED),
        (TimeoutError(), GrowthAdvisorErrorCode.TIMEOUT),
        (object(), GrowthAdvisorErrorCode.INVALID_RESULT),
    ],
)
def test_provider_errors_are_fixed_and_never_retried(
    tmp_path: Path,
    outcome: object,
    expected: GrowthAdvisorErrorCode,
) -> None:
    _vault, request, runtime, advisor = _runtime(tmp_path, outcome=outcome)
    preview = runtime.preview(request)

    branch = runtime.execute(request, preview)

    assert branch.error is not None
    assert branch.error.code is expected
    assert branch.assistant_result is None
    assert branch.provenance is None
    assert advisor.calls == 1


def test_cancelled_operation_wins_before_rebuild_and_provider_call(tmp_path: Path) -> None:
    _vault, request, runtime, advisor = _runtime(tmp_path)
    preview = runtime.preview(request)
    reader = cast(_CountingReader, runtime.reader)
    source = CancellationTokenSource()
    source.cancel()
    calls_before = reader.calls

    branch = runtime.execute(request, preview, cancellation=source.token)

    assert branch.error is not None
    assert branch.error.code is GrowthAdvisorErrorCode.CANCELLED
    assert reader.calls == calls_before
    assert advisor.calls == 0


def test_fixed_monotonic_deadline_is_finite_and_blocks_before_rebuild(tmp_path: Path) -> None:
    _vault, request, runtime, advisor = _runtime(tmp_path)
    preview = runtime.preview(request)
    reader = cast(_CountingReader, runtime.reader)
    values = iter((0.0, 31.0))
    runtime.monotonic_clock = lambda: next(values)
    calls_before = reader.calls

    branch = runtime.execute(request, preview)

    assert branch.error is not None
    assert branch.error.code is GrowthAdvisorErrorCode.TIMEOUT
    assert reader.calls == calls_before
    assert advisor.calls == 0


def test_runtime_does_not_write_vault_or_growth_mapping_state(tmp_path: Path) -> None:
    vault, request, runtime, _advisor = _runtime(tmp_path)
    before = {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
    }
    preview = runtime.preview(request)
    runtime.execute(request, preview)
    after = {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
    }

    assert after == before


def test_stage4_policy_drift_is_not_sent_to_advisor(tmp_path: Path) -> None:
    vault, request, _runtime_value, advisor = _runtime(tmp_path)
    runtime = BuildGrowthAdvisor(
        FileSystemVaultReader(vault),
        advisor,
        policy=replace(DEFAULT_SELF_MODEL_POLICY, claim_generation_policy="wrong-policy"),
        clock=lambda: GENERATED_AT,
    )

    with pytest.raises(GrowthAdvisorPolicyMismatchError):
        runtime.preview(request)

    assert advisor.calls == 0
