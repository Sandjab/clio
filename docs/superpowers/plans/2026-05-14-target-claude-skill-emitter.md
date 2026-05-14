# CLIO — `target: claude-skill` Emitter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a new compilation target `claude-skill` that takes a CLIO IR and emits a Claude Code skill directory (`SKILL.md` + `scripts/` + `schemas/` + `prompts/` + `process_flow.dot`) executable by the LLM host itself, with parity for v0.13 features (RESCUE, `step.error.*`, RESUME, CACHE, RETRY, RESOURCES).

**Architecture:** Add one new emitter module `clio/emitters/claude_skill.py` and one helper module `clio/emitters/_claude_skill_helpers.py`. **Extract** type-utility helpers (`_to_class_name`, `_to_field_name`, `_type_to_python`, `_render_type_short`, `_json_type_to_python`, `_shape_from_schema`, `_field_from_schema`, `_uses_contract_refs`) from `_python_helpers.py` into a new `clio/emitters/_shared_utils.py` consumed by `python`, `mcp-server` and `claude-skill` alike (in response to Gemini PR #11 #1). Convention-specific renderers stay emitter-local. The emitter walks the IR (same `FlowGraph` consumed by every other emitter) and writes a deterministic, byte-stable output tree. No edits to `parser/` or `ir/`.

**Tech Stack:** Python 3.12, frozen dataclasses, Pydantic v2 (already used for contracts), pytest. No new dependencies. See validated spec: `docs/superpowers/specs/2026-05-14-target-claude-skill-design.md`.

---

## File map

**Created:**

- `clio/emitters/_shared_utils.py` — type-utility helpers extracted from `_python_helpers.py` (consumed by `python`, `mcp-server`, `claude-skill`)
- `clio/emitters/claude_skill.py` — `ClaudeSkillEmitter(BaseEmitter)` (orchestration: writes the output tree)
- `clio/emitters/_claude_skill_helpers.py` — pure rendering helpers (frontmatter, SKILL.md sections, JSON Schema dump, exact-script body, DOT, bundled `_validate.py` and `_cache_key.py` script bodies)
- `tests/test_emitters/test_claude_skill.py` — emission tests (Layer 1 granular + Layer 2 runtime + golden snapshots)
- `tests/test_emitters/test_shared_utils.py` — unit tests for the extracted module
- `tests/fixtures/expected_skill/` — directory holding golden snapshots `<fixture_name>/` (separate from `expected/` used by `claude-cli`)
- `examples/skill_minimal.clio` — minimal example shipped with the manual
- `docs/manual/03-cookbook.md` entry — new recipe "Compile a `.clio` into a Claude Code skill"
- `docs/manual/06-troubleshooting.md` entries — new compile-time errors/warnings introduced by this target

**Modified:**

- `clio/emitters/_python_helpers.py` — extracted helpers replaced by `from clio.emitters._shared_utils import …`
- `clio/emitters/_mcp_helpers.py` — extracted helpers replaced by `from clio.emitters._shared_utils import …`
- `clio/cli.py` — register `"claude-skill"` target in `_cmd_compile` dispatch (one new `elif` branch)
- `docs/COMPILATION_TARGETS.md` — move `claude-skill` from "Future/Candidate" to "Implemented" + dedicated section
- `CHANGELOG.md` — new section for the release that ships this target

**Not touched:**

- `clio/parser/`, `clio/ir/`, `clio/keywords.py` — no language changes
- `clio/emitters/claude_cli.py`, `python.py`, `mcp_server.py`, `langgraph.py` — no edits to emitter bodies (only their `_*_helpers.py` are touched, transparently)

---

## Test conventions used by this plan

Two patterns coexist in this codebase:

1. **Granular tests** (this plan, tasks 1–12 + 14): direct `assert` on file existence, parsed YAML/JSON content, or regex matches on `SKILL.md`. Used while building the emitter feature by feature, before the full output is stable.
2. **Golden snapshots** (this plan, task 15 only): `_read_tree(tmp_path) == _read_tree(expected_skill/<fixture>)`. Used at the end for end-to-end regression on a handful of representative fixtures.

The golden-snapshot pattern matches the convention used by `tests/test_emitters/test_claude_cli.py`. To regenerate goldens after intentional changes:

```bash
python -m clio compile tests/fixtures/<name>.clio --target claude-skill --output tests/fixtures/expected_skill/<name>
```

This is documented in the test file's module docstring.

---

## Task 1: Scaffold the emitter and register the CLI target

**Files:**
- Create: `clio/emitters/claude_skill.py`
- Create: `clio/emitters/_claude_skill_helpers.py`
- Modify: `clio/cli.py:71-85` (compile dispatch)
- Test: `tests/test_emitters/test_claude_skill.py`

- [ ] **Step 1: Write the failing smoke test**

Create `tests/test_emitters/test_claude_skill.py`:

```python
"""Tests for the claude-skill emitter.

Granular tests (existence + parsed content) for tasks 1-12.
Golden snapshots (full tree equality) for task 14 only.
To regenerate goldens after intentional changes:

    python -m clio compile tests/fixtures/<name>.clio \\
        --target claude-skill --output tests/fixtures/expected_skill/<name>
"""

from pathlib import Path

import yaml

from clio.emitters.claude_skill import ClaudeSkillEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_smoke_emit_phase1_creates_skill_md(tmp_path):
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    skill_md = tmp_path / "SKILL.md"
    assert skill_md.exists()
    body = skill_md.read_text()
    assert body.startswith("---\n")
    front_end = body.index("\n---\n", 4)
    front = yaml.safe_load(body[4:front_end])
    assert front["name"]
    assert front["description"]
```

- [ ] **Step 2: Run test, verify red**

```bash
pytest tests/test_emitters/test_claude_skill.py::test_smoke_emit_phase1_creates_skill_md -v
```

Expected: `ModuleNotFoundError: No module named 'clio.emitters.claude_skill'`

- [ ] **Step 3: Create the empty helpers module**

`clio/emitters/_claude_skill_helpers.py`:

```python
"""Pure rendering helpers for the claude-skill emitter.

Functions in this module take IR nodes and produce strings or dicts.
No filesystem I/O. No imports from other emitter modules.
"""

from __future__ import annotations

from clio.ir.graph import FlowGraph


def render_frontmatter(graph: FlowGraph) -> str:
    """Render the YAML frontmatter block for SKILL.md (between '---' fences).

    Returns a string starting with '---\\n' and ending with '---\\n'.
    """
    name = graph.flow_name.replace("_", "-")
    description = (graph.flow_description or f"Execute flow {graph.flow_name}").strip()
    return f"---\nname: {name}\ndescription: {description}\nallowed-tools: Bash, Read, Write, TodoWrite\n---\n"
```

Note: `graph.flow_name` and `graph.flow_description` are the fields already exposed by `FlowGraph` (check `clio/ir/graph.py` — adapt names if they differ; pre-existing convention prevails).

- [ ] **Step 4: Create the emitter skeleton**

`clio/emitters/claude_skill.py`:

```python
"""target: claude-skill — emits a Claude Code skill directory.

See docs/superpowers/specs/2026-05-14-target-claude-skill-design.md
for the validated design.
"""

from __future__ import annotations

from pathlib import Path

from clio.emitters.base import BaseEmitter
from clio.emitters._claude_skill_helpers import render_frontmatter
from clio.ir.graph import FlowGraph


class ClaudeSkillEmitter(BaseEmitter):
    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        body = render_frontmatter(graph) + f"\n# {graph.flow_name}\n"
        (output_dir / "SKILL.md").write_text(body)
```

- [ ] **Step 5: Register the target in the CLI**

Modify `clio/cli.py` after line 80 (the existing `langgraph` branch). New branch right before the `else: return 2`:

```python
    elif target == "claude-skill":
        from clio.emitters.claude_skill import ClaudeSkillEmitter
        ClaudeSkillEmitter().emit(graph, out_path)
```

Use a lazy `from` import (as `mcp-server` and `langgraph` do) to keep CLI startup snappy.

- [ ] **Step 6: Run test, verify green**

```bash
pytest tests/test_emitters/test_claude_skill.py::test_smoke_emit_phase1_creates_skill_md -v
```

Expected: PASS.

- [ ] **Step 7: Run the full existing suite, confirm no regression**

```bash
pytest tests/ -q
```

Expected: same green count as before this task + 1 new green.

- [ ] **Step 8: Commit**

```bash
git add clio/emitters/claude_skill.py clio/emitters/_claude_skill_helpers.py clio/cli.py tests/test_emitters/test_claude_skill.py
git commit -m "feat(claude-skill): scaffold emitter + CLI target"
```

---

## Task 1b: Extract type-utility helpers into `clio/emitters/_shared_utils.py`

Done in response to Gemini PR #11 review comment #1 — the user (Sandjab) reversed the initial decision and asked for the refactor to be integrated in this sprint, not deferred.

**Strategy**: small, reversible move. Each function moves verbatim with no semantic change; `_python_helpers.py` and `_mcp_helpers.py` keep working through re-imports. The existing emitter test suites (`test_python.py`, `test_mcp_server.py`) are the safety net — every move must keep them fully green before moving on.

**Helpers to extract** (in order of dependency, leaves first):

1. `_to_class_name(name: str) -> str` — `_python_helpers.py:85-88`
2. `_to_field_name(name: str) -> str` — `_python_helpers.py:90-95`
3. `_model_id(short_name: str) -> str` — `_python_helpers.py:68-70`
4. `_uses_contract_refs(step: StepIR) -> bool` — `_python_helpers.py:97-117`
5. `_render_type_short(t: TypeExpr) -> str` — `_python_helpers.py:141-157`
6. `_type_to_python(t: TypeExpr, contracts) -> str` — `_python_helpers.py:119-139`
7. `_json_type_to_python(schema: dict) -> str` — `_python_helpers.py:201-223`
8. `_shape_from_schema(schema: dict) -> list[tuple[str, dict]]` — `_python_helpers.py:188-191`
9. `_field_from_schema(name: str, schema: dict) -> str` — `_python_helpers.py:193-199`

**Files:**
- Create: `clio/emitters/_shared_utils.py`
- Create: `tests/test_emitters/test_shared_utils.py`
- Modify: `clio/emitters/_python_helpers.py` (replace bodies with re-imports)
- Modify: `clio/emitters/_mcp_helpers.py` (replace usages with imports — verify it imported these from `_python_helpers` indirectly; if not, no change needed there)

- [ ] **Step 1: Inventory call sites — confirm all usages live in `_python_helpers.py` and `_mcp_helpers.py`**

```bash
for fn in _to_class_name _to_field_name _model_id _uses_contract_refs _render_type_short _type_to_python _json_type_to_python _shape_from_schema _field_from_schema; do
    echo "=== $fn ==="
    grep -rn "$fn" clio/ tests/ | grep -v __pycache__
done
```

Expected: each helper appears (a) once defined in `_python_helpers.py`, (b) possibly in `_mcp_helpers.py` (which may re-import or duplicate), (c) possibly in `python.py` / `mcp_server.py` via wildcard or direct import.

Write down the call-site map. If `_mcp_helpers.py` duplicates a helper rather than importing it from `_python_helpers.py`, the refactor must update **both** call paths.

- [ ] **Step 2: Capture the "before" test baseline**

```bash
pytest tests/ -q 2>&1 | tail -3
```

Expected: green, ~695 tests passing. Record the exact count — this becomes the regression bar.

- [ ] **Step 3: Create `_shared_utils.py` with the 9 helpers copied verbatim**

`clio/emitters/_shared_utils.py`:

```python
"""Type-utility helpers shared by emitter modules.

Originally lived in `_python_helpers.py`; extracted in v0.14 because
3 emitters now consume the same shape-rendering and naming logic
(python, mcp-server, claude-skill).

The CLAUDE.md rule "emitters never import from each other" continues
to hold: `_shared_utils.py` is a utility module, not an emitter. Both
emitter helper modules (`_python_helpers.py`, `_mcp_helpers.py`,
`_claude_skill_helpers.py`) import from here.
"""
from __future__ import annotations

# Lift type annotations and imports that the moved helpers need
# (TypeExpr, ContractIR, StepIR). Copy the exact import block from
# the top of _python_helpers.py.

# Then paste the 9 functions in dependency order (Step 1 list).
```

Copy the functions one by one from `_python_helpers.py` (the line numbers in the inventory above are the source-of-truth — adjust if the file has shifted). Preserve docstrings exactly. Do not change names, signatures, or bodies.

- [ ] **Step 4: Write a small unit test file**

`tests/test_emitters/test_shared_utils.py`:

```python
"""Smoke tests for clio.emitters._shared_utils.

These guard the public surface; behavioural coverage stays in
test_python.py and test_mcp_server.py via the existing emitter suites.
"""

from clio.emitters._shared_utils import (
    _field_from_schema,
    _json_type_to_python,
    _model_id,
    _render_type_short,
    _shape_from_schema,
    _to_class_name,
    _to_field_name,
    _type_to_python,
    _uses_contract_refs,
)


def test_to_class_name_basic():
    assert _to_class_name("customer_order") == "CustomerOrder"
    assert _to_class_name("foo") == "Foo"


def test_to_field_name_basic():
    # Adjust expectations after Step 3 if the original convention
    # differs — copy the existing examples already covered by
    # test_python.py to stay 1:1 with prior behaviour.
    assert _to_field_name("CustomerID") == "customer_id"  # adapt if different


def test_model_id_smoke():
    # _model_id maps a short alias to a full model id. Just verify it
    # returns a non-empty string for a known alias used elsewhere.
    out = _model_id("sonnet")
    assert isinstance(out, str) and len(out) > 0
```

These tests are deliberately small — the real safety net is the existing emitter suites that exercise these helpers indirectly.

- [ ] **Step 5: Run the new unit tests, verify green**

```bash
pytest tests/test_emitters/test_shared_utils.py -v
```

If any test fails, the function was copied incorrectly. Fix and retry.

- [ ] **Step 6: Rewrite `_python_helpers.py` to import from `_shared_utils`**

For each of the 9 functions, delete its body in `_python_helpers.py` and replace with a re-export:

```python
# At the top of the import block:
from clio.emitters._shared_utils import (
    _field_from_schema,
    _json_type_to_python,
    _model_id,
    _render_type_short,
    _shape_from_schema,
    _to_class_name,
    _to_field_name,
    _type_to_python,
    _uses_contract_refs,
)

# Then DELETE the original function definitions from _python_helpers.py
# (lines 68-70, 85-95, 97-117, 119-139, 141-157, 188-199, 201-223).
```

Keep them as re-exports (don't `__all__`-restrict): downstream code that does `from clio.emitters._python_helpers import _to_class_name` continues to work.

- [ ] **Step 7: Repeat for `_mcp_helpers.py` if it duplicated any helper**

Per the Step 1 inventory: if `_mcp_helpers.py` duplicated any of the 9 functions, replace those copies with the same re-export. If it imported them transitively from `_python_helpers.py`, no change required (still resolves through the re-exports).

- [ ] **Step 8: Run the full test suite — must equal the Step 2 baseline**

```bash
pytest tests/ -q 2>&1 | tail -3
```

Expected: exactly the same number of tests, all green, including the new `test_shared_utils.py` ones (+ ~3 new greens above baseline).

If any prior test fails: revert the failing helper's move and investigate. Do not advance.

- [ ] **Step 9: Run `git diff --stat` and verify scope discipline**

```bash
git diff --stat
```

Expected: only `clio/emitters/_python_helpers.py`, `clio/emitters/_mcp_helpers.py` (if changed in Step 7), `clio/emitters/_shared_utils.py` (new), `tests/test_emitters/test_shared_utils.py` (new). **No changes to `python.py`, `mcp_server.py`, `claude_cli.py`, `langgraph.py`** — if anything else shows up, you've gone too far.

- [ ] **Step 10: Commit**

```bash
git add clio/emitters/_shared_utils.py clio/emitters/_python_helpers.py clio/emitters/_mcp_helpers.py tests/test_emitters/test_shared_utils.py
git commit -m "refactor(emitters): extract type-utility helpers into _shared_utils.py

Addresses Gemini PR #11 review #1. The 9 type-utility helpers (naming,
type-to-python, schema shape, model id, contract-ref check) move verbatim
from _python_helpers.py into a new clio/emitters/_shared_utils.py. The
two emitter-helper modules re-import them from there so no consumer code
changes. This unblocks claude-skill emitter (and future targets) from
either duplicating the helpers or importing across emitters."
```

---

## Task 2: Frontmatter — description fallback + warning + allowed-tools logic

**Files:**
- Modify: `clio/emitters/_claude_skill_helpers.py`
- Modify: `clio/emitters/claude_skill.py` (emit warning to stderr)
- Test: `tests/test_emitters/test_claude_skill.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_emitters/test_claude_skill.py`:

```python
def test_frontmatter_uses_flow_description_when_present(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    front_end = body.index("\n---\n", 4)
    front = yaml.safe_load(body[4:front_end])
    # mvp_phase2.clio has a FLOW-level description; the emitter must use it verbatim.
    assert front["description"] == graph.flow_description.strip()


def test_frontmatter_warns_when_no_description(tmp_path, capsys):
    # Build a fixture-less in-memory graph with no description
    from clio.parser.parser import parse
    from clio.ir.builder import build_ir
    src = '''FLOW hello_world:
    @step01:
        STEP "say hello":
            MODE: exact
            CODE python: """
                return {"msg": "hi"}
            """
'''
    graph = build_ir(parse(src))
    assert not graph.flow_description  # precondition
    ClaudeSkillEmitter().emit(graph, tmp_path)
    captured = capsys.readouterr()
    assert "claude-skill warning" in captured.err
    assert "FLOW hello_world has no description" in captured.err
    body = (tmp_path / "SKILL.md").read_text()
    front_end = body.index("\n---\n", 4)
    front = yaml.safe_load(body[4:front_end])
    assert front["description"] == "Execute flow hello_world"


def test_frontmatter_allowed_tools_includes_bash_when_exact_step(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()  # has exact + judgment
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    front_end = body.index("\n---\n", 4)
    front = yaml.safe_load(body[4:front_end])
    tools = [t.strip() for t in front["allowed-tools"].split(",")]
    assert "Bash" in tools
    assert "Read" in tools
    assert "Write" in tools
    assert "TodoWrite" in tools
```

- [ ] **Step 2: Run, verify red**

```bash
pytest tests/test_emitters/test_claude_skill.py -v
```

Expected: the three new tests fail (one with assert error on description, one with no stderr match, one already green by chance — still mark it as a regression guard).

- [ ] **Step 3: Implement description fallback + warning**

Replace `render_frontmatter` in `_claude_skill_helpers.py` with:

```python
def render_frontmatter(graph: FlowGraph, *, warn: callable | None = None) -> str:
    """Render the YAML frontmatter block for SKILL.md.

    If the flow has no description, emit a warning via `warn` (a callable
    that takes a single string — typically `lambda m: print(m, file=sys.stderr)`).
    """
    name = graph.flow_name.replace("_", "-")
    description = (graph.flow_description or "").strip()
    if not description:
        description = f"Execute flow {graph.flow_name}"
        if warn is not None:
            warn(
                f"claude-skill warning: FLOW {graph.flow_name} has no description; "
                f"frontmatter description defaulted to '{description}'. "
                f"Auto-trigger of the emitted skill will be weak."
            )
    tools = _allowed_tools(graph)
    return (
        f"---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"allowed-tools: {', '.join(tools)}\n"
        f"---\n"
    )


def _allowed_tools(graph: FlowGraph) -> list[str]:
    """Static set for v1: every emitted skill uses the same tool surface.

    Read for state.json, Write for state mutations, Bash for exact scripts
    and validation, TodoWrite for the orchestration checklist.
    """
    return ["Bash", "Read", "Write", "TodoWrite"]
```

- [ ] **Step 4: Wire the warning from the emitter**

In `clio/emitters/claude_skill.py`, replace `emit` with:

```python
    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        import sys

        output_dir.mkdir(parents=True, exist_ok=True)
        warn = lambda m: print(m, file=sys.stderr)
        body = render_frontmatter(graph, warn=warn) + f"\n# {graph.flow_name}\n"
        (output_dir / "SKILL.md").write_text(body)
```

- [ ] **Step 5: Run, verify green**

```bash
pytest tests/test_emitters/test_claude_skill.py -v
```

Expected: all four tests in this file PASS.

- [ ] **Step 6: Commit**

```bash
git add clio/emitters/_claude_skill_helpers.py clio/emitters/claude_skill.py tests/test_emitters/test_claude_skill.py
git commit -m "feat(claude-skill): frontmatter with description fallback + allowed-tools"
```

---

## Task 3: Auxiliary files — `process_flow.dot`, `state.example.json`, `README.md`

**Files:**
- Modify: `clio/emitters/_claude_skill_helpers.py`
- Modify: `clio/emitters/claude_skill.py`
- Test: `tests/test_emitters/test_claude_skill.py`

- [ ] **Step 1: Write the failing tests**

```python
import json


def test_emits_process_flow_dot(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    dot = (tmp_path / "process_flow.dot").read_text()
    assert dot.startswith("digraph "), "DOT output must start with 'digraph '"
    assert "}" in dot.splitlines()[-1] or "}" in dot.splitlines()[-2]


def test_emits_state_example_json_valid(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    state = json.loads((tmp_path / "state.example.json").read_text())
    assert isinstance(state, dict)


def test_emits_readme(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    readme = (tmp_path / "README.md").read_text()
    assert graph.flow_name in readme
    assert "claude-skill" in readme.lower()
```

- [ ] **Step 2: Run, verify red**

Expected: 3 new failures (files don't exist).

- [ ] **Step 3: Reuse the existing DOT renderer**

Check `clio/cli.py` for the existing `_cmd_graph` implementation — it already calls a DOT renderer for the `--format dot` case. Find the function it calls (likely in a `clio/render/` module or similar). Reuse it. In `_claude_skill_helpers.py`:

```python
def render_process_flow_dot(graph: FlowGraph) -> str:
    """Render the flow as DOT. Reuses the existing renderer used by
    `python -m clio graph --format dot`.
    """
    from clio.render.dot import render_dot  # adapt import to the actual location
    return render_dot(graph)
```

If the renderer is private to `cli.py` or not exported, lift it to `clio/render/dot.py` first — this is the only refactor allowed in this sprint. If it's already in a `render/` module, just import.

- [ ] **Step 4: State example dump**

Append to `_claude_skill_helpers.py`:

```python
def render_state_example(graph: FlowGraph) -> str:
    """Initial-state template. Empty namespace per step at top level.

    Format: {"step01": {}, "step02": {}, ...} — one key per top-level
    STEP appearing in the flow, in topological order. Sub-steps inside
    control structures get their entries created at runtime by the
    LLM host.
    """
    state = {step.name: {} for step in graph.top_level_steps()}
    return json.dumps(state, indent=2) + "\n"
```

The exact accessor on `FlowGraph` to walk top-level steps depends on the existing API — check `clio/ir/graph.py` for a method like `iter_steps()`, `top_level_steps()`, or similar. Adapt the call.

- [ ] **Step 5: README**

```python
def render_readme(graph: FlowGraph) -> str:
    desc = (graph.flow_description or "").strip() or "(no description)"
    return (
        f"# {graph.flow_name} — claude-skill\n\n"
        f"Compiled from a CLIO `.clio` source for the `claude-skill` target.\n\n"
        f"**Flow purpose**: {desc}\n\n"
        "## How to install\n\n"
        "Copy this directory to `~/.claude/skills/<name>/`, then invoke from any Claude Code session.\n\n"
        "## Caveats\n\n"
        "This skill is executed by the LLM host. Fidelity of execution is "
        "conditioned on the rigor of the host — the TodoWrite checklist in "
        "`SKILL.md` provides the main anchor against drift.\n"
    )
```

- [ ] **Step 6: Wire the helpers in `emit`**

In `clio/emitters/claude_skill.py`, expand `emit`:

```python
    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        import sys
        from clio.emitters._claude_skill_helpers import (
            render_frontmatter,
            render_process_flow_dot,
            render_readme,
            render_state_example,
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        warn = lambda m: print(m, file=sys.stderr)
        body = render_frontmatter(graph, warn=warn) + f"\n# {graph.flow_name}\n"
        (output_dir / "SKILL.md").write_text(body)
        (output_dir / "process_flow.dot").write_text(render_process_flow_dot(graph))
        (output_dir / "state.example.json").write_text(render_state_example(graph))
        (output_dir / "README.md").write_text(render_readme(graph))
```

- [ ] **Step 7: Run, verify green**

```bash
pytest tests/test_emitters/test_claude_skill.py -v
```

- [ ] **Step 8: Commit**

```bash
git commit -am "feat(claude-skill): emit process_flow.dot + state.example.json + README"
```

---

## Task 4: STEP `exact` — autonomous Python script + SKILL.md section

**Files:**
- Modify: `clio/emitters/_claude_skill_helpers.py` (add `render_exact_script` + `render_exact_step_section`)
- Modify: `clio/emitters/claude_skill.py`
- Test: `tests/test_emitters/test_claude_skill.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_exact_step_emits_script(tmp_path):
    """A FLOW with one exact STEP must produce scripts/01_<name>.py."""
    src = (FIXTURES / "mvp_phase1.clio").read_text()  # phase1 = single exact step
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    scripts = sorted((tmp_path / "scripts").glob("*.py"))
    assert len(scripts) == 1
    assert scripts[0].name.startswith("01_")


def test_exact_step_script_is_autonomous(tmp_path):
    """Emitted script must read stdin JSON, write stdout JSON."""
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    script = next((tmp_path / "scripts").glob("*.py")).read_text()
    assert "import sys" in script
    assert "import json" in script
    assert "json.load(sys.stdin)" in script
    assert "json.dump(" in script


def test_skill_md_references_exact_step_script(tmp_path):
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    step_name = graph.top_level_steps()[0].name
    # The SKILL.md must contain a section for this step + the bash invocation
    assert f"## Step 01" in body or f"## Étape 01" in body
    assert step_name in body
    assert "scripts/01_" in body
    assert "python scripts/01_" in body
```

- [ ] **Step 2: Run, verify red**

- [ ] **Step 3: Implement the exact-script renderer (adapted from `_python_helpers.emit_default_exact_step`; type-utility helpers imported from `_shared_utils`)**

Open `clio/emitters/_python_helpers.py` and read `emit_default_exact_step` (currently at L225). It already produces a Python function body for an exact step. We adapt it into a **standalone script** (with `if __name__ == "__main__"` boilerplate).

In `_claude_skill_helpers.py`:

```python
def render_exact_script(step, contracts_by_name: dict, idx: int) -> str:
    """Standalone Python script for an exact STEP.

    Layout:
        #!/usr/bin/env python3
        import json, sys
        from pathlib import Path

        # JSON Schema validation (against schemas/NN_<name>.output.json)
        SCHEMA = Path(__file__).parent.parent / "schemas" / "<NN>_<name>.output.json"

        def run(state: dict) -> dict:
            # body adapted from emit_default_exact_step
            ...

        if __name__ == "__main__":
            state = json.load(sys.stdin)
            result = run(state)
            # validate result against SCHEMA via stdlib jsonschema (a dev-dep)
            json.dump({**state, "<step_name>": result}, sys.stdout, indent=2)
    """
    # Strategy: build the body by adapting emit_default_exact_step.
    # Inline the schema reference (the script can run without internet, just stdlib + jsonschema).
    # See _python_helpers.py:225-269 for the body convention to mirror.
    ...
```

Implementation note for the executing engineer: this is a one-time adaptation. The body of `emit_default_exact_step` produces a `def <step_name>(...)` function — you wrap that into a standalone script with `json.load(sys.stdin)` → call → `json.dump(merged_state, sys.stdout)`. Schema validation is done at the script level using `jsonschema.validate(result, schema_dict)` — `jsonschema` is already a transitive dev dep via pytest plugins; if not, declare it in `pyproject.toml` under `[project.optional-dependencies] skill = [...]`.

- [ ] **Step 4: Implement the SKILL.md section renderer for an exact step**

```python
def render_exact_step_section(step, idx: int, lang: str = "en") -> str:
    """Markdown section for an exact STEP.

    `lang`: "en" → "Step NN", "fr" → "Étape NN". Default "en".
    """
    label = {"en": "Step", "fr": "Étape"}[lang]
    title = f"## {label} {idx:02d} — {step.name} (MODE: exact)\n"
    doc = (step.description or "").strip()
    doc_block = f"\n{doc}\n" if doc else ""
    cmd = (
        f"\nRun:\n\n"
        f"    python scripts/{idx:02d}_{step.name}.py < state.json > state.next.json "
        f"&& mv state.next.json state.json\n\n"
    )
    tail = (
        "Tick the corresponding TodoWrite todo. "
        "Do not advance until the script exited 0.\n\n"
    )
    return title + doc_block + cmd + tail
```

- [ ] **Step 5: Detect emitted-skill language (heuristic)**

Append:

```python
def detect_skill_language(graph: FlowGraph) -> str:
    """Heuristic: if FLOW description or any STEP doc contains common French
    diacritics (é, è, à, ç, ô), emit in French; otherwise English. Conservative
    default is English."""
    sample = (graph.flow_description or "") + " ".join(
        (s.description or "") for s in graph.top_level_steps()
    )
    fr_markers = set("éèàçôî")
    return "fr" if any(c in fr_markers for c in sample) else "en"
```

(A later sprint may replace this with an explicit `lang:` annotation in the `.clio` source — out of scope.)

- [ ] **Step 6: Wire in `emit`**

In `claude_skill.py`, add the loop that emits scripts + extends `SKILL.md` body:

```python
    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        import sys
        from clio.emitters._claude_skill_helpers import (
            detect_skill_language,
            render_exact_script,
            render_exact_step_section,
            render_frontmatter,
            render_process_flow_dot,
            render_readme,
            render_state_example,
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "scripts").mkdir(exist_ok=True)

        warn = lambda m: print(m, file=sys.stderr)
        lang = detect_skill_language(graph)
        contracts = {c.name: c for c in graph.contracts}

        body_parts = [render_frontmatter(graph, warn=warn), f"\n# {graph.flow_name}\n"]
        for idx, step in enumerate(graph.top_level_steps(), start=1):
            if step.mode == "exact":
                script = render_exact_script(step, contracts, idx)
                (output_dir / "scripts" / f"{idx:02d}_{step.name}.py").write_text(script)
                body_parts.append(render_exact_step_section(step, idx, lang=lang))
            # judgment branch: deferred to Task 5
        (output_dir / "SKILL.md").write_text("".join(body_parts))

        (output_dir / "process_flow.dot").write_text(render_process_flow_dot(graph))
        (output_dir / "state.example.json").write_text(render_state_example(graph))
        (output_dir / "README.md").write_text(render_readme(graph))
```

Adapt `step.mode` and `step.name` to the actual `StepIR` API (check `clio/ir/graph.py`).

- [ ] **Step 7: Run, verify green**

- [ ] **Step 8: Commit**

```bash
git commit -am "feat(claude-skill): emit exact-step Python scripts + SKILL.md section"
```

---

## Task 4b: Bundle runtime helpers `_validate.py` and `_cache_key.py`

These bundled helpers keep the emitted skill self-contained (no PyPI dep at runtime) and make cache-key generation deterministic (no LLM-hashing-in-prose). Added in response to Gemini PR #11 review comments #3 and #4.

**Files:**
- Modify: `clio/emitters/_claude_skill_helpers.py` (add `render_bundled_validate_script`, `render_bundled_cache_key_script`)
- Modify: `clio/emitters/claude_skill.py` (write both scripts to `scripts/` in `emit`)
- Test: `tests/test_emitters/test_claude_skill.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_validate_helper_is_bundled(tmp_path):
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    validate_py = tmp_path / "scripts" / "_validate.py"
    assert validate_py.exists()
    body = validate_py.read_text()
    assert "import json" in body
    assert "import sys" in body
    # Must not blow up if jsonschema is missing — stdlib fallback
    assert "try:" in body and "jsonschema" in body


def test_cache_key_helper_is_bundled(tmp_path):
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    key_py = tmp_path / "scripts" / "_cache_key.py"
    assert key_py.exists()
    body = key_py.read_text()
    assert "hashlib" in body
    assert "sha256" in body


def test_validate_helper_runs_against_real_schema(tmp_path):
    """Layer 2 check: the bundled validator must actually work."""
    import subprocess
    src = (FIXTURES / "mvp_phase2.clio").read_text()  # has at least one schema
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    schema_path = next((tmp_path / "schemas").glob("*.output.json"))
    schema = json.loads(schema_path.read_text())
    # Build a trivially-valid instance from the schema's properties:
    instance = {k: _zero_value_for(s) for k, s in schema.get("properties", {}).items()}
    (tmp_path / "out.json").write_text(json.dumps(instance))
    result = subprocess.run(
        ["python", str(tmp_path / "scripts" / "_validate.py"),
         str(tmp_path / "out.json"), str(schema_path)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"validator failed: {result.stderr}"


def _zero_value_for(schema):
    t = schema.get("type")
    return {"string": "", "integer": 0, "number": 0.0, "boolean": False,
            "array": [], "object": {}}.get(t, None)
```

- [ ] **Step 2: Run, verify red**

- [ ] **Step 3: Implement `render_bundled_validate_script`**

In `_claude_skill_helpers.py`:

```python
BUNDLED_VALIDATE_PY = '''\
#!/usr/bin/env python3
"""Bundled JSON Schema validator for CLIO-emitted skills.

Usage: python _validate.py <instance.json> <schema.json>
Exits 0 if valid, non-zero with a human-readable message otherwise.

Prefers the `jsonschema` PyPI package when available; falls back to a
minimal stdlib check (type + required + property types) so the skill
remains usable on bare Python installs.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path


def _stdlib_validate(instance, schema, path="$"):
    t = schema.get("type")
    if t == "object":
        if not isinstance(instance, dict):
            raise ValueError(f"{path}: expected object, got {type(instance).__name__}")
        for req in schema.get("required", []):
            if req not in instance:
                raise ValueError(f"{path}: missing required field '{req}'")
        for k, sub in schema.get("properties", {}).items():
            if k in instance:
                _stdlib_validate(instance[k], sub, f"{path}.{k}")
    elif t == "array":
        if not isinstance(instance, list):
            raise ValueError(f"{path}: expected array, got {type(instance).__name__}")
        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(instance):
                _stdlib_validate(item, items_schema, f"{path}[{i}]")
    elif t == "string":
        if not isinstance(instance, str):
            raise ValueError(f"{path}: expected string")
    elif t == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            raise ValueError(f"{path}: expected integer")
    elif t == "number":
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            raise ValueError(f"{path}: expected number")
    elif t == "boolean":
        if not isinstance(instance, bool):
            raise ValueError(f"{path}: expected boolean")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: _validate.py <instance.json> <schema.json>", file=sys.stderr)
        return 2
    instance = json.loads(Path(sys.argv[1]).read_text())
    schema = json.loads(Path(sys.argv[2]).read_text())
    try:
        import jsonschema  # type: ignore
        jsonschema.validate(instance, schema)
    except ImportError:
        try:
            _stdlib_validate(instance, schema)
        except ValueError as e:
            print(f"validation error: {e}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"validation error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def render_bundled_validate_script() -> str:
    return BUNDLED_VALIDATE_PY
```

- [ ] **Step 4: Implement `render_bundled_cache_key_script`**

```python
BUNDLED_CACHE_KEY_PY = '''\
#!/usr/bin/env python3
"""Bundled deterministic cache-key generator for CLIO-emitted skills.

Usage: python _cache_key.py <state.json> <step_name> <key_fields_json>
Emits SHA256 hex on stdout.

`key_fields_json` is a JSON array of dotted paths into <state.json>
(e.g. '["customer.id", "order.items"]'). Missing paths are treated as
null, which deterministically participates in the hash.
"""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path


def _get(state, dotted_path):
    cur = state
    for part in dotted_path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: _cache_key.py <state.json> <step_name> <key_fields_json>", file=sys.stderr)
        return 2
    state = json.loads(Path(sys.argv[1]).read_text())
    step_name = sys.argv[2]
    key_fields = json.loads(sys.argv[3])
    payload = {"step": step_name, "inputs": {p: _get(state, p) for p in key_fields}}
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    print(hashlib.sha256(canon).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def render_bundled_cache_key_script() -> str:
    return BUNDLED_CACHE_KEY_PY
```

- [ ] **Step 5: Wire in `emit` (write both helpers to `scripts/`)**

In `claude_skill.py`'s `emit`, after `(output_dir / "scripts").mkdir(exist_ok=True)`:

```python
        (output_dir / "scripts" / "_validate.py").write_text(render_bundled_validate_script())
        (output_dir / "scripts" / "_cache_key.py").write_text(render_bundled_cache_key_script())
```

- [ ] **Step 6: Run, verify green**

- [ ] **Step 7: Commit**

```bash
git commit -am "feat(claude-skill): bundle _validate.py and _cache_key.py runtime helpers"
```

---

## Task 5: STEP `judgment` — prompt template + output schema + SKILL.md section

**Files:**
- Modify: `clio/emitters/_claude_skill_helpers.py` (add `render_judgment_prompt`, `render_judgment_step_section`)
- Modify: `clio/emitters/claude_skill.py`
- Test: `tests/test_emitters/test_claude_skill.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_judgment_step_emits_prompt_template(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()  # has at least one judgment
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    prompts = sorted((tmp_path / "prompts").glob("*.md"))
    assert len(prompts) >= 1
    body = prompts[0].read_text()
    assert "{{" in body and "}}" in body  # placeholders preserved


def test_judgment_step_emits_output_schema(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    out_schemas = sorted((tmp_path / "schemas").glob("*.output.json"))
    assert len(out_schemas) >= 1
    schema = json.loads(out_schemas[0].read_text())
    # JSON Schema must declare type=object with properties
    assert schema.get("type") == "object"
    assert "properties" in schema


def test_skill_md_judgment_section_has_prompt_and_schema_refs(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "MODE: judgment" in body
    assert "prompts/" in body
    assert "schemas/" in body
    assert "jsonschema" in body  # validation command mentioned
```

- [ ] **Step 2: Run, verify red**

- [ ] **Step 3: Prompt template renderer**

```python
def render_judgment_prompt(step) -> str:
    """Markdown file with the judgment prompt template + {{state.x}} placeholders.

    Mirrors the prompt body that `python` emitter would feed to anthropic.messages.create.
    `step.prompt` is the raw template (already with placeholders) from the .clio source —
    we preserve verbatim, only adding a brief header.
    """
    prompt = (step.prompt or "").strip()
    header = (
        f"# Prompt template — {step.name}\n\n"
        f"Substitute `{{{{state.x}}}}` placeholders from `state.json` before sending.\n\n"
        "---\n\n"
    )
    return header + prompt + "\n"
```

- [ ] **Step 4: Output schema renderer (via Pydantic `model_json_schema`)**

```python
def render_output_schema(step, contracts_by_name: dict) -> str:
    """JSON Schema for the step's output contract.

    `step.gives` references a contract by name. Reuse the Pydantic model already
    built in clio/ir/contracts.py (via `_python_helpers._type_to_python` patterns
    — but here we want the schema, not the Python type).
    """
    from clio.ir.contracts import contract_to_pydantic  # check actual API
    model = contract_to_pydantic(contracts_by_name[step.gives])
    return json.dumps(model.model_json_schema(), indent=2) + "\n"
```

If `contract_to_pydantic` doesn't exist with that exact name, find the equivalent helper already used by the `python` emitter — read `_python_helpers.emit_contracts` (L478) to locate it. Reuse the same path.

- [ ] **Step 5: SKILL.md section for judgment**

```python
def render_judgment_step_section(step, idx: int, lang: str = "en") -> str:
    label = {"en": "Step", "fr": "Étape"}[lang]
    title = f"## {label} {idx:02d} — {step.name} (MODE: judgment)\n"
    doc = (step.description or "").strip()
    doc_block = f"\n{doc}\n" if doc else ""
    body = (
        f"\n**Reads from state**: see prompt template `prompts/{idx:02d}_{step.name}.md`\n"
        f"**Writes to state**: `state.{step.name}` validated by "
        f"`schemas/{idx:02d}_{step.name}.output.json`\n\n"
        f"Steps:\n"
        f"1. Read `prompts/{idx:02d}_{step.name}.md`, substitute `{{{{state.x}}}}` "
        f"placeholders from `state.json`.\n"
        f"2. Generate an output as the assistant, save verbatim to `out.json`.\n"
        f"3. Validate using the bundled helper:\n\n"
        f"        python scripts/_validate.py out.json schemas/{idx:02d}_{step.name}.output.json\n\n"
        f"4. If exit 0 (valid): merge into `state.json` under `state.{step.name}`.\n"
        f"5. If exit ≠ 0 (invalid): see RESCUE/RETRY section below if present, "
        f"otherwise stop.\n\n"
        "Tick the corresponding TodoWrite todo.\n\n"
    )
    return title + doc_block + body
```

- [ ] **Step 6: Wire in `emit`**

Extend the loop in `emit`:

```python
        (output_dir / "prompts").mkdir(exist_ok=True)
        (output_dir / "schemas").mkdir(exist_ok=True)
        ...
        for idx, step in enumerate(graph.top_level_steps(), start=1):
            if step.mode == "exact":
                # ... as in Task 4
            elif step.mode == "judgment":
                (output_dir / "prompts" / f"{idx:02d}_{step.name}.md").write_text(
                    render_judgment_prompt(step)
                )
                (output_dir / "schemas" / f"{idx:02d}_{step.name}.output.json").write_text(
                    render_output_schema(step, contracts)
                )
                body_parts.append(render_judgment_step_section(step, idx, lang=lang))
```

- [ ] **Step 7: Run, verify green**

- [ ] **Step 8: Commit**

```bash
git commit -am "feat(claude-skill): emit judgment-step prompt + schema + SKILL.md section"
```

---

## Task 6: Input contracts as schemas (when a step declares `TAKES`)

**Files:**
- Modify: `clio/emitters/_claude_skill_helpers.py`
- Modify: `clio/emitters/claude_skill.py`
- Test: `tests/test_emitters/test_claude_skill.py`

- [ ] **Step 1: Write the failing test**

```python
def test_step_with_takes_emits_input_schema(tmp_path):
    """When a STEP has TAKES <contract>, schemas/NN_<name>.input.json is emitted."""
    # Find a fixture that uses TAKES — mvp_v03_contracts.clio is the canonical one.
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    input_schemas = sorted((tmp_path / "schemas").glob("*.input.json"))
    assert len(input_schemas) >= 1
    schema = json.loads(input_schemas[0].read_text())
    assert schema.get("type") == "object"
```

- [ ] **Step 2: Run, verify red**

- [ ] **Step 3: Add `render_input_schema` (mirror of output)**

```python
def render_input_schema(step, contracts_by_name: dict) -> str | None:
    """JSON Schema for the step's input contract. None if no TAKES."""
    if not step.takes:
        return None
    from clio.ir.contracts import contract_to_pydantic
    model = contract_to_pydantic(contracts_by_name[step.takes])
    return json.dumps(model.model_json_schema(), indent=2) + "\n"
```

- [ ] **Step 4: Wire in `emit`** — for every step (both modes), if `render_input_schema(...)` returns a string, write it to `schemas/NN_<name>.input.json`.

- [ ] **Step 5: Run, verify green**

- [ ] **Step 6: Commit**

```bash
git commit -am "feat(claude-skill): emit input contract JSON Schemas"
```

---

## Task 7: Conditional sub-flows — IF / MATCH

**Files:**
- Modify: `clio/emitters/_claude_skill_helpers.py` (add `render_if_section`, `render_match_section`)
- Modify: `clio/emitters/claude_skill.py` (recursive walk of IR sub-flows)
- Test: `tests/test_emitters/test_claude_skill.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_if_branches_appear_in_skill_md(tmp_path):
    # Find a fixture with IF/ELSE — mvp_phase6.clio uses control flow.
    src = (FIXTURES / "mvp_phase6.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "### IF " in body or "### Si " in body
    # The two branches must be referenced
    assert "True" in body or "Vrai" in body
    assert "False" in body or "Faux" in body


def test_match_branches_appear_in_skill_md(tmp_path):
    # Find a fixture with MATCH — search tests/fixtures/ for a .clio mentioning MATCH.
    candidate = FIXTURES / "mvp_phase8.clio"  # adjust if MATCH lives elsewhere
    src = candidate.read_text()
    if "MATCH" not in src:
        import pytest
        pytest.skip("No MATCH fixture available; covered by E2E task instead.")
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "### MATCH" in body or "### Cas" in body
```

- [ ] **Step 2: Run, verify red**

- [ ] **Step 3: Implement `render_if_section`**

```python
def render_if_section(if_node, idx_prefix: str, lang: str = "en") -> tuple[str, list]:
    """Render an IF/ELSE block as a SKILL.md sub-section.

    Returns (markdown, sub_steps) where sub_steps is a flat list of (idx_label, step_or_subnode)
    pairs to be rendered as separate sections after this header.
    """
    title = {"en": "IF", "fr": "Si"}[lang]
    cond = if_node.condition_repr  # human-readable rendering of the condition (adapt to IR API)
    head = (
        f"### {title} {cond}  (source line {if_node.source_line})\n\n"
        f"Evaluate the condition. If true: follow branch A. Else: follow branch B.\n\n"
    )
    # Sub-steps get ordinals like "Step 03a", "Step 03b" — flat-listed under this header.
    sub_steps = []
    for i, item in enumerate(if_node.then_branch):
        sub_steps.append((f"{idx_prefix}a-{i+1}", item))
    for i, item in enumerate(if_node.else_branch or []):
        sub_steps.append((f"{idx_prefix}b-{i+1}", item))
    return head, sub_steps
```

(`if_node.condition_repr` is the rendered condition string — if not present in the IR, derive it from `if_node.condition` similarly to how `python.py` does it. Check `_python_helpers._python_condition_expr` at L1141 for the pattern.)

- [ ] **Step 4: Implement `render_match_section`** (mirror, with `for case in match.cases`).

- [ ] **Step 5: Recursive walk in `emit`**

Replace the flat `for idx, step in enumerate(graph.top_level_steps(), ...)` loop with a recursive helper:

```python
def _walk_flow(items, idx_prefix: str, contracts: dict, lang: str, output_dir: Path) -> list[str]:
    """Recursively render a flow chain (list of FlowItemIR) into SKILL.md body parts.
    Side effect: writes scripts/, prompts/, schemas/ files."""
    parts = []
    for i, item in enumerate(items, start=1):
        label = f"{idx_prefix}{i:02d}" if idx_prefix == "" else f"{idx_prefix}-{i}"
        # dispatch on item type: StepIR / IfIR / MatchIR / WhileIR / ForEachIR / RescueBlockIR
        if isinstance(item, StepIR):
            parts.append(_render_step(item, label, contracts, lang, output_dir))
        elif isinstance(item, IfIR):
            head, sub = render_if_section(item, label, lang=lang)
            parts.append(head)
            parts.extend(_walk_flow([s for _, s in sub], label, contracts, lang, output_dir))
        # ... MatchIR similarly
    return parts
```

This refactors `emit` — the existing exact/judgment branches become `_render_step`.

- [ ] **Step 6: Run, verify green**

- [ ] **Step 7: Commit**

```bash
git commit -am "feat(claude-skill): IF / MATCH conditional sub-flows in SKILL.md"
```

---

## Task 8: Iteration sub-flows — WHILE / FOR EACH (with TodoWrite per-iteration instruction)

**Files:**
- Modify: `clio/emitters/_claude_skill_helpers.py` (add `render_while_section`, `render_for_each_section`)
- Modify: `clio/emitters/claude_skill.py` (extend `_walk_flow` dispatch)
- Test: `tests/test_emitters/test_claude_skill.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_for_each_renders_with_todo_instruction(tmp_path):
    # Find a fixture with FOR EACH — parallel_foreach plan probably ships one.
    candidate = FIXTURES / "mvp_phase9.clio"  # adjust as needed
    src = candidate.read_text()
    if "FOR EACH" not in src and "FOREACH" not in src.upper():
        import pytest
        pytest.skip("No FOR EACH fixture; covered by E2E.")
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "### FOR EACH" in body or "### Pour chaque" in body
    assert "sub-todo" in body or "sous-todo" in body


def test_while_renders_with_loop_instruction(tmp_path):
    # Same pattern — skip if no fixture.
    ...
```

- [ ] **Step 2: Run, verify red (or skipped if no fixture; ensure at least one runs)**

If no fixture covers FOR EACH yet, add a minimal one as part of this task:

```bash
# tests/fixtures/mvp_skill_foreach.clio  (new fixture if needed)
```

…with a tiny `FOR EACH` body. Re-run the test.

- [ ] **Step 3: Implement `render_for_each_section`**

```python
def render_for_each_section(node, idx_prefix: str, lang: str = "en") -> tuple[str, list]:
    title = {"en": "FOR EACH", "fr": "Pour chaque"}[lang]
    head = (
        f"### {title} `{node.var}` in `state.{node.collection}`  "
        f"(source line {node.source_line})\n\n"
        f"For each element of `state.{node.collection}`:\n"
        f"- Create a TodoWrite sub-todo \"Iteration {node.var}=<value>\".\n"
        f"- Run the sub-sequence below.\n"
        f"- Append the result to `state.{node.result_field}`.\n"
        f"- Mark the sub-todo done.\n\n"
    )
    sub_steps = [(f"{idx_prefix}/{i+1}", item) for i, item in enumerate(node.body)]
    return head, sub_steps
```

Adapt `node.var`, `node.collection`, `node.result_field`, `node.body` to the actual `ForEachIR` field names.

- [ ] **Step 4: Implement `render_while_section`** (mirror).

- [ ] **Step 5: Extend `_walk_flow` dispatch**

- [ ] **Step 6: Run, verify green**

- [ ] **Step 7: Commit**

```bash
git commit -am "feat(claude-skill): WHILE / FOR EACH with TodoWrite sub-todo instruction"
```

---

## Task 9: Step modifiers — CACHE + RETRY + RESOURCES

**Files:**
- Modify: `clio/emitters/_claude_skill_helpers.py`
- Modify: `clio/emitters/claude_skill.py`
- Test: `tests/test_emitters/test_claude_skill.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_cache_block_appears_in_step_section(tmp_path):
    src = (FIXTURES / "mvp_v02_cache.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "Cache" in body or "cache" in body
    assert ".cache/" in body


def test_resources_listed_in_skill_md_annex(tmp_path):
    # If the source flow has RESOURCES, an annex section must list them.
    src = (FIXTURES / "mvp_phase8.clio").read_text()  # adjust if RESOURCES live elsewhere
    graph = build_ir(parse(src))
    if not graph.resources:
        import pytest
        pytest.skip("No RESOURCES in this fixture; covered by E2E.")
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "## Resources" in body or "## Ressources" in body
```

- [ ] **Step 2: Run, verify red**

- [ ] **Step 3: Implement `render_cache_block(step)`, `render_retry_block(step)`, `render_resources_annex(graph)`**

Three small string-renderers. Each must:
- include the step name / key formula / budget verbatim from the IR
- include actionable instructions for the LLM host ("check `.cache/NN_<step>.json` before executing; if present and key matches, skip and load")

Code sketch (use the same pattern as the previous renderers):

```python
def render_cache_block(step, lang: str = "en") -> str:
    if not step.cache:
        return ""
    label = {"en": "Cache", "fr": "Mise en cache"}[lang]
    # Serialize the cache-key fields as JSON for the bundled helper invocation.
    import json as _json
    key_fields_json = _json.dumps(step.cache.key_fields)  # adapt attr name to actual IR
    return (
        f"**{label}**: before executing, compute the cache key:\n\n"
        f"    KEY=$(python scripts/_cache_key.py state.json '{step.name}' '{key_fields_json}')\n\n"
        f"If `.cache/{step.name}_${{KEY}}.json` exists, skip execution and merge its "
        f"contents into `state.json` under `state.{step.name}`. Otherwise run normally "
        f"and write the output to `.cache/{step.name}_${{KEY}}.json` after success.\n\n"
    )
```

- [ ] **Step 4: Wire into the step renderers** (append the CACHE/RETRY blocks at the end of each step section if present).

- [ ] **Step 5: Append RESOURCES annex at the end of SKILL.md**

- [ ] **Step 6: Run, verify green**

- [ ] **Step 7: Commit**

```bash
git commit -am "feat(claude-skill): CACHE / RETRY / RESOURCES modifiers in SKILL.md"
```

---

## Task 10: RESCUE handlers + `step.error.*` + RESUME (v0.13 parity)

**Files:**
- Modify: `clio/emitters/_claude_skill_helpers.py` (add `render_rescue_section`)
- Modify: `clio/emitters/claude_skill.py` (extend `_walk_flow` to handle `RescueBlockIR`)
- Test: `tests/test_emitters/test_claude_skill.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_rescue_section_emits_and_mentions_error_fields(tmp_path):
    src = (FIXTURES / "mvp_v02_fallback.clio").read_text()  # if rescue lives here; otherwise pick the v0.13 fixture
    if "RESCUE" not in src:
        import pytest
        pytest.skip("No RESCUE in this fixture; covered by E2E task 14.")
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "## RESCUE" in body or "### If" in body
    assert ".error.message" in body
    assert ".error.type" in body


def test_resume_terminator_renders_with_field_assignment(tmp_path):
    src_path = FIXTURES.parent.parent / "examples" / "critical_pipeline_resume.clio"
    src = src_path.read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "RESUME" in body
    # The RESUME instruction must include the target field assignment
    assert "← " in body or "<-" in body or "= " in body
```

- [ ] **Step 2: Run, verify red**

- [ ] **Step 3: Implement `render_rescue_section`**

```python
def render_rescue_section(rescue_block, rescued_step_name: str, lang: str = "en") -> str:
    """Render a RESCUE block as a SKILL.md sub-section.

    The body chain (rescue_block.chain) is a list of FlowItemIR — render each
    item in turn (delegate to the step renderers already in this module).
    Terminate with the AbortIR or ResumeIR rendering.
    """
    header_label = {"en": "If", "fr": "Si"}[lang]
    head = (
        f"### {header_label} step `{rescued_step_name}` fails\n\n"
        f"Available in the handler: `{rescued_step_name}.error.message`, "
        f"`{rescued_step_name}.error.type`.\n\n"
    )
    chain_md = ""  # render each item — for v1, render exact sub-steps inline and judgment sub-steps as sub-prompts.
    # ... (loop over rescue_block.chain)
    terminator = rescue_block.terminator
    if isinstance(terminator, ResumeIR):
        term_md = (
            f"**RESUME**: set `state.{rescued_step_name}.{terminator.field_name}` "
            f"← value of `state.{terminator.fallback_step}.{terminator.field_name}`, "
            f"then advance to the step after `{rescued_step_name}`.\n\n"
        )
    else:  # AbortIR
        term_md = f"**Abort**: stop the flow with message `{terminator.message}`.\n\n"
    return head + chain_md + term_md
```

(Import `ResumeIR`, `AbortIR` from `clio.ir.graph` at the top of `_claude_skill_helpers.py`.)

- [ ] **Step 4: Extend `_walk_flow` to dispatch `RescueBlockIR`**

```python
        elif isinstance(item, RescueBlockIR):
            parts.append(render_rescue_section(item, item.step_name, lang=lang))
```

Note: RESCUE blocks live at the same level as their rescued step. The walk encounters them in the chain order — they're rendered in a "RESCUE handlers" section at the bottom of `SKILL.md` (or inline; the spec leaves both acceptable, pick inline for v1 — simpler, no second pass).

- [ ] **Step 5: Run, verify green**

- [ ] **Step 6: Commit**

```bash
git commit -am "feat(claude-skill): RESCUE handlers + step.error + RESUME (v0.13 parity)"
```

---

## Task 11: Compile-time warnings and errors

**Files:**
- Modify: `clio/emitters/claude_skill.py` (validate before emitting)
- Test: `tests/test_emitters/test_claude_skill.py`

- [ ] **Step 1: Write the failing tests**

```python
import pytest


def test_unsupported_exact_language_raises_at_compile(tmp_path):
    # Construct a graph with an exact step in an unsupported lang.
    src = '''FLOW only_rust:
    @step01:
        STEP "rusty":
            MODE: exact
            CODE rust: """fn main() { }"""
'''
    graph = build_ir(parse(src))
    with pytest.raises(ValueError) as exc:
        ClaudeSkillEmitter().emit(graph, tmp_path)
    assert "claude-skill v1 supports python and bash" in str(exc.value)
    assert "line " in str(exc.value).lower()  # source line included


def test_parallel_construct_emits_warning(tmp_path, capsys):
    # Find or build a flow with PARALLEL_FOR_EACH.
    src = (FIXTURES / "parallel_foreach.clio").read_text() if (FIXTURES / "parallel_foreach.clio").exists() else None
    if src is None:
        pytest.skip("No parallel fixture available.")
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    captured = capsys.readouterr()
    assert "claude-skill warning" in captured.err
    assert "parallelism" in captured.err.lower() or "serialized" in captured.err.lower()
```

- [ ] **Step 2: Run, verify red**

- [ ] **Step 3: Add a pre-emit validation pass**

In `claude_skill.py`, before the writes:

```python
    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        import sys
        warn = lambda m: print(m, file=sys.stderr)
        self._validate(graph, warn)
        # ... rest of emit unchanged

    def _validate(self, graph: FlowGraph, warn) -> None:
        for step in graph.iter_all_steps():  # recursive walk including sub-flows
            if step.mode == "exact" and step.lang not in {"python", "bash"}:
                raise ValueError(
                    f"claude-skill v1 supports python and bash for exact steps; "
                    f"got '{step.lang}' at line {step.source_line}"
                )
        if graph.has_parallel():
            warn(
                "claude-skill warning: source flow contains PARALLEL; the emitted "
                "skill serializes steps in topological order (LLM host does not "
                "execute concurrently)."
            )
        # WHILE without budget — best-effort detection; warn only.
```

- [ ] **Step 4: Run, verify green**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(claude-skill): compile-time validation (unsupported lang, parallel, …)"
```

---

## Task 12: Layer 2 — execute emitted exact scripts in tests

**Files:**
- Modify: `tests/test_emitters/test_claude_skill.py`

- [ ] **Step 1: Write the test**

```python
import subprocess


def test_emitted_exact_script_runs_against_state_example(tmp_path):
    """The emitted exact-step script must read state.example.json on stdin and
    produce a JSON output that validates against schemas/NN_<name>.output.json."""
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    state_example = (tmp_path / "state.example.json").read_text()
    script = next((tmp_path / "scripts").glob("*.py"))
    result = subprocess.run(
        ["python", str(script)],
        input=state_example,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"Script failed: stderr={result.stderr}"
    output = json.loads(result.stdout)
    assert isinstance(output, dict)
```

- [ ] **Step 2: Run, verify green** (if it fails, fix the script template in `render_exact_script` until it passes — this is the integration check that the adapted helper actually produces standalone code)

- [ ] **Step 3: Commit**

```bash
git commit -am "test(claude-skill): Layer 2 — emitted exact script runs against state example"
```

---

## Task 13: Documentation

**Files:**
- Modify: `docs/COMPILATION_TARGETS.md` (move `claude-skill` to Implemented + dedicated section)
- Modify: `docs/manual/03-cookbook.md` (or current cookbook filename — adjust)
- Modify: `docs/manual/06-troubleshooting.md` (or current troubleshooting filename)
- Modify: `CHANGELOG.md`
- Create: `examples/skill_minimal.clio`

- [ ] **Step 1: Update `COMPILATION_TARGETS.md`**

In the "Targets at a glance" table, change the `claude-skill` (or add it if absent) row to `Implemented`. Then add a full section "`target: claude-skill`" with the same structure used for `claude-cli`, `python`, `mcp-server` (mapping table, runtime dependency, layout, caveats).

- [ ] **Step 2: Cookbook recipe**

Append a new recipe to `docs/manual/03-cookbook.md` (verify the exact file name first — `ls docs/manual/`). The recipe explains: how to compile a `.clio` to a skill, how to install the output into `~/.claude/skills/<name>/`, how to invoke it, and the constraints (LLM host orchestration, no API key).

- [ ] **Step 3: Troubleshooting entries**

Append entries to the troubleshooting doc covering the new compile-time messages:
- "claude-skill warning: FLOW … has no description"
- "claude-skill v1 supports python and bash for exact steps; got '<lang>'"
- "claude-skill warning: source flow contains PARALLEL …"
- And the corresponding fixes.

- [ ] **Step 4: CHANGELOG**

Add a new section (top of file). Until the version bump happens in a separate commit, label the section with a placeholder version like "## Unreleased" — or follow the convention of the existing file (read its top to match).

```markdown
## Unreleased

### Added
- `target: claude-skill` — new compilation target emitting a Claude Code skill directory
  (`SKILL.md` + `scripts/` + `schemas/` + `prompts/` + `process_flow.dot`).
  LLM-host-orchestrated execution model; parity with v0.13 features
  (RESCUE, `step.error.*`, RESUME, CACHE, RETRY, RESOURCES).
```

- [ ] **Step 5: Minimal example**

`examples/skill_minimal.clio`:

```
FLOW skill_minimal:
    "Compile this with `python -m clio compile examples/skill_minimal.clio --target claude-skill --output ./skill-min`"

    @greet:
        STEP "say hello":
            MODE: exact
            CODE python: """
                return {"msg": "Hello from a CLIO-compiled skill."}
            """
            GIVES: { "msg": str }
```

(Adapt syntax to the current `.clio` grammar — verify against `examples/` for the exact format.)

- [ ] **Step 6: Commit**

```bash
git add docs/COMPILATION_TARGETS.md docs/manual/ CHANGELOG.md examples/skill_minimal.clio
git commit -m "docs(claude-skill): COMPILATION_TARGETS + cookbook + troubleshooting + CHANGELOG"
```

---

## Task 14: End-to-end regression — golden snapshots on representative fixtures

**Files:**
- Modify: `tests/test_emitters/test_claude_skill.py`
- Create: `tests/fixtures/expected_skill/<fixture_name>/` (generated content from running the emitter — see Step 2)

- [ ] **Step 1: Write the golden-snapshot tests for 3 fixtures**

```python
def _read_tree(root):
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = p.read_text()
    return out


@pytest.mark.parametrize("fixture", ["mvp_phase1", "mvp_phase2", "critical_pipeline_resume"])
def test_emit_golden(tmp_path, fixture):
    src_dir = FIXTURES.parent.parent / "examples" if fixture == "critical_pipeline_resume" else FIXTURES
    src = (src_dir / f"{fixture}.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    expected = _read_tree(FIXTURES / "expected_skill" / fixture)
    actual = _read_tree(tmp_path)
    assert actual == expected
```

- [ ] **Step 2: Generate the goldens for the first time**

```bash
for f in mvp_phase1 mvp_phase2; do
    rm -rf tests/fixtures/expected_skill/$f
    python -m clio compile tests/fixtures/$f.clio --target claude-skill --output tests/fixtures/expected_skill/$f
done
rm -rf tests/fixtures/expected_skill/critical_pipeline_resume
python -m clio compile examples/critical_pipeline_resume.clio --target claude-skill --output tests/fixtures/expected_skill/critical_pipeline_resume
```

**Review every generated file manually** — these are the goldens that future tests will diff against. If anything looks wrong, fix the emitter and regenerate. Do not commit goldens that contain emitter bugs.

- [ ] **Step 3: Run the golden tests, verify green**

```bash
pytest tests/test_emitters/test_claude_skill.py::test_emit_golden -v
```

- [ ] **Step 4: Run the full test suite, verify no regression**

```bash
pytest tests/ -q
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_emitters/test_claude_skill.py tests/fixtures/expected_skill/
git commit -m "test(claude-skill): golden snapshots for mvp_phase1, mvp_phase2, critical_pipeline_resume"
```

---

## Final verification

- [ ] **Run the full test suite one last time**

```bash
pytest tests/ -q
```

- [ ] **Smoke-test the CLI**

```bash
python -m clio compile examples/skill_minimal.clio --target claude-skill --output /tmp/skill-min
ls /tmp/skill-min
cat /tmp/skill-min/SKILL.md
```

- [ ] **Optional manual install** (documented in troubleshooting): copy `/tmp/skill-min` into `~/.claude/skills/skill-minimal/` and try invoking it in a Claude Code session. Document any drift in `docs/manual/06-troubleshooting.md` under the new "Validating an emitted skill" entry.

---

## Self-review checklist (run after the plan is written, before handoff)

The following list captures spec items vs tasks. If any row is unchecked, add a task.

| Spec section / requirement | Task |
|---|---|
| `ClaudeSkillEmitter` extends `BaseEmitter` | Task 1 |
| CLI registration | Task 1 |
| Frontmatter (`name`, `description`, `allowed-tools`) + description fallback warning | Task 2 |
| `process_flow.dot` | Task 3 |
| `state.example.json` | Task 3 |
| `README.md` | Task 3 |
| STEP exact → `scripts/NN_<name>.py` + SKILL.md section | Task 4 |
| Bundled runtime helpers `_validate.py` + `_cache_key.py` (Gemini #3 + #4) | Task 4b |
| STEP judgment → `prompts/NN_<name>.md` + `schemas/NN_<name>.output.json` + SKILL.md section (validation via `scripts/_validate.py`) | Task 5 |
| Contract input schemas (TAKES) | Task 6 |
| Contract output schemas (GIVES) | Task 5 |
| Control structures IF / MATCH | Task 7 |
| Control structures WHILE / FOR EACH | Task 8 |
| CACHE / RETRY / RESOURCES | Task 9 |
| RESCUE / step.error / RESUME (v0.13 parity) | Task 10 |
| Compile-time validation (unsupported lang, parallel warning, …) | Task 11 |
| Layer 2 test (exec exact script) | Task 12 |
| Docs (COMPILATION_TARGETS, cookbook, troubleshooting, CHANGELOG, manual) | Task 13 |
| E2E goldens on 3 representative fixtures including v0.13 regression | Task 14 |
| FR/EN language heuristic (incl. "Cache" → "Mise en cache", Gemini #2) | Task 4 (Step 5), Task 9 (Step 3) |
| Extract type-utility helpers into `_shared_utils.py` (Gemini #1) | Task 1b |
| Convention-specific renderer for `exact`-step script (adapted from `emit_default_exact_step`, not extracted) | Task 4 / Task 5 |
| No edits to `parser/`, `ir/`, other emitters | Verified at each commit via `git diff --stat` |
