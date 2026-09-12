"""P1-2: StripeGateway must never use another tenant's secret key.

Without tenant context the _allow_unscoped escape hatch returns the
stripe_settings table's first row regardless of tenant. The gateway must
verify record ownership after the get and fail closed to the global key.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.clients.billing.stripe import StripeGateway

TENANT_A = uuid4()
TENANT_B = uuid4()


def _gateway(db: MagicMock | None = None) -> StripeGateway:
    return StripeGateway(db=db if db is not None else MagicMock())


@pytest.mark.asyncio
async def test_matching_tenant_uses_record_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Record owned by the requesting tenant is decrypted and returned."""
    from src.app.core.config import settings

    monkeypatch.setattr(settings.billing, "STRIPE_SECRET_KEY", None)
    record = {"tenant_id": TENANT_A, "encrypted_secret_key": "enc-blob"}
    with (
        patch("src.app.modules.billing.crud.crud_stripe_settings.get", new_callable=AsyncMock) as mock_get,
        patch("src.app.core.security.encryption.CredentialEncryption") as mock_enc,
    ):
        mock_get.return_value = record
        mock_enc.return_value.decrypt_column.return_value = "sk_test_tenant_a"
        assert await _gateway().get_api_key(TENANT_A) == "sk_test_tenant_a"
        mock_enc.return_value.decrypt_column.assert_called_once_with("enc-blob")


@pytest.mark.asyncio
async def test_mismatched_tenant_never_uses_foreign_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """First-row-bleed: record belongs to another tenant -> global fallback, no decrypt."""
    from src.app.core.config import settings

    monkeypatch.setattr(settings.billing, "STRIPE_SECRET_KEY", None)
    record = {"tenant_id": TENANT_B, "encrypted_secret_key": "enc-blob"}
    with (
        patch("src.app.modules.billing.crud.crud_stripe_settings.get", new_callable=AsyncMock) as mock_get,
        patch("src.app.core.security.encryption.CredentialEncryption") as mock_enc,
    ):
        mock_get.return_value = record
        assert await _gateway().get_api_key(TENANT_A) is None
        mock_enc.return_value.decrypt_column.assert_not_called()


@pytest.mark.asyncio
async def test_uuid_str_drift_still_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """UUID-vs-str drift between row value and caller arg must not false-positive."""
    from src.app.core.config import settings

    monkeypatch.setattr(settings.billing, "STRIPE_SECRET_KEY", None)
    record = {"tenant_id": str(TENANT_A), "encrypted_secret_key": "enc-blob"}
    with (
        patch("src.app.modules.billing.crud.crud_stripe_settings.get", new_callable=AsyncMock) as mock_get,
        patch("src.app.core.security.encryption.CredentialEncryption") as mock_enc,
    ):
        mock_get.return_value = record
        mock_enc.return_value.decrypt_column.return_value = "sk_test_tenant_a"
        assert await _gateway().get_api_key(TENANT_A) == "sk_test_tenant_a"


@pytest.mark.asyncio
async def test_missing_record_tenant_id_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unverifiable ownership -> fail closed, never decrypt."""
    from src.app.core.config import settings

    monkeypatch.setattr(settings.billing, "STRIPE_SECRET_KEY", None)
    record = {"encrypted_secret_key": "enc-blob"}
    with (
        patch("src.app.modules.billing.crud.crud_stripe_settings.get", new_callable=AsyncMock) as mock_get,
        patch("src.app.core.security.encryption.CredentialEncryption") as mock_enc,
    ):
        mock_get.return_value = record
        assert await _gateway().get_api_key(TENANT_A) is None
        mock_enc.return_value.decrypt_column.assert_not_called()


def test_record_belongs_to_tenant_matrix() -> None:
    assert StripeGateway._record_belongs_to_tenant({"tenant_id": TENANT_A}, TENANT_A) is True
    assert StripeGateway._record_belongs_to_tenant({"tenant_id": str(TENANT_A)}, TENANT_A) is True
    assert StripeGateway._record_belongs_to_tenant({"tenant_id": TENANT_B}, TENANT_A) is False
    assert StripeGateway._record_belongs_to_tenant({}, TENANT_A) is False
    assert StripeGateway._record_belongs_to_tenant({"tenant_id": TENANT_A}, None) is False
