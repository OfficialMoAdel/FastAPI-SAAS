r"""Attack Detection Middleware for detecting and blocking malicious requests.

This middleware inspects incoming requests for attack patterns including:
- SQL Injection (UNION SELECT, OR 1=1, etc.)
- XSS attacks (script tags, javascript: URIs, event handlers)
- Path Traversal (../, ..\, encoded variants)

Three operation modes:
- log: Log attacks but allow request to continue (default)
- block: Block attacks with 400 Bad Request
- ban: Block attacks and add IP to blacklist (403 Forbidden)

Whitelist bypass: IPs in whitelist skip attack detection entirely.
"""

import re
import unicodedata
from enum import Enum
from typing import cast

from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.observability.log_sanitizer import LogSanitizer
from src.app.core.security.logging import SecurityEventType, create_security_event, log_security_event


class BodyScanError(Exception):
    """Raised when the request body cannot be properly scanned or reinjected."""


from src.app.core.utils.ip_utils import get_client_ip
from src.app.modules.security.service import IPFilterService

logger = get_logger(__name__)


class AttackType(str, Enum):
    """Enumeration of attack types for categorization.

    Using str as base class for JSON serialization compatibility.
    """

    SQL_INJECTION = "sql_injection"
    XSS = "xss"
    PATH_TRAVERSAL = "path_traversal"
    COMMAND_INJECTION = "command_injection"
    SSRF = "ssrf"


# Paths exempt from attack detection (health checks, metrics, etc.)
EXEMPT_PATHS: frozenset[str] = frozenset(
    [
        "/health",
        "/health/live",
        "/health/ready",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        # S8 FIX: Webhook endpoints receive third-party payloads
        # that may contain SQL-like strings (invoice descriptions, etc.)
        # These endpoints verify signatures independently (e.g. Stripe HMAC).
        "/api/v1/billing/webhook",
        "/api/v1/webhooks/stripe",
    ]
)

# Named group → (pattern_name, AttackType) mapping for combined regex matches
_GROUP_TO_ATTACK: dict[str, tuple[str, AttackType]] = {
    "sql_union": ("sql_union_select", AttackType.SQL_INJECTION),
    "sql_or": ("sql_or_1_equals_1", AttackType.SQL_INJECTION),
    "sql_drop": ("sql_drop_table", AttackType.SQL_INJECTION),
    "sql_delete": ("sql_delete_from", AttackType.SQL_INJECTION),
    "sql_comment": ("sql_comment_end", AttackType.SQL_INJECTION),
    "sql_semicomment": ("sql_semicolon_comment", AttackType.SQL_INJECTION),
    "sql_exec": ("sql_exec_xp", AttackType.SQL_INJECTION),
    "xss_script": ("xss_script_tag", AttackType.XSS),
    "xss_jsuri": ("xss_javascript_uri", AttackType.XSS),
    "xss_event": ("xss_event_handler", AttackType.XSS),
    "xss_iframe_tag": ("xss_iframe", AttackType.XSS),
    "xss_object_tag": ("xss_object", AttackType.XSS),
    "path_dotdot": ("path_dotdot_slash", AttackType.PATH_TRAVERSAL),
    "path_enc": ("path_encoded", AttackType.PATH_TRAVERSAL),
    "path_penc": ("path_partial_encoded", AttackType.PATH_TRAVERSAL),
    "cmd_inj": ("command_injection", AttackType.COMMAND_INJECTION),
    "ssrf_cloud": ("ssrf_cloud_metadata", AttackType.SSRF),
}

# Combined bytes regex: single search() replaces 16 sequential pattern checks.
# Compiled as bytes pattern for zero-copy scanning of request body.
COMBINED_ATTACK_RE: re.Pattern[bytes] = re.compile(
    rb"(?P<sql_union>\bunion\b.*?\bselect\b)"
    rb"|(?P<sql_or>\bor\b\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+['\"]?)"
    rb"|(?P<sql_drop>\bdrop\b.*?\btable\b)"
    rb"|(?P<sql_delete>\bdelete\b.*?\bfrom\b)"
    rb"|(?P<sql_comment>--\s*$)"
    rb"|(?P<sql_semicomment>;\s*--)"
    rb"|(?P<sql_exec>\bexec\b.*?\bxp_)"
    rb"|(?P<xss_script><script[\s>])"
    rb"|(?P<xss_jsuri>javascript\s*:)"
    rb"|(?P<xss_event>\bon\w+\s*=)"
    rb"|(?P<xss_iframe_tag><iframe[\s>])"
    rb"|(?P<xss_object_tag><object[\s>])"
    rb"|(?P<path_dotdot>\.\.\/)"
    rb"|(?P<path_enc>%2e%2e%2f)"
    rb"|(?P<path_penc>%2e%2e\/)"
    rb"|(?P<cmd_inj>(?:;|\|\||&&)\s*(?:cat|ls|id|whoami|ping|curl|wget)\b)"
    rb"|(?P<ssrf_cloud>169\.254\.169\.254)",
    re.IGNORECASE | re.MULTILINE,
)

# Unicode characters commonly used to bypass regex-based detection.
# Zero-width joiners/non-joiners, directional overrides, BOM, etc.
_ZERO_WIDTH_RE = re.compile(
    "["
    "\u200b-\u200f"  # Zero-width space, ZWNJ, ZWJ, LRM, RLM
    "\u202a-\u202e"  # Directional overrides
    "\u2060-\u2064"  # Word joiner, invisible separators
    "\u2066-\u2069"  # Directional isolates
    "\ufeff"  # BOM / zero-width no-break space
    "\ufff9-\ufffb"  # Interlinear annotation anchors
    "]"
)


class AttackDetectionMiddleware(BaseHTTPMiddleware):
    """Middleware to detect and handle malicious attack patterns.

    Execution order (in relation to other middleware):
    1. IPFilterMiddleware (whitelist check - if whitelisted, skip attack detection)
    2. AttackDetectionMiddleware (this middleware)
    3. RateLimitMiddleware

    Operation modes:
    - log: Detect attacks, log them, but allow request to continue
    - block: Detect attacks, log them, return 400 Bad Request
    - ban: Detect attacks, log them, add IP to blacklist, return 403 Forbidden

    Whitelist bypass: If request.state.ip_whitelisted is True, skip all detection.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._filter_service = IPFilterService()
        self._mode = getattr(settings, "ATTACK_DETECTION_MODE", "log")
        self._log_full_request = getattr(settings, "ATTACK_DETECTION_LOG_FULL_REQUEST", False)
        self._enabled = getattr(settings, "ATTACK_DETECTION_ENABLED", True)

    def _is_exempt_path(self, path: str) -> bool:
        """Check if path is exempt from attack detection.

        BUG FIX: The original `startswith` check matched any path that merely
        *begins* with an exempt string (e.g. /docs-attack matched /docs, bypassing
        attack scanning). Now we require either an exact match or a true sub-path
        (path starts with exempt + "/").
        """
        import posixpath
        normalized_path = posixpath.normpath(path)

        if normalized_path in EXEMPT_PATHS:
            return True
        return any(normalized_path.startswith(exempt + "/") for exempt in EXEMPT_PATHS)

    @staticmethod
    def _sanitize_input(content: bytes) -> bytes:
        """Sanitize input bytes to defeat Unicode-based regex bypass attacks.

        Applies:
        1. NFKC normalization — collapses compatibility codepoints (e.g. U+FF35 → U)
        2. Zero-width character stripping — removes invisible chars that split tokens

        Falls back to raw bytes if decoding fails (binary payloads).
        """
        try:
            text = content.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return content

        # NFKC normalization: collapses fullwidth/compatibility chars to their
        # standard ASCII equivalents (e.g. \uff35\uff2e\uff29\uff2f\uff2e → UNION)
        text = unicodedata.normalize("NFKC", text)

        # Strip zero-width and invisible formatting characters
        text = _ZERO_WIDTH_RE.sub("", text)

        return text.encode("utf-8", errors="replace")

    async def _detect_attack_patterns(self, request: Request) -> tuple[list[dict[str, str]], Request]:
        """Scan request for attack patterns using zero-copy bytes scanning.

        Checks:
        - URL path and query parameters (encoded to bytes)
        - Request body (scanned as raw bytes without UTF-8 decode)
        - Headers (encoded to bytes)

        All text inputs are sanitized via NFKC normalization and zero-width
        character stripping before regex matching to prevent bypass attacks.

        Returns:
            Tuple of (detected attacks list, possibly modified request).
            The request may be replaced with a new one if body needs buffering.
        """
        detected_attacks: list[dict] = []

        # Encode small strings to bytes (fast) instead of decoding large body (slow)
        path_bytes = self._sanitize_input(request.url.path.encode("utf-8", errors="replace"))
        self._check_for_patterns(path_bytes, "url_path", detected_attacks)

        query = str(request.query_params)
        if query:
            self._check_for_patterns(
                self._sanitize_input(query.encode("utf-8", errors="replace")),
                "query_string",
                detected_attacks,
            )

        # Check security-relevant headers (expanded from original 3)
        headers_to_check = [
            "user-agent",
            "referer",
            "x-requested-with",
            "cookie",
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-proto",
            "x-real-ip",
            "forwarded",
            "origin",
            "authorization",
            "x-originating-ip",
        ]
        for header_name in headers_to_check:
            header_value = request.headers.get(header_name, "")
            if header_value:
                self._check_for_patterns(
                    self._sanitize_input(header_value.encode("utf-8", errors="replace")),
                    f"header:{header_name}",
                    detected_attacks,
                )

        # Check request body if it's a POST/PUT/PATCH with content
        if request.method in ("POST", "PUT", "PATCH"):
            # Removed early return for multipart/form-data to actually apply the BUG FIX.

            # BUG FIX: Scan all content types to prevent bypass via multipart/form-data or other types
            skip_body = False
            _raw_scan = getattr(settings, "ATTACK_DETECTION_MAX_SCAN_SIZE", 128 * 1024)
            MAX_SCAN_SIZE = _raw_scan if isinstance(_raw_scan, int) else 128 * 1024
            content_length_str = request.headers.get("content-length")
            if content_length_str:
                try:
                    if int(content_length_str) > MAX_SCAN_SIZE:
                        skip_body = True
                        logger.debug(
                            "Skipping attack scan of oversized body | content-length=%s",
                            content_length_str,
                        )
                except ValueError:
                    pass  # Malformed Content-Length; proceed with body read

            if not skip_body:
                try:
                    body_chunks: list[bytes] = []
                    body_size = 0

                    async for chunk in request.stream():
                        body_chunks.append(chunk)
                        body_size += len(chunk)
                        if body_size > MAX_SCAN_SIZE:
                            # Instead of rejecting, we just stop buffering and let the request continue,
                            # but we can't easily reconstruct the stream. We'll raise BodyScanError
                            # which now bypasses the scan instead of returning 413.
                            logger.warning("Request body exceeded maximum scan size of %d bytes", MAX_SCAN_SIZE)
                            raise BodyScanError(f"Request body exceeded maximum scan size of {MAX_SCAN_SIZE} bytes")

                    body_to_scan = b"".join(body_chunks)

                    if body_to_scan:
                        # SECURITY FIX: Avoid mutating private framework attributes like request._body.
                        # Instead, create a new receive callable that yields the buffered body.
                        body_returned = False

                        async def receive() -> dict:
                            nonlocal body_returned
                            if not body_returned:
                                body_returned = True
                                return {"type": "http.request", "body": body_to_scan, "more_body": False}
                            return {"type": "http.disconnect"}

                        request = Request(request.scope, receive=receive)

                        # Sanitize then scan — NFKC + zero-width stripping
                        self._check_for_patterns(self._sanitize_input(body_to_scan), "request_body", detected_attacks)
                except BodyScanError:
                    raise
                except Exception as e:
                    logger.error("Error reading request body in AttackDetectionMiddleware", error=e)
                    # Fail closed on body read error
                    detected_attacks.append(
                        {
                            "attack_type": "invalid_body",
                            "pattern_name": "body_read_error",
                            "location": "request_body",
                            "matched_content": "Error reading body",
                        }
                    )

        return detected_attacks, request  # type: ignore[return-value]

    def _check_for_patterns(self, content: bytes, location: str, attacks: list[dict]) -> None:
        """Check content against all attack patterns using combined bytes regex.

        Uses a single compiled regex with named groups instead of iterating
        over 16 individual patterns. The named group identifies the attack type.
        """
        for match in COMBINED_ATTACK_RE.finditer(content):
            group_name = match.lastgroup
            if group_name and group_name in _GROUP_TO_ATTACK:
                pattern_name, attack_type = _GROUP_TO_ATTACK[group_name]

                # False positive mitigation for multipart/form-data boundaries
                if pattern_name == "sql_comment_end":
                    match_start = match.start()
                    line_start = content.rfind(b"\n", 0, match_start) + 1
                    line = content[line_start : match.end()]
                    if line.startswith(b"--") and b" " not in line.strip() and len(line.strip()) > 10:
                        continue

                attacks.append(
                    {
                        "attack_type": attack_type.value,
                        "pattern_name": pattern_name,
                        "location": location,
                        "matched_content": (
                            LogSanitizer.sanitize_message(content[:100].decode("utf-8", errors="replace")) if self._log_full_request else "[redacted]"  # noqa: E501
                        ),
                    }
                )

    async def _handle_attack_detection(
        self,
        request: Request,
        client_ip: str,
        detected_attacks: list[dict],
    ) -> Response | None:
        """Handle detected attacks based on configured mode.

        Returns:
            Response if attack should be blocked/banned, None if request should continue
        """
        path = request.url.path

        # Log the attack
        for attack in detected_attacks:
            event = create_security_event(
                event_type=SecurityEventType.ATTACK_DETECTED,
                client_ip=client_ip,
                request_path=path,
                reason=f"{attack['attack_type']} detected via {attack['pattern_name']}",
                extra={
                    "attack_type": attack["attack_type"],
                    "pattern_name": attack["pattern_name"],
                    "location": attack["location"],
                    "matched_content": attack.get("matched_content", "[redacted]"),
                    "mode": self._mode,
                },
            )
            log_security_event(event)

            logger.warning(
                "Attack detected | type=%s | pattern=%s | location=%s | ip=%s | path=%s | mode=%s",
                attack["attack_type"],
                attack["pattern_name"],
                attack["location"],
                client_ip,
                path,
                self._mode,
            )

        # Mode: log - allow request to continue
        if self._mode == "log":
            return None

        # Mode: block - return 400 Bad Request
        if self._mode == "block":
            from src.app.core.errors import ErrorCode, create_error_response

            return create_error_response(
                code=ErrorCode.VALIDATION_ERROR,
                message="Bad Request - Attack pattern detected",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Mode: ban - add to blacklist and return 403 Forbidden
        if self._mode == "ban":
            from datetime import UTC, datetime, timedelta
            try:
                # Add IP to blacklist with a 24-hour expiration to prevent permanent bans from false positives
                await self._filter_service.add_to_blacklist(
                    ip_address=client_ip,
                    reason=f"Auto-banned for {detected_attacks[0]['attack_type']} attack",
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
                logger.warning(
                    "IP auto-banned | ip=%s | attack=%s | mode=ban", client_ip, detected_attacks[0]["attack_type"]
                )
            except Exception as e:
                logger.error("Failed to add IP to blacklist during ban: %s", str(e))
                # Fall back to block behavior but still return 403

            from src.app.core.errors import ErrorCode, create_error_response

            return create_error_response(
                code=ErrorCode.FORBIDDEN,
                message="Forbidden - Attack detected and IP banned",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        return None

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request through attack detection."""
        path = request.url.path

        # Refresh runtime-configurable flags each request so tests and
        # dynamic settings overrides are respected without app restart.
        self._mode = getattr(settings, "ATTACK_DETECTION_MODE", "log")
        self._log_full_request = getattr(settings, "ATTACK_DETECTION_LOG_FULL_REQUEST", False)
        self._enabled = getattr(settings, "ATTACK_DETECTION_ENABLED", True)

        if self._mode not in ("log", "block", "ban"):
            logger.error("Invalid ATTACK_DETECTION_MODE '%s', falling back to 'log'", self._mode)
            self._mode = "log"

        # Skip if attack detection is disabled
        if not self._enabled:
            response = await call_next(request)
            return cast(Response, response)

        # Skip exempt paths
        if self._is_exempt_path(path):
            response = await call_next(request)
            return cast(Response, response)

        # Skip if IP is whitelisted (set by IPFilterMiddleware)
        is_whitelisted = getattr(request.state, "ip_whitelisted", False)
        if is_whitelisted:
            logger.debug("Attack detection skipped for whitelisted IP | path=%s", path)
            response = await call_next(request)
            return cast(Response, response)

        client_ip = getattr(request.state, "client_ip", None) or get_client_ip(request)

        # Detect attack patterns
        try:
            detected_attacks, request = await self._detect_attack_patterns(request)
            if isinstance(detected_attacks, Response):
                return detected_attacks
        except BodyScanError as e:
            logger.error("Body scan failed: %s", str(e))
            from src.app.core.errors import ErrorCode, create_error_response

            return create_error_response(
                code=ErrorCode.VALIDATION_ERROR,
                message="Payload Too Large - Request body exceeded maximum scan size",
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        if detected_attacks:
            # Handle based on mode
            block_response = await self._handle_attack_detection(request, client_ip, detected_attacks)

            if block_response is not None:
                return block_response

        # Allow request to continue
        response = await call_next(request)
        return cast(Response, response)
