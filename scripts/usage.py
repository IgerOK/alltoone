"""Usage report from the OpenCode session store.

The meter in `.opencode/plugins/context-budget` only shows the current window.
The questions that need history are different: how much of the prompt came from
cache, how many times a new session re-read the handoff pack, which packed files
were actually used, and which model did the work.

The database is the provider's own record, so the numbers are measured, not
estimated. It is opened with `mode=ro` and never written; nothing but paths,
counts and model names is printed — no prompt content ever leaves the file.

Usage:
    python scripts/usage.py
    python scripts/usage.py --db path/to/opencode.db --sessions 20
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PACK = ".opencode/handoff/pack.md"
FILES_LIST = Path(".opencode/handoff/files.txt")


def default_db() -> Path | None:
    """Find the session store the way OpenCode lays it out on each platform."""
    home = Path.home()
    roots: list[Path] = []
    xdg = os.environ.get("XDG_DATA_HOME")
    roots.append(Path(xdg) / "opencode" if xdg else home / ".local" / "share" / "opencode")
    for variable in ("APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value) / "opencode")
    for root in roots:
        candidate = root / "opencode.db"
        if candidate.is_file():
            return candidate
    return None


def human(value: float) -> str:
    """Short human form of a token count."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 10_000:
        return f"{round(value / 1000)}k"
    if value >= 1_000:
        return f"{value / 1000:.1f}k"
    return str(int(value))


def relative(path: str, root: str) -> str:
    """Project-relative path, keeping paths that are already relative as they are."""
    raw = path.replace("\\", "/")
    base = root.replace("\\", "/").rstrip("/")
    if base and raw.lower().startswith(base.lower() + "/"):
        return raw[len(base) + 1 :]
    if not raw.startswith("/") and not re.match(r"^[A-Za-z]:", raw):
        # Some tool calls pass a path relative to the project root already.
        return raw[2:] if raw.startswith("./") else raw
    return raw.rsplit("/", 1)[-1]


@dataclass
class Session:
    """One session as the report sees it."""

    id: str
    created: int
    directory: str
    compacted: bool
    requests: int = 0
    prompt: int = 0
    cached: int = 0
    output: int = 0
    reasoning: int = 0
    pack_reads: int = 0
    tools: Counter = field(default_factory=Counter)
    reads: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)

    @property
    def hit(self) -> float:
        return self.cached / self.prompt if self.prompt else 0.0


@dataclass
class Model:
    """Per-model totals across the reported sessions."""

    requests: int = 0
    prompt: int = 0
    cached: int = 0
    output: int = 0
    reasoning: int = 0
    cost: float = 0.0

    @property
    def hit(self) -> float:
        return self.cached / self.prompt if self.prompt else 0.0


def _schema_problem(con: sqlite3.Connection) -> str:
    """Return why this database is not an OpenCode V2 store, "" when it is."""
    tables = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
    missing = {"session_v2", "session_message"} - tables
    if missing:
        return "not an OpenCode session store (missing: " + ", ".join(sorted(missing)) + ")"
    return ""


def _message(message: dict, session: Session, models: dict[str, Model]) -> None:
    """Fold one assistant message into the session and model totals."""
    tokens = message.get("tokens")
    model = (message.get("model") or {}).get("id")
    if model:
        totals = models.setdefault(model, Model())
        totals.requests += 1
        totals.cost += message.get("cost") or 0
    if tokens:
        cached = tokens.get("cache") or {}
        read = cached.get("read", 0) or 0
        write = cached.get("write", 0) or 0
        input_tokens = tokens.get("input", 0) or 0
        output = tokens.get("output", 0) or 0
        reasoning = tokens.get("reasoning", 0) or 0
        # `input` is the uncached part on the providers seen so far; the sum is
        # what the request actually occupied, which is what the hit rate means.
        session.requests += 1
        session.prompt += input_tokens + read + write
        session.cached += read
        session.output += output
        session.reasoning += reasoning
        if model:
            totals = models.setdefault(model, Model())
            totals.prompt += input_tokens + read + write
            totals.cached += read
            totals.output += output
            totals.reasoning += reasoning

    for part in message.get("content") or []:
        if part.get("type") != "tool":
            continue
        name = part.get("name") or "?"
        session.tools[name] += 1
        # A tool that failed is a skill candidate, but only a candidate: the
        # error text decides whether the failure taught anything (see AGENTS.md).
        state = part.get("state") or {}
        if state.get("status") == "error":
            error = state.get("error")
            message = error.get("message") if isinstance(error, dict) else str(error or "")
            session.errors[f"{name}: {str(message).strip()}"] += 1
        if name != "read":
            continue
        target = ((part.get("state") or {}).get("input") or {}).get("path")
        if not target:
            continue
        path = relative(str(target), session.directory)
        session.reads[path] += 1
        if path == PACK or path.endswith("/" + PACK) or path == "pack.md":
            session.pack_reads += 1


def load(con: sqlite3.Connection, limit: int) -> tuple[list[Session], dict[str, Model]]:
    """Read the newest ``limit`` sessions with their messages."""
    rows = con.execute(
        "select id, directory, time_created, time_compacting from session_v2"
        " order by time_created desc limit ?",
        (limit,),
    ).fetchall()
    sessions: list[Session] = []
    models: dict[str, Model] = {}
    for session_id, directory, created, compacting in rows:
        session = Session(session_id, created, directory or "", compacting is not None)
        messages = con.execute(
            "select data from session_message where session_id=? and type='assistant' order by seq",
            (session_id,),
        ).fetchall()
        for (data,) in messages:
            try:
                session_message = json.loads(data)
            except json.JSONDecodeError:
                continue
            _message(session_message, session, models)
        sessions.append(session)
    return sessions, models


def packed_not_read(sessions: list[Session], project: Path) -> list[str]:
    """Files listed in ``files.txt`` that no reported session ever opened."""
    listing = project / FILES_LIST
    if not sessions or not listing.is_file():
        return []
    wanted = [
        line.strip().replace("\\", "/")
        for line in listing.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    read = {path for session in sessions for path in session.reads}
    return [path for path in wanted if path not in read]


def skill_names(project: Path) -> list[str]:
    """Slugs of the project's earned skills, newest convention first-class."""
    return sorted(path.parent.name for path in (project / ".opencode" / "skills").glob("*/SKILL.md"))


def render(
    sessions: list[Session],
    models: dict[str, Model],
    unused: list[str],
    db: Path,
    skills: list[str] | None = None,
) -> str:
    """Format the report as fixed-width text."""
    out: list[str] = [f"usage from {db} (read-only)", ""]

    out.append("sessions (newest first)")
    header = f"  {'when':<12} {'session':<14} {'req':>4} {'prompt':>7} {'hit':>5} {'out':>6} {'pack':>4}  tools"
    out.append(header)
    for session in sessions:
        when = datetime.fromtimestamp(session.created / 1000).strftime("%m-%d %H:%M")
        tools = " · ".join(f"{name} {count}" for name, count in session.tools.most_common(3)) or "-"
        mark = " (compacted)" if session.compacted else ""
        out.append(
            f"  {when:<12} {session.id[:14]:<14} {session.requests:>4} {human(session.prompt):>7}"
            f" {session.hit:>4.0%} {human(session.output):>6} {session.pack_reads:>4}  {tools}{mark}"
        )
    out.append("")
    out.append("  hit = cached / (input + cached); input excludes the cached part on these providers")

    if models:
        out.extend(["", "models"])
        out.append(f"  {'model':<30} {'req':>4} {'prompt':>7} {'hit':>5} {'out':>6} {'reasoning':>9}")
        for name, model in sorted(models.items(), key=lambda item: -item[1].prompt):
            share = model.reasoning / (model.output + model.reasoning) if model.output + model.reasoning else 0.0
            out.append(
                f"  {name:<30} {model.requests:>4} {human(model.prompt):>7} {model.hit:>4.0%}"
                f" {human(model.output):>6} {share:>8.0%}"
            )
        spent = sum(model.cost for model in models.values())
        if spent > 0:
            out.append(f"  cost {spent:.4f} USD")

    reads: Counter = Counter()
    for session in sessions:
        reads.update(session.reads)
    if reads:
        out.extend(["", "files read"])
        for path, count in reads.most_common():
            out.append(f"  {count:>3}  {path}")

    if unused:
        out.extend(["", "listed in files.txt, never read in these sessions"])
        out.extend(f"  {path}" for path in unused)

    errors: Counter = Counter()
    for session in sessions:
        errors.update(session.errors)
    if errors:
        # Grouped by message, not by session: a message that repeats is the
        # signature of a failure worth writing down.
        out.extend(["", "tool errors (a repeated one is a skill candidate)"])
        for line, count in errors.most_common(8):
            out.append(f"  {count:>3}  {line[:110]}")

    if skills:
        out.extend(["", f"skills ({len(skills)}): " + ", ".join(skills)])

    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass

    parser = argparse.ArgumentParser(
        prog="usage.py",
        description="Report cache, pack reads and model mix from the OpenCode session store.",
    )
    parser.add_argument("--db", metavar="FILE", help="path to opencode.db (default: the platform location)")
    parser.add_argument("--project", metavar="DIR", default=".", help="project with files.txt (default: .)")
    parser.add_argument("--sessions", type=int, default=10, metavar="N", help="how many sessions (default: 10)")
    args = parser.parse_args(argv)

    db = Path(args.db).expanduser() if args.db else default_db()
    if db is None or not db.is_file():
        print("error: opencode.db not found; pass --db", file=sys.stderr)
        return 2

    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        print(f"error: cannot open {db}: {exc}", file=sys.stderr)
        return 2

    try:
        problem = _schema_problem(con)
        if problem:
            print(f"error: {problem} — the schema may have changed", file=sys.stderr)
            return 1
        sessions, models = load(con, args.sessions)
    except sqlite3.Error as exc:
        print(f"error: cannot read {db}: {exc} — the schema may have changed", file=sys.stderr)
        return 1
    finally:
        con.close()

    unused = packed_not_read(sessions, Path(args.project).expanduser())
    skills = skill_names(Path(args.project).expanduser())
    print(render(sessions, models, unused, db, skills), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
