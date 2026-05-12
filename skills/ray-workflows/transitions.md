# Transitions

Every LLM state and every script state produces an output containing exactly one transition tag (with the multi-`<fork>` exception noted below). This page is the reference for picking and writing each tag.

> Cheat sheet lives in [SKILL.md](SKILL.md). Return-stack and `{{input}}` mechanics live in [protocol.md](protocol.md).

## What the LLM sees

A markdown state's LLM gets **only the prompt text** (with `{{input}}`, `{{task_folder}}`, etc. substituted). The orchestrator does not inject any standing instruction about workflows, tags, or `allowed_transitions`. The model is, from its own perspective, just answering one Claude Code prompt — it has no idea a state machine is parsing its reply.

So the prompt itself has to teach the model how to end. For a state where the model picks among several next steps, that means writing the tag form into the prompt:

```yaml
TRY:
  prompt: |
    Attempt {{input}} of 3. Run the build and inspect the result.

    End your reply with exactly one of:
      <goto>DONE</goto>             — build passed
      <goto input="N+1">TRY</goto>  — failed, retry; replace N+1 with the next attempt number
      <goto>FAIL</goto>             — failed and attempts are exhausted
  allowed_transitions:
    - { tag: goto, target: TRY }
    - { tag: goto, target: DONE }
    - { tag: goto, target: FAIL }
```

The prompt's bottom paragraph is doing the work `allowed_transitions` cannot do: it tells the model which tags exist and when each applies. `allowed_transitions` is a server-side validator; it does not appear in the model's input.

### When you can skip the instruction

The orchestrator can fill in the tag itself in two cases, and only two:

| Policy shape | Implicit? | What the prompt needs |
|---|---|---|
| Exactly one allowed transition with a `target`, and tag is **not** `ask` or bare `result` | yes | Prompt may end without any tag instruction. |
| Exactly one allowed transition `{ tag: result, payload: "fixed-string" }` | yes | Prompt may end without any tag instruction (the orchestrator will return the fixed payload). |
| Exactly one allowed `ask` | **no** | Prompt must tell the model to emit `<ask next="…">…question…</ask>`. The tag body **is** the human-facing question — only the model can write it. |
| Exactly one allowed bare `result` (no fixed payload) | **no** | Prompt must tell the model to emit `<result>…</result>` with the payload it wants to return. |
| Multiple allowed transitions | no | Prompt must spell out which tag to emit when. |

When the implicit case applies, it's good style to note it in a comment so a future reader knows the missing instruction is deliberate:

```yaml
PLAN:
  prompt: |
    Write a step-by-step plan to plan.md.
  # Implicit transition — single non-result, non-ask reset; no tag needed in the prompt.
  allowed_transitions:
    - { tag: reset, target: IMPLEMENT }
```

### What the reminder mechanism does and doesn't do

When `allowed_transitions` is declared and the model's output has zero or wrong tags, the orchestrator auto-generates a reminder prompt listing the permitted transitions and asks the model to try again (default cap: 3 attempts). This recovers from a forgotten instruction — but every reminder costs one extra Claude Code round trip. Treat reminders as a safety net, not as a substitute for clear prompts.

When `allowed_transitions` is absent, no reminder can be generated; a missing tag terminates the agent immediately. Reserve omitting `allowed_transitions` for prototyping.

### Lint catches the most common gap

`raymond lint workflow.yaml` emits a warning (`unused-allowed-transition`) for any state with two or more `allowed_transitions` entries whose targets are not mentioned anywhere in the prompt body. It does not lint single-entry policies (those are presumed implicit). It's not a guarantee — the prompt could mention the target without instructing the model to emit a tag — but it catches the most common authoring mistake.

### Reliability hint: keep loop bodies tag-free

When a state loops back to itself, tag-emission instructions in its prompt accumulate in the session's history. After a few iterations the LLM has seen the completion tag repeatedly, and may emit it prematurely. The mitigation: give the loop body an implicit single-entry policy (no tag instructions in its prompt) and push the branching decision into a `<call>` whose branched session is invisible to the parent. See [patterns.md §Hide branching decisions inside a call](patterns.md#hide-branching-decisions-inside-a-call-loop-reliability) for the full shape. This is a refinement, not a default — reach for it when you see a loop exit prematurely.

### Phase-boundary "STOP" instructions: "yet" beats absolutes

When a state's job ends before the next obvious step (write a plan, then stop; review, then stop), the prompt usually has to tell the LLM to stop short of continuing. A flat prohibition like "Do not start implementation" can persist in the chat history visible to later `<goto>`-reached states and make those states feel constrained — including states whose job *is* implementation. Adding "yet" softens the prohibition to the current turn:

```
Brittle: STOP. Do NOT start implementation.
Better:  Do not start implementation yet — that happens in a later step.
```

Same effect now, but a future state in the same session is no longer fighting a residual "no implementation" instruction. The prohibition only persists while the LLM continues in the same session — `<reset>`, `<fork>` workers, and `<function>` children start fresh and leave it behind; everything else (including `<call>`/`<function>` return states, which resume the caller's session) inherits it. The workflows in `workflow_samples/` use this convention throughout.

## Quick decision tree

- Need to continue with full context? → **`<goto>`**
- Need to drop context but stay in the same agent (e.g. phase boundary)? → **`<reset>`**
- Delegating a noisy/iterative subtask whose only output is a summary? → **`<call>`**
- Delegating a stateless evaluation that should not see the caller's history? → **`<function>`**
- Fanning out independent work in parallel? → **`<fork>`**
- Need a human in the loop? → **`<ask>`**
- Returning to caller, or terminating? → **`<result>`**

## Attributes summary

| Tag | Required attrs | Optional attrs | Body |
|---|---|---|---|
| `<goto>` | — | `input` | target state name |
| `<reset>` | — | `input`, `cd`, `task="new"` | target state name |
| `<call>` | `return` | `input` | child state name |
| `<function>` | `return` | `input` | eval state name |
| `<fork>` | `next` | `input`, `cd`, `task="new"\|"inherit"`, *any other → template var* | worker state name |
| `<ask>` | `next` | `timeout`, `timeout_next`, `upload_*`, `display_files` | human-facing prompt |
| `<result>` | — | — | payload (passed verbatim) |

State-name targets (in the tag body, in `return=`, `next=`, `timeout_next=`) are **bare names** — `REVIEW`, not `REVIEW.md`. Extensions are accepted for back-compat but redundant; script states are resolved to the right platform extension automatically. `input` and `cd` are consumed by the orchestrator and do **not** become template variables. Extra attributes on `<fork>` (`item="x"`) do become template variables (`{{item}}`).

---

## `<goto>STATE</goto>`

Continue the same Claude Code session and load the target state.

```
<goto>NEXT</goto>
<goto input="3 issues found">REVIEW</goto>
```

- **Session**: resumed (same context, history visible to the next state).
- **Stack**: unchanged.
- **Use when**: the next step needs to see what this step did.

**vs `<reset>`** — `<goto>` keeps history. Pick `<reset>` when the prior chat is noise but the workflow logic should continue. **vs `<call>`** — `<goto>` is one-way; `<call>` will return here.

## `<reset>STATE</reset>`

Start a fresh session and load the target state in the same agent.

```
<reset>IMPLEMENT</reset>
<reset cd="/repo/worktree-feature">IMPLEMENT</reset>
<reset input="phase 2">IMPLEMENT</reset>
```

- **Session**: new.
- **Stack**: unchanged — `<reset>` does **not** clear pending returns. If the caller pushed a frame, the child can still `<result>` back to it across the reset.
- **`cd`**: changes the agent's working directory for all subsequent state executions until another `<reset cd=...>`.
- **`task="new"`**: increments the agent's task counter and assigns a fresh `{{task_folder}}`. Useful in retry loops where each attempt should have its own scratch space. Without the attribute, the task folder is preserved across the reset.

**Common shape**: planning state writes a plan to disk, then `<reset>IMPLEMENT` so the implementer reads the plan fresh rather than inheriting the planner's chat.

## `<call return="NEXT">CHILD</call>`

Run a child state that will eventually return via `<result>`. The child runs in a **branched** session (forks the caller's context, then accumulates its own).

```
<call return="SUMMARIZE">RESEARCH</call>
<call return="SUMMARIZE" input="focus on security">RESEARCH</call>
```

- **Session**: child branches from caller; on `<result>`, caller's original session resumes at the `return` state.
- **Stack**: push a frame (`{ session: caller, state: <return-state> }`).
- **Caller never sees the child's chatter** — only the `<result>` payload, via `{{input}}` in the return state.

**vs `<function>`** — `<call>` gives the child the caller's history. `<function>` gives the child nothing but its prompt. Pick `<function>` for cheap, stateless evaluators.

## `<function return="NEXT">EVAL</function>`

Like `<call>`, but the child starts with **no session history**.

```
<function return="HANDLE_VERDICT" input="test output: 3 failures">JUDGE</function>
```

- **Session**: fresh.
- **Stack**: push.
- **Use when**: the evaluation must not be biased by, or aware of, the caller's context. Classic case: an LLM-judge that reads only its own prompt plus `{{input}}`.

## `<fork next="NEXT" ...>WORKER</fork>`

Spawn an independent agent. The parent continues at `next`; the worker runs the worker state with its own lifecycle.

```
<fork next="DISPATCH-NEXT" item="issue-123">WORKER</fork>
<fork next="JOIN" cd="/repo/wt-a" input="high priority">WORKER</fork>
```

- **Worker stack**: empty (a worker that emits `<result>` simply terminates).
- **Parent stack**: unchanged (parent continues as if from `<goto>`).
- **Extra attributes** beyond `next`, `cd`, `input`, `task` become template variables in the worker's prompt (e.g. `item="issue-123"` → `{{item}}`).
- **`task="inherit"`** copies the parent's `{{task_folder}}` to the worker. Default (or `task="new"`) gives the worker its own fresh task folder — usually what you want for fan-out.
- **Multiple forks in one output are legal** — each spawns one worker. You can also pair them with a single `<goto>` for the parent.

The parent has no built-in way to wait for the worker. To fan-in, pattern the workers to write to a shared location and poll/await from a coordinator state. See [patterns.md](patterns.md#fan-out-fan-in).

## `<ask next="NEXT" ...>prompt</ask>`

Suspend the agent and request human input. The tag body is the **human-facing prompt** (anything before the tag is reasoning visible only in logs).

```
<ask next="HANDLE_DECISION" timeout="48h" timeout_next="ESCALATE">
I've completed my analysis of the three vendors.

Please respond with:
- "approve A/B/C" to select a vendor
- "reject" to restart sourcing
- "more info [question]" to dig deeper
</ask>
```

- **Required**: `next` (state to enter when input arrives).
- **Optional**: `timeout` (e.g. `"30m"`, `"24h"`, `"7d"`), `timeout_next` (state on timeout; if omitted on timeout the workflow fails).
- **Session**: preserved across the ask, like `<goto>`.
- **Never implicit.** The tag body **is** the human-facing question; only the LLM can write it. Your prompt must explicitly tell the model to emit `<ask next="...">...question...</ask>`. (See [What the LLM sees](#what-the-llm-sees).)
- **Input delivery**:
  - CLI default (`--on-ask=reject`) — the workflow fails if it asks. Use for CI.
  - `--on-ask=pause` — Raymond writes a structured JSON pending-input record to stdout and exits with code 2; resume via `raymond --resume <id> --input "..."`.
  - `raymond serve` — pending input is exposed through the HTTP API and MCP elicitation.
- **No `cd` allowed** on `<ask>` (the agent is suspended, the directory is irrelevant).
- **File attachments** (slot/bucket/display) are an opt-in extension — see [protocol.md](protocol.md#ask-file-attachments).

## `<result>payload</result>`

Return to the most recent caller's return state (with the payload available as `{{input}}` there), or terminate if the stack is empty.

```
<result>Found 3 issues; details in issues.md</result>
```

- **Payload**: raw text, passed verbatim. The orchestrator does not summarize.
- **Stack**: pop one frame, or terminate the agent.
- **Bare `<result>` is never implicit.** Even when `allowed_transitions` permits only `<result>`, the model must still emit the tag because the payload is variable. Your prompt must instruct the model to wrap its output in `<result>...</result>`. The one exception is a policy entry that pins the payload (`{ tag: result, payload: "ok" }`) — in that case the orchestrator returns the fixed payload implicitly and the prompt can stay tag-free.

---

## Implicit transitions (no-tag-needed shortcut)

When a state's `allowed_transitions` lists **exactly one** entry, and that entry is not `<ask>` and not a bare `<result>` (a `result` with a fixed `payload:` is fine), the orchestrator assumes that transition if the model emits no tag. This saves tokens when the path is forced — and it's the one case where you can omit transition instructions from the prompt. See [What the LLM sees](#what-the-llm-sees) for the policy-shape table.

```yaml
PLAN:
  prompt: |
    Write a plan to plan.md.
  allowed_transitions:
    - { tag: reset, target: IMPLEMENT }
  # Model may emit <reset>IMPLEMENT</reset> or omit it entirely.
```

If the model does emit a tag, it must match the policy. Structural attributes (`target`, `return`, `next`, `timeout_next`) are checked; `input`, `cd`, and custom fork attributes pass through unchecked. See [schema.md §allowed_transitions](schema.md#allowed_transitions) for full matching rules.

Implicit transitions also have a useful side effect: the orchestrator template-renders the policy entry's `input="..."` value before dispatching, so an implicit transition is a tidy way to forward `{{input}}` / `{{workflow_id}}` / `{{task_folder}}` without asking the model to interpolate them. See [protocol.md §is the input attribute itself a template](protocol.md#is-the-input-attribute-itself-a-template).

## Multi-tag outputs

The default is exactly one tag per state output. The single exception is parallelism:

- Multiple `<fork>` (and/or `<fork-workflow>`) in one output — each spawns one worker.
- Optionally combined with **one** `<goto>` for the parent's continuation.

No other combinations are legal. Two `<goto>`s, a `<call>` plus a `<goto>`, a `<result>` with anything else — all are protocol errors.

## Cross-workflow tags (brief)

Outside the scope of single-file YAML, but valid from inside one: `<call-workflow>`, `<function-workflow>`, `<fork-workflow>`, and `<reset-workflow>` transition to another workflow scope by filesystem path. They are exempt from the no-slash rule because their body **is** a path. See raymond's `docs/cross-workflow-design.md` for full semantics — most YAML workflows do not need them.
