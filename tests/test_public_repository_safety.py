"""Static guardrails for publishing this repository safely."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
IMMUTABLE_ACTION = re.compile(
    r"^\s*uses:\s+\S+@[0-9a-f]{40}(?:\s+#.*)?$",
)


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_all_workflow_actions_use_immutable_commit_pins() -> None:
    action_lines: list[str] = []
    for path in WORKFLOWS.glob("*.yml"):
        action_lines.extend(
            line for line in path.read_text(encoding="utf-8").splitlines() if "uses:" in line
        )

    assert action_lines
    assert all(IMMUTABLE_ACTION.fullmatch(line) for line in action_lines), action_lines


def test_pull_request_ci_is_unprivileged() -> None:
    ci = _workflow("ci.yml")

    assert re.search(r"^\s{2}pull_request:\s*$", ci, re.MULTILINE)
    assert "permissions:\n  contents: read" in ci
    assert "pull_request_target" not in ci
    assert "secrets." not in ci


def test_production_deploy_requires_trusted_main_push_after_ci() -> None:
    deploy = _workflow("deploy-production.yml")

    for required_expression in (
        "vars.PRODUCTION_DEPLOY_ENABLED == 'true'",
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.event == 'push'",
        "github.event.workflow_run.head_branch == 'main'",
        "github.event.workflow_run.head_repository.full_name == github.repository",
    ):
        assert required_expression in deploy

    assert "environment: production" in deploy
    assert "persist-credentials: false" in deploy
    assert "pull_request_target" not in deploy
    assert "secrets.PRODUCTION_SSH_PRIVATE_KEY" in deploy


def test_ignore_policy_excludes_private_runtime_and_transient_artifacts() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    for required_rule in ("*.env", "data/vault/", "runtime/", "*.sqlite-journal"):
        assert required_rule in gitignore
