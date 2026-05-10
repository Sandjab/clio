"""Unit tests for `clio.runtime.sql`.

The end-to-end flow uses sqlite (stdlib, always available). Postgres /
mysql drivers are lazy-imported and only the named-binding translation
is exercised in unit tests; the live-server lifecycle is exercised
manually with a real database when needed."""
from __future__ import annotations

import sqlite3

import pytest

from clio.runtime.sql import (
    _connections,
    _locks,
    _resolve_env,
    _translate_bindings,
    execute,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the per-database connection cache between tests so each
    test starts with a fresh sqlite connection."""
    for conn in list(_connections.values()):
        try:
            conn.close()
        except Exception:
            pass
    _connections.clear()
    _locks.clear()
    yield
    for conn in list(_connections.values()):
        try:
            conn.close()
        except Exception:
            pass
    _connections.clear()
    _locks.clear()


# ---- _resolve_env ----------------------------------------------------------


def test_resolve_env_returns_value_when_no_env_prefix():
    assert _resolve_env("./db.sqlite") == "./db.sqlite"


def test_resolve_env_resolves_full_match(monkeypatch):
    monkeypatch.setenv("SQL_TEST_URL", "postgresql://localhost/test")
    assert _resolve_env("env:SQL_TEST_URL") == "postgresql://localhost/test"


def test_resolve_env_raises_on_missing_var(monkeypatch):
    monkeypatch.delenv("UNSET_FOR_SQL_TEST", raising=False)
    with pytest.raises(KeyError, match="UNSET_FOR_SQL_TEST"):
        _resolve_env("env:UNSET_FOR_SQL_TEST")


def test_resolve_env_only_resolves_whole_string():
    """A bare `env:NAME` substring inside a longer string is plain text."""
    assert _resolve_env("postgresql://env:HOST_VAR/db") == "postgresql://env:HOST_VAR/db"


# ---- _translate_bindings ---------------------------------------------------


def test_translate_bindings_sqlite_passes_through():
    q = "SELECT * FROM t WHERE id = :id AND name = :name"
    assert _translate_bindings(q, "sqlite") == q


def test_translate_bindings_postgres_uses_pyformat():
    q = "SELECT * FROM t WHERE id = :id AND name = :name"
    assert _translate_bindings(q, "postgres") == (
        "SELECT * FROM t WHERE id = %(id)s AND name = %(name)s"
    )


def test_translate_bindings_mysql_uses_pyformat():
    q = "INSERT INTO t (email) VALUES (:email)"
    assert _translate_bindings(q, "mysql") == (
        "INSERT INTO t (email) VALUES (%(email)s)"
    )


def test_translate_bindings_preserves_postgres_cast_operator():
    """`value::int` is a PostgreSQL cast, not a binding. The lookbehind
    in the regex must keep it intact."""
    q = "SELECT (price::int) FROM t WHERE id = :id"
    assert _translate_bindings(q, "postgres") == (
        "SELECT (price::int) FROM t WHERE id = %(id)s"
    )


def test_translate_bindings_unknown_driver_raises():
    with pytest.raises(RuntimeError, match="unknown driver"):
        _translate_bindings("SELECT 1", "duckdb")


# ---- execute() with sqlite in-memory ---------------------------------------


@pytest.fixture
def memdb_spec():
    return {"name": "test_mem", "driver": "sqlite", "url": ":memory:"}


@pytest.fixture
def seeded_db(memdb_spec):
    """Open the cache connection then seed three rows. Subsequent calls
    via `execute()` reuse the same cached connection."""
    execute(
        memdb_spec,
        "CREATE TABLE customers (id INTEGER PRIMARY KEY, email TEXT, segment TEXT)",
        {},
        gives_shape="primitive",
    )
    execute(
        memdb_spec,
        "INSERT INTO customers (id, email, segment) VALUES (1, 'a@x', 'gold')",
        {},
        gives_shape="primitive",
    )
    execute(
        memdb_spec,
        "INSERT INTO customers (id, email, segment) VALUES (2, 'b@x', 'silver')",
        {},
        gives_shape="primitive",
    )
    execute(
        memdb_spec,
        "INSERT INTO customers (id, email, segment) VALUES (3, 'c@y', 'gold')",
        {},
        gives_shape="primitive",
    )
    return memdb_spec


def test_execute_select_list_of_records(seeded_db):
    rows = execute(
        seeded_db,
        "SELECT id, email FROM customers WHERE segment = :seg ORDER BY id",
        {"seg": "gold"},
        gives_shape="list_of_records",
    )
    assert rows == [
        {"id": 1, "email": "a@x"},
        {"id": 3, "email": "c@y"},
    ]


def test_execute_select_record_single_row(seeded_db):
    row = execute(
        seeded_db,
        "SELECT id, segment FROM customers WHERE email = :email",
        {"email": "b@x"},
        gives_shape="record",
    )
    assert row == {"id": 2, "segment": "silver"}


def test_execute_select_record_zero_rows_raises(seeded_db):
    with pytest.raises(RuntimeError, match="expects exactly one row, got 0"):
        execute(
            seeded_db,
            "SELECT id FROM customers WHERE email = :email",
            {"email": "missing@x"},
            gives_shape="record",
        )


def test_execute_select_record_multi_row_raises(seeded_db):
    with pytest.raises(RuntimeError, match="expects exactly one row, got 2"):
        execute(
            seeded_db,
            "SELECT id FROM customers WHERE segment = :seg",
            {"seg": "gold"},
            gives_shape="record",
        )


def test_execute_select_primitive(seeded_db):
    val = execute(
        seeded_db,
        "SELECT COUNT(*) FROM customers WHERE segment = :seg",
        {"seg": "gold"},
        gives_shape="primitive",
    )
    assert val == 2


def test_execute_dml_returns_rowcount(seeded_db):
    """An UPDATE has no cursor.description; runtime returns rowcount
    regardless of the requested gives_shape."""
    n = execute(
        seeded_db,
        "UPDATE customers SET segment = 'platinum' WHERE segment = :seg",
        {"seg": "gold"},
        gives_shape="primitive",
    )
    assert n == 2


def test_execute_unknown_gives_shape_raises(seeded_db):
    with pytest.raises(RuntimeError, match="unknown gives_shape"):
        execute(
            seeded_db,
            "SELECT 1",
            {},
            gives_shape="weird",
        )


def test_execute_caches_connection_per_db_name(memdb_spec):
    """Two execute() calls with the same db_spec name must share the
    underlying sqlite connection — otherwise an in-memory DB would
    appear empty across calls."""
    execute(memdb_spec, "CREATE TABLE t (x INT)", {}, gives_shape="primitive")
    execute(memdb_spec, "INSERT INTO t VALUES (42)", {}, gives_shape="primitive")
    val = execute(memdb_spec, "SELECT x FROM t", {}, gives_shape="primitive")
    assert val == 42
    # Underlying connection is the same object on both calls.
    conn = _connections["test_mem"]
    assert isinstance(conn, sqlite3.Connection)


def test_execute_resolves_env_url_for_sqlite(monkeypatch, tmp_path):
    """`env:NAME` in the url is resolved at connection-open time."""
    db_path = tmp_path / "envtest.sqlite"
    monkeypatch.setenv("SQLITE_TEST_PATH", str(db_path))
    spec = {"name": "envdb", "driver": "sqlite", "url": "env:SQLITE_TEST_PATH"}
    execute(spec, "CREATE TABLE t (x INT)", {}, gives_shape="primitive")
    execute(spec, "INSERT INTO t VALUES (1)", {}, gives_shape="primitive")
    val = execute(spec, "SELECT x FROM t", {}, gives_shape="primitive")
    assert val == 1
    assert db_path.exists()


def test_execute_strips_sqlite_url_prefix(tmp_path):
    """`sqlite:///path` SQLAlchemy-style URL is accepted (prefix stripped)."""
    db_path = tmp_path / "prefixed.sqlite"
    spec = {"name": "prefixdb", "driver": "sqlite", "url": f"sqlite:///{db_path}"}
    execute(spec, "CREATE TABLE t (x INT)", {}, gives_shape="primitive")
    execute(spec, "INSERT INTO t VALUES (7)", {}, gives_shape="primitive")
    val = execute(spec, "SELECT x FROM t", {}, gives_shape="primitive")
    assert val == 7


def test_execute_unknown_driver_raises():
    spec = {"name": "x", "driver": "duckdb", "url": "/tmp/x.db"}
    with pytest.raises(RuntimeError, match="unknown driver"):
        execute(spec, "SELECT 1", {}, gives_shape="primitive")


def test_execute_postgres_driver_raises_friendly_error_when_missing():
    """If `psycopg` is not installed, the lazy import should raise
    RuntimeError with installation guidance, never opaque ImportError."""
    try:
        import psycopg  # noqa: F401
        pytest.skip("psycopg installed; cannot exercise friendly-error path")
    except ImportError:
        pass
    spec = {"name": "pg", "driver": "postgres", "url": "postgresql://localhost/x"}
    with pytest.raises(RuntimeError, match="psycopg"):
        execute(spec, "SELECT 1", {}, gives_shape="primitive")


def test_execute_mysql_driver_raises_friendly_error_when_missing():
    """If `pymysql` is not installed, the lazy import should raise
    RuntimeError with installation guidance, never opaque ImportError."""
    try:
        import pymysql  # noqa: F401
        pytest.skip("pymysql installed; cannot exercise friendly-error path")
    except ImportError:
        pass
    spec = {"name": "my", "driver": "mysql", "url": "mysql://localhost/x"}
    with pytest.raises(RuntimeError, match="pymysql"):
        execute(spec, "SELECT 1", {}, gives_shape="primitive")


# ---- _translate_bindings: Gemini PR #6 review (string/comment safety) ------


def test_translate_bindings_skips_single_quoted_string_literal():
    """A `:name` substring inside a SQL string literal is data, not a
    binding. Gemini regression: `'time:00'` would silently become
    `'time:%(00)s'` and raise KeyError at execute time."""
    q = "SELECT * FROM t WHERE meta = 'time:12:00' AND id = :id"
    assert _translate_bindings(q, "postgres") == (
        "SELECT * FROM t WHERE meta = 'time:12:00' AND id = %(id)s"
    )


def test_translate_bindings_handles_escaped_single_quote_in_literal():
    """SQL `''` inside a single-quoted literal is an escaped quote — the
    literal continues. The state machine must not split the literal."""
    q = "SELECT * FROM t WHERE note = 'it''s :fine' AND id = :id"
    assert _translate_bindings(q, "postgres") == (
        "SELECT * FROM t WHERE note = 'it''s :fine' AND id = %(id)s"
    )


def test_translate_bindings_skips_double_quoted_identifier():
    """PostgreSQL double-quoted identifiers can carry colons; never
    rewrite inside them."""
    q = 'SELECT "weird:col" FROM t WHERE id = :id'
    assert _translate_bindings(q, "postgres") == (
        'SELECT "weird:col" FROM t WHERE id = %(id)s'
    )


def test_translate_bindings_skips_line_comment():
    q = "SELECT id FROM t -- where id = :ghost\nWHERE id = :id"
    assert _translate_bindings(q, "postgres") == (
        "SELECT id FROM t -- where id = :ghost\nWHERE id = %(id)s"
    )


def test_translate_bindings_skips_block_comment():
    q = "SELECT /* :ignored */ id FROM t WHERE id = :id"
    assert _translate_bindings(q, "postgres") == (
        "SELECT /* :ignored */ id FROM t WHERE id = %(id)s"
    )


def test_translate_bindings_unterminated_block_comment_left_alone():
    """Defensive: an unterminated `/*` runs to end-of-string, no rewrites
    in that tail. The driver will reject the SQL itself; we just refuse
    to corrupt it further."""
    q = "SELECT id FROM t /* :ignored to EOF and :no_rewrite"
    assert _translate_bindings(q, "postgres") == q


def test_translate_bindings_string_with_named_binding_after_it():
    """Mixed: a string literal followed by a real binding must round-trip
    correctly — the state machine has to re-enter scan mode after `'`."""
    q = "INSERT INTO t (name, id) VALUES ('static :literal', :id)"
    assert _translate_bindings(q, "postgres") == (
        "INSERT INTO t (name, id) VALUES ('static :literal', %(id)s)"
    )
