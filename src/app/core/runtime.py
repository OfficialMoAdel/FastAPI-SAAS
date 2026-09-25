"""Reusable asynchronous runtime lifecycle primitives.

This module contains mechanism only. Application-specific phase assembly belongs
in the application composition root, while feature registration belongs in the
worker or event composition root.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import NamedTuple


class StartupPhase(NamedTuple):
    """One ordered runtime step and its optional paired teardown."""

    name: str
    startup: Callable[[], Awaitable[None]]
    teardown: Callable[[], Awaitable[None]] | None = None


@asynccontextmanager
async def compose_phases(
    phases: Sequence[StartupPhase],
    tail: Sequence[Callable[[], Awaitable[None]]] = (),
    teardown_on_startup_failure: bool = False,
) -> AsyncGenerator[None, None]:
    """Run phase startups in order, then teardowns in reverse plus ``tail``.

    ``teardown_on_startup_failure`` preserves the two existing application
    policies without embedding either policy in this mechanism.
    """
    try:
        for phase in phases:
            await phase.startup()
    except BaseException:
        if teardown_on_startup_failure:
            for phase in reversed(phases):
                if phase.teardown is not None:
                    await phase.teardown()
            for teardown in tail:
                await teardown()
        raise
    try:
        yield
    finally:
        for phase in reversed(phases):
            if phase.teardown is not None:
                await phase.teardown()
        for teardown in tail:
            await teardown()
