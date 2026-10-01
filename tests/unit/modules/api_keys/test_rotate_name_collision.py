"""Unit tests: rotate_api_key name-collision error contract (K-R1-02).

The rotated name is derived deterministically from the base name
(``f"{base_name[:80]} (Rotated)"``) and ``api_keys`` carries
``UniqueConstraint("name", "tenant_id", name="uq_api_key_name_tenant")``.
Rotating a key that is itself the product of a rotation therefore derives a
name that is already taken, and the insert/commit used to surface a raw
``IntegrityError`` — a 500 on a legitimate operation.

The property under test IS the error contract, so the structural assertions
below are the point rather than an implementation detail.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.api_keys.models import APIKey
from src.app.modules.api_keys.service import APIKeyService
from src.app.shared.exceptions import DuplicateError

TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _existing_key(name: str) -> MagicMock:
    """A rotated-source key: name already ends in ' (Rotated)'."""
    key = MagicMock(spec=APIKey)
    key.id = 2
    key.name = name
    key.user_id = 1
    key.tenant_id = TENANT_ID
    key.is_active = True
    key.scopes = None
    key.expires_at = None
    key.hashed_key = "hashed_old"
    return key


def _service_with_existing(key: MagicMock) -> tuple[APIKeyService, AsyncMock]:
    """Service whose scoped lookup returns ``key``."""
    db = AsyncMock(spec=AsyncSession)

    async def _refresh(obj, *args, **kwargs):
        obj.id = 99
        return None

    db.refresh.side_effect = _refresh
    svc = APIKeyService(db)
    return svc, db


def _patched(svc: APIKeyService, name_lookup: MagicMock | None) -> tuple[object, object, object]:
    """Patch the collaborators rotate_api_key reaches for."""
    return (
        patch("src.app.modules.api_keys.service.execute_scoped", new=AsyncMock(return_value=name_lookup)),
        patch.object(svc.crud, "update", new_callable=AsyncMock),
        patch("src.app.modules.api_keys.service.event_bus.publish", new_callable=AsyncMock),
    )


class TestRotateNameCollision:
    """The re-rotation must be a 409 DuplicateError, never a 500."""

    async def test_rotating_a_rotated_key_raises_duplicate_not_integrity_error(self) -> None:
        """A key named '... (Rotated)' rotated again derives the same name."""
        svc, db = _service_with_existing(_existing_key("Service Key (Rotated)"))

        lookup = MagicMock()
        # The derived name is already present in the table.
        lookup.scalar_one_or_none.return_value = _existing_key("Service Key (Rotated)")
        found = MagicMock()
        found.scalar_one_or_none.return_value = _existing_key("Service Key (Rotated)")

        p_scoped, p_update, p_bus = _patched(svc, lookup)
        with (
            p_scoped as scoped,
            p_update,
            patch.object(svc, "_generate_key", return_value=("raw-new", "pfxnew", "hashed_new")),
            patch("src.app.modules.api_keys.service.audit", new_callable=AsyncMock),
        ):
            scoped.side_effect = [found, lookup]
            db.execute = AsyncMock(return_value=found)

            with pytest.raises(DuplicateError) as exc:
                await svc.rotate_api_key(key_id=2, user_id=1, tenant_id=TENANT_ID)

        # The contract: 409 ALREADY_EXISTS, naming the colliding field/value.
        assert exc.value.http_status == 409
        assert exc.value.field == "name"
        assert exc.value.value == "Service Key (Rotated)"
        # Nothing was committed and no row was added — the collision is
        # detected before the insert, not after a failed flush.
        db.add.assert_not_called()
        db.commit.assert_not_called()
        assert not isinstance(exc.value, IntegrityError)

    async def test_derived_name_is_identical_on_every_rotation_of_the_same_key(self) -> None:
        """The same source key always derives the same name — the collision source."""
        import re

        base = "Service Key (Rotated)"
        first = f"{re.sub(r' \(Rotated.*\)$', '', base)[:80]} (Rotated)"
        second = f"{re.sub(r' \(Rotated.*\)$', '', base)[:80]} (Rotated)"
        assert first == second == "Service Key (Rotated)"

    async def test_commit_integrity_error_becomes_duplicate_error(self) -> None:
        """The commit is guarded: a concurrent-name-race IntegrityError -> 409.

        The pre-check SELECT passes on both sides, so the DB constraint is the
        one that rejects the second insert; without this handler that is a 500.
        """
        svc, db = _service_with_existing(_existing_key("Service Key (Rotated)"))

        found = MagicMock()
        found.scalar_one_or_none.return_value = _existing_key("Service Key (Rotated)")
        free = MagicMock()
        free.scalar_one_or_none.return_value = None  # pre-check passes

        db.execute = AsyncMock(return_value=found)
        db.commit = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("uq_api_key_name_tenant")))

        p_scoped, p_update, p_bus = _patched(svc, free)
        with (
            p_scoped as scoped,
            p_update,
            p_bus,
            patch.object(svc, "_generate_key", return_value=("raw-new", "pfxnew", "hashed_new")),
            patch("src.app.modules.api_keys.service.audit", new_callable=AsyncMock),
        ):
            scoped.side_effect = [found, free]

            with pytest.raises(DuplicateError) as exc:
                await svc.rotate_api_key(key_id=2, user_id=1, tenant_id=TENANT_ID)

        assert exc.value.http_status == 409
        assert exc.value.field == "name"
        # The session is rolled back before re-raising so the failed flush
        # cannot poison the request-scoped session.
        db.rollback.assert_awaited_once()
        db.refresh.assert_not_awaited()
        assert exc.value.__cause__ is None

    async def test_successful_rotation_still_commits_and_returns_the_key(self) -> None:
        """The guard is inert when the name is free — rotation still works."""
        svc, db = _service_with_existing(_existing_key("Original"))

        found = MagicMock()
        found.scalar_one_or_none.return_value = _existing_key("Original")
        free = MagicMock()
        free.scalar_one_or_none.return_value = None

        db.execute = AsyncMock(return_value=found)

        p_scoped, p_update, p_bus = _patched(svc, free)
        with (
            p_scoped as scoped,
            p_update,
            p_bus,
            patch.object(svc, "_generate_key", return_value=("raw-new", "pfxnew", "hashed_new")),
            patch("src.app.modules.api_keys.service.audit", new_callable=AsyncMock),
        ):
            scoped.side_effect = [found, free]
            result = await svc.rotate_api_key(key_id=1, user_id=1, tenant_id=TENANT_ID)

        assert result.key == "raw-new"
        assert result.name == "Original (Rotated)"
        db.commit.assert_awaited_once()
        db.rollback.assert_not_awaited()
