"""Ports, используемые application use cases."""

from __future__ import annotations

from typing import Protocol

from second_brain.domain.models import ScanReport


class VaultReader(Protocol):
    """Read-only граница реализации vault."""

    def scan(self) -> ScanReport:
        """Проверить vault без его изменения."""
