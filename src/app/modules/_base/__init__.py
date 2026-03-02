"""Base module pattern for all feature modules.

This module provides reusable base classes that ensure consistency
across all feature modules in the application.

Exports:
    - Base: SQLAlchemy declarative base class
    - BaseCreate, BaseRead, BaseUpdate: Pydantic schemas for CRUD
    - BaseList, BasePagination: Pagination schemas
    - BaseService: Generic service with CRUD operations
    - get_base_service: DI factory for base service
    - ModelNotFoundError, ValidationError, DuplicateError: Exceptions

Usage:
    from src.app.modules._base import (
        Base,
        BaseCreate,
        BaseRead,
        BaseService,
        ModelNotFoundError,
    )

    class User(Base):
        __tablename__ = "users"
        ...

    class UserCreate(BaseCreate):
        email: str
        ...

    class UserService(BaseService[User]):
        ...
"""

from src.app.modules._base.exceptions import DuplicateError, ModelNotFoundError, ValidationError
from src.app.modules._base.models import Base
from src.app.modules._base.schemas import BaseCreate, BaseList, BasePagination, BaseRead, BaseReadUUID, BaseUpdate
from src.app.modules._base.service import BaseService, get_base_service

__all__ = [
    # Models
    "Base",
    # Schemas
    "BaseCreate",
    "BaseRead",
    "BaseReadUUID",
    "BaseUpdate",
    "BaseList",
    "BasePagination",
    # Service
    "BaseService",
    "get_base_service",
    # Exceptions
    "ModelNotFoundError",
    "ValidationError",
    "DuplicateError",
]
