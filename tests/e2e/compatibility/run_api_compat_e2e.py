"""
E2E Test Runner for API Compatibility Validation

This script runs all E2E tests and generates a detailed report.

Usage:
    python tests/e2e/run_api_compat_e2e.py
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(f"e2e_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)


def main():
    """Run E2E tests and generate report."""
    logger.info("=" * 80)
    logger.info("API Compatibility E2E Test Suite")
    logger.info("=" * 80)
    logger.info(f"Started at: {datetime.now()}")
    logger.info("")

    # Run pytest
    import pytest

    test_file = Path(__file__).parent / "test_api_compat_e2e.py"

    logger.info(f"Running tests from: {test_file}")
    logger.info("")

    # Run with verbose output and logging
    exit_code = pytest.main(
        [
            str(test_file),
            "-v",
            "-s",
            "--tb=short",
            f"--html=e2e_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html",
            "--self-contained-html",
        ]
    )

    logger.info("")
    logger.info("=" * 80)
    logger.info(f"Completed at: {datetime.now()}")
    logger.info(f"Exit code: {exit_code}")
    logger.info("=" * 80)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
