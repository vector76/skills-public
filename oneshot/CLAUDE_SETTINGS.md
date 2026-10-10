# Claude Code user settings

A one-time runbook. It adds four preferences to the user's Claude Code
settings file, asking before each one, then checks that the file still parses.

Claude Code will not load a `settings.json` that fails to parse: it stops at
startup with a Settings Error. So the rule throughout is **make sure the file
can be validated before touching it, back it up, validate after every write,
and restore the backup if validation fails.**

## 1. Choose a validation check

Do this before reading, backing up, or asking about anything. A machine that
can't validate the file must not have it edited.

A good check does more than parse the JSON. It rejects comments and trailing
commas, which Claude Code rejects, and it catches a key that appears twice in
the same object, which plain parsers silently accept. Go down the list below
and take the first check that passes the probe at the end of this step.

**On Windows, PowerShell 7 first.** It needs `pwsh` (PowerShell 7 or later),
because Windows PowerShell 5.1 lacks this parser. Set `$p` to the file
being checked:

```powershell
$p = "$HOME\.claude\settings.json"
function Test-Dupes($el, $path) {
  if ($el.ValueKind -eq 'Object') {
    $seen = @{}
    foreach ($prop in $el.EnumerateObject()) {
      if ($seen.ContainsKey($prop.Name)) { throw "duplicate key: $path$($prop.Name)" }
      $seen[$prop.Name] = $true
      Test-Dupes $prop.Value "$path$($prop.Name)."
    }
  } elseif ($el.ValueKind -eq 'Array') {
    foreach ($item in $el.EnumerateArray()) { Test-Dupes $item $path }
  }
}
$bytes = [IO.File]::ReadAllBytes($p)
if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { throw 'file starts with a BOM' }
$doc = [System.Text.Json.JsonDocument]::Parse([Text.Encoding]::UTF8.GetString($bytes))
Test-Dupes $doc.RootElement ''
'ok'
```

Don't use PowerShell's `ConvertFrom-Json` for this. It accepts comments,
trailing commas, and duplicate keys.

**Then Python** (first on macOS and Linux). The command is `python3` or
`python` on macOS and Linux, and `python` or `py` on Windows. Check that it's a
real interpreter first: `--version` must print a version. On Windows, a bare
`python` or `python3` is often just a shortcut to the Microsoft Store that
prints nothing useful or opens the Store; treat that as no Python.

```bash
python3 -c "
import json, sys
def no_dupes(pairs):
    keys = [k for k, _ in pairs]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes: sys.exit('duplicate keys: ' + ', '.join(dupes))
    return dict(pairs)
json.load(open(sys.argv[1], encoding='utf-8'), object_pairs_hook=no_dupes)
print('ok')
" ~/.claude/settings.json
```

**Last, Node.** It rejects comments, trailing commas, and a BOM, but not
duplicate keys, so after it passes, read the file back and confirm by eye that
no key appears twice in the same object:

```bash
node -e "JSON.parse(require('fs').readFileSync(process.argv[1],'utf8'));console.log('ok')" ~/.claude/settings.json
```

**Probe the one you chose.** A check that prints `ok` for everything is worse
than none, so prove it rejects bad input before trusting it. The commands above
name the settings file; point them at a scratch file for the probe:

- Write `{"a":1,}` (a trailing comma) to a scratch file and run the check on
  it. It must fail.
- For PowerShell or Python, also run it on `{"a":1,"a":2}` (a duplicate key).
  It must fail.

If a check doesn't run, or passes either probe, move to the next one. If none
is left, **stop before changing anything**. Tell the user which checks were
tried and that installing PowerShell 7 or Python would let this runbook run.
Don't skip validation, and don't substitute a looser check.

Use the chosen check, and only that one, for the rest of this runbook.

## 2. Find and read the file

- The file is `~/.claude/settings.json` (on Windows,
  `%USERPROFILE%\.claude\settings.json`). If `CLAUDE_CONFIG_DIR` is set, it is
  `$CLAUDE_CONFIG_DIR/settings.json` instead.
- Read the whole file before changing anything.
- If it does not exist, the starting content is `{}`. Create it only once the
  user has said yes to at least one setting.
- If it exists but does not parse (run the check from step 1 on it as it
  stands), **stop**. Report the error and where it is. Don't try to repair it
  unless the user asks, because a guess at a broken file can lose settings.
- Copy the file to `settings.json.bak-<yyyyMMdd-HHmmss>` next to it before the
  first edit. Tell the user the backup's path.

## 3. Offer each setting, one at a time

Take them in order. For each one:

- If the file already has exactly the proposed value, say so and move on
  without asking.
- Otherwise, show its purpose, the current value (or "not set"), and the
  proposed value. Ask, and wait for a clear yes before applying it. A no skips
  only that setting.
- If the current value differs from the proposal, show both and ask whether to
  replace it, since the user may have chosen it on purpose.

### a. Remove Claude's attribution from commits and PRs

By default Claude Code adds a `Co-Authored-By: Claude` trailer to commits it
makes, a "Generated with Claude Code" line to PR descriptions, and, for some
sessions, a link back to the session. These values turn all of that off, so
commits and PRs carry only what the user writes.

```json
"attribution": {
  "commit": "",
  "pr": "",
  "sessionUrl": false
}
```

- `commit` and `pr`: an empty string removes the commit trailer and the PR
  line.
- `sessionUrl: false` leaves out the session link.

Coherence: if the file has the older `includeCoAuthoredBy` key, it is
deprecated and superseded by `attribution`. Offer to remove it, as a separate
question.

### b. Keep session transcripts (nearly) forever

```json
"cleanupPeriodDays": 9999
```

At startup, Claude Code deletes transcripts of sessions inactive for longer
than this many days. The default is 30. After that, `/resume` can't reach
those sessions and their history can't be searched. 9999 days is about 27
years, which in practice means never. The cost is disk space: transcripts
build up under `~/.claude/projects/`. Don't use `0` to mean "never", because
it fails validation.

### c. Hide spinner tips

```json
"spinnerTipsEnabled": false
```

Hides the rotating tips shown next to the spinner while Claude works.

### d. Turn off the feedback survey

```json
"env": {
  "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY": "1"
},
"feedbackSurveyRate": 0
```

Stops the occasional "How is Claude doing?" prompt. Two settings, offered as
one question, because the environment variable alone has not been enough to
stop the prompt in the desktop app:

- `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY` is an environment variable, so it goes
  in the `env` block, and its value must be the string `"1"`, not the number.
- `feedbackSurveyRate` is a top-level key, not part of `env`. It is the chance
  of showing the survey, from 0 to 1, so its value is the number `0`, not a
  string.

## 4. Apply without disturbing anything else

- **Merge; never replace.** `attribution` and `env` are objects. If one already
  exists, add or update only the keys above inside it. Every other key in it
  stays: an existing `env` often holds unrelated variables, and replacing the
  object would silently drop them.
- Never add a second copy of a top-level key. JSON parsers keep only the last
  one without any error, so a second `"env": {...}` block wipes out the first.
- Leave all other keys, their order, and the file's indentation as they are.
- Strict JSON only: no comments, no trailing commas, double-quoted keys and
  strings, and a comma between entries, including the entry before a newly
  added one.
- Make edits with the file-editing tools, not shell redirection.
  Windows PowerShell 5's `>` and `Set-Content` can write UTF-16 or add a BOM,
  and the result may not load.

## 5. Validate after every write

Run the check from step 1 on the file. Then read it back and confirm each
setting that was applied has the intended value and type: strings are strings,
`false` is a boolean, `9999` and `0` are numbers.

**If validation fails:** restore the backup over `settings.json`, confirm the
restored file validates, report what went wrong, and stop.

## 6. Check for overrides, then report

- Settings in a project's `.claude/settings.json` or
  `.claude/settings.local.json`, and managed (organization) settings, take
  precedence over user settings. If the current project's files set any of the
  same keys, tell the user which ones win there. Don't edit those files.
- Summarize: each setting and whether it was applied, already set, or
  declined, plus the backup's path. The user can delete the backup once they're
  satisfied.
- Changes are sure to apply only to sessions started from now on. Tell the user
  to start a new session.
