"""The tenant email-template cache key family.

Card 11: the four raw ``build_tenant_key(f"email_template:{name}", tenant_id)``
call sites (read + write in ``get_template_content``, and the override
create/update/delete invalidations) now go through
``src.app.modules.notifications.template_cache``. These tests pin the key
grammar, the fail-soft policy, and that the service actually uses the module
rather than rebuilding the key by hand.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.notifications.service import NotificationService
from src.app.modules.notifications.template_cache import (
    TEMPLATE_CACHE_TTL_SECONDS,
    get_cached_template,
    invalidate_template_cache,
    store_cached_template,
    template_cache_key,
)


class TestKeyGrammar:
    def test_tenant_scoped_key(self) -> None:
        tenant_id = uuid4()
        assert template_cache_key(tenant_id, "welcome") == f"tenant:{tenant_id}:email_template:welcome"

    def test_global_key_when_no_tenant(self) -> None:
        assert template_cache_key(None, "welcome") == "email_template:welcome"


class TestReadWriteInvalidate:
    @pytest.mark.asyncio
    @patch("src.app.core.utils.cache.client")
    async def test_hit_returns_subject_and_content(self, mock_redis) -> None:
        mock_redis.get = AsyncMock(return_value=b'["Subj", "<p>hi</p>"]')
        tenant_id = uuid4()

        assert await get_cached_template(tenant_id, "welcome") == ("Subj", "<p>hi</p>")
        mock_redis.get.assert_awaited_once_with(f"tenant:{tenant_id}:email_template:welcome")

    @pytest.mark.asyncio
    @patch("src.app.core.utils.cache.client")
    async def test_miss_returns_none(self, mock_redis) -> None:
        mock_redis.get = AsyncMock(return_value=None)
        assert await get_cached_template(uuid4(), "welcome") is None

    @pytest.mark.asyncio
    @patch("src.app.core.utils.cache.client", None)
    async def test_no_redis_client_is_a_miss(self) -> None:
        assert await get_cached_template(uuid4(), "welcome") is None

    @pytest.mark.asyncio
    @patch("src.app.core.utils.cache.client")
    async def test_read_error_is_swallowed(self, mock_redis) -> None:
        """A broken cache must not break template resolution."""
        mock_redis.get = AsyncMock(side_effect=RuntimeError("redis down"))
        assert await get_cached_template(uuid4(), "welcome") is None

    @pytest.mark.asyncio
    @patch("src.app.core.utils.cache.client")
    async def test_store_writes_json_with_ttl(self, mock_redis) -> None:
        mock_redis.set = AsyncMock()
        tenant_id = uuid4()

        await store_cached_template(tenant_id, "welcome", ("Subj", "<p>hi</p>"))

        mock_redis.set.assert_awaited_once_with(
            f"tenant:{tenant_id}:email_template:welcome",
            '["Subj","<p>hi</p>"]',
            ex=TEMPLATE_CACHE_TTL_SECONDS,
        )

    @pytest.mark.asyncio
    @patch("src.app.core.utils.cache.client")
    async def test_store_error_is_swallowed(self, mock_redis) -> None:
        mock_redis.set = AsyncMock(side_effect=RuntimeError("redis down"))
        await store_cached_template(uuid4(), "welcome", ("Subj", "<p>hi</p>"))

    @pytest.mark.asyncio
    @patch("src.app.core.utils.cache.client")
    async def test_invalidate_deletes_the_key(self, mock_redis) -> None:
        mock_redis.delete = AsyncMock()
        tenant_id = uuid4()

        await invalidate_template_cache(tenant_id, "welcome")

        mock_redis.delete.assert_awaited_once_with(f"tenant:{tenant_id}:email_template:welcome")

    @pytest.mark.asyncio
    @patch("src.app.core.utils.cache.client", None)
    async def test_invalidate_without_redis_is_a_noop(self) -> None:
        await invalidate_template_cache(uuid4(), "welcome")


class TestServiceUsesTheModule:
    @pytest.mark.asyncio
    @patch("src.app.modules.notifications.service.invalidate_template_cache", new_callable=AsyncMock)
    async def test_create_override_invalidates(self, mock_invalidate) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        service = NotificationService(db=db)
        tenant_id = uuid4()

        await service.create_template_override(tenant_id, "welcome", "Subj", "<p>body</p>")

        mock_invalidate.assert_awaited_once_with(tenant_id, "welcome")

    @pytest.mark.asyncio
    @patch("src.app.modules.notifications.service.invalidate_template_cache", new_callable=AsyncMock)
    async def test_update_override_invalidates(self, mock_invalidate) -> None:
        db = AsyncMock()
        existing = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing)))
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        service = NotificationService(db=db)
        tenant_id = uuid4()

        await service.create_template_override(tenant_id, "welcome", "Subj", "<p>body</p>")

        mock_invalidate.assert_awaited_once_with(tenant_id, "welcome")

    @pytest.mark.asyncio
    @patch("src.app.modules.notifications.service.invalidate_template_cache", new_callable=AsyncMock)
    async def test_delete_override_invalidates(self, mock_invalidate) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=MagicMock())))
        db.delete = AsyncMock()
        db.commit = AsyncMock()
        service = NotificationService(db=db)
        tenant_id = uuid4()

        assert await service.delete_template_override(tenant_id, "welcome") is True
        mock_invalidate.assert_awaited_once_with(tenant_id, "welcome")

    @pytest.mark.asyncio
    @patch("src.app.core.utils.cache.client")
    async def test_get_template_content_reads_and_writes_through_the_module(self, mock_redis) -> None:
        """A cache hit short-circuits the DB/file lookup (no query, no rewrite)."""
        mock_redis.get = AsyncMock(return_value=b'["Subj", "<p>hi</p>"]')
        db = AsyncMock()
        service = NotificationService(db=db)

        assert await service.get_template_content(uuid4(), "welcome") == ("Subj", "<p>hi</p>")
        db.execute.assert_not_called()
        mock_redis.set.assert_not_called()
