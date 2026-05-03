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
