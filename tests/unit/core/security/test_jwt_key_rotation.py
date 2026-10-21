"""HS256 ``SECRET_KEY`` rotation: what the rotation actually invalidates, and
what it silently leaves alive.

A leaked HS256 ``SECRET_KEY`` is a total authentication bypass — the
verification key *is* the signing key, so anyone holding it can mint tokens.
Rotating it is therefore mandatory, and mandatory procedures are only as good
as what they pin down. These tests pin the three claims the rotation procedure
rests on:

* **A new key invalidates old tokens.** A token signed with the previous key
  is refused once the process holds the new one. This is the whole point; if
  it is false the rotation does nothing.
* **A bumped ``token_version`` invalidates outstanding tokens**, and the
  rejection is the *version check* — not an incidental signature, blacklist or
  expiry failure that happens to land at the same moment.
* **The swap is invisible until the key caches are cleared.** Both the signing
  key (``jwt_service._get_signing_key``) and the verification key
  (``jwt_utils._get_parsed_verification_key``) sit behind ``lru_cache(1)``, so
  a rotated key does not take effect until the process restarts. An operator
  who installs a new key and immediately concludes "the key is broken" is
  debugging the wrong thing; the procedure says to restart.

The replacement key must also clear config validation — the 32-character HS256
minimum and the committed-leak blocklist — which is pinned here for the
rotation-specific cases and in ``tests/unit/core/test_secret_blocklist.py``
for the general ones.

**No real secret appears here.** Every key is generated per-test with
``secrets.token_hex(32)`` — the same shape and strength as the
``openssl rand -hex 32`` the runbook tells an operator to run, but produced
in-process and discarded when the test ends. A committed test file must never
carry signing material, even throwaway material.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Iterator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import jwt
import pytest
from pydantic import SecretStr, ValidationError

from src.app.core.config import settings
from src.app.core.config.crypt import KNOWN_LEAKED_SECRET_KEYS, CryptSettings
from src.app.core.security import jwt_service, jwt_utils
from src.app.core.security.tokens import hash_token
from src.app.modules.users.session_invalidation import InvalidationReason, should_bump_token_version

AUD = "fastapi-saas-api"
ISS = "fastapi-saas"

SUBJECT = "rotation-probe"


def _fresh_key() -> str:
    """A throwaway HS256 secret, 64 hex chars — never persisted anywhere."""
    return secrets.token_hex(32)


def _current_key() -> str:
    """The plaintext key the settings singleton currently holds (test-only)."""
    return settings.SECRET_KEY.get_secret_value()


def _claims(**overrides) -> dict:
    """Claims shaped like a real access token, with generated jti/sid."""
    claims = {
        "sub": SUBJECT,
        "aud": AUD,
        "iss": ISS,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "jti": str(uuid4()),
        "sid": str(uuid4()),
        "token_type": "access",
        "token_version": 0,
    }
    claims.update(overrides)
    return claims


def _mint(key: str, **overrides) -> str:
    """Sign a token with `key` directly, bypassing the signing-key cache."""
    return jwt.encode(_claims(**overrides), key, algorithm="HS256")


def _drop_all_key_caches() -> None:
    """Every per-process memoisation of the key, plus the decode-failure sentinel.

    Dropping all of them is what a process restart does. An operator cannot do
    this remotely — see the runbook.
    """
    jwt_utils.clear_jwt_key_cache()
    jwt_service.clear_signing_key_cache()
    jwt_utils.clear_decode_failure_cache()


@pytest.fixture
def rotate_key() -> Iterator[Callable[[], str]]:
    """Install a fresh throwaway ``SECRET_KEY`` and drop every key cache.

    This is exactly the state a process reaches after an operator installs a
    new key and the process restarts. Yields an installer that returns the key
    it installed, so a test never has to guess which one is live.

    Restores the previous value on teardown so a rotation cannot leak into the
    next test.
    """
    saved = settings.crypt.SECRET_KEY

    def _install() -> str:
        key = _fresh_key()
        settings.crypt.SECRET_KEY = SecretStr(key)
        _drop_all_key_caches()
        return key

    try:
        yield _install
    finally:
        settings.crypt.SECRET_KEY = saved
        _drop_all_key_caches()


@pytest.fixture
def jwt_lookups():
    """Own the two DIP lookup seams for the duration of one test.

    ``jwt_service.reset_jwt_security_lookups`` nulls BOTH globals, so a test
    that calls it to tidy up after itself also unwires the tenant lookup for
    every test that runs later in the same process. ``_resolve_tenant_id_for_token``
    silently degrades to ``None`` when that seam is ``None``, so the damage
    shows up as an unrelated test's failure rather than as an error here.

    This fixture saves and restores whatever was registered instead of
    assuming ``None``, so a rotation test leaves the process exactly as it
    found it.
    """
    saved_version = jwt_service._token_version_lookup
    saved_tenant = jwt_service._user_tenant_lookup
    try:
        yield
    finally:
        jwt_service._token_version_lookup = saved_version
        jwt_service._user_tenant_lookup = saved_tenant


class _NullTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _db_double() -> MagicMock:
    """A session double: no PostgreSQL, no real SQL, no real revocation writes."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.begin_nested = MagicMock(return_value=_NullTx())
    return db


def _cache_client(version: int | None) -> MagicMock:
    """Redis double returning `version` from ``get_strict`` — the token_version cache."""
    client = MagicMock()
    client.get_strict = AsyncMock(return_value=None if version is None else str(version))
    client.set_nx = AsyncMock(return_value=True)
    return client


# ==================== Rotation invalidates old tokens ====================


@pytest.mark.asyncio
async def test_token_signed_with_old_key_is_rejected_after_rotation(rotate_key):
    """THE CORE CLAIM: a token signed with the previous key stops validating.

    Rotate the key, then present a token minted under the OLD key. It must be
    refused. Without this, the rotation procedure is security theatre.
    """
    old_key = _fresh_key()
    new_key = rotate_key()  # from here the process holds new_key

    stale = _mint(old_key)
    fresh = _mint(new_key)

    # Counter-condition first: the app is really holding the new key. If this
    # failed, the rejection asserted next would prove nothing — the stale token
    # could be refused simply because its key was never installed.
    assert jwt_utils._get_parsed_verification_key() == new_key

    assert await jwt_utils.decode_verified_token(fresh) is not None, "a token signed with the new key must verify"
    assert await jwt_utils.decode_verified_token(stale) is None, "a token signed with the OLD key must be refused"


@pytest.mark.asyncio
async def test_old_key_rejection_is_the_signature_not_something_incidental(rotate_key):
    """The rejection above must be a SIGNATURE failure, observed through the app.

    ``decode_verified_token`` returns ``None`` for expiry, audience, issuer and
    signature failures alike, so "it returned None" is not by itself evidence
    that the signature changed. This goes back through the real decode path
    with the decode-failure sentinel in front of it and shows that, once the
    sentinel is out of the way, the app's OWN verification key is what refuses
    the token — and that it refuses it specifically on the signature.

    Order matters here and is the point of the test: with the sentinel in
    place the app returns ``None`` immediately, running no crypto at all, so
    "returned None" alone cannot distinguish a memoised verdict from a fresh
    signature failure. Dropping the sentinel forces the real comparison.
    """
    old_key = _fresh_key()
    new_key = rotate_key()

    stale = _mint(old_key, exp=int(time.time()) + 3600)

    # The app refuses it — but note this verdict is now memoised, so the next
    # assertion would be reading the cache rather than the crypto.
    assert await jwt_utils.decode_verified_token(stale) is None

    # Force a real decode against the app's own verification key: the failure
    # is InvalidSignatureError, i.e. the key, not the expiry, audience or issuer.
    jwt_utils.clear_decode_failure_cache()
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            stale,
            jwt_utils._get_parsed_verification_key(),
            algorithms=["HS256"],
            audience=AUD,
            issuer=ISS,
        )

    # And that verification key really is the NEW key, and the token really is
    # signed by the OLD one — the two differ, and only in the key.
    assert jwt_utils._get_parsed_verification_key() == new_key
    assert jwt.decode(stale, old_key, algorithms=["HS256"], audience=AUD, issuer=ISS)["sub"] == SUBJECT


@pytest.mark.asyncio
async def test_signing_uses_the_rotated_key(rotate_key):
    """A token the app issues after the rotation is signed with the new key.

    Guards the mirror of the first test: if the *signing* key cache kept
    serving the old secret, freshly issued tokens would be born unverifiable
    after the restart — a self-inflicted outage rather than a leak response.
    """
    new_key = rotate_key()

    issued = await jwt_service.create_access_token({"sub": SUBJECT, "token_version": 0})

    assert await jwt_utils.decode_verified_token(issued) is not None
    # Decoded with the NEW key explicitly: proves the signature is bound to it.
    assert jwt.decode(issued, new_key, algorithms=["HS256"], audience=AUD, issuer=ISS)["sub"] == SUBJECT


# ==================== token_version invalidation ====================


@pytest.mark.asyncio
async def test_bumped_token_version_rejects_an_outstanding_token(rotate_key, jwt_lookups):
    """Bumping ``token_version`` refuses a token minted before the bump.

    The token is signed with the CORRECT current key and is not expired and not
    blacklisted, so the only thing that can refuse it is the version
    comparison. The version is served through the same seam production uses
    (``cache_client.get_strict`` on the key the code computes), so the bump genuinely
    flows through the real code path rather than a stubbed comparison.
    """
    rotate_key()  # a fresh key, so nothing else can be the cause
    db = _db_double()

    outstanding = await jwt_service.create_access_token({"sub": SUBJECT, "token_version": 7})

    with (
        patch("src.app.core.security.jwt_service.is_token_revoked", AsyncMock(return_value=False)),
        patch("src.app.core.cache.client.cache_client", _cache_client(version=7)),
    ):
        jwt_service.register_token_version_lookup(AsyncMock(return_value=7))
        assert await jwt_service.verify_token(outstanding, jwt_service.TokenType.ACCESS, db) is not None, (
            "precondition: the token must be ACCEPTED while the stored version still matches"
        )

        # The bump: the user's stored token_version moves 7 -> 8. The token
        # itself is untouched and still correctly signed.
        with patch("src.app.core.cache.client.cache_client", _cache_client(version=8)):
            assert await jwt_service.verify_token(outstanding, jwt_service.TokenType.ACCESS, db) is None, (
                "a token whose token_version no longer matches the stored value must be refused"
            )

    jwt_service.reset_jwt_security_lookups()


@pytest.mark.asyncio
async def test_version_rejection_survives_a_correctly_signed_unexpired_token(rotate_key, jwt_lookups):
    """Same rejection, asserted from the other side.

    Rebuilds the token independently with the current key so nothing about its
    construction can be blamed: fresh ``jti``, fresh ``sid``, an expiry an hour
    out, correct ``aud``/``iss``, ``token_type=access``. It decodes fine through
    the decode path (proven here), and ``verify_token`` still refuses it. That
    isolates the version check as the only remaining gate.
    """
    new_key = rotate_key()
    db = _db_double()

    outstanding = _mint(new_key, token_version=3, exp=int(time.time()) + 3600)

    with (
        patch("src.app.core.security.jwt_service.is_token_revoked", AsyncMock(return_value=False)),
        patch("src.app.core.cache.client.cache_client", _cache_client(version=4)),
    ):
        jwt_service.register_token_version_lookup(AsyncMock(return_value=4))

        # Not blacklisted, not revoked, not expired, correct aud/iss, correct
        # key: it passes the decode path cleanly, so the refusal below is the
        # version check and nothing else.
        assert await jwt_utils.decode_verified_token(outstanding) is not None, (
            "precondition: the token must be a valid, unexpired, correctly signed access token"
        )
        assert await jwt_service.verify_token(outstanding, jwt_service.TokenType.ACCESS, db) is None

    jwt_service.reset_jwt_security_lookups()


@pytest.mark.asyncio
async def test_only_the_version_changed_between_acceptance_and_rejection(rotate_key, jwt_lookups):
    """The causality a changing verdict demands: same token, both outcomes.

    The two tests above each hold one end of the story, and either could pass
    for the wrong reason — a stale decode-failure sentinel, an expired clock,
    a stub that refuses everything. This drives one and the SAME token through
    one and the SAME code path twice, changing only the stored ``token_version``,
    and requires the verdict to flip from accepted to rejected.

    A token accepted at observation A cannot be expired at observation B, so if
    a clock were the cause the second assertion would contradict the first; and
    a stub that refused everything would fail the first. What is left is the
    version comparison.
    """
    rotate_key()
    db = _db_double()

    outstanding = await jwt_service.create_access_token({"sub": SUBJECT, "token_version": 5})

    with patch("src.app.core.security.jwt_service.is_token_revoked", AsyncMock(return_value=False)):
        jwt_service.register_token_version_lookup(AsyncMock(return_value=5))

        with patch("src.app.core.cache.client.cache_client", _cache_client(version=5)):
            assert await jwt_service.verify_token(outstanding, jwt_service.TokenType.ACCESS, db) is not None, (
                "precondition: accepted while the stored version matches"
            )

        # Exactly one input changed between these two observations: the stored
        # version. Same token string, same key, same process, same clock.
        with patch("src.app.core.cache.client.cache_client", _cache_client(version=6)):
            assert await jwt_service.verify_token(outstanding, jwt_service.TokenType.ACCESS, db) is None, (
                "the same token must be refused once the stored token_version no longer matches"
            )

    jwt_service.reset_jwt_security_lookups()


@pytest.mark.asyncio
async def test_matching_token_version_is_accepted(rotate_key, jwt_lookups):
    """COUNTER-TEST: a correct key plus a correct version still authenticates.

    The rotation mechanism must not break normal operation. Same token, same
    key, same un-bumped version — accepted. This is the assertion that fails if
    the version check were wired to refuse everything, e.g. if a rotation
    "fix" short-circuited the comparison.
    """
    rotate_key()
    db = _db_double()

    outstanding = await jwt_service.create_access_token({"sub": SUBJECT, "token_version": 11})

    with (
        patch("src.app.core.security.jwt_service.is_token_revoked", AsyncMock(return_value=False)),
        patch("src.app.core.cache.client.cache_client", _cache_client(version=11)),
    ):
        jwt_service.register_token_version_lookup(AsyncMock(return_value=11))
        data = await jwt_service.verify_token(outstanding, jwt_service.TokenType.ACCESS, db)

    assert data is not None, "a current token with a matching token_version must authenticate"
    assert data.username_or_email == SUBJECT
    assert data.token_version == 11

    jwt_service.reset_jwt_security_lookups()


@pytest.mark.asyncio
async def test_version_lookup_is_reached_and_a_missing_version_is_refused(rotate_key, jwt_lookups):
    """The stored version is consulted, and an unknown user is refused.

    Pins both ends of the same seam. First: with the cache cold, the lookup
    seam is genuinely called — a test that hard-coded the version onto the
    token would pass against a lookup that never runs. Second: a lookup
    returning ``None`` — a deleted or unknown user — is a mismatch, not a pass,
    so a stale token cannot outlive the deletion of its owner.
    """
    rotate_key()
    db = _db_double()

    outstanding = await jwt_service.create_access_token({"sub": SUBJECT, "token_version": 0})

    lookup = AsyncMock(return_value=0)
    with (
        patch("src.app.core.security.jwt_service.is_token_revoked", AsyncMock(return_value=False)),
        patch("src.app.core.cache.client.cache_client", _cache_client(version=None)),
    ):
        jwt_service.register_token_version_lookup(lookup)
        assert await jwt_service.verify_token(outstanding, jwt_service.TokenType.ACCESS, db) is not None
        assert lookup.await_count >= 1, "the token_version lookup seam must actually be consulted"

        lookup.return_value = None
        assert await jwt_service.verify_token(outstanding, jwt_service.TokenType.ACCESS, db) is None, (
            "no stored version must be a mismatch (fail closed), not a pass"
        )

    jwt_service.reset_jwt_security_lookups()


def test_credential_and_deactivation_are_bump_class_reasons():
    """The procedure leans on ``token_version`` to force re-auth, not raw SQL.

    The bump is a policy decision: ``invalidate_user_sessions`` bumps only for
    reasons in ``_BUMP_REASONS``. An operator told to "bump token_version" is
    routing through that seam, and this asserts the seam's policy really does
    treat the security-affecting reasons as bump-class — and that a benign
    profile edit is not, so nobody widens the bump to cover everything.
    """
    assert should_bump_token_version(InvalidationReason.CREDENTIAL_CHANGE) is True
    assert should_bump_token_version(InvalidationReason.DEACTIVATION) is True
    assert should_bump_token_version(InvalidationReason.PROFILE_EDIT) is False


# ==================== The cache/restart interaction ====================


def test_rotated_key_is_invisible_until_the_caches_are_cleared():
    """Bead step 1(d), observed: no restart, no effect.

    Both accessors are ``lru_cache(1)``. Populate them, swap the key, and read
    again: the OLD key is still served. This is why the procedure mandates a
    restart — and why an operator who swaps the key and immediately retests gets
    a stale answer that looks like "the new key is broken".
    """
    saved = settings.crypt.SECRET_KEY
    try:
        original = _fresh_key()
        settings.crypt.SECRET_KEY = SecretStr(original)
        _drop_all_key_caches()

        # First use populates both memoised accessors with `original`.
        assert jwt_service._get_signing_key() == original
        assert jwt_utils._get_parsed_verification_key() == original

        rotated = _fresh_key()
        settings.crypt.SECRET_KEY = SecretStr(rotated)

        # Precondition: the settings object really does hold the new key.
        assert settings.SECRET_KEY.get_secret_value() == rotated

        # ...and neither accessor notices. This is the operator trap.
        assert jwt_service._get_signing_key() == original, (
            "precondition: the signing key must stay stale until the cache is cleared"
        )
        assert jwt_utils._get_parsed_verification_key() == original, (
            "precondition: the verification key must stay stale until the cache is cleared"
        )

        # A restart (drop both) picks the new key up.
        _drop_all_key_caches()
        assert jwt_service._get_signing_key() == rotated
        assert jwt_utils._get_parsed_verification_key() == rotated
    finally:
        settings.crypt.SECRET_KEY = saved
        _drop_all_key_caches()


@pytest.mark.asyncio
async def test_a_token_rejected_before_the_restart_is_memoised_for_60_seconds(rotate_key):
    """The decode-failure sentinel is a second thing a restart must clear.

    ``jwt_utils`` memoises permanent decode failures for 60 s, keyed by token
    hash. A token rejected under the old key keeps being rejected with no crypto
    at all, even after the correct key is installed and the key caches are
    dropped. A restart clears the sentinel too, so the stale verdict cannot
    survive a correct rotation — but an operator who clears caches in-process, or
    who retests the same rejected token string straight after installing the
    right key, will read a stale ``None`` and conclude the fix did not work.
    """
    new_key = rotate_key()

    stale = _mint(_fresh_key())
    assert await jwt_utils.decode_verified_token(stale) is None

    assert (hash_token(stale), True) in jwt_utils._decode_failure_cache, (
        "precondition: the failure must be memoised, otherwise this test proves nothing"
    )

    # Installing the CORRECT key and dropping only the key caches is not enough:
    # the sentinel short-circuits before the crypto runs. It is still the right
    # answer here, but for the wrong reason — which is the trap.
    jwt_utils.clear_jwt_key_cache()
    assert await jwt_utils.decode_verified_token(stale) is None

    # Dropping the sentinel too (what a restart does) lets the decode run, and
    # the verdict is then visibly the signature rather than a cached no.
    jwt_utils.clear_decode_failure_cache()
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(stale, new_key, algorithms=["HS256"], audience=AUD, issuer=ISS)

    # And a token signed with the live key is not poisoned by any of this.
    live = _mint(new_key)
    assert await jwt_utils.decode_verified_token(live) is not None


# ==================== The replacement key must pass config validation ====================


def test_a_generated_replacement_key_passes_validation():
    """The key the runbook tells an operator to generate loads cleanly.

    ``openssl rand -hex 32`` yields 64 hex characters; ``secrets.token_hex(32)``
    is the same shape and strength, so a key accepted here is the shape the
    procedure produces.
    """
    key = _fresh_key()
    loaded = CryptSettings(SECRET_KEY=SecretStr(key))
    assert loaded.SECRET_KEY is not None
    assert loaded.SECRET_KEY.get_secret_value() == key


def test_a_replacement_key_that_is_too_short_is_refused(monkeypatch):
    """A short HS256 key is rejected at config load, in EVERY environment.

    Parametrised over ENVIRONMENT because the check that does the rejecting
    (``validate_secrets``) is unconditional, while its sibling
    (``validate_production_secrets``) only fires in staging/production. A
    rotation rehearsed on a developer laptop must not be able to install a key
    that production would refuse — and if this ever fails only for
    ``production``, the min-length check has been accidentally narrowed to the
    production-only validator.
    """
    monkeypatch.setenv("ALGORITHM", "HS256")

    for environment in ("local", "staging", "production"):
        monkeypatch.setenv("ENVIRONMENT", environment)
        monkeypatch.setenv("SECRET_KEY", secrets.token_hex(8))  # 16 chars, well under 32
        with pytest.raises(ValidationError, match="at least 32 characters"):
            CryptSettings()


def test_a_replacement_key_on_the_leaked_blocklist_is_refused(monkeypatch):
    """A key that already leaked into git history can never be re-installed.

    Reads one entry out of the committed blocklist rather than hard-coding a
    leaked value into another test file, and asserts the blocklist is not empty
    first — an empty blocklist would make the membership check vacuous.
    """
    assert KNOWN_LEAKED_SECRET_KEYS, "the leak blocklist must not be empty"
    leaked = sorted(KNOWN_LEAKED_SECRET_KEYS)[0]

    monkeypatch.setenv("ALGORITHM", "HS256")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", leaked)

    with pytest.raises(ValidationError, match="leaked into git history"):
        CryptSettings()


def test_the_short_key_and_the_leaked_key_are_refused_by_different_checks():
    """Two distinct refusals, so neither can be mistaken for the other.

    A weak key trips the HS256 minimum; a leaked key trips the blocklist. Both
    fail closed, but only the second is a security regression — an operator who
    is told "at least 32 characters" about a 64-character leaked key is being
    told the wrong thing about why their configuration was refused.
    """
    with pytest.raises(ValidationError, match="at least 32 characters"):
        CryptSettings(SECRET_KEY=SecretStr(secrets.token_hex(4)))  # 8 chars

    leaked = sorted(KNOWN_LEAKED_SECRET_KEYS)[0]
    assert len(leaked) >= 32, (
        "precondition: the leaked key must clear the length check, so only the blocklist can refuse it"
    )
    with pytest.raises(ValidationError, match="leaked into git history"):
        CryptSettings(SECRET_KEY=SecretStr(leaked))


# ==================== Counter-test: a correct rotation breaks nothing ====================


@pytest.mark.asyncio
async def test_a_correctly_rotated_key_still_serves_the_full_lifecycle(rotate_key, jwt_lookups):
    """COUNTER-TEST: after a correct rotation, login → verify → refresh.

    The mechanism under test must not break normal operation when done right.
    Drives the real issuance seam (``TokenIssuer``) and the real verify path
    (``jwt_service``) with only the database session and the Redis-backed
    revocation oracle stubbed, so this needs neither PostgreSQL nor Redis.
    """
    from src.app.modules.auth.services.tokens import TokenIssuer

    rotate_key()
    db = _db_double()
    jwt_service.register_token_version_lookup(AsyncMock(return_value=0))

    user = {
        "id": 42,
        "username": SUBJECT,
        "email": f"{SUBJECT}@example.com",
        "tenant_id": str(uuid4()),
        "tier_id": 1,
    }

    # The version cache is served warm at 0, so ``verify_token`` takes the CACHED
    # branch of the version check rather than falling through to the DB lookup.
    # That branch is the one a "refuse everything" regression would live in, so
    # the counter-test has to go through it rather than around it.
    with (
        patch("src.app.core.security.login_helpers.register_login_session", new_callable=AsyncMock),
        patch("src.app.core.cache.client.cache_client", _cache_client(version=0)),
        patch("src.app.core.security.jwt_service.is_token_revoked", AsyncMock(return_value=False)),
    ):
        issued = await TokenIssuer.issue_session(user, channel="password")

        access = await jwt_service.verify_token(issued.access_token, jwt_service.TokenType.ACCESS, db)
        assert access is not None, "a token issued under the rotated key must authenticate"
        assert access.username_or_email == SUBJECT

        refresh_payload = await jwt_utils.decode_verified_token(issued.refresh_token)
        assert refresh_payload is not None

        new_access, new_refresh = await TokenIssuer.rotate_token_pair(
            TokenIssuer.build_token_data(subject=user["username"], user=user),
            refresh_payload,
        )
        assert await jwt_service.verify_token(new_access, jwt_service.TokenType.ACCESS, db) is not None
        assert await jwt_utils.decode_verified_token(new_refresh) is not None

    jwt_service.reset_jwt_security_lookups()
