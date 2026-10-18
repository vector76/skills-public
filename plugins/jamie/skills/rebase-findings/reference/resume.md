# State, and resuming a rebase

## state.md

`<review dir>/rebase/state.md`, written at the go-ahead:

```markdown
# Rebase <P sha7> -> <P' sha7>

## Parameters
<review dir, code root, output mode, P and P' (full shas), mode, batch size,
usage gate, the review directory relative to the code root (in-tree), the
commit listing (first-parent or non-merge), date, and any code root change
agreed at the gate>

## Commits
| n | sha | subject | changed lines | job |

## Jobs
| Job | Step | Inputs | Status |

## Flagged
| ID | Doc | Why | Job | Outcome |

## Reconcile pending
<IDs, or none>

## Steps
- [ ] diffs written
- [ ] summaries done
- [ ] screen done, flagged set built
- [ ] re-anchored; README, backlog Files and harness updated
- [ ] evaluations done
- [ ] new code done (or: skipped, adjust only)

## Log
```

`Step` is `summarize`, `screen`, `evaluate`, `delta`, `merge`, `unit`,
`verify` or `x-links`. Statuses are `pending | in-progress | done`, set as
the review sets them: `in-progress` before spawning, `done` only on the
step's done rule.

## Resuming

Use this when `<review dir>/rebase/state.md` exists. Work through these in
order. Each passes or stops the resume.

1. **Lock.** If `rebase/RUN.lock` exists, show its start and heartbeat times
   and ask the user to confirm the session that owns it has stopped. Never
   take a lock over without that, however old it looks. Then replace it with
   one for this session.
2. **Load, don't re-ask.** Every parameter is in `state.md`. Use them as they
   are.
3. **Check the world.**
   - The code root is still at P' and clean: both checks in the review
     skill's [tree identity reference](../../systematic-code-review/reference/tree-identity.md),
     with P'. If not, stop and say what is needed.
   - Until *screen done* is checked, the review's files are as they were:
     `git -C <review dir> status --porcelain -- findings SUMMARY.md render.json README.md backlog.md`
     prints nothing. After that, step 6 may have started, and changes are
     the run's own: step 4 below sorts out a re-anchor cut short, and the
     script itself refuses to write over changed files.
   - The usage gate: run `throttle.bat` once without `-q`, and show it.
4. **Repair what a crash left.**
   - **Summary and screen jobs** `in-progress`: if the output ends with
     `## Done`, mark the job done, `recovered on resume`. Otherwise delete the
     output and set it `pending`.
   - **Re-anchor cut short**, *re-anchored* unchecked: if `render.json` still
     holds P, the script stopped before moving the pin. Restore the files it
     writes, `git -C <review dir> checkout -- findings SUMMARY.md README.md render.json`,
     which were clean when it ran, and run step 6 again. If `render.json`
     holds P', the script finished: do step 6's hand edits that aren't done.
   - **Evaluation, merge and x-links jobs** `in-progress`: restore the job's
     docs from `rebase/before/<job>/` (`merge-<unit>/`, `x/`), delete its
     output if any, and set it `pending`. It runs again from the start.
   - **Delta and new-unit reviewers** `in-progress`: if the doc has a
     `## Coverage notes` section, mark it done, `recovered on resume`.
     Otherwise delete it and set it `pending`.
   - **Verify jobs** `in-progress`: as the review's resume does. Name the
     findings that already have a verdict in the re-run prompt, and never
     let one end with two Verification bullets.
5. **Carry on** from the first unchecked step in `## Steps`, with any
   `pending` jobs in it. Log one line, such as
   `Resumed 2026-10-07: E03 restored and reset`.
