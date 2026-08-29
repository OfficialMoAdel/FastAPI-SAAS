"""R0-03 guard: no relationship may default to lazy="select".

Under async SQLAlchemy, an implicit ``select`` load triggers synchronous IO
inside the event loop and raises ``MissingGreenlet`` when the attribute is
accessed on a detached instance (e.g. after the request session closed).
Every relationship must declare an explicit ``lazy=`` strategy.
"""

import importlib
import logging
import pkgutil

import pytest

logger = logging.getLogger(__name__)


def _import_all_models() -> None:
    """Import every module's models so all mappers register."""
    import src.app.modules as modules_pkg

    for mod_info in pkgutil.iter_modules(modules_pkg.__path__):
        try:
            importlib.import_module(f"src.app.modules.{mod_info.name}.models")
        except ModuleNotFoundError:
            # Module without a models.py (e.g. health, cache_warming)
            continue


class TestRelationshipLazyStrategies:
    def test_no_relationship_defaults_to_select(self):
        """Every relationship declares an explicit lazy= strategy."""
        _import_all_models()

        from sqlalchemy.orm import configure_mappers

        from src.app.core.db.database import Base

        configure_mappers()

        offenders = [
            f"{mapper.class_.__name__}.{rel.key}"
            for mapper in Base.registry.mappers
            for rel in mapper.relationships
            if rel.lazy == "select"
        ]
        assert not offenders, (
            "Relationships defaulting to lazy='select' (async MissingGreenlet trap). "
            f"Set an explicit lazy= strategy on: {', '.join(sorted(offenders))}"
        )

    def test_all_models_register_on_shared_metadata(self):
        """R0-06 companion: model import must not require extend_existing.

        If a table were registered twice under different module paths,
        DeclarativeBase would raise during import now that extend_existing
        is gone — this test fails loudly instead of masking the drift.
        """
        _import_all_models()

        from src.app.core.db.database import Base

        # Sanity: the registry is non-trivial and table names are unique keys.
        assert len(Base.metadata.tables) >= 30
        names = [t.name for t in Base.metadata.tables.values()]
        assert len(names) == len(set(names))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
