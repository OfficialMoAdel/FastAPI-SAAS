from fastcrud import FastCRUD

from src.app.modules.tiers.models import Tier
from src.app.modules.tiers.schemas import TierCreateInternal, TierDelete, TierRead, TierUpdate, TierUpdateInternal

CRUDTier = FastCRUD[Tier, TierCreateInternal, TierUpdate, TierUpdateInternal, TierDelete, TierRead]
crud_tiers = CRUDTier(Tier)
