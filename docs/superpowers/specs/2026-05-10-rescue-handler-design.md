# Design — `RESCUE` handler (v0.8)

- **Date**: 2026-05-10
- **Author**: brainstorming session 2026-05-10
- **Status**: draft, awaiting user review
- **Targets impacted**: `python`, `mcp-server` (langgraph rejects in v0.8)

## Context

v0.7 ships `IF / ELSE`, `MATCH / CASE / DEFAULT`, `WHILE … MAX`. Failure
recovery is currently expressed only via `ON_FAIL: retry/escalate/fallback/abort`,
declared on the STEP itself. Limitations:

- `ON_FAIL` strategies are a closed set of four primitives. No way to
  run a multi-step procedure on definitive failure (e.g. notify Slack →
  log to a tracker → abort with a contextualised message).
- The narrative example at `LANGUAGE_SPEC.md` l.656 already showcases
  `IF détecter_churn.FAILS:` as an idea for richer failure handling, but
  the construct is unimplemented and only mentioned as “deferred”.

The brainstorming session converged on a top-level `RESCUE step_a:`
handler that complements `ON_FAIL` rather than replacing it.

## Goals

1. Allow a multi-step body to run when a STEP definitively fails, with
   the same authoring ergonomics as the rest of the FLOW (chain of step
   calls).
2. Compose cleanly with the existing `ON_FAIL` machinery — automatic
   retries first, manual handler last.
3. Keep the runtime simple: only `Exception` is captured, the handler
   ends with a mandatory `abort(...)`.
4. Reuse the architectural pattern documented in
   `memory/next_steps.md` (9-step recipe) so the diff stays surgical.

## Non-goals (deferred to later versions)

- Resuming the chain after a successful rescue (no `RESUME` keyword
  in v0.8 — body must end with `abort`).
- Exposing the captured error message inside the handler body
  (`detect_churn.error` will arrive in a later version if requested).
- LangGraph emission — rejected at compile time (cyclic edges + state
  reducer + multi-step branches all need to land together).
- `RESCUE` inside `FOR EACH / IF / MATCH / WHILE` bodies. Top-level
  chain only in v0.8.
- Multiple `RESCUE` blocks targeting the same STEP. One per STEP, the
  IR builder rejects duplicates.

## Surface syntax

```
FLOW pipeline
  load_csv(path="data.csv")
    -> detect_churn(rows=load_csv)
    -> route_alerts(churn=detect_churn)

  RESCUE detect_churn:
    -> notify_slack(channel="#alerts")
    -> abort("churn detection failed — see #alerts")
```

Grammar (added to `LANGUAGE_SPEC.md §Failure strategies`):

```
flow_decl    := "FLOW" ident NEWLINE INDENT
                  flow_chain
                  rescue_block*
                DEDENT

rescue_block := "RESCUE" step_name ":" NEWLINE INDENT
                  rescue_chain
                DEDENT

rescue_chain := flow_item ("->" flow_item)*  // same chain grammar as
                                              // FLOW body, MUST end with
                                              // a call to abort(message)
```

`step_name` is a bare identifier referring to a STEP that appears in the
FLOW chain (top-level only — see Non-goals).

## Semantics

When a FLOW with a `RESCUE step_a:` handler runs:

1. The chain executes normally up to and including `step_a`.
2. `step_a` is wrapped in a `try / except Exception` block by the
   emitter.
3. **If `step_a` succeeds**: the handler is ignored, the chain continues.
4. **If `step_a` raises**:
   - Its `ON_FAIL` chain (if any) runs first: retry, escalate, fallback
     are attempted in order.
   - If `ON_FAIL` exhausts itself without producing a value, the
     handler body executes.
   - The handler body is a chain of step calls; the last item MUST be
     `abort(message)`.
   - The `abort` raises the standard `FlowAborted("message")` (already
     used by `ON_FAIL: abort(...)`).
   - The remainder of the main chain (any item after `step_a`) is
     skipped.

`KeyboardInterrupt` and `SystemExit` are **not** caught — the rescue
captures `Exception` only.

## Composition rules with `ON_FAIL`

| `ON_FAIL` last clause | `RESCUE` declared | Behaviour |
| --- | --- | --- |
| _(no ON_FAIL)_ | no | Exception propagates (v0.7 behaviour). |
| `retry` / `escalate` / `fallback` (no `abort`) | no | Exception propagates after exhaustion. |
| `... then abort("msg")` | no | `FlowAborted("msg")` raised after exhaustion. |
| _(no ON_FAIL)_ | yes | Exception caught, handler runs. |
| `retry` / `escalate` / `fallback` (no `abort`) | yes | Exhaustion → handler runs. |
| `... then abort("msg")` | **yes** | **Compile-time error**: `abort(...)` redundant when RESCUE handles step_a (line N). |

The compile-time error is enforced by `clio/ir/graph.py` builder when
folding `RescueBlockIR` against each `StepIR.on_fail`.

## AST changes (`clio/parser/ast_nodes.py`)

```python
@dataclass(frozen=True)
class RescueBlock:
    """RESCUE step_name:
           <chain ending with abort(message)>

    `step_name` is the bare identifier of a STEP appearing in the FLOW
    top-level chain. The handler runs only if that STEP raises after
    its ON_FAIL chain (if any) exhausts itself."""
    step_name: str
    body: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock, ...]"
    line: int
    col: int
```

`FlowDecl` gains a sibling field:

```python
@dataclass(frozen=True)
class FlowDecl:
    name: str
    chain: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock, ...]"
    rescues: "tuple[RescueBlock, ...]"   # NEW — empty tuple if none
    line: int
    col: int
```

`RescueBlock.body` reuses the existing union — same recursive items
allowed inside (FOR EACH, IF, MATCH, WHILE remain available within a
rescue body).

## IR changes (`clio/ir/graph.py`)

```python
@dataclass(frozen=True)
class RescueBlockIR:
    """IR mirror of RescueBlock. Bound to a StepIR by name (no direct
    pointer because StepIR is frozen and shared); resolved by
    flow_runtime helpers."""
    step_name: str
    body: "tuple[CallIR | ForEachIR | IfBlockIR | MatchBlockIR | WhileBlockIR, ...]"
    line: int
```

`FlowIR.rescues: tuple[RescueBlockIR, ...]` mirrors the AST.

### IR validations

The IR builder (`clio/ir/graph.py` builder helpers) adds:

1. **Step exists**: every `RescueBlockIR.step_name` must reference a
   STEP appearing in the FLOW top-level chain. Otherwise:
   `Rescue refers to unknown step '<name>' (line N)`.
2. **Top-level only**: the step must appear in the chain at depth 0
   (not inside a FOR EACH / IF / MATCH / WHILE body in v0.8).
   Otherwise: `Rescue target '<name>' must appear in the top-level FLOW
   chain (v0.8 limitation, line N)`.
3. **Single rescue per step**: `len({r.step_name for r in rescues}) ==
   len(rescues)`. Otherwise:
   `Step '<name>' already has a RESCUE handler (line N1, duplicate at
   line N2)`.
4. **No abort clash**: if step has `on_fail` ending in
   `OnFailStrategyIR(kind="abort")`, reject:
   `'abort(...)' final clause in ON_FAIL is redundant when RESCUE
   '<name>' is declared (rescue at line N1, abort at line N2)`.
5. **Body terminal abort**: the last `CallIR` in
   `RescueBlockIR.body` must be a call to `abort(...)`. Otherwise:
   `Rescue body for '<name>' must end with abort(...) (line N)`.
6. **Walker descent**: `_validate_parallel_for_each._walk` (and any
   future similar walker) descends into rescue bodies just like other
   recursive containers.

### `abort(...)` as a synthetic call

`abort` is already a reserved keyword in v0.7 (`clio/keywords.py:44 —
ABORT = "abort"`), used inside `ON_FAIL: abort("msg")`. v0.8 extends its
recognition to **rescue body chains**:

- The parser recognises `abort("string literal")` as a synthetic step
  call when emitted inside a `RescueBlock.body` chain.
- The IR builder represents it as
  `CallIR(step_name="abort", kwargs=(("message", "msg"),), line=N)`.
- Emitters render it as `raise FlowAborted(msg)`.
- Outside rescue bodies (i.e. in the main FLOW chain or in any
  FOR EACH / IF / MATCH / WHILE body), `abort(...)` remains rejected by
  the parser — the only legal use outside rescues stays
  `ON_FAIL: abort(...)`.

### Clarification on rule 5 (terminal abort)

Rule 5 above means: **the last item of the rescue body's top-level
chain** must be a `CallIR` with `step_name == "abort"`. It is NOT
sufficient for an `abort` to appear inside a nested IF / MATCH / WHILE
/ FOR EACH branch — the static check looks at the top-level chain only.

Legal:
```
RESCUE detect_churn:
  -> IF state.must_log:
       -> log_error(...)
  -> abort("churn detection failed")          # last top-level item ✓
```

Illegal (rejected at IR build):
```
RESCUE detect_churn:
  -> IF state.must_log:
       -> abort("with log")                   # in branch — not enough
     ELSE:
       -> abort("without log")                # in branch — not enough
```

The dev must hoist the `abort` to the body's top level. This keeps the
static analysis O(1) and avoids the « every branch must abort » data
flow check, which is a v0.9+ concern.

## Parser changes (`clio/parser/parser.py`)

1. Add `RESCUE = "RESCUE"` to `clio/keywords.py`.
2. New AST production `parse_rescue_block`:
   - Consume `RESCUE` keyword.
   - Parse step name (bare identifier).
   - Consume `:` and NEWLINE.
   - Indent → reuse `_parse_block_chain` for the body.
   - Dedent → return `RescueBlock(step_name, body, line, col)`.
3. `parse_flow_decl` collects rescues **after** the chain, **before**
   the optional `RESOURCES` block. Loop while next token is `RESCUE`.
4. Surface a helpful error if `RESCUE` appears inside an indented chain
   item (foreach/if/match/while body) — pointer to the v0.8 limitation.

## Emitter changes

### python (`clio/emitters/python.py`)

For each `RescueBlockIR rb` in `flow.rescues`, the emitter:

1. Builds a Python helper `_rescue_<step_name>(state, _session=None)` that
   contains the body chain (reusing `_emit_item` recursion).
2. Wraps the call site of the named STEP in the main flow function with:
   ```python
   try:
       <existing call site, including any ON_FAIL retry/escalate/fallback>
   except FlowAborted:
       raise        # ON_FAIL: abort propagates unchanged when no RESCUE
   except Exception:
       _rescue_<step_name>(state)
       raise        # defensive: rescue body should have aborted, but if
                    # for any reason it returns, re-raise the original
                    # exception (kept inside `as exc` binding)
   ```
   Note: rule (4) above forbids `ON_FAIL: abort` clashing with RESCUE,
   so the `except FlowAborted` arm only fires when there is no RESCUE.
3. The `abort(...)` call inside the rescue body is rendered as
   `raise FlowAborted("msg")` — same as in `ON_FAIL: abort`.

### mcp-server (`clio/emitters/_mcp_helpers.py`)

Same lowering, with `async def _rescue_<step_name>(state, _session=None)`
and `await` propagation. Judgment-mode steps inside the rescue body
thread `_session=_session`, mirroring the existing FOR EACH PARALLEL
pattern.

### langgraph (`clio/emitters/langgraph.py`)

Reject at compile time:

```
RESCUE handlers are not supported on the langgraph target in v0.8
(needs cyclic edges + state reducer; planned for the multi-step
branches sprint). Use --target python or --target mcp-server.
```

Same validation surface as the existing WHILE rejection.

### claude-cli

Already rejected by the parallel-handling pre-check (claude-cli rejects
PARALLEL too). Add explicit rejection for RESCUE for symmetry.

## Viewer (`clio/cli.py` → mermaid + html)

Mermaid diagram emits:

- A red-tinted node `rescue_<step_name>` clustered visually next to
  the protected STEP, with edges:
  ```
  step_a -. fails .-> rescue_step_a
  rescue_step_a --> abort_step_a((abort))
  ```
- Body steps inside the rescue are emitted as a sub-flow, dotted edges
  between them.
- HTML viewer side panel shows the rescue body content when the user
  clicks the protected STEP node.

A new accent hue is introduced: `rescue = #d73a49` (red), distinct
from the 5 existing hues (judgment=blue, shell=orange, rest=teal,
code=slate, parallel/foreach=amber/rust).

## Test plan

New test files:

- `tests/test_rescue_block.py` — parser shape (10+ cases):
  - Basic RESCUE attached to a chain step.
  - Multi-step rescue body (notify → log → abort).
  - RESCUE before RESOURCES.
  - Multiple RESCUE blocks for different STEPs.
  - Reject: RESCUE for unknown step.
  - Reject: RESCUE for step inside a foreach body.
  - Reject: duplicate RESCUE for same step.
  - Reject: ON_FAIL ending with abort + RESCUE on same step.
  - Reject: rescue body not ending with abort.
  - Reject: RESCUE inside indented chain item.

- Snapshot tests in `tests/test_emitters/test_python.py` and
  `test_mcp_server.py` — verify try/except wrapping, `_rescue_*` helper
  signature, propagation of `_session` for async.

- `tests/test_emitters/test_langgraph.py` — verify the rejection error
  message points to `--target python` / `--target mcp-server`.

- `tests/test_graph_viewer.py` — verify rescue cluster in mermaid output
  and `rescue_meta` exposed to JS.

Total new tests target: **~20**, putting the suite at ~477 green.

## Documentation impact

- `docs/LANGUAGE_SPEC.md`:
  - Replace l.448 ("no `.FAILS` shorthand … those are deferred") with
    a forward reference to §RESCUE.
  - Replace the example at l.649-657 to use `RESCUE détecter_churn:`.
  - Add a new section `### RESCUE handler (v0.8)` under
    `## Failure strategies`, with the grammar, semantics table, and
    composition rules.
- `docs/manual/02-language-tour.md`: add a 1-page section after WHILE.
- `docs/manual/03-cookbook.md`: add the « pipeline LLM critique avec
  notification Slack » recipe.
- `docs/manual/06-troubleshooting.md`: add 2 entries (compile errors
  4 + 5 above).
- `CHANGELOG.md`: open `## v0.8.0 — RESCUE handler` section.
- `examples/feedback_routing.clio` or new
  `examples/critical_pipeline.clio`: showcase RESCUE composing with
  `ON_FAIL: retry then escalate`.

## Implementation sequence (preview for writing-plans)

1. Keyword + AST node (`keywords.py`, `ast_nodes.py`).
2. Parser production + dispatch.
3. IR node + builder + 6 validations.
4. python emitter + tests.
5. mcp-server emitter + tests.
6. langgraph + claude-cli rejection + tests.
7. Viewer (mermaid + html cluster).
8. SPEC + manual + CHANGELOG.
9. New example showcasing ON_FAIL × RESCUE composition.

## Open questions surfaced during design

None — all 5 secondary decisions (targets, exception type, multiplicity,
scope, error access) were validated in the brainstorming session along
with the 2 structural ones (semantics, ON_FAIL composition) and the
naming question (`RESCUE` over `IF X.FAILS:`).
