"""Shared domain exceptions for application-wide use.

Zero-dependency leaf exceptions that can be safely imported by core,
middleware, domain services, repositories, and controllers.

Its only import is the sibling :mod:`src.app.shared.error_codes` leaf (for
the ``ErrorCode`` vocabulary a subclass declares in ``response_code``).
Nothing here may import ``core.errors`` — that would be a cycle, since
``core.errors`` imports ``core.exceptions.cache_exceptions``, which
imports this module.

A-M1 (partial): feature-domain exceptions must NOT live here — this
leaf is shared foundation, not a feature surface. StripeError is the
known deviation: its move to ``modules/billing/exceptions.py`` is
scheduled for the post-merge single-owner pass (its importers span
client + billing layers); until then it stays put and is re-exported
by ``modules/billing/exceptions.py`` and the deprecated
``modules/_base/exceptions.py`` shim.
"""

from __future__ import annotations

from typing import Any, ClassVar

from src.app.shared.error_codes import ErrorCode


class AppException(Exception):
    """Base application exception for all domain errors.

    HTTP presentation
    -----------------
    The envelope ``code`` and the HTTP status are NOT declared here. They
    resolve through this exception's own declaration — ``resolve_response_code()``
    and ``resolve_http_status()`` — so the pre-route guard adapter
    (``middleware_reject`` in ``src/app/core/errors.py``) and the one generic
    handler cannot disagree about what a rejection looks like.

    A subclass declares how it presents at the API boundary with five
    class-level attributes, so the mapping lives **beside the exception**
    rather than in a table in core. Core registers ONE handler for
    ``AppException`` that reads these off the instance. A family that needs
    custom translation (a per-instance status, a details payload) overrides
    ``resolve_http_status()`` / ``resolve_response_code()`` / ``http_details()``
    on the class — there is no second place to register anything.

    ``http_status``
        Status code for the response envelope. Default 500.
    ``response_code``
        ``ErrorCode`` value for the envelope ``error.code``. Default
        ``INTERNAL_ERROR``.
    ``public_message``
        When set, replaces the instance message in the envelope. Use it
        when the public message must differ from the internal one (e.g.
        never leak provider internals). ``None`` means "use the instance
        message".
    ``log_message``
        Structured-log event name. ``None`` derives one from the class
        name.
    ``log_error``
        Log at ERROR (True) instead of WARNING (False). Default False.

    NOTE: the code attribute is deliberately NOT called ``error_code``.
    ``AIProviderError`` and ``PaymentRequiredError`` already carry an
    ``error_code`` meaning a *provider* code (e.g. Stripe's
    ``card_declined``), which the envelope surfaces under
    ``details["error_code"]``. Reusing that name would conflate a provider
    code with an envelope code.
    """

    http_status: ClassVar[int] = 500
    response_code: ClassVar[str] = ErrorCode.INTERNAL_ERROR
    public_message: ClassVar[str | None] = None
    log_message: ClassVar[str | None] = None
    log_error: ClassVar[bool] = False

    def __init__(self, message: str = "An application error occurred"):
        self.message = message
        self.detail = message
        # Item 7: optional transport headers (e.g. SSO ``Set-Cookie`` hygiene).
        # The one generic handler merges these into the envelope response.
        self.headers: dict[str, str] | None = None
        super().__init__(self.message)

    def resolve_public_message(self) -> str:
        """The message the client sees (``public_message`` wins when set)."""
        if self.public_message is not None:
            return self.public_message
        return str(self.message)

    def resolve_http_status(self) -> int:
        """The status for the envelope.

        Reads the ``http_status`` attribute. Override when the status is
        chosen per *instance* rather than per class (``AuthError`` derives it
        from the status the raiser passed). A method rather than a property
        because ``http_status`` is a writable ``ClassVar`` and mypy forbids
        narrowing it to a read-only property.
        """
        return self.http_status

    def resolve_response_code(self) -> str:
        """The envelope ``error.code``. Override for per-instance codes."""
        return self.response_code

    def http_details(self) -> dict[str, Any] | None:
        """The envelope ``details`` payload, or ``None`` when there is none.

        Part of the client-visible contract, so it lives here rather than in
        core: override it when the response needs structured context (e.g. a
        payment failure's provider and decline codes).
        """
        return None

    def http_headers(self) -> dict[str, str] | None:
        """Transport headers this exception contributes to its own response.

        Separate from ``self.headers`` (the plain instance dict, set by a
        raiser that already knows the exact headers). This hook is for headers
        *derived* from the exception's own state — ``RateLimitExceededError``
        turns ``retry_after`` into ``Retry-After`` — so the value stays correct
        even if that state is mutated between construction and handling.

        The handler merges the two, with ``self.headers`` winning.
        """
        return None

    def resolve_log_message(self) -> str:
        """The structured-log event name (falls back to the class name)."""
        return self.log_message if self.log_message is not None else type(self).__name__


class ModelNotFoundError(AppException):
    """Raised when a model or entity is not found by ID or other criteria.

    Attributes:
        model_name: Name of the model class.
        identifier: The ID or identifier used for lookup.
        message: Human-readable error message.
        detail: Backward-compatibility attribute mirroring message.
    """

    http_status = 404
    response_code = ErrorCode.NOT_FOUND
    log_message = "Resource not found"
    log_error = False

    def __init__(self, model_name: str, identifier: Any = None, message: str | None = None):
        self.model_name = model_name
        self.identifier = identifier
        if message:
            self.message = message
        elif identifier is not None:
            self.message = f"{model_name} with id {identifier} not found"
        else:
            self.message = model_name
        self.detail = self.message
        super().__init__(self.message)

    def http_details(self) -> dict[str, Any]:
        """Envelope ``details``: which model, looked up by what.

        ``identifier`` is stringified so a UUID/int renders as the client sent
        it, and defaults to ``""`` (not null) when the lookup was by name only.
        """
        return {
            "model": getattr(self, "model_name", type(self).__name__),
            "identifier": str(getattr(self, "identifier", "")),
        }


class ValidationError(AppException):
    """Raised when domain validation fails.

    Attributes:
        message: Human-readable error message describing the validation failure.
        detail: Backward-compatibility attribute mirroring message.
    """

    http_status = 400
    response_code = ErrorCode.VALIDATION_ERROR
    log_message = "Validation error"
    log_error = False

    def __init__(self, message: str):
        self.message = message
        self.detail = message
        super().__init__(self.message)


class DuplicateError(AppException):
    """Raised when a duplicate record is detected.

    Attributes:
        field: The field name with duplicate value (e.g., "email").
        value: The duplicate value.
        message: Human-readable error message.
        detail: Backward-compatibility attribute mirroring message.
    """

    http_status = 409
    response_code = ErrorCode.ALREADY_EXISTS
    log_message = "Duplicate resource"
    log_error = False

    def __init__(self, field: str, value: Any = None, message: str | None = None):
        self.field = field
        self.value = value
        if message:
            self.message = message
        elif value is not None:
            self.message = f"Duplicate value for {field}: {value}"
        else:
            self.message = f"Duplicate value for {field}"
        self.detail = self.message
        super().__init__(self.message)

    def http_details(self) -> dict[str, Any]:
        """Envelope ``details``: which field collided, and on what value."""
        return {"field": getattr(self, "field", ""), "value": str(getattr(self, "value", ""))}


class ForbiddenError(AppException):
    """Raised when an action is forbidden due to business rules or permissions.

    Attributes:
        message: Human-readable error message.
        reason: Machine-readable reason code.
        detail: Backward-compatibility attribute mirroring message.
    """

    http_status = 403
    response_code = ErrorCode.FORBIDDEN
    log_message = "Forbidden action"
    log_error = False

    def __init__(self, message: str = "Forbidden", reason: str = "forbidden"):
        self.message = message
        self.reason = reason
        self.detail = message
        super().__init__(self.message)

    def http_details(self) -> dict[str, Any]:
        """Envelope ``details``: the machine-readable reason code.

        Falls back to the message when a subclass never set ``reason``.
        """
        return {"reason": getattr(self, "reason", str(self))}


class UnauthorizedError(AppException):
    """Raised when authentication fails or credentials are required.

    Attributes:
        message: Human-readable error message.
        reason: Optional machine-readable reason code.
        detail: Backward-compatibility attribute mirroring message.
    """

    http_status = 401
    response_code = ErrorCode.UNAUTHORIZED
    log_message = "Unauthorized error"
    log_error = False
    # NOTE: unlike ForbiddenError, this class carries a `reason` but
    # deliberately does NOT surface it — the old core row had details=None,
    # and an auth failure's reason ("token_expired" vs "bad_signature") is
    # useful to an attacker. Keep http_details() at the AppException default.

    def __init__(self, message: str = "Unauthorized", reason: str = "unauthorized"):
        self.message = message
        self.reason = reason
        self.detail = message
        super().__init__(self.message)


class TenantContextError(AppException):
    """Raised when a tenant-scoped operation lacks an active tenant context.

    Canonical home (item 7): previously lived in the now-deleted HTTP-shaped
    exception shim as a ``CustomException`` subclass. Services must raise this
    domain error; it declares its own 403 mapping.

    Attributes:
        message: Human-readable error message.
        reason: Machine-readable reason code.
        detail: Backward-compatibility attribute mirroring message.
    """

    http_status = 403
    response_code = ErrorCode.FORBIDDEN
    log_message = "Tenant context error"
    log_error = False

    def __init__(
        self,
        message: str = "Tenant context required for tenant-scoped operation",
        reason: str = "tenant_context_error",
    ):
        self.message = message
        self.reason = reason
        self.detail = message
        super().__init__(self.message)

    def http_details(self) -> dict[str, Any]:
        """Envelope ``details``: the machine-readable reason code."""
        return {"reason": getattr(self, "reason", str(self))}


class RateLimitExceededError(AppException):
    """Raised when a rate limit is exceeded.

    Attributes:
        message: Human-readable error message.
        retry_after: Seconds to wait before retrying.
        detail: Backward-compatibility attribute mirroring message.
    """

    http_status = 429
    response_code = ErrorCode.RATE_LIMITED
    log_message = "Rate limit exceeded"
    log_error = False

    def __init__(self, message: str = "Rate limit exceeded", retry_after: int = 60):
        self.message = message
        self.retry_after = retry_after
        self.detail = message
        super().__init__(self.message)

    def http_headers(self) -> dict[str, str]:
        """``Retry-After``, derived from ``retry_after`` at handling time.

        The old core row was ``lambda exc: {"Retry-After": str(getattr(exc,
        "retry_after", 60))}``; this keeps the derivation lazy so a mutated
        ``retry_after`` is still honoured.
        """
        return {"Retry-After": str(getattr(self, "retry_after", 60))}


class ServiceUnavailableError(AppException):
    """Raised when an external or downstream service is unavailable.

    Attributes:
        message: Human-readable error message.
        detail: Backward-compatibility attribute mirroring message.
    """

    http_status = 503
    response_code = ErrorCode.SERVICE_UNAVAILABLE
    log_message = "Service unavailable"
    # The one shared error logged at ERROR: a dependency being down is an
    # operational incident, not a client mistake.
    log_error = True

    def __init__(self, message: str = "Service temporarily unavailable"):
        self.message = message
        self.detail = message
        super().__init__(self.message)


class UnprocessableRequestError(AppException):
    """Raised when a request is syntactically valid but semantically rejected.

    Added for the pagination deep-scan guard (P1-10 follow-on). That guard
    raised ``fastapi.HTTPException(422)`` from ``core``, an HTTP response
    object below the transport layer — the same defect class the DB circuit
    guard had. This carries 422, so the wire status is unchanged; only the
    exception TYPE is now a domain one.

    Distinct from :class:`ValidationError` (400): that one means a field is
    malformed, this one means the combination is rejected even though every
    field parsed.
    """

    http_status = 422
    response_code = ErrorCode.VALIDATION_ERROR
    log_message = "Unprocessable request"
    log_error = False

    def __init__(self, message: str):
        self.message = message
        self.detail = message
        super().__init__(self.message)


class StripeError(AppException):
    """Raised when a Stripe API call fails.

    A-M1 DEBT: this is a billing-domain exception living in the shared
    leaf. Relocation to ``src.app/modules/billing/exceptions.py`` is
    scheduled for the post-merge single-owner pass (importers span
    ``src/app/clients/billing/stripe.py`` and the billing module); it
    stays here for now and is re-exported by billing and the deprecated
    ``_base`` shim so no importer breaks mid-merge.

    Attributes:
        message: Human-readable error message.
        status_code: HTTP status code to surface at the API boundary.
        detail: Backward-compatibility attribute mirroring message.
    """

    log_message = "Stripe error"
    log_error = False

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        self.detail = message
        super().__init__(self.message)

    def resolve_http_status(self) -> int:
        """Per-*instance* status: the raiser passes Stripe's own status."""
        return self.status_code

    def resolve_response_code(self) -> str:
        """Derived from the status: a 5xx is our dependency failing, a 4xx is input.

        Mirrors the old core ``_stripe_code`` helper exactly, including that
        ``SERVICE_UNAVAILABLE`` (not ``BAD_GATEWAY``) is the 5xx code here.
        """
        return ErrorCode.SERVICE_UNAVAILABLE if self.status_code >= 500 else ErrorCode.INVALID_INPUT


__all__ = [
    "AppException",
    "DuplicateError",
    "ForbiddenError",
    "ModelNotFoundError",
    "RateLimitExceededError",
    "ServiceUnavailableError",
    "UnprocessableRequestError",
    "StripeError",
    "TenantContextError",
    "UnauthorizedError",
    "ValidationError",
]
