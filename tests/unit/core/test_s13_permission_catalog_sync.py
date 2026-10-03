"""S-13 — the successor-permission migration was gated backwards.

`composition._start_permission_catalog_reconcile` reconciled the catalog and then ran

    if not report.stale:
        await migrate_successor_grants(db)

But `migrate_successor_grants` only does work when a STALE slug row actually
exists: it looks each `SUCCESSOR_SLUGS` key up in the persisted set and
`continue`s when absent (catalog.py:155-157). That row exists precisely when
`report.stale` is non-empty, since reconcile computes
`stale = existing - wanted` (catalog.py:106) and `files:manage` is persisted
but absent from the `Permission` enum.

So the old guard ran the migration exactly when there was nothing to migrate
(every lookup short-circuited) and skipped it exactly when there was. A role
holding the retired `files:manage` grant never received its
`files:write` / `files:delete` successors — silent, permanent permission loss.

Also pins S-10's observability half: the token-blacklist warmup logged nothing
when it found no rows, on the only warmup path in the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@dataclass
class _Report:
    stale: tuple[str, ...] = ()


@dataclass
class _Ctx:
    """Async context manager yielding a throwaway session."""

    async def __aenter__(self) -> MagicMock:
        return MagicMock()

    async def __aexit__(self, *exc: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_migration_runs_when_a_stale_slug_exists() -> None:
    """The case that was broken: stale present -> migration MUST run."""
    from src.app.composition import _start_permission_catalog_reconcile

    report = _Report(stale=("files:manage",))

    with (
        patch("src.app.modules.rbac.catalog.reconcile_permission_catalog", AsyncMock(return_value=report)),
        patch("src.app.modules.rbac.catalog.migrate_successor_grants", AsyncMock()) as migrate,
        patch("src.app.core.db.database.local_session", _Ctx),
    ):
        await _start_permission_catalog_reconcile()

    migrate.assert_awaited_once()


@pytest.mark.asyncio
async def test_migration_skipped_when_nothing_is_stale() -> None:
    """A clean catalog has nothing to carry forward, so the call is skipped."""
    from src.app.composition import _start_permission_catalog_reconcile

    report = _Report(stale=())

    with (
        patch("src.app.modules.rbac.catalog.reconcile_permission_catalog", AsyncMock(return_value=report)),
        patch("src.app.modules.rbac.catalog.migrate_successor_grants", AsyncMock()) as migrate,
        patch("src.app.core.db.database.local_session", _Ctx),
    ):
        await _start_permission_catalog_reconcile()

    migrate.assert_not_awaited()


@pytest.mark.asyncio
async def test_successor_slug_is_genuinely_stale() -> None:
    """Guard the premise: `files:manage` must not be in the Permission enum.

    If someone re-added it, `report.stale` could never contain it and the
    inverted condition would stop mattering — this test fails loudly if the
    premise is broken rather than silently passing.
    """
    from src.app.core.rbac.permissions import Permission
    from src.app.modules.rbac.catalog import SUCCESSOR_SLUGS

    for slug in SUCCESSOR_SLUGS:
        assert slug not in {p.value for p in Permission}, f"{slug} is no longer stale"


@pytest.mark.asyncio
async def test_empty_blacklist_warmup_is_logged() -> None:
    """S-10: an empty warmup must be observable, not silent.

    The query succeeds, so the `except (RedisError, SQLAlchemyError, ...)`
    cannot fire, and `warm_cache_from_db` has exactly one caller — so a silent
    empty warm left the revocation cache unmonitored.
    """
    from src.app.composition import _warm_token_blacklist_cache

    crud = MagicMock()
    crud.get_active_tokens = AsyncMock(return_value=[])

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop_bypass(session, *, reason: str, user_id: str | None = None):
        # S-10: the warmup now runs under an audited RLS bypass (the blacklist
        # is a global revocation oracle and RLS would otherwise return zero
        # rows at boot). The MagicMock session cannot satisfy that context.
        yield session

    with (
        patch("src.app.core.db.crud_token_blacklist.crud_token_blacklist", crud),
        patch("src.app.core.db.database.async_session_maker", _Ctx),
        patch("src.app.core.db.rls.bypass_rls", _noop_bypass),
        patch("src.app.core.security.token_blacklist_cache.warm_cache_from_db", AsyncMock()) as warm,
        patch("src.app.composition.logger") as log,
        patch("src.app.composition.settings.app.TOKEN_BLACKLIST_WARMUP_LIMIT", 100),
    ):
        await _warm_token_blacklist_cache()

    warm.assert_not_awaited()
    assert log.warning.called, "an empty warmup must warn, not pass silently"
