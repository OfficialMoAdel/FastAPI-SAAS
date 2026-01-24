"""
Dashboard Module

Provides dashboard metrics and analytics endpoints for the frontend.
Returns aggregated statistics about users, revenue, and activity.
"""

from src.app.modules.dashboard.router import router

__all__ = ["router"]
