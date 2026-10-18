# rebase-findings — maintainer notes

Nothing links to this file, so it never loads while the skill runs.

## What it is for

A `systematic-code-review` is pinned to one commit, and its line numbers and
judgements describe that commit. The code keeps moving: fixes land, features
land, files move. After a while the links point at the wrong lines, and some
findings describe code that is no longer there or that behaves differently.
This skill moves the review to a newer commit of the same branch, so it
stays a usable work list without being written again.

It is one of the set that acts on a delivered review, with `fix-light` and
`fix-seam`. A planned sibling, a re-evaluate mode of the review itself (a full
sweep at a new pin, told the existing findings), is not written. Step 8 here
is that idea scoped to the changed code.

## Why relevance, not line overlap

The first sketch flagged a finding when a commit changed the lines it cites.
That was rejected: a finding's effect can change with none of its lines
touched. A caller starts validating the input, a config default changes, a
dependency is upgraded, a type is narrowed elsewhere. So each commit is
summarized by what it does, and every Open finding is screened against those
summaries by mechanism. A direct line hit still always flags, because it is
free to compute and never wrong to check.

The screen is cheap by design (notes, not diffs; about 30 findings per agent)
and leans toward flagging, because the costs are lopsided: a false flag costs
one evaluation, a miss leaves a finding quietly wrong for as long as the
review lives. Screening by findings doc keeps the work proportional to
commits × units rather than commits × findings. A flagged finding also flags
its direct dependents, since a change to a prerequisite can change whether
the dependency still holds.

## Why the notes are discarded

A deliberate choice: the review must not grow with each rebase. Findings get
dispositioned and may later be deleted, and a long-lived finding shouldn't
carry the history of every commit that passed it. So the commit notes,
screens and evaluation lines are scratch in `rebase/`, deleted at the end. A
changed finding is rewritten to describe the new pin, with the review's
existing `(verifier: was …)` markers on ratings, and nothing like "since
commit X". What remains is the new pin, one `Rebased from` line in the
README (replaced, not appended, by the next rebase), and one log line per
rebase in `backlog.md`.

## Why the review must be in git

The rebase rewrites references in hundreds of findings and edits some of them
by judgement. The only practical way for a person to check that is a diff,
and the only safe way to undo a re-anchor cut short is to restore the files
from git. `reanchor.py` refuses to write otherwise.

## Why the mechanical part is a script

Re-anchoring thousands of references is exact arithmetic over diff hunks,
which an agent does slowly and sometimes wrong. The script reuses the
renderer's own reference regex and resolution, imported from the review
skill's `scripts/`, so it rewrites exactly the references the site links,
resolved the same way. A reference inside a changed hunk can't be mapped
exactly. It is mapped to the replacing hunk, which is right for a finding the
change fixed (the link shows the fix) and a starting point for the evaluator
otherwise. On the first test, against a review with a few
thousand references, nearly all mapped as same or exact, a handful as approx
or unresolved, and spot-checked shifted lines held the same code.
`scripts/tests/test_reanchor_mapping.py` checks the line arithmetic by hand
and against `git diff` on randomly edited files.

The pin moves last, so a run cut short leaves `render.json` on the old pin,
and a resume can tell the two states apart. Every check, the `render.json`
pin included, runs before the first write, so a refusal leaves nothing to
restore.

A reference into a file the commits deleted is class `deleted`, not `gone`,
so it can't be confused with the evaluator's `gone` outcome. It is left as
written. Step 9 once told the orchestrator to "fix" every reference that
stopped resolving, which would have rewritten deleted-file references in
dispositioned findings that the rebase is not meant to touch.

`README.md` is re-anchored along with the findings, because the renderer
links its references too, and a `file:line` in the Test harness section
would otherwise point at the new pin with the old line number.

## Why gone means Fixed

The status scale defines Fixed as "the defect is gone", and the evaluator has
just checked that against the code. The disposition rule against inferring a
disposition from code having changed is about code that merely moved; this is
a judgement that the defect is absent, with the commit cited. The finding's
text is left as it was, so it still says what the defect was. `never
confirmed` marks one that no verifier had confirmed, as the dispositions
rules ask. The user's check is the diff.

Findings fixed by a private `fix-light` or `fix-seam` run are left for
reconcile, which records the landed sha and the test. Writing Fixed here as
well would give reconcile a disposition to overwrite.

## Why only Open findings are screened

Fixed, Planned, Won't fix and Not a defect are decisions already made.
Their references are re-anchored like everything else, but re-judging them is
not this skill's job. A Won't fix that a commit made worse is a gap, noted
here: if it turns up in practice, screen Won't fix too.

## Why an evaluator may not refute

It judges what the commits changed. An evaluator that thinks a finding was
never a defect is making a claim against the review, like `fix-light`'s
`premise` bounce, and deciding Not a defect is the user's call per the
dispositions rules. So it reports it and changes nothing.

## Why one gate, early

Same reasoning as `fix-light`: everything slow happens after the go-ahead and
nothing asks after it. The output is an uncommitted diff, which is the review
of the run, so no stop at the end is needed.

## First pilot

First run in full against a review of a TypeScript web codebase whose code
had moved on by a couple of dozen merged pull requests. It changed the skill in five places:

- **Resolution had to match the renderer exactly.** `reanchor.py` once kept
  its own copy of the reference lookup, and a bare name with several
  candidates resolved differently from the site's link. It now calls the
  renderer's `resolve_ref` with each finding's own cited file, and
  `scripts/tests/test_reanchor_resolution.py` checks the two agree.
- **New top-level directories are a scope question.** The unit mapping missed
  a whole new top-level directory, because no unit's `Files` matched it. It was reported, not
  reviewed, which was the right outcome for the wrong reason. Step 1 now lists
  every new first-party top-level directory and settles its scope at the gate.
- **Briefs state facts, not guesses.** One summarizer brief said what a commit
  did from its title, and was wrong; the agent caught it against the diff.
  Briefs now carry only what the commit's stat and message say.
- **Coverage notes need a marker.** Carrying over "what this review could not
  assess" from the old doc was done by a heuristic that fired on ordinary
  commentary. Reviewers now start those lines with `Not assessed:`, and only
  those carry over.
- **Moves are not new code.** A workspace move touched most units' files
  without changing what they do. A unit whose diff only moves files or
  rewrites import specifiers is logged as no new code and skipped.
