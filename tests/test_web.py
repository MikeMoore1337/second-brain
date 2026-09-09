"""Deterministic tests for the local Web GUI foundation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.main import get_command
from typer.testing import CliRunner

from second_brain.entrypoints.cli.app import app
from second_brain.entrypoints.web import app as web_app
from second_brain.entrypoints.web.app import create_app

runner = CliRunner()
LOOPBACK_BASE_URL = "http://127.0.0.1"


def test_create_app_needs_no_vault_or_provider_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SECOND_BRAIN_VAULT_PATH", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    application = create_app()

    assert application.title == "Second Brain"


def test_root_is_the_react_shell_with_local_assets_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        '<!doctype html><html lang="ru"><head><title>Second Brain React</title></head>'
        '<body><div id="root"></div><script type="module" '
        'src="/assets/index.js"></script></body></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr(web_app, "REACT_INDEX_FILE", dist / "index.html")

    with TestClient(create_app(), base_url=LOOPBACK_BASE_URL) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "charset=utf-8" in response.headers["content-type"]
    html = response.content.decode("utf-8")
    assert "Second Brain" in html
    assert "Second Brain React" in html
    assert 'id="root"' in html
    assert "/static/" not in html
    assert "http://" not in html
    assert "https://" not in html


def test_react_build_is_served_at_root_and_compatibility_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html lang="ru"><body>React parity</body></html>',
        encoding="utf-8",
    )
    (assets / "index.js").write_text("console.log('foundation');", encoding="utf-8")
    monkeypatch.setattr(web_app, "REACT_INDEX_FILE", dist / "index.html")
    monkeypatch.setattr(web_app, "REACT_ASSETS_DIR", assets)

    with TestClient(create_app(), base_url=LOOPBACK_BASE_URL) as client:
        root = client.get("/")
        react = client.get("/react/")
        asset_at_root = client.get("/assets/index.js")
        asset = client.get("/react/assets/index.js")

    assert root.status_code == 200
    assert "React parity" in root.text
    assert react.status_code == 200
    assert "React parity" in react.text
    assert asset_at_root.status_code == 200
    assert asset_at_root.text == "console.log('foundation');"
    assert asset.status_code == 200
    assert asset.text == "console.log('foundation');"


def test_healthz_returns_exact_safe_json() -> None:
    with TestClient(create_app(), base_url=LOOPBACK_BASE_URL) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.content == b'{"status":"ok"}'
    assert response.json() == {"status": "ok"}


def test_legacy_static_entrypoint_is_not_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    with TestClient(create_app(), base_url=LOOPBACK_BASE_URL) as client:
        css = client.get("/static/app.css")
        javascript = client.get("/static/app.js")
        simulate_me_javascript = client.get("/static/simulate-me.js")

    assert css.status_code == 404
    assert javascript.status_code == 404
    assert simulate_me_javascript.status_code == 404


def test_static_serving_has_no_directory_listing_or_traversal() -> None:
    with TestClient(create_app(), base_url=LOOPBACK_BASE_URL) as client:
        directory = client.get("/static/")
        traversal = client.get("/static/%2e%2e/%2e%2e/pyproject.toml")
        unknown = client.get("/not-a-route")

    assert directory.status_code == 404
    assert traversal.status_code == 404
    assert unknown.status_code == 404


@pytest.mark.parametrize("path", ["/", "/static/app.css"])
def test_html_and_static_responses_have_security_headers(path: str) -> None:
    with TestClient(create_app(), base_url=LOOPBACK_BASE_URL) as client:
        response = client.get(path)

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == (
        "default-src 'self'; base-uri 'none'; connect-src 'self'; font-src 'self'; "
        "form-action 'none'; frame-ancestors 'none'; img-src 'self'; object-src 'none'; "
        "script-src 'self'; style-src 'self'"
    )


def test_web_serve_uses_fixed_loopback_and_fake_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(application: object, **kwargs: Any) -> None:
        calls.append({"application": application, **kwargs})

    monkeypatch.setattr("second_brain.entrypoints.cli.app.uvicorn.run", fake_run)

    result = runner.invoke(app, ["web", "serve", "--port", "8000"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8000
    assert calls[0]["access_log"] is False
    assert "factory" not in calls[0]
    assert getattr(calls[0]["application"], "title", None) == "Second Brain"


def test_web_serve_defaults_to_second_brain_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(application: object, **kwargs: Any) -> None:
        calls.append({"application": application, **kwargs})

    monkeypatch.setattr("second_brain.entrypoints.cli.app.uvicorn.run", fake_run)

    result = runner.invoke(app, ["web", "serve"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8123
    assert calls[0]["access_log"] is False


def test_web_serve_forwards_global_vault_options_to_app_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_calls: list[dict[str, object]] = []
    runner_calls: list[dict[str, object]] = []
    sentinel = object()
    env_file = tmp_path / ".env"

    def fake_create_app(**kwargs: object) -> object:
        app_calls.append(kwargs)
        return sentinel

    def fake_run(application: object, **kwargs: object) -> None:
        runner_calls.append({"application": application, **kwargs})

    monkeypatch.setattr("second_brain.entrypoints.cli.app.create_app", fake_create_app)
    monkeypatch.setattr("second_brain.entrypoints.cli.app.uvicorn.run", fake_run)

    result = runner.invoke(
        app,
        [
            "--env-file",
            str(env_file),
            "--vault-path",
            "relative-vault",
            "web",
            "serve",
            "--port",
            "8123",
        ],
    )

    assert result.exit_code == 0
    assert app_calls == [{"env_file": env_file, "vault_path_override": "relative-vault"}]
    assert runner_calls == [
        {"application": sentinel, "host": "127.0.0.1", "port": 8123, "access_log": False}
    ]


@pytest.mark.parametrize("port", ["0", "65536", "not-a-port"])
def test_web_serve_rejects_invalid_port(port: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr("second_brain.entrypoints.cli.app.uvicorn.run", calls.append)

    result = runner.invoke(app, ["web", "serve", "--port", port])

    assert result.exit_code == 2
    assert calls == []


def test_web_serve_does_not_offer_a_public_host_option() -> None:
    root_command: Any = get_command(app)
    web_command = root_command.commands["web"]
    serve_command = web_command.commands["serve"]
    option_names = {
        option_name
        for parameter in serve_command.params
        for option_name in getattr(parameter, "opts", ())
    }

    assert option_names == {"--port"}
