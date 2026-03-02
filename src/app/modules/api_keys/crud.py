"""API Key CRUD operations."""

from fastcrud import FastCRUD

from src.app.modules.api_keys.models import APIKey
from src.app.modules.api_keys.schemas import APIKeyRead

# Use type: ignore for FastCRUD generics since the library's type stubs
# don't properly support SQLAlchemy models as first argument.
# The runtime behavior is correct - FastCRUD accepts SQLAlchemy models.
crud_api_keys: FastCRUD[APIKey, APIKeyRead, APIKeyRead, APIKeyRead, APIKeyRead, APIKeyRead] = FastCRUD(APIKey)  # type: ignore[type-var]
