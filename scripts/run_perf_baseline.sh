#!/usr/bin/env bash
#
# Run the PERF-R1-A Locust baseline end-to-end.
#
# Why this script exists
# ----------------------
# A 100-user single-host load test cannot run against the app's DEFAULT
# security settings. Everything below is per client IP, and every virtual user
# shares this host's IP:
#
#   SECURITY_BUDGETS["auth"]  = (5, 60)     -> 5 logins/min
#   DEFAULT_ANONYMOUS_LIMIT   = (10, 3600)  -> 10 logins/hour (login is anonymous)
#   AUTO_BAN_ENABLED          = true (10)   -> bans the IP for 1h, escalating to 2h
#
# Forgetting any one of these turns the run into a wall of 403/429/500. This
# script starts a dedicated uvicorn with those limits relaxed (process env only
# - src/.env is never touched) and tears it down afterwards.
#
# Usage
#   ./scripts/run_perf_baseline.sh                     # 100 users, ramp 10/s, 5m
#   ./scripts/run_perf_baseline.sh -u 20 -r 5 -t 1m    # quick smoke
#   ./scripts/run_perf_baseline.sh --seed              # (re)seed account pool first
#   PERF_TEST_AI_ENABLED=1 ./scripts/run_perf_baseline.sh   # include /ai/chat
#
# Provenance: every run writes a manifest recording the harness blob SHAs, the
# app commit SHA, the store addresses and every security budget in force. The run
# VOIDS if any harness file is untracked, dirty or missing.
#
# Output: locust_baseline_<timestamp>.log in the repo root, CSVs in reports/, and
# reports/baseline_<timestamp>_manifest.json - the provenance record that makes
# the numbers attributable. The run VOIDS if the harness is not pinned to git;
# see the provenance gate below.

set -euo pipefail

cd "$(dirname "$0")/.."

# ---------------------------------------------------------------- arguments ---
USERS=100
RATE=10
DURATION="5m"
SEED=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -u|--users)    USERS="$2"; shift 2 ;;
    -r|--rate)     RATE="$2"; shift 2 ;;
    -t|--duration) DURATION="$2"; shift 2 ;;
    --seed)        SEED=1; shift ;;
    -h|--help)     awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# Resolve python: project venv if present, else whatever is on PATH.
if [[ -x .venv/Scripts/python.exe ]]; then
  PY=".venv/Scripts/python.exe"
elif [[ -x .venv/bin/python ]]; then
  PY=".venv/bin/python"
else
  PY="python"
fi

HOST="http://127.0.0.1:8000"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="locust_baseline_${STAMP}.log"
CSV="reports/baseline_${STAMP}"
# Provenance manifest, written next to the CSVs before anything is measured.
MANIFEST="reports/baseline_${STAMP}_manifest.json"
SERVER_PID=""

# ------------------------------------------------------------------- header ---
{
  echo "=============================================================="
  echo " Locust baseline - $(date '+%Y-%m-%d %H:%M:%S %z')"
  echo " users=${USERS} ramp=${RATE}/s duration=${DURATION} host=${HOST}"
  echo " locustfile=tests/performance/locustfile.py"
  echo " python=${PY}  log=${LOG}"
  echo "--------------------------------------------------------------"
  echo " SERVER: uvicorn started by this script with RELAXED limits:"
  echo "   AUTO_BAN_ENABLED=false"
  echo "   SECURITY_BUDGETS auth/refresh/ai_chat -> 10000/60"
  echo "   DEFAULT_ANONYMOUS_LIMIT / DEFAULT_AUTHENTICATED_LIMIT -> 100000/3600"
  echo " (process env only; src/.env untouched. Plain 'uvicorn ...' restores them.)"
  echo "=============================================================="
  echo
} > "${LOG}"

# ------------------------------------------------------------ provenance gate --
# P-9a (perf finding 3): the 2026-09-22 baseline is unrecoverable from git, so
# the two Locust baselines are not a same-instrument A/B and the headline p95
# figure cannot be attributed to the application at all. The fix is to make an
# unpinned instrument impossible to measure with: this gate VOIDS the run if any
# harness file is untracked, dirty, or missing, and otherwise writes a manifest
# recording the harness blob SHAs, the app commit SHA, the store addresses, and
# every security budget actually in force.
#
# It runs BEFORE the seed step: there is no reason to create an account pool for
# a run that is about to void.
echo "==> Verifying harness provenance"
set +e
"${PY}" -m src.benchmarks.run_manifest \
  --output "${MANIFEST}" \
  --users "${USERS}" --rate "${RATE}" --duration "${DURATION}" --host "${HOST}" \
  >> "${LOG}" 2>&1
PROVENANCE_RC=$?
set -e
if [[ "${PROVENANCE_RC}" -ne 0 ]]; then
  {
    echo "==> VOID: harness provenance check failed (exit ${PROVENANCE_RC}); see above and ${LOG}"
    echo "==> VOID: a baseline whose instrument cannot be recovered from git can never"
    echo "==> VOID: be re-run, so it can never be checked. No numbers were produced."
  } | tee -a "${LOG}" >&2
  exit "${PROVENANCE_RC}"
fi
echo "==> Provenance OK -> ${MANIFEST}"

# -------------------------------------------------------------- seed pool ----
if [[ "${SEED}" -eq 1 ]]; then
  echo "==> Seeding account pool"
  "${PY}" scripts/setup_locust_test_data.py >> "${LOG}" 2>&1
fi

# --------------------------------------------------------- start app server --
echo "==> Starting app server with relaxed limits"
export AUTO_BAN_ENABLED=false
export SECURITY_BUDGETS='{"auth":[10000,60],"mfa_verify":[5,60],"mfa_verify_global":[15,60],"mfa_mutate":[5,60],"magic_link_send":[3,60],"passkey_login":[10,60],"refresh":[10000,60],"ai_chat":[10000,60]}'
export DEFAULT_ANONYMOUS_LIMIT="[100000,3600]"
export DEFAULT_AUTHENTICATED_LIMIT="[100000,3600]"

"${PY}" -m uvicorn src.app.main:app --host 127.0.0.1 --port 8000 >> "${LOG}" 2>&1 &
SERVER_PID=$!

# ----------------------------------------------------------- health gate -----
# NOTE: two curl quirks are baked in here, both verified on this host:
#   1. --noproxy '*'  - http_proxy=127.0.0.1:64323 is set globally and would
#      otherwise intercept localhost calls, returning bogus 502s.
#   2. -o <real file> - NOT /dev/null. Under Git Bash, '-o /dev/null' makes
#      curl exit 23 (write error) even on a 200, so a naive 'curl -f' probe
#      always looks like a failure. We write the body to a temp file instead
#      and compare the emitted status code.
HEALTH_BODY="$(mktemp)"
cleanup() {
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    echo "==> Stopping app server (pid ${SERVER_PID})"
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
  # Quiet: some environments wrap rm in a trash shim that prints on failure.
  [[ -n "${HEALTH_BODY}" ]] && rm -f "${HEALTH_BODY}" 2>/dev/null || true
}
trap cleanup EXIT

health_code() {
  curl -s --noproxy '*' -m 3 -o "${HEALTH_BODY}" -w '%{http_code}' \
    "${HOST}/api/v1/health" 2>/dev/null || echo "000"
}

echo "==> Waiting for ${HOST}/api/v1/health"
HEALTHY=0
for _ in $(seq 1 60); do
  if [[ "$(health_code)" == "200" ]]; then
    HEALTHY=1
    echo "==> Server is up"
    break
  fi
  sleep 2
done
if [[ "${HEALTHY}" -ne 1 ]]; then
  echo "==> ERROR: server never became healthy; see ${LOG}" >&2
  exit 1
fi

# ------------------------------------------------------------------- locust --
echo "==> Running Locust (${USERS} users, ${DURATION}) -> ${LOG}"
# NO_PROXY: requests honours http_proxy, so Locust must bypass it too.
NO_PROXY='*' no_proxy='*' "${PY}" -m locust \
  -f tests/performance/locustfile.py \
  --headless -u "${USERS}" -r "${RATE}" --run-time "${DURATION}" \
  --host "${HOST}" \
  --csv="${CSV}" >> "${LOG}" 2>&1
LOCUST_RC=$?

echo "locust exit=${LOCUST_RC}" >> "${LOG}"
echo "==> Done. Log: ${LOG} ($(wc -l < "${LOG}") lines)"
echo "==> CSVs: ${CSV}_*.csv"

exit "${LOCUST_RC}"
