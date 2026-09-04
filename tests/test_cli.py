"""Проверки CLI и его стабильных exit codes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from second_brain.application.research import ResearchRequest, ResearchSource, SourceKind
from second_brain.entrypoints.cli.app import app
from tests.conftest import create_vault, managed_note, snapshot_tree, write_note

runner = CliRunner()


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
