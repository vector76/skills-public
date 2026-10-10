# Install the Claude Code CLI

A runbook: point a Claude Code session at this file to make the `claude`
command, including `claude -p`, work from any terminal. The Claude desktop app
doesn't provide one: it keeps its own copy of Claude Code, which isn't on
`PATH`. Follow the steps in order, and stop at every `[ STOP / ASK ]` until the
user answers.

**Done** means: `claude --version` works in a new terminal, and
`claude auth status` reports `"loggedIn": true`. Change nothing beyond that:
no edits to `~/.claude/settings.json`, and no uninstalling of other copies.

The native installer needs no admin rights. It installs to
`~/.local/bin/claude` (on Windows, `%USERPROFILE%\.local\bin\claude.exe`), adds
that folder to the user's `PATH`, and keeps itself up to date.

## 0. Detect what is there

On Windows, with the PowerShell tool:

```powershell
"on PATH: $((Get-Command claude -ErrorAction SilentlyContinue).Source)"
$native = "$env:USERPROFILE\.local\bin\claude.exe"
if (Test-Path $native) { "native:  $(& $native --version)" } else { 'native:  not installed' }
"desktop app copies: $((Get-ChildItem "$env:APPDATA\Claude\claude-code" -Directory -ErrorAction SilentlyContinue).Name -join ', ')"
```

On macOS or Linux:

```bash
echo "on PATH: $(command -v claude)"
~/.local/bin/claude --version 2>/dev/null || echo "native:  not installed"
```

Then:

- **`claude` is on `PATH`:** it's already installed. Say which copy it is and
  go to step 2. If it's an npm install (`claude.cmd` under `npm`, or a path
  under `node_modules`), it works as it is. Mention that `claude install`
  switches it to the native build, which updates itself, but don't run that
  unless the user asks.
- **Installed natively but not on `PATH`:** the install is fine, but this
  terminal predates it or the `PATH` change didn't take. Go to step 2.
- **Not installed:** go to step 1.

Don't use the desktop app's copies under `%APPDATA%\Claude\claude-code\` as
the `claude` command, and don't add them to `PATH`. Each sits in a folder named
for its version, and the app replaces them as it updates, so a path to one
stops working without warning.

## 1. Install   [ STOP / ASK ]

Tell the user what will run and that it needs no admin rights. The installer
is a script downloaded from `claude.ai` and run directly, so say that too.
Wait for a clear yes.

- Windows (PowerShell):

  ```powershell
  irm https://claude.ai/install.ps1 | iex
  ```

- macOS or Linux:

  ```bash
  curl -fsSL https://claude.ai/install.sh | bash
  ```

If the user would rather use a package manager, `winget install
Anthropic.ClaudeCode` (Windows) and `brew install --cask claude-code` (macOS)
also work. Those don't update themselves; the user updates them through the
package manager.

If the install fails, show the output and stop.

## 2. Confirm it runs

This session's `PATH` was fixed when it started, so call the new binary by its
full path:

```powershell
& "$env:USERPROFILE\.local\bin\claude.exe" --version
```

```bash
~/.local/bin/claude --version
```

It should print something like `2.1.282 (Claude Code)`. For a copy installed
another way, use the path found in step 0. If it doesn't run, show
`claude doctor`'s output (called the same way) and stop.

## 3. Sign in   [ STOP / ASK ]

Check whether this install is already signed in. The check reads the stored
sign-in and uses no quota:

```powershell
& "$env:USERPROFILE\.local\bin\claude.exe" auth status
```

It prints JSON. With `"loggedIn": true`, report `authMethod`, `orgName` and
`subscriptionType`, and go to step 4. Don't repeat the email address unless
the user asks. If the command isn't recognized, the copy predates it. Offer
`claude update`, then check again.

Otherwise, the user signs in. It's a browser sign-in they have to complete
themselves, so this session can't do it for them. Tell them to:

1. Open a **new** terminal, so `claude` is on its `PATH`.
2. Run:

   ```
   claude auth login
   ```

   A browser page opens for signing in with their Claude account. If the
   organization signs in through single sign-on, add `--sso`. If they're
   billed through the Anthropic Console with an API key instead of a Claude
   subscription, add `--console`.
3. Finish signing in in the browser, then come back and say so.

This sign-in is the CLI's own. Being signed in to the desktop app doesn't
count, so the user may need to do this even though the app is signed in.

Then run the `auth status` check again. If it still reports
`"loggedIn": false`, ask what the terminal showed, and stop.

For a machine where no one can sign in through a browser, such as a server,
`claude setup-token` creates a long-lived token to set as
`CLAUDE_CODE_OAUTH_TOKEN`. Mention it only if the user asks about that case.

## 4. Hand over

Tell the user:

- The version installed, or that an existing copy was already in place.
- That `claude` and `claude -p` work in any terminal opened from now on, and
  terminals already open need to be reopened first.
- How it's signed in: `authMethod`, `orgName` and `subscriptionType` from
  step 3.
- A native install updates itself. A winget or Homebrew install is updated
  through that package manager.

Don't run `claude -p` as a test. It's a real request that counts toward the
user's usage, and whatever needs it will run it.

## Notes for maintainers

Not part of the steps: when running this runbook, stop at the end of step 4.
These record why it is written the way it is. Read them before changing it.

- **Why it doesn't explain `claude -p`.** Some other tool finds that it needs
  the CLI and points here. That tool explains why it's needed; this runbook
  only installs it and gets it signed in.
- **Native first.** Anthropic recommends it: no admin rights, and it updates
  itself. winget and Homebrew are offered only as alternatives because they
  don't update themselves.
- **Status comes from `claude auth status`.** It reads the stored sign-in, so
  confirming uses no quota, unlike a test `claude -p` run. It exits 0 when
  signed in, and its output is JSON by default. `--text` gives
  human-readable output instead.
- **The user does the sign-in.** It's a browser OAuth flow, and it must be the
  user's own. The session checks the result but never enters anything itself.
- **The desktop app's copies are off limits.** They live in
  `%APPDATA%\Claude\claude-code\<version>\`, and the app replaces them as it
  updates, so a `PATH` entry or pinned path to one breaks.
