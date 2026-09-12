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


class RLSAuditFailureError(RuntimeError):
    """Raised when the mandatory RLS bypass audit-log INSERT fails (Q-M9).

    Typed successor of the generic ``RuntimeError`` raised from
    :func:`bypass_rls` when the compliance audit row cannot be written —
    an attacker able to force audit failure must not gain a silent RLS
    bypass. Callers (e.g. tenant middleware) catch THIS class instead of
    string-matching ``"audit log failure" in str(e)``.
    """


def _is_sqlite(connection: Any) -> bool:
    """Check if the connection is using SQLite dialect."""
    try:
        return bool(connection.dialect.name == "sqlite")
    except Exception:
        return False


def _mark_rls_dirty(connection: AsyncConnection) -> None:
    """Flag the pooled connection's record info as RLS-dirty.

    MUST be called BEFORE the GUC mutation it guards (fail-safe ordering):
    if the mutation then fails, the flag stays set and the next checkout
    re-asserts — an extra SELECT, never a stale-GUC skip.

    The flag (and the skip that reads it) only exists when
    ``connection.sync_connection.info`` is a real dict, which is exactly
    when ``restore_rls_context`` can observe it: the skip requires
    ``info["rls_tenant"] == <real tenant str>``, a comparison test doubles
    (MagicMock ``.info``) can never satisfy. Non-dict info is therefore
    left untouched — the checkout degrades to always-restore.
    """
    record_info = connection.sync_connection.info if connection.sync_connection is not None else None
    if isinstance(record_info, dict):
        record_info["rls_dirty"] = True


def restore_rls_context(dbapi_connection: Any, connection_record: Any, connection_proxy: Any) -> None:
    """Restores RLS context when a connection is acquired from the pool.

    This is necessary because PostgreSQL `set_config` with `is_local=True`
    is transaction-scoped. By hooking into the pool checkout event with an async callback,
    we can set a session-level config (`is_local=False`) that persists across all
    transactions on this connection checkout, until it is returned to the pool and checked out again.

    SECURITY: ``set_config`` runs on every checkout UNLESS the dirty-flag
    skip fires: ``connection_record.info["rls_tenant"]`` already equals this
    checkout's tenant AND ``info["rls_dirty"]`` is unset. The skip is safe
    because session-level GUCs persist on a pooled connection across
    checkouts, and every out-of-listener GUC mutation in this module
    (``set_rls_context``, ``clear_rls_context``, ``bypass_rls`` set) marks
    ``info["rls_dirty"]`` BEFORE mutating (fail-safe ordering: a failed
    mutation leaves the flag set, forcing re-assertion). A same-tenant skip
    shortcut therefore cannot carry a stale ``app.bypass_rls`` or tenant GUC
    into the next checkout. Re-asserting the tenant and clearing bypass/user
    on every non-skipped checkout is the fail-closed invariant.
    NOTE: the no-tenant branch never skips — an UNSET GUC (fresh pooled
    connection) reads as NULL while an asserted-empty GUC reads as '', and
    ``current_setting(..., true)`` NULL-vs-'' semantics differ per policy.
    Unauthenticated/no-tenant checkouts are low-volume; the tenant branch
    (every authed request) is where the skip pays.
    """
    tenant = get_current_tenant()
    tenant_id = str(tenant.id) if tenant else None

    async def _do_restore(conn: Any) -> None:
        try:
            # We use is_local=false so the config persists for the entire connection checkout.
            # When the connection is returned to the pool and checked out by another request,
            # this event will fire again and overwrite it.
            record_info = connection_record.info if connection_record is not None else {}
            if tenant_id:
                if not record_info.get("rls_dirty") and record_info.get("rls_tenant") == tenant_id:
                    # P2 RLS dirty-flag skip: this pooled connection already
                    # asserts this tenant's GUCs (set by a previous checkout's
                    # restore) and no out-of-listener mutation happened since
                    # (set/clear/bypass all mark rls_dirty first). 0 RTT.
                    return
                await conn.execute(
                    f"SELECT set_config('{RLS_TENANT_VAR}', $1, false), "
                    f"set_config('{RLS_USER_VAR}', '', false), "
                    f"set_config('{RLS_BYPASS_VAR}', '', false)",
                    tenant_id,
                )
                if connection_record is not None:
                    connection_record.info["rls_tenant"] = tenant_id
                    connection_record.info.pop("rls_dirty", None)
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

    # P2 RLS dirty flag (fail-safe ordering: mark BEFORE mutating, so a
    # failed set leaves the next checkout re-asserting instead of skipping).
    _mark_rls_dirty(connection)

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

    # P2 RLS dirty flag (fail-safe ordering: mark BEFORE mutating).
    _mark_rls_dirty(connection)

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

    # P2 RLS dirty flag (fail-safe ordering: mark BEFORE the bypass SET;
    # the finally-clear deliberately leaves the flag set — any bypass during
    # a checkout forces the next checkout to re-assert fail-closed).
    _mark_rls_dirty(connection)

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
        except Exception as audit_error:
            # SECURITY FIX: Audit log failure MUST be fatal. If an attacker can force
            # the audit log insertion to fail, they could bypass RLS silently.
            logger.error(
                "Failed to create RLS bypass audit log (FATAL)",
                user_id=user_id,
                reason=reason,
                error=str(audit_error),
            )
            # Re-raise to abort the RLS bypass (Q-M9: typed, not string-matched)
            raise RLSAuditFailureError(f"RLS bypass aborted due to audit log failure: {audit_error}") from audit_error

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
    "RLSAuditFailureError",
    "set_rls_context",
    "clear_rls_context",
    "rls_context",
    "bypass_rls",
    "get_current_tenant_id",
    "verify_rls_enabled",
    "list_rls_policies",
]
