"""Regression tests for Cluster D (Notifications) fixes.

Each test pins behaviour that WAS BROKEN before the fix and MUST stay fixed.

D1 — auth-critical templates draw on a SEPARATE email rate-limit bucket, so
      a tenant that exhausted its general budget cannot block its own
      password-reset / magic-link / email-verification mail.
D2 — a tenant template override is COMPILE-CHECKED before it is persisted, so
      broken Jinja is rejected at the admin edge instead of 500-ing every send.
D3 — the stuck-PENDING sweep only touches a row whose enqueue is PROVABLY
      lost; a row whose ARQ job is still live is left alone.
D4 — a failed notification carries a machine-readable ``error_kind``.

D5 was measured FALSE by the review (asyncio.gather without
return_exceptions=True does not orphan the sibling or leak the budget), so
there is nothing to pin — a test for correct-by-measurement code would only
restate the implementation.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.notifications.models import NotificationStatus
from src.app.modules.notifications.service import (
    AUTH_CRITICAL_TEMPLATES,
    ERROR_KIND_ENQUEUE_ERROR,
    ERROR_KIND_QUEUE_UNAVAILABLE,
    ERROR_KIND_RATE_LIMITED,
    ERROR_KIND_RECOVERY_EXHAUSTED,
    ERROR_KIND_SEND_ERROR,
    NotificationService,
    recover_stuck_notifications,
)
from src.app.shared.exceptions import RateLimitExceededError, ValidationError

# =============================================================================
# D1 — auth-critical mail gets its own rate-limit bucket
# =============================================================================


class TestAuthCriticalUsesItsOwnRateLimitBucket:
    def test_the_rate_check_knows_the_template(self):
        """The carve-out is impossible without the template name."""
        params = inspect.signature(NotificationService._rate_limited).parameters
        assert "template_name" in params, (
            "D1 regression: _rate_limited cannot tell an auth-critical template "
            "from a general one, so the carve-out cannot exist"
        )

    @pytest.mark.asyncio
    async def test_auth_and_general_templates_use_different_keys(self):
        """The two buckets must not share a Redis key."""
        service = NotificationService(db=AsyncMock())
        tenant = uuid4()

        seen: list[str] = []

        async def _consume(key, limit, period, **kwargs):
            seen.append(key)
            return (False, limit, 0, False)

        with patch("src.app.modules.notifications.service.consume", side_effect=_consume):
            await service._rate_limited(tenant, "a@b.c", "password_reset")
            await service._rate_limited(tenant, "a@b.c", "welcome")

        assert len(seen) == 4, "two templates x two windows"
        auth_keys = {k for k in seen if "auth" in k}
        general_keys = {k for k in seen if "general" in k}
        assert auth_keys, f"D1 regression: no auth bucket key in {seen}"
        assert general_keys, f"D1 regression: no general bucket key in {seen}"
        assert not (auth_keys & general_keys), (
            f"D1 regression: auth and general sends share a bucket: {seen}"
        )

    @pytest.mark.asyncio
    async def test_every_auth_critical_template_uses_the_auth_bucket(self):
        """The carve-out must cover the WHOLE auth-critical set, not one template."""
        service = NotificationService(db=AsyncMock())
        tenant = uuid4()

        for template in AUTH_CRITICAL_TEMPLATES:
            seen: list[str] = []

            async def _consume(key, limit, period, _seen=seen, **kwargs):
                _seen.append(key)
                return (False, limit, 0, False)

            with patch(
                "src.app.modules.notifications.service.consume",
                new=AsyncMock(side_effect=_consume),
            ):
                await service._rate_limited(tenant, "a@b.c", template)

            assert seen, f"D1 regression: {template} consumed no bucket"
            assert all(":auth:" in k for k in seen), (
                f"D1 regression: {template} did not use the auth bucket: {seen}"
            )

    @pytest.mark.asyncio
    async def test_a_general_send_cannot_consume_the_auth_budget(self):
        """The behavioural core of D1.

        Exhausting the general bucket must leave the auth bucket untouched,
        so the tenant's own authentication mail still goes out.
        """
        service = NotificationService(db=AsyncMock())
        tenant = uuid4()
        counts: dict[str, int] = {}

        async def _consume(key, limit, period, **kwargs):
            counts[key] = counts.get(key, 0) + 1
            # The general bucket is exhausted; the auth bucket is not.
            return (":general:" in key, limit, 0, False)

        with patch("src.app.modules.notifications.service.consume", side_effect=_consume):
            general = await service._rate_limited(tenant, "a@b.c", "welcome")
            auth = await service._rate_limited(tenant, "a@b.c", "password_reset")

        assert general is not None, "the general bucket should have refused"
        assert auth is None, (
            "D1 regression: a general send exhausted the bucket that auth mail "
            "draws on, so the tenant blocked its own authentication mail"
        )

    @pytest.mark.asyncio
    async def test_an_auth_send_cannot_consume_the_general_budget(self):
        """The carve-out is symmetric: auth mail must not starve admin sends."""
        service = NotificationService(db=AsyncMock())
        tenant = uuid4()

        async def _consume(key, limit, period, **kwargs):
            return (":auth:" in key, limit, 0, False)

        with patch("src.app.modules.notifications.service.consume", side_effect=_consume):
            auth = await service._rate_limited(tenant, "a@b.c", "magic_link")
            general = await service._rate_limited(tenant, "a@b.c", "welcome")

        assert auth is not None, "the auth bucket should have refused"
        assert general is None, (
            "D1 regression: an auth send consumed the general bucket's budget"
        )

    @pytest.mark.asyncio
    async def test_the_carve_out_is_not_a_bypass(self):
        """Auth mail must still be BOUNDED — it has its own budget, not none.

        An unbounded auth path is an open mail-bomb. The auth bucket's
        refusal must still be reported.
        """
        service = NotificationService(db=AsyncMock())
        tenant = uuid4()

        async def _consume(key, limit, period, **kwargs):
            return (True, 0, 0, False)

        with patch("src.app.modules.notifications.service.consume", side_effect=_consume):
            reason = await service._rate_limited(tenant, "a@b.c", "password_reset")

        assert reason is not None, (
            "D1 regression: auth-critical mail is no longer rate-limited at all"
        )

    @pytest.mark.asyncio
    async def test_the_acceptance_sequence_passes_the_template_through(self):
        """The bucket selection must be wired, not merely available."""
        service = NotificationService(db=AsyncMock())
        tenant = uuid4()

        with patch.object(
            NotificationService, "_rate_limited", new_callable=AsyncMock
        ) as mock_rate:
            mock_rate.return_value = None
            with patch.object(
                NotificationService, "_create_and_enqueue", new_callable=AsyncMock
            ):
                await service._accept_notification(tenant, "a@b.c", "magic_link", {})

        assert mock_rate.await_args is not None
        assert mock_rate.await_args.args[2] == "magic_link", (
            "D1 regression: the template name is not reaching the rate check"
        )


# =============================================================================
# D2 — template overrides are compile-checked at write time
# =============================================================================


class TestTemplateOverrideIsCompileChecked:
    @pytest.mark.asyncio
    async def test_broken_jinja_is_rejected(self):
        """The headline of D2: broken syntax never reaches the database."""
        service = NotificationService(db=AsyncMock())
        db = service.db
        db.add = MagicMock()
        db.commit = AsyncMock()

        with pytest.raises(ValidationError):
            await service.create_template_override(
                tenant_id=uuid4(),
                template_name="welcome",
                subject_override="Hi",
                content="{% if %} broken",
            )

        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_jinja_is_accepted(self):
        """The check must not reject good templates."""
        service = NotificationService(db=AsyncMock())
        db = service.db
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)

        override = await service.create_template_override(
            tenant_id=uuid4(),
            template_name="welcome",
            subject_override="Hi",
            content="<p>Hello {{ username }}</p>",
        )

        assert override is not None
        db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_an_undefined_variable_is_not_a_write_time_failure(self):
        """The check proves COMPILATION, not rendering.

        A template that references a variable the caller forgot compiles
        fine and renders to an empty string at send time. That is a
        content mistake, not a broken template, and rejecting it here
        would be a false rejection.
        """
        # No exception: the content compiles.
        NotificationService._validate_override_content(
            "welcome", "<p>Hello {{ never_provided }}</p>"
        )

    def test_the_check_uses_the_sandboxed_environment(self):
        """The check must be the SAME compile the render performs."""
        from src.app.modules.notifications import service as notification_service

        sentinel = MagicMock()
        with patch.object(notification_service, "jinja_env", sentinel):
            NotificationService._validate_override_content("welcome", "<p>Hello</p>")

        sentinel.from_string.assert_called_once_with("<p>Hello</p>")
        assert notification_service.jinja_env.__class__.__name__ == "SandboxedEnvironment"


# =============================================================================
# D3 — the sweep only touches provably-lost enqueues
# =============================================================================


class TestRecoverySweepProvesTheEnqueueIsLost:
    @pytest.mark.asyncio
    async def test_a_live_job_is_not_swept(self):
        """The headline of D3: a deferred-but-live job keeps its mail."""
        stuck = MagicMock()
        stuck.id = uuid4()
        stuck.status = NotificationStatus.PENDING

        db = AsyncMock()
        db.commit = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [stuck]
        db.execute = AsyncMock(return_value=result)

        db_ctx = AsyncMock()
        db_ctx.__aenter__.return_value = db
        bypass = AsyncMock()
        bypass.__aenter__.return_value = db

        with (
            patch("src.app.core.db.database.get_db_context", return_value=db_ctx),
            patch("src.app.core.db.rls.bypass_rls", return_value=bypass),
            patch(
                "src.app.modules.notifications.service._notification_job_is_live",
                new=AsyncMock(return_value=True),
            ),
        ):
            recovered = await recover_stuck_notifications({})

        assert recovered == 0, (
            "D1/D3 regression: the sweep destroyed a row whose ARQ job was still live"
        )
        assert stuck.status == NotificationStatus.PENDING
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_lost_job_is_swept(self):
        """A row whose job is gone is provably lost and IS swept."""
        stuck = MagicMock()
        stuck.id = uuid4()
        stuck.status = NotificationStatus.PENDING

        db = AsyncMock()
        db.commit = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [stuck]
        db.execute = AsyncMock(return_value=result)

        db_ctx = AsyncMock()
        db_ctx.__aenter__.return_value = db
        bypass = AsyncMock()
        bypass.__aenter__.return_value = db

        with (
            patch("src.app.core.db.database.get_db_context", return_value=db_ctx),
            patch("src.app.core.db.rls.bypass_rls", return_value=bypass),
            patch(
                "src.app.modules.notifications.service._notification_job_is_live",
                new=AsyncMock(return_value=False),
            ),
        ):
            recovered = await recover_stuck_notifications({})

        assert recovered == 1
        assert stuck.status == NotificationStatus.FAILED
        assert stuck.error_kind == ERROR_KIND_RECOVERY_EXHAUSTED
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_enqueue_uses_the_notification_id_as_the_job_id(self):
        """Liveness is provable only if the job id is derivable from the row.

        The job id IS the notification id, so the sweep needs no schema
        change and no new column.
        """
        from src.app.modules.notifications import service as notification_service

        notification = MagicMock()
        notification.id = uuid4()
        db = AsyncMock()
        db.add = MagicMock()
        db.refresh = AsyncMock()
        pool = AsyncMock()
        pool.enqueue_job = AsyncMock(return_value=MagicMock())

        service = NotificationService(db)
        with (
            patch.object(
                NotificationService,
                "render_template",
                AsyncMock(return_value=("Subject", "<p>Body</p>")),
            ),
            patch.object(notification_service, "Notification", return_value=notification),
            patch("src.app.core.utils.queue.pool", pool),
        ):
            await service._create_and_enqueue(None, "to@example.com", "welcome", {})

        assert pool.enqueue_job.await_args.kwargs["_job_id"] == str(notification.id), (
            "D3 regression: the enqueue does not use the notification id as the "
            "ARQ job id, so liveness cannot be proven from the row"
        )

    @pytest.mark.asyncio
    async def test_liveness_is_a_single_job_key_lookup(self):
        """The liveness check must ask Redis about the ARQ job key."""
        from src.app.modules.notifications.service import _notification_job_is_live

        notification_id = uuid4()
        pool = AsyncMock()
        pool.exists = AsyncMock(return_value=True)

        with patch("src.app.core.utils.queue.pool", pool):
            live = await _notification_job_is_live(notification_id)

        assert live is True
        pool.exists.assert_awaited_once()
        key = pool.exists.await_args.args[0]
        assert str(notification_id) in key
        assert key.startswith("arq:job:")

    @pytest.mark.asyncio
    async def test_a_redis_outage_degrades_to_age_only_sweeping(self):
        """The sweep must still reach a terminal state when Redis is down.

        The alternative (never sweeping) leaks PENDING rows forever, which
        the outbox's bounded-terminal-state contract forbids.
        """
        from src.app.modules.notifications.service import _notification_job_is_live

        pool = AsyncMock()
        pool.exists = AsyncMock(side_effect=RuntimeError("redis down"))

        with patch("src.app.core.utils.queue.pool", pool):
            live = await _notification_job_is_live(uuid4())

        assert live is False, (
            "D3 regression: a Redis outage must degrade to age-only sweeping, "
            "not freeze the sweep forever"
        )

    @pytest.mark.asyncio
    async def test_no_queue_client_means_nothing_is_live(self):
        """With no worker pool there is no job, so nothing can be live."""
        from src.app.modules.notifications.service import _notification_job_is_live

        with patch("src.app.core.utils.queue.pool", None):
            live = await _notification_job_is_live(uuid4())

        assert live is False


# =============================================================================
# D4 — a failed notification carries a machine-readable kind
# =============================================================================


class TestFailedNotificationsCarryAnErrorKind:
    def test_the_column_exists(self):
        from src.app.modules.notifications.models import Notification

        assert "error_kind" in Notification.__table__.columns

    @pytest.mark.asyncio
    async def test_the_rate_refusal_row_carries_the_rate_limited_kind(self):
        """The row an operator filters on must say WHY it failed."""
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        service = NotificationService(db=db)

        async def _consume(key, limit, period, **kwargs):
            return (True, 0, 0, False)

        with (
            patch("src.app.modules.notifications.service.consume", side_effect=_consume),
            pytest.raises(RateLimitExceededError),
        ):
            await service._accept_notification(uuid4(), "a@b.c", "welcome", {})

        added = db.add.call_args.args[0]
        assert added.status == NotificationStatus.FAILED
        assert added.error_kind == ERROR_KIND_RATE_LIMITED

    @pytest.mark.asyncio
    async def test_mark_failed_carries_the_send_error_kind(self):
        db = AsyncMock()
        db.commit = AsyncMock()
        notification = MagicMock()
        db.get = AsyncMock(return_value=notification)
        service = NotificationService(db=db)

        await service.mark_failed(uuid4(), "connection refused")

        assert notification.status == NotificationStatus.FAILED
        assert notification.error_kind == ERROR_KIND_SEND_ERROR

    @pytest.mark.asyncio
    async def test_the_enqueue_failure_carries_its_own_kind(self):
        """Each failure cause gets its OWN kind, so a filter is meaningful."""
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        service = NotificationService(db=db)

        pool = AsyncMock()
        pool.enqueue_job = AsyncMock(side_effect=RuntimeError("redis gone"))

        with (
            patch("src.app.core.utils.queue.pool", pool),
            patch.object(
                NotificationService, "render_template", new=AsyncMock(return_value=("s", "<p>x</p>"))
            ),
        ):
            notification = await service._create_and_enqueue(
                uuid4(), "a@b.c", "welcome", {}
            )

        assert notification.status == NotificationStatus.FAILED
        assert notification.error_kind == ERROR_KIND_ENQUEUE_ERROR

    @pytest.mark.asyncio
    async def test_a_missing_queue_carries_the_queue_unavailable_kind(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        service = NotificationService(db=db)

        with (
            patch("src.app.core.utils.queue.pool", None),
            patch.object(
                NotificationService, "render_template", new=AsyncMock(return_value=("s", "<p>x</p>"))
            ),
        ):
            notification = await service._create_and_enqueue(
                uuid4(), "a@b.c", "welcome", {}
            )

        assert notification.status == NotificationStatus.FAILED
        assert notification.error_kind == ERROR_KIND_QUEUE_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_an_already_queued_job_leaves_the_row_pending(self):
        """ARQ returns None when a job with that id already exists.

        With the job id being the notification id, that means this row was
        already enqueued — so the live job IS this notification's job and
        the row must stay PENDING, not be marked FAILED.
        """
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        service = NotificationService(db=db)

        pool = AsyncMock()
        # ARQ's contract: None means "a job with this id already exists".
        pool.enqueue_job = AsyncMock(return_value=None)

        with (
            patch("src.app.core.utils.queue.pool", pool),
            patch.object(
                NotificationService, "render_template", new=AsyncMock(return_value=("s", "<p>x</p>"))
            ),
        ):
            notification = await service._create_and_enqueue(
                uuid4(), "a@b.c", "welcome", {}
            )

        assert notification.status == NotificationStatus.PENDING, (
            "D3 regression: a duplicate enqueue marked the row FAILED — but the "
            "live job IS this row's job, so it is still deliverable"
        )

    def test_the_kinds_are_distinct(self):
        """A filter is only meaningful if the values do not collide."""
        kinds = {
            ERROR_KIND_RATE_LIMITED,
            ERROR_KIND_ENQUEUE_ERROR,
            ERROR_KIND_QUEUE_UNAVAILABLE,
            ERROR_KIND_RECOVERY_EXHAUSTED,
            ERROR_KIND_SEND_ERROR,
        }
        assert len(kinds) == 5

    def test_the_api_exposes_the_kind(self):
        """Operators read it through the API, so the schema must carry it."""
        from src.app.modules.notifications.schemas import NotificationRead

        assert "error_kind" in NotificationRead.model_fields
