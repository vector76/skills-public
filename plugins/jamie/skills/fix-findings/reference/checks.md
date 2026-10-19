# The checks

Run these yourself, in the order given. The first one that fails bounces the
task with the category given, per *Bounces* in [work.md](work.md). Run every
command through `$FIX exec <which> --log <ID>-<what>`, built from the
recorded templates. **Never run a command line a subagent wrote.** Read the
summary `exec` prints; when you need more, such as the names of failing
tests, search its log for them rather than reading it whole.

`<start>` is `HEAD` when the task started, from the task JSON. For a task
with a seam commit, the checks on the fixer's work compare against the seam
commit, which is `HEAD` when the fixer starts. A test file is one matching
the run's test paths; everything else, including configuration, is
production. The findings doc is neither, and stays out of every split and
count.

**`$FIX changes`** sorts what changed since the task's start, or since
`--since <ref>`: `test` and `production` files, `new` and `deleted` ones,
`review` (anything in the review directory, which a subagent never
touches), `line_ending_churn`, the `size`, and `flags` for the checker:
dependency manifests and lockfiles, build and CI configuration, new
production files, and existing test files with changed or removed lines,
whose assertions you then read in the diff. Untracked files that were there
when the task started are left out.

## Always, first: nothing escaped

After every subagent, before any other check: `$FIX guard --head <head>`,
with the `HEAD` you noted before spawning it. It checks that:

- `HEAD` hasn't moved, and the run branch is still checked out. Subagents
  never commit or switch branches.
- nothing in the review directory changed, apart from this run's own folder
  and the rendered site.

On `escaped`, **stop the run** and report what it found. Don't discard
anything first; the user looks.

## Regressions, and flaky tests

**Tiered testing.** The full suite is slow and runs once per task, at the
*Full suite* step in [work.md](work.md), before *Done*. Every check before
that, wherever it says *the regression run*, runs the `related` command
instead, which runs only the tests that depend on the changed files:
`$FIX exec related --files <changed files> --log <ID>-<what>`.

- **The changed files** are the `test` and `production` lists from
  `$FIX changes`, against the commit the check compares with, leaving out a
  moved-aside proof. For an improvement round, the files the whole task
  touched.
- **A related run that selects no tests passes.** Code no test reaches is
  what the full suite step is for.
- **The seam and harness checks use the full suite**, not `related`. They
  change how code is reached, or test configuration and infrastructure, which
  no import graph shows.
- With no `related` command, or no changed files, `exec related` runs the
  full suite itself and says so in `fallback`.

Wherever a check runs the regression run, the suite or the check command,
every failure must be on `<state>/baseline.md`'s list of tests that already
failed, or on `<state>/flaky.md`. If any failure isn't, run the same command
once more. A test that fails again is a regression, even if it would pass
alone: a fix that breaks a test through shared state or order still breaks
it. A test that passes the second time is flaky:
`$FIX flaky <ID> "<file>::<test name>"`, and it isn't counted against this
task.

## Checks on a fix (fix, partly, and the fix in seam)

### 1. The changes are allowed

From `$FIX changes`:

- **At least one test file and one production file changed.** A fix with no
  test, or a test with no fix, isn't red-green. → `no-red`
- **No line-ending churn**: `line_ending_churn` is empty. → `env`
- **The flags go to the checker**, without bouncing. Each is legitimate only
  if the defect needs it, and the checker judges that. A new dependency is
  allowed when the fix needs it.
- **Private mode**: `$FIX scan --since <start>` finds nothing. → `leak`

**Record the size**, which is not a check, for the checker and the record.
Nothing bounces on size.

### 2. Red: the test fails without the fix

1. Save the production changes as a patch,
   `git diff --binary HEAD -- <changed production files> > <state>/records/<ID>.fix.patch`,
   and the test changes as `<ID>.test.patch`. `git diff` doesn't show
   untracked files, so new files need step 2.
2. Set the production files to their state at the start of the fix:
   `git restore --source=HEAD -- <changed production files that HEAD has>`,
   and move new production files aside into `<state>/records/<ID>.aside/`,
   keeping their paths relative to `<root>`.
3. Run `target` for the test file, with the test name the fixer reported.
4. **It must fail on an assertion, for the finding's reason**, as the
   fixer's record predicts. Search the log for the test's name: it must have
   run and failed. Zero tests run, a suite that doesn't load, a compile or
   import error, or a different test failing is not red. → `no-red`
5. Put the fix back: move the new production files back to their paths, then
   `git apply <state>/records/<ID>.fix.patch`. If it doesn't apply cleanly,
   the tree is in an unknown state: discard, and bounce `env`.

### 3. Green: the test passes with the fix

Run `target` for the test file **without** a test name, so the whole file
runs. The named test passes, and so does every other test in that file. →
`no-green`

### 4. No regression

The regression run, then the `check`, under *Regressions* above. →
`regression`

## Checks on a seam

After the seam agent returns `SEAM`, before the fixer starts:

1. **Its changes are allowed**: only production files changed. No test
   files, no dependency manifest or lockfile, no build or CI configuration.
   New production files are fine, for a function moved into its own module. →
   `not-local`. Line-ending churn → `env`. Private mode, the scan → `leak`.
2. **The full suite and check are unchanged**, under *Regressions*. A seam that
   breaks a test changes behavior. → `regression`

Then commit it alone. The checker judges whether it preserves behavior
beyond what the tests cover, when it checks the fix.

## Checks on a fix without a test (notest)

1. **The changes are allowed**: at least one production file changed. Test
   files may change too: a test that passes before and after is a regression
   guard, not a proof. Line-ending churn → `env`. The flags go to the
   checker. Private mode, the scan → `leak`. Record the size.
2. **No regression**, under *Regressions*. → `regression`

There is no red or green check. That is why this kind gets the checker and
three review rounds, and why its record says it was fixed without a test.

## Checks on a harness

After the builder returns `READY`:

1. **The changes are allowed**:
   - **The proof is where the record says**: one new, untracked test file,
     holding the proving test and nothing the harness needs. → `no-proof`
   - New files are allowed, production and test alike. A dependency, or a
     test-runner or build configuration change, is allowed when the harness
     needs it; the flags go to the checker.
   - No line-ending churn. → `env`
   - Private mode, the scan. → `leak`
   - Record the size, the proof left out.
2. **Behavior is unchanged**: move the proof file into `<state>/records/`
   for now, with `.txt` appended. Run the full suite and the check, under
   *Regressions*. → `regression`. Then put the proof back.
3. **The proof fails for the dependent's reason**: `target` for the proof
   file and test name. It must run and fail on an assertion matching the
   failure the proving dependent's Description states. → `no-proof`
4. **The probe**: the record's `<file>::<string>`, a distinctive string the
   harness adds, must be absent from `<start>`:
   `git grep -F -c -e '<string>' <start>` finds nothing. If not, pick
   one yourself from the added lines. Never bounces.

## Checks on an improvement

After a reviewer returns `CHANGED`, with `HEAD` at the task's last commit.
The first failure rolls back **that round only**, with
`$FIX discard --to-head`, and the task goes on as it was.

1. **Its changes are allowed**, from `$FIX changes --since HEAD`: no
   line-ending churn; in private mode, `$FIX scan --since <start>` finds
   nothing; no assertion that existed before the task's test commit changed.
   An assertion the task added may be tightened, not removed.
2. **Red still holds**, for a task with a red-green test: save the
   improvement's production changes as a patch
   (`git diff --binary HEAD -- <production files>`; its test changes stay in
   the tree), set the production files to their state before the fix
   (`git restore --source=<the test commit> -- <every production file the
   fix or the improvement touched>`, moving aside any the test commit
   doesn't have), run `target` with the test name, and see the test fail
   for the finding's reason. Then `git restore --source=HEAD -- <the same
   files>`, put back what you moved aside, and re-apply the patch. If either
   step fails to restore the tree, roll the round back.
3. **For a harness, the proof still fails for its reason**: copy the kept
   proof back to its path, run `target` on it, see it fail as before, and
   delete it again.
4. **Green and no regression**: `target` on the test file without a name,
   then the regression run and check.

## Bounce categories

| Category | Meaning | What happens, and the next step |
|---|---|---|
| `intent` | The finding is right about the behavior, but it may be intended | Stays Open; what waits on it is blocked. The user decides, and the answer comes back as a note |
| `not-unit` | Reaching the code needs a seam the rating didn't name | A `Unit` finding comes back once as `seam`; otherwise bounced. Re-rate it |
| `not-local` | The seam needs more than a local refactor: shared test infrastructure, or an environment | Bounced. The review needs an `X-` finding for that harness |
| `blocked` | Another change must land first, which the review didn't record | Waits on it if it is a task in this run; otherwise bounced. The user may add `Depends on` |
| `no-red` | No test could be made to fail for the finding's reason | Bounced. Often a `premise` or `not-unit` in disguise; read the record |
| `no-green` | The fix didn't make the test pass | Bounced |
| `regression` | The fix broke another test, or a seam or harness changed behavior | Bounced. Callers rely on the old behavior |
| `rejected` | The checker found a reason not to accept it | Bounced, with the checker's reason |
| `no-proof` | No test of the proving dependent could be made to fail with the harness | Bounced. The harness may not be what the dependents need |
| `out-of-reach` | The harness needs an environment or service this machine can't run | Bounced. CI, or the user |
| `leak` | Private mode: the code or a message pointed at the review | Bounced. Read the record |
| `disputed` | A verifier disputed a GONE, REFUTED or PRESENT claim | Bounced. Two agents disagree about the code; the user decides |
| `env` | The harness, a command, a hook, line endings or the machine, not the finding | Bounced. Three in a row stop the run |

`GONE`, `REFUTED` and `PRESENT` aren't bounces. Once a verifier confirms
them, they are dispositions, per [work.md](work.md), and release what waits
on them.
