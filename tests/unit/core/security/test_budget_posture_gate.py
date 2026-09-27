"""The boot gate must enforce each budget's declared fail-closed posture.

``validate_budget_definitions`` is called at worker and web boot. It already
checked that every leg names a configured budget; it did NOT check the
fail-closed posture, which is consumed separately as
``fail_closed=leg.budget in SECURITY_BUDGETS_FAIL_CLOSED``.

So a newly added budget booted clean and then silently failed OPEN on a
Redis outage — degrading a brute-force control with no boot signal. The
defect is silent in exactly the way that matters: nothing is logged, the
control is simply absent during an outage.

Each ``BudgetDef`` now declares ``fail_closed`` (default True, i.e. the safe
posture), and the gate checks the table and the config agree in BOTH
directions, so neither side can drift.
"""

from unittest.mock import patch

import pytest

from src.app.core.security import budgets as budgets_mod
from src.app.core.security.budgets import BUDGETS, SecurityAction, validate_budget_definitions


def _validate_with(configured_fail_closed: set[str], configured_budgets: set[str] | None = None) -> None:
    with patch.object(budgets_mod.settings, "rate_limit") as rt:
        rt.SECURITY_BUDGETS = configured_budgets or {leg.budget for d in BUDGETS.values() for leg in d.legs}
        rt.SECURITY_BUDGETS_FAIL_CLOSED = configured_fail_closed
        validate_budget_definitions()


class TestShippedTableIsSelfConsistent:
    def test_shipped_table_boots(self) -> None:
        validate_budget_definitions()

    def test_ai_chat_is_the_only_deliberate_fail_open_budget(self) -> None:
        """ai_chat is a per-user quota, not a brute-force control.

        Failing it CLOSED would take the AI feature down on any Redis blip —
        an availability decision, not a security one. It is the ONLY opt-out,
        so a second one has to be an explicit, visible edit.
        """
        opt_outs = {
            action
            for action, definition in BUDGETS.items()
            if not definition.fail_closed
        }
        assert opt_outs == {SecurityAction.AI_CHAT}, (
            f"unexpected fail-open budgets: {sorted(str(a) for a in opt_outs)}"
        )

    def test_every_brute_force_budget_is_fail_closed(self) -> None:
        """AUTH / MFA / passkey / magic-link / refresh must all be fail-closed."""
        must = {
            SecurityAction.AUTH,
            SecurityAction.MFA_MUTATE,
            SecurityAction.MFA_STEPUP_VERIFY,
            SecurityAction.MFA_VERIFY_PENDING,
            SecurityAction.MFA_VERIFY_ACCOUNT,
            SecurityAction.PASSKEY_LOGIN,
            SecurityAction.MAGIC_LINK_SEND,
            SecurityAction.REFRESH,
        }
        open_budgets = {a for a, d in BUDGETS.items() if not d.fail_closed}
        assert not (must & open_budgets), f"brute-force controls fail open: {must & open_budgets}"

    def test_posture_default_is_fail_closed(self) -> None:
        """A new BudgetDef must not be able to default into failing open."""
        assert budgets_mod.BudgetDef.__dataclass_fields__["fail_closed"].default is True


class TestGateCatchesMisconfiguration:
    def test_a_budget_dropped_from_the_posture_set_fails_boot(self) -> None:
        """The core defect: this used to boot clean and fail open at runtime."""
        all_budgets = {leg.budget for d in BUDGETS.values() for leg in d.legs}
        without_auth = {leg.budget for a, d in BUDGETS.items() for leg in d.legs if a is not SecurityAction.AUTH}

        with pytest.raises(RuntimeError, match="fail-closed posture"):
            _validate_with(configured_fail_closed=without_auth, configured_budgets=all_budgets)

    def test_a_quota_incorrectly_declared_fail_closed_fails_boot(self) -> None:
        """The check runs both ways, so the config cannot claim a posture
        the table does not declare."""
        all_budgets = {leg.budget for d in BUDGETS.values() for leg in d.legs}

        with pytest.raises(RuntimeError, match="fail-closed posture"):
            _validate_with(configured_fail_closed=all_budgets, configured_budgets=all_budgets)

    def test_the_error_names_the_offending_budget(self) -> None:
        all_budgets = {leg.budget for d in BUDGETS.values() for leg in d.legs}
        without_mfa = {b for b in all_budgets if b != "mfa_verify"}

        with pytest.raises(RuntimeError) as exc:
            _validate_with(configured_fail_closed=without_mfa, configured_budgets=all_budgets)

        message = str(exc.value)
        assert "mfa_verify" in message, f"error does not name the budget: {message}"

    def test_missing_budget_check_still_works(self) -> None:
        """The pre-existing gate must not be weakened by the new one."""
        fail_closed = {leg.budget for a, d in BUDGETS.items() for leg in d.legs if d.fail_closed}

        with pytest.raises(RuntimeError, match="unconfigured budgets"):
            _validate_with(configured_fail_closed=fail_closed, configured_budgets={"auth"})
