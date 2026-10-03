"""S-24 — the Redis revocation write must run on every `blacklist_token` path.

`blacklist_token` wrapped its DB work in `try: ... except IntegrityError:` and
the whole thing in `except JWTError:`. Any OTHER database fault — an
`OperationalError`, a `TimeoutError`, a driver error — propagated straight past
the `await add_token_to_blacklist(...)` on the next line.

So the comment "the Redis cache write runs on EVERY path (fresh insert,
IntegrityError retry, tenant-less fallback)" was false, and the consequence is
the one that matters: the token was blacklisted NOWHERE (not in Redis, not
committed to the DB) while the caller saw an exception.

`blacklist_tokens` (plural) already got this right — it logs the commit failure
and falls through. The singular path now mirrors it.

The regression is pinned by making the DB raise a non-IntegrityError and
asserting the Redis write still happened.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from src.app.core.security import jwt_service as js


@pytest.fixture
def deps():
    """Patch every collaborator so only the failure path under test varies."""
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    with (
        patch.object(js, "TokenBlacklist", MagicMock()),
        patch.object(js, "add_token_to_blacklist", AsyncMock(return_value=True)) as redis_write,
        patch.object(js, "_resolve_tenant_id_for_token", AsyncMock(return_value=uuid4())),
        patch("src.app.core.security.jwt_utils.decode_verified_token", AsyncMock(return_value=None)),
    ):
        yield db, redis_write


def _token() -> str:
    return "header.payload.sig"


class TestS24RedisWriteAlwaysRuns:
    @pytest.mark.asyncio
    async def test_non_integrity_db_error_still_writes_redis(self, deps) -> None:
        """The regression: a generic DB fault must not skip the Redis write.

        Before the fix this propagated straight past add_token_to_blacklist,
        so the token ended up blacklisted NOWHERE.
        """
        db, redis_write = deps
        db.commit = AsyncMock(side_effect=OperationalError("UPDATE", {}, Exception("connection lost")))
        payload = {"jti": "j1", "exp": (datetime.now(UTC) + timedelta(hours=1)).timestamp()}

        with patch("src.app.core.security.jwt_utils.decode_verified_token", AsyncMock(return_value=payload)):
            await js.blacklist_token(_token(), db)

        assert redis_write.await_count == 1, "the Redis revocation write was skipped after a DB fault"
        assert db.rollback.await_count >= 1, "the failed transaction must be rolled back"

    @pytest.mark.asyncio
    async def test_resolved_tenant_path_writes_redis(self, deps) -> None:
        """The normal (tenant resolved) path named in the B1 comment."""
        db, redis_write = deps
        payload = {"jti": "j1", "exp": (datetime.now(UTC) + timedelta(hours=1)).timestamp()}

        with patch("src.app.core.security.jwt_utils.decode_verified_token", AsyncMock(return_value=payload)):
            await js.blacklist_token(_token(), db)

        assert redis_write.await_count == 1

    @pytest.mark.asyncio
    async def test_normal_path_writes_redis(self, deps) -> None:
        db, redis_write = deps
        payload = {"jti": "j1", "exp": (datetime.now(UTC) + timedelta(hours=1)).timestamp()}

        with patch("src.app.core.security.jwt_utils.decode_verified_token", AsyncMock(return_value=payload)):
            await js.blacklist_token(_token(), db)

        assert redis_write.await_count == 1

    def test_handler_catches_more_than_integrity_error(self) -> None:
        """Structural: the inner handler must not be IntegrityError-only.

        A single IntegrityError `except` is what allowed the S-24 gap; this
        fails if someone narrows it back.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(js))
        fn = next(
            n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "blacklist_token"
        )
        caught: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                name = ast.unparse(node.type)
                caught.add(name)
        assert any(
            "IntegrityError" in c and c not in ("IntegrityError",) for c in caught
        ) or "Exception" in caught, f"blacklist_token only catches {caught}"
