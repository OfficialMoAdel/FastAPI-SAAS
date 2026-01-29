"""Admin API endpoints package.

Provides administrative interfaces for system management and monitoring.
"""

from src.app.api.v1.admin.circuit_breakers import router as circuit_breakers_router

__all__ = ["circuit_breakers_router"]
