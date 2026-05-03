from dataclasses import dataclass
from enum import Enum


class TokenType(str, Enum):
    KEYWORD = "KEYWORD"
    IDENT = "IDENT"
    COLON = "COLON"
    COMMA = "COMMA"
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
