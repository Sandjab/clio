from clio.keywords import Keyword
from clio.parser.tokens import Token, TokenType


_KEYWORD_VALUES = {k.value for k in Keyword}

_SINGLE_CHAR_TOKENS = {
    ":": TokenType.COLON,
    ",": TokenType.COMMA,
    "<": TokenType.LANGLE,
    ">": TokenType.RANGLE,
    "{": TokenType.LBRACE,
    "}": TokenType.RBRACE,
    "(": TokenType.LPAREN,
    ")": TokenType.RPAREN,
    "|": TokenType.PIPE,
}


class LexError(Exception):
    def __init__(self, msg: str, line: int, col: int) -> None:
        super().__init__(f"line {line}:{col}: {msg}")
        self.line = line
        self.col = col


def lex(source: str) -> list[Token]:
    tokens: list[Token] = []
    indent_stack: list[int] = [0]
    lines = source.splitlines(keepends=False)

    for lineno, raw_line in enumerate(lines, start=1):
        line = _strip_comment(raw_line)
        stripped = line.lstrip(" ")
        if stripped == "":
            continue

        indent = len(line) - len(stripped)
        _emit_indent_changes(tokens, indent_stack, indent, lineno)

        col = indent + 1
        i = 0
        while i < len(stripped):
            ch = stripped[i]
            if ch == " ":
                i += 1
                col += 1
                continue
            single = _SINGLE_CHAR_TOKENS.get(ch)
            if single is not None:
                tokens.append(Token(single, ch, lineno, col))
                i += 1
                col += 1
                continue
            if ch.isalpha() or ch == "_":
                j = i
                while j < len(stripped) and (stripped[j].isalnum() or stripped[j] == "_"):
                    j += 1
                word = stripped[i:j]
                ttype = TokenType.KEYWORD if word in _KEYWORD_VALUES else TokenType.IDENT
                tokens.append(Token(ttype, word, lineno, col))
                col += j - i
                i = j
                continue
            raise LexError(f"unexpected character {ch!r}", lineno, col)

        tokens.append(Token(TokenType.NEWLINE, "\n", lineno, col))

    while len(indent_stack) > 1:
        indent_stack.pop()
        tokens.append(Token(TokenType.DEDENT, "", len(lines) + 1, 1))

    tokens.append(Token(TokenType.EOF, "", len(lines) + 1, 1))
    return tokens


def _strip_comment(line: str) -> str:
    in_str = False
    for idx, ch in enumerate(line):
        if ch == '"':
            in_str = not in_str
        elif ch == "#" and not in_str:
            return line[:idx].rstrip()
    return line.rstrip()


def _emit_indent_changes(
    tokens: list[Token], stack: list[int], indent: int, lineno: int
) -> None:
    if indent > stack[-1]:
        stack.append(indent)
        tokens.append(Token(TokenType.INDENT, "", lineno, 1))
    while indent < stack[-1]:
        stack.pop()
        tokens.append(Token(TokenType.DEDENT, "", lineno, 1))
    if indent != stack[-1]:
        raise LexError("inconsistent indentation", lineno, 1)
