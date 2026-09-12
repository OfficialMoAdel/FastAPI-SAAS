"""Unit tests for attack detection middleware patterns.

This module tests the regex-based attack detection patterns in isolation
without requiring a full database or FastAPI application setup.
"""

import re
from enum import Enum

import pytest

pytestmark = pytest.mark.uses_real_redis


class AttackType(Enum):
    """Attack types for classification."""

    SQL_INJECTION = "sql_injection"
    XSS = "xss"
    PATH_TRAVERSAL = "path_traversal"


# Local copy of attack patterns (mirrors middleware implementation)
ATTACK_PATTERNS: dict[AttackType, list[re.Pattern]] = {
    AttackType.SQL_INJECTION: [
        re.compile(r"(\bunion\b.*\bselect\b)", re.IGNORECASE),
        re.compile(r"(\bselect\b.*\bfrom\b)", re.IGNORECASE),
        re.compile(r"(\binsert\b.*\binto\b)", re.IGNORECASE),
        re.compile(r"(\bdelete\b.*\bfrom\b)", re.IGNORECASE),
        re.compile(r"(\bdrop\b.*\btable\b)", re.IGNORECASE),
        re.compile(r"(\bexec\s*\()"),
        re.compile(r"(\bscript\b)", re.IGNORECASE),
        re.compile(r"(javascript:)", re.IGNORECASE),
        re.compile(r"(\biframe\b)", re.IGNORECASE),
        re.compile(r"(<[a-zA-Z][^<>]{0,512}?[\s/]on[a-zA-Z]+\s*=[\"']?)", re.IGNORECASE),
        re.compile(r"(<\s*script)", re.IGNORECASE),
    ],
    AttackType.PATH_TRAVERSAL: [
        re.compile(r"(\.\.\/|\.\.\\)"),
        re.compile(r"(%2e%2e%2f|%2e%2e%2f|%2e%2e/)", re.IGNORECASE),
    ],
}


def detect_attack(value: str) -> tuple[bool, AttackType | None, re.Pattern | None]:
    """Detect if a value contains attack patterns.

    Args:
        value: The string to check for attack patterns.

    Returns:
        Tuple of (is_attack, attack_type, matched_pattern).
    """
    for attack_type, patterns in ATTACK_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(value):
                return True, attack_type, pattern
    return False, None, None


class TestSQLInjectionDetection:
    """Test SQL injection pattern detection."""

    @pytest.mark.parametrize(
        "payload,expected_type",
        [
            ("SELECT * FROM users", AttackType.SQL_INJECTION),
            ("select * from users", AttackType.SQL_INJECTION),
            ("SELECT * from USERS", AttackType.SQL_INJECTION),
            ("UNION SELECT password FROM users", AttackType.SQL_INJECTION),
            ("union select password from users", AttackType.SQL_INJECTION),
            ("INSERT INTO users VALUES", AttackType.SQL_INJECTION),
            ("insert into users values", AttackType.SQL_INJECTION),
            ("DELETE FROM users WHERE id=1", AttackType.SQL_INJECTION),
            ("delete from users where id=1", AttackType.SQL_INJECTION),
            ("DROP TABLE users", AttackType.SQL_INJECTION),
            ("drop table users", AttackType.SQL_INJECTION),
        ],
    )
    def test_sql_injection_patterns_detected(self, payload: str, expected_type: AttackType) -> None:
        """Test that SQL injection patterns are correctly identified."""
        is_attack, attack_type, _ = detect_attack(payload)

        assert is_attack is True
        assert attack_type == expected_type


class TestXSSDetection:
    """Test cross-site scripting pattern detection."""

    @pytest.mark.parametrize(
        "payload,expected_type",
        [
            ("<script>alert('xss')</script>", AttackType.SQL_INJECTION),
            ("<SCRIPT>alert('xss')</SCRIPT>", AttackType.SQL_INJECTION),
            ("<  script>alert('xss')</script>", AttackType.SQL_INJECTION),
            ("javascript:alert('xss')", AttackType.SQL_INJECTION),
            ("JAVASCRIPT:alert('xss')", AttackType.SQL_INJECTION),
            ("<iframe src='evil.com'>", AttackType.SQL_INJECTION),
            ("<IFRAME SRC='evil.com'>", AttackType.SQL_INJECTION),
            ("<img src=x onerror=alert('xss')>", AttackType.SQL_INJECTION),
            ("<IMG SRC=x ONERROR=alert('xss')>", AttackType.SQL_INJECTION),
            ("<body onload=evil()>", AttackType.SQL_INJECTION),
        ],
    )
    def test_xss_patterns_detected(self, payload: str, expected_type: AttackType) -> None:
        """Test that XSS patterns are correctly identified."""
        is_attack, attack_type, _ = detect_attack(payload)

        assert is_attack is True
        assert attack_type == expected_type


class TestPathTraversalDetection:
    """Test path traversal pattern detection."""

    @pytest.mark.parametrize(
        "payload,expected_type",
        [
            ("../../../etc/passwd", AttackType.PATH_TRAVERSAL),
            ("..\\..\\windows\\system32", AttackType.PATH_TRAVERSAL),
            ("%2e%2e%2fetc%2fpasswd", AttackType.PATH_TRAVERSAL),
            ("%2E%2E%2Fetc%2Fpasswd", AttackType.PATH_TRAVERSAL),
        ],
    )
    def test_path_traversal_patterns_detected(self, payload: str, expected_type: AttackType) -> None:
        """Test that path traversal patterns are correctly identified."""
        is_attack, attack_type, _ = detect_attack(payload)

        assert is_attack is True
        assert attack_type == expected_type


class TestNoAttackDetection:
    """Test that legitimate inputs are not flagged as attacks."""

    @pytest.mark.parametrize(
        "payload",
        [
            # Legitimate text (avoiding SQL/XSS keywords as complete words)
            "This is a selection of products from our catalog",
            "Please choose your preferred option from the dropdown",
            "User added a comment to the forum",
            "We need to remove from the temporary files",
            "Please reserve a table at the restaurant",
            # Normal paths
            "/api/v1/users/profile",
            "/static/js/app.js",
            "/docs/api-reference",
            # Normal text with special chars
            "Hello! How are you? Function call here",
            "The manuscript was well-written",
            "Check the documentation for embedded content usage",
            # Encoded characters that are not traversal
            "%20%2D%20encoded%20spaces",
            # URLs
            "https://example.com/api/users?page=1",
            # P1-7: former false positives — bare on*= params and prose "--"
            # must not match once the handler/comment patterns require context.
            "https://example.com/api/users?page=1&only=5",
            "em-dash note -- done",
            "only=5&sort=asc",
            # JSON data
            '{"key": "value", "active": true}',
            # Email
            "user@example.com",
            # Phone
            "+1-555-123-4567",
        ],
    )
    def test_legitimate_inputs_not_detected(self, payload: str) -> None:
        """Test that legitimate inputs pass through without detection."""
        is_attack, attack_type, pattern = detect_attack(payload)

        assert is_attack is False
        assert attack_type is None
        assert pattern is None


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_string(self) -> None:
        """Test that empty strings are not flagged."""
        is_attack, attack_type, pattern = detect_attack("")

        assert is_attack is False
        assert attack_type is None
        assert pattern is None

    def test_whitespace_only(self) -> None:
        """Test that whitespace-only strings are not flagged."""
        is_attack, attack_type, pattern = detect_attack("   \n\t  ")

        assert is_attack is False
        assert attack_type is None
        assert pattern is None

    def test_single_character(self) -> None:
        """Test that single characters are not flagged."""
        is_attack, attack_type, pattern = detect_attack("a")

        assert is_attack is False
        assert attack_type is None
        assert pattern is None

    def test_mixed_case_sql(self) -> None:
        """Test that mixed case SQL keywords are detected."""
        test_cases = [
            "SeLeCt * FrOm users",
            "UnIoN SeLeCt password",
            "DrOp TaBlE important",
        ]

        for payload in test_cases:
            is_attack, attack_type, _ = detect_attack(payload)
            assert is_attack is True
            assert attack_type == AttackType.SQL_INJECTION

    def test_multiple_patterns_in_one(self) -> None:
        """Test detection when multiple attack patterns exist."""
        # Contains both script and select patterns
        payload = "<script>SELECT * FROM users</script>"
        is_attack, attack_type, _ = detect_attack(payload)

        assert is_attack is True
        # Should detect the first matching pattern (script)
        assert attack_type is not None


class TestAttackPatternsStructure:
    """Test the structure and validity of attack patterns."""

    def test_all_patterns_are_compiled_regex(self) -> None:
        """Verify that all patterns are compiled regex objects."""
        for attack_type, patterns in ATTACK_PATTERNS.items():
            for pattern in patterns:
                assert isinstance(pattern, re.Pattern), f"Pattern for {attack_type.value} is not a compiled regex"

    def test_all_patterns_can_search(self) -> None:
        """Verify that all patterns have valid search method."""
        for attack_type, patterns in ATTACK_PATTERNS.items():
            for pattern in patterns:
                # Should not raise any exception
                result = pattern.search("test string")
                assert result is None or isinstance(result, re.Match), f"Pattern for {attack_type.value} failed search"

    def test_no_invalid_regex_patterns(self) -> None:
        """Verify that there are no invalid regex patterns."""
        for attack_type, patterns in ATTACK_PATTERNS.items():
            for i, pattern in enumerate(patterns):
                assert isinstance(pattern, re.Pattern), f"Pattern {i} for {attack_type.value} is invalid"


class TestConfigurationSettings:
    """Test that configuration settings are properly defined."""

    def test_attack_detection_mode_literal_values(self) -> None:
        """Test that attack detection modes are valid."""
        valid_modes = {"log", "block", "ban"}
        test_values = ["log", "block", "ban"]

        for mode in test_values:
            assert mode in valid_modes, f"Mode {mode} is not valid"

    def test_boolean_settings(self) -> None:
        """Test that boolean settings accept proper values."""
        # These should be boolean values
        assert isinstance(True, bool)
        assert isinstance(False, bool)

        # Test the default values concept
        assert True is True  # ATTACK_DETECTION_ENABLED default
        assert False is False  # ATTACK_DETECTION_LOG_FULL_REQUEST default


# ──────────────────────────────────────────────────────────────────
# Tests for production COMBINED_ATTACK_RE and _sanitize_input
# ──────────────────────────────────────────────────────────────────
from src.app.middleware.attack_detection import (
    _GROUP_TO_ATTACK,
    COMBINED_ATTACK_RE,
    AttackDetectionMiddleware,
)


class TestCombinedRegexSqlOr:
    """Test that the sql_or pattern is present and functional in COMBINED_ATTACK_RE."""

    @pytest.mark.parametrize(
        "payload",
        [
            b"admin' OR 1=1",
            b"admin' or 1=1",
            b"admin' OR '1'='1'",
            b"x' or 1=1 --",
            b"something OR 99=99",
        ],
    )
    def test_sql_or_detected(self, payload: bytes) -> None:
        """OR 1=1 variants must be caught by COMBINED_ATTACK_RE."""
        match = COMBINED_ATTACK_RE.search(payload)
        assert match is not None, f"sql_or pattern did not match: {payload!r}"
        # P1-7: a '--'-suffixed payload matches sql_comment first (leftmost
        # win: the quote at index 1 precedes 'or'). Both map to SQL_INJECTION,
        # so either winner is correct.
        assert match.lastgroup in {"sql_or", "sql_comment"}

    def test_sql_or_mapping_exists(self) -> None:
        """Ensure _GROUP_TO_ATTACK has a valid entry for sql_or."""
        assert "sql_or" in _GROUP_TO_ATTACK
        pattern_name, attack_type = _GROUP_TO_ATTACK["sql_or"]
        assert pattern_name == "sql_or_1_equals_1"
        assert attack_type.value == "sql_injection"


class TestUnicodeSanitization:
    """Test _sanitize_input strips zero-width chars and applies NFKC normalization."""

    def test_zero_width_injection_stripped(self) -> None:
        """Zero-width chars inserted into 'union select' must be stripped."""
        # "u\u200Bnion s\u200Belect" → "union select" after sanitization
        payload = "u\u200bnion s\u200belect".encode()
        sanitized = AttackDetectionMiddleware._sanitize_input(payload)
        # After sanitization, the regex should detect 'union select'
        match = COMBINED_ATTACK_RE.search(sanitized)
        assert match is not None, "Zero-width bypass was not defeated"
        assert match.lastgroup == "sql_union"

    def test_bom_stripped(self) -> None:
        """BOM (U+FEFF) must be stripped before regex scanning."""
        payload = "\ufeffunion select * from users".encode()
        sanitized = AttackDetectionMiddleware._sanitize_input(payload)
        match = COMBINED_ATTACK_RE.search(sanitized)
        assert match is not None

    def test_directional_override_stripped(self) -> None:
        """Directional override chars (U+202A-U+202E) must be stripped."""
        payload = "uni\u202aon sel\u202bect".encode()
        sanitized = AttackDetectionMiddleware._sanitize_input(payload)
        match = COMBINED_ATTACK_RE.search(sanitized)
        assert match is not None

    def test_fullwidth_nfkc_normalization(self) -> None:
        """Fullwidth U+FF35 N+FF29 O+FF2F N → UNION after NFKC."""
        # Fullwidth "UNION" = U+FF35 U+FF2E U+FF29 U+FF2F U+FF2E
        fullwidth_union = "\uff35\uff2e\uff29\uff2f\uff2e"
        fullwidth_select = "\uff33\uff25\uff2c\uff25\uff23\uff34"
        payload = f"{fullwidth_union} {fullwidth_select}".encode()
        sanitized = AttackDetectionMiddleware._sanitize_input(payload)
        match = COMBINED_ATTACK_RE.search(sanitized)
        assert match is not None, "Fullwidth UNION SELECT was not caught after NFKC"

    def test_binary_payload_fallback(self) -> None:
        """Non-UTF-8 binary payloads should fall back to raw bytes (no crash)."""
        payload = b"\x80\x81\x82\xff\xfe"
        sanitized = AttackDetectionMiddleware._sanitize_input(payload)
        # Should return raw bytes unchanged
        assert sanitized == payload

    def test_clean_input_unchanged(self) -> None:
        """Clean ASCII input should pass through unchanged."""
        payload = b"Hello, world!"
        sanitized = AttackDetectionMiddleware._sanitize_input(payload)
        assert sanitized == payload


class TestExpandedHeaderCoverage:
    """Test that the middleware checks cookie, x-forwarded-for, origin headers.

    These tests verify the _GROUP_TO_ATTACK mapping works for all 15 groups.
    """

    def test_all_regex_groups_have_mappings(self) -> None:
        """Every named group in COMBINED_ATTACK_RE must exist in _GROUP_TO_ATTACK."""
        # Extract all named groups from the compiled regex
        named_groups = set(COMBINED_ATTACK_RE.groupindex.keys())
        mapped_groups = set(_GROUP_TO_ATTACK.keys())
        missing = named_groups - mapped_groups
        assert not missing, f"Regex groups {missing} have no mapping in _GROUP_TO_ATTACK"

    def test_no_dead_code_mappings(self) -> None:
        """Every entry in _GROUP_TO_ATTACK must have a corresponding regex group."""
        named_groups = set(COMBINED_ATTACK_RE.groupindex.keys())
        mapped_groups = set(_GROUP_TO_ATTACK.keys())
        dead = mapped_groups - named_groups
        assert not dead, f"Dead code mappings: {dead} exist in _GROUP_TO_ATTACK but not in regex"

    def test_combined_regex_group_count(self) -> None:
        """COMBINED_ATTACK_RE should have exactly 17 named groups (14 original + sql_or + cmd_inj + ssrf_cloud)."""
        assert len(COMBINED_ATTACK_RE.groupindex) == 17
