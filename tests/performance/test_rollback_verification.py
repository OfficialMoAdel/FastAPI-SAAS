"""S-2 oracle: the logout transaction handling, pinned BEFORE the diff lands.

WHAT THIS PINS
``jwt_service.blacklist_tokens`` stages a ``TokenBlacklist`` row per token, and
on a duplicate it hits the unique constraint inside a ``begin_nested()``
savepoint. The savepoint rolls ITSELF back, and then ``jwt_service.py:578``
issues a session-level ``await db.rollback()`` — which unwinds the WHOLE outer
transaction, not the savepoint. ``:582``'s ``commit()`` then commits an empty
transaction and expires the entire identity map.

THE FAILURE MODE IS SILENT ROW LOSS, NOT AN EXCEPTION. Nothing raises. A caller
that staged unrelated work in the same session loses it, and the caller is a
logout endpoint that stages nothing today — which is the only reason this is a
latent bug rather than an active one. ``FastAPI_saas-3a56`` (C-32) proved that
the single caller (``auth/service.py:633``, inside ``logout``) holds no pending
non-blacklist mutation, so deleting ``:578`` is the legal fix.

WHY AN ORACLE AND NOT A FIX
The fix is one deleted line. The risk is that deleting it changes something
nobody thought to check. So the oracle lands first, green against HEAD with no
source change, and the diff then has to keep it green.

TEST 1 - GOLDEN ROW-SET, ``logout xN == logout x1``
``logout`` twice for the same token is not two logouts. It is one logout and one
no-op, and the row-set after N attempts must equal the row-set after one. A
golden row-set captured once and compared against ``x{2,3,5,10}`` catches the
identity-map expiry, because an expired identity map makes the second attempt
re-insert (and re-``add``) rows the session had already staged.

``id`` is EXCLUDED from the comparison. It is an autoincrement primary key, so
its value is a function of insertion history rather than of content, and two
runs that produce identical content in a different order are not a defect.

TEST 2 - THE TRANSACTION-WIDE ROLLBACK COUNT, AS AN ABSOLUTE FIXED CEILING
Statements are classified from ``before_cursor_execute`` into savepoint-scoped
(``SAVEPOINT`` / ``RELEASE SAVEPOINT`` / ``ROLLBACK TO SAVEPOINT``) and
transaction-wide (a bare ``ROLLBACK``). Only the transaction-wide count is gated,
and it is gated against an ABSOLUTE ceiling set at the value observed on HEAD.

THIS IS NOT ``new < old``, AND THE DIFFERENCE IS LOAD-BEARING. A bare
``assert bare_rollbacks < 2`` passes whenever the machine is slow, whenever the
dialect emits a different number of statements, and whenever the code changes in
a way that has nothing to do with the defect. The ceiling below was read off
HEAD once. Re-deriving it from a fresh run is exactly what
``src/benchmarks/protocol.py`` exists to prevent: a ceiling derived from a
previous measurement is ``new < old`` wearing a different hat. When S-2 lands
and the count goes to zero, the ceiling is LOWERED to zero in the same commit
that deletes ``:578``, deliberately, so the change is visible in the diff.

TEST 3 - THE ``no_resolved_identity`` DETECTOR, AS A DELIBERATE PAIR
``_revoke_session`` logs ``logout_session_revoked_skipped`` with
``reason="no_resolved_identity"`` whenever ``verify_token`` could not resolve an
identity — which is exactly what a duplicate logout produces, because the token
is already blacklisted. Two tests, and both are needed:

- ``test_the_detector_sees_the_signal_on_head`` asserts the line FIRES. Without
  it, the companion test below could pass for the wrong reason: a log line that
  never fires would make "it does not fire" trivially true.
- ``test_duplicate_logout_does_not_log_no_resolved_identity`` asserts it does NOT
  fire. **This is RED on HEAD by design.** It is the assertion the S-2 diff has
  to turn green, and leaving it red is the whole point of an oracle — a suite
  that is green before and after the change cannot tell you the change did
  anything.

WHY ``verify_token`` IS PATCHED IN TEST 3, AND WHAT THAT DOES NOT PROVE
Reaching ``_revoke_session`` with ``user_data is None`` requires a real
already-blacklisted token, which requires minting a real signed JWT for a real
user and letting the DB-fallback revocation path discover it. The duplicate
state is instead produced directly by patching ``service_mod.verify_token`` to
return ``None``, which is the value it returns in that situation.

That patch proves the DETECTOR fires when identity resolution fails. It does NOT
prove identity resolution fails on a duplicate logout. That half is Test 1's
job: the golden row-set shows the rows are committed, and
``is_token_revoked`` reads them back through its own session
(``jwt_service._check_db_fallback``). The two tests together cover the path;
neither covers it alone.

DIALECT
Parametrised over the dialect so a second dialect is a fixture away. ONLY SQLITE
RUNS TODAY — the postgres leg is recorded as NOT DONE, and
``tests/conftest.py:2124`` (``real_postgres`` / ``real_postgres_engine``) is the
concrete hook the follow-up bead should use. A golden row-set captured on sqlite
alone will not catch dialect-specific identity-map behaviour, which is why the
deferral is recorded here rather than left implicit.

EXTERNAL SEAMS PATCHED, AND WHY ONLY THESE
``add_tokens_to_blacklist`` (the Redis hot-path cache) and ``audit``. Neither is
under test — the code under test is ``blacklist_tokens``' transaction handling.
``session_manager.invalidate_session`` is deliberately NOT patched: it already
returns ``False`` when it has no Redis client and swallows every exception
(``session_manager.py:299-317``), so it needs no live Redis to be safe.

NOT DONE, BY USER INSTRUCTION: this file is not committed. Repo git policy for
this session forbids ``git commit`` and ``git push``; the acceptance clause
"is committed" is unmet by instruction, not by omission.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from src.app.core.db.token_blacklist import TokenBlacklist
from src.app.core.security import jwt_service as jwt_service_mod
from src.app.modules.auth import AuthService
from src.app.modules.auth import service as auth_service_mod

# --------------------------------------------------------------------------
# dialects
# --------------------------------------------------------------------------

#: SQLite is the session default and is the only leg that RUNS. The postgres
#: leg is a follow-up bead; naming it here is what makes the deferral a task
#: rather than an omission.
DIALECTS: tuple[str, ...] = (
    "sqlite",
)
POSTGRES_FOLLOW_UP_HOOK = "tests/conftest.py:2124 real_postgres / real_postgres_engine"

# --------------------------------------------------------------------------
# ceilings, read off HEAD once (see the module docstring)
# --------------------------------------------------------------------------

#: How many TRANSACTION-WIDE ``ROLLBACK`` statements one duplicate-logout attempt
#: emits on HEAD. Observed value: 1 — the redundant ``jwt_service.py:578``. When
#: S-2 lands this becomes 0 and the ceiling is lowered in the same commit that
#: deletes the line.
TRANSACTION_WIDE_ROLLBACK_CEILING = 1

#: Savepoint statements one duplicate-logout attempt emits. The measured value
#: is ZERO, and it is asserted as equality rather than as an upper bound. An
#: explicit ``begin_nested()`` on the same session immediately afterwards emits
#: SAVEPOINT, RELEASE SAVEPOINT and ROLLBACK TO SAVEPOINT, so the driver is not
#: hiding them: the duplicate path simply does not produce them on this
#: dialect. An earlier ceiling of 12 here passed against an empty bucket, which
#: is how twelve tests in this file were found to be vacuous.
SAVEPOINT_STATEMENT_CEILING = 0

#: The row-set ``logout x1`` must produce. Two rows per token: the raw token and
#: its ``jti:`` cache key (``jwt_service.py`` stages both).
EXPECTED_ROWS_PER_TOKEN = 2

NO_RESOLVED_IDENTITY_EVENT = "logout_session_revoked_skipped"


# --------------------------------------------------------------------------
# statement classification
# --------------------------------------------------------------------------


def _classify(statement: str) -> str:
    """Bucket one SQL statement by the scope of its transaction control.

    The distinction is the whole test: ``ROLLBACK TO SAVEPOINT sa`` unwinds one
    savepoint, a bare ``ROLLBACK`` unwinds the caller's entire transaction. They
    look similar in a log and cost vastly different amounts of pending work.
    """
    normalised = " ".join(statement.split()).upper()
    if not normalised.startswith(("ROLLBACK", "SAVEPOINT", "RELEASE")):
        return "other"
    if normalised.startswith("RELEASE SAVEPOINT"):
        return "savepoint"
    if normalised.startswith("ROLLBACK TO SAVEPOINT") or normalised.startswith("ROLLBACK TO"):
        return "savepoint"
    if normalised.startswith("SAVEPOINT"):
        return "savepoint"
    return "transaction_wide"


@dataclass
class StatementLedger:
    """Every statement the code under test emitted, bucketed by scope."""

    buckets: dict[str, int] = field(default_factory=lambda: {"savepoint": 0, "transaction_wide": 0, "other": 0})
    statements: list[str] = field(default_factory=list)
    #: Transaction-wide rollbacks seen on the ``rollback`` ConnectionEvent.
    #: Kept apart from ``statements`` because that event carries no SQL text —
    #: a bare ROLLBACK is never compiled, so there is nothing to quote.
    connection_rollbacks: list[str] = field(default_factory=list)

    def record(self, statement: str) -> None:
        self.statements.append(statement)
        self.buckets[_classify(statement)] += 1

    def record_transaction_wide_rollback(self) -> None:
        """Count one transaction-wide rollback seen on the ConnectionEvent."""
        self.connection_rollbacks.append("ROLLBACK")
        self.buckets["transaction_wide"] += 1

    @property
    def transaction_wide(self) -> int:
        return self.buckets["transaction_wide"]

    @property
    def savepoint(self) -> int:
        return self.buckets["savepoint"]

    def saw_any_statement(self) -> bool:
        """Whether the ledger observed anything at all.

        A ceiling read off a ledger that saw nothing is not a measurement, it is
        an absence, and it passes. Every ceiling assertion pairs with this so a
        listener that silently stops firing fails loudly instead.
        """
        return bool(self.statements) or bool(self.connection_rollbacks)


@contextmanager
def _statement_ledger(session: AsyncSession) -> Iterator[StatementLedger]:
    """Listen on the session's own engine for the duration of the block.

    Bound to ``session.get_bind()`` rather than to the global ``Engine``, so the
    ledger sees only what THIS session emits and cannot be perturbed by
    concurrent fixtures.

    TWO SEAMS ARE NECESSARY, and the reason is the whole reason this ledger was
    rewritten once. ``before_cursor_execute`` sees every *compiled* statement,
    which includes SAVEPOINT, RELEASE SAVEPOINT and ROLLBACK TO SAVEPOINT. It
    does NOT see a bare transaction-wide ROLLBACK: that is not a compiled
    statement at all. SQLAlchemy issues it through ``Connection._rollback_impl``
    onto the DBAPI connection (``do_rollback``), so no cursor is ever created
    and no cursor event fires. An earlier version of this file counted
    transaction-wide rollbacks here and observed exactly zero for a duplicate
    logout, which made twelve ceiling tests pass vacuously; the negative test is
    what caught it.

    A bare ROLLBACK is observable as the ``rollback`` ConnectionEvent, which is
    what the ``_on_rollback`` listener below records. A cursor-shaped statement
    is observable as ``before_cursor_execute``, which is what ``_before``
    records. Both feed the same ledger so a caller cannot tell which seam a
    given count came from, and :meth:`StatementLedger.saw_any_statement` exists
    so a caller can refuse to read a count off a ledger that saw nothing.
    """
    ledger = StatementLedger()

    def _before(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        del conn, cursor, parameters, context, executemany
        ledger.record(statement)

    def _on_rollback(conn: Any) -> None:
        del conn
        ledger.record_transaction_wide_rollback()

    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", _before)
    event.listen(bind, "rollback", _on_rollback)
    try:
        yield ledger
    finally:
        event.remove(bind, "before_cursor_execute", _before)
        event.remove(bind, "rollback", _on_rollback)


# --------------------------------------------------------------------------
# row-set helpers
# --------------------------------------------------------------------------

#: The comparable identity of a blacklisted row. ``id`` is deliberately absent.
GOLDEN_COLUMNS = ("token", "expires_at", "tenant_id")


async def _row_set(db: AsyncSession, tokens: tuple[str, ...]) -> list[tuple[Any, ...]]:
    """The golden row-set for ``tokens``, ordered, with ``id`` excluded.

    ``id`` is an autoincrement primary key: its value is a function of insertion
    history, not of content. Two runs producing identical content in a different
    order are not a defect, so including it would make this a test of the
    database's sequence rather than of the transaction handling.
    """
    result = await db.execute(select(TokenBlacklist).where(TokenBlacklist.token.in_(tokens)))
    rows = [tuple(getattr(row, column) for column in GOLDEN_COLUMNS) for row in result.scalars().all()]
    return sorted(rows, key=lambda row: str(row[0]))


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def tenant_id(db_session: AsyncSession) -> Any:
    """A tenant UUID that satisfies ``TokenBlacklist.tenant_id``'s NOT NULL FK.

    The FK is not enforced by the SQLite build this suite runs against, so the
    value need not exist — but it must be present and well-formed, because a
    ``None`` would take ``blacklist_tokens`` down the ``tenant_id`` unresolvable
    branch instead of the branch under test.
    """
    return uuid4()


@pytest.fixture
def payload_factory() -> Any:
    """Build a decoded JWT payload of the shape ``blacklist_tokens`` reads.

    Passing the payload in (``access_payload=`` / ``refresh_payload=``) is what
    lets this suite run without minting a signed JWT: the function decodes only
    when it was not handed the payload, and the payload is precisely the
    transaction-handling input under test.
    """

    def _make(jti: str, tenant: Any, lifetime_s: int = 900) -> dict[str, Any]:
        return {
            "jti": jti,
            "tenant_id": str(tenant),
            "exp": int((datetime.now(UTC) + timedelta(seconds=lifetime_s)).timestamp()),
        }

    return _make


@pytest.fixture
async def patched_seams() -> AsyncIterator[dict[str, AsyncMock]]:
    """Replace the two external seams; nothing else.

    ``add_tokens_to_blacklist`` is the Redis hot-path cache — asserting against
    it would make this a test of a live Redis. ``audit`` is best-effort and
    irrelevant to transaction handling.
    """
    cache = AsyncMock(return_value=0)
    audit = AsyncMock()
    with (
        patch.object(jwt_service_mod, "add_tokens_to_blacklist", cache),
        patch.object(auth_service_mod, "audit", audit),
    ):
        yield {"add_tokens_to_blacklist": cache, "audit": audit}


async def _logout_n_times(
    db: AsyncSession,
    token: str,
    payload: dict[str, Any],
    times: int,
) -> None:
    """Drive ``blacklist_tokens`` ``times`` with the SAME token.

    The first call stages and commits; every later call re-stages identical rows
    and takes the duplicate path, which is the path with the redundant rollback.
    """
    for _ in range(times):
        await jwt_service_mod.blacklist_tokens(
            access_token=token,
            refresh_token=None,
            db=db,
            access_payload=dict(payload),
        )


# --------------------------------------------------------------------------
# TEST 1 - the golden row-set
# --------------------------------------------------------------------------


class TestTheGoldenRowSetIsTheSameForOneLogoutAndForMany:
    """``logout xN == logout x1``: an idempotent endpoint is one that changes nothing."""

    async def test_one_logout_stages_exactly_the_expected_rows(
        self,
        db_session: AsyncSession,
        tenant_id: Any,
        payload_factory: Any,
        patched_seams: dict[str, AsyncMock],
    ) -> None:
        """The golden row-set itself, pinned so the comparison below is anchored.

        Without this, "the row-sets are equal" could be satisfied by both being
        empty — which is what a broken INSERT would produce.
        """
        del patched_seams
        token = "s2-oracle-access-token-1"
        payload = payload_factory("s2-oracle-jti-1", tenant_id)

        await _logout_n_times(db_session, token, payload, 1)
        await db_session.commit()

        rows = await _row_set(db_session, (token, "jti:s2-oracle-jti-1"))
        assert len(rows) == EXPECTED_ROWS_PER_TOKEN, (
            f"expected {EXPECTED_ROWS_PER_TOKEN} rows (the raw token and its jti key), got {rows}. "
            "If this is empty the INSERT never ran and every equality assertion below is vacuous."
        )
        assert {row[0] for row in rows} == {token, "jti:s2-oracle-jti-1"}
        assert all(row[2] == tenant_id for row in rows)

    @pytest.mark.parametrize("times", [2, 3, 5, 10])
    async def test_repeated_logout_leaves_the_row_set_untouched(
        self,
        db_session: AsyncSession,
        tenant_id: Any,
        payload_factory: Any,
        patched_seams: dict[str, AsyncMock],
        times: int,
    ) -> None:
        """The metamorphic property: the SAME token at x{times} must equal it at x1.

        This is the assertion that catches silent row loss. When the redundant
        rollback expires the identity map, the second attempt's ``db.add`` is a
        fresh INSERT rather than a no-op, and the row-set either grows or the
        commit commits nothing at all.

        The SAME token is the whole point. Comparing a once-logged-out token's
        rows against a different token's rows would compare two different values
        and prove nothing, which is the mistake the first draft of this test
        made.
        """
        del patched_seams
        token = "s2-oracle-repeated-token"
        jti = "s2-oracle-repeated-jti"
        keys = (token, f"jti:{jti}")
        payload = payload_factory(jti, tenant_id)

        await _logout_n_times(db_session, token, payload, 1)
        await db_session.commit()
        golden = await _row_set(db_session, keys)
        assert len(golden) == EXPECTED_ROWS_PER_TOKEN

        await _logout_n_times(db_session, token, payload, times - 1)
        await db_session.commit()
        after = await _row_set(db_session, keys)

        assert after == golden, (
            f"logout x{times} left a different row-set than logout x1 for the SAME token. "
            f"x1={golden} x{times}={after}. "
            "A repeated logout must be a no-op, not a second logout."
        )

    async def test_the_row_set_never_grows_with_attempts(
        self,
        db_session: AsyncSession,
        tenant_id: Any,
        payload_factory: Any,
        patched_seams: dict[str, Any],
    ) -> None:
        """Row COUNT is pinned per attempt, not merely equal across attempts.

        Equality between two attempts would survive a uniform regression that
        changed both. This asserts the absolute shape: N attempts, still exactly
        two rows.
        """
        del patched_seams
        token = "s2-oracle-count-token"
        payload = payload_factory("s2-oracle-count-jti", tenant_id)

        counts: list[int] = []
        for attempt in range(4):
            await _logout_n_times(db_session, token, payload, 1)
            await db_session.commit()
            counts.append(len(await _row_set(db_session, (token, "jti:s2-oracle-count-jti"))))
            del attempt

        assert counts == [EXPECTED_ROWS_PER_TOKEN] * 4, f"row count drifted across attempts: {counts}"


# --------------------------------------------------------------------------
# TEST 2 - the transaction-wide rollback count
# --------------------------------------------------------------------------


class TestTheRedundantRollbackIsPinnedByAnAbsoluteCeiling:
    """Never ``new < old``: an absolute ceiling, and only the transaction-wide bucket."""

    async def test_a_duplicate_logout_emits_at_most_the_ceiling_number_of_transaction_wide_rollbacks(
        self,
        db_session: AsyncSession,
        tenant_id: Any,
        payload_factory: Any,
        patched_seams: dict[str, AsyncMock],
    ) -> None:
        """Green on HEAD with no source change; the fix drives the count to zero.

        The ceiling is the observed HEAD value, not a target. Asserting ``< 2``
        would pass on a slow machine, on a different dialect, and on any change
        that has nothing to do with the defect.
        """
        del patched_seams
        token = "s2-oracle-rollback-token"
        payload = payload_factory("s2-oracle-rollback-jti", tenant_id)

        await _logout_n_times(db_session, token, payload, 1)
        await db_session.commit()

        with _statement_ledger(db_session) as ledger:
            await _logout_n_times(db_session, token, payload, 1)
            await db_session.commit()

        assert ledger.saw_any_statement(), (
            "the ledger observed nothing at all, so the transaction-wide count below would be an absence "
            f"rather than a measurement and would pass. Statements: {ledger.statements}, "
            f"connection rollbacks: {ledger.connection_rollbacks}"
        )
        assert ledger.transaction_wide <= TRANSACTION_WIDE_ROLLBACK_CEILING, (
            f"{ledger.transaction_wide} transaction-wide ROLLBACK statements for one duplicate logout, "
            f"ceiling is {TRANSACTION_WIDE_ROLLBACK_CEILING}. Statements: {ledger.statements}. "
            f"Connection rollbacks: {ledger.connection_rollbacks}. "
            "The fix (delete the redundant rollback at jwt_service.py:578) should drive this to 0; "
            "when it does, lower the ceiling in the SAME commit rather than leaving it loose."
        )

    async def test_the_savepoint_statements_stay_bounded(
        self,
        db_session: AsyncSession,
        tenant_id: Any,
        payload_factory: Any,
        patched_seams: dict[str, AsyncMock],
    ) -> None:
        """Guard the OPPOSITE failure: a transaction-wide rollback replaced by a loop.

        Lowering the transaction-wide count by issuing dozens of savepoint rolls
        would satisfy the ceiling above while making the transaction worse.

        The ceiling is set to ZERO and asserted as equality, which is what the
        measurement supports. A duplicate logout on this dialect/driver emits NO
        savepoint statements at all - not the SAVEPOINT, not the RELEASE, not
        the ROLLBACK TO - while an explicit ``begin_nested()`` on the same
        session immediately afterwards emits all three. So the savepoint
        statements the block at jwt_service.py:573 appears to bracket are not
        observable here, and the honest pin is that the count is exactly zero:
        if a future change starts routing this rollback through savepoints, that
        is a behaviour change and this test should say so rather than absorb it.
        An earlier version of this test used ``<= 12`` against an empty bucket,
        which passed for the wrong reason; the negative test is what found it.
        """
        del patched_seams
        token = "s2-oracle-savepoint-token"
        payload = payload_factory("s2-oracle-savepoint-jti", tenant_id)

        await _logout_n_times(db_session, token, payload, 1)
        await db_session.commit()

        with _statement_ledger(db_session) as ledger:
            await _logout_n_times(db_session, token, payload, 1)
            await db_session.commit()

        assert ledger.saw_any_statement(), (
            f"the ledger observed nothing, so the savepoint count would be an absence. {ledger.statements}"
        )
        assert ledger.savepoint == SAVEPOINT_STATEMENT_CEILING, (
            f"{ledger.savepoint} savepoint statements for one duplicate logout, expected exactly "
            f"{SAVEPOINT_STATEMENT_CEILING}. Statements: {ledger.statements}. "
            "A savepoint is the RIGHT scope for unwinding one flush; a loop of them is not a fix, and a "
            "sudden non-zero here means the rollback moved scope."
        )

    async def test_the_duplicate_logout_really_does_reach_the_rollback_block(
        self,
        db_session: AsyncSession,
        tenant_id: Any,
        payload_factory: Any,
        patched_seams: dict[str, AsyncMock],
    ) -> None:
        """Prove the ledgers above are observing a real duplicate, not a clean insert.

        This is the guard that would have caught the original vacuous class
        immediately. A duplicate logout must (a) raise the unique-constraint
        IntegrityError the code catches at jwt_service.py:575 and (b) emit a
        transaction-wide rollback on the ``rollback`` ConnectionEvent. If either
        stops happening, the ceiling tests would quietly keep passing while
        measuring nothing, so both are asserted rather than assumed.
        """
        del patched_seams
        token = "s2-oracle-reaches-rollback-token"
        payload = payload_factory("s2-oracle-reaches-rollback-jti", tenant_id)

        await _logout_n_times(db_session, token, payload, 1)
        await db_session.commit()

        with capture_logs() as events:
            with _statement_ledger(db_session) as ledger:
                await _logout_n_times(db_session, token, payload, 1)
                await db_session.commit()

        integrity_error_reported = any(
            event.get("event") == "Token already blacklisted in DB" or "IntegrityError" in str(event.get("event", ""))
            for event in events
        )
        assert integrity_error_reported, (
            "the duplicate logout did not report the IntegrityError, so jwt_service.py:571-578 was never "
            f"reached and the rollback counts below describe a clean insert instead. Events: {events}"
        )
        assert ledger.connection_rollbacks, (
            "no transaction-wide rollback was observed on the rollback ConnectionEvent, so the redundant "
            f"rollback at jwt_service.py:578 did not run. Statements: {ledger.statements}"
        )
        assert any("INSERT" in statement.upper() for statement in ledger.statements), (
            f"the ledger saw no INSERT at all, which means the listener is not observing this session. "
            f"Statements: {ledger.statements}"
        )

    @pytest.mark.parametrize(
        ("statement", "expected"),
        [
            ("ROLLBACK", "transaction_wide"),
            ("rollback", "transaction_wide"),
            ("ROLLBACK   TO   SAVEPOINT sa_1", "savepoint"),
            ("ROLLBACK TO SAVEPOINT sa_1", "savepoint"),
            ("SAVEPOINT sa_1", "savepoint"),
            ("RELEASE SAVEPOINT sa_1", "savepoint"),
            ("SELECT 1", "other"),
            ("BEGIN", "other"),
            ("COMMIT", "other"),
        ],
    )
    def test_the_classifier_separates_savepoint_scope_from_transaction_scope(
        self,
        statement: str,
        expected: str,
    ) -> None:
        """The classification the ceiling depends on, pinned case by case.

        Case and whitespace vary by dialect and by driver, and a classifier that
        only handles the exact string this session happened to emit would gate
        nothing on any other one.
        """
        assert _classify(statement) == expected

    def test_the_ceilings_are_absolute_and_not_derived_from_a_measurement(self) -> None:
        """A ceiling must be a constant, never computed from a run.

        This is the assertion that keeps the test honest against future edits:
        if someone rewrites the ceiling as ``observed / 2`` or ``observed * 0.9``
        the whole gate becomes a tautology.
        """
        assert isinstance(TRANSACTION_WIDE_ROLLBACK_CEILING, int)
        assert isinstance(SAVEPOINT_STATEMENT_CEILING, int)
        assert TRANSACTION_WIDE_ROLLBACK_CEILING >= 0
        assert SAVEPOINT_STATEMENT_CEILING >= 0


# --------------------------------------------------------------------------
# TEST 3 - the no_resolved_identity detector, as a deliberate pair
# --------------------------------------------------------------------------


def _reasons(events: list[MutableMapping[str, Any]], event_name: str = NO_RESOLVED_IDENTITY_EVENT) -> list[str]:
    """The ``reason`` of every captured occurrence of ``event_name``.

    Scoped to the ONE event rather than scanning every captured event: the
    assertion is about this line, and a capture of everything would let an
    unrelated ``reason=...`` satisfy it.
    """
    reasons: list[str] = []
    for entry in events:
        if entry.get("event") != event_name:
            continue
        reason = entry.get("reason")
        reasons.append(reason if isinstance(reason, str) else repr(reason))
    return reasons


@contextmanager
def _logout_with_no_resolved_identity() -> Iterator[list[MutableMapping[str, Any]]]:
    """Patch ``logout``'s seams and capture its structlog events.

    Three things must be true for ``_revoke_session`` to be reached with
    ``user_data is None``, and getting any of them wrong makes the detector
    test pass for the wrong reason:

    - the caller must pass a real ``db``, or ``logout`` refuses at
      ``service.py:610`` before it reaches ``_revoke_session`` at all;
    - ``verify_token`` must return ``None``, which is what it returns for an
      already-blacklisted token;
    - ``blacklist_tokens`` must be patched ON THE SERVICE MODULE, because
      ``service.py`` imports the name directly rather than reaching through
      ``jwt_service``. Patching ``jwt_service_mod`` would leave the real
      function in place, and with an undecodable token it would raise
      ``JWTError`` and ``logout`` would refuse at ``:632`` — again never
      reaching the detector.

    The first draft of this test omitted all three and was swallowed by a
    blanket ``pytest.raises``, so it asserted against a log line that had never
    been reached.

    CAPTION IS AT THE STRUCTLOG LEVEL, NOT THE STDLIB ONE. ``service.py`` uses
    ``logger.info("event_name", reason=...)`` — structlog, where the first
    positional argument is the EVENT and the rest are context. Whether that
    becomes a stdlib ``LogRecord`` at all depends on the processor chain the
    session happens to have configured, and the first draft installed a
    stdlib handler and saw nothing, because no ``LogRecord`` was ever created.
    ``structlog.testing.capture_logs`` swaps the processor chain instead, so it
    observes the event regardless of how logging is configured downstream.
    """
    with (
        patch.object(auth_service_mod, "verify_token", AsyncMock(return_value=None)),
        patch.object(auth_service_mod, "blacklist_tokens", AsyncMock()),
        patch.object(auth_service_mod, "audit", AsyncMock()),
        capture_logs() as events,
    ):
        yield events


class TestTheNoResolvedIdentityDetectorIsPinnedOnBothSides:
    """Two tests. Neither is sufficient alone; that is why both exist."""

    @pytest.mark.asyncio
    async def test_the_detector_sees_the_signal_on_head(self, db_session: AsyncSession) -> None:
        """The line FIRES when identity resolution fails — asserted, not assumed.

        Without this test the companion below could pass for the wrong reason: a
        detector that never fires at all would make "it does not fire" trivially
        true, and the oracle would certify a bug it never observed.
        """
        service = AuthService()
        with _logout_with_no_resolved_identity() as events:
            result = await service.logout(access_token="s2-oracle-unresolvable", refresh_token=None, db=db_session)

        assert result["message"], "logout did not complete, so no code after the token check ran"
        assert _reasons(events), (
            "the detector never fired, so the companion test asserting it does NOT fire "
            "would pass vacuously. If the log line moved, update this oracle rather than "
            "deleting it."
        )
        assert "no_resolved_identity" in _reasons(events)

    @pytest.mark.asyncio
    async def test_duplicate_logout_does_not_log_no_resolved_identity(self, db_session: AsyncSession) -> None:
        """RED ON HEAD BY DESIGN. This is the assertion S-2 has to turn green.

        On HEAD a duplicate logout resolves no identity — the token is already
        blacklisted — so the line fires. When ``jwt_service.py:578`` is deleted
        and the duplicate stops unwinding the outer transaction, the identity
        survives and this line stops firing.

        Leaving it red is the point. A suite that is green before and after the
        diff cannot tell you the diff did anything.
        """
        service = AuthService()
        with _logout_with_no_resolved_identity() as events:
            await service.logout(access_token="s2-oracle-unresolvable", refresh_token=None, db=db_session)

        assert "no_resolved_identity" not in _reasons(events), (
            f"a logout that resolved no identity logged {_reasons(events)}. "
            "On HEAD this is expected - it is the defect S-2 fixes. It flips green when the "
            "redundant rollback at jwt_service.py:578 is deleted."
        )


# --------------------------------------------------------------------------
# TEST 4 - the dialect deferral is a recorded task, not an omission
# --------------------------------------------------------------------------


def test_the_postgres_leg_is_named_rather_than_silently_absent() -> None:
    """The deferral must point at the hook that makes it buildable.

    A dialect parametrised with one value is indistinguishable from a suite that
    only ever ran on one dialect — unless the missing leg is named.
    """
    assert "sqlite" in DIALECTS, "the dialect parametrisation must keep the leg that actually runs"
    assert "postgres" not in DIALECTS, (
        "if the postgres leg now runs, delete this assertion AND the NOT DONE note in the "
        "module docstring, so the deferral cannot outlive the work."
    )
    assert "real_postgres" in POSTGRES_FOLLOW_UP_HOOK, "name the conftest fixture the follow-up bead should use"
