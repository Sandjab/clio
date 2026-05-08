# Structured JSON-Line Logging (Design)

Status: design approved, ready for implementation plan.
Date: 2026-05-08.
Addresses: POSITIONING.md W2 (short-term).

## Goal

Emit one structured JSON-line event per step (and per FLOW, and per PARALLEL block) from compiled CLIO projects, so that emitted code becomes observable without depending on a CLIO runtime, a CLIO dashboard, or a vendor SDK. Open standards (JSON Lines, ASCII timestamps) ensure the output integrates with whatever the team already runs (`grep`, `jq`, Datadog, Honeycomb, Tempo, Langfuse, …).

The event stream is a **strict superset** of what POSITIONING.md prescribes for the short-term horizon: `start`, `end`, `duration`, `cache_hit`, `model`, plus `tokens_in`/`tokens_out` (when the provider exposes them). Cost computation is deliberately deferred to v2.

This is also the foundational substrate W5 needs — `clio resume --from-step N` will read the same event stream to know which steps already ran. Shipping W2 first is therefore the right ordering.

## Non-goals (v1)

- **OpenTelemetry-native spans** (`trace_id`, `span_id`, `parent_span_id`, OTLP timestamps). Mid-term per POSITIONING.md. v1 schema is intentionally **OTel-mappable** by an aval converter, but flat.
- **`estimated_cost_usd` field**. Requires a per-model/per-provider pricing table that drifts. v1 logs `model` + `tokens_in`/`tokens_out`; cost computation is an aval job.
- **claude-cli emitter instrumentation**. claude-cli is positioned as "fast prototype"; v1 covers `python` + `mcp-server` only (the two production targets).
- **Vendor decorators** (`--observability=langfuse`). Long-term per POSITIONING.md.
- **Sub-step spans** (one span per LLM attempt during escalate, one span per cache lookup). v1 = one step = one start/end pair.
- **Step IDs / call IDs**. When `FOR EACH PARALLEL` calls the same step 24×, distinguishing pairs relies on chronology + thread of execution. If strong identity is needed downstream, it's a signal to move to OTel spans (mid-term).
- **Log levels** (DEBUG/INFO/WARN). All events are emitted at one level; consumers filter by `event` field.
- **stdout output**. Reserved for `json.dump(result)` of the emitted `__main__.py`. Logs go to stderr or a file, never stdout.

## Architecture

### Strategy: open standards, no runtime dependency

The compiler emits a small stand-alone module (`clio_runtime/logging.py`) into every generated project, exactly the same pattern that ships `clio_runtime/cache.py` today. The emitted project depends on **nothing** at runtime — no CLIO library, no observability SDK. A consumer who wants OTel spans, a Langfuse trace, or a Datadog event runs the JSON-Lines stream through their existing pipeline.

The module is **inert when `CLIO_LOG` is unset**: a single env-var lookup short-circuits to `return` before any work. Emitted projects compile-time-include the logging code regardless of activation; runtime decides whether anything is written.

### Mapping `.clio` → emitted code

Every emitted project gets:

```
<pkg_name>/
  clio_runtime/
    cache.py        # existing
    logging.py      # NEW — copied verbatim from clio/runtime/logging.py
  flow.py           # MODIFIED — set_flow + flow_start/flow_end
  steps/
    <step>.py       # MODIFIED — step_start/step_end (3 return paths for judgment)
```

Same skeleton for `target: mcp-server` (with async `flow.py`).

## Activation and destination

- **Toggle**: `CLIO_LOG=1` enables emission. Anything else (unset, `0`, empty, `false`) → no-op. Cohérent with the existing `CLIO_CACHE_DIR` convention.
- **Destination**: `sys.stderr` by default. If `CLIO_LOG_FILE=path/to/run.jsonl` is set, the file is opened in append mode (created on demand) and lines are written there instead. The file handle is cached in a module-level closure to avoid per-event open/close.
- **Atomicity**: each `emit()` call serializes the JSON payload, appends `\n`, and writes in one call. For typical event sizes (<500 bytes) this is atomic on POSIX (<= PIPE_BUF = 4KB) and on local filesystems. Concurrent threads/asyncio tasks therefore produce uncorrupted lines.

## Public surface of `clio_runtime/logging.py`

```python
def emit(event: str, **fields) -> None: ...
def set_flow(name: str | None) -> None: ...
```

Internals (~60 lines total):
- `_enabled()` — `os.environ.get("CLIO_LOG") == "1"`.
- `_destination()` — closure over `CLIO_LOG_FILE` decision; opens the file once on first use.
- `_now()` — `datetime.now(timezone.utc).isoformat(timespec='milliseconds')`.
- `_current_flow` — `contextvars.ContextVar("clio_flow", default=None)`. Thread-safe **and** asyncio-task-safe (each task inherits/overrides independently).
- `emit()` builds `{"ts": _now(), "event": event}`, then injects `flow` from the ContextVar (unless caller passed `flow=` explicitly), then merges remaining fields, serializes, writes. **Swallows all write errors** — logging never breaks a flow.

The full implementation of `emit()` is intentionally one tight function, no abstraction layers. Tests cover behavior, not internals.

## Schéma JSON-line v1

All events carry `ts` (ISO 8601 UTC, ms precision) and `event` (string). Per-event additional fields:

### `flow_start`
```json
{"ts": "2026-05-08T14:30:00.000+00:00", "event": "flow_start", "flow": "extract_entities"}
```

### `flow_end` (emitted from a `finally` clause in `run()`)
```json
{"ts": "...", "event": "flow_end", "flow": "extract_entities",
 "duration_ms": 4523, "success": true}
```
`success: false` when an exception propagated out of `run()`.

### `step_start` (first line of each generated step body)
```json
{"ts": "...", "event": "step_start", "step": "classify_doc",
 "flow": "extract_entities", "mode": "judgment"}
```
`flow` comes from the ContextVar set by `flow.run()`; absent if the step is invoked outside a flow context.

### `step_end` (emitted before each return path of the step)
```json
{"ts": "...", "event": "step_end", "step": "classify_doc",
 "flow": "extract_entities", "mode": "judgment",
 "duration_ms": 1234, "success": true,
 "cache_hit": false, "model": "claude-haiku-4-5",
 "fallback_used": false,
 "tokens_in": 450, "tokens_out": 87}
```
Field rules:
- `cache_hit`: bool, present for judgment steps only.
- `model`: string for judgment, absent for exact, `null` if abort happened before any call.
- `fallback_used`: bool, present for judgment steps with a `fallback` strategy in `ON_FAIL`.
- `tokens_in`/`tokens_out`: int, present iff the provider response surfaced `usage`. Absent on cache hit (the cache stores the validated output, not the original usage). Absent for exact steps. Absent in `mcp-server` target unless the MCP sampling response includes a usage field.
- `success: false` is emitted from the abort path right before `raise SystemExit(1)`.
- For exact steps (rest, shell, code), the schema is reduced: `mode="exact"`, no `model`, no `cache_hit`, no `fallback_used`, no `tokens_*`.

### `parallel_block_start` (before the worker pool in a `FOR EACH ... PARALLEL` block)
```json
{"ts": "...", "event": "parallel_block_start", "flow": "...",
 "step": "classify_one", "collector": "classifications",
 "total_iterations": 24, "max_workers": 10}
```

### `parallel_block_end` (after the gather)
```json
{"ts": "...", "event": "parallel_block_end", "flow": "...",
 "step": "classify_one", "collector": "classifications",
 "total_iterations": 24, "duration_ms": 8420, "success": true}
```
`success: false` if any task raised (fail-fast — first exception propagates).

Inside the parallel block, each task emits its own `step_start`/`step_end` concurrently. They are not nested under the parent in v1; consumers reconstruct the hierarchy by chronological enclosure (a `step_start` whose timestamp falls between a `parallel_block_start` and matching `parallel_block_end` is "in" the block).

## Points d'injection

### `target: python` — `flow.py`
- Add `import time` and `from .clio_runtime import logging as _log`.
- Wrap the body of `run()`:
  ```python
  def run(**initial: object) -> dict:
      state: dict = dict(initial)
      _log.set_flow("<flow_name>")
      _log.emit("flow_start")
      _success = False
      _t0 = time.monotonic()
      try:
          # ... existing chain_lines ...
          _success = True
          return state
      finally:
          _log.emit("flow_end",
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    success=_success)
          _log.set_flow(None)
  ```

### `target: python` — `steps/<name>.py` (judgment)
- Add `import time` (or hoist if already present) and `from ..clio_runtime import logging as _log`.
- First lines of the step body:
  ```python
  _t0 = time.monotonic()
  _log.emit("step_start", step="<name>", mode="judgment")
  ```
- Three return paths instrumented with `step_end`:
  1. **cache hit**: `cache_hit=True`, `model=_MODELS[0]`, `fallback_used=False`, `success=True`, no `tokens_*`.
  2. **success after `_attempt`**: `cache_hit=False`, `model=_MODELS[model_idx]`, `fallback_used=<flag>`, `success=True`, `tokens_in/out` if `_last_usage` is populated.
  3. **abort path**: `cache_hit=False`, `model=_MODELS[model_idx]`, `fallback_used=False`, `success=False`, no `tokens_*`.

The step body maintains a local `_last_usage: dict = {}` initialized before `_attempt` is called. `_attempt` is extended (in `_emit_attempt_block`, per provider) to populate `_last_usage` as a side effect by `nonlocal`-binding it from the enclosing function's scope before the call. Specifically:
- Anthropic: `_last_usage["tokens_in"] = response.usage.input_tokens; _last_usage["tokens_out"] = response.usage.output_tokens` after a successful create.
- OpenAI: same with `response.usage.prompt_tokens` / `response.usage.completion_tokens`.
- On `_attempt` returning `None` (validation/parse failure): `_last_usage` is left empty (the previous attempt's values, if any, are not reused).

The `step_end` call then expands the dict: `_log.emit("step_end", ..., **_last_usage)`. When `_last_usage == {}`, no `tokens_*` keys appear in the payload. This keeps the schema's "absent vs null" rule clean.

Side-effect over return-tuple chosen to minimize the diff in `_emit_attempt_block` (which is shared across all judgment steps). Tuple-return would force every `_attempt` caller across the codebase to unpack — much wider blast radius.

### `target: python` — `steps/<name>.py` (exact)
- Same `time` + `_log` imports.
- Single `step_start` at top, single `step_end` before the lone `return`. `mode="exact"`. Errors from rest/shell propagate naturally and are caught by `flow_end`'s `success: false`.

### `target: python` — `_python_helpers.emit_parallel_for_each_python`
- Wrap the `with ThreadPoolExecutor(...)` block:
  ```python
  _log.emit("parallel_block_start", step="<inner_step>",
            collector="<collector>", total_iterations=len(_items),
            max_workers=10)
  _pblock_t0 = time.monotonic()
  _pblock_success = False
  try:
      # existing pool code
      _pblock_success = True
  finally:
      _log.emit("parallel_block_end", step="<inner_step>",
                collector="<collector>", total_iterations=len(_items),
                duration_ms=int((time.monotonic() - _pblock_t0) * 1000),
                success=_pblock_success)
  ```

### `target: mcp-server` — strictement parallèle
- `_emit_flow_module_async` injects the same `set_flow` + `flow_start`/`flow_end` envelope around `async def run(...)`.
- `emit_judgment_step_via_sampling` injects `step_start`/`step_end` for the sampling path. `model` is taken from the sampling response (`result.model` or equivalent); `tokens_in/out` emitted iff the response carries usage.
- The PARALLEL block under `asyncio.gather` + `asyncio.Semaphore(10)` gets the same `parallel_block_*` envelope. ContextVar continues to track the flow per asyncio task.

### `target: claude-cli`
**Not modified in v1.** The bash + prompt-files architecture would require either a sourced bash logging helper or a python helper script invoked from each step. Out of scope — v2.

## Tests

### `tests/test_runtime_logging.py` (new)
Unit-tests `clio/runtime/logging.py` directly. Coverage:
- `CLIO_LOG` unset → `emit()` is no-op (no stderr write, no file open). Use `monkeypatch.delenv` + `capsys`.
- `CLIO_LOG=1`, no `CLIO_LOG_FILE` → writes to stderr. Each line parses as JSON. Required keys present.
- `CLIO_LOG=1`, `CLIO_LOG_FILE=tmp_path / "x.jsonl"` → file created, mode append (write twice, count lines).
- `set_flow("foo")` then `emit("step_start", step="x")` → `"flow":"foo"` in payload.
- `set_flow(None)` → no `flow` key.
- Caller `emit("flow_start", flow="explicit")` → caller's value wins over ContextVar.
- Erreur d'écriture (file path with non-existent directory) → `emit()` does **not** raise.
- `ts` format regex: `\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00`.
- Concurrency: 100 threads × 100 events → 10000 valid JSON lines, no corruption.
- ContextVar isolation across asyncio tasks: two tasks set different `flow` values, their events carry their own value.

### Extensions à `tests/test_emitters/test_python.py`
Form-based assertions on emitted code (no execution):
- Compiled flow.py contains `from .clio_runtime import logging as _log`, `import time`, the `set_flow` call, and the `flow_start`/`flow_end` pair in `try/finally`.
- Compiled `steps/<judgment>.py` contains 3 `step_end` calls (cache hit / success / abort paths) — count via regex.
- Compiled `steps/<exact>.py` contains exactly 1 `step_start` and 1 `step_end` with `mode="exact"`, no `model=` or `cache_hit=`.
- Compiled flow with PARALLEL contains `parallel_block_start` and `parallel_block_end` calls in `_python_helpers.emit_parallel_for_each_python`.
- `clio_runtime/logging.py` is copied byte-equal to the source `clio/runtime/logging.py` (similar to existing `cache.py` test).
- Sequential-only flow (no PARALLEL) does **not** contain `parallel_block_*` calls.

### Extensions à `tests/test_emitters/test_mcp_server.py`
Same shape as the python test, on the async flow.py and the sampling-based judgment step.

### `tests/test_e2e_logging.py` (new, gated `CLIO_E2E=1`)
End-to-end execution of a compiled flow with logging enabled:
- Compile `examples/entities.clio` (or fixtures).
- `subprocess.run([...], env={**os.environ, "CLIO_LOG": "1", "CLIO_LOG_FILE": str(tmp_path/"log.jsonl")})`.
- Parse the file as JSONL. Asserts:
  - At least 1 `flow_start`, exactly 1 `flow_end` (`success: true`).
  - Each step has 1 `step_start` + 1 `step_end`.
  - `flow_end.duration_ms >= max(step_end.duration_ms)` (loose lower bound).
  - All events have valid `ts` matching ISO format.
- Variant gating PARALLEL: same with a flow containing PARALLEL, asserts presence of `parallel_block_start`/`parallel_block_end`.

### Non-regression tests
- A flow compiled and run **without** `CLIO_LOG` exhibits identical behavior to pre-W2 main: same stdout (`json.dump(result)`), same exit code, no extra stderr noise. Assert empty stderr (modulo unrelated noise from upstream code).

## Risques et mitigation

| Risque | Mitigation |
|---|---|
| `_log.emit()` raises and breaks a production flow | All `_write` errors swallowed inside `emit()` (try/except Exception). Test 5.1 dedicated. |
| Concurrent writes corrupt lines | Single-call `write(line + "\n")` is atomic ≤ PIPE_BUF (4KB). JSON payloads stay <500B. Test 5.1 with 100 threads validates. |
| `CLIO_LOG_FILE` invalid path | Caught by the swallow above — flow continues, no log written, no crash. |
| Steps invoked outside `flow.run()` (direct import) | `_current_flow.get()` returns `None`; `flow` field absent from payload. Documented. |
| `ContextVar` not propagating to `ThreadPoolExecutor` workers | `concurrent.futures.ThreadPoolExecutor` does **not** propagate `ContextVar` by default — submitted tasks run in fresh threads that see the default value. Mitigation: wrap each submitted task with `contextvars.copy_context().run(target_fn, *args, **kwargs)`. The copy snapshots `_current_flow` set by `flow.run()` and propagates it to the worker. Implemented inside `emit_parallel_for_each_python` so callers don't see it. asyncio.gather (mcp-server target) propagates ContextVar natively — no special handling. |
| Tokens missing for some providers | Field optional — absent when not provided, never `null`. Documented. |

## Net diff estimé

- **New**: `clio/runtime/logging.py` (~60 LOC), `tests/test_runtime_logging.py` (~150 LOC), `tests/test_e2e_logging.py` (~80 LOC).
- **Modified**: `clio/emitters/_python_helpers.py` (~80 LOC added across step + parallel emitters), `clio/emitters/_mcp_helpers.py` (~70 LOC), `clio/emitters/python.py` (minor, integration), `clio/emitters/mcp_server.py` (minor).
- **Test extensions**: `tests/test_emitters/test_python.py`, `tests/test_emitters/test_mcp_server.py` (~50 LOC each).
- **Docs**: `docs/LANGUAGE_SPEC.md` (note on observability env vars), `docs/COMPILATION_TARGETS.md` (logging section per target), `CHANGELOG.md`.
- **Total**: ~400 LOC source + ~400 LOC tests, distributed across ~10 files.

## Migration et compatibilité

- **Backwards compatible**: existing `.clio` sources compile to projects whose runtime behavior is identical when `CLIO_LOG` is unset. The only diff in emitted code is added imports and instrumentation calls that are no-ops at runtime.
- **No breaking change to compiled artifacts**: `pyproject.toml`, `__main__.py`, `contracts.py` unchanged.
- **No new dependencies**: `clio_runtime/logging.py` uses `os`, `sys`, `json`, `datetime`, `contextvars` from stdlib.

## Order of execution (handed to writing-plans)

A reasonable TDD order (the writing-plans skill will refine):
1. `clio/runtime/logging.py` + unit tests.
2. python emitter: flow.py instrumentation + emitter tests.
3. python emitter: judgment step instrumentation + emitter tests.
4. python emitter: exact step instrumentation + emitter tests.
5. python emitter: PARALLEL block instrumentation + emitter tests.
6. mcp-server emitter: same five sub-steps adapted to async.
7. E2E gated test.
8. Docs (LANGUAGE_SPEC, COMPILATION_TARGETS, CHANGELOG, README).

Each step ships green tests before the next starts.
