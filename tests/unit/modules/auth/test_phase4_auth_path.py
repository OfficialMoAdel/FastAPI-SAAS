"""Phase 4: auth-path CPU and latent correctness.

WHAT THIS PINS
    §6   `_pepper()` is memoized, and the digest is unchanged by that.
    §6   `_decode_failure_key` (a peppered HMAC, ~7 us) is computed ONLY when
         the failure-sentinel cache is non-empty — and a failure still
         populates it.
    §8.2 the dummy bcrypt hash tracks `BCRYPT_ROUNDS` instead of being frozen
         at cost 12, so raising the knob cannot reopen the account-enumeration
         timing oracle.
    §9   `TokenData.raw_payload` carries the VERIFIED claims, so the refresh
         path does not re-verify the same token; it is excluded from
         serialization.
    §9   the RS256 signing key is cached PARSED, and produces the identical JWS.
    §9   WebAuthn verification runs off the event loop.

WHAT THIS DELIBERATELY DOES NOT DO
    The report's F-13 Cuts 3/4 (`model_construct` instead of `model_validate`,
    and dropping `model_dump`) were measured and REJECTED: both are SLOWER
    (6.16 vs 3.37 us, 11.55 vs 7.36 us) and Cut 4 is incorrect (`tier` is a
    nested model, so `AuthenticatedUser.model_validate(model)` raises
    `dict_type`). `test_the_rejected_cuts_stay_rejected` records the
    measurements so a future agent does not re-propose them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import bcrypt
import pytest

from src.app.core.principal import AuthenticatedUser
from src.app.core.schemas import TokenData
from src.app.core.security import jwt_utils
from src.app.core.security import tokens as tokens_module
from src.app.modules.auth.service import AuthService
from src.app.modules.users.schemas import UserAuthInfo


class TestPepperIsMemoized:
    def test_the_digest_is_unchanged(self) -> None:
        """Memoizing must not alter the HMAC -- this is the whole risk."""
        import hashlib
        import hmac

        from src.app.core.config import settings

        tokens_module.clear_pepper_cache()
        digest = tokens_module.hash_token("some-token")
        expected = hmac.new(
            f"saas-token-v1:{settings.SECRET_KEY.get_secret_value()}".encode(),
            b"some-token",
            hashlib.sha256,
        ).hexdigest()
        assert digest == expected

    def test_clearing_the_cache_changes_nothing_but_recomputes(self) -> None:
        tokens_module.clear_pepper_cache()
        first = tokens_module.hash_token("t")
        tokens_module.clear_pepper_cache()
        assert tokens_module.hash_token("t") == first


class TestDecodeFailureKeyIsLazy:
    @pytest.mark.asyncio
    async def test_a_successful_decode_does_not_compute_the_hmac(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The hot path: a VALID token, nothing cached, so skip the HMAC.

        This is the case that was pure waste -- the key was computed, the
        membership test missed, the decode succeeded, and the key was thrown
        away. A FAILING decode still computes it (that is how the sentinel gets
        populated), which the sibling test pins.
        """
        calls: list[str] = []

        def _spy(token: str, verify_issuer: bool) -> tuple[str, bool]:
            calls.append(token)
            return ("k", verify_issuer)

        monkeypatch.setattr(jwt_utils, "_decode_failure_key", _spy)
        jwt_utils.clear_decode_failure_cache()
        assert not jwt_utils._decode_failure_cache

        from src.app.core.security.jwt_service import create_access_token

        token = await create_access_token({"sub": "phase4-user"})
        payload = await jwt_utils.decode_verified_token(token)
        assert payload is not None, "precondition: the token must decode"
        assert calls == [], "a SUCCESSFUL decode must not pay the HMAC"

    @pytest.mark.asyncio
    async def test_a_failure_still_populates_the_sentinel(self) -> None:
        jwt_utils.clear_decode_failure_cache()
        assert await jwt_utils.decode_verified_token("not-a-jwt", verify_issuer=True) is None
        assert len(jwt_utils._decode_failure_cache) == 1, "a permanent failure must still be memoized"

    @pytest.mark.asyncio
    async def test_a_populated_cache_still_short_circuits(self) -> None:
        jwt_utils.clear_decode_failure_cache()
        assert await jwt_utils.decode_verified_token("bad-token", verify_issuer=True) is None
        size = len(jwt_utils._decode_failure_cache)
        # Second call takes the sentinel path and does not grow the cache.
        assert await jwt_utils.decode_verified_token("bad-token", verify_issuer=True) is None
        assert len(jwt_utils._decode_failure_cache) == size


class TestDummyHashTracksTheConfiguredCost:
    def test_the_dummy_hash_uses_the_configured_rounds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.app.core.config import settings

        AuthService._DUMMY_BCRYPT_HASHES.clear()
        monkeypatch.setattr(settings.crypt, "BCRYPT_ROUNDS", 8)
        low = AuthService._dummy_bcrypt_hash()
        assert low.split("$")[2] == "08"

        monkeypatch.setattr(settings.crypt, "BCRYPT_ROUNDS", 10)
        high = AuthService._dummy_bcrypt_hash()
        assert high.split("$")[2] == "10"
        assert high != low, "the dummy hash must track BCRYPT_ROUNDS"

    def test_it_still_rejects_every_password(self) -> None:
        """The oracle must stay closed: checkpw is False for ANY input.

        The zero-bytes arm is the one that matters: the report's suggested
        `bcrypt.hashpw(b"\x00" * 32, ...)` returns True against that input, so
        this asserts the dummy is built from an unguessable secret instead.
        """
        digest = AuthService._dummy_bcrypt_hash()
        assert bcrypt.checkpw(b"anything", digest.encode()) is False
        assert bcrypt.checkpw(b"\x00" * 32, digest.encode()) is False, (
            "a NUL-filled password must NOT satisfy the dummy hash"
        )

    def test_it_is_memoized_per_cost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.app.core.config import settings

        AuthService._DUMMY_BCRYPT_HASHES.clear()
        monkeypatch.setattr(settings.crypt, "BCRYPT_ROUNDS", 8)
        assert AuthService._dummy_bcrypt_hash() is AuthService._dummy_bcrypt_hash()
        assert len(AuthService._DUMMY_BCRYPT_HASHES) == 1


class TestRefreshDoesNotReVerifyTheToken:
    def test_raw_payload_is_excluded_from_serialization(self) -> None:
        data = TokenData(username_or_email="u@x.com", raw_payload={"secret_claim": "v"})
        assert "raw_payload" not in data.model_dump()
        assert data.raw_payload == {"secret_claim": "v"}

    @pytest.mark.asyncio
    async def test_the_refresh_path_reuses_the_verified_payload(self) -> None:
        """`verify_token`'s payload is reused, so the token is decoded ONCE.

        Behavioural, not a source grep: the refresh flow is driven with a
        TokenData carrying `raw_payload`, and the second `decode_jwt_token`
        must never run.
        """
        from unittest.mock import MagicMock

        from src.app.modules.auth import service as auth_service_module

        carried = {"tenant_id": "t-1", "exp": 9_999_999_999, "sid": "s-1", "jti": "j-1"}
        token_data = TokenData(username_or_email="u@x.com", raw_payload=carried)
        decode = AsyncMock(side_effect=AssertionError("the refresh path must not re-decode"))

        service = AuthService()
        with (
            patch.object(auth_service_module, "verify_token", AsyncMock(return_value=token_data)),
            patch.object(auth_service_module, "_", create=True),
            patch(
                "src.app.core.security.jwt_utils.decode_jwt_token",
                decode,
            ),
            patch.object(
                AuthService,
                "_reject_reused_refresh_token",
                AsyncMock(return_value=None),
            ),
            patch.object(
                AuthService,
                "_resolve_user_auth_info",
                AsyncMock(return_value=None),
            ),
        ):
            with pytest.raises(Exception):  # noqa: B017 - the flow stops at the user check
                await service.refresh_access_token("tok", db=MagicMock())
        decode.assert_not_awaited()


class TestRS256SigningKeyIsParsed:
    def test_the_jws_is_identical_to_the_pem_string_path(self) -> None:
        """A parsed key must produce byte-identical output (PyJWT passes it through)."""
        import jwt
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        parsed = serialization.load_pem_private_key(pem.encode(), password=None)
        payload = {"sub": "1", "jti": "j"}
        assert jwt.encode(payload, pem, algorithm="RS256") == jwt.encode(payload, parsed, algorithm="RS256")

    def test_the_hs256_branch_still_returns_the_secret_string(self) -> None:
        from src.app.core.security import jwt_service

        jwt_service.clear_signing_key_cache()
        assert isinstance(jwt_service._get_signing_key(), str), "HS256 must stay a plain string"


class TestWebAuthnRunsOffTheLoop:
    def test_the_verification_is_offloaded(self) -> None:
        source = __import__("pathlib").Path("src/app/modules/auth/services/passkey.py").read_text(encoding="utf-8")
        # Both call sites (registration + authentication) go through to_thread.
        assert source.count("await asyncio.to_thread(\n                self._verify_with_webauthn") == 1
        assert source.count("await asyncio.to_thread(\n                self._verify_authentication_response") == 1


class TestTheRejectedCutsStayRejected:
    """F-13 Cuts 3/4 were measured and rejected. Record the measurements."""

    def test_model_validate_is_faster_than_model_construct(self) -> None:
        payload = _auth_payload()
        import timeit

        validate = min(timeit.repeat(lambda: UserAuthInfo.model_validate(payload), number=2000, repeat=3))
        construct = min(timeit.repeat(lambda: UserAuthInfo.model_construct(**payload), number=2000, repeat=3))
        assert validate <= construct, (
            "Cut 3 assumed model_construct was cheaper; if that ever flips, re-open F-13 Cut 3"
        )

    def test_authenticated_user_cannot_take_the_model_directly(self) -> None:
        """Cut 4's premise is false: `tier` is a nested model, not a dict."""
        from pydantic import ValidationError

        model = UserAuthInfo.model_validate(_auth_payload())
        with pytest.raises(ValidationError):
            AuthenticatedUser.model_validate(model)
        # The dict round-trip is what makes it work.
        assert AuthenticatedUser(**model.model_dump()).tier == {
            "id": 2,
            "created_at": dt.datetime(2024, 1, 1),
            "updated_at": None,
            "name": "free",
        }


def _auth_payload() -> dict[str, Any]:
    return {
        "id": 1,
        "created_at": dt.datetime(2024, 1, 1),
        "updated_at": None,
        "name": "N",
        "username": "u",
        "email": "e@x.com",
        "profile_image_url": None,
        "tenant_id": uuid.uuid4(),
        "tier_id": 2,
        "hashed_password": "h",
        "is_superuser": False,
        "is_deleted": False,
        "is_active": True,
        "is_email_verified": True,
        "token_version": 3,
        "tier": {"id": 2, "created_at": dt.datetime(2024, 1, 1), "updated_at": None, "name": "free"},
    }
