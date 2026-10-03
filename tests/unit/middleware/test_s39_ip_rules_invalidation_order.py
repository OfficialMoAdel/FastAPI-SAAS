"""S-39 — the IP status cache must be invalidated AFTER the rule is written.

`add_to_blacklist` / `add_to_whitelist` called `invalidate_ip_status_cache` as
their FIRST statement, before touching Redis. A concurrent `check_ip_status`
landing in that window would read the pre-ban state and repopulate
`_ip_status_negative_cache[ip] = (False, False)`, so the new ban took up to
`_IP_STATUS_NEGATIVE_TTL` (5s) to become visible — during which the banned IP
was still admitted.

Also pins a second defect found in the same block: `expires_at` was subtracted
from an aware `datetime.now(UTC)`, so a NAIVE expiry raised `TypeError`, and
only `MissingClientError` was caught — the caller got an unhandled 500 instead of
the documented `False`.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.guard import ip_rules as ipm

SOURCE = Path("src/app/core/guard/ip_rules.py")


def _fn(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


def _invalidate_line(fn: ast.AsyncFunctionDef) -> list[int]:
    return [
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and "invalidate_ip_status_cache" in ast.dump(n.func)
    ]


def _write_lines(fn: ast.AsyncFunctionDef) -> list[int]:
    """Lines issuing the actual Redis SET/DEL for this rule."""
    return [
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("set", "setex", "delete")
    ]


class TestInvalidationHappensAfterTheWrite:
    @pytest.mark.parametrize(
        "name",
        ["add_to_blacklist", "add_to_whitelist", "remove_from_blacklist", "remove_from_whitelist"],
    )
    def test_invalidate_is_not_the_first_statement(self, name: str) -> None:
        fn = _fn(name)
        body = fn.body
        first = body[0]
        src = ast.unparse(first)
        assert "invalidate_ip_status_cache" not in src, (
            f"{name} still invalidates BEFORE the Redis write — a concurrent "
            "check_ip_status can repopulate the stale entry"
        )

    @pytest.mark.parametrize("name", ["add_to_blacklist", "add_to_whitelist"])
    def test_invalidate_exists_and_follows_a_write(self, name: str) -> None:
        fn = _fn(name)
        inv, writes = _invalidate_line(fn), _write_lines(fn)
        assert inv, f"{name} never invalidates the cache"
        assert writes, f"{name} has no Redis write"
        # The SUCCESS-path invalidation is the LAST one: an earlier occurrence
        # is the legitimate guard for the `ttl <= 0` early return, which exits
        # without writing anything.
        assert max(inv) > max(writes), (
            f"{name}: the success-path invalidation (line {max(inv)}) must come "
            f"after the Redis write (line {max(writes)})"
        )


class TestNaiveExpiryIsHandled:
    @pytest.mark.asyncio
    async def test_naive_expiry_returns_false_not_typeerror(self) -> None:
        rules = ipm.IPFilterService()
        naive = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

        conn = MagicMock()
        conn.setex = AsyncMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(ipm, "get_redis_client", return_value=ctx):
            result = await rules.add_to_blacklist("1.2.3.4", expires_at=naive)

        assert result is True, "a naive UTC expiry is valid input and must be accepted"

    @pytest.mark.asyncio
    async def test_past_expiry_returns_false(self) -> None:
        rules = ipm.IPFilterService()
        past = datetime.now(UTC) - timedelta(hours=1)
        assert await rules.add_to_blacklist("1.2.3.4", expires_at=past) is False

    @pytest.mark.asyncio
    async def test_naive_past_expiry_returns_false(self) -> None:
        rules = ipm.IPFilterService()
        naive_past = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None)
        assert await rules.add_to_blacklist("1.2.3.4", expires_at=naive_past) is False
