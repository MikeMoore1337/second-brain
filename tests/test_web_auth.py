"""Детерминированные проверки single-user GitHub OAuth Web boundary."""

from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from second_brain.config import ConfigurationError
from second_brain.entrypoints.web import app as web_app
from second_brain.entrypoints.web.app import create_app
from second_brain.entrypoints.web.auth import (
    DEFAULT_OAUTH_STATE_TTL_SECONDS,
    DEFAULT_SESSION_TTL_SECONDS,
    GITHUB_TOKEN_URL,
    GITHUB_USER_URL,
    MAX_PROVIDER_BODY_BYTES,
    OAUTH_STATE_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    GitHubIdentity,
    GitHubOAuthClient,
    GitHubOAuthError,
    OAuthStateCapacityError,
    OAuthStateStore,
    SignedSessionCodec,
    WebAuthConfig,
    load_web_auth_config,
    parse_github_identity,
)

OWNER_ID = "42142321"
BASE_URL = "https://brain.example.test"
CLIENT_ID = "github-client-id"
CLIENT_SECRET = "github-client-secret"
SESSION_SECRET = "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY"
FIXED_NOW = 1_800_000_000.0


@dataclass
class MutableClock:
    value: float = FIXED_NOW

    def __call__(self) -> float:
        return self.value


@dataclass
class FakeGateway:
    identity: object = field(
        default_factory=lambda: GitHubIdentity(user_id=int(OWNER_ID), login="MikeMoore1337")
    )
    token: str = "ephemeral-github-token"
    fail_exchange: bool = False
    fail_identity: bool = False
    exchange_calls: list[tuple[str, str]] = field(default_factory=list)
    user_calls: list[str] = field(default_factory=list)

    def exchange_code(self, code: str, redirect_uri: str) -> str:
        self.exchange_calls.append((code, redirect_uri))
        if self.fail_exchange:
            raise GitHubOAuthError
        return self.token

    def get_authenticated_user(self, access_token: str) -> GitHubIdentity:
        self.user_calls.append(access_token)
        if self.fail_identity:
            raise GitHubOAuthError
        return cast(GitHubIdentity, self.identity)


@dataclass
class FakeProviderResponse:
    status: int
    body: bytes

    def read(self, amount: int = -1) -> bytes:
        return self.body if amount < 0 else self.body[:amount]

    def __enter__(self) -> FakeProviderResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc_value, traceback


def _github_values() -> dict[str, object]:
    return {
        "SECOND_BRAIN_WEB_AUTH": "github",
        "SECOND_BRAIN_PUBLIC_BASE_URL": BASE_URL,
        "SECOND_BRAIN_GITHUB_CLIENT_ID": CLIENT_ID,
        "SECOND_BRAIN_GITHUB_CLIENT_SECRET": CLIENT_SECRET,
        "SECOND_BRAIN_GITHUB_ALLOWED_USER_ID": OWNER_ID,
        "SECOND_BRAIN_SESSION_SECRET": SESSION_SECRET,
        "SECOND_BRAIN_SESSION_TTL_SECONDS": DEFAULT_SESSION_TTL_SECONDS,
        "SECOND_BRAIN_OAUTH_STATE_TTL_SECONDS": DEFAULT_OAUTH_STATE_TTL_SECONDS,
    }


def _write_env(path: Path, values: dict[str, object]) -> Path:
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    return path


def _github_config(tmp_path: Path, **overrides: object) -> WebAuthConfig:
    values = _github_values()
    values.update(overrides)
    return load_web_auth_config(env_file=_write_env(tmp_path / "auth.env", values))


def _build_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gateway: FakeGateway,
    clock: MutableClock,
    *,
    config: WebAuthConfig | None = None,
    search_service: object | None = None,
) -> Any:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        '<!doctype html><html lang="ru"><head><title>Second Brain</title></head>'
        '<body><div id="root"></div><script src="/assets/index.js"></script></body></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr(web_app, "REACT_INDEX_FILE", dist / "index.html")
    monkeypatch.setattr(web_app, "REACT_ASSETS_DIR", tmp_path / "assets")
    kwargs: dict[str, object] = {
        "web_auth_config": config or _github_config(tmp_path),
        "web_auth_gateway": gateway,
        "web_auth_clock": clock,
    }
    if search_service is not None:
        kwargs["search_service"] = search_service
    return create_app(**kwargs)


def _oauth_start(client: TestClient) -> tuple[Any, str, dict[str, list[str]]]:
    response = client.get("/auth/github/login", follow_redirects=False)
    location = response.headers["location"]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
    return response, query["state"][0], query


def _callback_url(state: str, **params: str) -> str:
    return "/auth/github/callback?" + urllib.parse.urlencode({"state": state, **params})


def test_github_mode_redirects_private_surfaces_and_bounds_oauth_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = FakeGateway()
    clock = MutableClock()
    config = _github_config(tmp_path)
    with TestClient(
        _build_app(tmp_path, monkeypatch, gateway, clock, config=config),
        base_url=BASE_URL,
    ) as client:
        root = client.get("/", follow_redirects=False)
        malformed_session = client.get(
            "/",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}=not-a-session"},
            follow_redirects=False,
        )
        invalid_host = client.get(
            "/",
            headers={"Host": "evil.example.test"},
            follow_redirects=False,
        )
        private_api = client.get("/api/search")
        login = client.get("/login")
        healthz = client.get("/healthz")
        oauth, state, query = _oauth_start(client)

    assert root.status_code == 303
    assert root.headers["location"] == "/login"
    assert malformed_session.status_code == 303
    assert malformed_session.headers["location"] == "/login"
    assert invalid_host.status_code == 400
    assert invalid_host.text == "Invalid host header"
    assert private_api.status_code == 401
    assert private_api.json() == {"error": {"code": "AUTH_REQUIRED", "message": "Требуется вход"}}
    assert private_api.headers["cache-control"] == "no-store"
    assert login.status_code == 200
    assert 'id="root"' in login.text
    assert healthz.status_code == 200
    assert healthz.json() == {"status": "ok"}
    assert oauth.status_code == 303
    assert urllib.parse.urlsplit(oauth.headers["location"]).scheme == "https"
    assert urllib.parse.urlsplit(oauth.headers["location"]).netloc == "github.com"
    assert set(query) == {"client_id", "redirect_uri", "state", "allow_signup"}
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [config.callback_url]
    assert query["state"] == [state]
    assert query["allow_signup"] == ["false"]
    assert "scope" not in query
    assert CLIENT_SECRET not in oauth.headers["location"]
    state_cookie = oauth.headers["set-cookie"].lower()
    assert f"{OAUTH_STATE_COOKIE_NAME}=" in state_cookie
    assert "max-age=600" in state_cookie
    assert "httponly" in state_cookie
    assert "secure" in state_cookie
    assert "samesite=lax" in state_cookie
    assert "path=/auth/github/callback" in state_cookie


def test_successful_callback_uses_numeric_owner_id_and_preserves_existing_api_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = FakeGateway(identity=GitHubIdentity(user_id=int(OWNER_ID), login=None))
    clock = MutableClock()

    class EmptySearchService:
        def search(self, request: object) -> tuple[object, ...]:
            del request
            return ()

        def retrieve(self, note_id: object) -> object:
            del note_id
            raise AssertionError("retrieve is not part of this test")

    with TestClient(
        _build_app(
            tmp_path,
            monkeypatch,
            gateway,
            clock,
            search_service=EmptySearchService(),
        ),
        base_url=BASE_URL,
    ) as client:
        start, state, _ = _oauth_start(client)
        callback = client.get(
            _callback_url(state, code="one-time-code"),
            follow_redirects=False,
        )
        private_ui = client.get("/")
        search = client.post(
            "/api/search",
            json={"query": "memory", "limit": 1},
            headers={
                "Origin": BASE_URL,
                "X-Second-Brain-Request": "search-v1",
            },
        )
        wrong_origin = client.post(
            "/api/search",
            json={"query": "memory", "limit": 1},
            headers={
                "Origin": "https://evil.example.test",
                "X-Second-Brain-Request": "search-v1",
            },
        )

    assert start.status_code == 303
    assert callback.status_code == 303
    assert callback.headers["location"] == "/"
    session_cookie = callback.headers["set-cookie"].lower()
    assert f"{SESSION_COOKIE_NAME}=" in session_cookie
    assert "max-age=43200" in session_cookie
    assert "httponly" in session_cookie
    assert "secure" in session_cookie
    assert "samesite=lax" in session_cookie
    assert "path=/" in session_cookie
    assert gateway.exchange_calls == [
        ("one-time-code", "https://brain.example.test/auth/github/callback")
    ]
    assert gateway.user_calls == [gateway.token]
    assert private_ui.status_code == 200
    assert 'data-second-brain-auth-mode="github"' in private_ui.text
    assert search.status_code == 200
    assert search.json() == {"hits": []}
    assert wrong_origin.status_code == 400
    assert "one-time-code" not in callback.text
    assert gateway.token not in callback.text
    assert CLIENT_SECRET not in callback.text


def test_wrong_github_id_is_denied_without_a_session_or_identity_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = FakeGateway(identity=GitHubIdentity(user_id=999999, login="MikeMoore1337"))
    clock = MutableClock()
    with TestClient(
        _build_app(tmp_path, monkeypatch, gateway, clock),
        base_url=BASE_URL,
    ) as client:
        _, state, _ = _oauth_start(client)
        denied = client.get(
            _callback_url(state, code="code-for-wrong-owner"),
            follow_redirects=False,
        )
        root = client.get("/", follow_redirects=False)

    assert denied.status_code == 403
    assert 'data-second-brain-auth-error="denied"' in denied.text
    assert "Доступ закрыт" not in denied.text
    assert "999999" not in denied.text
    assert "code-for-wrong-owner" not in denied.text
    assert "second_brain_session=" not in denied.headers.get("set-cookie", "")
    assert root.status_code == 303


def test_oauth_state_provider_errors_and_replay_are_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = FakeGateway()
    clock = MutableClock()
    with TestClient(
        _build_app(tmp_path, monkeypatch, gateway, clock),
        base_url=BASE_URL,
    ) as client:
        missing = client.get("/auth/github/callback?code=missing-state")
        _, mismatch_state, _ = _oauth_start(client)
        mismatch = client.get(_callback_url("not-the-state", code="code"))
        _, provider_state, _ = _oauth_start(client)
        provider = client.get(_callback_url(provider_state, error="access_denied"))
        _, success_state, _ = _oauth_start(client)
        success = client.get(_callback_url(success_state, code="code"), follow_redirects=False)
        replay = client.get(_callback_url(success_state, code="code"))

    for response in (missing, mismatch, provider, replay):
        assert response.status_code == 400
        assert 'data-second-brain-auth-error="oauth"' in response.text
        assert "access_denied" not in response.text
        assert "missing-state" not in response.text
        assert "not-the-state" not in response.text
    assert mismatch_state != provider_state
    assert success.status_code == 303
    assert gateway.exchange_calls == [("code", "https://brain.example.test/auth/github/callback")]


@pytest.mark.parametrize(
    ("scenario", "status_code"),
    [
        ("exchange", 502),
        ("identity", 502),
        ("token", 502),
        ("payload", 502),
    ],
)
def test_provider_failures_have_fixed_public_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    status_code: int,
) -> None:
    gateway = FakeGateway(
        fail_exchange=scenario == "exchange",
        fail_identity=scenario == "identity",
        token="\n" if scenario == "token" else "ephemeral-github-token",
        identity=object() if scenario == "payload" else GitHubIdentity(int(OWNER_ID)),
    )
    clock = MutableClock()
    with TestClient(
        _build_app(tmp_path, monkeypatch, gateway, clock),
        base_url=BASE_URL,
    ) as client:
        _, state, _ = _oauth_start(client)
        response = client.get(_callback_url(state, code="provider-code"))

    assert response.status_code == status_code
    assert 'data-second-brain-auth-error="oauth"' in response.text
    assert "Не удалось выполнить вход" not in response.text
    assert "provider-code" not in response.text
    assert "ephemeral-github-token" not in response.text


def test_logout_and_session_codec_reject_tampering_and_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = FakeGateway()
    clock = MutableClock()
    config = _github_config(tmp_path)
    with TestClient(
        _build_app(tmp_path, monkeypatch, gateway, clock, config=config),
        base_url=BASE_URL,
    ) as client:
        _, state, _ = _oauth_start(client)
        callback = client.get(_callback_url(state, code="code"), follow_redirects=False)
        logout = client.get("/auth/logout", follow_redirects=False)
        root = client.get("/", follow_redirects=False)

    assert callback.status_code == 303
    assert logout.status_code == 303
    assert logout.headers["location"] == "/login"
    logout_cookie = logout.headers["set-cookie"].lower()
    assert f"{SESSION_COOKIE_NAME}=" in logout_cookie
    assert "max-age=0" in logout_cookie
    assert root.status_code == 303

    codec = SignedSessionCodec(
        secret=SESSION_SECRET.encode("ascii"),
        allowed_user_id=OWNER_ID,
        ttl_seconds=300,
        clock=clock,
    )
    token = codec.issue(OWNER_ID)
    payload_part = token.split(".")[1]
    payload = json.loads(
        base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4)).decode("ascii")
    )
    assert set(payload) == {"exp", "iat", "sub"}
    assert payload["sub"] == OWNER_ID
    assert "token" not in payload
    assert codec.decode(token) is not None
    altered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert codec.decode(altered) is None
    clock.value += 301
    assert codec.decode(token) is None
    assert codec.decode("not-a-session") is None


def test_state_store_is_one_time_bounded_and_expiring() -> None:
    clock = MutableClock()
    store = OAuthStateStore(ttl_seconds=60, clock=clock, max_entries=2)
    first = store.issue()
    second = store.issue()

    with pytest.raises(OAuthStateCapacityError):
        store.issue()
    assert store.consume(first)
    assert store.consume(second)
    assert not store.consume(second)
    fourth = store.issue()
    assert store.consume(fourth)
    expired = store.issue()
    clock.value += 61
    assert not store.consume(expired)


@pytest.mark.parametrize(
    "missing_name",
    [
        "SECOND_BRAIN_PUBLIC_BASE_URL",
        "SECOND_BRAIN_GITHUB_CLIENT_ID",
        "SECOND_BRAIN_GITHUB_CLIENT_SECRET",
        "SECOND_BRAIN_GITHUB_ALLOWED_USER_ID",
        "SECOND_BRAIN_SESSION_SECRET",
    ],
)
def test_github_mode_fails_closed_when_required_configuration_is_missing(
    tmp_path: Path,
    missing_name: str,
) -> None:
    values = _github_values()
    values[missing_name] = ""
    with pytest.raises(ConfigurationError) as error:
        load_web_auth_config(env_file=_write_env(tmp_path / "missing.env", values))
    assert CLIENT_SECRET not in str(error.value)
    assert SESSION_SECRET not in str(error.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SECOND_BRAIN_PUBLIC_BASE_URL", "http://brain.example.test"),
        ("SECOND_BRAIN_PUBLIC_BASE_URL", "https://brain.example.test/path"),
        ("SECOND_BRAIN_GITHUB_ALLOWED_USER_ID", "MikeMoore1337"),
        ("SECOND_BRAIN_GITHUB_ALLOWED_USER_ID", "0"),
        ("SECOND_BRAIN_SESSION_SECRET", "short"),
        ("SECOND_BRAIN_SESSION_TTL_SECONDS", 299),
        ("SECOND_BRAIN_OAUTH_STATE_TTL_SECONDS", 901),
    ],
)
def test_github_mode_rejects_unsafe_configuration(
    tmp_path: Path,
    name: str,
    value: object,
) -> None:
    values = _github_values()
    values[name] = value
    with pytest.raises(ConfigurationError):
        load_web_auth_config(env_file=_write_env(tmp_path / "invalid.env", values))


def test_disabled_mode_is_the_safe_local_default_and_has_no_real_oauth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_web_auth_config()
    assert config.mode == "disabled"
    assert config.public_base_url is None
    assert config.secure_cookies is False

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>local</body></html>", encoding="utf-8")
    monkeypatch.setattr(web_app, "REACT_INDEX_FILE", dist / "index.html")
    with TestClient(create_app(web_auth_config=config), base_url="http://127.0.0.1:8123") as client:
        response = client.get("/auth/github/login")
        root = client.get("/")
    assert response.status_code == 503
    assert "github.com" not in response.text
    assert "Не удалось выполнить вход" not in response.text
    assert root.status_code == 200
    assert 'data-second-brain-auth-mode="disabled"' in root.text


def test_github_client_uses_fixed_https_endpoints_and_bounded_provider_bodies() -> None:
    requests: list[urllib.request.Request] = []
    responses = [
        FakeProviderResponse(200, b'{"access_token":"provider-token"}'),
        FakeProviderResponse(200, b'{"id":42142321,"login":"MikeMoore1337"}'),
    ]

    def opener(request: urllib.request.Request, *, timeout: float) -> FakeProviderResponse:
        assert timeout == 10.0
        requests.append(request)
        return responses.pop(0)

    client = GitHubOAuthClient(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        opener=opener,
    )
    token = client.exchange_code("one-time-code", f"{BASE_URL}/auth/github/callback")
    identity = client.get_authenticated_user(token)

    assert token == "provider-token"
    assert identity == GitHubIdentity(user_id=int(OWNER_ID), login="MikeMoore1337")
    assert [request.full_url for request in requests] == [GITHUB_TOKEN_URL, GITHUB_USER_URL]
    assert CLIENT_SECRET not in requests[0].full_url
    assert CLIENT_SECRET.encode("ascii") in cast(bytes, requests[0].data)
    assert requests[1].get_header("Authorization") == "Bearer provider-token"

    oversized = GitHubOAuthClient(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        opener=lambda request, *, timeout: FakeProviderResponse(
            200,
            b"x" * (MAX_PROVIDER_BODY_BYTES + 1),
        ),
    )
    with pytest.raises(GitHubOAuthError):
        oversized.exchange_code("code", f"{BASE_URL}/auth/github/callback")


def test_github_identity_parser_accepts_only_a_stable_numeric_id() -> None:
    assert parse_github_identity({"id": int(OWNER_ID)}) == GitHubIdentity(int(OWNER_ID))
    with pytest.raises(GitHubOAuthError):
        parse_github_identity({"id": "42142321"})
    with pytest.raises(GitHubOAuthError):
        parse_github_identity({"id": 0})
