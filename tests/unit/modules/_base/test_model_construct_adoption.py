"""Phase 3.1 — ``model_construct`` for trusted DB list rows: NOT adopted.

The plan asked for a per-endpoint opt-in that replaces ``model_validate`` with
``model_construct`` on DB-backed list rows. Measured on this tree, the premise
does not hold, and one of the endpoints ADR-0012 pre-approved would change its
serialized output. Both facts are pinned here so the cut is not re-proposed
without confronting them (the same treatment ADR-0026 gave F-13 Cuts 3/4).

Context that keeps this honest: ``model_construct`` IS faster for a schema
whose validation is expensive (``UserRead``'s ``EmailStr``; see
``tests/unit/core/utils/test_regex_pydantic_optimizations.py``). The rejection
is about THESE list schemas and about the transform validators some of them
carry, not a claim about pydantic in general.
"""

from __future__ import annotations

import timeit
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.app.modules.api_keys.schemas import APIKeyRead
from src.app.modules.posts.schemas import PostRead

_REPO = Path(__file__).resolve().parents[4]


def _post_row() -> dict:
    return {
        "id": 7,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": None,
        "title": "Hello world",
        "text": "body",
        "media_url": None,
        "created_by_user_id": 3,
        "tenant_id": uuid4(),
    }


def _api_key_row() -> dict:
    return {
        "id": 7,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": None,
        "name": "key",
        "key_prefix": "abcd1234",
        "user_id": 3,
        "tenant_id": uuid4(),
        "is_active": True,
        # The COLUMN is JSON text; the read schema turns it into a list.
        "scopes": '["read"]',
        "expires_at": None,
        "last_used_at": None,
    }


def _best_of(fn, number: int = 2000) -> float:
    return min(timeit.repeat(fn, number=number, repeat=5)) / number


class TestModelConstructIsRejectedOnMeasurement:
    def test_a_cheap_read_schema_costs_more_to_construct(self) -> None:
        """``model_construct`` is a pessimization for a cheap list schema.

        ``PostRead`` validates almost nothing, so ``model_validate`` is mostly
        a dict copy — while ``model_construct(**row)`` pays Python-level
        kwargs unpacking plus pydantic's construct path. The measured gap is
        ~2.4x; the assertion only requires "slower", with a margin, so a busy
        CI box cannot make it flaky in the other direction.
        """
        row = _post_row()
        validate_us = _best_of(lambda: PostRead.model_validate(row)) * 1e6
        construct_us = _best_of(lambda: PostRead.model_construct(**row)) * 1e6

        assert construct_us > validate_us * 1.2, (
            f"model_construct is no longer slower than model_validate for PostRead "
            f"(validate={validate_us:.2f}us construct={construct_us:.2f}us). "
            "If that flips, Phase 3.1 is worth re-opening — but re-run the "
            "serialized-output parity check below first."
        )

    def test_the_serialized_output_is_identical_when_no_validator_transforms(self) -> None:
        """The plan's parity check, on the schema where it PASSES.

        Identical output means the only thing ``model_construct`` buys is
        (negative) speed, which is why nothing was adopted.
        """
        row = _post_row()
        assert PostRead.model_construct(**row).model_dump() == PostRead.model_validate(row).model_dump()

    def test_a_transforming_validator_makes_construct_observably_wrong(self) -> None:
        """The api_keys list is OUT, despite ADR-0012 pre-approving it.

        ``APIKeyRead.scopes`` has a ``mode="before"`` validator that decodes
        the column's JSON text into a list. ``model_construct`` skips it, so
        the API would serve ``"[\"read\"]"`` where it serves ``["read"]``
        today. That is a response-contract change, not a speed change, so the
        ADR's blanket ACCEPT does not reach this endpoint.
        """
        row = _api_key_row()

        validated = APIKeyRead.model_validate(row)
        constructed = APIKeyRead.model_construct(**row)

        assert validated.scopes == ["read"]
        assert constructed.scopes == '["read"]'
        assert validated.model_dump() != constructed.model_dump()

    def test_production_code_does_not_call_model_construct(self) -> None:
        """ADR-0012's own factual claim, re-asserted as a gate.

        Adopting the cut somewhere must update ADR-0012 (and this test) with a
        parity check for THAT schema, rather than appearing silently.
        """
        offenders: list[str] = []
        for path in (_REPO / "src" / "app").rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if "model_construct(" in line:
                    offenders.append(f"{path.relative_to(_REPO)}:{number}: {line.strip()}")

        assert offenders == [], (
            "model_construct is now called in src/app. Phase 3.1 was rejected on "
            f"measurement (see this file); re-open with a per-endpoint parity test. Found: {offenders}"
        )
