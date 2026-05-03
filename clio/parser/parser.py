from clio.parser.ast_nodes import Field, PrimitiveType, Program, StepDecl, TypeExpr
from clio.parser.lexer import lex
from clio.parser.tokens import Token, TokenType


class ParseError(Exception):
    def __init__(self, msg: str, line: int, col: int) -> None:
        super().__init__(f"line {line}:{col}: {msg}")
        self.line = line
        self.col = col


_PRIMITIVE_TYPES = {"int", "float", "str", "bool"}
_VALID_MODES = {"exact", "judgment"}


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def expect(self, ttype: TokenType, value: str | None = None) -> Token:
        t = self.peek()
        if t.type != ttype or (value is not None and t.value != value):
            want = f"{ttype.value}" + (f" {value!r}" if value else "")
            raise ParseError(f"expected {want}, got {t.type.value} {t.value!r}", t.line, t.col)
        return self.advance()

    def skip_newlines(self) -> None:
        while self.peek().type == TokenType.NEWLINE:
            self.advance()

    def parse_program(self) -> Program:
        decls: list[StepDecl] = []
        self.skip_newlines()
        while self.peek().type != TokenType.EOF:
            decls.append(self.parse_step())
            self.skip_newlines()
        return Program(tuple(decls))

    def parse_step(self) -> StepDecl:
        kw = self.expect(TokenType.KEYWORD, "STEP")
        ident = self.expect(TokenType.IDENT)
        self.expect(TokenType.NEWLINE)
        # Detect missing-MODE early (Phase 1 deviation kept).
        if self.peek().type != TokenType.INDENT:
            raise ParseError(
                f"STEP {ident.value} is missing required MODE field",
                kw.line, kw.col,
            )
        self.expect(TokenType.INDENT)

        takes: tuple[Field, ...] = ()
        gives: Field | None = None
        mode: str | None = None

        while self.peek().type != TokenType.DEDENT:
            t = self.peek()
            if t.type != TokenType.KEYWORD:
                raise ParseError(f"unexpected {t.type.value} {t.value!r}", t.line, t.col)

            if t.value == "TAKES":
                if takes:
                    raise ParseError(
                        f"STEP {ident.value} has duplicate TAKES field",
                        t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                takes = self.parse_field_list()
                self.expect(TokenType.NEWLINE)
            elif t.value == "GIVES":
                if gives is not None:
                    raise ParseError(
                        f"STEP {ident.value} has duplicate GIVES field",
                        t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                fields = self.parse_field_list()
                if len(fields) != 1:
                    raise ParseError("GIVES must declare exactly one field", t.line, t.col)
                gives = fields[0]
                self.expect(TokenType.NEWLINE)
            elif t.value == "MODE":
                if mode is not None:
                    raise ParseError(
                        f"STEP {ident.value} has duplicate MODE field",
                        t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                value_tok = self.expect(TokenType.KEYWORD)
                if value_tok.value not in _VALID_MODES:
                    raise ParseError(
                        f"unknown MODE {value_tok.value!r}, expected one of {sorted(_VALID_MODES)}",
                        value_tok.line, value_tok.col,
                    )
                mode = value_tok.value
                self.expect(TokenType.NEWLINE)
            else:
                raise ParseError(f"unexpected step field {t.value!r}", t.line, t.col)

        self.expect(TokenType.DEDENT)
        if mode is None:
            raise ParseError(f"STEP {ident.value} is missing required MODE field", kw.line, kw.col)

        return StepDecl(name=ident.value, mode=mode, takes=takes, gives=gives, line=kw.line, col=kw.col)

    def parse_field_list(self) -> tuple[Field, ...]:
        fields = [self.parse_field()]
        while self.peek().type == TokenType.COMMA:
            self.advance()
            fields.append(self.parse_field())
        return tuple(fields)

    def parse_field(self) -> Field:
        name_tok = self.expect(TokenType.IDENT)
        self.expect(TokenType.COLON)
        type_expr = self.parse_type_expr()
        return Field(name=name_tok.value, type=type_expr, line=name_tok.line, col=name_tok.col)

    def parse_type_expr(self) -> TypeExpr:
        t = self.peek()
        if t.type == TokenType.KEYWORD and t.value in _PRIMITIVE_TYPES:
            self.advance()
            return PrimitiveType(name=t.value)
        raise ParseError(
            f"expected a type expression, got {t.type.value} {t.value!r}",
            t.line, t.col,
        )


def parse(source: str) -> Program:
    return _Parser(lex(source)).parse_program()
