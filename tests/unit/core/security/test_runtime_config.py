"""The runtime security-configuration seam: one key grammar, one writer, one reader.

These pin the three things the seam owns and the two it decided:
* the Redis key + TTL are preserved, so an override an operator already
  stored keeps applying after the refactor;
* a write naming a key outside the grammar fails loud instead of being
  stored as a key nothing will ever read;
* boot distinguishes "no override stored" from "Redis could not answer", and
  fails OPEN on the latter (loudly — see the module docstring for the why).
"""

from unittest.mock import MagicMock, patch

import pytest

from src.app.core.config import settings
from src.app.core.security.runtime_config import (
    RUNTIME_CONFIG_KEY,
    RUNTIME_CONFIG_TTL,
    SECURITY_RUNTIME_KEYS,
    RuntimeConfigUnavailableError,
    UnknownRuntimeConfigKeyError,
    apply_and_store_runtime_config,
    load_runtime_config,
    read_stored_runtime_config,
)


class _StubLock:
    def __init__(self, _store: object) -> None:
        self._store = _store

    async def __aenter__(self) -> "_StubLock":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _StubCache:
    """Minimal stand-in for the RedisCacheAdapter surface the seam uses."""

    def __init__(self, stored: str | None = None, *, get_raises: Exception | None = None) -> None:
        self.payload = stored
        self.get_raises = get_raises
        self.set_calls: list[tuple[str, str, int | None]] = []

    @property
    def client(self) -> "_StubCache":
        return self

    def lock(self, _name: str, timeout: int | None = None) -> _StubLock:
        return _StubLock(self)

    async def get(self, key: str) -> str | None:
        if self.get_raises is not None:
            raise self.get_raises
        return self.payload if key == RUNTIME_CONFIG_KEY else None

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        self.set_calls.append((key, value, ttl))
        self.payload = value
        return True


def _patch_cache(stub: _StubCache):
    return patch("src.app.core.security.runtime_config.cache_client", new=stub)


# ==================== preserved store contract ====================


def test_redis_key_and_ttl_are_unchanged() -> None:
    """An operator's stored override must keep applying: same key, same TTL."""
    assert RUNTIME_CONFIG_KEY == "runtime_config:security"
    assert RUNTIME_CONFIG_TTL == 86400 * 30


def test_key_grammar_matches_the_settings_fields_it_overrides() -> None:
    """Every grammar key is a real ``settings.security`` field."""
    model_fields = type(settings.security).model_fields
    assert set(SECURITY_RUNTIME_KEYS) <= set(model_fields)


# ==================== the one writer ====================


@pytest.mark.asyncio
async def test_write_applies_to_settings_and_merges_into_the_store() -> None:
    original = settings.security.AUTO_BAN_THRESHOLD
    stub = _StubCache('{"IP_BLACKLIST_ENABLED": true, "AUTO_BAN_ENABLED": false}')
    try:
        with _patch_cache(stub):
            await apply_and_store_runtime_config({"AUTO_BAN_THRESHOLD": 42})

        assert settings.security.AUTO_BAN_THRESHOLD == 42
        key, value, ttl = stub.set_calls[0]
        assert key == RUNTIME_CONFIG_KEY
        assert ttl == RUNTIME_CONFIG_TTL
        # The stored blob is a merge: the keys the PATCH did not name survive.
        from src.app.core.json_utils import json_loads

        assert json_loads(value) == {
            "IP_BLACKLIST_ENABLED": True,
            "AUTO_BAN_ENABLED": False,
            "AUTO_BAN_THRESHOLD": 42,
        }
    finally:
        settings.security.AUTO_BAN_THRESHOLD = original


@pytest.mark.asyncio
async def test_write_with_a_typo_key_fails_loud_and_changes_nothing() -> None:
    """The regression: a typo'd UPPERCASE key used to be stored and ignored forever."""
    original = settings.security.AUTO_BAN_ENABLED
    stub = _StubCache(None)
    try:
        with _patch_cache(stub), pytest.raises(UnknownRuntimeConfigKeyError):
            await apply_and_store_runtime_config({"AUTO_BAN_ENABELD": False})

        assert settings.security.AUTO_BAN_ENABLED is original
        assert stub.set_calls == []
    finally:
        settings.security.AUTO_BAN_ENABLED = original


@pytest.mark.asyncio
async def test_write_with_an_invalid_value_fails_loud_before_applying() -> None:
    """Validation happens before any mutation, so a rejected PATCH is inert."""
    original = settings.security.AUTO_BAN_THRESHOLD
    stub = _StubCache(None)
    try:
        with _patch_cache(stub), pytest.raises(ValueError):
            await apply_and_store_runtime_config({"AUTO_BAN_ENABLED": True, "AUTO_BAN_THRESHOLD": {"nope": 1}})

        assert settings.security.AUTO_BAN_THRESHOLD is original
        assert stub.set_calls == []
    finally:
        settings.security.AUTO_BAN_THRESHOLD = original


@pytest.mark.asyncio
async def test_write_survives_an_unavailable_store() -> None:
    """A Redis outage must not fail the operator's PATCH (best-effort persist)."""
    original = settings.security.AUTO_BAN_ENABLED
    stub = _StubCache(None, get_raises=ConnectionError("redis down"))
    try:
        with _patch_cache(stub):
            await apply_and_store_runtime_config({"AUTO_BAN_ENABLED": False})

        assert settings.security.AUTO_BAN_ENABLED is False
        assert stub.set_calls == []
    finally:
        settings.security.AUTO_BAN_ENABLED = original


# ==================== the one reader ====================


@pytest.mark.asyncio
async def test_read_distinguishes_no_override_from_an_outage() -> None:
    with _patch_cache(_StubCache(None)):
        assert await read_stored_runtime_config() == {}

    with _patch_cache(_StubCache(None, get_raises=ConnectionError("redis down"))), pytest.raises(
        RuntimeConfigUnavailableError
    ):
        await read_stored_runtime_config()


@pytest.mark.asyncio
async def test_boot_applies_the_stored_overrides() -> None:
    original = settings.security.AUTO_BAN_THRESHOLD
    try:
        with _patch_cache(_StubCache('{"AUTO_BAN_THRESHOLD": "99"}')):
            assert await load_runtime_config() == ["AUTO_BAN_THRESHOLD"]
        assert settings.security.AUTO_BAN_THRESHOLD == 99
    finally:
        settings.security.AUTO_BAN_THRESHOLD = original


@pytest.mark.asyncio
async def test_boot_fails_open_loudly_when_the_store_cannot_be_read() -> None:
    """The stated decision: no stored overrides on an outage, logged at error."""
    fake_logger = MagicMock()
    with (
        _patch_cache(_StubCache(None, get_raises=ConnectionError("redis down"))),
        patch("src.app.core.security.runtime_config.logger", new=fake_logger),
    ):
        assert await load_runtime_config() == []

    fake_logger.error.assert_called_once()
    assert "unavailable" in fake_logger.error.call_args.args[0]
