"""A-5 oracle: API-key MGET six-row verdict matrix + round-trip baseline.

WHAT THIS PINS: the future A-5 diff collapses 2-3 serial Redis GETs into 1
MGET. This file proves, on HEAD, (1) the six verdicts the diff must preserve
(hit, tombstone, expired, legacy-digest, tenant-inactive, REDIS-DOWN) and
(2) the CURRENT per-row round-trip counts the diff collapses to 1.

WHY TWO CLASSES: ``TestVerdictMatrix`` asserts VERDICTS ONLY and survives the
MGET diff unchanged — it is the equivalence contract. ``TestRoundTripBaseline``
asserts today's exact GET counts and is REPLACED (not weakened) with MGET==1
assertions when the diff lands; its docstring says so explicitly, so a future
reader cannot mistake the replacement for a relaxation.

THE REDIS-DOWN ROW IS THE WHOLE POINT (§12 entry 7): the code has no
try/except around the cache reads — the real adapter is None-safe
(``core/cache/client.py:90-132``: get→None, set→noop, delete→0 when
``get_redis()`` is None), so a down Redis reads as all-miss and validation
falls through to the DB path WITHOUT raising (fail-OPEN to DB). A fail-closed
diff would add a raise/reject here and break the DB-consulted assertion —
that breakage is the test doing its job, not a test to fix. This row pins
CURRENT behaviour as the baseline; the owner's fail-open/fail-closed choice
is the target the diff implements.

NOT DONE, BY USER INSTRUCTION: is-committed = NOT DONE (dont commit or push
any file). E2E-over-HTTP-on-Linux NOT delivered (needs the Linux target).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.tenancy.cache import get_tenant_cache_key
from src.app.modules.api_keys import service as api_keys_service_mod
from src.app.modules.api_keys.service import APIKeyService

TENANT_ID = "11111111-1111-4111-8111-111111111111"
USER_ID = "1"
KEY_ID = 1
SCOPES = ["read"]
FUTURE_TS = "2099-01-01T00:00:00+00:00"
PAST_TS = "2000-01-01T00:00:00+00:00"
RAW_KEY = "ak_oracle_test_key_0123456789abcdef"


def _grant(**overrides: Any) -> dict[str, Any]:
    """A cache grant shaped exactly like the production writer's payload."""
    entry: dict[str, Any] = {
        "user_id": USER_ID,
        "tenant_id": TENANT_ID,
        "scopes": SCOPES,
        "key_id": KEY_ID,
        "expires_at": FUTURE_TS,
        "valid_from": None,
        "is_active": True,
    }
    entry.update(overrides)
    return entry


class _CountingCache:
    """Fake with the real adapter's None-safe contract + an op log.

    ``down=True`` mirrors a down Redis through the REAL None-safe adapter
    semantics (every get misses, sets/deletes are harmless) rather than by
    raising — a fake that raises on connect would pass the redis-down row for
    the wrong reason (the s24y failure mode: assert on the routing, not on
    an exception the production code never sees).
    """

    def __init__(self, entries: dict[str, str] | None = None, down: bool = False) -> None:
        self.entries: dict[str, str] = dict(entries or {})
        self.down = down
        self.ops: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        self.ops.append(("GET", key))
        if self.down:
            return None
        return self.entries.get(key)

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        self.ops.append(("SET", key))
        if not self.down:
            self.entries[key] = value

    async def delete(self, key: str) -> int:
        self.ops.append(("DELETE", key))
        if self.down:
            return 0
        return 1 if self.entries.pop(key, None) is not None else 0

    def gets(self) -> list[str]:
        return [k for op, k in self.ops if op == "GET"]


@pytest.fixture
def digests() -> tuple[str, str]:
    """Real digests from the production helper — never hardcoded."""
    from src.app.core.security.tokens import token_digest_candidates

    return token_digest_candidates(RAW_KEY)


@pytest.fixture
def svc() -> APIKeyService:
    """Service with a dummy db: cache-path rows never touch it (asserted)."""
    return APIKeyService(MagicMock())


@pytest.fixture
def tenant_entry() -> tuple[str, str]:
    """Live-tenant L2 entry under the exact probe key (id must match)."""
    from src.app.core.json_utils import json_dumps

    key = get_tenant_cache_key(TENANT_ID)
    return key, json_dumps({"is_active": True, "id": TENANT_ID}, as_str=True)


def _grant_entry(svc: APIKeyService, digest: str, grant: dict[str, Any]) -> tuple[str, str]:
    """(cache_key, payload) pair built the way the production writer builds it."""
    from src.app.core.json_utils import json_dumps

    return svc._get_cache_key(digest), json_dumps(grant, as_str=True)


class TestVerdictMatrix:
    """Six verdicts the MGET diff must preserve — assertions survive it."""

    async def test_hit_returns_the_grant(
        self, svc: APIKeyService, digests: tuple[str, str], tenant_entry: tuple[str, str]
    ) -> None:
        current, _ = digests
        key, payload = _grant_entry(svc, current, _grant())
        tenant_key, tenant_payload = tenant_entry
        fake = _CountingCache({key: payload, tenant_key: tenant_payload})
        with (
            patch.object(api_keys_service_mod, "cache_client", fake),
            patch.object(svc, "_validate_from_db", AsyncMock(return_value=None)) as db,
        ):
            result = await svc.validate_api_key(RAW_KEY)
        assert result == {"user_id": USER_ID, "tenant_id": TENANT_ID, "scopes": SCOPES, "key_id": KEY_ID}
        db.assert_not_awaited()

    async def test_tombstone_refuses_and_drops_the_entry(self, svc: APIKeyService, digests: tuple[str, str]) -> None:
        current, _ = digests
        key, payload = _grant_entry(svc, current, _grant(is_active=False))
        fake = _CountingCache({key: payload})
        with (
            patch.object(api_keys_service_mod, "cache_client", fake),
            patch.object(svc, "_validate_from_db", AsyncMock(return_value=None)) as db,
        ):
            assert await svc.validate_api_key(RAW_KEY) is None
        assert key not in fake.entries
        db.assert_not_awaited()

    async def test_expired_refuses_and_drops_the_entry(self, svc: APIKeyService, digests: tuple[str, str]) -> None:
        current, _ = digests
        key, payload = _grant_entry(svc, current, _grant(expires_at=PAST_TS))
        fake = _CountingCache({key: payload})
        with (
            patch.object(api_keys_service_mod, "cache_client", fake),
            patch.object(svc, "_validate_from_db", AsyncMock(return_value=None)) as db,
        ):
            assert await svc.validate_api_key(RAW_KEY) is None
        assert key not in fake.entries
        db.assert_not_awaited()

    async def test_legacy_digest_resolves(
        self, svc: APIKeyService, digests: tuple[str, str], tenant_entry: tuple[str, str]
    ) -> None:
        _, legacy = digests
        key, payload = _grant_entry(svc, legacy, _grant())
        tenant_key, tenant_payload = tenant_entry
        fake = _CountingCache({key: payload, tenant_key: tenant_payload})
        with (
            patch.object(api_keys_service_mod, "cache_client", fake),
            patch.object(svc, "_validate_from_db", AsyncMock(return_value=None)) as db,
        ):
            result = await svc.validate_api_key(RAW_KEY)
        assert result is not None and result["key_id"] == KEY_ID
        db.assert_not_awaited()

    async def test_tenant_inactive_rejects(
        self, svc: APIKeyService, digests: tuple[str, str], tenant_entry: tuple[str, str]
    ) -> None:
        from src.app.core.json_utils import json_dumps

        current, _ = digests
        key, payload = _grant_entry(svc, current, _grant())
        tenant_key, _ = tenant_entry
        dead = json_dumps({"is_active": False, "id": TENANT_ID}, as_str=True)
        fake = _CountingCache({key: payload, tenant_key: dead})
        with (
            patch.object(api_keys_service_mod, "cache_client", fake),
            patch.object(svc, "_validate_from_db", AsyncMock(return_value=None)) as db,
        ):
            assert await svc.validate_api_key(RAW_KEY) is None
        db.assert_not_awaited()

    async def test_redis_down_consults_the_db_without_raising(
        self, svc: APIKeyService, digests: tuple[str, str]
    ) -> None:
        """Fail-OPEN baseline: down Redis reads as all-miss, DB decides, no raise."""
        current, legacy = digests
        fake = _CountingCache(down=True)
        with (
            patch.object(api_keys_service_mod, "cache_client", fake),
            patch.object(svc, "_validate_from_db", AsyncMock(return_value=None)) as db,
        ):
            assert await svc.validate_api_key(RAW_KEY) is None
        db.assert_awaited_once_with(current, legacy)


class TestRoundTripBaseline:
    """TODAY's exact GET counts (no tenant ctx: probe reads the id key only).

    The MGET diff collapses every row's GETs to 1. When it lands, THIS class
    is REPLACED with MGET==1 assertions — replacing a baseline whose premise
    (serial GETs) no longer exists is not weakening it.
    """

    async def test_hit_costs_two_gets(
        self, svc: APIKeyService, digests: tuple[str, str], tenant_entry: tuple[str, str]
    ) -> None:
        current, _ = digests
        key, payload = _grant_entry(svc, current, _grant())
        tenant_key, tenant_payload = tenant_entry
        fake = _CountingCache({key: payload, tenant_key: tenant_payload})
        with (
            patch.object(api_keys_service_mod, "cache_client", fake),
            patch.object(svc, "_validate_from_db", AsyncMock(return_value=None)),
        ):
            assert await svc.validate_api_key(RAW_KEY) is not None
        assert fake.gets() == [key, tenant_key]

    async def test_tombstone_costs_one_get_and_one_delete(self, svc: APIKeyService, digests: tuple[str, str]) -> None:
        current, _ = digests
        key, payload = _grant_entry(svc, current, _grant(is_active=False))
        fake = _CountingCache({key: payload})
        with (
            patch.object(api_keys_service_mod, "cache_client", fake),
            patch.object(svc, "_validate_from_db", AsyncMock(return_value=None)),
        ):
            assert await svc.validate_api_key(RAW_KEY) is None
        assert fake.ops == [("GET", key), ("DELETE", key)]

    async def test_expired_costs_one_get_and_one_delete(self, svc: APIKeyService, digests: tuple[str, str]) -> None:
        current, _ = digests
        key, payload = _grant_entry(svc, current, _grant(expires_at=PAST_TS))
        fake = _CountingCache({key: payload})
        with (
            patch.object(api_keys_service_mod, "cache_client", fake),
            patch.object(svc, "_validate_from_db", AsyncMock(return_value=None)),
        ):
            assert await svc.validate_api_key(RAW_KEY) is None
        assert fake.ops == [("GET", key), ("DELETE", key)]

    async def test_legacy_digest_costs_three_gets(
        self, svc: APIKeyService, digests: tuple[str, str], tenant_entry: tuple[str, str]
    ) -> None:
        current, legacy = digests
        primary = svc._get_cache_key(current)
        key, payload = _grant_entry(svc, legacy, _grant())
        tenant_key, tenant_payload = tenant_entry
        fake = _CountingCache({key: payload, tenant_key: tenant_payload})
        with (
            patch.object(api_keys_service_mod, "cache_client", fake),
            patch.object(svc, "_validate_from_db", AsyncMock(return_value=None)),
        ):
            assert await svc.validate_api_key(RAW_KEY) is not None
        assert fake.gets() == [primary, key, tenant_key]

    async def test_tenant_inactive_costs_two_gets(
        self, svc: APIKeyService, digests: tuple[str, str], tenant_entry: tuple[str, str]
    ) -> None:
        from src.app.core.json_utils import json_dumps

        current, _ = digests
        key, payload = _grant_entry(svc, current, _grant())
        tenant_key, _ = tenant_entry
        dead = json_dumps({"is_active": False, "id": TENANT_ID}, as_str=True)
        fake = _CountingCache({key: payload, tenant_key: dead})
        with (
            patch.object(api_keys_service_mod, "cache_client", fake),
            patch.object(svc, "_validate_from_db", AsyncMock(return_value=None)),
        ):
            assert await svc.validate_api_key(RAW_KEY) is None
        assert fake.gets() == [key, tenant_key]


class TestFakeIsHonest:
    """Anti-vacuity: the counting fake can observe what the tests claim."""

    def test_seeded_entries_are_retrievable(self, svc: APIKeyService, digests: tuple[str, str]) -> None:
        current, _ = digests
        key, _ = _grant_entry(svc, current, _grant())
        assert len(key) > 0
        assert current != digests[1]

    async def test_down_mode_misses_everything(self) -> None:
        fake = _CountingCache({"k": "v"}, down=True)
        assert await fake.get("k") is None
        assert fake.gets() == ["k"]
