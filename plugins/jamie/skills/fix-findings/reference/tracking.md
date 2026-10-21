# Tracking modes

Where the record of the fixes lives, and what commits say. The tasks, checks
and agents are the same in both modes.

## Commit bodies, in both modes

With `commits: one` (step 1 of the playbook), each finding ends as one
commit: the bodies below for its test, fix, seam or harness, and rounds are
merged into that commit's single body, which `$FIX squash` commits.

Every commit has a subject and a body. The subject's form depends on the
mode, below. The body tells a reader of the history what the task's record
knows, so nobody has to find the record. Write it from the record, in the
project's own style as recorded at step 1 (`commit_style` in `$FIX config`):
its wrap width, whether it uses bullets, how it lays out a verification note.
Never add a `Co-Authored-By` trailer or a "Generated with" line; `done`
refuses a commit with either, or with no body. A trailer the project itself
puts on every commit, such as a DCO `Signed-off-by` its hooks require, is
part of its style and stays. Each subagent drafts the bodies for its own work
in its record, and you commit from the draft.

- **The fix commit**: what was wrong, as the failure scenario in a sentence
  or two; what changed, with any departure from the suggested fix, new file
  or new dependency, and why; any behavior choices the fixer made; and how it
  was verified: the test that fails without the fix, on which assertion, and
  passes with it, and the related tests and check. The full suite runs later,
  so the body doesn't claim it. For a fix without a test, say plainly that no
  test proves it, and give the argument for why it holds in a few lines, from
  the record's **Why it holds**. For a partly testable one, say what the test
  covers and what it doesn't.
- **The test commit**: the behavior the test pins down, and that it fails
  until the next commit.
- **A seam or harness commit**: what it makes possible, and why behavior is
  unchanged. For a fake, what it models and what it leaves out.
- **An improvement commit**: what the round found, mistake or better way, and
  what changed.
- **A disposition commit** (committed mode): the evidence for it, and that a
  second agent confirmed it.

Keep it to what a reviewer of the change needs, not the whole record.

## Committed

The review is committed in the code repository, and each task's commits also
rewrite its finding's Status. So the run branch shows the Status that matches
its code, an abandoned branch takes its Status changes with it, and merging
it is what records the dispositions.

**Checks at step 2:**

- `git ls-files <review dir>` is not empty: the review is committed at the
  base.
- Nothing in `<review dir>` is modified or untracked, apart from paths git
  ignores, such as a rendered `html/`.

**The Status line**, written with `$FIX set-status`, in place, changing
nothing else in the doc. The level comes first, so the review's parsing is
unchanged:

| Outcome | Status | Goes in |
|---|---|---|
| fixed | `Fixed — in this commit — <what changed>; red-green: <test file>::<test name>` | the fix commit |
| fixed-partly | `Fixed — in this commit — <what changed>; red-green: <test file>::<test name> for <the testable part>; residual not checked by a test: <the residual>, reviewed by a checker and fresh-eyes rounds` | the fix commit |
| fixed-without-test | `Fixed — in this commit — <what changed>; fixed without a test, reviewed by a checker and fresh-eyes rounds` | the fix commit |
| harness | `Fixed — in this commit — <the harness>; proven: <dependent ID>'s test fails for its reason` | the last harness commit |
| gone | `Fixed — no longer present when the fix run reached it — <evidence, naming the change that removed it if known>` | a commit of its own |
| not-a-defect | `Not a defect — refuted by fix run: <evidence, with file:line>` | a commit of its own |
| present | `Fixed — already present when the fix run reached it — <where>` | a commit of its own |

Add `; defect confirmed by the red test` to a red-green fix whose finding
had no verdict or was UNCERTAIN, and `; defect not confirmed by a test` to a
fix without one in the same case. The rounds run after the fix commit, so
the Status line doesn't count them; the queue's Rounds column records how
many ran.

**Commit subjects:** `seam(<ID>): expose <the boundary> for testing`,
`test(<ID>): failing test for <title>`, `fix(<ID>): <title>`,
`improve(<ID>): <one line>`, `harness(<ID>): <what it makes possible>`, and
`docs(<ID>): <the disposition>`. Each with a body, as above. With
`commits: one`, a finding's single commit is `fix(<ID>): <title>`, or
`harness(<ID>): …` for an `X-` finding, and its body covers the test, the fix
and what the rounds changed.

The run's own record, the queue and the task records, is committed once, at
step 6.

**Run branch:** the default is `fix/findings-<review dir name>-<date>`.

## Private

The review stays out of the project. The project sees only fixes, written as
ordinary fixes. Nothing in a commit, a test name, a comment or a string points
at a review, a finding, or work not yet done.

**Checks at step 2:**

- `git ls-files <review dir>` is empty.
- If `<review dir>` is inside the code root, git must ignore it
  (`git check-ignore -q <review dir>`). If it doesn't, the gate offers to add
  it to `.git/info/exclude` (`$FIX exclude <its path>`), and adds it on the
  go-ahead.

**Nothing in the code or commits may identify the review.** Every subagent is
told so. `$FIX scan --since <start>` runs on every fix, seam, harness and
improvement before committing: it scans the added lines, the untracked files
the task added, and the messages since `<start>` for any finding ID in the
review, the review's path or folder name, and review phrases. `$FIX done`
runs the same scan once more over everything the task committed, messages
included. A match bounces the task as `leak`.

**The run branch's name** must not point at the review either: the default
is `fix/findings-<date>`, not the review's name.

**Commit subjects** follow the recorded style and describe behavior, and
each commit has a body, as above, that follows the same rule as the code:
nothing in it may point at the review.

- a seam: what it makes possible, typed as a refactor, such as
  `refactor: move validateSecurityConfiguration into its own module so it can run without starting the server`
- a test: the behavior it pins down, such as
  `test: role update rejects removing protected permissions without security.admin`
- a fix: what is corrected, such as
  `fix: role update checks the permissions it removes as well as the ones it grants`
- a harness: a refactor or test change, by what it is, such as
  `refactor: build the app in createApp() so tests can start it without listening`
- an improvement: `fix:` for a mistake, `test:` or `refactor:` for a better
  way

**Status is written in the review, after the fact.** Once a task is done
(`$FIX done`) or disposed (`$FIX dispose`), `$FIX set-status` rewrites its
finding's Status in the review, which is outside the project, so nothing in
a commit points at it. A finding is marked Fixed as soon as its fix is
committed on the run branch, before it is pushed or merged, so the review
shows what has been done and no later run takes it again. Until it lands,
its Status says so, naming the branch and the local commits, written plain
rather than in backticks: the renderer links a backticked sha and warns that
one no remote has would 404.

| Outcome | Status |
|---|---|
| fixed | `Fixed — not yet landed: <sha>[, <sha>…] on <branch> — <what changed>; red-green: <test file>::<test name>` |
| fixed-partly | as fixed, then `; residual not checked by a test: <the residual>, reviewed by a checker and fresh-eyes rounds` |
| fixed-without-test | `Fixed — not yet landed: <sha>[, …] on <branch> — <what changed>; fixed without a test, reviewed by a checker and fresh-eyes rounds` |
| harness | `Fixed — not yet landed: <sha>[, …] on <branch> — <the harness>; proven: <dependent ID>'s test fails for its reason` |
| gone, not-a-defect, present | as in committed mode, from the disposition's evidence |

The confirmation notes are the same as in committed mode. The shas are the
task's commits, from `$FIX done`. Reconcile, in the review skill's
dispositions reference, later replaces `not yet landed: …` with the landed
sha, or puts the finding back to Open if its branch is abandoned.

The queue in `<review dir>/fixes/<run>/queue.md` keeps what reconcile needs:
the outcome, the test as `<file>::<name>` or the probe as `<file>::<string>`,
and the commits. Test names and probes must be distinctive, because they are
how a fix is found after a squash merge.

The changed findings docs are committed in the review's own repository with
the run's record, at the end (step 6). The guard leaves out the run's own
Status rewrites, as it does in committed mode: `set-status` takes a fresh
snapshot after each one.
