# CLIO — `target: go` emitter — design

**Date**: 2026-05-17
**Sprint**: candidate for v0.20
**Status**: Spec drafted, awaiting user review before writing the implementation plan.

## Motivation

CLIO ships five emitters today (`claude-cli`, `python`, `mcp-server`, `langgraph`, `claude-skill`). All five terminate the runtime in Python (or in shell + Python helpers). For developers and teams whose primary stack is Go — backend services, infrastructure tooling, sidecars — there is no native CLIO target. They must either install a Python interpreter alongside their Go binary, run CLIO behind an MCP/HTTP boundary, or rewrite the flow by hand in Go.

A `target: go` emitter changes that. It takes the same IR and writes a Go module: importable as a package (`flow.Run(ctx, kwargs)`), runnable as a CLI (`cmd/<flow>/main.go`), and self-contained after `go build`. Strategy: this is positioned as the "adoption ecosystem Go" target — feature parity with `target: python` is the goal; idiomatic Go (modules, `context.Context`, `errgroup`, table-driven tests) is the constraint.

`COMPILATION_TARGETS.md` already carries a brief sketch under "`target: go` (Future)" since v0.4. This spec replaces that sketch with a concrete, complete design.

## Decisions made during brainstorm

| Topic | Decision | Rejected alternatives |
|---|---|---|
| Motivation | Adoption ecosystem Go — idiomatic, feature parity with `target: python` | (a) Single static binary distribution sweet spot — too narrow; (b) Concurrence-first (goroutines for parallel exact) — too narrow; (c) Strategic-only (à la langgraph) — too vague |
| Artefact shape | Go module + CLI: importable `flow.Run(ctx, kwargs)` package + `cmd/<flow>/main.go` for `go run` / `go build` | (a) Library-only — loses symmetry with `target: python`; (b) Module + CLI + auto-emitted HTTP server — out of scope, user wraps `flow.Run` themselves |
| LLM SDK | Official Anthropic SDK Go (`github.com/anthropics/anthropic-sdk-go`) **and** official OpenAI Go SDK (`github.com/openai/openai-go`) — dispatched at emit time via `invoke.protocol` | (a) Anthropic-only — fails the parity promise (python target supports both); (b) `net/http` direct — antinomique avec "idiomatic Go" |
| Feature scope | Parity totale day-1 with `target: python` — all IR features, all four `impl.mode`, RESUME, TEST, FLOW composition, logging JSONL | (a) MVP ciblé (exact/judgment/CACHE/ON_FAIL/FOR EACH/MATCH only) — leaves users routing to `target: python`; (b) MVP élargi (+ rest + shell + RESUME) — same critique, smaller gap |
| Validation lib | `github.com/santhosh-tekuri/jsonschema/v6` (pure Go, draft 2020-12, no CGo) + hand-rolled walker for `x-clio-assert` (port of `clio/runtime/validate.py`) | (a) `xeipuuv/gojsonschema` — older, draft-7 ceiling, slower; (b) Hand-rolled JSON Schema — reinvents draft 2020-12 corner cases for no gain |
| Concurrency primitive | `golang.org/x/sync/errgroup.Group` with `g.SetLimit(10)` (mirror python cap=10 from `FOR EACH PARALLEL`) | (a) Channels + worker pool — more code, same semantics, harder to read; (b) `sync.WaitGroup` only — no cancellation propagation, no error aggregation |
| SQLite driver | `modernc.org/sqlite` (pure Go, no CGo — `go build` works on any platform without a C toolchain) | `mattn/go-sqlite3` — popular but requires CGo, complicates cross-compilation |
| Postgres driver | `github.com/jackc/pgx/v5` | `lib/pq` — deprecated upstream in favor of `pgx` |
| MySQL driver | `github.com/go-sql-driver/mysql` | none significant |
| MCP client | `github.com/mark3labs/mcp-go` | none — `mark3labs/mcp-go` is the de-facto Go MCP SDK in 2026 |
| `STEP exact LANG ∈ {python, rust, node, bash}` | Reject at compile time with `E_GO_001`, error message points to `--target python` or `--target claude-skill` (or to `impl.mode: shell` for shell use cases) | (a) Generate a Cgo wrapper invoking CPython — massive complexity, ABI fragility; (b) Subprocess `python -c` / `bash -c` — defeats the "self-contained Go binary" promise; (c) Auto-rewrite to Go — out of scope, a transpiler is a separate product. Note: the python target ignores LANG entirely (always emits Python); Go cannot afford that latitude because the surrounding code must compile in Go. |
| `STEP exact LANG: go` | Native Go function, stub body `panic("fill me in: <step>")` matching the python `NotImplementedError` convention | Generate an interface + dependency injection — overkill for stubs |
| `STEP exact LANG: auto` (default) | Map to Go | Map to a polyglot dispatcher — defeats the target's premise |
| `invoke.mode: cli` (i.e., subprocess `claude -p`) | Reject at compile time with `E_GO_002` | Embed `claude` binary fetch logic — out of scope |
| `invoke.protocol: bedrock` / `vertex` | Reject at compile time with `E_GO_003`, points to `--target python` | Add bedrock/vertex Go SDK day-1 — defer to a follow-up issue |
| Runtime helpers distribution | Embedded as **Go source templates** inside `clio/emitters/_go_helpers.py`, written verbatim into `<output>/clio_runtime/<pkg>/` at emit time. Same model as python target's `clio/runtime/*.py` copy-verbatim convention. | (a) Vendored Go module published on its own (e.g. `github.com/clio-lang/runtime-go`) — operational burden (versioning, releases) for marginal gain; (b) `go:embed` directive bundling — possible later optimization, no functional difference |
| Cache layout | Same on-disk layout as `target: python` and `target: claude-cli`: `<output>/.cache/<step_name>/<sha256>.json` with the same SHA256 key derivation. Cache files interchangeable across targets. | Go-specific binary format — breaks the cross-target cache promise |
| Logging | Same JSONL event taxonomy as python (`flow_start`, `flow_end`, `step_start`, `step_end`, `parallel_block_start`, `parallel_block_end`), emitted via `clio_runtime/logs` when `CLIO_LOG=1`. Field names identical so downstream tools work cross-target. | Use `log/slog` directly — fine for handler style but doesn't match the python event taxonomy; we want byte-identical JSON lines for cross-target tooling |
| RESUME | Same `--from-step N` + `CLIO_STATE_FILE` env var + atomic state.json write between top-level chain items. The emitted CLI reads/writes state.json identically to python target. | Per-step state files — fragmentation, breaks cross-target swap |
| TEST blocks | Emit one `tests/<test_name>_test.go` per TEST IR using `t.TempDir()`, `t.Setenv("CLIO_STATE_FILE", ...)`, and table-driven predicate assertions. `go test ./tests/...` runs them. | Skip TEST emission — leaves parity hole; `go test` is the idiomatic Go check, mandatory |

## Architecture

### New files

```
clio/emitters/
  go.py                          # GoEmitter(BaseEmitter)
  _go_helpers.py                 # Go-specific renderers + embedded runtime templates
clio/emitters/_shared_utils.py   # MODIFIED: extract any helpers needed by both python and go
                                 #   (e.g., contract-name → struct-name normalisation, type rendering)
tests/test_emitters/
  test_go.py                     # mirror of test_python.py — emission + go-build smoke
docs/
  COMPILATION_TARGETS.md         # update: `go` moves from Future to Implemented; replace the existing
                                 #   sketch with the canonical entry
  manual/04-targets.md           # add `go` column to the feature matrix
  manual/03-cookbook.md          # new recipe: "Compile a flow to a Go binary"
  manual/06-troubleshooting.md   # entries for E_GO_001/002/003 + "missing go toolchain" + "modernc.org/sqlite vs cgo"
  LANGUAGE_SPEC.md               # add Go to the table of targets supporting each impl.mode
examples/
  mvp_go.clio                    # an example flow tuned for the go target (LANG: go on exact)
```

### Emitter interface

Same `BaseEmitter.emit(graph: FlowGraph, output_dir: Path, *, source_path: Path | None = None) -> None`. The `source_path` parameter is accepted and ignored (consistent with python, mcp-server, langgraph). Registered in `cli.py`'s target table.

CLI:
```bash
python -m clio compile flow.clio --target go --output ./go-out
```

### Emitted output structure

```
<output-dir>/
  go.mod                              # module <pkg>; go 1.22; require anthropic-sdk-go, openai-go (only when needed)
  go.sum                              # generated by `go mod tidy` post-emit (or committed by the user)
  README.md                           # quickstart: `go run ./cmd/<flow>` + import example
  cmd/<flow>/main.go                  # CLI: flag --kwargs (JSON), --from-step, --state-file, --log
  contracts/contracts.go              # one struct per CONTRACT + Validate(ctx) method
  flow/flow.go                        # func Run(ctx, kwargs map[string]any) (state map[string]any, error)
  steps/
    NN_<exact>.go                     # exact step body — panic("fill me in") stub
    NN_<judgment>.go                  # judgment step body — SDK call + cache + ON_FAIL chain
    NN_<rest>.go                      # REST step — uses clio_runtime/rest
    NN_<sql>.go                       # SQL step — uses clio_runtime/sql
    NN_<mcp>.go                       # MCP-tool step — uses clio_runtime/mcpclient
    NN_<shell>.go                     # shell step — os/exec.CommandContext
  subflows/<subflow>.go               # one file per called sub-FLOW (FLOW composition)
  clio_runtime/                       # ported verbatim from clio/runtime/ Python
    cache/cache.go                    # SHA256 on-disk, layout-compatible with python/claude-cli
    logs/logs.go                      # JSONL events, byte-identical to python target
    rest/rest.go                      # impl.mode rest — net/http + ${var} substitution
    sql/sql.go                        # impl.mode sql — database/sql + lazy driver imports
    mcpclient/mcpclient.go            # impl.mode mcp_tool — mark3labs/mcp-go wrapper
    validate/validate.go              # jsonschema/v6 + x-clio-assert walker
    substitute/substitute.go          # ${var} + env: prefix
  tests/                              # one *_test.go per TEST block
    <test_name>_test.go
```

### Mapping IR → Go

| IR element | Emitted Go artifact | Notes |
|---|---|---|
| `CONTRACT name { shape, assert }` | `type Name struct { ... \`json:"..."\` }` + `func (n *Name) Validate(ctx context.Context) error` calling `clio_runtime/validate.Schema(ctx, schemaJSON, n)` | Schema JSON embedded as a `const` string in the same file. `x-clio-assert` runs after the schema check. |
| `STEP exact LANG: go` (or `auto`) | `func StepX(ctx context.Context, in StepXIn) (StepXOut, error) { panic("fill me in: x") }` | Stub. User fills body. |
| `STEP exact LANG ∈ {python, rust, node, bash}` | **Compile-time reject** with `E_GO_001` | Error message routes user to `--target python`, `--target claude-skill`, or `impl.mode: shell` for shell glue. |
| `STEP judgment` | `func StepX(ctx context.Context, in StepXIn) (StepXOut, error)` — body: cache lookup → LLM SDK call → JSON unmarshal → contract validate → cache store. Wrapped in ON_FAIL chain. | LLM SDK selected at emit time via `invoke.protocol`. |
| `FLOW` (top-level) | `flow/flow.go: func Run(ctx, kwargs map[string]any) (state map[string]any, error)` calling steps in chain order | Returns the final state map. |
| `FLOW composition` (v0.17) | `subflows/<sub>.go: func Run(ctx, in SubIn) (SubOut, error)` callable from `flow.go` and from other subflows | Sub-FLOW signature respected: `TAKES` → `SubIn` struct, `GIVES` → `SubOut` struct. State merged flat on caller, mirror python target. |
| `IF ... THEN ... ELSE` | `if condGo(state) { ... } else { ... }` | Condition rendered via shared `_python_condition_expr` adapted to Go (`state["x"]` accessors). |
| `MATCH ... { CASE v1: ...; CASE v2: ... }` | `switch state["x"] { case v1: ...; case v2: ... }` | Identifier sanitization shared with python target via `_shared_utils._to_field_name`. |
| `WHILE cond { ... }` | `for condGo(state) { ... }` | |
| `FOR EACH item IN coll` (sequential) | `for _, item := range stateColl { ... }` | |
| `FOR EACH PARALLEL item IN coll` | `g, ctx := errgroup.WithContext(ctx); g.SetLimit(10); for _, item := range stateColl { item := item; g.Go(func() error { ... }) }; if err := g.Wait(); err != nil { return ... }` | Cap = 10, identical to python target's `ThreadPoolExecutor(max_workers=10)`. |
| `ON_FAIL: retry(N) then escalate then fallback(stepY) then abort("msg")` | Wrapping `for attempt := 0; attempt < N; attempt++ { ... if err == nil { break }; backoff(attempt) }` then `if err != nil { /* escalate: switch model */ }`, then `if err != nil { out, err = StepY(ctx, in) }`, then `if err != nil { return nil, fmt.Errorf("...: %w", err) }` | Mirror `_emit_attempt_block` from python helpers. |
| `RESCUE` | `defer func() { if r := recover(); r != nil { ... } }()` + retry/escalate handling | Idiomatic Go panic recovery — explicit. |
| `CACHE: on` / `ttl(...)` | `key := cache.Key(stepName, model, prompt, schemaJSON); if v, ok := cache.Lookup(cacheDir, stepName, key, ttl); ok { return parse(v), nil }; ...; cache.Store(cacheDir, stepName, key, model, raw)` | On-disk format byte-identical to python target. |
| `RESOURCES.models` | `var modelMap = map[string]string{"haiku": "claude-haiku-4-5-20251001", "sonnet": "claude-sonnet-4-6", "opus": "claude-opus-4-7"}` | Same short→ID mapping as python target. |
| `RESUME` (CLI flag) | `cmd/<flow>/main.go` parses `--from-step N`, loads state from `CLIO_STATE_FILE`, skips items `1..N` in `flow.Run` | Atomic write between top-level chain items: write to `<state>.tmp`, `os.Rename(tmp, state)`. |
| `TEST` block | `tests/<test_name>_test.go` — `func Test<Name>(t *testing.T) { t.Setenv("CLIO_STATE_FILE", filepath.Join(t.TempDir(), "state.json")); state, err := flow.Run(context.Background(), kwargs); require.NoError(t, err); ... predicate asserts ... }` | Predicates rendered via a shared `_predicate_expr_go` mirror of `_predicate_expr` from python helpers. |
| `impl.mode rest` | `clio_runtime/rest.Do(ctx, RestSpec{URL, Method, Headers, Body, Auth})` returning `map[string]any` parsed from the response body | Lazy `net/http.Client` reused per process. ${var} substitution via `clio_runtime/substitute`. |
| `impl.mode sql` | `clio_runtime/sql.Query(ctx, dbName, query, bindings)` returning `[]map[string]any` for SELECT, `int64` rowcount for write ops | Long-lived per-DB connections, `sync.Mutex` per connection, lazy driver registration. |
| `impl.mode mcp_tool` | `clio_runtime/mcpclient.CallTool(ctx, server, toolName, args)` returning `map[string]any` | Lazy per-server clients (stdio subprocess or HTTP session), closed via `runtime.SetFinalizer` or explicit `Close()` on shutdown. |
| `impl.mode shell` | `os/exec.CommandContext(ctx, shell, "-c", body).Output()`; stdout parsed as JSON if `GIVES` schema is a struct, raw string otherwise | |

### Library choices (recap)

| Need | Library | Selection rationale |
|---|---|---|
| Anthropic SDK | `github.com/anthropics/anthropic-sdk-go` | Official, idiomatic, supports streaming and tool use. |
| OpenAI SDK | `github.com/openai/openai-go` | Official since 2024. |
| JSON Schema | `github.com/santhosh-tekuri/jsonschema/v6` | Pure Go, draft 2020-12, fast. |
| Concurrency | `golang.org/x/sync/errgroup` | Stdlib-adjacent. |
| MCP client | `github.com/mark3labs/mcp-go` | De-facto Go MCP SDK. |
| SQLite | `modernc.org/sqlite` | Pure Go (no CGo) — `go build` works on every platform without a C toolchain. |
| Postgres | `github.com/jackc/pgx/v5` | Reference modern driver (lib/pq is deprecated upstream). |
| MySQL | `github.com/go-sql-driver/mysql` | Reference driver. |

Licenses to be verified at the writing-plans stage before pinning in the emitted `go.mod` (all are open-source as of 2026-05; the spec does not pre-commit to specific license claims).

### Refused combinations (compile-time errors)

| Error code | Condition | Message + remediation |
|---|---|---|
| `E_GO_001` | Any STEP `exact` with `LANG ∈ {python, rust, node, bash}` | `"target: go can only embed exact step bodies in Go (LANG: go or LANG: auto). For Python/Bash/etc., use --target python (or --target claude-skill to let the LLM host drive the flow); for shell glue specifically, use impl.mode: shell which target: go supports natively."` |
| `E_GO_002` | `invoke.mode: cli` on any judgment step | `"target: go does not subprocess 'claude -p'. Use --target python, --target mcp-server, or --target claude-cli."` |
| `E_GO_003` | `invoke.protocol: bedrock` or `vertex` | `"target: go ships Anthropic and OpenAI SDKs only at v0.20. Use --target python for Bedrock/Vertex."` |
| `E_GO_004` | Source with no `EXPOSE FLOW` and no top-level FLOW | `"target: go needs at least one FLOW to emit cmd/<flow>/main.go."` (same shape as `E_MCP_001`) |

## Data flow

### State passing
- `flow.Run` receives `kwargs map[string]any` (parsed from `--kwargs` JSON or passed programmatically).
- It seeds an internal `state := map[string]any{}` and merges kwargs into it.
- Each step is called with a typed input struct extracted from `state` via the contracts package; its typed output is then re-merged into `state` under the step name namespace (`state["<step>"]["<field>"]`).
- When `RESUME` is active, state is loaded from disk before the first step and saved atomically (tmp + rename) after each top-level chain item.

### LLM call shape (judgment step, anthropic protocol)
```go
client := anthropic.NewClient(option.WithAPIKey(os.Getenv("ANTHROPIC_API_KEY")))
prompt := buildPrompt(state)
key := cache.Key(stepName, model, prompt, schemaJSON)
if cached, ok := cache.Lookup(cacheDir, stepName, key, ttl); ok {
    return unmarshalAndValidate(cached)
}
resp, err := client.Messages.New(ctx, anthropic.MessageNewParams{
    Model:     anthropic.F(model),
    MaxTokens: anthropic.F(int64(8192)),
    System:    anthropic.F(systemPromptJSONOnly),
    Messages:  anthropic.F([]anthropic.MessageParam{anthropic.NewUserMessage(anthropic.NewTextBlock(prompt))}),
})
// extract text, unmarshal, validate, store cache, return
```

### LLM call shape (judgment step, openai protocol)
Same shape, `openai.NewClient(...)` + `client.Chat.Completions.New(...)`. Selection at emit time via `invoke.protocol`.

## Error handling

| Error category | How it surfaces |
|---|---|
| Compile-time (parser/IR/refused combos) | Python emitter raises with the source line number (existing pattern); `E_GO_*` codes documented above |
| Runtime contract violation | `Validate` returns a `*ValidationError` with field path and offending value. Caught by the ON_FAIL chain when configured. |
| LLM API errors (rate limit, 5xx, timeout) | `errors.Is(err, anthropic.ErrRateLimit)` triggers backoff inside the retry loop. Unknown errors propagate to ON_FAIL `escalate` (switch model) or `fallback` (call alternate step) or `abort` (return wrapped error). |
| SQL connection errors | Surfaced as `*sql.DBError` (wrapped). ON_FAIL handles. |
| MCP transport errors | Surfaced as `mcpclient.TransportError`. ON_FAIL handles. |
| Context cancellation | All blocking calls take `ctx`; cancellation propagates and unwinds via `errgroup`. Top-level CLI traps `os.Interrupt` and cancels the context. |
| Panics in user-filled step bodies | Caught at the flow boundary via `defer recover()` only if a `RESCUE` block is declared in source. Otherwise the panic propagates — same as python `NotImplementedError`. |

## Testing

Three layers, mirroring the python target's test strategy.

### 1. Emission unit tests (`tests/test_emitters/test_go.py`)
Per-feature emission stability via golden-file diffing. One test per IR feature: STEP exact (Go/bash), STEP judgment (anthropic/openai), CONTRACT, FOR EACH, FOR EACH PARALLEL, MATCH, IF, WHILE, RESCUE, ON_FAIL chain, CACHE, FLOW composition, RESUME (CLI flag plumbing), TEST emission, refused combinations (E_GO_001..004).

### 2. Compile-check tests (`tests/test_emitters/test_go.py::test_go_build_passes`)
For each fixture, run `go build ./...` in a subprocess against the emitted output. Fails the test if the emitted Go does not compile. Analogous to the `python -m compileall` check existing for python target.

**Skip condition**: if `go` is not on PATH, the test is skipped with a message ("install Go 1.22+ to enable target: go compile-check"). The test does **not** download Go.

### 3. Smoke end-to-end (`tests/test_emitters/test_go_e2e.py`)
One end-to-end test: compile a minimal `.clio` to Go, `go build`, run the binary with mock kwargs (no real LLM call — exact-only flow), verify `state.json` content. Marked `e2e_go`, opt-in via pytest marker (skipped by default like `e2e_llm` for `clio gen`).

### 4. Manual targets (no automation)
- A judgment-step flow compiled against a real Anthropic key, run, output verified manually — documented in `docs/manual/03-cookbook.md`. (Same posture as python's manual smoke.)

## Build sequence (proposed for the plan)

The implementation plan will decompose this into TDD-driven tasks for `superpowers:subagent-driven-development`. Rough phasing:

1. **Foundation** — Skeleton:
   - `clio/emitters/go.py: GoEmitter` registered in CLI map (no-op emit initially).
   - `clio/emitters/_go_helpers.py` with Go type renderer (`_type_to_go`) and contract→struct renderer.
   - `go.mod` + `cmd/<flow>/main.go` shell that prints `"hello from <flow>"`.
   - Smoke test: `go build` passes on a trivial fixture.

2. **Contracts & exact steps** — Emit `contracts/contracts.go` with `Validate()` methods, embedded JSON Schema; emit `steps/NN_<name>.go` stubs for exact (Go and bash); emit `flow.go` chaining stubs sequentially.

3. **Judgment steps + Anthropic SDK** — `steps/NN_<name>.go` for judgment with anthropic-sdk-go, cache lookup/store, JSON Schema validation. Refused-combo error E_GO_002.

4. **Control flow** — IF, MATCH, WHILE, FOR EACH (sequential), RESCUE.

5. **Parallel FOR EACH** — `errgroup.WithContext` + `SetLimit(10)`.

6. **ON_FAIL chain** — retry/escalate/fallback/abort wrapping for every step kind.

7. **OpenAI SDK** — second LLM SDK path, dispatcher at emit time via `invoke.protocol`. Refused-combo E_GO_003.

8. **FLOW composition** — `subflows/<sub>.go` + cross-call wiring.

9. **Runtime modes — REST** — `clio_runtime/rest/rest.go` + `clio_runtime/substitute/substitute.go` + steps `NN_<rest>.go`.

10. **Runtime modes — Shell** — `os/exec.CommandContext` + steps `NN_<shell>.go`.

11. **Runtime modes — SQL** — `clio_runtime/sql/sql.go` (3 lazy drivers) + steps `NN_<sql>.go`.

12. **Runtime modes — MCP** — `clio_runtime/mcpclient/mcpclient.go` (mark3labs/mcp-go wrapper) + steps `NN_<mcp>.go`.

13. **RESUME** — `--from-step` flag + atomic state.json write + load.

14. **Logging** — `clio_runtime/logs/logs.go` (JSONL events byte-identical to python target).

15. **TEST blocks** — `tests/<test_name>_test.go` emission.

16. **Refused combos** — E_GO_001/002/003/004 hardening + error tests.

17. **Docs + example** — COMPILATION_TARGETS canonical entry, LANGUAGE_SPEC table, manual cookbook recipe + troubleshooting entries, `examples/mvp_go.clio`.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Go type system mismatch for anonymous shapes like `List<{name: str, revenue: float}>` | High | Emit anonymous structs inline (`[]struct { Name string \`json:"name"\`; Revenue float64 \`json:"revenue"\` }`). Tested explicitly. |
| `modernc.org/sqlite` performance vs CGo driver | Low | Acceptable for CLIO's batch-style flows. Document the choice. |
| anthropic-sdk-go API drift mid-sprint | Medium | Pin minor version in emitted `go.mod`; bump in a dedicated task. |
| `errgroup.SetLimit` semantics differ subtly from `ThreadPoolExecutor(max_workers=N)` (the former blocks `g.Go(...)` calls when limit reached; the latter queues tasks immediately and runs them as workers free up) | Low | Acceptable — same end behavior. Documented in the cookbook recipe so users understand. |
| `x-clio-assert` walker port introduces bugs | Medium | Direct line-by-line port from `validate.py`, with the existing python tests reused as a behavioural reference (one Go test per python test). |
| Cross-platform `go build` on `modernc.org/sqlite` (esp. ARM macOS / Windows) | Low | `modernc.org/sqlite` is pure Go; CI matrix tests Linux/macOS/Windows. |
| User has `LANG: python` exact steps in an existing flow they want to compile to Go | Medium | E_GO_001 message explicit; troubleshooting entry walks through migrating to `LANG: go` or routing to `--target python`. |
| Lock-step parity sprint takes too long, blocks other v0.20 work | Medium | Phased plan above lets us release a partial Go target (steps 1–8) early if needed, with refused-combos for the remaining `impl.mode` paths until phase 9–12 lands. Decision deferred to the writing-plans stage. |

## Out of scope (explicitly)

- HTTP server target shape (auto-emit `cmd/server/main.go` from `EXPOSE FLOW`). User wraps `flow.Run` themselves if they want HTTP.
- Bedrock/Vertex SDKs.
- Go-specific `LANG: go` syntactic sugar in `.clio` source (e.g., embedding Go code blocks inline). Out of scope — exact step bodies remain user-filled stubs.
- A `clio go-doctor` subcommand checking the host's Go toolchain. The `clio doctor` command (v0.15) can grow a Go-aware check in a follow-up.
- `clio import` from a Go-target emitted module (reverse direction). The v0.19 importer is skill-specific.
- Cross-target binary cache compatibility tests (we assert byte-identical layout in tests but don't run python+go interleaved end-to-end). Follow-up if anyone reports drift.

## Roadmap (post-v0.20 follow-ups)

- **Bedrock/Vertex Go SDK** — when the Go SDKs mature, drop E_GO_003 and add the dispatch.
- **HTTP server emit** — `EXPOSE FLOW myFlow AS http` directive triggers `cmd/server/main.go` emission. Possibly a separate `target: fastapi-go` or just an `--http-server` flag on `target: go`.
- **`go-doctor` integration** — `clio doctor` learns to check `go version`, `GOPATH`, presence of Postgres/MySQL drivers if the flow uses them.
- **Embed runtime via `go:embed`** — optimization, doesn't change behavior.

## Open questions (deferred to user / writing-plans stage)

- Should we publish runtime helpers as a separate `github.com/clio-lang/runtime-go` module instead of copy-verbatim? Decision deferred — copy-verbatim ships first, vendored module is a v0.21+ option.
- Should we ship a CI matrix in this repo that runs `go build` against every emitted fixture? Yes for the compile-check tests; full e2e matrix (Linux/macOS/Windows) is a follow-up.
- Should the example flow `examples/mvp_go.clio` mirror `examples/mvp.clio` 1:1, or introduce a Go-specific feature showcase (e.g., a `LANG: bash` step)? Decision deferred to the example task.
