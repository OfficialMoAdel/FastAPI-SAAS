"""Coverage tests for APIKeyService using mocked DB and Redis."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.api_keys.schemas import APIKeyCreate, APIKeyResponse
from src.app.modules.api_keys.service import APIKeyService
from src.app.shared.exceptions import ModelNotFoundError


def _make_service() -> tuple[APIKeyService, AsyncMock]:
    db = AsyncMock()
    service = APIKeyService(db)
    return service, db


def _make_key_record(name: str = "prod", key_id: int = 1) -> MagicMock:
    key = MagicMock()
    key.id = key_id
    key.name = name
    key.key_prefix = "abcdefgh"
    key.hashed_key = "x" * 64
    key.user_id = 1
    key.tenant_id = "11111111-1111-1111-1111-111111111111"
    key.is_active = True
    key.expires_at = None
    key.valid_from = None
    return key


async def test_generate_key_returns_triple() -> None:
    service, _ = _make_service()
    full, prefix, hashed = service._generate_key()
    assert len(full) > 0
    assert prefix == full[: service.KEY_PREFIX_LENGTH]
    assert hashed == service._hash_key(full)


async def test_create_api_key_duplicate_name() -> None:
    service, db = _make_service()
    existing = _make_key_record()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    db.execute.return_value = result
    data = APIKeyCreate(name="prod")
    tenant = "11111111-1111-1111-1111-111111111111"
    with pytest.raises(Exception):
        await service.create_api_key(api_key_data=data, user_id=1, tenant_id=tenant)


async def test_create_api_key_success() -> None:
    service, db = _make_service()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result
    db.refresh.side_effect = lambda obj: setattr(obj, "id", 1) or None
    data = APIKeyCreate(name="prod", scopes=["read:posts"])
    tenant = "11111111-1111-1111-1111-111111111111"
    resp = await service.create_api_key(api_key_data=data, user_id=1, tenant_id=tenant)
    assert isinstance(resp, APIKeyResponse)
    assert resp.key.startswith("abcdefgh") is False or len(resp.key) > 8
    db.add.assert_called_once()
    added_obj = db.add.call_args.args[0]
    assert added_obj.scopes is not None
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once()


async def test_revoke_api_key_not_found() -> None:
    service, db = _make_service()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result
    tenant = "11111111-1111-1111-1111-111111111111"
    with pytest.raises(ModelNotFoundError):
        await service.revoke_api_key(key_id=99, user_id=1, tenant_id=tenant)


async def test_revoke_api_key_forbidden() -> None:
    """T7: a key owned by another tenant is indistinguishable from missing.

    The scoped lookup returns no row, so revocation raises ModelNotFound
    (404) instead of the old ForbiddenException (403) — removing the
    existence oracle over other tenants' key ids.
    """
    service, db = _make_service()
    # Scoped query filters by tenant/user: mock yields NO row.
    empty = MagicMock()
    empty.scalar_one_or_none.return_value = None
    db.execute.return_value = empty
    tenant = "11111111-1111-1111-1111-111111111111"
    with pytest.raises(ModelNotFoundError):
        await service.revoke_api_key(key_id=1, user_id=1, tenant_id=tenant)


async def test_revoke_api_key_success() -> None:
    service, db = _make_service()
    key = _make_key_record()
    result = MagicMock()
    result.scalar_one_or_none.return_value = key
    db.execute.return_value = result
    tenant = "11111111-1111-1111-1111-111111111111"
    with patch.object(service.crud, "update") as mock_update:
        mock_update.return_value = None
        out = await service.revoke_api_key(key_id=1, user_id=1, tenant_id=tenant)
    assert out["message"] == "API key revoked successfully"
    mock_update.assert_awaited_once()


async def test_validate_api_key_cache_hit() -> None:
    service, db = _make_service()
    fake_redis = AsyncMock()

    async def _redis_get(key):
        # T8: second get probes tenant liveness — report active.
        if "tenant" in str(key):
            return '{"is_active": true}'
        return '{"user_id": 1, "tenant_id": "t", "scopes": ["read"], "key_id": 1}'

    fake_redis.get.side_effect = _redis_get
    with patch("src.app.core.utils.cache.client", fake_redis):
        result = await service.validate_api_key("rawkey")
    assert result is not None
    assert result["user_id"] == 1


async def test_validate_api_key_cache_hit_expired() -> None:
    service, db = _make_service()
    fake_redis = AsyncMock()
    from datetime import UTC, datetime, timedelta

    expired = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    fake_redis.get.return_value = (
        f'{{"user_id": 1, "tenant_id": "t", "scopes": [], "key_id": 1, "expires_at": "{expired}"}}'
    )
    with patch("src.app.core.utils.cache.client", fake_redis):
        result = await service.validate_api_key("rawkey")
    assert result is None
    fake_redis.delete.assert_awaited_once()


async def test_validate_api_key_cache_hit_valid_from_future() -> None:
    service, db = _make_service()
    fake_redis = AsyncMock()
    from datetime import UTC, datetime, timedelta

    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    fake_redis.get.return_value = (
        f'{{"user_id": 1, "tenant_id": "t", "scopes": [], "key_id": 1, "valid_from": "{future}"}}'
    )
    with patch("src.app.core.utils.cache.client", fake_redis):
        result = await service.validate_api_key("rawkey")
    assert result is None


async def test_validate_api_key_cache_miss() -> None:
    service, db = _make_service()
    fake_redis = AsyncMock()
    fake_redis.get.return_value = None
    # _validate_from_db returns None (key not in db)
    with (
        patch("src.app.core.utils.cache.client", fake_redis),
        patch.object(service, "_validate_from_db", AsyncMock(return_value=None)),
    ):
        result = await service.validate_api_key("rawkey")
    assert result is None


async def test_validate_api_key_cache_miss_then_cache() -> None:
    service, db = _make_service()
    fake_redis = AsyncMock()
    fake_redis.get.return_value = None
    db_result = {
        "user_id": 1,
        "tenant_id": "t",
        "scopes": ["read"],
        "key_id": 1,
        "expires_at": None,
        "valid_from": None,
    }
    with (
        patch("src.app.core.utils.cache.client", fake_redis),
        patch.object(service, "_validate_from_db", AsyncMock(return_value=db_result)),
    ):
        result = await service.validate_api_key("rawkey")
    assert result is not None
    # S-01: cached under the PRIMARY (current HMAC) digest only. The legacy
    # second write was an orphan (the read loop checks the primary first, both
    # payloads identical) and unreclaimable (the row migrates to the HMAC on
    # first use, so a revoke could never delete the SHA-256 entry) — leaving a
    # revoked key authenticable for the full 300s TTL. The legacy DB lookup is
    # untouched; `_validate_from_db` still tries both digests.
    assert fake_redis.set.await_count == 1
    for call in fake_redis.set.await_args_list:
        assert call.kwargs.get("ex") == 300
