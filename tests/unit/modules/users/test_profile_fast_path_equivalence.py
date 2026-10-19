"""F1 golden-output equivalence tests for the /users/me profile fast path.

Oracle: for the same user row, the serialization produced by the
principal fast path in ``UserService.resolve_current_profile`` must be
byte-identical (dict equality of the ``mode="json"`` dumps) to the
serialization produced by the pre-existing cache load path
(``UserService.get``), for every ``UserReadPublic`` field.

The tests are pure-Pydantic: no DB, no Redis. They construct the exact
payload shape ``CRUDUser.get_user_with_tier_eager`` builds (the bearer
auth source of truth) and the exact dict shape ``UserService.get``
returns on a cache miss (its ``UserRead`` revalidation output), then
assert the two serialization paths agree.

The comparison is on the SERIALIZED form: the fast path hands back the
``UserRead`` itself and ``_as_json_dict`` performs the ``mode="json"``
dump here (FastAPI_saas-wsiy), because the wire output is the contract,
not the container the service happens to return.
"""

from __future__ import annotations

import asyncio
import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

from src.app.core.principal import AuthenticatedUser
from src.app.modules.users.directory import UserDirectory
from src.app.modules.users.schemas import UserRead, UserReadPublic
from src.app.modules.users.service import UserService

CREATED_AT = datetime(2026, 1, 15, 12, 30, 0, tzinfo=UTC)
UPDATED_AT = datetime(2026, 9, 19, 8, 0, 0, tzinfo=UTC)


def _auth_payload(**overrides: Any) -> dict[str, Any]:
    """Payload exactly as built by CRUDUser.get_user_with_tier_eager."""
    tenant_id = uuid_pkg.uuid4()
    tier_id = 3
    payload: dict[str, Any] = {
        "id": 1,
        "created_at": CREATED_AT,
        "updated_at": UPDATED_AT,
        "name": "Ada Lovelace",
        "username": "ada",
        "email": "ada@example.com",
        "profile_image_url": "https://example.com/ada.png",
        "tenant_id": tenant_id,
        "tier_id": tier_id,
        "hashed_password": "x",
        "is_superuser": False,
        "is_deleted": False,
        "is_active": True,
        "is_email_verified": True,
        "token_version": 7,
        "tier": {"id": tier_id, "created_at": CREATED_AT, "updated_at": UPDATED_AT, "name": "pro"},
    }
    payload.update(overrides)
    return payload


def _profile_cache_miss_dict(base: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """Dict exactly as returned by UserService.get on a profile-cache miss.

    Mirrors ``UserRead.model_validate(orm_row).model_dump(mode="json")``
    over UserReadPublic + BaseRead + UserRead fields.
    """
    row = {
        "id": base["id"],
        "created_at": base["created_at"],
        "updated_at": base["updated_at"],
        "name": base["name"],
        "username": base["username"],
        "email": base["email"],
        "profile_image_url": base["profile_image_url"],
        "tier_id": base["tier_id"],
        "tenant_id": base["tenant_id"],
        "is_superuser": base["is_superuser"],
        "is_email_verified": base["is_email_verified"],
        "is_active": base["is_active"],
        "uuid": base.get("uuid", uuid_pkg.uuid4()),
        "is_deleted": base["is_deleted"],
        "token_version": base["token_version"],
    }
    row.update(overrides)
    return UserRead.model_validate(row).model_dump(mode="json")


def _as_json_dict(resolved: Any) -> dict[str, Any]:
    """The ``mode="json"`` mapping a resolved profile serializes to.

    ``resolve_current_profile`` returns a ``UserRead`` on the principal fast
    path (it used to return that model's ``mode="json"`` dump; FastAPI_saas-wsiy
    stopped the service dumping so the response model does not validate the
    email a second time) and a raw dict on the load path. These tests compare
    the SERIALIZATION, which is what the oracle is about, so the container is
    normalized here rather than inside the service.
    """
    return resolved.model_dump(mode="json") if isinstance(resolved, UserRead) else dict(resolved)


# ---------------------------------------------------------------------------
# Static contract guards (keep the isomorphism true as the code evolves)
# ---------------------------------------------------------------------------


class TestFastPathPreconditions:
    def test_principal_accepts_full_auth_payload(self) -> None:
        """The principal must keep every UserReadPublic input the auth
        payload provides; extra="ignore" silently dropping one would
        silently disable the fast path again."""
        principal = AuthenticatedUser(**_auth_payload())
        missing = UserReadPublic.model_fields.keys() - principal.model_fields.keys()
        assert not missing, f"AuthenticatedUser dropped profile inputs: {sorted(missing)}"

    def test_resolve_current_profile_takes_fast_path(self) -> None:
        """With a fully-populated principal, the fast path must fire:
        no DB session and no Redis cache access may occur."""
        service = UserService.__new__(UserService)  # bypass __init__: db must stay untouched
        principal = AuthenticatedUser(**_auth_payload())

        # No db attribute: any self.db use in resolve_current_profile raises.
        result = asyncio.run(service.resolve_current_profile(principal))

        assert result is not None
        fast = _as_json_dict(result)
        assert fast["name"] == "Ada Lovelace"
        assert fast["email"] == "ada@example.com"
        assert fast["profile_image_url"] == "https://example.com/ada.png"
        assert "hashed_password" not in fast
        assert "token_version" not in fast


# ---------------------------------------------------------------------------
# Golden-output equivalence (the oracle)
# ---------------------------------------------------------------------------


class TestFastPathEquivalence:
    def test_fast_path_matches_profile_cache_path(self) -> None:
        """Byte-identical (mode=json) output: principal fast path vs the
        dict the profile cache load path returns."""
        base = _auth_payload()
        principal = AuthenticatedUser(**base)
        service = UserService.__new__(UserService)

        fast = asyncio.run(service.resolve_current_profile(principal))
        assert fast is not None

        golden = _profile_cache_miss_dict(base)
        assert _as_json_dict(fast) == golden

    def test_equivalence_survives_edge_field_values(self) -> None:
        """Unicode, boundary lengths, nulls — outputs must stay identical.

        Note: ``profile_image_url=None`` is deliberately NOT a case — the
        column is non-nullable with a default, so neither path can ever
        observe it from a real row (UserRead rejects it on both sides).
        """
        cases = [
            {"name": "Al"},
            {"name": "Zoë 🌸"},
            {"updated_at": None},
            {"is_email_verified": False, "is_active": False},
            {"tier": None, "tier_id": None},
            {"profile_image_url": "https://example.com/p?x=1&y=2#frag"},
        ]
        service = UserService.__new__(UserService)
        for overrides in cases:
            base = _auth_payload(**overrides)
            principal = AuthenticatedUser(**base)
            fast = asyncio.run(service.resolve_current_profile(principal))
            golden = _profile_cache_miss_dict(base)
            assert _as_json_dict(fast) == golden, f"divergence for overrides={overrides}"

    def test_partial_principal_still_falls_back_to_load_path(self) -> None:
        """A principal constructed without profile fields (tests, partial
        payloads) must keep resolving via the profile load path — the
        fallback contract is unchanged."""
        service = UserService.__new__(UserService)
        service.db = AsyncMock()  # the load path needs a session
        principal = AuthenticatedUser(id=1, email="n@e.com")  # no profile fields

        async def _by_id_none(user_id: Any) -> None:
            return None

        # The load path reads through UserDirectory now; the one-line
        # UserService.get forward it used to call is gone.
        with patch.object(UserDirectory, "by_id", _by_id_none):
            result = asyncio.run(service.resolve_current_profile(principal))

        assert result is None

    def test_unserializable_principal_returns_none(self) -> None:
        """A principal whose profile fields exist but fail UserRead
        validation keeps the warn+None contract (no raise) on the fast
        path. Such a principal cannot come from a real user row (the
        column contract forbids it) — this pins the graceful-degradation
        behavior if an invalid state is ever constructed.
        """
        service = UserService.__new__(UserService)

        class Broken:
            # Present attributes → hasattr gate passes → validation inside
            # the try block fails (name must be a string) → warn + None.
            id = 1
            username = "broken"
            email = "broken@example.com"
            name = 123
            profile_image_url = "https://example.com/broken.png"
            created_at = datetime.now(UTC)
            tenant_id = uuid_pkg.uuid4()

        assert asyncio.run(service.resolve_current_profile(Broken())) is None
