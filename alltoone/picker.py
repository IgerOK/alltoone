"""Interactive selection of files to pack, driven by numbers in the console."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Callable, Sequence, TextIO

from .collector import FileEntry
from .tokens import human_tokens

PROMPT = "Numbers to toggle (3,7-9) | a=all n=none d=done q=quit: "


@dataclass
class _Node:
    name: str
    rel: str
    is_dir: bool
    depth: int
    children: list["_Node"] = field(default_factory=list)
    number: int = 0
    files: list[str] = field(default_factory=list)


def _build_tree(entries: Sequence[FileEntry]) -> _Node:
    root = _Node(name="", rel="", is_dir=True, depth=-1)
    nodes: dict[str, _Node] = {"": root}
    for entry in entries:
        parent = root
        path = ""
        parts = entry.rel.split("/")
        for index, part in enumerate(parts):
            path = f"{path}/{part}" if path else part
            node = nodes.get(path)
            if node is None:
                node = _Node(
                    name=part,
                    rel=path,
                    is_dir=index < len(parts) - 1,
                    depth=index,
                )
                nodes[path] = node
                parent.children.append(node)
            parent = node

    def sort_children(node: _Node) -> None:
        node.children.sort(key=lambda child: (not child.is_dir, child.name.lower()))
        for child in node.children:
            sort_children(child)

    sort_children(root)
    return root


def _collect_files(node: _Node) -> list[str]:
    if not node.is_dir:
        node.files = [node.rel]
        return node.files
    files: list[str] = []
    for child in node.children:
        files.extend(_collect_files(child))
    node.files = files
    return files


def _number(node: _Node, counter: list[int]) -> None:
    for child in node.children:
        counter[0] += 1
        child.number = counter[0]
        _number(child, counter)


def _index(node: _Node, table: dict[int, _Node]) -> None:
    for child in node.children:
        table[child.number] = child
        _index(child, table)


def _state(node: _Node, selected: set[str]) -> str:
    if not node.is_dir:
        return "all" if node.rel in selected else "none"
    if not node.files:
        return "none"
    chosen = sum(1 for path in node.files if path in selected)
    if chosen == 0:
        return "none"
    if chosen == len(node.files):
        return "all"
    return "partial"


def _toggle(node: _Node, selected: set[str]) -> None:
    if _state(node, selected) == "all":
        for path in node.files:
            selected.discard(path)
    else:
        selected.update(node.files)


def _parse_numbers(text: str) -> list[int]:
    """Parse ``3,7-9`` into a de-duplicated list of numbers."""
    numbers: list[int] = []
    for token in text.replace(",", " ").split():
        if "-" in token:
            start, _, end = token.partition("-")
            if start.isdigit() and end.isdigit():
                low, high = int(start), int(end)
                if low > high:
                    low, high = high, low
                numbers.extend(range(low, high + 1))
        elif token.isdigit():
            numbers.append(int(token))
    return list(dict.fromkeys(numbers))


def _draw(
    root: _Node,
    selected: set[str],
    entries: Sequence[FileEntry],
    stream: TextIO,
    root_name: str,
    width: int,
) -> None:
    if getattr(stream, "isatty", lambda: False)():
        stream.write("\033[2J\033[H")
    stream.write(f"{root_name}/  ({len(entries)} files)\n\n")
    for child in root.children:
        state = _state(child, selected)
        marker = {"all": "[x]", "partial": "[-]", "none": "[ ]"}[state]
        indent = "  " * child.depth
        suffix = "/" if child.is_dir else ""
        stream.write(f"{child.number:>{width}}  {marker}  {indent}{child.name}{suffix}\n")

    chosen = sum(1 for entry in entries if entry.rel in selected)
    tokens = sum(entry.tokens for entry in entries if entry.rel in selected)
    stream.write(f"\nSelected: {chosen} of {len(entries)} files, ~{human_tokens(tokens)} tokens\n\n")
    stream.flush()


def select_files(
    entries: Sequence[FileEntry],
    root_name: str,
    *,
    input_func: Callable[[str], str] | None = None,
    stream: TextIO | None = None,
) -> list[FileEntry] | None:
    """Show the tree and let the user toggle files. Returns ``None`` on quit."""
    if not entries:
        raise ValueError("there is nothing to select")

    stream = stream or sys.stderr
    ask = input_func or input

    root = _build_tree(entries)
    _collect_files(root)
    _number(root, [0])
    table: dict[int, _Node] = {}
    _index(root, table)
    width = len(str(max(table)))

    selected = {entry.rel for entry in entries}

    while True:
        _draw(root, selected, entries, stream, root_name, width)
        stream.write(PROMPT)
        stream.flush()
        try:
            line = ask("")
        except EOFError:
            return None
        command = line.strip().lower()

        if command in ("d", "done"):
            break
        if command in ("q", "quit"):
            return None
        if command in ("a", "all"):
            selected = {entry.rel for entry in entries}
            continue
        if command in ("n", "none"):
            selected.clear()
            continue
        if not command:
            continue

        numbers = _parse_numbers(command)
        if not numbers:
            stream.write(f"  ? unrecognised input: {line.strip()}\n")
            continue
        for number in numbers:
            node = table.get(number)
            if node is None:
                stream.write(f"  ? no entry number {number}\n")
                continue
            _toggle(node, selected)

    chosen = [entry for entry in entries if entry.rel in selected]
    if not chosen:
        stream.write("nothing selected\n")
        return None
    return chosen
