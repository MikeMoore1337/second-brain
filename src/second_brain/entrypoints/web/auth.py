"""Single-owner GitHub OAuth and session boundary for the Web GUI."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal, Protocol, cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.types import ASGIApp, Receive, Scope, Send

from second_brain.config import ConfigurationError

WEB_AUTH_DISABLED: Final[Literal["disabled"]] = "disabled"
WEB_AUTH_GITHUB: Final[Literal["github"]] = "github"
GITHUB_DISPLAY_LOGIN: Final[str] = "MikeMoore1337"
GITHUB_AUTHORIZE_URL: Final[str] = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL: Final[str] = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL: Final[str] = "https://api.github.com/user"
GITHUB_USER_AGENT: Final[str] = "Second-Brain-Web-Auth-v1"
SESSION_COOKIE_NAME: Final[str] = "second_brain_session"
OAUTH_STATE_COOKIE_NAME: Final[str] = "second_brain_oauth_state"
AUTH_TRUSTED_AUTHORITIES_SCOPE_KEY: Final[str] = "second_brain.auth.trusted_authorities"
AUTH_USER_ID_SCOPE_KEY: Final[str] = "second_brain.auth.user_id"
AUTH_MODE_APP_STATE_KEY: Final[str] = "second_brain.auth.mode"
MAX_COOKIE_HEADER_BYTES: Final[int] = 8 * 1024
MAX_SESSION_COOKIE_BYTES: Final[int] = 1024
MAX_OAUTH_STATE_BYTES: Final[int] = 512
MAX_OAUTH_CODE_BYTES: Final[int] = 2048
MAX_OAUTH_ERROR_BYTES: Final[int] = 128
MAX_OAUTH_QUERY_BYTES: Final[int] = 8 * 1024
MAX_PROVIDER_BODY_BYTES: Final[int] = 32 * 1024
MAX_INDEX_BYTES: Final[int] = 2 * 1024 * 1024
MIN_SESSION_SECRET_BYTES: Final[int] = 32
DEFAULT_SESSION_TTL_SECONDS: Final[int] = 12 * 60 * 60
MIN_SESSION_TTL_SECONDS: Final[int] = 5 * 60
MAX_SESSION_TTL_SECONDS: Final[int] = 7 * 24 * 60 * 60
DEFAULT_OAUTH_STATE_TTL_SECONDS: Final[int] = 10 * 60
MIN_OAUTH_STATE_TTL_SECONDS: Final[int] = 60
MAX_OAUTH_STATE_TTL_SECONDS: Final[int] = 15 * 60
MAX_STATE_ENTRIES: Final[int] = 256
_DECIMAL_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+\Z")
_LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "localhost", "::1"})

TrustedAuthority = tuple[str, int]
WebAuthMode = Literal["disabled", "github"]

AUTH_SECURITY_HEADERS: Final[dict[str, str]] = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; connect-src 'self'; font-src 'self'; "
        "form-action 'none'; frame-ancestors 'none'; img-src 'self'; object-src 'none'; "
        "script-src 'self'; style-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class WebAuthEnvironmentSettings(BaseSettings):
    """Raw typed environment values before public-mode validation."""

    web_auth: str = WEB_AUTH_DISABLED
    public_base_url: str | None = None
    github_client_id: str | None = None
    github_client_secret: str | None = None
    github_allowed_user_id: str | None = None
    session_secret: str | None = None
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS
    oauth_state_ttl_seconds: int = DEFAULT_OAUTH_STATE_TTL_SECONDS

    model_config = SettingsConfigDict(
        env_prefix="SECOND_BRAIN_",
        env_file=None,
        extra="ignore",
    )


def _optional_text(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value


def _required_text(value: str | None, name: str, *, max_bytes: int) -> str:
    normalized = _optional_text(value)
    if normalized is None:
        raise ValueError(f"{name} is required when SECOND_BRAIN_WEB_AUTH=github")
    if len(normalized.encode("utf-8")) > max_bytes or any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in normalized
    ):
        raise ValueError(f"{name} is invalid")
    return normalized


def _canonical_public_base_url(value: str | None) -> tuple[str, TrustedAuthority]:
    normalized = _required_text(value, "SECOND_BRAIN_PUBLIC_BASE_URL", max_bytes=512)
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
        port = parsed.port
    except UnicodeError, ValueError:
        raise ValueError("SECOND_BRAIN_PUBLIC_BASE_URL is invalid") from None
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or hostname is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or any(character.isspace() for character in normalized)
    ):
        raise ValueError("SECOND_BRAIN_PUBLIC_BASE_URL must be an HTTPS origin")
    try:
        hostname.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("SECOND_BRAIN_PUBLIC_BASE_URL hostname is invalid") from None
    normalized_host = hostname.casefold()
    normalized_port = 443 if port is None else port
    if not 1 <= normalized_port <= 65535:
        raise ValueError("SECOND_BRAIN_PUBLIC_BASE_URL port is invalid")
    url_host = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
    canonical = f"https://{url_host}"
    if normalized_port != 443:
        canonical += f":{normalized_port}"
    return canonical, (normalized_host, normalized_port)


def _validated_user_id(value: str | None) -> str:
    normalized = _required_text(value, "SECOND_BRAIN_GITHUB_ALLOWED_USER_ID", max_bytes=32)
    if _DECIMAL_ID.fullmatch(normalized) is None:
        raise ValueError("SECOND_BRAIN_GITHUB_ALLOWED_USER_ID is invalid")
    return normalized


def _validated_session_secret(value: str | None) -> bytes:
    normalized = _required_text(value, "SECOND_BRAIN_SESSION_SECRET", max_bytes=256)
    encoded = normalized.encode("utf-8")
    if (
        len(encoded) < MIN_SESSION_SECRET_BYTES
        or len(set(encoded)) < 4
        or normalized.casefold() in {"change-me", "replace-me", "your-secret", "placeholder"}
    ):
        raise ValueError(
            "SECOND_BRAIN_SESSION_SECRET must be a strong random value of at least 32 bytes"
        )
    return encoded


def _validated_ttl(value: int, name: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the supported bounded range")
    return value


@dataclass(frozen=True, slots=True)
class WebAuthConfig:
    """Validated Web auth configuration; secrets never leave this process boundary."""

    mode: WebAuthMode
    public_base_url: str | None
    github_client_id: str | None
    github_client_secret: str | None
    allowed_user_id: str | None
    session_secret: bytes | None
    session_ttl_seconds: int
    oauth_state_ttl_seconds: int
    trusted_authorities: frozenset[TrustedAuthority]

    @property
    def callback_url(self) -> str:
        if self.public_base_url is None:
            raise RuntimeError("callback URL is unavailable in disabled auth mode")
        return f"{self.public_base_url}/auth/github/callback"

    @property
    def secure_cookies(self) -> bool:
        return self.mode == WEB_AUTH_GITHUB

    @classmethod
    def from_settings(cls, settings: WebAuthEnvironmentSettings) -> WebAuthConfig:
        mode = settings.web_auth.strip().casefold()
        if mode == WEB_AUTH_DISABLED:
            return cls(
                mode=WEB_AUTH_DISABLED,
                public_base_url=None,
                github_client_id=None,
                github_client_secret=None,
                allowed_user_id=None,
                session_secret=None,
                session_ttl_seconds=DEFAULT_SESSION_TTL_SECONDS,
                oauth_state_ttl_seconds=DEFAULT_OAUTH_STATE_TTL_SECONDS,
                trusted_authorities=frozenset(),
            )
        if mode != WEB_AUTH_GITHUB:
            raise ValueError("SECOND_BRAIN_WEB_AUTH must be disabled or github")
        public_base_url, authority = _canonical_public_base_url(settings.public_base_url)
        return cls(
            mode=WEB_AUTH_GITHUB,
            public_base_url=public_base_url,
            github_client_id=_required_text(
                settings.github_client_id,
                "SECOND_BRAIN_GITHUB_CLIENT_ID",
                max_bytes=256,
            ),
            github_client_secret=_required_text(
                settings.github_client_secret,
                "SECOND_BRAIN_GITHUB_CLIENT_SECRET",
                max_bytes=512,
            ),
            allowed_user_id=_validated_user_id(settings.github_allowed_user_id),
            session_secret=_validated_session_secret(settings.session_secret),
            session_ttl_seconds=_validated_ttl(
                settings.session_ttl_seconds,
                "SECOND_BRAIN_SESSION_TTL_SECONDS",
                minimum=MIN_SESSION_TTL_SECONDS,
                maximum=MAX_SESSION_TTL_SECONDS,
            ),
            oauth_state_ttl_seconds=_validated_ttl(
                settings.oauth_state_ttl_seconds,
                "SECOND_BRAIN_OAUTH_STATE_TTL_SECONDS",
                minimum=MIN_OAUTH_STATE_TTL_SECONDS,
                maximum=MAX_OAUTH_STATE_TTL_SECONDS,
            ),
            trusted_authorities=frozenset({authority}),
        )


def load_web_auth_config(*, env_file: Path | None = None) -> WebAuthConfig:
    """Load auth configuration from the explicit env file or process environment."""

    selected_env: Path | None = None
    if env_file is not None:
        selected_env = env_file.resolve()
        if not selected_env.is_file():
            raise ConfigurationError(f"env file does not exist: {selected_env}")
    try:
        settings = WebAuthEnvironmentSettings(_env_file=selected_env)  # type: ignore[call-arg]
    except ValidationError:
        raise ConfigurationError("invalid Web auth configuration") from None
    try:
        return WebAuthConfig.from_settings(settings)
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from None


def _parse_authority(value: str, scheme: str) -> TrustedAuthority | None:
    if not value or any(character.isspace() for character in value):
        return None
    normalized_scheme = scheme.casefold()
    if normalized_scheme not in {"http", "https"}:
        return None
    try:
        parsed = urlsplit(f"//{value}")
        hostname = parsed.hostname
        port = parsed.port
    except UnicodeError, ValueError:
        return None
    if (
        hostname is None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
    ):
        return None
    normalized_port = port
    if normalized_port is None:
        normalized_port = 443 if normalized_scheme == "https" else 80
    if not 1 <= normalized_port <= 65535:
        return None
    return hostname.casefold(), normalized_port


def configured_authority_port(
    value: str,
    scheme: str,
    authorities: frozenset[TrustedAuthority],
) -> TrustedAuthority | None:
    """Return an exact configured authority match without accepting wildcards."""

    parsed = _parse_authority(value, scheme)
    if parsed is None or parsed not in authorities:
        return None
    return parsed


def trusted_authorities_from_scope(scope: Scope) -> frozenset[TrustedAuthority]:
    """Read the internal configured-authority marker set by WebAuthMiddleware."""

    value = scope.get(AUTH_TRUSTED_AUTHORITIES_SCOPE_KEY)
    if not isinstance(value, frozenset):
        return frozenset()
    authorities: set[TrustedAuthority] = set()
    for item in value:
        if (
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], str)
            and type(item[1]) is int
        ):
            authorities.add((item[0], item[1]))
    return frozenset(authorities)


def _loopback_authority(value: str, scheme: str) -> TrustedAuthority | None:
    parsed = _parse_authority(value, scheme)
    if parsed is None or parsed[0] not in _LOOPBACK_HOSTS:
        return None
    return parsed


def request_host_is_trusted(scope: Scope, authorities: frozenset[TrustedAuthority]) -> bool:
    """Accept the existing loopback policy or one exact configured public authority."""

    values = [
        value.decode("latin-1")
        for name, value in scope.get("headers", [])
        if name.lower() == b"host"
    ]
    if len(values) != 1:
        return False
    scheme = str(scope.get("scheme", "")).casefold()
    return (
        _loopback_authority(values[0], scheme) is not None
        or configured_authority_port(values[0], scheme, authorities) is not None
    )


def same_origin_is_trusted(
    scope: Scope,
    origin: str,
    authorities: frozenset[TrustedAuthority],
) -> bool:
    """Compare Origin with Host under loopback or exact configured-authority policy."""

    scheme = str(scope.get("scheme", "")).casefold()
    if scheme not in {"http", "https"}:
        return False
    try:
        parsed_origin = urlsplit(origin)
    except UnicodeError, ValueError:
        return False
    if (
        parsed_origin.scheme.casefold() != scheme
        or not parsed_origin.netloc
        or parsed_origin.path
        or parsed_origin.query
        or parsed_origin.fragment
        or parsed_origin.username is not None
        or parsed_origin.password is not None
    ):
        return False
    host_values = [
        value.decode("latin-1")
        for name, value in scope.get("headers", [])
        if name.lower() == b"host"
    ]
    if len(host_values) != 1:
        return False
    origin_authority = _loopback_authority(parsed_origin.netloc, scheme)
    request_authority = _loopback_authority(host_values[0], scheme)
    if origin_authority is not None or request_authority is not None:
        return origin_authority is not None and origin_authority == request_authority
    return (
        configured_authority_port(parsed_origin.netloc, scheme, authorities)
        == (configured_authority_port(host_values[0], scheme, authorities))
        and configured_authority_port(parsed_origin.netloc, scheme, authorities) is not None
    )


def _strict_b64decode(value: str, *, max_bytes: int) -> bytes:
    if not value or len(value) > max_bytes * 2 or _BASE64URL.fullmatch(value) is None:
        raise ValueError
    padded = value + "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    except binascii.Error, UnicodeError:
        raise ValueError from None
    if len(decoded) > max_bytes:
        raise ValueError
    return decoded


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _json_object_no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class SessionClaims:
    """Only the stable owner ID and expiry are represented in a session."""

    user_id: str
    expires_at: int


class SignedSessionCodec:
    """Stateless HMAC-authenticated session codec with bounded claims."""

    def __init__(
        self,
        *,
        secret: bytes,
        allowed_user_id: str,
        ttl_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(secret) < MIN_SESSION_SECRET_BYTES or _DECIMAL_ID.fullmatch(allowed_user_id) is None:
            raise ValueError("invalid session codec configuration")
        self._secret = bytes(secret)
        self._allowed_user_id = allowed_user_id
        self._ttl_seconds = _validated_ttl(
            ttl_seconds,
            "session TTL",
            minimum=MIN_SESSION_TTL_SECONDS,
            maximum=MAX_SESSION_TTL_SECONDS,
        )
        self._clock = clock

    def issue(self, user_id: str) -> str:
        """Create a short-lived token for the configured owner only."""

        if not hmac.compare_digest(user_id, self._allowed_user_id):
            raise ValueError("cannot issue a session for a different user")
        issued_at = int(self._clock())
        expires_at = issued_at + self._ttl_seconds
        payload = json.dumps(
            {"exp": expires_at, "iat": issued_at, "sub": user_id},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        encoded_payload = _b64encode(payload)
        signed = f"v1.{encoded_payload}".encode("ascii")
        signature = hmac.new(self._secret, signed, hashlib.sha256).digest()
        return f"v1.{encoded_payload}.{_b64encode(signature)}"

    def decode(self, value: str | None) -> SessionClaims | None:
        """Reject malformed, forged, expired, future-dated and wrong-owner tokens."""

        if value is None or type(value) is not str or len(value) > MAX_SESSION_COOKIE_BYTES:
            return None
        parts = value.split(".")
        if len(parts) != 3 or parts[0] != "v1":
            return None
        try:
            payload_bytes = _strict_b64decode(parts[1], max_bytes=512)
            provided_signature = _strict_b64decode(parts[2], max_bytes=64)
        except ValueError:
            return None
        signed = f"v1.{parts[1]}".encode("ascii")
        expected_signature = hmac.new(self._secret, signed, hashlib.sha256).digest()
        if not hmac.compare_digest(provided_signature, expected_signature):
            return None
        try:
            payload = json.loads(
                payload_bytes.decode("utf-8"),
                object_pairs_hook=_json_object_no_duplicates,
            )
        except UnicodeError, ValueError, json.JSONDecodeError:
            return None
        if type(payload) is not dict or set(payload) != {"exp", "iat", "sub"}:
            return None
        issued_at = payload.get("iat")
        expires_at = payload.get("exp")
        user_id = payload.get("sub")
        if (
            type(issued_at) is not int
            or type(expires_at) is not int
            or type(user_id) is not str
            or _DECIMAL_ID.fullmatch(user_id) is None
            or not hmac.compare_digest(user_id, self._allowed_user_id)
        ):
            return None
        now = int(self._clock())
        if (
            issued_at > now + 60
            or expires_at <= now
            or expires_at <= issued_at
            or expires_at - issued_at > self._ttl_seconds
        ):
            return None
        return SessionClaims(user_id=user_id, expires_at=expires_at)


def _state_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class OAuthStateCapacityError(RuntimeError):
    """Raised when issuing another OAuth state would evict a live state."""


@dataclass(slots=True)
class OAuthStateStore:
    """Bounded process-local one-time state store; only digests are retained."""

    ttl_seconds: int
    clock: Callable[[], float] = time.time
    max_entries: int = MAX_STATE_ENTRIES
    _entries: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.ttl_seconds = _validated_ttl(
            self.ttl_seconds,
            "OAuth state TTL",
            minimum=MIN_OAUTH_STATE_TTL_SECONDS,
            maximum=MAX_OAUTH_STATE_TTL_SECONDS,
        )
        if type(self.max_entries) is not int or not 1 <= self.max_entries <= MAX_STATE_ENTRIES:
            raise ValueError("invalid OAuth state store bound")

    def _prune(self, now: int) -> None:
        expired = [key for key, expires_at in self._entries.items() if expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)

    def issue(self) -> str:
        now = int(self.clock())
        with self._lock:
            self._prune(now)
            if len(self._entries) >= self.max_entries:
                raise OAuthStateCapacityError
            while True:
                state = secrets.token_urlsafe(32)
                digest = _state_digest(state)
                if digest not in self._entries:
                    self._entries[digest] = now + self.ttl_seconds
                    return state

    def consume(self, state: str | None) -> bool:
        if state is None or type(state) is not str or not 1 <= len(state) <= MAX_OAUTH_STATE_BYTES:
            return False
        now = int(self.clock())
        digest = _state_digest(state)
        with self._lock:
            self._prune(now)
            expires_at = self._entries.pop(digest, None)
        return expires_at is not None and expires_at > now


class GitHubOAuthError(RuntimeError):
    """Internal provider failure with no upstream detail in its public surface."""


@dataclass(frozen=True, slots=True)
class GitHubIdentity:
    """Minimal authenticated GitHub identity used by the allowlist."""

    user_id: int
    login: str | None = None


class GitHubOAuthGateway(Protocol):
    """Injectable gateway seam for deterministic OAuth tests."""

    def exchange_code(self, code: str, redirect_uri: str) -> str:
        """Exchange a one-time authorization code server-side."""

    def get_authenticated_user(self, access_token: str) -> GitHubIdentity:
        """Read the authenticated user's stable numeric identity."""


class _HttpResponse(Protocol):
    status: int

    def read(self, amount: int = -1) -> bytes:
        """Read bounded response bytes."""

    def __enter__(self) -> _HttpResponse:
        """Enter response context."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        """Close the response."""


class _UrlOpen(Protocol):
    def __call__(self, request: urllib.request.Request, *, timeout: float) -> _HttpResponse:
        """Open a fixed HTTPS request."""


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep provider requests bound to their fixed HTTPS endpoints."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request:
        del req, fp, code, msg, headers, newurl
        raise GitHubOAuthError


def _urlopen_without_redirects(request: urllib.request.Request, *, timeout: float) -> _HttpResponse:
    opener = urllib.request.build_opener(_RejectRedirectHandler)
    return cast(_HttpResponse, opener.open(request, timeout=timeout))


def _provider_json(response: _HttpResponse) -> dict[str, object]:
    try:
        status = response.status
        body = response.read(MAX_PROVIDER_BODY_BYTES + 1)
    except Exception:
        raise GitHubOAuthError from None
    if type(status) is not int or status != 200 or type(body) is not bytes:
        raise GitHubOAuthError
    if len(body) > MAX_PROVIDER_BODY_BYTES:
        raise GitHubOAuthError
    try:
        decoded = json.loads(body.decode("utf-8"))
    except UnicodeError, ValueError:
        raise GitHubOAuthError from None
    if type(decoded) is not dict:
        raise GitHubOAuthError
    return cast(dict[str, object], decoded)


def _valid_secret_text(value: str, *, max_bytes: int) -> bool:
    return 1 <= len(value.encode("utf-8")) <= max_bytes and not any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in value
    )


def parse_github_identity(payload: Mapping[str, object]) -> GitHubIdentity:
    """Validate only the stable ID and bounded optional login from GitHub JSON."""

    if type(payload) is not dict:
        raise GitHubOAuthError
    user_id = payload.get("id")
    if type(user_id) is not int or not 1 <= user_id <= 2**63 - 1:
        raise GitHubOAuthError
    login = payload.get("login")
    if login is not None and (
        type(login) is not str or not _valid_secret_text(login, max_bytes=128)
    ):
        raise GitHubOAuthError
    return GitHubIdentity(user_id=user_id, login=login)


@dataclass(slots=True)
class GitHubOAuthClient:
    """Small fixed-host stdlib HTTP client with bounded responses and no persistence."""

    client_id: str
    client_secret: str
    opener: _UrlOpen = field(
        default_factory=lambda: _urlopen_without_redirects,
    )
    timeout_seconds: float = 10.0

    def _request_json(self, request: urllib.request.Request) -> dict[str, object]:
        try:
            response = self.opener(request, timeout=self.timeout_seconds)
            with response:
                return _provider_json(response)
        except GitHubOAuthError:
            raise
        except Exception:
            raise GitHubOAuthError from None

    def exchange_code(self, code: str, redirect_uri: str) -> str:
        if not _valid_secret_text(code, max_bytes=MAX_OAUTH_CODE_BYTES):
            raise GitHubOAuthError
        request = urllib.request.Request(
            GITHUB_TOKEN_URL,
            data=urllib.parse.urlencode(
                {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                }
            ).encode("ascii"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": GITHUB_USER_AGENT,
            },
            method="POST",
        )
        payload = self._request_json(request)
        token = payload.get("access_token")
        if type(token) is not str or not _valid_secret_text(token, max_bytes=4096):
            raise GitHubOAuthError
        return token

    def get_authenticated_user(self, access_token: str) -> GitHubIdentity:
        if not _valid_secret_text(access_token, max_bytes=4096):
            raise GitHubOAuthError
        request = urllib.request.Request(
            GITHUB_USER_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "User-Agent": GITHUB_USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        return parse_github_identity(self._request_json(request))


def _scope_header_values(scope: Scope, name: bytes) -> list[str]:
    return [
        value.decode("latin-1")
        for header_name, value in scope.get("headers", [])
        if header_name.lower() == name
    ]


def _single_cookie(scope: Scope, name: str, *, max_bytes: int) -> str | None:
    values = _scope_header_values(scope, b"cookie")
    if len(values) != 1 or len(values[0].encode("latin-1")) > MAX_COOKIE_HEADER_BYTES:
        return None
    found: str | None = None
    for raw_pair in values[0].split(";"):
        pair = raw_pair.strip()
        if not pair:
            continue
        key, separator, value = pair.partition("=")
        if not separator or not key:
            return None
        if key == name:
            if found is not None:
                return None
            if not 1 <= len(value.encode("latin-1")) <= max_bytes:
                return None
            found = value
    return found


def _single_query_param(request: Request, name: str, *, max_bytes: int) -> str | None:
    raw_query = request.scope.get("query_string", b"")
    if not isinstance(raw_query, bytes) or len(raw_query) > MAX_OAUTH_QUERY_BYTES:
        return None
    values = request.query_params.getlist(name)
    if len(values) != 1:
        return None
    value = values[0]
    if not 1 <= len(value.encode("utf-8")) <= max_bytes or any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in value
    ):
        return None
    return value


def _with_deleted_cookie(response: Response, *, name: str, secure: bool, path: str) -> Response:
    response.delete_cookie(
        name,
        path=path,
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    return response


def _read_index_html(index_file: Path) -> str:
    if not index_file.is_file():
        raise OSError
    with index_file.open("rb") as stream:
        content = stream.read(MAX_INDEX_BYTES + 1)
    if len(content) > MAX_INDEX_BYTES:
        raise OSError
    return content.decode("utf-8")


def _index_html_with_markers(
    html: str,
    *,
    auth_mode: WebAuthMode,
    auth_error: Literal["oauth", "denied"] | None = None,
) -> str:
    body_start = html.casefold().find("<body")
    body_end = html.find(">", body_start)
    if body_start < 0 or body_end < 0:
        raise OSError
    markers = [f' data-second-brain-auth-mode="{auth_mode}"']
    if auth_error is not None:
        markers.append(f' data-second-brain-auth-error="{auth_error}"')
    return html[:body_end] + "".join(markers) + html[body_end:]


def web_index_response(
    index_file: Path,
    *,
    auth_mode: WebAuthMode = WEB_AUTH_DISABLED,
    status_code: int = 200,
) -> Response:
    if not index_file.is_file():
        return PlainTextResponse(
            "Для запуска нужен собранный React-интерфейс.",
            status_code=503,
            headers=AUTH_SECURITY_HEADERS,
        )
    try:
        html = _index_html_with_markers(
            _read_index_html(index_file),
            auth_mode=auth_mode,
        )
    except OSError, UnicodeError:
        return PlainTextResponse(
            "Для запуска нужен собранный React-интерфейс.",
            status_code=503,
            headers=AUTH_SECURITY_HEADERS,
        )
    return HTMLResponse(
        content=html,
        status_code=status_code,
        headers=AUTH_SECURITY_HEADERS,
    )


def _auth_error_page(
    index_file: Path,
    *,
    status_code: int,
    kind: Literal["oauth", "denied"],
    auth_mode: WebAuthMode,
) -> Response:
    title = "Доступ закрыт" if kind == "denied" else "Не удалось выполнить вход"
    message = (
        "Этот Second Brain доступен только владельцу."
        if kind == "denied"
        else "Попробуйте повторить вход через GitHub."
    )
    try:
        html = _index_html_with_markers(
            _read_index_html(index_file),
            auth_mode=auth_mode,
            auth_error=kind,
        )
        return HTMLResponse(
            content=html,
            status_code=status_code,
            headers=AUTH_SECURITY_HEADERS,
        )
    except OSError, UnicodeError:
        retry_query = "denied" if kind == "denied" else "oauth"
        fallback = (
            '<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>'
            f"{title}</title></head><body><main><h1>{title}</h1><p>{message}</p>"
            f'<a href="/login?error={retry_query}">Повторить вход</a></main></body></html>'
        )
        return HTMLResponse(
            content=fallback,
            status_code=status_code,
            headers=AUTH_SECURITY_HEADERS,
        )


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303, headers=AUTH_SECURITY_HEADERS)


def _error_with_state_clear(
    index_file: Path,
    *,
    status_code: int,
    kind: Literal["oauth", "denied"],
    auth_mode: WebAuthMode,
    secure: bool,
) -> Response:
    return _with_deleted_cookie(
        _auth_error_page(
            index_file,
            status_code=status_code,
            kind=kind,
            auth_mode=auth_mode,
        ),
        name=OAUTH_STATE_COOKIE_NAME,
        secure=secure,
        path="/auth/github/callback",
    )


class WebAuthMiddleware:
    """Require a valid Second Brain session on every private UI/API request."""

    def __init__(self, app: ASGIApp, *, config: WebAuthConfig, codec: SignedSessionCodec) -> None:
        self.app = app
        self.config = config
        self.codec = codec

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        if not request_host_is_trusted(scope, self.config.trusted_authorities):
            invalid_host_response = PlainTextResponse(
                "Некорректный заголовок запроса Host.",
                status_code=400,
                headers=AUTH_SECURITY_HEADERS,
            )
            await invalid_host_response(scope, receive, send)
            return
        scope[AUTH_TRUSTED_AUTHORITIES_SCOPE_KEY] = self.config.trusted_authorities
        path = str(scope.get("path", ""))
        public_path = path in {
            "/login",
            "/auth/github/login",
            "/auth/github/callback",
            "/auth/logout",
            "/healthz",
        }
        public_asset = (
            path == "/assets"
            or path.startswith("/assets/")
            or path == "/react/assets"
            or path.startswith("/react/assets/")
        )
        public_pwa_asset = path in {
            "/manifest.webmanifest",
            "/sw.js",
            "/offline.html",
            "/offline.css",
            "/icons",
        } or path.startswith("/icons/")
        if public_path or public_asset or public_pwa_asset:
            await self.app(scope, receive, send)
            return

        session = self.codec.decode(
            _single_cookie(scope, SESSION_COOKIE_NAME, max_bytes=MAX_SESSION_COOKIE_BYTES)
        )
        if session is not None:
            scope[AUTH_USER_ID_SCOPE_KEY] = session.user_id
            await self.app(scope, receive, send)
            return

        if path == "/api" or path.startswith("/api/"):
            response = JSONResponse(
                status_code=401,
                content={"error": {"code": "AUTH_REQUIRED", "message": "Требуется вход"}},
                headers=AUTH_SECURITY_HEADERS,
            )
            await response(scope, receive, send)
            return
        if path in {"/", "/react", "/react/"}:
            redirect_response = _redirect("/login")
            await redirect_response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def install_web_auth(
    app: FastAPI,
    *,
    config: WebAuthConfig,
    index_file: Path,
    gateway: GitHubOAuthGateway | None = None,
    clock: Callable[[], float] = time.time,
) -> None:
    """Install public auth routes and, in GitHub mode, the outer private boundary."""

    setattr(app.state, AUTH_MODE_APP_STATE_KEY, config.mode)
    codec: SignedSessionCodec | None = None
    state_store: OAuthStateStore | None = None
    actual_gateway: GitHubOAuthGateway | None = None
    if config.mode == WEB_AUTH_GITHUB:
        if (
            config.github_client_id is None
            or config.github_client_secret is None
            or config.allowed_user_id is None
            or config.session_secret is None
        ):
            raise ConfigurationError("incomplete GitHub Web auth configuration")
        codec = SignedSessionCodec(
            secret=config.session_secret,
            allowed_user_id=config.allowed_user_id,
            ttl_seconds=config.session_ttl_seconds,
            clock=clock,
        )
        state_store = OAuthStateStore(config.oauth_state_ttl_seconds, clock=clock)
        actual_gateway = gateway or GitHubOAuthClient(
            client_id=config.github_client_id,
            client_secret=config.github_client_secret,
        )
        app.add_middleware(WebAuthMiddleware, config=config, codec=codec)

    @app.get("/login", include_in_schema=False)
    def login() -> Response:
        """Serve the public branded login surface."""

        return web_index_response(index_file, auth_mode=config.mode)

    @app.get("/auth/github/login", include_in_schema=False)
    def github_login() -> Response:
        """Start one state-bound GitHub OAuth authorization request."""

        if config.mode != WEB_AUTH_GITHUB or state_store is None:
            return _auth_error_page(
                index_file,
                status_code=503,
                kind="oauth",
                auth_mode=config.mode,
            )
        if config.github_client_id is None:
            return _auth_error_page(
                index_file,
                status_code=503,
                kind="oauth",
                auth_mode=config.mode,
            )
        try:
            state = state_store.issue()
        except OAuthStateCapacityError:
            return _auth_error_page(
                index_file,
                status_code=503,
                kind="oauth",
                auth_mode=config.mode,
            )
        query = urllib.parse.urlencode(
            {
                "client_id": config.github_client_id,
                "redirect_uri": config.callback_url,
                "state": state,
                "allow_signup": "false",
            }
        )
        response = _redirect(f"{GITHUB_AUTHORIZE_URL}?{query}")
        response.set_cookie(
            OAUTH_STATE_COOKIE_NAME,
            state,
            max_age=config.oauth_state_ttl_seconds,
            secure=config.secure_cookies,
            httponly=True,
            samesite="lax",
            path="/auth/github/callback",
        )
        return response

    @app.get("/auth/github/callback", include_in_schema=False)
    def github_callback(request: Request) -> Response:
        """Validate state, exchange code server-side and establish the owner session."""

        if (
            config.mode != WEB_AUTH_GITHUB
            or state_store is None
            or codec is None
            or actual_gateway is None
        ):
            return _auth_error_page(
                index_file,
                status_code=503,
                kind="oauth",
                auth_mode=config.mode,
            )
        state = _single_query_param(request, "state", max_bytes=MAX_OAUTH_STATE_BYTES)
        state_cookie = _single_cookie(
            request.scope,
            OAUTH_STATE_COOKIE_NAME,
            max_bytes=MAX_OAUTH_STATE_BYTES,
        )
        state_is_valid = state_store.consume(state)
        if (
            not state_is_valid
            or state is None
            or state_cookie is None
            or not hmac.compare_digest(state, state_cookie)
        ):
            return _error_with_state_clear(
                index_file,
                status_code=400,
                kind="oauth",
                auth_mode=config.mode,
                secure=config.secure_cookies,
            )

        provider_error = _single_query_param(request, "error", max_bytes=MAX_OAUTH_ERROR_BYTES)
        if provider_error is not None:
            return _error_with_state_clear(
                index_file,
                status_code=400,
                kind="oauth",
                auth_mode=config.mode,
                secure=config.secure_cookies,
            )
        code = _single_query_param(request, "code", max_bytes=MAX_OAUTH_CODE_BYTES)
        if code is None:
            return _error_with_state_clear(
                index_file,
                status_code=400,
                kind="oauth",
                auth_mode=config.mode,
                secure=config.secure_cookies,
            )

        access_token: str | None = None
        try:
            access_token = actual_gateway.exchange_code(code, config.callback_url)
            if not _valid_secret_text(access_token, max_bytes=4096):
                raise GitHubOAuthError
            identity = actual_gateway.get_authenticated_user(access_token)
        except Exception:
            return _error_with_state_clear(
                index_file,
                status_code=502,
                kind="oauth",
                auth_mode=config.mode,
                secure=config.secure_cookies,
            )
        finally:
            access_token = None

        if not isinstance(identity, GitHubIdentity) or type(identity.user_id) is not int:
            return _error_with_state_clear(
                index_file,
                status_code=502,
                kind="oauth",
                auth_mode=config.mode,
                secure=config.secure_cookies,
            )
        if config.allowed_user_id is None or not hmac.compare_digest(
            str(identity.user_id), config.allowed_user_id
        ):
            return _error_with_state_clear(
                index_file,
                status_code=403,
                kind="denied",
                auth_mode=config.mode,
                secure=config.secure_cookies,
            )

        session_value = codec.issue(config.allowed_user_id)
        response = _redirect("/")
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_value,
            max_age=config.session_ttl_seconds,
            secure=config.secure_cookies,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return _with_deleted_cookie(
            response,
            name=OAUTH_STATE_COOKIE_NAME,
            secure=config.secure_cookies,
            path="/auth/github/callback",
        )

    @app.get("/auth/logout", include_in_schema=False)
    def logout() -> Response:
        """Clear the local session and return to the login surface."""

        response = _redirect("/login")
        return _with_deleted_cookie(
            response,
            name=SESSION_COOKIE_NAME,
            secure=config.secure_cookies,
            path="/",
        )


__all__ = [
    "AUTH_MODE_APP_STATE_KEY",
    "AUTH_SECURITY_HEADERS",
    "AUTH_TRUSTED_AUTHORITIES_SCOPE_KEY",
    "AUTH_USER_ID_SCOPE_KEY",
    "DEFAULT_OAUTH_STATE_TTL_SECONDS",
    "DEFAULT_SESSION_TTL_SECONDS",
    "GITHUB_AUTHORIZE_URL",
    "GITHUB_DISPLAY_LOGIN",
    "GITHUB_TOKEN_URL",
    "GITHUB_USER_URL",
    "MAX_OAUTH_CODE_BYTES",
    "MAX_OAUTH_STATE_BYTES",
    "SESSION_COOKIE_NAME",
    "WEB_AUTH_DISABLED",
    "WEB_AUTH_GITHUB",
    "GitHubIdentity",
    "GitHubOAuthClient",
    "GitHubOAuthError",
    "GitHubOAuthGateway",
    "OAuthStateCapacityError",
    "OAuthStateStore",
    "SignedSessionCodec",
    "WebAuthConfig",
    "WebAuthEnvironmentSettings",
    "WebAuthMiddleware",
    "WebAuthMode",
    "configured_authority_port",
    "install_web_auth",
    "load_web_auth_config",
    "parse_github_identity",
    "request_host_is_trusted",
    "same_origin_is_trusted",
    "trusted_authorities_from_scope",
    "web_index_response",
]
