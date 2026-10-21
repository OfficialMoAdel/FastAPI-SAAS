"""Contracts the webhook delivery path must keep.

Three of them are correctness, not cosmetics:

* **E3** — an endpoint update applies only the endpoint's own columns, and
  a null is only accepted where the column accepts one. Before this, every
  supplied key was `setattr`-ed: a JSON null on `url` reached a NOT NULL
  column (a 500 on a request the caller thought was fine), a null on
  `secret` silently destroyed the endpoint's signing material, and any field
  the update schema did not declare would have been persisted verbatim.
* **E6** — the recovery sweep releases its claim BEFORE it enqueues. It
  claims the DELIVERY lock on purpose (that claim is the atomic "is a
  delivery in flight?" check), but holding it across the enqueue made the
  enqueued job find the lock held, skip the delivery, and leave the row to
  be re-harvested ten minutes later.
* **E8** — a delivery that already reached a terminal state is not POSTed
  again. The lock serialises concurrent attempts; it does not stop a later
  one.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules._base import ValidationError
from src.app.modules.webhooks.delivery import deliver, recover_stuck_deliveries
from src.app.modules.webhooks.models import WebhookDeliveryStatus
from src.app.modules.webhooks.service import WebhookService


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.get = AsyncMock()
    db.execute = AsyncMock()
    return db


@pytest.fixture
def service(mock_db):
    return WebhookService(mock_db)


@pytest.fixture
def endpoint():
    from src.app.modules.webhooks.models import WebhookEndpoint

    ep = WebhookEndpoint(
        tenant_id=uuid4(),
        url="https://example.com/webhook",
        event_type="user.created",
        secret="encrypted_secret",
        is_active=True,
    )
    ep.id = uuid4()
    return ep


class TestUpdateEndpointIsBounded:
    """E3: only the endpoint's own columns, and no null where none is allowed."""

    async def test_a_null_url_is_refused(self, service, endpoint) -> None:
        """A null on a NOT NULL column used to be a 500 from the database."""
        with pytest.raises(ValidationError):
            await service.update_endpoint(endpoint, {"url": None})

    async def test_a_null_secret_is_refused(self, service, endpoint) -> None:
        """A null secret silently broke every later delivery to the endpoint."""
        with pytest.raises(ValidationError):
            await service.update_endpoint(endpoint, {"secret": None})

    async def test_a_null_signing_secret_is_allowed(self, service, endpoint) -> None:
        """The column is nullable, so clearing it is a legitimate request."""
        await service.update_endpoint(endpoint, {"signing_secret": None})
        assert endpoint.signing_secret is None

    async def test_an_undeclared_field_is_never_persisted(self, service, endpoint) -> None:
        """The tenant re-parenting path: a field the schema does not declare."""
        original_tenant = endpoint.tenant_id
        with patch("src.app.modules.webhooks.utils.validate_url_ssrf", AsyncMock()):
            await service.update_endpoint(
                endpoint, {"tenant_id": uuid4(), "url": "https://example.com/other"}
            )

        assert endpoint.tenant_id == original_tenant, "an undeclared field must never be persisted"
        assert endpoint.url == "https://example.com/other"

    async def test_a_normal_update_is_applied(self, service, endpoint) -> None:
        original = endpoint.url
        with patch("src.app.modules.webhooks.utils.validate_url_ssrf", AsyncMock()):
            await service.update_endpoint(endpoint, {"url": "https://example.com/new"})

        assert endpoint.url == "https://example.com/new"
        assert endpoint.url != original
        service.db.commit.assert_awaited()


class TestRecoveryReleasesBeforeEnqueue:
    """E6: the enqueued job must be able to take the delivery lock itself."""

    async def test_the_claim_is_released_before_the_enqueue(self) -> None:
        db = AsyncMock()
        delivery = MagicMock()
        delivery.id = 7
        delivery.tenant_id = "t1"

        result = MagicMock()
        result.scalars.return_value.all.return_value = [delivery]
        db.execute.return_value = result

        redis = AsyncMock()
        ctx = {"redis": redis}

        cache = AsyncMock()
        cache.client = MagicMock()
        cache.set_nx.return_value = True
        cache.release_lock_if_owned.return_value = True

        order: list[str] = []

        async def _release(key: str, token: str) -> bool:
            order.append("release")
            return True

        async def _enqueue(*args, **kwargs) -> MagicMock:
            order.append("enqueue")
            return MagicMock()

        cache.release_lock_if_owned.side_effect = _release
        redis.enqueue_job.side_effect = _enqueue

        with (
            patch("src.app.modules.webhooks.delivery.get_db_context") as mock_ctx,
            patch("src.app.modules.webhooks.delivery.bypass_rls") as mock_bypass,
            patch("src.app.core.cache.client.cache_client", cache),
        ):
            cm = AsyncMock()
            cm.__aenter__.return_value = db
            cm.__aexit__.return_value = False
            mock_ctx.return_value = cm
            mock_bypass.return_value.__aenter__.return_value = db

            recovered = await recover_stuck_deliveries(ctx)

        assert recovered == 1
        # The `finally` releases again as a safety net, so the assertion is on
        # the ORDER: the first release must come before the enqueue.
        assert order[0] == "release" and order.index("enqueue") > 0, (
            "the claim must be released BEFORE the enqueue — otherwise the enqueued job "
            "finds the lock held and skips the delivery"
        )


class TestTerminalStateIsNotRedelivered:
    """E8: a delivered row is not POSTed again."""

    async def test_a_delivered_row_is_not_posted(self) -> None:
        db = AsyncMock()
        did = uuid4()

        endpoint = MagicMock()
        endpoint.id = uuid4()
        endpoint.url = "https://example.com/hook"
        endpoint.secret = "enc"
        endpoint.signing_secret = None
        endpoint.old_signing_secret = None
        endpoint.tenant_id = uuid4()

        delivery = MagicMock()
        delivery.id = did
        delivery.tenant_id = endpoint.tenant_id
        delivery.webhook_endpoint = endpoint
        delivery.payload = {"a": 1}
        delivery.attempt_count = 1
        delivery.event_type = "test.event"
        delivery.status = WebhookDeliveryStatus.DELIVERED
        delivery.next_retry_at = None

        result = MagicMock()
        result.unique.return_value.scalar_one_or_none.return_value = delivery
        db.execute.return_value = result
        db.get.return_value = delivery

        http_client = AsyncMock()
        cache = AsyncMock()
        cache.client = MagicMock()
        cache.set_nx.return_value = True
        cache.release_lock_if_owned.return_value = True

        with (
            patch("src.app.core.cache.client.cache_client", cache),
            patch(
                "src.app.modules.webhooks.delivery.get_webhook_http_client",
                AsyncMock(return_value=http_client),
            ),
        ):
            out = await deliver(db, did)

        assert out.status == WebhookDeliveryStatus.DELIVERED
        http_client.post.assert_not_awaited()


class TestModelMatchesTheSchema:
    """E1/E2/E7: the ORM and the creating layer describe the same table."""

    def test_the_timestamps_are_timezone_aware(self) -> None:
        """A naive `created_at` made the recovery cutoff depend on the
        database server's session timezone."""
        from src.app.modules.webhooks.models import WebhookDelivery, WebhookEndpoint

        for model in (WebhookEndpoint, WebhookDelivery):
            for name in ("created_at", "updated_at"):
                column = model.__table__.columns[name]
                assert column.type.timezone is True, f"{model.__name__}.{name} must be timezone-aware"

    def test_the_secret_cannot_be_null(self) -> None:
        """The column is NOT NULL, so a null secret cannot be stored."""
        from src.app.modules.webhooks.models import WebhookEndpoint

        assert WebhookEndpoint.__table__.columns["secret"].nullable is False

    def test_the_retry_index_covers_the_rows_the_sweep_needs(self) -> None:
        """E7: the predicate must not exclude the NULL-next_retry_at rows."""
        from src.app.modules.webhooks.models import WebhookDelivery

        index = next(i for i in WebhookDelivery.__table__.indexes if i.name == "idx_webhook_delivery_retry")
        predicate = str(index.dialect_options["postgresql"]["where"])
        assert "next_retry_at IS NOT NULL" not in predicate, (
            "a delivery whose INITIAL enqueue was lost has next_retry_at NULL, and the "
            "recovery sweep selects on coalesce(next_retry_at, created_at)"
        )
        assert "status = 'pending'" in predicate

    def test_the_creating_layer_declares_the_same_secret_nullability(self) -> None:
        """The migration is the layer that creates the table."""
        from pathlib import Path

        migration = (
            Path(__file__).resolve().parents[4]
            / "src"
            / "migrations"
            / "versions"
            / "07_observability.py"
        )
        source = migration.read_text(encoding="utf-8")
        assert 'sa.Column("secret", sa.String(length=255), nullable=False)' in source
