# Language tour

CLIO has three primitives. Once you know them, the rest is decoration.

## STEP — atomic unit of work

A step has a name, typed inputs (`TAKES`), a typed output (`GIVES`), and a `MODE` declaring whether it runs as code or as an LLM call.

```
STEP classify_ticket
  TAKES: ticket: support_ticket
  GIVES: result: classified_ticket
  MODE:  judgment
```

### MODE

| Mode | What it means | When to use |
|---|---|---|
| `exact` | Deterministic — code, shell, REST call, anything with a name the compiler can reach | "Load this CSV", "Call this API", "Run this regex" |
| `judgment` | Stochastic — invoked via an LLM | "Summarise this", "Decide the category", "Score relevance" |
| `auto` | Compiler decides (parsed, **not yet implemented**) | Phase-2 feature; today acts like the explicit form |

**Rule of thumb:** if a senior engineer can name the function that does it, it's `exact`. If they'd say "ask the model", it's `judgment`.

### Implementation declarations

`exact` steps optionally take an `impl:` block describing *how* the code is reached:

```
STEP load_tickets
  TAKES: file:    str
  GIVES: tickets: List<support_ticket>
  MODE:  exact
  impl:
    mode:  shell
    cmd:   "cat ${file}"
    parse: json          # parse stdout as JSON before returning
```

Available `impl.mode` values today:

- `code` (default) — emits a Python stub you fill in.
- `shell` — argv-style subprocess. Add `parse: json` to JSON-decode stdout.
- `rest` — HTTP call (`method`, `url`, `response_path`).

`judgment` steps optionally take an `invoke:` block describing *which LLM* to call:

```
STEP classify
  TAKES: text:   str
  GIVES: result: classification
  MODE:  judgment
  invoke:
    mode:        api
    protocol:    openai
    base_url:    "http://localhost:4000"
    model:       "gemini-1.5-pro"
    auth:        "env:LITELLM_KEY"
    temperature: 0.0
```

If `invoke:` is omitted, the step uses **`invoke.cli`** (Claude Code) by default.

### Resilience

Two declarative knobs:

```
STEP detect_churn
  ...
  MODE:    judgment
  CACHE:   ttl(24h)
  ON_FAIL: retry(3) then escalate then abort("give up")
```

- **`CACHE: ttl(24h)`** — the compiler emits a cache layer that stores the response keyed by the step name + model + prompt. Subsequent calls within 24h hit the cache.
- **`ON_FAIL: retry(N) then escalate then ...`** — chain of fallback strategies. `escalate` on a `judgment` step bumps the model up the `RESOURCES.models` chain (e.g. haiku → sonnet → opus). `fallback(other_step)` substitutes another step. `abort("...")` raises with a message.

## CONTRACT — typed shape guarantee

A contract turns a stochastic LLM output into something deterministic code can compose with.

```
CONTRACT classified_ticket
  SHAPE:  {
    id:            int,
    category:      enum(bug|billing|feature|account|other),
    priority:      enum(low|medium|high|urgent),
    team:          enum(engineering|finance|product|support),
    urgency_score: float
  }
  ASSERT: 0.0 <= urgency_score <= 1.0
```

Compiles to a Pydantic v2 model. The compiler inlines this schema into the LLM prompt, validates the response, and reattempts on failure.

### Field types

- **Primitives:** `int`, `float`, `str`, `bool`
- **Constrained string:** `str(max=200)`
- **List:** `List<int>`, `List<chunk>`, `List<{k: str, v: int}>` (anonymous record)
- **Enum:** `enum(red|green|blue)` — compiles to `Literal[...]`
- **Contract reference:** any other contract name (`support_ticket`)

### ASSERT

A boolean expression on the fields. Supports:

- Comparisons: `== != < <= > >=`
- `len(field) > N` (string length / list length)
- **Chained comparators (since v0.6)**: `0.0 <= score <= 1.0` — desugars to `(0.0 <= score) and (score <= 1.0)` per Python semantics, left-associative for longer chains.

A single ASSERT must reference exactly one field (multi-field asserts are a planned future extension via `model_validator`).

## FLOW — composition

A flow is a directed graph of step calls.

```
FLOW ticket_routing
  load_tickets(file="tickets.json")
    -> FOR EACH t IN tickets PARALLEL AS classifications:
         classify_ticket(ticket=t)
    -> summarize_routing(classifications)
```

### Connectors

- **`->`** — sequential: the right-hand step starts when the left-hand finishes, and may reference its outputs.
- **`FOR EACH x IN xs:`** — sequential iteration. Today does not accumulate per-iteration results into state.
- **`FOR EACH x IN xs PARALLEL AS results:`** — fan a step across the collection in parallel, collect typed results into `state[results]`. Default cap = 10 concurrent. Single body step in v0.

### Step calls

```
classify_ticket(ticket=t)        # keyword arg
score_chunks(corpus, question)   # positional shorthand: matches by TAKES name
load_text(file="input.txt")      # literal value
```

A step's `TAKES` parameters are passed by keyword; the literal forms (`"input.txt"`, integers, booleans) are inlined into the call site at compile time.

## Putting it together

A complete minimal flow:

```
CONTRACT entity
  SHAPE:  {name: str(max=200), kind: enum(person|org|location|other), confidence: float}
  ASSERT: len(name) > 0

STEP load_article
  TAKES: file:    str
  GIVES: article: str
  MODE:  exact

STEP extract_entities
  TAKES:   article:  str
  GIVES:   entities: List<entity>
  MODE:    judgment
  CACHE:   ttl(7d)
  ON_FAIL: retry(3) then escalate then abort("entity extraction failed")

FLOW news_pipeline
  load_article(file="article.txt")
    -> extract_entities(article)
```

This is `examples/entities.clio` (with `summarize_entities` removed). Three primitives, twelve lines, runnable on three different targets.

Next: [the cookbook](03-cookbook.md) for common patterns built from these primitives.
