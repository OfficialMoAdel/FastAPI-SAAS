"""Security budget seam — one owner for identity keying, leg composition, and refusal.

CONTEXT.md names the concept: a **Security budget** is an identity-keyed
brute-force budget checked after routing, at the endpoint layer, once the
identity is known; it is distinct from **Path policy**, the ``(limit, period)``
resolved for a path + tier and checked at the edge. Both are expressed as a
**Verdict** — the outcome of ONE counter check — and this module is the single
place a Verdict for a security budget is assembled.

Before this module, every door composed its own key::

    check_security_budget("mfa_verify", str(ctx.user_id))            # routers/mfa.py
    check_security_budget("mfa_verify", request.mfa_token)          # services/mfa.py
    check_security_budget("mfa_verify_global", f"user:{uid}")       # services/mfa.py

Three doors, three hand-assembled key forms, and a fourth decision — which
budget name to charge for the per-account leg — taken by reading
``SECURITY_BUDGETS`` at the call site. A door could also pass any string it
liked as the identity, so the key namespace was caller-reachable.

A door now supplies an :class:`Identity` — facts about who is acting — and a
:class:`SecurityAction`. The leg set, the key namespace, the fail-closed
policy, and the refusal all come from :data:`BUDGETS` and this module. A door
cannot add, drop, or re-key a leg, and cannot assemble a key.

**The three MFA actions are three doors, not one defense with a duplicate
key.** ``POST /mfa/verify`` authenticates by session (step-up re-verification
for a logged-in user); ``POST /mfa/complete-login`` is unauthenticated
mid-login and only learns the account after peeking the pending token. They
meter separately and stay separate. They converge only on the durable Lua
lockout, which both reach through ``verify_totp_token``.

Key layout is unchanged. The namespace half is derived from ``leg.budget`` —
the ``SECURITY_BUDGETS`` entry supplying the leg's limit and period — and never
from the action or from caller input, so ``MFA_VERIFY_ACCOUNT`` keys on
``budget:mfa_verify_global`` while the two ``mfa_verify`` legs key on
``budget:mfa_verify``.

Deliberately *not* composed here: the durable MFA lockout. It is a different
mechanism — a Lua increment over ``mfa_attempts:{user_id}`` that refuses with
401 rather than 429 — owned by ``MFAService._check_mfa_lockout``. Composing it
would couple a generic security module to one feature's internals, so the
budget definitions *declare* it and ``test_security_budget_contract.py`` pins
that the declared owner still exists and that both MFA doors still route
through it.

Only the interface above the engine changed. ``consume``, ``build_verdict``,
``build_rate_limit_key``, ``RateLimitVerdict``, ``SECURITY_BUDGETS`` and
``SECURITY_BUDGETS_FAIL_CLOSED`` are used exactly as they were.

**Tenant scope: DELIBERATELY GLOBAL — AND NOW STRUCTURALLY SO.** ``check``
and ``probe`` no longer accept a ``tenant_id`` at all, so every budget key is
installation-wide and there is no code path that could scope one. That is a
decision, not an oversight, for three reasons:

1. **The two edge doors cannot supply a tenant.** ``GUARD_ORDER``
   (``core/guard/ordering.py``) executes ``RateLimit`` BEFORE ``Tenant``, so
   ``core/security/request_admission.py`` (``probe(REFRESH, ...)``) and
   ``modules/auth/rate_limit.py`` (``check(AUTH, ...)``) run before any
   tenant context exists. They are structurally unable to pass it.
2. **Scoping the IP-keyed login budgets would be a security regression.**
   ``AUTH``, ``PASSKEY_LOGIN`` and ``REFRESH`` cap brute force per source
   address. Tenant-scoping them would let an attacker mint a fresh budget by
   varying the tenant header — turning one global cap into a per-tenant one.
3. **Enabling scoping needs a coordinated Redis key migration.**
   ``build_tenant_key`` (``core/utils/cache/keys.py``) prefixes the key when a
   tenant is supplied, so scoping changes every live key; and
   ``core/security/rate_policy.py`` already records that this was
   deliberately left off pending that migration.

**P1-9 decision: Option B — the parameter was REMOVED, not threaded.** The
parameter was accepted by every door and supplied by none, which made
``test_security_budget_contract.py::test_golden_key_is_tenant_scoped`` a green
test proving a capability with zero production effect — the most misleading
kind of test. Option A (threading ``ctx.tenant_id`` through the user-identity
doors) was rejected: reason 2 shows per-tenant budgets are a security
regression, and doing it for *some* budgets would leave the property
inconsistent and invisible. The contract test now pins the global key as the
only reachable form.

Enabling tenant scope remains a separate, coordinated change: migrate the
Redis keys (resetting every in-flight counter), then reintroduce the
parameter deliberately. ``test_security_budget_contract.py`` pins the global
keys so that change is a visible act rather than an accident.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.app.core.config import settings
from src.app.core.security.rate_policy import (
    RateLimitVerdict,
    build_rate_limit_key,
    build_verdict,
    consume,
)

__all__ = [
    "BUDGETS",
    "BudgetDef",
    "DurableLockout",
    "Identity",
    "KeyKind",
    "Leg",
    "SecurityAction",
    "check",
    "probe",
    "validate_budget_definitions",
]


class SecurityAction(StrEnum):
    """The security action being attempted. Names a *budget*, not a path.

    Derived from the CONTEXT.md seam vocabulary: a security budget is endpoint
    semantics keyed on identity, so the caller names the action and the budget
    definition names the meters.

    Values are unique per *door* and are deliberately NOT the meter names. Two
    doors meter ``mfa_verify`` at different identity kinds, so action names
    would alias each other here and collapse their BUDGETS entries; keeping
    them distinct also makes it visible at a glance that the key namespace
    comes from ``leg.budget`` and never from this enum.
    """

    AUTH = "auth"
    MFA_STEPUP_VERIFY = "mfa_stepup_verify"
    MFA_VERIFY_PENDING = "mfa_verify_pending"
    MFA_VERIFY_ACCOUNT = "mfa_verify_account"
    MFA_MUTATE = "mfa_mutate"
    PASSKEY_LOGIN = "passkey_login"
    MAGIC_LINK_SEND = "magic_link_send"
    REFRESH = "refresh"
    AI_CHAT = "ai_chat"


class KeyKind(StrEnum):
    """Which identity fact a leg meters, and how that fact is rendered.

    ``render`` produces the exact string today's doors hand-assembled, so the
    Redis key bytes are preserved. A kind never accepts caller-composed key
    material.
    """

    USER = "user"
    USER_PREFIXED = "user:"
    IP = "ip"
    TOKEN = "token"
    EMAIL = "email"

    @property
    def identity_field(self) -> str:
        """The :class:`Identity` attribute this kind reads.

        Named separately from the value: the value is the *rendering* prefix
        ("user:", "email"), while a door author needs to know which field of
        Identity to populate when a leg rejects.
        """
        if self in (KeyKind.USER, KeyKind.USER_PREFIXED):
            return "user_id"
        if self is KeyKind.EMAIL:
            return "email_hash"
        return str(self)

    def render(self, fact: Any) -> str:
        """Render an identity fact into the identifier slot of the budget key."""
        if self is KeyKind.USER_PREFIXED:
            return f"user:{fact}"
        if self is KeyKind.EMAIL:
            return f"email:{fact}"
        return str(fact)


@dataclass(frozen=True, slots=True)
class Identity:
    """Facts about who is acting. Facts only — never a pre-assembled key.

    ``user_id`` is deliberately *not* normalised here: an int and its decimal
    string must produce identical key bytes, and that normalisation belongs in
    one place, the key builder. Golden tests pin both spellings.
    """

    user_id: str | int | None = None
    ip: str | None = None
    token: str | None = None
    email_hash: str | None = None

    def fact_for(self, kind: KeyKind) -> Any:
        """The raw fact this leg meters, or None when the door did not supply it."""
        match kind:
            case KeyKind.USER | KeyKind.USER_PREFIXED:
                return self.user_id
            case KeyKind.IP:
                return self.ip
            case KeyKind.TOKEN:
                return self.token
            case KeyKind.EMAIL:
                return self.email_hash


@dataclass(frozen=True, slots=True)
class Leg:
    """One counter within a budget definition.

    ``budget`` is the ``SECURITY_BUDGETS`` entry supplying ``(limit, period)``
    *and* the key namespace. It is intentionally independent of the action's
    own name: the per-account MFA leg names ``mfa_verify_global`` so it meters
    separately from the 5/60 token and step-up legs.
    """

    kind: KeyKind
    budget: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class DurableLockout:
    """A non-rate-budget control that sits behind a budget's legs.

    Declared so the layering lives in one table and cannot rot into a lie; not
    composed, because its mechanism and refusal differ from a counter leg.
    """

    owner: str
    attribute: str
    key_pattern: str
    refusal: str
    reached_via: str
    """Attribute on ``owner`` whose body enforces the lockout, so the contract
    test can check the enforcement point as well as the declaration."""


@dataclass(frozen=True, slots=True)
class BudgetDef:
    """The complete definition of one security action."""

    action: SecurityAction
    legs: tuple[Leg, ...]
    durable_lockout: DurableLockout | None = None
    #: Whether this budget MUST be declared in SECURITY_BUDGETS_FAIL_CLOSED,
    #: so a Redis outage keeps refusing instead of admitting. True for every
    #: identity-keyed brute-force control. False is a deliberate, visible
    #: opt-out for a quota: failing it CLOSED would take the feature down on
    #: any Redis blip, which is an availability decision, not a security one.
    #: validate_budget_definitions() enforces that the table and the config
    #: agree in BOTH directions, so neither side can drift silently.
    fail_closed: bool = True


# Both MFA verification doors reach the same durable lockout through
# verify_totp_token -> _check_mfa_lockout. Declared once and referenced by
# both actions so the two doors cannot diverge in how they declare their
# backstop; the contract test pins that both still route through it.
_MFA_DURABLE_LOCKOUT = DurableLockout(
    owner="src.app.modules.auth.services.mfa",
    attribute="MFAService._check_mfa_lockout",
    key_pattern="mfa_attempts:{user_id}",
    refusal="401 UnauthorizedError('MFA locked...')",
    reached_via="MFAService.verify_totp_token",
)

BUDGETS: Mapping[SecurityAction, BudgetDef] = {
    SecurityAction.AUTH: BudgetDef(
        action=SecurityAction.AUTH,
        legs=(Leg(KeyKind.IP, "auth", required=False),),
    ),
    SecurityAction.PASSKEY_LOGIN: BudgetDef(
        action=SecurityAction.PASSKEY_LOGIN,
        legs=(Leg(KeyKind.IP, "passkey_login", required=False),),
    ),
    SecurityAction.MAGIC_LINK_SEND: BudgetDef(
        action=SecurityAction.MAGIC_LINK_SEND,
        legs=(Leg(KeyKind.EMAIL, "magic_link_send"),),
    ),
    SecurityAction.MFA_MUTATE: BudgetDef(
        action=SecurityAction.MFA_MUTATE,
        legs=(Leg(KeyKind.USER, "mfa_mutate"),),
    ),
    SecurityAction.REFRESH: BudgetDef(
        action=SecurityAction.REFRESH,
        legs=(Leg(KeyKind.IP, "refresh", required=False),),
    ),
    SecurityAction.AI_CHAT: BudgetDef(
        action=SecurityAction.AI_CHAT,
        legs=(Leg(KeyKind.USER, "ai_chat"),),
        # Not a brute-force control: it meters per-user AI usage, not an
        # attack. Failing it closed would make the AI feature unavailable on
        # any Redis blip, so the posture follows RATE_LIMIT_FAIL_OPEN like
        # ordinary path policy.
        fail_closed=False,
    ),
    # --- MFA verification: three doors, three meters, deliberately separate ---
    #
    # Step-up: POST /mfa/verify, authenticated session, account known up front.
    SecurityAction.MFA_STEPUP_VERIFY: BudgetDef(
        action=SecurityAction.MFA_STEPUP_VERIFY,
        legs=(Leg(KeyKind.USER, "mfa_verify"),),
        durable_lockout=_MFA_DURABLE_LOCKOUT,
    ),
    # Pending token: POST /mfa/complete-login, charged BEFORE the pending token
    # is peeked, because the account is not known until the peek succeeds.
    SecurityAction.MFA_VERIFY_PENDING: BudgetDef(
        action=SecurityAction.MFA_VERIFY_PENDING,
        legs=(Leg(KeyKind.TOKEN, "mfa_verify"),),
    ),
    # Per-account backstop across rotating pending tokens. 15/60, deliberately
    # 3x the 5-attempt durable lockout so the lockout stays the first
    # observable control and this leg only backstops distributed spraying.
    SecurityAction.MFA_VERIFY_ACCOUNT: BudgetDef(
        action=SecurityAction.MFA_VERIFY_ACCOUNT,
        legs=(Leg(KeyKind.USER_PREFIXED, "mfa_verify_global"),),
        durable_lockout=_MFA_DURABLE_LOCKOUT,
    ),
}


def validate_budget_definitions() -> None:
    """Assert every leg names a CONFIGURED and FAIL-CLOSED budget. Boot gate.

    Leg budget names come from this table, so an operator who removes
    ``mfa_verify_global`` from ``SECURITY_BUDGETS`` would otherwise hit a
    ``KeyError`` on the first MFA verification in production. This turns that
    into a boot failure. It also retires the old call-site behaviour, where a
    door sniffed ``SECURITY_BUDGETS`` and silently fell back to a different
    budget name when the preferred one was absent.
    """
    configured = settings.rate_limit.SECURITY_BUDGETS
    missing = sorted(
        f"{action} leg {leg.kind} -> {leg.budget}"
        for action, definition in BUDGETS.items()
        for leg in definition.legs
        if leg.budget not in configured
    )
    if missing:
        raise RuntimeError(
            "security budget definitions reference unconfigured budgets: "
            + "; ".join(missing)
            + f". Configured: {sorted(configured)}"
        )

    # A budget that is configured but absent from SECURITY_BUDGETS_FAIL_CLOSED
    # still boots clean and then silently fails OPEN on a Redis outage —
    # consumed at :371 as `fail_closed=leg.budget in ..._FAIL_CLOSED`.
    #
    # Each BudgetDef declares its required posture (fail_closed=True by
    # default), so adding a budget without declaring a posture is now a boot
    # failure rather than a silent downgrade of a brute-force control. The
    # check runs both ways, so the table and the config cannot disagree.
    fail_closed_cfg = settings.rate_limit.SECURITY_BUDGETS_FAIL_CLOSED
    wrong_posture = sorted(
        f"{action} leg {leg.kind} -> {leg.budget} "
        f"(declares fail_closed={definition.fail_closed}, "
        f"{'absent from' if definition.fail_closed else 'present in'} "
        "SECURITY_BUDGETS_FAIL_CLOSED)"
        for action, definition in BUDGETS.items()
        for leg in definition.legs
        if (leg.budget in fail_closed_cfg) is not definition.fail_closed
    )
    if wrong_posture:
        raise RuntimeError(
            "security budget fail-closed posture disagrees with configuration: "
            + "; ".join(wrong_posture)
            + f". Declared: {sorted(fail_closed_cfg)}"
        )


def _definition(action: SecurityAction) -> BudgetDef:
    """Resolve a budget definition, failing loud on an unknown action."""
    try:
        return BUDGETS[SecurityAction(action)]
    except (KeyError, ValueError):
        raise KeyError(
            f"Unknown security action {str(action)!r}; known actions: {sorted(str(a) for a in BUDGETS)}"
        ) from None


def _render_leg(leg: Leg, identity: Identity, action: SecurityAction) -> str:
    """Render one leg's identity fact, or fail loud when it is missing.

    A falsy fact is treated as absent, which preserves today's bytes for an
    optional leg: the doors used to pass ``client_ip or "unknown"``, so an
    empty string and a None both keyed the literal ``unknown`` bucket. For a
    required leg a falsy fact now fails loud instead of silently landing in
    ``build_rate_limit_key``'s anonymous branch, which is the fail-loud
    behaviour the seam is for.
    """
    fact = identity.fact_for(leg.kind)
    if not fact:
        if leg.required:
            raise ValueError(
                f"security action {str(action)!r} requires identity fact "
                f"{leg.kind.identity_field!r} for its {leg.budget} leg"
            )
        return "unknown"
    return leg.kind.render(fact)


def _leg_key(leg: Leg, rendered: str) -> str:
    """Build one leg's Redis key.

    The namespace half comes from ``leg.budget`` — never from the action and
    never from caller input. ``rendered`` is non-empty by construction, so
    this always takes the identifier branch of ``build_rate_limit_key`` and
    can never silently land in the anonymous bucket.

    P1-9: ``tenant_id`` is gone. Every budget key is installation-wide, and
    that is a decision, not an oversight — see the module docstring
    ("Tenant scope: DELIBERATELY GLOBAL AT EVERY CALL SITE") for the three
    reasons. Keeping an accepted-but-never-supplied parameter produced a
    contract test that proved tenant scoping "works" with zero production
    effect; removing it makes the global scope the only reachable behaviour.
    """
    return build_rate_limit_key(f"budget:{leg.budget}", user_id=rendered)


async def _evaluate(
    action: SecurityAction,
    identity: Identity,
    *,
    limiter: Any,
) -> tuple[BudgetDef, Leg | None, RateLimitVerdict]:
    """Meter every leg of ``action``; return the first limited leg's verdict.

    Two passes, always: pass 1 renders and validates every leg, pass 2 charges.
    A partial identity therefore raises before any counter is touched, so a
    rejected call can never leave a charge behind.

    Returns ``(definition, limited_leg_or_None, verdict)``. When nothing is
    limited, ``limited_leg`` is None and the verdict belongs to the first leg,
    which is what carries ``limit``/``remaining`` for response headers.
    """
    definition = _definition(action)

    # Pass 1 — validate the whole leg set is satisfiable.
    rendered = [_render_leg(leg, identity, action) for leg in definition.legs]
    keys = [_leg_key(leg, text) for leg, text in zip(definition.legs, rendered, strict=True)]

    # Pass 2 — charge.
    first_verdict: RateLimitVerdict | None = None
    for leg, key in zip(definition.legs, keys, strict=True):
        limit, period = settings.rate_limit.SECURITY_BUDGETS[leg.budget]
        is_limited, remaining, retry_after, degraded = await consume(
            key,
            limit,
            period,
            fail_closed=leg.budget in settings.rate_limit.SECURITY_BUDGETS_FAIL_CLOSED,
            limiter=limiter,
        )
        verdict = build_verdict(
            key=key,
            limit=limit,
            period=period,
            is_limited=is_limited,
            remaining=remaining,
            retry_after=retry_after,
            degraded=degraded,
        )
        if first_verdict is None:
            first_verdict = verdict
        if is_limited:
            return definition, leg, verdict

    # Unreachable while every definition has >= 1 leg; stated so a future
    # zero-leg definition fails loudly instead of returning None.
    if first_verdict is None:
        raise ValueError(f"security action {str(action)!r} has no legs")
    return definition, None, first_verdict


async def check(
    action: SecurityAction,
    identity: Identity,
    *,
    limiter: Any = None,
) -> None:
    """Meter ``action`` against ``identity``; raise on the first limited leg.

    The single refusal site for security budgets. Returns None when every leg
    has remaining capacity.

    Raises:
        RateLimitExceededError: a leg is over its limit (429, Retry-After).
        ValueError: a required identity fact was not supplied.
        KeyError: unknown action.

    Note:
        P1-9: there is no ``tenant_id`` parameter. Every budget key is
        installation-wide by design; the three reasons, and the Redis key
        migration that would change it, are in the module docstring under
        "Tenant scope: DELIBERATELY GLOBAL AT EVERY CALL SITE".
    """
    from src.app.shared.exceptions import RateLimitExceededError

    definition, limited_leg, verdict = await _evaluate(action, identity, limiter=limiter)
    if limited_leg is None:
        return
    raise RateLimitExceededError(
        f"Too many {str(definition.action)} attempts. Please try again in {verdict.retry_after_seconds()} seconds.",
        retry_after=verdict.retry_after_seconds(),
    )


async def probe(
    action: SecurityAction,
    identity: Identity,
    *,
    limiter: Any = None,
) -> RateLimitVerdict:
    """Meter ``action`` exactly as :func:`check` does, returning the Verdict.

    For the two callers whose transport response is shaped by the numbers —
    admission (an ``AdmissionOutcome``) and the AI Depends (an
    ``HTTPException`` carrying ``X-RateLimit-*``). Those two must not be given
    a bare exception, and the six others must not be given a Verdict to raise
    by hand, so both entry points exist and share one evaluator.

    Returns the first limited leg's Verdict, or the first leg's when nothing is
    limited — the latter carries ``limit``/``remaining`` for response headers.

    Note:
        P1-9: there is no ``tenant_id`` parameter. Every budget key is
        installation-wide by design; the three reasons, and the Redis key
        migration that would change it, are in the module docstring under
        "Tenant scope: DELIBERATELY GLOBAL AT EVERY CALL SITE".
    """
    _, _, verdict = await _evaluate(action, identity, limiter=limiter)
    return verdict
