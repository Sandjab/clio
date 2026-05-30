"""Tests for the go emitter.

Granular tests (existence + parsed content) for tasks 1-19.
Golden snapshots (full-tree equality) for tasks 9, 17, 21.

To regenerate goldens after intentional changes:

    python -m clio compile tests/fixtures/<name>.clio \\
        --target go --output tests/fixtures/expected_go/<name>
"""
from __future__ import annotations

from pathlib import Path

from clio.cli import _cmd_compile


def _compile(source_path: Path, output_dir: Path) -> None:
    """Run `clio compile <source> --target go --output <out>` in-process."""
    _cmd_compile(str(source_path), "go", str(output_dir), None)


def test_target_go_is_registered_in_cli(tmp_path: Path) -> None:
    src = tmp_path / "trivial.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    assert (out / "go.mod").exists(), "go emitter must write go.mod"


def test_go_mod_uses_safe_package_name(tmp_path: Path) -> None:
    """Module name is lowercased and normalised for Go (no uppercase, no
    special chars).  CamelCase flow name 'CustomerRetention' becomes
    'customerretention'."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW CustomerRetention\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    content = (out / "go.mod").read_text()
    assert content.startswith("module customerretention\n"), content
    assert "go 1.22\n" in content


def test_go_mod_omits_sdk_when_no_judgment(tmp_path: Path) -> None:
    """A flow with no judgment step does not require anthropic-sdk-go."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    content = (out / "go.mod").read_text()
    assert "anthropic-sdk-go" not in content
    # jsonschema is always required (Validate methods)
    assert "santhosh-tekuri/jsonschema/v6" in content


def test_go_mod_pins_sdk_when_judgment_present(tmp_path: Path) -> None:
    """A flow with a judgment step pulls in anthropic-sdk-go."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    content = (out / "go.mod").read_text()
    assert "github.com/anthropics/anthropic-sdk-go" in content


def test_cmd_main_go_exists(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    assert (out / "cmd" / "pipeline" / "main.go").exists()


def test_cmd_main_go_parses_kwargs_json(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "cmd" / "pipeline" / "main.go").read_text()
    assert "package main" in body
    assert 'flag.String("kwargs"' in body
    assert "json.Unmarshal" in body
    assert "flow.Run(ctx, kwargs)" in body


_CONTRACT_SRC = (
    "CONTRACT customer_risk\n"
    "  SHAPE: {client: str, risk: enum(low|mid|high), reason: str(max=300)}\n"
    "STEP detect\n"
    "  TAKES: x: str\n"
    "  GIVES: risk: customer_risk\n"
    "  MODE:  judgment\n"
    "FLOW pipeline\n"
    "  detect(x=\"hi\")\n"
    "RESOURCES\n"
    "  target: go\n"
    "  models: [haiku]\n"
)


def test_contracts_file_written_when_contracts_present(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(_CONTRACT_SRC)
    out = tmp_path / "out"
    _compile(src, out)
    assert (out / "contracts" / "contracts.go").exists()


def test_contracts_struct_uses_json_tags(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(_CONTRACT_SRC)
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "contracts" / "contracts.go").read_text()
    assert "package contracts" in body
    assert "type CustomerRisk struct {" in body
    assert 'Client string `json:"client"`' in body
    assert 'Risk string `json:"risk"`' in body
    assert 'Reason string `json:"reason"`' in body


def test_contracts_json_schema_embedded_as_const(tmp_path: Path) -> None:
    """Each contract carries its JSON Schema as a `const` string so
    Validate() can call jsonschema/v6 without filesystem reads."""
    src = tmp_path / "src.clio"
    src.write_text(_CONTRACT_SRC)
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "contracts" / "contracts.go").read_text()
    assert "const customerRiskSchema = `" in body
    assert '"client"' in body  # field in schema
    assert "low" in body  # enum value


def test_contracts_file_omitted_when_no_contract_used(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    assert not (out / "contracts" / "contracts.go").exists()


def test_contracts_have_validate_method(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT customer_risk\n"
        "  SHAPE: {client: str, risk: enum(low|mid|high), reason: str(max=300)}\n"
        "  ASSERT: len(reason) > 0\n"
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: risk: customer_risk\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "contracts" / "contracts.go").read_text()
    assert "func (c *CustomerRisk) Validate(ctx context.Context) error {" in body
    # Calls clio_runtime/validate
    assert '"clio_runtime/validate"' in body or "validate.Schema" in body


def test_contracts_validate_includes_assert(tmp_path: Path) -> None:
    """ASSERT clause is encoded so the x-clio-assert walker can replay it."""
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT customer_risk\n"
        "  SHAPE: {client: str, reason: str(max=300)}\n"
        "  ASSERT: len(reason) > 0\n"
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: risk: customer_risk\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "contracts" / "contracts.go").read_text()
    # x-clio-assert is included in the schema JSON
    assert "x-clio-assert" in body or '"assert"' in body


def test_clio_runtime_validate_written(tmp_path: Path) -> None:
    """validate.go is emitted alongside contracts.go when contracts are used."""
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT customer_risk\n"
        "  SHAPE: {client: str}\n"
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: risk: customer_risk\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "clio_runtime" / "validate" / "validate.go").read_text()
    assert "package validate" in body
    assert "func Schema(" in body
    assert "jsonschema" in body
    # x-clio-assert walker
    assert "func evalAssert(" in body


def test_validate_template_omitted_when_no_contract(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    # No contract → no validate runtime needed
    assert not (out / "clio_runtime" / "validate" / "validate.go").exists()


def test_clio_runtime_cache_written_when_cache_directive_present(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  CACHE: ttl(24h)\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "clio_runtime" / "cache" / "cache.go").read_text()
    assert "package cache" in body
    assert "func Key(" in body
    assert "func Lookup(" in body
    assert "func Store(" in body
    assert "func CacheDirFromEnv(" in body
    assert "sha256" in body


def test_cache_layout_same_as_python_target(tmp_path: Path) -> None:
    """Cache key derivation: SHA256 of step + model + prompt + schema_json.
    Identical to clio/runtime/cache.py."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  CACHE: on\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "clio_runtime" / "cache" / "cache.go").read_text()
    assert 'strings.Join([]string{step, model, prompt, schemaJSON}, "\\n")' in body


def test_cache_omitted_when_no_cache_directive(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    assert not (out / "clio_runtime" / "cache" / "cache.go").exists()


# ---------------------------------------------------------------------------
# Task 9 — exact step stubs + golden snapshot

FIXTURES = Path(__file__).parent.parent / "fixtures"
EXPECTED_GO = FIXTURES / "expected_go"


def test_each_step_emits_its_own_go_file(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_minimal.clio", out)
    files = sorted((out / "steps").iterdir())
    assert [f.name for f in files] == ["01_load.go", "02_summarise.go"]


def test_step_function_has_typed_input_and_output(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_minimal.clio", out)
    body = (out / "steps" / "01_load.go").read_text()
    assert "package steps" in body
    assert "func Load(ctx context.Context, in LoadIn) (LoadOut, error)" in body
    assert 'panic("fill me in: load")' in body
    assert "type LoadIn struct {" in body
    assert "type LoadOut struct {" in body
    assert 'File string `json:"file"`' in body
    assert "Rows []struct" in body
    # godoc comment links Go function back to source step name
    assert "// Load implements the 'load' step." in body


def _read_tree(root: Path) -> dict[str, str]:
    """Return {relative_path: content} for all files under root."""
    result: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            result[str(p.relative_to(root))] = p.read_text()
    return result


def test_golden_go_minimal(tmp_path: Path) -> None:
    """Full-tree comparison against the committed golden snapshot."""
    golden_dir = EXPECTED_GO / "go_minimal"
    if not golden_dir.exists():
        import pytest
        pytest.skip("golden snapshot not yet generated")
    out = tmp_path / "out"
    _compile(FIXTURES / "go_minimal.clio", out)
    emitted = _read_tree(out)
    golden = _read_tree(golden_dir)
    assert emitted == golden, "Emitted tree differs from golden snapshot"


# ---------------------------------------------------------------------------
# Task 10 — flow/flow.go orchestrator


def test_flow_go_chains_exact_steps(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_minimal.clio", out)
    body = (out / "flow" / "flow.go").read_text()
    assert "package flow" in body
    assert "func Run(ctx context.Context, kwargs map[string]any) (map[string]any, error)" in body
    assert "loadOut, err := steps.Load(ctx, " in body
    assert "summariseOut, err := steps.Summarise(ctx, " in body
    # State keys use the GIVES field name (not the step name).
    # go_minimal: load GIVES rows, summarise GIVES total.
    assert 'state["rows"]' in body
    assert 'state["total"]' in body
    assert "return state, nil" in body


# ---------------------------------------------------------------------------
# Task 11 — judgment step with Anthropic SDK + cache integration


def test_judgment_step_calls_anthropic_sdk(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_judgment.clio", out)
    body = (out / "steps" / "02_detect_churn.go").read_text()
    assert "github.com/anthropics/anthropic-sdk-go" in body
    assert "cache.Key(" in body
    assert "cache.Lookup(" in body
    assert "anthropic.NewClient(" in body
    assert ".Messages.New(ctx" in body
    assert "json.Unmarshal(" in body
    assert ".Validate(ctx)" in body
    assert "cache.Store(" in body


def test_judgment_step_uses_resolved_model_id(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_judgment.clio", out)
    body = (out / "steps" / "02_detect_churn.go").read_text()
    assert "claude-haiku-4-5-20251001" in body


def test_judgment_step_with_ttl_cache(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_judgment.clio", out)
    body = (out / "steps" / "02_detect_churn.go").read_text()
    assert "86400" in body  # 24h = 86400s


# ---------------------------------------------------------------------------
# Task 12 — IF/ELSE emission in flow.go


def test_if_else_emits_go_branches(tmp_path: Path) -> None:
    """IF/ELSE block renders as `if <cond> { ... } else { ... }` in flow.go.

    The condition accesses a contract field on the typed step output via a
    Go type assertion:  state["assessment"].(steps.DetectOut).Level == "high".
    """
    out = tmp_path / "out"
    _compile(FIXTURES / "go_control_flow.clio", out)
    body = (out / "flow" / "flow.go").read_text()
    assert 'if state["assessment"].(steps.DetectOut).Level == "high" {' in body
    assert "} else {" in body
    assert "steps.NotifyTeam(ctx," in body
    assert "steps.StoreRecord(ctx," in body


def test_match_emits_go_switch(tmp_path: Path) -> None:
    """MATCH/CASE block renders as a Go switch statement in flow.go.

    The scrutinee is the typed state-field access for the step GIVES field,
    and each CASE arm becomes a quoted string constant (enum idents are
    rendered as Go string constants, consistent with IF condition rendering).
    """
    out = tmp_path / "out"
    _compile(FIXTURES / "go_control_flow.clio", out)
    body = (out / "flow" / "flow.go").read_text()
    assert 'switch state["assessment"].(steps.DetectOut).Level {' in body
    assert 'case "low":' in body
    assert 'case "mid":' in body
    assert 'case "high":' in body


def test_while_loop_emits_for_with_condition(tmp_path: Path) -> None:
    """WHILE block emits a Go `for <cond> { ... }` loop."""
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT poll_result\n"
        "  SHAPE: {done: bool}\n"
        "\n"
        "STEP poll\n"
        "  TAKES: x: str\n"
        "  GIVES: result: poll_result\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "\n"
        "FLOW pipeline\n"
        '  poll(x="start")\n'
        "  -> WHILE result.done != true MAX 10:\n"
        '    poll(x="job-id")\n'
        "\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "flow" / "flow.go").read_text()
    assert 'for state["result"].(steps.PollOut).Done != true {' in body, \
           f"Expected WHILE→for loop in flow.go, got:\n{body}"
    assert "steps.Poll(ctx," in body


def test_golden_go_judgment(tmp_path: Path) -> None:
    """Full-tree comparison against the committed golden snapshot."""
    golden_dir = EXPECTED_GO / "go_judgment"
    if not golden_dir.exists():
        import pytest
        pytest.skip("golden snapshot not yet generated")
    out = tmp_path / "out"
    _compile(FIXTURES / "go_judgment.clio", out)
    emitted = _read_tree(out)
    golden = _read_tree(golden_dir)
    assert emitted == golden, "Emitted tree differs from golden snapshot"


# ---------------------------------------------------------------------------
# Task 15 — sequential FOR EACH emission in flow.go


def test_rescue_emits_defer_recover(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "go_rescue.clio", out)
    body = (out / "flow" / "flow.go").read_text()
    assert "defer func() {" in body
    assert "if r := recover(); r != nil" in body
    assert "steps.Recover(ctx," in body


def test_for_each_sequential(tmp_path: Path) -> None:
    """FOR EACH block renders as `for _, item := range <collection> { ... }`.

    The loop variable is added to scope_local so that the inner step call
    resolves `item` as a bare identifier rather than a state lookup.
    """
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
        "  load(file=\"in.csv\")\n"
        "    -> FOR EACH item IN items:\n"
        "         process(item=item)\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "flow" / "flow.go").read_text()
    assert "for _, item := range" in body
    assert "steps.Process(ctx," in body


# ---------------------------------------------------------------------------
# Task 17 — parallel FOR EACH via errgroup


def test_for_each_parallel_emits_errgroup(tmp_path: Path) -> None:
    """FOR EACH PARALLEL renders as errgroup.WithContext + g.Go goroutines.

    Race-condition handling: state writes are suppressed inside goroutine bodies;
    results are collected into a pre-allocated slice indexed by loop position and
    written to state[<collector>] once after g.Wait().  Go 1.22+ scopes loop
    variables per-iteration so no `item := item` capture copy is emitted.
    """
    out = tmp_path / "out"
    _compile(FIXTURES / "go_parallel.clio", out)
    body = (out / "flow" / "flow.go").read_text()
    assert "errgroup" in body
    assert "g.SetLimit(10)" in body
    assert "g.Go(func() error {" in body
    assert "g.Wait()" in body
    assert "item := item" not in body  # Go 1.22+ scoped loop var


def test_golden_go_parallel(tmp_path: Path) -> None:
    """Full-tree comparison against the committed golden snapshot."""
    golden_dir = EXPECTED_GO / "go_parallel"
    if not golden_dir.exists():
        import pytest
        pytest.skip("golden snapshot not yet generated")
    out = tmp_path / "out"
    _compile(FIXTURES / "go_parallel.clio", out)
    emitted = _read_tree(out)
    golden = _read_tree(golden_dir)
    assert emitted == golden, "Emitted tree differs from golden snapshot"


# ---------------------------------------------------------------------------
# Task 18 — ON_FAIL chain: retry / escalate / fallback / abort


def test_judgment_step_wraps_in_retry_loop(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        '  ON_FAIL: retry(3) then abort("ouch")\n'
        "FLOW pipeline\n"
        '  detect(x="hi")\n'
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "steps" / "01_detect.go").read_text()
    assert "for attempt := 0; attempt < 3; attempt++ {" in body
    assert "time.Sleep" in body
    assert 'fmt.Errorf("ouch' in body


def test_judgment_step_fallback_step(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        '  ON_FAIL: retry(2) then fallback(naive) then abort("done")\n'
        "STEP naive\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW pipeline\n"
        '  detect(x="hi")\n'
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "steps" / "01_detect.go").read_text()
    assert "Naive(ctx, " in body


# ---------------------------------------------------------------------------
# Task 19 — compile-time validation: E_GO_001..012
# ---------------------------------------------------------------------------

import pytest  # noqa: E402 (after top-level imports)


def _compile_expecting_error(source_path: Path, output_dir: Path, code: str) -> None:
    with pytest.raises(ValueError, match=code):
        _compile(source_path, output_dir)


def test_E_GO_001_lang_python(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP load\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "  LANG:  python\n"
        "FLOW pipeline\n"
        "  load(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_001")


def test_E_GO_001_lang_rust(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP load\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "  LANG:  rust\n"
        "FLOW pipeline\n"
        "  load(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_001")


def test_E_GO_002_invoke_mode_cli(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  invoke:\n"
        "    mode: cli\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_002")


def test_E_GO_003_invoke_protocol_bedrock(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  invoke:\n"
        "    mode: api\n"
        "    protocol: bedrock\n"
        "    model: haiku\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_003")


def test_E_GO_004_no_flow(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT just_a_contract\n"
        "  SHAPE: {x: str}\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_004")


def test_E_GO_005_invoke_protocol_openai(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  invoke:\n"
        "    mode: api\n"
        "    protocol: openai\n"
        "    model: haiku\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_005")


def test_E_GO_006_flow_composition(tmp_path: Path) -> None:
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
        "  a(x)\n"
        "FLOW pipeline\n"
        "  sub(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_006")


def test_E_GO_007_impl_mode_rest(tmp_path: Path) -> None:
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
    _compile_expecting_error(src, tmp_path / "out", "E_GO_007")


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


def test_E_GO_009_impl_mode_sql(tmp_path: Path) -> None:
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


def test_E_GO_010_impl_mode_mcp_tool(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP call\n"
        "  TAKES: payload: str\n"
        "  GIVES: result: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode: mcp_tool\n"
        "    server: docs\n"
        "    tool: search\n"
        "    args: {q: \"${payload}\"}\n"
        "    parse: json\n"
        "FLOW pipeline\n"
        "  call(payload=\"x\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
        "  mcp_servers:\n"
        "    docs:\n"
        "      transport: stdio\n"
        "      command: \"my-mcp\"\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_010")


# ---------------------------------------------------------------------------
# Task 21 — mvp_go.clio end-to-end golden snapshot


def test_golden_mvp_go(tmp_path: Path) -> None:
    """Full-tree comparison against the committed golden snapshot.

    exercises/mvp_go.clio covers the full v0.20.0 surface:
    CONTRACT + exact (LANG: go) + judgment + CACHE + ON_FAIL chain.
    """
    out = tmp_path / "out"
    _compile(Path("examples/mvp_go.clio"), out)
    assert _read_tree(out) == _read_tree(EXPECTED_GO / "mvp_go")


def test_E_GO_012_test_block(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP load\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW pipeline\n"
        "  load(x=\"hi\")\n"
        "TEST sanity:\n"
        "  FLOW: pipeline\n"
        "  WITH:\n"
        "    x: \"hi\"\n"
        "  EXPECTS:\n"
        "    y: not_empty\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    _compile_expecting_error(src, tmp_path / "out", "E_GO_012")


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


def _rest_gives_graph():
    from clio.ir.builder import build_ir as build_graph
    from clio.parser.parser import parse
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
    assert "for _i := 0; _i < _attempts; _i++ {" in out
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


def test_render_rest_step_go_no_gives_side_effect():
    from clio.emitters._go_step_renderers import render_rest_step_go
    from clio.ir.builder import build_ir as build_graph
    from clio.parser.parser import parse

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


def test_go_emits_rest_runtimes_and_dispatches_rest_step(tmp_path, monkeypatch):
    from clio.emitters import go as _go
    from clio.emitters._go_helpers import _flow_uses_rest
    from clio.emitters.go import GoEmitter
    from clio.ir.builder import build_ir as build_graph
    from clio.parser.parser import parse

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
    from clio.ir.builder import build_ir as build_graph
    from clio.parser.parser import parse

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


# ---------------------------------------------------------------------------
# Shell step renderer tests (Phase 2 — E_GO_008)

from clio.emitters._go_step_renderers import render_shell_step_go  # noqa: E402
from clio.ir.builder import build_ir as _build_ir_for_shell  # noqa: E402
from clio.parser.parser import parse as _parse_clio_for_shell  # noqa: E402


def _shell_step_and_graph(source: str):
    """Parse a one-step .clio source, build its graph, return (step, contracts, graph)."""
    graph = _build_ir_for_shell(_parse_clio_for_shell(source))
    step = next(s for s in graph.steps if s.name == "load_corpus")
    contracts = {c.name: c for c in graph.contracts}
    return step, contracts, graph


def _go_pkg(graph):
    from clio.emitters._go_helpers import _go_module_name
    return _go_module_name(graph)


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


def test_render_shell_step_go_substitutes_each_token_and_honours_timeout() -> None:
    src = (
        "STEP load_corpus\n"
        "  TAKES: file: str, pattern: str\n"
        "  GIVES: matches: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode: shell\n"
        "    cmd:  \"grep ${pattern} ${file}\"\n"
        "    timeout: 5s\n"
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
    from clio.ir.builder import build_ir as _build_ir_ns
    from clio.parser.parser import parse as _parse_ns
    graph = _build_ir_ns(_parse_ns(src))
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
    from clio.emitters.go import _collect_reachable_steps
    from clio.ir.builder import build_ir as _build_ir_local
    from clio.parser.parser import parse as _parse_local

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
    graph = _build_ir_local(_parse_local(src))
    collected = _collect_reachable_steps(graph)
    names = [s.name for s in collected]
    # first-seen order: load (top-level), then process (inside FOR EACH body)
    assert names == ["load", "process"], names


def test_collect_reachable_steps_dedups_by_name() -> None:
    """A step called from two reachable sites is collected exactly once,
    at its first-seen position (stable NN_ numbering)."""
    from clio.emitters.go import _collect_reachable_steps
    from clio.ir.builder import build_ir as _build_ir_local
    from clio.parser.parser import parse as _parse_local

    # detect.GIVES: result (a CONTRACT with `outcome` field) so the IF
    # condition can reference result.outcome — a scalar gives would have no
    # sub-field to compare, so a contract is the minimal valid structure.
    src = (
        "CONTRACT status\n"
        "  SHAPE: {outcome: str}\n"
        "\n"
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: result: status\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "STEP audit\n"
        "  TAKES: x: str\n"
        "  GIVES: note: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW pipeline\n"
        '  detect(x="hi")\n'
        "  -> IF result.outcome == yes:\n"
        '       audit(x="a")\n'
        "  ELSE:\n"
        '       audit(x="b")\n'
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    graph = _build_ir_local(_parse_local(src))
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
