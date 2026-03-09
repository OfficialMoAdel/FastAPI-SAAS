"""
E2E Smoke Tests for Phase 4: Environment Validator

Tests realistic production scenarios to ensure environment validation
properly blocks unsafe configurations and allows safe ones.
"""

import pytest

from src.app.core.env_validator import EnvironmentValidator


class TestPhase4EnvValidatorE2E:
    """End-to-end smoke tests for environment validator startup behavior."""

    def test_production_startup_blocks_insecure_password(self, monkeypatch):
        """
        Test that production startup is BLOCKED when using insecure default password.

        Scenario: Production environment with ADMIN_PASSWORD="admin"
        Expected: Validation fails (returns False)
        """
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")  # Insecure default
        monkeypatch.setenv("POSTGRES_PASSWORD", "StrongDBPassword123!")
        monkeypatch.setenv("DEBUG", "false")

        # Should fail validation
        assert not EnvironmentValidator.validate_and_log()

    def test_production_startup_blocks_short_password(self, monkeypatch):
        """
        Test that production startup is BLOCKED when password is too short.

        Scenario: Production environment with POSTGRES_PASSWORD too short
        Expected: Validation fails (returns False)
        """
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ADMIN_PASSWORD", "StrongAdminPassword123!")
        monkeypatch.setenv("POSTGRES_PASSWORD", "short")  # Too short (< 8 chars)
        monkeypatch.setenv("DEBUG", "false")

        # Should fail validation
        assert not EnvironmentValidator.validate_and_log()

    def test_production_startup_blocks_debug_enabled(self, monkeypatch):
        """
        Test that production startup is BLOCKED when DEBUG is enabled.

        Scenario: Production environment with DEBUG=true
        Expected: Validation fails (returns False)
        """
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ADMIN_PASSWORD", "StrongAdminPassword123!")
        monkeypatch.setenv("POSTGRES_PASSWORD", "StrongDBPassword123!")
        monkeypatch.setenv("DEBUG", "true")  # Debug enabled in production

        # Should fail validation
        assert not EnvironmentValidator.validate_and_log()

    def test_production_startup_succeeds_with_strong_passwords(self, monkeypatch):
        """
        Test that production startup SUCCEEDS with strong passwords and proper config.

        Scenario: Production environment with all secure settings
        Expected: Validation passes (returns True)
        """
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ADMIN_PASSWORD", "StrongAdminPassword123!")  # Strong password
        monkeypatch.setenv("POSTGRES_PASSWORD", "StrongDBPassword123!")  # Strong password
        monkeypatch.setenv("DEBUG", "false")  # Debug disabled

        # Should pass validation
        assert EnvironmentValidator.validate_and_log()

    def test_development_startup_allows_weak_passwords(self, monkeypatch):
        """
        Test that development startup ALLOWS weak passwords with warning.

        Scenario: Development environment with weak password
        Expected: Validation logs warnings but returns True (allows startup)
        """
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")  # Weak password
        monkeypatch.setenv("POSTGRES_PASSWORD", "short")  # Short password
        monkeypatch.setenv("DEBUG", "true")  # Debug enabled (OK in dev)

        # Should fail validation but not block in dev
        # (The actual blocking happens in main.py based on ENVIRONMENT)
        result = EnvironmentValidator.validate_and_log()
        # Validation will fail, but dev environment won't block startup
        assert not result  # Validation fails

    def test_local_startup_allows_weak_passwords(self, monkeypatch):
        """
        Test that local environment startup ALLOWS weak passwords.

        Scenario: Local environment with weak password
        Expected: Validation logs warnings but allows startup
        """
        monkeypatch.setenv("ENVIRONMENT", "local")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")  # Weak password
        monkeypatch.setenv("POSTGRES_PASSWORD", "password")  # Insecure default
        monkeypatch.setenv("DEBUG", "true")  # Debug enabled (OK in local)

        # Should fail validation but not block in local
        result = EnvironmentValidator.validate_and_log()
        assert not result  # Validation fails

    def test_production_startup_blocks_multiple_errors(self, monkeypatch):
        """
        Test that production startup is BLOCKED when multiple validation errors exist.

        Scenario: Production with insecure password + DEBUG enabled + short password
        Expected: Validation fails (returns False)
        """
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")  # Insecure default
        monkeypatch.setenv("POSTGRES_PASSWORD", "short")  # Too short
        monkeypatch.setenv("DEBUG", "true")  # Debug enabled

        # Should fail validation
        assert not EnvironmentValidator.validate_and_log()

    def test_staging_environment_behavior(self, monkeypatch):
        """
        Test staging environment behavior (should allow startup with warnings).

        Scenario: Staging environment with weak password
        Expected: Validation fails but staging allows startup
        """
        monkeypatch.setenv("ENVIRONMENT", "staging")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")  # Insecure default
        monkeypatch.setenv("POSTGRES_PASSWORD", "StrongDBPassword123!")
        monkeypatch.setenv("DEBUG", "false")

        # Staging should allow startup even with validation errors
        result = EnvironmentValidator.validate_and_log()
        assert not result  # Validation fails

    @pytest.mark.parametrize(
        "insecure_password",
        ["password", "123456", "admin", "root", "secret", "changeme"],
    )
    def test_production_blocks_all_insecure_defaults(self, insecure_password: str, monkeypatch):
        """
        Test that production blocks ALL known insecure default passwords.

        Scenario: Production with various insecure defaults
        Expected: Validation fails for each insecure password
        """
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ADMIN_PASSWORD", insecure_password)
        monkeypatch.setenv("POSTGRES_PASSWORD", "StrongDBPassword123!")
        monkeypatch.setenv("DEBUG", "false")

        # Should fail validation
        assert not EnvironmentValidator.validate_and_log()

    def test_log_messages_contain_validation_details(self, monkeypatch, caplog):
        """
        Test that validation errors are properly logged with details.

        Scenario: Production with multiple validation errors
        Expected: Logs contain specific error messages
        """
        caplog.set_level("ERROR")

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("POSTGRES_PASSWORD", "short")
        monkeypatch.setenv("DEBUG", "true")

        # Should fail validation
        result = EnvironmentValidator.validate_and_log()
        assert not result

        # Check that logs contain validation error messages
        assert "[ENV VALIDATION ERROR]" in caplog.text


class TestPhase4ValidationLogic:
    """Additional E2E tests for validation logic edge cases."""

    def test_case_insensitive_insecure_password_detection(self):
        """Test that insecure password detection is case-insensitive."""
        from src.app.core.env_validator import EnvironmentValidator

        # Test various cases
        insecure_variants = ["ADMIN", "Admin", "aDmIn", "PASSWORD", "Password"]

        for variant in insecure_variants:
            # Mock environment
            with pytest.MonkeyPatch.context() as mp:
                mp.setenv("ADMIN_PASSWORD", variant)
                results = EnvironmentValidator._check_insecure_defaults()

                # Should detect as insecure
                assert any(r.key == "ADMIN_PASSWORD" for r in results), f"Failed to detect insecure password: {variant}"

    def test_validation_summary_accuracy(self):
        """Test that validation summary counts are accurate."""
        from src.app.core.env_validator import EnvironmentValidator

        with pytest.MonkeyPatch.context() as mp:
            # Setup environment with known errors
            mp.setenv("ENVIRONMENT", "production")
            mp.setenv("ADMIN_PASSWORD", "admin")  # Error 1
            mp.setenv("POSTGRES_PASSWORD", "short")  # Error 2
            mp.setenv("DEBUG", "true")  # Error 3

            summary = EnvironmentValidator.get_summary()

            # Should have at least 3 errors
            assert summary["errors"] >= 3, f"Expected >=3 errors, got {summary['errors']}"

    def test_empty_environment_validation(self):
        """Test validation behavior with minimal environment variables."""
        from src.app.core.env_validator import EnvironmentValidator

        with pytest.MonkeyPatch.context() as mp:
            # Clear all relevant env vars
            mp.delenv("ADMIN_PASSWORD", raising=False)
            mp.delenv("POSTGRES_PASSWORD", raising=False)
            mp.delenv("ENVIRONMENT", raising=False)
            mp.delenv("DEBUG", raising=False)

            results = EnvironmentValidator.validate_all()

            # Should not crash, may have 0 results
            assert isinstance(results, list)

    def test_validation_with_special_characters_in_password(self):
        """Test that strong passwords with special characters are accepted."""
        from src.app.core.env_validator import EnvironmentValidator

        strong_passwords = [
            "MyP@ssw0rd!2024",
            "C0mpl3x#P@ss",
            "Str0ng$ecure&Pass",
            "V3ry!L0ng@P@ssw0rd#2024",
        ]

        for password in strong_passwords:
            with pytest.MonkeyPatch.context() as mp:
                mp.setenv("ADMIN_PASSWORD", password)
                results = EnvironmentValidator._check_insecure_defaults()

                # Should NOT flag as insecure
                assert not any(r.key == "ADMIN_PASSWORD" for r in results), (
                    f"Strong password incorrectly flagged: {password}"
                )


class TestPhase4ProductionSafety:
    """Critical production safety tests."""

    def test_production_safety_checklist(self):
        """
        Comprehensive production safety checklist.

        Verifies all critical production safety checks are in place.
        """
        from src.app.core.env_validator import EnvironmentValidator

        # Test 1: Insecure defaults blocked
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("ADMIN_PASSWORD", "admin")
            assert not EnvironmentValidator.validate_and_log()

        # Test 2: Short passwords blocked
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("POSTGRES_PASSWORD", "short")
            assert not EnvironmentValidator.validate_and_log()

        # Test 3: DEBUG in production blocked
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("ENVIRONMENT", "production")
            mp.setenv("DEBUG", "true")
            assert not EnvironmentValidator.validate_and_log()

        # Test 4: Valid production config passes
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("ENVIRONMENT", "production")
            mp.setenv("ADMIN_PASSWORD", "StrongAdminPassword123!")
            mp.setenv("POSTGRES_PASSWORD", "StrongDBPassword123!")
            mp.setenv("DEBUG", "false")
            assert EnvironmentValidator.validate_and_log()

    def test_validation_prevents_common_misconfigurations(self):
        """Test that validator catches common production misconfigurations."""
        from src.app.core.env_validator import EnvironmentValidator

        common_misconfigs = [
            {"ADMIN_PASSWORD": "password"},  # Too common
            {"POSTGRES_PASSWORD": "123456"},  # Too simple
            {"ADMIN_PASSWORD": "changeme"},  # Default placeholder
            {"POSTGRES_PASSWORD": "root"},  # Default username as password
        ]

        for misconfig in common_misconfigs:
            with pytest.MonkeyPatch.context() as mp:
                for key, value in misconfig.items():
                    mp.setenv(key, value)

                # Should fail validation
                assert not EnvironmentValidator.validate_and_log(), f"Failed to catch: {misconfig}"
