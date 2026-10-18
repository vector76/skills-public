# Reviewing the new code

Step 8, in *review new code* mode. The review covered `<P>`. This covers what
the commits added or changed in scope, so the rebased review has looked at
all of `<P'>`. It reuses the review skill's prompts and rules, from its
[reference files](../../systematic-code-review/reference/), with the review's
own scales and harness from its `README.md`. Every spawn goes under the
throttle loop in step 4 of this playbook. After each merge, new unit, verify
job and the x-links job, re-render with `--quiet`, as step 6 says.

Work in this order. Each part is tracked as jobs in `state.md`.

## 1. Changed code in existing units

For each unit whose in-scope files gained or changed lines (deletions alone
leave nothing new to review), check the diff first. A unit whose diff only
moves or renames files, or only rewrites import paths and module specifiers,
has no new code: log `<unit>: no new code (moves and imports only)` in
`state.md` and skip it. Units left with only a few changed lines each may
share one reviewer, each still merged into its own doc. Then write
`rebase/unit-diffs/<unit>.diff`: `git diff <P> <P'>` over that unit's files,
renames followed. Spawn one Opus reviewer per unit with the review's
[reviewer prompt](../../systematic-code-review/reference/reviewer-prompt.md),
filled as the review fills it, including the unit's `Focus`, with these
changes:

- **What to review** replaces "Read EVERY line": "Review the code these
  commits added or changed in your unit: `rebase/unit-diffs/<unit>.diff`.
  Read the changed files in full for context. Report only defects that the
  changed code introduces or exposes. A defect in unchanged code that a change
  made reachable counts."
- **What is already reported**: "Your unit's existing findings are in
  `<findings doc>`, whatever their Status. Don't report any of them again, or
  a defect one of them already covers. Read it, and write nothing to it."
- **Where to write**: `rebase/new/<unit>.md`, numbered from the unit's next
  free number, such as `U12-9` after `U12-8`. A `Depends on` may name a
  finding in the existing doc.
- **Its count line** says `new` findings, e.g.
  `U12 done: 0 Critical, 1 High, 2 Medium, 0 Low new -> rebase/new/U12.md`.
- **Coverage notes**, if the review's prompt predates the rule: "In
  `## Coverage notes`, start each item you could not fully assess with
  `Not assessed:`."

A job is done under the same rule as a review unit: the count line, and the
doc has a `## Coverage notes` section.

**Merge** each finished doc into its unit doc, one at a time: copy the unit
doc to `rebase/before/merge-<unit>/` first, then insert the new findings
after the last finding and before `## Coverage notes`. From the new coverage
notes, carry over only the lines starting `Not assessed:`, as one paragraph
starting `Changed code at <P' sha7>:`, and nothing if there are none. Don't
judge which other remarks count: a heuristic for that once carried ordinary
commentary into the unit doc. Mark the merge done in `state.md`.

## 2. New units

Add each new unit proposed at the gate to `backlog.md`'s table as `pending`,
then sweep them exactly as the review's step 4 does, with the reviewer prompt
unchanged: every line, written to `findings/<ID>-<slug>.md`, marked `done`
only under that step's rule.

## 3. Verify the new findings

Every new Critical, High and Medium finding gets the review's
[verifier prompt](../../systematic-code-review/reference/verifier-prompt.md),
in jobs by whole docs, one doc in one running job, with this added: "Verify
only these findings: `<IDs>`. Leave every other finding in the doc exactly as
it is." Done when each named finding has one Verification bullet.

## 4. Link harness findings

For each new `Seam (harness)` finding, apply items 1 to 3 of *Write the
cross-cutting testability findings* in the review's step 6, against the `X-`
findings that exist: add its ID to the matching harness's dependents with
`Depends on: X-<n>`, and write a new `X-` finding at the end of
`findings/X-cross-cutting.md` only for a harness none of them names. Copy
that doc to `rebase/before/x/` first. Skip a refuted finding.

Cross-unit dependencies come from the review's synthesis themes, and no
synthesis runs here. Don't look for them; the report says so.
