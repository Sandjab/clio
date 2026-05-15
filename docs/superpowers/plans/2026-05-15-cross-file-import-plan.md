# CLIO v0.18 — Cross-file IMPORT + EXPOSE/INTERNAL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `FROM "<path>" IMPORT ...` cross-file symbol resolution and explicit `EXPOSE` / `INTERNAL` visibility markers on `FLOW` and `CONTRACT` declarations, with mechanical migration tooling for v0.17 sources.

**Architecture:** A new `resolver` module discovers and parses imported files recursively (with cycle detection), validates per-file exposure rules, computes transitively-exposed symbol sets (re-exports), and validates each import. The existing `build_ir` is extended to accept `dict[Path, Program]`, flatten the multi-file program into a single `FlowGraph` (alpha-renaming internals to avoid global name collisions), and pass it to emitters unchanged.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest, ruff, mypy strict, uv.

**Reference spec:** `docs/superpowers/specs/2026-05-15-cross-file-import-design.md`

---

## File structure overview

### Files to create

| Path | Purpose | Approx LOC |
|---|---|---|
| `clio/ir/resolver.py` | Multi-file resolution (discovery, validation, exposed sets, import validation) | ~250 |
| `tests/test_parser_imports.py` | Parser tests for IMPORT grammar | ~150 |
| `tests/test_parser_visibility.py` | Parser tests for EXPOSE/INTERNAL prefix | ~80 |
| `tests/test_resolver.py` | Resolver phases 1-4 tests | ~300 |
| `tests/test_ir_multifile.py` | Multi-file IR build + alpha-rename | ~200 |
| `tests/test_doctor_migrate.py` | Migration tool tests | ~150 |
| `tests/test_emitters/test_<emitter>_multifile.py` | One per emitter (5 files) | ~120 each |
| `tests/fixtures/imports/simple/{main,lib}.clio` | Simple 2-file fixture | small |
| `tests/fixtures/imports/diamond/{main,left,right,shared}.clio` | Diamond import fixture | small |
| `tests/fixtures/imports/reexport/{main,lib,facade}.clio` | Re-export fixture | small |
| `tests/fixtures/imports/cycles/{a,b}.clio` | Cycle-detection fixture | small |
| `tests/fixtures/imports/migration_v017_to_v018/{before,expected_after}.clio` | Migration tool fixture | small |
| `docs/manual/06-migration-v018.md` | Migration guide | ~120 |
| `examples/multi_file/{main,lib_nlp,schemas}.clio` | Multi-file example project | small |

### Files to modify

| Path | Change | Lines |
|---|---|---|
| `clio/keywords.py` | +4 keywords (`FROM`, `IMPORT`, `EXPOSE`, `INTERNAL`) | +4 |
| `clio/parser/ast_nodes.py` | +`ImportItem`, +`ImportDecl`; +`exposed: bool` on `FlowDecl`/`ContractDecl`; +`imports: tuple[...]`, +`source_path` on `Program` | +30 |
| `clio/parser/parser.py` | +`parse_import_decl()`, dispatcher recognises `FROM`; visibility prefix before `FLOW`/`CONTRACT` | +80 |
| `clio/ir/builder.py` | `build_ir` accepts `dict[Path, Program]`; delete `_compute_exposed_flows`; alpha-renaming | +120 / -15 |
| `clio/ir/graph.py` | No schema change (just doc comment update on `exposed_flow_names`) | +3 |
| `clio/cli.py` | `_cmd_compile`, `_cmd_check`, `_cmd_graph` call `resolve_imports` before `build_ir`; `_cmd_doctor` gains `--migrate-v018` flag | +30 |
| `clio/diagnostics.py` | `migrate_v018(...)` function: applies v0.17 sibling heuristic and emits a diff | +120 |
| `clio/emitters/mcp_server.py` | Drop heuristic dependency; reject empty `exposed_flow_names` | +15 |
| `clio/emitters/claude_cli.py` | Reject if any IMPORT was resolved (E_CLI_001) | +10 |
| Existing `tests/fixtures/*.clio` (mcp-server) | Add `EXPOSE` to public FLOWs/CONTRACTs | varies |
| `docs/LANGUAGE_SPEC.md` | +2 sections (IMPORT, EXPOSE/INTERNAL); target table update | +90 |
| `docs/ARCHITECTURE.md` | +"Multi-file resolution" section | +30 |
| `docs/COMPILATION_TARGETS.md` | Target table updated | +10 |
| `docs/manual/02-tutorial.md` | +chapter "Splitting code across files" | +60 |
| `docs/manual/03-cookbook.md` | +2 recipes | +80 |
| `docs/manual/06-troubleshooting.md` | +entries for E_IMP_*/E_RES_*/E_VIS_*/E_MCP_001 | +50 |
| `CHANGELOG.md` | `[Unreleased]` entry | +30 |

### Files NOT modified

- `clio/parser/lexer.py` — keyword detection is enum-driven, no lexer change needed.
- `clio/emitters/python.py`, `claude_skill.py`, `langgraph.py` — logic unchanged, only golden tests added.
- All other emitter `_*_helpers.py` modules.

---

## Phase A — Foundation (sequential)

Tasks 1–9 must be completed in order. Each builds on the previous.

### Task 1: Add new keywords + AST extensions

**Files:**
- Modify: `clio/keywords.py`
- Modify: `clio/parser/ast_nodes.py:228` (Program) and add ImportDecl/ImportItem after it
- Modify: `clio/parser/ast_nodes.py:179` (FlowDecl) — add `exposed` field
- Modify: `clio/parser/ast_nodes.py:62` (ContractDecl) — add `exposed` field
- Test: `tests/test_parser_imports.py` (create)
- Test: `tests/test_parser_visibility.py` (create)

- [ ] **Step 1: Add the 4 keywords to the enum**

In `clio/keywords.py`, after the last entry in the `Keyword` enum, add:

```python
FROM = "FROM"
IMPORT = "IMPORT"
EXPOSE = "EXPOSE"
INTERNAL = "INTERNAL"
```

- [ ] **Step 2: Add `ImportItem` and `ImportDecl` dataclasses**

In `clio/parser/ast_nodes.py`, after the `class Program:` block (line ~228), add:

```python
@dataclass(frozen=True)
class ImportItem:
    """One symbol in a FROM ... IMPORT ... list."""
    name: str
    alias: str | None    # None means: imported under its original name
    line: int
    col: int


@dataclass(frozen=True)
class ImportDecl:
    """FROM "<path>" IMPORT <item>, <item>, ..."""
    path: str            # raw path as in source ("./lib/nlp.clio")
    items: tuple[ImportItem, ...]
    line: int
    col: int
```

- [ ] **Step 3: Add `exposed` field to `FlowDecl` and `ContractDecl`**

In `clio/parser/ast_nodes.py:179`, modify `FlowDecl` to add `exposed: bool = False` as the LAST field (preserves dataclass default-order rule):

```python
@dataclass(frozen=True)
class FlowDecl:
    name: str
    chain: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock | ResumeAst, ...]"
    rescues: "tuple[RescueBlock, ...]"
    line: int
    col: int
    takes: tuple[Field, ...] = ()
    gives: tuple[Field, ...] = ()
    description: str | None = None
    exposed: bool = False         # v0.18 — set True by EXPOSE prefix
```

In `clio/parser/ast_nodes.py:62`, modify `ContractDecl`:

```python
@dataclass(frozen=True)
class ContractDecl:
    name: str
    shape: TypeExpr
    assert_expr: "ExprNode | None"
    line: int
    col: int
    exposed: bool = False         # v0.18 — set True by EXPOSE prefix
```

- [ ] **Step 4: Extend `Program` with `imports` and `source_path`**

In `clio/parser/ast_nodes.py:228`, modify `Program`:

```python
@dataclass(frozen=True)
class Program:
    decls: tuple[object, ...]
    imports: tuple[ImportDecl, ...] = ()       # v0.18
    source_path: Path | None = None            # v0.18 — for error messages
```

Add `from pathlib import Path` at the top of the file if not already present.

- [ ] **Step 5: Run existing tests to verify no regression from field additions**

Run: `uv run pytest tests/test_parser.py tests/test_ir.py -v`
Expected: all 200+ existing tests still PASS (default-valued new fields are backward-compatible).

- [ ] **Step 6: Commit**

```bash
git add clio/keywords.py clio/parser/ast_nodes.py
git commit -m "$(cat <<'EOF'
feat(v0.18): add FROM/IMPORT/EXPOSE/INTERNAL keywords and ImportDecl AST nodes

Extends AST with ImportItem, ImportDecl, exposed field on FlowDecl
and ContractDecl, and imports/source_path on Program. Default-valued
new fields preserve backward compatibility with existing tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Parse `FROM "..." IMPORT ...` statements

**Files:**
- Modify: `clio/parser/parser.py:108` (`parse_program` dispatcher) and add `parse_import_decl()` method
- Test: `tests/test_parser_imports.py`

- [ ] **Step 1: Write failing tests for IMPORT parsing**

Create `tests/test_parser_imports.py`:

```python
import pytest
from clio.parser.ast_nodes import ImportDecl, ImportItem, Program
from clio.parser.parser import ParseError, parse


def test_single_import():
    src = 'FROM "./lib.clio" IMPORT classify\n'
    program = parse(src)
    assert len(program.imports) == 1
    imp = program.imports[0]
    assert imp.path == "./lib.clio"
    assert len(imp.items) == 1
    assert imp.items[0].name == "classify"
    assert imp.items[0].alias is None


def test_multi_import():
    src = 'FROM "./lib.clio" IMPORT classify, summarize, Article\n'
    program = parse(src)
    imp = program.imports[0]
    assert [i.name for i in imp.items] == ["classify", "summarize", "Article"]


def test_import_with_alias():
    src = 'FROM "./lib.clio" IMPORT classify AS clf, summarize\n'
    program = parse(src)
    items = program.imports[0].items
    assert items[0].name == "classify" and items[0].alias == "clf"
    assert items[1].name == "summarize" and items[1].alias is None


def test_parent_dir_path():
    src = 'FROM "../shared/util.clio" IMPORT enrich\n'
    program = parse(src)
    assert program.imports[0].path == "../shared/util.clio"


def test_multiple_imports():
    src = (
        'FROM "./a.clio" IMPORT X\n'
        'FROM "./b.clio" IMPORT Y, Z\n'
    )
    program = parse(src)
    assert len(program.imports) == 2
    assert program.imports[0].path == "./a.clio"
    assert program.imports[1].path == "./b.clio"


def test_import_with_subsequent_flow():
    src = (
        'FROM "./lib.clio" IMPORT classify\n'
        '\n'
        'FLOW pipeline\n'
        '  - classify(text: input)\n'
    )
    program = parse(src)
    assert len(program.imports) == 1
    assert len(program.decls) == 1  # the FLOW


def test_e_imp_001_no_prefix():
    src = 'FROM "lib.clio" IMPORT X\n'
    with pytest.raises(ParseError, match=r"path must start with './' or '../'"):
        parse(src)


def test_e_imp_001_absolute_path():
    src = 'FROM "/abs/lib.clio" IMPORT X\n'
    with pytest.raises(ParseError, match=r"path must start with './' or '../'"):
        parse(src)


def test_e_imp_002_no_extension():
    src = 'FROM "./lib" IMPORT X\n'
    with pytest.raises(ParseError, match=r"path must end with '.clio'"):
        parse(src)


def test_e_imp_003_empty_list():
    src = 'FROM "./lib.clio" IMPORT\n'
    with pytest.raises(ParseError, match=r"expected at least one symbol after IMPORT"):
        parse(src)


def test_e_imp_004_missing_alias_identifier():
    src = 'FROM "./lib.clio" IMPORT X AS\n'
    with pytest.raises(ParseError, match=r"expected identifier after AS"):
        parse(src)


def test_e_imp_005_duplicate_in_same_statement():
    src = 'FROM "./lib.clio" IMPORT X, X\n'
    with pytest.raises(ParseError, match=r"duplicate symbol 'X'"):
        parse(src)


def test_alias_same_as_name_allowed():
    """X AS X is a no-op but explicit; allow silently."""
    src = 'FROM "./lib.clio" IMPORT X AS X\n'
    program = parse(src)
    assert program.imports[0].items[0].alias == "X"
```

- [ ] **Step 2: Run tests to confirm they all fail**

Run: `uv run pytest tests/test_parser_imports.py -v`
Expected: all 12 tests FAIL (no IMPORT parsing yet).

- [ ] **Step 3: Implement `parse_import_decl` method**

In `clio/parser/parser.py`, after `parse_resources` (line ~304), add:

```python
def parse_import_decl(self) -> "ImportDecl":
    """Parse a top-level FROM "<path>" IMPORT <item>, <item>, ... line.

    Grammar:
      FROM STRING_LIT IMPORT IDENT [AS IDENT] ("," IDENT [AS IDENT])* NEWLINE
    """
    from clio.parser.ast_nodes import ImportDecl, ImportItem

    tok_from = self.expect_keyword("FROM")
    path_tok = self.expect(TokenType.STRING)
    path = path_tok.value
    if not (path.startswith("./") or path.startswith("../")):
        raise ParseError(
            f"path must start with './' or '../', got {path!r}",
            path_tok.line, path_tok.col,
        )
    if not path.endswith(".clio"):
        raise ParseError(
            f"path must end with '.clio', got {path!r}",
            path_tok.line, path_tok.col,
        )
    self.expect_keyword("IMPORT")
    items: list[ImportItem] = []
    seen_names: set[str] = set()
    while True:
        if self.peek().type == TokenType.NEWLINE or self.peek().type == TokenType.EOF:
            if not items:
                raise ParseError(
                    "expected at least one symbol after IMPORT",
                    tok_from.line, tok_from.col,
                )
            break
        name_tok = self.expect(TokenType.IDENT)
        alias: str | None = None
        if self.peek().type == TokenType.KEYWORD and self.peek().value == "AS":
            self.advance()
            if self.peek().type != TokenType.IDENT:
                raise ParseError(
                    "expected identifier after AS",
                    self.peek().line, self.peek().col,
                )
            alias_tok = self.expect(TokenType.IDENT)
            alias = alias_tok.value
        if name_tok.value in seen_names:
            raise ParseError(
                f"duplicate symbol {name_tok.value!r} in same IMPORT statement",
                name_tok.line, name_tok.col,
            )
        seen_names.add(name_tok.value)
        items.append(ImportItem(
            name=name_tok.value, alias=alias,
            line=name_tok.line, col=name_tok.col,
        ))
        if self.peek().type == TokenType.COMMA:
            self.advance()
            continue
        break
    self.skip_newlines()
    return ImportDecl(
        path=path, items=tuple(items),
        line=tok_from.line, col=tok_from.col,
    )
```

- [ ] **Step 4: Modify `parse_program` to collect imports and route on `FROM`**

In `clio/parser/parser.py:108`, replace `parse_program` body:

```python
def parse_program(self) -> Program:
    decls: list[object] = []
    imports: list[ImportDecl] = []
    self.skip_newlines()
    while self.peek().type != TokenType.EOF:
        t = self.peek()
        if t.type == TokenType.KEYWORD and t.value == "FROM":
            imports.append(self.parse_import_decl())
        elif t.type == TokenType.KEYWORD and t.value == "STEP":
            decls.append(self.parse_step())
        elif t.type == TokenType.KEYWORD and t.value == "CONTRACT":
            decls.append(self.parse_contract())
        elif t.type == TokenType.KEYWORD and t.value == "FLOW":
            decls.append(self.parse_flow())
        elif t.type == TokenType.KEYWORD and t.value == "RESOURCES":
            decls.append(self.parse_resources())
        elif t.type == TokenType.KEYWORD and t.value == "TEST":
            decls.append(self.parse_test())
        else:
            raise ParseError(
                f"expected FROM / STEP / CONTRACT / FLOW / RESOURCES / TEST, "
                f"got {t.type.value} {t.value!r}",
                t.line, t.col,
            )
        self.skip_newlines()
    return Program(tuple(decls), imports=tuple(imports))
```

Make sure `ImportDecl` is imported at the top of the file.

- [ ] **Step 5: Run the parser tests until all pass**

Run: `uv run pytest tests/test_parser_imports.py -v`
Expected: all 12 tests PASS.

- [ ] **Step 6: Run full parser test suite to verify no regression**

Run: `uv run pytest tests/test_parser.py tests/test_ir.py -v --tb=no`
Expected: no new failures (existing tests still PASS).

- [ ] **Step 7: Commit**

```bash
git add clio/parser/parser.py tests/test_parser_imports.py
git commit -m "$(cat <<'EOF'
feat(v0.18): parse FROM "<path>" IMPORT statements

Adds parse_import_decl() and extends parse_program() to route FROM
keyword to it. Validates path starts with ./ or ../ and ends with
.clio (E_IMP_001/002), at least one symbol after IMPORT (E_IMP_003),
identifier after AS (E_IMP_004), no duplicates in same statement
(E_IMP_005).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Parse `EXPOSE` / `INTERNAL` prefixes on FLOW / CONTRACT

**Files:**
- Modify: `clio/parser/parser.py:108` (parse_program dispatcher); also `parse_flow` and `parse_contract`
- Test: `tests/test_parser_visibility.py`

- [ ] **Step 1: Write failing tests for visibility prefix**

Create `tests/test_parser_visibility.py`:

```python
import pytest
from clio.parser.parser import ParseError, parse


def _flow_named(program, name):
    for d in program.decls:
        if getattr(d, "name", None) == name and d.__class__.__name__ == "FlowDecl":
            return d
    raise KeyError(name)


def _contract_named(program, name):
    for d in program.decls:
        if getattr(d, "name", None) == name and d.__class__.__name__ == "ContractDecl":
            return d
    raise KeyError(name)


def test_expose_flow():
    src = (
        'EXPOSE FLOW classify\n'
        '  TAKES:\n'
        '    x: int\n'
        '  GIVES:\n'
        '    y: int\n'
        '  - step1(x: x)\n'
    )
    program = parse(src)
    assert _flow_named(program, "classify").exposed is True


def test_internal_flow_explicit():
    src = (
        'INTERNAL FLOW helper\n'
        '  TAKES:\n'
        '    x: int\n'
        '  GIVES:\n'
        '    y: int\n'
        '  - step1(x: x)\n'
    )
    program = parse(src)
    assert _flow_named(program, "helper").exposed is False


def test_flow_no_prefix_is_internal():
    src = (
        'FLOW helper\n'
        '  TAKES:\n'
        '    x: int\n'
        '  GIVES:\n'
        '    y: int\n'
        '  - step1(x: x)\n'
    )
    program = parse(src)
    assert _flow_named(program, "helper").exposed is False


def test_expose_contract():
    src = (
        'EXPOSE CONTRACT Article\n'
        '  SHAPE:\n'
        '    title: str\n'
    )
    program = parse(src)
    assert _contract_named(program, "Article").exposed is True


def test_internal_contract_explicit():
    src = (
        'INTERNAL CONTRACT Article\n'
        '  SHAPE:\n'
        '    title: str\n'
    )
    program = parse(src)
    assert _contract_named(program, "Article").exposed is False


def test_e_vis_001_both_markers():
    src = 'EXPOSE INTERNAL FLOW X\n'
    with pytest.raises(ParseError, match=r"only one visibility marker"):
        parse(src)


def test_e_vis_002_expose_on_step():
    src = (
        'EXPOSE STEP foo\n'
        '  MODE: exact\n'
    )
    with pytest.raises(ParseError, match=r"EXPOSE applies only to FLOW and CONTRACT"):
        parse(src)


def test_e_vis_002_expose_on_resources():
    src = (
        'EXPOSE RESOURCES\n'
        '  target: python\n'
    )
    with pytest.raises(ParseError, match=r"EXPOSE applies only to FLOW and CONTRACT"):
        parse(src)
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest tests/test_parser_visibility.py -v`
Expected: all 8 tests FAIL.

- [ ] **Step 3: Modify `parse_program` to handle EXPOSE/INTERNAL prefix**

Replace `parse_program` in `clio/parser/parser.py:108` with this version that detects the visibility prefix and forwards to `parse_flow`/`parse_contract` with an `exposed` flag:

```python
def parse_program(self) -> Program:
    decls: list[object] = []
    imports: list[ImportDecl] = []
    self.skip_newlines()
    while self.peek().type != TokenType.EOF:
        t = self.peek()
        if t.type == TokenType.KEYWORD and t.value == "FROM":
            imports.append(self.parse_import_decl())
            self.skip_newlines()
            continue

        # Visibility prefix detection (EXPOSE / INTERNAL)
        exposed: bool | None = None  # None = no prefix, True = EXPOSE, False = INTERNAL
        vis_tok = None
        if t.type == TokenType.KEYWORD and t.value in ("EXPOSE", "INTERNAL"):
            vis_tok = t
            exposed = (t.value == "EXPOSE")
            self.advance()
            nxt = self.peek()
            if nxt.type == TokenType.KEYWORD and nxt.value in ("EXPOSE", "INTERNAL"):
                raise ParseError(
                    "only one visibility marker allowed before FLOW/CONTRACT",
                    nxt.line, nxt.col,
                )
            if not (nxt.type == TokenType.KEYWORD and nxt.value in ("FLOW", "CONTRACT")):
                raise ParseError(
                    f"EXPOSE applies only to FLOW and CONTRACT (got {nxt.value!r})",
                    vis_tok.line, vis_tok.col,
                )
            t = nxt

        if t.type == TokenType.KEYWORD and t.value == "STEP":
            decls.append(self.parse_step())
        elif t.type == TokenType.KEYWORD and t.value == "CONTRACT":
            decls.append(self.parse_contract(exposed=exposed or False))
        elif t.type == TokenType.KEYWORD and t.value == "FLOW":
            decls.append(self.parse_flow(exposed=exposed or False))
        elif t.type == TokenType.KEYWORD and t.value == "RESOURCES":
            decls.append(self.parse_resources())
        elif t.type == TokenType.KEYWORD and t.value == "TEST":
            decls.append(self.parse_test())
        else:
            raise ParseError(
                f"expected FROM / STEP / CONTRACT / FLOW / RESOURCES / TEST, "
                f"got {t.type.value} {t.value!r}",
                t.line, t.col,
            )
        self.skip_newlines()
    return Program(tuple(decls), imports=tuple(imports))
```

- [ ] **Step 4: Modify `parse_flow` to accept `exposed` keyword argument**

Find `def parse_flow(self)` in `parser.py`. Change its signature and the return construction to thread `exposed`:

```python
def parse_flow(self, exposed: bool = False) -> FlowDecl:
    # ... existing body ...
    # At the FlowDecl(...) construction at the end, add:
    return FlowDecl(
        name=name,
        chain=tuple(chain),
        rescues=tuple(rescues),
        line=line,
        col=col,
        takes=takes,
        gives=gives,
        description=description,
        exposed=exposed,        # NEW
    )
```

(Find the exact `return FlowDecl(...)` site — there is exactly one in `parse_flow`.)

- [ ] **Step 5: Modify `parse_contract` similarly**

Find `def parse_contract(self)` at `parser.py:1872`:

```python
def parse_contract(self, exposed: bool = False) -> ContractDecl:
    # ... existing body ...
    return ContractDecl(
        name=name,
        shape=shape,
        assert_expr=assert_expr,
        line=line,
        col=col,
        exposed=exposed,        # NEW
    )
```

- [ ] **Step 6: Run the visibility tests until all pass**

Run: `uv run pytest tests/test_parser_visibility.py -v`
Expected: all 8 tests PASS.

- [ ] **Step 7: Full parser regression**

Run: `uv run pytest tests/test_parser.py tests/test_parser_imports.py tests/test_parser_visibility.py tests/test_ir.py -v --tb=no`
Expected: no new failures.

- [ ] **Step 8: Commit**

```bash
git add clio/parser/parser.py tests/test_parser_visibility.py
git commit -m "$(cat <<'EOF'
feat(v0.18): parse EXPOSE/INTERNAL prefix on FLOW and CONTRACT

Adds visibility prefix detection in parse_program(). Threads
exposed: bool through parse_flow() and parse_contract(). Rejects
double marker (E_VIS_001) and EXPOSE on STEP/RESOURCES (E_VIS_002).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Resolver — discovery phase (recursive parse + cycle detection)

**Files:**
- Create: `clio/ir/resolver.py`
- Create: `tests/test_resolver.py`
- Create: `tests/fixtures/imports/simple/{main.clio,lib.clio}`
- Create: `tests/fixtures/imports/cycles/{a.clio,b.clio}`

- [ ] **Step 1: Create fixture files**

Create `tests/fixtures/imports/simple/lib.clio`:

```
EXPOSE CONTRACT Article
  SHAPE:
    title: str
    body:  str

EXPOSE FLOW classify
  TAKES:
    article: Article
  GIVES:
    label: str
  - score(text: article.body)

STEP score
  MODE: exact
  IMPL: code
  LANG: python
  TAKES:
    text: str
  GIVES:
    label: str
  CODE: |
    return {"label": "ok"}
```

Create `tests/fixtures/imports/simple/main.clio`:

```
target: python
models:
  prefer: sonnet

FROM "./lib.clio" IMPORT Article, classify

EXPOSE FLOW pipeline
  TAKES:
    article: Article
  GIVES:
    label: str
  - classify(article: article)
```

Create `tests/fixtures/imports/cycles/a.clio`:

```
FROM "./b.clio" IMPORT X

FLOW main
  TAKES:
    x: int
  GIVES:
    y: int
  - X(input: x)
```

Create `tests/fixtures/imports/cycles/b.clio`:

```
FROM "./a.clio" IMPORT main

EXPOSE FLOW X
  TAKES:
    input: int
  GIVES:
    out: int
  - main(x: input)
```

- [ ] **Step 2: Write failing tests for discovery + cycles**

Create `tests/test_resolver.py`:

```python
from pathlib import Path
import pytest
from clio.ir.resolver import (
    CompileError,
    resolve_imports,
)

FIXTURES = Path(__file__).parent / "fixtures" / "imports"


def test_discovery_simple():
    """main.clio + lib.clio both parsed and indexed by resolved Path."""
    parsed = resolve_imports(FIXTURES / "simple" / "main.clio")
    assert len(parsed) == 2
    keys = {p.name for p in parsed.keys()}
    assert keys == {"main.clio", "lib.clio"}


def test_discovery_entry_alone():
    """A file with no imports yields a 1-entry dict."""
    entry = FIXTURES / "simple" / "lib.clio"
    parsed = resolve_imports(entry)
    assert len(parsed) == 1


def test_e_res_002_file_not_found(tmp_path):
    entry = tmp_path / "main.clio"
    entry.write_text('FROM "./missing.clio" IMPORT X\n')
    with pytest.raises(CompileError, match=r"imported file not found"):
        resolve_imports(entry)


def test_e_res_001_cycle_two_files():
    entry = FIXTURES / "cycles" / "a.clio"
    with pytest.raises(CompileError, match=r"cyclic import"):
        resolve_imports(entry)


def test_e_res_001_cycle_self_import(tmp_path):
    entry = tmp_path / "self.clio"
    entry.write_text('FROM "./self.clio" IMPORT X\n')
    with pytest.raises(CompileError, match=r"cyclic import"):
        resolve_imports(entry)


def test_discovery_idempotent_caching(tmp_path):
    """If file b is imported from both a and entry, it's only parsed once."""
    (tmp_path / "shared.clio").write_text(
        'EXPOSE CONTRACT Doc\n  SHAPE:\n    text: str\n'
    )
    (tmp_path / "left.clio").write_text(
        'FROM "./shared.clio" IMPORT Doc\n'
        'EXPOSE FLOW left_flow\n'
        '  TAKES:\n    doc: Doc\n'
        '  GIVES:\n    out: str\n'
        '  - noop(doc: doc)\n'
        'STEP noop\n'
        '  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    doc: Doc\n'
        '  GIVES:\n    out: str\n'
        '  CODE: |\n    return {"out": "x"}\n'
    )
    (tmp_path / "right.clio").write_text(
        'FROM "./shared.clio" IMPORT Doc\n'
        'EXPOSE FLOW right_flow\n'
        '  TAKES:\n    doc: Doc\n'
        '  GIVES:\n    out: str\n'
        '  - noop(doc: doc)\n'
        'STEP noop\n'
        '  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    doc: Doc\n'
        '  GIVES:\n    out: str\n'
        '  CODE: |\n    return {"out": "x"}\n'
    )
    (tmp_path / "main.clio").write_text(
        'target: python\n'
        'models: { prefer: sonnet }\n'
        'FROM "./left.clio" IMPORT left_flow\n'
        'FROM "./right.clio" IMPORT right_flow\n'
        'FROM "./shared.clio" IMPORT Doc\n'
        'EXPOSE FLOW main_flow\n'
        '  TAKES:\n    doc: Doc\n'
        '  GIVES:\n    out: str\n'
        '  - left_flow(doc: doc)\n'
    )
    parsed = resolve_imports(tmp_path / "main.clio")
    assert len(parsed) == 4
```

- [ ] **Step 3: Run tests to confirm they fail (module doesn't exist)**

Run: `uv run pytest tests/test_resolver.py -v`
Expected: all tests FAIL with `ModuleNotFoundError: clio.ir.resolver`.

- [ ] **Step 4: Implement `resolver.py` discovery phase**

Create `clio/ir/resolver.py`:

```python
"""Multi-file resolver for CLIO v0.18.

Phase 1 (discovery): recursive parse of all .clio files reachable from
the entry, with cycle detection. Returns dict[Path, Program] keyed by
the resolved absolute path of each file.

Subsequent phases (validation, exposed sets, import validation) are
added in later tasks.
"""
from __future__ import annotations

from pathlib import Path

from clio.parser.ast_nodes import Program
from clio.parser.parser import parse


class CompileError(Exception):
    """Raised by the resolver for build-time errors (cycles, missing
    files, validation failures). Distinct from parser-level ParseError."""


def resolve_imports(entry: Path) -> dict[Path, Program]:
    """Recursively parse all files reachable from `entry`.

    Returns a dict keyed by the resolved absolute path of each file.
    Raises CompileError on cyclic imports or missing files.
    """
    parsed: dict[Path, Program] = {}
    stack: list[Path] = []
    _visit(entry.resolve(), parsed, stack)
    return parsed


def _visit(path: Path, parsed: dict[Path, Program], stack: list[Path]) -> None:
    if path in stack:
        chain = " → ".join(str(p) for p in stack[stack.index(path):]) + f" → {path}"
        raise CompileError(f"cyclic import: {chain}")
    if path in parsed:
        return
    if not path.exists():
        # path may be the entry; the entry caller is responsible for the message
        raise CompileError(f"imported file not found: {path}")
    text = path.read_text()
    program = parse(text)
    # Thread the source_path onto the Program for downstream error messages
    program = Program(
        decls=program.decls,
        imports=program.imports,
        source_path=path,
    )
    stack.append(path)
    try:
        for imp in program.imports:
            child = (path.parent / imp.path).resolve()
            _visit(child, parsed, stack)
    finally:
        stack.pop()
    parsed[path] = program
```

- [ ] **Step 5: Run the resolver tests until all pass**

Run: `uv run pytest tests/test_resolver.py -v`
Expected: 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add clio/ir/resolver.py tests/test_resolver.py tests/fixtures/imports/
git commit -m "$(cat <<'EOF'
feat(v0.18): resolver discovery phase with cycle detection

Adds clio/ir/resolver.py with resolve_imports(entry) function that
recursively parses all .clio files reachable from an entry point.
Detects cycles (E_RES_001) with full import chain in the error.
Rejects missing files (E_RES_002). Idempotent: shared dependencies
parsed only once.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Resolver — per-file validation

**Files:**
- Modify: `clio/ir/resolver.py` (+`validate_per_file`)
- Modify: `tests/test_resolver.py` (+tests)

- [ ] **Step 1: Write failing tests for per-file validation**

Append to `tests/test_resolver.py`:

```python
from clio.ir.resolver import validate_per_file


def test_e_vis_003_exposed_flow_without_signature(tmp_path):
    """EXPOSE FLOW must declare TAKES and GIVES."""
    entry = tmp_path / "main.clio"
    entry.write_text(
        'EXPOSE FLOW broken\n'
        '  - step1(x: 1)\n'
        'STEP step1\n'
        '  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    x: int\n'
        '  GIVES:\n    y: int\n'
        '  CODE: |\n    return {"y": x}\n'
    )
    parsed = resolve_imports(entry)
    with pytest.raises(CompileError, match=r"exposed FLOW 'broken' must declare explicit TAKES and GIVES"):
        validate_per_file(parsed)


def test_e_vis_004_same_name_flow_and_contract(tmp_path):
    """A name cannot be EXPOSE FLOW and EXPOSE CONTRACT in the same file."""
    entry = tmp_path / "main.clio"
    entry.write_text(
        'EXPOSE CONTRACT X\n'
        '  SHAPE:\n    a: str\n'
        'EXPOSE FLOW X\n'
        '  TAKES:\n    x: int\n'
        '  GIVES:\n    y: int\n'
        '  - step1(x: x)\n'
        'STEP step1\n'
        '  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    x: int\n'
        '  GIVES:\n    y: int\n'
        '  CODE: |\n    return {"y": x}\n'
    )
    parsed = resolve_imports(entry)
    with pytest.raises(CompileError, match=r"'X' is exposed as both FLOW and CONTRACT"):
        validate_per_file(parsed)


def test_e_mod_001_resources_in_imported_file(tmp_path):
    """Only the entry file may declare RESOURCES."""
    (tmp_path / "lib.clio").write_text(
        'target: python\n'
        'models: { prefer: sonnet }\n'
        'EXPOSE CONTRACT X\n  SHAPE:\n    a: str\n'
    )
    (tmp_path / "main.clio").write_text(
        'target: python\n'
        'models: { prefer: sonnet }\n'
        'FROM "./lib.clio" IMPORT X\n'
        'EXPOSE FLOW main\n'
        '  TAKES:\n    x: X\n'
        '  GIVES:\n    y: str\n'
        '  - noop(x: x)\n'
        'STEP noop\n'
        '  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    x: X\n'
        '  GIVES:\n    y: str\n'
        '  CODE: |\n    return {"y": "ok"}\n'
    )
    parsed = resolve_imports(tmp_path / "main.clio")
    with pytest.raises(CompileError, match=r"only the entry file may declare"):
        validate_per_file(parsed, entry=(tmp_path / "main.clio").resolve())


def test_valid_file_passes(tmp_path):
    """A well-formed file is silently accepted."""
    entry = tmp_path / "main.clio"
    entry.write_text(
        'EXPOSE FLOW ok\n'
        '  TAKES:\n    x: int\n'
        '  GIVES:\n    y: int\n'
        '  - step1(x: x)\n'
        'STEP step1\n'
        '  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    x: int\n'
        '  GIVES:\n    y: int\n'
        '  CODE: |\n    return {"y": x}\n'
    )
    parsed = resolve_imports(entry)
    validate_per_file(parsed)  # no exception
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run pytest tests/test_resolver.py -v -k "validate"`
Expected: validation tests FAIL.

- [ ] **Step 3: Implement `validate_per_file` in resolver.py**

Append to `clio/ir/resolver.py`:

```python
from clio.parser.ast_nodes import (
    ContractDecl,
    FlowDecl,
    ResourcesDecl,
    TestDecl,
)


def validate_per_file(
    parsed: dict[Path, Program],
    entry: Path | None = None,
) -> None:
    """Phase 2: per-file integrity checks.

    - EXPOSE FLOW must declare TAKES and GIVES (E_VIS_003).
    - The same name cannot be both EXPOSE FLOW and EXPOSE CONTRACT
      in the same file (E_VIS_004).
    - RESOURCES / TEST blocks only allowed in the entry file
      (E_MOD_001, E_MOD_002).

    If `entry` is None, RESOURCES/TEST restrictions are not enforced
    (allows the function to be called outside the full compile flow).
    """
    for path, program in parsed.items():
        exposed_flow_names: set[str] = set()
        exposed_contract_names: set[str] = set()
        for decl in program.decls:
            if isinstance(decl, FlowDecl):
                if decl.exposed:
                    if not decl.takes or not decl.gives:
                        raise CompileError(
                            f"{path}:{decl.line}:{decl.col}: "
                            f"exposed FLOW {decl.name!r} must declare explicit "
                            f"TAKES and GIVES"
                        )
                    exposed_flow_names.add(decl.name)
            elif isinstance(decl, ContractDecl):
                if decl.exposed:
                    exposed_contract_names.add(decl.name)
            elif isinstance(decl, ResourcesDecl):
                if entry is not None and path != entry:
                    raise CompileError(
                        f"{path}:{decl.line}:{decl.col}: "
                        f"only the entry file may declare RESOURCES "
                        f"(found in {path.name})"
                    )
            elif isinstance(decl, TestDecl):
                if entry is not None and path != entry:
                    raise CompileError(
                        f"{path}:{decl.line}: "
                        f"only the entry file may declare TEST blocks "
                        f"(found in {path.name})"
                    )
        overlap = exposed_flow_names & exposed_contract_names
        if overlap:
            name = next(iter(overlap))
            raise CompileError(
                f"{path}: {name!r} is exposed as both FLOW and CONTRACT"
            )
```

- [ ] **Step 4: Run validation tests until they pass**

Run: `uv run pytest tests/test_resolver.py -v`
Expected: all resolver tests PASS.

- [ ] **Step 5: Commit**

```bash
git add clio/ir/resolver.py tests/test_resolver.py
git commit -m "$(cat <<'EOF'
feat(v0.18): resolver per-file validation phase

Adds validate_per_file() with checks for E_VIS_003 (exposed FLOW
without signature), E_VIS_004 (same name FLOW+CONTRACT exposed),
E_MOD_001 (RESOURCES in imported file), E_MOD_002 (TEST in
imported file).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Resolver — exposed sets + re-export resolution

**Files:**
- Modify: `clio/ir/resolver.py` (+`compute_exposed_sets`)
- Modify: `tests/test_resolver.py`
- Create: `tests/fixtures/imports/reexport/{main,lib,facade}.clio`

- [ ] **Step 1: Create re-export fixture**

`tests/fixtures/imports/reexport/lib.clio`:

```
EXPOSE CONTRACT Article
  SHAPE:
    title: str

EXPOSE FLOW classify
  TAKES:
    article: Article
  GIVES:
    label: str
  - score(text: article.title)

STEP score
  MODE: exact
  IMPL: code
  LANG: python
  TAKES:
    text: str
  GIVES:
    label: str
  CODE: |
    return {"label": "ok"}
```

`tests/fixtures/imports/reexport/facade.clio`:

```
FROM "./lib.clio" IMPORT Article, classify

EXPOSE CONTRACT Article
EXPOSE FLOW classify
```

Wait — the parser requires the body for an exposed declaration to be present (CONTRACT needs SHAPE, FLOW needs TAKES/GIVES/body). Pure re-export needs a different shape: a stub. The cleanest approach is to allow `EXPOSE` to "alias" the imported decl without re-declaring its body. This will require parser support — but per the spec, re-export is via the regular `EXPOSE` mechanism.

We need a different syntax: a "shorthand" for re-export. The spec doesn't strictly require new syntax — re-export means: a file imports X, then declares an EXPOSE marker that points at X. The simplest path is to extend the import statement itself with a re-export keyword. But spec says co-localized.

Resolution: a file that has both `FROM "./lib.clio" IMPORT X` and a top-level statement `EXPOSE X` (no body — just the marker) re-exports it.

Add a new top-level statement form: `EXPOSE <ident>` (no FLOW/CONTRACT keyword, no body) that re-exports a previously imported name.

Update fixture:

```
FROM "./lib.clio" IMPORT Article, classify

EXPOSE Article
EXPOSE classify
```

This re-export form has its own parsing rule.

`tests/fixtures/imports/reexport/main.clio`:

```
target: python
models: { prefer: sonnet }

FROM "./facade.clio" IMPORT Article, classify

EXPOSE FLOW pipeline
  TAKES:
    article: Article
  GIVES:
    label: str
  - classify(article: article)
```

- [ ] **Step 2: Extend grammar to support `EXPOSE <ident>` re-export form**

In `clio/parser/ast_nodes.py`, after `ImportDecl`, add:

```python
@dataclass(frozen=True)
class ReexportDecl:
    """Top-level 'EXPOSE <name>' re-exports a previously imported symbol."""
    name: str
    line: int
    col: int
```

Then in `clio/parser/parser.py`, in `parse_program` where the visibility prefix is detected, before checking `FLOW`/`CONTRACT`:

```python
# After advancing past EXPOSE, check if next token is IDENT (re-export form).
if exposed is True and self.peek().type == TokenType.IDENT:
    # EXPOSE <name>  — re-export of an imported symbol
    name_tok = self.expect(TokenType.IDENT)
    from clio.parser.ast_nodes import ReexportDecl
    decls.append(ReexportDecl(
        name=name_tok.value, line=vis_tok.line, col=vis_tok.col,
    ))
    self.skip_newlines()
    continue
```

Place this branch BEFORE the "must be FLOW or CONTRACT" check.

- [ ] **Step 3: Write failing tests for exposed sets**

Append to `tests/test_resolver.py`:

```python
from clio.ir.resolver import compute_exposed_sets


def test_exposed_set_local_only(tmp_path):
    entry = tmp_path / "main.clio"
    entry.write_text(
        'EXPOSE CONTRACT X\n  SHAPE:\n    a: str\n'
        'EXPOSE FLOW Y\n'
        '  TAKES:\n    x: X\n  GIVES:\n    out: str\n'
        '  - step1(x: x)\n'
        'STEP step1\n  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    x: X\n  GIVES:\n    out: str\n'
        '  CODE: |\n    return {"out": "ok"}\n'
    )
    parsed = resolve_imports(entry)
    sets = compute_exposed_sets(parsed)
    entry_resolved = entry.resolve()
    assert set(sets[entry_resolved].keys()) == {"X", "Y"}


def test_exposed_set_reexport():
    entry = FIXTURES / "reexport" / "main.clio"
    parsed = resolve_imports(entry)
    sets = compute_exposed_sets(parsed)
    facade_path = (entry.parent / "facade.clio").resolve()
    # facade.clio re-exports Article and classify from lib.clio
    assert set(sets[facade_path].keys()) == {"Article", "classify"}


def test_reexport_of_nonimported_name(tmp_path):
    entry = tmp_path / "main.clio"
    entry.write_text('EXPOSE NotImported\n')
    parsed = resolve_imports(entry)
    with pytest.raises(CompileError, match=r"'NotImported' is not imported"):
        compute_exposed_sets(parsed)
```

- [ ] **Step 4: Implement `compute_exposed_sets` with topological order**

Append to `clio/ir/resolver.py`:

```python
def compute_exposed_sets(
    parsed: dict[Path, Program],
) -> dict[Path, dict[str, object]]:
    """Phase 3: per-file set of transitively exposed symbols.

    Returns a dict {file_path: {symbol_name: decl_or_reexport_target}}.
    Resolution is topological over imports so re-exports are
    resolved by the time their declaring file is visited.
    """
    # Build the import graph for topo sort
    in_degree: dict[Path, int] = {p: 0 for p in parsed}
    edges: dict[Path, list[Path]] = {p: [] for p in parsed}
    for path, program in parsed.items():
        for imp in program.imports:
            child = (path.parent / imp.path).resolve()
            if child in parsed:
                edges[child].append(path)  # child precedes path
                in_degree[path] += 1

    # Kahn's algorithm
    queue: list[Path] = [p for p, d in in_degree.items() if d == 0]
    topo: list[Path] = []
    while queue:
        current = queue.pop(0)
        topo.append(current)
        for nxt in edges[current]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)

    result: dict[Path, dict[str, object]] = {}
    from clio.parser.ast_nodes import ReexportDecl

    for path in topo:
        program = parsed[path]
        exposed: dict[str, object] = {}

        # Local exposed FLOWs and CONTRACTs
        for decl in program.decls:
            if isinstance(decl, FlowDecl) and decl.exposed:
                exposed[decl.name] = decl
            elif isinstance(decl, ContractDecl) and decl.exposed:
                exposed[decl.name] = decl

        # Re-exports: must reference an imported symbol
        # Build per-file import name → resolved source decl map
        import_local_names: dict[str, tuple[Path, str]] = {}
        for imp in program.imports:
            child = (path.parent / imp.path).resolve()
            for item in imp.items:
                local_name = item.alias or item.name
                import_local_names[local_name] = (child, item.name)

        for decl in program.decls:
            if isinstance(decl, ReexportDecl):
                if decl.name not in import_local_names:
                    raise CompileError(
                        f"{path}:{decl.line}:{decl.col}: "
                        f"{decl.name!r} is not imported (cannot EXPOSE without IMPORT)"
                    )
                source_path, source_name = import_local_names[decl.name]
                if source_name not in result.get(source_path, {}):
                    raise CompileError(
                        f"{path}:{decl.line}:{decl.col}: "
                        f"{source_name!r} is not exposed by {imp.path!r}"
                    )
                exposed[decl.name] = result[source_path][source_name]

        result[path] = exposed
    return result
```

- [ ] **Step 5: Run exposed-set tests**

Run: `uv run pytest tests/test_resolver.py -v`
Expected: all resolver tests PASS.

- [ ] **Step 6: Commit**

```bash
git add clio/ir/resolver.py clio/parser/parser.py clio/parser/ast_nodes.py tests/test_resolver.py tests/fixtures/imports/reexport/
git commit -m "$(cat <<'EOF'
feat(v0.18): resolver exposed sets + re-export resolution

Adds compute_exposed_sets() with topological resolution of
re-exports. New ReexportDecl AST node + grammar form 'EXPOSE <name>'
(no body) for re-exporting an imported symbol. Re-export of a name
not in the import list is rejected with line:col.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Resolver — validate imports

**Files:**
- Modify: `clio/ir/resolver.py` (+`validate_imports`)
- Modify: `tests/test_resolver.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_resolver.py`:

```python
from clio.ir.resolver import validate_imports


def test_e_res_003_symbol_not_exposed(tmp_path):
    (tmp_path / "lib.clio").write_text(
        'CONTRACT X\n  SHAPE:\n    a: str\n'  # INTERNAL (no EXPOSE)
    )
    entry = tmp_path / "main.clio"
    entry.write_text(
        'FROM "./lib.clio" IMPORT X\n'
        'EXPOSE FLOW main\n'
        '  TAKES:\n    x: X\n  GIVES:\n    out: str\n'
        '  - noop(x: x)\n'
        'STEP noop\n  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    x: X\n  GIVES:\n    out: str\n'
        '  CODE: |\n    return {"out": "ok"}\n'
    )
    parsed = resolve_imports(entry)
    sets = compute_exposed_sets(parsed)
    with pytest.raises(CompileError, match=r"'X' is not exposed by"):
        validate_imports(parsed, sets)


def test_e_res_004_symbol_not_found(tmp_path):
    (tmp_path / "lib.clio").write_text('EXPOSE CONTRACT Y\n  SHAPE:\n    a: str\n')
    entry = tmp_path / "main.clio"
    entry.write_text(
        'FROM "./lib.clio" IMPORT X\n'
        'EXPOSE FLOW main\n'
        '  TAKES:\n    x: int\n  GIVES:\n    out: str\n'
        '  - noop(x: x)\n'
        'STEP noop\n  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    x: int\n  GIVES:\n    out: str\n'
        '  CODE: |\n    return {"out": "ok"}\n'
    )
    parsed = resolve_imports(entry)
    sets = compute_exposed_sets(parsed)
    with pytest.raises(CompileError, match=r"'X' not found in"):
        validate_imports(parsed, sets)
```

- [ ] **Step 2: Implement `validate_imports`**

Append to `clio/ir/resolver.py`:

```python
def validate_imports(
    parsed: dict[Path, Program],
    exposed_sets: dict[Path, dict[str, object]],
) -> None:
    """Phase 4: every FROM ... IMPORT X resolves to an exposed symbol."""
    for path, program in parsed.items():
        for imp in program.imports:
            child = (path.parent / imp.path).resolve()
            child_exposed = exposed_sets.get(child, {})
            child_program = parsed.get(child)
            # Find all symbols actually declared in child (regardless of EXPOSE)
            all_declared = set()
            if child_program is not None:
                for d in child_program.decls:
                    if isinstance(d, (FlowDecl, ContractDecl)):
                        all_declared.add(d.name)
            for item in imp.items:
                if item.name in child_exposed:
                    continue
                if item.name in all_declared:
                    raise CompileError(
                        f"{path}:{item.line}:{item.col}: "
                        f"{item.name!r} is not exposed by {imp.path!r}"
                    )
                raise CompileError(
                    f"{path}:{item.line}:{item.col}: "
                    f"{item.name!r} not found in {imp.path!r}"
                )
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_resolver.py -v`
Expected: all resolver tests PASS.

- [ ] **Step 4: Commit**

```bash
git add clio/ir/resolver.py tests/test_resolver.py
git commit -m "$(cat <<'EOF'
feat(v0.18): resolver import validation phase

Adds validate_imports() distinguishing E_RES_003 (symbol declared
but not exposed) from E_RES_004 (symbol not declared at all in the
imported file).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Builder — multi-file `build_ir` with alpha-renaming

**Files:**
- Modify: `clio/ir/builder.py:111` (`build_ir` signature + flatten logic)
- Modify: `clio/ir/builder.py:524` (delete `_compute_exposed_flows`)
- Test: `tests/test_ir_multifile.py` (create)

This is the most complex single task. It threads alpha-renaming through the existing build logic.

- [ ] **Step 1: Write failing tests for multi-file build**

Create `tests/test_ir_multifile.py`:

```python
from pathlib import Path
import pytest
from clio.ir.builder import build_ir
from clio.ir.resolver import resolve_imports
from clio.parser.parser import parse

FIXTURES = Path(__file__).parent / "fixtures" / "imports"


def test_build_simple_multifile():
    entry = FIXTURES / "simple" / "main.clio"
    parsed = resolve_imports(entry)
    graph = build_ir(parsed, entry=entry.resolve())
    flow_names = {f.name for f in graph.flows}
    # Local pipeline + imported classify (exposed names kept as-is)
    assert "pipeline" in flow_names
    assert "classify" in flow_names
    # Internal STEP from lib.clio is alpha-renamed
    step_names = {s.name for s in graph.steps}
    assert "lib__score" in step_names
    assert "score" not in step_names  # internal, renamed


def test_build_ir_backward_compat_single_program():
    """build_ir(Program) without dict still works (v0.17 callers)."""
    src = (
        'FLOW pipeline\n'
        '  TAKES:\n    x: int\n'
        '  GIVES:\n    y: int\n'
        '  - step1(x: x)\n'
        'STEP step1\n  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    x: int\n  GIVES:\n    y: int\n'
        '  CODE: |\n    return {"y": x}\n'
    )
    program = parse(src)
    graph = build_ir(program)
    assert graph.flow is not None
    assert graph.flow.name == "pipeline"


def test_exposed_flow_names_from_explicit_marker():
    """In v0.18, exposed_flow_names is derived from EXPOSE marker on
    entry-file FLOWs only."""
    entry = FIXTURES / "simple" / "main.clio"
    parsed = resolve_imports(entry)
    graph = build_ir(parsed, entry=entry.resolve())
    # Only the entry file's exposed FLOWs (pipeline) — not imported ones
    assert graph.exposed_flow_names == frozenset({"pipeline"})


def test_e_mcp_001_mcp_target_without_expose(tmp_path):
    """target: mcp-server with no EXPOSE FLOW is rejected."""
    entry = tmp_path / "main.clio"
    entry.write_text(
        'target: mcp-server\n'
        'models: { prefer: sonnet }\n'
        'FLOW pipeline\n'  # not exposed
        '  TAKES:\n    x: int\n'
        '  GIVES:\n    y: int\n'
        '  - step1(x: x)\n'
        'STEP step1\n  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    x: int\n  GIVES:\n    y: int\n'
        '  CODE: |\n    return {"y": x}\n'
    )
    parsed = resolve_imports(entry)
    from clio.ir.resolver import CompileError
    with pytest.raises(CompileError, match=r"requires at least one EXPOSE FLOW"):
        build_ir(parsed, entry=entry.resolve())
```

- [ ] **Step 2: Run tests to confirm failures**

Run: `uv run pytest tests/test_ir_multifile.py -v`
Expected: all tests FAIL.

- [ ] **Step 3: Modify `build_ir` signature and add flatten logic**

In `clio/ir/builder.py:111`, replace the function:

```python
def build_ir(
    parsed: "dict[Path, Program] | Program",
    entry: "Path | None" = None,
    flow_name: str | None = None,
) -> FlowGraph:
    """Build a FlowGraph from either a single Program (v0.17 callers)
    or a dict[Path, Program] (v0.18 multi-file).

    For the multi-file case, internal (non-exposed) STEP/CONTRACT/FLOW
    names are alpha-renamed to '{file_stem}__{name}' to avoid global
    name collisions in the flat output. Exposed names keep their
    original form.
    """
    from clio.parser.ast_nodes import Program as _Program

    # Backward-compat: single Program → wrap as single-entry dict
    if isinstance(parsed, _Program):
        return _build_ir_single(parsed, flow_name=flow_name)

    if entry is None:
        raise ValueError("build_ir requires `entry` when called with a dict")

    # Validate + compute exposed sets (calls into resolver)
    from clio.ir.resolver import (
        compute_exposed_sets,
        validate_imports,
        validate_per_file,
    )
    validate_per_file(parsed, entry=entry)
    exposed_sets = compute_exposed_sets(parsed)
    validate_imports(parsed, exposed_sets)

    # Flatten with alpha-renaming
    merged_program = _flatten_to_program(parsed, entry, exposed_sets)
    return _build_ir_single(merged_program, flow_name=flow_name)


def _build_ir_single(program: Program, flow_name: str | None = None) -> FlowGraph:
    # ... ORIGINAL body of build_ir, unchanged ...
```

The trick is: rename the original `build_ir` body to `_build_ir_single`. All existing tests that call `build_ir(program)` still go through this same code path. Existing tests for cross-target emission also remain green.

- [ ] **Step 4: Implement `_flatten_to_program` with alpha-renaming**

After `_build_ir_single`, in `clio/ir/builder.py`, add:

```python
def _flatten_to_program(
    parsed: "dict[Path, Program]",
    entry: "Path",
    exposed_sets: "dict[Path, dict[str, object]]",
) -> Program:
    """Merge multiple Programs into a single Program with internal
    symbols alpha-renamed.

    Convention: internal name X in file 'lib/nlp.clio' becomes
    'nlp__X'. Exposed names keep their original form.

    Returns a single Program containing all flattened decls. RESOURCES
    and TESTs are taken only from the entry file.
    """
    from clio.parser.ast_nodes import (
        ContractDecl,
        FlowDecl,
        Program,
        StepDecl,
        ResourcesDecl,
        TestDecl,
        ReexportDecl,
    )
    all_decls: list[object] = []

    # Per-file rename tables: {original_name: renamed_name}
    rename_tables: dict[Path, dict[str, str]] = {}

    # Pass 1: build rename tables for each file
    for path, program in parsed.items():
        stem = _file_stem(path)
        local_renames: dict[str, str] = {}
        for decl in program.decls:
            if isinstance(decl, (FlowDecl, ContractDecl)):
                if not decl.exposed:
                    local_renames[decl.name] = f"{stem}__{decl.name}"
            elif isinstance(decl, StepDecl):
                # STEPs are always internal in v0.18
                local_renames[decl.name] = f"{stem}__{decl.name}"
        rename_tables[path] = local_renames

    # Pass 2: emit decls with renames applied to internal references
    # and imports collapsed into a flat per-file scope.
    for path, program in parsed.items():
        # Build per-file scope: imported_name → declared_target_name
        imported_scope: dict[str, str] = {}
        for imp in program.imports:
            child = (path.parent / imp.path).resolve()
            for item in imp.items:
                local_name = item.alias or item.name
                # Resolve to the actual declared name (post-rename if internal)
                target_decl = exposed_sets[child].get(item.name)
                if target_decl is None:
                    continue  # validate_imports would have caught this
                # Exposed names keep their original form across files
                target_name = getattr(target_decl, "name", item.name)
                imported_scope[local_name] = target_name

        # Emit each local decl, applying renames + import resolution
        for decl in program.decls:
            if isinstance(decl, ReexportDecl):
                continue  # re-exports do not produce new decls
            if isinstance(decl, ResourcesDecl):
                if path == entry:
                    all_decls.append(decl)
                continue
            if isinstance(decl, TestDecl):
                if path == entry:
                    all_decls.append(decl)
                continue
            renamed_decl = _rename_decl(
                decl, rename_tables[path], imported_scope,
            )
            all_decls.append(renamed_decl)

    return Program(decls=tuple(all_decls))


def _file_stem(path: "Path") -> str:
    """Derive a safe identifier prefix from a file path.

    'lib/nlp.clio' → 'nlp'
    'shared_utils.clio' → 'shared_utils'
    """
    return path.stem.replace("-", "_")


def _rename_decl(
    decl: object,
    local_renames: "dict[str, str]",
    imported_scope: "dict[str, str]",
) -> object:
    """Apply renames to a decl: rename the decl's own name if internal,
    and rewrite any references in its body.

    This is a structural recursion over the AST. For v0.18 we cover
    StepCall.callee, ForEachBlock.body, IfBlock branches, MatchBlock
    cases, WhileBlock body, and ContractRef inside type expressions
    (in TAKES/GIVES).
    """
    from clio.parser.ast_nodes import (
        ContractDecl,
        FlowDecl,
        StepDecl,
        StepCall,
        ForEachBlock,
        IfBlock,
        MatchBlock,
        MatchCase,
        WhileBlock,
        ContractRef,
        Field,
        RecordType,
        ListType,
    )
    from dataclasses import replace

    # Combined name resolution: imported_scope wins, then local_renames
    def resolve_name(n: str) -> str:
        if n in imported_scope:
            return imported_scope[n]
        return local_renames.get(n, n)

    def rename_type(t):
        if isinstance(t, ContractRef):
            return ContractRef(name=resolve_name(t.name))
        if isinstance(t, ListType):
            return ListType(elem=rename_type(t.elem))
        if isinstance(t, RecordType):
            return RecordType(fields=tuple(
                Field(name=f.name, type=rename_type(f.type))
                for f in t.fields
            ))
        return t

    def rename_call(c):
        if isinstance(c, StepCall):
            return replace(c, callee=resolve_name(c.callee))
        if isinstance(c, ForEachBlock):
            return replace(c, body=tuple(rename_call(x) for x in c.body))
        if isinstance(c, IfBlock):
            return replace(
                c,
                then_branch=tuple(rename_call(x) for x in c.then_branch),
                else_branch=tuple(rename_call(x) for x in c.else_branch),
            )
        if isinstance(c, MatchBlock):
            return replace(c, cases=tuple(
                replace(case, body=tuple(rename_call(x) for x in case.body))
                for case in c.cases
            ))
        if isinstance(c, WhileBlock):
            return replace(c, body=tuple(rename_call(x) for x in c.body))
        return c

    if isinstance(decl, StepDecl):
        new_name = local_renames.get(decl.name, decl.name)
        new_takes = tuple(
            Field(name=f.name, type=rename_type(f.type)) for f in decl.takes
        )
        new_gives = (
            Field(name=decl.gives.name, type=rename_type(decl.gives.type))
            if decl.gives else None
        )
        return replace(decl, name=new_name, takes=new_takes, gives=new_gives)
    if isinstance(decl, ContractDecl):
        new_name = local_renames.get(decl.name, decl.name)
        # ContractDecl.shape is a RecordType
        return replace(decl, name=new_name, shape=rename_type(decl.shape))
    if isinstance(decl, FlowDecl):
        new_name = local_renames.get(decl.name, decl.name)
        new_chain = tuple(rename_call(x) for x in decl.chain)
        new_takes = tuple(
            Field(name=f.name, type=rename_type(f.type)) for f in decl.takes
        )
        new_gives = tuple(
            Field(name=f.name, type=rename_type(f.type)) for f in decl.gives
        )
        return replace(
            decl, name=new_name, chain=new_chain,
            takes=new_takes, gives=new_gives,
        )
    return decl
```

- [ ] **Step 5: Replace `_compute_exposed_flows` derivation**

In `clio/ir/builder.py`, find the call to `_compute_exposed_flows(all_flows, flow_sigs)` (line ~219). After `_build_ir_single` is defined, that call lives inside it. Change the derivation to use the `exposed: bool` flag on FLOWs that came from the entry file. Since after flattening the entry-file FLOWs are the ones whose names are unprefixed AND have `exposed=True`, replace:

```python
exposed_flow_names=_compute_exposed_flows(all_flows, flow_sigs),
```

with:

```python
exposed_flow_names=frozenset(
    f.name for f in all_flows.values()
    if _was_exposed_in_source(f, program)
),
```

And add a helper at module scope:

```python
def _was_exposed_in_source(flow_ir: "FlowIR", program: Program) -> bool:
    """Return True if the FLOW was declared with EXPOSE in the source
    program."""
    for decl in program.decls:
        if (
            decl.__class__.__name__ == "FlowDecl"
            and decl.name == flow_ir.name
            and getattr(decl, "exposed", False)
        ):
            return True
    return False
```

Then delete `_compute_exposed_flows` and `_collect_flow_call_names` if no longer used. (Confirm `_collect_flow_call_names` has no other callers via `grep -rn _collect_flow_call_names clio/`.)

Also reject empty `exposed_flow_names` when the target is `mcp-server`:

In `_build_ir_single`, after computing `exposed_flow_names`, add:

```python
if (
    resources_ir is not None
    and resources_ir.target == "mcp-server"
    and not exposed_flow_names
):
    from clio.ir.resolver import CompileError
    raise CompileError(
        f"{program.source_path or '<inline>'}: "
        f"target 'mcp-server' requires at least one EXPOSE FLOW in the entry file"
    )
```

- [ ] **Step 6: Run multi-file IR tests**

Run: `uv run pytest tests/test_ir_multifile.py tests/test_resolver.py -v`
Expected: all PASS.

- [ ] **Step 7: Full regression**

Run: `uv run pytest tests/ -v --tb=no -q`
Expected: some mcp-server fixtures fail due to E_MCP_001 (no EXPOSE). These are expected — they will be migrated in Task 13. All other tests (~900) must pass.

If non-fixture tests fail, fix them now before commit.

- [ ] **Step 8: Commit**

```bash
git add clio/ir/builder.py tests/test_ir_multifile.py
git commit -m "$(cat <<'EOF'
feat(v0.18): multi-file build_ir with alpha-renaming

Extends build_ir() to accept dict[Path, Program] in addition to a
single Program (backward compatible). For multi-file inputs, flattens
all decls into a single Program with internal (non-exposed) symbols
alpha-renamed as '{file_stem}__{name}'. Exposed names keep their
original form. RESOURCES and TEST blocks are taken from the entry file
only. exposed_flow_names is now derived from the explicit EXPOSE
marker on entry-file FLOWs (replacing the v0.17 sibling heuristic).
Empty exposed_flow_names on target: mcp-server raises E_MCP_001.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase B — Emitters, CLI, Migration (parallelizable)

Tasks 9–14 can be done in parallel by separate subagents — they share Phase A's foundation but have no inter-dependencies.

### Task 9: Emitter `mcp-server` — explicit `exposed_flow_names`

**Files:**
- Modify: `clio/emitters/mcp_server.py:48-55` (already uses `graph.exposed_flow_names`)
- Test: `tests/test_emitters/test_mcp_server_multifile.py` (create)

The mcp-server emitter already reads `graph.exposed_flow_names`. Phase A already changed the derivation. Only new tests + a small comment update remain.

- [ ] **Step 1: Write multi-file mcp-server tests**

Create `tests/test_emitters/test_mcp_server_multifile.py`:

```python
from pathlib import Path
from clio.cli import _cmd_compile

FIXTURES = Path(__file__).parent.parent / "fixtures" / "imports"


def test_mcp_multifile_emits_tool_per_exposed_flow(tmp_path):
    """A simple 2-file project compiles to a server with one tool
    per exposed FLOW (entry-file only)."""
    # Build a self-contained fixture with mcp-server target
    project = tmp_path / "src"
    project.mkdir()
    (project / "lib.clio").write_text(
        'EXPOSE CONTRACT Article\n  SHAPE:\n    title: str\n'
        'EXPOSE FLOW classify\n'
        '  TAKES:\n    article: Article\n  GIVES:\n    label: str\n'
        '  - score(text: article.title)\n'
        'STEP score\n  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    text: str\n  GIVES:\n    label: str\n'
        '  CODE: |\n    return {"label": "ok"}\n'
    )
    (project / "main.clio").write_text(
        'target: mcp-server\n'
        'models: { prefer: sonnet }\n'
        'FROM "./lib.clio" IMPORT Article, classify\n'
        'EXPOSE FLOW pipeline\n'
        '  TAKES:\n    article: Article\n  GIVES:\n    label: str\n'
        '  - classify(article: article)\n'
    )
    out = tmp_path / "server"
    rc = _cmd_compile(
        str(project / "main.clio"),
        target="mcp-server",
        output=str(out),
    )
    assert rc == 0
    server_py = (out / "server.py").read_text()
    # Only the entry file's exposed FLOW becomes a tool
    assert "@mcp.tool" in server_py
    assert "def pipeline" in server_py
    # The imported classify is NOT a tool (was exposed in lib.clio but
    # only entry-file EXPOSE counts for tool exposure)
    assert "def classify" in server_py  # generated as function...
    # ...but not preceded by @mcp.tool (only pipeline is)
    pipeline_idx = server_py.index("def pipeline")
    classify_idx = server_py.index("def classify")
    # Find @mcp.tool decorations:
    tool_count = server_py.count("@mcp.tool")
    assert tool_count == 1


def test_mcp_rejects_no_expose(tmp_path):
    """target: mcp-server with no EXPOSE FLOW fails with E_MCP_001."""
    src = tmp_path / "main.clio"
    src.write_text(
        'target: mcp-server\n'
        'models: { prefer: sonnet }\n'
        'FLOW pipeline\n'
        '  TAKES:\n    x: int\n  GIVES:\n    y: int\n'
        '  - step1(x: x)\n'
        'STEP step1\n  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    x: int\n  GIVES:\n    y: int\n'
        '  CODE: |\n    return {"y": x}\n'
    )
    rc = _cmd_compile(str(src), target="mcp-server", output=str(tmp_path / "out"))
    assert rc != 0
```

- [ ] **Step 2: Update doc comment on `exposed_flow_names`**

In `clio/ir/graph.py:421`, replace the comment:

```python
exposed_flow_names: frozenset[str] = frozenset()
# v0.18 — names of entry-file FLOWs marked EXPOSE; for target=mcp-server
# these become the public tools. Imported FLOWs are never exposed
# transitively by the importing file.
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_emitters/test_mcp_server_multifile.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add clio/ir/graph.py tests/test_emitters/test_mcp_server_multifile.py
git commit -m "$(cat <<'EOF'
feat(v0.18): mcp-server multifile tests + exposed_flow_names comment

Adds multifile tests for the mcp-server emitter validating that only
entry-file EXPOSE FLOWs become MCP tools. Imported FLOWs are present
as helper functions but not decorated with @mcp.tool. Updates the
exposed_flow_names doc comment to reflect v0.18 semantics.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Emitter `claude-cli` — reject IMPORT

**Files:**
- Modify: `clio/emitters/claude_cli.py`
- Test: `tests/test_emitters/test_claude_cli_multifile.py` (create)

- [ ] **Step 1: Write failing test**

Create `tests/test_emitters/test_claude_cli_multifile.py`:

```python
from pathlib import Path
import pytest
from clio.cli import _cmd_compile


def test_claude_cli_rejects_import(tmp_path):
    """target: claude-cli rejects sources containing FROM ... IMPORT ..."""
    (tmp_path / "lib.clio").write_text(
        'EXPOSE CONTRACT X\n  SHAPE:\n    a: str\n'
    )
    (tmp_path / "main.clio").write_text(
        'target: claude-cli\n'
        'models: { prefer: sonnet }\n'
        'FROM "./lib.clio" IMPORT X\n'
        'FLOW pipeline\n'
        '  TAKES:\n    x: X\n  GIVES:\n    y: str\n'
        '  - step1(x: x)\n'
        'STEP step1\n  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    x: X\n  GIVES:\n    y: str\n'
        '  CODE: |\n    return {"y": "ok"}\n'
    )
    rc = _cmd_compile(
        str(tmp_path / "main.clio"),
        target="claude-cli",
        output=str(tmp_path / "out"),
    )
    assert rc != 0
```

- [ ] **Step 2: Add the rejection guard**

In `clio/emitters/claude_cli.py`, find the entry function (likely `emit_project` or similar — grep `def emit`). At the top of the body, add:

```python
def emit_project(graph: FlowGraph, output_dir: Path) -> None:
    # v0.18: claude-cli does not support cross-file imports.
    # If any FLOW in the graph has the file_stem prefix '{stem}__'
    # in its name (indicating it was imported), reject.
    for f in graph.flows:
        if "__" in f.name and not f.name.startswith("_"):
            # Heuristic: alpha-renamed internal from another file
            raise ValueError(
                f"target 'claude-cli' does not support cross-file imports "
                f"(deferred): FLOW {f.name!r} appears to be imported"
            )
    # ... rest of existing emission ...
```

A cleaner approach: pass a "had_imports" flag through the graph. Since `FlowGraph` schema doesn't change, attach the info via a different mechanism — pass it through `_cmd_compile`:

In `clio/cli.py:_cmd_compile`, after `resolve_imports`, set a flag:

```python
parsed = resolve_imports(src_path)
had_imports = any(p.imports for p in parsed.values())
graph = build_ir(parsed, entry=src_path.resolve(), flow_name=flow)
if target == "claude-cli" and had_imports:
    print(f"error: target 'claude-cli' does not support cross-file imports "
          f"(deferred to a future release)")
    return 1
```

This is cleaner than threading a flag through the graph. Use this approach.

- [ ] **Step 3: Run test**

Run: `uv run pytest tests/test_emitters/test_claude_cli_multifile.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add clio/cli.py tests/test_emitters/test_claude_cli_multifile.py
git commit -m "$(cat <<'EOF'
feat(v0.18): claude-cli rejects cross-file imports (E_CLI_001)

target: claude-cli refuses sources that contain FROM ... IMPORT ...
declarations. Cleaner UX than emitting incorrect output.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Emitter `python` — multi-file fixtures + snapshots

**Files:**
- Test: `tests/test_emitters/test_python_multifile.py` (create)
- Fixtures: `tests/fixtures/imports/simple/` (reuse from Task 4)

- [ ] **Step 1: Write golden snapshot test**

Create `tests/test_emitters/test_python_multifile.py`:

```python
from pathlib import Path
from clio.cli import _cmd_compile

FIXTURES = Path(__file__).parent.parent / "fixtures" / "imports"


def test_python_multifile_simple(tmp_path):
    """Simple 2-file project compiles to a single python module
    containing both files' flows."""
    out = tmp_path / "out"
    rc = _cmd_compile(
        str(FIXTURES / "simple" / "main.clio"),
        target="python",
        output=str(out),
    )
    assert rc == 0
    main_py = (out / "main.py").read_text()
    # Entry FLOW: name unchanged
    assert "def run_pipeline(" in main_py
    # Imported exposed FLOW: name unchanged (was EXPOSE in lib.clio)
    assert "def run_classify(" in main_py
    # Imported internal STEP: alpha-renamed
    assert "def step_lib__score" in main_py or "lib__score" in main_py


def test_python_multifile_diamond(tmp_path):
    """Diamond import: shared.clio is parsed once, even if imported
    from left.clio and right.clio."""
    project = tmp_path / "src"
    project.mkdir()
    (project / "shared.clio").write_text(
        'EXPOSE CONTRACT Doc\n  SHAPE:\n    text: str\n'
    )
    (project / "left.clio").write_text(
        'FROM "./shared.clio" IMPORT Doc\n'
        'EXPOSE FLOW left_flow\n'
        '  TAKES:\n    doc: Doc\n  GIVES:\n    out: str\n'
        '  - left_step(doc: doc)\n'
        'STEP left_step\n  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    doc: Doc\n  GIVES:\n    out: str\n'
        '  CODE: |\n    return {"out": "left"}\n'
    )
    (project / "right.clio").write_text(
        'FROM "./shared.clio" IMPORT Doc\n'
        'EXPOSE FLOW right_flow\n'
        '  TAKES:\n    doc: Doc\n  GIVES:\n    out: str\n'
        '  - right_step(doc: doc)\n'
        'STEP right_step\n  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    doc: Doc\n  GIVES:\n    out: str\n'
        '  CODE: |\n    return {"out": "right"}\n'
    )
    (project / "main.clio").write_text(
        'target: python\n'
        'models: { prefer: sonnet }\n'
        'FROM "./left.clio" IMPORT left_flow\n'
        'FROM "./right.clio" IMPORT right_flow\n'
        'FROM "./shared.clio" IMPORT Doc\n'
        'EXPOSE FLOW pipeline\n'
        '  TAKES:\n    doc: Doc\n  GIVES:\n    out: str\n'
        '  - left_flow(doc: doc)\n'
    )
    out = tmp_path / "out"
    rc = _cmd_compile(str(project / "main.clio"), target="python", output=str(out))
    assert rc == 0
    main_py = (out / "main.py").read_text()
    assert "def run_pipeline" in main_py
    assert "def run_left_flow" in main_py
    assert "def run_right_flow" in main_py


def test_python_reexport(tmp_path):
    """Re-exported FLOW from facade.clio is accessible via main."""
    project = tmp_path / "src"
    project.mkdir()
    (project / "lib.clio").write_text(
        'EXPOSE CONTRACT X\n  SHAPE:\n    a: str\n'
        'EXPOSE FLOW classify\n'
        '  TAKES:\n    x: X\n  GIVES:\n    out: str\n'
        '  - score(x: x)\n'
        'STEP score\n  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    x: X\n  GIVES:\n    out: str\n'
        '  CODE: |\n    return {"out": "ok"}\n'
    )
    (project / "facade.clio").write_text(
        'FROM "./lib.clio" IMPORT X, classify\n'
        'EXPOSE X\n'
        'EXPOSE classify\n'
    )
    (project / "main.clio").write_text(
        'target: python\n'
        'models: { prefer: sonnet }\n'
        'FROM "./facade.clio" IMPORT X, classify\n'
        'EXPOSE FLOW pipeline\n'
        '  TAKES:\n    x: X\n  GIVES:\n    out: str\n'
        '  - classify(x: x)\n'
    )
    out = tmp_path / "out"
    rc = _cmd_compile(str(project / "main.clio"), target="python", output=str(out))
    assert rc == 0
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_emitters/test_python_multifile.py -v`
Expected: 3 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_emitters/test_python_multifile.py
git commit -m "$(cat <<'EOF'
test(v0.18): python emitter multi-file fixtures (simple, diamond, reexport)

No emitter logic change: the python emitter consumes the flattened
FlowGraph just like in v0.17. These golden tests verify the result
when the input is a multi-file project.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Emitter `claude-skill` — multi-file fixtures

**Files:**
- Test: `tests/test_emitters/test_claude_skill_multifile.py` (create)

- [ ] **Step 1: Write golden snapshot test**

Create `tests/test_emitters/test_claude_skill_multifile.py`:

```python
from pathlib import Path
from clio.cli import _cmd_compile

FIXTURES = Path(__file__).parent.parent / "fixtures" / "imports"


def test_claude_skill_multifile_simple(tmp_path):
    out = tmp_path / "skill"
    rc = _cmd_compile(
        str(FIXTURES / "simple" / "main.clio"),
        target="claude-skill",
        output=str(out),
    )
    assert rc == 0
    skill_md = (out / "SKILL.md").read_text()
    # Entry FLOW pipeline is the main flow described in SKILL.md
    assert "pipeline" in skill_md
    # Imported scripts are present
    assert (out / "scripts").exists()


def test_claude_skill_multifile_reexport(tmp_path):
    """Re-exported symbols should compile cleanly to a skill bundle."""
    project = tmp_path / "src"
    project.mkdir()
    (project / "lib.clio").write_text(
        'EXPOSE CONTRACT X\n  SHAPE:\n    a: str\n'
        'EXPOSE FLOW classify\n'
        '  TAKES:\n    x: X\n  GIVES:\n    out: str\n'
        '  - score(x: x)\n'
        'STEP score\n  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    x: X\n  GIVES:\n    out: str\n'
        '  CODE: |\n    return {"out": "ok"}\n'
    )
    (project / "main.clio").write_text(
        'target: claude-skill\n'
        'models: { prefer: sonnet }\n'
        'FROM "./lib.clio" IMPORT X, classify\n'
        'EXPOSE FLOW pipeline\n'
        '  TAKES:\n    x: X\n  GIVES:\n    out: str\n'
        '  - classify(x: x)\n'
    )
    rc = _cmd_compile(str(project / "main.clio"), target="claude-skill", output=str(tmp_path / "out"))
    assert rc == 0
```

- [ ] **Step 2: Run tests + commit**

Run: `uv run pytest tests/test_emitters/test_claude_skill_multifile.py -v`
Expected: PASS.

```bash
git add tests/test_emitters/test_claude_skill_multifile.py
git commit -m "test(v0.18): claude-skill emitter multi-file fixtures

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Emitter `langgraph` — multi-file fixtures

**Files:**
- Test: `tests/test_emitters/test_langgraph_multifile.py` (create)

Same pattern as Task 12, target `langgraph`. Verify compilation succeeds and the resulting StateGraph code references both entry-file and imported FLOWs (with internals alpha-renamed).

- [ ] **Step 1: Write the test**

```python
from pathlib import Path
from clio.cli import _cmd_compile

FIXTURES = Path(__file__).parent.parent / "fixtures" / "imports"


def test_langgraph_multifile_simple(tmp_path):
    out = tmp_path / "out"
    rc = _cmd_compile(
        str(FIXTURES / "simple" / "main.clio"),
        target="langgraph",
        output=str(out),
    )
    assert rc == 0
    main_py = (out / "main.py").read_text()
    assert "pipeline" in main_py
    assert "classify" in main_py
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/test_emitters/test_langgraph_multifile.py -v
git add tests/test_emitters/test_langgraph_multifile.py
git commit -m "test(v0.18): langgraph emitter multi-file fixtures

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: CLI integration (`compile`, `check`, `graph`)

**Files:**
- Modify: `clio/cli.py:82` (`_cmd_compile`), `:116` (`_cmd_check`), `:129` (`_cmd_graph`)
- Test: extend `tests/test_cli.py`

- [ ] **Step 1: Update `_cmd_compile` to use the resolver**

Replace in `clio/cli.py:82`:

```python
def _cmd_compile(source: str, target: str, output: str, flow: str | None = None) -> int:
    src_path = Path(source)
    if not src_path.exists():
        print(f"error: source file not found: {source}", file=sys.stderr)
        return 1
    try:
        from clio.ir.resolver import resolve_imports, CompileError
        parsed = resolve_imports(src_path)
        had_imports = any(p.imports for p in parsed.values())
        graph = build_ir(parsed, entry=src_path.resolve(), flow_name=flow)
        if target == "claude-cli" and had_imports:
            print(
                "error: target 'claude-cli' does not support cross-file imports "
                "(deferred to a future release)",
                file=sys.stderr,
            )
            return 1
    except (ParseError, IRBuildError, CompileError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    out_path = Path(output)
    # ... rest unchanged: dispatch to emitter ...
```

- [ ] **Step 2: Update `_cmd_check`**

```python
def _cmd_check(source: str) -> int:
    src_path = Path(source)
    if not src_path.exists():
        print(f"error: source file not found: {source}", file=sys.stderr)
        return 1
    try:
        from clio.ir.resolver import resolve_imports, CompileError
        parsed = resolve_imports(src_path)
        build_ir(parsed, entry=src_path.resolve())
    except (ParseError, IRBuildError, CompileError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("ok")
    return 0
```

- [ ] **Step 3: Update `_cmd_graph`**

Similar treatment in `_cmd_graph:129`: use `resolve_imports` + `build_ir(parsed, entry=...)`.

- [ ] **Step 4: Write CLI tests**

Append to `tests/test_cli.py`:

```python
def test_cli_compile_multifile(tmp_path):
    project = tmp_path / "src"
    project.mkdir()
    (project / "lib.clio").write_text(
        'EXPOSE CONTRACT X\n  SHAPE:\n    a: str\n'
        'EXPOSE FLOW classify\n'
        '  TAKES:\n    x: X\n  GIVES:\n    y: str\n'
        '  - score(x: x)\n'
        'STEP score\n  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    x: X\n  GIVES:\n    y: str\n'
        '  CODE: |\n    return {"y": "ok"}\n'
    )
    (project / "main.clio").write_text(
        'target: python\n'
        'models: { prefer: sonnet }\n'
        'FROM "./lib.clio" IMPORT X, classify\n'
        'EXPOSE FLOW pipeline\n'
        '  TAKES:\n    x: X\n  GIVES:\n    y: str\n'
        '  - classify(x: x)\n'
    )
    rc = _cmd_compile(str(project / "main.clio"), target="python", output=str(tmp_path / "out"))
    assert rc == 0
    assert (tmp_path / "out" / "main.py").exists()


def test_cli_check_multifile(tmp_path):
    # Setup same as above
    # ...
    rc = _cmd_check(str(project / "main.clio"))
    assert rc == 0


def test_cli_check_reports_missing_import(tmp_path):
    src = tmp_path / "main.clio"
    src.write_text('FROM "./missing.clio" IMPORT X\n')
    rc = _cmd_check(str(src))
    assert rc != 0
```

- [ ] **Step 5: Run + commit**

```bash
uv run pytest tests/test_cli.py -v
git add clio/cli.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat(v0.18): CLI compile/check/graph use multi-file resolver

_cmd_compile, _cmd_check, _cmd_graph now call resolve_imports() before
build_ir(). Errors from the resolver (cycles, missing files, validation
failures) are surfaced with the source file context.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Migration tool `clio doctor --migrate-v018`

**Files:**
- Modify: `clio/diagnostics.py` (+`migrate_v018`)
- Modify: `clio/cli.py:203` (`_cmd_doctor` accepts `--migrate-v018` flag and `--write`)
- Test: `tests/test_doctor_migrate.py` (create)
- Fixtures: `tests/fixtures/imports/migration_v017_to_v018/`

- [ ] **Step 1: Create migration fixture**

`tests/fixtures/imports/migration_v017_to_v018/before.clio`:

```
target: mcp-server
models:
  prefer: sonnet

CONTRACT Article
  SHAPE:
    title: str

FLOW classify_article
  TAKES:
    article: Article
  GIVES:
    label: str
  - score(text: article.title)

FLOW _internal_helper
  TAKES:
    x: int
  GIVES:
    y: int
  - bump(x: x)

STEP score
  MODE: exact
  IMPL: code
  LANG: python
  TAKES:
    text: str
  GIVES:
    label: str
  CODE: |
    return {"label": "ok"}

STEP bump
  MODE: exact
  IMPL: code
  LANG: python
  TAKES:
    x: int
  GIVES:
    y: int
  CODE: |
    return {"y": x + 1}
```

`tests/fixtures/imports/migration_v017_to_v018/expected_after.clio` — same file with `EXPOSE` added before `CONTRACT Article`, `FLOW classify_article`, and `FLOW _internal_helper` (the latter has TAKES/GIVES but isn't called by a sibling, so the heuristic exposes it; user can manually un-expose if they want).

Wait — `_internal_helper` shouldn't be auto-exposed if it's called by another FLOW. Refine the fixture: re-read the heuristic — v0.17 exposes a signed FLOW IFF not called by a sibling. So if `_internal_helper` is not called by `classify_article`, it would be exposed by the heuristic. Either we make the fixture call it, or accept that it gets exposed. Easier: make `classify_article` call `_internal_helper`:

Update before.clio:

```
FLOW classify_article
  TAKES:
    article: Article
  GIVES:
    label: str
  - _internal_helper(x: article.title)
  - score(text: $1)
```

Now `_internal_helper` IS called by a sibling, so the heuristic leaves it alone.

`expected_after.clio` then has EXPOSE only before CONTRACT Article and FLOW classify_article.

- [ ] **Step 2: Implement `migrate_v018` in diagnostics.py**

In `clio/diagnostics.py`, add:

```python
def migrate_v018(source: Path) -> tuple[str, list[tuple[int, str]]]:
    """Compute the v0.17 → v0.18 migration for a single .clio file.

    Returns:
      (new_source, changes) where `changes` is a list of
      (line_number, inserted_text) describing each EXPOSE insertion.

    Uses the v0.17 sibling-call heuristic to identify which FLOWs
    would have been implicitly exposed for mcp-server.
    """
    from clio.parser.ast_nodes import ContractDecl, FlowDecl
    from clio.parser.parser import parse

    text = source.read_text()
    program = parse(text)
    lines = text.splitlines(keepends=True)

    # Identify FLOWs with explicit signature
    signed_flows = [
        d for d in program.decls
        if isinstance(d, FlowDecl) and d.takes and d.gives
    ]
    # Compute which are NOT called by sibling FLOWs (v0.17 heuristic)
    called: set[str] = set()
    for f in program.decls:
        if isinstance(f, FlowDecl):
            called.update(_collect_call_names(f.chain))
    auto_exposed_flow_names = {
        f.name for f in signed_flows if f.name not in called
    }
    # Contracts referenced by exposed FLOWs' signatures
    auto_exposed_contract_names: set[str] = set()
    for f in signed_flows:
        if f.name in auto_exposed_flow_names:
            auto_exposed_contract_names.update(_collect_contract_refs(f))

    # Compute insertions
    changes: list[tuple[int, str]] = []
    for d in program.decls:
        if (
            isinstance(d, FlowDecl)
            and d.name in auto_exposed_flow_names
        ):
            changes.append((d.line, "EXPOSE "))
        if (
            isinstance(d, ContractDecl)
            and d.name in auto_exposed_contract_names
        ):
            changes.append((d.line, "EXPOSE "))

    # Apply insertions to the source text (line-based)
    new_lines = list(lines)
    for line_num, prefix in changes:
        # line_num is 1-based; lines is 0-based
        idx = line_num - 1
        if 0 <= idx < len(new_lines):
            # Prepend "EXPOSE " before the "FLOW " or "CONTRACT " keyword
            new_lines[idx] = prefix + new_lines[idx]
    return "".join(new_lines), changes


def _collect_call_names(chain) -> set[str]:
    from clio.parser.ast_nodes import (
        StepCall, ForEachBlock, IfBlock, MatchBlock, WhileBlock,
    )
    out: set[str] = set()
    for x in chain:
        if isinstance(x, StepCall):
            out.add(x.callee)
        elif isinstance(x, ForEachBlock):
            out |= _collect_call_names(x.body)
        elif isinstance(x, IfBlock):
            out |= _collect_call_names(x.then_branch)
            out |= _collect_call_names(x.else_branch)
        elif isinstance(x, MatchBlock):
            for case in x.cases:
                out |= _collect_call_names(case.body)
        elif isinstance(x, WhileBlock):
            out |= _collect_call_names(x.body)
    return out


def _collect_contract_refs(flow_decl) -> set[str]:
    from clio.parser.ast_nodes import ContractRef
    out: set[str] = set()
    for f in (*flow_decl.takes, *flow_decl.gives):
        out |= _walk_type_for_refs(f.type)
    return out


def _walk_type_for_refs(t) -> set[str]:
    from clio.parser.ast_nodes import ContractRef, ListType, RecordType
    if isinstance(t, ContractRef):
        return {t.name}
    if isinstance(t, ListType):
        return _walk_type_for_refs(t.elem)
    if isinstance(t, RecordType):
        out: set[str] = set()
        for fl in t.fields:
            out |= _walk_type_for_refs(fl.type)
        return out
    return set()
```

- [ ] **Step 3: Extend `_cmd_doctor` to accept `--migrate-v018` and `--write`**

In `clio/cli.py:203`, modify `_cmd_doctor`:

```python
def _cmd_doctor(
    source: str | None,
    flow: str | None = None,
    migrate_v018: bool = False,
    write: bool = False,
) -> int:
    # ... existing doctor body ...

    if migrate_v018 and source:
        from clio.diagnostics import migrate_v018 as do_migrate
        src_path = Path(source)
        new_text, changes = do_migrate(src_path)
        if not changes:
            print(f"{source}: no v0.18 migration needed")
            return 0
        print(f"file: {source}")
        print(f"Proposed changes (using v0.17 sibling-call heuristic):")
        for line_num, _prefix in changes:
            print(f"  line {line_num}: + EXPOSE  before existing declaration")
        if write:
            src_path.write_text(new_text)
            print(f"\nWrote {len(changes)} change(s) to {source}")
        else:
            print(f"\nRun with --write to apply.")
        return 0
```

Also add the CLI flags in `main(argv)`: the argparse subparser for `doctor` needs `--migrate-v018` and `--write`.

- [ ] **Step 4: Write tests**

Create `tests/test_doctor_migrate.py`:

```python
from pathlib import Path
from clio.diagnostics import migrate_v018


FIXTURES = Path(__file__).parent / "fixtures" / "imports" / "migration_v017_to_v018"


def test_migrate_v018_proposes_exposes():
    new_text, changes = migrate_v018(FIXTURES / "before.clio")
    # Expect at least 2 changes: CONTRACT Article + FLOW classify_article
    change_lines = {ln for ln, _ in changes}
    assert len(changes) >= 2


def test_migrate_v018_idempotent_on_already_migrated(tmp_path):
    """A file already containing EXPOSE produces no further changes."""
    src = tmp_path / "already.clio"
    src.write_text(
        'target: mcp-server\n'
        'models: { prefer: sonnet }\n'
        'EXPOSE CONTRACT X\n  SHAPE:\n    a: str\n'
        'EXPOSE FLOW pipeline\n'
        '  TAKES:\n    x: X\n  GIVES:\n    y: str\n'
        '  - step1(x: x)\n'
        'STEP step1\n  MODE: exact\n  IMPL: code\n  LANG: python\n'
        '  TAKES:\n    x: X\n  GIVES:\n    y: str\n'
        '  CODE: |\n    return {"y": "ok"}\n'
    )
    before = src.read_text()
    new_text, changes = migrate_v018(src)
    assert changes == []
    assert new_text == before


def test_migrate_v018_output_matches_expected():
    new_text, _ = migrate_v018(FIXTURES / "before.clio")
    expected = (FIXTURES / "expected_after.clio").read_text()
    assert new_text == expected
```

(Run + iterate; the migration logic and `expected_after.clio` fixture must agree.)

- [ ] **Step 5: Run + commit**

```bash
uv run pytest tests/test_doctor_migrate.py -v
git add clio/diagnostics.py clio/cli.py tests/test_doctor_migrate.py tests/fixtures/imports/migration_v017_to_v018/
git commit -m "$(cat <<'EOF'
feat(v0.18): clio doctor --migrate-v018 migration tool

Applies the v0.17 sibling-call heuristic to identify FLOWs that would
have been implicitly exposed for mcp-server targets, and proposes
prepending EXPOSE before FLOW and CONTRACT declarations. --write
applies the changes in place.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase C — Migration of existing fixtures + documentation

### Task 16: Migrate existing mcp-server fixtures + examples

**Files:**
- Audit + modify: all `.clio` fixtures under `tests/` and `examples/` that have `target: mcp-server`

- [ ] **Step 1: Find all affected files**

```bash
grep -lr "target: mcp-server" tests/ examples/ | sort -u
```

- [ ] **Step 2: For each, run the migration tool**

```bash
for f in $(grep -lr "target: mcp-server" tests/ examples/); do
    uv run python -m clio doctor "$f" --migrate-v018 --write
done
```

Inspect the diff with `git diff` and confirm each change is correct (the heuristic should match the expected behavior).

- [ ] **Step 3: Run full test suite**

```bash
uv run ruff check . --fix
uv run mypy
uv run pytest tests/ -v --tb=short
```

Expected: all ~1110 tests PASS. If any test depending on specific output names fails, fix the fixture or the test.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/ examples/
git commit -m "$(cat <<'EOF'
chore(v0.18): migrate mcp-server fixtures to explicit EXPOSE markers

Adds EXPOSE before FLOW and CONTRACT declarations in all .clio files
targeting mcp-server, matching the v0.17 sibling-call heuristic.
Applied via clio doctor --migrate-v018 --write.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: Documentation updates

**Files:**
- Modify: `docs/LANGUAGE_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/COMPILATION_TARGETS.md`, `docs/manual/02-tutorial.md`, `docs/manual/03-cookbook.md`, `docs/manual/06-troubleshooting.md`
- Create: `docs/manual/06-migration-v018.md`, `examples/multi_file/`
- Modify: `CHANGELOG.md`

This task has many sub-deliverables. Group them by file. Each `.md` change uses the patterns established in v0.17 documentation (look at `docs/manual/` for style).

- [ ] **Step 1: LANGUAGE_SPEC.md additions**

Add a new section after the FLOW composition section (around line 600), structured as:

```markdown
### IMPORT and EXPOSE (v0.18)

A `.clio` file may import FLOWs and CONTRACTs from other files via:

    FROM "<path>" IMPORT <name> [AS <alias>] [, ...]

The path is relative to the importing file's directory, must end in
`.clio`, and must start with `./` or `../`. Absolute paths are
rejected.

The visibility of a FLOW or CONTRACT is controlled by an optional
prefix:

    EXPOSE FLOW classify_article
      TAKES: ...

    INTERNAL FLOW _helper
      TAKES: ...

    FLOW also_internal     # absence of EXPOSE = INTERNAL

Only `EXPOSE`d symbols are importable. For `target: mcp-server`,
`EXPOSE FLOW`s on the entry file become MCP tools. For other targets,
the marker is informational (all symbols compile regardless).

Re-export: a file may import a symbol then re-`EXPOSE` it by name:

    FROM "./lib.clio" IMPORT classify
    EXPOSE classify       # re-exported through this file
```

Update the target-support table to include "IMPORT support" column.

- [ ] **Step 2: ARCHITECTURE.md additions**

Add section "Multi-file resolution pipeline":

```markdown
### Multi-file resolution (v0.18)

When the entry source contains `FROM ... IMPORT ...` declarations,
the compiler runs a resolver phase between parse and IR build:

1. Discover all reachable `.clio` files via recursive parsing,
   detecting cycles.
2. Validate per-file rules: EXPOSE FLOW has signature, name uniqueness.
3. Compute transitively exposed symbol sets (resolving re-exports
   in topological order).
4. Validate each IMPORT resolves to an exposed symbol in its source
   file.

The resulting `dict[Path, Program]` is flattened by the IR builder
into a single `FlowGraph` where internal (non-exposed) names are
alpha-renamed `{file_stem}__{name}` to avoid global collisions.
Emitters receive a `FlowGraph` of unchanged schema.
```

- [ ] **Step 3: COMPILATION_TARGETS.md updates**

Update the target table to include IMPORT support per target.

- [ ] **Step 4: docs/manual/02-tutorial.md additions**

Append a chapter "Splitting your code across files" with a worked example: a `schemas.clio` exposing `Article`, a `pipelines.clio` importing it and exposing `classify`, and a `main.clio` orchestrating both for `target: python`.

- [ ] **Step 5: docs/manual/03-cookbook.md additions**

Add two recipes: "Shared schemas across pipelines" and "Façade file (barrel-file pattern)" with concrete `.clio` snippets.

- [ ] **Step 6: docs/manual/06-troubleshooting.md additions**

Add entries for E_IMP_001 to E_IMP_005, E_RES_001 to E_RES_006, E_VIS_001 to E_VIS_005, E_MCP_001, E_CLI_001 with example error messages and fixes.

- [ ] **Step 7: Create docs/manual/06-migration-v018.md**

```markdown
# Migrating from CLIO v0.17 to v0.18

v0.18 introduces explicit `EXPOSE` / `INTERNAL` markers and cross-file
`IMPORT`. The single breaking change concerns `target: mcp-server`:
in v0.17, FLOWs were exposed implicitly when not called by a sibling;
in v0.18 you must mark them explicitly.

## Mechanical migration

Run for each file:

    clio doctor <file.clio> --migrate-v018 --write

This applies the v0.17 sibling-call heuristic and writes back the
modified file with `EXPOSE` prepended to the relevant declarations.

## Manual migration

If you prefer to migrate by hand:

1. For each `FLOW` not called by a sibling, prepend `EXPOSE`.
2. For each `CONTRACT` referenced in the signature of such a FLOW,
   prepend `EXPOSE`.

## Verification

After migration, run:

    clio check <file.clio>
    clio compile <file.clio> --target mcp-server --output ./out

Both must succeed with the same `server.py` output (in terms of
exposed tools) as before.

## Targets unaffected

`target: python`, `target: claude-skill`, `target: langgraph`,
`target: claude-cli` do not require migration. `EXPOSE` is
informational on these targets.
```

- [ ] **Step 8: Create examples/multi_file/**

`examples/multi_file/schemas.clio`:

```
EXPOSE CONTRACT Article
  SHAPE:
    title: str
    body:  str
```

`examples/multi_file/lib/nlp.clio`:

```
FROM "../schemas.clio" IMPORT Article

EXPOSE FLOW classify_article
  TAKES:
    article: Article
  GIVES:
    label: str
  - tokenize(text: article.body)
  - score(tokens: $1)

STEP tokenize
  MODE: exact
  IMPL: code
  LANG: python
  TAKES:
    text: str
  GIVES:
    tokens: List<str>
  CODE: |
    return {"tokens": text.split()}

STEP score
  MODE: judgment
  TAKES:
    tokens: List<str>
  GIVES:
    label: str
```

`examples/multi_file/main.clio`:

```
target: python
models:
  prefer: sonnet

FROM "./schemas.clio"  IMPORT Article
FROM "./lib/nlp.clio"  IMPORT classify_article

EXPOSE FLOW pipeline
  TAKES:
    article: Article
  GIVES:
    label: str
  - classify_article(article: article)
```

Test that this compiles:

    uv run python -m clio compile examples/multi_file/main.clio --target python --output /tmp/multi_out

- [ ] **Step 9: CHANGELOG.md `[Unreleased]` entry**

Add (or merge into existing `[Unreleased]`):

```markdown
## [Unreleased]

### Added
- Cross-file imports: new `FROM "<path>" IMPORT <name> [AS <alias>], ...`
  declaration enables sharing of `FLOW`s and `CONTRACT`s across `.clio`
  files. Paths are relative to the importing file, posix-style, with
  `.clio` extension. (#tbd)
- Explicit visibility markers: `EXPOSE` and `INTERNAL` may now prefix
  `FLOW` and `CONTRACT` declarations. The v0.17 sibling-call heuristic
  for `target: mcp-server` is replaced by explicit `EXPOSE` markers.
  (#tbd)
- Re-export support: a top-level `EXPOSE <name>` re-exports a
  previously-imported symbol. (#tbd)
- `clio doctor --migrate-v018 [--write]`: mechanical migration tool
  that applies the v0.17 heuristic and proposes/applies `EXPOSE`
  insertions. (#tbd)
- New multi-file example project under `examples/multi_file/`.

### Changed
- `target: mcp-server` now requires at least one `EXPOSE FLOW` in the
  entry file (E_MCP_001). Files relying on the v0.17 implicit exposure
  must be migrated.
- `target: claude-cli` rejects sources containing `FROM ... IMPORT ...`
  (E_CLI_001). Use `python`, `mcp-server`, `claude-skill`, or
  `langgraph` for multi-file projects, or inline the imported FLOWs.

### Migration
See `docs/manual/06-migration-v018.md` for the full migration guide.
```

- [ ] **Step 10: Commit docs**

```bash
git add docs/ examples/multi_file/ CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs(v0.18): IMPORT/EXPOSE language docs, manual, migration guide, examples

Adds LANGUAGE_SPEC sections for IMPORT and visibility markers;
ARCHITECTURE entry for multi-file resolution; updated target-support
table in COMPILATION_TARGETS; new tutorial chapter, cookbook recipes,
troubleshooting entries; full migration guide (06-migration-v018.md);
new multi-file example project under examples/multi_file/.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final checklist (before opening feature PR)

- [ ] All ~1110 tests green: `uv run pytest tests/ -v`
- [ ] Ruff clean: `uv run ruff check .`
- [ ] Mypy strict clean: `uv run mypy`
- [ ] CHANGELOG `[Unreleased]` populated
- [ ] All docs updated (LANGUAGE_SPEC, ARCHITECTURE, COMPILATION_TARGETS, manual/*, migration guide)
- [ ] `examples/multi_file/` compiles for both `python` and `mcp-server` targets
- [ ] No `.clio` fixture left unmigrated (`grep -lr "target: mcp-server" tests/ examples/` → all have `EXPOSE`)
- [ ] No partial-string Edit suspicion (`[[feedback_verify_partial_string_edits]]`) — re-read CHANGELOG and README after each Edit operation

Then push the feature branch and open the feature PR. **Do not** include version bumps in this PR — those go in a separate release-admin PR per `[[feedback_release_pr_separate]]`.

After feature PR merges, open the release-admin PR with:
- `pyproject.toml` 0.17.3 → 0.18.0
- `CHANGELOG` `[Unreleased]` → `[0.18.0] — YYYY-MM-DD`
- `README.md` badge + test count
- (and tag `v0.18.0` on the feature PR merge commit)
