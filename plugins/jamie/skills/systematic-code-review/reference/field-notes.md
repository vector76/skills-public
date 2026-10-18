# Notes from the field

Measured on three runs:

- **Embedded reviews A and B**: C and C++ firmware, each about 30 units and several hundred findings. Only Critical and High findings were adjudicated.
- **Web review C**: a TypeScript web codebase of similar size, with Critical, High and Medium findings adjudicated. It was later rebased with the `rebase-findings` skill.

## Disk-based backlog over in-context state

Non-negotiable at this scale. Review A spanned several days and a service
outage, and a fresh session could have picked it up at any point. This is the
single decision that makes the rest possible.

## The verification pass earns its cost

In A and B, about 6–9% of Critical/High findings were refuted. Two of them
would have wasted real time:

- A reported safety issue that a hardware safeguard already prevented.
- A "NULL dereference" that isn't one, because of how that platform maps memory.

Verification also *strengthened* one finding, by finding a second integer
overflow that bypassed a size check, and it corrected the blast radius of
several others. **Budget about 20–30% extra for it.**

## A low refutation rate is not always a weak prompt

Review C refuted under 1% of the findings it verified, even after the verifier
prompt was changed partway through to require a named refutation attempt for
every finding. Its verifiers were clearly engaged: they corrected nearly a
quarter of the severities, almost all of them down, and the one they raised
became the review's only Critical. Report the severity-correction rate beside
the refutation rate. A run with few refutations and many corrections is
checking findings, not rubber-stamping them.

## Verify the anomalies, don't assume them

A small, routine-looking file returning several Criticals looked like
over-reporting, but all of its Critical/High findings were upheld. In the
same run, two hardware-related findings in a different unit fell. The lesson
cuts both ways: an anomalous count is a reason to look, not a reason to
discount.

## Severity calibration needs per-unit guidance

Reviewers **over-rate operator-only debug paths** and **under-rate "High"
findings with severe operational consequences.** Tell each reviewer the
exposure model for its unit, and re-rank by real consequence in the summary.

Getting the exposure model wrong contaminates a whole batch. In review B,
several units were reviewed under a stated operator-only premise that turned out
to be wrong. A later re-sweep didn't cover everything the premise had touched,
so those units' Critical counts stayed suspect and had to be flagged in the
summary rather than trusted.

## Prompt each subagent to return one line

The orchestrator ran about 50 subagents across sweep and verification **in a
single session**, because results came back as counts, not prose. Relax this
and the orchestrator's context becomes the bottleneck well before the review
finishes.

Prompts are the other half of that budget. Pasting the scales and the test
harness verbatim into every prompt cost about 4k tokens per spawn in review C,
across roughly 80 spawns. Later prompts pointed at the README's sections
instead, which hold the same text; see *Prompt size* in the
[reviewer prompt](reviewer-prompt.md).

## Asymmetric verification is a real limitation

In A and B only Critical/High were adjudicated, which inverted the usual trust
gradient: a confirmed High was *more* reliable than an unverified Medium. It
also meant under-rating went unchecked: a real Critical filed as a Medium was
never looked at again. The playbook now verifies Mediums as well, and leaves
only Lows unverified.

## What the throttle actually did

Over A and B, the throttle paused several times, and every pause resumed
automatically. One usage-service outage was caught and reported after 15
minutes, and nothing needed manual intervention beyond restarting the usage
service. **The first batch alone used over a third of a session's usage**, so
the gating is not theoretical. In C, the throttle paused three times at the
start of a usage window and released by itself each time.

## Bookkeeping by hand goes wrong

In C, statuses, heartbeats and log lines were written by ad-hoc shell
one-liners. PowerShell's `Add-Content` and `WriteAllLines` write CRLF, bash
`>>` writes LF, and Python's text mode strips CRs, so `backlog.md` ended up with
mixed line endings and was damaged twice. In one edit, a literal backtick-n in a
replacement split two table rows. Reviewers also drifted from the format
(`Low:` and `Unit:` instead of an em dash), and nothing warned. The skill now
ships [scripts/review_state.py](../scripts/review_state.py) for that
bookkeeping and those checks.

## Short paths cost real time later

Review C's `backlog.md` listed files in shorthand, such as `web/…`, `(under X)`
and bare names relative to an earlier entry. When the review was rebased, the
`Files` column had to be parsed to map changed files to units, and that took
two attempts. Short references in findings had the same problem: a bare
basename resolved to the wrong one of several same-named files until the
renderer learned to prefer the file the finding cites. Write full repo-relative
paths in the backlog.

## Correct your own headline claims out loud

When verification refutes something you already reported to the user as a
top-line result, say so plainly and correct it, rather than quietly amending
the document. In review B, a memory-corruption Critical turned out to be a
bounded overread of a few bytes inside the same struct. It was real and worth
fixing, but High rather than Critical, and nowhere near the memory the report
claimed it corrupted. That correction only happened because newly discovered
findings got the same adversarial treatment as everything else, instead of
being published on the strength of being new.
