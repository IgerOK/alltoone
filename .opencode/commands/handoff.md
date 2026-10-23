---
description: Save session state and build one handoff file for a new chat
---

Prepare a clean handoff so a fresh chat can continue without the history.

1. Rewrite `.opencode/handoff/STATE.md` using the schema from `AGENTS.md`
   (Goal, Done, Next, Decisions, Open questions, Files, Commands). Under 60
   lines, paths verbatim, no secrets.
2. Rewrite `.opencode/handoff/files.txt` with only the files the next session
   needs to read, one relative path per line. Prefer 5–15 files.
3. Run `python -m alltoone handoff --check` and fix every problem it reports.
4. Run `python -m alltoone handoff` and report the output path and size.
5. Tell the user to open a new chat and run `/resume`.

Do not paste the pack into the chat. The file is the deliverable.
