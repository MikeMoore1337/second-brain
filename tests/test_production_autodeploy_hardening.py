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
SYSTEMD_CHECK_PATH = DEPLOY_ROOT / "systemd-contract-check.sh"
RUNTIME_CHECK_PATH = DEPLOY_ROOT / "runtime-entrypoint-check.sh"
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


def _run_bash(
    script: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    bash = _bash_path()
    if bash is None:
        pytest.skip("bash is required for shell helper integration tests")
    process_env = os.environ.copy()
    if env is not None:
        process_env.update(env)
    return subprocess.run(
        [bash, str(script), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=process_env,
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
        "format_version=1\nrequired=PUBLIC_VALUE\nrequired=NEW_SECRET_VALUE\n",
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
    (seed / ".gitignore").write_text(
        "\n".join(
            (
                ".venv/",
                "node_modules/",
                "dist/",
                "__pycache__/",
                ".env",
                ".env.*",
                "*.env",
                "*.key",
                "*.pem",
                "*.crt",
                "*.cer",
                "config/*.json",
                ".impeccable/config.local.json",
                "",
            )
        ),
        encoding="utf-8",
    )
    (seed / "tracked.txt").write_text("base\n", encoding="utf-8")
    _run_git(seed, "add", "--", ".gitignore", "tracked.txt")
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


def _write_candidate_state(
    candidate: Path, relative_path: str, content: str = "generated\n"
) -> None:
    path = candidate / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _clean_state_repository(tmp_path: Path) -> Path:
    """Create a plain main checkout without POSIX-only candidate symlinks."""
    repository, _sha = _init_seed_repository(
        tmp_path,
        "format_version=1\nrequired=PUBLIC_VALUE\n",
    )
    (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _run_git(repository, "add", "--", "tracked.txt")
    _run_git(repository, "commit", "-m", "add tracked fixture")
    return repository


def _clean_state_harness(tmp_path: Path) -> Path:
    """Run the production clean-state function against an isolated fixture."""
    script = AUTODEPLOY_PATH.read_text(encoding="utf-8")
    die_start = script.index("die() {")
    die_end = script.index("\n\nhandle_interruption", die_start)
    clean_start = script.index("GIT_STATUS_DIAGNOSTIC_MAX_BYTES=")
    clean_end = script.index("\n\nreset_candidate_python_environment()", clean_start)

    harness = tmp_path / "clean-state-harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -Eeuo pipefail\n"
        f"{script[die_start:die_end]}\n"
        f"{script[clean_start:clean_end]}\n"
        'assert_clean_main "$1" "$2"\n',
        encoding="utf-8",
    )
    harness.chmod(0o755)
    return harness


def _failing_git_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Make only the status subcommand fail while delegating all other Git calls."""
    bash = _bash_path()
    assert bash is not None
    real_git_result = subprocess.run(
        [bash, "-lc", "command -v git"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert real_git_result.returncode == 0, real_git_result.stderr
    real_git = real_git_result.stdout.strip()
    assert real_git

    call_log = tmp_path / "git-calls.log"
    bash_env = tmp_path / "bash-env.sh"
    bash_env.write_text(
        "git() {\n"
        '  printf \'%s\\n\' "$*" >> "$GIT_CALL_LOG"\n'
        'if [[ "${1:-}" == "-C" && "${3:-}" == "status" ]]; then\n'
        "  printf 'fatal: simulated index failure\\n' >&2\n"
        "  printf 'simulated status diagnostic on stdout\\n'\n"
        "  exit 73\n"
        "fi\n"
        '  "$REAL_GIT" "$@"\n'
        "}\n",
        encoding="utf-8",
    )

    environment = {
        "BASH_ENV": str(bash_env),
        "REAL_GIT": real_git,
        "GIT_CALL_LOG": str(call_log),
    }
    return environment, call_log


@pytest.mark.skipif(_bash_path() is None, reason="bash is required for clean-state tests")
def test_clean_state_guard_keeps_clean_checkout_flow_green(tmp_path: Path) -> None:
    repository = _clean_state_repository(tmp_path)
    result = _run_bash(
        _clean_state_harness(tmp_path),
        str(repository),
        "second-brain",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.skipif(_bash_path() is None, reason="bash is required for clean-state tests")
def test_dirty_clean_state_guard_reports_bounded_status_metadata_only(tmp_path: Path) -> None:
    control = _clean_state_repository(tmp_path)
    secret_content = "PRIVATE_FILE_CONTENT_MUST_NOT_BE_LOGGED"
    (control / "tracked.txt").write_text(secret_content, encoding="utf-8")

    result = _run_bash(
        _clean_state_harness(tmp_path),
        str(control),
        "second-brain",
    )

    assert result.returncode != 0
    assert "second-brain dirty;" in result.stderr
    assert "bounded_status=" in result.stderr
    assert "tracked.txt" in result.stderr
    assert secret_content not in result.stdout + result.stderr
    assert len(result.stderr) < 1500


@pytest.mark.skipif(_bash_path() is None, reason="bash is required for clean-state tests")
def test_nonzero_git_status_is_bounded_fail_closed_and_stops_before_mutation(
    tmp_path: Path,
) -> None:
    control = _clean_state_repository(tmp_path)
    secret_content = "PRIVATE_FILE_CONTENT_MUST_NOT_BE_LOGGED"
    (control / "tracked.txt").write_text(secret_content, encoding="utf-8")
    environment, call_log = _failing_git_environment(tmp_path)
    before_head = _run_git(control, "rev-parse", "HEAD")
    result = _run_bash(
        _clean_state_harness(tmp_path),
        str(control),
        "second-brain",
        env=environment,
    )

    assert result.returncode != 0
    assert "git status failed" in result.stderr
    assert "exit_code=73" in result.stderr
    assert "bounded_stderr=fatal: simulated index failure" in result.stderr
    assert "bounded_stdout=simulated status diagnostic on stdout" in result.stderr
    assert secret_content not in result.stdout + result.stderr
    assert len(result.stderr) < 1500
    assert _run_git(control, "rev-parse", "HEAD") == before_head
    calls = call_log.read_text(encoding="utf-8")
    assert "symbolic-ref" in calls
    assert "status" in calls
    for forbidden in ("fetch", "worktree", "reset", "clean", "checkout", "activate"):
        assert forbidden not in calls.casefold()


def test_clean_state_diagnostic_is_bounded_and_precedes_release_mutation() -> None:
    script = AUTODEPLOY_PATH.read_text(encoding="utf-8")

    for required in (
        "GIT_STATUS_DIAGNOSTIC_MAX_BYTES=512",
        "mktemp",
        "head -c",
        "exit_code=",
        "bounded_stderr=",
        "bounded_stdout=",
        "bounded_status=",
        "автоматическая очистка запрещена",
    ):
        assert required in script

    status_guard = script.index(
        'if status="$(git -C "$path" status --porcelain=v1 --untracked-files=all'
    )
    fetch = script.index('git -C "$APP_ROOT" fetch --no-tags origin main')
    candidate = script.index('CANDIDATE_RELEASE="$RELEASES_ROOT/$TARGET_SHA"')
    assert status_guard < fetch < candidate

    failure_start = script.index("else\n    status_exit=$?", status_guard)
    failure_end = script.index("\n  fi\n\n  status_stderr=", failure_start)
    failure_path = script[failure_start:failure_end]
    for forbidden in (
        "git fetch",
        "git worktree",
        "git reset",
        "git clean",
        "git checkout",
        "release-control",
        "rm -rf",
    ):
        assert forbidden not in failure_path.casefold()


def _run_candidate_check(
    fixture: dict[str, Path | str],
    *,
    expected_main_sha: str | None = None,
    phase: str = "recovery-pre-build",
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
        "--phase",
        phase,
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
@pytest.mark.parametrize(
    "kind",
    ("wrong-head", "branch", "dirty", "untracked", "symlink", "wrong-repository"),
)
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
    elif kind == "untracked":
        _run_git(control, "worktree", "add", "--detach", str(candidate), str(fixture["target_sha"]))
        (candidate / "untracked.txt").write_text("untracked\n", encoding="utf-8")
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


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX linked worktree and symlink semantics are required"
)
@pytest.mark.parametrize(
    ("relative_path", "secret_value"),
    (
        ("web/.env.production", "SECOND_BRAIN_SESSION_SECRET=TOP-SECRET\n"),
        (".env", "TOP-SECRET\n"),
        (".impeccable/config.local.json", '{"api_token":"TOP-SECRET"}\n'),
        ("web/private.key", "TOP-SECRET\n"),
        ("config/local-settings.json", '{"operator_token":"TOP-SECRET"}\n'),
        ("config/fixture.pem", "TOP-SECRET\n"),
        ("config/operator-credentials.json", '{"password":"TOP-SECRET"}\n'),
    ),
)
def test_candidate_recovery_rejects_external_sensitive_ignored_state_without_disclosure(
    tmp_path: Path,
    relative_path: str,
    secret_value: str,
) -> None:
    fixture = _candidate_fixture(tmp_path)
    candidate = Path(str(fixture["releases"])) / str(fixture["target_sha"])
    control = Path(str(fixture["control"]))
    _run_git(control, "worktree", "add", "--detach", str(candidate), str(fixture["target_sha"]))
    _write_candidate_state(candidate, relative_path, secret_value)

    result = _run_candidate_check(fixture)

    assert result.returncode != 0
    assert "STOP / HUMAN_REQUIRED" in result.stderr
    assert "TOP-SECRET" not in result.stdout + result.stderr
    assert relative_path not in result.stdout + result.stderr
    assert Path(str(fixture["current"])).readlink() == Path("releases") / str(fixture["base_sha"])


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX linked worktree and symlink semantics are required"
)
def test_candidate_recovery_rejects_unknown_external_ignored_config_without_disclosure(
    tmp_path: Path,
) -> None:
    fixture = _candidate_fixture(tmp_path)
    candidate = Path(str(fixture["releases"])) / str(fixture["target_sha"])
    control = Path(str(fixture["control"]))
    _run_git(control, "worktree", "add", "--detach", str(candidate), str(fixture["target_sha"]))
    relative_path = "config/operator-settings.json"
    _write_candidate_state(candidate, relative_path, '{"operator":"local"}\n')

    result = _run_candidate_check(fixture)

    assert result.returncode != 0
    assert "STOP / HUMAN_REQUIRED" in result.stderr
    assert "outside the explicit generated-state allowlist" in result.stderr
    assert relative_path not in result.stdout + result.stderr
    assert "operator" not in result.stdout + result.stderr


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX linked worktree and symlink semantics are required"
)
@pytest.mark.parametrize(
    "relative_path",
    (
        ".venv/Lib/python3.14/site-packages/generated_marker.py",
        ".venv/Lib/python3.14/site-packages/client-token-resource.py",
        ".venv/Lib/python3.14/site-packages/private-resource.pem",
        ".venv/Lib/python3.14/site-packages/client-secret.json",
        "web/node_modules/.package-lock.json",
        "web/dist/index.html",
        "src/second_brain/__pycache__/generated_marker.cpython-314.pyc",
    ),
)
def test_candidate_recovery_allows_discardable_generated_state_before_rebuild(
    tmp_path: Path,
    relative_path: str,
) -> None:
    fixture = _candidate_fixture(tmp_path)
    candidate = Path(str(fixture["releases"])) / str(fixture["target_sha"])
    control = Path(str(fixture["control"]))
    _run_git(control, "worktree", "add", "--detach", str(candidate), str(fixture["target_sha"]))
    _write_candidate_state(candidate, relative_path)

    result = _run_candidate_check(fixture)

    assert result.returncode == 0, result.stderr
    assert "generated" not in result.stdout + result.stderr


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX linked worktree and symlink semantics are required"
)
@pytest.mark.parametrize(
    "relative_path",
    (
        ".venv/.env",
        ".venv/Lib/python3.14/site-packages/client-token-resource.py",
        ".venv/Lib/python3.14/site-packages/private-resource.pem",
        ".venv/Lib/python3.14/site-packages/client-secret.json",
        ".venv/Lib/python3.14/site-packages/credential-fixture.json",
        ".venv/Lib/python3.14/site-packages/password-fixture.txt",
        ".venv/Lib/python3.14/site-packages/test-key.pem",
        "web/node_modules/package/private-resource.key",
        "web/node_modules/package/secret-resource.json",
        "web/dist/.env",
        "web/dist/token-resource.js",
    ),
)
def test_candidate_recovery_trusts_sensitive_looking_generated_state_structurally(
    tmp_path: Path,
    relative_path: str,
) -> None:
    fixture = _candidate_fixture(tmp_path)
    candidate = Path(str(fixture["releases"])) / str(fixture["target_sha"])
    control = Path(str(fixture["control"]))
    _run_git(control, "worktree", "add", "--detach", str(candidate), str(fixture["target_sha"]))
    _write_candidate_state(candidate, relative_path, "package-owned generated resource\n")

    result = _run_candidate_check(fixture, phase="final-post-build")

    assert result.returncode == 0, result.stderr
    assert relative_path not in result.stdout + result.stderr
    assert "package-owned generated resource" not in result.stdout + result.stderr


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX linked worktree and symlink semantics are required"
)
@pytest.mark.parametrize(
    "relative_path",
    (
        ".venv/Lib/python3.14/site-packages/trust_bundle.pem",
        ".venv/Lib/python3.14/site-packages/fixture.crt",
        "web/node_modules/package/fixtures/test.cer",
    ),
)
def test_candidate_recovery_allows_package_owned_certificate_resources(
    tmp_path: Path,
    relative_path: str,
) -> None:
    fixture = _candidate_fixture(tmp_path)
    candidate = Path(str(fixture["releases"])) / str(fixture["target_sha"])
    control = Path(str(fixture["control"]))
    _run_git(control, "worktree", "add", "--detach", str(candidate), str(fixture["target_sha"]))
    _write_candidate_state(candidate, relative_path, "package resource\n")

    result = _run_candidate_check(fixture, phase="final-post-build")

    assert result.returncode == 0, result.stderr
    assert "package resource" not in result.stdout + result.stderr
    assert relative_path not in result.stdout + result.stderr


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX linked worktree and symlink semantics are required"
)
@pytest.mark.parametrize(
    "relative_path",
    (
        ".venv/Lib/python3.14/site-packages/markdown_it/token.py",
        ".venv/Lib/python3.14/site-packages/coverage/phystokens.py",
        "web/node_modules/postcss/lib/tokenize.js",
    ),
)
def test_candidate_recovery_allows_ordinary_dependency_token_modules(
    tmp_path: Path,
    relative_path: str,
) -> None:
    fixture = _candidate_fixture(tmp_path)
    candidate = Path(str(fixture["releases"])) / str(fixture["target_sha"])
    control = Path(str(fixture["control"]))
    _run_git(control, "worktree", "add", "--detach", str(candidate), str(fixture["target_sha"]))
    _write_candidate_state(candidate, relative_path, "ordinary dependency module\n")

    result = _run_candidate_check(fixture, phase="final-post-build")

    assert result.returncode == 0, result.stderr
    assert "ordinary dependency module" not in result.stdout + result.stderr
    assert relative_path not in result.stdout + result.stderr


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX linked worktree and symlink semantics are required"
)
def test_candidate_recovery_rejects_generated_root_symlink(tmp_path: Path) -> None:
    fixture = _candidate_fixture(tmp_path)
    candidate = Path(str(fixture["releases"])) / str(fixture["target_sha"])
    control = Path(str(fixture["control"]))
    _run_git(control, "worktree", "add", "--detach", str(candidate), str(fixture["target_sha"]))
    outside = tmp_path / "outside-generated"
    outside.mkdir()
    (outside / "marker.pem").write_text("DO-NOT-READ\n", encoding="utf-8")
    (candidate / ".venv").symlink_to(outside, target_is_directory=True)
    before_current = Path(str(fixture["current"])).readlink()

    result = _run_candidate_check(fixture)

    assert result.returncode != 0
    assert "STOP / HUMAN_REQUIRED" in result.stderr
    assert "DO-NOT-READ" not in result.stdout + result.stderr
    assert "outside-generated" not in result.stdout + result.stderr
    assert (candidate / ".venv").is_symlink()
    assert Path(str(fixture["current"])).readlink() == before_current


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX linked worktree and symlink semantics are required"
)
def test_candidate_recovery_rejects_generated_root_path_escape(tmp_path: Path) -> None:
    fixture = _candidate_fixture(tmp_path)
    candidate = Path(str(fixture["releases"])) / str(fixture["target_sha"])
    control = Path(str(fixture["control"]))
    _run_git(control, "worktree", "add", "--detach", str(candidate), str(fixture["target_sha"]))
    outside = tmp_path / "outside-dist"
    outside.mkdir()
    (outside / "index.html").write_text("DO-NOT-READ\n", encoding="utf-8")
    web_root = candidate / "web"
    web_root.mkdir()
    (web_root / "dist").symlink_to(outside, target_is_directory=True)

    result = _run_candidate_check(fixture, phase="final-post-build")

    assert result.returncode != 0
    assert "STOP / HUMAN_REQUIRED" in result.stderr
    assert "DO-NOT-READ" not in result.stdout + result.stderr
    assert "outside-dist" not in result.stdout + result.stderr
    assert (web_root / "dist").is_symlink()


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX linked worktree and symlink semantics are required"
)
def test_fresh_candidate_after_pipeline_generated_state_passes_final_integrity(
    tmp_path: Path,
) -> None:
    fixture = _candidate_fixture(tmp_path)
    candidate = Path(str(fixture["releases"])) / str(fixture["target_sha"])
    control = Path(str(fixture["control"]))
    _run_git(control, "worktree", "add", "--detach", str(candidate), str(fixture["target_sha"]))
    for relative_path in (
        ".venv/Lib/python3.14/site-packages/client-token-resource.py",
        ".venv/Lib/python3.14/site-packages/private-resource.pem",
        ".venv/Lib/python3.14/site-packages/client-secret.json",
        ".venv/Lib/python3.14/site-packages/credential-fixture.json",
        ".venv/Lib/python3.14/site-packages/test-key.pem",
        ".venv/Lib/python3.14/site-packages/trust_bundle.pem",
        "web/node_modules/package/fixtures/test.crt",
        "web/dist/index.html",
        "src/second_brain/__pycache__/generated_marker.cpython-314.pyc",
    ):
        _write_candidate_state(candidate, relative_path)
    before_current = Path(str(fixture["current"])).readlink()

    result = _run_candidate_check(fixture, phase="final-post-build")

    assert result.returncode == 0, result.stderr
    assert "generated" not in result.stdout + result.stderr
    assert Path(str(fixture["current"])).readlink() == before_current


@pytest.mark.skipif(os.name == "nt", reason="uv reset semantics are required")
def test_uv_venv_clear_removes_arbitrary_generated_root_state(tmp_path: Path) -> None:
    venv_root = tmp_path / ".venv"
    first = subprocess.run(
        ["uv", "venv", "--no-project", "--clear", "--python", "3.14", str(venv_root)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    marker = venv_root / "untracked-generated-resource.pem"
    marker.write_text("generated\n", encoding="utf-8")

    second = subprocess.run(
        ["uv", "venv", "--no-project", "--clear", "--python", "3.14", str(venv_root)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert second.returncode == 0, second.stderr
    assert not marker.exists()
    assert (venv_root / "pyvenv.cfg").is_file()


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX linked worktree and symlink semantics are required"
)
def test_incident_style_candidate_is_rerunnable_without_manual_cleanup(tmp_path: Path) -> None:
    fixture = _candidate_fixture(tmp_path)
    candidate = Path(str(fixture["releases"])) / str(fixture["target_sha"])
    control = Path(str(fixture["control"]))
    _run_git(control, "worktree", "add", "--detach", str(candidate), str(fixture["target_sha"]))
    for relative_path in (
        ".venv/Lib/python3.14/site-packages/generated_marker.py",
        "web/node_modules/.package-lock.json",
        "src/second_brain/__pycache__/generated_marker.cpython-314.pyc",
    ):
        _write_candidate_state(candidate, relative_path)
    before_current = Path(str(fixture["current"])).readlink()

    first = _run_candidate_check(fixture)
    second = _run_candidate_check(fixture)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert Path(str(fixture["current"])).readlink() == before_current


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
    reset_environment = script.index("  reset_candidate_python_environment\n", pipeline_start)
    reset_function = script.index("reset_candidate_python_environment()")
    reset_command = script.index("uv venv --no-project --clear --python 3.14", reset_function)
    assert pipeline_start < reset_environment
    assert reset_function < reset_command
    assert script.index("uv sync --locked --python 3.14", pipeline_start) < activation
    uv_sync = script.index("uv sync --locked --python 3.14", pipeline_start)
    compileall = script.index("python -m compileall", pipeline_start)
    npm_ci = script.index("npm ci", frontend_pipeline)
    npm_check = script.index("npm run check", frontend_pipeline)
    npm_build = script.index("npm run build", frontend_pipeline)
    pwa_qa = script.index("npm run qa:pwa", frontend_pipeline)
    final_check = script.index("run_candidate_recovery_check \\\n  final-post-build")
    assert uv_sync < compileall < npm_ci < npm_check < npm_build < pwa_qa < final_check
    recovery_gate = script.index("existing candidate нельзя доказать recoverable")
    assert recovery_gate < script.index("uv sync --locked --python 3.14", recovery_gate)
    assert script.index("python -m compileall", pipeline_start) < activation
    assert frontend_pipeline > pipeline_start
    assert (
        "assert_candidate_frontend_roots"
        in script[frontend_pipeline - 100 : frontend_pipeline + 200]
    )
    assert script.index("npm run build", frontend_pipeline) < activation
    assert "recovery-pre-build" in script
    assert "final-post-build" in script
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
        "uv venv --no-project --clear --python 3.14",
        "python -m compileall",
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


def test_root_managed_diff_gate_separates_systemd_from_caddy_and_root() -> None:
    script = AUTODEPLOY_PATH.read_text(encoding="utf-8")

    caddy_root_gate = script.index(
        'git -C "$APP_ROOT" diff --quiet "$PREVIOUS_SHA" "$TARGET_SHA" -- deploy/caddy deploy/root'
    )
    systemd_gate = script.index(
        'git -C "$APP_ROOT" diff --quiet "$PREVIOUS_SHA" "$TARGET_SHA" -- deploy/systemd'
    )
    candidate_path = script.index('CANDIDATE_RELEASE="$RELEASES_ROOT/$TARGET_SHA"')
    assert caddy_root_gate < systemd_gate < candidate_path
    assert "run_systemd_contract_check \\" in script[systemd_gate:candidate_path]
    assert "SYSTEMD_CONTRACT_NOT_INTEGRATED / HUMAN_REQUIRED" in script
    assert "deploy/caddy deploy/root" in script


def test_runtime_entrypoint_gate_precedes_validation_and_activation() -> None:
    script = AUTODEPLOY_PATH.read_text(encoding="utf-8")
    assert SYSTEMD_CHECK_PATH.read_text(encoding="utf-8").startswith(
        "#!/usr/bin/env bash\nset -Eeuo pipefail"
    )
    assert RUNTIME_CHECK_PATH.read_text(encoding="utf-8").startswith(
        "#!/usr/bin/env bash\nset -Eeuo pipefail"
    )

    sync = script.index("uv sync --locked --python 3.14")
    runtime_gate = script.index('run_runtime_entrypoint_check "$CANDIDATE_RELEASE" "$TARGET_SHA"')
    direct_doctor = script.index('"$CANDIDATE_ENTRYPOINT" --env-file "$RUNTIME_ENV" doctor')
    activation = script.index("ACTIVATION_STARTED=1")
    assert sync < runtime_gate < direct_doctor < activation
    assert "SuccessExitStatus=143" not in (
        PROJECT_ROOT / "deploy" / "systemd" / "second-brain-web.service"
    ).read_text(encoding="utf-8")


def test_candidate_checker_covers_identity_state_and_worktree_registration() -> None:
    checker = CANDIDATE_CHECK_PATH.read_text(encoding="utf-8")

    for required in (
        "worktree list --porcelain",
        "ls-files --others --ignored --exclude-standard",
        "--phase",
        "recovery-pre-build",
        "final-post-build",
        "is_generated_state_path",
        "is_certificate_resource_path",
        "is_allowed_generated_path",
        "assert_generated_root_layout",
        "structural trust decision",
        "outside every approved",
        "escaped the exact candidate",
        ".venv/*",
        "web/node_modules/*",
        "web/dist/*",
        "src/*/__pycache__/*.cpython-314.pyc",
        "outside the explicit generated-state allowlist",
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

    generated_gate = checker.index("if is_allowed_generated_path")
    sensitive_gate = checker.index("if is_sensitive_ignored_path")
    assert generated_gate < sensitive_gate
    generated_classifier = checker[checker.index("is_allowed_generated_path()") : sensitive_gate]
    assert "is_env_or_key_like_path" not in generated_classifier
    assert "is_certificate_resource_path" not in generated_classifier


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
        "Explicit ignored-state allowlist",
        "ls-files --others --ignored --exclude-standard",
        ".venv/**",
        "web/node_modules/**",
        "web/dist/**",
        "src/**/__pycache__/*.cpython-314.pyc",
        "Trust model",
        "discardable",
        "filename/suffix heuristic",
        "sensitive-looking names",
        "Вне approved generated roots",
        "compileall -q -f --invalidation-mode checked-hash",
        "Оператор обычно **не",
        "должен SSH-подключаться и удалять candidate вручную",
        "без удаления и activation",
    ):
        assert required in runbook
