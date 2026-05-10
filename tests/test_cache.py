import json
import time

from clio.runtime import cache as cache_mod


def test_cache_key_is_deterministic():
    a = cache_mod.cache_key("s", "haiku", "p", "{}")
    b = cache_mod.cache_key("s", "haiku", "p", "{}")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_cache_key_differs_on_any_input():
    base = cache_mod.cache_key("s", "haiku", "p", "{}")
    assert cache_mod.cache_key("s2", "haiku", "p", "{}") != base
    assert cache_mod.cache_key("s", "sonnet", "p", "{}") != base
    assert cache_mod.cache_key("s", "haiku", "p2", "{}") != base
    assert cache_mod.cache_key("s", "haiku", "p", '{"x":1}') != base


def test_cache_lookup_miss_when_absent(tmp_path):
    assert cache_mod.cache_lookup(tmp_path, "step", "abc", None) is None


def test_cache_store_then_lookup_roundtrips(tmp_path):
    cache_mod.cache_store(tmp_path, "step", "abc", "haiku", "RESPONSE")
    assert cache_mod.cache_lookup(tmp_path, "step", "abc", None) == "RESPONSE"


def test_cache_lookup_ttl_fresh(tmp_path):
    cache_mod.cache_store(tmp_path, "s", "k", "m", "R")
    assert cache_mod.cache_lookup(tmp_path, "s", "k", 3600) == "R"


def test_cache_lookup_ttl_expired(tmp_path):
    cache_mod.cache_store(tmp_path, "s", "k", "m", "R")
    # Backdate the entry to simulate expiry
    f = tmp_path / "s" / "k.json"
    e = json.loads(f.read_text())
    e["created_at"] = int(time.time()) - 7200
    f.write_text(json.dumps(e))
    assert cache_mod.cache_lookup(tmp_path, "s", "k", 3600) is None


def test_cache_store_atomic_writes_via_tmp(tmp_path):
    cache_mod.cache_store(tmp_path, "s", "k", "m", "R")
    # No .tmp file should remain
    assert list((tmp_path / "s").glob("*.tmp")) == []


def test_cli_key_subcommand(tmp_path, capsys):
    cache_mod.main(["key", "step", "haiku", "prompt", "{}"])
    out = capsys.readouterr().out.strip()
    assert out == cache_mod.cache_key("step", "haiku", "prompt", "{}")


def test_cli_lookup_miss_returns_1(tmp_path):
    rc = cache_mod.main(["lookup", str(tmp_path), "step", "missing", "60"])
    assert rc == 1


def test_cli_lookup_hit_returns_0_and_prints(tmp_path, capsys):
    cache_mod.cache_store(tmp_path, "s", "k", "m", "RESP")
    rc = cache_mod.main(["lookup", str(tmp_path), "s", "k", "3600"])
    assert rc == 0
    assert capsys.readouterr().out == "RESP"


def test_cli_store_subcommand(tmp_path):
    rc = cache_mod.main(["store", str(tmp_path), "s", "k", "haiku", "BODY"])
    assert rc == 0
    assert cache_mod.cache_lookup(tmp_path, "s", "k", None) == "BODY"


def test_cli_arg_at_indirection(tmp_path, capsys):
    f = tmp_path / "prompt.txt"
    f.write_text("LARGE PROMPT")
    cache_mod.main(["key", "s", "m", f"@{f}", "{}"])
    out = capsys.readouterr().out.strip()
    assert out == cache_mod.cache_key("s", "m", "LARGE PROMPT", "{}")
