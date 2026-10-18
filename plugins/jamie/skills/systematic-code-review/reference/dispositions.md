# Recording dispositions

Use this once a review has been delivered, when the user asks to record what
happened to findings: fixes that landed, findings moved to tickets, findings
accepted, or findings shown to be wrong. The statuses and their exact bullet
forms are in [scales.md](scales.md). This file covers how to apply them.

## What a disposition is

A disposition is a decision about a finding, not the work that follows it.
**Planned** is the case this distinction matters for. It means the finding
now lives in another system that owns it, usually a ticket. As far as this
review is concerned it is handled. When the ticket is resolved it may be
updated to Fixed, but it doesn't have to be. Nobody has to come back to the
review to close it out.

That is why every status except Open draws a hollow square on the index. The
solid squares are what still needs someone to decide. The goal of a
disposition pass is no solid squares, not everything fixed.

## Rules

- **Only the Status bullet changes.** Leave the description, suggested fix,
  Verification, severity, fix impact, and every `file:line` as they are. They
  describe the pinned commit, and the links point there. A finding whose text
  follows the code afterwards no longer matches either. Moving the pin, and
  the text with it, is the `rebase-findings` skill's job, never a
  disposition's.
- **Every status except Open carries its evidence** on the same bullet, in the
  form [scales.md](scales.md) gives:
  - **Fixed**: the commit sha, and one clause on what changed and which test
    covers it. Write the sha in backticks and the renderer links it to the
    commit. The line numbers belong to the pin, so cite the commit, not lines.
    For a fix made red-green, name the test that failed before the fix and
    passes after it. For an RGR `No`, name who verified the fix and how, since
    no test could. The one exception to citing a sha is `Fixed — in this
    commit`, written by `fix-light` into the fix commit itself when the
    review is committed alongside the code. Don't rewrite those.
  - **Fixed, for a partly testable finding**: its test, as above, and also
    the check of its residual: what was done, who did it, and the date. Until
    the residual is checked, a passing test doesn't make the finding Fixed.
    Leave it Open with the test noted (`Open — test_x passes; residual
    unchecked`), or Planned with the ticket for the residual check.
  - **Planned**: the ticket ID or URL, or the plan document. A Planned without
    a reference is an Open with a different label.
  - **Won't fix**: the reason, who decided, and the date. A later review
    reads this to avoid reporting the same thing again.
  - **Not a defect**: the evidence, as specific as a REFUTED verdict:
    the guard, the constraint, or the `file:line` that shows it.
- **Fixed needs a real defect.** Mark a finding Fixed only if it was
  CONFIRMED, or whoever fixed it confirmed the defect against the code first.
  A fix to an unverified Low or an UNCERTAIN finding may have fixed nothing. If
  it was never confirmed, say so in the evidence.
- **Changing a disposition keeps the old one**, at the end of the bullet:
  `(was Planned — <ticket>)`. Moving a finding back to Open keeps the old one
  the same way, with the reason.
- **The user makes the decisions.** Record Won't fix and Not a defect only when
  the user says so, or when a document they point to (a triage, a ticket
  export) says so. Never infer a disposition from the code having changed:
  code that moved is not a fix.

## Where the evidence comes from

Record from something the user gives or points to, not from memory:

- **Fixes** — the commit and what it changed. Look at it before recording it.
  A fix campaign's own notes, such as a per-unit fixes log, are a good source
  when they exist. They are the user's working files, so leave them as they
  are.
- **Tickets** — the IDs the user gives, or an export they point to. Record the
  reference only. Don't copy ticket content into the review.
- **A private `fix-light` or `fix-seam` run** — reconcile it, as below.

## Reconciling a private fix run

Use this when the user asks to record the fixes from a `fix-light` run made
in **private** mode. That mode keeps the review out of the project: the fixes
were submitted as ordinary commits, every finding was left `Open`, and the
run's `queue.md` (in `<review dir>/fixes/<run>/`) lists each fixed finding
with its test, as `<test file>::<test name>`, its local shas, and an empty
`Landed` column. Fixes usually land through a PR, often squash-merged, so the
local shas are gone. The test name survives any squash or rebase, and it is
how the landed fix is found.

Ask for the run directory, or runs, and the **target branch** the fixes land
on, such as `origin/develop`. Reconcile reads the target as it is locally, so
ask the user to fetch first, or fetch with their yes. Apart from that fetch,
reconcile only reads the code repository.

For each finding the queue marks `fixed` with no `Landed` value:

1. **Find where the test landed**:
   `git log <target> --format=%H -S '<test name>' -- <test file>`, oldest
   first. If nothing turns up, try without the path, in case the file moved.
   Still nothing: it hasn't landed. Set `Landed` to `not yet`, record nothing,
   and go on.
2. **Find the fix.** If that commit also changes the production files in
   `records/<ID>.fix.patch`, it is the fix, squashed together with its test.
   Otherwise the branch was merged or rebased: find the commit on the target
   whose `git patch-id --stable` matches the local fix commit's. If neither
   works, the fix changed on its way in. Show the user, and record nothing
   for it.
3. **Check it is on the remote**: `git branch -r --contains <sha>` lists the
   target. A sha that isn't there would link nowhere.
4. **Look at it before recording it.** Compare the landed production change
   with `records/<ID>.fix.patch`. If review changed the fix in substance,
   show the user, and record only on their yes.
5. **Record it**: rewrite the Status bullet as
   `Fixed — \`<landed sha>\` — <what changed, from the run's record>; red-green: <test file>::<test name>`,
   adding `; defect confirmed by the red test` when the finding had no verdict
   or was UNCERTAIN. Set the queue's `Landed` to the sha.

Several findings squashed into one PR all cite the same sha, which is
correct. If the user says a PR was abandoned, set `Landed` to `abandoned`, and
leave the finding Open: it can go into another run. Bounced findings are
never reconciled; they were never submitted.

Log it as for any disposition batch, for example `Reconciled fix-run-1
2026-10-12: 7 Fixed, 3 not yet`.

**A `fix-seam` run** built a harness for one `X-` finding. Its queue has a
Probe, `<file>::<string>`, in place of a test, and a Proof naming the
dependent whose test proved it. It writes no `.fix.patch`: its local
commits, a production refactor and test infrastructure, one or both, are in
the queue's Commits. Reconcile it the same way, with the probe's string where
the steps use the test name, and those local commits where steps 2 and 4 use
the fix patch: the commit that added the probe is the harness if it contains
both local commits' changes, squashed; otherwise match each local commit by
patch-id. Record it as
`Fixed — \`<landed sha>\` — <the harness, from the run's record>; proven: <dependent ID>'s test failed for its reason`,
citing the later sha when the two landed separately.

**A `fix-lanes` run** writes one queue for many kinds of task, with its
outcome in the Status column, its local commits in Commits, the review
rounds that ran in Rounds, and an empty `Landed` column. Its records are in
`fixes/<run>/records/`, where `<ID>.final.patch` is everything the task
landed; compare with it wherever the steps above use `.fix.patch`. Reconcile
its rows by outcome:

- **`fixed`**: by its Test, exactly as above.
- **`fixed, residual without a test`**: by its Test, as above, adding
  `; residual not checked by a test: <the residual, from the record>`.
- **`fixed without a test`**: by its Probe, `<file>::<string>`, the way a
  harness is found, matching its local commits by patch-id. Record
  `Fixed — \`<landed sha>\` — <what changed>; fixed without a test, reviewed by a checker and <Rounds> fresh-eyes rounds`.
- **`fixed (harness)`**: as a `fix-seam` run, by its Probe, with its Proof.
- **`gone`, `not a defect` and `present`** have no commit to find: one of the
  run's agents found them, a second confirmed it, as a verifier refutes a
  finding, and the run's report listed them. Record them from the row's Note, the
  evidence: `Fixed — no longer present when the fix run reached it — <evidence>`,
  `Not a defect — refuted by fix run: <evidence>`, or
  `Fixed — already present when the fix run reached it — <where>`.
- **Bounced and blocked** rows are never reconciled.

## After recording

- **Log it.** Append one line to the `## Log` in `backlog.md` for each batch,
  for example `Dispositions 2026-10-02: 12 Fixed, 5 Planned, 1 Won't fix — from
  the fix campaign notes`. The log is how the review stays auditable after
  delivery.
- **Leave `SUMMARY.md` as delivered.** It records the review at delivery, and
  its totals, tiers, and themes are the review's result. Editing its tables
  as statuses change is extra work, and the tables drift. The rendered index
  counts statuses from the findings docs on every render, so it is the live
  view. If the user wants a narrative of the disposition work, it goes in its
  own document, linked from the index and not folded into the summary.
- **Re-render**, per [html-rendering.md](html-rendering.md). Pass `index` and
  the findings docs you changed, or render everything.
- **Read the warnings.** The generator reports findings with no Status bullet
  or a status it doesn't recognise. Either one means the doc drifted, and the
  finding is being counted as Open.

## A review written before the Status field

An older review has no Status bullets, and the index draws every finding solid,
exactly as before. To record dispositions in it, first give every finding a
Status bullet with this skill's
[scripts/add_status_bullets.py](../scripts/add_status_bullets.py), run in
place like the renderer:

```powershell
python <skill dir>\scripts\add_status_bullets.py <review dir>           # dry run
python <skill dir>\scripts\add_status_bullets.py <review dir> --write
```

It adds `Open` to every finding and `Not a defect — refuted at verification`
to each REFUTED one. It converts a `**Fixed:**` bullet, the form one review
used before this scale existed, to `Status: Fixed`, keeping its evidence. It
refuses to write unless every doc comes out with one Status bullet per
finding. Show the user the dry run's counts before writing. If the review
recorded dispositions some other way, convert those by hand first.
