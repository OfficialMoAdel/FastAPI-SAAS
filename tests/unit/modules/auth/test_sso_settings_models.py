"""Unit tests for SSOSettings model.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T031 [US2] Write unit tests for SSOSettings model
"""

from sqlalchemy import inspect


class TestSSOSettingsModel:
    """Test SSOSettings SQLAlchemy model."""

    def test_sso_settings_model_exists(self):
        """Verify SSOSettings model can be imported."""
        from src.app.modules.auth.models import SSOSettings

        assert SSOSettings is not None

    def test_sso_settings_table_name(self):
        """Verify table name is 'sso_settings'."""
        from src.app.modules.auth.models import SSOSettings

        assert SSOSettings.__tablename__ == "sso_settings"

    def test_sso_settings_has_required_columns(self):
        """Verify all required columns exist per data-model.md."""
        from src.app.modules.auth.models import SSOSettings

        mapper = inspect(SSOSettings)
        column_names = [col.key for col in mapper.columns]

        required_columns = [
            "id",
            "provider",
            "client_id",
            "client_secret",
            "additional_config",
            "is_enabled",
            "created_at",
            "updated_at",
        ]

        for col in required_columns:
            assert col in column_names, f"Missing required column: {col}"

    def test_sso_settings_provider_unique_constraint(self):
        """Verify provider column has unique constraint."""
        from src.app.modules.auth.models import SSOSettings

        mapper = inspect(SSOSettings)
        provider_col = mapper.columns["provider"]

        # Provider should be unique (one record per provider)
        assert provider_col.unique

    def test_sso_settings_provider_max_length(self):
        """Verify provider column has appropriate max length."""
        from src.app.modules.auth.models import SSOSettings

        mapper = inspect(SSOSettings)
        provider_col = mapper.columns["provider"]

        assert provider_col.type.length >= 50

    def test_sso_settings_client_id_bytea_type(self):
        """Verify client_id uses BYTEA for encrypted storage."""
        from src.app.modules.auth.models import SSOSettings

        mapper = inspect(SSOSettings)
        client_id_col = mapper.columns["client_id"]

        assert "BYTEA" in str(client_id_col.type).upper()

    def test_sso_settings_client_secret_bytea_type(self):
        """Verify client_secret uses BYTEA for encrypted storage."""
        from src.app.modules.auth.models import SSOSettings

        mapper = inspect(SSOSettings)
        client_secret_col = mapper.columns["client_secret"]

        assert "BYTEA" in str(client_secret_col.type).upper()

    def test_sso_settings_additional_config_jsonb_type(self):
        """Verify additional_config uses JSONB for provider-specific settings."""
        from src.app.modules.auth.models import SSOSettings

        mapper = inspect(SSOSettings)
        config_col = mapper.columns["additional_config"]

        assert "JSONB" in str(config_col.type).upper()

    def test_sso_settings_additional_config_nullable(self):
        """Verify additional_config is nullable (optional)."""
        from src.app.modules.auth.models import SSOSettings

        mapper = inspect(SSOSettings)
        config_col = mapper.columns["additional_config"]

        assert config_col.nullable

    def test_sso_settings_is_enabled_default(self):
        """Verify is_enabled defaults to True."""
        from src.app.modules.auth.models import SSOSettings

        mapper = inspect(SSOSettings)
        enabled_col = mapper.columns["is_enabled"]

        # Default should be True
        assert enabled_col.default is not None

    def test_sso_settings_no_tenant_id_column(self):
        """Verify no tenant_id column (host-level table per Constitution VII exception)."""
        from src.app.modules.auth.models import SSOSettings

        mapper = inspect(SSOSettings)
        column_names = [col.key for col in mapper.columns]

        # SSOSettings is host-level, should NOT have tenant_id
        assert "tenant_id" not in column_names

    def test_sso_settings_created_at_auto_set(self):
        """Verify created_at has server_default."""
        from src.app.modules.auth.models import SSOSettings

        mapper = inspect(SSOSettings)
        created_at_col = mapper.columns["created_at"]

        assert created_at_col.server_default is not None

    def test_sso_settings_updated_at_auto_update(self):
        """Verify updated_at has onupdate trigger."""
        from src.app.modules.auth.models import SSOSettings

        mapper = inspect(SSOSettings)
        updated_at_col = mapper.columns["updated_at"]

        assert updated_at_col.onupdate is not None
