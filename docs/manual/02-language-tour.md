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
- `rest` *(extended in v0.9)* — HTTP call (`method`, `url`, `response_path`, `query`/`headers` templating with `${var}` and `env:NAME`, 5 body forms incl. multipart and `@./file`, `retry: { backoff: exponential | constant }` honoring `Retry-After`).
- `mcp_tool` *(v0.10)* — call a tool exposed by an MCP server declared in `RESOURCES.mcp_servers`. Three transports: `stdio` / `sse` / `http`. Long-lived per-server clients on python + mcp-server; per-step bootstrap on claude-cli. `${var}` substitution in tool args, `parse: json|text`, `timeout`.
- `sql` *(v0.11)* — parameterized query against a database declared in `RESOURCES.databases` (drivers `sqlite` / `postgres` / `mysql`, lazy-imported). `:name` bindings auto-translated per driver via a small state machine that walks the query and skips single-quoted literals (with `''` escape), double-quoted identifiers, `--` line comments, `/* ... */` block comments, and the PostgreSQL `::cast` operator. Rows auto-mapped onto `GIVES` via `cursor.description`; DML returns `cursor.rowcount`. Multi-line queries use a `|` block scalar. Targets: python + mcp-server (claude-cli + langgraph rejected at compile time).

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
- **`IF report.confidence < 0.7: ... ELSE: ...`** *(v0.7, composed in v0.12)* — conditional branching on a contract sub-field. Since v0.12 multiple comparisons combine with the lowercase keywords `and` / `or` (Python precedence: `and` > `or`; parentheses override). No `not` yet — flip the comparator instead. ELSE optional on python/mcp-server, required on langgraph.
- **`MATCH classification.category: CASE bug: ... DEFAULT: ...`** *(v0.7)* — multi-way dispatch on an enum sub-field. CASE values must match enum variants; DEFAULT optional (recommended; required on langgraph).
- **`WHILE draft.score < 0.85 MAX 3: refine_draft(draft=draft)`** *(v0.7, composed in v0.12)* — bounded loop. MAX is mandatory; the loop exits when the condition turns false or after MAX iterations. Shares the IF grammar, so `and` / `or` work here too. Compiles to python/mcp-server only — langgraph rejects it (cyclic edges + state reducers planned for v0.8).

### Step calls

```
classify_ticket(ticket=t)        # keyword arg
score_chunks(corpus, question)   # positional shorthand: matches by TAKES name
load_text(file="input.txt")      # literal value
```

A step's `TAKES` parameters are passed by keyword; the literal forms (`"input.txt"`, integers, booleans) are inlined into the call site at compile time.

## RESCUE — multi-step failure handler (v0.8)

`RESCUE step_a:` declares a top-level handler that runs if `step_a` raises
after its `ON_FAIL` retry/escalate/fallback chain exhausts. Unlike
`ON_FAIL: abort(...)` (which is a single declarative clause), the RESCUE
body is a **chain of step calls** ending in `abort("message")`, so you can
notify, log, or otherwise side-effect before aborting:

```
STEP detect_churn
  ON_FAIL: retry(3) then escalate
  ...

FLOW pipeline
  load_csv(...) -> detect_churn(...) -> route(...)

  RESCUE detect_churn:
    -> notify_slack(channel="#alerts")
    -> abort("churn detection failed")
```

When `detect_churn` raises:
1. `ON_FAIL: retry(3) then escalate` exhausts itself.
2. The RESCUE body runs: `notify_slack` then `abort`.
3. `abort` raises `FlowAborted("...")`. The chain item after `detect_churn`
   (`route`) is skipped.

**One RESCUE per STEP**; the handler attaches to a STEP that appears in
the top-level FLOW chain (not nested inside FOR EACH / IF / MATCH /
WHILE). Compiles to **python** and **mcp-server**; **langgraph** and
**claude-cli** reject at compile time.

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
