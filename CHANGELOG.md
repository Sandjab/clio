# Changelog

## v0.9.0 — 2026-05-10

### Viewer

- **Replay an `events.jsonl` trace inside the HTML viewer**
  (`docs/manual/05-cli-reference.md` §`graph` / `html`). The toolbar
  now exposes a "Drop events.jsonl" target; once a trace is loaded, a
  control bar appears with play/pause/prev/next/restart, a `0.1×→10×`
  speed slider (default `2×`, scaled against real `ts` deltas), an
  auto-follow side panel for the active step, and a stats summary
  (`done` / `fail` / `total` walltime). Active steps pulse with a
  colored stroke; failed `step_end` events get a red border. No
  network calls — everything runs locally on the dropped file.
- The replay UI is non-invasive: REST-less / event-less flows render
  identically to v0.8 (the control bar stays hidden until a file is
  dropped).

### Tests

- 6 new viewer tests asserting dropzone, control-bar elements, CSS
  classes (`.replay-active`, `.replay-done`, `.replay-fail`), the
  `Replay` JS module entry points, and the manual-click auto-follow
  bypass. Suite total: 550 (up from 544).

### Language

- **`impl.rest` now parses and honors `query`, `headers`, and `body`**
  (`docs/LANGUAGE_SPEC.md` §impl.mode: rest). Inline-dict values support
  `${var}` substitution from `TAKES` and full-value `env:NAME` resolution
  from `os.environ`. Five body forms — JSON dict, raw string, `"@./file"`
  (content-type inferred from extension), `{form: {...}}` for
  `application/x-www-form-urlencoded`, and `{multipart: {...}}` for
  `multipart/form-data` (where `"@./path"` values become file parts).
  Forbidden: `body` on `GET`, mixing `form` + `multipart`.
- **`retry: {...}` replaces the parsed-but-ignored `retries: N` scalar**.
  Required field `attempts`; optional `backoff` (`exponential` |
  `constant`, default exponential), `base` (default 0.1s), `cap`
  (default 30s), `on` (default `["5xx", "429", "timeout"]`,
  also accepts `"network"`). Honored at runtime with `Retry-After`
  precedence on the computed delay. The bare scalar `retries: N` is now
  a parse-time error with a migration hint.
- New AST + IR nodes: `RetryPolicy` / `RetryPolicyIR`, sealed
  `RestBody` / `RestBodyIR` hierarchy with `JsonBody`, `RawBody`,
  `FileBody`, `FormBody`, `MultipartBody` variants.
- New parser primitives: inline-dict (`{k: v, ...}`) and inline-list
  (`[v, ...]`) value parsers, used for the new REST fields. Bool/null
  literals (`true`/`false`/`null`/`none`) are JSON-typed only inside
  inline dicts/lists; bareword `parse: none` etc. keep their string value.
- Inline-dict keys may now also be quoted strings, so users can write
  HTTP headers with non-identifier characters
  (`{"Content-Type": "application/json"}`).

### Emitters

- `python` and `mcp-server`: emit `requests.request(...)` with `params`,
  `headers`, JSON / raw / file / form / multipart body construction, and
  a retry loop wrapping the call when `impl.retry` is set. A new
  `clio/runtime/rest.py` module is bundled into `clio_runtime/`
  (templating + retry + content-type inference + file-body reading).
  REST-less flows still produce identical output (no spurious helper
  copy).
- `claude-cli`: each REST step now imports the same bundled
  `clio_runtime/rest.py` (added to `sys.path` at startup) and emits the
  same kwargs construction + retry loop. The runtime bundle is now
  copied whenever the flow has any REST or judgment step (not only
  judgment, as in v0.8).

### Documentation

- `docs/LANGUAGE_SPEC.md` §impl.mode: rest fully rewritten with the new
  syntax (templating rules, `body` table of 5 forms, `retry` field
  semantics) and updated implementation-status row.
- The legacy "v0 limitations carried forward" entries about
  `query/headers/body` and `retries` are removed.

### Tests

- 31 new unit tests for `clio.runtime.rest` (templating, content-type,
  retry classification, backoff, Retry-After parsing).
- 14 new parser tests (query/headers/body forms, retry validation,
  scalar-retries rejection, GET-with-body rejection,
  form/multipart-combined rejection).
- 13 new emitter tests (5 for python, 4 for claude-cli, plus runtime-
  copy assertions). Suite total: 543 (up from 483 at v0.8).

## v0.8.0 — 2026-05-10

### Language

- **RESCUE handler** (`docs/LANGUAGE_SPEC.md` §RESCUE handler): top-level
  block attached to a STEP that runs if the STEP raises after its
  `ON_FAIL` chain exhausts. Body is a chain of step calls ending in
  mandatory `abort("message")`, so you can notify/log/cleanup before
  aborting. Targets: python, mcp-server. langgraph and claude-cli reject
  at compile time.
- New keyword `RESCUE`.
- New IR validations: unknown step / nested step / duplicate rescue /
  abort clash with `ON_FAIL` / non-terminal abort / abort outside
  rescue body. All errors include the source line.
- `abort("...")` is now a recognised synthetic step call inside rescue
  bodies (still rejected outside).

### Emitters

- `python` and `mcp-server` emit a `try/except FlowAborted: raise; except
  Exception: <handler>; raise` wrap around protected STEPs and a
  `def _rescue_<step>(state)` (sync) / `async def _rescue_<step>(state,
  _session=None)` (async) helper containing the rescue body. `abort` is
  rendered as `raise FlowAborted("msg")`. `class FlowAborted(Exception)`
  is defined locally in the emitted `flow.py` (importable as
  `from <pkg>.flow import FlowAborted` for downstream catchers), gated
  on rescues being non-empty so flows without RESCUE produce
  byte-identical output to v0.7.

### Viewer

- `clio graph --format mermaid|html` now renders RESCUE blocks as a
  red-tinted `rescue_<step>` node connected by a dotted "fails" edge,
  with the body sub-flow ending in an `abort_<step>` circle. New
  `rescue_meta` is exposed to the JS via `__RESCUE_META_JSON__` for
  future side-panel enrichment.

### Documentation

- New §RESCUE handler in `docs/LANGUAGE_SPEC.md` with grammar,
  composition table (ON_FAIL × RESCUE), targets, v0.8 limitations, and
  a worked example.
- Manual updates: `02-language-tour.md` (RESCUE section), `03-cookbook.md`
  (critical LLM pipeline recipe), `06-troubleshooting.md` (2 new entries
  for the terminal-abort and ON_FAIL-clash errors).
- Narrative example at `docs/LANGUAGE_SPEC.md` lines ~649-657 migrated
  from the deferred `IF X.FAILS:` form to the actual `RESCUE` form.

### Tests

- 24 new tests covering parser, IR, emitters, and viewer for RESCUE.
- Suite total: 481 (up from 457 at v0.7).

## v0.7.0 — 2026-05-10

### Language

- **IF / ELSE conditional branching** (control flow). The condition is a
  single comparison `<state_field>.<sub_field> <op> <literal>` where `<op>`
  is one of `== != < <= > >=` and `<literal>` is a string, number,
  bare-ident (enum value), or the bool literals `true` / `false`. The
  state_field must be a CONTRACT so it has nested sub-fields exposed to
  the comparator. ELSE is optional. No boolean conjunction (`and`/`or`)
  and no `.FAILS` shorthand in v0.7 — those are deferred. Compiles to
  python (native `if/else`), mcp-server (same, async), and langgraph
  (`add_conditional_edges` + router function). LangGraph requires both
  ELSE and exactly one step call per branch in v0.7 (multi-step branches
  + optional ELSE are planned for v0.8).
- **MATCH / CASE / DEFAULT multi-way dispatch** on an enum sub-field of
  a CONTRACT. CASE values must match enum variants exactly; duplicate
  CASE values are rejected at IR build time. DEFAULT must come last and
  is optional in python/mcp-server, required in langgraph. Compiles to
  python/mcp-server via Python 3.10+ `match: case` and to langgraph
  via a `_match_<state_field>_<sub_field>` router function returning
  the next node name; `add_conditional_edges` wires the prev node to
  every arm's first step.
- **WHILE … MAX bounded loop** on python and mcp-server (langgraph
  rejects in v0.7). The body re-evaluates the condition each iteration;
  the loop exits when the condition turns false **or** after MAX
  iterations (whichever comes first). MAX is a mandatory positive
  integer — unbounded loops are forbidden at parse time. Emitted as
  `for _i in range(MAX): if not cond: break; body`. Body must update
  the state field referenced by the condition for progress (caller-side
  invariant).
- New tokens: `DOT` (`.` for `state_field.sub_field`).
- New keywords: `IF`, `ELSE`, `MATCH`, `CASE`, `DEFAULT`, `WHILE`, `MAX`.
- New IR nodes: `ConditionIR`, `IfBlockIR`, `MatchBlockIR`, `MatchCaseIR`,
  `WhileBlockIR`. The IR's FlowIR.chain union now includes all four
  control-flow primitives (`CallIR | ForEachIR | IfBlockIR | MatchBlockIR
  | WhileBlockIR`).

### Examples

- New `examples/feedback_routing.clio` — content-moderation + categorical
  routing pipeline that demonstrates IF/ELSE branching + MATCH/CASE
  dispatch in a realistic triage workflow. Compiles to python and
  mcp-server (langgraph rejects: nested MATCH inside the IF then-branch
  is a multi-step branch). Companion fixture at `examples/feedback.json`.

### Viewer

- HTML viewer renders IF as a Mermaid decision diamond (`if_N{"IF cond"}`)
  with `yes` / `no` labelled edges, MATCH as a diamond with one labelled
  edge per arm (`-- "spam" -->`, `-- "default" -->`), and WHILE as a
  cluster (subgraph) with the body inside and a `WHILE cond MAX N` label.
  `if_meta`, `match_meta`, `while_meta` are exposed as JS constants for
  future viewer enrichments (chip-pill banners, iteration counter, etc.).
  Vanilla `--format mermaid` and `--format dot` silently skip the new
  control-flow nodes (rich HTML viewer is the canonical visualisation).

### Emitters

- New `--target langgraph` emitter compiles a `.clio` source to a Python
  package whose `flow.py` builds a `langgraph.graph.StateGraph` (LangGraph
  1.0+). Each `STEP` becomes a node function `(state: State) -> dict`;
  `State` is a `TypedDict` aggregating every TAKES/GIVES field. `retry(N)`
  translates to `RetryPolicy(max_attempts=N)` on `add_node`. Step files
  are reused verbatim from the python target; only the orchestrator
  changes. Bridges CLIO into the LangChain ecosystem.
- v0 LangGraph scope: linear FLOW, `judgment.api.anthropic` (default
  `invoke`), `exact` (code stub / shell / rest), CONTRACT + Pydantic,
  CACHE, `retry(N)` + `abort`. Rejected at compile time with clear
  messages: FOR EACH (any kind), `invoke.cli`,
  `invoke.api.openai/bedrock/vertex`, ON_FAIL `escalate`/`fallback`.
  Send-API support for FOR EACH PARALLEL is planned for v0.7.

### Documentation

- New structured user manual at `docs/manual/`: getting-started tutorial,
  language tour, cookbook (7 recipes referencing every polished
  example), targets guide, CLI reference, and troubleshooting page.
  Linked from the main README. Complements the exhaustive
  `LANGUAGE_SPEC.md` reference with a parcours pédagogique.

## v0.6.0 — 2026-05-09

### Language

- `ASSERT` expressions now accept **chained comparators** —
  `0.0 <= score <= 1.0` desugars to `(0.0 <= score) and (score <= 1.0)`
  per Python semantics. Left-associative: `a < b < c < d` becomes
  `((a<b) and (b<c)) and (c<d)`. The chain must reference a single
  field (multi-field asserts remain rejected at emit time). Examples
  `rag_basic.clio` and `rag_selfcontained.clio` updated to use the new
  form (`0.0 <= score <= 1.0` instead of just the lower bound).
- `RESOURCES target:` now accepts `python` and `mcp-server` in addition
  to `claude-cli` (previously only `claude-cli` was allowed at parse
  time, forcing examples that compile to other targets to omit
  `RESOURCES` entirely). The `target:` field is informational — the
  `--target` CLI flag still drives the actual emitter selection.
- `RESOURCES.models:` is now optional when `target` is `python` or
  `mcp-server` (those targets take per-step model overrides via
  `invoke.api.model:`, so a flow-wide model chain is moot). Still
  required for `target: claude-cli` since the haiku→sonnet→opus
  escalation chain depends on it.

### Emitters

- Python emitter: emit `from .. import contracts` in step modules whose
  TAKES or GIVES reference any `CONTRACT` (impl.code stub, impl.shell,
  impl.rest). Without this, the qualified `list[contracts.Foo]` return
  annotation was an unresolved name — harmless under
  `from __future__ import annotations` but caught by
  `typing.get_type_hints`. Visible in the RAG self-contained example
  and in `ticket_routing` (impl.shell + parse:json).

### CLI

- `clio graph <file.clio> --format html` emits a single self-contained HTML
  viewer: the FLOW rendered by the existing Mermaid backend (loaded
  client-side from the mermaid.js ESM CDN), plus a click-to-inspect side
  panel that surfaces each step's TAKES, GIVES, mode, line, CACHE, ON_FAIL,
  IMPL, INVOKE, and the JSON Schema of every CONTRACT it references. No
  build step, no server. Open the HTML in any browser. The panel is
  populated via DOM API (textContent / appendChild), never `innerHTML`, so
  step or contract names containing HTML metacharacters are safe.
- `clio graph --format html` viewer redesign — Tabloid-grade rich cards.
  Cream paper background with a charcoal dot grid; each node is rendered
  as a Tabloid-style card with a colour-coded tinted head (icon + step
  name + kicker), a Lucide-style mode icon (sparkles for `judgment`, `>_`
  for `impl.shell`, code chevrons for `impl.code`, arrows for
  `impl.rest`), and a meta footer surfacing the most informative
  attributes (cache TTL, retry policy, gives type, parse mode). Mode
  classes — `judgment` / `exact-shell` / `exact-rest` / `exact-code` —
  drive both the node card and the detail panel theming. The kicker shows
  the next-level distinguishing detail (`cli`, `haiku`, `sonnet`, `cat`,
  `jq`, `GET`, `python`) instead of repeating the mode the icon already
  conveys. Typography: Geist Sans + Geist Mono (Google Fonts). Icons use
  the head's hue darkened (not the saturated brand colour), enforced via
  `!important` to defeat Mermaid's label-colour cascade. Vanilla
  `to_mermaid()` (used for `--format mermaid`) is unchanged so GitHub
  rendering is unaffected.
- `clio graph --format html` viewer — `FOR EACH … PARALLEL` cluster
  styling: soft cream-tinted wrapper with rounded corners, plus a chip
  pill flottante astride the top border (fieldset-legend style) showing
  a `git-branch` icon, the loop signature `FOR EACH t IN tickets`, and a
  `PARALLEL` kicker. Implemented as a post-render JS injection that
  swaps the placeholder cluster label for a `<template>`-cloned banner
  and resizes the `foreignObject` to fit. Amber/rust accent
  (`oklch(48% 0.155 60)`) — distinct from the four mode hues
  (judgment/shell/rest/code). The Mermaid source label is unchanged
  (`subgraph foreach_N["FOR EACH … [parallel]"]`), so vanilla
  `--format mermaid` output and existing tests stay valid.

### Examples

- `examples/ticket_routing.clio` — support-ticket routing pipeline. Three
  CONTRACTs (`support_ticket`, `classified_ticket`, `routing_summary`),
  multi-field structured judgment output (two bounded `enum(...)` fields plus
  a float with a numeric ASSERT), `FOR EACH ... PARALLEL AS classifications`
  to scale per-ticket classification, and a JUDGMENT summary step that turns
  the typed list into a narrative digest. Zero manual edit (loader uses
  `impl.shell` + `parse: json` on `examples/tickets.json`). Compiles to
  `--target python` and `--target mcp-server`; rejected by `--target
  claude-cli` (no PARALLEL support).
- `examples/tickets.json` — 6 French support tickets fixture
  (`{id, title, body}`), used by `ticket_routing.clio`.

## v0.5.0 — 2026-05-08

### Language

- `impl.mode: shell` accepts a new optional `parse:` field. Values: `none`
  (default — stdout returned as `str`, v0.4 behaviour) and `json` (stdout is
  passed through `json.loads` before `GIVES` validation, enabling
  `List<...>` / `Dict<...>` GIVES types from a `cat`-style command).
  Backward-compatible: every existing `.clio` file parses unchanged.

### Examples

- `examples/rag_basic.clio` — RAG-like pipeline (LLM-as-retriever) with the
  manual-edit loader pattern. Demonstrates 3 CONTRACTs, numeric ASSERT,
  multi-input judgment steps, and `citations: List<int>` for grounded answers.
- `examples/rag_selfcontained.clio` — same pipeline, zero-manual-edit using
  the new `impl.shell.parse: json`. Pair with `examples/faq.json`.
- `examples/faq.txt`, `examples/faq.json`, `examples/question.txt` — data
  fixtures shared by both variants.
- `examples/README.md` — new section 4 comparing the two variants.

### Resume

- **W5 (short-term): Step-granularity resume.** Python emitter writes
  `state.json` after each top-level chain item (atomic via
  `os.replace(tmp, path)`). The emitted `__main__.py` accepts
  `--from-step N` (1-based; reads `state.json` or `$CLIO_STATE_FILE`)
  and skips items 1..N. Granularity is one top-level chain item: a
  `FOR EACH` (sequential or PARALLEL) counts as one regardless of
  internal iterations. Strict fail-fast on edge cases. Targets v1:
  python only.

## v0.4.0 — 2026-05-08

### Language

- New `FOR EACH ... PARALLEL AS <name>:` syntax fans a single STEP across a collection in parallel and binds the typed result list to `state[<name>]`. Default concurrency cap = 10. Supported by the python target (`concurrent.futures.ThreadPoolExecutor`) and the mcp-server target (`asyncio.gather` + `Semaphore`); rejected at compile time by claude-cli. Body restricted to one step call in v1; nested PARALLEL rejected; failure mode = fail-fast (per-task ON_FAIL still applies).

### Language — v0.2 spec landed

- New per-step `impl:` block on EXACT steps: `mode: code | rest | shell`. `impl.mode: rest` describes an HTTP call with `method`, `url`, optional `response_path`, `timeout`, `retries`. `impl.mode: shell` runs an argv-style command with `cmd` (quoted, `shlex.split` at compile time) and optional `timeout`. The remaining modes (`sql`, `mcp_tool`, `binary`) are specified but not yet parsed.
- New per-step `invoke:` block on JUDGMENT steps: `mode: cli | api`. `invoke.mode: api` decomposes into `protocol` (`anthropic | openai | bedrock | vertex`), `base_url`, `model`, `auth`, `temperature`, `max_tokens`, `timeout`, `retries`. The protocol/base_url/model/auth split handles cases like Gemini-via-LiteLLM-via-OpenAI-compat.
- New per-step `LANG:` field accepted by the parser (`python | rust | go | node | bash | auto`). Specced since v0.1, now actually wired through AST and IR.
- New control flow: `FOR EACH <var> IN <collection>:` with an indented body. Loop variable binds to the collection's inner type and is visible to `step(x=item)` kwargs as a state-like reference.
- Refined semantic distinction between EXACT and JUDGMENT: EXACT = compiler can name the function (code, URL, shell, SQL, tool reference); JUDGMENT = invoked by prompt in an LLM. A REST call is therefore EXACT, not JUDGMENT.

### Emitters

- New `target: mcp-server` emitter compiles a `.clio` source into a runnable MCP (Model Context Protocol) server. Each `FLOW` becomes a tool registered with the official `mcp` Python SDK. Judgment steps delegate to the MCP client via `sampling/createMessage` — no API key on the server, no `anthropic`/`openai` SDK dep. inputSchema derives from the first step's TAKES (literal FLOW kwargs become defaults); outputSchema derives from the last step's GIVES. Steps with `invoke.protocol: anthropic|openai|bedrock|vertex` are rejected at compile time with a pointer to `--target python`. Reuses the python emitter's helpers for FOR EACH, CACHE, ON_FAIL, impl.rest, impl.shell. Emitted package ships a README with the client-config snippet.
- Python emitter: routes `invoke.protocol` between Anthropic SDK (default) and OpenAI SDK (chat.completions API). With `protocol: openai` + `base_url`, the same emission unblocks LiteLLM, OpenRouter, Ollama, vLLM, Together, Groq via OpenAI-compat. `pyproject.toml` adds `openai>=1.0` only when needed.
- Python emitter: emits `impl.mode: rest` as a step that calls `requests.request(...)` with optional `response_path` traversal (regex-walked, supports `.field` and `[N]` segments). `pyproject.toml` adds `requests>=2.31` only when needed.
- Python emitter: emits `FOR EACH` as `for var in state['coll']:` with body calls binding the loop variable as a local kwarg (not via `state[...]`). Nested loops supported.
- claude-cli emitter: emits `impl.mode: rest` as a standalone Python step using `requests` (the project ships no pyproject.toml, so `requests` is a documented operational requirement at run time).
- claude-cli emitter: emits `FOR EACH` as `mapfile -t _CLIO_ITER_N < <(jq <flag> '.<coll>[]' state.json)` then a bash `for` loop. `jq -r` is used for primitive collections (`List<str>`, etc.) and `jq -c` for object/list collections, so values arrive at body steps in the right shape. Body calls reference loop variables via `$var` rather than re-querying `state.json`.
- Both emitters reject explicitly at compile time the unsupported combinations: `protocol: bedrock`/`vertex`, `invoke.mode: cli` on python target, judgment steps inside `FOR EACH` on claude-cli target.
- Both emitters: `impl.mode: rest` substitutes TAKES into the `url` via `${var}` placeholders (`url.replace('${name}', str(name))` per TAKES). Templating is skipped when the url has no placeholder, preserving the existing static-url emission shape. Headers/body templating and `query`/`headers`/`body` field parsing remain on the v0.4+ backlog.
- Both emitters: `impl.mode: shell` emits a step that calls `subprocess.run([...], capture_output=True, text=True, check=True, timeout=...)`. The argv list is `shlex.split` at compile time and `${var}` placeholders are substituted token-by-token at runtime — `shell=False` keeps shell-injection out of the picture by construction. Stdout becomes the step's `GIVES`. No pipes/redirections (wrap a pipeline in a script).
- Python emitter: `pydantic>=2` is added to the emitted `pyproject.toml` only when at least one CONTRACT is declared. Skeleton flows (no contracts) no longer pull in an unused dependency.

### Observability

- **W2 (short-term): Structured JSON-Line logging.** New `clio_runtime/logging.py`
  module copied verbatim into emitted projects. Opt-in via `CLIO_LOG=1`,
  destination via `CLIO_LOG_FILE` (default stderr). Six event types: `flow_start`/
  `flow_end`, `step_start`/`step_end`, `parallel_block_start`/`parallel_block_end`.
  `python` and `mcp-server` targets instrumented; `claude-cli` deferred to v2.
  Schema is flat and OTel-mappable. ContextVar propagates `flow` natively
  through asyncio; ThreadPoolExecutor uses `contextvars.copy_context().run`.

### CLI

- New `clio graph <source>` subcommand that renders the FLOW as a Mermaid (default) or Graphviz DOT source. EXACT steps render as rectangles, JUDGMENT steps as parallelograms; FOR EACH renders as a labelled subgraph in Mermaid and as a dashed labelled edge in DOT (cluster-with-`lhead` machinery skipped on purpose). Output goes to stdout or to `--output FILE`. Designed for paste-into-GitHub-PR rendering since GitHub renders Mermaid natively.
- New `clio gen <description>` subcommand that turns a natural-language description into a valid `.clio` source via Anthropic SDK (Sonnet 4.6 by default). Compile-correct loop: parse + IR build validate the LLM output; on failure, a single retry feeds the previous attempt and the line/column error back to the model. After the retry budget, `GenerationError` is raised — the CLI prints the failed attempt as `# `-commented stderr lines so the user can paste-and-fix. The `anthropic` package is an optional `[gen]` extra; `compile`/`check`/`graph` keep their zero-runtime-deps. Reads description from arg, `--from-file`, or stdin; writes to stdout or `--output FILE`. Auth via `ANTHROPIC_API_KEY` env var.

### Refactor

- Split `clio/emitters/python.py` (was 991 lines): module-level helpers moved to `clio/emitters/_python_helpers.py` (375 lines). The `PythonEmitter` class stays in `python.py` (now 649 lines).
- Split `clio/emitters/claude_cli.py` (was 691 lines): helpers moved to `clio/emitters/_claude_cli_helpers.py` (249 lines). `claude_cli.py` is now 484 lines.
- `python.py` imports `_inline_schema` and `_render_prompt` from `_claude_cli_helpers` directly rather than through claude_cli re-export.

### Examples

- New `examples/entities.clio` (named-entity recognition + summary) demonstrating the language is not churn-specific. Three steps (two EXACT, one JUDGMENT), nested record types (`List<{kind: str, count: int}>`), enum + float fields. Compiles to both targets with no manual edits beyond filling EXACT step bodies.
- New `examples/classify_corpus.clio` combining `FOR EACH` + `invoke.protocol: openai` (LiteLLM proxy → Gemini). Two steps + one CONTRACT with ASSERT. Compiles via `--target python` only (claude-cli rejects openai protocol). Emits an `openai>=1.0`/`pydantic>=2` package — no `anthropic`, no `requests` — and a `flow.py` that chains `load_lines()` then `for line in state['lines']: classify(text=line)`.

### Documentation

- New `docs/POSITIONING.md`: 5 structural differentiators vs LangGraph and n8n (compiler-not-runtime, declarative source, EXACT/JUDGMENT split, multi-target, CONTRACT-as-primitive), 6 honest weaknesses with per-weakness action plan (W1–W6), and a bridge-target policy. n8n and LangChain compilation targets explicitly refused; LangGraph emitter conditional on python reaching W2/W5.
- `docs/COMPILATION_TARGETS.md`: new "Targets at a glance" table covering 15 targets (2 implemented, 4 documented future, 9 candidates). Fixes the line claiming exact steps could emit `.sh` (only `.py` was ever implemented).
- `docs/LANGUAGE_SPEC.md`: bumped to v0.2 with the new `impl:`/`invoke:` block specs, semantic note, override semantics, and an implementation-status table reflecting per-target coverage.

### Tests

- 294 tests + 2 e2e gated (was 263 + 2). +31 tests covering mcp-server emitter: file-tree structure, tool registration, sampling/createMessage judgment emission, inputSchema/outputSchema derivation, refused protocol combinations, FOR EACH and CACHE and ON_FAIL and impl.rest/shell wiring, emitted README content, and pyproject.toml dependency shape.

### Repo hygiene

- Track `uv.lock` in version control (per the gitignore comment, recommended for binary packages to ensure reproducibility).
- Add `.understand-anything/` to `.gitignore` (knowledge-graph cache generated locally by the skill).

### CI

- GitHub Actions workflow runs the pytest suite on push and PR to `main` (Python 3.12). E2E tests stay gated behind `CLIO_E2E=1`.

### Python emitter — latent fixes from v0.3 reviews

- Reject CONTRACT field names colliding with Pydantic v2 reserved attributes (`model_config`, `model_dump`, …) at emit time instead of crashing the generated `contracts.py` at import with `PydanticUserError`.
- Reject CONTRACT ASSERTs referencing more than one field at emit time instead of generating a `@field_validator` body that `NameError`s at runtime.
- Qualify `ContractRef` as `contracts.X` in step signatures so `typing.get_type_hints()` resolves under `from __future__ import annotations`.
- Treat stale cache hits (re-validation failure) as a cache miss and fall through to a fresh SDK call instead of crashing with `pydantic.ValidationError`.

### Parser

- Fix `IndexError` in `parse_term` when a bare identifier is the last token of an expression (`a > b`). Unblocks ASSERTs comparing two identifiers — the python emitter rejects them as multi-field, but the IR is now well-formed.

### Tests

- 121 tests + 2 e2e gated (was 116 + 2).

## v0.3.0 — 2026-05-04

Adds a second emitter target (`python`) producing a runnable Python package (Anthropic SDK + Pydantic v2) from the same IR. Validates the "IR is target-independent" architecture claim.

### Compiler

- `python -m clio compile --target python` — new target.
- Same `.clio` source, same IR; only the emitter differs.

### Emitted Python project

- Layout: `pyproject.toml` + importable package + `python -m <pkg>` CLI entry.
- Contracts → Pydantic v2 BaseModel classes (with `@field_validator` for `ASSERT`).
- Exact steps → typed function stubs (`NotImplementedError` body).
- Judgment steps → full implementations: Anthropic SDK call + Pydantic validation + `CACHE` + full `ON_FAIL` strategy chain.
- `clio_runtime/cache.py` copied verbatim — same on-disk cache format as the bash target; caches are interchangeable between targets.
- Dependencies: `anthropic>=0.40`, `pydantic>=2`. Runtime needs only Python 3.12+.
- Emitted SDK calls include a strict JSON-only system prompt to align behavior with `claude -p`.

### Tests

- Golden tests for: skeleton, contracts, exact stubs, cache wrapping, full strategy chain, fallback resolution.
- Pydantic round-trip validation tests.
- SDK monkeypatch tests for retry/escalate/fallback behavior (no network).
- E2E gated test: real `claude -p`, cache replay verified via SDK monkeypatch on the second run.

### Out of scope (planned for later)

Async / parallel step execution, streaming responses, tool_use, provider-neutral SDK, multi-FLOW per source, persistent state.

## v0.2.0 — 2026-05-03

Adds reproducibility (`CACHE`) and resilience (`ON_FAIL`) on `judgment` steps.

### Language

- `CACHE: on | off | ttl(<int><s|m|h|d>)` — judgment steps only.
- `ON_FAIL: <strategy> (then <strategy>)*` — judgment steps only. Strategies:
  - `retry(N)` — N additional attempts on the current model
  - `escalate` — one attempt on the next model in `RESOURCES.models`
  - `fallback(<step_name>)` — run a different STEP with identical TAKES/GIVES; cycles rejected at compile time
  - `abort("<msg>")` — stop with a clear error
- Fallback compat is checked structurally at IR build time (TAKES name+type and GIVES name+type must match).
- Implicit abort if all strategies are exhausted without a terminal `abort`.

### Runtime

- New `clio_runtime/cache.py` (key = SHA256 over step+model+rendered_prompt+inlined_schema). Atomic file writes. Project-local `.cache/` (override via `CLIO_CACHE_DIR`).
- `run.sh` gains a `_clio_run_attempt` bash helper at the top (one definition per emitted project).
- Bash variable names are now suffixed with the step index (`PROMPT_02`, `RESPONSE_02`, …) to avoid collision in multi-judgment-step flows.

### Tests

- 12 new unit tests for `cache.py`.
- Parser, IR, and emitter tests for CACHE, ON_FAIL, and fallback resolution.
- E2E test now validates that a second run within TTL produces zero `claude -p` invocations (verified via PATH-stub).

### Out of scope (planned for later)

`ON_FAIL` on `MODE: exact`, `CONFIDENCE`, `VALIDATE`, control-flow keywords (`FOR EACH`/`WHILE`/`IF`/`MATCH`), the optimizer, alternative emitter targets, NL → `.clio` frontend.

## v0.1.0 — 2026-05-03

First runnable slice. Compiles a strict subset of the CLIO language to a Claude Code project that runs end-to-end against `claude -p`.

### Language

- **Declarations**: `STEP`, `CONTRACT`, `FLOW`, `RESOURCES`
- **Step fields**: `TAKES`, `GIVES`, `MODE` (`exact` | `judgment`); duplicate fields rejected
- **Contract fields**: `SHAPE`, `ASSERT` (mini expression language: `len(x) > N`, `==`, `!=`, `>=`, `<=`, `>`, `<`, with literal int / float / str)
- **Types**: primitives (`int`, `float`, `str`, `bool`), `List<T>`, records `{f: T, ...}`, `enum(a|b|c)`, `str(max=N)`, `CSV` alias, contract refs by name
- **Flow**: sequential chain `->`, step calls with kwargs (string literals + state references)
- **Resources**: `target` (`claude-cli` only), `models` (first one becomes `claude -p --model`)

### Compiler

- Hand-written recursive-descent parser, frozen-dataclass AST and IR, single emitter for `claude-cli`. Zero LLM-framework dependencies.
- Inter-step type-checking on the FLOW chain.
- Errors carry `line:col` from the source.
- CLI: `python -m clio compile <source.clio> --target claude-cli --output <dir>` and `python -m clio check <source.clio>`.

### Emitted project

- `CLAUDE.md`, `.claude/hooks.json` (placeholder), `contracts/<name>.schema.json`, `steps/NN_name.{py,prompt,schema.json}`, `clio_runtime/{validate,substitute}.py`, `state.json`, `run.sh`, `README.md`.
- `run.sh` orchestrator: bash, dynamic Python 3.12+ detection (override via `PYTHON=`), state passed via `state.json` + `jq`, judgment steps invoke `claude -p --model <model> --output-format text` with the schema inlined directly into the prompt; markdown code-fences are stripped from the response before validation.
- Contract validation: `jsonschema.Draft202012Validator` + the `referencing` library (no deprecated `RefResolver`); `x-clio-assert` is a closed JSON-AST evaluated by an explicit walker (no `eval`).

### Tests

- 61 unit + golden-file tests run by default.
- `tests/test_e2e.py` is gated by `CLIO_E2E=1` and exercises the full pipeline against a real `claude -p`. Manually verified.

### Out of scope (planned for later)

`MODE: auto`, `LANG: auto`, `CACHE`, `VALIDATE`, `ON_FAIL`, `CONFIDENCE`, `FOR EACH`, `WHILE`, `IF` / `ELSE`, `MATCH`, the optimizer (batching, model routing, context budget), other emitter targets (`python`, `docker`, `rust`, `hybrid`), the natural-language → `.clio` frontend, hooks-based validation. The parser rejects each of these explicitly with a clear "not yet supported in v0.1" error rather than silently ignoring.
