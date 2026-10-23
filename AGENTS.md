# alltoone — working agreement

This repository is the `alltoone` packer. It is also the reference project for
the handoff workflow below: the same rules apply to any project that copies
`.opencode/` and `AGENTS.md` from here.

## Context budget

Every request carries a line injected by `.opencode/plugins/context-budget`:

```text
[budget] 118k/200k used (59%) · left 82k · reserve 36k · mode=normal
```

Treat it as the only source of truth about the window. Do not estimate the
remaining context yourself and do not argue with the number.

| mode | what to do |
| --- | --- |
| `normal` | work normally |
| `checkpoint` | update `.opencode/handoff/STATE.md` before starting anything new |
| `handoff` | stop new work, finish `STATE.md`, refresh `files.txt`, run `python -m alltoone handoff --check` until it passes, run `python -m alltoone handoff`, ask the user to open a new chat with `/resume` |
| `critical` | no more tool calls except writing `STATE.md`; stop and ask for a new chat |

The directive is repeated only when the mode changes. Do not wait for the user
to remind you: the number is already in the prompt.

At `critical` the plugin can also request compaction on its own
(`ctx.session.compact`). That is opt-in via `autoCompact` in `opencode.jsonc`
and off by default: compaction is lossy, a new chat with `/resume` is not.

`autoResume` (also off by default) appends one pointer to
`.opencode/handoff/pack.md` on the first request of a session, so a new chat
finds the pack without the user typing `/resume`. It is a pointer, not a read:
the pack is ~30k tokens and only a continuing session should pay for it.

## Handoff quality

A new chat starts from `.opencode/handoff/pack.md` — one file with the state
first and the packed project context after it. The quality of the next session
depends entirely on what goes into `STATE.md`, so it is not a summary of the
conversation. It is a briefing:

```md
# State

## Goal
One paragraph: what we are building and why.

## Done
- Finished items, with paths.

## Next
1. The exact next action, specific enough to start without reading history.
2. ...

## Decisions
- Decision — reason. (Things that must not be re-litigated.)

## Open questions
- What is still unknown and who can answer it.

## Files
- `path` — why it matters.

## Commands
- `python -m unittest discover -s tests` — how to verify the work.
```

Rules:

- Keep it under 60 lines. Long state is not read; specific state is.
- Paths and identifiers verbatim. No "the config file" — write `alltoone/cli.py`.
- Record decisions and their reasons. A new chat cannot recover them from code.
- Never put secrets, tokens, or personal data in `STATE.md` or `pack.md`.
- `files.txt` lists only what is needed to continue, one relative path per line.
  The pack still contains the full project tree, so missing files can be asked
  for by name.

## Reading economically

- Start from the tree, not from the repository: `python -m alltoone . --tree-only`.
- Read the files the task actually touches. Do not bulk-read directories.
- For an isolated question, use a subagent: it gets a clean window and returns
  only the answer, keeping the parent context small.
- Never paste large tool output back into the conversation; write it to a file
  and reference the path.

## Language

Answer in the language of the user's latest message. `/resume` and `/handoff`
carry no language of their own: continue in the language recorded in the
state's Decisions (for example `Working language: Russian`), and switch only
when the user does.

## Verification

- `python -m unittest discover -s tests` must stay green.
- `python -m alltoone handoff --check` must pass before a handoff.
- New behaviour needs a test in `tests/test_alltoone.py`.
- The package has no runtime dependencies; keep it that way.
