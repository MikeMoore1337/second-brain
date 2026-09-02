"""Минимальный injectable runner для системных CLI adapters."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Минимальная часть результата subprocess, нужная adapters."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    """Seam для unit tests без вызова реального GitHub/network."""

    def __call__(self, argv: Sequence[str], cwd: Path) -> CommandResult:
        """Выполнить явный argv в заданном рабочем каталоге."""


def run_command(argv: Sequence[str], cwd: Path) -> CommandResult:
    """Запустить CLI без shell и без конкатенации командных строк."""

    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
        )
    except OSError:
        raise
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)
