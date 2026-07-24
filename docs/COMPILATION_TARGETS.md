# CLIO Compilation Targets

Each target is an emitter module that transforms the IR graph into a runnable project. This document describes what each target emits and the constraints it operates under.

## Targets at a glance

| Target | Status | Output | Why / Use case | IMPORT (v0.18) | Effort |
|---|---|---|---|---|---|
| `claude-cli` | Implemented | Claude Code project (bash + `claude -p` subprocess) | Prototype + reference target | ❌ (deferred) | — |
| `python` | Implemented | Python package (Anthropic SDK + Pydantic v2) | Production-grade Python deployment | ✅ | — |
| `claude-skill` | Implemented | Claude Code skill directory (`SKILL.md` + `scripts/` + `schemas/` + `prompts/`) | Turn a `.clio` into an LLM-host-orchestrated skill; no external runtime or API key needed after install | ✅ | — |
| `mcp-server` | Implemented | MCP server, each FLOW exposed as a tool with sampling-based judgment | Native Anthropic ecosystem integration; turn a `.clio` into a structured MCP tool | ✅ | — |
| `langgraph` | Implemented | LangGraph graph (nodes = STEPs, state = CONTRACTs) | Adoption by existing LangChain users; positions CLIO as a meta-language | ✅ | — |
| `local` | Future | Same as `python`, with Ollama/vLLM | Offline / data-privacy constraints | ✅ (planned) | High (Outlines/Guidance) |
| `rust` | Future | Cargo async project | Performance-critical `exact` steps | planned | High |
| `go` | Implemented | Go module (package `flow.Run` + `cmd/<flow>/main.go`) | Single static binary, no runtime to install; concurrent `exact` steps via goroutines | ✅ | — |
| `swift` | Implemented | Swift package (SwiftPM); zero external SPM dependencies | Native Swift binary (macOS + Linux), URLSession Anthropic client, `withThrowingTaskGroup` parallel FOR EACH | ❌ (deferred) | — |
| `claude-workflow` | Implemented | Claude Code Workflow script — one JS module (`export const meta` + `agent()` / `parallel()` / `phase()`) | The only target where `FOR EACH … PARALLEL` really is parallel: each iteration is a concurrent subagent. Host-orchestrated — no API key, no runtime | ✅ | — |
| `docker` | Future | Multi-stage Dockerfile + compose | Mixed-language flows | planned | Medium |
| `hybrid` | Future | Claude CLI + precompiled binaries for `exact` | Heavy `exact` within CLI orchestration | planned | Medium |
| `fastapi` | Candidate | HTTP server (FLOW = endpoint, CONTRACT = `response_model`) | Deploy a `.clio` as a microservice | planned | Low–Medium |
| `temporal` | Candidate | Temporal workflow (Python/Go), STEPs = activities | Enterprise durability, retry, observability — maps 1:1 onto `ON_FAIL` semantics | planned | Medium–High |
| `typescript` | Candidate | TS/Node package with `@anthropic-ai/sdk` | Frontend / Vercel / edge audiences | planned | Medium |
| `dspy` | Candidate | DSPy signatures + composed module | Research-oriented audiences | planned | Medium |
| `modal` | Candidate | Python with `@modal.function` decorators | Frictionless cloud deployment | planned | Low |
| `step-functions` | Candidate | AWS States Language JSON + Bedrock integration | AWS-native enterprise | planned | Medium–High |
| `jupyter` | Candidate | Notebook with one cell per STEP | Exploration / demo / pedagogy | planned | Low |

**Status legend**:
- *Implemented* — emitter shipped, tests green.
- *Future* — designed in this document, not yet built. See dedicated sections below.
- *Candidate* — rationale captured here, no formal design yet, pending a decision to invest.

---

## `target: claude-cli` (Milestone 1)

**What it emits**: a Claude Code project folder.

**Runtime dependency**: Claude Code CLI (`claude` command).

| IR element        | Emitted artifact                                  |
|-------------------|---------------------------------------------------|
| STEP `exact`      | `steps/NN_name.py` (Python script with argparse + `state.json`) |
| STEP `judgment`   | `steps/NN_name.prompt` + `steps/NN_name.schema.json` |
| CONTRACT          | JSON Schema file + validation hook in `.claude/hooks.json` |
| FLOW              | `run.sh` — bash orchestrator                      |
| WHILE loop        | ❌ not supported (refused at compile time — sequential chains only) |
| FOR EACH          | bash `for` loop + `claude -p` or `xargs`          |
| MATCH/CASE        | ❌ not supported (refused at compile time)                 |
| IF/ELSE           | ❌ not supported (refused at compile time)                 |
| ON_FAIL/fallback  | `||` operator or trap                              |
| RESOURCES         | `CLAUDE.md` header + CLI flags in `run.sh`         |
| CACHE             | `.cache/` dir, SHA256 hash check before API calls   |

**State passing**: between steps, state is serialized as JSON to a `state.json` file. Each step reads its input from state, writes its output back.

**Judgment steps**: the `.prompt` file is a template with `{{variable}}` placeholders. `run.sh` substitutes variables from state before piping to `claude -p`.

**Contract validation**: hooks in `.claude/hooks.json` run a validation script after each judgment step. Validation is a simple `python -m jsonschema` call against the emitted `.schema.json` — no external lib beyond the stdlib-adjacent `jsonschema` package. If validation fails, the hook triggers the ON_FAIL strategy.

**Logging**: not instrumented. Use `--target python` or
`--target mcp-server` for observable runs.

---

## `target: python`

Produces a runnable Python package depending on `anthropic` and `pydantic`.

### Layout

```
output/
  pyproject.toml
  README.md
  <pkg>/
    __init__.py
    contracts.py        # Pydantic v2 BaseModel per CONTRACT
    flow.py             # orchestrator: calls steps in chain order
    __main__.py         # CLI: `python -m <pkg>`
    steps/
      <exact>.py        # NotImplementedError stub (user fills body)
      <judgment>.py     # auto-generated: SDK + cache + ON_FAIL chain
    clio_runtime/
      cache.py          # copied verbatim from clio/runtime/cache.py
```

### Use

```bash
pip install -e ./output
python -m <pkg> --kwargs '{"file": "customers.csv"}'
```

Or programmatically:

```python
from <pkg>.flow import run
result = run(file="customers.csv")
```

- **FOR EACH PARALLEL:** supported via `concurrent.futures.ThreadPoolExecutor` (cap = 10).

### Cache layout interchangeable with `claude-cli`

Both targets read/write `<output>/.cache/<step_name>/<sha256>.json` with the same key derivation (SHA256 of `step + model + prompt + schema`). Switching targets between runs preserves cache hits.

### Model name mapping

`RESOURCES.models` short names map to Anthropic SDK full model IDs at emit time:

| CLIO short | Anthropic ID |
|------------|--------------|
| `haiku` | `claude-haiku-4-5-20251001` |
| `sonnet` | `claude-sonnet-4-6` |
| `opus` | `claude-opus-4-7` |

### System prompt

Each judgment step's SDK call sends a strict JSON-only system prompt that aligns the model's behavior with `claude -p`'s built-in scaffolding. Ensures contract validation succeeds reliably.

**Logging** (since v0.4): structured JSONL events via `CLIO_LOG=1`. Six event types
covering `flow_start`/`flow_end`, `step_start`/`step_end` (3 paths for judgment),
`parallel_block_start`/`parallel_block_end`. Tokens extracted from
`response.usage` (Anthropic `input_tokens`/`output_tokens`, OpenAI
`prompt_tokens`/`completion_tokens`).

**Resume** (since v0.4): emitted package writes `state.json` atomically after
each top-level chain item; `python -m my_pkg --from-step N` reloads the
state and skips items 1..N. Path via `CLIO_STATE_FILE` env var.

---

## `target: mcp-server`

Produces a runnable MCP (Model Context Protocol) server. Each `EXPOSE FLOW` in
the entry file (v0.18+) becomes a tool registered with the official `mcp` Python
SDK. The entry file must expose at least one FLOW (E_MCP_001; this guard fires only when `target: mcp-server` is declared in the source — it is bypassed when the target is selected via the `--target mcp-server` flag at compile time). Prior to v0.18,
every signed FLOW not called by a sibling was implicitly exposed — sources relying
on that heuristic must be migrated (see `docs/manual/06-migration-v018.md`).
Judgment steps are handled by the MCP client via `sampling/createMessage` — the
server itself carries no API key and no `anthropic`/`openai` dependency.

### Layout

```
output/
  pyproject.toml
  README.md                  # includes client-config snippet (stdio + args)
  <pkg>/
    __init__.py
    contracts.py             # Pydantic v2 BaseModel per CONTRACT
    server.py                # MCP server entry point; registers one tool per FLOW
    __main__.py              # CLI: `python -m <pkg>` (starts the MCP server)
    steps/
      <exact>.py             # NotImplementedError stub (user fills body)
      <judgment>.py          # auto-generated: sampling/createMessage + cache + ON_FAIL chain
    clio_runtime/
      cache.py               # copied verbatim from clio/runtime/cache.py
```

### Use

```bash
pip install -e ./output
python -m <pkg>              # starts the MCP server on stdio
```

Then add it to your MCP client config (Claude Desktop, Claude Code, etc.) — see the emitted `README.md` for the exact snippet.

### Sampling differentiator

Judgment steps use `sampling/createMessage` instead of a direct SDK call. The MCP client (e.g. Claude Desktop) executes the LLM call with its own credentials — the server never sees an API key. This makes the emitted package safe to ship as a tool: no credential management, no API-key rotation, no SDK version pinning.

### inputSchema / outputSchema derivation

- **inputSchema**: when the FLOW declares `TAKES:` (v0.16+), it is the source of truth. Otherwise derived from the first step's `TAKES`. Literal kwargs in the FLOW call become `default` values in the JSON Schema; required fields are those with no default.
- **outputSchema**: when the FLOW declares `GIVES:` (v0.16+), it is the source of truth. Otherwise derived from the last step's `GIVES`. Inline types and CONTRACT refs both resolve to JSON Schema objects.

### Refused combinations

The following are rejected at compile time with a clear error and a pointer to `--target python`:

- `invoke.protocol: anthropic` — use `--target python` for direct SDK calls.
- `invoke.protocol: openai` — use `--target python`.
- `invoke.protocol: bedrock` / `vertex` — use `--target python`.
- `invoke.mode: cli` — no `claude -p` subprocess on an MCP server.
- Source with no `FLOW` declaration — rejected at compile time with a `ValueError`.

### Inherited features

These work identically to the `python` target (shared helpers in `_python_helpers.py`):

- `FOR EACH ... IN ...:` — emits `for var in state[...]:` with body step calls.
- **FOR EACH PARALLEL:** supported via `asyncio.gather` + `Semaphore(10)`. Judgment steps thread the MCP session per task.
- `CACHE: ttl(...)` — same on-disk layout as `python` and `claude-cli`; cache files are interchangeable.
- `ON_FAIL: retry / escalate / fallback / abort` — full strategy chain.
- `impl.mode: rest` — emits `requests.request(...)` with `${var}` URL templating.
- `impl.mode: shell` — emits `subprocess.run([...], shell=False)`.

**Logging** (since v0.4): same event taxonomy as `python` target. `model` field
comes from the MCP sampling response. `tokens_in`/`tokens_out` emitted iff the
sampling response carries `usage`.

---

## `target: claude-skill`

Produces a Claude Code skill directory. The emitted skill is **LLM-host-orchestrated**: the Claude Code host reads `SKILL.md` and drives the flow, calling emitted scripts for exact steps and producing judgment outputs inline. No external runtime, no CLIO binary, and no API key are required after the skill is installed.

### Layout

```
output/
  SKILL.md                          # orchestration manifest: step checklist + contracts
  README.md                         # install instructions + invocation guide
  process_flow.dot                  # Graphviz DOT — visual representation of the flow
  state.example.json                # example state object for testing
  scripts/
    NN_<step_name>.py               # exact step: NotImplementedError stub (user fills body)
    _validate.py                    # bundled: JSON Schema validation (stdlib fallback)
    _cache_key.py                   # bundled: deterministic SHA256 cache-key generator
  prompts/
    NN_<step_name>.md               # judgment step prompt template ({{variable}} placeholders)
  schemas/
    NN_<step_name>.input.json       # JSON Schema for step TAKES
    NN_<step_name>.output.json      # JSON Schema for step GIVES (judgment steps only)
```

### Mapping

| IR element       | Emitted artifact                                                  |
|------------------|-------------------------------------------------------------------|
| STEP `exact`     | `scripts/NN_<name>.py` — stub with `raise NotImplementedError`   |
| STEP `judgment`  | `prompts/NN_<name>.md` + `schemas/NN_<name>.output.json`         |
| CONTRACT         | Inline JSON Schema in `schemas/*.input.json` / `*.output.json`   |
| FLOW             | `SKILL.md` orchestration manifest (TodoWrite checklist per step)  |
| PARALLEL FOR EACH | Serialised in the manifest — see key behaviors below            |
| CACHE            | Noted in `SKILL.md`; `scripts/_cache_key.py` helper bundled      |
| RETRY / ON_FAIL  | Documented in `SKILL.md` step entry as an OnFail strategy note   |
| RESOURCES        | Noted in `SKILL.md` header; no runtime dependency on CLIO        |

### Use

```bash
python -m clio compile flow.clio --target claude-skill --output ./skill-out
```

Then:
1. Fill in the bodies of `scripts/NN_*.py` (they are `NotImplementedError` stubs by default).
2. Copy the output directory to `~/.claude/skills/<skill-name>/`.
3. Invoke the skill from Claude Code — the host reads `SKILL.md` and orchestrates the flow.

### Key behaviors

**Exact-step stubs.** `scripts/NN_<name>.py` files are stubs that `raise NotImplementedError("fill me in")`. This mirrors the `python` target's behavior. After compilation, fill in the function body: it receives the step's TAKES fields as kwargs and must return a `dict` matching the GIVES contract. The bundled `_validate.py` helper can be used to check the return value against the emitted `schemas/NN_<name>.output.json`.

**No FLOW description warning.** If the source FLOW has no description string, the emitter prints a warning to stderr and writes a placeholder in `SKILL.md`. The skill still compiles — add a description in the source to produce a more useful manifest.

**Unsupported exact languages.** Exact steps with a `LANG` other than `python` or `bash` raise a compile-time error. Rewrite the step in Python or Bash, or use `--target python` / `--target mcp-server`.

**PARALLEL FOR EACH serialisation.** The emitter cannot emit concurrent iteration — the LLM host does not execute tasks in parallel. A warning is printed to stderr; the emitted skill still runs correctly (iterations are serialised). If genuine parallelism is required, use `--target python` or `--target mcp-server`.

**Runtime dependency.** Python (for the bundled helpers in `scripts/`). The host that reads `SKILL.md` drives all judgment steps directly — no `anthropic` SDK, no `pydantic` install required.

### `.clio/` sidecar (v0.19)

Every `clio compile --target claude-skill` also writes a `<skill>/.clio/`
directory alongside the user-facing manifest:

```
<skill>/
  SKILL.md
  scripts/
  prompts/
  schemas/
  .clio/                # ← sidecar (v0.19)
    source.clio         # verbatim copy of the source .clio (entry file)
    manifest.json       # CLIO version, emission timestamp,
                        #   source_hash + per-file hashes (LF-normalised for text, raw for binary)
```

The sidecar enables byte-identical recovery of the source via
[`clio import <skill-dir>`](manual/05-cli-reference.md#import--recover-a-clio-from-a-claude-code-skill-v019):
when every recorded hash still matches the current file state on disk,
`clio import` returns `source.clio` directly — no LLM call, no API key.
A hash mismatch (`--mode auto`) triggers the LLM-assisted fallback;
under `--mode strict` it exits 2 instead.

The sidecar is excluded from `_gather_skill_files`, so `clio import --mode
infer` cannot accidentally read its own previous emission. Sidecar
emission is best-effort — a write failure is logged to stderr but never
blocks the main skill output.

**Known limitation (v0.19):** the sidecar stores only the entry `.clio`
file. Multi-file projects (those using `FROM "<path>" IMPORT ...`) recover
a `source.clio` that references files not present in the sidecar — resolved
in v0.22 (multi-file sidecar recovery shipped;
[#67](https://github.com/Sandjab/clio/issues/67) closed). Workarounds for
older skills: keep the imported `.clio` files next to the recovered entry,
or use `clio import --mode infer` to inline everything through the LLM.

---

## `target: local` (Future)

**What it emits**: same as `python`, but judgment steps use a local model (Ollama, vLLM) instead of an API.

**Contract validation**: this is the one target where Outlines or Guidance become necessary. Local models don't support native `response_model` — constrained decoding at the tokenizer level is the only way to guarantee schema compliance. The emitter would constrain decoding with Outlines/Guidance at the tokenizer level — the one place a pluggable validator interface (none is built today) would be warranted.

This is the only justified dependency on these libraries. Not day 1.

---

## `target: rust` (Future)

**What it emits**: a Cargo project with async runtime.

Steps marked `LANG: rust` or `LANG: auto` for large data compile to native Rust. Judgment steps compile to functions calling the Anthropic API via `reqwest`. Contracts compile to Rust structs with `serde` derive macros.

---

## `target: go`

Produces a Go module with `go.mod`, one package per STEP, a `contracts/` package, and a `cmd/<flow>/main.go` CLI entry point.

### Layout

```
output/
  go.mod
  go.sum                        # (after `go mod tidy`)
  contracts/
    contracts.go                # Go structs with `json:"..."` tags, one per CONTRACT
  steps/
    <exact_step>/
      <exact_step>.go           # NotImplementedError-equivalent (TODO body)
    <judgment_step>/
      <judgment_step>.go        # auto-generated: net/http Anthropic call + cache + ON_FAIL chain
  flow/
    flow.go                     # orchestrator: Run(ctx, kwargs) → (map[string]any, error)
  cmd/
    <flow_name>/
      main.go                   # CLI: `go run ./cmd/<flow_name>` or `go build`
  clio_runtime/
    validate/
      validate.go               # jsonschema/v6 + x-clio-assert walker
    cache/
      cache.go                  # SHA256 content-addressed on-disk cache
```

### Use

```bash
go run ./cmd/<flow_name> --kwargs '{"file": "customers.csv"}'
```

Or build a single static binary:

```bash
go build -o my_flow ./cmd/<flow_name>
./my_flow --kwargs '{"file": "customers.csv"}'
```

Or call programmatically from Go:

```go
import "example.com/<module>/flow"

result, err := flow.Run(ctx, map[string]any{"file": "customers.csv"})
```

- **FOR EACH PARALLEL:** emits `golang.org/x/sync/errgroup` with a concurrency cap of 10.

### Refused combinations (v0.23 scope)

The following are rejected at compile time with a clear error code and a pointer to the appropriate alternative:

- `LANG: python` / `bash` / `rust` / `node` — only `go` or `auto` accepted (E_GO_001).
- `invoke.mode: cli` — no `claude -p` subprocess in a Go binary (E_GO_002).
- `invoke.api.bedrock` / `vertex` — not wired (E_GO_003).
- `invoke.api.openai` — OpenAI-compat SDK not wired yet; use `--target python` (E_GO_005).
- A **multi-GIVES sub-flow used as a `FOR EACH PARALLEL` body** — a single typed slice collector cannot hold multiple GIVES fields (E_GO_006). Single-GIVES parallel and all sequential composition are supported.
- `impl.mode: sql` — deferred to v0.24 (E_GO_009).
- `impl.mode: mcp_tool` — deferred to v0.24 (E_GO_010).
- `--from-step N` resume — not implemented (the Go binary re-runs the full flow without error; use `--target python` for incremental re-runs).
- `TEST` blocks — deferred (E_GO_012).

### Inherited features

These work identically to the v0.23 Go target without restriction:

- `IF / ELSE`, `MATCH / CASE`, `WHILE ... MAX N:` — emits idiomatic Go `if/else`, `switch`, bounded `for`.
- `FOR EACH ... IN ...:` — emits `for _, v := range state[...]`.
- `FOR EACH ... PARALLEL AS <collector>:` — emits `errgroup.Go(...)` fan-out with a 10-goroutine cap.
- `CACHE: ttl(...)` — on-disk SHA256 layout interchangeable with the `python` target (same key derivation).
- `ON_FAIL: retry(N) then escalate then fallback(...) then abort(...)` — full strategy chain.
- `RESCUE` handlers + `step.error.*` + `RESUME(...)` — emitted as Go `error` wrapping + typed return injection.
- `impl.mode: rest` — `net/http` client with `${var}` substitution, `response_path` traversal, impl-level retry (constant/exponential backoff, `Retry-After`); **json/raw body parity** with `clio/runtime/rest.py`. `form`/`file`/`multipart` bodies are refused at compile time (E_GO_013) — use `--target python`.
- `impl.mode: shell` — `os/exec` with per-token `${var}` substitution, context timeout, `parse: none` (stdout str) / `parse: json` (unmarshal).
- **FLOW composition** — each signed sub-flow lowers to an unexported `run<Name>(ctx, …) (map[string]any, error)` func; the call site flat-merges the sub-flow's GIVES into parent state (parity with the `python` target's `run_<name>()`). A single-GIVES sub-flow used as a `FOR EACH PARALLEL` body is **terminal-only**: the collector is produced but typed downstream consumption (`aggregate(xs=results)`, `FOR EACH x IN results`) fails `go build` and is deferred to v0.24.

### Cache layout interchangeable with `python` and `claude-cli`

All three targets read/write `<output>/.cache/<step_name>/<sha256>.json` with the same key derivation (SHA256 of `step + model + prompt + schema`). Switching targets between runs preserves cache hits.

### Model name mapping

`RESOURCES.models` short names map to Anthropic API model IDs at emit time (same mapping as the `python` target):

| CLIO short | Anthropic ID |
|------------|--------------|
| `haiku`    | `claude-haiku-4-5-20251001` |
| `sonnet`   | `claude-sonnet-4-6` |
| `opus`     | `claude-opus-4-7` |

### Logging

Structured JSONL logging is a silent no-op for the Go target (the `clio_runtime/cache` package is wired; flow-level event emission is deferred). To get `CLIO_LOG=1` structured events, compile to `--target python` or `--target mcp-server`.

### Resume

`--from-step N` resume is not implemented. The Go binary runs the full flow on each invocation without error. For incremental re-runs on a long pipeline, compile to `--target python` today.

---

## `target: swift`

Produces a **two-target** Swift package (a library `ClioFlow` plus an executable named after the flow) that builds with `swift build` on macOS and Linux. **Zero external SPM dependencies** — all runtime code is vendored pure Swift.

### Layout

```
output/
  Package.swift                 # SPM manifest with two targets:
                                #   .target(name: "ClioFlow")
                                #   .executableTarget(name: "<flow_name>", dependencies: ["ClioFlow"])
  Sources/
    <flow_name>/
      Main.swift                # @main executable entry: parses --kwargs JSON, calls Flow.run(kwargs:)
    ClioFlow/
      Flow.swift                # orchestrator: public enum Flow { @MainActor public static func run(kwargs: [String: Any]) async throws -> [String: Any] }
      Contracts.swift           # (only if contracts) Codable + Sendable structs, one per CONTRACT, with static let jsonSchema + validate()
      Steps/
        Step01_<exact_step>.swift     # typed stub: func step_<name>(_ input: ...)  → fatalError("fill me in")
        Step02_<judgment_step>.swift  # auto-generated: URLSession Anthropic call + cache + ON_FAIL chain
      Runtime/
        Anthropic.swift         # (only if judgment) zero-dep URLSession client; #if canImport(FoundationNetworking) for Linux
        Validate.swift          # (only if contracts) hand-rolled JSON Schema + x-clio-assert walker (matches python/go semantics)
        Cache.swift             # (only if CACHE) SHA256-keyed on-disk cache; keys byte-identical with python and go targets
        SHA256.swift            # (only if CACHE) vendored pure-Swift SHA256 (no CryptoKit dependency)
```

`Flow.swift`, `Contracts.swift`, `Steps/`, and `Runtime/` all live under `Sources/ClioFlow/` (the library target). Only `Main.swift` lives under `Sources/<flow_name>/` (the executable target). The `Runtime/*` and `Contracts.swift` files are emitted conditionally — `Anthropic.swift` only when the flow has a judgment step, `Validate.swift`/`Contracts.swift` only when it declares a CONTRACT, `Cache.swift`/`SHA256.swift` only when a step uses `CACHE`.

### Use

```bash
swift build
.build/debug/<flow_name> --kwargs '{"file": "input.txt"}'
```

Or build a release binary:

```bash
swift build -c release
.build/release/<flow_name> --kwargs '{"file": "input.txt"}'
```

- **FOR EACH PARALLEL:** emits `withThrowingTaskGroup` with a concurrency cap of 10 and ordered collect.

### Refused combinations (MVP scope)

The following are rejected at compile time with a clear error code and a pointer to the appropriate alternative:

- `LANG: python` / `bash` / `rust` / `node` / `go` on an exact step — only `swift` or `auto` accepted (E_SWIFT_001).
- `invoke.mode: cli` — no `claude -p` subprocess in a Swift binary (E_SWIFT_002).
- `invoke.api.bedrock` / `vertex` — not wired (E_SWIFT_003).
- `invoke.api.openai` — OpenAI-compat SDK not wired; use `--target python` (E_SWIFT_005).
- A **multi-GIVES sub-flow used as a `FOR EACH PARALLEL` body** — reserved code `E_SWIFT_006`. This refusal is **dormant** today: a multi-GIVES sub-flow requires more than one FLOW, which is already refused earlier (FLOW composition — Phase 5). The code is held in reserve and only becomes reachable once FLOW composition ships. You will not encounter `E_SWIFT_006` in the current MVP.
- `impl.mode: sql` — deferred (E_SWIFT_009).
- `impl.mode: mcp_tool` — deferred (E_SWIFT_010).
- `TEST` blocks — deferred (E_SWIFT_012).
- `impl.rest` with a `form`/`file`/`multipart` body — only `json` and `raw` body forms are supported; others refused at compile time (E_SWIFT_013).
- `impl.mode: rest` (json/raw) — deferred to Phase 4; use `--target python` or `go` for now.
- `impl.mode: shell` — deferred to Phase 4; use `--target python` or `go` for now.
- **FLOW composition** (multiple FLOWs / sub-flow calls / `FlowCallIR`) — deferred to Phase 5; use `--target python` or `go`.
- `RESCUE` / `RESUME` handlers — deferred to Phase 5; use `--target python` or `go`.
- `FOR EACH <var> IN <loop_variable>:` (iterating over a loop variable from an enclosing FOR EACH) — deferred; use `--target python` or `go`.

### Inherited features (MVP scope)

These work identically across macOS and Linux without restriction:

- `IF / ELSE`, `MATCH / CASE` (with synthesized exhaustive `default`), `WHILE ... MAX N:` — emits idiomatic Swift `if/else`, `switch`, bounded `for/break`.
- `FOR EACH ... IN ...:` (sequential) — emits `for item in state[...] as! [...]`.
- `FOR EACH ... PARALLEL AS <collector>:` — emits `withThrowingTaskGroup` fan-out with a 10-task concurrency cap and ordered collect.
- `CACHE: ttl(...)` — SHA256-keyed on-disk layout **byte-identical** with the `python` and `go` targets (same key derivation: SHA256 of step + model + prompt + schema); `.cache/` directories are interchangeable across targets.
- `ON_FAIL: retry(N) then escalate then fallback(...) then abort(...)` — `retry(N)` (exponential backoff with `Task.sleep`), `fallback(step)`, and `abort(msg)` are emitted; `escalate` is a **no-op** (single model per emission, same as `target: go`).
- `CONTRACT` — emitted as a `Codable, Sendable` struct with `static let jsonSchema` and a `validate()` method backed by the hand-rolled `Runtime/Validate.swift` JSON Schema + `x-clio-assert` walker (parity with `clio/runtime/validate.py` and `clio_runtime/validate/validate.go`).

### Known limitation

A `FOR EACH ... PARALLEL AS <collector>` result is **terminal-only** on `target: swift`: `clio compile` emits the package without error (exit 0), but typed downstream consumption of the collector (`aggregate(xs=results)` or `FOR EACH x IN results`) does not compile under `swift build`. Use `--target python` or `go` if you need to feed the collected list into a downstream step.

### Cache layout interchangeable with `python` and `go`

All three targets read/write `<output>/.cache/<step_name>/<sha256>.json` with the same key derivation. Switching targets between runs preserves cache hits.

### Model name mapping

`RESOURCES.models` short names map to Anthropic API model IDs at emit time (same mapping as the `python` and `go` targets):

| CLIO short | Anthropic ID |
|------------|--------------|
| `haiku`    | `claude-haiku-4-5-20251001` |
| `sonnet`   | `claude-sonnet-4-6` |
| `opus`     | `claude-opus-4-7` |

### Logging

Structured JSONL logging is not emitted by the Swift target. To get `CLIO_LOG=1` structured events, compile to `--target python` or `--target mcp-server`.

### Resume

`--from-step N` resume is not implemented. The Swift binary runs the full flow on each invocation without error. For incremental re-runs on a long pipeline, compile to `--target python` today.

---

## `target: claude-workflow`

Produces a **Claude Code Workflow script**: a single JS module that orchestrates **subagents**. Like `claude-cli` and `claude-skill`, it is host-orchestrated — the Claude Code session is the runtime, so there is **no API key** and nothing to install.

It exists for one reason: it is the **only target where `FOR EACH … PARALLEL` is really parallel**. `claude-skill` serialises it (with a warning) and `claude-cli` rejects it; here each iteration becomes a concurrent subagent under `parallel()`. A linear flow gains nothing from this target; a fan-out flow gains everything.

### Layout

```
output/
  <flow-name>.workflow.js   # the script (kebab-cased flow name)
  README.md                 # how to install it, and which stubs you must fill in
  .clio/
    source.clio             # verbatim entry source
    manifest.json           # clio_version, emitted_at, source_hash, file_hashes
    sources/                # multi-file projects only (FROM … IMPORT)
```

The `.clio/` sidecar is the same one `claude-skill` writes (v0.19), so `clio import` recovers the source verbatim, hash-drift detection included.

### Use

```bash
python -m clio compile examples/parallel_review.clio --target claude-workflow --output ./wf-out
cp ./wf-out/parallel-review.workflow.js .claude/workflows/
```

Then run the workflow from Claude Code. The emitter never writes outside `<output>` — installing into `.claude/workflows/` is an explicit copy.

### What each IR node becomes

| CLIO | Emitted JS |
|---|---|
| `MODE: judgment` | `await agent(prompt, { label, phase, schema })` — a subagent, forced through the step's `GIVES` schema by the host |
| `MODE: exact` (code / no `impl`) | a **pure-JS stub** that throws until you fill it in |
| `FLOW.TAKES` | the `args` global, presence-checked at script start |
| `FOR EACH` (sequential) | `for…of` |
| `FOR EACH … PARALLEL AS c` | `state['c'] = $$collect(await parallel(items.map(x => () => $$settle(() => step(x)))), items, …)` — real fan-out |
| `IF` / `MATCH` / `WHILE … MAX N` | native `if` / `switch` / bounded `while` |
| sub-flow call | **inlined** as a local `async function` in the same script |
| `ON_FAIL` / `RESCUE` / `RESUME` | `try` / `catch` + retry loop / fallback step / abort |
| `CONTRACT` | a self-contained schema literal — every `$ref` is **inlined** (the sandbox cannot resolve a `$ref` at run time) |
| `TEST` | ignored (only `--target python` emits pytest) |

`agent()` returns **null** on terminal failure rather than throwing, so every emitted step wrapper converts that null into a thrown error — otherwise `ON_FAIL` and `RESCUE` would be dead code.

`parallel()` is the **only** fan-out primitive emitted. The Workflow host also offers `pipeline()` (one item threaded through several stages), but the language has no source that reaches it: a `FOR EACH … PARALLEL` body is always **exactly one** step or sub-flow call — [`LANGUAGE_SPEC.md`](LANGUAGE_SPEC.md#parallel-for-each-bodies), enforced by the IR builder for every target. A flow that wants several steps per item writes several `FOR EACH … PARALLEL` blocks, as [`examples/parallel_review.clio`](../examples/parallel_review.clio) does (review, then triage).

Inside a fan-out, a thunk that throws resolves to `null` in the array `parallel()` returns — the same value a step whose `GIVES` is `Optional<T>` produces when it legitimately returns `null`. The two are told apart by the emitted `$$settle` wrapper, which makes each item report its outcome (`{ok, value}` / `{ok, error}`) instead of having it inferred from its value. `$$collect` then **fails the flow** on any failed item, naming it and its cause — the semantics `python` / `go` / `swift` already have, the step's `ON_FAIL` chain having already run inside its own function — and maps the successes back **in order**, so `state['c'][i]` stays the result of `items[i]` and a legitimate `null` survives.

### Refused at compile time

- `E_WF_001` — the source declares no `FLOW`; nothing to orchestrate.
- `E_WF_002` — `invoke.api.openai / bedrock / vertex`: an `agent()` cannot call a non-Anthropic provider. Use `--target python`.
- `E_WF_003` — `impl.mode: shell | rest | sql | mcp_tool`: the workflow sandbox has **no process, no network and no filesystem**. Move the IO out of the flow, or use `--target python / go / swift`.
- `E_WF_004` — an explicit `LANG:` other than `node` / `auto` on an exact step: this target's language is JavaScript.
- `E_WF_005` — a `CONTRACT` reference **cycle**: schemas are inlined, and a cycle cannot be.
- `E_WF_006` — the source declares **several `FLOW`s** and none was selected. This target emits exactly one script, and compiling the first declared FLOW would silently drop the others: re-run with `--flow <name>`.
- `E_WF_007` — a `FLOW` that calls **itself**, directly or through a cycle: sub-flows are inlined, so recursion would overflow the stack at run time.

### Degraded, with a compile-time warning

- `W_WF_001` — **`CACHE:` is ignored.** The sandbox has no filesystem and no clock. A cache miss is slower, never wrong.
- `W_WF_002` — **`ON_FAIL` retries run without backoff.** They fire back-to-back: `Date.now()` throws in the sandbox, so there is no delay and no jitter.
- `W_WF_003` — **`CONTRACT … ASSERT` is not enforced.** The host validates the subagent's output against the emitted JSON Schema (types, ranges, enums); the `ASSERT` predicate is dropped. Use `--target python` if the predicate is load-bearing.

Each warning names the step (or contract) and its source line. The emitted `README.md` repeats exactly the warnings this flow actually triggered — and stays silent when it triggers none.

### Model name mapping

A declared Anthropic model id maps to the tier enum `agent()` accepts:

| Declared model | `agent({ model })` |
|---|---|
| `claude-opus-*` | `'opus'` |
| `claude-sonnet-*` | `'sonnet'` |
| `claude-haiku-*` | `'haiku'` |
| *(nothing declared)* | **omitted** — the subagent inherits the session model |

`invoke.mode: cli` needs no mapping: here, the `agent()` call *is* the Claude Code invocation.

### Known limitations

- The entry flow's `GIVES` is not returned anywhere — the values are left in the script's `state` object. Sub-flow `GIVES` **is** returned (that is how a sub-flow's result reaches the caller).
- No structured JSONL logging (`CLIO_LOG=1`) and no `--from-step N` resume: use `--target python` for either.

---

## `target: docker` (Future)

**What it emits**: a multi-stage Dockerfile + docker-compose.yml.

Each step with a different LANG compiles to its own build stage. The final stage contains all binaries + an orchestrator script. Judgment steps share a common Python/Node thin client for API calls.

This is the target for mixed-language flows where one step is Rust (performance), another is Python (glue), and judgment steps use the API.

---

## `target: hybrid` (Future)

**What it emits**: a Claude CLI project where `exact` steps are compiled binaries instead of scripts.

Combines `claude-cli` orchestration (CLAUDE.md, hooks, `claude -p`) with pre-compiled binaries for heavy `exact` steps. The `run.sh` calls binaries for `exact` and `claude -p` for `judgment`.

---

## Adding a new target

1. Create `emitters/new_target.py`
2. Implement `class NewTargetEmitter(BaseEmitter)`
3. Register it in the CLI's target map
4. Add tests in `tests/test_emitters/test_new_target.py`
5. Document it in this file

An emitter has exactly one job: take an IR graph, write files. It never imports from other emitters. It never calls LLMs. It never executes the flow.
