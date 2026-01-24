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

    async def _get_user_metrics(self, db: AsyncSession, start_date: datetime, end_date: datetime) -> UserMetrics:
        """Get user-related metrics.

        Args:
            db: Database session
            start_date: Start of time range
            end_date: End of time range

        Returns:
            UserMetrics with aggregated data
        """
        # Total users (all time)
        total_users_query = select(func.count(User.id)).where(User.deleted_at.is_(None))
        total_users_result = await db.execute(total_users_query)
        total_users = total_users_result.scalar() or 0

        # New users in time range
        new_users_query = select(func.count(User.id)).where(
            User.created_at >= start_date,
            User.created_at <= end_date,
            User.deleted_at.is_(None),
        )
        new_users_result = await db.execute(new_users_query)
        new_users = new_users_result.scalar() or 0

        # Active users (logged in during time range) - using audit logs
        active_users_query = select(func.count(func.distinct(AuditLog.user_id))).where(
            AuditLog.created_at >= start_date,
            AuditLog.created_at <= end_date,
            AuditLog.action == "login",
        )
        active_users_result = await db.execute(active_users_query)
        active_users = active_users_result.scalar() or 0

        # Deleted users in time range
        deleted_users_query = select(func.count(User.id)).where(
            User.deleted_at >= start_date,
            User.deleted_at <= end_date,
        )
        deleted_users_result = await db.execute(deleted_users_query)
        deleted_users = deleted_users_result.scalar() or 0

        return UserMetrics(
            total_users=total_users,
            active_users=active_users,
            new_users=new_users,
            deleted_users=deleted_users,
        )

    async def _get_revenue_metrics(self, db: AsyncSession, start_date: datetime, end_date: datetime) -> RevenueMetrics:
        """Get revenue-related metrics.

        Note: This is a placeholder implementation. In production, this would
        query the billing/subscriptions tables and integrate with Stripe.

        Args:
            db: Database session
            start_date: Start of time range
            end_date: End of time range

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
        self, db: AsyncSession, start_date: datetime, end_date: datetime
    ) -> ActivityMetrics:
        """Get activity-related metrics.

        Args:
            db: Database session
            start_date: Start of time range
            end_date: End of time range

        Returns:
            ActivityMetrics with aggregated data
        """
        # Total logins
        logins_query = select(func.count(AuditLog.id)).where(
            AuditLog.created_at >= start_date,
            AuditLog.created_at <= end_date,
            AuditLog.action == "login",
        )
        logins_result = await db.execute(logins_query)
        total_logins = logins_result.scalar() or 0

        # API calls (all audit log entries)
        api_calls_query = select(func.count(AuditLog.id)).where(
            AuditLog.created_at >= start_date,
            AuditLog.created_at <= end_date,
        )
        api_calls_result = await db.execute(api_calls_query)
        api_calls = api_calls_result.scalar() or 0

        # Webhook deliveries - placeholder
        webhook_deliveries = 0

        # Failed authentications
        failed_auth_query = select(func.count(AuditLog.id)).where(
            AuditLog.created_at >= start_date,
            AuditLog.created_at <= end_date,
            AuditLog.action.in_(["login_failed", "authentication_failed"]),
        )
        failed_auth_result = await db.execute(failed_auth_query)
        failed_authentications = failed_auth_result.scalar() or 0

        return ActivityMetrics(
            total_logins=total_logins,
            api_calls=api_calls,
            webhook_deliveries=webhook_deliveries,
            failed_authentications=failed_authentications,
        )

    async def get_metrics(self, db: AsyncSession, time_range: TimeRange = TimeRange.MONTH) -> DashboardMetricsResponse:
        """Get dashboard metrics for a time range.

        Args:
            db: Database session
            time_range: Time range for metrics (default: month)

        Returns:
            DashboardMetricsResponse with all metrics
        """
        start_date, end_date = self._get_date_range(time_range)

        # Fetch all metrics concurrently
        user_metrics = await self._get_user_metrics(db, start_date, end_date)
        revenue_metrics = await self._get_revenue_metrics(db, start_date, end_date)
        activity_metrics = await self._get_activity_metrics(db, start_date, end_date)

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
