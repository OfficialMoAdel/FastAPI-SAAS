"""P0-3: leaked-secret blocklists must reject published values fail-closed.

Pins the append-only blocklists (SECRET_KEY / ADMIN_PASSWORD /
POSTGRES_PASSWORD) and the fail-closed placeholder convention used by the
tracked scripts/*/.env.example files.
"""

import pytest
from pydantic import ValidationError

from src.app.core.config.auth import KNOWN_LEAKED_ADMIN_PASSWORDS, FirstUserSettings
from src.app.core.config.crypt import KNOWN_LEAKED_SECRET_KEYS, CryptSettings
from src.app.core.config.database import KNOWN_LEAKED_POSTGRES_PASSWORDS

LEAKED_SECRET_KEY = "de2132a4a3a029d6a93a2aefcb519f0219990f92ca258a7c5ed938a444dbe1c8"
PUBLISHED_EXAMPLE_KEY_1 = "953843cd400d99a039698e7feb46ca1b3e33c44fee2c24c6d88cf0f0b290fb61"
PUBLISHED_EXAMPLE_KEY_2 = "db210482bea9aae930b00b17f3449a21340c281ac7e1f2a4e33e2c5cd77f291e"


def test_blocklists_cover_published_values():
    # Keys previously shipped in tracked scripts/*/.env.example files are
    # published in the repo and therefore leaked — they must stay blocklisted.
    assert LEAKED_SECRET_KEY in KNOWN_LEAKED_SECRET_KEYS
    assert PUBLISHED_EXAMPLE_KEY_1 in KNOWN_LEAKED_SECRET_KEYS
    assert PUBLISHED_EXAMPLE_KEY_2 in KNOWN_LEAKED_SECRET_KEYS
    assert "Str1ngst!" in KNOWN_LEAKED_ADMIN_PASSWORDS
    assert "mypassword" in KNOWN_LEAKED_POSTGRES_PASSWORDS


def test_crypt_settings_rejects_leaked_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", LEAKED_SECRET_KEY)
    with pytest.raises(ValidationError, match="leaked into git history"):
        CryptSettings()


def test_crypt_settings_rejects_published_example_keys(monkeypatch):
    for key in (PUBLISHED_EXAMPLE_KEY_1, PUBLISHED_EXAMPLE_KEY_2):
        monkeypatch.setenv("SECRET_KEY", key)
        with pytest.raises(ValidationError, match="leaked into git history"):
            CryptSettings()


def test_crypt_settings_rejects_placeholder(monkeypatch):
    # REPLACE-ME fails the 32-char minimum with a regeneration hint.
    monkeypatch.setenv("SECRET_KEY", "REPLACE-ME")
    with pytest.raises(ValidationError, match="at least 32 characters"):
        CryptSettings()


def test_crypt_settings_accepts_fresh_key(monkeypatch):
    fresh = "a" * 64  # stand-in for `openssl rand -hex 32`
    monkeypatch.setenv("SECRET_KEY", fresh)
    settings = CryptSettings()
    assert settings.SECRET_KEY is not None
    assert settings.SECRET_KEY.get_secret_value() == fresh


def test_admin_password_rejects_leaked_value(monkeypatch):
    # "Str1ngst!" trips min_length=16 as well as the blocklist — either way
    # boot must fail. No match string: both errors are acceptable fail-closed.
    monkeypatch.setenv("ADMIN_PASSWORD", "Str1ngst!")
    with pytest.raises(ValidationError):
        FirstUserSettings()
