# `clio resume` — Step-Granularity Resume (Design)

Status: design approved, ready for implementation plan.
Date: 2026-05-08.
Addresses: POSITIONING.md W5 (short-term).

## Goal

Let the user re-run a compiled CLIO project from a specific point in the chain rather than from the start, by reading a snapshot of the state written incrementally during the original run. The minimum surface is a `--from-step N` flag on the emitted package's `__main__.py`; the underlying mechanism is a `state.json` snapshot persisted atomically after each top-level chain item.

This is W5 short-term per POSITIONING.md. Combined with W2 (structured logging), it closes the conditions POSITIONING.md sets for a future LangGraph emitter ("python target reaches W2 + W5 first").

## Non-goals (v1)

- **mcp-server target**. Server stateless by design (each tool invocation is a fresh `run()`). A resume mechanism would require client-side state-token plumbing that breaks the MCP model. Deferred indefinitely.
- **claude-cli target**. Has `state.json` already (per-step shell scripts read/write via `jq`), but adding `--from-step` to `run.sh` is a separate concern. Deferred to v2, consistent with W2's claude-cli deferral.
- **`clio resume <output_dir>` CLI wrapper**. POSITIONING.md mentioned this surface. v1 ships the flag inside the emitted `__main__.py` instead — self-contained, no CLIO runtime dependency. A `clio resume` wrapper can be added later if friction emerges.
- **`clio replay --rerun-step N`**. Forces cache miss on a specific step. Mid-term per POSITIONING.md; distinct semantics from resume.
- **Resume mid-FOR-EACH**. The granularity is the top-level chain item. A FOR EACH (sequential or PARALLEL) counts as one step regardless of internal iterations. Mid-iteration time-travel is a long-term event-journal feature.
- **Multi-process / NFS atomicity**. `os.replace` is atomic on POSIX local filesystems. Concurrent runs on the same `state.json` path produce undefined ordering — users distinguish via `CLIO_STATE_FILE`.
- **Compressed or rotated state.json**. Single snapshot, overwritten on each step. Size bounded by the state dict.

## Architecture

### Strategy: snapshot after each chain item, opt-in resume via flag

The compiler emits a `flow.py` whose `run()`:
1. Always writes a `state.json` snapshot after each top-level chain item completes successfully.
2. Accepts an optional `start_at: int = 0` parameter; when `> 0`, loads the existing `state.json`, validates it, sets `state` from the file, and skips the first `start_at` chain items.

The compiler also extends the emitted `__main__.py` argparse with `--from-step N` that maps to `run(start_at=N, ...)`.

### Mapping `.clio` → emitted code

Every emitted python project gets:

```
<pkg_name>/
  flow.py     # MODIFIED — _persist_state helper, start_at gating, conditional state load
  __main__.py # MODIFIED — argparse adds --from-step
```

No new runtime module. The `_persist_state` helper is inlined in `flow.py`.

## Persistence: state.json

Format:

```json
{
  "version": 1,
  "flow": "extract_entities",
  "step_index": 3,
  "state": { "...": "the state dict after step 3 completed" }
}
```

| Field | Type | Purpose |
|---|---|---|
| `version` | int | Format versioning. Always `1` in this milestone. Future formats (event journal) may bump to `2`. |
| `flow` | str | The FLOW name. Validated at resume against the compiled pkg's flow_name; mismatch → fail-fast. |
| `step_index` | int (1-based) | Index of the last chain item that completed. Validated at resume: `step_index >= start_at` required. |
| `state` | object | The accumulated state dict, serialized via `json.dumps(state, default=str)` (mirrors `__main__.py`'s final dump). |

### Path resolution

- Default: `./state.json` (cwd of the `python -m <pkg>` invocation).
- Override: `CLIO_STATE_FILE=path/to/state.json` env var. Mirrors `CLIO_CACHE_DIR` and `CLIO_LOG_FILE`.
- Path created on first write; not pre-allocated.

### Atomic write

```python
def _persist_state(step_idx: int, state: dict) -> None:
    path = os.environ.get("CLIO_STATE_FILE", "state.json")
    payload = {"version": 1, "flow": "<flow_name>", "step_index": step_idx, "state": state}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, default=str)
    os.replace(tmp, path)
```

`os.replace` is atomic on POSIX local filesystems (same directory). The tmp file pattern is reproduced from `clio/runtime/cache.py`.

### Pydantic model serialization

`default=str` produces the `__repr__` of objects (used today by `__main__.py` for the final dump). Consequence: at reload, Pydantic models become strings or already-validated raw dicts. The downstream step that consumes them must re-validate — this is exactly the same path the cache hit takes (cache stores JSON, not Pydantic objects). No new mechanism required.

## Surface

### Emitted `__main__.py` argparse

```python
parser.add_argument(
    "--from-step",
    type=int,
    default=0,
    metavar="N",
    help="Resume from step N+1 (1-based; reads state.json or $CLIO_STATE_FILE).",
)
```

Validation in `main()`: `args.from_step < 0` → `print(...stderr...); return 2`.

### `run(*, start_at: int = 0, **initial)`

The chain body becomes:

```python
def run(*, start_at: int = 0, **initial: object) -> dict:
    if start_at > 0:
        path = os.environ.get("CLIO_STATE_FILE", "state.json")
        if not os.path.exists(path):
            print(f"[clio] resume requested (start_at={start_at}) but {path} missing", file=sys.stderr)
            raise SystemExit(2)
        with open(path) as f:
            payload = json.load(f)
        if payload.get("flow") != "<flow_name>":
            print(f"[clio] state.json flow mismatch: expected <flow_name>, got {payload.get('flow')!r}", file=sys.stderr)
            raise SystemExit(2)
        if payload.get("step_index", 0) < start_at:
            print(f"[clio] state.json only reached step {payload.get('step_index', 0)}, can't resume from {start_at}", file=sys.stderr)
            raise SystemExit(2)
        if start_at >= TOTAL_STEPS:
            print(f"[clio] start_at={start_at} >= total steps={TOTAL_STEPS}", file=sys.stderr)
            raise SystemExit(2)
        state: dict = payload["state"]
    else:
        state = dict(initial)

    _log.set_flow("<flow_name>")
    _log.emit("flow_start", resumed_from=start_at if start_at > 0 else 0)
    _success = False
    _t0 = time.monotonic()
    try:
        if start_at < 1:
            <chain_item_1>
            _persist_state(1, state)
        if start_at < 2:
            <chain_item_2>
            _persist_state(2, state)
        ...
        _success = True
        return state
    finally:
        _log.emit("flow_end", duration_ms=..., success=_success)
        _log.set_flow(None)
```

`TOTAL_STEPS = N` is a module-level constant emitted at the top of `flow.py` (= `len(graph.flow.chain)`).

### Chain item types

- **`CallIR` (single step call)**: gated by `if start_at < idx:`, persists after.
- **`ForEachIR` (sequential)**: gated by `if start_at < idx:`, the `for x in collection:` loop runs entirely inside the `if`. `_persist_state(idx, state)` runs once after the loop completes.
- **`ForEachIR` (PARALLEL)**: gated by `if start_at < idx:`, the entire ThreadPoolExecutor block runs inside. `_persist_state(idx, state)` runs once after the gather completes (inside the parallel-block-end finally is fine — but cleaner: after the existing `state[collector] = _results` line).

In all three cases, the granularity is the chain item, not the inner step. `--from-step 2` skips items 1 and 2; item 3 (and onward) runs.

### Empty FLOW (graph.flow is None)

Stub emitted by `_emit_flow` when `graph.flow is None` (currently `def run(**kwargs): return {}`). Not modified — no instrumentation, no persistence. A flow with no chain has nothing to resume.

### `flow_start` event extension

W2's `flow_start` payload gains an optional `resumed_from` field:
- Absent (or `0`) when `start_at == 0`.
- Set to `start_at` when `start_at > 0`.

This signals to log consumers that a particular flow execution is a resume, useful for downstream replay/audit tooling.

## Edge cases (strict fail-fast)

| Case | Behavior |
|---|---|
| `--from-step 0` (default, no resume) | Run normally. state.json is overwritten as it goes. |
| `--from-step N` with N < 0 | argparse `type=int` accepts it; main() validates and returns 2. |
| `--from-step N` with state.json missing | `run()` raises SystemExit(2) with `state.json missing` message. |
| `--from-step N` with state.json containing different `flow` field | `run()` raises SystemExit(2) with mismatch message. |
| `--from-step N` with state.json `step_index < N` | `run()` raises SystemExit(2) with `only reached step X` message. |
| `--from-step N` with N >= TOTAL_STEPS | `run()` raises SystemExit(2). |
| state.json missing during normal run | First `_persist_state` call creates it. |
| state.json present, normal run | Overwritten step-by-step. Cohérent with cache_dir overwrite semantics. |
| Multiple concurrent runs on the same path | Undefined ordering. Users distinguish via `CLIO_STATE_FILE`. |

## Compatibility and migration

- **Backwards compatible**: existing `.clio` sources compile to projects whose runtime behavior is identical when `--from-step 0` (the default). The only diff in emitted code is `_persist_state` calls and the `start_at` parameter, both inert when `start_at == 0` except for the per-step state.json write.
- **State.json write per step**: this IS new I/O even on normal runs. Negligible (< 1 KB serialized JSON, typically; LLM call latency dominates).
- **No new dependencies**: `flow.py` already imports `json` (W2). `os` and `sys` are imported when needed for cache or other concerns; ensure they're imported when persistance is active (always, in v1).
- **No breaking change to the JSON dump signature of the final result**: `__main__.py` still dumps `result` to stdout via `json.dump(..., default=str)`.

## Tests

### Form tests (`tests/test_emitters/test_python.py` extensions)

- `flow.py` contains `def _persist_state(step_idx: int, state: dict)` helper.
- `flow.py` `run()` signature contains `start_at: int = 0`.
- `flow.py` chain items are wrapped in `if start_at < <idx>:` blocks.
- `flow.py` each chain item is followed by `_persist_state(<idx>, state)`.
- `flow.py` contains `TOTAL_STEPS = <N>` constant at module level.
- `flow.py` contains the four resume-validation paths (missing file / wrong flow / step_index too low / >= TOTAL_STEPS) each as a SystemExit(2).
- `flow.py` `flow_start` emits `resumed_from=start_at if start_at > 0 else 0`.
- `__main__.py` argparse contains `--from-step` with `type=int, default=0`.
- `__main__.py` validates `args.from_step < 0` → return 2.
- A flow with one FOR EACH (sequential) and one PARALLEL block emits exactly one `_persist_state(idx, state)` call per top-level chain item (not per inner iteration).
- Sequential-only flows (no PARALLEL, no FOR EACH) still get `_persist_state` after each item.

### Behavioral tests (`tests/test_emitters/test_python.py` extensions)

Compile + execute a fixture (with monkeypatched SDK), verify:
- Run with `--from-step 0` writes state.json with `step_index == TOTAL_STEPS`.
- Mid-run state.json contains the partial state after each completed step (assert at step 2 of a 3-step flow).
- `--from-step 1` skips chain item 1, loads state.json, runs items 2 and 3.
- `--from-step 0` (default) ignores state.json even if present.
- `--from-step N` with state.json absent → SystemExit(2).
- `--from-step N` with state.json `flow` mismatch → SystemExit(2).
- `--from-step N` with state.json `step_index < N` → SystemExit(2).
- `--from-step N` with N >= TOTAL_STEPS → SystemExit(2).
- `CLIO_STATE_FILE=path` redirects correctly.
- Pydantic model in state serializes as `default=str`-style and reloads as string (downstream step re-validates via Pydantic).

### E2E gated test (`tests/test_e2e_resume.py`, gated `CLIO_E2E=1`)

Mirror `test_e2e_logging.py`:
- Compile `mvp_v03_skeleton.clio` to tmp_path.
- Run `python -m <pkg>` (no flag). Assert: state.json created (or absent if first step raised before persist).
- Manually write a state.json with `{version:1, flow:"classify", step_index:1, state:{"some_field":"value"}}`.
- Run `python -m <pkg> --from-step 1`. Assert: state.json updated with `step_index == TOTAL_STEPS` (or higher than 1, depending on which subsequent steps raised).
- Run with `--from-step 99`: SystemExit(2).
- Run with `--from-step 0` after manually placing state.json: state.json is overwritten (verify by reading after).

### Non-regression tests

- A flow compiled and run **without** `--from-step` exhibits identical behavior to pre-W5 (modulo the state.json write).
- Final stdout JSON dump unchanged.

## Net diff estimate

- **Modified**: `clio/emitters/python.py` `_emit_flow` (~50 LOC added) and `_emit_main` (~10 LOC added).
- **Tests**: `tests/test_emitters/test_python.py` (~120 LOC), `tests/test_e2e_resume.py` (~100 LOC).
- **Fixture regen**: 6 v03 fixtures (`flow.py` + `__main__.py` shape changes).
- **Docs**: LANGUAGE_SPEC + COMPILATION_TARGETS + CHANGELOG + README. ~50 LOC.
- **Total**: ~330 LOC source + tests + fixtures + docs.

## Order of execution (handed to writing-plans)

A reasonable TDD order (writing-plans will refine):
1. Form tests for `_persist_state` helper + `__main__.py` `--from-step` flag.
2. `_emit_flow` modifications: helper, `start_at` parameter, validation paths, `if start_at < N:` gating.
3. `_emit_main` modifications: argparse extension.
4. Behavioral tests: monkeypatched SDK, mid-run state.json, resume from N.
5. E2E gated test: `tests/test_e2e_resume.py`.
6. Fixture regen.
7. Docs (LANGUAGE_SPEC observability section gets a "Resume" subsection; COMPILATION_TARGETS python target gets a Resume line; CHANGELOG + README).

Each step ships green tests before the next starts.
