from dataclasses import dataclass
from enum import Enum


class TokenType(str, Enum):
    KEYWORD = "KEYWORD"
    IDENT = "IDENT"
    NUMBER = "NUMBER"
    STRING = "STRING"
    DURATION = "DURATION"
    EQUALS = "EQUALS"
    OP_EQ = "OP_EQ"        # ==
    OP_NE = "OP_NE"        # !=
    OP_GE = "OP_GE"        # >=
    OP_LE = "OP_LE"        # <=
    COLON = "COLON"
    COMMA = "COMMA"
    ARROW = "ARROW"
    LANGLE = "LANGLE"
    RANGLE = "RANGLE"
    LBRACE = "LBRACE"
    RBRACE = "RBRACE"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    LBRACKET = "LBRACKET"
    RBRACKET = "RBRACKET"
    PIPE = "PIPE"
    NEWLINE = "NEWLINE"
    INDENT = "INDENT"
    DEDENT = "DEDENT"
    EOF = "EOF"


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: str
    line: int
    col: int
