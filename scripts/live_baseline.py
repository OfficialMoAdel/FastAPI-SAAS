"""Live baseline harness — exercises the real optimized code paths against the
live Redis (192.168.121.147:6379) and PostgreSQL (192.168.121.147:5432) VM.

Scenarios (see scripts/BASELINE.md):
  S1  BaseService.get_multi query count          (P5: expect 1 SELECT, no COUNT)
  S2  tenant L2 cold lookup Redis command shape  (P7: expect 1 pipeline round)
  S3  token_version concurrent-miss herd         (P6: expect 1 SELECT for N calls)
  N1  upload txn split A/B connection hold       (flag off vs on)

Run:  python scripts/live_baseline.py
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import time
from pathlib import Path
from uuid import UUID

# --- env bootstrap (mirrors root conftest.py) -------------------------------
_ROOT = Path(__file__).resolve().parent.parent
from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / "src" / ".env", override=True)
os.environ.setdefault("ENVIRONMENT", "local")
os.environ["TESTING"] = "0"  # real pool (not NullPool) so checkout/checkin events fire

sys.path.insert(0, str(_ROOT))

RESULTS: dict[str, dict] = {}


def _record(name: str, **data) -> None:
    RESULTS[name] = data
    print(f"[{name}] " + " | ".join(f"{k}={v}" for k, v in data.items()))


def _import_all_models() -> None:
    """Load every ORM model so SQLAlchemy can configure mappers/relationships
    (the app does this via router imports at startup)."""
    import importlib

    for mod in (
        "src.app.modules.tenants.models",
        "src.app.modules.users.models",
        "src.app.modules.auth.models",
        "src.app.modules.storage.models",
        "src.app.modules.notifications.models",
        "src.app.modules.rbac.models",
        "src.app.modules.posts.models",
        "src.app.modules.tiers.models",
        "src.app.modules.billing.models",
        "src.app.modules.audit_logs.models",
    ):
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError:
            pass


async def bootstrap():
    """Initialize Redis client + DB engine through the app's own seams."""
    _import_all_models()

    from src.app.core.setup import create_redis_cache_pool

    await create_redis_cache_pool()

    from src.app.core.db.database import get_async_engine
    from src.app.core.redis_provider import get_redis

    redis_client = get_redis()
    assert redis_client is not None, "Redis client not initialized"
    await redis_client.ping()

    engine = get_async_engine()
    return redis_client, engine


# --- S1: P5 query count ------------------------------------------------------
async def s1_get_multi_query_count(engine) -> None:
    import asyncpg
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.core.tenancy.context import TenantContext, set_current_tenant
    from src.app.modules.posts import PostService

    conn = await asyncpg.connect(
        host=os.environ["POSTGRES_SERVER"],
        port=int(os.environ["POSTGRES_PORT"]),
        database=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        timeout=5,
    )
    t = await conn.fetchrow("SELECT id, slug FROM tenants LIMIT 1")
    await conn.close()
    set_current_tenant(
        TenantContext(tenant_id=t["id"], tenant_slug=t["slug"], tenant_name="E2E", is_active=True)
    )

    statements: list[str] = []

    def _before_cursor(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _before_cursor)
    try:
        maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            svc = PostService(db)
            rows = await svc.get_multi(page=1, per_page=10)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _before_cursor)

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    counts = [s for s in selects if "count(" in s.lower()]
    _record(
        "S1_get_multi",
        rows_returned=len(rows),
        total_statements=len(statements),
        selects=len(selects),
        count_queries=len(counts),
        verdict="PASS: no COUNT" if not counts else "FAIL: COUNT still issued",
    )


# --- S2: P7 tenant L2 pipeline ----------------------------------------------
class _RedisSpy:
    """Wraps a real redis client, counting top-level command calls vs pipelines."""

    def __init__(self, real):
        self._real = real
        self.pipeline_calls = 0
        self.exists_calls = 0
        self.get_calls = 0
        self.pipeline_executes = 0

    def pipeline(self, *args, **kwargs):
        self.pipeline_calls += 1
        real_pipe = self._real.pipeline(*args, **kwargs)
        spy = self

        class _PipeSpy:
            def __getattr__(self, name):
                return getattr(real_pipe, name)

            async def execute(self):
                spy.pipeline_executes += 1
                return await real_pipe.execute()

        return _PipeSpy()

    async def exists(self, *args, **kwargs):
        self.exists_calls += 1
        return await self._real.exists(*args, **kwargs)

    async def get(self, *args, **kwargs):
        self.get_calls += 1
        return await self._real.get(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


async def s2_tenant_l2_pipeline(redis_client) -> None:
    from unittest.mock import patch

    from src.app.middleware.tenant import _L1_TENANT_CACHE, TenantMiddleware

    slug = "test-tenant-e2e"
    _L1_TENANT_CACHE.clear()
    await redis_client.delete(f"tenant:slug:{slug}", f"tenant:negative:{slug}")

    # Seed L2 so the lookup hits Redis (not DB)
    import asyncpg

    conn = await asyncpg.connect(
        host=os.environ["POSTGRES_SERVER"],
        port=int(os.environ["POSTGRES_PORT"]),
        database=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        timeout=5,
    )
    row = await conn.fetchrow("SELECT id, slug, is_active FROM tenants WHERE slug=$1", slug)
    await conn.close()
    from src.app.core.json_utils import json_dumps

    await redis_client.set(
        f"tenant:slug:{slug}",
        json_dumps({"id": str(row["id"]), "slug": row["slug"], "is_active": row["is_active"]}),
        ex=3600,
    )

    spy = _RedisSpy(redis_client)
    middleware = TenantMiddleware(app=None)
    with patch("src.app.middleware.tenant.get_redis", return_value=spy):
        t0 = time.perf_counter()
        tenant = await middleware._get_tenant_by_slug(slug)
        elapsed_ms = (time.perf_counter() - t0) * 1000

    _record(
        "S2_tenant_L2",
        resolved=tenant is not None,
        latency_ms=round(elapsed_ms, 2),
        pipeline_calls=spy.pipeline_calls,
        pipeline_executes=spy.pipeline_executes,
        separate_exists_calls=spy.exists_calls,
        separate_get_calls=spy.get_calls,
        verdict=(
            "PASS: single pipeline round, no separate exists/get"
            if spy.pipeline_calls == 1 and spy.exists_calls == 0 and spy.get_calls == 0
            else "FAIL"
        ),
    )

    # negative-cache path: one pipeline round must short-circuit
    _L1_TENANT_CACHE.clear()
    await redis_client.delete(f"tenant:slug:{slug}")
    await redis_client.set(f"tenant:negative:{slug}", "1", ex=60)
    spy2 = _RedisSpy(redis_client)
    with patch("src.app.middleware.tenant.get_redis", return_value=spy2):
        t0 = time.perf_counter()
        neg = await middleware._get_tenant_by_slug(slug)
        neg_ms = (time.perf_counter() - t0) * 1000
    _record(
        "S2_tenant_negative",
        returned_none=neg is None,
        latency_ms=round(neg_ms, 2),
        pipeline_calls=spy2.pipeline_calls,
        l1_negative_cached=_L1_TENANT_CACHE.get(slug, "missing") is None,
    )
    await redis_client.delete(f"tenant:negative:{slug}")


# --- S3: P6 token_version singleflight --------------------------------------
async def s3_token_version_herd(engine, redis_client) -> None:
    import asyncpg
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.core.security.jwt_service import TokenType, create_access_token, verify_token
    from src.app.core.tenancy.context import TenantContext, set_current_tenant

    conn = await asyncpg.connect(
        host=os.environ["POSTGRES_SERVER"],
        port=int(os.environ["POSTGRES_PORT"]),
        database=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        timeout=5,
    )
    u = await conn.fetchrow("SELECT id, username, email, token_version, tenant_id FROM users LIMIT 1")
    await conn.close()

    tenant_id = u["tenant_id"]
    set_current_tenant(
        TenantContext(
            tenant_id=tenant_id, tenant_slug="test-tenant-e2e", tenant_name="E2E", is_active=True
        )
    )

    token = await create_access_token(
        {"sub": u["username"], "tenant_id": str(tenant_id), "token_version": u["token_version"]}
    )

    # Flush the cached token_version so every verify_token hits the DB path
    from src.app.core.utils.cache import build_tenant_key

    cache_key = build_tenant_key(f"user:{u['username']}:token_version", str(tenant_id))
    await redis_client.delete(cache_key)

    statements: list[str] = []

    def _before_cursor(conn_, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _before_cursor)

    N = 10
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _one():
        async with maker() as db:
            return await verify_token(token, TokenType.ACCESS, db)

    try:
        results = await asyncio.gather(*(_one() for _ in range(N)))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _before_cursor)

    user_selects = [
        s for s in statements if s.lstrip().upper().startswith("SELECT") and "users" in s.lower()
    ]
    valid = sum(1 for r in results if r is not None)
    _record(
        "S3_token_version_herd",
        concurrent_calls=N,
        valid_tokens=valid,
        user_selects=len(user_selects),
        cache_backfilled=await redis_client.exists(cache_key) == 1,
        verdict="PASS: singleflight" if len(user_selects) == 1 and valid == N else "FAIL",
    )
    await redis_client.delete(cache_key)


# --- N1: upload txn split A/B ------------------------------------------------
class _SlowFakeProvider:
    """Fake storage provider with a configurable upload latency."""

    def __init__(self, delay_s: float):
        self.delay_s = delay_s
        self.upload_started: float | None = None
        self.upload_finished: float | None = None

    async def upload(self, file, path, mime_type):
        from src.app.modules.storage.interfaces import StorageResult

        self.upload_started = time.perf_counter()
        await asyncio.sleep(self.delay_s)
        self.upload_finished = time.perf_counter()
        return StorageResult(success=True, path=path, size=0)

    async def delete(self, path):
        from src.app.modules.storage.interfaces import StorageResult

        return StorageResult(success=True, path=path)


async def n1_upload_txn_split(engine, redis_client) -> None:
    import asyncpg
    from sqlalchemy import event, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.core.config import settings as app_settings
    from src.app.core.tenancy.context import TenantContext, set_current_tenant
    from src.app.modules.storage.service import FileService

    conn = await asyncpg.connect(
        host=os.environ["POSTGRES_SERVER"],
        port=int(os.environ["POSTGRES_PORT"]),
        database=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        timeout=5,
    )
    u = await conn.fetchrow("SELECT id, tenant_id FROM users LIMIT 1")
    await conn.close()
    tenant_id: UUID = u["tenant_id"]
    user_id: int = u["id"]

    set_current_tenant(
        TenantContext(
            tenant_id=tenant_id, tenant_slug="test-tenant-e2e", tenant_name="E2E", is_active=True
        )
    )

    UPLOAD_DELAY = 0.20
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Track connection hold windows via pool checkout/checkin
    holds: list[tuple[float, float]] = []
    open_at: dict[int, float] = {}

    def _checkout(dbapi_conn, conn_record, conn_proxy):
        open_at[id(dbapi_conn)] = time.perf_counter()

    def _checkin(dbapi_conn, conn_record):
        started = open_at.pop(id(dbapi_conn), None)
        if started is not None:
            holds.append((started, time.perf_counter()))

    event.listen(engine.sync_engine, "checkout", _checkout)
    event.listen(engine.sync_engine, "checkin", _checkin)

    created_ids: list[int] = []

    async def _run(flag: bool) -> dict:
        app_settings.performance.FEATURE_UPLOAD_TXN_SPLIT = flag
        holds.clear()
        provider = _SlowFakeProvider(UPLOAD_DELAY)
        async with maker() as db:
            svc = FileService(db)
            async def _fake_resolve(tid):
                from src.app.modules.storage.admin import ResolvedStorage

                return ResolvedStorage(
                    provider=provider,  # type: ignore[arg-type]
                    provider_name='local',
                    max_file_size_mb=1024,
                    allowed_file_types=None,
                    max_file_count=10**6,
                    storage_quota_bytes=10**12,
                )

            svc.admin.resolve = _fake_resolve  # type: ignore[method-assign]
            content = b"baseline harness plain text content"
            rec = await svc.upload_file(
                tenant_id=tenant_id,
                user_id=user_id,
                file=io.BytesIO(content),
                filename="baseline.txt",
                mime_type="text/plain",
                size_bytes=len(content),
            )
            created_ids.append(rec.id)
        # connection returned when session closes
        upload_dur = provider.upload_finished - provider.upload_started
        overlapping = [
            (s, e)
            for (s, e) in holds
            if s < provider.upload_finished and e > provider.upload_started
        ]
        hold_ms = [round((e - s) * 1000, 1) for (s, e) in holds]
        return {
            "flag": flag,
            "upload_ms": round(upload_dur * 1000, 1),
            "conn_holds_ms": hold_ms,
            "holds_overlapping_upload": len(overlapping),
        }


    try:
        off = await _run(False)
        on = await _run(True)
    finally:
        event.remove(engine.sync_engine, "checkout", _checkout)
        event.remove(engine.sync_engine, "checkin", _checkin)
        app_settings.performance.FEATURE_UPLOAD_TXN_SPLIT = False

    _record(
        "N1_upload_txn_split",
        off_holds_ms=off["conn_holds_ms"],
        off_overlapping_upload=off["holds_overlapping_upload"],
        on_holds_ms=on["conn_holds_ms"],
        on_overlapping_upload=on["holds_overlapping_upload"],
        upload_ms=off["upload_ms"],
        verdict=(
            "PASS: flag ON decouples connection from upload"
            if off["holds_overlapping_upload"] >= 1 and on["holds_overlapping_upload"] == 0
            else "CHECK"
        ),
    )

    # Cleanup: remove harness rows, reset denormalized counters
    async with maker() as db:
        for fid in created_ids:
            await db.execute(text("DELETE FROM files WHERE id = :id"), {"id": fid})
        await db.execute(
            text(
                "UPDATE tenants SET file_count = 0, storage_usage_bytes = 0 WHERE id = :tid"
            ),
            {"tid": tenant_id},
        )
        await db.commit()


async def main() -> None:
    redis_client, engine = await bootstrap()
    print(f"Redis: {await redis_client.ping()} | engine pool: {engine.pool.__class__.__name__}")

    await s1_get_multi_query_count(engine)
    await s2_tenant_l2_pipeline(redis_client)
    await s3_token_version_herd(engine, redis_client)
    await n1_upload_txn_split(engine, redis_client)

    # Persist results
    out_dir = _ROOT / "scripts" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out_file = out_dir / f"{stamp}-live-baseline.json"
    import json

    out_file.write_text(json.dumps(RESULTS, indent=2, default=str), encoding="utf-8")
    print(f"\nResults written to {out_file.relative_to(_ROOT)}")

    await redis_client.aclose()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
