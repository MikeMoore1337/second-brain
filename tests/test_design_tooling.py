"""Проверки project-local design tooling pack v1."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = PROJECT_ROOT / ".agents" / "skills"
EMIL_SKILLS = frozenset(
    {
        "animate",
        "emil-design-eng",
        "find-animation-opportunities",
        "improve-animations",
        "prototype",
        "review-animations",
    }
)
EXPECTED_SKILLS = EMIL_SKILLS | {"impeccable"}


def _skill_folder_hash(skill_dir: Path) -> str:
    """Match the skills CLI folder hash over the complete installed payload."""

    digest = hashlib.sha256()
    files = sorted(
        (path for path in skill_dir.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(skill_dir).as_posix(),
    )
    for path in files:
        digest.update(path.relative_to(skill_dir).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_project_local_skill_set_is_exact_and_discoverable() -> None:
    """Payload contains Impeccable and exactly the approved Emil skill set."""

    installed = {path.name for path in SKILLS_ROOT.iterdir() if path.is_dir()}
    assert installed == EXPECTED_SKILLS
    for skill_name in EXPECTED_SKILLS:
        skill_file = SKILLS_ROOT / skill_name / "SKILL.md"
        assert skill_file.is_file()
        assert skill_file.read_text(encoding="utf-8").strip()

    impeccable = SKILLS_ROOT / "impeccable"
    assert (impeccable / "scripts" / "impeccable.cmd").is_file()
    assert (impeccable / "scripts" / "VERSION").read_text(encoding="utf-8").strip()


def test_posix_launcher_has_executable_git_mode() -> None:
    """The POSIX launcher remains runnable after a repository checkout."""

    result = subprocess.run(
        ["git", "ls-files", "--stage", "--", ".agents/skills/impeccable/scripts/impeccable"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.startswith("100755 ")


def test_emil_lock_records_only_approved_project_skills() -> None:
    """Generated lock metadata keeps source and hashes reviewable."""

    payload = json.loads((PROJECT_ROOT / "skills-lock.json").read_text(encoding="utf-8"))
    assert payload["version"] == 1
    skills = payload["skills"]
    assert set(skills) == EMIL_SKILLS
    for skill_name in EMIL_SKILLS:
        record = skills[skill_name]
        assert record["source"] == "emilkowalski/skills"
        assert record["sourceType"] == "github"
        assert record["skillPath"] == f"skills/{skill_name}/SKILL.md"
        assert record["computedHash"] == _skill_folder_hash(SKILLS_ROOT / skill_name)


def test_codex_hook_is_relative_and_optional() -> None:
    """Hook manifest is project-local and cannot encode machine credentials."""

    hook_path = PROJECT_ROOT / ".codex" / "hooks.json"
    hook_text = hook_path.read_text(encoding="utf-8")
    payload = json.loads(hook_text)
    assert payload["hooks"]["PostToolUse"]
    assert payload["hooks"]["Stop"]
    assert "for engine in .agents/skills/impeccable/scripts/bin/*/impeccable" in hook_text
    assert "windows-x64/impeccable.exe" in hook_text
    assert "exit 0" in hook_text
    assert "curl" not in hook_text.casefold()
    assert "D:\\" not in hook_text
    assert "C:\\" not in hook_text
    for forbidden in ("OPENAI_API_KEY", "GH_TOKEN", "GITHUB_TOKEN", "PASSWORD"):
        assert forbidden not in hook_text


def test_tooling_has_no_python_runtime_dependency_or_machine_settings() -> None:
    """Design tooling remains outside the Python application runtime."""

    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8").casefold()
    assert not any(
        marker in pyproject for marker in ("impeccable", "emilkowalski", ".agents", "node")
    )
    assert not (PROJECT_ROOT / "package.json").exists()
    assert not (PROJECT_ROOT / ".impeccable" / "config.local.json").exists()
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (
        ".impeccable/config.local.json",
        ".impeccable/live/",
        ".impeccable/.cache/",
        ".impeccable/*.log",
        ".impeccable/*.ndjson",
        ".impeccable/*.pid",
    ):
        assert pattern in gitignore


def test_provenance_and_licenses_are_present() -> None:
    """Vendored upstream terms and provenance are retained in the repository."""

    docs = (PROJECT_ROOT / "docs" / "design" / "tooling.md").read_text(encoding="utf-8")
    assert "https://github.com/pbakaus/impeccable" in docs
    assert "https://github.com/emilkowalski/skills" in docs
    assert "Apache-2.0" in docs
    assert "MIT" in docs
    assert (PROJECT_ROOT / "docs" / "design" / "licenses" / "impeccable-LICENSE.txt").is_file()
    assert (PROJECT_ROOT / "docs" / "design" / "licenses" / "impeccable-NOTICE.md").is_file()
    assert (PROJECT_ROOT / "docs" / "design" / "licenses" / "emil-skills-LICENSE.txt").is_file()
