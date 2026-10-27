"""Turn one ORIGINAL Harness2 upstream summary into an untrusted, read-only draft note.

Does not invoke, embed, reimplement or modify Harness2 or write a private vault.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
from pathlib import Path

UPSTREAM_SHA = "1cfe254e8d25b15f37048c3730134686abe60335"
MAX_BYTES = 64 * 1024
SAFE_SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}\Z")
MODES = {"agg", "seq"}
STATUSES = {"running", "ok", "partial", "failed"}


class UntrustedUpstreamError(ValueError):
    pass


def note_from_summary(path: Path) -> str:
    if not path.is_absolute() or path.name != "summary.json":
        raise UntrustedUpstreamError("ABSOLUTE_SUMMARY_REQUIRED")
    if any(node.is_symlink() for node in (path, *path.parents)):
        raise UntrustedUpstreamError("LINKED_PATH")
    if not path.is_file():
        raise UntrustedUpstreamError("FILE_NOT_FOUND")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
        raise UntrustedUpstreamError("UNSAFE_SUMMARY")
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise UntrustedUpstreamError("SIZE_LIMIT")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise UntrustedUpstreamError("BAD_JSON") from exc
    expected = {"task_id", "domain", "k", "seed", "status", "base", "modes", "selections", "errors"}
    if not isinstance(doc, dict) or set(doc) != expected:
        raise UntrustedUpstreamError("UPSTREAM_SCHEMA")
    if (
        type(doc["task_id"]) is not str
        or not SAFE_SLUG.fullmatch(doc["task_id"])
        or type(doc["domain"]) is not str
        or not SAFE_SLUG.fullmatch(doc["domain"])
        or type(doc["k"]) is not int
        or not 1 <= doc["k"] <= 100
        or type(doc["status"]) is not str
        or doc["status"] not in STATUSES
        or type(doc["modes"]) is not dict
        or not set(doc["modes"]).issubset(MODES)
        or type(doc["selections"]) is not dict
        or not set(doc["selections"]).issubset(MODES)
        or type(doc["base"]) is not list
        or type(doc["errors"]) is not list
    ):
        raise UntrustedUpstreamError("INVALID_FIELDS")
    file_hash = hashlib.sha256(raw).hexdigest()
    modes = ", ".join(sorted(doc["modes"])) or "нет"
    return (
        "# Harness² - черновик\n\n"
        f"- Source upstream SHA: {UPSTREAM_SHA}\n"
        f"- Input summary SHA256: {file_hash}\n"
        f"- Задача: {doc['task_id']}\n"
        f"- Домен: {doc['domain']}\n"
        f"- Бюджет k: {doc['k']}\n"
        f"- Режимы: {modes}\n"
        f"- Заявленный исходным файлом статус: {doc['status']}\n"
        "- Достоверность результата: НЕ ПОДТВЕРЖДЕНА\n"
        "- Работа модели и независимое оценивание: НЕ ПРОВЕРЯЛИСЬ ЭТИМ ИМПОРТОМ\n"
        "- Сохранение: ТОЛЬКО вручную после просмотра и подтверждения\n\n"
        "Это внешние метаданные, не свидетельство качества Second Brain.\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    args = parser.parse_args(argv)
    try:
        note = note_from_summary(args.summary)
    except (UntrustedUpstreamError, OSError) as exc:
        print(f"HARNESS2_IMPORT_DENIED: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
