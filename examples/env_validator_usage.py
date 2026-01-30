"""
Environment Validator Usage Examples.

This script demonstrates how to extend the Environment Validator
with custom rules for application-specific validation.
"""

import logging
import os

from src.app.core.env_validator import EnvironmentValidator, ValidationSeverity

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Example 1: Customizing Validation Rules
# -----------------------------------------------------------------------------
def setup_custom_validation():
    """
    Add custom validation rules for your specific application needs.
    This should typically be done during application startup.
    """
    print("\n[1] Setting up custom validation rules...")

    # Add rule for API Key format (e.g., Stripe keys)
    EnvironmentValidator.VALIDATION_RULES["STRIPE_API_KEY"] = {
        "min_length": 32,
        "pattern": r"^sk_(test|live)_[a-zA-Z0-9]+$",
        "description": "Stripe Secret Key (starts with sk_test_ or sk_live_)",
    }

    # Add rule for numeric configuration
    EnvironmentValidator.VALIDATION_RULES["MAX_UPLOAD_SIZE_MB"] = {
        "pattern": r"^\d+$",
        "description": "Max upload size must be a positive integer",
    }

    # Add rule for detailed service URLs
    EnvironmentValidator.VALIDATION_RULES["REDIS_URL"] = {
        "pattern": r"^redis(s)?://.*",
        "description": "Redis URL must start with redis:// or rediss://",
    }


# -----------------------------------------------------------------------------
# Example 2: Running Validation
# -----------------------------------------------------------------------------
def run_validation_check():
    """
    Run validation and handle results.
    Typically called in main.py before app startup.
    """
    print("\n[2] Running environment validation...")

    # Set some dummy env vars for demonstration
    os.environ["STRIPE_API_KEY"] = "sk_test_12345abcdef"  # Valid
    os.environ["MAX_UPLOAD_SIZE_MB"] = "invalid"  # Invalid (not number)
    os.environ["REDIS_URL"] = "http://localhost:6379"  # Invalid protocol

    # Run validation
    results = EnvironmentValidator.validate_all()

    # Process results
    errors = []
    warnings = []

    for result in results:
        if result.severity == ValidationSeverity.ERROR:
            # Use ASCII characters instead of Unicode to avoid encoding errors on Windows
            errors.append(f"[X] {result.key}: {result.message}")
        elif result.severity == ValidationSeverity.WARNING:
            warnings.append(f"[!] {result.key}: {result.message}")

    # Print Report
    if errors:
        print("\nFound Blocking Errors:")
        for err in errors:
            print(err)
    else:
        print("\nNo blocking errors found.")

    if warnings:
        print("\nWarnings:")
        for warn in warnings:
            print(warn)


# -----------------------------------------------------------------------------
# Example 3: Integration in Startup Script
# -----------------------------------------------------------------------------
def startup_integration_example():
    """
    Example of how to integrate into main.py
    """
    print("\n[3] Startup Integration Example...")

    # Validate and Log automatically
    # Returns False if any ERROR severity issues are found
    is_valid = EnvironmentValidator.validate_and_log()

    if not is_valid:
        print(">>> CRITICAL: Environment validation failed. Startup aborted.")
        # sys.exit(1)
    else:
        print(">>> Environment valid. Proceeding with startup.")


if __name__ == "__main__":
    setup_custom_validation()
    run_validation_check()
    startup_integration_example()
