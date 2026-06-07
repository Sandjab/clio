# CLIO vs OpenProse — Comparative Analysis

A side-by-side reading of [CLIO](../README.md) and [openprose/prose](https://github.com/openprose/prose). Both projects target the same problem — durable, versionable, composable LLM workflows — but with **opposite philosophies**.

- **CLIO** is a real compiler: `.clio` → executable code (Python / bash / MCP server / Claude skill). The compiler knows whether each step is `EXACT` (nameable code: REST / SQL / shell / MCP tool / code) or `JUDGMENT` (LLM-by-prompt), and emits the right binding.
- **OpenProse** is a contract format interpreted by the LLM agent itself. The host agent (Claude Code, Codex, …) **is** the runtime. No code is emitted. Wiring happens by semantic match between `### Requires` and `### Ensures` (the "Forme" container).

## Comparison grid

| Axis | CLIO | OpenProse |
|---|---|---|
| **Author surface** | `.clio` DSL (Python-style, indented) | Markdown `.prose.md` (YAML frontmatter + `### sections`) |
| **Output** | Code (Python / bash / MCP server / Claude skill) | Nothing — the agent interprets the `.md` directly |
| **Runtime** | Python / bash / MCP — deterministic | LLM agent (Claude Code, Codex, …) |
| **Wiring** | Explicit: `step_a -> step_b(arg=x)` | Implicit: Forme matches `### Requires` ↔ `### Ensures` by meaning |
| **Type system** | Pydantic v2 + JSON Schema, `enum(...)`, `str(max=200)`, records | Natural language: `report: concise answer with sources` |
| **EXACT vs JUDGMENT** | **Central primitive.** Compiler knows whether to emit a REST / SQL / shell / MCP tool / code call or an LLM-prompt invocation | Everything is a subagent (each service = one `spawn_session`) |
| **Control flow** | `IF / ELSE` with `and`/`or`, `MATCH / CASE`, `WHILE MAX N`, `FOR EACH PARALLEL` — validated at parse time | ProseScript with natural-language conditions (`if review has critical concerns:`) |
| **Validation** | Parse-time (with `<file>:<line>:<col>`) + runtime (Pydantic / JSON Schema) | LLM "judges" conformance to the contract |
| **Persistence** | `state.json` + `--from-step N` (resume) | `runs/{id}/`, `state/`, `state/agents/`, `state/responsibilities/` |
| **Observability** | JSONL events, OTel-mappable (`CLIO_LOG=1`) | `runs/{id}/vm.log.md` + `prose status` |
| **Cache** | Deterministic, SHA256(prompt+schema+model), `ttl(24h)` | No deterministic cache (LLM-replay only) |
| **Multi-target** | python, claude-cli, mcp-server, claude-skill, langgraph, go (6 shipped) | Single target: the host agent |
| **Failure / recovery** | `ON_FAIL: retry(3) then escalate then fallback(x)` + `RESCUE` (`abort` / `RESUME`) | `### Errors` declarative + contract-driven retry |
| **Tests** | 1281+ pytest tests *of the compiler* | `kind: test` native, with `### Expects` / `### Expects Not` in natural language |
| **Dependencies** | Zero external (each `.clio` self-contained) | `prose install` + `prose.lock` + `deps/` (git-native) |
| **Reusable units** | `STEP` / `CONTRACT` reused manually across files | `kind: pattern` (slots + config + delegation), `std/` and `co/` packages |
| **External ingestion** | No abstraction | `kind: gateway` (cron, webhook, HTTP route) |
| **Standing goals** | None (run-once flows) | `kind: responsibility` + Reactor (event-driven continuity) |
| **Stack** | Python 3.12+ | TypeScript CLI wrapper + skill |
| **Maturity** | v0.24.0, 1351+ tests, 7 targets shipped, cross-file imports + skill ↔ `.clio` round-trip | Beta, ~19 open issues, MIT |

## Deep similarities

1. **Intent / execution separation** — declarative contract, runtime handles "how".
2. **Explicit refusal of LangChain / CrewAI** — orchestration libraries vs language primitives.
3. **Auditability** — CLIO JSONL logs ≈ OpenProse `runs/{id}/` receipts.
4. **Multi-LLM routing** — CLIO `models:[haiku,sonnet,opus] strategy:escalate` ≈ OpenProse `### Runtime: model:`.
5. **Markdown as first-class citizen** — CLIO emits `CLAUDE.md` and skill manifests; OpenProse uses `.prose.md` as the source itself.

## What CLIO does — and OpenProse does not

1. **Real multi-target compiler** (Python SDK, bash via `claude-cli`, MCP server, Claude skill, LangGraph `StateGraph`, Go module).
2. **EXACT vs JUDGMENT distinction** — the central primitive. `impl: code / rest / shell / mcp_tool / sql` vs `invoke: cli / api`. OpenProse routes *everything* through a subagent.
3. **Formal type system** — `List<{client: str, risque: enum(low|mid|high)}>` with Pydantic validation. OpenProse: prose-only contracts.
4. **Parse-time validation with line numbers** — errors like `<file>:<line>:<col>` instead of "the LLM didn't like it".
5. **Deterministic hash-keyed cache** + TTL — guaranteed reproducibility.
6. **First-class concrete backends**: SQL (sqlite / postgres / mysql), REST with retry / `Retry-After`, MCP tool, shell, inline code.
7. **`FOR EACH PARALLEL`** with `ThreadPoolExecutor` / `asyncio.gather`, cap = 10.
8. **`clio graph --format html`** — interactive viewer with side panel inspection per step.
9. **`clio gen`** — NL → `.clio` (assisted authoring).
10. **Resume `--from-step N`** with flow-name validation.

## What OpenProse does — and CLIO does not

1. **Markdown as the canonical format** — no DSL syntax to learn; LLMs read it natively.
2. **The agent IS the runtime** — zero re-compile, no emitter to maintain per target.
3. **Forme: semantic auto-wiring** — no need to trace the DAG by hand.
4. **`kind: pattern`** — parameterizable, instantiable units with slots / config / delegation.
5. **`kind: test`** with `### Expects` / `### Expects Not` in natural language.
6. **`kind: gateway`** — declares ingestion (cron, webhook, HTTP route).
7. **`kind: responsibility`** — standing goals + Reactor (continuous event-driven).
8. **Native subagent isolation**: `spawn_session` + copy-on-return (private `workspace/` → public `bindings/`).
9. **Persistent agents** (`persist: project | user`) — declared cross-run memory.
10. **Declared `### Memory`** — explicit reads/writes into agent memory.
11. **Dependency management**: `prose install`, `prose.lock`, packages `std/` and `co/`.
12. **Multiple services per file** via `## name` headings.
13. **`prose doctor`** — environment diagnostic.
14. **Mid-session runtime delegation** — services-as-coroutines via `Delegate:`.

## Narrative positioning

OpenProse bets that **LLMs will become reliable enough to act as universal runtimes**: the contract suffices, the simulator does the rest.

CLIO bets the opposite: **LLMs are stochastic, so anything that can be determined at compile time *must* be**. The compiler emits "real" code (Python, bash, MCP server), with Pydantic contracts and a hash-keyed cache. The LLM only steps in when there is no alternative — and always under schema constraints.

Both are defensible.

- **CLIO** is better positioned for **production workflows** (determinism, observability, multi-target, parseable error trails).
- **OpenProse** is better positioned for **research / fast-iteration workflows** (zero re-compile, living Markdown, semantic wiring).

## Cross-pollination — what CLIO adopts from OpenProse

This document drove a concrete sprint (PR `feat/openprose-inspired-improvements`, merged):

| Idea borrowed | CLIO change |
|---|---|
| `prose doctor` | New `clio doctor` subcommand: checks Python version, env vars, MCP servers, DBs declared in a flow |
| `### Description` / `### Strategies` | Optional `DESCRIPTION:` and `STRATEGIES:` fields per STEP, injected into the system prompt of `JUDGMENT` steps |
| `prose status` | New `clio status` subcommand: last run summary from `state.json` + recent `CLIO_LOG` events |
| `kind: test` | New `TEST` top-level block with `FLOW`, `WITH`, `EXPECTS`, `EXPECTS_NOT` — emitted as pytest in the `python` target |
| Multiple services per file | Allow multiple top-level `FLOW` declarations in one `.clio` file, with a CLI `--flow <name>` selector |

Deliberately **not** adopted (for now):

- **Forme-style auto-wiring** — sacrifices parse-time validation. Our explicit wiring stays.
- **Natural-language conditions** — non-deterministic and non-testable. Our grammar (`report.confidence < 0.7 and ...`) is strictly more inspectable.
- **"LLM as simulator" doctrine** — we keep the compiler as a pure function; the LLM is a constrained component, not a runtime.
- **Markdown as canonical format** — we keep `.clio` as the source of truth; we already emit Markdown artifacts where useful (`CLAUDE.md`, skill manifest).

Shipped after this comparison was first drafted:

- **`FROM "<path>" IMPORT <name>` + `EXPOSE`/`INTERNAL` visibility markers** (v0.18) — cross-file sharing of `FLOW`s and `CONTRACT`s. Stops short of a lockfile / registry on purpose (W1 anti-pattern in `POSITIONING.md`).
- **`clio import <skill-dir>`** (v0.19) — round-trip from an emitted Claude Code skill back to its `.clio` source (verbatim via `.clio/` sidecar if hashes match, LLM-assisted otherwise). Closest thing CLIO has to OpenProse's "the artifact IS the source".

Still held for later evaluation:

- `PATTERN` parameterizable units.
- Triggers / `target: cron` (Responsibility Runtime equivalent).
