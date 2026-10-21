# Doing one task

One task is one finding. The JSON from `$FIX next` gives its `id`, `kind`,
`rounds`, `fix_impact`, `rgr`, `verdict`, `deps`, `note`, `reason` and
`start`, and `deps` details each prerequisite: its title, kind, test, probe,
proof, and for a harness where its kept proof is. `$FIX show <ID>` prints the
same again. The finding's full text is in `<state>/briefs/<ID>.md`, and the
review's platform, harness and scales in `<state>/review-context.md`.
Subagents read those files themselves: never paste a finding into your own
context to pass it on.

**Every subagent is a fresh Opus subagent** (`model: "opus"`), returns one
line, and writes its reasoning to `<state>/records/<ID>.md`, which starts
empty for each attempt (`next` moves earlier attempts aside). Read a record
only to diagnose a failure or to fill in a commit or Status line.

**Filling the prompts.** `$FIX prompt <ID> <role>` fills a prompt in from the
run's state and writes it to `<state>/prompts/`. Roles: `fixer`, `seam`,
`notest-fixer`, `builder`, `checker`, `harness-checker`, `reviewer` and
`verifier`. It prints `spawn`: the one-line instruction to give the subagent,
which reads the file itself. Spawn with exactly that, so that no prompt is
paraphrased or trimmed from memory. Add to `spawn` only what the run knows
and the prompt can't, such as which later findings in this run touch the
same code.

- **What it fills**: the paths, the branch, the pin, the test paths and
  commit style from `$FIX config`, `<run a test>` (which runs the recorded
  command, so every run leaves its output in a log), the task's start, its
  prerequisites with a harness's probe and kept proof, the user's note, a
  re-rated task's reason, and for a checker the size and flags of the
  uncommitted work.
- **What you pass**: `--round <n>` for a reviewer, `--seam <sha>` and
  `--seam-line "<line>"` (or `--seam-double`) after a seam agent,
  `--proof-file <path>` for a harness checker, `--claim "<GONE|REFUTED|PRESENT>: <line>"`
  for a verifier, and `--set <placeholder>=<value>` for anything else. It
  refuses, naming them, when a value it needs is missing.
- **What it keeps**: a paragraph marked `[… ONLY:]` only when it applies,
  from the tracking mode, the commits setting, the task's kind, note,
  prerequisites and reason. A marker that starts a paragraph or a list item
  gates all of it; a marker inside a paragraph gates the rest of its
  sentence. An edit to a prompt keeps to that.

**Around every subagent**: note `<head>` = `git rev-parse HEAD` before you
spawn it, and after it returns run `$FIX guard --head <head>` before
anything else, per *Always, first* in [checks.md](checks.md). Record each
step as you reach it: `$FIX beat --step <step>`.

## The pipelines

| Kind | Finding | Steps |
|---|---|---|
| `fix` | `Unit`, or `Seam (harness)` with its harness done | fixer → checks → checker → commit test, commit fix → rounds → (squash) → full suite → done |
| `seam` | `Seam (local)` | seam agent → seam checks → commit seam → then as `fix` |
| `partly` | `No — partly testable` | fixer, told to test the testable part → as `fix` |
| `notest` | `No` | no-test fixer → no-test checks → checker → commit fix → rounds → (squash) → full suite → done |
| `harness` | `X-` | builder → harness checks → harness checker → commits → rounds → (squash) → full suite → done |

The prompts are in this folder: [fixer-prompt.md](fixer-prompt.md),
[seam-prompt.md](seam-prompt.md), [notest-fixer-prompt.md](notest-fixer-prompt.md),
[builder-prompt.md](builder-prompt.md), [checker-prompt.md](checker-prompt.md),
[harness-checker-prompt.md](harness-checker-prompt.md),
[reviewer-prompt.md](reviewer-prompt.md) and
[verifier-prompt.md](verifier-prompt.md). The checks are in
[checks.md](checks.md), and what commits say and carry in
[tracking.md](tracking.md).

**Commits**, everywhere below: explicit paths, never `git add -A`, and never a
merge. Every commit gets a subject and a body, as *Commit bodies* in
[tracking.md](tracking.md) says. The subagent whose work it is drafts the
body in its record's `## Commit bodies` section, so you don't have to read
the whole record: take the draft, correct anything your own checks
contradict, write the message to `<state>/records/<ID>.<n>.msg` (never into
the tree), and commit with `git commit -F <file>`. Never a trailer or a
"Generated with" line. A failing commit hook is a failed check: `env`, or the
hook's own reason if it names one.

**The Status line**: `$FIX set-status <ID> "<Status>"` rewrites the
finding's Status bullet in place, keeping the doc's line endings, and prints
the doc's path. Never edit a findings doc any other way.

- **Committed mode**: set it before the commit the table in
  [tracking.md](tracking.md) names, and add the doc's path to that commit.
- **Private mode**: set it right after `$FIX done` or `$FIX dispose`, in the
  form the private table in [tracking.md](tracking.md) gives, with the
  commits `done` printed. It goes in no commit of the project.

### fix and partly

1. **Fixer** (`--step fixer`). Returns `READY test=<file>::<name>`, `GONE`,
   `REFUTED`, or `BOUNCE <category>`.
2. **On READY**, run the checks on a fix (`--step checks`):
   `$FIX check-fix <ID> --test <file>::<name>`. It runs them in their order,
   as [checks.md](checks.md) gives them, and stops at the first failure with
   its bounce `category` and `why`. On `ok`, read its `red_lines` and confirm
   the test failed for the finding's reason, as the record's **Red** line
   says; if it failed for another, bounce `no-red`. A step it marks
   `flaky_suspect` failed once and passed on its rerun: name the test from
   that log with `$FIX flaky`. A `regression` is for you to compare with
   `<state>/baseline.md` and `<state>/flaky.md` before bouncing.
3. **Checker** (`--step checker`). `ACCEPT` or `REJECT`. `REJECT` is a
   bounce, `rejected`.
4. **On ACCEPT, commit**: with `commits: separate`, twice, the test files
   and then everything else the fix changed, so the first commit alone fails
   the test and the second passes it; with `commits: one`, once, test and fix
   together, from the fixer's `### fix` draft. In committed mode, set the
   Status first, so it goes in the fix commit.
5. **Review rounds**, below.
6. **Squash**, with `commits: one`, below. Then **Full suite**, below.
7. **Done**, below, with outcome `fixed` (`fixed-partly` for `partly`) and
   `--test`.

### seam

1. **Seam agent** (`--step seam`). Returns `SEAM`, `NONE` (the seam is a test
   double the fixer writes in its test), `GONE`, `REFUTED`, or `BOUNCE`.
2. **On SEAM**, the seam checks. If they pass, commit the seam on its own,
   and note its sha. The fixer starts from it, so the red check runs against
   the refactored, unfixed code. That catches a refactor that hid the defect.
3. **Then the `fix` pipeline**, with the fixer told about the seam commit
   (`$FIX prompt <ID> fixer --seam <sha> --seam-line "<line>"`), or on `NONE`
   about the double (`--seam-double`). The fixer's checks compare with the
   seam commit, which is `HEAD`; the improvement checks take
   `--before <seam sha>`. With `commits: one`, the seam is squashed into the
   finding's commit with the rest.

### notest

1. **No-test fixer** (`--step fixer`). Returns `READY`, `GONE`, `REFUTED`,
   or `BOUNCE`.
2. **On READY**, the no-test checks.
3. **Checker**, with its no-test paragraph.
4. **On ACCEPT, commit** the fix: any test files the fixer added first, as
   their own commit, then everything else, with the Status in committed mode.
   With `commits: one`, all of it as one commit.
5. **Review rounds**, then pick the **probe**: `<file>::<string>`, a
   distinctive string from the fix's added lines, absent from `<start>`
   (`git grep -F -c -e '<string>' <start>` finds nothing). With no test
   name, it is how the fix is found again after a squash merge.
6. **Squash**, with `commits: one`; then **Full suite**, then **Done**, with
   outcome `fixed-without-test` and `--probe`.

### harness

1. **Builder** (`--step builder`). Returns `READY probe=… proof=…`,
   `PRESENT`, or `BOUNCE`.
2. **On READY**, the harness checks, which include moving the proof out of
   the tree while the full suite runs, and checking the probe.
3. **Harness checker**. `ACCEPT` or `REJECT`.
4. **On ACCEPT, take the proof out and commit.** Copy the proof file to
   `<state>/records/<ID>.proof.<its file name>.txt`, then delete it from the
   tree: it fails until its dependent is fixed, and a failing test on the
   branch would break every later task's suite. Commit the production files
   as one commit and the test infrastructure as another, skipping either if
   empty; with `commits: one`, both as one commit, from the builder's
   `### harness` draft. In committed mode, the Status goes in the last of
   them.
5. **Review rounds**, with the reviewer's harness paragraph and the kept
   proof's path. Their improvement checks take
   `--proof <proof file>::<test name>`, so the kept proof is shown to fail
   still.
6. **Squash**, with `commits: one`; then **Full suite**, then **Done**, with
   outcome `harness`, `--probe`, and
   `--proof "<dependent ID>: <test file>::<test name>"`. The proving
   dependent's fixer is later pointed at the kept proof.

## Dispositions: GONE, REFUTED and PRESENT

The subagent found that the finding needs no fix, and its record holds the
evidence: `GONE`, the defect is no longer in the code, often because a
prerequisite's fix removed it; `REFUTED`, the finding was never right about
the code; `PRESENT`, the harness an `X-` finding asks for already exists.
What the code does is a matter of evidence, so agents may decide it. But one
agent's word is never enough, as for a verifier's REFUTED in the review:

1. `$FIX discard`, dropping anything the task changed or committed, a seam
   included.
2. **Verifier** (`--step verify`), fresh, with
   [verifier-prompt.md](verifier-prompt.md). It tries to show the claim is
   wrong, and returns `CONFIRM` or `DISPUTE`. On `DISPUTE`, bounce
   `disputed`: two agents disagree about the code, and the user decides.
3. **On CONFIRM, record it**, after `$FIX discard` once more, which throws
   away any test the verifier wrote to decide.
   - **Committed mode**: set the Status, as [tracking.md](tracking.md) gives,
     and commit the findings doc alone. Then **Done**, with outcome `gone`,
     `not-a-defect` or `present` and `--evidence "<one clause>"`. It needs no
     full suite.
   - **Private mode**: `$FIX dispose <ID> gone|not-a-defect|present
     "<evidence, one clause>"`, then set the Status in the review. No
     commit.

Either way, what waits on it is released.

**Intended behavior is different.** If the finding is right about what the
code does, but the code may do it on purpose, the subagent bounces `intent`.
Whether behavior is intended is the user's to own, so the finding stays
Open, and what waits on it is blocked. The user's answer comes back as a
note, through a resume or a later run.

## Review rounds

Up to the task's `rounds`: 1 by default, 2 for Fix impact Uncertain, 3 for a
harness and for `notest` and `partly`. A round that finds nothing ends the
series early. **If the last allowed round changed something, run one more,
once**, so that no change is kept without fresh eyes on it.

For each round (`--step round<n>`):

1. **Reviewer**, fresh, with [reviewer-prompt.md](reviewer-prompt.md). It
   looks at all of the task's commits together, with fresh eyes, for mistakes
   or a better way, and changes only what is about this finding. It returns
   `NONE` or `CHANGED mistake|better`.
2. **On CHANGED**, the checks on an improvement:
   `$FIX check-round <ID> [--test <file>::<name>] [--before <seam sha>]
   [--proof <file>::<name>]`, with `--test` for a task with a red-green test
   and `--proof` for a harness. If they pass, commit everything it changed as
   one more commit. If they fail, roll back that round only, with
   `$FIX discard --to-head`, and record `rolled back: <check>`. The task goes
   on.
3. Log the round's result: `$FIX log "<ID> round <n>: <NONE | CHANGED
   mistake: … | CHANGED better: … | rolled back: <check>>"`. The report
   counts these. Keep count of the rounds that ran.

## Squash

With `commits: one`, after the review rounds and before the full suite:
`$FIX squash <ID> --message <file>`. It folds the task's commits, a seam
included, into one with that message, and checks the tree is unchanged. The
message is the subagent's draft (`### fix`, or `### harness` for a builder),
with what the rounds changed merged in from their `### round <n>` drafts, and
corrected where your checks contradict it; write it to
`<state>/records/<ID>.final.msg`. Squash before `done`, never after: `done`
records the commits as they are, and refuses more than one in this mode.

## Full suite

After the review rounds and any squash, before *Done*: the full `suite` and the `check`
(`--step suite`), through `$FIX exec suite --log <ID>-suite` and
`$FIX exec check --log <ID>-check`, under the regression rules in
[checks.md](checks.md). Every earlier check ran only the tests related to the
change; this is the one that catches the rest. A failure bounces the task
`regression`, rounds and all. `done` refuses a fix whose tree the full suite
hasn't run on.

## Done

`$FIX done <ID> --outcome <outcome>`, with `--test`, `--probe`, `--proof`,
`--evidence` and `--rounds-run <n>` where they apply. It re-checks what the
record relies on, then records the task's commits and marks it done:

- the tree has nothing uncommitted, and the task's commits are linear on
  `<start>`
- the full suite ran on this tree (not needed for a disposition): on
  `no-full-suite`, run the *Full suite* step, then `done` again
- the probe, if any, is in `HEAD` and wasn't in `<start>`: on `bad-probe`
  (a round edited it away), pick a new one from the added lines, and `done`
  again
- every commit has a body and no attribution line: on `bad-message`, reword
  the commits it names, then `done` again. For the last commit,
  `git commit --amend -F <file>`. For an earlier one, rebuild the task's
  commits in order on `<start>`: note their shas, `git reset --keep
  <start>`, then for each `git cherry-pick <sha>`, and
  `git commit --amend -F <file>` where its message needs it. The tree ends
  exactly as before, so no check needs to run again.
- **private mode**: the leak scan of everything since `<start>`, commit
  messages included. On `leak`, discard and bounce `leak`.
- it saves `<state>/records/<ID>.final.patch`, what the task committed, for
  reconcile

In private mode, then set the Status in the review, with the commits `done`
printed. Then on to the next task.

## Bounces

On `BOUNCE <category>` from a subagent, or a failed check:

1. `$FIX discard`. Everything the task did goes, seam and improvement commits
   included, and the tree is back at `<start>`. If it can't get there, stop
   the run.
2. Record it, by category:
   - `not-unit` on a `fix` rated `Unit`: `$FIX rekind <ID> "<reason>"`. It
     comes back once as `seam`. The script bounces it instead if it was
     already retried, or wasn't `Unit`.
   - `blocked` naming an ID: `$FIX block-on <ID> <prerequisite> "<reason>"`.
     It waits for that task if it is in this run and not yet done, and
     bounces otherwise.
   - anything else: `$FIX bounce <ID> <category> "<one-line reason>"`. What
     waits on it is blocked.

These are the only ways a task comes back. Never retry a task otherwise, and
never repair a subagent's work yourself. A task that sprawls has told you
something; bounce it with what it told you.
