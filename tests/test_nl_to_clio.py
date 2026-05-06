import pytest


def test_generation_error_carries_attempt_and_message():
    from clio.nl_to_clio import GenerationError
    e = GenerationError(last_attempt="STEP foo\n", last_error="line 1: oops")
    assert e.last_attempt == "STEP foo\n"
    assert e.last_error == "line 1: oops"
    assert "oops" in str(e)


def test_validate_returns_none_for_valid_source():
    from clio.nl_to_clio import _validate
    src = "STEP foo\n  MODE: exact\n"
    assert _validate(src) is None


def test_validate_returns_error_string_for_parse_error():
    from clio.nl_to_clio import _validate
    # Missing step name — should fail at parse time
    src = "STEP\n  MODE: exact\n"
    err = _validate(src)
    assert err is not None
    assert "line" in err.lower()


def test_validate_returns_error_string_for_ir_build_error():
    from clio.nl_to_clio import _validate
    # FLOW references a step that does not exist
    src = (
        "STEP a\n  MODE: exact\n"
        "FLOW f\n"
        '  nope()\n'
    )
    err = _validate(src)
    assert err is not None
