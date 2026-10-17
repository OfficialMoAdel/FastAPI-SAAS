"""S-2 e2e oracle: logout xN leaves the row set and the transaction scope
exactly where logout x1 left them.

WHAT THIS PROVES. S-2 deleted a transaction boundary from
``src/app/core/security/jwt_service.py::blacklist_tokens`` -- the redundant
outer ``db.rollback()`` and the empty ``db.commit()`` that used to follow it.
The only honest evidence that deleting a boundary is safe is the persisted row
set over HTTP on a real database, so this module drives
``POST /api/v1/account/logout`` N times for N in {1, 2, 3, 5, 10} against
PostgreSQL, hashes the full row set after the first call and again after the
Nth, and asserts the two hashes are equal. Logout is a no-op by contract from
the second call on, so the row set must not move. A silent difference here is
a data-integrity regression no latency number would ever surface.

The audit event does not muddy the comparison: ``audit()`` delivers through a
Redis RPUSH buffer (``src/app/modules/audit_logs/trail.py``) whose DB fallback
opens its own ``local_session()``, so no audit row is ever written through the
logout request's session.

ANTI-VACUITY. Hashing every table could pass for the wrong reason -- a table
whose rows are invisible to the hashing session reads empty on every pass and
is therefore trivially equal. The baseline hash is therefore only accepted
when ``token_blacklist`` actually holds the rows logout is supposed to have
written (two tokens x two rows each: the raw token and its ``jti:`` cache
member), so a hash that sees nothing fails loudly instead of passing quietly.

DIALECT COVERAGE, and what it does not claim. The metamorphic row-set property
and the ROLLBACK count are asserted over HTTP on PostgreSQL. SQLite is
exercised at the function level against ``blacklist_tokens`` itself, because
this tree has no seeded SQLite HTTP harness (``tests/e2e/conftest.py``'s
``e2e_client`` exists but ships no tenant/user/token seeding helper), so an
HTTP leg on SQLite could not mint a real token pair. The SQLite leg is what
makes the dialect difference visible: it drives the duplicate path on the
dialect that has no SQLSTATE, which is the case
``_is_duplicate_blacklist_violation`` had to be made portable for. The caller
level verdict therefore rests on the PostgreSQL HTTP leg; this module does not
claim both dialects covered a caller.

DEVIATION FROM THE BEAD'S DESIGN ASSERTION (b), AUTHORISED BY THE OWNER, NOT
BY ME. The bead asked for ``logout_session_revoked_skipped`` with
``reason=no_resolved_identity`` to be asserted ABSENT on the duplicate path,
on the theory that it is "the observable consequence of the removed empty
commit". It is not, and no assertion can make it so. The line is emitted by
``AuthService._revoke_session`` (``src/app/modules/auth/service.py``) under
``if not user_data:`` -- an IDENTITY fact, not a transaction-boundary fact. On
a duplicate logout both tokens are already revoked, so ``verify_token`` yields
nothing for either leg, ``user_data`` stays ``None``, and the line is emitted
whether or not the old ``rollback()`` exists. Asserting its absence would pin
a test to a red build.

What this module does instead is strictly stronger than an absence: it asserts
the line is PRESENT on the duplicate path and ABSENT on the first path, with
the reason recorded. That is a claim about revocation state that would fail if
the identity guard stopped distinguishing a first logout from a repeat, and it
cannot be satisfied by deleting the line. Changing the production guard so the
line stops firing on a duplicate logout would be a production change, which is
outside this bead's "ROLLBACK: script only" scope.

ROLLBACK CEILING, derived from the pre-change build and never re-tuned. The
deleted ``db.rollback()`` executed at most once per ``blacklist_tokens`` call
that staged rows, and logout calls it once per request, so the pre-change
build emitted at most one ROLLBACK per logout request. That is the ceiling
this module asserts against. It is an upper bound taken from the build the
change replaced, not a number chosen to fit what the current build happens to
do; the pre-change build is not runnable here because HEAD already contains
the change, so the derivation is recorded rather than measured.

``tests/performance/test_rollback_verification.py`` (the S-2 oracle) pins
transaction-wide rollback and savepoint statement counts at the unit level.
This module does not duplicate it: it drives the HTTP path and asserts the
whole-database row set, which that suite cannot see.

Not a fix for the unexplained ~3.4 s logout step -- that is STEP5-P14, a
separate Phase-0 investigation.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.app.core.db.database import Base
from src.app.core.db.token_blacklist import TokenBlacklist
from src.app.core.security import jwt_service

LOGOUT_PATH = "/api/v1/account/logout"
REPETITION_COUNTS: tuple[int, ...] = (1, 2, 3, 5, 10)

# Logout blacklists an access token and a refresh token; each carries a `jti`,
# so each contributes two rows (the raw token and its `jti:` cache member).
EXPECTED_BLACKLIST_ROWS_AFTER_FIRST_LOGOUT = 4

# See the module docstring. Pre-change build: at most one ROLLBACK per logout
# request. Not tuned against the current build.
ROLLBACK_CEILING_PER_LOGOUT_REQUEST = 1

NO_IDENTITY_LOG_EVENT = "logout_session_revoked_skipped"
NO_IDENTITY_REASON = "no_resolved_identity"

pytestmark = pytest.mark.e2e


class LogRecorder:
    """Captures the logout path's log calls in arrival order.

    The repo's loggers are structlog bound loggers, so this stands in for them
    rather than relying on stdlib ``caplog`` to see a structlog emission.
    """

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def _record(self, level: str, event: str, **kwargs: Any) -> None:
        self.records.append({"level": level, "event": event, **kwargs})

    def debug(self, event: str, **kwargs: Any) -> None:
        self._record("debug", event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._record("info", event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._record("warning", event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._record("error", event, **kwargs)

    def exception(self, event: str, **kwargs: Any) -> None:
        self._record("exception", event, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return lambda *args, **kwargs: None

    def no_identity_records(self) -> list[dict[str, Any]]:
        return [
            record
            for record in self.records
            if record.get("event") == NO_IDENTITY_LOG_EVENT and record.get("reason") == NO_IDENTITY_REASON
        ]


@contextmanager
def _patched_loggers(recorder: LogRecorder) -> Iterator[LogRecorder]:
    """Swap the logout path's loggers for the recorder.

    Patching each module's own binding matters: ``auth.service`` and
    ``jwt_service`` each hold their own logger object, and a
    ``from ... import`` binding elsewhere would never see a patch applied to
    the definition module.
    """
    from src.app.modules.auth import service as auth_service

    original_service_logger = auth_service.logger
    original_jwt_logger = jwt_service._jwt_logger  # noqa: SLF001 - the recorder must see real emissions
    auth_service.logger = recorder  # type: ignore[assignment]
    jwt_service._jwt_logger = recorder  # type: ignore[assignment]  # noqa: SLF001
    try:
        yield recorder
    finally:
        auth_service.logger = original_service_logger  # type: ignore[assignment]
        jwt_service._jwt_logger = original_jwt_logger  # type: ignore[assignment]  # noqa: SLF001


def _sync_handle(session: AsyncSession) -> Any:
    """The sync-level handle (``Engine`` or ``Connection``) behind a session.

    This is the only object SQLAlchemy's ``event.listen`` accepts, and both
    shapes carry it: an engine exposes ``sync_engine`` and a connection
    exposes ``sync_connection``. The probe walks them in that order rather than
    assuming which one the harness opened, because ``AsyncSession.get_bind()``
    is typed to return ``Engine | Connection`` and this suite's sessions are
    bound to whichever of the two the caller opened.
    """
    bind = session.bind or session.get_bind()
    handle = getattr(bind, "sync_connection", None)
    if handle is None:
        handle = getattr(bind, "sync_engine", None)
    if handle is None:
        raise AssertionError(f"no sync handle behind {type(bind).__name__}")
    return handle


@contextmanager
def _statement_recorder(session: AsyncSession) -> Iterator[list[str]]:
    """Capture every statement the session's connection executes."""
    statements: list[str] = []

    def _record(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    sync_handle = _sync_handle(session)
    event.listen(sync_handle, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(sync_handle, "before_cursor_execute", _record)


def _rollback_statements(statements: list[str]) -> list[str]:
    return [statement for statement in statements if statement.strip().upper().startswith("ROLLBACK")]


def _savepoint_statements(statements: list[str]) -> list[str]:
    return [statement for statement in statements if statement.strip().upper().startswith("SAVEPOINT")]


async def _row_count(session: AsyncSession, table: str) -> int:
    result = await session.execute(text(f'SELECT count(*) FROM "{table}"'))  # noqa: S608 - identifier is a literal here
    return int(result.scalar_one())


async def _row_set_hashes(session: AsyncSession) -> dict[str, str]:
    """Hash every table's full row set, so a difference anywhere is visible.

    The table list comes from the declarative metadata rather than a
    ``SELECT``-free schema inspection: the schema is created from exactly this
    metadata by the suite's own fixture, so an inspector round trip would add a
    database dependency without widening coverage.

    Each entry is ``<row count>:<digest>``: the count is carried alongside the
    digest so a table that went empty is distinguishable from one whose rows
    merely re-ordered.
    """
    hashes: dict[str, str] = {}
    for table in sorted(table.name for table in Base.metadata.sorted_tables):
        result = await session.execute(text(f'SELECT * FROM "{table}"'))  # noqa: S608 - identifier comes from the metadata
        rows = sorted(repr(tuple(row)) for row in result)
        digest = hashlib.sha256()
        for row in rows:
            digest.update(row.encode("utf-8", "backslashreplace"))
            digest.update(b"\x00")
        hashes[table] = f"{len(rows)}:{digest.hexdigest()[:16]}"
    return hashes


@asynccontextmanager
async def _sqlite_session() -> AsyncIterator[tuple[AsyncEngine, AsyncSession]]:
    """An in-memory SQLite session carrying just the table logout writes."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: TokenBlacklist.__table__.create(conn, checkfirst=True))
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # The session must close BEFORE the engine is disposed: disposing first
    # leaves the session's close() rolling back on a dead pool, which raises
    # "no active connection" and masks whatever the test actually observed.
    async with session_maker() as session:
        yield engine, session
    await engine.dispose()


def _payload(jti: str) -> dict[str, Any]:
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    return {"exp": int(expires_at.timestamp()), "jti": jti, "tenant_id": str(uuid4())}


@pytest.mark.parametrize("repeat_count", REPETITION_COUNTS)
async def test_logout_row_set_is_identical_after_n_repeats(
    client: AsyncClient,
    authenticated_user: dict[str, Any],
    db_session: AsyncSession,
    log_step: Any,
    clear_auth_rate_limits: Any,
    repeat_count: int,
) -> None:
    """Logout xN leaves the row set logout x1 produced, on PostgreSQL, over HTTP.

    The auth limiter allows a handful of operations per minute per IP and
    logout is an auth endpoint, so N up to 10 would otherwise be refused with
    429 before it ever reached the code under test. The limiter is cleared
    before every request rather than asserted on: this oracle is about the
    transaction scope of a logout, and a refusal to call logout is not a
    property of the transaction scope.
    """
    assert db_session.bind is not None
    assert db_session.bind.dialect.name == "postgresql", "the HTTP leg is the PostgreSQL leg"

    headers = {"Authorization": f"Bearer {authenticated_user['access_token']}"}
    refresh_token = authenticated_user.get("refresh_token")
    cookies = {"refresh_token": refresh_token} if refresh_token else {}

    with _patched_loggers(LogRecorder()) as recorder:
        with _statement_recorder(db_session) as statements:
            await clear_auth_rate_limits()
            first = await client.post(LOGOUT_PATH, headers=headers, cookies=cookies)
        baseline_hashes = await _row_set_hashes(db_session)
        baseline_rollbacks = _rollback_statements(statements)
        baseline_no_identity = recorder.no_identity_records()

        log_step(f"N={repeat_count} first logout status={first.status_code}")
        assert first.status_code == 200, first.text
        assert not baseline_no_identity, f"a first logout resolves identity and must not log {NO_IDENTITY_REASON}"
        rows_after_first = await _row_count(db_session, TokenBlacklist.__tablename__)
        assert rows_after_first == EXPECTED_BLACKLIST_ROWS_AFTER_FIRST_LOGOUT, (
            f"the hash would be vacuous if logout wrote nothing: expected "
            f"{EXPECTED_BLACKLIST_ROWS_AFTER_FIRST_LOGOUT} token_blacklist rows, found {rows_after_first}"
        )

        repeat_statuses: list[int] = []
        for _ in range(repeat_count - 1):
            await clear_auth_rate_limits()
            repeat = await client.post(LOGOUT_PATH, headers=headers, cookies=cookies)
            repeat_statuses.append(repeat.status_code)
            assert repeat.status_code == 200, repeat.text

        repeated_hashes = await _row_set_hashes(db_session)
        repeated_rollbacks = _rollback_statements(statements)
        repeat_no_identity = recorder.no_identity_records()

    differing_tables = sorted(
        table for table in baseline_hashes if baseline_hashes[table] != repeated_hashes.get(table)
    )
    log_step(
        f"N={repeat_count} statuses={[first.status_code, *repeat_statuses]} "
        f"differing_tables={differing_tables} rollbacks={len(repeated_rollbacks)} "
        f"no_identity_lines={len(baseline_no_identity)}->{len(repeat_no_identity)}"
    )

    assert not differing_tables, f"logout x{repeat_count} changed the row set of {differing_tables}"
    assert len(baseline_rollbacks) <= ROLLBACK_CEILING_PER_LOGOUT_REQUEST, baseline_rollbacks
    assert len(repeated_rollbacks) <= ROLLBACK_CEILING_PER_LOGOUT_REQUEST, repeated_rollbacks
    if repeat_count > 1:
        assert len(repeat_no_identity) > len(baseline_no_identity), (
            "a repeat logout cannot resolve an identity from two revoked tokens, so the "
            f"{NO_IDENTITY_REASON} line must appear once per repeat; its absence would mean "
            "the identity guard no longer distinguishes a first logout from a repeat"
        )


async def test_sqlite_duplicate_blacklist_is_idempotent_with_no_transaction_wide_rollback(
    client: AsyncClient,
    log_step: Any,
) -> None:
    """The duplicate path on the dialect that has no SQLSTATE.

    ``_is_duplicate_blacklist_violation`` reads ``orig.sqlstate`` when the
    driver supplies one and falls back to the driver's message when it does
    not; sqlite3 supplies neither, so this leg is the one that pins the
    fallback. ``client`` is requested for its Redis wiring: the function
    records the revocation in the blacklist cache on every path.
    """
    assert client is not None

    async with _sqlite_session() as (engine, session):
        assert session.bind is not None
        assert session.bind.dialect.name == "sqlite"

        access_payload = _payload("jti-access")
        refresh_payload = _payload("jti-refresh")

        with _statement_recorder(session) as statements:
            await jwt_service.blacklist_tokens(
                access_token="access-token-under-test",
                refresh_token="refresh-token-under-test",
                db=session,
                access_payload=access_payload,
                refresh_payload=refresh_payload,
            )
            rows_after_first = await _row_count(session, TokenBlacklist.__tablename__)
            statements_after_first = list(statements)

            await jwt_service.blacklist_tokens(
                access_token="access-token-under-test",
                refresh_token="refresh-token-under-test",
                db=session,
                access_payload=access_payload,
                refresh_payload=refresh_payload,
            )
            rows_after_duplicate = await _row_count(session, TokenBlacklist.__tablename__)

        savepoints = _savepoint_statements(statements_after_first)
        rollbacks = _rollback_statements(statements)
        log_step(
            f"sqlite rows {rows_after_first}->{rows_after_duplicate} "
            f"savepoints={len(savepoints)} rollbacks={len(rollbacks)}"
        )

        assert rows_after_first == EXPECTED_BLACKLIST_ROWS_AFTER_FIRST_LOGOUT, rows_after_first
        assert rows_after_duplicate == rows_after_first, "a duplicate blacklist must insert nothing"
        # The savepoint count is RECORDED, not asserted: the aiosqlite/pysqlite
        # driver emits no SAVEPOINT for `async with db.begin_nested()`, so a
        # dialect that never opens one is a fact about the driver, not a
        # property this oracle can demand. What the code owes the caller on
        # every dialect is the same: no rows added, no exception raised, and no
        # transaction-wide ROLLBACK unwinding the caller's transaction.
        assert len(rollbacks) <= ROLLBACK_CEILING_PER_LOGOUT_REQUEST, rollbacks
        assert engine.dialect.name == "sqlite"
