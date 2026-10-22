"""Environment variable validation with severity levels.

P2-13: sanctioned raw-environment reader. This validator deliberately
inspects the raw process environment (not Pydantic settings) because its
contract is pre-Pydantic operator-input validation: insecure defaults,
pattern checks, production guards, and provider reachability. Tests pin
this contract via os.environ patching.
"""

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

        # S-37 FIX: read the TYPED settings, not os.getenv. Three problems with
        # the old reads:
        #   * AI_ENABLED / AI_ACTIVE_PROVIDER were declared nowhere, so
        #     os.getenv was the only source and the flat-config delegation had
        #     nothing to resolve;
        #   * OLLAMA_BASE_URL HAS a Pydantic default ("http://localhost:11434"),
        #     which os.getenv can never see — so a perfectly valid production
        #     deployment that never exported it was reported as an ERROR and
        #     blocked at boot for a setting that was fine;
        #   * the two sources could disagree (env says one thing, settings say
        #     another) and the validator would act on the wrong one.
        from src.app.core.config import settings as _settings

        ai_settings = _settings.ai
        if not ai_settings.AI_ENABLED:
            return results

        active_provider = ai_settings.AI_ACTIVE_PROVIDER.lower()

        if active_provider == "ollama":
            ollama_base_url = ai_settings.OLLAMA_BASE_URL
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
                    # P2-8: run the blocking sync httpx.get off the event loop
                    # (validate_and_log is invoked from inside FastAPI's
                    # async lifespan, where this would otherwise stall the
                    # loop up to 5s). asyncio.to_thread would require a
                    # running loop; ThreadPoolExecutor works from any sync
                    # caller.
                    from concurrent.futures import ThreadPoolExecutor

                    with ThreadPoolExecutor(max_workers=1) as _pool:
                        try:
                            response = _pool.submit(
                                lambda: httpx.get(f"{ollama_base_url}/api/tags", timeout=2.0)
                            ).result(timeout=2.5)
                        except Exception:
                            response = None
                            is_reachable = False
                        else:
                            is_reachable = response is not None and response.status_code == 200
                    if response is not None and not is_reachable:
                        results.append(
                            ValidationResult(
                                key="OLLAMA_BASE_URL",
                                severity=ValidationSeverity.WARNING,
                                message=f"Ollama at {ollama_base_url} is not reachable",
                            )
                        )
                    elif response is None:
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
                logger.error("[ENV VALIDATION ERROR]", key=result.key, message=result.message)
                has_errors = True
            elif result.severity == ValidationSeverity.WARNING:
                logger.warning("[ENV VALIDATION WARNING]", key=result.key, message=result.message)
            else:
                logger.info("[ENV VALIDATION INFO]", key=result.key, message=result.message)

        return not has_errors
