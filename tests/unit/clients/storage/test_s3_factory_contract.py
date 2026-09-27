"""The S3 factory must fail loud on missing required config, like its siblings.

``_azure_factory`` and ``_local_factory`` both raise ``ValueError`` when a
required credential key is absent. ``_s3_factory`` used to substitute
``"default-bucket"`` and ``"us-east-1"``, so a NULL ``credentials_encrypted``
row (which ``StorageAdminService._decrypt_credentials`` turns into ``{}``)
produced a live provider aimed at a bucket nobody configured.

It also has to fail at *construction*, not at first upload, when the
optional ``boto3`` dependency is absent — that import is function-local
precisely so the module imports without it.
"""

import pytest

from src.app.clients.storage.provider_type import StorageProviderType
from src.app.clients.storage.registry import (
    StorageProviderRegistry,
    _s3_factory,
)


class TestS3FactoryFailsLoud:
    def test_empty_credentials_raise(self) -> None:
        with pytest.raises(ValueError, match="missing required key"):
            _s3_factory({})

    def test_missing_region_raises(self) -> None:
        with pytest.raises(ValueError, match="missing required key"):
            _s3_factory({"bucket_name": "real-bucket"})

    def test_missing_bucket_raises(self) -> None:
        with pytest.raises(ValueError, match="missing required key"):
            _s3_factory({"region": "eu-west-1"})

    def test_no_silent_default_bucket(self) -> None:
        """Pre-fix this built a provider pointed at "default-bucket"."""
        with pytest.raises(ValueError) as exc:
            _s3_factory({"region": "eu-west-1"})
        assert "default-bucket" not in str(exc.value)

    def test_sibling_symmetry(self) -> None:
        """All three factories must reject an empty credential dict."""
        from src.app.clients.storage.registry import _azure_factory, _local_factory

        for factory in (_s3_factory, _azure_factory, _local_factory):
            with pytest.raises(ValueError):
                factory({})


class TestS3FactoryBoto3Guard:
    def test_absent_boto3_raises_valueerror_not_modulenotfound(self) -> None:
        """A missing optional dep must be an actionable ValueError.

        boto3 is intentionally NOT a declared dependency (see the round-1
        plan): it is imported function-locally so the module imports without
        it. Without this guard the operator discovers the gap on first
        upload as a bare ModuleNotFoundError 500.
        """
        try:
            import boto3  # noqa: F401
        except ModuleNotFoundError:
            with pytest.raises(ValueError, match="boto3"):
                _s3_factory({"bucket_name": "b", "region": "eu-west-1"})
        else:  # pragma: no cover - only when the optional dep is installed
            pytest.skip("boto3 is installed; the guard is inert")

    def test_registry_create_surfaces_the_same_error(self) -> None:
        try:
            import boto3  # noqa: F401
        except ModuleNotFoundError:
            with pytest.raises(ValueError, match="boto3"):
                StorageProviderRegistry.create(StorageProviderType.S3, {"bucket_name": "b", "region": "eu-west-1"})
        else:  # pragma: no cover
            pytest.skip("boto3 is installed; the guard is inert")
