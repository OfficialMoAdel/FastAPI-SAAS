"""Unit tests for the shared middleware exempt-path helper (P2 micro).

Covers equivalence of the C-speed startswith-tuple implementation with the
former f-string + Python loop: exact hits, prefix sub-paths, misses, custom
base/additional sets, and memoization of the prefix tuples.
"""

from src.app.core.middleware_exempt import (
    COMMON_EXEMPT_PATHS,
    IP_FILTER_EXEMPT_PATHS,
    _prefix_tuple,
    is_path_exempt,
)


class TestIsPathExempt:
    def test_exact_common_hit(self):
        assert is_path_exempt("/health") is True
        assert is_path_exempt("/api/v1/health") is True
        assert is_path_exempt("/openapi.json") is True

    def test_prefix_subpath_hit(self):
        assert is_path_exempt("/health/live/extra") is True
        assert is_path_exempt("/api/v1/billing/webhook/more") is True

    def test_miss(self):
        assert is_path_exempt("/api/v1/users") is False
        assert is_path_exempt("/metrics") is False  # H-1: bare /metrics not exempt
        assert is_path_exempt("/healthz") is False  # prefix without slash boundary

    def test_narrower_base_excludes_docs_trio(self):
        assert is_path_exempt("/docs", base=IP_FILTER_EXEMPT_PATHS) is False
        assert is_path_exempt("/openapi.json", base=IP_FILTER_EXEMPT_PATHS) is False
        assert is_path_exempt("/health", base=IP_FILTER_EXEMPT_PATHS) is True

    def test_additional_extras(self):
        extras = frozenset({"/custom"})
        assert is_path_exempt("/custom", additional=extras) is True
        assert is_path_exempt("/custom/deep", additional=extras) is True
        assert is_path_exempt("/customother", additional=extras) is False
        assert is_path_exempt("/custom", additional=frozenset()) is False

    def test_returns_bool(self):
        assert type(is_path_exempt("/api/v1/users")) is bool
        assert type(is_path_exempt("/health", additional=frozenset({"/x"}))) is bool


class TestPrefixTupleMemo:
    def test_same_set_returns_identical_object(self):
        assert _prefix_tuple(COMMON_EXEMPT_PATHS) is _prefix_tuple(COMMON_EXEMPT_PATHS)

    def test_entries_are_slashed_prefixes(self):
        prefixes = _prefix_tuple(frozenset({"/health"}))
        assert prefixes == ("/health/",)
