# systematic-code-review — maintainer notes

Nothing links to this file, so it never loads while the skill runs.

## What the skill is for

This skill is for the review you do once in a while, not the one you do on
every change: a deliberate audit of a whole codebase, or a whole subsystem,
measured in tens of thousands of lines. Its output is a catalogue of defects
that is complete enough to plan against. A diff review asks "is this change
safe?". This asks "what is wrong with this system, and where?". The two need
different machinery, and the skill says in its description that it is not for
the first kind.

## The central idea

A single model reading a large codebase end to end has too little attention
for it. Depth goes shallow, because context fills and early detail is
forgotten. Breadth goes patchy, because the model skims whatever looks
uninteresting. The skill trades that for many narrow, deep reads. The codebase
is cut into units small enough for one agent to read every line, with each
source file kept with its header so cross-file defects stay visible. Each unit
gets a fresh Opus subagent that knows only that unit, the platform, and who can
reach the code.

The orchestrating session does no reviewing itself. It is a thin loop that hands
out units, records results, and watches the usage budget. Everything it knows
lives in files. Subagents report back a single line of counts, not prose, which
is what lets one session coordinate some fifty agents without filling its own
context.

## The disk is the memory

The run's state is kept in its files, not in any conversation: the backlog,
the log, the findings docs, and the recorded parameters and commits. Real runs
last days, span several sessions, and outlive usage limits, service outages,
and reboots. A run that depended on one session's context would be lost at the
first interruption. One kept on disk can stop at any moment and continue from
where it was.

That idea shapes several smaller choices:

- **Everything the review needs is recorded before sweeping starts.** That
  includes the step 0 answers, the confirmed scope, each unit's severity focus,
  and the exact commits under review. A session that resumes the run reads
  these rather than asking again. Different answers halfway through would
  quietly turn one review into two reviews built on different premises.
- **Status is written before work is handed out.** A unit marked
  `in-progress` that never became `done` is how a crash shows up on disk, and
  the resume procedure is built around repairing exactly that state.
- **The review directory is self-contained.** The throttle and its usage
  checker are copied into it rather than referenced from the plugin, so a
  plugin update in the middle of a run cannot break it.
- **Nothing is registered globally.** The review directory is the only record
  of the run. Resuming means naming the directory. A central list of reviews
  would be a second copy of the state that goes stale the moment a directory
  moves.

## Doubt is built in

Reviewers, human or model, over-report. The skill doesn't try to prevent that.
It follows every Critical, High, and Medium finding with a second,
independent agent whose only job is to refute it, and who is told the finding
was written by someone else. In the runs this skill was built from, that
refuted between 6 and 9 percent of the most severe findings. It also corrected
the mechanism or blast radius of others that survived, which matters as much.

Those runs verified only Critical and High. That left a confirmed High more
trustworthy than an unverified Medium, and it left under-rating unchecked: a
real Critical filed as a Medium was never looked at again. It is most likely
where a reviewer was told to lower the stakes on a premise that turned out to
be wrong. A bulk review exists to be thorough, so the skill now verifies
Mediums too, at roughly the cost of the sweep again, and lets verifiers raise a
severity as well as lower it. Only Lows go unverified, and the summary says
so.

The same doubt is applied to the review's own results. A surprising count
earns a closer look rather than a discount. A headline claim that later falls
is corrected openly. The method-notes appendix records where the run's own
premises turned out to be wrong.

## Context is the main quality lever

What a reviewer knows about the system matters more than how hard it looks.
The platform description, the exposure model, and a per-unit focus go into
every prompt. Without an exposure model, reviewers consistently over-rate
debug paths only an operator can reach, and under-rate defects whose real
consequence is operational, such as an upgrade that fails but reports
success. The summary re-ranks by real consequence for the same reason.

## Rating the fix as well as the defect

Severity and confidence both describe the defect. Fix impact describes the
repair: how far a fix would reach, judged without designing it. A Critical
off-by-one with a one-character fix and a Critical race that needs a new
locking scheme share a severity but call for very different handling. The
reviewer is well placed to tell them apart, having just read the code and its
callers.

The scale has three levels: Trivial, Low, and Uncertain. It is defined by what
a fix touches rather than how large it feels. Uncertain covers both "clearly
large" and "can't tell", because both lead to the same next step, a planning
pass that maps the fix before anyone writes it. Doubt always rates up. The
point of a low rating is to allow lighter oversight, so rating too low is the
costly mistake.

The rating exists to split fix work into two lanes: Trivial and Low fixes can
go through with light supervision, and Uncertain ones get planned first. It
doesn't license fixing anything by itself. Low findings are never verified,
so a Trivial fix to one may repair a defect that isn't there.
Verifiers re-check fix impact on the findings they adjudicate, and themes can
override it: many trivial fixes to the same pattern can add up to one design
decision.

## Rating whether a test can prove it

Fix impact says how far a fix reaches. It doesn't say how anyone would know
the fix worked. RGR is the second question: can a test that fails because of
this defect be written first, and then pass once it is fixed? That is what
lets supervision drop. A red test checks the defect is real, which settles
the worry about fixing an unverified finding, and the green checks the fix.
Both are mechanical evidence rather than an agent's judgement. The two axes
are independent. A one-line fix to an ISR race has no test, and a parser bug
whose fix is Uncertain can be easy to test. Fix impact × RGR over CONFIRMED
findings gives the slices an automated fix pass can take, each with its own
level of supervision.

Testability depends on the harness more than on the code: whether the code
builds on the host, has a framework, and has stubs for the hardware layer. A
reviewer seeing one unit can't know that, so step 1 surveys it once and every
prompt carries it. Without that, reviewers guess, and they guess
optimistically.

Partly testable findings were the hard case: a test can show the length logic
is wrong but not produce the DMA timing that makes it fail in the field. A
fourth level was considered and dropped. The finding is rated No, because a
person still has to verify it, and the caveat names what the test proves and
what is left. Its test is still worth writing, because it narrows the
person's check to the residual. That is also why a passing test alone never
makes it Fixed, since otherwise a green test makes everyone believe the field
failure is gone. A compound finding, two defects in one, looks partly testable
but isn't. It is two findings, which is why reviewers now report one failure
mechanism per finding.

The rating is a prediction, and some will be wrong. That is safe only if the
fix pass treats "no test fails for the stated reason" as a stop that moves the
finding to a more supervised lane, never as a reason to try harder.

## Two kinds of seam, and harnesses as findings

In the first review rated with RGR, about a fifth of the findings were Seams,
and they were two different things. About half needed only a small refactor
of the code being fixed: export a function, or guard a script's top level so
it can be imported. The rest needed infrastructure: a test project that a
couple of dozen findings across many files were waiting on, or a database
integration fixture that dozens more were. A reviewer can tell those apart
from the code, so Seam now
carries its kind, `Seam (local)` or `Seam (harness)`. A local seam is nearly
Unit: its refactor is the one unprotected change, and it is bounded to
Trivial or Low reach. That is what lets a light fix pass take it.

A harness isn't the reviewer's to report, because one reviewer sees one unit
and would report the same missing test project sixteen times. Synthesis sees
everything, so it writes one `X-` finding per harness, with category
`testability`, and links each dependent with `Depends on`. A missing harness
gets a disposition like any finding: built, ticketed, or declined, and that
one decision settles its dependents. Its severity stays Low. Its weight is
what waits on it, which the summary ranks and the site shows as "Needed by".
Taking its dependents' severity would count their defects twice.

`Depends on` is general. It was added for harnesses, but the same review
showed it is needed between defects too. Before the bullet existed, about a
fifth of its findings named another finding in their text, and none was
recorded where a fix pass could act on it. Most of those mentions were "this
makes that worse" or "see also". A few dozen were real, of the form "depends
on the locking decision in U02-1" or "use the validation helper from U05-6".

So a dependency is defined narrowly, by what a fixer has to do about it.
**Order**: the other fix has to land first. **Shared fix**: the other fix
will also remove or change this defect. Amplification and context stay as
prose, because recording them as dependencies would block fixes that don't
need to wait. A shared fix points one way, from the narrower finding to the
broader one, so the graph has no cycles by construction.

Each stage records what it can see. Reviewers see their own unit, so they
record in-unit links, and they are told to decide on every finding they
mention, which is where the prose came from. Verifiers check the
dependencies of the findings they adjudicate, with the same
`(verifier: was …)` record as any other correction. Synthesis sees
everything: it sweeps the remaining mentions programmatically, and adds the
cross-unit links its themes reveal, which no reviewer could have written. A
dependency on a refuted finding is dropped, and the renderer warns about any
left.

It lists prerequisites only. The reverse is computed, so the two directions
can never disagree.

Eligibility for a fix pass follows from it. A dependent's rating stays `Seam
(harness) … Depends on: X-1`, and it becomes testable once X-1's fix is in
the branch. Nothing has to rewrite its RGR, so no rating goes stale.

## A rigid format joins the stages together

Sweep, verification, synthesis, and rendering are separate stages, often run in
separate sessions. The strict finding format is their shared interface.
Severity, confidence, category, fix impact, RGR, status and verification are
fixed-form bullets, so later stages read them with a regex instead of
re-reading documents into context. The cost of that rigidity is that a doc
which drifts from the format drops out silently. That is why the verdict
count is cross-checked against the findings that are not Low; the fix-impact,
RGR and status counts against the total; and the rendered totals against the
summary.

Corrections follow the same rule. A verifier who lowers or raises a severity
rewrites the bullet and keeps the old value as a
marker: `High (verifier: was Critical)`. The level is still the first word, so
the numbers follow the correction, while the history stays in the finding. The
first version of the skill left corrections in the Verification prose. That
meant the tiers and totals kept counting the original label, and the summary
could only warn readers that the prose outranked the numbers.

## Guarding a long unattended run

Most of the skill's smaller rules exist because an unattended run of several
days meets situations nobody is watching for:

- **Two sessions on one review.** A session paused in its watcher looks
  exactly like a crashed one. Resuming over a live run would delete docs that
  are still being written. `RUN.lock` records that a session owns the run, and
  a resume never takes it over without the user confirming the owner has
  stopped. Heartbeat times inform that decision, but don't make it: a paused
  run can go hours without one.
- **Subagents that fail.** A subagent can hit a usage limit, error out, or
  return prose. The same test that tells a crash from a finished unit on
  resume, a doc that contains `## Coverage notes`, is applied to every result
  as it arrives. Reviewers write that section last, so its presence, wherever
  it falls, marks a finished doc. A unit that fails twice stops the run rather than looping.
- **Agents editing the same file.** Verifiers edit findings docs in place, so
  jobs are cut along whole docs and never share one while running.
- **Untrusted code.** External mode exists largely for third-party code, which
  can contain text aimed at an AI reviewer. Every prompt treats the code as
  data. External-mode agents are told to use no shell and to write only their
  own doc. Text in the code that addresses reviewers is itself reported as a
  finding. This is enforced only by the prompt. A plugin-supplied agent type
  with a tool allowlist would enforce it for real, and that is the natural
  next step if it matters.
- **Links decided up front.** Whether a review can link into Azure DevOps or
  GitHub is settled at step 1, with the user choosing between fixing it and going
  without links. Re-pinning after the sweep would move every reference to
  different code, so step 7 never does it.
- **Code that can't be linked, or can't be written.** A review of a vendor
  drop has no sha, and a review of someone else's repository must not even
  write to `.git`. `reference/tree-identity.md` gives read-only commands for
  both, and a content hash stands in for the commit where there is no git.

## Humans hold the decisions that matter

The skill runs unattended for long stretches, but it stops at the points where
a wrong assumption would be expensive or hard to undo: agreeing the scope,
starting a sweep that will spend real budget, and anything written or committed
outside the review directory. It never pushes. A finished review is a list of
exploitable defects in real code, and publishing it is a decision for a person.
It also fixes nothing. A review that edits code as it goes is no longer
reviewing the code it started with.

## Where the review lives

A review is written against one exact tree, and its line numbers mean nothing
against any other. The commits are recorded at the start for that reason, and
the rendered links are pinned to them instead of to a branch. The output can
sit inside the reviewed repository, or outside it in a separate reviews
repository. The external mode exists for code the reviewer may read but not
write to, such as third-party code or someone else's repository. In that mode
nothing under the code root is touched at all.

## Reading the result

Hundreds of findings in markdown are hard to read. The rendered HTML site is
where the review actually gets read: severity, fix impact and verification
badges, filters, and every `file:line` reference linked to that line in Azure
DevOps or on GitHub at the reviewed commit. The generator ships with the skill, and each
review's `render.json` holds everything specific to that review.

The site used to be rendered only at the end. A run's findings stayed
unreadable for the hours or days it took, and link-mapping gaps and format
drift were found only once every unit had them. Now the orchestrator
re-renders after every batch. Two things make that safe. Units the backlog
doesn't mark done get a placeholder, not a page, because their docs may be
half-written. And `--quiet` keeps each render to one line in the
orchestrator's context, the budget that one-line subagent returns protect.
Rendering still changes no markdown and can run long after a review, so a
failed render never stops a run.

## Where the evidence comes from

The numbers and warnings in `reference/field-notes.md` come from two
full-scale runs on embedded firmware and one on a TypeScript web codebase
that was later rebased. Many of the skill's rules exist because something
went wrong once. For example, thousands of dead links came from a pin that was never pushed, and a batch of units was reviewed
under a wrong exposure premise. Before removing a rule, look for the incident
behind it.

## Recording what happened to each finding

A delivered review gets acted on. The first review of non-firmware code had a
fix campaign straight afterwards, and nowhere to record its results. It grew a
`**Fixed:**` bullet and a wrapper script that patched the renderer
to show it. Every review that gets acted on would rebuild that, so the format
now has a Status bullet from the start. Reviewers write `Open`, verifiers set
`Not a defect` on what they refute, and everything after delivery rewrites
that one bullet.

The scale is about dispositions, not fixes. **Planned** means handed to
another system, usually a ticket, which then owns the finding. It counts as
handled. It may never become Fixed here, because nobody should have to come
back to the review to close it. That is why the index draws every status but
Open as a hollow square: in a process that moves findings into tickets, the
solid squares are exactly what still needs a decision. A "fixed or not" view
would show tickets as outstanding work forever.

`SUMMARY.md` stays as delivered. Its tables were hand-edited into
`open (total)` once, and hand-edited tables drift from the findings. The
rendered index recounts statuses from the docs on every render, so the live
view comes free.

Two renderer changes came with this, because acting on a review breaks
assumptions that a fresh review never tests. File resolution reads the pinned
commit (`git ls-tree`) instead of the working tree, since fixes add and rename
files that a working-tree index would link to and the pin doesn't have. And
the link extensions moved from a C-only list to a broad default, plus
`render.json`'s `extensions`, since the first non-firmware review needed
`.go` and `.tsx`.

## Deliberately out of scope

The skill stops at a delivered review, apart from recording dispositions.
Triage by exposure, mapping which findings share a root cause, and turning
findings into planned fix work have all grown out of real runs, but each is a
separate piece of work with its own judgement calls. `SKILL.md` names them
only so they are not started by accident.

## Vendored: `scripts/slack-check.bat`

Copied from the cc_usage_dashboard project's `slack-check.bat` on 2026-09-24.
That file was untracked in that repo and has since gone, so this copy is now
the one that is maintained. The copy is CRLF (enforced by `.gitattributes`),
because cmd mis-parses labels and `goto` in LF batch files.

Changed here since the copy: each probe retries once, 3 seconds after a failed
attempt; each attempt waits 10 seconds instead of 5 (`CC_SLACK_TIMEOUT`); and
every failed attempt is appended to `slack-check.log` beside the script. A
long review saw the gate report "unreachable" three times while the trayapp
stayed up and a 5-second probe never caught a slow reply. Each miss cost the
review a full watcher interval. See `reference/throttle.md`. Its own path
also reaches PowerShell through the environment now, not pasted into the
command line: a path holding `'` was a PowerShell parse error, which exits 1,
the code for "a usage gate failed", so the review paused until the watcher's
6-hour cap instead of reporting a fault.

It is only a client. It needs the cc_usage_dashboard trayapp running and
listening on `127.0.0.1:27812`, and exits 2 when it isn't there. That is why
the skill tests it at startup rather than assuming it works.

When editing it, keep it CRLF, and check that the exit-code table in
`reference/throttle.md` still matches the script's header. The throttle contract depends on 0 meaning go,
and the watcher loop depends on 2 meaning unreachable.

## Bookkeeping goes through a script

`scripts/review_state.py` exists because the TypeScript run did its
bookkeeping with shell one-liners and damaged `backlog.md` twice: PowerShell,
bash and Python text mode each write a different line ending, and one
replacement split two table rows. The script changes only the lines it means
to, keeps each line's ending as it was, and refreshes the `RUN.lock`
heartbeat in the same call. Its checks (`check-unit`, `check-verify`,
`drift`, `extract`) replace the regexes each run used to write afresh, so a
format drift is caught on the first doc instead of at synthesis. Reviewers had
written `Unit:` and `Low:` for the em dash, and nothing warned.

`log` writes its line at the end of the `## Log` section, not the end of the
file. The verification queue is a second section in `backlog.md`, and once it
sat below the log, appended lines went under the queue's table instead.

## One vocabulary for the finding format

`scripts/review_format.py` holds the scales, the heading and ID regexes, and
how a field's level and its `(verifier: was …)` markers are read. The
renderer, `review_state.py`, `add_status_bullets.py` and `rebase-findings`'
`reanchor.py` all use it. Each used to keep its own copy, and they drifted:
`review_state.py` cut `(verifier: was Seam (local))` short at the first `)`,
read the newest marker as the original where a rebase had stacked two, and
rejected a typographic apostrophe in `Won’t fix` that the renderer accepted.
It is standard library only, so `review_state.py` still runs without
`markdown`.

## The review is a repository from the start

Step 2 now puts the review under git before the sweep. A rebase works on the
review's files as diffs and refuses to start on uncommitted ones, and the
first review rebased had been left untracked in someone else's repository. It
became its own nested repository, with the outer repository told to ignore it.
`.git/info/exclude` is preferred for that over `.gitignore`, which would be a
change to a repository the user doesn't own.

## Tests

`scripts/tests/test_render_resolution.py` covers the URL formats for both
hosts and the reference resolution rules, the ones that went wrong in practice:
a bare basename that has several candidates, a file too short for the
cited line, a Next.js `(group)` folder, a reference wrapped in brackets. It
sets the renderer's module state by hand, so it needs no repository. Run it
from the skill folder with `python -m unittest discover -s scripts/tests`.
`scripts/tests/test_review_format.py` covers the shared format reading, the
correction markers above all, and where `review_state.py log` puts its line.
`rebase-findings` has a matching suite that checks its resolution agrees with
the renderer's, and one for its line arithmetic.

## Why the review copies it instead of calling it in place

Installed plugins live in a cache directory named by version, and an update
replaces it. A review runs for days, across sessions. A `throttle.bat` that
points into the plugin cache stops working after the next update, so it looks
like a permanently unreachable gate. The copy in the review directory keeps
the run self-contained, the same way the rest of its state is.

The renderer (`scripts/render_review_html.py`) is the opposite case. It runs
once, inside a session that has the skill loaded, so it is called in place.
