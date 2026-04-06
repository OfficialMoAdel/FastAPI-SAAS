"""
RLS (Row Level Security) helper module for context management.

Provides helper functions and context managers for setting/clearing RLS session
variables in PostgreSQL connections to enforce tenant isolation at the database level.

SQLite Compatibility:
    All functions detect SQLite dialect and skip set_config calls gracefully.
    RLS is a PostgreSQL-only feature; SQLite tests run without RLS enforcement.
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from src.app.core.logger import get_logger
from src.app.core.tenancy.context import get_current_tenant

logger = get_logger(__name__)

# RLS session variable names
RLS_TENANT_VAR = "app.current_tenant_id"
RLS_USER_VAR = "app.current_user_id"
RLS_BYPASS_VAR = "app.bypass_rls"

import re

for _var in [RLS_TENANT_VAR, RLS_USER_VAR, RLS_BYPASS_VAR]:
    if not re.match(r"^[a-zA-Z0-9_.]+$", _var):
        raise ValueError(f"Invalid RLS variable name (potential SQLi vector): {_var}")

# Type variable for decorator
F = TypeVar("F", bound=Callable[..., Any])


def _is_sqlite(connection: Any) -> bool:
    """Check if the connection is using SQLite dialect."""
    try:
        return bool(connection.dialect.name == "sqlite")
    except Exception:
        return False


def restore_rls_context(dbapi_connection: Any, connection_record: Any, connection_proxy: Any) -> None:
    """Restores RLS context when a connection is acquired from the pool.

    This is necessary because PostgreSQL `set_config` with `is_local=True`
    is transaction-scoped. By hooking into the pool checkout event with an async callback,
    we can set a session-level config (`is_local=False`) that persists across all
    transactions on this connection checkout, until it is returned to the pool and checked out again.
    """
    tenant = get_current_tenant()
    tenant_id = str(tenant.id) if tenant else ""

    async def _do_restore(conn: Any) -> None:
        try:
            # We use is_local=false so the config persists for the entire connection checkout.
            # When the connection is returned to the pool and checked out by another request,
            # this event will fire again and overwrite it.
            if tenant_id:
                await conn.execute(f"SELECT set_config('{RLS_TENANT_VAR}', $1, false)", tenant_id)
            else:
                await conn.execute(f"SELECT set_config('{RLS_TENANT_VAR}', '', false)")
                await conn.execute(f"SELECT set_config('{RLS_USER_VAR}', '', false)")
                await conn.execute(f"SELECT set_config('{RLS_BYPASS_VAR}', '', false)")
        except Exception as e:
            # SECURITY FIX: Log heavily but do not raise.
            # SQLite does not support set_config and will fail here safely.
            if "set_config" not in str(e).lower() and "syntax error" not in str(e).lower():
                logger.critical(
                    "CRITICAL SECURITY FAILURE: Failed to restore RLS context in event listener",
                    error=str(e),
                    tenant_id=tenant_id,
                    exc_info=True,
                )

    # Invoke the async function in the synchronous checkout event
    dbapi_connection.run_async(_do_restore)


async def set_rls_context(
    connection: AsyncConnection,
    tenant_id: str,
    user_id: str | None = None,
) -> None:
    """
    Set RLS context variables in the database session.

    P2-2: Optimized to use single roundtrip for all context variables.

    Args:
        connection: Database connection
        tenant_id: Tenant UUID to set as current tenant
        user_id: Optional user UUID to set as current user

    Raises:
        Exception: If setting context fails (fail-closed)
    """
    # SQLite does not support set_config - skip silently
    if _is_sqlite(connection):
        logger.debug("Skipping RLS context set (SQLite)")
        return

    try:
        # P2-2: Single roundtrip for all context variables using COALESCE
        if user_id:
            await connection.execute(
                text(f"""
                    SELECT
                        set_config('{RLS_TENANT_VAR}', :tenant_id, true),
                        set_config('{RLS_USER_VAR}', :user_id, true)
                """),
                {"tenant_id": tenant_id, "user_id": user_id},
            )
            logger.debug("RLS context set", tenant_id=tenant_id, user_id=user_id)
        else:
            await connection.execute(
                text(f"SELECT set_config('{RLS_TENANT_VAR}', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            logger.debug("RLS context set", tenant_id=tenant_id)

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

    P2-2: Optimized to use single roundtrip for all reset operations.

    Args:
        connection: Database connection

    Note:
        Fails open - logs warnings but doesn't raise exceptions
    """
    # SQLite does not support set_config - skip silently
    if _is_sqlite(connection):
        logger.debug("Skipping RLS context clear (SQLite)")
        return

    try:
        # P2-2: Single roundtrip to reset all RLS variables
        await connection.execute(
            text(f"""
                SELECT
                    set_config('{RLS_TENANT_VAR}', '', true),
                    set_config('{RLS_USER_VAR}', '', true),
                    set_config('{RLS_BYPASS_VAR}', '', true)
            """)
        )
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
        # Store in session info for restoration upon new transactions
        if hasattr(session, "info"):
            session.info["rls_tenant_id"] = tenant_id
            session.info["rls_user_id"] = user_id
        else:
            session.sync_session.info["rls_tenant_id"] = tenant_id
            session.sync_session.info["rls_user_id"] = user_id

        # Set RLS context
        await set_rls_context(connection, tenant_id, user_id)
        yield session

    except Exception:
        # Let the upstream context manager (async_get_db) handle the rollback
        # to avoid "current transaction is aborted" errors when calling RESET
        # on a failed transaction or attempting double rollbacks.
        raise

    finally:
        # Always clear context, even on exception
        if hasattr(session, "info"):
            session.info.pop("rls_tenant_id", None)
            session.info.pop("rls_user_id", None)
        else:
            session.sync_session.info.pop("rls_tenant_id", None)
            session.sync_session.info.pop("rls_user_id", None)

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

    # SQLite does not support set_config - skip bypass silently
    if _is_sqlite(connection):
        logger.debug("Skipping RLS bypass (SQLite)")
        yield session
        return

    try:
        # Store in session info for restoration upon new transactions
        if hasattr(session, "info"):
            session.info["rls_bypass"] = True
            session.info["rls_user_id"] = user_id
        else:
            session.sync_session.info["rls_bypass"] = True
            session.sync_session.info["rls_user_id"] = user_id

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

        # SECURITY FIX: Implement RLS bypass audit logging
        # Log to rls_audit_log table for compliance and security investigation
        try:
            await connection.execute(
                text("""
                    INSERT INTO rls_audit_log (user_id, reason, timestamp, ip_address)
                    VALUES (:user_id, :reason, NOW(), :ip_address)
                """),
                {
                    "user_id": user_id or "system",
                    "reason": reason,
                    "ip_address": None,  # Will be populated by trigger if available
                },
            )
            logger.debug("RLS bypass audit log created", user_id=user_id, reason=reason)
        except Exception as audit_error:
            # SECURITY FIX: Fail closed - if we can't audit, we don't bypass
            logger.error(
                "Failed to create RLS bypass audit log - failing closed",
                user_id=user_id,
                reason=reason,
                error=str(audit_error),
            )
            raise

        yield session

    finally:
        # Clear bypass flag from stored info
        if hasattr(session, "info"):
            session.info.pop("rls_bypass", None)
            session.info.pop("rls_user_id", None)
        else:
            session.sync_session.info.pop("rls_bypass", None)
            session.sync_session.info.pop("rls_user_id", None)

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
            from src.app.core.db.database import get_db_context

            # Get session and set RLS context using proper async context manager
            async with get_db_context() as session:
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
    # SQLite does not support current_setting
    if _is_sqlite(connection):
        return None
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
