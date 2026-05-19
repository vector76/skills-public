# CLI Reference

Running, linting, and diagramming a `.yaml` workflow. For schema and tag semantics see [schema.md](schema.md) and [transitions.md](transitions.md).

## Run a workflow

```bash
# Start at the workflow's entry point (1_START or START)
ray workflow.yaml

# Start at a specific state (bare name, no extension)
ray workflow.yaml/REVIEW

# Pass an initial input — appears in the first state as {{input}}
ray workflow.yaml --input "review this PR"

# Pipe input
cat data.json | ray workflow.yaml --input "$(cat)"
```

The `workflow.yaml/STATE` syntax is **not** a filesystem path. The CLI splits on the `.yaml`/`.yml` extension to separate the scope from the initial-state name. The bare state name is resolved to the appropriate virtual filename (`REVIEW.md` for a markdown state, `REVIEW.sh` for a script state, etc.).

### Entry-point resolution

When no initial state is given:

1. Use the state named `1_START` if present.
2. Otherwise use `START`.
3. If both exist → ambiguity error.
4. If neither exists → error.

## Common flags

| Flag | Default | Meaning |
|---|---|---|
| `--input "..."` | — | Initial `{{input}}` for the first state. |
| `--model {opus\|sonnet\|haiku}` | `sonnet` | Default model for states that don't set `model:`. |
| `--effort {low\|medium\|high}` | — | Default effort (extended-thinking) level for states that don't set `effort:`. |
| `--budget <USD>` | `0` (unlimited) | Cost budget for the run. |
| `--timeout <sec>` | `600` | Idle timeout per invocation in seconds. States may override with their own `timeout:`. `0` disables. |
| `--on-ask {reject\|pause}` | `reject` | Behaviour when the workflow emits `<ask>`. `reject` fails immediately (safe for CI). `pause` writes a JSON pending-input record to stdout and exits with code 2; resume to deliver. |
| `--workflow-id <id>` | auto | Override the generated workflow run ID. |
| `--name <prefix>` | — | Terminal-titlebar prefix. |
| `--quiet` / `--verbose` | — | Adjust progress output. |
| `--continue-session` | false | Continue from the most recent interactive Claude session. |
| `--no-wait` | false | Don't wait for usage-limit reset; pause and exit immediately. |
| `--dangerously-skip-permissions` | — | Pass through to Claude Code. |

## Resume

```bash
ray --resume <workflow-id>

# Deliver pending <ask> input on resume
ray --resume <workflow-id> --input "approve"
```

On resume the YAML file is re-parsed and re-validated. If the file has been deleted or now fails validation, resume fails with a clear error.

## List, status, recover

```bash
ray --list                  # list workflow state files
ray --status <id>           # show status for a workflow
ray --recover               # list in-progress workflows
```

## Lint

```bash
ray lint workflow.yaml
ray lint --json --level error workflow.yaml
ray lint --level info workflow.yaml             # include info notices
```

Run after every edit — the cost is zero and it catches most authoring mistakes before a single LLM call. The most useful checks:

| Severity | Check | Catches |
|---|---|---|
| Error | `no-entry-point` / `ambiguous-entry-point` | Missing or conflicting `1_START`/`START`. |
| Error | `missing-target` / `missing-return` / `missing-fork-next` / `missing-ask-next` | A transition references a state that doesn't exist, or omits a required attribute. |
| Error | `frontmatter-parse-error` | The `allowed_transitions` (or other policy) failed to parse. |
| Error | `invalid-model` / `invalid-effort` / `invalid-task-value` | Out-of-range field value (e.g. `model: claude-3`, `task: refactor`). |
| Error | `unsupported-task-attribute` / `unsupported-cd-attribute` | A tag has an attribute it doesn't accept (e.g. `cd` on `<goto>`). |
| Warning | `fork-next-mismatch` | Multiple `<fork>` tags in one output disagree on `next`; all must share the same continuation state. |
| Warning | `unused-allowed-transition` | A state with 2+ allowed transitions has a target not mentioned in the prompt body — the prompt probably forgot to instruct the model which tag to emit. |
| Warning | `unreachable-state` | No path from the entry point reaches this state. |
| Warning | `dead-end-state` | State has no outbound transitions. |
| Warning | `call-without-result-path` | A `<call>` target has no `<result>` reachable from it. |
| Info | `implicit-transition` | Single-entry policy means the model can omit the tag. |

`--level error|warning|info` filters by severity (default: warning).

## Diagram

```bash
ray diagram workflow.yaml                    # Mermaid flowchart to stdout
ray diagram workflow.yaml --html --output diagram.html
```

Use the HTML form for hover details and styling; useful for reviewing the FSM before running anything.

## `<ask>` exit code protocol

When `--on-ask=pause` and the workflow emits an `<ask>`:

- Raymond quiesces all live agents.
- Writes a JSON record describing the pending input(s) to stdout.
- Exits with code **2**.

To deliver a response:

```bash
ray --resume <id> --input "the human's response"
```

For interactive use, prefer running under `ray serve` (HTTP / MCP / web UI) — the daemon delivers `<ask>` input through its own channels.

## Initial config

```bash
ray --init-config
```

Creates a template `.raymond/config.toml` in the current directory.

## Daemon mode

`ray serve --root <dir>` discovers all `.yaml` workflows under `<dir>` that include a top-level `id:` field (see [schema.md](schema.md#manifest-fields-optional-daemon-only)) and exposes them via:

- HTTP API (`POST /runs`, `POST /runs/{id}/asks/{ask_id}`, …)
- MCP tools (one tool per workflow)
- Web UI

Manifest fields like `description`, `default_budget`, `input.label`, and `requires_human_input` shape how each workflow is presented to callers. They have no effect on direct CLI runs.

## Tips

- Run `ray lint workflow.yaml` after each edit. The cost is zero and it catches most footguns.
- When iterating on a single state, use `ray workflow.yaml/STATE_NAME` to start partway in — combined with `--input "..."` for the value that would have arrived from the predecessor.
- Set `model: haiku` and `effort: low` on cheap evaluator states; reserve `opus` for the few states that need it.
- Use `ray diagram workflow.yaml --html` whenever the transition graph gets non-trivial — eyeballing the FSM is faster than reading the YAML.
