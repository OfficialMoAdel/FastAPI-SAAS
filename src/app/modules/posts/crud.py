from src.app.core.db.tenant_crud import TenantAwareCRUD
from src.app.modules.posts.models import Post
from src.app.modules.posts.schemas import PostCreateInternal, PostDelete, PostRead, PostUpdate, PostUpdateInternal

CRUDPost = TenantAwareCRUD[Post, PostCreateInternal, PostUpdate, PostUpdateInternal, PostDelete, PostRead]
crud_posts = CRUDPost(Post)
