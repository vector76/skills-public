# Changelog

What changed in the plugins, newest first. One entry per release, dated, with a
line for each plugin it ships:

    ## YYYY-MM-DD

    - **<plugin> <version>:** what changed, for someone invoking its skills

Each plugin has its own version, so a release lists only the plugins it bumped.
Name the effect on the skills, not the files touched.

## 2026-10-21

- **jamie 0.5.0:** `/jamie:fix-findings` fixes from its first runs. It can
  run directly on the branch you have checked out (`--in-place`) and make one
  commit per finding (`--commits one`). In private mode it marks each fixed
  finding in the review as soon as the fix is committed, naming the branch and
  commits, and re-renders the review site after every fix;
  `/jamie:systematic-code-review`'s reconcile later records where it landed.
  A bounced finding comes back through a follow-up run (`init --like`) that
  reuses the earlier run's settings. Fixers are told their red test must
  build without the fix. Go projects are supported: a package-level target
  command and a bundled related-tests command. The checks on a fix and on a
  review round, and filling in each subagent's prompt, are now script
  commands, so every run applies them the same way.

## 2026-10-19

- **jamie 0.4.0:** new `/jamie:fix-findings`, which fixes findings from a
  `systematic-code-review` one at a time in the current session: chosen by
  ID, by filter, or all of them, of any severity, Fix impact or RGR,
  cross-cutting `X-` harnesses included. Each fix is red-green where a test
  can prove it, checked by a fresh agent and fresh-eyes review rounds, and
  committed on a local run branch, prerequisites first. Every question comes
  before one go-ahead, the usage throttle paces each task, and a stopped run
  resumes from disk. `/jamie:systematic-code-review` now points to it, and
  reconciles its private runs as it does fix-lanes runs.

## 2026-10-18

- **jamie 0.3.1:** `/jamie:systematic-code-review` fixes from its first full run.
  The HTML site no longer crashes when `render.json` gives `"subprojects": []`;
  an empty list now means none, as leaving the key out does. Verifiers are told
  exactly where the Verification bullet goes and that headings and blank lines
  are never touched, after one deleted the next finding's heading. Reviewer
  prompts list every required bullet and the allowed categories, which keeps
  the shortened prompt from dropping one.

## 2026-10-18

- **jamie 0.3.0:** new `/jamie:systematic-code-review`, which runs a
  resumable, usage-throttled audit of a large codebase with one Opus subagent
  per unit, adversarial verification of the serious findings, and a browseable
  HTML site that links each finding to its line on Azure DevOps or GitHub. New
  `/jamie:rebase-findings`, which moves a delivered review to a newer commit:
  it re-anchors every reference, re-checks the findings the new commits could
  affect, and reviews the new code. Both need Windows with PowerShell. The
  review's usage throttle can use the cc_usage_dashboard tray app, another
  usage script, or no usage gate at all.

## 2026-10-10

- **jamie 0.2.0:** new `/jamie:update`. It updates the plugin to its latest
  published version, reports the old and new versions, and says whether the
  session needs `/reload-plugins`. It never changes auto-update settings; the
  install runbook no longer turns auto-update on either, so that is left to
  you.

## 2026-10-10

- **jamie 0.1.0:** first release as an installable plugin, from the
  `vector76-skills-public` marketplace. The skills are now invoked as
  `/jamie:check-again`, `/jamie:draw-the-owl`, and `/jamie:ray-workflows`.
  `check-again` and `draw-the-owl` are now commands, with the same prompts.
