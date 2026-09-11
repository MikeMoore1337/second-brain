"""Детерминированные проверки installable PWA shell и его auth/cache boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from second_brain.entrypoints.web import app as web_app
from second_brain.entrypoints.web.app import create_app
from second_brain.entrypoints.web.auth import (
    DEFAULT_OAUTH_STATE_TTL_SECONDS,
    DEFAULT_SESSION_TTL_SECONDS,
    WebAuthConfig,
    load_web_auth_config,
)

LOOPBACK_BASE_URL = "http://127.0.0.1"
GITHUB_BASE_URL = "https://brain.example.test"


def _disabled_auth_config() -> WebAuthConfig:
    return WebAuthConfig(
        mode="disabled",
        public_base_url=None,
        github_client_id=None,
        github_client_secret=None,
        allowed_user_id=None,
        session_secret=None,
        session_ttl_seconds=DEFAULT_SESSION_TTL_SECONDS,
        oauth_state_ttl_seconds=DEFAULT_OAUTH_STATE_TTL_SECONDS,
        trusted_authorities=frozenset(),
    )


def _github_auth_config(tmp_path: Path) -> WebAuthConfig:
    env_file = tmp_path / "auth.env"
    env_file.write_text(
        "\n".join(
            [
                "SECOND_BRAIN_WEB_AUTH=github",
                f"SECOND_BRAIN_PUBLIC_BASE_URL={GITHUB_BASE_URL}",
                "SECOND_BRAIN_GITHUB_CLIENT_ID=test-client-id",
                "SECOND_BRAIN_GITHUB_CLIENT_SECRET=test-client-secret",
                "SECOND_BRAIN_GITHUB_ALLOWED_USER_ID=42142321",
                "SECOND_BRAIN_SESSION_SECRET=test-session-secret-not-a-credential-000000",
                f"SECOND_BRAIN_SESSION_TTL_SECONDS={DEFAULT_SESSION_TTL_SECONDS}",
                f"SECOND_BRAIN_OAUTH_STATE_TTL_SECONDS={DEFAULT_OAUTH_STATE_TTL_SECONDS}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return load_web_auth_config(env_file=env_file)


def _prepare_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dist = tmp_path / "dist"
    icons = dist / "icons"
    assets = dist / "assets"
    icons.mkdir(parents=True)
    assets.mkdir()
    (dist / "index.html").write_text(
        '<!doctype html><html lang="ru"><head><title>Second Brain</title></head>'
        '<body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    (assets / "index.js").write_text("console.log('shell');", encoding="utf-8")
    pwa_files = {
        "manifest.webmanifest": '{"name":"Second Brain"}',
        "sw.js": "self.addEventListener('fetch', () => undefined);",
        "offline.html": "<!doctype html><h1>Сеть недоступна</h1>",
        "offline.css": "body { color: #c49aff; }",
    }
    for name, content in pwa_files.items():
        (dist / name).write_text(content, encoding="utf-8")
    (icons / "pwa-192.png").write_bytes(b"png-test")

    monkeypatch.setattr(web_app, "REACT_INDEX_FILE", dist / "index.html")
    monkeypatch.setattr(web_app, "REACT_ASSETS_DIR", assets)
    monkeypatch.setattr(web_app, "REACT_PWA_MANIFEST_FILE", dist / "manifest.webmanifest")
    monkeypatch.setattr(web_app, "REACT_PWA_SERVICE_WORKER_FILE", dist / "sw.js")
    monkeypatch.setattr(web_app, "REACT_PWA_OFFLINE_FILE", dist / "offline.html")
    monkeypatch.setattr(web_app, "REACT_PWA_OFFLINE_STYLES_FILE", dist / "offline.css")
    monkeypatch.setattr(web_app, "REACT_PWA_ICONS_DIR", icons)
    return dist


def test_disabled_auth_serves_only_static_pwa_files_with_safe_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_dist(tmp_path, monkeypatch)

    with TestClient(
        create_app(web_auth_config=_disabled_auth_config()),
        base_url=LOOPBACK_BASE_URL,
    ) as client:
        manifest = client.get("/manifest.webmanifest")
        worker = client.get("/sw.js")
        offline = client.get("/offline.html")
        styles = client.get("/offline.css")
        icon = client.get("/icons/pwa-192.png")
        api = client.post("/api/search", json={})

    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.headers["cache-control"] == "no-cache"
    assert worker.status_code == 200
    assert worker.headers["content-type"].startswith("application/javascript")
    assert worker.headers["cache-control"] == "no-cache"
    assert offline.status_code == 200
    assert offline.headers["content-type"].startswith("text/html")
    assert styles.status_code == 200
    assert styles.headers["content-type"].startswith("text/css")
    assert icon.status_code == 200
    assert icon.content == b"png-test"
    assert icon.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert api.status_code == 400
    assert api.headers["cache-control"] == "no-store"


def test_github_auth_keeps_pwa_assets_public_but_private_shell_and_api_protected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_dist(tmp_path, monkeypatch)

    with TestClient(
        create_app(web_auth_config=_github_auth_config(tmp_path)),
        base_url=GITHUB_BASE_URL,
    ) as client:
        manifest = client.get("/manifest.webmanifest")
        icon = client.get("/icons/pwa-192.png")
        root = client.get("/", follow_redirects=False)
        api = client.get("/api/search")

    assert manifest.status_code == 200
    assert icon.status_code == 200
    assert root.status_code == 303
    assert root.headers["location"] == "/login"
    assert api.status_code == 401
    assert api.headers["cache-control"] == "no-store"
    assert api.json() == {"error": {"code": "AUTH_REQUIRED", "message": "Требуется вход"}}


def test_missing_pwa_file_fails_closed_without_a_cacheable_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_dist(tmp_path, monkeypatch)
    missing = tmp_path / "not-built" / "sw.js"
    monkeypatch.setattr(web_app, "REACT_PWA_SERVICE_WORKER_FILE", missing)

    with TestClient(
        create_app(web_auth_config=_disabled_auth_config()),
        base_url=LOOPBACK_BASE_URL,
    ) as client:
        response = client.get("/sw.js")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.text == "PWA-ресурс доступен после сборки React-интерфейса."
