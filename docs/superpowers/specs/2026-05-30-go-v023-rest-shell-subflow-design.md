# CLIO — `target: go` v0.23 — REST + shell + sub-flow composition — design

**Date**: 2026-05-30
**Sprint**: v0.23 (GitHub issue #82)
**Status**: Spec drafted, awaiting user review before writing the implementation plan.

## Motivation

The Go emitter shipped in v0.20.0 with *exact-Go stubs + Anthropic judgment only*. Four families of IR construct are still refused at compile time (`clio/emitters/_go_helpers.py:225-273`, raised in `validate_graph_for_go`). This sprint lifts the three that are **stdlib-only** and have a Python parity reference:

| Code | Construct | Go primitive | Python ref |
|---|---|---|---|
| `E_GO_007` | `impl.mode: rest` | `net/http` | `clio/runtime/rest.py` |
| `E_GO_008` | `impl.mode: shell` | `os/exec` | shell handling in `_python_helpers.py` |
| `E_GO_006` | FLOW composition (`FlowCallIR`) | per-flow `run<Name>()` funcs | `python.py:837-876`, `:665-694` |

Lifting `E_GO_006` also closes the **last `FlowCallIR` gap across all six targets** (`docs/manual/04-targets.md:189`) and the *FOR EACH PARALLEL body = sub-flow* row (`:190`). Zero new Go dependencies — everything is Go stdlib (`net/http`, `os/exec`, `encoding/json`), plus the already-present `golang.org/x/sync/errgroup`.

The user elected to keep all three in one tag (rather than splitting impl-modes from composition) so that v0.23 is a single "Go reaches feature parity for stdlib constructs" milestone.

## Decisions made during brainstorm

| Topic | Decision | Rejected alternatives |
|---|---|---|
| Scope cut | All three (REST + shell + sub-flow) in v0.23 | (a) Split REST/shell as v0.23, sub-flow as a later tag; (b) sub-flow first |
| Untyped impl result (no GIVES) | **Non-issue.** A step with no GIVES adds nothing to `available` (`builder.py:1535-1541`) → its result is non-referenceable → pure side-effect. Go calls it, checks the error, discards the result. No `any`-typed state value, no dynamic read. | (a) Box untyped results as `any` + special-case readers — unnecessary; the IR forbids the reference |
| Sub-flow threading | **Parity-first flat-merge** (mirror Python `state.update(run_<name>(...))`) + per-flow `state_field_to_step` + boundary extension + new `take_field_to_gotype` map | (a) **Typed signature** `run<Name>(in <Name>In) (<Name>Out, error)` — rejected: references a `bind_key` field that does NOT exist on `FlowCallIR` (`graph.py:249-256`) and a call-site alias grammar that does not exist; (b) Minimal-diff variant leaning on a builder duplicate-rejection that does not exist (`builder.py:1537/1541` overwrite last-writer-wins) |
| Entry-flow TAKES seeding | **Retrofit the entry flow too** — `Run` seeds `state["<take>"] = kwargs["<take>"]` and registers TAKES in `take_field_to_gotype`, matching sub-flows. Existing golden snapshots regenerated. | Scope sub-flow seeding only and accept a typed/untyped asymmetry — rejected for consistency |
| Multi-GIVES sub-flow as `FOR EACH PARALLEL` body | **Refuse** with a clear `E_GO` error in v0.23 (a single `[]T` slot cannot hold multiple typed fields; the builder already declines a typed collector at `builder.py:1501`). Single-GIVES is fully supported with a typed `[]steps.<Cls>Out` collector. | Match Python's untyped `[]map[string]any` list-of-dicts — rejected: breaks Go's typed-state model, un-idiomatic |
| RESCUE protecting a bare sub-flow call | **Vacuous** — not representable. `RescueBlockIR.step_name` is resolved via `steps_by_name[decl.step_name]` (`builder.py:1442`); a flow name can never bind. No Go-side handling. | — |

## Architecture

### REST / shell — slot into the existing typed-state model

Both are new *step-body* shapes, not new orchestration. They produce the same `In`/`Out` structs and `func <Cls>(ctx, in <Cls>In) (<Cls>Out, error)` skeleton as exact/judgment steps (`_go_step_renderers.py:_step_in_out_struct`, `render_exact_step_go`). The orchestrator (`_go_flow_renderer.py`) is **unchanged** for them — a REST/shell `CallIR` renders exactly like an exact/judgment `CallIR` (`state["<gives>"] = <step>Out`). GIVES present → the contract `Out` struct is populated and `.Validate(ctx)` runs (same interface-assertion pattern as judgment, `_go_step_renderers.py:415-423`); GIVES absent → side-effect, returns empty `Out{}`.

**New files:**
- `clio/emitters/_go_step_renderers.py` gains `render_rest_step_go(step, contracts, graph)` and `render_shell_step_go(step, contracts, graph)`.
- `clio/emitters/_go_runtime_templates.py` gains `_REST_GO_TEMPLATE` (→ `clio_runtime/rest/rest.go`) and a `substitute` helper (`clio_runtime/substitute/substitute.go`, shared by REST and shell), with `render_clio_runtime_rest()` / `render_clio_runtime_substitute()` accessors mirroring `render_clio_runtime_cache()`.

**`clio_runtime/rest` (Go mirror of `clio/runtime/rest.py`):** `Subst(template, takes)` for `${var}` and whole-string `env:NAME`; `RenderDict`; retryable classification (`5xx`/`429`/`timeout`/`network`), `ComputeDelay` (constant/exponential backoff with cap), `ParseRetryAfter`. Semantics must be **byte-for-behaviour identical** to `rest.py` (cross-target parity — a `.clio` REST step must behave the same on python and go targets).

**REST step body:** build `*http.Request` from `RestImplIR` (`graph.py:105-115` — `method`, `url`, `query`, `headers`, `body`, `response_path`, `timeout_seconds`, `retry`) with `${var}` substituted from `in`; `http.Client{Timeout: ...}`; impl-level retry loop driven by the step's `RetryPolicyIR` (distinct from `ON_FAIL`); traverse `response_path` on the decoded JSON; `json.Unmarshal` into `<Cls>Out`; validate.

**Shell step body:** `ShellImplIR` (`graph.py:46-52` — `argv` already shlex-split with `${var}` tokens, `timeout_seconds`, `parse`). Per-token substitution via `substitute`; `exec.CommandContext(ctx, argv[0], argv[1:]...)`; timeout via `context.WithTimeout`. `parse: none` → single `str` field = stdout; `parse: json` → `json.Unmarshal(stdout)` into `<Cls>Out`. Validate.

### Sub-flow composition (E_GO_006) — parity-first flat-merge

A callable sub-flow is a `FlowIR` declaring both TAKES and GIVES (`builder.py:816-838`); `graph.flow` is the entry, `graph.flows` holds all flows. Recursion and inter-flow cycles are already rejected at IR build, so the call graph is a finite DAG — no Go-side cycle guard needed.

**One unexported function per callable sub-flow**, appended to `flow/flow.go` after `Run`, emitted in name-sorted order for deterministic goldens (Go sees package-level funcs regardless of source order, so A→B→C nesting is free):

```go
func runEnrich(ctx context.Context, url string) (map[string]any, error) {
    state := map[string]any{}
    state["url"] = url                                   // seed TAKES (mirror python sub_state_init)
    // ... chain rendered by the SAME _render_chain_item ...
    return map[string]any{"summary": state["summary"]}, nil   // GIVES subset only
}
```

Call site (top-level), mirroring python's `state.update(run_<name>(...))` (`python.py:694`):

```go
_subEnrich, err := runEnrich(ctx, /* @ref / literal kwargs, positional in flow.takes order */)
if err != nil { return nil, err }
for k, v := range _subEnrich { state[k] = v }            // typed flat-merge: values are inner steps.<Cls>Out, verbatim
```

Nested-scope call (inside FOR EACH/IF/MATCH/WHILE) mirrors python's invoke-without-bind (`python.py:685-686`): `if _, err := runEnrich(ctx, ...); err != nil { return nil, err }`.

**Three maps make the typed model hold (without any of them, the Go does not compile):**

- **A. Per-flow `state_field_to_step`** — replaces the single global build at `_go_flow_renderer.py:472-475`. New helper `_build_state_field_to_step(flow, steps_by_name)` walks only that flow's reachable producers (chain + rescues + nested IF/MATCH/WHILE/FOR EACH bodies), mapping each step's `gives.name → StepIR`. Built once per rendered function (`Run` and each `run<Name>`). Inside one flow the IR already forbids two producers of one field, so each per-flow map is collision-free. This eliminates cross-flow field-name collision at the root: flow A's `result` and flow B's `result` resolve against different maps in different functions.

- **B. Boundary extension during the chain walk** — the per-flow map is threaded as a *mutable running dict* through `_render_chain_item`. At each top-level `FlowCallIR` arm, before rendering downstream items, extend it: for each `g in subflow.gives`, look up the producer in the *sub-flow's own* per-flow map and register `running_map[g.name] = producer_StepIR`. The flat-merge stores that inner step's actual `steps.<Cls>Out` interface value verbatim, so the downstream read `state["<g>"].(steps.<Producer>Out).<GoField>` asserts to the correct concrete type. Two sibling sub-flows giving the same field → last-writer-wins (parity with python's `state.update` and the builder's `available[g.name]` overwrite). **Guard:** if `subflow_map.get(g.name)` is None at emit time, raise an internal emitter error rather than emit an untyped read that panics at runtime.

- **C. New `take_field_to_gotype` map** — a sub-flow TAKE is produced by no step, so it is absent from `state_field_to_step`; reading `@<take>` would fall to the untyped `state["<take>"]` fallback (`_go_kwarg_value:62`) and fail to compile against a typed `steps.<Cls>In` field. Build `{f.name: _type_to_go(f.type, contracts, qualifier="contracts") for f in flow.takes}` and thread it to **all five reader sites**; when a ref is a TAKE, emit a direct value assertion — scalar: `state["url"].(string)`; contract-typed: `state["x"].(contracts.<Cls>).<Field>`. The five sites: `_go_kwarg_value` (`_go_flow_renderer.py:55`), the MATCH scrutinee (`:287`), the sequential FOR EACH collection resolver (`:352`), the parallel FOR EACH collection resolver (`:396`), and `_go_condition_expr` (`_shared_utils.py`). Missing any one site emits `(any)` → runtime panic, so the threading must be exhaustive.

**Per the entry-flow retrofit decision (Q1):** `Run` also seeds `state["<take>"] = kwargs["<take>"]` for each entry TAKE and registers them in `take_field_to_gotype`, so entry and sub-flows read TAKES identically. Affected golden snapshots (e.g. `go_parallel`) are regenerated.

### `_render_chain_item` gains a `FlowCallIR` arm

Currently `_go_flow_renderer.py:448` raises `NotImplementedError`. The new arm has two shapes:
- **top-level / sequential** → emit the call + flat-merge above; return `prev_var` unchanged.
- **parallel goroutine body** (`suppress_state_write=True`) → for a **single-GIVES** sub-flow, emit `_sub, err := run<Name>(...)` then `_g := _sub["<g0>"].(steps.<Producer>Out)` and return `_g` as the new `prev_var`, so the existing collector line (`:432 _results[_i] = {cur_par}`) stores a typed struct. The parallel renderer pre-allocates `_results := make([]steps.<Producer>Out, len(_items))` for this case (instead of `[]any`), and the collector is registered in the enclosing flow's per-flow map → downstream `FOR EACH x IN <collector>` ranges a typed slice. **Multi-GIVES body → refuse** (Q2).

### Two pre-existing bugs the sub-flow feature exposes (mandatory fixes)

1. **Step-stub loop misses everything but top-level entry-chain CallIR** (`go.py:74-96`). It iterates only `graph.flow.chain` and only top-level `CallIR` (`:81-82`), so (a) steps nested in an entry-flow control block already get no stub file (latent), and (b) sub-flow steps would get none → the module would not compile. **Fix:** replace with a recursive collector that walks every flow in `graph.flows` (chain + nested bodies + rescues), dedups by step name, keeps stable `NN_` numbering by first-seen order. `contracts.go` already over-collects from all `graph.steps`, so contracts are fine.
2. **`_flow_uses_parallel` scans only `graph.flow.chain`** (`_go_helpers.py:70-74`) → would miss a `PARALLEL` block inside a sub-flow → missing `errgroup` import/dep. **Fix:** scan every flow's chain. (`_flow_uses_judgment` / `_flow_uses_cache` already scan all `graph.steps` and stay correct.)

These are required for the feature to compile, so they ship in this PR — as **separate commits** for reviewability (each with its own focused test, including a regression test for the pre-existing entry-flow nested-step case).

### Refusal-code changes (`_go_helpers.py`)

- Remove the `len(graph.flows) > 1` refusal (`:301-302`); **keep** `len(graph.flows) == 0` → `E_GO_004` (`:305-306`).
- Remove `raise _GO_E_007_MSG` / `_GO_E_008_MSG` / `_GO_E_006_MSG` for the now-supported shapes (the `RestImplIR` / `ShellImplIR` impl checks and the `FlowCallIR` arm of `_walk_chain` at `:281-282`).
- **Re-narrow** `_GO_E_006_MSG` to the one genuinely-unsupported shape: a multi-GIVES sub-flow read through a typed `FOR EACH PARALLEL` collector.
- Refresh the stale `"v0.20.0 / v0.20.x"` version strings in the remaining `_GO_E_*_MSG` constants.

## Data flow

State stays a single `map[string]any` per rendered function. Writers store typed step `Out` structs; readers type-assert. The three maps above are *compile-time* metadata (emitter-side), never present in the emitted Go. Sub-flow boundaries copy interface values verbatim, so the static type a reader asserts to always matches the runtime value — guaranteed by the per-flow `state_field_to_step` + boundary extension.

## Error handling

REST/shell return `(<Cls>Out, error)`; the orchestrator's existing `if err != nil { return nil, err }` pattern propagates. REST impl-level retry exhausts to a wrapped error. Sub-flow `run<Name>` returns `(map[string]any, error)`; the top-level error line works verbatim (the function's second return is `error`). Inside a parallel goroutine the existing `_rewrite_return_in_goroutine` maps `return nil, err → return err`.

## Testing (tests verify intent, per Rule 9)

A grep-only golden test cannot fail when `@take` typing regresses — so the suite **must** include a real `go build` of at least one emitted sub-flow module in CI (catches the type-assertion class of bugs).

- **REST**: a step with GIVES (typed `Out`, `json.Unmarshal`, `response_path` traversal, retry loop) + a no-GIVES side-effect step. Assert emitted Go shape + `go build`.
- **Shell**: `parse: none` (str field) and `parse: json` (unmarshal) + `${var}` substitution. Assert + `go build`.
- **Sub-flow sequential**: `examples/flow_composition.clio` entry → assert `func runEnrich(ctx context.Context, url string) (map[string]any, error)`, the `state["url"] = url` seed, the GIVES-subset return, and the call-site `for k, v := range _subEnrich`. `go build`.
- **Sub-flow parallel (single-GIVES)**: assert `_results := make([]steps.SummarizeOut, ...)` and `_g := _sub["summary"].(steps.SummarizeOut)`.
- **Collision**: two sub-flows both GIVE `result` from different-typed steps, both called in one parent → assert last-writer-wins **compiles** (`go build`).
- **Nested A→B→C**: assert all three `run<X>` funcs emitted and module builds.
- **Negatives**: multi-GIVES sub-flow as PARALLEL body with a typed downstream read → assert `E_GO` refusal. Pre-existing entry-flow nested-step → assert its stub file now exists.

## Build sequence (proposed for the plan)

1. REST runtime helper + `render_rest_step_go` → verify: REST emission tests + `go build`.
2. Shell runtime helper + `render_shell_step_go` → verify: shell emission tests + `go build`.
3. Pre-existing fixes (recursive step collector in `go.py`, `_flow_uses_parallel`) as standalone commits → verify: regression test for entry-flow nested step.
4. Per-flow `state_field_to_step` + `take_field_to_gotype` + entry-flow seeding retrofit → verify: existing goldens regenerated, full suite green.
5. `FlowCallIR` arm (sequential + parallel) + `run<Name>` emission + boundary extension → verify: sub-flow tests + `go build`.
6. Refusal-code edits + matrix/CHANGELOG/LANGUAGE_SPEC/manual updates → verify: doctor/check, matrix rows flipped.

## Verify gates (per MEMORY)

`uv run ruff check . --fix` → `uv run mypy` (the new map threading widens signatures — watch `dict` value-type tightness) → full `pytest` → a real `go build` of ≥1 emitted sub-flow module.

## Out of scope (explicitly)

- `E_GO_005` (OpenAI judgment for Go), `E_GO_009` sql / `E_GO_010` mcp_tool (→ v0.24, issue #84), `E_GO_011` (`--from-step` resume — see issue #83), `E_GO_012` (TEST → `go test`).
- Multi-GIVES sub-flow typed downstream iteration via a parallel collector (refused this sprint).

## Open questions

All four surfaced by the design panel are resolved: (Q1) entry-flow seeding → retrofit; (Q2) multi-GIVES parallel collector → refuse; (Q3) RESCUE-over-sub-flow → vacuous (verified `builder.py:1442`); (Q4) the two pre-existing fixes → in-scope, separate commits. None deferred.
