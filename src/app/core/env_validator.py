"""Environment variable validation with severity levels."""

import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.app.core.logger import get_logger

logger = get_logger(__name__)


class ValidationSeverity(Enum):
    ERROR = "error"  # Blocks startup
    WARNING = "warning"  # Logs warning, continues
    INFO = "info"  # Informational


@dataclass
class ValidationResult:
    key: str
    severity: ValidationSeverity
    message: str


class EnvironmentValidator:
    """
    Environment variable validator with severity levels.

    Complements Pydantic validation with:
    - Insecure default detection
    - Pattern-based validation
    - Production-specific checks
    """

    INSECURE_DEFAULTS = ["password", "123456", "admin", "root", "secret", "changeme"]

    VALIDATION_RULES: dict[str, dict[str, Any]] = {
        "ADMIN_PASSWORD": {
            "min_length": 16,
            "description": "Admin password should be very strong",
        },
        "POSTGRES_PASSWORD": {
            "min_length": 8,
            "description": "Database password should be strong",
        },
    }

    @classmethod
    def validate_all(cls) -> list[ValidationResult]:
        """Run all validations and return results."""
        results: list[ValidationResult] = []

        # Check validation rules
        for var, rules in cls.VALIDATION_RULES.items():
            value = os.getenv(var)
            if value is None:
                continue

            result = cls._validate_variable(var, value, rules)
            if result:
                results.append(result)

        # Check insecure defaults
        results.extend(cls._check_insecure_defaults())

        # Check production settings
        results.extend(cls._check_production_settings())

        # Check AI provider configuration
        results.extend(cls._check_ai_providers())

        return results

    @classmethod
    def _validate_variable(cls, var: str, value: str, rules: dict[str, Any]) -> ValidationResult | None:
        """Validate a single variable."""
        if "min_length" in rules and len(value) < rules["min_length"]:
            return ValidationResult(
                key=var,
                severity=ValidationSeverity.ERROR,
                message=f"{rules['description']} (minimum {rules['min_length']} characters)",
            )

        if "pattern" in rules and not re.match(rules["pattern"], value):
            return ValidationResult(
                key=var,
                severity=ValidationSeverity.ERROR,
                message=f"{var} does not match required pattern",
            )

        return None

    @classmethod
    def _check_insecure_defaults(cls) -> list[ValidationResult]:
        """Check for insecure default passwords."""
        results = []

        for var in ["ADMIN_PASSWORD", "POSTGRES_PASSWORD"]:
            value = os.getenv(var)
            if value and value.lower() in cls.INSECURE_DEFAULTS:
                results.append(
                    ValidationResult(
                        key=var,
                        severity=ValidationSeverity.ERROR,
                        message=f"{var} uses an insecure default password",
                    )
                )

        return results

    @classmethod
    def _check_production_settings(cls) -> list[ValidationResult]:
        """Check production-specific settings."""
        results = []

        if os.getenv("ENVIRONMENT") == "production":
            if os.getenv("DEBUG", "").lower() in ["true", "1"]:
                results.append(
                    ValidationResult(
                        key="DEBUG",
                        severity=ValidationSeverity.ERROR,
                        message="DEBUG should not be enabled in production",
                    )
                )

        return results

    @classmethod
    def _check_ai_providers(cls) -> list[ValidationResult]:
        """Check AI provider configuration."""
        results: list[ValidationResult] = []

        ai_enabled = os.getenv("AI_ENABLED", "true").lower() in ["true", "1"]
        if not ai_enabled:
            return results

        active_provider = os.getenv("AI_ACTIVE_PROVIDER", "ollama").lower()

        if active_provider == "ollama":
            ollama_base_url = os.getenv("OLLAMA_BASE_URL")
            if not ollama_base_url:
                results.append(
                    ValidationResult(
                        key="OLLAMA_BASE_URL",
                        severity=ValidationSeverity.ERROR,
                        message="OLLAMA_BASE_URL is required when AI_ACTIVE_PROVIDER is 'ollama'",
                    )
                )
            else:
                try:
                    import httpx
                except ImportError:
                    results.append(
                        ValidationResult(
                            key="OLLAMA_BASE_URL",
                            severity=ValidationSeverity.INFO,
                            message=(
                                f"OLLAMA_BASE_URL is set to {ollama_base_url} "
                                "(connectivity check skipped - httpx not available)"
                            ),
                        )
                    )
                else:
                    # Use sync client to avoid asyncio.run() issues when called from async context
                    try:
                        response = httpx.get(f"{ollama_base_url}/api/tags", timeout=5.0)
                        is_reachable = response.status_code == 200
                        if not is_reachable:
                            results.append(
                                ValidationResult(
                                    key="OLLAMA_BASE_URL",
                                    severity=ValidationSeverity.WARNING,
                                    message=f"Ollama at {ollama_base_url} is not reachable",
                                )
                            )
                    except Exception:
                        results.append(
                            ValidationResult(
                                key="OLLAMA_BASE_URL",
                                severity=ValidationSeverity.WARNING,
                                message=f"Could not verify Ollama connectivity at {ollama_base_url}",
                            )
                        )

        return results

    @classmethod
    def get_summary(cls) -> dict[str, int]:
        """Get validation summary."""
        results = cls.validate_all()
        return {
            "errors": len([r for r in results if r.severity == ValidationSeverity.ERROR]),
            "warnings": len([r for r in results if r.severity == ValidationSeverity.WARNING]),
            "info": len([r for r in results if r.severity == ValidationSeverity.INFO]),
        }

    @classmethod
    def validate_and_log(cls) -> bool:
        """Validate and log results. Returns False if any ERROR found."""
        results = cls.validate_all()

        has_errors = False
        for result in results:
            if result.severity == ValidationSeverity.ERROR:
                logger.error("[ENV VALIDATION ERROR] :", key=result.key, message=result.message)
                has_errors = True
            elif result.severity == ValidationSeverity.WARNING:
                logger.warning("[ENV VALIDATION WARNING] :", key=result.key, message=result.message)
            else:
                logger.info("[ENV VALIDATION INFO] :", key=result.key, message=result.message)

        return not has_errors
