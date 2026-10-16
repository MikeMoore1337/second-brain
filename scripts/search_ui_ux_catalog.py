#!/usr/bin/env python3
"""Search SHA-pinned UI UX Pro Max CSV reference catalogs without network access."""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".agents/references/external-quality/ui-ux-pro-max"
CATALOGS = {
    "charts": "charts.csv",
    "app-interface": "app-interface.csv",
    "ux-guidelines": "ux-guidelines.csv",
}


def search(catalog: str, query: str, limit: int = 8) -> list[dict[str, str]]:
    tokens = re.findall(r"\w+", query.casefold())
    if not tokens:
        raise ValueError("Query must contain at least one word")
    matches: list[tuple[int, dict[str, str]]] = []
    with (DATA / CATALOGS[catalog]).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            text = " ".join(value or "" for value in row.values()).casefold()
            if all(token in text for token in tokens):
                score = sum(text.count(token) for token in tokens)
                matches.append((score, row))
    matches.sort(key=lambda item: -item[0])
    return [row for _, row in matches[:limit]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, choices=sorted(CATALOGS))
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.limit <= 30:
        parser.error("--limit must be between 1 and 30")
    print(json.dumps(search(args.catalog, args.query, args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
