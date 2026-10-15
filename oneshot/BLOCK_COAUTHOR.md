# Block Co-Authored-By in commit messages

A runbook: point a Claude Code session at this file to make git reject any
commit whose message has a `Co-Authored-By:` line, in every repository on this
machine. Follow the steps in order, and stop at every `[ STOP / ASK ]` until
the user answers.

Turning off Claude Code's attribution setting is not enough: the model can
still write the trailer into a message on its own. This puts the check in git
itself, as a `commit-msg` hook defined in the user's global git config. That
kind of hook needs **Git 2.54 or newer**. It is stored in no repository, so
nothing gets committed, and it stacks with each repository's own hooks
instead of replacing them: husky, pre-commit, and `.git/hooks` scripts all
still run.

**Done** means:
- `git` is 2.54 or newer.
- `~/.config/git/no-coauthor.sh` exists, and the global git config has a
  `hook.no-coauthor` section that runs it on `commit-msg`.
- A test commit in a throwaway repository is rejected with the trailer and
  accepted without it.

Change nothing beyond that. Don't edit any repository's config or hooks,
don't set `core.hooksPath`, and don't touch Claude Code's settings. Run the
commands with the Bash tool; on Windows that is Git Bash, which Claude Code
requires.

## 0. Detect what is there

```bash
git --version
type -a git
git config --global --show-origin --get-regexp '^hook\.' ; echo "hook config exit: $?"
git config --global --show-origin --get core.hooksPath ; echo "hooksPath exit: $?"
ls -l ~/.config/git/no-coauthor.sh 2>&1
echo "GIT_CONFIG_GLOBAL=[${GIT_CONFIG_GLOBAL:-}]"
```

Report to the user:

- **The git version.** If there is more than one `git` on `PATH`, give each
  one's version (`<path> --version`). The one listed first is what Claude
  Code and terminals use.
- **Any `hook.*` lines.** If `hook.no-coauthor` is already there and the
  script exists, it is already installed: skip to step 4, to prove it works.
  If other `hook.*` names are there, say so; this runbook leaves them alone.
- **The global config file**, from `--show-origin`: usually `~/.gitconfig`,
  or `~/.config/git/config` if only that one exists, or `$GIT_CONFIG_GLOBAL`
  if set. If nothing printed a path, it is `~/.gitconfig`, and `git config`
  creates it in step 3.
- **A global `core.hooksPath`**, if one is set. It doesn't stop this hook,
  which doesn't use the hooks folder, but the user may want to know.

## 1. Git 2.54 or newer   [ STOP / ASK ]

If the first `git` is 2.54 or newer, go to step 2.

Otherwise, explain that older git ignores `hook.*` config without any
warning, so the hook would look installed and do nothing. Offer to upgrade,
say how, and wait for a clear yes:

- **Windows:** `winget upgrade --id Git.Git --exact` (or `winget install`
  with the same arguments if winget doesn't list it). This is Git for Windows'
  installer, which upgrades the existing install in place. It installs for
  all users, so Windows asks for administrator approval, which the user must
  give at the desktop. Before running it, run `winget show --id Git.Git
  --exact` and tell the user the version and installer URL it shows.
- **macOS:** Apple's `/usr/bin/git` can't be upgraded. `brew install git`
  (or `brew upgrade git`) installs a current one, which takes effect only if
  Homebrew's `bin` comes before `/usr/bin` on `PATH`. If Homebrew isn't
  installed, stop and say so.
- **Linux:** the distribution's package may still be older than 2.54. On
  Ubuntu, the `ppa:git-core/ppa` repository has current releases. Elsewhere,
  say what the package manager offers and let the user decide.

If the user declines, or the upgrade fails, stop. Don't install the hook on
an older git.

Afterwards run `git --version` again. If the first `git` on `PATH` is still
older than 2.54, stop and report which one it is.

## 2. Back up the global config

If the global config file from step 0 exists, copy it next to itself as
`<file>.bak-<yyyyMMdd-HHmmss>` and tell the user the backup's path. If it
doesn't exist, there is nothing to back up; step 6 would delete it instead of
restoring it.

## 3. Install the hook   [ STOP / ASK ]

Tell the user what will change:

- A new file, `~/.config/git/no-coauthor.sh` (the folder is created if
  missing).
- Two lines in the global git config:
  `hook.no-coauthor.command = sh ~/.config/git/no-coauthor.sh` and
  `hook.no-coauthor.event = commit-msg`.
- From then on, every `git commit` on this machine, by anyone or anything
  using this git, fails if the message has a line starting with
  `Co-Authored-By:` (any case, leading spaces allowed). Mentioning it in the
  middle of a sentence is fine.

Wait for a yes. Then write the script with the file-writing tool, exactly as
below, with LF line endings:

```sh
#!/bin/sh
# commit-msg hook: reject a message with a Co-Authored-By trailer.
# Installed globally as hook.no-coauthor; remove with
#   git config --global --remove-section hook.no-coauthor
if grep -v '^#' "$1" | grep -qiE '^[[:space:]]*co-authored-by[[:space:]]*:'; then
  echo 'Commit rejected by the no-coauthor hook: the message has a Co-Authored-By line. Remove that line and commit again. Do not use --no-verify.' >&2
  exit 1
fi
```

Check it has no carriage returns; this must print `0`:

```bash
grep -c $'\r' ~/.config/git/no-coauthor.sh
```

Then add the config with `git config`, never by editing the file by hand:

```bash
git config --global hook.no-coauthor.command 'sh ~/.config/git/no-coauthor.sh'
git config --global hook.no-coauthor.event commit-msg
git config --global --get-regexp '^hook\.no-coauthor\.'
```

The last command should print exactly the two lines. Keep the single quotes
on the first one: the `~` must reach git as written, so the shell git runs
the hook with expands it.

## 4. Prove it works

In a throwaway repository, try one commit that must be rejected and one that
must go through:

```bash
t="$(mktemp -d)"
git init -q "$t"
c() { git -C "$t" -c user.name=test -c user.email=test@example.invalid -c commit.gpgsign=false commit -q --allow-empty "$@"; }
c -m 'test' -m 'Co-Authored-By: Test <test@example.invalid>'; echo "with trailer: exit $?"
c -m 'test: mentions Co-Authored-By in prose'; echo "without trailer: exit $?"
git -C "$t" hook list commit-msg
rm -rf "$t"
```

The `-c` options keep the test from depending on the user's name, email, or
signing setup; they apply to this test repository only, which is deleted
afterwards.

- **With trailer:** must exit non-zero and print `Commit rejected by the
  no-coauthor hook`.
- **Without trailer:** must exit 0.
- `git hook list commit-msg` should show `no-coauthor`.

If the commit without the trailer fails, **every commit on this machine is
now failing**: go to step 6 at once. If the commit with the trailer goes
through, the hook isn't running: go to step 6, then report the `git
--version` and `hook list` output.

## 5. Hand over

Tell the user:

- What was installed (or that it already was), and where the backup is. They
  can delete the backup once they're satisfied.
- **It applies to every repository at once**, including ones already
  cloned, and to new sessions and old ones alike. Nothing needs restarting.
- **What it doesn't cover:** `git commit --no-verify` skips it, as it skips
  every `commit-msg` hook. Tools that bundle their own git (GitHub Desktop,
  for one) or don't use git's command line at all (libgit2-based tools) won't
  run it if their git is older than 2.54 or has no hook support. Commits made
  elsewhere, such as GitHub's web editor, aren't checked.
- **If the script is deleted or moved,** every commit fails with an error
  naming it. To remove the hook:

  ```bash
  git config --global --remove-section hook.no-coauthor
  rm ~/.config/git/no-coauthor.sh
  ```

## 6. Roll back (only if step 4 failed)

Restore the backup over the global config file (or, if there was none,
delete the file `git config` created), delete
`~/.config/git/no-coauthor.sh`, and confirm that
`git config --global --get-regexp '^hook\.no-coauthor\.'` prints nothing.
Report what failed, with the output, and stop.

## Notes for maintainers

Not part of the steps: when running this runbook, stop at the end of step 5
(or 6). These record why it is written the way it is. Read them before
changing it.

- **Why it exists.** A session in the convkb project showed the model writing
  `Co-Authored-By` into a commit message by itself, with Claude Code's
  `attribution` settings off and nothing in context asking for it. Settings
  can't stop text the model chooses to write, so the check sits in git.
- **Config-based hooks, not `core.hooksPath`.** Git 2.54 added hooks defined
  as `hook.<name>.command` plus `hook.<name>.event`. Several can share an
  event; they run in config order, and the repository's own hook script runs
  last. A global `core.hooksPath` would instead replace every repository's
  hooks folder, silently disabling their hooks, and repositories that set
  their own `hooksPath` (husky) would skip it. Per-clone `.git/hooks`
  files would have to be installed in every clone and chained by hand.
- **Older git ignores `hook.*` without a word,** so step 1 is a hard gate,
  and step 4 tests a real commit rather than trusting the config.
- **What it matches.** A line beginning with `Co-Authored-By` and a colon,
  any case, after optional spaces: the trailer form. Prose that mentions the
  phrase is allowed, because commit messages in this very repository talk
  about removing the trailer. Lines starting with `#` are skipped: the
  `commit-msg` hook runs before git strips the editor's comment lines.
- **`sh ~/...`, not a bare path.** Git runs the command through a shell, so
  `~` expands there, and Git for Windows sets `HOME` even when git is started
  from PowerShell. Calling `sh` explicitly means the script needs no
  executable bit or shebang handling. A CRLF script would make `sh` fail on
  every commit, hence the carriage-return check, and step 4's clean commit
  catches anything else that breaks every commit.
- **The rejection message addresses Claude.** It says to remove the line and
  not to use `--no-verify`, because the model reads the hook's output and
  retries. `--no-verify` is the known gap; a Claude Code `PreToolUse` hook
  could close it, but was left out to keep this to one mechanism.
- **The test pins `-c commit.gpgsign=false` and an identity** so it doesn't
  depend on the user's signing setup or a configured name; the throwaway
  repository is deleted straight after.
- **Verified** on Windows on 2026-10-14 with Git for Windows 2.55.0.windows.5
  (upgraded from 2.47.0 with `winget upgrade`), against a throwaway global
  config: rejected `-m`, `-F`, `--amend` and lower-case or indented
  trailers; allowed prose; the repository's own `.git/hooks/commit-msg`, and
  a husky-style local `core.hooksPath`, still ran; `--no-verify` bypassed it.
  The runbook itself was then run for real on the same machine, steps 0 to 5,
  against the user's own `~/.gitconfig`.
