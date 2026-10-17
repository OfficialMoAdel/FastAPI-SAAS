import time
from typing import Dict, List  # noqa: UP035
from unittest.mock import patch

import pytest

from src.app.core.job_monitoring import JobMonitoringService


# Mock Redis implementation for Hash and List operations
class MockRedis:
    def __init__(self):
        self.data = {}  # Key -> Value (for strings)
        self.hashes = {}  # Key -> Dict
        self.lists = {}  # Key -> List
        self.pipeline_transactions = []

    def _get_key(self, key: str | bytes) -> str:
        if isinstance(key, bytes):
            return key.decode("utf-8")
        return key

    async def hgetall(self, key: str | bytes) -> Dict[bytes, bytes]:
        key_str = self._get_key(key)
        if key_str not in self.hashes:
            return {}
        # Return bytes keys/values as redis-py does
        return {
            k.encode("utf-8") if isinstance(k, str) else k: str(v).encode("utf-8") if not isinstance(v, bytes) else v
            for k, v in self.hashes[key_str].items()
        }

    async def get(self, key: str | bytes) -> bytes | None:
        """K5: get_job_status reads the arq job definition STRING blob."""
        key_str = self._get_key(key)
        value = self.data.get(key_str)
        if value is None:
            return None
        return value.encode("utf-8") if isinstance(value, str) else value

    async def scan(self, cursor: int, match: str = None, count: int = None):
        # Simple scan implementation
        keys = []
        import fnmatch

        # Check hashes for matching keys
        all_keys = list(self.hashes.keys())
        if match:
            keys = [k.encode("utf-8") for k in fnmatch.filter(all_keys, match)]
        else:
            keys = [k.encode("utf-8") for k in all_keys]

        return 0, keys

    def pipeline(self, transaction: bool = True):
        # C-4 opens the read pipeline with transaction=False. Accept the keyword
        # and record it so a pipeline opened without it is visible rather than
        # silently equivalent to one opened with it.
        self.pipeline_transactions.append(transaction)
        return MockPipeline(self)

    async def lrange(self, key: str | bytes, start: int, end: int) -> List[bytes]:
        key_str = self._get_key(key)
        if key_str not in self.lists:
            return []
        # Redis lrange is inclusive for end
        # Python slice is exclusive for end
        # And redis end can be -1
        lst = self.lists[key_str]

        if end == -1:
            slice_end = len(lst)
        else:
            slice_end = end + 1

        result = lst[start:slice_end]
        return [str(x).encode("utf-8") if not isinstance(x, bytes) else x for x in result]

    async def llen(self, key: str | bytes) -> int:
        key_str = self._get_key(key)
        return len(self.lists.get(key_str, []))

    async def delete(self, key: str | bytes) -> int:
        key_str = self._get_key(key)
        count = 0
        if key_str in self.data:
            del self.data[key_str]
            count = 1
        if key_str in self.hashes:
            del self.hashes[key_str]
            count = 1
        if key_str in self.lists:
            del self.lists[key_str]
            count = 1
        return count


class MockPipeline:
    def __init__(self, redis: MockRedis):
        self.redis = redis
        self.commands = []

    async def __aenter__(self, *args):
        return self

    async def __aexit__(self, *args):
        return False

    def hincrby(self, key: str, field: str, amount: int):
        self.commands.append(("hincrby", key, field, amount))
        return self

    def hincrbyfloat(self, key: str, field: str, amount: float):
        self.commands.append(("hincrbyfloat", key, field, amount))
        return self

    def lpush(self, key: str, value: str):
        self.commands.append(("lpush", key, value))
        return self

    def ltrim(self, key: str, start: int, end: int):
        self.commands.append(("ltrim", key, start, end))
        return self

    def hgetall(self, key: str):
        self.commands.append(("hgetall", key))
        return self

    async def execute(self):
        results = []
        for cmd in self.commands:
            op = cmd[0]
            if op == "hincrby":
                key, field, amount = cmd[1], cmd[2], cmd[3]
                if key not in self.redis.hashes:
                    self.redis.hashes[key] = {}
                current = int(self.redis.hashes[key].get(field, 0))
                self.redis.hashes[key][field] = str(current + amount)
                results.append(current + amount)

            elif op == "hincrbyfloat":
                key, field, amount = cmd[1], cmd[2], cmd[3]
                if key not in self.redis.hashes:
                    self.redis.hashes[key] = {}
                current = float(self.redis.hashes[key].get(field, 0.0))
                self.redis.hashes[key][field] = str(current + amount)
                results.append(current + amount)

            elif op == "lpush":
                key, value = cmd[1], cmd[2]
                if key not in self.redis.lists:
                    self.redis.lists[key] = []
                self.redis.lists[key].insert(0, value)
                results.append(len(self.redis.lists[key]))

            elif op == "hgetall":
                results.append(await self.redis.hgetall(cmd[1]))

            elif op == "ltrim":
                key, start, end = cmd[1], cmd[2], cmd[3]
                if key in self.redis.lists:
                    # Redis ltrim retains elements in range [start, end]
                    if end == -1:
                        self.redis.lists[key] = self.redis.lists[key][start:]
                    else:
                        self.redis.lists[key] = self.redis.lists[key][start : end + 1]
                results.append("OK")

        self.commands = []
        return results


@pytest.fixture
def mock_redis_client():
    return MockRedis()


@pytest.fixture
def service(mock_redis_client):
    # Patch the imported client in job_monitoring module
    with patch("src.app.core.utils.cache.client", mock_redis_client):
        yield JobMonitoringService()


@pytest.mark.asyncio
async def test_get_job_status_found(service, mock_redis_client):
    # K5: arq stores the job definition as a JSON STRING blob; the service
    # reports it as "queued" and exposes enqueue_time (unix ms) as enqueued_at.
    import json

    job_id = "test-job-1"
    key = f"arq:job:{job_id}"
    enqueue_ms = 1756000000000
    mock_redis_client.data[key] = json.dumps(
        {"job_id": job_id, "function": "my_func", "args": ["x"], "enqueue_time": enqueue_ms}
    )

    status = await service.get_job_status(job_id)

    assert status["status"] == "queued"
    assert status["job_id"] == job_id
    assert status["function"] == "my_func"
    assert "enqueued_at" in status
    assert status["enqueued_at"].startswith("2025")


@pytest.mark.asyncio
async def test_get_job_status_running(service, mock_redis_client):
    import json

    job_id = "test-job-running"
    key = f"arq:job:{job_id}"
    mock_redis_client.data[key] = json.dumps(
        {"job_id": job_id, "function": "my_func", "enqueue_time": int(time.time() * 1000)}
    )

    status = await service.get_job_status(job_id)

    assert status["status"] == "queued"
    assert "enqueued_at" in status


@pytest.mark.asyncio
async def test_get_job_status_not_found(service):
    status = await service.get_job_status("non-existent")
    assert status["status"] == "not_found"


@pytest.mark.asyncio
async def test_get_job_status_malformed_blob(service, mock_redis_client):
    """K5: an unparseable blob degrades to status=unknown with raw excerpt."""
    mock_redis_client.data["arq:job:bad"] = b"\x80\x04not-json"

    status = await service.get_job_status("bad")

    assert status["status"] == "unknown"
    assert "raw" in status


@pytest.mark.asyncio
async def test_get_job_status_no_redis():
    with patch("src.app.core.utils.cache.client", None):
        srv = JobMonitoringService()
        status = await srv.get_job_status("any")
        assert status["error"] == "Redis unavailable"


@pytest.mark.asyncio
async def test_record_job_start(service, mock_redis_client):
    await service.record_job_start("job1", "my_func")

    stats_key = "arq:stats:my_func"
    assert stats_key in mock_redis_client.hashes
    assert mock_redis_client.hashes[stats_key]["total"] == "1"


@pytest.mark.asyncio
async def test_record_job_success(service, mock_redis_client):
    await service.record_job_success("job1", "my_func", 2.5)

    stats_key = "arq:stats:my_func"
    assert stats_key in mock_redis_client.hashes
    assert mock_redis_client.hashes[stats_key]["success"] == "1"
    assert mock_redis_client.hashes[stats_key]["total_duration"] == "2.5"

    # Record another one
    await service.record_job_success("job2", "my_func", 1.5)
    assert mock_redis_client.hashes[stats_key]["success"] == "2"
    assert mock_redis_client.hashes[stats_key]["total_duration"] == "4.0"


@pytest.mark.asyncio
async def test_record_job_failure(service, mock_redis_client):
    await service.record_job_failure("job1", "my_func", "ValueError")

    stats_key = "arq:stats:my_func"
    assert stats_key in mock_redis_client.hashes
    assert mock_redis_client.hashes[stats_key]["failed"] == "1"

    # Check DLQ
    dlq_key = "arq:dlq"
    assert dlq_key in mock_redis_client.lists
    assert len(mock_redis_client.lists[dlq_key]) == 1
    item = mock_redis_client.lists[dlq_key][0]
    import json
    data = json.loads(item)
    assert data["job_id"] == "job1"
    assert data["function"] == "my_func"
    assert data["error"] == "ValueError"


@pytest.mark.asyncio
async def test_get_job_metrics(service, mock_redis_client):
    # Setup stats
    mock_redis_client.hashes["arq:stats:func1"] = {
        "total": "10",
        "success": "8",
        "failed": "2",
        "total_duration": "16.0",
    }
    mock_redis_client.hashes["arq:stats:func2"] = {"total": "5", "success": "0", "failed": "5", "total_duration": "0.0"}

    metrics = await service.get_job_metrics()

    assert "func1" in metrics
    assert metrics["func1"]["total"] == 10
    assert metrics["func1"]["avg_duration"] == 2.0  # 16.0 / 8

    assert "func2" in metrics
    assert metrics["func2"]["avg_duration"] == 0.0  # Division by zero handled


@pytest.mark.asyncio
async def test_get_dead_letter_queue(service, mock_redis_client):
    import json

    dlq_key = "arq:dlq"
    # Add items directly
    ts = time.time()
    json_item = json.dumps({"job_id": "job_new", "function": "func3", "error": "json_error", "timestamp": ts})
    mock_redis_client.lists[dlq_key] = [f"job1|func1|err1|{ts}", f"job2|func2|err2|{ts}", json_item, "malformed_item"]

    items = await service.get_dead_letter_queue(limit=10)
    assert len(items) == 4

    assert items[0]["job_id"] == "job1"
    assert items[1]["function"] == "func2"
    assert items[2]["error"] == "json_error"
    assert items[2]["job_id"] == "job_new"
    assert items[3]["error"] == "parse_error"


@pytest.mark.asyncio
async def test_get_dlq_size(service, mock_redis_client):
    mock_redis_client.lists["arq:dlq"] = ["item1", "item2"]
    size = await service.get_dlq_size()
    assert size == 2


@pytest.mark.asyncio
async def test_clear_dlq(service, mock_redis_client):
    mock_redis_client.lists["arq:dlq"] = ["item1"]
    result = await service.clear_dlq()
    assert result is True
    assert "arq:dlq" not in mock_redis_client.lists


@pytest.mark.asyncio
async def test_get_summary(service, mock_redis_client):
    # Setup stats
    mock_redis_client.hashes["arq:stats:func1"] = {"total": "10", "failed": "2"}
    mock_redis_client.lists["arq:dlq"] = ["item1"]

    summary = await service.get_summary()

    assert summary["total_jobs_processed"] == 10
    assert summary["total_failures"] == 2
    assert summary["dlq_size"] == 1
    assert summary["status"] == "healthy"
