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
| ~~`auto`~~ | Not implemented — rejected at parse time | Phase-2 placeholder; use `exact` or `judgment` |

**Rule of thumb:** if a senior engineer can name the function that does it, it's `exact`. If they'd say "ask the model", it's `judgment`.

### DESCRIPTION and STRATEGIES (v0.15)

Two optional free-text fields carry author intent into the prompt the
model sees, without touching the JSON-only output contract:

```
STEP score_risk
  DESCRIPTION: "Score churn risk on a customer cohort"
  STRATEGIES: |
    - prefer high-recency signals over volume
    - tie-break on open tickets in the last 30 days
    - never promote a customer with no signal to "high"
  TAKES: rows: List<{name: str, ca: float}>
  GIVES: risks: List<{name: str, level: str}>
  MODE:  judgment
```

The python emitter appends `Step intent: …` and `Heuristics: …` to the
step's `_SYSTEM_PROMPT`. When neither field is set, the emitter output
is byte-identical to v0.14. Both fields accept a single-line `"..."`
string or a `|` block scalar.

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

If `invoke:` is omitted, the step uses the target's default: the `python` target calls the Anthropic SDK directly; `invoke.mode: cli` (Claude Code subprocess) is the default only for `--target claude-cli`.

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
  SHAPE: {id: int, category: enum(bug|billing|feature|account|other), priority: enum(low|medium|high|urgent), team: enum(engineering|finance|product|support), urgency_score: float}
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
- **`FOR EACH x IN xs PARALLEL AS results:`** — fan a step across the collection in parallel, collect typed results into `state[results]`. Default cap = 10 concurrent. Single body step in v1.
- **`IF report.confidence < 0.7: ... ELSE: ...`** *(v0.7, composed in v0.12)* — conditional branching on a contract sub-field. Since v0.12 multiple comparisons combine with the lowercase keywords `and` / `or` (Python precedence: `and` > `or`; parentheses override). No `not` yet — flip the comparator instead. ELSE optional on python/mcp-server, required on langgraph.
- **`MATCH classification.category: CASE bug: ... DEFAULT: ...`** *(v0.7)* — multi-way dispatch on an enum sub-field. CASE values must match enum variants; DEFAULT optional (recommended; required on langgraph).
- **`WHILE draft.score < 0.85 MAX 3: refine_draft(draft=draft)`** *(v0.7, composed in v0.12)* — bounded loop. MAX is mandatory; the loop exits when the condition turns false or after MAX iterations. Shares the IF grammar, so `and` / `or` work here too. Compiles to python, mcp-server, claude-skill, and go — langgraph and claude-cli reject it.

### Step calls

```
classify_ticket(ticket=t)        # keyword arg
score_chunks(corpus, question)   # positional shorthand: matches by TAKES name
load_text(file="input.txt")      # literal value
```

A step's `TAKES` parameters are passed by keyword; the literal forms (`"input.txt"`, integers, booleans) are inlined into the call site at compile time.

## RESCUE — multi-step failure handler (v0.8, extended in v0.13)

`RESCUE step_a:` declares a top-level handler that runs if `step_a` raises
after its `ON_FAIL` retry/escalate/fallback chain exhausts. Unlike
`ON_FAIL: abort(...)` (which is a single declarative clause), the RESCUE
body is a **chain of step calls** ending in either `abort("message")` or
`RESUME(<step>.<field>)` (v0.13), so you can notify, log, or run a
deterministic fallback before deciding how to terminate:

```
STEP detect_churn
  ON_FAIL: retry(3) then escalate
  ...

FLOW pipeline
  load_csv(...) -> detect_churn(...) -> route(...)

  RESCUE detect_churn:
    -> notify_slack(channel="#alerts", reason=detect_churn.error.message)
    -> abort("churn detection failed")
```

When `detect_churn` raises:
1. `ON_FAIL: retry(3) then escalate` exhausts itself.
2. The RESCUE body runs: `notify_slack` then `abort`.
3. `abort` raises `FlowAborted("...")`. The chain item after `detect_churn`
   (`route`) is skipped.

### Inspecting the captured error (v0.13)

Inside the body, `<rescued_step>.error.message` (the exception string) and
`<rescued_step>.error.type` (the Python class name) are valid kwarg values.
They are validated at compile time: only the protected step is reachable,
and only `message` / `type` are exposed.

### RESUME — typed drop-in fallback (v0.13)

If a deterministic fallback step can produce an acceptable answer, the
body can terminate with `RESUME(<fallback_step>.<field>)` instead of
`abort(...)`. The injected value lands in `state[<rescued_field>]` and
the downstream chain continues normally:

```
RESCUE detect_churn:
  -> notify_slack(channel="#alerts", reason=detect_churn.error.message)
  -> fallback_detect(rows=rows)
  -> RESUME(fallback_detect.report)
```

The compiler checks at build time that `fallback_detect.report`'s type
structurally equals `detect_churn`'s GIVES type — a mismatch is a compile
error, not a runtime surprise.

**One RESCUE per STEP**; the handler attaches to a STEP that appears in
the top-level FLOW chain (not nested inside FOR EACH / IF / MATCH /
WHILE). Compiles to **python**, **mcp-server**, **claude-skill**
(rendered as a RESCUE sub-section in `SKILL.md` for the LLM host to
follow), and **go** (v0.23); **langgraph** and **claude-cli** reject at compile time.

### FLOW signature (v0.16, optional)

A `FLOW` may declare `TAKES:` and `GIVES:` blocks, mirroring `STEP`. This is the recommended form when a flow starts with `FOR EACH` / `IF` / `WHILE` over an external input, when you want the test suite to type-check `TEST WITH:` / `EXPECTS:` clauses, or when you want a clean `inputSchema`/`outputSchema` exposed by the `mcp-server` or `claude-skill` targets. When a FLOW omits the signature, v0.15 behaviour is preserved (input auto-promotion from the first step, output inferred from the last step).

### FLOW composition (v0.17+)

A FLOW that declares both `TAKES:` and `GIVES:` is callable as a step in another FLOW — anywhere a step call is legal (chains, `IF` / `MATCH` / `WHILE` branches, `RESCUE` bodies, and a `FOR EACH PARALLEL` body). Name resolution is STEP first, then signed FLOW; a shared name is rejected at IR build time.

```clio
FLOW enrich
  TAKES: url: str
  GIVES: summary: str
  fetch(url=url) -> summarize(article=article)

FLOW batch
  TAKES: urls: List<str>
  FOR EACH u IN urls PARALLEL AS results:
    enrich(url=u)        # sub-flow as the parallel body
```

Three v0.17 limitations to keep in mind: a FLOW without a signature is **not** callable as a sub-flow, recursion and inter-flow cycles are rejected at compile time, and `target: claude-cli` rejects sub-flow calls (use `--target python` or `--target mcp-server`). See cookbook [recipe #20](03-cookbook.md#20-composing-flows-v017) for the worked example.

## Multiple FLOWs per file (v0.15)

A source file may declare any number of `FLOW`s. `clio compile` and
`clio graph` accept `--flow <name>` to pick one; single-FLOW files
don't need the flag.

```
FLOW ingest_only
  load(file="data.csv")

FLOW analyze_only
  load(file="cached.csv") -> classify(rows=rows)
```

```bash
clio compile two_flows.clio --target python --output ./out --flow analyze_only
```

Duplicate FLOW names are rejected at IR build time with a source-line message.

## TEST — declarative end-to-end tests (v0.15)

A top-level `TEST <name>:` block asserts behaviour against a named FLOW
without writing pytest by hand. The `python` target emits one
`<output>/tests/test_<name>.py` per block; other targets ignore TESTs
silently.

```
TEST scores_at_least_one_row:
  FLOW: pipeline
  WITH:
    rows: "[{\"name\":\"Acme\",\"ca\":100}]"
  EXPECTS:
    risks: not_empty
  EXPECTS_NOT:
    error: not_empty
```

Predicates: `not_empty`, `empty`, `== <literal>`, `!= <literal>`,
`> N`, `>= N`, `< N`, `<= N`, `contains <literal>`. The state path is a
top-level field name — nested paths are not yet supported.

```bash
clio compile risk.clio --target python --output ./out
cd ./out && pytest tests/ -v
```

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

## Splitting your code across files (v0.18)

Large pipelines accumulate STEPs, CONTRACTs, and FLOWs in a single `.clio` file.
When the file grows past a few dozen declarations, a three-file layout keeps
things clean: shared shapes in one file, library logic in a second, entry point
in a third.

### The `FROM … IMPORT` declaration

```
FROM "<path>" IMPORT <name> [AS <alias>] [, <name> [AS <alias>]] ...
```

- The path is relative to the importing file's directory.
- It must start with `./` or `../`.
- It must end with `.clio`.

```
FROM "./schemas.clio" IMPORT Article, AnalysisResult
FROM "./nlp/nlp.clio" IMPORT analyse AS nlp_analyse
```

Multiple `FROM` declarations are allowed in a single file. The compiler
builds the full transitive closure of imports and rejects cycles.

### Visibility markers: `EXPOSE` and `INTERNAL`

Only explicitly `EXPOSE`d symbols are importable. Symbols with no marker
or with `INTERNAL` are private to their file.

```
EXPOSE CONTRACT Article        # importable
  SHAPE: {title: str, body: str, lang: str}

INTERNAL FLOW _helper          # private helper — not importable
  TAKES: x: str
  GIVES: y: str
  ...

FLOW also_private              # no marker = INTERNAL
  ...
```

An `EXPOSE FLOW` must declare both `TAKES:` and `GIVES:`.

### Worked example: `examples/multi_file/`

The project under `examples/multi_file/` shows the pattern with three files:

**`schemas.clio`** — shared CONTRACT definitions:

```
EXPOSE CONTRACT Article
  SHAPE: {title: str, body: str, lang: str}

EXPOSE CONTRACT AnalysisResult
  SHAPE: {category: enum(news|opinion|analysis|other), summary: str, confidence: float}
  ASSERT: 0.0 <= confidence <= 1.0
```

**`nlp/nlp.clio`** — reusable NLP FLOW that imports the shared shapes:

```
FROM "../schemas.clio" IMPORT Article, AnalysisResult

STEP preprocess
  MODE: exact
  TAKES: article: Article
  GIVES: cleaned: str

EXPOSE FLOW analyse
  TAKES: article: Article
  GIVES: result: AnalysisResult
  preprocess(article=article)
  -> classify(text=cleaned)

STEP classify
  MODE: judgment
  TAKES: text: str
  GIVES: result: AnalysisResult
```

**`main.clio`** — the entry point:

```
RESOURCES
  target: python

FROM "./schemas.clio" IMPORT Article, AnalysisResult
FROM "./nlp/nlp.clio" IMPORT analyse

STEP load_article
  MODE: exact
  TAKES: path: str
  GIVES: article: Article

EXPOSE FLOW pipeline
  TAKES: path: str
  GIVES: result: AnalysisResult
  load_article(path=path)
  -> analyse(article=article)
```

Compile it:

```bash
clio compile examples/multi_file/main.clio --target python --output ./out --flow pipeline
```

### `target: mcp-server` and visibility

For `target: mcp-server`, `EXPOSE FLOW`s in the entry file become MCP tools.
The entry file must expose at least one FLOW. Files that relied on the v0.17
implicit-exposure heuristic must be migrated — run `clio doctor --migrate-v018`
to apply the heuristic automatically (see `docs/manual/06-migration-v018.md`).

### `target: claude-cli` limitation

`target: claude-cli` rejects sources containing `FROM … IMPORT`. Use
`--target python`, `--target mcp-server`, `--target claude-skill`, or
`--target langgraph` for multi-file projects.
