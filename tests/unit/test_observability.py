import pytest
from ia_observability import (
    RequestContext,
    clear_request_context,
    get_request_context,
    pseudonymize,
    set_request_context,
)


def test_request_context_lifecycle() -> None:
    context = RequestContext(
        request_id="request-1",
        trace_id="trace-1",
        tenant_pseudonym="tenant-hash",
        user_pseudonym="user-hash",
    )
    token = set_request_context(context)
    assert get_request_context() == context
    clear_request_context(token)
    assert get_request_context() is None


def test_pseudonymize_is_stable_and_keyed() -> None:
    first = pseudonymize("tenant-a", b"key-a")
    second = pseudonymize("tenant-a", b"key-a")
    other = pseudonymize("tenant-a", b"key-b")
    assert first == second
    assert first != other
    assert len(first) == 32


@pytest.mark.parametrize(("identifier", "key"), [("", b"key"), ("value", b"")])
def test_pseudonymize_rejects_empty_inputs(identifier: str, key: bytes) -> None:
    with pytest.raises(ValueError):
        pseudonymize(identifier, key)
