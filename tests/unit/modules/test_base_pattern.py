"""Unit tests for base module pattern.

Tests follow TDD approach - these tests define the expected behavior
for the base module classes that all feature modules inherit from.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestBaseModel:
    """Test base SQLAlchemy model."""

    def test_base_model_defined(self):
        """Test that Base class is properly defined."""
        from sqlalchemy.orm import DeclarativeBase

        from src.app.modules._base.models import Base

        assert issubclass(Base, DeclarativeBase)

    def test_base_metadata_accessible(self):
        """Test that Base.metadata is accessible."""
        from src.app.modules._base.models import Base

        assert hasattr(Base, "metadata")
        assert hasattr(Base.metadata, "tables")

    def test_base_extends_existing(self):
        """Test that Base is the same as core db Base."""
        from src.app.core.db.database import Base as CoreBase
        from src.app.modules._base.models import Base

        # Should be the same class for consistency
        assert Base is CoreBase


class TestBaseSchemas:
    """Test base Pydantic schemas."""

    def test_base_create_schema_exists(self):
        """Test BaseCreate schema structure."""
        from src.app.modules._base.schemas import BaseCreate

        # BaseCreate should have from_attributes config
        assert BaseCreate.model_config.get("from_attributes") is True

    def test_base_read_includes_timestamps(self):
        """Test BaseRead includes id and timestamps."""
        from src.app.modules._base.schemas import BaseRead

        fields = BaseRead.model_fields
        assert "id" in fields
        assert "created_at" in fields
        assert "updated_at" in fields

    def test_base_update_allows_partial(self):
        """Test BaseUpdate schema for partial updates."""
        from src.app.modules._base.schemas import BaseUpdate

        # Should have from_attributes config
        assert BaseUpdate.model_config.get("from_attributes") is True

    def test_base_list_pagination_fields(self):
        """Test BaseList has pagination fields."""
        from src.app.modules._base.schemas import BaseList

        fields = BaseList.model_fields
        assert "items" in fields
        assert "total" in fields
        assert "page" in fields
        assert "per_page" in fields
        assert "has_next" in fields
        assert "has_previous" in fields

    def test_base_pagination_defaults(self):
        """Test BasePagination default values."""
        from src.app.modules._base.schemas import BasePagination

        pagination = BasePagination()
        assert pagination.page == 1
        assert pagination.per_page == 20


class TestBaseService:
    """Test base service pattern."""

    @pytest.fixture
    def mock_fastcrud(self):
        """Fixture to patch FastCRUD for tests."""
        from unittest.mock import patch

        with patch("src.app.modules._base.service.FastCRUD") as mock_crud:
            mock_crud.return_value = MagicMock()
            yield mock_crud

    def test_base_service_accepts_model(self, mock_fastcrud):
        """Test that BaseService can be initialized with a model."""
        from src.app.modules._base.service import BaseService

        class MockModel:
            __tablename__ = "test_table"

        mock_db = AsyncMock()
        service = BaseService(MockModel, db=mock_db)

        assert service.model == MockModel

    def test_base_service_has_crud(self, mock_fastcrud):
        """Test that BaseService has CRUD operations."""
        from src.app.modules._base.service import BaseService

        class MockModel:
            pass

        mock_db = AsyncMock()
        service = BaseService(MockModel, db=mock_db)

        assert hasattr(service, "crud")
        # FastCRUD was called with the model
        mock_fastcrud.assert_called_once_with(MockModel)

    def test_base_service_has_db_session(self, mock_fastcrud):
        """Test that BaseService stores db session."""
        from src.app.modules._base.service import BaseService

        class MockModel:
            pass

        mock_db = AsyncMock()
        service = BaseService(MockModel, db=mock_db)

        assert service.db is mock_db

    def test_base_service_has_get_method(self, mock_fastcrud):
        """Test that BaseService has get method."""
        from src.app.modules._base.service import BaseService

        class MockModel:
            pass

        mock_db = AsyncMock()
        service = BaseService(MockModel, db=mock_db)

        assert hasattr(service, "get")
        assert callable(service.get)

    def test_base_service_has_get_multi_method(self, mock_fastcrud):
        """Test that BaseService has get_multi method."""
        from src.app.modules._base.service import BaseService

        class MockModel:
            pass

        mock_db = AsyncMock()
        service = BaseService(MockModel, db=mock_db)

        assert hasattr(service, "get_multi")
        assert callable(service.get_multi)

    def test_base_service_has_create_method(self, mock_fastcrud):
        """Test that BaseService has create method."""
        from src.app.modules._base.service import BaseService

        class MockModel:
            pass

        mock_db = AsyncMock()
        service = BaseService(MockModel, db=mock_db)

        assert hasattr(service, "create")
        assert callable(service.create)

    def test_base_service_has_update_method(self, mock_fastcrud):
        """Test that BaseService has update method."""
        from src.app.modules._base.service import BaseService

        class MockModel:
            pass

        mock_db = AsyncMock()
        service = BaseService(MockModel, db=mock_db)

        assert hasattr(service, "update")
        assert callable(service.update)

    def test_base_service_has_delete_method(self, mock_fastcrud):
        """Test that BaseService has delete method."""
        from src.app.modules._base.service import BaseService

        class MockModel:
            pass

        mock_db = AsyncMock()
        service = BaseService(MockModel, db=mock_db)

        assert hasattr(service, "delete")
        assert callable(service.delete)


class TestBaseExceptions:
    """Test base exception classes."""

    def test_model_not_found_error(self):
        """Test ModelNotFoundError exception."""
        from src.app.modules._base.exceptions import ModelNotFoundError

        error = ModelNotFoundError("User", 123)

        assert "User" in str(error)
        assert "123" in str(error)
        assert error.model_name == "User"
        assert error.identifier == 123

    def test_validation_error(self):
        """Test ValidationError exception."""
        from src.app.modules._base.exceptions import ValidationError

        error = ValidationError("Invalid email format")

        assert "Invalid email format" in str(error)
        assert error.message == "Invalid email format"

    def test_duplicate_error(self):
        """Test DuplicateError exception."""
        from src.app.modules._base.exceptions import DuplicateError

        error = DuplicateError("email", "test@example.com")

        assert "email" in str(error)
        assert "test@example.com" in str(error)
        assert error.field == "email"
        assert error.value == "test@example.com"


class TestBaseModuleExports:
    """Test base module exports all required classes."""

    def test_base_module_exports_base(self):
        """Test that _base module exports Base."""
        from src.app.modules._base import Base

        assert Base is not None

    def test_base_module_exports_schemas(self):
        """Test that _base module exports all schema classes."""
        from src.app.modules._base import (
            BaseCreate,
            BaseList,
            BasePagination,
            BaseRead,
            BaseUpdate,
        )

        assert BaseCreate is not None
        assert BaseRead is not None
        assert BaseUpdate is not None
        assert BaseList is not None
        assert BasePagination is not None

    def test_base_module_exports_service(self):
        """Test that _base module exports BaseService."""
        from src.app.modules._base import BaseService

        assert BaseService is not None

    def test_base_module_exports_exceptions(self):
        """Test that _base module exports all exception classes."""
        from src.app.modules._base import (
            DuplicateError,
            ModelNotFoundError,
            ValidationError,
        )

        assert ModelNotFoundError is not None
        assert ValidationError is not None
        assert DuplicateError is not None


class TestDependencyInjection:
    """Test dependency injection support."""

    def test_get_base_service_factory(self):
        """Test that get_base_service factory creates service."""
        from unittest.mock import patch

        from src.app.modules._base.service import get_base_service

        class MockModel:
            pass

        mock_db = AsyncMock()

        # Factory should create service with model and db
        with patch("src.app.modules._base.service.FastCRUD") as mock_crud:
            mock_crud.return_value = MagicMock()
            factory = get_base_service(MockModel)
            service = factory(db=mock_db)

            assert service.model == MockModel
            assert service.db is mock_db
