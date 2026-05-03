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


def test_lex_takes_gives_with_primitive():
    src = "STEP echo_str\n  TAKES: input: str\n  GIVES: output: str\n  MODE: exact\n"
    tokens = lex(src)
    types = _types(tokens)
    keyword_values = [t.value for t in tokens if t.type == TokenType.KEYWORD]
    assert "TAKES" in keyword_values
    assert "GIVES" in keyword_values
    assert "str" in keyword_values
    assert types.count(TokenType.COLON) == 5  # TAKES:, input:, GIVES:, output:, MODE:


def test_lex_list_records_enums():
    src = (
        "STEP foo\n"
        "  GIVES: items: List<{name: str, age: int}>\n"
        "  MODE:  exact\n"
    )
    tokens = lex(src)
    types = [t.type for t in tokens]
    assert TokenType.LANGLE in types
    assert TokenType.RANGLE in types
    assert TokenType.LBRACE in types
    assert TokenType.RBRACE in types


def test_lex_enum():
    src = "STEP foo\n  TAKES: s: enum(a|b|c)\n  MODE: exact\n"
    tokens = lex(src)
    assert any(t.type == TokenType.PIPE for t in tokens)
    assert any(t.type == TokenType.LPAREN for t in tokens)
    assert any(t.type == TokenType.RPAREN for t in tokens)


def test_lex_number_and_string():
    extra = lex('A 42 "hello world"\n')
    types = [t.type for t in extra]
    assert TokenType.NUMBER in types
    assert TokenType.STRING in types
    string_tok = [t for t in extra if t.type == TokenType.STRING][0]
    assert string_tok.value == "hello world"


def test_lex_arrow():
    src = "FLOW f\n  a -> b\n"
    tokens = lex(src)
    assert any(t.type == TokenType.ARROW for t in tokens)


def test_lex_comparison_operators():
    tokens = lex("a == b\nc != d\ne >= f\ng <= h\n")
    types = {t.type for t in tokens}
    assert TokenType.OP_EQ in types
    assert TokenType.OP_NE in types
    assert TokenType.OP_GE in types
    assert TokenType.OP_LE in types


def test_lex_duration():
    tokens = lex("a 24h 7d 30s 5m\n")
    durations = [t for t in tokens if t.type == TokenType.DURATION]
    assert [t.value for t in durations] == ["24h", "7d", "30s", "5m"]


def test_lex_int_without_suffix_is_number():
    tokens = lex("a 42 3.14\n")
    nums = [t for t in tokens if t.type == TokenType.NUMBER]
    assert [t.value for t in nums] == ["42", "3.14"]


def test_lex_new_keywords():
    src = (
        "STEP s\n"
        "  CACHE: ttl(24h)\n"
        "  ON_FAIL: retry(3) then escalate then fallback(other) then abort(\"x\")\n"
        "  MODE: judgment\n"
    )
    tokens = lex(src)
    keyword_values = {t.value for t in tokens if t.type == TokenType.KEYWORD}
    for k in ("CACHE", "ttl", "ON_FAIL", "retry", "then", "escalate", "fallback", "abort"):
        assert k in keyword_values, f"missing keyword {k!r}"
