"""MFA module - Multi-factor authentication support.

Exports:
- MFAService: Service for MFA operations
- get_mfa_service: Dependency injection factory
- crud_mfa_methods: CRUD operations for MFA methods
"""

from src.app.modules.mfa.crud import crud_mfa_methods
from src.app.modules.mfa.service import MFAService, get_mfa_service

__all__ = [
    "MFAService",
    "get_mfa_service",
    "crud_mfa_methods",
]
