"""The one job-level retry schedule: attempt -> delay, with jitter.

WHY THIS EXISTS

Three worker bodies carried the same 25 lines, each slightly differently:

* ``core/events/worker.py`` — clamp, ``round`` jitter, live-config ladder;
* ``modules/notifications/service.py`` — NO clamp, ``round`` jitter, module
  constant ladder;
* ``modules/webhooks/delivery.py`` — ``ceil`` jitter, live-config ladder, and
  an operator override.

The retry interface was "copy these lines into your body". The differences that
mattered were not the ones that looked intentional: one copy clamped a malformed
``job_try`` and one did not, so the two bodies disagreed about what a bad
counter means; and ``round`` vs ``ceil`` meant the same nominal jitter produced
a delay up to a second apart.

WHAT THIS DOES NOT DO

It does not decide the ceiling, and it does not perform the terminal action.
Those genuinely differ per caller — the event bus moves a job to the DLQ, the
notification worker marks the row FAILED, the webhook delivery flips its status
— and folding them in would couple this policy to three different lifecycles.
The caller asks "what delay?" and owns everything else.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

#: Jitter band, shared by every caller. +/-10% is enough to decorrelate a burst
#: that failed together without disturbing the operator-visible schedule.
JITTER_FRACTION = 0.1


def jittered_delay(seconds: float, *, jitter: bool = True) -> int:
    """Round a nominal delay into the whole seconds arq/ARQ-style deferrals take.

    This is the arithmetic that was copied three times and had drifted: two
    copies used ``round`` and one used ``ceil``, so the same nominal jitter
    produced delays up to a second apart. ``ceil`` wins because a fractional
    delay must never be truncated toward an immediate retry, and the floor of
    1 s stops a sub-second value becoming a hot loop.

    Callers that index their own schedule (the webhook delivery path has an
    ``override_delay`` for the circuit-open case, which bypasses the ladder
    entirely) use this directly; ``retry_delay`` is the ladder-shaped wrapper.
    """
    if not jitter:
        return max(1, int(seconds))
    return max(
        1,
        int(math.ceil(seconds * random.uniform(1 - JITTER_FRACTION, 1 + JITTER_FRACTION))),
    )


def retry_delay(
    ladder: Sequence[int],
    job_try: int,
    *,
    max_tries: int,
    jitter: bool = True,
) -> int | None:
    """Seconds to defer before the next attempt, or None when exhausted.

    Args:
        ladder: Backoff schedule in seconds. Callers resolve this themselves so
            each keeps its own configurability (the event bus and webhooks read
            settings live; notifications uses a module constant).
        job_try: The attempt that just failed, 1-indexed (arq's ``job_try``).
        max_tries: The caller's own ceiling. Deliberately NOT derived here: the
            three callers have different blast radii, so the ceiling stays with
            the caller.
        jitter: Apply +/-10% so a burst that failed together does not retry in
            lockstep. Default True; tests pass False for an exact schedule.

    Returns:
        The delay in whole seconds, or ``None`` when no retry remains and the
        caller should run its terminal branch.

    A malformed counter is CLAMPED to the first attempt rather than trusted.
    ``job_try`` indexes the ladder, so ``0`` or a negative would select the
    LAST (longest) entry — a bad counter would silently become the slowest
    possible retry. arq starts at 1, so this is a guard, not a hot path; but it
    is the guard that only one of the three copies had.
    """
    if not isinstance(job_try, int) or job_try < 1:
        job_try = 1

    if job_try >= max_tries:
        return None

    if not ladder:
        # An operator can configure an empty schedule. Falling back to "retry
        # immediately" would hot-loop; treat it as "no schedule" and let the
        # caller's terminal branch handle it.
        return None

    base = ladder[min(job_try - 1, len(ladder) - 1)]
    return jittered_delay(base, jitter=jitter)


__all__ = ["JITTER_FRACTION", "jittered_delay", "retry_delay"]
