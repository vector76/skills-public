# Install or update the Claude Usage Dashboard

A runbook: point a Claude Code session at this file to install or update the
dashboard. Follow the steps in order, and stop at every `[ STOP / ASK ]` until
the user answers.

The dashboard is `trayapp.exe` from `github.com/vector76/cc_usage_dashboard`:
one Go binary that tails `~/.claude/projects/` JSONL files, keeps a SQLite
database, and serves a dashboard at `http://localhost:27812`. This runbook
installs or updates it with `go install`, registers a per-user logon task, and
sets up where quota figures come from. It needs the Claude Code CLI, and step
0 checks for it before anything else. It does not install the browser
userscript, which it recommends against, or the container CLI; if the user
wants them, point them to the project's README.

Run the commands with the PowerShell tool. None of them need admin, except
installing Go with its standard installer, and step 2 offers a way around
that.

**Done** means: `trayapp.exe` in `%USERPROFILE%\go\bin` is current, the
scheduled task `ClaudeUsageDashboard` launches it (unless the user declined),
`GET http://localhost:27812/healthz` succeeds, and the user has chosen a quota
source. Never change anything beyond that: no edits to `~/.claude` settings,
no config changes other than the `oauth_usage` block in step 6, and no
commits.

## 0. Check the Claude Code CLI   [ STOP / ASK ]

**Windows only.** The tray UI and the logon task are Windows features. On any
other platform, stop and say so.

The recommended quota source (step 6) reads the sign-in stored by Claude
Code's command-line tool. When that sign-in goes stale, the trayapp refreshes
it by running the tool (`claude -p /usage`). The Claude desktop app doesn't
provide the `claude` command, so check for it before changing anything,
rather than finding it missing halfway through:

```powershell
$claude = (Get-Command claude -ErrorAction SilentlyContinue).Source
if (-not $claude -and (Test-Path "$env:USERPROFILE\.local\bin\claude.exe")) { $claude = "$env:USERPROFILE\.local\bin\claude.exe" }
if ($claude) {
  "claude: $claude  ($(& $claude --version))"
  & $claude auth status | ConvertFrom-Json | Select-Object loggedIn, authMethod, orgName | Format-List
} else { 'claude: not found' }
# Whether this session's writes to AppData are redirected (see below).
$probe = "usage-dashboard-probe-$(Get-Random)"
New-Item -ItemType Directory "$env:LOCALAPPDATA\$probe" | Out-Null
$redirected = [bool](Get-ChildItem "$env:LOCALAPPDATA\Packages\*\LocalCache\Local\$probe" -Directory -ErrorAction SilentlyContinue)
Remove-Item "$env:LOCALAPPDATA\$probe"
"redirected: $redirected"
```

Keep `$redirected`: steps 2, 4 and 6 use it. It is `True` when the session
runs inside an app that Windows gives private copies of AppData and the user
registry, such as the Claude desktop app. A new folder this session creates
at the top of `%LOCALAPPDATA%` or `%APPDATA%` then lands in that app's
private storage, and other programs never see it. The same goes for anything
a program started from this session creates there. Most of this runbook
isn't affected, and the steps that are say what to do instead.

- **Found and `loggedIn: True`:** keep the path; step 6 uses it as
  `claude_path`. Continue.
- **Not found, or not signed in:** stop and tell the user. Recommend
  installing and signing in first with the `INSTALL_CLAUDE_CLI.md` runbook,
  then coming back here and starting again at this step. Ask which they want:
  - **Set up the CLI first** (recommended): download
    `https://raw.githubusercontent.com/vector76/skills-public/main/oneshot/INSTALL_CLAUDE_CLI.md`
    verbatim, tell the user what it will change on this machine, and wait for
    their go-ahead before following it. Then rerun this check.
  - **Continue without it:** the token refresh won't be available. Polling
    works only while a Claude Code sign-in is stored on this machine, so the
    quota figures may pause or never start. Carry that into step 6.

## 1. Detect what is there   [ STOP / ASK ]

```powershell
$goExe = "$env:USERPROFILE\go\bin\trayapp.exe"
"go-bin exe:  $(Test-Path $goExe)"
"running exe: $((Get-Process trayapp -ErrorAction SilentlyContinue).Path)"
"task exe:    $((Get-ScheduledTask -TaskName ClaudeUsageDashboard -ErrorAction SilentlyContinue).Actions.Execute)"
try { "healthz:     $((Invoke-WebRequest http://localhost:27812/healthz -UseBasicParsing -TimeoutSec 3).StatusCode)" } catch { "healthz:     $($_.Exception.Message)" }
"app-only:    $((Get-ChildItem "$env:LOCALAPPDATA\Packages\*\LocalCache\*\usage_dashboard", "$env:LOCALAPPDATA\Packages\*\LocalCache\Local\Programs\go" -Directory -ErrorAction SilentlyContinue).FullName -join ', ')"
```

`app-only` lists copies of the dashboard's data folders, or of a Go from the
zip, that exist only in an app's private storage. An earlier run of this
runbook from the Claude desktop app could have left them. Programs outside
that app never see them. If any are listed, say so. The trayapp doesn't use
them, and this runbook doesn't touch them. They're the user's to inspect or
delete.

From the result, pick a **mode**, tell the user which one and why, and wait
for them to confirm it:

- **Install:** no exe in `go\bin`, nothing running, and no task. Run every
  step.
- **Update:** the exe in `go\bin` exists, and anything running or in the
  task points at that same path. Steps 3, 4, and 6 say what changes in this
  mode.
- **Installed elsewhere:** a running copy or the task points at a
  different `trayapp.exe`, for example one built from a checkout. This
  runbook updates only the `go install` copy. Offer two choices:
  - **Switch to the `go install` copy:** continue in **Install** mode. Step 4
    repoints the task, and step 3 stops the old copy. Warn first that a copy
    built from a checkout may have been using a `usage.db` in its own folder.
    The new copy uses `%LOCALAPPDATA%\usage_dashboard\usage.db`, so that
    history would appear missing until the database is moved or
    `database.path` is set. Check for a `usage.db` next to the old exe and
    say what you find.
  - **Leave it:** stop here. The user updates that copy their own way, such
    as `git pull` and a rebuild in the checkout.

## 2. Check Go   [ STOP / ASK ]

```powershell
"on PATH:    $(try { go version } catch { 'not found' })"
foreach ($g in "$env:ProgramFiles\Go\bin\go.exe", "$env:LOCALAPPDATA\Programs\go\bin\go.exe") {
  if (Test-Path $g) { "$g : $(& $g version)" }
}
```

The build needs **Go 1.26.2 or newer**. The build is pure Go, so no C
toolchain is needed. The second check finds a Go that is installed but not on
this session's `PATH`, such as one installed since the session started.

- If a new enough Go is found, continue. In the rest of this runbook, `go`
  means that executable. If it isn't the one on `PATH`, call it by its full
  path.
- If Go is missing or older, **halt**. Explain the two ways to install it
  below, ask which the user wants, and wait for the answer.

**The standard installer (needs admin).** `winget install GoLang.Go` runs the
official `.msi`, which installs for every user in `C:\Program Files\Go` and
upgrades an older Go there in place. Windows asks for administrator approval,
which the user must give at the desktop; this session can't. There is no
per-user version of this installer. Run it only on a clear yes. Afterwards,
check with `& "$env:ProgramFiles\Go\bin\go.exe" version`. If the user has no
admin rights, use the zip instead.

**The zip, for this user only (no admin).** Go also ships as a zip that runs
from any folder. This installs it to `%LOCALAPPDATA%\Programs\go`. Go finds its
own files from where it is unpacked, so nothing else needs configuring.

1. Look up the current release:

   ```powershell
   $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
   $rel  = (Invoke-RestMethod 'https://go.dev/dl/?mode=json') | Where-Object stable | Select-Object -First 1
   $zip  = $rel.files | Where-Object { $_.os -eq 'windows' -and $_.arch -eq $arch -and $_.kind -eq 'archive' }
   $zip | Format-List filename, size, sha256
   ```

2. Tell the user the file name, that it comes from `https://go.dev/dl/`, and
   its size in MB. If `%LOCALAPPDATA%\Programs\go` already exists (an older
   Go installed this way), say that it will be deleted and replaced: Go must
   not be unpacked over an older copy.

   **If step 0 found `redirected: True` and `%LOCALAPPDATA%\Programs` doesn't
   exist,** creating it from this session would put the whole Go install in
   the app's private storage, where terminals can't find it. Ask the user to
   create the folder first, in a PowerShell window of their own, with
   `New-Item -ItemType Directory "$env:LOCALAPPDATA\Programs"`. Continue once
   they have. What goes into a folder that already exists is written for
   real.

   Wait for a yes, then run this with
   `$name` and `$sha` filled in from the lookup's output, so what is installed is
   exactly the file the user agreed to:

   ```powershell
   $name = '<filename>'
   $sha  = '<sha256>'
   $dest = "$env:LOCALAPPDATA\Programs"
   $file = "$env:TEMP\$name"
   Invoke-WebRequest "https://go.dev/dl/$name" -OutFile $file -UseBasicParsing
   if ((Get-FileHash $file -Algorithm SHA256).Hash -ne $sha) { Remove-Item $file; throw 'checksum mismatch: download discarded' }
   New-Item -ItemType Directory -Force $dest | Out-Null
   if (Test-Path "$dest\go") { Remove-Item -Recurse -Force "$dest\go" }
   Expand-Archive $file -DestinationPath $dest
   Remove-Item $file
   & "$dest\go\bin\go.exe" version
   ```

   On a checksum mismatch, stop and report it. Don't retry with a different
   source.

3. Offer to add `%LOCALAPPDATA%\Programs\go\bin` to the user's `PATH`, so `go`
   works in new terminals. It's a lasting change, so ask separately. This
   runbook doesn't need it, because it calls Go by its full path. On a yes:

   ```powershell
   $bin = "$env:LOCALAPPDATA\Programs\go\bin"
   $key = Get-Item HKCU:\Environment
   $old = $key.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
   $entries = $old -split ';' | Where-Object { $_ }
   if ($entries | Where-Object { [Environment]::ExpandEnvironmentVariables($_).TrimEnd('\') -ieq $bin }) { 'already on PATH' }
   else {
     Set-ItemProperty HKCU:\Environment Path ((@($entries) + $bin) -join ';') -Type ExpandString
     # Setting and clearing a throwaway variable tells Windows the environment changed.
     [Environment]::SetEnvironmentVariable('GO_PATH_NOTIFY', '1', 'User')
     [Environment]::SetEnvironmentVariable('GO_PATH_NOTIFY', $null, 'User')
     'added'
   }
   ```

   The `PATH` is written through the registry rather than with
   `[Environment]::SetEnvironmentVariable`, which would store it as plain
   text. Entries such as `%USERPROFILE%\...` would then stop expanding.
   Existing entries are kept, unchanged and in order; only empty ones are
   dropped.

   Tell the user that terminals opened from now on see the change and ones
   already open don't. If Go is also installed for all users in
   `C:\Program Files\Go`, that copy comes first, because Windows puts the
   system `PATH` ahead of the user's.

   **If step 0 found `redirected: True`,** don't run the block. The session
   reads and writes its own copy of the user registry, so its "already on
   PATH" can be wrong, and the edit may never reach the real `PATH`. Give
   the user the block to run in a PowerShell window of their own, and have
   them check with `go version` in a new window afterwards.

A Go installed from the zip doesn't update itself. To upgrade it, repeat
these steps.

## 3. Install or update the binary   [ STOP / ASK ]

**Update mode:** first record the installed version. `go version -m` prints
the module line, a pseudo-version ending in the commit it was built from:

```powershell
go version -m "$env:USERPROFILE\go\bin\trayapp.exe" | Select-String '^\s+mod\s'
```

Tell the user what will run, and wait for a yes:

```powershell
go install -ldflags="-H=windowsgui" github.com/vector76/cc_usage_dashboard/cmd/trayapp@latest
```

`-H=windowsgui` is required. Without it the binary is a console app that holds
a terminal open, and `go install` cannot pick the flag up from the repo.

A running copy locks the file, so the overwrite fails. If a copy is running,
say so, and with the same yes stop it first with
`Stop-Process -Name trayapp -ErrorAction SilentlyContinue`. Stopping it loses
nothing: data is in the database, and ingest catches up on restart.

Confirm `%USERPROFILE%\go\bin\trayapp.exe` exists afterwards. If the build
fails, show the error and stop.

**Update mode:** run the `go version -m` line again and report the change
from the old version to the new one. If the version did not change, the user
already had the latest version, or the Go module proxy is still serving an
older cached `@latest`. The proxy can lag a push by several minutes. If the
user expected something newer, offer to rerun once with the proxy bypassed,
which fetches straight from GitHub and needs `git` on `PATH`: put
`$env:GOPROXY = 'direct'` before the `go install` line, in the same command.
Do not retry beyond that.

## 4. Register autostart   [ STOP / ASK ]

**Update mode:** if the task already exists and its exe is
`%USERPROFILE%\go\bin\trayapp.exe`, say so and skip to step 5. No gate is
needed.

Otherwise, explain that this adds a per-user Task Scheduler entry named
`ClaudeUsageDashboard` that starts the tray app at logon and replaces any
existing task of that name. Wait for a yes, then run:

```powershell
$exe = "$env:USERPROFILE\go\bin\trayapp.exe"
$user = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
$action    = New-ScheduledTaskAction -Execute $exe
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 0)
if (Get-ScheduledTask -TaskName ClaudeUsageDashboard -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName ClaudeUsageDashboard -Confirm:$false }
Register-ScheduledTask -TaskName ClaudeUsageDashboard -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Claude Usage Dashboard tray app (auto-start at logon).' | Out-Null
```

If the user declines, skip to step 5 and start the app with
`Start-Process $exe` instead of the task.

**If step 0 found `redirected: True`,** don't use `Start-Process` for the
trayapp here or in step 6. A program started from this session writes as the
session does: on a first run, the trayapp would create its config and
database folders in the app's private storage, so a later start at logon
would begin with neither. The task is safe, because Task Scheduler starts it
outside the session. Without the task, ask the user to start it themselves:
Win+R, then `%USERPROFILE%\go\bin\trayapp.exe`. Wait until they have, then
continue with the health check.

## 5. Start and verify

```powershell
Start-ScheduledTask -TaskName ClaudeUsageDashboard
```

Give it a few seconds, then check health:

```powershell
(Invoke-WebRequest http://localhost:27812/healthz -UseBasicParsing -TimeoutSec 5).StatusCode
```

If it does not respond, read the log and report what it says:

```powershell
Get-Content "$env:LOCALAPPDATA\usage_dashboard\trayapp.log" -Tail 30
```

A common cause is another process holding port 27812
(`Get-NetTCPConnection -LocalPort 27812`), such as an old copy from another
location that is still running.

## 6. Choose a quota source   [ STOP / ASK ]

**Update mode:** start by showing the current state, and only go on to the
choice below if the user wants to change it:

```powershell
Invoke-RestMethod http://localhost:27812/api/oauth/status -TimeoutSec 5
```

Report whether polling is on (`enabled`) and working, and whether the token
refresh is on (`refresh_enabled`), using the result list under **Confirm
polling works** below. If `refresh_enabled` is missing from the response, the
binary predates the option. If polling is on but the refresh is off, and
step 0 found the CLI, offer the token refresh on its own: the explanation
below, then the config edit. Also ask whether they have the userscript
running, since this runbook can't see that. If they do and polling works,
mention that polling alone covers the same figures. If they want no change,
go to step 7.

The trayapp gets token and cost figures from Claude Code's session files on
its own. The **quota** percentages (how much of the 5-hour and weekly
allowance is left) need a separate source. Explain the choices in plain
terms, recommend polling, then ask which the user wants and wait for the
answer:

- **Trayapp polls it itself (recommended).** The trayapp asks Anthropic's
  usage endpoint directly, using the sign-in Claude Code already stores. No
  browser is needed, and reading usage does not use any quota. The sign-in
  token is refreshed only when Claude Code runs, so on its own, polling pauses
  after a long idle stretch, such as overnight. The token refresh, asked
  about next, closes that gap.
- **Neither.** Usage and cost still work, but the quota charts and the slack
  signal have no figures.
- **Browser userscript (possible, but not recommended).** Mention it so the
  user knows it exists, and recommend against it. It needs a browser add-on
  (Tampermonkey or Violentmonkey) running a script on claude.ai while the user
  is signed in there. The usage page `https://claude.ai/settings/usage` has
  to stay open in a tab for figures to keep arriving. It reads the figures
  off that page, so a change to the page can quietly stop them. Polling
  covers the same figures without any of that. This runbook doesn't install
  it. If the user still wants it, point them to the "Userscript
  installation" section of the project README.

The userscript and polling can run at once; their figures are recorded
separately rather than merged. If the user wants both, do the polling setup
below and point them to the README.

**If the user wants polling, set up the token refresh with it.** It's
recommended, and it's how the trayapp keeps polling going when Claude Code
hasn't run for a while. If step 0 continued without the CLI, the refresh
can't work: say so, leave it off, and skip to the config edit. Otherwise,
explain it, recommend leaving it on, and ask:

- When the trayapp finds the sign-in token expired, it runs
  `claude -p /usage` once, in the background with no window. Starting
  Claude Code refreshes the token, and polling resumes without waiting for
  the user's next session. Claude Code does the refresh itself, so the
  trayapp never handles the refresh token.
- It makes **one attempt each time the token goes stale**. If that attempt
  does not help (Claude Code missing, signed out, or the token not rotated),
  it does not try again until a poll succeeds some other way. Network or
  server errors never trigger it. Each attempt is stopped after two minutes.
- It needs the `claude` command installed and signed in on this machine.
- It is a real Claude Code run. The trayapp's notes say it was once seen to
  open a 5-hour window when none was active, and later seemed not to. That
  depends on Claude Code and may change, so tell the user it might start a
  5-hour window overnight. The run also leaves a small transcript under the
  trayapp's own data folder.
- Off, the only cost is the overnight gap described above.

With the refresh on, set `claude_path` (below) to the path step 0 found. The
trayapp runs from Task Scheduler at logon, where `PATH` can differ from this
shell's, so a pinned path is more reliable than relying on `PATH`.

**If the user wants polling or the refresh changed:** edit
`%APPDATA%\usage_dashboard\config.yaml`, which the trayapp creates on its
first run. Read it first, then change only the `oauth_usage` block:

- If an uncommented `oauth_usage:` block exists, set `enabled`, and, with
  polling on, `refresh_with_claude` and `claude_path`, in it. Add a key if it
  is missing.
- Otherwise, to turn polling on, replace the whole commented-out
  `# oauth_usage:` block with:

  ```yaml
  oauth_usage:
    enabled: true
    refresh_with_claude: true
    claude_path: "C:\\path\\to\\claude.exe"
  ```

  Leave out the last two lines if the user declined the refresh. The path is
  in double quotes, so every backslash is doubled.

Leave every other line unchanged. Show the user the diff. The file is read
only at startup, so restart the app:

```powershell
Stop-Process -Name trayapp -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName ClaudeUsageDashboard
```

If the user declined autostart in step 4, use
`Start-Process "$env:USERPROFILE\go\bin\trayapp.exe"` instead, or, if the
session is redirected, ask the user to start it as in step 4. Check
`/healthz` again as in step 5. If the app does not come back, read the last
lines of `trayapp.log`; a config error there usually names the bad line.

**Confirm polling works.** The poller makes its first request as soon as the
app starts, so wait a few seconds and ask it for its status:

```powershell
Invoke-RestMethod http://localhost:27812/api/oauth/status -TimeoutSec 5
```

Report the result from these fields:

- **`enabled: true`, `available: true`, `last_success` set:** polling works.
- **`enabled: false`:** polling is off. If the user just turned it on, the
  app did not pick up the change. Check that the edit is under a top-level,
  uncommented `oauth_usage:` key, and that the app was actually restarted.
- **`credential_stale: true`:** the stored Claude Code sign-in token has
  expired. This fixes itself: it refreshes the next time Claude Code runs, and
  polling resumes on its own. Tell the user this is normal, not a failure.
  With the refresh on, the trayapp has also tried once itself; see the
  refresh fields.
- **`refresh_enabled`:** `true` confirms the refresh is on. If the user
  turned it on and it reads `false`, the key is not inside the uncommented
  `oauth_usage:` block, or polling itself is off. If the field is missing,
  the binary predates the option; handle it like a 404.
- **`refresh_armed: false` while `refresh_enabled: true`:** an attempt was
  made and did not help, and no other will be made until a poll succeeds.
  Show `last_refresh_error`, which usually says Claude Code was not found or
  is signed out. `claude_path` pointing at the wrong file is a common cause.
  `refresh_armed: true` with `last_refresh_attempt: null` is the normal state
  before any token has gone stale.
- **`available: false` without `credential_stale`:** something is wrong.
  Show `last_error`, and the log's `oauth usage poll failed` lines.
- **`last_attempt: null`:** no poll has run yet. Wait a few seconds and ask
  again once.
- **404:** the running binary predates this endpoint. Offer to rerun step 3
  in Update mode, including its proxy-lag advice.

Any of the non-working cases leaves the config change in place. Ask whether
to keep it, or revert `enabled` to `false` and restart.

**If the user picks the userscript or neither,** make no config change,
unless polling is currently on and they want it off.

## 7. Hand over

Tell the user:

- What was done: a fresh install, an update (from which version to which), or
  no binary change.
- The dashboard is at <http://localhost:27812>, and the tray icon's
  **Open dashboard** item opens it.
- Config: `%APPDATA%\usage_dashboard\config.yaml`. Database and log:
  `%LOCALAPPDATA%\usage_dashboard\`.
- To update later, run this runbook again, with its prompt from
  <https://github.com/vector76/skills-public/blob/main/oneshot/README.md>. It detects the install and updates it.
- Which quota source is now active. If they chose the userscript, remind them
  that it still needs installing from the README and that the usage page has
  to stay open. With polling, say whether the token refresh is on. Polling
  and the refresh can be turned on or off with `oauth_usage.enabled` and
  `oauth_usage.refresh_with_claude` in the config, followed by a restart,
  and checked at any time at
  <http://localhost:27812/api/oauth/status>.
- The container CLI, for Claude Code running in dev containers, is covered in
  the project's README.

## Notes for maintainers

Not part of the steps: when running this runbook, stop at the end of step 7.
These record why it is written the way it is. Read them before changing it.

### Why it exists

Installs the Claude Usage Dashboard tray app (`vector76/cc_usage_dashboard`)
on a Windows machine, from nothing to a running, auto-starting app. It
deliberately covers only `go install`, autostart, and choosing a quota source
(it can turn on OAuth polling). The userscript is manual browser work, so it
is mentioned, recommended against, and not installed. The container CLI and
clone-and-build paths were left out by choice.

### Decisions

- **A runbook, not a skill.** It installs software and registers a scheduled
  task, so nothing should start it unasked, and it runs once per machine plus
  the occasional update. As a runbook it costs no context in other sessions
  and needs no plugin release to change.
- **The Claude Code CLI is checked first, in step 0.** Polling reads the
  CLI's stored sign-in, and the token refresh runs `claude -p /usage`. The
  desktop app doesn't provide the command. Finding it missing in step 6
  would mean stopping to install software halfway through a setup, so the
  runbook asks for it before changing anything and points to
  `INSTALL_CLAUDE_CLI.md` rather than installing it itself.
- **Polling with the refresh is the recommended quota source.** The
  userscript is still named, because it exists and the upstream README
  documents it, but the runbook recommends against it. It needs a browser
  add-on running a script on a signed-in claude.ai page, that page kept
  open, and page scraping that breaks when the page changes. Polling gets
  the same figures without any of that.
- **The logon task is inlined, not taken from `install.ps1`.** `go install`
  puts no repo files on disk, so the script isn't available. The inline
  version mirrors it, including the task name `ClaudeUsageDashboard`, so
  running the upstream script later replaces this task instead of adding a
  second one. If upstream changes `install.ps1`, update step 4 to match.
- **A gate before each change** (Go install, binary install, task
  registration). Each one changes the machine.
- **Go has a no-admin route.** The winget package is the official `.msi`,
  which installs for all users and needs admin; winget has no per-user
  installer for it. The zip runs from any folder, so step 2 offers it too.
  Its download is pinned to the file name and checksum the user approved.
  Its `PATH` edit goes through the registry as `ExpandString`, because
  `[Environment]::SetEnvironmentVariable` rewrites the user `PATH` as plain
  text and breaks `%...%` entries.
- **The quota-source step comes after the first start.** The trayapp writes
  `config.yaml` on its first run, so there is nothing to edit before step 5.
  The file is read only at startup, so enabling polling needs a restart.
- **Only `oauth_usage` is touched.** Every other line of the user's config
  stays as it is. The runbook enables polling but never installs the
  userscript, because that is manual browser work.
- **Polling is confirmed with `/api/oauth/status`, not the shared counters.**
  `snapshots_received_total` and the snapshot age count every source, so a
  running userscript would hide a broken poller. That endpoint was added to
  trayapp for this runbook (commit `43e92b2`). Older binaries answer 404, which
  is why step 6 treats a 404 as "update the binary".
- **`credential_stale` is reported as normal.** The token expires after
  about 8 hours and refreshes only when Claude Code runs; flagging it as a
  failure would send users chasing a problem that fixes itself.
- **One runbook installs and updates.** Step 1 picks a mode (Install, Update,
  Installed elsewhere) so an update skips what is already set up: the task,
  and the quota choice unless the user wants to change it. Rerunning the
  runbook is the documented way to update.
- **"Installed elsewhere" asks rather than migrating.** A copy built from a
  checkout can resolve its database to a `usage.db` in its own working
  directory, while the `go install` copy uses `%LOCALAPPDATA%`. Switching
  silently would make the history look lost.
- **Versions come from `go version -m`.** With no tags upstream, `@latest` is
  a pseudo-version naming the commit. That is also how the runbook notices the
  module proxy serving an older cached `@latest`.
- **The token refresh is recommended with polling, and its caveat is stated.**
  `refresh_with_claude` (trayapp `75a99a4`) runs `claude -p /usage` on a
  stale token. Upstream notes that such a run was once seen to open a 5-hour
  window and later seemed not to. The runbook says so rather than promising
  it is free, because that behavior belongs to Claude Code and may change.
- **`claude_path` is pinned, not left to `PATH`.** The trayapp starts from
  Task Scheduler at logon, whose `PATH` may differ from the session that ran
  the runbook. A wrong path shows up only later, as `refresh_armed: false`.
- **Stop the running copy before reinstalling.** A running `trayapp.exe` locks
  the file, and `go install` then fails with a confusing error.
- **A session in the Claude desktop app is redirected, and step 0 checks.**
  Tested from the Code tab on 2026-09-28:
  - A new folder at the top of `%LOCALAPPDATA%` or `%APPDATA%` lands in
    `%LOCALAPPDATA%\Packages\<app>\LocalCache\...`. The session still sees it
    at the path it asked for; other programs don't. The same happens to a
    process started from the session with `Start-Process`.
  - Written for real: new files and folders inside AppData folders that
    already exist (`Programs`, `usage_dashboard`, `go-build`), rewrites of
    an existing `config.yaml`, `%USERPROFILE%` (so `go\bin` and the module
    cache), `%TEMP%`, and the Start-menu folder.
  - A task registered and started from the session runs outside it, so the
    logon task and `Start-ScheduledTask` are unaffected. `Stop-Process` from
    the session stops a trayapp running outside it.
  - The session had no package identity (`GetCurrentPackageFullName`
    returned `APPMODEL_ERROR_NO_PACKAGE`), so step 0 tests by writing a
    folder rather than asking Windows.

  That leaves three places to route around:
  - **The zip Go:** if `Programs` doesn't exist yet, the user creates it.
  - **The `PATH` edit:** the app keeps a private copy of the user registry
    (`Packages\<app>\SystemAppData\Helium\User.dat`). A process started
    outside, to see where an `HKCU\Environment` write landed, was refused.
    So the user makes the edit in their own console.
  - **Starting the trayapp without the task:** the user starts it.

  A first `go install` on a machine with no `%LOCALAPPDATA%\go-build` puts
  the build cache in private storage. That only costs a rebuild from a
  terminal later, so the runbook ignores it.
