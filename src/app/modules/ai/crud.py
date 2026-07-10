"""AI Features CRUD operations.

Tenant-aware CRUD classes for AI chat sessions, messages, and settings.
"""

from src.app.core.db.tenant_crud import TenantAwareCRUD
from src.app.modules.ai.models import AIChatMessage, AIChatSession, AISettings
from src.app.modules.ai.schemas import (
    AISettingsCreateInternal,
    AISettingsRead,
    AISettingsUpdate,
    ChatMessageCreateInternal,
    ChatMessageRead,
    ChatSessionCreate,
    ChatSessionRead,
    ChatSessionUpdate,
)


class CRUDChatSession(
    TenantAwareCRUD[AIChatSession, ChatSessionCreate, ChatSessionUpdate, ChatSessionUpdate, None, ChatSessionRead]  # type: ignore[type-var]
):
    create_schema = ChatSessionCreate


class CRUDChatMessage(
    TenantAwareCRUD[AIChatMessage, ChatMessageCreateInternal, None, None, None, ChatMessageRead]  # type: ignore[type-var]
):
    create_schema = ChatMessageCreateInternal


class CRUDAISettings(
    TenantAwareCRUD[AISettings, AISettingsCreateInternal, AISettingsUpdate, AISettingsUpdate, None, AISettingsRead]  # type: ignore[type-var]
):
    create_schema = AISettingsCreateInternal


crud_chat_sessions = CRUDChatSession(AIChatSession)  # type: ignore[type-var]
crud_chat_messages = CRUDChatMessage(AIChatMessage)  # type: ignore[type-var]
crud_ai_settings = CRUDAISettings(AISettings)  # type: ignore[type-var]
