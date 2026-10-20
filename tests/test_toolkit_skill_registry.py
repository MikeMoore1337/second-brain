"""Regression checks for scoped, source-pinned development skills."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED_SHA = "3a9bf14772ab579d37c6f4f9615db505ab6ac259"
EXPECTED = {
    "security-boundaries",
    "contract-testing",
    "change-impact-analysis",
    "fullstack-integration",
    "research-verification",
    "authentication-authorization",
    "codebase-knowledge-map",
}


def test_toolkit_development_skills_have_pinned_source_and_metadata() -> None:
    manifest = json.loads(
        (ROOT / ".agents" / "TOOLKIT_SOURCES.json").read_text(encoding="utf-8")
    )
    assert manifest["kind"] == "development_guidance_only"
    assert manifest["source_sha"] == PINNED_SHA
    assert set(manifest["skills"]) == EXPECTED
    for name, details in manifest["skills"].items():
        assert details["target_path"] == f".agents/skills/{name}/SKILL.md"
        skill_file = ROOT / details["target_path"]
        content = skill_file.read_text(encoding="utf-8")
        assert content.splitlines()[0] == "---"
        assert f"name: {name}" in content.split("---", 2)[1]
        assert f"Toolkit source revision: {PINNED_SHA}" in content


def test_preexisting_visual_skills_are_preserved() -> None:
    existing = json.loads((ROOT / "skills-lock.json").read_text(encoding="utf-8"))
    assert {"animate", "prototype", "impeccable"} <= set(existing["skills"])
    assert EXPECTED.isdisjoint(existing["skills"])


def test_vault_is_not_an_agent_skills_installation_target() -> None:
    document = (ROOT / ".agents" / "README.md").read_text(encoding="utf-8")
    assert "second-brain-vault" in document
    assert (ROOT / "AGENTS.md").is_file()
