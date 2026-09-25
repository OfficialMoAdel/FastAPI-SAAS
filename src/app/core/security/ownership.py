"""Framework-independent resource ownership policy."""

from src.app.shared.exceptions import ForbiddenError


def owns_resource(actor_user_id: object, owner_user_id: object) -> bool:
    """Return whether the actor owns the resource.

    IDs are compared as strings so an int/str representation difference cannot
    bypass the check.
    """
    return str(owner_user_id) == str(actor_user_id)


def require_resource_ownership(
    owner_user_id: object,
    actor_user_id: object,
    is_superuser: bool = False,
) -> None:
    """Raise when a non-superuser actor does not own the resource."""
    if not is_superuser and not owns_resource(actor_user_id, owner_user_id):
        raise ForbiddenError("You do not have permission to access this resource.")
