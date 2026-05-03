from dataclasses import dataclass
from enum import Enum


class TokenType(str, Enum):
    KEYWORD = "KEYWORD"          # STEP, MODE, exact, etc. (closed set, see Keyword enum)
    IDENT = "IDENT"              # bare identifier, e.g. foo
    COLON = "COLON"              # :
    NEWLINE = "NEWLINE"          # logical end-of-line (one or more \n)
    INDENT = "INDENT"            # increase in indentation
    DEDENT = "DEDENT"            # decrease in indentation
    EOF = "EOF"                  # end of file


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: str
    line: int     # 1-based
    col: int      # 1-based
