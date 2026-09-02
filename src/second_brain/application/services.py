"""Read-only application services для Foundation."""

from __future__ import annotations

from dataclasses import dataclass

from second_brain.application.ports import VaultReader
from second_brain.domain.models import ScanReport


@dataclass(frozen=True, slots=True)
class ValidateVault:
    """Запустить use case проверки vault через read-only port."""

    reader: VaultReader

    def execute(self) -> ScanReport:
        """Вернуть полный validation report."""

        return self.reader.scan()


@dataclass(frozen=True, slots=True)
class DoctorVault:
    """Запустить diagnostic use case через ту же read-only границу."""

    reader: VaultReader

    def execute(self) -> ScanReport:
        """Вернуть полный diagnostic report."""

        return self.reader.scan()
