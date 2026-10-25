# State

## Goal
One file carries state, map and files, so a new chat continues without history; plus metering in OpenCode.

## Done
- `alltoone handoff` (`alltoone/handoff.py`): state + full tree + selected files in one Markdown file.
  `--check` validates schema, 60-line limit and every path; exit 1, writes nothing.
- `unpack` detects a handoff pack without `--format` and parses only the body after `# Project context:`.
- `.opencode/plugins/context-budget/index.ts`: `[budget]` from provider usage in the message tail,
  `compact` at `critical` only with `autoCompact`, transitions in `budget.log`.
- `autoResume` on in `opencode.jsonc`: one pointer per session, naming the pack size and asking for
  the rest of a truncated read (2 node checks). Live check 2026-10-24: it fired once and led to the
  pack; the tail keeps 93–99% cache from request 4 (request 2 = 25%: the pack is new content).
- `scripts/usage.py`: read-only report from `opencode.db` — cache per session and model, pack
  reads, files never opened; 3 tests, a schema change fails softly.
- `files.txt` trimmed to the 9 files the next step needs: `AGENTS.md` is injected every request,
  `README.md`/`cli.py`/`render.py`/`handoff.md` were never read. Pack 130 → 84 KB.
- Memory is tracked: `.gitignore` ignores only `pack.md` and `budget.log`; `--check` warns above
  8 decisions (soft, exit 0). 2 tests. Ideas taken from `IgerOK/allToOne-setup`.
- Release prep: MIT `LICENSE` + RU translation, `scripts/install.ps1|.sh`, CI ubuntu/windows
  3.10/3.13, `.gitattributes`; 66 tests + 28 node checks. v0.5.6 shipped 2026-10-25.
- Measured after the trim 2026-10-25: pack 86 KB, 2 reads, 92% cache in the resume session.
  `cli.py`/`README.md`/`render.py` were read only in one pre-trim session (10-23); nothing since.

## Next
1. `agnes-3.0-flash` is unavailable now (user, 2026-10-24); revisit when it appears — tool calls first.

## Decisions
- Meter outside the model: provider usage is truth, the model never counts.
- New session over compaction: tokens are free, compaction is lossy; run to the window edge, no handoff at 85%.
- The meter line goes to the tail: `event.system` broke the cache (2% vs 97%); `autoResume` ships on,
  and `new chat: /resume` rides in every line.
- `usageTotal` is `max(input, input+read+write)`; cache hit is `read / (input + read)` from
  `session_message.tokens`; thresholds 70/85/92.
- `files.txt` carries only what the next step needs; `AGENTS.md` is never packed — it is in every request.
  `cli.py`/`README.md`/`render.py` stay out: their reads were one pre-trim session, not a standing need.
- Memory in git: `STATE.md` and `files.txt` are tracked, `pack.md` and `budget.log` are derived and ignored.
- Working language: Russian; local git only — commit each change, no remote until asked.
- Plugin: options need the `plugins` entry (one instance); logic is tested through node, no TS toolchain, zero runtime deps.

## Open questions
- Does `input` exclude cached tokens for every provider? Here yes; `max()` is a safe guess, not a rule.
- Plugin API is V2; a desktop update may break `session.hook("context")`.

## Files
- `.opencode/plugins/context-budget/index.ts`: meter, guard, resume pointer.
- `tests/plugin_budget_test.mjs`: node harness; `tests/test_alltoone.py`: suite + wrapper.
- `alltoone/handoff.py`: builder, `--check`; `alltoone/unpack.py`: restore.
- `scripts/usage.py`: dev-only usage report; reads `opencode.db`, never writes it.
- `pyproject.toml`, `alltoone/__init__.py`: version (0.5.6); `opencode.jsonc`: plugin options.
- `AGENTS.md`: the working agreement; `README.md`: user docs; `scripts/install.ps1|sh`: install.

## Commands
- `python -m unittest discover -s tests`: 66 tests; `node tests/plugin_budget_test.mjs`: 28 checks.
- `python scripts/usage.py`: cache, pack reads, unused packed files.
- `python -m alltoone handoff --check` / `python -m alltoone handoff`.
- `git add .opencode/handoff`: memory is tracked, so it is committed with the change.
