"""Детерминированные проверки tracked production Web deployment artifacts."""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
UNIT_PATH = PROJECT_ROOT / "deploy" / "systemd" / "second-brain-web.service"
CADDY_PATH = PROJECT_ROOT / "deploy" / "caddy" / "brain.mikemoore.top.caddy"
CADDY_TEMPLATE_PATH = PROJECT_ROOT / "deploy" / "caddy" / "Caddyfile.example"
RUNBOOK_PATH = PROJECT_ROOT / "docs" / "deployment" / "web-production.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fenced_code(markdown: str) -> str:
    return "\n".join(re.findall(r"```[^\n]*\n(.*?)```", markdown, flags=re.DOTALL))


def test_systemd_template_is_loopback_non_root_and_secret_free() -> None:
    unit = _read(UNIT_PATH)

    assert "User=second-brain" in unit
    assert "Group=second-brain" in unit
    assert "User=root" not in unit
    assert "WorkingDirectory=/srv/second-brain/current" in unit
    assert "WorkingDirectory=/srv/second-brain/second-brain" not in unit
    assert "EnvironmentFile=/srv/second-brain/runtime/web.env" in unit
    assert (
        "ExecStart=/usr/local/bin/uv run --python 3.14 --no-sync second-brain "
        "--env-file /srv/second-brain/runtime/web.env web serve --port 8123"
    ) in unit
    assert "127.0.0.1:8123" in unit
    assert "0.0.0.0" not in unit
    assert "--host" not in unit
    assert "Restart=on-failure" in unit
    assert "KillSignal=SIGTERM" in unit
    assert "TimeoutStopSec=30s" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=full" in unit
    assert "ProtectHome=read-only" in unit
    assert "SECOND_BRAIN_GITHUB_CLIENT_SECRET=" not in unit
    assert "SECOND_BRAIN_SESSION_SECRET=" not in unit
    assert "CLOUDFLARE_API_TOKEN=" not in unit
    assert "ghp_" not in unit
    assert "github_pat_" not in unit


def test_caddy_template_has_exact_https_boundary_and_oauth_log_policy() -> None:
    caddy = _read(CADDY_PATH)

    assert not CADDY_TEMPLATE_PATH.exists()
    assert re.search(r"(?m)^https://brain\.mikemoore\.top:8444\s*\{", caddy)
    assert re.search(r"(?m)^http://brain\.mikemoore\.top\s*\{", caddy)
    assert not re.search(r"(?m)^brain\.mikemoore\.top\s*\{", caddy)
    assert "reverse_proxy 127.0.0.1:8123" in caddy
    assert "log_skip /auth/github*" in caddy
    assert "output stderr" in caddy
    assert "format json" in caddy
    assert "redir https://brain.mikemoore.top{uri} 308" in caddy
    assert "redir https://brain.mikemoore.top:8444" not in caddy
    assert (
        "tls /etc/caddy/certs/brain.mikemoore.top.pem /etc/caddy/certs/brain.mikemoore.top.key"
    ) in caddy
    assert "https_port" not in caddy
    assert not re.search(r"(?m)^\s*\*\.", caddy)
    assert "tls_insecure_skip_verify" not in caddy
    assert "tls internal" not in caddy.casefold()
    assert "Access-Control-Allow-Origin" not in caddy
    assert "allow_origins" not in caddy
    assert "cloudflare" not in caddy.casefold()
    assert "0.0.0.0" not in caddy


def test_runbook_records_current_cli_build_oauth_and_external_boundaries() -> None:
    runbook = _read(RUNBOOK_PATH)

    for required in (
        "https://brain.mikemoore.top",
        "https://brain.mikemoore.top/auth/github/callback",
        "Cloudflare edge HTTPS :443",
        "site-scoped",
        "https://brain.mikemoore.top:8444",
        "http://brain.mikemoore.top",
        "redir https://brain.mikemoore.top{uri} 308",
        "global Caddy options",
        "другие Caddy sites",
        "explicit portless HTTP-to-HTTPS redirect",
        ":8444",
        ":80",
        'http.host eq "brain.mikemoore.top" and ssl',
        '(http.host eq "brain.mikemoore.top" and ssl)',
        "Destination port",
        "Host header override",
        "SNI override",
        "Full (strict)",
        "Flexible SSL",
        "/etc/caddy/certs/brain.mikemoore.top.pem",
        "/etc/caddy/certs/brain.mikemoore.top.key",
        "Origin CA",
        "Xray",
        "x-ui",
        ":2096",
        ":25566",
        "Reminder Bot",
        "UFW",
        "firewall hardening",
        "uv sync --locked --python 3.14",
        'RELEASES_ROOT="$SECOND_BRAIN_ROOT/releases"',
        "current -> releases/<ACTIVE_SHA>",
        'CANDIDATE_RELEASE="$RELEASES_ROOT/$APP_SHA"',
        'git -C "$APP_ROOT" worktree add --detach',
        'git -C "$CANDIDATE_RELEASE"',
        "releases/<SHA>",
        "mv -T",
        "PREVIOUS_KNOWN_GOOD_SHA",
        "npm ci",
        "npm run check",
        "npm run build",
        "web/dist",
        "second-brain web serve",
        "doctor",
        "vault validate",
        "systemd-analyze verify",
        "caddy validate",
        "brain.mikemoore.top.caddy",
        "CADDY_SITE_DIR",
        "CADDY_IMPORT_GLOB",
        'sudoedit "$CADDY_CONFIG"',
        "import /etc/caddy/sites.d/*.caddy",
        "preserve-existing",
        "existing config",
        "systemctl reload caddy",
        "127.0.0.1:8123",
        "A",
        "AAAA",
        "80",
        "443",
        "mtproxy",
        "Caddy/Nginx",
        "Docker/Compose",
        "PRODUCTION_AUTHENTICATED_SMOKE = PENDING_OWNER_EXTERNAL_SETUP",
        "CLOUDFLARE_CHANGED = NO",
        "XRAY_CHANGED = NO",
        "MTPROXY_CHANGED = NO",
        "FIREWALL_CHANGED = NO",
    ):
        assert required in runbook

    for variable_name in (
        "SECOND_BRAIN_VAULT_PATH",
        "SECOND_BRAIN_WEB_AUTH",
        "SECOND_BRAIN_PUBLIC_BASE_URL",
        "SECOND_BRAIN_GITHUB_CLIENT_ID",
        "SECOND_BRAIN_GITHUB_CLIENT_SECRET",
        "SECOND_BRAIN_GITHUB_ALLOWED_USER_ID",
        "SECOND_BRAIN_SESSION_SECRET",
        "SECOND_BRAIN_SESSION_TTL_SECONDS",
        "SECOND_BRAIN_OAUTH_STATE_TTL_SECONDS",
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
    ):
        assert variable_name in runbook

    assert "SECOND_BRAIN_GITHUB_CLIENT_SECRET=" not in runbook
    assert "SECOND_BRAIN_SESSION_SECRET=" not in runbook
    assert "CLOUDFLARE_API_TOKEN=" not in runbook
    assert "ghp_" not in runbook
    assert "github_pat_" not in runbook
    assert "https_port" not in runbook
    assert "PUBLIC_BASE_URL=https://brain.mikemoore.top:8444" not in runbook
    assert "Homepage URL: `https://brain.mikemoore.top:8444" not in runbook
    assert "Authorization callback URL:\n     `https://brain.mikemoore.top:8444" not in runbook
    assert "redir https://brain.mikemoore.top:8444" not in runbook
    assert "111.88.215.204" not in runbook


def test_runbook_code_blocks_do_not_offer_destructive_git_commands() -> None:
    code = _fenced_code(_read(RUNBOOK_PATH)).casefold()

    for forbidden in (
        "git pull",
        "git rebase",
        "git reset",
        "git clean",
        "git push --force",
        "git push -f",
    ):
        assert forbidden not in code


def test_runbook_does_not_mutate_protected_workloads_or_firewall() -> None:
    code = _fenced_code(_read(RUNBOOK_PATH)).casefold()

    for forbidden in (
        "systemctl stop xray",
        "systemctl restart xray",
        "systemctl reload xray",
        "systemctl stop x-ui",
        "systemctl restart x-ui",
        "systemctl stop mtproxy",
        "systemctl restart mtproxy",
        "docker stop",
        "docker restart",
        "docker compose down",
        "ufw enable",
        "iptables",
        "nft ",
    ):
        assert forbidden not in code


def test_release_and_caddy_procedures_are_preserve_by_default() -> None:
    runbook = _read(RUNBOOK_PATH)
    code = _fenced_code(runbook)

    assert "https_port" not in runbook
    assert "https://brain.mikemoore.top:8444 {" in code
    assert "http://brain.mikemoore.top {" in code
    assert "redir https://brain.mikemoore.top{uri} 308" in code
    assert 'git -C "$APP_ROOT" worktree add --detach "$CANDIDATE_RELEASE" "$APP_SHA"' in code
    assert 'cd "$CANDIDATE_RELEASE/web"' in code
    assert 'cd "$APP_ROOT/web"' not in code
    assert "WorkingDirectory=/srv/second-brain/current" in runbook
    assert 'sudo mv -T -- "$SWITCH_LINK" "$CURRENT_LINK"' in code
    assert 'sudo mv -T -- "$ROLLBACK_LINK" "$CURRENT_LINK"' in code
    assert "sudo install --owner=root --group=root --mode=0644" in code
    assert '"$CANDIDATE_RELEASE/deploy/caddy/brain.mikemoore.top.caddy"' in code
    assert (
        re.search(
            r"(?m)^\s*sudo\s+install\b.*(?:/etc/caddy/Caddyfile|\"\$CADDY_CONFIG\")",
            code,
        )
        is None
    )
    assert 'sudo caddy validate --config "$CADDY_CONFIG" --adapter caddyfile' in code
    assert code.index(
        'sudo caddy validate --config "$CADDY_CONFIG" --adapter caddyfile'
    ) < code.index("sudo systemctl reload caddy")
    assert "current exists but is not a symlink; stop" in runbook
    assert "Не удаляйте failed candidate или previous release автоматически" in runbook


def test_tracked_deployment_examples_contain_no_known_secret_values() -> None:
    paths = (
        UNIT_PATH,
        CADDY_PATH,
        RUNBOOK_PATH,
        PROJECT_ROOT / "deploy" / "env.example",
        PROJECT_ROOT / ".env.example",
    )
    known_secret_markers = (
        "ghp_",
        "github_pat_",
        "-----begin ",
        "client-secret-value",
        "session-secret-value",
    )

    for path in paths:
        content = _read(path).casefold()
        for marker in known_secret_markers:
            assert marker not in content
