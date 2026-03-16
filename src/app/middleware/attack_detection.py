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
from enum import Enum
from typing import cast

from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.security.logging import SecurityEventType, create_security_event, log_security_event
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
    ]
)

# Pre-compiled regex patterns for SQL injection detection
# These patterns are compiled at module load for optimal performance
SQL_INJECTION_PATTERNS: list[re.Pattern] = [
    # UNION SELECT pattern - classic SQL injection
    re.compile(r"(\bunion\b.*\bselect\b)", re.IGNORECASE),
    # OR 1=1 pattern - boolean-based blind injection
    re.compile(r"(\bor\b\s+1\s*=\s*1)", re.IGNORECASE),
    # DROP TABLE pattern - destructive SQL injection
    re.compile(r"(\bdrop\b.*\btable\b)", re.IGNORECASE),
    # DELETE FROM pattern - destructive SQL injection
    re.compile(r"(\bdelete\b.*\bfrom\b)", re.IGNORECASE),
    # Comment at end of line - comment injection
    re.compile(r"(--\s*$)", re.IGNORECASE),
    # Semicolon followed by comment - query termination
    re.compile(r"(;\s*--)", re.IGNORECASE),
    # EXEC xp_ pattern - stored procedure execution (SQL Server)
    re.compile(r"(\bexec\b.*\bxp_)", re.IGNORECASE),
]

# Pre-compiled regex patterns for XSS detection
XSS_PATTERNS: list[re.Pattern] = [
    # Script tag start - inline script injection
    re.compile(r"(<script[\s>])", re.IGNORECASE),
    # JavaScript protocol - protocol-based XSS
    re.compile(r"(javascript\s*:)", re.IGNORECASE),
    # Event handler pattern - onerror, onclick, etc.
    re.compile(r"(\bon\w+\s*=)", re.IGNORECASE),
    # Iframe tag - iframe injection
    re.compile(r"(<iframe[\s>])", re.IGNORECASE),
    # Object tag - object embedding
    re.compile(r"(<object[\s>])", re.IGNORECASE),
]

# Pre-compiled regex patterns for path traversal detection
PATH_TRAVERSAL_PATTERNS: list[re.Pattern] = [
    # Dot-dot-slash pattern - basic directory traversal
    re.compile(r"(\.\.\/)", re.IGNORECASE),
    # Triple dot pattern - some filter bypasses
    re.compile(r"(\.\.\.)", re.IGNORECASE),
    # URL-encoded dot-dot-slash - encoding bypass
    re.compile(r"(%2e%2e%2f)", re.IGNORECASE),
    # Partially-encoded dot-dot-slash - mixed encoding bypass
    re.compile(r"(%2e%2e\/)", re.IGNORECASE),
]

# Combined attack patterns dictionary for backward compatibility and type grouping
# Maps pattern names to (compiled_pattern, attack_type) tuples
ATTACK_PATTERNS: dict[str, tuple[re.Pattern, AttackType]] = {
    # SQL Injection patterns
    "sql_union_select": (SQL_INJECTION_PATTERNS[0], AttackType.SQL_INJECTION),
    "sql_or_1_equals_1": (SQL_INJECTION_PATTERNS[1], AttackType.SQL_INJECTION),
    "sql_drop_table": (SQL_INJECTION_PATTERNS[2], AttackType.SQL_INJECTION),
    "sql_delete_from": (SQL_INJECTION_PATTERNS[3], AttackType.SQL_INJECTION),
    "sql_comment_end": (SQL_INJECTION_PATTERNS[4], AttackType.SQL_INJECTION),
    "sql_semicolon_comment": (SQL_INJECTION_PATTERNS[5], AttackType.SQL_INJECTION),
    "sql_exec_xp": (SQL_INJECTION_PATTERNS[6], AttackType.SQL_INJECTION),
    # XSS patterns
    "xss_script_tag": (XSS_PATTERNS[0], AttackType.XSS),
    "xss_javascript_uri": (XSS_PATTERNS[1], AttackType.XSS),
    "xss_event_handler": (XSS_PATTERNS[2], AttackType.XSS),
    "xss_iframe": (XSS_PATTERNS[3], AttackType.XSS),
    "xss_object": (XSS_PATTERNS[4], AttackType.XSS),
    # Path Traversal patterns
    "path_dotdot_slash": (PATH_TRAVERSAL_PATTERNS[0], AttackType.PATH_TRAVERSAL),
    "path_triple_dot": (PATH_TRAVERSAL_PATTERNS[1], AttackType.PATH_TRAVERSAL),
    "path_encoded": (PATH_TRAVERSAL_PATTERNS[2], AttackType.PATH_TRAVERSAL),
    "path_partial_encoded": (PATH_TRAVERSAL_PATTERNS[3], AttackType.PATH_TRAVERSAL),
}

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
    "path_triple": ("path_triple_dot", AttackType.PATH_TRAVERSAL),
    "path_enc": ("path_encoded", AttackType.PATH_TRAVERSAL),
    "path_penc": ("path_partial_encoded", AttackType.PATH_TRAVERSAL),
}

# Combined bytes regex: single search() replaces 16 sequential pattern checks.
# Compiled as bytes pattern for zero-copy scanning of request body.
COMBINED_ATTACK_RE: re.Pattern[bytes] = re.compile(
    rb"(?P<sql_union>\bunion\b.*?\bselect\b)"
    rb"|(?P<sql_or>\bor\b\s+1\s*=\s*1)"
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
    rb"|(?P<path_triple>\.\.\.)"
    rb"|(?P<path_enc>%2e%2e%2f)"
    rb"|(?P<path_penc>%2e%2e\/)",
    re.IGNORECASE | re.MULTILINE,
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
        if path in EXEMPT_PATHS:
            return True
        return any(path.startswith(exempt + "/") for exempt in EXEMPT_PATHS)

    async def _detect_attack_patterns(self, request: Request) -> list[dict]:
        """Scan request for attack patterns using zero-copy bytes scanning.

        Checks:
        - URL path and query parameters (encoded to bytes)
        - Request body (scanned as raw bytes without UTF-8 decode)
        - Headers (encoded to bytes)

        Returns:
            List of detected attacks, each with pattern_type and location
        """
        detected_attacks: list[dict] = []

        # Encode small strings to bytes (fast) instead of decoding large body (slow)
        path_bytes = request.url.path.encode("utf-8", errors="replace")
        self._check_for_patterns(path_bytes, "url_path", detected_attacks)

        query = str(request.query_params)
        if query:
            self._check_for_patterns(query.encode("utf-8", errors="replace"), "query_string", detected_attacks)

        # Check specific headers
        headers_to_check = ["user-agent", "referer", "x-requested-with"]
        for header_name in headers_to_check:
            header_value = request.headers.get(header_name, "")
            if header_value:
                self._check_for_patterns(
                    header_value.encode("utf-8", errors="replace"),
                    f"header:{header_name}",
                    detected_attacks,
                )

        # Check request body if it's a POST/PUT/PATCH with content
        if request.method in ("POST", "PUT", "PATCH"):
            content_type = request.headers.get("content-type", "")
            text_content_types = ["application/json", "application/x-www-form-urlencoded", "text/"]
            if any(ct in content_type.lower() for ct in text_content_types):
                # BUG FIX: Content-Length pre-check previously used `pass` in the oversized
                # branch, letting execution fall through to `await request.body()` regardless.
                # Now we set a flag so extremely large payloads are genuinely skipped.
                # (They should already be blocked by BodyLimitMiddleware, but defense-in-depth.)
                skip_body = False
                content_length_str = request.headers.get("content-length")
                if content_length_str:
                    try:
                        if int(content_length_str) > 10 * 1024 * 1024:
                            skip_body = True
                            logger.debug(
                                "Skipping attack scan of oversized body | content-length=%s",
                                content_length_str,
                            )
                    except ValueError:
                        pass  # Malformed Content-Length; proceed with body read

                if not skip_body:
                    try:
                        body = await request.body()
                        if body:
                            # Re-inject body into a new receive channel so downstream handlers can read it.
                            # This is necessary because await request.body() consumes the stream.
                            async def receive():
                                return {"type": "http.request", "body": body, "more_body": False}

                            # Using request._receive (private API) as no public alternative exists.
                            try:
                                request._receive = receive
                            except AttributeError:
                                logger.error("Failed to reinject request body - request._receive not available")

                            # Scan raw bytes directly — no UTF-8 decode needed
                            self._check_for_patterns(body, "request_body", detected_attacks)
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

        return detected_attacks

    def _check_for_patterns(self, content: bytes, location: str, attacks: list[dict]) -> None:
        """Check content against all attack patterns using combined bytes regex.

        Uses a single compiled regex with named groups instead of iterating
        over 16 individual patterns. The named group identifies the attack type.
        """
        for match in COMBINED_ATTACK_RE.finditer(content):
            group_name = match.lastgroup
            if group_name and group_name in _GROUP_TO_ATTACK:
                pattern_name, attack_type = _GROUP_TO_ATTACK[group_name]
                attacks.append(
                    {
                        "attack_type": attack_type.value,
                        "pattern_name": pattern_name,
                        "location": location,
                        "matched_content": (
                            content[:100].decode("utf-8", errors="replace") if self._log_full_request else "[redacted]"
                        ),
                    }
                )

    def _get_attack_type(self, pattern_name: str) -> str:
        """Map pattern name to attack type category.

        NOTE: This method is kept for backward compatibility.
        The AttackType is now directly available from ATTACK_PATTERNS tuples.
        """
        attack_type_map = {
            "sql_": AttackType.SQL_INJECTION.value,
            "xss_": AttackType.XSS.value,
            "path_": AttackType.PATH_TRAVERSAL.value,
        }
        for prefix, attack_type in attack_type_map.items():
            if pattern_name.startswith(prefix):
                return attack_type
        return AttackType.SQL_INJECTION.value  # Default fallback

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
                attack['attack_type'], attack['pattern_name'], attack['location'], client_ip, path, self._mode
            )

        # Mode: log - allow request to continue
        if self._mode == "log":
            return None

        # Mode: block - return 400 Bad Request
        if self._mode == "block":
            return Response(
                content='{"detail":"Bad Request - Attack pattern detected"}',
                status_code=status.HTTP_400_BAD_REQUEST,
                media_type="application/json",
            )

        # Mode: ban - add to blacklist and return 403 Forbidden
        if self._mode == "ban":
            # Add IP to blacklist
            await self._filter_service.add_to_blacklist(
                ip_address=client_ip,
                reason=f"Auto-banned for {detected_attacks[0]['attack_type']} attack",
            )

            logger.warning(
                "IP auto-banned | ip=%s | attack=%s | mode=ban", client_ip, detected_attacks[0]['attack_type']
            )

            return Response(
                content='{"detail":"Forbidden - Attack detected and IP banned"}',
                status_code=status.HTTP_403_FORBIDDEN,
                media_type="application/json",
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

        client_ip = get_client_ip(request)

        # Detect attack patterns
        detected_attacks = await self._detect_attack_patterns(request)

        if detected_attacks:
            # Handle based on mode
            block_response = await self._handle_attack_detection(request, client_ip, detected_attacks)

            if block_response is not None:
                return block_response

        # Allow request to continue
        response = await call_next(request)
        return cast(Response, response)
