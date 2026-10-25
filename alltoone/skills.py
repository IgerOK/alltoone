"""Validate the project's earned skills (``.opencode/skills/<slug>/SKILL.md``).

A skill is written at the moment a repeated failure is solved, so the catalog is
a record of things that actually cost time. It is not free: every skill's
description sits in the system prompt of every request, which is why the checks
push it to stay small and searchable — the description has to read as a symptom,
because that is what the next agent is looking at when it needs the skill.
"""

from __future__ import annotations

import re
from pathlib import Path

SKILLS_DIR = ".opencode/skills"
SKILL_FILE = "SKILL.md"
MAX_SKILLS = 12
MAX_LINES = 50
MIN_DESCRIPTION = 20
MAX_DESCRIPTION = 200
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# A description is matched against an error, not against a subject index, so it
# must name what breaks. Both languages are accepted: the prose follows the user.
SYMPTOM = re.compile(
    r"error|fails?\b|failing|failed|breaks?\b|refuses?\b|times out|timeout|panics?\b"
    r"|ошибк|падает|сбой|не работает|не удаётся|ломает",
    re.IGNORECASE,
)
PROOF = re.compile(r"^#*\s*proof\b", re.IGNORECASE)


def _frontmatter(lines: list[str]) -> tuple[dict[str, str], int]:
    """Return the header fields and the index of the closing fence, ``({}, -1)`` if none."""
    if not lines or lines[0].strip() != "---":
        return {}, -1
    for index in range(1, len(lines)):
        if lines[index].strip() != "---":
            continue
        fields: dict[str, str] = {}
        for line in lines[1:index]:
            key, _, value = line.partition(":")
            value = value.strip().strip('"').strip("'")
            if key.strip() and value:
                fields[key.strip()] = value
        return fields, index
    return {}, -1


def skill_files(root: Path) -> list[Path]:
    """Every ``SKILL.md`` under the project's skills directory, sorted."""
    directory = root / SKILLS_DIR
    if not directory.is_dir():
        return []
    return sorted(directory.glob(f"*/{SKILL_FILE}"))


def check_skills(root: Path) -> list[str]:
    """Return the reasons the catalog would not be found or used, empty if it is fine."""
    root = root.resolve()
    files = skill_files(root)
    problems: list[str] = []

    if len(files) > MAX_SKILLS:
        problems.append(
            f"skills: {len(files)} skills, limit {MAX_SKILLS}; merge by symptom, "
            "every description rides in every request"
        )

    descriptions: dict[str, str] = {}
    for path in files:
        rel = path.relative_to(root).as_posix()
        slug = path.parent.name
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"skills: {rel}: cannot read: {exc}")
            continue

        fields, fence = _frontmatter(lines)
        if fence < 0:
            problems.append(f"skills: {rel}: missing --- frontmatter")
            continue

        name = fields.get("name", "")
        description = fields.get("description", "")
        if not SLUG.match(slug):
            problems.append(f"skills: {rel}: directory must be a lowercase slug")
        if not name:
            problems.append(f"skills: {rel}: frontmatter needs name")
        elif name != slug:
            problems.append(f"skills: {rel}: name {name!r} must match the directory {slug!r}")
        if not description:
            problems.append(f"skills: {rel}: frontmatter needs description")
        else:
            if len(description) < MIN_DESCRIPTION:
                problems.append(
                    f"skills: {rel}: description is {len(description)} chars, "
                    f"at least {MIN_DESCRIPTION} so the catalog says what breaks"
                )
            if len(description) > MAX_DESCRIPTION:
                problems.append(
                    f"skills: {rel}: description is {len(description)} chars, at most {MAX_DESCRIPTION}"
                )
            if not SYMPTOM.search(description):
                problems.append(
                    f"skills: {rel}: description must name the symptom, not the topic"
                )
            key = description.casefold()
            if key in descriptions:
                problems.append(f"skills: {rel}: description duplicates {descriptions[key]}")
            descriptions[key] = rel

        if len(lines) > MAX_LINES:
            problems.append(f"skills: {rel}: {len(lines)} lines, limit {MAX_LINES}")

        body = lines[fence + 1 :]
        if not any(PROOF.match(line.strip()) for line in body):
            problems.append(f"skills: {rel}: no Proof line (session id and observed result)")

    return problems
