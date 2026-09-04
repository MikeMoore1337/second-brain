"""Проверки CLI и его стабильных exit codes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from second_brain.application.llm import LlmRequest, NoteDraft
from second_brain.application.ports import (
    CancellationToken,
    CancellationTokenSource,
    LlmError,
    LlmErrorCode,
)
from second_brain.application.research import ResearchRequest, ResearchSource, SourceKind
from second_brain.domain.models import NoteType
from second_brain.entrypoints.cli.app import app
from tests.conftest import create_vault, managed_note, snapshot_tree, write_note

runner = CliRunner()


def test_llm_draft_json_uses_one_gateway_call_without_vault_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instruction = "Сформируй заметку о резервных копиях."
    context = "Контекст без преобразований.\nВторая строка."
    draft = NoteDraft(
        title="Резервные копии",
        note_type=NoteType.ZETTEL,
        content="# Резервные копии\n\nСохраняй несколько копий.",
        tags=("backup", "надёжность"),
        links=("[[Восстановление]]",),
    )
    calls: list[tuple[LlmRequest, CancellationToken]] = []

    class FakePort:
        def draft_note(
            self,
            request: LlmRequest,
            *,
            cancellation: CancellationToken,
        ) -> NoteDraft:
            calls.append((request, cancellation))
            return draft

    def fail_load_config(*args: object, **kwargs: object) -> object:
        del args, kwargs
        pytest.fail("llm draft must not load vault configuration")

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.CloudflareWorkersAiLlmPort",
        lambda: FakePort(),
    )
    monkeypatch.setattr("second_brain.entrypoints.cli.app.load_config", fail_load_config)
    monkeypatch.setenv("SECOND_BRAIN_VAULT_PATH", str(tmp_path / "missing-vault"))
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "account-sentinel")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "token-sentinel")

    result = runner.invoke(
        app,
        [
            "--env-file",
            str(tmp_path / "missing.env"),
            "--vault-path",
            str(tmp_path / "also-missing-vault"),
            "llm",
            "draft",
            "--instruction",
            instruction,
            "--context",
            context,
            "--max-output-bytes",
            "1234",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert list(payload) == ["title", "note_type", "content", "tags", "links"]
    assert payload == {
        "title": draft.title,
        "note_type": "zettel",
        "content": draft.content,
        "tags": ["backup", "надёжность"],
        "links": ["[[Восстановление]]"],
    }
    assert len(calls) == 1
    assert calls[0][0] == LlmRequest(
        instruction=instruction,
        context=context,
        max_output_bytes=1234,
    )
    assert isinstance(calls[0][1], CancellationTokenSource)


def test_llm_draft_text_preserves_content_and_shows_empty_collections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = "Первая строка.\n\nВторая строка без дополнений."
    draft = NoteDraft(
        title="Текстовая заметка",
        note_type=NoteType.AREA,
        content=content,
    )

    class FakePort:
        def draft_note(
            self,
            request: LlmRequest,
            *,
            cancellation: CancellationToken,
        ) -> NoteDraft:
            del request, cancellation
            return draft

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.CloudflareWorkersAiLlmPort",
        lambda: FakePort(),
    )

    result = runner.invoke(
        app,
        ["llm", "draft", "--instruction", "Покажи draft", "--format", "text"],
    )

    assert result.exit_code == 0
    assert result.stdout == (
        f"Title: Текстовая заметка\nType: area\nTags: -\nLinks: -\nContent:\n{content}\n"
    )


@pytest.mark.parametrize("error_code", [code.value for code in LlmErrorCode])
def test_llm_draft_maps_every_llm_error_to_safe_json_diagnostic(
    error_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "provider-body-and-token-sentinel"

    class FakePort:
        def draft_note(
            self,
            request: LlmRequest,
            *,
            cancellation: CancellationToken,
        ) -> NoteDraft:
            del request, cancellation
            raise LlmError(error_code, secret)

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.CloudflareWorkersAiLlmPort",
        lambda: FakePort(),
    )

    result = runner.invoke(
        app,
        ["llm", "draft", "--instruction", "Сделай draft", "--format", "json"],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert secret not in result.stderr
    payload = json.loads(result.stderr)
    assert set(payload) == {"error"}
    assert payload["error"]["code"] == error_code
    assert payload["error"]["message"]


def test_llm_draft_unexpected_runtime_failure_is_generic_and_exit_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "unexpected-provider-runtime-secret"

    def fail_create_port() -> object:
        raise RuntimeError(secret)

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.CloudflareWorkersAiLlmPort",
        fail_create_port,
    )

    result = runner.invoke(
        app,
        ["llm", "draft", "--instruction", "Сделай draft", "--format", "json"],
    )

    assert result.exit_code == 2
    assert "Ошибка runtime CLI: не удалось выполнить LLM draft." in result.stderr
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_llm_draft_help_has_exact_options_without_credential_options() -> None:
    result = runner.invoke(app, ["llm", "draft", "--help"], terminal_width=120, color=False)

    assert result.exit_code == 0
    for option in ("--instruction", "--context", "--max-output-bytes", "--format"):
        assert option in result.stdout
    for forbidden_option in ("--token", "--api-token", "--account-id"):
        assert forbidden_option not in result.stdout


def test_doctor_supports_json_output_and_returns_success(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Note.md", managed_note())
    before = snapshot_tree(vault)
    env_file = tmp_path / "config" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=../vault\n", encoding="utf-8")

    result = runner.invoke(app, ["--env-file", str(env_file), "doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["errors"] == 0
    assert payload["notes"] == 1
    assert payload["manifest"]["schema_version"] == 1
    assert snapshot_tree(vault) == before


def test_validate_returns_one_for_validation_errors(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Note.md", "# Missing metadata\n")
    env_file = tmp_path / ".env"
    env_file.write_text(f"SECOND_BRAIN_VAULT_PATH={vault}\n", encoding="utf-8")

    result = runner.invoke(app, ["--env-file", str(env_file), "vault", "validate"])

    assert result.exit_code == 1
    assert "NOTE_MISSING_ID" in result.stdout


def test_cli_rejects_relative_process_environment_without_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Process environment path намеренно относительный и без config_root.
    assert tmp_path.exists()
    monkeypatch.setenv("SECOND_BRAIN_VAULT_PATH", "../vault")
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 2
    assert "requires an explicit env file/config root" in result.stderr


def test_research_read_is_read_only_and_does_not_require_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ResearchSource(
        uri="https://example.com/article",
        source_kind=SourceKind.WEB,
        retrieved_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        backend="jina-reader",
        content="Внешний текст без обработки.",
        media_type="text/markdown",
    )

    class FakeAdapter:
        def read(self, request: object, *, cancellation: object) -> ResearchSource:
            return source

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.JinaReaderWebAdapter",
        lambda: FakeAdapter(),
    )

    result = runner.invoke(
        app,
        [
            "research",
            "read",
            "--type",
            "web",
            "--url",
            source.uri,
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["uri"] == source.uri
    assert payload["content"] == source.content
    assert payload["backend"] == "jina-reader"


def test_research_read_returns_one_for_research_validation_error_without_vault() -> None:
    result = runner.invoke(
        app,
        ["research", "read", "--type", "web", "--url", "http://localhost/article"],
    )

    assert result.exit_code == 1
    assert "RESEARCH_INVALID_REQUEST" in result.stderr
    assert "vault" not in result.stderr.casefold()


def test_research_read_supports_text_output_without_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ResearchSource(
        uri="https://example.com/article",
        source_kind=SourceKind.WEB,
        retrieved_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        backend="jina-reader",
        content="Текст статьи.",
        media_type="text/markdown",
    )

    class FakeAdapter:
        def read(self, request: object, *, cancellation: object) -> ResearchSource:
            return source

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.JinaReaderWebAdapter",
        lambda: FakeAdapter(),
    )

    result = runner.invoke(
        app,
        ["research", "read", "--url", source.uri, "--format", "text"],
    )

    assert result.exit_code == 0
    assert "Источник: https://example.com/article" in result.stdout
    assert "Content (untrusted external text):" in result.stdout
    assert source.content in result.stdout


def test_research_read_rss_supports_json_without_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ResearchSource(
        uri="https://feeds.example.org/feed.xml",
        source_kind=SourceKind.RSS,
        retrieved_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        backend="feedparser",
        content="# Лента\n\n## Запись",
        title="Лента",
        media_type="application/rss+xml",
    )

    class FakeAdapter:
        def read(self, request: ResearchRequest, *, cancellation: object) -> ResearchSource:
            assert request.source_kind is SourceKind.RSS
            return source

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.PublicRssAdapter",
        lambda: FakeAdapter(),
    )

    result = runner.invoke(
        app,
        [
            "research",
            "read",
            "--type",
            "rss",
            "--url",
            source.uri,
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["source_kind"] == "rss"
    assert payload["backend"] == "feedparser"
    assert payload["content"] == source.content


def test_research_read_rss_supports_text_output_without_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ResearchSource(
        uri="https://feeds.example.org/feed.xml",
        source_kind=SourceKind.RSS,
        retrieved_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        backend="feedparser",
        content="# Лента\n\n## Запись",
        media_type="application/rss+xml",
    )

    class FakeAdapter:
        def read(self, request: object, *, cancellation: object) -> ResearchSource:
            return source

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.PublicRssAdapter",
        lambda: FakeAdapter(),
    )

    result = runner.invoke(
        app,
        ["research", "read", "--type", "rss", "--url", source.uri],
    )

    assert result.exit_code == 0
    assert "Тип: rss" in result.stdout
    assert source.content in result.stdout


def test_research_read_youtube_supports_json_without_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ResearchSource(
        uri="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        source_kind=SourceKind.YOUTUBE,
        retrieved_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        backend="yt-dlp",
        content="Русский transcript.",
        title="Видео",
        author="Канал",
        media_type="text/youtube-transcript",
        upstream_id="dQw4w9WgXcQ",
    )

    class FakeAdapter:
        def read(self, request: ResearchRequest, *, cancellation: object) -> ResearchSource:
            assert request.source_kind is SourceKind.YOUTUBE
            return source

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.PublicYouTubeAdapter",
        lambda: FakeAdapter(),
    )

    result = runner.invoke(
        app,
        [
            "research",
            "read",
            "--type",
            "youtube",
            "--url",
            source.uri,
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["source_kind"] == "youtube"
    assert payload["backend"] == "yt-dlp"
    assert payload["content"] == source.content


def test_research_read_youtube_supports_text_output_without_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ResearchSource(
        uri="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        source_kind=SourceKind.YOUTUBE,
        retrieved_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        backend="yt-dlp",
        content="Текст transcript.",
        media_type="text/youtube-transcript",
    )

    class FakeAdapter:
        def read(self, request: object, *, cancellation: object) -> ResearchSource:
            return source

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.PublicYouTubeAdapter",
        lambda: FakeAdapter(),
    )

    result = runner.invoke(
        app,
        ["research", "read", "--type", "youtube", "--url", source.uri],
    )

    assert result.exit_code == 0
    assert "Тип: youtube" in result.stdout
    assert source.content in result.stdout


def test_research_read_github_supports_json_without_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ResearchSource(
        uri="https://github.com/github/.github",
        source_kind=SourceKind.GITHUB,
        retrieved_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        backend="github-rest",
        content="Repository: github/.github\nREADME:\n\n(отсутствует)",
        title="github/.github",
        author="github",
        media_type="text/plain",
        upstream_id="123456",
    )

    class FakeAdapter:
        def read(self, request: ResearchRequest, *, cancellation: object) -> ResearchSource:
            assert request.source_kind is SourceKind.GITHUB
            return source

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.PublicGitHubAdapter",
        lambda: FakeAdapter(),
    )

    result = runner.invoke(
        app,
        [
            "research",
            "read",
            "--type",
            "github",
            "--url",
            source.uri,
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["source_kind"] == "github"
    assert payload["backend"] == "github-rest"
    assert payload["media_type"] == "text/plain"
    assert payload["content"] == source.content


def test_research_read_github_supports_text_output_without_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ResearchSource(
        uri="https://github.com/github/.github",
        source_kind=SourceKind.GITHUB,
        retrieved_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        backend="github-rest",
        content="Repository: github/.github\nREADME:\n\n# Public repository",
        title="github/.github",
        author="github",
        media_type="text/markdown",
        upstream_id="123456",
    )

    class FakeAdapter:
        def read(self, request: object, *, cancellation: object) -> ResearchSource:
            return source

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.PublicGitHubAdapter",
        lambda: FakeAdapter(),
    )

    result = runner.invoke(
        app,
        ["research", "read", "--type", "github", "--url", source.uri],
    )

    assert result.exit_code == 0
    assert "Тип: github" in result.stdout
    assert source.content in result.stdout


@pytest.mark.parametrize("source_type", ["mastodon"])
def test_research_read_unsupported_source_types_remain_rejected_without_vault(
    source_type: str,
) -> None:
    result = runner.invoke(
        app,
        ["research", "read", "--type", source_type, "--url", "https://example.com/source"],
    )

    assert result.exit_code == 1
    assert "RESEARCH_INVALID_REQUEST" in result.stderr
    assert "vault" not in result.stderr.casefold()
