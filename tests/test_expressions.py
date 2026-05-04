import pytest

from clio.parser.expressions import (
    ExpressionError,
    expr_to_json_ast,
    parse_expression,
)
from clio.parser.lexer import lex
from clio.parser.tokens import TokenType


def _toks(src: str):
    return [
        t for t in lex(src + "\n")
        if t.type not in {TokenType.NEWLINE, TokenType.INDENT, TokenType.DEDENT, TokenType.EOF}
    ]


def test_parse_len_gt_zero():
    expr, _ = parse_expression(_toks("len(reason) > 0"))
    assert expr_to_json_ast(expr) == {
        "kind": "compare",
        "op": ">",
        "left": {
            "kind": "call",
            "func": "len",
            "args": [{"kind": "ident", "name": "reason"}],
        },
        "right": {"kind": "int", "value": 0},
    }


def test_parse_str_equality():
    expr, _ = parse_expression(_toks('status == "ok"'))
    assert expr_to_json_ast(expr) == {
        "kind": "compare",
        "op": "==",
        "left": {"kind": "ident", "name": "status"},
        "right": {"kind": "str", "value": "ok"},
    }


def test_parse_unknown_function_raises():
    with pytest.raises(ExpressionError):
        parse_expression(_toks("max(x) > 0"))


def test_parse_ident_compare_ident():
    """`a > b` — a bare identifier as the right-hand term. Used to crash with
    IndexError because parse_term peeked past the last token to check for
    LPAREN."""
    expr, _ = parse_expression(_toks("a > b"))
    assert expr_to_json_ast(expr) == {
        "kind": "compare",
        "op": ">",
        "left": {"kind": "ident", "name": "a"},
        "right": {"kind": "ident", "name": "b"},
    }
