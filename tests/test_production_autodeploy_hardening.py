"""Детерминированные проверки production autodeploy hardening v1."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[1]
DEPLOY_ROOT = PROJECT_ROOT / "deploy"
AUTODEPLOY_PATH = DEPLOY_ROOT / "autodeploy.sh"
ENV_PREFLIGHT_PATH = DEPLOY_ROOT / "production-env-preflight.sh"
CANDIDATE_CHECK_PATH = DEPLOY_ROOT / "candidate-recovery-check.sh"
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "deploy-production.yml"
RUNBOOK_PATH = PROJECT_ROOT / "docs" / "deployment" / "autodeploy.md"
REQUIREMENTS_PATH = DEPLOY_ROOT / "production-env-requirements.conf"


def _run_git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _bash_path() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    for candidate in (
        Path("C:/Program Files/Git/bin/bash.exe"),
        Path("C:/Program Files/Git/usr/bin/bash.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _run_bash(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    bash = _bash_path()
    if bash is None:
        pytest.skip("bash is required for shell helper integration tests")
    return subprocess.run(
        [bash, str(script), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _init_seed_repository(tmp_path: Path, contract: str) -> tuple[Path, str]:
    seed = tmp_path / "seed"
    seed.mkdir()
    _run_git(seed, "init", "-b", "main")
    _run_git(seed, "config", "user.name", "Autodeploy Test")
    _run_git(seed, "config", "user.email", "autodeploy@example.invalid")
    (seed / "deploy").mkdir()
    (seed / "deploy" / "production-env-requirements.conf").write_text(
        contract,
        encoding="utf-8",
    )
    _run_git(seed, "add", "--", "deploy/production-env-requirements.conf")
    _run_git(seed, "commit", "-m", "test requirements contract")
    return seed, _run_git(seed, "rev-parse", "HEAD")


def _preflight_fixture(
    tmp_path: Path,
    *,
    env_text: str,
    contract: str | None = None,
) -> tuple[Path, str, Path, Path]:
    seed, sha = _init_seed_repository(
        tmp_path,
        contract
        or "\n".join(
            (
                "format_version=1",
                "required=PUBLIC_VALUE",
                "required=SECRET_VALUE",
                "fixed=LOCK_PATH=/srv/runtime/vault-sync.lock",
                "optional=OPTIONAL_VALUE",
                "",
            )
        ),
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    runtime.chmod(0o700)
    env_file = runtime / "web.env"
    env_file.write_text(env_text, encoding="utf-8")
    env_file.chmod(0o600)
    return seed, sha, runtime, env_file


def _run_preflight(
    repository: Path,
    sha: str,
    runtime: Path,
    env_file: Path,
) -> subprocess.CompletedProcess[str]:
    return _run_bash(
        ENV_PREFLIGHT_PATH,
        "--repository",
        str(repository),
        "--sha",
        sha,
        "--runtime-root",
        str(runtime),
        "--env-file",
        str(env_file),
    )


def test_requirements_contract_is_versioned_and_contains_no_secret_values() -> None:
    contract = REQUIREMENTS_PATH.read_text(encoding="utf-8")

    assert "format_version=1" in contract
    assert (
        "fixed=SECOND_BRAIN_VAULT_OPERATION_LOCK_PATH=/srv/second-brain/runtime/vault-sync.lock"
        in contract
    )
    assert "required=SECOND_BRAIN_GITHUB_CLIENT_SECRET" in contract
    assert "required=SECOND_BRAIN_SESSION_SECRET" in contract
    assert "SECOND_BRAIN_GITHUB_CLIENT_SECRET=" not in contract
    assert "SECOND_BRAIN_SESSION_SECRET=" not in contract
    assert "CLOUDFLARE_API_TOKEN=" not in contract


@pytest.mark.skipif(os.name == "nt", reason="GNU stat, chmod and symlink semantics are required")
def test_env_preflight_accepts_present_required_and_fixed_values(tmp_path: Path) -> None:
    repository, sha, runtime, env_file = _preflight_fixture(
        tmp_path,
        env_text=(
            "PUBLIC_VALUE=visible\n"
            "SECRET_VALUE=TOP-SECRET\n"
            "LOCK_PATH=/srv/runtime/vault-sync.lock\n"
            "OPTIONAL_VALUE=\n"
        ),
    )

    result = _run_preflight(repository, sha, runtime, env_file)

    assert result.returncode == 0, result.stderr
    assert sha in result.stdout
    assert "TOP-SECRET" not in result.stdout + result.stderr


@pytest.mark.skipif(os.name == "nt", reason="GNU stat, chmod and symlink semantics are required")
@pytest.mark.parametrize(
    ("name", "env_text", "expected_message"),
    (
        (
            "missing",
            "PUBLIC_VALUE=visible\nSECRET_VALUE=TOP-SECRET\nOPTIONAL_VALUE=\n",
            "missing required variable",
        ),
        (
            "duplicate",
            "PUBLIC_VALUE=visible\nSECRET_VALUE=TOP-SECRET\n"
            "LOCK_PATH=/srv/runtime/vault-sync.lock\nPUBLIC_VALUE=again\n",
            "duplicate variable",
        ),
        (
            "malformed",
            "PUBLIC_VALUE=visible\nSECRET_VALUE=TOP-SECRET\n"
            "LOCK_PATH=/srv/runtime/vault-sync.lock\nnot an assignment\n",
            "malformed entry",
        ),
        (
            "fixed-mismatch",
            "PUBLIC_VALUE=visible\nSECRET_VALUE=TOP-SECRET\nLOCK_PATH=/srv/runtime/other.lock\n",
            "unexpected value",
        ),
    ),
)
def test_env_preflight_fails_closed_without_printing_secret(
    tmp_path: Path,
    name: str,
    env_text: str,
    expected_message: str,
) -> None:
    repository, sha, runtime, env_file = _preflight_fixture(tmp_path, env_text=env_text)
    before_env = env_file.read_text(encoding="utf-8")

    result = _run_preflight(repository, sha, runtime, env_file)

    assert result.returncode != 0, name
    assert expected_message in result.stderr
    assert "TOP-SECRET" not in result.stdout + result.stderr
    assert env_file.read_text(encoding="utf-8") == before_env
    assert not (runtime / sha).exists()


@pytest.mark.skipif(os.name == "nt", reason="GNU stat, chmod and symlink semantics are required")
def test_env_preflight_reads_requirements_from_the_exact_target_sha(tmp_path: Path) -> None:
    contract_v1 = "format_version=1\nrequired=PUBLIC_VALUE\n"
    repository, sha_v1 = _init_seed_repository(tmp_path, contract_v1)
    (repository / "deploy" / "production-env-requirements.conf").write_text(
        "format_version=1\nrequired=NEW_SECRET_VALUE\n",
        encoding="utf-8",
    )
    _run_git(repository, "add", "--", "deploy/production-env-requirements.conf")
    _run_git(repository, "commit", "-m", "add target-specific requirement")
    sha_v2 = _run_git(repository, "rev-parse", "HEAD")

    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    env_file = runtime / "web.env"
    env_file.write_text("PUBLIC_VALUE=visible\n", encoding="utf-8")
    env_file.chmod(0o600)

    old_target = _run_preflight(repository, sha_v1, runtime, env_file)
    new_target = _run_preflight(repository, sha_v2, runtime, env_file)

    assert old_target.returncode == 0, old_target.stderr
    assert new_target.returncode != 0
    assert "NEW_SECRET_VALUE" in new_target.stderr


def _candidate_fixture(tmp_path: Path) -> dict[str, Path | str]:
    seed = tmp_path / "seed"
    seed.mkdir()
    _run_git(seed, "init", "-b", "main")
    _run_git(seed, "config", "user.name", "Candidate Test")
    _run_git(seed, "config", "user.email", "candidate@example.invalid")
    (seed / "tracked.txt").write_text("base\n", encoding="utf-8")
    _run_git(seed, "add", "--", "tracked.txt")
    _run_git(seed, "commit", "-m", "base")
    base_sha = _run_git(seed, "rev-parse", "HEAD")

    (seed / "tracked.txt").write_text("wrong\n", encoding="utf-8")
    _run_git(seed, "commit", "-am", "wrong candidate")
    wrong_sha = _run_git(seed, "rev-parse", "HEAD")

    (seed / "tracked.txt").write_text("target\n", encoding="utf-8")
    _run_git(seed, "commit", "-am", "target release")
    target_sha = _run_git(seed, "rev-parse", "HEAD")

    origin = tmp_path / "origin.git"
    _run_git(tmp_path, "init", "--bare", str(origin))
    _run_git(seed, "remote", "add", "origin", str(origin))
    _run_git(seed, "push", "origin", "main")
    _run_git(tmp_path, "--git-dir", str(origin), "symbolic-ref", "HEAD", "refs/heads/main")

    control = tmp_path / "control"
    _run_git(tmp_path, "clone", str(origin), str(control))
    _run_git(control, "config", "user.name", "Candidate Test")
    _run_git(control, "config", "user.email", "candidate@example.invalid")
    releases = tmp_path / "releases"
    releases.mkdir()
    active = releases / base_sha
    _run_git(control, "worktree", "add", "--detach", str(active), base_sha)
    current = tmp_path / "current"
    current.symlink_to(Path("releases") / base_sha, target_is_directory=True)
    return {
        "control": control,
        "origin": origin,
        "releases": releases,
        "current": current,
        "base_sha": base_sha,
        "wrong_sha": wrong_sha,
        "target_sha": target_sha,
    }


def _run_candidate_check(
    fixture: dict[str, Path | str],
    *,
    expected_main_sha: str | None = None,
) -> subprocess.CompletedProcess[str]:
    target_sha = str(fixture["target_sha"])
    return _run_bash(
        CANDIDATE_CHECK_PATH,
        "--control-repository",
        str(fixture["control"]),
        "--releases-root",
        str(fixture["releases"]),
        "--current-link",
        str(fixture["current"]),
        "--target-sha",
        target_sha,
        "--expected-main-sha",
        expected_main_sha or target_sha,
    )


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX linked worktree and symlink semantics are required"
)
def test_valid_interrupted_candidate_is_recoverable_and_read_only(tmp_path: Path) -> None:
    fixture = _candidate_fixture(tmp_path)
    candidate = Path(str(fixture["releases"])) / str(fixture["target_sha"])
    _run_git(
        Path(str(fixture["control"])),
        "worktree",
        "add",
        "--detach",
        str(candidate),
        str(fixture["target_sha"]),
    )
    before_current = Path(str(fixture["current"])).readlink()

    result = _run_candidate_check(fixture)

    assert result.returncode == 0, result.stderr
    assert "recoverable" in result.stdout
    assert Path(str(fixture["current"])).readlink() == before_current
    assert candidate.is_dir()


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX linked worktree and symlink semantics are required"
)
@pytest.mark.parametrize("kind", ("wrong-head", "branch", "dirty", "symlink", "wrong-repository"))
def test_candidate_recovery_rejects_unprovable_state(tmp_path: Path, kind: str) -> None:
    fixture = _candidate_fixture(tmp_path)
    candidate = Path(str(fixture["releases"])) / str(fixture["target_sha"])
    control = Path(str(fixture["control"]))

    if kind == "wrong-head":
        _run_git(control, "worktree", "add", "--detach", str(candidate), str(fixture["wrong_sha"]))
    elif kind == "branch":
        _run_git(control, "branch", "candidate-branch", str(fixture["target_sha"]))
        _run_git(control, "worktree", "add", str(candidate), "candidate-branch")
    elif kind == "dirty":
        _run_git(control, "worktree", "add", "--detach", str(candidate), str(fixture["target_sha"]))
        (candidate / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    elif kind == "symlink":
        alternate = Path(str(fixture["releases"])) / "candidate-storage"
        _run_git(control, "worktree", "add", "--detach", str(alternate), str(fixture["target_sha"]))
        candidate.symlink_to(alternate, target_is_directory=True)
    else:
        _run_git(tmp_path, "clone", str(fixture["origin"]), str(candidate))

    result = _run_candidate_check(fixture)

    assert result.returncode != 0, kind
    assert "STOP / HUMAN_REQUIRED" in result.stderr
    assert candidate.exists() or candidate.is_symlink()
    assert Path(str(fixture["current"])).readlink() == Path("releases") / str(fixture["base_sha"])


def test_autodeploy_has_preflight_recovery_and_cancellation_gates() -> None:
    script = AUTODEPLOY_PATH.read_text(encoding="utf-8")
    exact_preflight = script.index(
        "exact target production env preflight failed; candidate creation запрещена"
    )
    before_candidate = script.index(
        'run_env_preflight \\\n  || die "production env preflight failed before candidate creation"'
    )
    candidate_path = script.index('CANDIDATE_RELEASE="$RELEASES_ROOT/$TARGET_SHA"')
    final_integrity = script.index(
        "final candidate integrity verification failed; activation запрещена"
    )
    activation = script.index("ACTIVATION_STARTED=1")
    pipeline_start = script.index('  cd "$CANDIDATE_RELEASE"')
    frontend_pipeline = script.index('  cd "$CANDIDATE_RELEASE/web"')

    assert exact_preflight < script.index('git -C "$APP_ROOT" merge --ff-only origin/main')
    assert before_candidate < candidate_path
    assert final_integrity < activation
    assert "run_candidate_recovery_check" in script[script.index("if [[ -e") : candidate_path + 800]
    assert 'git -C "$APP_ROOT" worktree add --detach' in script
    assert pipeline_start > candidate_path
    assert script.index("uv sync --locked --python 3.14", pipeline_start) < activation
    assert frontend_pipeline > pipeline_start
    assert script.index("npm run build", frontend_pipeline) < activation
    active_noop = script[script.index('if [[ "$PREVIOUS_SHA" == "$TARGET_SHA" ]]') : candidate_path]
    assert "run_candidate_recovery_check" not in active_noop
    for required in (
        "trap 'handle_interruption SIGTERM' TERM",
        "trap 'handle_interruption SIGHUP' HUP",
        "trap 'handle_interruption SIGINT' INT",
        "ACTIVATION_STARTED=1",
        "current и service не изменялись",
        "candidate сохранён для recovery",
        "uv sync --locked --python 3.14",
        "npm ci",
        "npm run check",
        "npm run build",
        "npm run qa:pwa",
    ):
        assert required in script

    preactivation = script[:activation]
    assert "systemctl restart" not in preactivation
    for forbidden in (
        "git reset",
        "git clean",
        "git rebase",
        "rm -rf",
        "ln -sfn",
        "source ",
        "eval ",
    ):
        assert forbidden not in script.casefold()


def test_candidate_checker_covers_identity_state_and_worktree_registration() -> None:
    checker = CANDIDATE_CHECK_PATH.read_text(encoding="utf-8")

    for required in (
        "worktree list --porcelain",
        "--git-common-dir",
        "--abbrev-ref HEAD",
        "symbolic-ref --quiet HEAD",
        "status --porcelain=v1 --untracked-files=all",
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "REBASE_HEAD",
        "rebase-merge",
        "rebase-apply",
        "BISECT_LOG",
        "sequencer",
        "index.lock",
        "STOP / HUMAN_REQUIRED",
    ):
        assert required in checker
    for forbidden in ("git clean", "git reset", "git rebase", "rm -rf", "git worktree prune"):
        assert forbidden not in checker.casefold()


def test_autodeploy_runbook_records_the_incident_retry_contract() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

    assert "34526439632" in runbook
    assert "9e6609fabfee3c935213ecb0a9f6a11c9ec30166" in runbook
    assert "uv sync --locked" in runbook
    assert "npm ci" in runbook
    assert "npm run build" in runbook
    assert "Target superseded более новым `main`" in runbook
    assert "ручной VPS cleanup не нужен" in runbook


def test_deploy_workflow_keeps_trusted_push_and_secret_boundary() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    for required in (
        "workflow_run:",
        'workflows: ["CI"]',
        "types: [completed]",
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.event == 'push'",
        "github.event.workflow_run.head_branch == 'main'",
        "github.event.workflow_run.head_repository.full_name == github.repository",
        "ref: ${{ env.DEPLOY_SHA }}",
        "persist-credentials: false",
        "environment: production",
        "permissions:\n  contents: read",
        "secrets.PRODUCTION_SSH_PRIVATE_KEY",
        "secrets.PRODUCTION_SSH_KNOWN_HOSTS",
    ):
        assert required in workflow
    assert "pull_request_target" not in workflow
    assert "permissions:\n  contents: write" not in workflow
    assert "github.token" not in workflow


def test_autodeploy_runbook_documents_recovery_and_no_blind_cleanup() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

    for required in (
        "Versioned production env preflight",
        "production-env-requirements.conf",
        "SECOND_BRAIN_VAULT_OPERATION_LOCK_PATH",
        "mode — `600`",
        "duplicate",
        "HUMAN_REQUIRED",
        "Interrupted candidate recovery",
        "Cancelled/interrupted deploy и retry",
        "34526439632",
        "Re-run jobs",
        "Оператор обычно **не",
        "должен SSH-подключаться и удалять candidate вручную",
        "без удаления и activation",
    ):
        assert required in runbook
