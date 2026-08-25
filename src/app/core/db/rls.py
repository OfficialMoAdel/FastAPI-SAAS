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

    P3 FIX: each pooled connection remembers which tenant it was last
    configured for (``connection_record.info["rls_tenant"]``). Repeat
    checkouts for the SAME tenant skip the set_config roundtrip entirely —
    session-level GUCs are already correct. Any tenant change, or a
    no-tenant checkout, still executes the statement (fail-closed reset).
    """
    tenant = get_current_tenant()
    tenant_id = str(tenant.id) if tenant else None

    # P3: skip when this physical connection was already configured for the
    # same tenant during its CURRENT checkout cycle. info is reset by
    # pool "reset-on-return", so a returned-and-rechecked-out connection
    # re-runs set_config — never stale.
    try:
        if tenant_id and connection_record is not None and connection_record.info.get("rls_tenant") == tenant_id:
            logger.debug("RLS context already configured; skipping redundant roundtrip")
            return
    except AttributeError:
        pass  # unusual record type — fall through to explicit set

    async def _do_restore(conn: Any) -> None:
        try:
            # We use is_local=false so the config persists for the entire connection checkout.
            # When the connection is returned to the pool and checked out by another request,
            # this event will fire again and overwrite it.
            if tenant_id:
                await conn.execute(
                    f"SELECT set_config('{RLS_TENANT_VAR}', $1, false), "
                    f"set_config('{RLS_USER_VAR}', '', false), "
                    f"set_config('{RLS_BYPASS_VAR}', '', false)",
                    tenant_id,
                )
                if connection_record is not None:
                    connection_record.info["rls_tenant"] = tenant_id
            else:
                # No tenant context at pool checkout. Reset ALL RLS vars to empty so a
                # previous connection's tenant context cannot leak into this checkout
                # (fail-closed). We still warn so a missing-context bug on an
                # authenticated path is observable rather than silently masked.
                logger.warning(
                    "restore_rls_context: no tenant context at pool checkout; resetting RLS vars to empty (fail-closed)"
                )
                await conn.execute(
                    f"SELECT set_config('{RLS_TENANT_VAR}', '', false), "
                    f"set_config('{RLS_USER_VAR}', '', false), "
                    f"set_config('{RLS_BYPASS_VAR}', '', false)"
                )
                if connection_record is not None:
                    connection_record.info.pop("rls_tenant", None)
        except Exception as e:
            # SECURITY FIX: Log heavily and raise.
            # SQLite does not support set_config and will fail here safely.
            if _is_sqlite(dbapi_connection):
                return

            logger.critical(
                "CRITICAL SECURITY FAILURE: Failed to restore RLS context in event listener",
                error=str(e),
                tenant_id=tenant_id,
                exc_info=True,
            )
            raise

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
                        set_config('{RLS_TENANT_VAR}', :tenant_id, false),
                        set_config('{RLS_USER_VAR}', :user_id, false)
                """),
                {"tenant_id": tenant_id, "user_id": user_id},
            )
            logger.debug("RLS context set", tenant_id=tenant_id, user_id=user_id)
        else:
            await connection.execute(
                text(f"SELECT set_config('{RLS_TENANT_VAR}', :tenant_id, false)"),
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
        Fails closed - raises on failure to prevent cross-tenant data leakage
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
                    set_config('{RLS_TENANT_VAR}', '', false),
                    set_config('{RLS_USER_VAR}', '', false),
                    set_config('{RLS_BYPASS_VAR}', '', false)
            """)
        )
        logger.debug("RLS context cleared")

    except Exception as e:
        # Fail closed - connection must be discarded to prevent cross-tenant data leakage
        logger.critical("CRITICAL: Failed to clear RLS context - discarding connection", error=str(e))
        raise


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

        # T4 FIX: the transaction may be aborted here (caller raised), so
        # clear_rls_context can fail - previously that CRITICAL raise replaced
        # the caller's real exception. Log instead of raising; the pool
        # checkout listener (restore_rls_context) still resets all RLS vars
        # fail-closed before this connection is reused.
        try:
            await clear_rls_context(connection)
        except Exception as clear_err:  # noqa: BLE001
            logger.error("Failed to clear RLS context during rls_context teardown", error=str(clear_err))


async def _write_bypass_audit(user_id: str | None, reason: str) -> None:
    """Write the RLS bypass audit row on a DEDICATED connection (T2 FIX).

    The INSERT runs outside the caller's transaction with an explicit COMMIT
    so the trail survives request-scoped rollbacks.

    rls_audit_log.user_id is an INTEGER column while callers pass app-level
    identifiers that may be UUIDs/strings: digit strings are coerced to int,
    anything else is preserved inside ``reason`` and stored as NULL so the
    audit insert itself can never fail on a type mismatch.
    """
    from src.app.core.db.database import async_engine

    audit_user_id: int | None = None
    if user_id is not None:
        try:
            audit_user_id = int(user_id)
        except (TypeError, ValueError):
            reason = f"{reason} | actor={user_id}"
            audit_user_id = None

    async with async_engine.connect() as audit_conn:
        await audit_conn.execute(
            text("""
                INSERT INTO rls_audit_log (user_id, reason, timestamp, ip_address)
                VALUES (:user_id, :reason, NOW(), :ip_address)
            """),
            {
                "user_id": audit_user_id,
                "reason": reason,
                "ip_address": None,  # Will be populated by trigger if available
            },
        )
        await audit_conn.commit()


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

        # Set bypass flag.
        # K4/T3 FIX: use a SESSION-level setting (is_local=false). With
        # transaction-local 'true', the first COMMIT inside a multi-batch
        # operation silently dropped app.bypass_rls and every later batch
        # failed closed. Session-level is safe: bypass_rls' finally clears it,
        # and restore_rls_context resets it to '' on every pool checkout.
        await connection.execute(text(f"SELECT set_config('{RLS_BYPASS_VAR}', 'true', false)"))

        # Log bypass event (CRITICAL for security audit)
        logger.warning(
            "RLS bypass enabled",
            reason=reason,
            user_id=user_id,
            extra={"security_event": "rls_bypass"},
        )

        # SECURITY FIX: Implement RLS bypass audit logging
        # Log to rls_audit_log table for compliance and security investigation.
        # T2 FIX: write on a DEDICATED connection + explicit COMMIT. Previously
        # the INSERT ran on the request's autobegun transaction which was never
        # committed (middleware path) or rolled back with the payload work, so
        # most bypasses left no audit trail despite the MUST-be-fatal design.
        try:
            # T2 FIX: dedicated connection + explicit COMMIT (see helper).
            await _write_bypass_audit(user_id, reason)
            logger.debug("RLS bypass audit log created", user_id=user_id, reason=reason)
        except Exception as audit_error:
            # SECURITY FIX: Audit log failure MUST be fatal. If an attacker can force
            # the audit log insertion to fail, they could bypass RLS silently.
            logger.error(
                "Failed to create RLS bypass audit log (FATAL)",
                user_id=user_id,
                reason=reason,
                error=str(audit_error),
            )
            # Re-raise to abort the RLS bypass
            raise RuntimeError(f"RLS bypass aborted due to audit log failure: {audit_error}") from audit_error

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
            # K4/T3 FIX: clear at session level to match the session-level set.
            await connection.execute(text(f"SELECT set_config('{RLS_BYPASS_VAR}', '', false)"))
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
        result = await connection.execute(text("SELECT current_setting(:t_var, true)"), {"t_var": RLS_TENANT_VAR})
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
