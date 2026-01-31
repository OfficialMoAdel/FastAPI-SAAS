"""AI Features CRUD operations.

Tenant-aware CRUD classes for AI chat sessions, messages, and settings.
"""

from src.app.core.db.tenant_crud import TenantAwareCRUD

from .models import AIChatMessage, AIChatSession, AISettings
from .schemas import (
    AISettingsRead,
    AISettingsUpdate,
    ChatMessageCreate,
    ChatMessageRead,
    ChatSessionCreate,
    ChatSessionRead,
    ChatSessionUpdate,
)

# Chat Session CRUD (tenant-aware)
CRUDChatSession = TenantAwareCRUD[  # type: ignore[type-var]
    AIChatSession,
    ChatSessionCreate,
    ChatSessionUpdate,
    ChatSessionUpdate,
    None,
    ChatSessionRead,
]
crud_chat_sessions = CRUDChatSession(AIChatSession)  # type: ignore[type-var]


# Chat Message CRUD (no tenant_id, linked via session)
CRUDChatMessage = TenantAwareCRUD[  # type: ignore[type-var]
    AIChatMessage,
    ChatMessageCreate,
    None,
    None,
    None,
    ChatMessageRead,
]
crud_chat_messages = CRUDChatMessage(AIChatMessage)  # type: ignore[type-var]


# AI Settings CRUD (user-level, no tenant_id)
CRUDAISettings = TenantAwareCRUD[  # type: ignore[type-var]
    AISettings,
    AISettingsUpdate,
    AISettingsUpdate,
    AISettingsUpdate,
    None,
    AISettingsRead,
]
crud_ai_settings = CRUDAISettings(AISettings)  # type: ignore[type-var]
