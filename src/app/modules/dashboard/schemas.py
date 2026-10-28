"""
Dashboard Schemas

Pydantic models for dashboard metrics and analytics responses.
"""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class TimeRange(str, Enum):
    """Time range filter for dashboard metrics."""

    TODAY = "today"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    ALL = "all"


class UserMetrics(BaseModel):
    """User-related metrics."""

    total_users: int = Field(description="Total number of users")
    active_users: int = Field(description="Active users in time range")
    new_users: int = Field(description="New users in time range")
    deleted_users: int = Field(description="Deleted users in time range")


class RevenueMetrics(BaseModel):
    """Revenue-related metrics.

    The two ratio fields share one numerator (``total_revenue``) and differ
    only in denominator, so each is named for the base it divides by. Both
    denominators are CURRENT snapshots (non-deleted users, active
    subscriptions) while the numerator is scoped to the requested range —
    that asymmetry is the standard ARPU reading and is stated here so the
    numbers cannot be misread as "per user active in this range".
    """

    total_revenue: float = Field(description="Total revenue in the requested time range")
    recurring_revenue: float = Field(description="Monthly recurring revenue (MRR) across current active subscriptions")
    average_revenue_per_user: float = Field(
        description=(
            "ARPU: range revenue ÷ the tenant's current non-deleted users "
            "(total_users). Zero when the tenant has no users."
        )
    )
    average_revenue_per_subscriber: float = Field(
        default=0.0,
        description=(
            "ARPS: range revenue ÷ current active subscriptions "
            "(total_subscriptions). Zero when there are none. This is the "
            "figure average_revenue_per_user used to publish."
        ),
    )
    total_subscriptions: int = Field(description="Current active subscriptions (not range-scoped)")


class ActivityMetrics(BaseModel):
    """Activity-related metrics."""

    total_logins: int = Field(description="Total logins in time range")
    api_calls: int = Field(description="Total API calls in time range")
    webhook_deliveries: int = Field(description="Webhook deliveries in time range")
    failed_authentications: int = Field(description="Failed auth attempts in time range")


class DashboardMetricsResponse(BaseModel):
    """Complete dashboard metrics response."""

    time_range: TimeRange = Field(description="Time range for metrics")
    start_date: datetime = Field(description="Start date of time range")
    end_date: datetime = Field(description="End date of time range")
    users: UserMetrics = Field(description="User metrics")
    revenue: RevenueMetrics = Field(description="Revenue metrics")
    activity: ActivityMetrics = Field(description="Activity metrics")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="When metrics were generated")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "time_range": "month",
                "start_date": "2026-01-01T00:00:00Z",
                "end_date": "2026-01-31T23:59:59Z",
                "users": {
                    "total_users": 1250,
                    "active_users": 890,
                    "new_users": 120,
                    "deleted_users": 5,
                },
                "revenue": {
                    "total_revenue": 45000.00,
                    "recurring_revenue": 38000.00,
                    "average_revenue_per_user": 36.00,
                    "average_revenue_per_subscriber": 60.00,
                    "total_subscriptions": 750,
                },
                "activity": {
                    "total_logins": 15420,
                    "api_calls": 125000,
                    "webhook_deliveries": 3200,
                    "failed_authentications": 45,
                },
                "generated_at": "2026-01-24T12:00:00Z",
            },
        },
    )
