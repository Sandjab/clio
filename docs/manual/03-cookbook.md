# Cookbook

Recipes for patterns that come up repeatedly. Each one references a runnable example in `examples/` so you can compile, run, and adapt.

## 1. Single-record classification with retry chain

**Pattern:** load one record, classify it via an LLM, fall back to a heuristic if the LLM fails repeatedly.

**Reference:** [`examples/mvp.clio`](../../examples/mvp.clio)

```
STEP detect_churn
  TAKES:   customers: List<{name: str, revenue: float}>
  GIVES:   risks:     List<customer_risk>
  MODE:    judgment
  CACHE:   ttl(24h)
  ON_FAIL: retry(3) then escalate then fallback(detect_churn_naive) then abort("nope")

STEP detect_churn_naive
  TAKES: customers: List<{name: str, revenue: float}>
  GIVES: risks:     List<customer_risk>
  MODE:  exact         # a Python heuristic the user fills in
```

Key idea: `fallback(other_step)` substitutes a deterministic step when the LLM keeps failing. The `other_step` must have the same `GIVES` type.

## 2. List-input classification with PARALLEL

**Pattern:** classify each item of a list independently, collect typed results.

**Reference:** [`examples/parallel_classify.clio`](../../examples/parallel_classify.clio)

```
STEP classify
  TAKES: text:  str
  GIVES: label: str
  MODE:  judgment

FLOW pipe
  load_corpus()
    -> FOR EACH doc IN docs PARALLEL AS labels:
         classify(text=doc)
    -> aggregate(labels=labels)
```

Key idea: `FOR EACH ... PARALLEL AS results` is the only loop today that **accumulates** results into state. Sequential `FOR EACH` is fire-and-forget. Default cap is 10 concurrent calls.

## 3. Multi-field structured judgment + summary digest

**Pattern:** each item is classified into a multi-field record (category, priority, team, etc.), then a separate JUDGMENT step turns the typed list into a narrative digest.

**Reference:** [`examples/ticket_routing.clio`](../../examples/ticket_routing.clio)

```
CONTRACT classified_ticket
  SHAPE: {id: int, category: enum(bug|billing|...), priority: enum(low|...|urgent),
          team: enum(...), urgency_score: float}
  ASSERT: 0.0 <= urgency_score <= 1.0

STEP classify_ticket
  TAKES:   ticket: support_ticket
  GIVES:   result: classified_ticket
  MODE:    judgment

STEP summarize_routing
  TAKES: classifications: List<classified_ticket>
  GIVES: summary:         routing_summary
  MODE:  judgment

FLOW ticket_routing
  load_tickets(file="tickets.json")
    -> FOR EACH t IN tickets PARALLEL AS classifications:
         classify_ticket(ticket=t)
    -> summarize_routing(classifications)
```

Key idea: a single LLM call per item produces a multi-field structured output (4 fields here) — much denser than calling the model once per field. The summary step is also `judgment`, demonstrating LLM-as-aggregator.

## 4. RAG without embeddings

**Pattern:** load a corpus, score each chunk against the question via an LLM-as-retriever, then answer using only the highest-scoring chunks. No embeddings, no vector store.

**Reference:** [`examples/rag_basic.clio`](../../examples/rag_basic.clio) and [`examples/rag_selfcontained.clio`](../../examples/rag_selfcontained.clio)

```
STEP score_chunks
  TAKES:   corpus: List<chunk>, question: str
  GIVES:   scored: List<scored_chunk>
  MODE:    judgment
  CACHE:   ttl(7d)

STEP answer
  TAKES: question: str, scored: List<scored_chunk>, corpus: List<chunk>
  GIVES: response: rag_answer
  MODE:  judgment

FLOW rag_faq
  load_corpus(file="faq.json")
    -> load_question(file="question.txt")
    -> score_chunks(corpus, question)
    -> answer(question, scored, corpus)
```

Key idea: `multi-input judgment` steps. `score_chunks(corpus, question)` and `answer(question, scored, corpus)` both reference multiple upstream outputs. The `rag_answer` contract enforces `citations: List<int>` so the LLM must ground its answer in source IDs.

## 5. Zero-edit data ingestion with `parse: json`

**Pattern:** when your input file is already in the shape your contract expects, skip the manual loader.

**Reference:** [`examples/rag_selfcontained.clio`](../../examples/rag_selfcontained.clio), [`examples/ticket_routing.clio`](../../examples/ticket_routing.clio)

```
STEP load_tickets
  TAKES: file:    str
  GIVES: tickets: List<support_ticket>
  MODE:  exact
  impl:
    mode:  shell
    cmd:   "cat ${file}"
    parse: json
```

Key idea: `parse: json` runs `json.loads(stdout)` before returning. With a `tickets.json` already containing `List<{id,title,body}>`, no manual Python is needed — the emitted `load_tickets.py` is runnable as-is.

Without `parse: json`, the stdout is returned as `str`. For a CSV, you'd need `MODE: exact` (no impl) and fill the Python stub.

## 6. Calling a non-Anthropic model via OpenAI compat

**Pattern:** point `judgment` steps at any OpenAI-compatible endpoint (LiteLLM, OpenRouter, Ollama, vLLM, Together, Groq).

**Reference:** [`examples/classify_corpus.clio`](../../examples/classify_corpus.clio)

```
STEP classify
  TAKES: text:   str
  GIVES: result: classification
  MODE:  judgment
  invoke:
    mode:        api
    protocol:    openai
    base_url:    "http://localhost:4000"      # LiteLLM proxy
    model:       "gemini-1.5-pro"
    auth:        "env:LITELLM_KEY"
    temperature: 0.0
```

Key idea: `protocol: openai` + `base_url` decouples the **wire protocol** (OpenAI Chat Completions API) from the **actual model** behind the endpoint. The Python emitter adds `openai>=1.0` to `pyproject.toml` only when needed.

## 7. Numeric range constraints with chained ASSERT

**Pattern:** lock a float into a valid range at the contract level (since v0.6).

```
CONTRACT scored_chunk
  SHAPE:  {id: int, score: float, reason: str(max=200)}
  ASSERT: 0.0 <= score <= 1.0
```

Key idea: chained comparators desugar to a left-associative `(a<=b) and (b<=c)`. The Pydantic field validator runs at every `model_validate` call, so a model returning `score: 1.5` triggers a `ValidationError` and the step's `ON_FAIL` chain kicks in (typically `retry`).

Single-field constraint only — for cross-field invariants like `created_at < updated_at`, see *future plans* in the spec.

## 8. Conditional routing with IF / MATCH

**Pattern:** moderate input, then either escalate (unsafe) or classify and dispatch by category to one of N specialised steps.

**Reference:** [`examples/feedback_routing.clio`](../../examples/feedback_routing.clio)

```
FLOW feedback_routing
    load_feedback(file="feedback.json")
    -> moderate_text(feedback=feedback)
    -> IF moderation.safe == true:
        classify_safe_text(feedback=feedback)
        -> MATCH classification.category:
            CASE bug:     route_bug(classification=classification)
            CASE feature: route_feature(classification=classification)
            CASE praise:  route_praise(classification=classification)
            DEFAULT:      route_general(classification=classification)
    ELSE:
        escalate_unsafe(feedback=feedback)
```

Key idea: `IF <state_field>.<sub_field> <op> <literal>` reads a contract sub-field; the state_field must be a CONTRACT (so it has nested fields). `MATCH` does multi-way dispatch on an enum sub-field; CASE values must match enum variants exactly. ELSE is optional in python/mcp-server, **required** in langgraph; same for DEFAULT.

`true` / `false` are recognised as bool literals on the right-hand side of a comparison.

## 9. Bounded refine loop with WHILE MAX

**Pattern:** generate a draft, iteratively refine it until a quality threshold is reached or a max iteration count is hit.

```
WHILE draft.score < 0.85 MAX 3:
    refine_draft(draft=draft)
```

Key idea: `MAX <int>` is **mandatory** — bounds the loop to keep LLM-driven flows terminating. The body must update the state field referenced by the condition (typically by writing back the same `gives.name`) so the loop can make progress. Compiles to python and mcp-server only — langgraph rejects `WHILE` in v0.7 (cyclic edges + state reducers planned for v0.8).

## 10. Critical LLM pipeline with ON_FAIL × RESCUE

For pipelines whose key STEP is a judgment (LLM) call, you usually want:

1. Auto-retry on transient failures (network, rate-limit, glitchy LLM).
2. Escalate to a more capable model if all retries fail.
3. As a last resort, notify a human and abort with a contextual message.

ON_FAIL handles (1) and (2) declaratively; RESCUE handles (3) procedurally:

```
STEP detect_churn
  TAKES:    rows: List<int>
  GIVES:    risks: List<{client: str, score: float}>
  MODE:     judgment
  ON_FAIL:  retry(3) then escalate

STEP notify_slack
  TAKES:    channel: str, reason: str
  GIVES:    sent: bool
  MODE:     exact

FLOW pipeline
  load_csv(path="data.csv") -> detect_churn(rows=rows)

  RESCUE detect_churn:
    -> notify_slack(channel="#alerts", reason="churn detection failed")
    -> abort("churn detection failed — see #alerts")
```

The runtime sequence:
- `load_csv` runs.
- `detect_churn` runs. If it raises:
  - `retry(3)` retries up to 3 times.
  - `escalate` switches to a more capable model.
  - If both exhaust, the `RESCUE` body runs: `notify_slack` then `abort`.
- The chain after `detect_churn` is skipped.

For a complete working example, see [`examples/critical_pipeline.clio`](../../examples/critical_pipeline.clio).

Key idea: ON_FAIL declares **what to try** (retry/escalate/fallback);
RESCUE declares **what to do once the tries are spent** (notify, log,
clean up, then abort). The two compose: ON_FAIL runs first, RESCUE runs
only on exhaustion. Compiles to python and mcp-server; langgraph and
claude-cli reject at compile time.

## 11. REST API integration with auth, retries, and file upload

You want to call an external HTTP API from a CLIO step. Common needs:
templated query parameters, an env-resolved bearer token in the headers,
a structured JSON body with values pulled from `TAKES`, automatic retry
on transient 5xx / 429 / network errors, and the ability to upload a
binary file via multipart.

```
CONTRACT geo_point
  SHAPE: {lat: float, lng: float}

# GET with templated query, env-resolved API key, exponential retry.
STEP geocode
  TAKES: address: str
  GIVES: location: geo_point
  MODE:  exact
  impl:
    mode:           rest
    method:         GET
    url:            "https://maps.googleapis.com/maps/api/geocode/json"
    query:          {address: "${address}", key: "env:GOOGLE_MAPS_KEY"}
    headers:        {Accept: "application/json"}
    response_path:  "results[0].geometry.location"
    timeout:        30s
    retry:          {attempts: 3, on: ["5xx", "429", "timeout"]}

# POST with JSON body — values templated, bool literal accepted inline.
STEP create_user
  TAKES: name: str, email: str
  GIVES: id: str
  MODE:  exact
  impl:
    mode:    rest
    method:  POST
    url:     "https://api.example.com/v1/users"
    headers: {Authorization: "env:AUTH_HEADER", "Content-Type": "application/json"}
    body:    {name: "${name}", email: "${email}", active: true}
    response_path: "id"

# POST with multipart body — text fields + binary file part.
STEP upload_cv
  TAKES: label: str
  GIVES: r: str
  MODE:  exact
  impl:
    mode:   rest
    method: POST
    url:    "https://api.example.com/v1/uploads"
    body:   {multipart: {label: "${label}", file: "@./cv.pdf"}}
    response_path: "id"
```

What's going on:

- **Templating**. Inside any string value (`url`, dict values, `body`),
  `${var}` is substituted at runtime from the step's `TAKES`. A value
  whose **whole** content is `env:NAME` reads `os.environ[NAME]` instead
  — that's how `env:GOOGLE_MAPS_KEY` becomes the actual API key without
  it appearing in the source.
- **Headers with non-identifier characters**. `Content-Type` contains a
  hyphen, which isn't a valid bareword key, so it's quoted:
  `{"Content-Type": "application/json"}`.
- **Body forms**. `body: {dict}` ⇒ `application/json`; `body: "raw text"`
  ⇒ `text/plain`; `body: "@./payload.json"` ⇒ file content with the
  Content-Type inferred from the extension; `body: {form: {...}}` ⇒
  `application/x-www-form-urlencoded`; `body: {multipart: {...}}` ⇒
  `multipart/form-data`, where any value starting with `@` becomes a
  binary file part. You cannot combine `form` and `multipart`.
- **Retry**. `retry: {attempts: 3}` is the minimal form; the rest of
  the policy uses documented defaults (exponential backoff, 0.1s base,
  30s cap, retry on `5xx` / `429` / `timeout`). Add `"network"` to `on`
  to also retry on connection errors. The `Retry-After` response header
  is honored when present.
- The legacy `retries: 3` scalar is **rejected at parse time** in v0.9
  with a migration hint; if you want retries, write `retry: {attempts: 3}`.

Compiles to all three exact-supporting targets (`python`,
`mcp-server`, `claude-cli`) — each emits the same kwargs construction
and the same retry loop, with `clio_runtime/rest.py` bundled into the
output for the templating + retry helpers.

## What's not in the cookbook (yet)

- **Multi-field ASSERT** — accept `a > b` between two fields. Specced, planned.
- **Boolean `and`/`or` keywords in ASSERT and conditions** — natural extension of the chained-comparator desugaring.
- **`auto` MODE routing** — parsed, runtime decision not yet implemented.
- **`.FAILS` postfix in IF conditions** — specced for failure-aware branching; for a multi-step failure handler today, see [recipe #10](#10-critical-llm-pipeline-with-on_fail--rescue).

When these land, this page gets new recipes. (See [the changelog](../../CHANGELOG.md) for what's recently moved out of "not yet".)

Next: [targets](04-targets.md) for choosing where to compile.
