"""Notification service for sending emails with template support."""

import asyncio
import hashlib
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any
from uuid import UUID

from jinja2 import FileSystemLoader, Template
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.clients.notifications import EmailMessage, SMTPProvider, get_email_provider  # noqa: F401
from src.app.core.db.tenant_crud import tenant_scope
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.redis_provider import get_redis
from src.app.core.utils.rate_limit import rate_limiter
from src.app.core.utils.retry import with_retry
from src.app.modules.notifications.models import EmailTemplateOverride, Notification, NotificationStatus

logger = get_logger(__name__)

AUTH_CRITICAL_TEMPLATES: frozenset[str] = frozenset(
    {
        "email_verification",
        "magic_link",
        "password_reset",
    }
)

# Initialize Jinja2 environment
TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates" / "email"
# Use SandboxedEnvironment to prevent SSTI attacks from user-supplied templates
jinja_env = SandboxedEnvironment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)

# P2-7: compiled-template cache for render_template(). get_template() has a
# loader-level cache, but from_string() compiles on every call and DB-backed
# tenant overrides bypass the loader entirely. Keyed by content hash so tenant
# edits invalidate automatically. Plain-dict FIFO — benign races (same key
# compiled twice) are harmless; rendering holds no locks.
_MAX_CACHED_TEMPLATES = 256
_COMPILED_TEMPLATE_CACHE: dict[str, Template] = {}


class NotificationService:
    """Service for sending and tracking email notifications."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize notification service.

        Args:
            db: Database session
        """
        self.db = db

    async def get_template_content(
        self,
        tenant_id: UUID | None,
        template_name: str,
    ) -> tuple[str, str]:
        """Get email template content (subject and body).

        Checks cache first, then tenant override, then falls back to default file template.

        Args:
            tenant_id: Tenant ID
            template_name: Name of the template (e.g., "welcome", "password_reset")

        Returns
        -------
            Tuple of (subject, html_content)
        """
        # Try cache first to avoid N+1 queries
        from src.app.core.utils.cache import build_tenant_key

        cache_key = build_tenant_key(f"email_template:{template_name}", tenant_id)
        redis_client = get_redis()
        if redis_client:
            try:
                cached = await redis_client.get(cache_key)
                if cached:
                    return tuple(json_loads(cached))
            except Exception as e:
                logger.debug("Cache get failed", error=e)

        # Check for tenant override
        override = None
        if tenant_id:
            query = select(EmailTemplateOverride).where(
                tenant_scope(EmailTemplateOverride, tenant_id),
                EmailTemplateOverride.template_name == template_name,
            )
            result = await self.db.execute(query)
            override = result.scalar_one_or_none()

        if override:
            logger.info(
                "using_template_override",
                tenant_id=str(tenant_id),
                template_name=template_name,
            )
            data = (override.subject_override or template_name, override.content)
        else:
            # Use default file template - return the RAW TEMPLATE SOURCE, not rendered output.
            # BUG FIX: Previously called template.render() here without any context, which
            # pre-renders the template with empty variables and destroys the Jinja2 syntax
            # (e.g., {{ user.name }} becomes empty string). The raw source must be stored
            # so that render_template() can fill variables later.
            try:
                # Non-blocking template loading with thread pool (validates the template exists)
                template_file = f"{template_name}.html.jinja2"
                try:
                    await asyncio.to_thread(jinja_env.get_template, template_file)
                except Exception:
                    template_file = f"{template_name}.html"
                    await asyncio.to_thread(jinja_env.get_template, template_file)

                # Default subject from template name
                subject = template_name.replace("_", " ").title()
                # Return the raw Jinja2 source via the loader.
                # get_source() returns (source_str, filename, uptodate_fn) — we take [0].
                # jinja_env.loader is always a FileSystemLoader (set at module init), never None.
                assert jinja_env.loader is not None
                raw_source, _, _ = await asyncio.to_thread(jinja_env.loader.get_source, jinja_env, template_file)
                data = (subject, raw_source)
            except Exception as e:
                logger.error(
                    "template_load_failed",
                    template_name=template_name,
                    error=str(e),
                )
                raise ValueError(f"Template '{template_name}' not found")

        # Update cache (1 hour TTL)
        if redis_client:
            try:
                await redis_client.set(cache_key, json_dumps(data, as_str=True), ex=3600)
            except Exception as e:
                logger.debug("Cache set failed", error=e)

        return data

    async def render_template(
        self,
        tenant_id: UUID | None,
        template_name: str,
        context: dict[str, Any],
    ) -> tuple[str, str]:
        """Render email template with context variables.

        Args:
            tenant_id: Tenant ID
            template_name: Name of the template
            context: Variables to pass to the template

        Returns
        -------
            Tuple of (subject, rendered_html)
        """
        subject, template_content = await self.get_template_content(tenant_id, template_name)

        # Render with context using the sandboxed environment
        # This prevents execution of unsafe code if the template comes from user input
        # P2-7: from_string() compiles on every call — serve from the
        # content-hash-keyed cache (see _COMPILED_TEMPLATE_CACHE above).
        cache_key = hashlib.sha256(template_content.encode("utf-8")).hexdigest()
        template = _COMPILED_TEMPLATE_CACHE.get(cache_key)
        if template is None:
            template = jinja_env.from_string(template_content)
            if len(_COMPILED_TEMPLATE_CACHE) >= _MAX_CACHED_TEMPLATES:
                _COMPILED_TEMPLATE_CACHE.pop(next(iter(_COMPILED_TEMPLATE_CACHE)))
            _COMPILED_TEMPLATE_CACHE[cache_key] = template
        rendered_html = template.render(**context)

        return subject, rendered_html

    async def send_email(
        self,
        tenant_id: UUID | None,
        to_email: str,
        template_name: str,
        context: dict[str, Any],
        user_id: int | None = None,
    ) -> Notification | None:
        """Send an email notification.

        This method queues the email for async delivery via ARQ.

        Args:
            tenant_id: Tenant ID
            to_email: Recipient email address
            template_name: Name of the email template
            context: Template variables
            user_id: Optional user ID

        Returns
        -------
            Notification record, or None if skipped by user preferences
        """
        # Check user preferences if user_id is provided (auth-critical bypasses quiet hours)
        if user_id and template_name not in AUTH_CRITICAL_TEMPLATES:
            should_send = await self.should_send_notification(user_id, template_name)
            if not should_send:
                logger.info(
                    "notification_skipped_by_user_preferences",
                    user_id=user_id,
                    template_name=template_name,
                )
                # BUG FIX: Skipped notifications should NOT be stored as PENDING or FAILED.
                # FAILED implies an error occurred; PENDING is confusing. Instead,
                # we use a specific SKIPPED status if available, or just use FAILED but with a clear message.
                # Since the schema might not have SKIPPED, we will not create a DB record at all
                # for skipped notifications.
                return None

        from src.app.shared.exceptions import RateLimitExceededError

        # Rate limiting check (T105)
        # 100 emails/minute per tenant, 1000 emails/hour per tenant.
        # N4: probe both windows concurrently (1 Redis round instead of 2).
        # Note: both counters increment even if the minute probe rejects —
        # accepted trade-off, the rejected request consumes one hourly slot.
        rate_limit_key_min = f"notif:min:{tenant_id}" if tenant_id else f"notif:min:global:{to_email}"
        rate_limit_key_hour = f"notif:hour:{tenant_id}" if tenant_id else f"notif:hour:global:{to_email}"
        (is_limited_min, _), (is_limited_hour, _) = await asyncio.gather(
            rate_limiter.check_rate_limit(rate_limit_key_min, 100, 60),
            rate_limiter.check_rate_limit(rate_limit_key_hour, 1000, 3600),
        )
        if is_limited_min:
            logger.warning("notification_rate_limit_exceeded_min", tenant_id=str(tenant_id))
            raise RateLimitExceededError("Rate limit exceeded: 100 emails per minute")
        if is_limited_hour:
            logger.warning("notification_rate_limit_exceeded_hour", tenant_id=str(tenant_id))
            raise RateLimitExceededError("Rate limit exceeded: 1000 emails per hour")

        # Render template
        subject, html_content = await self.render_template(tenant_id, template_name, context)

        # Create notification record
        notification = Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            template_name=template_name,
            to_email=to_email,
            subject=subject,
            status=NotificationStatus.PENDING,
        )
        self.db.add(notification)
        await self.db.commit()
        await self.db.refresh(notification)

        # Direct enqueue to ARQ worker (Candidate 8: retiring redundant hop)
        from src.app.core.utils.queue import pool as arq_pool

        if arq_pool:
            try:
                await arq_pool.enqueue_job(
                    "send_email_task",
                    str(notification.id),
                    to_email,
                    subject,
                    html_content,
                    str(tenant_id) if tenant_id else None,
                )
                logger.info(
                    "email_queued",
                    notification_id=str(notification.id),
                    template=template_name,
                )
            except Exception as e:
                logger.error(
                    "failed_to_enqueue_email_task",
                    notification_id=str(notification.id),
                    error=str(e),
                )
                notification.status = NotificationStatus.FAILED
                notification.error_message = f"Enqueue error: {e}"
                await self.db.commit()
        else:
            logger.warning(
                "arq_queue_not_available",
                notification_id=str(notification.id),
            )
            notification.status = NotificationStatus.FAILED
            notification.error_message = "ARQ queue not available"
            await self.db.commit()

        return notification

    async def mark_sent(self, notification_id: UUID) -> None:
        """Mark notification as sent.

        Args:
            notification_id: Notification ID
        """
        notification = await self.db.get(Notification, notification_id)
        if notification:
            notification.status = NotificationStatus.SENT
            notification.sent_at = datetime.now(UTC)
            await self.db.commit()

    async def mark_failed(self, notification_id: UUID, error_message: str) -> None:
        """Mark notification as failed.

        Args:
            notification_id: Notification ID
            error_message: Error message
        """
        notification = await self.db.get(Notification, notification_id)
        if notification:
            notification.status = NotificationStatus.FAILED
            notification.error_message = error_message
            await self.db.commit()

    async def get_notifications(
        self,
        tenant_id: UUID,
        user_id: int | None = None,
        status: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, list[Notification]]:
        """Get notification history.

        Args:
            tenant_id: Tenant ID
            user_id: Optional user filter
            status: Optional status filter
            start_date: Optional from date filter
            end_date: Optional to date filter
            limit: Maximum results
            offset: Offset for pagination

        Returns
        -------
            Tuple of (total_count, List of notifications)
        """
        from sqlalchemy import func

        total_col = func.count().over().label("total")
        # P2-2: tenant criterion via the tenant_scope() seam.
        query = select(Notification, total_col).where(tenant_scope(Notification, tenant_id))

        enum_status = None
        if status:
            try:
                enum_status = NotificationStatus(status)
                query = query.where(Notification.status == enum_status)
            except ValueError:
                return 0, []
        if user_id:
            query = query.where(Notification.user_id == user_id)
        if start_date:
            query = query.where(Notification.created_at >= start_date)
        if end_date:
            query = query.where(Notification.created_at <= end_date)

        query = query.order_by(Notification.created_at.desc()).limit(limit).offset(offset)

        result = await self.db.execute(query)
        rows = result.unique().all()
        if rows:
            total = int(rows[0][1])
            notifications = [row[0] for row in rows]
            return total, notifications

        if offset == 0:
            return 0, []

        # Out-of-range page fallback
        count_query = select(func.count()).select_from(Notification).where(tenant_scope(Notification, tenant_id))
        if user_id:
            count_query = count_query.where(Notification.user_id == user_id)
        if enum_status is not None:
            count_query = count_query.where(Notification.status == enum_status)
        if start_date:
            count_query = count_query.where(Notification.created_at >= start_date)
        if end_date:
            count_query = count_query.where(Notification.created_at <= end_date)
        total_result = await self.db.execute(count_query)
        return total_result.scalar_one(), []

    async def get_notification(
        self,
        notification_id: UUID,
        tenant_id: UUID,
        user_id: int | None = None,
    ) -> Notification | None:
        """Get a single notification by ID scoped to tenant and user."""
        query = select(Notification).where(
            Notification.id == notification_id,
            tenant_scope(Notification, tenant_id),
        )
        if user_id is not None:
            query = query.where(Notification.user_id == user_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    async def should_send_notification(
        self,
        user_id: int,
        notification_type: str,
        current_time: datetime | None = None,
    ) -> bool:
        """Check if a notification should be sent based on user preferences.

        Args:
            user_id: User ID to check preferences for
            notification_type: Type of notification (e.g., "welcome", "password_reset")
            current_time: Current time for quiet hours check (defaults to now)

        Returns
        -------
            True if notification should be sent, False otherwise
        """
        from src.app.modules.user_preferences.models import UserPreferences

        now = current_time or datetime.now(UTC)

        # Fetch user preferences
        query = select(UserPreferences).where(UserPreferences.user_id == user_id)
        result = await self.db.execute(query)
        prefs = result.scalar_one_or_none()

        # If no preferences exist, default to sending
        if not prefs:
            return True

        # Check if email notifications are enabled
        if not prefs.email_notifications_enabled:
            logger.debug(
                "email_notifications_disabled",
                user_id=user_id,
                notification_type=notification_type,
            )
            return False

        disabled_types = prefs.disabled_notification_types or []
        if notification_type in disabled_types:
            logger.debug(
                "notification_type_disabled",
                user_id=user_id,
                notification_type=notification_type,
            )
            return False

        # Check quiet hours
        if prefs.quiet_hours_enabled:
            if prefs.quiet_hours_start and prefs.quiet_hours_end:
                try:
                    start_time = time.fromisoformat(prefs.quiet_hours_start)
                    end_time = time.fromisoformat(prefs.quiet_hours_end)
                    current_time_only = now.time()

                    if start_time < end_time:
                        # Same-day range (e.g., 09:00 - 17:00, start < end)
                        in_quiet_hours = start_time <= current_time_only < end_time
                    else:
                        # Cross-day range (e.g., 22:00 - 07:00, start > end, spans midnight)
                        in_quiet_hours = current_time_only >= start_time or current_time_only < end_time

                    if in_quiet_hours:
                        logger.debug(
                            "quiet_hours_active",
                            user_id=user_id,
                            notification_type=notification_type,
                            quiet_hours_start=prefs.quiet_hours_start,
                            quiet_hours_end=prefs.quiet_hours_end,
                        )
                        return False
                except ValueError as e:
                    logger.warning(
                        "invalid_quiet_hours_format",
                        user_id=user_id,
                        start=prefs.quiet_hours_start,
                        end=prefs.quiet_hours_end,
                        error=str(e),
                    )
        return True

    async def list_template_overrides(
        self, tenant_id: UUID, limit: int = 100, offset: int = 0
    ) -> list[EmailTemplateOverride]:
        """List all email template overrides for a tenant"""
        query = (
            select(EmailTemplateOverride)
            .where(tenant_scope(EmailTemplateOverride, tenant_id))
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def create_template_override(
        self, tenant_id: UUID, template_name: str, subject_override: str | None, content: str
    ) -> EmailTemplateOverride:
        """Create or update an email template override"""
        query = select(EmailTemplateOverride).where(
            tenant_scope(EmailTemplateOverride, tenant_id),
            EmailTemplateOverride.template_name == template_name,
        )
        result = await self.db.execute(query)
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing
            existing.subject_override = subject_override
            existing.content = content
            await self.db.commit()
            await self.db.refresh(existing)

            # Invalidate cache
            redis_client = get_redis()
            if redis_client:
                from src.app.core.utils.cache import build_tenant_key

                await redis_client.delete(build_tenant_key(f"email_template:{template_name}", tenant_id))

            return existing

        # Create new
        override = EmailTemplateOverride(
            tenant_id=tenant_id,
            template_name=template_name,
            subject_override=subject_override,
            content=content,
        )
        self.db.add(override)
        await self.db.commit()
        await self.db.refresh(override)

        # Invalidate cache
        redis_client = get_redis()
        if redis_client:
            from src.app.core.utils.cache import build_tenant_key

            await redis_client.delete(build_tenant_key(f"email_template:{template_name}", tenant_id))

        return override

    async def delete_template_override(self, tenant_id: UUID, template_name: str) -> bool:
        """Delete an email template override returns True if deleted, False if not found"""
        query = select(EmailTemplateOverride).where(
            tenant_scope(EmailTemplateOverride, tenant_id),
            EmailTemplateOverride.template_name == template_name,
        )
        result = await self.db.execute(query)
        override = result.scalar_one_or_none()

        if not override:
            return False

        await self.db.delete(override)
        await self.db.commit()

        # Invalidate cache
        redis_client = get_redis()
        if redis_client:
            from src.app.core.utils.cache import build_tenant_key

            await redis_client.delete(build_tenant_key(f"email_template:{template_name}", tenant_id))

        return True


# ARQ task for actual email delivery
async def send_email_task(
    ctx: dict[str, Any],
    notification_id: str,
    to_email: str,
    subject: str,
    html_content: str,
    tenant_id: str | None = None,
) -> None:
    """ARQ worker task to send emails via SMTP.

    Args:
        ctx: ARQ context
        notification_id: Notification record ID
        to_email: Recipient email
        subject: Email subject
        html_content: HTML body content
    """
    from src.app.core.db.database import async_session_maker
    from src.app.core.db.rls import rls_context

    async with async_session_maker() as db:
        # BUG FIX: Previously, the rls_context was exited immediately after creating
        # the service object (the entire context block contained only `service = ...`).
        # This meant mark_sent() and mark_failed() DB writes happened WITHOUT tenant
        # isolation, which violates RLS and could cause cross-tenant data leakage.
        # Fix: wrap ALL DB operations (create, SMTP send, mark) inside the context.
        async def _run_with_context() -> None:
            service = NotificationService(db)
            provider = get_email_provider()
            message = EmailMessage(
                to_email=to_email,
                subject=subject,
                html_content=html_content,
            )

            try:
                # Protect transmission with automatic exponential backoff retry
                async def _send_with_check() -> bool:
                    result = await provider.send_email(message)
                    if not result:
                        raise RuntimeError(f"EmailProvider failed to send email to {to_email}")
                    return bool(result)

                await with_retry(
                    fn=_send_with_check,
                    max_attempts=3,
                    delay_seconds=1.0,
                    backoff_factor=2.0,
                )

                # Mark as sent (inside context = RLS enforced)
                await service.mark_sent(UUID(notification_id))
                logger.info("email_sent_success", notification_id=notification_id)

            except Exception as e:
                logger.error(
                    "email_send_failed",
                    notification_id=notification_id,
                    error=str(e),
                    exc_info=True,
                )
                # Mark as failed (inside context = RLS enforced)
                await service.mark_failed(UUID(notification_id), str(e))
                raise  # Re-raise for ARQ retry

        try:
            if tenant_id:
                async with rls_context(db, tenant_id):
                    await _run_with_context()
            else:
                await _run_with_context()
        except Exception:
            raise  # Already logged inside _run_with_context


# Event handler for notification.send events
async def handle_notification_send_event(data: dict[str, Any]) -> None:
    """Handle notification send events by queuing email delivery.

    This handler receives events published by send_email() and other services,
    then enqueues the actual email delivery via ARQ background worker.

    Args:
        data: Event payload with notification details:
            - notification_id: ID of the notification record
            - to_email: Recipient email address
            - subject: Email subject line
            - html_content: Rendered HTML body
    """
    from src.app.core.utils.queue import pool as arq_pool

    notification_id = data.get("notification_id")
    to_email = data.get("to_email")
    subject = data.get("subject")
    html_content = data.get("html_content")
    tenant_id = data.get("tenant_id")

    if not all([notification_id, to_email, subject, html_content]):
        logger.warning(
            "notification_send_event_missing_data",
            notification_id=notification_id,
            has_to_email=bool(to_email),
            has_subject=bool(subject),
            has_html_content=bool(html_content),
        )
        return

    # Enqueue the email delivery task via ARQ
    if arq_pool:
        try:
            await arq_pool.enqueue_job(
                "send_email_task",
                notification_id,
                to_email,
                subject,
                html_content,
                tenant_id,
            )
            logger.debug(
                "email_task_enqueued",
                notification_id=notification_id,
            )
        except Exception as e:
            logger.error(
                "failed_to_enqueue_email_task",
                notification_id=notification_id,
                error=str(e),
            )
            # BUG FIX: Mark notification as FAILED so it doesn't stay stuck in PENDING
            await _mark_notification_failed(notification_id, tenant_id, f"Enqueue error: {e}")
    else:
        logger.warning(
            "arq_queue_not_available",
            notification_id=notification_id,
        )
        # BUG FIX: Mark notification as FAILED so it doesn't stay stuck in PENDING
        await _mark_notification_failed(notification_id, tenant_id, "ARQ queue not available")

async def _mark_notification_failed(notification_id: str | None, tenant_id: str | None, reason: str) -> None:
    """Mark a notification as failed in the database."""
    if not notification_id:
        return
    try:
        from uuid import UUID

        from src.app.core.db.database import local_session
        from src.app.core.db.rls import rls_context

        async with local_session() as db:
            ctx = rls_context(db, tenant_id) if tenant_id else None
            if ctx:
                async with ctx:
                    service = NotificationService(db)
                    await service.mark_failed(UUID(notification_id), reason)
            else:
                service = NotificationService(db)
                await service.mark_failed(UUID(notification_id), reason)
    except Exception as e:
        logger.error("failed_to_mark_notification_failed", notification_id=notification_id, error=str(e))



async def handle_notification_request_event(data: dict[str, Any]) -> None:
    """Handle request to generate and send an email notification via outbox.

    Eliminates redundant event hops by creating the outbox record (Notification)
    and directly enqueuing the delivery task.

    Args:
        data: Event payload with request details:
            - to_email: Recipient email address
            - template_name: Name of the template
            - context: Template context variables
            - tenant_id: Optional tenant ID
            - user_id: Optional user ID
    """
    from src.app.core.utils.queue import pool as arq_pool

    to_email = data.get("to_email")
    template_name = data.get("template_name")

    if not to_email or not template_name:
        logger.warning("notification_request_missing_data", data=data)
        return

    tenant_id_str = data.get("tenant_id")
    tenant_id: UUID | None = None
    if tenant_id_str:
        try:
            tenant_id = UUID(str(tenant_id_str))
        except (ValueError, TypeError) as e:
            logger.warning(
                "invalid_tenant_id_in_notification_request",
                tenant_id=tenant_id_str,
                error=str(e),
            )
            return
    user_id = data.get("user_id")
    context = data.get("context", {})

    try:
        from src.app.core.db.database import local_session
        from src.app.core.db.rls import rls_context

        async with local_session() as db:
            if tenant_id:
                ctx: Any = rls_context(db, str(tenant_id))
            else:
                from contextlib import nullcontext

                ctx = nullcontext()

            async with ctx:
                service = NotificationService(db)

                # Check user preferences if user_id is provided (auth-critical bypasses quiet hours)
                if user_id and template_name not in AUTH_CRITICAL_TEMPLATES:
                    should_send = await service.should_send_notification(user_id, template_name)
                    if not should_send:
                        logger.info(
                            "notification_skipped_by_user_preferences",
                            user_id=user_id,
                            template_name=template_name,
                        )
                        return

                # Rate limiting check (T105)
                from src.app.shared.exceptions import RateLimitExceededError

                rate_limit_key_min = f"notif:min:{tenant_id}" if tenant_id else f"notif:min:global:{to_email}"
                rate_limit_key_hour = f"notif:hour:{tenant_id}" if tenant_id else f"notif:hour:global:{to_email}"
                (is_limited_min, _), (is_limited_hour, _) = await asyncio.gather(
                    rate_limiter.check_rate_limit(rate_limit_key_min, 100, 60),
                    rate_limiter.check_rate_limit(rate_limit_key_hour, 1000, 3600),
                )
                if is_limited_min or is_limited_hour:
                    limit_reason = "100 emails per minute" if is_limited_min else "1000 emails per hour"
                    logger.warning(
                        "notification_rate_limit_exceeded",
                        tenant_id=str(tenant_id),
                        reason=limit_reason,
                    )
                    # Create FAILED outbox record so there is an audit trace
                    notification = Notification(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        template_name=template_name,
                        to_email=to_email,
                        subject=f"Rate limited: {template_name}",
                        status=NotificationStatus.FAILED,
                    )
                    notification.error_message = f"Rate limit exceeded: {limit_reason}"
                    db.add(notification)
                    await db.commit()
                    raise RateLimitExceededError(f"Rate limit exceeded: {limit_reason}")
                # Render template
                subject, html_content = await service.render_template(tenant_id, template_name, context)

                # Create outbox record (status PENDING)
                notification = Notification(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    template_name=template_name,
                    to_email=to_email,
                    subject=subject,
                    status=NotificationStatus.PENDING,
                )
                db.add(notification)
                await db.commit()
                await db.refresh(notification)

                # Direct enqueue to ARQ worker (retiring redundant notification.send event hop)
                if arq_pool:
                    try:
                        await arq_pool.enqueue_job(
                            "send_email_task",
                            str(notification.id),
                            to_email,
                            subject,
                            html_content,
                            str(tenant_id) if tenant_id else None,
                        )
                        logger.info(
                            "email_queued",
                            notification_id=str(notification.id),
                            to_email=to_email,
                            template=template_name,
                        )
                    except Exception as e:
                        logger.error(
                            "failed_to_enqueue_email_task",
                            notification_id=str(notification.id),
                            error=str(e),
                        )
                        notification.status = NotificationStatus.FAILED
                        notification.error_message = f"Enqueue error: {e}"
                        await db.commit()
                else:
                    logger.warning(
                        "arq_queue_not_available",
                        notification_id=str(notification.id),
                        to_email=to_email,
                    )
                    notification.status = NotificationStatus.FAILED
                    notification.error_message = "ARQ queue not available"
                    await db.commit()

    except Exception as e:
        logger.error("failed_to_process_notification_request", error=str(e), exc_info=True)
        raise
