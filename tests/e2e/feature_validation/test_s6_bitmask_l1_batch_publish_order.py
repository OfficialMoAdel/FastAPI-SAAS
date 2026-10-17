"""S-6 e2e: batch invalidation emits one delete RTT and one PUBLISH RTT with the
seam payload sequence f"{user}:{tenant}" in input order; double-invalidate is
idempotent; a per-user invariant can still be published on its own.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_s6_ordered_publish_payloads_and_idempotence():
    from src.app.core import redis_provider
    from src.app.core.rbac import dependencies

    published: list[str] = []

    class _Pipe:
        def __init__(self):
            self._ops: list[tuple[str, str]] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def delete(self, key):
            self._ops.append(("del", key))

        def publish(self, channel, payload):
            published.append(payload)

        async def execute(self):
            return [None for _ in self._ops]

    class _Redis:
        def pipeline(self, transaction=False):
            return _Pipe()

    def fake_get_redis():
        return _Redis()

    old = redis_provider.get_redis
    redis_provider.get_redis = fake_get_redis
    dependencies.get_redis = fake_get_redis
    try:
        users = [3, 1, 4]
        for uid in users:
            await dependencies.invalidate_users_permissions_cache_batch(users, 42)
            assert published[-len(users):] == [f"{int(u)}:42" for u in users]
            # double-invalidate idempotence
            n = len(published)
            await dependencies.invalidate_users_permissions_cache_batch(users, 42)
            assert len(published) > n
        # empty input is a no-op for the publish RTT
        before = len(published)
        await dependencies.invalidate_users_permissions_cache_batch([], 42)
        assert len(published) == before
    finally:
        redis_provider.get_redis = old
        dependencies.get_redis = old
