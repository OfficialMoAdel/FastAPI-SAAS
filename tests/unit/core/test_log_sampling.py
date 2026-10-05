"""Tests for the P10 log sampling processor."""

from __future__ import annotations

import ast
import inspect
import random
import textwrap

import pytest
import structlog
from structlog.types import Processor

from src.app.core import logger as logger_mod
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


class TestMakeSamplingProcessorForwardsRates:
    """F-12: ``make_sampling_processor`` accepted ``rates`` and dropped it.

    It forwarded ``rates`` to nothing: the constructor call was
    ``SamplingProcessor(rate)``, so every caller's per-event override was
    discarded and every event was sampled at the global rate. Invisible because
    ``logger.setup_logging`` passes ``rates=settings.performance.LOG_SAMPLING_RATES``,
    whose default is ``{}`` — so the bug only ever bit a deployment that
    actually configured an override.

    Every assertion here uses the deterministic endpoints 0.0 and 1.0, so none of
    them depend on an RNG draw and none can flake.
    """

    def test_the_map_reaches_the_processor(self) -> None:
        proc = make_sampling_processor(0.5, rates={"rare_but_critical": 0.0})
        assert isinstance(proc, SamplingProcessor)
        assert proc._rates == {"rare_but_critical": 0.0}

    def test_an_override_of_zero_drops_the_named_event(self) -> None:
        """The behaviour the whole Mapping path exists for.

        Pre-fix this returned the event unchanged, because the override never
        reached the constructor.

        The global rate is 1.0 so the unnamed event's fate is deterministic —
        at 0.5 it would be kept roughly half the time, and a test that flakes
        is a test that gets deleted instead of fixed.
        """
        proc = make_sampling_processor(1.0, rates={"noisy": 0.0, "also_noisy": 0.0})
        for dropped in ("noisy", "also_noisy"):
            with pytest.raises(structlog.DropEvent):
                proc(None, "info", {"event": dropped})
        # ...and only the named events; the global rate still governs the rest.
        assert _kept(proc, "other") is True

    def test_an_override_of_one_keeps_an_event_the_global_rate_would_drop(self) -> None:
        proc = make_sampling_processor(0.0, rates={"must_always_appear": 1.0})
        assert _kept(proc, "must_always_appear") is True
        with pytest.raises(structlog.DropEvent):
            proc(None, "info", {"event": "anything_else"})

    def test_a_rate_of_one_with_overrides_is_not_a_passthrough(self) -> None:
        """``rate >= 1.0`` must not short-circuit a non-empty override map.

        This is the sharpest form of the defect: a deployment that turned the
        global sampling off and pinned one hot event to 0.0 got a pass-through
        and kept every sample of it. Pre-fix, ``SamplingProcessor(1.0)`` was
        built with no map and kept everything.
        """
        proc = make_sampling_processor(1.0, rates={"noisy": 0.0})
        assert isinstance(proc, SamplingProcessor), "an override map must force a real sampler"
        with pytest.raises(structlog.DropEvent):
            proc(None, "info", {"event": "noisy"})

    def test_passthrough_only_when_there_is_nothing_to_override(self) -> None:
        assert not isinstance(make_sampling_processor(1.0), SamplingProcessor)
        assert not isinstance(make_sampling_processor(1.0, rates={}), SamplingProcessor)
        assert isinstance(make_sampling_processor(1.0, rates={"x": 0.5}), SamplingProcessor)

    def test_overrides_are_clamped_like_the_global_rate(self) -> None:
        proc = make_sampling_processor(0.5, rates={"too_high": 9.0, "too_low": -3.0})
        assert isinstance(proc, SamplingProcessor)
        assert proc._rates == {"too_high": 1.0, "too_low": 0.0}

    def test_never_sample_levels_still_win_over_an_override_of_zero(self) -> None:
        """An override must not be able to suppress a warning or an error."""
        proc = make_sampling_processor(1.0, rates={"audit": 0.0})
        for method in ("warning", "error", "critical", "exception"):
            ed = {"event": "audit"}
            assert proc(None, method, ed) is ed
        # ...and an event carrying a traceback survives too.
        traced = {"event": "audit", "exc_info": (ValueError, ValueError("x"), None)}
        assert proc(None, "info", traced) is traced

    def test_setup_logging_forwards_the_configured_overrides(self) -> None:
        """Pin the wiring at logger.py's call site.

        ``setup_logging`` is not called here on purpose: it clears the root
        logger's handlers and starts a ``QueueListener`` thread
        (logger.py:164-169), which would corrupt logging for the rest of the
        test session. The contract under test is a keyword argument at a call
        site, so it is asserted structurally instead — dropping ``rates=`` there
        reintroduces F-12 exactly as it was found.
        """
        source = inspect.getsource(logger_mod.setup_logging)
        tree = ast.parse(textwrap.dedent(source))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "make_sampling_processor"
        ]
        assert len(calls) == 1, f"expected exactly one make_sampling_processor call, found {len(calls)}"
        keywords = {kw.arg for kw in calls[0].keywords if kw.arg is not None}
        assert "rates" in keywords, f"logger.setup_logging does not forward rates=; it passes {sorted(keywords)}"
        assert "rate" in keywords or len(calls[0].args) == 1, "the global rate is no longer passed either"


def _kept(proc: Processor, event: str = "x") -> bool:
    try:
        proc(None, "info", {"event": event})
        return True
    except structlog.DropEvent:
        return False
