# FLOW Composition (sub-flow callable as a step) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Issue:** [#24](https://github.com/Sandjab/clio/issues/24) (depends on #23, now closed in v0.16.0).

**Goal:** Let a FLOW be called wherever a STEP call is legal — in a chain, in a `FOR EACH PARALLEL` body, inside `IF` / `MATCH` / `WHILE` bodies, inside `RESCUE` bodies — provided the callee FLOW has an explicit `FLOW.TAKES` / `FLOW.GIVES` signature.

**Architecture:** Since #23 ships explicit FLOW signatures, a FLOW with declared `TAKES` / `GIVES` is structurally indistinguishable from a STEP. The compiler resolves each `name(kwargs)` call against step names first, then against flow names that have a signature. Each FLOW gets its own IR (the graph now holds **all** FLOWs, not just one main). Each emitter compiles a `FlowCallIR` to a target-specific sub-flow invocation. The `mcp-server` target additionally exposes every FLOW that is **not** called by another FLOW.

**Tech Stack:** Python 3.12+, frozen dataclasses for IR, pytest, ruff, no new dependencies.

**Version bump:** `0.16.0` → `0.17.0`.

**Out of scope (explicitly):**
- Cross-file `IMPORT` of FLOWs.
- `EXPOSE` / `INTERNAL` markers (default = expose every uncalled FLOW).
- Recursive FLOWs (rejected at compile time).
- Sub-flow support for `target: claude-cli` (compile-time rejection — bash sub-shell isolation is deferred).

---

## File Structure

**IR — modify:**
- `clio/ir/graph.py` — add `FlowCallIR`; update Union types in `ForEachIR`, `IfBlockIR`, `MatchCaseIR`, `WhileBlockIR`, `RescueBlockIR`, `FlowIR.chain`; add `FlowGraph.flows: tuple[FlowIR, ...]` + `FlowGraph.exposed_flow_names: frozenset[str]`.
- `clio/ir/builder.py` — thread `flow_sigs_by_name` through `_build_call`, `_build_flow_items`, `_build_for_each`, `_build_if_block`, `_build_match_block`, `_build_while_block`, `_build_rescue_block`, `_build_flow`. New helpers `_extract_flow_signatures`, `_detect_flow_call_cycles`, `_compute_exposed_flows`. Build **all** FlowIRs (not just one). Add collision check (step name ⊕ flow name).

**Emitters — modify:**
- `clio/emitters/python.py` + `clio/emitters/_python_helpers.py` — emit `def run_<flow>(**takes) -> dict:` per FlowIR; emit `state["<call_name>"] = run_<flow_name>(**kwargs)` for `FlowCallIR`.
- `clio/emitters/mcp_server.py` + `clio/emitters/_mcp_helpers.py` — emit one `@mcp.tool()` per **exposed** FLOW (= not in `exposed_flow_names`'s complement); each tool has its input/output schema derived from declared TAKES/GIVES; sub-flow calls compile to plain function calls in `flow.py`.
- `clio/emitters/claude_skill.py` + `clio/emitters/_claude_skill_helpers.py` — emit `scripts/sub_<flow>.sh` per FlowIR; main `scripts/run.sh` sources / invokes them on `FlowCallIR`.
- `clio/emitters/langgraph.py` + `clio/emitters/_langgraph_helpers.py` — compile each FlowIR to its own `StateGraph` and register sub-graphs via `compile()`-as-node.
- `clio/emitters/claude_cli.py` — raise `EmitError` on any `FlowCallIR` (compile-time rejection with clear message).

**Tests — create / modify:**
- `tests/test_ir.py` — sub-flow call resolves; missing-signature error; collision error; cycle error; recursion error; type-check against flow signature.
- `tests/test_emitters/test_python.py` — sub-flow call emits a Python function call; output is bound under call name.
- `tests/test_emitters/test_mcp_server.py` — multi-tool output for multi-FLOW source; uncalled FLOWs become tools; called sub-FLOWs do not.
- `tests/test_emitters/test_claude_skill.py` — main script invokes sub-flow scripts.
- `tests/test_emitters/test_langgraph.py` — sub-graph node present in compiled output.
- `tests/test_emitters/test_claude_cli.py` — compile-time rejection with clear error.
- `tests/fixtures/flow_composition.clio` — fixture exercising reuse + encapsulated RESCUE.
- `tests/test_examples_projects/` — end-to-end emit + (where applicable) run for new example.

**Docs — modify / create:**
- `docs/LANGUAGE_SPEC.md` — new `### FLOW composition (v0.17)` section after `### FLOW signature (v0.16, optional)` (currently at line 512).
- `docs/manual/cookbook.md` — new recipe "Composing FLOWs".
- `examples/flow_composition.clio` — illustrative example.
- `CHANGELOG.md` — `[Unreleased]` → `## v0.17.0 — 2026-05-15`.
- `README.md` — refresh test count + version badge.
- `pyproject.toml` + `uv.lock` — bump to `0.17.0`.

---

## Task 0: Branch + Baseline

**Files:** none modified yet.

- [ ] **Step 1:** Create feature branch.

```bash
git checkout main && git pull
git checkout -b feat/v0.17-flow-composition
```

- [ ] **Step 2:** Run baseline tests to confirm green start.

```bash
uv run pytest tests/ -q --tb=no
```

Expected: 859 passed, 15 skipped, 1 xfailed.

- [ ] **Step 3:** Confirm working tree clean.

```bash
git status
```

Expected: "nothing to commit, working tree clean" (untracked `.claude/` ignored).

---

## Task 1: Add `FlowCallIR` and Wire Unions

**Files:**
- Modify: `clio/ir/graph.py:242-372`

- [ ] **Step 1: Write the failing test.**

`tests/test_ir.py` — append:

```python
def test_flowcallir_is_distinct_from_callir():
    from clio.ir.graph import FlowCallIR, CallIR
    fc = FlowCallIR(flow_name="enrich", kwargs=(("a", "@art"),), line=10)
    sc = CallIR(step_name="enrich", kwargs=(("a", "@art"),), line=10)
    assert type(fc) is not type(sc)
    assert fc.flow_name == "enrich"
    assert fc.kwargs == (("a", "@art"),)
    assert fc.line == 10
```

- [ ] **Step 2: Run test to verify it fails.**

```bash
uv run pytest tests/test_ir.py::test_flowcallir_is_distinct_from_callir -v
```

Expected: FAIL with `ImportError: cannot import name 'FlowCallIR'`.

- [ ] **Step 3: Add `FlowCallIR` dataclass and extend Union types.**

In `clio/ir/graph.py`, after the `CallIR` definition (line 246), insert:

```python
@dataclass(frozen=True)
class FlowCallIR:
    """v0.17 — a sub-flow invocation in a parent FLOW's chain. Resolves
    to another FlowIR by name. Output is bound under the call-site name
    (defaults to flow_name unless aliased) in the parent's state."""
    flow_name: str
    kwargs: tuple[tuple[str, object], ...]
    line: int
```

Then update every body Union in `graph.py` to include `FlowCallIR`. Apply identical edits to `ForEachIR.body` (line 282), `IfBlockIR.then_body` + `IfBlockIR.else_body` (lines 317-318), `MatchCaseIR.body` (line 327), `WhileBlockIR.body` (line 351), `RescueBlockIR.body` (line 361), and `FlowIR.chain` (line 368). For each, change:

```python
body: "tuple[CallIR | ForEachIR | IfBlockIR | MatchBlockIR | WhileBlockIR, ...]"
```

to:

```python
body: "tuple[CallIR | FlowCallIR | ForEachIR | IfBlockIR | MatchBlockIR | WhileBlockIR, ...]"
```

(For `RescueBlockIR.body`, also keep `ResumeIR`. For `FlowIR.chain`, drop `RescueBlockIR` if absent — match existing union exactly.)

- [ ] **Step 4: Run test to verify it passes.**

```bash
uv run pytest tests/test_ir.py::test_flowcallir_is_distinct_from_callir -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite — should still be green.**

```bash
uv run pytest tests/ -q --tb=no
```

Expected: same baseline (859 passed). New IR class is unused so far.

- [ ] **Step 6: Commit.**

```bash
git add clio/ir/graph.py tests/test_ir.py
git commit -m "feat(v0.17): add FlowCallIR + extend body unions

First slice of #24. FlowCallIR is structurally distinct from CallIR so
emitters can pattern-match. No builder or emitter wires it yet."
```

---

## Task 2: Extract Flow Signatures (Pass 0.5)

**Files:**
- Modify: `clio/ir/builder.py`

- [ ] **Step 1: Write failing test.**

`tests/test_ir.py` — append:

```python
def test_extract_flow_signatures_skips_unsigned_flows():
    from clio.ir.builder import _extract_flow_signatures
    from clio.parser.parser import parse
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n\n"
        "FLOW with_sig\n  TAKES: x: str\n  GIVES: y: str\n  s(x=x)\n\n"
        "FLOW no_sig\n  s(x=\"hi\")\n"
    )
    prog = parse(src)
    flow_decls = [d for d in prog.decls if type(d).__name__ == "FlowDecl"]
    sigs = _extract_flow_signatures(flow_decls)
    assert "with_sig" in sigs
    assert "no_sig" not in sigs
    sig = sigs["with_sig"]
    assert [f.name for f in sig.takes] == ["x"]
    assert [f.name for f in sig.gives] == ["y"]
```

- [ ] **Step 2: Verify it fails.**

```bash
uv run pytest tests/test_ir.py::test_extract_flow_signatures_skips_unsigned_flows -v
```

Expected: FAIL with `ImportError: cannot import name '_extract_flow_signatures'`.

- [ ] **Step 3: Implement.**

In `clio/ir/builder.py`, near the top after existing helpers, add:

```python
@dataclass(frozen=True)
class FlowSignature:
    """Lightweight projection of a FlowDecl used for call-site resolution.
    Only flows that explicitly declared TAKES *and* GIVES are callable."""
    name: str
    takes: tuple[Field, ...]
    gives: tuple[Field, ...]
    line: int


def _extract_flow_signatures(
    flow_decls: list[FlowDecl],
) -> dict[str, FlowSignature]:
    """Pass 0.5 (v0.17): collect signatures of FLOWs that declare BOTH
    TAKES and GIVES. Unsigned FLOWs are silently omitted (they remain
    runnable as the main flow but cannot be called as sub-flows)."""
    sigs: dict[str, FlowSignature] = {}
    for d in flow_decls:
        if d.takes and d.gives:
            sigs[d.name] = FlowSignature(
                name=d.name, takes=d.takes, gives=d.gives, line=d.line,
            )
    return sigs
```

Make sure `Field` and `FlowDecl` are already imported at the top of `builder.py` (they are — `FlowDecl` is used at line 150, `Field` is the AST type).

- [ ] **Step 4: Verify test passes.**

```bash
uv run pytest tests/test_ir.py::test_extract_flow_signatures_skips_unsigned_flows -v
```

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add clio/ir/builder.py tests/test_ir.py
git commit -m "feat(v0.17): _extract_flow_signatures + FlowSignature dataclass

Pass 0.5 of the IR builder. Only FLOWs with both TAKES and GIVES are
exposed as callable sub-flows; unsigned FLOWs remain main-only."
```

---

## Task 3: Detect Step/Flow Name Collisions

**Files:**
- Modify: `clio/ir/builder.py:148-150` (insertion point after cycle detection, before flow handling)

- [ ] **Step 1: Write failing test.**

`tests/test_ir.py`:

```python
def test_step_flow_name_collision_rejected():
    from clio.parser.parser import parse
    from clio.ir.builder import build_ir, IRBuildError
    src = (
        "STEP enrich\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n\n"
        "FLOW enrich\n  TAKES: x: str\n  GIVES: y: str\n  enrich(x=x)\n"
    )
    import pytest as _pt
    with _pt.raises(IRBuildError) as ei:
        build_ir(parse(src))
    msg = str(ei.value)
    assert "collision" in msg.lower() or "shadow" in msg.lower()
    assert "enrich" in msg
```

- [ ] **Step 2: Verify failure.**

```bash
uv run pytest tests/test_ir.py::test_step_flow_name_collision_rejected -v
```

Expected: FAIL (current builder won't raise on the collision).

- [ ] **Step 3: Implement.**

In `clio/ir/builder.py`, immediately after the existing block detecting duplicate FLOW names (around lines 150-157), add:

```python
    # v0.17: a STEP and a FLOW cannot share a name (would render call
    # resolution ambiguous).
    for d in flow_decls:
        if d.name in steps_by_name:
            raise IRBuildError(
                f"line {d.line}:{d.col}: name {d.name!r} collides with a "
                f"STEP declared on line {steps_by_name[d.name].line}"
            )
```

- [ ] **Step 4: Verify pass.**

```bash
uv run pytest tests/test_ir.py::test_step_flow_name_collision_rejected -v
```

Expected: PASS.

- [ ] **Step 5: Full suite.**

```bash
uv run pytest tests/ -q --tb=no
```

Expected: still green (no fixture uses colliding names).

- [ ] **Step 6: Commit.**

```bash
git add clio/ir/builder.py tests/test_ir.py
git commit -m "feat(v0.17): reject STEP/FLOW name collisions at IR build time

Required before sub-flow call resolution can disambiguate between a
step and a flow purely by name."
```

---

## Task 4: Resolve Sub-Flow Calls in `_build_call`

**Files:**
- Modify: `clio/ir/builder.py:913-976` (`_build_call`) and all callers.

- [ ] **Step 1: Write failing tests.**

`tests/test_ir.py`:

```python
def test_subflow_call_returns_flowcallir():
    from clio.parser.parser import parse
    from clio.ir.builder import build_ir
    from clio.ir.graph import FlowCallIR
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n\n"
        "FLOW inner\n  TAKES: x: str\n  GIVES: y: str\n  s(x=x)\n\n"
        "FLOW outer\n  TAKES: x: str\n  GIVES: y: str\n  inner(x=x)\n"
    )
    g = build_ir(parse(src), flow_name="outer")
    outer = g.flow
    assert outer is not None
    item = outer.chain[0]
    assert isinstance(item, FlowCallIR)
    assert item.flow_name == "inner"


def test_call_to_unsigned_flow_rejected():
    from clio.parser.parser import parse
    from clio.ir.builder import build_ir, IRBuildError
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n\n"
        "FLOW inner\n  s(x=\"hi\")\n\n"
        "FLOW outer\n  TAKES: x: str\n  GIVES: y: str\n  inner(x=x)\n"
    )
    import pytest as _pt
    with _pt.raises(IRBuildError) as ei:
        build_ir(parse(src), flow_name="outer")
    msg = str(ei.value)
    assert "inner" in msg
    assert "signature" in msg.lower() or "TAKES" in msg
```

- [ ] **Step 2: Verify both fail.**

```bash
uv run pytest tests/test_ir.py::test_subflow_call_returns_flowcallir tests/test_ir.py::test_call_to_unsigned_flow_rejected -v
```

Expected: FAIL.

- [ ] **Step 3: Modify `_build_call` to accept `flow_sigs` and emit `FlowCallIR`.**

In `clio/ir/builder.py`, change the `_build_call` signature and body:

```python
def _build_call(
    call: StepCall,
    steps_by_name: dict[str, StepIR],
    flow_sigs: dict[str, FlowSignature],
    contracts: dict[str, ContractIR],
    available: dict[str, TypeExpr],
    in_rescue: bool = False,
) -> "CallIR | FlowCallIR":
    if call.name == "abort":
        if not in_rescue:
            raise IRBuildError(
                f"line {call.line}:{call.col}: abort(...) is only valid "
                f"inside a RESCUE body"
            )
        return CallIR(step_name=call.name, kwargs=call.kwargs, line=call.line)

    # Resolve: step first, then flow signature.
    if call.name in steps_by_name:
        return _build_step_call(call, steps_by_name, contracts, available, in_rescue)
    if call.name in flow_sigs:
        return _build_flow_call(call, flow_sigs, contracts, available)

    # Unknown name — but be helpful if the name matches an unsigned FLOW.
    raise IRBuildError(
        f"line {call.line}:{call.col}: unknown STEP or signed FLOW "
        f"{call.name!r} (signed FLOWs must declare both TAKES and GIVES)"
    )
```

Refactor the existing body into `_build_step_call(call, steps_by_name, contracts, available, in_rescue) -> CallIR` — same code as today, just renamed. Then add the new helper:

```python
def _build_flow_call(
    call: StepCall,
    flow_sigs: dict[str, FlowSignature],
    contracts: dict[str, ContractIR],
    available: dict[str, TypeExpr],
) -> FlowCallIR:
    sig = flow_sigs[call.name]
    provided = dict(call.kwargs)
    for taken in sig.takes:
        if taken.name not in provided:
            raise IRBuildError(
                f"line {call.line}:{call.col}: FLOW {sig.name} requires "
                f"kwarg {taken.name!r}, got {sorted(provided)}"
            )
        value = provided[taken.name]
        if isinstance(value, str) and value.startswith("@"):
            ref = value[1:]
            if ref not in available:
                raise IRBuildError(
                    f"line {call.line}:{call.col}: state reference "
                    f"{ref!r} not produced by any previous step"
                )
            ref_type = available[ref]
            if not (
                types_equal(ref_type, taken.type, contracts)
                or names_equal(ref_type, taken.type)
            ):
                raise IRBuildError(
                    f"line {call.line}:{call.col}: type mismatch on "
                    f"{taken.name!r}: FLOW {sig.name} expects "
                    f"{_render(taken.type)}, parent provides "
                    f"{_render(ref_type)}"
                )
    return FlowCallIR(
        flow_name=call.name, kwargs=tuple(call.kwargs), line=call.line,
    )
```

- [ ] **Step 4: Thread `flow_sigs` through all builder helpers that call `_build_call`.**

Add `flow_sigs: dict[str, FlowSignature]` parameter to: `_build_flow_items`, `_build_for_each`, `_build_if_block`, `_build_match_block`, `_build_while_block`, `_build_rescue_block`, `_build_flow`. Each just forwards it. At the call sites in `build_ir` (lines 167, 169), compute `flow_sigs = _extract_flow_signatures(flow_decls)` before `_build_flow` and pass it in.

Use the IDE/editor: search for `_build_flow_items(` and `_build_call(` to find every call site and add the new argument.

- [ ] **Step 5: Run the two new tests.**

```bash
uv run pytest tests/test_ir.py::test_subflow_call_returns_flowcallir tests/test_ir.py::test_call_to_unsigned_flow_rejected -v
```

Expected: PASS.

- [ ] **Step 6: Full IR test suite.**

```bash
uv run pytest tests/test_ir.py -q --tb=short
```

Expected: all green. Existing tests still pass because `flow_sigs` is empty for any source that does not declare signed FLOWs.

- [ ] **Step 7: Commit.**

```bash
git add clio/ir/builder.py tests/test_ir.py
git commit -m "feat(v0.17): resolve sub-flow calls to FlowCallIR

_build_call now accepts a flow signature map. Steps win over flows on
name conflict (impossible thanks to Task 3's collision check). Calls
to unsigned FLOWs produce a clear error pointing at the missing
TAKES/GIVES."
```

---

## Task 5: Cycle and Recursion Detection on Sub-Flow Calls

**Files:**
- Modify: `clio/ir/builder.py` (new helper + call site in `build_ir`).

- [ ] **Step 1: Write failing tests.**

`tests/test_ir.py`:

```python
def test_subflow_self_recursion_rejected():
    from clio.parser.parser import parse
    from clio.ir.builder import build_ir, IRBuildError
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n\n"
        "FLOW a\n  TAKES: x: str\n  GIVES: y: str\n  a(x=x)\n"
    )
    import pytest as _pt
    with _pt.raises(IRBuildError) as ei:
        build_ir(parse(src), flow_name="a")
    assert "recursion" in str(ei.value).lower() or "cycle" in str(ei.value).lower()


def test_subflow_mutual_recursion_rejected():
    from clio.parser.parser import parse
    from clio.ir.builder import build_ir, IRBuildError
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n\n"
        "FLOW a\n  TAKES: x: str\n  GIVES: y: str\n  b(x=x)\n\n"
        "FLOW b\n  TAKES: x: str\n  GIVES: y: str\n  a(x=x)\n"
    )
    import pytest as _pt
    with _pt.raises(IRBuildError) as ei:
        build_ir(parse(src), flow_name="a")
    assert "cycle" in str(ei.value).lower()
```

- [ ] **Step 2: Verify failures.**

```bash
uv run pytest tests/test_ir.py::test_subflow_self_recursion_rejected tests/test_ir.py::test_subflow_mutual_recursion_rejected -v
```

Expected: FAIL — either no exception, or a less specific one.

- [ ] **Step 3: Implement DFS cycle detection.**

In `clio/ir/builder.py`, add:

```python
def _collect_flow_call_names(
    items: "tuple[object, ...]",
) -> set[str]:
    """Walk a FLOW chain (or nested body) and collect every FLOW name
    invoked via FlowCallIR, including inside FOR EACH / IF / MATCH /
    WHILE / RESCUE bodies."""
    out: set[str] = set()
    for it in items:
        if isinstance(it, FlowCallIR):
            out.add(it.flow_name)
        elif isinstance(it, (ForEachIR, WhileBlockIR)):
            out.update(_collect_flow_call_names(it.body))
        elif isinstance(it, IfBlockIR):
            out.update(_collect_flow_call_names(it.then_body))
            out.update(_collect_flow_call_names(it.else_body))
        elif isinstance(it, MatchBlockIR):
            for arm in it.cases:
                out.update(_collect_flow_call_names(arm.body))
    return out


def _detect_flow_call_cycles(flows: dict[str, FlowIR]) -> None:
    """DFS three-color cycle detection over the flow→flow call graph.
    Self-edges are reported as 'recursion'; longer cycles as 'cycle'."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in flows}
    edges = {n: _collect_flow_call_names(f.chain) | _collect_flow_call_names_rescues(f.rescues) for n, f in flows.items()}

    def visit(name: str, path: list[str]) -> None:
        color[name] = GRAY
        for nb in sorted(edges.get(name, ())):
            if nb not in flows:
                continue
            if nb == name:
                f = flows[name]
                raise IRBuildError(
                    f"line {f.line}:0: FLOW {name!r} calls itself "
                    f"(recursion not supported in v0.17)"
                )
            if color[nb] == GRAY:
                f = flows[name]
                raise IRBuildError(
                    f"line {f.line}:0: sub-flow call creates a cycle: "
                    f"{' -> '.join(path + [name, nb])}"
                )
            if color[nb] == WHITE:
                visit(nb, path + [name])
        color[name] = BLACK

    for n in flows:
        if color[n] == WHITE:
            visit(n, [])


def _collect_flow_call_names_rescues(
    rescues: "tuple[RescueBlockIR, ...]",
) -> set[str]:
    out: set[str] = set()
    for r in rescues:
        out.update(_collect_flow_call_names(r.body))
    return out
```

- [ ] **Step 4: Call it from `build_ir`.**

After **all** FlowIRs are built (Task 6 will make this multi-flow; for now build each FlowIR individually, store in a dict, then call detection). For this task, since multi-flow IR is not yet in place, build the single selected `flow_ir` then run cycle detection on `{flow_ir.name: flow_ir}` plus any FlowIRs that need to be built to resolve the chain. The simplest path: build **all** flows in this task too — see Task 6 below for the structural change.

**Decision:** merge Task 5 and Task 6 into one commit because cycle detection requires a multi-flow graph to be meaningful. Proceed to Task 6's build-all logic first, then add the cycle detection call after the build, then re-run Task 5's tests.

- [ ] **Step 5: Defer test verification until after Task 6.**

(Task 5's tests will be verified at the end of Task 6.)

---

## Task 6: Build All FlowIRs, Expose `FlowGraph.flows`

**Files:**
- Modify: `clio/ir/graph.py` (`FlowGraph` extended fields).
- Modify: `clio/ir/builder.py` (`build_ir` loops over all flow_decls).

- [ ] **Step 1: Write failing test.**

`tests/test_ir.py`:

```python
def test_graph_exposes_all_flows():
    from clio.parser.parser import parse
    from clio.ir.builder import build_ir
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n\n"
        "FLOW a\n  TAKES: x: str\n  GIVES: y: str\n  s(x=x)\n\n"
        "FLOW b\n  TAKES: x: str\n  GIVES: y: str\n  a(x=x)\n"
    )
    g = build_ir(parse(src), flow_name="b")
    assert g.flow is not None and g.flow.name == "b"
    names = {f.name for f in g.flows}
    assert names == {"a", "b"}
```

- [ ] **Step 2: Verify failure.**

```bash
uv run pytest tests/test_ir.py::test_graph_exposes_all_flows -v
```

Expected: FAIL with `AttributeError: 'FlowGraph' object has no attribute 'flows'`.

- [ ] **Step 3: Extend `FlowGraph`.**

In `clio/ir/graph.py`, find the `FlowGraph` dataclass (search for `class FlowGraph`) and add two fields:

```python
    flows: tuple[FlowIR, ...] = ()             # v0.17 — all FLOWs, signed or not
    exposed_flow_names: frozenset[str] = frozenset()   # v0.17 — for mcp-server
```

- [ ] **Step 4: Build all FlowIRs in `build_ir`.**

In `clio/ir/builder.py`, replace the block that builds a single `flow_ir` (lines 159-175 currently) with:

```python
    flow_sigs = _extract_flow_signatures(flow_decls)

    # Build every FlowIR (signed and unsigned). Sub-flow calls resolve
    # against `flow_sigs`; unsigned flows are still built because they
    # can be the main flow.
    all_flows: dict[str, FlowIR] = {}
    for d in flow_decls:
        all_flows[d.name] = _build_flow(d, steps_by_name, flow_sigs, contracts)

    _detect_flow_call_cycles(all_flows)

    main: FlowIR | None = None
    if flow_name is not None:
        if flow_name not in all_flows:
            available = ", ".join(sorted(all_flows)) or "<none>"
            raise IRBuildError(
                f"flow {flow_name!r} not found in source (available: {available})"
            )
        main = all_flows[flow_name]
    elif len(all_flows) == 1:
        main = next(iter(all_flows.values()))
    elif len(all_flows) > 1:
        # v0.17: multi-flow is allowed *without* --flow. Targets that
        # need a main (python, langgraph, claude-skill, claude-cli)
        # will raise during emit if main is None and they cannot
        # synthesise one. mcp-server tolerates main=None.
        main = None
```

Then in the `FlowGraph(...)` constructor call, pass:

```python
        flow=main,
        flows=tuple(all_flows.values()),
        exposed_flow_names=_compute_exposed_flows(all_flows, flow_sigs),
```

Add `_compute_exposed_flows`:

```python
def _compute_exposed_flows(
    all_flows: dict[str, FlowIR],
    flow_sigs: dict[str, FlowSignature],
) -> frozenset[str]:
    """A FLOW is exposed iff it has an explicit signature (in flow_sigs)
    AND it is not called by any other FLOW in the same source."""
    called: set[str] = set()
    for f in all_flows.values():
        called.update(_collect_flow_call_names(f.chain))
        called.update(_collect_flow_call_names_rescues(f.rescues))
    return frozenset(n for n in flow_sigs if n not in called)
```

- [ ] **Step 5: Verify Task 5 + Task 6 tests pass.**

```bash
uv run pytest tests/test_ir.py -q --tb=short
```

Expected: all green (including the recursion / cycle / multi-flow tests from Tasks 4-6).

- [ ] **Step 6: Full suite.**

```bash
uv run pytest tests/ -q --tb=no
```

Expected: still green. Existing single-flow tests work because `main` resolves to the unique flow.

- [ ] **Step 7: Commit.**

```bash
git add clio/ir/graph.py clio/ir/builder.py tests/test_ir.py
git commit -m "feat(v0.17): build all FlowIRs + cycle/recursion detection

FlowGraph now holds every FlowIR; FlowGraph.exposed_flow_names lists
the signed flows not called by any sibling. Recursion and inter-flow
cycles are rejected with clear errors."
```

---

## Task 7: Emit Sub-Flow Calls in `python` Target

**Files:**
- Modify: `clio/emitters/python.py`
- Modify: `clio/emitters/_python_helpers.py`
- Modify: `tests/test_emitters/test_python.py`

- [ ] **Step 1: Write failing test.**

`tests/test_emitters/test_python.py`:

```python
def test_python_emits_subflow_as_function_call(tmp_path):
    from clio.parser.parser import parse
    from clio.ir.builder import build_ir
    from clio.emitters.python import emit
    src = (
        "STEP greet\n"
        "  TAKES: name: str\n  GIVES: msg: str\n  MODE: exact\n"
        "  LANG: python\n  IMPL:\n    code: |\n"
        "      def run(name): return {'msg': f'hi {name}'}\n\n"
        "FLOW inner\n  TAKES: name: str\n  GIVES: msg: str\n"
        "  greet(name=name)\n\n"
        "FLOW outer\n  TAKES: name: str\n  GIVES: msg: str\n"
        "  inner(name=name)\n\n"
        "RESOURCES\n  target: python\n  models: []\n"
    )
    g = build_ir(parse(src), flow_name="outer")
    emit(g, tmp_path)
    flow_py = (tmp_path / "outer" / "flow.py").read_text()
    # Sub-flow becomes a callable function inside the same module.
    assert "def run_inner" in flow_py
    # The outer chain invokes it.
    assert "run_inner(name=" in flow_py
```

- [ ] **Step 2: Verify failure.**

```bash
uv run pytest tests/test_emitters/test_python.py::test_python_emits_subflow_as_function_call -v
```

Expected: FAIL (current emitter ignores FlowCallIR, likely crashes or misses content).

- [ ] **Step 3: Implement.**

In `clio/emitters/_python_helpers.py` (the module that generates `flow.py` for the python target):

1. Locate the function that renders the flow's `chain` items. Add a branch for `FlowCallIR`:

   ```python
   if isinstance(item, FlowCallIR):
       lines.append(
           f"    state[{item.flow_name!r}] = run_{item.flow_name}(**{_render_kwargs(item.kwargs)})"
       )
       continue
   ```

2. Locate the function that renders the top-level `flow.py` (the one that emits `def run_flow(...)`). Make it iterate `graph.flows` and emit `def run_<flow_name>(**takes) -> dict:` for **every** signed flow (those in `graph.exposed_flow_names` or appearing as sub-flow targets — i.e., every flow that has a signature). The main `run_flow` continues to dispatch to the entry FLOW.

   Decision: emit `def run_<flow_name>(...) -> dict` for **every** `FlowIR` in `graph.flows` whose name has a signature. The unsigned ones are not generated as functions (they can only be the main flow, handled by the existing path).

3. Make sure `FlowCallIR` is imported at the top of `_python_helpers.py`.

- [ ] **Step 4: Verify pass.**

```bash
uv run pytest tests/test_emitters/test_python.py::test_python_emits_subflow_as_function_call -v
```

Expected: PASS.

- [ ] **Step 5: Run all python emitter tests.**

```bash
uv run pytest tests/test_emitters/test_python.py -q --tb=short
```

Expected: green.

- [ ] **Step 6: Commit.**

```bash
git add clio/emitters/python.py clio/emitters/_python_helpers.py tests/test_emitters/test_python.py
git commit -m "feat(v0.17): python target emits sub-flow calls

Each signed FLOW becomes a top-level run_<name>(**takes) function in
flow.py; FlowCallIR sites in any chain invoke them and bind output
under the call name in state."
```

---

## Task 8: Emit Sub-Flow Calls + Multi-Tool in `mcp-server` Target

**Files:**
- Modify: `clio/emitters/mcp_server.py`
- Modify: `clio/emitters/_mcp_helpers.py`
- Modify: `tests/test_emitters/test_mcp_server.py`

- [ ] **Step 1: Write failing tests.**

`tests/test_emitters/test_mcp_server.py`:

```python
def test_mcp_emits_one_tool_per_exposed_flow(tmp_path):
    from clio.parser.parser import parse
    from clio.ir.builder import build_ir
    from clio.emitters.mcp_server import emit
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "  LANG: python\n  IMPL:\n    code: |\n"
        "      def run(x): return {'y': x.upper()}\n\n"
        "FLOW a\n  TAKES: x: str\n  GIVES: y: str\n  s(x=x)\n\n"
        "FLOW b\n  TAKES: x: str\n  GIVES: y: str\n  s(x=x)\n\n"
        "RESOURCES\n  target: mcp-server\n  models: []\n"
    )
    g = build_ir(parse(src))
    emit(g, tmp_path)
    server_py = (tmp_path / next(tmp_path.iterdir()).name / "server.py").read_text()
    assert "def a(" in server_py
    assert "def b(" in server_py
    assert server_py.count("@mcp.tool") == 2


def test_mcp_called_subflow_is_not_exposed(tmp_path):
    from clio.parser.parser import parse
    from clio.ir.builder import build_ir
    from clio.emitters.mcp_server import emit
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "  LANG: python\n  IMPL:\n    code: |\n"
        "      def run(x): return {'y': x.upper()}\n\n"
        "FLOW helper\n  TAKES: x: str\n  GIVES: y: str\n  s(x=x)\n\n"
        "FLOW api\n  TAKES: x: str\n  GIVES: y: str\n  helper(x=x)\n\n"
        "RESOURCES\n  target: mcp-server\n  models: []\n"
    )
    g = build_ir(parse(src))
    emit(g, tmp_path)
    server_py = (tmp_path / next(tmp_path.iterdir()).name / "server.py").read_text()
    assert "def api(" in server_py
    assert server_py.count("@mcp.tool") == 1
    assert "def helper(" not in server_py or "@mcp.tool" not in server_py.split("def helper(")[0].rsplit("\n", 2)[-2]
```

- [ ] **Step 2: Verify failures.**

```bash
uv run pytest tests/test_emitters/test_mcp_server.py::test_mcp_emits_one_tool_per_exposed_flow tests/test_emitters/test_mcp_server.py::test_mcp_called_subflow_is_not_exposed -v
```

Expected: FAIL.

- [ ] **Step 3: Implement.**

In `clio/emitters/mcp_server.py` and `_mcp_helpers.py`:

1. Iterate `graph.exposed_flow_names` and emit one `@mcp.tool()` decorated function per name. Each tool's `inputSchema` / `outputSchema` derives from the matching FlowIR's `takes` / `gives`. Use the same `_declared_field_schema` helper introduced in v0.16.
2. In `flow.py` (or whichever module hosts the orchestrators), emit a function `def <flow_name>(**takes)` for every signed FlowIR (so sub-flow calls resolve). The exposed ones get the MCP wrapper; non-exposed ones are plain functions.
3. `FlowCallIR` compiles to a normal Python function call inside the parent's chain (analogous to Task 7).

- [ ] **Step 4: Verify both tests pass.**

```bash
uv run pytest tests/test_emitters/test_mcp_server.py::test_mcp_emits_one_tool_per_exposed_flow tests/test_emitters/test_mcp_server.py::test_mcp_called_subflow_is_not_exposed -v
```

Expected: PASS.

- [ ] **Step 5: Run all mcp-server emitter tests.**

```bash
uv run pytest tests/test_emitters/test_mcp_server.py -q --tb=short
```

Expected: green. Any single-flow legacy test still produces exactly one tool because `exposed_flow_names` collapses to `{the_one_flow_name}` when there is only one signed FLOW.

- [ ] **Step 6: Commit.**

```bash
git add clio/emitters/mcp_server.py clio/emitters/_mcp_helpers.py tests/test_emitters/test_mcp_server.py
git commit -m "feat(v0.17): mcp-server emits one tool per exposed FLOW

Multi-FLOW sources now produce multi-tool MCP servers. A FLOW is
exposed iff it has a signature and is not called by any sibling.
Sub-flow calls compile to plain function invocations."
```

---

## Task 9: Emit Sub-Flow Calls in `claude-skill` Target

**Files:**
- Modify: `clio/emitters/claude_skill.py`
- Modify: `clio/emitters/_claude_skill_helpers.py`
- Modify: `tests/test_emitters/test_claude_skill.py`

- [ ] **Step 1: Write failing test.**

`tests/test_emitters/test_claude_skill.py`:

```python
def test_claude_skill_emits_subflow_script(tmp_path):
    from clio.parser.parser import parse
    from clio.ir.builder import build_ir
    from clio.emitters.claude_skill import emit
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "  LANG: bash\n  IMPL:\n    code: |\n"
        "      jq -n --arg x \"$x\" '{y:$x}'\n\n"
        "FLOW inner\n  TAKES: x: str\n  GIVES: y: str\n  s(x=x)\n\n"
        "FLOW outer\n  TAKES: x: str\n  GIVES: y: str\n  inner(x=x)\n\n"
        "RESOURCES\n  target: claude-skill\n  models: []\n"
    )
    g = build_ir(parse(src), flow_name="outer")
    emit(g, tmp_path)
    skill_root = next(tmp_path.iterdir())
    scripts = (skill_root / "scripts")
    files = {p.name for p in scripts.iterdir()}
    assert "sub_inner.sh" in files
    main_sh = (scripts / "run.sh").read_text()
    assert "sub_inner.sh" in main_sh
```

- [ ] **Step 2: Verify failure.**

```bash
uv run pytest tests/test_emitters/test_claude_skill.py::test_claude_skill_emits_subflow_script -v
```

Expected: FAIL.

- [ ] **Step 3: Implement.**

In `_claude_skill_helpers.py`:

1. For every signed FlowIR (other than the main), emit a `scripts/sub_<flow_name>.sh` that orchestrates that flow's chain (same logic as the main `run.sh` generator, parameterised).
2. When the main `run.sh` encounters a `FlowCallIR`, emit a `bash scripts/sub_<flow_name>.sh "$arg1" "$arg2" ...` call whose stdout is captured into the state JSON under the call name.

- [ ] **Step 4: Verify pass.**

```bash
uv run pytest tests/test_emitters/test_claude_skill.py::test_claude_skill_emits_subflow_script -v
```

Expected: PASS.

- [ ] **Step 5: Run all claude-skill tests.**

```bash
uv run pytest tests/test_emitters/test_claude_skill.py -q --tb=short
```

Expected: green.

- [ ] **Step 6: Commit.**

```bash
git add clio/emitters/claude_skill.py clio/emitters/_claude_skill_helpers.py tests/test_emitters/test_claude_skill.py
git commit -m "feat(v0.17): claude-skill target emits sub-flow scripts

Each signed sub-FLOW becomes scripts/sub_<flow>.sh; the main run.sh
invokes them and captures their stdout into the state JSON under the
call name."
```

---

## Task 10: Emit Sub-Graph in `langgraph` Target

**Files:**
- Modify: `clio/emitters/langgraph.py`
- Modify: `clio/emitters/_langgraph_helpers.py`
- Modify: `tests/test_emitters/test_langgraph.py`

- [ ] **Step 1: Write failing test.**

`tests/test_emitters/test_langgraph.py`:

```python
def test_langgraph_emits_subgraph_node(tmp_path):
    from clio.parser.parser import parse
    from clio.ir.builder import build_ir
    from clio.emitters.langgraph import emit
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "  LANG: python\n  IMPL:\n    code: |\n"
        "      def run(x): return {'y': x}\n\n"
        "FLOW inner\n  TAKES: x: str\n  GIVES: y: str\n  s(x=x)\n\n"
        "FLOW outer\n  TAKES: x: str\n  GIVES: y: str\n  inner(x=x)\n\n"
        "RESOURCES\n  target: langgraph\n  models: []\n"
    )
    g = build_ir(parse(src), flow_name="outer")
    emit(g, tmp_path)
    flow_py = (tmp_path / "outer" / "flow.py").read_text()
    # The sub-flow becomes its own compiled StateGraph.
    assert "build_inner_graph" in flow_py or "inner_graph" in flow_py
    # The outer graph adds it as a node.
    assert "add_node(\"inner\"" in flow_py
```

- [ ] **Step 2: Verify failure.**

```bash
uv run pytest tests/test_emitters/test_langgraph.py::test_langgraph_emits_subgraph_node -v
```

Expected: FAIL.

- [ ] **Step 3: Implement.**

In `_langgraph_helpers.py`:

1. For every signed FlowIR, emit a helper `def build_<flow_name>_graph() -> StateGraph` that constructs and compiles the sub-flow's StateGraph using its own State TypedDict (subset of the parent — `flow.takes` becomes input, `flow.gives` becomes output).
2. When the parent flow's chain contains a `FlowCallIR`, register the compiled sub-graph as a node: `g.add_node(<call_name>, build_<flow_name>_graph().compile())` and wire its inputs/outputs from the parent state.

- [ ] **Step 4: Verify pass.**

```bash
uv run pytest tests/test_emitters/test_langgraph.py::test_langgraph_emits_subgraph_node -v
```

Expected: PASS.

- [ ] **Step 5: Run all langgraph tests.**

```bash
uv run pytest tests/test_emitters/test_langgraph.py -q --tb=short
```

Expected: green.

- [ ] **Step 6: Commit.**

```bash
git add clio/emitters/langgraph.py clio/emitters/_langgraph_helpers.py tests/test_emitters/test_langgraph.py
git commit -m "feat(v0.17): langgraph target compiles sub-flows to sub-graphs

Each signed sub-FLOW becomes its own StateGraph; sub-flow calls in a
parent flow register the compiled sub-graph as a node."
```

---

## Task 11: Reject Sub-Flow Composition on `claude-cli` Target

**Files:**
- Modify: `clio/emitters/claude_cli.py`
- Modify: `tests/test_emitters/test_claude_cli.py`

- [ ] **Step 1: Write failing test.**

`tests/test_emitters/test_claude_cli.py`:

```python
def test_claude_cli_rejects_subflow_calls(tmp_path):
    from clio.parser.parser import parse
    from clio.ir.builder import build_ir
    from clio.emitters.claude_cli import emit
    import pytest as _pt
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "  LANG: bash\n  IMPL:\n    code: |\n"
        "      jq -n --arg x \"$x\" '{y:$x}'\n\n"
        "FLOW inner\n  TAKES: x: str\n  GIVES: y: str\n  s(x=x)\n\n"
        "FLOW outer\n  TAKES: x: str\n  GIVES: y: str\n  inner(x=x)\n\n"
        "RESOURCES\n  target: claude-cli\n  models: []\n"
    )
    g = build_ir(parse(src), flow_name="outer")
    with _pt.raises(Exception) as ei:
        emit(g, tmp_path)
    msg = str(ei.value)
    assert "claude-cli" in msg and ("sub-flow" in msg.lower() or "composition" in msg.lower())
    assert "v0.17" in msg or "not supported" in msg.lower()
```

- [ ] **Step 2: Verify failure.**

```bash
uv run pytest tests/test_emitters/test_claude_cli.py::test_claude_cli_rejects_subflow_calls -v
```

Expected: FAIL — the emitter currently does something undefined.

- [ ] **Step 3: Implement.**

In `clio/emitters/claude_cli.py`, at the start of `emit(graph, ...)`, scan every flow's chain (including FOR EACH / IF / MATCH / WHILE / RESCUE bodies) for any `FlowCallIR`. If found, raise:

```python
from clio.emitters.base import EmitError  # or whatever the canonical error is

def _check_no_subflow_calls(graph):
    from clio.ir.graph import FlowCallIR
    def walk(items):
        for it in items:
            if isinstance(it, FlowCallIR):
                raise EmitError(
                    f"line {it.line}:0: target=claude-cli does not "
                    f"support FLOW composition (sub-flow calls). "
                    f"v0.17 limitation — use target=python or "
                    f"target=mcp-server."
                )
            for attr in ("body", "then_body", "else_body"):
                if hasattr(it, attr):
                    walk(getattr(it, attr))
            if hasattr(it, "cases"):
                for arm in it.cases:
                    walk(arm.body)
    for f in graph.flows:
        walk(f.chain)
        for r in f.rescues:
            walk(r.body)
```

Call `_check_no_subflow_calls(graph)` first thing in `emit`.

- [ ] **Step 4: Verify pass.**

```bash
uv run pytest tests/test_emitters/test_claude_cli.py::test_claude_cli_rejects_subflow_calls -v
```

Expected: PASS.

- [ ] **Step 5: Run all claude-cli tests.**

```bash
uv run pytest tests/test_emitters/test_claude_cli.py -q --tb=short
```

Expected: green (no existing fixture uses sub-flow calls).

- [ ] **Step 6: Commit.**

```bash
git add clio/emitters/claude_cli.py tests/test_emitters/test_claude_cli.py
git commit -m "feat(v0.17): claude-cli rejects sub-flow composition

v0.17 limitation — bash sub-shell isolation is deferred. Compile-time
error directs users to target=python or target=mcp-server."
```

---

## Task 12: End-to-End Example + Cookbook Recipe

**Files:**
- Create: `examples/flow_composition.clio`
- Modify: `docs/manual/cookbook.md`
- Modify: `tests/test_examples_projects/` (or wherever example-level compilation tests live — `grep` first)

- [ ] **Step 1: Write the example.**

Create `examples/flow_composition.clio`:

```clio
# v0.17 — FLOW composition. Demonstrates two patterns:
#   1. Reuse via a sub-flow called inside a FOR EACH PARALLEL.
#   2. Encapsulated RESCUE: the failure handler lives in the sub-flow
#      and a single sub-flow call wraps the whole "guarded section".

STEP fetch_article
  TAKES:   url:     str
  GIVES:   article: str
  MODE:    exact
  LANG:    python
  IMPL:
    code: |
      import urllib.request
      def run(url):
          return {"article": urllib.request.urlopen(url, timeout=10).read().decode()}
  ON_FAIL: retry(2) then abort("fetch failed")

STEP summarize
  TAKES:   article: str
  GIVES:   summary: str
  MODE:    judgment
  CACHE:   ttl(1d)

FLOW enrich
  TAKES:   url:      str
  GIVES:   summary:  str
  fetch_article(url=url) -> summarize(article=@fetch_article.article)

FLOW pipeline
  TAKES:   urls:     List<str>
  GIVES:   results:  List<str>
  FOR EACH u IN urls PARALLEL AS results:
    enrich(url=u)

RESOURCES
  target: python
  models: [haiku, sonnet]
```

- [ ] **Step 2: Verify it compiles to all supported targets.**

```bash
uv run python -m clio check examples/flow_composition.clio
uv run python -m clio compile examples/flow_composition.clio --target python --output /tmp/clio_fc_py
uv run python -m clio compile examples/flow_composition.clio --target mcp-server --output /tmp/clio_fc_mcp
uv run python -m clio compile examples/flow_composition.clio --target langgraph --output /tmp/clio_fc_lg
uv run python -m clio compile examples/flow_composition.clio --target claude-skill --output /tmp/clio_fc_skill
```

Expected: all four emit without errors.

- [ ] **Step 3: Verify claude-cli rejects it.**

```bash
uv run python -m clio compile examples/flow_composition.clio --target claude-cli --output /tmp/clio_fc_cli || echo "EXPECTED FAILURE"
```

Expected: clean error message, exit code != 0.

- [ ] **Step 4: Add an automated example test.**

Locate `tests/test_examples_projects/` and follow the local pattern (likely a parametrized test over `examples/*.clio`). Add a test case (or rely on existing parametrization if it auto-picks `examples/`):

```python
def test_flow_composition_example_compiles_all_targets(tmp_path):
    from clio.parser.parser import parse
    from clio.ir.builder import build_ir
    from clio.emitters.python import emit as emit_py
    from clio.emitters.mcp_server import emit as emit_mcp
    from clio.emitters.langgraph import emit as emit_lg
    from clio.emitters.claude_skill import emit as emit_sk
    src = (Path(__file__).parent.parent.parent / "examples" / "flow_composition.clio").read_text()
    g = build_ir(parse(src), flow_name="pipeline")
    emit_py(g, tmp_path / "py")
    emit_mcp(g, tmp_path / "mcp")
    emit_lg(g, tmp_path / "lg")
    emit_sk(g, tmp_path / "sk")
```

(If the existing test harness already parametrizes `examples/*.clio`, drop this manual test and just rely on the auto-pick.)

- [ ] **Step 5: Cookbook recipe.**

Append a new section to `docs/manual/cookbook.md`:

```markdown
## Composing FLOWs (v0.17+)

A FLOW with explicit `TAKES:` / `GIVES:` (added in v0.16) can be called
wherever a STEP is legal — directly in a chain, inside a `FOR EACH
PARALLEL` body, or inside an `IF` / `MATCH` / `WHILE` / `RESCUE` body.

Sub-flow calls run in their own scope: only the declared `GIVES` cross
back into the parent. This makes FLOWs the natural unit of reuse and
modular decomposition.

### Reuse pattern

[Show enrich + pipeline excerpt from examples/flow_composition.clio]

### Encapsulated RESCUE pattern

[Show how a sub-flow with a RESCUE handler can wrap a guarded section,
so the parent only sees one clean call.]

### Limitations (v0.17)

- A FLOW without `TAKES:` and `GIVES:` is not callable. Declare the
  signature first.
- Recursive FLOWs (A calls A) and inter-flow cycles (A → B → A) are
  rejected at compile time.
- `target: claude-cli` does not yet support sub-flow calls (bash
  sub-shell isolation deferred). Use `target: python` or
  `target: mcp-server`.
- Cross-file imports are deferred to a later sprint.
```

- [ ] **Step 6: Run full test suite.**

```bash
uv run pytest tests/ -q --tb=no
```

Expected: green.

- [ ] **Step 7: Commit.**

```bash
git add examples/flow_composition.clio docs/manual/cookbook.md tests/test_examples_projects/
git commit -m "docs(v0.17): flow_composition example + cookbook recipe

End-to-end example exercising both reuse and encapsulated-RESCUE
composition patterns. Compiles cleanly to python, mcp-server,
langgraph, claude-skill; rejected with a clear error on claude-cli."
```

---

## Task 13: Update `LANGUAGE_SPEC.md`

**Files:**
- Modify: `docs/LANGUAGE_SPEC.md`

- [ ] **Step 1: Locate insertion point.**

```bash
grep -n "^### FLOW signature" docs/LANGUAGE_SPEC.md
```

Expected output: `512:### FLOW signature (v0.16, optional)`.

- [ ] **Step 2: Insert new section.**

After the `### FLOW signature (v0.16, optional)` section (find the next `###` heading and insert just before it), add:

```markdown
### FLOW composition (v0.17)

Once a FLOW declares both `TAKES:` and `GIVES:`, it is structurally
indistinguishable from a STEP. The compiler lets you call such a FLOW
wherever a STEP call is legal — in a chain, in a `FOR EACH PARALLEL`
body, inside `IF` / `MATCH` / `WHILE`, or inside a `RESCUE` body.

Syntax: identical to a step call.

    pipeline(urls=raw_urls)

Resolution:

1. The name is looked up first in declared `STEP`s.
2. If not found, it is looked up in `FLOW`s that have an explicit
   signature.
3. A name shared between a STEP and a FLOW is rejected at compile
   time (collision).
4. A call to a FLOW that lacks `TAKES` / `GIVES` is rejected with a
   clear error.

Semantics:

- The sub-flow's `TAKES` are bound positionally by keyword (same as a
  step call), and each kwarg is type-checked against the declared
  signature.
- The sub-flow runs in its own scope — the parent's state is **not**
  visible to it. Only the declared `GIVES` of the sub-flow cross
  back into the parent, where they are bound under the sub-flow call
  name (or its alias, if used). Access as `<call_name>.<field>`.
- `RESCUE` handlers declared inside a sub-flow run only inside that
  sub-flow's scope. If they terminate the sub-flow with `abort(...)`,
  the abort propagates to the parent as a regular failure — the
  parent can in turn protect the sub-flow call with its own
  `RESCUE`.
- Recursive sub-flows (`A` calls `A`) and inter-flow cycles
  (`A → B → A`) are rejected at compile time.

### Default exposure of FLOWs (`target: mcp-server`)

When compiling to `target: mcp-server`, every FLOW that has an explicit
signature **and** is not called by any sibling FLOW in the same source
becomes a tool. Called sub-flows remain internal helpers and are not
exposed. An explicit `EXPOSE` / `INTERNAL` marker is deferred to a
later release.

### Target support

| target          | Sub-flow call support (v0.17) |
| --------------- | ----------------------------- |
| `python`        | yes (sub-flow → function)     |
| `mcp-server`    | yes (sub-flow → function; uncalled FLOWs become tools) |
| `claude-skill`  | yes (sub-flow → `scripts/sub_<name>.sh`)               |
| `langgraph`     | yes (sub-flow → compiled sub-`StateGraph`)             |
| `claude-cli`    | **no** — compile-time error (deferred to a later release) |
```

- [ ] **Step 3: Skim the rest of the spec for outdated cross-references.**

```bash
grep -n "FLOW signature\|sub-flow\|composition" docs/LANGUAGE_SPEC.md
```

Update any "v0.16+ only" statement that is now stale.

- [ ] **Step 4: Commit.**

```bash
git add docs/LANGUAGE_SPEC.md
git commit -m "docs(v0.17): LANGUAGE_SPEC §FLOW composition

Documents the call-resolution rules, scoping semantics, cycle/recursion
restrictions, default mcp-server exposure rule, and per-target support
table."
```

---

## Task 14: Release — Bump Version, CHANGELOG, README, lock

**Files:**
- Modify: `pyproject.toml:7` (`0.16.0` → `0.17.0`)
- Regenerate: `uv.lock`
- Modify: `CHANGELOG.md`
- Modify: `README.md` (badge + test count)

- [ ] **Step 1: Bump `pyproject.toml`.**

Edit line 7: `version = "0.17.0"`.

- [ ] **Step 2: Regenerate lock.**

```bash
uv lock
```

Expected: `uv.lock` updates the `clio` self-version.

- [ ] **Step 3: Close `[Unreleased]` in CHANGELOG.**

In `CHANGELOG.md`, rename the `## [Unreleased]` heading to:

```markdown
## v0.17.0 — 2026-05-15

### Added

- **FLOW composition** — a FLOW with explicit `TAKES:` / `GIVES:`
  (v0.16) can now be called wherever a STEP is legal, in chains,
  `FOR EACH PARALLEL` bodies, `IF` / `MATCH` / `WHILE`, and `RESCUE`
  bodies. Resolution: step name first, signed flow name second; a
  shared name is a compile-time collision. Recursive sub-flows and
  inter-flow cycles are rejected at compile time. Closes #24.
- **`target: mcp-server`** — multi-FLOW sources now produce one
  `@mcp.tool()` per *exposed* FLOW (every signed FLOW that is not
  called by a sibling). Sub-flow calls compile to plain function
  calls.
- **`FlowCallIR`** — new IR node, distinct from `CallIR`, returned
  by `_build_call` when the call resolves to a signed FLOW.
- **`FlowGraph.flows`** + **`FlowGraph.exposed_flow_names`** — every
  FlowIR is now built (not just the main); emitters that care about
  sub-flow scaffolding consume them.

### Changed

- `_build_call` now accepts a flow-signature map; all downstream
  builder helpers thread it through.
- `claude-cli` rejects any source containing sub-flow calls with a
  clear `EmitError` (sub-shell-based isolation is deferred).

### Known limitations

- No cross-file `IMPORT` yet — all sub-flow callees must live in the
  same `.clio` source.
- No `EXPOSE` / `INTERNAL` marker — the default rule (expose
  uncalled signed FLOWs) is fixed for now.

Open a new `## [Unreleased]` heading above with empty
`### Added` / `### Changed` / `### Fixed` placeholders.
```

- [ ] **Step 4: Update README test count.**

```bash
uv run pytest tests/ -q --tb=no | tail -3
```

Expected: prints something like `XXX passed, 15 skipped, 1 xfailed`.

Edit `README.md`: replace the previous test-count line (search `passed,`) with the new figure, and bump any version badge from `0.16.0` to `0.17.0`.

- [ ] **Step 5: Run lint + tests one last time.**

```bash
uv run ruff check . --fix && uv run pytest tests/ -q --tb=no
```

Expected: clean.

- [ ] **Step 6: Commit.**

```bash
git add pyproject.toml uv.lock CHANGELOG.md README.md
git commit -m "chore(release): v0.17.0

FLOW composition (issue #24): sub-flow callable as a step + multi-tool
mcp-server emission. See CHANGELOG for the full surface."
```

---

## Task 15: Open PR + Address Gemini Review

- [ ] **Step 1: Push and open PR.**

```bash
git push -u origin feat/v0.17-flow-composition
gh pr create --title "feat(v0.17): FLOW composition — sub-flow callable as a step (#24)" --body "$(cat <<'EOF'
## Summary

- Closes #24 (depends on #23 already landed in v0.16.0).
- Adds `FlowCallIR`, builds every FlowIR (not just the main), detects
  recursion and inter-flow cycles, rejects step/flow name collisions.
- python / mcp-server / claude-skill / langgraph emitters compile
  sub-flow calls.
- mcp-server now emits one `@mcp.tool()` per uncalled signed FLOW.
- claude-cli rejects sub-flow calls with a clear error (deferred).
- New example, cookbook recipe, LANGUAGE_SPEC §FLOW composition.
- Bumped to v0.17.0.

## Test plan

- [ ] `uv run ruff check .` — clean
- [ ] `uv run pytest tests/ -q` — green
- [ ] `uv run python -m clio compile examples/flow_composition.clio --target python`
- [ ] `uv run python -m clio compile examples/flow_composition.clio --target mcp-server`
- [ ] `uv run python -m clio compile examples/flow_composition.clio --target langgraph`
- [ ] `uv run python -m clio compile examples/flow_composition.clio --target claude-skill`
- [ ] `uv run python -m clio compile examples/flow_composition.clio --target claude-cli` → expected error

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Wait for Gemini review and CI.**

```bash
gh pr checks
gh pr view --comments
```

- [ ] **Step 3: Address each Gemini comment.**

For every Gemini comment:
1. Decide apply / pushback (per the project memory rule on Gemini reviews).
2. If apply: commit the fix with a `review(v0.17): address Gemini comment on …` message.
3. Reply on the comment thread quoting the fix commit SHA (or stating the rationale for pushback).

- [ ] **Step 4: Merge once green + reviewed.**

User confirms merge target (typically squash-merge into `main`). Then:

```bash
git checkout main && git pull
git tag v0.17.0
git push --tags
```

- [ ] **Step 5: Close the loop.**

```bash
gh issue view 24
```

Expected: auto-closed by PR merge.

---

## Self-Review Checklist (applied)

- **Spec coverage.** All sections of issue #24 are mapped:
  - Worked example "Reuse via sub-flow" → Task 12 example.
  - Worked example "Encapsulated RESCUE" → cookbook recipe, Task 12.
  - Semantics (resolution order, collision, signature-required,
    isolated sub-flow state, RESCUE locality, cycle/recursion
    rejection) → Tasks 3–5 + Task 13 docs.
  - Out of scope (`IMPORT`, `EXPOSE`/`INTERNAL`, recursion) → noted
    in Task 13 docs + CHANGELOG known limitations.
  - Implementation sketch (parser unchanged, IR resolution, cycle
    detection, per-target emit) → Tasks 1–11.
  - Tests (IR + python + mcp-server + claude-skill + langgraph +
    claude-cli + example) → Tasks 1–12.
- **Placeholder scan.** No "TBD" / "TODO" / "implement later" / "add
  error handling" left in the plan.
- **Type consistency.** `FlowCallIR`, `FlowSignature`,
  `_extract_flow_signatures`, `_detect_flow_call_cycles`,
  `_compute_exposed_flows`, `FlowGraph.flows`,
  `FlowGraph.exposed_flow_names` are used consistently across all
  tasks. The `_build_call` signature change in Task 4 propagates to
  every downstream `_build_*` helper.

---

## Execution Choice

Plan complete and saved to `docs/superpowers/plans/2026-05-15-flow-composition.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks, fast iteration. Matches the workflow used for v0.15 and v0.16.
2. **Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

**Which approach?**
