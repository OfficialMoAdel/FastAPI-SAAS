"""API Key CRUD operations."""

from fastcrud import FastCRUD

from .models import APIKey

crud_api_keys = FastCRUD(APIKey)
