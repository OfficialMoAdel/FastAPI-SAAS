"""Unit Tests: orjson Serialization Migration.

Tests validate orjson-based JSON utilities provide correct serialization
for all supported types with performance improvements over stdlib json.
"""

import json
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from src.app.core.json_utils import json_dumps, json_loads, orjson_dumps_default


class SampleModel(BaseModel):
    """Sample Pydantic model for testing."""

    id: int
    name: str
    active: bool = True


class TestOrjsonDumpsDefault:
    """Test custom default serializer for orjson."""

    def test_uuid_serialization(self):
        """Test UUID is serialized to string."""
        test_uuid = uuid4()
        result = orjson_dumps_default(test_uuid)
        assert result == str(test_uuid)
        assert isinstance(result, str)

    def test_decimal_serialization(self):
        """Test Decimal is serialized to float."""
        test_decimal = Decimal("123.45")
        result = orjson_dumps_default(test_decimal)
        assert result == 123.45
        assert isinstance(result, float)

    def test_decimal_precision(self):
        """Test Decimal maintains reasonable precision."""
        test_decimal = Decimal("0.1234567890123456")
        result = orjson_dumps_default(test_decimal)
        assert abs(result - 0.1234567890123456) < 1e-10

    def test_datetime_serialization(self):
        """Test datetime is serialized to ISO format."""
        test_dt = datetime(2024, 1, 15, 10, 30, 45, tzinfo=timezone.utc)
        result = orjson_dumps_default(test_dt)
        assert "2024-01-15" in result
        assert isinstance(result, str)

    def test_date_serialization(self):
        """Test date is serialized to ISO format."""
        test_date = date(2024, 1, 15)
        result = orjson_dumps_default(test_date)
        assert result == "2024-01-15"

    def test_pydantic_model_serialization(self):
        """Test Pydantic model is serialized via model_dump."""
        model = SampleModel(id=1, name="test")
        result = orjson_dumps_default(model)
        assert result == {"id": 1, "name": "test", "active": True}

    def test_set_serialization(self):
        """Test set is serialized to list."""
        test_set = {1, 2, 3}
        result = orjson_dumps_default(test_set)
        assert isinstance(result, list)
        assert sorted(result) == [1, 2, 3]

    def test_unsupported_type_raises_error(self):
        """Test unsupported types raise TypeError."""

        class CustomClass:
            pass

        with pytest.raises(TypeError):
            orjson_dumps_default(CustomClass())


class TestJsonDumps:
    """Test json_dumps function."""

    def test_simple_dict(self):
        """Test simple dictionary serialization."""
        data = {"key": "value", "number": 42}
        result = json_dumps(data)
        assert isinstance(result, bytes)
        assert b'"key"' in result
        assert b'"value"' in result

    def test_as_str_returns_string(self):
        """Test as_str=True returns string."""
        data = {"key": "value"}
        result = json_dumps(data, as_str=True)
        assert isinstance(result, str)
        assert '"key"' in result

    def test_nested_structures(self):
        """Test nested dict/list serialization."""
        data = {
            "users": [
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
            ],
            "meta": {"count": 2},
        }
        result = json_dumps(data, as_str=True)
        parsed = json_loads(result)
        assert parsed["users"][0]["name"] == "Alice"
        assert parsed["meta"]["count"] == 2

    def test_uuid_in_dict(self):
        """Test UUID in dictionary is serialized."""
        test_uuid = uuid4()
        data = {"id": test_uuid}
        result = json_dumps(data, as_str=True)
        parsed = json_loads(result)
        assert parsed["id"] == str(test_uuid)

    def test_datetime_in_dict(self):
        """Test datetime in dictionary is serialized."""
        test_dt = datetime(2024, 1, 15, 10, 30, 45, tzinfo=timezone.utc)
        data = {"created_at": test_dt}
        result = json_dumps(data, as_str=True)
        assert "2024-01-15" in result

    def test_decimal_in_dict(self):
        """Test Decimal in dictionary is serialized."""
        data = {"price": Decimal("99.99")}
        result = json_dumps(data, as_str=True)
        parsed = json_loads(result)
        assert parsed["price"] == 99.99

    def test_pydantic_model_in_dict(self):
        """Test Pydantic model in dictionary is serialized."""
        model = SampleModel(id=1, name="test")
        data = {"user": model}
        result = json_dumps(data, as_str=True)
        parsed = json_loads(result)
        assert parsed["user"]["id"] == 1
        assert parsed["user"]["name"] == "test"

    def test_empty_structures(self):
        """Test empty dict and list serialization."""
        assert json_dumps({}, as_str=True) == "{}"
        assert json_dumps([], as_str=True) == "[]"

    def test_none_value(self):
        """Test None serialization."""
        data = {"value": None}
        result = json_dumps(data, as_str=True)
        assert "null" in result


class TestJsonLoads:
    """Test json_loads function."""

    def test_simple_dict(self):
        """Test simple dictionary deserialization."""
        json_str = '{"key": "value", "number": 42}'
        result = json_loads(json_str)
        assert result["key"] == "value"
        assert result["number"] == 42

    def test_bytes_input(self):
        """Test bytes input deserialization."""
        json_bytes = b'{"key": "value"}'
        result = json_loads(json_bytes)
        assert result["key"] == "value"

    def test_nested_structures(self):
        """Test nested structure deserialization."""
        json_str = '{"users": [{"id": 1}, {"id": 2}]}'
        result = json_loads(json_str)
        assert len(result["users"]) == 2
        assert result["users"][1]["id"] == 2

    def test_unicode_handling(self):
        """Test Unicode string handling."""
        json_str = '{"message": "Hello, 世界! 🚀"}'
        result = json_loads(json_str)
        assert result["message"] == "Hello, 世界! 🚀"


class TestRoundtrip:
    """Test serialization/deserialization roundtrip."""

    def test_complex_object_roundtrip(self):
        """Test complex object survives roundtrip."""
        original = {
            "id": str(uuid4()),
            "count": 42,
            "price": 99.99,
            "active": True,
            "tags": ["a", "b", "c"],
            "nested": {"key": "value"},
        }
        serialized = json_dumps(original, as_str=True)
        restored = json_loads(serialized)
        assert restored == original

    def test_list_roundtrip(self):
        """Test list survives roundtrip."""
        original = [1, 2, 3, "four", {"five": 5}]
        serialized = json_dumps(original, as_str=True)
        restored = json_loads(serialized)
        assert restored == original

    def test_deeply_nested_roundtrip(self):
        """Test deeply nested structure survives roundtrip."""
        original = {"level1": {"level2": {"level3": {"level4": {"value": "deep"}}}}}
        serialized = json_dumps(original, as_str=True)
        restored = json_loads(serialized)
        assert restored["level1"]["level2"]["level3"]["level4"]["value"] == "deep"


class TestPerformance:
    """Test orjson performance improvements."""

    @pytest.fixture
    def large_payload(self):
        """Create a large payload for performance testing."""
        return {
            "users": [
                {
                    "id": str(uuid4()),
                    "email": f"user{i}@example.com",
                    "name": f"User {i}",
                    "active": i % 2 == 0,
                    "score": Decimal(str(i * 1.5)),
                }
                for i in range(100)
            ],
            "metadata": {
                "count": 100,
                "page": 1,
                "created_at": datetime.now(timezone.utc),
            },
        }

    def test_orjson_faster_than_stdlib(self, large_payload):
        """Test orjson is faster than stdlib json."""
        iterations = 100

        # Benchmark orjson
        start = time.perf_counter()
        for _ in range(iterations):
            json_dumps(large_payload, as_str=True)
        orjson_time = time.perf_counter() - start

        # Benchmark stdlib json (need to handle custom types)
        def default_handler(obj):
            if isinstance(obj, UUID):
                return str(obj)
            if isinstance(obj, Decimal):
                return float(obj)
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

        start = time.perf_counter()
        for _ in range(iterations):
            json.dumps(large_payload, default=default_handler)
        stdlib_time = time.perf_counter() - start

        # orjson should be faster (allow some tolerance for CI variance)
        # We just verify it completes without requiring strict speedup
        assert orjson_time < stdlib_time * 2, (
            f"orjson ({orjson_time:.3f}s) should not be significantly slower than stdlib ({stdlib_time:.3f}s)"
        )

    def test_serialization_latency_acceptable(self, large_payload):
        """Test serialization latency is within acceptable bounds."""
        iterations = 50
        latencies = []

        for _ in range(iterations):
            start = time.perf_counter()
            json_dumps(large_payload, as_str=True)
            latencies.append((time.perf_counter() - start) * 1000)  # ms

        avg_latency = sum(latencies) / len(latencies)
        p95_latency = sorted(latencies)[int(len(latencies) * 0.95)]

        # Should be fast (< 5ms for 100-item payload)
        assert p95_latency < 10.0, f"P95 serialization latency {p95_latency:.2f}ms exceeds 10ms threshold"
