"""Build a single handoff document for continuing work in a fresh chat.

The document has two layers:

* ``STATE.md`` — what the agent was doing, what is done, what comes next;
* the packed project context — a full tree plus only the files needed next.

One file is enough to start a new session without losing the thread, which is
why this lives next to the regular packer instead of being a separate tool.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .collector import collect
from .render import Meta, build_tree, render
from .tokens import human_bytes

HANDOFF_DIR = ".opencode/handoff"
DEFAULT_STATE = f"{HANDOFF_DIR}/STATE.md"
DEFAULT_FILES = f"{HANDOFF_DIR}/files.txt"
DEFAULT_OUTPUT = f"{HANDOFF_DIR}/pack.md"


def _read_list(path: Path) -> list[str]:
    """Read relative paths, one per line. Blank lines and ``#`` comments are ignored."""
    text = path.read_text(encoding="utf-8-sig")
    result: list[str] = []
    for line in text.splitlines():
        value = line.strip().replace("\\", "/")
        if not value or value.startswith("#"):
            continue
        result.append(value)
    return result


def build_handoff(
    root: Path,
    *,
    state: Path | None = None,
    files_from: Path | None = None,
    fmt: str = "markdown",
    max_file_size: int = 512 * 1024,
    show_tree: bool = True,
    show_integrity: bool = True,
) -> str:
    """Return the handoff document: state first, packed context after it."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")

    skip_paths = [path for path in (state, files_from) if path is not None]
    collection = collect(root, max_file_size=max_file_size, skip_paths=skip_paths)

    entries = collection.entries
    if files_from is not None:
        wanted = _read_list(files_from)
        if not wanted:
            raise ValueError(f"file list is empty: {files_from}")
        by_rel = {entry.rel: entry for entry in entries}
        missing = [rel for rel in wanted if rel not in by_rel]
        for rel in missing:
            print(f"warning: not collected, skipped: {rel}", file=sys.stderr)
        entries = [by_rel[rel] for rel in wanted if rel in by_rel]
        if not entries:
            raise ValueError("none of the listed files could be collected")

    # The tree always describes the whole project: it is the map the next
    # session uses to ask for what is missing.
    tree = build_tree([entry.rel for entry in collection.entries], root.name) if show_tree else ""
    meta = Meta(
        root_name=root.name,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        files=len(entries),
        tokens=sum(entry.tokens for entry in entries),
        size=sum(entry.size for entry in entries),
    )
    body = render(entries, tree, meta, fmt, show_tree, True, show_integrity)

    state_text = ""
    if state is not None and state.is_file():
        state_text = state.read_text(encoding="utf-8-sig").strip()

    if not state_text:
        return body

    header = (
        f"# Handoff state: {root.name}\n\n"
        f"_Generated {meta.generated}. Read this section first; "
        f"the full project context follows it._\n\n"
    )
    return f"{header}{state_text}\n\n---\n\n{body}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alltoone handoff",
        description="Build one file with the session state and the files needed next.",
    )
    parser.add_argument("path", nargs="?", default=".", help="project directory (default: .)")
    parser.add_argument("--state", metavar="FILE", help=f"state file (default: {DEFAULT_STATE})")
    parser.add_argument(
        "--files-from",
        metavar="FILE",
        help=f"paths to pack, one per line (default: {DEFAULT_FILES})",
    )
    parser.add_argument("-o", "--output", metavar="FILE", help=f"output file (default: {DEFAULT_OUTPUT})")
    parser.add_argument("-f", "--format", choices=("markdown", "xml", "plain"), default="markdown")
    parser.add_argument(
        "--max-file-size",
        type=int,
        default=512,
        metavar="KB",
        help="skip files larger than KB kilobytes (default: 512)",
    )
    parser.add_argument("--no-tree", action="store_true", help="omit the project tree")
    parser.add_argument("--no-integrity", action="store_true", help="omit the SHA-256 manifest")
    parser.add_argument("-q", "--quiet", action="store_true", help="do not print the summary")
    return parser


def handoff_main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    root = Path(args.path).expanduser()
    if not root.exists():
        print(f"error: path not found: {root}", file=sys.stderr)
        return 2
    root = root.resolve()

    state = Path(args.state).expanduser() if args.state else root / DEFAULT_STATE
    files_from = Path(args.files_from).expanduser() if args.files_from else root / DEFAULT_FILES
    output = Path(args.output).expanduser() if args.output else root / DEFAULT_OUTPUT
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()

    if args.files_from and not files_from.is_file():
        print(f"error: file list not found: {files_from}", file=sys.stderr)
        return 2

    try:
        document = build_handoff(
            root,
            state=state,
            files_from=files_from if files_from.is_file() else None,
            fmt=args.format,
            max_file_size=args.max_file_size * 1024,
            show_tree=not args.no_tree,
            show_integrity=not args.no_integrity,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8", newline="\n")

    if not args.quiet:
        print("alltoone handoff", file=sys.stderr)
        print(f"  state    {state if state.is_file() else '(none)'}", file=sys.stderr)
        print(f"  files    {files_from if files_from.is_file() else '(all collected)'}", file=sys.stderr)
        print(
            f"  output   {output} ({human_bytes(len(document.encode('utf-8')))})",
            file=sys.stderr,
        )
    return 0
