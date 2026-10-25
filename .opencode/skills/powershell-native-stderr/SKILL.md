---
name: powershell-native-stderr
description: `shell` fails with `NativeCommandError` and exit 1 on a successful command — `2>&1` piped into a cmdlet turns native stderr into an error
---

# `2>&1 | <cmdlet>` reports NativeCommandError although the command succeeded

## Cause
When a native command's stderr is merged with `2>&1` into a PowerShell pipeline,
PowerShell wraps each stderr line in an ErrorRecord. The pipeline then fails as
`NativeCommandError`, the harness sees exit code 1, and the real status of the
command is lost even though it exited 0.

## Recipe
Do not merge stderr into the pipeline. Let stderr through — the tool shows it
anyway — and read the real status from `$LASTEXITCODE`:

```powershell
python tests/run.py; "exit=$LASTEXITCODE"
```

To silence noisy stderr without changing the exit code:

```powershell
python tests/run.py 2>$null; "exit=$LASTEXITCODE"
```

## Verify
The call exits 0 and no `NativeCommandError` appears. To test the fix itself,
run a probe that writes to stderr and exits 0: plain and with `2>$null` it must
exit 0; with `2>&1 | Select-Object -First 1` it must fail with `NativeCommandError`.

## Proof
`ses_e8c1dbebbf` (2026-10-25) — `python -m unittest ... 2>&1 | Select-Object`
reported `NativeCommandError` and exit 1 while the suite printed `OK`;
`2>$null` returned exit 0 in the same session.
