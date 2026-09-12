"""Unit tests for standardized error responses and exception handlers."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.app.core.errors import (
    ErrorCode,
    build_exception_table,
    create_error_response,
    setup_exception_handlers,
)
from src.app.core.exceptions.cache_exceptions import CacheException
from src.app.core.exceptions.http_exceptions import TenantContextError
from src.app.modules.ai.unified_errors import SessionLimitExceededError
from src.app.modules.auth.errors import AuthError, InvalidMagicLinkError
from src.app.modules.billing.exceptions import (
    PaymentRequiredError,
    WebhookNotConfiguredError,
    WebhookVerificationError,
)
from src.app.modules.storage.errors import (
    FileEntityNotFoundError,
    FileTypeNotAllowedError,
    InvalidCredentialsError,
    QuotaExceededError,
    StorageBackendError,
    StorageNotConfiguredError,
)
from src.app.shared.exceptions import (
    DuplicateError,
    ForbiddenError,
    ModelNotFoundError,
    RateLimitExceededError,
    ServiceUnavailableError,
    StripeError,
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
    """B12: StorageNotConfiguredError must map to 400."""
    response = client.get("/test/storage-not-configured")
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == ErrorCode.INVALID_INPUT
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


def test_cache_exception_handler():
    """CacheException (framework handler) must map to 500 without leaking detail."""
    response = client.get("/test/cache-error")
    assert response.status_code == 500
    data = response.json()
    assert data["error"]["code"] == ErrorCode.INTERNAL_ERROR
    assert data["error"]["message"] == "Internal Server Error"


def test_exception_table_status_mapping():
    """Phase 2: every domain error maps to its expected HTTP status via the table."""
    by_class = {spec.exc_class: spec for spec in build_exception_table()}
    expected_static: dict[type, int] = {
        ModelNotFoundError: 404,
        DuplicateError: 409,
        ForbiddenError: 403,
        ValidationError: 400,
        RateLimitExceededError: 429,
        TenantContextError: 403,
        ServiceUnavailableError: 503,
        UnauthorizedError: 401,
        PaymentRequiredError: 402,
        WebhookVerificationError: 400,
        WebhookNotConfiguredError: 501,
        FileEntityNotFoundError: 404,
        StorageNotConfiguredError: 400,
        FileTypeNotAllowedError: 400,
        QuotaExceededError: 507,
        StorageBackendError: 502,
        InvalidCredentialsError: 400,
        SessionLimitExceededError: 429,
    }
    assert set(by_class) == set(expected_static) | {StripeError, AuthError}
    for exc_class, expected_status in expected_static.items():
        spec = by_class[exc_class]
        assert not callable(spec.status)
        assert spec.status == expected_status
    # Dynamic-status rows resolve from the instance (covered over HTTP above).
    assert callable(by_class[StripeError].status)
    assert callable(by_class[AuthError].status)
