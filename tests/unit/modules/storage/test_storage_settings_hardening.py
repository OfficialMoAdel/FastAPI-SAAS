"""P2-5: storage hardening regression tests.

- LocalCredentials.base_path must be absolute with no parent traversal.
- _validate_credentials must writability-probe LOCAL via validate_connection().
- Signed-URL endpoint caps expires_in at 1 hour (le=3600).
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from src.app.modules.storage.errors import InvalidCredentialsError
from src.app.modules.storage.schemas import LocalCredentials
from src.app.modules.storage.service import StorageAdminService


def _absolute_dir() -> str:
    return tempfile.gettempdir()


class TestLocalCredentialsBasePath:
    """P2-5: base_path cannot escape the uploads root."""

    def test_accepts_absolute_path(self) -> None:
        creds = LocalCredentials(base_path=_absolute_dir())
        assert creds.base_path == _absolute_dir()

    def test_rejects_relative_path(self) -> None:
        with pytest.raises(ValidationError):
            LocalCredentials(base_path=os.path.join("uploads", "tenant"))

    def test_rejects_parent_traversal(self) -> None:
        evil = os.path.join(_absolute_dir(), "..", "etc")
        with pytest.raises(ValidationError):
            LocalCredentials(base_path=evil)


class TestValidateCredentialsRouting:
    """P2-5: LOCAL uses the writability probe; others use exists()."""

    def _service_with_provider(self, provider: MagicMock) -> StorageAdminService:
        provider.close = AsyncMock()
        service = StorageAdminService(db=MagicMock())
        service._create_provider = MagicMock(return_value=provider)  # type: ignore[method-assign]
        return service

    async def test_local_uses_validate_connection(self) -> None:
        from src.app.modules.storage.models import StorageProviderType

        provider = MagicMock()
        provider.validate_connection = AsyncMock(return_value=True)
        service = self._service_with_provider(provider)

        await service._validate_credentials(StorageProviderType.LOCAL, {"base_path": "/data"})

        provider.validate_connection.assert_awaited_once()
        provider.exists.assert_not_called()

    async def test_local_unwritable_raises(self) -> None:
        from src.app.modules.storage.models import StorageProviderType

        provider = MagicMock()
        provider.validate_connection = AsyncMock(return_value=False)
        service = self._service_with_provider(provider)

        with pytest.raises(InvalidCredentialsError):
            await service._validate_credentials(StorageProviderType.LOCAL, {"base_path": "/data"})

    async def test_local_without_probe_raises(self) -> None:
        from src.app.modules.storage.models import StorageProviderType

        provider = MagicMock(spec=["exists"])
        provider.exists = AsyncMock(return_value=True)
        service = self._service_with_provider(provider)

        with pytest.raises(InvalidCredentialsError):
            await service._validate_credentials(StorageProviderType.LOCAL, {"base_path": "/data"})

    async def test_non_local_uses_exists(self) -> None:
        from src.app.modules.storage.models import StorageProviderType

        provider = MagicMock()
        provider.exists = AsyncMock(return_value=True)
        service = self._service_with_provider(provider)

        await service._validate_credentials(
            StorageProviderType.S3, {"bucket_name": "b", "region": "us-east-1"}
        )

        provider.exists.assert_awaited_once_with("__connection_test__")


class TestSignedUrlExpiryCap:
    """P2-5: GET /files/{id}/url caps expires_in at 1 hour."""

    def test_expires_in_capped_at_3600(self) -> None:
        from src.app.modules.storage.routers.files import router

        route = next(r for r in router.routes if getattr(r, "path", "") == "/files/{file_id}/url")
        param = next(p for p in route.dependant.query_params if p.name == "expires_in")
        # P2-5: constraints live on the annotated-types metadata (Ge/Le).
        ge = next(m.ge for m in param.field_info.metadata if hasattr(m, "ge"))
        le = next(m.le for m in param.field_info.metadata if hasattr(m, "le"))
        assert ge == 60
        assert le == 3600
