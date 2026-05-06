# CLIO examples

Three compilable `.clio` files demonstrate distinct use cases. The first two
compile to both targets (`claude-cli` and `python`); the third targets
`python` only because it uses `invoke.protocol: openai` (LiteLLM bridge) which
the `claude-cli` target does not implement.

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
