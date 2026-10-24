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

## Re-open condition

Re-open F‑13 Cuts 3/4 only if `model_construct` becomes cheaper than
`model_validate` **and** `tier` stops being a nested model; re-open §8.2 if the
dummy-hash cost memoization is ever bypassed on a request path.
