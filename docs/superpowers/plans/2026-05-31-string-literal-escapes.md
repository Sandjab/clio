# String-literal escapes (`\"` / `\\`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a CLIO single-line string literal `"…"` contain `\"` (literal double-quote) and `\\` (literal backslash), so an inline `impl` shell `cmd:` emitting JSON is expressible. Closes #88.

**Architecture:** One production-code change — the lexer's string scanner (`clio/parser/lexer.py`) gains escape consumption for `\"` and `\\` only; any other `\x` stays a literal backslash. A planning-time audit confirmed every downstream emitter already escapes string values safely (Python/langgraph/mcp/claude-skill via `repr()`, Go via manual escape; claude-cli does not render conditions), so no emitter change is needed.

**Tech Stack:** Python 3.12, pytest. Validators run via the project venv: `.venv/bin/python -m clio …`, `.venv/bin/pytest`.

**Spec:** `docs/superpowers/specs/2026-05-31-string-literal-escapes-design.md`

---

### Task 1: Lexer — consume `\"` and `\\` escapes (TDD)

**Files:**
- Modify: `clio/parser/lexer.py:74-83` (the `if ch == '"':` string scanner)
- Test: `tests/test_lexer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_lexer.py` (the file already imports `lex` and `TokenType`):

```python
def _string_value(src):
    toks = lex(src)
    return next(t for t in toks if t.type == TokenType.STRING).value


def test_lex_string_escaped_quote():
    # .clio source:  A "say \"hi\""
    assert _string_value('A "say \\"hi\\""\n') == 'say "hi"'


def test_lex_string_escaped_backslash():
    # .clio source:  "a\\b"  -> value  a\b
    assert _string_value('"a\\\\b"\n') == 'a\\b'


def test_lex_string_lone_backslash_preserved():
    # .clio source:  "C:\foo"  (\ not before " or \)  -> value unchanged  C:\foo
    assert _string_value('"C:\\foo"\n') == 'C:\\foo'


def test_lex_string_json_cmd_value():
    # the issue #88 motivating case, as it appears in a cmd: line
    assert _string_value('cmd: "echo \'{\\"available\\": true}\'"\n') == 'echo \'{"available": true}\''


def test_lex_string_unterminated_after_trailing_escaped_quote():
    import pytest
    from clio.parser.lexer import LexError
    # .clio source:  "foo\"   -> the \" escapes the quote, so the literal is unterminated
    with pytest.raises(LexError) as exc:
        lex('"foo\\"\n')
    assert "unterminated string literal" in str(exc.value)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/pytest tests/test_lexer.py -k "escaped or backslash or json_cmd or unterminated_after" -v`
Expected: the escape/backslash/json tests FAIL (current lexer stops at the first inner `"`; the JSON one raises `LexError: unexpected character '\'`). `test_lex_string_lone_backslash_preserved` may already pass (lone `\` is already kept).

- [ ] **Step 3: Implement the escape-consuming scanner**

In `clio/parser/lexer.py`, replace the current block:

```python
            if ch == '"':
                j = i + 1
                while j < len(stripped) and stripped[j] != '"':
                    j += 1
                if j >= len(stripped):
                    raise LexError("unterminated string literal", lineno, col)
                tokens.append(Token(TokenType.STRING, stripped[i + 1:j], lineno, col))
                col += (j - i) + 1
                i = j + 1
                continue
```

with:

```python
            if ch == '"':
                j = i + 1
                buf: list[str] = []
                while j < len(stripped) and stripped[j] != '"':
                    if (
                        stripped[j] == "\\"
                        and j + 1 < len(stripped)
                        and stripped[j + 1] in ('"', "\\")
                    ):
                        buf.append(stripped[j + 1])
                        j += 2
                        continue
                    buf.append(stripped[j])
                    j += 1
                if j >= len(stripped):
                    raise LexError("unterminated string literal", lineno, col)
                tokens.append(Token(TokenType.STRING, "".join(buf), lineno, col))
                col += (j - i) + 1
                i = j + 1
                continue
```

(Column counting is unchanged: `j` is still the source index of the closing `"`, so `(j - i) + 1` counts source columns. Only the token *value* changes — escapes are unescaped into `buf`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_lexer.py -v`
Expected: all lexer tests PASS, including the five new ones. (`test_lex_number_and_string` and the others must still pass — the change is backward-compatible for strings with no `\`.)

- [ ] **Step 5: Commit**

```bash
git add clio/parser/lexer.py tests/test_lexer.py
git commit -m "fix(lexer): support \\\" and \\\\ escapes in string literals (closes #88)"
```

---

### Task 2: End-to-end regression — JSON-emitting shell `cmd:` + IF literal (TDD)

This is the issue #88 repro at the IR level, plus a guard that the escaped value re-emits cleanly through the two parity targets (python, go) and through IF-condition rendering.

**Files:**
- Create: `tests/fixtures/shell_json_cmd.clio`
- Test: `tests/test_ir.py` (IR-level repro) and `tests/test_emitters/test_string_escapes.py` (new, emit guard)

- [ ] **Step 1: Create the fixture**

`tests/fixtures/shell_json_cmd.clio`:

```clio
STEP probe
  GIVES: out: {available: bool}
  MODE: exact
  impl:
    mode: shell
    cmd: "echo '{\"available\": true}'"
    parse: json

FLOW check_probe
  probe()
```

- [ ] **Step 2: Write the failing IR-level test**

Append to `tests/test_ir.py` (it already imports `build_ir`, `parse`, `ShellImplIR`):

```python
def test_build_ir_shell_cmd_with_escaped_json_quotes():
    # Issue #88: an inline shell cmd that emits JSON. The \" escapes survive the
    # lexer, shlex keeps the JSON blob as one argv token.
    src = (
        "STEP probe\n"
        "  GIVES: out: {available: bool}\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: shell\n"
        "    cmd: \"echo '{\\\"available\\\": true}'\"\n"
        "    parse: json\n"
    )
    step = build_ir(parse(src)).steps[0]
    assert isinstance(step.impl, ShellImplIR)
    assert step.impl.argv == ("echo", '{"available": true}')
```

- [ ] **Step 3: Run it to verify it fails (before Task 1) / passes (after Task 1)**

Run: `.venv/bin/pytest tests/test_ir.py::test_build_ir_shell_cmd_with_escaped_json_quotes -v`
Expected after Task 1: PASS. (On a tree without Task 1 it raises `LexError`, proving the test is real.)

- [ ] **Step 4: Write the cross-emitter emit guard**

Create `tests/test_emitters/test_string_escapes.py`:

```python
from pathlib import Path

from clio.emitters.python import PythonEmitter
from clio.emitters.go import GoEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _read_tree(root: Path) -> str:
    return "\n".join(
        p.read_text() for p in sorted(root.rglob("*")) if p.is_file()
    )


def test_python_emit_shell_json_cmd_reescapes(tmp_path):
    graph = build_ir(parse((FIXTURES / "shell_json_cmd.clio").read_text()))
    PythonEmitter().emit(graph, tmp_path)
    text = _read_tree(tmp_path)
    # The JSON blob survives as one argv token, re-escaped by Python repr.
    assert '{"available": true}' in text


def test_go_emit_shell_json_cmd_reescapes(tmp_path):
    graph = build_ir(parse((FIXTURES / "shell_json_cmd.clio").read_text()))
    GoEmitter().emit(graph, tmp_path)
    text = _read_tree(tmp_path)
    # Go renders argv tokens via json.dumps -> the quotes are backslash-escaped.
    assert 'available' in text


def test_python_emit_if_string_literal_with_escaped_quote(tmp_path):
    # IF/MATCH regression guard: a condition string literal carrying an escaped
    # quote must flow through condition rendering (repr) without crashing. The
    # flow is structurally complete (IF + ELSE) so any failure is the escape, not
    # a missing branch.
    src = (
        "CONTRACT r\n"
        "  SHAPE: {msg: str}\n"
        "\n"
        "STEP classify\n"
        "  TAKES: text: str\n"
        "  GIVES: result: r\n"
        "  MODE: judgment\n"
        "\n"
        "STEP handle\n"
        "  TAKES: x: r\n"
        "  GIVES: out: r\n"
        "  MODE: judgment\n"
        "\n"
        "FLOW f\n"
        "  TAKES: text: str\n"
        "  classify(text=text)\n"
        "    -> IF result.msg == \"a\\\"b\":\n"
        "         handle(x=result)\n"
        "    ELSE:\n"
        "         handle(x=result)\n"
    )
    graph = build_ir(parse(src))
    PythonEmitter().emit(graph, tmp_path)  # must not raise
    text = _read_tree(tmp_path)
    # repr() renders a string containing a double-quote with single quotes: 'a"b'
    assert 'a"b' in text


def test_claude_skill_sidecar_preserves_escaped_cmd_verbatim(tmp_path):
    # The claude-skill .clio/ sidecar stores the source verbatim, so the escaped
    # cmd line round-trips byte-exactly (this is what `clio import --mode strict`
    # recovers — no re-lex involved).
    from clio.emitters.claude_skill import ClaudeSkillEmitter

    src = (FIXTURES / "shell_json_cmd.clio").read_text()
    ClaudeSkillEmitter().emit(build_ir(parse(src)), tmp_path)
    sidecar = "\n".join(
        p.read_text()
        for p in sorted(tmp_path.rglob("*"))
        if p.is_file() and ".clio" in p.parts
    )
    assert 'cmd: "echo \'{\\"available\\": true}\'"' in sidecar
```

- [ ] **Step 5: Run the emit guard**

Run: `.venv/bin/pytest tests/test_emitters/test_string_escapes.py -v`
Expected: all four PASS. (If the IF flow fails to *parse/build* for a structural reason, that is a fixture bug, not the feature — the IF left-side `result.msg` is a dotted CONTRACT field, which is valid; fix the fixture, never weaken the assert.)

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/shell_json_cmd.clio tests/test_ir.py tests/test_emitters/test_string_escapes.py
git commit -m "test(lexer): end-to-end guard for escaped-quote shell cmd + IF literal across python/go"
```

---

### Task 3: Docs — LANGUAGE_SPEC + CHANGELOG

**Files:**
- Modify: `docs/LANGUAGE_SPEC.md` (string-literal section)
- Modify: `CHANGELOG.md` (`[Unreleased]`)

- [ ] **Step 1: Find the string-literal description in the spec**

Run: `grep -n 'string literal\|"\.\.\.\"\|single-line string\|quoted string' docs/LANGUAGE_SPEC.md | head`
Read the surrounding lines to place the note where string literals are defined.

- [ ] **Step 2: Add the escape note to `docs/LANGUAGE_SPEC.md`**

At the string-literal definition, add one sentence (match the file's existing prose style):

```markdown
Single-line string literals support two escapes: `\"` for a literal double-quote and `\\` for a
literal backslash. Any other `\x` is a literal backslash followed by `x`. (Newlines belong in the
multi-line `|` block form, not in a `"…"` literal.)
```

- [ ] **Step 3: Add a CHANGELOG `[Unreleased]` entry**

In `CHANGELOG.md`, under the existing `## [Unreleased]` heading's `### Fixed` (create the `### Fixed` subsection if absent), add:

```markdown
- **Parser: string literals now support `\"` and `\\` escapes** (`clio/parser/lexer.py`). An inline
  `impl` shell `cmd:` emitting JSON (e.g. `cmd: "echo '{\"ok\": true}'"`) is now expressible; before,
  the unescaped `"` terminated the string early and tripped a misleading `unexpected character '\'`.
  Any other `\x` stays a literal backslash (no regression). Closes #88.
```

- [ ] **Step 4: Verify docs parse and commit**

Run: `grep -n 'escapes' docs/LANGUAGE_SPEC.md CHANGELOG.md`
Expected: both edits present.

```bash
git add docs/LANGUAGE_SPEC.md CHANGELOG.md
git commit -m "docs(lexer): document \\\" / \\\\ string escapes (LANGUAGE_SPEC + CHANGELOG)"
```

---

### Task 4: Full gate + PR

**Files:** none (verification + integration)

- [ ] **Step 1: Run ruff (CI gates pytest behind it)**

Run: `.venv/bin/python -m ruff check . --fix && .venv/bin/python -m ruff check .`
Expected: `All checks passed!`

- [ ] **Step 2: Run mypy (strict, clio/ only — CI runs it)**

Run: `.venv/bin/python -m mypy clio/`
Expected: `Success: no issues found`. (The `buf: list[str]` annotation in Task 1 keeps the new code typed.)

- [ ] **Step 3: Run the full test suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (previous baseline ~1270 passed; this adds 10 tests → ~1280 passed, plus the existing skips/xfails).

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin fix/string-literal-escapes
gh pr create --base main --head fix/string-literal-escapes \
  --title "fix(lexer): string-literal escapes \\\" and \\\\ — closes #88" \
  --body "Lets a \`\"…\"\` string contain \`\\\"\` and \`\\\\\`, so an inline shell \`cmd:\` emitting JSON is expressible. One production change (the lexer string scanner); a planning-time audit confirmed all emitters already re-escape string values, so no emitter change is needed. Spec: docs/superpowers/specs/2026-05-31-string-literal-escapes-design.md.

closes #88

## Test plan
- [ ] Lexer unit: \`\\\"\`/\`\\\\\` unescaped, lone \`\\\` preserved, unterminated-after-escape
- [ ] IR repro: JSON shell cmd → argv keeps the blob as one token
- [ ] Emit guard: python + go re-escape; IF string literal emits without crashing
- [ ] CI green
- [ ] Gemini review cycle"
```

- [ ] **Step 5: After CI green, trigger Gemini and sweep the test plan**

Run: `gh pr comment <PR#> --body "/gemini review"` (auto-trigger is unreliable per project convention). After CI is green, check the test-plan boxes that are verified.
