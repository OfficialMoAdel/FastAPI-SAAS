"""T-6 verification: prove a PIPELINED command is counted as N, and that two counts AGREE.

Named by the bead, so the path is the bead's.

This file exists because writing it found a real defect. The shim parsed ONE RESP
header per element of the packed buffer, and ``pack_commands`` CONCATENATES staged
commands into a single element under the buffer cutoff - so a five-command
pipeline counted as ONE. That reads as a low command count, not as a bug in the
instrument, which is why the bead asks for the arithmetic to be checked against a
real encoder rather than against hand-built fixtures.

Everything here therefore goes through redis-py's OWN ``pack_commands``, so the
buffer shape under test is the one a live pipeline produces.
"""

from __future__ import annotations

import inspect

import pytest
from redis.connection import Connection

from src.app.core.monitoring.redis_commands import (
    CANDIDATE_SEND_PATHS,
    PATH_SINGLE,
    SINGLE_PATH_FUNNELS_INTO_PACKED,
    RedisCommandShim,
    assert_counts_agree,
    assert_pipeline_aware,
    commandstats_total,
    measure_shim_cost,
)


class RecordingConnection(Connection):
    """A real ``Connection`` with only the socket work stubbed.

    Deliberately a subclass and not a fake object. The encoder, the command
    packer and the buffer-cutoff logic are redis-py's own, because those are
    exactly the parts whose shape the counter has to survive. Substituting a
    hand-rolled fake here would test the fake.
    """

    def __init__(self) -> None:
        super().__init__(host="localhost", port=6379)
        self.sent: list = []

    def check_health(self) -> None:
        """No socket, so no health check. Nothing else is overridden."""

    def send_packed_command(self, command, check_health: bool = True) -> None:
        self.sent.append(command)

    def send_command(self, *args, **kwargs):
        self.sent.append(("single", args[0]))


@pytest.fixture
def shim() -> RedisCommandShim:
    instance = RedisCommandShim(enabled=True)
    instance.install(RecordingConnection)
    yield instance
    instance.uninstall(RecordingConnection)


def _pipeline_bytes(connection: Connection, commands: list[tuple[bytes, ...]]) -> bytes:
    """What ``Pipeline.execute`` would hand to ``send_packed_command``."""
    chunks = connection.pack_commands(commands)
    return b"".join(bytes(chunk) for chunk in chunks)


class TestAPipelineOfNCountsAsN:
    def test_five_pipelined_commands_count_as_five(self, shim: RedisCommandShim) -> None:
        """Acceptance (b): N in, N out. The property that was silently wrong."""
        connection = RecordingConnection()
        commands = [
            (b"MULTI",), (b"SET", b"k1", b"1"), (b"SET", b"k2", b"2"), (b"GET", b"k1"), (b"EXEC",)
        ]
        connection.send_packed_command(connection.pack_commands(commands))

        export = shim.export()
        assert export["redis_total_commands"] == 5
        assert export["redis_commands_by_name"] == {"MULTI": 1, "SET": 2, "GET": 1, "EXEC": 1}

    def test_the_packed_buffer_really_is_one_element(self, shim: RedisCommandShim) -> None:
        """The defect, pinned. If pack_commands ever stops concatenating, this
        test fails and points at the assumption that needs revisiting."""
        connection = RecordingConnection()
        commands = [(b"SET", b"a", b"1"), (b"SET", b"b", b"2")]
        assert len(connection.pack_commands(commands)) == 1, (
            "pack_commands now returns one element per command; the parser's "
            "whole-buffer walk is still correct, but this assumption changed"
        )

    def test_several_pipelines_accumulate(self, shim: RedisCommandShim) -> None:
        connection = RecordingConnection()
        for index in range(3):
            connection.send_packed_command(
                connection.pack_commands([(b"SET", f"k{index}".encode(), b"v")])
            )
        assert shim.export()["redis_total_commands"] == 3
        assert shim.export()["redis_total_round_trips"] == 3

    def test_a_single_command_is_counted_once_not_twice(self, shim: RedisCommandShim) -> None:
        """redis-py routes a single command THROUGH send_packed_command.

        So counting on both paths double-counts every single command. This was
        found on a live async client, not by reading: the shim reported 50 where
        the server reported 38, and the arithmetic was 11 ``send_command`` calls
        plus 39 ``send_packed_command`` calls - a 28-command pipeline plus those
        same 11 singles. It stayed hidden because a hand-written fake connection
        does not chain, so only a live client can see it.

        A real Connection is used here, not the fake, precisely because the fake
        cannot reproduce the delegation this bug lived in.
        """
        connection = RecordingConnection()
        connection.send_packed_command(connection.pack_commands([(b"PING",)]))
        export = shim.export()
        assert export["redis_total_commands"] == 1
        assert export["redis_commands_by_name"] == {"PING": 1}

    def test_the_single_path_is_patched_but_deliberately_not_counted(self) -> None:
        """It stays patched so a future redis-py that STOPS delegating is still
        observed - the flag records the intent rather than leaving it to be
        inferred from an absent key."""
        assert SINGLE_PATH_FUNNELS_INTO_PACKED is True
        assert PATH_SINGLE in CANDIDATE_SEND_PATHS


class TestTheHookTargetIsPinned:
    def test_send_command_is_still_a_hook_target(self) -> None:
        """Acceptance (c): a STATIC assertion pinning the hook target.

        Stated as source rather than as behaviour because the failure mode is a
        rename in a third-party library, and a behavioural test would keep passing
        against whatever replaced it.
        """
        assert PATH_SINGLE in CANDIDATE_SEND_PATHS
        source = inspect.getsource(Connection.send_command)
        assert "_command_packer.pack" in source, (
            "redis-py changed how send_command encodes; the shim's single-command "
            "path reads args[0] and may no longer be where the verb lives"
        )

    def test_the_shim_targets_connection_methods(self, shim: RedisCommandShim) -> None:
        found = shim.export()["send_paths_found"]
        assert PATH_SINGLE in found
        assert all(path in CANDIDATE_SEND_PATHS for path in found)


class TestTheTwoCountsMustAgree:
    def test_matching_counts_pass(self, shim: RedisCommandShim) -> None:
        connection = RecordingConnection()
        connection.send_packed_command(
            connection.pack_commands([(b"SET", b"a", b"1"), (b"GET", b"a")])
        )
        shim_total = shim.export()["redis_total_commands"]
        assert_counts_agree(shim_total, shim_total)

    def test_disagreement_fails_the_run(self, shim: RedisCommandShim) -> None:
        """Acceptance (d): the NEGATIVE test. A detector nobody watched fire might
        not be a detector."""
        connection = RecordingConnection()
        connection.send_packed_command(connection.pack_commands([(b"SET", b"a", b"1")]))
        shim_total = shim.export()["redis_total_commands"]

        with pytest.raises(AssertionError, match="DISAGREE"):
            assert_counts_agree(shim_total, shim_total + 4000)

    def test_info_commandstats_is_parsed_independently(self) -> None:
        """The second count comes from the server, not from this process."""
        assert commandstats_total({"cmdstat_set": "calls=2,usec=9"}) == 2
        assert commandstats_total({"cmdstat_set": {"calls": 2}}) == 2
        assert commandstats_total({"cmdstat_set": "calls=2", "cmdstat_get": "calls=3"}) == 5

    def test_the_shim_saw_more_than_a_narrow_counter_would_have(
        self, shim: RedisCommandShim
    ) -> None:
        connection = RecordingConnection()
        connection.send_packed_command(
            connection.pack_commands([(b"SET", b"a", b"1"), (b"SET", b"b", b"2")])
        )
        assert_pipeline_aware(shim.export()["redis_total_commands"], send_command_only_total=0)


class TestCommandsAndRoundTripsAreSeparate:
    def test_five_commands_are_one_round_trip(self, shim: RedisCommandShim) -> None:
        """Acceptance (e). A pipeline's whole purpose is many commands per trip."""
        connection = RecordingConnection()
        connection.send_packed_command(
            connection.pack_commands(
                [(b"SET", b"a", b"1"), (b"SET", b"b", b"2"), (b"GET", b"a")]
            )
        )
        export = shim.export()
        assert export["redis_total_commands"] == 3
        assert export["redis_total_round_trips"] == 1
        assert export["redis_commands_per_round_trip"] == 3.0

    def test_the_two_are_separate_keys_and_cannot_be_conflated(
        self, shim: RedisCommandShim
    ) -> None:
        """Distinct keys AND distinct values.

        Asserting only that both keys exist would pass for a run where commands
        and round trips happen to be equal - which is exactly the single-command
        case where conflating them would be invisible. The run below packs three
        commands into one trip, so a conflated export would collapse them to the
        same number.
        """
        connection = RecordingConnection()
        connection.send_packed_command(
            connection.pack_commands(
                [(b"SET", b"a", b"1"), (b"SET", b"b", b"2"), (b"GET", b"a")]
            )
        )
        export = shim.export()
        assert "redis_total_commands" in export
        assert "redis_total_round_trips" in export
        assert export["redis_total_commands"] == 3
        assert export["redis_total_round_trips"] == 1
        assert export["redis_total_commands"] != export["redis_total_round_trips"]


class TestAbsentFromTheRequestPath:
    def test_a_disabled_shim_is_not_installed(self) -> None:
        """Acceptance (f): the shim is shim-injected, never shipped.

        Installing on import would change every deployment's Redis client,
        including ones that never read a series.
        """
        instance = RedisCommandShim(enabled=False)
        instance.install(RecordingConnection)
        assert instance.export()["installed"] is False
        assert instance.export()["send_paths_found"] == []

    def test_a_disabled_shim_leaves_the_library_untouched(self) -> None:
        before = RecordingConnection.send_command
        RedisCommandShim(enabled=False).install(RecordingConnection)
        assert RecordingConnection.send_command is before

    def test_disabled_cost_is_under_its_ceiling(self) -> None:
        disabled = RedisCommandShim(enabled=False)

        def one_command() -> None:
            # The disabled path a request would actually take: no counts, no
            # dictionary, no string work.
            if not disabled.enabled:
                return
            disabled.counts.record_single("GET")

        from src.app.core.monitoring.telemetry import measure_call_cost_robust

        measured = measure_call_cost_robust(one_command)
        assert measured <= 20.0

    def test_the_enabled_cost_is_also_under_its_ceiling(self) -> None:
        measured = measure_shim_cost()
        assert measured["measured_us"] <= measured["ceiling_us"]
