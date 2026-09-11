"""POSIX integration regressions for the systemd/runtime contract helpers."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[1]
SYSTEMD_CHECK_PATH = PROJECT_ROOT / "deploy" / "systemd-contract-check.sh"
RUNTIME_CHECK_PATH = PROJECT_ROOT / "deploy" / "runtime-entrypoint-check.sh"
TEST_SHA = "a" * 40


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


def _require_posix_helpers() -> str:
    bash = _bash_path()
    if bash is None or shutil.which("git") is None:
        pytest.skip("POSIX bash and git are required for helper integration tests")
    if os.name == "nt":
        pytest.skip("systemd/runtime helper integration requires POSIX paths and permissions")
    return bash


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


def _init_unit_repository(tmp_path: Path, unit: str) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _run_git(repository, "init", "-b", "main")
    _run_git(repository, "config", "user.name", "Systemd Contract Test")
    _run_git(repository, "config", "user.email", "systemd@example.invalid")
    unit_path = repository / "deploy" / "systemd" / "second-brain-web.service"
    unit_path.parent.mkdir(parents=True)
    unit_path.write_text(unit, encoding="utf-8")
    _run_git(repository, "add", "--", "deploy/systemd/second-brain-web.service")
    _run_git(repository, "commit", "-m", "test systemd contract")
    return repository, _run_git(repository, "rev-parse", "HEAD")


def _root_stat_path(tmp_path: Path, owner_uid: str = "0") -> Path:
    """Make temp fixtures appear root-owned while retaining real mode checks."""

    real_stat = shutil.which("stat")
    if real_stat is None:
        pytest.skip("GNU stat is required for systemd contract integration tests")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake_stat = bin_dir / "stat"
    fake_stat.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'if [[ "${{1:-}}" == "-c" && "${{2:-}}" == "%u" ]]; then\n'
        f"  printf '{owner_uid}\\n'\n"
        "else\n"
        f'  exec {shlex.quote(real_stat)} "$@"\n'
        "fi\n",
        encoding="utf-8",
    )
    fake_stat.chmod(0o755)
    return bin_dir


def _run_systemd_check(
    tmp_path: Path,
    repository: Path,
    sha: str,
    installed_unit: Path,
    dropin_directory: Path,
    owner_uid: str = "0",
) -> subprocess.CompletedProcess[str]:
    bash = _require_posix_helpers()
    path = _root_stat_path(tmp_path, owner_uid)
    env = os.environ.copy()
    env["PATH"] = f"{path}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [
            bash,
            str(SYSTEMD_CHECK_PATH),
            "--repository",
            str(repository),
            "--sha",
            sha,
            "--installed-unit",
            str(installed_unit),
            "--drop-in-directory",
            str(dropin_directory),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_systemd_exact_root_contract_passes_and_mismatch_stops(tmp_path: Path) -> None:
    unit = (
        "[Unit]\nDescription=test\n[Service]\n"
        "ExecStart=/srv/second-brain/current/.venv/bin/second-brain\n"
    )
    repository, sha = _init_unit_repository(tmp_path, unit)
    installed_unit = tmp_path / "installed.service"
    installed_unit.write_text(unit, encoding="utf-8")
    installed_unit.chmod(0o644)
    dropin_directory = tmp_path / "second-brain-web.service.d"
    dropin_directory.mkdir(mode=0o755)

    passed = _run_systemd_check(tmp_path, repository, sha, installed_unit, dropin_directory)
    assert passed.returncode == 0, passed.stderr

    installed_unit.write_text(unit + "# changed\n", encoding="utf-8")
    mismatched = _run_systemd_check(tmp_path, repository, sha, installed_unit, dropin_directory)
    assert mismatched.returncode != 0
    assert "SYSTEMD_CONTRACT_NOT_INTEGRATED / HUMAN_REQUIRED" in mismatched.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink/permission contract")
def test_systemd_symlink_nonregular_and_unsafe_permissions_stop(tmp_path: Path) -> None:
    unit = "[Service]\nExecStart=/srv/second-brain/current/.venv/bin/second-brain\n"
    repository, sha = _init_unit_repository(tmp_path, unit)
    dropin_directory = tmp_path / "dropins"
    dropin_directory.mkdir()
    source = tmp_path / "source.service"
    source.write_text(unit, encoding="utf-8")

    symlink = tmp_path / "symlink.service"
    symlink.symlink_to(source)
    result = _run_systemd_check(tmp_path, repository, sha, symlink, dropin_directory)
    assert result.returncode != 0
    assert "regular file" in result.stderr

    directory = tmp_path / "unit-directory"
    directory.mkdir()
    result = _run_systemd_check(tmp_path, repository, sha, directory, dropin_directory)
    assert result.returncode != 0
    assert "regular file" in result.stderr

    unsafe = tmp_path / "unsafe.service"
    unsafe.write_text(unit, encoding="utf-8")
    unsafe.chmod(0o666)
    result = _run_systemd_check(tmp_path, repository, sha, unsafe, dropin_directory)
    assert result.returncode != 0
    assert "write permissions" in result.stderr

    owned = tmp_path / "not-root-owned.service"
    owned.write_text(unit, encoding="utf-8")
    owned.chmod(0o644)
    result = _run_systemd_check(
        tmp_path,
        repository,
        sha,
        owned,
        dropin_directory,
        owner_uid="1000",
    )
    assert result.returncode != 0
    assert "принадлежать root" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink/permission contract")
@pytest.mark.parametrize("dropin_name", ["10-uv-runtime.conf", "unknown.conf"])
def test_any_service_dropin_state_stops_until_owner_integration(
    tmp_path: Path,
    dropin_name: str,
) -> None:
    unit = "[Service]\nExecStart=/srv/second-brain/current/.venv/bin/second-brain\n"
    repository, sha = _init_unit_repository(tmp_path, unit)
    installed_unit = tmp_path / "installed.service"
    installed_unit.write_text(unit, encoding="utf-8")
    installed_unit.chmod(0o644)
    dropin_directory = tmp_path / "dropins"
    dropin_directory.mkdir()
    (dropin_directory / dropin_name).write_text(
        "[Service]\nEnvironment=UV_NO_CACHE=1\n", encoding="utf-8"
    )

    result = _run_systemd_check(tmp_path, repository, sha, installed_unit, dropin_directory)
    assert result.returncode != 0
    assert "drop-in" in result.stderr


@pytest.fixture
def executable_runtime_root() -> Iterator[Path]:
    root = Path(tempfile.mkdtemp(prefix=".runtime-contract-", dir=PROJECT_ROOT))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _runtime_fixture(root: Path) -> tuple[Path, Path, Path]:
    releases_root = root / "releases"
    candidate = releases_root / TEST_SHA
    venv_bin = candidate / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    interpreter = venv_bin / "python"
    interpreter.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    interpreter.chmod(0o755)
    entrypoint = venv_bin / "second-brain"
    entrypoint.write_text(f"#!{interpreter}\nexit 0\n", encoding="utf-8")
    entrypoint.chmod(0o755)
    return releases_root, candidate, entrypoint


def _run_runtime_check(
    releases_root: Path,
    candidate: Path,
    sha: str = TEST_SHA,
) -> subprocess.CompletedProcess[str]:
    bash = _require_posix_helpers()
    return subprocess.run(
        [
            bash,
            str(RUNTIME_CHECK_PATH),
            "--releases-root",
            str(releases_root),
            "--release-sha",
            sha,
            "--candidate",
            str(candidate),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable and symlink contract")
def test_valid_runtime_entrypoint_passes_and_missing_or_symlink_stops(
    executable_runtime_root: Path,
) -> None:
    releases_root, candidate, entrypoint = _runtime_fixture(executable_runtime_root)
    assert entrypoint.is_file()
    assert not entrypoint.is_symlink()
    assert entrypoint.stat().st_mode & 0o111
    passed = _run_runtime_check(releases_root, candidate)
    assert passed.returncode == 0, passed.stderr

    entrypoint.unlink()
    missing = _run_runtime_check(releases_root, candidate)
    assert missing.returncode != 0
    assert "RUNTIME_ENTRYPOINT_NOT_READY / HUMAN_REQUIRED" in missing.stderr

    outside = executable_runtime_root / "outside-entrypoint"
    outside.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    outside.chmod(0o755)
    entrypoint.symlink_to(outside)
    symlink = _run_runtime_check(releases_root, candidate)
    assert symlink.returncode != 0
    assert "unsafe" in symlink.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable contract")
def test_non_executable_runtime_entrypoint_stops(executable_runtime_root: Path) -> None:
    releases_root, candidate, entrypoint = _runtime_fixture(executable_runtime_root)
    entrypoint.chmod(0o644)
    result = _run_runtime_check(releases_root, candidate)
    assert result.returncode != 0
    assert "unsafe" in result.stderr
