"""Unit tests for credential encryption utilities.

Tests follow TDD approach - these tests define the expected behavior
for the CredentialEncryption class implementation.
"""

import pytest
from cryptography.fernet import Fernet


class TestCredentialEncryption:
    """Test credential encryption utilities."""

    def test_encrypt_decrypt_roundtrip(self):
        """Test that encrypted data can be decrypted back to original."""
        from src.app.core.security.encryption import CredentialEncryption

        encryption = CredentialEncryption()
        original_data = b"test credential data"

        encrypted = encryption.encrypt_credential(original_data)
        decrypted = encryption.decrypt_credential(encrypted)

        assert decrypted == original_data
        assert encrypted != original_data

    def test_encrypted_data_differs_from_original(self):
        """Test that encrypted data is not readable."""
        from src.app.core.security.encryption import CredentialEncryption

        encryption = CredentialEncryption()
        original = b"sensitive credential information"

        encrypted = encryption.encrypt_credential(original)

        assert encrypted != original
        # Encrypted data should not contain plaintext
        assert original not in encrypted

    def test_different_inputs_produce_different_outputs(self):
        """Test that same input encrypted twice produces different output (IV)."""
        from src.app.core.security.encryption import CredentialEncryption

        encryption = CredentialEncryption()
        data = b"same data"

        encrypted1 = encryption.encrypt_credential(data)
        encrypted2 = encryption.encrypt_credential(data)

        # Fernet uses timestamps and random IV, so same data produces different output
        assert encrypted1 != encrypted2

    def test_column_encryption_roundtrip(self):
        """Test string column encryption for database storage."""
        from src.app.core.security.encryption import CredentialEncryption

        encryption = CredentialEncryption()
        original_value = "sensitive_string_value"

        encrypted = encryption.encrypt_column(original_value)
        decrypted = encryption.decrypt_column(encrypted)

        assert decrypted == original_value
        assert isinstance(encrypted, str)

    def test_invalid_encrypted_data_raises_error(self):
        """Test that tampered encrypted data raises error."""
        from src.app.core.security.encryption import CredentialEncryption, DecryptionError

        encryption = CredentialEncryption()
        valid_encrypted = encryption.encrypt_column("test")

        # Tamper with the data
        tampered = valid_encrypted[:-5] + "xxxxx"

        with pytest.raises(DecryptionError):
            encryption.decrypt_column(tampered)

    def test_encryption_key_from_settings(self):
        """Test that encryption key is loaded from settings."""
        from src.app.core.security.encryption import CredentialEncryption

        encryption = CredentialEncryption()

        # Verify fernet is properly initialized
        assert encryption._fernet is not None
        assert isinstance(encryption._fernet, Fernet)

    def test_custom_key_initialization(self):
        """Test that custom key can be provided."""
        from src.app.core.security.encryption import CredentialEncryption

        custom_key = Fernet.generate_key()
        encryption = CredentialEncryption(key=custom_key)

        data = b"test data"
        encrypted = encryption.encrypt_credential(data)
        decrypted = encryption.decrypt_credential(encrypted)

        assert decrypted == data


class TestEncryptionKeyGeneration:
    """Test encryption key generation utilities."""

    def test_generate_fernet_key(self):
        """Test that we can generate a valid Fernet key."""
        from src.app.core.security.encryption import generate_fernet_key

        key = generate_fernet_key()

        assert isinstance(key, bytes)
        assert len(key) == 44  # Fernet key is 32 bytes base64-encoded

        # Verify it's a valid Fernet key
        fernet = Fernet(key)
        assert fernet.encrypt(b"test") is not None

    def test_different_keys_produce_different_encryption(self):
        """Test that different keys produce different encrypted outputs."""
        from src.app.core.security.encryption import CredentialEncryption, generate_fernet_key

        key1 = generate_fernet_key()
        key2 = generate_fernet_key()

        enc1 = CredentialEncryption(key=key1)
        enc2 = CredentialEncryption(key=key2)

        data = b"test message"
        encrypted1 = enc1.encrypt_credential(data)
        encrypted2 = enc2.encrypt_credential(data)

        assert encrypted1 != encrypted2


class TestKeyVersioning:
    """Test key versioning for audit trails."""

    def test_encrypted_data_includes_version(self):
        """Test that encrypted data includes version prefix."""
        from src.app.core.security.encryption import CredentialEncryption

        encryption = CredentialEncryption()
        encrypted = encryption.encrypt_credential(b"test data")

        # Should start with version prefix
        assert encrypted.startswith(b"v1:")

    def test_decrypt_versioned_data(self):
        """Test that versioned data can be decrypted."""
        from src.app.core.security.encryption import CredentialEncryption

        encryption = CredentialEncryption()
        original = b"versioned test data"

        encrypted = encryption.encrypt_credential(original)
        decrypted = encryption.decrypt_credential(encrypted)

        assert decrypted == original

    def test_decrypt_legacy_unversioned_data(self):
        """Test that legacy data without version can still be decrypted."""
        from src.app.core.security.encryption import CredentialEncryption

        encryption = CredentialEncryption()

        # Simulate legacy encrypted data (no version prefix)
        legacy_encrypted = encryption._fernet.encrypt(b"legacy data")

        # Should still decrypt successfully
        decrypted = encryption.decrypt_credential(legacy_encrypted)
        assert decrypted == b"legacy data"


class TestDictEncryption:
    """Test dictionary encryption for batch operations."""

    def test_encrypt_dict_roundtrip(self):
        """Test dictionary encryption and decryption."""
        from src.app.core.security.encryption import CredentialEncryption

        encryption = CredentialEncryption()
        original = {
            "api_key": "secret_key_123",
            "api_secret": "secret_value_456",
        }

        encrypted = encryption.encrypt_dict(original)
        decrypted = encryption.decrypt_dict(encrypted)

        assert decrypted == original

    def test_encrypt_dict_produces_different_values(self):
        """Test that dict values are actually encrypted."""
        from src.app.core.security.encryption import CredentialEncryption

        encryption = CredentialEncryption()
        original = {"key": "value"}

        encrypted = encryption.encrypt_dict(original)

        assert encrypted["key"] != original["key"]


class TestAuditLogging:
    """Test audit logging for encryption operations."""

    from unittest.mock import patch

    @patch("src.app.core.security.encryption.logger.debug")
    def test_encryption_logs_audit_event(self, mock_logger):
        """Test that encryption operations are logged."""
        from src.app.core.security.encryption import CredentialEncryption

        encryption = CredentialEncryption()
        encryption.encrypt_credential(b"test data")

        assert mock_logger.called

    from unittest.mock import patch

    @patch("src.app.core.security.encryption.logger.debug")
    def test_decryption_logs_audit_event(self, mock_logger):
        """Test that decryption operations are logged."""
        from src.app.core.security.encryption import CredentialEncryption

        encryption = CredentialEncryption()
        encrypted = encryption.encrypt_credential(b"test data")

        mock_logger.reset_mock()
        encryption.decrypt_credential(encrypted)

        assert mock_logger.called


class TestDependencyInjection:
    """Test dependency injection support."""

    def test_get_encryption_returns_instance(self):
        """Test that get_encryption factory returns CredentialEncryption."""
        from src.app.core.security.encryption import CredentialEncryption, get_encryption

        encryption = get_encryption()

        assert isinstance(encryption, CredentialEncryption)

    def test_multiple_get_encryption_calls_return_same_instance(self):
        """Test that get_encryption returns cached instance for efficiency."""
        from src.app.core.security.encryption import get_encryption

        enc1 = get_encryption()
        enc2 = get_encryption()

        # Should return same instance for performance (lazy singleton)
        assert enc1 is enc2


class TestBackwardCompatibility:
    """Test backward compatibility with existing encrypt_data/decrypt_data functions."""

    def test_encrypt_data_function(self):
        """Test that encrypt_data function still works."""
        from src.app.core.security.encryption import decrypt_data, encrypt_data

        original = "test string"
        encrypted = encrypt_data(original)
        decrypted = decrypt_data(encrypted)

        assert decrypted == original

    def test_rotate_encryption_function(self):
        """Test that rotate_encryption function still works."""
        from src.app.core.security.encryption import encrypt_data, rotate_encryption

        original = "test string"
        encrypted = encrypt_data(original)

        # Rotate should return valid encrypted data
        rotated = rotate_encryption(encrypted)

        assert rotated is not None
        assert isinstance(rotated, str)


class TestKeyRotation:
    """Test key rotation support via MultiFernet."""

    def test_decrypt_with_previous_key(self):
        """Test that data encrypted with previous key can still be decrypted."""
        from cryptography.fernet import Fernet

        from src.app.core.security.encryption import CredentialEncryption

        # Create two different keys
        old_key = Fernet.generate_key()
        new_key = Fernet.generate_key()

        # Encrypt with old key
        old_encryption = CredentialEncryption(key=old_key)
        encrypted_data = old_encryption.encrypt_credential(b"secret data")

        # Create encryption with new key and old key as previous
        # This tests the MultiFernet capability
        new_encryption = CredentialEncryption(key=new_key, previous_key=old_key)

        # Should be able to decrypt data encrypted with old key
        decrypted = new_encryption.decrypt_credential(encrypted_data)
        assert decrypted == b"secret data"

    def test_new_encryption_uses_current_key(self):
        """Test that new encryption always uses current key."""
        from cryptography.fernet import Fernet

        from src.app.core.security.encryption import CredentialEncryption

        old_key = Fernet.generate_key()
        new_key = Fernet.generate_key()

        # Create encryption with both keys
        encryption = CredentialEncryption(key=new_key, previous_key=old_key)
        encrypted = encryption.encrypt_credential(b"new data")

        # Should be decryptable with just the new key
        new_only_encryption = CredentialEncryption(key=new_key)
        decrypted = new_only_encryption.decrypt_credential(encrypted)

        assert decrypted == b"new data"


class TestTheRotationDiagnosis:
    """A rotated ENCRYPTION_KEY must not be reported as tampering.

    ``InvalidToken`` cannot distinguish "wrong key" from "corrupted bytes" — both
    are just a failed HMAC. The message therefore names BOTH causes with the
    mundane one first, because the old text ("data may be tampered or encrypted
    with different key") sent an operator hunting an attacker when the real cause
    was a key rotation. This became reachable in practice: a hex-digest key was
    rotated out and left one ``storage_settings`` row undecryptable.
    """

    def test_the_error_names_the_rotation_first(self) -> None:
        from src.app.core.security.encryption import CredentialEncryption, DecryptionError

        key_a = Fernet.generate_key()
        stale = CredentialEncryption(key=key_a).encrypt_credential(b"written with the old key")

        with pytest.raises(DecryptionError) as exc_info:
            CredentialEncryption(key=Fernet.generate_key()).decrypt_credential(stale)

        message = str(exc_info.value)
        assert "rotated ENCRYPTION_KEY" in message
        assert "ENCRYPTION_KEY_OLD" in message, "the message must point at the safe rotation path"
        assert message.index("rotated") < message.index("tampered"), (
            "the mundane cause must come first: tampering is the rarer explanation"
        )

    def test_concurrent_tampering_is_still_reported(self) -> None:
        """The reworded message must not stop genuine tampering being rejected."""
        from src.app.core.security.encryption import (
            CURRENT_VERSION,
            VERSION_PREFIX,
            CredentialEncryption,
            DecryptionError,
        )

        encryption = CredentialEncryption()
        blob = encryption.encrypt_credential(b"payload")
        body = blob[len(VERSION_PREFIX) :] if blob.startswith(VERSION_PREFIX) else blob
        index = len(body) // 2
        corrupted = body[:index] + (b"A" if body[index : index + 1] != b"A" else b"B") + body[index + 1 :]
        assert CURRENT_VERSION  # the prefix contract is versioned; asserted for clarity

        with pytest.raises(DecryptionError):
            encryption.decrypt_credential(VERSION_PREFIX + corrupted)

    def test_the_log_records_whether_a_rotation_key_exists(self) -> None:
        """No key material is logged — only whether rotation is configured.

        That single boolean is the difference between "you rotated and kept no
        old key" (data is gone) and "you rotated correctly, so this row should
        have decrypted" (a bug), and it is not otherwise discoverable from a
        stack trace.

        The module's ``logger`` is swapped rather than using
        ``structlog.testing.capture_logs``. Two reasons, both learned from this
        repo:
          * ``logger.py`` configures ``cache_logger_on_first_use=True``, so a
            logger bound earlier in the process is cached and does NOT route
            through a processor chain replaced later;
          * other suites reconfigure the global chain, which silently neuters
            ``capture_logs`` depending on collection order.
        This test passed alone and failed at 32% in the full run before the swap
        — the failure mode `tests/e2e/modules/billing/test_checkout_tenant_authority_e2e.py`
        documents for the same reason. Patching the call-site object is
        order-independent.
        """
        from unittest.mock import MagicMock, patch

        from src.app.core.security.encryption import CredentialEncryption, DecryptionError

        key_a = Fernet.generate_key()
        stale = CredentialEncryption(key=key_a).encrypt_credential(b"old")

        logger = MagicMock()
        with patch("src.app.core.security.encryption.logger", logger):
            with pytest.raises(DecryptionError):
                CredentialEncryption(key=Fernet.generate_key()).decrypt_credential(stale)

        calls = [
            call
            for call in logger.warning.call_args_list
            if call.args and call.args[0] == "decryption_invalid_token"
        ]
        assert calls, f"the failure must be logged with its own event name; got {logger.warning.call_args_list}"
        kwargs = calls[0].kwargs
        assert "has_previous_key" in kwargs, "the rotation state must be recorded"
        assert kwargs["has_previous_key"] is False
        assert "key" not in kwargs, "no key material may be logged"
