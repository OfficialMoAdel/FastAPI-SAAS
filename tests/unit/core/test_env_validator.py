import os
import sys
from unittest import mock

import pytest

from src.app.core.env_validator import (
    EnvironmentValidator,
    ValidationSeverity,
)


class TestEnvironmentValidator:
    @pytest.fixture
    def mock_env(self):
        """Mock environment variables."""
        with mock.patch.dict(os.environ, {}, clear=True):
            yield

    def test_validate_variable_min_length(self):
        """Test validation of minimum length."""
        rules = {"min_length": 5, "description": "Too short"}

        # Too short
        result = EnvironmentValidator._validate_variable("TEST_VAR", "1234", rules)
        assert result is not None
        assert result.severity == ValidationSeverity.ERROR
        assert "minimum 5 characters" in result.message

        # Valid length
        result = EnvironmentValidator._validate_variable("TEST_VAR", "12345", rules)
        assert result is None

    def test_validate_variable_pattern(self):
        """Test validation of regex pattern."""
        rules = {"pattern": r"^\d+$", "description": "Must be digits"}

        # Invalid pattern
        result = EnvironmentValidator._validate_variable("TEST_VAR", "abc", rules)
        assert result is not None
        assert result.severity == ValidationSeverity.ERROR
        assert "does not match required pattern" in result.message

        # Valid pattern
        result = EnvironmentValidator._validate_variable("TEST_VAR", "123", rules)
        assert result is None

    def test_check_insecure_defaults(self, mock_env):
        """Test detection of insecure default passwords."""
        # Test insecure password
        with mock.patch.dict(os.environ, {"ADMIN_PASSWORD": "admin"}):
            results = EnvironmentValidator._check_insecure_defaults()
            assert len(results) == 1
            assert results[0].key == "ADMIN_PASSWORD"
            assert "insecure default" in results[0].message
            assert results[0].severity == ValidationSeverity.ERROR

        # Test secure password
        with mock.patch.dict(os.environ, {"ADMIN_PASSWORD": "StrongPassword123!"}):
            results = EnvironmentValidator._check_insecure_defaults()
            assert len(results) == 0

    def test_check_production_settings(self, mock_env):
        """Test production settings validation."""
        # Test DEBUG enabled in production
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "production", "DEBUG": "true"}):
            results = EnvironmentValidator._check_production_settings()
            assert len(results) == 1
            assert results[0].key == "DEBUG"
            assert "DEBUG should not be enabled" in results[0].message
            assert results[0].severity == ValidationSeverity.ERROR

        # Test DEBUG disabled in production
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "production", "DEBUG": "false"}):
            results = EnvironmentValidator._check_production_settings()
            assert len(results) == 0

        # Test DEBUG enabled in local
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "local", "DEBUG": "true"}):
            results = EnvironmentValidator._check_production_settings()
            assert len(results) == 0

    def test_validate_all(self, mock_env):
        """Test comprehensive validation."""
        env_vars = {
            "ADMIN_PASSWORD": "admin",  # Insecure default (Error)
            "POSTGRES_PASSWORD": "short",  # Too short (Error)
            "ENVIRONMENT": "production",
            "DEBUG": "true",  # Debug in prod (Error)
        }

        with mock.patch.dict(os.environ, env_vars):
            results = EnvironmentValidator.validate_all()

            # Should have 3 errors
            assert len(results) >= 3
            keys = [r.key for r in results]
            assert "ADMIN_PASSWORD" in keys
            assert "POSTGRES_PASSWORD" in keys
            assert "DEBUG" in keys

    def test_get_summary(self, mock_env):
        """Test summary generation."""
        env_vars = {
            "ADMIN_PASSWORD": "admin",  # Error
        }

        with mock.patch.dict(os.environ, env_vars):
            summary = EnvironmentValidator.get_summary()
            assert summary["errors"] >= 1
            assert isinstance(summary["warnings"], int)
            assert isinstance(summary["info"], int)

    @mock.patch("src.app.core.env_validator.logger")
    def test_validate_and_log_failure(self, mock_logger, mock_env):
        """Test logging and failure return."""
        env_vars = {"ADMIN_PASSWORD": "admin"}  # Error

        with mock.patch.dict(os.environ, env_vars):
            success = EnvironmentValidator.validate_and_log()

            assert success is False
            mock_logger.error.assert_called()

    @mock.patch("src.app.core.env_validator.logger")
    def test_validate_and_log_success(self, mock_logger, mock_env):
        """Test logging and success return."""
        env_vars = {
            "ADMIN_PASSWORD": "StrongPassword123!",
            "POSTGRES_PASSWORD": "StrongDBPassword123!",
            "ENVIRONMENT": "production",
            "DEBUG": "false",
            "AI_ENABLED": "false",  # Disable AI to avoid Ollama connectivity check
        }

        with mock.patch.dict(os.environ, env_vars):
            success = EnvironmentValidator.validate_and_log()

            assert success is True
            # Should not log errors
            mock_logger.error.assert_not_called()

    def test_check_ai_providers_disabled(self, mock_env):
        """Test AI provider validation when AI is disabled."""
        env_vars = {"AI_ENABLED": "false"}

        with mock.patch.dict(os.environ, env_vars):
            results = EnvironmentValidator._check_ai_providers()
            assert len(results) == 0

    def test_check_ai_providers_ollama_missing_url(self, mock_env):
        """Test Ollama validation when base URL is missing."""
        env_vars = {"AI_ENABLED": "true", "AI_ACTIVE_PROVIDER": "ollama"}

        with mock.patch.dict(os.environ, env_vars):
            results = EnvironmentValidator._check_ai_providers()
            assert len(results) == 1
            assert results[0].key == "OLLAMA_BASE_URL"
            assert "required" in results[0].message.lower()
            assert results[0].severity == ValidationSeverity.ERROR

    @mock.patch("httpx.AsyncClient")
    def test_check_ai_providers_ollama_unreachable(self, mock_client, mock_env):
        """Test Ollama validation when server is unreachable."""
        mock_response = mock.Mock()
        mock_response.status_code = 500
        mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

        env_vars = {
            "AI_ENABLED": "true",
            "AI_ACTIVE_PROVIDER": "ollama",
            "OLLAMA_BASE_URL": "http://localhost:11434",
        }

        with mock.patch.dict(os.environ, env_vars):
            results = EnvironmentValidator._check_ai_providers()
            assert len(results) == 1
            assert results[0].key == "OLLAMA_BASE_URL"
            assert "not reachable" in results[0].message.lower()
            assert results[0].severity == ValidationSeverity.WARNING

    @mock.patch("httpx.AsyncClient")
    def test_check_ai_providers_ollama_reachable(self, mock_client, mock_env):
        """Test Ollama validation when server is reachable."""
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

        env_vars = {
            "AI_ENABLED": "true",
            "AI_ACTIVE_PROVIDER": "ollama",
            "OLLAMA_BASE_URL": "http://localhost:11434",
        }

        with mock.patch.dict(os.environ, env_vars):
            results = EnvironmentValidator._check_ai_providers()
            assert len(results) == 0
