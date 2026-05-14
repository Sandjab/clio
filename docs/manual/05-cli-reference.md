# CLI reference

All commands run via `uv run python -m clio <command>`. (After `uv pip install -e .`, `clio <command>` works directly too.)

## `compile` — emit a runnable project

```
clio compile <source.clio> --target <target> --output <dir>
```

| Flag | Required | Default | Notes |
|---|:-:|---|---|
| `<source.clio>` | yes | — | Path to the `.clio` file. |
| `--target` | yes | — | One of `claude-cli`, `python`, `mcp-server`, `langgraph`, `claude-skill`. |
| `--output` | yes | — | Directory to write the project into. Created if missing. **Overwrites** existing files. |
| `--flow` | no  | — | (v0.15) Select a FLOW by name when the source declares more than one. Single-FLOW files don't need it. |

**Examples:**

```bash
clio compile examples/mvp.clio --target claude-cli --output ./out
clio compile examples/mvp.clio --target python --output ./out
clio compile examples/ticket_routing.clio --target mcp-server --output ./out
clio compile examples/entities.clio --target langgraph --output ./out
```

**Errors you may see:** `ParseError` on bad syntax; `IRBuildError` on type mismatches; `ValueError` from emitter when the target rejects a feature (e.g. `FOR EACH PARALLEL` on `claude-cli`).

## `check` — validate without emitting

```
clio check <source.clio>
```

Parses + builds the IR. Exits 0 on success (silent), prints the error and exits 1 on failure.

Use this in CI to fail fast on syntax/type errors before invoking compile.

## `graph` — render the flow as a diagram

```
clio graph <source.clio> [--format mermaid|dot|html] [--output <file>]
```

| Flag | Default | Notes |
|---|---|---|
| `--format` | `mermaid` | One of `mermaid`, `dot`, `html`. |
| `--output` | stdout | Path to write the rendered diagram. If omitted, prints to stdout. |

**`mermaid`** — text suitable to paste into a GitHub README. Renders inline.

**`dot`** — Graphviz DOT source. Pipe to `dot -Tpng > graph.png`.

**`html`** — single self-contained HTML viewer with click-to-inspect cards (since v0.5.0; Tabloid-style polish since v0.6.0). `FOR EACH … PARALLEL` blocks render as a soft-tinted wrapper with a chip-pill banner astride the top border (`git-branch` icon + loop signature + `PARALLEL` kicker). Open in any browser with internet access (loads `mermaid@10` + Geist fonts from CDN).

**Replay an `events.jsonl` trace inside the viewer (since v0.9):** the toolbar exposes a "Drop events.jsonl" target. Drag-drop a trace produced by a compiled flow run (set `CLIO_LOG=1` and `CLIO_LOG_FILE=events.jsonl` to emit one — see [Environment variables](#environment-variables)) and a control bar appears with:

- **Play / pause / step-prev / step-next / restart** — walk the trace event by event.
- **Speed slider** (`0.1×` → `10×`, default `2×`) — scales the inter-event delay against the real `ts` timestamps in the file. A 30 s LLM call replays in 15 s at default speed; bump to `10×` for a sparse trace.
- **Progress strip** — `N / total` events, percentage fill, and the current event line.
- **Follow checkbox** (default on) — auto-shows the side panel of the step the trace is currently inside (driven by `step_start` events). Click any node manually to take over: auto-follow disables itself until you restart.
- **Stats** — running totals: `done`, `fail` (if any), `total` walltime in seconds derived from `step_end.duration_ms`.

Active steps pulse with a colored stroke; completed steps dim slightly; failed steps (`step_end` with `success: false`) get a red stroke. `FOR EACH PARALLEL` blocks light up multiple inner steps simultaneously, matching what `parallel_block_start` / `parallel_block_end` events bracket.

**Examples:**

```bash
clio graph flow.clio                            # mermaid to stdout
clio graph flow.clio --format dot               # dot to stdout
clio graph flow.clio --format html -o flow.html # rich viewer to file

# Generate a replay-ready trace:
CLIO_LOG=1 CLIO_LOG_FILE=events.jsonl python -m my_flow_pkg
# Then drag events.jsonl into the toolbar drop target of flow.html.
```

## `gen` — generate a `.clio` from a natural language description

Requires `pip install -e .[gen]` and `ANTHROPIC_API_KEY`.

```
clio gen "<description>"  [--output <file>] [--model <model>]
clio gen --from-file desc.txt [--output flow.clio] [--model claude-sonnet-4-6]
```

| Flag | Required | Default | Notes |
|---|:-:|---|---|
| `<description>` | one of | — | Inline description as positional arg. |
| `--from-file` | one of | — | Read description from a file instead of inline. |
| `--output` | no | stdout | Where to write the generated `.clio`. |
| `--model` | no | `claude-sonnet-4-6` | Anthropic model id. |

The generated source is **always** validated by `check` before being written. If validation fails, the LLM is asked to fix it (up to 3 retries) before falling back to printing the raw output to stderr.

## `doctor` — environment diagnostic (v0.15)

```
clio doctor [<source.clio>]
```

Checks the host before you compile or run. Without arguments: Python version,
`ANTHROPIC_API_KEY`, anthropic SDK importability. With a source file: also
compiles it and inspects `RESOURCES.mcp_servers` (commands on PATH) and
`RESOURCES.databases` (URLs parsable, env vars present). Exits **1** if any
check is FAIL, **0** otherwise.

```bash
clio doctor                                  # generic checks
clio doctor examples/critical_pipeline.clio  # plus flow-specific checks
```

## `status` — last run summary (v0.15)

```
clio status [--state-file PATH] [--log-file PATH] [--limit N]
```

Reads a `python` target's `state.json` (cwd or `CLIO_STATE_FILE`) and tails
the last N events from a `CLIO_LOG_FILE` JSONL log. Useful for "what was the
last run, where did it stop, what events did it emit" without writing custom
tooling.

```bash
clio status
clio status --state-file ./run-2026-05-14/state.json --log-file ./run-2026-05-14/log.jsonl --limit 20
```

## Environment variables

| Variable | Used by | Effect |
|---|---|---|
| `ANTHROPIC_API_KEY` | `python` target judgment steps with `protocol: anthropic` (default) | The SDK key. |
| `LITELLM_KEY` (or whatever your `auth: env:NAME` says) | `python` target with `protocol: openai` | OpenAI-compat endpoint key. |
| `CLIO_CACHE_DIR` | All targets when `CACHE: ttl(...)` is set | Override the cache directory (default: `<output>/.cache/`). |
| `CLIO_STATE_FILE` | `python` target | Override the path of `state.json` (default: `state.json` in cwd). |
| `CLIO_E2E=1` | Tests only | Unlocks gated end-to-end tests that hit real LLMs. |

## Resume a `python` target run

```
<entrypoint> --from-step N
```

Reads the persisted `state.json` (from a previous run) and starts at step N+1. Skips the first N items in the top-level chain. A `FOR EACH` (sequential or PARALLEL) counts as **one** chain item regardless of inner iterations.

Strict failure if `state.json` is missing, the recorded flow name doesn't match, or step N hasn't been completed yet.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Parse / IR / emit error |
| 2 | Source file not found |

Next: [troubleshooting](06-troubleshooting.md) for errors you may run into.
