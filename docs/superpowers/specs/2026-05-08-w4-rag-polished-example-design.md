# W4 — RAG-like polished example + `impl.shell.parse: json` (Design)

Status: design approved, ready for implementation plan.
Date: 2026-05-08.
Addresses: POSITIONING.md W4 (short-term — "2-3 polished examples"). Adds `RAG-like flow` to the existing pair (entity extraction, ticket-classification adjacent via mvp).

## Goal

Ship a polished RAG-like example that demonstrates the **LLM-as-retriever** pattern (no embeddings, no vector store) and, in the process, close one v0.4 friction point that prevented examples from being truly compile-and-run: `impl.shell` returning stdout as `str` only.

Two `.clio` files are delivered side-by-side:

- `examples/rag_basic.clio` — same flow, default `MODE: exact` stubs for the two file loaders. Cohérent with `mvp.clio` / `entities.clio` / `classify_corpus.clio` (pattern manual-edit ~10 lines per loader).
- `examples/rag_selfcontained.clio` — same flow, but the two loaders use `impl.shell` (one with `parse: json`, one without). Compile-and-run: zero Python lines to fill in.

The flow itself (`load_corpus → load_question → score_chunks → answer`) is identical between the two variants — the difference is purely on the EXACT loader steps. This makes the README comparison clean: same primitives, two file-loading strategies, both legitimate.

The language extension `impl.shell.parse: json` is opt-in, backward-compatible (default `none` = current v0.4 behaviour), and motivated by this concrete example rather than by speculative breadth.

## Non-goals (v1)

- **`parse: yaml`, `parse: csv`, `parse: lines`**. YAGNI. We add `json` because this RAG example needs it; other formats can be added when a real example demands them.
- **Runtime parse-error recovery beyond what `ON_FAIL` already provides**. If `json.loads(stdout)` fails, the step raises `json.JSONDecodeError` and the existing retry/escalate/fallback path applies — no new error-handling surface.
- **claude-cli emitter changes**. The bash target's shell-step generation already returns stdout-as-string; adding `parse: json` to it would mean emitting `jq` shellouts and is out of scope for W4. The two new `.clio` files target `python`. (Compile-time validation: parser accepts `parse:` regardless of target; the IR carries it; only the python emitter and mcp_server emitter honour it. claude-cli emitter is allowed to ignore the field with no warning, consistent with how other python-only fields behave for now.)
- **Embeddings, vector stores, FAISS, sentence-transformers**. The whole point is LLM-as-retriever — adding embeddings would make the example heavier and dilute the pedagogical claim that CLIO needs no infra to demonstrate RAG. A future "rag_with_embeddings.clio" can come if/when impl.code with inline body lands.
- **A `select_top_k` exact intermediate step**. The `answer` judgment step receives `(question, scored, corpus)` and the LLM picks which scored chunks to ground on. Pedagogically simpler (4 steps, not 5) and avoids manual_edit on yet another exact step in `rag_basic`.
- **Updating mvp.clio / entities.clio / classify_corpus.clio to use `parse: json`**. Surgical change discipline — they work as documented; we don't retrofit.

## Part A — Language extension `impl.shell.parse: json`

### Surface

```
STEP load_corpus
  TAKES: file: str
  GIVES: corpus: List<chunk>
  MODE:  exact
  impl:
    mode:  shell
    cmd:   "cat ${file}"
    parse: json     # NEW. Default: none.
```

### Semantics

| `parse:` | Behaviour | Compile-time check |
|---|---|---|
| absent / `none` | stdout returned as `str` (v0.4 behaviour). Pydantic validation against `GIVES` runs as before — fails at runtime if `GIVES` is not `str`. | none added |
| `json` | `json.loads(stdout)` runs at the end of the step. The parsed object goes through the standard `GIVES` Pydantic validation path. | `GIVES` must be present (exact steps with no GIVES + parse:json is rejected at parse time) |

`json.JSONDecodeError` from `json.loads` propagates exactly like a `subprocess.CalledProcessError` would: the step raises, `ON_FAIL` (if any) decides, otherwise the flow aborts. No new error type, no try/except wrapping inside the emitted step.

### Modules touched

| Module | Change | Approx LOC |
|---|---|---|
| `clio/parser/parser.py` | Recognise `parse:` inside `impl: mode: shell`. Validate value ∈ {`none`, `json`}. | +15 |
| `clio/parser/ast_nodes.py` (or wherever shell impl AST node lives) | Add `parse: str` field to the shell-impl AST | +2 |
| `clio/ir/...` (`ShellImplIR`) | Add `parse: Literal["none", "json"] = "none"` field | +3 |
| `clio/emitters/_python_helpers.py:emit_shell_step` | If `parse == "json"`, emit `import json` + change `return result.stdout` to `return json.loads(result.stdout)` | +8 |
| `clio/emitters/mcp_server.py` | No change — already calls `emit_shell_step`, inherits new behaviour | 0 |
| `clio/emitters/claude_cli.py` | No change — `parse:` ignored silently for bash target | 0 |
| `docs/LANGUAGE_SPEC.md` | Update `impl.mode: shell` section + the v0 limitations table | +20 |
| `CHANGELOG.md` | New entry under `## Unreleased` → `### Language` | +5 |

### Tests

| File | New tests |
|---|---|
| `tests/test_parser.py` | `test_parse_impl_shell_with_parse_json` ; `test_parse_impl_shell_with_parse_none_explicit` ; `test_parse_impl_shell_parse_invalid_value_raises` (e.g. `parse: yaml` rejected with line number in error) |
| `tests/test_ir.py` | `test_build_ir_propagates_parse_json_to_shell_impl` ; `test_build_ir_default_parse_is_none` |
| `tests/test_emitters/test_python.py` (or wherever shell emitter tests live) | `test_emit_shell_step_with_parse_json_imports_json_and_calls_loads` ; `test_emit_shell_step_default_parse_returns_stdout_string` (regression-guard for v0.4 behaviour) |
| `tests/fixtures/expected/...` | Add an expected fixture for a tiny shell-with-parse-json `.clio` to lock byte-identical emit output |

Total expected: ~7-8 new tests, all unit-level. No new gated E2E tests for the language extension itself — the RAG example exercises it end-to-end as a bonus integration check.

### Backward compatibility

`parse:` is optional with default `none`. Every existing `.clio` file with `impl: mode: shell` continues to parse, build IR, emit, and run identically. The fixture suite (~371 unit tests) should remain green without modification. We add new tests, we do not modify existing ones.

## Part B — RAG-like flow (.clio common shape)

```
CONTRACT chunk
  SHAPE:  {id: int, text: str(max=1000)}
  ASSERT: id >= 1

CONTRACT scored_chunk
  SHAPE:  {id: int, score: float, reason: str(max=200)}
  ASSERT: 0.0 <= score <= 1.0

CONTRACT rag_answer
  SHAPE:  {answer: str(max=2000), citations: List<int>}
  ASSERT: len(answer) > 0

STEP load_corpus
  TAKES: file:   str
  GIVES: corpus: List<chunk>
  MODE:  exact
  # variant-specific impl below

STEP load_question
  TAKES: file:     str
  GIVES: question: str
  MODE:  exact
  # variant-specific impl below

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
  load_corpus(file="faq.txt")          # rag_basic — file is faq.json in selfcontained
    -> load_question(file="question.txt")
    -> score_chunks(corpus, question)
    -> answer(question, scored, corpus)

RESOURCES
  target: python
  models: [haiku, sonnet, opus]
```

### Design rationale

- **3 CONTRACTs** (vs 2 in entities, 1 in mvp/classify_corpus): real nested types, a numeric `ASSERT` (`0.0 <= score <= 1.0`) that compiles to a Pydantic `@field_validator` — pedagogically distinct from existing `len(...) > 0` asserts.
- **`citations: List<int>`** in the final answer: forces the LLM to ground its response in source ids, demonstrating typed list-of-ints output and downstream traceability.
- **Multi-input judgment steps** (`score_chunks(corpus, question)`, `answer(question, scored, corpus)`): demonstrates that `TAKES` accepts multiple state-derived references in the FLOW expression. *To be confirmed in implementation* — if the parser/IR currently only supports single-arg call expressions in FLOW, the plan adds that support as a prerequisite (estimated +20-40 LOC parser + tests).
- **No `select_top_k` exact step**: the answer judgment receives `(question, scored, corpus)` and the LLM picks the relevant chunks itself. Cleaner pipeline; one less manual_edit point in rag_basic; no compromise on demonstrability since the scoring step already shows the typed-output pattern.
- **`CACHE: ttl(7d)` on `score_chunks`** : longer than `entities.clio` (7d there too) and `mvp.clio` (24h) — reasonable for a stable corpus where the question changes more often than the source. The `answer` step has no CACHE because the question varies more freely.
- **`ON_FAIL` distinct on each judgment**: `score_chunks` retries 3 + escalate (it's the costly call); `answer` retries only 2 + escalate (smaller payload, faster recovery). Demonstrates per-step tuning.

## Part C — `examples/rag_basic.clio`

Identical to the common shape above. `load_corpus` and `load_question` use the default (no `impl:` block) → emitter generates Python stubs the user fills in.

The README provides the two ~10-line bodies (corpus parser + cat-question) so the example is reproducible without the user having to invent them.

## Part D — `examples/rag_selfcontained.clio`

Identical to the common shape above except the two loaders carry `impl:` blocks:

```
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
```

After `python -m clio compile examples/rag_selfcontained.clio --target python --output ./out`, no Python file under `./out/rag_faq/steps/` requires editing. `cp examples/faq.json examples/question.txt ./out/ && uv pip install ./out && rag_faq` runs the full flow.

The `load_corpus` step uses `parse: json` because `faq.json` ships as a JSON array matching `List<chunk>`. The `load_question` step does not use `parse:` because `question.txt` is plain text and matches `str` natively.

## Part E — Data files

### `examples/faq.txt` (for rag_basic)

8 paragraphs, separated by blank lines. Each paragraph is a `Q: ... R: ...` pair on customer-support topics: cancellation, refunds, plan changes, billing, support contact, security, GDPR data export, team seats. ~80-100 lines total.

The README's manual-edit `load_corpus.py` body (provided ready-to-paste, ~12 lines) reads the file, splits by `\n\n`, and emits `[{id: i+1, text: p.strip()} for i, p in enumerate(paragraphs) if p.strip()]`.

### `examples/faq.json` (for rag_selfcontained)

Same 8 paragraphs, pre-chunked as a JSON array:

```json
[
  {"id": 1, "text": "Q: Comment annuler mon abonnement ? R: Pour annuler..."},
  {"id": 2, "text": "Q: Quel est le délai de remboursement ? R: ..."},
  ...
]
```

Both files ship in `examples/` and are copied into `./out/` by the user before running. Neither is regenerated automatically — they are committed as static fixtures. Keeping them in sync (faq.txt vs faq.json) is a maintenance burden we accept (small file, rarely changes); a `make examples` target could regenerate later but is out of scope.

### `examples/question.txt` (shared)

Single line, deliberately spanning two FAQ entries (cancellation **and** refund) so the scored output legitimately covers two ids and the answer's `citations` list has length ≥ 2. This makes the demo more visibly "RAG-like" — single-paragraph answers wouldn't show off the pattern.

```
Comment annuler mon abonnement et obtenir un remboursement ?
```

## Part F — README section

A 4th section is appended to `examples/README.md` (preserving the existing 3). Structure:

1. Pattern description (LLM-as-retriever, no embeddings).
2. What the example exercises that the others don't (3 contracts, numeric ASSERT, multi-input steps, `List<int>` citations).
3. Two-variant comparison table (load strategy, manual edit needed).
4. Run instructions for each variant (compile command, file copies, install, run).
5. "Why two variants" subsection: when to prefer rag_basic (format conversion needed) vs rag_selfcontained (file format already matches contract shape).

Total addition: ~80-100 lines of markdown. Existing 3 sections remain untouched.

## Implementation order

1. **Language extension first** (Part A): parser + IR + emitter + tests, all green.
2. **rag_basic.clio + faq.txt + question.txt** (Parts B, C, E partial): authoring + compile-check via `python -m clio check` + compile output inspection. No new tests beyond what already exists for the language.
3. **rag_selfcontained.clio + faq.json** (Parts B, D, E partial): authoring + compile-check + compile-and-run smoke (1 gated E2E if cheap, otherwise documented run command).
4. **README** (Part F): authoring last so it can reference the actually-emitted output.
5. **CHANGELOG + LANGUAGE_SPEC**: documented after the implementation lands.

This ordering means the language extension lands in its own commits before the example consumes it — useful if we later want to use `parse: json` from another future example without retroactively rebasing.

## Test plan

| Layer | What | Where | Count |
|---|---|---|---|
| Unit | `parse: json` parser/IR/emitter | `tests/test_parser.py`, `tests/test_ir.py`, `tests/test_emitters/test_python.py` | ~7-8 new |
| Unit | Multi-input call-expression in FLOW (if not already supported) | `tests/test_parser.py` | ~2-3 new |
| Fixture | A minimal `.clio` exercising `parse: json`, locked to expected emit output | `tests/fixtures/` + expected output | 1 new fixture pair |
| Smoke | Compile both rag_*.clio successfully | `tests/test_examples_compile.py` (extend if exists, otherwise new) | 2 new |
| Optional gated | Run rag_selfcontained end-to-end with stub Anthropic call (mocked) | `tests/test_e2e_*.py` (extend) | 1 if straightforward |

Suite stays at 371 + ~10-12 new = ~382 unit tests. No regressions expected.

## Open questions

1. **Multi-input FLOW expressions**: does the current parser accept `score_chunks(corpus, question)` and `answer(question, scored, corpus)` directly, or do we need `score_chunks(corpus=corpus, question=question)` keyword-form? Verify in plan task 1; if not, add support (estimated small).
2. **Where exactly is `ShellImplIR` defined?** Listed at `clio/ir/...` above — confirm precise path before plan write-up. (`grep -rn "class ShellImplIR" clio/` from plan first task.)
3. **Existing test for `test_examples_compile`**: confirm whether `tests/test_examples_compile.py` exists or whether compile-checks for the existing 4 examples live elsewhere. The plan picks the existing convention.
4. **Pydantic validator for `0.0 <= score <= 1.0`**: classify_corpus.clio uses `confidence > 0.0` — confirm the codepath compiles `<=` correctly (not just `<` and `>`). Should be straightforward, but verify in plan task 1.
5. **Working directory at runtime for `cat ${file}`**: the rag_selfcontained variant relies on `cat ${file}` with a relative path. Confirm whether the emitted entrypoint (`__main__.py`) runs from `cwd = ./out/` or from wherever the user invokes it. If the latter, document the run instructions to `cd ./out && rag_faq` rather than `rag_faq` from outside. (No code change expected — just README accuracy.)

## Acceptance criteria

- `pytest tests/ -v` passes 100% (no regressions, ~10-12 new tests green).
- `python -m clio check examples/rag_basic.clio` and `... rag_selfcontained.clio` both pass.
- `python -m clio compile examples/rag_selfcontained.clio --target python --output /tmp/rag_out` produces a project where `grep -r "TODO\|raise NotImplementedError" /tmp/rag_out/rag_faq/steps/` returns nothing in `load_corpus.py` or `load_question.py`.
- The compiled rag_selfcontained, with `cp examples/faq.json examples/question.txt /tmp/rag_out/` and an Anthropic key, runs end-to-end and writes a `state.json` with a non-empty `response.answer` and `response.citations`.
- `examples/README.md` has a new 4th section ≤120 lines, valid markdown.
- `docs/LANGUAGE_SPEC.md` has an updated `impl.mode: shell` section documenting `parse:`.
- `CHANGELOG.md` `## Unreleased` has a `### Language` entry for `parse: json` and an `### Examples` entry for the two new files.
