# Protocol & Runtime Semantics

The cross-cutting machinery that no single tag owns: the return stack, how `{{input}}` is forwarded, `<ask>` file modes, working-directory rules, template variables, and error recovery. Read this when something composes oddly across tags.

## The return stack

Each live agent maintains its own **return stack**. A frame says "where to resume when a callee emits `<result>`":

- **session** — which Claude Code session to resume (the caller's session).
- **state** — which state to load.
- **scope_dir / cwd** — restored when the frame is popped (used by cross-workflow returns).

### Operations

| Tag | Stack effect |
|---|---|
| `<goto>`, `<reset>`, `<ask>` | unchanged |
| `<call>`, `<function>` | push a frame for the caller (`{ session, return-state }`) |
| `<fork>` | parent unchanged; worker starts with empty stack |
| `<result>` | pop one frame; if empty, the agent terminates |

A worker spawned by `<fork>` that emits `<result>` simply terminates — there's no caller to return to.

### Why this is robust

The callee can `<goto>` and `<reset>` as many times as it likes before returning. The stack is unchanged by those, so `<result>` still pops back to the original caller:

```
S1 --call(return=S3)--> S2
S2 --goto--> S4
S4 --reset--> S5
S5 --result--> (pop) resume caller's session at S3
```

`<reset>` looks dramatic but does **not** clear the stack — the caller is still waiting.

## `{{input}}` forwarding

`{{input}}` is the one universal template variable that carries payloads between states. It is set when a state receives data from any of:

- The previous state's `<result>` payload (when popping into the return state).
- An `input="..."` attribute on the transition that landed here.
- The human's response after an `<ask>`.
- The CLI `--input` flag (sets `{{input}}` for the first state only).

After the state runs, `{{input}}` is cleared. To carry data further, plumb it forward explicitly via the next transition's `input` attribute, or write it to a file in the task folder.

```
<goto input="3 issues found">REVIEW</goto>
<call return="SUMMARIZE" input="focus on security">RESEARCH</call>
<fork next="JOIN" input="batch-42">WORKER</fork>
```

### Is the `input="..."` attribute itself a template?

It depends on the tag — this is a sharp edge worth memorising:

| Transition source | `input="..."` is template-rendered? |
|---|---|
| Explicit within-workflow tag (`<goto>`, `<reset>`, `<call>`, `<function>`, `<fork>`) emitted by the model | **No** — passed verbatim. `<goto input="batch {{workflow_id}}">NEXT</goto>` sends the literal string `batch {{workflow_id}}` as the next state's `{{input}}`. |
| Implicit transition (no tag emitted; policy has exactly one allowed transition, and that entry is neither `ask` nor a bare `result`) | **Yes** — the executor renders `{{input}}`, `{{workflow_id}}`, `{{agent_id}}`, `{{task_folder}}`, and fork attributes before dispatching. |
| Cross-workflow tag (`<call-workflow>`, `<function-workflow>`, `<fork-workflow>`, `<reset-workflow>`) | **Yes** — same variables as implicit. |

If you need a within-workflow transition to forward a templated value, either (a) declare the transition implicitly — a single `allowed_transitions` entry that's not `ask` and not a bare `result` — and let the orchestrator render it, or (b) have the model interpolate the value into the attribute itself before emitting the tag.

## Template variables

| Variable | Bound to | Available in |
|---|---|---|
| `{{input}}` | Pending payload (from `<result>`, `input="..."`, `<ask>` response, or `--input`) | Every prompt where the value is set; otherwise unsubstituted |
| `{{workflow_id}}` | Workflow run ID | Every prompt |
| `{{agent_id}}` | Current agent's ID (`main`, or a forked worker's ID) | Every prompt. For `<fork-workflow>`, evaluates to the **parent** agent's ID inside the spawning state. |
| `{{task_folder}}` | Absolute path to this agent's task folder | Every prompt |
| `{{ask_id}}` | ID of the most-recent resolved `<ask>` | **Only** in the state immediately following the `<ask>` (cleared on the next transition) |
| `{{<custom>}}` | Extra attributes on `<fork>` | Worker's prompt only (`<fork item="x">` → `{{item}}`) |

Unsubstituted variables remain as `{{name}}` in the prompt — they do not error.

Substitution inside transition `input="..."` attributes is governed by the table above ([§Is the `input="..."` attribute itself a template?](#is-the-input-attribute-itself-a-template)) — to summarise: never for explicit within-workflow tags; always for implicit transitions and cross-workflow tags.

### Script states see the same values as environment variables

Script states (`sh`/`ps1`/`bat`) don't use template substitution — instead the orchestrator sets environment variables before running the script:

| Markdown variable | Script env var |
|---|---|
| `{{input}}` | `RAYMOND_INPUT` (set only when input is non-nil, including empty string) |
| `{{workflow_id}}` | `RAYMOND_WORKFLOW_ID` |
| `{{agent_id}}` | `RAYMOND_AGENT_ID` |
| `{{task_folder}}` | `RAYMOND_TASK_FOLDER` |
| custom fork attribute (e.g. `{{item}}`) | same name, no `RAYMOND_` prefix (e.g. `$item`) |

`{{ask_id}}` has no script-state equivalent — the executor does not export it as an env var. If a script must run in the post-ask position and needs to find the upload directory, it can list `$RAYMOND_TASK_FOLDER/asks/` (one entry per recent ask) rather than reading an ID. In practice, post-ask states are almost always markdown.

## Working directory (`cd`)

Agents execute by default in the directory where `ray` was launched. The `cd` attribute changes that for the current agent (`<reset>`) or for a newly spawned worker (`<fork>`).

| Tag | Supports `cd` | Effect |
|---|---|---|
| `<reset>` | yes | Changes current agent's `cwd` for all subsequent state executions. |
| `<fork>` | yes | Sets the worker's initial `cwd`; parent unaffected. |
| `<goto>` | no | Would be incoherent mid-session. |
| `<call>` | no | Branches from caller's session, which is tied to caller's directory. |
| `<function>` | no | Could be added; currently rejected. |
| `<ask>` | no | Agent is suspended; directory is irrelevant. |

**Path resolution**:

- Absolute paths are used as-is (after normalization).
- Relative paths resolve against the invoking agent's current `cwd`, falling back to the orchestrator's launch directory if the agent has no `cwd` set.
- Normalization collapses `.` and `..`, so chained relative `<reset cd="...">` produces clean paths (`../foo/../bar` → `../bar`).
- `cwd` persists across subsequent transitions until another `<reset cd=...>`.
- Both Claude Code subprocesses and script subprocesses respect `cwd`.
- The orchestrator's own working directory is never touched.

The `cd` value is consumed by the orchestrator. It is **not** exposed as a template variable, and it is not passed as a fork attribute to the worker.

## `<ask>` file attachments

`<ask>` defaults to text-only (the human types a response). Optional attributes opt the ask into one of three file modes; they may be combined with display files.

### Modes

- **Slot mode** — declare named upload slots; the user uploads exactly one file per slot.
- **Bucket mode** — declare constraints (count / MIME / size); the user uploads any filenames meeting them.
- **Display only** — expose files for the user to inspect alongside the prompt; no upload.

`upload_slots` and `upload_bucket` are mutually exclusive. `display_files` may be combined with either.

### Attributes

| Attribute | Mode | Value | Meaning |
|---|---|---|---|
| `upload_slots` | slot | `name[:mime\|mime,...](,name...)` | Comma-separated slot list. Optional per-slot MIME allowlist after `:`, alternatives separated by `\|`. |
| `upload_bucket` | bucket | `"true"`/`"false"` | Switches to bucket mode. |
| `upload_max_count` | bucket | int > 0 | Max files per submission. |
| `upload_max_size` | bucket | int > 0 | Max bytes per file. |
| `upload_max_total_size` | bucket | int > 0 | Max total bytes across the submission. |
| `upload_mime` | bucket | `mime,mime,...` | MIME allowlist applied to every uploaded file. |
| `display_files` | any | `src-path[:label](,src-path...)` | Files (relative to the agent's task folder) to expose to the user; optional `:label` replaces the displayed filename. |

Bucket caps only apply when `upload_bucket="true"`. Slot mode and display-only use the daemon's server-wide caps.

### Staging

When the agent enters a file-bearing `<ask>`, the runtime creates `<task folder>/asks/<ask_id>/` and copies each declared display file into it (under its `:label`, or the source basename). Uploads, when they arrive, land in the same directory.

- Display sources are resolved strictly relative to the task folder. Absolute paths, `..` escapes, and symlinks out are rejected.
- A missing display source aborts the ask with an error rather than silently dropping the file.
- Staging is idempotent across daemon restarts.
- The workflow author should **not** write into `asks/<ask_id>/` before the ask runs — the directory is created at ask entry.

### Reading the result

In the state named by `next` (the post-ask state), discover what arrived using `{{ask_id}}`:

```yaml
states:
  ASK_RESUME:
    prompt: |
      Please upload your résumé.
    allowed_transitions:
      - tag: ask
        next: REVIEW
        upload_slots: "resume.pdf:application/pdf,cover.txt:text/plain"
  REVIEW:
    prompt: |
      Read the candidate's files:
      - {{task_folder}}/asks/{{ask_id}}/resume.pdf
      - {{task_folder}}/asks/{{ask_id}}/cover.txt
```

`{{ask_id}}` is bound only in this single post-ask state. If you need the ID later, write it to a file from this state (don't try to thread it through a within-workflow `input="..."` attribute — those are not template-rendered, see [the table above](#is-the-input-attribute-itself-a-template)).

### Bucket example

```
<ask next="PROCESS_IMAGES"
     upload_bucket="true"
     upload_max_count="5"
     upload_max_size="10485760"
     upload_max_total_size="52428800"
     upload_mime="image/png,image/jpeg">
Attach up to 5 PNG or JPEG images (≤10 MiB each, ≤50 MiB total).
</ask>
```

The post-ask state lists `{{task_folder}}/asks/{{ask_id}}/` to discover filenames.

### Display-only example

```
<ask next="HANDLE_REVIEW"
     display_files="out/report.pdf:Final Report,out/chart.png">
Please review the attached report and chart, then respond with your decision.
</ask>
```

## Protocol-level error handling

Rules apply to each **state output** (one Claude Code message for a markdown state, or one script's stdout):

- **Zero protocol tags** or **multiple protocol tags** in the output (outside the multi-`<fork>` exception) → protocol error.
- **Tag doesn't match `allowed_transitions`** → policy violation.

Recovery depends on whether `allowed_transitions` is declared:

- **Declared**: the orchestrator auto-generates a reminder prompt from the policy listing the permitted transitions and re-prompts (default cap: 3 attempts). Persistent failure → workflow fails. The same retry limit also applies to transient invocation errors (network, Claude Code crash).
- **Not declared**: no reminder can be generated, so the agent terminates immediately on a parse failure. Reserve this only for prototyping.

Script states cannot be re-prompted (there's no LLM). A script that prints zero or multiple tags fails the agent immediately.

## Model selection

`model` may be set:

1. **On the state itself** — the state's `model:` field (`opus` | `sonnet` | `haiku`). Highest priority.
2. **By CLI** — `ray ... --model sonnet`. Used when the state has no `model:` field.
3. **Default** — `sonnet`.

Common idiom: use `opus` for the few reasoning-heavy states (`PLAN`, `REVIEW`), and `haiku` for cheap deterministic evaluators.

## Multi-agent state

A workflow with `<fork>` may contain many live agents. The orchestrator persists a list per workflow:

```json
{
  "agents": [
    {
      "id": "main",
      "stack": [
        {"session": "session_…", "state": "RETURN.md", "scope_dir": "/wf/", "cwd": "/proj/"}
      ]
    },
    {"id": "worker-1", "stack": []}
  ]
}
```

Each agent has a stable `id`, its own stack, and its own `cwd`. `<fork>` increases the count; `<result>` with an empty stack decreases it. The workflow terminates when no live agents remain.
