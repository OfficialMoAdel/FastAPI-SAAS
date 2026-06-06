"""Unit tests for UserSSOConnection model.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T032 [US2] Write unit tests for UserSSOConnection model
"""

from sqlalchemy import inspect


class TestUserSSOConnectionModel:
    """Test UserSSOConnection SQLAlchemy model."""

    def test_user_sso_connection_model_exists(self):
        """Verify UserSSOConnection model can be imported."""
        from src.app.modules.sso.models import UserSSOConnection

        assert UserSSOConnection is not None

    def test_user_sso_connection_table_name(self):
        """Verify table name is 'user_sso_connections'."""
        from src.app.modules.sso.models import UserSSOConnection

        assert UserSSOConnection.__tablename__ == "user_sso_connections"

    def test_user_sso_connection_has_required_columns(self):
        """Verify all required columns exist per data-model.md."""
        from src.app.modules.sso.models import UserSSOConnection

        mapper = inspect(UserSSOConnection)
        column_names = [col.key for col in mapper.columns]

        required_columns = [
            "id",
            "user_id",
            "tenant_id",
            "provider",
            "provider_user_id",
            "provider_email",
            "access_token",
            "refresh_token",
            "token_expires_at",
            "created_at",
            "updated_at",
        ]

        for col in required_columns:
            assert col in column_names, f"Missing required column: {col}"

    def test_user_sso_connection_has_tenant_id(self):
        """Verify tenant_id exists for row-level isolation per Constitution VII."""
        from src.app.modules.sso.models import UserSSOConnection

        mapper = inspect(UserSSOConnection)
        column_names = [col.key for col in mapper.columns]

        assert "tenant_id" in column_names

    def test_user_sso_connection_tenant_id_foreign_key(self):
        """Verify tenant_id has correct foreign key constraint."""
        from src.app.modules.sso.models import UserSSOConnection

        mapper = inspect(UserSSOConnection)
        tenant_id_col = mapper.columns["tenant_id"]

        fks = list(tenant_id_col.foreign_keys)
        assert len(fks) == 1
        assert "tenants.id" in str(fks[0].target_fullname)

    def test_user_sso_connection_user_id_foreign_key(self):
        """Verify user_id has correct foreign key constraint."""
        from src.app.modules.sso.models import UserSSOConnection

        mapper = inspect(UserSSOConnection)
        user_id_col = mapper.columns["user_id"]

        fks = list(user_id_col.foreign_keys)
        assert len(fks) == 1
        assert "users.id" in str(fks[0].target_fullname)

    def test_user_sso_connection_user_provider_unique_constraint(self):
        """Verify unique constraint on (user_id, provider) pair."""
        from src.app.modules.sso.models import UserSSOConnection

        # Check table args for unique constraint
        table_args = UserSSOConnection.__table_args__
        constraint_names = [c.name for c in table_args if hasattr(c, "name")]

        assert "uq_user_sso_user_provider" in constraint_names

    def test_user_sso_connection_provider_user_unique_constraint(self):
        """Verify unique constraint on (provider, provider_user_id) pair."""
        from src.app.modules.sso.models import UserSSOConnection

        # Check table args for unique constraint
        table_args = UserSSOConnection.__table_args__
        constraint_names = [c.name for c in table_args if hasattr(c, "name")]

        assert "uq_user_sso_provider_user" in constraint_names

    def test_user_sso_connection_access_token_bytea_type(self):
        """Verify access_token uses BYTEA for encrypted storage."""
        from src.app.modules.sso.models import UserSSOConnection

        mapper = inspect(UserSSOConnection)
        token_col = mapper.columns["access_token"]

        assert "BYTEA" in str(token_col.type).upper()

    def test_user_sso_connection_refresh_token_bytea_type(self):
        """Verify refresh_token uses BYTEA for encrypted storage."""
        from src.app.modules.sso.models import UserSSOConnection

        mapper = inspect(UserSSOConnection)
        token_col = mapper.columns["refresh_token"]

        assert "BYTEA" in str(token_col.type).upper()

    def test_user_sso_connection_tokens_nullable(self):
        """Verify access_token and refresh_token are nullable (optional)."""
        from src.app.modules.sso.models import UserSSOConnection

        mapper = inspect(UserSSOConnection)

        assert mapper.columns["access_token"].nullable
        assert mapper.columns["refresh_token"].nullable

    def test_user_sso_connection_provider_email_nullable(self):
        """Verify provider_email is nullable (may differ from user email)."""
        from src.app.modules.sso.models import UserSSOConnection

        mapper = inspect(UserSSOConnection)
        email_col = mapper.columns["provider_email"]

        assert email_col.nullable

    def test_user_sso_connection_provider_user_id_required(self):
        """Verify provider_user_id is required (non-nullable)."""
        from src.app.modules.sso.models import UserSSOConnection

        mapper = inspect(UserSSOConnection)
        provider_id_col = mapper.columns["provider_user_id"]

        assert not provider_id_col.nullable

    def test_user_sso_connection_user_relationship(self):
        """Verify relationship to User model exists."""
        from src.app.modules.sso.models import UserSSOConnection

        mapper = inspect(UserSSOConnection)
        relationships = [r.key for r in mapper.relationships]

        assert "user" in relationships

    def test_user_sso_connection_tenant_relationship(self):
        """Verify relationship to Tenant model exists."""
        from src.app.modules.sso.models import UserSSOConnection

        mapper = inspect(UserSSOConnection)
        relationships = [r.key for r in mapper.relationships]

        assert "tenant" in relationships

    def test_user_sso_connection_user_id_indexed(self):
        """Verify user_id is indexed for query performance."""
        from src.app.modules.sso.models import UserSSOConnection

        mapper = inspect(UserSSOConnection)
        user_id_col = mapper.columns["user_id"]

        assert user_id_col.index

    def test_user_sso_connection_tenant_id_indexed(self):
        """Verify tenant_id is indexed for row-level isolation queries."""
        from src.app.modules.sso.models import UserSSOConnection

        mapper = inspect(UserSSOConnection)
        tenant_id_col = mapper.columns["tenant_id"]

        assert tenant_id_col.index
