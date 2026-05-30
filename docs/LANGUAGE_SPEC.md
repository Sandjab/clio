# CLIO Language Specification v0.2

This is the reference grammar for the CLIO language. The compiler parses `.clio` files written in this syntax.

## Orientation

If you're discovering CLIO, read [§Semantic note: EXACT vs JUDGMENT](#semantic-note-exact-vs-judgment) first, then [§Declarations](#declarations) for the core language, then [§Control flow](#control-flow). The `## v0.X changes` sections (v0.15, v0.2) and version-tagged subsections (e.g. `### IMPORT and EXPOSE (v0.18)`) are historical notes describing what each release added; they are kept for migration context but are not a beginner-friendly read.

For the current per-target feature matrix (what `python` / `mcp-server` / `langgraph` / `claude-skill` / `claude-cli` each support), see [`docs/manual/04-targets.md`](manual/04-targets.md#cross-target-feature-support). The table in [§v0.2 changes](#v02-changes) below is a snapshot of v0.2 implementation status and is not maintained as the authoritative cross-target matrix.

## Contents

- [§Semantic note: EXACT vs JUDGMENT](#semantic-note-exact-vs-judgment)
- [§File extension](#file-extension)
- [§Comments](#comments)
- [§Declarations](#declarations) — `STEP`, `CONTRACT`, `FLOW`, `IMPORT` / `EXPOSE` (v0.18), `TEST`, `RESOURCES`
- [§Control flow](#control-flow) — `FOR EACH`, `IF`, `MATCH`, `WHILE`
- [§Failure strategies (`ON_FAIL`)](#failure-strategies-on_fail)
- [§RESCUE handler (v0.8+)](#rescue-handler-v08)
- [§Observability (v0.4+)](#observability-v04)
- [§Types](#types)
- [§Example](#example)

Version diffs (historical): [§v0.15 changes](#v015-changes), [§v0.2 changes](#v02-changes).

## v0.15 changes

Adds OpenProse-inspired surface features without touching execution semantics:

- **`DESCRIPTION:` and `STRATEGIES:`** — optional free-text fields on `STEP`.
  Both accept either a quoted `"..."` single-line string or a `|` block scalar.
  The python emitter appends them to the judgment step's system prompt as a
  "Step intent: …" / "Heuristics: …" suffix. Other targets store them in IR
  and may use them later.
- **Multiple `FLOW` declarations per file** — the parser accepts any number.
  IR build requires `--flow <name>` (programmatic: `build_ir(program, flow_name=...)`)
  when more than one FLOW exists. Single-FLOW files behave exactly as v0.14.
  Duplicate FLOW names are rejected at IR build time with the source line.
- **`TEST` top-level block** — declarative tests, see §TEST below. Emitted as
  pytest files by the `python` target under `<output>/tests/`. Other targets
  ignore them silently in v0.15.

## v0.2 changes

Adds per-step **`impl:`** block (EXACT implementations: code, REST, shell, SQL, MCP tool, binary) and per-step **`invoke:`** block (JUDGMENT invocations: CLI, API, embedded, MCP sampling). Both are optional and backward-compatible — v0.1 files parse unchanged. Defaults can be set at the `RESOURCES` level and overridden per step.

Also lifts `FOR EACH <var> IN <collection>:` from spec-only to implemented control flow.

### Implementation status (as of v0.2)

| Feature | Parser | IR | python target | claude-cli target | mcp-server target | go target |
|---|---|---|---|---|---|---|
| `LANG:` per step | ✅ | ✅ | ignored (still emits Python on every EXACT) | ignored | ignored | only `go` or `auto` accepted (E_GO_001) |
| `impl.mode: code` | ✅ | ✅ | (default behavior — Python stub) | (default behavior — Python stub) | (default behavior — Python stub) |
| `impl.mode: rest` (`method`/`url`/`response_path`/`timeout`) | ✅ | ✅ | ✅ `requests.request(...)` | ✅ standalone Python step with `requests` | ✅ `requests.request(...)` |
| `impl.rest.query` / `impl.rest.headers` (templated dicts) | ✅ | ✅ | ✅ `params=` / `headers=` with `${var}` + `env:NAME` | ✅ same | ✅ same |
| `impl.rest.body` (json / raw / @file / form / multipart) | ✅ | ✅ | ✅ routed by form, content-type set automatically | ✅ same | ✅ same |
| `impl.rest.retry: {...}` (exponential/constant backoff) | ✅ | ✅ | ✅ honored at runtime, respects `Retry-After` | ✅ same | ✅ same |
| `impl.mode: shell` | ✅ | ✅ | ✅ `subprocess.run([...], shell=False)` | ✅ standalone Python step with `subprocess` | ✅ `subprocess.run([...], shell=False)` |
| `impl.shell.parse: json` | ✅ | ✅ | ✅ standalone Python step with `subprocess` + `json.loads` | ignored — stdout stored as raw `str` (since v0.5) | ✅ `subprocess.run([...]) + json.loads(stdout)` |
| `impl.mode: sql` (sqlite + postgres + mysql) | ✅ | ✅ | ✅ shared `clio_runtime.sql` | rejected at compile time | ✅ shared `clio_runtime.sql` |
| `impl.mode: binary` | ❌ | ❌ | ❌ | ❌ | ❌ |
| `impl.mode: mcp_tool` (stdio + SSE/HTTP) | ✅ | ✅ | ✅ long-lived client | ✅ per-step bootstrap | ✅ long-lived client |
| `RESOURCES.mcp_servers` block (named server specs) | ✅ | ✅ | ✅ | ✅ | ✅ |
| `RESOURCES.databases` block (named DB specs) | ✅ | ✅ | ✅ | rejected at compile time | ✅ |
| `invoke.mode: cli` | ✅ | ✅ | rejected at compile time | (default behavior — `claude -p`) | rejected at compile time |
| `invoke.mode: api` (`anthropic`) | ✅ | ✅ | ✅ with overrides | (uses RESOURCES.models chain) | rejected at compile time |
| `invoke.mode: api` (`openai`) | ✅ | ✅ | ✅ — covers LiteLLM / OpenRouter / Ollama / vLLM via OpenAI-compat | rejected | rejected at compile time |
| `invoke.mode: api` (`bedrock` / `vertex`) | ✅ | ✅ | rejected at compile time | rejected | rejected at compile time |
| `invoke.mode: embedded` / `mcp_sampling` | ❌ | ❌ | ❌ | ❌ | ✅ — `sampling/createMessage` via MCP client |
| `FOR EACH ... IN ...:` | ✅ | ✅ | ✅ `for x in state[...]:` | ✅ `mapfile` + bash `for` loop | ✅ `for x in state[...]:` |
| Judgment step inside FOR EACH | parses fine | builds fine | works | rejected at emit | works |
| `FOR EACH ... PARALLEL AS <name>` | ✅ | ✅ | ✅ `ThreadPoolExecutor` | ❌ rejected | ✅ `asyncio.gather` |
| `IF <cond>: ... ELSE: ...` (v0.7) | ✅ | ✅ | ✅ `if/else` | ❌ rejected | ✅ `if/else` |
| `MATCH x: CASE ...` (v0.7) | ✅ | ✅ | ✅ `match/case` | ❌ rejected | ✅ `match/case` |
| `WHILE <cond> MAX N:` (v0.7) | ✅ | ✅ | ✅ bounded `for/break` | ❌ rejected | ✅ bounded `for/break` |
| `FLOW.TAKES` / `FLOW.GIVES` (v0.16) | ❌ | ❌ | ❌ | ❌ | ❌ |

Where the table says *rejected at compile time*, the emitter raises a clear `ValueError` / `NotImplementedError` rather than producing silent or broken code.

v0 limitations carried forward, to be lifted in v0.3+:

- `impl.shell` invokes argv-style (no shell pipes/redirections); the `cmd` string is `shlex.split` at compile time. Stdout is returned as a `str` unless `parse: json` is set (since v0.5). To use a pipeline (`cmd1 | cmd2`), wrap in a script and call that script.
- `ASSERT` expressions support a single comparator clause or a **chained comparator** (`0.0 <= score <= 1.0`), which desugars to a left-associative `(a <= b) and (b <= c) and ...` per Python semantics. Explicit `and`/`or` keywords land in **IF/WHILE** conditions in v0.12; ASSERT keeps the chained-comparator form only. All chained sub-expressions must reference the same single field — multi-field asserts (e.g. `a > b`) remain rejected at emit time.
- `FOR EACH` body call results are not accumulated into state — the step is invoked for side effects only.
- `invoke.api` requires single-model overrides (no escalate chain when `invoke.model` is set).

## Semantic note: EXACT vs JUDGMENT

A step is **`EXACT`** when the compiler can *name* the function that does the work — a code path, a URL, a shell command, a SQL query, a tool reference. Output structure is determined by that named function.

A step is **`JUDGMENT`** when the function does not exist before execution: it is *invoked by prompt* in an LLM, and its output is validated against a `CONTRACT` because the compiler distrusts unstructured LLM output by default.

This refines the older "deterministic vs stochastic" framing. A REST call to an external API is `EXACT` (the endpoint is named) even though it can fail or rate-limit. Only LLM-by-prompt is `JUDGMENT`.

## File extension

`.clio`

## Comments

Lines starting with `#` are comments. Inline comments after `#` are also supported.

```
# This is a comment
STEP do_something  # inline comment
```

## Declarations

### STEP

An atomic unit of work. Does not describe HOW — only WHAT and with what guarantees.

```
STEP <name>
  TAKES:        <name>: <type> [, <name>: <type>]*
  GIVES:        <name>: <type>
  MODE:         exact | judgment | auto
  LANG:         python | rust | go | node | bash | auto  # optional, exact only — shorthand for impl.mode=code; impl.lang=<value>
  CACHE:        on | off | ttl(<duration>)               # optional, judgment only
  VALIDATE:     <boolean expression>                     # optional
  ON_FAIL:      <failure strategy>                       # optional
  impl:         <impl-block>                             # optional, exact only — see "EXACT implementations"
  invoke:       <invoke-block>                           # optional, judgment only — see "JUDGMENT invocation"
  DESCRIPTION:  "<text>" | |<block scalar>               # optional (v0.15) — author intent; injected into judgment prompts
  STRATEGIES:   "<text>" | |<block scalar>               # optional (v0.15) — heuristics for edge cases; injected into judgment prompts
```

**MODE values:**
- `exact` — deterministic code. The compiler generates a function/script.
- `judgment` — requires an LLM. The compiler generates a prompt + schema.
- `auto` — the compiler decides. Tries `exact` first, falls back to `judgment`.

**LANG** only applies to `exact` steps. If omitted, defaults to `auto` (compiler picks based on data size, dependencies, and target). Can also be set globally in RESOURCES.

**CACHE** only applies to `judgment` steps. Controls reproducibility:
- `on` — permanent cache. The input is hashed (prompt + schema + model + parameters) to produce a key. Same input = same output guaranteed, no API call.
- `ttl(<duration>)` — cache with expiration. Duration uses suffixes: `s`, `m`, `h`, `d` (e.g. `ttl(24h)`, `ttl(7d)`).
- `off` — every run calls the LLM. This is the default.

### CONTRACT

A typed shape guarantee on data flowing between steps.

```
CONTRACT <name>
  SHAPE:      <type definition>
  ASSERT:     <boolean expression>          # optional
  CONFIDENCE: >= <float>                    # optional, judgment steps only
```

**SHAPE** uses a type notation inspired by JSON Schema / Python typing:
- Primitives: `int`, `float`, `str`, `bool`
- Containers: `List<T>`, `Dict<K, V>`, `Optional<T>`
- Enums: `enum(val1|val2|val3)`
- Records: `{field: type, field: type}`
- Constrained: `str(max=200)`, `int(min=0)`

**CONFIDENCE** sets a threshold for LLM outputs. Below this, the step retries or escalates.

### EXACT implementations: `impl:` block

The `impl:` block describes how an `EXACT` step is realized. It is optional; if omitted, defaults to `mode: code`.

#### `impl.mode: code` (default)

Inline function in the target language.

```
STEP parse_csv
  MODE:    exact
  TAKES:   file: Path
  GIVES:   rows: List<Row>
  impl:
    mode:  code
    lang:  python    # optional; equivalent to top-level LANG
```

The compiler emits a stub function in the chosen language; the user fills the body.

#### `impl.mode: rest`

HTTP call to an external endpoint.

```
STEP geocode
  MODE:    exact
  TAKES:   address: str
  GIVES:   location: GeoPoint
  impl:
    mode:           rest
    method:         GET                       # GET | POST | PUT | PATCH | DELETE
    url:            "https://maps.googleapis.com/maps/api/geocode/json"
    query:          {address: "${address}", key: "env:GOOGLE_MAPS_KEY"}
    headers:        {Accept: "application/json"}
    response_path:  "results[0].geometry.location"
    timeout:        30s
    retry:          {attempts: 3}
```

##### Templating

All string values may use:

- `${var}` — substituted at runtime from the step's `TAKES` (compile-time error if `var` is not a TAKES name).
- `env:NAME` — the **whole** value must equal `env:NAME`; substituted at runtime from `os.environ[NAME]` (raises `KeyError` if unset). Inline `env:` inside a longer string is treated as plain text.

Templating applies to: `url`, every value of `query`, every value of `headers`, every string in any form of `body`, and the **content** of `@./file` bodies (after the file is read).

##### `query` and `headers`

Inline dicts. Values must be quoted strings or numbers — bare identifiers are accepted only when they form a valid `IDENT` token (so `Accept: "application/json"` requires quoting because `/` is not part of an identifier).

```
query:   {limit: 10, address: "${address}"}
headers: {Authorization: "Bearer env:API_TOKEN", Accept: "application/json"}
```

Note on `Authorization`: when the value contains `env:NAME` as a substring (not the whole value), it is **not** treated as a substitution — write the bearer prefix as part of the templated identity:

```
headers: {Authorization: "${auth_header}"}     # TAKES auth_header: str holds the full "Bearer ..." string
# or
headers: {Authorization: "env:AUTH_HEADER"}    # AUTH_HEADER env var holds the full "Bearer ..." string
```

##### `body` (POST / PUT / PATCH / DELETE)

Five forms, hybrid syntax:

| Form | Syntax | Content-Type | Notes |
|---|---|---|---|
| **JSON** | `body: {field: "${var}", ...}` (inline dict) | `application/json` | Values templated; numbers and bools allowed. |
| **Raw** | `body: "raw text ${var}"` (quoted string) | `text/plain` (overridable via `headers`) | The whole string is templated. |
| **File** | `body: "@./payload.json"` (string starting with `@`) | Inferred from extension (`.json` → application/json, `.xml` → application/xml, `.txt` → text/plain, else `application/octet-stream`) | File read at **runtime** relative to the cwd of the step process. The file content is templated (`${var}` and full-value `env:NAME` not honored inside file content — only `${var}`). Override content-type via `headers`. |
| **Form** | `body: {form: {field: "${var}", ...}}` | `application/x-www-form-urlencoded` | Values templated. |
| **Multipart** | `body: {multipart: {field: "${var}", file: "@./upload.pdf", ...}}` | `multipart/form-data` | Values starting with `@` are sent as binary file parts (read at runtime); other values are sent as text fields, templated. |

It is a parse error to combine forms (e.g. `body: {form: ..., multipart: ...}`).

> **`target: go` limitation.** The Go emitter supports the **JSON** and **Raw** body forms only. **File**, **Form**, and **Multipart** bodies are refused at compile time with `E_GO_013` — use `--target python` for those. (The other targets support all five forms.)

##### `retry`

Optional. Mandatory object form — the bare scalar `retries: N` is rejected at parse time (use `retry: {attempts: N}` for the same intent with the documented defaults).

```
retry:
  attempts: 3                          # required if `retry:` is present
  backoff:  exponential                # exponential | constant   (default: exponential)
  base:     0.1                        # seconds, base delay      (default: 0.1)
  cap:      30                         # seconds, max single delay (default: 30)
  on:       [5xx, 429, timeout]        # default: [5xx, 429, timeout]
```

Behavior:

- **`exponential`**: delay before attempt `i` (1-indexed retry, after the initial attempt fails) is `min(base × 2^(i-1), cap)`.
- **`constant`**: delay is `base` seconds, regardless of `i`.
- The `Retry-After` HTTP header (when present) **always overrides** the computed delay.
- Retries trigger when the response matches `on` — accepted tokens are `5xx` (any 500–599), `429`, `timeout` (network/read timeout), `network` (any `requests.exceptions.RequestException` other than HTTP status). 4xx errors other than 429 are **not** retried.
- After `attempts` total tries (initial + retries-1), the last error is raised and the step's `ON_FAIL` chain (if any) takes over.

##### Response handling

The compiler validates the response against the step's `GIVES` schema after applying `response_path`.

Forbidden combinations:

- `body` on `GET` (parse error — use `query` instead).
- `retry: {...}` and ON_FAIL `retry(N)` on the same step — they would compose unpredictably (parse error).

#### `impl.mode: shell`

Shell command with templated arguments. Output captured from stdout.

```
STEP extract_pdf
  MODE:    exact
  TAKES:   file: Path
  GIVES:   text: str
  impl:
    mode:    shell
    cmd:     "pdftotext ${file} -"
    timeout: 60s
```

The `cmd` is a quoted string. The compiler `shlex.split`s it at compile time, then templates `${var}` per token at runtime — `subprocess.run([...], shell=False)` runs the resulting argv. No pipes/redirections (wrap a pipeline in a script if needed). Non-zero exit codes raise `subprocess.CalledProcessError`, which `ON_FAIL` will see.

String literals (`"…"`) support two escapes — `\"` for a literal double-quote and `\\` for a literal backslash; any other `\x` is a literal backslash followed by `x`. This makes a `cmd` that emits JSON expressible: `cmd: "echo '{\"ok\": true}'"`. (Multi-line content belongs in a `|` block scalar, not a `"…"` literal.)

**`parse:`** (optional, default `none`) — controls how stdout is returned to the flow:

| Value | Behaviour |
|---|---|
| `none` (default) | `result.stdout` returned as `str`. The step's `GIVES` must be a `str` for downstream Pydantic validation to pass. v0.4 behaviour. |
| `json` | `json.loads(result.stdout)` runs at the end of the step. The parsed object goes through `GIVES` validation as usual — supports `List<...>`, `Dict<...>`, scalars, nested CONTRACTs. `JSONDecodeError` propagates and `ON_FAIL` (if any) handles it. |

Example (load a JSON-array file directly into a typed `List<chunk>`):

```
STEP load_corpus
  TAKES: file:   str
  GIVES: corpus: List<chunk>
  MODE:  exact
  impl:
    mode:  shell
    cmd:   "cat ${file}"
    parse: json
```

Other parse modes (`yaml`, `csv`, `lines`) are not supported in v0.5.

#### `impl.mode: sql`

Parameterized query against a database declared once in
`RESOURCES.databases` (see below) and referenced by name from the step.

```
STEP get_customer_orders
  MODE:    exact
  TAKES:   email: str
  GIVES:   orders: List<{id: int, status: str, total_cents: int}>
  impl:
    mode:    sql
    db:      crm                                # ← name from RESOURCES.databases
    query: |
      SELECT id, status, total_cents
      FROM orders
      WHERE customer_email = :email
```

**Field semantics:**

| Field   | Required | Notes |
|---------|:-:|---|
| `mode`  | required | Must be `sql`. |
| `db`    | required | Identifier; must match an entry in `RESOURCES.databases`. |
| `query` | required | SQL string. Multi-line `|` block scalar supported. Bindings use `:name`, where `name` must match a `TAKES` field. The runtime translates `:name` to the driver's native named-binding form (`?` for sqlite via `executemany`-style mapping; `%(name)s` for psycopg; `%(name)s` for pymysql). |

**Bindings:**

- Only TAKES field names may be referenced as `:name` in the query body. The runtime passes a `{name: value}` dict computed from the step's TAKES.
- `env:NAME` is **not** allowed inside `query` (secrets belong in `RESOURCES.databases.<name>.url`, not in query bodies). This is a parse-time rejection mirroring the same rule on `impl.mcp_tool.args`.

**Result mapping (auto-map, no explicit `columns:` field):**

The runtime reads `cursor.description` after `execute()` and maps each row to a dict keyed by the column name (or alias). Mapping then depends on the shape of `GIVES`:

| `GIVES` shape | Behavior |
|---|---|
| `List<{f1: T1, ...}>` | One record per row. Each record carries the fields named in `GIVES`. Compile time only checks the List-of-Record shape; the column-vs-field name match is verified at runtime via `cursor.description`. A missing field on any row raises a runtime error citing the column name and step name. |
| `{f1: T1, ...}` (single record) | Exactly one row expected. Zero or more-than-one rows raises a runtime error. |
| Primitive (`int`, `str`, `float`, `bool`) | Exactly one row, one column expected. |
| `int` for a DML query (INSERT / UPDATE / DELETE) | The runtime detects DML by `cursor.description is None` after `execute()` and returns `cursor.rowcount`. |

Type coercion across drivers (e.g. sqlite returns `int` for boolean columns) is the runtime's job; values are passed through as-is by the underlying driver.

**Restrictions:**

- `retry:` on `impl.sql` is **rejected at parse time** (use a `RESCUE` handler — same policy as `impl.mcp_tool` since v0.10).
- The `query` body must be a string literal, not an expression. No string concatenation, no template logic beyond `:name` bindings (no `${var}` substitution — that would invite SQL injection if mis-used by an author).
- Only one statement per query. Multi-statement queries (`SELECT ...; UPDATE ...`) are rejected by the underlying driver.
- Transactions across STEPs are not supported in v0.11. Each `impl.sql` STEP runs its query in autocommit mode.

**Targets:** supported on `python` and `mcp-server` (which reuse the same `clio_runtime.sql` module). Rejected at compile time on `claude-cli` and `langgraph`.

#### `impl.mode: mcp_tool`

Invocation of a tool exposed by an MCP (Model Context Protocol) server. The
server is declared once in the flow's `RESOURCES.mcp_servers` block (see
below) and referenced by name from the step. The runtime starts a client
per server, calls the tool over JSON-RPC, and maps the response into `GIVES`.

```
STEP search_docs
  MODE:    exact
  TAKES:   query: str
  GIVES:   results: List<DocChunk>
  impl:
    mode:    mcp_tool
    server:  docs                          # ← name from RESOURCES.mcp_servers
    tool:    search                         # tool name exposed by the server
    args:    {q: "${query}", top_k: 10}     # arguments passed to the tool call
    timeout: 30s                            # optional, default 60s
    parse:   json                           # optional, 'json' (default) or 'text'
```

**Field semantics:**

- `server` (required): name of an MCP server declared in `RESOURCES.mcp_servers`. Compile-time error if the name is undeclared.
- `tool` (required): tool name exposed by that server. Validated at runtime, not at compile time — a typo surfaces as a `tool not found` error from the server.
- `args` (optional, dict, default `{}`): arguments passed to the tool. **String values** support `${var}` substitution from `TAKES`; **numeric / bool / null** values pass through unchanged; **nested dicts and lists** are walked recursively (string values inside them get the same substitution).
- `timeout` (optional, default `60s`): wall-clock timeout for the tool call itself (does not include subprocess boot for stdio servers — that's tracked separately and is not user-visible).
- `parse` (optional, default `json`):
  - `json`: the first text content block of the response is `json.loads`-ed and validated against `GIVES`. Most MCP tools return JSON-shaped text — this is the natural default.
  - `text`: the first text content block is returned verbatim as a `str`. Only valid when `GIVES` is `str`. Compile-time error otherwise.

**`${var}` substitution scope:** identical to `impl.rest.body` — only string values inside `args` get rewritten. `env:NAME` is **not supported** in `args` (secrets belong in `RESOURCES.mcp_servers.<server>.env` / `.headers`, not in tool arguments).

**v0.10 limitations:**

- No `retry:` block on `impl.mcp_tool` — parse-time error if `retry:` is present. MCP failures bubble up immediately; wrap the step in a RESCUE handler if you need a retry-then-abort flow.
- Tool catalog not validated at compile time. A typo in `tool:` is a runtime error.
- `parse: <path>` (response navigation) not supported. Compose with a small `code` step downstream if you need to extract a sub-tree.
- The first text content block is the only one consumed. If a tool returns multiple content blocks (rare), only the first is mapped into `GIVES`.

#### `impl.mode: binary`

Pre-compiled binary with stdin/stdout JSON marshalling.

```
STEP fast_classify
  MODE:    exact
  TAKES:   text: str
  GIVES:   label: str
  impl:
    mode:   binary
    path:   ./bin/classifier
    args:   [--model, models/v3.bin]
    stdin:  json
    stdout: json
```

### JUDGMENT invocation: `invoke:` block

The `invoke:` block describes how a `JUDGMENT` step calls its LLM. It is optional; if omitted, falls back to `RESOURCES.invoke` (or compiler defaults if neither is set).

#### `invoke.mode: cli`

Subprocess to a locally installed LLM CLI. Authentication is inherited from the CLI environment — no API key in the source.

```
STEP analyze
  MODE:    judgment
  TAKES:   text: str
  GIVES:   summary: str
  invoke:
    mode:                  cli
    cli:                   claude          # default
    model:                 opus            # haiku | sonnet | opus (CLIO aliases)
    output_format:         json            # default
    max_turns:             5               # optional
    allowed_tools:         [Read, Grep]    # optional
    permission_mode:       default         # optional
    append_system_prompt:  <expr>          # optional
    session:               continue        # optional: continue | resume:<id>
```

#### `invoke.mode: api`

SDK or HTTP call to a network endpoint. The four-value `protocol` set determines which client the emitter generates. The `model` field is **opaque to the compiler** — the endpoint validates it.

```
STEP classify
  MODE:    judgment
  TAKES:   text: str
  GIVES:   label: enum(low|mid|high)
  invoke:
    mode:            api
    protocol:        openai                    # anthropic | openai | bedrock | vertex
    base_url:        http://litellm:4000       # optional for native providers; required for proxies / local servers
    model:           gemini-1.5-pro            # opaque — endpoint validates
    auth:            env:LITELLM_KEY           # env:VAR | aws-profile:NAME | gcp-sa:PATH | none
    temperature:     0.0                       # optional
    max_tokens:      1024                      # optional
    response_format: json_schema               # optional
    timeout:         60s                       # optional
    retries:         3                         # optional
    extra_headers:   {X-Tenant-ID: clio-prod}  # optional, passed through
    extra_body:      {metadata: {...}}         # optional, passed through
```

This decomposition (protocol / base_url / model / auth) handles cases where a model from one provider is served behind another protocol — e.g. Gemini exposed via LiteLLM in OpenAI-compat format: `protocol: openai`, `base_url: <litellm>`, `model: gemini-1.5-pro`.

#### `invoke.mode: embedded`

LLM loaded in-process by the workflow runtime.

```
STEP local_classify
  MODE:    judgment
  TAKES:   text: str
  GIVES:   label: str
  invoke:
    mode:          embedded
    engine:        mlx                        # mlx | llama_cpp | transformers | outlines | guidance
    model_repo:    mlx-community/Llama-3.1-8B-Instruct-4bit
    model_path:    <path>                     # mutually exclusive with model_repo
    quantization:  Q4_K_M                     # engine-specific
    n_ctx:         8192                       # optional
    n_gpu_layers:  32                         # optional, llama_cpp
    device:        mps                        # mps | cuda | cpu
    lazy_load:     false                      # default false
```

The runtime loads the model at workflow init (or first use if `lazy_load: true`) and shares it across all steps using the same `engine + model`.

#### `invoke.mode: mcp_sampling`

Delegation to the MCP client via JSON-RPC `sampling/createMessage`. Valid only for the `mcp-server` target.

```
STEP draft_reply
  MODE:    judgment
  TAKES:   request: str
  GIVES:   reply: str
  invoke:
    mode:                  mcp_sampling
    model_hints:           [claude-3-5-sonnet]
    intelligence_priority: 0.8
    speed_priority:        0.5
    cost_priority:         0.2
    include_context:       thisServer       # none | thisServer | allServers
    max_tokens:            1024
```

No model is enforced — the client decides based on hints. No API key on the server side.

### FLOW

A directed graph of steps.

```
FLOW <name>
  <step_call>
    -> <step_call>
    -> <step_call>
```

**Multiple FLOWs per file (v0.15):** a `.clio` source may declare any number
of FLOWs. `clio compile` and `clio graph` require `--flow <name>` to pick one
when more than one is declared. Programmatically: `build_ir(program, flow_name=...)`.
Duplicate FLOW names are rejected at IR build time.

### FLOW signature (v0.16, optional)

A FLOW may declare an explicit signature with optional `TAKES:` and `GIVES:` blocks, mirroring `STEP`. Both fields are optional in v0.16; absent fields fall back to v0.15 behaviour (first-step `TAKES` auto-promotion for inputs, last-step `GIVES` inference for outputs).

```
FLOW <name>
  TAKES: <field>: <type>, <field>: <type>, ...    # optional, multi-field
  GIVES: <field>: <type>, <field>: <type>, ...    # optional, multi-field
  <chain>
  <rescues>
```

**Semantics of `TAKES`:**
- Declared names are seeded into the chain's input scope before walking. This allows the chain's first item to be `FOR EACH`, `IF`, `WHILE`, or `MATCH` over an external input — these forms previously failed to compile because the auto-promote path only inspected first-position `StepCall`s.
- When `TAKES:` is declared, the first-step `StepCall` auto-promote (v0.15.1) is **disabled** for this FLOW. Any identifier kwarg in the chain that is not produced upstream and is not in `TAKES` is rejected at compile time with `line:col`.
- `clio compile --kwargs '{...}'` validates declared inputs structurally at parse time, not runtime.

**Semantics of `GIVES`:**
- Each declared field must match a field present in the *effective state* after the last chain item executes, with a structurally-equivalent type (subset coverage: the last step may produce additional state fields; only the declared subset is exposed externally).
- A missing field in the state — or a type mismatch — is rejected at compile time with `line:col`.
- `target: python` returns a dict keyed by the declared `GIVES` field names. `target: mcp-server` and `target: claude-skill` derive `outputSchema` from `GIVES` instead of inferring it from the last step.

**Interaction with `TEST`:**
- `TEST WITH:` kwarg names and types are checked against `FLOW.TAKES` when declared.
- `TEST EXPECTS:` / `EXPECTS_NOT:` field paths (`<root>.<sub>...`) are checked against `FLOW.GIVES` when declared.
- When the FLOW does not declare a signature, `TEST` behaves as in v0.15 (no compile-time type check).

**Worked example — closes the #21 case:**

```clio
STEP classify
  TAKES: item:  str
  GIVES: label: str
  MODE:  judgment

FLOW classify_batch
  TAKES: items:  List<str>
  GIVES: labels: List<str>
  FOR EACH item IN items PARALLEL AS labels:
    classify(item=item)
```

Without `TAKES:`, the FOR EACH at the head of the chain compiles to `state reference 'items' not produced by any previous step` (#21). With `TAKES:` declared, `items` is seeded as an external input and the FLOW compiles to all targets that support PARALLEL (python / mcp-server / claude-skill).

### FLOW description (v0.17.x, optional)

A FLOW may declare a free-text `DESCRIPTION:` that mirrors `STEP.DESCRIPTION` from v0.15. The field sits next to `TAKES:` / `GIVES:` in the FLOW header (any order) and accepts either a quoted string or a `|` block scalar.

```
FLOW <name>
  TAKES: <field>: <type>, ...
  GIVES: <field>: <type>, ...
  DESCRIPTION: "Short imperative sentence describing the FLOW's intent."
  <chain>
```

**Consumed by:**

- **`target: claude-skill`** — injected verbatim into the `SKILL.md` frontmatter `description:`, which is the signal the host LLM uses to auto-trigger the skill on intent match. Without an explicit description, the emitter defaults to `Execute flow <name>` and prints a runtime warning that auto-trigger will be weak.
- Other targets currently ignore the field. It is captured on `FlowIR.description` and available for future emitter use.

**Block-scalar form** (multi-line, same as `STEP.DESCRIPTION`):

```
FLOW retention_analysis
  DESCRIPTION: |
    Score each customer's churn risk from a CSV and route high-risk
    accounts to a follow-up flow.
  <chain>
```

Duplicate `DESCRIPTION:` on the same FLOW is a parse error (`line:col`).

### FLOW composition (v0.17)

Once a FLOW declares both `TAKES:` and `GIVES:`, it is structurally
indistinguishable from a STEP. The compiler lets you call such a FLOW
wherever a STEP call is legal — in a chain, in a `FOR EACH PARALLEL`
body, inside `IF` / `MATCH` / `WHILE`, or inside a `RESCUE` body.

Syntax: identical to a step call.

```
pipeline(urls=raw_urls)
```

#### Resolution

1. The name is looked up first in declared `STEP`s.
2. If not found, it is looked up in `FLOW`s that have an explicit
   signature (both `TAKES:` and `GIVES:` blocks).
3. A name shared between a `STEP` and a `FLOW` is rejected at compile
   time as a collision.
4. A call to a `FLOW` that lacks `TAKES` / `GIVES` is rejected with a
   clear error pointing at the missing signature.

#### Semantics

- The sub-flow's `TAKES` are bound by keyword (same as a step call),
  and each kwarg is type-checked against the declared signature.
- The sub-flow runs in its own scope — the parent's state is **not**
  visible to it. Only the declared `GIVES` of the sub-flow cross
  back into the parent, where each field is published flat into the
  parent's state (same convention as a step's `GIVES`).
- `RESCUE` handlers declared inside a sub-flow run only inside that
  sub-flow's scope. If they terminate the sub-flow with `abort(...)`,
  the abort propagates to the parent as a regular failure — the
  parent can in turn protect the sub-flow call with its own
  `RESCUE`.
- Recursive sub-flows (a FLOW that calls itself) and inter-flow
  cycles (`A → B → A`) are rejected at compile time.

#### PARALLEL FOR EACH bodies

A `FOR EACH ... PARALLEL` body may be either a single step call (as
in v0.16) or a single sub-flow call (new in v0.17). The collector
accumulates the sub-flow's GIVES across iterations. When the sub-flow
declares a single GIVES field, the collector publishes a
`List<<gives.type>>` cleanly. When the sub-flow declares multiple
GIVES fields, the collector holds a list of dicts and the parent's
declared `List<T>` annotation will not match — track this as a
limitation pending a follow-up release. On `target: go` this exact shape
(a multi-GIVES sub-flow read through a typed `FOR EACH PARALLEL`
collector) is refused at compile time with `E_GO_006`; single-GIVES
parallel and all sequential composition compile cleanly. The single-GIVES
go collector is **terminal-only**: it is produced, but typed downstream
consumption (`aggregate(xs=results)` or `FOR EACH x IN results`) fails the
`go build` and is deferred to v0.24.

### Target support for sub-flow composition

| target          | Sub-flow call support (v0.17)                                         |
| --------------- | --------------------------------------------------------------------- |
| `python`        | yes (sub-flow → function)                                             |
| `mcp-server`    | yes (sub-flow → function; uncalled FLOWs become tools)               |
| `claude-skill`  | yes (sub-flow → standalone script invoked from main)                  |
| `langgraph`     | yes (sub-flow → compiled sub-`StateGraph`)                            |
| `go`            | yes (sub-flow → `run<Name>()` func; single-GIVES parallel bodies are terminal-only — typed downstream consumption deferred to v0.24; multi-GIVES PARALLEL refused — E_GO_006) |
| `claude-cli`    | **no** — compile-time error (deferred to a later release)             |

### IMPORT and EXPOSE (v0.18)

#### Cross-file imports

A `.clio` file may import FLOWs and CONTRACTs from other files:

```
FROM "<path>" IMPORT <name> [AS <alias>] [, <name> [AS <alias>]] ...
```

- The path is relative to the importing file's directory.
- The path must start with `./` or `../`. Absolute paths are rejected (E_IMP_001).
- The path must end with `.clio` (E_IMP_002).
- The import list must not be empty (E_IMP_003).
- Each `AS <alias>` renames the symbol locally. Missing identifier after `AS` is E_IMP_004.
- Duplicate names in the same import list are rejected (E_IMP_005).

Multiple `FROM` declarations are allowed in a single file. The resolver
builds the full transitive closure; import cycles are rejected (E_RES_001).

**Example:**

```
FROM "./schemas.clio" IMPORT Article, AnalysisResult
FROM "./lib/nlp.clio" IMPORT analyse AS nlp_analyse
```

#### Visibility markers

The visibility of a FLOW or CONTRACT is controlled by an optional prefix:

```
EXPOSE FLOW classify_article
  TAKES: article: Article
  GIVES: label: str
  ...

INTERNAL FLOW _helper
  TAKES: x: str
  GIVES: y: str
  ...

FLOW also_internal     # absence of EXPOSE = INTERNAL
  ...
```

**Rules:**

- Only `EXPOSE`d symbols are importable by other files (E_RES_003 if attempted otherwise).
- A declaration may have at most one visibility marker (E_VIS_001).
- `EXPOSE` and `INTERNAL` may only prefix `FLOW` and `CONTRACT` declarations (E_VIS_002).
- An `EXPOSE FLOW` must declare both `TAKES:` and `GIVES:` (E_VIS_003).
- A name cannot be exposed as both a FLOW and a CONTRACT (E_VIS_004).

**For `target: mcp-server`:** `EXPOSE FLOW`s in the entry file become MCP
tools. The entry file must expose at least one FLOW (E_MCP_001). The v0.17
heuristic (uncalled signed FLOWs are implicitly exposed) is replaced by
explicit markers.

For all other targets, visibility markers are purely informational: the
compiler still emits all declared symbols regardless of `EXPOSE`/`INTERNAL`.

#### Re-export

A file may import a symbol and then re-`EXPOSE` it, making it importable
from this file by downstream consumers:

```
FROM "./lib.clio" IMPORT classify
EXPOSE classify       # re-exported through this file
```

A bare `EXPOSE <name>` without a full declaration re-exports an already-imported
symbol. It is legal to re-export a CONTRACT or a FLOW this way.

#### Resolution errors

| Code       | Trigger                                                         |
| ---------- | --------------------------------------------------------------- |
| E_IMP_001  | path does not start with `./` or `../`                          |
| E_IMP_002  | path does not end with `.clio`                                  |
| E_IMP_003  | empty import list                                               |
| E_IMP_004  | missing identifier after `AS`                                   |
| E_IMP_005  | duplicate symbol name in same `FROM ... IMPORT` list            |
| E_RES_001  | cyclic import between two or more files                         |
| E_RES_002  | imported file not found on disk                                 |
| E_RES_003  | symbol exists in source file but is not `EXPOSE`d               |
| E_RES_004  | symbol not declared at all in source file                       |
| E_RES_005  | same symbol imported twice (use `AS` to alias one)              |
| E_RES_006  | imported name clashes with a locally declared name              |
| E_VIS_001  | more than one visibility marker on a single declaration         |
| E_VIS_002  | `EXPOSE` / `INTERNAL` applied to a non-FLOW / non-CONTRACT      |
| E_VIS_003  | `EXPOSE FLOW` missing `TAKES:` or `GIVES:`                      |
| E_VIS_004  | same name exposed as both FLOW and CONTRACT                     |
| E_MCP_001  | `target: mcp-server` entry file has no `EXPOSE FLOW`            |
| E_CLI_001  | `target: claude-cli` source contains `FROM ... IMPORT ...`      |

#### Cross-file import support per target

| target         | `FROM ... IMPORT` support (v0.18)       |
| -------------- | --------------------------------------- |
| `python`       | yes                                     |
| `mcp-server`   | yes                                     |
| `claude-skill` | yes                                     |
| `langgraph`    | yes                                     |
| `claude-cli`   | **no** — E_CLI_001 at compile time      |

### TEST (v0.15)

Declarative test against a FLOW. Emitted as a pytest file under
`<output>/tests/test_<name>.py` by the **python** target.

```
TEST <name>:
  FLOW: <flow_name>                # required
  WITH:                            # optional: kwargs forwarded to run()
    <kwarg>: <literal>             # literal: "string" | number | true | false
    ...
  EXPECTS:                         # at least one EXPECTS or EXPECTS_NOT required
    <state_field>: <predicate>
    ...
  EXPECTS_NOT:                     # optional: same shape, sense inverted
    <state_field>: <predicate>
    ...
```

**Predicates:**

| Form              | Asserts                                    |
|-------------------|--------------------------------------------|
| `not_empty`       | `state[field]` is truthy / non-empty       |
| `empty`           | `state[field]` is falsy / empty / absent   |
| `== <literal>`    | equality (string / int / float / bool)     |
| `!= <literal>`    | inequality                                  |
| `> <number>`      | strictly greater than                       |
| `>= <number>`     | greater or equal                            |
| `< <number>`      | strictly less than                          |
| `<= <number>`     | less or equal                               |
| `contains <lit>`  | `<lit> in (state[field] or [])` — works on lists, strings, dict keys |

**Target support:**

- `python`: emits `tests/test_<name>.py` calling `run(**kwargs)` with
  `CLIO_STATE_FILE` pinned to a per-test tempfile (so runs don't pollute cwd).
- `claude-cli`, `mcp-server`, `langgraph`, `claude-skill`: ignore TESTs in
  v0.15 (compile cleanly, no test artefacts).

**Validation:** the referenced FLOW must exist in the same source. Duplicate
TEST names are rejected at IR build time. WITH-kwargs vs flow-signature
compatibility is **not** checked at compile time in v0.15 — type mismatches
surface at runtime.

### RESOURCES

Execution constraints declared at the flow level.

```
RESOURCES
  budget:       <amount>                        # e.g. 30€/month
  prefer:       cost | latency | quality
  models:       [<model>, <model>, ...]
  strategy:     escalate | round-robin | fixed
  target:       claude-cli | python | rust | go | node | docker | hybrid
  lang:         python | rust | go | node | bash | auto
  impl:         <impl-block>                    # default impl for all exact steps in this flow
  invoke:       <invoke-block>                  # default invoke for all judgment steps in this flow
  mcp_servers:  {<name>: <server-spec>, ...}    # MCP servers callable from impl.mcp_tool steps
  databases:    {<name>: <db-spec>, ...}        # databases callable from impl.sql steps
```

#### `RESOURCES.mcp_servers`

Declares the MCP (Model Context Protocol) servers a flow can talk to. Each
entry is a named **server spec** referenced from `impl.mcp_tool` steps via
`server: <name>`. Server names must be unique within a flow.

```
RESOURCES
  mcp_servers:
    docs:                                       # local stdio server
      transport: stdio                          # default, can be omitted
      command:   "mcp-server-docs"
      args:      ["--config", "./docs.json"]    # subprocess argv after command
      env:       {INDEX_DIR: "env:DOCS_INDEX"}  # subprocess env; env: refs allowed

    remote:                                     # remote SSE server
      transport: sse                            # 'sse' or 'http'
      url:       "https://api.example.com/mcp"
      headers:   {Authorization: "env:TOKEN"}   # optional; env: refs allowed
```

**Field semantics by transport:**

| Field       | stdio | sse / http | Notes |
|-------------|:-:|:-:|---|
| `transport` | optional, default `stdio` | required | One of `stdio` / `sse` / `http`. |
| `command`   | required | rejected | Path or PATH-name of the MCP server binary. |
| `args`      | optional, default `[]` | rejected | Subprocess argv after `command`. |
| `env`       | optional, default `{}` | rejected | Subprocess env. Values may use `env:NAME` to inherit from the host env at runtime. |
| `url`       | rejected | required | Server URL. Must be `https://` unless host is `localhost` or `127.0.0.1`. |
| `headers`   | rejected | optional, default `{}` | HTTP headers. Values may use `env:NAME`. |

Mixing transport-incompatible fields (e.g. `command:` on a `transport: sse` spec) is a parse-time error. Compile errors carry the source line of the offending field.

**Lifecycle (target-dependent):**

- **`python` and `mcp-server` targets — long-lived per-server.** The first `impl.mcp_tool` step that references a server boots the client lazily; subsequent steps in the same flow reuse it. Subprocess clients (stdio) are killed at flow exit via `atexit` / async-shutdown handler; SSE/HTTP sessions close via the underlying client's `__aexit__`. **A `FOR EACH ... PARALLEL` block does not duplicate clients** — all branches share the singleton, and the MCP protocol is concurrency-safe over a single connection (multiple in-flight `id`s).
- **`claude-cli` target — per-step bootstrap.** Each step is a standalone Python script invoked by the bash orchestrator; the client is started, called, and torn down within that script. SSE/HTTP servers pay only the HTTP-handshake cost; stdio servers pay subprocess-boot per step. This is a deliberate v0.10 trade-off — the bash orchestrator has no place to hold a long-lived client between subprocess invocations.

**Cross-target invariant:** the `args` (tool arguments) passed to a tool call, the `parse` semantics, and the `GIVES`-mapping are identical across the three supported targets — only the client lifecycle and transport handling differ.

**Validation rules:**

- A server referenced by an `impl.mcp_tool` step that is not declared in `RESOURCES.mcp_servers` is a compile-time error.
- A `RESOURCES.mcp_servers` entry with no referencing step emits a compile-time warning (the server spec is dead code).
- `command` and `url` are mutually exclusive within one server spec.

#### `RESOURCES.databases`

Declares the SQL databases a flow can talk to. Each entry is a named **DB
spec** referenced from `impl.sql` steps via `db: <name>`. Database names
must be unique within a flow (parse-time error on duplicates, mirroring
`mcp_servers`).

```
RESOURCES
  databases:
    crm:                                        # local sqlite file
      driver: sqlite
      url:    "./data/crm.sqlite"

    analytics:                                  # postgres via env URL
      driver: postgres
      url:    "env:ANALYTICS_DB_URL"

    legacy:                                     # mysql via env URL
      driver: mysql
      url:    "env:LEGACY_DB_URL"
```

**Field semantics:**

| Field    | Required | Notes |
|----------|:-:|---|
| `driver` | required | One of `sqlite` / `postgres` / `mysql`. Determines the runtime import (`sqlite3` stdlib, `psycopg`, `pymysql`) and the named-binding translation. |
| `url`    | required | Connection URL or path. Format depends on driver: a filesystem path or `:memory:` for sqlite (the runtime also accepts `sqlite:///path` SQLAlchemy-style URLs); a `postgresql://user:pass@host:port/db` libpq-compatible URL for postgres; a `mysql://user:pass@host:port/db` URL for mysql. May be the literal `env:NAME` to inherit the URL from a host environment variable at runtime. |

**Lifecycle (target-dependent):**

- **`python` and `mcp-server` targets — long-lived per-database connection.** The first `impl.sql` step that references a database opens its connection lazily; subsequent steps in the same flow reuse it. Connections are closed at process exit via `atexit`. `FOR EACH ... PARALLEL` blocks share the singleton; the runtime serialises access via a per-connection lock so the underlying drivers (which are not all thread-safe at the connection level — sqlite, in particular, is single-thread by default) stay consistent.
- **`claude-cli` and `langgraph` targets — rejected at compile time** in v0.11.

**Validation rules:**

- A database referenced by an `impl.sql` step that is not declared in `RESOURCES.databases` is a compile-time error.
- A `RESOURCES.databases` entry with no referencing step emits a compile-time warning (the DB spec is dead code).
- An unknown `driver` value is a parse-time error citing the offending line.
- An empty `url` is a parse-time error.
- Mixing extra unknown fields (e.g. `connection:` from the v0.2 inline form, `port:`, `host:`) is a parse-time error — the v0.11 form takes only `driver` and `url`.

**Cross-target invariant:** the SQL string, the `:name` binding mapping, the auto-mapping rule (column ↔ `GIVES` field), and the DML-vs-SELECT detection are identical across `python` and `mcp-server` — only the connection caching layer differs.

#### Override semantics for `impl` and `invoke`

`RESOURCES.impl` and `RESOURCES.invoke` provide flow-wide defaults. A step's own `impl:` or `invoke:` block performs a **shallow merge** over the corresponding default, key by key. Nested objects (e.g. `headers`, `extra_body`, `args`) are *replaced* on conflict, not deep-merged.

Example. If

```
RESOURCES
  invoke:
    mode:     api
    protocol: anthropic
    model:    haiku
    temperature: 0.0
```

and a step declares

```
  invoke:
    model: opus
    max_tokens: 4096
```

the effective configuration is `{mode: api, protocol: anthropic, model: opus, temperature: 0.0, max_tokens: 4096}`.

A step may also switch `mode` entirely — e.g. one step uses `mode: api` while the rest of the flow uses `mode: cli` — at which point the only inherited keys are those that make sense in both modes (typically none, so an explicit override of `mode` should re-declare all required fields).

## Control flow

### Sequential chaining

```
step_a -> step_b -> step_c
```

### FOR EACH

Iterates over a collection. The loop variable is available inside the block.

```
FOR EACH <item> IN <collection>:
  <step_call(item)>
```

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

### IF / ELSE (v0.7, composed in v0.12)

Conditional branching. A condition is a comparison
`<state_field>.<sub_field> <op> <literal>` where `<op>` is one of
`== != < <= > >=`, and `<literal>` is a string, number, bare-ident
(enum value), or the bool literals `true` / `false`. The state_field must be
a CONTRACT (so it has nested sub-fields exposed to the comparator). ELSE
is optional.

Since v0.12 multiple comparisons can be combined with the lowercase
keywords **`and`** / **`or`** and optional parentheses. Precedence
follows Python: `and` binds tighter than `or`, so

```
IF report.confidence < 0.7 or report.confidence > 0.9 and report.category == "bug":
```

parses as `a or (b and c)`. Use parentheses to override:

```
IF (report.confidence < 0.7 or report.confidence > 0.9) and report.category == "bug":
    human_review(report)
ELSE:
    auto_route(report)
```

There is no `not` keyword yet — invert a single comparison by flipping
its operator (`==` ↔ `!=`, `<` ↔ `>=`, …). Each leaf comparison is
validated independently: an unknown state field or sub-field anywhere in
the expression is rejected at IR-build time with the IF block's source
line. For failure-aware branching (`.FAILS` shorthand), see RESCUE
handlers (v0.8) below.

Both branches see the same outer state; fields produced inside a branch
do not "narrow" the type for downstream chain items (no implicit type
narrowing in v0.7).

Targets: python, mcp-server, langgraph (langgraph requires ELSE and
exactly one step call per branch in v0.7). The composed-condition form
in v0.12 changes nothing about those branch-shape constraints — only
the condition expression is richer; emitters render it as parenthesised
`(left) and/or (right)` in Python (`add_conditional_edges`' router on
LangGraph evaluates the same expression).

### MATCH / CASE / DEFAULT (v0.7)

Multi-way branching on an enum sub-field of a CONTRACT.

```
MATCH classification.category:
    CASE bug:     route_bug(classification)
    CASE feature: route_feature(classification)
    CASE praise:  route_praise(classification)
    DEFAULT:      route_general(classification)
```

CASE values are bare-idents (enum variants) or string literals; each value
must match an enum variant of the scrutinee's contract field. DEFAULT is
optional but strongly recommended (langgraph requires it). DEFAULT must
come last; duplicate CASE values are rejected at IR build time.

Targets: python (Python 3.10+ `match: case` natively), mcp-server (same,
async), langgraph (each arm = exactly one step call; DEFAULT mandatory).

### WHILE (v0.7, composed in v0.12)

Bounded conditional loop. The body re-evaluates the condition before each
iteration; the loop exits when the condition turns false **or** after MAX
iterations (whichever comes first). MAX is mandatory — unbounded loops
are forbidden at parse time.

```
WHILE draft.score < 0.85 MAX 3:
    refine_draft(draft=draft)
```

Since v0.12 WHILE shares the IF condition grammar — `and` / `or` and
parentheses compose multiple comparisons, e.g.

```
WHILE draft.score < 0.9 and draft.score > 0.1 MAX 5:
    refine_draft(draft=draft)
```

Body steps are expected to update the state field(s) referenced by the
condition for the loop to make progress (caller-side invariant; not
validated by the compiler).

Targets: python (`for _i in range(MAX): if not cond: break; body`),
mcp-server (same, async). LangGraph **rejects WHILE** at compile time in
v0.7 — bounded loops require cyclic edges plus state reducers, planned
for v0.8. Use python or mcp-server for refine-loop patterns today.

## Failure strategies (ON_FAIL)

Composable with `then`:

```
ON_FAIL: retry(3)
ON_FAIL: retry(3) then fallback(other_step)
ON_FAIL: escalate then retry(2)
ON_FAIL: abort("reason")
```

- `retry(n)` — retry the step up to n times
- `fallback(step)` — switch to an alternative step
- `escalate` — switch to a more capable LLM model
- `abort(message)` — stop the flow with an error

## RESCUE handler (v0.8+)

`RESCUE` declares a top-level handler attached to a STEP that runs only
if the STEP raises **after** its `ON_FAIL` chain (if any) exhausts
itself. Unlike `ON_FAIL: abort(...)`, which is a single declarative
clause, the `RESCUE` body is a **chain of step calls** — so you can
notify, log, persist, or otherwise side-effect before terminating. The
body ends in either `abort("message")` (raises `FlowAborted("message")`,
skipping the rest of the chain) or `RESUME(<fallback_step>.<field>)`
(injects a fallback value and continues normally). See
[v0.13 additions](#v013-additions) for error-access and `RESUME` details.

### Grammar

```
flow_decl         := "FLOW" ident NEWLINE INDENT
                       flow_chain
                       rescue_block*
                     DEDENT

rescue_block      := "RESCUE" step_name ":" NEWLINE INDENT
                       rescue_chain
                     DEDENT

rescue_chain      := (flow_item ("->" flow_item)* "->")? rescue_terminator
rescue_terminator := abort_call | resume_call
abort_call        := "abort" "(" STRING ")"                  # unchanged since v0.8
resume_call       := "RESUME" "(" IDENT "." IDENT ")"        # v0.13+

# Kwarg values in flow_item step calls accept an extra dotted shape
# inside RESCUE bodies:
kwarg_value       := STRING | NUMBER | IDENT | error_access
error_access      := IDENT "." "error" "." IDENT             # v0.13+, RESCUE body only
```

`RESCUE` blocks appear **after** the FLOW chain and **before** the
optional `RESOURCES` block. One block per protected STEP.

### Composition with ON_FAIL

`ON_FAIL` strategies (`retry`, `escalate`, `fallback`) run first; the
`RESCUE` body runs only if they exhaust. Declaring **both** an `ON_FAIL`
chain that already ends in `abort(...)` **and** a `RESCUE` block on the
same STEP is a compile error (redundant double-abort).

| ON_FAIL last clause | RESCUE present | Behaviour |
| --- | --- | --- |
| _(no ON_FAIL)_ | no | Exception propagates. |
| retry/escalate/fallback (no abort) | no | Exception propagates after exhaustion. |
| `... then abort("msg")` | no | `FlowAborted("msg")` after exhaustion. |
| _(no ON_FAIL)_ | yes | Exception caught, handler runs, ends with abort/RESUME¹. |
| retry/escalate/fallback | yes | Exhaustion → handler runs → abort/RESUME¹. |
| `... then abort("msg")` | yes | **Compile error**: redundant `abort` final. |

¹ "RESCUE present" covers both `abort(...)` and `RESUME(...)` terminators. With `abort`, `FlowAborted` is raised and the remaining chain is skipped. With `RESUME`, a fallback value is injected into `state` and the flow continues normally.

### Targets

- **python** ✓ — emits a `try/except FlowAborted: raise; except
  Exception: <handler>; raise` wrap around the protected STEP and a
  `def _rescue_<step>(state)` helper containing the body. `abort` is
  rendered as `raise FlowAborted("msg")`.
- **mcp-server** ✓ — async mirror with `_session=_session` threading.
- **langgraph** ✗ — rejects at compile time. Cyclic edges, state
  reducers, and multi-step branches all need to land together; planned
  for the multi-step branches sprint.
- **claude-cli** ✗ — rejects at compile time.

### Cross-target invariant

`class FlowAborted(Exception)` is defined locally in the emitted
`flow.py`, gated on `rescues` being non-empty so flows without RESCUE
produce **byte-identical** output to v0.7. The class is module-local —
not exposed as a runtime package symbol — but importable as
`from <pkg>.flow import FlowAborted` if a downstream user wraps the
flow.

### v0.13 additions

- `<rescued_step>.error.message` (str) and `<rescued_step>.error.type`
  (str = Python exception classname) are now valid as **kwarg values**
  inside step calls within the RESCUE body. They reference the captured
  error that triggered the handler. Only the rescued step is
  referenceable — `<other_step>.error.X` is a compile error.
- `RESUME(<fallback_step>.<field>)` is a second legal terminator of the
  RESCUE chain, alongside `abort("msg")`. The fallback step must be
  called earlier in the same chain, the field must be a key of its
  `GIVES`, and the field's type must match the rescued step's `GIVES`
  type. The flow continues normally on the line after the rescued step,
  reading the injected value from `state[<rescued_field>]`.

### Persistent limitations

- One `RESCUE` per STEP (compile error for duplicates).
- The protected STEP must appear in the **top-level** FLOW chain — not
  nested inside a `FOR EACH`, `IF`, `MATCH`, or `WHILE` body.
- The body must end with a `rescue_terminator` (`abort(...)` or
  `RESUME(...)`) **directly at the top level** of the body chain (not
  just inside an `IF`/`MATCH`/`WHILE` branch).
- The standalone `clio graph --format mermaid` (and `--format dot`)
  outputs intentionally omit the rescue cluster — only the rich
  `--format html` viewer renders it. Use HTML when you need the
  visual; standalone mermaid stays minimal for GitHub embedding.

### Worked example

```
STEP detect_churn
  TAKES:    rows: List<int>
  GIVES:    risks: List<{client: str, score: float}>
  MODE:     judgment
  ON_FAIL:  retry(3) then escalate

FLOW pipeline
  load_csv(path="data.csv")
    -> detect_churn(rows=rows)
    -> route_alerts(risks=risks)

  RESCUE detect_churn:
    -> notify_slack(channel="#alerts", reason="churn detection failed")
    -> abort("churn detection failed — see #alerts")
```

Runtime sequence on failure:

1. `load_csv` runs.
2. `detect_churn` runs. If it raises:
   - `retry(3)` retries up to 3 times.
   - `escalate` switches to a more capable model.
   - If both exhaust, the `RESCUE` body runs: `notify_slack` then
     `abort`.
3. `abort` raises `FlowAborted("churn detection failed — see #alerts")`.
4. `route_alerts` is **skipped**.

## Observability (v0.4+)

Every emitted project (`target: python` or `target: mcp-server`) embeds a
small JSON-Line logger at `clio_runtime/logging.py`. The logger is **opt-in**:

- `CLIO_LOG=1` enables emission. Anything else (unset, "0", empty) is no-op.
- `CLIO_LOG_FILE=path/to/run.jsonl` redirects output to a file (default: `stderr`).

Six event types are emitted:

| Event | When | Required fields | Optional fields |
|---|---|---|---|
| `flow_start` | beginning of `run()` | `flow` | — |
| `flow_end` | end of `run()` (`finally`) | `flow`, `duration_ms`, `success` | — |
| `step_start` | first line of step body | `step`, `mode` (`exact`\|`judgment`) | `flow` |
| `step_end` | before each return | `step`, `mode`, `duration_ms`, `success` | `flow`, `cache_hit`, `model`, `fallback_used`, `tokens_in`, `tokens_out` |
| `parallel_block_start` | before ThreadPoolExecutor / `asyncio.gather` | `step`, `collector`, `total_iterations`, `max_workers` | `flow` |
| `parallel_block_end` | after the gather (`finally`) | `step`, `collector`, `total_iterations`, `duration_ms`, `success` | `flow` |

All events carry `ts` (ISO 8601 UTC, ms precision) and `event` (string).

The schema is intentionally flat and OTel-mappable: a downstream converter
to OTLP spans can be added without changing the emission contract.

`target: claude-cli` does **not** instrument logging in v0.4 — use
`--target python` or `--target mcp-server` for observable runs.

### Resume (v0.4+)

The python target persists `state.json` after each top-level chain item
completes. To resume from a specific step:

```bash
python -m my_pkg --from-step 3   # skip the first 3 chain items, resume from item 4
```

State file location: `./state.json` by default (cwd of the invocation),
override via `CLIO_STATE_FILE=path/to/state.json`.

State file schema:

```json
{
  "version": 1,
  "flow": "<flow_name>",
  "step_index": <last completed top-level chain item, 1-based>,
  "state": { "...accumulated state dict..." }
}
```

Granularity: a `FOR EACH` (sequential or PARALLEL) is one chain item
regardless of internal iterations. Mid-iteration resume is not supported.

Failure modes (all `SystemExit(2)`):
- `--from-step N` with N < 0
- state.json missing
- state.json `flow` field doesn't match the compiled package
- state.json `step_index` < N
- N >= TOTAL_STEPS

Targets v1: python only. mcp-server is server-stateless by design;
claude-cli deferred to v2.

## Types

### Primitives

`int`, `float`, `str`, `bool`

### Containers

`List<T>`, `Dict<K, V>`, `Optional<T>`, `Set<T>`

#### `Dict<K, V>` constraints (v0.21)

- **`K` must be `str`.** `Dict<int, V>`, `Dict<enum(...), V>`, etc. are rejected
  at parse time. Rationale: JSON object keys are always strings, and Go's
  `encoding/json` only natively supports string-keyed maps. Future versions
  may relax this for `enum`-typed keys.
- **`FOR EACH` over a `Dict` is forbidden.** If you need iteration, model the
  data as `List<{key: str, val: V}>` upstream.
- Nested generics inside `V` are supported: `Dict<str, List<int>>`,
  `Dict<str, {a: int, b: str}>`.

#### `Optional<T>` semantics (v0.21)

- **Nullable, not missing.** `Optional<T>` means "value of type T or null".
  The field is REQUIRED at the schema level: it must be present in the
  record, just possibly null. Pydantic v2 calls this `T | None` (not
  `T = None`, which is "missing-allowed with a default").
- **Target rendering**:
  - Pydantic (python / mcp-server / langgraph / claude-skill): `T | None`
  - Go: `*T` (pointer; `nil` represents null)
  - JSON Schema: `{"anyOf": [<T-schema>, {"type": "null"}]}` — `anyOf`
    works uniformly across primitives, `$ref`s, arrays, records, and enums
    (the multi-type-array form `{"type": ["X", "null"]}` cannot express
    nullable `$ref`).
- **Nesting**: any inner type is allowed — `Optional<List<int>>`,
  `Optional<{a: int}>`, `List<Optional<int>>`, `Dict<str, Optional<int>>`.

### Records

`{field_name: type, field_name: type}`

Nested records are allowed: `{user: {name: str, age: int}, score: float}`

### Enums

`enum(value1|value2|value3)`

### Constrained types

`str(max=200)`, `str(min=1)`, `int(min=0, max=100)`, `float(min=0.0, max=1.0)`, `float(precision=2)`

**Semantics per base (v0.21):**
- `str(max=N)`, `str(min=N)` — string LENGTH (int values). Render to JSON
  Schema `maxLength` / `minLength`, Pydantic `Field(max_length=N, min_length=N)`.
- `int(min=N, max=N)` — integer VALUE, inclusive. JSON Schema `minimum` /
  `maximum`, Pydantic `Field(ge=N, le=N)`.
- `float(min=N, max=N)` — numeric VALUE, inclusive. Float values allowed
  (e.g. `0.0`). Same JSON Schema / Pydantic keywords as `int`.
- `float(precision=N)` — exactly N decimal places. Renders to JSON Schema
  `multipleOf: 10**-N` (e.g. `precision=2` → `multipleOf: 0.01`), Pydantic
  `Field(multiple_of=10**-N)`. Portable across validators; semantically
  "the value must be a multiple of `10**-N`".
- `bool` accepts no constraint.

Constraint values are integers everywhere except `float(min/max)` which
accepts a float. `precision` is always an int (decimal-place count).

### Domain types

Domain types are aliases for common patterns:

- `CSV`, `JSON`, `Log` — file inputs
- `Email`, `URL`, `Markdown` — string subtypes

These compile to their base type + validation.

## Example

```
CONTRACT compte_risque
  SHAPE:      {client: str, risque: enum(low|mid|high), raison: str(max=300)}
  ASSERT:     len(raison) > 0

STEP charger_clients
  TAKES:     fichier: CSV
  GIVES:     clients: List<{nom: str, ca: float, dernière_commande: str}>
  MODE:      exact

STEP détecter_churn
  TAKES:     clients: List<{nom: str, ca: float, dernière_commande: str}>
  GIVES:     risques: List<compte_risque>
  MODE:      judgment
  CACHE:     ttl(24h)
  VALIDATE:  each risque.raison cites a column from clients
  ON_FAIL:   retry(3) then escalate

STEP vérifier_ticket_zendesk
  TAKES:     client: str
  GIVES:     dernier_ticket: {sujet: str, date: str, statut: str}
  MODE:      exact

STEP rédiger_mail_rétention
  TAKES:     risque: compte_risque, ticket: {sujet: str, date: str, statut: str}
  GIVES:     mail: {objet: str, corps: str}
  MODE:      judgment
  VALIDATE:  len(mail.corps) > 50 AND len(mail.corps) < 2000

FLOW rétention_clients
  charger_clients(fichier="clients.csv")
    -> détecter_churn(clients)
    -> FOR EACH risque IN risques:
         vérifier_ticket_zendesk(risque.client)
           -> rédiger_mail_rétention(risque, dernier_ticket)

  RESCUE détecter_churn:
    -> abort("Impossible de détecter le churn — vérifier le format du CSV")

RESOURCES
  prefer:     quality
  models:     [haiku, sonnet]
  strategy:   escalate
  target:     claude-cli
  lang:       python

## The `.clio/` sidecar convention (v0.19+)

When `clio compile --target claude-skill` emits a skill, it also writes a
`.clio/` directory inside the skill with two files:

- `source.clio` — a verbatim, byte-identical copy of the input `.clio`.
- `manifest.json` — CLIO version, emission timestamp, the source hash, and
  per-file SHA-256 hashes of every emitted file (excluding `.clio/` itself).

Hashes for text files (utf-8 decodable) are computed on LF-normalized bytes
(CRLF and CR → LF), so a skill edited across platforms (Windows ↔ Unix) does
not show false drift. Binary files are hashed on raw bytes.

The sidecar enables `clio import <skill-dir>` to recover the original source
without an LLM call when the skill hasn't been modified. When the skill has
drifted, `clio import` falls back to an LLM-assisted import (or exits 2
under `--mode strict`).

`.clio/` is excluded from `_gather_skill_files` so importing a CLIO-emitted
skill in `--mode infer` does not cheat by reading the stored source.

**Single-file limitation (v0.19).** The v0.19 sidecar stores only the **entry**
`.clio` file. Projects using cross-file imports (`FROM "lib.clio" IMPORT ...`)
recover an `source.clio` that references files not present in the sidecar; the
recovered source compiles only when the imported `.clio` files are also
present on disk next to it. Multi-file sidecar recovery is tracked as issue
[#67](https://github.com/Sandjab/clio/issues/67) for v0.20.
```
