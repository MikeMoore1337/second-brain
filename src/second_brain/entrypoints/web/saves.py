"""Lazy Web composition for the existing vault Safe Write services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from second_brain.adapters.vault import FileSystemVaultReader, FileSystemVaultWriter
from second_brain.application.llm import NoteDraft
from second_brain.application.research import SourceProvenance
from second_brain.application.research_draft import ReviewedResearchDraft
from second_brain.application.services import (
    CreateManagedNoteFromDraft,
    CreateManagedNoteFromReviewedResearchDraft,
)
from second_brain.application.writes import (
    CreateManagedNoteFromDraftRequest,
    CreateManagedNoteFromReviewedResearchDraftRequest,
    CreateManagedNoteResult,
)
from second_brain.config import load_config


class DraftSaveService(Protocol):
    """Minimal injectable seam for explicit Web Save."""

    def save_text(self, draft: NoteDraft) -> CreateManagedNoteResult:
        """Save one reviewed text draft through the existing Safe Write use case."""

    def save_research(
        self,
        draft: NoteDraft,
        source: SourceProvenance,
    ) -> CreateManagedNoteResult:
        """Save one reviewed research draft with server-verified provenance."""


@dataclass(frozen=True, slots=True)
class LazyVaultDraftSaveService:
    """Load vault configuration and construct adapters only during explicit Save."""

    env_file: Path | None = None
    vault_path_override: str | None = None

    def save_text(self, draft: NoteDraft) -> CreateManagedNoteResult:
        """Execute text Safe Write with server-owned ``apply=True`` semantics."""

        reader, writer = self._vault_adapters()
        return CreateManagedNoteFromDraft(reader, writer).execute(
            CreateManagedNoteFromDraftRequest(draft=draft, apply=True)
        )

    def save_research(
        self,
        draft: NoteDraft,
        source: SourceProvenance,
    ) -> CreateManagedNoteResult:
        """Execute reviewed research Safe Write without another research/LLM call."""

        reader, writer = self._vault_adapters()
        reviewed = ReviewedResearchDraft(draft=draft, sources=(source,))
        return CreateManagedNoteFromReviewedResearchDraft(reader, writer).execute(
            CreateManagedNoteFromReviewedResearchDraftRequest(
                reviewed_draft=reviewed,
                apply=True,
            )
        )

    def _vault_adapters(self) -> tuple[FileSystemVaultReader, FileSystemVaultWriter]:
        """Resolve CLI-selected configuration at the exact write boundary."""

        config = load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )
        return FileSystemVaultReader(config.vault_path), FileSystemVaultWriter(config.vault_path)


def build_production_save_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> LazyVaultDraftSaveService:
    """Build a lazy service without reading config, vault, or filesystem state."""

    return LazyVaultDraftSaveService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


__all__ = [
    "DraftSaveService",
    "LazyVaultDraftSaveService",
    "build_production_save_service",
]
