# Changelog

What changed in the plugins, newest first. One entry per release, dated, with a
line for each plugin it ships:

    ## YYYY-MM-DD

    - **<plugin> <version>:** what changed, for someone invoking its skills

Each plugin has its own version, so a release lists only the plugins it bumped.
Name the effect on the skills, not the files touched.

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
