# YAML Schema Reference

The authoritative field reference for a `.yaml` workflow file. For tag semantics see [transitions.md](transitions.md); for runtime mechanics see [protocol.md](protocol.md).

## Top-level keys

```yaml
# Manifest (all optional; only consumed by `ray serve`)
id: vendor-approval
name: Vendor Approval
description: Evaluates a vendor proposal and routes through human approval
default_budget: 5.0
working_directory: ""
environment:
  API_KEY: ${API_KEY}
requires_human_input: auto      # auto | true | false
input:
  mode: required                # required | optional | none
  label: Vendor name
  description: The vendor to evaluate

# The only required key
states:
  STATE_NAME:
    ...
```

### Required

- `states` — non-empty mapping of state name → state definition.

### Manifest fields (optional, daemon-only)

Manifest keys have **no effect on CLI behavior**. `ray workflow.yaml` runs identically with or without them. They are consumed exclusively by `ray serve` for discovery.

| Field | Type | Default | Purpose |
|---|---|---|---|
| `id` | string | — | Required *for daemon discovery*. Without it the workflow still runs via CLI but is not indexed. Must be unique across the daemon's root. |
| `name` | string | `""` | Human-readable display name in the web UI / MCP tool list. |
| `description` | string | `""` | Description surfaced in tool/endpoint docs. |
| `default_budget` | float | `0` | Default USD budget when callers don't specify one. |
| `working_directory` | string | `""` | Default cwd for runs. |
| `environment` | map | nil | Env vars for runs; supports `${VAR}` interpolation from the daemon's environment. |
| `requires_human_input` | string | `"auto"` | `auto`/`true`/`false`. Controls whether the daemon rejects launches from contexts that cannot deliver `<ask>` input. `auto` resolves by scanning every state's `allowed_transitions` for an `ask` entry (transitively through `call-workflow` / `function-workflow` targets) — true if any are found, false otherwise. Set to `true` or `false` explicitly if you don't want the scan, or if the workflow emits `<ask>` only via paths that aren't declared in `allowed_transitions`. |
| `input` | object | `{mode: optional}` | Shape of the launch input (see below). |

### The `input` block

A workflow receives at most one string at launch time, available as `{{input}}` in the first state.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `mode` | string | `optional` | `required` (reject empty), `optional` (accept either), `none` (reject non-empty). |
| `label` | string | `""` | Short prompt shown next to the input field in the UI / MCP. |
| `description` | string | `""` | Longer help text under the input field. |

Omitting the block entirely is equivalent to `input: { mode: optional }` with no label.

## State definitions

A state is **either** a markdown state (LLM prompt) **or** a script state (shell). Never both, never neither.

### Markdown state

```yaml
REVIEW:
  prompt: |
    Review the code in {{input}}.
    Focus on edge cases and error handling.
  allowed_transitions:
    - { tag: goto, target: FIX }
    - { tag: result }
  model: sonnet
  effort: high
  timeout: 120
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `prompt` | string | yes | The prompt sent to the model. Supports `{{input}}`, `{{workflow_id}}`, `{{agent_id}}`, `{{task_folder}}`, `{{ask_id}}` (bound only in the state immediately after an `<ask>` resolves). |
| `allowed_transitions` | list | no | Restrict which tags + attributes the agent may emit. See below. |
| `model` | string | no | `opus`, `sonnet`, or `haiku`. Overrides the `--model` CLI default. Falls back to `sonnet` if neither is set. |
| `effort` | string | no | `low`, `medium`, `high`. Extended-thinking level. |
| `timeout` | number | no | Per-state timeout in seconds. Overrides global `--timeout`. `0` means no timeout. Negative values are a validation error. |

### Script state

```yaml
BUILD:
  sh: |
    set -euo pipefail
    make build 2>&1
  bat: |
    @echo off
    nmake build 2>&1
  timeout: 60
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `sh` | string | at least one of `sh`/`ps1`/`bat` | POSIX shell script. |
| `ps1` | string | ↑ | PowerShell. |
| `bat` | string | ↑ | Windows batch. |
| `timeout` | number | no | Same semantics as markdown. |

**Restrictions on script states**: no `prompt`, no `allowed_transitions`, no `model`, no `effort`. Script states emit transition tags directly to stdout — the orchestrator parses them with the same protocol as LLM output. They cannot recover from parse failures (no LLM to re-prompt).

### `allowed_transitions`

The policy is a list of entries. Each entry is a mapping with a `tag` key and the attributes required for that tag.

```yaml
allowed_transitions:
  - { tag: goto, target: NEXT }
  - { tag: goto, target: FIX }
  - tag: call
    target: RESEARCH
    return: SUMMARIZE
  - tag: function
    target: EVAL
    return: NEXT
  - tag: fork
    target: WORKER
    next: CONTINUE
  - tag: ask
    next: HANDLE_RESPONSE
    timeout: "48h"
    timeout_next: ESCALATE
  - { tag: result }
```

**Matching rule**: the agent must emit a transition whose `tag` matches one entry, and whose **structural attributes** (`target`, `return`, `next`, `timeout_next`) match the values in that entry when those keys are present in the policy. Mismatch → re-prompt with an auto-generated reminder (up to 3 attempts) → fail.

Attributes **not** validated by the policy:

- `input` — payload values pass through unchecked.
- `cd` — working-directory values pass through unchecked.
- Custom attributes on `<fork>` (the ones that become template variables in the worker, e.g. `item="x"`).

This means `{ tag: fork, target: WORKER, next: JOIN }` matches `<fork next="JOIN" item="task1" input="urgent">WORKER</fork>` — the extra attributes are accepted.

**Bare vs. extensioned targets in the policy**: writing `target: NEXT` (bare) accepts both `<goto>NEXT</goto>` and `<goto>NEXT.md</goto>` (and `.sh`/`.ps1`/`.bat` if `NEXT` is a script state). Writing `target: NEXT.md` (with extension) only accepts an exact match. Bare is the recommended form for YAML workflows.

**`<result>` with a fixed payload**: a policy entry `{ tag: result, payload: "ok" }` requires the result payload (after trimming) to equal `"ok"`. Without a `payload` key in the entry, any payload is accepted.

**Implicit transition**: when the list has exactly one entry and that entry is neither `<ask>` nor a bare `<result>`, the model may omit the tag and the orchestrator assumes it. `<ask>` is excluded because only the model can compose the human-facing prompt; bare `<result>` is excluded because the payload is variable. A `result` entry with a fixed `payload:` *is* implicit. See [transitions.md §What the LLM sees](transitions.md#what-the-llm-sees) for the full table and the prompt-authoring implications.

**Without the field**: all transitions are permitted, but parse failures terminate the agent immediately (no reminder can be generated). Reserve "no `allowed_transitions`" for prototyping; production workflows should declare policy.

## State name and target rules

- State names: non-empty strings; no `/` or `\` anywhere; unique within the workflow.
- Transition targets: **bare state name** (`REVIEW`). Extensions (`.md`, `.sh`, `.ps1`, `.bat`) are accepted for back-compat with directory-based workflows but unnecessary. No path separators.

## How bare targets resolve

The orchestrator maps a bare target name to its state by lookup, then dispatches to either the markdown executor or the script executor based on which keys the state defines. For script states that declare multiple platforms (any combination of `sh`/`ps1`/`bat`), the platform is chosen at run time so the same workflow runs on Unix and Windows without changing the targets.

Internally each state is also exposed under a virtual filename (`STATE.md`, `STATE.sh`, `STATE.ps1`, `STATE.bat`) so that lint, diagram, and orchestrator code can share machinery with directory-based workflows. Authors of YAML workflows do not need to write these extensions.

## Error taxonomy

| Class | When |
|---|---|
| **Parse error** | File unreadable or YAML syntax invalid. |
| **Validation error** | YAML is well-formed but violates schema: missing `states`, state with both `prompt` and `sh`, state with neither, script state declaring `allowed_transitions`/`model`/`effort`, negative `timeout`, duplicate state name, state name containing `/` or `\`. |
| **File not found** | A transition target does not match any state in the workflow. |
| **Protocol error** (runtime) | Zero tags, or multiple tags outside the multi-`<fork>` exception, in a state's output; or an emitted transition that doesn't match `allowed_transitions`. With `allowed_transitions` defined, this triggers a reminder re-prompt (default cap: 3 attempts); without it, the agent terminates. Script states cannot be re-prompted — a script that prints zero or multiple tags fails the agent immediately. |

When a workflow declares states at the root without the `states:` wrapper, the parser recognizes common state-like keys and suggests adding the wrapper.

## Full self-describing example

```yaml
id: vendor-approval
name: Vendor Approval
description: Evaluates a vendor proposal and routes through human approval
default_budget: 5.0
input:
  mode: required
  label: Vendor name
  description: The vendor to evaluate.
requires_human_input: auto

states:
  1_START:
    prompt: |
      Research the vendor "{{input}}" and prepare a short analysis covering
      price, support SLA, and references.

      Do NOT make a recommendation yourself. End your reply with this exact
      form (the body of <ask> is what the reviewer sees):

        <ask next="DECISION" timeout="48h" timeout_next="ESCALATE">
        ...your analysis...
        Respond with "approve", "reject", or "more info: ..." with the
        additional analysis you need.
        </ask>
    allowed_transitions:
      - tag: ask
        next: DECISION
        timeout: "48h"
        timeout_next: ESCALATE

  DECISION:
    prompt: |
      The reviewer's decision: {{input}}

      Carry out the decision and produce a final report in vendor_report.md.

      End your reply with: <result>completed</result>
    allowed_transitions:
      - { tag: result }

  ESCALATE:
    prompt: |
      The vendor decision timed out. Send an escalation notice and close.

      End your reply with: <result>escalated</result>
    allowed_transitions:
      - { tag: result }
```
