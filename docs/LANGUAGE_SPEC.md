# CLIO Language Specification v0.2

This is the reference grammar for the CLIO language. The compiler parses `.clio` files written in this syntax.

## v0.2 changes

Adds per-step **`impl:`** block (EXACT implementations: code, REST, shell, SQL, MCP tool, binary) and per-step **`invoke:`** block (JUDGMENT invocations: CLI, API, embedded, MCP sampling). Both are optional and backward-compatible — v0.1 files parse unchanged. Defaults can be set at the `RESOURCES` level and overridden per step.

Also lifts `FOR EACH <var> IN <collection>:` from spec-only to implemented control flow.

### Implementation status (as of v0.2)

| Feature | Parser | IR | python target | claude-cli target | mcp-server target |
|---|---|---|---|---|---|
| `LANG:` per step | ✅ | ✅ | ignored (still emits Python on every EXACT) | ignored | ignored |
| `impl.mode: code` | ✅ | ✅ | (default behavior — Python stub) | (default behavior — Python stub) | (default behavior — Python stub) |
| `impl.mode: rest` (`method`/`url`/`response_path`/`timeout`) | ✅ | ✅ | ✅ `requests.request(...)` | ✅ standalone Python step with `requests` | ✅ `requests.request(...)` |
| `impl.rest.query` / `impl.rest.headers` (templated dicts) | ✅ | ✅ | ✅ `params=` / `headers=` with `${var}` + `env:NAME` | ✅ same | ✅ same |
| `impl.rest.body` (json / raw / @file / form / multipart) | ✅ | ✅ | ✅ routed by form, content-type set automatically | ✅ same | ✅ same |
| `impl.rest.retry: {...}` (exponential/constant backoff) | ✅ | ✅ | ✅ honored at runtime, respects `Retry-After` | ✅ same | ✅ same |
| `impl.mode: shell` | ✅ | ✅ | ✅ `subprocess.run([...], shell=False)` | ✅ standalone Python step with `subprocess` | ✅ `subprocess.run([...], shell=False)` |
| `impl.shell.parse: json` | ✅ | ✅ | ✅ standalone Python step with `subprocess` + `json.loads` | ignored — stdout stored as raw `str` (since v0.5) | ✅ `subprocess.run([...]) + json.loads(stdout)` |
| `impl.mode: sql` / `binary` | ❌ | ❌ | ❌ | ❌ | ❌ |
| `impl.mode: mcp_tool` (stdio + SSE/HTTP) | ✅ | ✅ | ✅ long-lived client | ✅ per-step bootstrap | ✅ long-lived client |
| `RESOURCES.mcp_servers` block (named server specs) | ✅ | ✅ | ✅ | ✅ | ✅ |
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

Where the table says *rejected at compile time*, the emitter raises a clear `ValueError` / `NotImplementedError` rather than producing silent or broken code.

v0 limitations carried forward, to be lifted in v0.3+:

- `impl.shell` invokes argv-style (no shell pipes/redirections); the `cmd` string is `shlex.split` at compile time. Stdout is returned as a `str` unless `parse: json` is set (since v0.5). To use a pipeline (`cmd1 | cmd2`), wrap in a script and call that script.
- `ASSERT` expressions support a single comparator clause or a **chained comparator** (`0.0 <= score <= 1.0`), which desugars to a left-associative `(a <= b) and (b <= c) and ...` per Python semantics. Boolean conjunction with explicit `and`/`or` keywords is not yet parsed (planned for v0.7). All chained sub-expressions must reference the same single field — multi-field asserts (e.g. `a > b`) remain rejected at emit time.
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
  TAKES:     <name>: <type> [, <name>: <type>]*
  GIVES:     <name>: <type>
  MODE:      exact | judgment | auto
  LANG:      python | rust | go | node | bash | auto    # optional, exact only — shorthand for impl.mode=code; impl.lang=<value>
  CACHE:     on | off | ttl(<duration>)                  # optional, judgment only
  VALIDATE:  <boolean expression>                        # optional
  ON_FAIL:   <failure strategy>                          # optional
  impl:      <impl-block>                                # optional, exact only — see "EXACT implementations"
  invoke:    <invoke-block>                              # optional, judgment only — see "JUDGMENT invocation"
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

Parameterized query against a database connection.

```
STEP enrich_customer
  MODE:    exact
  TAKES:   email: str
  GIVES:   customer: CustomerRecord
  impl:
    mode:        sql
    connection:  env:CRM_DB_URL
    query: |
      SELECT id, segment, lifetime_value
      FROM customers
      WHERE email = :email
```

Bindings use `:name`. Result rows are mapped to the `GIVES` shape.

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

### IF / ELSE (v0.7)

Conditional branching. The condition is a single comparison
`<state_field>.<sub_field> <op> <literal>` where `<op>` is one of
`== != < <= > >=`, and `<literal>` is a string, number, bare-ident
(enum value), or the bool literals `true` / `false`. The state_field must be
a CONTRACT (so it has nested sub-fields exposed to the comparator). ELSE
is optional. No boolean conjunction (`and`/`or`) in v0.7. For
failure-aware branching (`.FAILS` shorthand), see RESCUE handlers (v0.8)
below.

```
IF report.confidence < 0.7:
    human_review(report)
ELSE:
    auto_route(report)
```

Both branches see the same outer state; fields produced inside a branch
do not "narrow" the type for downstream chain items (no implicit type
narrowing in v0.7).

Targets: python, mcp-server, langgraph (langgraph requires ELSE and
exactly one step call per branch in v0.7).

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

### WHILE (v0.7)

Bounded conditional loop. The body re-evaluates the condition before each
iteration; the loop exits when the condition turns false **or** after MAX
iterations (whichever comes first). MAX is mandatory — unbounded loops
are forbidden at parse time.

```
WHILE draft.score < 0.85 MAX 3:
    refine_draft(draft=draft)
```

Body steps are expected to update the state field referenced by the
condition for the loop to make progress (caller-side invariant; not
validated in v0.7).

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

## RESCUE handler (v0.8)

`RESCUE` declares a top-level handler attached to a STEP that runs only
if the STEP raises **after** its `ON_FAIL` chain (if any) exhausts
itself. Unlike `ON_FAIL: abort(...)`, which is a single declarative
clause, the `RESCUE` body is a **chain of step calls** — so you can
notify, log, persist, or otherwise side-effect before aborting. The
body always ends in `abort("message")`, which raises
`FlowAborted("message")`. The chain after the protected step is then
skipped.

### Grammar

```
flow_decl    := "FLOW" ident NEWLINE INDENT
                  flow_chain
                  rescue_block*
                DEDENT

rescue_block := "RESCUE" step_name ":" NEWLINE INDENT
                  rescue_chain
                DEDENT

rescue_chain := "->"? flow_item ("->" flow_item)*  // last top-level item MUST be abort("...")
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
| _(no ON_FAIL)_ | yes | Exception caught, handler runs, ends with abort. |
| retry/escalate/fallback | yes | Exhaustion → handler runs → abort. |
| `... then abort("msg")` | yes | **Compile error**: redundant `abort` final. |

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

### v0.8 limitations

- One `RESCUE` per STEP (compile error for duplicates).
- The protected STEP must appear in the **top-level** FLOW chain — not
  nested inside a `FOR EACH`, `IF`, `MATCH`, or `WHILE` body.
- The body must end with `abort(...)` **directly at the top level**
  of the body chain (not just inside an `IF`/`MATCH`/`WHILE` branch).
- The handler body cannot inspect the captured error message
  (`step_name.error` is reserved for v0.9+).
- No `RESUME` keyword for fall-through; `abort` is the only legal
  terminator.
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

### Records

`{field_name: type, field_name: type}`

Nested records are allowed: `{user: {name: str, age: int}, score: float}`

### Enums

`enum(value1|value2|value3)`

### Constrained types

`str(max=200)`, `str(min=1)`, `int(min=0, max=100)`, `float(precision=2)`

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
```
