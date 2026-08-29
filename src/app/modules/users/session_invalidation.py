"""R2-02: session-invalidation policy for user cache invalidation.

Encodes the "should this change bump the token version?" decision as a named,
testable policy instead of a bare bool threaded through call sites. The
security invariant this captures:

    Credential-affecting changes (password/email/username change, deletion,
    tier change, role grant/revoke) MUST invalidate existing sessions;
    benign profile edits (name, avatar, transparent hash upgrade) MUST NOT.

``UserService._invalidate_user_cache`` accepts BOTH a ``bump_token_version``
bool AND a ``reason`` enum, and the two MUST agree. The agreement check
(:func:`should_bump_token_version` of the reason MUST equal the bool) is
enforced at function entry so the two sources of truth cannot diverge.
Callers should always pass ``bump_token_version=should_bump_token_version(reason)``
— the redundant bool is the guard, the reason is the human-readable intent.
"""

from enum import Enum


class InvalidationReason(str, Enum):
    """Why a user's cache/session state is being invalidated."""

    PROFILE_EDIT = "profile_edit"
    CREDENTIAL_CHANGE = "credential_change"
    DELETION = "deletion"
    TIER_CHANGE = "tier_change"


# Reasons that must force re-authentication by bumping token_version.
_BUMP_REASONS: frozenset[InvalidationReason] = frozenset(
    {
        InvalidationReason.CREDENTIAL_CHANGE,
        InvalidationReason.DELETION,
        InvalidationReason.TIER_CHANGE,
    }
)


def should_bump_token_version(reason: InvalidationReason) -> bool:
    """Return True when the reason requires invalidating active sessions.

    S3 FIX semantics: only security-affecting changes bump. Benign profile
    updates leave sessions intact.
    """
    return reason in _BUMP_REASONS
