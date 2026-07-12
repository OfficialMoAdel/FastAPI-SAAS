"""Unit tests for standardized error responses and exception handlers."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.app.core.errors import (
    ErrorCode,
    create_error_response,
    setup_exception_handlers,
)
from src.app.modules._base.exceptions import (
    DuplicateError,
    ForbiddenError,
    ModelNotFoundError,
    RateLimitExceededError,
    ServiceUnavailableError,
    StripeError,
    TenantContextError,
    ValidationError,
)
from src.app.modules.ai.unified_errors import SessionLimitExceededError
from src.app.modules.billing.exceptions import PaymentRequiredError


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

@app.get("/test/generic")
async def route_generic():
    raise ValueError("Unexpected error")


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
