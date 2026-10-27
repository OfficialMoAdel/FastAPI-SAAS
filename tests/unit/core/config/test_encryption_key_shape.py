"""A wrong-shaped ``ENCRYPTION_KEY`` must be rejected at SETTINGS LOAD.

The bug this pins: ``ENCRYPTION_KEY`` is handed verbatim to
``cryptography.fernet.Fernet``, which requires exactly 32 url-safe
base64-encoded bytes (44 chars). Nothing validated that until the first
encrypt/decrypt, so a 64-hex value — which is what ``openssl rand -hex 32``
produces, and the correct generator for ``SECRET_KEY``/``ENCRYPTION_SALT`` —
booted cleanly and then raised on the passkey write path, the Stripe secret
read, and the storage-credential read.

``CryptSettings.validate_encryption_key_shape`` closes it. The check calls
``Fernet(key)`` itself rather than matching a regex: a shape approximation
would accept values the real constructor rejects, which is the bug class being
closed.

Absence is still allowed on purpose: ``ENCRYPTION_KEY`` is optional in local
dev, ``_get_key_from_settings`` raises a clear error at use, and
production/staging presence is enforced by ``validate_production_secrets``.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from src.app.core.config.crypt import CryptSettings

#: 64 hex chars — what `openssl rand -hex 32` produces. NOT a Fernet key.
HEX_DIGEST = "342dff3aca087def5d838fb4c43199c7abf2c5ce5814147a03140f58d7dc3ded"


def _settings(**overrides: object) -> CryptSettings:
    """Build CryptSettings with a valid SECRET_KEY unless overridden.

    ``_env_file=None`` is not enough on its own: ``core.config`` calls
    ``load_dotenv()`` at import time, which injects ``src/.env`` into
    ``os.environ``, so a real deployment key leaks into the test process. Each
    test that asserts about ABSENCE must therefore remove the variable from the
    process environment — see :class:`TestAbsenceIsStillAllowed`.
    """
    kwargs: dict[str, object] = {"SECRET_KEY": "s" * 64}
    kwargs.update(overrides)
    return CryptSettings(_env_file=None, **kwargs)  # type: ignore[arg-type]


class TestTheHexDigestTrap:
    """The exact value that shipped in src/.env."""

    def test_a_hex_digest_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not a valid Fernet key"):
            _settings(ENCRYPTION_KEY=HEX_DIGEST)

    def test_the_error_names_the_right_generator(self) -> None:
        """The message must be actionable, and must call out the wrong one.

        `openssl rand -hex 32` is correct for SECRET_KEY and ENCRYPTION_SALT,
        which is exactly why an operator reaches for it here. A message that
        only says "invalid" leaves them re-running the same command.
        """
        with pytest.raises(ValidationError) as exc_info:
            _settings(ENCRYPTION_KEY=HEX_DIGEST)

        message = str(exc_info.value)
        assert "Fernet.generate_key()" in message
        assert "openssl rand -hex 32" in message
        assert "WRONG generator" in message

    def test_a_truncated_hex_digest_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not a valid Fernet key"):
            _settings(ENCRYPTION_KEY=HEX_DIGEST[:32])

    def test_an_arbitrary_passphrase_is_rejected(self) -> None:
        """32 chars is not 44 base64 chars; a passphrase is not a key."""
        with pytest.raises(ValidationError, match="not a valid Fernet key"):
            _settings(ENCRYPTION_KEY="k" * 32)


class TestAValidKeyIsAccepted:
    def test_a_generated_fernet_key_is_accepted(self) -> None:
        key = Fernet.generate_key().decode()
        assert _settings(ENCRYPTION_KEY=key).ENCRYPTION_KEY is not None

    def test_the_accepted_key_actually_encrypts(self) -> None:
        """Counter-test: acceptance must mean usable, not merely non-raising."""
        key = Fernet.generate_key().decode()
        settings = _settings(ENCRYPTION_KEY=key)
        assert settings.ENCRYPTION_KEY is not None

        fernet = Fernet(settings.ENCRYPTION_KEY.get_secret_value().encode())
        assert fernet.decrypt(fernet.encrypt(b"round-trip")) == b"round-trip"


class TestAbsenceIsStillAllowed:
    """Local dev may omit the key; the failure there is at use, with a clear error."""

    def test_unset_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
        assert _settings().ENCRYPTION_KEY is None

    def test_empty_string_is_treated_as_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty is falsy, so the shape check skips it — no raise either way.

        An empty value from the ENVIRONMENT arrives as ``None`` (pydantic-settings
        maps empty env vars to the field default); an empty value passed as an
        explicit argument arrives as ``SecretStr('')``. Both are falsy, and the
        validator skips both, so the contract is "does not raise", not "is None".
        """
        monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
        assert not _settings(ENCRYPTION_KEY="").ENCRYPTION_KEY

    def test_an_empty_env_value_is_falsy_and_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty value means "not configured" and must not fail the shape check.

        Pydantic-settings may surface an empty env var as ``None`` or as
        ``SecretStr('')`` depending on version and precedence; both are falsy and
        the validator skips both, so the CONTRACT is "does not raise and is not
        treated as configured" — not a specific sentinel. Pinning the sentinel
        would test pydantic, not this code.
        """
        monkeypatch.setenv("ENCRYPTION_KEY", "")
        assert not _settings().ENCRYPTION_KEY


class TestTheRotationKeyGetsTheSameCheck:
    """ENCRYPTION_KEY_OLD is a Fernet key too; a bad one breaks decryption only.

    That asymmetry is what makes it dangerous: new writes succeed, so the
    deployment looks healthy while every pre-rotation ciphertext fails to
    decrypt.
    """

    def test_a_hex_old_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ENCRYPTION_KEY_OLD is not a valid Fernet key"):
            _settings(ENCRYPTION_KEY=Fernet.generate_key().decode(), ENCRYPTION_KEY_OLD=HEX_DIGEST)

    def test_a_valid_old_key_is_accepted(self) -> None:
        settings = _settings(
            ENCRYPTION_KEY=Fernet.generate_key().decode(),
            ENCRYPTION_KEY_OLD=Fernet.generate_key().decode(),
        )
        assert settings.ENCRYPTION_KEY_OLD is not None


class TestTheDeadDerivationHelpersAreGone:
    """The decoy that produced this bug must not come back.

    ``_derive_key``/``_get_salt`` were only ever the SECRET_KEY fallback that
    was removed deliberately (they made "passphrase + KDF" look like the
    intended scheme, so operators generated hex passphrases). They were
    unreachable, and `_derive_key` returned a *valid* Fernet key, which is what
    made the wrong mental model convincing.
    """

    def test_the_helpers_are_not_exported(self) -> None:
        from src.app.core.security import encryption

        assert not hasattr(encryption, "_derive_key")
        assert not hasattr(encryption, "_get_salt")
        assert not hasattr(encryption, "_FALLBACK_SALT")

    def test_the_current_key_path_is_still_the_raw_key(self) -> None:
        """No KDF is applied to ENCRYPTION_KEY — Fernet consumes it verbatim."""
        from src.app.core.security import encryption

        key = Fernet.generate_key().decode()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                encryption.settings.crypt,
                "ENCRYPTION_KEY",
                type(encryption.settings.crypt.ENCRYPTION_KEY)(key),  # SecretStr
            )
            assert encryption._get_key_from_settings() == key.encode()
