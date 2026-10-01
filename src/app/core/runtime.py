"""Reusable asynchronous runtime lifecycle primitives.

This module contains mechanism only. Application-specific phase assembly belongs
in the application composition root, while feature registration belongs in the
worker or event composition root.
"""

import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import NamedTuple

from src.app.core.logger import get_logger

logger = get_logger(__name__)

# muse 18: a phase over this budget is logged at WARNING. The point is not
# the specific number but that boot cost is visible at all — the token
# blacklist warm is bounded at 15s, so before this existed nobody could tell
# whether production boot was 200ms or 15s.
PHASE_SLOW_THRESHOLD_MS = 1_000


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
) -> AsyncGenerator[None]:
    """Run phase startups in order, then teardowns in reverse plus ``tail``.

    Each startup is timed and logged, because the phase NAME is an observable
    startup contract and the duration behind it was previously invisible. A
    phase over ``PHASE_SLOW_THRESHOLD_MS`` also logs a warning, so a boot
    latency cliff is a log line rather than something you have to go measure.

    ``teardown_on_startup_failure`` preserves the two existing application
    policies without embedding either policy in this mechanism.
    """
    boot_started = time.perf_counter()
    try:
        for phase in phases:
            phase_started = time.perf_counter()
            await phase.startup()
            elapsed_ms = (time.perf_counter() - phase_started) * 1000
            logger.info(
                "startup_phase_completed",
                phase=phase.name,
                duration_ms=round(elapsed_ms, 2),
                boot_elapsed_ms=round((time.perf_counter() - boot_started) * 1000, 2),
            )
            if elapsed_ms > PHASE_SLOW_THRESHOLD_MS:
                logger.warning(
                    "startup_phase_slow",
                    phase=phase.name,
                    duration_ms=round(elapsed_ms, 2),
                    threshold_ms=PHASE_SLOW_THRESHOLD_MS,
                )
    except BaseException:
        if teardown_on_startup_failure:
            for phase in reversed(phases):
                if phase.teardown is not None:
                    await phase.teardown()
            for teardown in tail:
                await teardown()
        raise
    try:
        logger.info(
            "startup_phases_completed",
            phase_count=len(phases),
            boot_duration_ms=round((time.perf_counter() - boot_started) * 1000, 2),
        )
        yield
    finally:
        for phase in reversed(phases):
            if phase.teardown is not None:
                await phase.teardown()
        for teardown in tail:
            await teardown()
