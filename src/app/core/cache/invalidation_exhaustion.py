"""The ONE exhaustion policy for cache invalidation, declared per key family.

Four families invalidate cached state after a mutation — ``tenant``, ``tier``,
``rate_limit`` and ``user`` — and each one re-derived the same skeleton: retry
once, warn on the first failure, error on the second, and (for two of them)
leave a poison-key marker. The skeleton was identical; only the marker decision
diverged, and it diverged *implicitly*: nothing in the code said "tier is
log-only on purpose", it just did not write a marker. That is the failure mode
this module removes — the divergence is now a DECLARED value on the family's
policy row, so flipping it later (for a dashboard/monitoring reader of the
markers) is a one-line declaration change rather than a behavioural discovery.

The marker is an intentional operational signal, not debris: a dashboard,
ops sweep or alert consumes ``<family>:invalidation_failed:<id>`` to find the
entity whose cache could not be invalidated. The reader itself is a parked
follow-up tied to the dashboard project — the WRITER stays.

What this module owns:

* the ``for attempt in (1, 2)`` retry-once loop (it exists here, once);
* warning on the first failure, error on exhaustion;
* the marker decision, taken from the declared policy rather than from which
  body the code happens to live in.

What it deliberately does NOT own, per ADR-0004: TTLs, ``maxsize`` values, or
the eventual-consistency bound. The stale window is still bounded by each
family's own TTL; the error log names that bound, it does not change it. A
family's marker TTL is chosen by the family and passed in as the marker writer,
so moving a TTL stays a feature-side decision.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.app.core.logger import get_logger

logger = get_logger(__name__)

#: Attempts, including the first one. Failure policy, not a TTL.
INVALIDATION_ATTEMPTS: tuple[int, ...] = (1, 2)


class ExhaustionMarker(StrEnum):
    """What a family leaves behind when its invalidation retries are exhausted.

    ``POISON_KEY`` writes a marker so a later reader/ops sweep can detect the
    entity whose cache could not be invalidated. ``LOG_ONLY`` records the
    exhaustion in the error log and nothing else. Both are valid; a family
    states which one it is.
    """

    POISON_KEY = "poison_key"
    LOG_ONLY = "log_only"


@dataclass(frozen=True)
class InvalidationPolicy:
    """One key family's declared exhaustion behaviour.

    :param family: the log-facing name of the key family ("Tenant", "User", …).
        It reproduces the caller's original log wording so existing log
        searches keep matching.
    :param on_exhaustion: whether exhaustion leaves a poison-key marker.
    :param exhausted_message: the error message naming what stays stale and
        that the TTL bounds it. Per family because the stale thing differs
        (a tenant's ``is_active``, a tier's compiled policy, …).
    """

    family: str
    on_exhaustion: ExhaustionMarker
    exhausted_message: str

    def __post_init__(self) -> None:
        if not self.family.strip():
            raise ValueError("InvalidationPolicy.family must be a non-empty name")
        if not self.exhausted_message.strip():
            raise ValueError("InvalidationPolicy.exhausted_message must name what stays stale")


async def invalidate_with_exhaustion(
    invalidate: Callable[[], Awaitable[None]],
    *,
    policy: InvalidationPolicy,
    log_context: Mapping[str, Any],
    write_marker: Callable[[], Awaitable[None]] | None = None,
) -> bool:
    """Run ``invalidate`` under the family's declared exhaustion policy.

    One attempt, then exactly one retry. The first failure is a warning
    (recoverable after retry); exhaustion is an error that names what stays
    stale, and — when the family declares it — a poison-key marker so a later
    reader can find the affected entity.

    :param invalidate: the mutation-side invalidation to run. Any exception it
        raises is a failure under this policy; it is never swallowed silently.
    :param policy: the family's declared policy row.
    :param log_context: identifiers for the log records (ids, keys, the poison
        key). Merged into both the warning and the error so an exhausted
        invalidation is attributable.
    :param write_marker: writes the family's poison-key marker. Required when
        ``policy.on_exhaustion`` is ``POISON_KEY`` — a missing one is a
        misdeclaration and fails loud rather than degrading to log-only. It is
        IGNORED for a ``LOG_ONLY`` family: the declared decision wins, so a
        log-only family cannot grow a marker by being handed a writer.
    :returns: ``True`` if the invalidation succeeded within the retry budget,
        ``False`` if the retries were exhausted.
    :raises ValueError: if the family declares a marker but no writer is given.
    """
    if policy.on_exhaustion is ExhaustionMarker.POISON_KEY and write_marker is None:
        raise ValueError(f"family {policy.family!r} declares a poison-key marker but no write_marker was given")

    for attempt in INVALIDATION_ATTEMPTS:
        try:
            await invalidate()
            return True
        except Exception as e:
            if attempt < max(INVALIDATION_ATTEMPTS):
                # Recoverable: the retry below may still land the delete.
                logger.warning(
                    f"{policy.family} cache invalidation failed (retrying)",
                    attempt=attempt,
                    error=str(e),
                    **log_context,
                )
                continue

            # Exhausted: the delete is lost, so the stale window is only
            # bounded by the TTL. The family decides whether that is visible
            # to a reader (marker) or to an operator log (log-only).
            logger.error(policy.exhausted_message, attempt=attempt, error=str(e), **log_context)
            # The DECLARED decision, not the presence of a writer, decides
            # whether a marker is left behind.
            if policy.on_exhaustion is ExhaustionMarker.POISON_KEY and write_marker is not None:
                try:
                    await write_marker()
                except Exception:
                    # Redis itself is down; the error log above is the record.
                    pass

    return False


__all__ = [
    "INVALIDATION_ATTEMPTS",
    "ExhaustionMarker",
    "InvalidationPolicy",
    "invalidate_with_exhaustion",
]
