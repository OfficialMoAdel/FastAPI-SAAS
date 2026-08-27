"""Tests for the P10 log sampling processor."""

from __future__ import annotations

import random

import pytest
import structlog

from src.app.core.observability.log_sampling import SamplingProcessor, make_sampling_processor


class TestSamplingProcessor:
    def test_rate_one_keeps_everything(self) -> None:
        proc = SamplingProcessor(1.0)
        for method in ("debug", "info", "warning", "error"):
            ed = {"event": "x"}
            assert proc(None, method, ed) is ed

    def test_rate_zero_drops_only_info_and_debug(self) -> None:
        proc = SamplingProcessor(0.0)
        with pytest.raises(structlog.DropEvent):
            proc(None, "info", {"event": "x"})
        with pytest.raises(structlog.DropEvent):
            proc(None, "debug", {"event": "x"})
        # warning/error/critical always survive
        for method in ("warning", "error", "critical", "exception"):
            assert proc(None, method, {"event": "x"}) == {"event": "x"}

    def test_exc_info_never_dropped(self) -> None:
        proc = SamplingProcessor(0.0)
        ed = {"event": "boom", "exc_info": (ValueError, ValueError("x"), None)}
        assert proc(None, "info", ed) is ed

    def test_probabilistic_keep_rate(self) -> None:
        """With a seeded RNG the keep fraction matches the configured rate."""
        rate = 0.3
        rng = random.Random(42)
        proc = SamplingProcessor(rate, _rng=rng)
        kept = sum(1 for _ in range(10_000) if _kept(proc))
        assert abs(kept / 10_000 - rate) < 0.02

    def test_rate_clamped_to_unit_interval(self) -> None:
        assert SamplingProcessor(5.0).rate == 1.0
        assert SamplingProcessor(-1.0).rate == 0.0


class TestMakeSamplingProcessor:
    def test_passthrough_when_disabled(self) -> None:
        proc = make_sampling_processor(1.0)
        ed = {"event": "x"}
        assert proc(None, "info", ed) is ed

    def test_returns_sampler_below_one(self) -> None:
        proc = make_sampling_processor(0.5)
        assert isinstance(proc, SamplingProcessor)


def _kept(proc: SamplingProcessor) -> bool:
    try:
        proc(None, "info", {"event": "x"})
        return True
    except structlog.DropEvent:
        return False
