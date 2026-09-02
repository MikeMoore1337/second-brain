"""Ports, используемые application use cases."""

from __future__ import annotations

from typing import Protocol

from second_brain.application.reports import VaultSnapshot


class VaultReader(Protocol):
    """Read-only граница реализации vault."""

    def scan(self) -> VaultSnapshot:
        """Прочитать vault без его изменения и вернуть raw DTO."""
