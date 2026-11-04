"""Exact Redis command-count windows derived from ``INFO commandstats``.

The protocol's :class:`~src.benchmarks.protocol.Ceiling` bounds a duration.
This module bounds a COUNT instead: how many Redis commands one operation
caused, as reported by Redis itself rather than by a client-side estimate.

Two properties are deliberate:

* The instrument's own ``INFO`` command is removed from every delta, because
  the second snapshot counts the call that produced it.
* A quiet window is checked before and after the operation. ``commandstats`` is
  server-wide, so any other traffic in the same window would be charged to the
  operation. A dirty control window refuses the measurement instead of
  publishing a number that describes another client.

The delta includes commands a Lua script calls on the server. Those appear under
their own ``cmdstat_*`` entries and are not wire round trips. A route budget
that needs ROUND TRIPS must therefore count client-issued commands separately;
this module's ceiling is a server command count, exactly as labelled.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from src.benchmarks import census, protocol

INSTRUMENT_COMMAND = "info"


class CommandCountMeasurementError(RuntimeError):
    """A command-count window could not produce a sound measurement."""


class CommandCountWindowDirty(CommandCountMeasurementError):
    """Another client issued Redis commands inside a control window."""


def _delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    delta = census.commandstats_delta(before, after)
    delta.pop(INSTRUMENT_COMMAND, None)
    return delta


async def _assert_quiet(client: Any, label: str) -> None:
    before = await census.commandstats_snapshot(client)
    after = await census.commandstats_snapshot(client)
    delta = _delta(before, after)
    if delta:
        raise CommandCountWindowDirty(
            f"{label} window is not quiet: {delta}. commandstats is server-wide, so this "
            "measurement would charge another client's commands to the operation; use a "
            "dedicated Redis instance or wait for the traffic to stop."
        )


async def measure_command_counts(
    client: Any,
    operation: Callable[[], Awaitable[Any]],
    *,
    name: str,
    require_quiet: bool = True,
) -> protocol.CommandCountResult:
    """Measure the exact Redis command delta caused by one operation.

    ``operation`` is awaited once. Redis/SQL errors from it, and errors from the
    snapshots, propagate unchanged: a failed operation is not a measurement of
    a successful one.
    """
    if require_quiet:
        await _assert_quiet(client, f"{name} pre-operation")

    before = await census.commandstats_snapshot(client)
    await operation()
    after = await census.commandstats_snapshot(client)

    if require_quiet:
        await _assert_quiet(client, f"{name} post-operation")

    return protocol.CommandCountResult.from_counts(name, _delta(before, after))
