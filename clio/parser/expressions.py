from clio.parser.ast_nodes import (
    BoolAndExpr,
    CallExpr,
    CompareExpr,
    ExprNode,
    FloatExpr,
    IdentExpr,
    IntExpr,
    StrExpr,
)
from clio.parser.tokens import Token, TokenType

_ALLOWED_FUNCS = {"len"}
_OP_TYPES = {
    TokenType.OP_EQ: "==",
    TokenType.OP_NE: "!=",
    TokenType.OP_GE: ">=",
    TokenType.OP_LE: "<=",
    TokenType.LANGLE: "<",
    TokenType.RANGLE: ">",
}


class ExpressionError(Exception):
    def __init__(self, msg: str, line: int, col: int) -> None:
        super().__init__(f"line {line}:{col}: {msg}")
        self.line = line
        self.col = col


class _ExprParser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def parse(self) -> ExprNode:
        """Parse `term OP term (OP term)*`.

        A single comparison returns a CompareExpr. A chained comparison
        (`0.0 <= score <= 1.0`) is desugared to a left-associative
        BoolAndExpr of pairwise CompareExprs — i.e.
        `(0.0 <= score) and (score <= 1.0)`. This matches Python semantics."""
        parts: list[ExprNode] = [self.parse_term()]
        ops: list[str] = []
        while self.pos < len(self.tokens) and self.peek().type in _OP_TYPES:
            ops.append(_OP_TYPES[self.peek().type])
            self.advance()
            parts.append(self.parse_term())

        if not ops:
            if self.pos < len(self.tokens):
                t = self.peek()
                raise ExpressionError(
                    f"expected comparison operator, got {t.type.value} {t.value!r}",
                    t.line, t.col,
                )
            raise ExpressionError(
                "expected comparison operator at end of input", 0, 0,
            )

        compares = [
            CompareExpr(left=parts[i], op=ops[i], right=parts[i + 1])
            for i in range(len(ops))
        ]
        if len(compares) == 1:
            return compares[0]
        result: ExprNode = compares[0]
        for c in compares[1:]:
            result = BoolAndExpr(left=result, right=c)
        return result

    def parse_term(self) -> ExprNode:
        t = self.peek()
        if t.type == TokenType.NUMBER:
            self.advance()
            if "." in t.value:
                return FloatExpr(value=float(t.value))
            return IntExpr(value=int(t.value))
        if t.type == TokenType.STRING:
            self.advance()
            return StrExpr(value=t.value)
        if t.type == TokenType.IDENT:
            self.advance()
            if self.pos < len(self.tokens) and self.peek().type == TokenType.LPAREN:
                if t.value not in _ALLOWED_FUNCS:
                    raise ExpressionError(
                        f"unknown function {t.value!r} (only `len` is allowed in v0.1)",
                        t.line, t.col,
                    )
                self.advance()
                args = [self.parse_term()]
                while self.peek().type == TokenType.COMMA:
                    self.advance()
                    args.append(self.parse_term())
                rp = self.peek()
                if rp.type != TokenType.RPAREN:
                    raise ExpressionError(
                        f"expected `)`, got {rp.type.value} {rp.value!r}",
                        rp.line, rp.col,
                    )
                self.advance()
                return CallExpr(func=t.value, args=tuple(args))
            return IdentExpr(name=t.value)
        raise ExpressionError(
            f"expected term, got {t.type.value} {t.value!r}", t.line, t.col,
        )


def parse_expression(tokens: list[Token]) -> tuple[ExprNode, int]:
    p = _ExprParser(tokens)
    expr = p.parse()
    return expr, p.pos


def expr_to_json_ast(node: ExprNode) -> dict:
    if isinstance(node, IntExpr):
        return {"kind": "int", "value": node.value}
    if isinstance(node, FloatExpr):
        return {"kind": "float", "value": node.value}
    if isinstance(node, StrExpr):
        return {"kind": "str", "value": node.value}
    if isinstance(node, IdentExpr):
        return {"kind": "ident", "name": node.name}
    if isinstance(node, CallExpr):
        return {
            "kind": "call",
            "func": node.func,
            "args": [expr_to_json_ast(a) for a in node.args],
        }
    if isinstance(node, CompareExpr):
        return {
            "kind": "compare",
            "op": node.op,
            "left": expr_to_json_ast(node.left),
            "right": expr_to_json_ast(node.right),
        }
    if isinstance(node, BoolAndExpr):
        return {
            "kind": "bool_and",
            "left": expr_to_json_ast(node.left),
            "right": expr_to_json_ast(node.right),
        }
    raise NotImplementedError(type(node).__name__)
