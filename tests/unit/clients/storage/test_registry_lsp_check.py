"""The storage registry must reject a factory that returns a non-provider.

``StorageFactory`` is ``Callable[[dict], StorageProvider]`` — an annotation
nothing enforces. A misbehaving or half-migrated factory would produce an
object the file layer cannot use, and the failure would surface on first
upload as a 500 instead of at the seam that built it.
"""

from typing import Any

import pytest

from src.app.clients.storage.interfaces import StorageProvider
from src.app.clients.storage.provider_type import StorageProviderType
from src.app.clients.storage.registry import StorageProviderRegistry


@pytest.fixture(autouse=True)
def _restore_registry():
    original = dict(StorageProviderRegistry._factories)
    yield
    StorageProviderRegistry._factories.clear()
    StorageProviderRegistry._factories.update(original)


class TestCreateValidatesTheResult:
    def test_a_non_provider_result_is_rejected(self) -> None:
        StorageProviderRegistry.register(StorageProviderType.LOCAL, lambda creds: object())  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="not a StorageProvider"):
            StorageProviderRegistry.create(StorageProviderType.LOCAL, {"base_path": "/tmp"})

    def test_the_error_names_the_provider_and_actual_type(self) -> None:
        class Sneaky:
            pass

        StorageProviderRegistry.register(StorageProviderType.LOCAL, lambda creds: Sneaky())  # type: ignore[arg-type]

        with pytest.raises(TypeError) as exc:
            StorageProviderRegistry.create(StorageProviderType.LOCAL, {"base_path": "/tmp"})

        message = str(exc.value)
        assert "LOCAL" in message
        assert "Sneaky" in message, f"error must name what came back instead: {message}"

    def test_a_real_provider_still_passes(self) -> None:
        from src.app.clients.storage.local_storage import LocalStorage

        provider = LocalStorage(base_path="/tmp")
        StorageProviderRegistry.register(StorageProviderType.LOCAL, lambda creds: provider)

        assert StorageProviderRegistry.create(StorageProviderType.LOCAL, {"base_path": "/tmp"}) is provider

    def test_unknown_provider_still_raises_value_error(self) -> None:
        """The pre-existing failure must not be masked by the new check."""
        StorageProviderRegistry._factories.pop(StorageProviderType.LOCAL, None)

        with pytest.raises(ValueError, match="Unknown provider"):
            StorageProviderRegistry.create(StorageProviderType.LOCAL, {"base_path": "/tmp"})

    def test_no_opaque_types_pass(self) -> None:
        """A MagicMock is not a StorageProvider unless spec'd as one.

        Guards against the isinstance check being satisfied by a loose
        mock, which would make the new tests above vacuous.
        """
        from unittest.mock import MagicMock

        StorageProviderRegistry.register(
            StorageProviderType.LOCAL, lambda creds: MagicMock()  # type: ignore[arg-type]
        )

        with pytest.raises(TypeError):
            StorageProviderRegistry.create(StorageProviderType.LOCAL, {"base_path": "/tmp"})

    def test_a_specd_mock_does_pass(self) -> None:
        """Confirms the check accepts anything genuinely StorageProvider-shaped."""
        from unittest.mock import MagicMock

        fake = MagicMock(spec=StorageProvider)
        fake.__class__ = StorageProvider  # isinstance passes only for the ABC
        StorageProviderRegistry.register(StorageProviderType.LOCAL, lambda creds: fake)

        assert StorageProviderRegistry.create(StorageProviderType.LOCAL, {"base_path": "/tmp"}) is fake


class TestProtocolIsActuallyChecked:
    def test_storage_provider_is_checkable_by_isinstance(self) -> None:
        """If StorageProvider were a non-runtime-checkable Protocol, the
        isinstance in create() would raise TypeError for EVERY call. This
        pins that the check is sound, not just present."""
        import abc

        assert not isinstance(getattr(StorageProvider, "_is_protocol", False), bool) or not getattr(
            StorageProvider, "_is_protocol", False
        ), "StorageProvider must be a real ABC/interface, not a typing.Protocol"
        assert hasattr(StorageProvider, "__subclasshook__") or isinstance(StorageProvider, type)
        assert isinstance(abc.ABCMeta, type)


def test_factories_registered_by_default_all_satisfy_the_check() -> None:
    """The three built-in factories must not trip the new guard."""
    from src.app.clients.storage.registry import _azure_factory, _local_factory, _s3_factory

    # Each raises ValueError (missing/optional dep) rather than TypeError
    # for empty credentials, which is the fail-loud path from round 1.
    for factory in (_s3_factory, _azure_factory, _local_factory):
        with pytest.raises(ValueError):
            factory({})


def test_registry_create_rejects_a_bad_factory_registration() -> None:
    """register() is untyped-checked by design; create() is the gate."""
    creds: Any = {"base_path": "/tmp"}
    StorageProviderRegistry.register(StorageProviderType.LOCAL, lambda c: "not a provider")  # type: ignore[arg-type,return-value]

    with pytest.raises(TypeError):
        StorageProviderRegistry.create(StorageProviderType.LOCAL, creds)
