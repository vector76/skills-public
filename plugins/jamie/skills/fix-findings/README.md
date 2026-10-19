# fix-findings: maintainer notes

Nothing links to this file, so it never loads while the skill runs. Read it
before changing the skill: most rules here exist because of a specific
decision, and the reasons are what keep an edit from undoing one.

## What it is for

`systematic-code-review` delivers a work list and fixes nothing. This skill
works through any part of that list, one finding at a time, in the session
that starts it, and leaves the fixes as commits on a local branch.

It was assembled from three earlier fix playbooks, none published here:

- a **light fixer**: sequential, in place, one gate up front, but limited to
  Fix impact Trivial or Low and RGR `Unit` or `Seam (local)`;
- a **harness builder**: one `X-` finding per run, ending at a stop for a
  person to review the harness before anything used it;
- a **lanes runner**: every kind of task, unattended, with a base session and
  parallel worker sessions in their own worktrees, coordinated by a script.

This one keeps the light fixer's shape (one session, one task at a time, in
the repository's own folder) and takes everything else from the lanes
runner: every kind of finding, its pipelines and prompts, the review rounds,
agent-made dispositions, the state script and the queue format. It drops what
only parallelism needed: worktrees, claims, heartbeats between sessions,
landing by rebase and compare-and-swap, the shared-core `{workers}`, per-lane
environments, and the redo after a conflict.

## Why sequential, in place

One session, one task at a time, means nothing needs claiming or landing:
each task starts from the tip the previous one left, and its full suite run
tests the tree with every earlier fix in it. Working in the repository's own
folder means dependencies are already installed and nothing is set up per
worktree. The cost is that the working tree belongs to the run until it
ends, which the gate says.

## Why one gate, at the start

A question in the middle of an unattended run is a stall nobody sees. So
everything is asked before the gate, the gate comes before anything slow (the
baseline suite runs after it), and afterwards each condition either lets the
run go on or stops it with a report. Anything that needs a person, such as an
`intent` or `disputed` bounce, becomes a bounce with the question in it, and
the run goes on to the next task. Answers come back as notes: through a
resume's one question, or `--note` on a later run.

## Why there is no eligibility limit

The light fixer limited Fix impact because a low rating was what let a fix go
through with light oversight. Here, as in the lanes runner, Fix impact sets
the supervision instead of gating the work:

| Task | Review rounds |
|---|---|
| default | 1 |
| Fix impact Uncertain | 2 |
| harness, notest, partly | 3 |

A round that finds nothing ends the series early, and if the last allowed
round changed something, one more runs, once.

**Harnesses land without a person reviewing them.** The harness builder
stopped for review because a harness is shared infrastructure, and a
production refactor changes how a project starts. Here a harness checker and
three rounds take that place, and the dependents' red tests check the
harness again as each is fixed. The gate says so, so a user who wants the
stop can scope the run to leave `X-` findings out.

**`RGR: No` findings are fixed and recorded as fixed without a test**, on the
fixer's written argument, the checker, and three rounds, with a probe string
so reconcile can find the fix after a squash. Partly testable findings get a
red test for the testable part.

## Why a script

All state goes through `fix_run.py`, as it did through the lanes runner's
script: the task table, the queue, the log, the lock, the eligibility rules,
the leak scan and the checks before a task is recorded done. A script can't
forget a step after a context compaction, and it keeps the queue's format
exact, which reconcile and the review's renderer depend on. The run uses its
own copy in `state/bin`, so a plugin update mid-run changes nothing.

The script imports `review_format.py` from the review skill, the same
vocabulary the renderer and `review_state.py` use, so the three never
disagree about a level, an ID or a correction marker. `init` copies it into
`state/bin` with the script.

## Where the state lives

**`<root>/.fix-findings/state/`**, ignored through `.git/info/exclude`.
Inside the code root, so subagents write their records without needing
another directory's permissions; outside the review, so a committed review
isn't littered with working files; and never under `.git/`, where Claude
Code may treat writes as sensitive.

**The lock is `<review dir>/fixes/<run>/RUN.lock`**, beside where the run's
record ends up, because `rebase-findings` refuses to start while any
`fixes/*/RUN.lock` exists: a fix run is working against the old pin. The
review's own `.gitignore` already ignores `RUN.lock`. In private mode the
queue lives there too, kept up to date, as reconcile expects.

**The guard** compares the review directory, by path, size and modification
time, with a snapshot taken at each task's start and after each Status
rewrite. A subagent that writes there stops the run. It is a file walk, not
`git status`, so it works whether the review is in the code's repository, its
own, or none.

## Why discard, never rescue

Every failed check discards the task's commits and changes and bounces it.
Two returns are sanctioned, both in the script: one retry of a `Unit` finding
as `Seam (local)`, which is a re-rating rather than trying harder, and
waiting on a prerequisite a fixer discovered, when that prerequisite is a
task in this run. The lanes runner's third, a redo after a landing conflict,
has nothing to do here.

`discard` restores tracked files to the task's start and deletes, one by
one, the untracked files the task added, against a snapshot `next` takes.
Untracked files that were there before, and ignored files, stay. It never
uses `git clean`, `reset --hard` or `stash`: each can reach files the run
doesn't own. The one reset it uses is `--soft`, which moves `HEAD` and
touches no file. It refuses when the run branch isn't checked out, rather
than switching and losing someone's work.

**An unfinished task is redone, never resumed.** After a crash or a
compaction, `next` reports the task still in progress, and the playbook
discards it and starts it again. Redoing costs a few subagents; guessing
where a task stood can record a fix no check ran on. The same rule covers
losing one's place mid-task after a compaction, which the playbook tells the
session to admit rather than guess. `next` prints the playbook's path with
every task, and the loop says to re-read it for every task, because after a
long run a remembered summary is not the playbook.

**Context.** A long run compacts. That is accepted: the state is on disk,
`status` says where things stand, and the rule above covers a compaction in
the middle of a task. There is no context check that stops the run, as the
lanes runner had, because a stopped single session needs a person to restart
it, and the point is not to need one.

## Why the queue matches the lanes runner's

Its columns are the lanes runner's, without Lane and Run sha: `ID | Kind |
Severity | Fix impact | RGR | Title | Status | Test | Probe | Proof | Rounds
| Commits | Landed | Note`, with the same Status texts and the same records
(`<ID>.final.patch`, kept proofs). The review skill's dispositions reference
already describes how to reconcile that queue, and the same steps work here.
Commits here are never rebased, so the Commits column's shas are final.

## Defenses are left out

The lanes runner defended each fix before it landed, as a page in the
format of a separate fix-report skill that builds a PR's companion report.
That skill isn't in this repository, so this one has no defense step. Adding
it means bringing fix-report here first; the records and final patches it
would read are already kept.

## Testing

Tiered, as in the lanes runner: checks while working run the `related`
command, the full suite runs once per task before `done`, which refuses to
record a fix whose tree the suite hasn't run on, and the seam and harness
checks always use the full suite, since their changes reach code through
configuration and entry points no import graph shows. A failure off the
baseline gets one rerun; a test that passes then is recorded as flaky.

`scripts/fix_run.py` has no test suite yet. It was exercised end to end
against a synthetic repository and review: eligibility, prerequisites pulled
in, red and green, the Status rewrite, `done`'s refusals, bounces blocking
dependents, a harness with its probe, the guard, the lock, a crash mid-task
and its restart, requeue, finish, and a second run archiving the first.

## Not done yet

- A test suite for `fix_run.py`.
- The defense step, once fix-report is here.
- A neutral, private-mode report.
