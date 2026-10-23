"""Command line interface for alltoone."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import __version__
from .collector import collect
from .render import Meta, build_tree, render, split_entries
from .tokens import estimate_tokens, human_bytes, human_tokens

CONFIG_NAME = "alltoone.json"

DEFAULTS: dict[str, object] = {
    "format": "markdown",
    "output": None,
    "include": [],
    "exclude": [],
    "use_gitignore": True,
    "use_default_ignores": True,
    "max_file_size_kb": 512,
    "max_tokens": None,
    "tree": True,
    "files": True,
    "clipboard": False,
}

EPILOG = """\
examples:
  alltoone                          pack the current directory to stdout
  alltoone ../my-app -o context.md  pack another project into a file
  alltoone -f xml --max-tokens 100000 -o ctx.xml
  alltoone --include "src/**" --include "*.md"
  alltoone --clipboard              copy the result to the clipboard
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alltoone",
        description="Pack every text file of a project into one file for an AI agent.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "path", nargs="?", default=".", help="project directory or a single file (default: .)"
    )
    parser.add_argument("-o", "--output", metavar="FILE", help="write to FILE instead of stdout ('-' for stdout)")
    parser.add_argument(
        "-f", "--format", choices=("markdown", "xml", "plain"), help="output format (default: markdown)"
    )
    parser.add_argument("--include", action="append", metavar="GLOB", help="only pack matching files (repeatable)")
    parser.add_argument("--exclude", action="append", metavar="GLOB", help="skip matching files (repeatable)")
    parser.add_argument(
        "--no-gitignore",
        action="store_const",
        const=True,
        default=None,
        help="do not read .gitignore / .alltooneignore files",
    )
    parser.add_argument(
        "--no-default-ignores",
        action="store_const",
        const=True,
        default=None,
        help="do not apply the built-in ignore list",
    )
    parser.add_argument(
        "--max-file-size", type=int, metavar="KB", help="skip files larger than KB kilobytes (default: 512)"
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        metavar="N",
        help="split the result into parts of at most N estimated tokens",
    )
    parser.add_argument("--tree-only", action="store_const", const=True, default=None, help="print the file tree only")
    parser.add_argument("--no-tree", action="store_const", const=True, default=None, help="omit the file tree")
    parser.add_argument("--config", metavar="FILE", help=f"use FILE instead of {CONFIG_NAME}")
    parser.add_argument(
        "--clipboard",
        action="store_const",
        const=True,
        default=None,
        help="copy the result to the system clipboard",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="do not print the summary")
    parser.add_argument("--version", action="version", version=f"alltoone {__version__}")
    return parser


def load_config(path: Path | None, root: Path) -> dict[str, object]:
    """Read the JSON config, if any. Explicit path must exist."""
    if path is None:
        candidate = root / CONFIG_NAME
        if not candidate.is_file():
            return {}
        path = candidate
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"error: config file not found: {path}") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"error: cannot read config {path}: {exc}") from None
    if not isinstance(raw, dict):
        raise SystemExit(f"error: config {path} must contain a JSON object")
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise SystemExit(f"error: unknown config keys in {path}: {', '.join(sorted(unknown))}")
    return raw


def resolve_settings(args: argparse.Namespace, config: dict[str, object]) -> dict[str, object]:
    """Merge defaults, config file and command line flags (CLI wins)."""
    settings = dict(DEFAULTS)
    settings.update(config)
    if args.output is not None:
        settings["output"] = args.output
    if args.format is not None:
        settings["format"] = args.format
    if args.include is not None:
        settings["include"] = list(args.include)
    if args.exclude is not None:
        settings["exclude"] = list(args.exclude)
    if args.no_gitignore:
        settings["use_gitignore"] = False
    if args.no_default_ignores:
        settings["use_default_ignores"] = False
    if args.max_file_size is not None:
        settings["max_file_size_kb"] = args.max_file_size
    if args.max_tokens is not None:
        settings["max_tokens"] = args.max_tokens
    if args.tree_only:
        settings["tree"] = True
        settings["files"] = False
    if args.no_tree:
        settings["tree"] = False
    if args.clipboard:
        settings["clipboard"] = True
    return settings


def _part_path(output: Path, index: int) -> Path:
    return output.with_name(f"{output.stem}.part{index}{output.suffix}")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _copy_to_clipboard(text: str) -> None:
    """Put text on the clipboard without breaking non-ASCII characters."""
    if os.name == "nt":
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as handle:
            handle.write(text)
            temp_path = handle.name
        try:
            script = (
                "Set-Clipboard -Value ([IO.File]::ReadAllText("
                f"'{temp_path}', [Text.Encoding]::UTF8))"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                check=True,
                capture_output=True,
            )
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        return

    commands = [["pbcopy"]] if sys.platform == "darwin" else [["wl-copy"], ["xclip", "-selection", "clipboard"]]
    for command in commands:
        try:
            subprocess.run(command, input=text.encode("utf-8"), check=True, capture_output=True)
            return
        except (OSError, subprocess.CalledProcessError):
            continue
    raise SystemExit("error: no clipboard tool found")


def _print_summary(collection, outputs: list[Path]) -> None:
    reasons = Counter(skip.reason for skip in collection.skipped)
    print(f"alltoone {__version__}", file=sys.stderr)
    print(f"  root     {collection.root}", file=sys.stderr)
    print(
        f"  included {len(collection.entries)} files, "
        f"{human_bytes(collection.total_size)}, ~{human_tokens(collection.total_tokens)} tokens",
        file=sys.stderr,
    )
    if reasons:
        detail = ", ".join(f"{reason} {count}" for reason, count in sorted(reasons.items()))
        print(f"  skipped  {sum(reasons.values())} ({detail})", file=sys.stderr)
    for path in outputs:
        print(f"  output   {path}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass

    parser = build_parser()
    args = parser.parse_args(argv)

    root = Path(args.path).expanduser()
    if not root.exists():
        print(f"error: path not found: {root}", file=sys.stderr)
        return 2
    root = root.resolve()

    config = load_config(Path(args.config) if args.config else None, root if root.is_dir() else root.parent)
    settings = resolve_settings(args, config)

    output_value = settings["output"]
    output_path: Path | None = None
    if output_value and output_value != "-":
        output_path = Path(str(output_value)).expanduser()
        if not output_path.is_absolute():
            output_path = (Path.cwd() / output_path).resolve()

    skip_paths: list[Path] = []
    if output_path is not None:
        skip_paths.append(output_path)
    if args.config:
        skip_paths.append(Path(args.config).expanduser().resolve())
    else:
        skip_paths.append((root / CONFIG_NAME) if root.is_dir() else (root.parent / CONFIG_NAME))

    # Never pack previous output of this tool, including its split parts.
    exclude_patterns = list(settings["exclude"])  # type: ignore[arg-type]
    if output_path is not None:
        exclude_patterns.append(f"{output_path.stem}.part*{output_path.suffix}")

    collection = collect(
        root,
        use_gitignore=bool(settings["use_gitignore"]),
        use_default_ignores=bool(settings["use_default_ignores"]),
        include=list(settings["include"]),  # type: ignore[arg-type]
        exclude=exclude_patterns,
        max_file_size=int(settings["max_file_size_kb"]) * 1024,  # type: ignore[arg-type]
        skip_paths=skip_paths,
    )

    root_name = root.name if root.is_dir() else root.parent.name
    all_entries = collection.entries
    tree = build_tree([entry.rel for entry in all_entries], root_name) if settings["tree"] else ""
    show_files = bool(settings["files"])
    show_tree = bool(settings["tree"])
    entries = all_entries if show_files else []

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    fmt = str(settings["format"])

    def make_meta(chunk, part: int | None = None, parts: int | None = None) -> Meta:
        source = chunk if show_files else all_entries
        return Meta(
            root_name=root_name,
            generated=generated,
            files=len(source),
            tokens=sum(entry.tokens for entry in source),
            size=sum(entry.size for entry in source),
            part=part,
            parts=parts,
        )

    max_tokens = settings["max_tokens"]
    chunks: list[list] = [entries]
    if max_tokens and entries:
        header_tokens = estimate_tokens(render([], tree, make_meta([]), fmt, show_tree, show_files))
        chunks = split_entries(entries, int(max_tokens), header_tokens)  # type: ignore[arg-type]

    documents: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        meta = make_meta(chunk, index if len(chunks) > 1 else None, len(chunks) if len(chunks) > 1 else None)
        documents.append(render(chunk, tree, meta, fmt, show_tree, show_files))

    written: list[Path] = []
    if len(documents) == 1:
        if output_path is None:
            sys.stdout.write(documents[0])
        else:
            _write(output_path, documents[0])
            written.append(output_path)
    else:
        base = output_path or (Path.cwd() / "alltoone-context.md")
        for index, document in enumerate(documents, start=1):
            path = _part_path(base, index)
            _write(path, document)
            written.append(path)

    if settings["clipboard"]:
        _copy_to_clipboard("\n\n".join(documents))

    if not args.quiet:
        _print_summary(collection, written)
        if len(documents) > 1:
            print(
                f"  split    {len(documents)} parts of <= {human_tokens(int(max_tokens))} tokens",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
