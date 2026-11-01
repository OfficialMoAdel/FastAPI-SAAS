"""
Dashboard Service

Business logic for generating dashboard metrics and analytics.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from src.app.core.cache.client import cache_client
from src.app.core.cache.read_through import ReadThroughPolicy
from src.app.core.db.scoped import criterion
from src.app.core.logger import get_logger
from src.app.core.resilience.db_circuit_breaker import db_circuit_guard
from src.app.modules.audit_logs.models import AuditLog
from src.app.modules.audit_logs.trail import LOGIN_AUDIT_ACTIONS, login_activity_subquery
from src.app.modules.billing.models import BillingRecord, SubscriptionPlan, UserSubscription
from src.app.modules.dashboard.schemas import (
    ActivityMetrics,
    DashboardMetricsResponse,
    RevenueMetrics,
    TimeRange,
    UserMetrics,
)
from src.app.modules.users.models import User

logger = get_logger(__name__)


class DashboardService:
    """Service for generating dashboard metrics."""

    # P1-11: dashboard aggregates are expensive (4 heavy aggregate queries
    # per load). Cache per (tenant, range) for 60s; cache failures fail
    # open (miss → recompute) via the adapter's Redis-down behavior.
    METRICS_CACHE_TTL = 60

    # P2-4: the single-flight refresh lock. Short by design — it covers one
    # aggregate pass, not the caller's patience; a waiter that outlives the
    # wait budget recomputes rather than stalling the dashboard.
    _METRICS_REFRESH_LOCK_TTL = 15
    _METRICS_REFRESH_WAIT_SECONDS = 5.0

    async def _get_date_range(
        self, db: AsyncSession, time_range: TimeRange, tenant_id: str
    ) -> tuple[datetime, datetime]:
        """Calculate start and end dates for a time range.

        Args:
            time_range: Time range enum value

        Returns:
            Tuple of (start_date, end_date)
        """
        end_date = datetime.now(UTC)

        if time_range == TimeRange.TODAY:
            start_date = end_date.replace(hour=0, minute=0, second=0, microsecond=0)
        elif time_range == TimeRange.WEEK:
            start_date = end_date - timedelta(days=7)
        elif time_range == TimeRange.MONTH:
            start_date = end_date - timedelta(days=30)
        elif time_range == TimeRange.QUARTER:
            start_date = end_date - timedelta(days=90)
        elif time_range == TimeRange.YEAR:
            start_date = end_date - timedelta(days=365)
        else:  # ALL
            # Query for actual minimum date in database for this tenant
            query = select(func.min(User.created_at)).where(criterion(User, tenant_id))
            async with db_circuit_guard():
                result = await db.execute(query)
                min_date = result.scalar()
            start_date = min_date if min_date else end_date - timedelta(days=365)

        return start_date, end_date

    @staticmethod
    def _min_created_subquery(tenant_id: str):
        """Scalar subquery for the tenant's earliest user (ALL range start)."""
        return (
            select(func.min(User.created_at))
            .where(criterion(User, tenant_id))
            .scalar_subquery()
        )

    @staticmethod
    def _audit_grouped_subquery(
        start: datetime | ColumnElement[datetime],
        end_date: datetime,
        tenant_id: str,
    ):
        """ONE scan of ``audit_logs`` producing all five activity aggregates.

        The dashboard's five audit_logs aggregates (logins, api_calls,
        failed_auth, webhook_deliveries, active_users) all read the SAME row
        set: ``tenant_id = X AND created_at BETWEEN start AND end``. They used
        to be five separate scalar subqueries, so that one window was scanned
        five times — four of them probing ``idx_audit_tenant_action`` and then
        fetching the heap to read ``details``/``user_id``.

        Here they are five conditional aggregates over a single pass, so the
        window's rows are read once. The access path is
        ``idx_audit_logs_tenant_created_id (tenant_id, created_at, id)``: its
        leading two columns are exactly this predicate, so the planner
        range-scans the window instead of probing the action index repeatedly.

        Each aggregate's predicate is byte-for-byte the one the scalar
        subquery it replaces used, so the numbers are unchanged. The column
        ORDER is the mappers' contract: ``_row_to_activity_metrics`` reads
        ``[logins, api_calls, failed_auth, webhook_deliveries]`` and
        ``_row_to_user_metrics`` reads ``active_users`` from this same
        subquery.

        Returns a subquery whose columns are read as ``audit.c.<name>``. It is
        an aggregate with no GROUP BY, so it always yields exactly one row —
        including when the window is empty (every count is 0), which is what
        keeps the merged statement's ``result.one()`` safe.

        This is the ONLY activity spelling in the module. The per-group
        ``_activity_subqueries`` builder that predated it was deleted: it had
        no production caller (its single reference was a test) and it restated
        these same four predicates in a form nothing executed, so an edit there
        would have changed no number while reading as live code. The login
        vocabulary and the ``details->>'success'`` extraction still live in
        one place — ``audit_logs.trail`` — and this method consumes them.
        """
        window = (
            criterion(AuditLog, tenant_id),
            AuditLog.created_at >= start,
            AuditLog.created_at <= end_date,
        )
        login_actions = AuditLog.action.in_(LOGIN_AUDIT_ACTIONS)
        # `details ->> 'success'` is text, and a row with no `success` key must
        # NOT count as a failure — the coalesce keeps the missing case from
        # silently flipping the count (same expression trail.py uses).
        failed_auth = and_(
            AuditLog.action == LOGIN_AUDIT_ACTIONS[0],
            func.coalesce(AuditLog.details["success"].astext, "true") == "false",
        )
        return (
            select(
                func.count(AuditLog.id).filter(login_actions).label("logins"),
                func.count(AuditLog.id).label("api_calls"),
                func.count(AuditLog.id).filter(failed_auth).label("failed_auth"),
                func.count(AuditLog.id).filter(AuditLog.action.like("webhook.%")).label("webhook_deliveries"),
                func.count(func.distinct(AuditLog.user_id)).filter(login_actions).label("active_users"),
            )
            .where(*window)
            .subquery()
        )

    @staticmethod
    def _user_subqueries(
        start: datetime | ColumnElement[datetime],
        end_date: datetime,
        tenant_id: str,
        audit=None,
    ) -> tuple:
        """Build the 4 user-aggregate scalar subqueries (no I/O).

        `start` is either a literal datetime or a SQL expression (the
        coalesced min-created subquery for TimeRange.ALL) — predicates are
        identical in both cases.

        `audit` is the grouped audit_logs subquery from
        :meth:`_audit_grouped_subquery`. When it is given, the active-users
        count is read from it instead of being built as its own scalar
        subquery — the merged statement then scans audit_logs once for ALL
        five activity aggregates rather than twice (once for active users,
        once for the four activity metrics). When it is omitted the column is
        built independently, which is what the single-group getters use.
        """
        # Soft-delete predicate: is_deleted.is_(False) — the User model's
        # canonical flag (deleted_users below intentionally ranges over
        # deleted_at, which is a time-window query, not a liveness check).

        # 1. Total users
        total_sub = (
            select(func.count(User.id))
            .where(
                criterion(User, tenant_id),
                User.is_deleted.is_(False),
            )
            .scalar_subquery()
        )

        # 2. New users
        new_sub = (
            select(func.count(User.id))
            .where(
                criterion(User, tenant_id),
                User.created_at >= start,
                User.created_at <= end_date,
                User.is_deleted.is_(False),
            )
            .scalar_subquery()
        )

        # 3. Active users — login-row contract owned by audit_logs.trail (I9).
        # When the caller already opened the grouped audit scan, read it from
        # there so audit_logs is scanned once, not twice.
        #
        # The ``audit is None`` branch is the PARITY ORACLE, not a production
        # path: ``_get_all_metrics`` always supplies the grouped subquery, so
        # this branch is reachable only from tests. It earns its place because
        # it is a second, independent spelling of the same predicate, and
        # ``test_p4_audit_grouped_scan`` compiles both and compares them — the
        # only way to catch a change to one side that silently changes the
        # number. Keeping it is deliberate; do not route production through it.
        active_sub = (
            audit.c.active_users
            if audit is not None
            else login_activity_subquery(tenant_id, start, end_date, metric="active_users")
        )

        # 4. Deleted users
        deleted_sub = (
            select(func.count(User.id))
            .where(
                criterion(User, tenant_id),
                User.deleted_at >= start,
                User.deleted_at <= end_date,
            )
            .scalar_subquery()
        )
        return (total_sub, new_sub, active_sub, deleted_sub)

    @staticmethod
    def _row_to_user_metrics(row, offset: int = 0) -> UserMetrics:
        return UserMetrics(
            total_users=row[offset] or 0,
            new_users=row[offset + 1] or 0,
            active_users=row[offset + 2] or 0,
            deleted_users=row[offset + 3] or 0,
        )

    @staticmethod
    def _revenue_subqueries(
        start: datetime | ColumnElement[datetime],
        end_date: datetime,
        tenant_id: str,
    ) -> tuple:
        """Build the 3 revenue-aggregate scalar subqueries (no I/O)."""
        # Total Revenue (sum of all successful billing amounts / 100 to convert cents)
        total_rev_sub = (
            select(func.sum(BillingRecord.amount_cents))
            .where(
                criterion(BillingRecord, tenant_id),
                BillingRecord.created_at >= start,
                BillingRecord.created_at <= end_date,
                BillingRecord.status == "succeeded",
            )
            .scalar_subquery()
        )
        # Recurring Revenue (Monthly * 12 + Yearly) approximated for MRR/ARR
        # For simplicity, we'll sum active subscriptions price if we had joining to plans
        # Since this is a dashboard simple value, we will query active subscriptions count
        subs_count_sub = (
            select(func.count(UserSubscription.id))
            .where(criterion(UserSubscription, tenant_id), UserSubscription.status == "active")
            .scalar_subquery()
        )

        # Calculate Monthly Recurring Revenue (MRR)
        mrr_sub = (
            select(
                func.sum(
                    case(
                        (UserSubscription.billing_interval == "monthly", SubscriptionPlan.price_monthly_cents / 100.0),
                        (UserSubscription.billing_interval == "yearly", SubscriptionPlan.price_yearly_cents / 1200.0),
                        else_=0.0,
                    )
                )
            )
            .select_from(UserSubscription)
            .join(SubscriptionPlan, UserSubscription.subscription_plan_id == SubscriptionPlan.id)
            .where(criterion(UserSubscription, tenant_id), UserSubscription.status == "active")
            .scalar_subquery()
        )
        return (total_rev_sub, subs_count_sub, mrr_sub)

    @staticmethod
    def _row_to_revenue_metrics(row, offset: int = 0, *, total_users: int) -> RevenueMetrics:
        """Map the three revenue columns to :class:`RevenueMetrics`.

        The two ratio fields divide the SAME numerator by DIFFERENT
        denominators, and each field is named for the denominator it uses.
        Before this, ``average_revenue_per_user`` divided by
        ``total_subscriptions`` — so it coincided with a per-user figure only
        for tenants where every user holds exactly one subscription.

        ``total_users`` comes from the user group at the head of the same
        statement (column 0), so taking the divisor from it costs no extra
        SQL, no extra column, and no change to the positional layout. It is
        keyword-only and REQUIRED: a default of ``0`` made a forgotten argument
        produce ``average_revenue_per_user = 0.0`` — a plausible-looking wrong
        number published with no error, which is worse than a TypeError at the
        call site. The only production caller always passes it.

        Window note: ``total_revenue`` is bounded by the requested range;
        ``total_users`` is the tenant's current non-deleted base and
        ``total_subscriptions`` is the current active snapshot. Neither
        denominator is range-scoped, so both ratios read as "this range's
        revenue over the current base" — which is the standard ARPU reading
        and is stated in the schema descriptions so it cannot be
        misread as a per-range-active-user figure.
        """
        total_cents = row[offset] or 0
        total_revenue = total_cents / 100.0
        total_subs = row[offset + 1] or 0
        mrr = float(row[offset + 2] or 0.0)
        avg_revenue_per_user = (total_revenue / total_users) if total_users > 0 else 0.0
        avg_revenue_per_subscriber = (total_revenue / total_subs) if total_subs > 0 else 0.0

        return RevenueMetrics(
            total_revenue=total_revenue,
            recurring_revenue=mrr,
            average_revenue_per_user=avg_revenue_per_user,
            average_revenue_per_subscriber=avg_revenue_per_subscriber,
            total_subscriptions=total_subs,
        )

    @staticmethod
    def _row_to_activity_metrics(row, offset: int = 0) -> ActivityMetrics:
        return ActivityMetrics(
            total_logins=row[offset] or 0,
            api_calls=row[offset + 1] or 0,
            webhook_deliveries=row[offset + 3] or 0,
            failed_authentications=row[offset + 2] or 0,
        )

    async def _get_all_metrics(
        self,
        db: AsyncSession,
        start: datetime | ColumnElement[datetime],
        end_date: datetime,
        tenant_id: str,
        include_min_created: bool = False,
    ) -> tuple[UserMetrics, RevenueMetrics, ActivityMetrics, datetime | None]:
        """Fetch all 11 aggregates in ONE statement (one snapshot, one RTT).

        Column layout: [4 user | 3 revenue | 4 activity] (+ [min_created]
        when `include_min_created` for TimeRange.ALL).

        The four activity columns and the active-users column all come from
        ONE grouped scan of audit_logs (:meth:`_audit_grouped_subquery`), so
        the (tenant, created_at) window is read once instead of five times.
        The grouped subquery is an aggregate with no GROUP BY, so it yields
        exactly one row and the cross join with the scalar subqueries stays
        one row — `result.one()` remains correct.
        """
        audit = self._audit_grouped_subquery(start, end_date, tenant_id)
        columns: list = [
            *self._user_subqueries(start, end_date, tenant_id, audit=audit),
            *self._revenue_subqueries(start, end_date, tenant_id),
            audit.c.logins,
            audit.c.api_calls,
            audit.c.failed_auth,
            audit.c.webhook_deliveries,
        ]
        if include_min_created:
            columns.append(self._min_created_subquery(tenant_id))
        async with db_circuit_guard():
            result = await db.execute(select(*columns))
            row = result.one()
        users = self._row_to_user_metrics(row, 0)
        revenue = self._row_to_revenue_metrics(row, 4, total_users=users.total_users)
        activity = self._row_to_activity_metrics(row, 7)
        min_created = row[11] if include_min_created else None
        return users, revenue, activity, min_created

    async def get_metrics(
        self, db: AsyncSession, tenant_id: str, time_range: TimeRange = TimeRange.MONTH
    ) -> DashboardMetricsResponse:
        """Get dashboard metrics for a time range.

        Args:
            db: Database session
            tenant_id: Tenant ID for isolation
            time_range: Time range for metrics (default: month)

        Returns:
            DashboardMetricsResponse with all metrics
        """
        cache_key = cache_client.tenant_key(f"dashboard:metrics:{time_range.value}", tenant_id)

        # P2-4: single-flight the miss through the ONE read-through seam. A cold
        # (or just-expired) key used to let every concurrent caller recompute the
        # same aggregates — the dashboard is DASHBOARD_READ-gated, so the herd is
        # the tenant's own viewers hitting the 60 s boundary together.
        #
        # This block used to spell the choreography out here (tri-state claim,
        # double-check, bounded poll, ownership-checked release). It is now the
        # shared implementation in ``core/cache/read_through.py``, which the
        # other caching call sites use too — so the give-up budget, the claim
        # TTL and the "no usable verdict means compute" rule are decided once.
        #
        # The two budgets stay SEPARATE and the seam enforces
        # ``give_up < claim TTL``: a waiter that outlives the claim it waits on
        # would compute alongside the next claimer.
        payload = await cache_client.read_through(
            cache_key,
            lambda: self._compute_metrics(db, tenant_id, time_range),
            ttl=self.METRICS_CACHE_TTL,
            policy=ReadThroughPolicy(
                give_up_seconds=self._METRICS_REFRESH_WAIT_SECONDS,
                lock_ttl_seconds=self._METRICS_REFRESH_LOCK_TTL,
            ),
        )
        return DashboardMetricsResponse.model_validate(payload)

    async def _compute_metrics(
        self,
        db: AsyncSession,
        tenant_id: str,
        time_range: TimeRange,
    ) -> dict[str, Any]:
        """Compute the aggregates and return the serialisable payload.

        Called only by :meth:`get_metrics` as the ``fetch`` half of the
        read-through seam, so it deliberately does NOT write to the cache: the
        seam owns publishing, which is what lets one place decide whether a
        computed value may be stored at all.
        """
        end_date = datetime.now(UTC)
        if time_range == TimeRange.ALL:
            # Fold the min-created lookup into the merged statement via
            # COALESCE (NULL min, i.e. no users, falls back to the same
            # end-365d start the old separate query produced).
            fallback = end_date - timedelta(days=365)
            start_expr = func.coalesce(self._min_created_subquery(tenant_id), fallback)
            user_metrics, revenue_metrics, activity_metrics, min_created = await self._get_all_metrics(
                db, start_expr, end_date, tenant_id, include_min_created=True
            )
            start_date = min_created if min_created else fallback
        else:
            start_date, end_date = await self._get_date_range(db, time_range, tenant_id)

            # Fetch all three metric groups in one statement
            # (AsyncSession does not support concurrent ops).
            user_metrics, revenue_metrics, activity_metrics, _ = await self._get_all_metrics(
                db, start_date, end_date, tenant_id
            )

        response = DashboardMetricsResponse(
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
            users=user_metrics,
            revenue=revenue_metrics,
            activity=activity_metrics,
        )
        # No set_json here: the read-through seam owns publishing, so a caller
        # that decided not to cache (``should_cache``) cannot have its value
        # stored behind its back.
        return response.model_dump(mode="json")


# Singleton instance
dashboard_service = DashboardService()
