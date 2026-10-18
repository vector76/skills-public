@echo off
setlocal
rem ---------------------------------------------------------------------------
rem slack-check.bat -- query the locally running trayapp for available usage.
rem
rem Prints session / weekly / fable burn-down and the release decision, then
rem exits with:
rem     0  release_recommended = true   (safe to run low-priority work)
rem     1  release_recommended = false  (a gate failed, or -w timed out)
rem     2  could not reach the trayapp  (two attempts failed; in -w mode,
rem                                      the last poll's two attempts)
rem     3  bad usage
rem
rem Usage:
rem     slack-check.bat                human-readable summary, single probe
rem     slack-check.bat -q             no output, exit code only
rem     slack-check.bat -j             raw JSON from GET /slack
rem     slack-check.bat -w             block until release, up to 360 minutes
rem     slack-check.bat -w 30 -q       block up to 30 minutes, silently
rem
rem Environment:
rem     CC_SLACK_PORT       trayapp port          (default 27812)
rem     CC_SLACK_INTERVAL   -w poll seconds       (default 300)
rem     CC_SLACK_TIMEOUT    seconds per attempt   (default 10)
rem
rem Each probe makes up to two attempts, 3 s apart. Every failed attempt is
rem appended to slack-check.log beside this script.
rem ---------------------------------------------------------------------------

rem Captured before arg parsing: shift renumbers %0 as well, so %~f0 stops
rem resolving to this file once any option has been consumed. Quoted so a
rem path holding & or ) still sets it.
set "CC_SLACK_SELF=%~f0"

set CC_SLACK_MODE=text
set CC_SLACK_WAIT_MIN=

:parseargs
if "%~1"=="" goto doneargs
if /i "%~1"=="-q" set CC_SLACK_MODE=quiet& shift& goto parseargs
if /i "%~1"=="-j" set CC_SLACK_MODE=json& shift& goto parseargs
if /i "%~1"=="-w" goto optwait
if /i "%~1"=="-h" goto usage
if /i "%~1"=="/?" goto usage
echo unknown option: %~1
goto usage

rem -w takes an optional minute count. Parsed on its own label rather than
rem inside an if-block because cmd substitutes every %1 in a block at parse
rem time, so a shift and a %1 read cannot share one block.
:optwait
set CC_SLACK_WAIT_MIN=360
shift
if "%~1"=="" goto doneargs
echo %~1| findstr /r "^[1-9][0-9]*$" >nul 2>&1
if errorlevel 1 goto parseargs
set CC_SLACK_WAIT_MIN=%~1
shift
goto parseargs

:usage
echo usage: slack-check.bat [-q^|-j] [-w [minutes]]
exit /b 3

:doneargs
if "%CC_SLACK_PORT%"=="" set CC_SLACK_PORT=27812

rem The PowerShell body lives past the exit below, after the #@POWERSHELL@
rem marker. cmd parses line by line as it executes and never reaches it, so
rem that body needs no batch escaping -- which is the whole point: the wait
rem loop is unreadable as a chain of caret-continued -Command fragments.
rem The path reaches PowerShell through the environment, not pasted into the
rem command: a path holding ' broke the parse, and a parse error exits 1,
rem which reads as "usage gate failed" and pauses a review indefinitely.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:CC_SLACK_SELF); Invoke-Expression $s.Substring($s.LastIndexOf([char]35+'@POWERSHELL@')+14)"
exit /b %ERRORLEVEL%

#@POWERSHELL@
$ErrorActionPreference = 'Stop'

$port     = if ($env:CC_SLACK_PORT)     { $env:CC_SLACK_PORT }            else { '27812' }
$mode     = if ($env:CC_SLACK_MODE)     { $env:CC_SLACK_MODE }            else { 'text' }
$waitMin  = if ($env:CC_SLACK_WAIT_MIN) { [int]$env:CC_SLACK_WAIT_MIN }   else { 0 }
$interval = if ($env:CC_SLACK_INTERVAL) { [int]$env:CC_SLACK_INTERVAL }   else { 300 }
$timeout  = if ($env:CC_SLACK_TIMEOUT)  { [int]$env:CC_SLACK_TIMEOUT }    else { 10 }
$url      = "http://127.0.0.1:$port/slack"
$logPath  = Join-Path (Split-Path -Parent $env:CC_SLACK_SELF) 'slack-check.log'

# Returns the parsed /slack response, or $null if the trayapp did not answer.
# Unreachable is a distinct outcome from "answered no" and the caller must
# keep them apart: a gate failure resolves on its own, a dead trayapp never
# does.
#
# A failed attempt is retried once. The trayapp has been seen to miss a 5 s
# deadline for a moment while staying up, and one miss is expensive: in -w
# mode, or under a caller's watcher, it costs a whole poll interval.
function Get-Slack {
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        $start = Get-Date
        try { return Invoke-RestMethod -Uri $url -TimeoutSec $timeout }
        catch {
            Write-AttemptFailure $attempt $start $_
            if ($attempt -lt 2) { Start-Sleep -Seconds 3 }
        }
    }
    $null
}

# Appends one line per failed attempt to slack-check.log. The trayapp logs
# any request that takes a second or more as "slow request"; a timestamp here
# with no matching record there means the time went before the trayapp's
# handler ran. Logging must never change the exit code, so a write failure
# is ignored.
function Write-AttemptFailure($attempt, $start, $err) {
    $ms = [int]((Get-Date) - $start).TotalMilliseconds
    $line = '{0}  attempt {1} failed after {2} ms: {3}' -f `
        $start.ToString('yyyy-MM-ddTHH:mm:ss.fffzzz'), $attempt, $ms, $err.Exception.Message
    try { Add-Content -LiteralPath $logPath -Value $line } catch { }
}

function Format-Row($w, $name) {
    if ($null -eq $w) { return ('{0,-8} (no active window)' -f $name) }
    $used = if ($null -ne $w.percent_used) { '{0,5:N1}%' -f $w.percent_used }         else { '  n/a' }
    $rem  = if ($null -ne $w.percent_used) { '{0,5:N1}%' -f (100 - $w.percent_used) } else { '  n/a' }
    $slk  = if ($null -ne $w.slack_fraction) { '{0,6:N3}' -f $w.slack_fraction }      else { '   n/a' }
    $exp  = '{0,5:N1}%' -f $w.percent_expected
    $ends = ([datetime]$w.window_end).ToLocalTime().ToString('ddd HH:mm')
    '{0,-8} used {1}  left {2}  pace {3}  slack {4}  resets {5}' -f $name, $used, $rem, $exp, $slk, $ends
}

function Get-FailedGates($r) {
    @($r.gates.PSObject.Properties | Where-Object { -not $_.Value } | ForEach-Object { $_.Name })
}

function Write-Result($r) {
    if ($mode -eq 'quiet') { return }
    if ($mode -eq 'json')  { $r | ConvertTo-Json -Depth 6; return }
    Write-Host (Format-Row $r.session 'session')
    Write-Host (Format-Row $r.weekly  'weekly')
    if ($null -ne $r.fable_weekly) { Write-Host (Format-Row $r.fable_weekly 'fable') }
    $c = if ($null -ne $r.slack_combined_fraction) { '{0:N3}' -f $r.slack_combined_fraction } else { 'n/a' }
    Write-Host ('combined slack {0}   paused {1}' -f $c, $r.paused)
    if ($r.release_recommended) {
        Write-Host 'RELEASE: yes' -ForegroundColor Green
    } else {
        Write-Host ('RELEASE: no  (failed: {0})' -f ((Get-FailedGates $r) -join ', ')) -ForegroundColor Yellow
    }
}

function Exit-Code($r) { [int](-not $r.release_recommended) }

# ---- single probe -----------------------------------------------------------
if ($waitMin -le 0) {
    $r = Get-Slack
    if ($null -eq $r) {
        if ($mode -ne 'quiet') { Write-Host "trayapp unreachable at $url" -ForegroundColor Red }
        exit 2
    }
    Write-Result $r
    exit (Exit-Code $r)
}

# ---- wait mode --------------------------------------------------------------
# Bounded on purpose. Some "no" answers never resolve on their own -- a closed
# browser tab fails the baseline-freshness gate forever -- so an unbounded
# block would hang the calling script rather than let it fail loudly.
$deadline = (Get-Date).AddMinutes($waitMin)
$unreachable = $false

while ($true) {
    $r = Get-Slack
    if ($null -ne $r) {
        $unreachable = $false
        if ($r.release_recommended) { Write-Result $r; exit 0 }
    } else {
        $unreachable = $true
    }

    $now = Get-Date
    if ($now -ge $deadline) { break }

    if ($mode -ne 'quiet') {
        $why = if ($unreachable) { "unreachable at $url" } else { 'failed: ' + ((Get-FailedGates $r) -join ', ') }
        $left = $deadline - $now
        Write-Host ('{0}  waiting -- {1}  ({2}:{3:00} left)' -f `
            $now.ToString('HH:mm:ss'), $why, [Math]::Floor($left.TotalMinutes), $left.Seconds)
    }

    # Never sleep past the deadline: a 300s interval with 20s remaining should
    # make one last poll at the deadline, not overshoot by four minutes.
    $sleep = [Math]::Min($interval, [int]($deadline - $now).TotalSeconds + 1)
    Start-Sleep -Seconds ([Math]::Max(1, $sleep))
}

if ($mode -ne 'quiet') {
    Write-Host ('timed out after {0} min without a release' -f $waitMin) -ForegroundColor Yellow
}
exit $(if ($unreachable) { 2 } else { 1 })
