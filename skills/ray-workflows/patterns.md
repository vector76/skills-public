# Patterns

Reusable recipes assembled from the primitives in [transitions.md](transitions.md). Each pattern names the problem it solves, gives the YAML shape, and links to a runnable example.

> Each prompt below either uses an implicit transition (noted in a comment) or explicitly instructs the model on which tag to emit. The LLM does not see `allowed_transitions` — see [transitions.md §What the LLM sees](transitions.md#what-the-llm-sees).

## Plan-then-implement (clean handoff)

**Problem**: A planning step produces a plan; the implementer should read the plan fresh without inheriting the planner's chat noise.

**Shape**: `PLAN` writes to disk, then `<reset>` into `IMPLEMENT`.

```yaml
PLAN:
  prompt: |
    Write a step-by-step plan to plan.md.
  # Implicit transition — single non-ask, non-bare-result entry; no tag needed.
  allowed_transitions:
    - { tag: reset, target: IMPLEMENT }

IMPLEMENT:
  prompt: |
    Read plan.md and implement each step in order.
  # Implicit transition.
  allowed_transitions:
    - { tag: goto, target: VERIFY }
```

## Subroutine (delegate noisy work)

**Problem**: A subtask iterates / makes mistakes / accumulates context. The caller only wants the conclusion.

**Shape**: `<call>` into a child state, return via `<result>`.

```yaml
DRIVER:
  prompt: |
    We need a research summary for {{input}}.
  # Implicit transition — single call; the orchestrator dispatches into RESEARCH.
  allowed_transitions:
    - tag: call
      target: RESEARCH
      return: USE_FINDINGS

RESEARCH:
  prompt: |
    Research {{input}} thoroughly. When done, end your reply with
    <result>... summary ...</result> (one line or short paragraph).
  allowed_transitions:
    - { tag: result }

USE_FINDINGS:
  prompt: |
    Research summary: {{input}}
    Use it to ...
```

The DRIVER's session never sees RESEARCH's exploratory chatter — only the `<result>` payload via `{{input}}` in USE_FINDINGS.

## LLM-judge (stateless evaluator)

**Problem**: Score / grade / classify, without contamination from the caller's history.

**Shape**: `<function>` into an evaluator whose only inputs are its prompt and `input="..."`.

```yaml
DRIVER:
  prompt: |
    Test output: {{input}}
  # Implicit transition — single function call; the orchestrator dispatches.
  # The "input" attribute is template-rendered (implicit-transition perk),
  # forwarding the current {{input}} into JUDGE.
  allowed_transitions:
    - tag: function
      target: JUDGE
      return: HANDLE_VERDICT
      input: "{{input}}"

JUDGE:
  prompt: |
    Output to judge:
    {{input}}

    End your reply with one of:
      <result>PASS</result>                                  (output is correct)
      <result>FAIL: ...one-line reason...</result>           (output is wrong)
  allowed_transitions:
    - { tag: result }
  model: haiku
```

## Retry loop

**Problem**: Try, check, loop until success or exhausted.

**Shape**: A LOOP state with `<goto>` back to itself on continue, `<goto>` to success/failure on terminate. Count attempts by passing them through `input="..."`.

```yaml
1_START:
  prompt: |
    Begin the retry sequence.
  # Implicit transition — single goto with templated input.
  allowed_transitions:
    - { tag: goto, target: TRY, input: "1" }

TRY:
  prompt: |
    Attempt {{input}} of 3. Try the operation; check the result.

    End your reply with exactly one of:
      <goto>DONE</goto>                  — success
      <goto input="N+1">TRY</goto>       — failed; retry with next attempt number
      <goto>FAIL</goto>                  — exhausted
  allowed_transitions:
    - { tag: goto, target: TRY }
    - { tag: goto, target: DONE }
    - { tag: goto, target: FAIL }
```

DONE and FAIL are elided here; each is a single-`<result>` state whose prompt must explicitly instruct the model to emit `<result>...</result>` — see `examples/retry-loop.yaml` for the full version.

For deterministic counters or sleeps, use a `sh` state between LLM attempts so you don't burn tokens on arithmetic.

## Fan-out, fan-in

**Problem**: Run N independent workers, then collect their outputs.

**Shape**: One state emits multiple `<fork>` tags (plus optionally a `<goto>` for the parent's continuation); workers write results to a shared location; the parent polls/awaits and aggregates.

```yaml
DISPATCH:
  prompt: |
    For each item `T` in {{input}}, emit one fork (substitute the literal
    item name for T):

      <fork next="JOIN" item="T">WORKER</fork>

    After all the forks, end your reply with a single <goto>JOIN</goto>
    for your own continuation. Workers will write results under
    {{task_folder}}/results/.
  allowed_transitions:
    - tag: fork
      target: WORKER
      next: JOIN
    - { tag: goto, target: JOIN }

WORKER:
  prompt: |
    Process item {{item}}. Write results to {{task_folder}}/results/{{item}}.json.

    End your reply with: <result>{{item}} done</result>
  allowed_transitions:
    - { tag: result }     # worker terminates after popping its empty stack

JOIN:
  prompt: |
    Wait until all expected result files exist in {{task_folder}}/results/,
    then read them.
  # Implicit transition — single goto to REPORT.
  allowed_transitions:
    - { tag: goto, target: REPORT }
```

There is no built-in barrier. Either (a) script-poll the filesystem from a `sh` JOIN state, or (b) have workers signal completion via `<ask>`-style coordination via daemon mode. Filesystem polling is the simpler default. See `examples/fan-out-fan-in.yaml`.

## Human-review gate

**Problem**: Pause for a human decision before continuing; escalate on timeout.

**Shape**: `<ask>` with `timeout` and `timeout_next`.

```yaml
PROPOSE:
  prompt: |
    Prepare your proposal. Do NOT make a recommendation yourself —
    end your reply by asking the reviewer to decide, like this:

      <ask next="HANDLE_DECISION" timeout="48h" timeout_next="ESCALATE">
      ...your analysis...
      Respond with "approve", "reject", or "more info: ..." with the
      additional analysis you need.
      </ask>

    Text inside <ask>...</ask> is shown to the human; text before it
    is internal reasoning visible only in logs.
  allowed_transitions:
    - tag: ask
      next: HANDLE_DECISION
      timeout: "48h"
      timeout_next: ESCALATE

HANDLE_DECISION:
  prompt: |
    The reviewer said: {{input}}

    Carry out the decision. End your reply with: <result>done</result>
  allowed_transitions:
    - { tag: result }

ESCALATE:
  prompt: |
    Decision timed out. Send an escalation notice.

    End your reply with: <result>escalated</result>
  allowed_transitions:
    - { tag: result }
```

Run with `raymond serve` for HTTP/MCP-delivered responses, or `raymond --on-ask=pause workflow.yaml` and resume with `raymond --resume <id> --input "approve"`. See `examples/human-review-gate.yaml`.

## File upload then process

**Problem**: Ask the user for files, then have a state operate on them.

**Shape**: `<ask>` in slot or bucket mode; the post-ask state reads `{{task_folder}}/asks/{{ask_id}}/`.

```yaml
COLLECT:
  prompt: |
    Request résumé and cover-letter uploads from the user. End your
    reply with this exact form (slot names must match those in
    allowed_transitions):

      <ask next="REVIEW" upload_slots="resume.pdf:application/pdf,cover.txt:text/plain">
      Please upload your résumé as `resume.pdf` and your cover letter
      as `cover.txt`.
      </ask>
  allowed_transitions:
    - tag: ask
      next: REVIEW
      upload_slots: "resume.pdf:application/pdf,cover.txt:text/plain"

REVIEW:
  prompt: |
    Read and review:
    - {{task_folder}}/asks/{{ask_id}}/resume.pdf
    - {{task_folder}}/asks/{{ask_id}}/cover.txt

    Produce a one-paragraph summary, then end your reply with
    <result>...summary text...</result>.
  allowed_transitions:
    - { tag: result }
```

`{{ask_id}}` is bound only in the state immediately after the `<ask>`. If a later state needs it, write it to a file from REVIEW.

## Polling for an external condition

**Problem**: Wait for a build / deploy / external job to finish.

**Shape**: A `sh` state that exits with the status as `<result>`, called by an LLM driver that loops via `<goto>`.

```yaml
POLL_LOOP:
  prompt: |
    Status so far: {{input}}

    End your reply with exactly one of:
      <goto>PROCESS</goto>             — status is "ready", proceed
      <call return="POLL_LOOP">CHECK</call>   — still pending, poll again
  allowed_transitions:
    - { tag: goto, target: PROCESS }
    - tag: call
      target: CHECK
      return: POLL_LOOP

CHECK:
  sh: |
    set -euo pipefail
    sleep 30
    status=$(curl -s https://api.example.com/status)
    echo "<result>$status</result>"
```

The `sh` state contributes zero LLM cost while it polls. The LLM state above it is just the loop body; with a tight policy, the model can `<goto>` to itself or to `PROCESS` without ambiguity.

## Worktree-per-fork (parallel coding agents)

**Problem**: Multiple agents need to edit the same repo without colliding.

**Shape**: `<fork cd="/path/to/worktree-N">` so each worker operates in its own git worktree.

```yaml
DISPATCH:
  prompt: |
    Spawn three workers, each in a separate worktree. Emit exactly these
    four tags (the `cd` paths and the `branch` / `goal` attributes are
    per-worker; `branch` and `goal` arrive in the worker's prompt as
    template variables; `cd` is consumed by the orchestrator):

      <fork next="JOIN" cd="../wt-a" branch="feature/a" goal="refactor">WORKER</fork>
      <fork next="JOIN" cd="../wt-b" branch="feature/b" goal="docs">WORKER</fork>
      <fork next="JOIN" cd="../wt-c" branch="feature/c" goal="tests">WORKER</fork>
      <goto>JOIN</goto>

    Multiple <fork> tags in one output are legal; a single <goto> may
    accompany them for the parent's continuation.
  allowed_transitions:
    - tag: fork
      target: WORKER
      next: JOIN
    - { tag: goto, target: JOIN }

WORKER:
  prompt: |
    You are in worktree {{branch}}. Implement {{goal}}. Commit and push.

    End your reply with: <result>{{branch}} done</result>
  allowed_transitions:
    - { tag: result }
```

`cd` consumes the path; the remaining attributes (`branch`, `goal`) become template variables in the worker. Avoid using the reserved name `task` here — it is consumed by the orchestrator for task-folder control (`task="new"` / `task="inherit"`) and never reaches the worker as a template variable.

## Hide branching decisions inside a call (loop reliability)

**Problem**: A loop iterates with `<goto>` back to itself; on each iteration the prompt's history grows. If that prompt contains "emit `<goto>DONE</goto>` when finished," the LLM sees that instruction again on the next iteration — and may inappropriately decide to finish even when more work remains. The completion tag has *leaked into the loop body's chat history*.

**Shape**: Make the loop body's policy implicit (single non-result, non-ask entry) so its prompt needs no tag instruction. Push the branching decision into a `<call>` — its branched session is invisible to the parent. Then use a `sh` state to translate the child's result into the next `<goto>`.

```yaml
REVIEW:
  prompt: |
    Review the work. Make any fixes you find appropriate.
  # Implicit transition — REVIEW's prompt mentions no tags. Loop history
  # never accumulates completion-tag instructions.
  allowed_transitions:
    - tag: call
      target: DID_FIX
      return: AGAIN_CHOICE

DID_FIX:
  prompt: |
    Look at the review that just completed in this session's history.
    Did it actually change any files?

    Respond with: <result>YES</result> or <result>NO</result>
  # Fixed-payload result entries — policy enforces YES/NO exactly.
  allowed_transitions:
    - { tag: result, payload: YES }
    - { tag: result, payload: NO }

AGAIN_CHOICE:
  sh: |
    if [ "$RAYMOND_INPUT" = "YES" ]; then
      echo "<goto>REVIEW</goto>"
    else
      echo "<goto>COMMIT</goto>"
    fi
```

**Why it works.** `<call>` branches the caller's session: DID_FIX runs in a fork of REVIEW's context, decides, and returns only the payload. REVIEW's own session never sees the YES/NO instructions. On the next iteration, REVIEW's history contains its prior review work but no tag-emission text, so the model can't be primed toward the wrong tag.

This is a reliability hint, not a hard rule. Most workflows don't need it. Reach for it when (a) a loop body has more than one possible exit, and (b) you've seen the model exit the loop prematurely. The full pattern is in `workflow_samples/work.yaml` (REVIEW → DID_FIX → AGAIN_CHOICE → REVIEW/COMMIT).

## When to script vs. when to prompt

Use a **script state** when the work is deterministic and cheap (build, fetch, file glob, simple parse). Use a **markdown state** when judgement is required. A common pattern is `script → markdown → script`: the script normalizes inputs, the LLM decides, the script applies the decision. This keeps token spend on the parts that need a brain.
