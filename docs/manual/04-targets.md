# Compilation targets

CLIO emits a runnable project for **one of eight targets** today, selected via `--target`:

| Target | Output | Best for |
|---|---|---|
| `claude-cli` | Bash orchestrator + step files (.sh / .prompt) calling `claude -p` | Scripts, demos, "I want it to run with Claude Code in my shell" |
| `python` | Python package (Anthropic / OpenAI SDK) | Production, integration with other Python code, CI pipelines |
| `mcp-server` | Python MCP server using the official `mcp` SDK | Exposing the flow as a tool to Claude Desktop / IDE / any MCP client |
| `langgraph` | Python package whose `flow.py` builds a `langgraph.graph.StateGraph` | Bridging into LangChain ecosystems; using LangGraph's runtime features (persistence, human-in-the-loop) with CLIO-defined logic |
| `claude-skill` | Claude Code skill directory (`SKILL.md` + `scripts/` + `schemas/` + `prompts/`) | LLM-host-orchestrated skills; ship a flow as a Claude Code skill with no external runtime or API key after install |
| `go` | Go module (`flow.Run` package + `cmd/<flow>/main.go`) | Single static binary, no runtime to install; Go exact steps + Anthropic judgment |
| `swift` | Swift package (SwiftPM, zero external SPM deps, macOS + Linux) | Native Swift binary; URLSession Anthropic client; `withThrowingTaskGroup` parallel FOR EACH |
| `claude-workflow` | Claude Code Workflow script — one JS module (`export const meta` + `agent()` / `parallel()` / `phase()`) | Fan-out flows: the only target where `FOR EACH … PARALLEL` is really parallel (one concurrent subagent per item). Host-orchestrated, no API key |

The `RESOURCES.target:` field in the `.clio` source is **informational** — the `--target` flag at compile time is what actually selects the emitter.

## When to use which

### `claude-cli`

```bash
uv run python -m clio compile flow.clio --target claude-cli --output ./out
bash ./out/run.sh
```

You get a `run.sh` orchestrator + step files. Each `judgment` step is a `claude -p --model haiku` invocation against a `.prompt` template. State lives in `state.json`, threaded between steps via `jq`.

**Use when:**
- You want to demo a flow without setting up a Python env.
- You want to read each prompt as a flat file and tweak it iteratively.
- You're already in Claude Code and don't want to spin up an SDK.

**Don't use when:**
- You need `FOR EACH PARALLEL` (rejected at compile time — bash can't async safely).
- You need OpenAI / Bedrock / Vertex (only Anthropic via `claude-cli` today).
- You need precise model overrides per step (uses `RESOURCES.models` chain only).

### `python`

```bash
uv run python -m clio compile flow.clio --target python --output ./out
uv pip install ./out
ANTHROPIC_API_KEY=... my_flow_name
```

You get a clean Python package with `pyproject.toml`, `flow.py` orchestrator, `contracts.py` (Pydantic models), and one `steps/<name>.py` per step.

**Use when:**
- You want to integrate the flow into existing Python code.
- You need OpenAI-compat (LiteLLM, vLLM, Ollama, OpenRouter, Together, Groq).
- You need `FOR EACH PARALLEL` with `concurrent.futures.ThreadPoolExecutor`.
- You need step-granularity resume (`--from-step N` reads the persisted `state.json`).

**Don't use when:**
- You don't want a Python toolchain in the loop. Pick `claude-cli`.

### `mcp-server`

```bash
uv run python -m clio compile flow.clio --target mcp-server --output ./out
uv pip install ./out
```

You get a Python MCP server that registers each `FLOW` as a tool. Judgment steps delegate to the MCP client via `sampling/createMessage` — **no API key on the server**, no `anthropic`/`openai` SDK dep.

> **Tool-surface check (v0.18):** the compile-time error [`E_MCP_001`](06-troubleshooting.md#e_mcp_001) (requires at least one `EXPOSE FLOW` in the entry file) **only fires when the source declares `RESOURCES.target: mcp-server` explicitly**. When the target is set only via the CLI `--target mcp-server` flag on a source without `RESOURCES`, the check is bypassed: a **single-FLOW** source auto-exposes its only FLOW as the tool (backward-compat / convenience); a **multi-FLOW** source without any `EXPOSE` produces a server with an **empty tool list**. To benefit from the check, declare `RESOURCES.target` in the source.

**Use when:**
- You want to expose your flow as a tool inside Claude Desktop / Cursor / any MCP client.
- You don't want to manage API keys (the client provides the LLM access).
- You want the flow callable from another agentic system without re-implementing it.

**Don't use when:**
- You need `protocol: bedrock` or `protocol: vertex` (rejected at compile time — point your client at MCP sampling instead).
- You need `invoke.mode: cli` (Claude CLI is per-machine; MCP clients hold the LLM access).

### `langgraph`

```bash
uv run python -m clio compile flow.clio --target langgraph --output ./out
uv pip install ./out
ANTHROPIC_API_KEY=... my_flow_name --kwargs '{"file": "input.txt"}'
```

You get a Python package whose `flow.py` builds a `langgraph.graph.StateGraph`:

```python
from <pkg>.flow import build_graph, run

app = build_graph()                        # the compiled StateGraph
state = app.invoke({"file": "input.txt"})  # or just: run(file="input.txt")
```

Each `STEP` becomes a node function `(state: State) -> dict`. The State is a `TypedDict` aggregating all TAKES and GIVES across the flow. `retry(N)` from `ON_FAIL` translates to a `RetryPolicy(max_attempts=N)` on the corresponding `add_node` call. Step files themselves are reused verbatim from the python target — only the orchestrator changes.

**Use when:**
- You're already in a LangChain/LangGraph stack and want CLIO-defined logic to fit native.
- You need LangGraph's runtime features (persistence layer, human-in-the-loop, event streaming) on top of a CLIO-described pipeline.

**Don't use when (v0):**
- You need `FOR EACH` (any kind) — rejected at compile time. Send-API support is planned (not yet shipped). Use `--target python` today.
- You need `WHILE` — rejected at compile time (cyclic edges + state reducers planned, not yet shipped). Use `--target python` or `--target mcp-server`.
- You need `impl.mode: sql` — rejected at compile time in v0.11. Use `--target python` or `--target mcp-server`.
- You need `invoke.api.openai/bedrock/vertex` — only `anthropic` is wired in v0. Use `--target python`.
- You need `invoke.mode: cli` — LangGraph runs server-side. Use `--target claude-cli`.
- You need `ON_FAIL escalate` or `fallback(<step>)` — only `retry(N)` and `abort(...)` are wired in v0. Use `--target python` for the full retry chain.

### `claude-skill`

```bash
uv run python -m clio compile flow.clio --target claude-skill --output ./skill-out
cp -r ./skill-out ~/.claude/skills/my-skill
```

You get a Claude Code skill directory: a `SKILL.md` orchestration manifest (with a TodoWrite checklist the host follows), per-step `scripts/NN_<name>.py` Python stubs you fill in for `exact` steps, `prompts/NN_<name>.md` templates for `judgment` steps, and `schemas/*.json` for typed inputs/outputs. Bundled helpers (`_validate.py`, `_cache_key.py`) ship inside `scripts/`.

The emitted skill is **LLM-host-orchestrated**: Claude Code reads `SKILL.md` and drives the flow — no `anthropic` SDK install, no API key, no CLIO binary after install. Exact steps are real Python scripts the host invokes; judgment steps are inline LLM calls the host produces.

**Use when:**
- You want to package a flow as a Claude Code skill that another user can install without setting up a Python environment, an API key, or any external service.
- You want the LLM host (Claude Code) to drive the orchestration directly, reading the step list from `SKILL.md`.
- The flow is small enough that LLM-host fidelity is acceptable (the TodoWrite checklist is the main drift anchor).

**Don't use when:**
- You need parallelism — `FOR EACH ... PARALLEL` is serialised in the emitted skill (the host doesn't execute concurrently). Use `--target python` or `--target mcp-server`.
- You need a language other than Python or Bash for `exact` steps — only `python` and `bash` are supported in v1 (a `LANG: ruby` step is a compile-time error).
- You need a runtime entrypoint outside Claude Code (Python entry point, CLI command, MCP tool). Pick `python` / `mcp-server` instead.

> **`.clio/` sidecar (v0.19):** every `claude-skill` emission also writes
> `<skill>/.clio/source.clio` (verbatim copy of the source) and
> `<skill>/.clio/manifest.json` (CLIO version, emission timestamp, per-file
> SHA-256 hashes). The sidecar is what makes [`clio import`](05-cli-reference.md#import--recover-a-clio-from-a-claude-code-skill-v019)
> a byte-identical round-trip when nothing in the skill has drifted; if a
> hash no longer matches, `clio import` falls back to LLM-assisted recovery
> (or exits 2 under `--mode strict`). Sidecar emission is best-effort — a
> write failure is logged to stderr but never blocks the main skill output.

### `go`

```bash
uv run python -m clio compile flow.clio --target go --output ./go-out
cd go-out && go mod tidy && go run ./cmd/<flow_name> --kwargs '{"file": "input.txt"}'
```

Or build a single static binary:

```bash
cd go-out && go build -o my_flow ./cmd/<flow_name>
./my_flow --kwargs '{"file": "input.txt"}'
```

You get a `go.mod`, a `contracts/` package (Go structs with `json` tags), `steps/<name>/<name>.go` files (stubs for exact steps, auto-generated for judgment steps), a `flow/flow.go` orchestrator, and a `cmd/<flow>/main.go` CLI entry point.

**Use when:**

- You want a single static binary with no Python interpreter to ship or install.
- Your exact steps are in Go (or `LANG: auto`) and you want the orchestrator to match.
- You need concurrent iteration (`FOR EACH PARALLEL`) backed by goroutines and `errgroup`.

**Don't use when (v0.23):**

- You need OpenAI / Bedrock / Vertex (only `invoke.api.anthropic` is wired — E_GO_005, E_GO_003).
- You need `impl.mode: sql / mcp_tool` (deferred — E_GO_009/010, tracked for v0.24).
- You need a multi-GIVES sub-flow as a `FOR EACH PARALLEL` body (E_GO_006 — single-GIVES parallel and all sequential composition are supported).
- You need `--from-step N` resume (not implemented — the Go binary re-runs the full flow without error).
- You need structured JSONL logging (`CLIO_LOG=1`) — silent no-op; use `--target python`.

See [`docs/COMPILATION_TARGETS.md`](../COMPILATION_TARGETS.md#target-go) for the full layout and refused-combo table.

### `swift`

```bash
uv run python -m clio compile flow.clio --target swift --output ./swift-out
cd swift-out && swift build
.build/debug/<flow_name> --kwargs '{"file": "input.txt"}'
```

Or build a release binary:

```bash
cd swift-out && swift build -c release
.build/release/<flow_name> --kwargs '{"file": "input.txt"}'
```

You get a **two-target** `Package.swift` (a library `ClioFlow` plus an executable named after the flow). `Sources/<flow_name>/` holds just `Main.swift` (the `@main` entry that parses `--kwargs` and calls `Flow.run(kwargs:)`); the library target `Sources/ClioFlow/` holds `Flow.swift` (the `public enum Flow` orchestrator), `Contracts.swift`, the per-step files under `Steps/`, and a `Runtime/` directory with zero-dependency pure-Swift implementations of the Anthropic URLSession client, JSON Schema validator, and SHA256-keyed cache. `Contracts.swift` and the `Runtime/*` files are emitted only when the flow needs them (contracts / judgment / cache respectively).

**Use when:**

- You want a native Swift binary on macOS or Linux with no Python interpreter, no Go toolchain, and no external SPM package dependencies.
- Your exact steps are in Swift (or `LANG: auto`) and you want the orchestrator to match.
- You need concurrent iteration (`FOR EACH PARALLEL`) backed by `withThrowingTaskGroup`.

**Don't use when (MVP scope):**

- You need `impl.mode: rest` or `impl.mode: shell` — deferred to Phase 4; use `--target python` or `--target go`.
- You need FLOW composition (sub-flow calls) or `RESCUE`/`RESUME` — deferred to Phase 5; use `--target python` or `--target go`.
- You need OpenAI / Bedrock / Vertex (only `invoke.api.anthropic` is wired — E_SWIFT_005, E_SWIFT_003).
- You need `impl.mode: sql` or `impl.mode: mcp_tool` — deferred (E_SWIFT_009/010).
- You need a multi-GIVES sub-flow as a `FOR EACH PARALLEL` body — this needs FLOW composition (Phase 5) first, so the `E_SWIFT_006` code reserved for it is dormant in the MVP. Single-GIVES parallel bodies and all sequential FOR EACH are supported.
- You need `--from-step N` resume (not implemented — the Swift binary re-runs the full flow; use `--target python`).

See [`docs/COMPILATION_TARGETS.md`](../COMPILATION_TARGETS.md#target-swift) for the full layout and refused-combo table.

### `claude-workflow`

```bash
uv run python -m clio compile examples/parallel_review.clio --target claude-workflow --output ./wf-out
cp ./wf-out/parallel-review.workflow.js .claude/workflows/
# then run the workflow from Claude Code
```

You get a **single JS module**: `export const meta` (name, description, phases), one `async function` per step, and the flow body at the bottom threading a `state` object between them. A `judgment` step becomes `await agent(prompt, {label, phase, schema})` — a Claude Code **subagent**, forced through the step's `GIVES` schema by the host. An `exact` step becomes a **pure-JS stub that throws until you fill it in**. A `.clio/` sidecar ships beside the script, so `clio import` recovers the source verbatim.

Like `claude-cli` and `claude-skill`, it is **host-orchestrated**: the Claude Code session *is* the runtime, so there is **no API key** and no package to install.

**Use when:**

- Your flow **fans out**. `FOR EACH … PARALLEL` compiles to `parallel()` over concurrent subagents — this is the only target where it is really parallel (`claude-skill` serialises it with a warning, `claude-cli` rejects it).
- You want the flow to run *inside* Claude Code, with no runtime and no key.
- Your `exact` steps are pure transforms (parse, map, filter, aggregate) you are happy to write in JavaScript.

**Don't use when:**

- Any `exact` step does **IO** — `impl.mode: shell / rest / sql / mcp_tool` is refused (`E_WF_003`): the workflow sandbox has no process, no network and no filesystem. Use `--target python / go / swift`.
- Your `exact` bodies are not JavaScript — an explicit `LANG:` other than `node` / `auto` is refused (`E_WF_004`).
- You need a non-Anthropic provider — `invoke.api.openai / bedrock / vertex` is refused (`E_WF_002`); an `agent()` cannot call them.
- You depend on `CACHE:` (**ignored**, `W_WF_001` — no filesystem, no clock), on retry **backoff** (`ON_FAIL` retries run back-to-back, `W_WF_002` — `Date.now()` throws in the sandbox), or on `CONTRACT … ASSERT` (**not enforced**, `W_WF_003` — the host validates the JSON Schema, not the predicate).

Two more refusals worth knowing: a source with several `FLOW`s needs `--flow <name>` (`E_WF_006` — this target emits exactly one script and will not silently drop the others), and a recursive `FLOW` is rejected (`E_WF_007` — sub-flows are *inlined*, so recursion would overflow the stack at run time).

See [`docs/COMPILATION_TARGETS.md`](../COMPILATION_TARGETS.md#target-claude-workflow) for the full layout and refused-combo table.

## Cross-target feature support

| Feature | claude-cli | python | mcp-server | langgraph | claude-skill | go | swift | claude-workflow |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `MODE: exact` (code stub) | ✅ | ✅ | ✅ | ✅ | ✅ (`scripts/NN.py` stub) | ✅ (Go stub) | ✅ (Swift stub) | ✅ (pure-JS stub; throws until filled) |
| `MODE: exact` + `LANG: swift / auto` | ⚠️ LANG ignored | ✅ | ✅ | ✅ | ✅ (Python or Bash only) | ❌ E_GO_001 | ✅ | `auto` ✅ / `swift` ❌ E_WF_004 |
| `MODE: exact` + `LANG: go / auto` | ⚠️ LANG ignored (always Python stub) | ✅ | ✅ | ✅ | ✅ (Python or Bash only) | ✅ | ❌ E_SWIFT_001 | `auto` ✅ / `go` ❌ E_WF_004 |
| `MODE: exact` + `LANG: python / bash / rust / node` | ⚠️ LANG ignored (always Python stub) | ✅ | ✅ | ✅ | ✅ (Python or Bash only) | ❌ E_GO_001 | ❌ E_SWIFT_001 | `node` ✅ / others ❌ E_WF_004 |
| `MODE: exact` + `impl.shell` | ✅ | ✅ | ✅ | ✅ | ✅ (Python or Bash only) | ✅ os/exec | ❌ Phase 4 | ❌ E_WF_003 (no process) |
| `MODE: exact` + `impl.shell` + `parse: json` | ⚠️ silently ignored | ✅ | ✅ | ✅ | ✅ | ✅ json.Unmarshal | ❌ Phase 4 | ❌ E_WF_003 |
| `MODE: exact` + `impl.rest` | ✅ (uses `requests` at runtime) | ✅ | ✅ | ✅ | ✅ | ✅ net/http + retry (json/raw bodies only; form/file/multipart → E_GO_013) | ❌ Phase 4 | ❌ E_WF_003 (no network) |
| `MODE: judgment` + `invoke: cli` (default) | ✅ | ❌ rejected | ❌ rejected | ❌ rejected | ✅ host-driven | ❌ E_GO_002 | ❌ E_SWIFT_002 | ✅ host-driven (`agent()` subagent) |
| `MODE: judgment` + `invoke.api.anthropic` | (uses `RESOURCES.models` chain) | ✅ | ❌ rejected | ✅ | ✅ host-driven | ✅ | ✅ URLSession | ✅ model → `opus`/`sonnet`/`haiku` tier |
| `MODE: judgment` + `invoke.api.openai` | ❌ | ✅ | ❌ | ❌ rejected (v0) | ✅ host-driven | ❌ E_GO_005 | ❌ E_SWIFT_005 | ❌ E_WF_002 |
| `MODE: judgment` + `invoke.api.bedrock`/`vertex` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ E_GO_003 | ❌ E_SWIFT_003 | ❌ E_WF_002 |
| `CACHE: ttl(...)` | ✅ | ✅ | ✅ | ✅ (reuses python runtime) | ⚠️ documented in `SKILL.md`; helper bundled | ✅ | ✅ (keys byte-identical with python + go) | ⚠️ **ignored** — W_WF_001 (no filesystem, no clock) |
| `ON_FAIL: retry(N)` | ✅ | ✅ | ✅ | ✅ via `RetryPolicy` | ⚠️ documented in `SKILL.md` (host-followed) | ✅ | ✅ (exponential backoff via `Task.sleep`) | ⚠️ retries, but **no backoff** — W_WF_002 (no clock) |
| `ON_FAIL: escalate / fallback` | ✅ | ✅ | ✅ minimum-compliance | ❌ rejected (v0) | ⚠️ documented in `SKILL.md` | ✅ | ✅ fallback; escalate no-op | ✅ fallback; escalate no-op |
| `ON_FAIL: abort` | ✅ | ✅ | ✅ | ✅ | ⚠️ documented in `SKILL.md` | ✅ | ✅ | ✅ |
| `RESCUE` + `step.error.*` + `RESUME` | ❌ rejected | ✅ | ✅ | ❌ rejected | ⚠️ documented in `SKILL.md` | ✅ | ❌ Phase 5 | ✅ `try` / `catch` |
| `FOR EACH` (sequential) | ✅ | ✅ | ✅ | ❌ rejected (v0; v0.7) | ✅ | ✅ | ✅ | ✅ `for…of` |
| `FOR EACH ... PARALLEL AS` | ❌ rejected | ✅ ThreadPool | ✅ asyncio.gather | ❌ rejected (v0; v0.7 via Send) | ⚠️ serialised with warning | ✅ errgroup | ✅ withThrowingTaskGroup (cap 10) | ✅ **`parallel()` — one concurrent subagent per item** |
| `FLOW.TAKES` / `FLOW.GIVES` (v0.16, optional) | ✅ README section | ✅ typed `run()` | ✅ inputSchema / outputSchema | ✅ State subset | ✅ SKILL.md Inputs / Outputs | ✅ typed `Run()` | ✅ typed `Flow.run(kwargs:)` | ⚠️ TAKES → `args`, presence-checked; the entry flow's GIVES stays in `state` (nothing is returned) |
| **FLOW composition** (sub-flow callable, v0.17) | ❌ rejected | ✅ `run_<name>()` | ✅ + multi-tool | ✅ sub-`StateGraph` | ⚠️ documented in SKILL.md (linear-only, `scripts/sub_<name>.py`) | ✅ `run<Name>()` func | ❌ Phase 5 | ✅ **inlined** as a local `async function` (recursion → E_WF_007) |
| `FOR EACH PARALLEL` body = sub-flow (v0.17) | ❌ rejected | ✅ | ✅ asyncio.gather | ❌ rejected (v0; v0.7 via Send) | ⚠️ linear sub-flow only | ✅ single-GIVES, terminal-only (typed downstream consumption of the collector → v0.24; multi-GIVES → E_GO_006) | ❌ Phase 5 (needs FLOW composition; `E_SWIFT_006` reserved/dormant until then) | ✅ `parallel()` — the collector holds the sub-flow's **GIVES objects**, not bare values |
| mcp-server multi-tool (multi-FLOW source, v0.17) | n/a | n/a | ✅ one tool per uncalled signed FLOW | n/a | n/a | n/a | n/a | n/a (one script per FLOW; `--flow` required → E_WF_006) |
| `TEST` blocks (v0.15) | ⚠️ ignored | ✅ pytest emitted | ⚠️ ignored | ⚠️ ignored | ⚠️ ignored | ❌ E_GO_012 | ❌ E_SWIFT_012 | ⚠️ ignored |
| `--from-step N` resume | ❌ | ✅ | ❌ | ❌ (use LangGraph checkpointers) | ❌ | ⚠️ not implemented (re-runs full flow) | ⚠️ not implemented (re-runs full flow) | ❌ |
| JSONL logging (`CLIO_LOG=1`) | ❌ | ✅ | ✅ | ⏸ delegated to LangSmith | ❌ | ⏸ silent no-op | ❌ not emitted | ❌ not emitted (the host shows phases + subagents) |
| `clio graph --format html` | n/a (graph is target-independent) | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

On `claude-workflow`, `CONTRACT … ASSERT` is **not enforced** (`W_WF_003`): the emitted schema is handed to the subagent and the host validates types, ranges and enums against it, but the `ASSERT` predicate is dropped. The compiler says so at compile time, once per contract that declares one.

## A common workflow: `python` for production, `claude-cli` for sketches

A `.clio` file is target-independent (modulo the limitations above). A common pattern:

1. **Sketch** the flow with `--target claude-cli`. Read the emitted `.prompt` files, tune the wording.
2. **Test** at scale with `--target python` once the prompts are stable.
3. **Distribute** as `--target mcp-server` if you want it consumable by other AI clients.
4. **Bridge** to `--target langgraph` if you need to plug into LangChain runtime features (checkpointers, human-in-the-loop, streaming). Subset features today, full parity is planned (not yet shipped).
5. **Ship as a Claude Code skill** with `--target claude-skill` when the audience is Claude Code users who want a zero-runtime install (no API key, no Python env).
6. **Fan out inside Claude Code** with `--target claude-workflow` when the flow's cost is in a `FOR EACH … PARALLEL`: there, and only there, the iterations run as concurrent subagents.

The same source compiles all eight (within each target's scope).

Next: [CLI reference](05-cli-reference.md) for every command and flag.
