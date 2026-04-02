from unittest.mock import patch

import pytest
from fastapi import FastAPI

from src.app.core.config import settings
from src.app.core.env_validator import EnvironmentValidator, ValidationResult, ValidationSeverity
from src.app.main import lifespan_with_admin


@pytest.mark.asyncio
class TestEnvValidationStartup:
    """Integration tests for environment validation during application startup."""

    async def test_startup_valid_config(self):
        """Test that application starts successfully with valid configuration."""
        app = FastAPI()

        # Mock validate_and_log to return True (valid)
        with patch.object(EnvironmentValidator, "validate_and_log", return_value=True):
            # Should not raise exception
            async with lifespan_with_admin(app):
                pass

    async def test_startup_blocks_on_error_in_production(self):
        """Test that application fails to start in production if validation fails."""
        app = FastAPI()

        # Simulate Production Environment
        with patch.object(settings.env, "ENVIRONMENT", "production"):
            # Mock validation to fail
            with patch.object(EnvironmentValidator, "validate_and_log", return_value=False):
                # Verify RuntimeError is raised
                with pytest.raises(RuntimeError) as excinfo:
                    async with lifespan_with_admin(app):
                        pass

                assert "Environment validation failed" in str(excinfo.value)
                assert "refusing to start in production" in str(excinfo.value)

    async def test_startup_allows_error_in_development(self):
        """Test that application allows startup with validation errors in non-production environments."""
        app = FastAPI()

        # Simulate Development Environment
        with patch.object(settings.env, "ENVIRONMENT", "development"):
            # Mock validation to fail
            with patch.object(EnvironmentValidator, "validate_and_log", return_value=False):
                # Should NOT raise exception
                async with lifespan_with_admin(app):
                    pass

    async def test_validation_logging(self, caplog):
        """Test that validation results are correctly logged."""
        # Setup specific logging capture
        caplog.set_level("INFO")

        # Create test results
        test_results = [
            ValidationResult(key="TEST_ERR", severity=ValidationSeverity.ERROR, message="Error msg"),
            ValidationResult(key="TEST_WARN", severity=ValidationSeverity.WARNING, message="Warn msg"),
            ValidationResult(key="TEST_INFO", severity=ValidationSeverity.INFO, message="Info msg"),
        ]

        # Mock validate_all to return our test results
        with patch.object(EnvironmentValidator, "validate_all", return_value=test_results):
            EnvironmentValidator.validate_and_log()

            # Verify logs
            assert "TEST_ERR" in caplog.text
            assert "Error msg" in caplog.text
            assert "TEST_WARN" in caplog.text
            assert "Warn msg" in caplog.text
            assert "TEST_INFO" in caplog.text
            assert "Info msg" in caplog.text

    async def test_real_validation_logic(self, monkeypatch):
        """Test actual validation logic against mocked environment variables."""
        # 1. Test Insecure Default
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")  # Known insecure password

        results = EnvironmentValidator._check_insecure_defaults()
        assert any(r.key == "ADMIN_PASSWORD" and r.severity == ValidationSeverity.ERROR for r in results)

        # 2. Test Production Settings
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("DEBUG", "true")

        results = EnvironmentValidator._check_production_settings()
        assert any(r.key == "DEBUG" and r.severity == ValidationSeverity.ERROR for r in results)

    async def test_custom_rule_validation(self, monkeypatch):
        """Test validation with a custom rule injection."""
        # Inject a temporary rule
        original_rules = EnvironmentValidator.VALIDATION_RULES.copy()
        try:
            EnvironmentValidator.VALIDATION_RULES["TEST_VAR"] = {"min_length": 5, "description": "Test variable"}

            # Case 1: Invalid value
            monkeypatch.setenv("TEST_VAR", "123")
            results = EnvironmentValidator.validate_all()
            assert any(r.key == "TEST_VAR" and r.severity == ValidationSeverity.ERROR for r in results)

            # Case 2: Valid value
            monkeypatch.setenv("TEST_VAR", "12345")
            results = EnvironmentValidator.validate_all()
            assert not any(r.key == "TEST_VAR" for r in results)

        finally:
            # Restore original rules
            EnvironmentValidator.VALIDATION_RULES = original_rules
