"""Детерминированные CLI-проверки research draft без live network/provider calls."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from typer.testing import CliRunner

from second_brain.application.llm import MAX_CONTEXT_BYTES, LlmRequest, NoteDraft
from second_brain.application.ports import (
    CancellationToken,
    LlmError,
    LlmErrorCode,
    ResearchError,
    ResearchErrorCode,
)
from second_brain.application.research import ResearchRequest, ResearchSource, SourceKind
from second_brain.domain.models import NoteType
from second_brain.entrypoints.cli.app import app

runner = CliRunner()


def make_source() -> ResearchSource:
    """Собрать fake source с metadata, которая не должна попасть в LLM instruction."""

    return ResearchSource(
        uri="https://example.com/article",
        source_kind=SourceKind.WEB,
        retrieved_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        backend="fake-research-backend",
        content="Точный untrusted source content.\nВторая строка.",
        title="source-title-sentinel",
        author="source-author-sentinel",
    )


def make_draft() -> NoteDraft:
    """Собрать fake semantic NoteDraft."""

    return NoteDraft(
        title="Research draft",
        note_type=NoteType.ZETTEL,
        content="# Research draft\n\nСодержимое.",
        tags=("research",),
        links=("[[Связь]]",),
    )


class FakeResearchAdapter:
    """Production-port substitute для monkeypatch-only CLI tests."""

    def __init__(self, source: ResearchSource, error: Exception | None = None) -> None:
        self.source = source
        self.error = error
        self.calls: list[ResearchRequest] = []

    def read(
        self,
        request: ResearchRequest,
        *,
        cancellation: CancellationToken,
    ) -> ResearchSource:
        del cancellation
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.source


class FakeLlmPort:
    """Provider-port substitute без credentials, network или retry."""

    def __init__(self, draft: NoteDraft | None = None, error: Exception | None = None) -> None:
        self.draft = draft
        self.error = error
        self.calls: list[LlmRequest] = []

    def draft_note(
        self,
        request: LlmRequest,
        *,
        cancellation: CancellationToken,
    ) -> NoteDraft:
        del cancellation
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        assert self.draft is not None
        return self.draft


def patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    research_adapter: FakeResearchAdapter,
    llm_port: FakeLlmPort,
) -> None:
    """Подменить только production adapter constructors и запретить vault config."""

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.JinaReaderWebAdapter",
        lambda: research_adapter,
    )
    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.CloudflareWorkersAiLlmPort",
        lambda: llm_port,
    )

    def fail_load_config(*args: object, **kwargs: object) -> object:
        del args, kwargs
        pytest.fail("research draft must not load vault configuration")

    monkeypatch.setattr("second_brain.entrypoints.cli.app.load_config", fail_load_config)

    def fail_forbidden_constructor(*args: object, **kwargs: object) -> object:
        del args, kwargs
        pytest.fail("research draft must not construct vault/Git/Safe Write components")

    for name in (
        "FileSystemVaultReader",
        "FileSystemVaultWriter",
        "GitVersionControlAdapter",
        "GitHubPullRequestAdapter",
    ):
        monkeypatch.setattr(f"second_brain.entrypoints.cli.app.{name}", fail_forbidden_constructor)


def invoke_valid(
    *,
    output_format: str,
    source: ResearchSource,
    instruction: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, FakeResearchAdapter, FakeLlmPort]:
    """Выполнить одну fake-only команду с полной CLI surface."""

    research_adapter = FakeResearchAdapter(source)
    llm_port = FakeLlmPort(make_draft())
    patch_runtime(monkeypatch, research_adapter, llm_port)
    result = runner.invoke(
        app,
        [
            "research",
            "draft",
            "--type",
            "web",
            "--url",
            source.uri,
            "--instruction",
            instruction,
            "--timeout",
            "17",
            "--max-source-bytes",
            "4096",
            "--max-output-bytes",
            "2048",
            "--format",
            output_format,
        ],
    )
    return result, research_adapter, llm_port


def test_research_draft_json_uses_exact_requests_and_five_note_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_source()
    instruction = "Пользовательская инструкция без изменений."

    result, research_adapter, llm_port = invoke_valid(
        output_format="json",
        source=source,
        instruction=instruction,
        monkeypatch=monkeypatch,
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert list(payload) == ["title", "note_type", "content", "tags", "links"]
    assert payload == {
        "title": "Research draft",
        "note_type": "zettel",
        "content": "# Research draft\n\nСодержимое.",
        "tags": ["research"],
        "links": ["[[Связь]]"],
    }
    assert len(research_adapter.calls) == 1
    assert research_adapter.calls[0] == ResearchRequest(
        source_kind=SourceKind.WEB,
        uri=source.uri,
        timeout_seconds=17,
        max_bytes=4096,
    )
    assert len(llm_port.calls) == 1
    assert llm_port.calls[0] == LlmRequest(
        instruction=instruction,
        context=source.content,
        max_output_bytes=2048,
    )
    assert llm_port.calls[0].context is source.content
    assert "source-title-sentinel" not in llm_port.calls[0].instruction
    assert "source-author-sentinel" not in llm_port.calls[0].instruction


def test_research_draft_cli_defaults_source_cap_to_max_context_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_source()
    research_adapter = FakeResearchAdapter(source)
    llm_port = FakeLlmPort(make_draft())
    patch_runtime(monkeypatch, research_adapter, llm_port)

    result = runner.invoke(
        app,
        [
            "research",
            "draft",
            "--url",
            source.uri,
            "--instruction",
            "Проверь default source cap.",
        ],
    )

    assert result.exit_code == 0
    assert len(research_adapter.calls) == 1
    assert research_adapter.calls[0].max_bytes == MAX_CONTEXT_BYTES
    assert llm_port.calls[0].max_output_bytes == 64 * 1024


def test_research_draft_text_reuses_existing_note_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _, _ = invoke_valid(
        output_format="text",
        source=make_source(),
        instruction="Сделай текстовый draft.",
        monkeypatch=monkeypatch,
    )

    assert result.exit_code == 0
    assert result.stdout == (
        "Title: Research draft\n"
        "Type: zettel\n"
        "Tags: research\n"
        "Links: [[Связь]]\n"
        "Content:\n"
        "# Research draft\n\nСодержимое.\n"
    )


def test_research_draft_help_has_exact_surface_and_bounded_read_only_statement() -> None:
    from typer.main import get_command

    root_command: Any = get_command(app)
    research_command: Any = root_command.commands["research"]
    draft_command: Any = research_command.commands["draft"]
    option_names = {
        option_name
        for parameter in draft_command.params
        for option_name in getattr(parameter, "opts", ())
    }

    assert option_names == {
        "--type",
        "--url",
        "--instruction",
        "--timeout",
        "--max-source-bytes",
        "--max-output-bytes",
        "--format",
    }
    assert "--context" not in option_names
    assert "--account-id" not in option_names
    assert "--api-token" not in option_names

    result = runner.invoke(app, ["research", "draft", "--help"])

    assert result.exit_code == 0
    help_text = result.stdout.casefold()
    assert "networked read-only" in help_text
    assert "one research read" in help_text
    assert "at most one llm draft" in help_text


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_research_error_is_safe_and_does_not_call_llm(
    output_format: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "research-body-and-token-secret"
    research_adapter = FakeResearchAdapter(
        make_source(),
        error=ResearchError(ResearchErrorCode.UPSTREAM_FAILURE, secret),
    )
    llm_port = FakeLlmPort(make_draft())
    patch_runtime(monkeypatch, research_adapter, llm_port)

    result = runner.invoke(
        app,
        [
            "research",
            "draft",
            "--type",
            "web",
            "--url",
            "https://example.com/article",
            "--instruction",
            "Сделай draft",
            "--format",
            output_format,
        ],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert secret not in result.stderr
    assert "Traceback" not in result.stderr
    assert len(research_adapter.calls) == 1
    assert llm_port.calls == []
    if output_format == "json":
        payload = json.loads(result.stderr)
        assert payload["error"]["code"] == ResearchErrorCode.UPSTREAM_FAILURE.value
    else:
        assert "Ошибка research: RESEARCH_UPSTREAM_FAILURE" in result.stderr


def test_llm_error_is_safe_and_does_not_repeat_research(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "provider-response-secret"
    research_adapter = FakeResearchAdapter(make_source())
    llm_port = FakeLlmPort(error=LlmError(LlmErrorCode.UPSTREAM_FAILURE, secret))
    patch_runtime(monkeypatch, research_adapter, llm_port)

    result = runner.invoke(
        app,
        [
            "research",
            "draft",
            "--url",
            "https://example.com/article",
            "--instruction",
            "Сделай draft",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert secret not in result.stderr
    assert json.loads(result.stderr)["error"]["code"] == LlmErrorCode.UPSTREAM_FAILURE.value
    assert len(research_adapter.calls) == 1
    assert len(llm_port.calls) == 1


def test_oversize_source_cap_is_rejected_before_fake_external_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    research_adapter = FakeResearchAdapter(make_source())
    llm_port = FakeLlmPort(make_draft())
    patch_runtime(monkeypatch, research_adapter, llm_port)

    result = runner.invoke(
        app,
        [
            "research",
            "draft",
            "--url",
            "https://example.com/article",
            "--instruction",
            "Сделай draft",
            "--max-source-bytes",
            str(MAX_CONTEXT_BYTES + 1),
        ],
    )

    assert result.exit_code == 1
    assert "RESEARCH_INVALID_REQUEST" in result.stderr
    assert research_adapter.calls == []
    assert llm_port.calls == []


def test_unexpected_runtime_is_generic_without_secret_leakage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "unexpected-local-runtime-secret"

    def fail_adapter_factory(source_kind: SourceKind) -> object:
        del source_kind
        raise RuntimeError(secret)

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app._research_adapter",
        fail_adapter_factory,
    )

    result = runner.invoke(
        app,
        [
            "research",
            "draft",
            "--url",
            "https://example.com/article",
            "--instruction",
            "Сделай draft",
        ],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Ошибка runtime CLI: не удалось выполнить research draft.\n"
    assert secret not in result.stderr
