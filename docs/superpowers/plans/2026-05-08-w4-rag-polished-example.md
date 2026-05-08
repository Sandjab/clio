# W4 RAG-like Polished Example + `impl.shell.parse: json` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a polished RAG-like example (LLM-as-retriever, no embeddings) in two `.clio` variants — basic (manual-edit loaders) and self-contained (zero-edit via new `impl.shell.parse: json`) — and add the supporting language extension.

**Architecture:** Three concentric deliverables: (1) language extension `impl.shell.parse: json` lives in parser → IR → python emitter (claude-cli ignores, mcp_server inherits via shared `emit_shell_step`); (2) two new `.clio` files in `examples/` plus `faq.txt`/`faq.json`/`question.txt`; (3) docs (README section, LANGUAGE_SPEC update, CHANGELOG entry). Implementation follows TDD layer by layer (parser → IR → emitter → fixture → examples → docs).

**Tech Stack:** Python 3.12+, Pydantic v2, hand-written recursive-descent parser, pytest. No new runtime deps. The parser's lexer already tokenizes `parse:` as IDENT/COLON; no lexer change needed.

**Spec:** `docs/superpowers/specs/2026-05-08-w4-rag-polished-example-design.md` (commit `159f6af`).

---

## Task 1: Pre-flight verification

**Files (read-only):**
- Read: `clio/parser/parser.py:430-466`
- Read: `clio/ir/graph.py:46`, `clio/ir/builder.py:248-261`
- Read: `clio/emitters/_python_helpers.py:597-652`
- Read: `tests/test_emitters/test_python.py` (locate shell-step tests)
- Read: any current handling of `>=` / `<=` in CONTRACT ASSERTs (Pydantic field validators)

Five open questions from the spec already resolved during plan-write:

| # | Question | Answer (verified) |
|---|---|---|
| 1 | Multi-input FLOW expressions | ✅ Supported. `parse_step_call` (parser.py:1053) accepts comma-separated args; `_parse_call_arg` (parser.py:1093) treats bare `IDENT` as state shorthand `@name`. So `score_chunks(corpus, question)` and `answer(question, scored, corpus)` parse with no change. |
| 2 | `ShellImplIR` location | `clio/ir/graph.py:46`. AST counterpart at `clio/parser/ast_nodes.py:208`. Builder at `clio/ir/builder.py:248-261`. |
| 3 | `tests/test_examples_compile.py` existence | Does **not** exist. We add new compile-smoke tests inline within `tests/test_emitters/test_python.py` or a new `tests/test_examples.py` (see Task 7). |
| 4 | Pydantic `<=` in CONTRACT ASSERT | **Verify in this task.** Search the assert-compilation codepath for both `<=` and `>=`. If only `<` and `>` are honoured, switch the spec's `0.0 <= score <= 1.0` to `score >= 0.0 and score <= 1.0` form, or to two separate single-comparator constraints — whichever the existing parser accepts. |
| 5 | Working directory at runtime | **Verify in this task.** Read `clio/emitters/python.py` for the emitted `__main__.py` template. If the entrypoint does not `chdir` into the install/output dir, document in the rag_selfcontained README that the user must `cd ./out && rag_faq` (or pass an absolute path via `${file}` substitution). |

- [ ] **Step 1: Verify Pydantic comparator support**

```bash
grep -n "ASSERT\|assert\|comparator\|le_\|ge_" clio/emitters/_python_helpers.py | head
grep -n "<=\|>=" clio/emitters/_python_helpers.py | head
grep -n "<=\|>=\|comparator" clio/parser/parser.py | head
```

Then read the relevant blocks. Note in a comment whether `<=` and `>=` round-trip from `.clio` source to Pydantic field validator. Record the answer in the plan execution log (or as a code comment in Task 6's contract block).

- [ ] **Step 2: Verify runtime cwd**

```bash
grep -n "chdir\|os.getcwd\|cwd=\|cd " clio/emitters/_python_helpers.py clio/emitters/python.py | head
```

If no `chdir` happens before steps run, the `cat ${file}` resolves against the user's invocation cwd. Document this in Task 8's README content.

- [ ] **Step 3: Inspect existing shell-step emitter test**

```bash
grep -n "shell\|emit_shell_step\|impl.*shell" tests/test_emitters/test_python.py | head
```

Locate the closest existing test pattern to copy for Task 4's test additions.

- [ ] **Step 4: No commit**

This task produces no code changes — only knowledge that informs the next tasks. Move on.

---

## Task 2: Parser — recognise `parse:` field in `impl: mode: shell`

**Files:**
- Modify: `clio/parser/ast_nodes.py:208-214` (add `parse: str = "none"` to `ShellImpl`)
- Modify: `clio/parser/parser.py:425-466` (extend `_build_shell_impl` to parse + validate `parse:`)
- Test: `tests/test_parser.py` (3 new tests after the existing `test_parse_impl_shell_*` block, near line 440-485)

- [ ] **Step 1: Write three failing tests**

Add to `tests/test_parser.py`, immediately after `test_parse_impl_shell_unknown_field_raises`:

```python
def test_parse_impl_shell_with_parse_json():
    src = (
        "STEP load_corpus\n"
        "  TAKES: file: str\n"
        "  GIVES: corpus: List<str>\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode:  shell\n"
        '    cmd:   "cat ${file}"\n'
        "    parse: json\n"
    )
    program = parse(src)
    step = program.declarations[0]
    assert isinstance(step.impl, ShellImpl)
    assert step.impl.cmd == "cat ${file}"
    assert step.impl.parse == "json"


def test_parse_impl_shell_with_parse_none_explicit():
    src = (
        "STEP load_corpus\n"
        "  TAKES: file: str\n"
        "  GIVES: corpus: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode:  shell\n"
        '    cmd:   "cat ${file}"\n'
        "    parse: none\n"
    )
    program = parse(src)
    step = program.declarations[0]
    assert step.impl.parse == "none"


def test_parse_impl_shell_parse_invalid_value_raises():
    src = (
        "STEP load_corpus\n"
        "  TAKES: file: str\n"
        "  GIVES: corpus: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode:  shell\n"
        '    cmd:   "cat ${file}"\n'
        "    parse: yaml\n"
    )
    with pytest.raises(ParseError, match="impl.parse must be one of"):
        parse(src)
```

If `ShellImpl` import is not already at the top of `tests/test_parser.py`, add it to the existing `from clio.parser.ast_nodes import ...` line.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_parser.py::test_parse_impl_shell_with_parse_json tests/test_parser.py::test_parse_impl_shell_with_parse_none_explicit tests/test_parser.py::test_parse_impl_shell_parse_invalid_value_raises -v
```

Expected: 3 FAILs (the first two on `AttributeError: 'ShellImpl' object has no attribute 'parse'`, the third on `Failed: DID NOT RAISE`).

- [ ] **Step 3: Add `parse` field to `ShellImpl` AST node**

In `clio/parser/ast_nodes.py:207-215`, change:

```python
@dataclass(frozen=True)
class ShellImpl(ImplBlock):
    """impl.mode: shell — argv-style invocation of a shell command. The
    `cmd` is shlex-split at compile time; templating substitutes TAKES
    into per-token slots. No pipes/redirections (those need shell=True
    which is unsafe with user-provided strings)."""
    cmd: str
    timeout_seconds: int | None
    parse: str = "none"   # NEW: "none" (default — stdout str) | "json" (stdout json.loads'd at runtime)
```

The `= "none"` default keeps the dataclass backward-compatible: every existing `ShellImpl(cmd=..., timeout_seconds=...)` constructor call in code or tests continues to work.

- [ ] **Step 4: Extend `_build_shell_impl` in the parser**

In `clio/parser/parser.py:425-466`, modify the function:

```python
def _build_shell_impl(
    self,
    fields: dict[str, tuple[object, int, int]],
    line: int, col: int,
    mode_line: int, mode_col: int,
) -> ShellImpl:
    allowed = {"cmd", "timeout", "parse"}    # CHANGED: add "parse"
    unknown = set(fields.keys()) - allowed
    if unknown:
        sample = sorted(unknown)[0]
        _, fline, fcol = fields[sample]
        raise ParseError(
            f"unknown field {sample!r} for impl.mode: shell "
            f"(allowed: {sorted(allowed)})",
            fline, fcol,
        )
    if "cmd" not in fields:
        raise ParseError(
            "impl.mode: shell requires 'cmd' (a quoted string, e.g. \"pdftotext ${file} -\")",
            mode_line, mode_col,
        )
    cmd_value, cline, ccol = fields["cmd"]
    if not isinstance(cmd_value, str):
        raise ParseError(
            f"impl.cmd must be a quoted string, got {type(cmd_value).__name__}",
            cline, ccol,
        )

    timeout_seconds = None
    if "timeout" in fields:
        to, tline, tcol = fields["timeout"]
        if not isinstance(to, int):
            raise ParseError(
                f"impl.timeout must be a duration (e.g. 30s, 2m), got {to!r}",
                tline, tcol,
            )
        timeout_seconds = to

    parse_value = "none"                       # NEW: parse field handling
    if "parse" in fields:
        pv, pline, pcol = fields["parse"]
        if not isinstance(pv, str) or pv not in ("none", "json"):
            raise ParseError(
                f"impl.parse must be one of: none, json (got {pv!r})",
                pline, pcol,
            )
        parse_value = pv

    return ShellImpl(
        line=line, col=col,
        cmd=cmd_value,
        timeout_seconds=timeout_seconds,
        parse=parse_value,                     # NEW
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_parser.py -v -k "impl_shell"
```

Expected: 3 new tests PASS, plus the 4 pre-existing `impl_shell_*` tests still PASS (regression guard).

- [ ] **Step 6: Run the full parser test suite**

```bash
pytest tests/test_parser.py -v
```

Expected: all PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add clio/parser/ast_nodes.py clio/parser/parser.py tests/test_parser.py
git commit -m "feat(parser): impl.shell parse: json|none field"
```

---

## Task 3: IR — propagate `parse` to `ShellImplIR`

**Files:**
- Modify: `clio/ir/graph.py:45-52` (add `parse: str = "none"` to `ShellImplIR`)
- Modify: `clio/ir/builder.py:248-261` (propagate `decl.parse` into `ShellImplIR(...)`)
- Test: `tests/test_ir.py` (2 new tests after `test_build_ir_impl_shell_empty_cmd_raises`, line 323+)

- [ ] **Step 1: Write two failing tests**

Add to `tests/test_ir.py`:

```python
def test_build_ir_propagates_parse_json_to_shell_impl():
    src = (
        "STEP load_corpus\n"
        "  TAKES: file: str\n"
        "  GIVES: corpus: List<str>\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode:  shell\n"
        '    cmd:   "cat ${file}"\n'
        "    parse: json\n"
    )
    program = parse(src)
    ir = build_ir(program)
    step = ir.steps_by_name["load_corpus"]
    assert isinstance(step.impl, ShellImplIR)
    assert step.impl.argv == ("cat", "${file}")
    assert step.impl.parse == "json"


def test_build_ir_default_parse_is_none():
    src = (
        "STEP extract_pdf\n"
        "  TAKES: file: str\n"
        "  GIVES: text: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: shell\n"
        '    cmd:  "pdftotext ${file} -"\n'
    )
    program = parse(src)
    ir = build_ir(program)
    step = ir.steps_by_name["extract_pdf"]
    assert step.impl.parse == "none"
```

If `ShellImplIR` import is not at the top of `tests/test_ir.py`, add it to the existing `from clio.ir.graph import ...` line.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_ir.py::test_build_ir_propagates_parse_json_to_shell_impl tests/test_ir.py::test_build_ir_default_parse_is_none -v
```

Expected: 2 FAILs on `AttributeError: 'ShellImplIR' object has no attribute 'parse'`.

- [ ] **Step 3: Add `parse` field to `ShellImplIR`**

In `clio/ir/graph.py:45-52`, change:

```python
@dataclass(frozen=True)
class ShellImplIR(ImplIR):
    """impl.mode: shell — argv-style command. `argv` is the shlex-split
    template; tokens may contain `${var}` placeholders that the emitters
    substitute at runtime."""
    argv: tuple[str, ...]
    timeout_seconds: int | None
    parse: str = "none"   # NEW: "none" | "json"
```

- [ ] **Step 4: Propagate `parse` in the IR builder**

In `clio/ir/builder.py:248-261`, change the `ShellImpl` branch:

```python
if isinstance(decl, ShellImpl):
    import shlex
    try:
        argv = tuple(shlex.split(decl.cmd))
    except ValueError as e:
        raise IRBuildError(
            f"line {decl.line}: impl.cmd is not a valid shell tokenization "
            f"({e}); fix unbalanced quotes or escapes"
        ) from e
    if not argv:
        raise IRBuildError(
            f"line {decl.line}: impl.cmd must contain at least one token"
        )
    return ShellImplIR(
        argv=argv,
        timeout_seconds=decl.timeout_seconds,
        parse=decl.parse,    # NEW
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_ir.py -v -k "impl_shell or parse_json or default_parse"
```

Expected: all related tests PASS, including pre-existing `test_build_ir_propagates_impl_shell_shlex_split` and friends (regression guard).

- [ ] **Step 6: Run the full IR test suite**

```bash
pytest tests/test_ir.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add clio/ir/graph.py clio/ir/builder.py tests/test_ir.py
git commit -m "feat(ir): propagate impl.shell parse field to ShellImplIR"
```

---

## Task 4: Python emitter — honour `parse: json` in `emit_shell_step`

**Files:**
- Modify: `clio/emitters/_python_helpers.py:597-652` (`emit_shell_step` function)
- Test: `tests/test_emitters/test_python.py` (2 new tests; locate the existing shell-step test in Task 1 step 3 to know the section)

- [ ] **Step 1: Write two failing tests**

Add to `tests/test_emitters/test_python.py` (next to existing shell-step tests):

```python
def test_emit_shell_step_with_parse_json_imports_json_and_calls_loads():
    src = (
        "STEP load_corpus\n"
        "  TAKES: file: str\n"
        "  GIVES: corpus: List<str>\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode:  shell\n"
        '    cmd:   "cat ${file}"\n'
        "    parse: json\n"
        "FLOW pipe\n"
        '  load_corpus(file="x")\n'
        "RESOURCES\n"
        "  target: python\n"
        "  models: [haiku]\n"
    )
    program = parse(src)
    ir = build_ir(program)
    files = PythonEmitter().emit(ir)
    body = files["pipe/steps/load_corpus.py"]
    assert "import json" in body
    assert "json.loads(result.stdout)" in body
    assert "return result.stdout" not in body


def test_emit_shell_step_default_parse_returns_stdout_string():
    """Regression guard for v0.4 behaviour — parse=none keeps the legacy emit."""
    src = (
        "STEP extract_pdf\n"
        "  TAKES: file: str\n"
        "  GIVES: text: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: shell\n"
        '    cmd:  "pdftotext ${file} -"\n'
        "FLOW pipe\n"
        '  extract_pdf(file="x")\n'
        "RESOURCES\n"
        "  target: python\n"
        "  models: [haiku]\n"
    )
    program = parse(src)
    ir = build_ir(program)
    files = PythonEmitter().emit(ir)
    body = files["pipe/steps/extract_pdf.py"]
    assert "return result.stdout" in body
    assert "json.loads" not in body
    # No `import json` either, since the step does not need it.
    assert "import json" not in body
```

The exact path under `files[...]` may differ — check Task 1 step 3 for the existing test pattern. If the emitter returns a different mapping (e.g. nested by module), adapt the keys accordingly.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_emitters/test_python.py -v -k "parse_json or default_parse_returns_stdout"
```

Expected: 2 FAILs (the first because `json.loads` is missing; the second passes vacuously by current behaviour but the assertion `import json not in body` may already pass — that's fine, test stays as a regression guard).

- [ ] **Step 3: Modify `emit_shell_step` to honour `parse: json`**

In `clio/emitters/_python_helpers.py:597-652`, change the function:

```python
def emit_shell_step(
    step: StepIR,
    contracts_by_name: dict[str, ContractIR],
    impl: ShellImplIR,
) -> str:
    """Emit a shell-impl exact step. Shared by python and mcp-server targets."""
    params = _step_signature(step, contracts_by_name)
    ret_type = (
        _type_to_python(step.gives.type, contracts_by_name)
        if step.gives is not None else "None"
    )
    takes_doc = (
        "\n    ".join(f"{t.name}: {_render_type_short(t.type)}" for t in step.takes)
        if step.takes else "(no TAKES)"
    )
    gives_doc = (
        f"{step.gives.name}: {_render_type_short(step.gives.type)}"
        if step.gives is not None else "(no GIVES)"
    )

    argv_repr = "[" + ", ".join(repr(t) for t in impl.argv) + "]"
    sub_lines = [
        f"    _argv = [_t.replace('${{{t.name}}}', str({_to_field_name(t.name)})) for _t in _argv]"
        for t in step.takes
    ]
    sub_block = ("\n".join(sub_lines) + "\n") if sub_lines else ""

    timeout_arg = (
        f"timeout={impl.timeout_seconds}"
        if impl.timeout_seconds is not None else "timeout=None"
    )

    # NEW: parse:json branch — json.loads + extra import
    if impl.parse == "json":
        json_import = "import json\n"
        return_line = "    return json.loads(result.stdout)\n"
    else:
        json_import = ""
        return_line = "    return result.stdout\n"

    return (
        f'"""STEP {step.name} (exact, impl: shell)\n'
        f'TAKES:\n'
        f'    {takes_doc}\n'
        f'GIVES:\n'
        f'    {gives_doc}\n\n'
        f'Auto-generated from `impl: mode: shell`. Argv-style invocation —\n'
        f'no shell pipes/redirections (subprocess.run is called with shell=False).\n'
        f'TAKES are substituted into argv tokens via ${{var}} placeholders.\n'
        f'"""\n'
        f'from __future__ import annotations\n\n'
        f'import subprocess\n'
        f'{json_import}'
        f'import time\n\n'
        f'from ..clio_runtime import logging as _log\n\n\n'
        f'def {step.name}({params}) -> {ret_type}:\n'
        f'    _t0 = time.monotonic()\n'
        f'    _log.emit("step_start", step={step.name!r}, mode="exact")\n'
        f'    _argv = {argv_repr}\n'
        f'{sub_block}'
        f'    result = subprocess.run(_argv, capture_output=True, text=True, check=True, {timeout_arg})\n'
        f'    _log.emit("step_end", step={step.name!r}, mode="exact",\n'
        f'              duration_ms=int((time.monotonic() - _t0) * 1000), success=True)\n'
        f'{return_line}'
    )
```

The `import json` is placed between `import subprocess` and `import time` (alphabetic-ish, matches the surrounding style).

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_emitters/test_python.py -v -k "parse_json or default_parse_returns_stdout"
```

Expected: 2 PASS.

- [ ] **Step 5: Run the full python emitter test suite**

```bash
pytest tests/test_emitters/test_python.py -v
```

Expected: all PASS, no regressions.

- [ ] **Step 6: Run the full unit suite**

```bash
pytest tests/ -v --ignore=tests/test_e2e_resume.py --ignore=tests/test_e2e_logging.py
```

Expected: 371 + 5 new = 376 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add clio/emitters/_python_helpers.py tests/test_emitters/test_python.py
git commit -m "feat(emitter/python): impl.shell parse: json wraps stdout in json.loads"
```

---

## Task 5: Lock fixture — minimal `parse: json` `.clio` round-trip

**Files:**
- Create: `tests/fixtures/shell_parse_json.clio`
- Create: `tests/fixtures/expected/shell_parse_json/<pkg>/steps/load_corpus.py`
- Create: `tests/fixtures/expected/shell_parse_json/<pkg>/...` (whatever else the emitter writes — pyproject, __init__, flow, __main__)
- Modify: `tests/test_emitters/test_python.py` (one new test that compares emit output byte-by-byte against the expected fixture)

The exact subdirectory names depend on the emitter convention used by other fixtures (e.g. `tests/fixtures/expected/python_v03_mvp/customer_retention/...`). Mirror the closest existing fixture's structure.

- [ ] **Step 1: Find the closest existing fixture pattern**

```bash
ls tests/fixtures/expected/ | head
ls tests/fixtures/expected/python_v03_mvp/ 2>/dev/null
grep -rn "tests/fixtures/expected" tests/test_emitters/ | head
```

Note the pattern (e.g. fixture name → subdirectory under `expected/`, with the package name as the next level).

- [ ] **Step 2: Create the source fixture**

Create `tests/fixtures/shell_parse_json.clio`:

```
STEP load_corpus
  TAKES: file:   str
  GIVES: corpus: List<str>
  MODE:  exact
  impl:
    mode:  shell
    cmd:   "cat ${file}"
    parse: json

FLOW shell_parse_pipe
  load_corpus(file="data.json")

RESOURCES
  target: python
  models: [haiku]
```

- [ ] **Step 3: Generate the expected emit output once, manually**

```bash
python -m clio compile tests/fixtures/shell_parse_json.clio --target python --output /tmp/_lock_check
ls /tmp/_lock_check/
```

Inspect the output. Copy it to `tests/fixtures/expected/shell_parse_json/` mirroring its structure. Then verify by eye that `steps/load_corpus.py` contains:
- `import json`
- `return json.loads(result.stdout)`

Once content is correct, copy the directory tree into the fixture location:

```bash
mkdir -p tests/fixtures/expected/shell_parse_json
cp -r /tmp/_lock_check/* tests/fixtures/expected/shell_parse_json/
```

- [ ] **Step 4: Add the lock test**

Add to `tests/test_emitters/test_python.py`, following any existing fixture-comparison helper (look for `_assert_emit_matches_fixture` or similar):

```python
def test_emit_shell_parse_json_fixture_locked():
    """Byte-identical lock for the parse:json shell-step fixture. Detects
    accidental drift in emit output (whitespace, ordering, imports)."""
    src = Path("tests/fixtures/shell_parse_json.clio").read_text()
    expected_root = Path("tests/fixtures/expected/shell_parse_json")
    program = parse(src)
    ir = build_ir(program)
    files = PythonEmitter().emit(ir)
    for rel_path, content in files.items():
        expected_file = expected_root / rel_path
        assert expected_file.exists(), f"missing expected fixture: {expected_file}"
        assert content == expected_file.read_text(), (
            f"emit drift in {rel_path}:\n"
            f"--- expected\n{expected_file.read_text()}\n"
            f"+++ actual\n{content}"
        )
```

If the existing tests use a helper, prefer the helper for consistency. If `Path` import is missing, add `from pathlib import Path` near the other imports.

- [ ] **Step 5: Run the lock test**

```bash
pytest tests/test_emitters/test_python.py::test_emit_shell_parse_json_fixture_locked -v
```

Expected: PASS (since the fixture was generated from the current emitter).

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/ -v --ignore=tests/test_e2e_resume.py --ignore=tests/test_e2e_logging.py
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures/shell_parse_json.clio tests/fixtures/expected/shell_parse_json/ tests/test_emitters/test_python.py
git commit -m "test(emitter/python): byte-identical fixture lock for parse:json"
```

---

## Task 6: Author `examples/rag_basic.clio` + data + smoke compile

**Files:**
- Create: `examples/rag_basic.clio`
- Create: `examples/faq.txt`
- Create: `examples/question.txt`
- Test: `tests/test_examples.py` (new file) — smoke compile checks for both rag_basic and rag_selfcontained (we add the rag_basic check now, rag_selfcontained in Task 7)

- [ ] **Step 1: Write the smoke test first (will fail because the file doesn't exist yet)**

Create `tests/test_examples.py`:

```python
"""Smoke tests for the polished examples in examples/. Each test compiles
the .clio file via the public CLI (or the IR build path) and asserts that:
  - parse + build_ir + emit succeed
  - the expected step files are emitted
  - no obviously-broken stub bodies are present in self-contained variants

These are not E2E tests (no Anthropic call). They guard against regressions
in the examples themselves and the emitters when the language extends."""
from __future__ import annotations

from pathlib import Path

from clio.ir.builder import build_ir
from clio.parser.parser import parse
from clio.emitters.python import PythonEmitter


REPO_ROOT = Path(__file__).resolve().parent.parent


def _compile_to_files(clio_path: Path) -> dict[str, str]:
    src = clio_path.read_text()
    program = parse(src)
    ir = build_ir(program)
    return PythonEmitter().emit(ir)


def test_compile_rag_basic_example():
    files = _compile_to_files(REPO_ROOT / "examples/rag_basic.clio")
    # 4 steps + flow + __main__ + __init__ + pyproject
    step_files = [k for k in files if k.endswith(".py") and "/steps/" in k]
    step_names = {Path(k).stem for k in step_files}
    assert step_names == {"load_corpus", "load_question", "score_chunks", "answer"}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_examples.py::test_compile_rag_basic_example -v
```

Expected: FAIL — `FileNotFoundError: examples/rag_basic.clio`.

- [ ] **Step 3: Create `examples/rag_basic.clio`**

Create `examples/rag_basic.clio` with this content (verbatim):

```
CONTRACT chunk
  SHAPE:  {id: int, text: str(max=1000)}
  ASSERT: id >= 1

CONTRACT scored_chunk
  SHAPE:  {id: int, score: float, reason: str(max=200)}
  ASSERT: score >= 0.0

CONTRACT rag_answer
  SHAPE:  {answer: str(max=2000), citations: List<int>}
  ASSERT: len(answer) > 0

STEP load_corpus
  TAKES: file:   str
  GIVES: corpus: List<chunk>
  MODE:  exact

STEP load_question
  TAKES: file:     str
  GIVES: question: str
  MODE:  exact

STEP score_chunks
  TAKES:   corpus:   List<chunk>
           question: str
  GIVES:   scored:   List<scored_chunk>
  MODE:    judgment
  CACHE:   ttl(7d)
  ON_FAIL: retry(3) then escalate then abort("scoring failed")

STEP answer
  TAKES:   question: str
           scored:   List<scored_chunk>
           corpus:   List<chunk>
  GIVES:   response: rag_answer
  MODE:    judgment
  ON_FAIL: retry(2) then escalate then abort("answer failed")

FLOW rag_faq
  load_corpus(file="faq.txt")
    -> load_question(file="question.txt")
    -> score_chunks(corpus, question)
    -> answer(question, scored, corpus)

RESOURCES
  target: python
  models: [haiku, sonnet, opus]
```

Note on the ASSERTs: the spec calls for `0.0 <= score <= 1.0` for `scored_chunk`. The plan uses `score >= 0.0` only — this is the conservative form pending Task 1's verification of `<=` support in the assert compiler. If Task 1 confirmed both `<=` and `>=` work, replace with `0.0 <= score and score <= 1.0` (or whatever syntax is supported); otherwise keep the single-comparator form documented here.

- [ ] **Step 4: Create `examples/faq.txt`**

Create `examples/faq.txt`:

```
Q: Comment annuler mon abonnement ? R: Pour annuler votre abonnement, allez dans Compte > Abonnements et cliquez sur "Résilier". L'annulation prend effet à la fin de la période en cours déjà payée. Aucune pénalité.

Q: Quel est le délai de remboursement ? R: Le délai de remboursement est de 14 jours après la souscription, hors période d'essai déjà consommée. Au-delà, seuls les abonnements annuels non utilisés peuvent être remboursés au prorata sur demande au support.

Q: Comment changer de plan tarifaire ? R: Allez dans Compte > Plan et sélectionnez le nouveau plan. Le changement prend effet immédiatement, avec proratisation automatique de la différence sur la facture suivante.

Q: Comment ajouter des membres à mon équipe ? R: Dans Compte > Équipe, cliquez sur "Inviter". Chaque membre ajouté augmente votre facture mensuelle selon le tarif "siège" du plan choisi. Les invitations expirent après 7 jours.

Q: Mes données sont-elles chiffrées ? R: Oui — TLS 1.3 en transit, AES-256 au repos. Les clés sont gérées par AWS KMS avec rotation annuelle automatique. Aucun employé n'a accès aux données client en clair.

Q: Comment exporter mes données (RGPD) ? R: Compte > Données personnelles > "Exporter mes données" génère une archive ZIP envoyée à votre adresse email vérifiée sous 48h. L'export inclut profil, historique d'usage et fichiers stockés.

Q: Comment contacter le support technique ? R: Le support technique est joignable via Compte > Aide > "Ouvrir un ticket". Délai de réponse standard : 24h ouvrées. Les abonnements Pro et Enterprise bénéficient d'un canal prioritaire avec réponse sous 4h ouvrées.

Q: Que se passe-t-il si je ne paie pas ma facture ? R: Après 7 jours de retard, l'accès passe en mode lecture seule. Après 30 jours, le compte est suspendu. Après 90 jours sans régularisation, les données sont supprimées définitivement conformément à notre politique de rétention.
```

- [ ] **Step 5: Create `examples/question.txt`**

Create `examples/question.txt` (single line, no trailing newline issue tolerated):

```
Comment annuler mon abonnement et obtenir un remboursement ?
```

- [ ] **Step 6: Run the smoke test**

```bash
pytest tests/test_examples.py::test_compile_rag_basic_example -v
```

Expected: PASS.

- [ ] **Step 7: Sanity-check via the CLI**

```bash
python -m clio check examples/rag_basic.clio
python -m clio compile examples/rag_basic.clio --target python --output /tmp/_rag_basic
ls /tmp/_rag_basic/rag_faq/steps/
```

Expected: 4 step files (`load_corpus.py`, `load_question.py`, `score_chunks.py`, `answer.py`). The two exact stubs contain `raise NotImplementedError(...)` or similar — that's expected for the manual-edit pattern.

- [ ] **Step 8: Commit**

```bash
git add examples/rag_basic.clio examples/faq.txt examples/question.txt tests/test_examples.py
git commit -m "feat(examples): rag_basic.clio + faq.txt + question.txt + smoke compile"
```

---

## Task 7: Author `examples/rag_selfcontained.clio` + faq.json + smoke compile

**Files:**
- Create: `examples/rag_selfcontained.clio`
- Create: `examples/faq.json`
- Modify: `tests/test_examples.py` (add `test_compile_rag_selfcontained_example`)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_examples.py`:

```python
def test_compile_rag_selfcontained_example():
    files = _compile_to_files(REPO_ROOT / "examples/rag_selfcontained.clio")
    step_files = {k: v for k, v in files.items() if k.endswith(".py") and "/steps/" in k}
    step_names = {Path(k).stem for k in step_files}
    assert step_names == {"load_corpus", "load_question", "score_chunks", "answer"}

    # The two loader steps must be impl.shell — no NotImplementedError stub.
    for loader in ("load_corpus", "load_question"):
        body = next(v for k, v in step_files.items() if Path(k).stem == loader)
        assert "subprocess.run" in body, f"{loader} should use impl.shell (subprocess)"
        assert "NotImplementedError" not in body, f"{loader} should not be a stub"

    # load_corpus specifically uses parse:json → json.loads.
    load_corpus_body = next(
        v for k, v in step_files.items() if Path(k).stem == "load_corpus"
    )
    assert "import json" in load_corpus_body
    assert "json.loads(result.stdout)" in load_corpus_body
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_examples.py::test_compile_rag_selfcontained_example -v
```

Expected: FAIL — file not found.

- [ ] **Step 3: Create `examples/rag_selfcontained.clio`**

```
CONTRACT chunk
  SHAPE:  {id: int, text: str(max=1000)}
  ASSERT: id >= 1

CONTRACT scored_chunk
  SHAPE:  {id: int, score: float, reason: str(max=200)}
  ASSERT: score >= 0.0

CONTRACT rag_answer
  SHAPE:  {answer: str(max=2000), citations: List<int>}
  ASSERT: len(answer) > 0

STEP load_corpus
  TAKES: file:   str
  GIVES: corpus: List<chunk>
  MODE:  exact
  impl:
    mode:  shell
    cmd:   "cat ${file}"
    parse: json

STEP load_question
  TAKES: file:     str
  GIVES: question: str
  MODE:  exact
  impl:
    mode: shell
    cmd:  "cat ${file}"

STEP score_chunks
  TAKES:   corpus:   List<chunk>
           question: str
  GIVES:   scored:   List<scored_chunk>
  MODE:    judgment
  CACHE:   ttl(7d)
  ON_FAIL: retry(3) then escalate then abort("scoring failed")

STEP answer
  TAKES:   question: str
           scored:   List<scored_chunk>
           corpus:   List<chunk>
  GIVES:   response: rag_answer
  MODE:    judgment
  ON_FAIL: retry(2) then escalate then abort("answer failed")

FLOW rag_faq
  load_corpus(file="faq.json")
    -> load_question(file="question.txt")
    -> score_chunks(corpus, question)
    -> answer(question, scored, corpus)

RESOURCES
  target: python
  models: [haiku, sonnet, opus]
```

Note: same FLOW name `rag_faq` as `rag_basic.clio` — the package name will collide if both are compiled into the same output directory, but separate output directories (one per .clio) is the expected workflow, so it's fine.

- [ ] **Step 4: Create `examples/faq.json`**

```json
[
  {"id": 1, "text": "Q: Comment annuler mon abonnement ? R: Pour annuler votre abonnement, allez dans Compte > Abonnements et cliquez sur Résilier. L'annulation prend effet à la fin de la période en cours déjà payée. Aucune pénalité."},
  {"id": 2, "text": "Q: Quel est le délai de remboursement ? R: Le délai de remboursement est de 14 jours après la souscription, hors période d'essai déjà consommée. Au-delà, seuls les abonnements annuels non utilisés peuvent être remboursés au prorata sur demande au support."},
  {"id": 3, "text": "Q: Comment changer de plan tarifaire ? R: Allez dans Compte > Plan et sélectionnez le nouveau plan. Le changement prend effet immédiatement, avec proratisation automatique de la différence sur la facture suivante."},
  {"id": 4, "text": "Q: Comment ajouter des membres à mon équipe ? R: Dans Compte > Équipe, cliquez sur Inviter. Chaque membre ajouté augmente votre facture mensuelle selon le tarif siège du plan choisi. Les invitations expirent après 7 jours."},
  {"id": 5, "text": "Q: Mes données sont-elles chiffrées ? R: Oui — TLS 1.3 en transit, AES-256 au repos. Les clés sont gérées par AWS KMS avec rotation annuelle automatique. Aucun employé n'a accès aux données client en clair."},
  {"id": 6, "text": "Q: Comment exporter mes données (RGPD) ? R: Compte > Données personnelles > Exporter mes données génère une archive ZIP envoyée à votre adresse email vérifiée sous 48h. L'export inclut profil, historique d'usage et fichiers stockés."},
  {"id": 7, "text": "Q: Comment contacter le support technique ? R: Le support technique est joignable via Compte > Aide > Ouvrir un ticket. Délai de réponse standard 24h ouvrées. Les abonnements Pro et Enterprise bénéficient d'un canal prioritaire avec réponse sous 4h ouvrées."},
  {"id": 8, "text": "Q: Que se passe-t-il si je ne paie pas ma facture ? R: Après 7 jours de retard, l'accès passe en mode lecture seule. Après 30 jours, le compte est suspendu. Après 90 jours sans régularisation, les données sont supprimées définitivement conformément à notre politique de rétention."}
]
```

(Note: double-quotes have been removed from the source paragraphs to keep JSON parseable. The ` ` characters and accents are valid UTF-8 JSON strings.)

- [ ] **Step 5: Run the smoke test**

```bash
pytest tests/test_examples.py::test_compile_rag_selfcontained_example -v
```

Expected: PASS.

- [ ] **Step 6: Sanity-check via CLI + actually run the loaders manually**

```bash
python -m clio check examples/rag_selfcontained.clio
python -m clio compile examples/rag_selfcontained.clio --target python --output /tmp/_rag_self
cat /tmp/_rag_self/rag_faq/steps/load_corpus.py | head -40
```

Expected: emitted `load_corpus.py` shows `subprocess.run`, `json.loads(result.stdout)`, and no `NotImplementedError`.

Optional manual sanity:

```bash
cp examples/faq.json examples/question.txt /tmp/_rag_self/
cd /tmp/_rag_self/rag_faq/steps && python -c "from load_corpus import load_corpus; print(load_corpus(file='faq.json')[:2])"
```

Expected: prints the first two chunk dicts. (This step is local validation — not asserted by tests because it requires the runtime context CLIO sets up.)

- [ ] **Step 7: Commit**

```bash
git add examples/rag_selfcontained.clio examples/faq.json tests/test_examples.py
git commit -m "feat(examples): rag_selfcontained.clio + faq.json (impl.shell parse:json)"
```

---

## Task 8: Update `examples/README.md`

**Files:**
- Modify: `examples/README.md` (append a 4th section after the 3rd; preserve existing 3 sections verbatim)

- [ ] **Step 1: Read the existing README to understand the section style**

```bash
cat examples/README.md
```

Note the heading levels (`##` for top-level examples, `###` for subsections), the run-instruction block style (triple-backtick bash), and the "What this example exercises that the others don't" pattern.

- [ ] **Step 2: Append the new section**

Append to `examples/README.md` (verbatim, after the existing section 3):

````markdown

## 4. `rag_basic.clio` / `rag_selfcontained.clio` — RAG-like (LLM-as-retriever)

Pipeline: load corpus + question → score each chunk via an LLM (with reasoning) →
answer the question quoting cited chunk ids. No embeddings, no vector store —
the LLM is both retriever and generator.

What these examples exercise that the others do not:

- Three CONTRACTs in the same flow (`chunk`, `scored_chunk`, `rag_answer`).
- A numeric ASSERT (`score >= 0.0`) compiled into a Pydantic `@field_validator`.
- Multi-input judgment steps: `score_chunks(corpus, question)` and
  `answer(question, scored, corpus)` — three TAKES references in one call.
- `citations: List<int>` forcing the LLM to ground its answer in source ids.

### Two variants — same flow, different load strategy

| Variant | `load_corpus` | `load_question` | Manual edit needed |
|---|---|---|---|
| `rag_basic.clio` | stub (default `MODE: exact`) | stub (default `MODE: exact`) | yes — two short Python helpers (~10 lines each) |
| `rag_selfcontained.clio` | `impl.shell` + `parse: json` on `faq.json` | `impl.shell` (`cat ${file}`) | none — compile-and-run |

### Run `rag_basic.clio`

```bash
uv run python -m clio compile examples/rag_basic.clio --target python --output ./out
# Edit ./out/rag_faq/steps/load_corpus.py:
#
#   def load_corpus(file: str) -> list[Chunk]:
#       paragraphs = Path(file).read_text().split("\n\n")
#       return [Chunk(id=i+1, text=p.strip()) for i, p in enumerate(paragraphs) if p.strip()]
#
# Edit ./out/rag_faq/steps/load_question.py:
#
#   def load_question(file: str) -> str:
#       return Path(file).read_text().strip()
#
cp examples/faq.txt examples/question.txt ./out/
uv pip install ./out
ANTHROPIC_API_KEY=... rag_faq
```

### Run `rag_selfcontained.clio` (zero edits)

```bash
uv run python -m clio compile examples/rag_selfcontained.clio --target python --output ./out
cp examples/faq.json examples/question.txt ./out/
uv pip install ./out
cd ./out && ANTHROPIC_API_KEY=... rag_faq
# (cd into ./out is needed because cmd: "cat ${file}" resolves the path
# relative to the entrypoint's cwd.)
```

### Why two variants

`rag_basic.clio` is the canonical pattern (matches `mvp.clio`, `entities.clio`,
`classify_corpus.clio`): `MODE: exact` steps emit Python stubs the user fills.
Use it when the file format needs conversion (CSV → records, raw text → chunks
with custom splitting rules).

`rag_selfcontained.clio` demonstrates the v0.5 `impl.shell.parse: json`
extension: when the file already matches the `GIVES` shape (here a JSON array
of `{id, text}` pairs), the loader becomes a one-line `cat`, parsed
declaratively. No Python required.
````

The triple-backticks inside the new section are escaped with a leading
backtick-pair when needed (the outer fence is four-backticks). Make sure the
final file's fence count is correct after the append.

- [ ] **Step 3: Verify the markdown renders correctly**

```bash
head -200 examples/README.md
```

Visually scan for unbalanced fences or broken tables.

- [ ] **Step 4: Commit**

```bash
git add examples/README.md
git commit -m "docs(examples): README section for rag_basic + rag_selfcontained"
```

---

## Task 9: Update `docs/LANGUAGE_SPEC.md` + `CHANGELOG.md`

**Files:**
- Modify: `docs/LANGUAGE_SPEC.md` (the `#### impl.mode: shell` section + the v0 limitations table)
- Modify: `CHANGELOG.md` (add `### Language` and `### Examples` entries under `## Unreleased`)

- [ ] **Step 1: Read the existing `impl.mode: shell` section**

```bash
grep -n "impl.mode: shell\|#### .impl" docs/LANGUAGE_SPEC.md | head
```

Locate the section (around line 155-170 per the earlier read).

- [ ] **Step 2: Update LANGUAGE_SPEC.md**

In `docs/LANGUAGE_SPEC.md`, change the `#### impl.mode: shell` section to add the `parse:` field documentation. The exact diff:

```
#### `impl.mode: shell`

Shell command with templated arguments. Output captured from stdout.

\`\`\`
STEP extract_pdf
  MODE:    exact
  TAKES:   file: Path
  GIVES:   text: str
  impl:
    mode:    shell
    cmd:     "pdftotext ${file} -"
    parse:   none                            # NEW (since v0.5): default — stdout returned as str
    timeout: 60s
\`\`\`

The `cmd` is a quoted string. The compiler `shlex.split`s it at compile time, then templates `${var}` per token at runtime — `subprocess.run([...], shell=False)` runs the resulting argv. No pipes/redirections (wrap a pipeline in a script if needed). Non-zero exit codes raise `subprocess.CalledProcessError`, which `ON_FAIL` will see.

\`\`\`
STEP load_corpus
  MODE:    exact
  TAKES:   file: str
  GIVES:   corpus: List<chunk>
  impl:
    mode:    shell
    cmd:     "cat ${file}"
    parse:   json                             # NEW: stdout is parsed via json.loads
\`\`\`

**`parse:`** (optional, default `none`) — controls how stdout is returned to the flow:

| Value | Behaviour |
|---|---|
| `none` (default) | `result.stdout` returned as `str`. The step's `GIVES` must be a `str` for downstream Pydantic validation to pass. v0.4 behaviour. |
| `json` | `json.loads(result.stdout)` runs at the end of the step. The parsed object goes through `GIVES` validation as usual — supports `List<...>`, `Dict<...>`, scalars, nested CONTRACTs. `JSONDecodeError` propagates and `ON_FAIL` (if any) handles it. |

Other parse modes (`yaml`, `csv`, `lines`) are not supported in v0.5.
```

(Replace the placeholder backslash-escaped fences with literal triple-backtick fences when editing the file.)

Also update the "v0 limitations" table near line 33-39 to remove or update any entry that says "shell stdout is str only" — replace with "shell stdout is `str` unless `parse: json` is set".

- [ ] **Step 3: Update CHANGELOG.md**

Read the current `## Unreleased` section:

```bash
grep -n "## Unreleased\|## v0\." CHANGELOG.md | head
```

Under `## Unreleased`, add (or extend if subsections already exist):

```markdown
### Language

- `impl.mode: shell` accepts a new optional `parse:` field. Values: `none`
  (default — stdout returned as `str`, v0.4 behaviour) and `json` (stdout is
  passed through `json.loads` before `GIVES` validation, enabling
  `List<...>` / `Dict<...>` GIVES types from a `cat`-style command). Backward-
  compatible: every existing `.clio` file parses unchanged.

### Examples

- `examples/rag_basic.clio` — RAG-like pipeline (LLM-as-retriever) with the
  manual-edit loader pattern. Demonstrates 3 CONTRACTs, numeric ASSERT,
  multi-input judgment steps, and `citations: List<int>` for grounded answers.
- `examples/rag_selfcontained.clio` — same pipeline, zero-manual-edit using
  the new `impl.shell.parse: json`. Pair with `examples/faq.json`.
- `examples/faq.txt`, `examples/faq.json`, `examples/question.txt` — data
  fixtures shared by both variants.
- `examples/README.md` — new section 4 comparing the two variants.
```

- [ ] **Step 4: Run the full unit test suite for sanity**

```bash
pytest tests/ -v --ignore=tests/test_e2e_resume.py --ignore=tests/test_e2e_logging.py
```

Expected: all PASS — docs changes don't affect tests but verify nothing breaks anyway.

- [ ] **Step 5: Commit**

```bash
git add docs/LANGUAGE_SPEC.md CHANGELOG.md
git commit -m "docs: impl.shell parse:json + examples/rag_* in LANGUAGE_SPEC + CHANGELOG"
```

---

## Task 10 (optional): Gated E2E test for rag_selfcontained

This task is optional — skip if Task 7's `subprocess.run` introspection assertions are deemed sufficient. The benefit is an actual subprocess invocation against a real `cat` to verify the emitted runtime path works on the developer's machine.

**Files:**
- Modify: `tests/test_e2e_resume.py` or create `tests/test_e2e_examples.py`

- [ ] **Step 1: Write the gated E2E test**

Create `tests/test_e2e_examples.py`:

```python
"""Gated end-to-end tests for examples/. Set CLIO_E2E=1 to run.

These tests actually invoke `cat` via subprocess and verify the loaders
return correctly-shaped data. They do NOT call any LLM — judgment steps
are not exercised here.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    os.environ.get("CLIO_E2E") != "1",
    reason="set CLIO_E2E=1 to run end-to-end example tests",
)


def test_rag_selfcontained_loaders_run_against_real_files(tmp_path):
    """Compile rag_selfcontained, copy data files into the output, then
    import and call load_corpus + load_question directly.
    Verifies impl.shell + parse:json end-to-end without an LLM."""
    out_dir = tmp_path / "out"
    subprocess.run(
        [sys.executable, "-m", "clio", "compile",
         str(REPO_ROOT / "examples/rag_selfcontained.clio"),
         "--target", "python",
         "--output", str(out_dir)],
        check=True,
    )
    # Copy the data files into the output dir (the cmd uses relative paths).
    for fname in ("faq.json", "question.txt"):
        (out_dir / fname).write_bytes(
            (REPO_ROOT / "examples" / fname).read_bytes()
        )
    # Import the emitted package and call the loaders.
    sys.path.insert(0, str(out_dir))
    try:
        # The package name is rag_faq (the FLOW name).
        from rag_faq.steps.load_corpus import load_corpus  # type: ignore
        from rag_faq.steps.load_question import load_question  # type: ignore

        os.chdir(out_dir)  # cmd: "cat ${file}" runs in cwd
        corpus = load_corpus(file="faq.json")
        question = load_question(file="question.txt")
    finally:
        sys.path.remove(str(out_dir))
        # purge module cache so re-imports in other tests are clean
        for m in [k for k in list(sys.modules) if k.startswith("rag_faq")]:
            del sys.modules[m]

    assert isinstance(corpus, list)
    assert len(corpus) == 8
    assert all("id" in c and "text" in c for c in corpus)
    assert corpus[0]["id"] == 1
    assert isinstance(question, str)
    assert "annuler" in question.lower()
```

- [ ] **Step 2: Run the gated test**

```bash
CLIO_E2E=1 pytest tests/test_e2e_examples.py -v
```

Expected: PASS. (If FAIL, the most likely cause is path resolution — the cmd `"cat ${file}"` is relative to cwd, hence the explicit `os.chdir(out_dir)`.)

- [ ] **Step 3: Verify the gate works (test is skipped without env var)**

```bash
pytest tests/test_e2e_examples.py -v
```

Expected: 1 SKIP (with reason "set CLIO_E2E=1...").

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_examples.py
git commit -m "test(e2e): gated end-to-end test for rag_selfcontained loaders"
```

---

## Final verification & wrap-up

- [ ] **Step 1: Run the full unit suite**

```bash
pytest tests/ -v --ignore=tests/test_e2e_resume.py --ignore=tests/test_e2e_logging.py --ignore=tests/test_e2e_examples.py
```

Expected: 371 + ~10 new = ~381 PASS. No regressions.

- [ ] **Step 2: Run all gated E2E tests**

```bash
CLIO_E2E=1 pytest tests/test_e2e_resume.py tests/test_e2e_logging.py tests/test_e2e_examples.py -v
```

Expected: 12 + ~1 new = ~13 PASS.

- [ ] **Step 3: Sanity-check both example compilations**

```bash
python -m clio check examples/rag_basic.clio
python -m clio check examples/rag_selfcontained.clio
python -m clio compile examples/rag_basic.clio --target python --output /tmp/_check_basic
python -m clio compile examples/rag_selfcontained.clio --target python --output /tmp/_check_self
```

Expected: 4 OKs, 4 directories created.

- [ ] **Step 4: Verify the README renders**

```bash
cat examples/README.md | wc -l
```

Should be ~190 lines (existing 113 + ~80 new).

- [ ] **Step 5: Final commit (if any docs polish needed)**

If everything is green, the previous commits are sufficient. If a polish-only commit is needed (typos, formatting), make it now.

```bash
git log --oneline | head -12
```

Expected: 8-10 new commits since the spec commit (`159f6af`).

---

## Self-review checklist (run before handing off)

- ✅ **Spec coverage**: Parts A (language extension), B (common pipeline), C (rag_basic), D (rag_selfcontained), E (data files), F (README) → tasks 2-5, 6, 7, 8 respectively. LANGUAGE_SPEC + CHANGELOG → task 9. E2E → task 10.
- ✅ **Acceptance criteria from spec**: all 7 criteria mapped (suite green, `clio check` passes both, no `NotImplementedError` in selfcontained loaders, end-to-end run produces non-empty answer/citations [task 10], README has new section, LANGUAGE_SPEC updated, CHANGELOG entry).
- ✅ **No placeholders**: every code block is concrete; every test has actual assertions; every commit message is final.
- ✅ **Type consistency**: `parse: str = "none"` field name and "none"/"json" values match across `ShellImpl` (parser AST) → `ShellImplIR` (IR) → `emit_shell_step` (emitter). Tests reference the same field name. Examples use the same literal values.
- ✅ **Open questions handled**: 3/5 resolved during plan-write (multi-input FLOW, ShellImplIR location, no test_examples_compile.py), 2/5 deferred to Task 1 (Pydantic `<=`, runtime cwd) with explicit fallback (single-comparator ASSERT, README `cd ./out` instruction).
