from clio.keywords import Keyword
from clio.parser.tokens import Token, TokenType

_KEYWORD_VALUES = {k.value for k in Keyword}

_SINGLE_CHAR_TOKENS = {
    ":": TokenType.COLON,
    ",": TokenType.COMMA,
    ".": TokenType.DOT,
    "<": TokenType.LANGLE,
    ">": TokenType.RANGLE,
    "{": TokenType.LBRACE,
    "}": TokenType.RBRACE,
    "(": TokenType.LPAREN,
    ")": TokenType.RPAREN,
    "[": TokenType.LBRACKET,
    "]": TokenType.RBRACKET,
    "|": TokenType.PIPE,
    "=": TokenType.EQUALS,
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

    idx = 0
    while idx < len(lines):
        lineno = idx + 1
        raw_line = lines[idx]
        line = _strip_comment(raw_line)
        stripped = line.lstrip(" ")
        if stripped == "":
            idx += 1
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
            if ch.isdigit():
                j = i
                saw_dot = False
                while j < len(stripped) and (stripped[j].isdigit() or (stripped[j] == "." and not saw_dot)):
                    if stripped[j] == ".":
                        saw_dot = True
                    j += 1
                # Duration suffix: int followed by [smhd] becomes a single DURATION token.
                if not saw_dot and j < len(stripped) and stripped[j] in "smhd":
                    suffix_end = j + 1
                    tokens.append(Token(TokenType.DURATION, stripped[i:suffix_end], lineno, col))
                    col += suffix_end - i
                    i = suffix_end
                    continue
                tokens.append(Token(TokenType.NUMBER, stripped[i:j], lineno, col))
                col += j - i
                i = j
                continue
            if ch == '"':
                j = i + 1
                buf: list[str] = []
                while j < len(stripped) and stripped[j] != '"':
                    if (
                        stripped[j] == "\\"
                        and j + 1 < len(stripped)
                        and stripped[j + 1] in ('"', "\\")
                    ):
                        buf.append(stripped[j + 1])
                        j += 2
                        continue
                    buf.append(stripped[j])
                    j += 1
                if j >= len(stripped):
                    raise LexError("unterminated string literal", lineno, col)
                tokens.append(Token(TokenType.STRING, "".join(buf), lineno, col))
                col += (j - i) + 1
                i = j + 1
                continue
            if ch == "-" and i + 1 < len(stripped) and stripped[i + 1] == ">":
                tokens.append(Token(TokenType.ARROW, "->", lineno, col))
                i += 2
                col += 2
                continue
            two = stripped[i:i + 2]
            if two == "==":
                tokens.append(Token(TokenType.OP_EQ, "==", lineno, col))
                i += 2
                col += 2
                continue
            if two == "!=":
                tokens.append(Token(TokenType.OP_NE, "!=", lineno, col))
                i += 2
                col += 2
                continue
            if two == ">=":
                tokens.append(Token(TokenType.OP_GE, ">=", lineno, col))
                i += 2
                col += 2
                continue
            if two == "<=":
                tokens.append(Token(TokenType.OP_LE, "<=", lineno, col))
                i += 2
                col += 2
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
                # Allow an optional hyphenated continuation like `claude-cli`.
                if j < len(stripped) and stripped[j] == "-":
                    k = j + 1
                    while k < len(stripped) and (stripped[k].isalnum() or stripped[k] == "_"):
                        k += 1
                    candidate = stripped[i:k]
                    if candidate in _KEYWORD_VALUES:
                        tokens.append(Token(TokenType.KEYWORD, candidate, lineno, col))
                        col += k - i
                        i = k
                        continue
                word = stripped[i:j]
                ttype = TokenType.KEYWORD if word in _KEYWORD_VALUES else TokenType.IDENT
                tokens.append(Token(ttype, word, lineno, col))
                col += j - i
                i = j
                continue
            raise LexError(f"unexpected character {ch!r}", lineno, col)

        # Literal block scalar (YAML-style `key: |`): if the last token on this
        # line is PIPE, treat the following more-indented lines as raw text and
        # emit a single BLOCK_SCALAR token. Used for `impl.sql.query` bodies.
        if tokens and tokens[-1].type == TokenType.PIPE:
            pipe_tok = tokens.pop()
            body_lines, consumed = _consume_block_body(lines, idx + 1, indent)
            content = _strip_common_indent(body_lines)
            tokens.append(Token(TokenType.BLOCK_SCALAR, content, pipe_tok.line, pipe_tok.col))
            tokens.append(Token(TokenType.NEWLINE, "\n", lineno, col))
            idx += 1 + consumed
            continue

        tokens.append(Token(TokenType.NEWLINE, "\n", lineno, col))
        idx += 1

    while len(indent_stack) > 1:
        indent_stack.pop()
        tokens.append(Token(TokenType.DEDENT, "", len(lines) + 1, 1))

    tokens.append(Token(TokenType.EOF, "", len(lines) + 1, 1))
    return tokens


def _strip_comment(line: str) -> str:
    in_str = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        # Mirror the string scanner's escapes: inside a string, \" and \\ consume
        # two chars so an escaped quote does not toggle `in_str` (and a # that
        # follows it is not mistaken for a comment).
        if in_str and ch == "\\" and i + 1 < n and line[i + 1] in ('"', "\\"):
            i += 2
            continue
        if ch == '"':
            in_str = not in_str
        elif ch == "#" and not in_str:
            return line[:i].rstrip()
        i += 1
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


def _consume_block_body(lines: list[str], start: int, base_indent: int) -> tuple[list[str], int]:
    """Aspirate consecutive lines that belong to a `|` block scalar.

    A line is part of the body when it is empty/whitespace-only OR strictly
    more indented than `base_indent` (the indent of the line carrying the
    `|`). Returns `(body_lines_raw, consumed_count)` where `body_lines_raw`
    keeps each line in its original form (so the common-indent strip can
    operate on the true leading spaces, not the comment-stripped version).
    """
    body: list[str] = []
    j = start
    while j < len(lines):
        line = lines[j]
        if line.strip() == "":
            body.append("")
            j += 1
            continue
        lstripped = line.lstrip(" ")
        lindent = len(line) - len(lstripped)
        if lindent > base_indent:
            body.append(line)
            j += 1
            continue
        break
    return body, j - start


def _strip_common_indent(body: list[str]) -> str:
    """Remove the longest leading-space prefix common to every non-empty line.

    Mirrors the YAML literal-block scalar (`|`) semantics: empty lines are
    preserved as empty inside the scalar, the common indentation is removed,
    and trailing empty lines are trimmed (clip-mode default)."""
    min_indent: int | None = None
    for bl in body:
        if bl.strip() == "":
            continue
        bli = len(bl) - len(bl.lstrip(" "))
        if min_indent is None or bli < min_indent:
            min_indent = bli
    if min_indent is None:
        min_indent = 0
    out = [bl[min_indent:] if bl.strip() else "" for bl in body]
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)
