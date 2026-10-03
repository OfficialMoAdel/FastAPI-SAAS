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

import asyncio
import logging
import posixpath
import re
import unicodedata
from enum import Enum

from fastapi import Request, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.app.core.config import settings
from src.app.core.guard.ip_rules import IpRules
from src.app.core.logger import get_logger
from src.app.core.observability.log_sanitizer import LogSanitizer
from src.app.core.security.logging import SecurityEventType, create_security_event, log_security_event
from src.app.core.utils.ip_utils import get_client_ip
from src.app.middleware.body_limit import BodySizeLimitExceeded

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


from src.app.core.middleware_exempt import COMMON_EXEMPT_PATHS
from src.app.core.middleware_exempt import is_path_exempt as _shared_is_path_exempt

EXEMPT_PATHS: frozenset[str] = COMMON_EXEMPT_PATHS  # backward-compat re-export


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
# P1-7 FP hardening: xss_event requires tag context (event handlers only
# execute inside <tag ...> — bare "only=" query/form params must not match);
# sql_comment requires SQL context (a quote or semicolon earlier on the same
# line — bare trailing "--" in prose/markdown must not match).
COMBINED_ATTACK_RE: re.Pattern[bytes] = re.compile(
    rb"(?P<sql_union>\bunion\b.*?\bselect\b)"
    rb"|(?P<sql_or>\bor\b\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+['\"]?)"
    rb"|(?P<sql_drop>\bdrop\b.*?\btable\b)"
    rb"|(?P<sql_delete>\bdelete\b.*?\bfrom\b)"
    rb"|(?P<sql_comment>(?:'[^'\r\n]{0,200}|;[^\r\n]{0,200}?)--[^\r\n]*$)"
    rb"|(?P<sql_semicomment>;\s*--)"
    rb"|(?P<sql_exec>\bexec\b.*?\bxp_)"
    rb"|(?P<xss_script><script[\s>])"
    rb"|(?P<xss_jsuri>javascript\s*:)"
    rb"|(?P<xss_event><[a-zA-Z][^<>]{0,512}?[\s/]on[a-zA-Z]+\s*=[\"']?)"
    rb"|(?P<xss_iframe_tag><iframe[\s>])"
    rb"|(?P<xss_object_tag><object[\s>])"
    rb"|(?P<path_dotdot>\.\.\/)"
    rb"|(?P<path_enc>%2e%2e%2f)"
    rb"|(?P<path_penc>%2e%2e\/)"
    rb"|(?P<cmd_inj>(?:;|\|\||&&)\s*(?:cat|ls|id|whoami|ping|curl|wget)\b)"
    rb"|(?P<ssrf_cloud>169\.254\.169\.254)",
    re.IGNORECASE | re.MULTILINE,
)

# P1-7: necessary-literal prefilter for the combined regex. Each literal is a
# *necessary* (not sufficient) condition for its group — every byte-string the
# group can match contains the literal — so "no literal present" provably
# implies "regex cannot match" and we can skip the ~1.1 ms finditer. Any hit
# falls through to the unchanged full regex, so false positives only cost the
# regex run, never a missed verdict. Literals are lowercase ASCII and content
# is ASCII-folded via bytes.lower() (bytes IGNORECASE folds ASCII only), which
# keeps the implication sound.
#
# group -> literal: sql_union->"union" | sql_or->"or" | sql_drop->"drop" |
#   sql_delete->"delete" | sql_comment/sql_semicomment->"--" |
#   sql_exec->"exec" | xss_script/xss_event/xss_iframe/xss_object->"<" |
#   xss_jsuri->"javascript" | path_dotdot->"../" | path_enc->"%2e%2e%2f" |
#   path_penc->"%2e%2e/" | cmd_inj->";"/"||"/"&&" (one separator is required) |
#   ssrf_cloud->"169.254.169.254".
_PREFILTER_LITERALS_BYTES: frozenset[bytes] = frozenset(
    [
        b"union",
        b"or",
        b"drop",
        b"delete",
        b"--",
        b"exec",
        b"<",
        b"javascript",
        b"../",
        b"%2e%2e%2f",
        b"%2e%2e/",
        b";",
        b"||",
        b"&&",
        b"169.254.169.254",
    ]
)

try:
    import ahocorasick as _ahocorasick
except ImportError:  # pragma: no cover - precaution: stdlib fallback below
    _ahocorasick = None  # type: ignore[assignment]


def _build_prefilter_automaton() -> object | None:
    """Build the Aho-Corasick automaton once, or None to use str.find fallback."""
    if _ahocorasick is None:
        return None
    try:
        automaton = _ahocorasick.Automaton()
        # str automaton over latin-1: pure-ASCII literals make a latin-1
        # decode of ASCII-folded bytes exactly equivalent to a bytes search.
        for literal in _PREFILTER_LITERALS_BYTES:
            automaton.add_word(literal.decode("latin-1"), literal)
        automaton.make_automaton()
        return automaton  # type: ignore[no-any-return]  # untyped C extension
    except Exception:  # pragma: no cover - precaution: fall back, never fail boot
        return None


_PREFILTER_AUTOMATON: object | None = _build_prefilter_automaton()


def _attack_prefilter_hit(content: bytes) -> bool:
    """Return True if any necessary literal is present (regex may match).

    Prefers the C automaton; falls back to a short-circuiting bytes ``in``
    chain when pyahocorasick is unavailable or the automaton failed to build.
    Read-only against the shared automaton, so safe from request threads and
    the scan executor.
    """
    lowered = content.lower()
    automaton = _PREFILTER_AUTOMATON
    if automaton is not None:
        try:
            for _ in automaton.iter(lowered.decode("latin-1")):  # type: ignore[union-attr, attr-defined]  # untyped C extension
                return True
            return False
        except Exception:  # pragma: no cover - precaution: degrade to fallback
            pass
    for literal in _PREFILTER_LITERALS_BYTES:
        if literal in lowered:
            return True
    return False


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


# P2-7: security-relevant headers scanned per request. Hoisted from a
# per-request list literal in _detect_attack_patterns — the rebuild cost was
# nanosecond-scale noise, but a module-level frozen tuple keeps the scanned
# set visible in one place and avoids re-allocating on every request.
_HEADERS_TO_CHECK: tuple[str, ...] = (
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
)


# P1-6: byte form of the scanned header set, so the native ASGI __call__
# can filter scope["headers"] in a single pass without per-name str lookups.
_HEADERS_TO_CHECK_BYTES: frozenset[bytes] = frozenset(h.encode("latin-1") for h in _HEADERS_TO_CHECK)


# nrgi.3: the only HTTP methods whose body is scanned. Hoisted from the
# inline tuple literal so the gate is a single named fact rather than a
# condition buried mid-function, and so the tests that pin it import the
# same object the middleware branches on. GET/HEAD/DELETE/OPTIONS are
# excluded: their attack surface is the path, query string and headers,
# all of which are scanned for EVERY method above the gate.
_BODY_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH"})


class AttackDetectionMiddleware:
    """Native ASGI middleware (not BaseHTTPMiddleware) to detect malicious patterns.

    P1-6: converted from BaseHTTPMiddleware, which pays an anyio task-group +
    full-body buffer/replay per POST/PUT/PATCH. The native __call__ drains the
    body once and replays it downstream via a wrapped receive channel, so
    streaming responses are never buffered and no task-group hop is incurred.
    (Template: RateLimitMiddleware.__call__ — plain class with
    ``async def __call__(self, scope, receive, send)``.)
    """

    # P1: bodies larger than this are scanned in the executor (bytes).
    _BODY_OFFLOAD_THRESHOLD = 16 * 1024
    # Cap on buffered body bytes held for the pattern scan. Bodies beyond
    # this are unscannable by policy (see _scan_request) — draining stops
    # early and the remainder streams to downstream live via delegation, so
    # a 10MB chunked upload never sits fully buffered here.
    _MAX_SCAN_SIZE = 128 * 1024
    """Middleware to detect and handle malicious attack patterns.

    Execution order is pinned by GUARD_ORDER (src/app/core/guard/ordering.py):
    this middleware runs AFTER RateLimit (a cheap 429 skips the expensive
    body scan) and AFTER BodyLimit (oversized payloads 413 before the scan
    buffers them). tests/unit/test_middleware_order.py pins the contract —
    do not restate the sequence here.

    Operation modes:
    - log: Detect attacks, log them, but allow request to continue
    - block: Detect attacks, log them, return 400 Bad Request
    - ban: Detect attacks, log them, add IP to blacklist, return 403 Forbidden

    Whitelist bypass: If request.state.ip_whitelisted is True, skip all detection.
    """

    def __init__(self, app: ASGIApp, ip_rules: IpRules | None = None) -> None:
        self.app = app
        # IP rules injected at the seam (default: Redis-backed service).
        # Middleware no longer reaches across into the security feature
        # module — the rules object owns that knowledge internally.
        from src.app.core.guard.ip_rules import RedisIpRules

        # `_filter_service` is the seam instance (RedisIpRules satisfies IpRules:
        # check_ip_status + ban_ip). Kept under the historical attribute name so
        # tests that patch/swap it keep exercising the ban path.
        self._filter_service: IpRules = ip_rules if ip_rules is not None else RedisIpRules()
        # Per-request flags: initialized here so unit tests that construct the
        # middleware directly (without running __call__) see the configured
        # defaults; __call__ refreshes them each request for dynamic overrides.
        self._mode = settings.attack_detection.ATTACK_DETECTION_MODE
        self._log_full_request = settings.attack_detection.ATTACK_DETECTION_LOG_FULL_REQUEST
        self._enabled = settings.attack_detection.ATTACK_DETECTION_ENABLED

    def _is_exempt_path(self, path: str) -> bool:
        """Check if path is exempt from attack detection.

        *begins* with an exempt string (e.g. /docs-attack matched /docs, bypassing
        attack scanning). Now we require either an exact match or a true sub-path
        (path starts with exempt + "/").
        """
        normalized_path = posixpath.normpath(path)
        # P2-7: delegate to the shared helper (common set is the whole
        # contract — the old per-middleware extra frozenset was empty).
        return _shared_is_path_exempt(normalized_path)

    @staticmethod
    def _sanitize_input(content: bytes) -> bytes:
        """Sanitize input bytes to defeat Unicode-based regex bypass attacks.

        Applies:
        1. NFKC normalization — collapses compatibility codepoints (e.g. U+FF35 → U)
        2. Zero-width character stripping — removes invisible chars that split tokens

        Falls back to raw bytes if decoding fails (binary payloads).

        P1 FIX: pure-ASCII content cannot be altered by NFKC normalization or
        zero-width stripping, so skip the decode/normalize/re-encode cycle
        entirely. ``bytes.isascii()`` is a C-speed scan and the vast majority
        of real headers/paths are ASCII — this removes most of the per-request
        sanitization CPU without weakening detection.
        """
        if content.isascii():
            return content

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

    @staticmethod
    async def _drain_body(receive: Receive) -> tuple[bytes | None, Receive]:
        """Drain the request body; return (body, replay_receive).

        Returns ``None`` as the body when the stream already exceeds
        ``_MAX_SCAN_SIZE`` — the caller skips the pattern scan (same verdict
        as the oversized-body path in ``_scan_request``). In that case only
        the prefix is buffered; ``replay_receive`` serves the buffered
        prefix first, then delegates live to the underlying channel so the
        remainder streams to downstream without ever being held in memory.

        P1-6: native ASGI has no _CachedRequest replay, so the middleware
        drains ``receive`` itself and hands downstream a wrapped channel that
        replays the buffered bytes once, then delegates to the underlying
        channel so ``http.disconnect`` (and any later frames) propagate.
        Delegation is required: swallowing disconnect breaks
        ``StreamingResponse.listen_for_disconnect`` (hangs SSE under
        TestClient; zombie generators on real servers).
        """
        chunks: list[bytes] = []
        total = 0
        complete = False
        while True:
            message = await receive()
            if message["type"] != "http.request":
                complete = True  # disconnect or terminal frame: scan what we have
                break
            chunk = message.get("body", b"")
            if chunk:
                chunks.append(chunk)
                total += len(chunk)
            if not message.get("more_body", False):
                complete = True
                break
            if total > AttackDetectionMiddleware._MAX_SCAN_SIZE:
                break  # oversized: prefix buffered, rest streams via delegation
        body: bytes | None = (
            None if (not complete and total > AttackDetectionMiddleware._MAX_SCAN_SIZE) else b"".join(chunks)
        )

        pending = list(chunks)

        async def replay_receive() -> Message:
            if pending:
                chunk = pending.pop(0)
                return {
                    "type": "http.request",
                    "body": chunk,
                    "more_body": bool(pending) or not complete,
                }
            if complete:
                # Fully drained and replayed: mirror the old single-shot
                # replay (terminal empty frame), then delegate.
                complete_consumed = getattr(replay_receive, "_terminal_sent", False)
                if not complete_consumed:
                    setattr(replay_receive, "_terminal_sent", True)
                    return {"type": "http.request", "body": b"", "more_body": False}
            return await receive()

        return body, replay_receive

    async def _scan_request(
        self,
        path: str,
        query_string: bytes,
        raw_headers: list[tuple[bytes, bytes]],
        method: str,
        receive: Receive,
    ) -> tuple[list[dict[str, str]], Receive]:
        """Scan path, query, headers and body for attack patterns.

        P1-6 scan-trim vs the old BaseHTTP version:
        - query_string is scanned as raw scope bytes (no str(query_params)
          decode/re-encode round-trip; percent-encoded traversal stays intact
          for the path_enc patterns);
        - headers are filtered in ONE pass over scope["headers"] (already
          lowercase bytes — no per-name dict lookup + str→bytes encode);
        - path comes from scope["path"] (no Request.url construction).
        Body handling (MAX_SCAN_SIZE skip, executor offload) is unchanged.

        Returns (detected attacks, receive) where receive replays the body
        for POST/PUT/PATCH and is the original channel otherwise.
        """
        detected_attacks: list[dict] = []

        # Encode small strings to bytes (fast) instead of decoding large body (slow)
        path_bytes = self._sanitize_input(path.encode("utf-8", errors="replace"))
        self._check_for_patterns(path_bytes, "url_path", detected_attacks)

        if query_string:
            self._check_for_patterns(
                self._sanitize_input(query_string),
                "query_string",
                detected_attacks,
            )

        # Check security-relevant headers in a single pass (expanded from original 3)
        content_length: bytes | None = None
        for header_name, header_value in raw_headers:
            if header_name == b"content-length" and content_length is None:
                content_length = header_value
            if header_value and header_name in _HEADERS_TO_CHECK_BYTES:
                self._check_for_patterns(
                    self._sanitize_input(header_value),
                    f"header:{header_name.decode('latin-1')}",
                    detected_attacks,
                )

        # nrgi.3: the body scan is method-gated, and this is the only skip in
        # the scan that was silent. The guard is load-bearing, not stylistic:
        # a bare logger.debug() through this structlog stack costs ~44.6us,
        # 6.7x the ENTIRE bodyless-GET scan it would announce (6.7us), so
        # unguarded this would make the hot path slower than the work it
        # reports on. Guarded, the check costs ~1.9us.
        #
        # Path, query string and headers are scanned ABOVE, for every method:
        # a GET carrying SQLi in its query string is still detected, so this
        # skip removes no attack coverage. See docs/adr/0008.
        if method not in _BODY_METHODS:
            if logger.is_enabled_for(logging.DEBUG):
                logger.debug(
                    "attack_detection_skipped_body_scan_for_method",
                    method=method,
                    path=path,
                )
        else:
            # Removed early return for multipart/form-data to actually apply the BUG FIX.

            # BUG FIX: Scan all content types to prevent bypass via multipart/form-data or other types
            skip_body = False
            # NOTE: ATTACK_DETECTION_MAX_SCAN_SIZE has no Settings field
            # (phantom knob). The 128KB default lives on _MAX_SCAN_SIZE;
            # adding a real field is tracked with the typed-settings sweep.
            MAX_SCAN_SIZE = self._MAX_SCAN_SIZE
            if content_length:
                try:
                    if int(content_length) > MAX_SCAN_SIZE:
                        skip_body = True
                        logger.debug(
                            "attack_scan_skipped_oversized_body",
                            source="content_length",
                            size=MAX_SCAN_SIZE,
                            content_length=content_length.decode("latin-1", errors="replace"),
                        )
                except ValueError:
                    pass  # Malformed Content-Length; proceed with body read

            if not skip_body:
                try:
                    body_to_scan, receive = await self._drain_body(receive)

                    if body_to_scan is None:
                        # Stream already exceeded MAX_SCAN_SIZE during drain:
                        # only the prefix was buffered, the rest is streaming
                        # downstream live. Same skip verdict as below.
                        logger.warning(
                            "attack_scan_skipped_oversized_body",
                            source="chunked_drain",
                            max_scan=MAX_SCAN_SIZE,
                        )
                    elif body_to_scan:
                        body_size = len(body_to_scan)
                        # Consistent with the Content-Length pre-check above:
                        # bodies larger than MAX_SCAN_SIZE skip the pattern scan
                        # and continue. Rejecting here (413) would close no gap —
                        # an attacker can declare a large Content-Length and hit
                        # that skip path anyway — it would only break legitimate
                        # chunked uploads. The outer RequestBodyLimitMiddleware
                        # still enforces the hard MAX_BODY_SIZE cap.
                        if body_size > MAX_SCAN_SIZE:
                            logger.warning(
                                "attack_scan_skipped_oversized_body",
                                source="buffered",
                                size=body_size,
                                max_scan=MAX_SCAN_SIZE,
                            )
                        else:
                            # Sanitize then scan — NFKC + zero-width stripping.
                            # P1 FIX: bodies above _BODY_OFFLOAD_THRESHOLD are
                            # sanitized+scanned in the thread pool so a large
                            # multipart/JSON payload cannot stall the event loop
                            # for every concurrent request.
                            sanitized_body = self._sanitize_input(body_to_scan)
                            if len(sanitized_body) > self._BODY_OFFLOAD_THRESHOLD:
                                await asyncio.get_running_loop().run_in_executor(
                                    None,
                                    lambda: self._check_for_patterns(sanitized_body, "request_body", detected_attacks),
                                )
                            else:
                                self._check_for_patterns(sanitized_body, "request_body", detected_attacks)
                except BodySizeLimitExceeded:
                    # S-07 FIX: an oversized body is a SIZE violation, not an
                    # attack. `_drain_body` reads the same shared receive channel
                    # that RequestBodyLimitMiddleware's `limited_receive` guards,
                    # and that guard raises BodySizeLimitExceeded mid-stream.
                    # The broad `except Exception` below used to swallow it and
                    # record an `invalid_body`/`body_read_error` attack verdict,
                    # so a legitimate client over the size cap got a 400/403 — and
                    # in `ban` mode a 24h IP ban — instead of the 413 the body
                    # limit middleware is designed to return.
                    #
                    # Re-raise so the exception escapes to the body-limit
                    # middleware's own 413 handler. This MUST stay a distinct
                    # clause placed BEFORE the broad handler: genuine body-read
                    # errors still fail closed below.
                    raise
                except Exception as e:
                    logger.error("attack_detection_body_read_failed", error=str(e))
                    # Fail closed on body read error
                    detected_attacks.append(
                        {
                            "attack_type": "invalid_body",
                            "pattern_name": "body_read_error",
                            "location": "request_body",
                            "matched_content": "Error reading body",
                        }
                    )

        return detected_attacks, receive

    def _check_for_patterns(self, content: bytes, location: str, attacks: list[dict]) -> None:
        """Check content against all attack patterns using combined bytes regex.

        Uses a single compiled regex with named groups instead of iterating
        over 16 individual patterns. The named group identifies the attack type.

        P1-7: a necessary-literal prefilter (Aho-Corasick, str.find fallback)
        skips the regex entirely when no group can possibly match.
        """
        if not _attack_prefilter_hit(content):
            return
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
                            LogSanitizer.sanitize_message(content[:100].decode("utf-8", errors="replace"))
                            if self._log_full_request
                            else "[redacted]"  # noqa: E501
                        ),
                    }
                )

    async def _handle_attack_detection(
        self,
        path: str,
        client_ip: str,
        detected_attacks: list[dict],
    ) -> Response | None:
        """Handle detected attacks based on configured mode.

        Returns:
            Response if attack should be blocked/banned, None if request should continue
        """

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
                "attack_detected",
                type=attack["attack_type"],
                pattern=attack["pattern_name"],
                location=attack["location"],
                ip=client_ip,
                path=path,
                mode=self._mode,
            )

        # Mode: log - allow request to continue
        if self._mode == "log":
            return None

        # Mode: block - return 400 Bad Request
        if self._mode == "block":
            from src.app.core.errors import middleware_reject

            return middleware_reject("attack_blocked")

        # Mode: ban - add to blacklist and return 403 Forbidden
        if self._mode == "ban":
            from datetime import UTC, datetime, timedelta

            try:
                # Ban via the injected IpRules seam — never touch the
                # security feature module directly (layering).
                await self._filter_service.ban_ip(
                    ip_address=client_ip,
                    reason=f"Auto-banned for {detected_attacks[0]['attack_type']} attack",
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
                logger.warning(
                    "ip_auto_banned",
                    ip=client_ip,
                    attack=detected_attacks[0]["attack_type"],
                    mode=self._mode,
                )
            except Exception as e:
                logger.error(
                    "ip_auto_ban_failed",
                    error=str(e),
                )
                # Fall back to block behavior but still return 403

            from src.app.core.errors import middleware_reject

            return middleware_reject("attack_banned")

        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process request through attack detection (native ASGI)."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")

        # Refresh runtime-configurable flags each request so tests and
        # dynamic settings overrides are respected without app restart.
        self._mode = settings.attack_detection.ATTACK_DETECTION_MODE
        self._log_full_request = settings.attack_detection.ATTACK_DETECTION_LOG_FULL_REQUEST
        self._enabled = settings.attack_detection.ATTACK_DETECTION_ENABLED

        if self._mode not in ("log", "block", "ban"):
            # S-7 FIX: an invalid mode previously degraded SILENTLY to
            # log-only — a posture downgrade attackers could rely on. Fail
            # closed instead: 'block' keeps the service available while
            # refusing known-malicious payloads, and every worker logs the
            # misconfig loudly at startup.
            logger.error(
                "invalid_attack_detection_mode",
                mode=self._mode,
                allowed=["log", "block", "ban"],
                fell_back_to="block",
                fix="set ATTACK_DETECTION_MODE to one of log|block|ban",
            )
            self._mode = "block"

        # Skip if attack detection is disabled
        if not self._enabled:
            await self.app(scope, receive, send)
            return

        # Skip exempt paths
        if self._is_exempt_path(path):
            await self.app(scope, receive, send)
            return

        # Skip if IP is whitelisted (set by IPFilterMiddleware).
        # P1-6: read from scope state directly — same dict BaseHTTP's
        # Request.state was backed by, so cross-middleware semantics hold.
        scope_state = scope.get("state", {})
        if scope_state.get("ip_whitelisted", False):
            logger.debug(
                "attack_detection_skipped_whitelisted_ip",
                path=path,
            )
            await self.app(scope, receive, send)
            return

        client_ip = scope_state.get("client_ip") or get_client_ip(Request(scope))

        # Detect attack patterns (drains + replays body on POST/PUT/PATCH)
        detected_attacks, receive = await self._scan_request(
            path,
            scope.get("query_string", b""),
            scope.get("headers", []),
            scope.get("method", "GET"),
            receive,
        )

        if detected_attacks:
            # Handle based on mode
            block_response = await self._handle_attack_detection(path, client_ip, detected_attacks)

            if block_response is not None:
                await block_response(scope, receive, send)
                return

        # Allow request to continue
        await self.app(scope, receive, send)
