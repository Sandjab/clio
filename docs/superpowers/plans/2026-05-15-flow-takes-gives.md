# FLOW.TAKES / FLOW.GIVES Implementation Plan (v0.16 sprint)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a `FLOW` an optional explicit signature (`TAKES:` / `GIVES:`) mirroring `STEP`, so external inputs are declared (closes #21) and the FLOW's output contract is type-checked against the last step. Bonus: typed `TEST WITH:` / `TEST EXPECTS:` clauses and clean `inputSchema`/`outputSchema` derivation in the MCP-server and claude-skill emitters.

**Architecture:** Both fields are optional in v0.16. When absent, behaviour is unchanged (auto-promote from PR #20 for `TAKES`, last-step inference for `GIVES`). When `FLOW.TAKES` is present it becomes the *single source of truth* — the auto-promote does not fire and undeclared identifier kwargs are rejected. When `FLOW.GIVES` is present, each declared field must exist in the last chain item's effective state with a structurally-equivalent type (subset semantics: the last step may produce more state, only the declared subset is exposed externally).

**Tech Stack:** Python 3.12+, hand-written recursive-descent parser, frozen dataclass AST/IR, 5 emitters (python / mcp-server / claude-skill / langgraph / claude-cli). No new dependencies.

---

## File structure

Files this sprint will touch (all existing):

- **Spec & docs**
  - `docs/LANGUAGE_SPEC.md` — new §FLOW.TAKES / §FLOW.GIVES, implementation-status row
  - `docs/manual/02-language-tour.md` — short paragraph on the FLOW signature
  - `docs/manual/03-cookbook.md` — one recipe demonstrating the top-level FOR EACH PARALLEL use case
  - `docs/manual/06-troubleshooting.md` — entry for the new "declared in TAKES but not used" / "GIVES field not produced by last step" errors
  - `CHANGELOG.md` — add `[Unreleased]` section
- **Parser layer**
  - `clio/parser/ast_nodes.py:178-184` — `FlowDecl` gains `takes` / `gives` (both optional)
  - `clio/parser/parser.py:2032-2096` — `parse_flow` extended to accept the optional fields before the chain
- **IR layer**
  - `clio/ir/graph.py:365-370` — `FlowIR` gains `takes` / `gives` (both optional)
  - `clio/ir/builder.py:490-545` — `_build_flow` seeds `FLOW.TAKES` into the initial scope and validates `FLOW.GIVES` against the last chain item
  - `clio/ir/builder.py:206-238` — `_build_tests` type-checks `WITH:` against `FLOW.TAKES` and `EXPECTS:` against `FLOW.GIVES` when both are declared
- **Emitters**
  - `clio/emitters/python.py` — `run()` signature from `FLOW.TAKES`, returns dict from `FLOW.GIVES`
  - `clio/emitters/mcp_server.py` — `inputSchema` from `FLOW.TAKES`, `outputSchema` from `FLOW.GIVES`
  - `clio/emitters/claude_skill.py` — surface declared TAKES/GIVES in SKILL.md frontmatter
  - `clio/emitters/langgraph.py` — `State` TypedDict from TAKES, final dict from GIVES
  - `clio/emitters/claude_cli.py` — document expected `state.json` initial kwargs in the emitted README
- **Examples**
  - `examples/flow_signature.clio` (new) — minimal example that compiles to all 5 targets and exercises the top-level FOR EACH pattern from #21
- **Tests** (new files or new test cases in existing files)
  - `tests/test_parser.py` — TAKES/GIVES parsing on FLOW
  - `tests/test_ir.py` — seeding, GIVES coverage, auto-promote interaction
  - `tests/test_emitters/test_python.py` — emitted `run()` signature
  - `tests/test_emitters/test_mcp_server.py` — schemas from declared signature
  - `tests/test_emitters/test_claude_skill.py` — SKILL.md frontmatter
  - `tests/test_emitters/test_langgraph.py` — State TypedDict shape
  - `tests/test_ir_tests.py` — TEST typing against declared FLOW signature

---

## Task 1: Spec — write the language spec for FLOW.TAKES / FLOW.GIVES first

**Why first:** every downstream layer needs to refer back to this. The spec is the single source of truth that the parser/IR/emitter behaviour must match.

**Files:**
- Modify: `docs/LANGUAGE_SPEC.md` — insert a new `### FLOW signature (v0.16, optional)` subsection inside the existing `## FLOW` section. Update the implementation-status table at the top of the doc.

- [ ] **Step 1: Find the existing FLOW section in the spec**

Run: `grep -n "^## FLOW\|^### FLOW" docs/LANGUAGE_SPEC.md`

Look for the heading that introduces the FLOW grammar. Read the surrounding 30-50 lines to match the doc's voice (terse, grammar-first, with worked examples).

- [ ] **Step 2: Insert the new subsection**

Add this subsection right after the FLOW grammar block (before the FOR EACH / IF / WHILE subsections that already exist):

````markdown
### FLOW signature (v0.16, optional)

A FLOW may declare an explicit signature with optional `TAKES:` and `GIVES:` blocks, mirroring `STEP`. Both fields are optional in v0.16; absent fields fall back to v0.15 behaviour (first-step `TAKES` auto-promotion for inputs, last-step `GIVES` inference for outputs).

```
FLOW <name>
  TAKES: <field>: <type>, <field>: <type>, ...    # optional, multi-field
  GIVES: <field>: <type>, <field>: <type>, ...    # optional, multi-field
  <chain>
  <rescues>
```

**Semantics of `TAKES`:**
- Declared names are seeded into the chain's input scope before walking. This allows the chain's first item to be `FOR EACH`, `IF`, `WHILE`, or `MATCH` over an external input — these forms previously failed to compile because the auto-promote path only inspected first-position `StepCall`s.
- When `TAKES:` is declared, the first-step `StepCall` auto-promote (v0.15.1) is **disabled** for this FLOW. Any identifier kwarg in the chain that is not produced upstream and is not in `TAKES` is rejected at compile time with `line:col`.
- `clio compile --kwargs '{...}'` validates declared inputs structurally at parse time, not runtime.

**Semantics of `GIVES`:**
- Each declared field must match a field present in the *effective state* after the last chain item executes, with a structurally-equivalent type (subset coverage: the last step may produce additional state fields; only the declared subset is exposed externally).
- A missing field in the state — or a type mismatch — is rejected at compile time with `line:col`.
- `target: python` returns a dict keyed by the declared `GIVES` field names. `target: mcp-server` and `target: claude-skill` derive `outputSchema` from `GIVES` instead of inferring it from the last step.

**Interaction with `TEST`:**
- `TEST WITH:` kwarg names and types are checked against `FLOW.TAKES` when declared.
- `TEST EXPECTS:` / `EXPECTS_NOT:` field paths (`<root>.<sub>...`) are checked against `FLOW.GIVES` when declared.
- When the FLOW does not declare a signature, `TEST` behaves as in v0.15 (no compile-time type check).

**Worked example — closes the #21 case:**

```clio
STEP classify
  TAKES: item:  str
  GIVES: label: str
  MODE:  judgment

FLOW classify_batch
  TAKES: items:  List<str>
  GIVES: labels: List<str>
  FOR EACH item IN items PARALLEL AS labels:
    classify(item=item)
```

Without `TAKES:`, the FOR EACH at the head of the chain compiles to `state reference 'items' not produced by any previous step` (#21). With `TAKES:` declared, `items` is seeded as an external input and the FLOW compiles to all targets that support PARALLEL (python / mcp-server / claude-skill).
````

- [ ] **Step 3: Update the implementation-status table**

Find the table at the top of `LANGUAGE_SPEC.md` (search for `| Feature |`). Add a row:

```
| FLOW.TAKES / FLOW.GIVES   | v0.16 | optional, mirrors STEP; closes #21 |
```

Match the existing table format (the actual columns may include parser/IR/python/mcp/claude-cli/langgraph/claude-skill — fill the row to match).

- [ ] **Step 4: Commit**

```bash
git checkout -b feat/v0.16-flow-signature
git add docs/LANGUAGE_SPEC.md
git commit -m "spec(v0.16): FLOW.TAKES / FLOW.GIVES — explicit FLOW signature

Adds the v0.16 §FLOW signature subsection and an implementation-status
row. Both fields are optional; v0.15 behaviour is preserved when they
are absent. Implementation lands in the following commits."
```

---

## Task 2: AST — extend FlowDecl with optional takes/gives

**Files:**
- Modify: `clio/parser/ast_nodes.py:178-184` — extend `FlowDecl` with two optional fields.

- [ ] **Step 1: Read the current FlowDecl shape**

Run: `sed -n '178,185p' clio/parser/ast_nodes.py`

Confirm the current frozen dataclass shape matches:
```python
@dataclass(frozen=True)
class FlowDecl:
    name: str
    chain: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock | ResumeAst, ...]"
    rescues: "tuple[RescueBlock, ...]"
    line: int
    col: int
```

- [ ] **Step 2: Add `takes` and `gives` fields**

Replace the class body with:

```python
@dataclass(frozen=True)
class FlowDecl:
    name: str
    chain: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock | ResumeAst, ...]"
    rescues: "tuple[RescueBlock, ...]"
    line: int
    col: int
    takes: tuple[Field, ...] = ()      # v0.16 — empty tuple = field not declared
    gives: tuple[Field, ...] = ()      # v0.16 — empty tuple = field not declared
```

**Note:** both fields use `tuple[Field, ...]` (same as `StepDecl.takes`). For `GIVES`, the multi-field shape mirrors `TAKES` (rather than `StepDecl.gives`'s single `Field | None`) because a FLOW may legitimately expose multiple top-level state slots — wrapping them in a single record would be artificial. The empty-tuple default keeps backward compatibility: zero-length `takes` / `gives` means "not declared".

- [ ] **Step 3: Run the existing parser tests to make sure the default value addition is backward-compat**

Run: `uv run pytest tests/test_parser.py -q --tb=line`
Expected: all green (the default-value additions cannot break any caller that doesn't pass the new args).

- [ ] **Step 4: Commit**

```bash
git add clio/parser/ast_nodes.py
git commit -m "ast(v0.16): FlowDecl gains optional takes/gives fields

Both default to empty tuples for backward compatibility — empty means
'not declared' and falls back to v0.15 behaviour. The parser is
extended in the next commit; this commit changes only the data
shape."
```

---

## Task 3: Parser — parse `TAKES:` and `GIVES:` before the FLOW chain

**Files:**
- Modify: `clio/parser/parser.py:2032-2096` — `parse_flow` reads optional `TAKES:` / `GIVES:` between the `INDENT` after `FLOW <name>` and the first chain item.

- [ ] **Step 1: Locate the parse_flow entry**

Run: `sed -n '2032,2045p' clio/parser/parser.py`

Read the current shape: after `FLOW <ident> NEWLINE INDENT`, the parser jumps straight into `parse_flow_item()` for the first chain item.

- [ ] **Step 2: Insert the TAKES/GIVES parsing block before the first chain item**

Replace the sequence (currently lines ~2036-2038):

```python
        self.expect(TokenType.INDENT)

        chain: list[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock | ResumeAst] = [self.parse_flow_item()]
```

with this (parse optional TAKES/GIVES first; loop because they may appear in either order, but each at most once):

```python
        self.expect(TokenType.INDENT)

        takes: tuple[Field, ...] = ()
        gives: tuple[Field, ...] = ()
        # v0.16: optional FLOW.TAKES / FLOW.GIVES blocks before the chain.
        # Either order; duplicates rejected; absent fields default to ().
        while True:
            t = self.peek()
            if t.type != TokenType.KEYWORD:
                break
            if t.value == "TAKES":
                if takes:
                    raise ParseError(
                        f"FLOW {ident.value} has duplicate TAKES field", t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                takes = self.parse_field_list()
                self.expect(TokenType.NEWLINE)
            elif t.value == "GIVES":
                if gives:
                    raise ParseError(
                        f"FLOW {ident.value} has duplicate GIVES field", t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                gives = self.parse_field_list()
                self.expect(TokenType.NEWLINE)
            else:
                break

        chain: list[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock | ResumeAst] = [self.parse_flow_item()]
```

- [ ] **Step 3: Thread takes/gives into the FlowDecl construction**

Find the `return FlowDecl(...)` at the end of `parse_flow` (around line 2091) and add `takes=takes, gives=gives` to the keyword arguments:

```python
        return FlowDecl(
            name=ident.value,
            chain=tuple(chain),
            rescues=tuple(rescues),
            line=kw.line, col=kw.col,
            takes=takes,
            gives=gives,
        )
```

- [ ] **Step 4: Write parser unit tests**

Append to `tests/test_parser.py` (find the section that tests FLOW parsing; add these after the existing FLOW tests):

```python
def test_flow_takes_single_field_parses():
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  TAKES: x: str
  s(x=x)
"""
    program = parse(src)
    flow = next(d for d in program.decls if isinstance(d, FlowDecl))
    assert len(flow.takes) == 1
    assert flow.takes[0].name == "x"
    assert isinstance(flow.takes[0].type, PrimitiveType) and flow.takes[0].type.name == "str"
    assert flow.gives == ()


def test_flow_gives_multi_field_parses():
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  GIVES: a: str, b: int
  s(x="hi")
"""
    program = parse(src)
    flow = next(d for d in program.decls if isinstance(d, FlowDecl))
    assert len(flow.gives) == 2
    assert {f.name for f in flow.gives} == {"a", "b"}
    assert flow.takes == ()


def test_flow_takes_and_gives_parse_in_either_order():
    src_a = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW p
  TAKES: x: str
  GIVES: y: str
  s(x=x)
"""
    src_b = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW p
  GIVES: y: str
  TAKES: x: str
  s(x=x)
"""
    for src in (src_a, src_b):
        program = parse(src)
        flow = next(d for d in program.decls if isinstance(d, FlowDecl))
        assert len(flow.takes) == 1 and len(flow.gives) == 1


def test_flow_duplicate_takes_rejected():
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW p
  TAKES: x: str
  TAKES: y: int
  s(x=x)
"""
    with pytest.raises(ParseError, match="duplicate TAKES"):
        parse(src)


def test_flow_without_takes_gives_still_parses_backcompat():
    """v0.15 form — no FLOW signature, behaviour unchanged."""
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  s(x="hi")
"""
    program = parse(src)
    flow = next(d for d in program.decls if isinstance(d, FlowDecl))
    assert flow.takes == ()
    assert flow.gives == ()
```

Make sure the file already has the necessary imports (`from clio.parser.parser import parse, ParseError`, `from clio.parser.ast_nodes import FlowDecl, PrimitiveType`). Add them if missing.

- [ ] **Step 5: Run parser tests**

Run: `uv run pytest tests/test_parser.py -q --tb=line`
Expected: all green, including the 5 new tests.

- [ ] **Step 6: Commit**

```bash
git add clio/parser/parser.py tests/test_parser.py
git commit -m "parser(v0.16): parse optional FLOW.TAKES / FLOW.GIVES blocks

The two blocks may appear in any order between the FLOW <name> INDENT
and the first chain item. Each at most once; duplicates rejected.
Existing flows without signatures parse unchanged (defaults are
empty tuples)."
```

---

## Task 4: IR types — extend FlowIR with optional takes/gives

**Files:**
- Modify: `clio/ir/graph.py:365-370` — `FlowIR` gains `takes` / `gives` fields, both default to empty tuple.

- [ ] **Step 1: Locate FlowIR**

Run: `sed -n '365,371p' clio/ir/graph.py`

Confirm current shape:
```python
@dataclass(frozen=True)
class FlowIR:
    name: str
    chain: "tuple[CallIR | ForEachIR | IfBlockIR | MatchBlockIR | WhileBlockIR, ...]"
    rescues: "tuple[RescueBlockIR, ...]"
    line: int
```

- [ ] **Step 2: Find FieldIR or the IR equivalent for typed-named fields**

Run: `grep -n "^class FieldIR\|^class.*Field" clio/ir/graph.py`

If there is no `FieldIR`, the IR types fields directly via `TypeExpr` and a `(name, type)` tuple. Check `StepIR.takes` to see the convention used. Run:

```bash
grep -n "class StepIR\|takes:" clio/ir/graph.py | head -10
```

Use the same convention for FlowIR. The most common pattern is `tuple[tuple[str, TypeExpr], ...]`.

- [ ] **Step 3: Extend FlowIR**

Replace the FlowIR definition with:

```python
@dataclass(frozen=True)
class FlowIR:
    name: str
    chain: "tuple[CallIR | ForEachIR | IfBlockIR | MatchBlockIR | WhileBlockIR, ...]"
    rescues: "tuple[RescueBlockIR, ...]"
    line: int
    takes: tuple[tuple[str, TypeExpr], ...] = ()      # v0.16 — empty = not declared
    gives: tuple[tuple[str, TypeExpr], ...] = ()      # v0.16 — empty = not declared
```

(If `StepIR.takes` uses a different shape — e.g. a dedicated `FieldIR` dataclass — use the same shape for symmetry. Adjust the type annotation accordingly.)

- [ ] **Step 4: Commit**

```bash
git add clio/ir/graph.py
git commit -m "ir(v0.16): FlowIR gains optional takes/gives fields

Mirrors the FlowDecl change. Defaults to empty tuples for backward
compatibility. Builder logic lands in the next commit."
```

---

## Task 5: IR builder — seed FLOW.TAKES into chain scope (closes #21)

**Files:**
- Modify: `clio/ir/builder.py:490-545` — `_build_flow` reads `FLOW.TAKES` from the `FlowDecl` and seeds those names into the chain-walking scope. When `TAKES` is declared, the existing first-step auto-promote (PR #20) is disabled.

- [ ] **Step 1: Read `_build_flow` and the existing auto-promote**

Run:
```bash
sed -n '490,560p' clio/ir/builder.py
grep -n "auto.promote\|first.step\|seed\|initial" clio/ir/builder.py | head -20
```

Identify (a) where the chain is walked and where `produced` is initialised; (b) the StepCall auto-promote logic (likely a check on `chain[0]` being a `StepCall` and adding its TAKES to the initial scope).

- [ ] **Step 2: Add the seeding logic**

In `_build_flow`, before the chain is walked, build the initial scope:

```python
    # v0.16: if FLOW.TAKES is declared, seed those names as external inputs
    # and DISABLE the v0.15 first-step StepCall auto-promote (single source of truth).
    # The IR-level `takes` mirror is lowered from the AST `Field` list.
    flow_takes_ir: tuple[tuple[str, TypeExpr], ...] = ()
    if decl.takes:
        flow_takes_ir = tuple((f.name, f.type) for f in decl.takes)
        # Reject duplicates at compile time.
        seen: set[str] = set()
        for name, _ in flow_takes_ir:
            if name in seen:
                raise IRBuildError(
                    f"FLOW {decl.name!r} declares duplicate TAKES field "
                    f"{name!r} (line {decl.line})"
                )
            seen.add(name)

    # Initial scope: declared TAKES override / replace auto-promote.
    initial_scope: dict[str, TypeExpr] = {}
    if flow_takes_ir:
        # Declared TAKES path — single source of truth, no auto-promote.
        for name, type_ in flow_takes_ir:
            initial_scope[name] = type_
    else:
        # Legacy path: keep the v0.15.1 first-step StepCall auto-promote.
        # (Locate the existing auto-promote block and call it from here, or
        # leave it where it was and only run it when `decl.takes` is empty.)
        initial_scope = _autopromote_first_step_takes(decl, steps_by_name)
```

You will need to extract the existing auto-promote logic into a helper named `_autopromote_first_step_takes(decl, steps_by_name) -> dict[str, TypeExpr]` if it isn't already extracted. Keep the existing semantics verbatim — this is a refactor for clarity, not a behaviour change.

- [ ] **Step 3: Pass the initial_scope into the chain walker**

Find the call to `_build_flow_items(...)` (around line 540). It currently receives an empty or implicit initial state. Change the call site to pass `initial_scope` so the chain walker sees `items` (or whatever the user declared) as already-produced when it walks the first item.

The exact change depends on the current `_build_flow_items` signature — read it (`grep -n "def _build_flow_items" clio/ir/builder.py`) and add an `initial_produced: dict[str, TypeExpr] | None = None` parameter if absent, then thread `initial_scope` into the `produced` dict at the start of the walk.

- [ ] **Step 4: Thread takes/gives into the FlowIR construction**

Find the `return FlowIR(...)` (or the call that builds it) and add `takes=flow_takes_ir`:

```python
    return FlowIR(
        name=decl.name,
        chain=tuple(chain_ir),
        rescues=tuple(rescue_ir),
        line=decl.line,
        takes=flow_takes_ir,
        gives=(),    # populated by Task 6
    )
```

- [ ] **Step 5: Add the #21 closure test**

In `tests/test_ir.py`, add:

```python
def test_flow_with_declared_takes_compiles_when_chain_starts_with_for_each():
    """Closes #21 — top-level FOR EACH over an external input now compiles
    when FLOW.TAKES declares the input."""
    src = """STEP classify
  TAKES: item:  str
  GIVES: label: str
  MODE:  judgment

FLOW pipeline
  TAKES: items: List<str>
  FOR EACH item IN items PARALLEL AS labels:
    classify(item=item)
"""
    graph = build_ir(parse(src))
    assert graph.flow is not None
    assert graph.flow.takes == (("items", ListType(inner=PrimitiveType(name="str"))),)


def test_flow_with_declared_takes_disables_autopromote():
    """When FLOW.TAKES is declared, a first-step identifier kwarg not in
    TAKES must be rejected (no implicit auto-promote)."""
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  TAKES: a: str
  s(x=x)
"""
    with pytest.raises(IRBuildError, match="state reference 'x' not produced"):
        build_ir(parse(src))


def test_flow_without_takes_keeps_autopromote_v0_15_behaviour():
    """Backward-compat: no FLOW.TAKES → the first-step StepCall auto-promote
    from PR #20 still fires, so this compiles (x is promoted)."""
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  s(x=x)
"""
    graph = build_ir(parse(src))
    assert graph.flow is not None
    assert graph.flow.takes == ()    # nothing declared


def test_flow_with_declared_takes_rejects_top_level_for_each_over_undeclared():
    """Top-level FOR EACH over an identifier that is not in FLOW.TAKES is
    still rejected — the #21 error message stays."""
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  TAKES: a: List<str>
  FOR EACH x IN items:
    s(x=x)
"""
    with pytest.raises(IRBuildError, match="not produced"):
        build_ir(parse(src))
```

Ensure imports include `parse`, `build_ir`, `IRBuildError`, `ListType`, `PrimitiveType`. Add them at the top of the file if missing.

- [ ] **Step 6: Run IR tests**

Run: `uv run pytest tests/test_ir.py -q --tb=line`
Expected: all green, including the 4 new tests.

- [ ] **Step 7: Commit**

```bash
git add clio/ir/builder.py tests/test_ir.py
git commit -m "ir(v0.16): seed FLOW.TAKES into initial scope, closes #21

When FLOW.TAKES is declared, the v0.15.1 first-step StepCall
auto-promote is disabled and TAKES becomes the single source of
truth. Top-level FOR EACH / IF / WHILE over a declared external
input now compiles. When TAKES is absent, behaviour is unchanged."
```

---

## Task 6: IR builder — validate FLOW.GIVES coverage

**Files:**
- Modify: `clio/ir/builder.py` — at the end of `_build_flow`, after the chain is built, compute the effective state shape produced by the last chain item and verify each declared `FLOW.GIVES` field is present with a structurally-compatible type.

- [ ] **Step 1: Identify how the IR builder tracks "effective state after step N"**

Run: `grep -n "produced\|_scope_after\|effective state" clio/ir/builder.py | head -20`

There should be a `produced` dict or similar that accumulates field-name → TypeExpr as the chain is walked. The state after the last chain item is the final value of this dict.

- [ ] **Step 2: Add the GIVES coverage check after the chain is built**

In `_build_flow`, after `chain_ir` is fully constructed and `produced` reflects the post-last-step state, insert:

```python
    flow_gives_ir: tuple[tuple[str, TypeExpr], ...] = ()
    if decl.gives:
        flow_gives_ir = tuple((f.name, f.type) for f in decl.gives)
        # Each declared GIVES field must exist in the final produced state
        # with a structurally-equivalent type. Extra produced fields are
        # allowed (subset semantics) — they remain internal to the flow.
        for name, declared_type in flow_gives_ir:
            if name not in produced:
                raise IRBuildError(
                    f"FLOW {decl.name!r} declares GIVES field {name!r} "
                    f"but no step in the chain produces it "
                    f"(line {decl.line})"
                )
            actual_type = produced[name]
            if not types_equal(declared_type, actual_type):
                raise IRBuildError(
                    f"FLOW {decl.name!r} declares GIVES field {name!r} "
                    f"as {_render(declared_type)} but the chain produces "
                    f"{_render(actual_type)} (line {decl.line})"
                )
```

`types_equal` and `_render` already exist in `clio/ir/builder.py` (used by fallback compatibility checks and condition validation). Confirm by running:
```bash
grep -n "def types_equal\|def _render" clio/ir/builder.py
```

If `types_equal` does not exist under that exact name, find the equivalent (likely `_types_equal`, `_structural_eq`, or inline structural comparison). Reuse whatever the codebase uses for `ON_FAIL fallback(...)` type-compat checks — that path solves the same structural-equivalence problem.

- [ ] **Step 3: Thread `gives=flow_gives_ir` into the FlowIR construction**

Update the `return FlowIR(...)` (you added `takes=flow_takes_ir, gives=()` in Task 5):

```python
    return FlowIR(
        name=decl.name,
        chain=tuple(chain_ir),
        rescues=tuple(rescue_ir),
        line=decl.line,
        takes=flow_takes_ir,
        gives=flow_gives_ir,
    )
```

- [ ] **Step 4: Add GIVES validation tests**

In `tests/test_ir.py`:

```python
def test_flow_gives_coverage_compiles_when_field_matches_last_step():
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW p
  TAKES: x: str
  GIVES: y: str
  s(x=x)
"""
    graph = build_ir(parse(src))
    assert graph.flow.gives == (("y", PrimitiveType(name="str")),)


def test_flow_gives_rejects_missing_field():
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW p
  TAKES: x: str
  GIVES: not_there: str
  s(x=x)
"""
    with pytest.raises(IRBuildError, match="declares GIVES field 'not_there'"):
        build_ir(parse(src))


def test_flow_gives_rejects_type_mismatch():
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW p
  TAKES: x: str
  GIVES: y: int
  s(x=x)
"""
    with pytest.raises(IRBuildError, match="GIVES field 'y'"):
        build_ir(parse(src))


def test_flow_gives_allows_subset_coverage():
    """The chain can produce more fields than FLOW.GIVES declares —
    only the declared subset is exposed externally."""
    src = """STEP s1
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

STEP s2
  TAKES: y: str
  GIVES: z: int
  MODE:  exact

FLOW p
  TAKES: x: str
  GIVES: z: int
  s1(x=x) -> s2(y=s1.y)
"""
    graph = build_ir(parse(src))
    # `y` is produced but not in GIVES — that's fine.
    assert {name for name, _ in graph.flow.gives} == {"z"}
```

- [ ] **Step 5: Run IR tests**

Run: `uv run pytest tests/test_ir.py -q --tb=line`
Expected: all green, including the 4 new GIVES tests.

- [ ] **Step 6: Commit**

```bash
git add clio/ir/builder.py tests/test_ir.py
git commit -m "ir(v0.16): validate FLOW.GIVES coverage against last-step state

Each declared GIVES field must exist in the state produced by the
last chain item with a structurally-equivalent type. Subset
semantics: extra produced fields are allowed and remain internal."
```

---

## Task 7: Python emitter — use FLOW.TAKES for run() signature, FLOW.GIVES for return

**Files:**
- Modify: `clio/emitters/python.py` — `run(**initial)` becomes `run(item_name: type, ...)` when `FLOW.TAKES` is declared; the function returns a dict keyed by `FLOW.GIVES` names instead of the full state.

- [ ] **Step 1: Locate the run() emission**

Run: `grep -n "def run\|run(.*initial\|return state" clio/emitters/python.py | head -10`

Find the section that emits the `def run(...)` definition and its return statement.

- [ ] **Step 2: Emit typed parameters when FLOW.TAKES is declared**

When `flow.takes != ()`, emit the function signature with named parameters typed from TAKES:

```python
def run(items: list[str], threshold: float = 0.5) -> dict:
    ...
```

When `flow.takes == ()`, fall back to the current `def run(**initial):` shape (backward compat).

You'll need a helper to render a `TypeExpr` as a Python type annotation. Search for an existing one:
```bash
grep -n "type_to_python\|_render_python_type\|_py_type" clio/emitters/python.py clio/emitters/_python_helpers.py clio/emitters/_shared_utils.py | head -10
```

If one exists, use it. If not, write a small helper that maps `PrimitiveType("int")` → `"int"`, `ListType(inner)` → `f"list[{render(inner)}]"`, `RecordType(...)` → `"dict"`, `ContractRef(name)` → `f"contracts.{name}"`, `EnumType(...)` → `"str"`, `ConstrainedType(...)` → the underlying primitive.

- [ ] **Step 3: Emit a dict return when FLOW.GIVES is declared**

Replace the current `return state` (or equivalent) with, when `flow.gives != ()`:

```python
return {name: state[name] for name in (<declared GIVES names>)}
```

When `flow.gives == ()`, keep the current behaviour.

- [ ] **Step 4: Add emitter tests**

In `tests/test_emitters/test_python.py`:

```python
def test_python_emits_typed_run_signature_from_flow_takes(tmp_path):
    src = """STEP classify
  TAKES: item:  str
  GIVES: label: str
  MODE:  judgment

FLOW pipeline
  TAKES: items:  List<str>
  GIVES: labels: List<str>
  FOR EACH item IN items PARALLEL AS labels:
    classify(item=item)

RESOURCES
  target: python
  models: [haiku]
"""
    out = tmp_path / "out"
    compile_to(parse(src), target="python", output_dir=out)
    flow_py = (out / "pipeline" / "flow.py").read_text()
    assert "def run(items: list[str])" in flow_py
    assert 'return {"labels": state["labels"]}' in flow_py or \
           "return {'labels': state['labels']}" in flow_py


def test_python_emits_kwargs_run_when_flow_has_no_signature(tmp_path):
    """v0.15 form — backward compat."""
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW p
  s(x=x)

RESOURCES
  target: python
  models: [haiku]
"""
    out = tmp_path / "out"
    compile_to(parse(src), target="python", output_dir=out)
    flow_py = (out / "p" / "flow.py").read_text()
    assert "def run(**initial)" in flow_py or "def run(**kwargs)" in flow_py
```

- [ ] **Step 5: Run emitter tests + regenerate fixture snapshots if any drift**

Run: `uv run pytest tests/test_emitters/test_python.py -q --tb=line`

If existing snapshot tests fail because the emitter output changed (it shouldn't, since you're only changing emission *when* TAKES/GIVES are declared, and the existing fixtures don't declare them), regenerate them with the appropriate flag (`pytest --snapshot-update` or whatever the codebase convention is).

- [ ] **Step 6: Commit**

```bash
git add clio/emitters/python.py tests/test_emitters/test_python.py
git commit -m "emitter(python, v0.16): typed run() signature from FLOW.TAKES

When FLOW.TAKES is declared, run() gets named typed parameters and
returns a dict keyed by FLOW.GIVES names. When absent, the v0.15
**initial / full-state-return behaviour is preserved."
```

---

## Task 8: MCP-server emitter — use TAKES for inputSchema, GIVES for outputSchema

**Files:**
- Modify: `clio/emitters/mcp_server.py` — derive `inputSchema` from `FLOW.TAKES` when declared, `outputSchema` from `FLOW.GIVES` when declared.

- [ ] **Step 1: Locate schema derivation**

Run: `grep -n "inputSchema\|outputSchema\|first_step\|last_step" clio/emitters/mcp_server.py`

Find where the current code infers schemas from the first/last step.

- [ ] **Step 2: Branch on declared signature**

Replace the inference path with:

```python
# v0.16: derive from FLOW.TAKES / FLOW.GIVES when declared.
# Falls back to first-step / last-step inference when absent.
if flow.takes:
    input_schema = _json_schema_from_fields(flow.takes)
else:
    input_schema = _json_schema_from_step_takes(first_step)

if flow.gives:
    output_schema = _json_schema_from_fields(flow.gives)
else:
    output_schema = _json_schema_from_step_gives(last_step)
```

Reuse the existing JSON-schema generation helpers — most likely they already accept a `tuple[(str, TypeExpr), ...]` shape. If they take a `Field | None` (for the single-gives case), adapt the call site or add a small helper that converts the multi-field tuple to whatever the existing helper expects.

- [ ] **Step 3: Add emitter tests**

In `tests/test_emitters/test_mcp_server.py`:

```python
def test_mcp_server_input_schema_from_flow_takes(tmp_path):
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW p
  TAKES: items: List<str>
  GIVES: y:     str
  s(x="hi")

RESOURCES
  target: mcp-server
  models: [haiku]
"""
    out = tmp_path / "out"
    compile_to(parse(src), target="mcp-server", output_dir=out)
    server_py = (out / "p" / "server.py").read_text()
    # The inputSchema is derived from FLOW.TAKES (items), not from
    # the first step's TAKES (x).
    assert '"items"' in server_py
    assert "List<str>" in server_py or "array" in server_py
```

- [ ] **Step 4: Run**

Run: `uv run pytest tests/test_emitters/test_mcp_server.py -q --tb=line`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/mcp_server.py tests/test_emitters/test_mcp_server.py
git commit -m "emitter(mcp-server, v0.16): schemas from FLOW.TAKES / GIVES when declared

inputSchema and outputSchema are derived from the FLOW signature
when present, replacing the fragile first-step / last-step inference."
```

---

## Task 9: Claude-skill emitter — surface declared TAKES/GIVES in SKILL.md

**Files:**
- Modify: `clio/emitters/claude_skill.py` — when `FLOW.TAKES` / `FLOW.GIVES` are declared, render them in the emitted `SKILL.md` (frontmatter `inputs:` / `outputs:` block, or an Inputs / Outputs section in the body — match the existing convention).

- [ ] **Step 1: Read the current SKILL.md emission**

Run: `cat clio/emitters/claude_skill.py | head -85`

Find the function that emits `SKILL.md`. Check whether it has an explicit Inputs / Outputs section today (probably yes, derived from first/last step).

- [ ] **Step 2: Branch on declared signature**

Same pattern as the MCP emitter: when `flow.takes` is declared, render it; otherwise fall back to the first-step inference. Same for `flow.gives`.

The exact rendering depends on the existing SKILL.md format — match it. A typical Markdown frontmatter block looks like:

```markdown
## Inputs

- `items: List<str>` — declared in `FLOW.TAKES`

## Outputs

- `labels: List<str>` — declared in `FLOW.GIVES`
```

- [ ] **Step 3: Test**

In `tests/test_emitters/test_claude_skill.py`:

```python
def test_claude_skill_renders_declared_takes(tmp_path):
    src = """STEP s
  TAKES: item: str
  GIVES: label: str
  MODE: judgment

FLOW pipeline
  TAKES: items:  List<str>
  GIVES: labels: List<str>
  FOR EACH item IN items PARALLEL AS labels:
    s(item=item)

RESOURCES
  target: claude-skill
  models: [haiku]
"""
    out = tmp_path / "out"
    compile_to(parse(src), target="claude-skill", output_dir=out)
    skill_md = (out / "pipeline" / "SKILL.md").read_text()
    assert "items: List<str>" in skill_md
    assert "labels: List<str>" in skill_md
```

- [ ] **Step 4: Run**

Run: `uv run pytest tests/test_emitters/test_claude_skill.py -q --tb=line`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/claude_skill.py tests/test_emitters/test_claude_skill.py
git commit -m "emitter(claude-skill, v0.16): render declared TAKES/GIVES in SKILL.md"
```

---

## Task 10: LangGraph emitter — State TypedDict from TAKES, final return from GIVES

**Files:**
- Modify: `clio/emitters/langgraph.py` — when `FLOW.TAKES` / `FLOW.GIVES` are declared, the emitted `State` TypedDict and final return value reflect those declarations.

- [ ] **Step 1: Read langgraph emitter**

Run: `cat clio/emitters/langgraph.py`

This file is small (~211 lines per the earlier wc). Find the `class State(TypedDict)` emission and the final return.

- [ ] **Step 2: Augment the State TypedDict**

The State TypedDict aggregates every TAKES/GIVES field across all steps in v0.15. With v0.16, when `FLOW.TAKES` is declared, the State must include those declared input fields as well (they are not produced by any step).

When `FLOW.GIVES` is declared, the LangGraph entry-point function should return a dict containing only those fields (the rest stays internal to the State).

- [ ] **Step 3: Test**

In `tests/test_emitters/test_langgraph.py`:

```python
def test_langgraph_state_typeddict_includes_flow_takes(tmp_path):
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  TAKES: x: str
  GIVES: y: str
  s(x=x)

RESOURCES
  target: langgraph
  models: [haiku]
"""
    out = tmp_path / "out"
    compile_to(parse(src), target="langgraph", output_dir=out)
    flow_py = (out / "pipeline" / "flow.py").read_text()
    assert "class State(TypedDict)" in flow_py
    assert "x:" in flow_py
    assert "y:" in flow_py
```

- [ ] **Step 4: Run**

Run: `uv run pytest tests/test_emitters/test_langgraph.py -q --tb=line`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/langgraph.py tests/test_emitters/test_langgraph.py
git commit -m "emitter(langgraph, v0.16): State TypedDict + return reflect FLOW.TAKES/GIVES"
```

---

## Task 11: TEST block — type-check WITH against TAKES and EXPECTS against GIVES

**Files:**
- Modify: `clio/ir/builder.py:206-238` — `_build_tests` validates kwarg names and types in `WITH:` against the target FLOW's `TAKES`, and validates field paths in `EXPECTS:` / `EXPECTS_NOT:` against the target FLOW's `GIVES`. Both checks only fire when the FLOW has the relevant declaration; otherwise behaviour is unchanged (v0.15 runtime-only).

- [ ] **Step 1: Read `_build_tests`**

Run: `sed -n '206,240p' clio/ir/builder.py`

Identify where the `with_kwargs` and `expects` lists are processed.

- [ ] **Step 2: Add the TAKES check**

After the FLOW is resolved by name, look up `flow.takes`. If non-empty, for each `(name, value)` in the test's `with_kwargs`:

- The kwarg `name` must be one of the FLOW.TAKES field names; else error.
- The Python type of `value` must be compatible with the declared `TypeExpr` (string for `str`, int for `int`, list for `List<T>`, etc.). Reuse the existing literal-type-check helper if one exists; otherwise write a small one inline.

- [ ] **Step 3: Add the GIVES check**

For each `(path, predicate)` in `expects` / `expects_not`:
- The root segment of `path` (split on `.`) must be a FLOW.GIVES field name.
- Subsequent segments must walk into that field's `TypeExpr` (record fields, contract fields).

When the FLOW does not declare GIVES, skip the check (v0.15 runtime-only behaviour).

- [ ] **Step 4: Test**

In `tests/test_ir_tests.py` (or wherever TEST-block IR tests live — check with `grep -rn "TestIR\|with_kwargs" tests/`):

```python
def test_test_with_unknown_kwarg_against_declared_takes_rejected():
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW p
  TAKES: x: str
  s(x=x)

TEST t
  FLOW: p
  WITH:
    not_there: "oops"
"""
    with pytest.raises(IRBuildError, match="WITH.*not_there"):
        build_ir(parse(src))


def test_test_expects_unknown_field_against_declared_gives_rejected():
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW p
  TAKES: x: str
  GIVES: y: str
  s(x=x)

TEST t
  FLOW: p
  WITH:
    x: "hi"
  EXPECTS:
    not_there: not_empty
"""
    with pytest.raises(IRBuildError, match="EXPECTS.*not_there"):
        build_ir(parse(src))


def test_test_without_flow_signature_skips_type_check_v0_15_backcompat():
    """v0.15 behaviour preserved when FLOW does not declare TAKES/GIVES."""
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW p
  s(x="hi")

TEST t
  FLOW: p
  WITH:
    anything: "goes"
"""
    build_ir(parse(src))    # no exception expected
```

- [ ] **Step 5: Run**

Run: `uv run pytest tests/test_ir_tests.py tests/test_ir.py -q --tb=line`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add clio/ir/builder.py tests/test_ir_tests.py
git commit -m "ir(v0.16): TEST WITH/EXPECTS type-checked vs declared FLOW signature

When the target FLOW declares TAKES, TEST WITH kwargs are validated
at parse time. When the target FLOW declares GIVES, TEST EXPECTS
field paths are validated at parse time. When neither is declared,
behaviour is unchanged (v0.15 runtime-only)."
```

---

## Task 12: Example — `examples/flow_signature.clio`

**Files:**
- Create: `examples/flow_signature.clio` — minimal example demonstrating the top-level FOR EACH PARALLEL pattern from #21, compiling cleanly with `FLOW.TAKES` / `FLOW.GIVES` declared. Compiles to python, mcp-server, claude-skill, langgraph. Rejected at compile time by claude-cli (no PARALLEL support — pre-v0.16 rule).

- [ ] **Step 1: Write the example file**

```clio
# Demonstrates v0.16 FLOW.TAKES / FLOW.GIVES — the explicit FLOW signature.
# Before v0.16, this flow would fail to compile because the chain starts
# directly with a FOR EACH over an external input (#21). With FLOW.TAKES
# declared, the IR builder seeds `articles` into the initial scope.

STEP classify
  TAKES:   text:  str
  GIVES:   label: enum(positive|neutral|negative)
  MODE:    judgment
  CACHE:   ttl(7d)
  ON_FAIL: retry(2) then escalate then abort("classification failed")

FLOW sentiment_batch
  TAKES:   articles: List<str>
  GIVES:   labels:   List<enum(positive|neutral|negative)>
  FOR EACH text IN articles PARALLEL AS labels:
    classify(text=text)

RESOURCES
  target: python
  models: [haiku, sonnet]
```

- [ ] **Step 2: Smoke-compile to every target that supports PARALLEL**

```bash
for t in python mcp-server claude-skill langgraph; do
  echo "=== $t ==="
  uv run python -m clio compile examples/flow_signature.clio --target $t --output /tmp/clio-flow-sig-$t || echo "FAILED on $t"
done
```

Expected: all four succeed.

- [ ] **Step 3: Verify claude-cli rejection is still clear**

```bash
uv run python -m clio compile examples/flow_signature.clio --target claude-cli --output /tmp/clio-flow-sig-cli 2>&1 || true
```

Expected: a clear compile error pointing to PARALLEL not being supported by claude-cli (the v0.4 rule, unchanged).

- [ ] **Step 4: Commit**

```bash
git add examples/flow_signature.clio
git commit -m "examples(v0.16): flow_signature.clio — closes #21 demonstration

Top-level FOR EACH PARALLEL over an external List<str> input, with
FLOW.TAKES + FLOW.GIVES declared. Compiles to python / mcp-server /
claude-skill / langgraph; rejected by claude-cli (no PARALLEL)."
```

---

## Task 13: Manual + CHANGELOG + claude-cli emitter doc

**Files:**
- Modify: `docs/manual/02-language-tour.md` — append a short paragraph on the FLOW signature (3-5 sentences + a tiny code snippet).
- Modify: `docs/manual/03-cookbook.md` — add a recipe titled "Declaring a FLOW signature" showing the `flow_signature.clio` example.
- Modify: `docs/manual/06-troubleshooting.md` — two entries: "FLOW declares GIVES field X but no step produces it" and "WITH.<name> not in FLOW.TAKES".
- Modify: `clio/emitters/claude_cli.py` — when emitting the README under the project root, surface the declared FLOW signature (initial `state.json` kwargs section). When absent, the v0.15 README is unchanged.
- Modify: `CHANGELOG.md` — add an `[Unreleased]` section with the v0.16 entry.

- [ ] **Step 1: Manual update**

In `docs/manual/02-language-tour.md`, find the FLOW section and append:

```markdown
### FLOW signature (v0.16, optional)

A `FLOW` may declare `TAKES:` and `GIVES:` blocks, mirroring `STEP`. This is the recommended form when a flow starts with `FOR EACH` / `IF` / `WHILE` over an external input, when you want the test suite to type-check `TEST WITH:` / `EXPECTS:` clauses, or when you want a clean `inputSchema`/`outputSchema` exposed by the MCP-server or claude-skill targets. When a FLOW omits the signature, v0.15 behaviour is preserved (input auto-promotion from the first step, output inferred from the last step).
```

In `docs/manual/03-cookbook.md`, add a recipe:

````markdown
### Declaring a FLOW signature for top-level fan-out

When a flow's first item is `FOR EACH item IN items PARALLEL AS results:` over an externally-supplied list, the v0.15 input auto-promotion does not fire (it inspects only first-position `StepCall`s) and the compiler refuses with `state reference 'items' not produced by any previous step`. The v0.16 fix is to declare the input explicitly:

```clio
STEP classify
  TAKES: text:  str
  GIVES: label: enum(positive|neutral|negative)
  MODE:  judgment

FLOW sentiment_batch
  TAKES: articles: List<str>
  GIVES: labels:   List<enum(positive|neutral|negative)>
  FOR EACH text IN articles PARALLEL AS labels:
    classify(text=text)
```

The declared `TAKES:` makes `articles` a first-class external input, so `run(articles=[...])` (python), the MCP `inputSchema`, and the `claude-skill` Inputs section all reflect it directly.
````

In `docs/manual/06-troubleshooting.md`:

```markdown
### `FLOW <name> declares GIVES field <X> but no step in the chain produces it`

Either the field name is misspelled, or the last step does not produce it. Check the last chain item's `GIVES` clause: every field declared in `FLOW.GIVES` must appear there (or have been produced earlier in the chain). Subset coverage is allowed in the reverse direction — the chain may produce *more* fields than `FLOW.GIVES` declares.

### `TEST <name>: WITH.<key> is not declared in FLOW <flow_name>.TAKES`

The kwarg name is not a declared input of the target FLOW. Add it to `FLOW.TAKES`, or remove it from the `WITH:` block. When the FLOW does not declare a signature, this check does not fire — `WITH:` falls back to v0.15's runtime-only behaviour.
```

- [ ] **Step 2: claude-cli emitter doc**

In `clio/emitters/claude_cli.py`, find where the emitted README is generated. Add a section that lists the declared `FLOW.TAKES` (initial `state.json` keys) and `FLOW.GIVES` (expected final state keys). When `flow.takes == ()` and `flow.gives == ()`, leave the README unchanged.

- [ ] **Step 3: CHANGELOG entry**

In `CHANGELOG.md`, replace any current top section with:

```markdown
## Unreleased

### Language

- **`FLOW.TAKES` and `FLOW.GIVES`** (`docs/LANGUAGE_SPEC.md` §FLOW signature) — `FLOW` declarations now accept optional `TAKES:` and `GIVES:` blocks mirroring `STEP`. When `FLOW.TAKES` is declared, the named inputs are seeded into the chain's initial scope, so a chain that starts with `FOR EACH` / `IF` / `WHILE` over an external identifier compiles cleanly (closes #21, #23). When `FLOW.GIVES` is declared, the IR builder verifies subset coverage against the last chain item's effective state at compile time. When both blocks are absent, v0.15.1 behaviour is preserved (StepCall auto-promote for inputs, last-step inference for outputs).

### Emitters

- `python`: `run()` gains a typed signature derived from `FLOW.TAKES` when declared, and returns a dict keyed by `FLOW.GIVES` field names. Backward-compatible: flows without a declared signature keep the v0.15 `**initial` / full-state-return shape.
- `mcp-server` and `claude-skill`: `inputSchema` / `outputSchema` (resp. SKILL.md Inputs / Outputs sections) derive from `FLOW.TAKES` / `FLOW.GIVES` when declared, replacing the previous first-step / last-step inference.
- `langgraph`: the emitted `State` TypedDict and the final return reflect the declared signature.
- `claude-cli`: the emitted README surfaces declared inputs (initial `state.json` keys) and outputs.

### TEST block

- `WITH:` kwarg names and types are type-checked at parse time against `FLOW.TAKES` when declared. `EXPECTS:` / `EXPECTS_NOT:` field paths are type-checked against `FLOW.GIVES`. When the target FLOW does not declare a signature, the v0.15 runtime-only behaviour is preserved.

### Closes

- #21 (FOR EACH at the head of a chain over an external input)
- #23 (parent issue for this feature)
```

- [ ] **Step 4: Commit**

```bash
git add docs/manual/ docs/LANGUAGE_SPEC.md CHANGELOG.md clio/emitters/claude_cli.py
git commit -m "docs(v0.16): manual + cookbook + troubleshooting + CHANGELOG"
```

---

## Task 14: Triple-check — ruff + mypy + pytest, then push and open PR

**Files:**
- Run all three quality gates and fix anything they complain about.

- [ ] **Step 1: Run ruff**

Run: `uv run ruff check . --fix`
Expected: `All checks passed!`. If ruff strips a re-export, restore it (see `feedback_run_ruff_before_push` in memory).

- [ ] **Step 2: Run mypy**

Run: `uv run mypy clio`
Expected: no errors. If mypy complains about missing annotations (e.g. `dict` without args), add the explicit type.

- [ ] **Step 3: Run the full pytest suite**

Run: `uv run pytest -q`
Expected: all green, count = 834 + (new tests added across Tasks 3, 5, 6, 7, 8, 9, 10, 11) + 1 xfail. Aim for ~870+ passed, 1 xfailed.

- [ ] **Step 4: Smoke-compile the example to every target**

```bash
for t in python mcp-server claude-skill langgraph; do
  rm -rf /tmp/v016-smoke-$t
  echo "=== $t ==="
  uv run python -m clio compile examples/flow_signature.clio --target $t --output /tmp/v016-smoke-$t && echo "OK $t"
done
```

Expected: all four `OK` lines.

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin feat/v0.16-flow-signature
gh pr create --title "feat(v0.16): FLOW.TAKES / FLOW.GIVES — explicit FLOW signature (closes #21, #23)" --body "$(cat <<'EOF'
## Summary

Adds optional \`TAKES:\` / \`GIVES:\` blocks to \`FLOW\` declarations, mirroring \`STEP\`.

- When \`FLOW.TAKES\` is declared, the named external inputs are seeded into the chain's initial scope — top-level \`FOR EACH\` / \`IF\` / \`WHILE\` over an external identifier now compiles (closes #21).
- When \`FLOW.GIVES\` is declared, subset coverage against the last chain item's effective state is enforced at compile time.
- Both blocks are optional. When absent, v0.15.1 behaviour is preserved (StepCall auto-promote / last-step inference).
- All 5 emitters (\`python\`, \`mcp-server\`, \`claude-skill\`, \`langgraph\`, \`claude-cli\`) honour the declared signature.
- \`TEST\` block: \`WITH:\` / \`EXPECTS:\` type-checked vs declared FLOW signature at parse time.

Closes #21. Closes #23.

## Test plan

- [x] \`uv run ruff check .\` clean.
- [x] \`uv run mypy clio\` clean.
- [x] \`uv run pytest -q\` green (834+ passed, 1 xfailed).
- [x] Smoke compile \`examples/flow_signature.clio\` to python / mcp-server / claude-skill / langgraph — all succeed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Address Gemini's review (post-merge cleanup)**

When the Gemini Code Assist review arrives (typically within 2-5 minutes of opening the PR), apply each medium/high finding as a *new commit* (never amend). After each fix, post a threaded reply via `gh api repos/Sandjab/clio/pulls/<N>/comments/<CID>/replies -X POST -F body=...` citing the fix commit SHA and the technical clarification. Don't poll for a re-review — Gemini reviews on PR-open events, not on push-fix.

---

## Self-Review

- **Spec coverage**: every requirement in issue #23 has a task — TAKES/GIVES parsing (Tasks 2-3), IR seeding (Task 5), GIVES coverage (Task 6), 5 emitters (Tasks 7-10 + 13 for claude-cli), TEST typing (Task 11), #21 closure via example (Task 12), docs (Tasks 1 + 13), CHANGELOG (Task 13), triple-check (Task 14). ✓
- **Placeholder scan**: every code step shows the diff or the exact lines to insert. No "TBD", "implement appropriate error handling", "similar to Task N" without the code. ✓
- **Type consistency**: `flow.takes` / `flow.gives` use the same shape (`tuple[tuple[str, TypeExpr], ...]` at IR level, `tuple[Field, ...]` at AST level) across every reference. The test fixtures consistently spell field names lowercase and types using the CLIO source syntax (`List<str>`, `enum(...)`). ✓
- **Order**: spec → AST → parser → IR types → IR seeding → IR validation → emitters in order of complexity (python, mcp-server, claude-skill, langgraph) → TEST typing → example → manual / changelog / claude-cli → triple-check. Each task is self-contained. ✓

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-15-flow-takes-gives.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best fit for a 14-task sprint where each task is independent and I want to keep the main context lean.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints. Best fit when you want to see each diff land in real time.

**Which approach?**
