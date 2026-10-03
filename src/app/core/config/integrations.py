"""R2-03: settings split from core/config.py — integrations domain."""

from pydantic import Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings


class ArqSettings(BaseSettings):
    """ARQ worker and job queue settings."""

    ARQ_ENABLED: bool = Field(default=True, description="Enable ARQ background task queue")
    ARQ_WORKERS: int = Field(default=1, ge=1, le=8, description="Number of ARQ worker processes")
    ARQ_MAX_JOBS: int = Field(default=10, ge=1, le=100, description="Max concurrent jobs per worker")
    ARQ_JOB_TIMEOUT: int = Field(default=300, ge=10, le=3600, description="Default job timeout (seconds)")
    ARQ_KEEP_RESULT: int = Field(default=3600, ge=0, le=86400, description="Keep job results (seconds)")
    # K10 FIX: arq fails every job when max_tries=0 (job_try starts at 1 > 0),
    # so 0 is not a legal value.
    ARQ_MAX_TRIES: int = Field(default=3, ge=1, le=10, description="Default max job tries (>=1)")

    @field_validator("ARQ_MAX_JOBS", mode="after")
    @classmethod
    def validate_max_jobs(cls, v: int, info: ValidationInfo) -> int:
        workers = info.data.get("ARQ_WORKERS", 1)
        max_total_jobs = v * workers
        import logging

        logging.getLogger("app.core.config").info(
            f"ARQ total capacity: {workers} workers × {v} max_jobs = {max_total_jobs} concurrent jobs"
        )
        return v


class BillingSettings(BaseSettings):
    """Stripe and Billing settings."""

    STRIPE_SECRET_KEY: SecretStr | None = None
    STRIPE_PUBLISHABLE_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: SecretStr | None = None
    DEFAULT_FREE_TIER_NAME: str = Field(default="free", description="Default tier name for free accounts")


class AISettings(BaseSettings):
    """Settings for AI/LLM integration.

    S-31: this declared ONLY the Ollama fields, but `ai/factory.py` resolves
    the hosted providers with `getattr(settings, "OPENAI_API_KEY", None)`.
    The flat-config delegation in `core/config` maps DECLARED nested fields to
    the root — it cannot invent undeclared ones — so every OpenAI/Anthropic
    lookup returned None and the factory's "missing API key" branch ALWAYS
    fired. Two of the three providers were unreachable regardless of the
    environment. Declared here so the delegation can resolve them.
    """

    # S-37: read by `core/env_validator._check_ai_providers` via os.getenv but
    # declared NOWHERE, so the validator could not see a Pydantic default and
    # the flat-config delegation could not resolve them either.
    AI_ENABLED: bool = True
    AI_ACTIVE_PROVIDER: str = "ollama"

    OPENAI_API_KEY: SecretStr | None = None
    OPENAI_BASE_URL: str | None = None
    ANTHROPIC_API_KEY: SecretStr | None = None
    ANTHROPIC_BASE_URL: str | None = None

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_TIMEOUT: float = 120.0
    OLLAMA_DEFAULT_MODEL: str = "gemma3"

    # HTTP Client Connection Limits (prevents resource exhaustion under load)
    OLLAMA_MAX_CONNECTIONS: int = 100  # Total concurrent connections to Ollama
    OLLAMA_MAX_KEEPALIVE: int = 20  # Idle connections kept alive for reuse
    OLLAMA_KEEPALIVE_EXPIRY: float = 30.0  # Seconds before idle connection closes
