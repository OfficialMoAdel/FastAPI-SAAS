from fastcrud import FastCRUD

from src.app.modules.rate_limits.models import RateLimit
from src.app.modules.rate_limits.schemas import (
    RateLimitCreateInternal,
    RateLimitDelete,
    RateLimitRead,
    RateLimitUpdate,
)

CRUDRateLimit = FastCRUD[
    RateLimit, RateLimitCreateInternal, RateLimitUpdate, RateLimitUpdate, RateLimitDelete, RateLimitRead
]
crud_rate_limits = CRUDRateLimit(RateLimit)
