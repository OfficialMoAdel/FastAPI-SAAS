"""The total row in a locust stats CSV must never be counted as an endpoint.

``read_locust_phase`` builds an ``endpoints`` dict from ``_stats.csv`` and
reports ``requests`` as the sum of that dict. locust's CSV ends with a total
row whose ``Name`` is ``Aggregated`` and whose ``Type`` cell is EMPTY.

An earlier version excluded the total by testing ``Type == "Aggregated"``. That
never matched, so the total row was admitted as an endpoint and the sum counted
every request twice. The observed symptom was exact and is worth recording as a
fixture rather than a description: RESULT-20261011-190914 reported 122040 warm
requests where the CSV held 61020, precisely 2x.

The damage was not a wrong number in a table. It propagated: ``_warm_population``
compares the reported count against locust's own post-reset count, the two
disagreed, ``verified`` went False, and the phase report then labelled every
percentile "no ramp request can be PROVEN absent". The population was correct -
the ramp is in the cold phase's own file - but the harness could not demonstrate
that, and published the doubt instead.

So the rule under test is narrow and mechanical: the total row is identified by
NAME, and excluding it by NAME is what makes the reported count equal the sum of
the endpoints. Both spellings are asserted because locust has shipped both.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.benchmarks.run_manifest import read_locust_phase

#: A locust stats CSV shaped like the real one, including the total row with an
#: EMPTY Type cell - the detail the original filter got wrong. Numbers are the
#: ones from RESULT-20261011-190914 so a regression shows the real 2x rather
#: than a synthetic one.
_STATS_ROWS: list[tuple[str, str, str, str]] = [
    # (Type, Name, Request Count, Failure Count)
    ("POST", "/account/login (setup)", "26", "0"),
    ("POST", "/account/refresh", "1372", "0"),
    ("GET", "/health/live", "672", "0"),
    ("GET", "/users/me", "10592", "1"),
    ("GET", "/users/me (readonly)", "47014", "0"),
    ("GET", "count", "667", "0"),
    ("GET", "sessions", "677", "0"),
    # The total row. Type is empty - that is what locust writes.
    ("", "Aggregated", "61020", "1"),
]


def _write_phase(tmp_path: Path, *, aggregated_type: str, aggregated_name: str) -> Path:
    """Write a warm-phase stats + history pair the reader accepts."""
    prefix = tmp_path / "run"
    stats = tmp_path / "run_stats.csv"
    with stats.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Type", "Name", "Request Count", "Failure Count", "Median Response Time"])
        for row in _STATS_ROWS[:-1]:
            writer.writerow([*row, "5"])
        writer.writerow([aggregated_type, aggregated_name, "61020", "1", "5"])

    history = tmp_path / "run_stats_history.csv"
    with history.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Timestamp", "Total Request Count", "User Count"])
        writer.writerow(["1000.0", "64", "50"])
        writer.writerow(["1060.0", "61084", "50"])
    return prefix


@pytest.mark.parametrize(
    ("aggregated_type", "aggregated_name", "label"),
    [
        ("", "Aggregated", "locust's actual layout: empty Type, name is the marker"),
        ("Aggregated", "Aggregated", "defensive: some versions also set Type"),
    ],
)
def test_the_total_row_is_never_an_endpoint(
    tmp_path: Path, aggregated_type: str, aggregated_name: str, label: str
) -> None:
    """``requests`` must equal the sum of the real endpoints, not double it."""
    prefix = _write_phase(
        tmp_path, aggregated_type=aggregated_type, aggregated_name=aggregated_name
    )

    phase = read_locust_phase(prefix, phase="warm")

    assert "Aggregated" not in phase["endpoints"], (
        f"the total row was admitted as an endpoint ({label}); summing endpoints "
        f"then counts every request twice"
    )
    assert phase["requests"] == 61020, (
        f"requests={phase['requests']}, expected 61020. The total row is the sum "
        f"of the rows above it, so admitting it doubles every count - this is the "
        f"122040-instead-of-61020 defect."
    )
    assert len(phase["endpoints"]) == 7, (
        f"expected the 7 real endpoints, got {len(phase['endpoints'])}: "
        f"{sorted(phase['endpoints'])}"
    )


def test_failures_are_not_doubled_either(tmp_path: Path) -> None:
    """The same row feeds the failure sum, so the defect has a second surface."""
    prefix = _write_phase(tmp_path, aggregated_type="", aggregated_name="Aggregated")

    phase = read_locust_phase(prefix, phase="warm")

    # One real failure (on /users/me) plus the total row's 1. Counting the total
    # would report 2.
    assert phase["failures"] == 1, (
        f"failures={phase['failures']}, expected 1 from the real rows alone"
    )


def test_the_sum_of_endpoints_equals_the_reported_count(tmp_path: Path) -> None:
    """The invariant the phase report's whole verification rests on.

    ``_warm_population`` compares this count against locust's own post-reset
    total and sets ``verified`` from the comparison. If the two can disagree for
    a reason internal to this reader, ``verified`` is measuring the reader's
    bug rather than the run's population - which is exactly what happened.
    """
    prefix = _write_phase(tmp_path, aggregated_type="", aggregated_name="Aggregated")

    phase = read_locust_phase(prefix, phase="warm")

    summed = sum(entry["requests"] for entry in phase["endpoints"].values())
    assert phase["requests"] == summed, (
        f"reported {phase['requests']} but endpoints sum to {summed}"
    )
    # And it equals locust's own post-reset total (61084 - 64), which is what
    # makes verified True rather than merely plausible.
    assert phase["requests"] == 61084 - 64
