"""Turn collected entries into a single document an LLM can read."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable, Sequence
from xml.sax.saxutils import escape as xml_escape, quoteattr

from . import __version__
from .collector import FileEntry
from .tokens import human_bytes, human_tokens

LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".json": "json",
    ".jsonc": "jsonc",
    ".md": "markdown",
    ".mdx": "mdx",
    ".rst": "rst",
    ".txt": "text",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".vue": "vue",
    ".svelte": "svelte",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".env": "dotenv",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".bat": "bat",
    ".cmd": "bat",
    ".sql": "sql",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".lua": "lua",
    ".dart": "dart",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".scala": "scala",
    ".r": "r",
    ".m": "objectivec",
    ".pl": "perl",
    ".proto": "protobuf",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".dockerfile": "dockerfile",
    ".makefile": "makefile",
    ".cmake": "cmake",
    ".tf": "hcl",
    ".xml": "xml",
    ".svg": "xml",
}


def language_for(rel_path: str) -> str:
    """Pick a Markdown code fence language for a path."""
    name = PurePosixPath(rel_path).name.lower()
    if name in {"dockerfile", "makefile", "jenkinsfile"}:
        return name
    return LANGUAGES.get(PurePosixPath(rel_path).suffix.lower(), "")


def build_tree(paths: Iterable[str], root_name: str) -> str:
    """Render an ASCII tree for the given relative file paths."""
    tree: dict[str, dict] = {}
    for path in sorted(paths):
        node = tree
        for part in path.split("/"):
            node = node.setdefault(part, {})

    lines = [f"{root_name}/"]

    def render(node: dict[str, dict], prefix: str) -> None:
        items = sorted(node.items(), key=lambda kv: (not kv[1], kv[0].lower()))
        for index, (name, children) in enumerate(items):
            last = index == len(items) - 1
            connector = "└── " if last else "├── "
            suffix = "/" if children else ""
            lines.append(f"{prefix}{connector}{name}{suffix}")
            if children:
                render(children, prefix + ("    " if last else "│   "))

    render(tree, "")
    return "\n".join(lines)


def _code_fence(text: str, language: str) -> str:
    """Wrap text in a fence that cannot be broken by backticks inside it."""
    longest = max((len(match) for match in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    body = text.rstrip("\n")
    return f"{ticks}{language}\n{body}\n{ticks}"


def _cdata(text: str) -> str:
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


@dataclass
class Meta:
    """Everything the header of the output document needs."""

    root_name: str
    generated: str
    files: int
    tokens: int
    size: int = 0
    part: int | None = None
    parts: int | None = None

    @property
    def part_label(self) -> str:
        if self.part and self.parts and self.parts > 1:
            return f" · part {self.part} of {self.parts}"
        return ""


def _markdown(
    entries: Sequence[FileEntry],
    tree: str,
    meta: Meta,
    show_tree: bool = True,
    show_files: bool = True,
) -> str:
    out: list[str] = [
        f"# Project context: {meta.root_name}",
        "",
        f"Packed with alltoone {__version__} on {meta.generated}{meta.part_label}.",
        f"{meta.files} files · ~{human_tokens(meta.tokens)} tokens · {human_bytes(meta.size)}"
        + ("" if show_files else " · tree only"),
        "",
    ]
    if show_tree:
        out.extend(["## Project structure", "", "```text", tree, "```", ""])
    if show_files:
        out.extend(["## Files", ""])
        for entry in entries:
            out.append(f"### `{entry.rel}`")
            out.append("")
            out.append(_code_fence(entry.text, language_for(entry.rel)))
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def _xml(
    entries: Sequence[FileEntry],
    tree: str,
    meta: Meta,
    show_tree: bool = True,
    show_files: bool = True,
) -> str:
    out: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<context name={quoteattr(meta.root_name)} generator="alltoone/{__version__}" '
            f'generated={quoteattr(meta.generated)} files="{meta.files}" '
            f'tokens="{meta.tokens}"'
            + (f' part="{meta.part}" parts="{meta.parts}"' if meta.parts and meta.parts > 1 else "")
            + ">"
        ),
    ]
    if show_tree:
        out.append(f"  <structure>{_cdata(tree)}</structure>")
    if show_files:
        out.append("  <files>")
        for entry in entries:
            out.append(
                f'    <file path={quoteattr(entry.rel)} tokens="{entry.tokens}">'
                f"{_cdata(entry.text)}</file>"
            )
        out.append("  </files>")
    out.append("</context>")
    return "\n".join(out) + "\n"


def _plain(
    entries: Sequence[FileEntry],
    tree: str,
    meta: Meta,
    show_tree: bool = True,
    show_files: bool = True,
) -> str:
    rule = "=" * 78
    thin = "-" * 78
    out: list[str] = [
        rule,
        f"PROJECT: {meta.root_name}{meta.part_label}",
        f"Packed with alltoone {__version__} on {meta.generated}",
        f"{meta.files} files | ~{human_tokens(meta.tokens)} tokens | {human_bytes(meta.size)}",
        rule,
        "",
    ]
    if show_tree:
        out.extend(["STRUCTURE:", tree, ""])
    if show_files:
        for entry in entries:
            out.extend([rule, f"FILE: {entry.rel}", thin, entry.text.rstrip("\n"), ""])
    return "\n".join(out) + "\n"


RENDERERS = {"markdown": _markdown, "xml": _xml, "plain": _plain}


def render(
    entries: Sequence[FileEntry],
    tree: str,
    meta: Meta,
    fmt: str = "markdown",
    show_tree: bool = True,
    show_files: bool = True,
) -> str:
    try:
        renderer = RENDERERS[fmt]
    except KeyError:
        raise ValueError(
            f"unknown format {fmt!r}, expected one of: {', '.join(RENDERERS)}"
        ) from None
    return renderer(entries, tree, meta, show_tree, show_files)


def split_entries(
    entries: Sequence[FileEntry], budget_tokens: int, overhead_tokens: int = 0
) -> list[list[FileEntry]]:
    """Greedily pack entries into chunks that fit a token budget."""
    limit = max(budget_tokens - overhead_tokens, 1)
    chunks: list[list[FileEntry]] = []
    current: list[FileEntry] = []
    used = 0
    for entry in entries:
        cost = entry.tokens + 8  # heading, fence and blank lines
        if current and used + cost > limit:
            chunks.append(current)
            current, used = [], 0
        current.append(entry)
        used += cost
    if current:
        chunks.append(current)
    return chunks
