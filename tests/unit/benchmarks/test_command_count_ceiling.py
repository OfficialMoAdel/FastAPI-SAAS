"""Command-count ceilings: exact deltas, extra/missing detection, and failure injection."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError

from src.benchmarks import census, command_count, protocol


class FakeRedis:
    """A fake Redis whose ``INFO commandstats`` reply reflects issued commands."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.fail_info: BaseException | None = None
        self.on_info: Callable[[int], None] | None = None
        self.after_info: Callable[[int], None] | None = None

    async def info(self, section: str) -> str:
        assert section == "commandstats"
        self.counts["info"] += 1
        call = self.counts["info"]
        if self.on_info is not None:
            self.on_info(call)
        if self.fail_info is not None:
            raise self.fail_info
        rendered = self.render()
        if self.after_info is not None:
            self.after_info(call)
        return rendered

    def issue(self, command: str) -> None:
        self.counts[command] += 1

    def render(self) -> str:
        lines = ["# Commandstats"]
        for name, count in sorted(self.counts.items()):
            lines.append(f"cmdstat_{name}:calls={count},usec=1,usec_per_call=1.00")
        return "\n".join(lines)


def _result(commands: dict[str, int], *, name: str = "route") -> protocol.CommandCountResult:
    return protocol.CommandCountResult.from_counts(name, commands)


def test_command_count_ceiling_rejects_impossible_bounds() -> None:
    for bad in (0, -1, True, 1.5, "2"):
        with pytest.raises(protocol.ProtocolViolation):
            protocol.CommandCountCeiling(max_commands=bad)  # type: ignore[arg-type]

    with pytest.raises(protocol.ProtocolViolation, match="declared more than once"):
        protocol.CommandCountCeiling(max_commands=2, expected=(("get", 1), ("get", 1)))

    with pytest.raises(protocol.ProtocolViolation, match="exceeds"):
        protocol.CommandCountCeiling(max_commands=1, expected=(("get", 2),))

    with pytest.raises(protocol.ProtocolViolation, match="non-negative"):
        protocol.CommandCountCeiling(max_commands=2, expected=(("get", -1),))


def test_command_count_verdict_refuses_a_duration_ceiling() -> None:
    with pytest.raises(protocol.RelativeComparisonRejected):
        protocol.command_count_verdict(_result({"get": 1}), protocol.Ceiling(value_us=1.0))  # type: ignore[arg-type]


def test_an_exact_expected_delta_passes() -> None:
    result = _result({"evalsha": 2, "hget": 1})
    ceiling = protocol.CommandCountCeiling(max_commands=3, expected=(("evalsha", 2), ("hget", 1)))
    outcome = protocol.command_count_verdict(result, ceiling)
    assert outcome.passed
    assert outcome.measured_commands == 3
    assert outcome.missing == ()
    assert outcome.unexpected == ()
    assert outcome.excess == ()


def test_an_extra_command_fails_even_when_the_total_is_under_the_ceiling() -> None:
    result = _result({"evalsha": 1, "hget": 1, "get": 1})
    ceiling = protocol.CommandCountCeiling(max_commands=10, expected=(("evalsha", 1), ("hget", 1)))
    outcome = protocol.command_count_verdict(result, ceiling)
    assert not outcome.passed
    assert outcome.unexpected == (("get", 1),)
    assert "unexpected commands" in outcome.message


def test_an_extra_invocation_of_an_expected_command_fails() -> None:
    result = _result({"evalsha": 2})
    ceiling = protocol.CommandCountCeiling(max_commands=10, expected=(("evalsha", 1),))
    outcome = protocol.command_count_verdict(result, ceiling)
    assert not outcome.passed
    assert outcome.excess == (("evalsha", 1),)
    assert "extra invocations" in outcome.message


def test_a_missing_command_fails_even_when_the_total_is_under_the_ceiling() -> None:
    result = _result({"evalsha": 1})
    ceiling = protocol.CommandCountCeiling(max_commands=10, expected=(("evalsha", 1), ("hget", 2)))
    outcome = protocol.command_count_verdict(result, ceiling)
    assert not outcome.passed
    assert outcome.missing == (("hget", 2),)
    assert "missing commands" in outcome.message


def test_a_total_ceiling_fails_without_an_expected_map() -> None:
    outcome = protocol.command_count_verdict(_result({"get": 3}), protocol.CommandCountCeiling(max_commands=2))
    assert not outcome.passed
    assert "exceed the redis-command-count ceiling 2" in outcome.message


def test_an_empty_delta_passes_and_filters_zero_counts() -> None:
    result = _result({"get": 0, "del": 0})
    assert result.commands == ()
    outcome = protocol.command_count_verdict(result, protocol.CommandCountCeiling(max_commands=1))
    assert outcome.passed
    assert outcome.measured_commands == 0


def test_commandstats_delta_includes_commands_first_seen_in_the_second_snapshot() -> None:
    delta = census.commandstats_delta({"info": 1, "get": 2}, {"info": 3, "get": 4, "evalsha": 1})
    assert delta == {"info": 2, "get": 2, "evalsha": 1}


@pytest.mark.asyncio
async def test_measurement_reports_the_exact_delta_without_the_instrument_info() -> None:
    redis = FakeRedis()

    async def operation() -> None:
        redis.issue("get")
        redis.issue("get")
        redis.issue("set")

    result = await command_count.measure_command_counts(redis, operation, name="route")
    assert result.commands == (("get", 2), ("set", 1))
    assert result.total == 3
    outcome = protocol.command_count_verdict(
        result,
        protocol.CommandCountCeiling(max_commands=3, expected=(("get", 2), ("set", 1))),
    )
    assert outcome.passed


@pytest.mark.asyncio
async def test_a_dirty_control_window_is_refused() -> None:
    redis = FakeRedis()
    redis.on_info = lambda call: redis.issue("get") if call == 2 else None

    async def operation() -> None:
        return None

    with pytest.raises(command_count.CommandCountWindowDirty, match="not quiet"):
        await command_count.measure_command_counts(redis, operation, name="route")


@pytest.mark.asyncio
async def test_require_quiet_false_ignores_pre_existing_traffic() -> None:
    redis = FakeRedis()
    redis.issue("get")

    async def operation() -> None:
        redis.issue("evalsha")

    result = await command_count.measure_command_counts(redis, operation, name="route", require_quiet=False)
    assert result.commands == (("evalsha", 1),)
    assert redis.counts["info"] == 2


@pytest.mark.asyncio
async def test_an_operation_error_propagates_without_producing_a_result() -> None:
    redis = FakeRedis()
    calls: list[str] = []

    async def operation() -> None:
        calls.append("called")
        raise RuntimeError("handler failed")

    with pytest.raises(RuntimeError, match="handler failed"):
        await command_count.measure_command_counts(redis, operation, name="route", require_quiet=False)
    assert calls == ["called"]
    assert redis.counts["info"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [RedisConnectionError("redis unavailable"), TimeoutError("redis timed out")],
)
async def test_a_redis_info_error_propagates(error: BaseException) -> None:
    redis = FakeRedis()
    redis.fail_info = error

    async def operation() -> None:
        return None

    with pytest.raises(type(error)):
        await command_count.measure_command_counts(redis, operation, name="route", require_quiet=False)


@pytest.mark.asyncio
async def test_an_after_snapshot_error_propagates() -> None:
    redis = FakeRedis()

    def fail_second_info(call: int) -> None:
        if call == 2:
            raise RedisConnectionError("snapshot failed")

    redis.on_info = fail_second_info

    async def operation() -> None:
        redis.issue("evalsha")

    with pytest.raises(RedisConnectionError, match="snapshot failed"):
        await command_count.measure_command_counts(redis, operation, name="route", require_quiet=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["NOSCRIPT No matching script", "BUSY Redis is busy"])
async def test_a_lua_script_error_propagates(message: str) -> None:
    redis = FakeRedis()

    async def operation() -> None:
        raise ResponseError(message)

    with pytest.raises(ResponseError, match=message.split()[0]):
        await command_count.measure_command_counts(redis, operation, name="route", require_quiet=False)


@pytest.mark.asyncio
async def test_a_dirty_post_operation_window_is_refused() -> None:
    redis = FakeRedis()
    redis.after_info = lambda call: redis.issue("del") if call == 5 else None

    async def operation() -> None:
        redis.issue("get")

    with pytest.raises(command_count.CommandCountWindowDirty, match="post-operation"):
        await command_count.measure_command_counts(redis, operation, name="route")
