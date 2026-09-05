"""Server-issued integrity tokens for the Web draft review boundary."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from second_brain.application.research import SourceKind, SourceProvenance

REVIEW_TOKEN_VERSION = 1
REVIEW_TOKEN_SECRET_BYTES = 32
MAX_REVIEW_TOKEN_BYTES = 128 * 1024
_MAX_REVIEW_PAYLOAD_BYTES = 96 * 1024
_TOKEN_PREFIX = "v1"
_SOURCE_FIELDS = frozenset(
    {
        "uri",
        "kind",
        "retrieved_at",
        "published_at",
        "title",
        "author",
        "upstream_id",
    }
)


class ReviewTokenError(ValueError):
    """Safe failure for an invalid, tampered, or stale review token."""


class ReviewTokenMode(StrEnum):
    """The only two review origins that can reach Web Save."""

    TEXT = "text"
    RESEARCH = "research"


@dataclass(frozen=True, slots=True)
class ReviewTokenClaims:
    """Verified server-owned mode and optional research provenance."""

    mode: ReviewTokenMode
    source: SourceProvenance | None = None

    def __post_init__(self) -> None:
        """Keep text tokens source-free and research tokens single-source."""

        if type(self.mode) is not ReviewTokenMode:
            raise ValueError("mode must be a ReviewTokenMode")
        if self.mode is ReviewTokenMode.TEXT and self.source is not None:
            raise ValueError("text review tokens must not contain a source")
        if self.mode is ReviewTokenMode.RESEARCH and type(self.source) is not SourceProvenance:
            raise ValueError("research review tokens require one SourceProvenance")


@dataclass(frozen=True, slots=True, repr=False)
class ReviewTokenCodec:
    """Issue and verify compact HMAC tokens with a process-local secret."""

    _secret: bytes

    def __init__(self, secret: bytes | None = None) -> None:
        """Create a codec with a random in-memory secret unless injected by a test."""

        selected_secret = (
            secrets.token_bytes(REVIEW_TOKEN_SECRET_BYTES) if secret is None else secret
        )
        if type(selected_secret) is not bytes or len(selected_secret) < REVIEW_TOKEN_SECRET_BYTES:
            raise ValueError("review token secret must be at least 32 bytes")
        object.__setattr__(self, "_secret", selected_secret)

    def __repr__(self) -> str:
        """Avoid exposing the process-local secret in diagnostics or logs."""

        return "ReviewTokenCodec(<process-local secret>)"

    def issue_text(self) -> str:
        """Issue a token whose verified mode is text and whose sources are absent."""

        return self._issue({"mode": ReviewTokenMode.TEXT.value, "v": REVIEW_TOKEN_VERSION})

    def issue_research(self, source: SourceProvenance) -> str:
        """Issue a token bound to exactly one server-generated provenance value."""

        if type(source) is not SourceProvenance:
            raise ValueError("source must be a SourceProvenance")
        return self._issue(
            {
                "mode": ReviewTokenMode.RESEARCH.value,
                "source": _source_as_payload(source),
                "v": REVIEW_TOKEN_VERSION,
            }
        )

    def verify(self, token: str) -> ReviewTokenClaims:
        """Verify signature, canonical JSON, version, and the strict claims shape."""

        try:
            token_bytes = token.encode("ascii") if type(token) is str else b""
        except UnicodeEncodeError:
            raise ReviewTokenError() from None
        if not token_bytes or len(token_bytes) > MAX_REVIEW_TOKEN_BYTES:
            raise ReviewTokenError()
        parts = token.split(".") if type(token) is str else []
        if len(parts) != 3 or parts[0] != _TOKEN_PREFIX:
            raise ReviewTokenError()
        try:
            payload_bytes = _decode_base64url(parts[1], _MAX_REVIEW_PAYLOAD_BYTES)
            signature = _decode_base64url(parts[2], hashlib.sha256().digest_size)
        except ReviewTokenError, ValueError:
            raise ReviewTokenError() from None
        expected_signature = hmac.new(self._secret, payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ReviewTokenError()
        try:
            decoded = json.loads(
                payload_bytes.decode("utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
            if _canonical_json(decoded) != payload_bytes:
                raise ReviewTokenError()
            return _claims_from_payload(decoded)
        except ReviewTokenError:
            raise
        except UnicodeDecodeError, UnicodeEncodeError, TypeError, ValueError, RecursionError:
            raise ReviewTokenError() from None

    def _issue(self, payload: dict[str, Any]) -> str:
        """Serialize bounded claims and sign the exact canonical bytes."""

        try:
            payload_bytes = _canonical_json(payload)
        except TypeError, UnicodeEncodeError, ValueError:
            raise ReviewTokenError() from None
        if len(payload_bytes) > _MAX_REVIEW_PAYLOAD_BYTES:
            raise ReviewTokenError()
        signature = hmac.new(self._secret, payload_bytes, hashlib.sha256).digest()
        token = ".".join(
            (
                _TOKEN_PREFIX,
                _encode_base64url(payload_bytes),
                _encode_base64url(signature),
            )
        )
        try:
            token_size = len(token.encode("ascii"))
        except UnicodeEncodeError:
            raise ReviewTokenError() from None
        if token_size > MAX_REVIEW_TOKEN_BYTES:
            raise ReviewTokenError()
        return token


def _source_as_payload(source: SourceProvenance) -> dict[str, str | None]:
    """Project only the seven public provenance fields into the token."""

    return {
        "author": source.author,
        "kind": source.source_kind.value,
        "published_at": _datetime_as_text(source.published_at),
        "retrieved_at": _datetime_as_text(source.retrieved_at),
        "title": source.title,
        "upstream_id": source.upstream_id,
        "uri": source.uri,
    }


def _datetime_as_text(value: datetime | None) -> str | None:
    """Use a lossless explicit-offset representation for provenance timestamps."""

    return None if value is None else value.isoformat()


def _claims_from_payload(payload: object) -> ReviewTokenClaims:
    """Decode an exact versioned payload without accepting client-added fields."""

    if type(payload) is not dict or set(payload) not in (
        {"mode", "v"},
        {"mode", "source", "v"},
    ):
        raise ReviewTokenError()
    version = payload.get("v")
    mode = payload.get("mode")
    if type(version) is not int or version != REVIEW_TOKEN_VERSION or type(mode) is not str:
        raise ReviewTokenError()
    try:
        parsed_mode = ReviewTokenMode(mode)
    except ValueError:
        raise ReviewTokenError() from None
    if parsed_mode is ReviewTokenMode.TEXT:
        if set(payload) != {"mode", "v"}:
            raise ReviewTokenError()
        return ReviewTokenClaims(parsed_mode)
    if set(payload) != {"mode", "source", "v"}:
        raise ReviewTokenError()
    return ReviewTokenClaims(parsed_mode, _source_from_payload(payload.get("source")))


def _source_from_payload(payload: object) -> SourceProvenance:
    """Reconstruct only the signed public provenance shape."""

    if type(payload) is not dict or set(payload) != _SOURCE_FIELDS:
        raise ReviewTokenError()
    try:
        values = {
            field: _strict_optional_string(payload, field)
            for field in ("uri", "published_at", "title", "author", "upstream_id")
        }
        retrieved_at = _strict_string(payload, "retrieved_at")
        kind = SourceKind(_strict_string(payload, "kind"))
        retrieved = datetime.fromisoformat(retrieved_at)
        published_text = values["published_at"]
        published = None if published_text is None else datetime.fromisoformat(published_text)
        return SourceProvenance(
            uri=values["uri"] or "",
            source_kind=kind,
            retrieved_at=retrieved,
            published_at=published,
            title=values["title"],
            author=values["author"],
            upstream_id=values["upstream_id"],
        )
    except TypeError, ValueError, OverflowError:
        raise ReviewTokenError() from None


def _strict_string(payload: dict[str, object], field: str) -> str:
    """Read one required JSON string without coercion."""

    value = payload.get(field)
    if type(value) is not str:
        raise ReviewTokenError()
    return value


def _strict_optional_string(payload: dict[str, object], field: str) -> str | None:
    """Read one nullable JSON string without coercion."""

    value = payload.get(field)
    if value is not None and type(value) is not str:
        raise ReviewTokenError()
    return value


def _canonical_json(value: object) -> bytes:
    """Encode a JSON value with deterministic UTF-8 object ordering."""

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON object keys before claims validation."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewTokenError()
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    """Reject non-standard JSON constants such as NaN and Infinity."""

    del value
    raise ReviewTokenError()


def _encode_base64url(value: bytes) -> str:
    """Encode without padding so the token grammar stays unambiguous."""

    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: str, max_decoded_bytes: int) -> bytes:
    """Decode only unpadded URL-safe base64 with a bounded output size."""

    if type(value) is not str or not value or len(value) > MAX_REVIEW_TOKEN_BYTES:
        raise ReviewTokenError()
    allowed_characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
    if any(char not in allowed_characters for char in value):
        raise ReviewTokenError()
    if len(value) % 4 == 1:
        raise ReviewTokenError()
    padded = value + "=" * ((4 - len(value) % 4) % 4)
    try:
        decoded = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except binascii.Error, ValueError:
        raise ReviewTokenError() from None
    if len(decoded) > max_decoded_bytes:
        raise ReviewTokenError()
    if _encode_base64url(decoded) != value:
        raise ReviewTokenError()
    return decoded


__all__ = [
    "MAX_REVIEW_TOKEN_BYTES",
    "REVIEW_TOKEN_SECRET_BYTES",
    "REVIEW_TOKEN_VERSION",
    "ReviewTokenClaims",
    "ReviewTokenCodec",
    "ReviewTokenError",
    "ReviewTokenMode",
]
