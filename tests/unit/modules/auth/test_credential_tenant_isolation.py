"""H1 regression tests: credential-table tenant isolation.

Covers:
- MagicLink / MFAMethod carry a non-nullable CASCADE tenant_id FK (model contract).
- enable_mfa stamps the owner's tenant and fails closed without one.
- verify_totp_token / regenerate paths 404 on cross-user MFA rows.
- verify_magic_link refuses a row bound to a different user.
- PasskeyCredentialCreate requires tenant_id.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

OWNER_TENANT = UUID("22222222-2222-4222-8222-222222222222")
OTHER_USER_ID = 999


class TestCredentialTenantColumns:
    @pytest.mark.parametrize("model_name", ["MagicLink", "MFAMethod"])
    def test_tenant_id_fk_cascade_not_null(self, model_name):
        from src.app.modules.auth import models as auth_models

        model = getattr(auth_models, model_name)
        col = model.__table__.columns["tenant_id"]
        assert col.nullable is False
        assert len(col.foreign_keys) == 1
        fk = list(col.foreign_keys)[0]
        assert "tenants.id" in str(fk.target_fullname)
        assert fk.ondelete == "CASCADE"

    def test_passkey_credential_create_requires_tenant(self):
        from pydantic import ValidationError as PydanticValidationError

        from src.app.modules.auth.schemas import PasskeyCredentialCreate

        with pytest.raises(PydanticValidationError):
            PasskeyCredentialCreate(
                user_id=1,
                credential_id=b"cid",
                public_key=b"pk",
            )
        ok = PasskeyCredentialCreate(
            user_id=1,
            tenant_id=OWNER_TENANT,
            credential_id=b"cid",
            public_key=b"pk",
        )
        assert ok.tenant_id == OWNER_TENANT


def _mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    return db


class TestEnableMfaTenantStamp:
    async def _run_enable(self, db, member):
        from src.app.modules.auth.schemas import MFAEnableRequest
        from src.app.modules.auth.services.mfa import MFAService

        svc = MFAService()
        redis = AsyncMock()
        redis.get.return_value = b"1:SECRET"
        with (
            patch("src.app.modules.auth.services.mfa.crud_mfa_methods") as crud,
            patch("src.app.modules.auth.services.mfa.get_redis", return_value=redis),
            patch("pyotp.TOTP") as totp_cls,
            patch.object(MFAService, "_check_mfa_lockout", new=AsyncMock()),
            patch.object(MFAService, "_generate_backup_codes", new=AsyncMock(return_value=["a", "b"])),
            patch.object(MFAService, "_encrypt_secret", new=AsyncMock(return_value="enc")),
            patch.object(MFAService, "_hash_backup_code", new=AsyncMock(return_value="h")),
            patch("src.app.modules.users.UserDirectory") as directory_cls,
        ):
            crud.get = AsyncMock(return_value=None)
            totp_cls.return_value.verify.return_value = True
            directory_cls.return_value.member = AsyncMock(return_value=member)
            result = await svc.enable_mfa(
                user_id=1,
                request=MFAEnableRequest(setup_token="tok", totp_token="123456", password="correct-horse"),
                db=db,
            )
        return result

    async def test_stamps_owner_tenant(self):
        from src.app.modules.auth.models import MFAMethod

        db = _mock_db()
        member = SimpleNamespace(tenant_id=OWNER_TENANT)
        result = await self._run_enable(db, member)
        assert result["message"] == "MFA enabled successfully"
        created = db.add.call_args[0][0]
        assert isinstance(created, MFAMethod)
        assert created.tenant_id == OWNER_TENANT
        assert created.user_id == 1

    async def test_fails_closed_without_tenant(self):
        from src.app.shared.exceptions import ValidationError

        db = _mock_db()
        with pytest.raises(ValidationError, match="no tenant assignment"):
            await self._run_enable(db, None)
        db.add.assert_not_called()


class TestMfaOwnershipGuards:
    async def _verify_with_row_owner(self, row_user_id):
        from src.app.modules.auth.schemas import MFAVerifyRequest
        from src.app.modules.auth.services.mfa import MFAService

        svc = MFAService()
        with (
            patch("src.app.modules.auth.services.mfa.crud_mfa_methods") as crud,
            patch.object(MFAService, "_check_mfa_lockout", new=AsyncMock()),
            patch.object(MFAService, "_decrypt_secret", new=AsyncMock(return_value="SECRET")),
            patch("pyotp.TOTP") as totp_cls,
        ):
            crud.get = AsyncMock(return_value={"id": "row-1", "secret": "enc"})
            totp_cls.return_value.verify.return_value = True
            db = _mock_db()
            db.get = AsyncMock(return_value=SimpleNamespace(user_id=row_user_id, last_used_at=None))
            return await svc.verify_totp_token(
                user_id=1,
                request=MFAVerifyRequest(totp_token="123456"),
                db=db,
            )

    async def test_verify_allows_owner_row(self):
        result = await self._verify_with_row_owner(row_user_id=1)
        assert result["valid"] is True

    async def test_verify_404_on_foreign_row(self):
        from src.app.shared.exceptions import ModelNotFoundError

        with pytest.raises(ModelNotFoundError):
            await self._verify_with_row_owner(row_user_id=OTHER_USER_ID)


class TestMagicLinkUserBinding:
    async def _verify_with_row_owner(self, row_user_id):
        from src.app.modules.auth.services.magic_link import MagicLinkService

        db = _mock_db()
        redis = AsyncMock()
        svc = MagicLinkService(db=db, redis=redis)
        manager = AsyncMock()
        manager.peek.return_value = {
            "email": "user@example.com",
            "extra_data": {"user_id": 1},
        }
        manager.consume.return_value = {
            "email": "user@example.com",
            "extra_data": {"user_id": 1},
        }
        row = SimpleNamespace(
            user_id=row_user_id,
            used_at=None,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        member = SimpleNamespace(
            model_dump=lambda mode="json": {
                "id": 1,
                "email": "user@example.com",
                "is_active": True,
                "is_deleted": False,
                "tenant_id": str(OWNER_TENANT),
            },
        )
        with (
            patch.object(MagicLinkService, "_get_magic_link_manager", return_value=manager),
            patch("src.app.modules.auth.crud.get_magic_link_by_token", new=AsyncMock(return_value=row)),
            patch("src.app.modules.auth.crud.mark_magic_link_used", new=AsyncMock()),
            patch("src.app.modules.users.UserDirectory") as directory_cls,
        ):
            directory_cls.return_value.member = AsyncMock(return_value=member)
            return await svc.verify_magic_link(token="tok", ip_address="127.0.0.1")

    async def test_binding_mismatch_rejected(self):
        from src.app.modules.auth.errors import InvalidMagicLinkError

        with pytest.raises(InvalidMagicLinkError, match="User not found"):
            await self._verify_with_row_owner(row_user_id=OTHER_USER_ID)
