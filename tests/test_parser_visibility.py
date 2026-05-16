import pytest

from clio.parser.parser import ParseError, parse


def _flow_named(program, name):
    for d in program.decls:
        if getattr(d, "name", None) == name and d.__class__.__name__ == "FlowDecl":
            return d
    raise KeyError(name)


def _contract_named(program, name):
    for d in program.decls:
        if getattr(d, "name", None) == name and d.__class__.__name__ == "ContractDecl":
            return d
    raise KeyError(name)


def test_expose_flow():
    src = (
        'EXPOSE FLOW classify\n'
        '  step1()\n'
    )
    program = parse(src)
    assert _flow_named(program, "classify").exposed is True


def test_internal_flow_explicit():
    src = (
        'INTERNAL FLOW helper\n'
        '  step1()\n'
    )
    program = parse(src)
    assert _flow_named(program, "helper").exposed is False


def test_flow_no_prefix_is_internal():
    src = (
        'FLOW helper\n'
        '  step1()\n'
    )
    program = parse(src)
    assert _flow_named(program, "helper").exposed is False


def test_expose_contract():
    src = (
        'EXPOSE CONTRACT Article\n'
        '  SHAPE: {title: str}\n'
    )
    program = parse(src)
    assert _contract_named(program, "Article").exposed is True


def test_internal_contract_explicit():
    src = (
        'INTERNAL CONTRACT Article\n'
        '  SHAPE: {title: str}\n'
    )
    program = parse(src)
    assert _contract_named(program, "Article").exposed is False


def test_e_vis_001_both_markers():
    src = 'EXPOSE INTERNAL FLOW X\n'
    with pytest.raises(ParseError, match=r"only one visibility marker"):
        parse(src)


def test_e_vis_002_expose_on_step():
    src = (
        'EXPOSE STEP foo\n'
        '  MODE: exact\n'
    )
    with pytest.raises(ParseError, match=r"EXPOSE applies only to FLOW and CONTRACT"):
        parse(src)


def test_e_vis_002_expose_on_resources():
    src = (
        'EXPOSE RESOURCES\n'
        '  target: python\n'
    )
    with pytest.raises(ParseError, match=r"EXPOSE applies only to FLOW and CONTRACT"):
        parse(src)


def test_e_vis_002_internal_on_step():
    src = (
        'INTERNAL STEP foo\n'
        '  MODE: exact\n'
    )
    with pytest.raises(ParseError, match=r"INTERNAL applies only to FLOW and CONTRACT"):
        parse(src)
