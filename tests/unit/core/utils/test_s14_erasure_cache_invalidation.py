"""S-14 — a GDPR erasure must actually evict the cached copy.

`erase_db_post` asked `invalidate_tenant_cache` for the shard
`v2:user_post_cache:{username}:{id}`, but the WRITER shards on
`_format_prefix(key_prefix, kwargs)` alone — i.e. `user_post_cache:{username}`.
Neither the `v2:` version token nor the trailing `:id` is part of the shard
name.

An empty shard made `invalidate_tenant_cache` `return 0` — silently — and the
caller ignored the return value, so nothing could detect the miss. A hard-deleted
post then kept being served from cache for the full TTL (3600s), on a route
whose whole purpose is erasure.

Root cause was in the invalidation seam, not the call site: `tenant_index.py`
computed `global_index_key` and then never used it, with a comment conceding the
fallback "should" happen and declining to implement it. The fix is implemented
here — a shard miss now falls back to the global index and filters by prefix —
AND the caller's prefix is corrected to match the writer.
"""

from __future__ import annotations

import ast
from pathlib import Path

TENANT_INDEX = Path("src/app/core/utils/cache/tenant_index.py")
POSTS_ROUTER = Path("src/app/modules/posts/router.py")
DECORATOR = Path("src/app/core/utils/cache/decorator.py")


def _fn(path: Path, name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


class TestShardMissFallsBackToTheGlobalIndex:
    def test_the_fallback_is_actually_implemented(self) -> None:
        """The concession in the old comment ("we should fall back") must be code."""
        fn = _fn(TENANT_INDEX, "invalidate_tenant_cache")
        src = ast.unparse(fn)
        assert "global_index_key" in src
        # A real fallback reads the global set and filters it.
        assert src.count("smembers") >= 2, "the global index is never read as a fallback"

    def test_fallback_only_triggers_on_an_empty_shard(self) -> None:
        """Falling back unconditionally would do a full sweep on every call."""
        src = ast.unparse(_fn(TENANT_INDEX, "invalidate_tenant_cache"))
        assert "if not keys" in src, "the fallback must be gated on an empty shard"


class TestErasureCallerUsesTheWritersShardName:
    def test_prefix_matches_the_writer(self) -> None:
        """The caller's prefix must be the writer's shard name."""
        router = POSTS_ROUTER.read_text(encoding="utf-8")
        assert 'prefix=f"user_post_cache:{username}"' in router, (
            "the erasure must pass the prefix the writer shards on"
        )
        assert "v2:user_post_cache" not in router, (
            "the version token and resource id are NOT part of the shard name"
        )

    def test_writer_shard_excludes_the_version_and_id(self) -> None:
        """Guards the premise: the writer really does shard on the prefix alone."""
        decorator = DECORATOR.read_text(encoding="utf-8")
        assert "_tenant_prefix_index_key(" in decorator
        shard_call = decorator.split("_tenant_prefix_index_key(")[1][:200]
        assert "_format_prefix(" in shard_call, (
            "the writer shards on the formatted key_prefix — so callers must too"
        )

    def test_erasure_reads_the_tenant_by_truthiness(self) -> None:
        """S-16: `is not None` turns the NO_TENANT sentinel into a UUID-zero id."""
        router = POSTS_ROUTER.read_text(encoding="utf-8")
        assert "if tenant is not None:" not in router.split("invalidate_tenant_cache")[0][-400:], (
            "the erasure must read the tenant by truthiness, not `is not None`"
        )


class TestNoSilentNoOpRemains:
    def test_zero_eviction_is_reported_not_discarded(self) -> None:
        """A 0 return means "nothing evicted" — the erasure must surface it.

        Asserted by BINDING the return value and branching on it, so a nearby
        unrelated `log` token cannot make this pass for the wrong reason.
        """
        fn = _fn(POSTS_ROUTER, "erase_db_post")
        bound = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Assign) and "invalidate_tenant_cache" in ast.unparse(n.value)
        ]
        assert bound, "invalidate_tenant_cache's result is discarded"
        name = bound[0].targets[0].id if isinstance(bound[0].targets[0], ast.Name) else None
        assert name, "the result must be bound to a name"
        branch = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.If)
            and any(
                isinstance(t, ast.Name) and t.id == name and isinstance(t.ctx, ast.Load)
                for t in ast.walk(n.test)
            )
        ]
        assert branch, f"the bound result `{name}` is never inspected"
