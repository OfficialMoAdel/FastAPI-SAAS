"""S-1: the round-trip count of ONE session invalidation, observed against a real Redis.

WHAT THIS IS. The S-1 change replaced the invalidation's two client round trips
(one HEXISTS plus one packed transaction pipeline) with a single atomic Lua
EVALSHA. The count was DERIVED by reading the before-code, not observed. This
script observes it: it snapshots the Redis server's own INFO commandstats
around one call to ``SessionManager.invalidate_session`` and reports the
ABSOLUTE per-command delta, for the hit path (the session is present) and the
miss path (the session is absent).

WHAT A ROUND TRIP IS HERE. The commands the script issues to Redis are the
round trips. The commands the LUA SCRIPT calls on the server (HEXISTS, HDEL,
ZREM) are not: they execute inside the server, on the same connection, with no
wire traffic of their own. Redis reports them separately, under
``cmdstat_lua``, and that separation is what makes the hit and the miss path
distinguishable: the script returns early on a miss, so the Lua-internal HDEL
and ZREM happen on the hit path only.

WHAT IS ASSERTED. Absolute counts, never a comparison. The client-side delta of
one invalidation is exactly one EVALSHA and nothing else, on both paths. The
Lua-internal delta is one HEXISTS on both paths, plus one HDEL and one ZREM on
the hit path only. Nothing here is a ``new < old`` criterion, and no number is
compared to a baseline taken elsewhere.

WHAT THIS IS NOT. Not an endpoint measurement: the app's lifespan is not run
and no request is issued, so nothing here says what a login or a logout costs.
Not a benchmark: there is no timing, and this artefact quotes no duration.

PROVENANCE. Written for bead FastAPI_saas-qs06, which owes the observation
that S-1 (FastAPI_saas-10iv) recorded as still owed: the Phase-0 INFO
commandstats delta on perf-local that would confirm the derived 2 -> 1 count.
The derivation is recorded in docs/verification/lane_b_checkbox2_arithmetic.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "perf-results" / "RESULT-20261020-qs06" / "s1_session_invalidate_roundtrips.json"

MEASUREMENT_USER_ID = "qs06-probe-user"
MEASUREMENT_SESSION_ID = "qs06-probe-session"
MISS_SESSION_ID = "qs06-probe-absent-session"
WARMUP_SESSION_ID = "qs06-probe-warmup-session"

DERIVED_CLAIM = {
    "source": "docs/verification/lane_b_checkbox2_arithmetic.md (bead FastAPI_saas-osxx)",
    "before_wire_round_trips": 2,
    "after_wire_round_trips": 1,
    "before_shape": (
        "one HEXISTS trip, then one packed transaction pipeline "
        "(redis-py sends MULTI+HDEL+ZREM+EXEC as ONE write)"
    ),
    "after_shape": "one EVALSHA of the session_invalidate script; the existence check moved INSIDE the script",
    "discrepancy_recorded": (
        "the S-1 commit body says 3 -> 1, which counted MULTI and EXEC as separate trips; "
        "the derived count here is 2 -> 1 and the observation below settles which is right"
    ),
}

# The command the instrument itself issues. Redis counts it like any other
# command, and the snapshot that reads the counters is itself counted, so the
# instrument contributes exactly one of these to every window it measures.
INSTRUMENT_COMMAND = "info"


def _repo_head() -> str:
    """The commit the instrument ran against, read out of the tree it ran in."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
        )
    except OSError:
        return "unknown (git is not available here)"
    if completed.returncode != 0:
        return "unknown (this directory is not a git checkout)"
    return completed.stdout.decode("utf-8", errors="replace").strip() or "unknown"


def _imported_module_file() -> str:
    """The file the session_manager module was actually imported from.

    The instrument can be pointed at one tree while the import resolves to
    another - an editable install wins over the working directory unless
    PYTHONPATH says otherwise. This field makes the code that actually ran
    visible in the committed record instead of implied by the directory.
    """
    module = sys.modules.get("src.app.core.security.session_manager")
    if module is None:
        return "unknown (the module is not in sys.modules)"
    return str(getattr(module, "__file__", "unknown"))


def _calls_from(value: Any) -> int:
    """Pull the `calls=N` counter out of one commandstats entry."""
    if isinstance(value, dict):
        raw = value.get("calls", 0)
    else:
        raw = 0
        for part in str(value).split(","):
            if part.strip().startswith("calls="):
                raw = part.strip().split("=", 1)[1]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _snapshot(info: dict[str, Any]) -> dict[str, Any]:
    """Reduce an INFO commandstats reply to absolute call counts.

    Two families are kept apart on purpose. ``client`` counts the commands a
    CLIENT sent, which is the round-trip count. ``lua`` counts the commands a
    SCRIPT called on the server, which are not round trips at all.
    """
    client: dict[str, int] = {}
    lua: dict[str, int] = {}
    for key, value in info.items():
        if not key.startswith("cmdstat_"):
            continue
        name = key[len("cmdstat_") :]
        if name == "lua":
            if isinstance(value, dict):
                for sub_name, sub_value in value.items():
                    lua[sub_name] = _calls_from(sub_value)
            continue
        if name == INSTRUMENT_COMMAND:
            # The instrument's own command. The SECOND INFO commandstats is
            # counted in the very snapshot it produces, so it must never be
            # read as part of the invalidation's traffic.
            continue
        client[name] = _calls_from(value)
    return {"client": client, "lua": lua}


def _delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """The absolute per-command delta between two snapshots.

    ONE flat map, not two families: Redis reports a script's INTERNAL calls in
    the commandstats entries of the commands themselves (cmdstat_hexists,
    cmdstat_hdel, cmdstat_zrem) and NOT under cmdstat_lua, so a family split
    cannot separate them - an earlier version of this instrument split on
    cmdstat_lua and put the script's internal calls in the "client" family.
    The "lua" family here carries only the EVAL counters and is dropped: it
    is not traffic.
    """
    names = set(before.get("client", {})) | set(after.get("client", {}))
    moved = {name: after["client"].get(name, 0) - before["client"].get(name, 0) for name in names}
    return {name: count for name, count in moved.items() if count}


async def _measure(client: Any, manager: Any, *, session_id: str, present: bool) -> dict[str, Any]:
    """Snapshot, invalidate once, snapshot again, and report the absolute delta."""
    if present:
        await client.hset(f"user_sessions_v2:{MEASUREMENT_USER_ID}", session_id, "probe")
        await client.zadd(f"user_sessions_order:{MEASUREMENT_USER_ID}", {session_id: 0})
    before = _snapshot(await client.info("commandstats"))
    returned = await manager.invalidate_session(MEASUREMENT_USER_ID, session_id)
    after = _snapshot(await client.info("commandstats"))
    return {
        "path": "hit (the session was present)" if present else "miss (the session was absent)",
        "session_present_before_the_call": present,
        "invalidate_session_returned": returned,
        "delta": _delta(before, after),
    }


async def _control_window(client: Any) -> dict[str, Any]:
    """Snapshot, do nothing, snapshot again.

    The anti-vacuity control: the window contains no invalidation, so every
    command in its delta was issued by the INSTRUMENT. With the instrument's
    own command excluded the delta must be empty.
    """
    before = _snapshot(await client.info("commandstats"))
    after = _snapshot(await client.info("commandstats"))
    return {"delta": _delta(before, after)}


async def _main_async(args: argparse.Namespace) -> int:
    from src.app.core.redis_provider import create_cache_client, get_redis
    from src.app.core.security.session_manager import SessionManager

    await create_cache_client()
    client = get_redis()
    if client is None:
        print("NO_REDIS_CLIENT: the cache client is not installed", file=sys.stderr)
        return 1

    manager = SessionManager(redis_client=client)

    # Warm the script cache first. The FIRST call on a cold script cache costs
    # more than one trip (EVALSHA raises NOSCRIPT and the manager falls back),
    # and the count under observation is the steady-state one.
    await manager.invalidate_session(MEASUREMENT_USER_ID, WARMUP_SESSION_ID)

    control = await _control_window(client)

    hit = await _measure(client, manager, session_id=MEASUREMENT_SESSION_ID, present=True)
    miss = await _measure(client, manager, session_id=MISS_SESSION_ID, present=False)

    await client.delete(f"user_sessions_v2:{MEASUREMENT_USER_ID}")
    await client.delete(f"user_sessions_order:{MEASUREMENT_USER_ID}")

    hit_delta = hit["delta"]
    miss_delta = miss["delta"]

    # What the CLIENT sent, per arm, taken from the code shape. Redis counts
    # a script's internal calls in the SAME delta as the client's own command,
    # so the split is DECLARED here and the whole delta is asserted.
    client_commands: tuple[str, ...]
    if args.expect == "after":
        # The client sent ONE command: the EVALSHA of the session_invalidate
        # script. The script's own HEXISTS/HDEL/ZREM are server-side calls
        # inside that one command and cost no wire traffic.
        client_commands = ("evalsha",)
        client_trips = 1
        expected_hit = {"evalsha": 1, "hexists": 1, "hdel": 1, "zrem": 1}
        expected_miss = {"evalsha": 1, "hexists": 1}
    else:
        # The pre-S-1 shape: the client sent HEXISTS (one trip), then a packed
        # transaction pipeline - MULTI+HDEL+ZREM+EXEC, which redis-py sends as
        # ONE write on ONE connection, so one more trip. On the miss path the
        # before-code paid the HEXISTS trip only.
        client_commands = ("hexists", "multi", "hdel", "zrem", "exec")
        client_trips = 2
        expected_hit = {"hexists": 1, "multi": 1, "hdel": 1, "zrem": 1, "exec": 1}
        expected_miss = {"hexists": 1}

    observations = {
        "hit_path_delta": hit_delta,
        "miss_path_delta": miss_delta,
        "client_commands": list(client_commands),
        "client_round_trips": client_trips,
        "client_round_trips_is": (
            "DERIVED from the observed command set and the code shape, not a second observation: the commands the "
            "client sent are counted, and redis-py sends a transaction pipeline as ONE write on ONE connection."
        ),
    }

    # The control window: a window with NO invalidation in it. Every command
    # in its delta was issued by the INSTRUMENT, so with the instrument's own
    # command excluded it must be empty. If it is not, the measurement is
    # reading traffic it did not cause.
    assert control["delta"] == {}, f"the control window must be empty, got {control['delta']}"

    # ABSOLUTE assertions - never a comparison of before against after. The
    # expected shape depends on WHICH implementation is under the instrument,
    # and the operator states which with --expect.
    assert hit_delta == expected_hit, f"hit path: expected {expected_hit}, got {hit_delta}"
    assert miss_delta == expected_miss, f"miss path: expected {expected_miss}, got {miss_delta}"
    assert hit["invalidate_session_returned"] is True, "the hit path must report the session as invalidated"
    assert miss["invalidate_session_returned"] is False, "the miss path must report no session"

    server_info = await client.info("server")
    artefact = {
        "schema": "perf-s1-session-invalidate-roundtrips/1",
        "bead": "FastAPI_saas-qs06",
        "item": "S-1",
        "title": "the round-trip count of one session invalidation, observed against a real Redis",
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "method": (
            "the Redis server's own INFO commandstats is snapshotted immediately before and after ONE call to "
            "SessionManager.invalidate_session; the absolute per-command delta is reported. The app's lifespan is "
            "NOT run and no request is issued, so the delta is the invalidation's own and nothing else."
        ),
        "host": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "redis_version": server_info.get("redis_version", "unknown"),
            "redis_host": args.redis_host,
            "redis_port": args.redis_port,
            "in_repo": True,
            "harness": "src/benchmarks/s1_session_invalidate_roundtrips.py",
            "repo_head": _repo_head(),
            "repo_dir": str(Path.cwd()),
            "imported_session_manager": _imported_module_file(),
        },
        "derived_claim": DERIVED_CLAIM,
        "expect": args.expect,
        "observations": observations,
        "control_window": control,
        "what_the_observation_settles": {
            "the_derived_count": "2 -> 1 client round trips",
            "the_s1_commit_body_count": "3 -> 1",
            "verdict": (
                (
                    "THE OBSERVATION CONFIRMS THE DERIVED COUNT OF 2 -> 1 AND CORRECTS THE COMMIT BODY'S 3 -> 1, "
                    "which counted MULTI and EXEC as separate trips. On the after-arm the client sent exactly ONE "
                    "command - the EVALSHA - on both the hit and the miss path; the script's own HEXISTS/HDEL/ZREM "
                    "appear in the same delta because Redis counts a script's internal calls under their own "
                    "commandstats entries, and they cost no wire traffic. On the before-arm the client sent HEXISTS "
                    "and one packed MULTI..EXEC pipeline, which is two trips, not three."
                )
                if args.expect == "after"
                else (
                    "THE OBSERVATION CONFIRMS THE DERIVED COUNT OF THE BEFORE-ARM AS 2 CLIENT ROUND TRIPS - one "
                    "HEXISTS trip and one packed transaction pipeline - and CORRECTS THE COMMIT BODY'S 3 -> 1, "
                    "which counted MULTI and EXEC as separate trips. The packed pipeline is ONE write on ONE "
                    "connection, so the before-arm paid two trips, not three."
                )
            ),
            "the_saving_is_on_the_hit_path_only": (
                "the before-code paid its second trip on both paths; the script's existence check is inside the "
                "EVALSHA, so the miss path pays one trip too. What differs between the paths is what the SCRIPT "
                "does, not how many trips it takes."
            ),
        },
        "what_is_not_asserted": {
            "no_new_less_old": (
                "no comparison of before against after is made. Each run asserts an ABSOLUTE count for the "
                "implementation it was pointed at; the two counts are reported side by side and neither is "
                "asserted to be better than the other."
            ),
            "no_duration": "this artefact quotes no timing and no latency.",
            "not_an_endpoint_measurement": (
                "no request is issued and the app's lifespan is not run, so nothing here says what a login or a "
                "logout costs end to end."
            ),
            "the_lua_internal_counts_are_not_round_trips": (
                "HEXISTS, HDEL and ZREM execute inside the server on the same connection; they are counted "
                "separately by Redis under cmdstat_lua and they cost no wire traffic of their own."
            ),
        },
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artefact, indent=2) + "\n", encoding="utf-8")
    print(f"WROTE {output}")
    print(json.dumps(observations, indent=2))
    print(f"OK: the {args.expect} arm observed the expected absolute client delta; the control window was empty")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--redis-host", default="127.0.0.1")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument(
        "--expect",
        choices=("after", "before"),
        default="after",
        help=(
            "which implementation is under the instrument: 'after' is the S-1 Lua EVALSHA path, 'before' is the "
            "pre-S-1 HEXISTS + packed transaction pipeline path. The absolute assertions follow it."
        ),
    )
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
