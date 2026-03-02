"""Base exceptions for all feature modules.

Provides reusable exception classes for common error scenarios
across all feature modules.

Usage:
    from src.app.modules._base import ModelNotFoundError, ValidationError

    async def get_user(user_id: int) -> User:
        user = await service.get(user_id)
        if not user:
            raise ModelNotFoundError("User", user_id)
        return user
"""


from __future__ import annotations

from typing import Any


class ModelNotFoundError(Exception):
    """Raised when a model is not found by ID or other criteria.

    Attributes:
        model_name: Name of the model class.
        identifier: The ID or identifier used for lookup.
        message: Human-readable error message.
    """

    def __init__(self, model_name: str, identifier: Any):
        """Initialize ModelNotFoundError.

        Args:
            model_name: Name of the model class (e.g., "User", "Tenant").
            identifier: The ID or identifier that was not found.
        """
        self.model_name = model_name
        self.identifier = identifier
        self.message = f"{model_name} with id {identifier} not found"
        super().__init__(self.message)


class ValidationError(Exception):
    """Raised when validation fails.

    Attributes:
        message: Human-readable error message describing the validation failure.
    """

    def __init__(self, message: str):
        """Initialize ValidationError.

        Args:
            message: Description of the validation failure.
        """
        self.message = message
        super().__init__(self.message)


class DuplicateError(Exception):
    """Raised when a duplicate record is detected.

    Typically used for unique constraint violations.

    Attributes:
        field: The field that has a duplicate value.
        value: The duplicate value.
        message: Human-readable error message.
    """

    def __init__(self, field: str, value: Any):
        """Initialize DuplicateError.

        Args:
            field: The field name with duplicate value (e.g., "email").
            value: The duplicate value.
        """
        self.field = field
        self.value = value
        self.message = f"Duplicate value for {field}: {value}"
        super().__init__(self.message)


class ForbiddenError(Exception):
    """Raised when an action is forbidden.

    Used when a valid request is rejected due to business rules,
    not authentication/authorization.

    Attributes:
        message: Human-readable error message.
        reason: Machine-readable reason code.
    """

    def __init__(self, message: str, reason: str = "forbidden"):
        """Initialize ForbiddenError.

        Args:
            message: Description of why the action is forbidden.
            reason: Machine-readable reason code for the error.
        """
        self.message = message
        self.reason = reason
        super().__init__(self.message)


__all__ = ["ModelNotFoundError", "ValidationError", "DuplicateError", "ForbiddenError"]
