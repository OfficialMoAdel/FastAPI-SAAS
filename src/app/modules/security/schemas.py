"""Security module schemas.

Pydantic models for IP filtering and security-related operations.
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class IPAddressRequest(BaseModel):
    """Request schema for adding an IP address to a list.

    Attributes:
        ip_address: IPv4 or IPv6 address to add
        reason: Optional reason for the addition
        expires_at: Optional expiration datetime
    """

    ip_address: str = Field(
        ...,
        min_length=7,
        max_length=45,
        description="IPv4 or IPv6 address",
        examples=["192.168.1.100", "2001:db8::1"],
    )
    reason: str | None = Field(
        default=None,
        max_length=500,
        description="Reason for adding this IP",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Optional expiration time for this entry",
    )

    @field_validator("ip_address")
    @classmethod
    def validate_ip_address(cls, v: str) -> str:
        """Validate IP address format."""
        import ipaddress

        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"Invalid IP address format: {v}")
        return v


class IPAddressRemoveRequest(BaseModel):
    """Request schema for removing an IP address from a list.

    Attributes:
        ip_address: IPv4 or IPv6 address to remove
    """

    ip_address: str = Field(
        ...,
        min_length=7,
        max_length=45,
        description="IPv4 or IPv6 address",
        examples=["192.168.1.100", "2001:db8::1"],
    )

    @field_validator("ip_address")
    @classmethod
    def validate_ip_address(cls, v: str) -> str:
        """Validate IP address format."""
        import ipaddress

        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"Invalid IP address format: {v}")
        return v


class IPListEntry(BaseModel):
    """Single IP list entry.

    Attributes:
        ip_address: The IP address
        added_at: When the IP was added
        reason: Reason for addition
        expires_at: When the entry expires (None if permanent)
        added_by: User who added this entry
    """

    ip_address: str
    added_at: datetime
    reason: str | None = None
    expires_at: datetime | None = None
    added_by: int | None = None


class IPListsResponse(BaseModel):
    """Response schema for getting both whitelist and blacklist.

    Attributes:
        whitelist: List of whitelisted IP addresses
        blacklist: List of blacklisted IP addresses
        whitelist_enabled: Whether whitelist checking is enabled
        blacklist_enabled: Whether blacklist checking is enabled
        whitelist_bypass_rate_limit: Whether whitelist bypasses rate limits
    """

    whitelist: list[IPListEntry]
    blacklist: list[IPListEntry]
    whitelist_enabled: bool
    blacklist_enabled: bool
    whitelist_bypass_rate_limit: bool


class IPListUpdateResponse(BaseModel):
    """Response schema for IP list update operations.

    Attributes:
        success: Whether the operation was successful
        ip_address: The affected IP address
        list_type: Which list was modified ("whitelist" or "blacklist")
        action: The action performed ("added" or "removed")
        message: Human-readable status message
    """

    success: bool
    ip_address: str
    list_type: str
    action: str
    message: str


class IPFilterConfig(BaseModel):
    """Response schema for IP filter configuration.

    Attributes:
        whitelist_enabled: Whether whitelist checking is enabled
        blacklist_enabled: Whether blacklist checking is enabled
        whitelist_bypass_rate_limit: Whether whitelist bypasses rate limits
    """

    whitelist_enabled: bool = Field(
        default=False,
        description="Enable IP whitelist filtering",
    )
    blacklist_enabled: bool = Field(
        default=True,
        description="Enable IP blacklist filtering",
    )
    whitelist_bypass_rate_limit: bool = Field(
        default=False,
        description="Allow whitelisted IPs to bypass rate limiting",
    )


class IPFilterConfigUpdate(BaseModel):
    """Request schema for updating IP filter configuration.

    Attributes:
        whitelist_enabled: Whether to enable whitelist checking
        blacklist_enabled: Whether to enable blacklist checking
        whitelist_bypass_rate_limit: Whether whitelisted IPs bypass rate limits
    """

    whitelist_enabled: bool | None = None
    blacklist_enabled: bool | None = None
    whitelist_bypass_rate_limit: bool | None = None


# ============================================================================
# Auto-ban Schemas
# ============================================================================


class AutoBanConfig(BaseModel):
    """Response schema for auto-ban configuration.

    Attributes:
        enabled: Whether auto-ban is enabled
        threshold: Violations before auto-ban
        duration: Ban duration in seconds
        escalation: Whether to double duration on repeat offenses
    """

    enabled: bool = Field(default=True, description="Enable auto-ban")
    threshold: int = Field(default=10, ge=1, le=1000, description="Violations before ban")
    duration: int = Field(default=3600, ge=60, le=86400, description="Ban duration in seconds")
    escalation: bool = Field(default=True, description="Double duration on repeat offenses")


class AutoBanConfigUpdate(BaseModel):
    """Request schema for updating auto-ban configuration.

    Attributes:
        enabled: Whether to enable auto-ban
        threshold: Violations before auto-ban
        duration: Ban duration in seconds
        escalation: Whether to double duration on repeat offenses
    """

    enabled: bool | None = None
    threshold: int | None = Field(default=None, ge=1, le=1000)
    duration: int | None = Field(default=None, ge=60, le=86400)
    escalation: bool | None = None


class BannedIPEntry(BaseModel):
    """Single banned IP entry.

    Attributes:
        ip: The banned IP address
        reason: Reason for the ban
        ttl_seconds: Time until ban expires
        ban_count: Historical number of bans for this IP
    """

    ip: str
    reason: str
    ttl_seconds: int
    ban_count: int


class BannedIPsResponse(BaseModel):
    """Response schema for listing banned IPs.

    Attributes:
        banned_ips: List of currently banned IP addresses
        total_count: Total number of banned IPs
    """

    banned_ips: list[BannedIPEntry]
    total_count: int


class ViolationStats(BaseModel):
    """Violation statistics.

    Attributes:
        total_ips: Number of IPs with violations
        total_violations: Total violation count across all IPs
    """

    total_ips: int
    total_violations: int


class IPViolationStats(ViolationStats):
    """Violation stats for specific IP.

    Attributes:
        ip: The IP address
        violations: Number of violations for this IP
    """

    ip: str
    violations: int


class BanActionResponse(BaseModel):
    """Response schema for ban/unban actions.

    Attributes:
        success: Whether the action was successful
        ip_address: The affected IP address
        action: The action performed ("banned" or "unbanned")
        message: Human-readable status message
    """

    success: bool
    ip_address: str
    action: str
    message: str
