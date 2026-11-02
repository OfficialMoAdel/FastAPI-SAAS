"""A corrupt webhook signing secret must surface as a labelled domain error.

``WebhookEndpoint.signing_secret`` is always written through ``encrypt_data``, so
a decryption failure means the row (or the key material) is corrupt. The read
must fail — returning the ciphertext would leak the stored blob — but it must
fail as an ``AppException`` so ``core/errors.py`` translates it.

The raise used to be a bare ``ValueError``. That is not an ``AppException``, so
it bypassed the central domain-error table entirely: callers got an unlabelled
500 with no ``error.code`` to branch on, and operators got no structured
``log_message`` to alert on. Rotation-key mismatch is exactly the incident where
that difference matters.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.webhooks.exceptions import WebhookSecretUnavailableError
from src.app.modules.webhooks.service import WebhookService
from src.app.shared.error_codes import ErrorCode
from src.app.shared.exceptions import AppException

_ENDPOINT_ID = uuid4()
_TENANT_ID = uuid4()


def _endpoint(signing_secret: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=_ENDPOINT_ID,
        signing_secret=signing_secret,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _scoped_result(endpoint: SimpleNamespace | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = endpoint
    return result


class TestExceptionContract:
    def test_it_is_a_domain_exception(self) -> None:
        assert issubclass(WebhookSecretUnavailableError, AppException)

    def test_it_maps_to_a_labelled_500(self) -> None:
        error = WebhookSecretUnavailableError(_ENDPOINT_ID)

        assert error.resolve_http_status() == 500
        assert error.resolve_response_code() == ErrorCode.INTERNAL_ERROR
        assert error.resolve_log_message() == "Webhook signing secret unavailable"
        assert error.log_error is True

    def test_the_public_message_is_safe_and_actionable(self) -> None:
        error = WebhookSecretUnavailableError(_ENDPOINT_ID)

        public = error.resolve_public_message()
        # The remedy is stated...
        assert "Rotate" in public
        # ...and no internal identifier or key material leaks.
        assert str(_ENDPOINT_ID) not in public
        assert "signing_secret=" not in public

    def test_the_internal_message_keeps_the_endpoint_id_for_operators(self) -> None:
        error = WebhookSecretUnavailableError(_ENDPOINT_ID)

        assert str(_ENDPOINT_ID) in error.message
        assert error.detail == error.message
        assert error.endpoint_id == _ENDPOINT_ID


class TestGetSigningSecret:
    async def test_a_decryption_failure_raises_the_domain_error(self) -> None:
        service = WebhookService(db=MagicMock())  # type: ignore[arg-type]
        endpoint = _endpoint("gAAAAA-corrupt-ciphertext")

        with (
            patch(
                "src.app.modules.webhooks.service.execute_scoped",
                new=AsyncMock(return_value=_scoped_result(endpoint)),
            ),
            patch(
                "src.app.modules.webhooks.service.decrypt_data",
                side_effect=Exception("InvalidToken"),
            ),
            pytest.raises(WebhookSecretUnavailableError) as raised,
        ):
            await service.get_signing_secret(_ENDPOINT_ID, _TENANT_ID)

        assert raised.value.endpoint_id == _ENDPOINT_ID
        # The original crypto error is preserved for the log trace.
        assert raised.value.__cause__ is not None

    async def test_the_ciphertext_is_never_returned_on_failure(self) -> None:
        """The whole reason the read fails rather than degrading."""
        service = WebhookService(db=MagicMock())  # type: ignore[arg-type]
        ciphertext = "gAAAAA-corrupt-ciphertext"
        endpoint = _endpoint(ciphertext)

        with (
            patch(
                "src.app.modules.webhooks.service.execute_scoped",
                new=AsyncMock(return_value=_scoped_result(endpoint)),
            ),
            patch(
                "src.app.modules.webhooks.service.decrypt_data",
                side_effect=Exception("InvalidToken"),
            ),
            pytest.raises(WebhookSecretUnavailableError) as raised,
        ):
            await service.get_signing_secret(_ENDPOINT_ID, _TENANT_ID)

        assert ciphertext not in raised.value.resolve_public_message()
        assert ciphertext not in raised.value.message

    async def test_a_successful_decrypt_still_returns_the_secret(self) -> None:
        """The happy path is untouched."""
        service = WebhookService(db=MagicMock())  # type: ignore[arg-type]
        endpoint = _endpoint("ciphertext")

        with (
            patch(
                "src.app.modules.webhooks.service.execute_scoped",
                new=AsyncMock(return_value=_scoped_result(endpoint)),
            ),
            patch("src.app.modules.webhooks.service.decrypt_data", return_value="whsec_plaintext"),
        ):
            payload = await service.get_signing_secret(_ENDPOINT_ID, _TENANT_ID)

        assert payload["signing_secret"] == "whsec_plaintext"
        assert payload["webhook_endpoint_id"] == _ENDPOINT_ID

    async def test_an_empty_secret_still_returns_an_empty_string(self) -> None:
        """A missing secret is a valid state, not a decryption failure."""
        service = WebhookService(db=MagicMock())  # type: ignore[arg-type]
        endpoint = _endpoint(None)

        with patch(
            "src.app.modules.webhooks.service.execute_scoped",
            new=AsyncMock(return_value=_scoped_result(endpoint)),
        ):
            payload = await service.get_signing_secret(_ENDPOINT_ID, _TENANT_ID)

        assert payload["signing_secret"] == ""
