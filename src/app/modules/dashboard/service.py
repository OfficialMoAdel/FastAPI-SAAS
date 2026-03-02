"""
Dashboard Service

Business logic for generating dashboard metrics and analytics.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.audit_logs.models import AuditLog
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

    def _get_date_range(self, time_range: TimeRange) -> tuple[datetime, datetime]:
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
            start_date = datetime(2020, 1, 1, tzinfo=UTC)  # Beginning of time for this app

        return start_date, end_date

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

        # NOTE: Using deleted_at.is_(None) to check for non-deleted users
        # This is inconsistent with most other queries that use is_deleted=False
        # Once the User model is refactored to use only deleted_at (see TODO in
        # src/app/modules/users/models.py), all queries should be updated to
        # use the same pattern for checking soft-deleted records.

        # 1. Total users
        total_sub = (
            select(func.count(User.id))
            .where(
                User.tenant_id == tenant_id,
                User.deleted_at.is_(None),
            )
            .scalar_subquery()
        )

        # 2. New users
        new_sub = (
            select(func.count(User.id))
            .where(
                User.tenant_id == tenant_id,
                User.created_at >= start_date,
                User.created_at <= end_date,
                User.deleted_at.is_(None),
            )
            .scalar_subquery()
        )

        # 3. Active users
        active_sub = (
            select(func.count(func.distinct(AuditLog.user_id)))
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.created_at >= start_date,
                AuditLog.created_at <= end_date,
                AuditLog.action == "login",
            )
            .scalar_subquery()
        )

        # 4. Deleted users
        deleted_sub = (
            select(func.count(User.id))
            .where(
                User.tenant_id == tenant_id,
                User.deleted_at >= start_date,
                User.deleted_at <= end_date,
            )
            .scalar_subquery()
        )

        # Execute combined query
        query = select(total_sub, new_sub, active_sub, deleted_sub)
        result = await db.execute(query)
        row = result.one()

        return UserMetrics(
            total_users=row[0] or 0,
            new_users=row[1] or 0,
            active_users=row[2] or 0,
            deleted_users=row[3] or 0,
        )

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
        # Placeholder values - in production, query billing tables
        return RevenueMetrics(
            total_revenue=0.0,
            recurring_revenue=0.0,
            average_revenue_per_user=0.0,
            total_subscriptions=0,
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

        # 1. Total logins
        logins_sub = (
            select(func.count(AuditLog.id))
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.created_at >= start_date,
                AuditLog.created_at <= end_date,
                AuditLog.action == "login",
            )
            .scalar_subquery()
        )

        # 2. API calls
        api_calls_sub = (
            select(func.count(AuditLog.id))
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.created_at >= start_date,
                AuditLog.created_at <= end_date,
            )
            .scalar_subquery()
        )

        # 3. Failed auth
        failed_auth_sub = (
            select(func.count(AuditLog.id))
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.created_at >= start_date,
                AuditLog.created_at <= end_date,
                AuditLog.action.in_(["login_failed", "authentication_failed"]),
            )
            .scalar_subquery()
        )

        # Execute combined query
        query = select(logins_sub, api_calls_sub, failed_auth_sub)
        result = await db.execute(query)
        row = result.one()

        # Webhook deliveries - placeholder
        webhook_deliveries = 0

        return ActivityMetrics(
            total_logins=row[0] or 0,
            api_calls=row[1] or 0,
            webhook_deliveries=webhook_deliveries,
            failed_authentications=row[2] or 0,
        )

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
        start_date, end_date = self._get_date_range(time_range)

        # Fetch all metrics concurrently
        user_metrics = await self._get_user_metrics(db, start_date, end_date, tenant_id)
        revenue_metrics = await self._get_revenue_metrics(db, start_date, end_date, tenant_id)
        activity_metrics = await self._get_activity_metrics(db, start_date, end_date, tenant_id)

        return DashboardMetricsResponse(
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
            users=user_metrics,
            revenue=revenue_metrics,
            activity=activity_metrics,
        )


# Singleton instance
dashboard_service = DashboardService()
