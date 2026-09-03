"""Unit tests for PasskeyCredential model.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T065 [US7] Write unit tests for PasskeyCredential model

Tests the PasskeyCredential SQLAlchemy model structure, constraints,
and field specifications.
"""

from datetime import UTC, datetime
from uuid import uuid4


class TestPasskeyCredentialModel:
    """Tests for PasskeyCredential model structure."""

    def test_model_exists(self):
        """Verify PasskeyCredential model can be imported."""
        from src.app.modules.auth.models import PasskeyCredential

        assert PasskeyCredential is not None

    def test_model_tablename(self):
        """Verify model uses correct table name."""
        from src.app.modules.auth.models import PasskeyCredential

        assert PasskeyCredential.__tablename__ == "passkey_credentials"

    def test_model_has_required_columns(self):
        """Verify model has all required columns from data-model.md."""
        from src.app.modules.auth.models import PasskeyCredential

        required_columns = [
            "id",
            "user_id",
            "credential_id",
            "public_key",
            "counter",
            "device_type",
            "aaguid",
            "transports",
            "created_at",
            "last_used_at",
        ]

        model_columns = [col.name for col in PasskeyCredential.__table__.columns]

        for col in required_columns:
            assert col in model_columns, f"Missing column: {col}"

    def test_id_is_primary_key(self):
        """Verify id is primary key with auto-increment."""
        from src.app.modules.auth.models import PasskeyCredential

        id_col = PasskeyCredential.__table__.columns["id"]
        assert id_col.primary_key is True
        assert id_col.autoincrement is True

    def test_user_id_is_foreign_key(self):
        """Verify user_id references users table with CASCADE delete."""
        from src.app.modules.auth.models import PasskeyCredential

        user_id_col = PasskeyCredential.__table__.columns["user_id"]
        assert user_id_col.nullable is False
        assert len(user_id_col.foreign_keys) == 1

        fk = list(user_id_col.foreign_keys)[0]
        assert "users.id" in str(fk.target_fullname)
        assert fk.ondelete == "CASCADE"

    def test_credential_id_is_unique_bytes(self):
        """Verify credential_id is unique BYTEA column."""
        from src.app.modules.auth.models import PasskeyCredential

        col = PasskeyCredential.__table__.columns["credential_id"]
        assert col.nullable is False
        assert col.unique is True
        # BYTEA type check
        assert "BYTEA" in str(col.type).upper() or "VARBINARY" in str(col.type).upper()

    def test_public_key_is_bytes(self):
        """Verify public_key is BYTEA column."""
        from src.app.modules.auth.models import PasskeyCredential

        col = PasskeyCredential.__table__.columns["public_key"]
        assert col.nullable is False
        assert "BYTEA" in str(col.type).upper() or "VARBINARY" in str(col.type).upper()

    def test_counter_defaults_to_zero(self):
        """Verify counter is BIGINT with default 0."""
        from src.app.modules.auth.models import PasskeyCredential

        col = PasskeyCredential.__table__.columns["counter"]
        assert col.nullable is False
        assert col.default is not None

    def test_device_type_is_string(self):
        """Verify device_type is VARCHAR(100)."""
        from src.app.modules.auth.models import PasskeyCredential

        col = PasskeyCredential.__table__.columns["device_type"]
        assert col.nullable is False
        # Check string type with length
        assert hasattr(col.type, "length") and col.type.length == 100

    def test_aaguid_is_nullable_uuid(self):
        """Verify aaguid is optional UUID column."""
        from src.app.modules.auth.models import PasskeyCredential

        col = PasskeyCredential.__table__.columns["aaguid"]
        assert col.nullable is True

    def test_transports_is_nullable_array(self):
        """Verify transports is optional array of strings."""
        from src.app.modules.auth.models import PasskeyCredential

        col = PasskeyCredential.__table__.columns["transports"]
        assert col.nullable is True
        # Should be ARRAY type
        assert "ARRAY" in str(col.type).upper() or "[]" in str(col.type) or "JSON" in str(col.type).upper()

    def test_created_at_has_server_default(self):
        """Verify created_at has server default timestamp."""
        from src.app.modules.auth.models import PasskeyCredential

        col = PasskeyCredential.__table__.columns["created_at"]
        assert col.nullable is False
        assert col.server_default is not None

    def test_last_used_at_is_nullable(self):
        """Verify last_used_at is optional timestamp."""
        from src.app.modules.auth.models import PasskeyCredential

        col = PasskeyCredential.__table__.columns["last_used_at"]
        assert col.nullable is True

    def test_has_user_relationship(self):
        """Verify model has user relationship."""
        from src.app.modules.auth.models import PasskeyCredential

        # Check relationship exists in mapper
        mapper = PasskeyCredential.__mapper__
        assert "user" in mapper.relationships


class TestPasskeyCredentialInstantiation:
    """Tests for PasskeyCredential instance creation."""

    def test_create_minimal_instance(self):
        """Verify model can be instantiated with required fields."""
        from src.app.modules.auth.models import PasskeyCredential

        credential = PasskeyCredential(
            user_id=1,
            credential_id=b"test-credential-id",
            public_key=b"test-public-key",
            device_type="Security Key",
        )

        assert credential.user_id == 1
        assert credential.credential_id == b"test-credential-id"
        assert credential.public_key == b"test-public-key"
        assert credential.device_type == "Security Key"

    def test_create_full_instance(self):
        """Verify model can be instantiated with all fields."""
        from src.app.modules.auth.models import PasskeyCredential

        test_uuid = uuid4()
        now = datetime.now(UTC)

        credential = PasskeyCredential(
            user_id=1,
            credential_id=b"test-credential-id",
            public_key=b"test-public-key",
            counter=5,
            device_type="Touch ID",
            aaguid=test_uuid,
            transports=["internal", "hybrid"],
            last_used_at=now,
        )

        assert credential.user_id == 1
        assert credential.counter == 5
        assert credential.device_type == "Touch ID"
        assert credential.aaguid == test_uuid
        assert credential.transports == ["internal", "hybrid"]
        assert credential.last_used_at == now

    def test_counter_defaults_to_zero_on_instance(self):
        """Verify counter defaults to 0 when not specified."""
        from src.app.modules.auth.models import PasskeyCredential

        credential = PasskeyCredential(
            user_id=1,
            credential_id=b"test-id",
            public_key=b"test-key",
            device_type="Test",
        )

        # Default is set in column definition
        assert credential.counter == 0

    def test_optional_fields_default_to_none(self):
        """Verify optional fields default to None."""
        from src.app.modules.auth.models import PasskeyCredential

        credential = PasskeyCredential(
            user_id=1,
            credential_id=b"test-id",
            public_key=b"test-key",
            device_type="Test",
        )

        assert credential.aaguid is None
        assert credential.transports is None
        assert credential.last_used_at is None


class TestPasskeyCredentialValidTransports:
    """Tests for valid transport values according to WebAuthn spec."""

    def test_valid_transport_usb(self):
        """Verify 'usb' is a valid transport."""
        from src.app.modules.auth.models import PasskeyCredential

        credential = PasskeyCredential(
            user_id=1,
            credential_id=b"test-id",
            public_key=b"test-key",
            device_type="YubiKey",
            transports=["usb"],
        )
        assert "usb" in credential.transports

    def test_valid_transport_nfc(self):
        """Verify 'nfc' is a valid transport."""
        from src.app.modules.auth.models import PasskeyCredential

        credential = PasskeyCredential(
            user_id=1,
            credential_id=b"test-id",
            public_key=b"test-key",
            device_type="YubiKey NFC",
            transports=["nfc"],
        )
        assert "nfc" in credential.transports

    def test_valid_transport_ble(self):
        """Verify 'ble' is a valid transport."""
        from src.app.modules.auth.models import PasskeyCredential

        credential = PasskeyCredential(
            user_id=1,
            credential_id=b"test-id",
            public_key=b"test-key",
            device_type="Bluetooth Key",
            transports=["ble"],
        )
        assert "ble" in credential.transports

    def test_valid_transport_internal(self):
        """Verify 'internal' is a valid transport (platform authenticator)."""
        from src.app.modules.auth.models import PasskeyCredential

        credential = PasskeyCredential(
            user_id=1,
            credential_id=b"test-id",
            public_key=b"test-key",
            device_type="Touch ID",
            transports=["internal"],
        )
        assert "internal" in credential.transports

    def test_valid_transport_hybrid(self):
        """Verify 'hybrid' is a valid transport (cross-device)."""
        from src.app.modules.auth.models import PasskeyCredential

        credential = PasskeyCredential(
            user_id=1,
            credential_id=b"test-id",
            public_key=b"test-key",
            device_type="iPhone Passkey",
            transports=["hybrid"],
        )
        assert "hybrid" in credential.transports

    def test_multiple_transports(self):
        """Verify multiple transports can be specified."""
        from src.app.modules.auth.models import PasskeyCredential

        credential = PasskeyCredential(
            user_id=1,
            credential_id=b"test-id",
            public_key=b"test-key",
            device_type="YubiKey 5",
            transports=["usb", "nfc"],
        )
        assert len(credential.transports) == 2
        assert "usb" in credential.transports
        assert "nfc" in credential.transports
