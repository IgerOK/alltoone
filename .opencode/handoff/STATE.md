# State

## Goal
One file carries state, map and files, so a new chat continues without history; plus metering in OpenCode.

## Done
- `alltoone handoff` (`alltoone/handoff.py`): state + full tree + selected files in one Markdown file.
  `--check` validates schema, 60-line limit, every path and the skill catalog; exit 1, writes nothing.
- `unpack` detects a handoff pack without `--format` and parses only the body after `# Project context:`.
- `.opencode/plugins/context-budget/index.ts`: `[budget]` in the tail from provider usage, `compact` only at `critical` with `autoCompact`, transitions in `budget.log`.
- `autoResume` on: one pointer per session naming the pack size and asking for the rest of a truncated read; cache 93–99% after request 4 (live 2026-10-24).
- Skills are earned: `.opencode/skills/powershell-native-stderr/` (first, verified) + `skill-template.md`,
  `alltoone/skills.py` checks the format, `usage.py` lists repeated tool errors as candidates.
- `scripts/usage.py`: read-only report — cache, pack reads, unread files, tool errors, skills; 4 tests.
- `files.txt` trimmed to the 9 files the next step needs: `AGENTS.md` is injected every request,
  `README.md`/`cli.py`/`render.py`/`handoff.md` were never read. Pack 130 → 84 KB.
- Memory in git: `pack.md`/`budget.log` are ignored; `--check` warns above 8 decisions (soft). Ideas from `IgerOK/allToOne-setup`.
- Release prep: MIT `LICENSE`+RU, `scripts/install.ps1|sh`, CI ubuntu/windows 3.10/3.13, `.gitattributes`;
  77 tests + 28 node checks. v0.5.6 shipped 2026-10-25.
- Measured 2026-10-25: pack 86 KB, 92% cache, 2 reads; the three stragglers were one pre-trim session.

## Next
1. Start from the user's next request: 0.5.6 is shipped and the pack is current.
2. Next long session, `python scripts/usage.py`: cache ≥90%, no packed file unread; else trim `files.txt`.

## Decisions
- Meter outside the model: provider usage is truth, the model never counts.
- New session over compaction: tokens are free, compaction is lossy; run to the window edge, no handoff at 85%.
- The meter line goes to the tail: `event.system` broke the cache (2% vs 97%); `autoResume` ships on,
  and `new chat: /resume` rides in every line.
- `usageTotal` is `max(input, input+read+write)`; cache hit is `read / (input + read)` from
  `session_message.tokens`; thresholds 70/85/92.
- `files.txt` carries only what the next step needs; `AGENTS.md` is never packed — it is in every request.
  `cli.py`/`README.md`/`render.py` stay out: their reads were one pre-trim session, not a standing need.
- A skill is earned, not invented: a repeated failure whose error text did not contain the answer;
  the description names the symptom, 12 skills max, tests hold it. No automatic writer: `shell`
  failures carry no error status, so the trigger stays in-session (user, 2026-10-25).
- Working language: Russian; local git only, commit each change, no remote until asked; `STATE.md` and
  `files.txt` are tracked, `pack.md`/`budget.log` are derived and ignored.
- Plugin: options need the `plugins` entry (one instance); logic is tested through node, no TS toolchain, zero runtime deps.

## Open questions
- Parked, do not raise: `agnes-3.0-flash` — the user drops it until they can use it (2026-10-25).
- Does `input` exclude cached tokens for every provider? Here yes; `max()` is a safe guess, not a rule.
- Plugin API is V2; a desktop update may break `session.hook("context")`.

## Files
- `.opencode/plugins/context-budget/index.ts`: meter, guard, resume pointer.
- `.opencode/skills/`: the earned catalog + `skill-template.md`; `alltoone/skills.py`: its checks.
- `tests/plugin_budget_test.mjs`: node harness; `tests/test_alltoone.py`: suite + wrapper.
- `alltoone/handoff.py`: builder, `--check`; `alltoone/unpack.py`: restore.
- `scripts/usage.py`: dev-only usage report; reads `opencode.db`, never writes it.
- `pyproject.toml`, `alltoone/__init__.py`: version (0.5.6); `opencode.jsonc`: plugin options.
- `AGENTS.md`: the working agreement; `README.md`: user docs; `scripts/install.ps1|sh`: install.

## Commands
- `python -m unittest discover -s tests`: 77 tests; `node tests/plugin_budget_test.mjs`: 28 checks.
- `python scripts/usage.py`: cache, pack reads, unused packed files, tool errors, skills.
- `python -m alltoone handoff --check` / `python -m alltoone handoff`.
- `git add .opencode/handoff`: memory is tracked, so it is committed with the change.
