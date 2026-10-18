# Resuming a review

Use this when step 0 establishes that the user is continuing an existing review,
whether it stopped cleanly, paused, or crashed. Everything you need is in
`<review dir>`. Nothing about the run is kept anywhere else, so without the
directory there is nothing to resume. **Ask the user for it; never guess by
searching.**

Work through these in order. Each one either passes or stops the resume.

## 1. Confirm it is a review directory

`<review dir>` must hold `README.md` (with a `## Run parameters` section) and
`backlog.md`. If either is missing, this is not a resumable review. Say what is
there and stop; do not rebuild it.

If `README.md` has no `## Run parameters` section, the review predates this
procedure. Recover the parameters from `README.md` and the prompts implied by
the findings, show them to the user, confirm them, and write the section before
continuing.

## 2. Make sure no other session is running it

If `RUN.lock` exists, a session either crashed or **is still running this
review**. It might be paused in its watcher, or waiting on a batch of
subagents that can run for tens of minutes. Taking over a live run is the one
thing a resume can make much worse: repairing its `in-progress` units deletes
docs that are still being written, and two orchestrators then hand out the same
units.

Show the user the lock's start and heartbeat times, and ask them to confirm
that the session that owns it has stopped. They can check or close the other
session. **Never remove a lock without that confirmation, however old it
looks.** A paused run's heartbeat can be hours old. Once they confirm, replace
the lock with one for this session and continue.

If there is no lock, create one for this session.

## 3. Load the parameters, don't re-ask them

Read `## Run parameters` and `## Reviewed tree` from `README.md`. They hold the
step 0 answers, the scope confirmed at step 1, and the reviewed commits. Use them
exactly. **Do not ask the user again for anything recorded there.** Answers that
differ from the first half of the run would split the review into two reviews
with different premises. If the user *wants* to change a parameter, that is
their call. Record the change and the date in `## Run parameters`, and say in
the method-notes appendix at step 6 which units ran under which value.

**The scales and finding format are the ones in the review's own
`README.md`**, not this skill's current
[scales.md](scales.md). A review started before a field existed, such as RGR
or Status, finishes without it, and its prompts carry the README's copies.
Adding a field partway through gives half the findings a bullet the other half
lack, which the step 6 cross-checks then report as drift. If the user wants
the field anyway, that is a parameter change as above, and the units already
done need it added before step 6.

Show the user a short summary of the parameters and where the run stands (from
section 5 below) before doing anything else.

## 4. Check the world still matches

- **The code tree.** Check that the code on disk is exactly what was
  recorded in `## Reviewed tree`, using the commands in
  [tree-identity.md](tree-identity.md). For a git repository, run **both**
  checks there: that the committed code matches the pin, and that the working
  tree is clean. Reviewers read the working tree, so an uncommitted edit
  matters as much as a new commit. Compare code, not commits: in in-tree mode,
  committing the review moves `HEAD` and leaves the code untouched, and the
  review's own files would make the tree look dirty. Both commands leave the
  review directory out. For code that is not in git, recompute the tree hash
  and compare it.

  **If the code has changed, stop and ask.** Findings written against two
  different trees cannot share line numbers. The fix is to check out the
  recorded commit (in-tree mode, with the user's go-ahead) or to review from a
  separate checkout of it. Never quietly carry on against the new tree. In
  external mode the code root is read-only, so you cannot check anything out
  yourself; tell the user what is needed.
- **The usage gate.** Re-run the step 0 test: run the usage-check script once
  without `-q` and show the output. Then run `throttle.bat` in both states, as
  in step 3. The trayapp may not be running after a reboot. The rules for exit
  2 are the same as at startup.

## 5. Find where the run stands

Read it from the files, in this order. The first match is the step to resume at:

| State on disk | Resume at |
|---|---|
| `backlog.md` has no `Go-ahead given` line in its `## Log` | step 3's gate: re-test the throttle and ask for the go-ahead again |
| any unit `pending` or `in-progress` | step 4, after repairing in-progress units below |
| no `## Verification queue`, or any verify job not `done` | step 5, after repairing in-progress jobs below |
| no `SUMMARY.md`, or no `SUMMARY written` line in the log | step 6 |
| `SUMMARY.md` done, and `html/index.html` missing or older than it | step 7: the final render |
| otherwise | the review is complete; say so. Recording what happened to its findings is [dispositions.md](dispositions.md), when the user asks |

## 6. Repair what a crash left behind

An `in-progress` status means work was handed out and never recorded as
finished. The subagent may have finished, died partway, or never started.

**Units.** For each `in-progress` unit:

- If its findings doc exists and contains a `## Coverage notes` section, the
  subagent finished and only the bookkeeping was lost. Mark it `done`, count its
  findings, and log it as `recovered on resume`.
- Otherwise delete the partial doc, if there is one, and set the unit back to
  `pending`. It will be swept again from scratch. A half-written doc is never
  extended; a new subagent cannot know what the old one skipped.

**Verify jobs.** For each `in-progress` job, check every Critical, High, and
Medium finding in its docs for a `- **Verification:**` bullet.

- If all have one, mark the job `done` and log it as `recovered on resume`.
- Otherwise set it back to `pending`, and in its re-run prompt name the findings
  that already have a verdict. The new verifier should leave those alone and
  only verify the rest. **Never let a finding end up with two Verification
  bullets.** Step 6's count of verdicts must equal the number of findings
  that are not Low, counting each by its severity before any verifier changed
  it.

Log one line summarising the repair (for example,
`Resumed 2026-09-24: U14 recovered, U15 reset to pending`) before the loop
starts again.

## 7. Carry on

Continue the playbook from the step found in section 5, with its gates intact.
A resume never skips a gate that the log does not show as already passed.
