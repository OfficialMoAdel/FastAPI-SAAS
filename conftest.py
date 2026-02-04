"""Root conftest.py - Load environment variables BEFORE pytest collects tests.

CRITICAL: Environment must be loaded at import time (module level), not in fixtures,
because pydantic-settings validates env vars at import time.
"""

import os
import sys
from pathlib import Path

# ============================================================================
# ENVIRONMENT LOADING - MUST BE AT MODULE LEVEL, BEFORE ANY IMPORTS
# ============================================================================

# Determine project root - this conftest.py is at the root
_project_root = Path(__file__).parent
_env_file = _project_root / "src" / ".env"
_env_local = _project_root / "src" / ".env.local"

# Load dotenv IMMEDIATELY at module import time
from dotenv import load_dotenv

_loaded = False
if _env_file.exists():
    load_dotenv(_env_file, override=True)
    _loaded = True
elif _env_local.exists():
    load_dotenv(_env_local, override=True)
    _loaded = True

if not _loaded:
    # Set minimum required values for testing if no env file
    os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32chars")
    os.environ.setdefault("POSTGRES_USER", "test")
    os.environ.setdefault("POSTGRES_PASSWORD", "test")
    os.environ.setdefault("ADMIN_PASSWORD", "test")

os.environ.setdefault("ENVIRONMENT", "local")

# ============================================================================
# Now safe to import pytest-related things
# ============================================================================
