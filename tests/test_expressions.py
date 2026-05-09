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


def test_parse_chained_comparator_double():
    """`0.0 <= score <= 1.0` desugars to `(0.0 <= score) and (score <= 1.0)`.
    Matches Python's chained-comparison semantics."""
    expr, _ = parse_expression(_toks("0.0 <= score <= 1.0"))
    assert expr_to_json_ast(expr) == {
        "kind": "bool_and",
        "left": {
            "kind": "compare",
            "op": "<=",
            "left": {"kind": "float", "value": 0.0},
            "right": {"kind": "ident", "name": "score"},
        },
        "right": {
            "kind": "compare",
            "op": "<=",
            "left": {"kind": "ident", "name": "score"},
            "right": {"kind": "float", "value": 1.0},
        },
    }


def test_parse_chained_comparator_triple_left_associative():
    """`a < b < c < d` builds left-associative bool_and:
    `((a<b) and (b<c)) and (c<d)`."""
    expr, _ = parse_expression(_toks("a < b < c < d"))
    ast = expr_to_json_ast(expr)
    assert ast["kind"] == "bool_and"
    assert ast["right"] == {
        "kind": "compare", "op": "<",
        "left": {"kind": "ident", "name": "c"},
        "right": {"kind": "ident", "name": "d"},
    }
    assert ast["left"]["kind"] == "bool_and"
    assert ast["left"]["right"] == {
        "kind": "compare", "op": "<",
        "left": {"kind": "ident", "name": "b"},
        "right": {"kind": "ident", "name": "c"},
    }
    assert ast["left"]["left"] == {
        "kind": "compare", "op": "<",
        "left": {"kind": "ident", "name": "a"},
        "right": {"kind": "ident", "name": "b"},
    }


def test_parse_chained_comparator_mixed_ops():
    """`0 < score <= 1` mixes < and <= — both produced as plain compares
    inside a bool_and."""
    expr, _ = parse_expression(_toks("0 < score <= 1"))
    ast = expr_to_json_ast(expr)
    assert ast["kind"] == "bool_and"
    assert ast["left"]["op"] == "<"
    assert ast["right"]["op"] == "<="
