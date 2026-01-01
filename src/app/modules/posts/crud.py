from ...core.db.tenant_crud import TenantAwareCRUD
from .models import Post
from .schemas import PostCreateInternal, PostDelete, PostRead, PostUpdate, PostUpdateInternal

CRUDPost = TenantAwareCRUD[Post, PostCreateInternal, PostUpdate, PostUpdateInternal, PostDelete, PostRead]
crud_posts = CRUDPost(Post)
