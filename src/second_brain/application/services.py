"""Read-only application services для Foundation."""

from __future__ import annotations

from dataclasses import dataclass

from second_brain.application.ports import VaultReader
from second_brain.application.reports import ScanReport
from second_brain.application.validation import build_report


@dataclass(frozen=True, slots=True)
class ValidateVault:
    """Запустить use case проверки vault через read-only port."""

    reader: VaultReader

    def execute(self) -> ScanReport:
        """Оркестрировать чтение и вернуть полный validation report."""

        return build_report(self.reader.scan())


@dataclass(frozen=True, slots=True)
class DoctorVault:
    """Запустить diagnostic use case через ту же read-only границу."""

    reader: VaultReader

    def execute(self) -> ScanReport:
        """Оркестрировать чтение и вернуть полный diagnostic report."""

        return build_report(self.reader.scan())
