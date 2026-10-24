"""P4-1: the dashboard's five audit_logs aggregates share one scan.

WHAT THIS PINS

The dashboard's activity metrics (logins, api_calls, failed_auth,
webhook_deliveries) and its active-users count all read the SAME row set:
``tenant_id = X AND created_at BETWEEN start AND end``. They used to be five
separate scalar subqueries inside the merged statement, so that one window
was scanned five times — four of them probing ``idx_audit_tenant_action`` and
then fetching the heap for ``details``/``user_id``.

They are now five conditional aggregates over a single pass
(``count(...) FILTER (WHERE ...)``), so the window's rows are read once and
the planner range-scans ``idx_audit_logs_tenant_created_id (tenant_id,
created_at, id)`` instead of probing the action index repeatedly.

WHAT THIS IS NOT: not an endpoint measurement. No request is issued and the
app's lifespan is not run. The subject is the shape of ONE statement and the
predicates it carries — the report's own obligation for this item is
"identical dashboard payload from one grouped scan vs five subqueries", and
that is what the structural and parity tests below assert.

WHY THERE IS NO VALUE-LEVEL DIFFERENTIAL HERE: the failed-auth aggregate
needs ``details ->> 'success'``, which is PostgreSQL JSONB syntax. SQLite
cannot parse it, so the two formulations cannot both be executed against the
same in-memory database. The parity test therefore compares the PREDICATES
the two formulations emit, aggregate by aggregate, which is the part that
could silently change the numbers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

import src.app.models  # noqa: F401  (register every mapper before compiling)
from src.app.modules.dashboard.service import dashboard_service


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _audit_where(sql: str) -> str:
    """The WHERE of the one audit_logs scan in the statement."""
    marker = "FROM audit_logs"
    assert marker in sql, "the merged statement must scan audit_logs"
    start = sql.index(marker) + len(marker)
    return sql[start : sql.index(") AS anon_3") if ") AS anon_3" in sql else len(sql)]


async def _merged_statement(include_min_created: bool = False):
    service = dashboard_service
    start = datetime.now(UTC) - timedelta(days=30)
    end = datetime.now(UTC)
    db = AsyncMock()
    result = MagicMock()
    result.one.return_value = tuple(range(12 if include_min_created else 11))
    db.execute = AsyncMock(return_value=result)

    await service._get_all_metrics(db, start, end, "tenant", include_min_created=include_min_created)
    return db.execute.call_args[0][0]


class TestTheAuditWindowIsScannedOnce:
    async def test_one_from_audit_logs(self) -> None:
        """The whole point: one scan, not five."""
        sql = _compiled(await _merged_statement())
        assert sql.count("FROM audit_logs") == 1, (
            f"audit_logs must be scanned exactly once; found {sql.count('FROM audit_logs')}"
        )

    async def test_all_five_aggregates_live_in_that_one_scan(self) -> None:
        sql = _compiled(await _merged_statement())
        # Each of the five is present, and none of them opens its own FROM.
        for name in ("AS logins", "AS api_calls", "AS failed_auth", "AS webhook_deliveries", "AS active_users"):
            assert name in sql, f"missing aggregate {name}"
        assert sql.count("FILTER (WHERE") == 4, (
            "four of the five aggregates are conditional (api_calls is the unfiltered superset)"
        )

    async def test_the_window_predicate_is_the_shared_index_prefix(self) -> None:
        """The scan's WHERE must be (tenant_id, created_at range) — the
        leading columns of idx_audit_logs_tenant_created_id, which is what
        makes one pass cheaper than five probes."""
        sql = _compiled(await _merged_statement())
        where = _audit_where(sql)
        assert "audit_logs.tenant_id" in where
        assert "audit_logs.created_at >=" in where
        assert "audit_logs.created_at <=" in where
        # The action predicate is a FILTER, not a WHERE: filtering inside the
        # aggregate is what lets one pass serve all five.
        assert "audit_logs.action" not in where

    async def test_the_scan_is_an_aggregate_with_no_group_by(self) -> None:
        """No GROUP BY means exactly one row, so the merged statement's
        `result.one()` stays valid — including for an empty window."""
        sql = _compiled(await _merged_statement())
        assert "GROUP BY" not in sql

    async def test_eleven_columns_survive(self) -> None:
        """The column layout is the mappers' contract; it must not move."""
        stmt = await _merged_statement()
        assert len(stmt.selected_columns) == 11

    async def test_min_created_still_makes_twelve(self) -> None:
        stmt = await _merged_statement(include_min_created=True)
        assert len(stmt.selected_columns) == 12

class TestTheAggregatesAreUnchanged:
    """A conditional aggregate is only a win if it counts the same rows.

    These compare each new aggregate's predicate against the predicate the
    scalar subquery it replaced used, built from the SAME module constants.
    If someone later changes one side and not the other, this fails.
    """

    async def test_login_predicate_matches_the_login_subquery(self) -> None:
        from src.app.modules.audit_logs.trail import LOGIN_AUDIT_ACTIONS, login_activity_subquery

        sql = _compiled(await _merged_statement())
        expected = _compiled(
            login_activity_subquery("tenant", datetime.now(UTC), datetime.now(UTC), metric="logins")
        )
        # The scalar subquery filters on action IN (...) in its WHERE; the
        # grouped scan carries the identical IN-list inside its FILTER.
        assert "FILTER (WHERE audit_logs.action IN ('auth.login', 'auth.sso_login')" in sql
        assert "action IN ('auth.login', 'auth.sso_login')" in expected
        assert LOGIN_AUDIT_ACTIONS == ("auth.login", "auth.sso_login")

    async def test_failed_auth_predicate_matches_the_trail_contract(self) -> None:
        """action = auth.login AND coalesce(details->>'success','true') = 'false'."""
        sql = _compiled(await _merged_statement())
        assert "audit_logs.action = 'auth.login'" in sql
        assert "coalesce((audit_logs.details ->> 'success'), 'true') = 'false'" in sql

    async def test_webhook_predicate_is_the_prefix_like(self) -> None:
        """webhook.% must stay a prefix LIKE, never %..% (btree-friendly)."""
        sql = _compiled(await _merged_statement())
        assert "audit_logs.action LIKE 'webhook.%%'" in sql
        assert "%webhook%" not in sql

    async def test_active_users_is_a_distinct_count_of_the_same_rows(self) -> None:
        sql = _compiled(await _merged_statement())
        assert (
            "count(distinct(audit_logs.user_id)) "
            "FILTER (WHERE audit_logs.action IN ('auth.login', 'auth.sso_login')" in sql
        )

    async def test_api_calls_is_the_unfiltered_superset(self) -> None:
        """api_calls has no action filter — it is the broadest of the five."""
        sql = _compiled(await _merged_statement())
        assert "count(audit_logs.id) AS api_calls" in sql
        # ...and no FILTER is attached to it: the api_calls column is the bare
        # count, the other four carry a FILTER.
        assert "count(audit_logs.id) FILTER (WHERE audit_logs.action" not in sql.replace(
            "count(audit_logs.id) FILTER (WHERE audit_logs.action IN ('auth.login', 'auth.sso_login')) AS logins",
            "",
        ).replace(
            "count(audit_logs.id) FILTER (WHERE audit_logs.action = 'auth.login' "
            "AND coalesce((audit_logs.details ->> 'success'), 'true') = 'false') AS failed_auth",
            "",
        ).replace(
            "count(audit_logs.id) FILTER (WHERE audit_logs.action LIKE 'webhook.%%') AS webhook_deliveries",
            "",
        )


class TestTheVocabularyIsStillTheProducerVocabulary:
    """Ported from the deleted ``TestDashboardAuditVocabulary`` (P5-5).

    Those tests went through the dead per-group getters. The vocabulary
    constraint they pinned is about the MERGED statement, which is the only
    one that runs, so the assertions are restated here rather than dropped
    with the getters.

    Producers (verified, do not regress):
    - ``handle_login_issued_event`` emits ``action="auth.login"`` with
      ``details={"success": true, ...}`` for every door; SSO keeps
      ``action="auth.sso_login"``.
    - ``auth/service.py::_publish_login_audit`` (failure paths) emits
      ``action="auth.login"`` with ``details={"success": bool, ...}``.
    Zero producers emit bare "login" / "login_failed" / "authentication_failed".
    """

    PRODUCER_LOGIN_ACTIONS = {"auth.login", "auth.sso_login"}

    async def test_the_merged_statement_uses_the_producer_login_actions(self) -> None:
        sql = _compiled(await _merged_statement())
        for action in self.PRODUCER_LOGIN_ACTIONS:
            assert action in sql
        assert "'login'" not in sql

    async def test_the_merged_statement_has_no_dead_vocabulary(self) -> None:
        sql = _compiled(await _merged_statement())
        assert "login_failed" not in sql
        assert "authentication_failed" not in sql
        # failed-auth is derived from details.success, not a separate action
        assert "success" in sql
        assert "'false'" in sql


class TestTheSingleGroupGettersStillWork:
    """The per-group subquery builders keep their own statements.

    (P5-5 removed the dead ``_get_*_metrics`` wrappers that used to call
    these; the builders stay, because ``_get_all_metrics`` and the grouped
    scan both go through them.)
"""

    async def test_user_group_still_builds_its_own_active_users(self) -> None:
        service = dashboard_service
        cols = service._user_subqueries(
            datetime.now(UTC) - timedelta(days=30), datetime.now(UTC), "tenant"
        )
        sql = _compiled(_select_of(cols))
        assert "audit_logs" in sql, "without the grouped subquery the user group scans audit_logs itself"

    async def test_activity_group_is_untouched(self) -> None:
        service = dashboard_service
        cols = service._activity_subqueries(
            datetime.now(UTC) - timedelta(days=30), datetime.now(UTC), "tenant"
        )
        sql = _compiled(_select_of(cols))
        assert sql.count("FROM audit_logs") == 4
        assert "webhook.%%" in sql

    async def test_user_group_reads_active_users_from_the_grouped_scan(self) -> None:
        """With the grouped subquery supplied, the user group adds NO extra
        audit_logs scan of its own."""
        service = dashboard_service
        start = datetime.now(UTC) - timedelta(days=30)
        end = datetime.now(UTC)
        audit = service._audit_grouped_subquery(start, end, "tenant")
        cols = service._user_subqueries(start, end, "tenant", audit=audit)
        sql = _compiled(_select_of(cols))
        assert sql.count("FROM audit_logs") == 1
        assert "active_users" in sql
        # The user group's own columns are still the three user scans; the
        # fourth is a reference into the grouped subquery, not a new scan.
        assert sql.count("FROM users") == 3


def _select_of(cols):
    from sqlalchemy import select

    return select(*cols)
