"""MFA (Multi-Factor Authentication) CRUD operations."""

from fastcrud import FastCRUD

from src.app.modules.mfa.models import MFAMethod

crud_mfa_methods: FastCRUD[MFAMethod, None, None, None, None, None] = FastCRUD(MFAMethod)  # type: ignore[type-var]
