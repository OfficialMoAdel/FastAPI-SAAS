"""S-01 — a revoked API key must stop authenticating immediately.

Before the fix the cache entry was a *complete authentication grant*: it
carried user, tenant, scopes and key id, and `validate_api_key`'s hit path
returned it verbatim with no revocation check. Worse, it was written under TWO
Redis keys — the primary peppered-HMAC digest and the pre-I2 bare SHA-256 —
with an always-true guard, because `token_digest_candidates` returns two
different digest functions.

Revocation could only delete the key derived from the stored HMAC. The row is
migrated to the HMAC on first use, so the SHA-256 entry was *unreclaimable*
and kept authenticating for the full `API_KEY_CACHE_TTL` (300s) after revoke.

Three properties are pinned here:
  1. the cache-hit path refuses an entry that says the key is not active;
  2. only the primary key is written, so no unreclaimable orphan exists;
  3. revoke evicts AFTER the commit, so a concurrent validate cannot re-arm
     the entry from the still-active row.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

FUTURE = (datetime.now(UTC) + timedelta(days=365)).isoformat()


def _payload(*, is_active: bool | None = True, **over: Any) -> str:
    data: dict[str, Any] = {
        "user_id": 1,
        "tenant_id": 1,
        "scopes": [],
        "key_id": 42,
        "expires_at": FUTURE,
        "valid_from": None,
    }
    if is_active is not None:
        data["is_active"] = is_active
    data.update(over)
    return json.dumps(data)


@pytest.fixture
def service():
    from src.app.modules.api_keys.service import APIKeyService

    svc = APIKeyService(MagicMock())
    svc.db = MagicMock()
    svc.db.execute = AsyncMock()
    return svc


class TestCacheHitRefusesRevokedEntry:
    @pytest.mark.asyncio
    async def test_inactive_entry_is_rejected_and_evicted(self, service) -> None:
        """A cached grant marked inactive must not authenticate."""
        with patch("src.app.core.utils.cache.client") as cache:
            cache.get = AsyncMock(return_value=_payload(is_active=False))
            cache.delete = AsyncMock()

            assert await service.validate_api_key("k" * 32) is None
            cache.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_active_entry_still_authenticates(self, service) -> None:
        """The happy path is unchanged — an active grant still works."""
        with patch("src.app.core.utils.cache.client") as cache:
            cache.get = AsyncMock(return_value=_payload(is_active=True))
            cache.delete = AsyncMock()
            service._get_cached_tenant_active = AsyncMock(return_value=True)

            result = await service.validate_api_key("k" * 32)
            assert result is not None
            assert result["user_id"] == 1

    @pytest.mark.asyncio
    async def test_legacy_entry_without_field_still_works(self, service) -> None:
        """Entries written before this change carry no field; they must still work.

        They are bounded by the 300s TTL, so treating a missing field as active
        has a one-TTL exposure window from deploy and zero afterwards.
        """
        with patch("src.app.core.utils.cache.client") as cache:
            cache.get = AsyncMock(return_value=_payload(is_active=None))
            cache.delete = AsyncMock()
            service._get_cached_tenant_active = AsyncMock(return_value=True)

            assert await service.validate_api_key("k" * 32) is not None


class TestNoUnreclaimableOrphanEntry:
    @pytest.mark.asyncio
    async def test_only_primary_key_is_written(self, service) -> None:
        """One write, not two — the legacy cache entry must not be created."""
        mock_key = MagicMock()
        mock_key.id = 42
        mock_key.user_id = 1
        mock_key.tenant_id = 1
        mock_key.scopes = "[]"
        mock_key.expires_at = None
        mock_key.valid_from = None
        mock_key.is_active = True
        mock_key.hashed_key = "h"
        # ADR-0023: the DB path now LEFT-JOINs user -> tier, so the row a real
        # session returns carries the owner. Mock it as the tierless owner this
        # row represents: `tier_id = NULL` is a real, valid state and it is the
        # one that must survive JSON encoding (a bare MagicMock here would be
        # injected into the cached payload and json_dumps would refuse it).
        mock_key.user = SimpleNamespace(tier_id=None, tier=None)

        result = MagicMock()
        result.scalar_one_or_none.return_value = mock_key
        service.db.execute = AsyncMock(return_value=result)

        with (
            patch("src.app.core.utils.cache.client") as cache,
            patch.object(service, "crud") as crud,
        ):
            cache.get = AsyncMock(return_value=None)
            cache.set = AsyncMock()
            crud.update = AsyncMock()

            assert await service.validate_api_key("k" * 32) is not None
            assert cache.set.await_count == 1, "the unreclaimable legacy entry must not be written"

            payload = json.loads(cache.set.await_args_list[0][0][1])
            assert payload["is_active"] is True


class TestRevokeEvictsAfterCommit:
    @pytest.mark.asyncio
    async def test_cache_deleted_only_after_update_commits(self, service) -> None:
        """The tombstone write must not precede the commit.

        Old order cleared the cache first, so (a) a failed update left the DB
        saying active with no cache entry, and (b) a concurrent validate in the
        write->commit window re-populated the entry from the still-active row.

        The write is a TOMBSTONE rather than a delete: the cached payload is
        the authentication grant, so removing it is indistinguishable from
        "never cached" and the read-side revocation guard never fires.
        """
        from src.app.modules.api_keys.models import APIKey

        key = MagicMock(spec=APIKey)
        key.hashed_key = "hashed-value"
        key.name = "n"
        key.key_prefix = "kp"
        key.is_active = True

        stmt_result = MagicMock()
        stmt_result.scalar_one_or_none.return_value = key
        service.db.execute = AsyncMock(return_value=stmt_result)

        order: list[str] = []

        with (
            patch("src.app.core.utils.cache.client") as cache,
            patch.object(service, "crud") as crud,
            patch("src.app.modules.api_keys.service.execute_scoped", AsyncMock(return_value=stmt_result)),
            patch("src.app.modules.api_keys.service.audit", AsyncMock()),
        ):
            cache.set = AsyncMock(side_effect=lambda *_a, **_k: order.append("cache_tombstone"))

            async def _update(*_a: Any, **kw: Any) -> None:
                order.append("db_update_commit")

            crud.update = AsyncMock(side_effect=_update)

            from uuid import uuid4

            await service.revoke_api_key(key_id=42, user_id=1, tenant_id=uuid4())

        assert order == ["db_update_commit", "cache_tombstone"], f"wrong eviction order: {order}"
