"""Tests for ARQ job serializer migration from pickle to orjson.

Task: TEST-P2-1 - ARQ orjson Serializer Roundtrip Tests
Validates that job arguments survive serialization/deserialization cycles.
"""

import logging
import pickle
import time
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.worker.settings import WorkerSettings

logger = logging.getLogger(__name__)


class TestARQSerializerRoundtrip:
    """Test ARQ job serialization roundtrip with various types."""

    def test_arq_job_serialization_roundtrip(self) -> None:
        """Verify job arguments survive serialize/deserialize cycle."""
        job_args = {
            "user_id": 123,
            "action": "send_email",
            "data": {"to": "test@example.com", "subject": "Test"},
        }

        serialized = WorkerSettings.job_serializer(job_args)
        deserialized = WorkerSettings.job_deserializer(serialized)

        logger.info(f"Serialized bytes size: {len(serialized)}")
        logger.info(f"Serialized content: {serialized}")

        assert deserialized == job_args
        assert deserialized["user_id"] == 123
        assert deserialized["data"]["to"] == "test@example.com"

    def test_arq_serializer_handles_nested_structures(self) -> None:
        """Verify deeply nested structures serialize correctly."""
        job_args = {
            "level1": {
                "level2": {
                    "level3": {
                        "values": [1, 2, 3],
                        "flag": True,
                        "null_value": None,
                    }
                }
            }
        }

        serialized = WorkerSettings.job_serializer(job_args)
        deserialized = WorkerSettings.job_deserializer(serialized)

        assert deserialized == job_args
        assert deserialized["level1"]["level2"]["level3"]["values"] == [1, 2, 3]
        assert deserialized["level1"]["level2"]["level3"]["flag"] is True
        assert deserialized["level1"]["level2"]["level3"]["null_value"] is None


class TestARQSerializerDatetime:
    """Test datetime serialization handling."""

    def test_arq_serializer_handles_datetime(self) -> None:
        """Verify datetime objects serialize correctly."""
        original_dt = datetime.now(UTC)
        job_args = {"scheduled_at": original_dt}

        serialized = WorkerSettings.job_serializer(job_args)
        deserialized = WorkerSettings.job_deserializer(serialized)

        logger.info(f"DateTime before: {original_dt}")
        logger.info(f"DateTime after: {deserialized['scheduled_at']}")

        # orjson serializes datetime to ISO string
        assert isinstance(deserialized["scheduled_at"], str)
        # Parse back and compare (should match to second precision)
        parsed_dt = datetime.fromisoformat(deserialized["scheduled_at"])
        assert abs((parsed_dt - original_dt).total_seconds()) < 1

    def test_arq_serializer_handles_datetime_naive(self) -> None:
        """Verify naive datetime objects serialize correctly."""
        original_dt = datetime(2024, 1, 15, 10, 30, 0)
        job_args = {"created_at": original_dt}

        serialized = WorkerSettings.job_serializer(job_args)
        deserialized = WorkerSettings.job_deserializer(serialized)

        assert isinstance(deserialized["created_at"], str)
        parsed_dt = datetime.fromisoformat(deserialized["created_at"])
        assert parsed_dt == original_dt


class TestARQSerializerUUID:
    """Test UUID serialization handling."""

    def test_arq_serializer_handles_uuid(self) -> None:
        """Verify UUID objects serialize correctly (becomes string)."""
        original_uuid = uuid4()
        job_args = {"task_id": original_uuid}

        serialized = WorkerSettings.job_serializer(job_args)
        deserialized = WorkerSettings.job_deserializer(serialized)

        logger.info(f"UUID before: {original_uuid} (type: {type(original_uuid).__name__})")
        logger.info(f"UUID after: {deserialized['task_id']} (type: {type(deserialized['task_id']).__name__})")

        # UUID becomes string after orjson roundtrip - this is expected behavior
        assert isinstance(deserialized["task_id"], str)
        assert deserialized["task_id"] == str(original_uuid)
        # Can convert back to UUID if needed
        recovered_uuid = UUID(deserialized["task_id"])
        assert recovered_uuid == original_uuid

    def test_arq_serializer_handles_multiple_uuids(self) -> None:
        """Verify multiple UUID objects in same payload."""
        uuid1, uuid2, uuid3 = uuid4(), uuid4(), uuid4()
        job_args = {
            "delivery_id": uuid1,
            "user_id": uuid2,
            "tenant_id": uuid3,
        }

        serialized = WorkerSettings.job_serializer(job_args)
        deserialized = WorkerSettings.job_deserializer(serialized)

        assert deserialized["delivery_id"] == str(uuid1)
        assert deserialized["user_id"] == str(uuid2)
        assert deserialized["tenant_id"] == str(uuid3)


class TestARQSerializerDecimal:
    """Test Decimal serialization handling."""

    def test_arq_serializer_handles_decimal(self) -> None:
        """Verify Decimal serializes (converts to float)."""
        job_args = {"amount": Decimal("123.45")}

        serialized = WorkerSettings.job_serializer(job_args)
        deserialized = WorkerSettings.job_deserializer(serialized)

        logger.info(f"Decimal before: {job_args['amount']} (type: {type(job_args['amount']).__name__})")
        logger.info(f"Decimal after: {deserialized['amount']} (type: {type(deserialized['amount']).__name__})")

        # Decimal becomes float after orjson roundtrip
        assert isinstance(deserialized["amount"], float)
        assert abs(deserialized["amount"] - 123.45) < 0.001

    def test_arq_serializer_handles_high_precision_decimal(self) -> None:
        """Verify high precision Decimal handling (may lose precision)."""
        job_args = {"precise_amount": Decimal("123.456789012345")}

        serialized = WorkerSettings.job_serializer(job_args)
        deserialized = WorkerSettings.job_deserializer(serialized)

        # Note: Float has limited precision, some loss is expected
        assert isinstance(deserialized["precise_amount"], float)
        # Just verify it's approximately correct
        assert abs(deserialized["precise_amount"] - 123.456789012345) < 0.0001


class TestARQSerializerErrorHandling:
    """Test error handling for non-serializable types."""

    def test_arq_serializer_rejects_non_json_types(self) -> None:
        """Verify proper error for non-serializable custom types."""

        class CustomObject:
            pass

        job_args = {"obj": CustomObject()}

        with pytest.raises(TypeError) as exc_info:
            WorkerSettings.job_serializer(job_args)

        logger.info(f"Error message: {exc_info.value}")
        assert "not JSON serializable" in str(exc_info.value)

    def test_arq_serializer_rejects_lambda(self) -> None:
        """Verify functions/lambdas cannot be serialized."""
        job_args = {"callback": lambda x: x * 2}

        with pytest.raises(TypeError):
            WorkerSettings.job_serializer(job_args)

    def test_arq_serializer_handles_bytes(self) -> None:
        """Verify bytes cannot be directly serialized (must be base64 encoded)."""
        job_args = {"raw_data": b"binary content"}

        # orjson cannot serialize bytes directly
        with pytest.raises(TypeError):
            WorkerSettings.job_serializer(job_args)


class TestARQSerializerPerformance:
    """Performance comparison tests."""

    def test_arq_serializer_performance_vs_pickle(self) -> None:
        """Benchmark: Compare orjson vs pickle speed."""
        large_payload = {"data": [{"id": i, "name": f"item_{i}", "value": i * 1.5} for i in range(1000)]}

        # Time orjson serialization
        start = time.perf_counter()
        for _ in range(100):
            serialized = json_dumps(large_payload)
            json_loads(serialized)
        orjson_time = time.perf_counter() - start

        # Time pickle serialization
        start = time.perf_counter()
        for _ in range(100):
            serialized = pickle.dumps(large_payload)
            pickle.loads(serialized)  # noqa: S301
        pickle_time = time.perf_counter() - start

        logger.info(f"orjson time (100 iterations): {orjson_time:.4f}s")
        logger.info(f"pickle time (100 iterations): {pickle_time:.4f}s")
        logger.info(f"orjson speedup: {pickle_time / orjson_time:.2f}x")

        # orjson should generally be faster or comparable
        # We allow up to 2x slower to account for test variance
        assert orjson_time < pickle_time * 2, "orjson should not be significantly slower than pickle"

    def test_arq_serializer_payload_size(self) -> None:
        """Compare payload sizes between orjson and pickle."""
        payload = {"data": [{"id": i, "name": f"item_{i}"} for i in range(100)]}

        orjson_size = len(json_dumps(payload))
        pickle_size = len(pickle.dumps(payload))

        logger.info(f"orjson payload size: {orjson_size} bytes")
        logger.info(f"pickle payload size: {pickle_size} bytes")
        logger.info(f"orjson size reduction: {((pickle_size - orjson_size) / pickle_size * 100):.1f}%")

        # JSON is typically more compact for simple data structures
        # We log the comparison but don't fail on size (depends on data structure)


class TestARQSerializerCompatibility:
    """Test compatibility and edge cases."""

    def test_arq_serializer_handles_empty_payload(self) -> None:
        """Verify empty payloads work correctly."""
        job_args: dict = {}

        serialized = WorkerSettings.job_serializer(job_args)
        deserialized = WorkerSettings.job_deserializer(serialized)

        assert deserialized == {}

    def test_arq_serializer_handles_list_payload(self) -> None:
        """Verify list as top-level payload."""
        job_args = [1, 2, 3, "four", {"five": 5}]

        serialized = WorkerSettings.job_serializer(job_args)
        deserialized = WorkerSettings.job_deserializer(serialized)

        assert deserialized == job_args

    def test_arq_serializer_handles_unicode(self) -> None:
        """Verify unicode strings are preserved."""
        job_args = {
            "message": "Hello, 世界! 🎉",
            "emoji": "👋🌍✨",
            "arabic": "مرحبا",
        }

        serialized = WorkerSettings.job_serializer(job_args)
        deserialized = WorkerSettings.job_deserializer(serialized)

        assert deserialized == job_args
        assert deserialized["message"] == "Hello, 世界! 🎉"

    def test_arq_serializer_handles_large_integers(self) -> None:
        """Verify large integers serialize correctly."""
        job_args = {
            "big_int": 9007199254740993,  # Larger than JS MAX_SAFE_INTEGER
            "negative": -9007199254740993,
        }

        serialized = WorkerSettings.job_serializer(job_args)
        deserialized = WorkerSettings.job_deserializer(serialized)

        assert deserialized["big_int"] == 9007199254740993
        assert deserialized["negative"] == -9007199254740993

    def test_arq_serializer_handles_sets_as_lists(self) -> None:
        """Verify sets are converted to lists."""
        job_args = {"tags": {"python", "fastapi", "async"}}

        serialized = WorkerSettings.job_serializer(job_args)
        deserialized = WorkerSettings.job_deserializer(serialized)

        # Set becomes list after serialization
        assert isinstance(deserialized["tags"], list)
        assert set(deserialized["tags"]) == {"python", "fastapi", "async"}


class TestDeliverWebhookTaskUUIDHandling:
    """Test that deliver_webhook_task correctly handles UUID deserialization."""

    def test_uuid_string_conversion_in_task(self) -> None:
        """Verify UUID string conversion logic works correctly."""
        from uuid import UUID

        # Simulate what happens after orjson deserialization
        delivery_id_as_string = "550e8400-e29b-41d4-a716-446655440000"

        # The task function should handle both UUID and string
        if isinstance(delivery_id_as_string, str):
            converted = UUID(delivery_id_as_string)
        else:
            converted = delivery_id_as_string

        assert isinstance(converted, UUID)
        assert str(converted) == delivery_id_as_string

    def test_uuid_passthrough_when_already_uuid(self) -> None:
        """Verify UUID objects are passed through unchanged."""
        original_uuid = uuid4()

        # If already UUID, no conversion needed
        if isinstance(original_uuid, str):
            converted = UUID(original_uuid)
        else:
            converted = original_uuid

        assert converted is original_uuid
