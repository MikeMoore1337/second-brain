#!/usr/bin/env python3
"""Run the bounded Night Shift worktree cleanup protocol.

The caller supplies JSON state already resolved from GitHub.  This command
does not contact GitHub, inspect arbitrary sibling directories, or perform
raw filesystem deletion.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from second_brain.application.worktree_cleanup import (  # noqa: E402
    WorktreeCleanup,
    WorktreeCleanupInputError,
    load_cleanup_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the bounded CLI parser."""

    parser = argparse.ArgumentParser(
        description="Safely clean completed registered Git worktrees after a verified merge."
    )
    parser.add_argument("--repo", type=Path, required=True, help="primary repository root")
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="JSON receipt path, or '-' to read the receipt from stdin",
    )
    parser.add_argument(
        "--vault-root",
        type=Path,
        action="append",
        default=[],
        help="additional protected vault root; may be repeated",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="classify eligible worktrees without remove or prune",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="alias for --dry-run, suitable for bounded inspection",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def _read_manifest(path: Path) -> str:
    if str(path) == "-":
        return sys.stdin.read()
    return path.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Execute one non-network cleanup pass."""

    args = build_parser().parse_args(argv)
    try:
        manifest = load_cleanup_manifest(_read_manifest(args.manifest))
    except (OSError, WorktreeCleanupInputError) as exc:
        print(f"cleanup_input_error: {exc}", file=sys.stderr)
        return 2

    summary = WorktreeCleanup(
        args.repo,
        vault_roots=args.vault_root,
    ).run(manifest, dry_run=args.dry_run or args.list)
    if args.format == "json":
        print(json.dumps(summary.as_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print(summary.render_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
