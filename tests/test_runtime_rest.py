"""Unit tests for clio.runtime.rest helpers.

These exercise the helpers used by emitted impl.rest steps (templating,
content-type inference, retry classification, backoff).
"""

from __future__ import annotations

import pytest
import requests

from clio.runtime import rest as _rest

# --- subst -----------------------------------------------------------------


def test_subst_replaces_var():
    assert _rest.subst("${name}", {"name": "alice"}) == "alice"


def test_subst_inline_var_in_text():
    assert _rest.subst("hello ${name}!", {"name": "alice"}) == "hello alice!"


def test_subst_multiple_vars():
    out = _rest.subst("${a}-${b}", {"a": "1", "b": "2"})
    assert out == "1-2"


def test_subst_full_env(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "abc")
    assert _rest.subst("env:MY_TOKEN", {}) == "abc"


def test_subst_inline_env_is_plain_text(monkeypatch):
    # Inline `env:` does NOT trigger env substitution — only the whole-string form.
    monkeypatch.setenv("MY_TOKEN", "abc")
    assert _rest.subst("Bearer env:MY_TOKEN", {}) == "Bearer env:MY_TOKEN"


def test_subst_missing_var_raises():
    with pytest.raises(KeyError, match="address"):
        _rest.subst("${address}", {})


def test_subst_missing_env_raises(monkeypatch):
    monkeypatch.delenv("UNSET_FOO", raising=False)
    with pytest.raises(KeyError, match="UNSET_FOO"):
        _rest.subst("env:UNSET_FOO", {})


def test_subst_var_is_int_coerced_to_str():
    assert _rest.subst("/items/${id}", {"id": 42}) == "/items/42"


# --- render_dict -----------------------------------------------------------


def test_render_dict_substitutes_strings():
    out = _rest.render_dict(
        (("name", "${who}"), ("limit", 10), ("flag", True)),
        {"who": "alice"},
    )
    assert out == {"name": "alice", "limit": 10, "flag": True}


def test_render_dict_preserves_non_strings():
    out = _rest.render_dict((("count", 0), ("none", None)), {})
    assert out == {"count": 0, "none": None}


# --- content_type_for_path -------------------------------------------------


def test_content_type_for_path_known_extensions():
    assert _rest.content_type_for_path("payload.json") == "application/json"
    assert _rest.content_type_for_path("dir/file.xml") == "application/xml"
    assert _rest.content_type_for_path("notes.txt") == "text/plain"
    assert _rest.content_type_for_path("page.HTML") == "text/html"


def test_content_type_for_path_unknown_extension():
    assert _rest.content_type_for_path("blob.bin") == "application/octet-stream"
    assert _rest.content_type_for_path("noext") == "application/octet-stream"


# --- read_file_body --------------------------------------------------------


def test_read_file_body_utf8_is_templated(tmp_path):
    p = tmp_path / "payload.json"
    p.write_text('{"name": "${who}"}')
    data, ct = _rest.read_file_body(str(p), {"who": "alice"})
    assert data == b'{"name": "alice"}'
    assert ct == "application/json"


def test_read_file_body_binary_passes_through(tmp_path):
    p = tmp_path / "blob.bin"
    p.write_bytes(b"\xff\xfe\x00\x01")
    data, ct = _rest.read_file_body(str(p), {})
    assert data == b"\xff\xfe\x00\x01"
    assert ct == "application/octet-stream"


# --- is_retryable_response -------------------------------------------------


@pytest.mark.parametrize("code,on,expected", [
    (500, ("5xx",), True),
    (503, ("5xx",), True),
    (429, ("429",), True),
    (429, ("5xx",), False),
    (404, ("5xx", "429"), False),
    (200, ("5xx", "429"), False),
])
def test_is_retryable_response(code, on, expected):
    assert _rest.is_retryable_response(code, on) is expected


# --- is_retryable_exception ------------------------------------------------


def test_timeout_is_retryable_when_in_on():
    exc = requests.exceptions.Timeout("timed out")
    assert _rest.is_retryable_exception(exc, ("timeout",)) is True


def test_timeout_not_retryable_when_not_in_on():
    exc = requests.exceptions.Timeout("timed out")
    assert _rest.is_retryable_exception(exc, ("5xx",)) is False


def test_connection_error_retryable_only_when_network_in_on():
    exc = requests.exceptions.ConnectionError("conn refused")
    assert _rest.is_retryable_exception(exc, ("network",)) is True
    assert _rest.is_retryable_exception(exc, ("5xx", "429", "timeout")) is False


# --- compute_delay ---------------------------------------------------------


def test_compute_delay_exponential():
    # base=0.1, attempt 1 → 0.1, 2 → 0.2, 3 → 0.4, capped at cap.
    assert _rest.compute_delay(1, 0.1, 30.0, "exponential") == pytest.approx(0.1)
    assert _rest.compute_delay(2, 0.1, 30.0, "exponential") == pytest.approx(0.2)
    assert _rest.compute_delay(3, 0.1, 30.0, "exponential") == pytest.approx(0.4)


def test_compute_delay_exponential_capped():
    assert _rest.compute_delay(20, 0.1, 1.0, "exponential") == pytest.approx(1.0)


def test_compute_delay_constant():
    assert _rest.compute_delay(1, 0.5, 30.0, "constant") == pytest.approx(0.5)
    assert _rest.compute_delay(5, 0.5, 30.0, "constant") == pytest.approx(0.5)


def test_compute_delay_constant_capped():
    assert _rest.compute_delay(1, 50.0, 10.0, "constant") == pytest.approx(10.0)


# --- parse_retry_after -----------------------------------------------------


def test_parse_retry_after_seconds():
    assert _rest.parse_retry_after("3") == 3.0
    assert _rest.parse_retry_after("0.5") == 0.5


def test_parse_retry_after_none():
    assert _rest.parse_retry_after(None) is None


def test_parse_retry_after_negative_clamped_to_zero():
    assert _rest.parse_retry_after("-1") == 0.0


def test_parse_retry_after_invalid_returns_none():
    # HTTP-date format is not supported in v1.
    assert _rest.parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
