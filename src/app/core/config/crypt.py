"""R2-03: settings split from core/config.py — crypt domain."""

import os
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings

# C-1 leaked-secret blocklist. Provenance (verified via
# `git log -p -- .env.txt src/.env.local`):
# - `de2132a4...be1c8` — SECRET_KEY committed in 6cbc8a56/af450c42/9119f7fd
#   (`src/.env.local` + `.env.txt`). ENCRYPTION_KEY was always empty in
#   history, so there is no historical ENCRYPTION_KEY value to block.
# - `mypassword` (POSTGRES_PASSWORD, same commits) is blocklisted in
#   `database.KNOWN_LEAKED_POSTGRES_PASSWORDS`; `Str1ngst!`
#   (ADMIN_PASSWORD) in `auth.KNOWN_LEAKED_ADMIN_PASSWORDS`.
# When rotating after a leak, append the old value here so it can never be
# reused — startup validation rejects any match (fail-closed).
KNOWN_LEAKED_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "de2132a4a3a029d6a93a2aefcb519f0219990f92ca258a7c5ed938a444dbe1c8",
    }
)


class CryptSettings(BaseSettings):
    SECRET_KEY: SecretStr | None
    ALGORITHM: Literal["HS256", "RS256"] = "HS256"  # Options: HS256 (symmetric), RS256 (asymmetric)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # SECURITY FIX: Maximum absolute session duration to prevent indefinite refresh
    MAX_SESSION_DURATION_DAYS: int = Field(
        default=30, ge=1, le=365, description="Maximum absolute session duration before forcing re-auth"
    )
    # Bcrypt work factor: 8=30ms(dev), 10=100ms, 12=250ms(default), 14=1000ms
    BCRYPT_ROUNDS: int = Field(default=12, ge=8, le=14)
    # L3: Configurable bcrypt thread pool size for password hashing
    # R1-07: default scales with CPU count (was hardcoded 4, which saturated
    # under concurrent login bursts). Overridable via BCRYPT_THREAD_POOL_SIZE.
    BCRYPT_THREAD_POOL_SIZE: int = Field(
        default=min(32, max(4, (os.cpu_count() or 4) * 2)),
        ge=1,
        le=32,
        description="Thread pool size for bcrypt operations",
    )
    # Key for Fernet encryption. If None, webhooks encryption might fail or use a dev key.
    ENCRYPTION_KEY: SecretStr | None = None
    # SECURITY: Set unique salt per deployment for key derivation
    ENCRYPTION_SALT: SecretStr | None = None
    # Old key for key rotation, stored securely
    ENCRYPTION_KEY_OLD: SecretStr | None = None
    # RS256 asymmetric keys (optional, required if ALGORITHM=RS256)
    JWT_PRIVATE_KEY: SecretStr | None = None  # PEM-encoded private key
    JWT_PUBLIC_KEY: str | None = None  # PEM-encoded public key
    # SECURITY FIX: Made JWT audience/issuer configurable instead of hardcoded
    JWT_AUDIENCE: str = Field(default="fastapi-saas-api", description="JWT token audience claim")
    JWT_ISSUER: str = Field(default="fastapi-saas", description="JWT token issuer claim")

    @model_validator(mode="after")
    def validate_secrets(self) -> "CryptSettings":
        """Validate required secrets are set and meet security requirements."""
        if not self.SECRET_KEY:
            raise ValueError("SECRET_KEY must be set and non-empty")
        key = self.SECRET_KEY.get_secret_value()
        if not key:
            raise ValueError("SECRET_KEY must be set and non-empty")

        if key in KNOWN_LEAKED_SECRET_KEYS:
            raise ValueError(
                "SECRET_KEY matches a value that leaked into git history. Generate a new one with: openssl rand -hex 32"
            )

        if self.ALGORITHM == "RS256":
            # For RS256, we need both private and public keys
            if not self.JWT_PRIVATE_KEY or not self.JWT_PUBLIC_KEY:
                raise ValueError(
                    "RS256 algorithm requires JWT_PRIVATE_KEY and JWT_PUBLIC_KEY. "
                    "Generate with: openssl genrsa -out private.pem 2048 && "
                    "openssl rsa -in private.pem -pubout -out public.pem"
                )
        else:
            # For HS256, validate SECRET_KEY length
            MIN_SECRET_KEY_LENGTH = 32
            if len(key) < MIN_SECRET_KEY_LENGTH:
                raise ValueError(
                    f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters for HS256. "
                    "Generate with: openssl rand -hex 32"
                )
        return self

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "CryptSettings":
        """Validate encryption keys are set to prevent runtime failures.

        S-1 FIX: ENVIRONMENT may be supplied via ``.env`` (loaded by
        pydantic-settings), in which case it never reaches ``os.environ`` —
        the old lookup silently defaulted to "local" and skipped this check,
        letting production boot with ENCRYPTION_KEY=None. Consult the process
        environment first, then the dotenv file(s) Settings loads.
        """
        import os
        from pathlib import Path

        from dotenv import dotenv_values

        env_value: str | None = os.environ.get("ENVIRONMENT")
        if env_value is None:
            # M-4: use python-dotenv's parser instead of hand-rolled line
            # splitting (handles quoting/escaping consistently with the loader).
            src_dir_env = Path(__file__).resolve().parents[2] / ".env"
            repo_env = Path(".env")
            for candidate in (src_dir_env, repo_env):
                if candidate.is_file():
                    env_value = dotenv_values(candidate).get("ENVIRONMENT")
                    if env_value is not None:
                        break

        env = (env_value or "local").lower()
        if env in ("production", "staging"):
            # ENCRYPTION_KEY and ENCRYPTION_SALT should be validated in production/staging
            if not self.ENCRYPTION_KEY or not self.ENCRYPTION_SALT:
                raise ValueError(
                    "SECURITY: ENCRYPTION_KEY and ENCRYPTION_SALT must be set in the production/staging environment."
                )

        return self
