"""FastAPI_saas-io15.4: the deliberate blind-except boundaries, as behaviour.

Triage is only worth something if the boundaries it keeps still behave the way
the reason claims. A reasoned ``noqa`` stops a reviewer asking *why*; these
tests answer *what actually happens*, so the reason cannot rot into a fiction
that the code no longer implements.

Two of the three muse-22 sites are deliberate boundaries and are pinned here:

* ``api/deps/auth.py`` — a PRESENTED bearer token that fails for any reason
  must be rejected loudly (C3), never served as the anonymous view. An
  unanticipated fault must not escape as a 500 either.
* ``clients/notifications/smtp.py`` — a failed email must not fail the
  request; it is recorded on the circuit breaker and reported as ``False``.

The third, ``middleware/tenant.py``, was NARROWED rather than kept, and is
covered in ``test_broad_except_narrowing.py`` alongside the tenant-resolution
rejection it protects.

These are behavioural: each one drives the real code and asserts the designed
outcome, rather than asserting that "nothing was raised" — which is also what
a handler that swallowed everything and did nothing at all would produce.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest


def _request():
    request = MagicMock()
    request.headers = {}
    request.method = "GET"
    request.url.path = "/"
    request.app = MagicMock()
    request.app.dependency_overrides = {}
    request.state = MagicMock()
    request.state.is_token_blacklisted = False
    return request


def _user(tenant_id: int = 5):
    return {
        "id": 7,
        "username": "u",
        "email": "u@example.com",
        "tenant_id": tenant_id,
        "is_active": True,
        "is_deleted": False,
        "is_superuser": False,
        "token_version": 0,
    }


def _bound_tenant(tenant_id: int = 5):
    from src.app.core.tenancy.context import TenantContext

    return TenantContext(
        tenant_id=UUID(int=tenant_id),
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=True,
    )
def _user(tenant_id: int = 5):
    return {
        "id": 7,
        "username": "u",
        "email": "u@example.com",
        "tenant_id": tenant_id,
        "is_active": True,
        "is_deleted": False,
        "is_superuser": False,
        "token_version": 0,
    }




class TestOptionalAuthDeliberateBoundary:
    """api/deps/auth.py: any verification fault becomes a denial, not a 500.

    The reason recorded at the site says the boundary exists so an
    unexpected fault cannot escape as a 500 AND cannot masquerade as "no
    credential". Both halves are asserted; asserting only the first would
    also pass for a handler that re-raised.
    """

    @pytest.mark.asyncio
    async def test_unexpected_fault_becomes_a_denial_not_a_500(self) -> None:
        """A fault we did not anticipate is logged, then denied."""
        from src.app.api.deps.auth import _resolve_optional_bearer_flow

        with (
            patch(
                "src.app.api.deps.auth._resolve_token_data",
                new=AsyncMock(return_value=(MagicMock(), "u@example.com", 0, "j1", None, "sid-1")),
            ),
            patch("src.app.api.deps.auth._load_and_validate_user", new=AsyncMock(return_value=_user())),
            patch(
                "src.app.api.deps.auth._enforce_session_valid",
                new=AsyncMock(side_effect=RuntimeError("session store exploded")),
            ),
        ):
            with pytest.raises(Exception) as exc:
                await _resolve_optional_bearer_flow(_request(), MagicMock(), "tok", None, _bound_tenant())

        # Denied as NO_CREDENTIAL — NOT the RuntimeError, and not a 500.
        assert type(exc.value) is not RuntimeError, (
            "the unanticipated fault escaped instead of being converted to a denial"
        )
        assert "not authenticated" in str(exc.value).lower(), (
            f"expected the C3 denial, got: {type(exc.value).__name__}: {exc.value}"
        )
        # And the original cause is preserved, so the log is not the only record.
        assert isinstance(exc.value.__cause__, RuntimeError), (
            "the original exception was not chained — the boundary swallowed the evidence"
        )

    @pytest.mark.asyncio
    async def test_the_fault_is_logged_not_silently_swallowed(self) -> None:
        """The claim "not lost" means logged; assert the call happened."""
        from src.app.api.deps.auth import _resolve_optional_bearer_flow

        with (
            patch("src.app.api.deps.auth.logger") as fake_logger,
            patch(
                "src.app.api.deps.auth._resolve_token_data",
                new=AsyncMock(return_value=(MagicMock(), "u@example.com", 0, "j1", None, "sid-1")),
            ),
            patch("src.app.api.deps.auth._load_and_validate_user", new=AsyncMock(return_value=_user())),
            patch(
                "src.app.api.deps.auth._enforce_session_valid",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            with pytest.raises(Exception):
                await _resolve_optional_bearer_flow(_request(), MagicMock(), "tok", None, _bound_tenant())

        assert fake_logger.warning.called, "the boundary swallowed the fault without recording it"
        assert "optional_auth_context_verify_failed" in str(fake_logger.warning.call_args)

    @pytest.mark.asyncio
    async def test_tenant_denial_still_wins_over_the_catch_all(self) -> None:
        """COUNTER-TEST: the catch-all must not eat the domain errors.

        The three domain errors are re-raised above the catch-all precisely
        so a tenant mismatch is not rewritten as NO_CREDENTIAL (P1-6). If the
        broad handler were ever hoisted above them, this is what would break.
        """
        from src.app.api.deps.auth import _resolve_optional_bearer_flow
        from src.app.core.tenancy.context import NO_TENANT

        with (
            patch(
                "src.app.api.deps.auth._resolve_token_data",
                new=AsyncMock(return_value=(MagicMock(), "u@example.com", 0, "j1", None, "sid-1")),
            ),
            patch("src.app.api.deps.auth._load_and_validate_user", new=AsyncMock(return_value=_user())),
            patch("src.app.api.deps.auth._enforce_session_valid", new=AsyncMock(return_value=None)),
            patch("src.app.api.deps.auth.get_current_tenant", return_value=NO_TENANT),
        ):
            with pytest.raises(Exception) as exc:
                await _resolve_optional_bearer_flow(_request(), MagicMock(), "tok", None, NO_TENANT)

        message = str(exc.value).lower()
        assert "not authenticated" not in message, (
            "the tenant denial was rewritten as anonymous — the catch-all ate it"
        )


class TestSmtpDeliberateBoundaries:
    """smtp.py: delivery failure is data, never an exception for the caller.

    ``send_email`` returns a bool and ``send_bulk_emails`` loops on it. If any
    of these handlers let a failure escape, one bad message would 500 an
    otherwise successful request and would abandon every message after it in a
    bulk send.
    """

    @staticmethod
    def _provider():
        from pydantic import SecretStr

        from src.app.clients.notifications import SMTPProvider

        return SMTPProvider(
            host="smtp.example.com",
            port=587,
            username="user",
            password=SecretStr("pass"),
            use_tls=True,
            from_email="noreply@example.com",
            from_name="App",
        )

    @staticmethod
    def _message():
        from src.app.clients.notifications import EmailMessage

        return EmailMessage(
            to_email="to@example.com",
            subject="s",
            html_content="<p>hi</p>",
            text_content="hi",
        )

    @pytest.mark.asyncio
    async def test_send_failure_returns_false_and_does_not_raise(self) -> None:
        from src.app.core.utils import circuit_breaker

        provider = self._provider()
        conn = MagicMock()
        conn.is_connected = True
        conn.send_message = AsyncMock(side_effect=RuntimeError("peer reset"))
        conn.quit = AsyncMock()

        with (
            patch.object(provider._pool, "acquire", new=AsyncMock(return_value=conn)),
            patch.object(provider._pool, "discard", new=AsyncMock()) as discard,
            patch.object(provider._pool, "release", new=AsyncMock()) as release,
            patch.object(circuit_breaker.email_circuit, "record_failure", new=AsyncMock()) as record_failure,
            patch.object(circuit_breaker.email_circuit, "record_success", new=AsyncMock()),
        ):
            result = await provider.send_email(self._message())

        assert result is False, "a failed send must be reported as False, not True"
        record_failure.assert_awaited_once()
        # A connection whose send failed has unknown state: it must not go
        # back into the idle pool, and must be closed.
        discard.assert_awaited_once_with(conn)
        release.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bulk_send_counts_failures_and_completes_every_message(self) -> None:
        """The request loop must survive one bad message and keep going."""
        provider = self._provider()
        send = AsyncMock(side_effect=[True, False, True])
        with patch.object(provider, "send_email", new=send):
            summary = await provider.send_bulk_emails([self._message() for _ in range(3)])

        assert summary == {"success": 2, "failed": 1}, f"bulk accounting is wrong: {summary}"
        assert send.await_count == 3, (
            f"the bulk loop abandoned messages after a failure: {send.await_count} of 3 attempted"
        )

    @pytest.mark.asyncio
    async def test_close_quietly_swallows_a_broken_close(self) -> None:
        """A teardown failure must not become the caller's failure."""
        from src.app.clients.notifications.smtp import _close_quietly

        conn = MagicMock()
        conn.is_connected = True
        conn.quit = AsyncMock(side_effect=RuntimeError("socket already gone"))

        await _close_quietly(conn)  # must return normally, not raise

    @pytest.mark.asyncio
    async def test_reset_provider_completes_even_when_pool_teardown_fails(self) -> None:
        """COUNTER-TEST: the reset is the contract; pool close is best-effort.

        Both globals are cleared BEFORE the guarded block, so the caller must
        get a clean reset even when teardown raises. Asserting the globals
        (not just "no exception") is the point: a handler that raised would
        leave the caller thinking the reset failed.
        """
        from src.app.clients.notifications import smtp as smtp_mod
        from src.app.clients.notifications.smtp import reset_email_provider

        class Exploding:
            @property
            def _pool(self):
                raise RuntimeError("pool introspection exploded")

        smtp_mod._default_smtp_provider = Exploding()
        smtp_mod._active_email_provider = object()
        try:
            reset_email_provider()
            assert smtp_mod._active_email_provider is None, "the active provider was not reset"
            assert smtp_mod._default_smtp_provider is None, "the default provider was not reset"
        finally:
            smtp_mod._active_email_provider = None
            smtp_mod._default_smtp_provider = None
