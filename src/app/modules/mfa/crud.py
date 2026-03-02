"""MFA (Multi-Factor Authentication) CRUD operations."""

from fastcrud import FastCRUD

from src.app.modules.mfa.models import MFAMethod

# Use type: ignore for FastCRUD generics since the library's type stubs
# don't properly support SQLAlchemy models as first argument.
# The runtime behavior is correct - FastCRUD accepts SQLAlchemy models.
crud_mfa_methods: FastCRUD[MFAMethod, None, None, None, None, None] = FastCRUD(MFAMethod)  # type: ignore[type-var]
