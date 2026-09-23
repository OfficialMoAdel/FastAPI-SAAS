"""Unit tests for standardized error responses and exception handlers."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.app.core.errors import (
    ErrorCode,
    create_error_response,
    setup_exception_handlers,
)
from src.app.core.exceptions.cache_exceptions import CacheException
from src.app.modules.ai.providers import AIProviderError
from src.app.modules.ai.unified_errors import (
    AIAuthenticationError,
    AIConnectionError,
    AIContentPolicyError,
    AIInvalidRequestError,
    AIModelNotFoundError,
    AIProviderUnavailableError,
    AIQuotaExceededError,
    AIRateLimitError,
    AIResponseParseError,
    AIServerError,
    AIServiceUnavailableError,
    AITimeoutError,
    SessionLimitExceededError,
)
from src.app.modules.auth.errors import (
    AuthError,
    InvalidMagicLinkError,
    MagicLinkAlreadyUsedError,
    MagicLinkRateLimitError,
    MagicLinkUserInactiveError,
    SSOGatewayError,
)
from src.app.modules.billing.exceptions import (
    PaymentRequiredError,
    SubscriptionRequiredError,
    WebhookNotConfiguredError,
    WebhookVerificationError,
)
from src.app.modules.storage.errors import (
    FileEntityNotFoundError,
    FileTypeNotAllowedError,
    InvalidCredentialsError,
    QuotaExceededError,
    StorageBackendError,
    StorageError,
    StorageNotConfiguredError,
)
from src.app.modules.user_preferences.service import PreferenceStorageError
from src.app.shared.exceptions import (
    DuplicateError,
    ForbiddenError,
    ModelNotFoundError,
    RateLimitExceededError,
    ServiceUnavailableError,
    StripeError,
    TenantContextError,
    UnauthorizedError,
    ValidationError,
)


def test_create_error_response():
    """Test standard error response format."""
    response = create_error_response(
        code=ErrorCode.INVALID_INPUT,
        message="Invalid input provided",
        status_code=400,
        details={"field": "email"},
        request_id="req-123",
    )

    assert response.status_code == 400

    body = response.body.decode()
    import json
    data = json.loads(body)

    assert "error" in data
    assert data["error"]["code"] == ErrorCode.INVALID_INPUT
    assert data["error"]["message"] == "Invalid input provided"
    assert data["error"]["details"] == {"field": "email"}
    assert data["error"]["request_id"] == "req-123"
    assert data["detail"] == "Invalid input provided"  # backward compatibility


# Setup a test app with all exception handlers
app = FastAPI()
setup_exception_handlers(app)

@app.get("/test/http-exception")
async def route_http_exception():
    raise StarletteHTTPException(status_code=404, detail="Not found here")

@app.get("/test/model-not-found")
async def route_model_not_found():
    raise ModelNotFoundError("User", 123)

@app.get("/test/duplicate")
async def route_duplicate():
    raise DuplicateError("email", "test@example.com")

@app.get("/test/forbidden")
async def route_forbidden():
    raise ForbiddenError("Not enough permissions")

@app.get("/test/rate-limit")
async def route_rate_limit():
    raise RateLimitExceededError("Too many requests")

@app.get("/test/tenant-context")
async def route_tenant_context():
    raise TenantContextError("Invalid context")

@app.get("/test/service-unavailable")
async def route_service_unavailable():
    raise ServiceUnavailableError("Service down")

@app.get("/test/stripe-error")
async def route_stripe_error():
    raise StripeError(message="Payment failed", status_code=402)

@app.get("/test/payment-required")
async def route_payment_required():
    raise PaymentRequiredError(message="Payment failed", error_code="card_declined", decline_code="insufficient_funds")

@app.get("/test/validation")
async def route_validation():
    raise ValidationError("Validation failed")

@app.get("/test/session-limit")
async def route_session_limit():
    raise SessionLimitExceededError(tenant_id="ten-1", limit=10, current_count=11)

@app.get("/test/unauthorized")
async def route_unauthorized():
    raise UnauthorizedError("Missing credentials")

@app.get("/test/auth-error")
async def route_auth_error():
    raise AuthError(detail="Bad credentials", status_code=401)

@app.get("/test/auth-magic-link-invalid")
async def route_auth_magic_link_invalid():
    raise InvalidMagicLinkError()

@app.get("/test/webhook-verification")
async def route_webhook_verification():
    raise WebhookVerificationError("Bad signature")

@app.get("/test/webhook-not-configured")
async def route_webhook_not_configured():
    raise WebhookNotConfiguredError()

@app.get("/test/cache-error")
async def route_cache_error():
    raise CacheException("redis down")


@app.get("/test/sso-gateway-timeout")
async def route_sso_gateway_timeout():
    # Item 7: provider-transport failures ride the table with cookie hygiene.
    exc = SSOGatewayError(detail="Provider timeout.", status_code=504)
    exc.headers = {"Set-Cookie": 'sso_state=""; Max-Age=0; Path=/; HttpOnly; SameSite=Lax'}
    raise exc


@app.get("/test/sso-gateway-bad-gateway")
async def route_sso_gateway_bad_gateway():
    raise SSOGatewayError(detail="Provider returned an error response.", status_code=502)


@app.get("/test/rate-limit-custom-retry")
async def route_rate_limit_custom_retry():
    raise RateLimitExceededError("Slow down", retry_after=17)

@app.get("/test/generic")
async def route_generic():
    raise ValueError("Unexpected error")

# B12: storage exception routes — the P0-2 bug class (a dropped handler
# registration shipped silently because no test raised these over HTTP).
@app.get("/test/storage-quota-exceeded")
async def route_storage_quota_exceeded():
    raise QuotaExceededError("Storage quota exceeded")

@app.get("/test/storage-backend")
async def route_storage_backend():
    raise StorageBackendError("S3 upload failed")

@app.get("/test/storage-file-not-found")
async def route_storage_file_not_found():
    raise FileEntityNotFoundError("File not found")

@app.get("/test/storage-file-type-not-allowed")
async def route_storage_file_type_not_allowed():
    raise FileTypeNotAllowedError("File type not allowed")

@app.get("/test/storage-not-configured")
async def route_storage_not_configured():
    raise StorageNotConfiguredError("Storage not configured")

@app.get("/test/storage-invalid-credentials")
async def route_storage_invalid_credentials():
    raise InvalidCredentialsError("Invalid storage credentials")


client = TestClient(app, raise_server_exceptions=False)


def test_http_exception_handler():
    response = client.get("/test/http-exception")
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == ErrorCode.NOT_FOUND
    assert data["error"]["message"] == "Not found here"


def test_model_not_found_handler():
    response = client.get("/test/model-not-found")
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == ErrorCode.NOT_FOUND
    assert data["error"]["details"] == {"model": "User", "identifier": "123"}


def test_duplicate_error_handler():
    response = client.get("/test/duplicate")
    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == ErrorCode.ALREADY_EXISTS
    assert data["error"]["details"] == {"field": "email", "value": "test@example.com"}


def test_forbidden_error_handler():
    response = client.get("/test/forbidden")
    assert response.status_code == 403
    data = response.json()
    assert data["error"]["code"] == ErrorCode.FORBIDDEN
    assert data["error"]["message"] == "Not enough permissions"


def test_rate_limit_handler():
    response = client.get("/test/rate-limit")
    assert response.status_code == 429
    data = response.json()
    assert data["error"]["code"] == ErrorCode.RATE_LIMITED
    assert data["error"]["message"] == "Too many requests"


def test_rate_limit_handler_sets_retry_after_header():
    # P2-E regression: domain 429s carry Retry-After from the exception
    # (headers hook in the EXCEPTION_HANDLERS table row).
    response = client.get("/test/rate-limit")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"


def test_tenant_context_handler():
    response = client.get("/test/tenant-context")
    assert response.status_code == 403
    data = response.json()
    assert data["error"]["code"] == ErrorCode.FORBIDDEN
    assert data["error"]["message"] == "Invalid context"


def test_service_unavailable_handler():
    response = client.get("/test/service-unavailable")
    assert response.status_code == 503
    data = response.json()
    assert data["error"]["code"] == ErrorCode.SERVICE_UNAVAILABLE
    assert data["error"]["message"] == "Service down"


def test_stripe_error_handler():
    response = client.get("/test/stripe-error")
    assert response.status_code == 402
    data = response.json()
    assert data["error"]["code"] == ErrorCode.INVALID_INPUT
    assert data["error"]["message"] == "Payment failed"


def test_payment_required_handler():
    response = client.get("/test/payment-required")
    assert response.status_code == 402
    data = response.json()
    assert data["error"]["code"] == ErrorCode.PAYMENT_REQUIRED
    assert data["error"]["details"] == {"error_code": "card_declined", "decline_code": "insufficient_funds"}


def test_validation_error_handler():
    response = client.get("/test/validation")
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == ErrorCode.VALIDATION_ERROR


def test_session_limit_handler():
    response = client.get("/test/session-limit")
    assert response.status_code == 429
    data = response.json()
    assert data["error"]["code"] == ErrorCode.SESSION_LIMIT_EXCEEDED
    assert data["error"]["details"] == {"tenant_id": "ten-1", "limit": 10, "current_count": 11}


def test_generic_exception_handler():
    response = client.get("/test/generic")
    assert response.status_code == 500
    data = response.json()
    assert data["error"]["code"] == ErrorCode.INTERNAL_ERROR
    assert data["error"]["message"] == "An internal error occurred"


def test_storage_quota_exceeded_handler():
    """B12: QuotaExceededError must map to 507, not fall through to 500."""
    response = client.get("/test/storage-quota-exceeded")
    assert response.status_code == 507
    data = response.json()
    assert data["error"]["code"] == ErrorCode.STORAGE_QUOTA_EXCEEDED
    assert data["error"]["message"] == "Storage quota exceeded."


def test_storage_backend_error_handler():
    """B12: StorageBackendError must map to 502 with a fixed (non-leaking) message."""
    response = client.get("/test/storage-backend")
    assert response.status_code == 502
    data = response.json()
    assert data["error"]["code"] == ErrorCode.STORAGE_BACKEND_ERROR
    assert data["error"]["message"] == "Storage provider error during file operation."


def test_storage_file_not_found_handler():
    """B12: FileEntityNotFoundError must map to 404."""
    response = client.get("/test/storage-file-not-found")
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == ErrorCode.NOT_FOUND
    assert data["error"]["message"] == "File not found"


def test_storage_file_type_not_allowed_handler():
    """B12: FileTypeNotAllowedError must map to 400."""
    response = client.get("/test/storage-file-type-not-allowed")
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == ErrorCode.INVALID_INPUT
    assert data["error"]["message"] == "File type not allowed."


def test_storage_not_configured_handler():
    """B12: StorageNotConfiguredError maps to 404 (missing resource).

    FIX (code-review): was 400 — but GET /storage for an unconfigured
    tenant is a missing resource (404), and the integration/e2e suite
    asserts 404. 400 also made the regular-user 403 unreachable
    (validation ran before authz).
    """
    response = client.get("/test/storage-not-configured")
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == ErrorCode.NOT_FOUND
    assert data["error"]["message"] == "Storage not configured for this tenant."


def test_storage_invalid_credentials_handler():
    """B12: InvalidCredentialsError must map to 400."""
    response = client.get("/test/storage-invalid-credentials")
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == ErrorCode.INVALID_INPUT
    assert data["error"]["message"] == "Invalid storage provider credentials."


def test_unauthorized_error_handler():
    """Shared UnauthorizedError must map to 401."""
    response = client.get("/test/unauthorized")
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == ErrorCode.UNAUTHORIZED
    assert data["error"]["message"] == "Missing credentials"


def test_auth_error_handler():
    """AuthError carries its own status; 401 maps to UNAUTHORIZED."""
    response = client.get("/test/auth-error")
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == ErrorCode.UNAUTHORIZED
    assert data["error"]["message"] == "Bad credentials"


def test_auth_subclass_uses_base_table_row():
    """Weakened-form check: MagicLink subclasses dispatch via the AuthError row."""
    response = client.get("/test/auth-magic-link-invalid")
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == ErrorCode.INVALID_INPUT
    assert data["error"]["message"] == "Invalid or expired magic link token"


def test_webhook_verification_handler():
    """WebhookVerificationError must map to 400."""
    response = client.get("/test/webhook-verification")
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == ErrorCode.INVALID_INPUT
    assert data["error"]["message"] == "Bad signature"


def test_webhook_not_configured_handler():
    """WebhookNotConfiguredError must map to 501 NOT_IMPLEMENTED (not 503)."""
    response = client.get("/test/webhook-not-configured")
    assert response.status_code == 501
    data = response.json()
    assert data["error"]["code"] == ErrorCode.NOT_IMPLEMENTED


def test_sso_gateway_timeout_handler_preserves_cookie_hygiene():
    """Item 7: SSO 504 via the table carries the Set-Cookie hygiene header."""
    response = client.get("/test/sso-gateway-timeout")
    assert response.status_code == 504
    data = response.json()
    assert data["error"]["code"] == ErrorCode.SERVICE_UNAVAILABLE
    assert data["error"]["message"] == "Provider timeout."
    assert response.headers["set-cookie"].startswith("sso_state=")


def test_sso_gateway_bad_gateway_handler():
    """Item 7: SSO 502 via the table maps to BAD_GATEWAY."""
    response = client.get("/test/sso-gateway-bad-gateway")
    assert response.status_code == 502
    data = response.json()
    assert data["error"]["code"] == ErrorCode.BAD_GATEWAY


def test_rate_limit_custom_retry_after_header():
    """Per-instance retry_after reaches the client as a real header.

    Since pass 5 there is no RateLimitExceededError row, so this now exercises
    the generic AppException fallback end-to-end — proving http_headers() is
    actually consulted and merged, not just implemented.
    """
    response = client.get("/test/rate-limit-custom-retry")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "17"


def test_cache_exception_handler():
    """CacheException (framework handler) must map to 500 without leaking detail.

    Also the precedence guard: Starlette resolves handlers by walking
    ``type(exc).__mro__`` and taking the FIRST registered class, so the
    ``CacheException`` handler beats the generic ``AppException`` fallback even
    though ``CacheException`` is a subclass of it. Pass 5 re-pointed this role
    here (it used to be proved by a table row) because ``CacheException`` is a
    live case that survives pass 6 deleting the table machinery.
    """
    response = client.get("/test/cache-error")
    assert response.status_code == 500
    data = response.json()
    assert data["error"]["code"] == ErrorCode.INTERNAL_ERROR
    assert data["error"]["message"] == "Internal Server Error"


# Candidate 3, pass 1: the storage family migrated OFF the table. Its mapping
# now lives beside the exceptions, so core no longer imports the storage module
# (the `core.errors -> modules.storage.errors` layering edge is deleted).
# NOTE: the "no longer a table row" assertions that lived here are gone — as of
# pass 6 there is no table at all, so asserting absence from it would be
# vacuous. The mapping itself is pinned by the parametrized test below, and the
# "core.errors imports no feature module" invariant is pinned in
# tests/unit/core/test_exception_registration.py.


@pytest.mark.parametrize(
    ("exc_class", "expected_status", "expected_code"),
    [
        (StorageError, 500, ErrorCode.INTERNAL_ERROR),
        (StorageNotConfiguredError, 404, ErrorCode.NOT_FOUND),
        (InvalidCredentialsError, 400, ErrorCode.INVALID_INPUT),
        (FileEntityNotFoundError, 404, ErrorCode.NOT_FOUND),
        (FileTypeNotAllowedError, 400, ErrorCode.INVALID_INPUT),
        (QuotaExceededError, 507, ErrorCode.STORAGE_QUOTA_EXCEEDED),
        (StorageBackendError, 502, ErrorCode.STORAGE_BACKEND_ERROR),
    ],
)
def test_storage_family_declares_its_own_mapping(exc_class, expected_status, expected_code):
    """The mapping that used to be a core row is now declared on the class."""
    assert exc_class.http_status == expected_status
    assert exc_class.response_code == expected_code


def test_public_message_overrides_the_instance_message():
    """A class-level public_message wins over whatever the raiser passed.

    This is the non-leaking policy the old table rows encoded: storage
    failures must not surface provider internals to the client.
    """
    exc = QuotaExceededError("bucket acme-prod-files exceeded 10GB")
    assert exc.resolve_public_message() == "Storage quota exceeded."
    # The internal message is still available for logs/debugging.
    assert str(exc) == "bucket acme-prod-files exceeded 10GB"


def test_domain_errors_no_longer_use_a_bespoke_row():
    """Every family migrated in candidate 3 now resolves via the fallback.

    Exercised over HTTP rather than by introspecting the table, so it keeps
    working after the table is deleted.
    """
    response = client.get("/test/stripe-error")
    assert response.status_code == 402
    assert response.json()["error"]["code"] == ErrorCode.INVALID_INPUT


# Candidate 3, pass 3: the auth family migrated OFF the table. Unlike storage
# and billing, its mapping is DERIVED from the instance status, because the
# family carries its status per instance (SSOGatewayError is 502 or 504).
@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (400, ErrorCode.INVALID_INPUT),
        (401, ErrorCode.UNAUTHORIZED),
        (403, ErrorCode.FORBIDDEN),
        (410, ErrorCode.GONE),
        (429, ErrorCode.RATE_LIMITED),
        (502, ErrorCode.BAD_GATEWAY),
        (504, ErrorCode.SERVICE_UNAVAILABLE),
        (418, ErrorCode.INTERNAL_ERROR),  # unmapped status falls back, as before
    ],
)
def test_auth_mapping_is_derived_from_the_instance_status(status_code, expected_code):
    """The old row did `lambda exc: exc.status_code` + `_auth_code`; so does the class."""
    exc = AuthError(detail="nope", status_code=status_code)
    assert exc.resolve_http_status() == status_code
    assert exc.resolve_response_code() == expected_code


def test_auth_subclasses_keep_their_own_status_through_the_derivation():
    """Every magic-link subclass must still present its own status/code."""
    assert InvalidMagicLinkError().resolve_http_status() == 400
    assert InvalidMagicLinkError().resolve_response_code() == ErrorCode.INVALID_INPUT

    assert MagicLinkAlreadyUsedError().resolve_http_status() == 410
    assert MagicLinkAlreadyUsedError().resolve_response_code() == ErrorCode.GONE

    assert MagicLinkRateLimitError().resolve_http_status() == 429
    assert MagicLinkRateLimitError().resolve_response_code() == ErrorCode.RATE_LIMITED

    assert MagicLinkUserInactiveError().resolve_http_status() == 403
    assert MagicLinkUserInactiveError().resolve_response_code() == ErrorCode.FORBIDDEN


def test_sso_gateway_error_derives_status_and_code_together():
    """The runtime 502/504 pair is exactly why this family is instance-derived."""
    timeout = SSOGatewayError(detail="Provider timeout.", status_code=504)
    assert timeout.resolve_http_status() == 504
    assert timeout.resolve_response_code() == ErrorCode.SERVICE_UNAVAILABLE

    bad_gateway = SSOGatewayError(detail="Provider error.", status_code=502)
    assert bad_gateway.resolve_http_status() == 502
    assert bad_gateway.resolve_response_code() == ErrorCode.BAD_GATEWAY


# Candidate 3, pass 2: the billing family migrated OFF the table.
@pytest.mark.parametrize(
    ("exc_class", "expected_status", "expected_code"),
    [
        (PaymentRequiredError, 402, ErrorCode.PAYMENT_REQUIRED),
        (SubscriptionRequiredError, 402, ErrorCode.PAYMENT_REQUIRED),
        (WebhookVerificationError, 400, ErrorCode.INVALID_INPUT),
        (WebhookNotConfiguredError, 501, ErrorCode.NOT_IMPLEMENTED),
    ],
)
def test_billing_family_declares_its_own_mapping(exc_class, expected_status, expected_code):
    """The mapping that used to be a core row is now declared on the class."""
    assert exc_class.http_status == expected_status
    assert exc_class.response_code == expected_code


def test_payment_details_come_from_the_exception():
    """The details payload moved from core's _payment_details to the class.

    This is the pass-2 shape of the seam: client-visible structure is the
    exception's job, so core no longer needs a per-family builder.
    """
    exc = PaymentRequiredError("Payment failed", error_code="card_declined", decline_code="insufficient_funds")
    assert exc.http_details() == {"error_code": "card_declined", "decline_code": "insufficient_funds"}

    # decline_code is omitted (not null) when Stripe did not supply one.
    assert PaymentRequiredError("Payment failed", error_code="card_declined").http_details() == {
        "error_code": "card_declined"
    }


def test_app_exception_default_has_no_details_payload():
    """A class that declares nothing must not invent a details payload."""
    assert QuotaExceededError("nope").http_details() is None


# Candidate 3, pass 4: the ai family migrated OFF the table — and this was the
# last feature-module edge out of core.errors. AIProviderError covers the whole
# 12-class tree with ONE declaration (subclasses inherit it), which is exactly
# what the single core row used to do via Starlette's MRO walk.
_AI_TREE = {
    AIAuthenticationError,
    AIRateLimitError,
    AIQuotaExceededError,
    AIModelNotFoundError,
    AIInvalidRequestError,
    AIContentPolicyError,
    AIServerError,
    AIServiceUnavailableError,
    AITimeoutError,
    AIConnectionError,
    AIResponseParseError,
    AIProviderUnavailableError,
}


def test_ai_provider_error_covers_the_whole_tree():
    """One declaration on the root serves all 12 subclasses, as the row did."""
    for exc_class in _AI_TREE:
        assert issubclass(exc_class, AIProviderError)
        assert exc_class.http_status == 502
        assert exc_class.response_code == ErrorCode.BAD_GATEWAY
        assert exc_class.log_error is True


def test_ai_server_error_does_not_activate_its_provider_status():
    """Regression guard for a behaviour change the migration must NOT make.

    AIServerError carries the *provider's* status_code for diagnostics. The
    old core row was a static 502 and ignored it; so must the class. If
    someone "fixes" this by honouring status_code, a provider echoing 429 or
    401 would leak through as our own envelope status.
    """
    assert AIServerError(status_code=429).resolve_http_status() == 502
    assert AIServerError(status_code=503).resolve_response_code() == ErrorCode.BAD_GATEWAY


def test_ai_provider_details_come_from_the_exception():
    """The details payload moved from core's _ai_provider_details to the class."""
    exc = AIProviderError("boom", error_code="rate_limit_exceeded", provider="openai")
    assert exc.http_details() == {"provider": "openai", "error_code": "rate_limit_exceeded"}

    # provider is optional; the key is present with a falsy value, as before.
    assert AIProviderError("boom", error_code="server_error").http_details() == {
        "provider": None,
        "error_code": "server_error",
    }


def test_ai_provider_error_uses_the_instance_message():
    """public_message is unset, so the raiser's message reaches the client."""
    exc = AIProviderError("OpenAI returned 529", error_code="server_error", provider="openai")
    assert exc.resolve_public_message() == "OpenAI returned 529"
    assert exc.resolve_log_message() == "AI provider error"


def test_session_limit_declares_its_own_mapping():
    """The 429/SESSION_LIMIT_EXCEEDED row is now a class declaration."""
    assert SessionLimitExceededError.http_status == 429
    assert SessionLimitExceededError.response_code == ErrorCode.SESSION_LIMIT_EXCEEDED
    assert SessionLimitExceededError.log_error is False


def test_session_limit_details_omit_an_unknown_current_count():
    """current_count is omitted (not null) when the raiser did not supply it."""
    assert SessionLimitExceededError(tenant_id=7).http_details() == {"tenant_id": 7, "limit": 100}
    assert SessionLimitExceededError(tenant_id=7, limit=50, current_count=50).http_details() == {
        "tenant_id": 7,
        "limit": 50,
        "current_count": 50,
    }


# Candidate 3, pass 5: the shared family (the leaf's own classes) + the last
# feature-module row (PreferenceStorageError). After this the table is empty,
# so the generic AppException fallback serves every domain error.
@pytest.mark.parametrize(
    ("exc_class", "expected_status", "expected_code", "expected_log_error"),
    [
        (ModelNotFoundError, 404, ErrorCode.NOT_FOUND, False),
        (DuplicateError, 409, ErrorCode.ALREADY_EXISTS, False),
        (ForbiddenError, 403, ErrorCode.FORBIDDEN, False),
        (ValidationError, 400, ErrorCode.VALIDATION_ERROR, False),
        (RateLimitExceededError, 429, ErrorCode.RATE_LIMITED, False),
        (TenantContextError, 403, ErrorCode.FORBIDDEN, False),
        (ServiceUnavailableError, 503, ErrorCode.SERVICE_UNAVAILABLE, True),
        (UnauthorizedError, 401, ErrorCode.UNAUTHORIZED, False),
    ],
)
def test_shared_family_declares_its_own_mapping(
    exc_class, expected_status, expected_code, expected_log_error
):
    """The mapping that used to be a core row is now declared on the class."""
    assert exc_class.http_status == expected_status
    assert exc_class.response_code == expected_code
    assert exc_class.log_error is expected_log_error


def test_stripe_error_derives_status_and_code_from_the_instance():
    """The one shared class with a per-instance status (the raiser passes it)."""
    assert StripeError("nope", status_code=402).resolve_http_status() == 402
    assert StripeError("nope", status_code=402).resolve_response_code() == ErrorCode.INVALID_INPUT
    # 5xx maps to SERVICE_UNAVAILABLE (not BAD_GATEWAY) — mirrors the old _stripe_code.
    assert StripeError("down", status_code=503).resolve_response_code() == ErrorCode.SERVICE_UNAVAILABLE


def test_shared_details_payloads_come_from_the_exception():
    """The three payload builders that lived in core now live on the classes."""
    assert ModelNotFoundError("User", 42).http_details() == {"model": "User", "identifier": "42"}
    # PRE-EXISTING QUIRK, preserved verbatim: `identifier` defaults to None and
    # the builder does str(getattr(exc, "identifier", "")). The attribute
    # EXISTS (as None), so the getattr default never applies and the client sees
    # the string "None". Ugly, but it is what the old core helper produced, and
    # this migration must not change the envelope. Fixing it is its own change.
    assert ModelNotFoundError("User").http_details() == {"model": "User", "identifier": "None"}

    assert DuplicateError("email", "a@b.c").http_details() == {"field": "email", "value": "a@b.c"}

    assert ForbiddenError(reason="not_owner").http_details() == {"reason": "not_owner"}
    assert TenantContextError(reason="no_tenant").http_details() == {"reason": "no_tenant"}


def test_unauthorized_error_does_not_leak_its_reason():
    """It carries a `reason` but must NOT surface it — the old row had no details.

    The distinction between "token_expired" and "bad_signature" is useful to an
    attacker, so this asymmetry with ForbiddenError is deliberate.
    """
    exc = UnauthorizedError(reason="token_expired")
    assert exc.reason == "token_expired"
    assert exc.http_details() is None


def test_rate_limit_retry_after_is_derived_lazily():
    """http_headers() replaces the row's `lambda exc: {"Retry-After": ...}`.

    Lazy derivation is the point: a retry_after mutated after construction must
    still reach the client, which an eagerly-populated dict could not do.
    """
    exc = RateLimitExceededError("Slow down", retry_after=17)
    assert exc.http_headers() == {"Retry-After": "17"}

    exc.retry_after = 3
    assert exc.http_headers() == {"Retry-After": "3"}

    # Defaults to 60 when a subclass never set retry_after, as the row did.
    assert RateLimitExceededError("Slow down").http_headers() == {"Retry-After": "60"}


def test_app_exception_has_no_headers_by_default():
    """A class that declares nothing must not invent transport headers."""
    assert ValidationError("nope").http_headers() is None


def test_preference_storage_error_declares_its_own_mapping():
    """The last feature-module row — its migration is what freed core.errors."""
    assert PreferenceStorageError.http_status == 400
    assert PreferenceStorageError.response_code == ErrorCode.INVALID_INPUT
    assert PreferenceStorageError.log_error is True

    exc = PreferenceStorageError("upload", "backend said no")
    # reason is the RAW backend detail, not the composed message.
    assert exc.http_details() == {"operation": "upload", "reason": "backend said no"}
    assert exc.resolve_public_message() == "Storage upload failed: backend said no"

