# Install the jamie plugin

A runbook: point a Claude Code session at this file to install the `jamie`
plugin from the `vector76-skills-public` marketplace, which is the GitHub
repository `vector76/skills-public`. Follow the steps in order, and stop at
every `[ STOP / ASK ]` until the user answers.

**Done** means:
- `vector76-skills-public` is registered at user scope, and `jamie` is
  installed from it.
- The user knows to start a new session, and how to update.

Change nothing beyond that. Don't edit settings files yourself, don't turn on
auto-update, and don't remove or change other marketplaces or plugins. The
commands are PowerShell, for Windows. On macOS or Linux, the notes in each
step give the equivalent.

## 0. Detect what is there

Find the Claude Code CLI, then check for an existing install:

```powershell
$cli = $null; $p = $PID
while ($p -and -not $cli) {
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$p" -ErrorAction SilentlyContinue
  if (-not $proc) { break }
  if ($proc.ExecutablePath -match '\\(claude-code\\[^\\]+|\.local\\bin)\\claude\.exe$') { $cli = $proc.ExecutablePath }
  $p = $proc.ParentProcessId
}
if (-not $cli) { $cli = (Get-Command claude -ErrorAction SilentlyContinue).Source }
"cli: $cli"; if ($cli) { & $cli --version }
$plugins = Join-Path $HOME '.claude\plugins'
$reg  = Get-Content (Join-Path $plugins 'known_marketplaces.json') -Raw -ErrorAction SilentlyContinue
$inst = Get-Content (Join-Path $plugins 'installed_plugins.json') -Raw -ErrorAction SilentlyContinue
"marketplace registered: $([bool]($reg -and ($reg -match '"vector76-skills-public"\s*:' -or $reg -match 'vector76/skills-public')))"
"jamie installed: $([bool]($inst -and $inst -match '"jamie@vector76-skills-public"'))"
```

The loop finds the CLI running this session. It is the desktop app's copy, or
a native install under `.local\bin`, so it exists even when `claude` is not on
`PATH`. Use `$cli` for every command below, and redefine it the same way in
any later PowerShell call. On macOS or Linux, use `claude` from `PATH`, and
check the same two files with `grep`.

Then pick a mode, and tell the user which one:

- **No CLI found:** stop. The user needs the `claude` command first; the
  `INSTALL_CLAUDE_CLI.md` runbook installs it. Its prompt is in
  <https://github.com/vector76/skills-public/blob/main/oneshot/README.md>.
- **Nothing installed:** go to step 1.
- **Marketplace registered, `jamie` not installed:** go to step 2, and install
  only the plugin. Don't remove and re-add the marketplace: that resets its
  settings, including any auto-update the user turned on.
- **Both installed:** stop. Say it is already installed, and that
  `/jamie:update` updates it to the latest version. A version older than
  0.2.0 has no `/jamie:update`; there, these two commands update it:

  ```
  claude plugin marketplace update vector76-skills-public
  claude plugin update jamie@vector76-skills-public
  ```

## 1. Check the repository can be reached

The marketplace is a public GitHub repository, which Claude Code fetches with
`git`. Check both before changing anything:

```powershell
$env:GIT_TERMINAL_PROMPT = '0'
git ls-remote 'https://github.com/vector76/skills-public' HEAD
"exit: $LASTEXITCODE"
```

On macOS or Linux, prefix the command with `GIT_TERMINAL_PROMPT=0`.

- **Exit 0, with a commit hash:** go to step 2.
- **`git` not found:** stop. Git is required, and on Windows it is part of Git
  for Windows, which Claude Code also needs.
- **Anything else:** stop, show the output, and say it looks like a network
  or proxy problem, since the repository is public and needs no sign-in.

## 2. Install   [ STOP / ASK ]

Tell the user what will run:

- Register the `vector76-skills-public` marketplace at user scope (unless step
  0 found it). This makes a git clone under
  `~/.claude/plugins/marketplaces/`, and adds the marketplace to
  `~/.claude/settings.json`.
- Install `jamie` at user scope, so it is available in every session.

Ask whether to go ahead, and wait for a clear answer.

```powershell
& $cli plugin marketplace add vector76/skills-public
& $cli plugin install jamie@vector76-skills-public
```

Skip the first command if the marketplace is already registered. Run each
command only if the one before it succeeded. If either fails, show its output
and stop.

## 3. Check the result

```powershell
& $cli plugin list
```

`jamie@vector76-skills-public` should be listed, with a version and user
scope. If it is missing, show the output and stop.

## 4. Hand over

Tell the user:

- The version of `jamie` installed.
- **Start a new session.** This one started before the install, so it doesn't
  have the plugin. In it, typing `/jamie:` lists what the plugin offers.
- **Updates don't arrive by themselves.** `/jamie:update` updates the plugin
  whenever they want. To have Claude Code update it automatically instead,
  see *Staying up to date* in
  <https://github.com/vector76/skills-public/blob/main/README.md>; that is
  their choice to make.

## Notes for maintainers

Not part of the steps: when running this runbook, stop at the end of step 4.
These record why it is written the way it is. Read them before changing it.

- **Installs only; it never turns on auto-update.** Auto-update means the
  author's later changes reach the user's sessions unseen, and one of its two
  settings, `FORCE_AUTOUPDATE_PLUGINS`, applies to every third-party
  marketplace on the machine. So it is the user's decision, documented in the
  README, not a step here. `/jamie:update` is the update path, so an existing
  install is sent there. The runbook never removes and re-adds the
  marketplace, which would reset its settings.
- **Why the CLI comes from the process tree.** A user who only has the desktop
  app has no `claude` on `PATH`, but the app's own copy is running this
  session. The loop takes the nearest ancestor whose path is a CLI location
  (`claude-code\<version>\claude.exe` or `.local\bin\claude.exe`). It matches
  by path because the desktop app, further up the tree, is also named
  `Claude.exe`, and running it with CLI arguments would be wrong. Using the
  app's copy for one run is fine. `INSTALL_CLAUDE_CLI.md` warns against putting
  it on `PATH`, because the app replaces it on update.
- **`GIT_TERMINAL_PROMPT=0`** stops `ls-remote` from waiting on a terminal
  password prompt the session can't answer. The repository is public, so a
  prompt would mean something else is wrong.
- **Hand-over says a new session.** `/reload-plugins` would also load the
  plugin, but a new session is simpler to explain and always works.
- **Verified** on Windows with Claude Code 2.1.290, from a clean state:
  steps 0 to 2 installed 0.1.0 at user scope. (That run used an earlier
  version of this runbook with an auto-update step, which was declined.)
  `REMOVE_JAMIE_PLUGIN.md`'s clean slate makes a clean state again.
