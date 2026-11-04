"""Route command-count harness: CLI parsing and the one-request measurement."""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import pytest

from src.benchmarks import protocol, route_command_count


class FakeRedis:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    async def info(self, section: str) -> str:
        assert section == "commandstats"
        self.counts["info"] += 1
        return "\n".join(
            ["# Commandstats"]
            + [f"cmdstat_{name}:calls={count},usec=1,usec_per_call=1.00" for name, count in sorted(self.counts.items())]
        )

    def issue(self, command: str) -> None:
        self.counts[command] += 1


def test_parse_expected_normalizes_names_and_counts() -> None:
    assert route_command_count.parse_expected(["EVALSHA=2", "get=1"]) == (("evalsha", 2), ("get", 1))
    assert route_command_count.parse_expected([]) == ()


@pytest.mark.parametrize("raw", ["evalsha", "=1", "get=x", "get="])
def test_parse_expected_rejects_malformed_values(raw: str) -> None:
    with pytest.raises(ValueError):
        route_command_count.parse_expected([raw])


@pytest.mark.asyncio
async def test_measure_route_executes_one_request_and_returns_the_verdict() -> None:
    redis = FakeRedis()
    calls: list[str] = []

    async def request() -> SimpleNamespace:
        calls.append("request")
        redis.issue("evalsha")
        redis.issue("get")
        return SimpleNamespace(status_code=200, text="")

    ceiling = protocol.CommandCountCeiling(
        max_commands=2,
        expected=(("evalsha", 1), ("get", 1)),
        label="GET /route",
    )
    result, verdict = await route_command_count.measure_route(redis, request, name="GET /route", ceiling=ceiling)

    assert calls == ["request"]
    assert result.commands == (("evalsha", 1), ("get", 1))
    assert verdict.passed
    assert verdict.measured_commands == 2


@pytest.mark.asyncio
async def test_measure_route_fails_a_route_over_its_ceiling() -> None:
    redis = FakeRedis()

    async def request() -> SimpleNamespace:
        redis.issue("evalsha")
        redis.issue("get")
        redis.issue("set")
        return SimpleNamespace(status_code=200, text="")

    ceiling = protocol.CommandCountCeiling(max_commands=2, label="GET /route")
    _, verdict = await route_command_count.measure_route(redis, request, name="GET /route", ceiling=ceiling)

    assert not verdict.passed
    assert verdict.measured_commands == 3
    assert "exceed" in verdict.message


@pytest.mark.asyncio
async def test_measure_route_rejects_a_non_200_response() -> None:
    redis = FakeRedis()
    redis.issue("evalsha")

    async def request() -> SimpleNamespace:
        return SimpleNamespace(status_code=401, text="unauthorized")

    ceiling = protocol.CommandCountCeiling(max_commands=1)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        await route_command_count.measure_route(redis, request, name="GET /route", ceiling=ceiling)
