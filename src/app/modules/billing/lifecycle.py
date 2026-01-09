"""Subscription lifecycle state management.

T113: Subscription lifecycle state transitions for billing module.

Defines valid subscription states and transitions to ensure
consistent state management across the application.
"""

from enum import Enum
from typing import Set


class SubscriptionStatus(str, Enum):
    """Valid subscription statuses.

    Maps to UserSubscription.status field values.
    """
    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    EXPIRED = "expired"


# Valid state transitions mapping
# Key: current state
# Value: set of valid next states
VALID_TRANSITIONS: dict[SubscriptionStatus, Set[SubscriptionStatus]] = {
    SubscriptionStatus.TRIALING: {
        SubscriptionStatus.ACTIVE,      # Trial converted to paid
        SubscriptionStatus.CANCELED,    # Trial canceled
        SubscriptionStatus.EXPIRED,     # Trial expired without conversion
    },
    SubscriptionStatus.ACTIVE: {
        SubscriptionStatus.PAST_DUE,    # Payment failed
        SubscriptionStatus.CANCELED,    # User/system canceled
        SubscriptionStatus.EXPIRED,     # Subscription period ended
    },
    SubscriptionStatus.PAST_DUE: {
        SubscriptionStatus.ACTIVE,      # Payment recovered
        SubscriptionStatus.CANCELED,    # Max retries exceeded or user canceled
        SubscriptionStatus.EXPIRED,     # Grace period ended
    },
    SubscriptionStatus.CANCELED: {
        # Canceled is typically a terminal state
        # Reactivation requires new subscription
        SubscriptionStatus.EXPIRED,     # Final cleanup
    },
    SubscriptionStatus.EXPIRED: {
        # Expired is terminal - no valid transitions
        # User must create new subscription
    },
}


# Stripe status to internal status mapping
# Used by webhook handlers
STRIPE_STATUS_MAP: dict[str, SubscriptionStatus] = {
    "active": SubscriptionStatus.ACTIVE,
    "trialing": SubscriptionStatus.TRIALING,
    "past_due": SubscriptionStatus.PAST_DUE,
    "canceled": SubscriptionStatus.CANCELED,
    "unpaid": SubscriptionStatus.PAST_DUE,
    "incomplete": SubscriptionStatus.PAST_DUE,
    "incomplete_expired": SubscriptionStatus.EXPIRED,
}


class SubscriptionLifecycleError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current_status: str, target_status: str):
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            f"Invalid subscription state transition: {current_status} -> {target_status}"
        )


def is_valid_transition(current_status: str, target_status: str) -> bool:
    """Check if a state transition is valid.

    Args:
        current_status: Current subscription status
        target_status: Target subscription status

    Returns:
        True if transition is valid, False otherwise
    """
    try:
        current = SubscriptionStatus(current_status)
        target = SubscriptionStatus(target_status)
    except ValueError:
        # Invalid status values
        return False

    # Same state is always valid (no-op)
    if current == target:
        return True

    valid_next_states = VALID_TRANSITIONS.get(current, set())
    return target in valid_next_states


def validate_transition(current_status: str, target_status: str) -> None:
    """Validate a state transition, raising error if invalid.

    Args:
        current_status: Current subscription status
        target_status: Target subscription status

    Raises:
        SubscriptionLifecycleError: If transition is not valid
    """
    if not is_valid_transition(current_status, target_status):
        raise SubscriptionLifecycleError(current_status, target_status)


def map_stripe_status(stripe_status: str) -> str:
    """Map Stripe subscription status to internal status.

    Args:
        stripe_status: Stripe subscription status string

    Returns:
        Internal subscription status string
    """
    mapped = STRIPE_STATUS_MAP.get(stripe_status)
    if mapped:
        return mapped.value
    # Default to active for unknown statuses
    return SubscriptionStatus.ACTIVE.value


def can_cancel(status: str) -> bool:
    """Check if a subscription can be canceled.

    Args:
        status: Current subscription status

    Returns:
        True if subscription can be canceled
    """
    cancelable_states = {
        SubscriptionStatus.ACTIVE.value,
        SubscriptionStatus.TRIALING.value,
        SubscriptionStatus.PAST_DUE.value,
    }
    return status in cancelable_states


def is_access_granted(status: str, cancel_at_period_end: bool = False) -> bool:
    """Check if subscription grants access to features.

    Args:
        status: Subscription status
        cancel_at_period_end: Whether subscription is set to cancel at period end

    Returns:
        True if user should have access
    """
    # Active and trialing always have access
    if status in {SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIALING.value}:
        return True

    # Past due may have grace period access (configurable)
    if status == SubscriptionStatus.PAST_DUE.value:
        return True  # Grace period - could be configurable

    return False


def get_terminal_states() -> set[str]:
    """Get set of terminal subscription states.

    Terminal states require new subscription to resume service.

    Returns:
        Set of terminal status values
    """
    return {
        SubscriptionStatus.CANCELED.value,
        SubscriptionStatus.EXPIRED.value,
    }


def get_active_states() -> set[str]:
    """Get set of active subscription states.

    Active states grant access to features.

    Returns:
        Set of active status values
    """
    return {
        SubscriptionStatus.ACTIVE.value,
        SubscriptionStatus.TRIALING.value,
    }
