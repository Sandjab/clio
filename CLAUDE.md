# CLAUDE.md — CLIO Compiler

**CLIO**: Compiled Language for Intent Orchestration

## What this project is

A compiler that takes hybrid LLM/code programs written in a declarative language and emits executable projects. The language unifies deterministic code and stochastic LLM calls through three primitives: **STEP** (unit of work), **CONTRACT** (typed guarantee), and **FLOW** (composition). Each STEP declares its `MODE` (`exact` or `judgment`): the compiler emits deterministic code for `exact` steps and LLM-call scaffolding for `judgment` steps. The author chooses the mode — the compiler does not decide, infer, or execute; it emits.

Read `docs/LANGUAGE_SPEC.md` for the full language reference before writing any code.

## Development principles

### 1. Think Before Coding
**Don't assume. Don't hide confusion. Surface tradeoffs. Ask if unsure.** If a design choice has multiple valid answers, don't pick one silently — document the tradeoff and ask. If a requirement is ambiguous, say so before writing code.

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First
**Minimum code that solves the problem. Nothing speculative.** No "we might need this later" abstractions. No placeholder modules. Every file, function, and class must serve a current, tested use case. If you can solve it in 20 lines, don't write 200.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes
**Touch only what you must. Clean up only your own mess.** Don't refactor adjacent code. Don't "improve" files unrelated to the task. If existing code is ugly but working, leave it. Scope discipline is non-negotiable.

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution
**Define success criteria. Loop until verified.** Before implementing, state what "done" looks like — concrete, testable conditions. After implementing, verify against those conditions. If it doesn't pass, iterate. Don't call it done until it is.

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## Architecture overview

Read `docs/ARCHITECTURE.md` for the detailed architecture.

The system has 3 layers:

```
[Source .clio file] → Parser → IR (intermediate representation) → Emitter → [Target project]
```

- **Parser**: reads `.clio` source, produces an IR (a typed AST of STEPs, CONTRACTs, FLOWs)
- **IR**: the internal graph — target-independent, validated, immutable
- **Emitter**: takes the IR and emits a project for a specific target

Each compilation target is a separate emitter module. Emitters are independent. Adding a new target = adding a new emitter. The parser and IR don't change.

## Compilation targets shipped

Six emitters live in `clio/emitters/`. Each takes an IR and writes a runnable project for a different deployment shape:

| Target | Output | Typical use |
|---|---|---|
| `claude-cli` | Bash orchestrator + `.prompt` / `.sh` step files calling `claude -p` | Sketches, demos inside Claude Code |
| `python` | Python package (Anthropic / OpenAI SDKs + Pydantic v2) | Production deployment, OpenAI-compat models |
| `mcp-server` | Python MCP server (one tool per `EXPOSE FLOW`, judgment via `sampling/createMessage`) | Expose the flow to Claude Desktop / IDE / any MCP client |
| `langgraph` | Python package whose `flow.py` builds a `langgraph.graph.StateGraph` | Bridge to LangChain stacks; observability delegated to LangSmith |
| `claude-skill` | Claude Code skill directory (`SKILL.md` + `scripts/` + `prompts/` + `schemas/`) — emits a `.clio/` sidecar (v0.19) so `clio import` can recover the source verbatim | Ship a flow as a host-orchestrated skill, no runtime needed after install |
| `go` | Go module (parity baseline with the Python target, anthropic-sdk-go v1) | Native Go deployment, no Python runtime |
| `swift` | Swift package (SwiftPM, zero external SPM deps, URLSession client, macOS + Linux) | Native Swift deployment, no Python runtime, parallel FOR EACH via `withThrowingTaskGroup` |

Read `docs/COMPILATION_TARGETS.md` for the per-target contracts and `docs/manual/04-targets.md` for the cross-target feature matrix.

## Tech stack

- **Language**: Python 3.12+
- **Contracts**: Pydantic v2 + JSON Schema
- **Parser**: Start with a hand-written recursive descent parser. No parser generators until complexity demands it.
- **Testing**: pytest. Every parser rule and every emitter output gets a test.
- **No frameworks**. No LangChain, no LangGraph, no DSPy. This project IS the framework.

## File structure

```
clio/
  parser/             # .clio source → AST
    lexer.py
    tokens.py
    parser.py
    expressions.py
    ast_nodes.py
  ir/                 # AST → IR graph (validated, target-independent)
    builder.py        # build the FlowGraph (multi-pass)
    graph.py          # FlowGraph + FlowIR / StepIR / FlowCallIR
    resolver.py       # cross-file FROM…IMPORT resolution (v0.18)
    contracts.py      # type_to_json_schema (CONTRACT shape → JSON Schema)
    types.py          # IR type nodes
  emitters/           # IR → target project (7 emitters)
    base.py           # abstract emitter interface
    claude_cli.py     # target: claude-cli
    python.py         # target: python
    mcp_server.py     # target: mcp-server
    langgraph.py      # target: langgraph
    claude_skill.py   # target: claude-skill
    go.py             # target: go (v0.20)
    swift.py          # target: swift (phase 1+)
    _sidecar.py       # .clio/ sidecar writer + hash drift detection (v0.19)
    _python_helpers.py, _mcp_helpers.py, _langgraph_helpers.py,
    _claude_skill_helpers.py, _claude_cli_helpers.py, _shared_utils.py,
    _go_helpers.py, _go_flow_renderer.py, _go_step_renderers.py, _go_runtime_templates.py,
    _swift_helpers.py, _swift_flow_renderer.py, _swift_step_renderers.py, _swift_runtime_templates.py
  runtime/            # snippets copied verbatim into emitted Python projects
    cache.py, logging.py, rest.py, sql.py, mcp_client.py, substitute.py, validate.py
  prompts/            # LLM system prompts loaded by gen/import (v0.19)
  nl_to_clio.py       # NL → .clio   (clio gen)
  skill_to_clio.py    # skill → .clio (clio import, v0.19)
  cli.py              # command-line entry point
  __main__.py         # `python -m clio` module entry point
  keywords.py         # the single keyword/token enum (no magic strings)
  diagnostics.py      # diagnostics + error formatting
  graph_render.py     # FLOW rendering for `clio graph` (Mermaid / DOT / HTML)
  _llm_validation.py  # shared validation helpers for gen/import LLM output
tests/
  fixtures/           # sample .clio files
  test_parser.py
  test_ir.py
  test_emitters/      # one test_<target>.py per emitter
docs/
  LANGUAGE_SPEC.md
  ARCHITECTURE.md
  COMPILATION_TARGETS.md
  POSITIONING.md
  manual/             # user-facing docs (getting-started, tour, cookbook, …)
```

## Conventions

- One module = one responsibility. If a file exceeds ~300 lines, split it.
- All IR nodes are frozen dataclasses or Pydantic models. Immutable by default.
- Emitters never import from each other. They only depend on the IR.
- Error messages must include the source line number from the `.clio` file.
- No magic strings. Keywords live in a single `keywords.py` enum.

## What NOT to build (yet)

- Runtime execution. The compiler emits files; it doesn't run them.
- Multi-LLM routing logic in the compiler. The emitter writes the scaffolding; the runtime decides.
- A package registry or plugin system.
- A VS Code extension or LSP server.
- Dependencies on Guidance, Outlines, or Instructor. Contract validation for API-based targets is trivial (JSON Schema + Pydantic). These libs become relevant only for `target: local` with open-source models — at which point a pluggable validator interface would be the natural seam (none is built today).

(`clio gen` — natural language → `.clio` — and `clio import` — skill → `.clio` — are both shipped. Their LLM-assisted paths live in `clio/nl_to_clio.py` and `clio/skill_to_clio.py`; they are CLI helpers, not part of the compiler core.)

## How to run

```bash
# Compile a .clio file to any of the seven targets
python -m clio compile examples/mvp.clio --target claude-cli   --output ./output
python -m clio compile examples/mvp.clio --target python       --output ./py-out
python -m clio compile examples/mvp.clio --target mcp-server   --output ./mcp-out
python -m clio compile examples/mvp.clio --target langgraph    --output ./lg-out
python -m clio compile examples/skill_minimal.clio --target claude-skill --output ./skill-out
python -m clio compile examples/mvp_go.clio --target go        --output ./go-out
python -m clio compile examples/mvp_go.clio --target swift     --output ./swift-out

# Validate a .clio file without emitting
python -m clio check examples/mvp.clio

# Render the FLOW as a Mermaid (default), DOT, or self-contained HTML viewer
# (the HTML form is a single file: Mermaid + a click-to-inspect side panel
# showing each step's contracts, cache, retry policy, and exec details).
python -m clio graph examples/mvp.clio
python -m clio graph examples/mvp.clio --format dot
python -m clio graph examples/mvp.clio --format html --output graph.html

# Generate a .clio source from natural language (requires `pip install -e .[gen]` and ANTHROPIC_API_KEY)
python -m clio gen "describe a pipeline that ..."
python -m clio gen --from-file desc.txt --output flow.clio --model claude-sonnet-4-6

# Recover a .clio source from an emitted (or hand-written) Claude Code skill (v0.19)
python -m clio import ./skill-out --output recovered.clio          # auto: sidecar if present, else LLM
python -m clio import ./skill-out --mode strict --output recovered.clio  # require sidecar + matching hashes

# Diagnose the host before compiling / running a flow (v0.15)
python -m clio doctor                                 # generic checks
python -m clio doctor examples/critical_pipeline.clio # flow-aware checks (MCP commands on PATH, db URLs)

# Inspect the last python-target run (state.json + tail of CLIO_LOG_FILE) (v0.15)
python -m clio status --state-file ./out/state.json --log-file ./out/events.jsonl --limit 20

# Run tests
pytest tests/ -v
```
