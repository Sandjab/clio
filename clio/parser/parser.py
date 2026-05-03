from clio.parser.ast_nodes import Program, StepDecl
from clio.parser.lexer import lex
from clio.parser.tokens import Token, TokenType


class ParseError(Exception):
    def __init__(self, msg: str, line: int, col: int) -> None:
        super().__init__(f"line {line}:{col}: {msg}")
        self.line = line
        self.col = col


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

        if self.peek().type != TokenType.INDENT:
            raise ParseError(
                f"STEP {ident.value} is missing required MODE field",
                kw.line, kw.col,
            )
        self.advance()  # consume INDENT

        mode: str | None = None
        while self.peek().type == TokenType.KEYWORD and self.peek().value == "MODE":
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

        self.expect(TokenType.DEDENT)

        if mode is None:
            raise ParseError(f"STEP {ident.value} is missing required MODE field", kw.line, kw.col)
        return StepDecl(name=ident.value, mode=mode, line=kw.line, col=kw.col)


def parse(source: str) -> Program:
    return _Parser(lex(source)).parse_program()
