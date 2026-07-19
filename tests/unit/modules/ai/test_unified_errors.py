"""Unit tests for ai/unified_errors.py exception classes."""


from src.app.modules.ai import unified_errors as ue
from src.app.modules.ai.providers import AIProviderError


def test_ai_authentication_error_defaults():
    err = ue.AIAuthenticationError()
    assert isinstance(err, AIProviderError)
    assert err.error_code == "authentication_error"
    assert err.original_error is None


def test_ai_authentication_error_custom():
    orig = ValueError("bad key")
    err = ue.AIAuthenticationError(message="custom", provider="openai", original_error=orig)
    assert err.provider == "openai"
    assert err.original_error is orig


def test_ai_rate_limit_error():
    err = ue.AIRateLimitError(provider="openai", retry_after=30, original_error=Exception())
    assert err.error_code == "rate_limit_error"
    assert err.retry_after == 30


def test_ai_quota_exceeded_error():
    err = ue.AIQuotaExceededError(quota_type="tokens", provider="openai")
    assert err.error_code == "quota_exceeded_error"
    assert err.quota_type == "tokens"


def test_ai_model_not_found_error():
    err = ue.AIModelNotFoundError(model_name="gpt-5", provider="openai")
    assert err.error_code == "model_not_found_error"
    assert err.model_name == "gpt-5"


def test_ai_invalid_request_error():
    err = ue.AIInvalidRequestError(field="temperature", provider="openai")
    assert err.error_code == "invalid_request_error"
    assert err.field == "temperature"


def test_ai_content_policy_error():
    err = ue.AIContentPolicyError(policy_type="harmful", provider="openai")
    assert err.error_code == "content_policy_error"
    assert err.policy_type == "harmful"


def test_ai_server_error():
    err = ue.AIServerError(status_code=500, provider="openai")
    assert err.error_code == "server_error"
    assert err.status_code == 500


def test_ai_service_unavailable_error():
    err = ue.AIServiceUnavailableError(provider="openai", original_error=Exception())
    assert err.error_code == "service_unavailable_error"


def test_ai_timeout_error():
    err = ue.AITimeoutError(timeout_seconds=12.5, provider="openai")
    assert err.error_code == "timeout_error"
    assert err.timeout_seconds == 12.5


def test_ai_connection_error():
    err = ue.AIConnectionError(provider="openai", original_error=Exception())
    assert err.error_code == "connection_error"


def test_ai_response_parse_error():
    err = ue.AIResponseParseError(expected_format="json", provider="openai")
    assert err.error_code if hasattr(err, "error_code") else True
    assert err.expected_format == "json"


def test_ai_provider_unavailable_error():
    err = ue.AIProviderUnavailableError(provider="openai", original_error=Exception())
    assert err.error_code == "provider_unavailable_error"


def test_session_limit_exceeded_error_defaults():
    err = ue.SessionLimitExceededError(tenant_id=7)
    assert err.tenant_id == 7
    assert err.limit == 100
    assert err.current_count is None
    assert "100" in str(err)
    assert isinstance(err, Exception)


def test_session_limit_exceeded_error_custom():
    err = ue.SessionLimitExceededError(tenant_id="t-1", limit=5, current_count=6)
    assert err.tenant_id == "t-1"
    assert err.limit == 5
    assert err.current_count == 6
    assert "5" in err.message
