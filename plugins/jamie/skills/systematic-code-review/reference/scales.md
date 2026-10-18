# Scales and finding format

Reproduce all six of these **verbatim** in the review's `README.md` and in
every subagent prompt. The rigidity is load-bearing: the synthesis step parses
these fields with regex, and a unit doc that drifts from the format silently
drops out of the totals. The last section, *Cross-cutting findings*, is for
synthesis. It goes in `README.md` too, but not in subagent prompts.

## Severity scale

- **Critical** — likely crash, data corruption, hang, or security hole reachable in normal operation
- **High** — real defect with operational impact (wrong results, resource leak, race) under plausible conditions
- **Medium** — defect reachable only in edge cases, or robustness gap (missing error handling on likely failure)
- **Low** — code smell, maintainability hazard, minor inefficiency, dead code

## Confidence scale

- **High** — demonstrable from the code alone; the reviewer can state concrete failing inputs/state
- **Medium** — strong indication but depends on runtime behavior or an external contract not fully visible
- **Low** — suspicious pattern; needs domain knowledge or hardware behavior to confirm

## Fix impact scale

How risky it is to *fix* the defect, judged from what the fix would have to
touch, without designing it. This is independent of severity: a Critical can
have a Trivial fix, and a Low can need a redesign.

- **Trivial** — a few lines in one function. No change to any signature, data layout, timing, or observable behavior other than removing the defect (a wrong bound, a missing check with an obvious existing error path, a wrong constant)
- **Low** — confined to one module; every affected caller is visible and there are few; behavior changes only on the failure path; touches no shared state, locking, persisted or wire format, or timing-sensitive path
- **Uncertain** — anything else, including moderate or larger fixes and any fix whose reach cannot be judged without designing it. Always Uncertain if the fix touches concurrency or locking, a persisted or wire format, an ISR or timing-sensitive path, a public interface, or more than one unit

**When in doubt, rate up.** Misrating a fix as too safe is the expensive mistake,
because a low rating is what lets a fix proceed with less oversight. A fix that
changes behavior callers might depend on is never Trivial, however small the
diff.

## RGR scale

Whether the defect can be fixed red-green-refactor: a test written first that
fails because of this defect, then passes once it is fixed. Rate it against
the test harness recorded in the review's `README.md`: what builds and runs
on the host, with which framework, and which test doubles already exist. It is
independent of severity and of fix impact. A one-line fix to an ISR race may
have no test at all, and a parser bug whose fix is Uncertain may be easy to
test.

- **Unit** — the recorded harness can run a test today that fails for this finding's failure scenario, at a boundary that already exists: call this function with this input, and see the wrong result. Name the boundary
- **Seam** — a test is possible, but only after adding something the harness lacks. Always say which kind, in parentheses:
  - **Seam (local)** — a small refactor of the code under review that exposes the boundary, such as exporting a function or wrapping a script's top level in a `main()` behind a run-directly guard, or a test double that lives only in this finding's test. The refactor itself must rate Trivial or Low on the fix impact scale. Name the refactor, then the test
  - **Seam (harness)** — anything else: new test infrastructure other tests would use (a test project, an integration fixture, a shared fake), an environment the harness can't provide (a real database, a runner for scripts), or a refactor too large to be local. Name the harness the same way every time it comes up, because findings that need the same harness are grouped by that name
- **No** — no test can show it: timing, concurrency, hardware behavior, or effects that emerge across units. A person has to verify the fix

**Partly testable is No.** When a test can reach part of the defect but not
all of it, such as the logic flaw but not the timing that triggers it in the
field, rate it No and give the split in this form:

```markdown
- **RGR:** No — partly testable: test: <what a failing test would prove>; residual: <what it can't, and how that would be checked>
```

Its test is still written first. It proves the logic is wrong, and it narrows
what a person has to check to the residual. But the test passing never makes
the finding Fixed on its own.

**When in doubt, rate toward No.** Misrating is expensive in the same
direction as fix impact: Unit is what lets a fix go through with the least
oversight. For the same reason, a Seam that might not be local is
`Seam (harness)`.

## Status scale

What has been decided about the finding. Every finding is written `Open`. The
review itself sets only one other value: a verifier that refutes a finding sets
`Not a defect`. Every other change comes after the review is delivered.

- **Open** — not yet dispositioned: nobody has decided what happens to it
- **Fixed** — the defect is gone. Cite the fix commit and the test that covers it
- **Planned** — handed to another system that now owns it, such as a ticket or a plan document. Cite it. This is a disposition, not a promise: the finding may never be marked Fixed here, because tracking it is now the other system's job
- **Won't fix** — the defect is real and has been accepted. Give the reason, who decided, and the date
- **Not a defect** — the finding is wrong. Give the evidence: a REFUTED verdict, or what was found later

Every status except Open means the finding is dispositioned. A review is fully
dispositioned when nothing is Open, which is not the same as everything being
fixed.

## Finding format

Every finding is a `###` heading with this exact bullet structure:

```markdown
### <UNIT-ID>-<n>: <one-line title>
- **File:** path/to/file.ext:123
- **Severity:** Critical | High | Medium | Low
- **Confidence:** High | Medium | Low
- **Category:** memory-safety | concurrency | error-handling | protocol-logic |
                resource-leak | arithmetic | initialization | api-misuse | security | style |
                testability
- **Description:** what is wrong and the concrete failure scenario (inputs/state -> wrong behavior)
- **Suggested fix:** brief
- **Fix impact:** Trivial | Low | Uncertain — <one clause on what the fix touches>
- **RGR:** Unit | Seam (local) | Seam (harness) | No — <the test boundary, the seam it needs, or why no test can show it>
- **Depends on:** <finding IDs, only when another finding's fix must land first or will cover this one; otherwise omit the bullet>
- **Status:** Open
```

**Depends on** is optional. It lists prerequisites only, never dependents;
the rendered site works those out. A finding depends on another when either
holds:

- **Order** — the other's fix has to land first: it changes the code this fix touches, or settles a decision this fix needs, so this one can't be fixed or tested before it
- **Shared fix** — the other's fix is expected to remove or change this defect too, so fixing this one first would be wasted or would conflict. Point the narrower finding at the broader one, never both ways

Findings that only make each other worse, or are worth reading together,
don't depend on each other. Say that in the Description, by ID, as before.
Reviewers record dependencies between findings in their own unit, verifiers
check them, and synthesis adds the cross-unit ones: every `Seam (harness)`
finding's dependency on its harness finding, and any between units that the
themes show. The `testability` category is for the harness findings only.
Reviewers never use it.

**One failure mechanism per finding.** A finding that describes two defects,
such as a missing bounds check and an unlocked write to the same buffer, is
two findings. Severity, fix impact and RGR each need one answer, and a
compound finding has none.

Findings are numbered `<ID>-1`, `<ID>-2`, … and sorted by severity
Critical→Low, ties broken by confidence High→Low.

The verification pass later appends one more bullet to every Critical, High,
and Medium finding:

```markdown
- **Verification:** CONFIRMED | REFUTED | UNCERTAIN — <1-2 sentence rationale with file:line evidence>
```

A verifier can also rewrite four other bullets, always in place and always
keeping the old value on record, so the numbers follow the correction:

```markdown
- **Severity:** High (verifier: was Critical)
- **Severity:** High (verifier: was Medium)
- **Fix impact:** Uncertain — <new reason> (verifier: was Trivial)
- **RGR:** No — <new reason> (verifier: was Unit)
- **RGR:** Seam (harness) — <new reason> (verifier: was Seam (local))
- **Depends on:** U12-3 (verifier: was U12-3, U12-5)
- **Depends on:** none (verifier: was U12-5)
```

A verifier that adds a dependency writes `(verifier: was none)`.

Severity can move either way. The reason goes in the Verification bullet. The
level is always the first word, so parsing is unchanged.

A verifier that refutes a finding also rewrites its Status bullet in place:

```markdown
- **Status:** Not a defect — refuted at verification
```

After delivery, recording a disposition rewrites the Status bullet in place,
and nothing else in the finding. Whatever follows the level is its evidence:

```markdown
- **Status:** Fixed — `<commit sha>` — <what changed, and the test that covers it>
- **Status:** Fixed — in this commit — <what changed>; red-green: <test file>::<test name>
- **Status:** Planned — <ticket ID or URL, or plan document>
- **Status:** Won't fix — <reason>; <who decided>, <date>
- **Status:** Not a defect — <evidence>
```

The `in this commit` form is for a review committed in the code repository,
where the Status change is part of the fix commit itself. It cites no sha,
because a squash or rebase would change it: the commit that set the line is
the fix, and `git log -S` on the line finds it.

When one disposition replaces another, the earlier one stays at the end, for
example `(was Planned — <ticket>)`. The level always comes first, so parsing
is unchanged.

## Cross-cutting findings

Synthesis writes one finding per harness that `Seam (harness)` findings
need, in `findings/X-cross-cutting.md`, numbered `X-1`, `X-2`, …, in the same
format:

```markdown
### X-<n>: <what is missing, e.g. "The service projects have no test project">
- **File:** <the directory or file the harness would cover>
- **Severity:** Low
- **Confidence:** High
- **Category:** testability
- **Description:** what is missing, the evidence that it is (no test project in the tree, no fixture, no runner), and how many findings wait on it
- **Suggested fix:** the harness, briefly
- **Fix impact:** Trivial | Low | Uncertain — <what building it touches>
- **RGR:** Unit — proven when <a dependent's ID>'s test fails for its finding's reason
- **Status:** Open
```

A missing harness is a maintainability hazard, so it is Low. It doesn't
inherit its dependents' severity, which would count their defects twice. Its
weight is what it unlocks, which the summary and the rendered site show. Its
RGR names the dependent whose red test will prove the harness works: a
harness that enables no failing test isn't the harness those findings need.
It isn't verified adversarially. Its evidence is in the Description, checked
against the tree when synthesis writes it.
