# `FOR EACH ... PARALLEL AS <name>` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit parallelism primitive `FOR EACH x IN xs PARALLEL AS results:` that fans a single STEP across a collection. Supported by python (ThreadPoolExecutor) and mcp-server (asyncio.gather + Semaphore) targets; rejected by claude-cli at compile time. Default cap = 10. Fail-fast on first definitive iteration failure.

**Architecture:** Two-field extension (`parallel: bool`, `collector: str | None`) on `ForEachBlock` (AST) and `ForEachIR` (IR). New parser branch handles the `PARALLEL AS <ident>` suffix. Two new module-level emitter helpers (`emit_parallel_for_each_python` in `_python_helpers.py`, `emit_parallel_for_each_mcp` in `_mcp_helpers.py`). Existing flow-emission walkers gain a `parallel` branch. Sequential FOR EACH unchanged — v0 sources emit byte-identically.

**Tech Stack:** Python 3.12+, `concurrent.futures` (stdlib, python target), `asyncio` (stdlib, mcp-server target), pytest.

**Spec:** `docs/superpowers/specs/2026-05-07-parallel-foreach-design.md`.

---

## File structure

| File | Action | Responsibility |
|---|---|---|
| `clio/keywords.py` | Modify | Add `PARALLEL = "PARALLEL"`, `AS = "AS"` enum members. |
| `clio/parser/ast_nodes.py` | Modify | Extend `ForEachBlock` with `parallel: bool = False`, `collector: str | None = None`. |
| `clio/parser/parser.py` | Modify | Extend `parse_for_each` to consume the optional `PARALLEL AS <ident>` suffix. Emit parser-level errors with line numbers. |
| `clio/ir/graph.py` | Modify | Extend `ForEachIR` with the same two fields. |
| `clio/ir/builder.py` | Modify | Propagate `parallel`/`collector` from AST → IR. Add IR-builder validation rules (multi-step body, missing GIVES, collector collision, nested parallel). |
| `clio/emitters/_python_helpers.py` | Modify | Add `emit_parallel_for_each_python(elem, steps_by_name, indent) -> str`. |
| `clio/emitters/_mcp_helpers.py` | Modify | Add `emit_parallel_for_each_mcp(elem, steps_by_name, indent) -> str`. Walker dispatches on `elem.parallel`. Add `import asyncio` to emitted `flow.py` when needed. |
| `clio/emitters/python.py` | Modify | Walker dispatches on `elem.parallel`. Add `import concurrent.futures` to emitted `flow.py` when any parallel FOR EACH is present. |
| `clio/emitters/claude_cli.py` | Modify | Reject any `ForEachIR` with `parallel=True` at emit time. |
| `clio/graph_render.py` | Modify | Append `[parallel]` to the FOR EACH node label in Mermaid/DOT output when `parallel=True`. |
| `tests/test_parser.py` | Modify | Add 3 parser tests (basic + 2 rejections). |
| `tests/test_ir.py` | Modify | Add 7 IR-builder tests (1 happy path + 6 rejections). |
| `tests/test_emitters/test_python.py` | Modify | Add parallel-emission tests + byte-identical regression check. |
| `tests/test_emitters/test_mcp_server.py` | Modify | Add parallel-emission tests for the mcp-server target. |
| `tests/test_emitters/test_claude_cli.py` | Modify | Add rejection test. |
| `examples/parallel_classify.clio` | Create | Demo example for the README and docs. |
| `README.md` | Modify | One-liner mention in the language features list. |
| `docs/LANGUAGE_SPEC.md` | Modify | New `#### PARALLEL` subsection under FOR EACH; new row in the implementation-status table. |
| `docs/COMPILATION_TARGETS.md` | Modify | Update python and mcp-server sections with "FOR EACH PARALLEL supported (cap=10)". |
| `CHANGELOG.md` | Modify | New "Language" entry under Unreleased. |
| `tests/test_e2e_parallel.py` | Create (optional, gated) | Wall-clock parallelism check (gated `CLIO_E2E=1`). |

Tests parse emitted code source via Python's standard library — `re` for finding the right block, `ast.literal_eval` for safely parsing dict/list literals (the same pattern used by the mcp-server tests).

---

## Task 1: Lexer keywords + AST extension

**Files:**
- Modify: `clio/keywords.py`
- Modify: `clio/parser/ast_nodes.py`
- Modify: `tests/test_parser.py`

Add `PARALLEL` and `AS` to the keyword enum so the lexer tokenizes them as `KEYWORD` (not `IDENT`). Extend `ForEachBlock` with `parallel: bool = False` and `collector: str | None = None`. The defaults are critical — they preserve byte-identical AST shape for existing v0 sources.

- [ ] **Step 1: Write the failing test (parser-level not yet — just AST default check)**

Append to `tests/test_parser.py`:

```python
def test_for_each_block_defaults_are_sequential():
    """Sequential FOR EACH must build with parallel=False, collector=None."""
    flow = next(d for d in parse(_FOREACH_SRC).decls if d.__class__.__name__ == "FlowDecl")
    fe = flow.chain[1]
    assert fe.parallel is False
    assert fe.collector is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_parser.py::test_for_each_block_defaults_are_sequential -v`

Expected: FAIL with `AttributeError: 'ForEachBlock' object has no attribute 'parallel'`.

- [ ] **Step 3: Add keywords**

Edit `clio/keywords.py`. Add two new lines to the `Keyword` enum (alphabetical or appended at the end — match the file's convention):

```python
    PARALLEL = "PARALLEL"
    AS = "AS"
```

- [ ] **Step 4: Extend ForEachBlock dataclass**

Edit `clio/parser/ast_nodes.py`. Modify the `ForEachBlock` dataclass:

```python
@dataclass(frozen=True)
class ForEachBlock:
    """FOR EACH <loop_var> IN <collection>:
        <body>

    `collection` is the name of a state field (the GIVES of an upstream step).
    `body` is a chain of FlowItems executed for each element.

    `parallel=True` + `collector=<name>` means the body runs concurrently for
    each item, and results are collected into `state[<collector>]` as a list."""
    loop_var: str
    collection: str
    body: "tuple[StepCall | ForEachBlock, ...]"
    line: int
    col: int
    parallel: bool = False
    collector: str | None = None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_parser.py::test_for_each_block_defaults_are_sequential -v`

Expected: PASS.

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`

Expected: full suite green (no regression — sequential FOR EACH is unchanged).

- [ ] **Step 6: Commit**

```bash
git add clio/keywords.py clio/parser/ast_nodes.py tests/test_parser.py
git commit -m "$(cat <<'EOF'
feat(parser): add PARALLEL/AS keywords + extend ForEachBlock

PARALLEL and AS join the keyword enum so the lexer tokenizes them as
KEYWORD instead of IDENT. ForEachBlock gains parallel: bool = False
and collector: str | None = None — defaults preserve byte-identical
AST for sequential FOR EACH. Parser support for the new syntax lands
in Task 2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Parser — accept `PARALLEL AS <ident>` suffix

**Files:**
- Modify: `clio/parser/parser.py`
- Modify: `tests/test_parser.py`

Extend `parse_for_each` to optionally consume `PARALLEL AS <ident>` between `<collection>` and the trailing `:`. Reject `PARALLEL` without `AS` and `AS` without `PARALLEL` with line numbers from the source.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_parser.py`:

```python
_FOREACH_PARALLEL_SRC = (
    "STEP load\n"
    "  GIVES: items: List<str>\n"
    "  MODE: exact\n"
    "STEP process\n"
    "  TAKES: x: str\n"
    "  GIVES: r: str\n"
    "  MODE: exact\n"
    "FLOW pipe\n"
    "  load()\n"
    "    -> FOR EACH item IN items PARALLEL AS results:\n"
    "         process(x=item)\n"
)


def test_parse_for_each_parallel_with_as():
    flow = next(d for d in parse(_FOREACH_PARALLEL_SRC).decls if d.__class__.__name__ == "FlowDecl")
    fe = flow.chain[1]
    assert fe.parallel is True
    assert fe.collector == "results"
    assert fe.loop_var == "item"
    assert fe.collection == "items"


def test_parse_for_each_parallel_without_as_fails():
    bad = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL:\n"
        "         process(x=item)\n"
    )
    with pytest.raises(Exception, match="PARALLEL requires an AS"):
        parse(bad)


def test_parse_for_each_as_without_parallel_fails():
    bad = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items AS results:\n"
        "         process(x=item)\n"
    )
    with pytest.raises(Exception, match="AS binding is only valid with PARALLEL"):
        parse(bad)
```

If `import pytest` isn't already at the top of `tests/test_parser.py`, add it there (alongside other imports — don't introduce mid-file imports).

- [ ] **Step 2: Run failing tests**

Run: `.venv/bin/python -m pytest tests/test_parser.py -v -k "parallel or as_without"`

Expected: 3 FAILs.

- [ ] **Step 3: Extend `parse_for_each`**

Edit `clio/parser/parser.py`. Locate `parse_for_each` (around line 987). Replace the section that expects `COLON` with a branch that optionally consumes `PARALLEL AS <ident>` first.

The original (lines 996-998):
```python
        self.expect(TokenType.KEYWORD, "IN")
        collection_tok = self.expect(TokenType.IDENT)
        self.expect(TokenType.COLON)
```

Replace with:
```python
        self.expect(TokenType.KEYWORD, "IN")
        collection_tok = self.expect(TokenType.IDENT)

        parallel = False
        collector: str | None = None

        # Optional `PARALLEL AS <ident>` between collection and ':'
        next_tok = self.peek()
        if next_tok.type == TokenType.KEYWORD and next_tok.value == "PARALLEL":
            self.advance()
            as_tok = self.peek()
            if not (as_tok.type == TokenType.KEYWORD and as_tok.value == "AS"):
                raise SyntaxError(
                    f"PARALLEL requires an AS <name> binding "
                    f"(line {next_tok.line}, col {next_tok.col})"
                )
            self.advance()
            collector_tok = self.expect(TokenType.IDENT)
            parallel = True
            collector = collector_tok.value
        elif next_tok.type == TokenType.KEYWORD and next_tok.value == "AS":
            raise SyntaxError(
                f"AS binding is only valid with PARALLEL — sequential FOR EACH "
                f"discards results (line {next_tok.line}, col {next_tok.col})"
            )

        self.expect(TokenType.COLON)
```

Then update the `return ForEachBlock(...)` at the end of the method to pass the new fields:

```python
        return ForEachBlock(
            loop_var=var_tok.value,
            collection=collection_tok.value,
            body=tuple(body),
            line=kw.line, col=kw.col,
            parallel=parallel,
            collector=collector,
        )
```

The exact exception class (`SyntaxError` vs the project's existing `ParserError` if there is one) should match what other parser-level errors raise. Check existing `raise` statements in `parser.py` for the canonical type.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_parser.py -v -k "parallel or as_without or each"`

Expected: all parser FOR EACH tests pass (4 happy path + 2 negative + 1 default = 7 + existing).

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`

Expected: full suite green.

- [ ] **Step 5: Commit**

```bash
git add clio/parser/parser.py tests/test_parser.py
git commit -m "$(cat <<'EOF'
feat(parser): parse FOR EACH ... PARALLEL AS <ident>

Optionally consume PARALLEL AS <ident> between IN <collection> and ':'.
Sequential FOR EACH unchanged — same AST shape (parallel=False,
collector=None) for v0 sources. Parser-level rejections (PARALLEL
without AS, AS without PARALLEL) include the source line number.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: IR — extend `ForEachIR` + propagation in builder

**Files:**
- Modify: `clio/ir/graph.py`
- Modify: `clio/ir/builder.py`
- Modify: `tests/test_ir.py`

Extend `ForEachIR` with the same `parallel` and `collector` fields. The IR builder copies them from `ForEachBlock` to `ForEachIR`. This task only handles propagation — IR-builder validations land in Task 4.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ir.py`:

```python
def test_ir_propagates_parallel_for_each_fields():
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL AS results:\n"
        "         process(x=item)\n"
    )
    g = build_ir(parse(src))
    fe = next(elem for elem in g.flow.chain if elem.__class__.__name__ == "ForEachIR")
    assert fe.parallel is True
    assert fe.collector == "results"


def test_ir_sequential_for_each_defaults_unchanged():
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items:\n"
        "         process(x=item)\n"
    )
    g = build_ir(parse(src))
    fe = next(elem for elem in g.flow.chain if elem.__class__.__name__ == "ForEachIR")
    assert fe.parallel is False
    assert fe.collector is None
```

If `parse` and `build_ir` aren't already imported in `tests/test_ir.py`, add them.

- [ ] **Step 2: Run failing tests**

Run: `.venv/bin/python -m pytest tests/test_ir.py -v -k "propagates_parallel or sequential_for_each_defaults"`

Expected: 2 FAILs (`AttributeError: 'ForEachIR' object has no attribute 'parallel'`).

- [ ] **Step 3: Extend `ForEachIR`**

Edit `clio/ir/graph.py`. Modify the `ForEachIR` dataclass:

```python
@dataclass(frozen=True)
class ForEachIR:
    """IR mirror of ForEachBlock: iterate `loop_var` over `collection` (a state
    field name), executing `body` for each element.

    `parallel=True` + `collector=<name>` means the body runs concurrently for
    each item, and results are collected into `state[<collector>]` as a list."""
    loop_var: str
    collection: str
    body: "tuple[CallIR | ForEachIR, ...]"
    line: int
    parallel: bool = False
    collector: str | None = None
```

- [ ] **Step 4: Propagate in the builder**

Edit `clio/ir/builder.py`. Find the call site that constructs `ForEachIR(...)` from a `ForEachBlock`. Pass the two new fields:

```python
    return ForEachIR(
        loop_var=block.loop_var,
        collection=block.collection,
        body=tuple(...),
        line=block.line,
        parallel=block.parallel,
        collector=block.collector,
    )
```

(The exact signature shape may differ — match the existing call site's style.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ir.py -v -k "propagates_parallel or sequential_for_each_defaults"`

Expected: 2 PASS.

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`

Expected: full suite green.

- [ ] **Step 6: Commit**

```bash
git add clio/ir/graph.py clio/ir/builder.py tests/test_ir.py
git commit -m "$(cat <<'EOF'
feat(ir): propagate parallel/collector fields from ForEachBlock to ForEachIR

ForEachIR mirrors ForEachBlock with parallel: bool = False and
collector: str | None = None. IR builder copies the values across.
Validations (multi-step body, missing GIVES, collector collision,
nested parallel) land in Task 4.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: IR-builder validations

**Files:**
- Modify: `clio/ir/builder.py`
- Modify: `tests/test_ir.py`

Add 6 validation rules to the IR-builder. Each rejection includes the source line number. The validations only fire when `parallel=True`; sequential FOR EACH is unchanged.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_ir.py`:

```python
def test_ir_rejects_parallel_multi_step_body():
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP a\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "STEP b\n  TAKES: y: str\n  GIVES: z: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL AS results:\n"
        "         a(x=item)\n"
        "           -> b(y)\n"
    )
    with pytest.raises(ValueError, match="must contain exactly one step call"):
        build_ir(parse(src))


def test_ir_rejects_parallel_with_nested_for_each_body():
    src = (
        "STEP load\n  GIVES: matrix: List<str>\n  MODE: exact\n"
        "STEP inner\n  TAKES: cell: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH row IN matrix PARALLEL AS rows:\n"
        "         FOR EACH cell IN row:\n"
        "           inner(cell=cell)\n"
    )
    with pytest.raises(ValueError, match="cannot contain nested FOR EACH"):
        build_ir(parse(src))


def test_ir_rejects_parallel_body_step_without_gives():
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP sink\n  TAKES: x: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL AS results:\n"
        "         sink(x=item)\n"
    )
    with pytest.raises(ValueError, match="must have a GIVES"):
        build_ir(parse(src))


def test_ir_rejects_parallel_collector_shadowing_state_field():
    """The collector must not collide with a state field already populated
    upstream in the FLOW chain (the GIVES name of a prior step in this case)."""
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL AS items:\n"  # 'items' shadows the upstream GIVES
        "         process(x=item)\n"
    )
    with pytest.raises(ValueError, match="shadows existing state field"):
        build_ir(parse(src))


def test_ir_rejects_nested_parallel():
    """Two nested PARALLEL blocks (transitive) are rejected in v1."""
    # Constructed via direct IR (parser would also reject the nested syntactic
    # shape via the multi-step / nested-foreach guard; this test ensures the
    # specific 'nested PARALLEL' rule fires when the inner block is itself
    # PARALLEL).
    from clio.ir.graph import ForEachIR, CallIR, FlowIR, FlowGraph, StepIR, FieldIR
    from clio.parser.ast_nodes import PrimitiveType

    inner = ForEachIR(
        loop_var="y",
        collection="ys",
        body=(CallIR(step_name="leaf", kwargs=(("y", "@y"),), line=1),),
        line=1,
        parallel=True,
        collector="leaf_results",
    )
    outer = ForEachIR(
        loop_var="x",
        collection="xs",
        body=(inner,),  # inner is also PARALLEL — should be rejected
        line=1,
        parallel=True,
        collector="outer_results",
    )
    flow = FlowIR(name="pipe", chain=(outer,), line=1)
    leaf = StepIR(
        name="leaf",
        takes=(FieldIR(name="y", type=PrimitiveType("str")),),
        gives=FieldIR(name="r", type=PrimitiveType("str")),
        mode="exact",
        impl=None, invoke=None, cache=None, on_fail=None, line=1,
    )
    graph = FlowGraph(steps=(leaf,), contracts=(), flow=flow)

    # Builder helper that runs validations on an already-constructed graph.
    # The exact entry point depends on the builder's API; if there is no
    # validate-after-build hook, this test triggers the nested-parallel
    # check by re-running the validator on the constructed graph. See
    # builder.py:_validate_parallel_for_each (added in Step 3).
    from clio.ir.builder import _validate_parallel_for_each
    with pytest.raises(ValueError, match="nested inside another PARALLEL"):
        _validate_parallel_for_each(graph)


def test_ir_accepts_parallel_inside_sequential_foreach():
    """A PARALLEL block inside a *sequential* FOR EACH is allowed (the outer
    is not parallel, so there's no nested-parallel issue)."""
    # NOTE: parser currently rejects multi-step body for PARALLEL but does NOT
    # forbid the inverse — a sequential FOR EACH that contains a single body
    # element which is itself a PARALLEL FOR EACH. v1's nested-parallel rule
    # is transitive on PARALLEL ancestors only.
    src = (
        "STEP load_outer\n  GIVES: groups: List<str>\n  MODE: exact\n"
        "STEP load_inner\n  TAKES: g: str\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load_outer()\n"
        "    -> FOR EACH g IN groups:\n"
        "         FOR EACH item IN items PARALLEL AS results:\n"
        "           process(x=item)\n"
    )
    # Should NOT raise — outer is sequential, inner is parallel.
    g = build_ir(parse(src))
    outer = g.flow.chain[1]
    assert outer.parallel is False
    inner = outer.body[0]
    assert inner.parallel is True
```

The `test_ir_rejects_nested_parallel` test references `_validate_parallel_for_each` which is added in Step 3. If you prefer to drive the rejection through `build_ir` (the public API) for consistency, restructure the test to use a `.clio` source where `build_ir` would catch the nesting via the multi-step / nested-foreach guard. The current shape exercises the validation directly because the parser already rejects most nested PARALLEL forms via Step 1 of Task 2.

- [ ] **Step 2: Run failing tests**

Run: `.venv/bin/python -m pytest tests/test_ir.py -v -k "rejects_parallel or nested_parallel or accepts_parallel"`

Expected: 6 FAILs.

- [ ] **Step 3: Implement validations**

Edit `clio/ir/builder.py`. Add a new module-level function `_validate_parallel_for_each(graph)` that walks `graph.flow.chain` and applies the 6 rules. Call it at the end of `build_ir(...)` (or wherever the builder finalizes the graph).

```python
def _validate_parallel_for_each(graph) -> None:
    """Enforce v1 constraints on FOR EACH PARALLEL blocks.
    Each error includes the source line number from the .clio source."""
    if graph.flow is None:
        return

    steps_by_name = {s.name: s for s in graph.steps}

    # Track state-field names populated upstream in the FLOW chain so we can
    # detect collector collisions.
    populated: set[str] = set()
    # The first step's TAKES are seeded as initial state — treat them as
    # populated from the start.
    if graph.flow.chain:
        first = graph.flow.chain[0]
        if hasattr(first, "step_name"):
            first_step = steps_by_name.get(first.step_name)
            if first_step is not None:
                for t in first_step.takes:
                    populated.add(t.name)

    def _walk(chain, ancestor_parallel: bool) -> None:
        nonlocal populated
        for elem in chain:
            if hasattr(elem, "step_name"):
                # CallIR — record GIVES into populated state
                step = steps_by_name.get(elem.step_name)
                if step is not None and step.gives is not None:
                    populated.add(step.gives.name)
                continue

            # ForEachIR
            if elem.parallel:
                if ancestor_parallel:
                    raise ValueError(
                        f"FOR EACH PARALLEL cannot be nested inside another "
                        f"PARALLEL block in v1 (line {elem.line})"
                    )
                if len(elem.body) != 1:
                    raise ValueError(
                        f"FOR EACH PARALLEL body must contain exactly one "
                        f"step call in v1 (line {elem.line})"
                    )
                inner = elem.body[0]
                if not hasattr(inner, "step_name"):
                    raise ValueError(
                        f"FOR EACH PARALLEL cannot contain nested FOR EACH "
                        f"in v1 (line {elem.line})"
                    )
                step = steps_by_name.get(inner.step_name)
                if step is None or step.gives is None:
                    raise ValueError(
                        f"FOR EACH PARALLEL body step "
                        f"{inner.step_name!r} must have a GIVES "
                        f"(line {elem.line})"
                    )
                if elem.collector in populated:
                    raise ValueError(
                        f"AS {elem.collector!r} shadows existing state "
                        f"field; rename the collector (line {elem.line})"
                    )
                populated.add(elem.collector)
            else:
                # Sequential FOR EACH — descend, but the inner is allowed to
                # be PARALLEL (we just track ancestor_parallel correctly).
                _walk(elem.body, ancestor_parallel)

    _walk(graph.flow.chain, ancestor_parallel=False)
```

Then at the end of `build_ir(...)`, after the graph is fully constructed, call:

```python
    _validate_parallel_for_each(graph)
    return graph
```

(The exact placement depends on the builder's existing structure. If `build_ir` already has a validation pass, slot this into it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ir.py -v -k "rejects_parallel or nested_parallel or accepts_parallel"`

Expected: 6 PASS.

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`

Expected: full suite green.

- [ ] **Step 5: Commit**

```bash
git add clio/ir/builder.py tests/test_ir.py
git commit -m "$(cat <<'EOF'
feat(ir): validate FOR EACH PARALLEL constraints (v1)

Reject at IR-build time: multi-step body, nested ForEach in body,
body step without GIVES, collector colliding with state field, and
nested PARALLEL (transitive). Each rejection includes the source
line number from the .clio source.

PARALLEL inside a *sequential* FOR EACH is allowed — only nested
PARALLEL is rejected.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: python emitter — `emit_parallel_for_each_python`

**Files:**
- Modify: `clio/emitters/_python_helpers.py`
- Modify: `clio/emitters/python.py`
- Modify: `tests/test_emitters/test_python.py`

Add a module-level helper that emits the `ThreadPoolExecutor` block for a parallel FOR EACH. The python target's flow walker (`_emit_flow`) gains a `parallel` branch. `flow.py` gains `import concurrent.futures` at module top when any parallel block is present.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_emitters/test_python.py`:

```python
_PARALLEL_FOR_EACH_SRC = (
    "STEP load\n  GIVES: docs: List<str>\n  MODE: exact\n"
    "STEP classify\n  TAKES: text: str\n  GIVES: label: str\n  MODE: exact\n"
    "FLOW pipe\n"
    "  load()\n"
    "    -> FOR EACH doc IN docs PARALLEL AS labels:\n"
    "         classify(text=doc)\n"
)


def test_python_emits_thread_pool_for_parallel_for_each(tmp_path):
    PythonEmitter().emit(build_ir(parse(_PARALLEL_FOR_EACH_SRC)), tmp_path)
    flow_py = (tmp_path / "pipe" / "flow.py").read_text()
    assert "import concurrent.futures" in flow_py
    assert "ThreadPoolExecutor(max_workers=10)" in flow_py
    assert "concurrent.futures.as_completed" in flow_py
    assert "_results = [None] *" in flow_py
    assert "state['labels'] = _results" in flow_py or 'state["labels"] = _results' in flow_py


def test_python_does_not_import_concurrent_when_no_parallel(tmp_path):
    """Sequential-only flows must not pull in concurrent.futures (unused dep)."""
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items:\n"
        "         process(x=item)\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "pipe" / "flow.py").read_text()
    assert "concurrent.futures" not in flow_py
```

- [ ] **Step 2: Run failing tests**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_python.py -v -k "thread_pool or does_not_import"`

Expected: 1 FAIL on the first (no `ThreadPoolExecutor` emitted), 1 PASS on the second (no parallel = no import — already true).

- [ ] **Step 3: Add the helper to `_python_helpers.py`**

Edit `clio/emitters/_python_helpers.py`. Append:

```python
def emit_parallel_for_each_python(
    elem: "ForEachIR",
    steps_by_name: dict,
    indent: str,
) -> str:
    """Emit a ThreadPoolExecutor block for a parallel FOR EACH (python target).

    The body is guaranteed (by IR validation) to be a single CallIR with a
    GIVES. Default cap is 10. Failure semantics: ThreadPoolExecutor's `with`
    exit cancels queued futures; in-flight tasks finish; the first
    `_fut.result()` to raise propagates."""
    inner = elem.body[0]
    step = steps_by_name[inner.step_name]

    # Render kwargs using the @-prefix disambiguation. Loop var is in scope.
    scope_local = {elem.loop_var}
    kw_parts: list[str] = []
    for name, value in inner.kwargs:
        if isinstance(value, str) and value.startswith("@"):
            ref = value[1:]
            if ref in scope_local:
                kw_parts.append(f"{name}={ref}")
            else:
                kw_parts.append(f"{name}=state[{ref!r}]")
        else:
            kw_parts.append(f"{name}={value!r}")
    kwargs_str = ", ".join(kw_parts)

    # Render the collection lookup. Sequential FOR EACH may emit `state[<col>]`
    # or a bare scope_local; in PARALLEL, IR validation guarantees no nested
    # parallel, so the collection always lives in state.
    items_lookup = f"state[{elem.collection!r}]"

    return (
        f"{indent}_items = {items_lookup}\n"
        f"{indent}_results = [None] * len(_items)\n"
        f"{indent}with concurrent.futures.ThreadPoolExecutor(max_workers=10) as _ex:\n"
        f"{indent}    _futures = {{_ex.submit({step.name}_mod.{step.name}, {kwargs_str}): _i "
        f"for _i, {elem.loop_var} in enumerate(_items)}}\n"
        f"{indent}    for _fut in concurrent.futures.as_completed(_futures):\n"
        f"{indent}        _idx = _futures[_fut]\n"
        f"{indent}        _results[_idx] = _fut.result()\n"
        f"{indent}state[{elem.collector!r}] = _results"
    )
```

(The function does not include a trailing newline; the caller appends one when concatenating into `chain_lines`.)

Add `ForEachIR` to the type-only imports if not already present.

- [ ] **Step 4: Wire the python emitter walker**

Edit `clio/emitters/python.py`. Locate the `_emit_item` function inside `_emit_flow` (around line 600). Add a parallel branch:

```python
        def _emit_item(item, indent: str, scope_local: set[str]) -> None:
            if isinstance(item, ForEachIR):
                if item.parallel:
                    chain_lines.append(emit_parallel_for_each_python(item, steps_by_name, indent))
                    # The step's name must be tracked as imported.
                    inner = item.body[0]
                    if inner.step_name not in imported_steps:
                        imported_steps.append(inner.step_name)
                    return
                # ... existing sequential path ...
```

Where `steps_by_name` is computed at the top of `_emit_flow`:

```python
        steps_by_name = {s.name: s for s in graph.steps}
```

Add `emit_parallel_for_each_python` to the existing import block in `python.py`. Add an `import concurrent.futures` line to the emitted `flow.py` ONLY when at least one parallel FOR EACH is present. Determine this by walking the chain once before emitting:

```python
        def _has_parallel(chain) -> bool:
            for elem in chain:
                if isinstance(elem, ForEachIR):
                    if elem.parallel:
                        return True
                    if _has_parallel(elem.body):
                        return True
            return False

        needs_concurrent = _has_parallel(graph.flow.chain) if graph.flow else False
```

Then the emitted `flow.py` header becomes (insert `import concurrent.futures` only when needed):

```python
        cf_import = "import concurrent.futures\n\n" if needs_concurrent else ""
        return (
            f'"""FLOW {graph.flow.name}.\n\n'
            f'Auto-generated. Calls steps in chain order, threading state through a dict.\n'
            f'"""\n'
            f'\n'
            f'{cf_import}'
            f'{imports}\n'
            ...
        )
```

(Match the existing template's exact shape — only insert the import; don't rewrite anything else.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_python.py -v -k "thread_pool or does_not_import"`

Expected: 2 PASS.

Run: `.venv/bin/python -m pytest tests/test_emitters/test_python.py -q | tail -5`

Expected: existing python tests still green (byte-identical for sequential FOR EACH).

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`

Expected: full suite green.

- [ ] **Step 6: Commit**

```bash
git add clio/emitters/_python_helpers.py clio/emitters/python.py tests/test_emitters/test_python.py
git commit -m "$(cat <<'EOF'
feat(python): emit ThreadPoolExecutor for FOR EACH PARALLEL

emit_parallel_for_each_python helper renders a ThreadPoolExecutor
block with max_workers=10 and as_completed-driven gathering. flow.py
gets `import concurrent.futures` at module top when at least one
parallel FOR EACH is present in the graph; sequential-only flows
remain byte-identical (no spurious dependency).

Failure semantics: first _fut.result() raise propagates; with-block
exit cancels queued futures.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: mcp-server emitter — `emit_parallel_for_each_mcp`

**Files:**
- Modify: `clio/emitters/_mcp_helpers.py`
- Modify: `tests/test_emitters/test_mcp_server.py`

Add the async equivalent for the mcp-server target. `asyncio.gather` + `asyncio.Semaphore(10)`. For judgment-mode body steps, threads `_session=_session` per task. `flow.py` gets `import asyncio` at module top when any parallel block is present (asyncio is already imported in mcp-server's `__main__.py`, but `flow.py` needs its own import for `Semaphore`).

- [ ] **Step 1: Write failing tests**

Append to `tests/test_emitters/test_mcp_server.py`:

```python
_PARALLEL_EXACT_SRC = (
    "STEP load\n  GIVES: docs: List<str>\n  MODE: exact\n"
    "STEP classify\n  TAKES: text: str\n  GIVES: label: str\n  MODE: exact\n"
    "FLOW pipe\n"
    "  load()\n"
    "    -> FOR EACH doc IN docs PARALLEL AS labels:\n"
    "         classify(text=doc)\n"
)


_PARALLEL_JUDGMENT_SRC = (
    "STEP load\n  GIVES: docs: List<str>\n  MODE: exact\n"
    "STEP classify\n  TAKES: text: str\n  GIVES: label: str\n  MODE: judgment\n"
    "FLOW pipe\n"
    "  load()\n"
    "    -> FOR EACH doc IN docs PARALLEL AS labels:\n"
    "         classify(text=doc)\n"
)


def test_mcp_emits_asyncio_gather_for_parallel_for_each(tmp_path):
    MCPServerEmitter().emit(build_ir(parse(_PARALLEL_EXACT_SRC)), tmp_path)
    flow_py = (tmp_path / "pipe" / "flow.py").read_text()
    assert "import asyncio" in flow_py
    assert "asyncio.Semaphore(10)" in flow_py
    assert "asyncio.gather" in flow_py
    assert "state['labels']" in flow_py or 'state["labels"]' in flow_py


def test_mcp_parallel_judgment_threads_session_per_iteration(tmp_path):
    MCPServerEmitter().emit(build_ir(parse(_PARALLEL_JUDGMENT_SRC)), tmp_path)
    flow_py = (tmp_path / "pipe" / "flow.py").read_text()
    # Each task awaits the judgment step with _session.
    assert "await classify_mod.classify" in flow_py
    assert "_session=_session" in flow_py


def test_mcp_does_not_import_asyncio_in_flow_when_no_parallel(tmp_path):
    """Sequential-only flow.py must not gain a top-level `import asyncio`
    (not strictly harmful, but pollutes the output unnecessarily)."""
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items:\n"
        "         process(x=item)\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "pipe" / "flow.py").read_text()
    assert "import asyncio" not in flow_py
```

- [ ] **Step 2: Run failing tests**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v -k "parallel"`

Expected: 2 FAILs, 1 PASS.

- [ ] **Step 3: Add the helper to `_mcp_helpers.py`**

Edit `clio/emitters/_mcp_helpers.py`. Append:

```python
def emit_parallel_for_each_mcp(
    elem: "ForEachIR",
    steps_by_name: dict,
    indent: str,
) -> str:
    """Emit an asyncio.gather block for a parallel FOR EACH (mcp-server target).

    Body is guaranteed (by IR validation) to be a single CallIR with a GIVES.
    Default cap is 10. _session is threaded into each task for judgment-mode
    body steps (await + _session=_session). Exact-mode body steps drop the
    await and the _session kwarg (matching the sync python target's call shape,
    but wrapped in an async helper because we're inside an async context)."""
    inner = elem.body[0]
    step = steps_by_name[inner.step_name]
    is_judgment = step.mode == "judgment"

    # Render kwargs (loop var in scope; @-prefix disambiguation).
    scope_local = {elem.loop_var}
    kw_parts: list[str] = []
    for name, value in inner.kwargs:
        if isinstance(value, str) and value.startswith("@"):
            ref = value[1:]
            if ref in scope_local:
                kw_parts.append(f"{name}={ref}")
            else:
                kw_parts.append(f"{name}=state[{ref!r}]")
        else:
            kw_parts.append(f"{name}={value!r}")
    kwargs_str = ", ".join(kw_parts)

    if is_judgment:
        if kwargs_str:
            call_expr = f"await {step.name}_mod.{step.name}({kwargs_str}, _session=_session)"
        else:
            call_expr = f"await {step.name}_mod.{step.name}(_session=_session)"
    else:
        # Exact step: sync. v1 calls it directly inside the async wrapper.
        # Future iteration could wrap in await asyncio.to_thread(...) for
        # true non-blocking behavior.
        call_expr = f"{step.name}_mod.{step.name}({kwargs_str})"

    items_lookup = f"state[{elem.collection!r}]"
    bound_name = f"_bound_{elem.collector}"

    return (
        f"{indent}_items = {items_lookup}\n"
        f"{indent}_sem = asyncio.Semaphore(10)\n"
        f"{indent}async def {bound_name}({elem.loop_var}):\n"
        f"{indent}    async with _sem:\n"
        f"{indent}        return {call_expr}\n"
        f"{indent}state[{elem.collector!r}] = await asyncio.gather("
        f"*[{bound_name}(_x) for _x in _items])"
    )
```

Note: for **exact-mode** body steps inside an async parallel block, this v1 calls them directly (`step.name_mod.step.name(...)`) — they're sync, so they block the event loop briefly. That's acceptable for v1; a future iteration could wrap in `await asyncio.to_thread(...)` for true non-blocking behavior. Document the limitation in the spec's "Open questions" section.

- [ ] **Step 4: Wire the mcp-server walker**

Edit `clio/emitters/_mcp_helpers.py`. Locate `_emit_flow_module_async`. Find the inner walker (`_emit_call` / `_emit_item` or equivalent — the function that handles `ForEachIR` items in the chain). Add a parallel branch:

```python
        # Inside _emit_item:
        if isinstance(elem, ForEachIR):
            if elem.parallel:
                chain_lines.append(emit_parallel_for_each_mcp(elem, steps_by_name, indent))
                inner = elem.body[0]
                if inner.step_name not in imported_steps:
                    imported_steps.append(inner.step_name)
                continue
            # ... existing sequential path ...
```

`steps_by_name = {s.name: s for s in graph.steps}` at the top of `_emit_flow_module_async` (already there if you're following the post-Task-6-of-mcp-server structure; otherwise add it).

For the `import asyncio` at the top of emitted `flow.py` — same `_has_parallel` check pattern as Task 5:

```python
def _has_parallel(chain) -> bool:
    for elem in chain:
        if isinstance(elem, ForEachIR):
            if elem.parallel:
                return True
            if _has_parallel(elem.body):
                return True
    return False
```

When `_has_parallel(graph.flow.chain)` is true, prepend `"import asyncio\n"` (or "from asyncio ... " — match what the file already does) to the emitted flow.py. (The mcp-server `flow.py` already has `from __future__ import annotations`; insert `import asyncio` after it but before the step imports.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v -k "parallel"`

Expected: 3 PASS.

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`

Expected: full suite green.

- [ ] **Step 6: Sanity compile**

```bash
rm -rf /tmp/clio-parallel-task6 && \
  .venv/bin/python -m clio compile examples/parallel_classify.clio --target mcp-server --output /tmp/clio-parallel-task6 && \
  cat /tmp/clio-parallel-task6/*/flow.py
```

(Skip if `examples/parallel_classify.clio` doesn't exist yet — that's Task 8.)

- [ ] **Step 7: Commit**

```bash
git add clio/emitters/_mcp_helpers.py tests/test_emitters/test_mcp_server.py
git commit -m "$(cat <<'EOF'
feat(mcp): emit asyncio.gather + Semaphore(10) for FOR EACH PARALLEL

emit_parallel_for_each_mcp helper renders an async block:
- _bound_<collector> coroutine guarded by Semaphore(10)
- asyncio.gather over the items list
- judgment-mode body: await + _session=_session per task
- exact-mode body: direct sync call inside the async wrapper

flow.py gains `import asyncio` only when at least one parallel block
is present.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: claude-cli rejection

**Files:**
- Modify: `clio/emitters/claude_cli.py`
- Modify: `tests/test_emitters/test_claude_cli.py`

Reject any FOR EACH PARALLEL at compile time when targeting claude-cli, with a clear message pointing at the alternative targets.

- [ ] **Step 1: Write failing test**

Append to `tests/test_emitters/test_claude_cli.py`:

```python
import pytest


def test_claude_cli_rejects_parallel(tmp_path):
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL AS results:\n"
        "         process(x=item)\n"
    )
    with pytest.raises(ValueError, match="claude-cli target does not support FOR EACH PARALLEL"):
        ClaudeCLIEmitter().emit(build_ir(parse(src)), tmp_path)
```

(Adapt class name `ClaudeCLIEmitter` to whatever the file imports.)

- [ ] **Step 2: Run failing test**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_claude_cli.py -v -k "rejects_parallel"`

Expected: FAIL — emitter doesn't raise.

- [ ] **Step 3: Implement the rejection**

Edit `clio/emitters/claude_cli.py`. Add a validator method (or inline check) at the top of `emit()`:

```python
    def _reject_parallel(self, graph: FlowGraph) -> None:
        from clio.ir.graph import ForEachIR

        def _walk(chain):
            for elem in chain:
                if isinstance(elem, ForEachIR):
                    if elem.parallel:
                        raise ValueError(
                            "claude-cli target does not support FOR EACH "
                            "PARALLEL; use --target python or --target mcp-server "
                            f"(line {elem.line})"
                        )
                    _walk(elem.body)

        if graph.flow is not None:
            _walk(graph.flow.chain)

    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        self._reject_parallel(graph)
        # ... rest of emit unchanged ...
```

Place the call at the very top of `emit()`, before any directory creation.

- [ ] **Step 4: Run tests to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_claude_cli.py -v -k "rejects_parallel"`

Expected: PASS.

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`

Expected: full suite green.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/claude_cli.py tests/test_emitters/test_claude_cli.py
git commit -m "$(cat <<'EOF'
feat(claude-cli): reject FOR EACH PARALLEL at compile time

The bash target's xargs -P + JSON aggregation glue is non-trivial
and out of v1 scope. Refusal points users at --target python or
--target mcp-server, both of which fully support PARALLEL.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Example + graph annotation

**Files:**
- Create: `examples/parallel_classify.clio`
- Modify: `clio/graph_render.py`
- Modify: `tests/test_graph_render.py` (or wherever graph tests live)

Provide a runnable example demonstrating the pattern, and surface PARALLEL in the Mermaid/DOT graph output so users can see the structure visually.

- [ ] **Step 1: Create the example**

Write `examples/parallel_classify.clio`:

```clio
STEP load_corpus
  GIVES: docs: List<str>
  MODE:  exact

STEP classify
  TAKES: text: str
  GIVES: label: str
  MODE:  judgment

STEP aggregate
  TAKES: labels: List<str>
  GIVES: summary: str
  MODE:  judgment

FLOW pipe
  load_corpus()
    -> FOR EACH doc IN docs PARALLEL AS labels:
         classify(text=doc)
    -> aggregate(labels=labels)
```

- [ ] **Step 2: Verify the example compiles to both targets**

Run:
```bash
rm -rf /tmp/clio-parallel-py /tmp/clio-parallel-mcp
.venv/bin/python -m clio compile examples/parallel_classify.clio --target python --output /tmp/clio-parallel-py
.venv/bin/python -m clio compile examples/parallel_classify.clio --target mcp-server --output /tmp/clio-parallel-mcp
```

Both should succeed silently. Spot-check the emitted `flow.py` files for `ThreadPoolExecutor` (python) and `asyncio.gather` (mcp-server).

- [ ] **Step 3: Verify claude-cli refuses**

Run:
```bash
.venv/bin/python -m clio compile examples/parallel_classify.clio --target claude-cli --output /tmp/clio-parallel-cli
```

Expected: exit non-zero, error message "claude-cli target does not support FOR EACH PARALLEL".

- [ ] **Step 4: Write graph-render test**

Append to `tests/test_graph_render.py` (create the file if it doesn't exist; check existing test structure):

```python
def test_graph_mermaid_marks_parallel_for_each():
    src = (
        "STEP load\n  GIVES: docs: List<str>\n  MODE: exact\n"
        "STEP classify\n  TAKES: text: str\n  GIVES: label: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH doc IN docs PARALLEL AS labels:\n"
        "         classify(text=doc)\n"
    )
    output = render_mermaid(build_ir(parse(src)))  # or whatever the entry point is
    assert "[parallel]" in output or "PARALLEL" in output
```

- [ ] **Step 5: Run failing test**

Run: `.venv/bin/python -m pytest tests/test_graph_render.py -v -k "parallel"`

Expected: FAIL — current output doesn't mark parallel.

- [ ] **Step 6: Update `graph_render.py`**

Edit `clio/graph_render.py`. Find the function that renders ForEachIR nodes. Append `[parallel]` (or a similar concise marker) to the node label when `elem.parallel` is true. Keep the marker compact (it has to fit in a Mermaid node label).

```python
    if elem.parallel:
        label = f"FOR EACH {elem.loop_var} IN {elem.collection} [parallel]"
    else:
        label = f"FOR EACH {elem.loop_var} IN {elem.collection}"
```

- [ ] **Step 7: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_graph_render.py -v -k "parallel"`

Expected: PASS.

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`

Expected: full suite green.

- [ ] **Step 8: Commit**

```bash
git add examples/parallel_classify.clio clio/graph_render.py tests/test_graph_render.py
git commit -m "$(cat <<'EOF'
feat(examples,graph): parallel_classify.clio + [parallel] graph annotation

New example demonstrates FOR EACH PARALLEL with a judgment body and
a downstream aggregate. The graph renderer (Mermaid/DOT) marks
parallel FOR EACH nodes with a [parallel] suffix so the visual
structure is obvious.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Project-level documentation

**Files:**
- Modify: `docs/LANGUAGE_SPEC.md`
- Modify: `docs/COMPILATION_TARGETS.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`

Pure documentation — no code changes.

- [ ] **Step 1: Update `docs/LANGUAGE_SPEC.md`**

In the `### FOR EACH` section, add a `#### PARALLEL` subsection:

```markdown
#### PARALLEL

Fan a single STEP over a collection in parallel. The collected results land in `state[<collector>]` as a `List<step.gives.type>`.

```clio
FOR EACH <loop_var> IN <collection> PARALLEL AS <collector>:
  <single_step_call(loop_var)>
```

**v1 constraints:**
- Body is exactly one step call (no chains, no nested FOR EACH).
- The body step must have a `GIVES` (otherwise the collector type is undefined).
- Default concurrency cap = 10. Not configurable in v1.
- Failure mode = fail-fast (first definitive failure cancels siblings on mcp-server, raises after queued cancellation on python).
- Nested PARALLEL (transitive) is rejected. PARALLEL inside a sequential FOR EACH is allowed.
- claude-cli target rejects PARALLEL at compile time; use `--target python` or `--target mcp-server`.

**Emission:**
- python target → `concurrent.futures.ThreadPoolExecutor(max_workers=10)` + `as_completed` (preserves order via indexed write).
- mcp-server target → `asyncio.gather` + `asyncio.Semaphore(10)`. Judgment-mode body steps thread `_session=_session` per task.
```

In the implementation-status table, add a row:

```markdown
| `FOR EACH ... PARALLEL AS <name>` | ✅ | ✅ | ❌ rejected | ✅ `ThreadPoolExecutor` | ✅ `asyncio.gather` |
```

(Match the table's exact column count and style.)

- [ ] **Step 2: Update `docs/COMPILATION_TARGETS.md`**

In the `python` target body section, add a bullet:

> - **FOR EACH PARALLEL:** supported via `concurrent.futures.ThreadPoolExecutor` (cap = 10).

In the `mcp-server` target body section:

> - **FOR EACH PARALLEL:** supported via `asyncio.gather` + `Semaphore(10)`. Judgment steps thread the MCP session per task.

- [ ] **Step 3: Update `CHANGELOG.md`**

In the Unreleased section, add a new "Language" entry (or append to it if one exists):

```markdown
- New `FOR EACH ... PARALLEL AS <name>:` syntax fans a single STEP across a collection in parallel and binds the typed result list to `state[<name>]`. Default concurrency cap = 10. Supported by the python target (`concurrent.futures.ThreadPoolExecutor`) and the mcp-server target (`asyncio.gather` + `Semaphore`); rejected at compile time by claude-cli. Body restricted to one step call in v1; nested PARALLEL rejected; failure mode = fail-fast (per-task ON_FAIL still applies).
```

Update the test count line if there is one.

- [ ] **Step 4: Update `README.md`**

In the language features list (or wherever the features are summarized), add a one-liner:

> - `FOR EACH ... PARALLEL AS <name>:` — fan a STEP over a collection in parallel, collect typed results.

- [ ] **Step 5: Verify the suite still passes**

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add docs/LANGUAGE_SPEC.md docs/COMPILATION_TARGETS.md CHANGELOG.md README.md
git commit -m "$(cat <<'EOF'
docs: FOR EACH PARALLEL — LANGUAGE_SPEC, COMPILATION_TARGETS, CHANGELOG, README

LANGUAGE_SPEC gains a #### PARALLEL subsection under FOR EACH and a
new row in the implementation-status table. COMPILATION_TARGETS calls
out PARALLEL support per target. CHANGELOG entry for Unreleased.
README one-liner in the language features list.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10 (optional, gated): E2E parallelism timing test

**Files:**
- Create: `tests/test_e2e_parallel.py`

A single end-to-end test that proves the python target's parallelism is real (not just emitted). Compiles a fixture where the body step sleeps 100 ms; runs it with N=5 items; asserts wall-clock is well under 5 × 100 ms.

This is gated behind `CLIO_E2E=1` (mirroring existing gated e2e tests).

- [ ] **Step 1: Write the gated test**

Create `tests/test_e2e_parallel.py`:

```python
"""E2E timing test for FOR EACH PARALLEL on the python target."""
import os
import subprocess
import sys
import time

import pytest

from clio.emitters.python import PythonEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse


@pytest.mark.skipif(
    os.environ.get("CLIO_E2E") != "1",
    reason="parallelism timing test gated; set CLIO_E2E=1 to run",
)
def test_python_parallel_runs_actually_in_parallel(tmp_path):
    """Compile a flow whose body step sleeps 100ms. With N=5 items and
    PARALLEL, wall-clock should be ~100ms, not ~500ms."""
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP slow\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL AS results:\n"
        "         slow(x=item)\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)

    # Provide implementations.
    (tmp_path / "pipe" / "steps" / "load.py").write_text(
        "def load() -> list:\n"
        "    return ['a', 'b', 'c', 'd', 'e']\n"
    )
    (tmp_path / "pipe" / "steps" / "slow.py").write_text(
        "import time\n"
        "def slow(*, x: str) -> str:\n"
        "    time.sleep(0.1)\n"
        "    return x.upper()\n"
    )

    # Install and run as a subprocess to isolate from our test env.
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", "-e", str(tmp_path)],
    )
    runner = (
        "import time\n"
        "from pipe.flow import run\n"
        "t0 = time.monotonic()\n"
        "result = run()\n"
        "elapsed = time.monotonic() - t0\n"
        "print(f'elapsed: {elapsed:.3f}')\n"
        "print(f'results: {sorted(result[\"results\"])}')\n"
        "assert elapsed < 0.4, f'expected <400ms wall-clock, got {elapsed:.3f}s'\n"
        "assert sorted(result['results']) == ['A', 'B', 'C', 'D', 'E']\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", runner],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"runner failed: {proc.stderr}\n{proc.stdout}"
    print(proc.stdout)
```

- [ ] **Step 2: Verify the test is correctly gated**

Run: `.venv/bin/python -m pytest tests/test_e2e_parallel.py -v`

Expected: 1 SKIPPED.

- [ ] **Step 3: Optional — actually run the gated test**

```bash
CLIO_E2E=1 .venv/bin/python -m pytest tests/test_e2e_parallel.py -v
```

Expected: PASS with elapsed << 500ms (likely 100-150ms).

If the test reveals a real problem (wall-clock too long), debug before committing.

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_parallel.py
git commit -m "$(cat <<'EOF'
test(parallel): gated E2E wall-clock check for python parallelism

Compiles a fixture whose body step sleeps 100ms, runs it with N=5
items via the emitted package in a fresh subprocess, asserts wall-
clock is well under 500ms (proving parallelism is real, not just
emitted code shape). Gated behind CLIO_E2E=1 so default test runs
don't pay the pip-install cost.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** all 6 spec sections map to tasks — syntax (Tasks 1, 2), AST/IR (Tasks 1, 3), validations (Task 4), emitters (Tasks 5, 6, 7), runtime semantics (Tasks 5, 6 emit them), tests (every task), docs (Task 9), graph annotation (Task 8). E2E timing test in Task 10 covers the "is it actually parallel?" question.
- **Type consistency:** `ForEachBlock.parallel/collector` (parser AST) → `ForEachIR.parallel/collector` (IR) — same names, same types. `emit_parallel_for_each_python` and `emit_parallel_for_each_mcp` have the same signature `(elem, steps_by_name, indent) -> str`. The emitted `state[<collector>]` field name is consistent across both targets.
- **Placeholders:** none. Every step has actual code or actual commands; cross-references to existing helpers (`@`-prefix disambiguation, `_emit_flow_module_async`) point at concrete locations in the codebase that the implementer can grep for.
- **Byte-identical guarantee for sequential FOR EACH:** preserved via default-False `parallel` and conditional `import concurrent.futures` / `import asyncio` insertion. Tests in Tasks 5 and 6 explicitly assert the import isn't added when no parallel block is present.
- **Safe parsing in tests:** the parser/IR tests use `pytest.raises` with `match`; the emitter tests grep the emitted source for keywords (`ThreadPoolExecutor`, `asyncio.gather`). Where dict literals need to come out of emitted source, `ast.literal_eval` (the same pattern the mcp-server tests use) is safe and sufficient.
- **Per-target consistency:** the python and mcp-server emitters both apply the same `@`-prefix kwarg disambiguation (the canonical rule from `python.py:_emit_flow`). The two helper functions duplicate this small block — acceptable for v1; if a third target ever supports PARALLEL, extract the kwarg-rendering logic into `_python_helpers.py` then.
- **claude-cli refusal:** placed in `_reject_parallel` before `output_dir.mkdir(...)` so failures don't leave a half-made dir.
