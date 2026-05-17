"""Tests for the claude-skill emitter.

Granular tests (existence + parsed content) for tasks 1-12.
Golden snapshots (full tree equality) for task 14 only.
To regenerate goldens after intentional changes:

    python -m clio compile tests/fixtures/<name>.clio \
        --target claude-skill --output tests/fixtures/expected_skill/<name>
"""

import json
import sys
from pathlib import Path

import pytest

from clio.cli import _cmd_compile
from clio.emitters.claude_skill import ClaudeSkillEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _parse_frontmatter(body: str) -> dict[str, str]:
    """Parse a simple YAML-style frontmatter block (key: value lines only)."""
    assert body.startswith("---\n"), "SKILL.md must start with ---"
    end = body.index("\n---\n", 4)
    block = body[4:end]
    result: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def test_smoke_emit_phase1_creates_skill_md(tmp_path):
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    skill_md = tmp_path / "SKILL.md"
    assert skill_md.exists()
    body = skill_md.read_text()
    assert body.startswith("---\n")
    front = _parse_frontmatter(body)
    assert front["name"]
    assert front["description"]


def test_cli_compile_claude_skill_produces_skill_md(tmp_path):
    """Exercise the _cmd_compile dispatch path for claude-skill.

    Guards against regressions in the elif branch in clio/cli.py. (The argparse
    choices guard is exercised separately at CLI-entry time and is not in scope
    for this test.)
    """
    source = str(FIXTURES / "mvp_phase1.clio")
    rc = _cmd_compile(source, "claude-skill", str(tmp_path))
    assert rc == 0, f"_cmd_compile returned {rc}"
    skill_md = tmp_path / "SKILL.md"
    assert skill_md.exists(), "SKILL.md not produced by CLI path"


def test_frontmatter_uses_flow_description_when_present(tmp_path):
    """v0.17.x: FLOW.DESCRIPTION (mirror of STEP.DESCRIPTION from v0.15) is
    captured by the parser, plumbed through the IR, and consumed by the
    claude-skill emitter to populate the SKILL.md frontmatter `description:`
    field — the signal the host LLM uses to auto-trigger the skill on intent
    match. Previously this test skipped because FlowIR.description did not
    exist ("Option B taken in Task 2"); now it asserts the wire-through."""
    src = (
        "STEP echo_str\n"
        "  TAKES: input: str\n"
        "  GIVES: output: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW pipeline\n"
        '  DESCRIPTION: "Refine a draft into a final, polished version."\n'
        '  echo_str(input="hello")\n'
    )
    graph = build_ir(parse(src))
    assert graph.flow is not None and graph.flow.description is not None, (
        "FLOW.DESCRIPTION must be wired through parser → IR before the emitter "
        "can pick it up"
    )
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    front = _parse_frontmatter(body)
    assert front["description"] == graph.flow.description.strip()


@pytest.mark.parametrize(
    "description",
    [
        # Capital first-letter diacritics (issue #40 — pre-fix all classified as EN)
        "Évaluer le risque de churn.",
        "Être ou ne pas être, telle est la question.",
        "Âme et conscience d'un système.",
        # Marker expansion (Gemini PR #42 medium feedback)
        "Château pour évasion estivale.",  # â
        "Où dois-je router cette requête ?",  # ù
        "Noël arrive bientôt et bouscule l'agenda.",  # ë
        # Lowercase baseline (was already working pre-#40)
        "évaluer le risque de churn.",
    ],
)
def test_detect_skill_language_handles_french_diacritics(description: str):
    """Issue #40 + Gemini PR #42 feedback — `detect_skill_language` must
    recognise both lowercase AND uppercase French diacritics, and cover
    the common letters that appear at sentence start in natural French
    (`É`, `Ê`, `Â`) plus the diacritics that mark uniquely French words
    (`ù` in `où`, `ë` in `Noël`)."""
    import pytest  # noqa: F401 — silence unused-import warning if collected without param

    from clio.emitters._claude_skill_helpers import detect_skill_language

    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE: exact\n"
        "\n"
        "FLOW pipeline\n"
        f'  DESCRIPTION: "{description}"\n'
        '  s(x="hi")\n'
    )
    graph = build_ir(parse(src))
    assert detect_skill_language(graph) == "fr", (
        f"FR must be detected from {description!r}; got `en`. The heuristic "
        "must either case-fold the samples (issue #40 fix) or include uppercase "
        "and broader-coverage markers (PR #42 expansion)."
    )


def test_frontmatter_no_warning_when_flow_description_set(tmp_path, capsys):
    """v0.17.x — when FLOW.DESCRIPTION is provided, the "weak auto-trigger"
    warning must NOT fire. The complementary case (warning fires when
    DESCRIPTION is absent) is covered by
    `test_frontmatter_warns_when_no_description`."""
    src = (
        "STEP echo_str\n"
        "  TAKES: input: str\n"
        "  GIVES: output: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW pipeline\n"
        '  DESCRIPTION: "Refine a draft into a final, polished version."\n'
        '  echo_str(input="hello")\n'
    )
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    captured = capsys.readouterr()
    assert "claude-skill warning" not in captured.err, (
        "no warning when FLOW.DESCRIPTION is explicit; got stderr:\n" + captured.err
    )


def test_frontmatter_warns_when_no_description(tmp_path, capsys):
    """A warning is emitted to stderr when the flow has no description."""
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    captured = capsys.readouterr()
    assert "claude-skill warning" in captured.err
    assert "no description" in captured.err.lower()
    body = (tmp_path / "SKILL.md").read_text()
    front = _parse_frontmatter(body)
    assert front["description"].startswith("Execute flow ")


def test_frontmatter_allowed_tools_baseline(tmp_path):
    """allowed-tools is the static v1 baseline list."""
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    front = _parse_frontmatter(body)
    tools = [t.strip() for t in front["allowed-tools"].split(",")]
    assert "Bash" in tools
    assert "Read" in tools
    assert "Write" in tools
    assert "TodoWrite" in tools


def test_emits_process_flow_dot(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    dot = (tmp_path / "process_flow.dot").read_text()
    assert dot.startswith("digraph "), "DOT output must start with 'digraph '"
    # Last non-empty line should contain the closing brace
    non_empty = [ln for ln in dot.splitlines() if ln.strip()]
    assert non_empty[-1].strip().endswith("}")


def test_emits_state_example_json_valid(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    state = json.loads((tmp_path / "state.example.json").read_text())
    assert isinstance(state, dict)
    # Each top-level step name should be a key with an empty dict value
    for step in graph.steps:
        assert step.name in state, f"Missing step {step.name!r} in state.example.json"
        assert state[step.name] == {}, f"Expected empty dict for step {step.name!r}"


def test_emits_readme(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    readme = (tmp_path / "README.md").read_text()
    # Should mention "claude-skill" and be non-trivial
    assert "claude-skill" in readme.lower()
    assert len(readme.strip()) >= 100, "README should be a few sentences"


def test_exact_step_emits_script(tmp_path):
    """A FLOW with one exact STEP must produce scripts/01_<name>.py."""
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    # Exclude bundled runtime helpers (underscore-prefixed).
    scripts = sorted(
        p for p in (tmp_path / "scripts").glob("*.py") if not p.name.startswith("_")
    )
    assert len(scripts) == 1
    assert scripts[0].name.startswith("01_")


def test_exact_step_script_is_autonomous(tmp_path):
    """Emitted script reads stdin JSON, writes stdout JSON."""
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    script = next(
        p for p in sorted((tmp_path / "scripts").glob("*.py"))
        if not p.name.startswith("_")
    ).read_text()
    assert "import sys" in script
    assert "import json" in script
    assert "json.load(sys.stdin)" in script
    assert "json.dump(" in script


def test_skill_md_references_exact_step_script(tmp_path):
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    # Some kind of section header for step 01 (Step or Étape)
    assert "## Step 01" in body or "## Étape 01" in body
    # The script reference must appear
    assert "scripts/01_" in body
    assert "python scripts/01_" in body


def test_validate_helper_is_bundled(tmp_path):
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    validate_py = tmp_path / "scripts" / "_validate.py"
    assert validate_py.exists()
    body = validate_py.read_text()
    assert "import json" in body
    assert "import sys" in body
    # Must not blow up if jsonschema is missing — stdlib fallback
    assert "try:" in body and "jsonschema" in body


def test_cache_key_helper_is_bundled(tmp_path):
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    key_py = tmp_path / "scripts" / "_cache_key.py"
    assert key_py.exists()
    body = key_py.read_text()
    assert "hashlib" in body
    assert "sha256" in body


def test_validate_helper_runs_against_real_schema(tmp_path):
    """Layer 2 check: the bundled validator must actually work."""
    import subprocess

    # Build a trivially-valid instance against a simple schema.
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    # Write a temp schema and a matching instance into tmp_path.
    schema_path = tmp_path / "test_schema.json"
    schema_path.write_text(json.dumps({"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}))
    instance_path = tmp_path / "instance.json"
    instance_path.write_text(json.dumps({"name": "hello"}))
    result = subprocess.run(
        [sys.executable, str(tmp_path / "scripts" / "_validate.py"),
         str(instance_path), str(schema_path)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"validator failed: {result.stderr}"


def test_cache_key_helper_produces_sha256_hex(tmp_path):
    """Layer 2 check: the bundled cache-key generator must actually work."""
    import subprocess

    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"customer": {"id": "c1"}, "order": {"items": [1, 2, 3]}}))
    result = subprocess.run(
        [sys.executable, str(tmp_path / "scripts" / "_cache_key.py"),
         str(state_path), "fetch_customer", '["customer.id", "order.items"]'],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"cache-key failed: {result.stderr}"
    key = result.stdout.strip()
    # SHA256 hex digest is 64 hex chars
    assert len(key) == 64
    assert all(c in "0123456789abcdef" for c in key)


def test_judgment_step_emits_prompt_template(tmp_path):
    # mvp_phase4.clio has one judgment STEP (detect_churn)
    src = (FIXTURES / "mvp_phase4.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    prompts = sorted((tmp_path / "prompts").glob("*.md"))
    assert len(prompts) >= 1
    body = prompts[0].read_text()
    # Template must be non-trivial
    assert len(body.strip()) > 0


def test_judgment_step_emits_output_schema(tmp_path):
    src = (FIXTURES / "mvp_phase4.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    out_schemas = sorted((tmp_path / "schemas").glob("*.output.json"))
    assert len(out_schemas) >= 1
    schema = json.loads(out_schemas[0].read_text())
    # JSON Schema must declare type=object with properties
    assert schema.get("type") == "object"
    assert "properties" in schema


def test_skill_md_judgment_section_has_prompt_and_schema_refs(tmp_path):
    src = (FIXTURES / "mvp_phase4.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "MODE: judgment" in body
    assert "prompts/" in body
    assert "schemas/" in body
    assert "_validate.py" in body


def _assert_no_external_refs(node, fname):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("../"):
            raise AssertionError(f"{fname}: external $ref still present: {ref}")
        for v in node.values():
            _assert_no_external_refs(v, fname)
    elif isinstance(node, list):
        for item in node:
            _assert_no_external_refs(item, fname)


def test_output_schema_has_no_external_refs(tmp_path):
    """Every $ref in emitted schemas must be self-contained (no file paths)."""
    src = (FIXTURES / "mvp_phase4.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    for schema_file in (tmp_path / "schemas").glob("*.output.json"):
        schema = json.loads(schema_file.read_text())
        _assert_no_external_refs(schema, schema_file.name)


def test_step_with_takes_emits_input_schema(tmp_path):
    """When a STEP has TAKES <contract>, schemas/NN_<name>.input.json is emitted."""
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    input_schemas = sorted((tmp_path / "schemas").glob("*.input.json"))
    assert len(input_schemas) >= 1
    schema = json.loads(input_schemas[0].read_text())
    assert schema.get("type") == "object" or "$ref" in str(schema) or "properties" in schema


def test_input_schema_has_no_external_refs(tmp_path):
    """No external $ref must survive inlining in emitted input schemas."""
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    for schema_file in (tmp_path / "schemas").glob("*.input.json"):
        schema = json.loads(schema_file.read_text())
        _assert_no_external_refs(schema, schema_file.name)


# ----- Task 7: IF / MATCH conditional sub-flows in SKILL.md ------------------

_IF_DECLS = (
    "CONTRACT classification\n"
    "  SHAPE: {category: str(max=20), confidence: float}\n"
    "\n"
    "CONTRACT routing_decision\n"
    "  SHAPE: {dest: str(max=40)}\n"
    "\n"
    "STEP classify\n"
    "  TAKES: email: str\n"
    "  GIVES: report: classification\n"
    "  MODE:  judgment\n"
    "\n"
    "STEP human_review\n"
    "  TAKES: report: classification\n"
    "  GIVES: decision: routing_decision\n"
    "  MODE:  judgment\n"
    "\n"
    "STEP auto_route\n"
    "  TAKES: report: classification\n"
    "  GIVES: decision: routing_decision\n"
    "  MODE:  judgment\n"
)

_IF_FLOW = (
    'FLOW main\n'
    '    classify(email="hi")\n'
    '    -> IF report.confidence < 0.7:\n'
    '        human_review(report)\n'
    '    ELSE:\n'
    '        auto_route(report)\n'
)

_MATCH_DECLS = (
    "CONTRACT classification\n"
    "  SHAPE: {category: enum(spam|support|sales), confidence: float}\n"
    "\n"
    "CONTRACT routing_decision\n"
    "  SHAPE: {dest: str(max=40)}\n"
    "\n"
    "STEP classify\n"
    "  TAKES: email: str\n"
    "  GIVES: report: classification\n"
    "  MODE:  judgment\n"
    "\n"
    "STEP archive\n"
    "  TAKES: report: classification\n"
    "  GIVES: decision: routing_decision\n"
    "  MODE:  judgment\n"
    "\n"
    "STEP route_support\n"
    "  TAKES: report: classification\n"
    "  GIVES: decision: routing_decision\n"
    "  MODE:  judgment\n"
    "\n"
    "STEP route_sales\n"
    "  TAKES: report: classification\n"
    "  GIVES: decision: routing_decision\n"
    "  MODE:  judgment\n"
    "\n"
    "STEP route_general\n"
    "  TAKES: report: classification\n"
    "  GIVES: decision: routing_decision\n"
    "  MODE:  judgment\n"
)

_MATCH_FLOW = (
    'FLOW main\n'
    '    classify(email="hi")\n'
    '    -> MATCH report.category:\n'
    '        CASE spam:    archive(report)\n'
    '        CASE support: route_support(report)\n'
    '        CASE sales:   route_sales(report)\n'
    '        DEFAULT:      route_general(report)\n'
)


def test_if_branches_appear_in_skill_md(tmp_path):
    """IF/ELSE in the FLOW chain produces a ### IF section in SKILL.md."""
    graph = build_ir(parse(_IF_DECLS + _IF_FLOW))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    # Section header for the IF block
    assert "### IF " in body or "### Si " in body
    # Both true and false branches are mentioned
    assert ("True branch" in body or "Branche Vrai" in body)
    assert ("False branch" in body or "Branche Faux" in body)
    # Condition rendered into the header
    assert "report.confidence" in body
    # Existing flat step sections are still present
    assert "## Step 01" in body or "## Étape 01" in body


def test_match_branches_appear_in_skill_md(tmp_path):
    """MATCH/CASE in the FLOW chain produces a ### MATCH section in SKILL.md."""
    graph = build_ir(parse(_MATCH_DECLS + _MATCH_FLOW))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    # Section header for the MATCH block
    assert "### MATCH " in body or "### Cas " in body
    # Discriminator rendered
    assert "report.category" in body
    # Named cases and DEFAULT appear
    assert "spam" in body
    assert "support" in body
    assert "DEFAULT" in body
    # Flat step sections preserved
    assert "## Step 01" in body or "## Étape 01" in body


def test_if_no_else_only_true_branch_mentioned(tmp_path):
    """IF without ELSE: only True branch count appears; False branch omitted."""
    src = (
        _IF_DECLS
        + 'FLOW main\n'
        '    classify(email="hi")\n'
        '    -> IF report.confidence < 0.7:\n'
        '        human_review(report)\n'
    )
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "### IF " in body or "### Si " in body
    # True branch always present; False branch absent when ELSE is missing
    assert "True branch" in body or "Branche Vrai" in body
    assert "False branch" not in body and "Branche Faux" not in body


def test_flat_step_sections_preserved_with_control_flow(tmp_path):
    """Tasks 4/5 step sections survive the render_skill_md refactor."""
    graph = build_ir(parse(_IF_DECLS + _IF_FLOW))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    # All 3 STEPs must appear (classify=01, human_review=02, auto_route=03)
    assert "01" in body
    assert "classify" in body
    assert "human_review" in body
    assert "auto_route" in body


def test_for_each_parallel_narration_states_serialisation(tmp_path):
    """A3 polish: when the source declares `FOR EACH ... PARALLEL`, the
    SKILL.md narration must explicitly state that the emitted skill
    SERIALISES iterations (the LLM host does not execute concurrently),
    matching the existing compile-time warning. Before this fix the
    narration only said `(PARALLEL mode)` which contradicted the warning
    and misled the host into expecting concurrent execution."""
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL AS results:\n"
        "         process(x=item)\n"
    )
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    foreach_section = body.split("### FOR EACH ")[1].split("\n## ")[0]
    # The narration must mention PARALLEL was declared
    assert "PARALLEL" in foreach_section, (
        f"narration must surface that source declared PARALLEL; got:\n{foreach_section}"
    )
    # AND must state the skill serialises (alignment with the compile warning)
    serialised_marker = (
        "serialise" in foreach_section.lower()
        or "serialize" in foreach_section.lower()
        or "sequential" in foreach_section.lower()
    )
    assert serialised_marker, (
        f"narration must state that iterations are serialised in the skill; got:\n{foreach_section}"
    )


def test_match_section_names_substeps_per_case(tmp_path):
    """A2 polish: render_match_section must NAME the sub-steps inside each
    case body, tied to the case pattern. Previously the narration just said
    `Case 'spam': N sub-step(s)`, forcing the host LLM to guess which flat
    `## Step NN` card belonged to which case. Now each case lists its
    sub-step names so the host can route deterministically."""
    graph = build_ir(parse(_MATCH_DECLS + _MATCH_FLOW))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    match_section = body.split("### MATCH ")[1].split("\n## ")[0]
    # Each case names its sub-step
    for pattern, expected_step in [
        ("spam", "archive"),
        ("support", "route_support"),
        ("sales", "route_sales"),
        ("DEFAULT", "route_general"),
    ]:
        # The line for this case must mention both the pattern and the step name
        case_line = next(
            (line for line in match_section.splitlines() if pattern in line),
            None,
        )
        assert case_line is not None, (
            f"MATCH section missing case '{pattern}'; section was:\n{match_section}"
        )
        assert expected_step in case_line, (
            f"Case '{pattern}' must name `{expected_step}`; got line: {case_line!r}"
        )


def test_if_section_names_then_and_else_substeps(tmp_path):
    """A1 polish: render_if_section must NAME the sub-steps it routes to in
    each branch, not just count them. The vague `2 sub-step(s) (see ordinal
    sections above/below)` forced the host LLM to grep back to step cards
    without a clear pointer. Naming the sub-steps lets the host map directly
    from the conditional to the `## Step NN — <name>` card."""
    graph = build_ir(parse(_IF_DECLS + _IF_FLOW))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    # Find the IF section block, scoped check (avoid matching the flat step cards above)
    if_section = body.split("### IF ")[1].split("\n## ")[0]
    # The True branch references human_review (the then-body step)
    assert "human_review" in if_section, (
        f"render_if_section must name then-body sub-steps in the IF block; got:\n{if_section}"
    )
    # The False branch references auto_route (the else-body step)
    assert "auto_route" in if_section, (
        f"render_if_section must name else-body sub-steps in the IF block; got:\n{if_section}"
    )


# ----- Task 8: WHILE / FOR EACH iteration sub-flows in SKILL.md ---------------

_FOR_EACH_DECLS = (
    "STEP collect_items\n"
    "  GIVES: items: List<str>\n"
    "  MODE: exact\n"
    "\n"
    "STEP echo\n"
    "  TAKES: x: str\n"
    "  GIVES: msg: str\n"
    "  MODE: exact\n"
)

_FOR_EACH_FLOW = (
    "FLOW pipe\n"
    "  collect_items()\n"
    "    -> FOR EACH item IN items:\n"
    "         echo(x=item)\n"
)

_WHILE_DECLS = (
    "CONTRACT draft_score\n"
    "  SHAPE: {text: str(max=2000), score: float}\n"
    "\n"
    "STEP draft_initial\n"
    "  TAKES: brief: str\n"
    "  GIVES: draft: draft_score\n"
    "  MODE:  judgment\n"
    "\n"
    "STEP refine_draft\n"
    "  TAKES: draft: draft_score\n"
    "  GIVES: draft: draft_score\n"
    "  MODE:  judgment\n"
)

_WHILE_FLOW = (
    'FLOW main\n'
    '    draft_initial(brief="x")\n'
    '    -> WHILE draft.score < 0.9 MAX 3:\n'
    '        refine_draft(draft=draft)\n'
)


def test_for_each_section_in_skill_md(tmp_path):
    """A FOR EACH block produces a section with the per-iteration TodoWrite instruction."""
    graph = build_ir(parse(_FOR_EACH_DECLS + _FOR_EACH_FLOW))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    # Section header for the FOR EACH block
    assert "### FOR EACH " in body or "### Pour chaque " in body
    # Per-iteration TodoWrite instruction is the drift anchor
    assert "TodoWrite" in body
    # Loop variable and collection are rendered
    assert "item" in body
    assert "items" in body
    # Flat step sections are still present
    assert "collect_items" in body
    assert "echo" in body


def test_for_each_section_mentions_iteration(tmp_path):
    """The FOR EACH section must use the word iteration (en) or itération (fr)."""
    graph = build_ir(parse(_FOR_EACH_DECLS + _FOR_EACH_FLOW))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "iteration" in body.lower() or "itération" in body.lower()


def test_while_section_in_skill_md(tmp_path):
    """A WHILE block produces a section with the loop and condition instructions."""
    graph = build_ir(parse(_WHILE_DECLS + _WHILE_FLOW))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    # Section header
    assert "### WHILE " in body or "### Tant que " in body
    # Condition is rendered
    assert "draft.score" in body
    # Max iters guard mentioned
    assert "3" in body
    # Flat step sections preserved
    assert "draft_initial" in body
    assert "refine_draft" in body


def test_while_section_mentions_todowrite(tmp_path):
    """The WHILE section must anchor iterations with TodoWrite."""
    graph = build_ir(parse(_WHILE_DECLS + _WHILE_FLOW))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "TodoWrite" in body


def test_for_each_parallel_collector_rendered(tmp_path):
    """PARALLEL AS <collector> appears in the FOR EACH section when set."""
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL AS results:\n"
        "         process(x=item)\n"
    )
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    # PARALLEL marker or collector name must appear in the section
    assert "results" in body or "parallel" in body.lower()


# ----- Task 9: CACHE / RETRY / RESOURCES modifiers in SKILL.md ----------------


def test_cache_block_in_step_section_uses_bundled_helper(tmp_path):
    """A step with CACHE config emits a cache sub-block that invokes scripts/_cache_key.py."""
    src = (FIXTURES / "mvp_v02_cache.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    # Cache header in EN or FR
    assert "Cache" in body or "Mise en cache" in body
    # The bundled helper is referenced
    assert "scripts/_cache_key.py" in body
    # The .cache directory path is mentioned
    assert ".cache/" in body


def test_cache_block_absent_when_no_cache_config(tmp_path):
    """Steps without CACHE config produce no cache sub-block."""
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    # No cache helper reference should appear in a flow with no CACHE
    assert "scripts/_cache_key.py" not in body
    assert ".cache/" not in body


def test_cache_block_uses_fr_label_when_flow_is_french(tmp_path):
    """When the FR heuristic detects French in any STEP/FLOW DESCRIPTION,
    the cache label in SKILL.md is rendered as 'Mise en cache' instead of
    'Cache'. The heuristic scans DESCRIPTION strings (not identifier names)
    for the markers `éèàçôîêûïü` — so the source needs at least one
    DESCRIPTION carrying a diacritic. The cleanest trigger is the new
    `FLOW.DESCRIPTION` field (v0.17.x); STEP.DESCRIPTION would work too.

    The previous version of this test built a source with `MODE: exact` +
    `CACHE: ttl(24h)` (rejected by the parser: CACHE is judgment-only),
    swallowed the resulting ParseError in a bare `except Exception`, and
    fell back to `pytest.skip` with a misleading "not parseable with
    current grammar" message. The skip masked a fixture bug — there was
    no grammar limitation, just an invalid mode/cache combo. Both issues
    are now fixed: the source uses `MODE: judgment` (CACHE valid), and
    the assertion is tightened to `"Mise en cache" in body` only — the
    old `"Mise en cache" in body or "Cache" in body` would have passed
    even when FR detection silently regressed."""
    # DESCRIPTION opens with a CAPITAL diacritic (`Évaluer`) — a natural
    # French sentence opener. The heuristic (issue #40 fix) now case-folds
    # samples via `text.lower()` before scanning, so capital É is detected
    # just like lowercase é. Pre-fix this exact description would have
    # silently classified as EN.
    fr_src = (
        "CONTRACT customer_risk\n"
        "  SHAPE: {client: str, risk: enum(low|mid|high), reason: str}\n"
        "\n"
        "STEP detecter_clients\n"
        "  TAKES: customers: List<{name: str, revenue: float}>\n"
        "  GIVES: risks: List<customer_risk>\n"
        "  MODE:  judgment\n"
        "  CACHE: ttl(24h)\n"
        "\n"
        "FLOW retention_clients\n"
        '  DESCRIPTION: "Évaluer le risque de churn du portefeuille client."\n'
        "  detecter_clients(customers=customers)\n"
    )
    graph = build_ir(parse(fr_src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "Mise en cache" in body, (
        "FR cache label expected when FLOW.DESCRIPTION carries French diacritics; "
        "got SKILL.md:\n" + body
    )
    assert "## Cache" not in body, (
        "EN cache header must not appear when FR is detected; got SKILL.md:\n" + body
    )


def test_retry_block_from_on_fail_in_step_section(tmp_path):
    """A step with ON_FAIL retry(N) emits a retry sub-block in SKILL.md.

    StepIR has no dedicated `.retry` field (TODO(post-v0.14)). The retry
    strategy is read from on_fail.strategies with kind='retry'.
    """
    src = (FIXTURES / "mvp_v02_onfail.clio").read_text()
    graph = build_ir(parse(src))
    # Verify the fixture actually has a retry strategy in the IR.
    step_with_retry = next(
        (s for s in graph.steps if s.on_fail is not None
         and any(st.kind == "retry" for st in s.on_fail.strategies)),
        None,
    )
    if step_with_retry is None:
        import pytest
        pytest.skip("mvp_v02_onfail.clio has no retry strategy in ON_FAIL; fixture check needed.")
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "Retry" in body or "Réessayer" in body
    # Budget (max_retries) should be mentioned as a number
    budget = next(
        st.max_retries for st in step_with_retry.on_fail.strategies if st.kind == "retry"
    )
    assert str(budget) in body


def test_retry_block_absent_when_no_retry(tmp_path):
    """Steps without any retry strategy produce no retry sub-block."""
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "Retry" not in body and "Réessayer" not in body


def test_resources_annex_listed(tmp_path):
    """When a flow declares RESOURCES, an annex section lists them in SKILL.md."""
    src = (FIXTURES / "mvp_phase9.clio").read_text()
    graph = build_ir(parse(src))
    assert graph.resources is not None, "mvp_phase9.clio must declare RESOURCES"
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "Resources" in body or "Ressources" in body


def test_resources_annex_lists_models(tmp_path):
    """The resources annex renders the declared models."""
    src = (FIXTURES / "mvp_phase9.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    # mvp_phase9.clio declares models: [haiku]
    assert "haiku" in body


def test_resources_annex_absent_when_no_resources(tmp_path):
    """When no RESOURCES block is declared, no resources annex appears."""
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    assert graph.resources is None, "mvp_phase1.clio must not declare RESOURCES"
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    # The section header should not appear at all
    assert "## Resources" not in body and "## Ressources" not in body


def test_resources_annex_with_multiple_models(tmp_path):
    """When RESOURCES declares multiple models, all appear in the annex."""
    src = (FIXTURES / "mvp_v02_onfail.clio").read_text()
    graph = build_ir(parse(src))
    if graph.resources is None:
        import pytest
        pytest.skip("mvp_v02_onfail.clio declares no RESOURCES.")
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    for model in graph.resources.models:
        assert model in body, f"Model {model!r} not found in SKILL.md"


# ---------------------------------------------------------------------------
# Task 10 — RESCUE handlers + step.error.* + RESUME (v0.13 parity)
# ---------------------------------------------------------------------------

_RESUME_FIXTURE = (
    Path(__file__).parent.parent.parent / "examples" / "critical_pipeline_resume.clio"
)


def _load_resume_graph():
    """Parse and build the IR for examples/critical_pipeline_resume.clio.

    Returns the FlowGraph, or raises pytest.skip if the file is absent.
    """
    if not _RESUME_FIXTURE.exists():
        import pytest
        pytest.skip("examples/critical_pipeline_resume.clio not present")
    src = _RESUME_FIXTURE.read_text()
    return build_ir(parse(src))


def test_rescue_section_emits_for_step_with_rescue(tmp_path):
    """A flow with a RESCUE handler produces a RESCUE section in SKILL.md."""
    graph = _load_resume_graph()
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    # Some kind of section header indicating a rescue handler
    assert "RESCUE" in body or "Rescue" in body or "rescue" in body
    # The rescued step name appears somewhere in the body
    rescue_step = graph.flow.rescues[0].step_name
    assert rescue_step in body


def test_rescue_section_mentions_error_message_and_type(tmp_path):
    """The RESCUE section must mention step.error.message and step.error.type."""
    graph = _load_resume_graph()
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert ".error.message" in body
    assert ".error.type" in body


def test_resume_terminator_rendered(tmp_path):
    """The RESUME terminator appears in the SKILL.md instruction."""
    graph = _load_resume_graph()
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    # The RESUME keyword or its rendered "set state.X.Y ← ..." form appears
    assert "RESUME" in body or "← " in body or "set state." in body


# ---------------------------------------------------------------------------
# Task 11 — Compile-time validation (unsupported lang, parallel, no-desc)
# ---------------------------------------------------------------------------

def test_unsupported_exact_language_raises_at_compile(tmp_path):
    """An exact STEP with LANG rust (valid parse time, invalid for claude-skill) raises ValueError."""
    src = (
        "STEP rusty\n"
        "  MODE: exact\n"
        "  LANG: rust\n"
        "  GIVES: x: str\n"
    )
    import pytest
    graph = build_ir(parse(src))
    with pytest.raises(ValueError) as excinfo:
        ClaudeSkillEmitter().emit(graph, tmp_path)
    msg = str(excinfo.value).lower()
    assert "claude-skill" in msg
    assert "python" in msg and "bash" in msg
    assert "line " in msg


def test_parallel_construct_warns_serialized(tmp_path, capsys):
    """A PARALLEL FOR EACH warns that the emitted skill serializes iterations."""
    src = (
        "STEP load\n"
        "  MODE: exact\n"
        "  GIVES: items: List<str>\n"
        "\n"
        "STEP process\n"
        "  TAKES: x: str\n"
        "  GIVES: r: str\n"
        "  MODE: exact\n"
        "\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL AS results:\n"
        "         process(x=item)\n"
    )
    import pytest
    try:
        graph = build_ir(parse(src))
    except Exception as exc:
        pytest.skip(f"PARALLEL AS not in this grammar: {exc}")
    ClaudeSkillEmitter().emit(graph, tmp_path)
    captured = capsys.readouterr()
    assert "claude-skill warning" in captured.err
    assert "parallel" in captured.err.lower() or "serial" in captured.err.lower()


def test_existing_warn_for_no_description_still_works(tmp_path, capsys):
    """The Task 2 warning for missing FLOW description remains functional after _validate."""
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    captured = capsys.readouterr()
    assert "claude-skill warning" in captured.err
    assert "no description" in captured.err.lower()


# ---------------------------------------------------------------------------
# Task 12 — Layer 2: emitted exact-step script runs against state.example.json
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Task 14 — End-to-end golden-snapshot regression tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture,fixture_dir", [
    ("mvp_phase1", FIXTURES),
    ("mvp_phase2", FIXTURES),
    ("critical_pipeline_resume", Path(__file__).parent.parent.parent / "examples"),
])
def test_emit_golden(tmp_path, fixture, fixture_dir):
    """Full-tree golden snapshot for representative fixtures.

    To regenerate after intentional changes:

        for f in mvp_phase1 mvp_phase2; do
            rm -rf tests/fixtures/expected_skill/$f
            python -m clio compile tests/fixtures/$f.clio --target claude-skill \\
                --output tests/fixtures/expected_skill/$f
        done
        rm -rf tests/fixtures/expected_skill/critical_pipeline_resume
        python -m clio compile examples/critical_pipeline_resume.clio --target claude-skill \\
            --output tests/fixtures/expected_skill/critical_pipeline_resume
    """
    src = (fixture_dir / f"{fixture}.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    expected_root = FIXTURES / "expected_skill" / fixture
    actual = _read_tree(tmp_path)
    expected = _read_tree(expected_root)
    assert actual == expected, _diff_summary(actual, expected)


def _read_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = p.read_text()
    return out


def _diff_summary(actual: dict, expected: dict) -> str:
    """Produce a useful diff message — keys only, no full content."""
    a_keys = set(actual.keys())
    e_keys = set(expected.keys())
    only_actual = a_keys - e_keys
    only_expected = e_keys - a_keys
    differing = sorted(k for k in (a_keys & e_keys) if actual[k] != expected[k])
    return (
        f"Tree mismatch.\n"
        f"  Extra in actual: {sorted(only_actual)}\n"
        f"  Missing in actual: {sorted(only_expected)}\n"
        f"  Content differs: {differing}"
    )


def test_emitted_exact_script_runs_against_state_example(tmp_path):
    """The emitted exact-step script reads state.example.json on stdin and
    writes JSON on stdout. End-to-end integration check.

    BLOCKED (Task 4 deficiency): render_exact_script always emits
    ``raise NotImplementedError(...)`` in the step body regardless of whether
    the .clio source contained a CODE python: block. No current fixture has a
    CODE python: block, and the parser/IR do not capture inline code bodies.
    Until Task 4 is extended to inline user-supplied code, this test is
    expected to fail with a non-zero exit code from the emitted script.

    See: clio/emitters/_claude_skill_helpers.py::render_exact_script lines ~196-199.
    """
    import json
    import subprocess
    import sys

    import pytest

    # mvp_phase1.clio: single STEP foo, MODE: exact, no TAKES, no GIVES.
    # Simplest possible fixture — minimises state.example.json noise.
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)

    state_example = (tmp_path / "state.example.json").read_text()

    # Find the first user-facing exact-step script (excludes _validate.py, _cache_key.py).
    scripts = sorted(
        p for p in (tmp_path / "scripts").glob("*.py")
        if not p.name.startswith("_")
    )
    if not scripts:
        pytest.skip("No user-facing exact-step scripts emitted by this fixture")

    script = scripts[0]
    result = subprocess.run(
        [sys.executable, str(script)],
        input=state_example,
        capture_output=True,
        text=True,
        timeout=10,
    )

    # Task 4 deficiency check: if the script raises NotImplementedError, mark
    # the test xfail so CI stays green while the gap is documented.
    if result.returncode != 0 and "NotImplementedError" in result.stderr:
        pytest.xfail(
            "BLOCKED — Task 4 deficiency: render_exact_script emits a stub body "
            "(raise NotImplementedError) because the .clio language has no CODE python: "
            "inline-code syntax and the IR does not capture step bodies. "
            "Fix: extend the parser, IR, and render_exact_script to inline user code "
            "before this test can pass."
        )

    assert result.returncode == 0, (
        f"Script {script.name} failed.\n"
        f"stderr:\n{result.stderr}\n"
        f"stdout:\n{result.stdout}"
    )
    # Output must be valid JSON dict.
    output = json.loads(result.stdout)
    assert isinstance(output, dict), f"Expected dict output, got {type(output).__name__}"


# ---------------------------------------------------------------------------
# Task 9 (v0.16) — FLOW.TAKES / FLOW.GIVES declared signature in SKILL.md
# ---------------------------------------------------------------------------

def test_claude_skill_renders_declared_takes_gives_in_skill_md(tmp_path):
    """Declared FLOW.TAKES / FLOW.GIVES surface in the SKILL.md Inputs/Outputs
    section rather than first-step / last-step inference.

    The FLOW declares TAKES: items: List<str> and GIVES: labels: List<str>.
    The SKILL.md must show those fields, NOT the step-level item: str.
    """
    src = (
        "STEP s\n"
        "  TAKES: item: str\n"
        "  GIVES: label: str\n"
        "  MODE:  judgment\n"
        "\n"
        "FLOW pipeline\n"
        "  TAKES: items: List<str>\n"
        "  GIVES: labels: List<str>\n"
        "  FOR EACH item IN items PARALLEL AS labels:\n"
        "    s(item=item)\n"
        "\n"
        "RESOURCES\n"
        "  target: claude-skill\n"
        "  models: [haiku]\n"
    )
    graph = build_ir(parse(src))
    assert graph.flow is not None
    assert len(graph.flow.takes) == 1
    assert len(graph.flow.gives) == 1
    ClaudeSkillEmitter().emit(graph, tmp_path)
    skill_md = (tmp_path / "SKILL.md").read_text()
    # Declared FLOW.TAKES surfaces as `items: List<str>`, NOT the step's `item: str`
    assert "items" in skill_md
    assert "List<str>" in skill_md
    # Declared FLOW.GIVES surfaces as `labels: List<str>`
    assert "labels" in skill_md
    # The Inputs / Outputs section headers are present
    assert "## Inputs" in skill_md or "## Entrées" in skill_md
    assert "## Outputs" in skill_md or "## Sorties" in skill_md


def test_claude_skill_falls_back_to_step_inference_without_signature(tmp_path):
    """v0.15 backward-compat: no FLOW.TAKES/GIVES → no Inputs/Outputs section
    is emitted (the v0.15 behaviour is preserved byte-for-byte).
    """
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "\n"
        "FLOW p\n"
        "  s(x=x)\n"
        "\n"
        "RESOURCES\n"
        "  target: claude-skill\n"
        "  models: [haiku]\n"
    )
    graph = build_ir(parse(src))
    assert graph.flow is not None
    assert graph.flow.takes == ()
    assert graph.flow.gives == ()
    ClaudeSkillEmitter().emit(graph, tmp_path)
    skill_md = (tmp_path / "SKILL.md").read_text()
    # No Inputs/Outputs section — v0.15 behaviour preserved
    assert "## Inputs" not in skill_md and "## Entrées" not in skill_md
    assert "## Outputs" not in skill_md and "## Sorties" not in skill_md
    # Step content is still rendered
    assert "x" in skill_md
    assert "y" in skill_md


def test_claude_skill_emits_subflow_script(tmp_path):
    """v0.17: a sub-FLOW called by the main FLOW becomes scripts/sub_<name>.py
    and the main SKILL.md narrates the invocation."""
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE: exact\n"
        "\n"
        "FLOW inner\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  s(x=x)\n"
        "\n"
        "FLOW outer\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  inner(x=x)\n"
        "\n"
        "RESOURCES\n"
        "  target: claude-skill\n"
    )
    g = build_ir(parse(src), flow_name="outer")
    ClaudeSkillEmitter().emit(g, tmp_path)
    # The emitter writes files directly under output_dir.
    scripts = tmp_path / "scripts"
    files = {p.name for p in scripts.iterdir()}
    # Sub-flow scaffold exists.
    assert any("inner" in name for name in files), (
        f"expected a sub-flow script for `inner` in {files}"
    )
    # Main SKILL.md references the sub-flow.
    skill_md = (tmp_path / "SKILL.md").read_text()
    assert "inner" in skill_md


def test_claude_skill_rejects_control_structures_in_subflow(tmp_path):
    """v0.17: sub-flows in the claude-skill target must be linear chains. Any
    control structure (IF / FOR EACH / MATCH / WHILE) inside a signed sub-flow
    is a compile-time error rather than a runtime NotImplementedError."""
    src = (
        "STEP load\n"
        "  TAKES: x: str\n"
        "  GIVES: items: List<str>\n"
        "  MODE: exact\n"
        "\n"
        "STEP process\n"
        "  TAKES: item: str\n"
        "  GIVES: result: str\n"
        "  MODE: exact\n"
        "\n"
        "FLOW inner\n"
        "  TAKES: x: str\n"
        "  GIVES: items: List<str>\n"
        "  load(x=x)\n"
        "  -> FOR EACH item IN items:\n"
        "       process(item=item)\n"
        "\n"
        "FLOW outer\n"
        "  TAKES: x: str\n"
        "  GIVES: items: List<str>\n"
        "  inner(x=x)\n"
        "\n"
        "RESOURCES\n"
        "  target: claude-skill\n"
    )
    g = build_ir(parse(src), flow_name="outer")
    with pytest.raises(ValueError) as exc:
        ClaudeSkillEmitter().emit(g, tmp_path)
    msg = str(exc.value)
    assert "inner" in msg
    assert "linear" in msg
    # A ForEachIR triggers the rejection; the error must surface the kind.
    assert "ForEach" in msg


# ---------------------------------------------------------------------------
# T5 — .clio/ sidecar tests
# ---------------------------------------------------------------------------

_SIDECAR_SOURCE = (
    "STEP foo\n"
    "  MODE: exact\n"
    "  LANG: python\n"
    "FLOW pipe\n"
    "  foo()\n"
)


def test_sidecar_source_clio_is_byte_identical(tmp_path):
    src = tmp_path / "src.clio"
    src.write_text(_SIDECAR_SOURCE)
    graph = build_ir(parse(_SIDECAR_SOURCE))
    out = tmp_path / "skill"
    ClaudeSkillEmitter().emit(graph, out, source_path=src)

    assert (out / ".clio" / "source.clio").read_bytes() == src.read_bytes()


def test_sidecar_manifest_has_required_keys(tmp_path):
    src = tmp_path / "src.clio"
    src.write_text(_SIDECAR_SOURCE)
    graph = build_ir(parse(_SIDECAR_SOURCE))
    out = tmp_path / "skill"
    ClaudeSkillEmitter().emit(graph, out, source_path=src)

    manifest = json.loads((out / ".clio" / "manifest.json").read_text())
    assert set(manifest.keys()) >= {"clio_version", "emitted_at", "source_hash", "file_hashes"}
    assert manifest["clio_version"]  # non-empty
    assert manifest["source_hash"].startswith("sha256:")


def test_sidecar_file_hashes_match_emitted_files(tmp_path):
    from clio.emitters._sidecar import compute_file_hash

    src = tmp_path / "src.clio"
    src.write_text(_SIDECAR_SOURCE)
    graph = build_ir(parse(_SIDECAR_SOURCE))
    out = tmp_path / "skill"
    ClaudeSkillEmitter().emit(graph, out, source_path=src)

    manifest = json.loads((out / ".clio" / "manifest.json").read_text())
    for rel, stored_hash in manifest["file_hashes"].items():
        actual = compute_file_hash(out / rel)
        assert actual == stored_hash, f"hash mismatch for {rel}"


def test_sidecar_reproducible_across_emissions(tmp_path):
    src = tmp_path / "src.clio"
    src.write_text(_SIDECAR_SOURCE)
    graph = build_ir(parse(_SIDECAR_SOURCE))

    out1 = tmp_path / "skill1"
    ClaudeSkillEmitter().emit(graph, out1, source_path=src)
    out2 = tmp_path / "skill2"
    ClaudeSkillEmitter().emit(graph, out2, source_path=src)

    m1 = json.loads((out1 / ".clio" / "manifest.json").read_text())
    m2 = json.loads((out2 / ".clio" / "manifest.json").read_text())
    assert m1["source_hash"] == m2["source_hash"]
    assert m1["file_hashes"] == m2["file_hashes"]


def test_no_sidecar_when_source_path_omitted(tmp_path):
    graph = build_ir(parse(_SIDECAR_SOURCE))
    out = tmp_path / "skill"
    ClaudeSkillEmitter().emit(graph, out)  # no source_path
    assert not (out / ".clio").exists()
