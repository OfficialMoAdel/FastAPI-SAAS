"""Notification service for sending emails with template support."""

import asyncio
import hashlib
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from jinja2 import FileSystemLoader, Template, TemplateNotFound
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.clients.notifications import EmailMessage, SMTPProvider, get_email_provider  # noqa: F401
from src.app.core.db.scoped import execute_scoped
from src.app.core.security.rate_policy import consume
from src.app.core.tenancy.scope import ExplicitTenant
from src.app.core.utils.retry import with_retry
from src.app.modules.notifications.models import EmailTemplateOverride, Notification, NotificationStatus
from src.app.modules.notifications.template_cache import (
    get_cached_template,
    invalidate_template_cache,
    store_cached_template,
)
from src.app.modules.tiers.entitlement import DEFAULT_ENTITLEMENT as _DEFAULT_QUOTA

logger = get_logger(__name__)

AUTH_CRITICAL_TEMPLATES: frozenset[str] = frozenset(
    {
        "email_verification",
        "magic_link",
        "password_reset",
    }
)

# Job-level ARQ retry schedule for send_email_task (transmission-level
# retries happen inside with_retry; these delays govern worker re-runs when
# the transmission budget is exhausted). Indexed by job_try - 1, so at the
# default ARQ_MAX_TRIES=3 only the first two entries fire (try 1 -> 10s,
# try 2 -> 60s; try 3 is terminal). The third entry is not dead weight: it
# is the schedule for operators who raise max tries, and keeping the tuple
# longer than the default is what makes that a config-only change.
EMAIL_RETRY_DELAYS: tuple[int, ...] = (10, 60, 360)

# P2-7: email rate-limit policy, shared by every door into the pipeline
# (send_email service door + handle_notification_request_event event door).
# Previously each door hard-coded its own 100/60 and 1000/3600 literals.
# A5: values owned by the tier entitlement seam (uniform across tiers
# today — identical effective behavior); names kept for compatibility.
EMAIL_RATE_LIMIT_PER_MINUTE: int = _DEFAULT_QUOTA.emails_per_minute
EMAIL_RATE_LIMIT_MINUTE_WINDOW_SECONDS: int = _DEFAULT_QUOTA.email_minute_window_seconds
EMAIL_RATE_LIMIT_PER_HOUR: int = _DEFAULT_QUOTA.emails_per_hour
EMAIL_RATE_LIMIT_HOUR_WINDOW_SECONDS: int = _DEFAULT_QUOTA.email_hour_window_seconds

# P2-6: outbox terminal semantics for notifications. PENDING rows older
# than NOTIFICATION_MAX_PENDING_AGE_HOURS are swept to FAILED by the
# recover_stuck_notifications cron (bounded terminal state — mirrors the
# webhooks recovery lifecycle without replicating its table shape).
# NOTE: re-enqueueing on recovery is impossible today because the rendered
# html body is not persisted on the Notification row; if recovery-retry is
# ever needed, add a body column first.
NOTIFICATION_MAX_PENDING_AGE_HOURS: int = 24
NOTIFICATION_RECOVERY_BATCH_SIZE: int = 100

# D4: the machine-readable failure classes carried on
# ``Notification.error_kind``. These are STABLE identifiers: an operator
# filters on them, and a dashboard counts them, so they must not be
# reworded casually the way a free-text message can be.
#
# Before this, a rate-limited row was identifiable only by parsing its
# subject ("Rate limited: {template}") — so refusals mixed into admin
# history that is filtered and sorted by subject, and a refusal looked
# like a message. The kind is the field to filter on; the subject stays
# human-readable.
ERROR_KIND_RATE_LIMITED: str = "rate_limited"
ERROR_KIND_ENQUEUE_ERROR: str = "enqueue_error"
ERROR_KIND_QUEUE_UNAVAILABLE: str = "queue_unavailable"
ERROR_KIND_RECOVERY_EXHAUSTED: str = "recovery_window_exhausted"
ERROR_KIND_SEND_ERROR: str = "send_error"

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
        cached_content = await get_cached_template(tenant_id, template_name)
        if cached_content is not None:
            return cached_content

        # Check for tenant override
        override = None
        if tenant_id:
            query = select(EmailTemplateOverride).where(
                EmailTemplateOverride.template_name == template_name,
            )
            result = await execute_scoped(
                query,
                db=self.db,
                model=EmailTemplateOverride,
                scope=ExplicitTenant(tenant_id, reason="legacy-criterion:EmailTemplateOverride"),
            )
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
                except TemplateNotFound:
                    # S-48: fall back ONLY when the .jinja2 genuinely does not
                    # exist. The old `except Exception` also fell back when the
                    # .jinja2 EXISTED but failed to parse/compile - and because
                    # src/app/templates/email/ holds divergent duplicate pairs
                    # (`email_verification.html` 1844 bytes vs the `.jinja2`
                    # 1128, different charset and title), that silently served
                    # DIFFERENT content instead of failing loudly.
                    template_file = f"{template_name}.html"
                    await asyncio.to_thread(jinja_env.get_template, template_file)
                except Exception as e:  # noqa: BLE001 - re-raised as a domain ValueError
                    raise ValueError(
                        f"Template '{template_name}.html.jinja2' exists but failed to load: {e}"
                    ) from e

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

        await store_cached_template(tenant_id, template_name, data)

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

    async def _rate_limited(
        self,
        tenant_id: UUID | None,
        to_email: str,
        template_name: str | None = None,
    ) -> str | None:
        """Check the shared email rate-limit policy (P2-7).

        Both doors into the email pipeline (send_email service door and
        handle_notification_request_event event door) call this helper so
        the policy constants are defined once.

        D1: auth-critical templates draw on a SEPARATE bucket from
        administratively-triggered sends. The preference gate already
        carves auth-critical templates out of quiet hours; without the
        matching carve-out here, a tenant that exhausted its own email
        budget also blocked its own ``password_reset`` / ``magic_link`` /
        ``email_verification`` mail — a tenant could lock out its own users'
        authentication.

        This is a SEPARATE BUCKET, not a bypass. The auth bucket has the
        same size as the general one, so the per-category bound is
        unchanged and an auth flood is still capped; what changes is that
        the tenant's TOTAL email budget becomes 2x the old value (100
        general + 100 auth per minute). That is a deliberate, bounded
        policy change, not an unbounded hole — and it is safe because the
        auth PATHS are themselves budgeted upstream
        (``magic_link_send`` is 3/min per email hash, ``auth`` is 5/min per
        IP), so one caller cannot drive this bucket arbitrarily fast.

        Args:
            tenant_id: Tenant ID (None falls back to a global per-email key)
            to_email: Recipient email address (used for the global key)
            template_name: Template being sent. An auth-critical template
                uses the dedicated auth bucket; everything else uses the
                tenant's general bucket.

        Returns:
            The limit reason when rate-limited (e.g. "100 emails per minute"),
            or None when the request is within budget.
        """
        # D1: the auth bucket is a separate key namespace, so a general
        # send can never consume an auth send's budget (and vice versa).
        auth_critical = template_name in AUTH_CRITICAL_TEMPLATES
        bucket = "auth" if auth_critical else "general"
        # N4: probe both windows concurrently (1 Redis round instead of 2).
        # Note: both counters increment even if the minute probe rejects —
        # accepted trade-off, the rejected request consumes one hourly slot.
        if tenant_id:
            rate_limit_key_min = f"notif:min:{bucket}:{tenant_id}"
            rate_limit_key_hour = f"notif:hour:{bucket}:{tenant_id}"
        else:
            rate_limit_key_min = f"notif:min:{bucket}:global:{to_email}"
            rate_limit_key_hour = f"notif:hour:{bucket}:global:{to_email}"
        # Cross the one rate-policy seam so these budgets honour
        # RATE_LIMIT_ALGORITHM like every other consumer (they previously
        # pinned the fixed-window engine regardless of configuration).
        (is_limited_min, _, _, _), (is_limited_hour, _, _, _) = await asyncio.gather(
            consume(
                rate_limit_key_min,
                EMAIL_RATE_LIMIT_PER_MINUTE,
                EMAIL_RATE_LIMIT_MINUTE_WINDOW_SECONDS,
            ),
            consume(
                rate_limit_key_hour,
                EMAIL_RATE_LIMIT_PER_HOUR,
                EMAIL_RATE_LIMIT_HOUR_WINDOW_SECONDS,
            ),
        )
        if is_limited_min:
            logger.warning(
                "notification_rate_limit_exceeded_min",
                tenant_id=str(tenant_id),
                bucket=bucket,
            )
            return f"{EMAIL_RATE_LIMIT_PER_MINUTE} emails per minute"
        if is_limited_hour:
            logger.warning(
                "notification_rate_limit_exceeded_hour",
                tenant_id=str(tenant_id),
                bucket=bucket,
            )
            return f"{EMAIL_RATE_LIMIT_PER_HOUR} emails per hour"
        return None

    async def _create_and_enqueue(
        self,
        tenant_id: UUID | None,
        to_email: str,
        template_name: str,
        context: dict[str, Any],
        user_id: int | None = None,
    ) -> Notification:
        """Render, persist the PENDING outbox row, and enqueue delivery (P2-7).

        Shared by every door into the email pipeline so the PENDING row,
        enqueue contract, and FAILED-marking idioms (enqueue error /
        pool unavailable) are defined exactly once.

        Args:
            tenant_id: Tenant ID
            to_email: Recipient email address
            template_name: Name of the email template
            context: Template variables
            user_id: Optional user ID

        Returns:
            The persisted Notification record (PENDING, or FAILED when the
            enqueue step failed).
        """
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
                job = await arq_pool.enqueue_job(
                    "send_email_task",
                    str(notification.id),
                    to_email,
                    subject,
                    html_content,
                    str(tenant_id) if tenant_id else None,
                    # D3: the ARQ job id IS the notification id. That makes
                    # the enqueue's liveness PROVABLE from the notification
                    # row alone — the recovery sweep can ask Redis whether
                    # the job still exists instead of guessing from the
                    # row's age. It costs no schema change: the notification
                    # id is already on the row.
                    _job_id=str(notification.id),
                )
                if job is None:
                    # ARQ returns None when a job with this id ALREADY
                    # exists. With the job id being the notification id,
                    # that can only mean this row was already enqueued — so
                    # the live job IS this notification's job and the row
                    # correctly stays PENDING. It is logged distinctly
                    # because "email_queued" would claim a fresh enqueue.
                    logger.info(
                        "email_already_queued",
                        notification_id=str(notification.id),
                        template=template_name,
                    )
                else:
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
                notification.error_kind = ERROR_KIND_ENQUEUE_ERROR
                await self.db.commit()
        else:
            logger.warning(
                "arq_queue_not_available",
                notification_id=str(notification.id),
            )
            notification.status = NotificationStatus.FAILED
            notification.error_message = "ARQ queue not available"
            notification.error_kind = ERROR_KIND_QUEUE_UNAVAILABLE
            await self.db.commit()

        return notification

    async def _accept_notification(
        self,
        tenant_id: UUID | None,
        to_email: str,
        template_name: str,
        context: dict[str, Any],
        user_id: int | None = None,
    ) -> Notification | None:
        """The one acceptance sequence every door into the email pipeline runs.

        The direct door (``send_email``) and the event door
        (``handle_notification_request_event``) differ only in how they obtain
        a session/context and what they do with the outcome; the ordered steps
        below — preference gate, rate-limit refusal with its FAILED audit row,
        create-and-enqueue — are defined exactly once so quiet hours, the limit
        policy and the queue-failure marking cannot drift between them.

        Args:
            tenant_id: Tenant ID
            to_email: Recipient email address
            template_name: Name of the email template
            context: Template variables
            user_id: Optional user ID

        Returns:
            The persisted Notification, or None when user preferences
            suppressed the send.

        Raises:
            RateLimitExceededError: after the FAILED outbox row is committed.
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
                return None

        # D1: the template name selects the rate-limit bucket — auth-critical
        # templates draw on their own, so a tenant that exhausted its general
        # email budget cannot block its own authentication mail.
        limit_reason = await self._rate_limited(tenant_id, to_email, template_name)
        if limit_reason is not None:
            from src.app.shared.exceptions import RateLimitExceededError

            logger.warning(
                "notification_rate_limit_exceeded",
                tenant_id=str(tenant_id),
                reason=limit_reason,
            )
            # Create FAILED outbox record so there is an audit trace.
            notification = Notification(
                tenant_id=tenant_id,
                user_id=user_id,
                template_name=template_name,
                to_email=to_email,
                subject=f"Rate limited: {template_name}",
                status=NotificationStatus.FAILED,
            )
            notification.error_message = f"Rate limit exceeded: {limit_reason}"
            # D4: the machine-readable class. The sentinel subject above
            # stays for humans (and for any operator grep), but the KIND is
            # what an operator filters on — the subject alone made refusals
            # indistinguishable from messages in history sorted by subject.
            notification.error_kind = ERROR_KIND_RATE_LIMITED
            self.db.add(notification)
            await self.db.commit()
            raise RateLimitExceededError(f"Rate limit exceeded: {limit_reason}")

        return await self._create_and_enqueue(tenant_id, to_email, template_name, context, user_id)

    async def send_email(
        self,
        tenant_id: UUID | None,
        to_email: str,
        template_name: str,
        context: dict[str, Any],
        user_id: int | None = None,
    ) -> Notification | None:
        """Send an email notification.

        Direct door into the pipeline. Delegates the whole acceptance sequence
        to :meth:`_accept_notification`, which the event door also runs.

        Rate-limit semantics (P2-7): when limited, a FAILED outbox row is
        written first so there is an audit trace, and only then is
        RateLimitExceededError raised.

        Args:
            tenant_id: Tenant ID
            to_email: Recipient email address
            template_name: Name of the email template
            context: Template variables
            user_id: Optional user ID

        Returns:
        -------
            Notification record, or None if skipped by user preferences
        """
        return await self._accept_notification(tenant_id, to_email, template_name, context, user_id)

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
            # D4: the machine-readable class for a delivery failure. The
            # free-text message above stays for the operator; the kind is
            # what a filter or a counter groups on.
            notification.error_kind = ERROR_KIND_SEND_ERROR
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
        # P2-2: tenant scoping via the execute_scoped seam.
        query = select(Notification, total_col)

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

        result = await execute_scoped(
            query,
            db=self.db,
            model=Notification,
            scope=ExplicitTenant(tenant_id, reason="legacy-criterion:Notification"),
        )
        rows = result.unique().all()
        if rows:
            total = int(rows[0][1])
            notifications = [row[0] for row in rows]
            return total, notifications

        if offset == 0:
            return 0, []

        # Out-of-range page fallback
        count_query = select(func.count()).select_from(Notification)
        if user_id:
            count_query = count_query.where(Notification.user_id == user_id)
        if enum_status is not None:
            count_query = count_query.where(Notification.status == enum_status)
        if start_date:
            count_query = count_query.where(Notification.created_at >= start_date)
        if end_date:
            count_query = count_query.where(Notification.created_at <= end_date)
        total_result = await execute_scoped(
            count_query,
            db=self.db,
            model=Notification,
            scope=ExplicitTenant(tenant_id, reason="legacy-criterion:Notification"),
        )
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
        )
        if user_id is not None:
            query = query.where(Notification.user_id == user_id)
        result = await execute_scoped(
            query,
            db=self.db,
            model=Notification,
            scope=ExplicitTenant(tenant_id, reason="legacy-criterion:Notification"),
        )
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
            .limit(limit)
            .offset(offset)
        )
        result = await execute_scoped(
            query,
            db=self.db,
            model=EmailTemplateOverride,
            scope=ExplicitTenant(tenant_id, reason="legacy-criterion:EmailTemplateOverride"),
        )
        return list(result.scalars().all())

    @staticmethod
    def _validate_override_content(template_name: str, content: str) -> None:
        """Compile-check a tenant template override BEFORE it is persisted.

        D2. The compile is the same one ``render_template`` performs, in the
        same sandboxed environment, so a template accepted here renders
        later. A syntax error is a 400 at the admin edge rather than a 500
        on every send of that template.

        The check is deliberately narrow: it proves the template COMPILES.
        It does not prove it renders — a template that references an
        undefined variable compiles fine and renders to an empty string at
        send time, which is a content mistake, not a broken template.

        Args:
            template_name: The template being overridden (for the message).
            content: The override's Jinja2 source.

        Raises:
            ValidationError: the content does not compile.
        """
        from jinja2 import TemplateSyntaxError

        from src.app.shared.exceptions import ValidationError

        try:
            jinja_env.from_string(content)
        except TemplateSyntaxError as exc:
            logger.warning(
                "template_override_syntax_error",
                template_name=template_name,
                line=exc.lineno,
                error=str(exc),
            )
            raise ValidationError(
                f"Template override for '{template_name}' is not valid Jinja2: "
                f"{exc.message} (line {exc.lineno})"
            ) from exc
        except Exception as exc:
            # A non-syntax failure (e.g. the sandbox refusing an operation
            # at compile time) is still a broken template. Refuse it here for
            # the same reason.
            logger.warning(
                "template_override_compile_error",
                template_name=template_name,
                error=str(exc),
            )
            raise ValidationError(
                f"Template override for '{template_name}' could not be compiled: {exc}"
            ) from exc

    async def create_template_override(
        self, tenant_id: UUID, template_name: str, subject_override: str | None, content: str
    ) -> EmailTemplateOverride:
        """Create or update an email template override.

        D2: the content is COMPILED before it is persisted. A tenant
        override with broken Jinja syntax used to be accepted here and then
        500 every send of that template until somebody noticed and deleted
        it — the loud-failure treatment the file templates already get
        (S-48) applied only to file templates, not to overrides. Rejecting
        at the admin edge turns a silent runtime failure into a 400 at the
        moment the mistake is made.

        The compile runs in the SANDBOXED environment, so the check is
        exactly the one ``render_template`` will perform later — a template
        that compiles here compiles there.
        """
        self._validate_override_content(template_name, content)

        query = select(EmailTemplateOverride).where(
            EmailTemplateOverride.template_name == template_name,
        )
        result = await execute_scoped(
            query,
            db=self.db,
            model=EmailTemplateOverride,
            scope=ExplicitTenant(tenant_id, reason="legacy-criterion:EmailTemplateOverride"),
        )
        existing: EmailTemplateOverride | None = result.scalar_one_or_none()

        if existing:
            # Update existing
            existing.subject_override = subject_override
            existing.content = content
            await self.db.commit()
            await self.db.refresh(existing)

            await invalidate_template_cache(tenant_id, template_name)

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

        await invalidate_template_cache(tenant_id, template_name)

        return override

    async def delete_template_override(self, tenant_id: UUID, template_name: str) -> bool:
        """Delete an email template override returns True if deleted, False if not found"""
        query = select(EmailTemplateOverride).where(
            EmailTemplateOverride.template_name == template_name,
        )
        result = await execute_scoped(
            query,
            db=self.db,
            model=EmailTemplateOverride,
            scope=ExplicitTenant(tenant_id, reason="legacy-criterion:EmailTemplateOverride"),
        )
        override = result.scalar_one_or_none()

        if not override:
            return False

        await self.db.delete(override)
        await self.db.commit()

        await invalidate_template_cache(tenant_id, template_name)

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

    Retry semantics: transmission failures inside a single run go through
    with_retry (3 attempts, 1s base delay). If that budget is exhausted,
    the job re-queues itself via arq.worker.Retry following
    EMAIL_RETRY_DELAYS, up to settings.arq.ARQ_MAX_TRIES worker-level tries.
    The notification row stays PENDING across worker retries and is only
    marked FAILED on the final permitted attempt.

    Args:
        ctx: ARQ context (carries job_try)
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
                #
                # S-05 (audit finding #5): this was the ONE `with_retry` call in
                # the codebase that did NOT override `exceptions`, so it
                # inherited the `(Exception,)` default and retried EVERY failure
                # - including TypeError / ValueError / AttributeError, which no
                # retry count can fix. It is also the longest ladder in the tree
                # (1.0 + 2.0 = 3 s of sleeping), so a bug in the SMTP provider
                # cost 3 seconds and three attempts before surfacing.
                async def _send_with_check() -> bool:
                    result = await provider.send_email(message)
                    if not result:
                        # F8: the message is NOT named here. This text is
                        # what `monitor_job` writes to the DLQ on the final
                        # failure, and the DLQ is an operator-facing surface:
                        # the recipient address would be persisted there (PII
                        # in a store that outlives the notification). The
                        # notification id is in scope of the caller and
                        # identifies the row without naming the person.
                        raise RuntimeError(f"EmailProvider failed to send notification {notification_id}")
                    return bool(result)

                await with_retry(
                    fn=_send_with_check,
                    max_attempts=3,
                    delay_seconds=1.0,
                    backoff_factor=2.0,
                    # Transport-level failures only. Never retry a TypeError /
                    # ValueError / AttributeError - those are bugs, not blips.
                    exceptions=(RuntimeError, OSError, ConnectionError, TimeoutError),
                )

                # Mark as sent (inside context = RLS enforced)
                await service.mark_sent(UUID(notification_id))
                logger.info("email_sent_success", notification_id=notification_id)

            except Exception as e:
                # ARQ contract (see core/events/worker.py K3 precedent): a bare
                # raise sets finish=True and DELETES the job — zero retries.
                # Re-queueing requires arq.worker.Retry. FAILED is terminal, so
                # it is only marked on the final permitted attempt.
                from arq.worker import Retry

                from src.app.core.config import settings

                job_try: int = ctx.get("job_try", 1)
                max_tries = settings.arq.ARQ_MAX_TRIES

                if job_try >= max_tries:
                    logger.error(
                        "email_send_failed",
                        notification_id=notification_id,
                        error=str(e),
                        job_try=job_try,
                        max_tries=max_tries,
                        exc_info=True,
                    )
                    # Terminal: mark FAILED (inside context = RLS enforced)
                    await service.mark_failed(UUID(notification_id), str(e))
                    raise

                # Retries remain: defer to the worker with backoff. The row
                # stays PENDING until the final attempt decides the outcome.
                delay = EMAIL_RETRY_DELAYS[min(job_try - 1, len(EMAIL_RETRY_DELAYS) - 1)]
                logger.warning(
                    "email_send_retry_scheduled",
                    notification_id=notification_id,
                    error=str(e),
                    job_try=job_try,
                    max_tries=max_tries,
                    retry_in_seconds=delay,
                )
                raise Retry(defer=delay) from e

        try:
            if tenant_id:
                async with rls_context(db, tenant_id):
                    await _run_with_context()
            else:
                await _run_with_context()
        except Exception:
            raise  # Already logged inside _run_with_context


async def handle_notification_request_event(data: dict[str, Any]) -> None:
    """Handle request to generate and send an email notification via outbox.

    Thin event door (P2-7): parses the payload, opens the RLS-scoped session,
    and delegates the whole acceptance sequence to
    ``NotificationService._accept_notification`` — the same one the direct
    ``send_email`` door runs — discarding its return value.

    Args:
        data: Event payload with request details:
            - to_email: Recipient email address
            - template_name: Name of the template
            - context: Template context variables
            - tenant_id: Optional tenant ID
            - user_id: Optional user ID
    """
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

    # S-40: needed by the handler below; fn-local like the sibling importer.
    from src.app.shared.exceptions import RateLimitExceededError

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

                # The one acceptance sequence, shared with the direct door
                # (send_email). Its return value is deliberately discarded
                # here: this door's contract is "accepted or not", and the
                # sequence already logs the skip and the rate-limit refusal.
                await service._accept_notification(tenant_id, to_email, template_name, context, user_id)

    except RateLimitExceededError:
        # S-40: a rate-limit refusal is PERMANENT, not transient. Re-raising it
        # made ARQ retry, and each attempt consumed two more quota windows and
        # wrote another FAILED outbox row - burning ARQ_MAX_TRIES on an outcome
        # that can never change.
        #
        # `_accept_notification` has already logged the skip and committed the
        # FAILED row, so the correct terminal behaviour is to ACKNOWLEDGE and
        # stop. Transient faults still propagate below so ARQ can retry them.
        logger.info(
            "notification_request_rate_limited_not_retried",
            tenant_id=str(tenant_id) if tenant_id else None,
        )
        return

    except Exception as e:
        logger.error("failed_to_process_notification_request", error=str(e), exc_info=True)
        raise


async def _notification_job_is_live(notification_id: UUID) -> bool:
    """Whether the ARQ job for this notification still exists.

    D3. The job id IS the notification id (``_create_and_enqueue`` enqueues
    with ``_job_id=str(notification.id)``), so liveness is a single Redis
    key lookup and no schema change is needed.

    ARQ writes ``arq:job:{job_id}`` when the job is enqueued and deletes it
    when the job finishes or terminally fails, so:
      * key present  -> the job is queued or deferred; the worker still owns
        it, and it MUST NOT be swept.
      * key absent   -> the job is gone; the enqueue is provably lost.

    If Redis cannot answer, this returns False ("not live"), which
    degrades the sweep to its age-only behaviour. That is the conservative
    choice for a bounded-terminal-state sweep, and the caller logs the
    degradation.
    """
    from arq.constants import job_key_prefix

    from src.app.core.utils.queue import pool as arq_pool

    if arq_pool is None:
        # No queue client: the worker never ran, so nothing can be live.
        return False
    try:
        return bool(await arq_pool.exists(job_key_prefix + str(notification_id)))
    except Exception as exc:
        logger.warning(
            "notification_job_liveness_check_failed",
            notification_id=str(notification_id),
            error=str(exc),
        )
        return False


async def recover_stuck_notifications(ctx: dict[str, Any]) -> int:
    """ARQ periodic job: sweep stuck PENDING notifications to terminal FAILED (P2-6).

    Outbox terminal semantics, mirroring the webhooks recovery lifecycle
    (recover_stuck_deliveries) without replicating its table shape: a
    PENDING row whose enqueue was lost (or whose worker retries all
    exhausted without a final FAILED marking) must not stay PENDING
    forever.

    D3: the sweep only touches a row whose enqueue is PROVABLY LOST. The
    age alone is not proof: during a long worker outage, rows legitimately
    deferred through worker retries are older than the threshold too, and
    sweeping them destroys deliverable mail that the worker would still
    have sent. So the sweep asks Redis whether the row's ARQ job still
    exists (the job id IS the notification id — see ``_create_and_enqueue``)
    and leaves a row alone while its job is live.

    A row whose job is gone is provably lost: ARQ deletes the job key when
    the job finishes or terminally fails, and the key expires on its own if
    the worker never picks it up.

    When Redis cannot answer (the queue is down), the sweep degrades to the
    AGE-ONLY behaviour it had before: an old row is swept. That is the
    conservative choice — a stuck PENDING row is a bounded terminal state
    by design, and the alternative (never sweeping) would leak rows
    forever. The degradation is logged, never silent.

    Re-enqueueing on recovery is deliberately NOT attempted: the rendered
    html body is not persisted on the Notification row, so a re-enqueue
    cannot reconstruct the original message. If recovery-retry is ever
    needed, add a body column first.

    Args:
        ctx: ARQ worker context

    Returns:
        Number of notifications swept to FAILED.
    """
    recovered = 0
    cutoff_time = datetime.now(UTC) - timedelta(hours=NOTIFICATION_MAX_PENDING_AGE_HOURS)

    from src.app.core.db.database import get_db_context
    from src.app.core.db.rls import bypass_rls

    async with get_db_context() as db:
        async with bypass_rls(db, reason="Notification recovery sweep job"):
            stmt = (
                select(Notification)
                .where(
                    Notification.status == NotificationStatus.PENDING,
                    Notification.created_at < cutoff_time,
                )
                .limit(NOTIFICATION_RECOVERY_BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )

            result = await db.execute(stmt)
            notifications = result.scalars().all()
            for notification in notifications:
                # D3: only sweep a row whose enqueue is provably lost. A row
                # whose ARQ job still exists is queued or deferred — the
                # worker will still deliver it, so sweeping it would destroy
                # deliverable mail.
                if await _notification_job_is_live(notification.id):
                    logger.info(
                        "notification_recovery_skipped_job_still_live",
                        notification_id=str(notification.id),
                    )
                    continue
                notification.status = NotificationStatus.FAILED
                notification.error_message = "Recovery window exhausted"
                notification.error_kind = ERROR_KIND_RECOVERY_EXHAUSTED
                recovered += 1

            if recovered > 0:
                await db.commit()

    if recovered > 0:
        logger.info("Recovered stuck notifications (swept to FAILED)", count=recovered)

    return recovered
