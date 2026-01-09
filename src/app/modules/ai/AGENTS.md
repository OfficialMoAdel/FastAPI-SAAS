# AI Module - Agent Guidance

## Package Identity

AI chat service with Ollama integration for LLM interactions. Manages chat sessions, messages, user AI settings, and provides both standard and streaming completions. Includes rate limiting and context window management.

## Setup & Run

```bash
# Run AI unit tests
uv run pytest tests/unit/ai/ -v

# Run AI integration tests
uv run pytest tests/integration/ai/ -v

# Test chat completion
curl -X POST http://localhost:8000/api/v1/ai/sessions/1/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, how are you?"}'

# Test available models
curl http://localhost:8000/api/v1/ai/models \
  -H "Authorization: Bearer <token>"
```

## Patterns & Conventions

### DO: Use services for AI operations

```python
from src.app.modules.ai.service import (
    get_chat_session_service,
    get_chat_completion_service,
    get_ai_settings_service,
)

session_service = get_chat_session_service(db)
session = await session_service.create_session(user_id, ChatSessionCreate(title="New Chat"))

completion_service = get_chat_completion_service(db)
response = await completion_service.complete(session_id, user_id, request)
```

### DON'T: Call Ollama directly from routers

```python
# BAD - Ollama calls in router
@router.post("/chat")
async def chat(...):
    client = OllamaClient()
    await client.chat(...)  # Don't do this
```

### DO: Handle streaming responses properly

```python
from fastapi.responses import StreamingResponse

@router.post("/chat/stream")
async def chat_stream(...) -> StreamingResponse:
    async def generate():
        async for chunk in service.complete_stream(session_id, user_id, request):
            yield f"data: {json.dumps(chunk)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```

### DO: Use rate limiting middleware

```python
from src.app.modules.ai.middleware import RateLimitedUser

@router.post("/chat")
async def chat_completion(
    current_user: RateLimitedUser,  # Rate-limited dependency
    ...
):
    ...
```

### DO: Handle AI exceptions properly

```python
from src.app.modules.ai.service import ChatCompletionError
from src.app.modules.ai.exceptions import AIRateLimitError

try:
    response = await completion_service.complete(session_id, user_id, request)
except ChatCompletionError as e:
    raise HTTPException(503, {"detail": e.message, "error_code": e.error_code})
```

### DO: Use user settings for defaults

```python
settings_service = get_ai_settings_service(db)
user_settings = await settings_service.get_or_create_settings(user_id)

# Use settings as defaults
model = request.model or user_settings.get("model", "gemma3")
temperature = request.temperature or user_settings.get("temperature", 0.7)
```

## Module Structure

```
src/app/modules/ai/
├── __init__.py           # Module exports
├── router.py             # /ai endpoints
├── service.py            # Service classes
├── crud.py               # CRUD operations
├── models.py             # SQLAlchemy models
├── schemas.py            # Pydantic schemas
├── ollama_client.py      # Ollama API client
├── middleware.py         # Rate limiting middleware
├── rate_limiter.py       # Rate limiter implementation
└── exceptions.py         # AI-specific exceptions
```

## Touch Points

- **Session service**: `modules/ai/service.py::ChatSessionService`
- **Completion service**: `modules/ai/service.py::ChatCompletionService`
- **Settings service**: `modules/ai/service.py::AISettingsService`
- **Ollama client**: `modules/ai/ollama_client.py::OllamaClient`
- **Rate limiter**: `modules/ai/rate_limiter.py::RateLimiter`

## JIT Index Hints

```bash
# Find AI endpoints
rg -n "router\.(post|get|patch|delete)" src/app/modules/ai/router.py

# Find Ollama integration
rg -n "OllamaClient|ollama" src/app/modules/ai/

# Find service methods
rg -n "async def (complete|create_session|get_settings)" src/app/modules/ai/

# Find rate limiting
rg -n "RateLimiter|rate_limit" src/app/modules/ai/
```

## Common Gotchas

- **Token estimation**: Use 4 chars per token for rough estimation
- **Context window**: Manage context size to avoid truncation
- **Rate limiting**: 60 requests per minute per user by default
- **Streaming**: SSE format with proper Content-Type header
- **Model availability**: Check Ollama status before completions
- **Default model**: "gemma3" is the default model

## Default AI Settings

- **Model**: gemma3
- **Temperature**: 0.7 (range: 0.0-2.0)
- **Max tokens**: 2048 (range: 1-32000)

## Environment Variables

```bash
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_TIMEOUT=120
```

## Pre-PR Checks

```bash
uv run pytest tests/unit/ai/ -v && \
uv run ruff check src/app/modules/ai/ && \
uv run mypy src/app/modules/ai/
```
