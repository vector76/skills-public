---
name: fix-findings
description: Use to fix one or more findings from a systematic-code-review, one at a time, in this session, unattended after a single go-ahead. Takes any finding, by ID, by filter, or all of them, of any severity, Fix impact or RGR: cross-cutting X- harnesses, Seam (local) refactors, red-green fixes, partly testable ones, and fixes no test can prove. Each fix is checked by the session itself, a fresh checker, and fresh-eyes review rounds, then committed on a local run branch. Prerequisites go first, and the usage throttle runs before every task. Every question is asked before the gate, so the run never stops partway to ask. Never pushes or merges.
disable-model-invocation: true
---

# Fix findings

You are running the **fix-findings playbook**. It fixes findings from a
review written by the `systematic-code-review` skill, **one at a time, in this
session**, on a local run branch checked out in the repository's own folder.
It takes every kind of work a review asks for:

| Kind | Finding | Pipeline, in [reference/work.md](reference/work.md) |
|---|---|---|
| `harness` | an `X-` finding | builder, harness checks, harness checker, commits, review rounds |
| `seam` | `Seam (local)` | seam agent, seam checks, seam commit, then as `fix` |
| `fix` | `Unit`, or `Seam (harness)` once its harness is done | fixer, red-green checks, checker, test and fix commits, review rounds |
| `partly` | `No — partly testable` | as `fix`, with the test covering the testable part |
| `notest` | `No` | no-test fixer, no-test checks, checker, fix commit, review rounds |

It has eight steps, 0 to 7, run in order, and **one** gate
(**[ STOP / ASK ]**), the go-ahead at step 3.

**Rules that define this playbook:**

1. **Nothing asks the user anything after the gate.** Every question is
   asked before it, and the gate comes before anything slow. After the
   go-ahead, every condition either lets the run go on or stops it with a
   report. Agents use their best judgement and record it. Never add a
   question after the gate. If something needs a decision, the task bounces
   with what needs deciding, and the run goes on to the next one.
2. **A fix is accepted on evidence, never on an agent's word.** You run the
   tests yourself, from the recorded commands. Fresh agents check and review
   the work. A claim that a finding needs no fix is confirmed by a second
   agent before it is recorded. A subagent's report is a claim to check.
3. **Anything that fails a check is discarded and bounced, never rescued.**
   The task's commits and changes are thrown away, and the task is marked
   with why. There are exactly two ways a task comes back, in *Bounces* in
   [reference/work.md](reference/work.md): one retry of a `Unit` finding as
   `Seam (local)`, and waiting on a prerequisite the fixer discovered.
4. **All state goes through `fix_run.py`.** The task table, the queue, the
   log and the lock change only through the script, never by editing its
   files. All state is on disk, so a fresh session continues from it alone.
5. **History is linear and local.** Commit only to the run branch, with
   explicit paths, never `git add -A`, never a merge. Never run `git push`,
   `git fetch` or `git pull`, in any form, and never touch the user's other
   branches.
6. **Only the throttle paces the run.** Before each task, run the run's
   `throttle.bat`. Never read plan usage or quota any other way.

## Prerequisites

Confirm before anything else, and stop if one is missing:

- **The Agent tool**, with subagents runnable on **Opus**. Every spawn sets
  `model: "opus"`.
- **Windows, Python 3 and git** on the path: the throttle is a batch file.
- **Started in the repository's own folder**: the code root, not a worktree
  and not a subfolder. `git rev-parse --show-toplevel` is the current
  directory, and `git rev-parse --path-format=absolute --git-common-dir` is
  its `.git` folder. The script refuses anything else.
- **A review directory** written by `systematic-code-review`, whose findings
  carry **RGR** and **Status** bullets, and whose code root is this
  repository. A review without RGR has nothing for this playbook: say so and
  stop.
- **A test harness that runs on this machine**, with a command that runs one
  test file. The review's `## Test harness` section says what exists.

## The files

```
<root>/.fix-findings/        ignored through .git/info/exclude, never committed
  state/
    run.json           the run's parameters
    tasks.json         the task table
    log.md             one line per event
    queue.md           committed mode: the queue, copied into the review at the end
    review-context.md  the review's platform, harness and scales, verbatim
    baseline.md        the baseline result, and tests that already failed
    flaky.md           tests found flaky during the run
    briefs/<ID>.md     each finding in full, for its subagents
    index.md           every finding in the review, for finding a blocker
    records/<ID>.md    each task's record, written by its subagents, with its patches
    records/attempts/  earlier attempts' records, moved aside when a task restarts
    logs/              command output
    bin/               the run's own copies of fix_run.py, review_format.py,
                       throttle.bat and slack-check.bat
    PAUSE.flag         present while the run is paused by hand
<review dir>/fixes/<run>/
  RUN.lock             present while a session is running the run
  queue.md             private mode: the queue, kept up to date as tasks finish
```

`<root>` is the code root, `<state>` is `<root>/.fix-findings/state`, and
`<run dir>` is `<review dir>/fixes/<run>/`. **`$FIX`** means
`python "<state>/bin/fix_run.py"`. Before the run exists, use the skill's own
copy, `python "<this skill's directory>/scripts/fix_run.py"`. Every command
prints one JSON object.

**Test output fills a session's context.** Run every test and build command
through `$FIX exec`, which writes the full output to a log under
`<state>/logs/` and prints the end of it. When you need more, search the log;
never read one whole. **Every subagent returns one line.** Read a record only
to diagnose a failure or to fill in a commit or Status line.

## Where everything is

- One task, by kind: [reference/work.md](reference/work.md). Its checks:
  [reference/checks.md](reference/checks.md). Commits and Status:
  [reference/tracking.md](reference/tracking.md). Continuing a run:
  [reference/resume.md](reference/resume.md).
- The subagent prompts, which [reference/work.md](reference/work.md) says
  when to use: [reference/fixer-prompt.md](reference/fixer-prompt.md),
  [reference/seam-prompt.md](reference/seam-prompt.md),
  [reference/notest-fixer-prompt.md](reference/notest-fixer-prompt.md),
  [reference/builder-prompt.md](reference/builder-prompt.md),
  [reference/checker-prompt.md](reference/checker-prompt.md),
  [reference/harness-checker-prompt.md](reference/harness-checker-prompt.md),
  [reference/reviewer-prompt.md](reference/reviewer-prompt.md) and
  [reference/verifier-prompt.md](reference/verifier-prompt.md).
- The state script: [scripts/fix_run.py](scripts/fix_run.py). Run it with
  `-h` for its commands.

## 0. A new run, or continuing one?

Look for `<root>/.fix-findings/state/run.json`. If it exists and isn't
finished (`$FIX status` shows `finished: false`), the user is continuing a
run, whatever they asked: follow [reference/resume.md](reference/resume.md)
instead of the rest of this playbook. Otherwise it is a new run.

## 1. Gather the parameters

Work out everything you can from the review and the repository, and **ask for
the rest in one message**. Don't ask what you can infer: show it at the gate,
where the user can correct it.

- **Review directory** — `<review dir>`. Read its `README.md`: the code root,
  the reviewed tree (the pin), the test harness, the run parameters. Must be
  given; never search for it. Its code root must be this folder.
- **Which findings** — must be given. One of:
  - **IDs**, in the prompt or in a document the user points to. Their order
    is the run's order, with each prerequisite moved ahead of what waits on
    it.
  - **A filter** over the whole review: "only High and Critical", "except
    RGR No", "except units 11 and 12". Translate it into `--include` and
    `--exclude` filters, each `key=value[,value…]`, repeatable. Keys: `id`,
    `unit` (`U11`), `rgr` (`unit`, `seam`, `seam-local`, `seam-harness`,
    `no`, `partly`; `no` covers partly testable too), `kind` (`harness`,
    `seam`, `fix`, `partly`, `notest`), `severity`, `fix` (`trivial`, `low`,
    `uncertain`), `category`, `verdict` (`confirmed`, `uncertain`, `none`).
  - **Everything**: every Open finding the review has.

  Never add findings yourself, except prerequisites the user agrees to at the
  gate.
- **Notes** (optional) — any decision the user gives about a finding up
  front, such as "for U3-2, an empty list returns 400", or an answer to an
  earlier run's `intent` bounce. Each goes verbatim to that finding's
  subagents, as `--note "<ID>=<text>"`.
- **Tracking mode** — **committed** or **private**, per
  [reference/tracking.md](reference/tracking.md). Infer it: a review git
  tracks in the code root means committed; one outside it, or untracked or
  ignored in it, means private. Ask only if the user's request contradicts
  that.
- **Base** — default the current `HEAD`. **Pin** — the review's pinned
  commit, from its `README.md`.
- **Run name** — default `findings-<date>`, unique among the review's
  `fixes/` runs. **Run branch** — default `fix/findings-<review dir
  name>-<date>` in committed mode, and `fix/findings-<date>` in private mode,
  where a branch the user may push must not name the review.
- **Commands**, from the review's harness, the build files and the CI
  configuration:
  - **Target** — runs one test file, with `{file}` and, where the runner
    supports it, `{name}`. For Jest: `npx jest {file} -t "{name}"`. The script
    escapes the name as a regex, since most runners' name filters are one;
    for a runner whose filter is literal, set `name_is_regex` to `false`.
  - **Related** (optional, recommended) — runs only the tests that depend on
    a set of changed files, with `{files}`, and **passes when it selects no
    tests**. For Jest:
    `npx jest --findRelatedTests {files} --passWithNoTests`. With none, every
    check runs the full suite.
  - **Suite** — the full unit suite, as CI runs it. `{workers}`, if used, is
    the machine's core count.
  - **Check** (optional) — the type check, build or lint CI gates on.
  - **Install** (optional) — the dependency install CI runs, used at setup
    only if dependencies are missing.
- **Test paths** — the globs that make a file a test file, such as
  `tests/**` and `**/*.test.ts`. Everything else is production.
- **Usage gate** — what the throttle asks: the **slack checker** (the review
  skill's `slack-check.bat`, the default), **another usage-check script**
  (its path; exit 0 means go), or **none** (the run pauses only on
  `PAUSE.flag`). Default to what the review's run parameters chose.
- **Commit style** — read the base's recent history, subjects and bodies
  (`git log --no-merges -20 --format='%s%n%n%b%n---'`), and describe the
  convention: the subject form (private mode uses it; committed mode uses its
  own), and how bodies are written: wrap width, bullets, how verification is
  described.

## 2. Check, quickly

Only checks that take seconds. Nothing slow runs before the gate.

- **The prerequisites** above.
- **The repository**: no tracked file is changed (untracked files are fine);
  the pin is an ancestor of the base (`git merge-base --is-ancestor`); the
  run branch doesn't exist.
- **The tracking mode's own checks**, in
  [reference/tracking.md](reference/tracking.md).
- **The task table, as a dry run**:

  ```
  $FIX init --review "<review dir>" --run <run> --branch <run branch> --mode <mode> --pin <pin> --gate <slack|none|path> [--ids "<IDs>"] [--include …] [--exclude …] [--with-prereqs] [--note "<ID>=<text>" …] --dry-run
  ```

  It applies eligibility (Open, not REFUTED, rated, every prerequisite fixed
  in the base or in this run, no cycle) and the scope, and prints the tasks
  by kind, the run's order, what is left out and why, the choke points, the
  harnesses, and the findings that will be fixed without a test.
  `open_prerequisites_not_in_run` lists Open findings that chosen ones wait
  on: those chosen ones are left out unless the prerequisites come too, with
  `--with-prereqs`. That is a question for the gate.
- **The usage gate**: run it once, without `-q`, and note the result. For the
  slack checker that is `<this skill's directory>/../systematic-code-review/scripts/slack-check.bat`.
  Exit `0` or `1` is a working answer. Exit `2` means the usage trayapp isn't
  reachable, and the run must not start on it: it would pause at once and
  give up. That is something to fix first.

Anything that fails here goes in the gate message as something to fix before
starting, not as a question.

## 3. Get the go-ahead   [ STOP / ASK ]

The only gate in the run. Show the user, in one message:

- **The scope**: the tasks in the run's order, by kind, with the choke
  points; what is left out, grouped by reason, with the IDs; and, if the dry
  run found any, the Open prerequisites to add (**ask** whether to add them).
- the tracking mode, the base, the run branch, the run name
- the commands, the test paths, the usage gate, and the commit style
- anything from step 2 that must be fixed first
- **What the agents will decide on their own**, as reminders:
  - **Harnesses are built and committed without a person reviewing them**
    first: a harness checker and three fresh-eyes rounds take that place, and
    the findings that wait on one are fixed on top of it in the same run.
  - Findings rated `RGR: No` are fixed and recorded as **fixed without a
    test** (list them). Partly testable ones get a red test for the testable
    part, and their residual is recorded as not checked by a test.
  - A finding whose defect isn't in the code is recorded **Not a defect —
    refuted by fix run**, with the evidence. One whose defect has already
    gone is recorded as such. Each needs a second agent to agree, and both
    release what waits on them.
  - A finding whose behavior may be **intended** is left Open for the user,
    and what waits on it is skipped.
  - Behavior choices a fix needs are made by the fixer and recorded, and the
    report lists them.
  - Review rounds per task: 1, 2 for Fix impact Uncertain, 3 for harnesses
    and for fixes without a full test.
- **How the run goes after the go-ahead**: setup and a baseline suite run,
  then every task, one at a time, with no further questions. Say what stops
  the run (*Stopping* in step 5).
  - **The working tree belongs to the run until it finishes.** The run
    branch is checked out in this folder; the user shouldn't edit files here
    or switch branches meanwhile.
  - **This session must not stop on permission prompts**: nobody is
    watching it. It should run in auto mode, or with an allowlist that covers
    git, Python, the test commands, and file edits in this folder.
  - Pausing by hand: create `<state>/PAUSE.flag`; delete it to go on.

**Halt here, unless the user's request already said to start without asking**
(for example, "go" or "don't wait for me"). In that case show the same message
and go straight on, provided step 2 found nothing to fix first and asked
nothing. A request that says to start never covers a problem step 2 found.

## 4. Set the run up, unattended

Tell the user, in one line, that setup has started and the baseline suite
takes a while. From here on, if a step fails, stop the run with a report:
`$FIX stop "<what failed>"`. Never ask.

1. **Create the run**: the same `init` command as the dry run, without
   `--dry-run`, with `--with-prereqs` if the user agreed. It adds
   `/.fix-findings/` to `.git/info/exclude`, and writes `<state>`, the
   briefs, the index, the queue, and the run's own copies of its scripts.
2. **Take the lock**: `$FIX lock --take`. Append `Go-ahead given <date>` to
   the log: `$FIX log "Go-ahead given <date>"`.
3. **Record the parameters**: `$FIX config --set commands.target="…"`, and the
   same for `commands.related`, `commands.suite`, `commands.check`,
   `commands.install`, `test_globs` (a JSON list), `commit_style`, and
   `name_is_regex` if it isn't true.
4. **Write `<state>/review-context.md`**: four sections copied verbatim from
   the review's `README.md`: the platform description its prompts use, *Test
   harness*, *Fix impact scale* and *RGR scale*. Every subagent reads it.
5. **Create the run branch** at the base and check it out:
   `git switch -c <run branch> <base>`.
6. **Install dependencies**, only if they are missing: `$FIX exec install`.
   Afterwards no tracked file may have changed. An install that changes a
   lockfile stops the run.
7. **Run the baseline**: `$FIX exec suite --log baseline-suite`, then
   `$FIX exec check --log baseline-check`. Write `<state>/baseline.md`: the
   commands' results, and every test that already fails, by name. A later
   failure counts as a regression only if it isn't on that list.
8. **Try the commands**: `$FIX exec target --file <an existing test file near
   the first task>` and see it run. If there is a `related` command, run it
   twice: with a source file some test imports, and with one no test
   imports. Both must exit 0: a related run that fails when it selects
   nothing would bounce every task.
9. **Artifacts**: `git status --porcelain --untracked-files=all`. Any
   untracked file the commands created (test reports, coverage, caches) will
   be created by every task's tests too. Add a pattern for each with
   `$FIX exclude <pattern> …`, so no task mistakes one for part of a fix.
10. `$FIX baseline`. It records that setup is done, and `next` refuses to
    start a task before it.

## 5. Fix, one task at a time, unattended

Repeat until a step ends the run:

1. **Throttle.** Run `<state>\bin\throttle.bat -q` and read only its exit
   code (from PowerShell, `& "<path>" -q; $LASTEXITCODE`; from Bash,
   `cmd //c "<path> -q"; echo $?`).
   - `0`: go on.
   - `1` (paused by usage pacing or `PAUSE.flag`): `$FIX log "paused by the
     throttle"`, run the watcher below **in the background**, and continue
     when it exits `0`.
   - `2` (the usage service is unreachable) or `3` (a wiring bug in the
     throttle): stop the run.
2. **Next**: `$FIX next`.
   - `picked`: the task, as JSON, with `deps` (what its subagents need about
     its prerequisites), `brief` and `record`. Do it per
     [reference/work.md](reference/work.md), **read again for every task**:
     after a long run your memory of it is not the playbook.
   - `done`: nothing is left. Go to step 6.
   - `stalled`: tasks are pending but none can be picked. That is a bug, not
     a state to wait in: stop the run, with the IDs.
   - `in-progress`: a task was left unfinished, after a context compaction or
     a crash. `$FIX discard --restart`, then `$FIX next` again: an unfinished
     task is redone from its start, never resumed from the middle.
3. **The task ends** as done (`$FIX done`), as a private-mode disposition
   (`$FIX dispose`), or as a bounce. After a bounce, `$FIX bounce` prints
   `env_streak`. At 3, three `env` bounces in a row, the harness, the
   commands or the machine is the problem, not the findings: stop the run.

**If you lose your place in a task**, because the context was compacted or
you can't tell which step comes next, run `$FIX status`, then
`$FIX discard --restart` and `$FIX next`. Redoing a task costs a little;
guessing where it stood can land a fix nobody checked.

### The throttle watcher

Run it in the background while paused, with the path filled in:

```powershell
$unreach = 0
while ($true) {
  & "<state>\bin\throttle.bat" -q *> $null
  $code = $LASTEXITCODE
  $ts = (Get-Date).ToString('HH:mm:ss')
  if ($code -eq 0) { Write-Output "$ts RELEASED"; exit 0 }
  if ($code -eq 3) { Write-Output "$ts throttle wiring bug (exit 3)"; exit 3 }
  if ($code -eq 2) { $unreach++ } else { $unreach = 0 }
  if ($unreach -ge 10) { Write-Output "$ts usage service unreachable for 30+ min"; exit 2 }
  python "<state>\bin\fix_run.py" --root "<root>" beat *> $null
  Write-Output "$ts paused (exit $code), rechecking in 3 min"
  Start-Sleep -Seconds 180
}
```

It exits `0` when released: go on. On `2` or `3`, stop the run. A pause waits
as long as it takes: usage windows roll over, and a `PAUSE.flag` is the
user's to lift. Waiting is not stopping.

### Stopping

**Stop the run**, with `$FIX stop "<reason>"`, then report, when any of these
happens. These are the only ways the run ends early:

- a check in *Always, first* in [reference/checks.md](reference/checks.md)
  finds that something escaped: `HEAD` moved or the branch changed while a
  subagent ran, or a subagent wrote in the review directory. Don't discard
  anything first; the user looks
- `discard` can't put the tree back at the task's start
- setup or the baseline fails, or the suite fails before any change in a way
  the baseline didn't
- three `env` bounces in a row
- the throttle reports `2` or `3`, or the watcher gives up
- `next` reports `stalled`

A bounce about the finding itself, whatever its category, is ordinary output,
never a reason to stop, and so is a task blocked by a bounce. `stop` leaves a
task in progress where it is; a resume redoes it.

## 6. Finish

1. **The full suite on the result**, only if `$FIX status` shows
   `suite_on_tip: false`, as it does when the last commit was a
   disposition: `$FIX exec suite`, under the regression rules in
   [reference/checks.md](reference/checks.md). Each fix already passed the
   full suite on a tree with every earlier fix in it. A failure that survives
   its rerun goes in the report, and nothing is undone.
2. `$FIX finish`. It records the end, copies the queue, the records, the
   final patches, the kept harness proofs and the log into `<run dir>`, and
   releases the lock.
3. **Keep the record**:
   - **Committed**: commit `<run dir>` on the run branch, leaving out
     `RUN.lock`, as `docs(<run>): fix-findings run record`, with a body
     giving the run's counts by outcome and no trailer.
   - **Private**: if the review directory is in a git repository of its own
     (`git -C <review dir> rev-parse --show-toplevel` is not `<root>`),
     commit `fixes/<run>/` there, locally, as `fix-findings run <run>`. Never
     commit it into the project.
4. If the review has a rendered site (`<review dir>/html/`), re-render it, as
   the review skill's rendering reference says, so it shows the new
   statuses.

## 7. Report

- **Done**, by outcome, with their commits: fixed red-green, fixed with a
  residual not checked by a test, fixed without a test (each listed), and
  harnesses. Say which findings the red test confirmed where the review
  hadn't (no verdict, or UNCERTAIN).
- **Dispositions the agents made**, each confirmed by a second agent: Not a
  defect, gone and present, each with its evidence in a clause. The user may
  disagree with any of them.
- **Left for the user**: `intent` and `disputed` bounces, each with what is
  in question and what it blocks. Answers come back through a later run, as
  [reference/resume.md](reference/resume.md) says.
- **Other bounces**, by category, with the next step from the table in
  [reference/checks.md](reference/checks.md), and what each blocks.
- **Decisions** fixers made, from the records' Decisions lines.
- **The review rounds**, from the log's round lines: how many found nothing,
  how many changed something, how many were rolled back, and each mistake
  they caught, named: each is something the checker accepted and shouldn't
  have.
- **Flaky tests**, from `<state>/flaky.md`.
- **The run branch**: its commit count, and that it is local and unpushed.
  Pushing, PRs and merging are the user's.
- **What comes next**, by mode:
  - **Committed**: the Status changes travel with the fixes. Merging the
    branch is what records them.
  - **Private**: the findings are still Open in the review. Once fixes have
    merged, record them with reconcile, in the review skill's
    [dispositions reference](../systematic-code-review/reference/dispositions.md).
- **What the run taught about the skill**: anything in the playbook that got
  in the way or needed a workaround, with a suggested change.

Nothing outside `<state>`, `<run dir>` and the run branch is yours to write,
apart from the Status changes committed mode makes.
