"""Audit logging service for tracking system actions."""

import asyncio
from collections.abc import Callable
from datetime import datetime
from functools import wraps
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from ...core.events import EventPriority, event_bus
from .models import AuditLog

logger = get_logger(__name__)


class AuditLogService:
    """Service for creating and querying audit logs."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize audit log service.

        Args:
            db: Database session
        """
        self.db = db

    async def create_log(
        self,
        tenant_id: int,
        action: str,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        user_id: int | None = None,
        details: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog:
        """Create an audit log entry.

        Args:
            tenant_id: Tenant ID (integer FK)
            action: Action performed (e.g., "user.created", "post.updated")
            entity_type: Type of entity affected (e.g., "user", "post")
            entity_id: ID of the affected entity
            user_id: ID of user who performed the action (integer FK)
            details: Additional context as JSON
            ip_address: IP address of the request
            user_agent: User agent string

        Returns
        -------
            Created audit log entry
        """
        log_entry = AuditLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        self.db.add(log_entry)
        await self.db.commit()
        await self.db.refresh(log_entry)

        logger.info(
            "audit_log_created",
            audit_id=str(log_entry.id),
            tenant_id=str(tenant_id),
            action=action,
            entity_type=entity_type,
        )

        return log_entry

    async def get_logs(
        self,
        tenant_id: UUID,
        user_id: UUID | None = None,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        """Query audit logs with filters.

        Args:
            tenant_id: Tenant ID (required for multi-tenancy)
            user_id: Filter by user ID
            action: Filter by action name
            entity_type: Filter by entity type
            entity_id: Filter by entity ID
            start_date: Filter logs after this date
            end_date: Filter logs before this date
            limit: Maximum number of results
            offset: Number of results to skip

        Returns
        -------
            List of audit log entries
        """
        query = select(AuditLog).where(AuditLog.tenant_id == tenant_id)

        if user_id:
            query = query.where(AuditLog.user_id == user_id)
        if action:
            query = query.where(AuditLog.action == action)
        if entity_type:
            query = query.where(AuditLog.entity_type == entity_type)
        if entity_id:
            query = query.where(AuditLog.entity_id == entity_id)
        if start_date:
            query = query.where(AuditLog.created_at >= start_date)
        if end_date:
            query = query.where(AuditLog.created_at <= end_date)

        query = query.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())


# Event handler for audit logs
@event_bus.subscribe("audit.log")
async def handle_audit_log_event(data: dict[str, Any]) -> None:
    """Handle audit log events from the event bus.

    Args:
        data: Event payload containing audit log details
    """
    from ...core.db.database import async_get_db

    async for db in async_get_db():
        try:
            service = AuditLogService(db)
            await service.create_log(
                tenant_id=int(data["tenant_id"]),
                action=data["action"],
                entity_type=data.get("entity_type"),
                entity_id=UUID(data["entity_id"]) if data.get("entity_id") else None,
                user_id=int(data["user_id"]) if data.get("user_id") else None,
                details=data.get("details", {}),
                ip_address=data.get("ip_address"),
                user_agent=data.get("user_agent"),
            )
        except Exception as e:
            logger.error("audit_log_event_failed", error=str(e), exc_info=True)
        finally:
            await db.close()
            break


def audit_action(action: str, entity_type: str) -> Callable:
    """Decorator to automatically audit actions.

    Args:
        action: Action name (e.g., "post.created")
        entity_type: Entity type (e.g., "post")

    Returns
    -------
        Decorator function

    Example:
        @audit_action("post.created", "post")
        async def create_post(db: AsyncSession, tenant_id: UUID, data: PostCreate):
            post = Post(**data.dict(), tenant_id=tenant_id)
            db.add(post)
            await db.commit()
            return post
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Execute original function
            result = await func(*args, **kwargs)

            # Extract context from result and kwargs
            tenant_id = kwargs.get("tenant_id") or getattr(result, "tenant_id", None)
            user_id = kwargs.get("current_user_id") or kwargs.get("user_id")
            entity_id = getattr(result, "id", None)

            # Publish audit event with error handling
            if tenant_id:

                async def _publish_audit_event() -> None:
                    """Publish audit event with error handling."""
                    try:
                        await event_bus.publish(
                            "audit.log",
                            {
                                "action": action,
                                "entity_type": entity_type,
                                "entity_id": str(entity_id) if entity_id else None,
                                "tenant_id": str(tenant_id),
                                "user_id": str(user_id) if user_id else None,
                                "details": {"result_id": str(getattr(result, "id", ""))},
                            },
                            priority=EventPriority.NORMAL,
                        )
                    except Exception as e:
                        logger.error("audit_publish_failed", error=str(e), action=action)

                asyncio.create_task(_publish_audit_event())

            return result

        return wrapper

    return decorator
