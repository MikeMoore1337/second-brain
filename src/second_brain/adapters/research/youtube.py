"""Ограниченный public YouTube adapter через внешний ``yt-dlp`` executable."""

from __future__ import annotations

import json
import math
import re
import stat
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import cast
from urllib.parse import parse_qsl, unquote, urlsplit

from second_brain.application.ports import (
    CancellationToken,
    ResearchBackendUnavailableError,
    ResearchCancelledError,
    ResearchContentTooLargeError,
    ResearchInvalidRequestError,
    ResearchMalformedResultError,
    ResearchTimeoutError,
    ResearchUpstreamError,
)
from second_brain.application.research import ResearchRequest, ResearchSource, SourceKind

from .process import (
    BoundedProcessRunner,
    ProcessResult,
    ProcessRunner,
    _ProcessCancelled,
    _ProcessContentTooLarge,
    _ProcessExecutionError,
    _ProcessFailed,
    _ProcessTimedOut,
)

YTDLP_EXECUTABLE = "yt-dlp"
YTDLP_BACKEND = "yt-dlp"
YOUTUBE_MEDIA_TYPE = "text/youtube-transcript"
MAX_METADATA_BYTES = 1_000_000
MAX_METADATA_FIELD_BYTES = 8_192
MAX_CAPTION_STDOUT_BYTES = 64 * 1024
MAX_CAPTION_FILE_BYTES = 5_000_000

_YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }
)
_YOUTU_BE_HOSTS = frozenset({"youtu.be", "www.youtu.be"})
_NO_COOKIE_HOSTS = frozenset({"youtube-nocookie.com", "www.youtube-nocookie.com"})
_VIDEO_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_LANGUAGE_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*\Z")
_CAPTION_FILE_PATTERN = re.compile(r"caption(?:\.[A-Za-z0-9_-]+)?\.vtt\Z", re.IGNORECASE)
_VTT_TIMING_PATTERN = re.compile(
    r"^\s*(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3}\s+-->\s+"
    r"(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3}(?:\s+.*)?\s*\Z"
)
_CREDENTIAL_QUERY_SUFFIXES = (
    "token",
    "secret",
    "password",
    "credential",
    "cookie",
    "session",
    "signature",
    "auth",
)
_CREDENTIAL_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "credentials",
        "jwt",
        "passwd",
        "refresh_token",
        "sig",
        "access_token",
    }
)
_CREDENTIAL_QUERY_KEYS_NORMALIZED = frozenset(
    re.sub(r"[^a-z0-9]", "", item.casefold()) for item in _CREDENTIAL_QUERY_KEYS
)
_IGNORED_CAPTION_TAGS = frozenset({"script", "style"})
_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass(frozen=True, slots=True)
class _YouTubeTarget:
    """Проверенный single-video target без преобразования пользовательского URI."""

    uri: str
    video_id: str


@dataclass(frozen=True, slots=True)
class _CaptionSelection:
    """Одна выбранная public caption track из metadata."""

    language: str
    automatic: bool


@dataclass(frozen=True, slots=True)
class _VideoMetadata:
    """Только bounded metadata, необходимая для normalized source."""

    video_id: str
    title: str | None
    author: str | None
    published_at: datetime | None
    captions: _CaptionSelection


@dataclass(frozen=True, slots=True)
class PublicYouTubeAdapter:
    """Production YouTube port для одного public video и одной caption track."""

    runner: ProcessRunner = field(default_factory=BoundedProcessRunner)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    monotonic: Callable[[], float] = field(default=time.monotonic)

    def read(
        self,
        request: ResearchRequest,
        *,
        cancellation: CancellationToken,
    ) -> ResearchSource:
        """Получить metadata и transcript без media download, write или auth."""

        if not isinstance(request, ResearchRequest):
            raise ResearchInvalidRequestError()
        if request.source_kind is not SourceKind.YOUTUBE:
            raise ResearchBackendUnavailableError()
        if cancellation.is_cancelled():
            raise ResearchCancelledError()
        _validate_adapter_limits(request)
        target = _parse_youtube_target(request.uri)
        deadline = _make_deadline(self.monotonic, request.timeout_seconds)

        metadata_result = _run_process(
            self.runner,
            _build_metadata_argv(target.uri),
            timeout_seconds=_remaining(self.monotonic, deadline),
            max_stdout_bytes=MAX_METADATA_BYTES,
            cancellation=cancellation,
        )
        if cancellation.is_cancelled():
            raise ResearchCancelledError()
        _remaining(self.monotonic, deadline)
        try:
            metadata = _parse_video_metadata(metadata_result.stdout, target)
        except ResearchUpstreamError:
            _remaining(self.monotonic, deadline)
            raise
        if cancellation.is_cancelled():
            raise ResearchCancelledError()

        with tempfile.TemporaryDirectory(prefix="second-brain-youtube-") as temp_directory:
            output_root = Path(temp_directory)
            if _is_link_or_reparse(output_root) or not output_root.is_dir():
                raise ResearchMalformedResultError()
            output_template = str(output_root / "caption.%(ext)s")
            caption_result = _run_process(
                self.runner,
                _build_caption_argv(
                    target.uri,
                    metadata.captions,
                    output_template,
                ),
                timeout_seconds=_remaining(self.monotonic, deadline),
                max_stdout_bytes=MAX_CAPTION_STDOUT_BYTES,
                cancellation=cancellation,
            )
            if cancellation.is_cancelled():
                raise ResearchCancelledError()
            del caption_result
            caption_path = _find_caption_file(output_root)
            caption_bytes = _read_caption_file(caption_path, request.max_bytes)
            content = _normalize_vtt(caption_bytes, request.max_bytes)

        if cancellation.is_cancelled():
            raise ResearchCancelledError()
        _remaining(self.monotonic, deadline)
        try:
            retrieved_at = self.clock()
        except Exception:
            raise ResearchMalformedResultError() from None
        if not _has_explicit_offset(retrieved_at):
            raise ResearchMalformedResultError()
        return ResearchSource(
            uri=request.uri,
            source_kind=SourceKind.YOUTUBE,
            retrieved_at=retrieved_at,
            backend=YTDLP_BACKEND,
            content=content,
            title=metadata.title,
            author=metadata.author,
            media_type=YOUTUBE_MEDIA_TYPE,
            upstream_id=metadata.video_id,
            published_at=metadata.published_at,
        )


def _validate_adapter_limits(request: ResearchRequest) -> None:
    """Не допустить unbounded direct-adapter invocation вне gateway."""

    if type(request.timeout_seconds) is not int or request.timeout_seconds <= 0:
        raise ResearchInvalidRequestError()
    if type(request.max_bytes) is not int or request.max_bytes <= 0:
        raise ResearchInvalidRequestError()


def _make_deadline(monotonic: Callable[[], float], timeout_seconds: int) -> float:
    """Создать общий deadline всего metadata + caption workflow."""

    try:
        started_at = monotonic()
    except Exception:
        raise ResearchTimeoutError() from None
    if isinstance(started_at, bool) or not isinstance(started_at, (int, float)):
        raise ResearchTimeoutError()
    return float(started_at) + timeout_seconds


def _remaining(monotonic: Callable[[], float], deadline: float) -> float:
    """Вернуть остаток общего deadline или безопасно сообщить timeout."""

    try:
        current = monotonic()
    except Exception:
        raise ResearchTimeoutError() from None
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        raise ResearchTimeoutError()
    remaining = deadline - float(current)
    if remaining <= 0:
        raise ResearchTimeoutError()
    return remaining


def _run_process(
    runner: ProcessRunner,
    argv: Sequence[str],
    *,
    timeout_seconds: float,
    max_stdout_bytes: int,
    cancellation: CancellationToken,
) -> ProcessResult:
    """Запустить только заранее собранный argv и скрыть process diagnostics."""

    try:
        result = runner.run(
            argv,
            timeout_seconds=timeout_seconds,
            max_stdout_bytes=max_stdout_bytes,
            cancellation=cancellation,
        )
    except _ProcessCancelled:
        raise ResearchCancelledError() from None
    except _ProcessTimedOut:
        raise ResearchTimeoutError() from None
    except _ProcessContentTooLarge:
        raise ResearchContentTooLargeError() from None
    except _ProcessFailed:
        raise ResearchUpstreamError() from None
    except _ProcessExecutionError:
        raise ResearchBackendUnavailableError() from None
    except OSError:
        raise ResearchBackendUnavailableError() from None

    if not isinstance(result, ProcessResult):
        raise ResearchMalformedResultError()
    if result.returncode != 0:
        raise ResearchUpstreamError()
    if not isinstance(result.stdout, bytes):
        raise ResearchMalformedResultError()
    if len(result.stdout) > max_stdout_bytes:
        raise ResearchContentTooLargeError()
    return result


def _parse_youtube_target(uri: object) -> _YouTubeTarget:
    """Принять только allowlisted URL shape для одного video."""

    if not isinstance(uri, str) or not uri or _contains_control_or_space(uri):
        raise ResearchInvalidRequestError()
    try:
        parsed = urlsplit(uri)
        scheme = parsed.scheme.casefold()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ResearchInvalidRequestError() from None
    if scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ResearchInvalidRequestError()
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ResearchInvalidRequestError()
    if port is not None:
        raise ResearchInvalidRequestError()
    normalized_host = hostname.rstrip(".").casefold()
    if normalized_host not in _YOUTUBE_HOSTS:
        raise ResearchInvalidRequestError()
    if _contains_credentials(parsed.query) or _contains_credentials(parsed.fragment):
        raise ResearchInvalidRequestError()
    query = _parse_query(parsed.query)

    if normalized_host in _YOUTU_BE_HOSTS:
        video_id = _single_path_segment(parsed.path)
    elif parsed.path.casefold() == "/watch":
        if _has_playlist_parameters(query):
            raise ResearchInvalidRequestError()
        values = [value for key, value in query if key.casefold() == "v"]
        if len(values) != 1:
            raise ResearchInvalidRequestError()
        video_id = values[0]
    elif normalized_host in _NO_COOKIE_HOSTS and parsed.path.casefold().startswith("/embed/"):
        video_id = _single_path_segment(parsed.path[len("/embed/") :])
    else:
        raise ResearchInvalidRequestError()

    if _has_playlist_parameters(query) or not _valid_video_id(video_id):
        raise ResearchInvalidRequestError()
    return _YouTubeTarget(uri=uri, video_id=video_id)


def _parse_query(query: str) -> tuple[tuple[str, str], ...]:
    """Разобрать query без вывода или передачи отдельных query arguments."""

    try:
        return tuple(parse_qsl(query, keep_blank_values=True, strict_parsing=False))
    except ValueError:
        raise ResearchInvalidRequestError() from None


def _single_path_segment(path: str) -> str:
    """Извлечь ровно один bounded video id из short/embed path."""

    value = path.strip("/")
    if not value or "/" in value:
        raise ResearchInvalidRequestError()
    try:
        return unquote(value)
    except ValueError:
        raise ResearchInvalidRequestError() from None


def _valid_video_id(value: str) -> bool:
    """Проверить ID без превращения его в option или path."""

    return _VIDEO_ID_PATTERN.fullmatch(value) is not None


def _has_playlist_parameters(query: Sequence[tuple[str, str]]) -> bool:
    """Отсечь playlist/container hints даже при наличии video id."""

    return any(key.casefold() in {"list", "index", "playlist"} for key, _value in query)


def _contains_credentials(value: str) -> bool:
    """Отсечь credential-like query/fragment keys без публикации их values."""

    try:
        pairs = parse_qsl(value, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return True
    for key, _value in pairs:
        normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
        if normalized in _CREDENTIAL_QUERY_KEYS_NORMALIZED or normalized.endswith(
            _CREDENTIAL_QUERY_SUFFIXES
        ):
            return True
    return False


def _parse_video_metadata(raw_bytes: bytes, target: _YouTubeTarget) -> _VideoMetadata:
    """Проверить bounded JSON metadata и выбрать одну caption track."""

    if not isinstance(raw_bytes, bytes):
        raise ResearchMalformedResultError()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError:
        raise ResearchMalformedResultError() from None
    if not isinstance(payload, dict):
        raise ResearchMalformedResultError()
    metadata = cast(Mapping[str, object], payload)
    entry_type = metadata.get("_type")
    if entry_type is not None and entry_type != "video":
        raise ResearchMalformedResultError()
    if "entries" in metadata:
        raise ResearchMalformedResultError()

    video_id = metadata.get("id")
    if not isinstance(video_id, str) or not _valid_video_id(video_id):
        raise ResearchMalformedResultError()
    if video_id != target.video_id:
        raise ResearchMalformedResultError()
    webpage_url = metadata.get("webpage_url")
    if not isinstance(webpage_url, str):
        raise ResearchMalformedResultError()
    try:
        canonical_target = _parse_youtube_target(webpage_url)
    except ResearchInvalidRequestError:
        raise ResearchMalformedResultError() from None
    if canonical_target.video_id != video_id:
        raise ResearchMalformedResultError()

    title = _metadata_text(metadata.get("title"))
    channel = _metadata_text(metadata.get("channel"))
    uploader = _metadata_text(metadata.get("uploader"))
    selection = _select_caption_track(metadata)
    if selection is None:
        raise ResearchUpstreamError()
    return _VideoMetadata(
        video_id=video_id,
        title=title,
        author=channel or uploader,
        published_at=_metadata_timestamp(metadata),
        captions=selection,
    )


def _metadata_text(value: object) -> str | None:
    """Принять bounded metadata text без control chars или raw external details."""

    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if "\x00" in value or any(ord(char) == 127 for char in value):
        raise ResearchMalformedResultError()
    normalized = " ".join(value.split())
    if not normalized:
        return None
    try:
        if len(normalized.encode("utf-8")) > MAX_METADATA_FIELD_BYTES:
            raise ResearchContentTooLargeError()
    except UnicodeEncodeError:
        raise ResearchMalformedResultError() from None
    return normalized


def _metadata_timestamp(metadata: Mapping[str, object]) -> datetime | None:
    """Использовать только numeric epoch, который можно однозначно перевести в UTC."""

    for key in ("release_timestamp", "timestamp"):
        value = metadata.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except OverflowError, OSError, TypeError, ValueError:
            continue
    return None


def _select_caption_track(metadata: Mapping[str, object]) -> _CaptionSelection | None:
    """Выбрать manual раньше automatic и применить ru -> en -> fallback policy."""

    manual_languages = _caption_languages(metadata.get("subtitles"))
    if manual_languages:
        return _CaptionSelection(_choose_language(manual_languages), automatic=False)
    automatic_languages = _caption_languages(metadata.get("automatic_captions"))
    if automatic_languages:
        return _CaptionSelection(_choose_language(automatic_languages), automatic=True)
    return None


def _caption_languages(value: object) -> tuple[str, ...]:
    """Оставить только bounded language keys с одной или несколькими formats."""

    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ResearchMalformedResultError()
    languages: list[str] = []
    for raw_language, formats in value.items():
        if not isinstance(raw_language, str):
            continue
        if _is_live_caption(raw_language) or _LANGUAGE_PATTERN.fullmatch(raw_language) is None:
            continue
        if _has_caption_formats(formats):
            languages.append(raw_language)
    return tuple(languages)


def _has_caption_formats(value: object) -> bool:
    """Проверить только форму metadata, не читать или публиковать caption URLs."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return False
    return any(isinstance(item, Mapping) for item in value)


def _is_live_caption(language: str) -> bool:
    """Исключить live_chat и близкие chat track identifiers."""

    normalized = re.sub(r"[-\s]+", "_", language.casefold())
    return "live_chat" in normalized or "livechat" in normalized or normalized == "chat"


def _choose_language(languages: Sequence[str]) -> str:
    """Детерминированно применить exact/prefix ru, exact/prefix en, lexical fallback."""

    def rank(language: str) -> tuple[int, str, str]:
        normalized = language.casefold()
        if normalized == "ru":
            priority = 0
        elif normalized.startswith(("ru-", "ru_")):
            priority = 1
        elif normalized == "en":
            priority = 2
        elif normalized.startswith(("en-", "en_")):
            priority = 3
        else:
            priority = 4
        return priority, normalized, language

    return min(languages, key=rank)


def _build_metadata_argv(source_uri: str) -> tuple[str, ...]:
    """Собрать closed argv для bounded single-video JSON metadata."""

    return (
        YTDLP_EXECUTABLE,
        "--ignore-config",
        "--no-cache-dir",
        "--no-playlist",
        "--retries",
        "0",
        "--fragment-retries",
        "0",
        "--extractor-retries",
        "0",
        "--file-access-retries",
        "0",
        "--quiet",
        "--no-warnings",
        "--skip-download",
        "--dump-single-json",
        "--",
        source_uri,
    )


def _build_caption_argv(
    source_uri: str,
    selection: _CaptionSelection,
    output_template: str,
) -> tuple[str, ...]:
    """Собрать closed argv для одной manual или automatic VTT track."""

    subtitle_flag = "--write-auto-subs" if selection.automatic else "--write-subs"
    return (
        YTDLP_EXECUTABLE,
        "--ignore-config",
        "--no-cache-dir",
        "--no-playlist",
        "--retries",
        "0",
        "--fragment-retries",
        "0",
        "--extractor-retries",
        "0",
        "--file-access-retries",
        "0",
        "--quiet",
        "--no-warnings",
        "--skip-download",
        subtitle_flag,
        "--sub-langs",
        selection.language,
        "--sub-format",
        "vtt",
        "--output",
        output_template,
        "--",
        source_uri,
    )


def _find_caption_file(root: Path) -> Path:
    """Разрешить ровно один обычный VTT file внутри private temp root."""

    if _is_link_or_reparse(root) or not root.is_dir():
        raise ResearchMalformedResultError()
    try:
        resolved_root = root.resolve(strict=True)
        entries = tuple(root.iterdir())
    except OSError, RuntimeError:
        raise ResearchMalformedResultError() from None

    candidates: list[Path] = []
    for entry in entries:
        if _is_link_or_reparse(entry):
            raise ResearchMalformedResultError()
        try:
            resolved_entry = entry.resolve(strict=True)
            resolved_entry.relative_to(resolved_root)
            is_file = entry.is_file()
        except OSError, RuntimeError, ValueError:
            raise ResearchMalformedResultError() from None
        if not is_file:
            raise ResearchMalformedResultError()
        if _CAPTION_FILE_PATTERN.fullmatch(entry.name) is None:
            raise ResearchMalformedResultError()
        candidates.append(entry)
    if len(candidates) != 1:
        raise ResearchMalformedResultError()
    return candidates[0]


def _is_link_or_reparse(path: Path) -> bool:
    """Распознать symlink/junction/reparse entry до resolve-following."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        file_attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(file_attributes & _REPARSE_POINT_ATTRIBUTE)
    except OSError, RuntimeError:
        return True


def _read_caption_file(path: Path, max_bytes: int) -> bytes:
    """Проверить размер до чтения и повторить bounded check после чтения."""

    limit = min(max_bytes, MAX_CAPTION_FILE_BYTES)
    try:
        size = path.stat().st_size
    except OSError:
        raise ResearchMalformedResultError() from None
    if size > limit:
        raise ResearchContentTooLargeError()
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        raise ResearchMalformedResultError() from None
    if len(raw_bytes) > limit:
        raise ResearchContentTooLargeError()
    return raw_bytes


def _normalize_vtt(raw_bytes: bytes, max_bytes: int) -> str:
    """Преобразовать bounded VTT в plain text без исполнения markup."""

    try:
        text = raw_bytes.decode("utf-8-sig")
    except AttributeError, UnicodeDecodeError:
        raise ResearchMalformedResultError() from None
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[str] = []
    current_lines: list[str] = []
    in_cue = False
    skip_block = False
    saw_timing = False

    for line in lines:
        stripped = line.strip()
        if not in_cue:
            if not stripped:
                skip_block = False
                continue
            if stripped.startswith("WEBVTT"):
                continue
            if stripped == "NOTE" or stripped.startswith("NOTE "):
                skip_block = True
                continue
            if stripped in {"STYLE", "REGION"}:
                skip_block = True
                continue
            if skip_block:
                continue
            if _VTT_TIMING_PATTERN.fullmatch(stripped):
                saw_timing = True
                in_cue = True
                current_lines = []
            continue

        if _VTT_TIMING_PATTERN.fullmatch(stripped):
            _append_cue(cues, _normalize_cue_lines(current_lines))
            current_lines = []
            saw_timing = True
        elif not stripped:
            _append_cue(cues, _normalize_cue_lines(current_lines))
            current_lines = []
            in_cue = False
        else:
            current_lines.append(stripped)
    if in_cue:
        _append_cue(cues, _normalize_cue_lines(current_lines))

    if not saw_timing or not cues:
        raise ResearchMalformedResultError()
    content = "\n".join(cues).strip()
    if not content:
        raise ResearchMalformedResultError()
    try:
        if len(content.encode("utf-8")) > max_bytes:
            raise ResearchContentTooLargeError()
    except UnicodeEncodeError:
        raise ResearchMalformedResultError() from None
    return content


def _normalize_cue_lines(lines: Sequence[str]) -> str:
    """Убрать cue markup и сделать одну bounded plain-text строку."""

    if not lines:
        return ""
    parser = _CaptionMarkupParser()
    try:
        parser.feed(" ".join(lines))
        parser.close()
    except AssertionError, ValueError:
        raise ResearchMalformedResultError() from None
    return _clean_caption_text(parser.text())


def _clean_caption_text(value: str) -> str:
    """Удалить controls и нормализовать whitespace, не интерпретируя content."""

    filtered = "".join(
        char for char in value if char in {"\n", "\t"} or (ord(char) >= 32 and ord(char) != 127)
    )
    return " ".join(filtered.split())


def _append_cue(cues: list[str], cue: str) -> None:
    """Детерминированно убрать только exact/word-overlap соседних cue fragments."""

    if not cue:
        return
    if not cues:
        cues.append(cue)
        return
    previous_tokens = cues[-1].split()[-64:]
    current_tokens = cue.split()
    if current_tokens == previous_tokens[-len(current_tokens) :]:
        return
    max_overlap = min(len(previous_tokens), len(current_tokens), 64)
    overlap = 0
    for size in range(max_overlap, 0, -1):
        if previous_tokens[-size:] == current_tokens[:size]:
            overlap = size
            break
    if overlap:
        remaining = current_tokens[overlap:]
        if remaining:
            cues.append(" ".join(remaining))
        return
    cues.append(cue)


class _CaptionMarkupParser(HTMLParser):
    """HTMLParser-only markup removal with script/style content ignored."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.casefold() in _IGNORED_CAPTION_TAGS:
            self._ignored_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag, attrs

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in _IGNORED_CAPTION_TAGS and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        """Вернуть только text nodes без network/side effects."""

        return "".join(self._parts)


def _has_explicit_offset(value: object) -> bool:
    """Принять только aware datetime с вычислимым UTC offset."""

    if not isinstance(value, datetime) or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() is not None
    except TypeError, ValueError, OverflowError:
        return False


def _contains_control_or_space(value: str) -> bool:
    """Отклонить whitespace/control в исходном URI до argv construction."""

    return any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)


__all__ = [
    "MAX_CAPTION_FILE_BYTES",
    "MAX_CAPTION_STDOUT_BYTES",
    "MAX_METADATA_BYTES",
    "MAX_METADATA_FIELD_BYTES",
    "YOUTUBE_MEDIA_TYPE",
    "YTDLP_BACKEND",
    "YTDLP_EXECUTABLE",
    "PublicYouTubeAdapter",
]
