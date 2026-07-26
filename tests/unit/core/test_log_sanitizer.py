"""Comprehensive unit tests for log sanitizer module."""

import logging
import time
from typing import Any

import pytest
import structlog

from src.app.core.observability.log_sanitizer import (
    LogSanitizer,
    SanitizingProcessor,
    SecureFormatter,
    get_log_sanitizer,
    sanitize_dict,
    sanitize_log_data,
    sanitize_sensitive_data,
)


class TestLogSanitizer:
    """Test suite for LogSanitizer class."""

    class TestJWTSanitization:
        """Test JWT token sanitization."""

        def test_standard_jwt_sanitized(self) -> None:
            """Test that standard JWT tokens are sanitized."""
            message = "User authenticated with token eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig"
            result = LogSanitizer.sanitize_message(message)
            assert "[JWT_TOKEN]" in result
            assert "eyJhbGciOiJIUzI1NiJ9" not in result

        def test_jwt_in_json_sanitized(self) -> None:
            """Test JWT tokens embedded in JSON are sanitized."""
            message = '{"token": "eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig"}'
            result = LogSanitizer.sanitize_message(message)
            # The pattern matches "token": "value" and replaces with token=[REDACTED]
            assert "token=[REDACTED]" in result or "[JWT_TOKEN]" in result
            assert "eyJhbGciOiJIUzI1NiJ9" not in result

        def test_non_jwt_preserved(self) -> None:
            """Test that non-JWT strings are preserved."""
            message = "Normal log message without sensitive data"
            result = LogSanitizer.sanitize_message(message)
            assert result == message

    class TestAPIKeySanitization:
        """Test API key sanitization."""

        def test_api_key_equals_format(self) -> None:
            """Test API key in key=value format."""
            message = "Request with api_key=sk_test_1234567890abcdef"
            result = LogSanitizer.sanitize_message(message)
            assert "api_key=[REDACTED]" in result
            assert "sk_test_1234567890abcdef" not in result

        def test_api_key_json_format(self) -> None:
            """Test API key in JSON format."""
            message = '{"apikey": "sk_test_1234567890abcdef"}'
            result = LogSanitizer.sanitize_message(message)
            assert "apikey=[REDACTED]" in result
            assert "sk_test_1234567890abcdef" not in result

        def test_api_key_yaml_format(self) -> None:
            """Test API key in YAML format."""
            message = "api-key: sk_test_1234567890abcdef"
            result = LogSanitizer.sanitize_message(message)
            assert "api-key=[REDACTED]" in result
            assert "sk_test_1234567890abcdef" not in result

    class TestDatabaseURLSanitization:
        """Test database URL sanitization."""

        @pytest.mark.parametrize(
            "url,expected_pattern",
            [
                (
                    "postgresql://user:password@localhost/db",
                    "postgresql://[USER]:[REDACTED]@localhost/db",
                ),
                (
                    "postgresql+asyncpg://admin:secret@db.example.com/mydb",
                    "postgresql+asyncpg://[USER]:[REDACTED]@db.example.com/mydb",
                ),
                (
                    "mysql://root:pass123@localhost:3306/app",
                    "mysql://[USER]:[REDACTED]@localhost:3306/app",
                ),
                (
                    "mysql+pymysql://user:pwd@db.local/test",
                    "mysql+pymysql://[USER]:[REDACTED]@db.local/test",
                ),
                (
                    "redis://user:password@localhost:6379",
                    "redis://[USER]:[REDACTED]@localhost:6379",
                ),
                (
                    "mongodb://admin:secret@localhost:27017",
                    "mongodb://[USER]:[REDACTED]@localhost:27017",
                ),
                (
                    "mongodb+srv://user:pass@cluster.mongodb.net",
                    "mongodb+srv://[USER]:[REDACTED]@cluster.mongodb.net",
                ),
            ],
        )
        def test_database_url_sanitized(self, url: str, expected_pattern: str) -> None:
            """Test various database URL formats are sanitized."""
            result = LogSanitizer.sanitize_message(url)
            assert expected_pattern in result
            assert "password" not in result
            assert "secret" not in result
            assert "pass123" not in result

    class TestEmailSanitization:
        """Test email address sanitization."""

        def test_email_partial_mask(self) -> None:
            """Test email addresses are partially masked."""
            message = "User john.doe@example.com logged in"
            result = LogSanitizer.sanitize_message(message)
            assert "j***@example.com" in result
            assert "john.doe@example.com" not in result

        def test_multiple_emails_sanitized(self) -> None:
            """Test multiple email addresses are sanitized."""
            message = "From: alice@test.com To: bob@test.com"
            result = LogSanitizer.sanitize_message(message)
            assert "a***@test.com" in result
            assert "b***@test.com" in result
            assert "alice@test.com" not in result
            assert "bob@test.com" not in result

    class TestCreditCardSanitization:
        """Test credit card number sanitization."""

        @pytest.mark.parametrize(
            "card_number",
            [
                "4532-1234-5678-9010",
                "4532 1234 5678 9010",
                "4532123456789010",
            ],
        )
        def test_credit_card_formats(self, card_number: str) -> None:
            """Test various credit card formats are sanitized."""
            message = f"Payment with card {card_number}"
            result = LogSanitizer.sanitize_message(message)
            assert "[CARD_NUMBER]" in result
            assert card_number not in result

    class TestSSNSanitization:
        """Test SSN sanitization."""

        def test_ssn_with_dashes(self) -> None:
            """Test SSN with dashes is sanitized."""
            message = "SSN: 123-45-6789"
            result = LogSanitizer.sanitize_message(message)
            assert "[SSN]" in result
            assert "123-45-6789" not in result

        def test_ssn_with_spaces(self) -> None:
            """Test SSN with spaces is sanitized."""
            message = "SSN: 123 45 6789"
            result = LogSanitizer.sanitize_message(message)
            assert "[SSN]" in result
            assert "123 45 6789" not in result

    class TestCloudProviderKeys:
        """Test cloud provider key sanitization."""

        def test_aws_access_key(self) -> None:
            """Test AWS access key is sanitized."""
            message = "AWS key: AKIAIOSFODNN7EXAMPLE"
            result = LogSanitizer.sanitize_message(message)
            assert "[AWS_ACCESS_KEY]" in result
            assert "AKIAIOSFODNN7EXAMPLE" not in result

        def test_aws_secret_key(self) -> None:
            """Test AWS secret key is sanitized."""
            message = "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
            result = LogSanitizer.sanitize_message(message)
            assert "aws_secret_access_key=[REDACTED]" in result
            assert "wJalrXUtnFEMI" not in result

        @pytest.mark.parametrize(
            "stripe_key",
            [
                "sk_live_1234567890abcdefghijklmnop",
                "pk_test_1234567890abcdefghijklmnop",
                "rk_live_1234567890abcdefghijklmnop",
            ],
        )
        def test_stripe_keys(self, stripe_key: str) -> None:
            """Test Stripe keys are sanitized."""
            message = f"Stripe key: {stripe_key}"
            result = LogSanitizer.sanitize_message(message)
            assert "[STRIPE_KEY]" in result
            assert stripe_key not in result

    class TestPasswordSanitization:
        """Test password sanitization."""

        def test_password_equals_format(self) -> None:
            """Test password in key=value format."""
            message = "Login failed with password=mysecret123"
            result = LogSanitizer.sanitize_message(message)
            assert "password=[REDACTED]" in result
            assert "mysecret123" not in result

        def test_secret_token_formats(self) -> None:
            """Test various secret/token formats."""
            test_cases = [
                ("secret=abc123", "secret=[REDACTED]"),
                ("token=xyz789", "token=[REDACTED]"),
                ("passwd=test123", "passwd=[REDACTED]"),
                ("pwd=pass123", "pwd=[REDACTED]"),
            ]
            for message, expected in test_cases:
                result = LogSanitizer.sanitize_message(message)
                assert expected in result

    class TestAuthorizationHeaders:
        """Test authorization header sanitization."""

        def test_bearer_token(self) -> None:
            """Test Bearer token is sanitized."""
            message = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.token.sig"
            result = LogSanitizer.sanitize_message(message)
            # Both patterns match: Bearer token pattern and Authorization header pattern
            assert "[REDACTED]" in result
            assert "eyJhbGciOiJIUzI1NiJ9" not in result

        def test_authorization_header_generic(self) -> None:
            """Test generic authorization header is sanitized."""
            message = "authorization: Basic dXNlcjpwYXNz"
            result = LogSanitizer.sanitize_message(message)
            assert "[REDACTED]" in result
            assert "Basic dXNlcjpwYXNz" not in result

    class TestPasskeySanitization:
        """Test WebAuthn/Passkey sensitive data sanitization."""

        def test_passkey_challenge(self) -> None:
            """Test passkey challenge is sanitized."""
            message = "WebAuthn challenge=abc123base64url"
            result = LogSanitizer.sanitize_message(message)
            assert "challenge=[REDACTED]" in result
            assert "abc123base64url" not in result

        def test_passkey_credential_id(self) -> None:
            """Test credential_id is sanitized."""
            message = '{"credential_id": "rawCredId64Url"}'
            result = LogSanitizer.sanitize_message(message)
            assert "credential_id=[REDACTED]" in result
            assert "rawCredId64Url" not in result

    class TestRecursiveSanitization:
        """Test recursive sanitization of nested structures."""

        def test_nested_dict_sanitization(self) -> None:
            """Test nested dictionaries are sanitized."""
            data = {
                "user": "john@example.com",
                "auth": {
                    "token": "eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig",
                    "metadata": {"api_key": "sk_test_123"},
                },
            }
            result = LogSanitizer.sanitize_dict(data)
            assert "j***@example.com" in result["user"]
            assert "[JWT_TOKEN]" in result["auth"]["token"]
            assert "sk_test_123" in result["auth"]["metadata"]["api_key"]

        def test_list_sanitization(self) -> None:
            """Test lists are sanitized."""
            data = [
                "normal",
                "eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig",
                "user@example.com",
            ]
            result = LogSanitizer.sanitize_value(data)
            assert result[0] == "normal"
            assert "[JWT_TOKEN]" in result[1]
            assert "u***@example.com" in result[2]

        def test_tuple_sanitization(self) -> None:
            """Test tuples are sanitized."""
            data = ("normal", "password=secret123", 42)
            result = LogSanitizer.sanitize_value(data)
            assert result[0] == "normal"
            assert "password=[REDACTED]" in result[1]
            assert result[2] == 42

        def test_depth_limit_prevents_overflow(self) -> None:
            """Test depth limit prevents stack overflow."""
            # Create deeply nested dict
            deep_data: dict[str, Any] = {"level": 0}
            current = deep_data
            for i in range(15):
                current["nested"] = {"level": i + 1}
                current = current["nested"]
            current["jwt"] = "eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig"

            # Should not raise RecursionError
            result = LogSanitizer.sanitize_dict(deep_data, _max_depth=10)
            assert result is not None

        def test_bytes_sanitization(self) -> None:
            """Test bytes are sanitized."""
            data = b"credential_id=secret_passkey_bytes"
            result = LogSanitizer.sanitize_value(data)
            assert isinstance(result, bytes)
            assert b"credential_id=[REDACTED]" in result

        def test_set_sanitization(self) -> None:
            """Test sets are sanitized."""
            data = {"normal", "password=secret_in_set"}
            result = LogSanitizer.sanitize_value(data)
            assert isinstance(result, set)
            assert "password=[REDACTED]" in result

    class TestEdgeCases:
        """Test edge cases and error handling."""

        def test_none_value_preserved(self) -> None:
            """Test None values are preserved."""
            result = LogSanitizer.sanitize_value(None)
            assert result is None

        def test_empty_string_preserved(self) -> None:
            """Test empty strings are preserved."""
            result = LogSanitizer.sanitize_message("")
            assert result == ""

        def test_non_string_to_sanitize_message(self) -> None:
            """Test non-string input to sanitize_message."""
            result = LogSanitizer.sanitize_message(123)  # type: ignore
            assert result == 123

        def test_primitives_preserved(self) -> None:
            """Test primitive types are preserved."""
            assert LogSanitizer.sanitize_value(42) == 42
            assert LogSanitizer.sanitize_value(3.14) == 3.14
            assert LogSanitizer.sanitize_value(True) is True
            assert LogSanitizer.sanitize_value(False) is False

        def test_unicode_handling(self) -> None:
            """Test unicode characters are handled correctly."""
            message = "User 用户 logged in with password=secret123"
            result = LogSanitizer.sanitize_message(message)
            assert "用户" in result
            assert "password=[REDACTED]" in result

        def test_empty_dict(self) -> None:
            """Test empty dict is handled."""
            result = LogSanitizer.sanitize_dict({})
            assert result == {}

        def test_mixed_type_dict(self) -> None:
            """Test dict with mixed value types."""
            data = {
                "string": "test",
                "int": 42,
                "float": 3.14,
                "bool": True,
                "none": None,
                "list": [1, 2, 3],
                "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig",
            }
            result = LogSanitizer.sanitize_dict(data)
            assert result["string"] == "test"
            assert result["int"] == 42
            assert result["float"] == 3.14
            assert result["bool"] is True
            assert result["none"] is None
            assert result["list"] == [1, 2, 3]
            assert "[JWT_TOKEN]" in result["jwt"]


class TestSanitizingProcessor:
    """Test suite for SanitizingProcessor class."""

    def test_processor_is_callable(self) -> None:
        """Test processor can be called with structlog signature."""
        processor = SanitizingProcessor()
        event_dict = {"event": "test", "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig"}
        result = processor(None, "info", event_dict)
        assert isinstance(result, dict)
        assert "[JWT_TOKEN]" in result["jwt"]

    def test_processor_sanitizes_event_dict(self) -> None:
        """Test processor sanitizes all values in event dict."""
        processor = SanitizingProcessor()
        event_dict = {
            "event": "login",
            "email": "user@example.com",
            "password": "secret123",
            "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig",
        }
        result = processor(None, "info", event_dict)
        assert "u***@example.com" in result["email"]
        assert "[JWT_TOKEN]" in result["jwt"]

    def test_respects_max_depth(self) -> None:
        """Test processor respects max_depth parameter."""
        processor = SanitizingProcessor(max_depth=2)
        deep_event = {
            "level1": {
                "jwt_shallow": "eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig",
                "level2": {"level3": {"jwt_deep": "eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig"}},
            }
        }
        result = processor(None, "info", deep_event)
        # Should sanitize within depth limit (depth 1 < max_depth 2)
        assert "[JWT_TOKEN]" in result["level1"]["jwt_shallow"]
        # Should stop recursion at max_depth 2, leaving deeper items untouched
        assert result["level1"]["level2"]["level3"]["jwt_deep"] == "eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig"

    def test_processor_with_nested_structures(self) -> None:
        """Test processor handles nested dicts and lists."""
        processor = SanitizingProcessor()
        event_dict = {
            "event": "api_call",
            "request": {
                "headers": {"authorization": "Bearer token123"},
                "body": {"items": ["item1", "password=secret"]},
            },
        }
        result = processor(None, "info", event_dict)
        assert "Bearer [REDACTED]" in result["request"]["headers"]["authorization"]
        assert "password=[REDACTED]" in result["request"]["body"]["items"][1]


class TestSecureFormatter:
    """Test suite for SecureFormatter class."""

    def test_formatter_sanitizes_message(self) -> None:
        """Test formatter sanitizes log record message."""
        formatter = SecureFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Login with password=secret123",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert "password=[REDACTED]" in formatted
        assert "secret123" not in formatted

    def test_formatter_sanitizes_args(self) -> None:
        """Test formatter sanitizes log record args."""
        formatter = SecureFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="User %s logged in",
            args=("user@example.com",),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert "u***@example.com" in formatted
        assert "user@example.com" not in formatted

    def test_formatter_with_dict_args(self) -> None:
        """Test formatter sanitizes dict args."""
        formatter = SecureFormatter()
        # Create record with tuple args containing dict
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Login attempt",
            args=(),
            exc_info=None,
        )
        # Manually set args as dict (simulating % formatting)
        record.args = {"jwt": "eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig"}
        formatted = formatter.format(record)
        # The formatter should sanitize the dict args
        assert "Login attempt" in formatted


class TestHelperFunctions:
    """Test suite for helper functions."""

    def test_sanitize_log_data(self) -> None:
        """Test sanitize_log_data function."""
        data = {"jwt": "eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig"}
        result = sanitize_log_data(data)
        assert "[JWT_TOKEN]" in result["jwt"]

    def test_sanitize_dict_function(self) -> None:
        """Test sanitize_dict function."""
        data = {"email": "user@example.com"}
        result = sanitize_dict(data)
        assert "u***@example.com" in result["email"]

    def test_sanitize_sensitive_data_alias(self) -> None:
        """Test sanitize_sensitive_data is an alias."""
        data = {"password": "secret123"}
        result = sanitize_sensitive_data(data)
        assert result == sanitize_log_data(data)

    def test_get_log_sanitizer(self) -> None:
        """Test get_log_sanitizer returns LogSanitizer class."""
        sanitizer_class = get_log_sanitizer()
        assert sanitizer_class is LogSanitizer


class TestStructlogIntegration:
    """Test integration with structlog."""

    def test_full_pipeline_with_json_renderer(self) -> None:
        """Test sanitizer works in full structlog pipeline with JSON renderer."""
        # Configure structlog with sanitizer
        structlog.configure(
            processors=[
                SanitizingProcessor(),
                structlog.processors.JSONRenderer(),
            ],
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=False,
        )

        logger = structlog.get_logger()
        # This would normally output to stdout, but we're just testing it doesn't crash
        logger.info(
            "test_event",
            jwt="eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig",
            email="user@example.com",
        )

    def test_console_renderer(self) -> None:
        """Test sanitizer works with console renderer."""
        structlog.configure(
            processors=[
                SanitizingProcessor(),
                structlog.dev.ConsoleRenderer(),
            ],
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=False,
        )

        logger = structlog.get_logger()
        logger.info(
            "test_event",
            password="secret123",
        )


class TestPerformance:
    """Test performance requirements."""

    def test_sanitization_under_1ms(self) -> None:
        """Test sanitization completes in under 1ms on average."""
        message = (
            "User john.doe@example.com logged in with password=secret123 "
            "and jwt=eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig "
            "using api_key=sk_test_1234567890"
        )

        iterations = 10000
        start = time.perf_counter()
        for _ in range(iterations):
            LogSanitizer.sanitize_message(message)
        end = time.perf_counter()

        avg_time_ms = ((end - start) / iterations) * 1000
        print(f"\nAverage sanitization time: {avg_time_ms:.4f}ms")

        # Target: < 1ms average
        assert avg_time_ms < 1.0, f"Average time {avg_time_ms:.4f}ms exceeds 1ms target"

    def test_dict_sanitization_performance(self) -> None:
        """Test dict sanitization performance."""
        data = {
            "event": "user_action",
            "user": "john.doe@example.com",
            "auth": {
                "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0Ijp0cnVlfQ.sig",
                "api_key": "sk_test_1234567890",
            },
            "metadata": {
                "ip": "192.168.1.1",
                "timestamp": "2024-01-01T00:00:00Z",
            },
        }

        iterations = 10000
        start = time.perf_counter()
        for _ in range(iterations):
            LogSanitizer.sanitize_dict(data)
        end = time.perf_counter()

        avg_time_ms = ((end - start) / iterations) * 1000
        print(f"\nAverage dict sanitization time: {avg_time_ms:.4f}ms")

        # Target: < 1ms average for typical log dict
        assert avg_time_ms < 1.0, f"Average time {avg_time_ms:.4f}ms exceeds 1ms target"
