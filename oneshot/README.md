# oneshot

These are setup runbooks you run once, each a single Markdown file. Nothing
here is installed as a skill or command: copy a prompt below into a Claude
Code session, and Claude fetches the current version, goes over it with you,
and runs it once you agree.

## Claude Code settings

[CLAUDE_SETTINGS.md](CLAUDE_SETTINGS.md). Adds four user-level Claude Code
preferences (no attribution in commits or PRs, transcripts kept, no spinner
tips, no feedback survey), asking before each, then validates the settings
file.

```
Download https://raw.githubusercontent.com/vector76/skills-public/main/oneshot/CLAUDE_SETTINGS.md verbatim, read all of it, and tell me what it will change on this machine and anything that looks risky or out of place. Wait for my go-ahead, then follow the copy you downloaded.
```

## Install the Claude Code CLI

[INSTALL_CLAUDE_CLI.md](INSTALL_CLAUDE_CLI.md). Installs the `claude`
command-line tool, which the desktop app doesn't provide, with the native
installer, and walks you through signing it in.

```
Download https://raw.githubusercontent.com/vector76/skills-public/main/oneshot/INSTALL_CLAUDE_CLI.md verbatim, read all of it, and tell me what it will change on this machine and anything that looks risky or out of place. Wait for my go-ahead, then follow the copy you downloaded.
```

## Install or update the usage dashboard

[INSTALL_USAGE_DASHBOARD.md](INSTALL_USAGE_DASHBOARD.md). Installs or updates
the Claude Usage Dashboard tray app on Windows (`go install`, autostart at
logon, a quota source), and confirms it is serving on `localhost:27812`. Needs
the Claude Code CLI; it checks first. Run it again to update.

```
Download https://raw.githubusercontent.com/vector76/skills-public/main/oneshot/INSTALL_USAGE_DASHBOARD.md verbatim, read all of it, and tell me what it will change on this machine and anything that looks risky or out of place. Wait for my go-ahead, then follow the copy you downloaded.
```

## Install the jamie plugin

[INSTALL_JAMIE_PLUGIN.md](INSTALL_JAMIE_PLUGIN.md). Installs the `jamie`
plugin from the `vector76-skills-public` marketplace. Once it is installed,
`/jamie:update` updates it whenever you run it.

```
Download https://raw.githubusercontent.com/vector76/skills-public/main/oneshot/INSTALL_JAMIE_PLUGIN.md verbatim, read all of it, and tell me what it will change on this machine and anything that looks risky or out of place. Wait for my go-ahead, then follow the copy you downloaded.
```

## Remove the jamie plugin

[REMOVE_JAMIE_PLUGIN.md](REMOVE_JAMIE_PLUGIN.md). Uninstalls `jamie`, or, for
a clean slate, also removes the marketplace and offers to delete the
leftovers.

```
Download https://raw.githubusercontent.com/vector76/skills-public/main/oneshot/REMOVE_JAMIE_PLUGIN.md verbatim, read all of it, and tell me what it will change on this machine and anything that looks risky or out of place. Wait for my go-ahead, then follow the copy you downloaded.
```

## Adding one

A runbook belongs here when it is:

- **One file.** If it needs `reference/` files or scripts beside it, it is a
  skill.
- **Run on purpose.** If it should trigger on its own, or recur, it is a skill.
- **Self-guarding.** It says what it's for, asks before each change, backs up
  what it edits, and verifies the result. Maintainer notes go at the end,
  under `## Notes for maintainers`, opening with a line telling the running
  session to stop before them.

Give each new runbook a section above, with the same prompt and its file name.
