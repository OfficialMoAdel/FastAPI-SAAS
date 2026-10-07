#!/usr/bin/env bash
# run_perf_baseline.sh - H-9 / P-3: measure against a SERVER ON A SEPARATE HOST,
# under the security budgets the server actually runs with, after a warm-up, and
# record the whole topology in a pinned manifest.
#
# USAGE
#   1. On the APP HOST, with the server's own environment in force and the app
#      started the way the Dockerfile starts it (uvicorn src.app.main:app, no
#      --loop, no --http):
#          python -m src.benchmarks.run_manifest --emit-app-host-report > app_host_report.json
#   2. Copy that file to the LOAD HOST, into this checkout, as
#      reports/app_host_report.json.
#   3. On the LOAD HOST, with the load generator's own environment:
#          ./scripts/run_perf_baseline.sh --host http://<app-host>:8000 -u 100 -r 10 -t 5m
#
# WHY IT LOOKS LIKE THIS
#   The load generator and the server used to be one process tree on one machine:
#   this script started uvicorn on 127.0.0.1 and then pointed locust at it, so
#   gevent's hub and locust's CSV aggregation competed with the application for
#   CPU and every latency figure was a contention measurement. It no longer starts
#   the server at all. There is no configuration of this script that measures a
#   server on this host, which is the only way to make the refusal below something
#   other than a promise.
#   It used to export AUTO_BAN_ENABLED=false and SECURITY_BUDGETS of 10000/60 and
#   100000/3600 while its own header described production as (5,60) and
#   (10,3600), and no report disclosed the difference. It now exports nothing: the
#   server keeps its own environment, and the manifest records the limits the
#   server reported - with an AUTO_BAN_ENABLED=false label whenever that is the
#   case, on every artefact.
#   The interpreter probe resolves .venv/bin/python BEFORE .venv/Scripts/python.exe
#   and refuses a Windows-only venv outright. The old order proved the archived
#   baselines ran on Windows, where uvloop cannot be installed and no number is
#   representative (addendum A-5).
#   The first locust run measures the COLD window, the second holds at target
#   concurrency for >=60 s, and only the third is reported. The hold is a separate
#   process writing separate files, so its requests cannot appear in any reported
#   percentile - that exclusion is structural, not a promise. The measured run adds
#   --reset-stats, and the phase report locates that reset in locust's own history
#   and records where it landed.
#   Both ends of the run are gated, and the end record must match the start. A
#   commit, harness blob, security limit or app-host runtime that changed while the
#   run was in flight voids it.
#
# OPTIONS
#   -u, --users N      concurrent users (default 100)
#   -r, --rate N       spawn rate, users per second (default 10)
#   -t, --duration T   measured run time, e.g. 5m (default 5m)
#       --hold T       hold at target concurrency before measuring, e.g. 60s
#                      (default: P-5's floor, read from run_manifest.py, not
#                      repeated here). A shorter hold is refused, not clamped.
#       --cold-window T
#                      the cold window, taken immediately after the health gate
#                      (default 30s, minimum 10s). This is the number an operator
#                      experiences on deploy, and it is reported alongside the warm
#                      one, never instead of it.
#       --host URL     REQUIRED. The application under test. A loopback address,
#                      this host's name, or any address bound on this host is
#                      refused rather than measured.
#       --app-host-report PATH
#                      the app host's report (default reports/app_host_report.json,
#                      overridable with PERF_APP_HOST_REPORT)
#       --seed         (re)seed the account pool before measuring
#   -h, --help         this text
#
# EXIT CODES
#   0   measured
#   3   the harness is not pinned to git          (run_manifest.py)
#   4   no usable HEAD                            (run_manifest.py)
#   5   an untracked harness dependency           (run_manifest.py)
#   6   no usable app-host report                 (run_manifest.py)
#   7   the app host is not uvloop + httptools    (run_manifest.py)
#   8   the manifest was incomplete              (run_manifest.py)
#   9   the end of the run did not match its start (run_manifest.py)
#   10  the deployment manifest had a hole in it  (run_manifest.py)
#   11  the cold/hold/warm phases are not what P-5 describes (run_manifest.py)
#   64  this script refused to run (EX_USAGE): missing --host, a local target, a
#       Windows-only venv, no venv at all, or a hold shorter than P-5's floor.

set -euo pipefail

cd "$(dirname "$0")/.."

USERS=100
RATE=10
DURATION=5m
HOLD=""
COLD_WINDOW=30s
SEED=0
HOST_URL=""
APP_HOST_REPORT="${PERF_APP_HOST_REPORT:-reports/app_host_report.json}"

# The cold window has to be long enough to produce a distribution at all. Below
# this it is not a cold measurement, it is a handful of samples wearing one.
MIN_COLD_SECONDS=10

while [[ $# -gt 0 ]]; do
    case "$1" in
        -u|--users)     USERS="$2"; shift 2 ;;
        -r|--rate)      RATE="$2"; shift 2 ;;
        -t|--duration)  DURATION="$2"; shift 2 ;;
        --hold)         HOLD="$2"; shift 2 ;;
        --cold-window)  COLD_WINDOW="$2"; shift 2 ;;
        --host)         HOST_URL="$2"; shift 2 ;;
        --app-host-report) APP_HOST_REPORT="$2"; shift 2 ;;
        --seed)         SEED=1; shift ;;
        -h|--help)      awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
        *)              echo "unknown argument: $1 (try --help)" >&2; exit 64 ;;
    esac
done

# ---------------------------------------------------------------------------
# refusals, before anything is measured
# ---------------------------------------------------------------------------

refuse() {
    echo "VOID: $1" >&2
    exit 64
}

# POSIX layout first. The old script tested .venv/Scripts/python.exe first, which
# is why a Windows host silently produced the pack's e2e baselines.
if [[ -x ".venv/bin/python" ]]; then
    PY=".venv/bin/python"
elif [[ -x ".venv/Scripts/python.exe" ]]; then
    refuse "this checkout has a Windows .venv (Scripts/python.exe) and no POSIX one. uvloop cannot be installed on Windows, so no number produced on this host is representative (addendum A-5), and a Windows run must not be able to pass as a Linux one. Create a Linux virtualenv (.venv/bin/python) and re-run."
else
    refuse "no interpreter found. Expected .venv/bin/python; create the virtualenv with 'uv sync'."
fi

[[ -n "${HOST_URL}" ]] || refuse "--host is required. This script measures a server on ANOTHER host and will not start one here, so there is no default target: pass --host http://<app-host>:8000."

# ---------------------------------------------------------------------------
# durations, and P-5's floor read from the harness rather than repeated here
# ---------------------------------------------------------------------------
#
# P-5 is ">=60 s at target concurrency, then --reset-stats". The floor lives in
# run_manifest.py, next to the rule, and is read from there: a literal here would be
# a second copy of a number that has already been written down once.

to_seconds() {
    local value="$1" number unit
    if [[ "${value}" =~ ^([0-9]+)([smh]?)$ ]]; then
        number="${BASH_REMATCH[1]}"
        unit="${BASH_REMATCH[2]}"
        case "${unit}" in
            s|"") echo "${number}" ;;
            m)    echo $((number * 60)) ;;
            h)    echo $((number * 3600)) ;;
        esac
        return 0
    fi
    return 1
}

require_at_least() {
    local label="$1" value="$2" floor="$3" seconds
    if ! seconds="$(to_seconds "${value}")"; then
        refuse "--${label} '${value}' is not a duration this runner understands. Use e.g. 90s, 5m, 1h."
    fi
    if [[ "${seconds}" -lt "${floor}" ]]; then
        refuse "--${label} is ${value} (${seconds}s), below the ${floor}s floor. P-5 requires at least ${floor}s at target concurrency before the measured window; a shorter hold is refused rather than clamped, because clamping would silently report a number taken before steady state."
    fi
}

HOLD_FLOOR="$("${PY}" -m src.benchmarks.run_manifest --print-min-hold-seconds)"
HOLD="${HOLD:-${HOLD_FLOOR}s}"
require_at_least "hold" "${HOLD}" "${HOLD_FLOOR}"
require_at_least "cold-window" "${COLD_WINDOW}" "${MIN_COLD_SECONDS}"

HOST_ADDR="${HOST_URL#*://}"
HOST_ADDR="${HOST_ADDR%%/*}"
if [[ "${HOST_ADDR}" == \[*\]* ]]; then
    # Bracketed IPv6, e.g. [::1]:8000 - the brackets come off first, or the
    # ':'-split below would reduce it to a bare '[' and stop matching anything.
    HOST_ADDR="${HOST_ADDR#\[}"
    HOST_ADDR="${HOST_ADDR%%\]*}"
elif [[ "${HOST_ADDR}" != *:*:* ]]; then
    # host:port, where the port is only unambiguous when there is at most one
    # colon. A bare IPv6 literal has several, and is kept whole.
    HOST_ADDR="${HOST_ADDR%%:*}"
fi
[[ -n "${HOST_ADDR}" ]] || refuse "--host '${HOST_URL}' has no host part. Pass a URL such as http://<app-host>:8000."

case "${HOST_ADDR}" in
    localhost|localhost.localdomain|*.localhost|127.*|0.0.0.0|::|::1|0:0:0:0:0:0:0:1)
        refuse "--host '${HOST_URL}' resolves to ${HOST_ADDR}, which is this machine or a loopback address. The load generator and the server must be on separate hosts: locust's gevent hub and CSV aggregation compete with the application for CPU, so a same-host number measures contention rather than latency (perf audit finding 2). Start the app on another host and pass its address."
        ;;
esac

local_addresses() {
    if command -v ip >/dev/null 2>&1; then
        ip -o addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1
    else
        hostname -I 2>/dev/null | tr ' ' '\n' || true
    fi
}

for name in $(hostname 2>/dev/null || true) $(hostname -s 2>/dev/null || true) $(hostname -f 2>/dev/null || true); do
    if [[ "${HOST_ADDR}" == "${name}" ]]; then
        refuse "--host '${HOST_URL}' is this host's own name (${name}). The load generator and the server must be on separate hosts; see the reason in the localhost case above."
    fi
done

while read -r local_addr; do
    [[ -n "${local_addr}" ]] || continue
    if [[ "${HOST_ADDR}" == "${local_addr}" ]]; then
        refuse "--host '${HOST_URL}' is an address bound on this host (${local_addr}). The load generator and the server must be on separate hosts; see the reason in the localhost case above."
    fi
done < <(local_addresses)

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="locust_baseline_${STAMP}.log"
CSV="reports/baseline_${STAMP}"
MANIFEST="reports/baseline_${STAMP}_manifest.json"
MANIFEST_END="reports/baseline_${STAMP}_manifest_end.json"
PHASES="reports/baseline_${STAMP}_phases.json"

{
    echo "baseline run ${STAMP}"
    echo "  load host:     $(uname -srm) ($(uname -n)), cpu_count recorded in the manifest"
    echo "  app host:      ${HOST_ADDR}"
    echo "  users:         ${USERS} at ${RATE}/s"
    echo "  phases:        cold ${COLD_WINDOW} -> hold ${HOLD} (floor ${HOLD_FLOOR}s) -> warm ${DURATION} with --reset-stats"
    echo "  app-host report: ${APP_HOST_REPORT}"
    echo "  security:      whatever the app host runs; recorded in the manifest, never overridden here"
    echo "  cold CSVs:     ${CSV}_cold_stats.csv (reported alongside the warm one, never instead of it)"
    echo "  hold CSVs:     ${CSV}_hold_stats.csv (discarded: the hold is not a result)"
} > "${LOG}"

# ---------------------------------------------------------------------------
# provenance gate, at the START of the run
# ---------------------------------------------------------------------------

set +e
"${PY}" -m src.benchmarks.run_manifest --output "${MANIFEST}" --users "${USERS}" --rate "${RATE}" --duration "${DURATION}" --host "${HOST_URL}" --app-host-report "${APP_HOST_REPORT}" --phase start >> "${LOG}" 2>&1
PROVENANCE_RC=$?
set -e
if [[ "${PROVENANCE_RC}" -ne 0 ]]; then
    {
        echo "VOID: the run-start provenance gate refused this run (exit ${PROVENANCE_RC})."
        echo "No load was generated and no manifest was written. See ${LOG} for the gate's reason."
    } | tee -a "${LOG}" >&2
    exit "${PROVENANCE_RC}"
fi

if [[ "${SEED}" -eq 1 ]]; then
    echo "seeding the account pool..." | tee -a "${LOG}"
    "${PY}" scripts/setup_locust_test_data.py >> "${LOG}" 2>&1
fi

# ---------------------------------------------------------------------------
# the app host must actually be serving
# ---------------------------------------------------------------------------

HEALTH_BODY="$(mktemp)"
cleanup() { rm -f "${HEALTH_BODY}"; }
trap cleanup EXIT

health_code() {
    # --noproxy '*': a global http_proxy in the operator's environment otherwise
    # intercepts the request to the app host and the code below is the proxy's.
    curl -s --noproxy '*' -m 3 -o "${HEALTH_BODY}" -w '%{http_code}' "${HOST_URL}/api/v1/health"
}

echo "waiting for ${HOST_ADDR} to serve /api/v1/health..." | tee -a "${LOG}"
READY=0
for _ in $(seq 1 60); do
    if [[ "$(health_code)" == "200" ]]; then
        READY=1
        break
    fi
    sleep 2
done
if [[ "${READY}" -ne 1 ]]; then
    {
        echo "VOID: ${HOST_ADDR} did not answer /api/v1/health with 200 within 120s."
        echo "The app host must be running before the load host measures it."
    } | tee -a "${LOG}" >&2
    exit 64
fi

# ---------------------------------------------------------------------------
# cold, then the hold, then the measured window
# ---------------------------------------------------------------------------
#
# Three invocations, in this order, because P-5 needs the cold figure and the warm
# figure SEPARATELY and the hold to sit between them:
#
#   cold  immediately after the health gate, against a server whose caches are cold.
#         NO --reset-stats: excluding the spawn ramp here would also exclude the
#         coldness, and this number is the one an operator sees on deploy.
#   hold  >=60 s at target concurrency, which is the concurrency that populates what
#         100 users populate. A lower-concurrency hold warms less. Its CSVs are kept
#         and named _hold so the discard is visible on disk.
#   warm  the baseline, with --reset-stats so the spawn ramp is out of the reported
#         percentiles.

run_locust() {
    local csv_prefix="$1" run_time="$2" reset_flag="${3:-}"
    NO_PROXY='*' no_proxy='*' "${PY}" -m locust -f tests/performance/locustfile.py --headless \
        -u "${USERS}" -r "${RATE}" --run-time "${run_time}" --host "${HOST_URL}" ${reset_flag} \
        --csv="${csv_prefix}" >> "${LOG}" 2>&1
}

echo "cold window: ${COLD_WINDOW} at ${USERS} users (CSVs -> ${CSV}_cold_*)..." | tee -a "${LOG}"
run_locust "${CSV}_cold" "${COLD_WINDOW}"

echo "hold: ${HOLD} at ${USERS} users, floor ${HOLD_FLOOR}s (CSVs -> ${CSV}_hold_*, discarded)..." | tee -a "${LOG}"
run_locust "${CSV}_hold" "${HOLD}"

echo "measuring: ${DURATION} at ${USERS} users with --reset-stats (CSVs -> ${CSV}_*)..." | tee -a "${LOG}"
set +e
run_locust "${CSV}" "${DURATION}" "--reset-stats"
LOCUST_RC=$?
set -e

# ---------------------------------------------------------------------------
# provenance gate, at the END of the run
# ---------------------------------------------------------------------------
#
# The start manifest says nothing by itself; if the commit moved, a harness file
# was edited, or the app host's limits changed during the run, it describes a run
# that did not happen and the numbers in between cannot be attributed to anything.

set +e
"${PY}" -m src.benchmarks.run_manifest --output "${MANIFEST_END}" --users "${USERS}" --rate "${RATE}" --duration "${DURATION}" --host "${HOST_URL}" --app-host-report "${APP_HOST_REPORT}" --phase end --assert-matches "${MANIFEST}" >> "${LOG}" 2>&1
PROVENANCE_RC=$?
set -e
if [[ "${PROVENANCE_RC}" -ne 0 ]]; then
    {
        echo "VOID: the run-end provenance gate refused this run (exit ${PROVENANCE_RC})."
        echo "Something changed between the start and the end of the run, so the measured numbers cannot be attributed to a single configuration."
        echo "Both manifests are kept: ${MANIFEST} and ${MANIFEST_END}."
    } | tee -a "${LOG}" >&2
    exit "${PROVENANCE_RC}"
fi

# ---------------------------------------------------------------------------
# the cold/warm report
# ---------------------------------------------------------------------------
#
# The CSVs are on disk but nothing has yet said what population each one describes.
# This reads all three back, records both the cold and the warm figure per endpoint,
# locates the --reset-stats boundary in locust's own history, and refuses (11) if
# the phases did not run in P-5's order. It runs AFTER the run-end gate on purpose:
# the report describes a run that the provenance gate has already accepted.

set +e
"${PY}" -m src.benchmarks.run_manifest --emit-phase-report --manifest "${MANIFEST}" \
    --cold-csv "${CSV}_cold" --hold-csv "${CSV}_hold" --warm-csv "${CSV}" \
    --output "${PHASES}" >> "${LOG}" 2>&1
PHASE_RC=$?
set -e
if [[ "${PHASE_RC}" -ne 0 ]]; then
    {
        echo "VOID: the cold/warm phase report refused this run (exit ${PHASE_RC})."
        echo "Reporting only the phase that happened to survive is how a 5-10x cold inflation hides. See ${LOG}."
    } | tee -a "${LOG}" >&2
    exit "${PHASE_RC}"
fi

{
    echo ""
    echo "manifest (start):  ${MANIFEST}"
    echo "manifest (end):    ${MANIFEST_END}"
    echo "cold and warm:     ${PHASES}"
    echo "cold CSVs:         ${CSV}_cold_stats.csv"
    echo "hold CSVs:         ${CSV}_hold_stats.csv (discarded)"
    echo "measured CSVs:     ${CSV}_stats.csv"
} | tee -a "${LOG}"

exit "${LOCUST_RC}"
