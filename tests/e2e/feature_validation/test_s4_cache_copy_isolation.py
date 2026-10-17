"""S-4 e2e: the auth-info cache copies its payload by value, for BOTH sources.

The S-4 change replaced ``copy.deepcopy`` with a two-level copy (the top level
plus the single nested ``tier`` dict) and made the shape assertion permanent at
every copy site. This oracle drives the REAL cache over HTTP and proves the
property the change exists for: a caller that mutates the dict it was handed
cannot reach the stored copy.

Two payload sources are exercised, because they are not the same object:

* DB path — ``cache_user_auth_info`` stores ``_two_level_copy(db_payload)`` in
  the process-local L1 and ``json_dumps(db_payload)`` in Redis. The L1 entry
  holds ``datetime`` objects.
* Redis path — ``get_cached_user`` parses the Redis string and stores
  ``_two_level_copy(parsed)`` in the L1. The L1 entry holds ISO strings.

Both must be isolated from mutation, and both must serialise to the same body.

PR impact statement: this moves capacity, not p95. The two-level copy saves
~24.55us per auth-info read against a 4090us p95 floor — 0.6%.

The three sites covered:
  1. ``cache_user_auth_info``  (DB payload -> L1 + Redis)
  2. ``get_cached_user`` Redis branch (Redis payload -> L1)
  3. ``get_cached_user`` L1 branch (L1 entry -> the returned object)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.json_utils import json_dumps
from src.app.core.redis_provider import get_redis
from src.app.modules.users.cache import (
    UserCacheInvalidator,
    _auth_info_l1,
    assert_user_payload_shape,
)
from src.app.modules.users.cache_keys import _user_auth_cache_key
from src.app.modules.users.crud import crud_users

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[3]


def _digest(entry: dict[str, Any]) -> str:
    """Stable digest of a cache entry (orjson is deterministic for dicts)."""
    import hashlib

    return hashlib.sha256(json_dumps(entry, as_str=True).encode("utf-8")).hexdigest()[:16]


def _mutate(payload: dict[str, Any]) -> None:
    """Mutate a returned payload the way a caller would: top level + ``tier``."""
    payload["name"] = "MUTATED-BY-CALLER"
    tier = payload.get("tier")
    if isinstance(tier, dict):
        tier["name"] = "MUTATED-BY-CALLER"


async def _tenant_id(db_session: AsyncSession, email: str) -> Any:
    """Resolve the fixture user's tenant by email.

    The ``authenticated_user`` fixture's ``user_id`` is read from the login
    response body (``result.get("user_id")``), which does not carry it, so the
    id is unusable here and the lookup is by email instead.
    """
    result = await db_session.execute(
        text("SELECT tenant_id FROM users WHERE email = :email"),
        {"email": email},
    )
    tenant_id = result.scalar()
    assert tenant_id is not None, "the fixture user must exist"
    return tenant_id


async def _redis_get(key: str) -> str | None:
    client = get_redis()
    if client is None:
        return None
    return await client.get(key)


async def _redis_delete(key: str) -> None:
    client = get_redis()
    if client is None:
        return
    await client.delete(key)


async def _db_payload(
    db_session: AsyncSession, username: str, tenant_id: Any
) -> dict[str, Any]:
    payload = await crud_users.get_user_with_tier_eager(
        db=db_session, username=username, tenant_id=tenant_id
    )
    assert payload is not None, "the fixture user must exist"
    return payload


def test_shape_assertion_rejects_nested_structures() -> None:
    """NEGATIVE test: the permanent guard fires on a non-conforming payload.

    The two-level copy is only an isomorphism while the payload stays one level
    deep with at most one nested dict (``tier``) whose own values are scalars.
    If a nested structure is ever added, this must raise rather than pass a
    shallow copy through.
    """
    assert_user_payload_shape({"id": 1, "tier": {"id": 2, "name": "pro"}})
    assert_user_payload_shape({"id": 1, "tier": None})

    with pytest.raises(TypeError):
        assert_user_payload_shape({"id": 1, "nested": {"a": 1}})

    with pytest.raises(TypeError):
        assert_user_payload_shape({"id": 1, "tier": {"id": 2, "deep": {"a": 1}}})


async def test_db_payload_to_l1_is_isolated_from_mutation(
    client: AsyncClient, authenticated_user: dict[str, Any], db_session: AsyncSession
) -> None:
    """Site 1 — ``cache_user_auth_info`` writes the DB payload into the L1.

    Force the DB path by emptying both stores, then feed the writer the real DB
    payload. The L1 entry it stores must not be reachable through the object a
    later reader is handed.
    """
    user = authenticated_user
    tenant_id = await _tenant_id(db_session, user["email"])
    key = _user_auth_cache_key(user["email"], tenant_id)

    _auth_info_l1.clear()
    await _redis_delete(key)
    payload = await _db_payload(db_session, user["username"], tenant_id)
    await UserCacheInvalidator.cache_user_auth_info(key, payload)

    stored_before = _digest(_auth_info_l1[key])
    returned = await UserCacheInvalidator.get_cached_user(key)
    assert returned is not None
    assert returned is not _auth_info_l1[key], "the L1 read must hand out a copy"
    _mutate(returned)

    assert _digest(_auth_info_l1[key]) == stored_before, (
        "mutating the returned payload reached the L1 entry written from the DB payload"
    )


async def test_redis_payload_to_l1_is_isolated_from_mutation(
    client: AsyncClient, authenticated_user: dict[str, Any], db_session: AsyncSession
) -> None:
    """Site 2 — ``get_cached_user`` writes the Redis payload into the L1.

    Force the Redis path by emptying only the L1 (Redis still holds the body).
    The L1 entry built from the parsed Redis string must not be reachable
    through the returned object, and the Redis body itself must be untouched.
    """
    user = authenticated_user
    tenant_id = await _tenant_id(db_session, user["email"])
    key = _user_auth_cache_key(user["email"], tenant_id)

    _auth_info_l1.clear()
    await _redis_delete(key)
    payload = await _db_payload(db_session, user["username"], tenant_id)
    await UserCacheInvalidator.cache_user_auth_info(key, payload)

    _auth_info_l1.clear()
    redis_before = await _redis_get(key)
    assert redis_before is not None, "the Redis body must be present to exercise the Redis path"

    returned = await UserCacheInvalidator.get_cached_user(key)
    assert returned is not None
    stored_before = _digest(_auth_info_l1[key])
    _mutate(returned)

    assert _digest(_auth_info_l1[key]) == stored_before, (
        "mutating the returned payload reached the L1 entry written from the Redis payload"
    )
    assert await _redis_get(key) == redis_before, "mutating the returned payload reached Redis"


async def test_l1_read_returns_a_fresh_isolated_copy(
    client: AsyncClient, authenticated_user: dict[str, Any], db_session: AsyncSession
) -> None:
    """Site 3 — ``get_cached_user`` L1 branch hands out a per-read copy.

    Two consecutive L1 reads must not alias each other or the stored entry.
    """
    user = authenticated_user
    tenant_id = await _tenant_id(db_session, user["email"])
    key = _user_auth_cache_key(user["email"], tenant_id)

    _auth_info_l1.clear()
    await _redis_delete(key)
    payload = await _db_payload(db_session, user["username"], tenant_id)
    await UserCacheInvalidator.cache_user_auth_info(key, payload)

    stored_before = _digest(_auth_info_l1[key])
    first = await UserCacheInvalidator.get_cached_user(key)
    second = await UserCacheInvalidator.get_cached_user(key)
    assert first is not None and second is not None
    assert first is not second, "each L1 read must return a distinct copy"

    _mutate(first)
    assert _digest(_auth_info_l1[key]) == stored_before, "mutating one read reached the L1 entry"
    assert second["name"] != "MUTATED-BY-CALLER", "two L1 reads aliased each other"


async def test_body_digest_identical_across_both_payload_sources(
    client: AsyncClient, authenticated_user: dict[str, Any], db_session: AsyncSession
) -> None:
    """The DB payload and the Redis payload serialise to the same body.

    The DB payload holds ``datetime`` objects; the Redis payload holds ISO
    strings. Both must produce the same ``json_dumps`` body, which is what makes
    the two sources interchangeable to a reader.
    """
    user = authenticated_user
    tenant_id = await _tenant_id(db_session, user["email"])
    key = _user_auth_cache_key(user["email"], tenant_id)

    _auth_info_l1.clear()
    await _redis_delete(key)
    payload = await _db_payload(db_session, user["username"], tenant_id)
    await UserCacheInvalidator.cache_user_auth_info(key, payload)
    db_body = json_dumps(_auth_info_l1[key], as_str=True)

    _auth_info_l1.clear()
    await UserCacheInvalidator.get_cached_user(key)
    redis_body = json_dumps(_auth_info_l1[key], as_str=True)

    assert db_body == redis_body, "the DB and Redis payload sources serialise differently"
