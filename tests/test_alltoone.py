from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from alltoone.cli import main
from alltoone.collector import FileEntry, collect, decode_text, looks_binary
from alltoone.ignore import IgnoreMatcher, compile_globs, compile_pattern
from alltoone.render import Meta, build_tree, language_for, render, split_entries
from alltoone.skills import check_skills
from alltoone.tokens import estimate_tokens, human_bytes, human_tokens
from alltoone.unpack import UnpackError, detect_format, parse, safe_target, unpack


class PatternTests(unittest.TestCase):
    def test_basename_pattern_matches_at_any_depth(self):
        pattern = compile_pattern("*.py")
        assert pattern is not None
        self.assertTrue(pattern.matches("main.py"))
        self.assertTrue(pattern.matches("src/deep/main.py"))
        self.assertFalse(pattern.matches("src/main.txt"))

    def test_anchored_pattern(self):
        pattern = compile_pattern("/build")
        assert pattern is not None
        self.assertTrue(pattern.matches("build"))
        self.assertFalse(pattern.matches("src/build"))

    def test_directory_only_pattern(self):
        pattern = compile_pattern("build/")
        assert pattern is not None
        self.assertTrue(pattern.matches("build", is_dir=True))
        self.assertFalse(pattern.matches("build", is_dir=False))
        self.assertTrue(pattern.matches("src/build", is_dir=True))

    def test_double_star(self):
        pattern = compile_pattern("docs/**/*.md")
        assert pattern is not None
        self.assertTrue(pattern.matches("docs/a.md"))
        self.assertTrue(pattern.matches("docs/x/y/a.md"))
        self.assertFalse(pattern.matches("docs/a.txt"))

    def test_character_class_and_question_mark(self):
        pattern = compile_pattern("file[0-9]?.txt")
        assert pattern is not None
        self.assertTrue(pattern.matches("file1a.txt"))
        self.assertFalse(pattern.matches("filexa.txt"))

    def test_comment_and_blank_lines_are_skipped(self):
        self.assertIsNone(compile_pattern("# comment"))
        self.assertIsNone(compile_pattern("   "))

    def test_negation(self):
        matcher = IgnoreMatcher(["*.log", "!keep.log"])
        self.assertTrue(matcher.match("debug.log"))
        self.assertFalse(matcher.match("keep.log"))

    def test_nested_rules_apply_only_in_subtree(self):
        matcher = IgnoreMatcher(["*.tmp"])
        matcher.add_lines(["*.bak"], base="src")
        self.assertTrue(matcher.match("a.tmp"))
        self.assertTrue(matcher.match("src/a.bak"))
        self.assertFalse(matcher.match("a.bak"))

    def test_exclude_globs(self):
        globs = compile_globs(["*.md", "tests/**"])
        self.assertTrue(any(p.matches("README.md") for p in globs))
        self.assertTrue(any(p.matches("tests/test_x.py") for p in globs))
        self.assertFalse(any(p.matches("main.py") for p in globs))


class DecodeTests(unittest.TestCase):
    def test_utf8(self):
        text, encoding = decode_text("привет".encode("utf-8"))
        self.assertEqual((text, encoding), ("привет", "utf-8"))

    def test_utf8_bom(self):
        text, encoding = decode_text(b"\xef\xbb\xbfhello")
        self.assertEqual((text, encoding), ("hello", "utf-8-sig"))

    def test_cp1251(self):
        text, encoding = decode_text("привет".encode("cp1251"))
        self.assertEqual(text, "привет")
        self.assertEqual(encoding, "cp1251")

    def test_binary_detection(self):
        self.assertTrue(looks_binary(b"\x89PNG\r\n\x1a\n\x00\x00"))
        self.assertFalse(looks_binary(b"print('hello')\n"))


class RenderTests(unittest.TestCase):
    def test_tree_orders_directories_first(self):
        tree = build_tree(["b.txt", "src/a.py", "src/lib/c.py"], "demo")
        self.assertEqual(
            tree.splitlines(),
            [
                "demo/",
                "├── src/",
                "│   ├── lib/",
                "│   │   └── c.py",
                "│   └── a.py",
                "└── b.txt",
            ],
        )

    def test_language_detection(self):
        self.assertEqual(language_for("src/main.py"), "python")
        self.assertEqual(language_for("Dockerfile"), "dockerfile")
        self.assertEqual(language_for("data.unknown"), "")

    def test_fence_grows_when_content_has_backticks(self):
        entry = FileEntry("a.md", "```python\nprint(1)\n```", 10, "utf-8", 5)
        meta = Meta("demo", "now", 1, 5, 10)
        document = render([entry], "demo/", meta)
        self.assertIn("````markdown", document)

    def test_xml_is_valid(self):
        entry = FileEntry("a<b>.txt", "x ]]> y", 7, "utf-8", 3)
        meta = Meta("demo", "now", 1, 3, 7)
        document = render([entry], "demo/", meta, "xml")
        root = ET.fromstring(document)
        self.assertEqual(root.get("files"), "1")
        self.assertEqual(root.find("files/file").text, "x ]]> y")

    def test_no_tree_option(self):
        meta = Meta("demo", "now", 0, 0)
        document = render([], "demo/", meta, show_tree=False)
        self.assertNotIn("Project structure", document)

    def test_split_respects_budget(self):
        entries = [FileEntry(f"f{i}.txt", "x" * 400, 400, "utf-8", 100) for i in range(5)]
        chunks = split_entries(entries, 250, overhead_tokens=0)
        self.assertEqual([len(chunk) for chunk in chunks], [2, 2, 1])
        for chunk in chunks:
            self.assertLessEqual(sum(entry.tokens + 8 for entry in chunk), 250)

    def test_single_oversized_entry_gets_its_own_chunk(self):
        entries = [
            FileEntry("huge.txt", "x", 1, "utf-8", 10_000),
            FileEntry("small.txt", "x", 1, "utf-8", 10),
        ]
        chunks = split_entries(entries, 500)
        self.assertEqual([[entry.rel for entry in chunk] for chunk in chunks], [["huge.txt"], ["small.txt"]])

    def test_token_estimate(self):
        self.assertGreater(estimate_tokens("hello world"), 0)
        self.assertGreater(estimate_tokens("привет мир"), estimate_tokens("hello world"))
        self.assertEqual(human_tokens(8_400), "8.4k")
        self.assertEqual(human_bytes(2048), "2.0 KB")


class CollectTests(unittest.TestCase):
    def test_collect_skips_ignored_binary_and_large_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
            (root / "secret.txt").write_text("nope\n", encoding="utf-8")
            (root / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
            (root / "image.png").write_bytes(b"\x89PNG\x00\x00")
            (root / "big.txt").write_text("x" * 5000, encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "lib.js").write_text("x\n", encoding="utf-8")

            collection = collect(root, max_file_size=1024)
            paths = {entry.rel for entry in collection.entries}
            self.assertIn("src/main.py", paths)
            self.assertIn(".gitignore", paths)
            self.assertNotIn("secret.txt", paths)
            self.assertNotIn("image.png", paths)
            self.assertNotIn("big.txt", paths)
            self.assertNotIn("node_modules/lib.js", paths)
            reasons = {skip.rel: skip.reason for skip in collection.skipped}
            self.assertEqual(reasons["secret.txt"], "ignored")
            self.assertEqual(reasons["image.png"], "binary")
            self.assertEqual(reasons["big.txt"], "too large")

    def test_exclude_prunes_whole_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("a\n", encoding="utf-8")
            (root / "reference").mkdir()
            (root / "reference" / "notes.md").write_text("n\n", encoding="utf-8")
            (root / "reference" / "deep").mkdir()
            (root / "reference" / "deep" / "x.txt").write_text("x\n", encoding="utf-8")

            collection = collect(root, exclude=["reference"])
            self.assertEqual([entry.rel for entry in collection.entries], ["src/a.py"])
            reasons = {skip.rel: skip.reason for skip in collection.skipped}
            self.assertEqual(reasons["reference"], "excluded")

    def test_alltooneignore_survives_no_gitignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("a\n", encoding="utf-8")
            (root / "reference").mkdir()
            (root / "reference" / "notes.md").write_text("n\n", encoding="utf-8")
            (root / ".gitignore").write_text("a.py\n", encoding="utf-8")
            (root / ".alltooneignore").write_text("reference/\n", encoding="utf-8")

            collection = collect(root, use_gitignore=False)
            paths = {entry.rel for entry in collection.entries}
            self.assertIn("a.py", paths)
            self.assertNotIn("reference/notes.md", paths)

    def test_alltooneignore_inside_a_folder_drops_the_subtree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("a\n", encoding="utf-8")
            (root / "reference").mkdir()
            (root / "reference" / ".alltooneignore").write_text("*\n", encoding="utf-8")
            (root / "reference" / "notes.md").write_text("n\n", encoding="utf-8")
            (root / "reference" / "deep").mkdir()
            (root / "reference" / "deep" / "x.txt").write_text("x\n", encoding="utf-8")

            collection = collect(root)
            self.assertEqual([entry.rel for entry in collection.entries], ["src/a.py"])

    def test_alltooneignore_with_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("a\n", encoding="utf-8")
            (root / "reference").mkdir()
            (root / "reference" / "notes.md").write_text("n\n", encoding="utf-8")
            # Windows editors (and PowerShell) write a BOM; the first rule must still work.
            (root / ".alltooneignore").write_bytes("reference/\n".encode("utf-8-sig"))

            collection = collect(root)
            paths = {entry.rel for entry in collection.entries}
            self.assertIn("a.py", paths)
            self.assertNotIn("reference/notes.md", paths)

    def test_negation_in_alltooneignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "debug.log").write_text("d\n", encoding="utf-8")
            (root / "keep.log").write_text("k\n", encoding="utf-8")
            (root / ".alltooneignore").write_text("*.log\n!keep.log\n", encoding="utf-8")

            collection = collect(root)
            paths = {entry.rel for entry in collection.entries}
            self.assertIn("keep.log", paths)
            self.assertNotIn("debug.log", paths)

    def test_include_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("a\n", encoding="utf-8")
            (root / "b.md").write_text("b\n", encoding="utf-8")
            collection = collect(root, include=["*.py"])
            self.assertEqual([entry.rel for entry in collection.entries], ["a.py"])


class CliTests(unittest.TestCase):
    def _run(self, *argv: str) -> str:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(list(argv))
        self.assertEqual(code, 0, stderr.getvalue())
        return stderr.getvalue()

    def test_writes_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hello.py").write_text("print('hello')\n", encoding="utf-8")
            output = root / "context.md"
            self._run(str(root), "-o", str(output))
            text = output.read_text(encoding="utf-8")
            self.assertIn("### `hello.py`", text)
            self.assertIn("print('hello')", text)

    def test_split_creates_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(4):
                (root / f"file{index}.txt").write_text("word " * 200, encoding="utf-8")
            output = root / "context.md"
            self._run(str(root), "-o", str(output), "--max-tokens", "300")
            parts = sorted(root.glob("context.part*.md"))
            self.assertGreater(len(parts), 1)
            self.assertIn("part 1 of", parts[0].read_text(encoding="utf-8"))

    def test_tree_only_has_no_files_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("x\n", encoding="utf-8")
            output = root / "context.md"
            self._run(str(root), "-o", str(output), "--tree-only")
            text = output.read_text(encoding="utf-8")
            self.assertIn("## Project structure", text)
            self.assertIn("a.py", text)
            self.assertNotIn("## Files", text)
            self.assertIn("1 files", text)

    def test_exclude_from_file_and_show_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("app\n", encoding="utf-8")
            (root / "reference").mkdir()
            (root / "reference" / "notes.md").write_text("reference material\n", encoding="utf-8")
            rules = root / "my-rules.txt"
            rules.write_text("reference/\n", encoding="utf-8")
            output = root / "context.md"

            stderr = self._run(
                str(root),
                "-o",
                str(output),
                "--exclude-from",
                str(rules),
                "--show-skipped",
            )
            text = output.read_text(encoding="utf-8")
            self.assertIn("app.py", text)
            self.assertNotIn("reference material", text)
            self.assertIn("reference (ignored)", stderr)

    def test_exclude_flag_prunes_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("app\n", encoding="utf-8")
            (root / "reference").mkdir()
            (root / "reference" / "notes.md").write_text("reference material\n", encoding="utf-8")
            output = root / "context.md"
            self._run(str(root), "-o", str(output), "--exclude", "reference")
            self.assertNotIn("reference material", output.read_text(encoding="utf-8"))

    def test_missing_exclude_file_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main([tmp, "--exclude-from", str(Path(tmp) / "nope.txt")])
            self.assertEqual(code, 2)
            self.assertIn("exclude file not found", stderr.getvalue())

    def test_missing_path_returns_error(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(["definitely/not/here"])
        self.assertEqual(code, 2)


class HandoffTests(unittest.TestCase):
    def test_state_comes_first_and_only_listed_files_are_packed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("print('a')\n", encoding="utf-8")
            (root / "b.py").write_text("print('b')\n", encoding="utf-8")
            state = root / "STATE.md"
            state.write_text("# State\n\nnext: read b.py\n", encoding="utf-8")
            wanted = root / "files.txt"
            wanted.write_text("b.py\n", encoding="utf-8")
            output = root / "pack.md"

            code = main(
                [
                    "handoff",
                    str(root),
                    "--state",
                    str(state),
                    "--files-from",
                    str(wanted),
                    "-o",
                    str(output),
                    "-q",
                ]
            )
            self.assertEqual(code, 0)

            text = output.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# Handoff state:"))
            self.assertLess(text.index("next: read b.py"), text.index("# Project context:"))
            self.assertIn("print('b')", text)
            self.assertNotIn("print('a')", text)
            # The map still covers the whole project, not only the packed files.
            self.assertIn("a.py", text)

    def test_unknown_path_is_skipped_with_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("print('a')\n", encoding="utf-8")
            wanted = root / "files.txt"
            wanted.write_text("a.py\nmissing.py\n", encoding="utf-8")
            output = root / "pack.md"

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(["handoff", str(root), "--files-from", str(wanted), "-o", str(output), "-q"])
            self.assertEqual(code, 0)
            self.assertIn("missing.py", stderr.getvalue())
            self.assertIn("print('a')", output.read_text(encoding="utf-8"))

    def test_empty_list_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("print('a')\n", encoding="utf-8")
            wanted = root / "files.txt"
            wanted.write_text("# nothing here\n", encoding="utf-8")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(["handoff", str(root), "--files-from", str(wanted)])
            self.assertEqual(code, 2)
            self.assertIn("file list is empty", stderr.getvalue())

    GOOD_STATE = """# State

## Goal
Continue the work.

## Done
- `a.py` exists.

## Next
1. Read `b.py`.

## Decisions
- Keep it small.

## Open questions
- None.

## Files
- `b.py` — the next step.

## Commands
- `python -m unittest`
"""

    def _check(self, root: Path, files: str, state: str | None = None):
        state_path = root / "STATE.md"
        state_path.write_text(self.GOOD_STATE if state is None else state, encoding="utf-8")
        wanted = root / "files.txt"
        wanted.write_text(files, encoding="utf-8")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(
                [
                    "handoff",
                    str(root),
                    "--state",
                    str(state_path),
                    "--files-from",
                    str(wanted),
                    "--check",
                ]
            )
        return code, stderr.getvalue()

    def test_check_accepts_a_good_state_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b.py").write_text("print('b')\n", encoding="utf-8")
            code, stderr = self._check(root, "b.py\n")
            self.assertEqual(code, 0)
            self.assertIn("  ok", stderr)
            # The check must not write a pack.
            self.assertFalse((root / ".opencode").exists())

    def test_check_reports_a_state_over_the_line_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b.py").write_text("print('b')\n", encoding="utf-8")
            bloated = self.GOOD_STATE.replace("## Done\n", "## Done\n" + "- filler\n" * 60)
            code, stderr = self._check(root, "b.py\n", state=bloated)
            self.assertEqual(code, 1)
            self.assertIn("limit 60", stderr)

    def test_check_reports_missing_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b.py").write_text("print('b')\n", encoding="utf-8")
            broken = self.GOOD_STATE.replace("## Decisions", "## Notes")
            code, stderr = self._check(root, "b.py\n", state=broken)
            self.assertEqual(code, 1)
            self.assertIn("missing sections: Decisions", stderr)

    def test_check_warns_about_a_long_decisions_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b.py").write_text("print('b')\n", encoding="utf-8")
            bloated = self.GOOD_STATE.replace(
                "## Decisions\n", "## Decisions\n" + "- extra decision\n" * 8
            )
            code, stderr = self._check(root, "b.py\n", state=bloated)
            # A long Decisions list is a warning, not a failure: the state still works.
            self.assertEqual(code, 0)
            self.assertIn("9 decisions", stderr)
            self.assertIn("  ! ", stderr)

    def test_check_reports_bad_paths_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b.py").write_text("print('b')\n", encoding="utf-8")
            code, stderr = self._check(root, "b.py\nmissing.py\n../escape.py\nC:/abs.py\nb.py\n")
            self.assertEqual(code, 1)
            self.assertIn("missing.py does not exist", stderr)
            self.assertIn("../escape.py must be a relative path", stderr)
            self.assertIn("C:/abs.py must be a relative path", stderr)
            self.assertIn("b.py is listed twice", stderr)

    def test_check_reports_a_listed_but_ignored_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b.py").write_text("print('b')\n", encoding="utf-8")
            (root / "secret.py").write_text("TOKEN = 'x'\n", encoding="utf-8")
            (root / ".gitignore").write_text("secret.py\n", encoding="utf-8")
            code, stderr = self._check(root, "b.py\nsecret.py\n")
            self.assertEqual(code, 1)
            self.assertIn("secret.py is skipped", stderr)

    def test_unpack_detects_a_handoff_pack_without_a_format_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            (root / ".opencode/handoff").mkdir(parents=True)
            (root / "app.py").write_text("print(1)\n", encoding="utf-8")
            state = root / ".opencode/handoff/STATE.md"
            state.write_text("# State\n\n## Goal\nContinue.\n", encoding="utf-8")
            wanted = root / ".opencode/handoff/files.txt"
            wanted.write_text("app.py\n", encoding="utf-8")
            pack = Path(tmp) / "pack.md"

            self.assertEqual(
                main(
                    [
                        "handoff",
                        str(root),
                        "--state",
                        str(state),
                        "--files-from",
                        str(wanted),
                        "-o",
                        str(pack),
                        "-q",
                    ]
                ),
                0,
            )
            dest = Path(tmp) / "out"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(["unpack", str(pack), "-o", str(dest)])
            self.assertEqual(code, 0, stderr.getvalue())
            self.assertEqual((dest / "app.py").read_text(encoding="utf-8"), "print(1)\n")
            self.assertIn("files verified", stderr.getvalue())
            # The state is text in the header, not a packed file.
            self.assertFalse((dest / ".opencode").exists())

    def test_unpack_ignores_markdown_quoted_in_the_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            (root / ".opencode/handoff").mkdir(parents=True)
            (root / "app.py").write_text("print(1)\n", encoding="utf-8")
            state = root / ".opencode/handoff/STATE.md"
            state.write_text(
                "# State\n\n## Done\n\n```md\n### `fake.py`\nnot a real file\n```\n",
                encoding="utf-8",
            )
            wanted = root / ".opencode/handoff/files.txt"
            wanted.write_text("app.py\n", encoding="utf-8")
            pack = Path(tmp) / "pack.md"
            main(
                [
                    "handoff",
                    str(root),
                    "--state",
                    str(state),
                    "--files-from",
                    str(wanted),
                    "-o",
                    str(pack),
                    "-q",
                ]
            )
            dest = Path(tmp) / "out"
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(["unpack", str(pack), "-o", str(dest)])
            self.assertEqual(code, 0)
            self.assertTrue((dest / "app.py").is_file())
            self.assertFalse((dest / "fake.py").exists())


class PluginBudgetTests(unittest.TestCase):
    """The context-budget plugin is TypeScript; node drives its branches.

    Skipped when node is absent: the package has no runtime dependencies and the
    suite must stay runnable with plain Python.
    """

    def test_compaction_is_opt_in_and_once_per_session(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        script = Path(__file__).with_name("plugin_budget_test.mjs")
        source = Path(__file__).parents[1] / ".opencode" / "plugins" / "context-budget" / "index.ts"
        result = subprocess.run(
            [node, str(script), str(source)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, f"{result.stdout}\n{result.stderr}")


class UsageScriptTests(unittest.TestCase):
    """The usage report reads an OpenCode V2 store; a fixture keeps it honest.

    The store is an internal format, so the script must fail softly on a
    mismatch and must never write: both are cheaper to test here than to find
    out on a real database.
    """

    SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "usage.py"

    def _store(self, root: Path, project: Path) -> Path:
        db = root / "opencode.db"
        con = sqlite3.connect(db)
        con.executescript(
            "create table session_v2 (id text primary key, directory text,"
            " time_created integer, time_compacting integer);"
            "create table session_message (id text primary key, session_id text,"
            " type text, seq integer, data text);"
        )
        con.executemany(
            "insert into session_v2 values (?, ?, ?, ?)",
            [
                ("ses_new", str(project), 2_000_000_000_000, None),
                ("ses_old", str(project), 1_000_000_000_000, 1_234_567),
            ],
        )
        pack = str(project / ".opencode" / "handoff" / "pack.md")
        con.executemany(
            "insert into session_message values (?, ?, ?, ?, ?)",
            [
                (
                    "msg_new",
                    "ses_new",
                    "assistant",
                    1,
                    json.dumps(
                        {
                            "model": {"id": "m1"},
                            "tokens": {"input": 100, "output": 10, "cache": {"read": 300, "write": 0}},
                            "content": [
                                {"type": "tool", "name": "read", "state": {"input": {"path": pack}}},
                                {"type": "tool", "name": "read", "state": {"input": {"path": "a.py"}}},
                                {
                                    "type": "tool",
                                    "name": "edit",
                                    "state": {
                                        "status": "error",
                                        "error": {
                                            "type": "tool.execution",
                                            "message": "No changes to apply",
                                        },
                                    },
                                },
                            ],
                        }
                    ),
                ),
                (
                    "msg_old",
                    "ses_old",
                    "assistant",
                    1,
                    json.dumps(
                        {
                            "model": {"id": "m1"},
                            "tokens": {"input": 50, "output": 5, "cache": {"read": 50, "write": 0}},
                            "content": [],
                        }
                    ),
                ),
            ],
        )
        con.commit()
        con.close()
        return db

    def _run(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), *argv],
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_report_reads_the_store_without_writing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "proj"
            listing = project / ".opencode" / "handoff"
            listing.mkdir(parents=True)
            (listing / "files.txt").write_text("a.py\nb.py\n", encoding="utf-8")
            db = self._store(root, project)
            before = db.stat().st_mtime_ns

            result = self._run("--db", str(db), "--project", str(project), "--sessions", "5")

            self.assertEqual(result.returncode, 0, result.stderr)
            out = result.stdout
            self.assertIn("75%", out)  # 300 of 400 prompt tokens from cache
            self.assertIn("50%", out)  # 50 of 100 in the older session
            self.assertIn("(compacted)", out)
            self.assertIn(".opencode/handoff/pack.md", out)
            self.assertIn("a.py", out)
            self.assertIn("m1", out)
            # Listed in files.txt and never opened in either session.
            self.assertIn("b.py", out)
            self.assertEqual(db.stat().st_mtime_ns, before)

    def test_tool_errors_and_skills_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "proj"
            (project / ".opencode" / "handoff").mkdir(parents=True)
            skill = project / ".opencode" / "skills" / "demo" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("---\nname: demo\n---\n", encoding="utf-8")
            db = self._store(root, project)

            result = self._run("--db", str(db), "--project", str(project))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("tool errors", result.stdout)
            self.assertIn("edit: No changes to apply", result.stdout)
            self.assertIn("skills (1): demo", result.stdout)

    def test_foreign_database_fails_softly(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "empty.db"
            sqlite3.connect(db).close()
            result = self._run("--db", str(db))
            self.assertEqual(result.returncode, 1)
            self.assertIn("schema may have changed", result.stderr)

    def test_missing_database_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run("--db", str(Path(tmp) / "nope.db"))
            self.assertEqual(result.returncode, 2)
            self.assertIn("not found", result.stderr)


class UnpackTests(unittest.TestCase):
    SAMPLES = {
        "a.py": "print('a')\n",
        "empty.txt": "",
        "no_newline.txt": "abc",
        "trailing.txt": "a\n\n\n",
        "backticks.md": "```js\ncode\n```\n### `fake.py`\n",
        "nested/deep/x.txt": "x\n",
        "cyrillic.txt": "привет\n",
    }

    def _make_project(self, base: Path) -> Path:
        root = base / "project"
        for rel, content in self.SAMPLES.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="")
        return root

    def _assert_restored(self, dest: Path) -> None:
        for rel, content in self.SAMPLES.items():
            restored = (dest / rel).read_text(encoding="utf-8")
            self.assertEqual(restored, content, f"mismatch in {rel}")

    def test_round_trip_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self._make_project(base)
            context = base / "ctx.md"
            self.assertEqual(main([str(root), "-o", str(context), "-q"]), 0)
            dest = base / "restored"
            unpack(context.read_text(encoding="utf-8"), dest)
            self._assert_restored(dest)

    def test_round_trip_xml(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self._make_project(base)
            context = base / "ctx.xml"
            self.assertEqual(main([str(root), "-f", "xml", "-o", str(context), "-q"]), 0)
            dest = base / "restored"
            unpack(context.read_text(encoding="utf-8"), dest)
            self._assert_restored(dest)

    def test_round_trip_plain(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self._make_project(base)
            context = base / "ctx.txt"
            self.assertEqual(main([str(root), "-f", "plain", "-o", str(context), "-q"]), 0)
            dest = base / "restored"
            unpack(context.read_text(encoding="utf-8"), dest)
            self._assert_restored(dest)

    def test_format_detection(self):
        self.assertEqual(detect_format("<?xml version='1.0'?><context/>", "x"), "xml")
        self.assertEqual(detect_format("# Project context: demo", "x"), "markdown")
        self.assertEqual(detect_format("PROJECT: demo", "x"), "plain")
        with self.assertRaises(UnpackError):
            detect_format("just some text", "x")

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for bad in ("../evil.txt", "/etc/passwd", "C:/Windows/evil.txt", "a/../../evil.txt"):
                with self.assertRaises(UnpackError, msg=bad):
                    safe_target(root, bad)

    def test_does_not_overwrite_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            document = "# Project context: demo\n\n### `a.txt`\n\n```text\nnew\n```\n"
            (dest / "a.txt").write_text("old\n", encoding="utf-8")
            result = unpack(document, dest)
            self.assertEqual(result.skipped, ["a.txt"])
            self.assertEqual((dest / "a.txt").read_text(encoding="utf-8"), "old\n")

            result = unpack(document, dest, overwrite=True)
            self.assertEqual(result.written, ["a.txt"])
            self.assertEqual((dest / "a.txt").read_text(encoding="utf-8"), "new")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            document = "# Project context: demo\n\n### `a/b.txt`\n\n```text\nhi\n```\n"
            result = unpack(document, dest, dry_run=True)
            self.assertEqual(result.written, ["a/b.txt"])
            self.assertFalse((dest / "a" / "b.txt").exists())

    def test_rejects_duplicate_paths(self):
        document = (
            "# Project context: demo\n\n### `a.txt`\n\n```text\n1\n```\n\n"
            "### `a.txt`\n\n```text\n2\n```\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(UnpackError):
                unpack(document, Path(tmp))

    def test_empty_context_is_rejected(self):
        with self.assertRaises(UnpackError):
            unpack("# Project context: demo\n", Path(tempfile.gettempdir()))

    def test_cli_unpack(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self._make_project(base)
            context = base / "ctx.md"
            self.assertEqual(main([str(root), "-o", str(context), "-q"]), 0)
            dest = base / "restored"
            self.assertEqual(main(["unpack", str(context), "-o", str(dest), "-q"]), 0)
            self._assert_restored(dest)


class IntegrityTests(unittest.TestCase):
    def _pack(self, base: Path, fmt: str, *extra: str) -> Path:
        root = base / "project"
        (root / "src").mkdir(parents=True)
        (root / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
        (root / "b.txt").write_text("bee\n", encoding="utf-8")
        suffix = "xml" if fmt == "xml" else "md"
        context = base / f"ctx.{suffix}"
        main([str(root), "-f", fmt, "-o", str(context), "-q", *extra])
        return context

    def test_manifest_is_written_in_every_format(self):
        for fmt in ("markdown", "xml", "plain"):
            with self.subTest(fmt=fmt), tempfile.TemporaryDirectory() as tmp:
                context = self._pack(Path(tmp), fmt)
                text = context.read_text(encoding="utf-8")
                if fmt == "xml":
                    self.assertIn('sha256="', text)
                    self.assertIn("<total hash=", text)
                else:
                    self.assertIn("sha256 manifest v1", text)
                    self.assertRegex(text, r"(?m)^total [0-9a-f]{64}$")

    def test_manifest_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = self._pack(Path(tmp), "markdown", "--no-integrity")
            self.assertNotIn("## Integrity", context.read_text(encoding="utf-8"))

    def test_corrupted_content_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            context = self._pack(base, "markdown")
            text = context.read_text(encoding="utf-8").replace("print('a')", "print('b')")
            dest = base / "restored"
            with self.assertRaises(UnpackError) as raised:
                unpack(text, dest)
            self.assertIn("checksum mismatch", str(raised.exception))
            self.assertFalse((dest / "src" / "a.py").exists())

    def test_dropped_manifest_line_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            context = self._pack(base, "markdown")
            text = context.read_text(encoding="utf-8")
            damaged = re.sub(r"(?m)^[0-9a-f]{64}  b\.txt$\n", "", text)
            self.assertNotEqual(text, damaged)
            with self.assertRaises(UnpackError) as raised:
                unpack(damaged, base / "restored")
            message = str(raised.exception)
            self.assertIn("without a checksum", message)
            self.assertIn("integrity block itself is damaged", message)

    def test_no_verify_allows_damaged_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            context = self._pack(base, "markdown")
            text = context.read_text(encoding="utf-8").replace("print('a')", "print('b')")
            dest = base / "restored"
            result = unpack(text, dest, verify_checksums=False)
            self.assertEqual(len(result.written), 2)
            self.assertIn("print('b')", (dest / "src" / "a.py").read_text(encoding="utf-8"))

    def test_context_without_manifest_still_unpacks(self):
        document = "# Project context: demo\n\n### `a.txt`\n\n```text\nhi\n```\n"
        with tempfile.TemporaryDirectory() as tmp:
            result = unpack(document, Path(tmp))
            self.assertFalse(result.verified)
            self.assertEqual(result.written, ["a.txt"])


class InstallTests(unittest.TestCase):
    """The installers are the only path an outsider has to the workflow.

    They ship the plugin, the commands and the agreement, so a file added to one
    installer and not the other — or a path that no longer exists — quietly
    breaks a fresh project while this repository stays green.
    """

    ROOT = Path(__file__).resolve().parents[1]

    def _ps1(self) -> list[str]:
        text = (self.ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
        start = text.index("$files = @(")
        end = text.index(")", start)
        return [path.replace("\\", "/") for path in re.findall(r'"([^"]+)"', text[start:end])]

    def _sh(self) -> list[str]:
        text = (self.ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
        start = text.index("for rel in")
        end = text.index("; do", start)
        return [path.replace("\\", "/") for path in re.findall(r'"([^"]+)"', text[start:end])]

    def test_both_installers_ship_the_same_workflow(self):
        self.assertEqual(self._ps1(), self._sh())

    def test_every_shipped_path_exists(self):
        shipped = self._sh()
        self.assertTrue(shipped)
        for rel in shipped:
            self.assertTrue((self.ROOT / rel).exists(), rel)

    def test_the_skill_template_travels_but_personal_skills_do_not(self):
        shipped = self._sh()
        self.assertIn(".opencode/skills/skill-template.md", shipped)
        # A learned skill names this machine's paths and sessions as proof.
        self.assertFalse([rel for rel in shipped if rel.endswith("SKILL.md")])

    def test_the_agreement_names_every_shipped_file(self):
        # The workflow is only usable if the rules and the copies agree.
        agreement = (self.ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for rel in (".opencode/skills/",):
            self.assertIn(rel, agreement)


class SkillsTests(unittest.TestCase):
    """A learned skill is cheap to write and expensive to trust.

    The checks are the trust: a catalog whose descriptions do not read as
    symptoms is a catalog the next session never finds.
    """

    GOOD = """---
name: demo
description: `shell` fails with `boom` — run the probe before the real call
---

# shell fails with boom

## Cause
A demo cause.

## Recipe
```powershell
echo ok
```

## Proof
`ses_demo` — exit 0.
"""

    def _skill(self, root: Path, slug: str, text: str) -> Path:
        path = root / ".opencode" / "skills" / slug / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_no_skills_directory_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(check_skills(Path(tmp)), [])

    def test_a_good_skill_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._skill(root, "demo", self.GOOD)
            self.assertEqual(check_skills(root), [])

    def test_missing_frontmatter_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._skill(root, "demo", "---\nname: demo\n")
            problems = check_skills(root)
            self.assertTrue(any("frontmatter" in problem for problem in problems), problems)

    def test_topic_description_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broken = self.GOOD.replace(
                "description: `shell` fails with `boom` — run the probe before the real call",
                "description: Working with the shell tool in PowerShell",
            )
            self._skill(root, "demo", broken)
            problems = check_skills(root)
            self.assertTrue(any("symptom" in problem for problem in problems), problems)

    def test_name_must_match_the_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._skill(root, "demo", self.GOOD.replace("name: demo", "name: other"))
            problems = check_skills(root)
            self.assertTrue(any("must match the directory" in problem for problem in problems), problems)

    def test_missing_proof_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._skill(root, "demo", self.GOOD.split("## Proof")[0])
            problems = check_skills(root)
            self.assertTrue(any("Proof" in problem for problem in problems), problems)

    def test_long_skill_and_duplicate_description_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._skill(root, "demo", self.GOOD + "\nfiller\n" * 60)
            twin = self.GOOD.replace("name: demo", "name: twin")
            self._skill(root, "twin", twin)
            problems = check_skills(root)
            self.assertTrue(any("limit 50" in problem for problem in problems), problems)
            self.assertTrue(any("duplicates" in problem for problem in problems), problems)

    def test_the_catalog_is_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(13):
                slug = f"s{index}"
                text = self.GOOD.replace("name: demo", f"name: {slug}").replace(
                    "`shell` fails with `boom`", f"`shell` fails with `boom{index}`"
                )
                self._skill(root, slug, text)
            problems = check_skills(root)
            self.assertTrue(any("limit 12" in problem for problem in problems), problems)

    def test_handoff_check_reports_a_bad_skill_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("print('a')\n", encoding="utf-8")
            listing = root / ".opencode" / "handoff"
            listing.mkdir(parents=True)
            (listing / "STATE.md").write_text(HandoffTests.GOOD_STATE, encoding="utf-8")
            (listing / "files.txt").write_text("a.py\n", encoding="utf-8")
            self._skill(root, "demo", self.GOOD.replace("name: demo", "name: wrong"))

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(["handoff", str(root), "--check"])
            self.assertEqual(code, 1)
            self.assertIn("skills:", stderr.getvalue())
            self.assertFalse((listing / "pack.md").exists())

    def test_the_agreement_stays_within_its_budget(self):
        # Every rule in AGENTS.md is paid for in every request; a self-improving
        # agent grows it silently unless the budget is a test.
        agreement = Path(__file__).resolve().parents[1] / "AGENTS.md"
        self.assertLessEqual(len(agreement.read_text(encoding="utf-8").splitlines()), 120)


if __name__ == "__main__":
    unittest.main()
