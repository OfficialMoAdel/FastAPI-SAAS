"""Bead io15.11 / P2-19 — the storage interface/close question, adjudicated.

The plan said the cache was never drained on shutdown and that ``close()``
was undeclared on the ``StorageProvider`` ABC. Reading the source settles
both: ``close()`` is a concrete default on the ABC
(``interfaces.py``), ``close_cached_providers()`` genuinely drains
``_PROVIDER_CACHE``, and the lifespan tail calls it. Those are pinned here
so the claims cannot drift back into "PARTIAL".

What was NOT already fixed, and is pinned here as a regression: provider
CONSTRUCTION sat outside the ``try`` in ``_validate_credentials``, so the
registry's actionable optional-dependency error ("requires boto3, install
with ...") escaped unlogged and the router rewrote it to "Invalid storage
settings payload." — an operator could not tell "install the dependency"
from "change backend". The class of bug these tests protect is a provider
that is missing a method or cannot be built failing at *registration* /
*construction*, never silently degrading later at shutdown.

Composition note: the e2e test enters the real ``lifespan_factory`` and
lets the real ``compose_phases`` run the teardown tail. The cache is
seeded through the real ``get_provider_for_settings`` (not by poking the
TTLCache directly), so the assertion is about the shipped wiring.
"""

from __future__ import annotations

import inspect
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from structlog.testing import capture_logs

from src.app.clients.storage.azure_storage import AzureBlobStorage
from src.app.clients.storage.interfaces import StorageProvider, StorageResult
from src.app.clients.storage.local_storage import LocalStorage
from src.app.clients.storage.provider_type import StorageProviderType
from src.app.clients.storage.registry import StorageProviderRegistry
from src.app.clients.storage.s3_storage import S3Storage
from src.app.modules.storage import admin as storage_admin
from src.app.modules.storage.admin import StorageAdminService, close_cached_providers
from src.app.modules.storage.errors import InvalidCredentialsError
from src.app.modules.storage.models import StorageSettings
from src.app.modules.storage.schemas import S3Credentials as S3CredentialsSchema
from src.app.modules.storage.schemas import StorageProviderType as SchemaProviderType
from src.app.modules.storage.schemas import StorageSettingsCreate
from tests.missing_package import without_boto3

S3_CREDS = {"bucket_name": "my-bucket", "region": "eu-west-1"}
AZURE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=a2V5;EndpointSuffix=core.windows.net"
)


@pytest.fixture(autouse=True)
def _isolate_registry_and_cache():
    """The factory map and the cache are both process-global by design."""
    storage_admin._PROVIDER_CACHE.clear()
    StorageProviderRegistry.reset()
    yield
    storage_admin._PROVIDER_CACHE.clear()
    StorageProviderRegistry.reset()


# ---------------------------------------------------------------------------
# Conformance: registration, not first-upload, is where a bad provider dies.
# ---------------------------------------------------------------------------


class _IncompleteProvider(StorageProvider):
    """Missing ``delete``/``exists``/``get_url`` — cannot be instantiated."""

    async def upload(self, file, path, mime_type) -> StorageResult:  # noqa: ANN001, ANN201
        raise NotImplementedError

    async def download(self, path: str):  # noqa: ANN201
        raise NotImplementedError
        yield b""  # pragma: no cover - makes this an async generator


class _NoCloseProvider(StorageProvider):
    """Conforms to every abstract method, and inherits the default ``close``."""

    async def upload(self, file, path, mime_type) -> StorageResult:  # noqa: ANN001, ANN201
        return StorageResult(success=True, path=path)

    async def download(self, path: str):  # noqa: ANN201
        yield b""

    async def delete(self, path: str) -> StorageResult:  # noqa: ANN201
        return StorageResult(success=True, path=path)

    async def exists(self, path: str) -> bool:  # noqa: ANN201
        return True

    async def get_url(self, path: str, expires_in: int = 3600) -> str:  # noqa: ANN201
        return f"https://example.invalid/{path}"


class TestRegistryEnforcesTheFullAbc:
    def test_a_non_conforming_provider_cannot_be_constructed(self) -> None:
        """The ABC itself refuses the gap — this is the first line of defence."""
        with pytest.raises(TypeError, match="abstract"):
            _IncompleteProvider()  # type: ignore[abstract]

    def test_the_registry_rejects_a_non_conforming_provider(self) -> None:
        """And a misbehaving factory cannot smuggle one past ``create``.

        ``isinstance`` runs against the real ``StorageProvider`` ABC, not a
        narrower shape, so the failure lands at the seam instead of on the
        first upload as a 500.
        """
        StorageProviderRegistry.register(StorageProviderType.LOCAL, lambda credentials: object())

        with pytest.raises(TypeError) as exc:
            StorageProviderRegistry.create(StorageProviderType.LOCAL, {})

        assert "not a StorageProvider" in str(exc.value)
        assert "object" in str(exc.value), "the message must name what came back"

    def test_the_registry_rejects_the_unimplemented_class_too(self) -> None:
        """Returning the CLASS, not an instance, is the same defect.

        A class object is not an instance of its own ABC, so the guard
        catches this too rather than letting a bare class reach the file
        layer and blow up on first attribute access.
        """
        StorageProviderRegistry.register(StorageProviderType.LOCAL, lambda credentials: _IncompleteProvider)

        with pytest.raises(TypeError, match="not a StorageProvider"):
            StorageProviderRegistry.create(StorageProviderType.LOCAL, {})

    @pytest.mark.parametrize(
        ("provider_type", "credentials", "expected"),
        [
            (StorageProviderType.LOCAL, {"base_path": "C:/storage-probe"}, LocalStorage),
            (
                StorageProviderType.AZURE,
                {"connection_string": AZURE_CONNECTION_STRING, "container_name": "c"},
                AzureBlobStorage,
            ),
        ],
    )
    def test_real_providers_register(self, provider_type, credentials, expected) -> None:
        provider = StorageProviderRegistry.create(provider_type, credentials)

        assert isinstance(provider, expected)
        assert isinstance(provider, StorageProvider)

    def test_the_s3_provider_registers_when_boto3_is_available(self) -> None:
        """S3 is guarded on the optional dep; construct with a stub client.

        ``boto3`` is deliberately undeclared (imported function-locally), so
        this stubs the client rather than the module and still exercises the
        real ``S3Storage`` class against the ABC.
        """
        StorageProviderRegistry.register(
            StorageProviderType.S3, lambda credentials: S3Storage(s3_client=MagicMock(), **_s3_kwargs(credentials))
        )
        provider = StorageProviderRegistry.create(StorageProviderType.S3, S3_CREDS)

        assert isinstance(provider, S3Storage)
        assert isinstance(provider, StorageProvider)

    @pytest.mark.asyncio
    async def test_a_conforming_provider_may_rely_on_the_default_close(self) -> None:
        """``close`` is concrete on the ABC, so omitting it is legal."""
        provider = _NoCloseProvider()

        assert provider.close.__func__ is StorageProvider.close
        assert await provider.close() is None


def _s3_kwargs(credentials: dict) -> dict:
    return {
        "bucket": credentials["bucket_name"],
        "region": credentials["region"],
        "aws_access_key_id": "ak",
        "aws_secret_access_key": "sk",
    }


# ---------------------------------------------------------------------------
# The fail-fast optional-dependency error, message and all.
# ---------------------------------------------------------------------------


class TestOptionalDependencyErrorIsActionable:
    """Both branches, neither skipped.

    boto3 became a DECLARED dependency in dxub.9 (DECIDED: DECLARE), so these
    two tests would now skip in every run — leaving the settings-time
    behaviour unpinned on exactly the machines that ship S3. ``without_boto3()``
    forces the absent state instead, so the guard is asserted regardless of
    what is installed; the manifest/lock assertions that pin the decision are
    in tests/contract/test_boto3_declared.py.
    """

    def test_the_message_says_how_to_fix_it(self) -> None:
        """Not merely "boto3 is missing" — the operator needs the remedy.

        The caller must be able to choose between installing the dependency
        and changing backend, so the message has to carry the install hint.
        """
        with without_boto3():
            with pytest.raises(ValueError) as exc:
                StorageProviderRegistry.create(StorageProviderType.S3, S3_CREDS)

        message = str(exc.value)
        assert "boto3" in message
        assert "install" in message.lower(), "the message must tell the operator what to do"
        assert "uv pip install boto3" in message

    @pytest.mark.asyncio
    async def test_the_construction_failure_is_logged_with_the_tenant_id(self) -> None:
        """A provider that cannot even be built is a validation failure.

        Construction lives inside the ``try`` in ``_validate_credentials``,
        so the tenant that asked for S3 is named in the log. The client must
        not see the remedy (public messages are fixed by design) — the log
        is the only place an operator can act on it.
        """
        tenant_id = uuid4()
        service = StorageAdminService(MagicMock())

        with without_boto3():
            with capture_logs() as logs:
                with pytest.raises(InvalidCredentialsError, match="boto3"):
                    await service._validate_credentials(StorageProviderType.S3, S3_CREDS, tenant_id)

        failures = [entry for entry in logs if entry["event"] == "Credential validation failed"]
        assert failures, f"an unbuildable provider must be logged; got {[e['event'] for e in logs]}"
        assert str(tenant_id) in str(failures[0]), "the log must name the tenant whose storage is misconfigured"
        assert "boto3" in str(failures[0]), "the log must carry the actionable error text"

    @pytest.mark.asyncio
    async def test_settings_time_surfaces_the_missing_dependency(self) -> None:
        """The gap must be found when the tenant CONFIGURES S3, not at first upload.

        ``create_settings`` validates credentials before writing the row, so
        the failure lands in the settings call the admin just made — where the
        remedy is actionable — rather than in an unrelated upload hours later.
        """
        service = StorageAdminService(MagicMock())
        data = StorageSettingsCreate(provider=SchemaProviderType.S3, s3_credentials=S3CredentialsSchema(**{
            "access_key_id": "ak",
            "secret_access_key": "sk",
            "bucket_name": "my-bucket",
            "region": "eu-west-1",
        }))

        with without_boto3():
            with pytest.raises(InvalidCredentialsError, match="boto3"):
                await service.create_settings(uuid4(), data)

        service.db.add.assert_not_called(), "a settings row must not persist for an unbuildable provider"

    @pytest.mark.asyncio
    async def test_settings_time_succeeds_when_boto3_is_present(self) -> None:
        """COUNTER-TEST at the same seam the negative case uses.

        The same call that fails above must succeed once boto3 is importable —
        otherwise the negative test would be proving only that S3 is
        permanently unavailable, not that the dependency is the deciding
        factor.
        """
        tenant_id = uuid4()
        db = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        service = StorageAdminService(db)
        data = StorageSettingsCreate(provider=SchemaProviderType.S3, s3_credentials=S3CredentialsSchema(**{
            "access_key_id": "ak",
            "secret_access_key": "sk",
            "bucket_name": "my-bucket",
            "region": "eu-west-1",
        }))

        real_client = MagicMock()
        real_client.exists.return_value = True
        with patch("boto3.client", return_value=real_client):
            settings = await service.create_settings(tenant_id, data)

        assert settings.tenant_id == tenant_id
        assert settings.provider == StorageProviderType.S3.value
        assert settings.bucket_name == "my-bucket"



def _settings_for(tenant_id: UUID, provider: str) -> StorageSettings:
    return StorageSettings(
        tenant_id=tenant_id,
        provider=provider,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Cache drain vs. closing one throwaway instance — the distinction the bead
# cares most about.
# ---------------------------------------------------------------------------


def _cache(provider, tenant_id: UUID | None = None) -> None:
    """Insert ``provider`` into the cache under a real (tenant, updated_at) key."""
    storage_admin._PROVIDER_CACHE[(tenant_id or uuid4(), datetime(2026, 1, 1, tzinfo=UTC))] = provider


class TestDrainEmptiesTheCache:
    @pytest.mark.asyncio
    async def test_every_cached_provider_is_closed_and_the_cache_is_emptied(self) -> None:
        """Drains the CACHE — not one throwaway instance.

        Each provider is spied individually, and the cache is asserted
        empty afterwards. A teardown that closed only the validation
        instance would pass a "was anything closed?" check and fail both
        of these.
        """
        providers = [AsyncMock(), AsyncMock(), AsyncMock()]
        for provider in providers:
            _cache(provider)

        assert len(storage_admin._PROVIDER_CACHE) == 3, "precondition: the cache is populated"

        closed = await close_cached_providers()

        assert closed == 3
        for provider in providers:
            provider.close.assert_awaited_once_with(), "every cached provider must be closed exactly once"
        assert len(storage_admin._PROVIDER_CACHE) == 0, "drained providers must not linger in the cache"

    @pytest.mark.asyncio
    async def test_a_provider_built_through_the_service_is_the_one_closed(self) -> None:
        """End-to-end within the service: build via the real cache path.

        Poking the TTLCache directly would prove the loop body runs; going
        through ``get_provider_for_settings`` proves the provider the file
        layer actually uses is the instance teardown reaches.
        """
        tenant_id = uuid4()
        service = StorageAdminService(MagicMock())
        provider = AsyncMock()
        provider.close = AsyncMock()
        settings = _settings_for(tenant_id, "local")

        with patch.object(service, "_create_provider", return_value=provider):
            built = service.get_provider_for_settings(settings)
        assert built is provider
        assert len(storage_admin._PROVIDER_CACHE) == 1

        await close_cached_providers()

        provider.close.assert_awaited_once_with()
        assert len(storage_admin._PROVIDER_CACHE) == 0


class TestCloseFailuresAreSurfaced:
    @pytest.mark.asyncio
    async def test_a_failing_close_does_not_strand_the_rest_and_is_logged(self) -> None:
        """A silent close failure leaks an HTTP client, so it is logged.

        One bad provider must not abort the drain — the others still own
        sockets — but its failure is recorded with the tenant id rather
        than discarded.
        """
        bad, good = AsyncMock(), AsyncMock()
        bad.close = AsyncMock(side_effect=RuntimeError("transport already gone"))
        bad_tenant = uuid4()
        _cache(bad, bad_tenant)
        _cache(good)

        with capture_logs() as logs:
            closed = await close_cached_providers()

        good.close.assert_awaited_once_with(), "a bad provider must not strand the healthy ones"
        assert closed == 1, "only the provider that closed successfully is counted"
        assert len(storage_admin._PROVIDER_CACHE) == 0, "the cache is emptied even when a close fails"

        failures = [entry for entry in logs if "Failed to close cached storage provider" in entry["event"]]
        assert len(failures) == 1, f"a close failure must be logged exactly once; got {failures}"
        assert str(bad_tenant) in str(failures[0]), "the log must name the tenant whose provider leaked"
        assert "transport already gone" in str(failures[0]), "the log must carry the underlying error"

    @pytest.mark.asyncio
    async def test_a_failing_validation_probe_still_closes_its_provider(self) -> None:
        """The throwaway validation instance is closed on BOTH outcomes.

        Success and failure both release the probe's client; a failure path
        that skipped ``close`` would leak a connection per rejected
        credential submission.
        """
        service = StorageAdminService(MagicMock())
        provider = AsyncMock()
        provider.exists = AsyncMock(side_effect=RuntimeError("endpoint refused"))

        with patch.object(service, "_create_provider", return_value=provider):
            with pytest.raises(InvalidCredentialsError):
                await service._validate_credentials(StorageProviderType.S3, S3_CREDS)

        provider.close.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_a_failed_construction_does_not_attempt_a_close(self) -> None:
        """Nothing was built, so there is nothing to close.

        Guards the ``storage is not None`` check: closing an unset name
        would be an ``UnboundLocalError`` masking the real error.
        """
        service = StorageAdminService(MagicMock())

        with patch.object(
            service, "_create_provider", side_effect=ValueError("S3 storage requires the optional 'boto3' dependency")
        ):
            with pytest.raises(InvalidCredentialsError, match="boto3"):
                await service._validate_credentials(StorageProviderType.S3, S3_CREDS)


# ---------------------------------------------------------------------------
# COUNTER-TEST: a provider that actually holds a transport.
# ---------------------------------------------------------------------------


class TestRealTransportsAreClosedOnShutdown:
    @pytest.mark.asyncio
    async def test_a_provider_holding_an_http_client_has_it_closed(self) -> None:
        """The behaviour the whole bead exists to protect.

        A stand-in provider owns a real ``httpx.AsyncClient``; teardown must
        close that client, and it must actually be closed afterwards —
        ``is_closed()`` is the observable, not "close was called".
        """
        import httpx

        closed = []

        class TransportProvider(StorageProvider):
            def __init__(self) -> None:
                self.client = httpx.AsyncClient()

            async def upload(self, file, path, mime_type) -> StorageResult:  # noqa: ANN001, ANN201
                return StorageResult(success=True, path=path)

            async def download(self, path: str):  # noqa: ANN201
                yield b""

            async def delete(self, path: str) -> StorageResult:  # noqa: ANN201
                return StorageResult(success=True, path=path)

            async def exists(self, path: str) -> bool:  # noqa: ANN201
                return True

            async def get_url(self, path: str, expires_in: int = 3600) -> str:  # noqa: ANN201
                return f"https://example.invalid/{path}"

            async def close(self) -> None:
                await self.client.aclose()
                closed.append(self.client)

        provider = TransportProvider()
        _cache(provider)
        assert provider.client.is_closed is False, "precondition: the client is live"

        await close_cached_providers()

        assert provider.client.is_closed is True, "the HTTP client must actually be closed, not just asked to be"
        assert closed == [provider.client], "the provider's own close() must run"

    @pytest.mark.asyncio
    async def test_the_real_s3_provider_closes_its_boto3_client(self) -> None:
        """S3 owns a client whose ``close`` is sync; the drain must await it.

        ``S3Storage.close`` hops to a thread for the blocking boto3 call,
        so a teardown that forgot to await would leave the pool open.
        """
        boto3_client = MagicMock()
        provider = S3Storage(s3_client=boto3_client, **_s3_kwargs(S3_CREDS))
        _cache(provider)

        await close_cached_providers()

        boto3_client.close.assert_called_once_with(), "the boto3 client's close must actually run"

    @pytest.mark.asyncio
    async def test_the_real_azure_provider_releases_its_service_client(self) -> None:
        """Azure nulls its client after closing, so a second close is a no-op."""
        provider = AzureBlobStorage(connection_string=AZURE_CONNECTION_STRING, container_name="c")
        service_client = AsyncMock()
        provider._service_client = service_client
        _cache(provider)

        await close_cached_providers()

        service_client.close.assert_awaited_once_with()
        assert provider._service_client is None, "the client reference must be dropped"

    @pytest.mark.asyncio
    async def test_the_real_local_provider_answers_close_in_the_drain(self) -> None:
        """LocalStorage holds nothing; the drain must not trip over it."""
        provider = LocalStorage(base_path="C:/storage-probe")
        _cache(provider)

        closed = await close_cached_providers()

        assert closed == 1, "a no-op close still counts as a drained provider"
        assert len(storage_admin._PROVIDER_CACHE) == 0


# ---------------------------------------------------------------------------
# e2e: a real lifespan shutdown drains the cache.
# ---------------------------------------------------------------------------


class TestLifespanShutdownDrainsTheCache:
    @pytest.mark.asyncio
    async def test_shutdown_completes_and_leaves_the_cache_empty(self) -> None:
        """Enter and exit the real lifespan, through the real teardown tail.

        Nothing about the tail is stubbed: the seeded providers must be
        closed by the same wiring production uses, and the shutdown must
        complete without raising. The other teardown steps are neutralised
        so this needs no database or Redis.
        """
        from src.app import composition
        from src.app.core.config import settings

        tenant_id = uuid4()
        service = StorageAdminService(MagicMock())
        provider = AsyncMock()
        settings_row = _settings_for(tenant_id, "local")
        with patch.object(service, "_create_provider", return_value=provider):
            service.get_provider_for_settings(settings_row)
        assert len(storage_admin._PROVIDER_CACHE) == 1, "precondition: the cache is populated"

        with _no_op_teardown(composition):
            lifespan = composition.lifespan_factory(settings, create_tables_on_start=False)
            app = type("FakeApp", (), {"state": type("S", (), {})()})()
            async with lifespan(app):
                # Still populated mid-flight: the drain is a SHUTDOWN action.
                assert len(storage_admin._PROVIDER_CACHE) == 1

        provider.close.assert_awaited_once_with(), "lifespan shutdown must close the cached provider"
        assert len(storage_admin._PROVIDER_CACHE) == 0, "lifespan shutdown must leave the cache empty"

    def test_the_drain_is_reachable_from_the_teardown_tail(self) -> None:
        """The tail entry is the one that imports and calls the drain.

        Asserting the function exists is not enough — an unreferenced
        helper would pass that. This checks the tail function itself calls
        it, which is the wiring that makes the e2e test above meaningful.
        """
        from src.app import composition

        source = inspect.getsource(composition._close_storage_providers)

        assert "close_cached_providers" in source, (
            "the teardown helper must call the drain; cached S3/Azure clients "
            "would otherwise never be closed"
        )


class _noop:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        pass

    async def __call__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None


def _no_op_teardown(composition_module):
    """Neutralise every teardown EXCEPT the provider drain.

    ``compose_phases`` runs the tail in order, so the storage drain is left
    real and the rest are stubbed to keep this test free of a database and
    Redis while still exercising the shipped shutdown path.
    """


    @contextmanager
    def _patched():
        with ExitStack() as stack:
            for name in (
                "_dispose_engine",
                "_close_sso_http_client",
                "_close_webhook_http_client",
                "_shutdown_ai_providers",
            ):
                stack.enter_context(patch.object(composition_module, name, _noop()))
            stack.enter_context(patch.object(composition_module.runtime_setup, "close_redis_pool", _noop()))
            yield

    return _patched()


@pytest.mark.asyncio
async def test_draining_an_empty_cache_is_a_no_op() -> None:
    assert await close_cached_providers() == 0

