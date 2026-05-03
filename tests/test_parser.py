import pytest
from clio.parser.parser import parse, ParseError


def test_parse_minimal_step():
    src = "STEP foo\n  MODE: exact\n"
    program = parse(src)
    assert len(program.decls) == 1
    step = program.decls[0]
    assert step.name == "foo"
    assert step.mode == "exact"
    assert step.line == 1


def test_parse_step_missing_mode_raises():
    src = "STEP foo\n"
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "MODE" in str(exc.value)


def test_parse_step_with_unknown_mode_raises():
    src = "STEP foo\n  MODE: bogus\n"
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "bogus" in str(exc.value)


def test_parse_step_with_takes_and_gives_primitives():
    src = (
        "STEP echo_str\n"
        "  TAKES: input: str\n"
        "  GIVES: output: str\n"
        "  MODE:  exact\n"
    )
    program = parse(src)
    step = program.decls[0]
    assert step.name == "echo_str"
    assert len(step.takes) == 1
    assert step.takes[0].name == "input"
    assert step.takes[0].type.__class__.__name__ == "PrimitiveType"
    assert step.takes[0].type.name == "str"
    assert step.gives is not None
    assert step.gives.name == "output"
    assert step.gives.type.name == "str"


def test_parse_step_with_multiple_takes():
    src = (
        "STEP add\n"
        "  TAKES: a: int, b: int\n"
        "  GIVES: sum: int\n"
        "  MODE:  exact\n"
    )
    program = parse(src)
    step = program.decls[0]
    assert [f.name for f in step.takes] == ["a", "b"]
    assert all(f.type.name == "int" for f in step.takes)


def test_parse_step_with_unknown_primitive_type_raises():
    src = "STEP foo\n  TAKES: x: bogus\n  MODE: exact\n"
    with pytest.raises(ParseError):
        parse(src)


def test_parse_step_rejects_duplicate_mode():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  MODE: judgment\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "duplicate" in str(exc.value).lower()


def test_parse_step_rejects_duplicate_takes():
    src = (
        "STEP foo\n"
        "  TAKES: a: int\n"
        "  TAKES: b: int\n"
        "  MODE:  exact\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "duplicate" in str(exc.value).lower()


def test_parse_step_rejects_duplicate_gives():
    src = (
        "STEP foo\n"
        "  GIVES: a: int\n"
        "  GIVES: b: int\n"
        "  MODE:  exact\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "duplicate" in str(exc.value).lower()


def test_parse_list_of_record():
    src = (
        "STEP load\n"
        "  GIVES: items: List<{name: str, age: int}>\n"
        "  MODE:  exact\n"
    )
    program = parse(src)
    step = program.decls[0]
    t = step.gives.type
    assert t.__class__.__name__ == "ListType"
    inner = t.inner
    assert inner.__class__.__name__ == "RecordType"
    assert [name for name, _ in inner.fields] == ["name", "age"]


def test_parse_enum_type():
    src = "STEP foo\n  TAKES: s: enum(low|mid|high)\n  MODE: exact\n"
    program = parse(src)
    step = program.decls[0]
    t = step.takes[0].type
    assert t.__class__.__name__ == "EnumType"
    assert t.values == ("low", "mid", "high")


def test_parse_unbalanced_brace_raises():
    src = "STEP foo\n  GIVES: x: {name: str\n  MODE: exact\n"
    with pytest.raises(ParseError):
        parse(src)
