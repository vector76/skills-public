---
name: rebase-findings
description: Move a delivered systematic-code-review to a newer commit of the branch it reviewed. Re-anchors every file:line to the new pin, screens what each new commit does against every Open finding, re-evaluates the findings a commit could affect (still there, changed, or gone), and by default reviews the new code for new findings. Leaves one uncommitted diff in the review directory. Asks once, up front, then runs unattended. Never commits or pushes.
disable-model-invocation: true
---

# Rebase findings

You are running the **rebase-findings playbook**. It takes a review written by
the `systematic-code-review` skill, pinned to commit P, and moves it to a
newer commit P' of the same branch. Afterwards the review reads as if it had
been written against P': every reference points at P', and every Open finding
describes P'. It has eleven steps, 0 to 10, run in order, and **one** gate
(**[ STOP / ASK ]**), the go-ahead at step 2.

**Four rules define this playbook:**

1. **What a commit does decides what gets re-evaluated, not which lines it
   touched.** A finding's effect can change while none of its lines do: a
   caller now validates the input, a default changed, a type narrowed. So
   every Open finding is screened against a summary of what each commit does,
   and line overlap is only the one signal that always flags.
2. **The findings carry no history of the rebase.** No per-rebase bullets, no
   commit notes, no "previously". A finding that changed is rewritten to
   describe P', with the review's usual `(verifier: was …)` markers on any
   rating it changed. Everything the run works from is scratch, deleted at the
   end. The record is the new pin and one line in the log.
3. **It is fire-and-forget after the gate.** Every question is asked before
   it, and the gate comes before anything slow. Afterwards nothing asks the
   user anything: each condition either lets the run go on or stops it with a
   report. Never add a question after the gate.
4. **All state lives on disk**, in `<review dir>/rebase/`, until the run
   finishes. A fresh session must be able to continue from it alone.

**The run's output is an uncommitted diff in the review directory**, and that
diff is the user's review of the rebase. Never commit, never push, and write
nothing outside `<review dir>`. The code root is read-only, apart from the one
checkout step 1 may offer.

## Prerequisites

Confirm before step 0, and stop if any is missing:

- **The Agent tool**, with subagents runnable on **Opus**. Every spawn sets
  `model: "opus"` explicitly.
- **Windows, with PowerShell**, for the review's throttle and watcher loop.
- **A shell, git, and Python 3.9+ with `markdown`.** The bundled
  [scripts/reanchor.py](scripts/reanchor.py) maps the references, using the
  review skill's renderer to find them the way the site links them.
- **A delivered review**, written by `systematic-code-review`:
  - its `backlog.md` log has a `SUMMARY written` line, and it has no
    `RUN.lock`
  - no `fixes/*/RUN.lock`: a fix run is working against the old pin
  - `render.json` names **one** repository, pinned to a commit (`GC<sha>`)
  - its findings have Status bullets. An older review without them gets them
    first, per *A review written before the Status field* in the review
    skill's [dispositions reference](../systematic-code-review/reference/dispositions.md)
  - **its files are tracked by git and unmodified**: `findings/`,
    `SUMMARY.md`, `render.json`, `README.md` and `backlog.md`. That is what
    makes the rebase one diff the user can read and discard, and what lets a
    resume undo a step cut short. A review that isn't in git can't be rebased
    until it is. In a repository the user won't commit review files to, it
    becomes its own nested repository, ignored through the outer one's
    `.git/info/exclude`, per *Put the review under git now* in the review
    skill's step 2. Offer that at the gate; creating it is not a commit.

The review's own files are the reference for everything the run applies: the
scales, finding format, test harness, and the system description in its
`README.md`, not this plugin's current copies. The review skill's reference
files, linked below, supply the procedures.

## 0. Gather the parameters

**First: a new rebase, or continuing one?** If `<review dir>/rebase/state.md`
exists, a rebase is under way: follow [reference/resume.md](reference/resume.md)
instead of the rest of this playbook. Never start a second one over it.

For a new rebase, work out what you can, and **ask for the rest in one
message**:

- **Review directory** — `<review dir>`. Must be given. Read its `README.md`:
  code root, output mode, reviewed tree, batch size, usage gate.
- **The new pin** — a commit or ref. Default: the remote-tracking branch of
  the branch in `## Reviewed tree`, such as `origin/develop`, as it is
  locally. Refs are read as they are. If the user wants the latest, they
  fetch first, or you fetch on their yes. In external mode only on an
  explicit yes, since it writes to the code root's `.git`.
- **Mode** — **review new code** (default): the code the commits added or
  changed is reviewed for new findings, as in step 8. **Adjust only**: step 8
  is skipped, and new code goes unreviewed.

## 1. Check, quickly

Only checks that take seconds. Nothing slow runs before the gate. Anything that
fails goes in the gate message as something to fix first, not as a question,
except the links question below.

- **The review**: every prerequisite above.
- **The new pin**: it resolves to a commit, differs from P, and P is an
  ancestor of it (`git merge-base --is-ancestor`). If P isn't, history was
  rewritten, and a rebase can't follow it.
- **The code root is at P'**: run both *Still the reviewed code?* checks in the
  review skill's [tree identity reference](../systematic-code-review/reference/tree-identity.md),
  with P' in place of the pinned sha. Subagents read the files on disk, so the
  code must be P'. If it isn't:
  - **In-tree mode**: offer to check it out (`git checkout --detach <P'>`) as
    part of the go-ahead. Only on the user's yes.
  - **External mode**: the user checks it out, or names another clean checkout
    of the same repository at P', recorded at the go-ahead as a changed code
    root, in `README.md` and `render.json`.
- **Links**: unless the repository has `"links": false`, P' must be on the
  remote (`git branch -r --contains <P'>`), or every link 404s. If it isn't,
  ask now, as step 1 of the review does: correct it first, or continue
  without links.
- **The commits**: if P is on the first-parent line of P'
  (`git rev-list --first-parent <P'>` lists it), take
  `git log --first-parent --reverse --format='%H%x09%s' <P>..<P'>`. Each
  first-parent commit is one change, and a merge is its whole pull request,
  diffed against its first parent. Otherwise P came in through a merged
  branch, and first-parent diffs would count P's own changes as new: take
  every non-merge commit, `git log --no-merges --reverse` over the same range,
  each diffed against its parent. Record which in `state.md`. Count the
  commits, and the changed files and lines, leaving out the review directory
  in in-tree mode.
- **The references**: run the script in report mode:

  ```powershell
  python <skill dir>\scripts\reanchor.py <review dir> <P'> --out <review dir>\rebase
  ```

  It prints counts by class and writes `summary.json`, with the Open findings
  whose cited lines changed (`direct_hits`), and one `anchors/<doc>.tsv` per
  document. The classes are in the script's header. It reads only.
- **Units**: map the changed paths to units by `backlog.md`'s `Files` column.
  Changed files in scope that no unit holds are new code. In *review new code*
  mode, group them into new units per *Unit design* in the review's step 2,
  numbered after the last unit, each with a `Focus`.
- **New top-level directories**: list every first-party top-level directory
  the commits add, such as a new app or package, with its file and line
  counts. The review's scope never named it, so whether it is in scope is the
  user's call at the gate, not the unit mapping's. Propose units for it, or
  report it as out of scope.
- **Private fix runs that landed**: rows `fixed` in any
  `<review dir>/fixes/*/queue.md` with no `Landed` value, whose Test or Probe
  is in P', found with the `git grep` in the `fix-light` skill's
  dependencies reference, where that private skill is installed. Their
  findings are still Open, and the evaluator would find them gone. Recommend
  reconciling first, per *Reconciling a private fix run* in the
  [dispositions reference](../systematic-code-review/reference/dispositions.md).
  Otherwise the run re-anchors them and leaves their Status to reconcile.
- **The usage gate**: run the review's `throttle.bat` once, as in the review's
  [throttle reference](../systematic-code-review/reference/throttle.md). Exit
  2 is a problem to show at the gate.

If the gate is declined, delete `<review dir>/rebase/`.

## 2. Get the go-ahead   [ STOP / ASK ]

This is the only gate. Show the user, in one message:

- P → P', the number of commits, and the files and lines they change in scope
- the reference counts, and how many Open findings the changed lines hit
- the mode, the units with changed code, and any proposed new units with
  their Focus
- every new top-level directory, and the scope you propose for it
- landed fixes from private runs that aren't reconciled
- the work: summary jobs, screen jobs, evaluation of whatever the screen
  flags (at least the direct hits), and in *review new code* mode the
  reviewers for changed units and new units
- anything from step 1 to fix first
- **what happens after the go-ahead**: everything below, unattended, with no
  further questions, ending in an uncommitted diff in the review directory and
  a report. Say what stops the run (step 4), and that the code root must stay
  at P', untouched, until the run finishes. Say too that the site under
  `html/` follows the run from step 6 on.

**Halt here, unless the user's request already said to start without asking**
and step 1 found nothing to fix first.

On the go-ahead, write `<review dir>/rebase/state.md`, per
[reference/resume.md](reference/resume.md), with every parameter and the
commit list. Create `rebase/RUN.lock` with the start time and a heartbeat,
updated whenever you write `state.md`, and log `Go-ahead given <date>` in
`state.md`. Set job statuses with the review skill's `review_state.py`
(`status <review dir>\rebase\state.md <job> --set done --lock <review dir>\rebase\RUN.lock`),
not with ad-hoc shell edits, which mix line endings.

## 3. Write out the diffs

For each commit, in order, write `rebase/diffs/<nnn>-<sha7>.diff`: its full
message (`git log -1 --format='%H%n%an%n%ad%n%n%B'`), a `--stat`, then the
patch against its first parent (its only parent, in the non-merge listing),
leaving out the review directory. Paths the
review's scope excludes as vendor or generated, and lockfiles, appear in the
stat only.

The orchestrator runs every git command. Subagents read files and write only
their own outputs and the findings docs they are given, and run no shell
commands, in either mode.

## 4. Summarize what each commit does

Group the commits, in order, into jobs of about 3,000 changed lines. A larger
commit is a job of its own. Spawn one Opus subagent per job with
[reference/summarizer-prompt.md](reference/summarizer-prompt.md), batch size
from the review's run parameters, under the throttle loop below. Each writes
`rebase/notes/<job>.md`.

**The throttle loop**, here and in every later step that spawns: run
`throttle.bat` before each batch. Non-zero: background the watcher loop from
the [throttle reference](../systematic-code-review/reference/throttle.md),
and carry on when it releases. Mark each job `in-progress` in `state.md`
before spawning it. A job is `done` only when the subagent returned its count
line and its output file ends with the line `## Done`. Anything else is a
failure: delete the output, set the job back to `pending`, and log why.

**A job that edits review files must keep their line endings.** Agents and
editing tools have rewritten LF files as CRLF, which turns the user's diff
into whole-file rewrites. After each such job, compare
`git -C <review dir> diff --stat` with `git -C <review dir> diff --ignore-cr-at-eol --stat`
for its files. If they differ, convert those files back to the line endings
they had, before marking the job done.

**Stop the run**, delete `RUN.lock`, and report, when any of these happens.
These are the only ways the run ends early:

- a job fails twice
- the throttle watcher reaches one of its caps
- the code root is no longer at P', or is dirty
- before step 6, a review file the run will rewrite has changed:
  `git -C <review dir> status --porcelain -- findings SUMMARY.md render.json README.md backlog.md`
  prints anything
- `reanchor.py --write` fails

## 5. Screen every Open finding

Group the findings docs, whole, into jobs of about 30 Open findings each,
`X-cross-cutting.md` included. Docs with no Open finding are left out. Spawn
one Opus subagent per job with
[reference/screener-prompt.md](reference/screener-prompt.md). Each reads every
note from step 4 and its own docs, and writes `rebase/screen/<job>.md`: one
line per finding it flags, with the commits and the reason.

Then build the flagged set in `state.md`'s `## Flagged` table, each with why:

- every direct hit from `summary.json`
- every finding a screener flagged
- every Open finding whose `Depends on` names a flagged finding, reading only
  the IDs before any `(verifier: was …)` marker

## 6. Re-anchor

Run the script again, writing:

```powershell
python <skill dir>\scripts\reanchor.py <review dir> <P'> --out <review dir>\rebase --write
```

It rewrites every reference it can map, in the findings docs, `SUMMARY.md` and
`README.md`, and moves `render.json`'s pin last. A reference inside a changed
hunk (`approx`) is mapped to the hunk that replaced it. For a flagged finding,
the evaluator corrects it, unless it finds the defect gone, when the mapped
reference shows the change that removed it. Elsewhere it stays as mapped: in a dispositioned
finding, or in prose, it points at the code that replaced what it cited. A
reference to a file deleted between the commits (`deleted`) is left as
written, and renders as plain text from here on. `SUMMARY.md` otherwise stays
as delivered.

Then, by hand:

- **`## Reviewed tree`** in `README.md`: P' as the commit, with its subject,
  whether it is on the remote, and the two check commands with P'. Replace
  the old pin, and any earlier `Rebased from` line, with one line:
  `Rebased from <P sha7> on <date>.`
- **A line in a findings doc's preamble that names P** as the commit it
  describes, such as `Reviewed at: <sha7>`: P' instead.
- **`backlog.md`'s `Files` column**: a renamed file under its new path, a
  deleted one removed. A resume, step 8, and the next rebase map changes to
  units by this column. A unit left with no files keeps its row and its
  findings.
- **The test harness**: if any note's **Tests** line changed test
  infrastructure, re-survey it per *Survey the test harness* in the review's
  step 1, reading only, and rewrite `## Test harness` to describe P'.
  Evaluators and reviewers get it verbatim.

Mark the step done in `state.md`.

**Render the site** once, without `--quiet`, with the review skill's
[renderer](../systematic-code-review/scripts/render_review_html.py):
`python <review skill dir>/scripts/render_review_html.py <review dir>`. From
here the links point at P'. Read the pin line and the warnings, then give the
user the path to `html/index.html`, saying the flagged findings still read as
written against P until their evaluation lands.

**From here on, re-render after every job that edits a findings doc**: each
evaluation job, merge, new unit, verify job and the x-links job. Use
`--quiet`, which prints only warnings and one closing line, and read the
warnings. **A failed render never stops the run**: log it once and carry on.
Step 9 renders again.

## 7. Evaluate the flagged findings

Group the flagged findings into jobs of about 10–12, by whole findings docs.
**A findings doc is in at most one running job at a time.** Before spawning a
job, copy its docs to `rebase/before/<job>/`, so a resume can restore them.
Spawn one Opus subagent per job with
[reference/evaluator-prompt.md](reference/evaluator-prompt.md). It edits the
docs in place, and writes `rebase/eval/<job>.md`: one line per finding, with
its outcome. A job is done only when every flagged finding in it has a line.
Copy each outcome into the `## Flagged` table, and re-render, as in step 6.

## 8. Review the new code

*Review new code* mode only. Follow [reference/new-code.md](reference/new-code.md):
changed code in existing units, then new units, then verification of the new
findings and their links to `X-` harness findings.

## 9. Finish

1. **Render** the final time, without `--quiet`, and read every line, per
   the review's
   [rendering reference](../systematic-code-review/reference/html-rendering.md).
   Read the pin line and the warnings, and render with `--unresolved` for
   the unresolved-reference sweep. Leave out the references
   `rebase/anchors/` lists as `unresolved`, which were unlinked before the
   rebase, and as `deleted`, whose file is gone at P': an evaluator has
   already handled those in a flagged finding, and elsewhere they stay as
   written, describing the code they were written against. Any other
   first-party reference that resolved before and doesn't now is one the
   rebase broke: fix it in the doc.
2. **Log one line** in `backlog.md`'s `## Log`:
   `Rebased <P sha7> -> <P' sha7> <date>: <n> commits; <f> of <o> open findings flagged; <s> still, <c> changed, <g> gone (Fixed), <u> unsure, <r> left to reconcile; <k> new findings in <m> units, <x> new units`.
   In *adjust only* mode, end it with `new code not reviewed` in place of the
   new-findings counts.
3. **Delete `<review dir>/rebase/`**, `RUN.lock` with it. The notes, screens
   and evaluations are scratch. Nothing in the review refers to them.

## 10. Report

- **Changed**, one line each: what changed, and any rating that moved.
- **Gone**, one line each: the commit that removed the defect, and whether the
  finding had ever been confirmed.
- **Unsure**, each with what would settle it. These are the user's to decide.
- **Claims against the review**: any evaluator that thought a finding was
  never a defect. The run changed nothing for these. Recording Not a defect is
  the user's call, per the dispositions reference.
- **Left to reconcile**: landed private fixes, with the reconcile reference.
- **New findings**, by severity, with verdicts, and any new `X-` finding. Say
  that cross-unit dependencies for new findings weren't looked for, since no
  synthesis ran. In *adjust only* mode, instead: the units with changed code
  and the new files that went unreviewed.
- **Render warnings** still standing.
- **The diff**: everything is uncommitted in `<review dir>`. Give the command
  to read it, such as `git -C <review dir> diff --stat`. Committing it is the
  user's call.
