"""Record the configuration a measurement actually ran under.

P-4 was not that a value was wrong. It was that every Round-1 baseline was
produced with the security controls disabled or relaxed AND no report disclosed
the override, so the numbers described a system nobody deploys.

This writes the disclosure next to the measurement instead of in a commit
message nobody reads: every override that is in force, the production value it
replaced, and why. A run with no overrides says so explicitly, because "nothing
was overridden" is itself a fact a reader needs.

It is deliberately a SCRIPT rather than a convention. A convention is followed
until someone is in a hurry, and the whole failure this addresses was a hurry.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Every variable that changes whether a measured request is served on the path
#: production takes. Names are the setting paths as the app reads them.
SECURITY_VARS = (
    "AUTO_BAN_ENABLED",
    "SECURITY_BUDGETS",
    "DEFAULT_AUTHENTICATED_LIMIT",
    "DEFAULT_ANONYMOUS_LIMIT",
    "TIER_RATE_LIMITS",
    # Added because its absence voided a run rather than merely being untidy.
    # ANONYMOUS_IP_AGGREGATE_LIMIT is NOT part of SECURITY_BUDGETS: it is a
    # separate ceiling on distinct per-path keys created by ONE ip per window
    # (300/3600 by default). Relaxing the four documented budgets leaves it in
    # force, so a load run keeps tripping it - 645 violations in 50 minutes -
    # and 4.5 voids any run with a non-zero count. It also keys on the PEER
    # address rather than X-Forwarded-For, so per-user client IPs do not spread
    # it. Found by running the run, not by reading the config.
    "ANONYMOUS_IP_AGGREGATE_LIMIT",
    # TIER_RATE_LIMITS is a PER-USER-PER-PATH cap keyed
    # `tenant:<id>:rate_limit:user:<user>:<path>`, and it is what voided a 48-minute
    # run at 1000/hour. It is the one that bit hardest, because it is per USER and
    # so spreading load across 300 accounts does not help: each account still hits
    # its own ceiling on the hot path.
    "TIER_RATE_LIMITS",
    # Fallbacks used when no tier and no per-path policy matches. Plain INTS, not
    # tuples - setting them as "[1000000,3600]" is a validation error and the
    # server refuses to start, which is at least a loud failure.
    "DEFAULT_RATE_LIMIT_LIMIT",
    "DEFAULT_RATE_LIMIT_PERIOD",
)

#: What production runs. Taken from config/services.py, not from memory.
PRODUCTION = {
    "AUTO_BAN_ENABLED": "true",
    "SECURITY_BUDGETS": {
        "auth": [5, 60],
        "mfa_verify": [5, 60],
        "mfa_verify_global": [15, 60],
        "mfa_mutate": [5, 60],
        "magic_link_send": [3, 60],
        "passkey_login": [10, 60],
        "refresh": [60, 60],
        "ai_chat": [60, 60],
    },
    "DEFAULT_AUTHENTICATED_LIMIT": [50, 3600],
    "DEFAULT_ANONYMOUS_LIMIT": [10, 3600],
    "ANONYMOUS_IP_AGGREGATE_LIMIT": [300, 3600],
    "TIER_RATE_LIMITS": {"free": [10, 3600], "basic": [100, 3600],
                          "pro": [1000, 3600], "enterprise": [10000, 3600]},
    "DEFAULT_RATE_LIMIT_LIMIT": None,
    "DEFAULT_RATE_LIMIT_PERIOD": None,
}


def _read_live_env(pid: int) -> dict[str, str]:
    """The environment of the process ACTUALLY being measured.

    A disclosure written from the shell that launched the server is a claim about
    an intention. This run proved the difference is not academic: a second uvicorn
    could not bind 8010, locust measured the FIRST one instead, and the artefact
    faithfully described overrides the measured process never had. That is P-4
    recurring through a different door - the number was disclosed, but it described
    the wrong process.

    So when a PID is given, the values come from /proc/<pid>/environ and the shell
    is not consulted at all.
    """
    raw = Path(f"/proc/{pid}/environ").read_bytes()
    env: dict[str, str] = {}
    for chunk in raw.split(b"\0"):
        if b"=" not in chunk:
            continue
        key, _, value = chunk.partition(b"=")
        env[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return env


def main() -> int:
    argv = [a for a in sys.argv[1:] if a != "--from-pid"]
    pid: int | None = None
    for i, a in enumerate(sys.argv):
        if a == "--from-pid" and i + 1 < len(sys.argv):
            pid = int(sys.argv[i + 1])

    out = Path(argv[0]) if argv else Path("reports/disclosure.json")
    reason = argv[1] if len(argv) > 1 else None

    source = os.environ
    source_note = "the launching shell's environment"
    if pid is not None:
        source = _read_live_env(pid)
        source_note = f"/proc/{pid}/environ - the process actually measured"

    overrides = {}
    for name in SECURITY_VARS:
        value = source.get(name)
        if value is None:
            continue
        overrides[name] = {"measured_with": value, "production": PRODUCTION.get(name)}

    payload = {
        "schema": "perf-config-disclosure/1",
        "source": source_note,
        "measured_pid": pid,
        "overrides": overrides,
        "override_count": len(overrides),
        "reason": reason,
        "reading": (
            "NO SECURITY OVERRIDE WAS IN FORCE. This run describes the system as "
            "it is configured to run in production."
            if not overrides
            else "SECURITY CONTROLS WERE OVERRIDDEN. This run describes a "
            "configuration that does not exist in production and must not be "
            "compared against a run that left them in force. The reason is "
            "recorded above."
        ),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    print(f"  source             : {source_note}")
    print(f"  overrides in force : {len(overrides)}")
    for name, spec in overrides.items():
        print(f"    {name}: {spec['measured_with']}  (production: {spec['production']})")
    if not overrides:
        print("  none - production controls were in force")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
