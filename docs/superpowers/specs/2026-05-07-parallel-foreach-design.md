# `FOR EACH ... PARALLEL AS <name>` (Design)

Status: design approved, ready for implementation plan.
Date: 2026-05-07.

## Goal

Add an explicit way to fan a STEP over a collection in parallel and gather the typed results. The user writes:

```clio
FOR EACH <loop_var> IN <collection> PARALLEL AS <collector>:
  <single_step_call(loop_var)>
```

After the block, `state[<collector>]` is `List<step.gives.type>`, ready for the next step in the FLOW.

This is the smallest concrete answer to "how does CLIO express launching N agents in parallel?" — a single grammar extension, supported by two of the three existing emitters, with no new control-flow primitives beyond what a `FOR EACH` already had.

## Non-goals (v1)

- `PARALLEL(max=N)` configurable cap. v1 hardcodes 10.
- Multi-step body inside `FOR EACH PARALLEL`. v1 = single step call only.
- Nested `PARALLEL` blocks (inner or outer). v1 rejects all nesting.
- Best-effort failure mode (`return_exceptions`-style result list). v1 is fail-fast; users who want best-effort wrap their step in `ON_FAIL: fallback(<sentinel>)`.
- claude-cli support. v1 rejects at compile time with a pointer to `--target python` or `--target mcp-server`.
- Sub-FLOW spawning (a step that *is* a sub-FLOW). Out of v1; pre-requisite for the broader "compiler decides agent vs subroutine" vision in `clio-spec.md`.
- Streaming partial results to the caller. v1 returns the full `List<...>` after the gather completes.

## Architecture

### Strategy: explicit keyword, no optimizer

The user declares parallel intent with the `PARALLEL` keyword. The compiler does not infer parallelism — there is no optimizer in v1, and adding `PARALLEL` is precisely the way to avoid building one prematurely.

This means:
- `FOR EACH x IN xs:` (no `PARALLEL`) keeps its v0 semantics — sequential, no result accumulation. **No breaking change** for existing `.clio` sources.
- `FOR EACH x IN xs PARALLEL AS <name>:` is a new syntactic shape with new semantics: parallel execution + typed result list at `state[<name>]`.

### Mapping `.clio` → emitted code

| `.clio` element | python target | mcp-server target |
|---|---|---|
| `FOR EACH x IN xs PARALLEL AS results:` | `concurrent.futures.ThreadPoolExecutor(max_workers=10)` + `as_completed` | `asyncio.gather(*tasks)` with `asyncio.Semaphore(10)` |
| `body: single_step(x)` | `_ex.submit(step_mod.step, x=...)` | `await step_mod.step(...)` inside an `async def _bound(_x)` |
| `state[results]` | `list` of `step.gives.type` | same |
| Failure handling | `_fut.result()` raises in the `as_completed` loop → `with` exit cancels remaining queued futures | `asyncio.gather` cancels sibling tasks on first exception |

claude-cli emits a compile-time `ValueError` (no runtime path).

## Components

### `clio/parser/ast_nodes.py`

Extend `ForEachBlock` with two optional fields:

```python
@dataclass(frozen=True)
class ForEachBlock:
    loop_var: str
    collection: str
    body: tuple[StepCall | ForEachBlock, ...]
    line: int
    parallel: bool = False
    collector: str | None = None
```

### `clio/parser/parser.py`

Extend `parse_for_each` to optionally consume `PARALLEL AS <ident>` after `IN <collection>`. Add `PARALLEL` and `AS` to the keyword set if not already present.

Parser-level rejections (with line numbers from the source `.clio`):

- `AS` without `PARALLEL` → "AS binding is only valid with PARALLEL — sequential FOR EACH discards results"
- `PARALLEL` without `AS` → "FOR EACH PARALLEL requires an AS <name> binding"

### `clio/ir/graph.py`

Symmetric extension on `ForEachIR`:

```python
@dataclass(frozen=True)
class ForEachIR:
    loop_var: str
    collection: str
    body: tuple[CallIR | ForEachIR, ...]
    line: int
    parallel: bool = False
    collector: str | None = None
```

### `clio/ir/builder.py`

IR-builder validations (each error includes the source line number):

1. `parallel=True` ⟹ `collector is not None` (defensive, parser should already enforce).
2. `parallel=True` ⟹ `len(body) == 1`.
3. The single body element must be a `CallIR` (no nested `ForEachIR`).
4. The called step must have a non-`None` `gives` (otherwise the collector type is undefined).
5. `collector` must be a valid identifier (same rules as TAKES names).
6. `collector` must not collide with any state field already populated upstream in the FLOW chain (loop_var, other collectors, GIVES from prior steps). Track populated names during the linearization that already exists for sequential FOR EACH.
7. `parallel=True` cannot appear inside any ancestor with `parallel=True` (transitive nesting check).

### `clio/emitters/_python_helpers.py`

New module-level helper:

```python
def emit_parallel_for_each_python(
    elem: ForEachIR,
    steps_by_name: dict[str, StepIR],
    indent: str,
) -> str:
    """Emit the ThreadPoolExecutor block for a parallel FOR EACH (python target).
    Returns the full block as a single string with the requested indent."""
```

Output shape (single-line collection lookup, fixed `max_workers=10`):

```python
    _items = state['<collection>']
    _results = [None] * len(_items)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as _ex:
        _futures = {_ex.submit(<step>_mod.<step>, <kwargs>): _i for _i, <loop_var> in enumerate(_items)}
        for _fut in concurrent.futures.as_completed(_futures):
            _idx = _futures[_fut]
            _results[_idx] = _fut.result()
    state['<collector>'] = _results
```

The kwargs rendering reuses the existing `@`-prefix disambiguation: literal kwargs become `repr`'d, state-refs become `state[<name>]`, the loop-var becomes its bare name.

`flow.py` gains an `import concurrent.futures` at module top when any parallel FOR EACH is present.

### `clio/emitters/_mcp_helpers.py`

New module-level helper:

```python
def emit_parallel_for_each_mcp(
    elem: ForEachIR,
    steps_by_name: dict[str, StepIR],
    indent: str,
) -> str:
    """Emit the asyncio.gather block for a parallel FOR EACH (mcp-server target).
    Threads _session into each task. Returns the full block."""
```

Output shape (judgment step variant — the body step is `async def`, takes `_session`):

```python
    _items = state['<collection>']
    _sem = asyncio.Semaphore(10)
    async def _bound_<collector>(_x):
        async with _sem:
            return await <step>_mod.<step>(<kwargs_with_session>)
    state['<collector>'] = await asyncio.gather(*[_bound_<collector>(_x) for _x in _items])
```

If the body step is exact-mode, the `await` and `_session=` are dropped (same logic the sequential path already uses).

`flow.py` gains an `import asyncio` at module top when any parallel FOR EACH is present (asyncio is already imported by the mcp-server target's `__main__.py`, but `flow.py` needs its own import for `Semaphore`).

### `clio/emitters/python.py`, `clio/emitters/mcp_server.py`

The flow-emission walker (`_emit_call` / `_emit_item` and async equivalent) gains a branch:

```python
if isinstance(item, ForEachIR) and item.parallel:
    chain_lines.append(emit_parallel_for_each_<target>(item, steps_by_name, indent))
elif isinstance(item, ForEachIR):
    # existing sequential path
    ...
```

### `clio/emitters/claude_cli.py`

Refuses at compile time with the documented message in the table below.

## Refused at compile time

| Combination | Message |
|---|---|
| `PARALLEL` without `AS` | `FOR EACH PARALLEL requires an AS <name> binding (line N)` |
| `AS` without `PARALLEL` | `AS binding is only valid with PARALLEL — sequential FOR EACH discards results (line N)` |
| Multi-step body | `FOR EACH PARALLEL body must contain exactly one step call in v1 (line N)` |
| Nested ForEachIR in body | `FOR EACH PARALLEL cannot contain nested FOR EACH in v1 (line N)` |
| Body step missing GIVES | `FOR EACH PARALLEL body step '<name>' must have a GIVES (line N)` |
| `collector` collides with state field | `AS '<name>' shadows existing state field; rename the collector (line N)` |
| Nested PARALLEL | `FOR EACH PARALLEL cannot be nested inside another PARALLEL block in v1 (line N)` |
| `--target claude-cli` with any PARALLEL | `claude-cli target does not support FOR EACH PARALLEL; use --target python or --target mcp-server` |

## Runtime semantics

- **Empty collection** (`state[<collection>] == []`) → `state[<collector>] = []`. No error, no iterations dispatched.
- **Order preservation**: collector list is indexed by enumeration order (`_results[_idx] = _fut.result()`). Even though `as_completed` returns futures out of order, the index lookup restores positional order. mcp-server's `asyncio.gather` preserves order natively.
- **Per-iteration ON_FAIL**: each task applies its own ON_FAIL chain (retry/escalate/fallback). The chain is rendered into the step body (Tasks 6 and 7 of the mcp-server emitter already cover this); the parallel block does not interfere.
- **Per-iteration CACHE**: each task does its own cache lookup. No coordination between tasks — two iterations with the same prompt will hit the cache independently (the second waits for the first to write, in the worst case there's a benign double-fetch).
- **Fail-fast**: first definitive failure (after ON_FAIL exhausted) cancels siblings (mcp-server) or lets them complete but raises (python ThreadPoolExecutor — sync threads don't cancel mid-execution). The exception propagates up to `flow.run`'s caller, which is the MCP server's `call_tool` or the python target's CLI.

## Interaction with existing features

- **`_session` threading (mcp-server)**: a single session is shared by all parallel tasks. Each task passes `_session=_session` to its step call. Assumption: the `mcp` Python SDK supports concurrent `session.create_message(...)` calls on the same session. **To verify in the gated e2e test** — if not supported, the v1 falls back to a queued-by-Semaphore-of-1 mode for mcp-server (effectively serial) and we surface this as a known limitation pending an SDK fix.
- **Contracts**: the collector's typed value is `List<step.gives.type>`. Downstream steps that accept `List<T>` for a TAKES with the matching inner type already work via the existing type system.
- **FOR EACH (sequential)**: completely unchanged. v0 sources parse, build, and emit byte-identically.
- **`graph` subcommand**: Mermaid/DOT renderer adds a `[parallel]` annotation on the FOR EACH node when `parallel=True`. Small visual cue; same shape otherwise.

## Output flow.py shape

### python target (sync `def run`)

```python
"""FLOW pipe."""
import concurrent.futures

from .steps import classify as classify_mod
from .steps import load_corpus as load_corpus_mod
from .steps import aggregate as aggregate_mod


def run(**initial: object) -> dict:
    state: dict = dict(initial)
    state['docs'] = load_corpus_mod.load_corpus()
    _items = state['docs']
    _results = [None] * len(_items)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as _ex:
        _futures = {_ex.submit(classify_mod.classify, text=doc): _i for _i, doc in enumerate(_items)}
        for _fut in concurrent.futures.as_completed(_futures):
            _idx = _futures[_fut]
            _results[_idx] = _fut.result()
    state['labels'] = _results
    state['summary'] = aggregate_mod.aggregate(labels=state['labels'])
    return state
```

### mcp-server target (async `async def run`)

```python
"""Async FLOW orchestrator. Auto-generated; do not edit."""
from __future__ import annotations
import asyncio

from .steps import classify as classify_mod
from .steps import load_corpus as load_corpus_mod
from .steps import aggregate as aggregate_mod


async def run(*, _session=None, **initial: object) -> dict:
    state: dict = dict(initial)
    state['docs'] = load_corpus_mod.load_corpus()
    _items = state['docs']
    _sem = asyncio.Semaphore(10)
    async def _bound_labels(_x):
        async with _sem:
            return await classify_mod.classify(text=_x, _session=_session)
    state['labels'] = await asyncio.gather(*[_bound_labels(_x) for _x in _items])
    state['summary'] = aggregate_mod.aggregate(labels=state['labels'])
    return state
```

## Testing strategy

### Parser tests (`tests/test_parser.py`)

- `test_parse_for_each_parallel_with_as` — AST has `parallel=True`, `collector='labels'`.
- `test_parse_for_each_parallel_without_as_fails` — error with line number.
- `test_parse_for_each_as_without_parallel_fails` — error with line number.

### IR tests (`tests/test_ir.py`)

- `test_ir_builds_parallel_for_each` — IR has `ForEachIR(parallel=True, collector='labels')`.
- `test_ir_rejects_multi_step_parallel_body`.
- `test_ir_rejects_nested_for_each_in_parallel_body`.
- `test_ir_rejects_collector_shadowing_state_field`.
- `test_ir_rejects_parallel_body_step_without_gives`.
- `test_ir_rejects_nested_parallel`.

### Python emitter tests (`tests/test_emitters/test_python.py`)

- `test_python_emits_thread_pool_for_parallel_for_each` — emitted `flow.py` contains `concurrent.futures.ThreadPoolExecutor`, `max_workers=10`, `as_completed`.
- `test_python_byte_identical_for_sequential_for_each` — sanity: a non-PARALLEL FOR EACH emits exactly as in v0.

### MCP-server emitter tests (`tests/test_emitters/test_mcp_server.py`)

- `test_mcp_emits_asyncio_gather_for_parallel_for_each` — `asyncio.gather`, `Semaphore(10)`.
- `test_mcp_parallel_judgment_threads_session_per_iteration` — each task passes `_session=_session`.
- `test_mcp_parallel_uses_collector_name_in_state` — `state['<collector>']` is the assignment target.
- `test_mcp_imports_asyncio_when_parallel_present`.

### Claude-CLI emitter tests (`tests/test_emitters/test_claude_cli.py`)

- `test_claude_cli_rejects_parallel` — `ValueError` with the documented message.

### E2E (gated `CLIO_E2E=1`)

- `test_python_parallel_runs_actually_in_parallel` — fixture step sleeps 100 ms; FOR EACH PARALLEL of N=5 finishes in < 5 × 100 ms wall-clock. Proves parallelism is real, not just emitted.
- `test_mcp_parallel_initialize_then_call` (gated `CLIO_MCP_E2E=1`) — extends the existing mcp e2e: compile a FOR EACH PARALLEL fixture, install, send `tools/call` with a small collection, verify response.

## Documentation updates

- `docs/LANGUAGE_SPEC.md`:
  - Extend the `### FOR EACH` section with a `#### PARALLEL` subsection describing the syntax, semantics, and v1 limitations.
  - Add a row to the implementation-status table: `FOR EACH ... PARALLEL` with ✅ for python and mcp-server, ❌ for claude-cli.
- `docs/COMPILATION_TARGETS.md`: update the `python` and `mcp-server` rows / body sections to mention "FOR EACH PARALLEL supported" and call out the cap default.
- `CHANGELOG.md` Unreleased — new "Language" entry.
- `examples/parallel_classify.clio` — short example demonstrating the pattern (5–10 lines, single FLOW with one PARALLEL step).
- `README.md` — one-liner mention in the language features list.

## Open questions intentionally deferred

- **Configurable cap** (`PARALLEL(max=N)`): defer until users hit the 10 wall. Avoids guessing the right default and bloating the grammar prematurely.
- **Best-effort mode**: defer; `ON_FAIL: fallback(...)` covers the use case for now.
- **Multi-step body**: defer; if needed, a future grammar can wrap a sub-FLOW.
- **Nested PARALLEL**: defer until there's a concrete use case. v1 rejection avoids confusing semantics around nested semaphores.
- **claude-cli support** (`xargs -P` + tmpfile JSON aggregation): defer; the bash glue is non-trivial and the python/mcp targets cover the common case.
- **Sub-FLOW spawning**: separate, larger feature; pre-requisite for the "spawn agent with own context" pattern from `clio-spec.md:184–203`.

## Risks

1. **`mcp` SDK concurrent session safety** — assumption: `session.create_message(...)` is safe to call concurrently on a shared session. Validated in the gated e2e test. If false, mcp-server's parallel path collapses to a Semaphore(1) (effectively serial) until the SDK supports it; no syntactic change.
2. **ThreadPoolExecutor + LLM SDKs** — the Anthropic and OpenAI Python SDKs are documented thread-safe; the python target's `emit_judgment_step` already creates a fresh client per call. Should be fine.
3. **Memory pressure for large collections** — N=10000 × 2 KB prompt = 20 MB peak. Acceptable for v1; users with much larger collections are expected to chunk explicitly in their FLOW (no streaming primitive in v1).
4. **claude-cli loss of parity** — users who rely on claude-cli now have a feature their target can't access. Documented refusal points them at python/mcp-server.

## Review checklist (self)

- ✅ Placeholders: none.
- ✅ Internal consistency: syntax, IR, validations, and emitter shapes all reference the same single-step body / fixed-cap / fail-fast contract. No section contradicts another.
- ✅ Scope: single grammar extension, two emitter changes, one helper per target, mechanical IR validation. Single-PR-able.
- ✅ Ambiguity: ordering preservation in the python target's `as_completed` path is explicit (indexed write into a pre-allocated list). Failure mode is explicit per target. Cap is hardcoded with a documented escape hatch in v2.
