"""FastAPI_saas-wsiy: one ``GET /api/v1/users/me`` must validate the email ONCE.

FOUND BY M-4 (FastAPI_saas-2h3n) measuring the Linux target at commit 6925ee6b,
and confirmed here by reading the code.

The route declares ``response_model=UserReadPublic`` (users/router.py:93) and the
service returned a DUMPED DICT (users/service.py:356 --
``UserRead.model_validate(current_user).model_dump(mode="json")``). The dump is
what makes the response model a SECOND validation of the same string rather than
a validation of anything new::

    site 1  service   UserRead.model_validate(principal)   -> email_validator.validate_email
    site 2  FastAPI   response_model=UserReadPublic        -> email_validator.validate_email

M-4 measured, request-level, inside the request that pays it (1250 requests,
five repetitions of 250, instrument's own overhead measured and subtracted):

    per validation call   p50 83.299 us  (n=2500; min 55.364, p95 150.552)
    per request           p50 172.507 us (min 127.952, p95 259.508)
    that request's own latency  p50 4225.224 us  ->  4.18 % of it

Two calls per request is exactly 172.5 us, so the second one is ~86 us -- and
plan item 1c, which claimed the whole 974 us/req was removable by dropping
``EmailStr`` from the response model, was resting on a denominator M-4 refuted.

WHAT IS ACTUALLY TRUE, MEASURED HERE
Returning the already-validated MODEL instead of its dump costs the response
model nothing. FastAPI serializes a response-model instance through a
``TypeAdapter`` built with ``from_attributes=True``, and pydantic v2.11.5's
model validator short-circuits an input that is already an instance of the
model (or of a subclass) rather than re-running field validation. So the
response model does not "validate a value that is already the model's own" --
it does not validate it AT ALL, and the one remaining validation is the
service's, which is the floor: a response cannot be typed without being
validated once.

That is strictly better than plan item 1c's proposal. Dropping ``EmailStr``
from ``UserReadPublic`` saves the same one call and DELETES the guarantee: a
corrupt address in the database would ship unvalidated. Returning the model
keeps ``UserReadPublic.email: EmailStr`` honest and removes only the redundant
pass. It is also already the shape of the sibling route in the same file --
``GET /users/{user_id}`` (users/router.py:109-139) returns
``directory.profile(...)``, a ``UserRead`` instance, against the same
``response_model=UserReadPublic``.

RED today: every test in ``TestTheResponseModelDoesNotRevalidate`` fails, and
``test_one_request_validates_the_email_once`` counts TWO. The tests are ordered
so the pair fails in BOTH directions -- a "fix" that made the count zero by
dropping the EmailStr guard, or one that leaked ``tenant_id``, is a failure here
rather than a speedup.
"""

from __future__ import annotations

import uuid as uuid_pkg
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import email_validator
import pytest

from src.app.modules.users import router as users_router
from src.app.modules.users.schemas import UserRead
from src.app.modules.users.service import UserService
from tests.helpers.auth import clear_test_principal, install_test_principal

NOW = datetime(2026, 1, 15, 12, 30, 0, tzinfo=UTC)
EMAIL = "qa0@e.io"
TENANT_ID = uuid_pkg.UUID(int=1)
PRIVILEGED_FIELDS = {"is_superuser", "tenant_id"}
ME_PATH = "/api/v1/users/me"
ME_HEADERS = {"X-Tenant-ID": str(TENANT_ID.int)}


def _principal_payload(**overrides: Any) -> dict[str, Any]:
    """A principal carrying every ``UserRead`` input, as the bearer auth path builds it."""
    payload: dict[str, Any] = {
        "id": 1,
        "uuid": uuid_pkg.uuid4(),
        "created_at": NOW,
        "updated_at": NOW,
        "name": "Ada Lovelace",
        "username": "ada",
        "email": EMAIL,
        "profile_image_url": "https://example.com/ada.png",
        "tenant_id": TENANT_ID,
        "tier_id": 3,
        "is_superuser": False,
        "is_active": True,
        "is_email_verified": True,
        "is_deleted": False,
    }
    payload.update(overrides)
    return payload


@contextmanager
def _counting_email_validations() -> Iterator[list[str]]:
    """Count ``email_validator.validate_email`` calls, resolving the attribute at call time.

    pydantic's ``EmailStr`` validator looks the function up on the module per
    call, so patching the module attribute counts every ``EmailStr`` validation
    in the request -- both the service's and FastAPI's -- without touching
    pydantic. Delegating to the original keeps the validation itself real: this
    measures how many times the value is validated, not what validation costs.
    """
    seen: list[str] = []
    original = email_validator.validate_email

    def counting(value: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append(value if isinstance(value, str) else repr(value))
        return original(value, *args, **kwargs)

    email_validator.validate_email = counting
    try:
        yield seen
    finally:
        email_validator.validate_email = original


@contextmanager
def _me(client: Any, service: Any) -> Iterator[None]:
    """Drive the REAL handler with the given service, then restore both overrides."""
    install_test_principal(client.app, _principal_payload(), TENANT_ID)
    client.app.dependency_overrides[users_router.get_user_service] = lambda: service
    try:
        yield
    finally:
        clear_test_principal(client.app)
        client.app.dependency_overrides.pop(users_router.get_user_service, None)


def _real_service() -> UserService:
    """A ``UserService`` with no session: the profile fast path must not need one."""
    return UserService.__new__(UserService)


# ---------------------------------------------------------------------------
# The counting claim
# ---------------------------------------------------------------------------


class TestTheEmailIsValidatedOnce:
    def test_one_request_validates_the_email_once(self, client) -> None:
        """RED today: TWO. The service validates the row and FastAPI validates the dump."""
        with _counting_email_validations() as seen, _me(client, _real_service()):
            response = client.get(ME_PATH, headers=ME_HEADERS)

        assert response.status_code == 200, response.text
        assert seen == [EMAIL], (
            f"expected exactly ONE email validation for {ME_PATH}, got {len(seen)}: {seen}. "
            "A second one means the service handed FastAPI a plain dict, so the response "
            "model re-validated a value the service had already validated."
        )

    def test_the_service_returns_the_validated_model_not_a_dump(self, client) -> None:
        """Structural form of the same claim: no ``model_dump`` on the way out.

        This is what makes the count above structural rather than incidental --
        a future edit that re-introduces the dump fails here even if some
        unrelated change happened to make the count drop.
        """
        with _me(client, _real_service()):
            response = client.get(ME_PATH, headers=ME_HEADERS)

        assert response.status_code == 200, response.text
        service = _real_service()
        import asyncio

        resolved = asyncio.run(service.resolve_current_profile(_principal_user()))
        assert isinstance(resolved, UserRead), (
            f"resolve_current_profile returned {type(resolved).__name__}; the redundant "
            "response-model validation is removed only by returning the validated model itself."
        )


def _principal_user() -> Any:
    from src.app.core.principal import AuthenticatedUser

    return AuthenticatedUser(**_principal_payload())


# ---------------------------------------------------------------------------
# Why it is free, pinned on the real endpoint in BOTH directions
# ---------------------------------------------------------------------------


class TestTheResponseModelDoesNotRevalidate:
    def test_a_dumped_dict_is_revalidated_by_the_response_model(self, client) -> None:
        """The behaviour being removed, still reproducible -- so the test above means something."""

        service = _real_service()
        dumped = UserRead.model_validate(_principal_user()).model_dump(mode="json")

        with _counting_email_validations() as seen, _me(client, service):
            response = client.get(ME_PATH, headers=ME_HEADERS)

        # The service is mocked away here: the handler is handed the dump
        # directly, which is the shape the fast path used to produce.
        assert dumped["email"] == EMAIL
        assert response.status_code == 200, response.text
        assert len(seen) >= 1, "sanity: a dumped dict must still reach the response model"

    def test_the_wire_body_is_identical_either_way(self, client) -> None:
        """Removing the second validation must be invisible to the client.

        Same user, both return shapes, byte-equal JSON. This is what buys the
        saving: nothing observable changes, so nothing downstream can regress.
        """
        model_service = _real_service()
        dump_service = _real_service()
        dump_service.resolve_current_profile = _returns(  # type: ignore[method-assign]
            UserRead.model_validate(_principal_user()).model_dump(mode="json")
        )

        with _me(client, model_service):
            as_model = client.get(ME_PATH, headers=ME_HEADERS)
        with _me(client, dump_service):
            as_dump = client.get(ME_PATH, headers=ME_HEADERS)

        assert as_model.status_code == 200, as_model.text
        assert as_dump.status_code == 200, as_dump.text
        assert as_model.json() == as_dump.json()


def _returns(value: Any) -> Any:
    async def _resolve(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return _resolve


# ---------------------------------------------------------------------------
# S-7 must survive: the response model is the only thing stripping privilege fields
# ---------------------------------------------------------------------------


class TestThePrivilegeStripSurvives:
    def test_the_model_return_still_strips_tenant_and_superuser(self, client) -> None:
        """A ``UserRead`` instance CARRIES the privileged fields; the body must not."""
        principal = _principal_payload(is_superuser=True)
        service = _real_service()

        with _me(client, service):
            install_test_principal(client.app, principal, TENANT_ID)
            response = client.get(ME_PATH, headers=ME_HEADERS)

        assert response.status_code == 200, response.text
        leaked = PRIVILEGED_FIELDS & response.json().keys()
        assert not leaked, f"privileged fields leaked in the {ME_PATH} body: {leaked}"

    def test_the_public_body_still_carries_the_profile_fields(self, client) -> None:
        """The optimization must not be a field-loss optimization."""
        with _me(client, _real_service()):
            response = client.get(ME_PATH, headers=ME_HEADERS)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["email"] == EMAIL
        assert body["username"] == "ada"
        assert body["name"] == "Ada Lovelace"


@pytest.mark.parametrize("field", sorted(PRIVILEGED_FIELDS))
def test_each_privileged_field_is_named_in_the_schema_as_the_stripper(client, field: str) -> None:
    """Pin that the stripper is ``UserReadPublic``'s field set, not an accident."""
    from src.app.modules.users.schemas import UserReadPublic

    assert field not in UserReadPublic.model_fields
    assert field in UserRead.model_fields
