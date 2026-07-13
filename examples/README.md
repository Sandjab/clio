# CLIO examples

This directory contains many compilable `.clio` files demonstrating distinct
use cases. The five documented sections below cover the main examples. For the
full set, run `ls examples/` — additional fixtures include
`critical_pipeline.clio` / `critical_pipeline_resume.clio` (resume-from-step
demo), `feedback_routing.clio`, `flow_composition.clio`, `flow_signature.clio`,
`mcp_tool.clio`, `parallel_classify.clio`, `rest_advanced.clio`,
`skill_minimal.clio`, `sql_demo.clio`, `mvp_go.clio` (Go target demo), and
`parallel_review.clio` (fan-out code review — the `claude-workflow` target demo,
where each `FOR EACH … PARALLEL` iteration runs as a concurrent subagent).
Multi-file examples live under [`multi_file/`](multi_file/).

## Project examples

Self-contained example projects live under [`projects/`](projects/). Each
project bundles its own `README.md`, `flow.clio`, input data, and the
**compiled `--target python` output committed alongside** -- so you can read
the result on GitHub before running anything.

- [`projects/01-iterative-refiner/`](projects/01-iterative-refiner/) -- writer + critic refine loop with bounded `WHILE ... MAX 3`. The first project example, and the first end-to-end use of `WHILE` plus per-step `invoke.model` in any CLIO example.

The flat `.clio` files below remain the right format for short, single-concept demos.

## 1. `mvp.clio` — customer churn detection

Pipeline: load customers from a CSV, detect churn risk via an LLM with a
typed contract and a bash heuristic fallback.

```bash
uv run python -m clio compile examples/mvp.clio --target claude-cli --output ./out
cp examples/customers.csv ./out/
# v0.1: edit out/steps/01_load_customers.py to replace the echo body with
# the CSV-parsing body shown in tests/fixtures/load_customers_real.py.
# v0.2: also edit out/steps/02_detect_churn_naive.py to a real heuristic
# (e.g. revenue < 1000 -> high; revenue < 10000 -> mid; else -> low).
bash ./out/run.sh
cat ./out/state.json
```

Requires `claude` (Claude Code CLI) authenticated, `python>=3.12`, and `jq`.

### Caching

The `detect_churn` step uses `CACHE: ttl(24h)`. The first run hits `claude -p`;
subsequent runs within 24 hours read from `out/.cache/detect_churn/<key>.json`
and do not invoke the API. To force a fresh call, `rm -rf out/.cache` or
override `CLIO_CACHE_DIR=/tmp/somewhere bash ./out/run.sh`.

### Resilience

If `claude -p --model haiku` produces a response that does not match the
contract, `detect_churn` retries up to 3 times. If still failing, it
escalates to `sonnet` (one attempt). If still failing, it falls back to
the heuristic `detect_churn_naive` step. If that fails too, the flow
aborts with an explicit message.

## 2. `entities.clio` — news entity extraction

Pipeline: load a plain-text article, extract typed named entities via an
LLM, then aggregate the entity list into a small summary.

What this example exercises that `mvp.clio` does not:

- Two CONTRACTs in the same flow (`entity`, `entity_summary`).
- Nested record types (`List<{kind: str, count: int}>`).
- Three steps (two EXACT, one JUDGMENT) chained linearly.
- A `confidence: float` field on each extracted entity.

```bash
uv run python -m clio compile examples/entities.clio --target claude-cli --output ./out
cp examples/article.txt ./out/
# Edit out/steps/01_load_article.py: read the file at args.file and assign
# its contents to `article`.
# Edit out/steps/03_summarize_entities.py: count entities, group by `kind`,
# pick top names by confidence.
bash ./out/run.sh
cat ./out/state.json
```

### Why two examples

`mvp.clio` proves the language handles a classic data-classification pipeline
with caching and resilience. `entities.clio` shows the same primitives (STEP,
CONTRACT, FLOW) handle a different domain (NER / extraction) with no language
change — only the contracts and step names differ. This is the central claim
of CLIO: one compiler, many use cases, no domain-specific runtime.

## 3. `classify_corpus.clio` — FOR EACH + OpenAI-compat (LiteLLM → Gemini)

Pipeline: load lines from a file, classify each line's sentiment via an
OpenAI-compatible endpoint (LiteLLM proxying to Gemini), with a typed
classification contract.

What this example exercises that the first two do not:

- `FOR EACH line IN lines:` — body executed once per element of the source
  list (loop variable `line` bound as a local kwarg).
- `invoke.protocol: openai` — Python emitter routes through the OpenAI SDK
  rather than the Anthropic SDK. With `base_url` pointing at a LiteLLM
  proxy, the actual model can be Gemini, OpenAI, Mistral, Together, Groq,
  Ollama or vLLM without any code change.
- A contract with `ASSERT` (`confidence > 0.0`) compiled into a Pydantic
  `@field_validator` in the emitted package.

This example compiles to `--target python` only. The `claude-cli` target rejects
it because its `FOR EACH` body contains a **judgment** step — `claude-cli` does
not implement `FOR EACH` with a judgment body. (`invoke.protocol: openai` is
accepted silently by `claude-cli`; it is not the reason for the rejection.)

```bash
uv run python -m clio compile examples/classify_corpus.clio --target python --output ./out

# Edit out/classify_corpus/steps/load_lines.py to read reviews.txt and return
# `[line.strip() for line in open(file).read().splitlines() if line.strip()]`.

# Start a LiteLLM proxy locally (or point base_url at any OpenAI-compatible
# endpoint), then:
export LITELLM_KEY=...
uv pip install ./out
classify_corpus
```

The emitted `out/pyproject.toml` declares `openai>=1.0` and `pydantic>=2` (no
`anthropic`, no `requests` — both omitted because no step needs them). The
emitted `flow.py` chains `load_lines` then `for line in state['lines']: classify(text=line)`.

### v0 limitation visible in this example

`FOR EACH` body call results are not yet accumulated into `state` — each
iteration's `classify` result is computed but not retained for downstream
aggregation. To aggregate, add a follow-up step after the loop that re-derives
the per-line results (e.g. read them back from a side-channel the step writes
to) or wait for the planned `FOR EACH x IN xs COLLECT y INTO ys:` syntax.

## 4. `rag_basic.clio` / `rag_selfcontained.clio` — RAG-like (LLM-as-retriever)

Pipeline: load corpus + question → score each chunk via an LLM (with reasoning) →
answer the question quoting cited chunk ids. No embeddings, no vector store —
the LLM is both retriever and generator.

What these examples exercise that the others do not:

- Three CONTRACTs in the same flow (`chunk`, `scored_chunk`, `rag_answer`).
- A numeric ASSERT (`score >= 0.0`) compiled into a Pydantic `@field_validator`.
  Note: the v0.1 expression parser supports a single comparator per ASSERT, so
  the upper bound (`score <= 1.0`) is not expressed at the contract level — the
  prompt is what asks the LLM to keep scores in [0, 1].
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
#   from pathlib import Path
#
#   def load_corpus(file: str) -> list[Chunk]:
#       paragraphs = Path(file).read_text().split("\n\n")
#       return [Chunk(id=i+1, text=p.strip()) for i, p in enumerate(paragraphs) if p.strip()]
#
# Edit ./out/rag_faq/steps/load_question.py:
#
#   from pathlib import Path
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
#  relative to the entrypoint's cwd. The trailing newline from `cat question.txt`
#  is included verbatim in the question string — fine for v0.4, can be stripped
#  in a future load step if needed.)
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

## 5. `ticket_routing.clio` — multi-field classification + parallel routing

Pipeline: load support tickets from JSON → classify each ticket in parallel into
a structured record (category, priority, team, urgency_score) → produce a
narrative routing summary.

What this example exercises that the others do not:

- **Multi-field structured judgment output** — `classify_ticket` returns a
  4-field `classified_ticket` (`category`, `priority`, `team`, `urgency_score`)
  in a single LLM call. Two of those fields are bounded `enum(...)`s compiled
  to `Literal[...]` in Pydantic; the float carries a numeric `ASSERT`.
- **`FOR EACH ... PARALLEL AS classifications`** — fans the classification step
  across the loaded tickets list. The collector binds the typed result list
  (`List<classified_ticket>`) into state, ready for the next step. PARALLEL
  here is the critical primitive: a sequential `FOR EACH` would not accumulate
  the per-iteration results (current v0 limitation).
- **Two-stage judgment** — a per-item judgment (`classify_ticket`) followed by
  a list-input judgment (`summarize_routing`). The summary is itself a CONTRACT
  with a `total > 0` ASSERT and a narrative field, so the LLM produces both
  counts and a short prose digest in one shot.
- **Zero manual edit** — like `rag_selfcontained.clio`, the loader uses
  `impl.shell` + `parse: json` against `tickets.json`, so the emitted package
  is runnable as-is once `tickets.json` is in cwd.

```bash
uv run python -m clio compile examples/ticket_routing.clio --target python --output ./out
cp examples/tickets.json ./out/
uv pip install ./out
cd ./out && ANTHROPIC_API_KEY=... ticket_routing
cat state.json
```

Compiles to `--target python` and `--target mcp-server`. The `claude-cli`
target rejects this file at compile time because it does not implement
`FOR EACH PARALLEL` (use one of the other two targets).
