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

        # S-M6: enable HSTS in every non-local environment (staging included).
        # The previous `== PRODUCTION` gate left staging deployments without
        # HSTS — an SSL-strip window in staging-tier deployments. LOCAL only
        # (plain HTTP dev servers must not receive HSTS headers).
        self.enable_hsts_env = settings.env.ENVIRONMENT != EnvironmentOption.LOCAL

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                from starlette.datastructures import MutableHeaders

                headers = MutableHeaders(scope=message)

                # XSS Prevention Headers
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", self.frame_options)
                headers.setdefault("X-XSS-Protection", "0")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                headers.setdefault(
                    "Permissions-Policy",
                    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
                    "magnetometer=(), microphone=(), payment=(), usb=()",
                )

                if self.enable_csp:
                    headers.setdefault("Content-Security-Policy", self.csp_policy)

                if self.enable_hsts and self.enable_hsts_env:
                    headers.setdefault(
                        "Strict-Transport-Security", f"max-age={self.hsts_max_age}; includeSubDomains; preload"
                    )

            await send(message)

        await self.app(scope, receive, send_wrapper)
