# CLIO v0.18 — Cross-file `IMPORT` + `EXPOSE` / `INTERNAL` — design

**Date**: 2026-05-15
**Sprint**: candidate for **v0.18.0**
**Status**: Spec drafted, awaiting user review before writing the implementation plan.

## Motivation

CLIO today (v0.17.3) is a single-file language: a `.clio` source declares its `RESOURCES`, `CONTRACT`s, `STEP`s, and one or more `FLOW`s, and the compiler emits one project per source. FLOW composition (v0.17) made signed FLOWs callable as STEPs inside the *same* file, but cross-file reuse is impossible — schemas, pipelines, and utility FLOWs must be copied verbatim across every entry point that needs them.

This blocks three concrete patterns that real CLIO projects are about to hit:

1. **Shared domain schemas.** A `CONTRACT Article` used by three pipelines lives three times in three files, with three risks of drift.
2. **Reusable pipelines.** A `classify_article` FLOW polished for one project should be re-importable into another without copy-paste.
3. **API surface control.** When compiling to `target: mcp-server`, the current heuristic *"every FLOW not called by a sibling becomes a tool"* is implicit and load-bearing — users can't say *"these three FLOWs are my public API, the rest is internal"* explicitly.

`v0.18` introduces three new primitives that resolve all three: `IMPORT` for cross-file symbol resolution, `EXPOSE` for explicit public surface, and `INTERNAL` for explicit-by-choice privacy.

## Goals

- Allow a `.clio` file to import `FLOW`s and `CONTRACT`s from other `.clio` files via a Python-like `FROM "<path>" IMPORT <names>` syntax.
- Replace the v0.17 implicit "sibling heuristic" for `mcp-server` tool exposure with an explicit `EXPOSE` marker.
- Keep the IR (`FlowGraph`) schema *unchanged* — multi-file projects flatten into a single graph that the existing emitters see exactly as today.
- Ship a `clio doctor --migrate-v018` tool that converts v0.17 files mechanically.

## Non-goals (deferred to later releases)

- Importing `STEP`s, `RESOURCES`, or `TEST` blocks across files.
- A registry / package system (no `clio.toml`, no `FROM "github.com/..."`, no version pinning).
- Multi-`RESOURCES` resolution (the entry file remains the single source of truth for `target`, `models`, `mcp_servers`, `databases`).
- Multi-file output: each emitter still produces the same single-bundle output it produces today.
- Support for `IMPORT` in `target: claude-cli` (deferred — claude-cli stays single-file).

## Decisions made during brainstorm

| Topic | Decision | Rejected alternatives |
|---|---|---|
| **Granularity of imports** | `FLOW`s and `CONTRACT`s only | (a) FLOWs only — forces type duplication; (b) +STEPs — STEPs carry too much config (CACHE/ON_FAIL/MODELS) to import cleanly; (c) +RESOURCES — semantically conflicts with the "one target per compilation unit" invariant; (d) transitive auto-include of CONTRACTs referenced by exposed FLOWs — too magical, hides cross-file dependencies |
| **Default visibility** | `INTERNAL` by default, `EXPOSE` opt-in | (a) `EXPOSE` by default with `INTERNAL` opt-out — preserves v0.17 ergonomics but encourages accidental API leakage; (b) keep the v0.17 sibling heuristic and only allow override — implicit surface remains, the explicit primitive becomes second-class |
| **IMPORT syntax** | `FROM "<path>" IMPORT <name> [AS <alias>], ...` (Python-like, multi-symbol, optional alias) | (a) JS-style `IMPORT { X } FROM "..."` — introduces `{` `}` which the grammar doesn't have; (b) Namespace `IMPORT "..." AS prefix` then `prefix.X` — breaks the "one name = one symbol" invariant and introduces `.` as a symbol separator |
| **EXPOSE marker placement** | Co-localized prefix on the declaration: `EXPOSE FLOW <name>` / `EXPOSE CONTRACT <name>` | (a) Single top-level `EXPOSE:` block with `FLOWS:` / `CONTRACTS:` sub-sections — readable but introduces a name registry that drifts on rename; (b) Two separate top-level `EXPOSE FLOWS:` and `EXPOSE CONTRACTS:` blocks — verbose, non-homologous to existing grammar patterns; (c) Casing-based dispatch (uppercase = CONTRACT, lowercase = FLOW) — fragile, silent on typos, breaks if future versions add STEPs |
| **`INTERNAL` keyword** | Optional explicit marker (equivalent to absence of `EXPOSE`) | Reserve `INTERNAL` as a no-op token — users who want to document intent should be allowed to |
| **MODE refactor (modifier prefix on STEP)** | **Rejected — out of scope.** `MODE:` stays inside the STEP block | Refactoring `MODE` to `EXACT STEP` / `JUDGMENT STEP` prefix — `MODE` is a behavioral dimension (ternary, non-orthogonal), not a binary visibility modifier like `EXPOSE`. Breaking change disproportionate to v0.18 scope |
| **Output structure when imports are present** | Total flattening: all imported FLOWs/CONTRACTs are merged into a single `FlowGraph` with no provenance attribute exposed to emitters | (a) Preserve `origin: Path | None` per `FlowIR` so emitters can choose multi-file output (e.g. python target emitting `lib_nlp.py` separately) — pays a per-emitter decision for no immediate user value; (b) True multi-`FlowGraph` workspace (`Workspace { modules: dict[str, FlowGraph] }`) — massive churn across builder + 5 emitters + CLI + tests, justified only for a future package system |
| **Path resolution** | Relative to the source file's directory, posix separators, `.clio` extension required, absolute paths forbidden | (a) Implicit `.clio` extension (Python-style) — ambiguous with non-`.clio` neighbors; (b) Manifest-rooted (`clio.toml`-based) — introduces a new config concept inappropriate for v0.18 |
| **Name conflicts** | Strict: any conflict (local vs import, import vs import, FLOW vs CONTRACT with same name) is a build-time error; user resolves via `AS` alias | (a) Local-wins silent override — error-prone on refactors; (b) Last-import-wins silent — hides bugs |
| **Re-export** | **Allowed.** A file can `IMPORT X` then `EXPOSE FLOW X` (or `EXPOSE CONTRACT X`) to make `X` re-importable through itself. Resolution is transitive | Forbidden in v0.18, opened in v0.19+ — would have been simpler now but blocks the "façade / barrel-file" pattern that aligns CLIO with Python (`__all__`), TypeScript (`export from`), and Rust (`pub use`) |
| **Cycles** | Detected during the resolver's discovery phase, rejected with the full import chain quoted in the error | Tolerated if not effectively traversed — increases build complexity for no benefit |
| **Internal CONTRACT collision across files** | Internal (non-exposed) names alpha-renamed at flatten time using `{file_stem}__{name}` convention. User never sees the renamed form in source | (a) Forbid name collisions across files even for internals — over-restrictive; (b) Let collisions fail at IR build — confusing error messages |
| **RESOURCES / TEST in imported files** | Forbidden. Only the entry file may declare `target:`, `models:`, `RESOURCES`, `TEST` blocks | Allow per-file RESOURCES with merge semantics — semantically muddy, defers a real question to a later release |
| **Migration tooling** | `clio doctor --migrate-v018` produces a mechanical diff (and applies it with `--write`) using the v0.17 sibling heuristic | A `--compat-v017` runtime flag — doubles the builder's logical paths for transient benefit |
| **Backwards compatibility** | v0.18 is **not** backwards-compatible on `target: mcp-server`: files without `EXPOSE` produce a build-time error directing users to the migration guide | Silent fallback to v0.17 heuristic on missing EXPOSE — leaks the implicit behavior into the explicit version |

## Architecture

### Pipeline change

```
v0.17:  src.clio → parse() → Program AST → build_ir() → FlowGraph → emit()

v0.18:  main.clio → resolve_imports() → dict[Path, Program] → build_ir() → FlowGraph → emit()
                          │                                       │
                          │                                       └─ flatten + alpha-rename internals
                          │
                          └─ recursive parse + cycle detection + per-file validation +
                             transitive exposed-set computation
```

The `FlowGraph` schema is unchanged — emitters receive exactly the same shape as in v0.17, just with more declarations inside.

### New module `clio/ir/resolver.py` (~200 lines)

Four phases:

1. **Discovery**: recursive parse of all `.clio` files reachable from the entry, with a stack-based cycle detector.
2. **Per-file validation**: every `EXPOSE` reference points to a local decl; an exposed FLOW has `TAKES` and `GIVES`; no name appears twice as `EXPOSE FLOW` and `EXPOSE CONTRACT` in the same file.
3. **Exposed-set computation**: for each file, the set of *transitively* exposed symbols (re-export resolution via topological order over imports).
4. **Import validation**: every `FROM "..." IMPORT X` resolves to an `X` in the target file's exposed set.

### New AST nodes (`clio/parser/ast_nodes.py`)

```python
@dataclass(frozen=True)
class ImportItem:
    name: str
    alias: str | None
    line: int
    col: int

@dataclass(frozen=True)
class ImportDecl:
    path: str
    items: tuple[ImportItem, ...]
    line: int
    col: int
```

Existing decls (`FlowDecl`, `ContractDecl`) gain an `exposed: bool = False` field.

`Program` gains:
- `imports: tuple[ImportDecl, ...] = ()`
- `source_path: Path | None = None` (threaded by `cli.py`, used in error messages)

### IR / `build_ir` extension

The function's signature becomes polymorphic:

```python
def build_ir(
    parsed: dict[Path, Program] | Program,
    entry: Path | None = None,
    flow_name: str | None = None,
) -> FlowGraph: ...
```

Single-`Program` calls (the v0.17 test path) still work — they are wrapped to `{Path("<inline>"): program}` internally. No existing test breaks.

Alpha-renaming of internal symbols uses a deterministic rule:

```python
def _rename_internal(file_stem: str, original: str) -> str:
    # lib/nlp.clio :: _Tokenized  →  nlp__Tokenized
    return f"{file_stem}__{original}"
```

Exposed names keep their original form (already disambiguated by the Q7 conflict check).

### Per-emitter impact

| Emitter | IMPORT support | EXPOSE role | Code change |
|---|---|---|---|
| `python` | ✅ | informational | 0 (optional `_` prefix for renamed internals: ~5 lines) |
| `mcp-server` | ✅ | **structural** — only `EXPOSE FLOW`s become MCP tools | ~10 lines (replace heuristic derivation of `exposed_flow_names`) |
| `claude-skill` | ✅ | informational | 0 |
| `langgraph` | ✅ | informational | 0 |
| `claude-cli` | ❌ rejected | n/a — error if IMPORT present | ~5 lines |

The `_compute_exposed_flows` heuristic in `builder.py` is **deleted** — replaced by the explicit derivation `{f.name for f in entry_file_flows if f.exposed}`.

### Convention: order in `graph.flows` and `graph.contracts`

For golden-test stability, the flattened graph maintains:

1. The entry file's main FLOW first (selected by `--flow` or the unique candidate).
2. Other entry-file FLOWs in declaration order.
3. Imported files' FLOWs in topological order (leaves first).
4. Within each file, declaration order.

CONTRACTs follow the same rule, with the additional convention that exposed CONTRACTs precede internals at the file level.

## New grammar (LANGUAGE_SPEC.md additions)

```ebnf
program       := top_decl*
top_decl      := target_decl | resources_decl | models_decl
               | import_decl | contract_decl | step_decl | flow_decl | test_decl

import_decl   := "FROM" STRING_LIT "IMPORT" import_list NEWLINE
import_list   := import_item ("," import_item)*
import_item   := IDENT ("AS" IDENT)?

flow_decl     := visibility? "FLOW" ident NEWLINE INDENT flow_body DEDENT
contract_decl := visibility? "CONTRACT" ident NEWLINE INDENT contract_body DEDENT
visibility    := "EXPOSE" | "INTERNAL"
```

The four new keywords (`FROM`, `IMPORT`, `EXPOSE`, `INTERNAL`) are added to `clio/keywords.py`. The lexer requires no further change — it post-matches identifiers against the `Keyword` enum.

## Error catalogue

### Parse-time

| Code | Cause | Message |
|---|---|---|
| `E_IMP_001` | path does not start with `./` or `../` | `path must start with './' or '../'` |
| `E_IMP_002` | path does not end with `.clio` | `path must end with '.clio'` |
| `E_IMP_003` | empty IMPORT list | `expected at least one symbol after IMPORT` |
| `E_IMP_004` | empty alias | `expected identifier after AS` |
| `E_IMP_005` | duplicate symbol in same statement | `duplicate symbol 'X' in same IMPORT statement` |
| `E_VIS_001` | both `EXPOSE` and `INTERNAL` | `only one visibility marker allowed before FLOW/CONTRACT` |
| `E_VIS_002` | EXPOSE applied to non-FLOW / non-CONTRACT decl | `EXPOSE applies only to FLOW and CONTRACT (got STEP)` |

### IR-build-time

| Code | Cause | Message |
|---|---|---|
| `E_RES_001` | import cycle | `cyclic import: a.clio → b.clio → a.clio` |
| `E_RES_002` | file not found | `imported file not found: ./x.clio (from main.clio:3)` |
| `E_RES_003` | symbol not exposed by source file | `'X' is not exposed by "./a.clio"` |
| `E_RES_004` | symbol absent from source file | `'X' not found in "./a.clio"` |
| `E_RES_005` | same name imported twice | `'X' already imported from "./a.clio"; use AS to disambiguate` |
| `E_RES_006` | name clashes with local decl | `name 'X' clashes with import from "./a.clio"` |
| `E_VIS_003` | exposed FLOW without TAKES/GIVES | `exposed FLOW 'X' must declare explicit TAKES and GIVES` |
| `E_VIS_004` | same name exposed twice | `name 'X' is exposed as both FLOW and CONTRACT` |
| `E_VIS_005` | exposed FLOW references internal CONTRACT | `exposed FLOW 'X' references INTERNAL CONTRACT 'Y' — both must be exposed` |
| `E_MOD_001` | RESOURCES in imported file | `only the entry file may declare RESOURCES (found in ./lib/foo.clio:2)` |
| `E_MOD_002` | TEST in imported file | `only the entry file may declare TEST blocks (found in ./lib/foo.clio:50)` |
| `E_MCP_001` | mcp-server target with no EXPOSE | `target 'mcp-server' requires at least one EXPOSE FLOW in the entry file` |

### Emit-time

| Code | Cause | Message |
|---|---|---|
| `E_CLI_001` | claude-cli target with IMPORT present | `target 'claude-cli' does not support cross-file imports (deferred)` |

All error messages include `file:line:col` where applicable.

## Worked example

```
# schemas.clio
EXPOSE CONTRACT Article
  SHAPE:
    title: str
    body:  str

CONTRACT _Tokenized
  SHAPE:
    tokens: List<str>


# lib/nlp.clio
FROM "../schemas.clio" IMPORT Article

EXPOSE FLOW classify_article
  TAKES:
    article: Article
  GIVES:
    label: str
  - tokenize(text: article.body)
  - score(tokens: $1)
  - decide_label(score: $2)

STEP tokenize
  MODE: exact
  IMPL: code
  TAKES: ...
  GIVES: ...

STEP score
  MODE: judgment
  TAKES: ...
  GIVES: ...

STEP decide_label
  MODE: exact
  IMPL: code
  TAKES: ...
  GIVES: ...


# main.clio (entry point)
target: mcp-server
models:
  prefer: sonnet

FROM "./schemas.clio"  IMPORT Article
FROM "./lib/nlp.clio"  IMPORT classify_article

EXPOSE FLOW classify_pipeline
  TAKES:
    article: Article
  GIVES:
    label: str
  - classify_article(article: article)
  - emit_label(label: $1)

STEP emit_label
  MODE: exact
  IMPL: code
  ...
```

Compiled with `clio compile main.clio --target mcp-server --output ./server/`, the resulting `FlowGraph` contains:

- 1 exposed CONTRACT (`Article`), 1 internal CONTRACT (`nlp__Tokenized`)
- 4 STEPs (`nlp__tokenize`, `nlp__score`, `nlp__decide_label`, `emit_label`)
- 2 FLOWs (`classify_article`, `classify_pipeline`) — both exposed → both become MCP tools
- `exposed_flow_names == {"classify_article", "classify_pipeline"}`

## Migration v0.17 → v0.18

The single breaking change concerns `target: mcp-server`. Other targets are unaffected (EXPOSE is informational for them).

**Before (v0.17)**:
```
target: mcp-server
models: { ... }

CONTRACT Article
  SHAPE: ...

FLOW classify_article
  TAKES: { article: Article }
  GIVES: { label: str }
  - ...
```

**After (v0.18)**:
```
target: mcp-server
models: { ... }

EXPOSE CONTRACT Article          # added
  SHAPE: ...

EXPOSE FLOW classify_article     # added
  TAKES: { article: Article }
  GIVES: { label: str }
  - ...
```

Mechanical migration rule: any signed FLOW not called by a sibling becomes `EXPOSE FLOW`; any CONTRACT referenced by an exposed FLOW's signature becomes `EXPOSE CONTRACT`. This is the v0.17 sibling heuristic rephrased explicitly.

The migration tool implements this rule:

```
$ clio doctor analyse.clio --migrate-v018
file: analyse.clio (target: mcp-server)

Proposed changes (using v0.17 sibling-call heuristic):
  line 12: + EXPOSE  before CONTRACT Article
  line 28: + EXPOSE  before FLOW classify_article
  line 45: + EXPOSE  before FLOW retention_score

Internal (not exposed):
  line 60:           FLOW _normalize_text   (called by classify_article)

Run with --write to apply the changes.
```

## Test strategy

TDD-driven. Estimated layout:

| Category | New tests |
|---|---|
| Parser (`test_parser_imports.py`, `test_parser_visibility.py`) | ~30 |
| Resolver (`test_resolver.py`) | ~40 |
| Builder/IR (`test_ir_multifile.py`) | ~20 |
| Per-emitter multi-file fixtures (5 emitters × ~15) | ~75 |
| CLI extensions | ~10 |
| Migration tool (`test_doctor_migrate.py`) | ~10 |
| **Sub-total new tests** | **~185** |
| Preserved v0.17 tests | 924 |
| **Total v0.18** | **~1110** (+20%) |

Per-emitter regression covers:
- v0.17 single-file fixtures (unchanged behavior)
- v0.17 sub-flow fixtures (unchanged behavior)
- v0.18 simple multi-file (entry + lib)
- v0.18 diamond import (`a` imports `b` and `c`, both import `d`)
- v0.18 re-export (`facade.clio` re-exposes from `lib.clio`)
- v0.18 internal alpha-renaming visible in emitted output

## Documentation impact

| File | Action |
|---|---|
| `docs/LANGUAGE_SPEC.md` | + IMPORT section (~50 lines), + EXPOSE/INTERNAL section (~30 lines), + target-support table update |
| `docs/ARCHITECTURE.md` | + "Multi-file resolution pipeline" section |
| `docs/COMPILATION_TARGETS.md` | target table updated with IMPORT support per target |
| `docs/manual/02-tutorial.md` | + chapter "Splitting your code across files" |
| `docs/manual/03-cookbook.md` | + recipes: "shared schemas", "barrel-file façade" |
| `docs/manual/06-troubleshooting.md` | + entries for E_IMP_*, E_RES_*, E_VIS_*, E_MCP_001 |
| `docs/manual/06-migration-v018.md` | **new** — full migration guide |
| `CHANGELOG.md` | `[Unreleased]` entry detailed |
| `examples/multi_file/` | **new** directory — multi-file example project (entry + 2 libs) |
| `examples/` (existing mcp-server samples) | add `EXPOSE` to public FLOWs (official fixture migration) |

## Implementation plan (rough)

| Phase | Tasks | Sequencing |
|---|---|---|
| A | Parser + AST, Resolver, Builder/IR | strictly sequential (1 → 2 → 3) |
| B | 5 emitters in parallel + CLI + migration tool | parallelizable via `superpowers:subagent-driven-development` |
| C | Migrate existing fixtures, write docs | sequential after B |

Effort estimate: 10-14 calendar days, ~18-23 worker-days. To be refined in the implementation-plan phase.

## Release strategy

Per `[[feedback_release_pr_separate]]`, two PRs:

1. **PR feature v0.18**: all implementation (tasks A + B + C). No version bump.
2. **PR release-admin v0.18**: `pyproject.toml` 0.17.3 → 0.18.0, `CHANGELOG` `[Unreleased]` → `[0.18.0] — YYYY-MM-DD`, `README` badge + test count update.

Tag `v0.18.0` is placed on the **merge commit of the feature PR**, not the release-admin PR.

Pre-push checks at every push: `uv run ruff check . --fix`, `uv run mypy`, `uv run pytest tests/ -v` (per `[[feedback_run_ruff_before_push]]` and `[[feedback_run_mypy_before_push]]`).

## Risks

| Risk | Mitigation |
|---|---|
| Alpha-rename breaks emitters that hard-code internal FLOW/CONTRACT names | Per-emitter multi-file fixtures with explicit internal names exercise this |
| Complex cycles (4+ files, or via re-export) escape detection | Resolver tests cover cycles at depths 2, 3, 4, plus re-export cycles |
| Performance regression on large projects | `resolve_imports` caches by `Path.resolve()`; no recompilation expected on small projects (the entire `examples/` set is <50 files) |
| Migration tool produces an incorrect diff | Fixture `migration_v017_to_v018/before.clio` + `expected_after.clio` validates the tool's output |
| mcp-server fixtures not all identified, CI breaks on v0.18 build | Audit step before push: `grep -lr 'target: mcp-server' tests/` |

## Open questions

None at this stage. All design questions raised during brainstorm have been resolved (see "Decisions made during brainstorm" table). Implementation-level decisions (e.g. whether to add a Python `_` prefix to renamed internals in the python emitter) are deferred to the plan phase.

## Future work (not in v0.18)

- `EXPOSE STEP` (import utility steps cross-file)
- Per-file `RESOURCES` with merge semantics
- `clio.toml` project manifest + `--root` flag
- Package registry / remote imports (`FROM "github.com/..."`)
- `target: claude-cli` IMPORT support (inline the imported flows at emit)
- `FROM "..." IMPORT *` wildcard import (currently rejected by the grammar — every symbol must be named)
