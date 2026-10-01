"""Magic-link redemption claims its row atomically.

Magic links are single-use credentials: the second redeemer must never get a
session. The claim is a conditional UPDATE (``WHERE id = :id AND used_at IS
NULL``) whose ``rowcount`` decides the race, fed by a SELECT ... FOR UPDATE —
the same idiom ``passwords.reset_password`` and
``email_verification.verify_email`` use to hold the row lock across their
``used`` check and their single-use marker.

Proven here, in two layers that cannot substitute for each other:

* the DECISION, with two genuinely concurrent transactions against real SQL —
  both redeemers read the link before either writes it, and exactly one wins;
* the MECHANISM, as the SQL PostgreSQL actually executes. FOR UPDATE is a
  no-op on SQLite, so the lock itself is asserted by compiling the statement
  for the production dialect rather than pretending SQLite enforced it.

The stale-reader case is additionally forced with ``autoflush=False`` so the
loser's ORM state cannot be flushed over the winner's write — the race is
driven by the barrier and the claim, not by an ORM side effect.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.app.core.security.tokens import hash_token, legacy_token_digest
from src.app.modules.auth.crud import get_magic_link_by_token, mark_magic_link_used
from src.app.modules.auth.errors import InvalidMagicLinkError, MagicLinkAlreadyUsedError
from src.app.modules.auth.models import MagicLink
from src.app.modules.auth.services.magic_link import MagicLinkService
from src.app.modules.auth.services.mfa import MFAService

TOKEN = "concurrent-redemption-probe-token-000000"


# ---------------------------------------------------------------------------
# Database harness
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def claim_engine():
    """Engine on a private WAL-mode SQLite file holding just ``magic_links``.

    WAL lets a reader keep its snapshot while a writer commits, which is what
    makes the stale-read interleaving below reachable at all. The pragmas are
    re-applied per connect because NullPool hands out a new connection each
    time.
    """

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    try:
        async with engine.begin() as conn:
            await conn.run_sync(MagicLink.__table__.create)
        yield engine
    finally:
        await engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass


@pytest_asyncio.fixture
def sessions(claim_engine):
    """Builds a session maker for genuinely independent connections.

    Returned as a callable because ``autoflush`` is a constructor argument,
    not a ``maker()`` argument: the stale-reader test needs a maker with
    autoflush off, and the rest need the default.
    """
    def _make(**kwargs: Any) -> Any:
        return async_sessionmaker(
            claim_engine, class_=AsyncSession, expire_on_commit=False, **kwargs
        )

    return _make


@pytest_asyncio.fixture
def unused_link(sessions):
    """Seeds one unused magic link row, keyed on the digest of ``TOKEN``."""
    async def _seed() -> None:
        async with sessions()() as session:
            session.add(
                MagicLink(
                    tenant_id=uuid4(),
                    user_id=7,
                    token=hash_token(TOKEN),
                    action="login",
                    expires_at=datetime.now(UTC) + timedelta(minutes=15),
                )
            )
            await session.commit()

    return _seed


def _rendezvous(parties: int) -> Any:
    """A gate every caller must reach before any of them proceeds.

    Without it the winner would normally commit before the loser has even read
    the row, and the test would pass without ever interleaving.
    """
    gate = asyncio.Event()
    arrived = 0
    lock = asyncio.Lock()

    async def wait() -> None:
        nonlocal arrived
        async with lock:
            arrived += 1
            if arrived == parties:
                gate.set()
        await gate.wait()

    return wait


# ---------------------------------------------------------------------------
# Layer 1: the decision, under a real overlap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_claims_grant_exactly_one_redemption(sessions, unused_link):
    """Two overlapping redeemers of one link: one wins, one is refused.

    Both read the row before either writes it, so neither can be protected by
    having read late — the ``used_at IS NULL`` predicate on the claim is the
    only thing that separates them.
    """
    await unused_link()
    gate = _rendezvous(2)
    reads: list[Any] = []
    outcomes: list[str] = []

    async def redeem() -> None:
        async with sessions()() as session:
            row = await get_magic_link_by_token(db=session, token=TOKEN, for_update=True)
            assert row is not None
            reads.append(row.used_at)
            await gate()
            try:
                await mark_magic_link_used(db=session, magic_link=row)
                outcomes.append("claimed")
            except MagicLinkAlreadyUsedError:
                outcomes.append("refused")

    await asyncio.gather(redeem(), redeem())

    assert reads == [None, None], f"the race must be live: both read the link unused, got {reads}"
    assert sorted(outcomes) == ["claimed", "refused"], f"exactly one winner, got {outcomes}"

    async with sessions()() as check:
        rows = (await check.execute(select(MagicLink))).scalars().all()
        assert len(rows) == 1
        assert rows[0].used_at is not None, "the link must be marked used exactly once"


@pytest.mark.asyncio
async def test_stale_reader_cannot_steal_a_committed_claim(sessions, unused_link):
    """A redeemer holding a pre-commit snapshot still loses.

    The loser reads the link as unused, the winner commits, and only then does
    the loser claim. ``autoflush=False`` matters here: with autoflush on, the
    loser's dirty ORM state would be written before its UPDATE, resetting
    ``used_at`` to NULL and letting the replay through — the test would then be
    measuring ORM behaviour, not the claim.
    """
    await unused_link()

    # autoflush=False: the loser must not be able to flush its own (NULL)
    # read of used_at over the winner's committed write.
    stale_maker = sessions(autoflush=False)
    loser = stale_maker()
    try:
        loser_row = await get_magic_link_by_token(db=loser, token=TOKEN, for_update=True)
        assert loser_row is not None and loser_row.used_at is None

        # The winner claims and commits while the loser still holds its read.
        async with sessions()() as winner:
            row = await get_magic_link_by_token(db=winner, token=TOKEN, for_update=True)
            await mark_magic_link_used(db=winner, magic_link=row)

        with pytest.raises(MagicLinkAlreadyUsedError):
            await mark_magic_link_used(db=loser, magic_link=loser_row)
    finally:
        await loser.rollback()
        await loser.close()

    async with sessions()() as check:
        used = (await check.execute(text("SELECT used_at FROM magic_links"))).scalar_one()
        assert used is not None, "the winner's claim must survive the loser's failed attempt"


@pytest.mark.asyncio
async def test_single_redeem_succeeds(sessions, unused_link):
    """The lock does not break the ordinary path."""
    await unused_link()
    async with sessions()() as session:
        row = await get_magic_link_by_token(db=session, token=TOKEN, for_update=True)
        assert row is not None and row.used_at is None
        await mark_magic_link_used(db=session, magic_link=row)

    async with sessions()() as session:
        again = await get_magic_link_by_token(db=session, token=TOKEN)
        assert again is not None and again.used_at is not None


@pytest.mark.asyncio
async def test_exactly_one_of_two_concurrent_verifies_issues_a_session(sessions, unused_link):
    """End to end through the service: one minted session, one refusal.

    Only the Redis token store and token minting are stubbed — and the Redis
    stub deliberately hands the payload to *both* callers, as a non-GETDEL
    read would. The refusal therefore has to come from the database claim, not
    from the cache.
    """
    await unused_link()

    # The token payload carries no ``extra_data.user_id``, so user resolution
    # takes the legacy email branch and calls ``self._get_user_by_email`` — a
    # method on the instance under test.
    manager = AsyncMock()
    manager.peek.return_value = {"email": "user@example.com"}
    manager.consume.side_effect = lambda _token: {"email": "user@example.com"}

    user = {
        "id": 7,
        "email": "user@example.com",
        "is_active": True,
        "is_deleted": False,
        "tenant_id": str(uuid4()),
    }

    issued: list[int] = []
    refused: list[str] = []

    async def _tokens(*_a: Any, **_k: Any) -> dict[str, Any]:
        issued.append(7)
        return {"access_token": "at", "token_type": "bearer", "expires_in": 3600}

    async def redeem() -> None:
        async with sessions()() as session:
            service = MagicLinkService(db=session, redis=AsyncMock())
            # Per-instance seams; safe to nest because each service is its own
            # object.
            with (
                patch.object(service, "_get_magic_link_manager", return_value=manager),
                patch.object(service, "_get_user_by_email", return_value=user),
                patch.object(service, "_create_auth_tokens", side_effect=_tokens),
            ):
                try:
                    await service.verify_magic_link(token=TOKEN, ip_address="192.0.2.1")
                except MagicLinkAlreadyUsedError as exc:
                    refused.append(str(exc))

    # MFAService is instantiated inside verify_magic_link, so the CLASS is the
    # only patch target — and it is shared by both redeemers. The patch must
    # therefore wrap the whole gather: nested per-coroutine patches on one
    # class attribute un-nest out of order, and the loser restores the pristine
    # method while its sibling is still verifying, which let the real MFA query
    # run and fail on an absent mfa_methods table.
    with patch.object(MFAService, "challenge_if_required", new=AsyncMock(return_value=None)):
        await asyncio.gather(redeem(), redeem())

    assert len(issued) == 1, f"a single-use link must mint exactly one session, got {len(issued)}"
    assert len(refused) == 1, f"the second redeemer must be refused, refusals={refused}"


# ---------------------------------------------------------------------------
# Layer 2: the mechanism, as PostgreSQL executes it
# ---------------------------------------------------------------------------


def _recording_db(row: Any = None, rowcount: int | None = None) -> tuple[AsyncMock, list[Any]]:
    """An AsyncSession stub that records the statements it is handed."""
    captured: list[Any] = []

    async def _execute(stmt: Any) -> Any:
        captured.append(stmt)
        return SimpleNamespace(scalar_one_or_none=lambda: row, rowcount=rowcount)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    return db, captured


def _pg_sql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class TestClaimSql:
    def test_claim_is_conditional_on_used_at_is_null(self) -> None:
        """The guard that actually decides the race."""
        db, captured = _recording_db(rowcount=1)
        asyncio.run(mark_magic_link_used(db=db, magic_link=SimpleNamespace(id=42)))

        sql = _pg_sql(captured[0])
        assert "UPDATE magic_links" in sql
        assert "used_at IS NULL" in sql, f"the claim lost its single-use predicate: {sql}"
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_zero_rowcount_refuses_and_commits_nothing(self) -> None:
        """The loser of a race must not record anything."""
        db, _ = _recording_db(rowcount=0)

        with pytest.raises(MagicLinkAlreadyUsedError):
            await mark_magic_link_used(db=db, magic_link=SimpleNamespace(id=1))
        db.commit.assert_not_awaited()

    def test_redemption_lookup_takes_a_row_lock(self) -> None:
        """FOR UPDATE is the password-reset idiom, applied to redemption."""
        db, captured = _recording_db()
        asyncio.run(get_magic_link_by_token(db, TOKEN, for_update=True))

        assert "FOR UPDATE" in _pg_sql(captured[0]), (
            f"the redemption lookup is not locked: {_pg_sql(captured[0])}"
        )

    def test_plain_lookup_is_unlocked(self) -> None:
        """Non-redemption callers keep the cheap unlocked read."""
        db, captured = _recording_db()
        asyncio.run(get_magic_link_by_token(db, TOKEN))

        assert "FOR UPDATE" not in _pg_sql(captured[0])

    @pytest.mark.asyncio
    async def test_locked_legacy_rehash_flushes_instead_of_committing(self) -> None:
        """A commit inside the locked region would release the lock early.

        That is the bug already fixed in ``passwords.reset_password`` and
        ``email_verification.verify_email``: the row was migrated forward in
        its own transaction while still unused, so a concurrent redeemer
        re-acquired the lock and re-read ``used_at`` as NULL.
        """
        row = SimpleNamespace(token=legacy_token_digest(TOKEN))
        db, _ = _recording_db(row=row)

        await get_magic_link_by_token(db, TOKEN, for_update=True)

        assert row.token == hash_token(TOKEN), "the legacy row must migrate forward"
        db.flush.assert_awaited_once()
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unlocked_legacy_rehash_still_commits(self) -> None:
        """The non-locking caller keeps its original migrate-and-commit path."""
        row = SimpleNamespace(token=legacy_token_digest(TOKEN))
        db, _ = _recording_db(row=row)

        await get_magic_link_by_token(db, TOKEN)

        assert row.token == hash_token(TOKEN)
        db.commit.assert_awaited_once()


class TestRedemptionRequestsTheLock:
    """The service must ask for the lock, and must let it go."""

    @pytest.mark.asyncio
    async def test_verify_claims_under_a_row_lock(self) -> None:
        lookup = AsyncMock(return_value=None)
        manager = AsyncMock()
        manager.peek.return_value = {"email": "user@example.com"}
        manager.consume.return_value = None

        db, _ = _recording_db()
        service = MagicLinkService(db=db, redis=AsyncMock())

        with (
            patch.object(service, "_get_magic_link_manager", return_value=manager),
            patch("src.app.modules.auth.crud.get_magic_link_by_token", lookup),
        ):
            with pytest.raises(MagicLinkAlreadyUsedError):
                await service.verify_magic_link(token=TOKEN, ip_address="192.0.2.1")

        assert lookup.await_args is not None
        assert lookup.await_args.kwargs.get("for_update") is True, (
            "verify_magic_link must take the row lock the way reset_password does"
        )

    @pytest.mark.asyncio
    async def test_refusal_releases_the_transaction(self) -> None:
        """A raise inside the locked region must not strand the lock.

        ``verify_magic_link`` opens the transaction before running its guards.
        Without an explicit rollback the losing request would hold the row
        lock while the exception unwinds, turning a two-request race into a
        stalled one.
        """
        row = SimpleNamespace(
            user_id=7,
            used_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        manager = AsyncMock()
        manager.peek.return_value = {"email": "user@example.com"}

        db, _ = _recording_db(row=row)
        service = MagicLinkService(db=db, redis=AsyncMock())

        with (
            patch.object(service, "_get_magic_link_manager", return_value=manager),
            patch("src.app.modules.auth.crud.get_magic_link_by_token", AsyncMock(return_value=row)),
        ):
            with pytest.raises(MagicLinkAlreadyUsedError):
                await service.verify_magic_link(token=TOKEN, ip_address="192.0.2.1")

        db.rollback.assert_awaited()
        manager.consume.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_token_does_not_open_a_transaction(self) -> None:
        """The peek-miss branch must not take a lock it cannot use."""
        db, _ = _recording_db(row=None)
        manager = AsyncMock()
        manager.peek.return_value = None

        service = MagicLinkService(db=db, redis=AsyncMock())
        with patch.object(service, "_get_magic_link_manager", return_value=manager):
            with pytest.raises(InvalidMagicLinkError):
                await service.verify_magic_link(token=TOKEN, ip_address="192.0.2.1")

        db.rollback.assert_not_awaited()
        manager.consume.assert_not_awaited()


class TestSqliteDoesNotHonourTheLock:
    """Documents why the lock is proven by compiled SQL, not by execution.

    If a future dependency bump makes SQLite honour FOR UPDATE, this test
    starts failing and tells us the concurrency coverage here can be upgraded
    from "decision proven, mechanism proven separately" to "both proven in
    one place".
    """

    def test_for_update_is_absent_from_the_sqlite_rendering(self) -> None:
        db, captured = _recording_db()
        asyncio.run(get_magic_link_by_token(db, TOKEN, for_update=True))

        from sqlalchemy.dialects import sqlite

        assert "FOR UPDATE" not in str(captured[0].compile(dialect=sqlite.dialect()))
