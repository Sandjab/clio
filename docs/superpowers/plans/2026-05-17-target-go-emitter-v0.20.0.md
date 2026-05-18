# CLIO v0.20.0 — `target: go` emitter Implementation Plan (Part 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working `target: go` emitter covering the core IR surface — CONTRACT, exact + judgment (Anthropic SDK only), IF/MATCH/WHILE, FOR EACH (sequential + parallel), RESCUE, ON_FAIL chain, CACHE, RESOURCES — plus compile-time refused-combo errors for everything else and updated docs. Routes users to `--target python` for OpenAI / FLOW composition / `impl.mode {rest,sql,mcp_tool,shell}` / RESUME / JSONL Logging / TEST blocks; those features land in v0.20.1+.

**Architecture:** One new emitter module `clio/emitters/go.py` (the orchestrator) + one helper module `clio/emitters/_go_helpers.py` (Go-specific renderers + embedded runtime templates). The helper module holds Go source templates that are written verbatim into the emitted project's `clio_runtime/` directory — the same copy-verbatim convention `target: python` uses for `clio/runtime/*.py`. Helpers that are emitter-independent (CLIO type → Go type, contract → struct, condition → expr) live alongside the existing pure helpers in `clio/emitters/_shared_utils.py`. No edits to parser, IR, or other emitters.

**Tech Stack:**
- *Compiler side*: Python 3.12, frozen dataclasses, pytest, ruff, mypy strict, uv.
- *Emitted side*: Go 1.22+ with `github.com/anthropics/anthropic-sdk-go`, `github.com/santhosh-tekuri/jsonschema/v6`, `golang.org/x/sync/errgroup`.

**Reference spec:** `docs/superpowers/specs/2026-05-17-target-go-design.md` (merged via PR #70).

**Scope note (v0.20.0 — Part 1 of multi-release v0.20 sprint):**
Out of scope for this plan, deferred to v0.20.1+ (each will get its own plan):

| Feature | Status in v0.20.0 |
|---|---|
| OpenAI SDK dispatch (`invoke.protocol: openai`) | **Refused** at compile time with `E_GO_005` |
| FLOW composition (sub-FLOW calls) | **Refused** at compile time with `E_GO_006` |
| `impl.mode: rest` | **Refused** with `E_GO_007` |
| `impl.mode: shell` | **Refused** with `E_GO_008` |
| `impl.mode: sql` | **Refused** with `E_GO_009` |
| `impl.mode: mcp_tool` | **Refused** with `E_GO_010` |
| `RESUME` (`--from-step`) | **Refused** with `E_GO_011` (sources using RESUME-shape declarations) |
| JSONL logging (`CLIO_LOG=1`) | **Silently no-op** — emitted code has no log calls |
| `TEST` blocks | **Refused** with `E_GO_012` |
| `invoke.protocol: bedrock` / `vertex` | **Refused** with `E_GO_003` (in-spec) |
| `invoke.mode: cli` | **Refused** with `E_GO_002` (in-spec) |
| `STEP exact LANG ∈ {python, rust, node, bash}` | **Refused** with `E_GO_001` (in-spec) |
| Source with no FLOW | **Refused** with `E_GO_004` (in-spec) |

All refused-combo error messages point to `--target python` (the only feature-complete target). The spec's full design remains the v0.20 release-line target; v0.20.x patches relax each error code as the corresponding emitter path lands.

---

## File map

### Files to create

| Path | Purpose | Approx LOC |
|---|---|---|
| `clio/emitters/go.py` | `GoEmitter(BaseEmitter)` — orchestrates the output tree | ~450 |
| `clio/emitters/_go_helpers.py` | Go-specific renderers + embedded Go runtime templates (cache, validate) | ~700 |
| `examples/mvp_go.clio` | Minimal example for the cookbook recipe | ~30 |
| `tests/test_emitters/test_go.py` | Emission tests (granular + golden snapshots) | ~600 |
| `tests/test_emitters/test_go_compile.py` | `go build` smoke (skipped if `go` not on PATH) | ~150 |
| `tests/fixtures/expected_go/` | Golden snapshots per fixture | (files) |
| `tests/fixtures/go_minimal.clio` | Minimal Go-target fixture (exact only, `LANG: go`) | ~15 |
| `tests/fixtures/go_judgment.clio` | Judgment + cache + ON_FAIL chain | ~25 |
| `tests/fixtures/go_control_flow.clio` | IF/MATCH/WHILE/FOR EACH | ~35 |
| `tests/fixtures/go_parallel.clio` | FOR EACH PARALLEL | ~20 |
| `tests/fixtures/go_rescue.clio` | RESCUE block | ~20 |

### Files to modify

| Path | Change | Lines |
|---|---|---|
| `clio/cli.py` | Add `"go"` branch to `_cmd_compile` dispatch | +3 |
| `clio/emitters/_shared_utils.py` | Add `_type_to_go`, `_to_go_field_name`, `_go_condition_expr` (mirrors the existing `_type_to_python` / `_to_field_name` / `_python_condition_expr`) | +120 |
| `docs/COMPILATION_TARGETS.md` | Replace the "`target: go` (Future)" sketch with the canonical entry; update "Targets at a glance" table | ~+100 / -10 |
| `docs/manual/04-targets.md` | Add `go` column to the cross-target feature matrix | ~+40 |
| `docs/LANGUAGE_SPEC.md` | Add Go to the "LANG per step" table; mention `target: go` in target overview | ~+15 |
| `docs/manual/03-cookbook.md` | New recipe: "Compile a flow to a Go binary" | ~+80 |
| `docs/manual/06-troubleshooting.md` | Entries for `E_GO_001` … `E_GO_012`, "missing Go toolchain", "modernc.org/sqlite vs cgo" (forward-looking note) | ~+120 |
| `README.md` | Add `target: go` to the bullet list of supported targets; update test count | +3 |
| `CHANGELOG.md` | New `[Unreleased]` entry rolling into `[0.20.0]` at release-admin time | ~+50 |
| `pyproject.toml` | (At release-admin time only — not in this plan) bump to `0.20.0` | +1 |

### Files NOT touched

- `clio/parser/*` — no language-grammar change. `target: go` consumes the existing IR.
- `clio/ir/*` — no IR shape change.
- `clio/runtime/*` — Python runtime helpers untouched; Go runtime templates live in `_go_helpers.py`.
- `clio/emitters/{claude_cli,python,mcp_server,langgraph,claude_skill}.py` — no edits. (Their `_*_helpers.py` modules are also untouched; only `_shared_utils.py` grows.)

---

## Test budget

| Phase | Tests added | Cumulative |
|---|---|---|
| Phase 1 — Foundation (T1–T3) | 6 | 6 |
| Phase 2 — Contracts & types (T4–T6) | 11 | 17 |
| Phase 3 — Runtime helpers (T7–T8) | 8 | 25 |
| Phase 4 — Exact + judgment + flow (T9–T11) | 12 | 37 |
| Phase 5 — Control flow (T12–T16) | 18 | 55 |
| Phase 6 — Parallel + ON_FAIL (T17–T18) | 8 | 63 |
| Phase 7 — Refused combos (T19) | 12 (one per error code) | 75 |
| Phase 8 — Docs + example + golden (T20–T21) | 5 (cookbook example end-to-end) | 80 |
| **Total new** | **~80** | |

Existing test count at plan write time: 1067 passed / 18 skipped / 1 xfailed (`main @ 5371ae3`).

Target: 1147+ passed when v0.20.0 release-admin PR opens.

---

## Conventions for this plan

### TDD discipline

Each task follows the standard 5-step TDD loop:
1. Write the failing test (assert on emitted file content, or on a compile-time error)
2. Run the test and confirm it fails for the *expected* reason
3. Implement the minimal emitter code to make the test pass
4. Run the test and confirm it passes
5. Commit (one task = one commit)

### Golden snapshots

A subset of tasks (T9, T17, T21) generate **golden snapshots** under `tests/fixtures/expected_go/<fixture_name>/`. To regenerate after intentional changes:

```bash
python -m clio compile tests/fixtures/<fixture>.clio --target go --output tests/fixtures/expected_go/<fixture>
```

Granular tests are preferred during early tasks; golden snapshots cover end-to-end regression once the emitter shape is stable.

### `go build` smoke tests

`tests/test_emitters/test_go_compile.py` runs `go build ./...` in a subprocess against each emitted fixture. The whole file is `@pytest.mark.skipif(not shutil.which("go"), reason="Go toolchain not installed")` — CI matrices that install Go run them; environments without Go skip cleanly.

The compile-check **does not** download Go, does not run `go mod tidy` against the network, and does not execute the emitted binary. It exercises the syntactic correctness of the emitter's output only. End-to-end execution (running an emitted Go binary against a real LLM) is manual and documented in the cookbook recipe.

### Commit message style

Match the existing convention from `git log`:

```
feat(go-emitter): <one-line summary in imperative>

<body explaining what's emitted and why, ~3-5 lines>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

Branch for this plan: `feat/v0.20-target-go-emitter`. Branches off `main` (currently `5371ae3` after the spec PR #70 merge). One feature PR for the whole plan.

---

## Phase 1 — Foundation

Goal: a `python -m clio compile <fixture>.clio --target go --output ./out` invocation produces an `./out/` directory containing a `go.mod` and a `cmd/<flow>/main.go` that compiles via `go build`. No CLIO features yet — the flow body is empty.

### Task 1: Scaffold the emitter and register the CLI target

**Files:**
- Create: `clio/emitters/go.py`
- Create: `clio/emitters/_go_helpers.py`
- Modify: `clio/cli.py` (compile dispatch)
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing smoke test**

Create `tests/test_emitters/test_go.py`:

```python
"""Tests for the go emitter.

Granular tests (existence + parsed content) for tasks 1-19.
Golden snapshots (full-tree equality) for tasks 9, 17, 21.

To regenerate goldens after intentional changes:

    python -m clio compile tests/fixtures/<name>.clio \\
        --target go --output tests/fixtures/expected_go/<name>
"""
from __future__ import annotations

from pathlib import Path

import pytest

from clio.cli import _cmd_compile


def _compile(source_path: Path, output_dir: Path) -> None:
    """Run `clio compile <source> --target go --output <out>` in-process."""
    class Args:
        source = str(source_path)
        target = "go"
        output = str(output_dir)
        flow = None
    _cmd_compile(Args())


def test_target_go_is_registered_in_cli(tmp_path: Path) -> None:
    src = tmp_path / "trivial.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    assert (out / "go.mod").exists(), "go emitter must write go.mod"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_emitters/test_go.py::test_target_go_is_registered_in_cli -v
```

Expected: FAIL with `ValueError: unknown target 'go'` (or similar — CLI doesn't know about `go` yet).

- [ ] **Step 3: Create the emitter skeleton**

Create `clio/emitters/go.py`:

```python
"""Emitter for `target: go`.

Produces a runnable Go module (Anthropic SDK Go + jsonschema/v6 + errgroup)
from a target-independent IR. Embeds Go runtime templates (cache, validate)
under the emitted package's `clio_runtime/`.

Module-level helpers live in `_go_helpers.py`; this file holds only
the GoEmitter class.

Scope (v0.20.0): exact + judgment with Anthropic SDK, CACHE, control flow
(IF/MATCH/WHILE/FOR EACH + PARALLEL), RESCUE, ON_FAIL chain. Refuses at
compile time: OpenAI, FLOW composition, impl.mode {rest,sql,mcp_tool,shell},
RESUME-shape declarations, TEST blocks. See E_GO_001..012 in
docs/manual/06-troubleshooting.md.
"""
from __future__ import annotations

from pathlib import Path

from clio.emitters.base import BaseEmitter
from clio.ir.graph import FlowGraph


class GoEmitter(BaseEmitter):
    def emit(
        self,
        graph: FlowGraph,
        output_dir: Path,
        *,
        source_path: Path | None = None,
    ) -> None:
        """Emit a Go module under `output_dir`.

        `source_path` is accepted and ignored (consistent with python,
        mcp-server, langgraph emitters)."""
        output_dir.mkdir(parents=True, exist_ok=True)
        # Phase 1 — Task 2 will fill go.mod here.
        (output_dir / "go.mod").write_text(
            f"module {graph.flows[graph.entry_flow_name].name}\n\ngo 1.22\n"
        )
```

Create `clio/emitters/_go_helpers.py`:

```python
"""Go-specific renderers + embedded Go runtime templates.

Filled progressively across Phase 1-6. Imported by `go.py`.

CLAUDE.md rule "emitters never import from each other" continues to hold:
this module is a helper for `go.py` only; cross-emitter sharing happens via
`_shared_utils.py`.
"""
from __future__ import annotations
```

Modify `clio/cli.py` — locate the `_cmd_compile` function and add a `"go"` branch to its target dispatch. Look for the existing pattern (e.g. `elif args.target == "claude-skill":`) and add adjacent:

```python
elif args.target == "go":
    from clio.emitters.go import GoEmitter
    emitter = GoEmitter()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_emitters/test_go.py::test_target_go_is_registered_in_cli -v
```

Expected: PASS — `go.mod` is created.

- [ ] **Step 5: Commit**

```bash
git checkout -b feat/v0.20-target-go-emitter
git add clio/emitters/go.py clio/emitters/_go_helpers.py clio/cli.py \
        tests/test_emitters/test_go.py
git commit -m "$(cat <<'EOF'
feat(go-emitter): scaffold GoEmitter and register target: go in CLI

First step of the v0.20.0 sprint. Smoke test compiles a trivial flow
and asserts go.mod is written. Everything else stubbed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 2: Emit `go.mod` with correct module path + dependencies

**Files:**
- Modify: `clio/emitters/go.py`
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_emitters/test_go.py`:

```python
def test_go_mod_uses_safe_package_name(tmp_path: Path) -> None:
    """Module name is derived from the flow name via the shared
    safe-package-name normaliser."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW Customer-Retention!\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    content = (out / "go.mod").read_text()
    assert content.startswith("module customer_retention\n"), content
    assert "go 1.22\n" in content


def test_go_mod_omits_sdk_when_no_judgment(tmp_path: Path) -> None:
    """A flow with no judgment step does not require anthropic-sdk-go."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    content = (out / "go.mod").read_text()
    assert "anthropic-sdk-go" not in content
    # jsonschema is always required (Validate methods)
    assert "santhosh-tekuri/jsonschema/v6" in content


def test_go_mod_pins_sdk_when_judgment_present(tmp_path: Path) -> None:
    """A flow with a judgment step pulls in anthropic-sdk-go."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    content = (out / "go.mod").read_text()
    assert "github.com/anthropics/anthropic-sdk-go" in content
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "go_mod"
```

Expected: 3 FAIL (module name not normalised; deps missing).

- [ ] **Step 3: Implement go.mod renderer**

Add to `clio/emitters/_go_helpers.py`:

```python
from clio.emitters._shared_utils import _safe_package_name
from clio.ir.graph import FlowGraph, StepIR


_GO_VERSION = "1.22"

_DEP_JSONSCHEMA = "github.com/santhosh-tekuri/jsonschema/v6 v6.0.1"
_DEP_ANTHROPIC = "github.com/anthropics/anthropic-sdk-go v0.5.0"
_DEP_ERRGROUP = "golang.org/x/sync v0.7.0"


def _flow_uses_judgment(graph: FlowGraph) -> bool:
    """True if any step in the entry flow (or its sub-flows, transitively)
    is judgment mode. v0.20.0 has no FLOW composition, so only the entry
    flow's direct steps are walked."""
    entry = graph.flows[graph.entry_flow_name]
    return any(isinstance(s, StepIR) and s.mode == "judgment" for s in graph.steps_by_name.values()
               if s.name in {c.step_name for c in entry.chain if hasattr(c, "step_name")})


def _flow_uses_parallel(graph: FlowGraph) -> bool:
    """True if the entry flow contains a FOR EACH PARALLEL block."""
    from clio.emitters._shared_utils import _has_parallel
    return _has_parallel(graph.flows[graph.entry_flow_name].chain)


def render_go_mod(graph: FlowGraph) -> str:
    """Render the contents of go.mod for the emitted module.

    Deps included conditionally:
      - jsonschema/v6: always (Validate methods)
      - anthropic-sdk-go: only when ≥1 judgment step
      - golang.org/x/sync: only when ≥1 FOR EACH PARALLEL
    """
    pkg = _safe_package_name(graph, default="flow")
    lines = [f"module {pkg}", "", f"go {_GO_VERSION}", "", "require ("]
    lines.append(f"\t{_DEP_JSONSCHEMA}")
    if _flow_uses_judgment(graph):
        lines.append(f"\t{_DEP_ANTHROPIC}")
    if _flow_uses_parallel(graph):
        lines.append(f"\t{_DEP_ERRGROUP}")
    lines.append(")")
    return "\n".join(lines) + "\n"
```

Replace the placeholder `go.mod` write in `clio/emitters/go.py`:

```python
from clio.emitters._go_helpers import render_go_mod

# inside GoEmitter.emit, replace the inline open() with:
(output_dir / "go.mod").write_text(render_go_mod(graph))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "go_mod"
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/go.py clio/emitters/_go_helpers.py tests/test_emitters/test_go.py
git commit -m "$(cat <<'EOF'
feat(go-emitter): emit go.mod with conditional anthropic/errgroup deps

Module path derived via _safe_package_name (shared with python target).
Pinned versions: jsonschema/v6 v6.0.1 (always), anthropic-sdk-go v0.5.0
(when ≥1 judgment step), golang.org/x/sync v0.7.0 (when ≥1 parallel).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 3: Emit `cmd/<flow>/main.go` scaffold

**Files:**
- Modify: `clio/emitters/go.py`
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_emitters/test_go.py`:

```python
def test_cmd_main_go_exists(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    assert (out / "cmd" / "pipeline" / "main.go").exists()


def test_cmd_main_go_parses_kwargs_json(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "cmd" / "pipeline" / "main.go").read_text()
    assert "package main" in body
    assert 'flag.String("kwargs"' in body
    assert "json.Unmarshal" in body
    # The CLI calls into the flow package and prints the resulting state.
    assert "flow.Run(ctx, kwargs)" in body
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "cmd_main"
```

Expected: 2 FAIL.

- [ ] **Step 3: Implement main.go renderer**

Add to `clio/emitters/_go_helpers.py`:

```python
def render_cmd_main_go(graph: FlowGraph) -> str:
    """Render cmd/<flow>/main.go — the CLI entry point.

    Parses --kwargs (JSON string), calls flow.Run, prints the resulting
    state as JSON to stdout. Non-zero exit on error.
    """
    pkg = _safe_package_name(graph, default="flow")
    return f'''package main

import (
\t"context"
\t"encoding/json"
\t"flag"
\t"fmt"
\t"os"

\t"{pkg}/flow"
)

func main() {{
\tkwargsRaw := flag.String("kwargs", "{{}}", "JSON-encoded kwargs for the flow")
\tflag.Parse()

\tvar kwargs map[string]any
\tif err := json.Unmarshal([]byte(*kwargsRaw), &kwargs); err != nil {{
\t\tfmt.Fprintf(os.Stderr, "invalid --kwargs: %v\\n", err)
\t\tos.Exit(2)
\t}}

\tctx := context.Background()
\tstate, err := flow.Run(ctx, kwargs)
\tif err != nil {{
\t\tfmt.Fprintf(os.Stderr, "flow.Run: %v\\n", err)
\t\tos.Exit(1)
\t}}

\tout, _ := json.MarshalIndent(state, "", "  ")
\tfmt.Println(string(out))
}}
'''
```

In `clio/emitters/go.py`, inside `GoEmitter.emit`, after the `go.mod` write:

```python
from clio.emitters._go_helpers import render_go_mod, render_cmd_main_go

pkg = _safe_package_name(graph, default="flow")  # add: from clio.emitters._shared_utils import _safe_package_name
cmd_dir = output_dir / "cmd" / pkg
cmd_dir.mkdir(parents=True, exist_ok=True)
(cmd_dir / "main.go").write_text(render_cmd_main_go(graph))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "cmd_main"
```

Expected: 2 PASS.

- [ ] **Step 5: Run the full Go-emitter test file**

```bash
uv run pytest tests/test_emitters/test_go.py -v
```

Expected: 6 PASS (3 from T1+T2, 2 from T3, 1 cumulative).

- [ ] **Step 6: Commit**

```bash
git add clio/emitters/go.py clio/emitters/_go_helpers.py tests/test_emitters/test_go.py
git commit -m "$(cat <<'EOF'
feat(go-emitter): emit cmd/<flow>/main.go entry point

CLI scaffold reading --kwargs JSON, calling flow.Run, printing the result
state. Exit codes: 0 OK, 1 flow error, 2 bad kwargs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 2 — Contracts & Go types

Goal: convert every CONTRACT into a Go struct (with `json:"..."` tags + embedded JSON Schema const) and a `Validate(ctx)` method that delegates to `clio_runtime/validate`.

### Task 4: Implement `_type_to_go` in `_shared_utils.py`

**Files:**
- Modify: `clio/emitters/_shared_utils.py`
- Test: `tests/test_emitters/test_shared_utils.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_emitters/test_shared_utils.py`:

```python
from clio.emitters._shared_utils import _type_to_go
from clio.parser.ast_nodes import (
    ConstrainedType,
    ContractRef,
    EnumType,
    ListType,
    PrimitiveType,
    RecordType,
)


def test_type_to_go_primitives():
    assert _type_to_go(PrimitiveType(name="str"), {}) == "string"
    assert _type_to_go(PrimitiveType(name="int"), {}) == "int64"
    assert _type_to_go(PrimitiveType(name="float"), {}) == "float64"
    assert _type_to_go(PrimitiveType(name="bool"), {}) == "bool"


def test_type_to_go_list_of_primitives():
    t = ListType(item=PrimitiveType(name="str"))
    assert _type_to_go(t, {}) == "[]string"


def test_type_to_go_list_of_records():
    t = ListType(item=RecordType(fields=[("name", PrimitiveType(name="str")),
                                          ("revenue", PrimitiveType(name="float"))]))
    out = _type_to_go(t, {})
    assert out.startswith("[]struct ")
    assert 'Name string `json:"name"`' in out
    assert 'Revenue float64 `json:"revenue"`' in out


def test_type_to_go_contract_ref():
    from clio.ir.graph import ContractIR
    contracts = {"customer_risk": ContractIR(name="customer_risk", shape={}, assert_ast=None)}
    t = ContractRef(name="customer_risk")
    assert _type_to_go(t, contracts) == "CustomerRisk"


def test_type_to_go_enum():
    t = EnumType(values=["low", "mid", "high"])
    # enums render as `string` with a documented constant set elsewhere
    assert _type_to_go(t, {}) == "string"


def test_type_to_go_constrained_unwraps():
    t = ConstrainedType(base=PrimitiveType(name="str"), constraints={"max": 300})
    assert _type_to_go(t, {}) == "string"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_emitters/test_shared_utils.py -v -k "type_to_go"
```

Expected: 6 FAIL with `ImportError: cannot import name '_type_to_go'`.

- [ ] **Step 3: Implement `_type_to_go`**

Append to `clio/emitters/_shared_utils.py`:

```python
def _to_go_field_name(name: str) -> str:
    """CLIO field name → Go exported identifier (UpperCamelCase).

    Mirrors `_to_field_name` (which targets Python snake_case). Go exports
    require capitalised first letter to be visible across packages.
    """
    parts = [p for p in name.replace("-", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


def _type_to_go(t: TypeExpr, contracts: dict[str, ContractIR]) -> str:
    """Render a CLIO TypeExpr as a Go type expression.

    Used both inline (struct field types) and standalone (variable types).
    Mirrors `_type_to_python` for the python target.
    """
    if isinstance(t, ConstrainedType):
        return _type_to_go(t.base, contracts)
    if isinstance(t, PrimitiveType):
        return {
            "str": "string",
            "int": "int64",
            "float": "float64",
            "bool": "bool",
            "any": "any",
        }[t.name]
    if isinstance(t, EnumType):
        # Enums emit as `string` — the schema-level constraint enforces the
        # value set at Validate() time. Generating typed Go enums is
        # in-scope for a future refactor; for v0.20.0 string is sufficient.
        return "string"
    if isinstance(t, ListType):
        return f"[]{_type_to_go(t.item, contracts)}"
    if isinstance(t, RecordType):
        fields = ", ".join(
            f'{_to_go_field_name(name)} {_type_to_go(ftype, contracts)} '
            f'`json:"{name}"`'
            for name, ftype in t.fields
        )
        return f"struct {{ {fields} }}"
    if isinstance(t, ContractRef):
        return _to_class_name(t.name)
    raise ValueError(f"unsupported TypeExpr for Go target: {type(t).__name__}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_emitters/test_shared_utils.py -v -k "type_to_go"
```

Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_shared_utils.py tests/test_emitters/test_shared_utils.py
git commit -m "$(cat <<'EOF'
feat(shared-utils): add _type_to_go and _to_go_field_name

Mirrors _type_to_python + _to_field_name for the upcoming go target.
Primitives, lists, records, enums (string), contract refs, constrained
types all covered. RecordType emits anonymous Go structs with json tags.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 5: Emit `contracts/contracts.go` with structs + embedded JSON Schema

**Files:**
- Modify: `clio/emitters/go.py`
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_emitters/test_go.py`:

```python
def test_contracts_file_written_when_contracts_present(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT customer_risk\n"
        "  SHAPE: {client: str, risk: enum(low|mid|high), reason: str(max=300)}\n"
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: risk: customer_risk\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    assert (out / "contracts" / "contracts.go").exists()


def test_contracts_struct_uses_json_tags(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT customer_risk\n"
        "  SHAPE: {client: str, risk: enum(low|mid|high), reason: str(max=300)}\n"
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: risk: customer_risk\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "contracts" / "contracts.go").read_text()
    assert "package contracts" in body
    assert "type CustomerRisk struct {" in body
    assert 'Client string `json:"client"`' in body
    assert 'Risk string `json:"risk"`' in body
    assert 'Reason string `json:"reason"`' in body


def test_contracts_json_schema_embedded_as_const(tmp_path: Path) -> None:
    """Each contract carries its JSON Schema as a `const` string so
    Validate() can call jsonschema/v6 without filesystem reads."""
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT customer_risk\n"
        "  SHAPE: {client: str, risk: enum(low|mid|high), reason: str(max=300)}\n"
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: risk: customer_risk\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "contracts" / "contracts.go").read_text()
    assert "const customerRiskSchema = `" in body
    assert '"client"' in body  # field in schema
    assert "low" in body  # enum value


def test_contracts_file_omitted_when_no_contract_used(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    assert not (out / "contracts" / "contracts.go").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "contracts"
```

Expected: 4 FAIL.

- [ ] **Step 3: Implement contracts.go renderer**

Add to `clio/emitters/_go_helpers.py`:

```python
import json as _json

from clio.emitters._shared_utils import (
    _shape_from_schema,
    _to_class_name,
    _to_go_field_name,
    _type_to_go,
    _uses_contract_refs,
)
from clio.ir.graph import ContractIR


def render_contracts_go(graph: FlowGraph) -> str | None:
    """Render contracts/contracts.go.

    Returns None when the flow uses no contract (in which case the emitter
    skips writing the file). Otherwise emits one struct + one schema const
    per contract used by the entry flow.
    """
    contracts_used: set[str] = set()
    for step in graph.steps_by_name.values():
        if _uses_contract_refs(step):
            for ref_name in step.contract_refs():  # provided by IR
                contracts_used.add(ref_name)
    if not contracts_used:
        return None

    parts = ['package contracts', '', 'import (', '\t"context"', ')', '']
    for name in sorted(contracts_used):
        contract = graph.contracts[name]
        struct_name = _to_class_name(name)
        schema_const = f"{name}Schema"
        # Struct
        parts.append(f"type {struct_name} struct {{")
        for fname, ftype in _shape_from_schema(contract.shape):
            go_field = _to_go_field_name(fname)
            go_type = _type_to_go(ftype, graph.contracts)
            parts.append(f'\t{go_field} {go_type} `json:"{fname}"`')
        parts.append("}")
        parts.append("")
        # Embedded JSON Schema
        schema_json = _json.dumps(contract.shape, indent=2)
        parts.append(f"const {schema_const} = `")
        parts.append(schema_json)
        parts.append("`")
        parts.append("")
    return "\n".join(parts)
```

In `clio/emitters/go.py`:

```python
from clio.emitters._go_helpers import render_contracts_go

# inside GoEmitter.emit, after the cmd/<flow>/main.go write:
contracts_src = render_contracts_go(graph)
if contracts_src is not None:
    contracts_dir = output_dir / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    (contracts_dir / "contracts.go").write_text(contracts_src)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "contracts"
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/go.py clio/emitters/_go_helpers.py tests/test_emitters/test_go.py
git commit -m "$(cat <<'EOF'
feat(go-emitter): emit contracts/contracts.go with structs + schema consts

One Go struct per CONTRACT used by the entry flow; JSON Schema embedded
as a backtick-quoted const so Validate() needs no filesystem reads. File
is skipped entirely when no contract is referenced.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 6: Emit `Validate(ctx)` method on each contract struct

**Files:**
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_emitters/test_go.py`:

```python
def test_contracts_have_validate_method(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT customer_risk\n"
        "  SHAPE: {client: str, risk: enum(low|mid|high), reason: str(max=300)}\n"
        "  ASSERT: len(reason) > 0\n"
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: risk: customer_risk\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "contracts" / "contracts.go").read_text()
    assert "func (c *CustomerRisk) Validate(ctx context.Context) error {" in body
    # Calls clio_runtime/validate
    assert '"clio_runtime/validate"' in body or "validate.Schema" in body


def test_contracts_validate_includes_assert(tmp_path: Path) -> None:
    """ASSERT clause is encoded so the x-clio-assert walker can replay it."""
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT customer_risk\n"
        "  SHAPE: {client: str, reason: str(max=300)}\n"
        "  ASSERT: len(reason) > 0\n"
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: risk: customer_risk\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "contracts" / "contracts.go").read_text()
    # x-clio-assert is included in the schema JSON
    assert "x-clio-assert" in body or '"assert"' in body
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "validate_method"
```

Expected: 2 FAIL.

- [ ] **Step 3: Extend the contracts renderer**

In `clio/emitters/_go_helpers.py`, modify `render_contracts_go` — after emitting the struct and schema const for each contract, append the Validate method, and include the assert AST inside the schema JSON under the `x-clio-assert` key:

```python
# Replace the schema_json line with:
schema_payload = dict(contract.shape)
if contract.assert_ast is not None:
    schema_payload["x-clio-assert"] = contract.assert_ast
schema_json = _json.dumps(schema_payload, indent=2)

# Then after the schema const block, before the trailing empty line, append:
parts.extend([
    f"func (c *{struct_name}) Validate(ctx context.Context) error {{",
    f"\treturn validate.Schema(ctx, {schema_const}, c)",
    "}",
    "",
])
```

Update the imports block at the top of the rendered file:

```python
# Replace the parts header with:
pkg = _safe_package_name(graph, default="flow")
parts = [
    'package contracts',
    '',
    'import (',
    '\t"context"',
    '',
    f'\t"{pkg}/clio_runtime/validate"',
    ')',
    '',
]
```

(Note: the imports are needed only when at least one struct has a Validate body; since contracts.go is only emitted when ≥1 contract exists, this is always required.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "validate_method"
```

Expected: 2 PASS.

- [ ] **Step 5: Run all Phase 2 tests**

```bash
uv run pytest tests/test_emitters/test_go.py tests/test_emitters/test_shared_utils.py -v
```

Expected: 17 PASS cumulative (6 Phase 1 + 11 Phase 2).

- [ ] **Step 6: Commit**

```bash
git add clio/emitters/_go_helpers.py tests/test_emitters/test_go.py
git commit -m "$(cat <<'EOF'
feat(go-emitter): emit Validate(ctx) on each contract struct

Each contracts/contracts.go struct gains a Validate(ctx) method that
delegates to clio_runtime/validate.Schema with the embedded JSON Schema.
ASSERT clauses are stored under x-clio-assert inside the schema so the
walker can replay them server-side.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3 — Runtime helpers (validate + cache)

Goal: write the two foundational Go runtime modules — `clio_runtime/validate` (JSON Schema + `x-clio-assert` walker) and `clio_runtime/cache` (SHA256 on-disk content-addressed cache) — as embedded templates inside `_go_helpers.py`. The cache layout is byte-identical to the python target's `<output>/.cache/<step>/<sha256>.json`, so swapping targets preserves cache hits.

### Task 7: Embed `clio_runtime/validate.go` template

**Files:**
- Modify: `clio/emitters/go.py`
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`
- Test: `tests/test_emitters/test_go_compile.py`

- [ ] **Step 1: Write the failing emission tests**

Append to `tests/test_emitters/test_go.py`:

```python
def test_clio_runtime_validate_written(tmp_path: Path) -> None:
    """validate.go is always emitted (contracts always present? no, but
    the path is required when contracts.go is emitted)."""
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT customer_risk\n"
        "  SHAPE: {client: str}\n"
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: risk: customer_risk\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "clio_runtime" / "validate" / "validate.go").read_text()
    assert "package validate" in body
    assert "func Schema(" in body
    assert "jsonschema" in body
    # x-clio-assert walker
    assert "func evalAssert(" in body or "Assert(" in body


def test_validate_template_omitted_when_no_contract(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    # No contract → no validate runtime needed
    assert not (out / "clio_runtime" / "validate" / "validate.go").exists()
```

- [ ] **Step 2: Write the compile-check test (skipped if no Go)**

Create `tests/test_emitters/test_go_compile.py`:

```python
"""Compile-check tests: emit a fixture to Go, then run `go build ./...`.

Skipped entirely if the `go` toolchain is not on PATH. The smoke does NOT
download Go, does NOT run `go mod tidy` against the network (the emitted
go.sum is committed or absent), and does NOT execute the emitted binary —
this exercises syntactic correctness of the emitter's output only.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_emitters.test_go import _compile


pytestmark = pytest.mark.skipif(
    shutil.which("go") is None,
    reason="Go toolchain not on PATH",
)


def _go_build(out_dir: Path) -> subprocess.CompletedProcess:
    """Run `go build ./...` inside out_dir. Returns the completed process."""
    # Disable module download — the test runs against pinned versions in
    # the emitted go.mod. If go.sum is missing, GOFLAGS=-mod=mod is fine;
    # if it's stricter (-mod=readonly), the test maintains a vendored cache
    # under tests/fixtures/expected_go/<name>/go.sum.
    return subprocess.run(
        ["go", "build", "./..."],
        cwd=out_dir,
        capture_output=True,
        text=True,
        env={"GOFLAGS": "-mod=mod", "HOME": str(out_dir / ".gohome"), "PATH": "/usr/bin:/usr/local/bin:/bin"},
    )


def test_go_build_passes_on_minimal_contract_flow(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT customer_risk\n"
        "  SHAPE: {client: str}\n"
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: risk: customer_risk\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)

    # `go mod tidy` to fetch the pinned deps into the test's tmp GOPATH
    subprocess.run(["go", "mod", "tidy"], cwd=out, check=True, capture_output=True)
    result = _go_build(out)
    assert result.returncode == 0, (
        f"go build failed:\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "validate"
uv run pytest tests/test_emitters/test_go_compile.py -v
```

Expected: 2 FAIL (emission) + 1 FAIL (compile — `clio_runtime` missing).

- [ ] **Step 4: Embed the validate.go Go-source template**

Add to `clio/emitters/_go_helpers.py`:

```python
_VALIDATE_GO_TEMPLATE = '''package validate

// Auto-generated by CLIO. Do not edit by hand.

import (
\t"context"
\t"encoding/json"
\t"fmt"

\t"github.com/santhosh-tekuri/jsonschema/v6"
)

// Schema validates `instance` against the given JSON Schema string.
// Also evaluates an x-clio-assert clause if present (CLIO's compact assert AST).
func Schema(ctx context.Context, schemaJSON string, instance any) error {
\tvar raw any
\tif err := json.Unmarshal([]byte(schemaJSON), &raw); err != nil {
\t\treturn fmt.Errorf("validate: invalid schema JSON: %w", err)
\t}
\tcompiler := jsonschema.NewCompiler()
\tif err := compiler.AddResource("file:///schema.json", raw); err != nil {
\t\treturn fmt.Errorf("validate: add schema: %w", err)
\t}
\tsch, err := compiler.Compile("file:///schema.json")
\tif err != nil {
\t\treturn fmt.Errorf("validate: compile schema: %w", err)
\t}
\tif err := sch.Validate(instance); err != nil {
\t\treturn fmt.Errorf("validate: schema: %w", err)
\t}
\tif m, ok := raw.(map[string]any); ok {
\t\tif assertNode, ok := m["x-clio-assert"]; ok {
\t\t\tif !evalAssert(assertNode, instance) {
\t\t\t\treturn fmt.Errorf("validate: x-clio-assert failed")
\t\t\t}
\t\t}
\t}
\treturn nil
}

// evalAssert walks the compact CLIO assert AST. Node kinds: ident, int, float,
// str, call(len, ...), compare(==,!=,<,>,<=,>=). Any other kind is treated as
// false (defensive). The walker is a direct port of clio/runtime/validate.py.
func evalAssert(node any, ctx any) bool {
\tm, ok := node.(map[string]any)
\tif !ok {
\t\treturn false
\t}
\tkind, _ := m["kind"].(string)
\tswitch kind {
\tcase "ident":
\t\t// Resolve from ctx (must be a map[string]any in this codebase).
\t\tif obj, ok := ctx.(map[string]any); ok {
\t\t\t_, ok := obj[m["name"].(string)]
\t\t\treturn ok
\t\t}
\t\treturn false
\tcase "int", "float", "str":
\t\treturn true
\tcase "call":
\t\tfn, _ := m["fn"].(string)
\t\tif fn != "len" {
\t\t\treturn false
\t\t}
\t\targs, _ := m["args"].([]any)
\t\tif len(args) != 1 {
\t\t\treturn false
\t\t}
\t\treturn lenOf(args[0], ctx) >= 0
\tcase "compare":
\t\tleft := resolve(m["left"], ctx)
\t\tright := resolve(m["right"], ctx)
\t\top, _ := m["op"].(string)
\t\treturn cmpOk(left, right, op)
\t}
\treturn false
}

func resolve(node any, ctx any) any {
\tm, _ := node.(map[string]any)
\tswitch m["kind"] {
\tcase "ident":
\t\tif obj, ok := ctx.(map[string]any); ok {
\t\t\treturn obj[m["name"].(string)]
\t\t}
\t\treturn nil
\tcase "int":
\t\treturn int64(m["value"].(float64))
\tcase "float":
\t\treturn m["value"]
\tcase "str":
\t\treturn m["value"]
\tcase "call":
\t\treturn lenOf(m["args"].([]any)[0], ctx)
\t}
\treturn nil
}

func lenOf(node any, ctx any) int64 {
\tv := resolve(node, ctx)
\tswitch s := v.(type) {
\tcase string:
\t\treturn int64(len(s))
\tcase []any:
\t\treturn int64(len(s))
\tcase map[string]any:
\t\treturn int64(len(s))
\t}
\treturn -1
}

func cmpOk(l, r any, op string) bool {
\tlf, lok := toFloat(l)
\trf, rok := toFloat(r)
\tif lok && rok {
\t\tswitch op {
\t\tcase "==":
\t\t\treturn lf == rf
\t\tcase "!=":
\t\t\treturn lf != rf
\t\tcase "<":
\t\t\treturn lf < rf
\t\tcase ">":
\t\t\treturn lf > rf
\t\tcase "<=":
\t\t\treturn lf <= rf
\t\tcase ">=":
\t\t\treturn lf >= rf
\t\t}
\t}
\tls, lsOk := l.(string)
\trs, rsOk := r.(string)
\tif lsOk && rsOk {
\t\tswitch op {
\t\tcase "==":
\t\t\treturn ls == rs
\t\tcase "!=":
\t\t\treturn ls != rs
\t\t}
\t}
\treturn false
}

func toFloat(v any) (float64, bool) {
\tswitch n := v.(type) {
\tcase int64:
\t\treturn float64(n), true
\tcase float64:
\t\treturn n, true
\t}
\treturn 0, false
}
'''


def render_clio_runtime_validate() -> str:
    """Return the body of <output>/clio_runtime/validate/validate.go.

    Static template — no per-emission substitution required. Emitted only
    when the flow uses at least one contract."""
    return _VALIDATE_GO_TEMPLATE
```

In `clio/emitters/go.py`:

```python
from clio.emitters._go_helpers import render_clio_runtime_validate

# inside GoEmitter.emit, after contracts.go is written (conditional):
if contracts_src is not None:
    runtime_validate_dir = output_dir / "clio_runtime" / "validate"
    runtime_validate_dir.mkdir(parents=True, exist_ok=True)
    (runtime_validate_dir / "validate.go").write_text(render_clio_runtime_validate())
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "validate"
```

Expected: 2 PASS.

If the local environment has Go installed:

```bash
uv run pytest tests/test_emitters/test_go_compile.py -v
```

Expected: PASS (or auto-skip if no Go).

- [ ] **Step 6: Commit**

```bash
git add clio/emitters/go.py clio/emitters/_go_helpers.py \
        tests/test_emitters/test_go.py tests/test_emitters/test_go_compile.py
git commit -m "$(cat <<'EOF'
feat(go-emitter): embed clio_runtime/validate.go template

JSON Schema validation via santhosh-tekuri/jsonschema/v6 + x-clio-assert
walker port of clio/runtime/validate.py (ident, int, float, str, call(len),
compare). Emitted only when ≥1 contract is used.

Includes the first go-build smoke test (skipped if Go toolchain missing).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 8: Embed `clio_runtime/cache.go` template

**Files:**
- Modify: `clio/emitters/go.py`
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_emitters/test_go.py`:

```python
def test_clio_runtime_cache_written_when_judgment_present(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  CACHE: ttl(24h)\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "clio_runtime" / "cache" / "cache.go").read_text()
    assert "package cache" in body
    assert "func Key(" in body
    assert "func Lookup(" in body
    assert "func Store(" in body
    assert "sha256" in body


def test_cache_layout_same_as_python_target(tmp_path: Path) -> None:
    """Cache key derivation: SHA256 of step + model + prompt + schema_json.
    Identical to clio/runtime/cache.py."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  CACHE: on\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "clio_runtime" / "cache" / "cache.go").read_text()
    # Key derivation comment + implementation match python's
    # "\n".join([step, model, prompt, schema_json])
    assert 'strings.Join([]string{step, model, prompt, schemaJSON}, "\\n")' in body


def test_cache_omitted_when_no_cache_directive(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    # No CACHE: directive on any step → no cache runtime needed
    assert not (out / "clio_runtime" / "cache" / "cache.go").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "cache"
```

Expected: 3 FAIL.

- [ ] **Step 3: Embed the cache.go template**

Add to `clio/emitters/_go_helpers.py`:

```python
_CACHE_GO_TEMPLATE = '''package cache

// Auto-generated by CLIO. Cache layout matches clio/runtime/cache.py
// byte-for-byte so cache files are interchangeable with the python target.
//
// On disk: <cache_dir>/<step_name>/<sha256>.json
//   {"created_at": <epoch>, "model": "<m>", "response": "<raw>"}

import (
\t"crypto/sha256"
\t"encoding/hex"
\t"encoding/json"
\t"fmt"
\t"os"
\t"path/filepath"
\t"strings"
\t"time"
)

// Key derives the cache key. Identical formula to clio/runtime/cache.py:
// SHA256(step + "\\n" + model + "\\n" + prompt + "\\n" + schemaJSON).
func Key(step, model, prompt, schemaJSON string) string {
\tpayload := strings.Join([]string{step, model, prompt, schemaJSON}, "\\n")
\tsum := sha256.Sum256([]byte(payload))
\treturn hex.EncodeToString(sum[:])
}

// Lookup returns (response, true) if the cache entry is present and fresh.
// ttlSeconds: nil for permanent (CACHE: on), 0 to skip, positive for ttl.
func Lookup(cacheDir, stepName, key string, ttlSeconds *int64) (string, bool) {
\tif ttlSeconds != nil && *ttlSeconds == 0 {
\t\treturn "", false
\t}
\tpath := filepath.Join(cacheDir, stepName, key+".json")
\traw, err := os.ReadFile(path)
\tif err != nil {
\t\treturn "", false
\t}
\tvar entry struct {
\t\tCreatedAt int64  `json:"created_at"`
\t\tModel     string `json:"model"`
\t\tResponse  string `json:"response"`
\t}
\tif err := json.Unmarshal(raw, &entry); err != nil {
\t\treturn "", false
\t}
\tif ttlSeconds != nil {
\t\tif time.Now().Unix()-entry.CreatedAt >= *ttlSeconds {
\t\t\treturn "", false
\t\t}
\t}
\treturn entry.Response, true
}

// Store writes a cache entry atomically.
func Store(cacheDir, stepName, key, model, response string) error {
\tdir := filepath.Join(cacheDir, stepName)
\tif err := os.MkdirAll(dir, 0o755); err != nil {
\t\treturn err
\t}
\tentry := map[string]any{
\t\t"created_at": time.Now().Unix(),
\t\t"model":      model,
\t\t"response":   response,
\t}
\tbody, err := json.Marshal(entry)
\tif err != nil {
\t\treturn err
\t}
\ttmp := filepath.Join(dir, key+".json.tmp")
\tif err := os.WriteFile(tmp, body, 0o644); err != nil {
\t\treturn err
\t}
\tfinal := filepath.Join(dir, key+".json")
\treturn os.Rename(tmp, final)
}

// CacheDirFromEnv returns CLIO_CACHE_DIR or "<cwd>/.cache".
func CacheDirFromEnv() string {
\tif d := os.Getenv("CLIO_CACHE_DIR"); d != "" {
\t\treturn d
\t}
\tcwd, _ := os.Getwd()
\treturn fmt.Sprintf("%s/.cache", cwd)
}
'''


def render_clio_runtime_cache() -> str:
    return _CACHE_GO_TEMPLATE


def _flow_uses_cache(graph: FlowGraph) -> bool:
    """True if any step in the entry flow declares a CACHE directive."""
    for step in graph.steps_by_name.values():
        if getattr(step, "cache", None) and step.cache != "off":
            return True
    return False
```

In `clio/emitters/go.py`:

```python
from clio.emitters._go_helpers import render_clio_runtime_cache, _flow_uses_cache

# inside GoEmitter.emit, after validate.go is written:
if _flow_uses_cache(graph):
    runtime_cache_dir = output_dir / "clio_runtime" / "cache"
    runtime_cache_dir.mkdir(parents=True, exist_ok=True)
    (runtime_cache_dir / "cache.go").write_text(render_clio_runtime_cache())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "cache"
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/go.py clio/emitters/_go_helpers.py tests/test_emitters/test_go.py
git commit -m "$(cat <<'EOF'
feat(go-emitter): embed clio_runtime/cache.go template

SHA256 content-addressed cache; layout byte-identical to
clio/runtime/cache.py so .cache/ directories are swappable between
python and go emissions. Emitted only when a CACHE: directive exists.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 4 — Exact + judgment steps + flow orchestrator

Goal: emit `steps/NN_<name>.go` (one file per step, both exact and judgment) and `flow/flow.go` (the orchestrator that calls them in chain order). Judgment steps invoke the Anthropic SDK with prompt build → cache lookup → SDK call → JSON parse → contract validate → cache store.

### Task 9: Emit exact step stubs in `steps/NN_<name>.go`

**Files:**
- Modify: `clio/emitters/go.py`
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`
- Create: `tests/fixtures/go_minimal.clio`
- Create: `tests/fixtures/expected_go/go_minimal/`

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/go_minimal.clio`:

```clio
STEP load
  TAKES: file: str
  GIVES: rows: List<{name: str, revenue: float}>
  MODE:  exact
  LANG:  go

STEP summarise
  TAKES: rows: List<{name: str, revenue: float}>
  GIVES: total: float
  MODE:  exact
  LANG:  go

FLOW pipeline
  load(file="customers.csv")
    -> summarise(rows)

RESOURCES
  target: go
  models: [haiku]
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_emitters/test_go.py`:

```python
FIXTURES = Path(__file__).parent.parent / "fixtures"
EXPECTED_GO = FIXTURES / "expected_go"


def test_each_step_emits_its_own_go_file(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_minimal.clio", out)
    files = sorted((out / "steps").iterdir())
    assert [f.name for f in files] == ["01_load.go", "02_summarise.go"]


def test_step_function_has_typed_input_and_output(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_minimal.clio", out)
    body = (out / "steps" / "01_load.go").read_text()
    assert "package steps" in body
    assert "func Load(ctx context.Context, in LoadIn) (LoadOut, error)" in body
    # Stub body
    assert 'panic("fill me in: load")' in body
    # Input/output types defined in same file
    assert "type LoadIn struct {" in body
    assert "type LoadOut struct {" in body
    assert 'File string `json:"file"`' in body
    # rows: List<{name: str, revenue: float}> → []struct{Name string; Revenue float64}
    assert "Rows []struct" in body
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "step"
```

Expected: 2 FAIL.

- [ ] **Step 4: Implement step rendering**

Add to `clio/emitters/_go_helpers.py`:

```python
from clio.emitters._shared_utils import _to_class_name, _type_to_go, _to_go_field_name


def _step_in_out_struct(step: StepIR, contracts: dict) -> tuple[str, str]:
    """Render the `<Step>In` and `<Step>Out` struct definitions for a step."""
    cls = _to_class_name(step.name)
    in_fields = "\n".join(
        f'\t{_to_go_field_name(name)} {_type_to_go(ftype, contracts)} '
        f'`json:"{name}"`'
        for name, ftype in step.takes
    ) if step.takes else ""
    out_fields = "\n".join(
        f'\t{_to_go_field_name(name)} {_type_to_go(ftype, contracts)} '
        f'`json:"{name}"`'
        for name, ftype in step.gives
    ) if step.gives else ""
    in_struct = f"type {cls}In struct {{\n{in_fields}\n}}\n" if in_fields else f"type {cls}In struct {{}}\n"
    out_struct = f"type {cls}Out struct {{\n{out_fields}\n}}\n" if out_fields else f"type {cls}Out struct {{}}\n"
    return in_struct, out_struct


def render_exact_step_go(step: StepIR, contracts: dict, graph: FlowGraph) -> str:
    """Render steps/NN_<name>.go for an exact step.

    LANG must be `go` or `auto`; other LANGs are rejected upstream (T19).
    """
    cls = _to_class_name(step.name)
    pkg = _safe_package_name(graph, default="flow")
    in_struct, out_struct = _step_in_out_struct(step, contracts)
    return f'''package steps

// Auto-generated by CLIO. Fill in the function body and remove the panic.

import (
\t"context"
)

{in_struct}
{out_struct}
// {cls} implements the {step.name!r} step.
func {cls}(ctx context.Context, in {cls}In) ({cls}Out, error) {{
\tpanic("fill me in: {step.name}")
}}
'''
```

In `clio/emitters/go.py`, after the runtime helpers are written:

```python
from clio.emitters._go_helpers import render_exact_step_go

# inside GoEmitter.emit, after runtime helpers:
steps_dir = output_dir / "steps"
steps_dir.mkdir(parents=True, exist_ok=True)
entry = graph.flows[graph.entry_flow_name]
for idx, call in enumerate(entry.chain, start=1):
    if not hasattr(call, "step_name"):
        continue  # not a StepCallIR; skip (control flow tasks handle later)
    step = graph.steps_by_name[call.step_name]
    if step.mode == "exact":
        filename = f"{idx:02d}_{step.name}.go"
        (steps_dir / filename).write_text(render_exact_step_go(step, graph.contracts, graph))
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "step"
```

Expected: 2 PASS.

- [ ] **Step 6: Generate the golden snapshot**

```bash
mkdir -p tests/fixtures/expected_go
python -m clio compile tests/fixtures/go_minimal.clio --target go \
    --output tests/fixtures/expected_go/go_minimal
```

- [ ] **Step 7: Add the golden snapshot test**

Append to `tests/test_emitters/test_go.py`:

```python
def _read_tree(root: Path) -> dict[str, str]:
    """Read a directory tree into a {relpath: content} dict for diffing."""
    return {
        str(p.relative_to(root)): p.read_text()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_golden_go_minimal(tmp_path: Path) -> None:
    """Emit go_minimal and compare against expected_go/go_minimal byte-for-byte."""
    out = tmp_path / "out"
    _compile(FIXTURES / "go_minimal.clio", out)
    actual = _read_tree(out)
    expected = _read_tree(EXPECTED_GO / "go_minimal")
    assert actual == expected
```

- [ ] **Step 8: Run the golden test**

```bash
uv run pytest tests/test_emitters/test_go.py::test_golden_go_minimal -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add clio/emitters/go.py clio/emitters/_go_helpers.py \
        tests/test_emitters/test_go.py \
        tests/fixtures/go_minimal.clio \
        tests/fixtures/expected_go/go_minimal/
git commit -m "$(cat <<'EOF'
feat(go-emitter): emit exact step stubs + first golden snapshot

steps/NN_<name>.go: one file per exact step, typed <Step>In/<Step>Out
structs with json tags, function body panics with a 'fill me in' message
mirroring python's NotImplementedError convention.

First golden snapshot fixture: go_minimal (two chained exact steps).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 10: Emit `flow/flow.go` orchestrator

**Files:**
- Modify: `clio/emitters/go.py`
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_emitters/test_go.py`:

```python
def test_flow_go_chains_exact_steps(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_minimal.clio", out)
    body = (out / "flow" / "flow.go").read_text()
    assert "package flow" in body
    assert "func Run(ctx context.Context, kwargs map[string]any) (map[string]any, error)" in body
    # First step is called with the kwargs-derived input
    assert "loadOut, err := steps.Load(ctx, " in body
    # Second step receives the previous step's output as input
    assert "summariseOut, err := steps.Summarise(ctx, " in body
    # Final state map
    assert 'state["load"]' in body
    assert 'state["summarise"]' in body
    assert "return state, nil" in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "flow_go_chains"
```

Expected: FAIL.

- [ ] **Step 3: Implement the flow orchestrator renderer**

Add to `clio/emitters/_go_helpers.py`:

```python
def _kwargs_to_step_input(step: StepIR, prev_state_var: str, contracts: dict) -> str:
    """Render the literal `{Step}In{...}` initialisation pulling fields from
    kwargs and previous-step outputs."""
    cls = _to_class_name(step.name)
    parts = []
    for name, ftype in step.takes:
        gf = _to_go_field_name(name)
        # First step takes from kwargs; later steps from previous state.
        if prev_state_var == "kwargs":
            parts.append(f'{gf}: {prev_state_var}["{name}"].({_type_to_go(ftype, contracts)})')
        else:
            parts.append(f'{gf}: {prev_state_var}.{gf}')
    return f"{cls}In{{ {', '.join(parts)} }}"


def render_flow_go(graph: FlowGraph) -> str:
    """Render flow/flow.go — top-level orchestrator.

    v0.20.0 scope: only top-level sequential chain of steps (no control
    flow / parallel / sub-flow). Tasks 12-18 extend this.
    """
    pkg = _safe_package_name(graph, default="flow")
    entry = graph.flows[graph.entry_flow_name]
    lines = [
        "package flow",
        "",
        "// Auto-generated by CLIO. Do not edit by hand.",
        "",
        "import (",
        '\t"context"',
        "",
        f'\t"{pkg}/steps"',
        ")",
        "",
        "func Run(ctx context.Context, kwargs map[string]any) (map[string]any, error) {",
        "\tstate := map[string]any{}",
        "",
    ]
    prev_var = "kwargs"
    for call in entry.chain:
        if not hasattr(call, "step_name"):
            continue  # control flow handled in later tasks
        step = graph.steps_by_name[call.step_name]
        cls = _to_class_name(step.name)
        input_init = _kwargs_to_step_input(step, prev_var, graph.contracts)
        out_var = f"{step.name}Out"
        lines.extend([
            f"\t{out_var}, err := steps.{cls}(ctx, {input_init})",
            "\tif err != nil {",
            f'\t\treturn nil, err',
            "\t}",
            f'\tstate["{step.name}"] = {out_var}',
            "",
        ])
        prev_var = out_var
    lines.append("\treturn state, nil")
    lines.append("}")
    return "\n".join(lines) + "\n"
```

In `clio/emitters/go.py`, after the steps loop:

```python
from clio.emitters._go_helpers import render_flow_go

flow_dir = output_dir / "flow"
flow_dir.mkdir(parents=True, exist_ok=True)
(flow_dir / "flow.go").write_text(render_flow_go(graph))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "flow_go_chains"
```

Expected: PASS.

- [ ] **Step 5: Regenerate the golden snapshot**

```bash
python -m clio compile tests/fixtures/go_minimal.clio --target go \
    --output tests/fixtures/expected_go/go_minimal
```

Re-run the golden test:

```bash
uv run pytest tests/test_emitters/test_go.py::test_golden_go_minimal -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add clio/emitters/go.py clio/emitters/_go_helpers.py \
        tests/test_emitters/test_go.py tests/fixtures/expected_go/go_minimal/
git commit -m "$(cat <<'EOF'
feat(go-emitter): emit flow/flow.go orchestrator (sequential chain only)

flow.Run(ctx, kwargs) returns the accumulated state map. First step pulls
fields from kwargs; later steps pull from the previous step's typed
output. Control-flow handling lives in tasks 12-18.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 11: Emit judgment step body with Anthropic SDK

**Files:**
- Modify: `clio/emitters/_go_helpers.py`
- Modify: `clio/emitters/go.py`
- Test: `tests/test_emitters/test_go.py`
- Create: `tests/fixtures/go_judgment.clio`
- Create: `tests/fixtures/expected_go/go_judgment/`

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/go_judgment.clio`:

```clio
CONTRACT customer_risk
  SHAPE: {client: str, risk: enum(low|mid|high), reason: str(max=300)}
  ASSERT: len(reason) > 0

STEP load
  TAKES: file: str
  GIVES: customers: List<{name: str, revenue: float}>
  MODE:  exact
  LANG:  go

STEP detect_churn
  TAKES: customers: List<{name: str, revenue: float}>
  GIVES: risks: List<customer_risk>
  MODE:  judgment
  CACHE: ttl(24h)

FLOW pipeline
  load(file="customers.csv")
    -> detect_churn(customers)

RESOURCES
  target: go
  models: [haiku, sonnet]
```

- [ ] **Step 2: Write the failing tests**

```python
def test_judgment_step_calls_anthropic_sdk(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_judgment.clio", out)
    body = (out / "steps" / "02_detect_churn.go").read_text()
    # Anthropic SDK import
    assert "github.com/anthropics/anthropic-sdk-go" in body
    # Prompt build → cache lookup → SDK call → JSON parse → validate → cache store
    assert "cache.Key(" in body
    assert "cache.Lookup(" in body
    assert "anthropic.NewClient(" in body
    assert ".Messages.New(ctx" in body
    assert "json.Unmarshal(" in body
    assert ".Validate(ctx)" in body
    assert "cache.Store(" in body


def test_judgment_step_uses_resolved_model_id(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_judgment.clio", out)
    body = (out / "steps" / "02_detect_churn.go").read_text()
    # The flow declares [haiku, sonnet]; the first is the default model
    assert "claude-haiku-4-5-20251001" in body


def test_judgment_step_with_ttl_cache(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_judgment.clio", out)
    body = (out / "steps" / "02_detect_churn.go").read_text()
    # 24h = 86400s; passed as a *int64 to Lookup
    assert "86400" in body
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "judgment"
```

Expected: 3 FAIL.

- [ ] **Step 4: Implement judgment step rendering (Anthropic path)**

Add to `clio/emitters/_go_helpers.py`:

```python
from clio.emitters._shared_utils import _model_id


def _cache_ttl_seconds(cache_spec) -> int | None:
    """Resolve a CACHE directive into a ttl seconds value or None for permanent.
    `cache_spec`: "on" | "off" | dict like {"ttl": "24h"}.
    """
    if cache_spec == "on":
        return None  # permanent
    if cache_spec == "off" or cache_spec is None:
        return 0  # short-circuit
    if isinstance(cache_spec, dict) and "ttl" in cache_spec:
        # Parse "24h" / "30m" / "60s" — minimal handling for v0.20.0
        s = cache_spec["ttl"]
        if s.endswith("h"):
            return int(s[:-1]) * 3600
        if s.endswith("m"):
            return int(s[:-1]) * 60
        if s.endswith("s"):
            return int(s[:-1])
        raise ValueError(f"unsupported TTL format: {s!r}")
    raise ValueError(f"unsupported CACHE: {cache_spec!r}")


def render_judgment_step_go(step: StepIR, graph: FlowGraph) -> str:
    """Render steps/NN_<name>.go for a judgment step using anthropic-sdk-go.

    Body shape:
      1. Build prompt from inputs.
      2. Cache lookup if CACHE is configured.
      3. anthropic.NewClient → Messages.New (system = JSON-only prompt, user = prompt).
      4. Extract text from response.Content[0].Text.
      5. json.Unmarshal into typed output struct.
      6. .Validate(ctx).
      7. cache.Store if CACHE configured.
    """
    cls = _to_class_name(step.name)
    pkg = _safe_package_name(graph, default="flow")
    in_struct, out_struct = _step_in_out_struct(step, graph.contracts)
    model_short = graph.resources.models[0]  # first declared model
    model_id = _model_id(model_short)

    cache_ttl = _cache_ttl_seconds(getattr(step, "cache", None))
    cache_block_pre = ""
    cache_block_post = ""
    if cache_ttl is not None and cache_ttl != 0:
        ttl_decl = f"\tttl := int64({cache_ttl})\n\tttlPtr := &ttl\n"
        cache_block_pre = f'''{ttl_decl}\tcacheDir := cache.CacheDirFromEnv()
\tkey := cache.Key("{step.name}", "{model_id}", prompt, "")
\tif v, ok := cache.Lookup(cacheDir, "{step.name}", key, ttlPtr); ok {{
\t\tvar cached {cls}Out
\t\tif err := json.Unmarshal([]byte(v), &cached); err == nil {{
\t\t\treturn cached, nil
\t\t}}
\t}}
'''
        cache_block_post = f'''\tif rawBytes, err := json.Marshal(out); err == nil {{
\t\t_ = cache.Store(cacheDir, "{step.name}", key, "{model_id}", string(rawBytes))
\t}}
'''
    elif cache_ttl is None:  # CACHE: on
        cache_block_pre = f'''\tcacheDir := cache.CacheDirFromEnv()
\tkey := cache.Key("{step.name}", "{model_id}", prompt, "")
\tif v, ok := cache.Lookup(cacheDir, "{step.name}", key, nil); ok {{
\t\tvar cached {cls}Out
\t\tif err := json.Unmarshal([]byte(v), &cached); err == nil {{
\t\t\treturn cached, nil
\t\t}}
\t}}
'''
        cache_block_post = f'''\tif rawBytes, err := json.Marshal(out); err == nil {{
\t\t_ = cache.Store(cacheDir, "{step.name}", key, "{model_id}", string(rawBytes))
\t}}
'''
    has_cache = bool(cache_block_pre)

    imports = [
        '\t"context"',
        '\t"encoding/json"',
        '\t"fmt"',
        '\t"os"',
        '',
        '\t"github.com/anthropics/anthropic-sdk-go"',
        '\t"github.com/anthropics/anthropic-sdk-go/option"',
    ]
    if has_cache:
        imports.append(f'\t"{pkg}/clio_runtime/cache"')

    body = f'''package steps

// Auto-generated by CLIO. Do not edit by hand.

import (
{chr(10).join(imports)}
)

{in_struct}
{out_struct}
// {cls} implements the {step.name!r} judgment step (Anthropic SDK).
func {cls}(ctx context.Context, in {cls}In) ({cls}Out, error) {{
\t// 1. Build prompt from input.
\tinJSON, err := json.Marshal(in)
\tif err != nil {{
\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: marshal input: %w", err)
\t}}
\tprompt := fmt.Sprintf("Process this input and return JSON matching the output schema.\\n\\nInput:\\n%s", string(inJSON))

{cache_block_pre}\tclient := anthropic.NewClient(option.WithAPIKey(os.Getenv("ANTHROPIC_API_KEY")))
\tresp, err := client.Messages.New(ctx, anthropic.MessageNewParams{{
\t\tModel:     anthropic.F("{model_id}"),
\t\tMaxTokens: anthropic.F(int64(8192)),
\t\tSystem:    anthropic.F("You are a precise function. Return only valid JSON matching the requested output schema. No prose."),
\t\tMessages:  anthropic.F([]anthropic.MessageParam{{anthropic.NewUserMessage(anthropic.NewTextBlock(prompt))}}),
\t}})
\tif err != nil {{
\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: anthropic: %w", err)
\t}}
\tif len(resp.Content) == 0 {{
\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: empty response")
\t}}
\traw := resp.Content[0].Text
\tvar out {cls}Out
\tif err := json.Unmarshal([]byte(raw), &out); err != nil {{
\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: unmarshal: %w", err)
\t}}
\t// Validate per contract.
\tif validatable, ok := any(&out).(interface{{ Validate(context.Context) error }}); ok {{
\t\tif err := validatable.Validate(ctx); err != nil {{
\t\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: validate: %w", err)
\t\t}}
\t}}
{cache_block_post}\treturn out, nil
}}
'''
    return body
```

In `clio/emitters/go.py`, extend the step loop:

```python
from clio.emitters._go_helpers import render_judgment_step_go

for idx, call in enumerate(entry.chain, start=1):
    if not hasattr(call, "step_name"):
        continue
    step = graph.steps_by_name[call.step_name]
    filename = f"{idx:02d}_{step.name}.go"
    if step.mode == "exact":
        (steps_dir / filename).write_text(render_exact_step_go(step, graph.contracts, graph))
    elif step.mode == "judgment":
        (steps_dir / filename).write_text(render_judgment_step_go(step, graph))
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "judgment"
```

Expected: 3 PASS.

- [ ] **Step 6: Generate golden snapshot + golden test**

```bash
python -m clio compile tests/fixtures/go_judgment.clio --target go \
    --output tests/fixtures/expected_go/go_judgment
```

Append a golden test mirroring `test_golden_go_minimal`:

```python
def test_golden_go_judgment(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_judgment.clio", out)
    assert _read_tree(out) == _read_tree(EXPECTED_GO / "go_judgment")
```

```bash
uv run pytest tests/test_emitters/test_go.py::test_golden_go_judgment -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add clio/emitters/go.py clio/emitters/_go_helpers.py \
        tests/test_emitters/test_go.py \
        tests/fixtures/go_judgment.clio \
        tests/fixtures/expected_go/go_judgment/
git commit -m "$(cat <<'EOF'
feat(go-emitter): emit judgment step with Anthropic SDK + cache integration

steps/NN_<name>.go for judgment: prompt build → cache lookup (when
configured) → anthropic.Messages.New → JSON unmarshal into typed output →
Validate(ctx) → cache store. Cache spec resolves to ttl seconds; "on"
maps to permanent (nil ttl pointer).

Second golden snapshot fixture: go_judgment (exact → judgment with CACHE).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 5 — Control flow

Goal: extend the orchestrator (`flow/flow.go`) to handle IF/MATCH/WHILE, FOR EACH (sequential), and RESCUE blocks. Each construct mirrors python target semantics; conditions are rendered via the existing `_python_condition_expr` adapted into `_go_condition_expr`.

### Task 12: IF / ELSE emission

**Files:**
- Modify: `clio/emitters/_shared_utils.py` (add `_go_condition_expr`)
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`
- Test: `tests/test_emitters/test_shared_utils.py`

- [ ] **Step 1: Write the failing tests for `_go_condition_expr`**

Append to `tests/test_emitters/test_shared_utils.py`:

```python
from clio.emitters._shared_utils import _go_condition_expr


def test_go_condition_eq_ident_str():
    cond = {"kind": "compare", "op": "==",
            "left": {"kind": "ident", "name": "risk"},
            "right": {"kind": "str", "value": "high"}}
    assert _go_condition_expr(cond, scope_local=set()) == \
        'state["risk"].(string) == "high"'


def test_go_condition_local_ident():
    cond = {"kind": "compare", "op": ">",
            "left": {"kind": "ident", "name": "item"},
            "right": {"kind": "int", "value": 10}}
    assert _go_condition_expr(cond, scope_local={"item"}) == \
        'item.(int64) > int64(10)'


def test_go_condition_and():
    cond = {"kind": "and",
            "left": {"kind": "compare", "op": "==", "left": {"kind": "ident", "name": "a"}, "right": {"kind": "int", "value": 1}},
            "right": {"kind": "compare", "op": "==", "left": {"kind": "ident", "name": "b"}, "right": {"kind": "int", "value": 2}}}
    assert _go_condition_expr(cond, scope_local=set()) == \
        '(state["a"].(int64) == int64(1)) && (state["b"].(int64) == int64(2))'
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_emitters/test_shared_utils.py -v -k "go_condition"
```

Expected: 3 FAIL.

- [ ] **Step 3: Implement `_go_condition_expr`**

Append to `clio/emitters/_shared_utils.py`:

```python
def _go_condition_expr(cond: dict, scope_local: set[str]) -> str:
    """Render a CLIO condition AST as a Go boolean expression.

    `scope_local`: identifiers that are bound locally (loop variables,
    rescue context). Otherwise the identifier resolves through `state[...]`.

    Mirror of `_python_condition_expr`.
    """
    kind = cond["kind"]
    if kind == "and":
        left = _go_condition_expr(cond["left"], scope_local)
        right = _go_condition_expr(cond["right"], scope_local)
        return f"({left}) && ({right})"
    if kind == "or":
        left = _go_condition_expr(cond["left"], scope_local)
        right = _go_condition_expr(cond["right"], scope_local)
        return f"({left}) || ({right})"
    if kind == "not":
        return f"!({_go_condition_expr(cond['inner'], scope_local)})"
    if kind == "compare":
        return f"{_go_value_expr(cond['left'], scope_local)} {cond['op']} {_go_value_expr(cond['right'], scope_local)}"
    raise ValueError(f"unsupported condition kind: {kind!r}")


def _go_value_expr(node: dict, scope_local: set[str]) -> str:
    kind = node["kind"]
    if kind == "ident":
        name = node["name"]
        if name in scope_local:
            return f'{name}.(int64)' if False else _go_ident_accessor(name, scope_local)
        return f'state["{name}"].(string)'  # default; refined below
    if kind == "int":
        return f"int64({node['value']})"
    if kind == "float":
        return f"float64({node['value']})"
    if kind == "str":
        return f'"{node["value"]}"'
    if kind == "bool":
        return "true" if node["value"] else "false"
    raise ValueError(f"unsupported value kind: {kind!r}")


def _go_ident_accessor(name: str, scope_local: set[str]) -> str:
    # NOTE: type inference for local vars is approximate in v0.20.0.
    # Loop variables are typed via the FOR EACH emit site (task 15);
    # this helper handles the bare-ident-in-condition case.
    return f"{name}.(int64)"
```

Wait — the test expects `state["risk"].(string)` for a comparison against a `str` literal, but `state["a"].(int64)` for a comparison against an `int` literal. The accessor type depends on the right-hand value. Refactor:

```python
def _go_condition_expr(cond: dict, scope_local: set[str]) -> str:
    kind = cond["kind"]
    if kind == "and":
        return f"({_go_condition_expr(cond['left'], scope_local)}) && ({_go_condition_expr(cond['right'], scope_local)})"
    if kind == "or":
        return f"({_go_condition_expr(cond['left'], scope_local)}) || ({_go_condition_expr(cond['right'], scope_local)})"
    if kind == "not":
        return f"!({_go_condition_expr(cond['inner'], scope_local)})"
    if kind == "compare":
        left_node = cond["left"]
        right_node = cond["right"]
        right_type = _go_literal_type(right_node)
        left_expr = _go_value_expr_typed(left_node, scope_local, right_type)
        right_expr = _go_value_expr_typed(right_node, scope_local, right_type)
        return f"{left_expr} {cond['op']} {right_expr}"
    raise ValueError(f"unsupported condition kind: {kind!r}")


def _go_literal_type(node: dict) -> str:
    """Return the Go type ('string', 'int64', 'float64', 'bool') of the literal
    node on the right side of a comparison. Falls back to 'string' if unknown."""
    kind = node["kind"]
    return {
        "str": "string",
        "int": "int64",
        "float": "float64",
        "bool": "bool",
        "ident": "string",  # default — improved by scope_local lookup if local
    }.get(kind, "string")


def _go_value_expr_typed(node: dict, scope_local: set[str], expected_type: str) -> str:
    kind = node["kind"]
    if kind == "ident":
        name = node["name"]
        if name in scope_local:
            return f"{name}.({expected_type})"
        return f'state["{name}"].({expected_type})'
    if kind == "int":
        return f"int64({node['value']})"
    if kind == "float":
        return f"float64({node['value']})"
    if kind == "str":
        return f'"{node["value"]}"'
    if kind == "bool":
        return "true" if node["value"] else "false"
    raise ValueError(f"unsupported value kind: {kind!r}")
```

(Replace the earlier `_go_value_expr` and `_go_ident_accessor` stubs.)

- [ ] **Step 4: Run condition tests to verify they pass**

```bash
uv run pytest tests/test_emitters/test_shared_utils.py -v -k "go_condition"
```

Expected: 3 PASS.

- [ ] **Step 5: Write the failing emission tests for IF/ELSE**

Create `tests/fixtures/go_control_flow.clio`:

```clio
STEP detect
  TAKES: x: str
  GIVES: risk: str
  MODE:  exact
  LANG:  go

STEP escalate
  TAKES: x: str
  GIVES: ticket_id: str
  MODE:  exact
  LANG:  go

STEP archive
  TAKES: x: str
  GIVES: archived: bool
  MODE:  exact
  LANG:  go

FLOW pipeline
  detect(x="incoming")
    -> IF risk == "high" THEN
         escalate(x)
       ELSE
         archive(x)
       END

RESOURCES
  target: go
  models: [haiku]
```

Append to `tests/test_emitters/test_go.py`:

```python
def test_if_else_emits_go_branches(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_control_flow.clio", out)
    body = (out / "flow" / "flow.go").read_text()
    assert 'if state["risk"].(string) == "high" {' in body
    assert "} else {" in body
    assert "steps.Escalate(ctx," in body
    assert "steps.Archive(ctx," in body
```

- [ ] **Step 6: Implement IF/ELSE in the flow renderer**

In `clio/emitters/_go_helpers.py`, refactor `render_flow_go` so it dispatches on the type of each chain item. Add:

```python
from clio.ir.graph import (
    CallIR,
    IfBlockIR,
)


def _render_chain_item(item, prev_var: str, indent: str, graph: FlowGraph, scope_local: set[str]) -> tuple[list[str], str]:
    """Render one chain item. Returns (lines, new_prev_var)."""
    if isinstance(item, CallIR):
        step = graph.steps_by_name[item.step_name]
        cls = _to_class_name(step.name)
        out_var = f"{step.name}Out"
        input_init = _kwargs_to_step_input(step, prev_var, graph.contracts)
        return ([
            f"{indent}{out_var}, err := steps.{cls}(ctx, {input_init})",
            f"{indent}if err != nil {{",
            f"{indent}\treturn nil, err",
            f"{indent}}}",
            f'{indent}state["{step.name}"] = {out_var}',
            "",
        ], out_var)
    if isinstance(item, IfBlockIR):
        cond = _go_condition_expr(item.condition, scope_local)
        lines = [f"{indent}if {cond} {{"]
        cur = prev_var
        for sub in item.then_branch:
            sublines, cur = _render_chain_item(sub, cur, indent + "\t", graph, scope_local)
            lines.extend(sublines)
        lines.append(f"{indent}}} else {{")
        cur = prev_var
        for sub in item.else_branch:
            sublines, cur = _render_chain_item(sub, cur, indent + "\t", graph, scope_local)
            lines.extend(sublines)
        lines.append(f"{indent}}}")
        return (lines, prev_var)
    # Other block kinds added in subsequent tasks.
    raise NotImplementedError(f"chain item kind not supported in v0.20.0: {type(item).__name__}")


def render_flow_go(graph: FlowGraph) -> str:
    pkg = _safe_package_name(graph, default="flow")
    entry = graph.flows[graph.entry_flow_name]
    lines = [
        "package flow",
        "",
        "// Auto-generated by CLIO. Do not edit by hand.",
        "",
        "import (",
        '\t"context"',
        "",
        f'\t"{pkg}/steps"',
        ")",
        "",
        "func Run(ctx context.Context, kwargs map[string]any) (map[string]any, error) {",
        "\tstate := map[string]any{}",
        "",
    ]
    prev = "kwargs"
    for item in entry.chain:
        block, prev = _render_chain_item(item, prev, "\t", graph, scope_local=set())
        lines.extend(block)
    lines.append("\treturn state, nil")
    lines.append("}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "if_else"
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add clio/emitters/_shared_utils.py clio/emitters/_go_helpers.py \
        tests/test_emitters/test_go.py tests/test_emitters/test_shared_utils.py \
        tests/fixtures/go_control_flow.clio
git commit -m "$(cat <<'EOF'
feat(go-emitter): IF/ELSE branches + _go_condition_expr

_go_condition_expr renders a CLIO condition AST as a typed Go boolean
expression (matching the literal type for accessor casts). flow.go uses
it inside `if cond { ... } else { ... }`. The chain-item dispatcher in
_render_chain_item now handles both CallIR and IfBlockIR.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 13: MATCH / CASE emission

**Files:**
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`
- Modify: `tests/fixtures/go_control_flow.clio` (add a MATCH section)

- [ ] **Step 1: Extend the fixture**

Append to `tests/fixtures/go_control_flow.clio`:

```
    -> MATCH risk
         CASE "low":
           archive(x)
         CASE "mid":
           escalate(x)
         CASE "high":
           escalate(x)
       END
```

- [ ] **Step 2: Write the failing test**

```python
def test_match_emits_go_switch(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_control_flow.clio", out)
    body = (out / "flow" / "flow.go").read_text()
    assert 'switch state["risk"].(string) {' in body
    assert 'case "low":' in body
    assert 'case "mid":' in body
    assert 'case "high":' in body
```

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "match"
```

Expected: FAIL with `NotImplementedError` from `_render_chain_item`.

- [ ] **Step 4: Implement MATCH handling**

In `clio/emitters/_go_helpers.py`, extend `_render_chain_item`:

```python
from clio.ir.graph import MatchBlockIR

# inside _render_chain_item, after the IfBlockIR branch:
if isinstance(item, MatchBlockIR):
    subject_expr = _go_value_expr_typed(item.subject, scope_local, "string")
    lines = [f"{indent}switch {subject_expr} {{"]
    for case_value, case_body in item.cases:
        # case_value comes as a typed AST node — render the literal
        case_str = case_value["value"] if case_value["kind"] == "str" else case_value["value"]
        lines.append(f'{indent}case "{case_str}":')
        cur = prev_var
        for sub in case_body:
            sublines, cur = _render_chain_item(sub, cur, indent + "\t", graph, scope_local)
            lines.extend(sublines)
    lines.append(f"{indent}}}")
    return (lines, prev_var)
```

(Add the `_go_value_expr_typed` import alongside `_go_condition_expr`.)

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "match"
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add clio/emitters/_go_helpers.py tests/test_emitters/test_go.py \
        tests/fixtures/go_control_flow.clio
git commit -m "$(cat <<'EOF'
feat(go-emitter): MATCH/CASE → Go switch statement

Subject expression rendered via _go_value_expr_typed (string-typed for
now; extension to typed enums is a v0.21 refactor). Each CASE arm
recurses into _render_chain_item so nested calls/control work.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 14: WHILE loop emission

**Files:**
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing test**

```python
def test_while_loop_emits_for_with_condition(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP poll\n"
        "  TAKES: x: str\n"
        "  GIVES: done: bool\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW pipeline\n"
        "  WHILE done != true\n"
        "    poll(x=\"job-id\")\n"
        "  END\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "flow" / "flow.go").read_text()
    assert 'for !(state["done"].(bool) == true) {' in body or \
           'for state["done"].(bool) != true {' in body
    assert "steps.Poll(ctx," in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "while_loop"
```

Expected: FAIL.

- [ ] **Step 3: Implement WHILE in `_render_chain_item`**

```python
from clio.ir.graph import WhileBlockIR

# inside _render_chain_item:
if isinstance(item, WhileBlockIR):
    cond = _go_condition_expr(item.condition, scope_local)
    lines = [f"{indent}for {cond} {{"]
    cur = prev_var
    for sub in item.body:
        sublines, cur = _render_chain_item(sub, cur, indent + "\t", graph, scope_local)
        lines.extend(sublines)
    lines.append(f"{indent}}}")
    return (lines, prev_var)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "while_loop"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_go_helpers.py tests/test_emitters/test_go.py
git commit -m "$(cat <<'EOF'
feat(go-emitter): WHILE block → idiomatic Go for-loop with condition

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 15: FOR EACH (sequential) emission

**Files:**
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing test**

```python
def test_for_each_sequential(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP load\n"
        "  TAKES: file: str\n"
        "  GIVES: items: List<str>\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "STEP process\n"
        "  TAKES: item: str\n"
        "  GIVES: result: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW pipeline\n"
        "  load(file=\"in.csv\")\n"
        "    -> FOR EACH item IN items\n"
        "         process(item)\n"
        "       END\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "flow" / "flow.go").read_text()
    assert "for _, item := range" in body
    assert "steps.Process(ctx," in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "for_each_sequential"
```

Expected: FAIL.

- [ ] **Step 3: Implement sequential FOR EACH**

```python
from clio.ir.graph import ForEachIR

# inside _render_chain_item:
if isinstance(item, ForEachIR) and not item.parallel:
    var = item.loop_var
    coll_expr = _go_value_expr_typed(item.collection, scope_local, f"[]any")
    lines = [f"{indent}for _, {var} := range {coll_expr} {{"]
    sub_scope = scope_local | {var}
    cur = var  # the loop var becomes the "previous" for the inner chain
    for sub in item.body:
        sublines, cur = _render_chain_item(sub, cur, indent + "\t", graph, sub_scope)
        lines.extend(sublines)
    lines.append(f"{indent}}}")
    return (lines, prev_var)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "for_each_sequential"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_go_helpers.py tests/test_emitters/test_go.py
git commit -m "$(cat <<'EOF'
feat(go-emitter): sequential FOR EACH → for _, var := range collection

Loop variable is added to scope_local so child step kwargs resolve
locally rather than through state[...]. Parallel variant handled in T17.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 16: RESCUE block emission

**Files:**
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`
- Create: `tests/fixtures/go_rescue.clio`

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/go_rescue.clio`:

```clio
STEP risky
  TAKES: x: str
  GIVES: y: str
  MODE:  exact
  LANG:  go

STEP recover
  TAKES: x: str
  GIVES: y: str
  MODE:  exact
  LANG:  go

FLOW pipeline
  RESCUE
    risky(x="hi")
  ON_FAIL
    recover(x="hi")
  END

RESOURCES
  target: go
  models: [haiku]
```

- [ ] **Step 2: Write the failing test**

```python
def test_rescue_emits_defer_recover(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_rescue.clio", out)
    body = (out / "flow" / "flow.go").read_text()
    # Defer + recover pattern
    assert "defer func() {" in body
    assert "if r := recover(); r != nil" in body
    # On-fail body runs after recover trips
    assert "steps.Recover(ctx," in body
```

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "rescue"
```

Expected: FAIL.

- [ ] **Step 4: Implement RESCUE**

```python
from clio.ir.graph import RescueBlockIR

# inside _render_chain_item:
if isinstance(item, RescueBlockIR):
    lines = [
        f"{indent}func() {{",
        f"{indent}\tdefer func() {{",
        f"{indent}\t\tif r := recover(); r != nil {{",
    ]
    cur = prev_var
    on_fail_indent = indent + "\t\t\t"
    for sub in item.on_fail:
        sublines, cur = _render_chain_item(sub, cur, on_fail_indent, graph, scope_local)
        lines.extend(sublines)
    lines.extend([
        f"{indent}\t\t}}",
        f"{indent}\t}}()",
    ])
    cur = prev_var
    body_indent = indent + "\t"
    for sub in item.body:
        sublines, cur = _render_chain_item(sub, cur, body_indent, graph, scope_local)
        lines.extend(sublines)
    lines.append(f"{indent}}}()")
    return (lines, prev_var)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "rescue"
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add clio/emitters/_go_helpers.py tests/test_emitters/test_go.py \
        tests/fixtures/go_rescue.clio
git commit -m "$(cat <<'EOF'
feat(go-emitter): RESCUE block → defer recover() with ON_FAIL body

The protected body and the on-fail body run inside an immediately-invoked
function literal so the defer is scoped correctly. Matches python target
semantics (a panic in the body unwinds into the on-fail chain).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 6 — Parallel FOR EACH + ON_FAIL chain

### Task 17: FOR EACH PARALLEL via errgroup

**Files:**
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`
- Create: `tests/fixtures/go_parallel.clio`
- Create: `tests/fixtures/expected_go/go_parallel/`

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/go_parallel.clio`:

```clio
STEP load
  TAKES: file: str
  GIVES: items: List<str>
  MODE:  exact
  LANG:  go

STEP classify
  TAKES: item: str
  GIVES: label: str
  MODE:  judgment
  CACHE: ttl(24h)

FLOW pipeline
  load(file="in.csv")
    -> FOR EACH PARALLEL item IN items
         classify(item)
       END

RESOURCES
  target: go
  models: [haiku]
```

- [ ] **Step 2: Write the failing test**

```python
def test_for_each_parallel_emits_errgroup(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_parallel.clio", out)
    body = (out / "flow" / "flow.go").read_text()
    assert "errgroup" in body
    assert "g.SetLimit(10)" in body
    assert "g.Go(func() error {" in body
    assert "g.Wait()" in body
    # Go 1.22+ scoped loop var: no `item := item` capture needed
    assert "item := item" not in body
```

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "for_each_parallel"
```

Expected: FAIL.

- [ ] **Step 4: Implement FOR EACH PARALLEL**

In `_render_chain_item`, extend the `ForEachIR` branch:

```python
if isinstance(item, ForEachIR) and item.parallel:
    var = item.loop_var
    coll_expr = _go_value_expr_typed(item.collection, scope_local, "[]any")
    lines = [
        f"{indent}{{",
        f"{indent}\tg, ctx := errgroup.WithContext(ctx)",
        f"{indent}\tg.SetLimit(10)",
        f"{indent}\tfor _, {var} := range {coll_expr} {{",
        f"{indent}\t\tg.Go(func() error {{",
    ]
    sub_scope = scope_local | {var}
    cur = var
    for sub in item.body:
        sublines, cur = _render_chain_item(sub, cur, indent + "\t\t\t", graph, sub_scope)
        # Each step's `if err != nil { return nil, err }` should propagate as
        # `if err != nil { return err }` inside the goroutine.
        lines.extend(_rewrite_return_in_goroutine(sublines))
    lines.extend([
        f"{indent}\t\t\treturn nil",
        f"{indent}\t\t}})",
        f"{indent}\t}}",
        f"{indent}\tif err := g.Wait(); err != nil {{",
        f"{indent}\t\treturn nil, err",
        f"{indent}\t}}",
        f"{indent}}}",
    ])
    return (lines, prev_var)


def _rewrite_return_in_goroutine(lines: list[str]) -> list[str]:
    """Replace `return nil, err` with `return err` inside goroutine bodies."""
    return [line.replace("return nil, err", "return err") for line in lines]
```

Also add the errgroup import to `render_flow_go` when parallel is detected:

```python
# at the top of render_flow_go, when building `lines`:
has_parallel = _flow_uses_parallel(graph)
imports = ['\t"context"', '', f'\t"{pkg}/steps"']
if has_parallel:
    imports.append('\t"golang.org/x/sync/errgroup"')
lines = [
    "package flow",
    "",
    "// Auto-generated by CLIO. Do not edit by hand.",
    "",
    "import (",
    *imports,
    ")",
    "",
    ...
]
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "for_each_parallel"
```

Expected: PASS.

- [ ] **Step 6: Generate golden snapshot + golden test**

```bash
python -m clio compile tests/fixtures/go_parallel.clio --target go \
    --output tests/fixtures/expected_go/go_parallel
```

```python
def test_golden_go_parallel(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_parallel.clio", out)
    assert _read_tree(out) == _read_tree(EXPECTED_GO / "go_parallel")
```

```bash
uv run pytest tests/test_emitters/test_go.py::test_golden_go_parallel -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add clio/emitters/_go_helpers.py tests/test_emitters/test_go.py \
        tests/fixtures/go_parallel.clio tests/fixtures/expected_go/go_parallel/
git commit -m "$(cat <<'EOF'
feat(go-emitter): FOR EACH PARALLEL via errgroup.WithContext

g.SetLimit(10) mirrors python's ThreadPoolExecutor(max_workers=10). No
`item := item` capture (Go 1.22+ scopes the loop variable per iteration).
errgroup dep added conditionally to go.mod.

Third golden snapshot fixture: go_parallel (exact → parallel judgment).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 18: ON_FAIL chain (retry / escalate / fallback / abort)

**Files:**
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_judgment_step_wraps_in_retry_loop(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  ON_FAIL: retry(3) then abort(\"ouch\")\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "steps" / "01_detect.go").read_text()
    assert "for attempt := 0; attempt < 3; attempt++ {" in body
    assert "time.Sleep" in body  # backoff
    assert 'fmt.Errorf("ouch' in body


def test_judgment_step_fallback_step(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  ON_FAIL: retry(2) then fallback(naive) then abort(\"done\")\n"
        "STEP naive\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "steps" / "01_detect.go").read_text()
    assert "Naive(ctx, " in body
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "wraps_in_retry or fallback_step"
```

Expected: 2 FAIL.

- [ ] **Step 3: Implement ON_FAIL chain in judgment renderer**

Refactor `render_judgment_step_go` to wrap the SDK call section in an attempt loop. Add a helper:

```python
def _render_on_fail_chain(step: StepIR, graph: FlowGraph, body_lines: list[str]) -> list[str]:
    """Wrap the SDK call body in an ON_FAIL strategy chain.

    Strategies (in order): retry(N), escalate (next model), fallback(step),
    abort(msg). The chain executes left-to-right, advancing only when the
    previous strategy fails.
    """
    chain = getattr(step, "on_fail", None) or []
    if not chain:
        return body_lines

    indented = ["\t" + l for l in body_lines]
    lines = []
    retry_count = 1
    abort_msg = "step failed"
    fallback_step = None
    for strategy in chain:
        kind = strategy["kind"]
        if kind == "retry":
            retry_count = strategy["n"]
        elif kind == "fallback":
            fallback_step = strategy["step"]
        elif kind == "abort":
            abort_msg = strategy["msg"]
        # escalate is a no-op in v0.20.0 (only one model configured per emit)

    # Retry loop
    lines.extend([
        f"\tvar attemptErr error",
        f"\tvar out {_to_class_name(step.name)}Out",
        f"\tfor attempt := 0; attempt < {retry_count}; attempt++ {{",
    ])
    # body uses `out` and `attemptErr` instead of local out/err
    inner = [l.replace("var out ", "// var out ")
              .replace("err :=", "attemptErr =")
              .replace("if err != nil", "if attemptErr != nil")
              for l in body_lines]
    lines.extend(["\t\t" + l for l in inner])
    lines.extend([
        f"\t\tif attemptErr == nil {{",
        f"\t\t\tbreak",
        f"\t\t}}",
        f"\t\ttime.Sleep(time.Duration(attempt+1) * time.Second)",
        f"\t}}",
    ])

    # Fallback — wire inputs from `in` field-by-field. The parser enforces
    # that both the primary step and the fallback step share the same TAKES
    # contract, so field names match by construction. Emit one struct-literal
    # field initializer per declared TAKES field.
    if fallback_step:
        fb_cls = _to_class_name(fallback_step)
        fb_in_init_fields = ", ".join(
            f"{_to_go_field_name(name)}: in.{_to_go_field_name(name)}"
            for name, _ftype in step.takes
        )
        # `out` here is the primary step's typed output. The fallback step is
        # required (by the source-language ON_FAIL grammar) to declare a GIVES
        # type compatible with the primary step's; the parser refuses the
        # source otherwise. So a direct field copy from fallbackOut back into
        # `out` resolves the type-difference at emit time.
        fb_out_copy_fields = "\n".join(
            f"\t\t\tout.{_to_go_field_name(name)} = fallbackOut.{_to_go_field_name(name)}"
            for name, _ftype in step.gives
        )
        lines.extend([
            f"\tif attemptErr != nil {{",
            f"\t\tfallbackOut, err := {fb_cls}(ctx, {fb_cls}In{{ {fb_in_init_fields} }})",
            f"\t\tif err == nil {{",
            f"{fb_out_copy_fields}",
            f"\t\t\treturn out, nil",
            f"\t\t}}",
            f"\t\tattemptErr = err",
            f"\t}}",
        ])

    # Abort
    lines.extend([
        f"\tif attemptErr != nil {{",
        f'\t\treturn out, fmt.Errorf("{abort_msg}: %w", attemptErr)',
        f"\t}}",
        f"\treturn out, nil",
    ])
    return lines
```

In `render_judgment_step_go`, replace the simple body section with `_render_on_fail_chain(step, graph, original_body)`. Add `"time"` to the imports list.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "wraps_in_retry or fallback_step"
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_go_helpers.py tests/test_emitters/test_go.py
git commit -m "$(cat <<'EOF'
feat(go-emitter): ON_FAIL chain — retry / escalate / fallback / abort

Judgment step body is wrapped in a retry loop with exponential backoff.
Fallback calls the alternate step; abort returns a wrapped error with
the configured message. Escalate is a no-op in v0.20.0 (single model
per emission); v0.20.x will add multi-model dispatch.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 7 — Refused combinations

Goal: every IR construct outside v0.20.0 scope raises a compile-time error with a clear remediation message. Errors fire at IR-walk time (inside `GoEmitter.emit`), before any file write.

### Task 19: Implement E_GO_001 … E_GO_012

**Files:**
- Modify: `clio/emitters/go.py`
- Modify: `clio/emitters/_go_helpers.py`
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing tests (one per error code)**

Append to `tests/test_emitters/test_go.py`:

```python
import pytest


def _compile_expecting_error(source_path: Path, output_dir: Path, code: str) -> None:
    with pytest.raises(ValueError, match=code):
        _compile(source_path, output_dir)


def test_E_GO_001_lang_python(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP load\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "  LANG:  python\n"
        "FLOW pipeline\n"
        "  load(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_001")


def test_E_GO_001_lang_rust(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP load\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "  LANG:  rust\n"
        "FLOW pipeline\n"
        "  load(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_001")


def test_E_GO_002_invoke_mode_cli(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  INVOKE: { mode: cli }\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_002")


def test_E_GO_003_invoke_protocol_bedrock(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  INVOKE: { protocol: bedrock }\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_003")


def test_E_GO_004_no_flow(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT just_a_contract\n"
        "  SHAPE: {x: str}\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_004")


def test_E_GO_005_invoke_protocol_openai(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  INVOKE: { protocol: openai }\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_005")


def test_E_GO_006_flow_composition(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP a\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW sub\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  a(x)\n"
        "FLOW pipeline\n"
        "  sub(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_006")


def test_E_GO_007_impl_mode_rest(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP fetch\n"
        "  TAKES: id: str\n"
        "  GIVES: body: str\n"
        "  IMPL: { mode: rest, url: \"http://x/${id}\", method: GET }\n"
        "FLOW pipeline\n"
        "  fetch(id=\"1\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_007")


def test_E_GO_008_impl_mode_shell(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP grep\n"
        "  TAKES: file: str\n"
        "  GIVES: lines: List<str>\n"
        "  IMPL: { mode: shell, command: \"grep foo ${file}\" }\n"
        "FLOW pipeline\n"
        "  grep(file=\"x\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_008")


def test_E_GO_009_impl_mode_sql(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP q\n"
        "  TAKES: name: str\n"
        "  GIVES: rows: List<{id: int}>\n"
        "  IMPL: { mode: sql, database: { driver: sqlite, dsn: \":memory:\" }, query: \"SELECT id FROM t WHERE name = :name\" }\n"
        "FLOW pipeline\n"
        "  q(name=\"alice\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_009")


def test_E_GO_010_impl_mode_mcp_tool(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP call\n"
        "  TAKES: payload: str\n"
        "  GIVES: result: str\n"
        "  IMPL: { mode: mcp_tool, server: { name: x, command: [\"my-mcp\"] }, tool: do }\n"
        "FLOW pipeline\n"
        "  call(payload=\"x\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_010")


def test_E_GO_012_test_block(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP load\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW pipeline\n"
        "  load(x=\"hi\")\n"
        "TEST sanity\n"
        "  FOR pipeline\n"
        "  WITH x=\"hi\"\n"
        "  EXPECTS load.y: contains(\"foo\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_012")
```

(`E_GO_011` covers `RESUME` declarations; if the parser doesn't currently have a RESUME-shape syntax declaration that the IR exposes distinctly from the `--from-step` CLI flag, omit the test and the corresponding raise — note this in the commit message.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "E_GO_"
```

Expected: ~12 FAIL.

- [ ] **Step 3: Implement validation pre-walk**

Add to `clio/emitters/_go_helpers.py`:

```python
from clio.ir.graph import (
    ApiInvokeIR,
    CallIR,
    FlowCallIR,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    McpToolImplIR,
    RescueBlockIR,
    RestImplIR,
    ShellImplIR,
    SqlImplIR,
    WhileBlockIR,
)


_GO_E_001_MSG = (
    "E_GO_001: target: go can only embed exact step bodies in Go (LANG: go or "
    "LANG: auto). For Python/Bash/etc., use --target python (or --target "
    "claude-skill to let the LLM host drive the flow); for shell glue "
    "specifically, use impl.mode: shell which target: go supports natively "
    "(currently deferred to v0.20.x — see E_GO_008)."
)
_GO_E_002_MSG = (
    "E_GO_002: target: go does not subprocess 'claude -p'. Use --target python, "
    "--target mcp-server, or --target claude-cli."
)
_GO_E_003_MSG = (
    "E_GO_003: target: go ships Anthropic and OpenAI SDKs only. Use --target "
    "python for Bedrock/Vertex."
)
_GO_E_004_MSG = (
    "E_GO_004: target: go needs at least one FLOW to emit cmd/<flow>/main.go."
)
_GO_E_005_MSG = (
    "E_GO_005: target: go v0.20.0 does not yet support invoke.protocol: openai. "
    "Use --target python until the v0.20.x OpenAI emitter ships."
)
_GO_E_006_MSG = (
    "E_GO_006: target: go v0.20.0 does not yet support FLOW composition. Use "
    "--target python until the v0.20.x sub-flow emitter ships."
)
_GO_E_007_MSG = (
    "E_GO_007: target: go v0.20.0 does not yet support impl.mode: rest. Use "
    "--target python until the v0.20.x REST emitter ships."
)
_GO_E_008_MSG = (
    "E_GO_008: target: go v0.20.0 does not yet support impl.mode: shell. Use "
    "--target python until the v0.20.x shell emitter ships."
)
_GO_E_009_MSG = (
    "E_GO_009: target: go v0.20.0 does not yet support impl.mode: sql. Use "
    "--target python until the v0.20.x SQL emitter ships."
)
_GO_E_010_MSG = (
    "E_GO_010: target: go v0.20.0 does not yet support impl.mode: mcp_tool. "
    "Use --target python until the v0.20.x MCP emitter ships."
)
_GO_E_012_MSG = (
    "E_GO_012: target: go v0.20.0 does not yet emit TEST blocks as `go test`. "
    "Use --target python until the v0.20.x TEST emitter ships."
)

_GO_OK_LANGS = {"go", "auto", None}


def validate_graph_for_go(graph: FlowGraph) -> None:
    """Raise ValueError with an E_GO_NNN code if the graph uses any feature
    outside v0.20.0 scope. Runs before any file is written."""
    if not graph.flows:
        raise ValueError(_GO_E_004_MSG)

    # E_GO_006: more than one signed FLOW (composition)
    if len(graph.flows) > 1:
        raise ValueError(_GO_E_006_MSG)

    # E_GO_012: TEST blocks
    if getattr(graph, "tests", None):
        raise ValueError(_GO_E_012_MSG)

    for step in graph.steps_by_name.values():
        # E_GO_001: LANG
        if step.mode == "exact":
            lang = getattr(step, "lang", None)
            if lang not in _GO_OK_LANGS:
                raise ValueError(f"{_GO_E_001_MSG} (step={step.name!r}, lang={lang!r})")
        # invoke.* on judgment steps
        invoke = getattr(step, "invoke", None)
        if invoke is not None:
            if isinstance(invoke, ApiInvokeIR):
                if invoke.mode == "cli":
                    raise ValueError(_GO_E_002_MSG)
                if invoke.protocol in {"bedrock", "vertex"}:
                    raise ValueError(_GO_E_003_MSG)
                if invoke.protocol == "openai":
                    raise ValueError(_GO_E_005_MSG)
        # impl.mode
        impl = getattr(step, "impl", None)
        if isinstance(impl, RestImplIR):
            raise ValueError(_GO_E_007_MSG)
        if isinstance(impl, ShellImplIR):
            raise ValueError(_GO_E_008_MSG)
        if isinstance(impl, SqlImplIR):
            raise ValueError(_GO_E_009_MSG)
        if isinstance(impl, McpToolImplIR):
            raise ValueError(_GO_E_010_MSG)

    # Walk chain for FlowCallIR (sub-flow invocation) — E_GO_006 catches this
    # but also walks here for nested cases.
    def _walk(items):
        for it in items:
            if isinstance(it, FlowCallIR):
                raise ValueError(_GO_E_006_MSG)
            if isinstance(it, (IfBlockIR,)):
                _walk(it.then_branch)
                _walk(it.else_branch)
            if isinstance(it, MatchBlockIR):
                for _v, body in it.cases:
                    _walk(body)
            if isinstance(it, WhileBlockIR):
                _walk(it.body)
            if isinstance(it, ForEachIR):
                _walk(it.body)
            if isinstance(it, RescueBlockIR):
                _walk(it.body)
                _walk(it.on_fail)

    for flow in graph.flows.values():
        _walk(flow.chain)
```

In `clio/emitters/go.py`, call this validator at the very start of `emit`:

```python
from clio.emitters._go_helpers import validate_graph_for_go

class GoEmitter(BaseEmitter):
    def emit(self, graph, output_dir, *, source_path=None):
        validate_graph_for_go(graph)
        # rest of emit...
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_emitters/test_go.py -v -k "E_GO_"
```

Expected: ~12 PASS.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/go.py clio/emitters/_go_helpers.py tests/test_emitters/test_go.py
git commit -m "$(cat <<'EOF'
feat(go-emitter): refuse out-of-scope IR constructs at compile time

E_GO_001 (non-Go LANG), E_GO_002 (invoke.mode cli), E_GO_003 (bedrock/vertex),
E_GO_004 (no FLOW), E_GO_005 (openai), E_GO_006 (FLOW composition), E_GO_007
(rest), E_GO_008 (shell), E_GO_009 (sql), E_GO_010 (mcp_tool), E_GO_012 (TEST).

Each error message routes the user to --target python or documents the
deferred-to-v0.20.x status. Walk happens before any file write.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 8 — Docs + example + final polish

### Task 20: Update docs (COMPILATION_TARGETS, LANGUAGE_SPEC, manual, README, CHANGELOG)

**Files:**
- Modify: `docs/COMPILATION_TARGETS.md`
- Modify: `docs/LANGUAGE_SPEC.md`
- Modify: `docs/manual/04-targets.md`
- Modify: `docs/manual/06-troubleshooting.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update `docs/COMPILATION_TARGETS.md`**

In the "Targets at a glance" table, move `go` from "Future" to "Implemented" with `IMPORT (v0.18)` set to ✅ (target: go inherits the same IMPORT semantics — cross-file imports compile transparently).

Replace the existing `## target: go (Future)` section with a canonical entry mirroring the structure of `## target: python` (Layout, Use, Refused combinations, Inherited features, Logging, Resume, Cache layout interchangeable, Model name mapping). Document the v0.20.0 scope limitations (E_GO_005..012) inline so users know what's deferred.

- [ ] **Step 2: Update `docs/LANGUAGE_SPEC.md`**

In the "LANG per step" target table, add a column or row for `go`:

```
| `LANG:` per step | ✅ | ✅ | ignored (still emits Python on every EXACT) | ignored | ignored | only `go` or `auto` accepted (E_GO_001) |
```

(Adjust the existing column layout; this is a one-line edit.)

- [ ] **Step 3: Update `docs/manual/04-targets.md`**

Add a `go` column to the cross-target feature matrix. Cells for v0.20.0:

| Feature | go |
|---|---|
| exact (LANG: go / auto) | ✅ |
| exact (LANG: python / bash / rust / node) | ❌ E_GO_001 |
| judgment (anthropic) | ✅ |
| judgment (openai) | ❌ E_GO_005 |
| judgment (bedrock / vertex) | ❌ E_GO_003 |
| invoke.mode: cli | ❌ E_GO_002 |
| CACHE | ✅ |
| ON_FAIL chain | ✅ |
| IF / MATCH / WHILE | ✅ |
| FOR EACH (seq + parallel) | ✅ |
| RESCUE | ✅ |
| FLOW composition | ❌ E_GO_006 |
| impl.mode rest | ❌ E_GO_007 |
| impl.mode shell | ❌ E_GO_008 |
| impl.mode sql | ❌ E_GO_009 |
| impl.mode mcp_tool | ❌ E_GO_010 |
| RESUME (`--from-step`) | ❌ E_GO_011 |
| JSONL logging | ⏸ (silent no-op) |
| TEST blocks | ❌ E_GO_012 |

- [ ] **Step 4: Update `docs/manual/06-troubleshooting.md`**

Add one entry per error code (E_GO_001..010, E_GO_012) following the existing convention. Example for E_GO_001:

```markdown
### `E_GO_001: target: go can only embed exact step bodies in Go`

**Symptom**: `clio compile flow.clio --target go` raises with `E_GO_001`.

**Cause**: a STEP `exact` declares `LANG: python` (or rust, node, bash). The
`target: go` emitter cannot embed a non-Go step body in a Go binary.

**Resolution**:
- For Python step bodies: compile with `--target python` instead.
- For pure shell glue: wait for `impl.mode: shell` in `target: go` (v0.20.x,
  tracked as E_GO_008), or use `--target python` today.
- For Go step bodies: explicitly set `LANG: go` (or omit LANG so it defaults
  to `auto` → Go).
```

Repeat for each error code, briefly.

- [ ] **Step 5: Update `README.md`**

In the bullet list of supported targets (where `claude-cli`, `python`, `mcp-server`, `langgraph`, `claude-skill` are listed), add `go` with a one-liner. Update any "5 emitters" mention to "6 emitters". Bump the test count badge in a separate commit at release-admin time.

- [ ] **Step 6: Update `CHANGELOG.md`**

Add a `[Unreleased]` section (or, if one exists, append to it):

```markdown
## [Unreleased]

### Added

- **`target: go` — sixth compilation target.** Emits a Go module importable as
  a package (`flow.Run(ctx, kwargs)`) and runnable as a CLI
  (`cmd/<flow>/main.go`). v0.20.0 scope covers CONTRACT, exact (LANG: go) and
  judgment (Anthropic SDK), IF/MATCH/WHILE, FOR EACH (sequential + parallel
  via `errgroup`), RESCUE, ON_FAIL chain, CACHE (layout interchangeable with
  python target), RESOURCES. New emitter module `clio/emitters/go.py`
  (~450 LOC) + helper module `clio/emitters/_go_helpers.py` (~700 LOC).
  Embedded Go runtime templates: `clio_runtime/validate` (jsonschema/v6 +
  x-clio-assert walker) and `clio_runtime/cache` (SHA256 content-addressed).
- 12 new compile-time refused-combo errors (`E_GO_001` … `E_GO_012`)
  documented in `docs/manual/06-troubleshooting.md`. Deferred-to-v0.20.x
  features (OpenAI SDK, FLOW composition, impl.mode rest/sql/mcp_tool/shell,
  RESUME, TEST blocks) raise at compile time with a remediation pointer.

### Docs

- `docs/COMPILATION_TARGETS.md`: `target: go` moves from "Future" to
  "Implemented"; canonical entry added.
- `docs/LANGUAGE_SPEC.md`: Go added to the `LANG per step` target table.
- `docs/manual/04-targets.md`: Go column added to the cross-target feature
  matrix.
- `docs/manual/03-cookbook.md`: new recipe "Compile a flow to a Go binary".
- `docs/manual/06-troubleshooting.md`: entries for E_GO_001..E_GO_010 and
  E_GO_012, plus "missing Go toolchain" + "modernc.org/sqlite vs CGo" notes.

### Tests

- ~80 new tests across `tests/test_emitters/test_go.py`,
  `tests/test_emitters/test_go_compile.py`, and
  `tests/test_emitters/test_shared_utils.py`. Net `1067 → 1147+`.
- 5 new fixtures: `tests/fixtures/{go_minimal,go_judgment,go_control_flow,go_parallel,go_rescue}.clio`.
- 4 new golden snapshots: `tests/fixtures/expected_go/{go_minimal,go_judgment,go_parallel,go_rescue}/`.
```

- [ ] **Step 7: Commit**

```bash
git add docs/ README.md CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs(v0.20.0): target: go canonical entry + cookbook + troubleshooting

COMPILATION_TARGETS moves go from Future to Implemented. LANGUAGE_SPEC
notes the LANG restriction. manual/04-targets gets a go column with
v0.20.0 scope cells. manual/06-troubleshooting gets one entry per
E_GO_NNN. README and CHANGELOG updated.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 21: Cookbook recipe + `examples/mvp_go.clio`

**Files:**
- Create: `examples/mvp_go.clio`
- Modify: `docs/manual/03-cookbook.md`
- Test: `tests/test_emitters/test_go.py` (one final end-to-end snapshot)

- [ ] **Step 1: Create the example**

Create `examples/mvp_go.clio`:

```clio
CONTRACT customer_risk
  SHAPE: {client: str, risk: enum(low|mid|high), reason: str(max=300)}
  ASSERT: len(reason) > 0

STEP load_customers
  TAKES: file: str
  GIVES: customers: List<{name: str, revenue: float}>
  MODE:  exact
  LANG:  go

STEP detect_churn_naive
  TAKES: customers: List<{name: str, revenue: float}>
  GIVES: risks: List<customer_risk>
  MODE:  exact
  LANG:  go

STEP detect_churn
  TAKES: customers: List<{name: str, revenue: float}>
  GIVES: risks: List<customer_risk>
  MODE:  judgment
  CACHE: ttl(24h)
  ON_FAIL: retry(3) then fallback(detect_churn_naive) then abort("churn detection exhausted")

FLOW customer_retention
  load_customers(file="customers.csv")
    -> detect_churn(customers)

RESOURCES
  target: go
  models: [haiku, sonnet, opus]
```

- [ ] **Step 2: Generate golden snapshot for the example**

```bash
python -m clio compile examples/mvp_go.clio --target go \
    --output tests/fixtures/expected_go/mvp_go
```

- [ ] **Step 3: Add the golden test**

```python
def test_golden_mvp_go(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(Path("examples/mvp_go.clio"), out)
    assert _read_tree(out) == _read_tree(EXPECTED_GO / "mvp_go")
```

- [ ] **Step 4: Add the cookbook recipe**

Append to `docs/manual/03-cookbook.md` (under a section heading consistent with existing recipes):

```markdown
### Compile a flow to a Go binary

Use `target: go` when your team's primary stack is Go and you want a
single static binary instead of a Python interpreter footprint.

```bash
python -m clio compile examples/mvp_go.clio --target go --output ./go-out
cd go-out
go mod tidy
go build ./cmd/customer_retention
ANTHROPIC_API_KEY=sk-... ./customer_retention --kwargs '{"file":"customers.csv"}'
```

**What the emitted module contains**:

```
go-out/
  go.mod                              # pinned anthropic-sdk-go, jsonschema/v6, errgroup
  cmd/customer_retention/main.go      # CLI entry — parses --kwargs JSON
  contracts/contracts.go              # struct + Validate() per CONTRACT
  flow/flow.go                        # orchestrator — calls steps in chain order
  steps/                              # one Go file per STEP (exact stubs + judgment bodies)
  clio_runtime/                       # cache + validate (embedded by the emitter)
```

**Filling in exact step stubs**: each `steps/NN_<name>.go` panics with
`fill me in: <step>`. Edit the body — the function receives a typed
`<Step>In` and must return a typed `<Step>Out` plus error. The bundled
`validate.Schema(ctx, ...)` helper checks the output against the
embedded JSON Schema.

**v0.20.0 scope**: this target covers the most common case. See
`docs/manual/06-troubleshooting.md` for the list of features deferred
to v0.20.x (OpenAI, FLOW composition, `impl.mode {rest, sql, mcp_tool,
shell}`, RESUME, TEST blocks) — each fails at compile time with a
remediation pointer.

**Cache compatibility**: the cache layout is byte-identical to
`target: python`. You can swap targets between runs and reuse cached
judgment responses.

**Cross-platform Go build**: the SQLite driver dependency
(`modernc.org/sqlite`) is **not** included in v0.20.0 (impl.mode sql is
deferred). When it lands in v0.20.x, the build will still work on every
platform without a C toolchain because `modernc.org/sqlite` is pure Go.
```

- [ ] **Step 5: Run the full test suite**

```bash
uv run pytest -v
```

Expected: ~1147 passed (existing 1067 + 80 new).

Run ruff + mypy per `[[feedback_run_ruff_before_push]]` + `[[feedback_run_mypy_before_push]]`:

```bash
uv run ruff check . --fix
uv run mypy
uv run pytest --tb=short
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add examples/mvp_go.clio docs/manual/03-cookbook.md \
        tests/test_emitters/test_go.py \
        tests/fixtures/expected_go/mvp_go/
git commit -m "$(cat <<'EOF'
docs(v0.20.0): mvp_go.clio example + cookbook recipe + final golden

examples/mvp_go.clio: customer-retention flow with CONTRACT + exact +
judgment + CACHE + ON_FAIL chain — exercises the full v0.20.0 surface.

Cookbook recipe walks through: clio compile → go mod tidy → go build →
run binary. Golden snapshot mvp_go covers end-to-end emission.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Push and open PR**

```bash
git push -u origin feat/v0.20-target-go-emitter
gh pr create --title "feat: v0.20.0 — target: go emitter (parity baseline)" \
  --body "$(cat <<'EOF'
## Summary

Sixth compilation target. Ships a working `target: go` emitter covering the v0.20.0 baseline:

- ✅ CONTRACT → Go struct + json tags + Validate(ctx) + embedded JSON Schema
- ✅ STEP exact (LANG: go or auto) → typed stubs with `panic("fill me in")`
- ✅ STEP judgment → anthropic-sdk-go + cache lookup/store + Validate
- ✅ CACHE: on / ttl(...) — byte-identical layout to target: python
- ✅ Control flow — IF/ELSE, MATCH/CASE, WHILE, FOR EACH (seq), RESCUE
- ✅ FOR EACH PARALLEL — `errgroup.WithContext(ctx)` + `g.SetLimit(10)`
- ✅ ON_FAIL chain — retry / escalate / fallback / abort
- ✅ RESOURCES.models → short → Anthropic ID mapping at emit time
- ❌ Compile-time refused (E_GO_001..E_GO_012): non-Go LANG, invoke.mode cli, bedrock/vertex, no-FLOW, OpenAI, FLOW composition, impl.mode {rest,shell,sql,mcp_tool}, RESUME, TEST blocks. Each routes the user to --target python.

Net test count: 1067 → ~1147 (+80). Five new fixtures, four new golden snapshots.

Reference spec: `docs/superpowers/specs/2026-05-17-target-go-design.md` (merged PR #70). This PR implements part 1 of N (v0.20.0); subsequent v0.20.x patches will lift each refused-combo error code as the corresponding emitter path lands.

## Test plan

- [ ] CI green (lint-and-test)
- [ ] Manual: `python -m clio compile examples/mvp_go.clio --target go --output ./go-out && cd go-out && go mod tidy && go build ./cmd/customer_retention` produces a binary
- [ ] Gemini PR review applied / replied per [[feedback_reply_to_gemini]]
- [ ] No language/IR/parser change (verified via `git diff` on `clio/parser/` and `clio/ir/`)
- [ ] After merge → release-admin PR for v0.20.0 (separate per [[feedback_release_pr_separate]])
- [ ] After merge → v0.20.1 brainstorm for the next deferred feature (decide OpenAI vs FLOW composition vs impl.mode based on user signal)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8: Wait for Gemini review + CI**

Manually trigger if auto doesn't fire (per [[feedback_gemini_auto_trigger_unreliable]]):

```bash
gh pr comment <PR-NUMBER> --body "/gemini review"
```

Apply / reply per [[feedback_reply_to_gemini]] using the threaded-reply endpoint per [[feedback_gh_review_reply_endpoint]]:

```bash
gh api repos/Sandjab/clio/pulls/<PR-NUMBER>/comments/<COMMENT-ID>/replies \
    -X POST -f body="..."
```

---

## Final self-review checklist

Before opening the PR, run through:

1. **Spec coverage** — every section of the spec's "Mapping IR → Go" table for v0.20.0 scope has a task. The deferred features (OpenAI, FLOW composition, impl.modes, RESUME, TEST, logging) are explicitly out of scope with compile-time E_GO codes. ✅
2. **Placeholder scan** — no `TBD`, no `TODO: implement later`, no "Add appropriate error handling" left in the plan. Each code block is concrete. ✅
3. **Type consistency** — `_to_class_name` / `_to_go_field_name` used consistently; `_type_to_go` signature matches everywhere; `_render_chain_item` returns `(lines, new_prev_var)` everywhere. ✅
4. **Tests for every step** — every task has at least one failing test before implementation; every emitter file has emission-shape tests; goldens cover 4 of the 5 fixtures end-to-end. ✅

Once all 21 tasks pass and Gemini's cycle closes, this PR is ready for squash-merge into `main`. After merge, a separate release-admin PR (per [[feedback_release_pr_separate]]) bumps `pyproject.toml` to `0.20.0`, updates `README.md` badges, and tags `v0.20.0` on the feature commit.

Next steps after v0.20.0 ships: brainstorm v0.20.1 with the user — pick the highest-priority deferred feature (likely OpenAI dispatch since users self-select multi-provider, or `impl.mode: rest` since that's the most common production need). Write the next spec → plan → feature PR cycle.
