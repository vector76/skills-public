# Working in skills-public

This repo is a Claude Code plugin marketplace, `vector76-skills-public`, with
one plugin, `jamie`. Editing it has one non-obvious rule: **an edit here does
not reach any normal session**, including this one.

Installed plugins, the author's included, are copies of what was last *pushed*,
cached under their version number. So there are two separate loops.

## Testing an edit: `--plugin-dir`

```bash
claude --plugin-dir ./plugins/jamie
```

That session loads the plugin straight from the working tree — uncommitted
edits included, no install, no bump — and takes priority over the installed
copy, for that session only. Editing can happen anywhere; only this testing
session has to be started from a terminal (the desktop app can't pass the
flag). Start a new one after each round of edits.

**Testing in another project.** Many skills only make sense in the project
they act on, not in this repo. `--plugin-dir` takes any path, so start the
session from that project and point it back here. `bin/try.sh` and
`bin/try.cmd` do that from wherever they are run:

```bash
cd ~/code/some-project
bash ~/path/to/skills-public/bin/try.sh        # extra arguments go to claude
```

```
cd C:\code\some-project
C:\path\to\skills-public\bin\try.cmd
```

The edits still happen here, in this repo; the other project only hosts the
session. Nothing is written to that project's settings, so there is nothing to
undo there afterwards.

Invoking a skill in an ordinary session runs the **released** version. When an
edit "does nothing", check which session you are in before debugging content
that was never loaded.

## Releasing: verify, bump, changelog, commit, push

1. `bash bin/verify.sh` — fix every `FAIL`; read every `WARN`.
2. `bash bin/bump.sh jamie` — patch by default; `minor` for a new skill or
   command, `major` for a restructuring.
3. Add a dated entry to `CHANGELOG.md` naming `jamie <new version>`. If a
   skill or command was added, removed, or renamed, update the table in
   `README.md` too. The plugin's own description deliberately lists none, so
   it never goes stale.
4. `bash bin/verify.sh` again, then commit the change, the bump, and the
   changelog entry together.
5. Push — only on an explicit yes in the moment.

- **The version lives in `plugins/<plugin>/.claude-plugin/plugin.json`** and
  nowhere else.
- **Versions must move forward.** An install refreshes only when the version
  string changes, so a push without a bump, or with a reused or lower number,
  refreshes nothing.
- **Never push unprompted.** Commit locally; a push needs an explicit yes.

## Layout rules

- `plugins/jamie/skills/<name>/SKILL.md` — skills: step-by-step playbooks, or
  reference guidance. The frontmatter `name:` must match the directory. Long
  material goes in `<name>/reference/*.md`, linked from `SKILL.md`, so it loads
  only when needed.
- `plugins/jamie/commands/<name>.md` — short one-file prompts, invoked as
  `/jamie:<name>`.
- Every skill sets `disable-model-invocation` explicitly. A model-invocable
  skill's description says only *when* it applies ("Use when …"), because it
  rides in context in every session where the plugin is enabled.
- Everything under a plugin's `skills/` and `commands/` is live. Templates and
  notes go outside them; a plugin registers *every* `.md` under `commands/` as
  a command, `README.md` included.
- No HTML comments in anything under `plugins/`. The whole file loads into the
  model, so a hidden note is read as an instruction. Notes for maintainers go
  in a `README.md` in the skill's folder, which nothing links to; a command
  has no folder, so its notes go in `plugins/jamie/README.md`.
- A new plugin needs an entry in `.claude-plugin/marketplace.json`, or it ships
  to nobody. `bin/verify.sh` fails on that.

## Runbooks: `oneshot/`

Something a person runs once — install a tool, configure a machine — is a
runbook, not a skill: a single Markdown file in `oneshot/`, outside
`plugins/`. Nothing installs it, it costs no context and no `/` menu entry,
it needs no release, and it never goes stale, because it is read fresh each
time. [oneshot/README.md](oneshot/README.md) is the index, with a
copy-paste prompt for each runbook, and has the test for what belongs there.
Add each new runbook to it, with the same prompt.

Keep that prompt's wording; each part is there for a reason, and the README
deliberately doesn't explain it:

- **Review before running.** Following a downloaded file is close kin to
  `curl | bash`, and Claude is right to hesitate or refuse. Having it report
  what will change, and anything out of place, then wait for a go-ahead, puts
  the trust decision with the user, in the open, before anything runs.
- **"Verbatim".** A summarizing web fetch can drop steps and
  `[ STOP / ASK ]` gates.
- **"The copy you downloaded".** What runs is what was reviewed, not a second
  fetch.

`bin/verify.sh` checks only the links in `oneshot/`, so try a runbook before
pushing it: once pushed, an edit is live for the next person who runs it.

## This repo is public

Everything here, history included, is visible to anyone. Don't add anything
that shouldn't be.
