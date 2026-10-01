"""Response schemas for mutations that return a small fixed body (io15.8).

These exist because `response_model` does not just DOCUMENT a payload, it
FILTERS one: every field the schema omits is dropped from the response. So a
schema added for typing has to be faithful to what the handler actually
returns, or the "typed" endpoint starts sending something different from the
one clients already parse.

Each schema below mirrors a handler's real return, field for field. Where a
handler returns something that cannot be modelled without risking a payload
change, it is deliberately NOT given a schema — see
`scripts/check_mutation_response_models.py`, whose ALLOWLIST records every
mutating endpoint that stays undeclared and why, and which fails on a new one.
"""

from pydantic import BaseModel, Field


class MessageResponse(BaseModel):
    """`{"message": "..."}` — the shape used by most delete/confirm routes.

    Distinct from the same-named model in `src/app/core/schemas.py`, which is
    the one `posts`, `rate_limits` and `tiers` declare on their routes. Two
    models with one field and one name are a trap: a reader who imports the
    wrong one gets an OpenAPI schema that is structurally identical and a
    diff that does not explain itself. This one exists only for the routes
    annotated in the io15.8 sweep; the core one is untouched because it is
    the one three routers already depend on.
    """

    message: str


class MessageWithIdResponse(BaseModel):
    """`{"message": "...", "id": ...}` — a soft delete that echoes the id.

    Kept separate from `MessageResponse` rather than adding an optional `id`
    there: an optional field would make `id` appear in the generated OpenAPI
    for every caller, and would let a second endpoint start relying on an `id`
    its handler never sets.
    """

    message: str
    id: int


class CircuitBreakerResetResponse(BaseModel):
    """Result of resetting one named circuit breaker."""

    status: str
    name: str


class CircuitBreakerResetAllResponse(BaseModel):
    """Result of resetting every circuit breaker."""

    status: str
    count: int


class MFAEnableResponse(BaseModel):
    """Result of enabling TOTP MFA.

    `backup_codes` is returned in plaintext exactly once, at enable time, and
    is never retrievable afterwards. That is why it is declared rather than
    left to `dict[str, Any]`: the field being visible in the OpenAPI schema is
    a deliberate, reviewable statement about a sensitive payload.
    """

    message: str
    backup_codes: list[str] = Field(description="Shown once; stored only as hashes.")
    backup_codes_remaining: int


class MFADisableResponse(BaseModel):
    """Result of disabling TOTP MFA.

    ``/disable`` answers with a confirmation, not a status snapshot: the
    TOTP method row is deleted, so there is no longer a code count or a
    ``last_used`` to report. A caller wanting the post-state asks
    ``/status``, which serves ``MFAStatusResponse`` and correctly reports
    ``is_enabled: False``.

    The field set was previously ``is_enabled`` / ``backup_codes_remaining``
    / ``last_used``, which the endpoint never returned — every call to
    ``/disable`` failed response validation. The annotation is the useful
    part of this class: it puts the response in the OpenAPI schema instead
    of leaving it an untyped ``dict[str, str]``. It must therefore describe
    what the endpoint actually sends.
    """

    message: str
