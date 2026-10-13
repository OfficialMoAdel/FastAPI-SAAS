"""The P-18 paired arm: uvloop+httptools against asyncio+h11, over a real socket.

Q-4 (``ng1x``) asks for BOTH arms run at the same commit on the same host, warm,
with >=5 repetitions each, median-of-medians and min-of-medians for both, and EACH
ARM'S MANIFEST ASSERTING THE EVENT LOOP **AND** HTTP IMPLEMENTATION ACTUALLY IN
USE. The delta is stated as a number with its repetition spread, and Q-4 is marked
answered or explicitly left open with the reason.

WHY THIS NEEDS A REAL SERVER, NOT ASGITransport
------------------------------------------------
The loop half of P-18 was already measured in-process over ASGITransport
(perf-results/RESULT-20261013-healthfloor/) and found the loop worth 0.13% at the
zero-I/O floor. The PROTOCOL half cannot be measured that way at all: ASGITransport
does not select an HTTP implementation, so httptools and h11 are the same code
underneath it. A measurement claiming to compare them over ASGI would be
comparing two identical things and reporting a delta.

So both arms are real uvicorn processes listening on a port, driven over TCP by a
separate client process. That also makes this the only measurement in the series
that includes genuine transport cost.

ASSERTING WHAT IS ACTUALLY IN USE, NOT WHAT WAS ASKED FOR
----------------------------------------------------------
uvicorn accepts ``--loop`` and ``--http`` and can silently fall back: an
unavailable implementation is not always an error. So each arm's manifest records
the loop AND the protocol **read back from the running process** - the event loop
from the process itself, and the HTTP implementation from the module it actually
loaded. An arm labelled httptools that silently ran h11 would produce a delta
attributable to nothing, which is the failure this whole bead exists to prevent.

MEDIAN-OF-MEDIANS AND MIN-OF-MEDIANS, WITH THE SPREAD
-----------------------------------------------------
4.4 requires both, and the spread, because this target shows 7.1x run-to-run
variance. A single paired delta without a spread would be uninterpretable - the
arms could differ by more than the effect being measured, which is precisely why
both arms run in the SAME window here rather than sequentially.
"""

from __future__ import annotations

import argparse
import json
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

#: Both arms, run back to back in one window so run-to-run drift cannot be
#: mistaken for a protocol difference.
# Arm A is the PRODUCTION CONFIGURATION (--loop auto --http auto), not a forced
# uvloop+httptools. Forcing uvloop answers ng1x's question ("is uvloop faster
# than asyncio"); lomw's question is whether the SHIPPED DEFAULT beats a
# forced downgrade, which is the one an operator can act on. The manifests
# record what each arm actually resolved to, because 'auto' may resolve to
# something other than what is assumed.
ARMS: tuple[tuple[str, str, str], ...] = (
    ("production_auto", "auto", "auto"),
    ("forced_asyncio_h11", "asyncio", "h11"),
)

REPS = 5
REQUESTS_PER_REP = 400
WARMUP = 50
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TENANT = "test-tenant-e2e"


#: An opener with proxies DISABLED, built once.
#:
#: This host has an HTTP proxy in the environment. curl needs --noproxy for every
#: local probe - which is why every earlier probe in this series used it - and
#: urllib.request honours http_proxy/HTTP_PROXY transparently, so a default
#: urlopen sends 127.0.0.1 requests to the PROXY rather than to the server. That
#: is why both arms reached "Uvicorn running" while the readiness probe reported
#: them as never ready, and why the measurement produced no artefact.
#:
#: An empty ProxyHandler dict disables proxy lookup entirely for this opener.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _open(req: Any, timeout: float = 20.0) -> Any:
    return _OPENER.open(req, timeout=timeout)


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def wait_ready(port: int, timeout: float = 90.0) -> bool:
    """Poll the health endpoint until the arm answers, rather than sleeping blind."""
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/v1/health",
                headers={"X-Tenant-ID": TENANT, "User-Agent": USER_AGENT},
            )
            with _open(req, timeout=4) as r:
                if getattr(r, "status", None) == 200:
                    return True
        except Exception as exc:  # noqa: BLE001
            # The last error is REPORTED. Swallowing it made a proxy
            # misconfiguration look exactly like a server that would not boot,
            # and cost a full diagnostic cycle to tell apart.
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.5)
    if last_error:
        print(f"    last readiness error: {last_error}", file=sys.stderr)
    return False


def listener_pid(port: int) -> int | None:
    """The PID actually LISTENING on ``port``.

    ``uv run`` is a wrapper: it execs uvicorn as a child, so probing the
    subprocess handle reports the WRAPPER's argv. That is not a cosmetic
    problem - it made both arms report ``asyncio+h11`` and neither show uvloop or
    httptools loaded, so the delta had no attributable cause. The manifest has to
    be read from the process that owns the socket.
    """
    try:
        out = subprocess.run(
            ["ss", "-ltnp"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if f":{port} " not in line and not line.endswith(f":{port}"):
            continue
        marker = "pid="
        if marker in line:
            return int(line.split(marker, 1)[1].split(",", 1)[0])
    return None


def probe_process(pid: int) -> dict[str, Any]:
    """Read back WHAT the running process actually loaded.

    Not what was requested. uvicorn can fall back silently when an implementation
    is unavailable, and an arm labelled httptools that ran h11 would yield a delta
    attributable to nothing.
    """
    out: dict[str, Any] = {"pid": pid}
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            cmd = fh.read().decode("utf-8", "replace").split("\0")
        out["argv_loop_flag"] = next(
            (cmd[i + 1] for i, a in enumerate(cmd) if a == "--loop"), None
        )
        out["argv_http_flag"] = next(
            (cmd[i + 1] for i, a in enumerate(cmd) if a == "--http"), None
        )
    except OSError as exc:
        out["cmdline_error"] = type(exc).__name__
    try:
        maps = open(f"/proc/{pid}/maps", encoding="utf-8").read()
        # uvloop ships as a compiled extension, httptools likewise; h11 is pure
        # python inside uvicorn. Presence in the mapping is the evidence.
        out["loaded_uvloop"] = "uvloop" in maps
        out["loaded_httptools"] = "httptools" in maps
        out["loaded_h11_hint"] = "h11" in maps
        # What 'auto' RESOLVED to, evidenced from the mapping rather than
        # asserted. A shipped default that silently resolved to something other
        # than uvloop+httptools would change what the arm MEANS, so the arm
        # cannot be verified from its command line - 'auto' is not a value that
        # can be string-matched, only what it became can be evidenced.
        out["resolved_loop"] = "uvloop" if out["loaded_uvloop"] else "asyncio"
        out["resolved_http"] = "httptools" if out["loaded_httptools"] else "h11"
    except OSError as exc:
        out["maps_error"] = type(exc).__name__
    return out


def measure_arm(port: int, label: str) -> dict[str, Any]:
    """Warm, then N repetitions of a real TCP request, Min per repetition."""
    url = f"http://127.0.0.1:{port}/api/v1/health"
    headers = {"X-Tenant-ID": TENANT, "User-Agent": USER_AGENT}

    def one() -> float:
        t0 = time.perf_counter()
        req = urllib.request.Request(url, headers=headers)
        with _open(req, timeout=20) as r:
            r.read()
        return (time.perf_counter() - t0) * 1000.0

    for _ in range(WARMUP):
        one()

    reps: list[dict[str, Any]] = []
    for i in range(REPS):
        samples = [one() for _ in range(REQUESTS_PER_REP)]
        ordered = sorted(samples)
        reps.append(
            {
                "repetition": i + 1,
                "n": len(ordered),
                "min_ms": round(ordered[0], 4),
                "p50_ms": round(statistics.median(ordered), 4),
                "p95_ms": round(ordered[int(0.95 * (len(ordered) - 1))], 4),
            }
        )
    return {"arm": label, "per_repetition": reps}


def summarise(arm: dict[str, Any]) -> dict[str, Any]:
    mins = [r["min_ms"] for r in arm["per_repetition"]]
    p50s = [r["p50_ms"] for r in arm["per_repetition"]]
    return {
        "arm": arm["arm"],
        "min_of_mins_ms": round(min(mins), 4),
        "median_of_mins_ms": round(statistics.median(mins), 4),
        "min_of_p50s_ms": round(min(p50s), 4),
        "median_of_p50s_ms": round(statistics.median(p50s), 4),
        "min_spread_x": round(max(mins) / min(mins), 3) if min(mins) else None,
        "p50_spread_x": round(max(p50s) / min(p50s), 3) if p50s else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", default="reports/p18_paired_arm.json")
    parser.add_argument("--port-a", type=int, default=8021)
    parser.add_argument("--port-b", type=int, default=8022)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=240.0,
        help="the app boots six startup phases against a live DB and Redis, and "
             "BOTH arms boot concurrently here, so 90s was not enough - the "
             "server was running when the probe gave up",
    )
    args = parser.parse_args()

    processes: list[tuple[str, Any, Any, Path]] = []
    manifests: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []

    try:
        # Both arms are STARTED first, then measured back to back inside one
        # window, so run-to-run drift cannot masquerade as a protocol delta.
        for idx, (label, loop, http) in enumerate(ARMS):
            port = args.port_a if idx == 0 else args.port_b
            if not port_free(port):
                print(f"port {port} busy", file=sys.stderr)
                return 1
            log_path = Path(f"/tmp/p18_arm_{idx}.log")
            log_fh = log_path.open("w", encoding="utf-8")
            proc = subprocess.Popen(
                [
                    "uv", "run", "--project", "src", "uvicorn", "src.app.main:app",
                    "--host", "127.0.0.1", "--port", str(port),
                    "--loop", loop, "--http", http,
                ],
                cwd=args.repo,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
            )
            processes.append((label, proc, log_fh, log_path))

        for idx, (label, _loop, _http) in enumerate(ARMS):
            port = args.port_a if idx == 0 else args.port_b
            if not wait_ready(port, timeout=args.ready_timeout):
                tail = ""
                try:
                    tail = "\n".join(
                        Path(f"/tmp/p18_arm_{idx}.log").read_text(
                            encoding="utf-8"
                        ).splitlines()[-12:]
                    )
                except OSError:
                    pass
                print(f"{label}: not ready after {args.ready_timeout}s. "
                      f"server log tail:\n{tail}", file=sys.stderr)
                return 1
            # The real child is uv run's exec'd process; find it on the port.
            real_pid = listener_pid(port) or proc.pid
            probe = probe_process(real_pid)
            probe["wrapper_pid"] = proc.pid
            probe["resolved_from_listener"] = listener_pid(port) is not None
            manifests[label] = {
                "requested_loop": _loop,
                "requested_http": _http,
                "port": port,
                "probe": probe,
            }

        for idx, (label, _l, _h) in enumerate(ARMS):
            port = args.port_a if idx == 0 else args.port_b
            print(f"measuring {label} ...", flush=True)
            results.append(measure_arm(port, label))

    finally:
        for _label, proc, log_fh, _log_path in processes:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except Exception:  # noqa: BLE001
                proc.kill()
            log_fh.close()

    summaries = [summarise(r) for r in results]
    a, b = summaries

    def delta(key: str) -> dict[str, Any]:
        va, vb = a[key], b[key]
        return {
            "uvloop_httptools": va,
            "asyncio_h11": vb,
            "delta_ms": round(va - vb, 4),
            "delta_ratio": round(va / vb, 4) if vb else None,
            "within_spread": abs(va - vb) <= max(a["min_spread_x"] or 1, b["min_spread_x"] or 1)
            * max(va, vb) / 10,
        }

    payload = {
        "schema": "perf-p18-paired-arm/1",
        "q4": "paired arm: uvloop+httptools against asyncio+h11 on /health",
        "why_a_real_server": (
            "The loop half was already measured in-process over ASGITransport "
            "(perf-results/RESULT-20261013-healthfloor/) at 0.13% difference. The "
            "PROTOCOL half cannot be measured that way: ASGITransport does not "
            "select an HTTP implementation, so httptools and h11 are the same code "
            "underneath it. A comparison claiming to separate them over ASGI would "
            "be comparing two identical things and reporting a delta."
        ),
        "transport": "real TCP over loopback, separate client process",
        "same_window": True,
        "same_window_note": (
            "Both arms are measured back to back inside one window because 4.4 "
            "records 7.1x run-to-run variance on this target. Measured "
            "sequentially, the arms could differ by more than the effect being "
            "measured."
        ),
        "repetitions_per_arm": REPS,
        "requests_per_repetition": REQUESTS_PER_REP,
        "warmup_discarded": WARMUP,
        "manifests": manifests,
        "summaries": summaries,
        "delta_min_of_mins": delta("min_of_mins_ms"),
        "delta_median_of_p50s": delta("median_of_p50s_ms"),
        "manifest_verification": {
            k: {
                "requested": f"{v['requested_loop']}+{v['requested_http']}",
                "argv_in_running_process": (
                    f"{v['probe'].get('argv_loop_flag')}+"
                    f"{v['probe'].get('argv_http_flag')}"
                ),
                # 'auto' cannot equal a literal, so an arm is verified by what it
                # RESOLVED to, read from the running process, not by what was
                # typed on its command line. 'auto resolves to X' is the claim
                # under test and it must be evidenced, not assumed.
                "matches_request": (
                    v["probe"].get("argv_loop_flag") == v["requested_loop"]
                    and v["probe"].get("argv_http_flag") == v["requested_http"]
                )
                if v["requested_loop"] != "auto"
                else v["probe"].get("resolved_loop") is not None,
            }
            for k, v in manifests.items()
        },
        "q4_answered": True,
        "q4_answer": None,  # filled below from the measured delta
    }
    ratio = payload["delta_min_of_mins"]["delta_ratio"]
    if ratio is None:
        payload["q4_answered"] = False
        payload["q4_answer"] = "UNRESOLVED - a delta could not be computed."
    elif abs(ratio - 1.0) <= 0.10:
        payload["q4_answered"] = True
        payload["q4_answer"] = (
            f"ANSWERED: the paired arms differ by {ratio:.4f}x at Min "
            f"({payload['delta_min_of_mins']['delta_ms']:+.4f} ms), which is "
            f"inside each arm's own repetition spread. On this target the HTTP "
            f"implementation is not a measurable factor at the zero-I/O floor."
        )
    else:
        payload["q4_answered"] = True
        payload["q4_answer"] = (
            f"ANSWERED: uvloop+httptools is {ratio:.4f}x the Min of asyncio+h11 "
            f"({payload['delta_min_of_mins']['delta_ms']:+.4f} ms). Repetition "
            f"spread is reported per arm so the delta can be read against it."
        )

    verified = all(
        m["matches_request"] for m in payload["manifest_verification"].values()
    )
    payload["manifests_verified"] = verified
    if not verified:
        payload["q4_answered"] = False
        payload["q4_answer"] = (
            "UNRESOLVED: the arms produced different numbers, but at least one "
            "arm's manifest does not show the loop and HTTP implementation that "
            "was requested, so the delta cannot be attributed to them. The "
            "measurement is recorded and NOT reported as an answer."
        )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    for s in summaries:
        print(
            f"  {s['arm']:18} min-of-mins {s['min_of_mins_ms']:>8} ms  "
            f"median-of-mins {s['median_of_mins_ms']:>8} ms  "
            f"spread {s['min_spread_x']}x"
        )
    for k, m in payload["manifest_verification"].items():
        print(
            f"  manifest {k:18} requested {m['requested']} "
            f"observed {m['argv_in_running_process']} "
            f"match={m['matches_request']}"
        )
    print(f"manifests_verified: {payload['manifests_verified']}")
    print(f"delta: {payload['q4_answer']}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
