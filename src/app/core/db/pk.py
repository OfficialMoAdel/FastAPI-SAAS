"""R3-03: typed helpers for mixed int↔UUID PK joins.

Most tables use ``Mapped[int]`` PKs while ``tenants``/``audit_logs`` use
``Mapped[UUID]``. New tables must use UUID (ADR-0003). Joins that mix types
(e.g. ``User.tenant_id: UUID`` vs ``User.id: int`` in filters) go through
these helpers instead of ad-hoc ``UUID(str(x))`` casts hidden behind
``type: ignore``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import UUID as SA_UUID  # noqa: F401 — re-exported for typing

# P0-3: uuid_utils was never a declared dependency (pyproject ships uuid6).
# Use uuid6's uuid7 — the same package the models import — and fail loudly
# on a missing dependency instead of silently degrading to uuid4 (which
# broke the time-ordered UUIDv7 contract for every new_uuid7() consumer).
from uuid6 import uuid7

UUID = uuid.UUID


def cast_tenant_id(value: str | UUID | None) -> UUID | None:
    """Coerce a tenant identifier to UUID, preserving None."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def cast_user_id(value: str | int | None) -> int | None:
    """Coerce a user identifier to int, preserving None.

    Accepts the int PK as int or as its string representation (e.g. from JWT
    ``sub``). Raises ``ValueError`` on non-numeric strings.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return int(str(value))


def new_uuid7() -> UUID:
    """Generate a time-ordered UUIDv7 for new PKs."""
    return uuid7()  # type: ignore[no-any-return]
