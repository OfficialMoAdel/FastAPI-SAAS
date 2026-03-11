"""Unit tests for SSO JSON exception handling with orjson.

Tests verify that the SSO router correctly catches orjson.JSONDecodeError
when parsing Apple Sign-In user data, not stdlib json.JSONDecodeError.
"""

import orjson
import pytest

from src.app.core.json_utils import json_loads


class TestOrjsonExceptionHandling:
    """Tests for orjson exception handling in SSO user data parsing."""

    def test_orjson_exception_caught(self) -> None:
        """Test that malformed JSON raises orjson.JSONDecodeError.

        This verifies that json_loads (which uses orjson internally)
        raises orjson.JSONDecodeError, not json.JSONDecodeError.
        """
        malformed_json = '{"name": "John", "email": invalid}'

        with pytest.raises(orjson.JSONDecodeError):
            json_loads(malformed_json)

    def test_valid_json_parsed(self) -> None:
        """Test that valid Apple user JSON is parsed correctly.

        Apple sends user data as JSON on first authorization with structure:
        {"name": {"firstName": "John", "lastName": "Doe"}, "email": "user@example.com"}
        """
        valid_apple_user = '{"name": {"firstName": "John", "lastName": "Doe"}, "email": "user@example.com"}'

        result = json_loads(valid_apple_user)

        assert result["name"]["firstName"] == "John"
        assert result["name"]["lastName"] == "Doe"
        assert result["email"] == "user@example.com"

    def test_empty_user_handled(self) -> None:
        """Test that empty string raises orjson.JSONDecodeError.

        Empty strings should raise an exception, not return None or empty dict.
        The router handles this by catching the exception and leaving user_data as None.
        """
        empty_string = ""

        with pytest.raises(orjson.JSONDecodeError):
            json_loads(empty_string)

    def test_none_like_strings_raise(self) -> None:
        """Test that null-like strings are handled correctly."""
        # "null" is valid JSON
        assert json_loads("null") is None

        # But invalid strings should raise
        with pytest.raises(orjson.JSONDecodeError):
            json_loads("undefined")

    def test_partial_json_raises(self) -> None:
        """Test that incomplete JSON raises orjson.JSONDecodeError."""
        partial_json = '{"name": "John"'

        with pytest.raises(orjson.JSONDecodeError):
            json_loads(partial_json)

    def test_exception_type_is_orjson(self) -> None:
        """Explicitly verify the exception type is orjson, not stdlib json.

        This is the core test for the bug fix - we must catch orjson.JSONDecodeError,
        not json.JSONDecodeError, since json_loads uses orjson internally.
        """
        import json as stdlib_json

        malformed = '{"broken":'

        # Verify orjson exception is raised
        try:
            json_loads(malformed)
            pytest.fail("Expected orjson.JSONDecodeError to be raised")
        except orjson.JSONDecodeError:
            pass  # Expected behavior
        except stdlib_json.JSONDecodeError:
            pytest.fail("stdlib json.JSONDecodeError was raised - this should be orjson.JSONDecodeError")


class TestSSOUserDataParsing:
    """Tests simulating the SSO router's user data parsing logic."""

    def _parse_user_data(self, user: str | None) -> dict | None:
        """Simulate the SSO router's user data parsing logic.

        This mirrors the logic in src/app/modules/sso/router.py:handle_post_callback
        """
        user_data = None
        if user:
            try:
                user_data = json_loads(user)
            except orjson.JSONDecodeError:
                pass
        return user_data

    def test_valid_user_parsed(self) -> None:
        """Test valid Apple user JSON is parsed."""
        user = '{"name": {"firstName": "Jane", "lastName": "Smith"}}'

        result = self._parse_user_data(user)

        assert result is not None
        assert result["name"]["firstName"] == "Jane"

    def test_malformed_user_returns_none(self) -> None:
        """Test malformed JSON returns None (exception caught)."""
        user = '{"name": broken}'

        result = self._parse_user_data(user)

        assert result is None

    def test_empty_user_returns_none(self) -> None:
        """Test empty string returns None (exception caught)."""
        result = self._parse_user_data("")

        assert result is None

    def test_none_user_returns_none(self) -> None:
        """Test None input returns None (no parsing attempted)."""
        result = self._parse_user_data(None)

        assert result is None

    def test_whitespace_only_returns_none(self) -> None:
        """Test whitespace-only string returns None (exception caught)."""
        result = self._parse_user_data("   ")

        assert result is None
