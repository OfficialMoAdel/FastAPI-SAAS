"""Test model registration and metadata consistency for consolidated storage models."""

from src.app.core.db.database import Base
from src.app.models import File as F2
from src.app.models import StorageSettings as SS2
from src.app.modules.storage.models import File, StorageSettings


def test_single_model_registration():
    """Verify models are singletons and match src.app.models exports."""
    assert F2 is File
    assert SS2 is StorageSettings

    tables = Base.metadata.tables
    assert "files" in tables
    assert "storage_settings" in tables

    # Verify table args and indexes on File
    file_table = tables["files"]
    index_names = {idx.name for idx in file_table.indexes}
    assert "ix_files_tenant_mime" in index_names
    assert "idx_files_tenant_created" in index_names
    assert "idx_files_tenant_user_created" in index_names
