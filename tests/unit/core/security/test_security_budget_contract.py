"""Contract tests for the security budget seam (card 1).

The interface is the test surface. These tests pin the four things a door used
to be able to get wrong: which counters a budget has, what bytes the key is,
that the namespace half comes from ``leg.budget`` rather than the action, and
that the refusal is identical no matter which door tripped it.

``test_core.py`` pins the legacy ``check_security_budget`` bytes; these pin the
seam, and the MFA rows below are byte-identical to what the doors emit today.
"""

import importlib
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.core.security.budgets import (
    BUDGETS,
    BudgetDef,
    Identity,
    KeyKind,
    Leg,
    SecurityAction,
    check,
    probe,
    validate_budget_definitions,
)
from src.app.shared.exceptions import RateLimitExceededError


def _limiter(is_limited=False, remaining=5, retry_after=0):
    lim = MagicMock()
    lim.check_rate_limit_algorithm = AsyncMock(return_value=(is_limited, remaining, retry_after))
    return lim


def _keys(limiter):
    return [c.args[0] for c in limiter.check_rate_limit_algorithm.call_args_list]


# ---------------------------------------------------------------- golden keys
# Every expected string below is what the corresponding door emits TODAY. No
# MFA key byte changes; the three MFA actions exist so the meters stay
# separate, not so their keys move.


@pytest.mark.parametrize(
    ("action", "identity", "tenant_id", "expected"),
    [
        (SecurityAction.AUTH, Identity(ip="1.2.3.4"), None, "rate_limit:user:1.2.3.4:budget:auth"),
        (
            SecurityAction.PASSKEY_LOGIN,
            Identity(ip="1.2.3.4"),
            None,
            "rate_limit:user:1.2.3.4:budget:passkey_login",
        ),
        (
            SecurityAction.MAGIC_LINK_SEND,
            Identity(email_hash="abc123"),
            None,
            "rate_limit:user:email:abc123:budget:magic_link_send",
        ),
        (SecurityAction.MFA_MUTATE, Identity(user_id="42"), None, "rate_limit:user:42:budget:mfa_mutate"),
        (SecurityAction.AI_CHAT, Identity(user_id="42"), None, "rate_limit:user:42:budget:ai_chat"),
        (SecurityAction.REFRESH, Identity(ip="1.2.3.4"), None, "rate_limit:user:1.2.3.4:budget:refresh"),
        # MFA door 1 — POST /mfa/verify, step-up, account known up front.
        (SecurityAction.MFA_STEPUP_VERIFY, Identity(user_id="42"), None, "rate_limit:user:42:budget:mfa_verify"),
        # MFA door 2 — POST /mfa/complete-login, charged before the peek.
        (
            SecurityAction.MFA_VERIFY_PENDING,
            Identity(token="tok-abc"),
            None,
            "rate_limit:user:tok-abc:budget:mfa_verify",
        ),
        # MFA door 2, after the peek resolves the account. 15/60, own namespace.
        (
            SecurityAction.MFA_VERIFY_ACCOUNT,
            Identity(user_id="42"),
            None,
            "rate_limit:user:user:42:budget:mfa_verify_global",
        ),
    ],
)
async def test_golden_key_bytes(action, identity, tenant_id, expected):
    """Every leg's Redis key is pinned byte-for-byte against today's output."""
    limiter = _limiter()
    await probe(action, identity, tenant_id=tenant_id, limiter=limiter)
    assert _keys(limiter)[0] == expected


async def test_the_three_mfa_doors_stay_on_three_separate_meters():
    """The bug card 1 fixes: one defense was metered under three key spellings.

    Collapsing any two of these would merge two independent doors' limits.
    """
    stepup, pending, account = _limiter(), _limiter(), _limiter()
    await probe(SecurityAction.MFA_STEPUP_VERIFY, Identity(user_id="42"), limiter=stepup)
    await probe(SecurityAction.MFA_VERIFY_PENDING, Identity(token="t"), limiter=pending)
    await probe(SecurityAction.MFA_VERIFY_ACCOUNT, Identity(user_id="42"), limiter=account)

    keys = {_keys(stepup)[0], _keys(pending)[0], _keys(account)[0]}
    assert len(keys) == 3, f"MFA meters collapsed onto shared keys: {keys}"


async def test_golden_key_is_tenant_scoped():
    limiter = _limiter()
    await probe(SecurityAction.MFA_MUTATE, Identity(user_id="42"), tenant_id="t1", limiter=limiter)
    assert _keys(limiter)[0] == "tenant:t1:rate_limit:user:42:budget:mfa_mutate"


async def test_user_id_int_and_str_produce_identical_bytes():
    """Normalisation lives in the key builder, so both spellings agree."""
    as_int, as_str = _limiter(), _limiter()
    await probe(SecurityAction.AI_CHAT, Identity(user_id=42), limiter=as_int)
    await probe(SecurityAction.AI_CHAT, Identity(user_id="42"), limiter=as_str)
    assert _keys(as_int)[0] == _keys(as_str)[0] == "rate_limit:user:42:budget:ai_chat"


async def test_namespace_half_comes_from_leg_budget_not_the_action(monkeypatch):
    """The action name never reaches the key.

    Every MFA action name differs from the meter it charges, so if the key
    were built from the action these keys would all read budget:mfa_* and the
    golden rows above would fail. They do not, so the namespace comes from
    leg.budget.
    """
    assert SecurityAction.MFA_STEPUP_VERIFY.value != BUDGETS[SecurityAction.MFA_STEPUP_VERIFY].legs[0].budget
    assert SecurityAction.MFA_VERIFY_ACCOUNT.value != BUDGETS[SecurityAction.MFA_VERIFY_ACCOUNT].legs[0].budget

    seen: list[str] = []
    from src.app.core.security import budgets

    original = budgets.build_rate_limit_key
    monkeypatch.setattr(
        budgets,
        "build_rate_limit_key",
        lambda path, *, user_id=None, tenant_id=None, anonymous_ip=None: (
            seen.append(path),
            original(path, user_id=user_id, tenant_id=tenant_id, anonymous_ip=anonymous_ip),
        )[1],
    )
    await probe(SecurityAction.MFA_VERIFY_ACCOUNT, Identity(user_id="42"), limiter=_limiter())
    assert seen == ["budget:mfa_verify_global"]


async def test_caller_input_never_reaches_the_key_namespace(monkeypatch):
    """Even a hostile identity value cannot alter the namespace half."""
    from src.app.core.security import budgets

    seen: list[str] = []
    original = budgets.build_rate_limit_key

    def _spy(path, *, user_id=None, tenant_id=None, anonymous_ip=None):
        seen.append(path)
        return original(path, user_id=user_id, tenant_id=tenant_id, anonymous_ip=anonymous_ip)

    monkeypatch.setattr(budgets, "build_rate_limit_key", _spy)

    await check(
        SecurityAction.MFA_MUTATE,
        Identity(user_id="budget:auth:1.2.3.4", ip="budget:auth:1.2.3.4"),
        limiter=_limiter(),
    )
    assert seen == ["budget:mfa_mutate"], f"namespace must come from leg.budget, got {seen}"


# ------------------------------------------------------------------ leg shape


async def test_each_action_declares_exactly_its_documented_legs():
    """A leg cannot be silently added, dropped, or re-keyed."""
    expected = {
        SecurityAction.AUTH: (("ip", "auth"),),
        SecurityAction.PASSKEY_LOGIN: (("ip", "passkey_login"),),
        SecurityAction.MAGIC_LINK_SEND: (("email", "magic_link_send"),),
        SecurityAction.MFA_MUTATE: (("user", "mfa_mutate"),),
        SecurityAction.REFRESH: (("ip", "refresh"),),
        SecurityAction.AI_CHAT: (("user", "ai_chat"),),
        SecurityAction.MFA_STEPUP_VERIFY: (("user", "mfa_verify"),),
        SecurityAction.MFA_VERIFY_PENDING: (("token", "mfa_verify"),),
        SecurityAction.MFA_VERIFY_ACCOUNT: (("user:", "mfa_verify_global"),),
    }
    actual = {
        action: tuple((leg.kind.value, leg.budget) for leg in definition.legs)
        for action, definition in BUDGETS.items()
    }
    assert actual == expected


async def test_missing_required_fact_raises_naming_the_identity_field():
    limiter = _limiter()
    with pytest.raises(ValueError, match="requires identity fact 'user_id'"):
        await check(SecurityAction.MFA_STEPUP_VERIFY, Identity(ip="1.2.3.4"), limiter=limiter)
    with pytest.raises(ValueError, match="requires identity fact 'token'"):
        await check(SecurityAction.MFA_VERIFY_PENDING, Identity(user_id="42"), limiter=limiter)
    with pytest.raises(ValueError, match="requires identity fact 'email_hash'"):
        await check(SecurityAction.MAGIC_LINK_SEND, Identity(ip="1.2.3.4"), limiter=limiter)
    assert _keys(limiter) == []


async def test_two_pass_evaluate_charges_nothing_when_a_leg_is_unsatisfiable(monkeypatch):
    """A future multi-leg action must not bill a counter it then rejects.

    Every shipped action has one leg, so this drives a synthetic two-leg
    definition to pin the pass-1/pass-2 ordering itself.
    """
    from src.app.core.security import budgets

    synthetic = BudgetDef(
        action=SecurityAction.AUTH,
        legs=(Leg(KeyKind.TOKEN, "auth"), Leg(KeyKind.USER, "auth")),
    )
    monkeypatch.setitem(budgets.BUDGETS, SecurityAction.AUTH, synthetic)

    limiter = _limiter()
    with pytest.raises(ValueError, match="requires identity fact 'user_id'"):
        await check(SecurityAction.AUTH, Identity(token="t"), limiter=limiter)

    assert _keys(limiter) == [], "pass 1 must validate every leg before pass 2 charges any"


async def test_ip_leg_is_optional_and_falls_back_to_the_unknown_bucket():
    """A request with no resolvable client IP keeps today's literal bucket."""
    limiter = _limiter()
    await probe(SecurityAction.REFRESH, Identity(), limiter=limiter)
    assert _keys(limiter)[0] == "rate_limit:user:unknown:budget:refresh"


async def test_unknown_action_fails_loud():
    with pytest.raises(KeyError, match="Unknown security action"):
        await check("not_a_budget", Identity(user_id="1"), limiter=_limiter())


# ------------------------------------------------- a door cannot build a key


async def test_identity_carries_no_field_that_can_hold_a_key():
    """No Identity field may smuggle in caller-composed key material."""
    fields = set(Identity.__dataclass_fields__)
    assert fields == {"user_id", "ip", "token", "email_hash"}
    assert not any("key" in name for name in fields)


async def test_entry_points_expose_no_free_form_identifier_parameter():
    """check/probe take facts, never a caller-built key or identifier string."""
    for fn in (check, probe):
        assert set(inspect.signature(fn).parameters) == {"action", "identity", "tenant_id", "limiter"}


# ------------------------------------------------------------------- refusal


def _identity_for(action):
    definition = BUDGETS[action]
    leg = definition.legs[0]
    return {
        KeyKind.IP: Identity(ip="1.2.3.4"),
        KeyKind.EMAIL: Identity(email_hash="h"),
        KeyKind.USER: Identity(user_id="42"),
        KeyKind.USER_PREFIXED: Identity(user_id="42"),
        KeyKind.TOKEN: Identity(token="t"),
    }[leg.kind]


@pytest.mark.parametrize("action", list(BUDGETS))
async def test_refusal_is_one_exception_shape_for_every_action(action):
    limiter = _limiter(is_limited=True, remaining=0, retry_after=37)
    with pytest.raises(RateLimitExceededError) as exc:
        await check(action, _identity_for(action), limiter=limiter)

    err = exc.value
    assert err.http_status == 429
    assert err.retry_after == 37
    assert "37 seconds" in err.message
    assert err.http_headers()["Retry-After"] == "37"


# ---------------------------------------------------------------- fail-closed


@pytest.mark.parametrize(
    ("action", "expected_fail_closed"),
    [
        (SecurityAction.AUTH, True),
        (SecurityAction.MFA_STEPUP_VERIFY, True),
        (SecurityAction.MFA_VERIFY_PENDING, True),
        (SecurityAction.MFA_VERIFY_ACCOUNT, True),
        (SecurityAction.MFA_MUTATE, True),
        (SecurityAction.PASSKEY_LOGIN, True),
        (SecurityAction.MAGIC_LINK_SEND, True),
        (SecurityAction.REFRESH, True),
        (SecurityAction.AI_CHAT, False),
    ],
)
async def test_fail_closed_policy_reaches_the_engine_per_leg(monkeypatch, action, expected_fail_closed):
    """The SECURITY_BUDGETS_FAIL_CLOSED set decides, and it must reach consume().

    Asserting the config table alone would not prove the flag is threaded
    through — a door must not be able to lose fail-closed by omission.
    """
    from src.app.core.security import budgets

    seen: list[bool] = []
    original = budgets.consume

    async def _spy(key, limit, period, *, fail_closed=False, limiter=None):
        seen.append(fail_closed)
        return await original(key, limit, period, fail_closed=fail_closed, limiter=limiter)

    monkeypatch.setattr(budgets, "consume", _spy)
    await probe(action, _identity_for(action), limiter=_limiter())

    assert seen == [expected_fail_closed], seen


# ------------------------------------------------------------------ probe


async def test_probe_returns_a_verdict_with_the_leg_numbers():
    limiter = _limiter(is_limited=False, remaining=3, retry_after=0)
    verdict = await probe(SecurityAction.AI_CHAT, Identity(user_id="42"), limiter=limiter)
    assert verdict.is_limited is False
    assert verdict.limit == 60
    assert verdict.period == 60
    assert verdict.remaining == 3
    assert verdict.headers["X-RateLimit-Limit"] == "60"


async def test_probe_and_check_meter_the_same_keys():
    """The two entry points differ only in refusal, never in what they charge."""
    a, b = _limiter(), _limiter()
    await probe(SecurityAction.MFA_STEPUP_VERIFY, Identity(user_id="42"), limiter=a)
    await check(SecurityAction.MFA_STEPUP_VERIFY, Identity(user_id="42"), limiter=b)
    assert _keys(a) == _keys(b)


# ------------------------------------------------------- boot-time validation


def test_boot_validation_passes_for_the_shipped_table():
    validate_budget_definitions()


def test_boot_validation_rejects_an_unconfigured_leg_budget(monkeypatch):
    """An operator removing mfa_verify_global must fail at boot, not at 3am.

    This is what retires the old call-site sniff, which silently fell back to
    a different budget name when the preferred one was absent.
    """
    from src.app.core.config import settings

    trimmed = {k: v for k, v in settings.rate_limit.SECURITY_BUDGETS.items() if k != "mfa_verify_global"}
    monkeypatch.setattr(settings.rate_limit, "SECURITY_BUDGETS", trimmed)

    with pytest.raises(RuntimeError, match="unconfigured budgets") as exc:
        validate_budget_definitions()
    assert "mfa_verify_global" in str(exc.value)


# ------------------------------------------------- durable lockout declaration


def test_both_mfa_verification_doors_declare_the_durable_lockout():
    """The third tier is declared, not composed — so pin that both doors have it.

    If MFAService._check_mfa_lockout is renamed or deleted without updating the
    table, this fails instead of the layering quietly becoming two-deep.
    """
    module = importlib.import_module("src.app.modules.auth.services.mfa")
    lockout = BUDGETS[SecurityAction.MFA_STEPUP_VERIFY].durable_lockout
    assert lockout is not None
    assert lockout is BUDGETS[SecurityAction.MFA_VERIFY_ACCOUNT].durable_lockout, (
        "both MFA doors must declare the same backstop so they cannot diverge"
    )

    owner_class, _, method = lockout.attribute.partition(".")
    assert hasattr(getattr(module, owner_class), method)
    assert lockout.key_pattern == "mfa_attempts:{user_id}"
    assert "401" in lockout.refusal


def test_both_mfa_doors_still_route_through_the_durable_lockout():
    """The declaration must not rot: both doors reach verify_totop_token.

    Structural source check, in the same spirit as the existing
    test_startup_wires_the_assertion guard: it pins that the path named by
    reached_via is still on the call chain of each MFA door.
    """
    from src.app.modules.auth.routers import mfa as mfa_router
    from src.app.modules.auth.services import mfa as mfa_service

    lockout = BUDGETS[SecurityAction.MFA_STEPUP_VERIFY].durable_lockout
    owner_class, _, entry_method = lockout.reached_via.partition(".")

    # The shared hop both doors pass through enforces the lockout.
    entry_source = inspect.getsource(getattr(mfa_service.MFAService, entry_method))
    assert "_check_mfa_lockout" in entry_source

    # Door 1: POST /mfa/verify -> verify_totop_token
    stepup_source = inspect.getsource(mfa_router.verify_mfa)
    assert entry_method in stepup_source, "step-up door no longer calls verify_totp_token"

    # Door 2: complete_login -> verify_totp_token
    assert entry_method in inspect.getsource(mfa_service.MFAService.complete_login)
