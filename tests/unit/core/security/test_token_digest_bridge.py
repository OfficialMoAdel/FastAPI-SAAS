"""I2 regression: legacy bare-SHA-256 token rows keep authenticating.

The stored token digest changed from bare SHA-256 to peppered HMAC-SHA256.
Token tables are keyed on that digest, so any verify path that queries only
``hash_token(t)`` cannot see rows written by the previous function — every
outstanding credential dies on deploy, silently, as an empty result set.

These tests pin the migration bridge: a legacy-digest row still resolves and
migrates forward on first use. They fail without the bridge because
``hash_token`` never matches a legacy digest.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security.tokens import (
    hash_token,
    legacy_token_digest,
    token_digest_candidates,
    verify_token_digest,
)


class TestDigestBridge:
    """The digests themselves."""

    def test_legacy_digest_is_bare_sha256(self) -> None:
        assert legacy_token_digest("tok") == hashlib.sha256(b"tok").hexdigest()

    def test_current_digest_differs_from_legacy(self) -> None:
        # The whole point of I2: the formats must not collide.
        assert hash_token("tok") != legacy_token_digest("tok")

    def test_candidates_order_current_first(self) -> None:
        current, legacy = token_digest_candidates("tok")
        assert current == hash_token("tok")
        assert legacy == legacy_token_digest("tok")

    def test_candidates_never_collide(self) -> None:
        # A collision would make the bridge a no-op.
        for token in ("a", "", "1" * 64, "BC-12345-67890"):
            current, legacy = token_digest_candidates(token)
            assert current != legacy

    def test_verify_token_digest_accepts_both_formats(self) -> None:
        candidate = "some-magic-link-token"
        assert verify_token_digest(candidate, hash_token(candidate)) is True
        assert verify_token_digest(candidate, legacy_token_digest(candidate)) is True
        # A wrong token matches neither digest.
        assert verify_token_digest("other-token", hash_token(candidate)) is False
        assert verify_token_digest("other-token", legacy_token_digest(candidate)) is False

    def test_verify_token_digest_is_constant_time(self) -> None:
        # hmac.compare_digest is the timing-safe primitive; confirm it is the
        # one used rather than `==`.
        candidate = "tok"
        with patch("src.app.core.security.tokens.hmac.compare_digest", wraps=hmac.compare_digest) as spy:
            assert verify_token_digest(candidate, hash_token(candidate)) is True
        assert spy.called


class TestLegacyRowResolution:
    """Every lookup path must see a legacy-format row.

    These are the sites that broke when ``hash_token`` changed: each does a
    ``WHERE token = <digest>`` against rows written by the old function.
    """

    @pytest.mark.asyncio
    async def test_magic_link_lookup_finds_legacy_row_and_rehashes(self) -> None:
        from src.app.modules.auth import crud

        token = "legacy-magic-link"
        legacy_row = MagicMock(token=legacy_token_digest(token), used_at=None)
        captured: list[Any] = []

        async def _execute(stmt: Any) -> Any:
            captured.append(stmt)
            result = MagicMock()
            result.scalar_one_or_none.return_value = legacy_row
            return result

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_execute)
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        found = await crud.get_magic_link_by_token(db, token)

        assert found is legacy_row
        # THE BRIDGE: the query must cover BOTH digests, not just the current
        # one — otherwise this row (written pre-I2) is invisible and the link
        # is dead. A pre-fix single-digest query binds exactly one digest and
        # this assertion fails.
        literal_sql = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
        assert legacy_token_digest(token) in literal_sql
        assert hash_token(token) in literal_sql
        # The row migrated forward to the current digest.
        assert legacy_row.token == hash_token(token)
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_magic_link_lookup_finds_current_row_without_rehash(self) -> None:
        from src.app.modules.auth import crud

        token = "current-magic-link"
        current_row = MagicMock(token=hash_token(token), used_at=None)

        async def _execute(stmt: Any) -> Any:
            result = MagicMock()
            result.scalar_one_or_none.return_value = current_row
            return result

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_execute)
        db.commit = AsyncMock()

        found = await crud.get_magic_link_by_token(db, token)

        assert found is current_row
        # Already current: no write.
        assert current_row.token == hash_token(token)
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_magic_link_lookup_returns_none_for_unknown_token(self) -> None:
        from src.app.modules.auth import crud

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)

        assert await crud.get_magic_link_by_token(db, "no-such-token") is None
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_api_key_lookup_resolves_legacy_row_and_rehashes(self) -> None:
        from src.app.modules.api_keys.service import APIKeyService

        raw_key = "sk_legacy_0123456789abcdef"
        key = MagicMock(
            id=7,
            user_id=1,
            tenant_id=2,
            scopes='["read"]',
            hashed_key=legacy_token_digest(raw_key),
            valid_from=None,
            expires_at=None,
        )

        async def _execute(stmt: Any) -> Any:
            res = MagicMock()
            res.scalar_one_or_none.return_value = key
            return res

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_execute)
        service = APIKeyService(db)
        service.crud.update = AsyncMock()

        with patch("src.app.modules.api_keys.service.cache_client") as mock_cache:
            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()
            info = await service.validate_api_key(raw_key)

        assert info is not None
        assert info["key_id"] == 7
        # Row re-hashed to the current digest. The last_used_at bump also
        # routes through crud.update, so match on the payload, not the count.
        rehash = [
            call
            for call in service.crud.update.await_args_list
            if call.kwargs.get("object") == {"hashed_key": hash_token(raw_key)}
        ]
        assert len(rehash) == 1, "legacy row must be re-hashed exactly once"
        # Both digest cache keys populated so the next lookup hits either way.
        assert mock_cache.set.await_count == 2

    @pytest.mark.asyncio
    async def test_api_key_lookup_rejects_wrong_key(self) -> None:
        from src.app.modules.api_keys.service import APIKeyService

        db = AsyncMock()
        res = MagicMock()
        res.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=res)
        service = APIKeyService(db)

        with patch("src.app.modules.api_keys.service.cache_client") as mock_cache:
            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()
            assert await service.validate_api_key("sk_wrong") is None

        mock_cache.set.assert_not_awaited()
