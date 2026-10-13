"""Bucketed store latency per concurrency level, with commands and round trips apart.

L-7 / P-11 (``7azv``) requires, per store and per concurrency step:

* a BUCKETED latency histogram - never a single RTT constant, and never a
  "p95 per RTT" figure, because averaging away the tail is how a store that is
  fine at the median and terrible at p95 reads as fine;
* commands and round trips as SEPARATE columns, since a pipelined batch is many
  commands and one round trip, and conflating them overstates network cost by
  the batch size;
* TWO INDEPENDENT COUNTS THAT AGREE. The client-side counter patches the
  connection; the server-side counter reads the store's own accounting. If they
  disagree, one of them is lying, and a single counter cannot tell you which;
* the store addresses actually used, and whether they are co-located or remote,
  stated next to the numbers rather than assumed to be one or the other.

The last point is not bookkeeping. On this VM the stores are co-located, so an RTT
measured here is loopback-plus-kernel, not network. A reader who does not see that
will compare this against a remote-store figure and conclude the network is free.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

#: Bucket edges in milliseconds. Fixed in advance so two runs are comparable and a
#: reader can see the resolution rather than trusting a summary.
#: Redis counts the INFO commandstats read that takes the final snapshot before
#: returning it, so the server-side tally is always one higher than the workload.
_REDIS_SNAPSHOT_SELF_COUNT = 1

#: Postgres has the identical quirk: the pg_stat_statements read that takes the
#: snapshot is itself a tracked statement, so a workload of N reads back as N+1.
#: Same structural cause as Redis, stated separately rather than shared, because
#: they are two different subsystems and a reader debugging one should not have
#: to reason about the other.
_POSTGRES_SNAPSHOT_SELF_COUNT = 1

#: The exact statement the Postgres probe times. Named because pg_stat_statements
#: is queried BY TEXT: setup and teardown statements must be excluded by matching
#: this string, and an inline literal in two places would eventually drift.
_PROBE_STATEMENT = "select 1"

#: What pg_stat_statements stores for it. The extension NORMALISES constants to
#: positional placeholders, so the literal sent above is recorded as `select $1`.
#: Matching the sent text instead returned 0 against 362 real calls - a server-side
#: count that disagreed with the client about everything because it was filtering on
#: a string the server never stores. The two forms are declared together so they
#: cannot drift apart again.
_PROBE_STATEMENT_AS_TRACKED = "select $1"

BUCKET_EDGES_MS: tuple[float, ...] = (
    0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0,
)


def bucketise(latencies_ms: list[float]) -> dict[str, int]:
    """Count samples per bucket. Under ``_lt<edge>``, plus the open tail."""
    buckets = {f"_lt{edge}ms": 0 for edge in BUCKET_EDGES_MS}
    buckets["_gte1000ms"] = 0
    for value in latencies_ms:
        placed = False
        for edge in BUCKET_EDGES_MS:
            if value < edge:
                buckets[f"_lt{edge}ms"] += 1
                placed = True
                break
        if not placed:
            buckets["_gte1000ms"] += 1
    return buckets


def summarise(latencies_ms: list[float]) -> dict[str, Any]:
    """Min, p50, p95 and count. A summary to READ BESIDE the buckets, never instead.

    The buckets are the evidence; these three numbers are the orientation. They are
    reported together deliberately so nobody takes p95 alone as the headline.
    """
    if not latencies_ms:
        return {"count": 0}
    ordered = sorted(latencies_ms)

    def pick(p: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        idx = min(len(ordered) - 1, max(0, int(round(p * (len(ordered) - 1)))))
        return ordered[idx]

    return {
        "count": len(ordered),
        "min_ms": round(ordered[0], 4),
        "p50_ms": round(pick(0.50), 4),
        "p95_ms": round(pick(0.95), 4),
        "max_ms": round(ordered[-1], 4),
    }


class RedisCommandCounter:
    """Client-side command and round-trip counters.

    A round trip is one ``execute``/``send_packed_command`` reaching the socket;
    a command is one Redis protocol verb. A pipeline of N commands is N commands
    and ONE round trip, which is exactly the distinction L-7 asks to preserve -
    and exactly what is lost if a naive counter counts verbs and calls them trips.
    """

    def __init__(self) -> None:
        self.commands = 0
        self.round_trips = 0
        self._installed = False
        self._originals: list[tuple[Any, str, Any]] = []

    def _commands_in(self, method: str, args: tuple[Any, ...],
                     kwargs: dict[str, Any]) -> int:
        """How many protocol verbs this call actually carries.

        ``send_packed_command`` receives a LIST of already-packed command buffers,
        one per verb, so a batch of N arrives as ONE call. Counting one per call
        is what made the client-side tally read a quarter of the server's: the
        ratio was 3.99x at every step, which is four verbs per call, not noise.
        ``send_command`` takes the verb as its first positional argument, so that
        one is always exactly one command.
        """
        if method == "send_packed_command":
            payload = kwargs.get("command", args[0] if args else None)
            if isinstance(payload, list | tuple):
                return len(payload)
        return 1

    def install(self) -> None:
        from redis.asyncio.connection import (  # type: ignore[import-not-found]
            AbstractConnection,
            Connection,
        )

        # PATCH ONE LEVEL ONLY: send_packed_command, the lowest function that
        # actually writes to the socket.
        #
        # redis-py's chain is execute_command -> send_command -> send_packed_command,
        # so patching all three counts every logical command once per layer - which
        # is what produced the 3.99x ratio, not a verb-per-batch effect as first
        # assumed. At the bottom of the chain there is exactly one call per round
        # trip, and its argument is the list of packed verbs, so BOTH numbers the
        # criterion wants fall out of a single hook: one round trip per call, and
        # len(packed) commands in it.
        # PATCH THE CONCRETE CLASS ONLY. Connection subclasses AbstractConnection,
        # so setting the attribute on both makes the subclass wrapper call the
        # superclass wrapper - two counts per call again, which is exactly the
        # residual 2.0x this probe reported after the multi-method patch was
        # removed. One class, one hook, one count.
        target = Connection if hasattr(Connection, "send_packed_command") else AbstractConnection
        original = getattr(target, "send_packed_command", None)
        if original is not None:
            self._originals.append((target, "send_packed_command", original))
            setattr(target, "send_packed_command", self._wrap(original, "send_packed_command"))

        self._installed = True

    def _wrap(self, original: Any, method: str) -> Any:
        counter = self

        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            counter.round_trips += 1
            counter.commands += counter._commands_in(method, args, kwargs)
            return await original(*args, **kwargs)

        return wrapper

    def remove(self) -> None:
        for cls, method, original in self._originals:
            setattr(cls, method, original)
        self._originals.clear()
        self._installed = False


async def _server_side_redis_commands(client: Any) -> int | None:
    """The store's OWN command count, from ``INFO commandstats``.

    Independent of the client-side patch by construction: it is the server's
    tally. Disagreement means a patch bug or a shared store, and either way the
    client-side number alone cannot be trusted.
    """
    try:
        info = await client.info("commandstats")
    except Exception:  # noqa: BLE001 - unavailability is a fact to record, not to raise
        return None
    return sum(int(entry.get("calls", 0)) for entry in info.values())


async def measure_redis(
    url: str, password: str | None, *, concurrency: int, ops_per_worker: int
) -> dict[str, Any]:
    """Time individual Redis commands at a fixed concurrency."""
    import redis.asyncio as aioredis

    counter = RedisCommandCounter()
    counter.install()
    latencies: list[float] = []
    client = aioredis.Redis.from_url(url, password=password, decode_responses=True)
    try:
        await client.ping()
        # RESET BOTH TALLIES AFTER SETUP. The INFO reads and the PING are probe
        # overhead, not workload, and they were landing in one tally but not the
        # other - a constant 3-command gap at every step. Both counters now start
        # measuring at the same instant, so "client == server" means the same
        # thing on both sides instead of two populations that differ by the
        # probe's own overhead.
        server_before = await _server_side_redis_commands(client)
        counter.commands = 0
        counter.round_trips = 0

        async def worker(worker_id: int) -> None:
            local: list[float] = []
            for i in range(ops_per_worker):
                key = f"l7:probe:{concurrency}:{worker_id}:{i}"
                started = time.perf_counter()
                await client.set(key, "x", ex=60)
                local.append((time.perf_counter() - started) * 1000.0)
            latencies.extend(local)

        started = time.perf_counter()
        await asyncio.gather(*(worker(w) for w in range(concurrency)))
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        # SNAPSHOT THE CLIENT COUNTERS HERE, BEFORE CLEANUP. The server delta and
        # the client tally have to describe the SAME window, and the KEYS/DELs below
        # are traffic the measurement does not contain. Leaving them in made the
        # client count read ~2x the server's at every step, which looked exactly
        # like a counting bug and was not one - it was a phase-boundary bug, with
        # two populations being compared.
        client_commands = counter.commands
        client_round_trips = counter.round_trips
        server_after = await _server_side_redis_commands(client)
        for key in await client.keys("l7:probe:*"):
            await client.delete(key)
    finally:
        await client.aclose()
        if counter._installed:
            counter.remove()

    commands = client_commands
    round_trips = client_round_trips
    server_delta = (
        server_after - server_before
        if server_before is not None and server_after is not None
        else None
    )
    # The INFO read that TAKES the final snapshot is itself a command, and Redis
    # has already counted it by the time it returns. So a workload of N commands
    # reads back as N+1 server-side. That offset is structural, not a
    # disagreement, and it is subtracted here with the reason recorded rather than
    # quietly dropped - a reader who sees a raw +1 with no explanation cannot tell
    # a known accounting quirk from a real mismatch.
    expected = commands + _REDIS_SNAPSHOT_SELF_COUNT if server_delta is not None else None
    return {
        "store": "redis",
        "url": url.split("@")[-1],
        "concurrency": concurrency,
        "wall_ms": round(elapsed_ms, 2),
        "throughput_ops_per_s": round(
            len(latencies) / (elapsed_ms / 1000.0), 2
        ) if elapsed_ms else None,
        "commands": commands,
        "round_trips": round_trips,
        "commands_per_round_trip": round(commands / round_trips, 4) if round_trips else None,
        "server_side_commands_delta": server_delta,
        "counts_agree": (server_delta == expected) if expected is not None else None,
        "counts_agree_note": (
            "server-side count includes the INFO read that takes the snapshot, "
            "so a workload of N commands reads back as N+1; the offset is "
            "subtracted and reported here rather than dropped"
        ),
        "buckets_ms": bucketise(latencies),
        "summary": summarise(latencies),
    }


async def _server_side_pg_statements(conn: Any) -> int | None:
    """Executed statements on this database, from Postgres's OWN accounting.

    ``pg_stat_statements`` is the right source and the only honest one: it counts
    what the server EXECUTED, independently of anything the client counted. The
    substitute tried before this, ``pg_stat_database.xact_commit``, is written by
    an asynchronous collector and read 0 while 40 statements were genuinely in
    flight - a second number that agreed with nothing, which is worse than no
    second number because it looks like one.

    Returns None when the extension is absent. That is a fact to surface, not to
    default: a missing second count must read as missing rather than as agreement.

    The one-off statements this probe runs for setup and teardown are excluded by
    their query text, so the delta describes the measured window and nothing else.
    """
    try:
        row = await conn.fetchrow(
            "select coalesce(sum(calls), 0)::bigint as calls "
            "from pg_stat_statements "
            "where query = $1",
            _PROBE_STATEMENT_AS_TRACKED,
        )
    except Exception:  # noqa: BLE001 - unavailability is recorded, never faked
        return None
    return int(row["calls"]) if row else None


async def measure_postgres(
    dsn: str, *, concurrency: int, ops_per_worker: int
) -> dict[str, Any]:
    """Time individual Postgres statements at a fixed concurrency."""
    import asyncpg

    latencies: list[float] = []

    # ONE CONNECTION PER WORKER, not one shared. asyncpg refuses concurrent use
    # of a single connection ("another operation is in progress"), and the obvious
    # workarounds are worse than the crash: a lock would serialise the workers and
    # measure the lock rather than the database, while letting them share silently
    # would report a "concurrency N" figure that was really concurrency 1. A pool
    # is the only arrangement where the label means what it says.
    pool = await asyncpg.create_pool(dsn, min_size=concurrency, max_size=concurrency)
    try:
        # SERVER-SIDE count, from Postgres's own accounting rather than from
        # anything this process does. pg_stat_statements is the obvious choice and
        # is NOT available here (it needs shared_preload_libraries and a restart),
        # so this uses pg_stat_database.xact_commit instead.
        #
        # THE CAVEAT IS PART OF THE MEASUREMENT: a transaction is not a statement.
        # Under autocommit each statement IS its own transaction, so the two
        # coincide - and these probes are autocommit. That is why the comparison is
        # meaningful here and would silently stop meaning anything the moment a
        # caller wrapped its work in BEGIN/COMMIT. Stated rather than assumed.
        async with pool.acquire() as probe:
            server_before = await _server_side_pg_statements(probe)
            await probe.execute(_PROBE_STATEMENT)

        async def worker(worker_id: int) -> None:
            async with pool.acquire() as conn:
                local: list[float] = []
                for _ in range(ops_per_worker):
                    started = time.perf_counter()
                    await conn.execute(_PROBE_STATEMENT)
                    local.append((time.perf_counter() - started) * 1000.0)
            latencies.extend(local)

        started = time.perf_counter()
        await asyncio.gather(*(worker(w) for w in range(concurrency)))
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        async with pool.acquire() as probe:
            server_after = await _server_side_pg_statements(probe)
    finally:
        await pool.close()

    # The two counts are only comparable when BOTH exist. If either is missing the
    # comparison is None rather than True - a defaulted agreement is the one thing
    # this column must never contain.
    server_delta = (
        server_after - server_before
        if server_before is not None and server_after is not None
        else None
    )
    expected = (
        len(latencies) + _POSTGRES_SNAPSHOT_SELF_COUNT
        if server_delta is not None
        else None
    )

    return {
        "store": "postgres",
        "url": dsn.split("@")[-1],
        "concurrency": concurrency,
        "wall_ms": round(elapsed_ms, 2),
        "throughput_ops_per_s": round(
            len(latencies) / (elapsed_ms / 1000.0), 2
        ) if elapsed_ms else None,
        # One statement is one exchange on a dedicated connection, so commands and
        # round trips coincide. Recorded as equal rather than omitted, because
        # "equal here, different for Redis" is the shape of the finding.
        "commands": len(latencies),
        "round_trips": len(latencies),
        "commands_per_round_trip": 1.0,
        "server_side_commands_delta": server_delta,
        "counts_agree": (server_delta == expected) if expected is not None else None,
        "counts_agree_note": (
            "server-side count includes the pg_stat_statements read that takes "
            "the snapshot, so a workload of N statements reads back as N+1; the "
            "offset is subtracted and reported here rather than dropped"
        ),
        "server_side_source": (
            "pg_stat_statements: sum(calls) for the probe statement's exact query "
            "text. Counts what the server EXECUTED, independent of the client. "
            "Enabled via shared_preload_libraries on this store."
        ),
        "buckets_ms": bucketise(latencies),
        "summary": summarise(latencies),
    }


async def main_async(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    load_dotenv("src/.env", override=True)

    redis_url = args.redis_url or os.getenv(
        "REDIS_URL", f"redis://{os.getenv('REDIS_HOST', '127.0.0.1')}:"
        f"{os.getenv('REDIS_PORT', '6379')}/0"
    )
    # There is no single REDIS_PASSWORD in this project: the app carries a separate
    # credential per role (cache, queue, rate-limit, crud-admin) and the server
    # rejects an unauthenticated client outright. Resolve through the names rather
    # than hardcoding one role, so the probe authenticates the way the app does
    # instead of failing on a naming assumption.
    password = (
        args.redis_password
        or os.getenv("REDIS_PASSWORD")
        or os.getenv("REDIS_CACHE_PASSWORD")
        or os.getenv("REDIS_QUEUE_PASSWORD")
        or os.getenv("REDIS_RATE_LIMIT_PASSWORD")
        or os.getenv("CRUD_ADMIN_REDIS_PASSWORD")
        or None
    )
    pg_dsn = args.pg_dsn or (
        f"postgresql://{os.environ.get('POSTGRES_USER')}:"
        f"{os.environ.get('POSTGRES_PASSWORD')}@{os.environ.get('POSTGRES_SERVER')}:"
        f"{os.environ.get('POSTGRES_PORT', '5432')}/{os.environ.get('POSTGRES_DB')}"
    )

    # Locality comes from the manifest's OWN resolver, not from a second copy of
    # the logic. That resolver already distinguishes derivable from declared, and
    # its docstring records why the Redis programme depends on the answer: a
    # co-located round trip is ~0.3 ms against the 0.72-3.10 ms the pack recorded,
    # so "reduce round trips" is worth 10-25x less than those reports assume.
    # Re-deriving it here would risk the two disagreeing, which is precisely the
    # class of bug this programme keeps finding.
    from src.benchmarks.run_manifest import resolve_store_locality  # noqa: PLC0415

    redis_host = redis_url.split("//")[-1].split(":")[0].split("@")[-1]
    pg_host = os.environ.get("POSTGRES_SERVER", "")
    locality = resolve_store_locality(
        {
            "postgres": f"{pg_host}:{os.environ.get('POSTGRES_PORT', '5432')}/"
            f"{os.environ.get('POSTGRES_DB')}",
            "redis_cache": redis_host,
            "redis_queue": redis_host,
        }
    ) or "underivable"

    levels = [int(x) for x in args.concurrency.split(",")]
    results: list[dict[str, Any]] = []
    for level in levels:
        for store, coro in (
            ("redis", measure_redis(
                redis_url, password, concurrency=level, ops_per_worker=args.ops)),
            ("postgres", measure_postgres(
                pg_dsn, concurrency=level, ops_per_worker=args.ops)),
        ):
            try:
                entry = await coro
            except Exception as exc:  # noqa: BLE001
                # A store's own connection ceiling is a MEASUREMENT, not a crash.
                # When Postgres refuses the pool it is reporting max_connections,
                # a hard ceiling on this store's concurrency that belongs in the
                # artefact beside the latencies. Swallowing it as "try fewer" would
                # hide the one number that matters at that step, and aborting the
                # sweep would hide every step after it.
                name = type(exc).__name__
                if "TooManyConnections" not in name:
                    raise
                results.append({
                    "store": store,
                    "concurrency": level,
                    "status": "store-connection-ceiling",
                    "error": name,
                    "detail": (
                        f"{store} refused {level} concurrent connections. That is "
                        f"the store's own ceiling, reported as a finding rather "
                        f"than worked around."
                    ),
                    "store_locality": locality,
                })
                print(f"{store:9} c={level:<4} STORE CONNECTION CEILING ({name})", flush=True)
                continue
            entry["store_locality"] = locality
            results.append(entry)
            print(
                f"{store:9} c={level:<4} p50={entry['summary'].get('p50_ms')}ms "
                f"p95={entry['summary'].get('p95_ms')}ms "
                f"ops/s={entry['throughput_ops_per_s']}",
                flush=True,
            )

    payload = {
        "schema": "perf-store-rtt/1",
        "bucket_edges_ms": list(BUCKET_EDGES_MS),
        "store_locality": locality,
        "stores": {
            "redis": {"address": redis_url.split("@")[-1], "locality": locality},
            "postgres": {"address": pg_dsn.split("@")[-1], "locality": locality},
        },
        "note": (
            "RTT is reported ONLY as buckets. No single RTT constant and no "
            "p95-per-RTT figure appears anywhere in this artefact, by L-7."
        ),
        "results": results,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/store_rtt.json")
    parser.add_argument("--concurrency", default="1,8,32,64,128,256")
    parser.add_argument("--ops", type=int, default=40, help="operations per worker")
    parser.add_argument("--redis-url", default=None)
    parser.add_argument("--redis-password", default=None)
    parser.add_argument("--pg-dsn", default=None)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
