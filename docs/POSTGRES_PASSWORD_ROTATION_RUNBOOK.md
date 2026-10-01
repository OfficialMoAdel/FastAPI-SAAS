# Runbook — rotating the dev `POSTGRES_PASSWORD`

**Bead:** `FastAPI_saas-54ua.2` (P1) · **Written:** 2026-10-01
**Status:** the ROTATION ITSELF IS NOT DONE. Every step marked 🤚 OPERATOR is
outstanding. Nothing in this document has been executed against a live
database.

> **No secret value appears in this runbook, in any commit message, or in any
> log.** That is deliberate policy, matching the SECRET_KEY rotation runbook.
> Where a command needs a value, read it from your own secret store.

---

## 1. Why this exists

The dev database password was committed to git history at
`6cbc8a56` and `af450c42` — the same commit immediately before and after a
`git filter-repo` rewrite. Both commit ids still resolve and the tree still
contains `POSTGRES_PASSWORD` occurrences.

This is lower severity than the HS256 signing key, which was a P0: a leaked
signing key lets anyone mint valid tokens, whereas a leaked dev database
password grants access to one development database. It is still a credential
in repository history, and it is still P1.

**The existing detector working correctly does NOT mean this is clean.**
`KNOWN_LEAKED_POSTGRES_PASSWORDS` (`src/app/core/config/database.py:8-13`)
already refuses the leaked value, and
`validate_no_leaked_password` (`:149-175`) warns on `local` and **raises** on
`staging`/`production`. A developer running locally sees the warning every day
and correctly keeps working — that is by design, because the leaked value is
also the documented local example. Rotation makes the old value useless;
only the separate history-purge bead (`FastAPI_saas-54ua.3`, currently
**blocked** on this one) removes it from the record.

## 2. Scope boundary

**Dev database only.** Do not rotate production credentials from this bead. If
a production credential is found in history, that is a P0 incident requiring
its own bead and its own announcement.

---

## 3. Pre-flight

### 3.1 🤚 OPERATOR — confirm the old password still works

You need this baseline: rotation is unverifiable if you never proved the old
value authenticated.

```bash
# Read the CURRENT value from your own environment; do not paste it anywhere.
psql "postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@$POSTGRES_SERVER:$POSTGRES_PORT/$POSTGRES_DB" -c 'SELECT 1'
```

**Automated:** nothing. This is a manual pre-flight.

### 3.2 🤚 OPERATOR — confirm `.env` is untracked

A rotation is pointless if the new value is then committed. The repo already
enforces this: the `tracked-env-guard` job in
`.github/workflows/security-supply-chain.yml:86-101` fails the build if any
`.env` file other than `*.env.example` is tracked. Confirm it is green on
`main` before you start.

```bash
git ls-files --error-unmatch .env 2>&1 | head -1   # must print "did not match"
```

**Automated:** `tracked-env-guard` in CI.

---

## 4. Generate the new password

### 4.1 🤚 OPERATOR — generate

```bash
# 32+ chars, URL-safe (this value goes into a connection URI).
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Do not echo it into a shell history you share, a CI log, or a bead.

---

## 5. Change the password on the server

### 5.1 🤚 OPERATOR — rotate in place

Connect as a superuser or the owning role, then:

```sql
ALTER ROLE <POSTGRES_USER> WITH PASSWORD '<new value>';
```

Notes:

- `ALTER ROLE ... WITH PASSWORD` takes effect immediately for **new**
  connections. Existing pooled sessions keep the old credentials until they
  reconnect, so see the restart step below — a rolling restart is what makes
  the old value truly dead.
- The app builds its DSN from `POSTGRES_USER` / `POSTGRES_PASSWORD` /
  `POSTGRES_SERVER` / `POSTGRES_PORT` / `POSTGRES_DB`
  (`src/app/core/config/database.py:140-144`), so nothing in the application
  code needs to change. This is a data-layer change only.

**Automated:** nothing.

---

## 6. Update configuration outside version control

### 6.1 🤚 OPERATOR — write the new value

Update `POSTGRES_PASSWORD` in whichever file your deployment reads — `.env`
locally, your secret store in staging/production. Confirm the file stays
untracked (3.2).

**Automated:** nothing.

### 6.2 Append the old value to the blocklist

Once the old value is dead, add it to
`KNOWN_LEAKED_POSTGRES_PASSWORDS` in `src/app/core/config/database.py` so
anyone who still has the leaked value in a local `.env` gets the warning
instead of silently connecting. The comment in that block already anticipates
this:

> When rotating after a leak, append the old value here.

**This step deliberately reintroduces the leaked string into the working
tree.** That is the intended trade: a *known-compromised* value sitting in a
blocklist is inert, while a value nobody tracks is a live credential. Do not
skip it, and do not add the *new* value — that one is fine.

**Automated:** nothing. A test should assert the blocklist is non-empty and
that `validate_no_leaked_password` refuses a member in every environment; if
one does not exist, add it rather than trusting this paragraph.

---

## 7. Restart so the old value stops working

### 7.1 🤚 OPERATOR — restart the fleet

The SQLAlchemy pool holds connections authenticated with the old password.
Without a restart they continue to work, so "the old password no longer
authenticates" is not yet true for the application even though it is true for
`psql`.

**Automated:** nothing. There is no credential-rotation hook in this codebase
to wire into — unlike the JWT signing key, which is `lru_cache`d in two
places (`jwt_service.py:159-166`, `jwt_utils.py:145-182`) and has **no
production caller** for its cache clearers, a database password has no
in-process cache to clear. A restart is the whole mechanism.

---

## 8. Verify

### 8.1 🤚 OPERATOR — the new value works

```bash
psql "postgresql://$POSTGRES_USER:<new value>@$POSTGRES_SERVER:$POSTGRES_PORT/$POSTGRES_DB" -c 'SELECT 1'
```

**Automated:** nothing.

### 8.2 🤚 OPERATOR — the old value no longer works

The same command with the old value must fail with an authentication error.
Do not skip this: it is the actual acceptance criterion, and 3.1 is what makes
it meaningful.

**Automated:** nothing.

### 8.3 🤚 OPERATOR — the application still boots and serves

```bash
uv run python -c "from src.app.core.config import settings; print('config ok')"
uv run pytest tests/unit -q -x
```

**Automated:** the existing test suite. Note that any test relying on the old
value in a committed fixture will now fail — that is a **good** failure, and
the fixture should be updated to read from the environment, not given a new
hard-coded password.

---

## 9. Rollback

If the new value causes a problem before you have announced it, revert:

1. `ALTER ROLE <POSTGRES_USER> WITH PASSWORD '<old value>';`
2. Restore the old value in the secret store / `.env`.
3. Restart the fleet.

**Cost of rollback:** none to users. This is a dev database, and no
application session state depends on the password — unlike the SECRET_KEY
rotation, which invalidates every JWT. There is no announcement step and no
user impact, which is exactly why this rotation is P1 and not P0.

**If the old value is restored, the credential is live again.** If you roll
back, the rotation has not happened; either complete it or record why you
abandoned it.

---

## 10. What this runbook does NOT do

| Not done | Why | Where |
|---|---|---|
| Remove the value from git history | Destructive, and possibly not worth it for a dev DB | `FastAPI_saas-54ua.3` — **blocked on this bead** |
| Rotate any production credential | Out of scope; a P0 incident needs its own bead | — |
| Rotate the HS256 signing key | Separate credential, separate runbook | `docs/SECRET_KEY_ROTATION_RUNBOOK.md` |
| Verify the old value is absent from history | Read-only, safe, and it IS worth doing | Section 11 |

## 11. Follow-up worth doing regardless

Once the value is dead, confirm it survives only as history:

```bash
# Read-only. Expect the old value to be findable in history and NOWHERE in the tree.
git log --all -S"<old value>" --oneline | head
git grep -c "<old value>" -- . ':!src/app/core/config/database.py' | head
```

The second command must return **nothing** outside the blocklist. If it
returns a working-tree hit, a copy is still tracked and needs removing before
the purge bead is worth running.

**Automated:** the `tracked-env-guard` CI job covers the `.env` case; the
committed-file case is not currently gated, which is a real gap.

---

## 12. A concern worth raising about this credential class

`KNOWN_LEAKED_POSTGRES_PASSWORDS` stores the leaked password **in plaintext in
the source tree**, and 6.1 asks you to add another. That is defensible for a
value that is already publicly compromised and is dead the moment rotation
completes — but it is a pattern worth being deliberate about:

- The blocklist grows monotonically and never shrinks, because there is no
  signal for "this leaked value is definitely dead" beyond the rotation record.
- Anyone who reads the source learns every dev password ever used, rotated or
  not. A hash of each value would serve the identical purpose with one
  property this lacks: the source stops being a password oracle.
- There is no test pinning the blocklist's behaviour across environments. The
  environment-dependent raise/warn split at `:166-175` is the kind of logic
  that decays quietly.

This is **not** in scope for the rotation and I have not changed it. Recording
it so the decision is visible rather than inherited.
