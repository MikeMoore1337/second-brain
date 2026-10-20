"""Reject accidental removal of the Second Brain pre-approval UI read-only protocol."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DesignOrchestraAdapterTests(unittest.TestCase):
    def test_scope_and_vault_boundary(self):
        s = (ROOT / ".agents/skills/ux-ui-design-orchestra/SKILL.md").read_text(encoding="utf-8")
        d = (ROOT / "docs/development/ux-ui-design-orchestra.md").read_text(encoding="utf-8")
        for token in ("name: ux-ui-design-orchestra", "READ_ONLY", "SEQUENTIAL",
                      "second-brain-vault", "DESIGN.md", "Принимаю"):
            self.assertIn(token, s)
        self.assertIn("VISUAL_PACKAGE_MISSING", d)


if __name__ == "__main__":
    unittest.main()
