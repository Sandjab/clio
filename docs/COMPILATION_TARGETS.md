# CLIO Compilation Targets

Each target is an emitter module that transforms the IR graph into a runnable project. This document describes what each target emits and the constraints it operates under.

## `target: claude-cli` (Milestone 1)

**What it emits**: a Claude Code project folder.

**Runtime dependency**: Claude Code CLI (`claude` command).

| IR element        | Emitted artifact                                  |
|-------------------|---------------------------------------------------|
| STEP `exact`      | `steps/NN_name.sh` or `steps/NN_name.py`          |
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

---

## `target: python` (Milestone 2)

**What it emits**: a Python package with a CLI entry point.

**Runtime dependency**: Python 3.12+, `anthropic` SDK, `pydantic` v2.

| IR element        | Emitted artifact                                  |
|-------------------|---------------------------------------------------|
| STEP `exact`      | Function in `steps/name.py`                       |
| STEP `judgment`   | Function that calls Anthropic API with Pydantic model as `response_model` |
| CONTRACT          | Pydantic model class in `contracts.py`            |
| FLOW              | `async def run()` in `flow.py` using asyncio      |
| WHILE loop        | `while` loop with state dict                      |
| FOR EACH          | `asyncio.gather()` or sequential loop             |
| MATCH/CASE        | Python `match/case`                                |
| IF/ELSE           | Python `if/else`                                   |
| ON_FAIL/fallback  | `try/except` with retry decorator                  |
| RESOURCES         | Config dataclass in `config.py`                    |
| CACHE             | `@cached_judgment` decorator with hash-based file store |

**Judgment steps**: the emitter generates a function that constructs the prompt from a template, calls `anthropic.messages.create()` with `tools` for structured output, and validates the response against the Pydantic model.

**Contract validation**: Pydantic does both schema definition and runtime validation. No additional library needed — the emitted Pydantic model IS the contract. Retry logic is a simple `for` loop with `try/except ValidationError`.

**Model routing**: the emitter generates a `get_model()` function that reads the strategy from config and returns the appropriate model string.

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
