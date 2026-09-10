"""Lazy Web composition for the existing vault Safe Write services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from second_brain.adapters.vault import FileSystemVaultReader, FileSystemVaultWriter
from second_brain.application.decision_journal import (
    DecisionJournalDraft,
    OutcomeObservationDraft,
)
from second_brain.application.llm import NoteDraft
from second_brain.application.personal_memory import PersonalMemoryDraft
from second_brain.application.research import SourceProvenance
from second_brain.application.research_draft import ReviewedResearchDraft
from second_brain.application.services import (
    CreateManagedNoteFromDecisionJournalDraft,
    CreateManagedNoteFromDraft,
    CreateManagedNoteFromOutcomeObservationDraft,
    CreateManagedNoteFromPersonalMemoryDraft,
    CreateManagedNoteFromReviewedResearchDraft,
)
from second_brain.application.writes import (
    CreateManagedNoteFromDecisionJournalDraftRequest,
    CreateManagedNoteFromDraftRequest,
    CreateManagedNoteFromOutcomeObservationDraftRequest,
    CreateManagedNoteFromPersonalMemoryDraftRequest,
    CreateManagedNoteFromReviewedResearchDraftRequest,
    CreateManagedNoteResult,
)
from second_brain.config import load_config


class DraftSaveService(Protocol):
    """Minimal injectable seam for the two explicit Web Save phases."""

    def prepare_text(self, draft: NoteDraft) -> CreateManagedNoteResult:
        """Prepare one reviewed text draft without publishing it."""

    def prepare_research(
        self,
        draft: NoteDraft,
        source: SourceProvenance,
    ) -> CreateManagedNoteResult:
        """Prepare one reviewed research draft without publishing it."""

    def apply_text(self, draft: NoteDraft) -> CreateManagedNoteResult:
        """Apply one previously prepared text draft through Safe Write."""

    def prepare_personal_memory(self, draft: PersonalMemoryDraft) -> CreateManagedNoteResult:
        """Prepare one reviewed Personal Memory draft without publishing it."""

    def apply_personal_memory(self, draft: PersonalMemoryDraft) -> CreateManagedNoteResult:
        """Apply one reviewed Personal Memory draft through Safe Write."""

    def apply_research(
        self,
        draft: NoteDraft,
        source: SourceProvenance,
    ) -> CreateManagedNoteResult:
        """Apply one previously prepared research draft through Safe Write."""

    def prepare_decision_journal(
        self,
        draft: DecisionJournalDraft,
    ) -> CreateManagedNoteResult:
        """Prepare one structured Decision Journal through Stage 2 core."""

    def apply_decision_journal(
        self,
        draft: DecisionJournalDraft,
        *,
        plan_sha256: str,
    ) -> CreateManagedNoteResult:
        """Apply one confirmed Decision Journal through Stage 2 core."""

    def prepare_outcome_observation(
        self,
        draft: OutcomeObservationDraft,
    ) -> CreateManagedNoteResult:
        """Prepare one structured Outcome Observation through Stage 2 core."""

    def apply_outcome_observation(
        self,
        draft: OutcomeObservationDraft,
        *,
        plan_sha256: str,
    ) -> CreateManagedNoteResult:
        """Apply one confirmed Outcome Observation through Stage 2 core."""


@dataclass(frozen=True, slots=True)
class LazyVaultDraftSaveService:
    """Load vault configuration and construct adapters only during explicit Save."""

    env_file: Path | None = None
    vault_path_override: str | None = None

    def prepare_text(self, draft: NoteDraft) -> CreateManagedNoteResult:
        """Execute text Safe Write dry-run with server-owned ``apply=False``."""

        return self._execute_text(draft, apply=False)

    def apply_text(self, draft: NoteDraft) -> CreateManagedNoteResult:
        """Execute text Safe Write apply after server-side confirmation."""

        return self._execute_text(draft, apply=True)

    def prepare_personal_memory(self, draft: PersonalMemoryDraft) -> CreateManagedNoteResult:
        """Execute Personal Memory Safe Write dry-run with server-owned apply=False."""

        return self._execute_personal_memory(draft, apply=False)

    def apply_personal_memory(self, draft: PersonalMemoryDraft) -> CreateManagedNoteResult:
        """Execute Personal Memory Safe Write apply after server-side confirmation."""

        return self._execute_personal_memory(draft, apply=True)

    def prepare_research(
        self,
        draft: NoteDraft,
        source: SourceProvenance,
    ) -> CreateManagedNoteResult:
        """Execute reviewed research dry-run with ``apply=False``."""

        return self._execute_research(draft, source, apply=False)

    def apply_research(
        self,
        draft: NoteDraft,
        source: SourceProvenance,
    ) -> CreateManagedNoteResult:
        """Execute reviewed research apply after server-side confirmation."""

        return self._execute_research(draft, source, apply=True)

    def prepare_decision_journal(self, draft: DecisionJournalDraft) -> CreateManagedNoteResult:
        """Execute Decision Journal Safe Write dry-run with ``apply=False``."""

        return self._execute_decision_journal(draft, apply=False)

    def apply_decision_journal(
        self,
        draft: DecisionJournalDraft,
        *,
        plan_sha256: str,
    ) -> CreateManagedNoteResult:
        """Execute Decision Journal Safe Write apply with ``apply=True``."""

        return self._execute_decision_journal(
            draft,
            apply=True,
            expected_plan_sha256=plan_sha256,
        )

    def prepare_outcome_observation(
        self,
        draft: OutcomeObservationDraft,
    ) -> CreateManagedNoteResult:
        """Execute Outcome Observation Safe Write dry-run with ``apply=False``."""

        return self._execute_outcome_observation(draft, apply=False)

    def apply_outcome_observation(
        self,
        draft: OutcomeObservationDraft,
        *,
        plan_sha256: str,
    ) -> CreateManagedNoteResult:
        """Execute Outcome Observation Safe Write apply with ``apply=True``."""

        return self._execute_outcome_observation(
            draft,
            apply=True,
            expected_plan_sha256=plan_sha256,
        )

    def _execute_text(self, draft: NoteDraft, *, apply: bool) -> CreateManagedNoteResult:
        """Run the existing text Safe Write with a server-selected phase."""

        reader, writer = self._vault_adapters()
        return CreateManagedNoteFromDraft(reader, writer).execute(
            CreateManagedNoteFromDraftRequest(draft=draft, apply=apply)
        )

    def _execute_research(
        self,
        draft: NoteDraft,
        source: SourceProvenance,
        *,
        apply: bool,
    ) -> CreateManagedNoteResult:
        """Run the existing reviewed research Safe Write with a server-selected phase."""

        reader, writer = self._vault_adapters()
        reviewed = ReviewedResearchDraft(draft=draft, sources=(source,))
        return CreateManagedNoteFromReviewedResearchDraft(reader, writer).execute(
            CreateManagedNoteFromReviewedResearchDraftRequest(
                reviewed_draft=reviewed,
                apply=apply,
            )
        )

    def _execute_personal_memory(
        self,
        draft: PersonalMemoryDraft,
        *,
        apply: bool,
    ) -> CreateManagedNoteResult:
        """Run the existing Personal Memory Safe Write composition boundary."""

        reader, writer = self._vault_adapters()
        return CreateManagedNoteFromPersonalMemoryDraft(reader, writer).execute(
            CreateManagedNoteFromPersonalMemoryDraftRequest(
                draft=draft,
                apply=apply,
            )
        )

    def _execute_decision_journal(
        self,
        draft: DecisionJournalDraft,
        *,
        apply: bool,
        expected_plan_sha256: str | None = None,
    ) -> CreateManagedNoteResult:
        """Compose the structured Decision Journal with the existing core use case."""

        reader, writer = self._vault_adapters()
        return CreateManagedNoteFromDecisionJournalDraft(reader, writer).execute(
            CreateManagedNoteFromDecisionJournalDraftRequest(
                draft=draft,
                apply=apply,
                expected_plan_sha256=expected_plan_sha256,
            )
        )

    def _execute_outcome_observation(
        self,
        draft: OutcomeObservationDraft,
        *,
        apply: bool,
        expected_plan_sha256: str | None = None,
    ) -> CreateManagedNoteResult:
        """Compose the structured Outcome with current-target core validation."""

        reader, writer = self._vault_adapters()
        return CreateManagedNoteFromOutcomeObservationDraft(reader, writer).execute(
            CreateManagedNoteFromOutcomeObservationDraftRequest(
                draft=draft,
                apply=apply,
                expected_plan_sha256=expected_plan_sha256,
            )
        )

    def _vault_adapters(self) -> tuple[FileSystemVaultReader, FileSystemVaultWriter]:
        """Resolve CLI-selected configuration at the exact write boundary."""

        config = load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )
        return (
            FileSystemVaultReader(config.vault_path),
            FileSystemVaultWriter(
                config.vault_path,
                operation_lock_path=config.vault_operation_lock_path,
            ),
        )


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
