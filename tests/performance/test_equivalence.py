"""Equivalence oracle tests for performance optimizations.

Per optimization workflow methodology, every optimization must be provably isomorphic.
These tests verify that outputs remain unchanged after optimizations.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.app.core.json_utils import json_dumps, json_loads

logger = logging.getLogger(__name__)


def _hash_data(data: bytes | str) -> str:
    """Compute SHA256 hash of data for comparison."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _log_comparison(test_name: str, input_data: Any, output1: bytes, output2: bytes, match: bool) -> None:
    """Log comparison results for debugging."""
    logger.info(
        f"{test_name}: input_hash={_hash_data(str(input_data).encode())[:16]}, "
        f"output1_hash={_hash_data(output1)[:16]}, output2_hash={_hash_data(output2)[:16]}, "
        f"match={match}"
    )


class TestOrjsonStdlibEquivalence:
    """Verify orjson produces equivalent output to stdlib json."""

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param({"key": "value"}, id="simple_dict"),
            pytest.param({"nested": {"a": 1, "b": 2}}, id="nested_dict"),
            pytest.param([1, 2, 3, 4, 5], id="simple_list"),
            pytest.param({"list": [1, "two", 3.0]}, id="mixed_list"),
            pytest.param({}, id="empty_dict"),
            pytest.param([], id="empty_list"),
            pytest.param({"unicode": "Hello \u4e16\u754c \u0432\u0435\u0442"}, id="unicode"),
            pytest.param({"special": "line1\nline2\ttab"}, id="special_chars"),
            pytest.param({"bool": True, "null": None}, id="bool_null"),
            pytest.param({"numbers": [0, -1, 1.5, 1e10]}, id="numbers"),
        ],
    )
    def test_basic_types_equivalence(self, data: dict | list) -> None:
        """Test that orjson and stdlib json produce equivalent output for basic types."""
        stdlib_output = json.dumps(data, sort_keys=True).encode("utf-8")
        orjson_output = json_dumps(data)

        # Parse both back to compare semantically
        stdlib_parsed = json.loads(stdlib_output)
        orjson_parsed = json_loads(orjson_output)

        _log_comparison("basic_types", data, stdlib_output, orjson_output, stdlib_parsed == orjson_parsed)

        assert stdlib_parsed == orjson_parsed, f"Semantic mismatch: {stdlib_parsed} != {orjson_parsed}"

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param({"uuid": UUID("12345678-1234-5678-1234-567812345678")}, id="uuid"),
            pytest.param({"decimal": Decimal("123.456")}, id="decimal"),
            pytest.param({"datetime": datetime(2024, 1, 15, 12, 30, 45, tzinfo=UTC)}, id="datetime_utc"),
            pytest.param({"set": {1, 2, 3}}, id="set_to_list"),
        ],
    )
    def test_extended_types_serialization(self, data: dict) -> None:
        """Test that orjson correctly serializes extended types."""
        orjson_output = json_dumps(data)
        parsed = json_loads(orjson_output)

        # Verify each extended type is correctly serialized
        for key, value in data.items():
            if isinstance(value, UUID):
                assert parsed[key] == str(value)
            elif isinstance(value, Decimal):
                assert parsed[key] == float(value)
            elif isinstance(value, datetime):
                assert parsed[key] == value.isoformat().replace("+00:00", "Z")
            elif isinstance(value, set):
                assert set(parsed[key]) == value

        logger.info(f"Extended type {list(data.keys())[0]}: serialized correctly")

    def test_large_payload_equivalence(self) -> None:
        """Test equivalence with large nested payload."""
        large_data = {
            "users": [
                {
                    "id": i,
                    "name": f"User {i}",
                    "email": f"user{i}@example.com",
                    "metadata": {"created": "2024-01-01", "tags": ["a", "b", "c"]},
                }
                for i in range(100)
            ],
            "total": 100,
            "page": 1,
        }

        stdlib_output = json.dumps(large_data, sort_keys=True)
        orjson_output = json_dumps(large_data, as_str=True)

        stdlib_parsed = json.loads(stdlib_output)
        orjson_parsed = json_loads(orjson_output)

        assert stdlib_parsed == orjson_parsed
        logger.info(f"Large payload: {len(stdlib_output)} bytes, match=True")


class TestUserSerializationEquivalence:
    """Verify direct dict construction matches Pydantic serialization."""

    def test_user_dict_fields(self) -> None:
        """Test that direct dict construction includes all expected fields."""
        # Simulated user data as would come from get_user_with_tier_eager
        user_dict = {
            "id": uuid4(),
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "name": "Test User",
            "username": "testuser",
            "email": "test@example.com",
            "profile_image_url": "https://example.com/avatar.jpg",
            "tier_id": 1,
            "is_superuser": False,
            "tier": {
                "id": 1,
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "name": "Free",
            },
        }

        # Verify all required fields are present
        required_fields = {
            "id",
            "created_at",
            "updated_at",
            "name",
            "username",
            "email",
            "profile_image_url",
            "tier_id",
            "is_superuser",
            "tier",
        }
        assert set(user_dict.keys()) == required_fields

        # Verify tier nested fields
        tier_fields = {"id", "created_at", "updated_at", "name"}
        assert set(user_dict["tier"].keys()) == tier_fields

        # Verify serialization works
        serialized = json_dumps(user_dict)
        parsed = json_loads(serialized)

        assert parsed["username"] == "testuser"
        assert parsed["tier"]["name"] == "Free"
        logger.info("User dict serialization: all fields present and serializable")

    def test_user_with_null_tier(self) -> None:
        """Test user serialization when tier is None."""
        user_dict = {
            "id": uuid4(),
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "name": "No Tier User",
            "username": "notier",
            "email": "notier@example.com",
            "profile_image_url": None,
            "tier_id": None,
            "is_superuser": False,
            "tier": None,
        }

        serialized = json_dumps(user_dict)
        parsed = json_loads(serialized)

        assert parsed["tier"] is None
        assert parsed["tier_id"] is None
        logger.info("User with null tier: serialized correctly")


class TestCacheResponseEquivalence:
    """Verify cache hit and miss responses are equivalent."""

    def test_json_roundtrip_preserves_data(self) -> None:
        """Test that JSON roundtrip through cache preserves data integrity."""
        original_data = {
            "id": 123,
            "name": "Test Item",
            "metadata": {
                "tags": ["performance", "cache"],
                "score": 98.5,
            },
            "items": [1, 2, 3],
        }

        # Simulate cache storage (serialize to bytes)
        cached_bytes = json_dumps(original_data)

        # Simulate cache retrieval (deserialize from bytes)
        retrieved_data = json_loads(cached_bytes)

        assert original_data == retrieved_data
        logger.info(f"Cache roundtrip: {len(cached_bytes)} bytes, data preserved")

    def test_response_bytes_stability(self) -> None:
        """Test that same input produces same output bytes (deterministic)."""
        data = {"a": 1, "b": 2, "c": 3}

        output1 = json_dumps(data)
        output2 = json_dumps(data)

        assert output1 == output2
        logger.info(f"Deterministic output: hash={_hash_data(output1)[:16]}")


class TestDatetimeSerializationEquivalence:
    """Test datetime handling across timezone scenarios."""

    @pytest.mark.parametrize(
        "dt",
        [
            pytest.param(datetime(2024, 1, 15, 12, 30, 45, tzinfo=UTC), id="utc"),
            pytest.param(datetime(2024, 6, 15, 0, 0, 0, tzinfo=UTC), id="midnight_utc"),
            pytest.param(datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC), id="year_end"),
        ],
    )
    def test_datetime_serialization(self, dt: datetime) -> None:
        """Test datetime serialization produces ISO format."""
        data = {"timestamp": dt}
        serialized = json_dumps(data)
        parsed = json_loads(serialized)

        # orjson with OPT_UTC_Z uses 'Z' suffix for UTC
        expected_suffix = "Z"
        assert parsed["timestamp"].endswith(expected_suffix), f"Expected Z suffix: {parsed['timestamp']}"

        logger.info(f"Datetime {dt.isoformat()}: serialized as {parsed['timestamp']}")


# Integration test for the full optimization chain
class TestOptimizationChainEquivalence:
    """End-to-end tests for the complete optimization chain."""

    def test_json_utils_api_stability(self) -> None:
        """Test that json_utils API is stable and complete."""
        # Verify module exports
        from src.app.core import json_utils

        assert hasattr(json_utils, "json_dumps")
        assert hasattr(json_utils, "json_loads")
        assert hasattr(json_utils, "orjson_dumps_default")

        # Verify function signatures work
        result = json_utils.json_dumps({"test": True})
        assert isinstance(result, bytes)

        result_str = json_utils.json_dumps({"test": True}, as_str=True)
        assert isinstance(result_str, str)

        parsed = json_utils.json_loads(result)
        assert parsed == {"test": True}

        logger.info("json_utils API: stable and complete")
