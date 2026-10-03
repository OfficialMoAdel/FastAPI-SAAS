"""Log sampling processor (P10).

Probabilistically drops INFO/DEBUG structlog events before they reach the
sanitizer and renderer, reducing per-event CPU (16-pattern sanitization pass)
and log volume on hot paths.

Safety rules:
- WARNING / ERROR / CRITICAL / exception events are NEVER dropped.
- Events carrying ``exc_info`` are never dropped (diagnostic value).
- Only attached to the structlog-native processor chain. It must NOT run in
  ``ProcessorFormatter.foreign_pre_chain``: structlog 25.x's
  ``ProcessorFormatter.format`` does not catch ``DropEvent`` there, so a drop
  on a stdlib record would surface as a logging error.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Any

from structlog.types import EventDict, Processor

# Levels exempt from sampling — always kept.
_NEVER_SAMPLE = frozenset({"warning", "error", "critical", "exception"})


class SamplingProcessor:
    """structlog processor that keeps each INFO/DEBUG event with probability ``rate``.

    Args:
        rate: Keep-probability in [0.0, 1.0]. 1.0 disables sampling entirely
            (fast path: no random draw). 0.0 drops every sampleable event.
    """

    __slots__ = ("_rate", "_draw", "_rates")

    def __init__(
        self,
        rate: float = 1.0,
        *,
        rates: Mapping[str, float] | None = None,
        _rng: random.Random | None = None,
    ) -> None:
        self._rate = max(0.0, min(1.0, float(rate)))
        # Injectable RNG for deterministic tests.
        self._draw = (_rng or random).random
        # S-05c: optional per-event overrides, keyed on the structlog event
        # name. One global rate forces a bad trade-off — set it low to control
        # volume from hot paths and you lose the rare-but-critical events; set
        # it high to keep those and you drown in noise. Overrides let a rare
        # event pin itself to 1.0 while a hot one is sampled hard.
        self._rates: dict[str, float] = {
            k: max(0.0, min(1.0, float(v))) for k, v in (rates or {}).items()
        }

    @property
    def rate(self) -> float:
        return self._rate

    def __call__(self, logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
        # Per-event override wins over the global rate, so an event can opt
        # OUT of sampling even when the global rate is low.
        event_name = event_dict.get("event")
        override = self._rates.get(event_name) if (self._rates and isinstance(event_name, str)) else None
        if override is not None and override >= 1.0:
            return event_dict
        # Fast path: sampling disabled — zero overhead beyond this check.
        effective = self._rate if override is None else override
        if effective >= 1.0:
            return event_dict
        # Never sample warnings/errors or events carrying tracebacks.
        if method_name in _NEVER_SAMPLE or event_dict.get("exc_info"):
            return event_dict
        if effective <= 0.0 or self._draw() >= effective:
            from structlog import DropEvent

            raise DropEvent
        return event_dict


def make_sampling_processor(
    rate: float,
    *,
    rates: Mapping[str, float] | None = None,
) -> Processor:
    """Build the processor; returns a plain pass-through callable when rate >= 1.0."""
    if rate >= 1.0 and not rates:

        def _passthrough(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
            return event_dict

        return _passthrough
    return SamplingProcessor(rate)
