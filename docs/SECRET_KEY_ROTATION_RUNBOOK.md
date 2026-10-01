# SECRET_KEY Rotation Runbook

**Version:** 1.0
**Last Updated:** 2026-09-30
**Audience:** Engineering Team, On-Call Engineers
**Criticality:** HIGH — mandatory after any suspected exposure; a leaked HS256 `SECRET_KEY` is a total authentication bypass

**Scope:** Rotating the symmetric HS256 `SECRET_KEY` and invalidating every session that was issued under the old one.

**Tracked by bead:** `FastAPI_saas-54ua.1`

---

## Execution status — read this first

**No rotation was performed while writing this runbook.**

This document was written on a healthy system, against a system with no leaked key in its current configuration. Specifically:

| Step | Status |
|---|---|
| Step 1 — generate a key | ✅ **REHEARSED** with throwaway keys, in-process |
| Step 2 — config refuses a weak or blocklisted key | ✅ **REHEARSED** (tests) |
| Step 3 — `.env` stays untracked | ❌ **NOT EXECUTED** — operator step |
| Step 4 — install the new key in the secret store | ❌ **NOT EXECUTED** — operator step |
| Step 5 — bump `token_version` | ❌ **NOT EXECUTED** — operator step |
| Step 6 — restart the fleet | ❌ **NOT EXECUTED** — operator step |
| Step 7 — announce the forced logout | ❌ **NOT EXECUTED** — operator step |
| Step 8 — verify the old key is gone from every current `.env` | ❌ **NOT EXECUTED** — operator step |
| Step 9 — post-rotation verification | ❌ **NOT EXECUTED** — operator step |
| History purge (out of scope, bead `54ua.3`) | ❌ **NOT EXECUTED** — separate bead, see below |

**Nothing here touched a real credential.** No secret was generated into a file, no `.env` was read, no environment was modified, and no git history was rewritten. Every key used to validate this procedure was produced inside the test process by `secrets.token_hex(32)` and discarded when the test ended.

**The history purge is explicitly out of scope.** Purging the leaked value from git history is bead `FastAPI_saas-54ua.3` ("Write and run a history-purge runbook"), referenced in Step 8(b) and listed under "What this runbook does not cover". Step 8 only establishes that the value is absent from *current* configuration; removing it from *history* is that bead's job and must not be conflated with rotating it. Rotating first is correct — rotation is what makes the leaked value useless; the purge is what stops it being read.

---

## ⚠️ Read this before you touch anything

### Why this is P0, not housekeeping

Under HS256 the verification key **is** the signing key. One `SECRET_KEY` both mints and validates. So a leaked `SECRET_KEY` is not "someone can read a few tokens" — it is **anyone who has the value can mint a token for any user, including an administrator, with no credential, no password reset, and no rate limit that matters.** There is no partial damage available.

Two leaked values are recorded in this repo's history: one committed as `SECRET_KEY` (`src/.env.local` + `.env.txt`) and two shipped in tracked `scripts/*/.env.example` files. All three are permanently blocklisted in `src/app/core/config/crypt.py:21-27`, and config load refuses any of them.

### The trap that will cost you an hour

**Installing a new key does nothing until the process restarts.**

Both key accessors are memoised per process:

- `jwt_service._get_signing_key()` — `@lru_cache(maxsize=1)`, `src/app/core/security/jwt_service.py:159-166`
- `jwt_utils._get_parsed_verification_key()` — `@lru_cache(maxsize=1)`, `src/app/core/security/jwt_utils.py:145-182`

After you change `SECRET_KEY` in the environment, the running process keeps signing **and** verifying with whatever it cached on first use. Nothing reloads. There is no admin endpoint, no SIGHUP handler, and no config watcher that calls `clear_signing_key_cache()` or `clear_jwt_key_cache()`; the only callers are the autouse test fixture at `tests/conftest.py:328-329` and its teardown at `tests/conftest.py:338-339`, and `jwt_utils.py:118-124` documents the sentinel clearer as *"no production callers"*. **A restart is the only supported mechanism.**

The failure looks like "the rotation silently failed". An engineer installs the new key, watches verification keep failing against old tokens exactly as before, and concludes the new key is broken. It is not broken; it has simply never been loaded. Observed in the rehearsal:

```
verification key still serving the pre-rotation value, cache not cleared : True
token signed with the NEW key verifies WITHOUT a cache clear              : False   ← operator's symptom
token signed with the NEW key verifies AFTER the cache clear (restart)     : True    ← same key, one restart later
```

**A second trap, one level down.** `jwt_utils` also memoises decode *failures* for 60 seconds (`_decode_failure_cache`, `jwt_utils.py:102-106`), keyed by token hash. Once a token is refused as a permanent failure, every subsequent attempt with that same token string short-circuits to `None` **without running any crypto**. So during the window between "new key installed" and "process restarted", a token you already tested and saw fail keeps failing. A restart clears this too. Do not read a stale `None` as evidence that the new key is wrong.

---

## What is already correct (do not re-derive this)

Verified against the source on 2026-09-30:

| Property | Where | Confirmed |
|---|---|---|
| `SECRET_KEY` rejected when absent or empty | `crypt.py:66-70` | ✅ |
| `SECRET_KEY` refused if it matches the committed-leak blocklist | `crypt.py:21-27`, `crypt.py:72-75` | ✅ |
| HS256 requires `SECRET_KEY` ≥ 32 characters | `crypt.py:85-92` | ✅ |
| That minimum is enforced **unconditionally**, in every environment | `crypt.py:63-93` (no environment branch; only `validate_production_secrets` at `crypt.py:95-130` branches, and it covers a different concern) | ✅ |
| A bad `SECRET_KEY` fails at **config load**, not at first request | `crypt.py:63-93` runs as a pydantic `model_validator(mode="after")` on `CryptSettings` | ✅ |
| `token_version` is compared on every verification, so bumping it refuses outstanding tokens | `jwt_service.py:410-456` | ✅ |
| A `token_version` lookup that raises rejects the token (fail-closed) | `jwt_service.py:446-451` | ✅ |
| `ensure_jwt_lookups_registered()` aborts startup when the lookup seam is unwired | `jwt_service.py:141-156`, wired at `composition.py:67-79` and `composition.py:345` | ✅ |
| Signing uses the same key the verifier checks | `jwt_service.py:229` and `jwt_utils.py:182` both read `settings.SECRET_KEY` | ✅ |
| `.env` files are gitignored | `.gitignore:101-105`, `.gitignore:147-148`, `.gitignore:209` | ✅ |
| CI fails the build if any `.env` file is tracked | `.github/workflows/security-supply-chain.yml:86-101` (`tracked-env-guard`) | ✅ |
| Nothing in `src/` calls the key-cache clearers outside their own definitions | `jwt_utils.py:122` says so in its own docstring: *"no production callers"* — confirmed by search | ✅ |

Two notes for the record, because both are easy to get backwards:

1. **The blocklist is a leak list, not a retirement list.** After rotating, append the old value only if it is being retired *because of an exposure*. Rotating on a routine schedule does not make the previous value "leaked", and blocklisting it would make the key unusable for any environment still holding it during a staged rollout.
2. **Bumping `token_version` is not optional once the key changes**, because the two mechanisms invalidate different things. The key rotation invalidates tokens whose *signature* no longer verifies. `token_version` invalidates tokens whose *claims* no longer match the user record — and it is the one that also kills sessions on the deployment where an old process is still signing with the old key. Do both. Step 5.

---

## Step 1 — Generate the new key 🤚 OPERATOR

Generate **outside the application**, on a machine whose shell history and logs you trust. Never generate a signing key inside a container whose output is scraped.

```bash
openssl rand -hex 32
```

This yields 64 hex characters. Do not write it to a repo file, a pastebin, an issue, or a chat message. Take it straight to the secret store (Step 4).

> The blocklist is checked for **exact match**, so any value that was ever committed stays refused forever. Generating rather than reusing is not optional.

---

## Step 2 — Confirm the new key passes config validation 🤚 OPERATOR

Do this **before** touching a running environment. `CryptSettings` validates on load, so this is a fail-fast check that costs one command.

**Note on the loader.** `crypt.py` has no imports from its sibling settings modules, but importing it as `src.app.core.config.crypt` runs the package `__init__.py`, which constructs the full `AppSettings` and therefore demands `POSTGRES_USER`, `POSTGRES_PASSWORD` and friends. To exercise the crypt validator in isolation, load the module by file path as below. This is exactly what the test suite does, so what you check here and what CI checks are the same code.

```bash
cat > /tmp/crypt_check.py <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("_crypt", "src/app/core/config/crypt.py")
m = importlib.util.module_from_spec(spec)
sys.modules["_crypt"] = m
spec.loader.exec_module(m)
try:
    m.CryptSettings()
    print("LOADED")
except Exception as exc:
    print("REFUSED:", str(exc).splitlines()[1].strip()[:140])
PY

# 1. A fresh key must LOAD.
SECRET_KEY="$(openssl rand -hex 32)" ALGORITHM=HS256 .venv/Scripts/python.exe /tmp/crypt_check.py
#    expected: LOADED

# 2. A short key must be REFUSED.
SECRET_KEY="$(openssl rand -hex 8)" ALGORITHM=HS256 .venv/Scripts/python.exe /tmp/crypt_check.py
#    expected: REFUSED: Value error, SECRET_KEY must be at least 32 characters for HS256.

# 3. A leaked key must be REFUSED. Read one entry off the blocklist; never paste a
#    leaked value into a terminal, a file or a ticket.
.venv/Scripts/python.exe -c "
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location('_crypt', 'src/app/core/config/crypt.py')
m = importlib.util.module_from_spec(spec); sys.modules['_crypt']=m; spec.loader.exec_module(m)
os.environ['SECRET_KEY'] = sorted(m.KNOWN_LEAKED_SECRET_KEYS)[0]
os.environ['ALGORITHM'] = 'HS256'
try:
    m.CryptSettings(); print('NOT REFUSED — the blocklist is not working, stop')
except Exception as exc:
    print('REFUSED:', str(exc).splitlines()[1].strip()[:140])
"
#    expected: REFUSED: Value error, SECRET_KEY matches a value that leaked into git history.

rm -f /tmp/crypt_check.py
```

Both refusals above were observed verbatim while writing this document. If a key that should be refused loads, **stop** — that instance would boot and mint tokens under a credential you know is compromised.


Automated equivalent: `tests/unit/core/security/test_jwt_key_rotation.py::test_a_replacement_key_that_is_too_short_is_refused` and `::test_a_replacement_key_on_the_leaked_blocklist_is_refused`, plus `tests/unit/core/test_secret_blocklist.py`.

---

## Step 3 — Confirm no `.env` file is tracked 🤚 OPERATOR

The repo has a CI job for this: **`tracked-env-guard`** ("Guard against tracked .env files (C-1)") in `.github/workflows/security-supply-chain.yml:86-101`. It runs on push and pull request and nightly, fails the build if any `.env` file other than `*.env.example` is tracked, and its error message already points at `git filter-repo`.

Check it locally before you push, because a leaked key that reaches the repo is leaked for good:

```bash
git ls-files | grep -E '(^|/)\.env(\.|$)' | grep -v '\.env\.example$' && echo "STOP: tracked .env file(s) above"
git status --porcelain --ignored | grep -E '\.env$' # should show them as ignored, not untracked-new
```

Expected: no output from the first command, and every `.env` listed as `!!` (ignored) by the second.

**Automated:** the `tracked-env-guard` CI job. This step is a local pre-flight for the same check.

---

## Step 4 — Install the new key 🤚 OPERATOR

1. Write the Step 1 key into the secret store (Vault / AWS Secrets Manager / SOPS-encrypted file) under the existing `SECRET_KEY` path. Do not create a second key name — a second variable would not be read, and the old one would keep working.
2. **Keep the old value available** until Step 6 completes. You do not need it to keep working, but you may need it to roll back, and you will need its fingerprint to confirm it is gone in Step 8.
3. Do **not** restart anything yet. The fleet is still healthy and still serving; nothing has changed for users.

Record the rotation in the change log with the **fingerprint** of the old key (a hash of it), never the value itself.

---

## Step 5 — Bump `token_version` 🤚 OPERATOR

The key rotation invalidates by signature. `token_version` invalidates by claim, and it is what forces re-auth for users whose tokens were minted by an instance that has not yet restarted. Together they close the window a rolling deployment leaves open.

**Do not hand-write SQL.** `token_version` is owned by one seam, and it also updates the Redis copy — which is what verification actually reads on the hot path (`jwt_service.py:421-435`). A raw `UPDATE users SET token_version = token_version + 1` bumps the database and leaves Redis serving the old value for up to 300 s, which silently re-admits every token you just invalidated.

Route through the seam:

```python
from src.app.modules.users.session_invalidation import (
    InvalidationReason,
    invalidate_user_sessions,
)

# All users, one transaction. commit=True so the DB bump is durable.
await invalidate_user_sessions(
    db,
    user_ids,                        # list of user ids
    InvalidationReason.CREDENTIAL_CHANGE,
    commit=True,
)
```

The seam (`src/app/modules/users/session_invalidation.py:325-429`) performs the atomic `UPDATE … RETURNING`, reads the fresh post-bump versions back, commits, and writes the fresh version into Redis under both the username and the email key form.

**Which reason to use.** `should_bump_token_version` (`session_invalidation.py:41-57`) treats `CREDENTIAL_CHANGE`, `DELETION`, `TIER_CHANGE` and `DEACTIVATION` as bump-class, and `PROFILE_EDIT` as not. A fleet-wide key rotation is a credential change for every user at once, so `CREDENTIAL_CHANGE` is the honest reason. Do not invent a new enum member for this; the policy is tested and a new reason would have to be added to `_BUMP_REASONS` deliberately.

**Ordering.** Bump **before** the restart (Step 6). A restart with a new key but an un-bumped `token_version` still leaves any token minted by a not-yet-restarted instance valid.

**Automated:** the policy itself is pinned by `tests/unit/core/security/test_jwt_key_rotation.py::test_credential_and_deactivation_are_bump_class_reasons`. The execution is an operator action.

---

## Step 6 — Restart the fleet 🤚 OPERATOR

Roll the deployment so every process picks up the new key. This is the step that makes Steps 1-5 take effect.

**Watch the 401 rate and the auth error rate for the whole roll.** A rising 401 count during the roll is expected — it is the old-key tokens dying — but it must return to baseline within one access-token lifetime (`ACCESS_TOKEN_EXPIRE_MINUTES`, 30 minutes by default) once every process is on the new key. A 401 rate that stays elevated **after** the roll is finished is not expected: re-run the Step 9 verification.

If you cannot restart, **stop and escalate.** There is no supported in-process rotation path, and building one under incident pressure is how the 60-second decode-failure cache and the two `lru_cache` layers get half-fixed and left in a worse state than before.

---

## Step 7 — Announce the forced logout 🤚 OPERATOR

**Every user session dies. All of them, everywhere, at once.** Both halves of this rotation are fleet-wide:

- The new `SECRET_KEY` means every previously issued token fails signature validation.
- The `token_version` bump means every previously issued token fails its claim comparison.

There is no partial degradation and no "some users will be fine". Refresh tokens (7 days by default) are not spared: they are signed with the same key and carry the same `token_version`, so **users will be asked to log in again, not merely to refresh silently.** Any in-flight password-reset or email-verification link also dies with the key, because those tokens are digested with a pepper derived from `SECRET_KEY` (`src/app/core/security/tokens.py:56-63`).

Before you restart (Step 6), put this in front of users:

1. **Advance notice**, in the channel users actually read, naming the window in UTC.
2. **The window itself**, stated as: *all users will be signed out and must sign in again.* Not "sessions may expire". Not "you may need to log in again". They will.
3. **What is affected**: web sessions, API clients holding a refresh token, the admin panel session, and any MFA-enrolled device's pending enrolment.
4. **What they must do**: sign in again; re-register any MFA device if it was enrolled during the window; request a new password-reset or verification email if one is in flight.
5. **What is NOT affected**: account data, passwords, billing, API keys you created earlier (those are stored under their own digests — but see the caveat below), and anything not authenticated by this `SECRET_KEY`.

> **Caveat worth stating to support, not to users.** `SECRET_KEY` is also the pepper for stored token digests (`tokens.py:56-63`). The lookup path is deliberately dual-digest for the *bare-SHA-256 → peppered* format change (`token_digest_candidates`, `tokens.py:87-100`, used by magic links, email verification, password reset and API keys), but that bridge covers exactly one prior format. Digests written under a *previous pepper value* are not in `token_digest_candidates`, so outstanding magic links, verification links, password-reset links and API keys minted before this rotation stop resolving. Expect support tickets about "my reset link doesn't work" and have the answer ready: re-issue, do not debug.

**Automated:** none. This is a communication step and the bead's requirement that it be explicit. There is no code that can tell a user their session died before it happens.

---

## Step 8 — Verify the old key is gone from current configuration 🤚 OPERATOR

You need to show two different things, and they are easy to conflate.

**(a) Absent from every current `.env` and every current deployment variable.**

```bash
# The fingerprint of the OLD value, recorded in Step 4. SHA-256 of the key
# itself — publishable, and it lets you compare without either key on screen.
OLD_SECRET_KEY_FINGERPRINT="$(.venv/Scripts/python.exe -c 'import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$OLD_SECRET_KEY")"

# Show every .env that still defines SECRET_KEY. The value is redacted: this
# check is about WHICH files, not what is in them.
for f in $(find . -name '.env*' -not -path './.venv/*' -not -path '*/node_modules/*'); do
  if grep -qs '^\s*SECRET_KEY' "$f"; then echo "== $f"; grep -s '^\s*SECRET_KEY' "$f" | sed 's/=.*/=<redacted>/'; fi
done

# Then confirm the key the process actually loaded is not the old one, without
# printing either value.
ENVIRONMENT=local SECRET_KEY="$NEW_SECRET_KEY" ADMIN_PASSWORD='<from your store>' \
POSTGRES_USER='<from your store>' POSTGRES_PASSWORD='<from your store>' \
  .venv/Scripts/python.exe -c "
import hashlib, os
old = os.environ['OLD_SECRET_KEY_FINGERPRINT']
from src.app.core.config import settings
cur = hashlib.sha256(settings.SECRET_KEY.get_secret_value().encode()).hexdigest()
print('ROTATED' if cur != old else 'STILL THE OLD KEY — not rotated')
"
```

Expected: `ROTATED`. Also confirm the same in your secret store's version history — the old revision may still be the one any rollback would restore.

**(b) Present in history only — and that is the *next* bead, not this one.**

The leaked value is still in git history. Rotating does not remove it from history; it makes it worthless, because nothing trusts it any more. **Removing it from history is bead `FastAPI_saas-54ua.3`** ("Write and run a history-purge runbook") and must be done with `git filter-repo` plus a force-push and a re-clone by all contributors. Do not attempt it inline here.

Order matters and is not negotiable:

1. **Rotate first** (this runbook). Until nothing trusts the value, purging it changes nothing observable.
2. **Purge second** (`54ua.3`). Once it is gone from every working tree, rewriting history is safe because no environment needs to copy the old value out of it.

Attempting the purge first is the more common mistake, and it is worse than doing nothing: it is disruptive, it forces every clone to be discarded, and if the rotation then slips, the leaked value is *still* in history *and* your team has lost a week.

**Automated:** none for (a) or (b); both are operator actions over real environments and git.

---

## Step 9 — Post-rotation verification 🤚 OPERATOR

Run all of these. A partial pass is not a pass.

```bash
# 1. The app boots and serves. The startup gate proves the token_version lookup
#    is wired — an unwired one aborts boot rather than flooding with 401s.
curl -fsS https://<host>/api/v1/health/live

# 2. A token minted under the OLD key is refused. Take a real pre-rotation token,
#    or any token string you know predates the restart.
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer <old token>" \
  https://<host>/api/v1/users/me
#    expected: 401

# 3. A FRESH login works end to end — this is the counter-test, and it is the one
#    that catches a rotation that broke normal operation instead of enabling it.
curl -fsS -X POST https://<host>/api/v1/account/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"<a real account>","password":"<a real password>"}' \
  | .venv/Scripts/python.exe -c "import sys,json; print('LOGIN OK' if 'access_token' in json.load(sys.stdin) else 'LOGIN BROKEN')"

# 4. That fresh token authenticates. (/users/me is the authenticated
#    current-user read; there is no /account/me route.)
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer <fresh access token>" \
  https://<host>/api/v1/users/me
#    expected: 200

# 5. Refresh works, so the 7-day lifecycle is intact and not merely reset.
curl -fsS -X POST https://<host>/api/v1/account/refresh \
  -H 'Content-Type: application/json' -d '{"refresh_token":"<fresh refresh token>"}' \
  | .venv/Scripts/python.exe -c "import sys,json; print('REFRESH OK' if 'access_token' in json.load(sys.stdin) else 'REFRESH BROKEN')"
```

Automated equivalent of checks 2 and 4, in-process: `tests/unit/core/security/test_jwt_key_rotation.py`. Checks 1, 3 and 5 need a real deployment and are **not** covered by the test suite.

**Also confirm, in the logs:** the `jwt_lookups_startup_check_failed` error (`composition.py:78`) is absent. If the `token_version` lookup seam were unwired, the app would have refused to boot — that is the fail-fast guarantee — but a boot that succeeded after a *partial* rollout can still leave you with a process whose lookups were reset. `ensure_jwt_lookups_registered` is called once at startup (`composition.py:345`), and it is the reason a botched rotation surfaces as a boot failure rather than as a silent flood of 401s.

---

## Rollback — if the rotation breaks login for everyone

**Trigger:** any of Step 9's checks failing after the fleet has fully rolled, or a 401 rate that does not return to baseline within one access-token lifetime.

**The rollback is a single-variable revert, and it is fast:**

1. Restore the **old** `SECRET_KEY` in the secret store.
2. Revert the `token_version` bump if you want the old sessions back — otherwise users simply sign in again under the restored key, which is cheaper than trying to un-bump a column for every row.
3. Roll the fleet again. Step 6's restart is what makes the restore take effect, for the same reason the rotation needed it.
4. Re-run Step 9. Expected: everything green, and outstanding tokens valid again **if** you also reverted the bump.

**Cost of rollback:** the exposure comes back. A rollback is a decision to accept a known-compromised credential for a bounded period in exchange for restored availability — take it only with that trade stated out loud, and put a hard deadline on it.

**Do not roll back by reverting only one of the two steps.** Restoring the old key while leaving `token_version` bumped gives you a working login flow and a permanently logged-out user base; reverting the bump while leaving the new key gives you back exactly the tokens an attacker who holds the leaked key can also mint. Both are worse than the full revert.

---

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| New key installed, old tokens **still** verify | Process has not restarted; `lru_cache(1)` still serving the old key | Re-do Step 6. This is not a broken key. |
| New key installed, a token you already tested still fails | 60-second decode-failure sentinel (`jwt_utils.py:102-106`) | Restart, or wait out the TTL. Do not conclude the key is bad. |
| Instance will not boot after the change | The new key is too short, empty, or on the leak blocklist | Re-run Step 2 against the **running** config, not the intended one |
| Boot fails with "JWT security lookups unwired" | The `token_version` DIP seam was not registered | `composition.py:67-79`; check `register_users_security_lookups` ran before `jwt_gate` |
| Users are logged in but every API call 401s | Refresh succeeded, access token minted under the old key by a non-restarted process | Finish the roll (Step 6); the bump in Step 5 will log those out anyway |
| Password-reset / magic / verification links stopped working | Those digests are peppered with `SECRET_KEY` (`tokens.py:56-63`) and the old-pepper rows are not in `token_digest_candidates` | Expected. Re-issue the link. See the Step 7 caveat. |
| 401s ~60 s after a key fix, then resolve | The decode-failure sentinel | Expected; harmless once the process restarts |
| A user's session survived the rotation | Their `token_version` was not bumped — the raw-SQL mistake in Step 5 | Bump through the seam; do not write SQL |

---

## What this runbook does not cover

- **Git history purge.** Bead `FastAPI_saas-54ua.3`. Cross-referenced in Step 8, deliberately not performed here.
- **The leaked dev `POSTGRES_PASSWORD`.** Bead `FastAPI_saas-54ua.2`. It has its own blocklist (`database.KNOWN_LEAKED_POSTGRES_PASSWORDS`) and its own blast radius; rotating it does not rotate `SECRET_KEY` and vice versa.
- **Moving to RS256.** `docs/RS256_MIGRATION_RUNBOOK.md`. That migration *eliminates* this exposure class rather than responding to it — under RS256 a verifier can no longer mint. If you are choosing between rotating HS256 and migrating, read that document's trade-off section first; it argues the migration is the better answer, and this runbook is what you do if you cannot make that decision now.
- **Any real environment.** Nothing in this document was executed against a running server, a real secret store, or a real user base.

---

## The behavioural claims above are pinned by

`tests/unit/core/security/test_jwt_key_rotation.py` — rotation invalidating old tokens, `token_version` invalidation and its causality, the `lru_cache` restart lag, the decode-failure sentinel, and the config-level refusals — with the general blocklist policy in `tests/unit/core/test_secret_blocklist.py` and the startup gate in `tests/unit/core/test_boot_gates.py`.