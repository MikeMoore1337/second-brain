"""Deterministic Web raw-audio boundary and static Voice UI tests."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message, Scope

import second_brain.entrypoints.web.app as web_app
from second_brain.application.ports import (
    TranscriptionBackendUnavailableError,
    TranscriptionTimeoutError,
    TranscriptionUpstreamError,
)
from second_brain.application.transcription import Transcript
from second_brain.entrypoints.web.app import (
    MAX_RAW_TRANSCRIPTION_BODY_BYTES,
    TRANSCRIPTION_REQUEST_HEADER_NAME,
    TRANSCRIPTION_REQUEST_HEADER_VALUE,
    create_app,
)

LOOPBACK_BASE_URL = "http://127.0.0.1"
VOICE_HEADERS = {
    TRANSCRIPTION_REQUEST_HEADER_NAME: TRANSCRIPTION_REQUEST_HEADER_VALUE,
    "content-type": "audio/wav",
}


class RecordingTranscriptionService:
    """DI fake для проверки raw audio boundary без Cloudflare."""

    def __init__(self, result: Transcript | None = None, error: Exception | None = None) -> None:
        self.result = result if result is not None else Transcript("Распознанный русский текст")
        self.error = error
        self.calls: list[tuple[bytes, str]] = []
        self.thread_ids: list[int] = []

    def transcribe(self, audio: bytes, media_type: str) -> Transcript:
        self.thread_ids.append(threading.get_ident())
        self.calls.append((audio, media_type))
        if self.error is not None:
            raise self.error
        return self.result


def send_raw_asgi_request(
    application: ASGIApp,
    *,
    path: str,
    headers: dict[str, str],
    body_chunks: tuple[bytes, ...],
) -> tuple[int, dict[str, str], bytes, int]:
    """Послать контролируемые ASGI chunks без httpx body buffering."""

    encoded_headers = [
        (name.lower().encode("ascii"), value.encode("latin-1")) for name, value in headers.items()
    ]
    messages: list[Message] = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(body_chunks) - 1,
        }
        for index, chunk in enumerate(body_chunks)
    ]
    receive_calls = 0
    sent_messages: list[Message] = []

    async def receive() -> Message:
        nonlocal receive_calls
        receive_calls += 1
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent_messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": encoded_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }

    async def run() -> None:
        await application(scope, receive, send)

    asyncio.run(run())
    response_start = next(
        message for message in sent_messages if message["type"] == "http.response.start"
    )
    response_headers = {
        name.decode("latin-1").lower(): value.decode("latin-1")
        for name, value in response_start["headers"]
    }
    response_body = b"".join(
        message.get("body", b"")
        for message in sent_messages
        if message["type"] == "http.response.body"
    )
    return int(response_start["status"]), response_headers, response_body, receive_calls


def test_audio_endpoint_returns_only_transcript_and_never_calls_draft_flow() -> None:
    service = RecordingTranscriptionService()
    audio = b"\x00\x01exact raw audio"

    with TestClient(
        create_app(transcription_service=service), base_url=LOOPBACK_BASE_URL
    ) as client:
        response = client.post("/api/transcriptions/audio", content=audio, headers=VOICE_HEADERS)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
    assert response.json() == {"transcript": {"text": "Распознанный русский текст"}}
    assert service.calls == [(audio, "audio/wav")]


def test_sync_transcription_is_dispatched_through_threadpool_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecordingTranscriptionService()
    event_loop_thread_ids: list[int] = []
    threadpool_calls = 0

    async def recording_run_in_threadpool(function: object, *args: object) -> object:
        nonlocal threadpool_calls
        threadpool_calls += 1
        event_loop_thread_ids.append(threading.get_ident())
        return await asyncio.to_thread(function, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(web_app, "run_in_threadpool", recording_run_in_threadpool)

    with TestClient(
        create_app(transcription_service=service), base_url=LOOPBACK_BASE_URL
    ) as client:
        response = client.post("/api/transcriptions/audio", content=b"audio", headers=VOICE_HEADERS)

    assert response.status_code == 200
    assert threadpool_calls == 1
    assert len(event_loop_thread_ids) == 1
    assert service.thread_ids[0] != event_loop_thread_ids[0]
    assert len(service.calls) == 1


def test_audio_endpoint_accepts_browser_codec_parameter_and_preserves_header_for_adapter() -> None:
    service = RecordingTranscriptionService()
    headers = {**VOICE_HEADERS, "content-type": "audio/webm;codecs=opus"}

    with TestClient(
        create_app(transcription_service=service), base_url=LOOPBACK_BASE_URL
    ) as client:
        response = client.post("/api/transcriptions/audio", content=b"webm", headers=headers)

    assert response.status_code == 200
    assert service.calls == [(b"webm", "audio/webm;codecs=opus")]


@pytest.mark.parametrize(
    "headers",
    [
        {"content-type": "audio/wav"},
        {**VOICE_HEADERS, TRANSCRIPTION_REQUEST_HEADER_NAME: "wrong"},
        {**VOICE_HEADERS, "origin": "https://evil.example"},
        {**VOICE_HEADERS, "origin": "http://127.0.0.1:9999"},
        {**VOICE_HEADERS, "host": "evil.example"},
        {**VOICE_HEADERS, "content-type": "application/json"},
        {**VOICE_HEADERS, "content-type": "text/plain"},
        {**VOICE_HEADERS, "content-type": "audio/webm; charset=utf-8"},
        {**VOICE_HEADERS, "content-type": "audio/webm;codecs=opus;extra=1"},
    ],
)
def test_audio_boundary_rejects_host_origin_header_and_mime_violations(
    headers: dict[str, str],
) -> None:
    service = RecordingTranscriptionService()

    with TestClient(
        create_app(transcription_service=service), base_url=LOOPBACK_BASE_URL
    ) as client:
        response = client.post("/api/transcriptions/audio", content=b"audio", headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TRANSCRIPTION_INVALID_REQUEST"
    assert response.headers["cache-control"] == "no-store"
    assert service.calls == []


def test_empty_audio_is_rejected_before_injected_service() -> None:
    service = RecordingTranscriptionService()

    with TestClient(
        create_app(transcription_service=service), base_url=LOOPBACK_BASE_URL
    ) as client:
        response = client.post("/api/transcriptions/audio", content=b"", headers=VOICE_HEADERS)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TRANSCRIPTION_INVALID_REQUEST"
    assert service.calls == []


def test_declared_audio_cap_rejects_before_read_or_service() -> None:
    service = RecordingTranscriptionService()
    status, headers, body, receive_calls = send_raw_asgi_request(
        create_app(transcription_service=service),
        path="/api/transcriptions/audio",
        headers={
            **VOICE_HEADERS,
            "host": "127.0.0.1",
            "content-length": str(MAX_RAW_TRANSCRIPTION_BODY_BYTES + 1),
        },
        body_chunks=(b"not read",),
    )

    assert status == 413
    assert headers["cache-control"] == "no-store"
    assert json.loads(body)["error"]["code"] == "TRANSCRIPTION_CONTENT_TOO_LARGE"
    assert receive_calls == 0
    assert service.calls == []


@pytest.mark.parametrize("declared_length", [None, "1"])
def test_actual_audio_cap_rejects_missing_or_misleading_content_length(
    declared_length: str | None,
) -> None:
    service = RecordingTranscriptionService()
    headers = {**VOICE_HEADERS, "host": "127.0.0.1"}
    if declared_length is not None:
        headers["content-length"] = declared_length

    status, response_headers, body, receive_calls = send_raw_asgi_request(
        create_app(transcription_service=service),
        path="/api/transcriptions/audio",
        headers=headers,
        body_chunks=(b"x", b"y" * MAX_RAW_TRANSCRIPTION_BODY_BYTES),
    )

    assert status == 413
    assert response_headers["cache-control"] == "no-store"
    assert json.loads(body)["error"]["code"] == "TRANSCRIPTION_CONTENT_TOO_LARGE"
    assert receive_calls == 2
    assert service.calls == []


def test_service_errors_are_safe_and_bounded() -> None:
    service = RecordingTranscriptionService(error=TranscriptionUpstreamError("provider secret"))

    with TestClient(
        create_app(transcription_service=service), base_url=LOOPBACK_BASE_URL
    ) as client:
        response = client.post("/api/transcriptions/audio", content=b"audio", headers=VOICE_HEADERS)

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "TRANSCRIPTION_UPSTREAM_FAILURE",
            "message": "transcription backend failed",
        }
    }
    assert "provider" not in response.text
    assert "secret" not in response.text


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (TranscriptionTimeoutError(), 504, "TRANSCRIPTION_TIMEOUT"),
        (TranscriptionBackendUnavailableError(), 503, "TRANSCRIPTION_BACKEND_UNAVAILABLE"),
    ],
)
def test_service_error_http_mapping(
    error: Exception,
    status: int,
    code: str,
) -> None:
    service = RecordingTranscriptionService(error=error)

    with TestClient(
        create_app(transcription_service=service), base_url=LOOPBACK_BASE_URL
    ) as client:
        response = client.post("/api/transcriptions/audio", content=b"audio", headers=VOICE_HEADERS)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_voice_static_ui_has_native_capture_and_in_memory_review_boundary() -> None:
    static_dir = (
        Path(__file__).parents[1] / "src" / "second_brain" / "entrypoints" / "web" / "static"
    )
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    javascript = (static_dir / "app.js").read_text(encoding="utf-8")

    assert 'data-mode="voice"' in html
    assert "data-audio-file" in html
    assert (
        'accept="audio/webm,audio/ogg,audio/wav,audio/x-wav,audio/mpeg,audio/mp4,audio/x-m4a"'
    ) in html
    assert "getUserMedia({ audio: true })" in javascript
    assert "MediaRecorder" in javascript
    assert "MediaRecorder.isTypeSupported" in javascript
    assert "microphoneRequestGeneration" in javascript
    await_index = javascript.index("const acquiredStream = await")
    stale_guard_index = javascript.index(
        "if (!isCurrentMicrophoneRequest(requestGeneration))", await_index
    )
    stale_stop_index = javascript.index("stopAudioTracks(acquiredStream)", stale_guard_index)
    assert await_index < stale_guard_index < stale_stop_index
    guard_definition = javascript[
        javascript.index("isCurrentMicrophoneRequest") : stale_guard_index
    ]
    assert 'mode === "voice"' in guard_definition
    assert "const RECORDING_TIMESLICE_MS = 1000;" in javascript
    assert "recorder.start(RECORDING_TIMESLICE_MS)" in javascript
    assert "recordingBytes + event.data.size" in javascript
    assert "recordingTooLarge = true" in javascript
    assert "stopRecordingStream();" in javascript[stale_stop_index:]
    assert "audioBlob = null" in javascript[stale_stop_index:]
    assert 'fetch("/api/transcriptions/audio"' in javascript
    assert '"X-Second-Brain-Request": "voice-v1"' in javascript
    assert "textInput.value = transcript" in javascript
    assert "Проверь расшифровку и затем создай черновик" in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript
    assert "indexedDB" not in javascript
    assert "serviceWorker" not in javascript
    transcription_section = javascript[
        javascript.index("transcribeButton") : javascript.index("form.addEventListener")
    ]
    assert "/api/drafts/text" not in transcription_section
