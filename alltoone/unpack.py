"""Restore a project tree from a context file produced by alltoone."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

MARKDOWN_HEADING = re.compile(r"^### `(.+)`\s*$")
MARKDOWN_FENCE = re.compile(r"^(`{3,})([^`]*)$")
PLAIN_RULE = "=" * 78
PLAIN_THIN = "-" * 78


class UnpackError(Exception):
    """Raised when a context file cannot be parsed or is unsafe to write."""


@dataclass
class UnpackedFile:
    path: str
    text: str


@dataclass
class UnpackResult:
    fmt: str
    dest: Path
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(len(path.encode("utf-8")) for path in self.written)


def detect_format(text: str, filename: str = "") -> str:
    """Guess the format of a context file."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".xml":
        return "xml"
    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped.startswith(("<?xml", "<context")):
        return "xml"
    if stripped.startswith("# Project context:"):
        return "markdown"
    if re.search(r"^PROJECT: ", text, re.MULTILINE):
        return "plain"
    if MARKDOWN_HEADING.search(text):
        return "markdown"
    raise UnpackError("cannot detect the format of the context file; pass --format")


def parse_markdown(text: str) -> list[UnpackedFile]:
    """Extract ``### `path``` sections followed by a fenced code block."""
    lines = text.splitlines(keepends=True)
    files: list[UnpackedFile] = []
    index = 0
    total = len(lines)

    while index < total:
        heading = MARKDOWN_HEADING.match(lines[index].rstrip("\r\n"))
        if not heading:
            index += 1
            continue

        path = heading.group(1)
        index += 1
        while index < total and lines[index].strip() == "":
            index += 1
        if index >= total:
            raise UnpackError(f"{path}: code block not found after the heading")

        opening = MARKDOWN_FENCE.match(lines[index].rstrip("\r\n"))
        if not opening:
            raise UnpackError(f"{path}: expected a fenced code block after the heading")
        ticks = opening.group(1)
        index += 1

        body: list[str] = []
        closed = False
        while index < total:
            line = lines[index]
            if line.rstrip("\r\n").strip() == ticks:
                closed = True
                index += 1
                break
            body.append(line)
            index += 1
        if not closed:
            raise UnpackError(f"{path}: unterminated code block")

        content = "".join(body)
        # The writer inserted exactly one newline before the closing fence.
        if content.endswith("\r\n"):
            content = content[:-2]
        elif content.endswith("\n"):
            content = content[:-1]
        files.append(UnpackedFile(path, content))

    return files


def parse_xml(text: str) -> list[UnpackedFile]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise UnpackError(f"invalid XML: {exc}") from None

    files: list[UnpackedFile] = []
    for element in root.iter("file"):
        path = element.get("path")
        if not path:
            raise UnpackError("found a <file> element without a path attribute")
        files.append(UnpackedFile(path, element.text or ""))
    return files


def parse_plain(text: str) -> list[UnpackedFile]:
    """Extract ``FILE: path`` blocks delimited by rules."""
    lines = text.splitlines()
    files: list[UnpackedFile] = []
    index = 0
    total = len(lines)

    while index < total:
        is_header = (
            lines[index] == PLAIN_RULE
            and index + 2 < total
            and lines[index + 1].startswith("FILE: ")
            and lines[index + 2] == PLAIN_THIN
        )
        if not is_header:
            index += 1
            continue

        path = lines[index + 1][len("FILE: ") :]
        index += 3
        body: list[str] = []
        while index < total and lines[index] != PLAIN_RULE:
            body.append(lines[index])
            index += 1
        # splitlines() drops the newline that join() added back, so this is exact.
        files.append(UnpackedFile(path, "\n".join(body)))

    return files


PARSERS = {"markdown": parse_markdown, "xml": parse_xml, "plain": parse_plain}


def parse(text: str, fmt: str | None = None, filename: str = "") -> tuple[str, list[UnpackedFile]]:
    fmt = fmt or detect_format(text, filename)
    try:
        parser = PARSERS[fmt]
    except KeyError:
        raise UnpackError(
            f"unknown format {fmt!r}, expected one of: {', '.join(PARSERS)}"
        ) from None
    return fmt, parser(text)


def safe_target(root: Path, rel_path: str) -> Path:
    """Resolve a path from the context file, refusing anything outside ``root``."""
    normalized = rel_path.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise UnpackError(f"refusing absolute path: {rel_path}")

    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise UnpackError(f"refusing path containing '..': {rel_path}")
    if not parts:
        raise UnpackError(f"empty file path: {rel_path!r}")

    root_resolved = root.resolve()
    target = (root_resolved / Path(*parts)).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise UnpackError(f"path escapes the destination directory: {rel_path}")
    return target


def unpack(
    text: str,
    dest: Path,
    *,
    fmt: str | None = None,
    filename: str = "",
    overwrite: bool = False,
    dry_run: bool = False,
) -> UnpackResult:
    """Write every file from ``text`` under ``dest``."""
    fmt, files = parse(text, fmt, filename)
    if not files:
        raise UnpackError("no files found in the context file")

    result = UnpackResult(fmt=fmt, dest=dest)
    seen: set[str] = set()

    for item in files:
        target = safe_target(dest, item.path)
        key = str(target).lower()
        if key in seen:
            raise UnpackError(f"duplicate file in the context: {item.path}")
        seen.add(key)

        if target.exists() and not overwrite:
            result.skipped.append(item.path)
            continue
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="" keeps the bytes exactly as they were packed.
            target.write_text(item.text, encoding="utf-8", newline="")
        result.written.append(item.path)

    return result
