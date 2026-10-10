# Remove the jamie plugin

A runbook: point a Claude Code session at this file to uninstall the `jamie`
plugin. Optionally it also removes the `vector76-skills-public` marketplace
and what the plugin left behind. That restores a clean slate: the state a
first run of `INSTALL_JAMIE_PLUGIN.md` expects, so it also serves to test that
runbook. Follow the steps in order, and stop at every `[ STOP / ASK ]` until
the user answers.

**Done** means one of:
- **Plugin only:** `jamie` is uninstalled at every scope it was installed at.
  The marketplace stays.
- **Clean slate:** every plugin from `vector76-skills-public` is uninstalled,
  the marketplace is removed, and the leftovers the user agreed to are
  deleted. `INSTALL_JAMIE_PLUGIN.md`'s step 0 finds nothing installed.

Change nothing beyond that. Don't touch other marketplaces or their plugins,
or any setting not named here. The commands are PowerShell, for Windows.

## 0. Detect what is there

Find the Claude Code CLI:

```powershell
$cli = $null; $p = $PID
while ($p -and -not $cli) {
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$p" -ErrorAction SilentlyContinue
  if (-not $proc) { break }
  if ($proc.ExecutablePath -match '\\(claude-code\\[^\\]+|\.local\\bin)\\claude\.exe$') { $cli = $proc.ExecutablePath }
  $p = $proc.ParentProcessId
}
if (-not $cli) { $cli = (Get-Command claude -ErrorAction SilentlyContinue).Source }
"cli: $cli"
```

Use `$cli` for every command below, and redefine it the same way in any later
PowerShell call. If no CLI is found, stop: the user needs the `claude`
command, which the `INSTALL_CLAUDE_CLI.md` runbook installs. Its prompt is in
<https://github.com/vector76/skills-public/blob/main/oneshot/README.md>.

Then list what is installed from `vector76-skills-public`:

```powershell
$mp = 'vector76-skills-public'
$plugins = Join-Path $HOME '.claude\plugins'
$reg  = Get-Content (Join-Path $plugins 'known_marketplaces.json') -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
$inst = Get-Content (Join-Path $plugins 'installed_plugins.json') -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
"marketplace registered: $([bool]($reg -and $reg.PSObject.Properties[$mp]))"
foreach ($prop in @($inst.plugins.PSObject.Properties | Where-Object Name -like "*@$mp")) {
  foreach ($e in @($prop.Value)) { "installed: $($prop.Name)  scope: $($e.scope)  version: $($e.version)$(if ($e.projectPath) { "  project: $($e.projectPath)" })" }
}
"other third-party marketplaces: $(@($reg.PSObject.Properties | Where-Object { $_.Name -ne $mp -and $_.Value.source.repo -notlike 'anthropics/*' } | ForEach-Object Name) -join ', ')"
```

A plugin can be installed more than once, at different scopes or for
different projects. Each `installed:` line is one install, and each one is
uninstalled separately.

- **Marketplace not registered, and nothing installed from it:** there is
  nothing to uninstall. Go to step 4, which checks for leftovers.
- **Otherwise:** go to step 1.

## 1. Choose what to remove   [ STOP / ASK ]

Show the user the `installed:` lines. Then offer the two choices:

- **Plugin only.** Uninstall each `jamie@vector76-skills-public` install. The
  marketplace stays, so reinstalling needs only `claude plugin install`.
- **Clean slate.** Uninstall every install listed, then remove the
  marketplace (step 3) and offer to delete the leftovers (step 4). Say this is
  the one to use before testing `INSTALL_JAMIE_PLUGIN.md`.

If `jamie` isn't installed but other plugins from the marketplace are, say so.
The only choice left is the clean slate.

Wait for a clear answer.

## 2. Uninstall

For each install chosen, uninstall it at its own scope. For a `local` or
`project` install, run the command from its project folder:

```powershell
& $cli plugin uninstall jamie@vector76-skills-public -s user
```

```powershell
Push-Location '<project>'; & $cli plugin uninstall jamie@vector76-skills-public -s local; Pop-Location
```

Uninstalling also removes the plugin's entry from `enabledPlugins` in that
scope's settings file, and its data folder under `~/.claude/plugins/data/`.
If the project folder no longer exists, run the command from any folder, and
if that fails, report it and go on to the next.

If any other uninstall fails, show its output and stop. For **plugin only**,
go to step 5 afterwards.

## 3. Remove the marketplace

Clean slate only.

```powershell
& $cli plugin marketplace remove vector76-skills-public
```

This removes the marketplace from the registry and deletes its git clone
under `~/.claude/plugins/marketplaces/`. It also drops its entry under
`extraKnownMarketplaces`, including `autoUpdate`, from the settings files it
knows about.

Then check the project settings files of the uninstalled local and project
installs, which it may not have covered:

```powershell
Select-String -Path '<project>\.claude\settings.local.json', '<project>\.claude\settings.json' -Pattern '"vector76-skills-public"' -ErrorAction SilentlyContinue
```

If a match remains in a `settings.local.json`, offer to remove that
`"vector76-skills-public"` entry. Use the same care as in step 4. A match in a
project's `settings.json` is a file committed to that repository. Tell the
user where it is, and leave it.

## 4. Delete the leftovers   [ STOP / ASK ]

Clean slate only. List what is left:

```powershell
$claude = Join-Path $HOME '.claude'
Get-ChildItem (Join-Path $claude 'plugins\data') -Directory -ErrorAction SilentlyContinue | Where-Object Name -like 'jamie-*' | ForEach-Object { "data: $($_.FullName)" }
$cache = Join-Path $claude 'plugins\cache\vector76-skills-public'
if (Test-Path $cache) {
  $live = @(Get-ChildItem $cache -Recurse -Force -Directory -Filter '.in_use' | Get-ChildItem -File -Force | Where-Object { Get-Process -Id $_.Name -ErrorAction SilentlyContinue })
  "cache: $cache  (locks held by running sessions: $($live.Count))"
}
Select-String -Path (Join-Path $claude 'settings.json') -Pattern '"FORCE_AUTOUPDATE_PLUGINS"' -ErrorAction SilentlyContinue
```

What each one is:

- **Data folders:** plugin data that uninstall didn't cover. This plugin's
  are expected to be named `jamie-vector76-skills-public`, or `jamie-inline`
  for one from a `--plugin-dir` session. The listing matches loosely, so it
  can catch another plugin whose name starts with `jamie-`. Offer only this
  plugin's, and leave the rest. Safe to delete.
- **Cache:** the installed copies of every version. Uninstall only marks them
  orphaned. A running session holds a lock on the copy it loaded, and this
  session is probably one of them. Offer to delete the cache only when no locks
  are held. Otherwise, tell the user they can delete the folder themselves once
  every Claude Code session is closed, and that leaving it doesn't get in the
  way of a reinstall.
- **`FORCE_AUTOUPDATE_PLUGINS`** in the `env` of `~/.claude/settings.json`.
  `INSTALL_JAMIE_PLUGIN.md` adds it, but it isn't specific to this
  marketplace: under the desktop app, it lets *every* third-party marketplace
  update its plugins, and there's no record of who added it. If step 0 listed
  other third-party marketplaces, recommend keeping it. Otherwise, removing it
  is part of a clean slate, and lets a test of `INSTALL_JAMIE_PLUGIN.md` add it
  again in its step 3.

Show what was found. Ask which to delete, and wait for a clear answer.

Delete files and folders with `Remove-Item -Recurse -Force`, one path at a
time, and only the paths listed.

To remove `FORCE_AUTOUPDATE_PLUGINS`:

1. Back up the settings file:

   ```powershell
   $settings = Join-Path $HOME '.claude\settings.json'
   Copy-Item $settings "$settings.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
   ```

2. With the file-editing tools, not shell redirection, remove only that key
   from `env`, along with the comma that separated it. If `env` is then empty,
   remove `"env": {}` too. Keep everything else as it is. Strict JSON only: no
   trailing commas.
3. Validate:

   ```powershell
   Get-Content $settings -Raw | ConvertFrom-Json | Out-Null; "valid: $?"
   ```

   If it fails, restore the backup over `settings.json`, confirm the restored
   file validates, report what went wrong, and stop.

## 5. Check the result

Run step 0's listing again.

- **Plugin only:** no `installed: jamie@vector76-skills-public` line remains.
- **Clean slate:** `marketplace registered: False`, and no `installed:` lines.
  This is what `INSTALL_JAMIE_PLUGIN.md`'s step 0 reads as nothing installed.

If anything remains, show it and stop.

## 6. Hand over

Tell the user:

- What was uninstalled, whether the marketplace was removed, and which
  leftovers were deleted or kept.
- **Sessions that are already open still have the plugin**, this one
  included, until they are closed or run `/reload-plugins`. New sessions start
  without it.
- For a clean slate: `INSTALL_JAMIE_PLUGIN.md` installs everything again from
  scratch, including the auto-update settings. Its prompt is in
  <https://github.com/vector76/skills-public/blob/main/oneshot/README.md>.

## Notes for maintainers

Not part of the steps: when running this runbook, stop at the end of step 6.
These record why it is written the way it is. Read them before changing it.

- **Why the clean slate exists.** It is the counterpart of
  `INSTALL_JAMIE_PLUGIN.md`. Running this one and then that one on the same
  machine tests a first install for real, without a spare machine or Windows
  account. So step 5's clean-slate check is the same condition that runbook's
  step 0 reads as nothing installed.
- **What the CLI does, from an earlier test of the same steps** with a
  throwaway git marketplace (Claude Code 2.1.284, Windows):
  - `plugin uninstall` removed the `enabledPlugins` entry and the data folder.
  - `marketplace remove` removed the registry entry, the whole
    `extraKnownMarketplaces` entry (`autoUpdate` included), and the clone.
  - The cache folder remained, marked `.orphaned_at`.
  - Uninstalling a local-scope plugin from its project folder left
    `"enabledPlugins": {}` in that project's `settings.local.json`, which is
    harmless.
- **Not verified:** whether `marketplace remove` without `--scope` reaches a
  local or project declaration in a project other than the current folder.
  Hence step 3's check. Also not verified: when Claude Code deletes orphaned
  cache versions by itself, and the exact naming of data folders, which is
  why step 4 matches loosely and shows each one.
- **`FORCE_AUTOUPDATE_PLUGINS` is a question, not a default.** It is shared by
  every third-party marketplace, so removing it can silently stop another
  marketplace's updates under the desktop app.
- **The CLI lookup** is the same as `INSTALL_JAMIE_PLUGIN.md`'s; see its notes.
- **Not run end to end** against this marketplace.
