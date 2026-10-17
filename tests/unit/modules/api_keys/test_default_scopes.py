"""A key minted with the documented default must be able to call something.

Bead: FastAPI_saas-ns82. Found by M-7 (see
perf-results/RESULT-20261017-m7/m7_apikey_endpoint_baseline.json), which
minted a key with ``POST /api/v1/api-keys`` using only a name and then got::

    Forbidden action error='API key missing required scope: read'
    path=/api/v1/users/me

``APIKeyBase.scopes`` used to default to ``None``, ``create_api_key`` persisted
that NULL (``scopes_json`` is only written when the value is truthy), and
``api_key_scope_allows`` denies an empty scope set everywhere by design. So
the endpoint's own default produced a key that could do nothing at all.

The default is now ``["read"]``: the least privilege that makes a minted key
usable. This file pins BOTH halves of that decision:

* an omitted ``scopes`` field yields a key that passes the scope gate for a
  read, and
* an EXPLICIT ``[]`` or ``null`` still yields a key that is denied
  everywhere.

The second half is the important one. The fail-closed policy is deliberate
(``api_key_scope_allows``'s own comment: "Keys with no scopes are denied
everywhere") and is pinned by ``tests/unit/api/deps/test_auth.py``. Widening
it would be a privilege grant dressed as a bug fix, so this fix leaves it
exactly as it is and moves only the default.
"""

import json

import pytest

from src.app.api.deps.auth import API_KEY_SCOPE_WILDCARD, api_key_scope_allows
from src.app.modules.api_keys.schemas import APIKeyCreate

# The read/write scope pair `api_key_scope_allows` understands:
# `_required_api_key_scope` maps GET/HEAD/OPTIONS to "read" and everything
# else to "write", and "write" satisfies a read.
READ = "read"
WRITE = "write"

# The path M-7 measured.
MEASURED_GET = "/api/v1/users/me"


def test_the_documented_default_is_read_only() -> None:
    """Omitting `scopes` must not mint a key that is denied everywhere."""
    created = APIKeyCreate(name="prod")

    assert created.scopes == [READ]


def test_the_default_default_is_not_the_wildcard() -> None:
    """A default may grant the least privilege, never the most.

    `API_KEY_SCOPE_WILDCARD` admits every method on every path. If this ever
    becomes the default, every integration key silently gains delete.
    """
    created = APIKeyCreate(name="prod")

    assert API_KEY_SCOPE_WILDCARD not in (created.scopes or [])


def test_the_default_passes_the_scope_gate_for_the_measured_path() -> None:
    """The end of the M-7 failure: a GET on the path it actually called."""
    created = APIKeyCreate(name="prod")

    assert api_key_scope_allows(created.scopes, "GET", MEASURED_GET) is True


def test_the_default_is_read_only_in_practice() -> None:
    """`read` must not quietly admit a mutation."""
    created = APIKeyCreate(name="prod")

    assert api_key_scope_allows(created.scopes, "POST", MEASURED_GET) is False
    assert api_key_scope_allows(created.scopes, "DELETE", MEASURED_GET) is False


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param({"name": "prod"}, [READ], id="field-omitted"),
        pytest.param({"name": "prod", "scopes": ["read:posts"]}, ["read:posts"], id="scoped-supplied"),
        pytest.param({"name": "prod", "scopes": [READ, WRITE]}, [READ, WRITE], id="read-write-supplied"),
        pytest.param({"name": "prod", "scopes": []}, [], id="explicit-empty-stays-empty"),
        pytest.param({"name": "prod", "scopes": None}, None, id="explicit-null-stays-null"),
    ],
)
def test_only_an_omitted_field_takes_the_default(body: dict[str, object], expected: list[str] | None) -> None:
    """A caller who states their scopes is never overwritten; only silence is.

    The last two cases are the load-bearing ones. Explicitly asking for no
    scopes is a statement, not an omission, and silently upgrading it to
    `["read"]` would grant access the caller did not ask for.
    """
    assert APIKeyCreate(**body).scopes == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("scopes", "allowed"),
    [
        pytest.param([], False, id="explicit-empty-denied-everywhere"),
        pytest.param(None, False, id="explicit-null-denied-everywhere"),
    ],
)
def test_an_explicitly_scopeless_key_is_still_denied_everywhere(scopes: list[str] | None, allowed: bool) -> None:
    """The fail-closed policy is untouched by this fix.

    These are the same assertions `tests/unit/api/deps/test_auth.py` pins on
    the policy function; they are repeated here against the VALUE the minting
    endpoint can actually produce, which is the pair of facts this bug is made
    of.
    """
    assert api_key_scope_allows(scopes, "GET", MEASURED_GET) is allowed
    assert api_key_scope_allows(scopes, "POST", MEASURED_GET) is allowed


def test_the_default_survives_the_persistence_encoding() -> None:
    """`create_api_key` persists scopes only when they are truthy.

    With the old `None` default this branch was skipped and the row stored
    NULL, so the key came back out of the cache and the database with no
    scopes at all. The default has to be truthy for the row to carry it.
    """
    created = APIKeyCreate(name="prod")

    assert json.loads(json.dumps(created.scopes)) == [READ]
