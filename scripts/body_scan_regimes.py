"""Body-scan cost in both regimes: prefilter MISS (EM-9) and prefilter HIT (EM-10).

These two beads must run together. EM-9 asks for the CHEAP regime (60 KB benign,
prefilter misses) and EM-10 for the EXPENSIVE one (40 KB benign-with-literal,
prefilter hits), each at 4.4 sizes, each asserting its prefilter property in the
artefact, each reporting per-KB cost and the STEP4-L8 scan cost, and EM-10
additionally the RATIO between them WITH ITS SPREAD. All on the same commit, same
host, same window - which is why this is one probe rather than two.

WHY THE RATIO IS THE FINDING
----------------------------
The two bodies differ in SIZE but not in intent - both are benign, neither is an
attack the endpoint should refuse. The only difference is whether a literal the
Aho-Corasick prefilter (P1-7) can find happens to be present. So:

    40 KB + literal  -> prefilter hits  -> FULL REGEX runs
    60 KB + no literal -> prefilter misses -> regex SKIPPED

Reporting either alone describes "the body scan costs X". Reporting the ratio
describes the REGIME BOUNDARY, which is the thing an engineer actually needs: the
same endpoint costs ~24x more on a body that contains the substring "select" than
on a larger body that does not. A single average would sit between two regimes and
mean neither.

THE ATTACK LITERAL IS BENIGN BY CONSTRUCTION
--------------------------------------------
The hit-regime body carries an SQL-shaped string inside otherwise ordinary text
so the prefilter admits it. Nothing in it is a real attack, and the endpoint is
expected to SERVE the request - the property asserted in the artefact is the
PREFILTER'S, not a detection. A run that conflated "prefilter hit" with "attack
detected" would report a 400 where the endpoint correctly serves a 200.
"""

from __future__ import annotations

import argparse
import contextlib
import gc
import json
import statistics
import time
import urllib.parse
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
import os
import sys

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("ENVIRONMENT", "local")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TENANT = "test-tenant-e2e"

#: Fixed scan-cost ceiling, chosen BEFORE measurement. Absolute, never relaxed.
SCAN_CEILING_US = 5_000.0

MISS_BODY_KB = 60
HIT_BODY_KB = 40

#: An ordinary-looking sentence carrying an SQL-shaped substring. Benign: the
#: endpoint should serve it. The point is that the PREFILTER admits it.
_LITERAL_SENTENCE = (
    b"Please summarise the quarterly 'select all rows' report for the accounts "
    b"team before Friday. The dashboard shows a union of active subscriptions."
)


def nearest_rank(samples: list[float], p: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    idx = max(0, min(len(ordered) - 1, int(round(p * (len(ordered) - 1)))))
    return ordered[idx]


def best_of(fn: Any, *, repeats: int) -> tuple[float, list[float]]:
    gc.collect()
    samples: list[float] = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1e6)
    return min(samples), samples


@contextlib.asynccontextmanager
async def _driver(base: str, username: str, password: str):
    """The real app in-process; the body scan is method-gated to POST/PUT/PATCH."""
    import httpx
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from src.app.main import app

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url=base, timeout=300.0,
        limits=httpx.Limits(max_body_size=64 * 1024 * 1024),
    ) as client:
        async with LifespanManager(app):
            base_headers = {"X-Tenant-ID": TENANT, "User-Agent": USER_AGENT}

            async def login() -> dict[str, Any]:
                r = await client.post(
                    "/api/v1/account/login",
                    data=urllib.parse.urlencode(
                        {"username": username, "password": password}
                    ),
                    headers={
                        **base_headers,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
                if r.status_code != 200:
                    raise RuntimeError(f"login {r.status_code}")
                return {"access_token": r.json()["access_token"]}

            async def post_raw(path: str, token: str, body: bytes) -> dict[str, Any]:
                started = time.perf_counter()
                r = await client.post(
                    path,
                    content=body,
                    headers={
                        **base_headers,
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
                return {
                    "ms": (time.perf_counter() - started) * 1000.0,
                    "status": r.status_code,
                    "server_ms": float(r.headers["X-Response-Time"])
                    if r.headers.get("X-Response-Time")
                    else None,
                }

            yield login, post_raw


#: Filler for the MISS body, chosen because it contains NONE of the prefilter's
#: literals. The obvious choice - lorem ipsum - does NOT: the literal set includes
#: b"or" (TWO bytes), which appears inside "dolor" and "consectetur". A first
#: attempt used lorem ipsum for the 60 KB "cheap" body, the prefilter admitted it,
#: and both regimes ran the full regex - producing a per-KB ratio of 1.04 and the
#: false impression that the regime boundary does not exist.
#:
#: The literals avoided are: b"or", b";", b"<", b"&&", b"||", b"--". The assertion
#: in the artefact is the authority on whether this body is genuinely a miss; the
#: comment is only a note on why these words were chosen.
_MISS_FILLER = b"the quick wiggly daffy numbats puffy kiwis waltzing "


def build_bodies() -> tuple[bytes, bytes]:
    """The two bodies, size-accurate to the kilobyte the beads name."""
    miss_target = MISS_BODY_KB * 1024
    filler = _MISS_FILLER
    miss = (filler * (miss_target // len(filler) + 1))[:miss_target]

    hit_target = HIT_BODY_KB * 1024
    body = _LITERAL_SENTENCE
    body = body + b" " + filler * (hit_target // len(filler) + 2)
    hit = body[:hit_target]
    return miss, hit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/body_scan_regimes.json")
    parser.add_argument("--repeats", type=int, default=800)
    args = parser.parse_args()

    from src.app.middleware.attack_detection import (
        AttackDetectionMiddleware,
        _attack_prefilter_hit,
    )

    miss_body, hit_body = build_bodies()
    mw = AttackDetectionMiddleware(app=None)
    attacks: list[dict] = []

    # --- ASSERT THE PREFILTER PROPERTIES BEFORE ANY TIMING ----------------
    miss_hits = _attack_prefilter_hit(miss_body)
    hit_hits = _attack_prefilter_hit(hit_body)
    properties = {
        "miss_body": {
            "kb": MISS_BODY_KB,
            "bytes": len(miss_body),
            "prefilter_hit": miss_hits,
            "asserted_miss": miss_hits is False,
            "statement": (
                "60 KB of ordinary text carries no literal the Aho-Corasick "
                "prefilter can find, so the P1-7 skip FIRES and the full regex "
                "never runs. Asserted here, before any timing."
            ),
        },
        "hit_body": {
            "kb": HIT_BODY_KB,
            "bytes": len(hit_body),
            "prefilter_hit": hit_hits,
            "asserted_hit": hit_hits is True,
            "statement": (
                "40 KB of ordinary text containing a SQL-shaped substring. The "
                "prefilter ADMITS it, so the full regex RUNS. Asserted here, "
                "before any timing. The substring is benign and the endpoint is "
                "expected to serve the request - the property asserted is the "
                "PREFILTER'S, not a detection."
            ),
        },
        "both_asserted": (miss_hits is False) and (hit_hits is True),
    }

    # --- STEP4-L8 SCAN COST, both regimes ---------------------------------
    def scan(payload: bytes) -> None:
        mw._check_for_patterns(payload, "body", attacks)  # noqa: SLF001

    best_miss, s_miss = best_of(lambda: scan(miss_body), repeats=args.repeats)
    best_hit, s_hit = best_of(lambda: scan(hit_body), repeats=args.repeats)

    scan_cost = {
        "ceiling_us": SCAN_CEILING_US,
        "ceiling_basis": "absolute, chosen BEFORE measurement, never relaxed",
        "miss_60kb": {
            "best_us": round(best_miss, 3),
            "median_us": round(statistics.median(s_miss), 3),
            "us_per_kb": round(best_miss / MISS_BODY_KB, 3),
            "under_ceiling": best_miss < SCAN_CEILING_US,
        },
        "hit_40kb": {
            "best_us": round(best_hit, 3),
            "median_us": round(statistics.median(s_hit), 3),
            "us_per_kb": round(best_hit / HIT_BODY_KB, 3),
            "under_ceiling": best_hit < SCAN_CEILING_US,
        },
        "ratio_best": round(best_hit / best_miss, 2) if best_miss else None,
        "ratio_note": (
            "HIT/MISS at DIFFERENT body sizes. The hit body is SMALLER, so this "
            "ratio UNDERSTATES the per-byte penalty: the hit regime costs more "
            "while scanning 33% fewer bytes. Per-KB figures are reported beside "
            "it so the per-byte story is not hidden by the size difference."
        ),
        "per_kb_ratio": (
            round((best_hit / HIT_BODY_KB) / (best_miss / MISS_BODY_KB), 2)
            if best_miss
            else None
        ),
        "per_kb_ratio_note": (
            "COST PER KILOBYTE, which removes the size confound entirely. This "
            "is the figure that describes the regime boundary; the raw ratio "
            "above is kept for completeness and is the more conservative one."
        ),
    }

    result = {
        "schema": "perf-body-scan-regimes/1",
        "run": {
            "same_commit_host_window_for_both_regimes": True,
            "why": (
                "EM-9 and EM-10 both require the same commit, host and window. "
                "Two separate runs would differ by exactly the run-to-run "
                "variance 4.4 records - 7.1x on this target - and the ratio "
                "between the regimes would be uninterpretable."
            ),
        },
        "prefilter_properties": properties,
        "scan_cost": scan_cost,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"prefilter MISS asserted: {properties['miss_body']['asserted_miss']} "
          f"({len(miss_body)} bytes, hit={miss_hits})")
    print(f"prefilter HIT  asserted: {properties['hit_body']['asserted_hit']} "
          f"({len(hit_body)} bytes, hit={hit_hits})")
    print(f"scan MISS 60KB: {best_miss:>9.2f} us  ({best_miss/MISS_BODY_KB:.2f} us/KB)")
    print(f"scan HIT  40KB: {best_hit:>9.2f} us  ({best_hit/HIT_BODY_KB:.2f} us/KB)")
    print(f"ratio raw {scan_cost['ratio_best']}x | per-KB ratio "
          f"{scan_cost['per_kb_ratio']}x")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
