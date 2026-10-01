# RS256 Migration Runbook

**Version:** 1.0
**Last Updated:** 2026-09-30
**Audience:** Engineering Team, On-Call Engineers
**Criticality:** MEDIUM — planned change; execute on a healthy system, never under incident pressure

**Scope:** Moving token signing from `HS256` (symmetric) to `RS256` (asymmetric) in the `FastAPI_saas` backend. The configuration already supports both; this document is the *procedure* and the *reversal*.

**Tracked by bead:** `FastAPI_saas-io15.9`

---

## Execution status — read this first

**Date of the recorded dry run: 2026-09-30.**

This runbook is **partially executed**. The honest accounting:

| Section | Status |
|---|---|
| Step 1 — generate the key pair | ✅ **EXECUTED** (throwaway keypair, in-process) |
| Step 2 — config-load fail-fast on a missing half | ✅ **EXECUTED** |
| Step 3 — the dual-verify truth table | ✅ **EXECUTED** |
| Step 4 — algorithm-confusion rejection | ✅ **EXECUTED** |
| Step 5 — key rotation and the `lru_cache` restart lag | ✅ **EXECUTED** |
| Step 6 — login → verify → refresh-rotate → logout under both algorithms | ✅ **EXECUTED** (service layer) |
| Step 7 — stage into a real secret store | ❌ **NOT EXECUTED** — operator step |
| Step 8 — restart the fleet / observe the dual-verify window | ❌ **NOT EXECUTED** — operator step |
| Step 9 — cutover and drain | ❌ **NOT EXECUTED** — operator step |
| Step 10 — post-cutover soak and retire `SECRET_KEY` | ❌ **NOT EXECUTED** — operator step |

**What the recorded run was NOT:** it was not a deployment. No server was started, no container was built, no process was restarted, no secret store was touched, and no real keypair was generated or retained. It ran in a single Python process against the pytest harness, which supplies the environment and stubs the database session and the Redis-backed revocation write.

**Specifically NOT exercised, and therefore NOT claimed:**
- **HTTP endpoints.** `/api/v1/account/login`, `/refresh`, `/logout` were not called. The run drove `TokenIssuer.issue_session` / `rotate_token_pair` and `jwt_service.verify_token` / `blacklist_token` directly. The service layer beneath the endpoints is what was tested; the routing, cookie handling, and middleware above it were not.
- **PostgreSQL.** Not running. The database session was a double, so `token_version` came from a stubbed lookup rather than the `users` table.
- **Redis.** Not running. `is_token_revoked` fails **closed** when Redis is unavailable — the correct production posture, and it means the harness had to pin that oracle to "not revoked" to test the lifecycle. A real Redis will behave differently; do not read the harness's revocation result as evidence about Redis behaviour.
- **A genuine process restart.** The cache-lag step cleared the caches *in-process*, which is what a restart does, but it does not prove an orchestrator actually restarts pods on a config change.

The recorded output is reproduced verbatim in [§ Recorded execution](#recorded-execution-2026-09-30).

---

## What is already correct (do not re-derive this)

Verified against the source on 2026-09-30:

| Property | Where | Confirmed |
|---|---|---|
| `ALGORITHM` is constrained to `HS256` or `RS256` | `src/app/core/config/crypt.py:32` | ✅ |
| RS256 requires **both** key halves at config load | `crypt.py:77-84` | ✅ |
| HS256 requires `SECRET_KEY` ≥ 32 characters | `crypt.py:85-92` | ✅ |
| `SECRET_KEY` is rejected if absent/empty | `crypt.py:66-70` | ✅ |
| `SECRET_KEY` is checked against a committed-leak blocklist | `crypt.py:21-27, 72-75` | ✅ |
| Decode pins the algorithm via `algorithms=[alg]` | `src/app/core/security/jwt_utils.py:314, 322` | ✅ |
| `aud` and `iss` are verified on every decode | `jwt_utils.py:296-298` | ✅ |

Two corrections to the bead's line references, for the record:

- The `lru_cache` key holder the bead cites as `jwt_service.py:122-129` is at **`jwt_service.py:159-171`** (`_get_signing_key` + `clear_signing_key_cache`). Lines 122-129 are `register_user_tenant_lookup` / `reset_jwt_security_lookups`, which are the DIP registration seams and have nothing to do with the key.
- The **verification** key cache the migration actually bites is `jwt_utils.py:145-191` (`_get_parsed_verification_key` + `clear_jwt_key_cache`). The bead names only the signing cache. Both matter; the verification one is the one that decides whether new tokens validate.

---

## ⚠️ The `lru_cache` restart interaction — read before you rotate anything

Both key accessors are memoised per process:

- `jwt_service._get_signing_key()` — `@lru_cache(maxsize=1)`, `jwt_service.py:159`
- `jwt_utils._get_parsed_verification_key()` — `@lru_cache(maxsize=1)`, `jwt_utils.py:145`

**Consequence:** after you change `JWT_PRIVATE_KEY`, `JWT_PUBLIC_KEY`, or `SECRET_KEY` in the environment, the running process keeps using the key it cached on first use. **The change does not take effect until the process restarts.**

This is easy to get wrong, because the failure looks like the rotation silently not working. An engineer who installs a new key, watches verification keep failing, and concludes "the new key is broken" will be debugging the wrong thing. In the recorded run:

```
JWT_PUBLIC_KEY swapped; cached key object unchanged    : True
new-key token verifies WITHOUT key-cache clear         : False   ← operator swaps the key, nothing happens
new-key token verifies AFTER key-cache clear (restart) : True    ← same key, one restart later, works
```

A restart is the only supported mechanism. `clear_signing_key_cache()` and `clear_jwt_key_cache()` exist and are called by the test fixtures, but **there is no admin endpoint or signal that calls them in production** — do not build a rotation procedure that assumes one does.

**There is a second, sharper trap.** `jwt_utils` also memoises decode *failures* in `_decode_failure_cache` for **60 seconds** (`jwt_utils.py:102-106`), keyed by token hash. Once a token has been rejected as a permanent failure (`InvalidSignatureError` / `DecodeError` / `ExpiredSignatureError`), that verdict is cached and every subsequent attempt with the same token string short-circuits to `None` **without running the crypto at all**.

The practical consequence during a key swap: a token that was rejected under the old key keeps being rejected for up to 60 seconds after you install the correct one. So during cutover, when you "fix" the key and then immediately retest with a token that already failed, you may get a stale `False` and conclude the fix did not work. Wait out the TTL or restart the process (a restart drops both caches). This is why the recorded run clears both caches at every observation point.

---

## Step 1 — Generate the key pair

Generate **outside** the application, on a machine whose logs you trust. Never generate a signing key inside a container that may be logged.

```bash
# RSA-2048. 3072 if your crypto policy demands it.
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out jwt_private.pem
openssl rsa -in jwt_private.pem -pubout -out jwt_public.pem

# Verify the pair actually matches before you distribute anything.
openssl rsa -in jwt_private.pem -pubout -out jwt_private_check.pem
diff jwt_public.pem jwt_private_check.pem && echo "PAIR MATCHES"
```

**Record the output of `diff`.** A public key that does not match its private key is the single most common cause of a total verification outage, and it is invisible until the first request.

> The error message in `crypt.py:82-83` suggests `openssl genrsa -out private.pem 2048 && openssl rsa -in private.pem -pubout -out public.pem`, which also works. `genpkey` is preferred because it emits a PKCS#8 `BEGIN PRIVATE KEY` body, which is what `JWT_PRIVATE_KEY` expects.

**Storage.** The private key goes in the secret store (Vault / AWS Secrets Manager / SOPS-encrypted file). It must **never** be committed, and the repository blocklist (`KNOWN_LEAKED_SECRET_KEYS`) is the safety net for `SECRET_KEY` only — a leaked PEM is not covered by it.

---

## Step 2 — Confirm config load refuses to proceed on a missing half

This is the fail-fast check. Do it before touching any running environment.

```bash
ALGORITHM=RS256 SECRET_KEY="$(openssl rand -hex 32)" \
  python -c "from src.app.core.config.crypt import CryptSettings; CryptSettings()"
```

Expected: `ValidationError` — *"RS256 algorithm requires JWT_PRIVATE_KEY and JWT_PUBLIC_KEY"*. Repeat with each half supplied individually; both single-half cases must also be refused.

Verified behaviour (recorded run):

```
private only   -> REFUSED: Value error, RS256 algorithm requires JWT_PRIVATE_KEY and JWT_PUBLIC_KEY
public only    -> REFUSED: Value error, RS256 algorithm requires JWT_PRIVATE_KEY and JWT_PUBLIC_KEY
both           -> LOADED
```

If any of these load, **stop**. The instance would start and fail every request instead of failing to boot.

---

## Step 3 — Understand the truth table before you deploy it

There is **no** dual-algorithm mode. `ALGORITHM` is a single value and `algorithms=[alg]` pins it to exactly one. The decoder has no "try both" path.

The consequence is that a token is valid **only** under the algorithm that minted it:

| Token minted under | Configured `ALGORITHM` | Verifies? |
|---|---|---|
| HS256 | HS256 | ✅ |
| HS256 | RS256 | ❌ |
| RS256 | RS256 | ✅ |
| RS256 | HS256 | ❌ |

Recorded run, verbatim:

```
HS token verifies under HS256 config : True
RS token verifies under HS256 config : False
RS token verifies under RS256 config : True
HS token verifies under RS256 config : False
```

**Therefore "the dual-verify window" in this system is not a decoder feature — it is a fleet window.** You get overlap because you can run old and new algorithm *instances at the same time*, not because one instance accepts both. Plan for that: cutover is a rolling replacement of processes, and until every process has been replaced, users hitting a not-yet-migrated instance with a new token get a 401.

The practical shape this forces:

1. Mint under the **old** algorithm until every verifier has been migrated, **or**
2. Accept a brief 401 spike during the rolling replacement and drain sessions, **or**
3. Deploy the RS256 instances first (they can only verify RS256, so they serve nothing useful yet), then flip the minters.

Given `ACCESS_TOKEN_EXPIRE_MINUTES` defaults to 30 and refresh tokens live 7 days, **option 2 with a planned logout is the honest default** unless you run a two-phase deploy. Budget for the full access-token TTL (30 min) for the old tokens to age out; do not shorten refresh TTLs to rush it.

---

## Step 4 — Confirm the algorithm pin is load-bearing

The security property that makes this safe is that a token minted under one algorithm is refused under the other. Verify it, and verify it is the **pin** doing it rather than an incidental key error.

```python
jwt.decode(hs_token, rsa_public_key, algorithms=["RS256"])      # InvalidAlgorithmError
jwt.decode(hs_token, rsa_public_key, algorithms=["RS256", "HS256"])  # key-layer error instead
```

Recorded run:

```
pinned RS256 vs HS256 token -> InvalidAlgorithmError: The specified alg value is not allowed
unpinned (RS256+HS256) vs HS256 token -> TypeError: Expected a string value
```

Both reject — but for different reasons, and that distinction matters. Widening the pin does not make the token acceptable; it moves the failure from the algorithm check to the key layer (PyJWT refuses to use an RSA public key object as an HMAC secret). That second defence is real, but it is a *different* defence: it does not exist for every key type, and it is not what `algorithms=[alg]` buys you. The pin is the property; do not relax it.

This is pinned by `tests/unit/core/security/test_jwt_algorithm_pinning.py::test_rs256_rejection_is_caused_by_the_alg_pin_not_the_key`.

---

## Step 5 — Rotate the key and observe the cache lag

Before the real rotation, rehearse it:

1. Install the new **public** key on one verifier.
2. Mint a token with the new **private** key.
3. Observe: rejected. **This is correct** — the process has cached the old key.
4. Restart the process (or confirm a restart happens via config rollout).
5. Observe: accepted.

Recorded run:

```
token minted by NEW keypair verifies under OLD config : False
JWT_PUBLIC_KEY swapped; cached key object unchanged    : True
new-key token verifies WITHOUT key-cache clear         : False
new-key token verifies AFTER key-cache clear (restart) : True
old-key token verifies AFTER cutover                    : False
```

The last line is the property you want to see: after cutover, tokens from the **old** keypair are refused. If that line ever reads `True`, the old keypair is still trusted and you have not finished the migration.

Remember the 60-second decode-failure cache when you reproduce this by hand.

---

## Step 6 — Regression check (the counter-test)

Adding the RS256 capability must not disturb HS256. The counter-test drives the real issuance and verification seams under **both** algorithms:

```
HS256: login(alg=HS256) verify=True refresh=True rotate=True logout=blacklisted
RS256: login(alg=RS256) verify=True refresh=True rotate=True logout=blacklisted
```

`verify` = `jwt_service.verify_token`; `refresh` = the refresh token decodes; `rotate` = `rotate_token_pair` produces a new pair that verifies; `logout` = the token is blacklisted. Pinned by `test_login_refresh_logout_cycle_survives` (parametrised over both algorithms).

On a real environment, also exercise the HTTP endpoints end to end — `POST /api/v1/account/login` → `POST /api/v1/account/refresh` → `POST /api/v1/account/logout` — with PostgreSQL and Redis up. **That was not done here** (see the execution status table).

---

## Steps 7-10 — The operator cutover (NOT EXECUTED)

These require a real environment. They are written to be followed; they have not been rehearsed here.

### Step 7 — Stage the keys

1. Store the private key in the secret store. Store the public key wherever non-secret config lives — it is not a secret.
2. Set on the deployment target, **still leaving `ALGORITHM=HS256`**:
   ```
   JWT_PRIVATE_KEY=<PEM with \n escapes>
   JWT_PUBLIC_KEY=<PEM with \n escapes>
   ```
3. Confirm the instance still boots and still signs HS256. If it does not boot, you have a PEM escaping problem — the single most common Step 7 failure.

Do **not** flip `ALGORITHM` in this step. Staging the keys first means a later flip is a one-variable change with a known-good fallback.

### Step 8 — Dual-verify window

Run at least one full `ACCESS_TOKEN_EXPIRE_MINUTES` interval (30 min by default) with the keys staged and the algorithm unchanged. During this window:

- The keys are distributed and validated.
- Any PEM/escaping problem surfaces now, not at cutover.
- Nothing has changed for users.

Then restart the fleet so the staged keys are picked up (see the `lru_cache` section). Old and new-key instances can now coexist.

### Step 9 — Cutover

1. Set `ALGORITHM=RS256` in the deployment config.
2. Roll the fleet. **Watch the 401 rate the whole way.** The rolling replacement is the window where a new-algorithm token can land on an old-algorithm instance.
3. Watch `jwt_decode_failed` in the logs. This event fires on every rejection, including the expected ones during the rollout.
4. Once every instance is on RS256, re-run the Step 5 check: a token from the **old** keypair must now be **refused**.

### Step 10 — Soak, then retire the old material

1. Soak for at least the refresh-token lifetime (`REFRESH_TOKEN_EXPIRE_DAYS`, 7 days by default) so no live refresh token depends on the old key.
2. Only then remove the old `SECRET_KEY` from the environment. Until then it is still load-bearing.
3. Add the old `SECRET_KEY` to `KNOWN_LEAKED_SECRET_KEYS` if it is being retired because of any exposure concern, so it can never be reintroduced. (Only do this for a value that actually leaked — the blocklist is a leak list, not a retirement list.)
4. Update `docs/user-guide/configuration/environment-variables.md` and the deployment manifests.
5. If you need to rotate the HS256 `SECRET_KEY` on its own — without migrating to RS256 — the procedure is `docs/SECRET_KEY_ROTATION_RUNBOOK.md`. It covers the `token_version` bump and the session-invalidation announcement that this document does not.

---

## Rollback — if verification fails mid-cutover

**Trigger:** any unexplained 401 spike lasting more than 5 minutes, or any failure of the Step 5 "old-key token is now refused" check, or any boot failure after the algorithm flip.

**The rollback is a single-variable revert, and it is fast:**

```
ALGORITHM=HS256
```

then roll the fleet. Because `ALGORITHM` was the only value changed and the HS256 secret was never removed, reverting restores the previous state completely.

**Order matters — revert completely before diagnosing:**

1. Set `ALGORITHM=HS256` and roll. Do not skip ahead to debugging.
2. Watch the 401 rate fall. A revert that does not reduce 401s within ~5 minutes means the cause is **not** the algorithm — go to "If rollback does not fix it" below.
3. Only once 401s have returned to baseline, investigate.

**Cost of rollback:** every token minted under RS256 becomes invalid. In-flight users are logged out. This is why the cutover is a planned change on a healthy system — the rollback is cheap in effort and expensive only in user sessions.

**Do not roll back by removing the RS256 keys while `ALGORITHM=RS256`.** That produces an instance that refuses to boot.

### If rollback does not fix it

| Symptom | Likely cause | Action |
|---|---|---|
| 401s persist after reverting to HS256 | Not an algorithm problem — check the blacklist and `token_version` | Check `REFRESH_TOKEN_EXPIRE_DAYS`, and whether users were invalidated by a password change |
| Instance will not boot | A key half is missing after an env edit | Re-run Step 2; check the secret actually injected |
| 401s for ~60 s then resolve themselves | The decode-failure cache during a key swap | Expected; wait out the TTL or restart |
| Tokens valid before restart, invalid after | An instance picked up a half-installed key set | Re-check Step 2 on the running config, not the intended one |

---

## The HS256 vs RS256 trade-off

This is a real decision with real costs on both sides. It is argued here rather than assumed.

### What RS256 buys

**A verifier can no longer mint.** With HS256 the verification key *is* the signing key — one shared `SECRET_KEY`. Every service that can verify a token can therefore forge one, and every copy of that secret is a complete authentication bypass. This is precisely why a leaked `SECRET_KEY` is a P0 and not a P2: there is no partial damage available.

RS256 splits this. The private key stays on the issuer; verifiers hold only the public key. A compromised verifier can reject tokens but cannot manufacture them. For a SaaS where the API verifies tokens and may be replicated or fronted by anything else that needs to check identity, this converts a total-compromise failure mode into a denial-of-service one.

**Blast radius shrinks from the secret to the deployment.** Rotating `SECRET_KEY` under HS256 invalidates every session everywhere simultaneously. Rotating an RSA keypair can be staged.

### What RS256 costs

**A key-distribution surface that HS256 does not have.** Under HS256 there is one secret and it is already in the secret store. Under RS256 there is a keypair whose two halves must be generated, matched, stored, distributed, rotated, and eventually destroyed. The mismatch failure (`diff` in Step 1) is a real operational class of outage that simply does not exist with a single symmetric secret.

**Rotation is harder and slower.** Two materials instead of one; the staged-key window in Step 8 exists because of this. Under HS256 a rotation is one value changed in one place.

**Signature cost.** RS256 is asymmetric and slower to sign and verify than HMAC. Measured on this stack the RS256 access token is **495 bytes versus 196 bytes** for HS256 — 2.5× the header, more to transport per request. For a token-bearing API this is measurable but rarely decisive on its own.

**Operational coupling.** The `lru_cache` lag and the 60-second decode-failure cache both bite harder here, because there are now two key materials moving and a real staged window in which they disagree.

### Recommendation

**Migrate to RS256, but not urgently, and not without the operational work in this document.**

The security argument is strong and one-directional: removing the ability to mint from a verifier is a genuine, permanent improvement, and it is the difference between "a leaked secret" being a total bypass and being a denial of service. For a SaaS template whose users will run this in multi-service architectures, shipping HS256 as the default teaches the wrong instinct.

The counterweight is that RS256 is an *operational* commitment, not a config flip — and the operational maturity to hold that commitment is exactly what this repo was missing until now. That is why the recommendation is conditional on the runbook being followed, not on the algorithm alone:

- Do it **with** the full staged procedure above, not as a one-line env change.
- Do it **while healthy**, never during an incident.
- Do it **after** the HTTP-level end-to-end check in Step 6 has been run in a real environment. That check is the one part of this runbook that has not been executed; it should be before the first production cutover, not after.

**Staying on HS256 is defensible** for a single-service deployment that owns its own verification and has no other component needing to check token validity. In that topology the mint-from-verifier concern is largely theoretical, and the key-distribution cost is real money for no gain. The decision should be made per deployment, deliberately — not by inheriting the default.

---

## Recorded execution (2026-09-30)

Run in-process against the pytest environment, single process, throwaway RSA-2048 keypair generated at run time and discarded immediately. Output verbatim:

```
=== Step 1: generate the key pair ===
private.pem: -----BEGIN PRIVATE KEY-----  (1704 bytes)
public.pem:  -----BEGIN PUBLIC KEY-----  (451 bytes)

=== Step 2: config-load fail-fast ===
  private only   -> REFUSED: Value error, RS256 algorithm requires JWT_PRIVATE_KEY and JWT_PUBLIC_KEY
  public only    -> REFUSED: Value error, RS256 algorithm requires JWT_PRIVATE_KEY and JWT_PUBLIC_KEY
  both           -> LOADED

=== Step 3: dual-verify window ===
  issued under HS256: header alg = HS256 (196 chars)
  issued under RS256: header alg = RS256 (495 chars)
  HS token verifies under HS256 config : True
  RS token verifies under HS256 config : False
  RS token verifies under RS256 config : True
  HS token verifies under RS256 config : False

=== Step 4: algorithm confusion ===
  pinned RS256 vs HS256 token -> InvalidAlgorithmError: The specified alg value is not allowed
  unpinned (RS256+HS256) vs HS256 token -> TypeError: Expected a string value

=== Step 5: key rotation and the lru_cache(1) ===
  token minted by NEW keypair verifies under OLD config : False
  JWT_PUBLIC_KEY swapped; cached key object unchanged    : True
  new-key token verifies WITHOUT key-cache clear         : False
  new-key token verifies AFTER key-cache clear (restart) : True
  old-key token verifies AFTER cutover                    : False

=== Step 6: cutover, then the session lifecycle ===
  HS256: login(alg=HS256) verify=True refresh=True rotate=True logout=blacklisted
  RS256: login(alg=RS256) verify=True refresh=True rotate=True logout=blacklisted
```

The behavioural claims in this document are pinned by
`tests/unit/core/security/test_jwt_algorithm_pinning.py`.