---
name: ray-workflows
description: Authoring Raymond (ray) workflows as single-file YAML — finite-state machines whose states are LLM prompts or shell scripts, transitioning via XML tags (`<goto>`, `<reset>`, `<call>`, `<function>`, `<fork>`, `<ask>`, `<result>`). Use when the user is creating, editing, debugging, or running a `.yaml` ray workflow, mentions raymond/ray workflows, or asks about transitions, the return stack, `allowed_transitions`, `<ask>` human input, or the `ray` CLI.
---

# Ray Workflows (YAML)

A workflow is a **finite-state machine**. Each state is either an **LLM prompt** (markdown) or a **shell script** (`sh`/`ps1`/`bat`). Each state's output contains **exactly one** transition tag, which decides where the workflow goes next. The whole workflow lives in one `.yaml` file.

> Terminology: a *workflow run* is the whole execution (the thing `ray --resume <id>` resumes). A *state output* is the single message produced by one state's invocation — that's where the transition tag lives.

> This skill covers YAML workflows only. Directory and zip layouts exist but are not the recommended form for new work.

## The 7 transition tags

| Tag | What it does | Session | Stack |
|---|---|---|---|
| `<goto>STATE</goto>` | Continue this thread | resume current | unchanged |
| `<reset>STATE</reset>` | Fresh session, same agent | new | unchanged |
| `<call return="NEXT">CHILD</call>` | Subroutine; child returns via `<result>` | branched from caller | push |
| `<function return="NEXT">EVAL</function>` | Pure eval; no caller context | fresh | push |
| `<fork next="NEXT" ...>WORKER</fork>` | Spawn parallel agent | worker: new; parent: resume current | worker: empty; parent: unchanged |
| `<ask next="NEXT">prompt</ask>` | Suspend for human input | preserved | unchanged |
| `<result>payload</result>` | Return to caller, or terminate if stack empty | — | pop |

Targets are **bare state names** — `STATE`, not `STATE.md`. The orchestrator resolves the name to the right state in the workflow (and, for script states, picks `.sh` / `.ps1` / `.bat` based on platform). Extensions are accepted for back-compat but no longer needed.

## YAML shape

```yaml
# Optional manifest (only needed for `ray serve` discovery)
id: my-workflow
name: My Workflow
description: One-line summary
input: { mode: optional, label: "Input" }

# Skeleton showing field structure — see "Minimal runnable workflow" below for a working example.
states:
  1_START:
    prompt: |
      <prompt text — must instruct the model which tag to emit, since
      this state has multiple allowed transitions (not implicit)>
    allowed_transitions:
      - { tag: goto, target: BUILD }
      - { tag: result }
    model: sonnet            # opus | sonnet | haiku (optional)
    effort: high             # low | medium | high  (optional)
    timeout: 120             # seconds, per-state    (optional)

  BUILD:
    sh: |
      set -euo pipefail
      make build
    # script states: no prompt, no allowed_transitions, no model, no effort
```

## The 4 rules you must internalize

1. **Exactly one transition tag per state output.** The one exception: an output may contain multiple `<fork>` tags (each spawns one worker), optionally combined with a single `<goto>` for the parent's continuation.
2. **Targets are bare state names.** No `/` or `\`. Extensions are accepted but unnecessary. The exception is cross-workflow tags (`<call-workflow>` etc.) whose content is by design a filesystem path.
3. **A state is markdown XOR script.** A state with both `prompt` and `sh` (or neither) is a validation error.
4. **`<result>` is the only tag that pops the stack.** Everything else either preserves the stack or pushes.

## Authoring discipline: the LLM is oblivious to the workflow

The model running a markdown state sees **only the prompt you wrote** (with `{{input}}` etc. substituted). It does not know it is inside a state machine, does not see `allowed_transitions`, and does not know that the orchestrator is parsing its output for an XML tag. If the prompt doesn't tell it to emit a transition tag, it won't.

The practical rule: **every prompt must instruct the model, in plain English, what tag(s) to emit and when** — except in cases the orchestrator can handle implicitly:

- **Implicit transition (free pass)**: when `allowed_transitions` has exactly one entry and that entry is **neither `ask` nor a bare `result`** (a `result` with a fixed `payload:` is fine), the orchestrator dispatches the transition without requiring a tag in the output. The prompt may stay tag-free.
- **`<ask>` is never implicit** — the model has to compose the human-facing prompt inside the tag. Always tell the prompt to emit `<ask next="…">…</ask>`.
- **Bare `<result>` is never implicit** — the payload is variable, so the model has to emit it. Always tell a returning state to wrap its output in `<result>…</result>`.

If a non-implicit prompt forgets to instruct the model, the orchestrator's auto-generated reminder will recover on the next attempt (when `allowed_transitions` is declared) — but you pay an extra LLM round trip every time it fires. With `allowed_transitions` absent, the same omission terminates the agent fatally. Either way, the prompt is the right place to make the instruction explicit.

## Minimal runnable workflow

```yaml
states:
  1_START:
    prompt: |
      Greet {{input}} warmly in one sentence.
    # Implicit transition — single non-result, non-ask goto. The
    # orchestrator dispatches to DONE; the prompt needs no tag instruction.
    allowed_transitions:
      - { tag: goto, target: DONE }

  DONE:
    prompt: |
      Say goodbye in one sentence.

      End your reply with exactly: <result>done</result>
    allowed_transitions:
      - { tag: result }
```

```bash
ray workflow.yaml --input "Alice"
```

Entry point: `1_START` if present, otherwise `START`. Start elsewhere with `ray workflow.yaml/DONE` (bare state name).

## Load further references on demand

Each file below is loaded only when you need it. They do **not** repeat the cheat sheet above.

- **Picking a tag** — semantics, attributes, common pitfalls per tag, and "which tag should I use here?" → [transitions.md](transitions.md)
- **YAML schema** — every field, validation rules, error taxonomy, manifest metadata for `ray serve` → [schema.md](schema.md)
- **Cross-cutting semantics** — return stack, `{{input}}` forwarding, `<ask>` file uploads (slot vs bucket), `cd`/`cwd`, template variables → [protocol.md](protocol.md)
- **Recipes** — retry loops, fan-out/fan-in, human-review gates, polling, escalation → [patterns.md](patterns.md)
- **CLI** — running, `ray lint`, `ray diagram`, entry-point resolution, `--input`, `--on-ask`, resume → [cli.md](cli.md)
- **Worked examples** (runnable `.yaml`) → [examples/](examples/)

## Authoring workflow

1. Sketch the states and the arrows on paper (or in a diagram comment at the top of the file). Decide which states are LLM and which are script.
2. Write the YAML shape with empty prompts and the `allowed_transitions` lists. The transition graph is the contract.
3. Fill in prompts. Reference `{{input}}` where the prior state's payload should appear, and instruct the model which tag(s) to emit — skip the instruction only when the implicit-transition rules apply (see *Authoring discipline* above).
4. Run `ray lint workflow.yaml` — catches missing targets, unreachable states, policy violations, and (via `unused-allowed-transition`) prompts that don't mention a target listed in `allowed_transitions`. Zero cost; run after every edit.
5. Optionally run `ray diagram workflow.yaml --html --output diagram.html` to eyeball the FSM.
6. Run the workflow. Where the policy has exactly one allowed transition and that entry is neither `<ask>` nor a bare `<result>`, the orchestrator assumes the transition implicitly — the model may omit the tag.
