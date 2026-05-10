# CLIO

**Compiled Language for Intent Orchestration**

[![CI](https://github.com/Sandjab/clio/actions/workflows/ci.yml/badge.svg)](https://github.com/Sandjab/clio/actions/workflows/ci.yml)
[![Last commit](https://img.shields.io/github/last-commit/Sandjab/clio)](https://github.com/Sandjab/clio/commits/main)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.12-blue.svg)](https://www.python.org)
[![Version](https://img.shields.io/badge/Version-v0.11.0-green.svg)](https://github.com/Sandjab/clio/releases/tag/v0.11.0)
[![Visitors](https://komarev.com/ghpvc/?username=sandjab-clio&label=Visitors&color=0e75b6&style=flat)](https://github.com/Sandjab/clio)

CLIO is a declarative language that compiles hybrid LLM/code programs into executable projects. You describe *what* you want — the compiler decides *what runs as code and what runs as an LLM*, then emits a project you can run directly.

```
STEP detect_churn
  TAKES:     customers: CSV
  GIVES:     risks: List<{client: str, risk: enum(low|mid|high), reason: str}>
  MODE:      judgment
  CACHE:     ttl(24h)
  VALIDATE:  each risk.reason cites a column from customers
  ON_FAIL:   retry(3) then escalate
```

## The problem

Every LLM-powered system today is a handwired mix of prompts, scripts, API calls, and glue code. The wiring is fragile, the LLM parts are non-deterministic, and nothing is reusable.

Existing tools each solve a piece: DSPy optimizes prompts, LangGraph orchestrates agents, Outlines constrains outputs, Prefect manages dataflows. None of them unify deterministic code and LLM reasoning in a single composable abstraction.

## The idea

Three primitives:

- **STEP** — an atomic unit of work. Declares inputs, outputs, and a `MODE`: `exact` (deterministic code), `judgment` (needs an LLM), or `auto` (compiler decides).
- **CONTRACT** — a typed shape guarantee (`SHAPE`, `ASSERT`, `CONFIDENCE`) that makes stochastic LLM output composable with deterministic code downstream.
- **FLOW** — a directed graph of steps with control flow (`FOR EACH`, `WHILE`, `IF`, `MATCH/CASE`) and failure strategies (`retry`, `fallback`, `escalate`).
  - `FOR EACH ... PARALLEL AS <name>:` — fan a STEP over a collection in parallel, collect typed results.

A compiler parses `.clio` files into an intermediate representation, optimizes it (batching, context budgeting, model routing), and emits a runnable project for a chosen target.

```mermaid
flowchart LR
    src[".clio source"] --> parse["Parser → AST"]
    parse --> ir["IR Builder<br/><sub>resolve contracts, fallbacks,<br/>type-check edges</sub>"]
    ir --> emit["Emitter<br/><sub>per target</sub>"]
    emit --> proj["Runnable project<br/><sub>bash / Python / Docker / ...</sub>"]
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full pipeline and IR build passes.

## Compilation targets

The same `.clio` source compiles to different targets:

| Target       | Output                                              |
|--------------|------------------------------------------------------|
| `claude-cli` | Claude Code project (CLAUDE.md, hooks, bash scripts) |
| `python`     | Python package (Pydantic + Anthropic / OpenAI-compat SDKs, optional `requests`, `mcp`, `psycopg`, `pymysql`) |
| `mcp-server` | MCP server (FLOW = tool, judgment via `sampling/createMessage`, exact via the same runtime as `python`) |
| `langgraph`  | LangGraph StateGraph (single-step IF / MATCH branches, sequential chain only — multi-step branches deferred) |
| `rust` / `docker` | *(planned)* not yet implemented; the emitter interface is target-agnostic, only the dispatch is missing |

## Quick start

```bash
# Compile a .clio file to a Claude Code project
python -m clio compile examples/mvp.clio --target claude-cli --output ./output

# Validate syntax without emitting
python -m clio check examples/mvp.clio

# Render the FLOW as a Mermaid diagram (paste into a GitHub PR)
python -m clio graph examples/mvp.clio
python -m clio graph examples/mvp.clio --format dot --output flow.dot
python -m clio graph examples/mvp.clio --format html --output flow.html   # rich, click-to-inspect

# Generate a .clio source from a natural-language description (requires `pip install -e .[gen]`)
export ANTHROPIC_API_KEY=...
python -m clio gen "Pour chaque article, extrais les entités et résume-les" > flow.clio
python -m clio compile flow.clio --target python --output ./out

# Compile to a runnable MCP server (each FLOW becomes a tool)
python -m clio compile examples/ticket_routing.clio --target mcp-server --output ./mcp-out
pip install -e ./mcp-out
# then add the server to your MCP client config — see ./mcp-out/README.md

# Compile a SQL-backed flow to a Python package (sqlite, postgres, or mysql)
python -m clio compile examples/sql_demo.clio --target python --output ./sql-out

# Run tests
pytest tests/ -v
```

### Observability

Set `CLIO_LOG=1` to emit structured JSON-Line events to stderr, or
`CLIO_LOG_FILE=run.jsonl` to redirect to a file:

```bash
CLIO_LOG=1 CLIO_LOG_FILE=run.jsonl python -m my_compiled_flow
```

Six event types cover flow start/end, step start/end (with `mode`,
`duration_ms`, optional `cache_hit`/`model`/`tokens_*`), and
parallel-block start/end. The schema is OTel-mappable. See
[docs/LANGUAGE_SPEC.md](docs/LANGUAGE_SPEC.md) for the full reference.

### Resume

If a long pipeline crashes mid-flow, resume from the last completed step:

```bash
python -m my_compiled_flow --from-step 3
```

The package writes `state.json` after each completed step (path via
`CLIO_STATE_FILE`). See [docs/LANGUAGE_SPEC.md](docs/LANGUAGE_SPEC.md)
for the schema.

## Example

This is `examples/mvp.clio` — it compiles to both `claude-cli` and `python` with no edits beyond filling the EXACT step bodies.

```
CONTRACT customer_risk
  SHAPE:  {client: str, risk: enum(low|mid|high), reason: str(max=300)}
  ASSERT: len(reason) > 0

STEP load_customers
  TAKES: file:      CSV
  GIVES: customers: List<{name: str, revenue: float}>
  MODE:  exact

STEP detect_churn_naive
  TAKES: customers: List<{name: str, revenue: float}>
  GIVES: risks:     List<customer_risk>
  MODE:  exact

STEP detect_churn
  TAKES:    customers: List<{name: str, revenue: float}>
  GIVES:    risks:     List<customer_risk>
  MODE:     judgment
  CACHE:    ttl(24h)
  ON_FAIL:  retry(3) then escalate then fallback(detect_churn_naive) then abort("churn detection exhausted")

FLOW customer_retention
  load_customers(file="customers.csv")
    -> detect_churn(customers)

RESOURCES
  target:  claude-cli
  models:  [haiku, sonnet, opus]
```

The compiler reads this and emits a runnable project with: a typed `CustomerRisk` Pydantic model with the `len(reason) > 0` assertion, a `detect_churn` step that calls the LLM with the inlined JSON Schema and a 24-hour cache, and a resilience chain — three retry attempts on Haiku, escalation to Sonnet (one attempt), fallback to the deterministic `detect_churn_naive` step, and finally `abort` with an explicit message. None of that wiring is hand-written.

See [`examples/`](examples/) for the full set: `mvp.clio` (above), `entities.clio` (NER + summary, nested record types), `classify_corpus.clio` (FOR EACH + OpenAI-compat via LiteLLM / Gemini), `parallel_classify.clio` (FOR EACH PARALLEL), `ticket_routing.clio` (IF / MATCH branches), `critical_pipeline.clio` (ON_FAIL × RESCUE), `rest_advanced.clio` (auth, multipart, retries), `mcp_tool.clio` (MCP server consumer), `sql_demo.clio` (sqlite + auto-mapped GIVES), `rag_basic.clio` and `rag_selfcontained.clio`.

## Project structure

```
clio/
  parser/          # .clio source → AST
  ir/              # intermediate representation, optimization
  emitters/        # IR → target project
  cli.py           # entry point
tests/
docs/
  LANGUAGE_SPEC.md
  ARCHITECTURE.md
  COMPILATION_TARGETS.md
```

## Documentation

- **[User manual](docs/manual/README.md)** — start here. Tutorial, language tour, cookbook, CLI reference, troubleshooting.
- [Language specification](docs/LANGUAGE_SPEC.md) — full grammar, types, and keywords (authoritative reference).
- [Architecture](docs/ARCHITECTURE.md) — compiler pipeline, design decisions.
- [Compilation targets](docs/COMPILATION_TARGETS.md) — what each target emits.
- [Positioning](docs/POSITIONING.md) — strategy, comparisons (DSPy, LangGraph, Outlines).
- [Design document (FR)](docs/clio-spec.md) — original design rationale (in French).
- [Examples README](examples/README.md) — guided tour of the polished `.clio` files in `examples/`.
- [Changelog](CHANGELOG.md) — what's landed in each tag.

## Current status

**v0.11.0 (current)**: **4 compilation targets** (`claude-cli`, `python`, `mcp-server`, `langgraph`). **651 unit tests + 13 gated e2e.**

What's in the language today:
- **Control flow**: sequential chains, `FOR EACH`, `FOR EACH ... PARALLEL AS <name>`, `IF/ELSE`, `MATCH/CASE/DEFAULT`, `WHILE ... MAX N`.
- **Resilience**: `CACHE: ttl(...)`, `ON_FAIL: retry(N) then escalate then fallback(...) then abort(...)`, multi-step `RESCUE` handlers (v0.8).
- **EXACT implementations** (`impl:` block):
  - `impl.mode: rest` — full HTTP (5 body forms: JSON / raw / `@file` / form-urlencoded / multipart, `query`/`headers` templating, `env:NAME` auth, `retry: { backoff: exponential | constant }` with `Retry-After` honored, `response_path` JSONPath).
  - `impl.mode: shell` — argv-style subprocess, optional `parse: json`.
  - `impl.mode: mcp_tool` — call a tool on a configured MCP server (3 transports: `stdio` / `sse` / `http`, long-lived per-server clients, `${var}` templating in args).
  - `impl.mode: sql` — parameterized query against sqlite / postgres / mysql; `:name` bindings auto-translated per driver, multi-row → `List<{...}>` auto-mapped via `cursor.description`, DML rowcount, multi-line `|` block scalar for query bodies.
- **JUDGMENT invocation** (`invoke:` block): `cli` (Claude Code), `api` (Anthropic, OpenAI-compat covering LiteLLM / OpenRouter / Ollama / vLLM), `mcp_sampling` (mcp-server target).
- **Observability**: structured JSONL events (`CLIO_LOG=1`, six event types, OTel-mappable). Replay an `events.jsonl` inside the HTML viewer (`clio graph --format html` then drag-drop the trace).
- **Resume**: `--from-step N` from `state.json` (atomically written after each completed step).

**Phase 2** (future): natural language → `.clio` frontend (the `gen` command is a first cut), `MODE: auto` routing inference, multi-step LangGraph IF / MATCH / WHILE / RESCUE branches, additional EXACT modes (`binary`), `CONFIDENCE` thresholds, `VALIDATE` post-conditions, batching / context-budget / model-routing optimizer.

## License

MIT — see [LICENSE](LICENSE).
