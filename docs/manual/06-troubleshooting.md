# Troubleshooting

Errors you're likely to hit, organised by where they happen in the pipeline.

## At parse time (`clio check` / `clio compile`)

### `ParseError: target 'rust' is not supported (valid targets: claude-cli, python, mcp-server)`

Your `RESOURCES target:` value isn't recognised. The three valid values are listed in the error message itself.

**Fix:** change `target: rust` to one of `claude-cli`, `python`, or `mcp-server`. The `target:` field is informational anyway — `--target` at compile time is what selects the emitter.

### `ParseError: RESOURCES with target: claude-cli requires a 'models' field`

You declared `target: claude-cli` without a `models:` line. Claude CLI needs the haiku→sonnet→opus escalation chain.

**Fix:** add `models: [haiku, sonnet, opus]` (or whichever subset). For `python` and `mcp-server` targets, `models:` is optional — per-step `invoke.api.model:` overrides.

### `ParseError: expected comparison operator at end of input`

Your `ASSERT` expression is missing its right-hand side, e.g. `ASSERT: score >=`.

**Fix:** complete the expression: `ASSERT: score >= 0.0`.

### `IRBuildError: line 22:8: unknown STEP 'classify'`

A `FLOW` calls a step name that wasn't declared. Often a typo (`classify` vs `classify_ticket`) or the `STEP` block is below the `FLOW` (CLIO doesn't care about order, but case-sensitivity does).

**Fix:** declare the step, or correct the spelling.

### `IRBuildError: line 36:5: FOR EACH iterates over 'tickets' but no upstream step produced it`

The collection variable in a `FOR EACH` doesn't match anything in scope at this point in the flow. Either the upstream step doesn't `GIVES` it, or its `GIVES` field has a different name.

**Fix:** check your upstream `STEP`'s `GIVES: tickets: List<...>`. The names must match exactly.

## At emit time

### `ValueError: claude-cli target does not support FOR EACH PARALLEL`

The `claude-cli` emitter explicitly rejects `FOR EACH ... PARALLEL AS` because bash can't safely manage concurrent state.

**Fix:** compile to `--target python` or `--target mcp-server` instead. Or rewrite the loop as a sequential `FOR EACH` if the parallelism isn't critical.

### `IRBuildError: IF/WHILE condition reads X.Y but X is not a CONTRACT`

You wrote `IF moderation.safe == true:` but `moderation` is a primitive (e.g. `bool`) — it has no nested fields to drill into. CLIO's IF/WHILE/MATCH conditions always read a contract sub-field, never a bare primitive. The same rule applies to each leaf of a composed condition (`A and B or C` — every leaf is validated independently).

**Fix:** wrap the value in a CONTRACT (`CONTRACT moderation_check SHAPE: {safe: bool, ...}`) and reference it as `state_field.sub_field`.

### `ParseError: expected COLON, got KEYWORD 'and'` (or `'or'`)

Two common shapes trigger this on an IF / WHILE line:

- a missing right-hand operand: `IF report.confidence < 0.7 and:` — `and` must be followed by another comparison.
- an unbalanced parenthesis: `IF (report.confidence < 0.7 and report.category == "bug":` swallows the closing `)`.

**Fix:** make sure each `and` / `or` joins two complete comparisons and that opening parentheses are closed before the `:` terminator. Remember the precedence rule — `and` binds tighter than `or`, so `a or b and c` already means `a or (b and c)`; explicit parens are only needed when you want the opposite grouping.

### `IRBuildError: CASE 'spam' is not one of the enum variants of report.category`

A MATCH CASE value doesn't match any variant declared in the contract field's enum.

**Fix:** check the contract — `enum(spam|support|sales)` for example. CASE values are bare-idents (or strings); typos and missing variants are caught at IR build time.

### `ValueError: langgraph target requires IF to have an ELSE branch in v0.7`

LangGraph's `add_conditional_edges` needs a destination for both truth values; an IF without ELSE leaves the false branch unwired.

**Fix:** add an ELSE branch (it can be a single passthrough step), or compile to `--target python` / `--target mcp-server` which support optional ELSE natively.

### `ValueError: langgraph target requires each IF branch to contain exactly one step call in v0.7`

You nested another control-flow block (`MATCH`, another `IF`, a chain `step1 -> step2`) inside an IF branch when targeting langgraph.

**Fix:** flatten the branches to single calls, **or** use `--target python` / `--target mcp-server` which support arbitrarily deep nesting. Multi-step branches in langgraph need conditional joins (planned for v0.8).

### `ValueError: WHILE is not supported by the langgraph target in v0.7`

WHILE requires cyclic edges plus state-counter accumulators in LangGraph, which the v0.7 emitter doesn't lower yet.

**Fix:** use `--target python` or `--target mcp-server` for refine-loop / improve-until-acceptable patterns. The bounded `for _i in range(MAX): if not cond: break; body` pattern they emit is the canonical CLIO WHILE today.

### `IRBuildError: line N: <step>.error.<field>: can only reference the step protected by this RESCUE`

**Cause:** A `step.error.message` or `step.error.type` kwarg value refers to a
step other than the one protected by the enclosing `RESCUE` handler.

**Fix:** use the rescued step's own name on the left of `.error.`:

```
RESCUE detect:
  -> notify(reason=detect.error.message)    # OK — detect is the rescued step
  -> notify(reason=load.error.message)      # ERROR — load is not the rescued step
```

### `IRBuildError: line N: unknown error field 'X', expected one of ['message', 'type']`

**Cause:** Only `step.error.message` (the exception string) and `step.error.type`
(the Python exception class name) are exposed in v0.13. Other names like
`.stacktrace` or `.cause` are not supported.

**Fix:** use one of the two supported fields. If you need additional context,
read it inside the `exact` step's Python body (e.g., `traceback.format_exc()`).

### `IRBuildError: line N: step.error.<field> is only valid inside a RESCUE handler`

**Cause:** `step.error.message` or `step.error.type` appeared as a kwarg value
in the main FLOW chain, not inside a RESCUE body. These values are only
meaningful while handling a failure.

**Fix:** move the reference into the `RESCUE <step>:` block attached to the
step whose error you want to inspect.

### `IRBuildError: line N: RESUME(<step>.<field>): step '<step>' is not called in this RESCUE handler`

**Cause:** The step named in `RESUME(X.field)` does not appear as a call
earlier in the same RESCUE body chain.

**Fix:** call the fallback step in the RESCUE body before the `RESUME` line:

```
RESCUE detect:
  -> fallback_detect(rows=rows)     # call the fallback step first
  -> RESUME(fallback_detect.report) # then resume from its result
```

### `IRBuildError: line N: RESUME(<step>.<field>): '<field>' is not a field of step '<step>'`

**Cause:** The field name in `RESUME(step.field)` does not match any `GIVES`
declaration on the fallback step.

**Fix:** check the fallback step's `GIVES` and use the exact field name it
declares.

### `IRBuildError: line N: RESUME(<step>.<field>): type T1 is incompatible with rescued step's GIVES type T2`

**Cause:** The fallback step's `GIVES` type does not structurally match the
rescued step's `GIVES` type. v0.13 requires strict equality so that the
injected value is a drop-in replacement.

**Fix:** align the types. Either change the fallback step's `GIVES` type to
match, or introduce an intermediate `exact` step that transforms the fallback
result into the expected shape before the `RESUME`.

### `IRBuildError: line N: RESCUE body for 'X' must end with abort(...) or RESUME(...)`

**Cause:** The last top-level item in the `RESCUE <step>:` chain is neither
`abort("...")` nor `RESUME(<step>.<field>)`. Every RESCUE handler must
terminate with exactly one of these.

**Fix:** add the missing terminator. If the handler runs side effects and has
no meaningful fallback, end with `abort("...")`:

```
RESCUE detect:
  -> notify(channel="#alerts", reason=detect.error.message, err_type=detect.error.type)
  -> abort("detect failed — see #alerts")
```

If a deterministic fallback is available, end with `RESUME(...)` instead (see
[recipe #14](#14-fallback-via-resume--recover-from-a-judgment-step-failure)).

### `IRBuildError: line N: RESCUE body for 'X' must end with abort(...) at the top level of the body chain`

The last item of the top-level chain in your `RESCUE X:` block must be
`abort("message")`. Putting `abort` only inside an IF/MATCH/WHILE branch
is not enough — the validator looks at the body's top level, not nested
data flow:

```
RESCUE detect:
  -> IF detect.ok == true:
       -> abort("ok-branch")
     ELSE:
       -> abort("ko-branch")
```

→ rejected. Hoist the `abort` to the body's top level:

```
RESCUE detect:
  -> IF detect.ok == true:
       -> log_ok()
     ELSE:
       -> log_ko()
  -> abort("done")
```

**Fix:** Move the terminal `abort(...)` to the top level of the rescue body chain. Use `IF`/`MATCH`/`WHILE` only for intermediate side effects; the final item must be `abort(...)` directly.

### `IRBuildError: line N: 'abort(...)' final clause in ON_FAIL is redundant when RESCUE 'X' is declared`

You declared both `ON_FAIL: ... then abort(...)` on STEP X and a
`RESCUE X:` at the FLOW level. That's ambiguous (double abort). Choose
one:

- Remove `abort(...)` from the `ON_FAIL` chain (leave only
  `retry/escalate/fallback`); the RESCUE body will handle the final
  abort.
- OR remove the `RESCUE X:` block; the `ON_FAIL: abort(...)` will
  handle the final abort instead.

The most common shape is the first: `ON_FAIL: retry(3) then escalate`
+ `RESCUE X: ... -> abort(...)`.

**Fix:** Either drop `abort(...)` from the `ON_FAIL` chain (keeping only `retry`/`escalate`/`fallback`) and let `RESCUE` produce the final abort, or remove the `RESCUE X:` block entirely and let `ON_FAIL: ... then abort(...)` produce it.

### `ParseError: impl.retries (scalar) is no longer accepted; use retry: {attempts: N} instead`

You wrote the legacy v0.8 form `retries: 3` on an `impl: rest` step. v0.9
requires the explicit object form so the policy is unambiguous.

**Fix:** rewrite as `retry: {attempts: 3}`. That picks up the documented
defaults (exponential backoff, base 0.1s, cap 30s, retry on
`5xx` / `429` / `timeout`). Override any sub-field you want, e.g.
`retry: {attempts: 5, backoff: constant, base: 0.5, on: ["5xx", "network"]}`.

### `ParseError: impl.body is not allowed on GET — use impl.query instead`

You attached a `body:` field to a `method: GET` step. HTTP semantics
forbid that. The compiler rejects it at parse time so the mistake doesn't
sneak into the generated code.

**Fix:** move the parameters into `query: {...}` (URL-encoded querystring).
If you really mean to send a body with a GET, change the method.

### `ParseError: impl.body cannot combine 'form' and 'multipart'`

You wrote `body: {form: {...}, multipart: {...}}`. The two body forms are
mutually exclusive — they imply different content-types and require a
different `requests` kwarg path.

**Fix:** pick one. If you need to send both fields and a file in the same
request, use `multipart` exclusively (text fields become regular form
parts, `"@./path"` values become file parts).

### `ParseError: impl.headers.X must be a string`

A header value is a number or bool: e.g. `headers: {X-Page: 10}`. HTTP
header values are strings; CLIO won't auto-stringify (which would hide
bugs like passing a boolean by mistake).

**Fix:** quote it: `headers: {X-Page: "10"}`. If the value is templated
from `TAKES`, write `headers: {X-Page: "${page}"}` — `${var}` substitution
takes care of stringifying via `str(...)` at runtime.

### `ParseError: impl.mcp_tool does not support 'retry:' in v0.10`

You wrote a `retry: {...}` block on a `mcp_tool` step.

**Fix:** drop it. If you need retries on MCP tool calls, wrap the step in a `RESCUE` handler that calls a recovery step before `abort(...)` — see [LANGUAGE_SPEC.md §RESCUE handler](../LANGUAGE_SPEC.md). A first-class `retry:` block on `mcp_tool` is planned for v0.11+ (it needs different semantics from REST: tool errors come back as a CallToolResult `isError` flag, not an HTTP status).

### `ParseError: RESOURCES.mcp_servers.<name> uses transport: stdio but declares 'url'`

You mixed transport-incompatible fields. `stdio` servers use `command` + `args` + `env`; `sse` and `http` servers use `url` + `headers`.

**Fix:** keep only the fields that match the chosen transport. The error message names the offending field. If you wanted a remote server, change `transport:` to `sse` or `http` and rewrite the spec accordingly.

### `ParseError: RESOURCES.mcp_servers.<name>.url must be https:// (or http:// for localhost / 127.0.0.1)`

For security, MCP server URLs must be HTTPS unless the host is local.

**Fix:** use `https://` in production. For local development, `http://localhost` and `http://127.0.0.1` are allowed.

### `IRBuildError: STEP 'X': impl.mcp_tool.server 'docs' is not declared in RESOURCES.mcp_servers (available: [...])`

A step references a server name that doesn't exist in the flow's `mcp_servers:` block.

**Fix:** declare the server in `RESOURCES.mcp_servers`, or correct the spelling. The error lists the available names. If `mcp_servers:` is missing entirely, add it.

### `IRBuildError: STEP 'X': impl.mcp_tool.parse: text requires GIVES of type 'str', got int`

`parse: text` returns the tool's text content block verbatim as a Python `str`. CLIO refuses to coerce it into a non-string GIVES (intentional — it would mask bugs).

**Fix:** either change `GIVES` to `str`, or switch to `parse: json` if the tool returns JSON-shaped text and your contract has a richer shape. For numeric coercion, use a small `code` step downstream.

### `warning: RESOURCES.mcp_servers.X is declared but never referenced by any impl.mcp_tool step (dead spec)`

A server spec exists in `RESOURCES.mcp_servers` but no step uses it. Compile still succeeds — this is a lint, not an error.

**Fix:** remove the unused spec, or wire up a step that calls it. If you're staging a future step, suppress the warning by leaving a `TODO:` comment near the spec.

### `RuntimeError: The 'mcp' package is required for impl.mcp_tool steps`

The compiled output ran a `mcp_tool` step but the `mcp` SDK isn't installed in that environment.

**Fix:** `pip install mcp` (or `pip install -U mcp` if `transport: http` complains about `streamablehttp_client` missing — that needs ≥ 1.4). The runtime imports `mcp` lazily, so REST-only and judgment-only flows in the same compiled package don't pay this cost.

### `ParseError: impl.sql does not support 'retry:' in v0.11`

Same policy as `impl.mcp_tool`: SQL errors don't fit a generic backoff scheme (a constraint violation will never succeed on retry; a connection drop usually needs a bigger pause than the runtime would pick). Use a `RESCUE` handler instead — it lets you decide explicitly how to recover.

**Fix:** drop the `retry:` block. If you need retry-then-abort semantics, wrap the step in `RESCUE` (see [recipe #10](03-cookbook.md#10-critical-llm-pipeline-with-on_fail--rescue)).

### `ParseError: impl.sql.query may not contain 'env:NAME' substitutions`

`env:NAME` inline in a SQL query body would be a SQL-injection vector if the host env var ever held untrusted text. CLIO blocks this at parse time.

**Fix:** put the secret in `RESOURCES.databases.<name>.url` (the URL field is the right place for credentials — `"env:CRM_DB_URL"`). If the secret is *data* the query genuinely needs (a tenant id, an API key passed through), pass it as a `:name` binding via TAKES, not via `env:`.

### `IRBuildError: STEP 'X': impl.sql.db 'crm' is not declared in RESOURCES.databases (available: [...])`

The step references a database name not in the flow's `RESOURCES.databases` block.

**Fix:** add the named entry to `RESOURCES.databases`, or correct the typo in `impl.sql.db`. The error lists the available names.

### `IRBuildError: STEP 'X': impl.sql requires a GIVES declaration`

Every `impl.sql` step needs a `GIVES` shape — the runtime maps query rows onto it. A bare `INSERT INTO log VALUES (:x)` step without GIVES would silently discard the affected-row count, hiding bugs.

**Fix:** add `GIVES: count: int` for DML (the runtime returns `cursor.rowcount`), or `GIVES: rows: List<{...}>` for a SELECT.

### `warning: RESOURCES.databases.X is declared but never referenced by any impl.sql step (dead spec)`

A database spec exists but no step uses it. Compile still succeeds — this is a lint, not an error.

**Fix:** remove the unused entry, or wire up a step that uses it.

### `RuntimeError: impl.sql with driver: postgres requires the 'psycopg' package`

The compiled output tried to open a postgres connection but `psycopg` isn't installed in the runtime environment.

**Fix:** `pip install 'psycopg[binary]'` (the `[binary]` extra avoids a libpq build). Same pattern for `mysql`: `pip install pymysql`. `sqlite` uses the stdlib and never raises this.

### `RuntimeError: impl.sql: GIVES expects exactly one row, got N (db='X')`

A step declared `GIVES: order: {...}` (a single record) but the SELECT returned 0 or 2+ rows.

**Fix:** if 0-or-1 rows is the right shape, change the GIVES to `Optional<{...}>` (planned for a later milestone) or split into two steps with explicit existence handling. If the query was meant to be unique, add a `LIMIT 1` plus a `WHERE` clause that guarantees uniqueness, or switch to `GIVES: rows: List<{...}>` and assert downstream.

### `ValueError: invoke.protocol 'bedrock' is not yet supported`

Bedrock and Vertex are specced but not implemented in any emitter yet.

**Fix:** route through an OpenAI-compat proxy (LiteLLM) and use `protocol: openai`, **or** stick to `protocol: anthropic` for direct Claude.

### `ValueError: CONTRACT 'foo' ASSERT references multi-field (...)`

Your `ASSERT` expression references more than one field name, e.g. `ASSERT: a > b` — Pydantic field validators only see one field at a time.

**Fix:** either restructure your contract so the constraint is on a single field, or wait for the planned `model_validator` extension. For numeric ranges on the same field (`0.0 <= score <= 1.0`), use chained comparators — that's a single field, multi-comparison, and is supported.

## At runtime (after `bash run.sh` / `python -m flow_name`)

### `pydantic_core.ValidationError: ASSERT failed: (urgency_score >= 0.0) and (urgency_score <= 1.0)`

The LLM returned a value out of the declared range. The contract validator caught it.

**What the runtime does:** triggers the step's `ON_FAIL` chain. If you have `ON_FAIL: retry(3) then escalate ...`, it reattempts up to 3 times, then escalates to the next model in `RESOURCES.models` (claude-cli) or whatever `escalate` means for your target.

**Fix:** if it happens chronically, sharpen the prompt — make the contract's range constraint explicit in the JSON schema the model sees.

### `[clio] resume requested (start_at=N) but state.json missing`

You ran `<entrypoint> --from-step N` but there's no `state.json` in the cwd (or where `CLIO_STATE_FILE` points).

**Fix:** run the flow from scratch first to produce a `state.json`, *then* resume.

### `ANTHROPIC_API_KEY not set`

The Python target's anthropic-protocol step couldn't authenticate.

**Fix:** `export ANTHROPIC_API_KEY=sk-ant-...`. For OpenAI-compat steps, set whatever env var is named in the step's `auth: env:NAME` field.

### `subprocess.TimeoutExpired` on an `impl.shell` step

Your shell command exceeded its `timeout: <s>` setting (or the runtime default).

**Fix:** raise the timeout in the step's `impl.shell` block, or split the work.

## At graph render time

### Mermaid renders but click-to-inspect doesn't work in `--format html`

The viewer needs internet to load `mermaid@10` + Geist fonts from CDN.

**Fix:** check the network. There's no offline `--inline` flag yet (planned). If you need offline, `--format mermaid` produces source that works in any markdown viewer.

### The arrows in the HTML viewer arrive at the wrong corner of the cards

Mermaid v10 with rich HTML labels has bbox-measurement quirks. CLIO works around this by **not** forcing `display: block` on Mermaid's wrappers. If you've extended the CSS yourself, watch for that.

**Fix:** if you didn't extend the CSS, file a bug — this should not happen on the bundled viewer.

## Build & install

### `pip install ./out` fails with "Multiple top-level packages discovered in a flat-layout"

The emitted project has a flat layout (no `src/`). Setuptools 68+ wants this configured.

**Fix:** the emitted `pyproject.toml` already declares `[tool.setuptools.packages.find]` correctly. If you're seeing this, your local setuptools may be older than 68 — `pip install -U setuptools` first.

## When the docs and code diverge

The CHANGELOG, language spec, and this manual are kept in sync **per release tag**. If you're on `main` between tags, expect occasional drift.

**Authoritative sources, in order of trust:**
1. The tests in `tests/` (machine-checked).
2. `LANGUAGE_SPEC.md` (kept current with each merge).
3. This manual (updated on tag boundaries — slightly delayed).

If something here contradicts the tests, the tests win and this page is stale. File an issue.

---

That's the manual. Compile, run, ship. If you hit something not covered here, open a GitHub issue with the `.clio` source + the exact error.
