"""The one percentile implementation in the harness tree.

Why this module exists
----------------------
Five separate percentile implementations existed across the harness tree, and
they disagreed. Two of them were outright broken at the boundary; the rest were
individually defensible but mutually inconsistent, so the same sample could be
reported as two different numbers depending on which script produced the
artefact:

    driver_count_io.py   s[min(int(n*q), n-1)]        broken: p95 and p99 both max
    locustfile.py        same formula, 0-100 scale    broken identically
    census.py            s[round(q*(n-1))]            different rank convention
    profile_refresh.py   s[round(q*(n-1))]            different rank convention
    baseline_metrics.py  linear interpolation         different estimator class

The broken formula is the one that produced the unusable "208.47 ms p95" that
this whole measurement programme exists to explain: at n=20,
``int(20 * 0.95) == int(20 * 0.99) == 19``, so the p95 column was a maximum.

The estimator
-------------
Nearest-rank: the value at 1-based rank ``ceil(q * n)``. It is chosen over the
alternatives because

  * it always returns a sample that was actually observed, so a reported
    percentile can always be traced to a real measurement, and
  * it is a one-line definition, which is what makes it auditable at 3am in
    front of a regression.

Linear interpolation was rejected as the shared estimator: it returns values
that frequently are not in the sample at all (``s[9]``/``s[10]`` midpoint), so
a p99 can be quoted that no request ever experienced. ``baseline_metrics.py``
used interpolation; migrating it changes every percentile it had published, and
that change is recorded on the bead that authorised it rather than absorbed
quietly.

Honesty about resolution
------------------------
``nearest_rank`` cannot manufacture resolution it does not have. At n=20,
``ceil(0.99 * 20) == 20``, so p99 IS the maximum - that is arithmetic, not a
bug. Use :func:`unresolvable_quantiles` to find out which quantiles a given
sample size cannot support, and say so in the output rather than quoting a
number that carries no information.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final

__all__ = ["PERCENTILES", "nearest_rank", "unresolvable_quantiles"]

#: The quantiles the harness reports. Fractions, not 0-100.
PERCENTILES: Final[tuple[float, ...]] = (0.5, 0.95, 0.99)


def nearest_rank(samples: Sequence[float], q: float) -> float:
    """Return the nearest-rank percentile of ``samples`` at quantile ``q``.

    ``q`` is a FRACTION in [0, 1]. Callers holding a 0-100 percentage divide
    before calling; that conversion is a unit change, not a second estimator.

    Raises ``ValueError`` on an empty sample rather than returning a sentinel:
    a caller that reports "0.0 ms" for a measurement it never took has produced
    a number that reads like data and is not.
    """
    if not samples:
        raise ValueError("nearest_rank() of an empty sample")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"quantile {q!r} is outside [0, 1]; pass a fraction, not a percentage")
    ordered = sorted(samples)
    rank = min(max(math.ceil(q * len(ordered)), 1), len(ordered))
    return ordered[rank - 1]


def unresolvable_quantiles(n: int, qs: Sequence[float] = PERCENTILES) -> list[float]:
    """The quantiles in ``qs`` that ``n`` samples cannot resolve.

    A quantile is unresolvable when its rank lands on the last sample, i.e.
    when reporting it is reporting the maximum under a different name. At
    n=20 that is p99; at n=1 it is every quantile.
    """
    if n < 1:
        raise ValueError(f"sample size {n} is not a positive count")
    return [q for q in qs if math.ceil(q * n) == n]
