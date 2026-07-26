from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from src.app.core.utils.path_utils import sanitize_path as sanitize_path
from src.app.modules._base.schemas import BaseCreate, BaseRead, BaseUpdate


class RateLimitBase(BaseModel):
    path: Annotated[str, Field(examples=["users"])]
    limit: Annotated[int, Field(examples=[5])]
    period: Annotated[int, Field(examples=[60])]

    @field_validator("path")
    def validate_and_sanitize_path(cls, v: str) -> str:
        return sanitize_path(v)


class RateLimitInternal(RateLimitBase):
    """Internal schema with tier_id for service layer use."""

    tier_id: int
    name: Annotated[str | None, Field(default=None, examples=["users:5:60"])]


class RateLimitRead(BaseRead, RateLimitBase):
    """Read schema for rate limit responses with timestamps."""

    tier_id: int
    name: str


class RateLimitCreate(BaseCreate, RateLimitBase):
    """Create schema for rate limit."""

    name: Annotated[str | None, Field(default=None, examples=["api_v1_users:5:60"])]


class RateLimitCreateInternal(RateLimitCreate):
    tier_id: int


class RateLimitUpdate(BaseUpdate):
    """Update schema for rate limit with optional fields."""

    path: str | None = Field(default=None)
    limit: int | None = None
    period: int | None = None
    name: str | None = None

    @field_validator("path")
    def validate_and_sanitize_path(cls, v: str | None) -> str | None:
        return sanitize_path(v) if v is not None else None





class RateLimitDelete(BaseModel):
    pass
