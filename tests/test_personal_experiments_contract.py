from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "cognitive-twin" / "personal-experiments-v1-contract.md"
ROADMAP = ROOT / "docs" / "cognitive-twin" / "cognitive-twin-v3-roadmap.md"
DESIGN_ROADMAP = ROOT / "docs" / "cognitive-twin" / "design-roadmap-v1.md"


def test_personal_experiments_contract_is_complete_design_gate() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    required_sections = (
        "## 1. Назначение и граница",
        "## 2. Authority model и слои данных",
        "## 3. Версионирование, fingerprints и лимиты",
        "## 4. Companion record families",
        "## 5. Exact binding и жизненный цикл",
        "## 6. Derived result v1",
        "## 7. Safe Write boundary",
        "## 8. Web/API и privacy",
        "## 9. UI contract",
        "## 10. Alternatives register",
        "## 11. Карта реализации",
        "## 12. Acceptance flags for Stage 14",
    )
    for section in required_sections:
        assert section in text

    required_literals = (
        "personal-experiment-v1",
        "second_brain_personal_experiment: 1",
        "stage12_observation_id",
        "experiment_definition_fingerprint",
        "observed_change_is_not_proof_of_causation",
        "no_automatic_adaptation",
        "X-Second-Brain-Request: personal-experiments-v1",
        "localStorage",
        "Stage 15",
        "HUMAN_REQUIRED",
    )
    for literal in required_literals:
        assert literal in text


def test_stage_14_is_complete_but_stage_15_remains_not_started() -> None:
    roadmap = ROADMAP.read_text(encoding="utf-8")
    design_roadmap = DESIGN_ROADMAP.read_text(encoding="utf-8")
    assert (
        "STAGE 14 COMPLETE / PHASES 14.0–14.6 COMPLETE / PRODUCTION\n"
        "CLOSEOUT COMPLETE" in roadmap
    )
    assert "Phase 14.1 read-side" in design_roadmap
    assert "Phase 14.2 reviewed Safe Write" in design_roadmap
    assert "Phase 14.3 provider-free evaluator" in design_roadmap
    assert "Phase 14.4 owner-only Web/API/UI" in design_roadmap
    assert "Phase 14.5 adversarial security/E2E gate" in design_roadmap
    assert "Phase 14.6 final release/closeout" in design_roadmap
    assert "Stage 15" in roadmap and "PLANNED / NOT STARTED" in roadmap
