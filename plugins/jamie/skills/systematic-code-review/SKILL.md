---
name: systematic-code-review
description: Run a deep-dive, subagent-driven code review of a large codebase — survey and scope it, split it into risk-ordered units, sweep every line with one Opus subagent per unit, adversarially re-check every Critical, High, and Medium finding with a fresh agent whose job is to refute it, synthesize cross-cutting themes into a tiered SUMMARY, then render the whole thing to a browseable HTML site whose file:line references deep-link into Azure DevOps or GitHub at the reviewed commit. All state lives on disk so the run is resumable and self-throttling against usage limits. Also records dispositions on a delivered review: findings fixed, planned as tickets, won't fix, or not a defect. Use for a whole-codebase or whole-subsystem audit measured in tens of thousands of lines, not for reviewing a diff or a pull request.
disable-model-invocation: true
---

# Systematic Code Review

You are running the **systematic code review playbook**. It has seven steps and
three hard gates (**[ STOP / ASK ]**): the scope (step 1), the go-ahead to begin
sweeping (step 3), and anything that writes outside the review directory
(step 6). Never cross one on your own initiative.

**Two rules that define this playbook. Violating either wastes the run:**

1. **All state lives on disk, not in your context.** A completely fresh session
   must be able to read only the files you create and pick up exactly where you
   left off. These runs span hours or days and outlive any one context window.
   The review directory is the **only** record of the run. Nothing about it is
   kept anywhere else, so resuming means being given that directory.
2. **Report findings only. Fix nothing** unless the user explicitly asks, as a
   separate piece of work after the review is delivered.

## Prerequisites

Confirm before step 1, and stop if any is missing:

- **The Agent tool**, with subagents runnable on **Opus**. Every spawn sets
  `model: "opus"` explicitly. A smaller model measurably lowers yield on this
  task — do not silently accept a downgrade.
- **Windows, with cmd and PowerShell.** The throttle is a `.bat` file, the
  bundled usage checker runs PowerShell, and the watcher loop is PowerShell. On
  another OS these must be rewritten first; say so rather than improvising
  equivalents mid-run.
- **A shell**, for the throttle gate and for programmatic extraction in step 6.
- **Python 3.9+ with `markdown`**, for the HTML site. It is rendered during
  the run (step 4) and finished in step 7. Without it the review runs the
  same, and nothing renders until it is installed.

Without the first three, say so and stop. Do not degrade this into a single-threaded
read-everything crawl: that is how a review gets expensive and shallow at the
same time.

## 0. Gather the parameters

**First ask: a new review, or continuing one?**

- **Continuing**: ask for the review directory, then follow
  [reference/resume.md](reference/resume.md) instead of the rest of this step.
  Every parameter below is already recorded in that directory. Don't ask for
  any of them again.
- **Recording dispositions** on a delivered review, such as marking findings
  fixed or planned, or reconciling a private `fix-findings`, `fix-light` or
  `fix-seam` run once its fixes have merged: ask for the review directory,
  then go to *After delivery: recording dispositions* below. Nothing else in
  this playbook runs.
- **New**: ask for everything below. Once the output location is known, check
  it. **If `<review dir>/backlog.md` already exists, stop**: a review is already
  there. Ask whether to resume it or choose another location. Never build a
  new review over an existing one.

For a new review, ask for anything not already known:

- **Code root** — absolute path to the tree under review: **one** git
  repository, or one source tree that is not in git. Several repositories,
  submodules, or a repository nested inside the code root are **out of
  scope** for this skill. Step 1 checks for them and stops unless they are
  excluded from scope. Review each repository as its own review.
- **Output location** — where the review directory goes. One of two modes:
  - **In-tree** (default): `<code root>/docs/code-review/`. Either it is
    committed alongside the code, or, in a repository the user won't commit
    review files to (someone else's project), it is its own nested git
    repository, which the code repository ignores (step 2).
  - **External**: any directory outside the code root, typically in a separate
    repository that tracks reviews. Use it for code you may not write to, such as
    third-party code or someone else's repository. In this mode the **code root is
    read-only**: create, modify, and delete nothing under it — not state files,
    not scratch files, not HTML. Read files and run read-only git queries only.

  Call the chosen directory `<review dir>` from here on, and record the mode in
  its `README.md`.
- **Platform description** — language, chip/OS/RTOS, key middleware. One or two
  sentences on what the system does *and what talks to it*. This feeds every
  subagent prompt and is the single biggest lever on finding quality.
- **Exposure model** — who can reach this code: network-facing, operator-only,
  physically-present-technician-only. Reviewers systematically over-rate
  operator-only debug paths without it.
- **Known field failures** — any prior symptoms and their suspect areas.
- **Batch size** — default **3** concurrent subagents.
- **Usage gate** — what pauses the sweep when usage runs short. One of:
  - **Slack checker**: the bundled
    [scripts/slack-check.bat](scripts/slack-check.bat), which asks the usage
    trayapp running on this machine whether there is room to run.
  - **Another usage-check script**: its path. It must exit 0 to mean go.
  - **None**: the throttle pauses only on a manual `PAUSE.flag`.

  **Test the choice now**, before step 1. Run the script once without `-q` and
  show the user the output. See
  [reference/throttle.md](reference/throttle.md) for what each exit code means.
  If the trayapp is unreachable (exit 2), stop and ask: start it, or run with
  no usage gate. Never switch to no gate silently.

## 1. Scope and survey   [ STOP / ASK ]

Before proposing anything, **measure** the codebase: file count, line count,
directory structure, and which parts are first-party versus
vendor/third-party/generated. Report what you find.

Default scope is **first-party source only.** Vendor middleware, generated code,
and register-definition headers are excluded unless the user says otherwise —
reviewing them multiplies cost for low yield. State explicitly what you are
excluding and roughly how many lines that is.

**Check for more than one repository.** If the code root has submodules or
another repository nested inside it, and they are in scope, stop: that is out
of scope for this skill (see step 0). The commands are in
[reference/tree-identity.md](reference/tree-identity.md).

**Record the tree being reviewed**, following
[reference/tree-identity.md](reference/tree-identity.md). It has the exact
commands for each case. In external mode those commands are the read-only
forms: no `git fetch`, no plain `git status`.

- **A git repository**: its `origin` URL, branch, full `HEAD` sha, and whether
  `HEAD` is on the remote.
- **Code that is not a git repository**, such as a vendor drop or tarball: its
  version label, if any, and a content hash of the tree.

**A git review is a review of committed code.** Reviewers read the files on
disk, but every finding is pinned to the commit. The two only agree while the
working tree is clean and at that commit. So:

- **A dirty working tree stops the review here**, untracked files included.
  The user commits or stashes their changes, or points the review at a clean
  checkout of the commit they want reviewed. In external mode you cannot do
  any of that yourself; say what is needed.
- **The tree must stay clean and at the pin until verification ends.** Tell
  the user this at the gate. If they need to keep working in that repository
  during a run of several days, suggest reviewing a separate clean checkout,
  such as a `git worktree` of the commit, with an external output location.

**Decide here whether the review will have links.** Step 7 links every
reference to its line at the recorded commit, in Azure DevOps or on GitHub,
whichever hosts `origin`. That works only if the host has that commit.
Committed is not the same as pushed. Links will not work if any of these is
true:

- `HEAD` is not on the remote, or that can't be confirmed.
- The remote is neither Azure DevOps nor GitHub.
- The code is not in git at all.

If any is, **ask the user which they want**:

- **Stop and correct it first**, by pushing the commit or reviewing one that
  is already pushed. With third-party code, nobody here can push.
- **Continue without links.** The review is the same; its references render
  as plain text.

Record the answer in `## Run parameters`. In `render.json`, a GitHub repo
gets `"host": "github"` and its `"github_base"`; an Azure DevOps repo needs no
`host` (it is the default) but needs an `ado_base`; a review without links
gets `"links": false`. This is the only point where the
choice is cheap: the pin can't change once findings are written against it.

**Survey the test harness.** Every finding is rated for whether a failing test
can prove it (the RGR scale in [reference/scales.md](reference/scales.md)),
and reviewers can only rate that against a harness they are told about. For
each part of the scope, find out from the tree: the test framework, whether
tests build and run on the host or only on target or a simulator, how they are
run, and which test doubles exist for hardware or OS layers. Read test
directories, build files and CI configuration. **Build and run nothing**: this
is a survey, and in external mode the code root is read-only. Code with no
harness is a result worth reporting. Every finding in it will be Seam or No.

- **Halt here.** The user confirms scope before you build anything.

## 2. Build the work queue

Create `<review dir>`:

```
README.md      # run parameters, reviewed tree, process, scales, finding format
backlog.md     # the work queue + a running log
render.json    # the reviewed commits and link mapping — see below
throttle.bat   # usage gate — see reference/throttle.md
slack-check.bat  # copied from this skill, only when the slack checker is chosen
findings/      # one doc per unit, created as the review runs
RUN.lock       # present while a session is running the review — see step 4
SUMMARY.md     # written at the very end
.gitignore     # html/, RUN.lock, PAUSE.flag, slack-check.log
```

**Put the review under git now.** A review is read as diffs once it exists:
dispositions, fixes and the `rebase-findings` skill all work on the review's
files as tracked, unmodified files, and a rebase refuses to start without that.
Choose with the user:

- **In-tree, in the code's repository**, when they will commit review files there.
- **In-tree, as its own repository**: `git init` inside `<review dir>`. The
  outer repository must ignore it, or every `git status` there shows it. Prefer
  adding `/docs/code-review/` to the outer repository's `.git/info/exclude`,
  which is never committed, over its `.gitignore`, which would be a change to a
  repository the user doesn't own.
- **External**: the reviews repository the output location already names.

Set `core.autocrlf false` in the review's own repository, so files keep the
line endings they are written with. Whether and when to commit is still the
user's call (see *Gates on delivery*); creating the repository is not a commit.

**Write down everything a resumed session needs, now.** A fresh session must
not have to ask the user again, because different answers halfway through
would split the review into two reviews with different premises.

- A `## Run parameters` section in `README.md`: every step 0 answer, verbatim.
  That means the code root, output mode and `<review dir>`, platform
  description, exposure model, known field failures, batch size, and usage
  gate. Also record the scope confirmed at step 1: what is included, and what
  is excluded with its approximate line count.
- A `## Reviewed tree` section in `README.md`: the repository's remote,
  branch, and sha from step 1, or the tree hash for code not in git.
- A `## Test harness` section in `README.md`: the step 1 survey, per part of
  the scope, including the parts that have none. It goes verbatim into every
  reviewer and verifier prompt.
- A short `## Resuming` note in `README.md`: to resume, invoke this skill,
  choose *continuing*, and give it this directory.

**The reviewed commits especially.** Also create `render.json` with those shas
as the pins (its format is in
[reference/html-rendering.md](reference/html-rendering.md)). Line numbers in
the findings are only meaningful against these commits. Recovering them after
the fact is slow and error-prone, and impossible to do from commit dates when the
review lives in a different repository from the code.

**Unit design.** Group related files into units of roughly **500–2,000 lines** —
a source file with its header, or a small cluster of related modules.

- **Never review a source file without its header.** Cross-file defects are
  exactly the ones single-file review misses.
- **Order by risk:** protocol, transport, parsing, and anything network-facing
  first; leaf utilities and vendor-derived code last.
- Expect **30–50 units per 50k lines**.
- **Write every file in the `Files` column as a full repo-relative path**, such
  as `src/net/http_parse.c`. No shorthand prefixes, no "(under dir/)" groups,
  no names relative to an earlier entry, and no absolute `C:\...` paths. A
  resume, `rebase-findings` and the next review map changed files to units by
  this column, and every shorthand has to be parsed back first. A glob like
  `src/net/**` is fine where a unit really is a whole directory.

`backlog.md` is a table — `ID | Unit name | Files | ~Lines | Focus | Status`
with status `pending | in-progress | done` — plus a `## Log` section at the
bottom. `Focus` is the per-unit severity emphasis from step 4, decided now and
written down, so a resumed session builds the same prompt the original would
have. Append one line per completed batch with per-unit finding counts. **That
log is what makes the run auditable and resumable.** The log also records the
milestones a resume reads to find its place. Each is a line starting with the
exact words `Go-ahead given` (step 3) or `SUMMARY written` (step 6).

Put the severity, confidence, fix impact, RGR and status scales, and the
finding format, from [reference/scales.md](reference/scales.md) into `README.md`
**verbatim**. They also go verbatim into every subagent prompt, or, when the
orchestrator's context is the constraint, the prompt points at those `README.md`
sections, which hold the same text (see *Prompt size* in
[reference/reviewer-prompt.md](reference/reviewer-prompt.md)). The rigidity
is load-bearing: step 6 parses these fields with regex, and a unit doc that
drifts from the format silently drops out of the totals.

## 3. Set up the throttle, then get the go-ahead   [ STOP / ASK ]

Build and test `throttle.bat` per [reference/throttle.md](reference/throttle.md),
wired to the usage gate chosen in step 0. With the slack checker, **copy**
`scripts/slack-check.bat` into `<review dir>` and call that copy; don't call it
where the plugin is installed. A plugin update replaces that directory, and a
throttle pointing into it breaks partway through a run that lasts days.
Its entire contract is **exit 0 = CONTINUE, any non-zero = PAUSE**, and the
orchestrator must only ever read the exit code — so the gate's internals can be
replaced without touching the review.

**Test it in both states** (create `PAUSE.flag`, confirm non-zero; remove it,
confirm it delegates) and show the results.

- **Halt here. Do not begin sweeping until the user says to start.** When they
  do, append `Go-ahead given <date>` to the log and create `RUN.lock` (see
  step 4) before the first batch.
- When you ask, say that the findings can be read as the run goes: the HTML
  site under `<review dir>/html/` fills in as each unit finishes (step 4).

## 4. The sweep

**Never edit `backlog.md`, `RUN.lock` or a findings doc's bookkeeping with
ad-hoc shell commands.** PowerShell writes CRLF, bash `>>` writes LF, and
Python's text mode strips CRs; mixed line endings turn the user's diff into
noise, and a stray escape can split a table row. Use
[scripts/review_state.py](scripts/review_state.py), which keeps every line's
ending as it was:

```powershell
python <skill dir>\scripts\review_state.py status <review dir>\backlog.md U07 U08 --set in-progress --lock <review dir>\RUN.lock
python <skill dir>\scripts\review_state.py check-unit <review dir> U07-parser.md
python <skill dir>\scripts\review_state.py log <review dir>\backlog.md "U07 done: 0 Critical, 2 High, 3 Medium, 1 Low" --lock <review dir>\RUN.lock
```

Orchestrator loop:

1. Run `throttle.bat`. Exit 0 → proceed. Non-zero → pause.
2. **On pause**, background a watcher that re-runs `throttle.bat -q` every ~3
   minutes and exits 0 the moment the gate clears. **Cap it:** if the pause
   outlasts ~6 hours, or the usage script is unreachable for ~15 minutes, stop
   and report — a dead usage service never resolves on its own, and because all
   state is on disk, stopping costs nothing.
3. Mark the next N units `in-progress` **in `backlog.md` before spawning**
   (`review_state.py status`), then spawn **one Opus subagent per unit** using
   [reference/reviewer-prompt.md](reference/reviewer-prompt.md). The file must
   always say what was handed out: a crash between spawn and bookkeeping is
   exactly what [reference/resume.md](reference/resume.md) repairs.
4. **As each subagent returns, check its unit before marking it `done`.** A
   unit is done only when the subagent returned its count line *and* its
   findings doc exists and contains a `## Coverage notes` section, wherever it
   falls. Reviewers write that section last, so a doc cut short doesn't have
   one. `review_state.py check-unit` checks this, counts the doc's findings,
   and reports any format drift, such as `Unit:` where the format says
   `Unit — `: fix the reviewer prompt after the first drift, not 30 docs
   later. Anything
   else is a failure: an error, a usage-limit stop, prose instead of counts, or
   a doc cut short. For a failure, delete the partial doc, set the unit back to
   `pending`, and log why. A usage-limit failure means the batch outran the
   budget, so run the throttle before the next batch. **A unit that fails twice
   stops the run.** Report it; it may be too large, or something in it may be
   derailing the reviewer.
5. **Re-render the site** once the batch's units are marked:
   `python <skill dir>/scripts/render_review_html.py <review dir> --quiet`.
   It renders every unit `backlog.md` marks done and shows the rest as
   placeholders, so a doc still being written never appears. It changes no
   markdown and prints one line, plus any `WARNING`. Read the warnings: format
   drift found after the first batch is fixed in the reviewer prompt, not in
   30 docs. **After the first unit**, render once without `--quiet` and set up
   the links, per *Rendering during the run* in
   [reference/html-rendering.md](reference/html-rendering.md), then give the
   user the path to `html/index.html`. **A failed render never stops the
   run**: log it once and carry on. Step 7 renders again.
6. Repeat from 1.

**Run it as a rolling queue.** There is no need to wait for a whole batch: as
each unit is marked done, run the throttle and hand out the next unit, so N
reviewers stay busy. Once the last units are out, verify jobs (step 5) may
start for docs whose units are done, still within N agents in all and never
for a doc a running job holds.

**`RUN.lock` marks a live run.** It holds the time the session started and a
`heartbeat` time. Update the heartbeat whenever you write `backlog.md`. Delete
the lock whenever you stop the run yourself: finished, capped, or asked to
stop. A lock left behind means a session crashed, or is still running
somewhere. A resume never takes it over without asking (see
[reference/resume.md](reference/resume.md)), because repairing a live run's
`in-progress` units would delete docs that are still being written.

**Tailor the severity emphasis per unit**, taken from its `Focus` column. This
measurably improves yield, and
the per-unit guidance in the reviewer prompt reference is not optional
boilerplate. Debug and operator-only paths in particular must have their stakes
explicitly *lowered*, or reviewers inflate them.

**Every subagent returns one line of counts only.** That is what lets a single
session orchestrate ~50 subagents across sweep and verification without
exhausting its context.

## 5. Adversarial verification

**Every Critical, High, and Medium finding** gets re-checked by a **fresh**
Opus agent whose job is to **refute** it, using
[reference/verifier-prompt.md](reference/verifier-prompt.md). Only Low findings
(smells, dead code, minor inefficiency) go unverified.

- Group into verify jobs of ~10–12 findings, **by whole findings docs**, and
  run 3 at a time under the same throttle loop. **A findings doc is in at most
  one running job at a time.** Verifiers edit the docs in place, and two agents
  rewriting one file lose each other's edits.
- Track them in a `## Verification queue` table in `backlog.md`, above
  `## Log`:
  `Job | Findings docs | Findings to verify | Notes | Status`, with the same
  `pending | in-progress | done` statuses, set the same way as units. `Notes`
  holds any anomaly flagged for that job's prompt, so a re-run gets it too. A
  job is `done` only when every Critical, High, and Medium finding in it has a
  verdict, which `review_state.py check-verify <review dir> <docs>` checks
  (exactly one Verification bullet each, by the severity each had before any
  verifier changed it). **Re-render the site as each job is done**, as in step 4: verifiers
  rewrite verdicts, severities and statuses, and the site should show them.
- Verifiers **edit the findings docs in place**. They append a
  `- **Verification:**` bullet, and may rewrite four other bullets when the
  evidence says so: **Severity**, **Fix impact**, **RGR** and **Depends on**,
  which they may also add. Each rewrite
  keeps the old value on record as `(verifier: was <old>)`, in the form given in
  [reference/scales.md](reference/scales.md). A severity can move either way:
  down when the reach is narrower than claimed, up when a Medium is worse than
  rated. A refuted finding also has its **Status** rewritten to
  `Not a defect — refuted at verification`, so the index stops counting it as
  open. They change nothing else, and never touch Low findings.
- Where a unit's result looks anomalous — an unexpectedly hot count for a
  trivial-seeming file, or the highest Critical count in the review — say so in
  that verifier's prompt and ask for extra scrutiny. **Verify anomalies, don't
  assume them**; this has cut both ways in practice.

It refutes findings, and it also *corrects* the mechanism or blast radius of
others that survive. In the runs this skill was built from, it refuted ~6–9%
of Critical/High findings. See [reference/field-notes.md](reference/field-notes.md).
Those runs verified only Critical and High, at ~20–30% extra. Mediums typically
outnumber Critical and High together, so budget for verification to cost
roughly as much again as the sweep. One run that verified Mediums too
refuted under 1% of its Critical, High and Medium findings, while its
verifiers corrected nearly a quarter of the severities, so record both rates in the method
notes: a low refutation rate with many corrections means checking, not
rubber-stamping.

Verifying Mediums also catches **under-rating**. A real Critical filed as a
Medium is most likely in units whose Focus lowered the stakes, when that
premise turns out to be wrong. A verifier that finds one raises its severity.

## 6. Synthesis   [ STOP / ASK before writing outside the review directory ]

Extract the structured fields from all findings docs **programmatically** —
the `**Severity:**` / `**Confidence:**` / `**Category:**` /
`**Fix impact:**` / `**RGR:**` / `**Depends on:**` / `**Verification:**` /
`**Status:**` bullets —
rather than re-reading them into context.
`python <skill dir>\scripts\review_state.py extract <review dir> --json <scratch>\findings.json`
does the extraction, prints every cross-check below and the tables the summary
is built from, and groups the `Seam (harness)` findings by harness name;
`review_state.py drift <review dir>` lists any format drift to fix first. **Cross-check the verdict count
against the number of findings that are not Low, counting each by its
severity before any verifier changed it. Also cross-check the fix-impact,
RGR and status counts against the total number of findings. Every status must
be `Open`, except that each REFUTED finding is `Not a defect`. A mismatch
means a unit doc drifted from the format.** Severity is its first word; a
trailing `(verifier: was …)` records where it came from. Find and fix the
drift before writing the summary.

**Write the cross-cutting testability findings**, before the summary, per
*Cross-cutting findings* in [reference/scales.md](reference/scales.md):

1. Extract every `Seam (harness)` finding with the harness its RGR names.
   Group them by harness. Reviewers were told to name a harness the same way
   each time, but merge names that plainly mean the same thing.
2. For each harness, check the tree for it, and write one `X-` finding in
   `findings/X-cross-cutting.md`. Its Description gives that evidence and the
   number of findings waiting on it. Its RGR names one dependent whose test
   will prove it. End the doc with a `## Coverage notes` section, as a unit
   doc does.
3. Add `- **Depends on:** X-<n>` to each dependent, after its RGR bullet. Add
   it to a dependent that already has the bullet by appending the ID. Skip a
   refuted finding: it keeps its rating, but nothing waits on a fix for it,
   and it doesn't count toward the harness's dependents. Change nothing else
   in the unit docs.
4. Render, and read its warnings: it reports any Depends on that names no
   finding, any cycle, and any `Seam (harness)` finding left without a
   prerequisite. Fix each one.

A harness found in only one finding still gets an `X-` finding. Its
disposition is a decision of its own. If a resumed session finds
`X-cross-cutting.md` already there, check it against the grouping and
complete it rather than writing it again.

**Complete the dependencies between findings**, per *Depends on* in
[reference/scales.md](reference/scales.md). Reviewers and verifiers recorded
the ones inside a unit, and these two checks catch what they missed:

1. **Mentions without a link.** Programmatically list every finding whose
   text names another finding it has no `Depends on` to, with just the
   sentence that names it. Read those sentences, not the docs. Where one
   states an order or a shared fix, add the bullet. Most mentions are "makes
   it worse" or "see also", and stay as they are.
2. **Across units, from the themes.** Reviewers can't see other units' IDs,
   so cross-unit dependencies come from synthesis. As you group findings into
   themes, note where a theme's findings share one fix, or one has to land
   first, and add `Depends on` for those. A theme that is one design decision
   doesn't make its findings depend on each other: each still has its own fix.

Drop any dependency on a refuted finding: what it names isn't there. Render
again and read the warnings.

Write `SUMMARY.md` per the structure in
[reference/summary-structure.md](reference/summary-structure.md). The
**cross-cutting themes** section is the most valuable part of the entire
deliverable: individual findings are a work list, themes tell the reader what is
systemically wrong. When it is finished, append `SUMMARY written <date>` to the
log, and re-render so the index links the summary. A `SUMMARY.md` without that
line is treated as a draft cut short.

Five things the summary must say plainly, because readers will otherwise
misuse it:

- **Low findings were never verified.** A confirmed Medium is therefore
  more trustworthy than an unverified Low, whatever the labels suggest. Lows
  are also the one place under-rating could still hide.
- **The Verification line overrides the rest of the finding.** Verifiers
  corrected the severity, mechanism or blast radius of findings that were still
  confirmed. Severity changes are already in the numbers; say how many there
  were, and in which direction.
- **Operational consequence can exceed severity.** A "High" that makes failed
  upgrades report success outranks most Criticals in practice. Re-rank by real
  consequence and say so.
- **Fix impact routes fix work; it does not authorize it.** A Trivial fix to an
  unverified Low, or to an UNCERTAIN finding, may fix a defect that isn't
  there. Only CONFIRMED findings,
  or findings the fixer re-confirms first, are ready for lightly supervised
  fixing; Uncertain ones need a planning step first.
- **RGR says how a fix gets checked.** Unit means a test can prove both the
  defect and the fix. No means a person has to. A partly testable No still
  needs a person for its residual, whatever its test shows.

Also report honestly on your own run: which units were reviewed under a premise
you later discovered was wrong, and which verifier prompts lacked refutation
levers (a job returning 0% refutation against a run averaging ~9% is a signal
about the prompt, not the code). Put that in a method-notes appendix rather than
smoothing it over.

**Gates on delivery:**

- The review directory is yours to write. **Anything else — indexes, README
  entries, git commits — requires asking first.** Leave the deliverables
  uncommitted until the user decides, though the repository step 2 set up may
  already exist. Say that `rebase-findings` needs the review committed. That includes a commit in a separate reviews
  repository in external mode: tracking the review in git is the point of that
  repository, but when to commit is still the user's call.
- In external mode, nothing is ever written under the code root, gate or no
  gate. Offering a fix there is out of scope as well.
- **Never push.** These documents catalogue exploitable defects in shipping
  code; publishing them is the user's deliberate call, never a side effect. For
  third-party code, a reviews repository should stay private for the same
  reason. Say so if its remote looks public.

## 7. Render to browseable HTML

**The HTML site is how the findings actually get read**, so it is built during
the run (steps 4–6) and finished here. It has a sidebar of units, severity,
fix impact and verification badges on every finding, and each `file.cpp:123`
reference deep-linked to that exact line in Azure DevOps or on GitHub. Code
with no hosted copy (see step 1) renders the same, with plain-text references. Reading
a review of several hundred findings as raw markdown is materially worse.

Once `SUMMARY.md` is written, render everything once more **without
`--quiet`** and read every line it prints. That final render is this step.
If nothing rendered during the run, because Python was missing or a review
predates live rendering, offer it now.

Rendering changes no markdown, so it can also run much later against a
finished review. The generator ships with this skill,
as [scripts/render_review_html.py](scripts/render_review_html.py). Run it from
there against `<review dir>`, and do not copy it anywhere. It writes only
`<review dir>/html/`, so it is safe in external mode. Follow
[reference/html-rendering.md](reference/html-rendering.md). The three things
that go wrong:

- **Links must be pinned to the commit the review was written against**, not to
  a branch, or every reference slides as fixes land. The pins are the shas in
  `render.json`, recorded in step 2. For an older review that never recorded
  them, recover the candidate commit by date, then **prove** it by
  spot-checking findings against files the candidate commits actually differ
  in.
- **The pinned commit must be pushed.** A correct sha that exists only locally
  404s every link while the render reports success — this shipped thousands of dead
  links once. Step 1 settles this before the sweep. The generator checks it
  again on every run; read those lines. If it fails anyway, because the commit
  was never pushed or history was rewritten, **don't re-pin**: the findings'
  line numbers belong to this commit. Tell the user. They can push it, or you
  can render without links (`"links": false`) if they agree.
- **Format drift surfaces here.** Cross-check the index's per-severity totals
  (in each card's `aria-label`; the card itself draws squares) against
  `SUMMARY.md`; a mismatch means a finding drifted out of format and is
  silently missing features. The generator also warns about any finding with
  no Status bullet, or a status it doesn't recognise.

Leave the generated output **untracked**: the review's `.gitignore` leaves
`html/` out. The step 6 delivery gate still applies: committing it, which
means taking it out of `.gitignore`, is the user's call (see *Committing* in
the rendering reference).

---

## After delivery: recording dispositions

A delivered review is a work list, and people then decide what happens to each
finding: fixed, handed to a ticket, accepted, or shown not to be a defect. That
decision goes in the finding's **Status** bullet. On the index, each Open
finding is a solid square and each dispositioned finding a hollow one, so the
solid squares are exactly what still needs a decision.

Record dispositions only when the user asks. It is later work, like fixing,
and not part of the run. Follow
[reference/dispositions.md](reference/dispositions.md). It says what each
status needs as evidence, what else may change (nothing), and why
`SUMMARY.md` is left as delivered. The review directory is still yours to
write, and the step 6 delivery gates still apply.

---

## Downstream work this playbook does not cover

Real runs have grown these afterwards. They are **separate, later asks** — do
not start any of them as part of a review:

- `security-triage.md` — the exposure-model cut across findings.
- `relationships.md` — which findings share a root cause or interact.
- `fix-campaign/` — turning confirmed findings into sequenced, ticketed work.
  Its results are recorded as dispositions, as above.
- Fixing findings — the `fix-findings` skill fixes any of them, by ID, by
  filter or all, one at a time and unattended after one go-ahead: harnesses,
  seams, red-green fixes, and fixes no test can prove. The `fix-light` skill
  fixes findings with a Trivial or Low fix rated RGR Unit or `Seam (local)`,
  and `Seam (harness)` ones once their harness is in. The `fix-seam` skill
  builds the harness an `X-` finding names. A private run of any of them is
  recorded by reconciling, as above.
- Moving the review to a newer commit — the `rebase-findings` skill
  re-anchors every reference to the new pin, re-evaluates the findings the
  new commits could affect, and reviews the new code.
