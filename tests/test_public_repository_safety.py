"""Static guardrails for publishing this repository safely."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW_SUFFIXES = (".yml", ".yaml")
IMMUTABLE_ACTION = re.compile(
    r"^\s*uses:\s+\S+@[0-9a-f]{40}(?:\s+#.*)?$",
)


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _workflow_paths(root: Path = WORKFLOWS) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (path for suffix in WORKFLOW_SUFFIXES for path in root.glob(f"*{suffix}")),
            key=str,
        ),
    )


def _assert_workflow_actions_are_pinned(paths: tuple[Path, ...]) -> None:
    action_lines: list[str] = []
    for path in paths:
        action_lines.extend(
            line for line in path.read_text(encoding="utf-8").splitlines() if "uses:" in line
        )

    assert action_lines
    assert all(IMMUTABLE_ACTION.fullmatch(line) for line in action_lines), action_lines


def test_all_workflow_actions_use_immutable_commit_pins() -> None:
    _assert_workflow_actions_are_pinned(_workflow_paths())


def test_yaml_workflow_with_mutable_action_ref_fails_guard(tmp_path: Path) -> None:
    workflow = tmp_path / "mutable.yaml"
    workflow.write_text(
        "name: fixture\njobs:\n  check:\n    steps:\n      - uses: actions/checkout@v4\n",
        encoding="utf-8",
    )

    with pytest.raises(AssertionError):
        _assert_workflow_actions_are_pinned(_workflow_paths(tmp_path))


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
