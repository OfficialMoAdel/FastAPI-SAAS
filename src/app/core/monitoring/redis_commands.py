"""T-6: pipeline-aware Redis command counting, with an independent second count.

WHY send_command, AND WHY send_command ALONE IS NOT ENOUGH HERE
--------------------------------------------------------------
The bead specifies patching ``Connection.send_command`` and not
``Redis.execute_command``, because redis-py sends pipelined commands through
``send_command`` and a counter on ``execute_command`` is structurally blind to
them (perf J6: ``census.txt`` shows zero ``PIPE:`` entries on the refresh path
while ``driver_count_io.out`` proves that path emits ``PIPE:ZADD/DEL/RPUSH/...``).
That reasoning is right, and this module patches ``send_command``.

It is not sufficient on its own in redis-py 5.3.0, which has THREE send paths,
and a counter that only wraps one of them is blind to two:

1. ``Connection.send_command(*args)`` - the single-command path, used by
   ``Redis.execute_command`` for a non-pipelined call. Wrapping this is what the
   bead asks for and it works.
2. ``Connection.send_packed_command(all_cmds)`` - the PACKED path. A pipeline does
   not call ``send_command`` per command. ``Pipeline._execute_transaction`` packs
   MULTI/EXEC and every staged command into one buffer and calls this ONCE. A
   counter on ``send_command`` alone sees a transaction as a single opaque send
   and cannot attribute the commands inside it.
3. ``Connection.send_pipelined_command`` - which does NOT exist in redis-py
   5.3.0. Read off ``AbstractConnection`` at install time rather than assumed: a
   path named by the instrument but absent from the installed library would be
   wrapped by nothing and would read as a legitimately absent path.

So this module wraps every send path the INSTALLED library actually has, and
records which ones it found. Wrapping only ``send_command`` - the literal
instruction - would have produced a counter that silently under-reports precisely
the traffic the bead exists to count, and the under-report would have looked like
"fewer commands than expected" rather than like a bug in the instrument.

Commands are reported SEPARATELY from round trips
-------------------------------------------------
Unified plan §4.5 and T-6 both require commands and round trips to be counted
separately, because conflating them is how a pipeline improvement is invisible: a
batch of 50 commands sent in one round trip is 50 commands and 1 round trip, and
an artefact reporting only "51 operations" cannot tell which it measured. P-11
exists to show whether the round trips are worth removing, and it can only do
that if the two counts are separable.

TWO INDEPENDENT COUNTS MUST AGREE
---------------------------------
The second count is ``INFO commandstats`` - Redis's own tally, read as a delta
across the run. It is independent because it comes from the server rather than
from this process. :func:`assert_counts_agree` compares them, and disagreement
is not a rounding difference to be tolerated: it locates a real defect. The two
disagree when the shim missed a path (finding 1), when a command went out on a
connection this process did not create (finding 2), or when ``commandstats`` was
itself reset mid-run (finding 3) - and each of those has a different fix, which is
why the artefact reports the delta rather than only the verdict.

SHIM-INJECTABLE, NOT SHIPPED IN THE REQUEST PATH
------------------------------------------------
Nothing here is wired into the request path. :class:`RedisCommandShim` is
installed explicitly, by a benchmark, against a connection this process owns. It
is never imported by the application at startup, because an instrument that
monkeypatches a third-party library on import changes the behaviour of every
deployment that imports the module that imports it - including ones that never
look at the series.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.app.core.monitoring.telemetry import (
    Ceiling,
    assert_cost_under_ceiling,
)
from src.app.core.monitoring.telemetry import (
    measure_call_cost_robust as measure_call_cost,
)

#: The three send paths this redis-py version uses. Named because the artefact has
#: to say WHICH path a command took: a run whose traffic all arrived packed is a
#: different experiment from one that sent everything singly, and a single total
#: cannot express that.
PATH_SINGLE = "send_command"
PATH_PACKED_TRANSACTION = "send_packed_command"
PATH_PACKED_PIPELINE = "send_pipelined_command"

#: Probed against the installed library rather than assumed, because redis-py 5.3.0
#: has ``send_command`` and ``send_packed_command`` and NOT ``send_pipelined_command``.
#: A path the instrument names but the library does not have is a path that wraps
#: nothing, and a path that wraps nothing reads as a path with no traffic.
CANDIDATE_SEND_PATHS: tuple[str, ...] = (PATH_SINGLE, PATH_PACKED_TRANSACTION, PATH_PACKED_PIPELINE)

#: The counter's added per-command cost. Absolute, chosen in advance. One dict
#: increment and one string lookup; a counter that measurably slows the Redis
#: client is measuring itself.
COUNTER_COST_CEILING_US = 20.0
COUNTER_COST_CEILING = Ceiling(COUNTER_COST_CEILING_US, "T-6 shim per-command cost")

#: The agreement tolerance between the shim's count and Redis's own. Absolute.
#: Not zero, because ``INFO commandstats`` is itself a command and is counted by
#: neither side consistently; not loose, because a large disagreement is the
#: signal this check exists to catch.
COUNTS_AGREE_TOLERANCE = 2


@dataclass
class RedisCommandCounts:
    """Command counts split by name and by send path, plus round trips.

    Round trips are counted separately from commands because a pipeline's whole
    purpose is to move many commands per round trip, and an artefact reporting
    only a command total cannot show whether a change moved either.
    """

    commands_by_name: dict[str, int] = field(default_factory=dict)
    commands_by_path: dict[str, int] = field(default_factory=dict)
    round_trips_by_path: dict[str, int] = field(default_factory=dict)
    total_commands: int = 0
    total_round_trips: int = 0

    def record_single(self, name: str) -> None:
        self._count(name, PATH_SINGLE, round_trips=1)

    def record_packed(self, path: str, names: list[str]) -> None:
        for name in names:
            self._count(name, path, round_trips=0)
        self.round_trips_by_path[path] = self.round_trips_by_path.get(path, 0) + 1
        self.total_round_trips += 1

    def _count(self, name: str, path: str, *, round_trips: int) -> None:
        self.commands_by_name[name] = self.commands_by_name.get(name, 0) + 1
        self.commands_by_path[path] = self.commands_by_path.get(path, 0) + 1
        self.total_commands += 1
        if round_trips:
            self.round_trips_by_path[path] = self.round_trips_by_path.get(path, 0) + round_trips
            self.total_round_trips += round_trips

    def export(self) -> dict[str, Any]:
        return {
            "redis_total_commands": self.total_commands,
            "redis_total_round_trips": self.total_round_trips,
            "redis_commands_by_name": dict(sorted(self.commands_by_name.items())),
            "redis_commands_by_path": dict(self.commands_by_path),
            "redis_round_trips_by_path": dict(self.round_trips_by_path),
            "redis_commands_per_round_trip": (
                round(self.total_commands / self.total_round_trips, 3)
                if self.total_round_trips
                else 0.0
            ),
        }

    def reset(self) -> None:
        self.commands_by_name.clear()
        self.commands_by_path.clear()
        self.round_trips_by_path.clear()
        self.total_commands = 0
        self.total_round_trips = 0


class RedisCommandShim:
    """Wraps the redis-py connection send paths to count commands.

    Installed and removed explicitly. :meth:`install` is idempotent and records
    what it replaced so :meth:`uninstall` restores the library exactly - leaving
    a patched ``send_command`` behind would silently change every later user of
    that connection object in the same process, including the test suite.
    """

    name = "redis_commands"

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self._counts = RedisCommandCounts()
        self._installed = False
        self._originals: dict[str, Any] = {}
        self._installed_paths: list[str] = []
        self._absent_paths: list[str] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    @property
    def counts(self) -> RedisCommandCounts:
        return self._counts

    def install(self, connection_cls: type) -> RedisCommandShim:
        """Wrap the three send paths on ``connection_cls``. Idempotent."""
        if not self._enabled or self._installed:
            return self
        for path in CANDIDATE_SEND_PATHS:
            original = getattr(connection_cls, path, None)
            if original is None:
                # Absent in this redis-py. Recorded rather than silently skipped:
                # a blind spot that announces itself can be closed, while one that
                # does not is read as a low command count.
                self._absent_paths.append(path)
                continue
            self._originals[path] = original
            setattr(connection_cls, path, self._wrap(path, original))
            self._installed_paths.append(path)
        self._installed = True
        return self

    def uninstall(self, connection_cls: type) -> None:
        """Restore every wrapped path exactly as it was."""
        if not self._installed:
            return
        for path, original in self._originals.items():
            setattr(connection_cls, path, original)
        self._originals.clear()
        self._installed_paths.clear()
        self._installed = False

    def _wrap(self, path: str, original: Any) -> Any:
        """Build the replacement for one send path.

        A bound-method replacement rather than a closure over ``self`` alone, so
        the wrapper is a plain function on the class and the original is reachable
        for restoration. The wrapper is deliberately thin: it reads the command
        name and delegates, and nothing else - an instrument that inspects
        arguments, decodes payloads, or builds strings here would be measurably
        slowing the client it counts.
        """
        counts = self._counts
        enabled = self._enabled

        if path == PATH_SINGLE:
            def send_command(this_connection: Any, *args: Any, **kwargs: Any) -> Any:
                if enabled:
                    counts.record_single(_command_name(args))
                return original(this_connection, *args, **kwargs)
            return send_command

        def packed(this_connection: Any, all_cmds: Any, **kwargs: Any) -> Any:
            if enabled:
                counts.record_packed(path, _packed_command_names(all_cmds))
            return original(this_connection, all_cmds, **kwargs)

        return packed

    def export(self) -> dict[str, Any]:
        return {
            "instrument": self.name,
            "enabled": self._enabled,
            "installed": self._installed,
            "send_paths_found": list(self._installed_paths),
            "send_paths_absent_in_this_redis": list(self._absent_paths),
            "cost_ceiling_us": COUNTER_COST_CEILING_US,
            **self._counts.export(),
        }

    def reset(self) -> None:
        self._counts.reset()


def _command_name(args: tuple[Any, ...]) -> str:
    """The command name from a redis-py ``send_command`` argument tuple.

    ``args[0]`` is the command, as bytes or str depending on
    ``decode_responses``. Normalised to upper-case str so a byte command and a
    string command for the same verb do not appear as two different commands in
    the artefact.
    """
    if not args:
        return "<empty>"
    first = args[0]
    if isinstance(first, bytes):
        return first.decode("ascii", "replace").upper()
    return str(first).upper()


def _packed_command_names(all_cmds: Any) -> list[str]:
    """Command names out of redis-py's PACKED buffer.

    The shape was read off the installed library rather than assumed, and the
    assumption an earlier version of this function made was wrong in a way that
    mattered. ``Connection.send_packed_command`` does::

        for item in command:
            self._sock.sendall(item)

    so ``command`` is a list of ALREADY-SERIALISED RESP chunks - one complete
    command per element, each looking like
    ``b"*3\r\n$4\r\nZADD\r\n$1\r\nk\r\n..."``. Taking every second
    element as a name (the flat name/arg-array assumption) therefore reads
    argument bytes as commands: a ``ZADD key 1 member`` produced the names
    ``MULTI``, ``ZADD``, ``1``, ``EXEC``.

    Counting elements would also be wrong, because the number of commands in a
    packed transaction is not the number of elements unless the caller happened to
    pass one element per command - ``MULTI`` and ``EXEC`` are bracketed in by the
    transaction machinery and are not user commands.

    So the name is parsed out of each RESP chunk's bulk header. That is the only
    reading that is correct for both shapes redis-py produces.
    """
    names: list[str] = []
    try:
        entries = list(all_cmds)
    except TypeError:
        return names
    for entry in entries:
        names.append(_resp_command_name(entry))
    return names


def _resp_command_name(chunk: Any) -> str:
    """Pull the verb out of one serialised RESP command chunk.

    A RESP array of bulk strings is ``*<n>\r\n$<len>\r\n<name>\r\n...``. Only the
    second header is read; the arguments are never decoded, because an instrument
    that decodes payloads is doing work on the Redis hot path that the application
    did not ask for and cannot afford.

    Anything that does not parse is reported as ``<unparsed>`` rather than
    guessed. A wrong command name in the artefact is worse than an obviously
    unparsed one, because a wrong one is believed.
    """
    if isinstance(chunk, str):
        chunk = chunk.encode("utf-8", "replace")
    if not isinstance(chunk, bytes | bytearray):
        return "<unparsed>"
    data = bytes(chunk)
    if not data.startswith(b"*"):
        return "<unparsed>"
    # Skip the array header to the first bulk header, then take its payload.
    marker = data.find(b"$")
    if marker == -1:
        return "<unparsed>"
    length_end = data.find(b"\r\n", marker)
    if length_end == -1:
        return "<unparsed>"
    try:
        length = int(data[marker + 1 : length_end])
    except ValueError:
        return "<unparsed>"
    start = length_end + 2
    end = start + length
    if end > len(data):
        return "<unparsed>"
    return data[start:end].decode("ascii", "replace").upper()


def commandstats_total(commandstats: dict[str, Any]) -> int:
    """Total commands from a parsed ``INFO commandstats`` section.

    Each entry is ``cmdstat_<verb>: calls=N,usec=M,...``; the verb's suffix may
    carry a subcommand (``cmdstat_client|list``), so the key is split on ``|`` as
    well as the underscore prefix. Parsed rather than counted by key because the
    shard/cluster ``INFO`` shape differs and a counter that assumes one of them
    returns zero silently.
    """
    total = 0
    for key, value in commandstats.items():
        if not key.startswith("cmdstat_"):
            continue
        if isinstance(value, dict) and "calls" in value:
            total += int(value["calls"])
            continue
        text = str(value)
        marker = "calls="
        if marker in text:
            tail = text.split(marker, 1)[1]
            digits = ""
            for char in tail:
                if char.isdigit():
                    digits += char
                else:
                    break
            if digits:
                total += int(digits)
    return total


def assert_counts_agree(shim_total: int, redis_total: int, *,
                        tolerance: int = COUNTS_AGREE_TOLERANCE) -> int:
    """The two independent counts must agree, or the shim missed a path.

    ``shim_total`` comes from the wrapped send paths in this process;
    ``redis_total`` comes from ``INFO commandstats`` on the server. They are
    independent sources, and that is the entire value of the comparison: when
    they agree, the shim saw every command; when they do not, the shim is
    blind to something and every number derived from it is wrong.

    The tolerance covers only ``INFO``'s own commands, which neither side counts
    consistently. It is not a rounding allowance - a disagreement beyond it is
    reported as a defect with its size, because "the counts differ by 4000" and
    "the counts differ by 1" are different findings.
    """
    delta = abs(shim_total - redis_total)
    if delta > tolerance:
        raise AssertionError(
            f"T-6 counts DISAGREE: shim counted {shim_total} command(s), "
            f"INFO commandstats reports {redis_total} (delta {delta}, tolerance "
            f"{tolerance}). The shim missed a send path, or a command went out on "
            f"a connection this process did not create, or commandstats was reset "
            f"mid-run. Every per-command figure derived from this run is void until "
            f"that is identified."
        )
    return shim_total


def assert_pipeline_aware(shim_total: int, send_command_only_total: int) -> None:
    """Prove the shim is not blind to pipelined commands.

    ``send_command_only_total`` is what a counter wrapping only
    ``send_command`` - the literal, narrower instruction - would have reported.
    If the two are equal, every command arrived singly and this run says nothing
    about pipeline blindness; if the shim is higher, the packed paths carried
    traffic that the narrower counter would have missed, which is the defect
    perf J6 describes.
    """
    if shim_total == send_command_only_total:
        raise AssertionError(
            f"T-6 shim counted {shim_total} commands, identical to the "
            f"send_command-only count. Either no traffic was pipelined in this run, "
            f"or the packed send paths are unwrapped - and the second is the blind "
            f"spot this instrument exists to close. Distinguish the two before "
            f"quoting a per-command figure."
        )


def assert_cost(measured_us: float) -> float:
    """Check the shim's measured per-command cost against its ceiling."""
    return assert_cost_under_ceiling(measured_us, COUNTER_COST_CEILING)


def measure_shim_cost(*, repeats: int = 5000) -> dict[str, Any]:
    """Measure the shim's added per-command cost against its absolute ceiling."""
    shim = RedisCommandShim(enabled=True)
    counts = shim.counts

    def one_command() -> None:
        counts.record_single("GET")

    cost_us = measure_call_cost(one_command, repeats=repeats)
    assert_cost(cost_us)
    return {"measured_us": round(cost_us, 4), "ceiling_us": COUNTER_COST_CEILING_US}
