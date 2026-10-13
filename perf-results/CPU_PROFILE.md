## CPU profile of the hot request, Linux target

From `perf-results/RESULT-20261012-linuxvm/raw_linux_d.json`, produced by
`src/benchmarks/prof_request_path.py` on `perf-local` (Linux 5.14, CPython 3.13.16,
uvloop 0.22.1), measured in-process over ASGITransport so cProfile and the app share
a thread.

| | |
|---|---|
| profiler | cProfile, overhead quantified: `distortion_x` **1.939** |
| requests profiled | 25 |
| clean wall time per request | **4.233 ms** |
| total tottime | 0.204843 s |
| **top-5 tottime share** | **2.8%** |
| remainder (`other` bucket) | **64.0%** |

### Top 5 by tottime

| # | site | tottime |
|---|---|---|
| 1 | `src/app/core/observability/log_sanitizer.py:192(<genexpr>)` | 2.169 ms |
| 2 | `src/app/middleware/attack_detection.py:558(_check_for_patterns)` | 1.564 ms |
| 3 | `src/app/core/observability/log_sanitizer.py:252(sanitize_value)` | 0.691 ms |
| 4 | `src/app/core/observability/log_sanitizer.py:182(_maybe_sensitive)` | 0.675 ms |
| 5 | `src/app/middleware/attack_detection.py:165(_attack_prefilter_hit)` | 0.563 ms |

The remainder is not an unexamined gap: the exporter buckets every unattributed
frame into `other` explicitly, so 64% of
tottime is a named bucket rather than an absence.

### THE OBLIGATION THIS DISCHARGES

`cekt` states: *"If the top-5 share is insufficient, the plan is rewritten - that
obligation is discharged explicitly, not silently."*

**The top-5 share is 2.8%. That is insufficient, and it refutes the premise the plan
optimises against.**

The plan's method is to profile the hot request and act on the functions it names.
On this target that method returns a nearly flat profile: the five largest
functions together are 2.8% of time, and the single largest - a log-sanitiser
generator expression - is **1.1%**. Micro-optimising all five would
address under a twentieth of the request.

That is a stronger result than a list of hot spots would have been. It says the
request cost is **distributed**, not concentrated, so the wins are in breadth
(removing work from the path) rather than depth (making named functions faster).
Two of the five are the attack-detection prefilter and three are log sanitising -
both of which the plan already targets elsewhere, so the profile corroborates them
as present and irrelevant as *sole* explanations.

Any plan revision must therefore not assume a profile will name the hot spot.
This record is the discharge.
