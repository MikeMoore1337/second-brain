#!/usr/bin/env python3
"""Generate one exact-SHA owner prompt for an application or vault operation."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from second_brain.application.production_prompts import (
    ProductionOperation,
    ProductionPromptContext,
    generate_production_prompt,
)

_DEFAULT_REPOSITORIES = {
    ProductionOperation.APPLICATION_DEPLOY: "https://github.com/MikeMoore1337/second-brain.git",
    ProductionOperation.VAULT_SYNC: "https://github.com/MikeMoore1337/second-brain-vault.git",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate an exact-SHA production operation prompt."
    )
    parser.add_argument(
        "--operation", choices=[item.value for item in ProductionOperation], required=True
    )
    sha = parser.add_mutually_exclusive_group()
    sha.add_argument("--sha")
    sha.add_argument("--source-repo", type=Path)
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--target-repository")
    parser.add_argument("--production-path", required=True)
    parser.add_argument("--app-root")
    parser.add_argument("--backup-root")
    parser.add_argument("--lock-path")
    parser.add_argument("--expected-branch", default="main")
    parser.add_argument("--expected-remote")
    return parser


def _resolve_sha(args: argparse.Namespace) -> str:
    if args.sha is not None:
        return args.sha
    if args.source_repo is None:
        raise ValueError("provide --sha or --source-repo")
    ref = args.ref
    if (
        type(ref) is not str
        or not ref
        or len(ref) > 200
        or any(ord(character) < 32 or character.isspace() for character in ref)
        or ref.startswith("-")
    ):
        raise ValueError("ref is not a safe Git ref")
    try:
        result = subprocess.run(
            ["git", "-C", str(args.source_repo), "rev-parse", "--verify", f"{ref}^{{commit}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("source repository SHA could not be read") from exc
    if result.returncode != 0:
        raise ValueError("source repository SHA could not be read")
    output = result.stdout
    if not isinstance(output, str):
        raise ValueError("source repository SHA could not be read")
    return output.strip()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        operation = ProductionOperation(args.operation)
        if operation is ProductionOperation.VAULT_SYNC and (
            args.app_root is None or args.backup_root is None or args.lock_path is None
        ):
            raise ValueError("vault-sync requires --app-root, --backup-root and --lock-path")
        target_repository = args.target_repository or _DEFAULT_REPOSITORIES[operation]
        context = ProductionPromptContext(
            target_sha=_resolve_sha(args),
            target_repository=target_repository,
            production_path=args.production_path,
            app_root=args.app_root,
            backup_root=args.backup_root,
            lock_path=args.lock_path,
            expected_branch=args.expected_branch,
            expected_remote=args.expected_remote,
        )
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(generate_production_prompt(operation, context), end="")
    except ValueError as exc:
        print(f"error: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
