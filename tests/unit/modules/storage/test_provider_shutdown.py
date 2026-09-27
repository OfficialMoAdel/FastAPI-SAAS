"""Cached storage providers must be closed on shutdown.

``_PROVIDER_CACHE`` holds up to 512 providers for 600s, keyed by
``(tenant_id, updated_at)``. S3 and Azure providers own an HTTP client;
without a drain the process exits with those still open, and a redeploy
inside the TTL strands their sockets.

``LocalStorage`` had no ``close()`` at all, so the existing
``if hasattr(storage, "close")`` guard in ``_validate_credentials`` was
load-bearing for a capability the ``StorageProvider`` interface never
declared — a reader could not tell whether closing was part of the
contract. The interface now declares a default no-op ``close()``,
``LocalStorage`` implements it explicitly, and teardown can call it
unconditionally.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.app.clients.storage.interfaces import StorageProvider
from src.app.clients.storage.local_storage import LocalStorage
from src.app.modules.storage import admin as storage_admin
from src.app.modules.storage.admin import close_cached_providers


@pytest.fixture(autouse=True)
def _clear_cache():
    storage_admin._PROVIDER_CACHE.clear()
    yield
    storage_admin._PROVIDER_CACHE.clear()


class TestInterfaceDeclaresClose:
    def test_every_provider_answers_close(self) -> None:
        """No hasattr probe needed: the capability is on the interface."""
        assert hasattr(StorageProvider, "close")
        assert not getattr(StorageProvider.close, "__isabstractmethod__", False), (
            "close() must have a default implementation so providers that "
            "hold nothing are not forced to declare it"
        )

    def test_local_storage_close_is_a_noop(self) -> None:
        provider = LocalStorage(base_path="/tmp")
        assert isinstance(provider, StorageProvider)

    @pytest.mark.asyncio
    async def test_local_storage_close_is_callable_and_idempotent(self) -> None:
        provider = LocalStorage(base_path="/tmp")
        await provider.close()
        await provider.close()  # must not raise

    @pytest.mark.asyncio
    async def test_a_provider_holding_a_client_closes_it(self) -> None:
        closed: list[str] = []

        class FakeProvider(StorageProvider):
            async def upload(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
                raise NotImplementedError

            async def download(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
                raise NotImplementedError

            async def delete(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
                raise NotImplementedError

            async def exists(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
                raise NotImplementedError

            async def get_url(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
                raise NotImplementedError

            async def close(self) -> None:
                closed.append("closed")

        await FakeProvider().close()
        assert closed == ["closed"], "an override must actually run"


class TestDrainClosesEverything:
    @pytest.mark.asyncio
    async def test_every_cached_provider_is_closed(self) -> None:
        providers = [AsyncMock(), AsyncMock(), AsyncMock()]
        for i, provider in enumerate(providers):
            storage_admin._PROVIDER_CACHE[(uuid4(), datetime(2025, 1, 1, tzinfo=UTC) + _s(i))] = provider

        closed = await close_cached_providers()

        assert closed == 3
        for provider in providers:
            provider.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_cache_is_emptied(self) -> None:
        storage_admin._PROVIDER_CACHE[(uuid4(), datetime(2025, 1, 1, tzinfo=UTC))] = AsyncMock()

        await close_cached_providers()

        assert len(storage_admin._PROVIDER_CACHE) == 0, "drained providers must not linger in the cache"

    @pytest.mark.asyncio
    async def test_one_failing_provider_does_not_strand_the_rest(self) -> None:
        bad, good = AsyncMock(), AsyncMock()
        bad.close = AsyncMock(side_effect=RuntimeError("already closed"))
        storage_admin._PROVIDER_CACHE[(uuid4(), datetime(2025, 1, 1, tzinfo=UTC))] = bad
        storage_admin._PROVIDER_CACHE[(uuid4(), datetime(2025, 1, 2, tzinfo=UTC))] = good

        closed = await close_cached_providers()

        good.close.assert_awaited_once()
        assert closed == 1, "the healthy provider must still be closed"
        assert len(storage_admin._PROVIDER_CACHE) == 0

    @pytest.mark.asyncio
    async def test_draining_an_empty_cache_is_a_no_op(self) -> None:
        assert await close_cached_providers() == 0


class TestTeardownIsWired:
    def test_lifespan_tail_drains_the_provider_cache(self) -> None:
        """The drain must be in the actual teardown, not just defined."""
        import ast
        from pathlib import Path

        import src.app.composition as composition

        tree = ast.parse(Path(composition.__file__).read_text(encoding="utf-8"))
        factory = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "lifespan_factory"
        )
        names = {
            n.id
            for n in ast.walk(factory)
            if isinstance(n, ast.Name)
        }
        assert "_close_storage_providers" in names, (
            "lifespan_factory does not reference the provider drain; cached "
            "S3/Azure clients would never be closed"
        )

    def test_the_teardown_helper_exists_and_is_awaitable(self) -> None:
        import inspect

        from src.app import composition

        assert hasattr(composition, "_close_storage_providers")
        assert inspect.iscoroutinefunction(composition._close_storage_providers)

    @pytest.mark.asyncio
    async def test_teardown_swallows_provider_failures(self) -> None:
        """Teardown must not mask shutdown."""
        from src.app import composition

        with patch_close_cached_providers(side_effect=RuntimeError("boom")):
            await composition._close_storage_providers()  # must not raise


def patch_close_cached_providers(**kwargs):
    from unittest.mock import patch

    return patch(
        "src.app.modules.storage.admin.close_cached_providers",
        AsyncMock(**kwargs),
    )


def _s(i: int):
    from datetime import timedelta

    return timedelta(seconds=i)
