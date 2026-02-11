"""
RLS (Row Level Security) helper module for context management.

Provides helper functions and context managers for setting/clearing RLS session
variables in PostgreSQL connections to enforce tenant isolation at the database level.
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.orm import Session

from src.app.core.logger import get_logger

from ..tenancy.context import get_current_tenant

logger = get_logger(__name__)

# RLS session variable names
RLS_TENANT_VAR = "app.current_tenant_id"
RLS_USER_VAR = "app.current_user_id"
RLS_BYPASS_VAR = "app.bypass_rls"

# Type variable for decorator
F = TypeVar("F", bound=Callable[..., Any])


def restore_rls_context(session: Session, transaction: Any, connection: Any) -> None:
    """Restores RLS context after a transaction begins.

    This is necessary because PostgreSQL `set_config` with `is_local=True`
    is transaction-scoped. When a commit occurs, the configuration is lost.
    This listener ensures it's re-applied for the next transaction in the same session.
    """
    # SQLite does not support set_config (used in tests)
    if connection.dialect.name != "postgresql":
        return

    tenant = get_current_tenant()
    if tenant:
        # We are in a synchronous event listener, so we use the sync connection directly.
        # This connection is the underlying driver connection or SQLAlchemy wrapper.
        connection.execute(
            text(f"SELECT set_config('{RLS_TENANT_VAR}', :tenant_id, true)"),
            {"tenant_id": str(tenant.id)},
        )


async def set_rls_context(
    connection: AsyncConnection,
    tenant_id: str,
    user_id: str | None = None,
) -> None:
    """
    Set RLS context variables in the database session.

    Args:
        connection: Database connection
        tenant_id: Tenant UUID to set as current tenant
        user_id: Optional user UUID to set as current user

    Raises:
        Exception: If setting context fails (fail-closed)
    """
    try:
        # Set tenant context (required)
        await connection.execute(
            text(f"SELECT set_config('{RLS_TENANT_VAR}', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        logger.debug("RLS context set", tenant_id=tenant_id)

        # Set user context (optional)
        if user_id:
            await connection.execute(
                text(f"SELECT set_config('{RLS_USER_VAR}', :user_id, true)"),
                {"user_id": user_id},
            )
            logger.debug("RLS user context set", user_id=user_id)

    except Exception as e:
        logger.error(
            "Failed to set RLS context",
            tenant_id=tenant_id,
            user_id=user_id,
            error=str(e),
        )
        raise


async def clear_rls_context(connection: AsyncConnection) -> None:
    """
    Clear RLS context variables from the database session.

    Args:
        connection: Database connection

    Note:
        Fails open - logs warnings but doesn't raise exceptions
    """
    try:
        # Reset all RLS variables
        await connection.execute(text(f"RESET {RLS_TENANT_VAR}"))
        await connection.execute(text(f"RESET {RLS_USER_VAR}"))
        await connection.execute(text(f"RESET {RLS_BYPASS_VAR}"))
        logger.debug("RLS context cleared")

    except Exception as e:
        # Fail open - log warning but don't block
        logger.warning("Failed to clear RLS context", error=str(e))


@asynccontextmanager
async def rls_context(
    session: AsyncSession,
    tenant_id: str,
    user_id: str | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager that sets RLS context for the duration of the block.

    Args:
        session: Database session
        tenant_id: Tenant UUID to set as current tenant
        user_id: Optional user UUID to set as current user

    Yields:
        The same session with RLS context set

    Example:
        async with rls_context(session, tenant_id="abc-123") as session:
            # All queries in this block are filtered by tenant
            posts = await session.execute(select(Post))
    """
    connection = await session.connection()

    try:
        # Set RLS context
        await set_rls_context(connection, tenant_id, user_id)
        yield session

    finally:
        # Always clear context, even on exception
        await clear_rls_context(connection)


@asynccontextmanager
async def bypass_rls(
    session: AsyncSession,
    reason: str,
    user_id: str | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager that bypasses RLS for admin operations.

    CRITICAL: All RLS bypasses are logged to audit trail.

    Args:
        session: Database session
        reason: Required reason for bypassing RLS (for audit)
        user_id: Optional user ID performing the bypass

    Yields:
        The same session with RLS bypass enabled

    Example:
        async with bypass_rls(session, reason="Admin data export", user_id="admin-123"):
            # Queries can access all tenant data
            all_posts = await session.execute(select(Post))
    """
    if not reason:
        raise ValueError("Reason is required for RLS bypass (audit trail)")

    connection = await session.connection()

    try:
        # Set bypass flag
        await connection.execute(
            text(f"SELECT set_config('{RLS_BYPASS_VAR}', 'true', true)"),
        )

        # Log bypass event (CRITICAL for security audit)
        logger.warning(
            "RLS bypass enabled",
            reason=reason,
            user_id=user_id,
            extra={"security_event": "rls_bypass"},
        )

        # TODO: Write to audit_logs table
        # This should be done in a separate transaction to ensure it's logged
        # even if the main operation fails

        yield session

    finally:
        # Clear bypass flag
        try:
            await connection.execute(text(f"RESET {RLS_BYPASS_VAR}"))
            logger.debug("RLS bypass cleared")
        except Exception as e:
            logger.warning("Failed to clear RLS bypass", error=str(e))


def with_tenant_context(tenant_id: str, user_id: str | None = None) -> Callable[[F], F]:
    """
    Decorator for async functions that need RLS context.

    Useful for ARQ background jobs and other async tasks.

    Args:
        tenant_id: Tenant UUID to set as current tenant
        user_id: Optional user UUID to set as current user

    Returns:
        Decorated function with RLS context

    Example:
        @with_tenant_context(tenant_id="abc-123")
        async def process_tenant_data(ctx):
            # RLS context is automatically set
            async with get_session() as session:
                posts = await session.execute(select(Post))
    """

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Import here to avoid circular dependency
            from src.app.core.db.database import async_get_db

            # Get session and set RLS context
            async for session in async_get_db():
                async with rls_context(session, tenant_id, user_id):
                    # Call the original function
                    return await func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator


async def get_current_tenant_id(connection: AsyncConnection) -> str | None:
    """
    Get the current tenant ID from the session context.

    Args:
        connection: Database connection

    Returns:
        Current tenant ID or None if not set
    """
    try:
        result = await connection.execute(text(f"SELECT current_setting('{RLS_TENANT_VAR}', true)"))
        tenant_id = result.scalar()
        return tenant_id if tenant_id else None
    except Exception as e:
        logger.warning("Failed to get current tenant ID", error=str(e))
        return None


async def verify_rls_enabled(connection: AsyncConnection, table_name: str) -> bool:
    """
    Verify that RLS is enabled on a table.

    Args:
        connection: Database connection
        table_name: Name of the table to check

    Returns:
        True if RLS is enabled, False otherwise
    """
    try:
        result = await connection.execute(
            text(
                """
                SELECT relrowsecurity
                FROM pg_class
                WHERE relname = :table_name
                """
            ),
            {"table_name": table_name},
        )
        row = result.fetchone()
        return bool(row[0]) if row else False
    except Exception as e:
        logger.error(
            "Failed to verify RLS status",
            table_name=table_name,
            error=str(e),
        )
        return False


async def list_rls_policies(connection: AsyncConnection, table_name: str) -> list[dict[str, Any]]:
    """
    List all RLS policies on a table.

    Args:
        connection: Database connection
        table_name: Name of the table

    Returns:
        List of policy dictionaries with name, command, and definition
    """
    try:
        result = await connection.execute(
            text(
                """
                SELECT policyname, cmd, qual, with_check
                FROM pg_policies
                WHERE tablename = :table_name
                """
            ),
            {"table_name": table_name},
        )
        policies = []
        for row in result:
            policies.append(
                {
                    "name": row[0],
                    "command": row[1],
                    "using": row[2],
                    "with_check": row[3],
                }
            )
        return policies
    except Exception as e:
        logger.error(
            "Failed to list RLS policies",
            table_name=table_name,
            error=str(e),
        )
        return []


__all__ = [
    "RLS_TENANT_VAR",
    "RLS_USER_VAR",
    "RLS_BYPASS_VAR",
    "set_rls_context",
    "clear_rls_context",
    "rls_context",
    "bypass_rls",
    "with_tenant_context",
    "get_current_tenant_id",
    "verify_rls_enabled",
    "list_rls_policies",
]
