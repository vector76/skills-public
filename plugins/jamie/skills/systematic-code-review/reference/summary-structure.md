# SUMMARY.md structure

Build this from **programmatically extracted** fields — regex over the
`**Severity:**` / `**Confidence:**` / `**Category:**` / `**Fix impact:**` /
`**RGR:**` / `**Verification:**` / `**Status:**` bullets — not by re-reading
findings docs into context.

`SUMMARY.md` records the review **as delivered**. Once it is written, it isn't
updated as findings are dispositioned: the rendered index counts statuses
live, from the findings docs. Say so in one line near the top, so a reader
who arrives after dispositions have started knows where the current state is.

Cross-check two counts before writing. The verdict count must equal the
number of findings that are not Low, counting each by its severity before any
verifier changed it. The fix-impact, RGR and status counts must each equal
the total number of findings, and every status must be `Open` except
that each REFUTED finding is `Not a defect`. A mismatch means a unit doc
drifted from the format; find and fix the drift first. Count the
cross-cutting `X-` findings separately from the unit findings, in every table
and check: they are written at synthesis, are never verified, and would
otherwise read as reviewer output.

Severity is the first word of its bullet. Count and tier every finding by its
**current** severity, and use the `(verifier: was …)` markers only to report
the changes.

## Sections, in order

1. **Totals table** — reported vs confirmed vs refuted vs uncertain, per
   current severity, and the refutation rate for each severity. Under it, one
   line for the severity corrections: how many verifiers lowered and how many
   they raised, and between which levels (for example, "9 lowered, Critical to
   High; 7 Mediums raised to High"). Name the units raised findings came from.
   A cluster in one unit usually means that unit was reviewed under a wrong
   premise, which belongs in the method notes too.

2. **Fix impact table** — severity × fix impact (Trivial / Low / Uncertain),
   counting refuted findings out. Say how many fix-impact ratings verifiers
   changed, and in which direction. This is the table a fix plan starts from.

   Under it, the **fix-readiness table**: CONFIRMED findings only, fix impact
   (Trivial / Low / Uncertain) × RGR (Unit / Seam (local) / Seam (harness) /
   No, partly testable / No). Each cell is a slice a fix pass can take with
   one level of supervision. Confirmed, Trivial or Low, and Unit is the corner
   a test can check on its own, and Seam (local) sits next to it: one small
   refactor away. Say how many RGR ratings verifiers changed, and in which
   direction.

   Then the **dependencies**, from the `Depends on` bullets, harness links
   apart: how many findings wait on another finding, how many of those links
   cross units, the longest chains, and the findings most waited on. Those
   are where a fix pass starts, since their fixes unblock others. Say how
   many dependencies verifiers added or removed.

3. **Cross-cutting themes** — 4–8 of them. **This is the most valuable section
   in the deliverable.** Individual findings are a work list; themes tell the
   reader what is systemically wrong. Group related findings and *name the
   pattern*. A single pattern producing four Criticals across three units is the
   most actionable thing in the document, and it is invisible from the
   per-unit docs.

   Give each theme a fix impact of its own. Twelve individually Trivial
   missing length checks can be one design decision (a shared parsing helper),
   and then the theme is Uncertain even though every finding in it is Trivial.
   Say so where it happens. Where the theme's findings depend on one another,
   name the one to fix first.

4. **Testability** — the cross-cutting `X-` findings, one row each: the
   harness, how many findings depend on it, the highest severity among them,
   and how many of those have a Trivial or Low fix. Rank by what each unlocks,
   not by its own severity, which is always Low. A harness that a few
   Criticals wait on comes first. Say how many findings in all wait on a
   harness, and that its own disposition (built, planned, or won't fix)
   decides theirs.

5. **Tier 1** — confirmed Critical, High confidence. Full table: ID,
   `file:line`, category, fix impact, issue.

6. **Tier 2** — confirmed Critical, Medium/Low confidence.

7. **Tier 3** — confirmed High, High confidence, grouped by subsystem with
   inline IDs.

8. **Tier 4** — confirmed High, Medium/Low confidence. ID list plus a sentence
   of characterization.

9. **Tier 5** — confirmed Medium, as ID lists grouped by unit. List the Trivial
   and Low fix-impact ones first: confirmed, cheap, and safe to fix, they are
   the natural first batch for lightly supervised fixing.

10. **Appendix: refuted findings** — with the refutation evidence, so a later
   pass does not re-report them.

11. **Appendix: unresolved** — UNCERTAIN verdicts and what would settle each.

12. **Appendix: scope and method notes** — see below.

## Re-rank by real-world consequence

Call out findings whose operational consequence exceeds their severity label. A
"High" that makes failed upgrades report success outranks most Criticals in
practice. The severity scale measures the defect; this section measures what it
costs you.

## Fix impact routes work; it does not authorize it

Fix impact exists so that fix work can be split: Trivial and Low fixes can go
through with light oversight, while Uncertain fixes get a planning step that
maps their reach first. Say plainly, next to the fix impact table:

- **A low fix impact does not mean the defect is real.** Low findings were
  never verified, and UNCERTAIN ones were not settled. A Trivial fix to either
  may fix nothing. Treat only CONFIRMED findings as ready to fix under light oversight, or
  require whoever fixes a finding to confirm the defect first.
- **Fix impact is a reviewer's estimate made without designing the fix.** The
  scale rates up under doubt, but a Trivial rating that turns out wrong when
  the fix is written should move the finding to the planning lane, not be
  pushed through.
- **A theme can override its findings' ratings**, as described above.
- **RGR is a prediction against the recorded harness**, made without writing
  the test. When a fix pass can't get a test that fails for the finding's
  stated reason, the finding moves to a more supervised lane. The pass doesn't
  try harder, and it doesn't settle for a test that fails for another reason.
- **A partly testable finding is never Fixed by its test alone.** Its residual
  needs a person to check it, and the Fixed evidence names who did.

## The method-notes appendix must be honest

This is where a review earns or loses trust. State plainly:

- **What was not reviewed** — vendor, generated, excluded trees, with rough line
  counts.
- **Low findings were never verified.** A confirmed Medium is more
  trustworthy than an unverified Low, whatever the labels suggest, and Lows
  are the one place under-rating could still hide. Readers will misuse the
  tiers without this sentence.
- **The `**Verification:**` line overrides the rest of the finding.** Verifiers
  corrected the mechanism or blast radius of findings that were still confirmed,
  so acting on a title alone can mean chasing the wrong thing. Severity
  corrections are already in the numbers.
- **Which units were reviewed under a premise you later found wrong** — e.g. an
  exposure model you corrected mid-run. Name the units and say their severity
  distribution is suspect.
- **Which verifier jobs lacked refutation levers.** A job returning 0%
  refutation against a run averaging ~9% is evidence about the prompt, not the
  code. Say which verdicts came from such prompts. Give the severity-correction
  rate beside it: verifiers that refuted little but corrected many severities
  were checking, not rubber-stamping, and one run refuted under 1% while
  correcting nearly a quarter of its severities.
- **A single-reviewer sweep would likely surface more on a second pass.**

Put all of this in the appendix rather than smoothing it over. The reviews that
have held up are the ones where the caveats were stated where readers arrive,
not buried.

## And in the index, wherever these docs are linked

If the review gets an entry in a `docs/README.md` or similar, **lead with the
caveats** — findings-only, Low unverified, verification line overrides the
rest of the finding. The index is where a cold reader arrives, and it is where
they will form the wrong impression of the tiers if it is not said.
