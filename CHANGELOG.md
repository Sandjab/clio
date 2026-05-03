# Changelog

## v0.2.0 — 2026-05-03

Adds reproducibility (`CACHE`) and resilience (`ON_FAIL`) on `judgment` steps.

### Language

- `CACHE: on | off | ttl(<int><s|m|h|d>)` — judgment steps only.
- `ON_FAIL: <strategy> (then <strategy>)*` — judgment steps only. Strategies:
  - `retry(N)` — N additional attempts on the current model
  - `escalate` — one attempt on the next model in `RESOURCES.models`
  - `fallback(<step_name>)` — run a different STEP with identical TAKES/GIVES; cycles rejected at compile time
  - `abort("<msg>")` — stop with a clear error
- Fallback compat is checked structurally at IR build time (TAKES name+type and GIVES name+type must match).
- Implicit abort if all strategies are exhausted without a terminal `abort`.

### Runtime

- New `clio_runtime/cache.py` (key = SHA256 over step+model+rendered_prompt+inlined_schema). Atomic file writes. Project-local `.cache/` (override via `CLIO_CACHE_DIR`).
- `run.sh` gains a `_clio_run_attempt` bash helper at the top (one definition per emitted project).
- Bash variable names are now suffixed with the step index (`PROMPT_02`, `RESPONSE_02`, …) to avoid collision in multi-judgment-step flows.

### Tests

- 12 new unit tests for `cache.py`.
- Parser, IR, and emitter tests for CACHE, ON_FAIL, and fallback resolution.
- E2E test now validates that a second run within TTL produces zero `claude -p` invocations (verified via PATH-stub).

### Out of scope (planned for later)

`ON_FAIL` on `MODE: exact`, `CONFIDENCE`, `VALIDATE`, control-flow keywords (`FOR EACH`/`WHILE`/`IF`/`MATCH`), the optimizer, alternative emitter targets, NL → `.clio` frontend.

## v0.1.0 — 2026-05-03

First runnable slice. Compiles a strict subset of the CLIO language to a Claude Code project that runs end-to-end against `claude -p`.

### Language

- **Declarations**: `STEP`, `CONTRACT`, `FLOW`, `RESOURCES`
- **Step fields**: `TAKES`, `GIVES`, `MODE` (`exact` | `judgment`); duplicate fields rejected
- **Contract fields**: `SHAPE`, `ASSERT` (mini expression language: `len(x) > N`, `==`, `!=`, `>=`, `<=`, `>`, `<`, with literal int / float / str)
- **Types**: primitives (`int`, `float`, `str`, `bool`), `List<T>`, records `{f: T, ...}`, `enum(a|b|c)`, `str(max=N)`, `CSV` alias, contract refs by name
- **Flow**: sequential chain `->`, step calls with kwargs (string literals + state references)
- **Resources**: `target` (`claude-cli` only), `models` (first one becomes `claude -p --model`)

### Compiler

- Hand-written recursive-descent parser, frozen-dataclass AST and IR, single emitter for `claude-cli`. Zero LLM-framework dependencies.
- Inter-step type-checking on the FLOW chain.
- Errors carry `line:col` from the source.
- CLI: `python -m clio compile <source.clio> --target claude-cli --output <dir>` and `python -m clio check <source.clio>`.

### Emitted project

- `CLAUDE.md`, `.claude/hooks.json` (placeholder), `contracts/<name>.schema.json`, `steps/NN_name.{py,prompt,schema.json}`, `clio_runtime/{validate,substitute}.py`, `state.json`, `run.sh`, `README.md`.
- `run.sh` orchestrator: bash, dynamic Python 3.12+ detection (override via `PYTHON=`), state passed via `state.json` + `jq`, judgment steps invoke `claude -p --model <model> --output-format text` with the schema inlined directly into the prompt; markdown code-fences are stripped from the response before validation.
- Contract validation: `jsonschema.Draft202012Validator` + the `referencing` library (no deprecated `RefResolver`); `x-clio-assert` is a closed JSON-AST evaluated by an explicit walker (no `eval`).

### Tests

- 61 unit + golden-file tests run by default.
- `tests/test_e2e.py` is gated by `CLIO_E2E=1` and exercises the full pipeline against a real `claude -p`. Manually verified.

### Out of scope (planned for later)

`MODE: auto`, `LANG: auto`, `CACHE`, `VALIDATE`, `ON_FAIL`, `CONFIDENCE`, `FOR EACH`, `WHILE`, `IF` / `ELSE`, `MATCH`, the optimizer (batching, model routing, context budget), other emitter targets (`python`, `docker`, `rust`, `hybrid`), the natural-language → `.clio` frontend, hooks-based validation. The parser rejects each of these explicitly with a clear "not yet supported in v0.1" error rather than silently ignoring.
