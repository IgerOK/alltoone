"""gitignore-style pattern matching.

The rules here follow the parts of the gitignore spec that matter for packing a
project: comments, negations, directory-only patterns, anchoring and ``**``.
Matching is done against paths that are relative to the root and always use
forward slashes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_IGNORES: tuple[str, ...] = (
    # version control / tooling
    ".git/",
    ".hg/",
    ".svn/",
    ".idea/",
    ".vscode/",
    ".vs/",
    ".zed/",
    # dependencies
    "node_modules/",
    "bower_components/",
    ".venv/",
    "venv/",
    "env/",
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".tox/",
    ".eggs/",
    "*.egg-info/",
    # build output
    "dist/",
    "build/",
    "out/",
    "target/",
    "bin/",
    "obj/",
    "coverage/",
    ".next/",
    ".nuxt/",
    ".svelte-kit/",
    ".cache/",
    "vendor/",
    # lockfiles and generated bundles (pure noise for an LLM)
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "*.min.js",
    "*.min.css",
    "*.map",
    # OS junk
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
)


def _translate(pattern: str) -> str:
    """Translate a glob pattern into a regular expression body."""
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        char = pattern[i]
        if char == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                i += 2
                if i < n and pattern[i] == "/":
                    # ``**/`` matches zero or more directories
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
            i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        elif char == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append(r"\[")
                i += 1
            else:
                body = pattern[i + 1 : j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j + 1
        elif char == "\\":
            if i + 1 < n:
                out.append(re.escape(pattern[i + 1]))
                i += 2
            else:
                out.append(r"\\")
                i += 1
        else:
            out.append(re.escape(char))
            i += 1
    return "".join(out)


@dataclass(frozen=True)
class GlobPattern:
    """A single compiled pattern."""

    pattern: str
    regex: re.Pattern[str]
    negated: bool = False
    dir_only: bool = False

    def matches(self, rel_path: str, is_dir: bool = False) -> bool:
        if self.dir_only and not is_dir:
            return False
        return self.regex.match(rel_path) is not None


def compile_pattern(line: str) -> GlobPattern | None:
    """Compile one gitignore-style line. Returns ``None`` for blanks/comments."""
    text = line.rstrip("\n").rstrip("\r")
    stripped = text.strip()
    if not stripped or stripped.startswith("#"):
        return None

    negated = stripped.startswith("!")
    if negated:
        stripped = stripped[1:]
    if not stripped:
        return None

    dir_only = stripped.endswith("/")
    if dir_only:
        stripped = stripped.rstrip("/")
    if not stripped:
        return None

    anchored = stripped.startswith("/")
    if anchored:
        stripped = stripped[1:]
    # A slash anywhere else also anchors the pattern to its base directory.
    if "/" in stripped:
        anchored = True

    body = _translate(stripped)
    regex = re.compile(("^" if anchored else "^(?:.*/)?") + body + "$")
    return GlobPattern(
        pattern=text.strip(), regex=regex, negated=negated, dir_only=dir_only
    )


class IgnoreMatcher:
    """Ordered collection of pattern sets, one per directory that has rules."""

    def __init__(self, defaults: Iterable[str] = ()) -> None:
        self._sets: dict[str, list[GlobPattern]] = {}
        self.add_lines(defaults, base="")

    def add_lines(self, lines: Iterable[str], base: str = "") -> None:
        base = base.strip("/")
        bucket = self._sets.setdefault(base, [])
        for line in lines:
            compiled = compile_pattern(line)
            if compiled is not None:
                bucket.append(compiled)

    def add_file(self, path: Path, base: str = "") -> bool:
        """Load rules from ``path``. Returns ``True`` if the file was read."""
        try:
            # utf-8-sig also strips a BOM that Windows editors like to add.
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return False
        self.add_lines(text.splitlines(), base=base)
        return True

    def match(self, rel_path: str, is_dir: bool = False) -> bool:
        rel_path = rel_path.replace("\\", "/").strip("/")
        ignored = False
        for base in sorted(self._sets, key=lambda b: b.count("/") if b else -1):
            if base:
                if not rel_path.startswith(base + "/"):
                    continue
                sub_path = rel_path[len(base) + 1 :]
            else:
                sub_path = rel_path
            for rule in self._sets[base]:
                if rule.matches(sub_path, is_dir):
                    ignored = not rule.negated
        return ignored


def compile_globs(patterns: Iterable[str]) -> list[GlobPattern]:
    compiled: list[GlobPattern] = []
    for pattern in patterns:
        item = compile_pattern(pattern)
        if item is not None:
            compiled.append(item)
    return compiled


def any_match(patterns: list[GlobPattern], rel_path: str, is_dir: bool = False) -> bool:
    return any(p.matches(rel_path, is_dir) for p in patterns)
