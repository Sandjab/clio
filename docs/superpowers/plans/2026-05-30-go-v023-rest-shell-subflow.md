# Go target v0.23 — REST + shell + sub-flow composition — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lift `E_GO_006` (FLOW composition), `E_GO_007` (`impl.mode: rest`), and `E_GO_008` (`impl.mode: shell`) in the Go emitter, reaching parity for all stdlib-only constructs.

**Architecture:** REST/shell slot into the existing typed-state model as new step-body renderers (`net/http`, `os/exec`, a shared `${var}` substitute runtime). Sub-flow composition uses a parity-first flat-merge (`for k,v := range run<Name>(...)`) made type-safe by three emitter-side maps: a per-flow `state_field_to_step`, a FlowCall boundary extension, and a new `take_field_to_gotype` for `@take` reads.

**Tech Stack:** Python 3.12 emitter (generates Go), Go stdlib (`net/http`, `os/exec`, `encoding/json`) + the already-present `golang.org/x/sync/errgroup`, pytest (string-grep + real `go build` harness in `tests/test_emitters/test_go_compile.py`).

**Spec:** `docs/superpowers/specs/2026-05-30-go-v023-rest-shell-subflow-design.md`
**Branch:** `feat/go-v023-rest-shell-subflow` (already created)

---

## Phase ordering & integration notes (read before starting)

This plan was drafted as six independent phases around a shared signature contract. Honour these cross-phase reconciliations (surfaced during plan self-review):

1. **Phase order = 1 → 2 → 3 → 4 → 5 → 6** (matches the spec's build sequence). Phases 4 and 5 are a hard chain: Phase 5 (FlowCall rendering) consumes the three maps + `take_types`/`flows_by_name` params that Phase 4 introduces.

2. **`render_clio_runtime_rest(pkg: str)` — accepted deviation** from the nullary contract signature. The emitted `rest.go` imports `<pkg>/clio_runtime/substitute`, so the renderer needs the module name (exactly like judgment steps import `<pkg>/clio_runtime/cache`). `go.py` passes the already-computed `pkg`.

3. **The entry-flow TAKES-seeding retrofit does NOT perturb existing goldens.** Phase 4 verified that none of `go_minimal`/`go_judgment`/`go_parallel`/`mvp_go` declare entry-FLOW TAKES (their FLOWs are unsigned), so seeding `state["<take>"]=kwargs["<take>"]` `for f in flow.takes` adds zero lines. The spec's "regenerate goldens" caveat is therefore inert on current fixtures; the new `go_entry_takes.clio` fixture is what exercises the retrofit. Regenerate-and-eyeball still runs as a guard (expect zero diff on the four existing goldens).

4. **`go.py` step-stub loop is touched by Phases 1, 2, and 3.** Phase 3 replaces step *collection* (`_collect_reachable_steps` over all flows); Phases 1/2 add *dispatch* branches (`RestImplIR→render_rest_step_go`, `ShellImplIR→render_shell_step_go`) in the loop body. When Phase 3 rewrites the loop, **preserve** the REST/shell dispatch from Phases 1/2 — the final loop iterates `_collect_reachable_steps(graph)` and dispatches on `step.mode`/`step.impl` across exact/judgment/rest/shell.

5. **Refusal lifting.** As drafted, Phases 1/2/5 unit-test renderers by **monkeypatching `validate_graph_for_go`**, and Phase 6 removes the refusals. **Recommended simplification (executor's call):** lift each refusal *in its own feature phase* — `RestImplIR` in Phase 1, `ShellImplIR` in Phase 2, the `FlowCallIR`/`len(flows)>1` refusals in Phase 5 — so each phase reaches a real end-to-end `clio compile --target go` without monkeypatching, leaving Phase 6 to only re-narrow `_GO_E_006_MSG` (multi-GIVES parallel collector) + refresh stale strings + docs. If you take this path, drop the monkeypatch from the Phase 1/2/5 integration tests.

6. **`_render_chain_item` signature grows in Phase 4, is used in Phase 5.** Phase 4 adds BOTH `take_types: dict[str, str] = {}` and `flows_by_name: dict[str, FlowIR] | None = None`; Phase 5 fills in the FlowCall arm that uses `flows_by_name`. Keep the param names identical across both phases.

7. **`examples/flow_composition.clio` is `target: python`.** Any `go build` test that reuses it must force `--target go`; the primary sub-flow test vehicles are the new `go_subflow_seq/parallel/collision/abc.clio` fixtures (Phase 5), authored with `target: go`.

8. **Verified accessors** (do not re-guess): `ForEachIR.parallel: bool` + `ForEachIR.collector: str | None` (`graph.py:294-295`); `_has_parallel` lives in `_shared_utils.py:400` and is already imported into `_go_helpers.py`.

---

## Phase 1 — REST (E_GO_007)

Builds Go REST support: the `clio_runtime/substitute` + `clio_runtime/rest` Go runtime
templates (byte-for-behaviour mirror of `clio/runtime/rest.py`), `render_rest_step_go`, and the
`go.py` wiring that emits those runtimes + dispatches `RestImplIR` steps. Scope of the REST body
shapes lifted in v0.23-go-v1: `JsonBodyIR` (application/json flat dict) and `RawBodyIR` (text/plain),
plus `query`, `headers`, `response_path` traversal, `timeout`, and impl-level `RetryPolicyIR` retry.

> NOTE on body-shape scope: `examples/rest_advanced.clio` uses `FormBodyIR`/`FileBodyIR`/`MultipartBodyIR`,
> which are **NOT** supported by the Go target in this sprint (the `_REST_GO_TEMPLATE` brief in the spec
> lists only `${var}` subst + JSON/raw bodies). Phase 1 therefore uses a **new, JSON+raw-only fixture**
> for all REST tests. The compile-time *refusal* for the unsupported body shapes is owned by the
> refusal-code phase (Phase 6); Phase 1 only emits the supported subset. This is flagged in `notes`.

> NOTE on E_GO_007 unblocking: `render_rest_step_go` and the runtime templates can be unit-tested by
> calling them directly (Tasks 1-4) **before** `validate_graph_for_go` stops refusing REST. The first
> end-to-end `clio compile` of a REST flow only becomes possible once Phase 6 removes the
> `RestImplIR → E_GO_007` refusal — so the `go build` integration task (Task 6) and the go.py wiring
> tests (Task 5) **monkeypatch `validate_graph_for_go` to a no-op**, exercising the emitted module
> directly rather than going through the refusal. This keeps Phase 1 independently shippable.

---

### Task 1: `render_clio_runtime_substitute()` Go template (package substitute, `Apply`)

The shared `${var}` substitution helper used by both REST (and, in Phase 2, shell). Mirrors
`clio/runtime/rest.py::subst` semantics: whole-string `env:NAME` → `os.LookupEnv` (error if unset);
`${var}` occurrences → `takes[var]` stringified (error if absent); inline `env:` is plain text.

**Files:**
- Modify: `clio/emitters/_go_runtime_templates.py` (append after `render_clio_runtime_cache`, ~line 276)
- Test: `tests/test_emitters/test_go.py` (append at end of file)

- [ ] **Step 1: Write the failing test**
```python
def test_render_clio_runtime_substitute_shape():
    from clio.emitters._go_runtime_templates import render_clio_runtime_substitute

    src = render_clio_runtime_substitute()
    assert src.startswith("package substitute\n")
    assert "func Apply(token string, takes map[string]any) (string, error)" in src
    assert "os.LookupEnv" in src
    assert "regexp.MustCompile(`\\$\\{([a-zA-Z_][a-zA-Z0-9_]*)\\}`)" in src
    assert "not found in TAKES" in src
    assert "is not set" in src
    assert render_clio_runtime_substitute() == src
```

- [ ] **Step 2: Run test to verify it fails**
```bash
uv run pytest tests/test_emitters/test_go.py::test_render_clio_runtime_substitute_shape -q
```
Expected: `ImportError: cannot import name 'render_clio_runtime_substitute' from 'clio.emitters._go_runtime_templates'` (collection error / 1 failed).

- [ ] **Step 3: Write minimal implementation**
Append to `clio/emitters/_go_runtime_templates.py` (after the `render_clio_runtime_cache` definition):
```python
_SUBSTITUTE_GO_TEMPLATE = '''package substitute

// Auto-generated by CLIO. Mirrors clio/runtime/rest.py::subst so REST and
// shell steps interpolate ${var} / resolve env:NAME identically across targets.

import (
\t"fmt"
\t"os"
\t"regexp"
)

var (
\tplaceholderRe = regexp.MustCompile(`\\$\\{([a-zA-Z_][a-zA-Z0-9_]*)\\}`)
\tenvWholeRe    = regexp.MustCompile(`^env:([A-Z_][A-Z0-9_]*)$`)
)

// Apply substitutes ${var} placeholders from `takes` and resolves a whole-string
// `env:NAME` against the process environment. Inline `env:` (not the whole string)
// is treated as plain text. Returns an error when a referenced var is absent from
// `takes` or a referenced env var is unset (parity with rest.py's KeyError).
func Apply(token string, takes map[string]any) (string, error) {
\tif m := envWholeRe.FindStringSubmatch(token); m != nil {
\t\tname := m[1]
\t\tv, ok := os.LookupEnv(name)
\t\tif !ok {
\t\t\treturn "", fmt.Errorf("impl.rest: env var %q is not set", name)
\t\t}
\t\treturn v, nil
\t}
\tvar subErr error
\tout := placeholderRe.ReplaceAllStringFunc(token, func(match string) string {
\t\tkey := placeholderRe.FindStringSubmatch(match)[1]
\t\tv, ok := takes[key]
\t\tif !ok {
\t\t\tif subErr == nil {
\t\t\t\tsubErr = fmt.Errorf("impl.rest: ${%s} not found in TAKES", key)
\t\t\t}
\t\t\treturn match
\t\t}
\t\treturn fmt.Sprintf("%v", v)
\t})
\tif subErr != nil {
\t\treturn "", subErr
\t}
\treturn out, nil
}
'''


def render_clio_runtime_substitute() -> str:
    """Return the body of <output>/clio_runtime/substitute/substitute.go.

    Static template — no per-emission substitution. Emitted only when the flow
    has >=1 REST (or, from Phase 2, shell) step. Shared `${var}` / `env:NAME`
    helper mirroring clio/runtime/rest.py::subst."""
    return _SUBSTITUTE_GO_TEMPLATE
```

- [ ] **Step 4: Run test to verify it passes**
```bash
uv run pytest tests/test_emitters/test_go.py::test_render_clio_runtime_substitute_shape -q
```
Expected: `1 passed`.

- [ ] **Step 5: Commit**
```bash
git add clio/emitters/_go_runtime_templates.py tests/test_emitters/test_go.py
git commit -m "feat(go): add clio_runtime/substitute Go template for \${var}/env:NAME"
```

---

### Task 2: `render_clio_runtime_rest(pkg)` Go template (package rest: retry classification + delay)

The Go mirror of `clio/runtime/rest.py`'s retry helpers. Exports the names in the signature
contract: `Subst`, `RenderDict`, `IsRetryableStatus`, `IsRetryableErr`, `ComputeDelay`,
`ParseRetryAfter`. Semantics behaviour-identical to `rest.py` (cross-target parity). `Subst`
delegates to the `substitute` package (single source for `${var}`), so the template needs the
per-module package name for its import path — hence the `pkg` parameter (see `notes` #1).

**Files:**
- Modify: `clio/emitters/_go_runtime_templates.py` (append after Task 1's `render_clio_runtime_substitute`)
- Test: `tests/test_emitters/test_go.py` (append at end of file)

- [ ] **Step 1: Write the failing test**
```python
def test_render_clio_runtime_rest_shape():
    from clio.emitters._go_runtime_templates import render_clio_runtime_rest

    src = render_clio_runtime_rest("flow")
    assert src.startswith("package rest\n")
    assert "func Subst(template string, takes map[string]any) (string, error)" in src
    assert "func RenderDict(items map[string]any, takes map[string]any) (map[string]any, error)" in src
    assert "func IsRetryableStatus(code int, on []string) bool" in src
    assert "func IsRetryableErr(err error, on []string) bool" in src
    assert "func ComputeDelay(attempt int, base, cap float64, backoff string) time.Duration" in src
    assert "func ParseRetryAfter(v string) (time.Duration, bool)" in src
    assert "code >= 500 && code < 600" in src
    assert "code == 429" in src
    assert '"timeout"' in src
    assert '"network"' in src
    assert 'backoff == "constant"' in src
    assert "substitute.Apply(" in src
    assert render_clio_runtime_rest("flow") == src


def test_render_clio_runtime_rest_imports_substitute_package():
    # Subst must reuse the substitute package, not reimplement ${var}, so the
    # two runtimes can never drift. (Intent: single source for interpolation.)
    from clio.emitters._go_runtime_templates import render_clio_runtime_rest

    src = render_clio_runtime_rest("flow")
    assert "flow/clio_runtime/substitute" in src
```

- [ ] **Step 2: Run test to verify it fails**
```bash
uv run pytest tests/test_emitters/test_go.py::test_render_clio_runtime_rest_shape tests/test_emitters/test_go.py::test_render_clio_runtime_rest_imports_substitute_package -q
```
Expected: `ImportError: cannot import name 'render_clio_runtime_rest'` (collection error / failed).

- [ ] **Step 3: Write minimal implementation**
Append to `clio/emitters/_go_runtime_templates.py`:
```python
_REST_GO_TEMPLATE = '''package rest

// Auto-generated by CLIO. Retry classification + backoff mirror
// clio/runtime/rest.py byte-for-behaviour so a .clio REST step behaves
// identically on the python and go targets.

import (
\t"errors"
\t"fmt"
\t"net"
\t"net/url"
\t"strconv"
\t"time"

\t"{pkg}/clio_runtime/substitute"
)

// Subst delegates ${var} / env:NAME interpolation to the shared substitute pkg.
func Subst(template string, takes map[string]any) (string, error) {{
\treturn substitute.Apply(template, takes)
}}

// RenderDict substitutes string values via Subst; non-string scalars pass through.
// Mirrors clio/runtime/rest.py::render_dict.
func RenderDict(items map[string]any, takes map[string]any) (map[string]any, error) {{
\tout := make(map[string]any, len(items))
\tfor k, v := range items {{
\t\tif s, ok := v.(string); ok {{
\t\t\tr, err := Subst(s, takes)
\t\t\tif err != nil {{
\t\t\t\treturn nil, err
\t\t\t}}
\t\t\tout[k] = r
\t\t}} else {{
\t\t\tout[k] = v
\t\t}}
\t}}
\treturn out, nil
}}

// IsRetryableStatus mirrors clio/runtime/rest.py::is_retryable_response.
func IsRetryableStatus(code int, on []string) bool {{
\tfor _, o := range on {{
\t\tif o == "5xx" && code >= 500 && code < 600 {{
\t\t\treturn true
\t\t}}
\t\tif o == "429" && code == 429 {{
\t\t\treturn true
\t\t}}
\t}}
\treturn false
}}

// IsRetryableErr mirrors clio/runtime/rest.py::is_retryable_exception using the
// Go stdlib: a net.Error with Timeout() classifies as "timeout"; any other
// transport-level url.Error classifies as "network".
func IsRetryableErr(err error, on []string) bool {{
\tif err == nil {{
\t\treturn false
\t}}
\tvar netErr net.Error
\tisTimeout := errors.As(err, &netErr) && netErr.Timeout()
\tfor _, o := range on {{
\t\tif o == "timeout" && isTimeout {{
\t\t\treturn true
\t\t}}
\t}}
\tvar urlErr *url.Error
\tif errors.As(err, &urlErr) && !isTimeout {{
\t\tfor _, o := range on {{
\t\t\tif o == "network" {{
\t\t\t\treturn true
\t\t\t}}
\t\t}}
\t}}
\treturn false
}}

// ComputeDelay mirrors clio/runtime/rest.py::compute_delay (attempt 1-indexed).
func ComputeDelay(attempt int, base, cap float64, backoff string) time.Duration {{
\tvar secs float64
\tif backoff == "constant" {{
\t\tsecs = base
\t}} else {{
\t\tsecs = base * float64(int64(1)<<uint(attempt-1))
\t}}
\tif secs > cap {{
\t\tsecs = cap
\t}}
\treturn time.Duration(secs * float64(time.Second))
}}

// ParseRetryAfter parses a seconds-only Retry-After header (HTTP-date unsupported,
// matching clio/runtime/rest.py::parse_retry_after). Returns (delay, true) on a
// valid non-negative number; negatives clamp to zero.
func ParseRetryAfter(v string) (time.Duration, bool) {{
\tif v == "" {{
\t\treturn 0, false
\t}}
\tf, err := strconv.ParseFloat(v, 64)
\tif err != nil {{
\t\treturn 0, false
\t}}
\tif f < 0 {{
\t\tf = 0
\t}}
\treturn time.Duration(f * float64(time.Second)), true
}}

var _ = fmt.Sprintf
'''


def render_clio_runtime_rest(pkg: str) -> str:
    """Return the body of <output>/clio_runtime/rest/rest.go.

    `pkg` is the emitted module name so the embedded import of the shared
    substitute package resolves. Emitted only when the flow has >=1 REST step.
    Retry classification + backoff mirror clio/runtime/rest.py."""
    return _REST_GO_TEMPLATE.format(pkg=pkg)
```
> If `go build` later reports `fmt imported and not used` (Task 6), the `var _ = fmt.Sprintf` guard
> keeps `fmt` live; `fmt.Errorf` is already used in `IsRetryableErr`-path errors above, so the guard
> can be dropped if redundant — defer that to Task 6.

- [ ] **Step 4: Run test to verify it passes**
```bash
uv run pytest tests/test_emitters/test_go.py::test_render_clio_runtime_rest_shape tests/test_emitters/test_go.py::test_render_clio_runtime_rest_imports_substitute_package -q
```
Expected: `2 passed`.

- [ ] **Step 5: Commit**
```bash
git add clio/emitters/_go_runtime_templates.py tests/test_emitters/test_go.py
git commit -m "feat(go): add clio_runtime/rest Go template (retry classification + backoff parity)"
```

---

### Task 3: `render_rest_step_go` — GIVES-typed REST step (json.Unmarshal + response_path + retry)

Renders `steps/NN_<name>.go` for a REST `RestImplIR` step with a GIVES (typed `Out`). Reuses
`_step_in_out_struct` + the `In`/`Out` + `func <Cls>(ctx, in <Cls>In) (<Cls>Out, error)` skeleton.
Builds the request (URL/query/headers/body with `${var}` subst), runs the impl-level
`RetryPolicyIR` loop, traverses `response_path`, `json.Unmarshal`s into `Out`, validates.

**Files:**
- Modify: `clio/emitters/_go_step_renderers.py` (extend the `clio.ir.graph` import at line 22 with `JsonBodyIR`, `RawBodyIR`, `RestImplIR`; append `render_rest_step_go` + the three helpers after `render_judgment_step_go`, ~line 431)
- Test: `tests/test_emitters/test_go.py` (append at end of file)

- [ ] **Step 1: Write the failing test**
```python
def _rest_gives_graph():
    from clio.parser.parser import parse
    from clio.ir.builder import build_graph
    src = (
        "CONTRACT geo_point\n"
        "  SHAPE: {lat: float, lng: float}\n"
        "STEP geocode\n"
        "  TAKES: address: str\n"
        "  GIVES: location: geo_point\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:           rest\n"
        "    method:         GET\n"
        '    url:            "https://maps.example.com/geocode"\n'
        '    query:          {address: "${address}", key: "env:MAPS_KEY"}\n'
        '    headers:        {Accept: "application/json"}\n'
        '    response_path:  "results[0].geometry.location"\n'
        "    timeout:        30s\n"
        '    retry:          {attempts: 3, on: ["5xx", "429", "timeout"]}\n'
        "FLOW pipeline\n"
        '  geocode(address="123 Main St")\n'
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    return build_graph(parse(src))


def test_render_rest_step_go_gives_typed():
    from clio.emitters._go_step_renderers import render_rest_step_go

    graph = _rest_gives_graph()
    step = next(s for s in graph.steps if s.name == "geocode")
    contracts = {c.name: c for c in graph.contracts}
    out = render_rest_step_go(step, contracts, graph)

    # Skeleton reused from _step_in_out_struct + judgment pattern.
    assert "package steps\n" in out
    assert "type GeocodeIn struct {" in out
    assert 'Address string `json:"address"`' in out
    assert "type GeocodeOut struct {" in out
    assert 'Location contracts.GeoPoint `json:"location"`' in out
    assert "func Geocode(ctx context.Context, in GeocodeIn) (GeocodeOut, error) {" in out

    # Request construction: method + URL subst + query/headers via RenderDict.
    assert 'method := "GET"' in out
    assert 'rest.Subst("https://maps.example.com/geocode", _takes)' in out
    assert 'rest.RenderDict(map[string]any{"address": "${address}", "key": "env:MAPS_KEY"}, _takes)' in out
    assert 'rest.RenderDict(map[string]any{"Accept": "application/json"}, _takes)' in out

    # Impl-level retry loop driven by RetryPolicyIR (NOT ON_FAIL).
    assert "for _i := 0; _i < 3; _i++ {" in out
    assert "rest.IsRetryableStatus(" in out
    assert "rest.IsRetryableErr(" in out
    assert "rest.ComputeDelay(_i+1," in out
    assert "rest.ParseRetryAfter(" in out

    # response_path traversal: results[0].geometry.location → keyed + indexed.
    assert '_data = _m["results"]' in out
    assert "_data = _arr[0]" in out
    assert '_data = _m["geometry"]' in out
    assert '_data = _m["location"]' in out

    # Re-marshal traversed node, unmarshal into the typed Out field, validate.
    assert "json.Unmarshal(_nodeBytes, &out.Location)" in out
    assert "interface{ Validate(context.Context) error }" in out

    # _takes seeds every TAKE for ${var} resolution.
    assert '_takes := map[string]any{"address": in.Address}' in out

    # Imports: stdlib http/json + the rest runtime + contracts.
    assert '"net/http"' in out
    assert "/clio_runtime/rest" in out
    assert "/contracts" in out
```

- [ ] **Step 2: Run test to verify it fails**
```bash
uv run pytest tests/test_emitters/test_go.py::test_render_rest_step_go_gives_typed -q
```
Expected: `ImportError: cannot import name 'render_rest_step_go'` (collection error / failed).

- [ ] **Step 3: Write minimal implementation**
First extend the `clio.ir.graph` import in `clio/emitters/_go_step_renderers.py` (line 22):
```python
from clio.ir.graph import (
    CacheConfigIR,
    ContractIR,
    FlowGraph,
    JsonBodyIR,
    RawBodyIR,
    RestImplIR,
    StepIR,
)
```
Then append to `clio/emitters/_go_step_renderers.py`:
```python
# ---------------------------------------------------------------------------
# REST step renderer (impl.mode: rest)


def _go_json_scalar_kv(key: str, value: object) -> str:
    """Render one JSON-body/query field as a Go map literal entry. bool MUST be
    checked before str (Python bool is not a str, but keep the order explicit).
    Strings stay Go-quoted (subst happens at runtime in RenderDict)."""
    if isinstance(value, bool):
        return f'"{key}": {str(value).lower()}'
    if isinstance(value, str):
        return f'"{key}": {value!r}'
    if value is None:
        return f'"{key}": nil'
    return f'"{key}": {value!r}'


def _go_response_path_traversal(response_path: str, cls: str, step_name: str) -> list[str]:
    """Go lines walking `response_path` over a decoded JSON value `_data` (any).
    Dotted keys assert map[string]any; `[n]` indices assert []any. Empty path → whole body."""
    import re as _re
    parts = _re.findall(r"[^.\[\]]+|\[\d+\]", response_path)
    lines: list[str] = []
    for part in parts:
        if part.startswith("["):
            idx = part[1:-1]
            lines += [
                "\t_arr, _ok := _data.([]any)",
                f"\tif !_ok || {idx} >= len(_arr) {{",
                f'\t\treturn {cls}Out{{}}, fmt.Errorf("{step_name}: response_path index [{idx}] out of range")',
                "\t}",
                f"\t_data = _arr[{idx}]",
            ]
        else:
            lines += [
                "\t_m, _ok := _data.(map[string]any)",
                "\tif !_ok {",
                f'\t\treturn {cls}Out{{}}, fmt.Errorf("{step_name}: response_path on non-object")',
                "\t}",
                f'\t_data = _m["{part}"]',
            ]
    return lines


def _go_rest_body_lines(impl: RestImplIR, cls: str, stepf: str) -> tuple[list[str], bool, bool]:
    """Build the body reader + header defaults. Returns (lines, uses_bytes, uses_strings)
    so the caller can keep imports tight. Only JsonBodyIR + RawBodyIR are supported."""
    lines = ["\tvar _bodyReader io.Reader"]
    uses_bytes = False
    uses_strings = False
    if isinstance(impl.body, JsonBodyIR):
        uses_bytes = True
        items = ", ".join(_go_json_scalar_kv(k, v) for k, v in impl.body.fields)
        lines += [
            f"\t_bodyDict, _bErr := rest.RenderDict(map[string]any{{{items}}}, _takes)",
            "\tif _bErr != nil {",
            f"\t\treturn {cls}Out{{}}, fmt.Errorf({stepf}, _bErr)",
            "\t}",
            "\t_bodyBytes, _ := json.Marshal(_bodyDict)",
            "\t_bodyReader = bytes.NewReader(_bodyBytes)",
            '\tif _, ok := _headers["Content-Type"]; !ok {',
            '\t\t_headers["Content-Type"] = "application/json"',
            "\t}",
        ]
    elif isinstance(impl.body, RawBodyIR):
        uses_strings = True
        lines += [
            f"\t_raw, _rErr := rest.Subst({impl.body.template!r}, _takes)",
            "\tif _rErr != nil {",
            f"\t\treturn {cls}Out{{}}, fmt.Errorf({stepf}, _rErr)",
            "\t}",
            "\t_bodyReader = strings.NewReader(_raw)",
            '\tif _, ok := _headers["Content-Type"]; !ok {',
            '\t\t_headers["Content-Type"] = "text/plain"',
            "\t}",
        ]
    return lines, uses_bytes, uses_strings


def render_rest_step_go(
    step: StepIR, contracts: dict[str, ContractIR], graph: FlowGraph
) -> str:
    """Render steps/NN_<name>.go for an impl.mode: rest step.

    GIVES present → json.Unmarshal the (optionally response_path-traversed) body
    into the typed <Cls>Out, then Validate(ctx). GIVES absent → side-effect:
    issue the request, check the error, discard the body, return <Cls>Out{}.

    Impl-level retry is driven by the step's RetryPolicyIR (impl.retry), distinct
    from the ON_FAIL chain. Body shapes: JsonBodyIR + RawBodyIR only.
    """
    assert isinstance(step.impl, RestImplIR)
    impl = step.impl
    cls = _to_class_name(step.name)
    pkg = _go_module_name(graph)
    has_contract_refs = _uses_contract_refs(step)
    qualifier = "contracts" if has_contract_refs else ""
    in_body, out_body = _step_in_out_struct(step, contracts, qualifier=qualifier)
    stepf = f'"{step.name}: %w"'

    takes_kv = ", ".join(
        f'"{f.name}": in.{_to_go_field_name(f.name)}' for f in step.takes
    )
    takes_line = f"\t_takes := map[string]any{{{takes_kv}}}"
    url_line = f"\t_url, _uErr := rest.Subst({impl.url!r}, _takes)"

    query_lines: list[str] = []
    if impl.query is not None:
        items = ", ".join(_go_json_scalar_kv(k, v) for k, v in impl.query)
        query_lines = [
            f"\t_query, _qErr := rest.RenderDict(map[string]any{{{items}}}, _takes)",
            "\tif _qErr != nil {",
            f"\t\treturn {cls}Out{{}}, fmt.Errorf({stepf}, _qErr)",
            "\t}",
        ]

    header_lines: list[str] = ["\t_headers := map[string]any{}"]
    if impl.headers is not None:
        items = ", ".join(_go_json_scalar_kv(k, v) for k, v in impl.headers)
        header_lines = [
            f"\t_headers, _hErr := rest.RenderDict(map[string]any{{{items}}}, _takes)",
            "\tif _hErr != nil {",
            f"\t\treturn {cls}Out{{}}, fmt.Errorf({stepf}, _hErr)",
            "\t}",
        ]

    body_lines, uses_bytes, uses_strings = _go_rest_body_lines(impl, cls, stepf)

    timeout = impl.timeout_seconds if impl.timeout_seconds is not None else 0
    retry = impl.retry

    send_block: list[str] = [
        "\t\t_req, _reqErr := http.NewRequestWithContext(ctx, method, _url, _bodyReader)",
        "\t\tif _reqErr != nil {",
        f"\t\t\treturn {cls}Out{{}}, fmt.Errorf({stepf}, _reqErr)",
        "\t\t}",
        "\t\tfor _k, _v := range _headers {",
        '\t\t\t_req.Header.Set(_k, fmt.Sprintf("%v", _v))',
        "\t\t}",
        "\t\tif _query != nil {",
        "\t\t\t_q := _req.URL.Query()",
        "\t\t\tfor _k, _v := range _query {",
        '\t\t\t\t_q.Set(_k, fmt.Sprintf("%v", _v))',
        "\t\t\t}",
        "\t\t\t_req.URL.RawQuery = _q.Encode()",
        "\t\t}",
    ]

    if retry is not None:
        on_lit = "[]string{" + ", ".join(f'"{o}"' for o in retry.on) + "}"
        do_request = [
            f"\t_attempts := {retry.attempts}",
            f"\t_retryOn := {on_lit}",
            f"\t_backoff := {retry.backoff!r}",
            f"\t_base := {retry.base}",
            f"\t_cap := {retry.cap}",
            "\tvar _resp *http.Response",
            "\tfor _i := 0; _i < _attempts; _i++ {",
            *send_block,
            "\t\t_r, _doErr := _client.Do(_req)",
            "\t\tif _doErr != nil {",
            "\t\t\tif rest.IsRetryableErr(_doErr, _retryOn) && _i+1 < _attempts {",
            "\t\t\t\ttime.Sleep(rest.ComputeDelay(_i+1, _base, _cap, _backoff))",
            "\t\t\t\tcontinue",
            "\t\t\t}",
            f"\t\t\treturn {cls}Out{{}}, fmt.Errorf({stepf}, _doErr)",
            "\t\t}",
            "\t\tif rest.IsRetryableStatus(_r.StatusCode, _retryOn) && _i+1 < _attempts {",
            '\t\t\t_ra, _ok := rest.ParseRetryAfter(_r.Header.Get("Retry-After"))',
            "\t\t\t_r.Body.Close()",
            "\t\t\tif _ok {",
            "\t\t\t\ttime.Sleep(_ra)",
            "\t\t\t} else {",
            "\t\t\t\ttime.Sleep(rest.ComputeDelay(_i+1, _base, _cap, _backoff))",
            "\t\t\t}",
            "\t\t\tcontinue",
            "\t\t}",
            "\t\t_resp = _r",
            "\t\tbreak",
            "\t}",
            "\tif _resp == nil {",
            f'\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: request exhausted retries")',
            "\t}",
        ]
    else:
        # No retry: a single send_block, de-indented by one tab (no for-loop nest).
        single = [ln[1:] if ln.startswith("\t\t") else ln for ln in send_block]
        do_request = [
            *single,
            "\t_resp, _doErr := _client.Do(_req)",
            "\tif _doErr != nil {",
            f"\t\treturn {cls}Out{{}}, fmt.Errorf({stepf}, _doErr)",
            "\t}",
        ]

    decode_lines: list[str] = [
        "\tdefer _resp.Body.Close()",
        "\t_respBytes, _readErr := io.ReadAll(_resp.Body)",
        "\tif _readErr != nil {",
        f"\t\treturn {cls}Out{{}}, fmt.Errorf({stepf}, _readErr)",
        "\t}",
        "\tif _resp.StatusCode >= 400 {",
        f'\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: http %d", _resp.StatusCode)',
        "\t}",
    ]
    if step.gives is not None:
        gives_field = _to_go_field_name(step.gives.name)
        decode_lines += [
            "\tvar _data any",
            "\tif err := json.Unmarshal(_respBytes, &_data); err != nil {",
            f"\t\treturn {cls}Out{{}}, fmt.Errorf({stepf}, err)",
            "\t}",
        ]
        if impl.response_path:
            decode_lines += _go_response_path_traversal(impl.response_path, cls, step.name)
        decode_lines += [
            "\t_nodeBytes, _ := json.Marshal(_data)",
            f"\tvar out {cls}Out",
            f"\tif err := json.Unmarshal(_nodeBytes, &out.{gives_field}); err != nil {{",
            f"\t\treturn {cls}Out{{}}, fmt.Errorf({stepf}, err)",
            "\t}",
            "\tif validatable, ok := any(&out)"
            ".(interface{ Validate(context.Context) error }); ok {",
            "\t\tif err := validatable.Validate(ctx); err != nil {",
            f"\t\t\treturn {cls}Out{{}}, fmt.Errorf({stepf}, err)",
            "\t\t}",
            "\t}",
            "\treturn out, nil",
        ]
    else:
        decode_lines += [
            "\t_ = _respBytes",
            f"\treturn {cls}Out{{}}, nil",
        ]

    imports = ['\t"context"', '\t"encoding/json"', '\t"fmt"', '\t"io"', '\t"net/http"', '\t"time"']
    if uses_bytes:
        imports.insert(0, '\t"bytes"')
    if uses_strings:
        imports.append('\t"strings"')
    imports = sorted(set(imports))
    imports += ["", f'\t"{pkg}/clio_runtime/rest"']
    if has_contract_refs:
        imports.append(f'\t"{pkg}/contracts"')

    lines: list[str] = [
        "package steps",
        "",
        "// Auto-generated by CLIO. Do not edit by hand.",
        "",
        "import (",
        "\n".join(imports),
        ")",
        "",
        f"type {cls}In struct {{",
    ]
    if in_body:
        lines.append(in_body)
    lines.append("}")
    lines.append("")
    lines.append(f"type {cls}Out struct {{")
    if out_body:
        lines.append(out_body)
    lines.append("}")
    lines.append("")
    lines.append(f"// {cls} implements the '{step.name}' REST step.")
    lines.append(f"func {cls}(ctx context.Context, in {cls}In) ({cls}Out, error) {{")
    lines.append(takes_line)
    lines.append(url_line)
    lines.append("\tif _uErr != nil {")
    lines.append(f"\t\treturn {cls}Out{{}}, fmt.Errorf({stepf}, _uErr)")
    lines.append("\t}")
    if query_lines:
        lines.extend(query_lines)
    else:
        lines.append("\tvar _query map[string]any")
    lines.extend(header_lines)
    lines.extend(body_lines)
    lines.append(f'\tmethod := "{impl.method}"')
    lines.append(f"\t_client := &http.Client{{Timeout: {timeout} * time.Second}}")
    lines.extend(do_request)
    lines.extend(decode_lines)
    lines.append("}")
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**
```bash
uv run pytest tests/test_emitters/test_go.py::test_render_rest_step_go_gives_typed -q
```
Expected: `1 passed`.

- [ ] **Step 5: Commit**
```bash
git add clio/emitters/_go_step_renderers.py tests/test_emitters/test_go.py
git commit -m "feat(go): render_rest_step_go for GIVES-typed REST (unmarshal + response_path + impl retry)"
```

---

### Task 4: `render_rest_step_go` — no-GIVES side-effect REST step

A REST step with no GIVES is a pure side-effect (per the spec's untyped-impl decision): Go issues
the request, checks the error, discards the body, returns `<Cls>Out{}`. Locks the renderer's
GIVES-absent branch and the bool-literal JSON body.

**Files:**
- Modify: `clio/emitters/_go_step_renderers.py` (only if Step 2 fails — Task 3's branch should already cover it)
- Test: `tests/test_emitters/test_go.py` (append at end of file)

- [ ] **Step 1: Write the failing test**
```python
def test_render_rest_step_go_no_gives_side_effect():
    from clio.emitters._go_step_renderers import render_rest_step_go
    from clio.parser.parser import parse
    from clio.ir.builder import build_graph

    src = (
        "STEP notify\n"
        "  TAKES: msg: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:    rest\n"
        "    method:  POST\n"
        '    url:     "https://hooks.example.com/notify"\n'
        '    body:    {text: "${msg}", urgent: true}\n'
        "FLOW pipeline\n"
        '  notify(msg="hi")\n'
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    graph = build_graph(parse(src))
    step = next(s for s in graph.steps if s.name == "notify")
    out = render_rest_step_go(step, {}, graph)

    # Side-effect skeleton: empty Out, returns NotifyOut{} with no field copy.
    assert "type NotifyOut struct {\n}" in out
    assert "func Notify(ctx context.Context, in NotifyIn) (NotifyOut, error) {" in out
    assert "return NotifyOut{}, nil" in out

    # JSON body still built + subst; bool literal renders bare.
    assert 'rest.RenderDict(map[string]any{"text": "${msg}", "urgent": true}, _takes)' in out
    assert "bytes.NewReader(" in out
    assert '"application/json"' in out

    # No GIVES → no field unmarshal, no traversal, no Validate.
    assert "&out." not in out
    assert "_data = _m[" not in out
    assert "interface{ Validate(context.Context) error }" not in out

    # No contracts import (no contract refs); no retry block (no impl.retry).
    assert "/contracts" not in out
    assert "for _i := 0; _i <" not in out
```

- [ ] **Step 2: Run test to verify it fails**
```bash
uv run pytest tests/test_emitters/test_go.py::test_render_rest_step_go_no_gives_side_effect -q
```
Expected: if Task 3's GIVES-absent branch is correct, this passes immediately (a pure
regression-lock — note that in the commit body). If an assertion fails (e.g. empty-`Out`
formatting), iterate on `render_rest_step_go`.

- [ ] **Step 3: Write minimal implementation**
If Step 2 already passes, no code change (proceed to Step 4). Otherwise adjust the GIVES-absent
branch / `_go_json_scalar_kv` bool ordering in `clio/emitters/_go_step_renderers.py` until the
assertions hold (empty `Out` body → `type NotifyOut struct {\n}`; `return NotifyOut{}, nil`; bool
renders as `true`).

- [ ] **Step 4: Run test to verify it passes**
```bash
uv run pytest tests/test_emitters/test_go.py::test_render_rest_step_go_no_gives_side_effect -q
```
Expected: `1 passed`.

- [ ] **Step 5: Commit**
```bash
git add clio/emitters/_go_step_renderers.py tests/test_emitters/test_go.py
git commit -m "test(go): lock no-GIVES side-effect REST step (empty Out, no decode)"
```

---

### Task 5: Wire `go.py` — emit REST runtimes + dispatch RestImplIR in the stub loop

`go.py` must (a) emit `clio_runtime/rest/rest.go` + `clio_runtime/substitute/substitute.go` when any
`RestImplIR` step is present, and (b) dispatch `RestImplIR` exact steps to `render_rest_step_go` in
the stub loop. The `E_GO_007` refusal is NOT lifted here (Phase 6) — tests monkeypatch
`validate_graph_for_go` to a no-op.

**Files:**
- Modify: `clio/emitters/_go_helpers.py` (add `_flow_uses_rest` after `_flow_uses_cache`, ~line 92)
- Modify: `clio/emitters/go.py` (imports lines 21-35; emit-runtimes block after the cache block at line 69; dispatch branch at lines 92-95)
- Test: `tests/test_emitters/test_go.py` (append at end of file)

- [ ] **Step 1: Write the failing test**
```python
def test_go_emits_rest_runtimes_and_dispatches_rest_step(tmp_path, monkeypatch):
    from clio.emitters import go as _go
    from clio.emitters._go_helpers import _flow_uses_rest
    from clio.emitters.go import GoEmitter
    from clio.parser.parser import parse
    from clio.ir.builder import build_graph

    src = (
        "STEP geocode\n"
        "  TAKES: address: str\n"
        "  GIVES: lat: float\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:           rest\n"
        "    method:         GET\n"
        '    url:            "https://maps.example.com/geocode"\n'
        '    query:          {address: "${address}"}\n'
        '    response_path:  "results[0].lat"\n'
        "FLOW pipeline\n"
        '  geocode(address="x")\n'
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    graph = build_graph(parse(src))
    assert _flow_uses_rest(graph) is True

    monkeypatch.setattr(_go, "validate_graph_for_go", lambda g: None)
    out = tmp_path / "out"
    GoEmitter().emit(graph, out)

    rest_go = out / "clio_runtime" / "rest" / "rest.go"
    subst_go = out / "clio_runtime" / "substitute" / "substitute.go"
    assert rest_go.exists()
    assert subst_go.exists()
    assert "pipeline/clio_runtime/substitute" in rest_go.read_text()

    step_file = out / "steps" / "01_geocode.go"
    assert step_file.exists()
    text = step_file.read_text()
    assert 'method := "GET"' in text
    assert "rest.Subst(" in text
    assert 'panic("fill me in' not in text


def test_go_omits_rest_runtimes_when_no_rest_step(tmp_path):
    from clio.emitters._go_helpers import _flow_uses_rest
    from clio.emitters.go import GoEmitter
    from clio.parser.parser import parse
    from clio.ir.builder import build_graph

    src = (
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        '  noop(x="hi")\n'
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    graph = build_graph(parse(src))
    assert _flow_uses_rest(graph) is False
    out = tmp_path / "out"
    GoEmitter().emit(graph, out)
    assert not (out / "clio_runtime" / "rest").exists()
    assert not (out / "clio_runtime" / "substitute").exists()
```

- [ ] **Step 2: Run test to verify it fails**
```bash
uv run pytest tests/test_emitters/test_go.py::test_go_emits_rest_runtimes_and_dispatches_rest_step tests/test_emitters/test_go.py::test_go_omits_rest_runtimes_when_no_rest_step -q
```
Expected: `ImportError: cannot import name '_flow_uses_rest'` (collection error / failed).

- [ ] **Step 3: Write minimal implementation**
Add `_flow_uses_rest` to `clio/emitters/_go_helpers.py` (after `_flow_uses_cache`, before `render_cmd_main_go`):
```python
def _flow_uses_rest(graph: FlowGraph) -> bool:
    """True if any step in the source is an impl.mode: rest step.

    Gates emission of clio_runtime/rest + clio_runtime/substitute. Like
    _flow_uses_cache, graph.steps over-collects; harmless (the extra runtime
    is only ever emitted, never wrong-imported)."""
    return any(
        isinstance(s, StepIR) and isinstance(s.impl, RestImplIR) for s in graph.steps
    )
```
(`RestImplIR` and `StepIR` are already imported in `_go_helpers.py`.)

In `clio/emitters/go.py`, extend the imports (lines 21-35):
```python
from clio.emitters._go_helpers import (
    _flow_uses_cache,
    _flow_uses_rest,
    _go_module_name,
    render_cmd_main_go,
    render_contracts_go,
    render_go_mod,
    validate_graph_for_go,
)
from clio.emitters._go_runtime_templates import (
    render_clio_runtime_cache,
    render_clio_runtime_rest,
    render_clio_runtime_substitute,
    render_clio_runtime_validate,
)
from clio.emitters._go_step_renderers import (
    render_exact_step_go,
    render_judgment_step_go,
    render_rest_step_go,
)
from clio.emitters.base import BaseEmitter
from clio.ir.graph import CallIR, FlowGraph, RestImplIR, StepIR
```
After the cache block (`go.py` line 69), add:
```python
        if _flow_uses_rest(graph):
            runtime_rest_dir = output_dir / "clio_runtime" / "rest"
            runtime_rest_dir.mkdir(parents=True, exist_ok=True)
            (runtime_rest_dir / "rest.go").write_text(render_clio_runtime_rest(pkg))
            runtime_subst_dir = output_dir / "clio_runtime" / "substitute"
            runtime_subst_dir.mkdir(parents=True, exist_ok=True)
            (runtime_subst_dir / "substitute.go").write_text(render_clio_runtime_substitute())
```
Replace the dispatch (lines 92-95):
```python
                if step.mode == "exact":
                    if isinstance(step.impl, RestImplIR):
                        src = render_rest_step_go(step, contracts_by_name, graph)
                    else:
                        src = render_exact_step_go(step, contracts_by_name, graph)
                else:
                    src = render_judgment_step_go(step, graph)
```

- [ ] **Step 4: Run test to verify it passes**
```bash
uv run pytest tests/test_emitters/test_go.py::test_go_emits_rest_runtimes_and_dispatches_rest_step tests/test_emitters/test_go.py::test_go_omits_rest_runtimes_when_no_rest_step -q
```
Expected: `2 passed`.

- [ ] **Step 5: Commit**
```bash
git add clio/emitters/_go_helpers.py clio/emitters/go.py tests/test_emitters/test_go.py
git commit -m "feat(go): wire go.py to emit REST runtimes + dispatch RestImplIR steps"
```

---

### Task 6: Real `go build` of an emitted GIVES-typed + no-GIVES REST module (intent gate)

Per Rule 9 / the spec's testing section, a grep-only test cannot fail when the emitted Go regresses
(unused import, type-assertion mismatch, missing runtime). This task assembles a complete REST module
via the actual renderers and runs `go build ./...`. `skipif`-gated on `shutil.which("go")` exactly
like the existing harness, so it runs in CI (Go on PATH) and skips otherwise.

**Files:**
- Test: `tests/test_emitters/test_go_compile.py` (append at end; reuses `_go_build` + the module `pytestmark` skip)

- [ ] **Step 1: Write the failing test**
```python
def test_go_build_passes_on_rest_flow(tmp_path: Path, monkeypatch) -> None:
    """Emit a REST flow (GIVES-typed geocode + no-GIVES notify) to Go and
    `go build`. Catches the type-assertion / import / runtime-template class of
    bugs a string-grep can never see."""
    from clio.emitters import go as _go
    from clio.emitters.go import GoEmitter
    from clio.parser.parser import parse
    from clio.ir.builder import build_graph

    src_text = (
        "CONTRACT geo_point\n"
        "  SHAPE: {lat: float, lng: float}\n"
        "STEP geocode\n"
        "  TAKES: address: str\n"
        "  GIVES: location: geo_point\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:           rest\n"
        "    method:         GET\n"
        '    url:            "https://maps.example.com/geocode"\n'
        '    query:          {address: "${address}", key: "env:MAPS_KEY"}\n'
        '    headers:        {Accept: "application/json"}\n'
        '    response_path:  "results[0].geometry.location"\n'
        "    timeout:        30s\n"
        '    retry:          {attempts: 3, on: ["5xx", "429", "timeout"]}\n'
        "STEP notify\n"
        "  TAKES: msg: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:    rest\n"
        "    method:  POST\n"
        '    url:     "https://hooks.example.com/notify"\n'
        '    body:    {text: "${msg}", urgent: true}\n'
        "FLOW pipeline\n"
        '  geocode(address="123 Main St")\n'
        '  -> notify(msg="done")\n'
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    src = tmp_path / "src.clio"
    src.write_text(src_text)
    graph = build_graph(parse(src.read_text()))

    # Phase 6 owns lifting E_GO_007; bypass the refusal so the emitted module —
    # the thing under test — is what `go build` checks.
    monkeypatch.setattr(_go, "validate_graph_for_go", lambda g: None)
    out = tmp_path / "out"
    GoEmitter().emit(graph, out)

    tidy_env = {
        "GOFLAGS": "-mod=mod",
        "HOME": str(out / ".gohome"),
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin:/bin"),
    }
    subprocess.run(
        ["go", "mod", "tidy"], cwd=out, check=True, capture_output=True, env=tidy_env
    )
    result = _go_build(out)
    assert result.returncode == 0, (
        f"go build failed:\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert (out / "clio_runtime" / "rest" / "rest.go").exists()
    assert (out / "clio_runtime" / "substitute" / "substitute.go").exists()
```

- [ ] **Step 2: Run test to verify it fails**
```bash
uv run pytest tests/test_emitters/test_go_compile.py::test_go_build_passes_on_rest_flow -q
```
Expected (Go on PATH): `go build failed` on any emitted-code compile error. Expected (Go NOT on PATH,
e.g. this sandbox): `1 skipped` via the module `pytestmark` — the task is NOT considered done until it
is confirmed green in a Go-enabled environment / CI (note the skip explicitly in the commit body).

- [ ] **Step 3: Write minimal implementation**
Fix whatever `go build` reports, in the renderers/templates from Tasks 1-3. Likely fixes:
- `fmt imported and not used` in `clio_runtime/rest/rest.go` → drop the `var _ = fmt.Sprintf` guard
  and the `"fmt"` import if `fmt.Errorf` is genuinely unused there, OR keep both if used. (Decide by
  reading the build error.)
- unused `"bytes"`/`"strings"` in a step → Task 3 already makes these conditional on body shape via
  `uses_bytes`/`uses_strings`; if the build still flags one, tighten that gating.
- `go.mod` missing a require → `go mod tidy` (run in the test) resolves stdlib-only deps; the REST
  runtimes are stdlib + the in-module substitute import, so no new external require is needed.
Iterate Step 2 ↔ Step 3 until `go build` returns 0.

- [ ] **Step 4: Run test to verify it passes**
```bash
uv run pytest tests/test_emitters/test_go_compile.py::test_go_build_passes_on_rest_flow -q
```
Expected (Go on PATH): `1 passed`. (Sandbox without Go: `1 skipped` — must be confirmed green in CI.)

- [ ] **Step 5: Commit**
```bash
git add tests/test_emitters/test_go_compile.py clio/emitters/_go_step_renderers.py clio/emitters/_go_runtime_templates.py
git commit -m "test(go): go build of emitted REST module (GIVES-typed + side-effect)"
```

---

### Task 7: Phase-1 verify gate — ruff + mypy + full suite

Final Phase-1 gate (per MEMORY: ruff and mypy gate CI ahead of pytest; green local pytest alone is
not sufficient). No new test code; runs the quality gates and fixes any lint/type fallout from the
new signatures (`render_clio_runtime_rest(pkg)`, `_flow_uses_rest`, `render_rest_step_go`).

**Files:**
- Modify (only if a gate flags something): `clio/emitters/_go_runtime_templates.py`,
  `clio/emitters/_go_step_renderers.py`, `clio/emitters/_go_helpers.py`, `clio/emitters/go.py`

- [ ] **Step 1: Write the failing test**
No new test. The "failing" condition is a non-clean ruff/mypy run.

- [ ] **Step 2: Run test to verify it fails**
```bash
uv run ruff check . && uv run mypy clio/
```
Expected: either clean, or diagnostics on the new code (e.g. an unused import, a missing return
annotation, or `dict`/`object` value-type tightness on `_go_json_scalar_kv`'s `value: object`).

- [ ] **Step 3: Write minimal implementation**
`uv run ruff check . --fix`, then hand-fix any remaining mypy error in the four Phase-1 files (the
`value: object` param in `_go_json_scalar_kv` is fine since it only branches via `isinstance`; ensure
`render_clio_runtime_rest(pkg: str) -> str` is annotated).

- [ ] **Step 4: Run test to verify it passes**
```bash
uv run ruff check . && uv run mypy clio/ && uv run pytest tests/ -q
```
Expected: ruff clean, mypy `Success`, full suite green (all Phase-1 tests included).

- [ ] **Step 5: Commit**
```bash
git add clio/emitters/_go_runtime_templates.py clio/emitters/_go_step_renderers.py clio/emitters/_go_helpers.py clio/emitters/go.py
git commit -m "chore(go): satisfy ruff + mypy for REST phase"
```
(If the gates were already clean, skip the commit and note it in the phase summary.)

---

## Phase 2 — shell (E_GO_008)

This phase makes a `MODE: exact` + `impl.mode: shell` step emit a runnable Go step body
(`os/exec` + the shared `clio_runtime/substitute` helper from Phase 1) instead of being
refused with `E_GO_008`. Parity reference is `clio/emitters/_python_helpers.py:emit_shell_step`
(lines 638-707). It covers four shapes: `parse: none` (single `str` GIVES = stdout),
`parse: json` (`json.Unmarshal` stdout into the typed `Out`), `${var}` per-token substitution,
`timeout` via `context.WithTimeout`, and a no-GIVES side-effect step.

**Phase-1 dependency (referenced, NOT created here):** `render_clio_runtime_substitute()`
in `clio/emitters/_go_runtime_templates.py` writes `clio_runtime/substitute/substitute.go`
(package `substitute`, export `func Apply(token string, takes map[string]any) (string, error)`).
This phase calls that accessor and imports `<pkg>/clio_runtime/substitute` in the emitted step.

**Shared-contract names used here:** `render_shell_step_go(step, contracts, graph) -> str`
(new, in `_go_step_renderers.py`); reuses `_step_in_out_struct`; calls `_to_class_name`,
`_to_go_field_name`, `_type_to_go`, `_uses_contract_refs`, `_go_module_name` (all pre-existing).

---

### Task 1: `render_shell_step_go` — parse:json body (the typed unmarshal path)

**Files:**
- Modify: `clio/emitters/_go_step_renderers.py` (add import of `ShellImplIR`; add new function `render_shell_step_go` after `render_judgment_step_go`, i.e. after line 431)
- Test: `tests/test_emitters/test_go.py` (append new test)

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_emitters/test_go.py (after the existing shell/impl section,
# e.g. after test_for_each_parallel_emits_errgroup or near the golden block — anywhere
# at module level is fine; the renderer is exercised directly so no fixture file is needed).

from clio.emitters._go_step_renderers import render_shell_step_go  # noqa: E402
from clio.ir.builder import build_graph  # noqa: E402
from clio.parser.parser import parse as _parse_clio  # noqa: E402


def _shell_step_and_graph(source: str):
    """Parse a one-step .clio source, build its graph, return (step, contracts, graph)."""
    graph = build_graph(_parse_clio(source))
    step = next(s for s in graph.steps if s.name == "load_corpus")
    contracts = {c.name: c for c in graph.contracts}
    return step, contracts, graph


def test_render_shell_step_go_parse_json_unmarshals_stdout() -> None:
    src = (
        "STEP load_corpus\n"
        "  TAKES: file: str\n"
        "  GIVES: corpus: List<str>\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:  shell\n"
        "    cmd:   \"cat ${file}\"\n"
        "    parse: json\n"
        "FLOW shell_pipe\n"
        "  load_corpus(file=\"data.json\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    step, contracts, graph = _shell_step_and_graph(src)
    body = render_shell_step_go(step, contracts, graph)
    # package + skeleton reused from _step_in_out_struct
    assert "package steps" in body
    assert "func LoadCorpus(ctx context.Context, in LoadCorpusIn) (LoadCorpusOut, error) {" in body
    assert "type LoadCorpusIn struct {" in body
    assert 'File string `json:"file"`' in body
    assert "type LoadCorpusOut struct {" in body
    assert 'Corpus []string `json:"corpus"`' in body
    # os/exec invocation, argv built from the shlex-split template
    assert '"os/exec"' in body
    assert 'argv := []string{"cat", "${file}"}' in body
    assert "exec.CommandContext(" in body
    # per-token ${var} substitution via the Phase-1 substitute helper
    assert '"' + _go_pkg(graph) + '/clio_runtime/substitute"' in body
    assert "substitute.Apply(argv[i], takes)" in body
    # parse: json -> Unmarshal stdout into the typed Out
    assert '"encoding/json"' in body
    assert "json.Unmarshal(stdout, &out)" in body
    assert "return out, nil" in body


def _go_pkg(graph):
    from clio.emitters._go_helpers import _go_module_name
    return _go_module_name(graph)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_render_shell_step_go_parse_json_unmarshals_stdout -x -q
```
Expected failure: `ImportError: cannot import name 'render_shell_step_go' from 'clio.emitters._go_step_renderers'` (the function does not exist yet).

- [ ] **Step 3: Write minimal implementation**

In `clio/emitters/_go_step_renderers.py`, extend the IR import on line 22 to add `ShellImplIR`:

```python
from clio.ir.graph import CacheConfigIR, ContractIR, FlowGraph, ShellImplIR, StepIR
```

Then append this function after `render_judgment_step_go` (after line 431):

```python
# ---------------------------------------------------------------------------
# Shell step renderer (os/exec + clio_runtime/substitute)


def render_shell_step_go(
    step: StepIR, contracts: dict[str, ContractIR], graph: FlowGraph
) -> str:
    """Render steps/NN_<name>.go for a shell-impl exact step.

    Go parity with clio/emitters/_python_helpers.py:emit_shell_step. Builds an
    argv slice from the shlex-split ShellImplIR.argv template, substitutes each
    `${var}` token from the step's TAKES via the shared clio_runtime/substitute
    helper (Apply), runs exec.CommandContext with a timeout when configured,
    and shapes the result by `parse`:
      - parse: none -> the single str GIVES field is set to stdout verbatim.
      - parse: json -> json.Unmarshal(stdout) into the typed Out struct.
    No GIVES -> pure side-effect; returns an empty Out{} after a successful run.
    Validate() runs via interface assertion when GIVES is present (same pattern
    as the judgment renderer).
    """
    impl = step.impl
    assert isinstance(impl, ShellImplIR)  # dispatch guarantees this

    cls = _to_class_name(step.name)
    pkg = _go_module_name(graph)
    has_contract_refs = _uses_contract_refs(step)
    qualifier = "contracts" if has_contract_refs else ""
    in_body, out_body = _step_in_out_struct(step, contracts, qualifier=qualifier)

    parse_json = impl.parse == "json"

    # Imports — context + the substitute helper are always needed; os/exec always;
    # time only when a timeout is configured; encoding/json only for parse:json;
    # fmt always (error wrapping); contracts only when the Out struct uses a ref.
    imports: list[str] = [
        '\t"context"',
        '\t"fmt"',
        '\t"os/exec"',
    ]
    if impl.timeout_seconds is not None:
        imports.append('\t"time"')
    if parse_json:
        imports.append('\t"encoding/json"')
    imports += [
        "",
        f'\t"{pkg}/clio_runtime/substitute"',
    ]
    if has_contract_refs:
        imports.append(f'\t"{pkg}/contracts"')

    argv_literal = "[]string{" + ", ".join(_go_string_literal(t) for t in impl.argv) + "}"

    lines: list[str] = [
        "package steps",
        "",
        "// Auto-generated by CLIO. Do not edit by hand.",
        "",
        "import (",
        "\n".join(imports),
        ")",
        "",
        f"type {cls}In struct {{",
    ]
    if in_body:
        lines.append(in_body)
    lines.append("}")
    lines.append("")
    lines.append(f"type {cls}Out struct {{")
    if out_body:
        lines.append(out_body)
    lines.append("}")
    lines.append("")
    lines.append(f"// {cls} implements the '{step.name}' shell step (os/exec).")
    lines.append(f"func {cls}(ctx context.Context, in {cls}In) ({cls}Out, error) {{")

    # Build the TAKES map the substitute helper resolves ${var} against.
    lines.append("\ttakes := map[string]any{")
    for f in step.takes:
        lines.append(f'\t\t"{f.name}": in.{_to_go_field_name(f.name)},')
    lines.append("\t}")

    # argv template + per-token substitution.
    lines.append(f"\targv := {argv_literal}")
    lines.append("\tfor i := range argv {")
    lines.append("\t\tv, err := substitute.Apply(argv[i], takes)")
    lines.append("\t\tif err != nil {")
    lines.append(f'\t\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: substitute: %w", err)')
    lines.append("\t\t}")
    lines.append("\t\targv[i] = v")
    lines.append("\t}")

    # Command (+ optional timeout context).
    if impl.timeout_seconds is not None:
        lines.append(
            f"\tcmdCtx, cancel := context.WithTimeout(ctx, "
            f"{impl.timeout_seconds}*time.Second)"
        )
        lines.append("\tdefer cancel()")
        cmd_ctx = "cmdCtx"
    else:
        cmd_ctx = "ctx"
    lines.append(f"\tcmd := exec.CommandContext({cmd_ctx}, argv[0], argv[1:]...)")
    lines.append("\tstdout, err := cmd.Output()")
    lines.append("\tif err != nil {")
    lines.append(f'\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: exec: %w", err)')
    lines.append("\t}")

    lines.append(f"\tvar out {cls}Out")
    if step.gives is None:
        # Side-effect step: discard stdout, return empty Out.
        lines.append("\t_ = stdout")
    elif parse_json:
        lines.append("\tif err := json.Unmarshal(stdout, &out); err != nil {")
        lines.append(f'\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: unmarshal: %w", err)')
        lines.append("\t}")
    else:
        # parse: none -> single str field = stdout verbatim.
        gives_field = _to_go_field_name(step.gives.name)
        lines.append(f"\tout.{gives_field} = string(stdout)")

    if step.gives is not None:
        lines.append(
            "\tif validatable, ok := any(&out)"
            ".(interface{ Validate(context.Context) error }); ok {"
        )
        lines.append("\t\tif err := validatable.Validate(ctx); err != nil {")
        lines.append(f'\t\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: validate: %w", err)')
        lines.append("\t\t}")
        lines.append("\t}")

    lines.append("\treturn out, nil")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _go_string_literal(s: str) -> str:
    """Render a Go double-quoted string literal for an argv token.

    Tokens come from a shlex-split shell template and may contain `${var}`
    placeholders and arbitrary shell text. Go's strconv.Quote rules match
    JSON string escaping for the characters CLIO can produce, so reuse
    json.dumps (already imported at module top via stdlib json)."""
    import json as _json

    return _json.dumps(s)
```

Note: add `import json as _json` inline in the helper (kept local — the module does not
currently import `json` at top level, and the rest of this file builds strings without it).
If a top-level `import json` is preferred for style, that is the assembler's call; the inline
form keeps the diff surgical.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_render_shell_step_go_parse_json_unmarshals_stdout -x -q
```
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_go_step_renderers.py tests/test_emitters/test_go.py
git commit -m "feat(go): render_shell_step_go — parse:json shell step body (os/exec + substitute)"
```

---

### Task 2: `render_shell_step_go` — parse:none (stdout into single str field)

**Files:**
- Modify: `clio/emitters/_go_step_renderers.py` (no change expected — the parse:none branch
  written in Task 1 should already satisfy this; this task LOCKS that behaviour with a test
  and fixes the renderer only if the test fails)
- Test: `tests/test_emitters/test_go.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_render_shell_step_go_parse_none_assigns_stdout_to_str_field() -> None:
    src = (
        "STEP load_corpus\n"
        "  TAKES: file: str\n"
        "  GIVES: contents: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode: shell\n"
        "    cmd:  \"cat ${file}\"\n"
        "FLOW shell_pipe\n"
        "  load_corpus(file=\"data.txt\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    step, contracts, graph = _shell_step_and_graph(src)
    body = render_shell_step_go(step, contracts, graph)
    # parse defaults to none -> no json import, no Unmarshal
    assert '"encoding/json"' not in body
    assert "json.Unmarshal" not in body
    # single str GIVES field = stdout verbatim
    assert "out.Contents = string(stdout)" in body
    assert 'Contents string `json:"contents"`' in body
    assert "return out, nil" in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_render_shell_step_go_parse_none_assigns_stdout_to_str_field -x -q
```
Expected outcome: if Task 1's branch is correct, this PASSES immediately (it locks behaviour
already written — a legitimate TDD "characterization" task that guards the parse-default path).
If it FAILS, the most likely cause is `impl.parse` not defaulting to `"none"`; re-read
`ShellImplIR` (`graph.py:52`, `parse: str = "none"`) and ensure the renderer compares
`impl.parse == "json"` (not truthiness).

- [ ] **Step 3: Write minimal implementation**

No change expected. If Step 2 failed, the only correct fix is in the `parse_json` derivation
or the `else` (parse:none) branch of `render_shell_step_go`:

```python
    parse_json = impl.parse == "json"
```
and the parse:none branch:
```python
        gives_field = _to_go_field_name(step.gives.name)
        lines.append(f"\tout.{gives_field} = string(stdout)")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_render_shell_step_go_parse_none_assigns_stdout_to_str_field -x -q
```
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_emitters/test_go.py clio/emitters/_go_step_renderers.py
git commit -m "test(go): lock parse:none shell body (stdout -> single str GIVES field)"
```

---

### Task 3: `${var}` substitution + timeout context emitted per-token

**Files:**
- Modify: `clio/emitters/_go_step_renderers.py` (only if a test below fails)
- Test: `tests/test_emitters/test_go.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_render_shell_step_go_substitutes_each_token_and_honours_timeout() -> None:
    src = (
        "STEP load_corpus\n"
        "  TAKES: file: str\n"
        "  TAKES: pattern: str\n"
        "  GIVES: matches: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode: shell\n"
        "    cmd:  \"grep ${pattern} ${file}\"\n"
        "    timeout_seconds: 5\n"
        "FLOW shell_pipe\n"
        "  load_corpus(file=\"a.txt\", pattern=\"x\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    step, contracts, graph = _shell_step_and_graph(src)
    body = render_shell_step_go(step, contracts, graph)
    # takes map carries BOTH TAKES so substitute.Apply can resolve either token
    assert '"file": in.File,' in body
    assert '"pattern": in.Pattern,' in body
    # argv template preserves both ${var} tokens (shlex-split: grep / ${pattern} / ${file})
    assert 'argv := []string{"grep", "${pattern}", "${file}"}' in body
    # one substitution loop over every token (not per-take, unlike the python target)
    assert "for i := range argv {" in body
    assert "substitute.Apply(argv[i], takes)" in body
    # timeout context
    assert '"time"' in body
    assert "context.WithTimeout(ctx, 5*time.Second)" in body
    assert "defer cancel()" in body
    assert "exec.CommandContext(cmdCtx, argv[0], argv[1:]...)" in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_render_shell_step_go_substitutes_each_token_and_honours_timeout -x -q
```
Expected outcome: PASSES if Task 1's renderer is complete (timeout + multi-take map + loop are
all in Task 1's body). This task is a characterization guard for the substitution/timeout
contract. If it FAILS, re-read the timeout branch and the `takes := map[string]any{...}`
construction in `render_shell_step_go` and correct the mismatched assertion.

- [ ] **Step 3: Write minimal implementation**

No change expected. If Step 2 failed on the timeout assertion, ensure the timeout branch reads:

```python
    if impl.timeout_seconds is not None:
        lines.append(
            f"\tcmdCtx, cancel := context.WithTimeout(ctx, "
            f"{impl.timeout_seconds}*time.Second)"
        )
        lines.append("\tdefer cancel()")
        cmd_ctx = "cmdCtx"
    else:
        cmd_ctx = "ctx"
    lines.append(f"\tcmd := exec.CommandContext({cmd_ctx}, argv[0], argv[1:]...)")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_render_shell_step_go_substitutes_each_token_and_honours_timeout -x -q
```
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_emitters/test_go.py clio/emitters/_go_step_renderers.py
git commit -m "test(go): lock per-token \${var} substitution + WithTimeout in shell body"
```

---

### Task 4: no-GIVES side-effect shell step (discards stdout, no Validate)

**Files:**
- Modify: `clio/emitters/_go_step_renderers.py` (only if a test below fails)
- Test: `tests/test_emitters/test_go.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_render_shell_step_go_no_gives_is_side_effect() -> None:
    src = (
        "STEP notify\n"
        "  TAKES: msg: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode: shell\n"
        "    cmd:  \"logger ${msg}\"\n"
        "FLOW shell_pipe\n"
        "  notify(msg=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    graph = build_graph(_parse_clio(src))
    step = next(s for s in graph.steps if s.name == "notify")
    contracts = {c.name: c for c in graph.contracts}
    body = render_shell_step_go(step, contracts, graph)
    # empty Out struct (no GIVES)
    assert "type NotifyOut struct {\n}" in body
    # stdout discarded, no Validate, no Unmarshal, no json import
    assert "_ = stdout" in body
    assert "Validate(ctx)" not in body
    assert "json.Unmarshal" not in body
    assert "return out, nil" in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_render_shell_step_go_no_gives_is_side_effect -x -q
```
Expected outcome: PASSES if Task 1's `if step.gives is None:` branch (`_ = stdout`, and the
Validate block guarded by `if step.gives is not None`) is correct. Characterization guard.
If it FAILS — e.g. the empty-struct assertion `type NotifyOut struct {\n}` does not match —
re-read how the Out struct is emitted when `out_body == ""` (Task 1 appends `"}"` directly
after the `type ...Out struct {` line, producing exactly `struct {\n}`).

- [ ] **Step 3: Write minimal implementation**

No change expected. If Step 2 failed on the Validate guard, ensure the Validate block is
nested under `if step.gives is not None:` and the side-effect branch is:

```python
    if step.gives is None:
        lines.append("\t_ = stdout")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_render_shell_step_go_no_gives_is_side_effect -x -q
```
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_emitters/test_go.py clio/emitters/_go_step_renderers.py
git commit -m "test(go): lock no-GIVES shell step as pure side-effect (discard stdout)"
```

---

### Task 5: lift the E_GO_008 refusal + dispatch shell steps in go.py + emit substitute runtime

This is the wiring task: without it, the emitter raises `E_GO_008` before `render_shell_step_go`
is ever reached, and even with the refusal lifted, go.py's exact-step dispatch (lines 92-95)
would route the shell step to `render_exact_step_go` (panic stub) and never emit the substitute
runtime package. End-to-end emission becomes possible only after all three edits.

**Files:**
- Modify: `clio/emitters/_go_helpers.py` (remove the `ShellImplIR` refusal at lines 334-335;
  refresh the stale `_GO_E_008_MSG` constant at lines 258-261 — the message is reused only by
  REST/sql/mcp in their own phases, but the v0.20.0 string is stale; this phase narrows only
  the shell line)
- Modify: `clio/emitters/go.py` (import `render_shell_step_go` and `ShellImplIR`; branch the
  exact-step dispatch at lines 92-95; emit the substitute runtime package when any shell step
  is present)
- Test: `tests/test_emitters/test_go.py` (replace the `test_E_GO_008_impl_mode_shell` negative
  test — lines 842-858 — with a positive emission test)

- [ ] **Step 1: Write the failing test**

First, DELETE the obsolete negative test `test_E_GO_008_impl_mode_shell` (lines 842-858) — it
asserts the refusal we are removing. Then append the positive emission test:

```python
def test_shell_step_emits_go_file_instead_of_E_GO_008(tmp_path: Path) -> None:
    """impl.mode: shell is now compiled (not refused). The step file calls
    os/exec + the substitute runtime, and the substitute runtime package is
    written under clio_runtime/substitute/."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP load_corpus\n"
        "  TAKES: file: str\n"
        "  GIVES: corpus: List<str>\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:  shell\n"
        "    cmd:   \"cat ${file}\"\n"
        "    parse: json\n"
        "FLOW shell_pipe\n"
        "  load_corpus(file=\"data.json\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)  # must NOT raise E_GO_008
    step_file = out / "steps" / "01_load_corpus.go"
    assert step_file.exists(), "shell step must get its own steps/NN_<name>.go file"
    body = step_file.read_text()
    assert "exec.CommandContext(" in body
    assert "substitute.Apply(argv[i], takes)" in body
    assert 'panic("fill me in' not in body  # NOT the exact-step stub
    # substitute runtime package emitted
    sub = out / "clio_runtime" / "substitute" / "substitute.go"
    assert sub.exists(), "shell step must trigger clio_runtime/substitute emission"
    assert "package substitute" in sub.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_shell_step_emits_go_file_instead_of_E_GO_008 -x -q
```
Expected failure: `ValueError: E_GO_008: ...` raised by `validate_graph_for_go` (the refusal
at `_go_helpers.py:334-335` still fires).

- [ ] **Step 3: Write minimal implementation**

(a) In `clio/emitters/_go_helpers.py`, remove the shell refusal at lines 334-335:

```python
        # impl.mode checks
        if isinstance(step.impl, RestImplIR):
            raise ValueError(_GO_E_007_MSG)
        if isinstance(step.impl, SqlImplIR):
            raise ValueError(_GO_E_009_MSG)
        if isinstance(step.impl, McpToolImplIR):
            raise ValueError(_GO_E_010_MSG)
```
(the `if isinstance(step.impl, ShellImplIR): raise ValueError(_GO_E_008_MSG)` pair is deleted).

Refresh the stale `_GO_E_008_MSG` constant (lines 258-261). It is no longer raised for the
supported shape; keep it defined (re-narrowed for clarity, future-proof) without the stale
version string:

```python
_GO_E_008_MSG = (
    "E_GO_008: target: go supports impl.mode: shell via os/exec since v0.23. "
    "This message is retained for back-reference; the construct is no longer refused."
)
```

(b) In `clio/emitters/go.py`, extend the imports. Change line 33:

```python
from clio.emitters._go_step_renderers import (
    render_exact_step_go,
    render_judgment_step_go,
    render_shell_step_go,
)
```
add `render_clio_runtime_substitute` to the `_go_runtime_templates` import (lines 29-32):

```python
from clio.emitters._go_runtime_templates import (
    render_clio_runtime_cache,
    render_clio_runtime_substitute,
    render_clio_runtime_validate,
)
```
and add `ShellImplIR` to the graph import (line 35):

```python
from clio.ir.graph import CallIR, FlowGraph, ShellImplIR, StepIR
```

Branch the dispatch at lines 92-95 so shell steps route to the shell renderer:

```python
                if step.mode == "exact" and isinstance(step.impl, ShellImplIR):
                    src = render_shell_step_go(step, contracts_by_name, graph)
                elif step.mode == "exact":
                    src = render_exact_step_go(step, contracts_by_name, graph)
                else:
                    src = render_judgment_step_go(step, graph)
                (steps_dir / filename).write_text(src)
```

Emit the substitute runtime package when any shell step is present. Add, right after the cache
block (after line 69) — a local predicate keeps the diff surgical and mirrors `_flow_uses_cache`:

```python
        if any(
            isinstance(s, StepIR) and isinstance(s.impl, ShellImplIR)
            for s in graph.steps
        ):
            runtime_sub_dir = output_dir / "clio_runtime" / "substitute"
            runtime_sub_dir.mkdir(parents=True, exist_ok=True)
            (runtime_sub_dir / "substitute.go").write_text(render_clio_runtime_substitute())
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_shell_step_emits_go_file_instead_of_E_GO_008 -x -q
```
Expected: `1 passed`. Then run the full Go suite to confirm no golden/refusal regression:
```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py -q
```
Expected: all pass (the deleted `test_E_GO_008_impl_mode_shell` no longer collected).

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/go.py clio/emitters/_go_helpers.py tests/test_emitters/test_go.py
git commit -m "feat(go): compile impl.mode: shell — dispatch + lift E_GO_008 + emit substitute runtime"
```

---

### Task 6: REAL `go build` of an emitted shell module (intent test — catches type-assertion / import regressions a grep cannot)

Per Rule 9 and the spec's testing section, a grep test cannot fail when the emitted Go does not
actually compile (wrong import path, type mismatch on `cmd.Output()`, missing `substitute`
package, malformed argv literal). This task compiles a shell fixture end-to-end.

**Files:**
- Test: `tests/test_emitters/test_go_compile.py` (append a build test, reusing `_go_build`)
- Create: `tests/fixtures/go_shell.clio` (a shell fixture with both parse modes + a no-GIVES step)

> NOTE FOR ASSEMBLER: this build test depends on Phase 1's `render_clio_runtime_substitute()`
> being merged (it writes `clio_runtime/substitute/substitute.go`). If phases land out of order,
> gate ordering so Phase 1 precedes this task. The `_go_build` / `go mod tidy` harness is copied
> verbatim from `test_go_build_passes_on_minimal_contract_flow`.

- [ ] **Step 1: Write the failing test**

Create `tests/fixtures/go_shell.clio`:

```
STEP read_json
  TAKES: file: str
  GIVES: rows: List<str>
  MODE:  exact
  impl:
    mode:  shell
    cmd:   "cat ${file}"
    parse: json

STEP read_text
  TAKES: file: str
  GIVES: contents: str
  MODE:  exact
  impl:
    mode: shell
    cmd:  "cat ${file}"

STEP notify
  TAKES: contents: str
  MODE:  exact
  impl:
    mode: shell
    cmd:  "logger ${contents}"
    timeout_seconds: 3

FLOW shell_pipe
  read_json(file="data.json")
  -> read_text(file="data.txt")
  -> notify(contents=contents)

RESOURCES
  target: go
  models: [haiku]
```

Append to `tests/test_emitters/test_go_compile.py`:

```python
def test_go_build_passes_on_shell_flow(tmp_path: Path) -> None:
    """A flow with parse:json, parse:none, and a no-GIVES shell step must
    emit Go that actually compiles (catches import-path / type-assertion bugs
    a string-grep cannot)."""
    fixtures = Path(__file__).parent.parent / "fixtures"
    from tests.test_emitters.test_go import _compile as _go_compile

    out = tmp_path / "out"
    _go_compile(fixtures / "go_shell.clio", out)

    tidy_env = {
        "GOFLAGS": "-mod=mod",
        "HOME": str(out / ".gohome"),
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin:/bin"),
    }
    subprocess.run(
        ["go", "mod", "tidy"], cwd=out, check=True, capture_output=True, env=tidy_env
    )
    result = _go_build(out)
    assert result.returncode == 0, (
        f"go build failed:\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go_compile.py::test_go_build_passes_on_shell_flow -x -q
```
Expected outcome: if the `go` toolchain is on PATH, the test runs and (before this phase's
work is fully merged with Phase 1) FAILS — either at `_go_compile` (Task 5 not yet applied →
`E_GO_008`) or at `go build` (missing `clio_runtime/substitute` package → `package
.../clio_runtime/substitute is not in std`). If `go` is NOT on PATH, the module-level
`pytestmark` skips it; record the skip and run the build on a host with Go before merge
(do not claim the gate passed on a skip — Rule 12).

- [ ] **Step 3: Write minimal implementation**

No emitter change if Tasks 1-5 + Phase 1 are correct. If `go build` reports a concrete failure,
fix the named cause and re-run — common cases:
- `undefined: substitute.Apply` → Phase 1 export name drift; confirm `func Apply(token string, takes map[string]any) (string, error)` in `clio_runtime/substitute/substitute.go`.
- `cannot use stdout (variable of type []byte) as ...` → `cmd.Output()` returns `[]byte`; the parse:none branch must wrap with `string(stdout)` and parse:json must pass `stdout` (a `[]byte`) directly to `json.Unmarshal`.
- import-path mismatch → the emitted import must be `"<pkg>/clio_runtime/substitute"` where `<pkg> == _go_module_name(graph)`.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go_compile.py::test_go_build_passes_on_shell_flow -x -q
```
Expected (Go on PATH): `1 passed`. Then the full gate:
```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run ruff check . --fix && uv run mypy clio && uv run pytest tests/test_emitters/test_go.py tests/test_emitters/test_go_compile.py -q
```
Expected: ruff clean, mypy clean, all tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/go_shell.clio tests/test_emitters/test_go_compile.py
git commit -m "test(go): go build of shell flow (parse:json + parse:none + no-GIVES side-effect)"
```

---

## Phase 3 — Pre-existing fixes (standalone commits)

Two pre-existing bugs the sub-flow feature exposes. Each is its own focused commit with its own regression test. **These ship BEFORE the sub-flow feature** so reviewers can see them in isolation, but they also fix latent bugs that bite today (a step nested in an ENTRY-flow `FOR EACH` / `IF` / `MATCH` / `WHILE` body already gets no `steps/NN_*.go` file). Neither task introduces `FlowCallIR` handling — that is Phase 5. This phase only walks nested control-flow bodies and rescues within every `graph.flows` entry to collect `CallIR`-backed `StepIR` producers.

**Scope note on `FlowCallIR`:** At this point in the build, `validate_graph_for_go` still refuses multi-flow graphs (the `len(graph.flows) > 1` refusal is removed in Phase 6), so in practice `graph.flows` holds exactly one flow during Phase 3. The recursive collector is nonetheless written to walk *all* `graph.flows` and to *skip* `FlowCallIR` nodes (they reference a flow, not a step) so it is correct once Phase 6 lifts the refusal. The two regression tests in this phase use only single-flow graphs with nested bodies, which is the latent bug that bites today.

---

### Task 1: Recursive step collector — `_collect_reachable_steps(graph)` walks all flows + nested bodies + rescues

**Files:**
- Modify: `clio/emitters/go.py` (replace the top-level-only stub loop at lines 71–96; adjust imports at lines 35)
- Test: `tests/test_emitters/test_go.py` (append new test functions near the existing `test_each_step_emits_its_own_go_file` at line 380)

- [ ] **Step 1: Write the failing test**

  Append these two tests to `tests/test_emitters/test_go.py`. The first asserts the new helper directly (unit-level, on the IR); the second is the end-to-end regression: a step nested in an ENTRY-flow `FOR EACH` body must now get its own `steps/NN_*.go` file (today it gets none).

  ```python
  # ---------------------------------------------------------------------------
  # Phase 3 Task 1 — recursive step collector (pre-existing bug fix)
  # Regression: a step reachable ONLY through a nested control-flow body
  # (FOR EACH / IF / MATCH / WHILE) or a RESCUE handler previously got no
  # steps/NN_*.go file, because the stub loop walked only top-level CallIR
  # in graph.flow.chain. The recursive collector walks every flow's chain,
  # nested bodies, and rescues; dedups by name; numbers by first-seen order.


  def test_collect_reachable_steps_walks_nested_for_each_body() -> None:
      """A step that appears ONLY inside a FOR EACH body is reachable and must
      be collected — the stub loop previously skipped it (it walked only
      top-level CallIR), so its steps/NN_*.go file went missing and the
      emitted module would not compile if that step had a contract Out."""
      from clio.cli import _build_graph_from_source
      from clio.emitters.go import _collect_reachable_steps

      src = (
          "STEP load\n"
          "  TAKES: file: str\n"
          "  GIVES: items: List<str>\n"
          "  MODE:  exact\n"
          "  LANG:  go\n"
          "STEP process\n"
          "  TAKES: item: str\n"
          "  GIVES: result: str\n"
          "  MODE:  exact\n"
          "  LANG:  go\n"
          "FLOW pipeline\n"
          '  load(file="in.csv")\n'
          "    -> FOR EACH item IN items:\n"
          "         process(item=item)\n"
          "RESOURCES\n"
          "  target: go\n"
          "  models: [haiku]\n"
      )
      graph = _build_graph_from_source(src, "<test>")
      collected = _collect_reachable_steps(graph)
      names = [s.name for s in collected]
      # first-seen order: load (top-level), then process (inside FOR EACH body)
      assert names == ["load", "process"], names


  def test_collect_reachable_steps_dedups_by_name() -> None:
      """A step called from two reachable sites is collected exactly once,
      at its first-seen position (stable NN_ numbering)."""
      from clio.cli import _build_graph_from_source
      from clio.emitters.go import _collect_reachable_steps

      src = (
          "STEP detect\n"
          "  TAKES: x: str\n"
          "  GIVES: flag: str\n"
          "  MODE:  exact\n"
          "  LANG:  go\n"
          "STEP audit\n"
          "  TAKES: x: str\n"
          "  GIVES: note: str\n"
          "  MODE:  exact\n"
          "  LANG:  go\n"
          "FLOW pipeline\n"
          '  detect(x="hi")\n'
          '    -> IF detect.flag == "yes":\n'
          '         audit(x="a")\n'
          "       ELSE:\n"
          '         audit(x="b")\n'
          "RESOURCES\n"
          "  target: go\n"
          "  models: [haiku]\n"
      )
      graph = _build_graph_from_source(src, "<test>")
      collected = _collect_reachable_steps(graph)
      names = [s.name for s in collected]
      assert names == ["detect", "audit"], names  # audit appears once, not twice


  def test_nested_for_each_body_step_emits_its_own_go_file(tmp_path: Path) -> None:
      """End-to-end regression: a step reachable only through an ENTRY-flow
      FOR EACH body now gets a steps/NN_*.go file. Before the fix the stub
      loop produced only 01_load.go and the module would not build."""
      src = tmp_path / "src.clio"
      src.write_text(
          "STEP load\n"
          "  TAKES: file: str\n"
          "  GIVES: items: List<str>\n"
          "  MODE:  exact\n"
          "  LANG:  go\n"
          "STEP process\n"
          "  TAKES: item: str\n"
          "  GIVES: result: str\n"
          "  MODE:  exact\n"
          "  LANG:  go\n"
          "FLOW pipeline\n"
          '  load(file="in.csv")\n'
          "    -> FOR EACH item IN items:\n"
          "         process(item=item)\n"
          "RESOURCES\n"
          "  target: go\n"
          "  models: [haiku]\n"
      )
      out = tmp_path / "out"
      _compile(src, out)
      files = sorted(f.name for f in (out / "steps").iterdir())
      assert files == ["01_load.go", "02_process.go"], files
      body = (out / "steps" / "02_process.go").read_text()
      assert "func Process(ctx context.Context, in ProcessIn) (ProcessOut, error)" in body
  ```

  Before writing the test, confirm the source-to-graph helper name the suite uses. If `clio.cli._build_graph_from_source` does not exist with that exact signature, replace the two unit tests' graph construction with the in-process compile already used elsewhere in this file and read the IR back via `_cmd_compile`'s machinery. Run this one-liner to confirm the helper exists and its signature:

  ```bash
  cd /Users/jean-paulgavini/Documents/Dev/clio && uv run python -c "import inspect, clio.cli as c; print([n for n in dir(c) if 'graph' in n.lower() or 'build' in n.lower()]); import clio.cli; print(inspect.signature(clio.cli._build_graph_from_source)) if hasattr(clio.cli,'_build_graph_from_source') else print('NO _build_graph_from_source')"
  ```

  If `_build_graph_from_source` is absent, use whatever the suite already imports to turn source into a `FlowGraph` (grep the test dir: `grep -rn "FlowGraph\|build_graph\|_build_graph\|parse_program\|build(" tests/test_emitters/test_go.py tests/test_ir.py | head`) and substitute that call, keeping the assertions identical.

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py -k "collect_reachable_steps or nested_for_each_body_step" -v
  ```

  Expected failure: `test_collect_reachable_steps_walks_nested_for_each_body` and `test_collect_reachable_steps_dedups_by_name` fail with `ImportError: cannot import name '_collect_reachable_steps' from 'clio.emitters.go'` (the helper does not exist yet). `test_nested_for_each_body_step_emits_its_own_go_file` fails with `AssertionError: ['01_load.go']` — the `process` step file is missing because the current loop walks only top-level `CallIR`.

- [ ] **Step 3: Write minimal implementation**

  In `clio/emitters/go.py`, first widen the IR imports at line 35. Replace:

  ```python
  from clio.ir.graph import CallIR, FlowGraph, StepIR
  ```

  with:

  ```python
  from clio.ir.graph import (
      CallIR,
      FlowGraph,
      ForEachIR,
      IfBlockIR,
      MatchBlockIR,
      RescueBlockIR,
      StepIR,
      WhileBlockIR,
  )
  ```

  Then add the recursive collector as a module-level function directly above the `GoEmitter` class (insert after the imports block, before `class GoEmitter`):

  ```python
  def _collect_reachable_steps(graph: FlowGraph) -> list[StepIR]:
      """Return every StepIR reachable from any flow in the graph.

      Walks each flow's chain plus all nested control-flow bodies
      (IF / MATCH / WHILE / FOR EACH) and RESCUE handlers, resolving each
      CallIR.step_name against the graph's steps. Dedups by step name,
      preserving stable first-seen order so steps/NN_<name>.go numbering is
      deterministic. FlowCallIR nodes are skipped — they reference a flow,
      not a step (sub-flow step bodies are reached via their own flow's
      chain, which this walk covers because it iterates graph.flows).

      Replaces the prior top-level-only loop (go.py:74-96) that walked only
      graph.flow.chain and only top-level CallIR, so steps nested in a
      control-flow body or a rescue — or in a sub-flow — got no stub file.
      """
      steps_by_name = {s.name: s for s in graph.steps if isinstance(s, StepIR)}
      seen: set[str] = set()
      ordered: list[StepIR] = []

      def visit_chain(items: tuple) -> None:  # type: ignore[type-arg]
          for it in items:
              if isinstance(it, CallIR):
                  if it.step_name in seen:
                      continue
                  step = steps_by_name.get(it.step_name)
                  if step is None:
                      continue
                  seen.add(it.step_name)
                  ordered.append(step)
              elif isinstance(it, IfBlockIR):
                  visit_chain(it.then_body)
                  visit_chain(it.else_body)
              elif isinstance(it, MatchBlockIR):
                  for case in it.cases:
                      visit_chain(case.body)
              elif isinstance(it, WhileBlockIR):
                  visit_chain(it.body)
              elif isinstance(it, ForEachIR):
                  visit_chain(it.body)
              # FlowCallIR and any other node type: skip (no step to collect).

      for fl in graph.flows:
          visit_chain(fl.chain)
          for rescue in fl.rescues:
              visit_chain(rescue.body)

      return ordered
  ```

  Note: RESCUE bodies may contain `ResumeIR` nodes (per `RescueBlockIR` in graph.py:371); those are not `CallIR` and fall through the `if/elif` chain harmlessly.

  Now replace the stub loop in `emit` (lines 71–96, the block from the `# Emit step stubs ...` comment through the inner loop) with a call to the collector:

  ```python
          # Emit step stubs under steps/NN_<name>.go (exact and judgment).
          # _collect_reachable_steps walks every flow's chain, nested control-
          # flow bodies, and rescues, so a step reachable only through a
          # FOR EACH / IF / MATCH / WHILE body or a RESCUE handler (or a
          # sub-flow) still gets its file. Use the collector's first-seen
          # order so numbering is stable; skip control-flow-only steps that
          # aren't exact/judgment.
          contracts_by_name = {c.name: c for c in graph.contracts}
          steps_dir: Path | None = None
          step_idx = 0
          for step in _collect_reachable_steps(graph):
              if step.mode not in ("exact", "judgment"):
                  continue
              step_idx += 1
              if steps_dir is None:
                  steps_dir = output_dir / "steps"
                  steps_dir.mkdir(parents=True, exist_ok=True)
              filename = f"{step_idx:02d}_{step.name}.go"
              if step.mode == "exact":
                  src = render_exact_step_go(step, contracts_by_name, graph)
              else:
                  src = render_judgment_step_go(step, graph)
              (steps_dir / filename).write_text(src)
  ```

  The `if graph.flow is not None:` guard and the inner `steps_by_name`/`contracts_by_name` rebuild are removed: `_collect_reachable_steps` handles the empty-flows case (returns `[]`) and builds its own name lookup. `validate_graph_for_go` already guarantees `len(graph.flows) >= 1` before `emit` writes files, so no flow-presence guard is needed here.

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py -k "collect_reachable_steps or nested_for_each_body_step" -v
  ```

  Expected: all three new tests PASS. Then run the full Go emitter suite to confirm no regression in numbering/golden snapshots:

  ```bash
  cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py tests/test_emitters/test_go_compile.py -q
  ```

  Expected: all PASS (existing `test_each_step_emits_its_own_go_file` and the golden tests still green — the collector preserves first-seen order, which for top-level-only chains is identical to the old `enumerate`-of-top-level-CallIR order). If a golden snapshot now differs because a previously-missed nested step file appears, that golden was wrong; regenerate it with `python -m clio compile tests/fixtures/<name>.clio --target go --output tests/fixtures/expected_go/<name>` and note it in the commit body — but inspect the diff first to confirm it is ONLY new nested-step files (no spurious changes).

  Then run the gate checks:

  ```bash
  cd /Users/jean-paulgavini/Documents/Dev/clio && uv run ruff check clio/emitters/go.py tests/test_emitters/test_go.py --fix && uv run mypy clio/
  ```

  Expected: ruff clean, mypy clean.

- [ ] **Step 5: Commit**

  ```bash
  cd /Users/jean-paulgavini/Documents/Dev/clio && git add clio/emitters/go.py tests/test_emitters/test_go.py && git commit -m "fix(go): collect steps from all flows + nested bodies + rescues

Replace the top-level-only stub loop (go.py:74-96), which walked only
graph.flow.chain and only top-level CallIR, with _collect_reachable_steps:
a recursive walk over every flow's chain, nested IF/MATCH/WHILE/FOR EACH
bodies, and RESCUE handlers, deduped by name with stable first-seen NN_
numbering. Pre-existing latent bug: a step reachable only through an
entry-flow control-flow body previously got no steps/NN_*.go file. Also
unblocks sub-flow step emission (their bodies are reached via graph.flows).

Regression test asserts a step nested in an entry-flow FOR EACH body now
emits its own steps/02_*.go file."
  ```

  If a golden snapshot was legitimately regenerated in Step 4, add those paths to the same `git add` and mention them in the commit body.

---

### Task 2: `_flow_uses_parallel` scans every flow's chain (not just `graph.flow.chain`)

**Files:**
- Modify: `clio/emitters/_go_helpers.py` (lines 70–74, the `_flow_uses_parallel` function)
- Test: `tests/test_emitters/test_go.py` (append near the existing parallel tests around line 631)

- [ ] **Step 1: Write the failing test**

  A `FOR EACH PARALLEL` block reachable only through a non-entry flow's chain currently goes undetected, so `golang.org/x/sync` (errgroup) is omitted from `go.mod` and the emitted module fails to build. Because the multi-flow refusal is still active in Phase 3, the regression must be driven at the helper level on a hand-built two-flow graph rather than via end-to-end compile (which would be refused by `validate_graph_for_go` until Phase 6). The test builds a minimal `FlowGraph` whose **second** flow (not `graph.flow`) contains the parallel block and asserts `_flow_uses_parallel` returns `True`.

  Append to `tests/test_emitters/test_go.py`:

  ```python
  # ---------------------------------------------------------------------------
  # Phase 3 Task 2 — _flow_uses_parallel scans every flow's chain
  # (pre-existing bug fix). Before: scanned only graph.flow.chain, so a
  # FOR EACH PARALLEL inside a non-entry flow was missed -> errgroup dep
  # omitted from go.mod -> module fails to build once sub-flows ship.


  def test_flow_uses_parallel_detects_parallel_in_non_entry_flow() -> None:
      """A FOR EACH PARALLEL block in a flow OTHER than graph.flow must still
      be detected, otherwise the errgroup dependency is dropped from go.mod.
      Driven on a hand-built graph because multi-flow compile is still refused
      in this phase (the refusal is lifted in Phase 6)."""
      from clio.emitters._go_helpers import _flow_uses_parallel
      from clio.ir.graph import CallIR, FieldIR, FlowGraph, FlowIR, ForEachIR, StepIR

      entry = FlowIR(
          name="main",
          chain=(CallIR(step_name="seed", kwargs=(), line=1),),
          rescues=(),
          line=1,
          takes=(FieldIR(name="x", type=_str_type()),),
          gives=(FieldIR(name="items", type=_str_type()),),
      )
      sub = FlowIR(
          name="worker",
          chain=(
              ForEachIR(
                  loop_var="item",
                  collection="items",
                  body=(CallIR(step_name="proc", kwargs=(), line=3),),
                  line=2,
                  parallel=True,
                  collector="results",
              ),
          ),
          rescues=(),
          line=2,
          takes=(FieldIR(name="items", type=_str_type()),),
          gives=(FieldIR(name="results", type=_str_type()),),
      )
      seed = StepIR(
          name="seed", mode="exact",
          takes=(FieldIR(name="x", type=_str_type()),),
          gives=FieldIR(name="items", type=_str_type()),
          cache=None, on_fail=None, lang="go", impl=None, invoke=None, line=1,
      )
      proc = StepIR(
          name="proc", mode="exact",
          takes=(FieldIR(name="item", type=_str_type()),),
          gives=FieldIR(name="r", type=_str_type()),
          cache=None, on_fail=None, lang="go", impl=None, invoke=None, line=3,
      )
      graph = FlowGraph(
          steps=(seed, proc),
          flow=entry,            # entry flow has NO parallel block
          flows=(entry, sub),    # the parallel block lives in `sub`
      )
      assert _flow_uses_parallel(graph) is True


  def test_flow_uses_parallel_false_when_no_flow_has_parallel() -> None:
      """Sanity: with no parallel block in any flow, returns False (errgroup
      stays out of go.mod)."""
      from clio.emitters._go_helpers import _flow_uses_parallel
      from clio.ir.graph import CallIR, FieldIR, FlowGraph, FlowIR, StepIR

      entry = FlowIR(
          name="main",
          chain=(CallIR(step_name="seed", kwargs=(), line=1),),
          rescues=(), line=1,
      )
      seed = StepIR(
          name="seed", mode="exact",
          takes=(FieldIR(name="x", type=_str_type()),),
          gives=FieldIR(name="y", type=_str_type()),
          cache=None, on_fail=None, lang="go", impl=None, invoke=None, line=1,
      )
      graph = FlowGraph(steps=(seed,), flow=entry, flows=(entry,))
      assert _flow_uses_parallel(graph) is False
  ```

  These tests need a `_str_type()` helper to build a `TypeExpr` for `str`. Before writing the tests, check whether the suite already has one and reuse it; only add the helper if absent. Run:

  ```bash
  cd /Users/jean-paulgavini/Documents/Dev/clio && grep -rn "_str_type\|TypeExpr(\|def _str\|ScalarType\|type=.*str" tests/test_emitters/test_go.py tests/test_ir.py | head
  ```

  If no reusable type-builder exists, add this helper near the top of `tests/test_emitters/test_go.py` (just after the `_compile` definition), using whatever `TypeExpr` constructor `clio/parser/ast_nodes.py` actually exposes — confirm the field names first:

  ```bash
  cd /Users/jean-paulgavini/Documents/Dev/clio && uv run python -c "import inspect; from clio.parser.ast_nodes import TypeExpr; print(inspect.signature(TypeExpr.__init__)); print([f for f in TypeExpr.__dataclass_fields__])"
  ```

  Then write `_str_type()` to construct a scalar `str` `TypeExpr` matching that signature, e.g. (adapt field names to the printed signature):

  ```python
  def _str_type() -> "TypeExpr":
      from clio.parser.ast_nodes import TypeExpr
      return TypeExpr(kind="scalar", name="str")  # adapt to actual TypeExpr fields
  ```

  Add the import guard `from clio.parser.ast_nodes import TypeExpr` at the top of the file if not already present, or keep it inside `_str_type` as shown.

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py -k "flow_uses_parallel" -v
  ```

  Expected failure: `test_flow_uses_parallel_detects_parallel_in_non_entry_flow` fails with `assert False is True` — the current implementation calls `_has_parallel(graph.flow.chain)` on the entry flow only, which has no parallel block, so it returns `False`. `test_flow_uses_parallel_false_when_no_flow_has_parallel` should already PASS (it documents the negative case).

- [ ] **Step 3: Write minimal implementation**

  In `clio/emitters/_go_helpers.py`, replace `_flow_uses_parallel` (lines 70–74):

  ```python
  def _flow_uses_parallel(graph: FlowGraph) -> bool:
      """True if the entry flow contains a FOR EACH PARALLEL block."""
      if graph.flow is None:
          return False
      return _has_parallel(graph.flow.chain)
  ```

  with:

  ```python
  def _flow_uses_parallel(graph: FlowGraph) -> bool:
      """True if ANY flow contains a FOR EACH PARALLEL block.

      Scans every flow's chain (not just graph.flow.chain), so a PARALLEL
      block reachable only through a sub-flow still pulls golang.org/x/sync
      (errgroup) into go.mod. (_flow_uses_judgment / _flow_uses_cache already
      scan all graph.steps, so they stay correct under FLOW composition.)"""
      return any(_has_parallel(fl.chain) for fl in graph.flows)
  ```

  Note: this drops the `graph.flow is None` short-circuit because `graph.flows` is `()` when no flow exists, so `any(...)` over an empty iterable correctly returns `False`. `_has_parallel` already recurses into nested IF/MATCH/WHILE/FOR EACH bodies (`_shared_utils.py:400-426`), so a parallel block nested inside another control-flow block in any flow is also caught.

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py -k "flow_uses_parallel" -v
  ```

  Expected: both new tests PASS. Then confirm the existing parallel emission + golden tests still pass (the entry-flow case is unchanged behaviour):

  ```bash
  cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py tests/test_emitters/test_go_compile.py -q
  ```

  Expected: all PASS — `test_for_each_parallel_emits_errgroup` and `test_golden_go_parallel` (whose entry flow has the parallel block) are unaffected because `graph.flows` still contains the entry flow.

  Gate checks:

  ```bash
  cd /Users/jean-paulgavini/Documents/Dev/clio && uv run ruff check clio/emitters/_go_helpers.py tests/test_emitters/test_go.py --fix && uv run mypy clio/
  ```

  Expected: ruff clean, mypy clean. (`any(... for fl in graph.flows)` returns a plain `bool`; mypy is satisfied.)

- [ ] **Step 5: Commit**

  ```bash
  cd /Users/jean-paulgavini/Documents/Dev/clio && git add clio/emitters/_go_helpers.py tests/test_emitters/test_go.py && git commit -m "fix(go): _flow_uses_parallel scans every flow, not just the entry

Pre-existing bug: _flow_uses_parallel checked only graph.flow.chain, so a
FOR EACH PARALLEL block reachable only through a non-entry flow was missed
and golang.org/x/sync (errgroup) was dropped from go.mod, breaking the
build once FLOW composition ships. Scan every flow's chain instead.

Regression test builds a two-flow graph whose parallel block lives in a
non-entry flow and asserts the helper returns True."
  ```

---

> **Phase 4 scope** — build the three compile-time metadata maps and the entry-flow TAKES seeding, and thread `take_types` through all five reader sites so `@take` references emit a direct value assertion instead of the untyped `state["x"]` fallback. This phase does **not** render `FlowCallIR` (the `NotImplementedError` arm at `_go_flow_renderer.py:448` stays) and does **not** touch the refusal codes in `_go_helpers.py` — `flow_composition.clio` (3 flows) is still refused at compile time during this phase. The end-to-end `go build` exerciser is a new **single-flow** fixture whose entry FLOW declares TAKES.
>
> **Pre-flight (run once before Task 1, do not commit):** confirm the baseline is green so later "existing tests still pass" claims are meaningful.
> ```bash
> cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py tests/test_emitters/test_go_compile.py -q
> ```
> Expected: all pass (the `test_go_compile.py` cases skip if `go` is not on PATH).

---

### Task 1: `_build_state_field_to_step(flow, steps_by_name)` — per-flow producer map walking chain + rescues + nested bodies

**Files:**
- Test: `tests/test_emitters/test_go.py` (append a new section at end of file)
- Modify: `clio/emitters/_go_flow_renderer.py` (add new helper after the imports block, before `_go_kwarg_value` at line 30)

- [ ] **Step 1: Write the failing test**

```python
# ---------------------------------------------------------------------------
# Phase 4 (v0.23) — per-flow typed-state maps + entry-flow TAKES seeding


def test_build_state_field_to_step_walks_chain_rescues_and_nested_bodies() -> None:
    """_build_state_field_to_step maps each GIVES field name -> producing StepIR
    for ONE flow, descending into IF / FOR EACH bodies AND rescue handlers.

    Intent: a per-flow map (not a global one) is what lets flow A's `result`
    and flow B's `result` resolve against different producers.  If the walker
    skipped nested bodies or rescues, a downstream `@field` read inside (or
    after) those constructs would fall to the untyped fallback and the emitted
    Go would not type-check.  This test pins the SET of producers the walker
    must discover, not just one happy-path step.
    """
    from clio.emitters._go_flow_renderer import _build_state_field_to_step
    from clio.ir.builder import build_ir
    from clio.ir.graph import StepIR
    from clio.parser.parser import parse

    src = (
        "STEP load\n"
        "  TAKES: file: str\n"
        "  GIVES: rows: str\n"
        "  MODE:  exact\n"
        "STEP guard\n"
        "  TAKES: rows: str\n"
        "  GIVES: ok: bool\n"
        "  MODE:  exact\n"
        "STEP fallback_load\n"
        "  TAKES: file: str\n"
        "  GIVES: rows2: str\n"
        "  MODE:  exact\n"
        "STEP inner\n"
        "  TAKES: rows: str\n"
        "  GIVES: tagged: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  load(file=\"a.csv\")\n"
        "    -> guard(rows=rows)\n"
        "  IF guard.ok == true:\n"
        "    inner(rows=rows)\n"
        "  RESCUE load:\n"
        "    fallback_load(file=\"a.csv\")\n"
        "    RESUME(fallback_load.rows2)\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    graph = build_ir(parse(src))
    steps_by_name = {s.name: s for s in graph.steps if isinstance(s, StepIR)}
    m = _build_state_field_to_step(graph.flow, steps_by_name)

    # Top-level chain producers.
    assert m["rows"].name == "load"
    assert m["ok"].name == "guard"
    # Nested IF-body producer must be discovered.
    assert m["tagged"].name == "inner"
    # Rescue-handler producer must be discovered.
    assert m["rows2"].name == "fallback_load"
    # Map is collision-free per flow: exactly one StepIR per field name.
    assert set(m) == {"rows", "ok", "tagged", "rows2"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_build_state_field_to_step_walks_chain_rescues_and_nested_bodies -q
```
Expected failure: `ImportError: cannot import name '_build_state_field_to_step' from 'clio.emitters._go_flow_renderer'`.

- [ ] **Step 3: Write minimal implementation**

Add this helper to `clio/emitters/_go_flow_renderer.py` immediately after the import block (before `def _go_kwarg_value` at line 30):

```python
def _build_state_field_to_step(
    flow: FlowIR,
    steps_by_name: dict[str, StepIR],
) -> dict[str, StepIR]:
    """Map each state-dict key (a step's GIVES field name) to the StepIR that
    produced it, for ONE flow only.

    Walks the flow's reachable producers: chain + rescue-handler bodies +
    nested IF / MATCH / WHILE / FOR EACH bodies.  Replaces the single global
    build that assumed one flow per graph — per-flow maps keep two flows that
    each GIVE `result` from colliding (each resolves against its own map in
    its own rendered function).

    Inside one flow the IR already forbids two producers of one field, so each
    per-flow map is collision-free (last-writer-wins on the rare overwrite,
    matching builder.py's `available[...]` semantics).
    """
    result: dict[str, StepIR] = {}

    def walk(items: tuple) -> None:  # type: ignore[type-arg]
        for it in items:
            if isinstance(it, CallIR):
                step = steps_by_name.get(it.step_name)
                if step is not None and step.gives is not None:
                    result[step.gives.name] = step
            elif isinstance(it, IfBlockIR):
                walk(it.then_body)
                walk(it.else_body)
            elif isinstance(it, MatchBlockIR):
                for case in it.cases:
                    walk(case.body)
            elif isinstance(it, WhileBlockIR):
                walk(it.body)
            elif isinstance(it, ForEachIR):
                walk(it.body)
            elif isinstance(it, RescueBlockIR):
                walk(it.body)
            # FlowCallIR / ResumeIR contribute no StepIR producer here:
            # FlowCallIR boundary extension is Phase 5; RESUME writes a field
            # already produced by its fallback step (captured via that CallIR).

    walk(flow.chain)
    for rb in flow.rescues:
        walk(rb.body)
    return result
```

Add `FlowIR` to the existing `from clio.ir.graph import (...)` block in this file (it currently imports `CallIR, ContractIR, FlowGraph, ForEachIR, IfBlockIR, MatchBlockIR, RescueBlockIR, ResumeIR, StepIR, WhileBlockIR`):

```python
from clio.ir.graph import (
    CallIR,
    ContractIR,
    FlowGraph,
    FlowIR,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    RescueBlockIR,
    ResumeIR,
    StepIR,
    WhileBlockIR,
)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_build_state_field_to_step_walks_chain_rescues_and_nested_bodies -q
```
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && git add clio/emitters/_go_flow_renderer.py tests/test_emitters/test_go.py && git commit -m "feat(go): per-flow _build_state_field_to_step walking chain+rescues+nested bodies"
```

---

### Task 2: `_build_take_field_to_gotype(flow, contracts)` — TAKE field → Go type-string map

**Files:**
- Test: `tests/test_emitters/test_go.py` (append after Task 1's test)
- Modify: `clio/emitters/_go_flow_renderer.py` (add helper after `_build_state_field_to_step`)

- [ ] **Step 1: Write the failing test**

```python
def test_build_take_field_to_gotype_maps_scalar_and_contract_takes() -> None:
    """_build_take_field_to_gotype returns {take_name: go_type_string} for a
    flow's TAKES, using the same _type_to_go(qualifier="contracts") rendering
    the rest of the emitter uses.

    Intent: a TAKE is produced by no step, so it is absent from
    state_field_to_step.  Reading `@take` must assert to a concrete Go type,
    NOT the untyped `state["x"]` fallback (which fails to compile against a
    typed steps.<Cls>In field).  This map is the single source of those types,
    so the scalar form must be `string` and the contract form must be the
    `contracts.`-qualified struct name.
    """
    from clio.emitters._go_flow_renderer import _build_take_field_to_gotype
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse

    src = (
        "CONTRACT customer\n"
        "  SHAPE: {client: str}\n"
        "STEP use\n"
        "  TAKES: url: str\n"
        "  GIVES: out: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  TAKES: url: str, who: customer\n"
        "  GIVES: out: str\n"
        "  use(url=url)\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    graph = build_ir(parse(src))
    contracts = {c.name: c for c in graph.contracts}
    m = _build_take_field_to_gotype(graph.flow, contracts)

    assert m == {"url": "string", "who": "contracts.Customer"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_build_take_field_to_gotype_maps_scalar_and_contract_takes -q
```
Expected failure: `ImportError: cannot import name '_build_take_field_to_gotype' from 'clio.emitters._go_flow_renderer'`.

- [ ] **Step 3: Write minimal implementation**

Add `_type_to_go` to the existing `from clio.emitters._shared_utils import (...)` block in `_go_flow_renderer.py` (currently `_go_condition_expr, _to_class_name, _to_go_field_name`):

```python
from clio.emitters._shared_utils import (
    _go_condition_expr,
    _to_class_name,
    _to_go_field_name,
    _type_to_go,
)
```

Add the helper after `_build_state_field_to_step`:

```python
def _build_take_field_to_gotype(
    flow: FlowIR,
    contracts: dict[str, ContractIR],
) -> dict[str, str]:
    """Map each TAKE field name to its Go type string for ONE flow.

    A TAKE is produced by no step, so it never appears in
    state_field_to_step.  Readers consult this map to emit a DIRECT value
    assertion for `@<take>` — `state["url"].(string)` for a scalar,
    `state["x"].(contracts.<Cls>).<Field>` for a contract — instead of the
    untyped `state["x"]` fallback (which fails to compile against a typed
    steps.<Cls>In field).

    Uses the same `_type_to_go(qualifier="contracts")` rendering as the rest
    of the Go emitter so the asserted type matches the struct definitions in
    the contracts/ package.
    """
    return {
        f.name: _type_to_go(f.type, contracts, qualifier="contracts")
        for f in flow.takes
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_build_take_field_to_gotype_maps_scalar_and_contract_takes -q
```
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && git add clio/emitters/_go_flow_renderer.py tests/test_emitters/test_go.py && git commit -m "feat(go): _build_take_field_to_gotype maps flow TAKES to Go type strings"
```

---

### Task 3: `_go_kwarg_value` emits a direct value assertion for TAKE refs (scalar + contract)

**Files:**
- Test: `tests/test_emitters/test_go.py` (append after Task 2's test)
- Modify: `clio/emitters/_go_flow_renderer.py` (`_go_kwarg_value` lines 30-71; `_kwargs_to_step_input` lines 74-99)

- [ ] **Step 1: Write the failing test**

```python
def test_go_kwarg_value_emits_direct_assertion_for_take_refs() -> None:
    """When a `@ref` names a flow TAKE (in take_types, not a producer, not a
    loop var), _go_kwarg_value emits a direct value assertion instead of the
    untyped `state["x"]` fallback.

    Intent: the untyped fallback `state["url"]` is `any`; assigning it to a
    typed steps.<Cls>In field does not compile in Go.  This test fails the
    moment the take_types branch is dropped — a grep-only test could not,
    because the untyped string IS a valid Go expression (just ill-typed).
    Precedence is also pinned: scope_local wins over take_types (loop var),
    and a producer (state_field_to_step) wins over take_types.
    """
    from clio.emitters._go_flow_renderer import _go_kwarg_value

    # Scalar TAKE -> direct scalar assertion.
    out = _go_kwarg_value(
        "@url", {}, {}, scope_local=None, take_types={"url": "string"},
    )
    assert out == 'state["url"].(string)'

    # Contract TAKE -> the type-string is the whole assertion target; readers
    # that need a field append `.<Field>` themselves (kwarg passes the struct).
    out_c = _go_kwarg_value(
        "@who", {}, {}, scope_local=None, take_types={"who": "contracts.Customer"},
    )
    assert out_c == 'state["who"].(contracts.Customer)'

    # Precedence: a loop variable still wins over take_types.
    out_scope = _go_kwarg_value(
        "@url", {}, {}, scope_local={"url"}, take_types={"url": "string"},
    )
    assert out_scope == "url"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_go_kwarg_value_emits_direct_assertion_for_take_refs -q
```
Expected failure: `TypeError: _go_kwarg_value() got an unexpected keyword argument 'take_types'`.

- [ ] **Step 3: Write minimal implementation**

Replace the signature and the reference branch of `_go_kwarg_value`. Change lines 30-62. New signature and body head:

```python
def _go_kwarg_value(
    value: object,
    contracts: dict[str, ContractIR],
    state_field_to_step: dict[str, StepIR],
    scope_local: set[str] | None = None,
    take_types: dict[str, str] | None = None,
) -> str:
    """Render one CallIR kwarg value as a Go expression.

    Three cases, mirroring the python emitter's logic in python.py:
    - Reference ``@<field>`` — resolved in precedence order:
        1. loop variable (scope_local) -> bare identifier `<field>`
        2. step producer (state_field_to_step) ->
           `state["<field>"].(steps.<StepCls>Out).<GoField>`
        3. flow TAKE (take_types) -> direct value assertion
           `state["<field>"].(<go-type>)` (scalar or `contracts.<Cls>`)
        4. unknown -> untyped fallback `state["<field>"]`
      This is the same precedence used by `_go_condition_expr`, so the
      readers stay consistent with the writer.
    - Literal (str / int / float / bool) — rendered as a Go literal.
      Plain strings that do not start with ``@`` are string literals.
    """
    _scope = scope_local or set()
    _takes = take_types or {}
    if isinstance(value, str) and value.startswith("@"):
        ref = value[1:]  # the state-dict key (= the prior step's GIVES name)
        if ref in _scope:
            # Loop variable — use bare identifier (no state lookup needed).
            return ref
        step = state_field_to_step.get(ref)
        if step is not None:
            cls = _to_class_name(step.name)
            gf = _to_go_field_name(ref)
            return f'state["{ref}"].(steps.{cls}Out).{gf}'
        if ref in _takes:
            # Flow TAKE — produced by no step; seeded into state by Run /
            # run<Name>.  Assert directly to its declared Go type.
            return f'state["{ref}"].({_takes[ref]})'
        # Unknown ref — fall back to untyped any access (should not happen
        # after IR validation, but guards against future call-site bugs).
        return f'state["{ref}"]'
```

(Leave the literal-rendering tail of the function, lines 63-71, unchanged.)

Then thread `take_types` through `_kwargs_to_step_input` (lines 74-99). New signature + the one forwarding call:

```python
def _kwargs_to_step_input(
    call: CallIR,
    step: StepIR,
    contracts: dict[str, ContractIR],
    state_field_to_step: dict[str, StepIR],
    scope_local: set[str] | None = None,
    take_types: dict[str, str] | None = None,
) -> str:
    """Render the ``steps.<Step>In{...}`` initialisation from CallIR.kwargs.

    Each kwarg pair binds a TAKES field name to either a literal value or a
    reference into a prior step's typed output (``@<field>`` syntax) or a flow
    TAKE (``take_types``).

    `scope_local` and `take_types` are forwarded to `_go_kwarg_value` so that
    FOR EACH loop variables render as bare identifiers and flow TAKES render
    as direct value assertions.
    """
    cls = _to_class_name(step.name)
    parts: list[str] = []
    for name, value in call.kwargs:
        gf = _to_go_field_name(name)
        rendered = _go_kwarg_value(
            value, contracts, state_field_to_step, scope_local, take_types,
        )
        parts.append(f"{gf}: {rendered}")
    return f"steps.{cls}In{{ {', '.join(parts)} }}"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_go_kwarg_value_emits_direct_assertion_for_take_refs -q
```
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && git add clio/emitters/_go_flow_renderer.py tests/test_emitters/test_go.py && git commit -m "feat(go): _go_kwarg_value emits direct value assertion for flow TAKE refs"
```

---

### Task 4: `_go_condition_expr` resolves TAKE refs (the fifth reader, in `_shared_utils.py`)

**Files:**
- Test: `tests/test_emitters/test_go.py` (append after Task 3's test)
- Modify: `clio/emitters/_shared_utils.py` (`_go_condition_expr` lines 465-532)

- [ ] **Step 1: Write the failing test**

```python
def test_go_condition_expr_resolves_take_ref_as_contract_assertion() -> None:
    """An IF/WHILE condition whose state field is a flow TAKE (a contract)
    asserts to `state["x"].(contracts.<Cls>).<Field>`, not the `(any)`
    fallback.

    Intent: when a condition reads a contract-typed TAKE, the `(any)` fallback
    makes `.<Field>` an invalid field access on `any` — the Go does not
    compile.  This test pins the take_types branch in the leaf resolver; it
    fails if the threading regresses, which a grep test cannot catch.
    """
    from clio.emitters._shared_utils import _go_condition_expr
    from clio.ir.graph import ConditionIR

    cond = ConditionIR(
        step_name="who",            # state field name == the TAKE name
        field="level",
        op="==",
        literal_value="high",
        literal_kind="ident",
    )
    out = _go_condition_expr(
        cond,
        scope_local=set(),
        state_field_to_step={},     # no producer: it's a TAKE
        take_types={"who": "contracts.Customer"},
    )
    assert out == 'state["who"].(contracts.Customer).Level == "high"'
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_go_condition_expr_resolves_take_ref_as_contract_assertion -q
```
Expected failure: `TypeError: _go_condition_expr() got an unexpected keyword argument 'take_types'`.

- [ ] **Step 3: Write minimal implementation**

In `clio/emitters/_shared_utils.py`, change the `_go_condition_expr` signature (line 465) and the leaf type-assertion logic (lines 502-514). New signature:

```python
def _go_condition_expr(
    condition: ConditionIR | BoolOpIR,
    scope_local: set[str],
    state_field_to_step: dict[str, StepIR],
    take_types: dict[str, str] | None = None,
) -> str:
```

In the `BoolOpIR` recursion (lines 493-497), forward `take_types`:

```python
    if isinstance(condition, BoolOpIR):
        left = _go_condition_expr(
            condition.left, scope_local, state_field_to_step, take_types,
        )
        right = _go_condition_expr(
            condition.right, scope_local, state_field_to_step, take_types,
        )
        go_op = "&&" if condition.op == "and" else "||"
        return f"({left}) {go_op} ({right})"
```

In the leaf (replace lines 502-514) add the TAKE branch and emit the assertion directly (a TAKE is not in `scope_local` and not in `state_field_to_step`):

```python
    # Leaf: ConditionIR
    # condition.step_name is the state-dict key (GIVES field name of the
    # step that produced it), not the step's own name.
    _takes = take_types or {}
    state_field = condition.step_name
    step = state_field_to_step.get(state_field)
    if state_field in scope_local:
        # Loop variable — bare identifier; its element is a step Out struct.
        if step is not None:
            cls = _to_class_name(step.name)
            base = f"{state_field}.(steps.{cls}Out)"
        else:
            base = f"{state_field}.(any)"
    elif step is not None:
        cls = _to_class_name(step.name)
        base = f'state["{state_field}"].(steps.{cls}Out)'
    elif state_field in _takes:
        # Flow TAKE (contract) — direct value assertion to its Go type.
        base = f'state["{state_field}"].({_takes[state_field]})'
    else:
        # Fallback: unknown state field — use `any`.  Should not happen after
        # IR validation, but guards against future call-site bugs.
        base = f'state["{state_field}"].(any)'
    access = f"{base}.{_to_go_field_name(condition.field)}"
```

(Leave the RHS-literal rendering, lines 517-532, unchanged.)

- [ ] **Step 4: Run test to verify it passes, and the pre-existing condition tests still pass**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_go_condition_expr_resolves_take_ref_as_contract_assertion tests/test_emitters/test_go.py -k "if_else or match or while" -q
```
Expected: `test_go_condition_expr_resolves_take_ref_as_contract_assertion` passes; `test_if_else_emits_go_branches`, the MATCH and WHILE tests still pass (the refactor of the scope_local branch must keep `state["assessment"].(steps.DetectOut).Level` and `state["result"].(steps.PollOut).Done` byte-identical for non-TAKE fields).

- [ ] **Step 5: Commit**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && git add clio/emitters/_shared_utils.py tests/test_emitters/test_go.py && git commit -m "feat(go): _go_condition_expr resolves flow TAKE refs to typed assertions"
```

---

### Task 5: Thread `take_types` through `_render_chain_item` — CallIR, MATCH scrutinee, both FOR EACH collection resolvers, IF/WHILE conditions

**Files:**
- Test: `tests/test_emitters/test_go.py` (append after Task 4's test)
- Modify: `clio/emitters/_go_flow_renderer.py` (`_render_chain_item` signature line 112-123; every internal call site; the MATCH scrutinee ~287, seq FOR EACH ~352, parallel FOR EACH ~396; the IF cond ~248 and WHILE cond ~327; the two `_kwargs_to_step_input` calls)

- [ ] **Step 1: Write the failing test**

```python
def test_render_chain_item_threads_take_types_to_match_and_foreach() -> None:
    """take_types reaches all inline reader sites of _render_chain_item:
    the MATCH scrutinee, the sequential FOR EACH collection, and the parallel
    FOR EACH collection.  A TAKE used as a MATCH scrutinee (contract) or a
    FOR EACH collection (list TAKE) must assert to its Go type, not `(any)`
    or `[]any`.

    Intent: each of these three sites independently builds a type assertion;
    missing the take_types branch at any one emits an ill-typed read.  This
    test drives all three through the public renderer so a regression at any
    single site is caught.
    """
    from clio.emitters._go_flow_renderer import _render_chain_item
    from clio.ir.graph import (
        CallIR,
        ForEachIR,
        MatchBlockIR,
        MatchCaseIR,
    )

    # MATCH on a contract TAKE.
    match_item = MatchBlockIR(
        state_field="who",
        sub_field="level",
        cases=(MatchCaseIR(value="high", body=(), line=1),),
        line=1,
    )
    lines, _ = _render_chain_item(
        match_item, "kwargs", "\t",
        steps_by_name={},
        state_field_to_step={},
        contracts_by_name={},
        scope_local=set(),
        take_types={"who": "contracts.Customer"},
    )
    assert any(
        'switch state["who"].(contracts.Customer).Level {' in ln for ln in lines
    ), lines

    # Sequential FOR EACH over a list TAKE.
    fe_item = ForEachIR(
        loop_var="u",
        collection="urls",
        body=(),
        line=1,
        parallel=False,
    )
    fe_lines, _ = _render_chain_item(
        fe_item, "kwargs", "\t",
        steps_by_name={},
        state_field_to_step={},
        contracts_by_name={},
        scope_local=set(),
        take_types={"urls": "[]string"},
    )
    assert any(
        'for _, u := range state["urls"].([]string) {' in ln for ln in fe_lines
    ), fe_lines

    # Parallel FOR EACH over a list TAKE.
    fep_item = ForEachIR(
        loop_var="u",
        collection="urls",
        body=(),
        line=1,
        parallel=True,
        collector="results",
    )
    fep_lines, _ = _render_chain_item(
        fep_item, "kwargs", "\t",
        steps_by_name={},
        state_field_to_step={},
        contracts_by_name={},
        scope_local=set(),
        take_types={"urls": "[]string"},
    )
    assert any(
        '_items := state["urls"].([]string)' in ln for ln in fep_lines
    ), fep_lines
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_render_chain_item_threads_take_types_to_match_and_foreach -q
```
Expected failure: `TypeError: _render_chain_item() got an unexpected keyword argument 'take_types'`.

- [ ] **Step 3: Write minimal implementation**

In `clio/emitters/_go_flow_renderer.py`, add `take_types` to `_render_chain_item`'s signature (after `suppress_state_write`, line 122):

```python
def _render_chain_item(
    item: object,
    prev_var: str,
    indent: str,
    *,
    steps_by_name: dict[str, StepIR],
    state_field_to_step: dict[str, StepIR],
    contracts_by_name: dict[str, ContractIR],
    scope_local: set[str],
    rescues_by_step: dict[str, RescueBlockIR] | None = None,
    suppress_state_write: bool = False,
    take_types: dict[str, str] | None = None,
) -> tuple[list[str], str]:
```

Add one line at the top of the body (just after `_rescues = rescues_by_step or {}`, line 145):

```python
    _takes = take_types or {}
```

**(a) CallIR** — pass `_takes` to both `_kwargs_to_step_input` calls. The top-level call (line 152-158):

```python
        input_init = _kwargs_to_step_input(
            item,
            step,
            contracts_by_name,
            state_field_to_step,
            scope_local,
            _takes,
        )
```

And the rescue-body sub-call (lines 195-197):

```python
                    sub_input = _kwargs_to_step_input(
                        sub, sub_step, contracts_by_name, state_field_to_step,
                        scope_local, _takes,
                    )
```

**(b) Every recursive `_render_chain_item(...)` call** (the IF then/else, MATCH arm, WHILE body, seq FOR EACH body, parallel FOR EACH body — lines 254, 268, 313, 332, 367, 419) gains `take_types=_takes`. Example, the IF then-branch (lines 253-261):

```python
        for sub in item.then_body:
            sub_lines, cur = _render_chain_item(
                sub, cur, inner_indent,
                steps_by_name=steps_by_name,
                state_field_to_step=state_field_to_step,
                contracts_by_name=contracts_by_name,
                scope_local=scope_local,
                rescues_by_step=_rescues,
                take_types=_takes,
            )
            lines.extend(sub_lines)
```

Apply the identical `take_types=_takes` addition to the else-branch (line 268), MATCH arm (line 313), WHILE body (line 332), seq FOR EACH body (line 367), and parallel FOR EACH body (line 419, which also keeps `suppress_state_write=True`).

**(c) IF condition** (line 248) and **WHILE condition** (line 327) — pass `_takes` to `_go_condition_expr`:

```python
        cond = _go_condition_expr(item.condition, scope_local, state_field_to_step, _takes)
```

(both the IF and WHILE lines get the trailing `, _takes`).

**(d) MATCH scrutinee** (replace lines 286-296). Insert the TAKE branch with correct precedence (scope_local first, then producer, then take_types):

```python
        state_field = item.state_field
        step = state_field_to_step.get(state_field)
        if state_field in scope_local:
            if step is not None:
                cls = _to_class_name(step.name)
                base = f"{state_field}.(steps.{cls}Out)"
            else:
                base = f"{state_field}.(any)"
        elif step is not None:
            cls = _to_class_name(step.name)
            base = f'state["{state_field}"].(steps.{cls}Out)'
        elif state_field in _takes:
            base = f'state["{state_field}"].({_takes[state_field]})'
        else:
            base = f'state["{state_field}"].(any)'
        gf = _to_go_field_name(item.sub_field)
        subject_expr = f"{base}.{gf}"
```

**(e) Sequential FOR EACH collection** (replace lines 351-360):

```python
        coll_name = item.collection
        coll_step = state_field_to_step.get(coll_name)
        if coll_step is not None:
            coll_cls = _to_class_name(coll_step.name)
            coll_gf = _to_go_field_name(coll_name)
            coll_expr = f'state["{coll_name}"].(steps.{coll_cls}Out).{coll_gf}'
        elif coll_name in _takes:
            # List-typed flow TAKE — assert directly to its Go slice type.
            coll_expr = f'state["{coll_name}"].({_takes[coll_name]})'
        else:
            # Unknown collection source — fall back to untyped (should not
            # happen after IR validation).
            coll_expr = f'state["{coll_name}"].([]any)'
```

**(f) Parallel FOR EACH collection** (replace lines 395-402) with the identical three-branch logic:

```python
        coll_name = item.collection
        coll_step = state_field_to_step.get(coll_name)
        if coll_step is not None:
            coll_cls = _to_class_name(coll_step.name)
            coll_gf = _to_go_field_name(coll_name)
            coll_expr = f'state["{coll_name}"].(steps.{coll_cls}Out).{coll_gf}'
        elif coll_name in _takes:
            coll_expr = f'state["{coll_name}"].({_takes[coll_name]})'
        else:
            coll_expr = f'state["{coll_name}"].([]any)'
```

- [ ] **Step 4: Run test to verify it passes, and existing chain tests still pass**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_render_chain_item_threads_take_types_to_match_and_foreach tests/test_emitters/test_go.py -q
```
Expected: the new test passes and the full `test_go.py` module is green (the golden tests still pass because the existing fixtures have no entry TAKES, so `_takes` is empty and every reader takes the unchanged producer/loop-var branch — byte-identical output).

- [ ] **Step 5: Commit**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && git add clio/emitters/_go_flow_renderer.py tests/test_emitters/test_go.py && git commit -m "feat(go): thread take_types through all five chain-reader sites"
```

---

### Task 6: Retrofit `render_flow_go`'s `Run` — seed entry TAKES + build the per-flow maps + thread take_types

**Files:**
- Test: `tests/test_emitters/test_go.py` (append after Task 5's test)
- Test fixture: `tests/fixtures/go_entry_takes.clio` (new)
- Modify: `clio/emitters/_go_flow_renderer.py` (`render_flow_go` lines 453-517)

- [ ] **Step 1: Write the failing test + the fixture**

Create `tests/fixtures/go_entry_takes.clio`:

```
STEP fetch
  TAKES: url: str
  GIVES: body: str
  MODE:  exact
  LANG:  go

FLOW pipeline
  TAKES: url: str
  GIVES: body: str
  fetch(url=url)

RESOURCES
  target: go
  models: [haiku]
```

Append the test to `tests/test_emitters/test_go.py`:

```python
def test_run_seeds_entry_takes_and_reads_them_typed(tmp_path: Path) -> None:
    """The entry flow's Run seeds `state["<take>"] = kwargs["<take>"]` for each
    declared TAKE, and a downstream `@take` reference reads it via a direct
    value assertion (not the untyped fallback).

    Intent: without the seed line, `state["url"]` is nil at the `fetch` call;
    without the typed read, `steps.FetchIn{ Url: state["url"] }` assigns `any`
    to a `string` field and the module fails `go build`.  Both halves of the
    retrofit are pinned here.
    """
    out = tmp_path / "out"
    _compile(FIXTURES / "go_entry_takes.clio", out)
    body = (out / "flow" / "flow.go").read_text()
    # Entry TAKE is seeded from kwargs.
    assert 'state["url"] = kwargs["url"]' in body
    # The @url reference reads the seeded value with a typed assertion.
    assert 'steps.FetchIn{ Url: state["url"].(string) }' in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_run_seeds_entry_takes_and_reads_them_typed -q
```
Expected failure: `AssertionError` — `state["url"] = kwargs["url"]` is absent and the kwarg renders as the untyped `state["url"]` (no `.(string)`), because `render_flow_go` does not yet seed TAKES or build `take_types`.

- [ ] **Step 3: Write minimal implementation**

In `clio/emitters/_go_flow_renderer.py`, replace the body of `render_flow_go` from the `state_field_to_step` build through the chain loop (lines 468-514). New body:

```python
    contracts_by_name = {c.name: c for c in graph.contracts}
    steps_by_name = {s.name: s for s in graph.steps if isinstance(s, StepIR)}
    # Per-flow producer map (chain + rescues + nested bodies) for the entry
    # flow — replaces the old global build so it matches the sub-flow path.
    state_field_to_step = _build_state_field_to_step(graph.flow, steps_by_name)
    # Entry-flow TAKES are produced by no step; seed them into state and
    # register their Go types so `@<take>` reads assert to the right type.
    take_types = _build_take_field_to_gotype(graph.flow, contracts_by_name)
    # Maps protected step name → RescueBlockIR for RESCUE handlers (T16).
    rescues_by_step: dict[str, RescueBlockIR] = {
        rb.step_name: rb for rb in graph.flow.rescues
    }

    # Build the import block dynamically so errgroup is only included when
    # the flow contains a FOR EACH PARALLEL block (T17).
    import_lines: list[str] = ['\t"context"', ""]
    if _flow_uses_parallel(graph):
        import_lines.append('\t"golang.org/x/sync/errgroup"')
        import_lines.append("")
    import_lines.append(f'\t"{pkg}/steps"')

    lines: list[str] = [
        "package flow",
        "",
        "// Auto-generated by CLIO. Do not edit by hand.",
        "",
        "import (",
        *import_lines,
        ")",
        "",
        "func Run(ctx context.Context, kwargs map[string]any) (map[string]any, error) {",
        "\tstate := map[string]any{}",
        "",
    ]
    # Seed entry-flow TAKES from kwargs (name-sorted for deterministic goldens).
    for f in sorted(graph.flow.takes, key=lambda fld: fld.name):
        lines.append(f'\tstate["{f.name}"] = kwargs["{f.name}"]')
    if graph.flow.takes:
        lines.append("")
    prev_var = "kwargs"
    for elem in graph.flow.chain:
        elem_lines, prev_var = _render_chain_item(
            elem,
            prev_var,
            "\t",
            steps_by_name=steps_by_name,
            state_field_to_step=state_field_to_step,
            contracts_by_name=contracts_by_name,
            scope_local=set(),
            rescues_by_step=rescues_by_step,
            take_types=take_types,
        )
        lines.extend(elem_lines)
    lines.append("\treturn state, nil")
    lines.append("}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_run_seeds_entry_takes_and_reads_them_typed -q
```
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && git add clio/emitters/_go_flow_renderer.py tests/fixtures/go_entry_takes.clio tests/test_emitters/test_go.py && git commit -m "feat(go): Run seeds entry-flow TAKES and threads take_types through the chain"
```

---

### Task 7: Real `go build` of the entry-TAKES module (the type-assertion guard a grep cannot give)

**Files:**
- Test: `tests/test_emitters/test_go_compile.py` (append after `test_go_build_passes_on_minimal_contract_flow`)

- [ ] **Step 1: Write the failing test**

```python
def test_go_build_passes_on_entry_takes_flow(tmp_path: Path) -> None:
    """A flow whose entry FLOW declares a TAKE and reads it via `@take` must
    compile.  This is the type-assertion guard: if the seed line is missing,
    `state["url"]` is nil; if the typed read is missing, `any` is assigned to
    a `string` field — either way `go build` fails.  A string-grep test cannot
    catch this class of regression, so the build is the real verification.
    """
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP fetch\n"
        "  TAKES: url: str\n"
        "  GIVES: body: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW pipeline\n"
        "  TAKES: url: str\n"
        "  GIVES: body: str\n"
        "  fetch(url=url)\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)

    tidy_env = {
        "GOFLAGS": "-mod=mod",
        "HOME": str(out / ".gohome"),
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin:/bin"),
    }
    subprocess.run(
        ["go", "mod", "tidy"], cwd=out, check=True, capture_output=True, env=tidy_env,
    )
    result = _go_build(out)
    assert result.returncode == 0, (
        f"go build failed:\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
```

- [ ] **Step 2: Run test to verify it fails (pre-implementation) / passes (post-implementation)**

This test depends on Task 6's implementation already being in place. To prove it is a real guard, temporarily prove it would fail without the seed: run it now (after Task 6) — it should PASS. To demonstrate it catches the regression, you may transiently comment out the seed loop in `render_flow_go` and re-run to observe `go build` failing with `cannot use state["url"] (variable of type any) as string value`; then restore the seed loop. (Do not commit the transient edit.)

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go_compile.py::test_go_build_passes_on_entry_takes_flow -q
```
Expected: `1 passed` (or `skipped` if the `go` toolchain is not on PATH).

- [ ] **Step 3: Write minimal implementation**

No production code change — the implementation landed in Task 6. This task adds the build-level test that pins it.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go_compile.py -q
```
Expected: both compile tests pass (or skip together when `go` is absent).

- [ ] **Step 5: Commit**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && git add tests/test_emitters/test_go_compile.py && git commit -m "test(go): go build guard for entry-flow TAKES typed read"
```

---

### Task 8: Regenerate goldens + eyeball (verify the per-flow map switch is byte-identical for unsigned entry flows)

**Files:**
- Verify only: `tests/fixtures/expected_go/go_minimal`, `.../go_judgment`, `.../go_parallel`, `.../mvp_go`

> **Note for the assembler:** the design doc asserts existing goldens (e.g. `go_parallel`) WILL change. That is **not** true for Phase 4 in isolation — none of the four committed golden fixtures declare entry-FLOW TAKES (their FLOWs are unsigned), so the seeding loop emits zero lines and the per-flow `_build_state_field_to_step` switch produces the same producer map as the old global build. This task therefore VERIFIES a zero-diff, which is itself a meaningful regression check (it proves the Run refactor did not perturb the unsigned-flow path). Goldens change in Phase 5 (sub-flow funcs), not here.

- [ ] **Step 1: Write the failing test** — none. This is a verification task against existing golden tests, not new behaviour. The four `test_golden_go_*` / `test_golden_mvp_go` tests are the assertion.

- [ ] **Step 2: Run the golden tests to confirm zero drift**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py -k "golden" -q
```
Expected: `test_golden_go_minimal`, `test_golden_go_judgment`, `test_golden_go_parallel`, `test_golden_mvp_go` all PASS unchanged (proving the Run refactor is byte-identical for unsigned entry flows).

- [ ] **Step 3: Regenerate + diff to eyeball (defensive — must produce no change)**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && \
python -m clio compile tests/fixtures/go_minimal.clio  --target go --output /tmp/regen_go_minimal && \
python -m clio compile tests/fixtures/go_judgment.clio --target go --output /tmp/regen_go_judgment && \
python -m clio compile tests/fixtures/go_parallel.clio --target go --output /tmp/regen_go_parallel && \
python -m clio compile examples/mvp_go.clio            --target go --output /tmp/regen_mvp_go && \
diff -ru tests/fixtures/expected_go/go_minimal  /tmp/regen_go_minimal  ; \
diff -ru tests/fixtures/expected_go/go_judgment /tmp/regen_go_judgment ; \
diff -ru tests/fixtures/expected_go/go_parallel /tmp/regen_go_parallel ; \
diff -ru tests/fixtures/expected_go/mvp_go      /tmp/regen_mvp_go
```
Expected: every `diff` prints nothing (exit 0). If any diff is non-empty, STOP — the Run refactor regressed the unsigned-flow path; fix before proceeding. (No `git add` of regenerated trees — they are identical to what is committed.)

- [ ] **Step 4: Run the full Go suite + lint/type gates (per MEMORY verify gates)**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && \
uv run ruff check clio/emitters/_go_flow_renderer.py clio/emitters/_shared_utils.py --fix && \
uv run mypy clio/emitters/_go_flow_renderer.py clio/emitters/_shared_utils.py && \
uv run pytest tests/test_emitters/test_go.py tests/test_emitters/test_go_compile.py -q
```
Expected: ruff clean, mypy clean (watch the new `dict[str, str]` take_types params vs the `dict[str, StepIR]` state map — they must not be conflated), full Go suite green.

- [ ] **Step 5: Commit** — only if ruff applied a fix; otherwise nothing to commit (the golden trees are unchanged by design).

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && git add -A && git diff --cached --quiet || git commit -m "chore(go): lint pass after take_types threading; goldens verified unchanged"
```

---

## Phase 5 — `FlowCallIR` arm + `run<Name>` sub-flow funcs + boundary extension

> **Depends on Phase 4** (referenced, not re-created here):
> - `_build_state_field_to_step(flow: FlowIR, steps_by_name: dict[str, StepIR]) -> dict[str, StepIR]` — walks `flow.chain` + `flow.rescues` + nested IF/MATCH/WHILE/FOR EACH bodies, mapping each producer step's `gives.name -> StepIR`.
> - `_build_take_field_to_gotype(flow: FlowIR, contracts: dict[str, ContractIR]) -> dict[str, str]` — `{f.name: _type_to_go(f.type, contracts, qualifier="contracts") for f in flow.takes}`.
> - The `take_types: dict[str, str]` (default `{}`) param threaded into all five readers (`_go_kwarg_value`, MATCH scrutinee, sequential & parallel FOR EACH collection resolvers, `_go_condition_expr`). A ref that is a key in `take_types` (and not in `scope_local`, not in `state_field_to_step`) emits a direct value assertion — scalar `state["x"].(string)`, contract `state["x"].(contracts.<Cls>).<Field>`.
> - `render_flow_go.Run` already: builds its per-flow `state_field_to_step` via `_build_state_field_to_step`, builds `take_types` via `_build_take_field_to_gotype`, seeds `state["<take>"] = kwargs["<take>"]` for each entry TAKE, and threads both maps into `_render_chain_item`.
>
> Phase 5 builds: (a) a shared `_render_flow_body` used by both `Run` and `run<Name>`; (b) one unexported `func run<Name>(ctx, <takes...>) (map[string]any, error)` per callable sub-flow, name-sorted, seeding TAKES and returning the GIVES subset; (c) the `FlowCallIR` arm in `_render_chain_item` (top-level flat-merge + boundary extension, nested invoke-without-bind, parallel single-GIVES collector); (d) a name-collision guard for `run<Name>` vs `Run`.

> The existing `_compile()` helper in `tests/test_emitters/test_go.py` hard-codes `flow=None`. `examples/flow_composition.clio` and the new sub-flow fixtures declare **multiple** FLOWs, so `build_ir(flow_name=None)` leaves `graph.flow=None` (`builder.py:234` only auto-selects when `len(all_flows)==1`). Sub-flow tests therefore need an entry-flow-selecting compile helper. **Task 1 adds it.**

---

### Task 1: `_compile_flow` test helper (entry-flow selector for multi-FLOW fixtures)

**Files:**
- Modify: `tests/test_emitters/test_go.py` (top of file, after the existing `_compile`, around lines 18-20)

- [ ] **Step 1: Write the failing test**

Add this test to `tests/test_emitters/test_go.py` (append near the other top-level helper tests, e.g. after `test_target_go_is_registered_in_cli`):

```python
def test_compile_flow_helper_selects_named_entry(tmp_path: Path) -> None:
    """_compile_flow must select a named entry FLOW from a multi-FLOW source,
    so graph.flow is non-None and flow/flow.go renders a real orchestrator
    rather than the empty `graph.flow is None` fallback."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP a\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW sub\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  a(x=x)\n"
        "FLOW pipeline\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  sub(x=x)\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile_flow(src, out, "pipeline")
    body = (out / "flow" / "flow.go").read_text()
    # Real orchestrator, not the empty fallback.
    assert "func Run(ctx context.Context, kwargs map[string]any) (map[string]any, error) {" in body
    assert "return map[string]any{}, nil" not in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_compile_flow_helper_selects_named_entry -x -q
```

Expected failure: `NameError: name '_compile_flow' is not defined` (the helper does not exist yet).

- [ ] **Step 3: Write minimal implementation**

In `tests/test_emitters/test_go.py`, directly below the existing `_compile` function (currently lines 18-20), add:

```python
def _compile_flow(source_path: Path, output_dir: Path, flow_name: str) -> None:
    """Run `clio compile <source> --target go --flow <flow_name> --output <out>`
    in-process. Required for multi-FLOW fixtures (composition tests), where
    flow=None would leave graph.flow unset."""
    _cmd_compile(str(source_path), "go", str(output_dir), flow_name)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_compile_flow_helper_selects_named_entry -x -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && git add tests/test_emitters/test_go.py && git commit -m "test(go): add _compile_flow helper for multi-FLOW composition fixtures"
```

---

### Task 2: `_render_flow_body` — shared chain-walking body renderer

Extract the per-function body (chain walk + per-flow maps) into a single helper so `Run` and every `run<Name>` produce identical orchestration. This refactor must leave the existing `Run` output **byte-identical** (verified by the existing golden + flow tests).

**Files:**
- Modify: `clio/emitters/_go_flow_renderer.py` — add `_render_flow_body` (new function before `render_flow_go`, ~line 452); retrofit `render_flow_go.Run` (lines 489-516) to call it.
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_emitters/test_go.py`:

```python
def test_render_flow_body_drives_run(tmp_path: Path) -> None:
    """Run's chain body is produced by the shared _render_flow_body helper.
    A single-FLOW exact pipeline still emits the seed + step call + state
    write + return through the shared body path (no regression)."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP load\n"
        "  TAKES: file: str\n"
        "  GIVES: rows: List<str>\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW pipeline\n"
        "  TAKES: file: str\n"
        "  GIVES: rows: List<str>\n"
        "  load(file=file)\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "flow" / "flow.go").read_text()
    # Entry TAKES seeding (Phase 4 retrofit, exercised through shared body).
    assert 'state["file"] = kwargs["file"]' in body
    # @file read resolves via take_types -> direct scalar assertion.
    assert 'steps.Load(ctx, steps.LoadIn{ File: state["file"].(string) })' in body
    assert 'state["rows"] = loadOut' in body
    assert "return state, nil" in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_render_flow_body_drives_run -x -q
```

Expected failure: assertion error on the seed/return lines if `_render_flow_body` is not yet the path (or, if Phase 4 already seeds TAKES, this confirms the extraction preserves it). The intent: after extraction, the body is produced by `_render_flow_body`.

- [ ] **Step 3: Write minimal implementation**

In `clio/emitters/_go_flow_renderer.py`, add `_render_flow_body` immediately before `render_flow_go` (~line 452). It returns the body lines for one flow function — the `state := map[string]any{}` init, TAKES seeding, the chain walk, and the final return. The `return_fields` arg distinguishes `Run` (returns the whole `state`) from `run<Name>` (returns the GIVES subset):

```python
def _render_flow_body(
    flow: FlowIR,
    *,
    steps_by_name: dict[str, StepIR],
    contracts_by_name: dict[str, ContractIR],
    take_types: dict[str, str],
    return_fields: tuple[str, ...] | None,
) -> list[str]:
    """Render the body of one flow function (Run or run<Name>).

    Shared by `Run` (return_fields=None -> `return state, nil`) and each
    `run<Name>` (return_fields=GIVES names -> `return map[string]any{...}, nil`).

    Builds this flow's own per-flow `state_field_to_step` (collision-free
    inside one flow — the IR forbids two producers of one field), seeds the
    TAKES into state, then threads a *mutable running copy* of that map plus
    `take_types` through `_render_chain_item`, so each top-level FlowCallIR
    arm can extend the map (boundary extension) before downstream items read
    the sub-flow's published GIVES.
    """
    state_field_to_step = _build_state_field_to_step(flow, steps_by_name)
    rescues_by_step: dict[str, RescueBlockIR] = {
        rb.step_name: rb for rb in flow.rescues
    }
    body: list[str] = ["\tstate := map[string]any{}"]
    # Seed each TAKE so reads resolve via take_types (Phase 4 retrofit).
    for f in flow.takes:
        body.append(f'\tstate["{f.name}"] = kwargs["{f.name}"]'
                    if return_fields is None
                    else f'\tstate["{f.name}"] = {f.name}')
    body.append("")
    # Running, mutable per-flow map — extended at FlowCallIR boundaries.
    running_map = dict(state_field_to_step)
    prev_var = "kwargs"
    for elem in flow.chain:
        elem_lines, prev_var = _render_chain_item(
            elem,
            prev_var,
            "\t",
            steps_by_name=steps_by_name,
            state_field_to_step=running_map,
            contracts_by_name=contracts_by_name,
            scope_local=set(),
            rescues_by_step=rescues_by_step,
            take_types=take_types,
        )
        body.extend(elem_lines)
    if return_fields is None:
        body.append("\treturn state, nil")
    else:
        pairs = ", ".join(f'"{g}": state["{g}"]' for g in return_fields)
        body.append(f"\treturn map[string]any{{{pairs}}}, nil")
    return body
```

> NOTE: `_render_chain_item` gains the `take_types` keyword param in Phase 4. The `running_map` is a *copy* of the per-flow map; the FlowCallIR arm (Task 4) mutates this copy in place as it walks, so downstream sibling items see the sub-flow's published producers.

Then retrofit `render_flow_go.Run` (lines 489-516) to delegate to `_render_flow_body`. Replace the block from `lines: list[str] = [` (line 489) through `lines.append("}")` (line 516) with:

```python
    take_types = _build_take_field_to_gotype(graph.flow, contracts_by_name)
    lines: list[str] = [
        "package flow",
        "",
        "// Auto-generated by CLIO. Do not edit by hand.",
        "",
        "import (",
        *import_lines,
        ")",
        "",
        "func Run(ctx context.Context, kwargs map[string]any) (map[string]any, error) {",
    ]
    lines.extend(
        _render_flow_body(
            graph.flow,
            steps_by_name=steps_by_name,
            contracts_by_name=contracts_by_name,
            take_types=take_types,
            return_fields=None,
        )
    )
    lines.append("}")
```

Add `FlowIR` to the `from clio.ir.graph import (...)` block at the top of the file (it is not currently imported).

> Delete the now-orphaned `prev_var = "kwargs"` / `for elem in graph.flow.chain:` loop and the inline `state := map[string]any{}` init that Phase 4 left at lines 498-514 — they are subsumed by `_render_flow_body`. Re-read the file after this Edit to confirm no orphaned suffix remains (MEMORY: verify partial-string Edits).

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_render_flow_body_drives_run tests/test_emitters/test_go.py -k "flow or golden or for_each or render_flow_body" -q
```

Expected: all pass (the extraction is byte-preserving; existing flow/golden/for-each tests stay green).

- [ ] **Step 5: Commit**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run ruff check clio/emitters/_go_flow_renderer.py --fix && git add clio/emitters/_go_flow_renderer.py tests/test_emitters/test_go.py && git commit -m "refactor(go): extract _render_flow_body shared by Run and run<Name>"
```

---

### Task 3: `run<Name>` emission — one unexported func per callable sub-flow

`render_flow_go` appends one `func run<Name>(ctx, <takes...>) (map[string]any, error)` per callable sub-flow (`flow.takes and flow.gives`, excluding `graph.flow`), name-sorted, each delegating to `_render_flow_body`. No FlowCallIR call sites exist yet (Task 4), so this only proves the function definitions render correctly.

**Files:**
- Modify: `clio/emitters/_go_flow_renderer.py` — `render_flow_go`, after the `Run` block (~line 516, before `return "\n".join(lines) + "\n"`)
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing test**

```python
def test_subflow_funcs_emitted_name_sorted(tmp_path: Path) -> None:
    """Each callable sub-flow (TAKES + GIVES, != entry) emits an unexported
    run<Name>(ctx, <takes...>) (map[string]any, error). Order is name-sorted
    for deterministic goldens. The entry flow is NOT re-emitted as run<Name>."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP fetch\n"
        "  TAKES: url: str\n"
        "  GIVES: article: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "STEP sumz\n"
        "  TAKES: article: str\n"
        "  GIVES: summary: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW zeta\n"
        "  TAKES: url: str\n"
        "  GIVES: summary: str\n"
        "  fetch(url=url) -> sumz(article=article)\n"
        "FLOW alpha\n"
        "  TAKES: url: str\n"
        "  GIVES: article: str\n"
        "  fetch(url=url)\n"
        "FLOW pipeline\n"
        "  TAKES: url: str\n"
        "  GIVES: summary: str\n"
        "  zeta(url=url)\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile_flow(src, out, "pipeline")
    body = (out / "flow" / "flow.go").read_text()
    # Both sub-flows emitted, entry (pipeline) not re-emitted as a func.
    assert "func runAlpha(ctx context.Context, url string) (map[string]any, error) {" in body
    assert "func runZeta(ctx context.Context, url string) (map[string]any, error) {" in body
    assert "func runPipeline(" not in body
    # Name-sorted: alpha before zeta.
    assert body.index("func runAlpha(") < body.index("func runZeta(")
    # GIVES-subset return for each.
    assert 'return map[string]any{"article": state["article"]}, nil' in body
    assert 'return map[string]any{"summary": state["summary"]}, nil' in body
    # TAKES seeded from the param (not kwargs) inside a run<Name>.
    assert 'state["url"] = url' in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_subflow_funcs_emitted_name_sorted -x -q
```

Expected failure: `runAlpha`/`runZeta` funcs are absent from `flow.go` (only `Run` is emitted).

- [ ] **Step 3: Write minimal implementation**

In `clio/emitters/_go_flow_renderer.py`, inside `render_flow_go`, after the `lines.append("}")` that closes `Run` (~line 516) and before `return "\n".join(lines) + "\n"`, append the sub-flow functions:

```python
    # v0.23: one unexported run<Name> per callable sub-flow (TAKES + GIVES),
    # excluding the entry flow. Name-sorted for deterministic goldens; Go
    # resolves package-level funcs regardless of source order, so A->B->C
    # nesting needs no topological sort.
    entry_name = graph.flow.name
    callable_subflows = sorted(
        (
            f for f in graph.flows
            if f.name != entry_name and f.takes and f.gives
        ),
        key=lambda f: f.name,
    )
    for sub in callable_subflows:
        sub_cls = _to_class_name(sub.name)
        params = ", ".join(
            f"{f.name} {_type_to_go(f.type, contracts_by_name, qualifier='contracts')}"
            for f in sub.takes
        )
        sub_take_types = _build_take_field_to_gotype(sub, contracts_by_name)
        lines.append("")
        lines.append(
            f"func run{sub_cls}(ctx context.Context, {params}) (map[string]any, error) {{"
        )
        lines.extend(
            _render_flow_body(
                sub,
                steps_by_name=steps_by_name,
                contracts_by_name=contracts_by_name,
                take_types=sub_take_types,
                return_fields=tuple(g.name for g in sub.gives),
            )
        )
        lines.append("}")
```

Add `_type_to_go` and `_build_take_field_to_gotype` to imports if not already present (`_type_to_go` from `clio.emitters._shared_utils`; `_build_take_field_to_gotype` is defined in this module by Phase 4).

> The `run<Name>` param TAKES are seeded as `state["<take>"] = <take>` (the bare param) — driven by `return_fields is not None` in `_render_flow_body` (Task 2). The entry `Run` seeds from `kwargs["<take>"]`.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_subflow_funcs_emitted_name_sorted -x -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run ruff check clio/emitters/_go_flow_renderer.py --fix && git add clio/emitters/_go_flow_renderer.py tests/test_emitters/test_go.py && git commit -m "feat(go): emit one run<Name> func per callable sub-flow (name-sorted)"
```

---

### Task 4: `FlowCallIR` arm — top-level call + flat-merge + boundary extension

Add the `FlowCallIR` arm in `_render_chain_item` (replacing the `NotImplementedError` at line 448). Top-level shape: emit `_sub<Name>, err := run<Name>(...)`; propagate the error; flat-merge `for k, v := range _sub<Name> { state[k] = v }`; **boundary-extend** the running `state_field_to_step` so downstream reads of the sub-flow's published GIVES resolve to the inner producer's concrete `steps.<Cls>Out`.

**Files:**
- Modify: `clio/emitters/_go_flow_renderer.py` — `_render_chain_item`, add the `FlowCallIR` arm before line 448; thread a per-flow `steps_by_name`-keyed lookup for the sub-flow's own producer map.
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing test**

```python
def test_subflow_call_site_flat_merge(tmp_path: Path) -> None:
    """A top-level sub-flow call emits run<Name>(...) + flat-merge into state,
    mirroring python's state.update(run_<name>(...)). Positional kwargs are
    rendered in flow.takes order. A downstream read of the sub-flow's
    published GIVES asserts to the inner producer's typed Out struct
    (boundary extension)."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP fetch\n"
        "  TAKES: url: str\n"
        "  GIVES: article: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "STEP shout\n"
        "  TAKES: article: str\n"
        "  GIVES: loud: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW enrich\n"
        "  TAKES: url: str\n"
        "  GIVES: article: str\n"
        "  fetch(url=url)\n"
        "FLOW pipeline\n"
        "  TAKES: url: str\n"
        "  GIVES: loud: str\n"
        "  enrich(url=url) -> shout(article=article)\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile_flow(src, out, "pipeline")
    body = (out / "flow" / "flow.go").read_text()
    # Call site: run<Name>, positional in flow.takes order, @url -> take ref.
    assert '_subEnrich, err := runEnrich(ctx, state["url"].(string))' in body
    assert "if err != nil {" in body
    # Typed flat-merge (verbatim interface copy).
    assert "for k, v := range _subEnrich {" in body
    assert "state[k] = v" in body
    # Boundary extension: downstream shout reads @article, which enrich
    # publishes from its inner Fetch step -> assert to steps.FetchOut.
    assert 'steps.Shout(ctx, steps.ShoutIn{ Article: state["article"].(steps.FetchOut).Article })' in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_subflow_call_site_flat_merge -x -q
```

Expected failure: `NotImplementedError: chain item kind not yet supported in v0.20.0: FlowCallIR` raised from `_render_chain_item`.

- [ ] **Step 3: Write minimal implementation**

In `clio/emitters/_go_flow_renderer.py`:

(a) Add `FlowCallIR` and `FlowIR` to the `from clio.ir.graph import (...)` block (if not already added in Task 2).

(b) Update the `_render_chain_item` signature to accept `flows_by_name` and `steps_by_name_for_subflows` so the arm can build the sub-flow's own per-flow producer map. Add two keyword params with defaults to keep all existing call sites valid (they pass through via `**` only where needed). Concretely, add to the signature:

```python
    flows_by_name: dict[str, FlowIR] | None = None,
    take_types: dict[str, str] | None = None,
```

> `take_types` is already added in Phase 4; `flows_by_name` is new for Phase 5. Thread `flows_by_name=flows_by_name` and `take_types=take_types` into every recursive `_render_chain_item(...)` call inside the function (IF then/else, MATCH arms, WHILE body, sequential & parallel FOR EACH bodies) — there are seven recursive call sites; missing one drops the sub-flow context inside that control block.

(c) `_render_flow_body` (Task 2) must build `flows_by_name = {f.name: f for f in graph.flows}` and pass it into `_render_chain_item`. Since `_render_flow_body` only has the single `flow`, thread `flows_by_name` and `steps_by_name` down from `render_flow_go` into `_render_flow_body` as an extra param:

In `_render_flow_body`'s signature add `flows_by_name: dict[str, FlowIR]`, and forward it into the `_render_chain_item(... flows_by_name=flows_by_name, take_types=take_types)` call. In `render_flow_go`, build `flows_by_name = {f.name: f for f in graph.flows}` once and pass it to both the `Run` `_render_flow_body(...)` call and each `run<Name>` `_render_flow_body(...)` call.

(d) Insert the `FlowCallIR` arm in `_render_chain_item` immediately before the final `raise NotImplementedError` (line 448):

```python
    if isinstance(item, FlowCallIR):
        _flows = flows_by_name or {}
        _takes = take_types or {}
        sub_flow = _flows.get(item.flow_name)
        if sub_flow is None:
            # Unknown sub-flow — should not happen after IR validation.
            return [], prev_var
        sub_cls = _to_class_name(item.flow_name)
        # Render positional kwargs in flow.takes order (run<Name> is positional).
        take_order = [f.name for f in sub_flow.takes]
        by_take = {name: value for name, value in item.kwargs}
        arg_exprs = [
            _go_kwarg_value(
                by_take[t], contracts_by_name, state_field_to_step,
                scope_local, take_types=_takes,
            )
            for t in take_order
        ]
        args = ", ".join(["ctx", *arg_exprs])

        if suppress_state_write:
            # Parallel goroutine body — handled in Task 5 (single-GIVES).
            return _render_subflow_parallel_body(
                item, sub_flow, args, indent,
                steps_by_name=steps_by_name,
            )

        if scope_local:
            # Nested scope (FOR EACH / IF / MATCH / WHILE body): invoke without
            # binding, mirroring python's invoke-without-bind. Errors propagate.
            return (
                [
                    f"{indent}if _, err := run{sub_cls}({args}); err != nil {{",
                    f"{indent}\treturn nil, err",
                    f"{indent}}}",
                    "",
                ],
                prev_var,
            )

        # Top-level: call + typed flat-merge + boundary extension.
        sub_var = f"_sub{sub_cls}"
        lines = [
            f"{indent}{sub_var}, err := run{sub_cls}({args})",
            f"{indent}if err != nil {{",
            f"{indent}\treturn nil, err",
            f"{indent}}}",
            f"{indent}for k, v := range {sub_var} {{",
            f"{indent}\tstate[k] = v",
            f"{indent}}}",
            "",
        ]
        # Boundary extension: the sub-flow publishes each GIVES field carrying
        # its inner producer's concrete steps.<Cls>Out value verbatim. Register
        # those producers in THIS flow's running map so downstream typed reads
        # of @<g> assert to the right struct. Last-writer-wins on collision
        # (parity with python's state.update + builder available[g] overwrite).
        sub_producers = _build_state_field_to_step(sub_flow, steps_by_name)
        for g in sub_flow.gives:
            producer = sub_producers.get(g.name)
            if producer is None:
                raise ValueError(
                    f"internal go emitter error: sub-flow {sub_flow.name!r} "
                    f"declares GIVES {g.name!r} with no producing step"
                )
            state_field_to_step[g.name] = producer  # mutate running map in place
        return lines, prev_var
```

> The `state_field_to_step` passed into `_render_chain_item` from `_render_flow_body` is the **mutable running copy** (Task 2). Mutating it here is the boundary extension — downstream siblings in the same chain see the extended map. `_build_state_field_to_step` is the Phase 4 helper.

(e) Add a stub for `_render_subflow_parallel_body` that Task 5 fills in. For now, to keep this commit self-contained and green, define it to raise so the parallel path is unmistakably unhandled until Task 5:

```python
def _render_subflow_parallel_body(
    item: FlowCallIR,
    sub_flow: FlowIR,
    args: str,
    indent: str,
    *,
    steps_by_name: dict[str, StepIR],
) -> tuple[list[str], str]:
    """Single-GIVES sub-flow as a FOR EACH PARALLEL body. Filled in Task 5."""
    raise NotImplementedError("sub-flow parallel body: Task 5")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_subflow_call_site_flat_merge tests/test_emitters/test_go.py -k "flow or golden or for_each" -q
```

Expected: `test_subflow_call_site_flat_merge` passes; existing flow/golden/for-each tests stay green.

- [ ] **Step 5: Commit**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run ruff check clio/emitters/_go_flow_renderer.py --fix && git add clio/emitters/_go_flow_renderer.py tests/test_emitters/test_go.py && git commit -m "feat(go): FlowCallIR arm — top-level call, flat-merge, boundary extension"
```

---

### Task 5: parallel goroutine body — single-GIVES typed collector

A sub-flow used as a `FOR EACH PARALLEL` body is rendered inside the goroutine via `_render_subflow_parallel_body`: `_sub, err := run<Name>(...)`, then `_g := _sub["<g0>"].(steps.<Producer>Out)`, returning `_g` as `cur_par` so the existing collector line `_results[_i] = {cur_par}` stores a typed struct. The parallel renderer (lines 380-446) must, when its single body item is a single-GIVES `FlowCallIR`, pre-allocate `_results := make([]steps.<Producer>Out, len(_items))` instead of `[]any`, and register the collector in the enclosing per-flow map so a downstream typed `FOR EACH x IN <collector>` ranges a typed slice. Multi-GIVES body → refused in Phase 6 (E_GO); here we only support single-GIVES.

**Files:**
- Modify: `clio/emitters/_go_flow_renderer.py` — `_render_subflow_parallel_body` (stub from Task 4) and the parallel `ForEachIR` arm's `_results` pre-allocation (lines 408-410) + collector registration.
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing test**

```python
def test_subflow_parallel_single_gives_typed_collector(tmp_path: Path) -> None:
    """A single-GIVES sub-flow as a FOR EACH PARALLEL body pre-allocates a
    typed []steps.<Producer>Out collector and extracts the GIVES field from
    the returned map via _g := _sub["<g0>"].(steps.<Producer>Out)."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP fetch\n"
        "  TAKES: url: str\n"
        "  GIVES: summary: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW enrich\n"
        "  TAKES: url: str\n"
        "  GIVES: summary: str\n"
        "  fetch(url=url)\n"
        "FLOW batch\n"
        "  TAKES: urls: List<str>\n"
        "  GIVES: results: List<str>\n"
        "  FOR EACH u IN urls PARALLEL AS results:\n"
        "    enrich(url=u)\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile_flow(src, out, "batch")
    body = (out / "flow" / "flow.go").read_text()
    # Typed collector pre-allocation (not []any).
    assert "_results := make([]steps.FetchOut, len(_items))" in body
    # GIVES extraction from the returned map inside the goroutine.
    assert '_subEnrich, err := runEnrich(ctx, u)' in body
    assert '_g := _subEnrich["summary"].(steps.FetchOut)' in body
    # Collector stores the typed struct.
    assert "_results[_i] = _g" in body
    assert 'state["results"] = _results' in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_subflow_parallel_single_gives_typed_collector -x -q
```

Expected failure: `NotImplementedError: sub-flow parallel body: Task 5` raised from `_render_subflow_parallel_body`.

- [ ] **Step 3: Write minimal implementation**

In `clio/emitters/_go_flow_renderer.py`:

(a) Fill in `_render_subflow_parallel_body` (replace the Task 4 stub):

```python
def _render_subflow_parallel_body(
    item: FlowCallIR,
    sub_flow: FlowIR,
    args: str,
    indent: str,
    *,
    steps_by_name: dict[str, StepIR],
) -> tuple[list[str], str]:
    """Single-GIVES sub-flow as a FOR EACH PARALLEL body.

    Emits `_sub<Name>, err := run<Name>(...)`, propagates the error (rewritten
    to `return err` by the enclosing goroutine), then extracts the lone GIVES
    field as a typed struct so the collector slot stores `steps.<Producer>Out`.
    Returns (_g) as the new prev_var (cur_par) for the collector line.

    Multi-GIVES bodies are refused upstream (Phase 6 E_GO) — asserted here for
    defence in depth.
    """
    if len(sub_flow.gives) != 1:
        raise ValueError(
            f"internal go emitter error: multi-GIVES sub-flow {sub_flow.name!r} "
            f"used as a typed FOR EACH PARALLEL body (should be refused upstream)"
        )
    sub_cls = _to_class_name(item.flow_name)
    sub_var = f"_sub{sub_cls}"
    g0 = sub_flow.gives[0].name
    producer = _build_state_field_to_step(sub_flow, steps_by_name).get(g0)
    if producer is None:
        raise ValueError(
            f"internal go emitter error: sub-flow {sub_flow.name!r} GIVES "
            f"{g0!r} has no producing step"
        )
    producer_cls = _to_class_name(producer.name)
    lines = [
        f"{indent}{sub_var}, err := run{sub_cls}({args})",
        f"{indent}if err != nil {{",
        f"{indent}\treturn nil, err",
        f"{indent}}}",
        f'{indent}_g := {sub_var}["{g0}"].(steps.{producer_cls}Out)',
    ]
    return lines, "_g"
```

> `return nil, err` is rewritten to `return err` by the existing `_rewrite_return_in_goroutine` (line 428) that the parallel arm applies to every body sub-line, so the goroutine signature `func() error` is honoured.

(b) In the parallel `ForEachIR` arm (lines 380-446), the `_results` slice type must be the producer's typed `Out` when the single body item is a single-GIVES `FlowCallIR`; otherwise keep `[]any`. Replace the `_results := make([]any, len(_items))` line (line 410) with a computed type. Just before building `par_lines`, derive the element type:

```python
        # Determine the collector element type. When the sole body item is a
        # single-GIVES sub-flow call, the collector holds the inner producer's
        # typed Out struct; otherwise (a step/control body) keep []any.
        results_type = "any"
        if (
            len(item.body) == 1
            and isinstance(item.body[0], FlowCallIR)
            and flows_by_name is not None
        ):
            _sf = flows_by_name.get(item.body[0].flow_name)
            if _sf is not None and len(_sf.gives) == 1:
                _prod = _build_state_field_to_step(_sf, steps_by_name).get(
                    _sf.gives[0].name
                )
                if _prod is not None:
                    results_type = f"steps.{_to_class_name(_prod.name)}Out"
```

and change the slice-alloc line to:

```python
            f"{indent}\t_results := make([]{results_type}, len(_items))",
```

(c) Collector registration in the enclosing per-flow map: after the parallel arm writes `state["<collector>"] = _results`, register the collector so a downstream `FOR EACH x IN <collector>` resolves a typed slice. The cleanest hook: when `results_type != "any"` and `item.collector is not None`, set `state_field_to_step[item.collector] = producer_step`. Add, just before `return par_lines, prev_var`:

```python
        if results_type != "any" and item.collector is not None and _prod is not None:
            state_field_to_step[item.collector] = _prod
```

> `state_field_to_step` here is the mutable running map (same object threaded by `_render_flow_body`), so this registration is visible to downstream siblings. `_prod` is in scope from the `results_type` derivation above.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_subflow_parallel_single_gives_typed_collector tests/test_emitters/test_go.py -k "parallel or for_each or golden" -q
```

Expected: new test passes; existing parallel/for-each/golden tests stay green (the `[]any` path is preserved for non-sub-flow bodies).

- [ ] **Step 5: Commit**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run ruff check clio/emitters/_go_flow_renderer.py --fix && git add clio/emitters/_go_flow_renderer.py tests/test_emitters/test_go.py && git commit -m "feat(go): single-GIVES sub-flow as typed FOR EACH PARALLEL collector"
```

---

### Task 6: name-collision guard — `run<Name>` vs `Run`

A sub-flow named `run` (or any name whose `_to_class_name` is empty/collides such that `run<Name>` would shadow `Run`, e.g. a sub-flow literally producing `func Run`) must not silently shadow the entry orchestrator. The risk surface is narrow — `_to_class_name` always capitalises, so `run<Cls>` starts with `run` lowercase and cannot equal `Run`; but a sub-flow named the same as the entry is already impossible (duplicate FLOW names are rejected at IR build, `builder.py:200`). The genuine collision is **two sub-flows whose `_to_class_name` collapse to the same `run<Name>`** (e.g. `my_flow` and `my-flow`, or `foo_bar` and `fooBar`). Guard against that with a clear emitter error.

**Files:**
- Modify: `clio/emitters/_go_flow_renderer.py` — `render_flow_go`, in the `callable_subflows` loop (Task 3).
- Test: `tests/test_emitters/test_go.py`

- [ ] **Step 1: Write the failing test**

```python
def test_subflow_name_collision_raises(tmp_path: Path) -> None:
    """Two sub-flows whose Go func names collapse to the same run<Name> must
    raise a clear emitter error rather than emit a duplicate func (which Go
    would reject with a redeclaration error far from the cause)."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP a\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW foo_bar\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  a(x=x)\n"
        "FLOW foo-bar\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  a(x=x)\n"
        "FLOW pipeline\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  foo_bar(x=x)\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="run func name collision"):
        _compile_flow(src, out, "pipeline")
```

> If the parser rejects `foo-bar` as a FLOW name before the emitter runs, replace the second flow name with another pair that collapses identically (e.g. `fooBar` vs `foo_bar`) — both `_to_class_name` to `FooBar`. Confirm which pair survives parsing during Step 2; pick the surviving pair.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_subflow_name_collision_raises -x -q
```

Expected failure: no `ValueError` raised (two `func runFooBar` lines emitted) — `DID NOT RAISE`. If instead the parser rejects the source, adjust the fixture per the Step 1 note and re-run until the failure is "DID NOT RAISE" (proving the guard is what's missing).

- [ ] **Step 3: Write minimal implementation**

In `render_flow_go`, inside the `for sub in callable_subflows:` loop (Task 3), track emitted Go func names and raise on collision. Add before the loop:

```python
    _seen_run_names: set[str] = set()
```

and at the top of the loop body, after computing `sub_cls`:

```python
        run_name = f"run{sub_cls}"
        if run_name in _seen_run_names:
            raise ValueError(
                f"E_GO: run func name collision — two sub-flows render to "
                f"{run_name!r}; rename one (Go func names must be unique)"
            )
        _seen_run_names.add(run_name)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest tests/test_emitters/test_go.py::test_subflow_name_collision_raises -x -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run ruff check clio/emitters/_go_flow_renderer.py --fix && git add clio/emitters/_go_flow_renderer.py tests/test_emitters/test_go.py && git commit -m "feat(go): guard against run<Name> collision across sub-flows"
```

---

### Task 7: end-to-end `go build` — sequential, parallel, collision, and A→B→C nesting

This is the **intent-verifying** task (Rule 9 / spec §Testing): a real `go build` of emitted sub-flow modules. A grep test cannot fail when `@take` typing or the boundary extension regresses — only the Go compiler catches a wrong type assertion. Covers four shapes the spec mandates: sequential sub-flow, single-GIVES parallel collector, sibling last-writer-wins collision, and A→B→C nesting.

**Files:**
- Create fixtures: `tests/fixtures/go_subflow_seq.clio`, `tests/fixtures/go_subflow_parallel.clio`, `tests/fixtures/go_subflow_collision.clio`, `tests/fixtures/go_subflow_abc.clio`
- Modify: `tests/test_emitters/test_go_compile.py` — add a parametrised build test using `_compile_flow`.

- [ ] **Step 1: Write the failing test**

Create `tests/fixtures/go_subflow_seq.clio`:

```
STEP fetch
  TAKES: url: str
  GIVES: article: str
  MODE:  exact
  LANG:  go

STEP shout
  TAKES: article: str
  GIVES: loud: str
  MODE:  exact
  LANG:  go

FLOW enrich
  TAKES: url:  str
  GIVES: article: str
  fetch(url=url)

FLOW pipeline
  TAKES: url:  str
  GIVES: loud: str
  enrich(url=url) -> shout(article=article)

RESOURCES
  target: go
  models: [haiku]
```

Create `tests/fixtures/go_subflow_parallel.clio`:

```
STEP fetch
  TAKES: url: str
  GIVES: summary: str
  MODE:  exact
  LANG:  go

FLOW enrich
  TAKES: url: str
  GIVES: summary: str
  fetch(url=url)

FLOW batch
  TAKES: urls: List<str>
  GIVES: results: List<str>
  FOR EACH u IN urls PARALLEL AS results:
    enrich(url=u)

RESOURCES
  target: go
  models: [haiku]
```

Create `tests/fixtures/go_subflow_collision.clio` (two sub-flows both GIVE `result` from different-typed steps; both called in one parent → last-writer-wins must compile):

```
STEP make_a
  TAKES: x: str
  GIVES: result: str
  MODE:  exact
  LANG:  go

STEP make_b
  TAKES: x: int
  GIVES: result: str
  MODE:  exact
  LANG:  go

FLOW flow_a
  TAKES: x: str
  GIVES: result: str
  make_a(x=x)

FLOW flow_b
  TAKES: x: int
  GIVES: result: str
  make_b(x=x)

FLOW pipeline
  TAKES: x: str
  GIVES: result: str
  flow_a(x=x) -> flow_b(x=7)

RESOURCES
  target: go
  models: [haiku]
```

> Both `make_a` and `make_b` GIVE a field named `result` (different producing steps). The parent calls `flow_a` then `flow_b`; the boundary extension's last-writer-wins must register `make_b` as the `result` producer, and the module must `go build`.

Create `tests/fixtures/go_subflow_abc.clio` (A calls B calls C, all sub-flows + an entry):

```
STEP s1
  TAKES: a: str
  GIVES: b: str
  MODE:  exact
  LANG:  go

STEP s2
  TAKES: b: str
  GIVES: c: str
  MODE:  exact
  LANG:  go

STEP s3
  TAKES: c: str
  GIVES: d: str
  MODE:  exact
  LANG:  go

FLOW level_c
  TAKES: c: str
  GIVES: d: str
  s3(c=c)

FLOW level_b
  TAKES: b: str
  GIVES: d: str
  s2(b=b) -> level_c(c=c)

FLOW level_a
  TAKES: a: str
  GIVES: d: str
  s1(a=a) -> level_b(b=b)

FLOW pipeline
  TAKES: a: str
  GIVES: d: str
  level_a(a=a)

RESOURCES
  target: go
  models: [haiku]
```

Add to `tests/test_emitters/test_go_compile.py`:

```python
import pytest

from tests.test_emitters.test_go import FIXTURES, _compile_flow


@pytest.mark.parametrize(
    "fixture,entry",
    [
        ("go_subflow_seq.clio", "pipeline"),
        ("go_subflow_parallel.clio", "batch"),
        ("go_subflow_collision.clio", "pipeline"),
        ("go_subflow_abc.clio", "pipeline"),
    ],
)
def test_go_build_passes_on_subflow_composition(
    tmp_path: Path, fixture: str, entry: str
) -> None:
    """Emit a sub-flow composition fixture, `go mod tidy`, then `go build ./...`.
    A real build is the only check that catches a wrong @take/boundary type
    assertion — a string grep cannot. Covers sequential, parallel single-GIVES
    collector, sibling collision (last-writer-wins), and A->B->C nesting."""
    out = tmp_path / "out"
    _compile_flow(FIXTURES / fixture, out, entry)
    tidy_env = {
        "GOFLAGS": "-mod=mod",
        "HOME": str(out / ".gohome"),
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin:/bin"),
    }
    subprocess.run(
        ["go", "mod", "tidy"], cwd=out, check=True, capture_output=True, env=tidy_env
    )
    result = _go_build(out)
    assert result.returncode == 0, (
        f"go build failed for {fixture}:\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest "tests/test_emitters/test_go_compile.py::test_go_build_passes_on_subflow_composition" -q
```

Expected: the four params build successfully **only if** Tasks 2-6 are correct. If a type assertion is wrong (e.g. boundary extension missed, or `@take` not threaded), `go build` returns non-zero and the assertion fails with the compiler error in the message. (If `go` is not on PATH the module-level `pytestmark` skips — note the skip explicitly per Rule 12; do not treat a skip as a pass.)

- [ ] **Step 3: Write minimal implementation**

No new implementation — this task validates Tasks 2-6 end-to-end. If `go build` fails, fix the offending renderer (most likely a missing boundary-extension registration or an un-threaded `take_types`/`flows_by_name` at a recursive call site) and re-run. Do not weaken the test to pass.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run pytest "tests/test_emitters/test_go_compile.py::test_go_build_passes_on_subflow_composition" -q -rs
```

Expected: `4 passed` (or `4 skipped` with reason "Go toolchain not on PATH" — surface the skip; it is NOT a green build). `-rs` prints skip reasons so a silent skip cannot masquerade as a pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && git add tests/fixtures/go_subflow_seq.clio tests/fixtures/go_subflow_parallel.clio tests/fixtures/go_subflow_collision.clio tests/fixtures/go_subflow_abc.clio tests/test_emitters/test_go_compile.py && git commit -m "test(go): go build of sub-flow composition (seq/parallel/collision/A->B->C)"
```

---

### Task 8: full-suite + lint + type gates for the phase

Close the phase against the MEMORY verify gates: ruff, mypy (the new `flows_by_name` / `take_types` threading widens signatures — watch `dict` value tightness), and the full pytest run.

**Files:** none (verification only)

- [ ] **Step 1: Write the failing test** — N/A (aggregate gate). The "failing" condition is any red in the commands below.

- [ ] **Step 2: Run the gates**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run ruff check . --fix && uv run mypy clio/ && uv run pytest tests/ -q -rs
```

- [ ] **Step 3: Fix any breakage**

Address ruff/mypy findings in `clio/emitters/_go_flow_renderer.py` only (this phase's surface). Likely mypy items: annotate `flows_by_name: dict[str, FlowIR] | None`, `take_types: dict[str, str] | None`, and the running-map mutation type; ensure `_render_subflow_parallel_body`'s return is `tuple[list[str], str]`.

- [ ] **Step 4: Re-run until green**

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && uv run ruff check . && uv run mypy clio/ && uv run pytest tests/ -q -rs
```

Expected: ruff clean, mypy clean (`Success: no issues found`), pytest all green (Go-build tests pass, or skip with surfaced reason if `go` absent).

- [ ] **Step 5: Commit** (only if Step 3 changed files)

```bash
cd /Users/jean-paulgavini/Documents/Dev/clio && git add -A && git commit -m "chore(go): satisfy ruff + mypy gates for sub-flow composition phase"
```

---

## Phase 6 — refusal edits + docs + final verify

This phase assumes Phases 1–5 are merged: `render_rest_step_go` / `render_shell_step_go` emit working bodies, `render_clio_runtime_rest()` / `render_clio_runtime_substitute()` write the runtime, `_collect_reachable_steps` replaces the stub loop in `go.py`, `_flow_uses_parallel` already scans every flow, the `FlowCallIR` arm in `_render_chain_item` is live, and `render_flow_go` emits `run<Name>` sub-flow funcs. What remains: stop `validate_graph_for_go` from refusing the now-supported shapes, re-narrow `E_GO_006` to the single genuinely-unsupported shape (multi-GIVES sub-flow read through a typed `FOR EACH PARALLEL` collector), refresh stale `v0.20.x` strings, then sync every doc surface and run the final verify gate.

---

### Task 1: Re-narrow `E_GO_006` — refuse ONLY a multi-GIVES sub-flow read through a typed `FOR EACH PARALLEL` collector

This is the keystone refusal change. The spec (decision Q2) keeps exactly one sub-flow shape unsupported: a sub-flow declaring **two or more** GIVES fields invoked as a `FOR EACH ... PARALLEL` body, because a single `[]T` collector slot cannot hold multiple typed fields (`builder.py:1501` already declines a typed collector when `len(sub_sig.gives) != 1`). Single-GIVES parallel and any sequential/nested multi-GIVES call are now fully supported. We write the negative test FIRST against a brand-new detector helper.

**Files:**
- Test: `tests/test_emitters/test_go.py` (append after the existing refusal tests, near line 911)
- Modify: `clio/emitters/_go_helpers.py:278-295` (the `_walk_chain` helper) and `:250-253` (`_GO_E_006_MSG`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_emitters/test_go.py`:

```python
def test_E_GO_006_multi_gives_subflow_in_parallel(tmp_path: Path) -> None:
    """A multi-GIVES sub-flow used as a FOR EACH PARALLEL body is the ONE
    sub-flow shape Go still refuses in v0.23: a single typed []T collector
    slot cannot hold two typed GIVES fields (builder.py:1501 declines the
    typed collector). The refusal MUST survive even though single-GIVES
    parallel and all sequential composition are now supported."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP enrich\n"
        "  TAKES: u: str\n"
        "  GIVES: a: str, b: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW sub\n"
        "  TAKES: u: str\n"
        "  GIVES: a: str, b: str\n"
        "  enrich(u=u)\n"
        "FLOW pipeline\n"
        "  TAKES: urls: List<str>\n"
        "  FOR EACH url IN urls PARALLEL AS results:\n"
        "    sub(u=url)\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_006")


def test_E_GO_006_single_gives_subflow_in_parallel_is_allowed(tmp_path: Path) -> None:
    """The companion positive guard: a SINGLE-GIVES sub-flow as a PARALLEL
    body must NOT raise E_GO_006 — it is fully supported in v0.23. This
    locks the re-narrowing: if the detector over-refuses, this test catches it."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP enrich\n"
        "  TAKES: u: str\n"
        "  GIVES: a: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW sub\n"
        "  TAKES: u: str\n"
        "  GIVES: a: str\n"
        "  enrich(u=u)\n"
        "FLOW pipeline\n"
        "  TAKES: urls: List<str>\n"
        "  FOR EACH url IN urls PARALLEL AS results:\n"
        "    sub(u=url)\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    # Must not raise E_GO_006 (or any ValueError carrying that code).
    _compile(src, out)
    assert (out / "flow" / "flow.go").exists()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_emitters/test_go.py::test_E_GO_006_multi_gives_subflow_in_parallel tests/test_emitters/test_go.py::test_E_GO_006_single_gives_subflow_in_parallel_is_allowed -v
```

Expected failure: `test_E_GO_006_single_gives_subflow_in_parallel_is_allowed` errors because `_walk_chain` still raises `_GO_E_006_MSG` on **every** `FlowCallIR` (current `_go_helpers.py:281-282`), so the single-GIVES positive case wrongly raises `E_GO_006` (`Failed: DID NOT match ... ` / `ValueError: E_GO_006...`). The multi-GIVES test may pass for the wrong reason (blanket refusal) — that is expected pre-fix; after the fix both must pass for the *right* reason.

- [ ] **Step 3: Write minimal implementation**

In `clio/emitters/_go_helpers.py`, first replace the `_GO_E_006_MSG` constant (currently `:250-253`):

```python
_GO_E_006_MSG = (
    "E_GO_006: target: go does not support a multi-GIVES sub-flow used as a "
    "FOR EACH ... PARALLEL body — a single typed slice collector cannot hold "
    "multiple GIVES fields. Use --target python, or give the sub-flow a single "
    "GIVES field (single-GIVES parallel and all sequential composition are "
    "supported)."
)
```

Then rewrite `_walk_chain` (currently `:278-295`) so it stops refusing every `FlowCallIR` and instead refuses ONLY the multi-GIVES parallel-collector shape. The walk needs the flow signatures to look up a called flow's GIVES count, and it needs to know when it is inside a `PARALLEL` ForEach body. Replace the whole function:

```python
def _walk_chain(
    items: tuple,  # type: ignore[type-arg]
    flow_sigs: dict[str, FlowIR],
    in_parallel_body: bool = False,
) -> None:
    """Recursively walk a FLOW chain and raise on the one unsupported shape.

    The only refusal left in v0.23 is a multi-GIVES sub-flow invoked as a
    FOR EACH ... PARALLEL body: a single typed []T collector slot cannot
    hold multiple typed GIVES fields. Every other FlowCallIR shape
    (sequential, nested IF/MATCH/WHILE, single-GIVES parallel) is supported.
    """
    for it in items:
        if isinstance(it, FlowCallIR):
            sig = flow_sigs.get(it.name)
            if in_parallel_body and sig is not None and len(sig.gives) > 1:
                raise ValueError(_GO_E_006_MSG)
        elif isinstance(it, IfBlockIR):
            _walk_chain(it.then_body, flow_sigs, in_parallel_body)
            _walk_chain(it.else_body, flow_sigs, in_parallel_body)
        elif isinstance(it, MatchBlockIR):
            for case in it.cases:
                _walk_chain(case.body, flow_sigs, in_parallel_body)
        elif isinstance(it, WhileBlockIR):
            _walk_chain(it.body, flow_sigs, in_parallel_body)
        elif isinstance(it, ForEachIR):
            _walk_chain(it.body, flow_sigs, in_parallel_body or it.parallel)
        elif isinstance(it, RescueBlockIR):
            _walk_chain(it.body, flow_sigs, in_parallel_body)
```

Add `FlowIR` to the `clio.ir.graph` import block at the top of the file (currently `:26-41`):

```python
from clio.ir.graph import (
    ApiInvokeIR,
    CliInvokeIR,
    FlowCallIR,
    FlowGraph,
    FlowIR,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    McpToolImplIR,
    RescueBlockIR,
    RestImplIR,
    ShellImplIR,
    SqlImplIR,
    StepIR,
    WhileBlockIR,
)
```

> CONFIRMED (plan self-review): `ForEachIR.parallel: bool` and `ForEachIR.collector: str | None` (`clio/ir/graph.py:294-295`). Use `it.parallel` in the `ForEachIR` branch — same accessor Phase 5's parallel renderer reads.

Finally, update the two call sites in `validate_graph_for_go` (currently `:342-345`) to build and pass `flow_sigs`, and to walk **every** flow (not just the entry) so a multi-GIVES parallel body inside a sub-flow is caught too:

```python
    # E_GO_006: multi-GIVES sub-flow read through a typed FOR EACH PARALLEL
    # collector is the one composition shape still unsupported. Walk every
    # flow's chain + rescues with the signature table so the detector can
    # check the called flow's GIVES count.
    flow_sigs = {f.name: f for f in graph.flows}
    for flow in graph.flows:
        _walk_chain(flow.chain, flow_sigs)
        for rescue in flow.rescues:
            _walk_chain(rescue.body, flow_sigs)
```

(This replaces the `if graph.flow is not None:` block at `:342-345`.)

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_emitters/test_go.py::test_E_GO_006_multi_gives_subflow_in_parallel tests/test_emitters/test_go.py::test_E_GO_006_single_gives_subflow_in_parallel_is_allowed -v
```

Expected: both PASS — the multi-GIVES parallel case raises `E_GO_006`, the single-GIVES parallel case compiles and emits `flow/flow.go`.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_go_helpers.py tests/test_emitters/test_go.py
git commit -m "fix(go): re-narrow E_GO_006 to multi-GIVES parallel-collector sub-flow

The blanket FlowCallIR refusal in _walk_chain is replaced by a signature-aware
check that refuses only a multi-GIVES sub-flow used as a FOR EACH ... PARALLEL
body (a single typed []T collector cannot hold multiple GIVES fields). All other
composition shapes are now supported. Walks every flow's chain + rescues."
```

---

### Task 2: Remove the `len(graph.flows) > 1` refusal (keep `== 0` → E_GO_004)

Multiple FLOWs no longer means "ambiguous entry" — `graph.flow` is the entry and `graph.flows` holds the callable sub-flows. The `> 1` guard at `_go_helpers.py:301-302` must go; the `== 0` guard (`:305-306`) stays and still raises `E_GO_004`.

**Files:**
- Test: `tests/test_emitters/test_go.py` (append)
- Modify: `clio/emitters/_go_helpers.py:300-306`

- [ ] **Step 1: Write the failing test**

```python
def test_multi_flow_source_no_longer_refused(tmp_path: Path) -> None:
    """v0.20 refused any source with >1 FLOW (E_GO_006 via ambiguous entry).
    v0.23 supports sub-flows, so a two-FLOW source (entry + signed sub-flow)
    must compile, not raise."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP enrich\n"
        "  TAKES: u: str\n"
        "  GIVES: summary: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW sub\n"
        "  TAKES: u: str\n"
        "  GIVES: summary: str\n"
        "  enrich(u=u)\n"
        "FLOW pipeline\n"
        "  sub(u=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    assert (out / "flow" / "flow.go").exists()


def test_E_GO_004_still_raised_on_zero_flows(tmp_path: Path) -> None:
    """Removing the >1 guard must NOT touch the ==0 guard: a source with no
    FLOW still cannot emit cmd/<flow>/main.go and must raise E_GO_004."""
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT just_a_contract\n"
        "  SHAPE: {x: str}\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_004")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_emitters/test_go.py::test_multi_flow_source_no_longer_refused tests/test_emitters/test_go.py::test_E_GO_004_still_raised_on_zero_flows -v
```

Expected: `test_multi_flow_source_no_longer_refused` FAILS — `_compile` raises `ValueError: E_GO_006...` from the `len(graph.flows) > 1` guard (`_go_helpers.py:301-302`). `test_E_GO_004_still_raised_on_zero_flows` already passes (control).

- [ ] **Step 3: Write minimal implementation**

In `clio/emitters/_go_helpers.py`, delete the `> 1` block (`:300-302`) from `validate_graph_for_go`. The function head should go from:

```python
    # E_GO_006: multiple FLOWs means FLOW composition (entry is ambiguous)
    if len(graph.flows) > 1:
        raise ValueError(_GO_E_006_MSG)

    # E_GO_004: no FLOW at all
    if len(graph.flows) == 0:
        raise ValueError(_GO_E_004_MSG)
```

to:

```python
    # E_GO_004: no FLOW at all
    if len(graph.flows) == 0:
        raise ValueError(_GO_E_004_MSG)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_emitters/test_go.py::test_multi_flow_source_no_longer_refused tests/test_emitters/test_go.py::test_E_GO_004_still_raised_on_zero_flows -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_go_helpers.py tests/test_emitters/test_go.py
git commit -m "feat(go): allow multi-FLOW sources (sub-flow composition)

Removes the len(graph.flows)>1 refusal in validate_graph_for_go now that the
Go target lowers signed sub-flows to run<Name>() funcs. The len==0 -> E_GO_004
guard is unchanged."
```

---

### Task 3: Drop the `RestImplIR` / `ShellImplIR` impl refusals (keep sql/mcp_tool) and delete the obsolete E_GO_006 step-call refusal test

`render_rest_step_go` / `render_shell_step_go` ship in Phases 1–2, so `validate_graph_for_go` must stop raising `_GO_E_007_MSG` / `_GO_E_008_MSG` on those impls. `sql` (`E_GO_009`) and `mcp_tool` (`E_GO_010`) stay refused (deferred to v0.24). We also delete the now-invalid `test_E_GO_006_flow_composition`, `test_E_GO_007_impl_mode_rest`, and `test_E_GO_008_impl_mode_shell` tests, whose subjects are now supported.

**Files:**
- Modify: `clio/emitters/_go_helpers.py:331-339` (the impl.mode checks in `validate_graph_for_go`)
- Modify/Delete tests: `tests/test_emitters/test_go.py` — remove `test_E_GO_006_flow_composition` (`:801-819`), `test_E_GO_007_impl_mode_rest` (`:822-839`), `test_E_GO_008_impl_mode_shell` (`:842-858`)

- [ ] **Step 1: Write the failing test**

Append positive tests that lock in the now-supported behaviour (these replace the deleted refusal tests):

```python
def test_rest_impl_no_longer_refused(tmp_path: Path) -> None:
    """impl.mode: rest is supported in v0.23: must emit, not raise E_GO_007."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP fetch\n"
        "  TAKES: id: str\n"
        "  GIVES: body: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: GET\n"
        "    url: \"http://x/${id}\"\n"
        "FLOW pipeline\n"
        "  fetch(id=\"1\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    assert (out / "flow" / "flow.go").exists()


def test_shell_impl_no_longer_refused(tmp_path: Path) -> None:
    """impl.mode: shell is supported in v0.23: must emit, not raise E_GO_008."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP grep\n"
        "  TAKES: file: str\n"
        "  GIVES: lines: List<str>\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode: shell\n"
        "    cmd:  \"grep foo ${file}\"\n"
        "    parse: json\n"
        "FLOW pipeline\n"
        "  grep(file=\"x\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    assert (out / "flow" / "flow.go").exists()


def test_E_GO_009_sql_still_refused(tmp_path: Path) -> None:
    """sql stays deferred to v0.24 — must still raise E_GO_009."""
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT OrderRow\n"
        "  SHAPE: {id: int}\n"
        "STEP q\n"
        "  TAKES: name: str\n"
        "  GIVES: rows: List<OrderRow>\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode: sql\n"
        "    db: crm\n"
        "    query: |\n"
        "      SELECT id FROM t WHERE name = :name\n"
        "FLOW pipeline\n"
        "  q(name=\"alice\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
        "  databases:\n"
        "    crm:\n"
        "      driver: sqlite\n"
        "      url: \":memory:\"\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_009")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_emitters/test_go.py::test_rest_impl_no_longer_refused tests/test_emitters/test_go.py::test_shell_impl_no_longer_refused tests/test_emitters/test_go.py::test_E_GO_009_sql_still_refused -v
```

Expected: `test_rest_impl_no_longer_refused` FAILS with `ValueError: E_GO_007...` (the `RestImplIR` guard at `:332-333`); `test_shell_impl_no_longer_refused` FAILS with `ValueError: E_GO_008...` (`:334-335`); `test_E_GO_009_sql_still_refused` passes (control).

- [ ] **Step 3: Write minimal implementation**

In `clio/emitters/_go_helpers.py`, delete the `RestImplIR` and `ShellImplIR` checks from the `impl.mode checks` block (`:331-339`). Change:

```python
        # impl.mode checks
        if isinstance(step.impl, RestImplIR):
            raise ValueError(_GO_E_007_MSG)
        if isinstance(step.impl, ShellImplIR):
            raise ValueError(_GO_E_008_MSG)
        if isinstance(step.impl, SqlImplIR):
            raise ValueError(_GO_E_009_MSG)
        if isinstance(step.impl, McpToolImplIR):
            raise ValueError(_GO_E_010_MSG)
```

to:

```python
        # impl.mode checks (rest/shell supported since v0.23; sql/mcp_tool deferred)
        if isinstance(step.impl, SqlImplIR):
            raise ValueError(_GO_E_009_MSG)
        if isinstance(step.impl, McpToolImplIR):
            raise ValueError(_GO_E_010_MSG)
```

Then delete the now-obsolete refusal tests from `tests/test_emitters/test_go.py`: `test_E_GO_006_flow_composition` (`:801-819`), `test_E_GO_007_impl_mode_rest` (`:822-839`), and `test_E_GO_008_impl_mode_shell` (`:842-858`). (Their subjects are supported in v0.23; Tasks 1–3 add positive replacements.)

> NOTE: `RestImplIR` and `ShellImplIR` remain imported at the top of `_go_helpers.py` because `_GO_E_007_MSG` / `_GO_E_008_MSG` are gone from `validate_graph_for_go` — but the Phase-5 chain renderer / Phase-1/2 step renderers import them from `_shared_utils`/`graph`, not from here. After this edit, ruff `F401` will flag `RestImplIR` and `ShellImplIR` as unused in `_go_helpers.py`. Remove them from the `clio.ir.graph` import block in the same edit (they were only referenced by the deleted guards). Run `uv run ruff check clio/emitters/_go_helpers.py` after editing to confirm.

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run ruff check clio/emitters/_go_helpers.py
uv run pytest tests/test_emitters/test_go.py::test_rest_impl_no_longer_refused tests/test_emitters/test_go.py::test_shell_impl_no_longer_refused tests/test_emitters/test_go.py::test_E_GO_009_sql_still_refused -v
```

Expected: ruff clean (no F401), all three tests PASS. Also confirm the deleted tests are gone:

```bash
uv run pytest tests/test_emitters/test_go.py -k "E_GO_006_flow_composition or E_GO_007 or E_GO_008" -v
```

Expected: `no tests ran` (collected 0 items / deselected).

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_go_helpers.py tests/test_emitters/test_go.py
git commit -m "feat(go): stop refusing impl.mode rest/shell (E_GO_007/008 lifted)

REST and shell step bodies are emitted natively in v0.23, so validate_graph_for_go
no longer raises E_GO_007/E_GO_008. sql (E_GO_009) and mcp_tool (E_GO_010) stay
deferred to v0.24. Drops the obsolete flow-composition/rest/shell refusal tests and
the now-unused RestImplIR/ShellImplIR imports."
```

---

### Task 4: Refresh stale `v0.20.0` / `v0.20.x` version strings in the remaining `_GO_E_*_MSG` constants

The surviving refusal messages (`E_GO_001`, `_002`, `_003`, `_005`, `_009`, `_010`, `_012`) still say "v0.20.0 does not yet…" / "until the v0.20.x … ships". After v0.23 these are wrong: rest/shell/composition landed in v0.23; sql/mcp_tool are tracked for v0.24; OpenAI/TEST/from-step remain on the backlog. Also `_GO_E_001_MSG` references E_GO_008 as "currently deferred to v0.20.x" — that pointer is now false (shell is supported), so its parenthetical must be rewritten.

**Files:**
- Test: `tests/test_emitters/test_go.py` (append)
- Modify: `clio/emitters/_go_helpers.py:228-273` (the `_GO_E_*_MSG` constants)

- [ ] **Step 1: Write the failing test**

```python
import re as _re_for_version_audit

from clio.emitters import _go_helpers as _goh


def test_go_error_messages_have_no_stale_v020_strings() -> None:
    """Every surviving E_GO_* message constant must not mention the retired
    'v0.20.0' / 'v0.20.x' milestone — those shapes either shipped (rest/shell/
    composition in v0.23) or moved to v0.24 (sql/mcp_tool). A stale string
    misleads users about which target to switch to."""
    stale = _re_for_version_audit.compile(r"v0\.20\.[0-9x]")
    offenders = {
        name: getattr(_goh, name)
        for name in dir(_goh)
        if name.startswith("_GO_E_") and name.endswith("_MSG")
        and stale.search(getattr(_goh, name))
    }
    assert offenders == {}, f"stale v0.20.x in: {sorted(offenders)}"


def test_go_error_messages_mention_correct_milestone() -> None:
    """sql/mcp_tool now point at v0.24; the E_GO_001 parenthetical no longer
    claims shell is deferred (it ships in v0.23)."""
    assert "v0.24" in _goh._GO_E_009_MSG
    assert "v0.24" in _goh._GO_E_010_MSG
    # E_GO_001 must not still tell users shell is "deferred to v0.20.x".
    assert "E_GO_008" not in _goh._GO_E_001_MSG or "supports" in _goh._GO_E_001_MSG
    assert "deferred to v0.20" not in _goh._GO_E_001_MSG
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_emitters/test_go.py::test_go_error_messages_have_no_stale_v020_strings tests/test_emitters/test_go.py::test_go_error_messages_mention_correct_milestone -v
```

Expected: both FAIL. The first reports offenders `['_GO_E_001_MSG', '_GO_E_002_MSG'?...]` — every constant still carrying `v0.20.x`. (`_002`/`_003` may not carry the string; the exact offender set is whatever still matches — at minimum `_001`, `_005`, `_006` (already fixed in Task 1), `_009`, `_010`, `_012`.) The second fails because `_GO_E_009_MSG` says `v0.20.x`, not `v0.24`.

- [ ] **Step 3: Write minimal implementation**

In `clio/emitters/_go_helpers.py`, rewrite the affected constants (`:228-273`). `_GO_E_004_MSG` and the already-fixed `_GO_E_006_MSG` (Task 1) need no change. Replace:

```python
_GO_E_001_MSG = (
    "E_GO_001: target: go can only embed exact step bodies in Go (LANG: go or "
    "LANG: auto). For Python/Bash/etc., use --target python (or --target "
    "claude-skill to let the LLM host drive the flow); for shell glue "
    "specifically, use impl.mode: shell which target: go supports natively."
)
_GO_E_002_MSG = (
    "E_GO_002: target: go does not subprocess 'claude -p'. Use --target python, "
    "--target mcp-server, or --target claude-cli."
)
_GO_E_003_MSG = (
    "E_GO_003: target: go ships Anthropic and OpenAI SDKs only. Use --target "
    "python for Bedrock/Vertex."
)
_GO_E_004_MSG = (
    "E_GO_004: target: go needs at least one FLOW to emit cmd/<flow>/main.go."
)
_GO_E_005_MSG = (
    "E_GO_005: target: go does not yet support invoke.protocol: openai. "
    "Use --target python until the Go OpenAI emitter ships."
)
_GO_E_007_MSG = (
    "E_GO_007: target: go REST emission shipped in v0.23 — this message should "
    "no longer be raised."
)
_GO_E_008_MSG = (
    "E_GO_008: target: go shell emission shipped in v0.23 — this message should "
    "no longer be raised."
)
_GO_E_009_MSG = (
    "E_GO_009: target: go does not yet support impl.mode: sql. Use "
    "--target python until the Go SQL emitter ships (tracked for v0.24)."
)
_GO_E_010_MSG = (
    "E_GO_010: target: go does not yet support impl.mode: mcp_tool. "
    "Use --target python until the Go MCP emitter ships (tracked for v0.24)."
)
_GO_E_012_MSG = (
    "E_GO_012: target: go does not yet emit TEST blocks as `go test`. "
    "Use --target python until the Go TEST emitter ships."
)
```

> NOTE: `_GO_E_007_MSG` / `_GO_E_008_MSG` are no longer raised (Task 3 removed their call sites), but the constants are kept as breadcrumbs and reworded so they no longer carry a stale `v0.20.x`. If ruff flags them as unused (`F401`-style is for imports, not module constants — module-level constants are not flagged by ruff), they stay. If a later cleanup wants them gone, that is out of scope here. Keeping `_001`'s shell pointer ("which target: go supports natively") aligns with the lifted E_GO_008.

The exact final wording of `_001` is a judgment call; the test only asserts (a) no `v0.20.x` substring and (b) it does not say "deferred to v0.20". The wording above satisfies both.

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_emitters/test_go.py::test_go_error_messages_have_no_stale_v020_strings tests/test_emitters/test_go.py::test_go_error_messages_mention_correct_milestone -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_go_helpers.py tests/test_emitters/test_go.py
git commit -m "docs(go): refresh stale v0.20.x milestone strings in E_GO_* messages

rest/shell/composition shipped in v0.23; sql/mcp_tool now point at v0.24. The
E_GO_001 parenthetical no longer claims shell is deferred. Adds a guard test that
fails if any surviving E_GO_* message regresses to a v0.20.x reference."
```

---

### Task 5: Flip the `docs/manual/04-targets.md` cross-target matrix rows for `go`

The authoritative cross-target matrix lives here. Six `go`-column cells lie post-v0.23: `impl.shell` (`:174`), `impl.shell + parse: json` (`:175`), `impl.rest` (`:176`), **FLOW composition** (`:189`), `FOR EACH PARALLEL body = sub-flow` (`:190`), and the LANG row's `impl.shell` family. Also the "When NOT to use go" bullets at `:160-161` claim rest/shell/composition are deferred. This is a doc-only edit; the test is a grep assertion on the rendered matrix file (justified because these are static doc claims, not codegen — a `go build` cannot verify a markdown table).

**Files:**
- Test: `tests/test_docs_go_matrix.py` (new file)
- Modify: `docs/manual/04-targets.md:160-161, :174-176, :189-190`

- [ ] **Step 1: Write the failing test**

Create `tests/test_docs_go_matrix.py`:

```python
"""Doc-consistency guard: the v0.23 go-target matrix rows must say the right
thing. A grep here is the correct tool — these are static markdown claims about
target support, not emitted code; a go build cannot verify a table cell. The
test encodes WHY each row flipped (rest/shell/composition shipped in v0.23)."""
from __future__ import annotations

from pathlib import Path

MATRIX = Path("docs/manual/04-targets.md").read_text()


def _row(prefix: str) -> str:
    for line in MATRIX.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"no matrix row starting {prefix!r}")


def test_flow_composition_row_supported_on_go() -> None:
    row = _row("| **FLOW composition**")
    # last column is the go cell; v0.23 supports it -> no E_GO_006 there.
    assert "E_GO_006" not in row
    assert "run<Name>" in row or "run_<name>" in row or "sub-flow func" in row


def test_parallel_subflow_row_supported_on_go() -> None:
    row = _row("| `FOR EACH PARALLEL` body = sub-flow")
    assert "E_GO_006" not in row


def test_impl_rest_row_supported_on_go() -> None:
    row = _row("| `MODE: exact` + `impl.rest`")
    assert "E_GO_007" not in row


def test_impl_shell_rows_supported_on_go() -> None:
    shell = _row("| `MODE: exact` + `impl.shell` |")
    shell_json = _row("| `MODE: exact` + `impl.shell` + `parse: json`")
    assert "E_GO_008" not in shell
    assert "E_GO_008" not in shell_json


def test_when_not_to_use_go_no_longer_lists_rest_shell_composition() -> None:
    # The "When NOT to use go" bullets must not deferral-flag rest/shell/composition.
    assert "FLOW composition (sub-flow calls) — deferred" not in MATRIX
    assert "rest / shell / sql / mcp_tool` (deferred — E_GO_007..010)" not in MATRIX
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_docs_go_matrix.py -v
```

Expected: all five FAIL — the `go` cells still carry `E_GO_006`/`E_GO_007`/`E_GO_008` and the deferral bullets are present.

- [ ] **Step 3: Write minimal implementation**

Edit `docs/manual/04-targets.md`. First the "When NOT to use go" bullets (`:160-161`) — change:

```
- You need `impl.mode: rest / shell / sql / mcp_tool` (deferred — E_GO_007..010).
- You need FLOW composition (sub-flow calls) — deferred to v0.20.x (E_GO_006).
```

to:

```
- You need `impl.mode: sql / mcp_tool` (deferred — E_GO_009/010, tracked for v0.24).
- You need a multi-GIVES sub-flow as a `FOR EACH PARALLEL` body (E_GO_006 — single-GIVES parallel and all sequential composition are supported).
```

Then the matrix cells. Row `:174` (`impl.shell`), change the go cell `❌ E_GO_008` → `✅ os/exec`. Row `:175` (`impl.shell + parse: json`), go cell `❌ E_GO_008` → `✅ json.Unmarshal`. Row `:176` (`impl.rest`), go cell `❌ E_GO_007` → `✅ net/http + retry`. Row `:189` (`**FLOW composition**`), go cell `❌ E_GO_006` → `✅ run<Name>() func`. Row `:190` (`FOR EACH PARALLEL body = sub-flow`), go cell `❌ E_GO_006` → `✅ single-GIVES (multi-GIVES → E_GO_006)`.

Concretely, the edited rows read:

```
| `MODE: exact` + `impl.shell` | ✅ | ✅ | ✅ | ✅ | ✅ (Python or Bash only) | ✅ os/exec |
| `MODE: exact` + `impl.shell` + `parse: json` | ⚠️ silently ignored | ✅ | ✅ | ✅ | ✅ | ✅ json.Unmarshal |
| `MODE: exact` + `impl.rest` | ✅ (uses `requests` at runtime) | ✅ | ✅ | ✅ | ✅ | ✅ net/http + retry |
```

```
| **FLOW composition** (sub-flow callable, v0.17) | ❌ rejected | ✅ `run_<name>()` | ✅ + multi-tool | ✅ sub-`StateGraph` | ⚠️ documented in SKILL.md (linear-only, `scripts/sub_<name>.py`) | ✅ `run<Name>()` func |
| `FOR EACH PARALLEL` body = sub-flow (v0.17) | ❌ rejected | ✅ | ✅ asyncio.gather | ❌ rejected (v0; v0.7 via Send) | ⚠️ linear sub-flow only | ✅ single-GIVES (multi-GIVES → E_GO_006) |
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_docs_go_matrix.py -v
```

Expected: all five PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/manual/04-targets.md tests/test_docs_go_matrix.py
git commit -m "docs(go): flip 04-targets matrix rows for v0.23 (rest/shell/composition)

FLOW composition, FOR EACH PARALLEL body = sub-flow, impl.rest and both impl.shell
rows now show ✅ for go. The 'When NOT to use go' bullets drop the lifted deferrals.
Adds a doc-consistency guard test."
```

---

### Task 6: Update `docs/COMPILATION_TARGETS.md` — refused-combo table + inherited-features list

The Go target section's "Refused combinations" list (`:374-388`) still defers rest/shell/composition. Flip them: remove the E_GO_006/007/008 deferral lines, add the narrowed E_GO_006 (multi-GIVES parallel) line, and add a new "Composition + REST + shell" entry to the inherited-features list (`:390-399`). Update the `(v0.20.0 scope)` heading.

**Files:**
- Test: `tests/test_docs_go_matrix.py` (extend)
- Modify: `docs/COMPILATION_TARGETS.md:374-399`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_docs_go_matrix.py`:

```python
COMPTARGETS = Path("docs/COMPILATION_TARGETS.md").read_text()


def test_comptargets_no_longer_defers_rest_shell_composition() -> None:
    """The refused-combo list must not blanket-defer rest/shell/full composition."""
    assert "`impl.mode: rest` — deferred to v0.20.x (E_GO_007)." not in COMPTARGETS
    assert "`impl.mode: shell` — deferred to v0.20.x (E_GO_008)." not in COMPTARGETS
    assert "**FLOW composition** (sub-flow calls) — deferred to v0.20.x (E_GO_006)." not in COMPTARGETS


def test_comptargets_narrowed_e_go_006_present() -> None:
    """The one remaining composition refusal is documented."""
    assert "multi-GIVES sub-flow" in COMPTARGETS
    assert "E_GO_006" in COMPTARGETS


def test_comptargets_lists_composition_rest_shell_as_inherited() -> None:
    """rest/shell/composition appear in the supported-features prose."""
    assert "net/http" in COMPTARGETS
    assert "os/exec" in COMPTARGETS
    assert "run<Name>" in COMPTARGETS or "sub-flow" in COMPTARGETS.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_docs_go_matrix.py -k comptargets -v
```

Expected: `test_comptargets_no_longer_defers_rest_shell_composition` and `test_comptargets_narrowed_e_go_006_present` FAIL (the deferral lines are present, the narrowed line is not); `test_comptargets_lists_composition_rest_shell_as_inherited` FAILS (no net/http / os/exec prose).

- [ ] **Step 3: Write minimal implementation**

In `docs/COMPILATION_TARGETS.md`, change the `### Refused combinations (v0.20.0 scope)` heading (`:374`) to `### Refused combinations (v0.23 scope)`. Replace the three lines:

```
- **FLOW composition** (sub-flow calls) — deferred to v0.20.x (E_GO_006).
- `impl.mode: rest` — deferred to v0.20.x (E_GO_007).
- `impl.mode: shell` — deferred to v0.20.x (E_GO_008).
```

with a single narrowed line:

```
- A **multi-GIVES sub-flow used as a `FOR EACH PARALLEL` body** — a single typed slice collector cannot hold multiple GIVES fields (E_GO_006). Single-GIVES parallel and all sequential composition are supported.
```

Update the remaining deferral lines' milestone: `impl.mode: sql` and `impl.mode: mcp_tool` change `deferred to v0.20.x` → `deferred to v0.24`. In the `### Inherited features` block change the `These work identically to the v0.20.0 Go target…` lead-in to `v0.23 Go target` and append three bullets:

```
- `impl.mode: rest` — `net/http` client with `${var}` substitution, `response_path` traversal, impl-level retry (constant/exponential backoff, `Retry-After`), parity with `clio/runtime/rest.py`.
- `impl.mode: shell` — `os/exec` with per-token `${var}` substitution, context timeout, `parse: none` (stdout str) / `parse: json` (unmarshal).
- **FLOW composition** — each signed sub-flow lowers to an unexported `run<Name>(ctx, …) (map[string]any, error)` func; the call site flat-merges the sub-flow's GIVES into parent state (parity with the `python` target's `run_<name>()`).
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_docs_go_matrix.py -k comptargets -v
```

Expected: all three PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/COMPILATION_TARGETS.md tests/test_docs_go_matrix.py
git commit -m "docs(go): update COMPILATION_TARGETS refused-combo + inherited lists for v0.23

Refused-combo list drops the rest/shell/composition deferrals and keeps the one
narrowed E_GO_006 (multi-GIVES parallel). sql/mcp_tool now defer to v0.24. Adds
rest/shell/composition to the inherited-features prose."
```

---

### Task 7: Update `docs/LANGUAGE_SPEC.md` target-support tables + sub-flow target table

Two tables: the v0.2 snapshot (`:51-63`) is explicitly *not authoritative* (the file header at `:9` says so) and only carries `impl.mode rest/shell` rows whose `go target` column is **blank** (the table truncates at `mcp-server`), so it needs **no go edit** — confirm by reading. The authoritative change is the sub-flow target table (`:656-664`), which lists `claude-cli` as the only "no" and omits `go` entirely. Add a `go` row. Also the multi-GIVES limitation note (`:649-654`) should mention the Go-target refusal.

**Files:**
- Test: `tests/test_docs_go_matrix.py` (extend)
- Modify: `docs/LANGUAGE_SPEC.md:649-664`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_docs_go_matrix.py`:

```python
LANGSPEC = Path("docs/LANGUAGE_SPEC.md").read_text()


def test_langspec_subflow_table_lists_go() -> None:
    """The sub-flow target-support table must carry a go row showing support."""
    rows = [ln for ln in LANGSPEC.splitlines() if ln.strip().startswith("| `go`")]
    assert rows, "no `go` row in the sub-flow target-support table"
    go_row = rows[0]
    assert "yes" in go_row.lower()
    assert "run<Name>" in go_row or "run_<name>" in go_row or "sub-flow" in go_row.lower()


def test_langspec_multi_gives_note_mentions_go_refusal() -> None:
    """The multi-GIVES limitation prose must note the go-target parallel refusal."""
    assert "E_GO_006" in LANGSPEC
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_docs_go_matrix.py -k langspec -v
```

Expected: both FAIL — there is no `| \`go\`` row in the sub-flow table and no `E_GO_006` anywhere in `LANGUAGE_SPEC.md`.

- [ ] **Step 3: Write minimal implementation**

First read `docs/LANGUAGE_SPEC.md:51-63` to confirm the v0.2 snapshot `go target` column is truncated/blank on the rest/shell rows (no edit needed there — it is a frozen v0.2 snapshot per the `:9` disclaimer). Then in the sub-flow target-support table (`:658-664`) add a `go` row after the `langgraph` row:

```
| `go`            | yes (sub-flow → `run<Name>()` func; single-GIVES parallel bodies; multi-GIVES PARALLEL refused — E_GO_006) |
```

And extend the multi-GIVES limitation note (`:649-654`) — append to the final sentence:

```
... track this as a limitation pending a follow-up release. On `target: go`
this exact shape (a multi-GIVES sub-flow read through a typed `FOR EACH
PARALLEL` collector) is refused at compile time with `E_GO_006`; single-GIVES
parallel and all sequential composition compile cleanly.
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_docs_go_matrix.py -k langspec -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/LANGUAGE_SPEC.md tests/test_docs_go_matrix.py
git commit -m "docs(go): add go row to LANGUAGE_SPEC sub-flow target table (v0.23)

The sub-flow composition target-support table gains a go row (run<Name>() funcs,
single-GIVES parallel, multi-GIVES PARALLEL refused via E_GO_006). The multi-GIVES
limitation note now points at the go-target refusal."
```

---

### Task 8: Update the cookbook + troubleshooting in `docs/manual/`

Per MEMORY ("Update manual on features"), every user-visible change updates the manual. Two files: (1) cookbook recipe #24 (`03-cookbook.md:1011-1024`) scope note and the sub-flow recipe target-limitation bullets (`:825`), (2) the `target: go` troubleshooting section (`06-troubleshooting.md:444, :470-498`) — flip E_GO_006/007/008 entries.

**Files:**
- Test: `tests/test_docs_go_matrix.py` (extend)
- Modify: `docs/manual/03-cookbook.md:1011-1015`, `docs/manual/06-troubleshooting.md:444, :470-498`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_docs_go_matrix.py`:

```python
COOKBOOK = Path("docs/manual/03-cookbook.md").read_text()
TROUBLE = Path("docs/manual/06-troubleshooting.md").read_text()


def test_cookbook_go_scope_note_updated() -> None:
    """Recipe #24 must not still list rest/shell/composition as v0.20.x deferrals."""
    assert "OpenAI, FLOW composition, `impl.mode {rest, sql, mcp_tool,\nshell}`" not in COOKBOOK
    # The deferral set is now just sql/mcp_tool/OpenAI/RESUME/TEST.
    assert "v0.20.x" not in COOKBOOK or "v0.23" in COOKBOOK


def test_troubleshooting_e_go_006_narrowed() -> None:
    """The E_GO_006 troubleshooting entry describes the multi-GIVES parallel case."""
    assert "does not support FLOW composition (sub-flow calls) in v0.20.0" not in TROUBLE
    assert "multi-GIVES" in TROUBLE


def test_troubleshooting_e_go_007_008_removed_or_marked_lifted() -> None:
    """REST/shell are supported; their old 'deferred' troubleshooting entries
    must not claim v0.20.0 non-support."""
    assert "does not support impl.mode: rest in v0.20.0" not in TROUBLE
    assert "does not support impl.mode: shell in v0.20.0" not in TROUBLE
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_docs_go_matrix.py -k "cookbook or troubleshooting" -v
```

Expected: all three FAIL — the cookbook scope note still lists the lifted deferrals, and the troubleshooting E_GO_006/007/008 entries still say "v0.20.0".

- [ ] **Step 3: Write minimal implementation**

In `docs/manual/03-cookbook.md`, change the recipe-#24 scope note (`:1011-1015`):

```
**v0.20.0 scope**: this target covers the most common case. See
`docs/manual/06-troubleshooting.md` for the list of features deferred
to v0.20.x (OpenAI, FLOW composition, `impl.mode {rest, sql, mcp_tool,
shell}`, RESUME, TEST blocks) — each fails at compile time with a
remediation pointer.
```

to:

```
**Scope (v0.23)**: REST + shell impl bodies and FLOW composition (sub-flow
calls) are supported. Still deferred — each fails at compile time with a
remediation pointer: OpenAI judgment (E_GO_005), `impl.mode {sql, mcp_tool}`
(E_GO_009/010, tracked for v0.24), `--from-step` RESUME (E_GO_011), TEST
blocks (E_GO_012), and a multi-GIVES sub-flow used as a `FOR EACH PARALLEL`
body (E_GO_006).
```

In `docs/manual/06-troubleshooting.md`, change the section heading (`:444`) `## \`target: go\` errors (v0.20.0+)` → `## \`target: go\` errors (v0.23+)`. Replace the E_GO_006 entry (`:470-474`):

```
### E_GO_006 — `ValueError: target=go does not support FLOW composition (sub-flow calls) in v0.20.0`

**Cause**: the source contains a `FlowCallIR` site (a signed FLOW called as a step inside another FLOW).

**Fix**: compile to `--target python` or `--target mcp-server` for full sub-flow support. FLOW composition for the Go target is tracked for v0.20.x. As a workaround, inline the sub-flow's steps directly in the parent FLOW.
```

with:

```
### E_GO_006 — `ValueError: target: go does not support a multi-GIVES sub-flow used as a FOR EACH ... PARALLEL body`

**Cause**: a sub-flow declaring two or more `GIVES` fields is invoked as a `FOR EACH ... PARALLEL` body. The Go target collects parallel results into a single typed `[]T` slice, which cannot hold multiple typed GIVES fields. (Single-GIVES parallel bodies and all sequential / nested composition are supported in v0.23.)

**Fix**: give the sub-flow a single `GIVES` field, run it sequentially, or compile to `--target python` for the multi-GIVES parallel shape.
```

Delete the E_GO_007 entry (`:476-480`) and E_GO_008 entry (`:482-486`) entirely — REST and shell are supported, so there is no error to document. (Leave E_GO_009/E_GO_010/E_GO_011/E_GO_012 entries, but change their `v0.20.x` tracking pointers: E_GO_009/010 → "tracked for v0.24".)

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_docs_go_matrix.py -k "cookbook or troubleshooting" -v
```

Expected: all three PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/manual/03-cookbook.md docs/manual/06-troubleshooting.md tests/test_docs_go_matrix.py
git commit -m "docs(go): update cookbook scope + troubleshooting for v0.23

Recipe #24 scope note drops the lifted rest/shell/composition deferrals.
Troubleshooting: E_GO_006 narrowed to the multi-GIVES parallel case, E_GO_007/008
entries removed (supported), sql/mcp_tool pointers moved to v0.24."
```

---

### Task 9: Add the `[0.23.0]` CHANGELOG entry

CHANGELOG discipline (MEMORY) requires a top entry. This is feature-PR scope (the release-admin version bump is a separate PR per the "Release PR separate" rule, NOT in this phase). The entry summarizes the whole v0.23 arc (Phases 1–6), not just this phase.

**Files:**
- Test: `tests/test_docs_go_matrix.py` (extend)
- Modify: `CHANGELOG.md:1-3` (insert above the `## [0.22.0]` entry)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_docs_go_matrix.py`:

```python
CHANGELOG = Path("CHANGELOG.md").read_text()


def test_changelog_has_0_23_0_entry() -> None:
    assert "## [0.23.0]" in CHANGELOG
    # 0.23.0 must sit above 0.22.0 (newest-first).
    assert CHANGELOG.index("## [0.23.0]") < CHANGELOG.index("## [0.22.0]")


def test_changelog_0_23_0_mentions_go_rest_shell_subflow() -> None:
    head = CHANGELOG.split("## [0.22.0]")[0]
    assert "go" in head.lower()
    assert "rest" in head.lower()
    assert "shell" in head.lower()
    assert "sub-flow" in head.lower() or "composition" in head.lower()
    assert "E_GO_006" in head
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_docs_go_matrix.py -k changelog -v
```

Expected: both FAIL — no `## [0.23.0]` entry yet.

- [ ] **Step 3: Write minimal implementation**

Insert at the top of `CHANGELOG.md`, immediately after line 1 (`# Changelog`) and before `## [0.22.0]`:

```markdown
## [0.23.0] — 2026-05-30

Minor release closing **#82 — Go target reaches stdlib feature parity**. The `target: go` emitter now lowers three IR families it previously refused: `impl.mode: rest` (`net/http` client with `${var}` substitution, `response_path` traversal, and impl-level retry — constant/exponential backoff with cap and `Retry-After`, byte-for-behaviour parity with `clio/runtime/rest.py`), `impl.mode: shell` (`os/exec` with per-token `${var}` substitution, context timeout, `parse: none`/`parse: json`), and **FLOW composition** — each signed sub-flow lowers to an unexported `run<Name>(ctx, …) (map[string]any, error)` func and the call site flat-merges the sub-flow's GIVES into parent state (parity with the `python` target's `run_<name>()`). Lifting composition also closes the last `FlowCallIR` gap across all six targets. Zero new Go dependencies — everything is stdlib (`net/http`, `os/exec`, `encoding/json`) plus the already-present `golang.org/x/sync/errgroup`.

### Added

- **`impl.mode: rest` on `target: go`.** New `clio_runtime/rest` (Go mirror of `clio/runtime/rest.py`) — `Subst` / `RenderDict` / `IsRetryableStatus` / `IsRetryableErr` / `ComputeDelay` / `ParseRetryAfter` — plus `clio_runtime/substitute` for `${var}` tokens, shared by REST and shell. `render_rest_step_go` emits the typed `<Cls>In`/`<Cls>Out` skeleton; GIVES present → `json.Unmarshal` + `.Validate(ctx)`, GIVES absent → pure side-effect.
- **`impl.mode: shell` on `target: go`.** `render_shell_step_go` emits `exec.CommandContext` with per-token substitution and a context timeout; `parse: none` → a single stdout `str` field, `parse: json` → unmarshal into `<Cls>Out`.
- **FLOW composition on `target: go`.** Each callable sub-flow (signed `TAKES` + `GIVES`, excluding the entry) becomes a name-sorted unexported `run<Name>()` func appended after `Run`; the entry flow is retrofitted to seed `state["<take>"] = kwargs["<take>"]` and register entry TAKES, so entry and sub-flows read TAKES identically. Three compile-time maps (`_build_state_field_to_step`, `_build_take_field_to_gotype`, plus boundary extension) keep the typed-state model sound; `_render_chain_item` gains a `FlowCallIR` arm (sequential flat-merge + single-GIVES parallel `_results := make([]steps.<Cls>Out, …)`).

### Fixed

- **Step-stub collector walked only the top-level entry chain** (`go.py:74-96`). Replaced with `_collect_reachable_steps`, a recursive collector over every flow (chain + nested IF/MATCH/WHILE/FOR EACH bodies + rescues), dedup by name with stable first-seen `NN_` numbering. Pre-existing latent bug: steps nested in an entry-flow control block already got no stub file.
- **`_flow_uses_parallel` scanned only `graph.flow.chain`** (`_go_helpers.py:70-74`), so a `FOR EACH PARALLEL` inside a sub-flow would miss the `golang.org/x/sync/errgroup` dependency. Now scans every flow's chain.

### Changed

- **`E_GO_006` re-narrowed.** It no longer refuses all FLOW composition — only a multi-GIVES sub-flow used as a `FOR EACH PARALLEL` body (a single typed slice collector cannot hold multiple GIVES fields). `E_GO_007` (rest) and `E_GO_008` (shell) are no longer raised. `validate_graph_for_go` drops the `len(graph.flows) > 1` refusal (kept `== 0` → `E_GO_004`). Stale `v0.20.x` milestone strings in the surviving `E_GO_*` messages refreshed; `sql`/`mcp_tool` (E_GO_009/010) now point at v0.24.

### Docs

- `docs/manual/04-targets.md` matrix: `impl.rest`, both `impl.shell` rows, **FLOW composition**, and `FOR EACH PARALLEL body = sub-flow` flipped to ✅ for go. `docs/COMPILATION_TARGETS.md`, `docs/LANGUAGE_SPEC.md` (sub-flow target table + multi-GIVES note), and `docs/manual/{03-cookbook,06-troubleshooting}.md` updated.

### Tests

- A real `go build` of an emitted sub-flow module is added (a grep-only golden cannot fail when `@take` typing regresses). Refusal-test suite updated: obsolete flow-composition/rest/shell refusal tests removed, replaced by positive emission tests + a narrowed multi-GIVES-parallel `E_GO_006` negative.
```

> NOTE for the assembler: the test-count line (e.g. "suite at N passed") is intentionally omitted here — the release-admin PR fills the exact count after the full suite runs. If your convention requires it in the feature PR, add it after Task 10's pytest output is known.

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_docs_go_matrix.py -k changelog -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md tests/test_docs_go_matrix.py
git commit -m "docs(changelog): add [0.23.0] entry — Go rest/shell/sub-flow parity

Summarizes the v0.23 arc: impl.mode rest + shell, FLOW composition (run<Name>()
funcs), the two pre-existing fixes, and the E_GO_006 re-narrowing. Adds a guard
test asserting the entry exists newest-first and names the headline features."
```

---

### Task 10: Final verify gate — ruff + mypy + full pytest + a real `go build` of an emitted sub-flow module

The closing gate per MEMORY ("Run ruff before push", "Run mypy before push") and Rule 9 (a real `go build`, not a grep, catches the `@take` type-assertion regression class). This task adds the build test if Phase 5 did not already commit one for the *sequential sub-flow* shape, then runs the four-command gate. If Phase 5 already shipped a sub-flow `go build` test, this task verifies it is present and green and skips re-adding.

**Files:**
- Test: `tests/test_emitters/test_go_compile.py` (append, only if absent)
- No production code changes — verification only

- [ ] **Step 1: Write the failing test**

First check whether a sub-flow `go build` test already exists (Phase 5 may own it):

```bash
grep -n "runEnrich\|sub-flow\|flow_composition\|subflow" tests/test_emitters/test_go_compile.py
```

If none matches, append to `tests/test_emitters/test_go_compile.py` (it already has the `_go_build` helper and the `go`-on-PATH skip marker):

```python
def test_go_build_passes_on_subflow_composition(tmp_path: Path) -> None:
    """A real `go build` of an emitted sub-flow module. This is the gate a
    grep-only golden cannot provide: if @take typing or the flat-merge type
    assertion regresses (e.g. state["url"].(string) becomes state["url"].(any)),
    the emitted Go fails to compile and THIS test fails. Uses the committed
    examples/flow_composition.clio entry (sub-flow → run<Name>() func)."""
    out = tmp_path / "out"
    _compile(Path("examples/flow_composition.clio"), out)

    tidy_env = {
        "GOFLAGS": "-mod=mod",
        "HOME": str(out / ".gohome"),
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin:/bin"),
    }
    subprocess.run(
        ["go", "mod", "tidy"], cwd=out, check=True, capture_output=True, env=tidy_env
    )
    result = _go_build(out)
    assert result.returncode == 0, (
        f"go build failed:\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    # The sub-flow lowers to a run<Name>() func — assert the emitted shape too,
    # so a passing build with a vanished sub-flow func still fails.
    flow_go = (out / "flow" / "flow.go").read_text()
    assert "func run" in flow_go, "no run<Name>() sub-flow func emitted"
```

> NOTE for the assembler: confirm `examples/flow_composition.clio` declares `RESOURCES.target: go` (or that `_compile` forces the go target). The fixture exists in the repo; the spec's Phase-5 testing notes reference it as the canonical sub-flow case. If its `RESOURCES.target` is `python`, either Phase 5 added a `examples/flow_composition_go.clio` variant (use that path) or override the target in `_compile`. Resolve against whatever Phase 5 committed — do not duplicate a fixture if one already exists.

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest "tests/test_emitters/test_go_compile.py::test_go_build_passes_on_subflow_composition" -v
```

Expected (if Phases 1–5 are correctly merged): PASS immediately — the emitted module builds. This test is a *regression sentinel*, not a red-then-green TDD step: it must already pass because the implementation shipped in Phases 1–5. If it FAILS, that is a real signal Phase 5's type threading is incomplete — STOP and report the `go build` stderr rather than papering over it (Rule 12: fail loud). If the `go` toolchain is absent the test is skipped (existing `pytestmark`).

- [ ] **Step 3: Write minimal implementation**

No production code. If Step 2 surfaced a real build failure, the fix belongs in the Phase-5 renderer (`_go_flow_renderer.py` / `_go_step_renderers.py`), not here — route it back. This phase only *gates*.

- [ ] **Step 4: Run test to verify it passes**

Run the full four-command gate in order (per the spec's "Verify gates"):

```bash
uv run ruff check . --fix
uv run mypy
uv run pytest
uv run pytest "tests/test_emitters/test_go_compile.py" -v
```

Expected:
- `ruff check .` → `All checks passed!` (the new map threading in `_go_flow_renderer.py` from Phase 5 must be import-clean; this phase removed the `RestImplIR`/`ShellImplIR` imports in `_go_helpers.py` — confirm no F401).
- `uv run mypy` → `Success: no issues found` (watch the widened `dict[str, str]` signatures Phase 5 added; if mypy complains about a `dict` value-type in the new helpers, that is a Phase-5 fix routed back).
- `uv run pytest` → all green, no skips other than the documented `go`-toolchain skips and the pre-existing xfail (suite was `1206 passed / 19 skipped / 1 xfailed` at v0.22; v0.23 adds the Phase 1–6 tests).
- The final `test_go_compile.py` run → all `go build` tests PASS (or SKIP if no toolchain).

- [ ] **Step 5: Commit**

```bash
git add tests/test_emitters/test_go_compile.py
git commit -m "test(go): real go build of an emitted sub-flow module (verify gate)

Compiles examples/flow_composition.clio to go and runs `go mod tidy` + `go build
./...` so a @take type-assertion regression (state[\"url\"].(string) -> .(any))
fails CI — a grep-only golden cannot catch this. Skips when the go toolchain is
absent."
```

> Final phase checkpoint (Rule 10): after Task 10, the branch has lifted E_GO_006/007/008 to exactly one narrowed refusal, every doc surface (04-targets matrix, COMPILATION_TARGETS, LANGUAGE_SPEC, cookbook, troubleshooting, CHANGELOG) is consistent, and the four-command gate is green including a real sub-flow `go build`. The release-admin version bump (`pyproject.toml` + `clio/__init__.py` together, per MEMORY "Dual version source") and the annotated tag are a SEPARATE release-admin PR — NOT part of this feature phase.

---

## Final verify gate (per MEMORY release discipline)

Before opening the PR, from the repo root on `feat/go-v023-rest-shell-subflow`:

```bash
uv run ruff check . --fix
uv run mypy            # the take_types / flows_by_name threading widens signatures — watch dict value-type tightness
uv run pytest tests/ -q
# real go build of one emitted sub-flow module:
python -m clio compile tests/fixtures/go_subflow_seq.clio --target go --output /tmp/go-subflow-out
cd /tmp/go-subflow-out && go mod tidy && go build ./...
```

All four must be green. Then update `docs/manual/` (matrix rows flipped), `CHANGELOG.md [0.23.0]`, and `docs/LANGUAGE_SPEC.md` target-support tables (Phase 6), and open the PR (feature) — release-admin PR + manual tag are a separate follow-up per the two-PR release discipline.
