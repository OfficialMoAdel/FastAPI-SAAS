# Runbook — purging leaked credentials from git history

**Bead:** `FastAPI_saas-54ua.3` (P2) · **Written:** 2026-10-01
**Status:** 🛑 **NOT EXECUTED AGAINST THE REAL REPOSITORY.** Section 2 is a
decision gate that only the repository owner can answer. The procedure in
section 5 has been *tested end-to-end on a throwaway mirror* (section 6), but
nothing has been pushed, and no ref on `origin` has been rewritten.

> **No secret value appears in this runbook, in any commit message, or in any
> log.** That is deliberate policy, matching the two rotation runbooks. Where
> a command needs a value, it is read from your own secret store or from the
> blocklist at runtime (section 5, step 2).

---

## 1. What this is, and what it is not

Rotating a credential makes the old value useless. That is **already done**:
`FastAPI_saas-54ua.1` (HS256 `SECRET_KEY`) and `FastAPI_saas-54ua.2` (dev
`POSTGRES_PASSWORD`) are closed.

This runbook covers the *other* half: the old values are still sitting in git
history, and anyone who can read the repository can read them. Rotation and
purge are separate jobs. **Purging is optional** — see the decision gate.

**Purging is not a security fix.** It is history hygiene. It cannot un-leak a
value: anyone who cloned before the rotation already has it, and anyone who
scraped it before then has a copy. It only reduces the window for *future*
readers. That framing matters for the cost/benefit below.

---

## 2. Decision gate — answer this before anything else

**Is the repository public, and has it ever been forked or mirrored?**

| Answer | Recommendation |
|---|---|
| **Private, access controlled, never forked** | **Do not purge.** Rotation already revoked the values. A rewrite invalidates 43 refs, every clone, every open PR, and costs ~28% of commit history (section 6). The disruption is real and the security benefit is close to zero. |
| **Public** | Purge, and treat the values as permanently compromised regardless — they must never be re-used anywhere. |
| **Was ever public, or forked/mirrored** | Purging is **ineffective** for anyone who already cloned. Rotation is the only real mitigation, and it is done. Decide whether to purge for *hygiene* only, and expect no security credit. |

This is a business decision, not a technical one. **Get it in writing before
starting.** Nothing below should run until it is made.

---

## 3. What was verified (2026-10-01)

All findings were produced by scanning with values referenced from the
blocklist at runtime; no value is reproduced here.

| Check | Result |
|---|---|
| Leaked values in the **HEAD tree**, outside the blocklist files | **0** — the current codebase is clean |
| `.claude/settings.local.json`, `src/.env`, `site/` | Contain values, but all three are **untracked and gitignored** (`.gitignore:170`, `:147`, `:121`). Not in history. |
| Refs whose **history** still carries a value | **11 of 43** (section 4) |
| Stash entries | 14 |
| Reflog entries | 410 |
| `.git/filter-repo/` | **Present** — a rewrite has run before |
| `refs/original/` | Absent (that is `filter-branch`'s mechanism, not `filter-repo`'s) |

### Residual exposure by design

`src/app/core/config/crypt.py` and `tests/unit/core/test_secret_blocklist.py`
contain the blocklist **as raw 64-char hex values, not hashes**. That is
required for the equality check the validator performs, but it means the
repository still ships the literal values. They are burned (rotated), so this
is low risk, and the verification in section 5 deliberately excludes those
files — otherwise it can never return clean.

---

## 4. Why the previous purge was incomplete

A rewrite **did** run — `.git/filter-repo/` exists. It did not take, because
it only rewrote the branches it was pointed at. Everything else still reaches
the original history:

```
refs/heads/main                 7 occurrences   <- still dirty
refs/heads/mult_dev             7
refs/remotes/origin/main        7
refs/tags/v0.14.0               2
refs/tags/v0.15.0               2
refs/tags/v0.16.0               2
refs/cline/checkpoints/.../1-3  3 each (5 refs)
```

Plus **14 stash entries** and a **410-entry reflog**, which survive a rewrite
of the branches entirely.

**This is the reason the values appear in the same commit before and after
the earlier attempt.** Re-running the same command will fail identically.
Section 5 therefore rewrites **all** refs and clears the ancillary retention.

---

## 5. Procedure — only after section 2 is answered "purge"

### Step 1 — Announce and freeze

Rewriting history invalidates every existing clone. Do not start mid-sprint.

- Announce a coordination window; ask contributors to push nothing.
- Record the pre-state for the rollback check:
  ```bash
  git for-each-ref --format='%(refname)' | sort > /tmp/refs-before.txt
  git rev-list --all --count > /tmp/commits-before.txt
  ```

### Step 2 — Build the replace-text file (do not commit it)

```bash
python - <<'PY'
import os, pathlib
for k, v in dict(ENVIRONMENT='local', SECRET_KEY='0'*64, ADMIN_PASSWORD='x',
                 POSTGRES_USER='postgres', POSTGRES_PASSWORD='postgres',
                 POSTGRES_DB='t', POSTGRES_SERVER='localhost', POSTGRES_PORT='5432',
                 REDIS_QUEUE_HOST='localhost', REDIS_QUEUE_PORT='6379',
                 REDIS_CACHE_HOST='localhost', REDIS_CACHE_PORT='6379').items():
    os.environ.setdefault(k, v)
from src.app.core.config.crypt import KNOWN_LEAKED_SECRET_KEYS
from src.app.core.config.database import KNOWN_LEAKED_POSTGRES_PASSWORDS
vals = [x for x in KNOWN_LEAKED_SECRET_KEYS if x] + \
       [p for p in KNOWN_LEAKED_POSTGRES_PASSWORDS if p]
pathlib.Path("/tmp/replace.txt").write_text(
    "".join(f"literal:{v}==>***REMOVED***\n" for v in vals), encoding="utf-8")
PY
chmod 600 /tmp/replace.txt   # it contains real values
```

**Delete `/tmp/replace.txt` immediately after step 4.** It is a plaintext
copy of the leaked values.

### Step 3 — Dry run on a throwaway mirror (REQUIRED, non-destructive)

Never rehearse on the real repo.

```bash
git clone --mirror "https://github.com/OfficialMoAdel/FastAPI-SAAS.git" /tmp/purge-mirror.git
cd /tmp/purge-mirror.git
git filter-repo --replace-text /tmp/replace.txt --force
```

### Step 4 — Verify the mirror

Every ref must be clean. Expect **0 dirty refs**:

```bash
cd -                     # back to the real repo
python scripts/verify_history_purge.py /tmp/purge-mirror.git   # see section 7
```

### Step 5 — Rewrite the real repository

```bash
cd /path/to/FastAPI-SAAS
git filter-repo --replace-text /tmp/replace.txt --force
```

`filter-repo` drops `origin` as a safety measure. That is expected.

### Step 6 — Clear ancillary retention

This is the step the earlier attempt missed.

```bash
git for-each-ref --format='%(refname)' refs/cline/checkpoints | xargs -r -n1 git update-ref -d
git reflog expire --expire=now --all
git stash clear
git gc --prune=now --aggressive
rm -f /tmp/replace.txt
```

### Step 7 — Force-push every ref

```bash
git push --force --mirror origin        # branches AND tags
git push origin --force --tags          # explicit, because --mirror is easy to misread
```

Delete the `mult_dev` branch from the remote if it is retired; a rewrite
preserves it, and it is one of the dirty refs.

### Step 8 — Contributors re-clone

Existing clones will diverge and cannot be repaired by pulling.

```bash
git remote set-url origin <url>
git fetch origin
git checkout main
git reset --hard origin/main
git reflog expire --expire=now --all && git gc --prune=now   # drop local retention too
```

Anyone with local branches must rebase or abandon them. **Announce this before
starting, not after.**

Open PRs must be closed and reopened against the new history.

---

## 6. What the test run measured (2026-10-01, throwaway mirror)

The procedure was executed against a local `--mirror` clone. The real
repository was **not** modified.

| Measurement | Value |
|---|---|
| Rewrite time | **14 s** (1514 → 1097 commits) |
| Refs in mirror | 43 → 41 (`filter-repo` drops `origin`) |
| Tags preserved | **25 of 25** (`v0.1.0` … `v0.16.0`) |
| Cline checkpoints preserved | 5 (cleaned separately in step 6) |
| Dirty refs after rewrite | **0 of 41** ✅ |
| Real repo after the test | **unchanged** — still 11 dirty refs, as before |

> ⚠️ **Commit count drops 1514 → 1097 (−28%).** `filter-repo` prunes commits
> that become identical to their parent once the replacement text is applied.
> That is expected behaviour, not corruption, but it is a real change to the
> project's history and should be agreed to explicitly.

---

## 7. Rollback

There is **no rollback once pushed.** A force-push is not revertible from the
client side; recovery requires someone with the old objects.

**Before pushing**, take a full mirror so the old history is never lost:

```bash
git clone --mirror <url> /tmp/pre-purge-backup.git
```

Keep that mirror until the project has run a release cycle without incident.
Note that this backup **retains the leaked values** — store it accordingly and
delete it when the retention window closes.

If the rewrite is wrong but **not yet pushed**: `git reset --hard` to the
pre-recorded ref state in step 1.

---

## 8. Why this runbook stops short of running the purge

Two reasons, both deliberate:

1. **The decision gate (section 2) is unanswered.** Repository visibility is
   not knowable from inside the repo. If the project is private with
   controlled access, the recommended action is *do nothing*.
2. **A history rewrite is not an agent's call to make.** It is irreversible
   for every contributor at once and cannot be undone from the client side.

The safe and useful half — establishing *why* the previous attempt failed —
is done and recorded in section 4, so that whoever makes the decision does not
repeat it.