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
