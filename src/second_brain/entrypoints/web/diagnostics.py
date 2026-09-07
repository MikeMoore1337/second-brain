"""Lazy Web composition for the application-owned doctor report."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from second_brain.adapters.search import SqliteFts5SearchIndex
from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.diagnostics import BuildDoctorReport, DoctorReport
from second_brain.config import load_config


class DiagnosticsService(Protocol):
    """Minimal injectable seam for one current doctor report."""

    def build(self) -> DoctorReport:
        """Build the existing bounded report through the application core."""


@dataclass(frozen=True, slots=True)
class LazyDoctorService:
    """Resolve configuration and scan the vault only after explicit refresh."""

    env_file: Path | None = None
    vault_path_override: str | None = None

    def build(self) -> DoctorReport:
        """Return the exact application DoctorReport without caching or writes."""

        try:
            config = load_config(
                env_file=self.env_file,
                vault_path_override=self.vault_path_override,
            )
            reader = FileSystemVaultReader(config.vault_path)
        except Exception:
            return BuildDoctorReport(
                reader=None,
                config_resolvable=False,
                search_index_factory=SqliteFts5SearchIndex,
            ).execute()
        return BuildDoctorReport(
            reader,
            config_resolvable=True,
            search_index_factory=SqliteFts5SearchIndex,
        ).execute()


def build_production_diagnostics_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> LazyDoctorService:
    """Build a lazy service without config, vault, search or provider side effects."""

    return LazyDoctorService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


class DiagnosticsRequestPayload(BaseModel):
    """Strict empty JSON request for an explicit diagnostics refresh."""

    model_config = ConfigDict(extra="forbid", strict=True)


__all__ = [
    "DiagnosticsRequestPayload",
    "DiagnosticsService",
    "LazyDoctorService",
    "build_production_diagnostics_service",
]
