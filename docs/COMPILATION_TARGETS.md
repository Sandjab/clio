# CLIO Compilation Targets

Each target is an emitter module that transforms the IR graph into a runnable project. This document describes what each target emits and the constraints it operates under.

## Targets at a glance

| Target | Status | Output | Why / Use case | IMPORT (v0.18) | Effort |
|---|---|---|---|---|---|
| `claude-cli` | Implemented | Claude Code project (bash + `claude -p` subprocess) | Prototype + reference target | ❌ E_CLI_001 | — |
| `python` | Implemented | Python package (Anthropic SDK + Pydantic v2) | Production-grade Python deployment | ✅ | — |
| `claude-skill` | Implemented | Claude Code skill directory (`SKILL.md` + `scripts/` + `schemas/` + `prompts/`) | Turn a `.clio` into an LLM-host-orchestrated skill; no external runtime or API key needed after install | ✅ | — |
| `mcp-server` | Implemented | MCP server, each FLOW exposed as a tool with sampling-based judgment | Native Anthropic ecosystem integration; turn a `.clio` into a structured MCP tool | ✅ | — |
| `langgraph` | Candidate | LangGraph graph (nodes = STEPs, state = CONTRACTs) | Adoption by existing LangChain users; positions CLIO as a meta-language | ✅ | Medium |
| `local` | Future | Same as `python`, with Ollama/vLLM | Offline / data-privacy constraints | ✅ (planned) | High (Outlines/Guidance) |
| `rust` | Future | Cargo async project | Performance-critical `exact` steps | planned | High |
| `go` | Future | Go module with goroutines + `net/http` | Concurrent `exact` steps, single static binary | planned | Medium |
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
| WHILE loop        | `claude -p` in a bash while loop with state file  |
| FOR EACH          | bash `for` loop + `claude -p` or `xargs`          |
| MATCH/CASE        | bash `case ... esac`                               |
| IF/ELSE           | bash `if/else`                                     |
| ON_FAIL/fallback  | `||` operator or trap                              |
| RESOURCES         | `CLAUDE.md` header + CLI flags in `run.sh`         |
| CACHE             | `.cache/` dir, SHA256 hash check before API calls   |

**State passing**: between steps, state is serialized as JSON to a `state.json` file. Each step reads its input from state, writes its output back.

**Judgment steps**: the `.prompt` file is a template with `{{variable}}` placeholders. `run.sh` substitutes variables from state before piping to `claude -p`.

**Contract validation**: hooks in `.claude/hooks.json` run a validation script after each judgment step. Validation is a simple `python -m jsonschema` call against the emitted `.schema.json` — no external lib beyond the stdlib-adjacent `jsonschema` package. If validation fails, the hook triggers the ON_FAIL strategy.

**Logging**: not instrumented in v0.4. Use `--target python` or
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

**Logging** (v0.4+): structured JSONL events via `CLIO_LOG=1`. Six event types
covering `flow_start`/`flow_end`, `step_start`/`step_end` (3 paths for judgment),
`parallel_block_start`/`parallel_block_end`. Tokens extracted from
`response.usage` (Anthropic `input_tokens`/`output_tokens`, OpenAI
`prompt_tokens`/`completion_tokens`).

**Resume** (v0.4+): emitted package writes `state.json` atomically after
each top-level chain item; `python -m my_pkg --from-step N` reloads the
state and skips items 1..N. Path via `CLIO_STATE_FILE` env var.

---

## `target: mcp-server`

Produces a runnable MCP (Model Context Protocol) server. Each `EXPOSE FLOW` in
the entry file (v0.18+) becomes a tool registered with the official `mcp` Python
SDK. The entry file must expose at least one FLOW (E_MCP_001). Prior to v0.18,
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
- Source with no `FLOW` declaration — an MCP server with no tools is a no-op.

### Inherited features

These work identically to the `python` target (shared helpers in `_python_helpers.py`):

- `FOR EACH ... IN ...:` — emits `for var in state[...]:` with body step calls.
- **FOR EACH PARALLEL:** supported via `asyncio.gather` + `Semaphore(10)`. Judgment steps thread the MCP session per task.
- `CACHE: ttl(...)` — same on-disk layout as `python` and `claude-cli`; cache files are interchangeable.
- `ON_FAIL: retry / escalate / fallback / abort` — full strategy chain.
- `impl.mode: rest` — emits `requests.request(...)` with `${var}` URL templating.
- `impl.mode: shell` — emits `subprocess.run([...], shell=False)`.

**Logging** (v0.4+): same event taxonomy as `python` target. `model` field
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

---

## `target: local` (Future)

**What it emits**: same as `python`, but judgment steps use a local model (Ollama, vLLM) instead of an API.

**Contract validation**: this is the one target where Outlines or Guidance become necessary. Local models don't support native `response_model` — constrained decoding at the tokenizer level is the only way to guarantee schema compliance. The emitter plugs Outlines/Guidance behind the same `ContractValidator` interface used by other targets.

This is the only justified dependency on these libraries. Not day 1.

---

## `target: rust` (Future)

**What it emits**: a Cargo project with async runtime.

Steps marked `LANG: rust` or `LANG: auto` for large data compile to native Rust. Judgment steps compile to functions calling the Anthropic API via `reqwest`. Contracts compile to Rust structs with `serde` derive macros.

---

## `target: go` (Future)

**What it emits**: a Go module with `go.mod`, an orchestrator `main.go`, and one package per step.

Steps marked `LANG: go` or `LANG: auto` compile to native Go, with goroutines + channels for `parallel`/`foreach` blocks. Judgment steps compile to functions calling the Anthropic API via `net/http`. Contracts compile to Go structs with `json` tags, validated through generated JSON Schema checkers.

Distribution sweet spot: a single static binary, no runtime to install — useful for CLI tools and side-cars where the `python` target's interpreter footprint is unwelcome.

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
