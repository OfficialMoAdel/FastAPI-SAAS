"""Passkey credential shape bounds (auth.mfa.unbounded_credential_dict).

The passkey verify bodies accept a client-supplied ``credential`` object. The
body-limit middleware (``settings.app.MAX_BODY_SIZE``) bounds the bytes, but a
single body under that cap can still carry a 200k-key dict or a 10k-deep nest,
and both reach ``parse_registration_credential_json`` / the log sanitizer / the
DB write. These tests pin the shape bound that closes that.

The bound must reject hostile shapes AND keep accepting every real browser
credential — a real assertion is a fixed 4-key object with a flat ``response``
map, so a legitimate payload is nowhere near the caps.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from src.app.modules.auth.schemas import (
    PASSKEY_CREDENTIAL_MAX_DEPTH,
    PASSKEY_CREDENTIAL_MAX_ENTRIES,
    PasskeyLoginVerifyRequest,
    PasskeyRegistrationVerifyRequest,
    _max_nesting_depth,
)
from src.app.shared.exceptions import ValidationError as DomainValidationError

REGISTRATION_ID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"


def _real_registration_credential() -> dict[str, Any]:
    """Exactly what a browser posts for navigator.credentials.create()."""
    return {
        "id": "SZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzzuoMdl2M",
        "rawId": "SZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzzuoMdl2M",
        "response": {
            "clientDataJSON": "eyJ0eXBlIjoid2ViYXV0aG4uY3JlYXRlIn0",
            "attestationObject": "o2NmbXRkbm9uZWdhdHRTdG10oGhhdXRo",
            "authenticatorData": "SZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzz",
        },
        "type": "public-key",
        "clientExtensionResults": {},
    }


def _real_assertion_credential() -> dict[str, Any]:
    """Exactly what a browser posts for navigator.credentials.get()."""
    return {
        "id": "SZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzzuoMdl2M",
        "rawId": "SZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzzuoMdl2M",
        "response": {
            "clientDataJSON": "eyJ0eXBlIjoid2ViYXV0aG4uZ2V0In0",
            "authenticatorData": "SZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzz",
            "signature": "MEUCIQDx0Kz",
            "userHandle": "MTIzNDU2Nzg5MA",
        },
        "type": "public-key",
        "clientExtensionResults": {},
    }


def _nest(depth: int, leaf: object = "x") -> dict[str, Any]:
    """A dict chain whose deepest container sits at exactly ``depth``."""
    node: dict[str, Any] = {"leaf": leaf}
    for _ in range(depth - 1):
        node = {"n": node}
    return node


# --- the documented configuration is the one wired into the schema ---------


def test_max_body_size_is_the_middleware_cap_the_bounds_are_measured_against() -> None:
    """The bound only makes sense relative to the real byte cap.

    ``MAX_BODY_SIZE`` is a deployment knob, so the test pins the *relationship*
    (the entry/depth caps exist because the byte cap is far too loose to bound
    shape), not the 10 MiB literal.
    """
    from src.app.core.config import settings
    from src.app.main import app
    from src.app.middleware.body_limit import DEFAULT_MAX_BODY_SIZE, RequestBodyLimitMiddleware

    assert settings.app.MAX_BODY_SIZE == DEFAULT_MAX_BODY_SIZE
    registered = next(m for m in app.user_middleware if m.cls is RequestBodyLimitMiddleware)
    assert registered.kwargs["max_body_size"] == settings.app.MAX_BODY_SIZE
    # A flat dict of PASSKEY_CREDENTIAL_MAX_ENTRIES max-length strings is still
    # far under the byte cap, i.e. the byte cap cannot stand in for the bound.
    worst_flat = {f"k{i}": "v" * 10_000 for i in range(PASSKEY_CREDENTIAL_MAX_ENTRIES)}
    assert len(str(worst_flat)) < settings.app.MAX_BODY_SIZE


# --- legitimate traffic must still pass ------------------------------------


def test_real_registration_credential_accepted() -> None:
    body = PasskeyRegistrationVerifyRequest(
        credential=_real_registration_credential(),
        registration_id=REGISTRATION_ID,
        password="StepUpPassword123!",
    )
    assert body.credential["type"] == "public-key"
    assert body.credential["response"]["attestationObject"] == "o2NmbXRkbm9uZWdhdHRTdG10oGhhdXRo"


def test_real_assertion_credential_accepted() -> None:
    body = PasskeyLoginVerifyRequest(
        credential=_real_assertion_credential(),
        challenge_id="3a9f1c77-1d2e-4f60-9a11-6b8e0c5d4a22",
    )
    assert body.credential["response"]["signature"] == "MEUCIQDx0Kz"


def test_credential_at_the_entry_cap_is_accepted() -> None:
    credential = {f"key{i}": i for i in range(PASSKEY_CREDENTIAL_MAX_ENTRIES)}
    body = PasskeyLoginVerifyRequest(credential=credential, challenge_id="c")
    assert len(body.credential) == PASSKEY_CREDENTIAL_MAX_ENTRIES


def test_credential_at_the_depth_cap_is_accepted() -> None:
    credential = _nest(PASSKEY_CREDENTIAL_MAX_DEPTH)
    body = PasskeyLoginVerifyRequest(credential=credential, challenge_id="c")
    assert body.credential == credential


def test_lists_count_toward_depth() -> None:
    """A list is a container too — bounding only dicts would be a hole."""
    body = PasskeyLoginVerifyRequest(
        credential={"response": [[[["deep"]]]]},  # depth 5
        challenge_id="c",
    )
    assert body.credential["response"] == [[[["deep"]]]]

def test_too_many_entries_rejected() -> None:
    credential = {f"key{i}": i for i in range(PASSKEY_CREDENTIAL_MAX_ENTRIES + 1)}
    with pytest.raises(ValidationError) as exc:
        PasskeyLoginVerifyRequest(credential=credential, challenge_id="c")
    assert exc.value.errors()[0]["type"] == "too_long"
    assert exc.value.errors()[0]["loc"] == ("credential",)


def test_credential_at_the_depth_cap_is_accepted_regression() -> None:
    """A real credential nests 2 deep; the cap must not be off by one either way."""
    credential = {"id": "a", "response": {"clientDataJSON": "b", "signature": "c"}}
    assert _max_nesting_depth(credential) == 2 < PASSKEY_CREDENTIAL_MAX_DEPTH


def test_too_deep_credential_rejected_on_registration() -> None:
    credential = _nest(PASSKEY_CREDENTIAL_MAX_DEPTH + 1)
    with pytest.raises(DomainValidationError, match="levels deep"):
        PasskeyRegistrationVerifyRequest(
            credential=credential, registration_id=REGISTRATION_ID, password="pw"
        )


def test_too_deep_credential_rejected_on_login() -> None:
    with pytest.raises(DomainValidationError, match="levels deep"):
        PasskeyLoginVerifyRequest(credential=_nest(5000), challenge_id="c")


def test_deeply_deep_nest_is_rejected_without_recursion_error() -> None:
    """A 20k-deep nest is a clean rejection, not a RecursionError.

    The depth walk is iterative precisely so the payload it exists to reject
    cannot blow the stack; this pins that. The error is the domain
    ValidationError (see _bound_passkey_credential_depth) rather than a pydantic
    one, so no caller ever has to serialize this structure.
    """
    credential: dict[str, Any] = {"leaf": 1}
    for _ in range(20_000):
        credential = {"n": credential}
    with pytest.raises(DomainValidationError, match="levels deep"):
        PasskeyLoginVerifyRequest(credential=credential, challenge_id="c")


def test_depth_violation_is_not_a_pydantic_validation_error() -> None:
    """The depth bound must not become a pydantic error.

    Pydantic errors carry the offending value as `input`, and FastAPI echoes it
    when building the 422 — a recursive encode that 500s on exactly the payloads
    this bound exists to reject (measured: RecursionError from nesting ~998).
    Escaping pydantic's error machinery is the point; this pins it.
    """
    with pytest.raises(DomainValidationError) as exc:
        PasskeyLoginVerifyRequest(credential=_nest(1000), challenge_id="c")
    assert not isinstance(exc.value, ValidationError)
    assert f"at most {PASSKEY_CREDENTIAL_MAX_DEPTH} levels deep, got 1000" in str(exc.value)


def test_wide_dict_rejected_without_traversal() -> None:
    """200k keys under the byte cap — rejected by the entry cap, fast."""
    credential = {f"k{i}": 1 for i in range(200_000)}
    with pytest.raises(ValidationError) as exc:
        PasskeyLoginVerifyRequest(credential=credential, challenge_id="c")
    assert exc.value.errors()[0]["type"] == "too_long"


# --- the depth helper itself ------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({}, 1),
        ({"a": 1, "b": "x"}, 1),
        ({"a": {"b": 1}}, 2),
        ({"a": {"b": {"c": 1}}}, 3),
        ({"a": [[1]]}, 3),
        ({"a": [{"b": 1}]}, 3),
        ({"a": ({"b": 1},)}, 3),
        ({"a": [{"b": [{"c": 1}]}]}, 5),
    ],
)
def test_max_nesting_depth(value: object, expected: int) -> None:
    assert _max_nesting_depth(value) == expected


# --- the bound is published to API clients ---------------------------------


@pytest.mark.parametrize(
    "model", [PasskeyRegistrationVerifyRequest, PasskeyLoginVerifyRequest]
)
def test_entry_cap_is_published_as_max_properties(model: type) -> None:
    """max_length on a dict surfaces as maxProperties — SDKs can see the cap."""
    assert model.model_json_schema()["properties"]["credential"]["maxProperties"] == PASSKEY_CREDENTIAL_MAX_ENTRIES
