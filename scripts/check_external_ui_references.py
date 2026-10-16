#!/usr/bin/env python3
"""Offline SHA verification for external skills reference copies."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "docs/development/external-ui-sources.json"


def main() -> int:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    if lock.get("schema_version") != 1:
        raise ValueError("Unsupported lockfile schema")
    problems: list[str] = []
    for item in lock["files"]:
        target = (ROOT / item["local"]).resolve()
        if target == ROOT or ROOT not in target.parents:
            problems.append(f"Unsafe path: {item['local']}")
            continue
        if item["ref"] != lock["sources"][item["source"]]["commit"]:
            problems.append(f"Unpinned source: {item['local']}")
        elif not target.is_file():
            problems.append(f"Missing file: {item['local']}")
        else:
            data = target.read_bytes()
            blob = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            if blob != item["git_blob_sha"]:
                problems.append(f"SHA mismatch: {item['local']}")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"Verified {len(lock['files'])} reference files (offline).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
