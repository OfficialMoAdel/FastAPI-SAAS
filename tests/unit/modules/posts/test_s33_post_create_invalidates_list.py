"""S-33 — creating a post must invalidate the cached post list.

`write_post` was the only mutating route in `modules/posts/router.py` with no
cache decorator, so it never dropped the `user_posts:{username}:*` list cache
that `read_posts` fills. `patch_post` and `erase_post` both carried
`pattern_to_invalidate_extra` for that list; the create path was missed, so a
newly created post stayed invisible until those entries aged out (60s default).

The invalidation is explicit (`invalidate_by_pattern`) rather than via
`@cache(..., pattern_to_invalidate_extra=...)` because this route has no
resource id and `@cache` raises `CacheIdentificationInferenceError` without
one. A create endpoint has no prior cache entry for the new resource anyway —
only the LIST pattern needs dropping.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROUTER = Path("src/app/modules/posts/router.py")


def _fn(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


def _decorators(name: str) -> list[str]:
    return [ast.unparse(d) for d in _fn(name).decorator_list]


class TestWritePostInvalidatesTheList:
    @pytest.mark.asyncio
    async def test_create_drops_the_user_posts_pattern(self) -> None:
        """The pattern MUST be scoped — this is the regression guard.

        The first version of this fix called
        `invalidate_by_pattern("user_posts:bob:*")`. That reads a DIFFERENT
        Redis set than the one `read_posts` registers into: the decorator
        registers under
        `_pattern_index_key(_scoped_invalidation_pattern(...))`, i.e.
        `tenant:{id}:user:{uid}:user_posts:bob:*`. The bare pattern is always
        empty, so the call returned 0 and the list was never dropped — the
        write was safe and the bug it was filed for was still there.

        The assertion below therefore pins the SCOPED name, not just "it was
        called". Asserting only the call would pass against the broken code.
        """
        from src.app.modules.posts.router import write_post

        service = MagicMock()
        service.create_post = AsyncMock(return_value={"id": 7, "title": "hi"})
        ctx = MagicMock()
        ctx.user = MagicMock(id=1)
        ctx.user.username = "bob"

        with (
            patch("src.app.modules.posts.router._require_post_owner"),
            patch("src.app.modules.posts.router.get_current_tenant", return_value=None),
            patch(
                "src.app.core.utils.cache.patterns.invalidate_by_pattern",
                new_callable=AsyncMock,
                return_value=2,
            ) as invalidate,
        ):
            await write_post(MagicMock(), "bob", MagicMock(), ctx, service)

        invalidate.assert_awaited_once()
        pattern = invalidate.await_args[0][0]
        assert pattern == "public:user:1:user_posts:bob:*", (
            f"pattern must be scoped like the decorator's registration, got {pattern!r}"
        )

    @pytest.mark.asyncio
    async def test_cache_failure_does_not_fail_the_write(self) -> None:
        """The post is already created; a cache error must not 500 the POST."""
        from src.app.modules.posts.router import write_post

        service = MagicMock()
        service.create_post = AsyncMock(return_value={"id": 7})
        ctx = MagicMock()
        ctx.user = MagicMock(id=1)
        ctx.user.username = "bob"

        with (
            patch("src.app.modules.posts.router._require_post_owner"),
            patch("src.app.modules.posts.router.logger"),
            patch(
                "src.app.core.utils.cache.patterns.invalidate_by_pattern",
                new_callable=AsyncMock,
                side_effect=ConnectionError("redis down"),
            ),
        ):
            result = await write_post(MagicMock(), "bob", MagicMock(), ctx, service)

        assert result == {"id": 7}, "the write result must survive a cache outage"

    def test_invalidation_happens_after_the_create(self) -> None:
        """Invalidating first would leave a window where the list re-caches stale."""
        fn = _fn("write_post")
        create_line = next(
            n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and "create_post" in ast.dump(n.func)
        )
        invalidate_line = next(
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and "invalidate_by_pattern" in ast.dump(n.func)
        )
        assert create_line < invalidate_line


class TestSiblingsAlreadyCovered:
    """Control: the create path was the only mutating route without this."""

    @pytest.mark.parametrize("route", ["patch_post", "erase_post", "erase_db_post"])
    def test_siblings_declare_pattern_invalidation(self, route: str) -> None:
        """Read decorators via AST, not a line window.

        `erase_db_post` carries a long comment block inside its decorator, so
        any fixed line-offset scan produces false negatives.
        """
        cache_args = next((n for n in _decorators(route) if n.startswith("cache(")), "")
        assert "pattern_to_invalidate_extra" in cache_args, (
            f"{route} must invalidate the user_posts list; decorators={_decorators(route)}"
        )

    def test_write_post_has_no_cache_decorator(self) -> None:
        """It must not regress to a decorator that cannot infer a resource id."""
        names = _decorators("write_post")
        assert not any(n.startswith("cache(") for n in names), (
            f"write_post has no resource id; @cache would raise CacheIdentificationInferenceError: {names}"
        )
