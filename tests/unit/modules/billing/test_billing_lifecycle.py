"""Unit tests for billing/lifecycle.py - subscription state management."""

import pytest

from src.app.modules.billing.lifecycle import (
    VALID_TRANSITIONS,
    SubscriptionLifecycleError,
    SubscriptionStatus,
    can_cancel,
    get_active_states,
    get_terminal_states,
    is_access_granted,
    is_valid_transition,
    map_stripe_status,
    validate_transition,
)


class TestSubscriptionStatus:
    def test_enum_values(self):
        assert SubscriptionStatus.ACTIVE.value == "active"
        assert SubscriptionStatus.TRIALING.value == "trialing"
        assert SubscriptionStatus.PAST_DUE.value == "past_due"
        assert SubscriptionStatus.CANCELED.value == "canceled"
        assert SubscriptionStatus.EXPIRED.value == "expired"

    def test_enum_from_string(self):
        assert SubscriptionStatus("active") == SubscriptionStatus.ACTIVE
        assert SubscriptionStatus("trialing") == SubscriptionStatus.TRIALING

    def test_enum_is_string_subclass(self):
        assert isinstance(SubscriptionStatus.ACTIVE, str)
        assert SubscriptionStatus.ACTIVE == "active"


class TestValidTransitions:
    def test_trialing_valid_transitions(self):
        valid = VALID_TRANSITIONS[SubscriptionStatus.TRIALING]
        assert SubscriptionStatus.ACTIVE in valid
        assert SubscriptionStatus.CANCELED in valid
        assert SubscriptionStatus.EXPIRED in valid
        assert SubscriptionStatus.PAST_DUE not in valid

    def test_active_valid_transitions(self):
        valid = VALID_TRANSITIONS[SubscriptionStatus.ACTIVE]
        assert SubscriptionStatus.PAST_DUE in valid
        assert SubscriptionStatus.CANCELED in valid
        assert SubscriptionStatus.EXPIRED in valid
        assert SubscriptionStatus.TRIALING not in valid

    def test_past_due_valid_transitions(self):
        valid = VALID_TRANSITIONS[SubscriptionStatus.PAST_DUE]
        assert SubscriptionStatus.ACTIVE in valid
        assert SubscriptionStatus.CANCELED in valid
        assert SubscriptionStatus.EXPIRED in valid

    def test_canceled_valid_transitions(self):
        valid = VALID_TRANSITIONS[SubscriptionStatus.CANCELED]
        assert SubscriptionStatus.EXPIRED in valid
        assert len(valid) == 1

    def test_expired_has_no_transitions(self):
        valid = VALID_TRANSITIONS[SubscriptionStatus.EXPIRED]
        assert len(valid) == 0


class TestIsValidTransition:
    def test_valid_transition_active_to_canceled(self):
        assert is_valid_transition("active", "canceled") is True

    def test_valid_transition_trialing_to_active(self):
        assert is_valid_transition("trialing", "active") is True

    def test_valid_transition_past_due_to_active(self):
        assert is_valid_transition("past_due", "active") is True

    def test_valid_transition_active_to_past_due(self):
        assert is_valid_transition("active", "past_due") is True

    def test_invalid_transition_expired_to_active(self):
        assert is_valid_transition("expired", "active") is False

    def test_invalid_transition_canceled_to_active(self):
        assert is_valid_transition("canceled", "active") is False

    def test_same_state_transition_is_always_valid(self):
        for status in SubscriptionStatus:
            assert is_valid_transition(status.value, status.value) is True

    def test_invalid_status_string_returns_false(self):
        assert is_valid_transition("nonexistent", "active") is False
        assert is_valid_transition("active", "nonexistent") is False
        assert is_valid_transition("bad1", "bad2") is False


class TestValidateTransition:
    def test_valid_transition_does_not_raise(self):
        # Should not raise
        validate_transition("active", "canceled")
        validate_transition("trialing", "active")

    def test_invalid_transition_raises_lifecycle_error(self):
        with pytest.raises(SubscriptionLifecycleError) as exc_info:
            validate_transition("expired", "active")

        err = exc_info.value
        assert err.current_status == "expired"
        assert err.target_status == "active"
        assert "expired -> active" in str(err)

    def test_invalid_status_raises_lifecycle_error(self):
        with pytest.raises(SubscriptionLifecycleError):
            validate_transition("invalid_status", "active")


class TestMapStripeStatus:
    def test_maps_active(self):
        assert map_stripe_status("active") == "active"

    def test_maps_trialing(self):
        assert map_stripe_status("trialing") == "trialing"

    def test_maps_past_due(self):
        assert map_stripe_status("past_due") == "past_due"

    def test_maps_canceled(self):
        assert map_stripe_status("canceled") == "canceled"

    def test_maps_unpaid_to_past_due(self):
        assert map_stripe_status("unpaid") == "past_due"

    def test_maps_incomplete_to_past_due(self):
        assert map_stripe_status("incomplete") == "past_due"

    def test_maps_incomplete_expired_to_expired(self):
        assert map_stripe_status("incomplete_expired") == "expired"

    def test_unknown_status_defaults_to_past_due(self):
        """Unknown Stripe statuses default to past_due (security fix - denies access by default)."""
        assert map_stripe_status("unknown_stripe_status") == "past_due"
        assert map_stripe_status("") == "past_due"


class TestCanCancel:
    def test_active_can_be_canceled(self):
        assert can_cancel("active") is True

    def test_trialing_can_be_canceled(self):
        assert can_cancel("trialing") is True

    def test_past_due_can_be_canceled(self):
        assert can_cancel("past_due") is True

    def test_canceled_cannot_be_canceled(self):
        assert can_cancel("canceled") is False

    def test_expired_cannot_be_canceled(self):
        assert can_cancel("expired") is False

    def test_invalid_status_cannot_be_canceled(self):
        assert can_cancel("nonexistent") is False


class TestIsAccessGranted:
    def test_active_granted(self):
        assert is_access_granted("active") is True

    def test_trialing_granted(self):
        assert is_access_granted("trialing") is True

    def test_past_due_granted_grace_period(self):
        assert is_access_granted("past_due") is True

    def test_canceled_not_granted(self):
        assert is_access_granted("canceled") is False

    def test_expired_not_granted(self):
        assert is_access_granted("expired") is False

    def test_cancel_at_period_end_still_granted_if_active(self):
        assert is_access_granted("active", cancel_at_period_end=True) is True


class TestGetTerminalStates:
    def test_terminal_states_includes_canceled_and_expired(self):
        terminal = get_terminal_states()
        assert "canceled" in terminal
        assert "expired" in terminal

    def test_terminal_states_does_not_include_active_states(self):
        terminal = get_terminal_states()
        assert "active" not in terminal
        assert "trialing" not in terminal
        assert "past_due" not in terminal


class TestGetActiveStates:
    def test_active_states_includes_active_and_trialing(self):
        active = get_active_states()
        assert "active" in active
        assert "trialing" in active

    def test_active_states_does_not_include_terminal_states(self):
        active = get_active_states()
        assert "canceled" not in active
        assert "expired" not in active
        assert "past_due" not in active
