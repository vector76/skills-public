---
description: Update the jamie plugin to its latest published version now, and say whether this session needs a reload to use it.
disable-model-invocation: true
---

You are updating the `jamie` plugin, installed from the `vector76-skills-public`
marketplace, to its latest published version, and telling the user where they
stand. Run the steps in order.

Rules that hold throughout:

- Run only the commands below. Don't install, uninstall, or remove and re-add
  the marketplace; re-adding it resets its settings.
- Don't edit any settings file, and don't change auto-update. If the user asks
  how to get updates automatically, point them to *Staying up to date* in
  <https://github.com/vector76/skills-public/blob/main/README.md>.
- No commits, no pushes, whatever repo this session is in.

## 1. Find the Claude Code CLI

If `claude --version` prints a version, use `claude`.

Otherwise, on Windows, the session may be running in the desktop app, whose
own copy of the CLI isn't on `PATH`. Find the copy running this session with
PowerShell:

```powershell
$cli = $null; $p = $PID
while ($p -and -not $cli) {
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$p" -ErrorAction SilentlyContinue
  if (-not $proc) { break }
  if ($proc.ExecutablePath -match '\\(claude-code\\[^\\]+|\.local\\bin)\\claude\.exe$') { $cli = $proc.ExecutablePath }
  $p = $proc.ParentProcessId
}
"cli: $cli"
```

Use that path wherever `claude` appears below. If no CLI is found, stop. Tell
the user they can update from the desktop app's plugin manager instead, or
install the CLI with the `INSTALL_CLAUDE_CLI.md` runbook, whose prompt is in
<https://github.com/vector76/skills-public/blob/main/oneshot/README.md>.

## 2. Record what is installed

```
claude plugin list --json
```

Note the `version` and `scope` of each entry whose `id` is
`jamie@vector76-skills-public`. If there is none, say the plugin isn't
installed from the marketplace on this machine, and stop.

## 3. Update

```
claude plugin marketplace update vector76-skills-public
claude plugin update jamie@vector76-skills-public -s <scope>
```

Run the second command once for each entry from step 2, with its scope. For a
`local` or `project` entry, run it from that project's folder: the
`projectPath` recorded for it in `~/.claude/plugins/installed_plugins.json`.

If the marketplace update fails, quote its output and stop: without it,
"latest" is whatever was last fetched. If a plugin update fails, quote it and
go on to the next entry.

## 4. Report

Run `claude plugin list --json` again, and tell the user, briefly:

- **Each entry's version,** as old → new, or that it was already current.
- **If anything changed:** this session, and any other session already open,
  still runs the old copy. Typing `/reload-plugins` loads the new one into a
  session; a new session starts with it. No app restart is needed.
- **If nothing changed:** they are up to date, and nothing is needed.
