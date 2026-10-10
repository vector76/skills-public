# Install the jamie plugin

A runbook: point a Claude Code session at this file to install the `jamie`
plugin from the `vector76-skills-public` marketplace, which is the GitHub
repository `vector76/skills-public`, and to turn on its auto-update. Follow the
steps in order, and stop at every `[ STOP / ASK ]` until the user answers.

It also checks an existing install. If the marketplace or the plugin is already
there, it skips what is done and goes on to the auto-update settings.

**Done** means:
- `vector76-skills-public` is registered at user scope, and `jamie` is
  installed from it.
- `~/.claude/settings.json` holds the two auto-update settings from step 3,
  unless the user declined them.
- The user knows to start a new session.

Change nothing beyond that. Don't edit other settings, and don't remove or
change other marketplaces or plugins. The commands are PowerShell, for
Windows. On macOS or Linux, the notes in each step give the equivalent.

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
  only the plugin. Don't remove and re-add the marketplace: that silently turns
  its auto-update off.
- **Both installed:** say so, and go to step 3 to check the auto-update
  settings.

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

## 3. Turn on auto-update   [ STOP / ASK ]

Out of the box, Claude Code won't update this plugin by itself. Two settings
in `~/.claude/settings.json` turn that on:

- **`"autoUpdate": true` on the marketplace.** For a marketplace not published
  by Anthropic, auto-update is off by default, and `marketplace add` has no
  option to turn it on.
- **`"FORCE_AUTOUPDATE_PLUGINS": "1"` in `env`.** The desktop app starts every
  session with Claude Code's auto-updater disabled, and that blocks plugin
  updates too unless this is set. It matters to anyone who uses the desktop
  app. For someone who only uses the terminal, it changes nothing.

First check which are already there:

```powershell
Select-String -Path (Join-Path $HOME '.claude\settings.json') -Pattern '"vector76-skills-public"|"autoUpdate"|"FORCE_AUTOUPDATE_PLUGINS"'
```

Read the file to confirm that any `"autoUpdate": true` found is inside the
`"vector76-skills-public"` entry, not another marketplace's. If both settings
are already in place, say so and go to step 4.

Otherwise, explain the missing ones, and ask whether to add them. Recommend
both. Wait for a clear answer, which may accept one and decline the other.

For each one accepted:

1. **Back up the file first:**

   ```powershell
   $settings = Join-Path $HOME '.claude\settings.json'
   Copy-Item $settings "$settings.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
   ```

2. **Edit it with the file-editing tools**, not shell redirection. Windows
   PowerShell 5's `>` and `Set-Content` can write UTF-16 or add a BOM, and the
   result may not load.
   - Add `"autoUpdate": true` inside the `"vector76-skills-public"` entry
     under `"extraKnownMarketplaces"`, as a sibling of its `"source"` key.
     `marketplace add` created that entry.
   - Add `"FORCE_AUTOUPDATE_PLUGINS": "1"` inside the top-level `"env"`
     object. Create the object if it is missing.

   Merge; never replace. Keep every other key, and the file's order and
   indentation. Never add a second copy of a top-level key: JSON parsers keep
   only the last one, so a second `"env"` block wipes out the first. Strict
   JSON only: no comments and no trailing commas.

3. **Validate:**

   ```powershell
   Get-Content $settings -Raw | ConvertFrom-Json | Out-Null; "valid: $?"
   ```

   If it fails, restore the backup over `settings.json`, confirm the restored
   file validates, report what went wrong, and stop.

Both settings take effect from the next session.

## 4. Check the result

```powershell
& $cli plugin list
Select-String -Path (Join-Path $HOME '.claude\settings.json') -Pattern '"autoUpdate"|"FORCE_AUTOUPDATE_PLUGINS"'
```

`jamie@vector76-skills-public` should be listed, with a version and user
scope, and the settings accepted in step 3 should show. If `jamie` is missing,
show the output and stop.

## 5. Hand over

Tell the user:

- The version of `jamie` installed, or that it was already installed.
- Which auto-update settings were added or already present. If they declined
  any, say that plugin updates won't arrive by themselves until it is set, and
  that running this runbook again offers it again.
- **Start a new session.** This one started before the install, so it doesn't
  have the plugin. The auto-update settings also take effect only from a new
  session. In it, typing `/jamie:` lists what the plugin offers.
- From then on, updates arrive by themselves. To update straight away:

  ```
  claude plugin marketplace update vector76-skills-public
  claude plugin update jamie@vector76-skills-public
  ```

## Notes for maintainers

Not part of the steps: when running this runbook, stop at the end of step 5.
These record why it is written the way it is. Read them before changing it.

- **Installs and checks.** There is no update skill and no startup hook in
  this plugin, so this runbook is the one place that checks the auto-update
  settings. Rerunning it on an existing install skips to those. It never
  removes and re-adds the marketplace, because that turns auto-update off.
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
- **The auto-update settings are described in two places:** this runbook's
  step 3 and the README's *Staying up to date*. Change one, and check the
  other.
- **Hand-over says a new session, not `/reload-plugins`.** A hand edit to
  `extraKnownMarketplaces` reaches the registry only at session start, so the
  settings need one anyway. A new session makes the final check clean.
- **Not verified end to end.** It is adapted from a runbook for another
  marketplace whose install and settings steps were documented but not run on
  a clean machine either. `REMOVE_JAMIE_PLUGIN.md`'s clean slate makes one.
