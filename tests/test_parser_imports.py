import pytest

from clio.parser.parser import ParseError, parse


def test_single_import():
    src = 'FROM "./lib.clio" IMPORT classify\n'
    program = parse(src)
    assert len(program.imports) == 1
    imp = program.imports[0]
    assert imp.path == "./lib.clio"
    assert len(imp.items) == 1
    assert imp.items[0].name == "classify"
    assert imp.items[0].alias is None


def test_multi_import():
    src = 'FROM "./lib.clio" IMPORT classify, summarize, Article\n'
    program = parse(src)
    imp = program.imports[0]
    assert [i.name for i in imp.items] == ["classify", "summarize", "Article"]


def test_import_with_alias():
    src = 'FROM "./lib.clio" IMPORT classify AS clf, summarize\n'
    program = parse(src)
    items = program.imports[0].items
    assert items[0].name == "classify" and items[0].alias == "clf"
    assert items[1].name == "summarize" and items[1].alias is None


def test_parent_dir_path():
    src = 'FROM "../shared/util.clio" IMPORT enrich\n'
    program = parse(src)
    assert program.imports[0].path == "../shared/util.clio"


def test_multiple_imports():
    src = (
        'FROM "./a.clio" IMPORT X\n'
        'FROM "./b.clio" IMPORT Y, Z\n'
    )
    program = parse(src)
    assert len(program.imports) == 2
    assert program.imports[0].path == "./a.clio"
    assert program.imports[1].path == "./b.clio"


def test_import_with_subsequent_flow():
    src = (
        'FROM "./lib.clio" IMPORT classify\n'
        '\n'
        'STEP classify\n'
        '  MODE: exact\n'
        '\n'
        'FLOW pipeline\n'
        '  classify()\n'
    )
    program = parse(src)
    assert len(program.imports) == 1
    assert len(program.decls) == 2  # STEP + FLOW


def test_e_imp_001_no_prefix():
    src = 'FROM "lib.clio" IMPORT X\n'
    with pytest.raises(ParseError, match=r"path must start with './' or '../'"):
        parse(src)


def test_e_imp_001_absolute_path():
    src = 'FROM "/abs/lib.clio" IMPORT X\n'
    with pytest.raises(ParseError, match=r"path must start with './' or '../'"):
        parse(src)


def test_e_imp_002_no_extension():
    src = 'FROM "./lib" IMPORT X\n'
    with pytest.raises(ParseError, match=r"path must end with '.clio'"):
        parse(src)


def test_e_imp_003_empty_list():
    src = 'FROM "./lib.clio" IMPORT\n'
    with pytest.raises(ParseError, match=r"expected at least one symbol after IMPORT"):
        parse(src)


def test_e_imp_004_missing_alias_identifier():
    src = 'FROM "./lib.clio" IMPORT X AS\n'
    with pytest.raises(ParseError, match=r"expected identifier after AS"):
        parse(src)


def test_e_imp_005_duplicate_in_same_statement():
    src = 'FROM "./lib.clio" IMPORT X, X\n'
    with pytest.raises(ParseError, match=r"duplicate symbol 'X'"):
        parse(src)


def test_alias_same_as_name_allowed():
    """X AS X is a no-op but explicit; allow silently."""
    src = 'FROM "./lib.clio" IMPORT X AS X\n'
    program = parse(src)
    assert program.imports[0].items[0].alias == "X"
