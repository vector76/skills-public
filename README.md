# skills-public

Jamie Kawabata's public Claude Code plugins, published as the marketplace
`vector76-skills-public`. It holds one plugin, `jamie`.

## What's in `jamie`

| Invoke | What it does |
|---|---|
| `/jamie:check-again` | Asks Claude to take a second look at the work so far for mistakes or a better approach, and fix what it finds. |
| `/jamie:draw-the-owl <document>` | Fills the gaps in an underspecified feature document, updates it, and lists each assumption it made, most significant first, for you to approve. Stops before implementing. |
| `/jamie:update` | Updates this plugin to its latest published version, and says whether the session needs `/reload-plugins`. |
| `/jamie:systematic-code-review` | Runs a deep, resumable audit of a whole codebase or subsystem: one Opus subagent per unit, a second agent that tries to refute each serious finding, a tiered summary, and an HTML site linking every finding to its line on Azure DevOps or GitHub. Asks before it starts sweeping; Windows only. |
| `/jamie:rebase-findings` | Moves a delivered `systematic-code-review` to a newer commit: re-anchors every reference, re-checks the findings the new commits could affect, and reviews the new code. Asks once, then leaves one uncommitted diff. |
| `/jamie:fix-findings` | Fixes findings from a `systematic-code-review`, by ID, by filter or all of them, one at a time in the current session: harnesses, seams, red-green fixes, and fixes no test can prove. Each is checked by fresh agents and committed on a local branch. Asks once, up front, then runs unattended; never pushes. Windows only. |
| `ray-workflows` | Guidance for writing Raymond (`ray`) workflows. Claude loads it on its own when you work on one; `/jamie:ray-workflows` loads it explicitly. |

## Install

```bash
claude plugin marketplace add vector76/skills-public
claude plugin install jamie@vector76-skills-public
```

Then start a new session — an open one keeps the copy it started with.

For one project only, run both commands inside that project with
`--scope project` and commit its `.claude/settings.json`.

## Staying up to date

Run `/jamie:update` in any session to update to the latest version. Or by
hand:

```bash
claude plugin marketplace update vector76-skills-public
claude plugin update jamie@vector76-skills-public
```

Then type `/reload-plugins`, or start a new session.

Claude Code can also update plugins by itself, but for a third-party
marketplace like this one, auto-update is **off by default**, and turning it
on is your call: later changes then arrive without you looking at them. To
turn it on, open `/plugin`, go to the marketplaces list, and enable
auto-update for `vector76-skills-public`. In the desktop app, which has no
toggle for it, add `"autoUpdate": true` to this marketplace's entry under
`extraKnownMarketplaces` in `~/.claude/settings.json`. The desktop app also
starts sessions with the auto-updater disabled, which blocks plugin updates
unless `"FORCE_AUTOUPDATE_PLUGINS": "1"` is set in that file's `env` object.
That setting applies to every third-party marketplace you have, not just this
one.

## Runbooks

[oneshot/](oneshot/README.md) holds one-time setup runbooks — installing the
Claude Code CLI, user settings, the usage dashboard, installing or removing
this plugin. You don't install them; you paste the prompt for one from that
page into a Claude session.

## Work on it

Edit the files with anything. To try an edit, start a session that loads the
working tree directly — no install, no version bump:

```bash
claude --plugin-dir ./plugins/jamie
```

To try it in another project, run `bin/try.sh` (or `bin\try.cmd`) by its full
path from that project. See [CLAUDE.md](CLAUDE.md) for the layout rules and
the release steps, and [CHANGELOG.md](CHANGELOG.md) for what changed.
