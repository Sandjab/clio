# CLAUDE.md — CLIO Compiler

**CLIO**: Compiled Language for Intent Orchestration

## What this project is

A compiler that takes hybrid LLM/code programs written in a declarative language and emits executable projects. The language unifies deterministic code and stochastic LLM calls through three primitives: **STEP** (unit of work), **CONTRACT** (typed guarantee), and **FLOW** (composition). The compiler decides what runs as code and what runs as LLM, based on the `MODE` field (`exact`, `judgment`, `auto`).

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
- **IR**: the internal graph — target-independent, validated, optimizable
- **Emitter**: takes the IR and emits a project for a specific target

Each compilation target is a separate emitter module. Emitters are independent. Adding a new target = adding a new emitter. The parser and IR don't change.

## Current milestone: `target: claude-cli`

The first emitter. It takes an IR and emits a Claude Code project folder:

```
output/
  CLAUDE.md
  .claude/hooks.json
  steps/
    01_step_name.sh          # exact steps
    02_step_name.prompt       # judgment steps
    02_step_name.schema.json  # contract schemas
  run.sh                      # orchestrator
```

Read `docs/COMPILATION_TARGETS.md` for target-specific details.

## Tech stack

- **Language**: Python 3.12+
- **Contracts**: Pydantic v2 + JSON Schema
- **Parser**: Start with a hand-written recursive descent parser. No parser generators until complexity demands it.
- **Testing**: pytest. Every parser rule and every emitter output gets a test.
- **No frameworks**. No LangChain, no LangGraph, no DSPy. This project IS the framework.

## File structure

```
clio/
  parser/          # .clio source → IR
    lexer.py
    parser.py
    ast_nodes.py
  ir/              # intermediate representation
    graph.py       # the flow graph
    contracts.py   # contract validation
    optimizer.py   # batching, context budget, model routing
  emitters/        # IR → target project
    base.py        # abstract emitter interface
    claude_cli.py  # target: claude-cli
  cli.py           # command-line entry point
tests/
  fixtures/        # sample .clio files
  test_parser.py
  test_ir.py
  test_emitters/
    test_claude_cli.py
docs/
  LANGUAGE_SPEC.md
  ARCHITECTURE.md
  COMPILATION_TARGETS.md
```

## Conventions

- One module = one responsibility. If a file exceeds ~300 lines, split it.
- All IR nodes are frozen dataclasses or Pydantic models. Immutable by default.
- Emitters never import from each other. They only depend on the IR.
- Error messages must include the source line number from the `.clio` file.
- No magic strings. Keywords live in a single `keywords.py` enum.

## What NOT to build (yet)

- Natural language → FLOW parser (the LLM-powered "gradual" compiler). That's Phase 2.
- Runtime execution. The compiler emits files; it doesn't run them.
- Multi-LLM routing logic. The emitter writes the scaffolding; the runtime decides.
- A package registry or plugin system.
- A VS Code extension or LSP server.
- Dependencies on Guidance, Outlines, or Instructor. Contract validation for API-based targets is trivial (JSON Schema + Pydantic). These libs become relevant only for `target: local` with open-source models — not day 1. Keep a `ContractValidator` interface in the emitter for future pluggability.

## How to run

```bash
# Parse and compile a .clio file to claude-cli target
python -m clio compile examples/analyse.clio --target claude-cli --output ./output

# Run tests
pytest tests/ -v

# Validate a .clio file without emitting
python -m clio check examples/analyse.clio
```
