"""RS256 migration: algorithm pinning, config fail-fast, and the key cache.

The signing algorithm is selected by ``CryptSettings.ALGORITHM``
(``src/app/core/config/crypt.py``). The decode side pins it with
``algorithms=[alg]`` in ``jwt_utils.decode_verified_token``, and the encode
side uses it in ``jwt_service._jwt_encode``. These tests pin the three
properties the RS256 migration runbook depends on:

* an RS256 deployment needs BOTH key halves, and config load refuses to
  construct without them (fail-fast at boot, not at first request);
* a token minted under one algorithm is rejected under the other — the
  algorithm-confusion case, which is the property that makes ``algorithms=[alg]``
  load-bearing rather than decorative;
* the verification key is memoised behind an ``lru_cache(1)``, so a rotated
  key does NOT take effect until the cache is cleared (a restart, in
  production).

Every RSA key here is generated per-run; nothing is a fixed fixture, so a
committed test file never carries signing material.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr, ValidationError

from src.app.core.config import settings
from src.app.core.config.crypt import CryptSettings
from src.app.core.security import jwt_service, jwt_utils
from src.app.modules.auth.services.tokens import TokenIssuer

AUD = "fastapi-saas-api"
ISS = "fastapi-saas"


def _keypair() -> tuple[str, str]:
    """A fresh RSA-2048 pair as (private PEM, public PEM)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, public


PRIVATE_KEY, PUBLIC_KEY = _keypair()
OTHER_PRIVATE_KEY, OTHER_PUBLIC_KEY = _keypair()

# Any well-formed secret; the leak blocklist and the 32-char HS256 minimum
# are covered by tests/unit/core/test_secret_blocklist.py.
SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef12"


def _claims(**overrides) -> dict:
    claims = {
        "sub": "rotation-probe",
        "aud": AUD,
        "iss": ISS,
        "exp": int(time.time()) + 3600,
        "jti": str(uuid4()),
    }
    claims.update(overrides)
    return claims


@pytest.fixture
def alg():
    """Install an algorithm (and, for RS256, both key halves) on the singleton.

    Restores the previous values and drops both key caches afterwards, so a
    test that flips the global algorithm cannot leak into the next one.
    """
    saved = (
        settings.crypt.ALGORITHM,
        settings.crypt.JWT_PRIVATE_KEY,
        settings.crypt.JWT_PUBLIC_KEY,
    )

    def _install(algorithm: str) -> None:
        jwt_utils.clear_jwt_key_cache()
        jwt_service.clear_signing_key_cache()
        settings.crypt.ALGORITHM = algorithm
        if algorithm == "RS256":
            settings.crypt.JWT_PRIVATE_KEY = SecretStr(PRIVATE_KEY)
            settings.crypt.JWT_PUBLIC_KEY = PUBLIC_KEY

    yield _install

    settings.crypt.ALGORITHM, settings.crypt.JWT_PRIVATE_KEY, settings.crypt.JWT_PUBLIC_KEY = saved
    jwt_utils.clear_jwt_key_cache()
    jwt_service.clear_signing_key_cache()


# ==================== Config load: RS256 requires both halves ====================


def test_rs256_round_trips_through_config_load():
    """Both halves present → the config loads and keeps the algorithm."""
    crypt = CryptSettings(
        SECRET_KEY=SecretStr(SECRET),
        ALGORITHM="RS256",
        JWT_PRIVATE_KEY=SecretStr(PRIVATE_KEY),
        JWT_PUBLIC_KEY=PUBLIC_KEY,
    )
    assert crypt.ALGORITHM == "RS256"
    assert crypt.JWT_PRIVATE_KEY is not None
    assert crypt.JWT_PUBLIC_KEY == PUBLIC_KEY


@pytest.mark.parametrize(
    "halves",
    [
        pytest.param({}, id="neither-half"),
        pytest.param({"JWT_PRIVATE_KEY": PRIVATE_KEY}, id="private-only"),
        pytest.param({"JWT_PUBLIC_KEY": PUBLIC_KEY}, id="public-only"),
    ],
)
def test_rs256_config_load_fails_fast_on_missing_half(monkeypatch, halves):
    """Missing EITHER half must abort config load, not surface at first use.

    ``CryptSettings()`` is built with no arguments so pydantic-settings reads
    the environment exactly the way ``settings = Settings()`` does at import
    time — this is the boot path, not a hand-assembled object.
    """
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("ALGORITHM", "RS256")
    monkeypatch.delenv("JWT_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("JWT_PUBLIC_KEY", raising=False)
    for name, value in halves.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match="requires JWT_PRIVATE_KEY and JWT_PUBLIC_KEY"):
        CryptSettings()


def test_rs256_config_load_succeeds_with_env_supplied_halves(monkeypatch):
    """The boot path accepts a genuine PEM pair supplied via the environment.

    Guard against a runbook that documents multiline PEMs that config load
    silently rejects: this asserts the environment-driven construction that
    deployment actually uses.
    """
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("ALGORITHM", "RS256")
    monkeypatch.setenv("JWT_PRIVATE_KEY", PRIVATE_KEY.replace("\n", "\\n"))
    monkeypatch.setenv("JWT_PUBLIC_KEY", PUBLIC_KEY.replace("\n", "\\n"))

    crypt = CryptSettings()
    assert crypt.ALGORITHM == "RS256"
    assert crypt.JWT_PUBLIC_KEY == PUBLIC_KEY.replace("\n", "\\n")


# ==================== Sign / verify round trip ====================


@pytest.mark.asyncio
async def test_rs256_access_token_round_trips(alg):
    """An RS256-minted token verifies through the one decode path."""
    alg("RS256")
    token = jwt.encode(_claims(), PRIVATE_KEY, algorithm="RS256")

    payload = await jwt_utils.decode_verified_token(token)

    assert payload is not None
    assert payload["sub"] == "rotation-probe"
    assert jwt.get_unverified_header(token)["alg"] == "RS256"


@pytest.mark.asyncio
async def test_issuer_signs_rs256_when_configured(alg):
    """``create_access_token`` mints an RS256 token, header included.

    Guards the encode half of the algorithm binding: a verifier that pinned
    RS256 while the signer still emitted HS256 would reject every real token,
    which is the mid-cutover failure the runbook's rollback covers.
    """
    alg("RS256")

    token = await jwt_service.create_access_token({"sub": "signer"})

    assert jwt.get_unverified_header(token)["alg"] == "RS256"
    assert await jwt_utils.decode_verified_token(token) is not None


# ==================== Algorithm confusion ====================


@pytest.mark.asyncio
async def test_hs256_token_is_rejected_under_rs256_config(alg):
    """ALGORITHM CONFUSION: an HS256 token must not verify against RS256."""
    alg("RS256")
    forged = jwt.encode(_claims(), SECRET, algorithm="HS256")

    assert await jwt_utils.decode_verified_token(forged) is None


@pytest.mark.asyncio
async def test_rs256_token_is_rejected_under_hs256_config(alg):
    """The mirror case: pinning must bind in both directions.

    Without this, an RS256 deployment that fell back to the shared HS256
    secret would accept an attacker-supplied HS256 token.
    """
    alg("HS256")
    genuine = jwt.encode(_claims(), PRIVATE_KEY, algorithm="RS256")

    assert await jwt_utils.decode_verified_token(genuine) is None


@pytest.mark.asyncio
async def test_rs256_rejection_is_caused_by_the_alg_pin_not_the_key(alg):
    """The rejection must be the ALGORITHM PIN, not an unusable key.

    Without this, the two tests above could pass for the wrong reason: an
    HS256 token presented to a verifier holding an RSA public key may be
    refused anyway, because a public key object is not a usable HMAC secret.
    PyJWT does refuse it (``TypeError: Expected a string value``) — a real
    defence, but a DIFFERENT one, and leaning on it would leave
    ``algorithms=[alg]`` completely untested.

    So this drives raw PyJWT twice with the identical token and the identical
    verification key. It asserts two things:

    1. With the app's pin the refusal is on the ALGORITHM, and the token's
       claims are never even reached — that is the error the app's own
       rejection rests on.
    2. With the pin widened to allow HS256, the failure MOVES to the key
       layer. The pin is therefore causal: widen it and the algorithm check
       stops being what refuses the token.
    """
    alg("RS256")
    forged = jwt.encode(_claims(), SECRET, algorithm="HS256")
    verification_key = jwt_utils._get_parsed_verification_key()

    # Sanity: the forged token really is HS256.
    assert jwt.get_unverified_header(forged)["alg"] == "HS256"

    # (1) The app's pin: refused on the ALGORITHM.
    with pytest.raises(jwt.InvalidAlgorithmError, match="The specified alg value is not allowed"):
        jwt.decode(forged, verification_key, algorithms=["RS256"], audience=AUD, issuer=ISS)

    # (2) Pin widened to allow HS256: the algorithm check stops firing, and
    #     the refusal moves to the key layer (a parsed RSA public key is not a
    #     valid HMAC secret). The exact exception type does not matter — what
    #     matters is that it is NOT InvalidAlgorithmError.
    with pytest.raises(Exception) as excinfo:  # noqa: B017 - type is the assertion
        jwt.decode(forged, verification_key, algorithms=["RS256", "HS256"], audience=AUD, issuer=ISS)

    assert not isinstance(excinfo.value, jwt.InvalidAlgorithmError), (
        "unpinning must remove the algorithm rejection; if it does not, the "
        "pinned test above proves nothing about the pin"
    )


# ==================== Wrong key ====================


@pytest.mark.asyncio
async def test_rs256_token_signed_with_a_different_key_is_rejected(alg):
    """A valid RS256 token from a foreign keypair is still refused."""
    alg("RS256")
    foreign = jwt.encode(_claims(), OTHER_PRIVATE_KEY, algorithm="RS256")

    assert await jwt_utils.decode_verified_token(foreign) is None


@pytest.mark.asyncio
async def test_hs256_token_signed_with_a_different_secret_is_rejected(alg):
    """The same property on the symmetric path."""
    alg("HS256")
    forged = jwt.encode(_claims(), "f" * 48, algorithm="HS256")

    assert await jwt_utils.decode_verified_token(forged) is None


# ==================== The key cache: rotation needs a restart ====================


@pytest.mark.asyncio
async def test_rotated_key_is_invisible_until_the_cache_is_cleared(alg):
    """A key change does NOT take effect until the ``lru_cache(1)`` is dropped.

    This is the interaction the RS256 runbook has to warn about: an engineer
    who changes JWT_PUBLIC_KEY in the environment and reloads nothing will
    watch verification keep using the OLD key, and may conclude the rotation
    is broken when it has not started yet.
    """
    alg("HS256")
    jwt_utils._get_parsed_verification_key()  # populate the singleton

    settings.SECRET_KEY = SecretStr("r" * 48)  # rotate
    jwt_utils.clear_jwt_key_cache()
    assert jwt_utils._get_parsed_verification_key() == "r" * 48  # sanity: rotation works once cleared

    settings.SECRET_KEY = SecretStr(SECRET)  # rotate back WITHOUT clearing
    assert jwt_utils._get_parsed_verification_key() == "r" * 48, (
        "precondition: the cached key must survive an un-cleared rotation"
    )
    jwt_utils.clear_jwt_key_cache()
    assert jwt_utils._get_parsed_verification_key() == SECRET


@pytest.mark.asyncio
async def test_rotated_public_key_is_invisible_until_the_cache_is_cleared(alg):
    """Same lag, asymmetric path — the case the migration runbook actually hits.

    An RS256 cutover installs a NEW public key on the verifiers. Because the
    parsed key is memoised, a verifier that never clears the cache keeps
    validating against the keypair it cached at first use, and will reject
    every token minted by the new private key.
    """
    alg("RS256")
    first = jwt_utils._get_parsed_verification_key()

    settings.crypt.JWT_PUBLIC_KEY = OTHER_PUBLIC_KEY  # rotate
    assert jwt_utils._get_parsed_verification_key() is first, (
        "precondition: the cached RSA public key must survive an un-cleared rotation"
    )
    jwt_utils.clear_jwt_key_cache()
    assert jwt_utils._get_parsed_verification_key() is not first


# ==================== Counter-test: the working algorithm still works ====================


class _NullTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _db_double():
    """A session double that accepts the blacklist writes without a database."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.begin_nested = MagicMock(return_value=_NullTx())
    return db


def _user() -> dict:
    return {
        "id": 42,
        "username": "rotation-probe",
        "email": "probe@example.com",
        "tenant_id": str(uuid4()),
        "tier_id": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("algorithm", ["HS256", "RS256"])
async def test_login_refresh_logout_cycle_survives(alg, algorithm):
    """COUNTER-TEST: issue → verify → rotate → revoke, under BOTH algorithms.

    Pins that adding the RS256 capability did not disturb the HS256 path.
    Driven through ``TokenIssuer`` (the real issuance seam) and
    ``jwt_service`` (the real verify/blacklist path) with only the database
    session and the session-registry Redis write stubbed — this needs neither
    PostgreSQL nor Redis, so it runs in the unit tier.

    ``rotate_token_pair`` mints the new pair from the old refresh payload,
    which is the refresh-rotation logic the endpoint runs; see
    ``TokenIssuer.rotate_session`` for the lock/blacklist/marker wrapper.
    """
    alg(algorithm)
    db = _db_double()
    jwt_service.register_token_version_lookup(AsyncMock(return_value=0))

    user = _user()
    with (
        patch("src.app.core.security.login_helpers.register_login_session", new_callable=AsyncMock),
        patch("src.app.core.cache.client.cache_client", MagicMock(get=AsyncMock(return_value=None))),
    ):
        # --- login: issue_session mints the pair and stamps aud/iss/exp.
        issued = await TokenIssuer.issue_session(user, channel="password")
        assert jwt.get_unverified_header(issued.access_token)["alg"] == algorithm

        # --- the access token authenticates.
        access_data = await jwt_service.verify_token(issued.access_token, jwt_service.TokenType.ACCESS, db)
        assert access_data is not None
        assert access_data.username_or_email == "rotation-probe"

        # --- refresh: the old refresh payload is decoded, then rotated.
        refresh_payload = await jwt_utils.decode_verified_token(issued.refresh_token)
        assert refresh_payload is not None
        rotated = await TokenIssuer.rotate_token_pair(
            TokenIssuer.build_token_data(subject=user["username"], user=user),
            refresh_payload,
        )
        new_access, new_refresh = rotated
        assert jwt.get_unverified_header(new_access)["alg"] == algorithm
        assert await jwt_service.verify_token(new_access, jwt_service.TokenType.ACCESS, db) is not None
        assert await jwt_utils.decode_verified_token(new_refresh) is not None

        # --- logout: the refresh token is revoked ...
        await jwt_service.blacklist_token(issued.refresh_token, db)
        # ... and the session is torn down.
        await jwt_service.blacklist_token(new_refresh, db)

    jwt_service.reset_jwt_security_lookups()
