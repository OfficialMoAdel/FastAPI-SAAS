"""Base SQLAlchemy model for all feature modules.

Re-exports the Base class from core.db.database to maintain consistency
across all feature modules while providing a single import location.

Usage:
    from src.app.modules._base import Base

    class User(Base):
        __tablename__ = "users"
        id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
        email: Mapped[str] = mapped_column(String(255), unique=True)
"""

from src.app.core.db.database import Base

__all__ = ["Base"]
