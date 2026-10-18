# Throttle setup

The throttle is what makes an unattended multi-hour review safe to run.

**The entire contract is the exit code: 0 = CONTINUE, any non-zero = PAUSE.**
The orchestrator must only ever read the exit code, so the gate's internals can
be replaced without touching the review process.

## throttle.bat

Lives in the review directory. Manual override first, then delegation to
whatever usage measurement exists:

```bat
@echo off
REM Code review throttle control.
REM Contract: exit code 0 = CONTINUE, non-zero = PAUSE.
REM 1. Manual override: pauses while PAUSE.flag exists next to this script.
REM 2. Usage gate: delegates to the usage-check script.

if exist "%~dp0PAUSE.flag" (
    echo PAUSED ^(manual PAUSE.flag^)
    exit /b 1
)
call "%~dp0slack-check.bat" %*
exit /b %ERRORLEVEL%
```

The `call` line depends on the usage gate chosen in step 0:

- **Slack checker**: as shown. `slack-check.bat` is copied from this skill's
  `scripts/` into the review directory, next to `throttle.bat`, so the throttle
  keeps working when the plugin updates mid-run.
- **Another usage-check script**: `call "<its absolute path>" %*`.
- **None**: drop the `call` line and end with `exit /b 0`. Tell the user the
  sweep will pause only on `PAUSE.flag`.

## The slack checker

`slack-check.bat` asks the usage trayapp listening on `127.0.0.1:27812`
(`CC_SLACK_PORT` overrides the port) whether there is room to run low-priority
work, and exits:

| Exit | Meaning | For the review |
|---|---|---|
| `0` | release recommended | continue |
| `1` | a usage gate failed | pause; clears on its own as usage windows roll over |
| `2` | trayapp unreachable (two attempts failed) | pause; **never clears on its own** — the watcher gives up after ~15 min |
| `3` | bad usage (unknown option) | a wiring bug in `throttle.bat`; fix it, don't wait on it |

`-q` suppresses output, `-j` prints the raw JSON. Without either, it prints
session, weekly, and per-model usage, then `RELEASE: yes` or
`RELEASE: no (failed: …)`.

Each probe makes up to two attempts, 3 seconds apart, each waiting 10 seconds
(`CC_SLACK_TIMEOUT` overrides it). A trayapp that is up but slow for a moment
therefore doesn't read as unreachable, which would otherwise cost the review a
whole watcher interval. A trayapp that isn't running at all takes about 8
seconds to report exit `2`.

Every failed attempt is appended to `slack-check.log` beside the script, in
the review directory: the time, the attempt, how long it took, and the error.
"Unable to connect" means nothing is listening; "timed out" means the trayapp
is listening but didn't answer in time. The trayapp logs any request taking a
second or more as `slow request`, so a timeout here can be matched against its
`trayapp.log`. A timeout with no matching record means the time went before
the trayapp's handler ran. If the review directory is a git repo, the log
shows up as an untracked file there.

**The startup test (step 0)** runs it once with no flags and shows the output.
Exit `0` or `1` means it works: `1` is a real answer, and the sweep simply waits
until usage clears. Exit `2` means the trayapp isn't running or is on another
port, and the sweep must not start on it, because it would pause at once and
give up. Ask the user to start the trayapp, or to go ahead with no usage gate.

## Testing throttle.bat

**Test it in both states before using it** — create the flag, confirm non-zero;
remove it, confirm delegation (the slack checker's own exit code comes back
through) — and show the results. Note that `echo. > file`
is cmd syntax; from PowerShell use `New-Item`.

## Manual control

Works regardless of the usage gate:

```powershell
New-Item C:\path\to\code-review\PAUSE.flag
```

```powershell
Remove-Item C:\path\to\code-review\PAUSE.flag
```

## Watcher loop

Backgrounded by the orchestrator while paused:

```powershell
$deadline = (Get-Date).AddHours(6); $unreach = 0
while ($true) {
  & "C:\path\to\code-review\throttle.bat" -q *> $null
  $code = $LASTEXITCODE
  $ts = (Get-Date).ToString('HH:mm:ss')
  if ($code -eq 0) { Write-Output "$ts RELEASED - resume review"; exit 0 }
  if ($code -eq 2) { $unreach++ } else { $unreach = 0 }
  if ($unreach -ge 6) { Write-Output "$ts usage service unreachable for 15+ min"; exit 2 }
  if ((Get-Date) -ge $deadline) { Write-Output "$ts still paused after 6h"; exit 1 }
  Write-Output "$ts paused (exit $code), rechecking in 3 min"
  Start-Sleep -Seconds 180
}
```

Six unreachable checks in a row, 3 minutes apart, span the 15 minutes the
message reports; five would span only 12.

## Why this shape

- **Exit code as the only contract.** The usage script's internals changed
  meaning mid-run (session windows reset, a trayapp restarted) without the
  orchestrator noticing or caring.
- **Manual override first.** A flag file beats any usage heuristic when you want
  the review stopped *now*.
- **Bounded waiting.** An unreachable usage service never recovers on its own.
  Reporting beats polling into the void — and because all state is on disk,
  "stop and report" costs nothing.
- **Pass `%*` through.** Lets the orchestrator use the usage script's quiet mode
  for watcher polling without a second wrapper.

## Do not use the usage script's blocking wait mode

Not through this wrapper. The manual `PAUSE.flag` check returns immediately
regardless of a `-w` flag, which would busy-loop during a manual pause.
