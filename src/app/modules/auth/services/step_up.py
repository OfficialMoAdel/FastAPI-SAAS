"""Step-up re-authentication for authentication-factor mutations.

Run-3 finding ``auth.mfa.passkey_enrollment_without_reauthentication``: passkey
registration and MFA setup/enable authenticated with a bearer token only — no
password, no existing second factor, no session-freshness check. A single stolen
access token (XSS exfiltration, log leak, malicious extension) could therefore
enroll a durable attacker-controlled WebAuthn credential that kept working after
the session token expired, after logout-all, and after the victim changed their
password, because no password-change or session-invalidation path removed
``PasskeyCredential`` rows.

The same module already treats factor *removal* as sensitive:
``MFAService.disable_mfa`` (:694) and ``regenerate_backup_codes`` (:814) both
require the current password plus a verified second factor. Enrollment was the
outlier. This module is the shared seam that closes that gap.

Mechanism
---------
The password travels in the **request body**, matching the convention
``disable_mfa`` / ``regenerate_backup_codes`` already use. It is deliberately not
a header: intermediaries log headers routinely, and step-up is precisely the
control that must not be reconstructable from a proxy access log.

``verify_step_up_password`` is the single verification point. It reuses the same
``UserDirectory(...).active(...)`` + ``verify_password`` pair as ``disable_mfa``
so there is one credential-comparison path, not two.
"""

from __future__ import annotations

from typing import Any

from src.app.core.config import settings
from src.app.core.logger import get_logger
from src.app.core.security import verify_password
from src.app.shared.exceptions import UnauthorizedError

logger = get_logger(__name__)

logger = get_logger(__name__)

# A generic message for every failure mode. Distinguishing "no such user" from
# "wrong password" here would reintroduce the enumeration oracle this module
# exists to close.
_INVALID_STEP_UP = "Step-up authentication failed. Re-enter your password."


async def verify_step_up_password(db, user_id: int, password: str | None) -> None:
    """Verify the caller's current password for a factor mutation.

    Args:
        db: Request-scoped session.
        user_id: The authenticated principal's id (never taken from the body).
        password: Password supplied in the request body.

    Raises:
        UnauthorizedError: If the password is missing, the user is inactive, or
            the password is wrong. The message is identical in all three cases.
    """
    # Imported here to keep this module free of a service -> module cycle.
    from src.app.modules.users import UserDirectory

    if not password:
        raise UnauthorizedError(_INVALID_STEP_UP, reason="step_up_missing")

    info = await UserDirectory(db).active(id=user_id)
    if not info:
        # Same message and same class as a wrong password: an attacker must not
        # be able to tell a valid step-up principal from an invalid one.
        raise UnauthorizedError(_INVALID_STEP_UP, reason="step_up_subject")

    user = info.model_dump()
    if not await verify_password(password, user.get("hashed_password", "")):
        logger.info(
            "step_up_password_rejected",
            user_id=user_id,
            reason="step_up_bad_password",
        )
        raise UnauthorizedError(_INVALID_STEP_UP, reason="step_up_bad_password")


async def revoke_user_passkeys(db: Any, user_id: int) -> int:
    """Delete every enrolled WebAuthn passkey for a user.

    Run-3 N-3 (``auth.mfa.passkey_enrollment_without_reauthentication``): a
    passkey could be enrolled with nothing but a stolen access token, and no
    password-change or session-invalidation path removed it — so the attacker
    kept a durable credential after the victim rotated their password.

    Gated on ``settings.auth.REVOKE_PASSKEYS_ON_PASSWORD_CHANGE`` (default
    True). Returns the number of credentials removed so the caller can log it.

    Best-effort by design: this runs alongside the password update, and a
    failure here must not roll back a legitimate password change. The failure is
    logged loudly so an operator can see the account still holds passkeys.
    """
    if not settings.auth.REVOKE_PASSKEYS_ON_PASSWORD_CHANGE:
        return 0

    try:
        from sqlalchemy import delete as sa_delete

        from src.app.modules.auth.models import PasskeyCredential

        result = await db.execute(
            sa_delete(PasskeyCredential).where(PasskeyCredential.user_id == user_id)
        )
        removed = result.rowcount
        await db.flush()
    except Exception as exc:  # noqa: BLE001 - must not break the password change
        logger.error(
            "passkey_revocation_on_password_change_failed", user_id=user_id, error=str(exc)
        )
        return 0

    if removed:
        logger.info("passkeys_revoked_on_password_change", user_id=user_id, removed=removed)
    return int(removed or 0)


__all__ = ["revoke_user_passkeys", "verify_step_up_password"]
