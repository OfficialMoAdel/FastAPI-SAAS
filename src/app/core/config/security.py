"""R2-03: settings split from core/config.py — security domain."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class SecuritySettings(BaseSettings):
    """Security and performance optimization settings."""

    # IP Filtering Configuration
    # Enable/disable IP whitelist checking. When enabled, only IPs in the whitelist are allowed.
    IP_WHITELIST_ENABLED: bool = Field(
        default=False,
        description="Enable IP whitelist filtering. Only allows requests from whitelisted IPs.",
    )
    # Enable/disable IP blacklist checking. When enabled, IPs in the blacklist are blocked.
    IP_BLACKLIST_ENABLED: bool = Field(
        default=True,
        description="Enable IP blacklist filtering. Blocks requests from blacklisted IPs.",
    )
    # When enabled, whitelisted IPs bypass rate limiting restrictions.
    IP_WHITELIST_BYPASS_RATE_LIMIT: bool = Field(
        default=False,
        description="Allow whitelisted IPs to bypass rate limiting checks.",
    )

    # Auto-ban configuration
    # Automatically ban repeat offenders after threshold violations
    AUTO_BAN_ENABLED: bool = Field(
        default=True,
        description="Enable auto-ban for repeat rate limit offenders",
    )
    AUTO_BAN_THRESHOLD: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Number of violations before auto-ban (1-1000)",
    )
    AUTO_BAN_DURATION: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Ban duration in seconds (60-86400 = 1 min to 24 hours)",
    )
    AUTO_BAN_ESCALATION: bool = Field(
        default=True,
        description="Double ban duration on repeat offenses",
    )
    AUTO_BAN_VIOLATION_WINDOW: int = Field(
        default=86400,
        ge=3600,
        le=604800,
        description="Time window in seconds for counting violations (3600-604800 = 1 hour to 7 days)",
    )

    # Bad Bot Filtering Configuration
    BAD_BOT_FILTER_ENABLED: bool = Field(
        default=True,
        description="Enable blocking of known malicious scanners and bots",
    )
    BAD_BOT_USER_AGENTS: list[str] | None = Field(
        default=[
            "sqlmap",  # SQL injection tool
            "nikto",  # Web scanner
            "nmap",  # Network scanner
            "masscan",  # Port scanner
            "zgrab",  # Go-based scanner
            "gobuster",  # Directory bruteforcer
            "dirbuster",  # Java directory bruteforcer
            "wpscan",  # WordPress scanner
            "nuclei",  # Vulnerability scanner
            "httpx",  # Probe tool
        ],
        description="List of user-agent substrings to block as bad bots",
    )

    # S-3 FIX: default trusts ONLY loopback. The previous default trusted all
    # RFC1918 ranges, so any host on the internal network could spoof
    # X-Forwarded-For and corrupt rate-limit/ban/audit IP identity. Operators
    # behind an internal LB must explicitly set their LB's CIDRs here.
    TRUSTED_PROXY_IPS: list[str] | None = Field(
        default=["127.0.0.1", "::1"],
        description="List of trusted proxy IP addresses (loopback by default)",
    )
    TRUSTED_PROXY_NETWORKS: list[str] | None = Field(
        default=None,
        description=(
            "Trusted proxy network CIDRs. Default None = no network-level "
            "trust (loopback IPs above still apply). Set to your load "
            "balancer's CIDRs explicitly, e.g. ['10.0.0.0/8']."
        ),
    )


class AttackDetectionSettings(BaseSettings):
    """Attack pattern detection configuration.

    Detects SQL injection, XSS, and path traversal attacks.
    Modes:
    - 'log': Log attack and continue (monitoring mode)
    - 'block': Return 400 Bad Request
    - 'ban': Add to blacklist and return 403
    """

    ATTACK_DETECTION_ENABLED: bool = Field(
        default=True,
        description="Enable attack pattern detection middleware",
    )
    ATTACK_DETECTION_MODE: Literal["log", "block", "ban"] = Field(
        default="block",
        description="Attack detection mode: 'log' (monitor), 'block' (400), 'ban' (403 + blacklist)",
    )
    ATTACK_DETECTION_LOG_FULL_REQUEST: bool = Field(
        default=False,
        description="Log full request URL (True) or just matched pattern (False). Privacy consideration.",
    )
    ATTACK_DETECTION_BRANCH_PRUNE: bool = Field(
        default=True,
        description=(
            "Kill switch for the necessary-literal prefilter (A-1). True (default): "
            "skip the combined-regex scan when no necessary literal is present. "
            "False: run the FULL COMBINED_ATTACK_RE on every scan, at roughly the "
            "pre-P1 cost. Set False to make every request match the regex so a "
            "suspected-too-narrow literal set cannot hide a detection. This is a "
            "SECURITY control, so it fails safe toward the regex: the prefilter is "
            "an optimisation and the regex is the detector."
        ),
    )
    ATTACK_DETECTION_BRANCH_PRUNE_OFFLOAD: bool = Field(
        default=False,
        description=(
            "A-8: run the request-BODY pattern scan in a worker process "
            "instead of on the event loop. False (default): scan in process. "
            "It defaults to False because the process arm's p95 was never "
            "measured on the Linux target (L-13's parity figures are for the "
            "THREAD arm), so switching it on would be an unmeasured change "
            "to production. It is a CAPACITY lever, never a detection lever: "
            "the in-process scan finds exactly the same attacks, and ADR-0016 "
            "requires a pool-side failure to fall back to that in-process "
            "scan (fail-to-current) with a circuit breaker that disables the "
            "offload after repeated failures."
        ),
    )
