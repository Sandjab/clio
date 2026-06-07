from pathlib import Path

from clio.cli import _cmd_compile

FIXTURES = Path(__file__).parent.parent / "fixtures"
EXPECTED_SWIFT = FIXTURES / "expected_swift"


def _compile(source_path: Path, output_dir: Path) -> int:
    return _cmd_compile(str(source_path), "swift", str(output_dir), None)


def test_swift_target_is_a_valid_choice(tmp_path: Path) -> None:
    src = tmp_path / "f.clio"
    src.write_text(
        "STEP load\n"
        "  TAKES: path: str\n"
        "  GIVES: data: str\n"
        "  MODE:  exact\n\n"
        'FLOW pipeline\n'
        '  load(path="input.txt")\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc == 0
    assert (tmp_path / "out" / "Package.swift").exists()


def test_swift_refuses_source_without_flow(tmp_path: Path) -> None:
    src = tmp_path / "noflow.clio"
    src.write_text("STEP only\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n")
    rc = _compile(src, tmp_path / "out")
    assert rc != 0   # E_SWIFT_004


def test_swift_minimal_emits_steps_and_flow(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_minimal.clio", out)
    assert (out / "Sources/ClioFlow/Steps/Step01_load.swift").exists()
    flow = (out / "Sources/ClioFlow/Flow.swift").read_text()
    assert "func run(kwargs: [String: Any]) async throws" in flow
    assert "try await step_summarize(" in flow


def test_swift_emits_contract_struct_and_validate(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_contract.clio", out)
    contracts_path = out / "Sources/ClioFlow/Contracts.swift"
    assert contracts_path.exists(), "Contracts.swift was not emitted"
    contracts = contracts_path.read_text()
    assert "struct CustomerRisk: Codable" in contracts
    assert "static let jsonSchema" in contracts
    assert "func validate() throws" in contracts
    assert (out / "Sources/ClioFlow/Runtime/Validate.swift").exists()


# ---------------------------------------------------------------------------
# Task 5 — honest phase-1 refusal gate
# ---------------------------------------------------------------------------

# ---- lifted in Phase 2: anthropic judgment is now supported ---------------

def test_swift_judgment_anthropic_default_allowed(tmp_path: Path) -> None:
    """A judgment-mode step with no invoke block (default Anthropic) compiles
    from Phase 2 onwards — the temporary refusal was lifted."""
    src = tmp_path / "j.clio"
    src.write_text(
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE: judgment\n"
        "\n"
        "FLOW f\n"
        '  s(x="hi")\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc == 0


def test_swift_judgment_emits_anthropic_call(tmp_path: Path) -> None:
    """Judgment step emits the correct files: step file with Out decode, and
    Anthropic.swift runtime with the URL and x-api-key header."""
    out = tmp_path / "out"
    rc = _compile(FIXTURES / "swift_judgment.clio", out)
    assert rc == 0
    # Step file: references Anthropic.complete and decodes into Out struct.
    step_file = out / "Sources/ClioFlow/Steps/Step01_analyze.swift"
    assert step_file.exists(), "judgment step file not emitted"
    step_src = step_file.read_text()
    assert "Anthropic.complete(" in step_src
    assert "Step01_analyze_Out.self" in step_src
    assert "out.result.validate()" in step_src  # ContractRef → validate call
    # Runtime file: the URL and header live in Anthropic.swift.
    anthropic_rt = out / "Sources/ClioFlow/Runtime/Anthropic.swift"
    assert anthropic_rt.exists(), "Anthropic.swift runtime not emitted"
    rt_src = anthropic_rt.read_text()
    assert "api.anthropic.com/v1/messages" in rt_src
    assert "x-api-key" in rt_src


# ---- temporary refusals (will be lifted in later phases) ------------------


def test_swift_refuses_shell_impl(tmp_path: Path, capsys: object) -> None:
    """impl.mode: shell is not yet supported (Phase 4)."""
    src = tmp_path / "sh.clio"
    src.write_text(
        "STEP stamp\n"
        "  GIVES: ts: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: shell\n"
        '    cmd: "date"\n'
        "\n"
        "FLOW pipeline\n"
        "  stamp()\n"
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "not yet supported" in captured.err.lower()


def test_swift_if_block_compiles(tmp_path: Path) -> None:
    """IF/ELSE control flow compiles successfully from Phase 3.

    Source uses CONTRACT + LANG: auto so E_SWIFT_001 does not fire."""
    src = tmp_path / "if.clio"
    src.write_text(
        "CONTRACT risk_result\n"
        "  SHAPE: {level: enum(low|mid|high)}\n"
        "\n"
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: assessment: risk_result\n"
        "  MODE: exact\n"
        "  LANG: auto\n"
        "\n"
        "STEP notify\n"
        "  TAKES: x: str\n"
        "  GIVES: done: bool\n"
        "  MODE: exact\n"
        "  LANG: auto\n"
        "\n"
        "FLOW pipeline\n"
        '  detect(x="input")\n'
        "  -> IF assessment.level == high:\n"
        '       notify(x="high")\n'
        "  ELSE:\n"
        '       notify(x="low")\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc == 0
    flow = (tmp_path / "out" / "Sources" / "ClioFlow" / "Flow.swift").read_text()
    assert "if " in flow
    assert "} else {" in flow


def test_swift_refuses_two_flows(tmp_path: Path, capsys: object) -> None:
    """Sub-flow composition (multiple FLOWs) is not yet supported (Phase 5)."""
    src = tmp_path / "subflow.clio"
    src.write_text(
        "STEP fetch\n"
        "  TAKES: url: str\n"
        "  GIVES: article: str\n"
        "  MODE: exact\n"
        "\n"
        "STEP shout\n"
        "  TAKES: article: str\n"
        "  GIVES: loud: str\n"
        "  MODE: exact\n"
        "\n"
        "FLOW enrich\n"
        "  TAKES: url: str\n"
        "  GIVES: article: str\n"
        "  fetch(url=url)\n"
        "\n"
        "FLOW pipeline\n"
        "  TAKES: url: str\n"
        "  GIVES: loud: str\n"
        "  enrich(url=url)\n"
        "  -> shout(article=article)\n"
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "not yet supported" in captured.err.lower()


def test_swift_refuses_rescue(tmp_path: Path, capsys: object) -> None:
    """RESCUE/RESUME is not yet supported (Phase 5).

    A single-flow source with a RESCUE handler uses no feature that is
    otherwise refused, so without an explicit refusal it would pass the gate
    and emit step files — but render_flow_swift only walks flow.chain, never
    flow.rescues, so the handler logic is silently dropped (the protected
    step's error would propagate instead of running the handler). Refuse it
    until the Swift RESCUE emitter ships."""
    src = tmp_path / "rescue.clio"
    src.write_text(
        "STEP risky\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE: judgment\n"
        "\n"
        "STEP recover\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE: exact\n"
        "\n"
        "FLOW pipeline\n"
        '  risky(x="hi")\n'
        "\n"
        "  RESCUE risky:\n"
        '    -> recover(x="hi")\n'
        "    -> RESUME(recover.y)\n"
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "not yet supported" in captured.err.lower()
    assert "phase 5" in captured.err.lower()


def test_swift_refuses_rest_impl(tmp_path: Path, capsys: object) -> None:
    """impl.mode: rest (json body) is not yet supported (Phase 4).

    E_SWIFT_013 (form/file/multipart) is a distinct permanent check tested
    separately. This test uses a json body, which is NOT E_SWIFT_013, so the
    temporary rest refusal is the only gate that fires."""
    src = tmp_path / "rest.clio"
    src.write_text(
        "STEP fetch\n"
        "  TAKES: id: str\n"
        "  GIVES: body: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: GET\n"
        '    url: "http://x/${id}"\n'
        "\n"
        "FLOW pipeline\n"
        '  fetch(id="1")\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "not yet supported" in captured.err.lower()


# ---- permanent E_SWIFT_* refusals -----------------------------------------

def test_E_SWIFT_001_lang_python(tmp_path: Path, capsys: object) -> None:
    """LANG: python on an exact step is a permanent refusal (E_SWIFT_001)."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP load\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE: exact\n"
        "  LANG: python\n"
        "\n"
        "FLOW pipeline\n"
        '  load(x="hi")\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "E_SWIFT_001" in captured.err


def test_E_SWIFT_001_lang_go(tmp_path: Path, capsys: object) -> None:
    """LANG: go on an exact step is a permanent refusal (E_SWIFT_001)."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP run\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE: exact\n"
        "  LANG: go\n"
        "\n"
        "FLOW pipeline\n"
        '  run(x="hi")\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "E_SWIFT_001" in captured.err


def test_E_SWIFT_001_not_raised_for_auto_lang(tmp_path: Path) -> None:
    """LANG: auto is explicitly allowed — must NOT trigger E_SWIFT_001."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP greet\n"
        "  TAKES: name: str\n"
        "  GIVES: msg: str\n"
        "  MODE: exact\n"
        "  LANG: auto\n"
        "\n"
        "FLOW pipeline\n"
        '  greet(name="world")\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc == 0


def test_E_SWIFT_001_not_raised_for_no_lang(tmp_path: Path) -> None:
    """No LANG field (None) is allowed — must NOT trigger E_SWIFT_001."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP greet\n"
        "  TAKES: name: str\n"
        "  GIVES: msg: str\n"
        "  MODE: exact\n"
        "\n"
        "FLOW pipeline\n"
        '  greet(name="world")\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc == 0


def test_E_SWIFT_002_invoke_mode_cli(tmp_path: Path, capsys: object) -> None:
    """invoke.mode: cli is a permanent refusal (E_SWIFT_002).

    The permanent invoke check fires before the temporary judgment check
    so the stable error code is surfaced even in Phase 1."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: cli\n"
        "\n"
        "FLOW pipeline\n"
        '  detect(x="hi")\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "E_SWIFT_002" in captured.err


def test_E_SWIFT_003_invoke_protocol_bedrock(tmp_path: Path, capsys: object) -> None:
    """invoke.protocol: bedrock is a permanent refusal (E_SWIFT_003)."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: api\n"
        "    protocol: bedrock\n"
        "    model: haiku\n"
        "\n"
        "FLOW pipeline\n"
        '  detect(x="hi")\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "E_SWIFT_003" in captured.err


def test_E_SWIFT_003_invoke_protocol_vertex(tmp_path: Path, capsys: object) -> None:
    """invoke.protocol: vertex is a permanent refusal (E_SWIFT_003)."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: api\n"
        "    protocol: vertex\n"
        "    model: haiku\n"
        "\n"
        "FLOW pipeline\n"
        '  detect(x="hi")\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "E_SWIFT_003" in captured.err


def test_E_SWIFT_005_invoke_protocol_openai(tmp_path: Path, capsys: object) -> None:
    """invoke.protocol: openai is a permanent refusal (E_SWIFT_005).

    Note: model names with hyphens (like gpt-4) do not parse; use a simple
    identifier instead."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: api\n"
        "    protocol: openai\n"
        "    model: haiku\n"
        "\n"
        "FLOW pipeline\n"
        '  detect(x="hi")\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "E_SWIFT_005" in captured.err


def test_E_SWIFT_009_impl_mode_sql(tmp_path: Path, capsys: object) -> None:
    """impl.mode: sql is a permanent refusal (E_SWIFT_009)."""
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT OrderRow\n"
        "  SHAPE: {id: int}\n"
        "\n"
        "STEP q\n"
        "  TAKES: name: str\n"
        "  GIVES: rows: List<OrderRow>\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: sql\n"
        "    db: crm\n"
        "    query: |\n"
        "      SELECT id FROM t WHERE name = :name\n"
        "\n"
        "FLOW pipeline\n"
        '  q(name="alice")\n'
        "\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
        "  databases:\n"
        "    crm:\n"
        "      driver: sqlite\n"
        '      url: ":memory:"\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "E_SWIFT_009" in captured.err


def test_E_SWIFT_010_impl_mode_mcp_tool(tmp_path: Path, capsys: object) -> None:
    """impl.mode: mcp_tool is a permanent refusal (E_SWIFT_010)."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP call\n"
        "  TAKES: payload: str\n"
        "  GIVES: result: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: mcp_tool\n"
        "    server: docs\n"
        "    tool: search\n"
        '    args: {q: "${payload}"}\n'
        "    parse: json\n"
        "\n"
        "FLOW pipeline\n"
        '  call(payload="x")\n'
        "\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
        "  mcp_servers:\n"
        "    docs:\n"
        "      transport: stdio\n"
        '      command: "my-mcp"\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "E_SWIFT_010" in captured.err


def test_E_SWIFT_012_test_block(tmp_path: Path, capsys: object) -> None:
    """A TEST block is a permanent refusal (E_SWIFT_012)."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP load\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE: exact\n"
        "\n"
        "FLOW pipeline\n"
        '  load(x="hi")\n'
        "\n"
        "TEST sanity:\n"
        "  FLOW: pipeline\n"
        "  WITH:\n"
        '    x: "hi"\n'
        "  EXPECTS:\n"
        "    y: not_empty\n"
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "E_SWIFT_012" in captured.err


def test_E_SWIFT_013_rest_form_body_refused(tmp_path: Path, capsys: object) -> None:
    """form body on impl.mode: rest is a permanent refusal (E_SWIFT_013).

    Fires BEFORE the temporary 'rest not supported' check so the stable
    code is surfaced even in Phase 1."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP send\n"
        "  TAKES: u: str\n"
        "  GIVES: r: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: POST\n"
        '    url: "https://example.com/x"\n'
        '    body: {form: {user: "${u}"}}\n'
        "\n"
        "FLOW pipeline\n"
        '  send(u="bob")\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "E_SWIFT_013" in captured.err


def test_E_SWIFT_013_rest_multipart_body_refused(tmp_path: Path, capsys: object) -> None:
    """multipart body on impl.mode: rest is a permanent refusal (E_SWIFT_013)."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP send\n"
        "  TAKES: u: str\n"
        "  GIVES: r: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: POST\n"
        '    url: "https://example.com/x"\n'
        '    body: {multipart: {label: "${u}"}}\n'
        "\n"
        "FLOW pipeline\n"
        '  send(u="bob")\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "E_SWIFT_013" in captured.err


# ---------------------------------------------------------------------------
# P2.3 — ON_FAIL chain: retry / escalate / fallback / abort
# ---------------------------------------------------------------------------


def test_swift_judgment_onfail_emits_retry_loop(tmp_path: Path) -> None:
    """The detect step emits the retry loop, Task.sleep, fallback call, and abort message."""
    out = tmp_path / "out"
    rc = _compile(FIXTURES / "swift_judgment_onfail.clio", out)
    assert rc == 0

    steps_dir = out / "Sources/ClioFlow/Steps"

    detect_src = (steps_dir / "Step01_detect.swift").read_text()
    assert "for attempt in 0..<2" in detect_src, "retry loop not emitted"
    assert "Task.sleep" in detect_src, "exponential backoff sleep not emitted"
    assert "step_naive(" in detect_src, "fallback call to step_naive not emitted"
    assert "detection exhausted" in detect_src, "abort message not emitted"

    # naive must be emitted as Step02_naive.swift even though it's not in the flow chain
    assert (steps_dir / "Step02_naive.swift").exists(), "fallback step file not emitted"


def test_swift_judgment_onfail_abort_message_is_escaped(tmp_path: Path) -> None:
    """A quote inside an abort() message is escaped so the emitted Swift string
    literal stays valid. Without escaping `"a "quote" b"` would not compile."""
    src = tmp_path / "esc.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        '  ON_FAIL: retry(2) then abort("a \\"quote\\" b")\n'
        "\n"
        "FLOW pipeline\n"
        '  detect(x="hi")\n'
        "\n"
        "RESOURCES\n"
        "  target: swift\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    rc = _compile(src, out)
    assert rc == 0
    detect_src = (out / "Sources/ClioFlow/Steps/Step01_detect.swift").read_text()
    assert 'throw AnthropicError(message: "a \\"quote\\" b")' in detect_src


def _read_tree(root: Path) -> dict[str, str]:
    """Return {relative_path: content} for all files under root."""
    result: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            result[str(p.relative_to(root))] = p.read_text()
    return result


def test_golden_swift_minimal(tmp_path: Path) -> None:
    """Full-tree comparison against the committed golden snapshot."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_minimal.clio", out)
    assert _read_tree(out) == _read_tree(EXPECTED_SWIFT / "swift_minimal")


def test_golden_swift_contract(tmp_path: Path) -> None:
    """Full-tree comparison against the committed golden snapshot."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_contract.clio", out)
    assert _read_tree(out) == _read_tree(EXPECTED_SWIFT / "swift_contract")


def test_golden_swift_judgment(tmp_path: Path) -> None:
    """Full-tree comparison against the committed golden snapshot."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_judgment.clio", out)
    assert _read_tree(out) == _read_tree(EXPECTED_SWIFT / "swift_judgment")


def test_golden_swift_judgment_cache(tmp_path: Path) -> None:
    """Full-tree comparison against the committed golden snapshot."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_judgment_cache.clio", out)
    assert _read_tree(out) == _read_tree(EXPECTED_SWIFT / "swift_judgment_cache")


# ---------------------------------------------------------------------------
# Phase 3a — IF/ELSE, MATCH/CASE, WHILE control flow
# ---------------------------------------------------------------------------


def test_swift_control_flow_emits_if_match_while(tmp_path: Path) -> None:
    """The emitted Flow.swift contains switch/case/default, if/else, and a
    bounded while loop — all from the swift_control_flow.clio fixture."""
    out = tmp_path / "out"
    rc = _compile(FIXTURES / "swift_control_flow.clio", out)
    assert rc == 0
    flow = (out / "Sources" / "ClioFlow" / "Flow.swift").read_text()
    assert "switch " in flow
    assert 'case "low":' in flow
    assert "default:" in flow
    assert "if " in flow
    assert "} else {" in flow
    assert "while " in flow
    assert "&& _while" in flow


def test_golden_swift_control_flow(tmp_path: Path) -> None:
    """Full-tree comparison against the committed golden snapshot."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_control_flow.clio", out)
    assert _read_tree(out) == _read_tree(EXPECTED_SWIFT / "swift_control_flow")


# ---------------------------------------------------------------------------
# Phase 3b — sequential FOR EACH with loop-variable scoping
# ---------------------------------------------------------------------------


def test_swift_foreach_seq_emits_for_loop(tmp_path: Path) -> None:
    """Emitted Flow.swift contains a typed for-in loop over the collection.

    The collection cast uses the element-level contract type (RiskAssessment),
    not an untyped [Any], so the loop variable is strongly typed."""
    out = tmp_path / "out"
    rc = _compile(FIXTURES / "swift_foreach_seq.clio", out)
    assert rc == 0
    flow = (out / "Sources" / "ClioFlow" / "Flow.swift").read_text()
    # for-in loop with typed cast on the collection
    assert "for a in (state[\"assessments\"] as! [RiskAssessment])" in flow
    # nested MATCH resolves loop var as bare identifier, not state lookup
    assert "switch a.level" in flow
    assert 'state["a"]' not in flow
    # second FOR EACH with IF — loop var used bare
    assert "for b in (state[\"assessments\"] as! [RiskAssessment])" in flow
    assert "b.level ==" in flow
    assert 'state["b"]' not in flow


def test_swift_foreach_seq_parallel_compiles(tmp_path: Path) -> None:
    """Parallel FOR EACH now compiles successfully (Phase 3c lifted the refusal)."""
    src = tmp_path / "par.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: items: List<str>\n"
        "  MODE: exact\n"
        "\n"
        "STEP process\n"
        "  TAKES: item: str\n"
        "  GIVES: result: str\n"
        "  MODE: exact\n"
        "\n"
        "FLOW pipeline\n"
        '  detect(x="in")\n'
        "  -> FOR EACH item IN items PARALLEL AS results:\n"
        "       process(item=item)\n"
    )
    rc = _compile(src, tmp_path / "out")
    assert rc == 0
    flow = (tmp_path / "out" / "Sources" / "ClioFlow" / "Flow.swift").read_text()
    assert "withThrowingTaskGroup" in flow
    assert "group.addTask" in flow
    assert 'state["results"]' in flow


def test_golden_swift_foreach_seq(tmp_path: Path) -> None:
    """Full-tree comparison against the committed golden snapshot."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_foreach_seq.clio", out)
    assert _read_tree(out) == _read_tree(EXPECTED_SWIFT / "swift_foreach_seq")


# ---------------------------------------------------------------------------
# Phase 3c — parallel FOR EACH via withThrowingTaskGroup
# ---------------------------------------------------------------------------


def test_swift_parallel_foreach_emits_task_group(tmp_path: Path) -> None:
    """Parallel FOR EACH emits withThrowingTaskGroup with cap-10 back-pressure
    and ordered collect.  The group.addTask closure must NOT reference state."""
    out = tmp_path / "out"
    rc = _compile(FIXTURES / "swift_parallel.clio", out)
    assert rc == 0
    flow = (out / "Sources" / "ClioFlow" / "Flow.swift").read_text()
    # TaskGroup is used
    assert "withThrowingTaskGroup" in flow
    assert "group.addTask" in flow
    # Back-pressure cap of 10
    assert ">= 10" in flow
    # Ordered collect: (0..<N).map { dict[$0]! }
    assert "(0..<" in flow
    assert 'state["labels"]' in flow
    # No reference to `state` inside the addTask closure (child tasks must not
    # touch state — [String: Any] is not Sendable)
    lines = flow.splitlines()
    in_task = False
    for line in lines:
        stripped = line.lstrip()
        if "group.addTask" in stripped:
            in_task = True
        elif in_task and stripped == "}":
            in_task = False
        elif in_task:
            assert "state[" not in line, (
                f"child task captures state (Sendable violation): {line!r}"
            )


def test_golden_swift_parallel(tmp_path: Path) -> None:
    """Full-tree comparison against the committed golden snapshot."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_parallel.clio", out)
    assert _read_tree(out) == _read_tree(EXPECTED_SWIFT / "swift_parallel")


def _addtask_closure_lines(flow: str) -> list[str]:
    """Return the lines strictly inside each `group.addTask { ... }` closure.

    Used to assert that a parallel-body closure never references `state`."""
    inside: list[str] = []
    in_task = False
    for line in flow.splitlines():
        stripped = line.lstrip()
        if "group.addTask" in stripped:
            in_task = True
        elif in_task and stripped == "}":
            in_task = False
        elif in_task:
            inside.append(line)
    return inside


def test_swift_parallel_foreach_hoists_shared_state_kwarg(tmp_path: Path) -> None:
    """A parallel-body kwarg that reads an upstream state field is HOISTED to a
    `let` on the actor before withThrowingTaskGroup, and the @Sendable closure
    references the hoisted Sendable local — never `state` directly.

    Why it matters (Fix 1): `var state` is [String: Any], not Sendable. Emitting
    `state["threshold"] as! Double` inside group.addTask captures `state` and
    Swift 6 strict concurrency rejects the build."""
    out = tmp_path / "out"
    rc = _compile(FIXTURES / "swift_parallel_shared.clio", out)
    assert rc == 0
    flow = (out / "Sources" / "ClioFlow" / "Flow.swift").read_text()
    # The hoisted local is bound on the actor, before the TaskGroup.
    group_pos = flow.index("withThrowingTaskGroup")
    hoist_pos = flow.index('state["threshold"] as! Double')
    assert hoist_pos < group_pos, (
        "the state read for the hoisted kwarg must precede withThrowingTaskGroup"
    )
    # The hoisted local is uniquely named with the call index suffix.
    assert "let _kw3_threshold = state[\"threshold\"] as! Double" in flow
    # No `state[` access anywhere inside the addTask closure.
    for line in _addtask_closure_lines(flow):
        assert "state[" not in line, (
            f"child task captures state (Sendable violation): {line!r}"
        )
    # The In constructor inside the closure uses the hoisted local + loop var.
    assert "Step03_classify_In(item: item, threshold: _kw3_threshold)" in flow


def test_swift_parallel_foreach_refuses_non_sendable_kwarg(
    tmp_path: Path, capsys: object
) -> None:
    """A parallel-body kwarg whose resolved Swift type contains `Any` (a value
    that cannot be hoisted as Sendable) is refused fail-loud rather than emitting
    Swift that fails strict-concurrency checking.

    Here `cfg` is an anonymous record `{a: str, b: int}`, which _type_to_swift
    maps to `[String: Any]` (non-Sendable). Passing it as an upstream kwarg into
    a parallel body must be refused, not silently hoisted into an uncompilable
    `let _kwN_cfg = state["cfg"] as! [String: Any]`."""
    src = tmp_path / "ns.clio"
    src.write_text(
        "STEP load\n"
        "  GIVES: cfg: {a: str, b: int}\n"
        "  MODE: exact\n"
        "\n"
        "STEP load2\n"
        "  TAKES: cfg: {a: str, b: int}\n"
        "  GIVES: items: List<str>\n"
        "  MODE: exact\n"
        "\n"
        "STEP work\n"
        "  TAKES: item: str, cfg: {a: str, b: int}\n"
        "  GIVES: out: str\n"
        "  MODE: exact\n"
        "\n"
        "FLOW pipeline\n"
        "  load()\n"
        "  -> load2(cfg=cfg)\n"
        "  -> FOR EACH item IN items PARALLEL AS outs:\n"
        "       work(item=item, cfg=cfg)\n"
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "non-sendable" in captured.err.lower()
    assert "cfg" in captured.err


# ---------------------------------------------------------------------------
# Fix 2 — FOR EACH over a loop variable is refused (seq + parallel)
# ---------------------------------------------------------------------------


def test_swift_refuses_foreach_over_loop_var_sequential(
    tmp_path: Path, capsys: object
) -> None:
    """A nested sequential FOR EACH whose collection is an outer loop variable
    is refused fail-loud.

    The collection resolver consults state_field_to_step / take_types but never
    the loop-var scope, so it would emit `state["row"] as! [Any]` — a runtime
    lookup of a key that does not exist (`row` is a loop var, not a state field).
    Refuse rather than emit wrong Swift."""
    src = tmp_path / "lv.clio"
    src.write_text(
        "STEP load\n"
        "  TAKES: file: str\n"
        "  GIVES: matrix: List<List<str>>\n"
        "  MODE: exact\n"
        "\n"
        "STEP work\n"
        "  TAKES: cell: str\n"
        "  GIVES: out: str\n"
        "  MODE: exact\n"
        "\n"
        "FLOW pipeline\n"
        '  load(file="in.csv")\n'
        "  -> FOR EACH row IN matrix:\n"
        "       FOR EACH cell IN row:\n"
        "         work(cell=cell)\n"
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "for each over a loop variable" in captured.err.lower()


def test_swift_refuses_foreach_over_loop_var_parallel(
    tmp_path: Path, capsys: object
) -> None:
    """A nested PARALLEL FOR EACH whose collection is an outer loop variable is
    refused fail-loud (same gap as sequential, plus the resolved [Any] element
    would be non-Sendable inside the TaskGroup)."""
    src = tmp_path / "lvp.clio"
    src.write_text(
        "STEP load\n"
        "  TAKES: file: str\n"
        "  GIVES: matrix: List<List<str>>\n"
        "  MODE: exact\n"
        "\n"
        "STEP work\n"
        "  TAKES: cell: str\n"
        "  GIVES: out: str\n"
        "  MODE: exact\n"
        "\n"
        "FLOW pipeline\n"
        '  load(file="in.csv")\n'
        "  -> FOR EACH row IN matrix:\n"
        "       FOR EACH cell IN row PARALLEL AS outs:\n"
        "         work(cell=cell)\n"
    )
    rc = _compile(src, tmp_path / "out")
    assert rc != 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "for each over a loop variable" in captured.err.lower()


def test_swift_foreach_over_state_field_still_accepted(tmp_path: Path) -> None:
    """Regression guard: the normal case — FOR EACH over a GIVES/TAKE state
    field, not a loop var — must remain accepted. swift_foreach_seq iterates a
    GIVES (`assessments`), so it must still compile."""
    out = tmp_path / "out"
    rc = _compile(FIXTURES / "swift_foreach_seq.clio", out)
    assert rc == 0


# ---------------------------------------------------------------------------
# Gemini review fixes (A, B, C, D, G, I)
# ---------------------------------------------------------------------------


def test_fix_a_optional_chaining_condition_dot(tmp_path: Path) -> None:
    """Defensive: when a GIVES/TAKE type renders to a Swift Optional (ends
    with '?'), the condition renderer must use '?.' not '.' for field access.

    Reachability note: the IR validator rejects IF/WHILE conditions on Optional
    contract types (only non-optional ContractRef passes the field-access check),
    so this path is currently unreachable from CLIO source.  The fix is applied
    defensively — the renderer is correct regardless.  This test exercises the
    renderer logic directly via string-match rather than parse+compile."""
    # Simulate a step that GIVES an Optional type (e.g. "Risk?").
    # Build a minimal fake StepIR with Optional gives to drive the renderer.
    from dataclasses import dataclass

    from clio.emitters._swift_flow_renderer import _swift_condition_expr
    from clio.ir.graph import ConditionIR

    class FakeOptType:
        """Mimics an Optional swift type whose _type_to_swift returns 'Risk?'."""

    @dataclass
    class MinimalFieldIR:
        name: str
        type: object

    # Patch _type_to_swift to return "Risk?" for our fake type.
    from clio.emitters import _swift_flow_renderer as renderer
    original = renderer._type_to_swift

    def patched(t: object, contracts: object) -> str:
        if isinstance(t, FakeOptType):
            return "Risk?"
        return original(t, contracts)  # type: ignore[arg-type]

    renderer._type_to_swift = patched  # type: ignore[assignment]
    try:
        fake_gives = MinimalFieldIR(name="result", type=FakeOptType())
        # Minimal StepIR-like object sufficient for _swift_condition_expr.
        fake_step = type("FakeStep", (), {"gives": fake_gives})()
        state_field_to_step = {"result": fake_step}

        cond = ConditionIR(
            step_name="result",
            field="level",
            op="==",
            literal_value="high",
            literal_kind="ident",
        )
        result = _swift_condition_expr(cond, set(), state_field_to_step, {}, {})
        # Must use '?.' not '.' because the type ends with '?'
        assert "?." in result, f"expected '?.' for Optional type, got: {result!r}"
        assert "(state[\"result\"] as! Risk?)?." in result
    finally:
        renderer._type_to_swift = original  # type: ignore[assignment]


def test_fix_a_non_optional_still_uses_plain_dot(tmp_path: Path) -> None:
    """Regression guard: non-Optional types must still use '.' not '?.'."""
    from clio.emitters._swift_flow_renderer import _swift_condition_expr
    from clio.ir.graph import ConditionIR

    class FakeNonOptType:
        pass

    from clio.emitters import _swift_flow_renderer as renderer
    original = renderer._type_to_swift

    def patched(t: object, contracts: object) -> str:
        if isinstance(t, FakeNonOptType):
            return "Risk"
        return original(t, contracts)  # type: ignore[arg-type]

    renderer._type_to_swift = patched  # type: ignore[assignment]
    try:
        fake_gives = type("FakeFieldIR", (), {"name": "result", "type": FakeNonOptType()})()
        fake_step = type("FakeStep", (), {"gives": fake_gives})()
        state_field_to_step = {"result": fake_step}

        cond = ConditionIR(
            step_name="result",
            field="level",
            op="==",
            literal_value="high",
            literal_kind="ident",
        )
        result = _swift_condition_expr(cond, set(), state_field_to_step, {}, {})
        assert "?." not in result, f"non-Optional type must not use '?.', got: {result!r}"
        assert "(state[\"result\"] as! Risk)." in result
    finally:
        renderer._type_to_swift = original  # type: ignore[assignment]


def test_fix_b_null_condition_emits_nil(tmp_path: Path) -> None:
    """Defensive: 'null' ident in a condition RHS renders as Swift nil.

    Reachability note: the IR builder currently treats 'null' as an ident
    (literal_kind='ident', literal_value='null'), so the condition renderer
    was emitting the string \"null\" rather than nil.  The fix is applied at
    the renderer level so the emitted Swift is correct regardless."""
    from clio.emitters._swift_flow_renderer import _swift_condition_expr
    from clio.ir.graph import ConditionIR

    cond = ConditionIR(
        step_name="r",
        field="level",
        op="==",
        literal_value="null",
        literal_kind="ident",
    )
    # With no step/take type context the fallback branch fires.
    result = _swift_condition_expr(cond, set(), {}, {}, {})
    assert "nil" in result, f"'null' ident must render as nil, got: {result!r}"
    assert '"null"' not in result, f"'null' must NOT render as string literal, got: {result!r}"


def test_fix_b_none_kwarg_emits_nil(tmp_path: Path) -> None:
    """Defensive: Python None value in a kwarg renders as Swift nil.

    Reachability note: CLIO source cannot currently produce a Python None in
    a kwarg value — 'null' in kwargs is parsed as '@null' (a state ref).
    The guard is applied defensively in _swift_kwarg_value."""
    from clio.emitters._swift_flow_renderer import _swift_kwarg_value
    result = _swift_kwarg_value(None, {}, {}, {})
    assert result == "nil", f"Python None must render as nil, got: {result!r}"


def test_fix_c_anthropic_swift_error_surface(tmp_path: Path) -> None:
    """Emitted Anthropic.swift parses the API error object and surfaces the
    message when the API returns an error body instead of a content array."""
    from clio.emitters._swift_runtime_templates import render_runtime_anthropic_swift
    src = render_runtime_anthropic_swift()
    # Must check for error object with message before attempting content access.
    assert 'json["error"] as? [String: Any]' in src
    assert '"Anthropic API error: ' in src
    # The content access is still required, as a fallback after error check.
    assert 'json["content"] as? [[String: Any]]' in src


def test_fix_d_cache_store_overwrite_guard(tmp_path: Path) -> None:
    """Emitted Cache.swift contains the fileExists/removeItem guard before
    moveItem so that a refreshed cache entry after TTL expiry always wins."""
    from clio.emitters._swift_runtime_templates import render_runtime_cache_swift
    src = render_runtime_cache_swift()
    # The guard must appear before the moveItem call.
    exists_pos = src.index("fileExists(atPath: final.path)")
    remove_pos = src.index("removeItem(at: final)")
    move_pos = src.index("moveItem(at: tmp, to: final)")
    assert exists_pos < remove_pos < move_pos, (
        "fileExists guard must precede removeItem, which must precede moveItem"
    )


def test_fix_g_validate_swift_bool_branch(tmp_path: Path) -> None:
    """Emitted Validate.swift contains an explicit Bool branch at the top of
    cmpOk() before the toDouble path, for Linux cross-platform compatibility."""
    from clio.emitters._swift_runtime_templates import render_runtime_validate_swift
    src = render_runtime_validate_swift()
    # The Bool branch must appear before the toDouble branch in cmpOk.
    bool_pos = src.index("let lb = l as? Bool, let rb = r as? Bool")
    double_pos = src.index("if let lf = toDouble(l), let rf = toDouble(r)")
    assert bool_pos < double_pos, (
        "Bool branch must precede toDouble branch in cmpOk"
    )


def test_fix_i_onfail_abort_only_emits_throw(tmp_path: Path) -> None:
    """ON_FAIL: abort("boom") without retry must emit the throw statement.

    A chain without retry(N) still applies: has_on_fail keys off
    step.on_fail being present, and attempts = max(1, retry_count) = 1, so the
    loop runs once and the abort fires post-loop. Without this the abort would
    be silently dropped and the simple no-ON_FAIL path would run instead."""
    out = tmp_path / "out"
    rc = _compile(FIXTURES / "swift_onfail_abort_only.clio", out)
    assert rc == 0
    step_src = (out / "Sources/ClioFlow/Steps/Step01_detect.swift").read_text()
    # The custom abort message must appear.
    assert 'throw AnthropicError(message: "boom")' in step_src, (
        "abort-only ON_FAIL must emit custom throw"
    )
    # The loop must run exactly once (attempts=1).
    assert "for attempt in 0..<1" in step_src, (
        "abort-only ON_FAIL must emit loop with 1 attempt"
    )
    # The simple no-ON_FAIL path must not appear (no plain Anthropic.complete outside a loop).
    lines = step_src.splitlines()
    for line in lines:
        if "for attempt in" in line:
            break
    else:
        raise AssertionError("no retry loop found in emitted step")


def test_fix_i_existing_onfail_unaffected(tmp_path: Path) -> None:
    """Regression guard: an existing ON_FAIL with retry(2) + fallback + abort
    still emits the correct loop bound (0..<2, not 0..<1)."""
    out = tmp_path / "out"
    rc = _compile(FIXTURES / "swift_judgment_onfail.clio", out)
    assert rc == 0
    step_src = (out / "Sources/ClioFlow/Steps/Step01_detect.swift").read_text()
    assert "for attempt in 0..<2" in step_src, "retry(2) must emit 0..<2 loop bound"
    assert "step_naive(" in step_src, "fallback step must still be emitted"
    assert "detection exhausted" in step_src, "abort message must still be emitted"
