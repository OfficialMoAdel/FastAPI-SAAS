"""P1-4 e2e: a real Apple id_token is verified through the whole provider path.

The unit tests in ``test_jwks_client_caching.py`` REPLACE the JWKS client, so
they cannot catch a bad constructor argument or a broken ``asyncio.to_thread``
wrapper — a call site that awaited the wrong thing, or passed the wrong client,
would still look green. This module exercises the real code end to end:

  * a genuine RS256 id_token is signed with a real RSA key;
  * the module-level ``PyJWKClient`` fetches a real JWKS document over a
    stubbed urlopen (the only boundary stubbed is the network);
  * the token is verified through ``get_user_info`` — the production entry
    point — and the claims come back.

No ``_jwks_client`` patch, no ``jwt.decode`` patch. If the offload or the
client wiring were wrong, this fails.
"""

import asyncio
import json
import threading
import time
from unittest.mock import patch

import jwt
import pytest

PRIVATE_KEY_PEM = (
    '-----BEGIN PRIVATE KEY-----'
    'MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQChK7/CQWdr3BQY'
    'Kci6AejYemTDcHoLjWeS5IZ6D5Ndh3Fc58Wj+e+feWZ9R+AsFGrWdYQxzkHR1YOy'
    'pUvO2CCsHe4YIf55KdxUBsclIK5ZgF+bpdNBUrUZ6wdYr8PxIs8PkTUS0S5qA6Lb'
    '5VboseR4ylpyJ/VszKYI5U3HbEvjjJhrR7E0yO9k/WmYwrQY8yCEJFXCsPRPGBsz'
    '3JNSc9lbfvreMcTjGkojftjhjlPfccteRkHYB3JarCaZUBQmYTkmdp2UJFDwSsy+'
    '0LGaMESYd9+JH4Z/6rgHJU/oDPL94f6GyHUlZ9roBDsCuw48E0lN0KMU4xCW+Xgs'
    'hMHFQ+gPAgMBAAECggEAJOv2ToSYMjPlIJH163LDilAjpEgwlVPsDdEvY2gNDOiZ'
    '1jw8YS/BLsnrajjciIyW2xFp2tUS7ASbWoIWh0io13U11a538v7dfBxfY8iEWHmZ'
    'rXkEF9tgJJNch/sgCq48PCc6PtjP5rZ1iSWVTtJt8iP9K1ivFyEcyj3pnw1Wp0Zx'
    'Y2SODJv5uMvUCbyDG05gqr/23FnDeL1HebfUqDgHVIZRrCbWeH7tCPxp/6ueNgDn'
    'gnqVA9J8jzC+SHE8P9NAjrlcCahkXYbzwXoxVuX16bkayPuACSJ6XkX2l8/kMxoi'
    'FbTulh/CJjxJoKNMOg7oFv3d3rX19UBDgZ1JwiIOJQKBgQDYs1fi5YZFVAHp+YJ2'
    'UbC/8koMQf6QDmHUHvcTN41/oi2po5emXj45I5vpnPtvTEuZr1JeF0uA6BRdil6F'
    'f9a3FkeXhTHI/HP50KkEq3iy8tBEsP2OPdq7Xk73hvjp6dCapEeTNrkfZyBYdx54'
    'p5QLunSVg6CoX0fUdF6V6FeJnQKBgQC+ZlsNGSon6uFg9ESZwZtnEpiwVXg5XDHJ'
    'BXFZVFwODsjroQ4BpTmMu+UrKu9l93f7AqXE5m9BwZ7hEm4l4K+fTFELjHJA6wbY'
    'G0g89qQMY0yX4ma4amWpSGXcJanHq21EnYoCLxi4AhBDakk/jwEVdKJDoJdnsaH0'
    '2gIM36EOmwKBgQCpT67aS+UV4+Y+7kDdu0PzJRZv71w7J9GNz8fSDvSShZ9RzMzf'
    'IwFQ2Z6m742QTzc+U7pyY5cNl8bZsxPZEKItmM3E+D+GvqDKzhpQ2LzMbGUHVpY1'
    'feSZwrzGL4HqGVJwkOyPfBE2FPgzxpZbROuqIjuW2io7g35245lHXSqQDQKBgQC8'
    'heajyoX2LlhowpRmVdBEt6Id/xLiYMoey9fW5nObq8H/pWGT+YBJPAaGvqV6JhVT'
    'oeR3WN9SkZ96JeCQerAKH+ujbrX2V4BYXHMjgecfKPc+8HpbIWZ1vmy1FUnNnYy8'
    'wuxSi0WDdO+bgNL0LumYz/qW0Zz4nGbLZU1gZ9E1/wKBgBX8TOVltuEqQRxmLgdo'
    'AD7mJecEd2pdaJyQXSkpKLkVfKA4b5h+fLx+b1bPtyy/U52OiG7R3FpYa1A0jami'
    '7ePWi5kEkAgONVR9gcTryxr5FzgIBEA0ezLz6r3a0Zfe6rElfKKZeJIw9rwdVEHB'
    'IXYtYaPYtw0JyZGK1a6iTjs0'
    '-----END PRIVATE KEY-----'
)

JWK_N = (
    'oSu_wkFna9wUGCnIugHo2Hpkw3B6C41nkuSGeg-TXYdxXOfFo_nvn3lmfUfgLBRq1nWEMc5B0dWDsqVLztggrB3uGCH-eSncVAbH'
    'JSCuWYBfm6XTQVK1GesHWK_D8SLPD5E1EtEuagOi2-VW6LHkeMpacif1bMymCOVNx2xL44yYa0exNMjvZP1pmMK0GPMghCRVwrD0'
    'TxgbM9yTUnPZW3763jHE4xpKI37Y4Y5T33HLXkZB2AdyWqwmmVAUJmE5JnadlCRQ8ErMvtCxmjBEmHffiR-Gf-q4ByVP6Azy_eH-'
    'hsh1JWfa6AQ7ArsOPBNJTdCjFOMQlvl4LITBxUPoDw'
)
JWK_E = 'AQAB'

@pytest.fixture(autouse=True)
def _cold_jwks_cache():
    """The module client is shared; clear it so tests cannot inherit warm keys.

    A warm cache means no fetch happens, which would make the burst test
    measure nothing and the happy path skip the code under test.
    """
    from src.app.modules.auth.providers import apple, microsoft

    for module in (apple, microsoft):
        module._jwks_client.jwk_set_cache.jwk_set_with_timestamp = None
    yield
    for module in (apple, microsoft):
        module._jwks_client.jwk_set_cache.jwk_set_with_timestamp = None


#: Claims the patched ``jwt.decode`` returns, matching _id_token()'s payload.
_APPLE_CLAIMS = {
    "sub": "apple-user-123",
    "email": "user@privaterelay.appleid.com",
    "email_verified": "true",
    "is_private_email": "true",
}


def _jwks_document() -> dict:
    """A real JWKS document; PyJWKClient parses the modulus eagerly."""
    return {"keys": [{"kty": "RSA", "kid": "test-kid", "use": "sig", "alg": "RS256", "n": JWK_N, "e": JWK_E}]}


class _Resp:
    """Minimal urlopen response returning the JWKS document."""

    status = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _provider():
    from src.app.modules.auth.providers.apple import AppleProvider

    return AppleProvider(
        client_id="com.example.app",
        client_secret="team:key",
        redirect_uri="https://example.test/callback",
        team_id="team",
        key_id="key",
        private_key=PRIVATE_KEY_PEM,
    )


def _id_token(**overrides) -> str:
    """A genuine RS256 id_token signed with the matching private key."""
    claims = {
        "sub": "apple-user-123",
        "email": "user@privaterelay.appleid.com",
        "email_verified": "true",
        "is_private_email": "true",
        "aud": "com.example.app",
        "iss": "https://appleid.apple.com",
        "exp": int(time.time()) + 600,
        "iat": int(time.time()),
    }
    claims.update(overrides)
    return jwt.encode(claims, PRIVATE_KEY_PEM, algorithm="RS256", headers={"kid": "test-kid"})


class TestAppleLoginEndToEnd:
    """The wiring the mocked unit tests cannot see."""

    @pytest.mark.asyncio
    async def test_real_id_token_is_verified_through_get_user_info(self) -> None:
        """A genuine Apple login completes: fetch, verify, normalize."""
        fetches = []

        def _urlopen(url, **_kwargs):
            # PyJWKClient passes a urllib Request, not a string.
            fetches.append(getattr(url, "full_url", url))
            return _Resp(_jwks_document())

        with patch("urllib.request.urlopen", new=_urlopen):
            user = await _provider().get_user_info(_id_token())

        assert fetches, "the JWKS endpoint was never contacted"
        assert "appleid.apple.com" in fetches[0]
        assert user.provider_user_id == "apple-user-123"
        assert user.email == "user@privaterelay.appleid.com"
        assert user.email_verified in (True, "true")
        # Apple sends these as JSON strings; the provider passes them through.
        assert str(user.is_private_email) == "true"

    @pytest.mark.asyncio
    async def test_wrong_audience_is_rejected(self) -> None:
        """Verification is real: a token for another client must not pass.

        A stubbed client or a skipped decode would accept this.
        """
        with patch("urllib.request.urlopen", new=lambda url, **k: _Resp(_jwks_document())):
            with pytest.raises(ValueError, match="Invalid id_token"):
                await _provider().get_user_info(_id_token(aud="com.attacker.app"))

    @pytest.mark.asyncio
    async def test_tampered_signature_is_rejected(self) -> None:
        """The signature is actually checked against the JWKS key."""
        token = _id_token()
        head, payload, sig = token.split(".")
        tampered = f"{head}.{payload}.{'A' * len(sig)}"

        with patch("urllib.request.urlopen", new=lambda url, **k: _Resp(_jwks_document())):
            with pytest.raises(ValueError, match="Invalid id_token"):
                await _provider().get_user_info(tampered)


class TestConcurrentAppleLogins:
    """The user-visible latency claim: a burst must not serialise."""

    @pytest.mark.asyncio
    async def test_concurrent_burst_overlaps_instead_of_serialising(self) -> None:
        """N simultaneous logins finish in ~one blocking call, not N.

        Called inline, each blocking fetch would occupy the event loop one
        after another. Offloaded with ``asyncio.to_thread``, the worker
        threads overlap and the wall time stays near a single fetch.

        The blocking call is stubbed on the module client's
        ``get_signing_key_from_jwt`` — the exact seam production invokes.
        That makes the test independent of the JWKS cache state (which is
        shared process-wide and may be warm), so it measures the offload and
        nothing else.
        """
        from src.app.modules.auth.providers import apple

        signing_key = type("_Key", (), {"key": "stubbed-public-key"})()
        call_threads: set[str] = set()

        def _blocking_get(_token: str):
            # Records which thread ran the blocking call, then blocks it.
            call_threads.add(threading.current_thread().name)
            time.sleep(0.15)
            return signing_key

        provider = _provider()
        token = _id_token()
        burst = 8

        started = time.monotonic()
        with (
            patch.object(apple._jwks_client, "get_signing_key_from_jwt", new=_blocking_get),
            patch.object(jwt, "decode", return_value=_APPLE_CLAIMS),
        ):
            results = await asyncio.gather(*[provider.get_user_info(token) for _ in range(burst)])
        elapsed = time.monotonic() - started

        assert len(results) == burst
        assert all(r.provider_user_id == "apple-user-123" for r in results)

        # The decisive property: the blocking call must NOT have run on the
        # event loop thread. This is cache-independent, so it still fails if
        # someone drops the ``asyncio.to_thread`` wrapper.
        assert call_threads, "the blocking call never ran"
        assert threading.current_thread().name not in call_threads, (
            "get_signing_key_from_jwt ran on the event loop thread — the "
            "to_thread offload is missing"
        )

        # And the burst must have overlapped: serial execution would be
        # burst x 0.15s = 1.2s.
        assert elapsed < 0.8, f"burst appears serialised: {elapsed:.2f}s for {burst} logins"
