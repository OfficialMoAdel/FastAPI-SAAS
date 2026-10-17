"""M-7 (FastAPI_saas-o0fg) found that POST /api/v1/api-keys could only ever mint a
key for the caller: APIKeyCreate carried no user_id and forbade extras, so a
tenant admin had to impersonate a user to hand them a credential. This bead
(FastAPI_saas-aepv) adds the optional target.

The shape is deliberately narrow, and each of these cases pins one edge of it:

- the common path (no target, or the caller naming themselves) must cost ZERO
  extra queries. A key request is the one thing that must not get slower
  because a field became optional.
- a target in ANOTHER tenant, or a soft-deleted one, must be indistinguishable
  from a target that does not exist. Anything else turns the endpoint into an
  oracle for which user ids exist.
- an inactive target is a different answer: the row is visible to this tenant, so
  saying so leaks nothing new, and a 403 is more actionable than a 404.
- the audit trail must name the target. A credential minted for someone else is
  a different event from one minted for the caller, and an audit row that cannot
  tell them apart cannot answer "who has whose keys".
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.api_keys.schemas import APIKeyCreate, APIKeyResponse
from src.app.modules.api_keys.service import APIKeyService
from src.app.shared.exceptions import ForbiddenError, ModelNotFoundError

CALLER_ID = 7
TARGET_ID = 4242
TENANT_ID = uuid4()

KEY_PREFIX = "abcd1234"
RAW_KEY = f"sk_{KEY_PREFIX}_body"


def _user_result(row: Any) -> MagicMock:
    """A result shaped like the one `select(User.id, User.is_active)` returns.

    `scalar_one_or_none` is set too because the duplicate-name pre-check reads
    its own result that way; leaving it an unset MagicMock attribute makes it
    truthy and every create collides on "onboarding" for reasons that have
    nothing to do with what is under test.
    """
    result = MagicMock()
    result.first.return_value = row
    result.scalar_one_or_none.return_value = None
    return result


def _service(user_rows: list[Any]) -> tuple[APIKeyService, MagicMock]:
    """A service over a mocked session; every user lookup yields `user_rows`."""
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_user_result(row) for row in user_rows] or [_user_result(None)])
    db.add = MagicMock()
    db.commit = AsyncMock()
    # refresh is what gives the record its server-side identity in production;
    # without it APIKeyResponse fails on id=None, which is a fixture artefact
    # masquerading as a defect.
    db.refresh = AsyncMock(side_effect=_hydrate)
    return APIKeyService(db), db


async def _hydrate(record: Any) -> None:
    record.id = 99
    record.created_at = datetime(2026, 1, 1, tzinfo=UTC)


def _body(**overrides: Any) -> APIKeyCreate:
    return APIKeyCreate(name="onboarding", **overrides)


async def _create(service: APIKeyService, body: APIKeyCreate) -> Any:
    with (
        patch(
            "src.app.modules.api_keys.service.execute_scoped",
            AsyncMock(return_value=_user_result(None)),
        ),
        patch.object(APIKeyService, "_generate_key", return_value=(RAW_KEY, KEY_PREFIX, "hashed")),
        patch("src.app.modules.api_keys.service.audit", AsyncMock()) as audit_spy,
    ):
        response = await service.create_api_key(api_key_data=body, user_id=CALLER_ID, tenant_id=TENANT_ID)
        return response, audit_spy


class TestTheCommonPathIsUnchanged:
    @pytest.mark.asyncio
    async def test_no_target_mints_for_the_caller(self) -> None:
        service, db = _service([])
        response, _ = await _create(service, _body())

        added = db.add.call_args[0][0]
        assert added.user_id == CALLER_ID
        assert response.key == RAW_KEY
        # No user lookup: the pre-fix path issued exactly one execute_scoped and
        # nothing else, so any db.execute here is a regression in the hot path.
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_naming_yourself_is_not_a_lookup(self) -> None:
        service, db = _service([])
        await _create(service, _body(user_id=CALLER_ID))

        assert db.add.call_args[0][0].user_id == CALLER_ID
        db.execute.assert_not_awaited()


class TestIssuingForSomebodyElse:
    @pytest.mark.asyncio
    async def test_the_key_and_the_audit_both_name_the_target(self) -> None:
        service, db = _service([(TARGET_ID, True)])
        _response, audit_spy = await _create(service, _body(user_id=TARGET_ID))

        assert db.add.call_args[0][0].user_id == TARGET_ID
        assert audit_spy.await_args.kwargs["user_id"] == TARGET_ID
        assert audit_spy.await_args.kwargs["details"]["issued_for_user_id"] == TARGET_ID

    @pytest.mark.asyncio
    async def test_an_unknown_or_cross_tenant_target_is_not_found(self) -> None:
        """No row means no answer, whatever the reason the row is missing."""
        service, db = _service([None])

        with pytest.raises(ModelNotFoundError):
            await _create(service, _body(user_id=TARGET_ID))

        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_soft_deleted_or_missing_row_never_leaks_through_the_query(self) -> None:
        """The filter, not the check: no user_id + no tenant + no is_deleted."""
        service, db = _service([None])
        with pytest.raises(ModelNotFoundError):
            await _create(service, _body(user_id=TARGET_ID))

        compiled = str(db.execute.await_args_list[0].args[0])
        assert "users.id" in compiled
        assert "users.tenant_id" in compiled
        assert "users.is_deleted" in compiled

    @pytest.mark.asyncio
    async def test_an_inactive_target_is_refused_with_a_reason(self) -> None:
        service, db = _service([(TARGET_ID, False)])

        with pytest.raises(ForbiddenError):
            await _create(service, _body(user_id=TARGET_ID))

        db.add.assert_not_called()


class TestTheTargetIsOptionalInTheSchema:
    def test_the_field_exists_and_defaults_to_absent(self) -> None:
        assert APIKeyCreate(name="k").user_id is None

    def test_a_named_target_is_carried_on_the_create_schema_only(self) -> None:
        """The target belongs to the request, not to the credential that comes
        back: a response that echoed it would restate what the caller sent."""
        assert "user_id" in APIKeyCreate.model_fields
        assert "user_id" not in APIKeyResponse.model_fields


def test_expires_at_still_flows_through_unchanged() -> None:
    moment = datetime(2030, 1, 1, tzinfo=UTC)
    assert APIKeyCreate(name="k", expires_at=moment).expires_at == moment


def test_the_schema_still_forbids_unknown_fields() -> None:
    """extra='forbid' is what made this bug possible; do not quietly relax it."""
    with pytest.raises(ValueError, match="extra"):
        APIKeyCreate(name="k", owner_id=1)  # type: ignore[call-arg]


def test_the_service_still_takes_the_caller_on_the_user_id_parameter() -> None:
    """The route calls create_api_key(user_id=ctx.user_id, tenant_id=...). The
    bead adds a TARGET; it must not repurpose the existing parameter's meaning,
    because every other caller in the tree passes the caller there."""
    assert list(inspect.signature(APIKeyService.create_api_key).parameters) == [
        "self",
        "api_key_data",
        "user_id",
        "tenant_id",
    ]
