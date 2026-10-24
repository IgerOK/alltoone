---
description: Restore work from the handoff pack in a new chat
---

Read `.opencode/handoff/pack.md`. It contains the previous session's state
first, then the project tree and the packed files.

The pack is ~130 KB, which does not fit a single read: if the output is
truncated, continue with `offset` until the end of the file before starting
work. A half-read pack is worse than none.

Then:

1. Restate the goal, what is done, and the next step in at most 10 lines.
2. List any file you still need that is not in the pack.
3. Continue with the next step.

Continue in the working language recorded in the state's Decisions unless the
user switches.

Do not re-read files that are already in the pack, and do not ask the user to
repeat decisions that are written in the state section.
