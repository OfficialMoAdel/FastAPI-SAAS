"""S-4 oracle: the user-payload copy contract, pinned BEFORE the diff.

The S-4 change replaces ``copy.deepcopy`` at ``cache.py`` :127/:137/:194
with the two-level copy ``{**hit, 'tier': dict(tier) if isinstance(tier,
dict) else tier}`` (bead ``FastAPI_saas-qbim``). That replacement is only
isomorphic while the payload stays one level deep with at most one nested
dict, ``tier``. This file pins three things for BOTH live payload sources
(DB datetimes/UUIDs and Redis ISO strings), because the two sources DIFFER
and the Redis path is the one that actually runs on a cache hit:

1. SHAPE — the permanent guard ``assert_user_payload_shape`` accepts both
   sources and fails loudly on anything deeper.
2. DIFFERENTIAL — ``deepcopy(p) == <two-level copy>(p)`` on both sources.
3. ISOLATION — mutating the copy never touches the original, on both
   sources, for both copiers.

On the ``fast`` reference: ``fast`` does not exist in production yet — this
bead lands BEFORE the diff. ``_reference_two_level_copy`` below is the
EXACT expression the S-4 implementation bead names, written here as the
specification the diff must satisfy. When S-4 lands, the differential and
isolation tests swap this reference for the production ``fast`` import;
everything else stays. A test that imported a function that does not exist
yet could never be green against HEAD, and green-against-HEAD is what this
bead owes.

Payload fixtures mirror ``crud.py`` ``get_user_with_tier_eager`` (:80-108):
15 top-level keys, all scalar-ish, plus exactly one nested 4-scalar dict
``tier`` (or ``None``). ``test_fixtures_match_the_crud_key_set`` pins the
fixtures to the real source with an AST read, so fixture rot fails loudly
instead of silently testing a payload the code never builds.
"""

from __future__ import annotations

import ast
import copy
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.app.modules.users import cache as users_cache
from src.app.modules.users.cache import assert_user_payload_shape

REPO_ROOT = Path(__file__).resolve().parents[4]
CRUD_PATH = REPO_ROOT / "src" / "app" / "modules" / "users" / "crud.py"

_TIER_KEYS = ("id", "created_at", "updated_at", "name")


def _db_payload(*, with_tier: bool = True) -> dict[str, Any]:
    """The DB source: datetimes are datetimes, ids are UUIDs (crud.py:80-108)."""
    tenant_id = uuid4()
    payload: dict[str, Any] = {
        "id": 7,
        "created_at": datetime(2026, 1, 2, 3, 4, 5),
        "updated_at": datetime(2026, 3, 4, 5, 6, 7),
        "name": "User Userson",
        "username": "userson",
        "email": "user.userson@example.com",
        "profile_image_url": "https://www.profileimageurl.com",
        "tenant_id": tenant_id,
        "tier_id": 3,
        "hashed_password": "definitely-not-the-password",
        "is_superuser": False,
        "is_deleted": False,
        "is_active": True,
        "is_email_verified": True,
        "token_version": 4,
    }
    payload["tier"] = (
        {
            "id": 3,
            "created_at": datetime(2025, 12, 1, 0, 0, 0),
            "updated_at": None,
            "name": "pro",
        }
        if with_tier
        else None
    )
    return payload


def _redis_payload(*, with_tier: bool = True) -> dict[str, Any]:
    """The Redis source: everything a JSON round-trip leaves behind (ISO strings)."""
    payload = _db_payload(with_tier=with_tier)
    payload["created_at"] = payload["created_at"].isoformat()
    payload["updated_at"] = payload["updated_at"].isoformat()
    payload["tenant_id"] = str(payload["tenant_id"])
    if with_tier:
        assert isinstance(payload["tier"], dict)
        payload["tier"] = {
            "id": payload["tier"]["id"],
            "created_at": payload["tier"]["created_at"].isoformat(),
            "updated_at": payload["tier"]["updated_at"],
            "name": payload["tier"]["name"],
        }
    return payload


def _reference_two_level_copy(hit: dict[str, Any]) -> dict[str, Any]:
    """The S-4 expression, verbatim from bead ``FastAPI_saas-qbim``.

    ``{**hit, 'tier': dict(tier) if isinstance(tier, dict) else tier}``
    with ``tier`` bound to the payload's own ``tier`` entry. A non-dict
    tier (including ``None``) passes through unchanged rather than
    crashing — that pass-through is specified, not accidental.
    """
    tier = hit.get("tier")
    return {**hit, "tier": dict(tier) if isinstance(tier, dict) else tier}


def _crud_user_dict_keys() -> set[str]:
    """The key set of the ``user_dict`` literal built in crud.py:80-96."""
    tree = ast.parse(CRUD_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "user_dict" for t in node.targets
        ):
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "user_dict"
            and node.value is not None
        ):
            # crud.py declares it annotated: user_dict: dict[str, Any] = {...}
            value = node.value
        if isinstance(value, ast.Dict):
            keys = set()
            for k in value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
            if keys:
                return keys
    raise AssertionError("could not find the user_dict literal in crud.py")


class TestFixturesMatchTheRealPayload:
    def test_fixtures_match_the_crud_key_set(self) -> None:
        """The fixtures test the payload the code builds — all 15 keys plus tier."""
        # crud.py builds the 15 scalar keys as one literal (:80-96) and adds
        # "tier" afterwards (:98-106), so the AST sees 15 and the fixture
        # must hold exactly those 15 plus "tier" — nothing more, nothing less.
        assert _crud_user_dict_keys() == set(_db_payload()) - {"tier"}
        assert _crud_user_dict_keys() == set(_redis_payload()) - {"tier"}

    def test_fixtures_carry_a_real_nested_tier(self) -> None:
        """Without a real tier dict these tests would pass on flat payloads only."""
        for payload in (_db_payload(), _redis_payload()):
            tier = payload["tier"]
            assert isinstance(tier, dict), "fixture lost its nested tier"
            assert set(tier) == set(_TIER_KEYS)
            assert all(not isinstance(v, dict | list) for v in tier.values())

    def test_both_sources_differ_in_value_types(self) -> None:
        """The two sources must actually differ, or 'both sources' is one source twice."""
        db, redis = _db_payload(), _redis_payload()
        assert isinstance(db["created_at"], datetime)
        assert isinstance(redis["created_at"], str)
        assert isinstance(db["tenant_id"], UUID)
        assert isinstance(redis["tenant_id"], str)


class TestShapeAssertion:
    @pytest.mark.parametrize("payload", [_db_payload(), _redis_payload()], ids=["db", "redis"])
    def test_conforming_payloads_pass(self, payload: dict[str, Any]) -> None:
        assert_user_payload_shape(payload)

    @pytest.mark.parametrize("payload", [_db_payload(with_tier=False), _redis_payload(with_tier=False)])
    def test_tier_none_passes(self, payload: dict[str, Any]) -> None:
        assert payload["tier"] is None
        assert_user_payload_shape(payload)

    @pytest.mark.parametrize("payload", [_db_payload(), _redis_payload()])
    def test_non_dict_tier_passes_through(self, payload: dict[str, Any]) -> None:
        """Impl risk (ii): a non-dict tier must not crash the guard (nor the copy)."""
        payload["tier"] = "tier-3"
        assert_user_payload_shape(payload)
        assert _reference_two_level_copy(payload)["tier"] == "tier-3"

    @pytest.mark.parametrize("payload", [_db_payload(), _redis_payload()])
    def test_nested_top_level_dict_fails(self, payload: dict[str, Any]) -> None:
        payload["sso"] = {"provider": "google"}
        with pytest.raises(TypeError, match="sso"):
            assert_user_payload_shape(payload)

    @pytest.mark.parametrize("payload", [_db_payload(), _redis_payload()])
    def test_top_level_list_fails(self, payload: dict[str, Any]) -> None:
        payload["roles"] = ["admin"]
        with pytest.raises(TypeError, match="roles"):
            assert_user_payload_shape(payload)

    @pytest.mark.parametrize("payload", [_db_payload(), _redis_payload()])
    def test_nested_dict_inside_tier_fails(self, payload: dict[str, Any]) -> None:
        assert isinstance(payload["tier"], dict)
        payload["tier"]["limits"] = {"rps": 10}
        with pytest.raises(TypeError, match="limits"):
            assert_user_payload_shape(payload)

    @pytest.mark.parametrize("payload", [_db_payload(), _redis_payload()])
    def test_list_inside_tier_fails(self, payload: dict[str, Any]) -> None:
        assert isinstance(payload["tier"], dict)
        payload["tier"]["features"] = ["a"]
        with pytest.raises(TypeError, match="features"):
            assert_user_payload_shape(payload)


class TestDifferential:
    @pytest.mark.parametrize("payload", [_db_payload(), _redis_payload()], ids=["db", "redis"])
    def test_deepcopy_equals_two_level_copy(self, payload: dict[str, Any]) -> None:
        """The load-bearing equivalence: the fast copy returns what deepcopy returns."""
        assert copy.deepcopy(payload) == _reference_two_level_copy(payload)

    @pytest.mark.parametrize("payload", [_db_payload(with_tier=False), _redis_payload(with_tier=False)])
    def test_differential_holds_without_tier(self, payload: dict[str, Any]) -> None:
        assert copy.deepcopy(payload) == _reference_two_level_copy(payload)

    def test_reference_copies_tier_by_value_not_by_alias(self) -> None:
        """Equality alone could pass with a shared ref — the tier dict must be new."""
        payload = _db_payload()
        assert _reference_two_level_copy(payload)["tier"] is not payload["tier"]


class TestMutationIsolation:
    @staticmethod
    def _mutate(copied: dict[str, Any]) -> None:
        copied["name"] = "Mutant Mutant"
        copied["token_version"] = 999
        assert isinstance(copied["tier"], dict)
        copied["tier"]["name"] = "mutant-tier"

    @pytest.mark.parametrize("payload", [_db_payload(), _redis_payload()], ids=["db", "redis"])
    def test_deepcopy_isolates(self, payload: dict[str, Any]) -> None:
        snapshot = copy.deepcopy(payload)
        self._mutate(copy.deepcopy(payload))
        assert payload == snapshot

    @pytest.mark.parametrize("payload", [_db_payload(), _redis_payload()], ids=["db", "redis"])
    def test_two_level_copy_isolates(self, payload: dict[str, Any]) -> None:
        """The property S-4 must preserve: same isolation as deepcopy, both sources."""
        snapshot = copy.deepcopy(payload)
        copied = _reference_two_level_copy(payload)
        self._mutate(copied)
        assert payload == snapshot

    @pytest.mark.parametrize("payload", [_db_payload(), _redis_payload()], ids=["db", "redis"])
    def test_top_level_rebinding_never_aliases(self, payload: dict[str, Any]) -> None:
        copied = _reference_two_level_copy(payload)
        copied["email"] = "mutant@example.com"
        assert payload["email"] == "user.userson@example.com"


class TestGuardIsInstalledNotJustDefined:
    def test_guard_is_called_at_all_three_copy_sites(self) -> None:
        """A guard that exists but is never called guards nothing.

        The three ``copy.deepcopy`` sites in cache.py (L1-hit read, Redis
        fill, auth-info write) must each have the shape check beside them.
        """
        tree = ast.parse(
            (REPO_ROOT / "src" / "app" / "modules" / "users" / "cache.py").read_text(encoding="utf-8")
        )
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "assert_user_payload_shape"
        ]
        assert len(calls) >= 3, (
            f"expected the shape guard at all three copy sites, found {len(calls)} call(s)"
        )

    def test_guard_is_the_production_function(self) -> None:
        assert users_cache.assert_user_payload_shape is assert_user_payload_shape
