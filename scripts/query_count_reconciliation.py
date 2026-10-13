"""pg_stat_statements delta vs the app's own X-Query-Count, for warm GET /users/me.

P-21 (``1o8a``) asks for the two counts side by side and requires them to AGREE,
so that the "zero SQL" claim for this endpoint is either confirmed authoritatively
or refuted with a number.

TWO COUNTS, AND WHY BOTH ARE NEEDED
------------------------------------
The app's ``before_cursor_execute`` listener counts statements as SQLAlchemy
issues them. That is a CLIENT-side count: it cannot see a statement issued by a
path that bypassed the engine, and it cannot see one the pool retried.
``pg_stat_statements`` is the SERVER's own tally - what Postgres actually
executed. Agreement between the two is the only evidence that the client-side
number describes the work the database really did.

THE +1 IS NOT HERE
------------------
The store probes (store_rtt_probe.py) each subtract one because the snapshot read
counts itself. Here the snapshot is filtered to EXCLUDE pg_stat_statements'
own rows, so no offset applies and none is subtracted. Subtracting an offset that
is not present is how a probe manufactures agreement, so this one is stated
rather than borrowed.

WARM MEANS WARM
---------------
The first request on a cold process pays for pool creation, schema checks and
first-touch page faults, and the endpoint caches. Every request after the warm-up
is measured; the warm-ups are issued and discarded, and their counts are not
folded into the total - otherwise a cold-start spike would be attributed to the
steady state the bead asks about.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

TENANT_HEADER = {"X-Tenant-ID": "test-tenant-e2e"}


def _app_side_count(response: Any) -> int | None:
    """``X-Query-Count`` from the response, or None when the profiler is off.

    A missing header is None, never 0. "The profiler did not report" and "the
    profiler reported zero queries" are different facts, and conflating them is
    how a zero-SQL claim gets confirmed by an instrument that was never switched
    on.
    """
    raw = response.headers.get("X-Query-Count")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _pg_statements_total(conn: Any) -> int:
    """Total statements the server has executed, excluding this probe's own.

    Excluding the probe's rows is what removes the self-count offset the store
    probes have to subtract. No offset is applied here.
    """
    row = await conn.fetchrow(
        "select coalesce(sum(calls), 0)::bigint as calls "
        "from pg_stat_statements "
        "where query not like '%pg_stat_statements%'"
    )
    return int(row["calls"]) if row else 0


async def _pg_statements_breakdown(conn: Any) -> list[dict[str, Any]]:
    """Per-statement calls, so the report can show WHICH SQL, not just how many."""
    rows = await conn.fetch(
        "select calls, round(total_exec_time::numeric, 3) as total_ms, "
        "       round(mean_exec_time::numeric, 4) as mean_ms, left(query, 80) as query "
        "from pg_stat_statements "
        "where query not like '%pg_stat_statements%' "
        "order by calls desc limit 8"
    )
    return [
        {
            "calls": int(r["calls"]),
            "total_ms": float(r["total_ms"]),
            "mean_ms": float(r["mean_ms"]),
            "query": r["query"],
        }
        for r in rows
    ]


async def main_async(args: argparse.Namespace) -> int:
    import asyncpg

    load_dotenv("src/.env", override=True)

    dsn = (
        f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ['POSTGRES_SERVER']}:{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/{os.environ['POSTGRES_DB']}"
    )
    conn = await asyncpg.connect(dsn, timeout=15)
    base = args.base.rstrip("/")
    headers = {**TENANT_HEADER}

    # WARM-UP: authenticate, then issue and DISCARD warm requests so pool creation
    # and first-touch costs are not charged to the steady state.
    # The login endpoint takes FORM data, not JSON. Posting JSON with an
    # x-www-form-urlencoded content type returns 422 "Field required" for both
    # username and password, which reads exactly like broken credentials and is
    # not. urlencoded() matches what the endpoint actually parses.
    login_body = urllib.parse.urlencode(
        {"username": args.username, "password": args.password}
    ).encode()
    login_req = urllib.request.Request(
        f"{base}/api/v1/account/login",
        data=login_body,
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(login_req, timeout=20) as r:
        token = json.load(r).get("access_token", "")
    if not token:
        print("LOGIN FAILED - no token; cannot measure an authenticated endpoint")
        return 1

    auth = {**headers, "Authorization": f"Bearer {token}"}
    for _ in range(args.warmup):
        req = urllib.request.Request(f"{base}/api/v1/users/me", headers=auth)
        with urllib.request.urlopen(req, timeout=20):
            pass

    before_server = await _pg_statements_total(conn)
    app_total = 0
    missing_headers = 0
    per_request: list[int | None] = []

    for _ in range(args.requests):
        req = urllib.request.Request(f"{base}/api/v1/users/me", headers=auth)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                count = _app_side_count(r)
                if count is None:
                    missing_headers += 1
                else:
                    app_total += count
                per_request.append(count)
        except urllib.error.HTTPError:
            per_request.append(None)

    after_server = await _pg_statements_total(conn)
    breakdown = await _pg_statements_breakdown(conn)
    await conn.close()

    server_delta = after_server - before_server
    result = {
        "schema": "perf-query-count-reconciliation/1",
        "endpoint": "GET /users/me",
        "warm": True,
        "requests_measured": args.requests,
        "warmup_discarded": args.warmup,
        "app_side": {
            "source": "X-Query-Count header, from before_cursor_execute",
            "total_queries": app_total,
            "per_request": per_request,
            "responses_without_header": missing_headers,
            "usable": missing_headers == 0,
        },
        "server_side": {
            "source": "pg_stat_statements, sum(calls), probe's own rows excluded",
            "total_calls": server_delta,
            "top_statements": breakdown,
        },
        "agree": app_total == server_delta if missing_headers == 0 else None,
        "delta": server_delta - app_total if missing_headers == 0 else None,
        "queries_per_request": (
            app_total / args.requests if args.requests and missing_headers == 0 else None
        ),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"app-side  (X-Query-Count) : {app_total} over {args.requests} requests")
    print(f"server-side (pg_stat_statements): {server_delta}")
    print(f"agree     : {result['agree']}   delta: {result['delta']}")
    if missing_headers:
        print(f"WARNING: {missing_headers} responses carried no X-Query-Count header; "
              f"is QUERY_PROFILER_ENABLED set on the app?")
    print(f"queries per request: {result['queries_per_request']}")
    print(f"wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/query_count_reconciliation.json")
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--username", default="qa0@e.io")
    parser.add_argument("--password", default="Str0ngFixture!x")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=5)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
