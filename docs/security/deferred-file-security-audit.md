# Deferred-file security audit — the seven files that were never read

**Bead.** `FastAPI_saas-sj09.2`. **Date**: 2026-09-30.
**Predecessor.** [`router-auth-audit.md`](router-auth-audit.md) (bead `sj09.1`),
[`SECURITY_REVIEW_P1-2_PASSKEY_HASH_INDEX.md`](SECURITY_REVIEW_P1-2_PASSKEY_HASH_INDEX.md).

**Tool.** The same detector of record —
[`scripts/audit_router_auth.py`](../../scripts/audit_router_auth.py), AST not
text. It was re-run against the three router-shaped files in this set rather
than reimplemented; its verdict on them is quoted verbatim below.

```bash
python scripts/audit_router_auth.py --json   # route table + idiom signals
```

**Bottom line.** All seven files exist at the paths the bead names; none has
moved. Six are **checked and clean** against a named property, one carries a
**defect recorded below** (not fixed — see *Findings*). The honest position the
bead insists on is preserved per file below: *no defect found* is recorded
separately from *ruled out*, and the two never collapse into one another.

---

## The questions asked

`sj09.2` refers to "the four questions from the router-audit bead". Those are
not in `router-auth-audit.md`; they are in the `sj09.1` bead text, which is the
method record for that audit. Quoted (`CHECK THE FOUR QUESTIONS PER MUTATING
HANDLER`):

| # | Question |
|---|---|
| a | Any authentication idiom present? |
| b | If authenticated only by `get_current_user` with no permission slug, can a low-privilege holder do it? (P1-1 shape.) |
| c | Does it take `ctx` but **not** pass `ctx.tenant_id` to the service? (P0-1 shape.) |
| d | Does it take a tenant, user, or role id from path or query and pass it onward unchecked? |

Plus the three auth-specific questions from `sj09.2`:

| # | Question |
|---|---|
| e | Is the factor mutation behind step-up verification? |
| f | Is the pending / one-time token genuinely single-use — **structural**, not check-then-use? |
| g | Is the lockout counter keyed on something the **attacker** does not control? |

For the four *service* files (passkey, mfa, step_up, sso_settings) questions
a–d are re-posed against the **route** that reaches them, because a service has
no idiom of its own; the route is the authenticated surface and the service is
where the tenant/ownership predicate lives. This is the split
`router-auth-audit.md` already records for the PARTIAL class.

### Cross-reference

`billing/webhook/handlers/checkout.py` was the subject of `dxub.7` ("Webhook
checkout handler must re-resolve tenant from DB, not metadata"), **closed**.
Its fix is present in the file and its key test exists
(`tests/e2e/modules/billing/test_checkout_tenant_authority_e2e.py`). This audit
does not re-litigate `dxub.7`; it confirms the fix landed and then asks the
questions `dxub.7` did not.

---

## Per-file verdicts

Every clean verdict below names (1) the property, (2) how it was checked, and
(3) the existing test that covers it — or records the absence of one as a
coverage gap, which is itself a finding.

### 1. `src/app/modules/auth/services/passkey.py` (720 lines) — CHECKED AND CLEAN

| Q | Answer | Evidence |
|---|--------|----------|
| a | Authenticated, idiom 1 (`ctx: RequestContextDep`) | `routers/passkey.py:57,76,95,109` |
| b | No permission axis is the right gate — a credential belongs to its owner | `services/passkey.py:260`, `:173` |
| c | `ctx.tenant_id` stamped on the written row; fail-closed without it | `services/passkey.py:211-213` |
| d | `credential_id` path param is ownership-checked before use | `services/passkey.py:256-261` |
| e | Registration **and** deletion both sit behind step-up | `routers/passkey.py:63,82,120` |
| f | Challenges are consumed by atomic GETDEL, not checked-then-used | `services/passkey.py:528`, `:591` |
| g | n/a — no lockout counter in this file | — |

**Property checked: a registration challenge minted for user A cannot be
redeemed by user B.** Method: read `verify_registration` and traced the
`stored_data["user_id"] != user_id` comparison at `services/passkey.py:173`
against `user_id = get_user_attr(user, "id")` (`:166`), i.e. the *caller's*
identity, never a body field. **No test existed** — `test_passkey_service.py`
mocks `_get_stored_challenge` to return the *same* user in every case
(`:303`, `:357`). That absence is the coverage gap; it is now covered by
`tests/unit/modules/auth/test_deferred_security_verdicts.py`.

**Property checked: a credential id belonging to another user 404s, never 403.**
Method: read `delete_credential` (`services/passkey.py:245-263`) — the
unscoped PK read at `:256` is followed by the explicit `cred.user_id !=
user_id` soft-404 at `:260`, deliberately not `require_resource_ownership`.
Covered by `tests/unit/modules/auth/test_passkey_service.py:441`
(`test_delete_credential_verifies_ownership`) and `:463`.

**Property checked: tenant is stamped on the row and a missing tenant fails
closed.** Method: read `verify_registration:208-216` — `owner_tenant` comes from
the registering principal, and `ValidationError` is raised **before**
`_store_credential` when it is `None`. Covered by
`tests/unit/modules/auth/test_credential_tenant_isolation.py:52`
(`test_passkey_credential_create_requires_tenant`) and
`tests/unit/modules/auth/test_step_up_reauth.py:244`
(`test_credential_deletion_requires_step_up`).

**Ruled out vs. not found.** The cross-tenant and cross-user paths are
**ruled out by reading** — each has an explicit, unconditional predicate. The
challenge-binding path was **not found clean** until this audit added the test;
before that it was unverified.

### 2. `src/app/modules/auth/services/mfa.py` (1036 lines) — **DEFECT FOUND**

The three factors of the strongest authentication control in the product. One
defect, recorded in *Findings* below; the rest of the file is clean.

| Q | Answer | Evidence |
|---|--------|----------|
| a | Authenticated, idiom 1 | `routers/mfa.py:64,94,126,161,194,215` |
| b | Self-scoped: every method keys on `ctx.user_id` | `routers/mfa.py:149-153,182-188,238-244` |
| c | `enable_mfa` stamps `owner_tenant`, fail-closed | `services/mfa.py:362-367` |
| d | No tenant/user id is accepted from path or query anywhere | whole file |
| e | Factor **removal** requires password + a verified second factor; **addition** requires step-up at the route | `services/mfa.py:746,761,866,881`; `routers/mfa.py:83,115` |
| f | Pending token consumed by GETDEL | `services/mfa.py:197` → `pending_token_store.py:59` |
| g | Lockout keyed `mfa_attempts:{user_id}` from `ctx.user_id` | `services/mfa.py:431,438`; `budgets.py:261` |

**Property checked: the lockout counter cannot be keyed by attacker input.**
Method: traced all four `_check_mfa_lockout` call sites — `enable_mfa:339`,
`verify_totp_token:503` — and their two entry points: `routers/mfa.py:149,182,238`
(all pass `ctx.user_id`) and `complete_login:187` (passes `token_data["user_id"]`,
server-minted at challenge time in `tokens.py:471-478`, **not** from the
request body). The key template `mfa_attempts:{user_id}` is pinned
declaratively by `tests/unit/core/security/test_security_budget_contract.py:413`
and the door-to-lockout chain by `:417`. **Ruled out.**

**Property checked: the pending token is single-use.** Method: read
`complete_login:150` (`peek`, non-destructive) and `:197` (`consume`), then read
`PendingTokenStore.consume` (`pending_token_store.py:47-67`) and the `get_delete`
Lua script (`lua_manager.py:326-334`) — `GET` then `DEL` in one server-side
script, so the two-requests race is structurally impossible rather than a
check-then-use window. The consuming side is covered by
`tests/unit/modules/auth/test_mfa_login_completion.py:227`
(`test_consume_miss_prevents_lookup_and_issuance`) and `:214`
(`test_failed_verification_preserves_pending`). **Ruled out — but see Finding 1,
which is about what happens *before* the consume.**

**Property checked: a cross-user MFA row is a 404, never a foreign update.**
Method: read the two unscoped PK reads — `db.get(MFAMethod, ...)` at
`services/mfa.py:534` and `:890` — and the explicit
`mfa_method.user_id != user_id → ModelNotFoundError` at `:535` and `:891`.
Covered by `tests/unit/modules/auth/test_credential_tenant_isolation.py:136`
(`test_verify_404_on_foreign_row`). **Ruled out.**

**Property checked: an MFA setup token cannot be redeemed by another user.**
Method: read `enable_mfa:329-335`. Covered by
`tests/unit/modules/auth/test_round2_auth_security.py:70`
(`test_setup_token_wrong_user_rejected`). **Ruled out for the format the code
writes**; see Observation 1 for the legacy-format branch.

**Property checked: TOTP replay within the validity window is refused on
login.** Method: read `verify_totp_token:519-529` — the used-code marker is
`SET NX EX` with `TOTP_REPLAY_MARKER_TTL = (2*SKEW+2)*30 = 120s`
(`:61`), which covers the full ±1-interval verification window. Covered by
`tests/unit/modules/auth/test_round2_auth_security.py:22` and
`tests/unit/modules/auth/test_mfa_service.py:828`. **Ruled out on the login
door.** Cross-endpoint reuse is **not ruled out** — see Observation 2; it is
already a documented residual at `services/mfa.py:473-479` and bead `aul0.2`
records it as a deliberate, reverted decision, so it is **not re-raised here**.

**Coverage gap recorded (finding in its own right, per the bead).** There is no
test that a *lockout failure* cannot be induced by pointing `user_id` at another
account, because no caller can. What there is instead is the *declarative* pin.
Acceptable — recorded so the next reader does not re-derive it.

### 3. `src/app/modules/auth/services/step_up.py` (121 lines) — CHECKED AND CLEAN

| Q | Answer | Evidence |
|---|--------|----------|
| e | This file **is** the step-up verification point | whole file |
| f | n/a — no token minted here | — |
| g | n/a — no counter here | — |

**Property checked: the step-up principal is the authenticated one, never the
body.** Method: read `verify_step_up_password:48-79` — `user_id` is a
parameter with the docstring stating "never taken from the body"
(`:53`); only `password` arrives from the request. Every call site passes
`ctx.user_id`: `routers/mfa.py:83,115`, `routers/passkey.py:63,82,120`.
Covered by `tests/unit/modules/auth/test_step_up_reauth.py:179`
(`test_wrong_password_does_not_reach_service`, parametrized over both passkey
routes) and `:271,298` (the MFA pair). **Ruled out.**

**Property checked: step-up failure is not an account-existence oracle.**
Method: read `:63-79` — all three failure modes (missing password, unknown or
inactive subject, wrong password) raise the **same** message `_INVALID_STEP_UP`
(`:45`) and differ only in the internal `reason=` for logs. Covered by
`tests/unit/modules/auth/test_step_up_reauth.py:138`
(`test_failures_are_indistinguishable`), which asserts both halves: identical
messages, different internal reasons. **Ruled out.**

**Property checked: the password is not reconstructable from a proxy access
log.** Method: read `routers/passkey.py:120-122` and `routers/mfa.py:83,115` —
the password travels in the request **body** (`StepUpRequest` /
`MFAEnableRequest`), never a header, by the explicit decision documented at
`services/step_up.py:19-22`. The route-level tests post it as JSON
(`test_step_up_reauth.py:195,236,258`), which is the observable half of this
property. **Ruled out.**

**Property checked: password change / reset revokes every passkey.**
Method: read `revoke_user_passkeys:100-118` — the DELETE is filtered by
`PasskeyCredential.user_id == user_id`; the table is never wiped. Called from
`services/passwords.py:77` (change) and `:355` (reset). Covered by
`tests/unit/modules/auth/test_passkey_revocation.py:32`
(`test_scoped_to_the_user`) and the flag-off / DB-failure paths at `:56,76`.
**Ruled out**, with the design caveat recorded below.

**Design caveat (not a defect).** `revoke_user_passkeys` swallows every
exception (`:110-114`) and returns `0`, so a DB failure leaves the account
holding passkeys and the password change succeeds. The docstring states this is
deliberate ("must not roll back a legitimate password change"), and it is
loudly logged. Recorded so the next reader sees it was considered, not missed.

### 4. `src/app/modules/auth/services/sso_settings.py` (263 lines) — CHECKED AND CLEAN

| Q | Answer | Evidence |
|---|--------|----------|
| a | All five mutating/reading routes are idiom 3 (`get_current_superuser`) | `routers/sso_settings.py:46,67,125,144,166` |
| b | Superuser gate — the correct axis for host-level OAuth credentials | as above |
| c | **n/a by design**: `SSOSettings` is a host-level table with no `tenant_id` | `models.py:165-171` |
| d | `provider` path param is existence-checked, not used as a tenant key | `routers/sso_settings.py:155,176` |
| f | n/a | — |

**Property checked: the cache key needs no tenant component.** This is the
question a reviewer must actually ask here, and it is not "is the key
tenant-scoped" but "does it need to be". Method: read `get_settings_by_provider:93`
(`cache_key = f"sso_settings:{provider}"`) and the model at
`models.py:165-171` — the table has **no `tenant_id` column** and `provider` is
globally `unique=True` (`:174-179`). A tenant component would be dead weight.
Corroborating: `modules/user_preferences/models.py:39-40` names `sso_settings`
as the precedent for a host-level exception. **Ruled out.**

**Property checked: the cache never carries secrets.** Method: read the
write-through payload at `:110-128` — it stores `id`, `provider`, `is_enabled`
and `additional_config` only; `client_id` / `client_secret` are absent. The
public read returns `SSOSettingsRead`, which has neither secret field
(`schemas.py:254-258`). **Ruled out**, and no test existed — see below.

**Property checked: the write path cannot leave the cache stale.** Method: read
`create_settings:72`, `update_settings:214`, `delete_settings:243` — each
invalidates `sso_settings:{provider}` through `_invalidate_provider_cache`
(`:253-259`), which is `try/except`-logged rather than fatal, so a Redis outage
degrades to TTL expiry rather than a lost invalidation. Covered indirectly by
`tests/unit/modules/auth/test_sso_settings_service.py` (create/update/delete
happy and not-found paths at `:39,142,207`).

**Property checked: the P1-11 defect is fixed, not merely deferred.** Method:
re-ran the detector against `routers/sso_settings.py` and read the route: it
uses `PaginationParams = Depends(get_pagination_params)`
(`routers/sso_settings.py:72`), not bare `int`. Covered by
`tests/unit/modules/auth/test_sso_settings_pagination.py:29`.

**Coverage gap recorded.** No test asserts the write-through payload is
secret-free, and no test asserts the cache key contains no tenant component.
Both are one-line structural assertions; neither is load-bearing enough to add
a runtime test, so they are recorded as an **observation**, not a fix.

### 5. `src/app/modules/billing/routers/checkouts.py` (127 lines) — CHECKED AND CLEAN

Detector verdict, verbatim from `audit_router_auth.py --json`:

```
PARTIAL  src/app/modules/billing/routers/checkouts.py:32 POST       create_checkout
PARTIAL  src/app/modules/billing/routers/checkouts.py:86 POST /cancel cancel_current_subscription
OK       src/app/modules/billing/routers/checkouts.py:56 GET  /current get_current_subscription
```

Both PARTIALs are the same false-positive class `router-auth-audit.md` records
as "Self-scoped payment initiation (2)": there is no role axis on "buy your
own subscription".

| Q | Answer | Evidence |
|---|--------|----------|
| a | Authenticated, idiom **2** (`Depends(get_current_user)`) | `:34,57,87` |
| b | Self-scoped — the missing slug would be the *wrong* gate | below |
| c | `ctx.tenant_id` compared explicitly on cancel; the service reads tenant from context | `:124`; `subscription_service.py:254-256` |
| d | `subscription_id` is body-supplied but ownership-checked | `:122-125` |

**Property checked: a body-supplied `subscription_id` cannot cancel another
user's subscription, and the tenant axis is checked separately.** Method: read
`:121-127` — the router fetches the row then compares **both**
`subscription["user_id"] != current_user.id` **and**
`str(subscription.get("tenant_id")) != str(current_user.tenant_id)`, raising
`ForbiddenError`. The fetch itself is tenant-scoped and fail-closed
(`subscription_service.py:254-256` → `resolve_tenant_id(..., fail_closed=True)`),
so a cross-tenant id 404s before the comparison; the second comparison is
defence in depth against a context/row mismatch. **Ruled out.**

**Coverage gap found and closed.** `tests/unit/modules/billing/routers/test_checkouts.py`
parametrises only the **user** mismatch (`test_cancel_subscription_unauthorized:112`,
`user_id=2`). The **tenant-only** mismatch — same `user_id`, different
`tenant_id` — was untested, so half of the `or` at `:124` was unverified. The
bead is explicit that an untested ownership property on a payment surface is a
finding; it is now covered by
`tests/unit/modules/auth/test_deferred_security_verdicts.py::TestCheckoutCancelOwnership`.

**Property checked: checkout initiation cannot name another tenant.** Method:
read `create_checkout:47-52` — it passes only `current_user.id` and
`current_user.email`; `tenant` is never a parameter. The service then calls
`require_active_tenant()` (`checkout_service.py:77`) and resolves the plan
through the tenant-scoped `get_plan` (`checkout_service.py:88` →
`subscription_service.py:115-121`), so a body-supplied `plan_id` from another
tenant 404s. The `tenant_id` written into the Stripe metadata
(`checkout_service.py:119-121`) is the *resolved* tenant, not an input.
Covered by `tests/unit/modules/billing/test_checkout_tenant_resolution.py`
(the plan-tenant seam) and `tests/integration/modules/billing/test_checkout_api.py`.
**Ruled out.**

### 6. `src/app/modules/billing/routers/invoices.py` (88 lines) — CHECKED AND CLEAN

Detector verdict: **OK** on both routes — non-mutating, idiom 2 present.

**Property checked: invoice reads are tenant-scoped, and ownership is compared
explicitly.** Method: read `get_invoice:78-88` and then the service it calls.
The router's `get_record` (`subscription_service.py:412-420`) resolves the
tenant via `resolve_tenant_id(None, entity_name="BillingRecord", ...)` with
`fail_closed=True` (the default), so the **query itself** carries
`tenant_id=tid` (`subscription_service.py:416`) and fails closed with no tenant
context at all. The ownership comparison at `invoices.py:85` is therefore the
*second* axis (user within tenant), not the only one. This is the P0-1 shape
answered in the negative: the tenant **is** passed to the service.

Covered by `tests/unit/modules/billing/routers/test_invoices.py:76`
(`test_get_invoice_forbidden`) and `:64` (`test_get_invoice_not_found`).

**Property checked: the list route cannot be pointed at another user.**
Method: read `list_invoices:44-45` — `get_user_records(current_user.id, ...)`
and nothing else; `BillingRecordService.get_user_records`
(`subscription_service.py:442-468`) calls `require_active_tenant()` (`:448`)
and filters on **both** `user_id` and `tenant_id` (`:450`). Covered by
`tests/unit/modules/auth/../billing/test_billing_history_service.py:53`.

**Ruled out.** Unlike `checkouts.py`, there was no tenant-only ownership
branch here to leave untested: the tenant lives in the query, so the untested
gap that file had does not exist in this one.

### 7. `src/app/modules/billing/webhook/handlers/checkout.py` (287 lines) — CHECKED AND CLEAN

| Q | Answer | Evidence |
|---|--------|----------|
| a | Authenticated by **Stripe HMAC signature**, not a user token — by design | `ingest.py:71-93` |
| c | Tenant is re-resolved from the DB plan row, never from the session | `:89-99` |
| d | `user_id` in metadata is membership-checked against the DB tenant | `:156-167` |

**Property checked: `metadata.tenant_id` is a claim, never an authority.** This
is the `dxub.7` fix. Method: read `:47-62` (the claim is parsed and fails closed
if unparseable), `:89-99` (**the authority**: `SubscriptionPlanService.get_plan_tenant_id`,
an explicitly unscoped I10 seam, `subscription_service.py:192-204`), and
`:105-113` (a claim that disagrees with the row is rejected and **both** ids are
logged). Everything downstream — the Stripe key `:125`, the membership check
`:157`, the written row `:171`, the RLS context `:184` — uses the resolved
value. **Ruled out.**

Covered three ways: unit (`tests/unit/modules/billing/test_checkout_tenant_resolution.py:141,156,172,185,198,209,222,234`),
integration (`tests/unit/modules/billing/test_webhook_handler.py:126`),
and end-to-end against a real signed delivery with a **forged** metadata tenant
(`tests/e2e/modules/billing/test_checkout_tenant_authority_e2e.py:222`,
whose key resolver raises if the handler ever reaches Stripe for the forged
tenant — so the rejection cannot come from the outbound call simply never
happening).

**Property checked: signature verification precedes every tenant derivation.**
Method: read `ingest.py:71-93` then `:96` — the HMAC is verified against the
global platform secret first, and only then is `derive_and_attach_tenant`
called. The handler's own docstring at `:33-38` states the same invariant.
**Ruled out.**

**Ruled out vs. not found: this file is fully *ruled out*, not merely
"no defect found".** It is the only one of the seven where every tenant-deciding
predicate is both present and covered by a test that has been observed failing
its opposite.

---

## Findings

### Finding 1 — P2 — A pending MFA token can burn backup codes it will never be allowed to redeem

**Not fixed. Deliberately.** An audit whose output is an accurate list is worth
more than one whose list is short; a rushed fix hides the shape of the problem.

**Location.**
`src/app/modules/auth/services/mfa.py:187-199` (the verify-then-consume ordering)
× `src/app/modules/auth/services/mfa.py:664-673` (`_verify_backup_code` mutates
**and commits** before the consume is attempted).

**The shape.** `complete_login` peeks the pending token (`:150`), verifies the
factor (`:187`), and only *then* consumes the token atomically (`:197`). The
verify step is not read-only: on a backup code it removes the matched hash from
`mfa_method.backup_codes` and **commits** (`:667-673`), inside a
`SELECT … FOR UPDATE` lock (`:617`).

**Exploitation path.**

1. The attacker obtains a valid `mfa_pending` token. The precondition is
   materially weaker than a backup code: the token is a bearer value handed to
   a browser during a normal login (passkey `passkey.py:402-406`, password, SSO
   or magic-link doors all mint one), it lives in the client, and nothing in
   `complete_login` binds it beyond the optional IP grace
   (`MFA_ENFORCE_IP_BINDING`, `:160-172`).
2. The attacker sends **N concurrent** `POST /api/v1/mfa/complete-login`
   requests with that one token and **N distinct backup codes**.
3. All N pass `peek` — `peek` is a plain `GET` (`pending_token_store.py:69`)
   and does not consume.
4. The N requests serialise on the `FOR UPDATE` row lock. Each burns one backup
   code and commits. `BACKUP_CODE_COUNT = 10` (`mfa.py:62`), so up to **10 of
   the victim's backup codes are gone**.
5. All N then reach `consume`. `GETDEL` is atomic, so exactly one wins; the
   other N−1 find `None` and raise `UnauthorizedError` (`:198-199`). **No
   session is issued to them** — the security control itself does not fail.
6. Net effect: an attacker holding only a stale pending token has downgraded a
   two-factor account to a single-factor one, destroying the second factor
   without ever knowing a code. The victim is locked out of their own MFA until
   they recover, and the audit trail shows a successful verification per burnt
   code.

**Why it is not already covered.**
`tests/unit/modules/auth/test_mfa_login_completion.py:214`
(`test_failed_verification_preserves_pending`) covers the case where the
*verify* fails — the token is correctly preserved. `:227`
(`test_consume_miss_prevents_lookup_and_issuance`) covers the consume-miss
outcome — but asserts only that no user lookup and no issuance follow. It does
**not** assert that nothing was mutated, which is exactly the gap.

**Shape of the fix (not applied).** Make the redemption and the token
consumption one atomic step — consume the pending token *first* and return the
stored payload for the verify, or hold the pending token's Lua GETDEL and the
backup-code removal in one transaction with an explicit rollback of the
`backup_codes` mutation when the consume misses. Either restores the invariant
the pending token is supposed to carry: **redeem it, or leave the account
exactly as you found it.**

### Finding 2 — P2 (coverage) — The four questions were never asked of the SSO-settings write path's secrets — **CLOSED by `FastAPI_saas-0fdr`**

`src/app/modules/auth/services/sso_settings.py:134`
(`SSOSettingsService.get_decrypted_settings`) returns plaintext `client_id`
and `client_secret`. Method used to clear it: read the sole HTTP caller,
`src/app/modules/auth/routers/sso_settings.py:113-129` (`get_sso_settings`),
which sits behind `dependencies=[Depends(get_current_superuser)]` (`:116`).
**Ruled out by reading.** Recorded as a finding because the bead's own
criterion applies: a decryption seam whose only guard is a decorator on a
caller in a different file had **no test** asserting that a non-superuser
cannot reach it. `tests/unit/modules/auth/test_sso_settings_service.py`
(`TestSSOSettingsServiceGetDecrypted`) tests the service in isolation and so
cannot express the guard at all.

**Two corrections to this finding, recorded by `0fdr` (bead 0fdr as written
misattributed GAP 1 to `billing/routers/invoices.py`, and its own summary
carried stale line numbers):**

1. `get_decrypted_settings` is in **`auth/services/sso_settings.py`**, NOT in
   `billing/routers/invoices.py`. `invoices.py` has no such function; it is
   the file holding the empty `test_requires_authentication` cited below. The
   two were unrelated files and the bead's summary conflated them. The only
   other caller of the seam is `auth/services/sso.py:155,271` (the SSO login
   redirect), which returns no settings to the caller.
2. The empty `pass`-body test cited in the original finding,
   `test_invoices_router.py:97` (`test_requires_authentication`), is real and
   is now **closed**: replaced by
   `test_every_endpoint_refuses_an_unauthenticated_caller`, which hits both
   invoice routes and requires a 401 with the billing service untouched.

**Closed** by `TestSSOSettingsReadRouteIsSuperuserOnly` in
`tests/unit/modules/auth/test_deferred_security_verdicts.py`: a mini-app
carrying the REAL router proves a non-superuser gets 403 *and* that
`get_decrypted_settings` is never awaited, with a superuser counter-test so
"always refuse" cannot satisfy it, plus a test that the plaintext never
reaches the wire even for a superuser. The mini-app is used rather than
`src.app.main` so the assertion does not depend on the whole composition
root.

### Finding 3 — P3 (coverage) — `cross-tenant` on `checkouts.py:124` had no test

See the file-5 entry above. Half of an `or` on a payment route was unverified
until this audit. Closed by a new test, recorded here because the bead counts
an untested ownership property on the payment path as a finding in its own
right.

**Re-verified by `FastAPI_saas-0fdr`; no duplicate test added.** The tenant
half is genuinely covered: `TestCheckoutCancelOwnership.test_mismatch_forbids_cancellation`
in `tests/unit/modules/auth/test_deferred_security_verdicts.py` is
parametrised with `id="user-mismatch"` and `id="tenant-mismatch-only"`, so
each operand of the `or` at `checkouts.py:124` forbids on its own, and
`cancel_subscription` is asserted un-awaited (refused before the write). A
second test for the same property would be worse than none.

---

## Observations (checked, deliberately left as-is)

These were read and judged **correct as written**. Recorded so the next reviewer
does not re-derive them — and, more importantly, does not "fix" them.

**Observation 1 — `enable_mfa`'s legacy-format branch is fail-open by
construction.** `mfa.py:329-335` binds the setup token to a user **only if** the
stored value contains `":"`; a colon-free value is accepted unbound. The current
writer always emits `f"{user_id}:{secret}"` (`mfa.py:278`), and the Redis key
lives 600 s (`:278`), so an unbound value can only exist inside a 10-minute
window spanning a format change. Practically unreachable, and the bound path is
tested (`test_round2_auth_security.py:70`). The shape is nonetheless
fail-open-conditional and worth knowing about if the writer ever changes.

**Observation 2 — cross-endpoint TOTP reuse is a documented residual, not a new
finding.** `verify_totp_token`'s `prevent_replay` defaults to `False`
(`mfa.py:473-480`); only the login door passes `True` (`:191`). So one code can
authorise `enable → backup-codes → disable` inside the ~90 s window. This is
recorded in the source as a deliberate UX trade-off, and bead `aul0.2` records
it as *"reverted replay default with documented residual"*. **Not re-raised.**

**Observation 3 — `MFA_VERIFY_PENDING` is keyed on an attacker-supplied value,
and that is fine.** `mfa.py:143` charges
`Identity(token=request.mfa_token)` — the token the caller presents. A
per-token meter keyed on attacker input rotates freely, which is exactly why
`mfa.py:185` charges `MFA_VERIFY_ACCOUNT` on the **server-resolved** `user_id`
after the peek. Question (g) is answered by the second leg, and the pairing and
its ordering are pinned by
`tests/unit/modules/auth/test_mfa_login_completion.py:81` and `:117`.

**Observation 4 — `passkey.list_credentials` skips its tenant check when the
member lookup returns `None`.** `services/passkey.py:239-241` raises only
`if member and member.tenant_id != tenant.id`. With no tenant context the check
is skipped entirely (`:238`) — the worker path, where the `user_id` filter on
`get_by_user` (`crud.py:234`) still applies. Correct for the context it is
written for; recorded so the next reader knows the guard is conditional and why
that is acceptable.

---

## What this audit did NOT establish

Stated plainly, because the bead's central point is the difference between
"no defect found" and "ruled out":

- **Reading is not testing.** Six files are ruled out by *reading each
  predicate* and tracing it to its caller. That is a stronger claim than a spot
  check and a weaker one than a passing test, and it is the honest label.
- The audit did **not** exercise the app end-to-end. Every runtime claim in
  this document comes from an existing test that was run, or from source read
  line by line. No new runtime observation of the live MFA login path was made.
- **A predicate that is present is not a predicate that is correct.** The
  audit checks that a comparison exists and that its operands are the intended
  ones; it does not prove the DB constraint, the RLS policy, or the middleware
  that backs it. Those were read, not executed.
- Finding 1 is the one place where reading produced a *defect* rather than a
  confirmation. That is the yield ratio this bead existed to produce.