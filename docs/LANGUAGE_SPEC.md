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
| `impl.mode: rest` | ✅ | ✅ | ✅ `requests.request(...)` | ✅ standalone Python step with `requests` | ✅ `requests.request(...)` |
| `impl.mode: shell` | ✅ | ✅ | ✅ `subprocess.run([...], shell=False)` | ✅ standalone Python step with `subprocess` | ✅ `subprocess.run([...], shell=False)` |
| `impl.mode: sql` / `mcp_tool` / `binary` | ❌ | ❌ | ❌ | ❌ | ❌ |
| `invoke.mode: cli` | ✅ | ✅ | rejected at compile time | (default behavior — `claude -p`) | rejected at compile time |
| `invoke.mode: api` (`anthropic`) | ✅ | ✅ | ✅ with overrides | (uses RESOURCES.models chain) | rejected at compile time |
| `invoke.mode: api` (`openai`) | ✅ | ✅ | ✅ — covers LiteLLM / OpenRouter / Ollama / vLLM via OpenAI-compat | rejected | rejected at compile time |
| `invoke.mode: api` (`bedrock` / `vertex`) | ✅ | ✅ | rejected at compile time | rejected | rejected at compile time |
| `invoke.mode: embedded` / `mcp_sampling` | ❌ | ❌ | ❌ | ❌ | ✅ — `sampling/createMessage` via MCP client |
| `FOR EACH ... IN ...:` | ✅ | ✅ | ✅ `for x in state[...]:` | ✅ `mapfile` + bash `for` loop | ✅ `for x in state[...]:` |
| Judgment step inside FOR EACH | parses fine | builds fine | works | rejected at emit | works |

Where the table says *rejected at compile time*, the emitter raises a clear `ValueError` / `NotImplementedError` rather than producing silent or broken code.

v0 limitations carried forward, to be lifted in v0.3+:

- `impl.rest` templates TAKES into the `url` via `${var}` substitution (since v0.4); headers/body templating is not yet supported.
- `impl.rest` does not yet parse `query`/`headers`/`body` fields.
- `impl.rest` `retries` is parsed but not honored at runtime.
- `impl.shell` invokes argv-style (no shell pipes/redirections); the `cmd` string is `shlex.split` at compile time. Stdout is returned as a string. To use a pipeline (`cmd1 | cmd2`), wrap in a script and call that script.
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
    url:            https://maps.googleapis.com/maps/api/geocode/json
    query:          {address: ${address}, key: env:GOOGLE_MAPS_KEY}
    headers:        {Accept: application/json}
    body:           <expr>                    # POST/PUT only
    response_path:  results[0].geometry.location
    timeout:        30s                       # optional
    retries:        3                         # optional
```

Templating uses `${var}` for input fields and `env:NAME` for environment variables. The compiler validates the response against the step's `GIVES` schema after applying `response_path`.

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

Invocation of a tool exposed by a configured MCP server.

```
STEP search_docs
  MODE:    exact
  TAKES:   query: str
  GIVES:   results: List<DocChunk>
  impl:
    mode:    mcp_tool
    server:  internal-docs
    tool:    search
    args:    {q: ${query}, top_k: 10}
```

The server is assumed to be configured in the host environment (Claude Code MCP config or equivalent).

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
  budget:     <amount>                          # e.g. 30€/month
  prefer:     cost | latency | quality
  models:     [<model>, <model>, ...]
  strategy:   escalate | round-robin | fixed
  target:     claude-cli | python | rust | go | node | docker | hybrid
  lang:       python | rust | go | node | bash | auto
  impl:       <impl-block>                      # default impl for all exact steps in this flow
  invoke:     <invoke-block>                    # default invoke for all judgment steps in this flow
```

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

### WHILE

Conditional loop. Compiles to an agent pattern (iterative LLM calls with state).

```
WHILE <condition>:
  <step_call>
    -> <step_call>
```

### IF / ELSE

Conditional branching.

```
IF <condition>:
  -> <step_call>
ELSE:
  -> <step_call>
```

A step's failure can be tested:

```
IF <step_name>.FAILS:
  -> <fallback_step>
```

### MATCH / CASE / DEFAULT

Multi-way branching.

```
MATCH <expression>:
  CASE <value>: <step_call>
  CASE <value>: <step_call>
  DEFAULT:      <step_call>
```

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

  IF détecter_churn.FAILS:
    -> abort("Impossible de détecter le churn — vérifier le format du CSV")

RESOURCES
  prefer:     quality
  models:     [haiku, sonnet]
  strategy:   escalate
  target:     claude-cli
  lang:       python
```
