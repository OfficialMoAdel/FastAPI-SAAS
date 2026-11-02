"""Webhook module specific exceptions.

Follows the ``billing/exceptions.py`` pattern: each class declares its own HTTP
mapping (``http_status`` / ``response_code`` / ``log_message``) so
``core.errors`` needs no row for this family and therefore no import of this
module.
"""

from __future__ import annotations

from src.app.shared.error_codes import ErrorCode
from src.app.shared.exceptions import AppException


class WebhookSecretUnavailableError(AppException):
    """Raised when a stored webhook signing secret cannot be decrypted.

    ``WebhookEndpoint.signing_secret`` is ALWAYS written through
    ``encrypt_data``, so a decryption failure means the row (or the key
    material that produced it) is corrupt — never that the caller sent
    something wrong. Returning the ciphertext instead would leak the stored
    blob to the caller, so the read must fail.

    Why this class exists: the raise used to be a bare ``ValueError``, which is
    not an ``AppException`` and therefore not in the central domain-error
    table in ``core/errors.py``. Every key-rotation mismatch surfaced as an
    unlabelled 500 with no ``error.code`` for a client to branch on and no
    structured ``log_message`` for an operator to alert on.

    The client-visible message is deliberately generic — it names the failure
    and the remedy, not the endpoint's internals. ``endpoint_id`` stays on the
    instance for the structured log line.
    """

    http_status = 500
    response_code = ErrorCode.INTERNAL_ERROR
    public_message = (
        "The stored signing secret for this webhook endpoint could not be read. "
        "Rotate the secret to restore delivery signing."
    )
    log_message = "Webhook signing secret unavailable"
    log_error = True

    def __init__(self, endpoint_id: object) -> None:
        self.endpoint_id = endpoint_id
        message = f"Cannot decrypt webhook signing_secret for endpoint {endpoint_id}."
        self.message = message
        self.detail = message
        super().__init__(message)


__all__ = ["WebhookSecretUnavailableError"]
