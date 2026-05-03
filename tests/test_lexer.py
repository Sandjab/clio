from clio.parser.lexer import lex
from clio.parser.tokens import TokenType


def _types(tokens):
    return [t.type for t in tokens]


def test_lex_minimal_step():
    src = "STEP foo\n  MODE: exact\n"
    tokens = lex(src)
    assert _types(tokens) == [
        TokenType.KEYWORD,   # STEP
        TokenType.IDENT,     # foo
        TokenType.NEWLINE,
        TokenType.INDENT,
        TokenType.KEYWORD,   # MODE
        TokenType.COLON,
        TokenType.KEYWORD,   # exact
        TokenType.NEWLINE,
        TokenType.DEDENT,
        TokenType.EOF,
    ]


def test_lex_tracks_line_and_col():
    src = "STEP foo\n"
    tokens = lex(src)
    assert tokens[0].line == 1 and tokens[0].col == 1
    assert tokens[1].line == 1 and tokens[1].col == 6  # 'foo' starts at col 6


def test_lex_ignores_blank_and_comment_lines():
    src = "# top comment\n\nSTEP foo\n  MODE: exact  # inline\n"
    tokens = lex(src)
    # First non-trivial token must still be STEP
    assert tokens[0].type == TokenType.KEYWORD
    assert tokens[0].value == "STEP"
