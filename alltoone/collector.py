"""Walk a project and turn its text files into entries ready for rendering."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .ignore import (
    DEFAULT_IGNORES,
    GlobPattern,
    IgnoreMatcher,
    any_match,
    compile_globs,
)
from .tokens import estimate_tokens

# Extensions that are never worth reading as text, even if they happen to decode.
BINARY_EXTENSIONS: frozenset[str] = frozenset(
    """
    .png .jpg .jpeg .gif .bmp .ico .icns .webp .tif .tiff .psd .ai .eps .raw
    .mp3 .mp4 .wav .ogg .flac .aac .avi .mkv .mov .webm .m4a .wmv
    .zip .tar .gz .tgz .bz2 .xz .7z .rar .jar .war .whl .egg .iso
    .pdf .doc .docx .xls .xlsx .ppt .pptx .odt .ods .odp .rtf
    .exe .dll .so .dylib .bin .o .obj .a .lib .class .pyc .pyo .wasm .node
    .ttf .otf .woff .woff2 .eot
    .sqlite .sqlite3 .db .mdb .dat .pdb
    """.split()
)

@dataclass
class FileEntry:
    """One text file that will be packed."""

    rel: str
    text: str
    size: int
    encoding: str
    tokens: int


@dataclass
class Skip:
    """A path that was left out, with the reason why."""

    rel: str
    reason: str


@dataclass
class Collection:
    root: Path
    entries: list[FileEntry] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(entry.tokens for entry in self.entries)

    @property
    def total_size(self) -> int:
        return sum(entry.size for entry in self.entries)


def decode_text(data: bytes) -> tuple[str, str]:
    """Decode bytes into text, returning ``(text, encoding_name)``.

    UTF-8 is tried first, then common Windows/Cyrillic encodings. ``latin-1`` is
    the final fallback because it can decode any byte sequence.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace"), "utf-16"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    for encoding in ("cp1251", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


def looks_binary(data: bytes) -> bool:
    """Heuristic binary check: a NUL byte in the head of the file."""
    return b"\x00" in data[:8192]


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _sorted_entries(directory: Path) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(directory) as handle:
            items = list(handle)
    except OSError:
        return []
    # Directories first, then files, both alphabetically and case-insensitively.
    return sorted(items, key=lambda item: (not item.is_dir(follow_symlinks=False), item.name.lower()))


def _load_ignores(matcher: IgnoreMatcher, directory: Path, rel: str, use_gitignore: bool) -> None:
    """Read ignore files of one directory. ``.alltooneignore`` always applies."""
    names = ((".gitignore",) if use_gitignore else ()) + (".alltooneignore",)
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            matcher.add_file(candidate, base=rel)


def collect(
    root: Path,
    *,
    use_gitignore: bool = True,
    use_default_ignores: bool = True,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    extra_ignore_files: Sequence[Path] = (),
    max_file_size: int = 512 * 1024,
    skip_paths: Iterable[Path] = (),
) -> Collection:
    """Collect every text file under ``root`` that passes the filters."""
    collection = Collection(root=root)
    matcher = IgnoreMatcher(DEFAULT_IGNORES if use_default_ignores else ())
    include_globs = compile_globs(include)
    exclude_globs = compile_globs(exclude)
    resolved_skips = {path.resolve() for path in skip_paths}

    if root.is_file():
        _add_file(root, root.name, matcher, include_globs, exclude_globs,
                  max_file_size, resolved_skips, collection)
        return collection

    _load_ignores(matcher, root, "", use_gitignore)
    for extra in extra_ignore_files:
        matcher.add_file(extra, base="")

    def walk(directory: Path, rel_dir: str) -> None:
        for item in _sorted_entries(directory):
            rel = f"{rel_dir}/{item.name}" if rel_dir else item.name
            try:
                is_dir = item.is_dir(follow_symlinks=False)
            except OSError:
                collection.skipped.append(Skip(rel, "unreadable"))
                continue

            if is_dir:
                # An explicit --exclude wins over ignore files, so the reason is clear.
                if exclude_globs and any_match(exclude_globs, rel, is_dir=True):
                    collection.skipped.append(Skip(rel, "excluded"))
                    continue
                if matcher.match(rel, is_dir=True):
                    collection.skipped.append(Skip(rel, "ignored"))
                    continue
                _load_ignores(matcher, Path(item.path), rel, use_gitignore)
                walk(Path(item.path), rel)
                continue

            _add_file(
                Path(item.path), rel, matcher, include_globs, exclude_globs,
                max_file_size, resolved_skips, collection,
            )

    walk(root, "")
    return collection


def _add_file(
    path: Path,
    rel: str,
    matcher: IgnoreMatcher,
    include_globs: list[GlobPattern],
    exclude_globs: list[GlobPattern],
    max_file_size: int,
    resolved_skips: set[Path],
    collection: Collection,
) -> None:
    if exclude_globs and any_match(exclude_globs, rel):
        collection.skipped.append(Skip(rel, "excluded"))
        return
    if matcher.match(rel, is_dir=False):
        collection.skipped.append(Skip(rel, "ignored"))
        return
    if include_globs and not any_match(include_globs, rel):
        collection.skipped.append(Skip(rel, "not included"))
        return
    if path.suffix.lower() in BINARY_EXTENSIONS:
        collection.skipped.append(Skip(rel, "binary"))
        return
    try:
        if path.resolve() in resolved_skips:
            collection.skipped.append(Skip(rel, "internal file"))
            return
        size = path.stat().st_size
    except OSError:
        collection.skipped.append(Skip(rel, "unreadable"))
        return
    if size > max_file_size:
        collection.skipped.append(Skip(rel, "too large"))
        return
    try:
        data = path.read_bytes()
    except OSError:
        collection.skipped.append(Skip(rel, "unreadable"))
        return
    if looks_binary(data):
        collection.skipped.append(Skip(rel, "binary"))
        return

    text, encoding = decode_text(data)
    text = _normalize_newlines(text)
    collection.entries.append(
        FileEntry(
            rel=rel.replace("\\", "/"),
            text=text,
            size=size,
            encoding=encoding,
            tokens=estimate_tokens(text),
        )
    )
