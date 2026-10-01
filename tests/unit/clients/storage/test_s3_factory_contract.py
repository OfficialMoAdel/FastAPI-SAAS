"""The S3 factory must fail loud on missing required config, like its siblings.

``_azure_factory`` and ``_local_factory`` both raise ``ValueError`` when a
required credential key is absent. ``_s3_factory`` used to substitute
``"default-bucket"`` and ``"us-east-1"``, so a NULL ``credentials_encrypted``
row (which ``StorageAdminService._decrypt_credentials`` turns into ``{}``)
produced a live provider aimed at a bucket nobody configured.

It also has to fail at *construction*, not at first upload, when the
``boto3`` dependency is absent — that import is function-local precisely so
the module imports without it. boto3 is declared (dxub.9, DECIDED: DECLARE),
so the guard's live branch is the "partial build" one; both are forced below.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.app.clients.storage.provider_type import StorageProviderType
from src.app.clients.storage.registry import (
    StorageProviderRegistry,
    _s3_factory,
)
from src.app.clients.storage.s3_storage import S3Storage
from tests.missing_package import without_boto3


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
    """Both branches of the guard, neither skipped.

    boto3 is a DECLARED dependency as of dxub.9 (DECIDED: DECLARE), so these
    tests used to be dead in exactly the environment they were written for:
    each one skipped whenever boto3 was importable. The declared-dependency
    and clean-install assertions now live in
    tests/contract/test_boto3_declared.py; what remains here is the guard
    itself, forced onto both branches.
    """

    def test_absent_boto3_raises_valueerror_not_modulenotfound(self) -> None:
        """A missing dependency must be an actionable ValueError.

        Without this guard the operator discovers the gap on first upload as
        a bare ModuleNotFoundError 500.
        """
        with without_boto3():
            with pytest.raises(ValueError, match="boto3"):
                _s3_factory({"bucket_name": "b", "region": "eu-west-1"})

    def test_registry_create_surfaces_the_same_error(self) -> None:
        with without_boto3():
            with pytest.raises(ValueError, match="boto3"):
                StorageProviderRegistry.create(StorageProviderType.S3, {"bucket_name": "b", "region": "eu-west-1"})

    def test_present_boto3_builds_the_provider(self) -> None:
        """Counter-test: the guard does not fire when the dependency is there."""
        with patch("boto3.client", MagicMock(return_value=MagicMock())):
            provider = _s3_factory({"bucket_name": "b", "region": "eu-west-1"})

        assert isinstance(provider, S3Storage)
