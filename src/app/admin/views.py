from typing import Annotated

import bcrypt
from crudadmin import CRUDAdmin
from crudadmin.admin_interface.model_view import PasswordTransformer
from pydantic import BaseModel, Field

from src.app.core.config import settings
from src.app.core.security.password import _normalize_secret
from src.app.modules.posts.models import Post
from src.app.modules.posts.schemas import PostUpdate
from src.app.modules.tiers.models import Tier
from src.app.modules.tiers.schemas import TierCreate, TierUpdate
from src.app.modules.users.models import User
from src.app.modules.users.schemas import UserCreate, UserCreateInternal, UserUpdate


def _sync_hash_password(password: str) -> str:
    """Sync wrapper for password hashing (for admin panel).

    M3: CRUDAdmin's ``PasswordTransformer`` invokes ``hash_function``
    synchronously on the event-loop thread (vendor contract:
    ``Callable[[str], str]``, never awaited), so true executor offload is
    not possible here. This wrapper at least shares the canonical secret
    normalization (SHA-256 pre-hash beyond bcrypt's 72-byte limit) and the
    configured cost factor with ``core.security.password``. Admin user
    creation is a rare operator action, so the blocking cost is accepted.
    """
    hashed: bytes = bcrypt.hashpw(
        _normalize_secret(password),
        bcrypt.gensalt(rounds=settings.crypt.BCRYPT_ROUNDS),
    )
    return hashed.decode()


class PostCreateAdmin(BaseModel):
    title: Annotated[str, Field(min_length=2, max_length=30, examples=["This is my post"])]
    text: Annotated[str, Field(min_length=1, max_length=63206, examples=["This is the content of my post."])]
    created_by_user_id: int
    media_url: Annotated[
        str | None,
        Field(
            pattern=r"^(https?|ftp)://[^\s/$.?#].[^\s]*$",
            examples=["https://www.postimageurl.com"],
            default=None,
        ),
    ]


def register_admin_views(admin: CRUDAdmin) -> None:
    """Register all models and their schemas with the admin interface.

    This function adds all available models to the admin interface with appropriate
    schemas and permissions.
    """
    password_transformer = PasswordTransformer(
        password_field="password",
        hashed_field="hashed_password",
        hash_function=_sync_hash_password,
        required_fields=["name", "username", "email"],
    )

    admin.add_view(
        model=User,
        create_schema=UserCreate,
        update_schema=UserUpdate,
        update_internal_schema=UserCreateInternal,
        password_transformer=password_transformer,
        allowed_actions={"view", "create", "update"},
    )

    admin.add_view(
        model=Tier,
        create_schema=TierCreate,
        update_schema=TierUpdate,
        allowed_actions={"view", "create", "update", "delete"},
    )

    admin.add_view(
        model=Post,
        create_schema=PostCreateAdmin,
        update_schema=PostUpdate,
        allowed_actions={"view", "create", "update", "delete"},
    )
