"""
Dashboard Service

Business logic for generating dashboard metrics and analytics.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from redis.exceptions import RedisError
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from src.app.core.cache.client import cache_client
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
    _METRICS_REFRESH_POLL_SECONDS = 0.05

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
    def _row_to_revenue_metrics(row, offset: int = 0) -> RevenueMetrics:
        total_cents = row[offset] or 0
        total_revenue = total_cents / 100.0
        total_subs = row[offset + 1] or 0
        mrr = float(row[offset + 2] or 0.0)
        avg_revenue = (total_revenue / total_subs) if total_subs > 0 else 0.0

        return RevenueMetrics(
            total_revenue=total_revenue,
            recurring_revenue=mrr,
            average_revenue_per_user=avg_revenue,
            total_subscriptions=total_subs,
        )

    @staticmethod
    def _activity_subqueries(
        start: datetime | ColumnElement[datetime],
        end_date: datetime,
        tenant_id: str,
    ) -> tuple:
        """Build the 4 activity-aggregate scalar subqueries (no I/O)."""
        # 1. Total logins — login-row contract owned by audit_logs.trail (I9)
        logins_sub = login_activity_subquery(tenant_id, start, end_date, metric="logins")

        # 2. API calls
        api_calls_sub = (
            select(func.count(AuditLog.id))
            .where(
                criterion(AuditLog, tenant_id),
                AuditLog.created_at >= start,
                AuditLog.created_at <= end_date,
            )
            .scalar_subquery()
        )

        # 3. Failed auth — action/success extraction owned by audit_logs.trail (I9)
        failed_auth_sub = login_activity_subquery(tenant_id, start, end_date, metric="failed_auth")

        # Webhook deliveries - webhook audit actions live in the dotted
        # `webhook.*` namespace (e.g. webhook.ssrf_blocked). Prefix LIKE
        # keeps the match set identical to `%webhook%` while using the
        # idx_audit_tenant_action (tenant_id, action) btree range scan
        # instead of a sequential scan.
        webhook_sub = (
            select(func.count(AuditLog.id))
            .where(
                criterion(AuditLog, tenant_id),
                AuditLog.created_at >= start,
                AuditLog.created_at <= end_date,
                AuditLog.action.like("webhook.%"),
            )
            .scalar_subquery()
        )
        return (logins_sub, api_calls_sub, failed_auth_sub, webhook_sub)

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
        revenue = self._row_to_revenue_metrics(row, 4)
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
        cached = await cache_client.get_json(cache_key)
        if cached is not None:
            return DashboardMetricsResponse.model_validate(cached)

        # P2-4: single-flight the miss. A cold (or just-expired) key used to
        # let every concurrent caller recompute the same aggregates — the
        # dashboard is DASHBOARD_READ-gated, so the herd is the tenant's own
        # viewers hitting the 60s boundary together. The first caller claims
        # a short lock on the key and computes; the others wait on the key
        # (double-checked after the claim) and are served the stored payload.
        # The claim/release primitives are the adapter's own (`set_nx` +
        # `release_lock_if_owned`), so the get_json/set_json seam this module
        # documents is preserved — unlike `get_with_lock`, which would move
        # the read off that seam and change the stored payload type.
        # A caller that cannot claim the lock computes anyway once the
        # wait budget is spent: a duplicated query is cheaper than a stalled
        # dashboard.
        lock_key = f"{cache_key}:refresh"
        lock_token = uuid4().hex
        # P1-2: "cannot claim" must be distinguishable from "lock held".
        # `set_nx` returns False BOTH when the key is taken and when Redis is
        # unavailable, and it PROPAGATES a Redis error (the adapter's one
        # non-fail-open operation, so the billing webhook can fall back to its
        # DB idempotency check). Treating either as "a peer is computing"
        # meant a Redis outage or blip turned a dashboard load into a 5 s
        # stall or a 500, where the pre-P2-4 code simply recomputed. So the
        # claim is tri-state:
        #   True  -> we hold the lock, compute and release
        #   False -> a peer holds it, wait on the key
        #   None  -> no usable verdict (no client, or the claim errored),
        #            so fall straight through to computing.
        claim: bool | None = None
        if cache_client.client is not None:
            try:
                claim = await cache_client.set_nx(lock_key, lock_token, self._METRICS_REFRESH_LOCK_TTL)
            except (RedisError, OSError) as e:  # a failed claim degrades to a plain recompute
                # RedisError covers the redis-py transport failures; OSError
                # covers the connection-refused/reset class that surfaces as a
                # builtin ConnectionError when the client is replaced or the
                # socket goes away mid-call. Both mean "the claim could not be
                # made", which is the one answer this call site can act on.
                logger.warning("dashboard_refresh_claim_failed", error=str(e))
        if claim is True:
            try:
                cached = await cache_client.get_json(cache_key)
                if cached is None:
                    cached = await self._compute_metrics(db, tenant_id, time_range, cache_key)
            finally:
                await cache_client.release_lock_if_owned(lock_key, lock_token)
        elif claim is False:
            deadline = time.monotonic() + self._METRICS_REFRESH_WAIT_SECONDS
            while True:
                await asyncio.sleep(self._METRICS_REFRESH_POLL_SECONDS)
                cached = await cache_client.get_json(cache_key)
                if cached is not None:
                    break
                if time.monotonic() >= deadline:
                    cached = await self._compute_metrics(db, tenant_id, time_range, cache_key)
                    break
        else:
            cached = await self._compute_metrics(db, tenant_id, time_range, cache_key)

        return DashboardMetricsResponse.model_validate(cached)

    async def _compute_metrics(
        self,
        db: AsyncSession,
        tenant_id: str,
        time_range: TimeRange,
        cache_key: str,
    ) -> dict[str, Any]:
        """Compute the aggregates and store them; return the JSON payload.

        Split out of :meth:`get_metrics` so the single-flight lock wraps only
        the expensive part. Returns the serialisable payload, which the caller
        validates into the response model — the same shape
        ``set_json``/``get_json`` round-trip.
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
        payload = response.model_dump(mode="json")
        await cache_client.set_json(cache_key, payload, ttl=self.METRICS_CACHE_TTL)
        return payload


# Singleton instance
dashboard_service = DashboardService()
