"""Checksums that let ``alltoone unpack`` detect corrupted context files.

The manifest is not a security feature: someone who edits a context file can
recompute every hash. It exists to catch the realistic failure modes — a
truncated copy, a lost line, a mangled character after pasting through a chat.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable

MANIFEST_MAGIC = "sha256 manifest v1"
TOTAL_PREFIX = "total "
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def file_digest(text: str) -> str:
    """SHA-256 of the text exactly as it is stored in the context file."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest_total(entries: dict[str, str]) -> str:
    """Hash of the manifest itself, so a dropped line is detectable."""
    hasher = hashlib.sha256()
    for path in sorted(entries):
        hasher.update(f"{entries[path]}  {path}\n".encode("utf-8"))
    return hasher.hexdigest()


@dataclass
class Integrity:
    entries: dict[str, str] = field(default_factory=dict)
    total: str | None = None

    def __bool__(self) -> bool:
        return bool(self.entries)


def build_manifest(files: Iterable) -> tuple[list[str], dict[str, str]]:
    """Return manifest lines plus the path → digest mapping."""
    entries = {item.rel: file_digest(item.text) for item in files}
    lines = [MANIFEST_MAGIC]
    for path in sorted(entries):
        lines.append(f"{entries[path]}  {path}")
    lines.append(f"{TOTAL_PREFIX}{manifest_total(entries)}")
    return lines, entries


def parse_manifest_lines(lines: Iterable[str]) -> Integrity:
    """Parse ``<hex>  <path>`` lines and the trailing ``total <hex>``."""
    integrity = Integrity()
    for raw in lines:
        line = raw.strip()
        if not line or line == MANIFEST_MAGIC:
            continue
        if line.startswith(TOTAL_PREFIX):
            value = line[len(TOTAL_PREFIX) :].strip()
            if HEX64.match(value):
                integrity.total = value
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, path = parts
        if HEX64.match(digest):
            integrity.entries[path.strip()] = digest
    return integrity


def verify(files: Iterable, integrity: Integrity) -> list[str]:
    """Compare parsed files against the manifest. Returns human-readable problems."""
    if not integrity:
        return []

    actual = {item.path: file_digest(item.text) for item in files}
    problems: list[str] = []

    for path, expected in integrity.entries.items():
        if path not in actual:
            problems.append(f"missing file: {path}")
        elif actual[path] != expected:
            problems.append(f"checksum mismatch: {path}")

    for path in actual:
        if path not in integrity.entries:
            problems.append(f"file without a checksum: {path}")

    if integrity.total and manifest_total(integrity.entries) != integrity.total:
        problems.append("the integrity block itself is damaged (manifest checksum mismatch)")

    return problems
