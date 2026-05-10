"""Runtime helpers for impl.mode: sql steps.

Copied verbatim into the emitted package's `clio_runtime/` directory by
the python and mcp-server emitters when the flow has any impl.sql step.
See LANGUAGE_SPEC.md §impl.mode: sql for the source-language reference.

Design:
- Long-lived per-database connections (singleton dict keyed by db name)
  on python and mcp-server. The first impl.sql step that references a
  database opens its connection lazily; subsequent steps reuse it.
  Connections are closed at process exit via `atexit`.
- A per-connection threading.Lock serialises access — sqlite3 is
  single-thread by default, and most DB-API drivers serialise queries
  per connection anyway, so taking the lock keeps `FOR EACH ... PARALLEL`
  branches safe without spawning extra connections.
- Bindings translation: the source-language uses `:name` everywhere;
  sqlite3 supports it natively, psycopg/pymysql want `%(name)s`. We
  translate at execute time with a single regex.
- Driver imports are lazy: `sqlite3` is stdlib and always available;
  `psycopg` and `pymysql` raise a friendly `RuntimeError` if missing.
"""

from __future__ import annotations

import atexit
import os
import re
import threading
from typing import Any
from urllib.parse import urlparse

_ENV_WHOLE = re.compile(r"^env:([A-Z_][A-Z0-9_]*)$")
_NAMED_BINDING = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


def _resolve_env(value: str) -> str:
    """`env:NAME` → os.environ[NAME] when the whole string matches; else
    return the value unchanged. Used for `RESOURCES.databases.*.url`."""
    m = _ENV_WHOLE.match(value)
    if m is None:
        return value
    name = m.group(1)
    if name not in os.environ:
        raise KeyError(f"impl.sql: env var {name!r} is not set")
    return os.environ[name]


# ---- per-database connection cache -----------------------------------------

_connections: dict[str, Any] = {}
_locks: dict[str, threading.Lock] = {}
_cache_lock = threading.Lock()


def _get_connection(db_spec: dict[str, Any]) -> tuple[Any, threading.Lock]:
    """Look up or open the connection for `db_spec['name']`. Returns the
    cached `(connection, lock)` on subsequent calls."""
    name = db_spec["name"]
    with _cache_lock:
        if name in _connections:
            return _connections[name], _locks[name]
        conn = _open_connection(db_spec)
        lock = threading.Lock()
        _connections[name] = conn
        _locks[name] = lock
        return conn, lock


def _open_connection(db_spec: dict[str, Any]) -> Any:
    driver = db_spec["driver"]
    url = _resolve_env(db_spec["url"])
    if driver == "sqlite":
        import sqlite3
        # Strip the `sqlite:///` prefix if the author used SQLAlchemy-style
        # form. `:memory:` and bare paths are accepted as-is.
        path = url
        if path.startswith("sqlite:///"):
            path = path[len("sqlite:///"):]
        elif path.startswith("sqlite://"):
            path = path[len("sqlite://"):]
        # `check_same_thread=False` lets us share the connection across
        # the daemon-thread/main-thread boundary; the per-connection lock
        # we hold around every execute() keeps it correct.
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.isolation_level = None  # autocommit
        return conn
    if driver == "postgres":
        try:
            import psycopg  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "impl.sql with driver: postgres requires the `psycopg` "
                "package. Install it with `pip install psycopg[binary]`."
            ) from e
        conn = psycopg.connect(url, autocommit=True)
        return conn
    if driver == "mysql":
        try:
            import pymysql  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "impl.sql with driver: mysql requires the `pymysql` "
                "package. Install it with `pip install pymysql`."
            ) from e
        p = urlparse(url)
        if p.scheme not in ("mysql", "mysql+pymysql"):
            raise RuntimeError(
                f"impl.sql mysql url must use the 'mysql://' scheme, got "
                f"{p.scheme!r}"
            )
        conn = pymysql.connect(
            host=p.hostname or "localhost",
            port=p.port or 3306,
            user=p.username or "",
            password=p.password or "",
            database=(p.path or "").lstrip("/") or None,
            autocommit=True,
        )
        return conn
    raise RuntimeError(
        f"impl.sql: unknown driver {driver!r} (expected one of "
        f"'sqlite' / 'postgres' / 'mysql')"
    )


def _translate_bindings(query: str, driver: str) -> str:
    """Translate `:name` bindings to the driver's native named-parameter form.

    sqlite3 supports `:name` natively, so we leave the query unchanged.
    psycopg and pymysql expect `%(name)s` (paramstyle='pyformat'). The
    `(?<!:)` lookbehind in the regex avoids touching `::cast` operators
    such as `value::int` in PostgreSQL."""
    if driver == "sqlite":
        return query
    if driver in ("postgres", "mysql"):
        return _NAMED_BINDING.sub(r"%(\1)s", query)
    raise RuntimeError(f"impl.sql: unknown driver {driver!r}")


# ---- public API ------------------------------------------------------------

def execute(
    db_spec: dict[str, Any],
    query: str,
    params: dict[str, Any],
    gives_shape: str,
) -> Any:
    """Execute `query` against the database described by `db_spec` with
    `:name` bindings supplied via `params`. Maps the result to the shape
    declared by the source-language `GIVES`:

      - `gives_shape == "list_of_records"` → list[dict[col → value]]
      - `gives_shape == "record"` → dict[col → value]; raises if rows != 1
      - `gives_shape == "primitive"` → the single column of the single row
      - DML (`cursor.description is None`) → cursor.rowcount, regardless
        of `gives_shape` (caller's GIVES should be `int`)
    """
    conn, lock = _get_connection(db_spec)
    translated = _translate_bindings(query, db_spec["driver"])
    with lock:
        cur = conn.cursor()
        try:
            cur.execute(translated, params)
            if cur.description is None:
                # DML — autocommit mode is on for all three drivers, but
                # call commit() defensively in case the connection was
                # configured otherwise upstream.
                count = cur.rowcount
                try:
                    conn.commit()
                except Exception:
                    pass
                return count
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        finally:
            cur.close()

    if gives_shape == "list_of_records":
        return [dict(zip(cols, row, strict=True)) for row in rows]
    if gives_shape == "record":
        if len(rows) != 1:
            raise RuntimeError(
                f"impl.sql: GIVES expects exactly one row, got {len(rows)} "
                f"(db={db_spec['name']!r})"
            )
        return dict(zip(cols, rows[0], strict=True))
    if gives_shape == "primitive":
        if len(rows) != 1 or len(cols) != 1:
            raise RuntimeError(
                f"impl.sql: GIVES expects a single column on a single row, "
                f"got {len(rows)} rows x {len(cols)} columns "
                f"(db={db_spec['name']!r})"
            )
        return rows[0][0]
    raise RuntimeError(
        f"impl.sql: unknown gives_shape {gives_shape!r} "
        f"(expected 'list_of_records' | 'record' | 'primitive')"
    )


# ---- atexit cleanup --------------------------------------------------------

def _shutdown() -> None:  # pragma: no cover — atexit-only
    with _cache_lock:
        for name, conn in list(_connections.items()):
            try:
                conn.close()
            except Exception:
                pass
            _connections.pop(name, None)
            _locks.pop(name, None)


atexit.register(_shutdown)
