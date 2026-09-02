"""Filesystem- и Markdown-adapter для vault."""

from second_brain.adapters.vault.scanner import FileSystemVaultReader
from second_brain.adapters.vault.writer import FileSystemVaultWriter

__all__ = ["FileSystemVaultReader", "FileSystemVaultWriter"]
