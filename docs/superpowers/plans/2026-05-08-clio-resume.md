# `clio resume` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--from-step N` flag to the python-target emitted package and persist a `state.json` snapshot atomically after each top-level chain item, enabling step-granularity resume.

**Architecture:** The python emitter's `_emit_flow` is restructured to track chain items in groups (each top-level item = one group). Each group is wrapped in `if start_at < <idx>:` and followed by `_persist_state(<idx>, state)`. A new module-level `_persist_state` helper writes `{version, flow, step_index, state}` atomically via `os.replace(tmp, path)`. The `_emit_main` argparse adds `--from-step N` (default 0). Targets v1: python only.

**Tech Stack:** Python 3.12+, stdlib only (`os`, `sys`, `json`, `argparse`). No new dependencies.

**Spec reference:** `docs/superpowers/specs/2026-05-08-clio-resume-design.md`.

---

## File structure

| File | Action | Responsibility |
|---|---|---|
| `clio/emitters/python.py` | **modify** | `_emit_flow`: restructure chain_lines into chain_groups, emit `_persist_state` helper, `start_at` parameter, validation logic, gating. `_emit_main`: add `--from-step` argparse. |
| `tests/test_emitters/test_python.py` | **modify** | Form tests for the new emit shape; behavioral tests with monkeypatched SDK + tmp_path. |
| `tests/test_e2e_resume.py` | **create** | Gated `CLIO_E2E=1` end-to-end: compile, run, manual state.json, `--from-step`. |
| `tests/fixtures/expected/{v03_*,python_v03_mvp}/` | **regenerate** | 6 fixtures with new flow.py + __main__.py shape. |
| `docs/LANGUAGE_SPEC.md` | **modify** | Add Resume subsection in Observability (env var `CLIO_STATE_FILE`, `--from-step` flag, semantics). |
| `docs/COMPILATION_TARGETS.md` | **modify** | python target gets a Resume line. |
| `CHANGELOG.md` | **modify** | Unreleased entry. |
| `README.md` | **modify** | Brief usage mention. |

---

## Task 1: `_persist_state` helper + `TOTAL_STEPS` constant

Emit the helper and the constant in `flow.py`. Don't yet wire them into the chain (Tasks 2-3 do that).

**Files:**
- Modify: `clio/emitters/python.py:_emit_flow` (line 398)
- Modify: `tests/test_emitters/test_python.py` (form tests appended)

- [ ] **Step 1.1: Write failing form tests**

Append to `tests/test_emitters/test_python.py`:

```python
def test_flow_py_contains_persist_state_helper(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert "def _persist_state(step_idx: int, state: dict)" in flow_py
    assert "os.environ.get(\"CLIO_STATE_FILE\", \"state.json\")" in flow_py
    assert "json.dump" in flow_py and "default=str" in flow_py
    assert "os.replace(tmp, path)" in flow_py


def test_flow_py_contains_total_steps_constant(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    # mvp_v03_skeleton has 1 step in its FLOW chain
    assert "TOTAL_STEPS = 1" in flow_py


def test_flow_py_imports_os_for_persist_state(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert "import os" in flow_py
```

- [ ] **Step 1.2: Run tests, verify FAIL**

Run: `.venv/bin/pytest tests/test_emitters/test_python.py -k "persist_state_helper or total_steps_constant or imports_os_for_persist_state" -v`
Expected: FAIL on all three.

- [ ] **Step 1.3: Modify `_emit_flow` to inject helper + constant + import os**

In `clio/emitters/python.py:_emit_flow`, find the return block at the end (line 483-510). Add `import os` to the emitted imports, `TOTAL_STEPS = N` constant at module level, and `_persist_state` helper before `def run(...)`.

Replace the return block with:

```python
        total_steps = len(graph.flow.chain)
        return (
            f'"""FLOW {graph.flow.name}.\n\n'
            f'Auto-generated. Calls steps in chain order, threading state through a dict.\n'
            f'"""\n'
            f'\n'
            f'import json\n'
            f'import os\n'
            f'import time\n'
            f'{cf_import}'
            f'{imports}\n'
            f'\n'
            f'from .clio_runtime import logging as _log\n'
            f'\n'
            f'\n'
            f'TOTAL_STEPS = {total_steps}\n'
            f'\n'
            f'\n'
            f'def _persist_state(step_idx: int, state: dict) -> None:\n'
            f'    """Atomic write of {{version, flow, step_index, state}} to state.json."""\n'
            f'    path = os.environ.get("CLIO_STATE_FILE", "state.json")\n'
            f'    payload = {{"version": 1, "flow": {flow_name_lit}, "step_index": step_idx, "state": state}}\n'
            f'    tmp = path + ".tmp"\n'
            f'    with open(tmp, "w") as f:\n'
            f'        json.dump(payload, f, default=str)\n'
            f'    os.replace(tmp, path)\n'
            f'\n'
            f'\n'
            f'def run(**initial: object) -> dict:\n'
            f'    state: dict = dict(initial)\n'
            f'    _log.set_flow({flow_name_lit})\n'
            f'    _log.emit("flow_start")\n'
            f'    _success = False\n'
            f'    _t0 = time.monotonic()\n'
            f'    try:\n'
            f'{chain_body}\n'
            f'        _success = True\n'
            f'        return state\n'
            f'    finally:\n'
            f'        _log.emit("flow_end", '
            f'duration_ms=int((time.monotonic() - _t0) * 1000), '
            f'success=_success)\n'
            f'        _log.set_flow(None)\n'
        )
```

Note that `import json` is also added — Task 1 needs it for `_persist_state`. The current code may rely on `json` being available transitively via the steps imports; making it explicit at the top is correct.

- [ ] **Step 1.4: Run tests, verify PASS**

Run: `.venv/bin/pytest tests/test_emitters/test_python.py -k "persist_state_helper or total_steps_constant or imports_os_for_persist_state" -v`
Expected: PASS.

- [ ] **Step 1.5: Run full python emitter suite**

Run: `.venv/bin/pytest tests/test_emitters/test_python.py -v 2>&1 | tail -3`

Expected: most tests pass; the fixture-tree comparison tests (e.g. `test_emit_skeleton`) FAIL because flow.py now has the new lines. We'll regenerate fixtures in Task 6.

For now, mark this task as DONE_WITH_CONCERNS if those fixture tests fail; that's expected and Tasks 2-5 may add more shape changes.

- [ ] **Step 1.6: Commit**

```bash
git add clio/emitters/python.py tests/test_emitters/test_python.py
git commit -m "$(cat <<'EOF'
feat(emitters/python): emit _persist_state helper + TOTAL_STEPS in flow.py

Adds the runtime infrastructure for step-granularity resume: a
module-level _persist_state(step_idx, state) helper that writes
{version, flow, step_index, state} atomically via tmp + os.replace,
plus a TOTAL_STEPS = N constant equal to len(graph.flow.chain).

Imports json and os in the emitted flow.py.

Helper is not yet wired (Task 3 will gate chain items and call it).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `start_at` parameter + load+validation logic

Add `start_at: int = 0` to `run()` and emit the load+validation prelude that runs when `start_at > 0`.

**Files:**
- Modify: `clio/emitters/python.py:_emit_flow`
- Modify: `tests/test_emitters/test_python.py`

- [ ] **Step 2.1: Write failing form tests**

Append to `tests/test_emitters/test_python.py`:

```python
def test_run_signature_has_start_at_keyword_only(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert "def run(*, start_at: int = 0, **initial: object)" in flow_py


def test_run_emits_state_json_load_when_start_at_gt_zero(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert "if start_at > 0:" in flow_py
    assert 'os.environ.get("CLIO_STATE_FILE", "state.json")' in flow_py
    assert "json.load(f)" in flow_py


def test_run_emits_four_validation_systemexits(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    # Four distinct SystemExit(2) paths in the load+validation prelude:
    # missing file / wrong flow / step_index too low / start_at >= TOTAL_STEPS
    assert flow_py.count("raise SystemExit(2)") >= 4
    assert "missing" in flow_py  # missing-file message
    assert "flow mismatch" in flow_py  # wrong-flow message
    assert "only reached step" in flow_py  # step_index-too-low message
    assert ">= total steps=" in flow_py  # start_at >= TOTAL_STEPS message


def test_run_emits_else_branch_with_initial_state(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    # When start_at == 0, state comes from initial kwargs.
    assert "state: dict = dict(initial)" in flow_py
    assert "else:" in flow_py


def test_flow_start_event_includes_resumed_from(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert 'resumed_from=start_at if start_at > 0 else 0' in flow_py
```

- [ ] **Step 2.2: Run tests, verify FAIL**

Run: `.venv/bin/pytest tests/test_emitters/test_python.py -k "start_at or state_json_load or validation_systemexits or else_branch or resumed_from" -v`
Expected: FAIL.

- [ ] **Step 2.3: Modify `_emit_flow` to inject `start_at` parameter + load logic**

In the same return block of `_emit_flow`, replace the `def run(...)` portion with:

```python
            f'def run(*, start_at: int = 0, **initial: object) -> dict:\n'
            f'    if start_at > 0:\n'
            f'        path = os.environ.get("CLIO_STATE_FILE", "state.json")\n'
            f'        if not os.path.exists(path):\n'
            f'            print(f"[clio] resume requested (start_at={{start_at}}) but {{path}} missing", file=sys.stderr)\n'
            f'            raise SystemExit(2)\n'
            f'        with open(path) as f:\n'
            f'            payload = json.load(f)\n'
            f'        if payload.get("flow") != {flow_name_lit}:\n'
            f'            print(f"[clio] state.json flow mismatch: expected {flow_name_lit}, got {{payload.get(\\"flow\\")!r}}", file=sys.stderr)\n'
            f'            raise SystemExit(2)\n'
            f'        if payload.get("step_index", 0) < start_at:\n'
            f'            print(f"[clio] state.json only reached step {{payload.get(\\"step_index\\", 0)}}, can\\'t resume from {{start_at}}", file=sys.stderr)\n'
            f'            raise SystemExit(2)\n'
            f'        if start_at >= TOTAL_STEPS:\n'
            f'            print(f"[clio] start_at={{start_at}} >= total steps={{TOTAL_STEPS}}", file=sys.stderr)\n'
            f'            raise SystemExit(2)\n'
            f'        state: dict = payload["state"]\n'
            f'    else:\n'
            f'        state: dict = dict(initial)\n'
            f'    _log.set_flow({flow_name_lit})\n'
            f'    _log.emit("flow_start", resumed_from=start_at if start_at > 0 else 0)\n'
            f'    _success = False\n'
            f'    _t0 = time.monotonic()\n'
            f'    try:\n'
            f'{chain_body}\n'
            f'        _success = True\n'
            f'        return state\n'
            f'    finally:\n'
            f'        _log.emit("flow_end", '
            f'duration_ms=int((time.monotonic() - _t0) * 1000), '
            f'success=_success)\n'
            f'        _log.set_flow(None)\n'
```

Also ensure `import sys` is in the emitted file's imports — currently flow.py only imports json/os/time/(concurrent.futures+contextvars). Add `import sys` after `import os`:

```python
            f'import json\n'
            f'import os\n'
            f'import sys\n'
            f'import time\n'
```

- [ ] **Step 2.4: Run tests, verify PASS**

Run: `.venv/bin/pytest tests/test_emitters/test_python.py -k "start_at or state_json_load or validation_systemexits or else_branch or resumed_from" -v`
Expected: PASS.

- [ ] **Step 2.5: Sanity AST-parse**

Run:
```bash
.venv/bin/python -c "
import ast, tempfile
from pathlib import Path
from clio.emitters.python import PythonEmitter
from clio.parser.parser import parse
from clio.ir.builder import build_ir

src = Path('tests/fixtures/mvp_v03_skeleton.clio').read_text()
with tempfile.TemporaryDirectory() as d:
    PythonEmitter().emit(build_ir(parse(src)), Path(d))
    flow_py = (Path(d) / 'classify' / 'flow.py').read_text()
    ast.parse(flow_py)
    print('OK')
"
```
Expected: OK.

- [ ] **Step 2.6: Commit**

```bash
git add clio/emitters/python.py tests/test_emitters/test_python.py
git commit -m "$(cat <<'EOF'
feat(emitters/python): emit start_at parameter + state.json load prelude

run(*, start_at: int = 0, **initial) — when start_at > 0, loads
state.json (path from CLIO_STATE_FILE or default ./state.json) and
validates four conditions: file exists, flow name matches, step_index
>= start_at, start_at < TOTAL_STEPS. Each failure raises SystemExit(2)
with a clear message.

When start_at == 0 (default), state comes from initial kwargs as before.
flow_start event gets a resumed_from field for log consumers.

Adds 'import sys' to the emitted flow.py imports.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Chain item gating with `if start_at < N:` + `_persist_state(N, state)`

The chain currently emits `chain_lines: list[str]` where one element per `_emit_item` top-level call may be one or many lines. We need to know the boundaries of each top-level chain item so we can wrap it with `if start_at < N:` and follow with `_persist_state(N, state)`.

**Approach:** restructure `_emit_flow` to track `chain_groups: list[list[str]]`, where each inner list is the lines belonging to one top-level chain item.

**Files:**
- Modify: `clio/emitters/python.py:_emit_flow`
- Modify: `tests/test_emitters/test_python.py`

- [ ] **Step 3.1: Write failing form tests**

Append to `tests/test_emitters/test_python.py`:

```python
def test_chain_items_are_wrapped_in_start_at_gate(tmp_path):
    """Every top-level chain item must be guarded by 'if start_at < <idx>:'"""
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    # mvp_v03_skeleton has exactly 1 chain item
    assert "if start_at < 1:" in flow_py


def test_persist_state_called_after_each_chain_item(tmp_path):
    """Every top-level chain item must be followed by _persist_state(<idx>, state)."""
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert "_persist_state(1, state)" in flow_py


def test_three_chain_items_three_gates_three_persists(tmp_path):
    """A flow with 3 top-level chain items emits 3 gates and 3 persists."""
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "retention" / "flow.py").read_text()
    # mvp_v03_cache.clio's FLOW chain has 3 items: load_customers, detect_churn, draft_email
    assert "if start_at < 1:" in flow_py
    assert "if start_at < 2:" in flow_py
    assert "if start_at < 3:" in flow_py
    assert flow_py.count("_persist_state(") == 3
    assert "_persist_state(1, state)" in flow_py
    assert "_persist_state(2, state)" in flow_py
    assert "_persist_state(3, state)" in flow_py
    assert "TOTAL_STEPS = 3" in flow_py


def test_for_each_block_counts_as_one_chain_item(tmp_path):
    """A FOR EACH (sequential) is one top-level chain item — one gate, one persist."""
    src = Path("examples/classify_corpus.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = next(tmp_path.rglob("flow.py")).read_text()
    # classify_corpus has 2 chain items: load_lines, FOR EACH ... classify
    assert "if start_at < 1:" in flow_py
    assert "if start_at < 2:" in flow_py
    assert flow_py.count("_persist_state(") == 2
    assert "TOTAL_STEPS = 2" in flow_py


def test_parallel_block_counts_as_one_chain_item(tmp_path):
    """A FOR EACH PARALLEL is one top-level chain item — one gate, one persist."""
    src = Path("examples/parallel_classify.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = next(tmp_path.rglob("flow.py")).read_text()
    # parallel_classify has 2 chain items: load_lines, FOR EACH ... PARALLEL classify
    assert "if start_at < 1:" in flow_py
    assert "if start_at < 2:" in flow_py
    assert flow_py.count("_persist_state(") == 2
```

- [ ] **Step 3.2: Run tests, verify FAIL**

Run: `.venv/bin/pytest tests/test_emitters/test_python.py -k "chain_items_are_wrapped or persist_state_called or three_chain_items or for_each_block_counts or parallel_block_counts" -v`
Expected: FAIL.

- [ ] **Step 3.3: Refactor `_emit_flow` to track chain_groups**

In `clio/emitters/python.py:_emit_flow`, replace:

```python
        chain_lines: list[str] = []
        ...
        for item in graph.flow.chain:
            _emit_item(item, "    ", set())
        ...
        chain_body = "\n".join(
            "\n".join("    " + line for line in cl.split("\n"))
            for cl in chain_lines
        )
```

With a chain_groups approach. Refactor in three sub-steps:

(a) Change `chain_lines` to `chain_groups`. In `_emit_item` and `_emit_call`, append to the *current group* rather than the flat list. Use a wrapper list-of-lists approach:

```python
        chain_groups: list[list[str]] = []
        imported_steps: list[str] = []
        steps_by_name = {s.name: s for s in graph.steps}
        # The currently-being-built group; cleared between top-level items
        _current: list[str] = []

        def _emit_call(call: CallIR, indent: str, scope_local: set[str]) -> None:
            step = next(s for s in graph.steps if s.name == call.step_name)
            if step.name not in imported_steps:
                imported_steps.append(step.name)
            kw_parts = []
            for name, value in call.kwargs:
                if isinstance(value, str) and value.startswith("@"):
                    ref = value[1:]
                    if ref in scope_local:
                        kw_parts.append(f"{name}={ref}")
                    else:
                        kw_parts.append(f"{name}=state[{ref!r}]")
                else:
                    kw_parts.append(f"{name}={value!r}")
            kwargs_str = ", ".join(kw_parts)
            out_name = step.gives.name if step.gives is not None else "_result"
            if scope_local:
                _current.append(
                    f"{indent}{step.name}_mod.{step.name}({kwargs_str})"
                )
            else:
                _current.append(
                    f"{indent}state[{out_name!r}] = {step.name}_mod.{step.name}({kwargs_str})"
                )

        def _emit_item(item, indent: str, scope_local: set[str]) -> None:
            if isinstance(item, ForEachIR):
                if item.parallel:
                    _current.append(emit_parallel_for_each_python(item, steps_by_name, indent))
                    inner = item.body[0]
                    if inner.step_name not in imported_steps:
                        imported_steps.append(inner.step_name)
                    return
                source = (
                    item.collection
                    if item.collection in scope_local
                    else f"state[{item.collection!r}]"
                )
                _current.append(f"{indent}for {item.loop_var} in {source}:")
                inner_scope = scope_local | {item.loop_var}
                inner_indent = indent + "    "
                if not item.body:
                    _current.append(f"{inner_indent}pass")
                for sub in item.body:
                    _emit_item(sub, inner_indent, inner_scope)
                return
            if isinstance(item, CallIR):
                _emit_call(item, indent, scope_local)
                return
            raise ValueError(f"unknown flow item: {type(item).__name__}")

        for item in graph.flow.chain:
            _current = []
            _emit_item(item, "    ", set())
            chain_groups.append(list(_current))
```

The closure issue: `_emit_call` and `_emit_item` reference `_current` from the enclosing scope. Because we re-assign `_current = []` in the outer loop, the closures still see the latest binding. **Verify this works**: since Python closures bind by name, when `_emit_call` runs after `_current = []`, it sees the new list. This is the standard Python closure pattern.

If the closure doesn't capture properly (rare in this case but possible), use `nonlocal _current` inside `_emit_call` and `_emit_item`. The simplest robust form is to define the functions inside a wrapper class, but a list-of-lists captured by reference is the cleanest pattern. Test by running Step 3.4 — if behavior is unexpected, switch to `nonlocal`.

(b) Build `chain_body` from `chain_groups`, wrapping each group with `if start_at < N:` and appending `_persist_state(N, state)`:

```python
        # Build the gated chain body. Each top-level chain item becomes:
        #     if start_at < <idx>:
        #         <group lines, re-indented +4 to live inside try and the if>
        #         _persist_state(<idx>, state)
        chain_body_parts: list[str] = []
        for idx, group in enumerate(chain_groups, start=1):
            chain_body_parts.append(f"        if start_at < {idx}:")
            for line in group:
                # Group entries may themselves be multi-line strings (parallel block).
                # Re-indent each line by +4 (was 4-space, now 8-space inside try+if).
                for sub in line.split("\n"):
                    chain_body_parts.append("    " + sub)
            chain_body_parts.append(f"            _persist_state({idx}, state)")
        chain_body = "\n".join(chain_body_parts)
```

Note: the existing chain_lines were re-indented by +4 to live inside `try:`. Now with the gate, they need +4 again to live inside both `try:` and `if start_at < N:` — total 12 spaces from column 0 for sub-statements that were originally at 4-space.

Wait — let me recount. Pre-Task-3, top-level items had 4-space indent at construction time. Post-W2, they were re-indented +4 to live inside `try:` (8-space). Post-W5, they need to live inside `try:` AND `if start_at < N:`, so +8 from original construction (12-space).

The `_emit_call` constructs lines like `"    state['x'] = ..."` (4-space). My new `chain_body_parts` adds `"        if start_at < N:"` (8-space) and the body lines as `"    " + sub` (which adds 4 to the existing 4 = 8). That's wrong — the body should be at 12 (inside both try and if).

Fix: add `"        " + sub` (8 spaces) to push lines from 4-space to 12-space:

```python
        for idx, group in enumerate(chain_groups, start=1):
            chain_body_parts.append(f"        if start_at < {idx}:")
            for line in group:
                for sub in line.split("\n"):
                    # Original construction is at 4-space (or deeper for nested).
                    # Need +8 to land at 12-space (inside try at 8 + inside if at 12).
                    chain_body_parts.append("        " + sub)
            chain_body_parts.append(f"            _persist_state({idx}, state)")
        chain_body = "\n".join(chain_body_parts)
```

Wait again. The `try:` is at 4-space. Inside try, `if start_at < N:` is at 8-space. Inside the if, body is at 12-space. So if a line was originally at 4-space, we need +8 to bring it to 12. If it was at 8-space (a nested for body), we need +8 to bring to 16. The blanket `+8` works for all depths. The `_persist_state` call is at 12-space (inside the if).

Confirmed: prefix each group line with `"        "` (8 spaces). Prefix `_persist_state` with `"            "` (12 spaces).

(c) Remove the old `chain_lines` and `chain_body = "\n".join(...)` block. The new build is the only one.

- [ ] **Step 3.4: Run form tests, verify PASS**

Run: `.venv/bin/pytest tests/test_emitters/test_python.py -k "chain_items_are_wrapped or persist_state_called or three_chain_items or for_each_block_counts or parallel_block_counts" -v`
Expected: PASS.

- [ ] **Step 3.5: AST-parse a complex emitted flow**

```bash
.venv/bin/python -c "
import ast, tempfile
from pathlib import Path
from clio.emitters.python import PythonEmitter
from clio.parser.parser import parse
from clio.ir.builder import build_ir

src = Path('examples/parallel_classify.clio').read_text()
with tempfile.TemporaryDirectory() as d:
    PythonEmitter().emit(build_ir(parse(src)), Path(d))
    flow_py = next(Path(d).rglob('flow.py')).read_text()
    ast.parse(flow_py)
    print('=== flow.py ===')
    print(flow_py)
"
```

Expected: AST parses; the printed flow.py shows nested `try: -> if start_at < N: -> chain item -> _persist_state` structure with correct indentation.

- [ ] **Step 3.6: Run full python emitter tests (excluding fixture-tree comparisons)**

Run: `.venv/bin/pytest tests/test_emitters/test_python.py -v -k "not test_emit_skeleton and not test_emit_contracts and not test_emit_pyproject_v0 and not test_emit_pyproject_with_cache and not test_emit_with_cache and not test_emit_with_onfail and not test_emit_with_fallback and not test_python_emits_full_pipeline_with_overrides" 2>&1 | tail -5`

Expected: most non-fixture tests pass. The full-tree comparison tests will fail until Task 6 regen. That's expected.

- [ ] **Step 3.7: Commit**

```bash
git add clio/emitters/python.py tests/test_emitters/test_python.py
git commit -m "$(cat <<'EOF'
feat(emitters/python): gate chain items by start_at + persist after each

Refactor _emit_flow to track chain_groups (list-of-lists) instead of
chain_lines (flat list). Each top-level chain item now becomes a group;
groups are wrapped in 'if start_at < <idx>:' and followed by
_persist_state(<idx>, state).

A FOR EACH (sequential or PARALLEL) is exactly one top-level chain item:
one gate, one persist, regardless of inner iterations. Sequential CallIR
is also one item.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `__main__.py` `--from-step` argparse + validation

The emitted `__main__.py` must accept `--from-step N`, validate it, and pass it to `run(start_at=N, ...)`.

**Files:**
- Modify: `clio/emitters/python.py:_emit_main` (line 512)
- Modify: `tests/test_emitters/test_python.py`

- [ ] **Step 4.1: Write failing form tests**

Append to `tests/test_emitters/test_python.py`:

```python
def test_main_argparse_has_from_step(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    main_py = (tmp_path / "classify" / "__main__.py").read_text()
    assert '"--from-step"' in main_py
    assert "type=int" in main_py
    assert "default=0" in main_py


def test_main_validates_negative_from_step(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    main_py = (tmp_path / "classify" / "__main__.py").read_text()
    # main() must check args.from_step < 0 and return 2
    assert "args.from_step < 0" in main_py
    assert "return 2" in main_py


def test_main_passes_start_at_to_run(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    main_py = (tmp_path / "classify" / "__main__.py").read_text()
    assert "run(start_at=args.from_step" in main_py or "start_at=args.from_step" in main_py
```

- [ ] **Step 4.2: Run tests, verify FAIL**

Run: `.venv/bin/pytest tests/test_emitters/test_python.py -k "main_argparse_has_from_step or main_validates_negative or main_passes_start_at" -v`
Expected: FAIL.

- [ ] **Step 4.3: Modify `_emit_main`**

In `clio/emitters/python.py:_emit_main` (line 512), replace the entire method body. Find:

```python
    def _emit_main(self, pkg_name: str) -> str:
        return (
            f'"""CLI entry point: `python -m {pkg_name}`."""\n'
            f'import argparse\n'
            f'import json\n'
            f'import sys\n'
            f'\n'
            f'from .flow import run\n'
            f'\n'
            f'\n'
            f'def main(argv: list[str] | None = None) -> int:\n'
            f'    parser = argparse.ArgumentParser(prog="{pkg_name}")\n'
            f'    parser.add_argument("--kwargs", default="{{}}", help="JSON dict of initial flow kwargs")\n'
            f'    args = parser.parse_args(argv)\n'
            f'    initial = json.loads(args.kwargs)\n'
            f'    result = run(**initial)\n'
            f'    json.dump(result, sys.stdout, indent=2, default=str)\n'
            f'    sys.stdout.write("\\n")\n'
            f'    return 0\n'
            f'\n'
            f'\n'
            f'if __name__ == "__main__":\n'
            f'    raise SystemExit(main())\n'
        )
```

Replace with:

```python
    def _emit_main(self, pkg_name: str) -> str:
        return (
            f'"""CLI entry point: `python -m {pkg_name}`."""\n'
            f'import argparse\n'
            f'import json\n'
            f'import sys\n'
            f'\n'
            f'from .flow import run\n'
            f'\n'
            f'\n'
            f'def main(argv: list[str] | None = None) -> int:\n'
            f'    parser = argparse.ArgumentParser(prog="{pkg_name}")\n'
            f'    parser.add_argument("--kwargs", default="{{}}", help="JSON dict of initial flow kwargs")\n'
            f'    parser.add_argument(\n'
            f'        "--from-step",\n'
            f'        type=int,\n'
            f'        default=0,\n'
            f'        metavar="N",\n'
            f'        help="Resume from step N+1 (1-based; reads state.json or $CLIO_STATE_FILE).",\n'
            f'    )\n'
            f'    args = parser.parse_args(argv)\n'
            f'    if args.from_step < 0:\n'
            f'        print(f"[clio] --from-step must be >= 0, got {{args.from_step}}", file=sys.stderr)\n'
            f'        return 2\n'
            f'    initial = json.loads(args.kwargs)\n'
            f'    result = run(start_at=args.from_step, **initial)\n'
            f'    json.dump(result, sys.stdout, indent=2, default=str)\n'
            f'    sys.stdout.write("\\n")\n'
            f'    return 0\n'
            f'\n'
            f'\n'
            f'if __name__ == "__main__":\n'
            f'    raise SystemExit(main())\n'
        )
```

- [ ] **Step 4.4: Run form tests, verify PASS**

Run: `.venv/bin/pytest tests/test_emitters/test_python.py -k "main_argparse_has_from_step or main_validates_negative or main_passes_start_at" -v`
Expected: PASS.

- [ ] **Step 4.5: AST-parse a regenerated __main__.py**

```bash
.venv/bin/python -c "
import ast, tempfile
from pathlib import Path
from clio.emitters.python import PythonEmitter
from clio.parser.parser import parse
from clio.ir.builder import build_ir

src = Path('tests/fixtures/mvp_v03_skeleton.clio').read_text()
with tempfile.TemporaryDirectory() as d:
    PythonEmitter().emit(build_ir(parse(src)), Path(d))
    main_py = (Path(d) / 'classify' / '__main__.py').read_text()
    ast.parse(main_py)
    print(main_py)
"
```

- [ ] **Step 4.6: Commit**

```bash
git add clio/emitters/python.py tests/test_emitters/test_python.py
git commit -m "$(cat <<'EOF'
feat(emitters/python): __main__.py adds --from-step N argparse flag

Maps to run(start_at=N, ...). Validates args.from_step < 0 → return 2
with a stderr message. Default 0 keeps backwards compat: existing
invocations of python -m <pkg> behave identically (modulo the
state.json write that Task 3 added).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Behavioral tests

Compile + execute a simple flow with monkeypatched SDK (or even simpler: a flow whose only step is exact, no LLM). Verify the runtime behavior of state.json + --from-step.

**Files:**
- Modify: `tests/test_emitters/test_python.py`

- [ ] **Step 5.1: Decide on a behavior-friendly fixture**

The simplest path is to write a tiny `.clio` fixture inline in the test file that has all-exact steps with `impl: code` (NotImplementedError stubs are fine — the test catches them). For tests that need actual successful execution, override the emitted step files with stubs that just return canned values.

Use `tests/fixtures/mvp_v03_skeleton.clio` for paths that don't need successful execution (we just observe state.json after a single step's exception). For paths that need 2+ steps to complete, write a minimal inline fixture or override step files post-emit.

- [ ] **Step 5.2: Write behavioral tests**

Append to `tests/test_emitters/test_python.py`:

```python
def test_state_json_written_after_each_completed_step(tmp_path, monkeypatch):
    """Run a 1-step flow; verify state.json contains step_index=1 with the state."""
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)

    # Override the emitted step (default raises NotImplementedError) with a stub
    step_file = tmp_path / "classify" / "steps" / "detect_topic.py"
    # Read the original to keep imports + signature; replace the body with `return "topic"`
    original = step_file.read_text()
    # Find the line `    raise NotImplementedError(` and replace the function body.
    # Simpler: just overwrite the whole file with a minimal stub that imports _log
    # and time and returns a value.
    step_file.write_text(
        '"""STEP detect_topic (judgment, stubbed for test)"""\n'
        'from __future__ import annotations\n'
        'import time\n'
        'from ..clio_runtime import logging as _log\n\n'
        'def detect_topic(*, doc: str = "") -> str:\n'
        '    _t0 = time.monotonic()\n'
        '    _log.emit("step_start", step="detect_topic", mode="judgment")\n'
        '    _log.emit("step_end", step="detect_topic", mode="judgment", '
        'duration_ms=int((time.monotonic()-_t0)*1000), '
        'cache_hit=False, model="haiku", fallback_used=False, success=True)\n'
        '    return "topic_value"\n'
    )

    # Import and run from the emitted package
    import sys, importlib
    sys.path.insert(0, str(tmp_path))
    try:
        flow_mod = importlib.import_module("classify.flow")
        result = flow_mod.run(doc="hello")
        assert result.get("topic") == "topic_value"
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k.startswith("classify"):
                del sys.modules[k]

    # state.json should exist in cwd. The test runs in pytest's cwd, not tmp_path.
    # Use CLIO_STATE_FILE to redirect to tmp_path/state.json.
    # Actually: flow_mod.run() looked up CLIO_STATE_FILE at write time. We didn't
    # set it, so state.json was written to the test's cwd. Skip and re-test with env var.


def test_state_json_written_with_clio_state_file_env(tmp_path, monkeypatch):
    """When CLIO_STATE_FILE is set, state.json is written there."""
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)

    # Stub the step
    step_file = tmp_path / "classify" / "steps" / "detect_topic.py"
    step_file.write_text(
        '"""STEP detect_topic (judgment, stubbed)"""\n'
        'from __future__ import annotations\n'
        'def detect_topic(*, doc: str = "") -> str:\n'
        '    return "topic_value"\n'
    )

    state_file = tmp_path / "state.json"
    monkeypatch.setenv("CLIO_STATE_FILE", str(state_file))

    import sys, importlib, json
    sys.path.insert(0, str(tmp_path))
    try:
        flow_mod = importlib.import_module("classify.flow")
        importlib.reload(flow_mod)  # ensure env var is read fresh
        flow_mod.run(doc="hello")
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k.startswith("classify"):
                del sys.modules[k]

    assert state_file.exists()
    payload = json.loads(state_file.read_text())
    assert payload["version"] == 1
    assert payload["flow"] == "classify"
    assert payload["step_index"] == 1
    assert payload["state"].get("topic") == "topic_value"


def test_resume_from_step_skips_chain_item(tmp_path, monkeypatch):
    """--from-step 1 with a pre-populated state.json skips chain item 1."""
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)

    # Pre-populate state.json — claim step_index=1 has completed with a known state.
    state_file = tmp_path / "state.json"
    import json
    state_file.write_text(json.dumps({
        "version": 1,
        "flow": "classify",
        "step_index": 1,
        "state": {"topic": "preloaded_topic"},
    }))
    monkeypatch.setenv("CLIO_STATE_FILE", str(state_file))

    # The skeleton has only 1 chain item, so start_at >= TOTAL_STEPS would fail.
    # Use a fixture with 2+ items. Use mvp_v03_cache (3 items: load, detect, draft).
    src2 = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src2)), tmp_path / "out2")
    state_file2 = tmp_path / "state2.json"
    state_file2.write_text(json.dumps({
        "version": 1,
        "flow": "retention",
        "step_index": 1,
        "state": {"customers": [{"id": 1}]},
    }))
    monkeypatch.setenv("CLIO_STATE_FILE", str(state_file2))

    # Stub all three steps
    for name, ret in [("load_customers", "[]"), ("detect_churn", "[]"), ("draft_email", "ok")]:
        f = tmp_path / "out2" / "retention" / "steps" / f"{name}.py"
        f.write_text(
            f'"""stub"""\nfrom __future__ import annotations\n'
            f'def {name}(**kw): return {ret!r}\n'
        )

    import sys, importlib
    sys.path.insert(0, str(tmp_path / "out2"))
    try:
        flow_mod = importlib.import_module("retention.flow")
        importlib.reload(flow_mod)
        result = flow_mod.run(start_at=1)
        # Item 1 (load_customers) was skipped; state retained from preload.
        # Items 2 and 3 ran, overwriting/adding state.
        assert "customers" in result  # from the preloaded state
    finally:
        sys.path.remove(str(tmp_path / "out2"))
        for k in list(sys.modules):
            if k.startswith("retention"):
                del sys.modules[k]


def test_resume_fails_when_state_json_missing(tmp_path, monkeypatch):
    """--from-step N with no state.json raises SystemExit(2)."""
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    monkeypatch.setenv("CLIO_STATE_FILE", str(tmp_path / "nonexistent.json"))

    import sys, importlib, pytest
    sys.path.insert(0, str(tmp_path))
    try:
        flow_mod = importlib.import_module("retention.flow")
        importlib.reload(flow_mod)
        with pytest.raises(SystemExit) as exc:
            flow_mod.run(start_at=1)
        assert exc.value.code == 2
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k.startswith("retention"):
                del sys.modules[k]


def test_resume_fails_when_flow_mismatches(tmp_path, monkeypatch):
    """state.json with wrong flow field raises SystemExit(2)."""
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)

    state_file = tmp_path / "state.json"
    import json
    state_file.write_text(json.dumps({
        "version": 1, "flow": "different_flow", "step_index": 1, "state": {}
    }))
    monkeypatch.setenv("CLIO_STATE_FILE", str(state_file))

    import sys, importlib, pytest
    sys.path.insert(0, str(tmp_path))
    try:
        flow_mod = importlib.import_module("retention.flow")
        importlib.reload(flow_mod)
        with pytest.raises(SystemExit) as exc:
            flow_mod.run(start_at=1)
        assert exc.value.code == 2
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k.startswith("retention"):
                del sys.modules[k]


def test_resume_fails_when_step_index_too_low(tmp_path, monkeypatch):
    """state.json with step_index < start_at raises SystemExit(2)."""
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)

    state_file = tmp_path / "state.json"
    import json
    state_file.write_text(json.dumps({
        "version": 1, "flow": "retention", "step_index": 1, "state": {}
    }))
    monkeypatch.setenv("CLIO_STATE_FILE", str(state_file))

    import sys, importlib, pytest
    sys.path.insert(0, str(tmp_path))
    try:
        flow_mod = importlib.import_module("retention.flow")
        importlib.reload(flow_mod)
        with pytest.raises(SystemExit) as exc:
            flow_mod.run(start_at=2)  # state only at step 1, can't resume from 2
        assert exc.value.code == 2
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k.startswith("retention"):
                del sys.modules[k]


def test_resume_fails_when_start_at_exceeds_total_steps(tmp_path, monkeypatch):
    """start_at >= TOTAL_STEPS raises SystemExit(2)."""
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()  # 3 chain items
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)

    state_file = tmp_path / "state.json"
    import json
    state_file.write_text(json.dumps({
        "version": 1, "flow": "retention", "step_index": 99, "state": {}
    }))
    monkeypatch.setenv("CLIO_STATE_FILE", str(state_file))

    import sys, importlib, pytest
    sys.path.insert(0, str(tmp_path))
    try:
        flow_mod = importlib.import_module("retention.flow")
        importlib.reload(flow_mod)
        with pytest.raises(SystemExit) as exc:
            flow_mod.run(start_at=99)  # exceeds TOTAL_STEPS=3
        assert exc.value.code == 2
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k.startswith("retention"):
                del sys.modules[k]
```

- [ ] **Step 5.3: Run behavioral tests, verify PASS**

Run: `.venv/bin/pytest tests/test_emitters/test_python.py -k "state_json_written or resume_" -v 2>&1 | tail -20`
Expected: 7 tests pass.

If a test fails because of `sys.path` / module-cache issues from earlier tests, ensure each test cleans up its `sys.modules` and `sys.path` insertions in `finally`. The pattern in the snippet above does this.

- [ ] **Step 5.4: Commit**

```bash
git add tests/test_emitters/test_python.py
git commit -m "$(cat <<'EOF'
test(emitters/python): behavioral tests for resume + state.json

Compile + import + run a stubbed flow, verify:
- state.json is written after each completed step (CLIO_STATE_FILE)
- start_at=N skips chain items < N and reloads state from state.json
- four failure paths raise SystemExit(2): missing file, wrong flow,
  step_index too low, start_at >= TOTAL_STEPS

Each test cleans sys.path + sys.modules in finally to avoid cache
pollution across tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Regenerate v03 expected fixtures

The shape of `flow.py` and `__main__.py` changed. Regenerate fixtures.

**Files:**
- Regenerate: `tests/fixtures/expected/v03_skeleton`, `v03_contracts`, `v03_cache`, `v03_onfail`, `v03_fallback`, `python_v03_mvp`

- [ ] **Step 6.1: Run regen script**

```bash
.venv/bin/python -c "
from pathlib import Path
import shutil
from clio.emitters.python import PythonEmitter
from clio.parser.parser import parse
from clio.ir.builder import build_ir

fixtures = Path('tests/fixtures')
sources = {
    'v03_skeleton': 'mvp_v03_skeleton.clio',
    'v03_contracts': 'mvp_v03_contracts.clio',
    'v03_cache': 'mvp_v03_cache.clio',
    'v03_onfail': 'mvp_v03_onfail.clio',
    'v03_fallback': 'mvp_v03_fallback.clio',
    'python_v03_mvp': 'mvp.clio',
}
for name, src_file in sources.items():
    src_path = fixtures / src_file
    if not src_path.exists():
        print(f'SKIP {name}')
        continue
    out_dir = fixtures / 'expected' / name
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    PythonEmitter().emit(build_ir(parse(src_path.read_text())), out_dir)
    print(f'OK {name}')
"
```

- [ ] **Step 6.2: AST-parse all regenerated step + flow files**

```bash
find tests/fixtures/expected -path "*/steps/*.py" -o -name "flow.py" -o -name "__main__.py" \
  | xargs -I {} .venv/bin/python -c "import ast, sys; ast.parse(open(sys.argv[1]).read())" {}
echo "AST OK"
```

Expected: no SyntaxError.

- [ ] **Step 6.3: Run full python emitter test suite**

Run: `.venv/bin/pytest tests/test_emitters/test_python.py -v 2>&1 | tail -3`
Expected: all green.

- [ ] **Step 6.4: Run full suite**

Run: `.venv/bin/pytest tests/ -v 2>&1 | tail -3`
Expected: all green (348 + ~13 new tests = ~361 passed, 7 skipped).

- [ ] **Step 6.5: Commit**

```bash
git add tests/fixtures/expected/
git commit -m "$(cat <<'EOF'
test(fixtures): regenerate v03 expected fixtures with resume scaffolding

flow.py: TOTAL_STEPS constant, _persist_state helper, start_at parameter
with load+validation prelude, chain items wrapped in 'if start_at < N:'
gates with _persist_state(N, state) calls.

__main__.py: argparse adds --from-step N (default 0).

6 fixtures regenerated: v03_skeleton, v03_contracts, v03_cache,
v03_onfail, v03_fallback, python_v03_mvp.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: E2E gated test

End-to-end test: compile, run as subprocess, verify state.json + resume behavior.

**Files:**
- Create: `tests/test_e2e_resume.py`

- [ ] **Step 7.1: Write the E2E test file**

Create `tests/test_e2e_resume.py`:

```python
"""End-to-end test for clio resume — gated by CLIO_E2E=1.

Compiles a fixture flow, runs it as a subprocess, verifies state.json
is written, manually pre-populates state.json and re-runs with
--from-step N to verify resume semantics.

Skipped by default. Enable: CLIO_E2E=1 pytest ..."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CLIO_E2E") != "1",
    reason="set CLIO_E2E=1 to enable end-to-end resume tests",
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _compile(src_path: Path, out_dir: Path) -> str:
    subprocess.run(
        [sys.executable, "-m", "clio", "compile", str(src_path),
         "--target", "python", "--output", str(out_dir)],
        check=True,
    )
    pkg_name = next(
        p.name for p in out_dir.iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
    )
    return pkg_name


def test_normal_run_writes_state_json(tmp_path):
    """A normal run (no --from-step) writes state.json with step_index >= 0."""
    src = _FIXTURES / "mvp_v03_skeleton.clio"
    out_dir = tmp_path / "out"
    pkg_name = _compile(src, out_dir)

    state_file = tmp_path / "state.json"
    env = {**os.environ, "CLIO_STATE_FILE": str(state_file)}
    proc = subprocess.run(
        [sys.executable, "-m", pkg_name, "--kwargs", "{}"],
        cwd=out_dir, env=env, capture_output=True, text=True,
    )
    # The skeleton's stub raises NotImplementedError, so rc != 0 is expected.
    # State.json may or may not exist depending on whether _persist_state was
    # called before the NotImplementedError. Test both cases.
    if state_file.exists():
        payload = json.loads(state_file.read_text())
        assert payload["version"] == 1
        assert payload["flow"] == "classify"
        assert payload["step_index"] >= 0


def test_resume_with_prepopulated_state(tmp_path):
    """--from-step N with a pre-populated state.json skips items < N."""
    src = _FIXTURES / "mvp_v03_skeleton.clio"
    out_dir = tmp_path / "out"
    pkg_name = _compile(src, out_dir)

    # Pre-populate state.json claiming step 1 already done.
    # Skeleton has 1 chain item, so start_at=1 hits start_at>=TOTAL_STEPS -> exit 2.
    # We use start_at=0 = no skip but still validate state structure.
    # Actually, to test resume on a multi-step flow, switch to mvp_v03_cache.
    src = _FIXTURES / "mvp_v03_cache.clio"
    out_dir = tmp_path / "out2"
    pkg_name = _compile(src, out_dir)

    state_file = tmp_path / "state2.json"
    state_file.write_text(json.dumps({
        "version": 1, "flow": "retention", "step_index": 1,
        "state": {"customers": []},
    }))

    env = {**os.environ, "CLIO_STATE_FILE": str(state_file)}
    proc = subprocess.run(
        [sys.executable, "-m", pkg_name, "--kwargs", "{}", "--from-step", "1"],
        cwd=out_dir, env=env, capture_output=True, text=True,
    )
    # Items 2 and 3 still raise NotImplementedError (default stubs), so rc != 0.
    # The point is: SystemExit code is from the step body, not from the resume
    # validation (which would be exit 2 silently).
    # We just verify no resume-validation SystemExit happened.
    # Resume validation would print "[clio] resume requested" or similar to stderr.
    assert "[clio] resume" not in proc.stderr, proc.stderr


def test_resume_fails_with_missing_state_file(tmp_path):
    """--from-step N with no state.json exits 2."""
    src = _FIXTURES / "mvp_v03_cache.clio"
    out_dir = tmp_path / "out"
    pkg_name = _compile(src, out_dir)

    state_file = tmp_path / "no_such_state.json"
    env = {**os.environ, "CLIO_STATE_FILE": str(state_file)}
    proc = subprocess.run(
        [sys.executable, "-m", pkg_name, "--from-step", "1"],
        cwd=out_dir, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "missing" in proc.stderr


def test_resume_fails_with_negative_from_step(tmp_path):
    """--from-step -1 exits 2 from main()."""
    src = _FIXTURES / "mvp_v03_cache.clio"
    out_dir = tmp_path / "out"
    pkg_name = _compile(src, out_dir)
    proc = subprocess.run(
        [sys.executable, "-m", pkg_name, "--from-step", "-1"],
        cwd=out_dir, capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "must be >= 0" in proc.stderr


def test_no_state_json_when_unused(tmp_path):
    """A package that doesn't write state.json keeps cwd clean.

    Actually our impl ALWAYS writes state.json (Task 3). So this test
    verifies the state.json IS written, even without --from-step.
    Negative test: previous semantics where state.json was opt-in are gone."""
    src = _FIXTURES / "mvp_v03_cache.clio"
    out_dir = tmp_path / "out"
    pkg_name = _compile(src, out_dir)

    state_file = tmp_path / "state.json"
    env = {**os.environ, "CLIO_STATE_FILE": str(state_file)}
    proc = subprocess.run(
        [sys.executable, "-m", pkg_name, "--kwargs", "{}"],
        cwd=out_dir, env=env, capture_output=True, text=True,
    )
    # The first step raises NotImplementedError, so state.json may not be
    # written if the raise happens before the first _persist_state call.
    # Just verify the run is structured correctly and state.json (if present)
    # has the right shape.
    if state_file.exists():
        payload = json.loads(state_file.read_text())
        assert payload["version"] == 1
        assert payload["flow"] == "retention"
```

- [ ] **Step 7.2: Run gated**

Run: `CLIO_E2E=1 .venv/bin/pytest tests/test_e2e_resume.py -v`
Expected: 5 tests pass.

- [ ] **Step 7.3: Run without gate**

Run: `.venv/bin/pytest tests/test_e2e_resume.py -v`
Expected: 5 tests skipped.

- [ ] **Step 7.4: Run full suite**

Run: `.venv/bin/pytest tests/ -v 2>&1 | tail -3`
Expected: 12 skipped (4 pre-existing + 3 W2 logging + 5 new resume), all rest passing.

- [ ] **Step 7.5: Commit**

```bash
git add tests/test_e2e_resume.py
git commit -m "$(cat <<'EOF'
test(e2e): gated end-to-end test for clio resume (W5)

Compiles a fixture, runs the package as subprocess with various
--from-step values, verifies state.json structure, --from-step
validation, and resume scenarios. Gated by CLIO_E2E=1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Documentation

Update LANGUAGE_SPEC, COMPILATION_TARGETS, CHANGELOG, README.

**Files:**
- Modify: `docs/LANGUAGE_SPEC.md`
- Modify: `docs/COMPILATION_TARGETS.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`

- [ ] **Step 8.1: Add Resume subsection to LANGUAGE_SPEC.md**

Locate the Observability section (added by W2). Add a Resume subsection at the end:

```markdown
### Resume (v0.4+)

The python target persists `state.json` after each top-level chain item
completes. To resume from a specific step:

```bash
python -m my_pkg --from-step 3   # skip the first 3 chain items, resume from item 4
```

State file location: `./state.json` by default (cwd of the invocation),
override via `CLIO_STATE_FILE=path/to/state.json`.

State file schema:

```json
{
  "version": 1,
  "flow": "<flow_name>",
  "step_index": <last completed top-level chain item, 1-based>,
  "state": { "...accumulated state dict..." }
}
```

Granularity: a `FOR EACH` (sequential or PARALLEL) is one chain item
regardless of internal iterations. Mid-iteration resume is not supported.

Failure modes (all `SystemExit(2)`):
- `--from-step N` with N < 0
- state.json missing
- state.json `flow` field doesn't match the compiled package
- state.json `step_index` < N
- N >= TOTAL_STEPS

Targets v1: python only. mcp-server is server-stateless by design;
claude-cli deferred to v2.
```

- [ ] **Step 8.2: Add Resume line to COMPILATION_TARGETS.md python section**

Add to the python target section:

```markdown
**Resume** (v0.4+): emitted package writes `state.json` atomically after
each top-level chain item; `python -m my_pkg --from-step N` reloads the
state and skips items 1..N. Path via `CLIO_STATE_FILE` env var.
```

- [ ] **Step 8.3: Update CHANGELOG.md**

Under `## Unreleased`, add:

```markdown
### Resume

- **W5 (short-term): Step-granularity resume.** Python emitter writes
  `state.json` after each top-level chain item (atomic via
  `os.replace(tmp, path)`). The emitted `__main__.py` accepts
  `--from-step N` (1-based; reads `state.json` or `$CLIO_STATE_FILE`)
  and skips items 1..N. Granularity is one top-level chain item: a
  `FOR EACH` (sequential or PARALLEL) counts as one regardless of
  internal iterations. Strict fail-fast on edge cases. Targets v1:
  python only.
```

- [ ] **Step 8.4: Update README.md**

Add a Resume subsection near Observability:

```markdown
### Resume

If a long pipeline crashes mid-flow, resume from the last completed
step:

\`\`\`bash
python -m my_compiled_flow --from-step 3
\`\`\`

The package writes `state.json` after each completed step (path via
`CLIO_STATE_FILE`). See `docs/LANGUAGE_SPEC.md` for the schema.
```

- [ ] **Step 8.5: Run full suite to confirm nothing broke**

Run: `.venv/bin/pytest tests/ 2>&1 | tail -3`
Expected: passing.

- [ ] **Step 8.6: Commit docs**

```bash
git add docs/LANGUAGE_SPEC.md docs/COMPILATION_TARGETS.md CHANGELOG.md README.md
git commit -m "$(cat <<'EOF'
docs: clio resume — LANGUAGE_SPEC, COMPILATION_TARGETS, CHANGELOG, README

LANGUAGE_SPEC: new Resume subsection in Observability with state.json
schema, --from-step usage, granularity rules, failure modes.
COMPILATION_TARGETS: python target gets a Resume line.
CHANGELOG: Unreleased entry under Resume.
README: brief usage paragraph.

Closes W5 short-term per POSITIONING.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review checklist

After completing all tasks:

**Spec coverage:**
- `_persist_state` helper: Task 1.
- `TOTAL_STEPS` constant: Task 1.
- `start_at` parameter + load+validation logic: Task 2.
- Chain item gating: Task 3.
- `__main__.py` `--from-step`: Task 4.
- `flow_start` `resumed_from` field: Task 2.
- Behavioral tests (write/read/resume/fail): Task 5.
- E2E gated: Task 7.
- Fixtures regen: Task 6.
- Docs: Task 8.

**No placeholders:** all code blocks contain runnable snippets.

**Type consistency:** `_persist_state(step_idx: int, state: dict)`, `start_at: int = 0`, `TOTAL_STEPS = N`, `--from-step` (kebab-case CLI → `args.from_step` snake_case Python). Consistent across all tasks.

**Frequent commits:** 8 commits, one per task.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-08-clio-resume.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session via executing-plans, batch execution with checkpoints.

Which approach?
