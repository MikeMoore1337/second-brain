"""Детерминированные проверки public RSS/Atom adapter без live network/DNS."""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, datetime
from multiprocessing.connection import Connection
from threading import Event, Thread

import feedparser  # type: ignore[import-untyped]
import pytest

from second_brain.adapters.research.process import ProcessResult
from second_brain.adapters.research.rss import PublicRssAdapter, _normalize_feed
from second_brain.adapters.research.rss_worker import (
    MultiprocessingParserWorker,
    NormalizedFeed,
    ParserWorker,
)
from second_brain.application.ports import (
    CancellationToken,
    CancellationTokenSource,
    ResearchBackendUnavailableError,
    ResearchCancelledError,
    ResearchContentTooLargeError,
    ResearchInvalidRequestError,
    ResearchMalformedResultError,
    ResearchTimeoutError,
    ResearchUpstreamError,
)
from second_brain.application.research import (
    ResearchGateway,
    ResearchRequest,
    ResearchSource,
    SourceKind,
)

RSS_20 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Русские новости</title>
    <link>https://feeds.example.org/</link>
    <description>Публичная лента</description>
    <managingEditor>Редакция</managingEditor>
    <item>
      <title>Первая запись</title>
      <link>https://items.example.org/one</link>
      <pubDate>Thu, 03 Sep 2026 10:00:00 +0300</pubDate>
      <description><![CDATA[<p>Короткий <strong>текст</strong>.</p>
        <script>alert('не выполнять'); fetch('https://evil.example/')</script>
        <a href="https://evil.example/resource">Безопасная ссылка</a>]]></description>
      <enclosure url="https://evil.example/media.mp3" type="audio/mpeg" />
    </item>
    <item>
      <title>Вторая запись</title>
      <description>Ещё один абзац.</description>
    </item>
  </channel>
</rss>
""".encode()

ATOM_10 = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom лента</title>
  <author><name>Автор канала</name></author>
  <updated>2026-09-03T07:00:00Z</updated>
  <entry>
    <title>Атомная запись</title>
    <link href="https://items.example.org/atom" />
    <updated>2026-09-03T08:00:00Z</updated>
    <content type="html"><![CDATA[<p>Тело <em>Atom</em>.</p>
      <style>body{display:none}</style>]]></content>
  </entry>
</feed>
""".encode()


class FakeResolver:
    """Resolver fixture, который не обращается к системе или сети."""

    def __init__(self, addresses: Sequence[str] | Exception) -> None:
        self.addresses = addresses
        self.calls: list[tuple[str, int]] = []

    def resolve(self, hostname: str, port: int) -> Sequence[str]:
        self.calls.append((hostname, port))
        if isinstance(self.addresses, Exception):
            raise self.addresses
        return self.addresses


class FakeRunner:
    """Process fixture, записывающий закрытый argv без запуска subprocess."""

    def __init__(self, result: ProcessResult) -> None:
        self.result = result
        self.calls: list[tuple[tuple[str, ...], float, int, CancellationToken]] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        max_stdout_bytes: int,
        cancellation: CancellationToken,
    ) -> ProcessResult:
        self.calls.append((tuple(argv), timeout_seconds, max_stdout_bytes, cancellation))
        return self.result


class FakeParserWorker:
    """Deterministic parser seam для unit tests без child-process overhead."""

    def __init__(self) -> None:
        self.inputs: list[bytes] = []

    def run(
        self,
        raw_bytes: bytes,
        *,
        max_bytes: int,
        deadline: float,
        cancellation: CancellationToken,
    ) -> NormalizedFeed:
        del deadline, cancellation
        self.inputs.append(raw_bytes)
        return _normalize_feed(feedparser.parse(raw_bytes), max_bytes)


def make_adapter(
    raw: bytes = RSS_20,
    *,
    addresses: Sequence[str] | Exception = ("93.184.216.34", "8.8.8.8"),
    parser_worker: ParserWorker | None = None,
) -> tuple[PublicRssAdapter, FakeResolver, FakeRunner]:
    resolver = FakeResolver(addresses)
    runner = FakeRunner(ProcessResult(0, raw, b"diagnostic secret"))
    return (
        PublicRssAdapter(
            resolver=resolver,
            runner=runner,
            parser_worker=parser_worker or FakeParserWorker(),
            clock=lambda: datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        ),
        resolver,
        runner,
    )


def read(
    adapter: PublicRssAdapter,
    request: ResearchRequest | None = None,
) -> ResearchSource:
    """Вызвать production boundary с deterministic cancellation token."""

    return ResearchGateway(adapter).read(
        request or ResearchRequest(SourceKind.RSS, "https://feeds.example.org/feed.xml?tab=1"),
        cancellation=CancellationTokenSource(),
    )


def test_rss_request_resolves_validates_pins_and_parses_bounded_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, resolver, runner = make_adapter()
    parser_inputs: list[object] = []
    original_parse = feedparser.parse

    def parse(raw: object) -> object:
        parser_inputs.append(raw)
        return original_parse(raw)

    monkeypatch.setattr(feedparser, "parse", parse)

    source = read(adapter)

    assert resolver.calls == [("feeds.example.org", 443)]
    argv, timeout, max_bytes, _ = runner.calls[0]
    assert argv[0] == "curl"
    assert argv[1] == "--disable"
    assert argv[argv.index("--proto") + 1] == "=https"
    assert argv[argv.index("--resolve") + 1] == "feeds.example.org:443:8.8.8.8"
    assert argv[argv.index("--url") + 1] == "https://feeds.example.org/feed.xml?tab=1"
    assert argv.count("https://feeds.example.org/feed.xml?tab=1") == 1
    assert "--location" not in argv
    assert "--insecure" not in argv
    assert "--cookie" not in argv
    assert "--netrc" not in argv
    assert "--user" not in argv
    assert "--proxy" not in argv
    assert timeout <= 30
    assert max_bytes == 5_000_000
    assert parser_inputs == [RSS_20]
    assert source.uri == "https://feeds.example.org/feed.xml?tab=1"
    assert source.source_kind is SourceKind.RSS
    assert source.backend == "feedparser"
    assert source.media_type == "application/rss+xml"
    assert source.title == "Русские новости"
    assert source.author == "Редакция"
    assert "# Русские новости" in source.content
    assert "## Первая запись" in source.content
    assert "## Вторая запись" in source.content
    assert "Published: 2026-09-03T07:00:00+00:00" in source.content
    assert "https://items.example.org/one" in source.content
    assert "Безопасная ссылка" in source.content
    assert "alert" not in source.content
    assert "fetch(" not in source.content
    assert "<script" not in source.content
    assert "<p>" not in source.content
    assert "evil.example/media.mp3" not in source.content


def test_atom_request_normalizes_author_html_and_structured_date() -> None:
    adapter, resolver, runner = make_adapter(ATOM_10, addresses=("1.1.1.1",))

    source = read(
        adapter,
        ResearchRequest(SourceKind.RSS, "http://feeds.example.org:8080/atom.xml", max_bytes=4096),
    )

    assert resolver.calls == [("feeds.example.org", 8080)]
    argv = runner.calls[0][0]
    assert argv[argv.index("--proto") + 1] == "=http"
    assert argv[argv.index("--resolve") + 1] == "feeds.example.org:8080:1.1.1.1"
    assert source.media_type == "application/atom+xml"
    assert source.title == "Atom лента"
    assert source.author == "Автор канала"
    assert "## Атомная запись" in source.content
    assert "Published: 2026-09-03T08:00:00+00:00" in source.content
    assert "Тело Atom." in source.content
    assert "display:none" not in source.content


def test_non_rss_source_kind_fails_closed_without_dns_or_process() -> None:
    adapter, resolver, runner = make_adapter()

    with pytest.raises(ResearchBackendUnavailableError):
        adapter.read(
            ResearchRequest(SourceKind.WEB, "https://feeds.example.org/article"),
            cancellation=CancellationTokenSource(),
        )

    assert resolver.calls == []
    assert runner.calls == []


@pytest.mark.parametrize(
    "addresses",
    [
        ("127.0.0.1",),
        ("192.168.1.10",),
        ("169.254.1.10",),
        ("224.0.0.1",),
        ("0.0.0.0",),
        ("203.0.113.10",),
        ("::ffff:8.8.8.8",),
        ("fe80::1%12",),
        ("not-an-ip",),
    ],
)
def test_unsafe_dns_candidate_is_rejected_before_curl(addresses: tuple[str, ...]) -> None:
    adapter, resolver, runner = make_adapter(addresses=addresses)

    with pytest.raises(ResearchInvalidRequestError):
        read(adapter)

    assert resolver.calls == [("feeds.example.org", 443)]
    assert runner.calls == []


def test_mixed_public_and_private_dns_result_is_rejected_entirely() -> None:
    adapter, _resolver, runner = make_adapter(addresses=("8.8.8.8", "10.0.0.1"))

    with pytest.raises(ResearchInvalidRequestError):
        read(adapter)

    assert runner.calls == []


@pytest.mark.parametrize(
    ("addresses", "expected_error"),
    [
        ((), ResearchBackendUnavailableError),
        (OSError("resolver secret"), ResearchBackendUnavailableError),
    ],
)
def test_empty_or_failed_dns_is_safe_error(
    addresses: Sequence[str] | Exception,
    expected_error: type[Exception],
) -> None:
    adapter, _resolver, runner = make_adapter(addresses=addresses)

    with pytest.raises(expected_error):
        read(adapter)

    assert runner.calls == []


def test_literal_public_ip_is_validated_without_dns_or_resolve() -> None:
    adapter, resolver, runner = make_adapter(addresses=OSError("must not run"))

    source = read(adapter, ResearchRequest(SourceKind.RSS, "https://93.184.216.34/feed.xml"))

    assert source.uri == "https://93.184.216.34/feed.xml"
    assert resolver.calls == []
    argv = runner.calls[0][0]
    assert "--resolve" not in argv
    assert argv[argv.index("--url") + 1] == "https://93.184.216.34/feed.xml"


def test_literal_public_ipv6_is_validated_without_dns_or_resolve() -> None:
    adapter, resolver, runner = make_adapter(addresses=OSError("must not run"))
    uri = "https://[2001:4860:4860::8888]/feed.xml"

    source = read(adapter, ResearchRequest(SourceKind.RSS, uri))

    assert source.uri == uri
    assert resolver.calls == []
    argv = runner.calls[0][0]
    assert "--resolve" not in argv
    assert argv[argv.index("--proto") + 1] == "=https"
    assert argv[argv.index("--url") + 1] == uri


def test_trailing_dot_hostname_is_used_for_resolve_pinning() -> None:
    adapter, resolver, runner = make_adapter(addresses=("8.8.8.8",))

    read(adapter, ResearchRequest(SourceKind.RSS, "https://feeds.example.org./feed.xml"))

    assert resolver.calls == [("feeds.example.org", 443)]
    argv = runner.calls[0][0]
    assert argv[argv.index("--resolve") + 1] == "feeds.example.org.:443:8.8.8.8"


def test_dns_timeout_is_mapped_before_curl() -> None:
    release = Event()

    class SlowResolver:
        def resolve(self, hostname: str, port: int) -> Sequence[str]:
            del hostname, port
            release.wait(5)
            return ("8.8.8.8",)

    _adapter, _resolver, runner = make_adapter()
    adapter = PublicRssAdapter(resolver=SlowResolver(), runner=runner)
    try:
        with pytest.raises(ResearchTimeoutError):
            read(
                adapter,
                ResearchRequest(
                    SourceKind.RSS,
                    "https://feeds.example.org/feed.xml",
                    timeout_seconds=1,
                ),
            )
    finally:
        release.set()

    assert runner.calls == []


def test_raw_overflow_is_rejected_before_feedparser(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _resolver, runner = make_adapter(raw=b"x" * 5)
    request = ResearchRequest(SourceKind.RSS, "https://feeds.example.org/feed.xml", max_bytes=4)
    parse_called = False

    def parse(_raw: object) -> object:
        nonlocal parse_called
        parse_called = True
        raise AssertionError("feedparser must not receive oversized raw feed")

    monkeypatch.setattr(feedparser, "parse", parse)

    with pytest.raises(ResearchContentTooLargeError):
        read(adapter, request)

    assert not parse_called
    assert len(runner.calls) == 1


def test_normalized_content_overflow_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _resolver, runner = make_adapter(raw=b"ok")
    request = ResearchRequest(SourceKind.RSS, "https://feeds.example.org/feed.xml", max_bytes=2)

    monkeypatch.setattr(
        feedparser,
        "parse",
        lambda _raw: {
            "bozo": False,
            "version": "rss20",
            "feed": {"title": "Заголовок"},
            "entries": [],
        },
    )

    with pytest.raises(ResearchContentTooLargeError):
        read(adapter, request)

    assert len(runner.calls) == 1


def test_real_parser_worker_preserves_rss_and_atom_regression() -> None:
    for raw, expected_media_type in (
        (RSS_20, "application/rss+xml"),
        (ATOM_10, "application/atom+xml"),
    ):
        adapter, _resolver, _runner = make_adapter(
            raw,
            addresses=("8.8.8.8",),
            parser_worker=MultiprocessingParserWorker(),
        )

        source = read(adapter)

        assert source.media_type == expected_media_type
        assert source.content


def _slow_parser_worker(
    _send_conn: Connection,
    _raw_bytes: bytes,
    _max_bytes: int,
) -> None:
    """Deterministic stuck parser fixture; parent must terminate this child."""

    time.sleep(30)


def _secret_failing_parser_worker(
    _send_conn: Connection,
    _raw_bytes: bytes,
    _max_bytes: int,
) -> None:
    """Fixture for verifying that child failure details never cross the boundary."""

    raise RuntimeError("parser secret should stay in the worker")


def test_slow_parser_timeout_terminates_worker() -> None:
    worker = MultiprocessingParserWorker(target=_slow_parser_worker)
    adapter, _resolver, _runner = make_adapter(parser_worker=worker)

    with pytest.raises(ResearchTimeoutError):
        read(
            adapter,
            ResearchRequest(
                SourceKind.RSS,
                "https://feeds.example.org/feed.xml",
                timeout_seconds=1,
            ),
        )

    assert worker.last_process is not None
    assert not worker.last_process.is_alive()


def test_parser_cancellation_terminates_worker() -> None:
    worker = MultiprocessingParserWorker(target=_slow_parser_worker)
    adapter, _resolver, _runner = make_adapter(parser_worker=worker)
    cancellation = CancellationTokenSource()

    def cancel_after_worker_starts() -> None:
        assert worker.started.wait(5)
        cancellation.cancel()

    cancel_thread = Thread(target=cancel_after_worker_starts)
    cancel_thread.start()
    try:
        with pytest.raises(ResearchCancelledError):
            adapter.read(
                ResearchRequest(SourceKind.RSS, "https://feeds.example.org/feed.xml"),
                cancellation=cancellation,
            )
    finally:
        cancel_thread.join()

    assert worker.last_process is not None
    assert not worker.last_process.is_alive()


def test_parser_worker_hides_child_exception_details() -> None:
    worker = MultiprocessingParserWorker(target=_secret_failing_parser_worker)
    adapter, _resolver, _runner = make_adapter(parser_worker=worker)

    with pytest.raises(ResearchMalformedResultError) as error:
        read(adapter)

    assert "parser secret" not in str(error.value)
    assert worker.last_process is not None
    assert not worker.last_process.is_alive()


@pytest.mark.parametrize("raw", [b"not xml", b"\xff\xfe\x00"])
def test_malformed_or_invalid_feed_is_rejected(raw: bytes) -> None:
    adapter, _resolver, runner = make_adapter(raw=raw)

    with pytest.raises(ResearchMalformedResultError):
        read(adapter)

    assert len(runner.calls) == 1


def test_nonzero_curl_does_not_expose_stderr() -> None:
    adapter, _resolver, _runner = make_adapter()
    adapter = PublicRssAdapter(
        resolver=FakeResolver(("8.8.8.8",)),
        runner=FakeRunner(ProcessResult(22, RSS_20, b"Authorization: secret-token")),
        clock=lambda: datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
    )

    with pytest.raises(ResearchUpstreamError) as error:
        read(adapter)

    assert "secret-token" not in str(error.value)
    assert "Authorization" not in str(error.value)


def test_cancelled_request_does_not_resolve_or_launch_process() -> None:
    adapter, resolver, runner = make_adapter()
    token = CancellationTokenSource()
    token.cancel()

    with pytest.raises(ResearchCancelledError):
        adapter.read(
            ResearchRequest(SourceKind.RSS, "https://feeds.example.org/feed.xml"),
            cancellation=token,
        )

    assert resolver.calls == []
    assert runner.calls == []


@pytest.mark.parametrize(
    ("runner_error", "expected_error"),
    [
        (ResearchTimeoutError(), ResearchTimeoutError),
        (ResearchCancelledError(), ResearchCancelledError),
        (ResearchContentTooLargeError(), ResearchContentTooLargeError),
        (OSError("curl secret"), ResearchBackendUnavailableError),
    ],
)
def test_runner_errors_map_without_upstream_details(
    runner_error: Exception,
    expected_error: type[Exception],
) -> None:
    class ErrorRunner:
        def run(
            self,
            argv: Sequence[str],
            *,
            timeout_seconds: float,
            max_stdout_bytes: int,
            cancellation: CancellationToken,
        ) -> ProcessResult:
            del argv, timeout_seconds, max_stdout_bytes, cancellation
            raise runner_error

    adapter = PublicRssAdapter(
        resolver=FakeResolver(("8.8.8.8",)),
        runner=ErrorRunner(),
    )

    with pytest.raises(expected_error) as error:
        read(adapter)

    assert "secret" not in str(error.value)
