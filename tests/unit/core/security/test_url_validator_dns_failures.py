"""Resolver failures are validation failures, not server errors.

``validate_webhook_url`` used to catch only ``socket.gaierror``. ``socket.timeout``
(an alias of the builtin ``TimeoutError`` since 3.10) and other ``OSError``
subclasses — ``EAI_AGAIN`` from a temporarily overloaded resolver, for instance —
escaped the seam, so ``validate_url_ssrf`` could not translate them and creating
or updating a webhook answered **500** instead of a 4xx validation error.

A 500 is the wrong answer twice over: it is not the caller's fault, and it invites
the client to retry a request that will keep failing. Every resolver failure is a
validation failure for the URL being checked.

``socket.gaierror`` is itself an ``OSError`` subclass, so the narrower clause was
also the *only* clause — these tests pin that the whole family is covered while
the original NXDOMAIN behaviour is preserved.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from src.app.core.security.url_validator import SSRFProtectionError, validate_webhook_url
from src.app.shared.exceptions import ValidationError

_GETADDRINFO = "src.app.core.security.url_validator.socket.getaddrinfo"
_URL = "https://hooks.example.com/webhook"


def _resolve_raising(error: BaseException):
    """A ``getaddrinfo`` stand-in that raises ``error``."""

    def _raise(*args: object, **kwargs: object) -> object:
        raise error

    return _raise


class TestResolverFailuresAreDomainErrors:
    async def test_nxdomain_still_raises_ssrf_error(self) -> None:
        """Pre-existing behaviour: a name with no address is a validation error."""
        with (
            patch(_GETADDRINFO, _resolve_raising(socket.gaierror("Name or service not known"))),
            pytest.raises(SSRFProtectionError),
        ):
            await validate_webhook_url(_URL)

    async def test_a_resolver_timeout_raises_ssrf_error(self) -> None:
        """``socket.timeout`` is ``TimeoutError``; it used to escape as a 500."""
        with (
            patch(_GETADDRINFO, _resolve_raising(TimeoutError("timed out"))),
            pytest.raises(SSRFProtectionError),
        ):
            await validate_webhook_url(_URL)

    async def test_a_builtin_timeout_raises_ssrf_error(self) -> None:
        with (
            patch(_GETADDRINFO, _resolve_raising(TimeoutError("timed out"))),
            pytest.raises(SSRFProtectionError),
        ):
            await validate_webhook_url(_URL)

    async def test_a_transient_resolver_failure_raises_ssrf_error(self) -> None:
        """``EAI_AGAIN`` — the resolver was temporarily unavailable."""
        error = socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
        with (
            patch(_GETADDRINFO, _resolve_raising(error)),
            pytest.raises(SSRFProtectionError),
        ):
            await validate_webhook_url(_URL)

    async def test_a_generic_oserror_raises_ssrf_error(self) -> None:
        with (
            patch(_GETADDRINFO, _resolve_raising(OSError("network is unreachable"))),
            pytest.raises(SSRFProtectionError),
        ):
            await validate_webhook_url(_URL)

    async def test_the_original_error_is_chained(self) -> None:
        original = OSError("network is unreachable")
        with (
            patch(_GETADDRINFO, _resolve_raising(original)),
            pytest.raises(SSRFProtectionError) as raised,
        ):
            await validate_webhook_url(_URL)

        assert raised.value.__cause__ is original

    async def test_the_hostname_is_named_in_the_error(self) -> None:
        with (
            patch(_GETADDRINFO, _resolve_raising(OSError("boom"))),
            pytest.raises(SSRFProtectionError) as raised,
        ):
            await validate_webhook_url(_URL)

        assert "hooks.example.com" in str(raised.value)

    async def test_cancellation_is_not_swallowed(self) -> None:
        """``CancelledError`` is a ``BaseException``; it must propagate untouched."""
        import asyncio

        with (
            patch(_GETADDRINFO, _resolve_raising(asyncio.CancelledError())),
            pytest.raises(asyncio.CancelledError),
        ):
            await validate_webhook_url(_URL)


class TestWebhookTranslation:
    async def test_the_webhook_seam_maps_it_to_a_4xx_validation_error(self) -> None:
        """End to end: a resolver failure is a 400, not a 500."""
        from src.app.modules.webhooks.utils import validate_url_ssrf

        with (
            patch(_GETADDRINFO, _resolve_raising(OSError("resolver down"))),
            pytest.raises(ValidationError) as raised,
        ):
            await validate_url_ssrf(_URL)

        assert raised.value.resolve_http_status() == 400
