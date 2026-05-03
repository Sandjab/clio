from dataclasses import dataclass
from enum import Enum


class TokenType(str, Enum):
    KEYWORD = "KEYWORD"
    IDENT = "IDENT"
    COLON = "COLON"
    COMMA = "COMMA"
    LANGLE = "LANGLE"      # <
    RANGLE = "RANGLE"      # >
    LBRACE = "LBRACE"      # {
    RBRACE = "RBRACE"      # }
    LPAREN = "LPAREN"      # (
    RPAREN = "RPAREN"      # )
    PIPE = "PIPE"          # |
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
