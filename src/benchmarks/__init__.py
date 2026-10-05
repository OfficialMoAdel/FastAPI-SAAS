"""Performance-measurement harnesses (operator-run, not imported by the app).

These scripts boot the real ASGI app against the real Postgres and Redis and
drive the measured endpoints in-process. They are NOT part of the request path
and nothing under `src.app` imports them.

Tracked here rather than left in a scratch `reports/` directory because the
performance programme's standing rule is that a measurement must be
reproducible from the repository: a harness that only exists on one machine's
disk cannot be cited as evidence by anyone else.

Run from the repository root:

    uv run python src/benchmarks/driver_count_io.py
    uv run python src/benchmarks/census.py
"""
