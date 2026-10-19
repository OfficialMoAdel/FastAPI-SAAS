# S-8 e2e — the attack-scan method gate, driven over the real stack

**Bead:** `FastAPI_saas-xfpt` — "S-8 e2e: prove the attack-scan gate tests exercise the REAL
predicate at INFO and DEBUG".
**Recorded artefact:** `s8_attack_scan_gate.json` in this directory.
**Driver:** `tests/e2e/feature_validation/test_s8_attack_scan_gate_e2e.py` (in the tree).

## What was asked

xfpt asked for an end-to-end script that drives a POST with a body and with a tripping
user-agent, so the body-scan gate is genuinely entered, and that logs for each request whether
the scan was invoked, which branch it selected, and the value of the log-level predicate at
INFO and at DEBUG. Its design recorded four assertions:

- **(a)** at INFO the predicate `is_enabled_for(DEBUG)` is `False`, and at DEBUG it is `True` —
  asserted as an equality, "the assertion that cannot pass by accident";
- **(b)** a GET with no body does NOT enter the body-scan gate while a POST with a body does
  (the gate is POST/PUT/PATCH only — the scoping fact, perf D18, that keeps the scan programme
  off the `/users/me` hot path);
- **(c)** the tripping user-agent case still trips, and the recorded reason is correct — with
  the explicit warning that Android contains the `id` substring while Pixel does NOT, so a
  test that passes for the wrong reason here will be broken incorrectly later;
- **(d)** a HARD GATE: the final assertion requires A-3 to have landed, because before the
  `wrapper_class` change the predicate was always `True`.

## A correction to this bead's own text

xfpt's description names the gate as living at
`src/app/modules/security/attack_detection.py:429`. That path does not exist. The module is
`src/app/middleware/attack_detection.py`, and the guard is at lines 482–488:

```python
        if method not in _BODY_METHODS:
            if logger.is_enabled_for(logging.DEBUG):
                logger.debug(
                    "attack_detection_skipped_body_scan_for_method",
                    method=method,
                    path=path,
                )
```

The script targets the module that exists. The bead's text is left as written here; the
correction is recorded rather than silently applied to the bead.

## What was driven

Five cases, each driven TWICE — once with the module's stdlib logger at INFO and once at
DEBUG — over the real application through `ASGITransport`, with the real middleware stack
(the `AttackDetectionMiddleware` included) and the real logging stack.

| case | method | path | body | user-agent |
|---|---|---|---|---|
| `get_bodyless` | GET | `/api/v1/users/me` | no | default |
| `post_body_benign` | POST | `/api/v1/posts/by-user/{username}` | benign | default |
| `post_body_sqli` | POST | `/api/v1/posts/by-user/{username}` | `UNION SELECT password FROM users` | default |
| `get_user_agent_benign_pixel` | GET | `/api/v1/users/me` | no | `Mozilla/5.0 (Linux; Android 14; Pixel 8) …` |
| `get_user_agent_command` | GET | `/api/v1/users/me` | no | `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ; id` |

The POST route is the one the L-13 harness used: it is authenticated and self-scoped, and
`src/app/core/rbac/permissions.py` has no posts entry, so a plain seeded account gets a 201
without a permission grant.

## The result

| case | predicate @INFO | predicate @DEBUG | body-scan branch | selected branch | status @INFO | status @DEBUG |
|---|---|---|---|---|---|---|
| `get_bodyless` | `False` | `True` | **skipped** | no_attack | 200 | 200 |
| `post_body_benign` | `False` | `True` | **entered** | no_attack | 201 | 201 |
| `post_body_sqli` | `False` | `True` | **entered** | `request_body` | 400 | 400 |
| `get_user_agent_benign_pixel` | `False` | `True` | **skipped** | no_attack | 200 | 200 |
| `get_user_agent_command` | `False` | `True` | **skipped** | `header:user-agent` | 400 | 400 |

The log records are the REAL ones, captured on the module's own stdlib logger — not a spy
on the call site, and not structlog's global processor chain. The event payload is the
structlog event dict:

```json
{"method": "GET", "path": "/api/v1/users/me",
 "event": "attack_detection_skipped_body_scan_for_method",
 "level": "debug", "logger": "src.app.middleware.attack_detection",
 "timestamp": "2026-10-19T15:10:07.812439Z"}
```

The skip event is emitted at DEBUG and **not** at INFO, in the bodyless cases only. The
detection event is emitted in both arms, because a detection is a WARNING and the level does
not gate it.

### (a) The predicate is the real one

`False` at INFO and `True` at DEBUG, in every case, asserted as an equality. The script
installs this repository's logging configuration itself and asserts that the bound logger is
a `LevelAwareBoundLogger` (A-3's class) before it counts anything. That assertion is a hard
precondition, not a formality: the e2e `client` fixture uses `ASGITransport`, whose own
comment in `tests/e2e/feature_validation/conftest.py` says it "doesn't trigger lifespan",
so the application's lifespan — the thing that calls `setup_logging()` in production — never
runs. Without the installation, structlog stays at its ambient default
(`BoundLoggerFilteringAtNotset`), whose predicate is **structurally always `True`**, and every
assertion in this file would be vacuous: the guard would never close at any level, and the
script would "prove" the pre-A-3 behaviour.

### (b) The gate is the method gate

A bodyless GET is `skipped`; a POST with a body is `entered`. The two POST cases are the
ones whose bodies are read and scanned, and the benign one is a 201 with no detection while
the SQLi one is a 400 with the detection located in `request_body`.

### (c) The tripping user-agent, and the corrected attribution

Both user-agents carry the substring `id`. The difference is POSITION:

- `Mozilla/5.0 (Linux; Android 14; Pixel 8) …` — the `id` is inside "Android", mid-word,
  never at `;` + command position. It is **not detected at all** (`no_attack`, status 200).
- `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ; id` — the `id` sits directly after a
  `;` separator, which is the command position `cmd_inj` matches. It **is detected**, with
  `type=command_injection`, `pattern=command_injection`, `location=header:user-agent`, and
  the request is rejected with 400 in `block` mode.

The benign verdict is therefore NOT explained by the absence of `id` — "Pixel" has no `id`,
but "Android" does, and the request is still clean. The script asserts the POSITION, and
asserts that both strings carry the substring, so an assertion that passed for the wrong
reason cannot survive the attribution being fixed.

## The two clocks, and why one of them is absent

avpc's epic asks for the resolved latency from BOTH clocks. The server-side header is
`X-Response-Time` (`src/app/core/monitoring/request_timer.py`). It was **absent** from every
request in this run, and the reason is recorded per request rather than papered over: the
`RequestTimer` is enabled by `_request_timer_enabled()`, which reads `REQUEST_TIMER_ENABLED`
ONCE, at import of the guard-ordering module — so a session that sets the flag after the
application was constructed still sees no header, and the disabled path never writes one.

Both clocks are **recorded, not thresholded**. No latency assertion is made anywhere in this
file, because S-8's own arithmetic is 0 µs, 0.00 % of the 4.09 ms uncontended floor and
0.00 % of the 740 ms p95. This moves capacity, not p95.

## The deviation, stated plainly

xfpt's inherited standing rule asked for the run to happen "on the Linux target". **That leg
was not run.** The rule came from the latency framing that `FastAPI_saas-uo44`'s close has
already retired: the predicate is a behavioural property — a boolean that depends on the
effective level and not on a host — and the quantity this file measures is a behaviour
(which branch the gate selected, whether an event was emitted), not a duration. The script
drives the real application over the real middleware stack and the real logging stack
in-process, which is where the behaviour lives.

Two further reasons, recorded rather than used as excuses: GATE A(4) (a separate load host)
is unsatisfiable in this environment — `perf-app` and `perf-load` are unreachable from
`perf-local` — and the run is not load generation: it is five cases at two levels, ten
requests, against a local Postgres and Redis.

## What was NOT changed

No production code was touched. The middleware, the logger, the configuration and the
ordering are all as they were; the script only drives them. No assertion in any other test
file was relaxed, and no existing oracle was weakened to accommodate this one.

## How to re-run

```bash
uv run pytest tests/e2e/feature_validation/test_s8_attack_scan_gate_e2e.py -q -p no:randomly
```

To rewrite the recorded artefact:

```bash
S8_E2E_WRITE_ARTEFACT=1 uv run pytest tests/e2e/feature_validation/test_s8_attack_scan_gate_e2e.py -q -p no:randomly
```

The last test in the file reads the recorded artefact back and asserts that its
`stable_facts` are the facts the run measured, so the artefact cannot drift away from the
script that produced it.

## File inventory

| file | what it is |
|---|---|
| `s8_attack_scan_gate.json` | the recorded run: five cases, two levels each, the real log records, the per-request fields, the verdict and the limits |
| `FINDINGS.md` | this note |
| `../RESULT-20261019-uo44/` | the unit-level counter that uo44 closed on (counts of guard evaluations and `logger.debug` invocations) |
