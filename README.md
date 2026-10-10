# skills-public

Jamie Kawabata's public Claude Code plugins, published as the marketplace
`vector76-skills-public`. It holds one plugin, `jamie`.

## What's in `jamie`

| Invoke | What it does |
|---|---|
| `/jamie:check-again` | Asks Claude to take a second look at the work so far for mistakes or a better approach, and fix what it finds. |
| `/jamie:draw-the-owl <document>` | Fills the gaps in an underspecified feature document, updates it, and lists each assumption it made, most significant first, for you to approve. Stops before implementing. |
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

Claude Code can update plugins by itself, but for a third-party marketplace
like this one, auto-update is **off by default**. To turn it on, open
`/plugin`, go to the marketplaces list, and enable auto-update for
`vector76-skills-public`. In the desktop app, which has no toggle for it, add
`"autoUpdate": true` to this marketplace's entry under
`extraKnownMarketplaces` in `~/.claude/settings.json`. The desktop app also
starts sessions with the auto-updater disabled, which blocks plugin updates
unless `"FORCE_AUTOUPDATE_PLUGINS": "1"` is set in that file's `env` object.

To update by hand:

```bash
claude plugin marketplace update vector76-skills-public
claude plugin update jamie@vector76-skills-public
```

Then start a new session.

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
