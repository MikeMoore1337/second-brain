"""Lazy Web composition for read-only Search and canonical Retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from second_brain.adapters.search import SqliteFts5SearchIndex
from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.ports import RetrievedNote, SearchHit, SearchRequest
from second_brain.application.search import RetrieveManagedNote, SearchVault
from second_brain.config import load_config


class SearchService(Protocol):
    """Minimal injectable Web seam for Search and canonical Retrieval."""

    def search(self, request: SearchRequest) -> tuple[SearchHit, ...]:
        """Find ranked hits from the current vault."""

    def retrieve(self, note_id: UUID) -> RetrievedNote:
        """Read one current canonical note by stable UUID."""


@dataclass(frozen=True, slots=True)
class LazyVaultSearchService:
    """Load vault configuration only when a Search/Retrieval request arrives."""

    env_file: Path | None = None
    vault_path_override: str | None = None

    def search(self, request: SearchRequest) -> tuple[SearchHit, ...]:
        """Build a disposable in-memory FTS5 index from the current scan."""

        reader = self._vault_reader()
        index = SqliteFts5SearchIndex()
        try:
            return SearchVault(reader, index).execute(request)
        finally:
            index.close()

    def retrieve(self, note_id: UUID) -> RetrievedNote:
        """Re-scan the current vault and return the exact current note body."""

        return RetrieveManagedNote(self._vault_reader()).execute(note_id)

    def _vault_reader(self) -> FileSystemVaultReader:
        """Resolve the same explicit CLI-selected config used by Safe Write."""

        config = load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )
        return FileSystemVaultReader(config.vault_path)


def build_production_search_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> LazyVaultSearchService:
    """Build a lazy service without reading config, vault, or search state."""

    return LazyVaultSearchService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


__all__ = [
    "LazyVaultSearchService",
    "SearchService",
    "build_production_search_service",
]
