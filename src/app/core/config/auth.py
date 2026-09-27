"""R2-03: settings split from core/config.py — auth domain."""

import re
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings

KNOWN_LEAKED_ADMIN_PASSWORDS: frozenset[str] = frozenset(
    {
        "SuperS3cretP@ssw0rd!123",
        "Str1ngst!",
    }
)


class FirstUserSettings(BaseSettings):
    ADMIN_NAME: str = "admin"
    ADMIN_EMAIL: str = "admin@admin.com"
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: SecretStr | None = Field(..., min_length=16)

    @field_validator("ADMIN_PASSWORD", mode="after")
    @classmethod
    def validate_password_complexity(cls, v: SecretStr | None) -> SecretStr | None:
        if v is None:
            return v
        password = v.get_secret_value()
        if password in KNOWN_LEAKED_ADMIN_PASSWORDS:
            raise ValueError("ADMIN_PASSWORD matches a value that leaked into git history. Generate a new one.")
        if not re.search(r"[a-z]", password):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"[A-Z]", password):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"\d", password):
            raise ValueError("Password must contain at least one digit")
        if not re.search(r"[@$!%*?&]", password):
            raise ValueError("Password must contain at least one special character (@$!%*?&)")
        return v


class AuthSettings(BaseSettings):
    """Authentication security settings."""

    # Account lockout configuration
    AUTH_MAX_LOGIN_ATTEMPTS: int = Field(default=5, ge=1, le=50)
    AUTH_LOCKOUT_DURATION_SECONDS: int = Field(default=900, ge=60, le=86400)  # 15 minutes
    # SECURITY FIX: Make audit log failure mode configurable
    # "fail_open": Log error but allow operation (default, availability focused)
    # "fail_closed": Raise exception and block operation (security focused, compliance)
    # SECURITY FIX: Changed default to 'fail_closed' to ensure mandatory audit trails in production.
    AUDIT_LOG_FAILURE_MODE: Literal["fail_open", "fail_closed"] = Field(
        default="fail_closed",
        description="How to handle audit log failures: 'fail_open' (allow) or 'fail_closed' (block)",
    )
    # Configurable MFA pending token expiry (was previously a hardcoded magic number)
    MFA_PENDING_EXPIRY_SECONDS: int = Field(
        default=300, ge=60, le=3600, description="MFA pending token TTL in seconds (default: 5 minutes)"
    )
    # S-M2: open self-service registration gate. When False (default), the
    # public /account/register endpoint only creates the FIRST user of a
    # tenant (tenant bootstrap); joining an EXISTING tenant requires an
    # admin-created account — closing anonymous signup into any tenant via
    # a guessable X-Tenant-ID slug.
    AUTH_OPEN_REGISTRATION: bool = Field(
        default=False,
        description="Allow self-service registration into existing tenants (default False: first-user bootstrap only)",
    )
    # Run-3 finding tenancy.zero_user_tenant_claim_by_slug: the empty-tenant
    # bootstrap was gated on "the tenant has no users", a STANDING property. Any
    # caller who could guess a freshly created or user-emptied tenant's slug
    # could permanently squat it, because the legitimate owner could then no
    # longer register.
    #
    # The fix replaces that test with proof of intent: the operator issues a
    # per-deployment secret and hands it to the tenant owner out of band. Empty
    # => the bootstrap is DISABLED (fail closed); there is no "no token means
    # allow" path, because that would be the original defect.
    FIRST_USER_REGISTRATION_TOKEN: str = Field(
        default="",
        description=(
            "Secret required to bootstrap the first user of an empty tenant. "
            "Empty disables first-user self-registration entirely (fail closed). "
            "Set per deployment and distribute out of band."
        ),
    )
    # Run-3 N-3: revoke enrolled passkeys when the password changes or is reset.
    #
    # A stolen access token can enroll an attacker-controlled passkey; nothing
    # previously removed it, so the attacker kept a durable credential after the
    # victim rotated their password. Revoking on a credential change closes that
    # persistence path. Costs the legitimate user one re-enrollment.
    #
    # Default True (secure). Set False only if passkey re-enrollment friction is
    # judged worse than the persistence risk for your deployment.
    REVOKE_PASSKEYS_ON_PASSWORD_CHANGE: bool = Field(
        default=True,
        description=(
            "Delete all enrolled WebAuthn passkeys when a password is changed or "
            "reset, so a credential enrolled with a stolen session cannot outlive it."
        ),
    )
    AUTH_MAX_SESSIONS: int = Field(default=5, ge=1, le=50, description="Max concurrent sessions per user")
    AUTH_SESSION_TTL_SECONDS: int = Field(
        default=86400 * 7, ge=3600, le=86400 * 30, description="Session TTL in seconds"
    )
    # SECURITY FIX (M-1): When True, MFA completion is rejected if client IP
    # differs from the IP used during the login phase.
    # S-M3: default flipped to True — a stolen 5-minute MFA pending token
    # could previously be completed from any IP. Networks that legitimately
    # rotate client IPs mid-login (mobile carriers) can widen the escape
    # hatch via MFA_IP_BINDING_GRACE_SECONDS instead of disabling binding.
    MFA_ENFORCE_IP_BINDING: bool = Field(
        default=True, description="Reject MFA completion if client IP changed since login (prevents stolen MFA tokens)"
    )
    # S-M3: rotation-window escape hatch — when the client IP changed since
    # login, completion is still accepted while the pending token is within
    # this many seconds of expiring (i.e. the IP rotated recently enough that
    # both IPs plausibly belong to the same login attempt). 0 disables the
    # grace window entirely (strictest posture).
    MFA_IP_BINDING_GRACE_SECONDS: int = Field(
        default=60, ge=0, le=300, description="Grace window for IP rotation during MFA completion (0 = strict)"
    )
    # Token blacklist cache failure mode - fail_closed by default for security
    # "fail_open": Return None when Redis unavailable (allow auth to continue)
    # "fail_closed": Return True when Redis unavailable (treat as blacklisted, block auth)
    # S-1 NOTE: with fail_open, SessionManager enters degraded mode during Redis
    # outages and accepts sessions whose JWT is unexpired — revoked sessions can
    # be accepted for the outage duration. Only choose fail_open if availability
    # outweighs that risk.
    TOKEN_CACHE_FAILURE_MODE: Literal["fail_open", "fail_closed"] = Field(
        default="fail_closed",
        description="How to handle token blacklist cache failures: 'fail_open' (allow) or 'fail_closed' (block)",
    )
    # SECURITY FIX (S-4): Session registration failure mode. Previously there was
    # no config knob — the getattr fallback in login_helpers.py silently defaulted
    # to fail_open, so concurrent-session limiting was bypassed during Redis outages.
    # "fail_open": Log the error but let the login proceed (availability focused).
    # "fail_closed": Reject the login with 503 (security focused — session limiting enforced).
    # Mirrors AUDIT_LOG_FAILURE_MODE / TOKEN_CACHE_FAILURE_MODE (fail_closed default).
    SESSION_REGISTRATION_FAIL_MODE: Literal["fail_open", "fail_closed"] = Field(
        default="fail_closed",
        description="Session-registration failure mode: 'fail_open' (allow login) or 'fail_closed' (block login)",
    )
    MFA_LOCKOUT_FAIL_CLOSED: bool = Field(
        default=True,
        description="Whether MFA lockout check fails closed (blocks authentication) during Redis outage",
    )
