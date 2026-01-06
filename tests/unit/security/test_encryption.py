"""Unit tests for credential encryption utilities.

Tests follow TDD approach - these tests define the expected behavior
for the CredentialEncryption class implementation.
"""

import logging

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

    def test_encryption_logs_audit_event(self, caplog):
        """Test that encryption operations are logged."""
        from src.app.core.security.encryption import CredentialEncryption

        encryption = CredentialEncryption()

        with caplog.at_level(logging.DEBUG, logger="src.app.core.security.encryption"):
            encryption.encrypt_credential(b"test data")

        # Should log encryption event
        assert any("encrypt" in record.message.lower() for record in caplog.records)

    def test_decryption_logs_audit_event(self, caplog):
        """Test that decryption operations are logged."""
        from src.app.core.security.encryption import CredentialEncryption

        encryption = CredentialEncryption()
        encrypted = encryption.encrypt_credential(b"test data")

        caplog.clear()

        with caplog.at_level(logging.DEBUG, logger="src.app.core.security.encryption"):
            encryption.decrypt_credential(encrypted)

        # Should log decryption event
        assert any("decrypt" in record.message.lower() for record in caplog.records)


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
