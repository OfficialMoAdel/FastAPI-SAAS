# ADR-0026: Phase 4 — auth-path CPU, and two cuts rejected on measurement

**Status:** ACCEPTED — 2026-10-23
**Date:** 2026-10-23
**Tracked by bead:** (to be filed — records Phase 4 of `REPORT 1-PLAN FOR ADVANCED OPTIMIZATIONS.md`)
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com>
**Measured by:** `tests/unit/modules/auth/test_phase4_auth_path.py`

## The questions

1. **F‑13** — the auth payload chain runs `assert_user_payload_shape` → copy →
   `UserAuthInfo.model_validate` → `model_dump()` → `AuthenticatedUser(**dict)`.
   The plan proposed four cuts. Which of them are real?
2. **§8.2** — the dummy bcrypt hash is a frozen cost-12 literal while
   `BCRYPT_ROUNDS` is an operator knob (`ge=8 le=14`). Is that a live
   enumeration oracle?
3. **§9** — RS256 signing re-parses the private key per sign; MFA's Fernet
   PBKDF2 (480k) runs on the first MFA request; WebAuthn verification runs on
   the loop; the refresh path decodes the same token twice.

## Decision

### §8.2 — ACCEPT, with a correction the plan got wrong

The dummy hash now tracks `BCRYPT_ROUNDS` (`_dummy_bcrypt_hash()`, memoized per
legal cost). Raising the knob no longer makes a real account pay 2×/4× the CPU
of a nonexistent one.

**The plan's proposed fix is unsafe as written.** It hashes `b"\x00" * 32` and
claims `checkpw` returns False for any input — **verified false**:
`bcrypt.checkpw(b"\x00" * 32, <that hash>)` returns **True**. That is harmless
only because the caller also requires `db_user`; a future refactor dropping
that guard would make 32 NUL bytes a valid password. The dummy is therefore
built from `secrets.token_bytes(32)`, so it validates for **no** input.

### §9 — ACCEPT (four items)

- **RS256 signing key is cached PARSED.** Measured **35.34 ms → 0.76 ms** per
  sign (46×), and the JWS is byte-identical (PyJWT passes a parsed key
  through unchanged). A no-op under the default HS256, which is why it must
  land *before* `RS256_MIGRATION_RUNBOOK.md` Step 9, not after.
- **MFA Fernet derivation moved to boot** as a new `mfa_fernet_warm` startup
  phase, off the event loop. Measured **~1.5 s at boot**, then **15.7 µs** per
  later call. A bad `ENCRYPTION_KEY` now fails at boot rather than as a 500 on
  someone's first login.

  **Correction (2026-10-26).** The "fails at boot" claim above was, at the
  time it was written, true only of the MFA derivation and false of the path
  that actually raises. `MFAService.warm()` deliberately *swallows* the error
  (see its docstring), and `CredentialEncryption` — the class behind
  `encrypt_column`/`decrypt_column` and `encrypt_credential`/`decrypt_credential`
  — was never constructed at startup. So a wrong-shaped key booted cleanly and
  raised `ValueError: Fernet key must be 32 url-safe base64-encoded bytes` on
  the passkey write path, the Stripe secret read, or the storage-credential
  read. The guarantee now comes from `CryptSettings.validate_encryption_key_shape`,
  a settings-load validator that constructs `Fernet(key)` itself — it fires for
  every process that loads settings (app, ARQ worker, alembic, operator
  scripts), not just the ones that run the app lifespan. `mfa_fernet_warm`
  stays a non-gating warm-up on purpose: MFA is a code path that may never be
  exercised, and refusing to boot in local dev over it would be worse than the
  clear error the lazy derivation already raises.
- **WebAuthn verification offloaded** via `asyncio.to_thread` at both call
  sites — the pattern the same file already uses for
  `encryption.decrypt_credential`.
- **`TokenData.raw_payload`** carries the already-verified claims (excluded
  from serialization), so the refresh path no longer re-verifies the same
  token. The fallback re-decodes when the attribute is not a dict (test
  doubles), so a mock cannot silently produce an empty payload.

### F‑13 — REJECT Cuts 3 and 4 on measurement

| Operation | µs/call |
|---|---|
| `assert_user_payload_shape` | 3.78 |
| `_two_level_copy` (already landed) | 0.36 |
| `UserAuthInfo.model_validate(dict)` | **3.37** |
| `UserAuthInfo.model_construct(**dict)` (Cut 3) | **6.16** |
| `model.model_dump()` | 3.35 |
| `AuthenticatedUser(**dump)` | **7.36** |
| `AuthenticatedUser.model_construct(**dump)` (Cut 4) | **11.55** |

Both "cuts" are **slower**, and Cut 4 is **incorrect**: `tier` is a nested
`UserTierAuthInfo`, so `AuthenticatedUser.model_validate(model)` raises
`dict_type`. `model_dump()` is load-bearing, not redundant. Cut 1 already
landed (0.36 µs vs `deepcopy`'s 22.54 µs), and the remaining copy is the
documented mutation-isolation contract for `get_cached_user` callers — at
0.36 µs it is not worth trading a correctness guarantee for.

`test_the_rejected_cuts_stay_rejected` pins both measurements so a future agent
cannot re-propose them without confronting the numbers.

### §10 — ACCEPT (later): drop the intermediate model, not the validation

Cuts 3 and 4 were rejected because they keep the same shape and remove the
validator. A different cut removes the SHAPE: `_load_and_validate_user` now
reads `UserDirectory.auth_info_dict()` (the raw `get_user_with_tier_eager`
dict) and hands it straight to `AuthenticatedUser(**dict)` instead of going
`dict -> UserAuthInfo.model_validate -> model_dump -> AuthenticatedUser(**dump)`.
The two hops it deletes are exactly the measured 3.37 µs + 3.35 µs; the
7.36 µs `AuthenticatedUser(**dump)` stays, so the principal is still validated
once — Cut 4's correctness objection (a nested `tier` MODEL raising
`dict_type`) does not arise, because the input is the dict, not the model.

Two consequences worth keeping:

- The raw payload's shape is guaranteed by provenance (cache and DB rows are
  both the `get_user_with_tier_eager` dict), so ADR-0012's "trusted rows" rule
  is satisfied without `model_construct`.
- `AuthenticatedUser.tenant_id` gained a before-validator that parses
  UUID-shaped strings: with the intermediate model gone, a JSON cache payload
  would otherwise leave the principal's tenant as a `str` while the DB path
  produced a `UUID`, and any `Model.tenant_id == current_user.tenant_id` site
  would bind for only one of them.

The other three `UserDirectory.auth_info` callers still get the model.

`tests/unit/api/deps/test_auth.py::TestBearerPathReadsTheRawAuthPayload` pins
the equivalence (`UserAuthInfo.model_validate` is patched to raise and the
request path still resolves), so this cut cannot quietly re-grow the round
trip.

## Re-open condition

Re-open F‑13 Cuts 3/4 only if `model_construct` becomes cheaper than
`model_validate` **and** `tier` stops being a nested model; re-open §8.2 if the
dummy-hash cost memoization is ever bypassed on a request path.
