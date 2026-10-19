"""FastAPI_saas-a7ce: the no-SDK tracing path allocates NO span per call.

Three production docstrings described the dead tracing path as "every call
allocates a ``NonRecordingSpan`` ... and exports to nobody":

* ``src/app/core/observability/tracing.py`` (``trace_span``)
* ``src/benchmarks/loop_occupancy.py`` -- module docstring AND
  ``SPAN_ARM_LABELS``, the second of which is written into the measurement
  artefact as the arm's own description
* ``scripts/span_cost.py`` -- module docstring

That claim is FALSE on the installed dependency. Measured in the repository
venv on ``opentelemetry-api`` 1.39.1 (the declared pin is ``>=1.20.0``,
pyproject.toml:56), with no SDK installed::

    trace.get_tracer("fastapi-saas") -> ProxyTracer
    ProxyTracer._tracer              -> NoOpTracer
    NoOpTracer.start_as_current_span -> yield INVALID_SPAN

``INVALID_SPAN`` is a module-level SINGLETON, built once when
``opentelemetry.trace`` was imported, so entering the dead path N times
constructs ZERO spans and hands back the identical object every time. Two
consequences, and this oracle pins both.

1. The dead work is the CONTEXTMANAGER protocol, not an allocation. A reader
   who believes the old sentence sizes a regression against "one
   NonRecordingSpan per DB request" and misjudges the fix by an order of
   magnitude in the wrong direction.

2. The claim was never verifiable from this repository, because it is a
   property of whichever ``opentelemetry-api`` is installed rather than of any
   code here, and a version bump can change it in either direction with no
   source change to notice. So the behaviour is pinned BY TEST rather than
   trusted to prose: if a future release starts allocating, the constructor
   count below stops being zero and fails loudly, instead of the docstrings
   quietly becoming true-by-accident-of-drift in one direction only.

LIMITS OF THE SOURCE-TEXT TESTS, STATED PLAINLY
The ``FALSE_CLAIMS`` table is an exact-substring ratchet: it fails on the four
sentences quoted verbatim from the defect report, and a reworded-but-still-
false claim would slip past it. That is a deliberate trade, because the
alternative -- a regex over "any allocating verb near the word Span" -- fails
on correct prose, and a docstring ratchet that cries wolf is worse than none.
What keeps the docstrings honest is the pair of facts above, not this table:
the behaviour tests are the checkable part, and if they ever fail the prose
must be re-read rather than re-worded around the failure.

The S-3 guard in ``tracing.py`` is out of scope and untouched. Whether the
tracer is ENTERED is stable across the declared pin and is already pinned by
``tests/unit/core/observability/test_trace_span_noop.py``; the claim repaired
here is narrower and is about what the entered path ALLOCATES.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from opentelemetry import trace

from src.app.core.observability import tracing

REPO_ROOT = Path(__file__).resolve().parents[4]

#: Each entry is a sentence that was false when this bead was filed, quoted
#: VERBATIM from the file that carried it, with the path that carried it. A
#: reviewer reads the table to see what was removed and why it was wrong; a
#: stale entry (the sentence still present) fails the test.
FALSE_CLAIMS: tuple[tuple[str, str], ...] = (
    (
        "src/app/core/observability/tracing.py",
        "every call allocates a ``NonRecordingSpan``",
    ),
    (
        "src/benchmarks/loop_occupancy.py",
        "build a ``NonRecordingSpan`` and discard it",
    ),
    (
        "src/benchmarks/loop_occupancy.py",
        "tracing builds a ProxyTracer NonRecordingSpan",
    ),
    (
        "scripts/span_cost.py",
        "constructs and enters a NonRecordingSpan",
    ),
)

#: A declared version must look like one. Asserted as a SHAPE so the check does
#: not have to restate whichever version happens to be installed -- the whole
#: point of the provenance assertion is that a reader can go and re-verify it.
VERSION_PATTERN = re.compile(r"\b\d+\.\d+")

#: The distribution whose behaviour is being described. The repository pins a
#: FLOOR (>=1.20.0) rather than a ceiling, so "what does the dead path do" is
#: only ever a statement about the version that happens to be installed.
API_DISTRIBUTION = "opentelemetry-api"

#: Enough repeats that a per-call allocation would be obvious; one is enough to
#: fail, and the pair of tests differ only in this number.
CALL_COUNTS = (1, 5)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


@contextmanager
def _counting_span_constructions() -> Iterator[list[object]]:
    """Count every ``NonRecordingSpan`` constructed inside the block.

    ``INVALID_SPAN`` is built at import time, so it is already constructed
    before this runs; what is counted is only what the probed path creates.
    That is the exact quantity the defect claimed was "once per database
    session, on every request".
    """
    built: list[object] = []
    original = trace.NonRecordingSpan.__init__

    def counting(self: trace.NonRecordingSpan, *args: Any, **kwargs: Any) -> None:
        built.append(self)
        original(self, *args, **kwargs)

    with patch.object(trace.NonRecordingSpan, "__init__", counting):
        yield built


def _dead_path_spans(count: int) -> list[trace.Span]:
    """Run ``trace_span`` ``count`` times the way production runs it.

    With ``_SDK_INSTALLED`` forced True, ``trace_span`` takes the branch S-3
    removed: it calls the module ``ProxyTracer``, whose inner tracer is a
    ``NoOpTracer`` because no SDK is installed. That is the "dead path" every
    measurement in this repository prices.
    """
    spans: list[trace.Span] = []
    with patch.object(tracing, "_SDK_INSTALLED", True):
        for index in range(count):
            with tracing.trace_span(f"a7ce_dead_{index}") as span:
                spans.append(span)
    return spans


def _source(relative: str) -> str:
    path = REPO_ROOT / relative
    assert path.is_file(), f"{relative} does not exist; the claim it carried moved or was deleted"
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# (1) the premises. An allocation count of zero means nothing if what is being
#     counted is not the path the docstring describes.
# --------------------------------------------------------------------------


class TestTheNoSDKTopologyIsWhatIsBeingMeasured:
    def test_no_sdk_is_installed_so_the_dead_path_is_reachable(self) -> None:
        assert tracing._SDK_INSTALLED is False, (
            "with an SDK installed this whole oracle is measuring the wrong path; "
            "the dead path only exists when there is no SDK"
        )

    def test_the_module_tracer_is_a_proxy_over_a_noop_tracer(self) -> None:
        """The topology the claim rests on, asserted rather than assumed.

        ``ProxyTracer`` delegating to ``NoOpTracer`` is the whole reason the
        dead path is cheap. If a future release changes that wiring this test
        fails, which is the point: the docstrings' claim would be describing a
        path that no longer exists.
        """
        assert isinstance(tracing.tracer, trace.ProxyTracer)
        assert isinstance(tracing.tracer._tracer, trace.NoOpTracer)  # type: ignore[attr-defined]

    def test_the_yielded_span_is_a_non_recording_span_singleton(self) -> None:
        """``trace.INVALID_SPAN`` is what is handed back, and it is the module
        singleton -- not a fresh object built per call. This is the fact that
        makes the constructor count zero rather than one."""
        assert isinstance(trace.INVALID_SPAN, trace.NonRecordingSpan)
        with tracing.trace_span("a7ce_guarded") as span:
            assert span is trace.INVALID_SPAN


# --------------------------------------------------------------------------
# (2) the measurement
# --------------------------------------------------------------------------


class TestTheDeadPathAllocatesNothing:
    @pytest.mark.parametrize("count", CALL_COUNTS)
    def test_the_dead_path_constructs_no_span(self, count: int) -> None:
        with _counting_span_constructions() as built:
            _dead_path_spans(count)
        assert built == [], (
            f"{len(built)} NonRecordingSpan(s) were constructed by {count} no-op "
            "tracing call(s). If a new opentelemetry-api release started "
            "allocating, the docstrings that describe this path are now WRONG "
            "and must be corrected -- do not adjust this assertion to match"
        )

    @pytest.mark.parametrize("count", CALL_COUNTS)
    def test_every_call_yields_the_same_shared_object(self, count: int) -> None:
        """Identity, not equality.

        ``count`` calls must hand back the identical singleton. A per-call
        allocation would produce ``count`` distinct objects, which is the
        defect stated in a form that needs no constructor spy to observe.
        """
        spans = _dead_path_spans(count)
        assert len(spans) == count
        assert all(span is trace.INVALID_SPAN for span in spans)
        assert len({id(span) for span in spans}) == 1

    def test_the_guarded_path_constructs_no_span_either(self) -> None:
        """Production is on the guarded path, so it is the one whose cost
        matters; the dead path above only exists to price what S-3 removed."""
        with _counting_span_constructions() as built:
            with tracing.trace_span("a7ce_guarded_cost"):
                pass
        assert built == []


# --------------------------------------------------------------------------
# (3) the prose. Weak by nature; kept honest by the two classes above.
# --------------------------------------------------------------------------


class TestNoProseStillAssertsTheFalseClaim:
    @pytest.mark.parametrize(
        ("relative", "false_sentence"),
        FALSE_CLAIMS,
        ids=[f"{path}::{fragment[:40]}" for path, fragment in FALSE_CLAIMS],
    )
    def test_the_false_sentence_is_gone(self, relative: str, false_sentence: str) -> None:
        assert false_sentence not in _source(relative), (
            f"{relative} still asserts that the no-SDK tracing path allocates a "
            "NonRecordingSpan per call. On the installed opentelemetry-api it "
            "yields the module-level INVALID_SPAN singleton and allocates "
            "nothing; correct the sentence rather than deleting this test"
        )

    def test_the_trace_span_docstring_says_whose_behaviour_this_is(self) -> None:
        """Provenance, so the corrected claim can be re-checked by a reader.

        ``pyproject.toml`` declares ``opentelemetry-api>=1.20.0`` -- a FLOOR,
        not a pin -- so "what the dead path does" is only ever a statement about
        the installed release. A docstring that states the fact without saying
        whose fact it is asks the next reader to trust a repository that cannot
        possibly know.
        """
        doc = tracing.trace_span.__doc__
        assert doc is not None, "trace_span lost its docstring"
        assert API_DISTRIBUTION in doc, (
            f"the docstring does not name {API_DISTRIBUTION}, so the behaviour it "
            "describes has no stated owner and cannot be re-verified"
        )
        assert VERSION_PATTERN.search(doc), (
            "the docstring names no version, so 'checked against' is unstated and "
            "a future dependency bump has nothing to contradict"
        )

    def test_the_artefact_arm_label_does_not_describe_a_span_being_built(self) -> None:
        """``SPAN_ARM_LABELS`` is written into the measurement artefact as the
        arm's own description, so a false sentence there becomes a false
        sentence in a stored result. This file already states the opposite
        truth in ``probe_span_path``; the two must not contradict each other."""
        labels = _source("src/benchmarks/loop_occupancy.py")
        probe_note = re.search(r'"not a span allocation\."', labels)
        assert probe_note is not None, (
            "the probe note that records what the delta actually prices has been "
            "removed; this oracle's premise about the arm label is unverifiable"
        )
