"""
Dashboard Schemas

Pydantic models for dashboard metrics and analytics responses.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


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
    """Revenue-related metrics."""

    total_revenue: float = Field(description="Total revenue in time range")
    recurring_revenue: float = Field(description="Monthly recurring revenue (MRR)")
    average_revenue_per_user: float = Field(description="ARPU in time range")
    total_subscriptions: int = Field(description="Active subscriptions")


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
    generated_at: datetime = Field(default_factory=datetime.utcnow, description="When metrics were generated")

    class Config:
        json_schema_extra = {
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
                    "average_revenue_per_user": 50.56,
                    "total_subscriptions": 750,
                },
                "activity": {
                    "total_logins": 15420,
                    "api_calls": 125000,
                    "webhook_deliveries": 3200,
                    "failed_authentications": 45,
                },
                "generated_at": "2026-01-24T12:00:00Z",
            }
        }
