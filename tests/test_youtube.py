"""Детерминированные проверки public YouTube adapter без live network."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from second_brain.adapters.research.process import ProcessResult
from second_brain.adapters.research.youtube import (
    MAX_CAPTION_STDOUT_BYTES,
    MAX_METADATA_BYTES,
    YOUTUBE_MEDIA_TYPE,
    YTDLP_BACKEND,
    YTDLP_EXECUTABLE,
    PublicYouTubeAdapter,
    _normalize_vtt,
)
from second_brain.application.ports import (
    CancellationToken,
    CancellationTokenSource,
    ResearchBackendUnavailableError,
    ResearchCancelledError,
    ResearchContentTooLargeError,
    ResearchInvalidRequestError,
    ResearchMalformedResultError,
    ResearchUpstreamError,
)
from second_brain.application.research import (
    ResearchGateway,
    ResearchRequest,
    ResearchSource,
    SourceKind,
)

VIDEO_ID = "dQw4w9WgXcQ"
VIDEO_URI = f"https://www.youtube.com/watch?v={VIDEO_ID}"
CANONICAL_URI = VIDEO_URI
CAPTION_VTT = """WEBVTT

00:00:00.000 --> 00:00:02.000
<c.colorE5E5E5>Привет</c>

00:00:02.000 --> 00:00:04.000
Привет мир

00:00:04.000 --> 00:00:06.000
<script>alert('не выполнять')</script>Текст
""".encode()


def _track() -> list[dict[str, str]]:
    """Сделать fixture track с намеренно неиспользуемым signed-like URL."""

    return [{"ext": "vtt", "url": "https://www.youtube.com/api/caption?sig=secret-token"}]


def _metadata(
    *,
    subtitles: dict[str, object] | None = None,
    automatic_captions: dict[str, object] | None = None,
    **extra: object,
) -> bytes:
    payload: dict[str, object] = {
        "_type": "video",
        "id": VIDEO_ID,
        "webpage_url": CANONICAL_URI,
        "title": "Русское видео\nс заголовком",
        "channel": "Канал автора",
        "subtitles": subtitles if subtitles is not None else {"ru": _track()},
        "automatic_captions": automatic_captions if automatic_captions is not None else {},
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class FakeRunner:
    """Fake process boundary, который пишет только ожидаемый VTT fixture."""

    def __init__(
        self,
        metadata: bytes | None = None,
        caption: bytes = CAPTION_VTT,
        caption_result: ProcessResult | None = None,
    ) -> None:
        self.metadata = metadata or _metadata()
        self.caption = caption
        self.caption_result = caption_result or ProcessResult(0, b"", b"")
        self.calls: list[tuple[tuple[str, ...], float, int, CancellationToken]] = []
        self.output_templates: list[Path] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        max_stdout_bytes: int,
        cancellation: CancellationToken,
    ) -> ProcessResult:
        command = tuple(argv)
        self.calls.append((command, timeout_seconds, max_stdout_bytes, cancellation))
        if len(self.calls) == 1:
            return ProcessResult(0, self.metadata, b"metadata stderr secret")
        template = Path(command[command.index("--output") + 1])
        self.output_templates.append(template)
        template.parent.mkdir(parents=True, exist_ok=True)
        (template.parent / "caption.vtt").write_bytes(self.caption)
        return self.caption_result


def _read(
    runner: FakeRunner,
    *,
    request: ResearchRequest | None = None,
    monotonic: Any = None,
) -> ResearchSource:
    adapter = PublicYouTubeAdapter(
        runner=runner,
        clock=lambda: datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        monotonic=monotonic or (lambda: 0.0),
    )
    return ResearchGateway(adapter).read(
        request or ResearchRequest(SourceKind.YOUTUBE, VIDEO_URI),
        cancellation=CancellationTokenSource(),
    )


def test_youtube_uses_fixed_bounded_argv_and_normalizes_metadata_and_vtt() -> None:
    runner = FakeRunner()

    source = _read(runner)

    metadata_argv, metadata_timeout, metadata_limit, _ = runner.calls[0]
    caption_argv, caption_timeout, caption_limit, _ = runner.calls[1]
    assert metadata_argv[0] == YTDLP_EXECUTABLE
    assert metadata_argv[-1] == VIDEO_URI
    assert metadata_argv.count(VIDEO_URI) == 1
    assert metadata_argv[metadata_argv.index("--dump-single-json") - 1] == "--skip-download"
    assert metadata_argv[metadata_argv.index("--") + 1] == VIDEO_URI
    assert metadata_timeout == 30
    assert metadata_limit == MAX_METADATA_BYTES
    assert caption_timeout == 30
    assert caption_limit == MAX_CAPTION_STDOUT_BYTES
    assert caption_argv[-1] == VIDEO_URI
    assert caption_argv.count(VIDEO_URI) == 1
    assert caption_argv[caption_argv.index("--sub-langs") + 1] == "ru"
    assert "--write-subs" in caption_argv
    assert "--write-auto-subs" not in caption_argv
    assert caption_argv[caption_argv.index("--sub-format") + 1] == "vtt"
    for forbidden in (
        "--cookies",
        "--cookies-from-browser",
        "--netrc",
        "--proxy",
        "--extractor-args",
        "--add-headers",
        "--write-thumbnail",
        "--write-info-json",
        "--audio-format",
    ):
        assert forbidden not in metadata_argv
        assert forbidden not in caption_argv
    for required in ("--ignore-config", "--no-cache-dir", "--no-playlist", "--skip-download"):
        assert required in metadata_argv
        assert required in caption_argv
    assert source.uri == VIDEO_URI
    assert source.source_kind is SourceKind.YOUTUBE
    assert source.backend == YTDLP_BACKEND
    assert source.upstream_id == VIDEO_ID
    assert source.title == "Русское видео с заголовком"
    assert source.author == "Канал автора"
    assert source.media_type == YOUTUBE_MEDIA_TYPE
    assert source.content == "Привет\nмир\nТекст"
    assert runner.output_templates
    assert not runner.output_templates[0].parent.exists()


@pytest.mark.parametrize(
    ("cue_text", "expected"),
    [
        ("<00:00:00.250><c>Hello</c>", "Hello"),
        ("Hello <00:00:00.500>world", "Hello world"),
    ],
)
def test_inline_webvtt_timestamp_tags_are_removed_before_cue_normalization(
    cue_text: str,
    expected: str,
) -> None:
    raw_vtt = f"""WEBVTT

00:00:00.000 --> 00:00:02.000
{cue_text}
""".encode()

    normalized = _normalize_vtt(raw_vtt, max_bytes=1024)

    assert normalized == expected
    assert "<00:" not in normalized


def test_inline_webvtt_timestamp_removal_preserves_adjacent_cue_dedup() -> None:
    raw_vtt = b"""WEBVTT

00:00:00.000 --> 00:00:02.000
<00:00:00.250><c>Hello</c>

00:00:02.000 --> 00:00:04.000
<c>Hello</c><00:00:02.250> world
"""

    normalized = _normalize_vtt(raw_vtt, max_bytes=1024)

    assert normalized == "Hello\nworld"
    assert "<00:" not in normalized


def test_manual_caption_is_preferred_over_automatic_even_when_automatic_is_ru() -> None:
    runner = FakeRunner(
        metadata=_metadata(
            subtitles={"en": _track()},
            automatic_captions={"ru": _track()},
        )
    )

    _read(runner)

    caption_argv = runner.calls[1][0]
    assert "--write-subs" in caption_argv
    assert "--write-auto-subs" not in caption_argv
    assert caption_argv[caption_argv.index("--sub-langs") + 1] == "en"


@pytest.mark.parametrize(
    ("languages", "expected"),
    [
        ({"de": _track(), "en-US": _track(), "ru-orig": _track()}, "ru-orig"),
        ({"de": _track(), "en-US": _track(), "live_chat": _track()}, "en-US"),
        ({"fr": _track(), "de": _track(), "livechat": _track()}, "de"),
    ],
)
def test_automatic_language_policy_is_ru_then_en_then_deterministic_fallback(
    languages: dict[str, object],
    expected: str,
) -> None:
    runner = FakeRunner(metadata=_metadata(subtitles={}, automatic_captions=languages))

    _read(runner)

    assert runner.calls[1][0][runner.calls[1][0].index("--sub-langs") + 1] == expected
    assert "live_chat" not in runner.calls[1][0]


def test_non_youtube_source_kind_fails_closed_without_process() -> None:
    runner = FakeRunner()
    adapter = PublicYouTubeAdapter(runner=runner)

    with pytest.raises(ResearchBackendUnavailableError):
        adapter.read(
            ResearchRequest(SourceKind.RSS, "https://feeds.example.org/feed.xml"),
            cancellation=CancellationTokenSource(),
        )

    assert runner.calls == []


@pytest.mark.parametrize(
    "uri",
    [
        "https://www.youtube.com/playlist?list=PL123",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PL123",
        "https://www.youtube.com/results?search_query=python",
        "https://www.youtube.com/channel/UC123",
        "https://youtu.be/one/two",
        "https://example.com/watch?v=other",
        "https://www.youtube.com/watch",
        "https://www.youtube.com/watch?v=bad%20id",
    ],
)
def test_playlist_channel_search_and_other_url_shapes_are_rejected_before_process(uri: str) -> None:
    runner = FakeRunner()
    adapter = PublicYouTubeAdapter(runner=runner)

    with pytest.raises(ResearchInvalidRequestError):
        ResearchGateway(adapter).read(
            ResearchRequest(SourceKind.YOUTUBE, uri),
            cancellation=CancellationTokenSource(),
        )

    assert runner.calls == []


def test_user_url_stays_one_argv_item_even_with_option_like_query_text() -> None:
    uri = f"https://www.youtube.com/watch?v={VIDEO_ID}&feature=--proxy%20http%3A%2F%2Fevil.invalid"
    runner = FakeRunner(metadata=_metadata())

    _read(runner, request=ResearchRequest(SourceKind.YOUTUBE, uri))

    for argv, _timeout, _limit, _token in runner.calls:
        assert argv[-1] == uri
        assert argv.count(uri) == 1
        assert "--proxy http://evil.invalid" not in argv


@pytest.mark.parametrize(
    "metadata",
    [
        b"not json",
        _metadata(extra_field="ignored", entries=[]),
        _metadata(_type="playlist"),
        _metadata(id="other-id"),
        _metadata(webpage_url="https://example.com/video"),
    ],
)
def test_malformed_or_non_single_video_metadata_is_rejected(metadata: bytes) -> None:
    runner = FakeRunner(metadata=metadata)

    with pytest.raises(ResearchMalformedResultError):
        _read(runner)

    assert len(runner.calls) == 1


def test_missing_captions_returns_safe_upstream_error_without_caption_process() -> None:
    runner = FakeRunner(metadata=_metadata(subtitles={}, automatic_captions={}))

    with pytest.raises(ResearchUpstreamError) as error:
        _read(runner)

    assert error.value.code == "RESEARCH_UPSTREAM_FAILURE"
    assert len(runner.calls) == 1


@pytest.mark.parametrize("raw_caption", [b"", b"not a vtt", b"\xff\xfe"])
def test_empty_malformed_or_invalid_caption_is_rejected(raw_caption: bytes) -> None:
    runner = FakeRunner(caption=raw_caption)

    with pytest.raises(ResearchMalformedResultError):
        _read(runner)

    assert not runner.output_templates[0].parent.exists()


def test_caption_file_size_is_checked_before_read_and_not_truncated() -> None:
    runner = FakeRunner(caption=b"x" * 20)
    request = ResearchRequest(SourceKind.YOUTUBE, VIDEO_URI, max_bytes=10)

    with pytest.raises(ResearchContentTooLargeError):
        _read(runner, request=request)

    assert not runner.output_templates[0].parent.exists()


def test_timeout_is_shared_between_metadata_and_caption_phases() -> None:
    class AdvancingMonotonic:
        def __init__(self) -> None:
            self.values = iter((0.0, 0.0, 0.0, 6.0, 6.0))

        def __call__(self) -> float:
            return next(self.values)

    runner = FakeRunner()
    _read(
        runner,
        request=ResearchRequest(SourceKind.YOUTUBE, VIDEO_URI, timeout_seconds=10),
        monotonic=AdvancingMonotonic(),
    )

    assert runner.calls[0][1] == 10
    assert runner.calls[1][1] == 4


def test_cancellation_between_phases_stops_before_temp_caption_operation() -> None:
    token = CancellationTokenSource()

    class CancellingRunner(FakeRunner):
        def run(
            self,
            argv: Sequence[str],
            *,
            timeout_seconds: float,
            max_stdout_bytes: int,
            cancellation: CancellationToken,
        ) -> ProcessResult:
            result = super().run(
                argv,
                timeout_seconds=timeout_seconds,
                max_stdout_bytes=max_stdout_bytes,
                cancellation=cancellation,
            )
            token.cancel()
            return result

    runner = CancellingRunner()
    adapter = PublicYouTubeAdapter(
        runner=runner,
        clock=lambda: datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        monotonic=lambda: 0.0,
    )

    with pytest.raises(ResearchCancelledError):
        adapter.read(
            ResearchRequest(SourceKind.YOUTUBE, VIDEO_URI),
            cancellation=token,
        )

    assert len(runner.calls) == 1


def test_missing_ytdlp_is_backend_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_executable(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("yt-dlp")

    monkeypatch.setattr(subprocess, "Popen", missing_executable)

    with pytest.raises(ResearchBackendUnavailableError):
        PublicYouTubeAdapter().read(
            ResearchRequest(SourceKind.YOUTUBE, VIDEO_URI),
            cancellation=CancellationTokenSource(),
        )


def test_caption_url_and_stderr_are_not_exposed() -> None:
    runner = FakeRunner(
        caption_result=ProcessResult(
            1,
            b"",
            b"https://youtube.example/caption?sig=secret-token Authorization: secret-token",
        )
    )

    with pytest.raises(ResearchUpstreamError) as error:
        _read(runner)

    assert "secret-token" not in str(error.value)
    assert "caption?sig" not in str(error.value)


def test_symlink_caption_file_is_rejected_and_temp_root_is_cleaned(tmp_path: Path) -> None:
    outside = tmp_path / "outside.vtt"
    outside.write_bytes(CAPTION_VTT)
    symlink_error: OSError | None = None

    class SymlinkRunner(FakeRunner):
        def run(
            self,
            argv: Sequence[str],
            *,
            timeout_seconds: float,
            max_stdout_bytes: int,
            cancellation: CancellationToken,
        ) -> ProcessResult:
            nonlocal symlink_error
            command = tuple(argv)
            self.calls.append((command, timeout_seconds, max_stdout_bytes, cancellation))
            if len(self.calls) == 1:
                return ProcessResult(0, self.metadata, b"")
            template = Path(command[command.index("--output") + 1])
            self.output_templates.append(template)
            try:
                template.parent.joinpath("caption.vtt").symlink_to(outside)
            except OSError as exc:
                symlink_error = exc
            return ProcessResult(0, b"", b"")

    runner = SymlinkRunner()
    if symlink_error is not None:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ResearchMalformedResultError):
        _read(runner)
    if symlink_error is not None:
        pytest.skip("symlink creation is unavailable")
    assert not runner.output_templates[0].parent.exists()
