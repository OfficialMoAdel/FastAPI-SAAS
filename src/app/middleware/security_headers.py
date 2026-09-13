"""Security headers middleware for XSS and other attack prevention.

Adds standard security headers to all responses:
- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY
- X-XSS-Protection: 0 (disabled per OWASP — "1; mode=block" is exploitable)
- Content-Security-Policy (configurable)
- Strict-Transport-Security (HSTS)
- Referrer-Policy
- Permissions-Policy
"""

from structlog import get_logger

from src.app.core.config import EnvironmentOption, settings

logger = get_logger(__name__)

# P2 micro: static header values built once at import, not per response.
_PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
)


class SecurityHeadersMiddleware:
    """Add security headers to all responses.

    Configurable via settings or constructor parameters.
    """

    # Default CSP policy - restrictive but functional
    DEFAULT_CSP = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' https:; "
        "font-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )

    def __init__(
        self,
        app,
        enable_hsts: bool = True,
        hsts_max_age: int = 31536000,  # 1 year
        enable_csp: bool = True,
        csp_policy: str | None = None,
        frame_options: str = "DENY",
    ):
        """Initialize security headers middleware.

        Args:
            app: ASGI application
            enable_hsts: Enable HTTP Strict Transport Security
            hsts_max_age: HSTS max-age in seconds (default: 1 year)
            enable_csp: Enable Content-Security-Policy header
            csp_policy: Custom CSP policy string (or use default)
            frame_options: X-Frame-Options value (DENY, SAMEORIGIN)
        """
        self.app = app
        self.enable_hsts = enable_hsts
        self.hsts_max_age = hsts_max_age
        self.enable_csp = enable_csp
        self.csp_policy = csp_policy or self.DEFAULT_CSP
        self.frame_options = frame_options
        # P2 micro: HSTS value depends only on constructor args — build once.
        self._hsts_value = f"max-age={hsts_max_age}; includeSubDomains; preload"

        # S-M6: enable HSTS in every non-local environment (staging included).
        # The previous `== PRODUCTION` gate left staging deployments without
        # HSTS — an SSL-strip window in staging-tier deployments. LOCAL only
        # (plain HTTP dev servers must not receive HSTS headers).
        self.enable_hsts_env = settings.env.ENVIRONMENT != EnvironmentOption.LOCAL

        # R5-MICRO (plan §4): precompute all header (name, value) byte pairs
        # once at construction. Per response we only scan existing header
        # names (lowercased) and append the missing pairs — no per-response
        # MutableHeaders.setdefault calls or str->bytes encoding.
        pairs: list[tuple[bytes, bytes]] = [
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", self.frame_options.encode("ascii")),
            (b"x-xss-protection", b"0"),
            (b"referrer-policy", b"strict-origin-when-cross-origin"),
            (b"permissions-policy", _PERMISSIONS_POLICY.encode("ascii")),
        ]
        if self.enable_csp:
            pairs.append((b"content-security-policy", self.csp_policy.encode("ascii")))
        if self.enable_hsts and self.enable_hsts_env:
            pairs.append((b"strict-transport-security", self._hsts_value.encode("ascii")))
        self._header_pairs = tuple(pairs)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                # R5-MICRO: setdefault semantics without MutableHeaders —
                # scan existing names once, append only the missing pairs.
                existing = {name.lower() for name, _ in message["headers"]}
                missing = [pair for pair in self._header_pairs if pair[0] not in existing]
                if missing:
                    message["headers"] = [*message["headers"], *missing]

            await send(message)

        await self.app(scope, receive, send_wrapper)
