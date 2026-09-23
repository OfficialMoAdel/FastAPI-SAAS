"""
Dashboard Service

Business logic for generating dashboard metrics and analytics.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from src.app.core.cache.client import cache_client
from src.app.core.db.scoped import criterion
from src.app.core.resilience.db_circuit_breaker import db_circuit_guard
from src.app.modules.audit_logs.models import AuditLog
from src.app.modules.audit_logs.trail import login_activity_subquery
from src.app.modules.billing.models import BillingRecord, SubscriptionPlan, UserSubscription
from src.app.modules.dashboard.schemas import (
    ActivityMetrics,
    DashboardMetricsResponse,
    RevenueMetrics,
    TimeRange,
    UserMetrics,
)
from src.app.modules.users.models import User


class DashboardService:
    """Service for generating dashboard metrics."""

    # P1-11: dashboard aggregates are expensive (4 heavy aggregate queries
    # per load). Cache per (tenant, range) for 60s; cache failures fail
    # open (miss → recompute) via the adapter's Redis-down behavior.
    METRICS_CACHE_TTL = 60

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
    def _user_subqueries(
        start: datetime | ColumnElement[datetime],
        end_date: datetime,
        tenant_id: str,
    ) -> tuple:
        """Build the 4 user-aggregate scalar subqueries (no I/O).

        `start` is either a literal datetime or a SQL expression (the
        coalesced min-created subquery for TimeRange.ALL) — predicates are
        identical in both cases.
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

        # 3. Active users — login-row contract owned by audit_logs.trail (I9)
        active_sub = login_activity_subquery(tenant_id, start, end_date, metric="active_users")

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

    async def _get_user_metrics(
        self, db: AsyncSession, start_date: datetime, end_date: datetime, tenant_id: str
    ) -> UserMetrics:
        """Get user-related metrics.

        Args:
            db: Database session
            start_date: Start of time range
            end_date: End of time range
            tenant_id: Tenant ID for isolation

        Returns:
            UserMetrics with aggregated data
        """
        # P2-3: Combine queries to reduce roundtrips (1 query instead of 4)
        # We use scalar_subquery() to select multiple counts in one go
        query = select(
            *self._user_subqueries(start_date, end_date, tenant_id)
        )
        async with db_circuit_guard():
            result = await db.execute(query)
            row = result.one()

        return self._row_to_user_metrics(row)

    async def _get_revenue_metrics(
        self, db: AsyncSession, start_date: datetime, end_date: datetime, tenant_id: str
    ) -> RevenueMetrics:
        """Get revenue-related metrics.

        Note: This is a placeholder implementation. In production, this would
        query the billing/subscriptions tables and integrate with Stripe.

        Args:
            db: Database session
            start_date: Start of time range
            end_date: End of time range
            tenant_id: Tenant ID for isolation

        Returns:
            RevenueMetrics with aggregated data
        """
        query = select(
            *self._revenue_subqueries(start_date, end_date, tenant_id)
        )
        async with db_circuit_guard():
            result = await db.execute(query)
            row = result.one()

        return self._row_to_revenue_metrics(row)

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

    async def _get_activity_metrics(
        self, db: AsyncSession, start_date: datetime, end_date: datetime, tenant_id: str
    ) -> ActivityMetrics:
        """Get activity-related metrics.

        Args:
            db: Database session
            start_date: Start of time range
            end_date: End of time range
            tenant_id: Tenant ID for isolation

        Returns:
            ActivityMetrics with aggregated data
        """
        # P2-3: Combine queries (1 query instead of 3)
        query = select(
            *self._activity_subqueries(start_date, end_date, tenant_id)
        )
        async with db_circuit_guard():
            result = await db.execute(query)
            row = result.one()

        return self._row_to_activity_metrics(row)

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
        """
        columns: list = [
            *self._user_subqueries(start, end_date, tenant_id),
            *self._revenue_subqueries(start, end_date, tenant_id),
            *self._activity_subqueries(start, end_date, tenant_id),
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
        await cache_client.set_json(cache_key, response.model_dump(mode="json"), ttl=self.METRICS_CACHE_TTL)
        return response


# Singleton instance
dashboard_service = DashboardService()
