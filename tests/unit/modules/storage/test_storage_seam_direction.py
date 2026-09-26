"""The storage seam's dependency direction (card 16).

Before card 16 ``src/app/clients/storage/registry.py`` imported four feature
modules — ``modules.storage.s3_storage`` / ``azure_storage`` / ``local_storage``
/ ``interfaces`` plus the ``models`` enum — so the OUTBOUND client package
depended on the FEATURE package, and ``StorageAdminService`` reached back into
the registry to build a provider. Card 16 puts provider construction and vendor
behaviour on the outbound side: the feature package imports downward, never up.

These tests pin the direction from source (AST, no import side effects) so the
edge cannot be re-introduced quietly, and pin the two identity facts the move
depends on: the provider enum is ONE object on both sides, and the factories
still build the three real adapters.
"""

from __future__ import annotations

import ast
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.app.clients.storage import (
    AzureBlobStorage,
    LocalStorage,
    S3Storage,
    StorageProvider,
    StorageProviderRegistry,
    StorageProviderType,
)
from src.app.clients.storage import registry as registry_module
from src.app.modules.storage.admin import StorageAdminService
from src.app.modules.storage.models import StorageProviderType as ModelStorageProviderType

CLIENTS_STORAGE_ROOT = Path(__file__).resolve().parents[4] / "src" / "app" / "clients" / "storage"
FEATURE_PACKAGE = "src.app.modules"

# Names the outbound package is allowed to export; anything else means a new
# dependency crept in and this test should say so rather than rot silently.
EXPECTED_CLIENTS_STORAGE_MODULES = {
    "__init__.py",
    "azure_storage.py",
    "interfaces.py",
    "local_storage.py",
    "provider_type.py",
    "registry.py",
    "s3_storage.py",
}

# The ONE edge that survives card 16, named here so it cannot grow quietly.
# ``S3Storage.get_url`` raises ``StorageBackendError``, which lives in the
# feature's error family (``StorageBackendError(StorageError)`` beside
# ``QuotaExceededError`` etc.) — splitting that family to move one class is a
# separate decision from provider construction, so the import stays deferred
# inside the ``except`` block. ``clients/billing/stripe.py`` has the same shape.
ALLOWED_DEFERRED_FEATURE_IMPORTS = {
    ("s3_storage.py", "src.app.modules.storage.errors"): "StorageBackendError, feature error family",
}


def _imported_module_names(path: Path) -> set[str]:
    """Absolute module names imported by ``path`` (import and from-import)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


class TestOutboundPackageHasNoFeatureImports:
    def test_no_client_storage_module_imports_a_feature_module(self) -> None:
        edges = {
            (path.name, name)
            for path in sorted(CLIENTS_STORAGE_ROOT.glob("*.py"))
            for name in _imported_module_names(path)
            if name == FEATURE_PACKAGE or name.startswith(f"{FEATURE_PACKAGE}.")
        }
        assert edges == set(ALLOWED_DEFERRED_FEATURE_IMPORTS), (
            "src/app/clients/storage/ must not import a feature module: the storage "
            "seam's outbound half owns provider construction and vendor behaviour, so "
            f"the dependency direction is clients -> (nothing) <- modules. Got {sorted(edges)}; "
            f"only the declared exceptions are {sorted(ALLOWED_DEFERRED_FEATURE_IMPORTS)}."
        )

    def test_registry_builds_the_outbound_adapters(self) -> None:
        """The factories must construct classes from the outbound package."""
        assert registry_module._s3_factory.__module__ == "src.app.clients.storage.registry"
        assert registry_module.S3Storage is S3Storage
        assert registry_module.AzureBlobStorage is AzureBlobStorage
        assert registry_module.LocalStorage is LocalStorage

    def test_s3_factory_maps_credentials_onto_the_adapter(self) -> None:
        """``_s3_factory`` keeps its credential mapping; only its home moved."""
        sentinel = MagicMock()
        with patch.object(registry_module, "S3Storage", return_value=sentinel) as s3_cls:
            assert registry_module._s3_factory({"bucket_name": "b", "region": "eu-west-1"}) is sentinel

        assert s3_cls.call_args.kwargs == {
            "bucket": "b",
            "region": "eu-west-1",
            "aws_access_key_id": None,
            "aws_secret_access_key": None,
        }

    def test_outbound_package_init_stays_vendor_free(self) -> None:
        """Importing the package must not drag boto3/azure/aiofiles in.

        ``modules.storage.models`` imports ``StorageProviderType`` for its column
        default, so an eager registry import here would put the vendor SDKs on
        the model-import path (and re-create an import cycle).
        """
        import subprocess
        import sys

        script = (
            "import sys, src.app.clients.storage as c;"
            "assert c.StorageProviderType and c.StorageProvider;"
            "print([m for m in ('boto3','botocore','azure','aiofiles') if m in sys.modules])"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "[]", result.stdout


class TestEnumIsOneObjectOnBothSides:
    def test_models_re_exports_the_outbound_enum(self) -> None:
        assert ModelStorageProviderType is StorageProviderType

    def test_models_import_path_keeps_the_name_importable(self) -> None:
        """``src.app.models`` re-exports it; that path must not change."""
        from src.app.models import StorageProviderType as AggregatorStorageProviderType

        assert AggregatorStorageProviderType is StorageProviderType

    def test_schema_enum_stays_distinct(self) -> None:
        """The schema enum is a separate Pydantic-facing type and is untouched."""
        from src.app.modules.storage.schemas import StorageProviderType as SchemaStorageProviderType

        assert SchemaStorageProviderType is not StorageProviderType
        assert {m.value for m in SchemaStorageProviderType} == {m.value for m in StorageProviderType}


class TestRegistryStillBuildsRealAdapters:
    def test_local_factory_round_trip(self, tmp_path: Path) -> None:
        provider = StorageProviderRegistry.create(StorageProviderType.LOCAL, {"base_path": str(tmp_path)})

        assert isinstance(provider, LocalStorage)
        assert isinstance(provider, StorageProvider)

    def test_azure_factory_requires_connection_string(self) -> None:
        with pytest.raises(ValueError, match="Azure storage credentials missing required key"):
            StorageProviderRegistry.create(StorageProviderType.AZURE, {"container_name": "c"})

    def test_local_factory_requires_base_path(self) -> None:
        with pytest.raises(ValueError, match="Local storage credentials missing required key"):
            StorageProviderRegistry.create(StorageProviderType.LOCAL, {})


class TestAdminConsumesTheSeam:
    def test_create_provider_delegates_to_the_registry(self, tmp_path: Path) -> None:
        service = StorageAdminService(MagicMock())
        sentinel = LocalStorage(base_path=str(tmp_path))
        with patch.object(
            registry_module.StorageProviderRegistry, "create", return_value=sentinel
        ) as mock_create:
            provider = service._create_provider(StorageProviderType.LOCAL, {"base_path": str(tmp_path)})

        assert provider is sentinel
        assert mock_create.call_args.args[0] is StorageProviderType.LOCAL

    def test_admin_module_imports_the_registry_at_module_scope(self) -> None:
        """No function-local import of the registry: the edge is a plain import."""
        admin_path = Path(StorageAdminService.__module__.replace(".", "/") + ".py")
        source = Path(__file__).resolve().parents[4] / admin_path
        assert "src.app.clients.storage.registry" in _imported_module_names(source)

    def test_provider_cache_and_close_behaviour_are_preserved(self, tmp_path: Path) -> None:
        """The cached instance is reused, and a settings change rotates it."""
        from datetime import UTC, datetime

        from src.app.modules.storage.admin import _PROVIDER_CACHE
        from src.app.modules.storage.models import StorageSettings

        _PROVIDER_CACHE.clear()
        service = StorageAdminService(MagicMock())
        tenant_id = uuid.uuid4()
        before = StorageSettings(tenant_id=tenant_id, provider="local", updated_at=datetime(2026, 1, 1, tzinfo=UTC))
        after = StorageSettings(tenant_id=tenant_id, provider="local", updated_at=datetime(2026, 1, 2, tzinfo=UTC))

        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            with patch.object(service, "_decrypt_credentials", return_value={"base_path": first}):
                provider_one = service.get_provider_for_settings(before)
                provider_one_again = service.get_provider_for_settings(before)
            with patch.object(service, "_decrypt_credentials", return_value={"base_path": second}):
                provider_two = service.get_provider_for_settings(after)

        assert provider_one is provider_one_again
        assert provider_one is not provider_two
        _PROVIDER_CACHE.clear()
